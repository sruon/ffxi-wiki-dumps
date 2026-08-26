# ffxi-wiki-dumps

JSONL dumps of the three community FFXI wikis. Rebuilt every two weeks, published
as [release](../../releases) assets.

| File | Source | Body |
|---|---|---|
| `bg-wiki.jsonl` | [BG Wiki](https://www.bg-wiki.com) (ns 0) | wikitext |
| `bg-wiki-categories.jsonl` | BG Wiki (ns 14) | wikitext |
| `bg-wiki-redirects.jsonl` | BG Wiki | `{from, to}` |
| `ffxiclopedia.jsonl` | [FFXIclopedia](https://ffxiclopedia.fandom.com) (ns 0) | wikitext |
| `ffxiclopedia-categories.jsonl` | FFXIclopedia (ns 14) | wikitext |
| `ffxiclopedia-redirects.jsonl` | FFXIclopedia | `{from, to}` |
| `ffo-wiki.jsonl` | [wiki.ffo.jp](https://wiki.ffo.jp) (Japanese) | Markdown |

Category pages get their own file because both wikis put real content there:
bestiary tables, family resistances, equipment lists.

`ns` is the MediaWiki namespace, which is what the prefix in a page title means:

| ns | prefix | contents |
|---|---|---|
| 0 | none | articles, `Joyeuse` |
| 14 | `Category:` | category pages, `Category:Acrolith` |
| 10 | `Template:` | template definitions, not dumped |
| 6 | `File:` | image description pages, not dumped |

Only 0 and 14 are dumped. Every record carries its `ns`.

## Schema

One JSON object per line, UTF-8.

```json
{
  "title": "Joyeuse",
  "pageid": 12345,
  "ns": 0,
  "url": "https://www.bg-wiki.com/ffxi/Joyeuse",
  "revid": 696666,
  "timestamp": "2024-03-11T17:20:37Z",
  "categories": ["Sword", "Item"],
  "wikitext": "{{item\n|description=..."
}
```

FFO entries have `text` (Markdown) instead of `wikitext`, plus `reading`
(`kana/English name`, useful for JP↔EN lookups). Their `categories` come from the
breadcrumb. There's no `revid`/`timestamp`, the wiki doesn't expose them per page.

Wikitext is kept as-is. The templates are the structured data on these wikis
(`{{item|...}}`, `{{Bestiary Abilities Row|...}}`), so rendering to HTML or
stripping to prose loses more than it saves.

FFO has no API and serves Bootstrap pages, so those get converted to Markdown.
That keeps headings, data tables and internal links while dropping the ~70% of
each page that was nav, scripts and analytics.

## Usage

```bash
pip install -r requirements.txt

python scrape_mediawiki.py bg                      # -> bg-wiki*.jsonl
python scrape_mediawiki.py ffxiclopedia --delay 2
python scrape_ffo.py                               # -> ffo-wiki.jsonl

python scrape_mediawiki.py bg --limit 50           # smoke test
python test_scrapers.py                            # offline self-check
```

Both scrapers append and resume, so an interrupted run picks up where it stopped.
`scrape_ffo.py --convert old-dump.jsonl -o new.jsonl` re-parses an old raw-HTML
dump into the current schema with no network calls.

Defaults are deliberately unhurried: 1s between API requests with `maxlag=5`, and
8 threads at 0.25s for FFO. These are small community wikis. Recent full-run
times were 29 minutes for BG, 32 for FFXIclopedia and about 45 for FFO, which
fetches every page individually instead of 50 at a time through an API.

## Releases

`.github/workflows/corpus.yml` runs on the 1st and 15th, scrapes all three sources
in parallel, gzips them and cuts a dated release. Each release includes a
`manifest.json` with line counts, sizes and SHA-256 per file. Also runnable from
the Actions tab.

BG Wiki blocks datacenter IPs with a Cloudflare challenge, so that job needs a
self-hosted runner labelled `bg-scrape` on a connection the challenge lets
through. Check any candidate box with:

```bash
curl -s -o /dev/null -w '%{http_code}
' -A 'ffxi-wiki-dumps/1.0'   'https://www.bg-wiki.com/api.php?action=query&meta=siteinfo&siprop=general&format=json'
```

200 means it works, 403 means Cloudflare challenges it. The other two sources run
on GitHub-hosted runners.

## Caveats

These are community wikis: mistakes, stale mechanics and era confusion are all in
there. All three describe retail FFXI, which can be wrong for private servers at
older caps. Markers worth paying attention to: `{{verification}}`, `{{Question}}`,
and the `Information Needed` / `Verification` categories.

Content belongs to the wikis and their contributors (BG Wiki and FFXIclopedia are
CC-BY-SA). These dumps are redistribution for research and offline use. The code
here is MIT.
