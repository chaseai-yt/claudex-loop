"""Agent 3 — eligibility_engine.

Applies four eligibility gates per line in sequence.
DNA/EA 3.0 lines auto-pass without an API call.

Gate order (stop at first failure):
  1. INCLUDED ITEM != "Yes"
  2. order_type not in {125, "XaaS-Annu"}
  3. ship_complete_flag != True  (conservative pass if undetectable)
  4. SPARE EQUIVALENT SKU NAME is non-empty
"""
from __future__ import annotations

from .checkpoint import write_checkpoint
from .models import EligibilityStatus, SPALine

_INELIGIBLE_ORDER_TYPES = {"125", "XaaS-Annu", "xaas-annu"}


def _is_dna_auto_pass(line: SPALine) -> bool:
    return (
        (line.bpa_buying_program or "").strip() == "EA 3.0"
        and line.unit_net_price == 0.0
        and line.sku.endswith("=")
    )


def _evaluate_line(line: SPALine) -> SPALine:
    if _is_dna_auto_pass(line):
        return line.model_copy(
            update={
                "eligibility_status": "DNA_AUTO_PASS",
                "dna_auto_pass": True,
                "eligibility_failed_gate": None,
            }
        )

    # Gate 1 — INCLUDED ITEM must not be "Yes"
    if (line.included_item or "").strip().lower() == "yes":
        return line.model_copy(
            update={"eligibility_status": "INELIGIBLE_GATE1", "eligibility_failed_gate": 1}
        )

    # Gate 2 — order type must not be 125 or XaaS-Annu
    ot = str(line.order_type or "").strip()
    if ot in _INELIGIBLE_ORDER_TYPES:
        return line.model_copy(
            update={"eligibility_status": "INELIGIBLE_GATE2", "eligibility_failed_gate": 2}
        )

    # Gate 3 — ship complete must not be set
    # If undetectable, conservatively pass but retain gate3_unverifiable flag
    if line.ship_complete_flag is True:
        return line.model_copy(
            update={"eligibility_status": "INELIGIBLE_GATE3", "eligibility_failed_gate": 3}
        )

    # Gate 4 — spare equivalent SKU must be populated
    if not (line.spare_sku or "").strip():
        return line.model_copy(
            update={"eligibility_status": "INELIGIBLE_GATE4", "eligibility_failed_gate": 4}
        )

    return line.model_copy(
        update={"eligibility_status": "ELIGIBLE", "eligibility_failed_gate": None}
    )


def run(lines: list[SPALine]) -> list[SPALine]:
    result = [_evaluate_line(line) for line in lines]
    write_checkpoint(3, result)
    return result
