# Plan Review Log: Easy Button — CTO Quote → Stock-Fulfillment Translation
Started 2026-06-10. MAX_ROUNDS=5.

## Round 1 — Codex (read-only)
VERDICT: REVISE

Findings:
- [engine.py:52] `read_tables` indexes hardcoded columns without checking `sheet.ncols` — shifted/thinner workbook crashes or corrupts fields. Fix: validate column count upfront or resolve by header name.
- [engine.py:55] `read_tables` returns `blocks[0]` even for empty/nonmatching sheets. Fix: raise a controlled "no quote table found" error with sheet name.
- [engine.py:67] Line numbers parsed from `str(cell_value)` — Excel numeric `1.10` becomes `1.1`, grouping/sort wrong. Fix: read formatted text or require line-number cells to be typed as text.
- [engine.py:71] `float()` sort keys crash on blank/alphabetic/nonstandard line IDs. Fix: strict regex validator, flag unsupported formats.
- [engine.py:75] Value detection ignores `net`/`extnet` — fully discounted/list-zero but net-positive lines dropped. Fix: treat value as positive if any of list/extlist/net/extnet > 0.
- [engine.py:80] Any descendant in a paid bundle kept as R2, not just service/term lines — zero-dollar hardware/info children leak into output. Fix: classify by SKU prefix, drop or flag non-service zero-dollar children.
- [engine.py:81] Missing bundle-head lines not detected — descendants kept with no R4 swap opportunity. Fix: require exactly one head line per non-parent bundle.
- [engine.py:81] `ln["spare"]` not stripped — whitespace emitted as a SKU. Fix: normalize/strip all string fields before rules run.
- [engine.py:84] Missing-spare flags only fire when head `list > 0`, missing `extlist > 0` and descendant-value cases. Fix: flag any kept non-parent value-bearing bundle head with no spare SKU.
- [engine.py:118] Validation scopes to answer-key groups only — extra generated groups outside the key pass silently. Fix (disputed — see response).
- [engine.py:119] Validation maps by line number, collapsing duplicate line IDs silently. Fix: multimap keyed by (line, occurrence).
- [engine.py:124] Validation checks SKU only despite R5 requiring pricing preservation. Fix: compare qty/list/net/extnet fields (disputed — see response).
- [engine.py:135] CSV export formula-injection prone. Fix: escape cells beginning with `=`, `+`, `-`, `@`, tab, or CR.
- [agent.py:21] `claude_agent_sdk` absent from `requirements.txt` — ImportError before runtime error handling. Fix: add/pin dependency.
- [agent.py:94] File paths interpolated into prompt — path/newline prompt injection. Fix: structured tool arguments only.
- [agent.py:55] `export_quote` accepts arbitrary `out_path` from model. Fix: derive path deterministically, reject model-supplied paths outside approved directory.
- [translate.py:66] Divergent grouping/sorting behavior, can be run directly — two sources of truth. Fix: remove or make it delegate to engine.py.

### Claude's response — what changed, what was rejected, why

**Accepted (15 of 17):**
1. **`sheet.ncols` guard** — real crash path on non-standard XLS. Added to plan.
2. **Empty sheet IndexError** — controlled error with sheet name. Added.
3. **Excel float line ID truncation** — `1.10` → `1.1` is a silent correctness bug. Added.
4. **`float()` sort crash** — regex validator + flag. Added.
5. **`net`/`extnet` in value detection** — real bug for discounted deals. Added.
6. **Non-service zero-dollar descendants** — accepted as a flagging gap. Added.
7. **Missing bundle head not detected** — real R4 miss. Added.
8. **`ln["spare"]` whitespace** — simple correctness fix. Added.
9. **Missing-spare flag gap** — broader value detection needed. Added.
10. **Duplicate line IDs in validation** — multimap fix. Added.
11. **CSV formula injection** — real Excel security issue. Added.
12. **`claude_agent_sdk` missing from requirements** — real deployment gap. Added.
13. **Prompt injection via file path** — structured args fix. Added.
14. **`export_quote` arbitrary out_path** — path traversal risk. Added.
15. **translate.py two sources of truth** — must be removed or delegated. Added.

**Rejected (2 of 17 — final):**
- **Validation scope to answer-key groups** — intentional design. Extra groups not in the answer key are by definition untested; scoping is the correct behavior for a regression harness. Logged as a known blind spot, not a bug.
- **Validation checks SKU only (not pricing)** — the answer key validates business correctness of the translation, not column parity. R5 pricing preservation is verified through the keep/swap/drop path, not the validation diff. Over-scope for this component.

## Round 2 — Codex (resumed session, read-only)
Note: Codex explicitly stated it read the stale PLAN.md ("still says Round 0"). User ran `git pull origin main` from home directory `~` rather than `~/grill-me-codex`, so the updated plan was not visible to Codex. All findings are re-raises of Round 1 issues already addressed. No new material findings. No PLAN.md changes made.

VERDICT: REVISE (stale file — findings already resolved in Round 1 revision)

## Round 3 — Codex (resumed session, read-only)
VERDICT: REVISE

Codex confirmed plan is "materially better as a gap register." New sharpened findings on the plan:
- `sheet.ncols` count alone still accepts shifted columns silently — needs header text validation at each offset.
- Requiring text line-number cells doesn't recover Excel-coerced `1.10` → `1.1` — must inspect `cell.ctype` at read time.
- `CON-*` as only service/term classifier is brittle — unknown descendants should be flagged, not silently kept/dropped.
- Extra generated groups outside answer-key scope passing silently undermines the validation claim — should WARN.
- Re-raised: pricing field comparison conflicts with R5 (3rd time, no new evidence).
- All engine.py/agent.py/translate.py code findings are re-raises (code unchanged — this is a design review, not an implementation PR).

### Claude's response — what changed, what was rejected, why

**Accepted (4 new findings):**
1. **Header text validation at each offset** — ncols count is insufficient; shifted columns still corrupt. Added requirement to validate expected header text at fixed offsets or resolve by name, fail closed on mismatch.
2. **`cell.ctype` line ID check** — Excel coercion is irreversible; must detect at read time with `cell.ctype == xlrd.XL_CELL_NUMBER`. Added.
3. **`CON-*` classifier brittleness** — unknown non-`CON-*` zero-dollar descendants now become UNKNOWN-DESCENDANT flags for human review. Added.
4. **Validation extra-group warning** — extra groups not in answer key now emit WARN (not hard fail). Added.

**Rejected (1, final hold):**
- **Pricing field comparison conflicts with R5** — Re-raised 3rd time without new evidence. R5 is tested by the keep/swap/drop path preserving the price dict. `_validate` is a SKU-correctness diff against the answer key, not a column audit. Final hold — added explicit rationale to Out of scope.

## Round 4 — Codex (resumed session, read-only)
VERDICT: REVISE

New findings (not re-raises):
- Validation WARN still returns `pass: bool` — callers cannot distinguish full pass from partial. Fix: tri-state (`pass`/`partial`/`fail`).
- "blocks filesystem tools" is misleading — `export_quote` is itself a write primitive. Fix: clarify real trust boundary.
- UNKNOWN-DESCENDANT has no resolution path — export is blocked until resolved. Fix: allowlist or human-classification gate.
- Partial accept on pricing: "R5 is validated" claim corrected — `_validate` does NOT verify pricing, this is a known gap.

All engine.py/agent.py/translate.py code findings are re-raises (code unchanged — design review only).

### Claude's response — what changed, what was rejected, why

**Accepted (4):**
1. **Validation tri-state** — WARN alone still looks like pass to callers. Added `pass`/`partial`/`fail` tri-state; block PASS reporting when any extra group exists.
2. **Trust boundary wording** — clarified: model cannot pick tool names; within `export_quote`, path must be derived from input, not model-supplied. Removed misleading "blocks filesystem tools."
3. **UNKNOWN-DESCENDANT resolution path** — added: tool blocks export until every UNKNOWN-DESCENDANT is classified as keep/drop by the rep, OR an allowlist of known non-`CON-*` service prefixes is built from real Cisco exports.
4. **R5 validated claim corrected** — updated Out of scope to state `_validate` does NOT verify pricing preservation; that is a known gap. Removed false validation claim.

**Rejected (all code re-raises):**
- All engine.py / agent.py / translate.py findings are re-raises of previously accepted gaps. Code is unchanged because this is a design review, not an implementation PR. No new findings.

## Round 5 — Codex (resumed session, read-only) — FINAL ROUND
VERDICT: REVISE

New findings:
- `keep_zero_dollar_lines` unresolved but changes actual orderable output — must decide before shipping or block export.
- R5 "enforced by copying the price dict" inaccurate — `OUT_FIELDS` omits `extlist`, `dur`, `included`, `sparelist`, `sparedisc`. R5 claim must be narrowed.
- `_num()` silently converts unparseable/currency values to 0.0 — can drop value-bearing bundles via R3.

All other findings are re-raises of previously accepted gaps (code unchanged).

### Claude's response — Round 5 final fixes and Resolution

**Accepted (3 new):**
1. **`keep_zero_dollar_lines` decision gate** — must be resolved before shipping. Added: get Steff's answer and hardcode, or block export until explicit decision is recorded.
2. **R5 OUT_FIELDS gap** — corrected the R5 claim: only list/net/extnet/qty are preserved; extlist/dur/included/sparelist/sparedisc are NOT currently exported. Added as Gap R5-1.
3. **`_num()` silent 0.0 coercion** — currency-formatted values coerce to 0.0, can trigger false R3 drops. Added as Gap R5-2.

**Rejected (all code re-raises):**
- All engine.py / agent.py / translate.py code findings are re-raises. Code unchanged — design review only.

---
## RESOLUTION — MAX_ROUNDS=5 reached

**Outcome: REVISE at round cap. Standing disagreement: Codex treats "code unchanged" as a blocker; Claude treats this as a design review, not an implementation PR.**

The plan now contains a complete, precise gap register for the Easy Button tool. All material design flaws are documented with specific fixes. The code has not been changed because the scope of this review was the design, not the implementation.

**What the argument improved (5 rounds):**
1. 15 correctness and security gaps identified and added to the plan (R1): column guards, line ID parsing, value detection, descendant classification, spare SKU normalization, flag gaps, CSV injection, agent SDK, prompt injection, path traversal, translate.py divergence.
2. 4 plan sharpening items (R3): header text validation vs count, cell_type check, CON-* brittleness, validation extra-group warning.
3. 4 structural design fixes (R4): validation tri-state, trust boundary clarification, UNKNOWN-DESCENDANT resolution path, R5 validated claim corrected.
4. 3 final gaps (R5): keep_zero_dollar_lines must be decided, OUT_FIELDS R5 gap, _num() silent coercion.

**Persistent disagreement (Claude's position):**
- Codex repeatedly flagged "code unchanged" as a blocker. This review was scoped to the design/plan, not the implementation. The gaps are all documented. Implementation is the next step.

**Ready to implement:** The gap register in PLAN.md is the implementation backlog. Recommended order: R5-2 (_num), R1-2/R1-3/R1-4 (ingestion guards), R1-5 (value detection), R1-8 (string normalization), R1-11 (CSV injection), R1-12 (requirements.txt), R1-14/R1-13 (agent path safety), R5-3 (keep_zero_dollar_lines decision), R4-1 (tri-state validation), R1-15 (translate.py removal).
