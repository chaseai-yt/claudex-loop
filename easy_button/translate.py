"""Thin wrapper — delegates entirely to engine.py.

The original standalone implementation has been superseded by engine.py.
This file is kept only to avoid breaking any existing references;
it re-exports the engine's public API and does not contain translation logic.
"""
from .engine import translate, translate_workbook, read_tables, export_csv  # noqa: F401


def validate(path):
    """Validate a workbook against its embedded answer keys via engine.py."""
    import json
    report = translate_workbook(path)
    all_pass = True
    for sheet in report["sheets"]:
        if "error" in sheet:
            print(f"\n=== Sheet {sheet['deal']}: ERROR — {sheet['error']} ===")
            all_pass = False
            continue
        v = sheet.get("validation")
        result = v["result"] if v else "no answer key"
        print(
            f"\n=== Sheet {sheet['deal']}: "
            f"{sheet['input_lines']} CTO lines -> {sheet['output_lines']} translated "
            f"| validation: {result} ==="
        )
        if v and v["issues"]:
            for issue in v["issues"]:
                print(f"  ISSUE: {issue}")
        if v and v["warnings"]:
            for w in v["warnings"]:
                print(f"  WARN: {w}")
        if sheet["flags"]:
            for f in sheet["flags"]:
                print(f"  FLAG: {f}")
        if v:
            all_pass = all_pass and (result == "pass")
    return all_pass


