"""Export outcomes.jsonl → outcomes.parquet for Power BI.

Owner: Harsh Dhabalia
Schedule: nightly (cron or Azure Function trigger)

Usage:
    python scripts/export_outcomes_parquet.py [--input PATH] [--output PATH]

The parquet file is written atomically via a temp file + rename so that Power BI
never reads a partially-written file.

Two KPIs computed by Power BI from this export:
  override_rate        = rows where option_b_available=True AND option_selected='A'
                         / rows where option_b_available=True
  option_b_surface_rate = rows where option_b_available=True / all eligible rows
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_INPUT = _PROJECT_ROOT / "OUTPUTS" / "outcomes.jsonl"
_DEFAULT_OUTPUT = _PROJECT_ROOT / "OUTPUTS" / "outcomes.parquet"


def export(input_path: Path, output_path: Path) -> int:
    try:
        import pandas as pd
    except ImportError:
        print("ERROR: pandas is required — pip install pandas", file=sys.stderr)
        sys.exit(1)
    try:
        import pyarrow  # noqa: F401 — validates engine availability
    except ImportError:
        print("ERROR: pyarrow is required — pip install pyarrow", file=sys.stderr)
        sys.exit(1)

    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    lines = [ln for ln in input_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        print("WARNING: outcomes.jsonl is empty — writing empty parquet", file=sys.stderr)

    df = pd.read_json("\n".join(lines), lines=True) if lines else pd.DataFrame()

    # Normalise types for Power BI
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    if "option_b_available" in df.columns:
        df["option_b_available"] = df["option_b_available"].astype(bool)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(output_path.parent), suffix=".parquet.tmp")
    os.close(tmp_fd)
    try:
        df.to_parquet(tmp_path, index=False, engine="pyarrow")
        os.replace(tmp_path, output_path)
    except Exception:
        if Path(tmp_path).exists():
            os.unlink(tmp_path)
        raise

    row_count = len(df)
    print(f"Exported {row_count} record(s) → {output_path}")
    return row_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Export outcomes.jsonl to Parquet for Power BI")
    parser.add_argument("--input", default=str(_DEFAULT_INPUT), help="Path to outcomes.jsonl")
    parser.add_argument("--output", default=str(_DEFAULT_OUTPUT), help="Path for output .parquet")
    args = parser.parse_args()
    export(Path(args.input), Path(args.output))


if __name__ == "__main__":
    main()
