"""Pipeline-specific Pydantic models.

Separate from src/inventory/models.py — the existing Maple AI service models
are NOT modified. PipelineInventoryRecord extends the base with pricing fields
that the TDS Inventory API must return.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from ..inventory.models import InventoryRecord


# ---------------------------------------------------------------------------
# Pipeline inventory models (extend base without modifying it)
# ---------------------------------------------------------------------------

class PipelineInventoryRecord(InventoryRecord):
    spare_list_price: float = Field(default=0.0, ge=0.0)
    spare_sku_discount: float = Field(default=0.0, ge=0.0, le=1.0)


class PipelineInventoryResponse(BaseModel):
    records: Dict[str, PipelineInventoryRecord]
    queried_at: datetime
    is_degraded: bool
    is_stale: bool = False
    data_as_of: Optional[datetime] = None


# ---------------------------------------------------------------------------
# SPA line item (parsed from Excel)
# ---------------------------------------------------------------------------

EligibilityStatus = Literal[
    "ELIGIBLE",
    "DNA_AUTO_PASS",
    "INELIGIBLE_GATE1",
    "INELIGIBLE_GATE2",
    "INELIGIBLE_GATE3",
    "INELIGIBLE_GATE4",
]

PricingStatus = Literal["OK", "WARN", "BLOCK", "SKIPPED"]

Confidence = Literal["HIGH", "MEDIUM", "LOW"]

ReviewFlag = Literal["WARN", "BLOCK", "STALE_DATA", "STALE_OR_MISSING_PRICE"]


class SPALine(BaseModel):
    line_number: str
    ship_set_id: int
    sku: str
    description: str
    quantity: int
    unit_net_price: float
    spare_sku: Optional[str]
    included_item: Optional[str]
    order_type: Optional[str]
    ship_complete_flag: Optional[bool]
    gate3_unverifiable: bool = False
    bpa_buying_program: Optional[str]
    eligibility_status: Optional[EligibilityStatus] = None
    eligibility_failed_gate: Optional[int] = None
    # Filled by inventory_pricer
    available_qty: Optional[int] = None
    spare_list_price: Optional[float] = None
    spare_sku_discount: Optional[float] = None
    spare_net_price: Optional[float] = None
    pricing_status: Optional[PricingStatus] = None
    dna_auto_pass: bool = False
    inventory_is_stale: bool = False
    missing_price: bool = False


class ReviewQueueItem(BaseModel):
    line_number: str
    sku: str
    spare_sku: Optional[str]
    flag: ReviewFlag
    reason: str
    spare_net_price: Optional[float] = None
    unit_net_price: Optional[float] = None


# ---------------------------------------------------------------------------
# Per-ship-set recommendation
# ---------------------------------------------------------------------------

class ShipSetOption(BaseModel):
    available: bool
    reason: Optional[str] = None
    lead_time: str
    net_price: Optional[float] = None
    warehouse_coverage_pct: Optional[float] = None


class ShipSetRecommendation(BaseModel):
    ship_set_id: int
    option_a: ShipSetOption
    option_b: ShipSetOption
    option_c_suppressed: bool = True
    review_queue: List[ReviewQueueItem]
    confidence: Confidence
    eligible_line_count: int
    total_line_count: int


# ---------------------------------------------------------------------------
# Full pipeline recommendation output
# ---------------------------------------------------------------------------

class PipelineRecommendation(BaseModel):
    deal_id: str
    generated_at: datetime
    ship_sets: List[ShipSetRecommendation]
    total_lines: int
    total_eligible: int


# ---------------------------------------------------------------------------
# outcomes.jsonl record
# ---------------------------------------------------------------------------

class OutcomeRecord(BaseModel):
    deal_id: str
    timestamp: datetime
    lines_eligible: int
    option_b_available: bool
    option_selected: Optional[str] = None
    rep_id: Optional[str] = None
