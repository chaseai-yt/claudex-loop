"""Agent 5 — recommendation_writer.

Produces a structured PipelineRecommendation per ship set.
Option C is HARD-SUPPRESSED for Phase 1 — never emitted.
Requires Nicko Roussos sign-off to unlock (code change, not config).
"""
from __future__ import annotations

import html as html_lib
import json
import os
import string
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from filelock import FileLock

from .models import (
    Confidence,
    OutcomeRecord,
    PipelineRecommendation,
    ReviewQueueItem,
    ShipSetOption,
    ShipSetRecommendation,
    SPALine,
)

# Phase 1 hard suppression — do NOT change to True without Nicko Roussos sign-off
OPTION_C_ENABLED = False

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_OUTPUTS_DIR = _PROJECT_ROOT / "OUTPUTS"
_OUTCOMES_FILE = _OUTPUTS_DIR / "outcomes.jsonl"
_OUTCOMES_LOCK = _OUTPUTS_DIR / "outcomes.jsonl.lock"


def _build_review_queue(lines: list[SPALine]) -> list[ReviewQueueItem]:
    items: list[ReviewQueueItem] = []
    for line in lines:
        ps = line.pricing_status

        if ps == "BLOCK" and line.missing_price:
            items.append(
                ReviewQueueItem(
                    line_number=line.line_number,
                    sku=line.sku,
                    spare_sku=line.spare_sku,
                    flag="STALE_OR_MISSING_PRICE",
                    reason="Spare SKU price data unavailable — Option B cannot be priced, contact pricing team",
                    spare_net_price=line.spare_net_price,
                    unit_net_price=line.unit_net_price,
                )
            )
        elif ps == "BLOCK":
            items.append(
                ReviewQueueItem(
                    line_number=line.line_number,
                    sku=line.sku,
                    spare_sku=line.spare_sku,
                    flag="BLOCK",
                    reason="Pricing ceiling exceeded — Option B suppressed for this line",
                    spare_net_price=line.spare_net_price,
                    unit_net_price=line.unit_net_price,
                )
            )
        elif ps == "WARN":
            items.append(
                ReviewQueueItem(
                    line_number=line.line_number,
                    sku=line.sku,
                    spare_sku=line.spare_sku,
                    flag="WARN",
                    reason="Near pricing ceiling — manual review required before presenting to customer",
                    spare_net_price=line.spare_net_price,
                    unit_net_price=line.unit_net_price,
                )
            )

        if line.inventory_is_stale:
            items.append(
                ReviewQueueItem(
                    line_number=line.line_number,
                    sku=line.sku,
                    spare_sku=line.spare_sku,
                    flag="STALE_DATA",
                    reason="Inventory data may not reflect current stock — verify before booking",
                    spare_net_price=line.spare_net_price,
                    unit_net_price=line.unit_net_price,
                )
            )
    return items


def _compute_confidence(
    eligible_lines: list[SPALine],
    review_queue: list[ReviewQueueItem],
) -> Confidence:
    if not eligible_lines:
        return "LOW"
    has_block = any(item.flag in ("BLOCK", "STALE_OR_MISSING_PRICE") for item in review_queue)
    if has_block:
        return "LOW"
    has_warn_or_unverifiable = any(
        item.flag in ("WARN", "STALE_DATA") for item in review_queue
    ) or any(line.gate3_unverifiable for line in eligible_lines)
    if has_warn_or_unverifiable:
        return "MEDIUM"
    return "HIGH"


def _build_ship_set(
    ship_set_id: int,
    all_lines: list[SPALine],
) -> ShipSetRecommendation:
    # Lines that passed eligibility gates (ELIGIBLE or DNA_AUTO_PASS)
    eligible = [
        ln for ln in all_lines
        if ln.eligibility_status in ("ELIGIBLE", "DNA_AUTO_PASS")
    ]

    review_queue = _build_review_queue(eligible)

    # Option B is available iff all eligible lines have:
    #   - confirmed stock (available_qty > 0 or DNA auto-pass)
    #   - pricing_status in {OK, WARN}  (WARN shows with review badge but not blocked)
    # Lines that failed eligibility gates are neutral — they don't block Option B.
    option_b_available = False
    suppression_reason: Optional[str] = None
    coverage_pct: Optional[float] = None
    option_b_price: Optional[float] = None

    # Lines that failed Gate 2/3/4 are non-ancillary lines with no warehouse path —
    # they must go factory, so Option B for the whole ship set is suppressed.
    # Gate 1 failures (INCLUDED ITEM = Yes) are ancillary components and are neutral.
    non_ancillary_blocked = [
        ln for ln in all_lines
        if ln.eligibility_status in ("INELIGIBLE_GATE2", "INELIGIBLE_GATE3", "INELIGIBLE_GATE4")
    ]

    if non_ancillary_blocked:
        suppression_reason = "Insufficient coverage — one or more lines have no warehouse fulfillment path"
    elif not eligible:
        suppression_reason = "No spare equivalent exists for any line in this ship set"
    else:
        blocked_lines = [ln for ln in eligible if (ln.pricing_status or "") == "BLOCK"]
        no_stock_lines = [
            ln for ln in eligible
            if not ln.dna_auto_pass and (ln.available_qty or 0) == 0
        ]
        if blocked_lines:
            suppression_reason = "Pricing ceiling exceeded — contact your manager"
        elif no_stock_lines:
            suppression_reason = "Insufficient warehouse stock for one or more lines"
        else:
            option_b_available = True
            total_needed = sum(ln.quantity for ln in eligible)
            total_available = sum(
                min(ln.available_qty or 0, ln.quantity) for ln in eligible
            )
            coverage_pct = total_available / total_needed if total_needed > 0 else 0.0
            # Net price = sum of spare_net_price × quantity for priced lines
            option_b_price = sum(
                (ln.spare_net_price or 0.0) * ln.quantity
                for ln in eligible
            )

    option_a = ShipSetOption(
        available=True,
        lead_time="Ships from Cisco in approximately 4 weeks",
    )
    option_b = ShipSetOption(
        available=option_b_available,
        reason=suppression_reason,
        lead_time="Ships from TDS warehouse in 1–3 business days" if option_b_available else "",
        net_price=round(option_b_price, 2) if option_b_price is not None else None,
        warehouse_coverage_pct=round(coverage_pct, 4) if coverage_pct is not None else None,
    )

    confidence = _compute_confidence(eligible, review_queue)

    return ShipSetRecommendation(
        ship_set_id=ship_set_id,
        option_a=option_a,
        option_b=option_b,
        option_c_suppressed=True,
        review_queue=review_queue,
        confidence=confidence,
        eligible_line_count=len(eligible),
        total_line_count=len(all_lines),
    )


def _append_outcome(deal_id: str, rec: PipelineRecommendation) -> None:
    _OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    any_option_b = any(ss.option_b.available for ss in rec.ship_sets)
    record = OutcomeRecord(
        deal_id=deal_id,
        timestamp=rec.generated_at,
        lines_eligible=rec.total_eligible,
        option_b_available=any_option_b,
        option_selected=None,
        rep_id=None,
    )
    line = json.dumps(record.model_dump(mode="json"), default=str) + "\n"
    lock = FileLock(str(_OUTCOMES_LOCK))
    with lock:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=_OUTPUTS_DIR, suffix=".tmp")
        try:
            # Copy existing content
            if _OUTCOMES_FILE.exists():
                with open(tmp_fd, "w", encoding="utf-8") as tmp_fh:
                    tmp_fh.write(_OUTCOMES_FILE.read_text(encoding="utf-8"))
                    tmp_fh.write(line)
            else:
                with open(tmp_fd, "w", encoding="utf-8") as tmp_fh:
                    tmp_fh.write(line)
            os.replace(tmp_path, _OUTCOMES_FILE)
        except Exception:
            os.unlink(tmp_path)
            raise


_HTML_TEMPLATE = string.Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Deal $deal_id &#8212; Fulfillment Recommendation</title>
<style>
  body{font-family:Arial,sans-serif;max-width:920px;margin:40px auto;color:#333}
  h1{font-size:1.4em;border-bottom:2px solid #0050a0;padding-bottom:8px}
  h2{font-size:1.1em;margin-top:28px}
  .ship-set{border:1px solid #ddd;border-radius:6px;padding:16px;margin:16px 0}
  .opt{display:inline-block;border:1px solid #bbb;border-radius:4px;padding:10px 16px;
       margin:8px 8px 8px 0;min-width:210px;vertical-align:top}
  .opt-a{background:#f0f7ff}
  .opt-b-y{background:#f0fff4;border-color:#2e7d32}
  .opt-b-n{background:#fff8f0;border-color:#e65100}
  .badge{display:inline-block;font-size:.75em;padding:2px 7px;border-radius:3px;margin-left:6px}
  .HIGH{background:#c8e6c9;color:#1b5e20}
  .MEDIUM{background:#fff9c4;color:#f57f17}
  .LOW{background:#ffcdd2;color:#b71c1c}
  table{width:100%;border-collapse:collapse;font-size:.9em;margin-top:8px}
  th{background:#f5f5f5;text-align:left;padding:6px;border-bottom:2px solid #ddd}
  td{padding:5px 6px;border-bottom:1px solid #eee}
  .BLOCK,.STALE_OR_MISSING_PRICE{color:#c62828;font-weight:bold}
  .WARN{color:#e65100}
  .STALE_DATA{color:#7b1fa2}
  .footer{margin-top:40px;font-size:.8em;color:#888}
</style>
</head>
<body>
<h1>Cisco Fulfillment Recommendation &#8212; Deal $deal_id</h1>
<p>Generated: $generated_at &nbsp;|&nbsp; Total lines: $total_lines &nbsp;|&nbsp;
Eligible: $total_eligible</p>
$ship_sets_html
<div class="footer">
<p><strong>Option A</strong> &#8212; Standard drop-ship via Cisco (approx. 4 weeks).<br>
<strong>Option B</strong> &#8212; Warehouse fulfillment from TDS stock (1&#8211;3 business days).<br>
Option C is not available in Phase 1.</p>
</div>
</body>
</html>""")


def _render_ship_set_html(ss: ShipSetRecommendation) -> str:
    conf_badge = (
        f'<span class="badge {html_lib.escape(ss.confidence)}">'
        f'{html_lib.escape(ss.confidence)}</span>'
    )
    b_cls = "opt-b-y" if ss.option_b.available else "opt-b-n"
    b_status = "Available" if ss.option_b.available else "Not available"
    b_reason = (
        f'<br><small>{html_lib.escape(ss.option_b.reason or "")}</small>'
        if ss.option_b.reason else ""
    )
    b_price = (
        f'<br>Net price: <strong>${ss.option_b.net_price:,.2f}</strong>'
        if ss.option_b.net_price is not None else ""
    )
    b_lead = html_lib.escape(ss.option_b.lead_time) if ss.option_b.lead_time else ""

    review_rows = ""
    for item in ss.review_queue:
        review_rows += (
            f'<tr><td>{html_lib.escape(item.line_number)}</td>'
            f'<td>{html_lib.escape(item.sku)}</td>'
            f'<td>{html_lib.escape(item.spare_sku or "")}</td>'
            f'<td class="{html_lib.escape(item.flag)}">{html_lib.escape(item.flag)}</td>'
            f'<td>{html_lib.escape(item.reason)}</td></tr>'
        )
    review_section = ""
    if review_rows:
        review_section = (
            "<h2>Review Queue</h2>"
            '<table><tr><th>Line</th><th>SKU</th><th>Spare SKU</th>'
            '<th>Flag</th><th>Reason</th></tr>'
            f"{review_rows}</table>"
        )

    return (
        f'<div class="ship-set">'
        f'<h2>Ship Set {html_lib.escape(str(ss.ship_set_id))} '
        f'({ss.eligible_line_count}/{ss.total_line_count} lines eligible) {conf_badge}</h2>'
        f'<div class="opt opt-a"><strong>Option A</strong><br>'
        f'{html_lib.escape(ss.option_a.lead_time)}</div>'
        f'<div class="opt {b_cls}"><strong>Option B</strong> &#8212; {b_status}'
        f'{b_reason}{b_price}<br>{b_lead}</div>'
        f'{review_section}'
        f'</div>'
    )


def generate_html(rec: PipelineRecommendation) -> str:
    """Render rec as a self-contained, CSS-inline HTML string (HTML fallback path).

    All user-data fields are passed through html.escape() to prevent malformed output.
    Mirrors FR-P01–FR-P06. Does NOT satisfy FR-P04 (rep confirmation POST) — fallback only.
    """
    ship_sets_html = "\n".join(_render_ship_set_html(ss) for ss in rec.ship_sets)
    return _HTML_TEMPLATE.substitute(
        deal_id=html_lib.escape(rec.deal_id),
        generated_at=html_lib.escape(rec.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")),
        total_lines=rec.total_lines,
        total_eligible=rec.total_eligible,
        ship_sets_html=ship_sets_html,
    )


def run(deal_id: str, lines: list[SPALine]) -> PipelineRecommendation:
    # Group all lines by ship_set_id
    groups: dict[int, list[SPALine]] = defaultdict(list)
    for line in lines:
        groups[line.ship_set_id].append(line)

    ship_sets = [
        _build_ship_set(ship_set_id, group_lines)
        for ship_set_id, group_lines in sorted(groups.items())
    ]

    total_eligible = sum(
        1 for ln in lines if ln.eligibility_status in ("ELIGIBLE", "DNA_AUTO_PASS")
    )

    rec = PipelineRecommendation(
        deal_id=deal_id,
        generated_at=datetime.now(timezone.utc),
        ship_sets=ship_sets,
        total_lines=len(lines),
        total_eligible=total_eligible,
    )

    # Write recommendation JSON
    _OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _OUTPUTS_DIR / f"recommendation_{deal_id}.json"
    out_path.write_text(
        json.dumps(rec.model_dump(mode="json"), default=str, indent=2),
        encoding="utf-8",
    )

    _append_outcome(deal_id, rec)
    return rec
