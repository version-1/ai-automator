#!/usr/bin/env python3
"""Collect curation candidates and their available bodies into one JSON document.

The script intentionally only discovers sites with documented public endpoints.  Sites
whose TARGET entry has no usable feed are represented as a source result that requests
an explicit fallback, rather than guessing an undocumented endpoint.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import ipaddress
import json
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

USER_AGENT = "ai-automator-curation/1.0 (+https://github.com)"
SOURCES = ("zenn", "dev", "hacker_news", "note", "medium", "github")


class ArticleExtractor(HTMLParser):
    """Extract article-like regions without treating page navigation as evidence."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0
        self.containers: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        elif tag in {"article", "main"} or (tag in {"div", "section"} and re.search(r"article|content|post|entry", " ".join(value or "" for _, value in attrs), re.I)):
            self.containers.append(tag)
        elif self.containers and tag in {"p", "br", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        elif self.containers and tag == self.containers[-1]:
            self.containers.pop()

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and self.containers:
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", "".join(self.parts))).strip()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat()


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    kept = [(key, value) for key, value in query if not key.startswith("utm_")]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(kept), ""))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: Any, fp: Any, code: int, message: str, headers: Any, newurl: str) -> None:
        return None


def is_public_url(url: str, resolver: Callable[..., Any]) -> tuple[bool, str | None]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False, "unsupported URL scheme or missing host"
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost":
        return False, "local host is not allowed"
    try:
        addresses = {ipaddress.ip_address(result[4][0]) for result in resolver(hostname, None, type=socket.SOCK_STREAM)}
    except ValueError:
        return False, "invalid IP address"
    except socket.gaierror as error:
        return False, f"DNS resolution failed: {error}"
    if not addresses or any(not address.is_global for address in addresses):
        return False, "non-public address is not allowed"
    return True, None


def fetch(url: str, opener: Callable[..., Any] | None = None, resolver: Callable[..., Any] | None = None) -> tuple[bytes | None, str | None, str | None]:
    # A test opener receives deterministic fixture URLs; production requests resolve each
    # hop and never let urllib follow an unvalidated redirect.
    validate_dns = opener is None
    resolver = resolver or socket.getaddrinfo
    effective_opener = opener or urllib.request.build_opener(NoRedirect()).open
    current = url
    for _ in range(5):
        if validate_dns:
            allowed, error = is_public_url(current, resolver)
            if not allowed:
                return None, None, error
        else:
            parsed = urllib.parse.urlsplit(current)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.hostname.rstrip(".").lower() == "localhost":
                return None, None, "unsupported or local URL"
        request = urllib.request.Request(current, headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/rss+xml, text/html;q=0.9"})
        try:
            with effective_opener(request, timeout=20) as response:
                return response.read(), response.headers.get_content_type(), None
        except urllib.error.HTTPError as error:
            location = error.headers.get("Location") if 300 <= error.code < 400 else None
            if not location:
                return None, None, f"{type(error).__name__}: {error}"
            current = urllib.parse.urljoin(current, location)
        except (urllib.error.URLError, TimeoutError) as error:
            return None, None, f"{type(error).__name__}: {error}"
    return None, None, "too many redirects"


def fetch_json(url: str, opener: Callable[..., Any]) -> tuple[Any | None, str | None]:
    body, _, error = fetch(url, opener)
    if error:
        return None, error
    try:
        return json.loads((body or b"").decode("utf-8")), None
    except json.JSONDecodeError as error:
        return None, f"JSONDecodeError: {error}"


def article_body(url: str, opener: Callable[..., Any]) -> dict[str, Any]:
    body, content_type, error = fetch(url, opener)
    if error:
        return {"url": url, "status": "unreadable", "text": None, "error": error}
    if content_type and not ("html" in content_type or content_type.startswith("text/") or "json" in content_type):
        return {"url": url, "status": "unreadable", "text": None, "error": f"unsupported content type: {content_type}"}
    decoded = (body or b"").decode("utf-8", errors="replace")
    if content_type and "html" in content_type:
        parser = ArticleExtractor()
        parser.feed(decoded)
        decoded = parser.text()
    if not decoded:
        return {"url": url, "status": "unreadable", "text": None, "error": "article body was empty"}
    if len(decoded) < 80:
        return {"url": url, "status": "partial", "text": decoded, "error": "article body was too short to verify"}
    return {"url": url, "status": "fetched", "text": decoded[:100000], "error": None}


def candidate(source: str, url: str, title: str | None, summary: str | None, *, evidence_url: str, published_at: str | None = None, shared_at: str | None = None, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "url": canonical_url(url), "title": title, "source": source,
        "discovered_at": now_iso(), "published_at": published_at, "updated_at": None,
        "shared_at": shared_at, "event_date": None, "summary": summary,
        "category": None, "status": "pending", "reason": None,
        "selected_report_dates": [], "discovery": {"url": evidence_url},
        "metrics": metrics or {}, "content": None,
    }


def feed_candidates(source: str, feed_url: str, limit: int, opener: Callable[..., Any]) -> tuple[list[dict[str, Any]], str | None]:
    body, _, error = fetch(feed_url, opener)
    if error:
        return [], error
    try:
        root = ET.fromstring(body or b"")
    except ET.ParseError as error:
        return [], f"XMLParseError: {error}"
    entries = root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry")
    results = []
    for entry in entries[:limit]:
        link = entry.findtext("link")
        if link is None:
            atom_link = entry.find("{http://www.w3.org/2005/Atom}link")
            link = atom_link.get("href") if atom_link is not None else None
        if not link:
            continue
        title = entry.findtext("title") or entry.findtext("{http://www.w3.org/2005/Atom}title")
        summary = entry.findtext("description") or entry.findtext("{http://www.w3.org/2005/Atom}summary")
        published = entry.findtext("pubDate") or entry.findtext("{http://www.w3.org/2005/Atom}published")
        results.append(candidate(source, link, html.unescape(title or ""), html.unescape(summary or ""), evidence_url=feed_url, published_at=published))
    return results, None


def collect_dev(limit: int, targets: list[str], opener: Callable[..., Any]) -> tuple[list[dict[str, Any]], str | None]:
    endpoint = f"https://dev.to/api/articles/latest?per_page={limit}"
    for target in targets:
        parts = [part for part in urllib.parse.urlsplit(target).path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "t":
            endpoint = f"https://dev.to/api/articles?tag={urllib.parse.quote(parts[1])}&per_page={limit}"
            break
        if parts and parts[0] not in {"api", "latest"}:
            endpoint = f"https://dev.to/api/articles?username={urllib.parse.quote(parts[0])}&per_page={limit}"
            break
    payload, error = fetch_json(endpoint, opener)
    if error or not isinstance(payload, list):
        return [], error or "unexpected DEV API response"
    return [candidate("dev", item["url"], item.get("title"), item.get("description"), evidence_url=endpoint, published_at=item.get("published_at"), metrics={"public_reactions_count": item.get("public_reactions_count"), "comments_count": item.get("comments_count")}) for item in payload if item.get("url")], None


def collect_hn(limit: int, opener: Callable[..., Any]) -> tuple[list[dict[str, Any]], str | None]:
    base = "https://hacker-news.firebaseio.com/v0"
    streams: list[list[int]] = []
    errors: list[str] = []
    for list_name in ("topstories", "newstories", "showstories"):
        values, error = fetch_json(f"{base}/{list_name}.json", opener)
        if error:
            errors.append(f"{list_name}: {error}")
        elif isinstance(values, list):
            streams.append(values[:limit])
    results, seen = [], set()
    ids = [story_id for index in range(limit) for stream in streams if index < len(stream) for story_id in [stream[index]]]
    for story_id in ids:
        if story_id in seen:
            continue
        seen.add(story_id)
        item_url = f"{base}/item/{story_id}.json"
        item, error = fetch_json(item_url, opener)
        if error:
            errors.append(f"item/{story_id}: {error}")
            continue
        if not isinstance(item, dict) or item.get("deleted") or item.get("dead") or item.get("type") != "story":
            continue
        url = item.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
        shared_at = dt.datetime.fromtimestamp(item.get("time", 0), dt.timezone.utc).isoformat() if item.get("time") else None
        result = candidate("hacker_news", url, item.get("title"), item.get("text"), evidence_url=item_url, shared_at=shared_at, metrics={"score": item.get("score"), "descendants": item.get("descendants"), "hn_item_url": f"https://news.ycombinator.com/item?id={story_id}"})
        if not item.get("url"):
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(item.get("text") or ""))).strip()
            result["content"] = {"url": item_url, "status": "fetched" if text else "unreadable", "text": text or None, "error": None if text else "HN item did not contain text"}
        results.append(result)
        if len(results) >= limit:
            break
    return results, "; ".join(errors) or None


def collect_github(limit: int, opener: Callable[..., Any]) -> tuple[list[dict[str, Any]], str | None]:
    url = "https://github.com/trending?since=weekly"
    body, _, error = fetch(url, opener)
    if error:
        return [], error
    text = (body or b"").decode("utf-8", errors="replace")
    seen, results = set(), []
    for row in re.findall(r'<article[^>]*class="[^"]*Box-row[^"]*"[^>]*>(.*?)</article>', text, re.S | re.I):
        match = re.search(r'<h2[^>]*>.*?href="/([\w.-]+/[\w.-]+)"', row, re.S | re.I)
        if not match:
            continue
        repository = match.group(1)
        if repository in seen or len(results) >= limit:
            continue
        seen.add(repository)
        stars = re.search(r"(\d[\d,]*)\s+stars this week", row, re.I)
        results.append(candidate("github", f"https://github.com/{repository}", repository, None, evidence_url=url, metrics={"stars_this_week": stars.group(1) if stars else None}))
    if not results:
        return [], "GitHub Trending HTML did not contain repository links"
    return results, None


def site_from_target(target: str) -> str | None:
    host = urllib.parse.urlsplit(target).netloc.lower()
    if host == "zenn.dev": return "zenn"
    if host.endswith("dev.to"): return "dev"
    if host.endswith("ycombinator.com") or host.endswith("news.ycombinator.com"): return "hacker_news"
    if host.endswith("note.com"): return "note"
    if host.endswith("medium.com"): return "medium"
    if host.endswith("github.com"): return "github"
    return None


def target_urls(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    section = re.search(r"^## 対象ページ\s*$([\s\S]*?)(?=^##\s|\Z)", text, re.M)
    if section is None:
        return []
    return re.findall(r"https?://[^\s)>]+", re.sub(r"<!--.*?-->", "", section.group(1), flags=re.S))


def source_targets(urls: list[str]) -> dict[str, list[str]]:
    targets: dict[str, list[str]] = {}
    for url in urls:
        source = site_from_target(url)
        if source:
            targets.setdefault(source, []).append(url)
    return targets


def configured_feeds(source: str, urls: list[str]) -> list[str]:
    """Return only an explicitly configured feed; never invent note/Medium feeds."""
    feeds = []
    for url in urls:
        path = urllib.parse.urlsplit(url).path.rstrip("/")
        if path.endswith("/feed") or path == "/feed" or "/feed/" in path or path.endswith("/rss") or "/rss/" in path:
            feeds.append(url)
    if source == "zenn":
        for url in urls:
            parsed = urllib.parse.urlsplit(url)
            path = parsed.path.rstrip("/")
            if path and path != "/":
                feeds.append(urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, f"{path}/feed", "", "")))
            else:
                feeds.append("https://zenn.dev/feed")
    return list(dict.fromkeys(feeds))


def collect_feeds(source: str, feeds: list[str], limit: int, opener: Callable[..., Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items, attempts = [], []
    for feed in feeds:
        found, error = feed_candidates(source, feed, limit, opener)
        items.extend(found)
        attempts.append({"url": feed, "candidate_count": len(found), "error": error})
    return items, attempts


def add_content(candidates: list[dict[str, Any]], opener: Callable[..., Any]) -> None:
    for item in candidates:
        if item.get("content") is not None:
            continue
        if item["source"] == "github":
            match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)", item["url"])
            content_url = f"https://raw.githubusercontent.com/{match.group(1)}/HEAD/README.md" if match else item["url"]
        else:
            content_url = item["url"]
        item["content"] = article_body(content_url, opener)
        if item["content"]["status"] in {"unreadable", "partial"}:
            item["status"] = "unreadable"
            item["reason"] = "本文を確認できませんでした"


DEFAULT_CANDIDATE = {
    "title": None, "source": None, "discovered_at": None, "published_at": None,
    "updated_at": None, "shared_at": None, "event_date": None, "summary": None,
    "category": None, "status": "pending", "reason": None, "selected_report_dates": [],
    "discovery": {"url": None}, "metrics": {}, "content": None,
}


def normalize_candidate(value: dict[str, Any]) -> dict[str, Any]:
    normalized = {**DEFAULT_CANDIDATE, **value, "url": canonical_url(value["url"])}
    normalized["selected_report_dates"] = list(value.get("selected_report_dates") or [])
    normalized["metrics"] = dict(value.get("metrics") or {})
    normalized["discovery"] = {"url": None, **(value.get("discovery") or {})}
    content = value.get("content")
    if content is not None:
        normalized["content"] = {"url": normalized["url"], "status": "unreadable", "text": None, "error": None, **content}
        if normalized["content"]["status"] == "fetched" and not str(normalized["content"].get("text") or "").strip():
            normalized["content"].update({"status": "unreadable", "text": None, "error": "fallback fetched content was empty"})
    return normalized


def content_rank(content: dict[str, Any] | None) -> int:
    if not content:
        return 0
    return {"unreadable": 1, "partial": 2, "fetched": 3}.get(content.get("status"), 0)


def merge_fallback(candidates: list[dict[str, Any]], path: Path | None) -> list[str]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("candidates", payload) if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        raise ValueError("fallback JSON must be a candidate array or an object with candidates")
    existing = {item["url"]: item for item in candidates}
    imported = []
    for item in values:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        fallback = normalize_candidate(item)
        current = existing.get(fallback["url"])
        if current is None:
            candidates.append(fallback)
            existing[fallback["url"]] = fallback
            imported.append(fallback["url"])
            continue
        if content_rank(fallback["content"]) > content_rank(current.get("content")):
            current["content"] = fallback["content"]
            current["status"] = "pending" if fallback["content"]["status"] == "fetched" else "unreadable"
            current["reason"] = fallback.get("reason")
        if fallback["discovery"].get("url"):
            current["discovery"] = fallback["discovery"]
        current["metrics"].update(fallback["metrics"])
        for field in ("title", "summary", "published_at", "updated_at", "shared_at", "event_date", "category"):
            if current.get(field) is None and fallback.get(field) is not None:
                current[field] = fallback[field]
        imported.append(fallback["url"])
    return imported


def collect(target_path: Path, output_path: Path, limit: int, fallback_path: Path | None, opener: Callable[..., Any] | None = None) -> dict[str, Any]:
    targets = source_targets(target_urls(target_path))
    requested = set(targets)
    source_results: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    for source in SOURCES:
        if source not in requested:
            continue
        attempts = []
        if source == "zenn":
            items, attempts = collect_feeds(source, configured_feeds(source, targets[source]), limit, opener)
            error = "; ".join(attempt["error"] for attempt in attempts if attempt["error"]) or None
        elif source == "dev": items, error = collect_dev(limit, targets[source], opener)
        elif source == "hacker_news": items, error = collect_hn(limit, opener)
        elif source == "github": items, error = collect_github(limit, opener)
        else:
            feeds = configured_feeds(source, targets[source])
            if feeds:
                items, attempts = collect_feeds(source, feeds, limit, opener)
                error = "; ".join(attempt["error"] for attempt in attempts if attempt["error"]) or None
            else:
                items, error = [], "fallback_required: TARGET.md に RSS URL が指定されていません"
        candidates.extend(items)
        source_results[source] = {"status": "completed" if not error else "completed_with_error", "candidate_count": len(items), "error": error, "attempts": attempts}
    add_content(candidates, opener)
    imported = merge_fallback(candidates, fallback_path)
    payload = {"collected_at": now_iso(), "timezone": "Asia/Tokyo", "target": str(target_path), "sources": source_results, "fallback_imported_urls": imported, "candidates": candidates}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, default=Path("curation/TARGET.md"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--fallback-json", type=Path)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    result = collect(args.target, args.output, args.limit, args.fallback_json)
    print(json.dumps({"output": str(args.output), "sources": result["sources"], "candidates": len(result["candidates"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
