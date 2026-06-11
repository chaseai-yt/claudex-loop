from __future__ import annotations

from collections import defaultdict
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4

from ..ingestion.models import Quote
from ..inventory.client import InventoryClient
from ..mapping.table import MappingTable
from .models import FulfillmentRecommendation, LineScore, Recommendation

_DEFAULT_THRESHOLDS = {
    "stock": 1.0,   # >= 1.0 → STOCK
    "hybrid": 0.5,  # >= 0.5 → HYBRID, else DROP_SHIP
    "version": 1,
}


class RulesEngine:
    def __init__(
        self,
        thresholds: Optional[dict],
        mapping_table: MappingTable,
        inventory_client: InventoryClient,
    ) -> None:
        self._thresholds = thresholds or _DEFAULT_THRESHOLDS
        self._mapping_table = mapping_table
        self._inventory_client = inventory_client

    # ------------------------------------------------------------------

    def evaluate(self, quote: Quote) -> FulfillmentRecommendation:
        now = datetime.now(timezone.utc)

        # Collect all spare SKUs to batch-fetch inventory
        spare_skus: list[str] = []
        line_mapping: list[dict] = []  # per quote line, enrichment data

        for line in quote.lines:
            mapping_entry = self._mapping_table.lookup(line.sku)
            spare_sku = mapping_entry.spare_sku if mapping_entry else line.spare_sku
            is_strict = mapping_entry.is_strict_match if mapping_entry else False
            if spare_sku:
                spare_skus.append(spare_sku)
            line_mapping.append(
                {
                    "spare_sku": spare_sku,
                    "is_strict_match": is_strict,
                }
            )

        inv_response = self._inventory_client.get_inventory(list(set(spare_skus)))
        inventory_as_of = inv_response.queried_at

        # Group lines by build_group
        # build_group → list of (line, enrichment)
        groups: dict[str, list[tuple]] = defaultdict(list)
        for line, enrich in zip(quote.lines, line_mapping):
            groups[line.build_group].append((line, enrich))

        stock_threshold = self._thresholds.get("stock", 1.0)
        hybrid_threshold = self._thresholds.get("hybrid", 0.5)

        line_scores: list[LineScore] = []
        group_recommendations: list[Recommendation] = []

        for build_group, items in groups.items():
            group_required = 0
            group_capped = 0
            group_has_strict_zero = False

            for line, enrich in items:
                spare_sku = enrich["spare_sku"]
                is_strict = enrich["is_strict_match"]
                avail = 0
                if spare_sku and spare_sku in inv_response.records:
                    avail = inv_response.records[spare_sku].available_qty

                capped = min(avail, line.quantity)
                group_required += line.quantity
                group_capped += capped

                if is_strict and avail == 0:
                    group_has_strict_zero = True

                # Per-line recommendation (preliminary; group overrides below)
                line_scores.append(
                    LineScore(
                        sku=line.sku,
                        build_group=build_group,
                        required_qty=line.quantity,
                        available_qty=avail,
                        capped_qty=capped,
                        spare_sku=spare_sku,
                        is_strict_match=is_strict,
                        recommendation="STOCK",  # placeholder; set after group calc
                    )
                )

            # Calculate group recommendation
            if group_has_strict_zero:
                group_rec: Recommendation = "MANUAL_REVIEW"
            else:
                score = group_capped / group_required if group_required > 0 else 0.0
                if score >= stock_threshold:
                    group_rec = "STOCK"
                elif score >= hybrid_threshold:
                    group_rec = "HYBRID"
                else:
                    group_rec = "DROP_SHIP"

            group_recommendations.append(group_rec)

            # Back-fill line recommendation with group recommendation
            # (lines in this group are the last len(items) entries)
            for i in range(len(items)):
                idx = len(line_scores) - len(items) + i
                line_scores[idx] = line_scores[idx].model_copy(
                    update={"recommendation": group_rec}
                )

        # Overall = worst across groups
        _priority: dict[Recommendation, int] = {
            "STOCK": 0,
            "HYBRID": 1,
            "DROP_SHIP": 2,
            "MANUAL_REVIEW": 3,
        }
        overall: Recommendation = max(
            group_recommendations, key=lambda r: _priority[r]
        ) if group_recommendations else "MANUAL_REVIEW"

        # Global score
        total_required = sum(ls.required_qty for ls in line_scores)
        total_capped = sum(ls.capped_qty for ls in line_scores)
        score_pct = total_capped / total_required if total_required > 0 else 0.0

        return FulfillmentRecommendation(
            quote_id=quote.quote_id,
            recommendation_id=uuid4(),
            overall=overall,
            score_pct=round(score_pct, 4),
            line_scores=line_scores,
            thresholds_version=self._thresholds.get("version", 1),
            mapping_table_version=self._mapping_table.get_version(),
            generated_at=now,
            as_of_inventory=inventory_as_of,
        )
