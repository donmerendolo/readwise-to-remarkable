#!/usr/bin/env python3
"""Extract highlights from reMarkable and sync back to Readwise."""

import json
import os
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import fitz  # PyMuPDF
import requests
from rmscene import read_tree

from config import Config
from tracker import ExportTracker


class HighlightExtractor:
    """Extract highlights from reMarkable document downloads."""

    # reMarkable canvas dimensions (used for coordinate scaling)
    RM_WIDTH = 1404
    RM_HEIGHT = 1872

    @staticmethod
    def extract_from_zip(zip_path: Path) -> list[dict]:
        """Extract highlights from a downloaded reMarkable zip.

        Returns list of dicts: {text, page, color}
        """
        highlights = []

        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmpdir)

            tmpdir_path = Path(tmpdir)

            # Find the .content file to get page list and file type
            content_files = list(tmpdir_path.glob("*.content"))
            if not content_files:
                return []

            content_file = content_files[0]
            doc_uuid = content_file.stem

            with content_file.open(encoding="utf-8") as f:
                content = json.load(f)

            file_type = content.get("fileType", "")
            pages = content.get("pages", [])

            # Find source document
            source_pdf = tmpdir_path / f"{doc_uuid}.pdf"
            source_epub = tmpdir_path / f"{doc_uuid}.epub"

            # For EPUBs, reMarkable also generates a .pdf for rendering
            # Both the .epub and .pdf may exist
            has_pdf = source_pdf.exists()

            # Process each page's .rm file
            for page_idx, page_uuid in enumerate(pages):
                rm_file = tmpdir_path / doc_uuid / f"{page_uuid}.rm"
                if not rm_file.exists():
                    continue

                page_highlights = HighlightExtractor._extract_page_highlights(
                    rm_file, page_idx, source_pdf if has_pdf else None
                )
                highlights.extend(page_highlights)

        # Merge highlights that span page breaks: if the last highlight on
        # page N doesn't end with sentence-ending punctuation and the first
        # highlight on page N+1 starts with a lowercase letter, they're the
        # same passage continued across a page boundary.
        return HighlightExtractor._merge_cross_page(highlights)

    @staticmethod
    def _merge_cross_page(highlights: list[dict]) -> list[dict]:
        """Merge highlights that span page breaks."""
        if len(highlights) <= 1:
            return highlights

        merged = [highlights[0]]
        for h in highlights[1:]:
            prev = merged[-1]
            prev_text = prev["text"].rstrip()
            curr_text = h["text"]

            # Check if this continues from the previous page
            pages_adjacent = h["page"] == prev["page"] + 1
            prev_ends_mid = prev_text and prev_text[-1] not in ".!?;\u201d"
            curr_starts_lower = curr_text and (curr_text[0].islower() or curr_text[0] in "\"'\u201c")

            if pages_adjacent and prev_ends_mid and curr_starts_lower:
                # Merge: combine text, keep earlier page number
                merged[-1] = {
                    "text": prev["text"] + " " + curr_text,
                    "page": prev["page"],
                    "color": prev["color"],
                }
            else:
                merged.append(h)

        return merged

    @staticmethod
    def _extract_page_highlights(
        rm_file: Path, page_idx: int, source_pdf: Path | None
    ) -> list[dict]:
        """Extract highlights from a single page's .rm file."""
        highlights = []

        try:
            with rm_file.open("rb") as f:
                tree = read_tree(f)
        except Exception as e:
            print(f"  Warning: Could not parse {rm_file.name}: {e}")
            return []

        # Strategy 1: GlyphRange (EPUB highlights — text embedded directly)
        glyph_highlights = HighlightExtractor._extract_glyph_highlights(
            tree, page_idx
        )
        if glyph_highlights:
            return glyph_highlights

        # Strategy 2: Stroke-based highlights on PDFs
        if source_pdf and source_pdf.exists():
            stroke_highlights = HighlightExtractor._extract_stroke_highlights(
                tree, page_idx, source_pdf
            )
            if stroke_highlights:
                return stroke_highlights

        return highlights

    @staticmethod
    def _extract_glyph_highlights(tree, page_idx: int) -> list[dict]:
        """Extract text from GlyphRange items (EPUB highlights).

        GlyphRange objects are per-line fragments. Contiguous fragments
        (where start offsets are adjacent) belong to the same highlight
        passage and are merged. Gaps in start offsets indicate separate
        highlight strokes.
        """
        # Collect raw fragments with position info
        fragments = []
        for item in tree.walk():
            cls_name = type(item).__name__
            if cls_name == "GlyphRange":
                text = getattr(item, "text", None)
                if text and text.strip():
                    fragments.append({
                        "text": text.strip(),
                        "start": getattr(item, "start", None),
                        "length": getattr(item, "length", None),
                        "color": _map_rm_color(str(getattr(item, "color", "yellow"))),
                    })

        if not fragments:
            return []

        # Sort by start offset (if available)
        has_offsets = all(f["start"] is not None for f in fragments)
        if has_offsets:
            fragments.sort(key=lambda f: f["start"])

        # Merge contiguous fragments into passages
        # Two fragments are contiguous if the gap between them is small
        # (allowing for whitespace/newlines between lines)
        MAX_GAP = 20  # characters — covers newlines and small whitespace gaps
        highlights = []
        current_texts = [fragments[0]["text"]]
        current_color = fragments[0]["color"]
        current_end = (fragments[0]["start"] or 0) + (fragments[0]["length"] or 0)

        for frag in fragments[1:]:
            frag_start = frag["start"] or 0
            gap = frag_start - current_end if has_offsets else 0

            if has_offsets and gap > MAX_GAP:
                # Gap too large — this is a new highlight passage
                highlights.append({
                    "text": " ".join(current_texts),
                    "page": page_idx + 1,
                    "color": current_color,
                })
                current_texts = [frag["text"]]
                current_color = frag["color"]
            else:
                current_texts.append(frag["text"])

            current_end = frag_start + (frag["length"] or 0)

        # Don't forget the last passage
        highlights.append({
            "text": " ".join(current_texts),
            "page": page_idx + 1,
            "color": current_color,
        })

        return highlights

    @staticmethod
    def _extract_stroke_highlights(
        tree, page_idx: int, source_pdf: Path
    ) -> list[dict]:
        """Extract highlights by mapping strokes to PDF text."""
        # Collect highlight stroke bounding boxes
        highlight_rects = []

        for item in tree.walk():
            cls_name = type(item).__name__
            if cls_name == "Line":
                tool = getattr(item, "tool", None)
                tool_name = str(tool) if tool else ""

                # Check if this is a highlighter stroke
                if "highlight" in tool_name.lower():
                    points = getattr(item, "points", [])
                    if points:
                        xs = [p.x for p in points if hasattr(p, "x")]
                        ys = [p.y for p in points if hasattr(p, "y")]
                        if xs and ys:
                            highlight_rects.append((
                                min(xs), min(ys), max(xs), max(ys)
                            ))

        if not highlight_rects:
            return []

        # Map stroke coordinates to PDF text
        try:
            pdf = fitz.open(str(source_pdf))
            if page_idx >= len(pdf):
                pdf.close()
                return []

            page = pdf[page_idx]
            page_rect = page.rect
            words = page.get_text("words", sort=True)
            pdf.close()

            if not words:
                return []

            # Scale factors: reMarkable canvas → PDF coordinates
            sx = page_rect.width / HighlightExtractor.RM_WIDTH
            sy = page_rect.height / HighlightExtractor.RM_HEIGHT

            highlights = []
            for rx0, ry0, rx1, ry1 in highlight_rects:
                # Convert reMarkable coords to PDF coords
                pdf_rect = fitz.Rect(rx0 * sx, ry0 * sy, rx1 * sx, ry1 * sy)

                # Find words that intersect the highlight rectangle
                matched_words = []
                for w in words:
                    word_rect = fitz.Rect(w[0], w[1], w[2], w[3])
                    if pdf_rect.intersects(word_rect):
                        matched_words.append(w[4])

                if matched_words:
                    text = " ".join(matched_words)
                    highlights.append({
                        "text": text.strip(),
                        "page": page_idx + 1,
                        "color": "yellow",
                    })

            # Merge adjacent highlights on the same page
            return _merge_adjacent_highlights(highlights)

        except Exception as e:
            print(f"  Warning: Could not extract PDF text for page {page_idx}: {e}")
            return []


def _merge_adjacent_highlights(highlights: list[dict]) -> list[dict]:
    """Merge highlights that are likely from the same passage."""
    if len(highlights) <= 1:
        return highlights

    merged = [highlights[0]]
    for h in highlights[1:]:
        prev = merged[-1]
        if prev["page"] == h["page"] and prev["color"] == h["color"]:
            # Merge text from consecutive highlight strokes
            merged[-1] = {
                "text": prev["text"] + " " + h["text"],
                "page": prev["page"],
                "color": prev["color"],
            }
        else:
            merged.append(h)

    return merged


def _map_rm_color(color_str: str) -> str:
    """Map reMarkable highlight color to Readwise color."""
    color_lower = color_str.lower()
    if "yellow" in color_lower:
        return "yellow"
    if "blue" in color_lower:
        return "blue"
    if "pink" in color_lower or "red" in color_lower:
        return "pink"
    if "orange" in color_lower:
        return "orange"
    if "green" in color_lower:
        return "green"
    return "yellow"


class HighlightSync:
    """Sync highlights from reMarkable back to Readwise."""

    READWISE_HIGHLIGHTS_URL = "https://readwise.io/api/v2/highlights/"

    def __init__(self, config_path: Path | None = None) -> None:
        self.config = Config(config_path)
        self.tracker = ExportTracker()
        self.rmapi_path = self.config.rmapi_path
        self.remarkable_folder = self.config.remarkable_folder
        self.temp_dir = Path(__file__).parent / "temp"
        self.temp_dir.mkdir(exist_ok=True)

    def sync(self) -> None:
        """Check for new highlights on reMarkable and push to Readwise."""
        if not self.config.highlight_sync_enabled:
            return

        print("\n--- Highlight sync (reMarkable → Readwise) ---")

        exported = self.tracker.get_all_exported()
        if not exported:
            print("No tracked documents to check for highlights.")
            return

        for doc_id, entry in exported.items():
            title = entry.get("title", "Unknown")
            remote_name = entry.get("remote_name", "")

            if not remote_name:
                continue

            try:
                self._sync_document_highlights(doc_id, title, remote_name)
            except Exception as e:
                print(f"  Failed to sync highlights for '{title}': {e}")
                continue

    def _sync_document_highlights(
        self, doc_id: str, title: str, remote_name: str
    ) -> None:
        """Download a document from reMarkable, extract highlights, push to Readwise."""
        # rmapi uses document name without file extension
        doc_name = Path(remote_name).stem
        remote_path = f"{self.remarkable_folder}/{doc_name}"

        # Download via rmapi get (produces .rmdoc file)
        try:
            original_cwd = Path.cwd()
            try:
                os.chdir(self.temp_dir)
                result = subprocess.run(
                    [self.rmapi_path, "get", remote_path],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            finally:
                os.chdir(original_cwd)

            if result.returncode != 0:
                # Document may have been deleted from reMarkable
                return

            # rmapi downloads as .rmdoc (which is a zip)
            rmdoc_files = list(self.temp_dir.glob("*.rmdoc"))
            if not rmdoc_files:
                return
            zip_path = rmdoc_files[0]

        except Exception as e:
            print(f"  Could not download '{title}': {e}")
            return

        # Extract highlights
        try:
            highlights = HighlightExtractor.extract_from_zip(zip_path)
        except Exception as e:
            print(f"  Could not extract highlights from '{title}': {e}")
            return
        finally:
            # Clean up downloaded files
            for zf in self.temp_dir.glob("*.rmdoc"):
                zf.unlink(missing_ok=True)

        if not highlights:
            return

        # Filter out already-synced highlights
        synced_texts = self.tracker.get_synced_highlights(doc_id)
        new_highlights = [
            h for h in highlights if h["text"] not in synced_texts
        ]

        if not new_highlights:
            return

        print(f"  Found {len(new_highlights)} new highlights in '{title}'")

        # Push to Readwise
        success = self._push_to_readwise(title, new_highlights)
        if success:
            for h in new_highlights:
                self.tracker.mark_highlight_synced(doc_id, h["text"])
            print(f"  Synced {len(new_highlights)} highlights for '{title}'")

    def _push_to_readwise(
        self, title: str, highlights: list[dict]
    ) -> bool:
        """Push highlights to Readwise via the v2 highlights API."""
        payload = {
            "highlights": [
                {
                    "text": h["text"],
                    "title": title,
                    "source_type": "remarkable_sync",
                    "category": "articles",
                    "location": h.get("page", 0),
                    "location_type": "page",
                    "color": h.get("color", "yellow"),
                }
                for h in highlights
            ]
        }

        try:
            response = requests.post(
                self.READWISE_HIGHLIGHTS_URL,
                headers={
                    "Authorization": f"Token {self.config.readwise_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"  Failed to push highlights to Readwise: {e}")
            return False


def main() -> int:
    """Main entry point."""
    try:
        sync = HighlightSync()
        sync.sync()
    except KeyboardInterrupt:
        print("\nHighlight sync interrupted.")
    except Exception as e:
        print(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
