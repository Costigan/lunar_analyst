index: false

# RAG Corpus

This directory is the canonical git-managed source corpus for assistant RAG indexing.

## Purpose

- Keep retrieval source documents versioned with code.
- Make RAG behavior reproducible across branches/releases.
- Provide a single reviewed location for implementation knowledge.

## Supported file types

- `.md`
- `.txt`
- `.csv`
- `.pdf`
- `.html` / `.htm`
- `.json`

## Front matter (recommended for `.md` / `.txt`)

Use top-of-file `key: value` metadata lines. Parsing stops at the first non-matching line; the rest is body text.

Minimum recommended keys:

- `title: ...`
- `channel: procedural|domain|mixed`

Common optional keys:

- `index: true|false` (default `true`; set `false` to keep an operational doc out of retrieval)
- `source_kind: inline|file|url`
- `source_ref: <relative-path-or-url>`
- `chunking: section|paragraph|sliding_window|single`
- `chunk_size_chars: 2200`
- `chunk_overlap_chars: 200`
- `tags: a, b, c`

### Sample: Inline Markdown Document

```md
title: GeoTIFF Generation Procedure
channel: procedural
index: true
tags: geotiff, workflow, raster_transform
chunking: section

# Goal
Create a new GeoTIFF output in the active scenario.

# Recommended Flow
1. Validate input raster paths.
2. Run `raster.transform` with `output_relative_path`.
3. Verify output registration in Scenario Explorer.
```

### Sample: Metadata Descriptor For External File

```md
title: Lunar Source Book Thermal Chapter
channel: domain
index: true
source_kind: file
source_ref: references/lunar_source_book_thermal.pdf
chunking: sliding_window
chunk_size_chars: 2400
chunk_overlap_chars: 250
tags: thermal, temperature, lunar

This body text is optional for `source_kind: file`.
The ingester reads content from `source_ref` when allowed by config.
```

### Sample: Operational Doc Excluded From Retrieval

```md
title: Corpus Maintenance Notes
index: false

# Internal Notes
These instructions are for maintainers and are not indexed for retrieval.
```

## Optional first-line directive (`.md` / `.txt`)

If line 1 is exactly:

`RAG_CHUNKING: single`

the ingester will keep the whole file as one chunk.

This is also recognized when written as front matter:

`rag_chunking: single`

## Notes

- Avoid secrets and credentials in this folder.
- Prefer small, focused documents with stable headings.
- When content changes, re-run RAG ingest/refresh.
- URL ingestion is static-only (no JS rendering/crawling) and disabled unless enabled in config.
