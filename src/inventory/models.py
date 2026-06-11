from datetime import datetime
from typing import Dict, List

from pydantic import BaseModel


class InventoryRecord(BaseModel):
    sku: str
    available_qty: int
    warehouse_locations: List[str]
    allocation_status: str  # e.g. "AVAILABLE", "RESERVED", "ALLOCATED"
    backorder_status: bool
    as_of: datetime


class InventoryResponse(BaseModel):
    records: Dict[str, InventoryRecord]  # keyed by SKU
    queried_at: datetime
    is_degraded: bool
