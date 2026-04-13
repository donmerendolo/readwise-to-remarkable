#!/usr/bin/env python3
"""Main synchronization orchestrator for Readwise to reMarkable sync."""

import sys
from pathlib import Path

import requests

from config import Config
from converter import DocumentConverter
from readwise_api import ReadwiseAPI
from tracker import ExportTracker
from uploader import RemarkableUploader


class ReadwiseRemarkableSync:
    """Main synchronization orchestrator."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.config = Config(config_path)
        self.tracker = ExportTracker()
        self.readwise = ReadwiseAPI(self.config.readwise_token)
        self.converter = DocumentConverter()
        self.uploader = RemarkableUploader(
            self.config.rmapi_path,
            self.config.remarkable_folder,
        )
        self.temp_dir = Path(__file__).parent / "temp"
        self.temp_dir.mkdir(exist_ok=True)

    def sync(self) -> None:
        """Main synchronization process."""
        print("Starting Readwise to reMarkable sync...")
        print(
            f"Looking for documents tagged '{self.config.tag}' "
            f"in locations: {', '.join(self.config.locations)}",
        )

        try:
            # Get documents from Readwise (only from configured locations)
            documents = self.readwise.get_documents(
                self.config.locations,
                self.config.tag,
            )
            print(f"Found {len(documents)} documents with tag '{self.config.tag}'")

            # Collect all active doc IDs for cleanup later
            active_doc_ids = {doc["id"] for doc in documents}

            # Filter out already exported documents
            new_documents = [
                doc for doc in documents if not self.tracker.is_exported(doc["id"])
            ]
            print(f"Found {len(new_documents)} new documents to sync")

            # Process new documents
            if new_documents:
                for i, doc in enumerate(new_documents, 1):
                    print(
                        f"\nProcessing document {i}/{len(new_documents)}: {doc['title']}"
                    )
                    try:
                        self._process_document(doc)
                    except Exception as e:
                        print(f"Failed to process document '{doc['title']}': {e}")
                        continue

                print(f"\nSync completed! Processed {len(new_documents)} documents.")
            else:
                print("All documents have already been exported.")

            # Cleanup: remove docs from reMarkable that are no longer active
            self._cleanup_removed_documents(active_doc_ids)

        except Exception as e:
            print(f"Sync failed: {e}")
            raise
        finally:
            self._cleanup_temp_files()

    def _cleanup_removed_documents(self, active_doc_ids: set[str]) -> None:
        """Remove documents from reMarkable that are no longer in sync locations.

        Any previously-synced document that is no longer present in the configured
        locations (moved to archive, seen, deleted, etc.) gets removed from reMarkable.
        """
        exported_ids = self.tracker.get_all_exported_ids()
        removed_ids = exported_ids - active_doc_ids

        if not removed_ids:
            return

        print(f"\nFound {len(removed_ids)} documents to clean up from reMarkable...")

        for doc_id in removed_ids:
            entry = self.tracker.get_exported_entry(doc_id)
            if not entry:
                continue

            title = entry.get("title", "Unknown")
            remote_name = entry.get("remote_name", "")

            if not remote_name:
                # Old tracker entries without remote_name — try to find by title
                print(
                    f"Skipping cleanup for '{title}' — no remote filename tracked. "
                    "Will be cleaned up after next re-sync."
                )
                # Remove from tracker anyway so it can be re-synced with proper tracking
                self.tracker.remove_exported(doc_id)
                continue

            print(f"Removing '{title}' from reMarkable (no longer in sync locations)")
            if self.uploader.delete_file(remote_name):
                self.tracker.remove_exported(doc_id)
                print(f"Cleaned up: {title}")
            else:
                print(f"Failed to remove '{title}' — will retry next cycle")

    def _process_document(self, doc: dict) -> None:
        """Process a single document."""
        doc_id = doc["id"]
        title = doc["title"]
        author = doc.get("author", "Unknown")
        category = doc.get("category", "article")

        clean_title = DocumentConverter.clean_filename(title)

        if category == "pdf":
            # Download PDF from source URL or raw source
            source_url = doc.get("source_url")
            if not source_url or source_url.startswith("mailto:"):
                print(f"Trying raw source URL for PDF: {title}")
                source_url = self.readwise.get_document_raw_source_url(doc_id)
            if not source_url:
                print(f"No source URL for PDF: {title}")
                return

            pdf_path = self.temp_dir / f"{clean_title}.pdf"
            try:
                print(f"Downloading PDF: {title}")
                headers = {}
                if "readwise.io" in source_url:
                    headers["Authorization"] = f"Token {self.config.readwise_token}"
                response = requests.get(
                    source_url, timeout=60, stream=True, headers=headers
                )
                response.raise_for_status()

                with Path.open(pdf_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

                remote_name = f"{clean_title}.pdf"
                upload_success = self.uploader.upload_file(pdf_path)
                if upload_success:
                    self.tracker.mark_exported(doc_id, title, remote_name)
                    print(f"Successfully synced PDF: {title}")
                else:
                    print(f"Failed to upload PDF: {title}")
                return

            except Exception as e:
                print(f"Failed to download PDF {title}: {e}")
                return

        # Strategy 1: Check if HTML content was already in the list response
        html_content = doc.get("html_content", "")

        # Strategy 2: Fetch HTML content via API with withHtmlContent=true
        if not html_content:
            print(f"Fetching content for: {title}")
            html_content = self.readwise.get_document_content(doc_id)

        # Strategy 3: Download raw source from S3 and use as HTML
        if not html_content:
            print(f"Trying raw source for: {title}")
            raw_url = self.readwise.get_document_raw_source_url(doc_id)
            if raw_url:
                try:
                    raw_headers = {}
                    if "readwise.io" in raw_url:
                        raw_headers["Authorization"] = f"Token {self.config.readwise_token}"
                    resp = requests.get(raw_url, timeout=60, headers=raw_headers)
                    resp.raise_for_status()
                    content_type = resp.headers.get("Content-Type", "")
                    if "pdf" in content_type:
                        pdf_path = self.temp_dir / f"{clean_title}.pdf"
                        with Path.open(pdf_path, "wb") as f:
                            f.write(resp.content)
                        remote_name = f"{clean_title}.pdf"
                        upload_success = self.uploader.upload_file(pdf_path)
                        if upload_success:
                            self.tracker.mark_exported(doc_id, title, remote_name)
                            print(f"Successfully synced (as PDF): {title}")
                        else:
                            print(f"Failed to upload PDF: {title}")
                        return
                    else:
                        html_content = resp.text
                except Exception as e:
                    print(f"Failed to fetch raw source: {e}")

        if not html_content:
            print(f"No content available for: {title} (category: {category})")
            return

        # Convert to EPUB
        epub_path = self.temp_dir / f"{clean_title}.epub"
        try:
            self.converter.html_to_epub(html_content, title, author, epub_path)
        except Exception as e:
            print(f"Failed to convert to EPUB: {e}")
            return

        # Upload to reMarkable
        remote_name = f"{clean_title}.epub"
        upload_success = self.uploader.upload_file(epub_path)
        if upload_success:
            self.tracker.mark_exported(doc_id, title, remote_name)
            print(f"Successfully synced: {title}")
        else:
            print(f"Failed to upload: {title}")

    def _cleanup_temp_files(self) -> None:
        """Clean up temporary files."""
        try:
            for file_path in self.temp_dir.glob("*.epub"):
                file_path.unlink()
            for file_path in self.temp_dir.glob("*.pdf"):
                file_path.unlink()
        except Exception as e:
            print(f"Warning: Could not clean up temp files: {e}")


def main() -> int:
    """Main entry point."""
    try:
        sync = ReadwiseRemarkableSync()
        sync.sync()
    except KeyboardInterrupt:
        print("\nSync interrupted by user.")
    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
