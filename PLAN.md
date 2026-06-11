# Plan: Easy Button — CTO Quote → Stock-Fulfillment Translation
_Round 10 — local adversarial review round 1 applied_

## Goal
The Easy Button deterministically converts Cisco CTO quote workbooks (.xls) to
orderable stock-fulfillment line sets using a 5-rule engine (R1–R5), validated
against 3 answer-key deals. The core invariant: the engine never invents a SKU —
anything outside the rules becomes a blocking FLAG for human review before export.
A Claude Agent SDK wrapper orchestrates the engine and writes the orderable CSV.

## Architecture (implemented)

### engine.py — deterministic R1–R5 rule engine

#### read_tables(sheet)
- Column count guard: `ncols >= max(COL.values()) + 1` raises ValueError on under-wide sheets.
- Header validation (`_check_headers`): verifies expected substrings ("line", "sku") at
  their COL offsets and asserts all COL positions have non-numeric header cells. Fails
  closed with an actionable error if any check fails.
- Line ID cell type: `cell.ctype == XL_CELL_NUMBER` → `skip_row=True`; sentinel
  `{'_skip': True}` appended; never processed by translate().
- Unparseable pricing cell → `skip_row=True`; same sentinel path. `has_parse_errors`
  set on the sheet result; export_csv blocks on it.
- Block detection: second block (after 2+ blank rows) is only used as answer key if
  it contains at least one valid dotted-integer line ID. Footers/notes are discarded.
- Controlled ValueError on no-table-found.
- More than 2 blocks containing quote-line data → ValueError (split-quote/data-loss guard);
  non-data trailing blocks (footers/notes) safely discarded.

#### translate(lines)
- `keep_zero_dollar_lines` removed from CLI and agent API. Engine default (drop) is the
  canonical behavior pending Steff's confirmation (Gap R5-3). Flag remains in engine
  signature for test use only.
- Sentinel rows (`_skip`) filtered before bundle grouping.
- Duplicate line IDs detected globally before bundle processing → `DUPLICATE-LINE-ID`
  `action="flag"` on all occurrences; blocks export.
- Invalid line IDs (not matching `^\d+\.\d+(\.\d+)*$`, i.e. bare integers or alpha) →
  `INVALID-LINE-ID` `action="flag"` decision; blocks export.
- `has_value` and `line_has_value` include `net`/`extnet` — fully-discounted lines survive R3.
- Negative pricing → `NEGATIVE-PRICE` `action="flag"`; blocks export.
- Missing bundle head (non-parent) → all descendants `MISSING-HEAD-DESCENDANT` `action="flag"`.
- Duplicate bundle heads (any level, caught by duplicate_ids) → `DUPLICATE-LINE-ID` blocks export.
- Non-CON-* zero-dollar descendant in paid bundle → `UNKNOWN-DESCENDANT` `action="flag"`.
- Missing spare on value-bearing child head → `MISSING-SPARE` `action="flag"`; blocks export.
- All string fields stripped before use; `spare` field stripped before R4 check.
- `has_blocking_flags` covers ALL `action="flag"` lines (all blocking conditions).

#### _validate(kept, answer_key)
- Tri-state result: `"pass"` / `"partial"` / `"fail"`.
- Malformed answer-key line IDs → `"fail"` (not silent skip).
- Sort key uses `_safe_sort_key` with per-part try/except; no crash path.
- Missing value-line check uses `list/extlist/net/extnet > 0` consistent with R3.
- Duplicate line occurrences handled via ordered lists per line ID.

#### export_csv(report, out_path)
- Raises ValueError if ANY sheet has: `"error"` key, `has_parse_errors`, or
  `has_blocking_flags` (all blocking flag types).
- Atomic write: `tempfile.mkstemp` + `os.replace`; temp cleaned on failure.
- `_sanitize_csv`: strips leading whitespace before formula-char check (`" =EVIL()"` caught).

### agent.py — Claude Agent SDK wrapper
- `_APPROVED_PATH` captures startup resolved path; both tools call `_require_approved_path`
  and reject any other path — model cannot invoke tools on arbitrary files.
- `_CACHED_REPORT` caches translate output; `export_quote` exports cached report only —
  translate and export cannot diverge.
- `export_quote` output path derived from approved input path; model cannot override.
- Symlink rejection via `Path(out).is_symlink()` (lstat-based, no-follow).
- `keep_zero_dollar_lines` removed from CLI and tool API.
- `disallowed_tools` blocks Bash, Write, Edit, WebSearch, WebFetch.
- `max_turns=6` caps runaway loops.
- xlrd/IO exceptions caught in `translate_workbook`; returned as controlled error report.
- Prompt uses `json.dumps({...})` with structured path argument.

### translate.py
- Thin wrapper delegating entirely to engine.py. No logic. No `__main__`.

### test_engine.py
- 31 unit tests covering: sanitization (including space-before-= bypass), sort key,
  all translate rule paths, NEGATIVE-PRICE, MISSING-SPARE, MISSING-HEAD-DESCENDANT,
  DUPLICATE-LINE-ID, INVALID-LINE-ID, net>0 R3 survival, sentinel filtering,
  _validate pass/partial/fail/malformed-AK, all export blocking conditions.

### requirements.txt
- All runtime dependencies pinned to known-good version ranges.
- `xlrd>=1.2,<2.0`, `claude-agent-sdk>=0.1.0` present.

## Key decisions & tradeoffs

1. **Blocking-flag pattern**: all conditions that could produce wrong orders are
   `action="flag"` decisions. `export_csv` checks `has_blocking_flags` (which
   covers all flag actions) and refuses to write until every flag is resolved. This
   is conservative — false positives force human review; false negatives could ship
   wrong SKUs.

2. **Header validation**: validates "line"/"sku" substrings and non-numeric type at
   all COL positions. Full canonical Cisco header strings are not yet confirmed; this
   is the strongest check implementable without ground-truth data.

3. **CON-* classifier**: kept as primary; any non-CON-* zero-dollar descendant becomes
   UNKNOWN-DESCENDANT. Requires human classification before export. A confirmed allowlist
   of non-CON-* service prefixes would reduce false positives.

4. **keep_zero_dollar_lines**: drop is the canonical default. Flag removed from CLI/agent
   pending Steff's confirmation. Engine API retains the parameter for test use.

5. **UNKNOWN-DESCENDANT resolution**: export is blocked; no in-tool classification UI
   exists yet. Reps must resolve flags manually (e.g. re-export with corrected data or
   contact Steff for allowlist additions). This is safe-by-default.

6. **Validation tri-state**: `"partial"` when extra groups appear beyond answer-key scope.
   Callers must not treat partial as a full pass.

7. **translate/export coherence**: `_CACHED_REPORT` ensures the exported CSV is exactly
   what was translated — no re-computation that could diverge.

## Risks / open questions

- **Cisco canonical column headers**: currently validating non-numeric at all COL offsets
  and expected substrings at line/sku. If Cisco uses different header text, the substring
  check will false-positive. Must be confirmed against real exports.
- **CON-* completeness**: if Cisco uses non-CON-* prefixes for legitimate service/software
  lines, those lines generate UNKNOWN-DESCENDANT flags on every quote. Requires a sample
  of real Cisco XLS exports to calibrate.
- **keep_zero_dollar_lines canonical decision**: must get Steff's answer to harden behavior.
- **UNKNOWN-DESCENDANT classification workflow**: no in-tool resolution path yet. A
  classification tool or allowlist is the next engineering milestone.
- **Agent path in prompt**: JSON-escaped and tool enforcement prevents file-write abuse;
  a hostile filename could affect model explanation text but not output data. Acceptable
  risk given model is locked to two deterministic tools.

## Out of scope
- UI / web quote surface
- CIS inventory API integration
- Canada / non-U.S. quote formats
- .xlsx support (xlrd ≥ 2.0 does not support .xlsx)
- Multi-user concurrency hardening
- Pricing field column-parity in `_validate` — validation is SKU-correctness only;
  pricing preservation is enforced by the keep/swap/drop path, not verified by _validate.
- Full Cisco canonical header resolution (requires confirmed header strings from Steff)
- UNKNOWN-DESCENDANT in-tool classification UI (next milestone)
