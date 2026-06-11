from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import InventoryRecord, InventoryResponse

_DEFAULT_JSON = (
    Path(__file__).parent.parent.parent / "data" / "mock" / "inventory.json"
)


class InventoryClient(ABC):
    @abstractmethod
    def get_inventory(self, skus: list[str]) -> InventoryResponse:
        ...


class MockInventoryClient(InventoryClient):
    """Mock inventory client backed by a local JSON file.

    Simulates ~200 ms network latency.
    If the env var MOCK_INVENTORY_FAIL=true, returns a degraded response
    with all quantities set to 0 to trigger circuit-breaker behaviour.
    """

    def __init__(self, json_path: Optional[Path] = None) -> None:
        path = Path(json_path) if json_path else _DEFAULT_JSON
        with open(path, encoding="utf-8") as fh:
            raw: dict = json.load(fh)

        self._data: dict[str, dict] = raw

    # ------------------------------------------------------------------

    def get_inventory(self, skus: list[str]) -> InventoryResponse:
        # Simulate latency
        time.sleep(0.2)

        queried_at = datetime.now(timezone.utc)
        is_degraded = os.environ.get("MOCK_INVENTORY_FAIL", "false").lower() == "true"

        records: dict[str, InventoryRecord] = {}
        for sku in skus:
            if is_degraded:
                records[sku] = InventoryRecord(
                    sku=sku,
                    available_qty=0,
                    warehouse_locations=[],
                    allocation_status="DEGRADED",
                    backorder_status=True,
                    as_of=queried_at,
                )
            elif sku in self._data:
                entry = self._data[sku]
                records[sku] = InventoryRecord(
                    sku=sku,
                    available_qty=entry.get("available_qty", 0),
                    warehouse_locations=entry.get("warehouse_locations", []),
                    allocation_status=entry.get("allocation_status", "UNKNOWN"),
                    backorder_status=entry.get("backorder_status", False),
                    as_of=queried_at,
                )
            else:
                # SKU not in mock data → treat as zero stock
                records[sku] = InventoryRecord(
                    sku=sku,
                    available_qty=0,
                    warehouse_locations=[],
                    allocation_status="NOT_FOUND",
                    backorder_status=True,
                    as_of=queried_at,
                )

        return InventoryResponse(
            records=records,
            queried_at=queried_at,
            is_degraded=is_degraded,
        )
