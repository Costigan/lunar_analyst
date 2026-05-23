from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request

from backend.services.repositories.trek_catalog_cache_repository import TrekCatalogCacheRepository
from backend.services.repositories.trek_catalog_cache_repository import TrekCatalogCacheSnapshot


_TREK_SEARCH_URL = "https://trek.nasa.gov/moon/TrekServices/ws/index/polar/searchItems"
_TREK_PROJ = "urn:ogc:def:crs:IAU2000::30120"


@dataclass(frozen=True)
class TrekCatalogSnapshot:
    layers: list[dict[str, Any]]
    fetched_at_utc: str
    cached: bool


class TrekCatalogService:
    def __init__(
        self,
        *,
        ttl_seconds: int = 600,
        http_fetcher: Callable[[], list[dict[str, Any]]] | None = None,
        cache_db_path: Path | None = None,
        persistent_cache: TrekCatalogCacheRepository | None = None,
    ) -> None:
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._http_fetcher = http_fetcher or self._fetch_remote_layers
        if persistent_cache is not None:
            self._persistent_cache = persistent_cache
        elif cache_db_path is not None:
            self._persistent_cache = TrekCatalogCacheRepository(cache_db_path)
        else:
            self._persistent_cache = None
        self._lock = threading.RLock()
        self._cache_layers: list[dict[str, Any]] = []
        self._cache_fetched_at_utc: str = ""
        self._cache_epoch_s: float = 0.0

    def list_layers(self, *, force_refresh: bool = False) -> TrekCatalogSnapshot:
        stale_persistent_snapshot: TrekCatalogCacheSnapshot | None = None
        with self._lock:
            is_fresh = (
                self._cache_layers
                and (time.time() - self._cache_epoch_s) < self._ttl_seconds
            )
            if not force_refresh and is_fresh:
                return TrekCatalogSnapshot(
                    layers=list(self._cache_layers),
                    fetched_at_utc=self._cache_fetched_at_utc,
                    cached=True,
                )

            if not force_refresh and self._persistent_cache is not None:
                persisted_snapshot = self._persistent_cache.load_snapshot(include_expired=False)
                if persisted_snapshot is not None:
                    self._adopt_cache_snapshot(persisted_snapshot)
                    return TrekCatalogSnapshot(
                        layers=list(self._cache_layers),
                        fetched_at_utc=self._cache_fetched_at_utc,
                        cached=True,
                    )
                stale_persistent_snapshot = self._persistent_cache.load_snapshot(include_expired=True)

            try:
                layers = self._http_fetcher()
                now_utc = _utc_now_iso()
                self._cache_layers = list(layers)
                self._cache_fetched_at_utc = now_utc
                self._cache_epoch_s = time.time()
                if self._persistent_cache is not None:
                    self._persistent_cache.save_snapshot(
                        layers=self._cache_layers,
                        fetched_at_utc=now_utc,
                        ttl_seconds=self._ttl_seconds,
                    )
                return TrekCatalogSnapshot(
                    layers=list(self._cache_layers),
                    fetched_at_utc=now_utc,
                    cached=False,
                )
            except Exception:
                if not force_refresh and stale_persistent_snapshot is not None:
                    self._adopt_cache_snapshot(stale_persistent_snapshot)
                    return TrekCatalogSnapshot(
                        layers=list(self._cache_layers),
                        fetched_at_utc=self._cache_fetched_at_utc,
                        cached=True,
                    )
                raise

    def search_layers(self, *, pattern: str, force_refresh: bool = False) -> TrekCatalogSnapshot:
        snapshot = self.list_layers(force_refresh=force_refresh)
        needle = pattern.strip()
        if not needle:
            return snapshot
        parsed = self._parse_search_pattern(needle)
        filtered = [layer for layer in snapshot.layers if self._evaluate_search(layer, parsed)]
        return TrekCatalogSnapshot(
            layers=filtered,
            fetched_at_utc=snapshot.fetched_at_utc,
            cached=snapshot.cached,
        )

    @staticmethod
    def _fetch_remote_layers() -> list[dict[str, Any]]:
        query = parse.urlencode(
            {
                "proj": _TREK_PROJ,
                "start": 0,
                "rows": 1000,
            }
        )
        url = f"{_TREK_SEARCH_URL}?{query}"
        req = request.Request(url=url, method="GET")
        try:
            with request.urlopen(req, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except error.URLError as exc:
            raise RuntimeError(f"Trek catalog request failed: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Trek catalog request failed: {exc}") from exc

        try:
            payload = json.loads(raw)
        except Exception as exc:
            raise RuntimeError(f"Trek catalog returned invalid JSON: {exc}") from exc
        docs = payload.get("response", {}).get("docs", [])
        if not isinstance(docs, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in docs:
            if not isinstance(item, dict):
                continue
            product_label = str(item.get("productLabel", "")).strip()
            if not product_label or product_label.lower() == "tour":
                continue
            row = dict(item)
            service_types = row.get("serviceTypes")
            if isinstance(service_types, list):
                row["serviceTypes"] = [str(value) for value in service_types]
            else:
                row["serviceTypes"] = []
            row["item_UUID"] = str(row.get("item_UUID", "")).strip()
            row["productLabel"] = product_label
            row["title"] = str(row.get("title", "")).strip() or product_label
            row["description"] = str(row.get("description", "")).strip()
            normalized.append(row)
        return normalized

    def _adopt_cache_snapshot(self, snapshot: TrekCatalogCacheSnapshot) -> None:
        self._cache_layers = list(snapshot.layers)
        self._cache_fetched_at_utc = snapshot.fetched_at_utc
        self._cache_epoch_s = _epoch_from_utc_iso(snapshot.fetched_at_utc) or time.time()

    def _parse_search_pattern(self, pattern: str) -> dict[str, Any]:
        normalized = pattern.strip()
        if not normalized:
            return {"type": "match_all"}
        normalized = re.sub(r"\bAND\b", " & ", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bOR\b", " | ", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bNOT\b", " -", normalized, flags=re.IGNORECASE)

        tokens: list[Any] = []
        current = ""
        i = 0
        while i < len(normalized):
            char = normalized[i]
            if char == "(":
                if current.strip():
                    tokens.append(current.strip())
                    current = ""
                depth = 1
                j = i + 1
                while j < len(normalized) and depth > 0:
                    if normalized[j] == "(":
                        depth += 1
                    elif normalized[j] == ")":
                        depth -= 1
                    j += 1
                inner = normalized[i + 1 : j - 1]
                tokens.append(self._parse_search_pattern(inner))
                i = j
                continue
            if char in {"&", "|"}:
                if current.strip():
                    tokens.append(current.strip())
                    current = ""
                tokens.append(char)
                i += 1
                continue
            if char == "-" and (i == 0 or normalized[i - 1] in {" ", "(", "&", "|"}):
                if current.strip():
                    tokens.append(current.strip())
                    current = ""
                tokens.append("-")
                i += 1
                continue
            current += char
            i += 1
        if current.strip():
            tokens.append(current.strip())
        return {"type": "tokens", "tokens": tokens}

    def _evaluate_search(self, layer: dict[str, Any], parsed: dict[str, Any]) -> bool:
        if parsed.get("type") == "match_all":
            return True
        if parsed.get("type") != "tokens":
            return False

        tokens = parsed.get("tokens", [])
        if not isinstance(tokens, list) or not tokens:
            return True

        search_text = " ".join(
            [
                str(layer.get("productLabel", "")),
                str(layer.get("title", "")),
                str(layer.get("description", "")),
                " ".join(str(value) for value in layer.get("serviceTypes", [])),
            ]
        ).lower()

        processed: list[Any] = []
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token == "-":
                if i + 1 < len(tokens):
                    processed.append({"op": "NOT", "operand": tokens[i + 1]})
                    i += 2
                else:
                    i += 1
                continue
            processed.append(token)
            i += 1

        and_groups: list[Any] = []
        current_group: list[Any] = []
        for token in processed:
            if token == "&":
                continue
            if token == "|":
                if current_group:
                    and_groups.append(current_group)
                    current_group = []
                and_groups.append("|")
                continue
            current_group.append(token)
        if current_group:
            and_groups.append(current_group)

        or_results: list[bool] = []
        for group in and_groups:
            if group == "|":
                continue
            and_result = True
            for term in group:
                if isinstance(term, dict) and term.get("op") == "NOT":
                    operand = term.get("operand")
                    if isinstance(operand, dict):
                        match = self._evaluate_search(layer, operand)
                    else:
                        match = str(operand).strip().lower() in search_text
                    and_result = and_result and (not match)
                elif isinstance(term, dict):
                    and_result = and_result and self._evaluate_search(layer, term)
                else:
                    and_result = and_result and (str(term).strip().lower() in search_text)
            or_results.append(and_result)

        return any(or_results) if or_results else False


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch_from_utc_iso(value: str) -> float | None:
    try:
        parsed = datetime.strptime(str(value).strip(), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except Exception:
        return None
    return parsed.timestamp()
