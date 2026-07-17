#!/usr/bin/env python3
"""Fetch self-improvement papers from arXiv and build a static website.

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
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


ROOT = Path(__file__).resolve().parent
API_URL = "https://export.arxiv.org/api/query"
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


def fetch_feed(
    query: str,
    max_results: int,
    user_agent: str,
    start: int = 0,
    timeout: int = 120,
    attempts: int = 4,
) -> bytes:
    """Fetch one small result page, retrying temporary arXiv failures politely."""
    request = urllib.request.Request(
        build_api_url(query, max_results, start),
        headers={"User-Agent": user_agent, "Accept": "application/atom+xml"},
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
    raise RuntimeError("arXiv request failed after retries")


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
        description="Fetch self-improvement papers from arXiv and build a static website."
    )
    parser.add_argument("--query", default=DEFAULT_QUERY, help="arXiv API search_query value")
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
    parser.add_argument("--data-file", type=Path, default=ROOT / "data" / "papers.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "public")
    parser.add_argument("--source-dir", type=Path, default=ROOT / "src")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="rebuild the webpage from cached data without calling arXiv",
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
    error_message = ""

    if not args.offline:
        try:
            if args.backfill_year:
                incoming = fetch_year_by_month(
                    args.query,
                    args.backfill_year,
                    args.max_results,
                    args.user_agent,
                    checked_at[:10],
                )
            elif args.all_results:
                incoming = fetch_all_papers(
                    args.query, args.max_results, args.user_agent, checked_at[:10]
                )
            else:
                feed = fetch_feed(args.query, args.max_results, args.user_agent)
                incoming = parse_atom(feed, checked_at[:10])
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ET.ParseError) as error:
            error_message = f"{type(error).__name__}: {error}"

    existing_papers = database.get("papers", [])
    existing_ids = {paper.get("id") for paper in existing_papers if paper.get("id")}
    papers, new_count = merge_papers(existing_papers, incoming)
    if args.query == DEFAULT_QUERY:
        # Remove cached records that do not satisfy the current title/abstract
        # topic match and Agent-domain focus.
        papers = [paper for paper in papers if matches_agent_self_improvement(paper)]
        new_count = sum(paper.get("id") not in existing_ids for paper in papers)
    database.update(
        {
            "papers": papers,
            "query": args.query,
            "source": "arXiv API",
            "source_url": API_URL,
            "last_checked": database.get("last_checked", "") if args.offline else checked_at,
            "last_success": (
                database.get("last_success", "") if args.offline or error_message else checked_at
            ),
            "new_count": 0 if args.offline else new_count,
            "total_cached": len(papers),
            "fetch_error": error_message,
        }
    )
    save_database(args.data_file, database)
    build_site(database, args.output_dir, args.source_dir)

    if error_message:
        print(f"arXiv fetch failed; rebuilt the site with cached data: {error_message}", file=sys.stderr)
        return 1
    mode = "cached" if args.offline else f"{new_count} new"
    print(f"Built {args.output_dir / 'index.html'} with {len(papers)} papers ({mode}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
