from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from ..rules.models import FulfillmentRecommendation


def _emit(event: dict) -> None:
    """Write a structured JSON audit event to stdout."""
    print(json.dumps(event, default=str), flush=True)


class AuditLogger:
    def __init__(self, threshold_config_version: int = 1, mapping_table_version: int = 1) -> None:
        self._threshold_config_version = threshold_config_version
        self._mapping_table_version = mapping_table_version

    def log_recommendation(
        self,
        rec: FulfillmentRecommendation,
        actor_id: str,
    ) -> None:
        _emit(
            {
                "event_type": "RECOMMENDATION_GENERATED",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "actor_id": actor_id,
                "recommendation_id": str(rec.recommendation_id),
                "quote_id": rec.quote_id,
                "overall": rec.overall,
                "score_pct": rec.score_pct,
                "threshold_config_version": rec.thresholds_version,
                "mapping_table_version": rec.mapping_table_version,
                "detection_source": "rules_engine",
                "generated_at": rec.generated_at.isoformat(),
                "as_of_inventory": rec.as_of_inventory.isoformat(),
                "line_count": len(rec.line_scores),
            }
        )

    def log_override(
        self,
        rec_id: str,
        actor_id: str,
        override_reason: str,
        new_fulfillment: str,
    ) -> None:
        _emit(
            {
                "event_type": "RECOMMENDATION_OVERRIDE",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "actor_id": actor_id,
                "recommendation_id": rec_id,
                "override_reason": override_reason,
                "new_fulfillment": new_fulfillment,
                "threshold_config_version": self._threshold_config_version,
                "mapping_table_version": self._mapping_table_version,
                "detection_source": "manual_override",
            }
        )

    def log_mismatch(
        self,
        order_id: str,
        actor_id: str,
        rec_id: str,
        available_skus: list[str],
        cutoff_status: str,
        alert_outcome: str,
        inventory_snapshot: dict,
    ) -> None:
        _emit(
            {
                "event_type": "FULFILLMENT_MISMATCH",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "actor_id": actor_id,
                "recommendation_id": rec_id,
                "order_id": order_id,
                "available_skus": available_skus,
                "cutoff_status": cutoff_status,
                "alert_outcome": alert_outcome,
                "inventory_snapshot": inventory_snapshot,
                "threshold_config_version": self._threshold_config_version,
                "mapping_table_version": self._mapping_table_version,
                "detection_source": "mismatch_detector",
            }
        )
