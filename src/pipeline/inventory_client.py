"""PipelineInventoryClient ABC and implementations.

Separate from src/inventory/client.py — the existing Maple AI ABC is
NOT modified. Pipeline-specific ABC returns PipelineInventoryResponse,
which includes spare_list_price and spare_sku_discount per record.
"""
from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import PipelineInventoryRecord, PipelineInventoryResponse

_DEFAULT_MOCK_JSON = Path(__file__).parent.parent.parent / "data" / "mock" / "pipeline_inventory.json"


class PipelineInventoryClient(ABC):
    @abstractmethod
    def get_inventory(self, skus: list[str]) -> PipelineInventoryResponse:
        ...


class PipelineMockInventoryClient(PipelineInventoryClient):
    """Mock client backed by pipeline_inventory.json.

    Simulates ~50ms latency. Supports MOCK_INVENTORY_FAIL=true for degraded mode.
    JSON format per SKU:
    {
      "C9300-48P-A=": {
        "available_qty": 10,
        "spare_list_price": 5800.00,
        "spare_sku_discount": 0.108,
        "warehouse_locations": ["PHX"],
        "allocation_status": "AVAILABLE",
        "backorder_status": false
      }
    }
    """

    def __init__(self, json_path: Optional[Path] = None) -> None:
        path = Path(json_path) if json_path else _DEFAULT_MOCK_JSON
        self._data: dict = {}
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                self._data = json.load(fh)

    def get_inventory(self, skus: list[str]) -> PipelineInventoryResponse:
        time.sleep(0.05)
        queried_at = datetime.now(timezone.utc)
        is_degraded = os.environ.get("MOCK_INVENTORY_FAIL", "false").lower() == "true"

        records: dict[str, PipelineInventoryRecord] = {}
        for sku in skus:
            if is_degraded:
                records[sku] = PipelineInventoryRecord(
                    sku=sku,
                    available_qty=0,
                    warehouse_locations=[],
                    allocation_status="DEGRADED",
                    backorder_status=True,
                    as_of=queried_at,
                    spare_list_price=0.0,
                    spare_sku_discount=0.0,
                )
            elif sku in self._data:
                entry = self._data[sku]
                records[sku] = PipelineInventoryRecord(
                    sku=sku,
                    available_qty=entry.get("available_qty", 0),
                    warehouse_locations=entry.get("warehouse_locations", []),
                    allocation_status=entry.get("allocation_status", "UNKNOWN"),
                    backorder_status=entry.get("backorder_status", False),
                    as_of=queried_at,
                    spare_list_price=float(entry.get("spare_list_price", 0.0)),
                    spare_sku_discount=float(entry.get("spare_sku_discount", 0.0)),
                )
            else:
                records[sku] = PipelineInventoryRecord(
                    sku=sku,
                    available_qty=0,
                    warehouse_locations=[],
                    allocation_status="NOT_FOUND",
                    backorder_status=True,
                    as_of=queried_at,
                    spare_list_price=0.0,
                    spare_sku_discount=0.0,
                )

        return PipelineInventoryResponse(
            records=records,
            queried_at=queried_at,
            is_degraded=is_degraded,
        )


class LiveInventoryClient(PipelineInventoryClient):
    """HTTP client for the TDS Inventory API.

    Reads INVENTORY_API_URL and INVENTORY_API_KEY from environment.
    API is expected to return a JSON body with per-SKU records matching
    PipelineInventoryRecord schema.
    """

    def __init__(self) -> None:
        import urllib.request  # stdlib only; no requests dep required for MVP
        self._url = os.environ["INVENTORY_API_URL"].rstrip("/")
        self._key = os.environ["INVENTORY_API_KEY"]
        self._urllib = urllib.request

    def get_inventory(self, skus: list[str]) -> PipelineInventoryResponse:
        import urllib.request
        import urllib.error

        queried_at = datetime.now(timezone.utc)
        payload = json.dumps({"skus": skus}).encode()
        req = urllib.request.Request(
            f"{self._url}/inventory",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-API-Key": self._key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw: dict = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Inventory API HTTP {exc.code}: {exc.reason}") from exc
        except Exception as exc:
            raise RuntimeError(f"Inventory API unreachable: {exc}") from exc

        records: dict[str, PipelineInventoryRecord] = {}
        for sku, entry in raw.get("records", {}).items():
            records[sku] = PipelineInventoryRecord(
                sku=sku,
                available_qty=int(entry.get("available_qty", 0)),
                warehouse_locations=entry.get("warehouse_locations", []),
                allocation_status=entry.get("allocation_status", "UNKNOWN"),
                backorder_status=bool(entry.get("backorder_status", False)),
                as_of=queried_at,
                spare_list_price=float(entry.get("spare_list_price", 0.0)),
                spare_sku_discount=float(entry.get("spare_sku_discount", 0.0)),
            )

        is_degraded = raw.get("is_degraded", False)
        return PipelineInventoryResponse(
            records=records,
            queried_at=queried_at,
            is_degraded=is_degraded,
        )
