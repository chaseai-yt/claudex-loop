# Plan Review Log: Cisco Fulfillment Optimization — Phase 1 Completion
Started 2026-06-09. MAX_ROUNDS=5.

## Round 1 — Adversarial Review (Claude in read-only reviewer role)
_Note: Codex CLI 403 "Host not in allowlist" — OpenAI API unreachable from this remote execution environment. Claude performed adversarial review with identical read-only posture and VERDICT format._

Findings:

1. `LiveInventoryClient._parse_response()` referenced in plan but doesn't exist in code — inline parsing is in `get_inventory()`. Fix: corrected reference.
2. `is_stale` fallback value undefined — if API omits the field, default must be `False` not `None`. Fix: specified `default=False`.
3. Integration smoke-test skip mechanism unspecified — inconsistent skip strategies cause CI confusion. Fix: `@pytest.mark.skipif(not os.environ.get("INVENTORY_API_URL"), ...)`.
4. `POST /outcomes/{deal_id}/record` endpoint unauthenticated — any caller could corrupt Power BI baseline. Fix: `WEBQUOTE_CALLBACK_KEY` bearer token required.
5. `outcomes.jsonl` "update matching record" is a race condition — O(n) read-mutate-rewrite under concurrent writes. Fix: delta-append only; Power BI uses `LAST(option_selected)`.
6. Power BI KPI formula conflates override rate with drop-ship baseline rate. Fix: two distinct KPIs — `override_rate` and `option_b_surface_rate`.
7. `spare_list_price == 0` on non-DNA lines silently passes pricing ceiling. Fix: `STALE_OR_MISSING_PRICE` flag + BLOCK status added to `inventory_pricer`.
8. `--keep-checkpoints` production block has no enforcement. Fix: `PIPELINE_ENV=production` guard in CLI with `sys.exit()`.
9. HTML fallback leaves FR-P04 (Must have) unmet with no stakeholder acknowledgment path. Fix: added to Risks explicitly.

VERDICT: REVISE

### Claude's response — all 9 accepted
All findings are material or safety-relevant. Applied all 9 fixes.


## Round 2 — Adversarial Review (Claude in read-only reviewer role)

Findings:

1. `--html` flag on `recommendation_writer.py` (not a CLI) — should be on `pipeline.py`. Fix: moved to `pipeline.py`; calls `recommendation_writer.generate_html(rec)`.
2. `spare_sku_discount` / `spare_list_price` have no range validation — API returning `discount=1.5` or negative prices produces negative `spare_net_price`, trivially passes ceiling. Fix: Pydantic `Field(ge=0.0)` / `Field(ge=0.0, le=1.0)` constraints added to plan.
3. SC-05 population mismatch unacknowledged — 49.4% baseline covers all Cisco orders; `override_rate` only covers pipeline-processed orders. Fix: added to Risks.
4. Parquet export mechanism unspecified — "nightly parquet export" with no owner or trigger. Fix: `scripts/export_outcomes_parquet.py`, Harsh Dhabalia owner, cron/Azure Function.
5. Step 12 out of sequence — appeared after step 10, before step 11. Fix: renumbered and folded into Path A as steps 5–6.

VERDICT: REVISE

### Claude's response — all 5 accepted


## Round 3 — Adversarial Review (Claude in read-only reviewer role)

Findings:

1. Duplicate step numbers — Path A ends at 6, Path B restarts at 5, Path C collides at 8/9. Fix: renumbered sequentially 1–13.
2. `filelock` contention between pipeline and new API endpoint — both write to same `outcomes.jsonl`; plan didn't specify shared lock path. Fix: API endpoint imports `_OUTCOMES_FILE` + `_OUTCOMES_LOCK` from `recommendation_writer`, uses same `FileLock`.
3. `generate_html()` implicit but not specified — Fix: explicitly add method signature + `html.escape()` requirement.
4. `data_as_of: datetime` should be `Optional[datetime] = None` — real-time APIs don't return a batch timestamp. Fix: made Optional.

VERDICT: REVISE

### Claude's response — all 4 accepted

## Round 4 — Adversarial Review (Claude in read-only reviewer role)

Findings:

1. Duplicate sentence in step 3 — "inventory_pricer propagates the staleness flag to each SPALine only when is_stale=True" appeared twice. Fix: removed duplicate sentence from PLAN.md.

VERDICT: APPROVED

