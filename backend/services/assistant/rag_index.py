from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from backend.core.config import (
    load_app_config as core_load_app_config,
    resolve_config_path as core_resolve_config_path,
    resolve_config_relative_path as core_resolve_config_relative_path,
)

logger = logging.getLogger(__name__)

DEFAULT_CORPUS_RELATIVE_ROOT = "docs/rag_corpus"
DEFAULT_GLOBAL_DB_RELATIVE_PATH = ".assistant/rag/global_rag.db"
SINGLE_CHUNK_DIRECTIVE = "RAG_CHUNKING: single"
DEFAULT_ALLOWED_EXTENSIONS = ("md", "txt", "csv", "pdf", "html", "htm", "json")
DEFAULT_CHANNEL = "mixed"
DEFAULT_MAX_URL_BYTES = 5 * 1024 * 1024

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "with",
}

_FRONT_MATTER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_\-]*:\s+.*$")


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    relative_path: str
    content: str
    score: float
    snippet: str
    title: str = ""
    channel: str = DEFAULT_CHANNEL

    def to_reference(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "chunk_id": self.chunk_id,
            "score": float(self.score),
            "snippet": self.snippet,
            "title": self.title,
            "channel": self.channel,
        }


@dataclass(frozen=True)
class RetrievalBundle:
    chunks: list[RetrievedChunk]
    context_text: str

    def references(self) -> list[dict[str, object]]:
        return [item.to_reference() for item in self.chunks]


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = str(data or "").strip()
        if text:
            self._parts.append(text)

    def text(self) -> str:
        return "\n".join(self._parts).strip()


class RagIndex:
    def __init__(
        self,
        *,
        db_path: Path,
        corpus_root: Path,
        allowed_extensions: list[str] | None = None,
        allow_external_file_sources: bool = True,
        allow_url_fetch: bool = False,
        external_source_allow_roots: list[Path] | None = None,
        max_url_bytes: int = DEFAULT_MAX_URL_BYTES,
    ) -> None:
        self._db_path = db_path.resolve()
        self._corpus_root = corpus_root.resolve()
        exts = allowed_extensions or list(DEFAULT_ALLOWED_EXTENSIONS)
        normalized = [str(item).strip().lstrip(".").lower() for item in exts]
        self._allowed_extensions = tuple(item for item in normalized if item)
        if not self._allowed_extensions:
            self._allowed_extensions = DEFAULT_ALLOWED_EXTENSIONS
        self._allow_external_file_sources = bool(allow_external_file_sources)
        self._allow_url_fetch = bool(allow_url_fetch)
        roots = list(external_source_allow_roots or [self._corpus_root])
        self._external_source_allow_roots = tuple(Path(root).expanduser().resolve() for root in roots)
        self._max_url_bytes = max(1024, int(max_url_bytes))
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def corpus_root(self) -> Path:
        return self._corpus_root

    @property
    def allowed_extensions(self) -> tuple[str, ...]:
        return self._allowed_extensions

    def refresh(
        self,
        *,
        relative_root: str | None = None,
        rebuild: bool = False,
        extensions: list[str] | None = None,
        respect_directives: bool = True,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, int]:
        root = self._resolve_root(relative_root)
        allowed_exts = self._normalize_extensions(extensions)
        files = self._list_corpus_files(root=root, extensions=allowed_exts)
        reserved_sources = self._collect_reserved_source_files(files)
        indexed_before = self._indexed_paths()
        scanned = 0
        added = 0
        updated = 0
        skipped = 0
        current_paths: set[str] = set()

        if rebuild:
            with self._connect() as conn:
                conn.execute("DELETE FROM chunk_terms")
                conn.execute("DELETE FROM chunks")
                conn.execute("DELETE FROM documents")
                conn.commit()
            indexed_before.clear()

        for idx, file_path in enumerate(files):
            scanned += 1
            rel = file_path.relative_to(self._corpus_root).as_posix()
            if file_path in reserved_sources:
                skipped += 1
                if progress is not None:
                    progress(
                        {
                            "stage": "scan",
                            "message": f"Skipped reserved source {idx + 1}/{len(files)}",
                            "scanned": scanned,
                            "added": added,
                            "updated": updated,
                            "skipped": skipped,
                            "deleted": 0,
                        }
                    )
                continue

            file_stat = file_path.stat()
            mtime_ns = int(file_stat.st_mtime_ns)
            size_bytes = int(file_stat.st_size)
            key = indexed_before.get(rel)
            if key and key["mtime_ns"] == mtime_ns and key["size_bytes"] == size_bytes:
                skipped += 1
                current_paths.add(rel)
                if progress is not None:
                    progress(
                        {
                            "stage": "scan",
                            "message": f"Scanned {idx + 1}/{len(files)}",
                            "scanned": scanned,
                            "added": added,
                            "updated": updated,
                            "skipped": skipped,
                            "deleted": 0,
                        }
                    )
                continue

            digest = _sha256_file(file_path)
            parsed = self._prepare_document(file_path, respect_directives=respect_directives)
            if parsed is None:
                skipped += 1
                if progress is not None:
                    progress(
                        {
                            "stage": "scan",
                            "message": f"Skipped non-indexed doc {idx + 1}/{len(files)}",
                            "scanned": scanned,
                            "added": added,
                            "updated": updated,
                            "skipped": skipped,
                            "deleted": 0,
                        }
                    )
                continue
            action = "updated" if key else "added"
            self._upsert_document(
                relative_path=rel,
                mtime_ns=mtime_ns,
                size_bytes=size_bytes,
                sha256=digest,
                chunks=parsed["chunks"],
                chunking_mode=parsed["chunking_mode"],
                title=parsed["title"],
                channel=parsed["channel"],
                source_kind=parsed["source_kind"],
                source_ref=parsed["source_ref"],
                tags=parsed["tags"],
                authority=parsed["authority"],
                metadata=parsed["metadata"],
            )
            if key:
                updated += 1
            else:
                added += 1
            current_paths.add(rel)
            logger.info(
                "assistant rag index document action=%s relative_path=%s chunks=%s channel=%s source_kind=%s",
                action,
                rel,
                len(parsed["chunks"]),
                str(parsed["channel"]),
                str(parsed["source_kind"]),
            )
            if progress is not None:
                progress(
                    {
                        "stage": "index",
                        "message": f"Indexed {idx + 1}/{len(files)}",
                        "scanned": scanned,
                        "added": added,
                        "updated": updated,
                        "skipped": skipped,
                        "deleted": 0,
                        }
                    )

        stale = [path for path in indexed_before if path not in current_paths]
        deleted = 0
        if stale:
            with self._connect() as conn:
                for rel in stale:
                    conn.execute("DELETE FROM chunk_terms WHERE relative_path = ?", (rel,))
                    conn.execute("DELETE FROM chunks WHERE relative_path = ?", (rel,))
                    conn.execute("DELETE FROM documents WHERE relative_path = ?", (rel,))
                    deleted += 1
                    logger.info("assistant rag index document action=deleted relative_path=%s", rel)
                conn.commit()

        stats = {
            "scanned": scanned,
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "deleted": deleted,
        }
        logger.info(
            "assistant rag index refresh scanned=%s added=%s updated=%s skipped=%s deleted=%s",
            scanned,
            added,
            updated,
            skipped,
            deleted,
        )
        return stats

    def _collect_reserved_source_files(self, files: list[Path]) -> set[Path]:
        descriptors = {path.resolve() for path in files}
        reserved: set[Path] = set()
        for descriptor in files:
            suffix = descriptor.suffix.lower()
            if suffix not in {".md", ".txt"}:
                continue
            try:
                text = descriptor.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            front_matter, _body = _parse_front_matter(text)
            source_kind = str(front_matter.get("source_kind", "")).strip().lower()
            source_ref = str(front_matter.get("source_ref", "")).strip()
            if source_kind != "file" or not source_ref:
                continue
            ref_path = Path(source_ref).expanduser()
            if ref_path.is_absolute():
                candidate = ref_path.resolve()
            else:
                candidate = (descriptor.parent / ref_path).resolve()
            if candidate == descriptor.resolve():
                continue
            if candidate not in descriptors:
                continue
            reserved.add(candidate)
        return reserved

    def retrieve(
        self,
        *,
        query: str,
        top_k: int = 6,
        max_context_chars: int = 6000,
        channel: str | None = None,
        max_query_terms: int = 24,
        fallback_query_mode: str = "and_then_or",
    ) -> RetrievalBundle:
        normalized = _normalize_query(query)
        if not normalized:
            return RetrievalBundle(chunks=[], context_text="")
        terms = _extract_query_terms(normalized, max_query_terms=max_query_terms)
        if not terms:
            return RetrievalBundle(chunks=[], context_text="")
        requested_channel = _normalize_channel(channel)

        with self._connect() as conn:
            rows = self._query_rows(
                conn,
                terms=terms,
                operator="AND",
                limit=max(1, int(top_k) * 4),
                channel=requested_channel,
            )
            if (not rows) and str(fallback_query_mode or "").strip().lower() == "and_then_or":
                rows = self._query_rows(
                    conn,
                    terms=terms,
                    operator="OR",
                    limit=max(1, int(top_k) * 4),
                    channel=requested_channel,
                )
                logger.info(
                    "assistant rag retrieval fallback query_mode=OR channel=%s terms=%s",
                    requested_channel,
                    len(terms),
                )

        chunks: list[RetrievedChunk] = []
        for row in rows:
            chunk_id = str(row[0])
            relative_path = self._sanitize_relative_path(str(row[1]))
            content = str(row[2])
            rank = float(row[3]) if row[3] is not None else 0.0
            score = -rank
            snippet = _snippet(content, limit=240)
            chunks.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    relative_path=relative_path,
                    content=content,
                    score=score,
                    snippet=snippet,
                    title=str(row[4] or "").strip(),
                    channel=_normalize_channel(str(row[5] or "")),
                )
            )
        selected = chunks[: max(1, int(top_k))]
        context_text, selected_len = _build_context_text(selected, max_context_chars=max_context_chars)
        logger.info(
            "assistant rag retrieval hits=%s selected=%s channel=%s context_chars=%s",
            len(rows),
            selected_len,
            requested_channel,
            len(context_text or ""),
        )
        return RetrievalBundle(chunks=selected[:selected_len], context_text=context_text)

    def _query_rows(
        self,
        conn: sqlite3.Connection,
        *,
        terms: list[str],
        operator: str,
        limit: int,
        channel: str,
    ) -> list[tuple[Any, ...]]:
        fts_query = _build_fts_query(terms, operator=operator)
        if not fts_query:
            return []
        where_channel = ""
        args: list[Any] = [fts_query]
        if channel in {"procedural", "domain"}:
            where_channel = " AND d.channel IN (?, 'mixed') "
            args.append(channel)
        args.append(limit)
        return conn.execute(
            f"""
            SELECT
                c.chunk_id,
                c.relative_path,
                c.content_text,
                bm25(chunk_terms) AS rank,
                d.title,
                d.channel
            FROM chunk_terms
            JOIN chunks AS c ON c.chunk_id = chunk_terms.chunk_id
            JOIN documents AS d ON d.relative_path = c.relative_path
            WHERE chunk_terms MATCH ?
            {where_channel}
            ORDER BY rank
            LIMIT ?
            """,
            tuple(args),
        ).fetchall()

    def _prepare_document(self, descriptor_path: Path, *, respect_directives: bool) -> dict[str, Any] | None:
        suffix = descriptor_path.suffix.lower()
        raw_text = _read_path_text(descriptor_path)
        front_matter, body_text = _parse_front_matter(raw_text)
        if not _parse_boolish(front_matter.get("index"), default=True):
            return None

        directive_single = False
        directive_value = str(front_matter.get("rag_chunking", "")).strip().lower()
        if respect_directives and directive_value == "single":
            directive_single = True
        if respect_directives and suffix in {".md", ".txt"}:
            lines = body_text.splitlines()
            if lines and lines[0].strip() == SINGLE_CHUNK_DIRECTIVE:
                directive_single = True
                body_text = "\n".join(lines[1:]).strip()

        title = str(front_matter.get("title", "")).strip() or descriptor_path.relative_to(self._corpus_root).as_posix()
        channel = _normalize_channel(front_matter.get("channel", DEFAULT_CHANNEL))
        source_kind = str(front_matter.get("source_kind", "inline")).strip().lower() or "inline"
        source_ref = str(front_matter.get("source_ref", "")).strip() or None
        authority = str(front_matter.get("authority", "")).strip() or None
        tags = _normalize_tags(front_matter.get("tags"))

        chunking = str(front_matter.get("chunking", "")).strip().lower() or ""
        if directive_single and chunking != "single":
            chunking = "single"
        chunk_size = _int_or_none(front_matter.get("chunk_size_chars"))
        chunk_overlap = _int_or_none(front_matter.get("chunk_overlap_chars"))

        content_text = body_text
        if source_kind == "file":
            content_text = self._load_external_file_text(descriptor_path=descriptor_path, source_ref=source_ref)
        elif source_kind == "url":
            content_text = self._load_external_url_text(source_ref=source_ref)

        chunks = _chunk_document_text(
            content_text,
            suffix=suffix,
            chunking=chunking,
            chunk_size_chars=chunk_size,
            chunk_overlap_chars=chunk_overlap,
        )
        chunking_mode = chunking or ("single" if directive_single else "structured")

        reserved = {
            "title",
            "channel",
            "source_kind",
            "source_ref",
            "tags",
            "authority",
            "chunking",
            "chunk_size_chars",
            "chunk_overlap_chars",
            "rag_chunking",
        }
        metadata = {k: v for k, v in front_matter.items() if k not in reserved}

        return {
            "title": title,
            "channel": channel,
            "source_kind": source_kind,
            "source_ref": source_ref,
            "tags": tags,
            "authority": authority,
            "metadata": metadata,
            "chunks": chunks,
            "chunking_mode": chunking_mode,
        }

    def _load_external_file_text(self, *, descriptor_path: Path, source_ref: str | None) -> str:
        if not self._allow_external_file_sources:
            raise PermissionError("External file ingestion is disabled")
        if not source_ref:
            return ""
        ref_path = Path(str(source_ref)).expanduser()
        if ref_path.is_absolute():
            candidate = ref_path.resolve()
        else:
            candidate = (descriptor_path.parent / ref_path).resolve()
        if not any(_is_path_within_root(candidate, root) for root in self._external_source_allow_roots):
            raise PermissionError(f"External source path escapes allow roots: {candidate}")
        if not candidate.exists() or not candidate.is_file():
            return ""
        return _extract_text_from_path(candidate)

    def _load_external_url_text(self, *, source_ref: str | None) -> str:
        if not self._allow_url_fetch:
            logger.info("RAG URL ingestion skipped because allow_url_fetch=false")
            return ""
        if not source_ref:
            return ""
        url = str(source_ref).strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise PermissionError(f"Unsupported URL scheme for RAG source: {url}")
        content, content_type = _fetch_url_content(url, max_bytes=self._max_url_bytes)
        return _extract_text_from_bytes(content, suffix=Path(parsed.path).suffix.lower(), content_type=content_type)

    def _upsert_document(
        self,
        *,
        relative_path: str,
        mtime_ns: int,
        size_bytes: int,
        sha256: str,
        chunks: list[str],
        chunking_mode: str,
        title: str,
        channel: str,
        source_kind: str,
        source_ref: str | None,
        tags: list[str],
        authority: str | None,
        metadata: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM chunk_terms WHERE relative_path = ?", (relative_path,))
            conn.execute("DELETE FROM chunks WHERE relative_path = ?", (relative_path,))
            conn.execute(
                """
                INSERT INTO documents(
                    relative_path, mtime_ns, size_bytes, sha256, chunking_mode,
                    title, channel, source_kind, source_ref, tags_json, authority, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    mtime_ns = excluded.mtime_ns,
                    size_bytes = excluded.size_bytes,
                    sha256 = excluded.sha256,
                    chunking_mode = excluded.chunking_mode,
                    title = excluded.title,
                    channel = excluded.channel,
                    source_kind = excluded.source_kind,
                    source_ref = excluded.source_ref,
                    tags_json = excluded.tags_json,
                    authority = excluded.authority,
                    metadata_json = excluded.metadata_json
                """,
                (
                    relative_path,
                    int(mtime_ns),
                    int(size_bytes),
                    sha256,
                    str(chunking_mode or "structured"),
                    str(title or ""),
                    _normalize_channel(channel),
                    str(source_kind or "inline"),
                    source_ref,
                    json.dumps(tags, ensure_ascii=True),
                    authority,
                    json.dumps(metadata, ensure_ascii=True),
                ),
            )
            for ordinal, chunk in enumerate(chunks):
                chunk_id = f"{relative_path}:{ordinal}"
                conn.execute(
                    """
                    INSERT INTO chunks(chunk_id, relative_path, ordinal, content_text, section, token_estimate, keywords_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        relative_path,
                        ordinal,
                        chunk,
                        _infer_section(chunk),
                        _estimate_tokens(chunk),
                        "[]",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO chunk_terms(chunk_id, relative_path, content_text)
                    VALUES (?, ?, ?)
                    """,
                    (chunk_id, relative_path, chunk),
                )
            conn.commit()

    def _indexed_paths(self) -> dict[str, dict[str, int]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT relative_path, mtime_ns, size_bytes FROM documents",
            ).fetchall()
        return {
            str(row[0]): {"mtime_ns": int(row[1] or 0), "size_bytes": int(row[2] or 0)}
            for row in rows
        }

    def _resolve_root(self, relative_root: str | None) -> Path:
        rel = str(relative_root or "").strip().replace("\\", "/").strip("/")
        if not rel:
            return self._corpus_root
        candidate = (self._corpus_root / rel).resolve()
        if self._corpus_root != candidate and self._corpus_root not in candidate.parents:
            raise PermissionError(f"Path escapes corpus root: {candidate}")
        return candidate

    def _list_corpus_files(self, *, root: Path, extensions: tuple[str, ...]) -> list[Path]:
        if not root.exists() or not root.is_dir():
            return []
        files: list[Path] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            suffix = path.suffix.lower().lstrip(".")
            if suffix not in extensions:
                continue
            resolved = path.resolve()
            if self._corpus_root != resolved and self._corpus_root not in resolved.parents:
                continue
            files.append(resolved)
        return files

    def _sanitize_relative_path(self, relative_path: str) -> str:
        rel = str(relative_path or "").strip().replace("\\", "/").lstrip("/")
        parts = [part for part in rel.split("/") if part and part != "."]
        if any(part == ".." for part in parts):
            raise PermissionError(f"Invalid stored relative path: {relative_path}")
        normalized = "/".join(parts)
        resolved = (self._corpus_root / normalized).resolve()
        if self._corpus_root != resolved and self._corpus_root not in resolved.parents:
            raise PermissionError(f"Stored path escapes corpus root: {relative_path}")
        return normalized

    def _normalize_extensions(self, extensions: list[str] | None) -> tuple[str, ...]:
        if not extensions:
            return self._allowed_extensions
        normalized = [str(item).strip().lstrip(".").lower() for item in extensions]
        values = tuple(item for item in normalized if item)
        return values or self._allowed_extensions

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    relative_path TEXT PRIMARY KEY,
                    mtime_ns INTEGER NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    chunking_mode TEXT NOT NULL DEFAULT 'structured',
                    title TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL DEFAULT 'mixed',
                    source_kind TEXT NOT NULL DEFAULT 'inline',
                    source_ref TEXT,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    authority TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    relative_path TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    content_text TEXT NOT NULL,
                    section TEXT,
                    token_estimate INTEGER NOT NULL DEFAULT 0,
                    keywords_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunk_terms USING fts5(
                    chunk_id UNINDEXED,
                    relative_path UNINDEXED,
                    content_text
                )
                """
            )
            self._ensure_column(conn, "documents", "title", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "documents", "channel", "TEXT NOT NULL DEFAULT 'mixed'")
            self._ensure_column(conn, "documents", "source_kind", "TEXT NOT NULL DEFAULT 'inline'")
            self._ensure_column(conn, "documents", "source_ref", "TEXT")
            self._ensure_column(conn, "documents", "tags_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(conn, "documents", "authority", "TEXT")
            self._ensure_column(conn, "documents", "metadata_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "chunks", "section", "TEXT")
            self._ensure_column(conn, "chunks", "token_estimate", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "chunks", "keywords_json", "TEXT NOT NULL DEFAULT '[]'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_channel ON documents(channel)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_title ON documents(title)")
            conn.commit()

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        columns = {str(row[1]) for row in rows}
        if column in columns:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn


def create_default_rag_index(
    *,
    db_relative_path: str | None = None,
    corpus_relative_root: str | None = None,
    allowed_extensions: list[str] | None = None,
    workspace_root: str | Path | None = None,
    repo_root: str | Path | None = None,
    allow_external_file_sources: bool = True,
    allow_url_fetch: bool = False,
    external_source_allow_roots: list[str] | None = None,
) -> RagIndex:
    app_cfg = _load_app_config()
    backend_cfg = app_cfg.get("backend", {}) if isinstance(app_cfg, dict) else {}
    workspace_root_path = (
        Path(workspace_root).expanduser().resolve()
        if workspace_root is not None
        else _resolve_workspace_root(backend_cfg if isinstance(backend_cfg, dict) else {})
    )
    repo_root_path = (
        Path(repo_root).expanduser().resolve()
        if repo_root is not None
        else _resolve_repo_root()
    )

    db_rel = str(db_relative_path or DEFAULT_GLOBAL_DB_RELATIVE_PATH).strip()
    db_path = (workspace_root_path / db_rel).resolve()
    if workspace_root_path != db_path and workspace_root_path not in db_path.parents:
        raise PermissionError(f"RAG DB path escapes workspace root: {db_path}")

    corpus_rel = str(corpus_relative_root or DEFAULT_CORPUS_RELATIVE_ROOT).strip()
    corpus_root = (repo_root_path / corpus_rel).resolve()
    if repo_root_path != corpus_root and repo_root_path not in corpus_root.parents:
        raise PermissionError(f"RAG corpus path escapes repository root: {corpus_root}")
    corpus_root.mkdir(parents=True, exist_ok=True)

    external_roots: list[Path] = [corpus_root]
    for raw in external_source_allow_roots or []:
        value = str(raw or "").strip()
        if not value:
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = (repo_root_path / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if repo_root_path != candidate and repo_root_path not in candidate.parents:
            raise PermissionError(f"RAG external source root escapes repository root: {candidate}")
        if candidate not in external_roots:
            external_roots.append(candidate)

    return RagIndex(
        db_path=db_path,
        corpus_root=corpus_root,
        allowed_extensions=allowed_extensions,
        allow_external_file_sources=allow_external_file_sources,
        allow_url_fetch=allow_url_fetch,
        external_source_allow_roots=external_roots,
    )


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_app_config() -> dict[str, Any]:
    try:
        return core_load_app_config(strict=True)
    except Exception:
        return {}


def _resolve_workspace_root(backend_cfg: dict[str, Any]) -> Path:
    raw = backend_cfg.get("workspace_root")
    if isinstance(raw, str) and raw.strip():
        cfg_path = core_resolve_config_path()
        return core_resolve_config_relative_path(raw, config_path=cfg_path).resolve()
    return (_resolve_repo_root() / "scenarios").resolve()


def _normalize_query(query: str) -> str:
    text = str(query or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text)


def _extract_query_terms(normalized_query: str, *, max_query_terms: int = 24) -> list[str]:
    tokens = re.findall(r"[a-z0-9_:.\/-]+", normalized_query)
    cleaned: list[str] = []
    for token in tokens:
        value = token.strip()
        if len(value) <= 1:
            continue
        if value in _STOPWORDS:
            continue
        cleaned.append(value)
        if len(cleaned) >= max(4, int(max_query_terms)):
            break
    return cleaned


def _build_fts_query(terms: list[str], *, operator: str = "AND") -> str:
    cleaned = [term for term in terms if term]
    if not cleaned:
        return ""
    op = " OR " if str(operator or "").strip().upper() == "OR" else " AND "
    return op.join(f'"{term}"' for term in cleaned)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _read_path_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return _extract_pdf_text_file(path)
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_text_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf_text_file(path)
    if suffix in {".html", ".htm"}:
        return _extract_html_text(path.read_text(encoding="utf-8", errors="ignore"))
    if suffix == ".json":
        return _extract_json_text(path.read_text(encoding="utf-8", errors="ignore"))
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_text_from_bytes(payload: bytes, *, suffix: str, content_type: str) -> str:
    media = str(content_type or "").split(";", 1)[0].strip().lower()
    guessed = str(suffix or "").lower()
    if guessed == ".pdf" or media == "application/pdf":
        return _extract_pdf_text_bytes(payload)
    if guessed in {".html", ".htm"} or media == "text/html":
        return _extract_html_text(payload.decode("utf-8", errors="ignore"))
    if guessed == ".json" or media == "application/json":
        return _extract_json_text(payload.decode("utf-8", errors="ignore"))
    return payload.decode("utf-8", errors="ignore")


def _extract_pdf_text_file(path: Path) -> str:
    return _extract_pdf_text_bytes(path.read_bytes())


def _extract_pdf_text_bytes(payload: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(BytesIO(payload))
        parts: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts).strip()
    except Exception:
        pass
    try:
        from PyPDF2 import PdfReader  # type: ignore

        reader = PdfReader(BytesIO(payload))
        parts = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts).strip()
    except Exception:
        return ""


def _extract_html_text(html: str) -> str:
    parser = _HtmlTextExtractor()
    parser.feed(str(html or ""))
    return parser.text()


def _extract_json_text(text: str) -> str:
    try:
        parsed = json.loads(str(text or "").strip() or "{}")
    except Exception:
        return str(text or "")
    return json.dumps(parsed, indent=2, ensure_ascii=True)


def _fetch_url_content(url: str, *, max_bytes: int) -> tuple[bytes, str]:
    req = Request(url, headers={"User-Agent": "LunarAnalyst-RAG/1.0"})
    with urlopen(req, timeout=15) as response:  # noqa: S310
        content_type = str(response.headers.get("Content-Type", "")).strip().lower()
        media = content_type.split(";", 1)[0].strip()
        if media not in {"", "text/html", "text/plain", "text/markdown", "application/json", "application/pdf"}:
            raise PermissionError(f"Unsupported URL content type: {media}")
        payload = response.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise ValueError("URL content exceeds max allowed size")
        return payload, content_type


def _parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    lines = str(text or "").splitlines()
    metadata: dict[str, Any] = {}
    body_start = 0
    for idx, line in enumerate(lines):
        if not _FRONT_MATTER_RE.match(line):
            body_start = idx
            break
        key, value = line.split(":", 1)
        metadata[key.strip().lower()] = value.strip()
    else:
        body_start = len(lines)
    body = "\n".join(lines[body_start:]).lstrip("\n")
    return metadata, body


def _normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        items = [part.strip() for part in str(value).split(",")]
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _normalize_channel(value: Any) -> str:
    normalized = str(value or DEFAULT_CHANNEL).strip().lower()
    if normalized in {"procedural", "domain", "mixed"}:
        return normalized
    return DEFAULT_CHANNEL


def _chunk_document_text(
    text: str,
    *,
    suffix: str,
    chunking: str,
    chunk_size_chars: int | None,
    chunk_overlap_chars: int | None,
) -> list[str]:
    payload = str(text or "").strip()
    if not payload:
        return []
    mode = str(chunking or "").strip().lower()
    if mode == "single":
        return [payload]
    if mode == "sliding_window":
        size = max(256, int(chunk_size_chars or 2000))
        overlap = max(0, int(chunk_overlap_chars or 200))
        return _chunk_sliding_window(payload, size=size, overlap=overlap)
    if suffix == ".csv":
        return _chunk_csv_text(payload)
    if mode == "paragraph":
        return _chunk_text(payload, max_chars=max(400, int(chunk_size_chars or 1800)))
    if mode == "section" and suffix == ".md":
        return _chunk_markdown(payload, max_chars=max(600, int(chunk_size_chars or 2200)))
    if suffix == ".md":
        return _chunk_markdown(payload, max_chars=max(600, int(chunk_size_chars or 2200)))
    return _chunk_text(payload, max_chars=max(400, int(chunk_size_chars or 1800)))


def _chunk_text(text: str, *, max_chars: int = 1800) -> list[str]:
    paras = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    chunks: list[str] = []
    current = ""
    for para in paras:
        if not current:
            current = para
            continue
        candidate = f"{current}\n\n{para}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        chunks.append(current)
        current = para
    if current:
        chunks.append(current)
    return chunks


def _chunk_markdown(text: str, *, max_chars: int = 2200) -> list[str]:
    lines = text.splitlines()
    blocks: list[str] = []
    current_lines: list[str] = []

    def _flush() -> None:
        nonlocal current_lines
        block = "\n".join(current_lines).strip()
        if block:
            blocks.append(block)
        current_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# ") or stripped.startswith("## ") or stripped.startswith("### "):
            _flush()
            current_lines = [line]
            continue
        if "|" in line and current_lines and "|" in current_lines[-1]:
            current_lines.append(line)
            continue
        if not stripped and current_lines:
            current_lines.append("")
            continue
        current_lines.append(line)
    _flush()

    chunks: list[str] = []
    current = ""
    for block in blocks:
        if not current:
            current = block
            continue
        candidate = f"{current}\n\n{block}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        chunks.append(current)
        current = block
    if current:
        chunks.append(current)
    return chunks


def _chunk_csv_text(text: str, *, target_rows: int = 30) -> list[str]:
    reader = csv.reader(StringIO(text))
    rows = list(reader)
    if not rows:
        return []
    header = rows[0]
    body = rows[1:]
    chunks: list[str] = []
    for start in range(0, len(body), target_rows):
        group = body[start : start + target_rows]
        lines = [",".join(header)] + [",".join(row) for row in group]
        chunks.append("\n".join(lines).strip())
    if not body:
        chunks.append(",".join(header))
    return chunks


def _chunk_sliding_window(text: str, *, size: int, overlap: int) -> list[str]:
    if size <= 0:
        return [text]
    step = max(1, size - overlap)
    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(length, start + size)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        start += step
    return chunks


def _snippet(content: str, *, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", content).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _estimate_tokens(text: str) -> int:
    words = re.findall(r"\S+", str(text or ""))
    return max(1, int(round(len(words) * 1.3))) if words else 0


def _infer_section(chunk: str) -> str | None:
    for line in str(chunk or "").splitlines()[:6]:
        value = line.strip()
        if value.startswith("#"):
            return value.lstrip("#").strip() or None
    return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _parse_boolish(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _build_context_text(chunks: list[RetrievedChunk], *, max_context_chars: int) -> tuple[str, int]:
    context_lines: list[str] = []
    used_chars = 0
    for idx, item in enumerate(chunks):
        tag = f"[src#{idx + 1} path={item.relative_path} chunk={item.chunk_id}]"
        block = f"{tag}\n{item.content.strip()}\n"
        if used_chars + len(block) > max(512, int(max_context_chars)):
            break
        context_lines.append(block)
        used_chars += len(block)
    return "\n".join(context_lines).strip(), len(context_lines)


def _is_path_within_root(path: Path, root: Path) -> bool:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents
