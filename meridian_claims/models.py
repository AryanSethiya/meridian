"""Typed structures for the coordinator action packet."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Identifiers:
    load_number: str | None = None
    po: str | None = None
    bol: str | None = None
    pro: str | None = None


@dataclass
class Classification:
    claim_type: str
    confidence: float
    alleged_facts: str = ""
    source: str = "deterministic"  # deterministic | llm | llm_fallback


@dataclass
class CarrierInfo:
    legal_name: str | None = None
    mc: str | None = None
    scac: str | None = None
    status: str | None = None
    email_mention: str | None = None


@dataclass
class PodResult:
    filename: str | None = None
    mode: str = "none"  # none | text | vision | unreadable | blank_page | invalid | vision_blocked
    excerpt: str = ""
    findings: str = ""
    status: str = "missing"  # missing | readable | unreadable | no_attachment | invalid


@dataclass
class Resolution:
    status: str  # resolved | ambiguous | unresolved
    method: str | None = None
    confidence: float = 0.0
    load: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None
    conflicts: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    multi_load_ids: list[str] = field(default_factory=list)
    # Sender vs FreightPro shipper ContactEmail/domain: matched | conflict | unavailable
    shipper_corroboration: str = "unavailable"


@dataclass
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    model: str = ""


@dataclass
class ActionPacket:
    email_id: str
    summary: str
    from_addr: str
    to_addr: str
    subject: str
    date: str
    classification: Classification
    identifiers: Identifiers
    resolution: Resolution
    carrier: CarrierInfo
    pod: PodResult
    discrepancies: list[str] = field(default_factory=list)
    draft_reply: str = ""
    needs_human: bool = True
    needs_human_reasons: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    usage: ModelUsage | None = None
    # Explicit split: deterministic observations vs model interpretation
    observed: dict[str, Any] = field(default_factory=dict)
    interpretation: dict[str, Any] = field(default_factory=dict)
    llm_status: str = "skipped"  # ok | skipped | error | dry_run | pii_blocked
    llm_error: str | None = None
    # Observable PII gate outcome — never contains original PII values
    pii_gate: dict[str, Any] = field(default_factory=dict)
    # Lightweight audit/decision record (no bodies, no driver PII)
    decision: dict[str, Any] = field(default_factory=dict)
    # Coordinator analysis: separated facts / unknowns / AI / draft
    analysis: dict[str, Any] = field(default_factory=dict)
    # Auditable attachment inventory / POD selection (no binary payloads)
    attachments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
