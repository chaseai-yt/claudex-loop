"""Pipeline CLI orchestrator.

Usage:
  python -m src.pipeline.pipeline --file PATH [--deal-id ID]
      [--reset-from step{n}] [--mock-inventory] [--keep-checkpoints] [--html]

--reset-from step{n}  Resume from agent n (requires checkpoint n-1 to exist).
--mock-inventory      Force mock client regardless of INVENTORY_API_URL env var.
--keep-checkpoints    Do not delete checkpoints after successful completion.
--html                Also write OUTPUTS/recommendation_{deal_id}.html (fallback panel).
--deal-id             Optional override for deal ID extracted from file.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (
    checkpoint as ckpt_mod,
    eligibility_engine,
    file_validator,
    inventory_pricer,
    recommendation_writer,
    spa_parser,
)
from .inventory_client import (
    LiveInventoryClient,
    PipelineInventoryClient,
    PipelineMockInventoryClient,
)
from .models import SPALine


def _build_client(mock: bool) -> PipelineInventoryClient:
    import os
    if mock:
        return PipelineMockInventoryClient()
    if os.environ.get("INVENTORY_API_URL"):
        return LiveInventoryClient()
    return PipelineMockInventoryClient()


def _parse_step(reset_from: str | None) -> int:
    """Return the agent number to resume from (1-5), or 1 if not set."""
    if not reset_from:
        return 1
    try:
        n = int(reset_from.lower().replace("step", ""))
        if n < 1 or n > 5:
            raise ValueError
        return n
    except ValueError:
        print(f"ERROR: --reset-from must be step1 through step5, got '{reset_from}'", file=sys.stderr)
        sys.exit(1)


def run(
    file_path: str,
    deal_id_override: str | None = None,
    reset_from: str | None = None,
    mock_inventory: bool = False,
    keep_checkpoints: bool = False,
    html: bool = False,
) -> None:
    import os
    if os.environ.get("PIPELINE_ENV") == "production" and keep_checkpoints:
        print(
            "ERROR: --keep-checkpoints requires Legal approval in production",
            file=sys.stderr,
        )
        sys.exit(1)

    start_step = _parse_step(reset_from)
    client = _build_client(mock_inventory)

    # -----------------------------------------------------------------------
    # Agent 1 — file_validator
    # -----------------------------------------------------------------------
    if start_step <= 1:
        print("[ 1/5 ] file_validator ...", flush=True)
        try:
            file_validator.run(file_path)
        except file_validator.ValidationError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        print("[ 1/5 ] file_validator — skipped (loaded from checkpoint)", flush=True)

    # -----------------------------------------------------------------------
    # Agent 2 — spa_parser
    # -----------------------------------------------------------------------
    if start_step <= 2:
        print("[ 2/5 ] spa_parser ...", flush=True)
        lines: list[SPALine] = spa_parser.run(file_path)
    else:
        print("[ 2/5 ] spa_parser — loading from checkpoint ...", flush=True)
        raw = ckpt_mod.read_checkpoint(2)
        lines = [SPALine(**item) for item in raw]

    # Extract deal_id from lines or use override
    deal_id = deal_id_override
    if not deal_id:
        # Attempt to extract from filename as fallback
        stem = Path(file_path).stem
        import re
        m = re.search(r"\d{6,}", stem)
        deal_id = m.group(0) if m else stem

    print(f"      deal_id = {deal_id}  ({len(lines)} lines)", flush=True)

    # -----------------------------------------------------------------------
    # Agent 3 — eligibility_engine
    # -----------------------------------------------------------------------
    if start_step <= 3:
        print("[ 3/5 ] eligibility_engine ...", flush=True)
        lines = eligibility_engine.run(lines)
    else:
        print("[ 3/5 ] eligibility_engine — loading from checkpoint ...", flush=True)
        raw = ckpt_mod.read_checkpoint(3)
        lines = [SPALine(**item) for item in raw]

    eligible_count = sum(
        1 for ln in lines if ln.eligibility_status in ("ELIGIBLE", "DNA_AUTO_PASS")
    )
    print(f"      {eligible_count}/{len(lines)} lines eligible", flush=True)

    # -----------------------------------------------------------------------
    # Agent 4 — inventory_pricer
    # -----------------------------------------------------------------------
    if start_step <= 4:
        print("[ 4/5 ] inventory_pricer ...", flush=True)
        lines = inventory_pricer.run(lines, client)
    else:
        print("[ 4/5 ] inventory_pricer — loading from checkpoint ...", flush=True)
        raw = ckpt_mod.read_checkpoint(4)
        lines = [SPALine(**item) for item in raw]

    # -----------------------------------------------------------------------
    # Agent 5 — recommendation_writer
    # -----------------------------------------------------------------------
    print("[ 5/5 ] recommendation_writer ...", flush=True)
    rec = recommendation_writer.run(deal_id, lines)

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    if html:
        from . import recommendation_writer as _rw
        html_path = _rw._OUTPUTS_DIR / f"recommendation_{deal_id}.html"
        html_path.write_text(_rw.generate_html(rec), encoding="utf-8")
        print(f"\n✓ Done — OUTPUTS/recommendation_{deal_id}.json + .html", flush=True)
    else:
        print(f"\n✓ Done — OUTPUTS/recommendation_{deal_id}.json", flush=True)
    for ss in rec.ship_sets:
        b_label = "AVAILABLE" if ss.option_b.available else f"SUPPRESSED ({ss.option_b.reason})"
        flags = [item.flag for item in ss.review_queue]
        flag_str = f"  flags={flags}" if flags else ""
        print(
            f"  Ship set {ss.ship_set_id}: Option B={b_label}  "
            f"confidence={ss.confidence}{flag_str}",
            flush=True,
        )

    if not keep_checkpoints:
        ckpt_mod.cleanup_checkpoints()


def main() -> None:
    parser = argparse.ArgumentParser(description="Cisco Fulfillment Optimization Pipeline v2")
    parser.add_argument("--file", required=True, help="Path to Cisco SPA Deal ID Excel file")
    parser.add_argument("--deal-id", default=None, help="Optional deal ID override (default: extracted from file)")
    parser.add_argument("--reset-from", default=None, metavar="STEP", help="Resume from step{n} (e.g. step3)")
    parser.add_argument("--mock-inventory", action="store_true", help="Force mock inventory client")
    parser.add_argument("--keep-checkpoints", action="store_true", help="Keep checkpoint files after completion")
    parser.add_argument("--html", action="store_true", help="Also write HTML recommendation file")
    args = parser.parse_args()

    run(
        file_path=args.file,
        deal_id_override=args.deal_id,
        reset_from=args.reset_from,
        mock_inventory=args.mock_inventory,
        keep_checkpoints=args.keep_checkpoints,
        html=args.html,
    )


if __name__ == "__main__":
    main()
