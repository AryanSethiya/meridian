"""
Minimal local HTTP server for the coordinator review UI.

Serves static UI files and ActionPacket JSON from output/.
Never sends email, never calls models, never writes ActionPackets.
"""

from __future__ import annotations

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_DIR = Path(__file__).resolve().parent / "review_ui"


def serve_review_ui(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    output_dir: Path | None = None,
) -> int:
    out = Path(output_dir) if output_dir else REPO_ROOT / "output"
    out.mkdir(parents=True, exist_ok=True)

    if not UI_DIR.exists():
        print(f"Review UI assets missing: {UI_DIR}")
        return 1

    handler = _make_handler(out, UI_DIR)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Coordinator review UI: http://{host}:{port}/")
    print(f"Loading ActionPackets from: {out}")
    print("Human review only — no outbound email is sent by this demo.")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


def _make_handler(output_dir: Path, ui_dir: Path):
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
            m = re.fullmatch(r"/api/packets/([A-Za-z0-9_-]+)", path)
            if m:
                packet = _load_packet(output_dir, m.group(1))
                if packet is None:
                    return self._send_error(404, f"Packet not found: {m.group(1)}")
                return self._send_json(packet)

            # Block path traversal to anything outside ui/output
            return self._send_error(404, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            # Intentionally no write / send endpoints.
            return self._send_error(
                405,
                "Review actions are client-only (localStorage). No email send path.",
            )

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

        def _send_json(self, obj) -> None:
            data = json.dumps(obj, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_error(self, code: int, message: str) -> None:
            data = json.dumps({"error": message}).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return ReviewHandler


def _list_packets(output_dir: Path) -> dict:
    items = []
    for path in sorted(output_dir.glob("*.json")):
        name = path.name
        if name.endswith(".decision.json"):
            continue
        if name in ("eval_all.json",):
            continue
        # Skip nested dirs; only top-level action packets
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
            }
        )
    return {"packets": items, "count": len(items), "output_dir": str(output_dir)}


def _load_packet(output_dir: Path, email_id: str) -> dict | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", email_id):
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
