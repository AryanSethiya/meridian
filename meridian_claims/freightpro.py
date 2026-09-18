"""Read-only FreightPro snapshot accessors (CSV exports)."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Columns that Legal treats as PII — never send to a model unless redacted.
PII_LOAD_FIELDS = ("DriverName", "DriverPhone")

# Snapshot metadata for action packets / model context.
# Do not invent per-field as_of timestamps — this export does not provide them.
FREIGHTPRO_SNAPSHOT_DATE = "2026-08-26"
FREIGHTPRO_SOURCE_META: dict[str, Any] = {
    "source": "FreightPro snapshot",
    "snapshot_date": FREIGHTPRO_SNAPSHOT_DATE,
    "freshness_note": (
        "Reporting data from the assignment snapshot. Real FreightPro reporting "
        "data can be approximately 14 hours behind live operations. Individual "
        "field-level as_of timestamps are not available in this export."
    ),
    "status_semantics": "recorded_status_not_live_tracking",
}


def freightpro_source_meta() -> dict[str, Any]:
    """Copy of snapshot/freshness metadata for packets and OBSERVED_FACTS."""
    return dict(FREIGHTPRO_SOURCE_META)


SAFE_LOAD_FIELDS = (
    "LoadID",
    "LoadNumber",
    "Status",
    "ShipperID",
    "CarrierID",
    "OriginCity",
    "OriginState",
    "OriginZip",
    "DestCity",
    "DestState",
    "DestZip",
    "PickupDate",
    "DeliveryDate",
    "EquipmentType",
    "Commodity",
    "WeightLbs",
    "Miles",
    "CustomerRate",
    "CarrierRate",
    "FuelSurcharge",
    "PONumber",
    "BOLNumber",
    "PRONumber",
    "PODReceived",
    "ExceptionNotes",
    "CreatedAt",
)


@dataclass
class FreightProDB:
    data_dir: Path
    loads: list[dict[str, str]]
    carriers: dict[str, dict[str, str]]
    shippers: dict[str, dict[str, str]]
    by_load_number: dict[str, list[dict[str, str]]]
    by_po: dict[str, list[dict[str, str]]]
    by_bol: dict[str, list[dict[str, str]]]
    by_pro: dict[str, list[dict[str, str]]]
    by_mc: dict[str, list[dict[str, str]]]
    by_scac: dict[str, list[dict[str, str]]]

    @classmethod
    def load(cls, data_dir: Path | None = None) -> FreightProDB:
        root = Path(data_dir) if data_dir else _default_data_dir()
        fp = root / "freightpro"
        loads = _read_csv(fp / "loads.csv")
        carriers_list = _read_csv(fp / "carriers.csv")
        shippers_list = _read_csv(fp / "shippers.csv")

        carriers = {r["CarrierID"]: r for r in carriers_list}
        shippers = {r["ShipperID"]: r for r in shippers_list}

        by_load_number: dict[str, list[dict[str, str]]] = {}
        by_po: dict[str, list[dict[str, str]]] = {}
        by_bol: dict[str, list[dict[str, str]]] = {}
        by_pro: dict[str, list[dict[str, str]]] = {}
        for row in loads:
            _index(by_load_number, row.get("LoadNumber", ""), row)
            _index(by_po, row.get("PONumber", ""), row)
            _index(by_bol, row.get("BOLNumber", ""), row)
            _index(by_pro, row.get("PRONumber", ""), row)

        by_mc: dict[str, list[dict[str, str]]] = {}
        by_scac: dict[str, list[dict[str, str]]] = {}
        for row in carriers_list:
            _index(by_mc, row.get("MCNumber", ""), row)
            scac = (row.get("SCAC") or "").strip().upper()
            if scac:
                _index(by_scac, scac, row)

        return cls(
            data_dir=root,
            loads=loads,
            carriers=carriers,
            shippers=shippers,
            by_load_number=by_load_number,
            by_po=by_po,
            by_bol=by_bol,
            by_pro=by_pro,
            by_mc=by_mc,
            by_scac=by_scac,
        )

    def raw_load_by_number(self, load_number: str) -> dict[str, str] | None:
        """Unredacted CSV row for PII collection (never send this to a model)."""
        rows = self.by_load_number.get(load_number, [])
        return rows[0] if len(rows) == 1 else None

    def enrich_load(self, load: dict[str, str], *, include_pii: bool = False) -> dict[str, Any]:
        """Return a coordinator-facing load dict with shipper/carrier joined."""
        out: dict[str, Any] = {k: load.get(k, "") for k in SAFE_LOAD_FIELDS}
        if include_pii:
            for k in PII_LOAD_FIELDS:
                out[k] = load.get(k, "")
        else:
            for k in PII_LOAD_FIELDS:
                out[k] = "[REDACTED]" if load.get(k) else ""

        shipper = self.shippers.get(load.get("ShipperID", ""), {})
        carrier = self.carriers.get(load.get("CarrierID", ""), {})
        out["shipper_name"] = shipper.get("Name", "")
        out["shipper_credit"] = shipper.get("CreditStatus", "")
        out["carrier_legal_name"] = carrier.get("LegalName", "")
        out["carrier_mc"] = carrier.get("MCNumber", "")
        out["carrier_scac"] = carrier.get("SCAC", "")
        out["carrier_status"] = carrier.get("Status", "")
        out["lane"] = (
            f"{load.get('OriginCity', '')}, {load.get('OriginState', '')}"
            f" → {load.get('DestCity', '')}, {load.get('DestState', '')}"
        )
        return out

    def find_carrier_mention(self, text: str) -> dict[str, str] | None:
        """
        Match a free-text carrier mention using FreightPro carrier rows only.

        Preference order (deterministic, no AI fuzzy matching):
        1. SCAC as a token / compact substring
        2. MCNumber (digits or MC-prefixed form)
        3. Normalized LegalName containment (longest match wins)
        """
        if not (text or "").strip():
            return None

        upper = text.upper()
        tokens = set(re.findall(r"[A-Z0-9]+", upper.replace(".", " ")))
        compact = re.sub(r"[^A-Z0-9]", "", upper)

        # 1) SCAC
        for scac, rows in self.by_scac.items():
            scac_u = (scac or "").strip().upper()
            if not scac_u or not rows:
                continue
            if scac_u in tokens or (len(scac_u) >= 2 and scac_u in compact):
                return rows[0]

        # 2) MCNumber
        for mc, rows in self.by_mc.items():
            mc_u = (mc or "").strip().upper()
            if not mc_u or not rows:
                continue
            if mc_u in tokens or mc_u in compact:
                return rows[0]
            if f"MC{mc_u}" in compact:
                return rows[0]

        # 3) Normalized LegalName (substring; longest name wins to prefer specificity)
        normalized_text = _normalize_name(text)
        best: dict[str, str] | None = None
        best_len = 0
        for carrier in self.carriers.values():
            name = carrier.get("LegalName", "")
            norm = _normalize_name(name)
            if len(norm) >= 4 and norm in normalized_text and len(norm) > best_len:
                best = carrier
                best_len = len(norm)
        return best


def redacted_load_view(enriched: dict[str, Any]) -> dict[str, Any]:
    """Ensure PII fields stay redacted for model / packet payloads."""
    out = dict(enriched)
    for k in PII_LOAD_FIELDS:
        if out.get(k) and out[k] != "[REDACTED]":
            out[k] = "[REDACTED]"
    return out


def _default_data_dir() -> Path:
    # meridian_claims/ -> repo root -> data/
    return Path(__file__).resolve().parent.parent / "data"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _index(index: dict[str, list[dict[str, str]]], key: str, row: dict[str, str]) -> None:
    key = (key or "").strip()
    if not key:
        return
    index.setdefault(key, []).append(row)


def _normalize_name(value: str) -> str:
    return (
        value.upper()
        .replace(".", "")
        .replace(",", "")
        .replace("-", " ")
        .replace("  ", " ")
        .strip()
    )
