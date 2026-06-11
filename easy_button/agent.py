"""Easy-button agent: CTO quote -> stock-fulfillment quote.

Wraps the deterministic translation engine (engine.py) in a Claude
Agent SDK agent. Division of labor, per Steff's requirements:

  - SKU decisions: 100% deterministic engine. The model NEVER picks,
    renames, or invents a SKU.
  - The agent: runs the tools, explains the result, surfaces flags for
    the human checkpoint, and writes the orderable CSV.

Trust boundary:
  - The model is locked to translate_quote and export_quote.
  - Both tools verify that the supplied path matches the single approved
    input path captured at startup — any other path is rejected.
  - export_quote derives its output path from the approved input path;
    the model cannot supply or override the output path.
  - The translation report is cached server-side; export_quote exports
    exactly the cached report so translate and export cannot diverge.

Auth comes from the local Claude Code install (~/.claude). No API keys.

Usage:
    python -m easy_button.agent <quote.xls>

Zero-dollar bundle handling: drop (ships inside the spare box). This is the
default engine behavior. The keep alternative is not exposed until Steff
confirms canonical policy (see PLAN.md Gap R5-3).
"""
import asyncio
import json
import os
import sys
from pathlib import Path

try:
    from claude_agent_sdk import (
        ClaudeAgentOptions,
        ClaudeSDKClient,
        CLINotFoundError,
        ProcessError,
        create_sdk_mcp_server,
        tool,
    )
except ImportError as e:
    print(
        f"error: claude-agent-sdk is not installed ({e}). "
        "Run: pip install claude-agent-sdk",
        file=sys.stderr,
    )
    sys.exit(2)

from .engine import export_csv, translate_workbook

# Set at startup to the resolved absolute input path.
# Both tools reject any path that doesn't match.
_APPROVED_PATH: str = ""

# Cached report produced by translate_quote; export_quote uses this directly
# so translate and export cannot diverge.
_CACHED_REPORT = None  # type: dict | None


def _safe_output_path(input_path: str) -> str:
    p = Path(input_path).expanduser().resolve()
    return str(p.with_suffix("")) + "_stock_fulfillment.csv"


def _require_approved_path(path: str) -> str:
    resolved = str(Path(path).expanduser().resolve())
    if not _APPROVED_PATH:
        raise ValueError("no approved input path registered — internal error")
    if resolved != _APPROVED_PATH:
        raise ValueError(
            f"path '{path}' is not the approved input path — rejected"
        )
    if not Path(resolved).exists():
        raise ValueError(f"file not found: {resolved}")
    return resolved


@tool(
    "translate_quote",
    "Translate the approved Cisco CTO quote workbook (.xls) to its stock-fulfillment "
    "state using the validated deterministic rule set R1-R5. Returns per-line "
    "decisions (keep/swap/drop/flag with reasons), human-review flags, and "
    "answer-key validation when the sheet contains one. The path must match the "
    "file supplied at startup.",
    {"path": str},
)
async def translate_quote(args):
    global _CACHED_REPORT
    try:
        path = _require_approved_path(args["path"])
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"error: {e}"}]}
    report = translate_workbook(path)
    _CACHED_REPORT = report
    return {"content": [{"type": "text", "text": json.dumps(report, indent=1)}]}


@tool(
    "export_quote",
    "Write the kept (orderable) lines of the most recent translation to a "
    "CSV file the sales team can load. Uses the cached translation — translate "
    "and export cannot diverge. Output path is derived from the approved input "
    "path; the model cannot override it. Blocked if any sheet has unresolved "
    "UNKNOWN-DESCENDANT flags.",
    {"path": str},
)
async def export_quote(args):
    global _CACHED_REPORT
    try:
        path = _require_approved_path(args["path"])
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"error: {e}"}]}

    if _CACHED_REPORT is None:
        return {"content": [{"type": "text",
                              "text": "error: call translate_quote first"}]}

    out = _safe_output_path(path)
    # Reject symlinks on the output path (is_symlink uses lstat — no-follow)
    if Path(out).is_symlink():
        return {"content": [{"type": "text",
                              "text": "error: output path is a symlink — rejected"}]}

    try:
        export_csv(_CACHED_REPORT, out)
    except ValueError as e:
        return {"content": [{"type": "text", "text": f"error: {e}"}]}
    return {"content": [{"type": "text", "text": f"written: {out}"}]}


SERVER = create_sdk_mcp_server(
    name="easy_button", version="1.0.0", tools=[translate_quote, export_quote]
)

SYSTEM_PROMPT = """You are the stock-fulfillment easy button for Cisco CTO quotes.

Hard rules:
- Every SKU you mention MUST come verbatim from translate_quote tool output.
  Never propose, correct, or invent a SKU under any circumstance.
- Always call translate_quote first, then export_quote.
- Both tools require the same approved file path supplied at startup.
  Do not attempt to substitute or modify the path.
- Your report for each deal (sheet) contains exactly:
  1. Line counts: input -> orderable output.
  2. SKU swaps performed (original -> spare '=' SKU).
  3. Dropped bundles, one line: count + 'ship inside the spare box'.
  4. UNKNOWN-DESCENDANT flags: every flagged line, verbatim. If none, say so.
  5. FLAGS FOR HUMAN REVIEW: every other flag from the tool, verbatim. If none, say so.
  6. Validation: pass/partial/fail if the sheet had an answer key, else 'not present'.
- Keep it terse. A salesperson reads this before clicking order."""


async def run(quote_path: str) -> int:
    global _APPROVED_PATH, _CACHED_REPORT
    _APPROVED_PATH = str(Path(quote_path).expanduser().resolve())
    _CACHED_REPORT = None

    options = ClaudeAgentOptions(
        mcp_servers={"easy_button": SERVER},
        allowed_tools=[
            "mcp__easy_button__translate_quote",
            "mcp__easy_button__export_quote",
        ],
        disallowed_tools=["Bash", "Write", "Edit", "WebSearch", "WebFetch"],
        system_prompt=SYSTEM_PROMPT,
        setting_sources=[],
        max_turns=6,
    )
    prompt = json.dumps({
        "action": "translate_and_export",
        "path": _APPROVED_PATH,
    })
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(
                f"Process this translation request: {prompt}"
            )
            async for message in client.receive_response():
                for block in getattr(message, "content", []) or []:
                    text = getattr(block, "text", None)
                    if text:
                        print(text)
    except CLINotFoundError:
        print("error: Claude Code CLI not found - install and log in first",
              file=sys.stderr)
        return 2
    except ProcessError as e:
        print(f"error: agent process failed (exit {e.exit_code})", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    quote = Path(args[0]).expanduser()
    if not quote.exists():
        print(f"error: {quote} not found", file=sys.stderr)
        return 2
    return asyncio.run(run(str(quote.resolve())))


if __name__ == "__main__":
    sys.exit(main())
