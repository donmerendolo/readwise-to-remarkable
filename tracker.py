#!/usr/bin/env python3
"""Export tracking for the Readwise to reMarkable sync tool."""

import re
from datetime import UTC, datetime
from pathlib import Path


class ExportTracker:
    """Tracks which documents have been exported to reMarkable."""

    def __init__(self, tracker_file: Path | None = None) -> None:
        if tracker_file is None:
            tracker_file = Path(__file__).parent / "exported_documents.txt"

        self.tracker_file = tracker_file
        self.exported_docs: set[str] = set()
        self.load_exported_docs()

    def load_exported_docs(self) -> None:
        """Load previously exported document IDs from file."""
        if not self.tracker_file.exists():
            return

        try:
            with Path.open(self.tracker_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        match = re.search(r"\(([^)]+)\)$", line)
                        if match:
                            self.exported_docs.add(match.group(1))
        except Exception as e:
            print(f"Warning: Could not load exported files: {e}")

    def is_exported(self, doc_id: str) -> bool:
        """Check if a document has already been exported."""
        return doc_id in self.exported_docs

    def mark_exported(self, doc_id: str, title: str) -> None:
        """Mark a document as exported."""
        timestamp = datetime.now(tz=UTC).isoformat()
        entry = f"{timestamp} - {title} ({doc_id})\n"

        with Path.open(self.tracker_file, "a", encoding="utf-8") as f:
            f.write(entry)

        self.exported_docs.add(doc_id)
