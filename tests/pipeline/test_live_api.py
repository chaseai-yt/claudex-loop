"""Integration smoke-test for LiveInventoryClient.

Skipped unless INVENTORY_API_URL is set in the environment.
Run against the real TDS Inventory API with a known Deal ID (83737219).

  INVENTORY_API_URL=https://... INVENTORY_API_KEY=... pytest tests/pipeline/test_live_api.py -v
"""
from __future__ import annotations

import os
import time

import pytest

from src.pipeline.inventory_client import LiveInventoryClient

_KNOWN_SKU = "C9300-48P-A="
_MAX_LATENCY_S = 2.0


@pytest.mark.skipif(
    not os.environ.get("INVENTORY_API_URL"),
    reason="Live API not configured — set INVENTORY_API_URL to run",
)
class TestLiveInventoryClient:
    def test_schema_fields_present(self):
        """API response must include all three required pricing fields, non-negative."""
        client = LiveInventoryClient()
        resp = client.get_inventory([_KNOWN_SKU])
        assert _KNOWN_SKU in resp.records, f"SKU {_KNOWN_SKU} missing from response"
        rec = resp.records[_KNOWN_SKU]
        assert rec.available_qty >= 0, "available_qty must be non-negative"
        assert rec.spare_list_price >= 0.0, "spare_list_price must be non-negative"
        assert 0.0 <= rec.spare_sku_discount <= 1.0, "spare_sku_discount must be in [0, 1]"

    def test_latency_within_nfr(self):
        """Single-SKU call must complete within 2 seconds (SC NFR)."""
        client = LiveInventoryClient()
        start = time.monotonic()
        client.get_inventory([_KNOWN_SKU])
        elapsed = time.monotonic() - start
        assert elapsed < _MAX_LATENCY_S, (
            f"API call took {elapsed:.2f}s — exceeds {_MAX_LATENCY_S}s NFR"
        )

    def test_pricing_ceiling_end_to_end(self):
        """End-to-end: pricing ceiling must hold on live data for Deal 83737219."""
        from pathlib import Path
        from tests.pipeline.fixtures import build_deal_83737219
        import tempfile
        from src.pipeline import eligibility_engine, file_validator, inventory_pricer, spa_parser

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            path = build_deal_83737219(tmp_path)
            file_validator.run(path)
            lines = spa_parser.run(path)
            lines = eligibility_engine.run(lines)
            client = LiveInventoryClient()
            lines = inventory_pricer.run(lines, client)
            for ln in lines:
                if ln.pricing_status == "OK" and ln.spare_net_price is not None:
                    assert ln.spare_net_price <= (ln.unit_net_price or float("inf")), (
                        f"SC-03 VIOLATION on live data: line {ln.line_number} "
                        f"spare_net_price {ln.spare_net_price} > ceiling {ln.unit_net_price}"
                    )
