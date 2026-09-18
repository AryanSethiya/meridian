"""Eval harness: run claims@ sample emails and report a compact table."""

from __future__ import annotations

from pathlib import Path

from meridian_claims.email_parser import parse_eml
from meridian_claims.pipeline import process_email

# Spot-check expectations from the sample snapshot (2026-08-26).
EXPECTED_LOADS = {
    "035": "MF-10487",
    "036": "MF-10005",
    "037": "MF-10055",
    "038": "MF-10088",
    "039": "MF-10027",
    "040": "MF-10031",
    "041": "MF-10032",
    "042": "MF-10034",
}


def find_claims_emails(emails_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for path in sorted(emails_dir.glob("*.eml")):
        parsed = parse_eml(path, attachment_dir=None)
        if "claims@" in parsed.to_addr.lower():
            paths.append(path)
    return paths


def run_eval(
    *,
    data_dir: Path | None = None,
    emails_dir: Path | None = None,
    output_dir: Path | None = None,
    dry_run: bool = False,
) -> int:
    repo = Path(__file__).resolve().parent.parent
    data = Path(data_dir) if data_dir else repo / "data"
    emails = Path(emails_dir) if emails_dir else data / "emails"
    out = Path(output_dir) if output_dir else repo / "output" / "eval"

    claims = find_claims_emails(emails)
    if not claims:
        print(f"No claims@ emails found under {emails}")
        return 1

    print(
        f"{'id':<5} {'type':<10} {'resolve':<12} {'method':<12} "
        f"{'load':<10} {'expect':<10} {'ok':<4} {'pod':<12} human"
    )
    print("-" * 95)

    correct = 0
    total_checked = 0
    for path in claims:
        packet = process_email(
            path,
            data_dir=data,
            output_dir=out,
            use_llm=not dry_run,
            use_vision=not dry_run,
        )
        got = (packet.resolution.load or {}).get("LoadNumber", "—")
        expect = EXPECTED_LOADS.get(packet.email_id, "?")
        ok = "—"
        if packet.email_id in EXPECTED_LOADS:
            total_checked += 1
            match = got == expect
            ok = "Y" if match else "N"
            if match:
                correct += 1
        print(
            f"{packet.email_id:<5} "
            f"{packet.classification.claim_type:<10} "
            f"{packet.resolution.status:<12} "
            f"{(packet.resolution.method or '—'):<12} "
            f"{got:<10} "
            f"{expect:<10} "
            f"{ok:<4} "
            f"{packet.pod.mode:<12} "
            f"{packet.needs_human}"
        )

    print("-" * 95)
    if total_checked:
        print(f"Load resolution accuracy: {correct}/{total_checked}")
    print(f"Packets written under {out}")
    if total_checked and correct < total_checked:
        return 1
    return 0
