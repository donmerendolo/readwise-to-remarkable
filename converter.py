#!/usr/bin/env python3
"""Document converter with rate-limited image fetching."""

import re
import time
from datetime import UTC, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from ebooklib import epub


class RateLimitedImageFetcher:
    """Rate-limited image fetcher to avoid overwhelming servers."""

    def __init__(self) -> None:
        # 1 second between image requests to be respectful to servers
        self.min_interval = 1.0
        self.last_request_time = 0
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "Mozilla/5.0 (compatible; ReadwiseRemarkableSync/1.0)"},
        )

    def _rate_limit(self) -> None:
        """Implement rate limiting for image requests."""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time

        if time_since_last < self.min_interval:
            sleep_time = self.min_interval - time_since_last
            time.sleep(sleep_time)

        self.last_request_time = time.time()

    def fetch_image(self, url: str, timeout: int = 15) -> bytes | None:
        """Fetch an image with rate limiting and error handling."""
        max_retries = 3
        base_delay = 2

        for attempt in range(max_retries):
            self._rate_limit()

            try:
                response = self.session.get(url, timeout=timeout, stream=True)

                if response.status_code == 429:  # Rate limited
                    retry_after = int(
                        response.headers.get("Retry-After", base_delay * (2**attempt)),
                    )
                    print(
                        f"Image server rate limited. Waiting {retry_after} seconds...",
                    )
                    time.sleep(retry_after)
                    continue

                if (
                    response.status_code == 403
                ):  # Forbidden - often means hotlink protection
                    print(f"Access forbidden for image: {url}")
                    return None

                response.raise_for_status()

                # Read content with size limit
                content = b""
                for chunk in response.iter_content(chunk_size=8192):
                    content += chunk

                return content

            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    print(
                        f"Failed to fetch image after {max_retries} attempts: {url} - {e}",
                    )
                    return None

                delay = base_delay * (2**attempt)
                print(f"Image fetch failed (attempt {attempt + 1}): {url} - {e}")
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)

        return None


class DocumentConverter:
    """Converts documents to different formats."""

    def __init__(self) -> None:
        self.image_fetcher = RateLimitedImageFetcher()

    @staticmethod
    def clean_filename(title: str) -> str:
        """Clean a title to create a valid filename."""
        # Remove or replace invalid characters
        cleaned = re.sub(r'[<>:"/\\|?*]', "", title)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        return cleaned or "Untitled"

    def html_to_epub(
        self,
        html_content: str,
        title: str,
        author: str | None = None,
        output_path: Path | None = None,
    ) -> Path:
        """Convert HTML content to EPUB format."""
        if output_path is None:
            clean_title = self.clean_filename(title)
            output_path = Path(f"{clean_title}.epub")

        # Create EPUB book
        book = epub.EpubBook()

        # Set metadata
        book.set_identifier(f"readwise_{datetime.now(tz=UTC).isoformat()}")
        book.set_title(title)

        if author and author != "Unknown":
            book.add_author(author)

        # Create chapter
        chapter = epub.EpubHtml(title=title, file_name="content.xhtml")

        # Build content - let ebooklib handle the structure
        if html_content:
            # Parse HTML properly
            soup = BeautifulSoup(html_content, "html.parser")

            # Download and embed images
            img_counter = 0
            total_images = len(soup.find_all("img"))

            if total_images > 0:
                print(f"Processing {total_images} images...")

            for img in soup.find_all("img"):
                src = img.get("src")
                if src and src.startswith(("http://", "https://")):
                    content = self.image_fetcher.fetch_image(src)
                    if content:
                        try:
                            # Determine file extension from content type or URL
                            ext = self._determine_image_extension(src, content)

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
                        except Exception as e:
                            print(f"Failed to process image {src}: {e}")
                    else:
                        print(f"Failed to fetch image: {src}")

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

    def _determine_image_extension(self, url: str, content: bytes) -> str:
        """Determine the appropriate file extension for an image."""
        # Try to detect from content (magic bytes)
        if content.startswith(b"\x89PNG"):
            return "png"
        elif content.startswith(b"\xff\xd8\xff"):
            return "jpg"
        elif content.startswith(b"GIF"):
            return "gif"
        elif content.startswith(b"RIFF") and b"WEBP" in content[:12]:
            return "webp"
        elif content.startswith(b"<svg") or b"<svg" in content[:100]:
            return "svg"

        # Fallback to URL extension
        if "." in url:
            url_ext = url.split(".")[-1].split("?")[0].lower()
            if url_ext in ["png", "gif", "webp", "svg", "jpeg", "bmp"]:
                return "jpg" if url_ext == "jpeg" else url_ext

        # Default fallback
        return "jpg"
