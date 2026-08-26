"""
Dump a MediaWiki site to JSONL: article wikitext, category pages, redirects.

Wikitext is kept verbatim - templates are the structured data on these wikis,
so rendering to HTML or prose would throw away the part worth having.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = "ffxi-wiki-dumps/1.0 (+https://github.com/sruon/ffxi-wiki-dumps)"

WIKIS = {
    "bg": ("https://www.bg-wiki.com", "bg-wiki"),
    "ffxiclopedia": ("https://ffxiclopedia.fandom.com", "ffxiclopedia"),
}

# prop=revisions with content caps at 50 pages per request regardless of limit.
BATCH = 50


class Wiki:
    def __init__(self, base_url: str, delay: float = 1.0, retries: int = 5):
        self.api = base_url.rstrip("/") + "/api.php"
        self.delay = delay
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self.session.mount(
            "https://",
            HTTPAdapter(
                max_retries=Retry(
                    total=retries,
                    backoff_factor=2,
                    status_forcelist=(429, 500, 502, 503, 504),
                    allowed_methods=("GET",),
                    respect_retry_after_header=True,
                )
            ),
        )
        info = self.query(action="query", meta="siteinfo", siprop="general")["query"]["general"]
        server = info["server"]
        self.server = "https:" + server if server.startswith("//") else server
        self.articlepath = info["articlepath"]

    def query(self, **params) -> dict:
        params.update(format="json", formatversion=2, maxlag=5)
        while True:
            resp = self.session.get(self.api, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            error = data.get("error")
            if not error:
                return data
            if error.get("code") == "maxlag":
                print(f"  maxlag, backing off: {error.get('info')}", file=sys.stderr, flush=True)
                time.sleep(10)
                continue
            raise RuntimeError(f"API error: {error}")

    def query_pages(self, **params):
        """Yield pages from a generator query, merging continued category lists.

        With a generator, `cllimit` applies to the whole batch, so a page's
        categories can arrive split across several responses. MediaWiki signals
        a finished batch with `batchcomplete`; only then is a page complete.
        """
        buf: dict[int, dict] = {}
        cont: dict = {}
        while True:
            data = self.query(**params, **cont)
            for page in data.get("query", {}).get("pages", []):
                seen = buf.get(page["pageid"])
                if seen is None:
                    buf[page["pageid"]] = page
                else:
                    seen.setdefault("categories", []).extend(page.get("categories", []))
            if data.get("batchcomplete"):
                yield from buf.values()
                buf = {}
            cont = data.get("continue", {})
            if not cont:
                break
            time.sleep(self.delay)
        yield from buf.values()

    def page_url(self, title: str) -> str:
        path = self.articlepath.replace("$1", quote(title.replace(" ", "_"), safe="/:!$&'()*,;=@~-._"))
        return urljoin(self.server, path)

    def record(self, page: dict) -> dict:
        revision = (page.get("revisions") or [{}])[0]
        content = revision.get("slots", {}).get("main", {}).get("content", "")
        return {
            "title": page["title"],
            "pageid": page["pageid"],
            "ns": page["ns"],
            "url": self.page_url(page["title"]),
            "revid": revision.get("revid"),
            "timestamp": revision.get("timestamp"),
            "categories": sorted({c["title"].split(":", 1)[-1] for c in page.get("categories", [])}),
            "wikitext": content,
        }

    def dump_pages(self, out: Path, namespace: int = 0, limit: int | None = None) -> int:
        """Dump one namespace, resuming from the last title already written."""
        done, resume_from = read_progress(out)
        if resume_from:
            print(f"Resuming {out.name} after {resume_from!r} ({len(done)} pages)")

        params = dict(
            action="query",
            generator="allpages",
            gapnamespace=namespace,
            gaplimit=BATCH,
            gapfilterredir="nonredirects",
            prop="revisions|categories",
            rvprop="content|ids|timestamp",
            rvslots="main",
            cllimit="max",
            clshow="!hidden",
        )
        if resume_from:
            # allpages is title-sorted, so the last title written is where to pick up.
            params["gapfrom"] = strip_prefix(resume_from, namespace)

        written = 0
        with out.open("a", encoding="utf-8") as f:
            for page in self.query_pages(**params):
                if page["pageid"] in done:
                    continue
                f.write(json.dumps(self.record(page), ensure_ascii=False) + "\n")
                done.add(page["pageid"])
                written += 1
                if written % 500 == 0:
                    f.flush()
                    print(f"  {written} pages", flush=True)
                if limit and written >= limit:
                    break
        print(f"Wrote {written} pages -> {out}")
        return written

    def dump_redirects(self, out: Path, namespace: int = 0, limit: int | None = None) -> int:
        """Dump redirect -> target pairs, so aliases resolve offline."""
        titles: list[str] = []
        cont: dict = {}
        while True:
            data = self.query(
                action="query",
                list="allpages",
                apnamespace=namespace,
                aplimit="max",
                apfilterredir="redirects",
                **cont,
            )
            titles += [p["title"] for p in data["query"]["allpages"]]
            cont = data.get("continue", {})
            if not cont or (limit and len(titles) >= limit):
                break
            time.sleep(self.delay)
        titles = titles[:limit] if limit else titles
        print(f"Resolving {len(titles)} redirects...")

        written = 0
        with out.open("w", encoding="utf-8") as f:
            for i in range(0, len(titles), BATCH):
                data = self.query(action="query", titles="|".join(titles[i:i + BATCH]), redirects=1)
                for hop in data.get("query", {}).get("redirects", []):
                    f.write(json.dumps({"from": hop["from"], "to": hop["to"]}, ensure_ascii=False) + "\n")
                    written += 1
                time.sleep(self.delay)
        print(f"Wrote {written} redirects -> {out}")
        return written


def strip_prefix(title: str, namespace: int) -> str:
    """gapfrom wants a title without its namespace prefix - but ns 0 titles may
    contain a colon of their own ("Ballista: Points"), so only strip off ns 0."""
    return title.split(":", 1)[-1] if namespace else title


def read_progress(out: Path) -> tuple[set[int], str | None]:
    """Return already-dumped page ids and the last title written."""
    done: set[int] = set()
    last: str | None = None
    if not out.exists():
        return done, last
    with out.open(encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            done.add(entry["pageid"])
            last = entry["title"]
    return done, last


def main() -> None:
    ap = argparse.ArgumentParser(description="Dump a MediaWiki site to JSONL")
    ap.add_argument("wiki", choices=sorted(WIKIS), help="Preset wiki to dump")
    ap.add_argument("--out-dir", default=".", type=Path)
    ap.add_argument("--delay", type=float, default=1.0, help="Seconds between API requests")
    ap.add_argument("--limit", type=int, help="Stop after N pages per file (smoke test)")
    ap.add_argument("--skip", nargs="*", default=[], choices=["pages", "categories", "redirects"])
    args = ap.parse_args()

    base_url, prefix = WIKIS[args.wiki]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    wiki = Wiki(base_url, delay=args.delay)

    if "pages" not in args.skip:
        wiki.dump_pages(args.out_dir / f"{prefix}.jsonl", namespace=0, limit=args.limit)
    if "categories" not in args.skip:
        # Category pages carry real content on these wikis (bestiary data, lists).
        wiki.dump_pages(args.out_dir / f"{prefix}-categories.jsonl", namespace=14, limit=args.limit)
    if "redirects" not in args.skip:
        wiki.dump_redirects(args.out_dir / f"{prefix}-redirects.jsonl", limit=args.limit)


if __name__ == "__main__":
    main()
