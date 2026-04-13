# Readwise to reMarkable Sync

Sync documents from [Readwise Reader](https://readwise.io/read) to a [reMarkable](https://remarkable.com/) tablet, and extract highlights back to Readwise.

## Features

### Document Sync (Readwise --> reMarkable)

- **Seen filtering** -- Skips documents you have already opened in Readwise Reader (`first_opened_at` is set), so only unread items land on the tablet.
- **Tag-optional mode** -- Set `tag = *` in config to sync all documents from your configured locations, not just those with a specific tag.
- **Multi-strategy content fetch** -- Three fallback strategies to get document content: (1) HTML from list response, (2) HTML via `withHtmlContent` API call, (3) raw source download from S3. PDFs are detected and handled natively at each stage.
- **HTML-to-EPUB conversion** -- Articles and web content are converted to EPUB with embedded images (rate-limited fetcher, magic-byte format detection) for a clean reading experience on reMarkable.
- **Native PDF passthrough** -- PDF documents are downloaded and uploaded directly without conversion.
- **Auto-cleanup** -- Documents that leave your sync locations (archived, deleted, moved) are automatically removed from the reMarkable tablet on the next sync cycle. The tracker keeps the remote filename so cleanup is precise.
- **Delete support** -- The uploader can remove specific documents from reMarkable via `rmapi rm`, enabling the cleanup lifecycle.
- **JSON tracker** -- Tracks exported documents in a structured JSON file (`exported_documents.json`) with doc ID, title, remote filename, and timestamp. Migrates automatically from the upstream text-based tracker format.

### Highlight Sync (reMarkable --> Readwise)

- **GlyphRange extraction** -- For EPUB highlights, reads the native `GlyphRange` objects from reMarkable `.rm` scene files, which contain the highlighted text directly.
- **Contiguous fragment merge** -- GlyphRange objects are per-line fragments. Fragments with adjacent start offsets (within a 20-character gap tolerance) are merged into complete highlight passages. In testing, 34 raw fragments merged correctly into 7 passages.
- **Cross-page merge** -- Highlights that span a page break are detected and merged: if the last highlight on page N does not end with sentence-ending punctuation and the first on page N+1 starts lowercase, they are joined.
- **Stroke-based PDF highlights** -- For PDF documents, highlight strokes are mapped from reMarkable canvas coordinates to PDF text coordinates using PyMuPDF, then matched to underlying words.
- **Deduplication** -- Already-synced highlights are tracked per document and skipped on subsequent runs.
- **Readwise v2 API push** -- Highlights are pushed to Readwise with title, page number, and color metadata.

### Configuration

Edit `config.cfg`:

```ini
[readwise]
access_token = your_readwise_access_token_here

[remarkable]
rmapi_path = rmapi
folder = Readwise

[sync]
locations = new,later,shortlist
tag = remarkable

[highlights]
enabled = false
```

- `locations` -- Comma-separated Readwise Reader locations to sync from.
- `tag` -- Tag to filter by. Set to `*` to sync all documents regardless of tags.
- `highlights.enabled` -- Set to `true` to enable reverse highlight sync.

## Requirements

- Python 3.11+
- [rmapi](https://github.com/juruen/rmapi) -- CLI tool for reMarkable cloud API
- Readwise Reader access token

## Installation

```bash
git clone https://github.com/donmerendolo/readwise-to-remarkable
cd readwise-to-remarkable
pip install -r requirements.txt
```

## Usage

```bash
# Sync documents from Readwise to reMarkable
python sync.py

# Sync highlights from reMarkable back to Readwise
python highlights.py
```

### Docker

The project includes a Dockerfile for running as a container (e.g., on Unraid):

```bash
docker build -t readwise-remarkable .
docker run -v /path/to/config:/app/config readwise-remarkable
```

## Architecture

```
sync.py            -- Main orchestrator: fetch, convert, upload, cleanup
readwise_api.py    -- Readwise Reader API client with rate limiting + backoff
converter.py       -- HTML-to-EPUB conversion with embedded images
uploader.py        -- reMarkable upload/delete via rmapi
tracker.py         -- JSON-based export and highlight tracking
highlights.py      -- Highlight extraction and Readwise sync
config.py          -- Configuration management
```

## Credits

This is a fork of [donmerendolo/readwise-to-remarkable](https://github.com/donmerendolo/readwise-to-remarkable) with added features for seen filtering, auto-cleanup, and highlight sync.

Libraries and tools used:

- [rmscene](https://github.com/ricklupton/rmscene) -- Parse reMarkable `.rm` scene files
- [remarks](https://github.com/lucasrla/remarks) / [rmc](https://github.com/ricklupton/rmc) -- Prior art for reMarkable annotation extraction
- [PyMuPDF](https://pymupdf.readthedocs.io/) -- PDF text extraction for stroke-based highlights
- [ebooklib](https://github.com/aerkalov/ebooklib) -- EPUB generation
- [Readwise API](https://readwise.io/api_deets) -- Document list and highlight sync
- [rmapi](https://github.com/juruen/rmapi) -- reMarkable cloud CLI
