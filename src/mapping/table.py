from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from typing import Optional

from .models import MappingEntry
from .models import MappingTable as MappingTableModel

_DEFAULT_CSV = Path(__file__).parent.parent.parent / "data" / "mock" / "mapping_table.csv"


class MappingTable:
    """Manager for the CTO-to-spare SKU mapping table."""

    def __init__(self, csv_path: Optional[Path] = None) -> None:
        self._entries: list[MappingEntry] = []
        self._version: int = 1
        self._created_at: datetime = datetime.now(timezone.utc)
        path = Path(csv_path) if csv_path else _DEFAULT_CSV
        if path.exists():
            self.load_from_csv(str(path))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup(self, cto_sku: str) -> Optional[MappingEntry]:
        """Return the active, non-expired mapping for *cto_sku*, or None."""
        today = date.today()
        for entry in self._entries:
            if (
                entry.cto_sku == cto_sku
                and entry.is_active
                and entry.effective_date <= today
                and (entry.expiry_date is None or entry.expiry_date >= today)
            ):
                return entry
        return None

    def get_version(self) -> int:
        return self._version

    def load_from_csv(self, path: str) -> None:
        """Load (or reload) mapping entries from a CSV file."""
        entries: list[MappingEntry] = []
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for i, row in enumerate(reader):
                entry = MappingEntry(
                    id=row.get("id", f"map-{i:04d}"),
                    cto_sku=row["cto_sku"].strip(),
                    spare_sku=row["spare_sku"].strip(),
                    approved_by=row.get("approved_by", "system"),
                    approved_at=datetime.fromisoformat(
                        row.get("approved_at", "2025-01-01T00:00:00")
                    ),
                    cisco_ref=row.get("cisco_ref", ""),
                    is_strict_match=row.get("is_strict_match", "false").lower() == "true",
                    version=int(row.get("version", 1)),
                    effective_date=date.fromisoformat(
                        row.get("effective_date", "2025-01-01")
                    ),
                    expiry_date=(
                        date.fromisoformat(row["expiry_date"])
                        if row.get("expiry_date", "").strip()
                        else None
                    ),
                    superseded_by=row.get("superseded_by") or None,
                    is_active=row.get("is_active", "true").lower() == "true",
                )
                entries.append(entry)
        self._entries = entries
        # Derive version from max entry version
        if entries:
            self._version = max(e.version for e in entries)

    def to_model(self) -> MappingTableModel:
        return MappingTableModel(
            entries=self._entries,
            version=self._version,
            created_at=self._created_at,
        )
