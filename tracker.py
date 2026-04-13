#!/usr/bin/env python3
"""Export tracking for the Readwise to reMarkable sync tool."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path


class ExportTracker:
    """Tracks which documents have been exported to reMarkable.

    Uses JSON format to store doc_id, title, remote filename, and timestamp.
    Supports migration from the old text-based format.
    """

    def __init__(self, tracker_file: Path | None = None) -> None:
        if tracker_file is None:
            tracker_file = Path(__file__).parent / "exported_documents.json"

        self.tracker_file = tracker_file
        self.data: dict = {"exported": {}}
        self._load()

    def _load(self) -> None:
        """Load tracker data, migrating from old format if needed."""
        if self.tracker_file.exists():
            try:
                with self.tracker_file.open(encoding="utf-8") as f:
                    self.data = json.load(f)
                return
            except (json.JSONDecodeError, ValueError):
                pass

        # Try migrating from old text format
        old_txt = self.tracker_file.with_suffix(".txt")
        if old_txt.exists():
            self._migrate_from_txt(old_txt)
            return

        # Also check for .json that's actually txt content (entrypoint mismatch)
        if self.tracker_file.exists():
            try:
                self._migrate_from_txt(self.tracker_file)
                return
            except Exception:
                pass

    def _migrate_from_txt(self, txt_path: Path) -> None:
        """Migrate from old text-based tracker format."""
        print(f"Migrating tracker from {txt_path} to JSON format...")
        try:
            with txt_path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # Format: timestamp - Title (doc_id)
                    match = re.match(
                        r"(.+?) - (.+?) \(([^)]+)\)$", line
                    )
                    if match:
                        timestamp, title, doc_id = match.groups()
                        self.data["exported"][doc_id] = {
                            "title": title,
                            "remote_name": "",  # Unknown from old format
                            "exported_at": timestamp,
                        }
            self._save()
            print(f"Migrated {len(self.data['exported'])} entries to JSON format.")
        except Exception as e:
            print(f"Warning: Could not migrate old tracker: {e}")

    def _save(self) -> None:
        """Persist tracker data to disk."""
        with self.tracker_file.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)

    def is_exported(self, doc_id: str) -> bool:
        """Check if a document has already been exported."""
        return doc_id in self.data.get("exported", {})

    def mark_exported(
        self, doc_id: str, title: str, remote_name: str = ""
    ) -> None:
        """Mark a document as exported."""
        self.data.setdefault("exported", {})[doc_id] = {
            "title": title,
            "remote_name": remote_name,
            "exported_at": datetime.now(tz=UTC).isoformat(),
        }
        self._save()

    def remove_exported(self, doc_id: str) -> dict | None:
        """Remove a document from the exported tracker. Returns the entry if found."""
        entry = self.data.get("exported", {}).pop(doc_id, None)
        if entry is not None:
            self._save()
        return entry

    def get_exported_entry(self, doc_id: str) -> dict | None:
        """Get the tracker entry for a document."""
        return self.data.get("exported", {}).get(doc_id)

    def get_all_exported_ids(self) -> set[str]:
        """Get all exported document IDs."""
        return set(self.data.get("exported", {}).keys())

    # --- Highlight tracking ---

    def get_all_exported(self) -> dict:
        """Get the full exported documents dict."""
        return dict(self.data.get("exported", {}))

    def get_synced_highlights(self, doc_id: str) -> set[str]:
        """Get the set of highlight texts already synced for a document."""
        return set(
            self.data.get("highlights", {}).get(doc_id, {}).get("texts", [])
        )

    def mark_highlight_synced(self, doc_id: str, text: str) -> None:
        """Mark a highlight as synced to Readwise."""
        hl = self.data.setdefault("highlights", {}).setdefault(
            doc_id, {"texts": [], "last_synced": ""}
        )
        if text not in hl["texts"]:
            hl["texts"].append(text)
        hl["last_synced"] = datetime.now(tz=UTC).isoformat()
        self._save()
