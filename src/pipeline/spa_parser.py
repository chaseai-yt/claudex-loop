"""Agent 2 — spa_parser.

Parses all line items from a Cisco SPA Deal ID Excel file.
Groups lines by ship set using int(str(LINE#).split('.')[0]).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pandas as pd

from .checkpoint import write_checkpoint
from .file_validator import SKU_COLUMN_CANDIDATES
from .models import SPALine


def _str_or_none(val) -> Optional[str]:
    if pd.isna(val):
        return None
    return str(val).strip() or None


def _float_or_zero(val) -> float:
    if pd.isna(val):
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _int_qty(val) -> int:
    if pd.isna(val):
        return 1
    try:
        return max(1, int(float(val)))
    except (ValueError, TypeError):
        return 1


def _parse_ship_set_id(line_num_val) -> int:
    """Extract integer ship set prefix from LINE# cell (may be float or string)."""
    raw = str(line_num_val).strip()
    raw = re.split(r"[.\s]", raw)[0]
    try:
        return int(raw)
    except ValueError:
        return 0


def _detect_sku_column(columns: list[str]) -> str:
    """Return the SKU/part-number column name; raises if not found."""
    for candidate in SKU_COLUMN_CANDIDATES:
        if candidate in columns:
            return candidate
    raise ValueError(
        f"No SKU column found in {columns}. Expected one of {SKU_COLUMN_CANDIDATES}. "
        "Run file_validator first."
    )


def _detect_order_type_column(columns: list[str]) -> Optional[str]:
    for candidate in ("Order Type", "ORDER TYPE", "Type"):
        if candidate in columns:
            return candidate
    return None


def _detect_ship_complete_column(columns: list[str]) -> Optional[str]:
    for candidate in ("Ship Complete", "SHIP COMPLETE", "Ship_Complete"):
        if candidate in columns:
            return candidate
    return None


def run(file_path: str | Path) -> list[SPALine]:
    path = Path(file_path)
    df = pd.read_excel(path, dtype=str)
    df = df.where(df.notna(), None)

    cols = list(df.columns)
    sku_col = _detect_sku_column(cols)      # raises if absent — validated upstream
    order_type_col = _detect_order_type_column(cols)
    ship_complete_col = _detect_ship_complete_column(cols)

    lines: list[SPALine] = []
    for _, row in df.iterrows():
        line_num_val = row.get("LINE#")
        if line_num_val is None or pd.isna(line_num_val):
            continue

        line_number = str(line_num_val).strip()
        ship_set_id = _parse_ship_set_id(line_num_val)

        sku = _str_or_none(row.get(sku_col)) or ""

        description = _str_or_none(row.get("Description")) or ""
        quantity = _int_qty(row.get("Quantity") or row.get("QTY") or 1)
        unit_net_price = _float_or_zero(row.get("UNIT NET PRICE"))
        spare_sku = _str_or_none(row.get("SPARE EQUIVALENT SKU NAME"))
        included_item = _str_or_none(row.get("INCLUDED ITEM"))
        bpa_buying_program = _str_or_none(row.get("BpaBuyingProgram"))

        # Gate 2: order type
        order_type: Optional[str] = None
        if order_type_col:
            order_type = _str_or_none(row.get(order_type_col))

        # Gate 3: ship complete
        ship_complete_flag: Optional[bool] = None
        gate3_unverifiable = False
        if ship_complete_col:
            val = _str_or_none(row.get(ship_complete_col))
            if val is not None:
                ship_complete_flag = val.lower() in {"yes", "true", "1", "y"}
            else:
                gate3_unverifiable = True
        else:
            gate3_unverifiable = True

        lines.append(
            SPALine(
                line_number=line_number,
                ship_set_id=ship_set_id,
                sku=sku,
                description=description,
                quantity=quantity,
                unit_net_price=unit_net_price,
                spare_sku=spare_sku,
                included_item=included_item,
                order_type=order_type,
                ship_complete_flag=ship_complete_flag,
                gate3_unverifiable=gate3_unverifiable,
                bpa_buying_program=bpa_buying_program,
            )
        )

    write_checkpoint(2, lines)
    return lines
