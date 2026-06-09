"""Sanitized fixture builders for pipeline tests.

Real Deal ID files contain commercial customer pricing and are not committed.
These builders produce structurally equivalent Excel files preserving:
- line counts, ship set groupings, eligibility outcomes, pricing relationships.
Real pricing is replaced with synthetic values at the same order of magnitude.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

import openpyxl


def _make_workbook(rows: list[dict]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    if not rows:
        return b""
    headers = list(rows[0].keys())
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h) for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _base_row(
    line_num: str,
    sku: str,
    spare_sku: Optional[str],
    unit_net_price: float,
    bpa: str = "",
    included: str = "No",
    qty: int = 1,
) -> dict:
    return {
        "LINE#": line_num,
        "SKU": sku,
        "Description": f"Desc {sku}",
        "Quantity": qty,
        "UNIT NET PRICE": unit_net_price,
        "SPARE EQUIVALENT SKU NAME": spare_sku or "",
        "INCLUDED ITEM": included,
        "BpaBuyingProgram": bpa,
        "Order Type": "",
        "Ship Complete": "",
    }


def build_deal_83737219(tmp_path: Path) -> Path:
    """
    Sanitized equivalent of Deal 83737219 (SHI / Corgan Associates).
    28 lines total; 4 eligible; 1 DNA auto-pass; 1 near-ceiling WARN.
    """
    rows = []

    # Ship set 1 — 4 eligible lines
    rows.append(_base_row("1.0", "C9300-48P-A", "C9300-48P-A=", 5176.87))
    rows.append(_base_row("1.1", "C9300-48P-A", "C9300-48P-A=", 3200.00))
    # DNA auto-pass line
    rows.append(_base_row("1.2", "C9300-DNA-E-48=", "C9300-DNA-E-48=", 0.0, bpa="EA 3.0"))
    rows.append(_base_row("1.3", "C9300-48T-A", "C9300-48T-A=", 4900.00))

    # Ship set 1 — additional non-eligible lines (INCLUDED ITEM = Yes)
    for i in range(4, 16):
        rows.append(_base_row(f"1.{i}", f"INCL-SKU-{i:02d}", None, 100.0, included="Yes"))

    # Ship set 2 — no spare equivalent (gate 4 fails)
    for i in range(0, 12):
        rows.append(_base_row(f"2.{i}", f"NO-SPARE-{i:02d}", None, 500.0))

    assert len(rows) == 28

    path = tmp_path / "deal_83737219.xlsx"
    path.write_bytes(_make_workbook(rows))
    return path


def build_deal_84709746(tmp_path: Path) -> Path:
    """
    Sanitized equivalent of Deal 84709746 (SHI / iHeartMedia).
    7 lines; all in one ship set; mix of eligible and ineligible.
    """
    rows = [
        _base_row("1.0", "C9300-24P-A", "C9300-24P-A=", 3100.00),
        _base_row("1.1", "C9300-48P-A", "C9300-48P-A=", 5000.00),
        _base_row("1.2", "XaaS-SKU-01", None, 200.0, included="No"),
        _base_row("1.3", "INCL-SKU-01", None, 150.0, included="Yes"),
        _base_row("1.4", "C9300-48T-A", "C9300-48T-A=", 4800.00),
        _base_row("1.5", "NO-SPARE-01", None, 300.0),
        _base_row("1.6", "C9200CX-DNA-E-12=", "C9200CX-DNA-E-12=", 0.0, bpa="EA 3.0"),
    ]
    assert len(rows) == 7
    path = tmp_path / "deal_84709746.xlsx"
    path.write_bytes(_make_workbook(rows))
    return path


def build_deal_84251013(tmp_path: Path) -> Path:
    """
    Sanitized equivalent of Deal 84251013 (SHI / William O'Neil).
    2 ship sets + 1 training line; Option C discussion case.
    Ship set 1: full coverage.
    Ship set 2: partial (some no spare equiv — Option B suppressed).
    """
    rows = [
        # Ship set 1 — full coverage
        _base_row("1.0", "C9300-48P-A", "C9300-48P-A=", 5000.00),
        _base_row("1.1", "C9300-24P-A", "C9300-24P-A=", 3000.00),
        # Ship set 2 — partial (1 has spare, 1 doesn't)
        _base_row("2.0", "C9300-48T-A", "C9300-48T-A=", 4500.00),
        _base_row("2.1", "NO-SPARE-02", None, 600.0),
        # Training line (INCLUDED ITEM = Yes)
        _base_row("3.0", "TRAINING-01", None, 0.0, included="Yes"),
    ]
    path = tmp_path / "deal_84251013.xlsx"
    path.write_bytes(_make_workbook(rows))
    return path
