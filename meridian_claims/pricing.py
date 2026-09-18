"""
Estimated Anthropic cost from reported token usage.

Pricing is configurable via environment variables so rates can be updated
without code changes. Costs are labeled "estimated" — not invoices.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelPricing:
    """USD per million tokens (input / output)."""

    input_per_mtok: float
    output_per_mtok: float
    source: str = "env_or_default"

    def estimate(
        self,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> dict[str, Any]:
        """
        Return estimated cost fields.

        Missing token counts → null estimated costs (do not invent tokens).
        """
        if input_tokens is None and output_tokens is None:
            return {
                "input_tokens": None,
                "output_tokens": None,
                "estimated_input_cost_usd": None,
                "estimated_output_cost_usd": None,
                "estimated_total_cost_usd": None,
                "pricing_input_per_mtok_usd": self.input_per_mtok,
                "pricing_output_per_mtok_usd": self.output_per_mtok,
                "pricing_source": self.source,
                "note": "Estimated model cost — tokens unavailable.",
            }

        in_tok = int(input_tokens or 0)
        out_tok = int(output_tokens or 0)
        in_cost = (in_tok / 1_000_000.0) * self.input_per_mtok
        out_cost = (out_tok / 1_000_000.0) * self.output_per_mtok
        return {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "estimated_input_cost_usd": round(in_cost, 8),
            "estimated_output_cost_usd": round(out_cost, 8),
            "estimated_total_cost_usd": round(in_cost + out_cost, 8),
            "pricing_input_per_mtok_usd": self.input_per_mtok,
            "pricing_output_per_mtok_usd": self.output_per_mtok,
            "pricing_source": self.source,
            "note": (
                "Estimated model cost from configured USD/MTok rates "
                "and Anthropic-reported token usage — not an invoice."
            ),
        }


def load_pricing() -> ModelPricing:
    """
    Read rates from env (easy to update):

      MERIDIAN_PRICE_INPUT_PER_MTOK   default 3.00
      MERIDIAN_PRICE_OUTPUT_PER_MTOK  default 15.00

    Defaults approximate Claude Sonnet list prices; override when rates change.
    """
    try:
        inp = float(os.environ.get("MERIDIAN_PRICE_INPUT_PER_MTOK", "3.0"))
    except ValueError:
        inp = 3.0
    try:
        out = float(os.environ.get("MERIDIAN_PRICE_OUTPUT_PER_MTOK", "15.0"))
    except ValueError:
        out = 15.0
    source = "env" if (
        os.environ.get("MERIDIAN_PRICE_INPUT_PER_MTOK")
        or os.environ.get("MERIDIAN_PRICE_OUTPUT_PER_MTOK")
    ) else "default_sonnet_approx"
    return ModelPricing(input_per_mtok=inp, output_per_mtok=out, source=source)
