from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import anthropic

from .models import ParseResult, Quote, QuoteLine
from .validator import validate_quote

_client = anthropic.Anthropic()

_SYSTEM_PROMPT = """\
You are a Cisco quote parser for TD SYNNEX's Maple AI inventory fulfillment system.
Given the raw text of a Cisco CTO/spare parts quote, extract structured data.

Return ONLY a JSON object with this exact schema (no markdown fences, no extra keys):
{
  "quote_id": "<string>",
  "cisco_deal_id": "<string>",
  "lines": [
    {
      "sku": "<string — Cisco part number>",
      "description": "<string>",
      "quantity": <integer>,
      "build_group": "<string — e.g. '1.0' or 'Group A'>",
      "is_cto": <true|false>,
      "spare_sku": "<string or null>",
      "confidence": <float 0.0-1.0>
    }
  ]
}

Rules:
- If you cannot confidently determine a field, use the best guess and set confidence < 0.8.
- If the SKU is completely unreadable, use "UNKNOWN-<index>".
- build_group must never be empty; infer from context if not explicit.
- is_cto should be true when the part number ends with common CTO suffixes or is listed as a configured item.
- spare_sku should be the equivalent spare/stock part when mentioned; otherwise null.
- quote_id and cisco_deal_id: extract from headers; use "UNKNOWN" if absent.
"""


def parse_quote_file(file_content: str, filename: str) -> ParseResult:
    """Parse raw quote file content via Claude and validate the result."""
    user_msg = (
        f"Filename: {filename}\n\n"
        f"Quote content:\n{file_content}"
    )

    message = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = message.content[0].text.strip()

    # Strip accidental markdown fences
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    data = json.loads(raw)

    lines = [QuoteLine(**line) for line in data["lines"]]

    # Aggregate exact duplicates before validation
    lines = _aggregate_exact_duplicates(lines)

    quote = Quote(
        quote_id=data["quote_id"],
        cisco_deal_id=data["cisco_deal_id"],
        lines=lines,
        parsed_at=datetime.now(timezone.utc),
        confirmed=False,
    )

    low_confidence = [
        i for i, line in enumerate(quote.lines) if line.confidence < 0.8
    ]

    _is_valid, errors = validate_quote(quote)

    warnings: list[str] = []
    if errors:
        warnings.extend(errors)

    return ParseResult(
        quote=quote,
        low_confidence_lines=low_confidence,
        warnings=warnings,
    )


def _aggregate_exact_duplicates(lines: list[QuoteLine]) -> list[QuoteLine]:
    """Merge lines with identical (sku, build_group) by summing quantities."""
    seen: dict[tuple[str, str], int] = {}  # key → index in result
    result: list[QuoteLine] = []

    for line in lines:
        key = (line.sku, line.build_group)
        if key in seen:
            existing = result[seen[key]]
            result[seen[key]] = existing.model_copy(
                update={"quantity": existing.quantity + line.quantity}
            )
        else:
            seen[key] = len(result)
            result.append(line)

    return result
