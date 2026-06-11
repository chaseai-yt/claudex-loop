"""Deterministic CTO-to-stock-fulfillment translation engine.

Rule set validated against Steff's answer-key tables in
'Translation Example 1.xls' (deals 83737219, 84709746, 84251013):

  R1. Parent hardware line (x.0) -> KEEP, SKU unchanged.
  R2. Service/term lines (CON-*) -> KEEP unchanged when their bundle is kept.
      Non-CON-* zero-dollar descendants -> UNKNOWN-DESCENDANT flag for human review.
  R3. A child bundle (x.N + descendants x.N.*) with no value anywhere
      (list, extlist, net, extnet all <= 0) -> DROP (ships inside the spare box).
  R4. A kept bundle's head line swaps to its SPARE EQUIVALENT SKU
      ('=' SKU, col 32) when one exists; descendants keep their SKUs.
  R5. list price, net price, extended net, and quantity are preserved on every
      kept line. (extlist, dur, included, sparelist, sparedisc not in CSV export.)

The engine never invents a SKU. Anything outside the rules raises a flag
for human review instead of guessing.

Blocking conditions (export refused until resolved):
  - Any sheet with a read/schema error (including >2 data blocks).
  - Any sheet with parse errors (numeric line ID, unparseable pricing).
  - Any line with action="flag": UNKNOWN-DESCENDANT, NEGATIVE-PRICE,
    MISSING-HEAD-DESCENDANT, DUPLICATE-HEAD, DUPLICATE-LINE-ID,
    INVALID-LINE-ID, MISSING-SPARE.
  Line IDs must be x.N or x.N.M... format (bare integers are rejected).
  The sheet-level gate field is has_blocking_flags (true if ANY decision
  has action="flag").
"""
import csv
import os
import re
import tempfile
import xlrd

COL = dict(line=5, sku=7, included=8, qty=9, dur=10, list=14, extlist=15,
           net=16, extnet=17, spare=32, sparelist=33, sparedisc=34)

# Expected header substrings at key columns (case-insensitive).
# Must be confirmed against real Cisco exports before widening.
# Current two are the minimum sanity check; all others are validated as
# non-numeric (any numeric value in row 0 at a COL offset is a hard error).
_EXPECTED_HEADERS = {COL["line"]: "line", COL["sku"]: "sku"}

OUT_FIELDS = ["line", "action", "sku", "original_sku", "qty", "list", "net",
              "extnet", "reason"]

_MIN_NCOLS = max(COL.values()) + 1
_SERVICE_PREFIX = "CON-"
# Require at least x.N — bare integers have no bundle structure
_LINE_ID_RE = re.compile(r'^\d+\.\d+(\.\d+)*$')
_FORMULA_CHARS = frozenset(('=', '+', '-', '@', '\t', '\r'))
_WHITESPACE = frozenset((' ', '\t', '\r', '\n', '\x0b', '\x0c'))
_PRICE_COLS = ('list', 'extlist', 'net', 'extnet', 'sparelist', 'sparedisc', 'qty', 'dur')


def _num(v):
    if v is None or v == '':
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _strip(v):
    if v is None:
        return ''
    return str(v).strip()


def _sanitize_csv(v):
    s = str(v) if v is not None else ''
    # Strip leading whitespace/control chars before checking for formula prefix
    stripped = s.lstrip(''.join(_WHITESPACE))
    if stripped and stripped[0] in _FORMULA_CHARS:
        return "'" + s
    return s


def _safe_sort_key(s):
    parts = []
    for x in s.split("."):
        try:
            parts.append(float(x))
        except ValueError:
            parts.append(0.0)
    return parts


def _check_headers(sheet):
    """Verify header row sanity at all COL offsets in row 0.

    Two checks:
    1. Columns with known expected substrings must contain them (case-insensitive).
    2. ALL COL columns must have text/empty headers — any numeric cell in row 0
       at a COL position indicates a shifted workbook.

    Raises ValueError on any mismatch so the caller fails closed.
    """
    if sheet.nrows < 1:
        raise ValueError(f"Sheet '{sheet.name}': no rows at all — cannot validate headers.")
    import xlrd as _xlrd
    for col_idx, expected in _EXPECTED_HEADERS.items():
        cell = sheet.cell(0, col_idx)
        header = _strip(cell.value).lower()
        if expected not in header:
            raise ValueError(
                f"Sheet '{sheet.name}': expected header containing '{expected}' "
                f"at column {col_idx}, got '{_strip(cell.value)!r}'. "
                "Workbook columns may be shifted — re-export and verify layout."
            )
    # All referenced columns must have non-numeric headers
    for name, col_idx in COL.items():
        cell = sheet.cell(0, col_idx)
        if cell.ctype == _xlrd.XL_CELL_NUMBER:
            raise ValueError(
                f"Sheet '{sheet.name}': column '{name}' at offset {col_idx} "
                f"has a numeric header value {cell.value!r} — workbook appears shifted."
            )


def read_tables(sheet):
    """Split a sheet into (original, answer_key) line lists.

    Raises ValueError on schema violations or empty sheets.
    Blocking parse errors (numeric line IDs, unparseable pricing) produce
    sentinel rows with '_skip': True so translate() can skip them while
    still propagating the error flags.
    """
    if sheet.ncols < _MIN_NCOLS:
        raise ValueError(
            f"Sheet '{sheet.name}': expected >= {_MIN_NCOLS} columns, "
            f"got {sheet.ncols}. Workbook may use a different column layout."
        )
    _check_headers(sheet)

    blocks, cur, empties = [], [], 0

    for r in range(1, sheet.nrows):
        vals = [sheet.cell_value(r, c) for c in range(sheet.ncols)]
        if all(v in ("", None) for v in vals):
            empties += 1
            continue
        if vals[0] == "AUTHORIZATION NUMBER" or empties >= 2:
            if cur:
                blocks.append(cur)
                cur = []
        empties = 0
        if vals[0] == "AUTHORIZATION NUMBER":
            continue

        row = {}
        row_flags = []
        skip_row = False

        for k, c in COL.items():
            cell = sheet.cell(r, c)
            if k == 'line':
                if cell.ctype == xlrd.XL_CELL_NUMBER:
                    msg = (
                        f"row {r}: line ID is numeric in Excel "
                        f"(value={cell.value!r}) — original value unrecoverable; "
                        "re-export with line column as text; row skipped"
                    )
                    row_flags.append(msg)
                    skip_row = True
                    break
                row[k] = _strip(cell.value)
            elif k in _PRICE_COLS:
                raw = cell.value
                if raw in (None, ''):
                    row[k] = 0.0
                else:
                    try:
                        row[k] = float(raw)
                    except (TypeError, ValueError):
                        msg = (
                            f"row {r}, col '{k}': unparseable value {raw!r} — "
                            "cannot safely determine value; row skipped"
                        )
                        row_flags.append(msg)
                        skip_row = True
                        break
            else:
                row[k] = _strip(cell.value)

        if skip_row:
            cur.append({'_flags': row_flags, 'line': '', '_skip': True})
            continue

        row['_flags'] = row_flags
        cur.append(row)

    if cur:
        blocks.append(cur)

    if not blocks:
        raise ValueError(f"Sheet '{sheet.name}': no quote table found.")

    def _is_data_block(block):
        return any(
            not row.get('_skip') and _LINE_ID_RE.match(row.get("line", ""))
            for row in block
        )

    # More than 2 DATA blocks means the sheet structure is ambiguous: the
    # original quote may have been split by stray blank rows (silent data
    # loss if we discard the extras). Fail closed rather than guess.
    # Non-data trailing blocks (footers, notes) are safe to discard.
    data_blocks = [b for b in blocks if _is_data_block(b)]
    if len(data_blocks) > 2:
        raise ValueError(
            f"Sheet '{sheet.name}': found {len(data_blocks)} blocks containing "
            "quote-line data — expected at most 2 (quote + answer key). The "
            "quote may contain stray blank rows splitting it. Fix the sheet "
            "layout before processing."
        )

    # Validate that a second block looks like an answer key (has valid line IDs)
    # before using it. Footer text / notes after two blank rows would otherwise
    # be silently promoted to answer-key status and corrupt validation results.
    answer_key = []
    if len(blocks) > 1:
        candidate = blocks[1]
        valid_rows = [
            row for row in candidate
            if not row.get('_skip') and _LINE_ID_RE.match(row.get("line", ""))
        ]
        if valid_rows:
            answer_key = candidate
    return blocks[0], answer_key


def translate(lines, keep_zero_dollar_lines=False):
    """Apply rules R1-R5. Returns (decisions, flags).

    Lines with blocking conditions (negative pricing, missing/duplicate heads,
    UNKNOWN-DESCENDANTs) receive action="flag" and block CSV export.
    """
    decisions, flags = [], []

    for ln in lines:
        flags.extend(ln.get('_flags', []))

    valid_lines = [ln for ln in lines if not ln.get('_skip')]

    # Detect duplicate line IDs in the original quote — flag all occurrences
    seen_ids: dict = {}
    for ln in valid_lines:
        seen_ids.setdefault(ln["line"], []).append(ln)
    duplicate_ids = {lid for lid, lns in seen_ids.items() if len(lns) > 1}
    for lid in duplicate_ids:
        flags.append(
            f"line ID '{lid}' appears {len(seen_ids[lid])} times in quote — "
            "all occurrences flagged; deduplicate before export"
        )

    bundles = {}
    for ln in valid_lines:
        line_id = ln["line"]
        parts = line_id.split(".")
        bundles.setdefault(".".join(parts[:2]), []).append(ln)

    for head, bl in sorted(bundles.items(), key=lambda item: _safe_sort_key(item[0])):
        is_parent = head.endswith(".0")
        head_lines = [ln for ln in bl if ln["line"] == head]

        missing_head = not is_parent and not head_lines
        # Duplicate head check applies to both parent and non-parent bundles
        duplicate_head = len(head_lines) > 1

        if missing_head:
            flags.append(
                f"bundle '{head}': no head line found — "
                "all descendants flagged; resolve before export"
            )
        if duplicate_head:
            flags.append(
                f"bundle '{head}': {len(head_lines)} duplicate head lines — "
                "expected exactly one; all flagged; resolve before export"
            )

        has_value = any(
            _num(l.get("list")) > 0
            or _num(l.get("extlist")) > 0
            or _num(l.get("net")) > 0
            or _num(l.get("extnet")) > 0
            for l in bl
        )

        for ln in bl:
            line_id = ln["line"]

            # Duplicate line ID: flag this occurrence
            if line_id in duplicate_ids:
                d_dup = dict(
                    line=line_id, original_sku=ln.get("sku", ""),
                    sku=ln.get("sku", ""), qty=0, list=0, net=0, extnet=0,
                    action="flag",
                    reason=f"DUPLICATE-LINE-ID: '{line_id}' — deduplicate before export",
                )
                decisions.append(d_dup)
                continue

            if not _LINE_ID_RE.match(line_id):
                msg = (
                    f"line ID '{line_id}' is not a valid dotted-integer (x.N format required) — "
                    "flagged; resolve before export"
                )
                flags.append(msg)
                decisions.append(dict(
                    line=line_id, original_sku=ln.get("sku", ""),
                    sku=ln.get("sku", ""), qty=0, list=0, net=0, extnet=0,
                    action="flag",
                    reason=f"INVALID-LINE-ID: {msg}",
                ))
                continue

            spare = _strip(ln.get("spare", ""))

            d = dict(
                line=line_id,
                original_sku=ln["sku"],
                sku=ln["sku"],
                qty=_num(ln.get("qty")),
                list=_num(ln.get("list")),
                net=_num(ln.get("net")),
                extnet=_num(ln.get("extnet")),
            )

            # Negative pricing: flag for human review — blocks export
            has_negative = any(
                _num(ln.get(f)) < 0
                for f in ('list', 'extlist', 'net', 'extnet')
            )
            if has_negative:
                d.update(
                    action="flag",
                    reason=(
                        "NEGATIVE-PRICE: negative pricing detected — "
                        "may be credit or adjustment; classify before export"
                    ),
                )
                flags.append(
                    f"line {line_id} ({ln['sku']}): NEGATIVE-PRICE — "
                    "human classification required before export"
                )
                decisions.append(d)
                continue

            # Missing or duplicate head: flag all lines in bundle
            if missing_head or duplicate_head:
                reason_tag = "MISSING-HEAD-DESCENDANT" if missing_head else "DUPLICATE-HEAD"
                d.update(
                    action="flag",
                    reason=(
                        f"{reason_tag}: bundle structure error — "
                        "resolve head line before export"
                    ),
                )
                flags.append(
                    f"line {line_id} ({ln['sku']}): {reason_tag} — "
                    "human classification required before export"
                )
                decisions.append(d)
                continue

            if is_parent or has_value:
                if line_id == head and not is_parent and spare:
                    d.update(action="swap", sku=spare,
                             reason="paid line -> spare '=' SKU (R4)")
                elif line_id == head and not is_parent:
                    d.update(
                        action="flag",
                        reason=(
                            "MISSING-SPARE: value-bearing child bundle head has no spare "
                            "equivalent SKU — confirm orderable from stock before export"
                        ),
                    )
                    flags.append(
                        f"line {line_id} ({ln['sku']}): MISSING-SPARE — "
                        "no spare equivalent; human approval required before export"
                    )
                elif line_id == head and is_parent:
                    d.update(action="keep", reason="parent hardware (R1)")
                else:
                    sku = ln["sku"]
                    is_service = sku.upper().startswith(_SERVICE_PREFIX)
                    line_has_value = (
                        _num(ln.get("list")) > 0
                        or _num(ln.get("extlist")) > 0
                        or _num(ln.get("net")) > 0
                        or _num(ln.get("extnet")) > 0
                    )
                    if is_service:
                        d.update(action="keep", reason="service/term in kept bundle (R2)")
                    elif line_has_value:
                        d.update(action="keep",
                                 reason="value-bearing descendant in kept bundle")
                    else:
                        d.update(
                            action="flag",
                            reason=(
                                "UNKNOWN-DESCENDANT: non-CON-* zero-dollar child "
                                "in kept bundle — classify as keep or drop before export"
                            ),
                        )
                        flags.append(
                            f"line {line_id} ({sku}): UNKNOWN-DESCENDANT — "
                            "human classification required before export"
                        )
            else:
                d.update(
                    action="keep" if keep_zero_dollar_lines else "drop",
                    reason="zero-dollar bundle, ships in the box (R3)",
                )
            decisions.append(d)

    return decisions, flags


def translate_workbook(path, keep_zero_dollar_lines=False):
    """Translate every sheet. Returns a structured report dict."""
    try:
        wb = xlrd.open_workbook(path)
    except Exception as e:
        return {
            "file": path,
            "sheets": [{
                "deal": "(workbook)", "error": f"cannot open workbook: {e}",
                "input_lines": 0, "output_lines": 0,
                "decisions": [], "flags": [str(e)], "validation": None,
                "has_blocking_flags": False, "has_parse_errors": False,
            }],
        }

    report = {"file": path, "sheets": []}
    for name in wb.sheet_names():
        sheet = wb.sheet_by_name(name)
        try:
            orig, answer_key = read_tables(sheet)
        except ValueError as e:
            report["sheets"].append({
                "deal": name, "error": str(e),
                "input_lines": 0, "output_lines": 0,
                "decisions": [], "flags": [str(e)], "validation": None,
                "has_blocking_flags": False, "has_parse_errors": False,
            })
            continue

        has_parse_errors = any(ln.get('_skip') for ln in orig)
        decisions, flags = translate(orig, keep_zero_dollar_lines)
        has_blocking = any(d["action"] == "flag" for d in decisions)
        kept = [d for d in decisions if d["action"] not in ("drop", "flag")]

        sheet_result = {
            "deal": name,
            "input_lines": len([ln for ln in orig if not ln.get('_skip')]),
            "output_lines": len(kept),
            "decisions": decisions,
            "flags": flags,
            "validation": None,
            "has_blocking_flags": has_blocking,
            "has_parse_errors": has_parse_errors,
        }
        if answer_key:
            sheet_result["validation"] = _validate(kept, answer_key)
        report["sheets"].append(sheet_result)
    return report


def _validate(kept, answer_key):
    """Diff kept lines against answer key. Returns tri-state: pass/partial/fail."""
    exp_groups = {str(e["line"]).split(".")[0] for e in answer_key}
    all_kept_groups = {d["line"].split(".")[0] for d in kept}
    extra_groups = all_kept_groups - exp_groups

    exp_by_line: dict = {}
    ak_parse_errors = []
    for e in answer_key:
        k = str(e["line"])
        if not _LINE_ID_RE.match(k):
            ak_parse_errors.append(f"answer-key line ID '{k}' is not valid dotted-integer")
            continue
        exp_by_line.setdefault(k, []).append(e)

    my_by_line: dict = {}
    for d in kept:
        if d["line"].split(".")[0] in exp_groups:
            my_by_line.setdefault(d["line"], []).append(d)

    issues, warnings = list(ak_parse_errors), []

    all_keys = sorted(
        set(exp_by_line) | set(my_by_line),
        key=_safe_sort_key,
    )
    for k in all_keys:
        exp_entries = exp_by_line.get(k, [])
        my_entries = my_by_line.get(k, [])
        for i, (e, m) in enumerate(zip(exp_entries, my_entries)):
            if e["sku"] != m["sku"]:
                issues.append(f"{k}[{i}]: expected {e['sku']}, got {m['sku']}")
        for e in exp_entries[len(my_entries):]:
            if (_num(e.get("list")) > 0 or _num(e.get("extlist")) > 0
                    or _num(e.get("net")) > 0 or _num(e.get("extnet")) > 0):
                issues.append(f"{k}: value line {e['sku']} missing from output")
        for m in my_entries[len(exp_entries):]:
            issues.append(f"{k}: extra line {m['sku']} not in answer key")

    for g in sorted(extra_groups):
        warnings.append(
            f"group {g}: kept in output but not in answer key — validation partial"
        )

    result = "fail" if issues else ("partial" if warnings else "pass")
    return {"result": result, "issues": issues, "warnings": warnings}


def export_csv(report, out_path):
    """Write orderable lines to CSV.

    Raises ValueError if ANY sheet has a read/schema error, parse errors,
    or unresolved blocking flags (UNKNOWN-DESCENDANT, NEGATIVE-PRICE,
    MISSING-HEAD-DESCENDANT, DUPLICATE-HEAD).
    Uses atomic write (temp file + os.replace) to prevent partial output.
    All cells sanitized against Excel formula injection.
    """
    error_sheets = [s["deal"] for s in report["sheets"] if "error" in s]
    parse_error_sheets = [s["deal"] for s in report["sheets"] if s.get("has_parse_errors")]
    blocking_sheets = [s["deal"] for s in report["sheets"] if s.get("has_blocking_flags")]
    failed_validation_sheets = [
        s["deal"] for s in report["sheets"]
        if (s.get("validation") or {}).get("result") == "fail"
    ]

    problems = []
    if error_sheets:
        problems.append(f"sheets with read/schema errors: {error_sheets}")
    if parse_error_sheets:
        problems.append(f"sheets with parse errors: {parse_error_sheets}")
    if blocking_sheets:
        problems.append(f"sheets with unresolved blocking flags: {blocking_sheets}")
    if failed_validation_sheets:
        problems.append(
            f"sheets with FAILED answer-key validation: {failed_validation_sheets}"
        )
    if problems:
        raise ValueError(
            "Export blocked — " + "; ".join(problems) + ". "
            "Resolve all issues before exporting."
        )

    out_dir = os.path.dirname(os.path.abspath(out_path))
    fd, tmp_path = tempfile.mkstemp(dir=out_dir, suffix=".csv.tmp")
    try:
        with os.fdopen(fd, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["DEAL"] + [x.upper() for x in OUT_FIELDS])
            for sheet in report["sheets"]:
                for d in sheet["decisions"]:
                    if d["action"] not in ("drop", "flag"):
                        w.writerow(
                            [_sanitize_csv(sheet["deal"])]
                            + [_sanitize_csv(d.get(k, "")) for k in OUT_FIELDS]
                        )
        os.replace(tmp_path, out_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return out_path
