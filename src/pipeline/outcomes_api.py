"""FastAPI router for the outcomes recording endpoint (Path C).

Mounted into src/api/main.py as an included router.
Kept separate so tests can import this router without pulling in the
full Maple AI app (which has an anthropic dependency).

Auth: Bearer token matched against WEBQUOTE_CALLBACK_KEY env var.
Uses the same FileLock as recommendation_writer to prevent cross-process
contention when pipeline runs and WebQuote callbacks write concurrently.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from filelock import FileLock
from pydantic import BaseModel

from .recommendation_writer import _OUTCOMES_FILE, _OUTCOMES_LOCK

router = APIRouter()
_bearer = HTTPBearer(auto_error=False)


class OutcomeRecordRequest(BaseModel):
    option_selected: str  # "A", "B", or "DEFERRED"
    rep_id: Optional[str] = None


def _require_webquote_auth(credentials: Optional[HTTPAuthorizationCredentials]) -> None:
    expected = os.environ.get("WEBQUOTE_CALLBACK_KEY")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WEBQUOTE_CALLBACK_KEY not configured on this server",
        )
    token = credentials.credentials if credentials else None
    if token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token",
        )


@router.post("/outcomes/{deal_id}/record", status_code=204)
def record_outcome(
    deal_id: str,
    body: OutcomeRecordRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer),
) -> None:
    """Record the rep's option selection for a deal (WebQuote callback)."""
    _require_webquote_auth(credentials)

    if body.option_selected not in ("A", "B", "DEFERRED"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="option_selected must be 'A', 'B', or 'DEFERRED'",
        )

    record = {
        "deal_id": deal_id,
        "option_selected": body.option_selected,
        "rep_id": body.rep_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    line = json.dumps(record) + "\n"

    _OUTCOMES_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(_OUTCOMES_LOCK))
    with lock:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(_OUTCOMES_FILE.parent), suffix=".tmp")
        try:
            if _OUTCOMES_FILE.exists():
                with open(tmp_fd, "w", encoding="utf-8") as fh:
                    fh.write(_OUTCOMES_FILE.read_text(encoding="utf-8"))
                    fh.write(line)
            else:
                with open(tmp_fd, "w", encoding="utf-8") as fh:
                    fh.write(line)
            os.replace(tmp_path, _OUTCOMES_FILE)
        except Exception:
            os.unlink(tmp_path)
            raise
