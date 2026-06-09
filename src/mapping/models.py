from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel


class MappingEntry(BaseModel):
    id: str
    cto_sku: str
    spare_sku: str
    approved_by: str
    approved_at: datetime
    cisco_ref: str
    is_strict_match: bool
    version: int
    effective_date: date
    expiry_date: Optional[date] = None
    superseded_by: Optional[str] = None
    is_active: bool


class MappingTable(BaseModel):
    entries: List[MappingEntry]
    version: int
    created_at: datetime
