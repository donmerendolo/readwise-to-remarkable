#!/usr/bin/env python3
"""Readwise Reader API client with rate limiting."""

import time

import requests


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

        # Rate limiting: 20 requests per minute = 3.1 seconds between requests
        self.min_request_interval = 3.1
        self.last_request_time = 0

    def _rate_limit(self) -> None:
        """Implement rate limiting for Readwise API."""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time

        if time_since_last < self.min_request_interval:
            sleep_time = self.min_request_interval - time_since_last
            time.sleep(sleep_time)

        self.last_request_time = time.time()

    def _make_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make a rate-limited request with exponential backoff on errors."""
        max_retries = 5
        base_delay = 2

        for attempt in range(max_retries):
            self._rate_limit()

            try:
                response = self.session.request(method, url, **kwargs)

                if response.status_code == 429:  # Rate limited
                    retry_after = int(
                        response.headers.get("Retry-After", base_delay * (2**attempt)),
                    )
                    print(
                        f"Readwise API rate limited. Waiting {retry_after} seconds...",
                    )
                    time.sleep(retry_after)
                    continue

                response.raise_for_status()
                return response

            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise

                delay = base_delay * (2**attempt)
                print(f"Readwise API request failed (attempt {attempt + 1}): {e}")
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)

        msg = "Max retries exceeded for Readwise API"
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
