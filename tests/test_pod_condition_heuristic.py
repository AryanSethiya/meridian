"""Focused tests for deterministic POD clean vs exception classification."""

from __future__ import annotations

from meridian_claims.analysis import classify_pod_exceptions_noted
from meridian_claims.evidence import damage_vs_clean_pod
from meridian_claims.models import PodResult


def test_clean_labeled_none_and_zero_values():
    cases = [
        "Exceptions: None",
        "Shortage: 0 cases",
        "Damage: None",
        "Visible damage: None",
        "Shipment received in full.",
        "Condition: seals intact, no exceptions noted",
        # Combined clean POD (MF-10487_POD style)
        (
            "PROOF OF DELIVERY\n"
            "Shipment received in full.\n"
            "Quantity received: 480 cases\n"
            "Quantity expected: 480 cases\n"
            "Shortage: 0 cases\n"
            "Condition: Good\n"
            "Visible damage: None\n"
            "Exceptions: None\n"
        ),
    ]
    for text in cases:
        assert classify_pod_exceptions_noted(text) is False, text


def test_explicit_exception_values():
    cases = [
        "Shortage: 12 cases",
        "Damage: water damage",
        "Exceptions: 3 pallets damaged",
        "Visible damage: crushed corner",
    ]
    for text in cases:
        assert classify_pod_exceptions_noted(text) is True, text


def test_positive_exception_wins_over_clean_labels():
    text = "Exceptions: None\nShortage: 12 cases\nVisible damage: None"
    assert classify_pod_exceptions_noted(text) is True


def test_ambiguous_prefers_unknown():
    cases = [
        "",
        "   ",
        "Receiver signed the form.",
        "See attached photos.",
        # Bare tokens without labeled values — do not guess
        "shortage damage exception",
    ]
    for text in cases:
        assert classify_pod_exceptions_noted(text) is None, repr(text)


def test_damage_vs_clean_pod_uses_labeled_clean_values():
    pod = PodResult(
        filename="MF-10487_POD.pdf",
        mode="text",
        status="readable",
        excerpt=(
            "Shortage: 0 cases\n"
            "Exceptions: None\n"
            "Visible damage: None\n"
            "Shipment received in full."
        ),
        findings="ok",
    )
    discs = damage_vs_clean_pod(
        "We are filing a shortage claim for 12 missing cases.",
        pod,
        {"PODReceived": "Y"},
    )
    assert any("clean delivery" in d.lower() for d in discs)


def test_damage_vs_clean_pod_skips_when_pod_notes_exception():
    pod = PodResult(
        filename="POD.pdf",
        mode="text",
        status="readable",
        excerpt="Exceptions: 3 pallets damaged\nShortage: 12 cases",
        findings="ok",
    )
    discs = damage_vs_clean_pod(
        "We are filing a shortage claim for 12 missing cases.",
        pod,
        {"PODReceived": "Y"},
    )
    assert not any("clean delivery" in d.lower() for d in discs)
