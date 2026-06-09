"""Pipeline tests — SC-02, SC-03, SC-04 compliance.

SC-02: Zero false passes on Deal IDs 83737219, 84709746, 84251013.
SC-03: Pricing ceiling never violated (spare_net_price never > UNIT NET PRICE).
       Option C never appears in any output.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.pipeline import eligibility_engine, file_validator, inventory_pricer, recommendation_writer, spa_parser
from src.pipeline.inventory_client import PipelineMockInventoryClient
from src.pipeline.models import SPALine

from tests.pipeline.fixtures import build_deal_83737219, build_deal_84709746, build_deal_84251013


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_INV_PATH = Path("data/mock/pipeline_inventory.json")


def _run_full(file_path: Path, deal_id: str):
    """Run agents 1-5 and return (lines, recommendation)."""
    file_validator.run(file_path)
    lines = spa_parser.run(file_path)
    lines = eligibility_engine.run(lines)
    client = PipelineMockInventoryClient(_MOCK_INV_PATH)
    lines = inventory_pricer.run(lines, client)
    rec = recommendation_writer.run(deal_id, lines)
    return lines, rec


# ---------------------------------------------------------------------------
# file_validator
# ---------------------------------------------------------------------------

class TestFileValidator:
    def test_rejects_missing_file(self, tmp_path):
        with pytest.raises(file_validator.ValidationError, match="not found"):
            file_validator.run(tmp_path / "nonexistent.xlsx")

    def test_rejects_unsupported_extension(self, tmp_path):
        f = tmp_path / "bad.csv"
        f.write_text("col1,col2")
        with pytest.raises(file_validator.ValidationError, match="Unsupported"):
            file_validator.run(f)

    def test_rejects_missing_columns(self, tmp_path):
        import openpyxl, io
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["LINE#", "SKU"])  # missing required columns
        buf = io.BytesIO()
        wb.save(buf)
        f = tmp_path / "bad.xlsx"
        f.write_bytes(buf.getvalue())
        with pytest.raises(file_validator.ValidationError, match="Missing"):
            file_validator.run(f)

    def test_passes_valid_file(self, tmp_path):
        path = build_deal_83737219(tmp_path)
        result = file_validator.run(path)
        assert result["validation"] == "PASSED"


# ---------------------------------------------------------------------------
# spa_parser
# ---------------------------------------------------------------------------

class TestSpaParser:
    def test_line_count_83737219(self, tmp_path):
        path = build_deal_83737219(tmp_path)
        lines = spa_parser.run(path)
        assert len(lines) == 28

    def test_ship_set_grouping(self, tmp_path):
        path = build_deal_83737219(tmp_path)
        lines = spa_parser.run(path)
        ship_set_ids = {ln.ship_set_id for ln in lines}
        assert 1 in ship_set_ids
        assert 2 in ship_set_ids

    def test_line_number_stringified(self, tmp_path):
        """LINE# cells (numeric in Excel) must produce correct ship_set_id."""
        path = build_deal_83737219(tmp_path)
        lines = spa_parser.run(path)
        for ln in lines:
            assert isinstance(ln.ship_set_id, int)
            assert ln.ship_set_id >= 0


# ---------------------------------------------------------------------------
# eligibility_engine
# ---------------------------------------------------------------------------

class TestEligibilityEngine:
    def test_gate1_included_item_excluded(self, tmp_path):
        """Lines with INCLUDED ITEM = Yes must fail Gate 1."""
        path = build_deal_83737219(tmp_path)
        lines = spa_parser.run(path)
        lines = eligibility_engine.run(lines)
        gate1_fails = [ln for ln in lines if ln.eligibility_status == "INELIGIBLE_GATE1"]
        assert len(gate1_fails) > 0, "Expected some INCLUDED ITEM=Yes lines to fail Gate 1"

    def test_dna_auto_pass(self, tmp_path):
        """DNA/EA 3.0 lines must auto-pass without gate evaluation."""
        path = build_deal_83737219(tmp_path)
        lines = spa_parser.run(path)
        lines = eligibility_engine.run(lines)
        dna_lines = [ln for ln in lines if ln.dna_auto_pass]
        assert len(dna_lines) >= 1, "Expected at least 1 DNA auto-pass line"
        for ln in dna_lines:
            assert ln.eligibility_status == "DNA_AUTO_PASS"

    def test_gate4_no_spare_sku(self, tmp_path):
        """Lines with empty SPARE EQUIVALENT SKU NAME must fail Gate 4."""
        path = build_deal_83737219(tmp_path)
        lines = spa_parser.run(path)
        lines = eligibility_engine.run(lines)
        gate4_fails = [ln for ln in lines if ln.eligibility_status == "INELIGIBLE_GATE4"]
        assert len(gate4_fails) > 0

    def test_sc02_zero_false_passes_83737219(self, tmp_path):
        """SC-02: No line without a spare SKU may be marked ELIGIBLE."""
        path = build_deal_83737219(tmp_path)
        lines = spa_parser.run(path)
        lines = eligibility_engine.run(lines)
        for ln in lines:
            if ln.eligibility_status == "ELIGIBLE":
                assert ln.spare_sku, f"Line {ln.line_number} marked ELIGIBLE but has no spare_sku"

    def test_sc02_zero_false_passes_84709746(self, tmp_path):
        path = build_deal_84709746(tmp_path)
        lines = spa_parser.run(path)
        lines = eligibility_engine.run(lines)
        for ln in lines:
            if ln.eligibility_status == "ELIGIBLE":
                assert ln.spare_sku, f"Line {ln.line_number} marked ELIGIBLE but has no spare_sku"

    def test_sc02_zero_false_passes_84251013(self, tmp_path):
        path = build_deal_84251013(tmp_path)
        lines = spa_parser.run(path)
        lines = eligibility_engine.run(lines)
        for ln in lines:
            if ln.eligibility_status == "ELIGIBLE":
                assert ln.spare_sku, f"Line {ln.line_number} marked ELIGIBLE but has no spare_sku"


# ---------------------------------------------------------------------------
# inventory_pricer
# ---------------------------------------------------------------------------

class TestInventoryPricer:
    def test_sc03_pricing_ceiling_never_violated(self, tmp_path):
        """SC-03: spare_net_price must never exceed UNIT NET PRICE on priced lines."""
        path = build_deal_83737219(tmp_path)
        lines = spa_parser.run(path)
        lines = eligibility_engine.run(lines)
        client = PipelineMockInventoryClient(_MOCK_INV_PATH)
        lines = inventory_pricer.run(lines, client)
        for ln in lines:
            if ln.pricing_status == "BLOCK":
                assert (ln.spare_net_price or 0.0) > (ln.unit_net_price or 0.0), \
                    f"Line {ln.line_number} marked BLOCK but spare_net_price <= unit_net_price"
            elif ln.pricing_status == "OK" and ln.spare_net_price is not None:
                assert ln.spare_net_price <= (ln.unit_net_price or float("inf")), \
                    f"SC-03 VIOLATION: Line {ln.line_number} spare_net_price {ln.spare_net_price} > ceiling {ln.unit_net_price}"

    def test_dna_lines_not_api_called(self, tmp_path):
        """DNA auto-pass lines must have spare_net_price=0 and pricing_status=OK."""
        path = build_deal_83737219(tmp_path)
        lines = spa_parser.run(path)
        lines = eligibility_engine.run(lines)
        client = PipelineMockInventoryClient(_MOCK_INV_PATH)
        lines = inventory_pricer.run(lines, client)
        for ln in lines:
            if ln.dna_auto_pass:
                assert ln.spare_net_price == 0.0
                assert ln.pricing_status == "OK"

    def test_ineligible_lines_skipped(self, tmp_path):
        path = build_deal_83737219(tmp_path)
        lines = spa_parser.run(path)
        lines = eligibility_engine.run(lines)
        client = PipelineMockInventoryClient(_MOCK_INV_PATH)
        lines = inventory_pricer.run(lines, client)
        for ln in lines:
            if ln.eligibility_status not in ("ELIGIBLE", "DNA_AUTO_PASS"):
                assert ln.pricing_status == "SKIPPED"


# ---------------------------------------------------------------------------
# recommendation_writer
# ---------------------------------------------------------------------------

class TestRecommendationWriter:
    def test_option_c_never_in_output(self, tmp_path):
        """Option C must never appear in any recommendation output (SC-03 corollary)."""
        for build_fn, deal_id in [
            (build_deal_83737219, "83737219"),
            (build_deal_84709746, "84709746"),
            (build_deal_84251013, "84251013"),
        ]:
            path = build_fn(tmp_path)
            _, rec = _run_full(path, deal_id)
            rec_json = json.dumps(rec.model_dump(mode="json"))
            assert "option_c" not in rec_json.lower() or '"option_c_suppressed": true' in rec_json, \
                f"option_c_suppressed must be true for {deal_id}"
            for ss in rec.ship_sets:
                assert ss.option_c_suppressed is True

    def test_option_b_suppressed_when_no_spare(self, tmp_path):
        """Ship sets with no spare equivalent must have Option B suppressed."""
        path = build_deal_84251013(tmp_path)
        _, rec = _run_full(path, "84251013")
        # Ship set 2 has a line with no spare — Option B must be suppressed
        ship_set_2 = next((ss for ss in rec.ship_sets if ss.ship_set_id == 2), None)
        assert ship_set_2 is not None
        assert not ship_set_2.option_b.available

    def test_option_a_always_available(self, tmp_path):
        """Option A must always be available on every ship set."""
        path = build_deal_83737219(tmp_path)
        _, rec = _run_full(path, "83737219")
        for ss in rec.ship_sets:
            assert ss.option_a.available is True

    def test_outcomes_jsonl_written(self, tmp_path, monkeypatch):
        """outcomes.jsonl must be written after pipeline completion."""
        import src.pipeline.recommendation_writer as rw
        outputs_dir = tmp_path / "OUTPUTS"
        outcomes_file = outputs_dir / "outcomes.jsonl"
        lock_file = outputs_dir / "outcomes.jsonl.lock"
        monkeypatch.setattr(rw, "_OUTPUTS_DIR", outputs_dir)
        monkeypatch.setattr(rw, "_OUTCOMES_FILE", outcomes_file)
        monkeypatch.setattr(rw, "_OUTCOMES_LOCK", lock_file)

        path = build_deal_83737219(tmp_path)
        file_validator.run(path)
        lines = spa_parser.run(path)
        lines = eligibility_engine.run(lines)
        client = PipelineMockInventoryClient(_MOCK_INV_PATH)
        lines = inventory_pricer.run(lines, client)
        recommendation_writer.run("83737219", lines)

        assert outcomes_file.exists()
        records = [json.loads(line) for line in outcomes_file.read_text().splitlines() if line.strip()]
        assert len(records) >= 1
        assert records[-1]["deal_id"] == "83737219"
        assert "lines_eligible" in records[-1]
        assert "option_b_available" in records[-1]

    def test_confidence_high_when_clean(self, tmp_path):
        """Confidence must be HIGH when all eligible lines have confirmed stock and no flags."""
        path = build_deal_84709746(tmp_path)
        _, rec = _run_full(path, "84709746")
        # Ship set 1 should have HIGH confidence if all eligible lines pass cleanly
        for ss in rec.ship_sets:
            if ss.option_b.available and not ss.review_queue:
                assert ss.confidence == "HIGH"
