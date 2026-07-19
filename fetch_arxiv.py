#!/usr/bin/env python3
"""Fetch self-improvement papers from arXiv/OpenReview and build a static website.

The script intentionally uses only Python's standard library so it can run on a
fresh machine or in a scheduled GitHub Action without installing dependencies.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import shutil
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


ROOT = Path(__file__).resolve().parent
API_URL = "https://export.arxiv.org/api/query"
OPENREVIEW_API_URL = "https://api2.openreview.net/notes/search"
TOPIC_TERMS = (
    "self improvement",
    "self-improvement",
    "self improving",
    "self-improving",
    "recursive self-improvement",
    "self-refinement",
    "self-reflection",
    "self-correction",
    "self-evolution",
    "autonomous improvement",
    "continual self-training",
    "iterative refinement",
    "learning from self-generated feedback",
    "agent evolution",
)
AGENT_TERMS = (
    "agent",
    "agents",
    "agentic",
    "multi-agent",
    "multiagent",
)
TITLE_ABSTRACT_QUERY = " OR ".join(
    f'(ti:"{term}" OR abs:"{term}")' for term in TOPIC_TERMS
)
AGENT_TITLE_ABSTRACT_QUERY = " OR ".join(
    f'(ti:"{term}" OR abs:"{term}")' for term in AGENT_TERMS
)
DEFAULT_QUERY = (
    f"({TITLE_ABSTRACT_QUERY}) AND "
    f"({AGENT_TITLE_ABSTRACT_QUERY}) AND "
    "(cat:cs.AI OR cat:cs.CL OR cat:cs.LG OR cat:cs.MA) AND "
    "submittedDate:[202501010000 TO 209912312359]"
)
OPENREVIEW_QUERY_BATCHES = tuple(
    "(" + " OR ".join(f'\"{term}\"' for term in TOPIC_TERMS[start : start + 4]) + ") AND ("
    + " OR ".join(f'\"{term}\"' for term in AGENT_TERMS)
    + ")"
    for start in range(0, len(TOPIC_TERMS), 4)
)
DEFAULT_USER_AGENT = (
    "ArxivSelfImprovementDaily/1.0 "
    "(+https://github.com/your-name/arxiv-self-improvement)"
)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
ATOM = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
OPENSEARCH = "http://a9.com/-/spec/opensearch/1.1/"


def clean_text(value: Optional[str]) -> str:
    """Collapse whitespace from arXiv's line-wrapped Atom fields."""
    return re.sub(r"\s+", " ", value or "").strip()


def valid_datetime(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def element_text(element: ET.Element, path: str) -> str:
    child = element.find(path, ATOM)
    return clean_text(child.text if child is not None else "")


def base_arxiv_id(identifier: str) -> str:
    identifier = identifier.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"v\d+$", "", identifier)


def parse_atom(xml_bytes: bytes, discovered_on: Optional[str] = None) -> list[dict[str, Any]]:
    """Parse an arXiv Atom response into JSON-serializable paper records."""
    root = ET.fromstring(xml_bytes)
    discovered_on = discovered_on or datetime.now(timezone.utc).date().isoformat()
    papers: list[dict[str, Any]] = []

    for entry in root.findall("atom:entry", ATOM):
        entry_url = element_text(entry, "atom:id")
        if not entry_url or "/abs/" not in entry_url:
            # arXiv can encode API errors as an Atom entry; do not treat them as papers.
            continue

        links = {
            link.attrib.get("rel", "alternate"): link.attrib.get("href", "")
            for link in entry.findall("atom:link", ATOM)
        }
        pdf_url = next(
            (
                link.attrib.get("href", "")
                for link in entry.findall("atom:link", ATOM)
                if link.attrib.get("type") == "application/pdf"
            ),
            "",
        )
        categories = [
            category.attrib.get("term", "")
            for category in entry.findall("atom:category", ATOM)
            if category.attrib.get("term")
        ]
        primary = entry.find("arxiv:primary_category", ATOM)
        versioned_id = entry_url.rstrip("/").rsplit("/", 1)[-1]
        paper = {
            "id": base_arxiv_id(versioned_id),
            "versioned_id": versioned_id,
            "title": element_text(entry, "atom:title"),
            "authors": [
                clean_text(author.findtext("atom:name", default="", namespaces=ATOM))
                for author in entry.findall("atom:author", ATOM)
            ],
            "abstract": element_text(entry, "atom:summary"),
            "published": element_text(entry, "atom:published"),
            "updated": element_text(entry, "atom:updated"),
            "categories": categories,
            "primary_category": (
                primary.attrib.get("term", "") if primary is not None else (categories[0] if categories else "")
            ),
            "arxiv_url": links.get("alternate") or entry_url.replace("http://", "https://"),
            "pdf_url": pdf_url.replace("http://", "https://")
            or f"https://arxiv.org/pdf/{versioned_id}",
            "comment": element_text(entry, "arxiv:comment"),
            "journal_ref": element_text(entry, "arxiv:journal_ref"),
            "doi": element_text(entry, "arxiv:doi"),
            "discovered_on": discovered_on,
            "source": "arXiv",
            "sources": ["arXiv"],
        }
        papers.append(paper)
    return papers


def build_api_url(query: str, max_results: int, start: int = 0) -> str:
    params = urllib.parse.urlencode(
        {
            "search_query": query,
            "start": start,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    return f"{API_URL}?{params}"


def fetch_url(
    url: str,
    user_agent: str,
    accept: str,
    timeout: int = 120,
    attempts: int = 4,
) -> bytes:
    """Fetch a public API URL while retrying temporary failures politely."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent, "Accept": accept},
    )
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code not in RETRYABLE_STATUS or attempt == attempts - 1:
                raise
            retry_after = error.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                delay = int(retry_after)
            elif error.code == 429:
                delay = 60 * (2**attempt)
            else:
                delay = 3 * (2**attempt)
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException):
            if attempt == attempts - 1:
                raise
            delay = 3 * (2**attempt)
        time.sleep(delay)
    raise RuntimeError("API request failed after retries")


def fetch_feed(
    query: str,
    max_results: int,
    user_agent: str,
    start: int = 0,
) -> bytes:
    """Fetch one arXiv result page."""
    return fetch_url(
        build_api_url(query, max_results, start),
        user_agent,
        "application/atom+xml",
    )


def total_results(xml_bytes: bytes) -> int:
    root = ET.fromstring(xml_bytes)
    element = root.find(f"{{{OPENSEARCH}}}totalResults")
    try:
        return int(element.text or "0") if element is not None else 0
    except ValueError:
        return 0


def fetch_all_papers(
    query: str,
    page_size: int,
    user_agent: str,
    discovered_on: str,
) -> list[dict[str, Any]]:
    """Fetch every page for a refined query, respecting arXiv's 3-second guidance."""
    papers: list[dict[str, Any]] = []
    start = 0
    total: Optional[int] = None

    while total is None or start < total:
        feed = fetch_feed(query, page_size, user_agent, start=start)
        page = parse_atom(feed, discovered_on)
        if total is None:
            total = total_results(feed)
            print(f"arXiv matched {total} papers; fetching in pages of {page_size}.", flush=True)
        if not page:
            break
        papers.extend(page)
        start += page_size
        print(f"Fetched {min(start, total)} / {total} papers.", flush=True)
        if start < total:
            time.sleep(3)
    return papers


def fetch_year_by_month(
    query: str,
    year: int,
    page_size: int,
    user_agent: str,
    discovered_on: str,
) -> list[dict[str, Any]]:
    """Backfill one year using small monthly queries to reduce arXiv API load."""
    base_query = re.sub(
        r"\s+AND\s+submittedDate:\[[^\]]+\]\s*$", "", query, flags=re.IGNORECASE
    )
    papers: list[dict[str, Any]] = []
    for month in range(1, 13):
        start = f"{year}{month:02d}010000"
        if month == 12:
            end = f"{year + 1}01010000"
        else:
            end = f"{year}{month + 1:02d}010000"
        monthly_query = f"{base_query} AND submittedDate:[{start} TO {end}]"
        print(f"Backfilling {year}-{month:02d}...", flush=True)
        papers.extend(
            fetch_all_papers(monthly_query, page_size, user_agent, discovered_on)
        )
        if month < 12:
            time.sleep(10)
    return papers


def openreview_content_value(content: dict[str, Any], field: str, default: Any = "") -> Any:
    value = content.get(field, default)
    if isinstance(value, dict) and "value" in value:
        return value.get("value", default)
    return value


def milliseconds_to_iso(value: Any) -> str:
    try:
        timestamp = int(value) / 1000
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(microsecond=0).isoformat()


def build_openreview_url(query: str, limit: int, offset: int = 0) -> str:
    params = urllib.parse.urlencode(
        {
            "query": query,
            "content": "all",
            "source": "forum",
            "sort": "tmdate:desc",
            "limit": min(limit, 1000),
            "offset": offset,
        }
    )
    return f"{OPENREVIEW_API_URL}?{params}"


def is_openreview_submission(note: dict[str, Any]) -> bool:
    invitations = note.get("invitations") or []
    if not isinstance(invitations, list):
        invitations = [str(invitations)]
    if any(str(invitation).startswith("DBLP.org/") for invitation in invitations):
        return False
    if any("Workshop_Proposals" in str(invitation) for invitation in invitations):
        return False
    return any("Submission" in str(invitation) for invitation in invitations)


def parse_openreview_notes(
    payload: dict[str, Any], discovered_on: Optional[str] = None
) -> list[dict[str, Any]]:
    """Convert public OpenReview submission notes into the site's paper schema."""
    discovered_on = discovered_on or datetime.now(timezone.utc).date().isoformat()
    papers: list[dict[str, Any]] = []
    for note in payload.get("notes", []):
        if not isinstance(note, dict) or not is_openreview_submission(note):
            continue
        content = note.get("content") or {}
        if not isinstance(content, dict):
            continue
        title = clean_text(str(openreview_content_value(content, "title", "")))
        abstract = clean_text(str(openreview_content_value(content, "abstract", "")))
        note_id = clean_text(str(note.get("id", "")))
        published = milliseconds_to_iso(note.get("cdate") or note.get("tcdate"))
        published_date = valid_datetime(published)
        if not note_id or not title or not abstract or not published_date:
            continue
        if published_date.year < 2025:
            continue

        raw_authors = openreview_content_value(content, "authors", [])
        authors = [clean_text(str(author)) for author in raw_authors] if isinstance(raw_authors, list) else []
        venue = clean_text(str(openreview_content_value(content, "venue", "")))
        venue_id = clean_text(str(openreview_content_value(content, "venueid", "")))
        forum_url = f"https://openreview.net/forum?id={note_id}"
        paper = {
            "id": f"openreview:{note_id}",
            "versioned_id": note_id,
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "published": published,
            "updated": milliseconds_to_iso(note.get("tmdate") or note.get("mdate") or note.get("cdate")),
            "categories": ["OpenReview"],
            "primary_category": "OpenReview",
            "arxiv_url": forum_url,
            "openreview_url": forum_url,
            "pdf_url": f"https://openreview.net/pdf?id={note_id}",
            "comment": venue,
            "journal_ref": venue,
            "doi": clean_text(str(openreview_content_value(content, "doi", ""))),
            "venue": venue,
            "venue_id": venue_id,
            "discovered_on": discovered_on,
            "source": "OpenReview",
            "sources": ["OpenReview"],
        }
        if matches_agent_self_improvement(paper):
            papers.append(paper)
    return papers


def fetch_openreview_papers(
    queries: Iterable[str],
    page_size: int,
    user_agent: str,
    discovered_on: str,
    fetch_all: bool = False,
) -> list[dict[str, Any]]:
    """Fetch public OpenReview submissions matching the Agent topic query."""
    query_batches = list(queries)
    papers_by_id: dict[str, dict[str, Any]] = {}
    page_size = min(page_size, 1000)
    for batch_number, query in enumerate(query_batches, start=1):
        offset = 0
        total: Optional[int] = None
        while total is None or offset < total:
            raw = fetch_url(
                build_openreview_url(query, page_size, offset),
                user_agent,
                "application/json",
            )
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Invalid OpenReview response")
            if total is None:
                total = int(payload.get("count") or 0)
                print(
                    f"OpenReview batch {batch_number} matched {total} notes.",
                    flush=True,
                )
            page = payload.get("notes") or []
            for paper in parse_openreview_notes(payload, discovered_on):
                papers_by_id[paper["id"]] = paper
            if not fetch_all or not page:
                break
            offset += page_size
            print(
                f"Fetched {min(offset, total)} / {total} notes in OpenReview batch {batch_number}.",
                flush=True,
            )
            if offset < total:
                time.sleep(1)
        if batch_number < len(query_batches):
            time.sleep(1)
    return list(papers_by_id.values())


def load_database(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"papers": []}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return {"papers": data}
    if not isinstance(data, dict) or not isinstance(data.get("papers", []), list):
        raise ValueError(f"Invalid paper database: {path}")
    return data


def merge_papers(
    existing: Iterable[dict[str, Any]], incoming: Iterable[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    merged = {paper["id"]: paper for paper in existing if paper.get("id")}
    new_count = 0
    for paper in incoming:
        old = merged.get(paper["id"])
        if old is None:
            new_count += 1
        else:
            paper["discovered_on"] = old.get("discovered_on", paper["discovered_on"])
        merged[paper["id"]] = paper
    papers = sorted(
        merged.values(),
        key=lambda paper: (paper.get("published", ""), paper.get("updated", "")),
        reverse=True,
    )
    return papers, new_count


def normalized_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return re.sub(r"[^a-z0-9]+", "", normalized)


def ensure_source_metadata(paper: dict[str, Any]) -> None:
    source = paper.get("source") or (
        "OpenReview" if str(paper.get("id", "")).startswith("openreview:") else "arXiv"
    )
    paper["source"] = source
    sources = paper.get("sources") if isinstance(paper.get("sources"), list) else []
    paper["sources"] = list(dict.fromkeys([*sources, source]))


def combine_duplicate_sources(primary: dict[str, Any], duplicate: dict[str, Any]) -> dict[str, Any]:
    combined = dict(primary)
    sources = [*(primary.get("sources") or []), *(duplicate.get("sources") or [])]
    combined["sources"] = list(dict.fromkeys(sources))
    for paper in (primary, duplicate):
        if paper.get("source") == "OpenReview":
            if paper.get("openreview_url"):
                combined["openreview_url"] = paper["openreview_url"]
            venues = [*(combined.get("openreview_venues") or [])]
            if paper.get("venue") and paper["venue"] not in venues:
                venues.append(paper["venue"])
            if venues:
                combined["openreview_venues"] = venues
    discovery_dates = [
        value
        for value in (primary.get("discovered_on"), duplicate.get("discovered_on"))
        if value
    ]
    if discovery_dates:
        combined["discovered_on"] = min(discovery_dates)
    return combined


def deduplicate_papers_by_title(papers: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse exact normalized-title duplicates, preferring the arXiv record."""
    deduplicated: dict[str, dict[str, Any]] = {}
    for paper in papers:
        ensure_source_metadata(paper)
        key = normalized_title(str(paper.get("title", ""))) or str(paper.get("id", ""))
        existing = deduplicated.get(key)
        if existing is None:
            deduplicated[key] = paper
            continue
        if paper.get("source") == "arXiv" and existing.get("source") != "arXiv":
            deduplicated[key] = combine_duplicate_sources(paper, existing)
        else:
            deduplicated[key] = combine_duplicate_sources(existing, paper)
    return sorted(
        deduplicated.values(),
        key=lambda paper: (paper.get("published", ""), paper.get("updated", "")),
        reverse=True,
    )


def matches_agent_self_improvement(paper: dict[str, Any]) -> bool:
    """Return whether title/abstract contain both topic and Agent-domain terms."""
    searchable = f'{paper.get("title", "")} {paper.get("abstract", "")}'.casefold()
    topic_match = any(term.casefold() in searchable for term in TOPIC_TERMS)
    agent_match = any(
        re.search(
            rf"(?<![a-z0-9]){re.escape(term.casefold())}(?![a-z0-9])",
            searchable,
        )
        for term in AGENT_TERMS
    )
    return topic_match and agent_match


def matched_topic_terms(text: str) -> list[str]:
    searchable = text.casefold()
    return [term for term in TOPIC_TERMS if term.casefold() in searchable]


def agent_term_matches(text: str) -> list[tuple[str, int]]:
    searchable = text.casefold()
    matches: list[tuple[str, int]] = []
    for term in AGENT_TERMS:
        pattern = rf"(?<![a-z0-9]){re.escape(term.casefold())}(?![a-z0-9])"
        match = re.search(pattern, searchable)
        if match:
            matches.append((term, match.start()))
    return matches


def topic_term_positions(text: str) -> list[int]:
    searchable = text.casefold()
    positions: list[int] = []
    for term in TOPIC_TERMS:
        start = searchable.find(term.casefold())
        if start >= 0:
            positions.append(start)
    return positions


def signals_are_close(topic_positions: list[int], agent_positions: list[int], limit: int) -> bool:
    return bool(topic_positions and agent_positions) and min(
        abs(topic - agent)
        for topic in topic_positions
        for agent in agent_positions
    ) <= limit


def calculate_relevance(paper: dict[str, Any]) -> tuple[int, list[str]]:
    """Score Agent self-improvement relevance with an explainable 0–100 heuristic."""
    title = str(paper.get("title", ""))
    abstract = str(paper.get("abstract", ""))
    title_topics = matched_topic_terms(title)
    abstract_topics = matched_topic_terms(abstract)
    title_agents = agent_term_matches(title)
    abstract_agents = agent_term_matches(abstract)
    reasons: list[str] = []
    score = 0

    if title_topics:
        score += 40
        reasons.append("标题命中自进化关键词")
    elif abstract_topics:
        score += 18
        reasons.append("摘要命中自进化关键词")

    if title_agents:
        score += 30
        reasons.append("标题命中 Agent 关键词")
    elif abstract_agents:
        score += 15
        reasons.append("摘要命中 Agent 关键词")

    if title_topics and title_agents:
        score += 15
        reasons.append("标题同时包含自进化与 Agent 信号")
        if signals_are_close(
            topic_term_positions(title),
            [position for _, position in title_agents],
            80,
        ):
            score += 5
            reasons.append("标题中的两类信号距离接近")
    elif abstract_topics and abstract_agents and signals_are_close(
        topic_term_positions(abstract),
        [position for _, position in abstract_agents],
        240,
    ):
        score += 10
        reasons.append("摘要中的两类信号距离接近")

    distinct_topics = set(title_topics) | set(abstract_topics)
    if len(distinct_topics) > 1:
        score += 5
        reasons.append("覆盖多个自进化关键词")

    if paper.get("primary_category") in {"cs.AI", "cs.MA"}:
        score += 5
        reasons.append("属于核心 Agent 相关分类")

    return min(score, 100), reasons


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def save_database(path: Path, database: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(database, ensure_ascii=False, indent=2) + "\n")


def build_site(database: dict[str, Any], output_dir: Path, source_dir: Path) -> None:
    """Copy the static shell and write data as JavaScript for file:// support."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "styles.css", "app.js"):
        shutil.copyfile(source_dir / name, output_dir / name)
    payload = json.dumps(database, ensure_ascii=False, separators=(",", ":"))
    # Escape characters that could terminate a script context in unusual metadata.
    payload = payload.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    atomic_write_text(output_dir / "papers-data.js", f"window.PAPERS_DATA={payload};\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch self-improvement papers from arXiv and OpenReview, then build a static website."
    )
    parser.add_argument("--query", default=DEFAULT_QUERY, help="arXiv API search_query value")
    parser.add_argument(
        "--openreview-query",
        help="replace the built-in batched OpenReview query with one custom full-text query",
    )
    parser.add_argument("--max-results", type=int, default=100, help="results requested per daily run")
    parser.add_argument(
        "--all-results",
        action="store_true",
        help="paginate through every result for the query (use for historical backfills)",
    )
    parser.add_argument(
        "--backfill-year",
        type=int,
        help="fetch a complete year using smaller monthly queries",
    )
    parser.add_argument(
        "--backfill-openreview",
        action="store_true",
        help="paginate through every OpenReview result from 2025 onward",
    )
    parser.add_argument(
        "--skip-arxiv",
        action="store_true",
        help="skip arXiv for this run",
    )
    parser.add_argument(
        "--skip-openreview",
        action="store_true",
        help="skip OpenReview for this run",
    )
    parser.add_argument("--data-file", type=Path, default=ROOT / "data" / "papers.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "public")
    parser.add_argument("--source-dir", type=Path, default=ROOT / "src")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="rebuild the webpage from cached data without calling external APIs",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.max_results <= 2000:
        print("--max-results must be between 1 and 2000", file=sys.stderr)
        return 2

    checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    database = load_database(args.data_file)
    incoming: list[dict[str, Any]] = []
    source_errors: list[str] = []
    successful_sources: list[str] = []

    if not args.offline:
        if not args.skip_arxiv:
            try:
                if args.backfill_year:
                    incoming.extend(
                        fetch_year_by_month(
                            args.query,
                            args.backfill_year,
                            args.max_results,
                            args.user_agent,
                            checked_at[:10],
                        )
                    )
                elif args.all_results:
                    incoming.extend(
                        fetch_all_papers(
                            args.query, args.max_results, args.user_agent, checked_at[:10]
                        )
                    )
                else:
                    feed = fetch_feed(args.query, args.max_results, args.user_agent)
                    incoming.extend(parse_atom(feed, checked_at[:10]))
                successful_sources.append("arXiv")
            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                TimeoutError,
                http.client.HTTPException,
                ET.ParseError,
            ) as error:
                source_errors.append(f"arXiv {type(error).__name__}: {error}")

        if not args.skip_openreview:
            try:
                openreview_queries = (
                    [args.openreview_query]
                    if args.openreview_query
                    else OPENREVIEW_QUERY_BATCHES
                )
                incoming.extend(
                    fetch_openreview_papers(
                        openreview_queries,
                        args.max_results,
                        args.user_agent,
                        checked_at[:10],
                        fetch_all=args.backfill_openreview,
                    )
                )
                successful_sources.append("OpenReview")
            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                TimeoutError,
                http.client.HTTPException,
                json.JSONDecodeError,
                ValueError,
            ) as error:
                source_errors.append(f"OpenReview {type(error).__name__}: {error}")

    error_message = "；".join(source_errors)

    existing_papers = database.get("papers", [])
    for paper in existing_papers:
        ensure_source_metadata(paper)
    existing_ids = {paper.get("id") for paper in existing_papers if paper.get("id")}
    papers, _ = merge_papers(existing_papers, incoming)
    if args.query == DEFAULT_QUERY:
        # Remove cached records that do not satisfy the current title/abstract
        # topic match and Agent-domain focus.
        papers = [paper for paper in papers if matches_agent_self_improvement(paper)]
    papers = deduplicate_papers_by_title(papers)
    new_count = sum(paper.get("id") not in existing_ids for paper in papers)
    for paper in papers:
        score, reasons = calculate_relevance(paper)
        paper["relevance_score"] = score
        paper["relevance_reasons"] = reasons
    database.update(
        {
            "papers": papers,
            "query": args.query,
            "openreview_query": (
                args.openreview_query
                if args.openreview_query
                else list(OPENREVIEW_QUERY_BATCHES)
            ),
            "source": "arXiv + OpenReview",
            "source_url": API_URL,
            "source_urls": {
                "arXiv": API_URL,
                "OpenReview": OPENREVIEW_API_URL,
            },
            "last_checked": database.get("last_checked", "") if args.offline else checked_at,
            "last_success": (
                database.get("last_success", "")
                if args.offline or not successful_sources
                else checked_at
            ),
            "new_count": 0 if args.offline else new_count,
            "total_cached": len(papers),
            "fetch_error": error_message,
            "relevance_method": "explainable-keyword-v1",
        }
    )
    save_database(args.data_file, database)
    build_site(database, args.output_dir, args.source_dir)

    expected_sources = int(not args.skip_arxiv) + int(not args.skip_openreview)
    fatal_error = not args.offline and expected_sources > 0 and not successful_sources
    incomplete_backfill = args.backfill_openreview and "OpenReview" not in successful_sources
    if error_message:
        print(f"Source warning: {error_message}", file=sys.stderr)
    if fatal_error or incomplete_backfill:
        print("No complete update was available; rebuilt the site with cached data.", file=sys.stderr)
        return 1
    mode = "cached" if args.offline else f"{new_count} new"
    print(f"Built {args.output_dir / 'index.html'} with {len(papers)} papers ({mode}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
