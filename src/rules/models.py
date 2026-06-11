from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel


Recommendation = Literal["STOCK", "HYBRID", "DROP_SHIP", "MANUAL_REVIEW"]


class LineScore(BaseModel):
    sku: str
    build_group: str
    required_qty: int
    available_qty: int
    capped_qty: int  # min(available_qty, required_qty)
    spare_sku: Optional[str]
    is_strict_match: bool
    recommendation: Recommendation


class FulfillmentRecommendation(BaseModel):
    quote_id: str
    recommendation_id: UUID
    overall: Recommendation
    score_pct: float  # 0.0 – 1.0
    line_scores: List[LineScore]
    thresholds_version: int
    mapping_table_version: int
    generated_at: datetime
    as_of_inventory: datetime
