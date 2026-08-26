"""
Dump wiki.ffo.jp (Japanese FFXI wiki) to JSONL.

No MediaWiki API - static HTML pages at /html/<id>.html. Article bodies are
converted to Markdown: readable without an HTML parser, and about a third
the size of the raw pages once the Bootstrap chrome is gone.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://wiki.ffo.jp"
UA = "ffxi-wiki-dumps/1.0 (+https://github.com/sruon/ffxi-wiki-dumps)"
PAGE_RE = re.compile(r"/html/(\d+)\.html")

# Chrome, navigation and edit affordances - none of it is article content.
DROP_SELECTORS = (
    "script", "style", "nav", "#footer", ".navbar", ".offcanvas",
    "hr.comment", "a[href*='Command=Write']", "a[href*='Command=Comment']",
)


class _Converter(MarkdownConverter):
    """Site-relative links (the page graph), no title attrs, no decorative images."""

    def convert_a(self, el, text, parent_tags=None):
        el.attrs = {"href": el.get("href", "")}
        return super().convert_a(el, text, parent_tags)

    def convert_img(self, el, text, parent_tags=None):
        return ""


_md = _Converter(
    heading_style="ATX",
    table_infer_header=True,
    escape_underscores=False,
    escape_asterisks=False,
)


_local = threading.local()


def session() -> requests.Session:
    """One session per thread - requests.Session is not thread-safe."""
    if not hasattr(_local, "session"):
        _local.session = make_session()
    return _local.session


def make_session(retries: int = 5) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.9"})
    retry = Retry(
        total=retries,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
    return s


def parse_page(html: str, page_id: int) -> dict:
    """Extract one article from a rendered wiki.ffo.jp page."""
    soup = BeautifulSoup(html, "lxml")

    title_el = soup.select_one("h1.title")
    title = title_el.get_text(strip=True) if title_el else ""

    # The tooltip under the title holds "kana/English name" - the JP<->EN key.
    reading_el = soup.select_one("#offcanvasYomi .offcanvas-body")
    reading = reading_el.get_text(strip=True) if reading_el else ""

    # Breadcrumb doubles as the category path.
    categories = [a.get_text(strip=True) for a in soup.select(".breadcrumb a")]

    container = soup.select_one("div.container")
    if container is None:
        return {}

    for sel in DROP_SELECTORS:
        for el in container.select(sel):
            el.decompose()
    if title_el:
        title_el.decompose()
    for el in container.select(".breadcrumb, .bi"):
        el.decompose()

    # Section headers are styled divs, not heading tags.
    for level in ("h2", "h3", "h4"):
        for el in container.select(f"div.row.{level}"):
            el.name = level
            el.attrs = {}

    text = re.sub(r"\n{3,}", "\n\n", _md.convert_soup(container)).strip()

    return {
        "title": title,
        "pageid": page_id,
        "ns": 0,
        "url": f"{BASE}/html/{page_id}.html",
        "reading": reading,
        "categories": categories,
        "text": text,
    }


def list_pages(limit: int | None = None) -> list[int]:
    """Enumerate article IDs from the recent-changes index."""
    ids: dict[int, None] = {}
    for page_num in range(1, 10_000):
        resp = session().get(
            f"{BASE}/wiki.cgi",
            params={"Command": "ChangeList", "pageid": page_num},
            timeout=60,
        )
        resp.raise_for_status()
        found = PAGE_RE.findall(resp.text)
        if not found:
            break
        ids.update({int(i): None for i in found})
        print(f"  index page {page_num}: {len(ids)} ids", flush=True)
        if limit and len(ids) >= limit:
            break
        time.sleep(0.5)
    out = list(ids)
    return out[:limit] if limit else out


def fetch(page_id: int, delay: float) -> dict | None:
    try:
        resp = session().get(f"{BASE}/html/{page_id}.html", timeout=60)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  page {page_id}: {exc}", file=sys.stderr, flush=True)
        return None
    time.sleep(delay)
    article = parse_page(resp.text, page_id)
    return article or None


def load_done(path: Path) -> set[int]:
    done: set[int] = set()
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["pageid"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return done


def convert_dump(src: Path, dst: Path) -> None:
    """Re-parse an older raw-HTML dump into the current Markdown schema."""
    written = 0
    with src.open(encoding="utf-8") as fin, dst.open("w", encoding="utf-8") as fout:
        for line in fin:
            try:
                old = json.loads(line)
            except json.JSONDecodeError:
                continue
            article = parse_page(old["html"], int(old["page_id"]))
            if article:
                fout.write(json.dumps(article, ensure_ascii=False) + "\n")
                written += 1
    print(f"Converted {written} articles -> {dst}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Dump wiki.ffo.jp to JSONL")
    ap.add_argument("-o", "--output", default="ffo-wiki.jsonl")
    ap.add_argument("--delay", type=float, default=0.25, help="Per-worker delay between requests")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, help="Stop after N pages (smoke test)")
    ap.add_argument("--convert", metavar="OLD_DUMP", help="Convert a raw-HTML dump instead of scraping")
    args = ap.parse_args()

    out = Path(args.output)
    if args.convert:
        convert_dump(Path(args.convert), out)
        return

    done = load_done(out)
    print(f"Resuming with {len(done)} pages already dumped" if done else "Fresh dump")

    print("Enumerating pages...")
    page_ids = [i for i in list_pages(args.limit) if i not in done]
    print(f"{len(page_ids)} pages to fetch")

    written = 0
    with out.open("a", encoding="utf-8") as f, ThreadPoolExecutor(args.workers) as pool:
        for article in pool.map(lambda i: fetch(i, args.delay), page_ids):
            if not article:
                continue
            f.write(json.dumps(article, ensure_ascii=False) + "\n")
            written += 1
            if written % 500 == 0:
                f.flush()
                print(f"  {written}/{len(page_ids)}", flush=True)

    print(f"Wrote {written} articles -> {out}")


if __name__ == "__main__":
    main()
