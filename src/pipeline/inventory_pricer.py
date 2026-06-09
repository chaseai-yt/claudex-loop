"""Agent 4 — inventory_pricer.

For each eligible (non-DNA) line: calls the TDS Inventory API,
calculates spare_net_price, enforces pricing ceiling.

  spare_net_price = spare_list_price × (1 − spare_sku_discount)

  spare_net_price > UNIT NET PRICE        → BLOCK
  spare_net_price > 0.95 × UNIT NET PRICE → WARN
  otherwise                               → OK

DNA auto-pass lines are priced at $0 and skipped for API calls.
"""
from __future__ import annotations

from .checkpoint import write_checkpoint
from .inventory_client import PipelineInventoryClient
from .models import SPALine

_WARN_THRESHOLD = 0.95


def _price_line(line: SPALine, client: PipelineInventoryClient) -> SPALine:
    status = line.eligibility_status

    if status not in ("ELIGIBLE", "DNA_AUTO_PASS"):
        # Not eligible — skip pricing entirely
        return line.model_copy(update={"pricing_status": "SKIPPED"})

    if status == "DNA_AUTO_PASS":
        # DNA lines are $0 — always OK, no API call
        return line.model_copy(
            update={
                "available_qty": 999,
                "spare_list_price": 0.0,
                "spare_sku_discount": 0.0,
                "spare_net_price": 0.0,
                "pricing_status": "OK",
            }
        )

    spare_sku = line.spare_sku or ""
    resp = client.get_inventory([spare_sku])
    record = resp.records.get(spare_sku)

    available_qty = record.available_qty if record else 0
    spare_list_price = record.spare_list_price if record else 0.0
    spare_sku_discount = record.spare_sku_discount if record else 0.0
    inventory_is_stale = resp.is_stale

    # Zero-price guard: API misconfiguration or missing data — cannot price Option B
    if spare_list_price == 0.0:
        return line.model_copy(
            update={
                "available_qty": available_qty,
                "spare_list_price": 0.0,
                "spare_sku_discount": spare_sku_discount,
                "spare_net_price": 0.0,
                "pricing_status": "BLOCK",
                "missing_price": True,
                "inventory_is_stale": inventory_is_stale,
            }
        )

    spare_net_price = spare_list_price * (1.0 - spare_sku_discount)

    ceiling = line.unit_net_price
    if ceiling > 0 and spare_net_price > ceiling:
        pricing_status = "BLOCK"
    elif ceiling > 0 and spare_net_price > _WARN_THRESHOLD * ceiling:
        pricing_status = "WARN"
    else:
        pricing_status = "OK"

    return line.model_copy(
        update={
            "available_qty": available_qty,
            "spare_list_price": spare_list_price,
            "spare_sku_discount": spare_sku_discount,
            "spare_net_price": spare_net_price,
            "pricing_status": pricing_status,
            "inventory_is_stale": inventory_is_stale,
        }
    )


def run(lines: list[SPALine], client: PipelineInventoryClient) -> list[SPALine]:
    result = [_price_line(line, client) for line in lines]
    write_checkpoint(4, result)
    return result
