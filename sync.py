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
            # Get documents from Readwise
            documents = self.readwise.get_documents(
                self.config.locations,
                self.config.tag,
            )
            print(f"Found {len(documents)} documents with tag '{self.config.tag}'")

            if not documents:
                print("No documents to sync.")
                return

            # Filter out already exported documents
            new_documents = [
                doc for doc in documents if not self.tracker.is_exported(doc["id"])
            ]
            print(f"Found {len(new_documents)} new documents to sync")

            if not new_documents:
                print("All documents have already been exported.")
                return

            # Process each document
            for i, doc in enumerate(new_documents, 1):
                print(f"\nProcessing document {i}/{len(new_documents)}: {doc['title']}")

                try:
                    self._process_document(doc)
                except Exception as e:
                    print(f"Failed to process document '{doc['title']}': {e}")
                    continue

            print(f"\nSync completed! Processed {len(new_documents)} documents.")

        except Exception as e:
            print(f"Sync failed: {e}")
            raise
        finally:
            # Clean up temp files
            self._cleanup_temp_files()

    def _process_document(self, doc: dict) -> None:
        """Process a single document."""
        doc_id = doc["id"]
        title = doc["title"]
        author = doc.get("author", "Unknown")
        category = doc.get("category", "article")

        clean_title = DocumentConverter.clean_filename(title)

        if category == "pdf":
            # Download PDF from source URL
            source_url = doc.get("source_url")
            if not source_url:
                print(f"No source URL for PDF: {title}")
                return

            pdf_path = self.temp_dir / f"{clean_title}.pdf"
            try:
                print(f"Downloading PDF: {title}")
                response = requests.get(source_url, timeout=30, stream=True)
                response.raise_for_status()

                with Path.open(pdf_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

                # Upload PDF directly to reMarkable
                upload_success = self.uploader.upload_file(pdf_path)
                if upload_success:
                    self.tracker.mark_exported(doc_id, title)
                    print(f"Successfully synced PDF: {title}")
                else:
                    print(f"Failed to upload PDF: {title}")
                return

            except Exception as e:
                print(f"Failed to download PDF {title}: {e}")
                return

        # Get HTML content
        html_content = doc.get("html_content", "")
        if not html_content:
            print(f"No HTML content available for: {title}")
            return

        # Convert to EPUB
        epub_path = self.temp_dir / f"{clean_title}.epub"
        try:
            self.converter.html_to_epub(html_content, title, author, epub_path)
        except Exception as e:
            print(f"Failed to convert to EPUB: {e}")
            return

        # Upload to reMarkable
        upload_success = self.uploader.upload_file(epub_path)
        if upload_success:
            self.tracker.mark_exported(doc_id, title)
            print(f"Successfully synced: {title}")
        else:
            print(f"Failed to upload: {title}")
            return  # Don't mark as exported if upload failed

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
