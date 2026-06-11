from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..ingestion.models import ParseResult, Quote
from ..ingestion.parser import parse_quote_file
from ..inventory.client import MockInventoryClient
from ..mapping.table import MappingTable
from ..monitoring.audit import AuditLogger
from ..rules.engine import RulesEngine, _DEFAULT_THRESHOLDS
from ..rules.models import FulfillmentRecommendation
from ..pipeline.outcomes_api import router as outcomes_router

app = FastAPI(title="Maple AI — Cisco Inventory Fulfillment", version="0.1.0")
app.include_router(outcomes_router)

# ---------------------------------------------------------------------------
# Shared singletons
# ---------------------------------------------------------------------------

_mapping_table = MappingTable()
_inventory_client = MockInventoryClient()
_thresholds: dict = dict(_DEFAULT_THRESHOLDS)
_audit = AuditLogger(
    threshold_config_version=_thresholds.get("version", 1),
    mapping_table_version=_mapping_table.get_version(),
)

# In-memory stores (MVP)
_quotes: dict[str, Quote] = {}
_parse_results: dict[str, ParseResult] = {}
_recommendations: dict[str, FulfillmentRecommendation] = {}  # keyed by quote_id


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class ParseRequest(BaseModel):
    filename: str
    content: str


class ConfirmResponse(BaseModel):
    recommendation: FulfillmentRecommendation


class OverrideRequest(BaseModel):
    reason: str
    fulfillment: str


class OverrideResponse(BaseModel):
    status: str = "ok"


class ThresholdsResponse(BaseModel):
    thresholds: dict


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/quotes/parse", response_model=ParseResult, status_code=200)
def parse_quote(body: ParseRequest) -> ParseResult:
    """Parse a raw quote file via Claude and validate it."""
    result = parse_quote_file(body.content, body.filename)

    # Store for later confirmation
    _quotes[result.quote.quote_id] = result.quote
    _parse_results[result.quote.quote_id] = result

    # If there are hard validation errors, return 422
    from ..ingestion.validator import validate_quote
    is_valid, errors = validate_quote(result.quote)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"errors": errors, "parse_result": result.model_dump(mode="json")},
        )

    return result


@app.post("/quotes/{quote_id}/confirm", response_model=FulfillmentRecommendation)
def confirm_quote(quote_id: str) -> FulfillmentRecommendation:
    """Confirm a parsed quote and run the rules engine."""
    quote = _quotes.get(quote_id)
    if not quote:
        raise HTTPException(status_code=404, detail=f"Quote '{quote_id}' not found.")

    # Mark as confirmed
    quote = quote.model_copy(update={"confirmed": True})
    _quotes[quote_id] = quote

    engine = RulesEngine(
        thresholds=_thresholds,
        mapping_table=_mapping_table,
        inventory_client=_inventory_client,
    )
    rec = engine.evaluate(quote)
    _recommendations[quote_id] = rec

    _audit.log_recommendation(rec, actor_id="system")

    return rec


@app.post("/quotes/{quote_id}/override", response_model=OverrideResponse)
def override_quote(quote_id: str, body: OverrideRequest) -> OverrideResponse:
    """Log a manual override for a previously generated recommendation."""
    rec = _recommendations.get(quote_id)
    if not rec:
        raise HTTPException(
            status_code=404,
            detail=f"No recommendation found for quote '{quote_id}'.",
        )

    _audit.log_override(
        rec_id=str(rec.recommendation_id),
        actor_id="api-user",
        override_reason=body.reason,
        new_fulfillment=body.fulfillment,
    )

    return OverrideResponse(status="ok")


@app.get("/admin/thresholds", response_model=ThresholdsResponse)
def get_thresholds(x_admin_key: Optional[str] = Header(default=None)) -> ThresholdsResponse:
    _require_admin(x_admin_key)
    return ThresholdsResponse(thresholds=dict(_thresholds))


@app.put("/admin/thresholds", response_model=ThresholdsResponse)
def put_thresholds(
    body: dict,
    x_admin_key: Optional[str] = Header(default=None),
) -> ThresholdsResponse:
    _require_admin(x_admin_key)
    global _thresholds
    _thresholds.update(body)
    return ThresholdsResponse(thresholds=dict(_thresholds))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_admin(key: Optional[str]) -> None:
    expected = os.environ.get("ADMIN_KEY", "change-me")
    if key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Admin-Key header.",
        )
