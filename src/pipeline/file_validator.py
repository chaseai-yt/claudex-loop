"""Agent 1 — file_validator.

Validates that the uploaded file is a Cisco SPA Deal ID Excel file with
all required columns present. Fails fast with a structured ValidationError.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from .checkpoint import write_checkpoint

# Columns that must be present in every Cisco SPA Deal ID file.
REQUIRED_COLUMNS = {
    "LINE#",
    "INCLUDED ITEM",
    "SPARE EQUIVALENT SKU NAME",
    "UNIT NET PRICE",
    "BpaBuyingProgram",
}

# At least one of these must be present as the SKU/part-number column.
# The exact name varies across SPA versions; validated separately below.
SKU_COLUMN_CANDIDATES = ("SKU", "Part Number", "PART NUMBER", "PN", "Item Number")


class ValidationError(Exception):
    def __init__(self, message: str, missing_columns: Optional[list[str]] = None) -> None:
        super().__init__(message)
        self.missing_columns = missing_columns or []


def run(file_path: str | Path) -> dict:
    path = Path(file_path)

    if not path.exists():
        raise ValidationError(f"File not found: {path}")

    suffix = path.suffix.lower()
    if suffix not in {".xlsx", ".xls"}:
        raise ValidationError(
            f"Unsupported file type '{suffix}'. Expected .xlsx or .xls."
        )

    try:
        df = pd.read_excel(path, nrows=0)
    except Exception as exc:
        raise ValidationError(f"Could not read Excel file: {exc}") from exc

    present = set(df.columns)
    missing = [col for col in sorted(REQUIRED_COLUMNS) if col not in present]
    if missing:
        raise ValidationError(
            f"Missing required columns: {missing}. "
            f"Found columns: {sorted(present)}",
            missing_columns=missing,
        )

    if not any(c in present for c in SKU_COLUMN_CANDIDATES):
        raise ValidationError(
            f"No SKU/part-number column found. "
            f"Expected one of {SKU_COLUMN_CANDIDATES}. "
            f"Found columns: {sorted(present)}",
        )

    result = {
        "file": str(path.resolve()),
        "format": suffix,
        "columns_present": sorted(present),
        "validation": "PASSED",
    }
    write_checkpoint(1, result)
    return result
