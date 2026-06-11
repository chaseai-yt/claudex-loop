from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class QuoteLine(BaseModel):
    sku: str
    description: str
    quantity: int
    build_group: str
    is_cto: bool
    spare_sku: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)


class Quote(BaseModel):
    quote_id: str
    cisco_deal_id: str
    lines: List[QuoteLine]
    parsed_at: datetime
    confirmed: bool = False


class ParseResult(BaseModel):
    quote: Quote
    low_confidence_lines: List[int]  # indices into quote.lines
    warnings: List[str]
