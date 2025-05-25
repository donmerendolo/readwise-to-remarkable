#!/usr/bin/env python3
"""Readwise to reMarkable Sync Tool.

Syncs documents from Readwise Reader that are tagged "remarkable"
and in locations "new", "later", or "shortlist" to reMarkable tablet.

Converts HTML articles to EPUB format using ebooklib.
Uploads files using rmapi.
"""

import configparser
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from ebooklib import epub


class Config:
    """Configuration management for the sync tool."""

    def __init__(self, config_path: Path | None = None) -> None:
        if config_path is None:
            config_path = Path(__file__).parent / "config.cfg"

        self.config_path = config_path
        self.config = configparser.ConfigParser()
        self.load_config()

    def load_config(self) -> None:
        """Load configuration from config.cfg file."""
        if not self.config_path.exists():
            self.create_default_config()

        self.config.read(self.config_path)

    def create_default_config(self) -> None:
        """Create a default configuration file."""
        default_config = """[readwise]
access_token = your_readwise_access_token_here

[remarkable]
rmapi_path = rmapi
folder = Readwise

[sync]
locations = new,later,shortlist
tag = remarkable
"""
        with Path.open(self.config_path, "w") as f:
            f.write(default_config)

        print(f"Created default config at {self.config_path}")
        print("Please edit the config file with your settings and run again.")
        sys.exit(1)

    @property
    def readwise_token(self) -> str:
        return self.config.get("readwise", "access_token")

    @property
    def rmapi_path(self) -> str:
        return self.config.get("remarkable", "rmapi_path", fallback="rmapi")

    @property
    def remarkable_folder(self) -> str:
        return self.config.get("remarkable", "folder", fallback="Readwise")

    @property
    def locations(self) -> list[str]:
        locations_str = self.config.get(
            "sync",
            "locations",
            fallback="new,later,shortlist",
        )
        return [loc.strip() for loc in locations_str.split(",")]

    @property
    def tag(self) -> str:
        return self.config.get("sync", "tag", fallback="remarkable")


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


class ReadwiseAPI:
    """Readwise Reader API client with rate limiting."""

    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self.base_url = "https://readwise.io/api/v3"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Token {access_token}",
                "Content-Type": "application/json",
            },
        )

        # Rate limiting: 20 requests per minute
        self.min_request_interval = 3.1  # Slightly over 3 seconds to be safe
        self.last_request_time = 0

    def _rate_limit(self) -> None:
        """Implement rate limiting with exponential backoff."""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time

        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last
            print(f"Rate limiting: sleeping for {sleep_time:.1f} seconds")
            time.sleep(sleep_time)

        self.last_request_time = time.time()

    def _make_request(self, method: str, url: str, **kwargs: dict) -> requests.Response:
        """Make a rate-limited request with exponential backoff on errors."""
        max_retries = 5
        base_delay = 1

        for attempt in range(max_retries):
            self._rate_limit()

            try:
                response = self.session.request(method, url, **kwargs)

                if response.status_code == 429:  # Rate limited
                    retry_after = int(
                        response.headers.get("Retry-After", base_delay * (2**attempt)),
                    )
                    print(f"Rate limited. Waiting {retry_after} seconds...")
                    time.sleep(retry_after)
                    continue

                response.raise_for_status()
                return response

            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise

                delay = base_delay * (2**attempt)
                print(f"Request failed (attempt {attempt + 1}): {e}")
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)

        msg = "Max retries exceeded"
        raise Exception(msg)

    def get_documents(self, locations: list[str], tag: str) -> list[dict]:
        """Fetch documents with specified locations and tag."""
        all_documents = []

        for location in locations:
            print(f"Fetching documents from location: {location}")
            page_cursor = None

            while True:
                params = {"location": location, "withHtmlContent": "true"}

                if page_cursor:
                    params["pageCursor"] = page_cursor

                response = self._make_request(
                    "GET",
                    f"{self.base_url}/list/",
                    params=params,
                )
                data = response.json()

                # Filter documents by tag
                for doc in data.get("results", []):
                    doc_tags = doc.get("tags", {})
                    if isinstance(doc_tags, dict):
                        # Convert dict format to list for easier checking
                        tag_list = list(doc_tags.keys())
                    else:
                        tag_list = doc_tags if isinstance(doc_tags, list) else []

                    if tag in tag_list:
                        all_documents.append(doc)

                page_cursor = data.get("nextPageCursor")
                if not page_cursor:
                    break

        return all_documents

    def get_document_content(self, doc_id: str) -> str:
        """Get the HTML content of a specific document."""
        params = {"id": doc_id, "withHtmlContent": "true"}
        response = self._make_request("GET", f"{self.base_url}/list/", params=params)
        data = response.json()

        if data.get("results"):
            return data["results"][0].get("html_content", "")

        return ""


class DocumentConverter:
    """Converts documents to different formats."""

    @staticmethod
    def clean_filename(title: str) -> str:
        """Clean a title to create a valid filename."""
        # Remove or replace invalid characters
        cleaned = re.sub(r'[<>:"/\\|?*]', "", title)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        return cleaned or "Untitled"

    @staticmethod
    def html_to_epub(
        html_content: str,
        title: str,
        author: str | None = None,
        output_path: Path | None = None,
    ) -> Path:
        """Convert HTML content to EPUB format."""
        if output_path is None:
            clean_title = DocumentConverter.clean_filename(title)
            output_path = Path(f"{clean_title}.epub")

        # Create EPUB book
        book = epub.EpubBook()

        # Set metadata
        book.set_identifier(f"readwise_{datetime.now(tz=UTC).isoformat()}")
        book.set_title(title)
        book.set_language("en")

        if author and author != "Unknown":
            book.add_author(author)

        # Create chapter
        chapter = epub.EpubHtml(title=title, file_name="content.xhtml", lang="en")

        # Build content - let ebooklib handle the structure
        if html_content:
            # Parse HTML properly
            soup = BeautifulSoup(html_content, "html.parser")

            # Download and embed images
            img_counter = 0
            for img in soup.find_all("img"):
                src = img.get("src")
                if src and src.startswith(("http://", "https://")):
                    try:
                        time.sleep(0.5)  # Rate limit
                        response = requests.get(src, timeout=10, stream=True)

                        content = response.content

                        # Determine file extension from content type or URL
                        content_type = response.headers.get("content-type", "").lower()
                        if "png" in content_type:
                            ext = "png"
                        elif "gif" in content_type:
                            ext = "gif"
                        elif "webp" in content_type:
                            ext = "webp"
                        elif "svg" in content_type:
                            ext = "svg"
                        else:
                            # Default to jpg or try to extract from URL
                            ext = "jpg"
                            if "." in src:
                                url_ext = src.split(".")[-1].split("?")[0].lower()
                                if url_ext in [
                                    "png",
                                    "gif",
                                    "webp",
                                    "svg",
                                    "jpeg",
                                    "bmp",
                                ]:
                                    ext = "jpg" if url_ext == "jpeg" else url_ext

                        img_name = f"img_{img_counter}.{ext}"
                        epub_img = epub.EpubImage(
                            uid=f"image{img_counter}",
                            file_name=img_name,
                            content=content,
                        )
                        book.add_item(epub_img)

                        img["src"] = img_name
                        img["style"] = "max-width: 100%; height: auto;"
                        img_counter += 1
                    except Exception:
                        pass  # Keep original if download fails

            # Get body content or use as-is
            body = soup.find("body")
            html_content = str(body) if body else str(soup)

            # Add title and author at the top
            content_parts = [f"<h1>{title}</h1>"]
            if author and author != "Unknown":
                content_parts.append(f"<p><em>by {author}</em></p>")
                content_parts.append("<hr/>")
            content_parts.append(html_content)

            chapter.set_content("".join(content_parts))
        else:
            chapter.set_content(f"<h1>{title}</h1><p>No content available</p>")

        # Add chapter to book
        book.add_item(chapter)

        # Simple spine - just the chapter, no navigation
        book.spine = [chapter]

        # Write EPUB file
        epub.write_epub(str(output_path), book, {})

        return output_path


class RemarkableUploader:
    """Handles uploading files to reMarkable using rmapi."""

    def __init__(self, rmapi_path: str, folder: str) -> None:
        self.rmapi_path = rmapi_path
        self.folder = folder
        self._ensure_rmapi_available()
        self._ensure_folder_exists()

    def _ensure_rmapi_available(self) -> None:
        """Check if rmapi is available."""
        try:
            subprocess.run(
                [self.rmapi_path, "version"],
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            msg = (
                "rmapi not found or not working. Please install rmapi "
                "and ensure it's in PATH or set the correct path in config."
            )
            raise RuntimeError(
                msg,
            )

    def _ensure_folder_exists(self) -> None:
        """Ensure the target folder exists on reMarkable."""
        try:
            # Check if folder exists
            result = subprocess.run(
                [self.rmapi_path, "find", self.folder],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0 or not result.stdout.strip():
                # Create folder
                print(f"Creating folder '{self.folder}' on reMarkable...")
                subprocess.run([self.rmapi_path, "mkdir", self.folder], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Warning: Could not ensure folder exists: {e}")

    def upload_file(self, file_path: Path) -> bool:
        """Upload a file to reMarkable."""
        try:
            print(f"Uploading {file_path.name} to reMarkable...")

            # Change to temp directory to ensure relative path upload
            original_cwd = Path.cwd()
            try:
                os.chdir(file_path.parent)
                cmd = [self.rmapi_path, "put", file_path.name, self.folder]
                subprocess.run(cmd, capture_output=True, text=True, check=True)
            finally:
                os.chdir(original_cwd)

            print(f"Successfully uploaded {file_path.name}")
            return True

        except subprocess.CalledProcessError as e:
            print(f"Failed to upload {file_path.name}: {e}")
            if e.stderr:
                print(f"Error output: {e.stderr}")
            return False


class ReadwiseRemarkableSync:
    """Main synchronization orchestrator."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.config = Config(config_path)
        self.tracker = ExportTracker()
        self.readwise = ReadwiseAPI(self.config.readwise_token)
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
            DocumentConverter.html_to_epub(html_content, title, author, epub_path)
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
