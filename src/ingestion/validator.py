from __future__ import annotations

from collections import defaultdict

from .models import Quote


def validate_quote(quote: Quote) -> tuple[bool, list[str]]:
    """Fail-closed validator for parsed quotes.

    Returns (is_valid, errors).  Any non-empty errors list means the quote
    must not proceed to evaluation without manual intervention.
    """
    errors: list[str] = []

    # Zero lines
    if not quote.lines:
        errors.append("Quote contains zero line items.")
        return False, errors

    # Per-line checks + duplicate tracking
    # key: (sku, build_group) → list of line indices
    exact_map: dict[tuple[str, str], list[int]] = defaultdict(list)
    # key: sku → set of build_groups
    sku_bg_map: dict[str, set[str]] = defaultdict(set)

    for idx, line in enumerate(quote.lines):
        # Quantity
        if line.quantity <= 0:
            errors.append(
                f"Line {idx} ({line.sku}): quantity must be > 0, got {line.quantity}."
            )

        # Unknown / empty SKU
        if not line.sku or line.sku.upper().startswith("UNKNOWN"):
            errors.append(
                f"Line {idx}: SKU is empty or unknown ('{line.sku}'). "
                "Manual Review required."
            )

        # Empty build_group
        if not line.build_group or not line.build_group.strip():
            errors.append(
                f"Line {idx} ({line.sku}): build_group is empty."
            )

        exact_map[(line.sku, line.build_group)].append(idx)
        sku_bg_map[line.sku].add(line.build_group)

    # Duplicate handling
    for (sku, bg), indices in exact_map.items():
        if len(indices) > 1:
            # Exact duplicates: aggregate quantities (warn, don't reject)
            total_qty = sum(quote.lines[i].quantity for i in indices)
            # Note: aggregation happens in parser; here we just warn
            errors.append(
                f"Lines {indices} are exact duplicates (sku={sku}, build_group={bg}). "
                f"Quantities will be aggregated to {total_qty}. Please confirm."
            )

    for sku, build_groups in sku_bg_map.items():
        if len(build_groups) > 1:
            errors.append(
                f"SKU '{sku}' appears in multiple build_groups "
                f"({sorted(build_groups)}). Flagged for Manual Review."
            )

    # Exact-duplicate warnings are advisory; ambiguous duplicates are hard errors
    hard_errors = [
        e for e in errors
        if "Flagged for Manual Review" in e
        or "quantity must be > 0" in e
        or "SKU is empty or unknown" in e
        or "build_group is empty" in e
        or "zero line items" in e
    ]

    is_valid = len(hard_errors) == 0
    return is_valid, errors
