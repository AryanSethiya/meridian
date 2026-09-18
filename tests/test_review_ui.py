"""Smoke tests for the local review UI server (no browser framework)."""

from __future__ import annotations

import json
from pathlib import Path

from meridian_claims.review_server import _list_packets, _load_packet


def test_list_packets_discovers_action_json(tmp_path: Path):
    (tmp_path / "035.json").write_text(
        json.dumps(
            {
                "email_id": "035",
                "subject": "Damage",
                "needs_human": True,
                "classification": {"claim_type": "damage", "confidence": 0.9},
                "identifiers": {"load_number": "MF-10487"},
                "resolution": {
                    "status": "resolved",
                    "load": {"LoadNumber": "MF-10487"},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "035.decision.json").write_text("{}", encoding="utf-8")
    (tmp_path / "eval_all.json").write_text("{}", encoding="utf-8")
    (tmp_path / "bad.json").write_text("{not-json", encoding="utf-8")

    listing = _list_packets(tmp_path)
    ids = {p["email_id"] for p in listing["packets"]}
    assert "035" in ids
    assert "eval_all" not in ids
    bad = next(p for p in listing["packets"] if p["email_id"] == "bad")
    assert bad.get("error") == "malformed_json"


def test_load_packet_rejects_traversal(tmp_path: Path):
    assert _load_packet(tmp_path, "../etc/passwd") is None
    assert _load_packet(tmp_path, "missing") is None
