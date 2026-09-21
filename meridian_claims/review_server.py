"""
Minimal local HTTP server for the coordinator review UI.

Serves static UI + ActionPacket JSON from output/.
Can run the claims pipeline on demand (live process / re-run).
Never sends outbound email.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import traceback
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from meridian_claims.agent import MissingAPIKeyError, temporary_api_key
from meridian_claims.pipeline import process_email

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_DIR = Path(__file__).resolve().parent / "review_ui"
_EMAIL_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def serve_review_ui(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    output_dir: Path | None = None,
    data_dir: Path | None = None,
) -> int:
    out = Path(output_dir) if output_dir else REPO_ROOT / "output"
    out.mkdir(parents=True, exist_ok=True)
    data = Path(data_dir) if data_dir else REPO_ROOT / "data"
    emails = data / "emails"

    if not UI_DIR.exists():
        print(f"Review UI assets missing: {UI_DIR}")
        return 1

    handler = _make_handler(out, UI_DIR, data, emails)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Coordinator review UI: http://{host}:{port}/")
    print(f"Loading ActionPackets from: {out}")
    print(f"Live process source: {emails}")
    print("Human review only — no outbound email is sent by this demo.")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


def _make_handler(
    output_dir: Path,
    ui_dir: Path,
    data_dir: Path,
    emails_dir: Path,
):
    class ReviewHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # quieter console
            sys_stderr = __import__("sys").stderr
            sys_stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = unquote(parsed.path)

            if path in ("/", "/index.html"):
                return self._send_file(ui_dir / "index.html", "text/html; charset=utf-8")
            if path == "/styles.css":
                return self._send_file(ui_dir / "styles.css", "text/css; charset=utf-8")
            if path == "/app.js":
                return self._send_file(
                    ui_dir / "app.js", "application/javascript; charset=utf-8"
                )
            if path == "/api/packets":
                return self._send_json(_list_packets(output_dir))
            if path == "/api/emails":
                return self._send_json(_list_emails(emails_dir, output_dir))
            if path == "/api/config":
                return self._send_json(_api_config())
            m = re.fullmatch(r"/api/packets/([A-Za-z0-9_-]+)", path)
            if m:
                packet = _load_packet(output_dir, m.group(1))
                if packet is None:
                    return self._send_error(404, f"Packet not found: {m.group(1)}")
                return self._send_json(packet)

            return self._send_error(404, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path != "/api/process":
                return self._send_error(
                    405,
                    "Only POST /api/process is allowed. Accept/Reject stay client-only. "
                    "No outbound email send path.",
                )

            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                length = 0
            raw = self.rfile.read(max(0, length)) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return self._send_error(400, "Invalid JSON body")
            if not isinstance(body, dict):
                return self._send_error(400, "JSON body must be an object")

            email_id = str(body.get("email_id") or "").strip()
            # Live LLM is the default; dry_run must be explicitly true.
            dry_run = bool(body.get("dry_run", False))
            api_key = body.get("api_key")
            if api_key is not None and not isinstance(api_key, str):
                return self._send_error(400, "api_key must be a string when provided")
            result = _process_email_id(
                email_id,
                emails_dir=emails_dir,
                data_dir=data_dir,
                output_dir=output_dir,
                dry_run=dry_run,
                api_key=api_key.strip() if isinstance(api_key, str) and api_key.strip() else None,
            )
            code = int(result.get("http_status") or 200)
            payload = {k: v for k, v in result.items() if k != "http_status"}
            if code >= 400:
                return self._send_json(payload, code=code)
            return self._send_json(payload)

        def _send_file(self, file_path: Path, content_type: str) -> None:
            if not file_path.is_file():
                return self._send_error(404, "Asset missing")
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, obj, *, code: int = 200) -> None:
            data = json.dumps(obj, indent=2).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_error(self, code: int, message: str) -> None:
            self._send_json({"error": message}, code=code)

    return ReviewHandler


def _list_packets(output_dir: Path) -> dict:
    items = []
    for path in sorted(output_dir.glob("*.json")):
        name = path.name
        if name.endswith(".decision.json"):
            continue
        if name in ("eval_all.json",):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            items.append(
                {
                    "email_id": path.stem,
                    "error": "malformed_json",
                    "load_number": None,
                    "claim_type": None,
                    "confidence": None,
                    "needs_human": True,
                    "resolution_status": "unknown",
                }
            )
            continue
        if not isinstance(data, dict) or "email_id" not in data:
            items.append(
                {
                    "email_id": path.stem,
                    "error": "not_an_action_packet",
                    "load_number": None,
                    "claim_type": None,
                    "confidence": None,
                    "needs_human": True,
                    "resolution_status": "unknown",
                }
            )
            continue
        load = (data.get("resolution") or {}).get("load") or {}
        classification = data.get("classification") or {}
        usage = data.get("usage") or {}
        decision = data.get("decision") or {}
        items.append(
            {
                "email_id": data.get("email_id") or path.stem,
                "load_number": load.get("LoadNumber")
                or (data.get("identifiers") or {}).get("load_number"),
                "claim_type": classification.get("claim_type"),
                "confidence": classification.get("confidence"),
                "needs_human": bool(data.get("needs_human", True)),
                "resolution_status": (data.get("resolution") or {}).get("status"),
                "llm_status": data.get("llm_status"),
                "subject": data.get("subject"),
                "processing_duration_ms": decision.get("processing_duration_ms"),
                "model_latency_ms": usage.get("latency_ms")
                if usage.get("latency_ms") is not None
                else decision.get("model_latency_ms"),
                "input_tokens": usage.get("input_tokens")
                if usage.get("input_tokens") is not None
                else decision.get("model_input_tokens"),
                "output_tokens": usage.get("output_tokens")
                if usage.get("output_tokens") is not None
                else decision.get("model_output_tokens"),
                "estimated_total_cost_usd": usage.get("estimated_total_cost_usd")
                if usage.get("estimated_total_cost_usd") is not None
                else decision.get("estimated_total_cost_usd"),
                "model_called": bool(decision.get("model_called"))
                or (
                    data.get("llm_status") == "ok"
                    and usage.get("input_tokens") is not None
                ),
            }
        )
    return {"packets": items, "count": len(items), "output_dir": str(output_dir)}


def _api_config() -> dict:
    """Public config for the review UI — never includes secret values."""
    has_env = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    return {
        "has_env_api_key": has_env,
        "default_mode": "live",
        "note": (
            "Paste an API key in the UI to override for this browser session, "
            "or leave blank to use ANTHROPIC_API_KEY from the server environment / .env. "
            "The key is never written into ActionPackets."
        ),
    }


def _list_emails(emails_dir: Path, output_dir: Path) -> dict:
    """Sample .eml files available for live process from the review UI."""
    items = []
    if emails_dir.is_dir():
        for path in sorted(emails_dir.glob("*.eml")):
            email_id = path.stem
            if not _EMAIL_ID_RE.fullmatch(email_id):
                continue
            packet_path = output_dir / f"{email_id}.json"
            items.append(
                {
                    "email_id": email_id,
                    "path": str(path),
                    "has_packet": packet_path.is_file(),
                }
            )
    return {
        "emails": items,
        "count": len(items),
        "emails_dir": str(emails_dir),
    }


def _process_email_id(
    email_id: str,
    *,
    emails_dir: Path,
    data_dir: Path,
    output_dir: Path,
    dry_run: bool = False,
    api_key: str | None = None,
) -> dict:
    """
    Run the claims pipeline for one sample email and write ActionPacket under output/.

    Optional api_key overrides ANTHROPIC_API_KEY for this request only (never logged
    or stored on the packet). Empty api_key falls back to the server environment.

    Returns a JSON-serializable result (includes http_status for the handler).
    """
    if not _EMAIL_ID_RE.fullmatch(email_id or ""):
        return {"ok": False, "error": "Invalid email_id", "http_status": 400}

    eml_path = (emails_dir / f"{email_id}.eml").resolve()
    try:
        emails_root = emails_dir.resolve()
        if emails_root not in eml_path.parents and eml_path != emails_root:
            return {"ok": False, "error": "Email path escapes emails_dir", "http_status": 400}
    except OSError:
        return {"ok": False, "error": "Cannot resolve email path", "http_status": 400}

    if not eml_path.is_file():
        return {
            "ok": False,
            "error": f"Email not found: {email_id}.eml",
            "http_status": 404,
        }

    mode = "dry_run" if dry_run else "live"
    key_source = "ui" if api_key else ("env" if os.environ.get("ANTHROPIC_API_KEY", "").strip() else "none")
    try:
        with temporary_api_key(api_key):
            packet = process_email(
                eml_path,
                data_dir=data_dir,
                output_dir=output_dir,
                use_llm=not dry_run,
                use_vision=not dry_run,
            )
    except MissingAPIKeyError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "email_id": email_id,
            "mode": mode,
            "api_key_source": key_source,
            "http_status": 400,
        }
    except Exception as exc:  # noqa: BLE001 — surface to UI; log traceback server-side
        traceback.print_exc()
        return {
            "ok": False,
            "error": f"Pipeline failed: {exc}",
            "email_id": email_id,
            "mode": mode,
            "api_key_source": key_source,
            "http_status": 500,
        }

    usage = asdict(packet.usage) if packet.usage else None
    decision = packet.decision or {}
    return {
        "ok": True,
        "email_id": packet.email_id,
        "mode": mode,
        "api_key_source": key_source if not dry_run else "n/a",
        "llm_status": packet.llm_status,
        "llm_error": packet.llm_error,
        "needs_human": packet.needs_human,
        "resolution_status": packet.resolution.status if packet.resolution else None,
        "load_number": (packet.resolution.load or {}).get("LoadNumber")
        if packet.resolution
        else None,
        "processing_duration_ms": decision.get("processing_duration_ms"),
        "usage": usage,
        "packet": packet.to_dict(),
        "http_status": 200,
    }


def _load_packet(output_dir: Path, email_id: str) -> dict | None:
    if not _EMAIL_ID_RE.fullmatch(email_id):
        return None
    path = output_dir / f"{email_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"email_id": email_id, "error": "malformed_json"}
    if not isinstance(data, dict):
        return {"email_id": email_id, "error": "malformed_json"}
    return data


# silence unused — kept for clarity if we add more static types later
_ = mimetypes
