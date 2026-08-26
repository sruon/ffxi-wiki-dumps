# Brief: build a lookup skill over the FFXI wiki corpus

For an agent building a **net new** Claude Code skill. Nothing exists yet, do not
port anything. This document is the spec: what the data looks like, what the skill
has to do, and which rules about the data are not negotiable.

## 0. Deliverable

A Claude Code skill: a directory under `~/.claude/skills/<name>/` containing a
`SKILL.md` with `name` and `description` frontmatter, plus whatever helper scripts
it needs. The `description` is what decides whether the skill gets loaded at all,
so it has to enumerate the shapes of question this covers (what does X do, where
does Y drop, stats on Z, quest steps, mob weaknesses, drop rates, job traits,
crafting recipes) rather than describing the corpus.

## 1. The data

Published as release assets on `github.com/sruon/ffxi-wiki-dumps`. Each release is
a full re-scrape, tagged by date (`2026.08.26`), containing gzipped JSONL plus a
`manifest.json` with `{name, lines, bytes, sha256}` per file.

```bash
gh release download --repo sruon/ffxi-wiki-dumps \
  --pattern '*.jsonl.gz' --pattern manifest.json --dir <corpus-dir>
gunzip <corpus-dir>/*.gz
```

Omitting a tag gets the latest. Check each file against its `sha256` in
`manifest.json` before trusting it.

If no release exists yet, clone the repo and run the scrapers (see its README).
BG Wiki needs a residential IP, the other two do not.

**Do not read the working directory of that repo as a corpus.** A pre-rewrite set
of dumps may still be sitting there with the old schema: `html` instead of `text`
on FFO entries, `page_id` as a string, and wrong FFXIclopedia URLs. Release assets
are the only trustworthy source.

| File | Wiki | Body field | Rough size |
|---|---|---|---|
| `bg-wiki.jsonl` | BG Wiki, articles | `wikitext` | 47k lines, 93 MB |
| `bg-wiki-categories.jsonl` | BG Wiki, Category: pages | `wikitext` | 1.5k lines, 14 MB |
| `bg-wiki-redirects.jsonl` | BG Wiki | `{from, to}` only | 15k lines |
| `ffxiclopedia.jsonl` | FFXIclopedia, articles | `wikitext` | 53k lines, 123 MB |
| `ffxiclopedia-categories.jsonl` | FFXIclopedia, Category: pages | `wikitext` | 1.3k lines, 8 MB |
| `ffxiclopedia-redirects.jsonl` | FFXIclopedia | `{from, to}` only | 5.5k lines |
| `ffo-wiki.jsonl` | wiki.ffo.jp (Japanese) | `text` (Markdown) | 39k lines, ~170 MB |

Line counts are measured from a previous scrape and drift up slowly. The FFO size
is an estimate. Read `manifest.json` for the real numbers of whichever release you
pull.

One JSON object per line, UTF-8.

MediaWiki entries (BG, FFXIclopedia):

```json
{"title": "Joyeuse", "pageid": 12345, "ns": 0,
 "url": "https://www.bg-wiki.com/ffxi/Joyeuse",
 "revid": 696666, "timestamp": "2024-03-11T17:20:37Z",
 "categories": ["Sword", "Item"], "wikitext": "{{item\n|description=..."}
```

FFO entries:

```json
{"title": "デッドエイム", "pageid": 23695, "ns": 0,
 "url": "https://wiki.ffo.jp/html/23695.html",
 "reading": "でっどえいむ/Dead Aim",
 "categories": ["ジョブ", "ジョブ特性"],
 "text": "[狩人](/html/255.html)の[ジョブ特性](/html/450.html)の一つ。..."}
```

Things that will bite you:

- The body field differs by source: `wikitext` on MediaWiki entries, `text` on FFO.
- Category-file titles keep the `Category:` prefix, article `categories` lists do
  not. Cross-referencing them needs one strip or one prepend.
- FFO `reading` is `kana/English name`. That is the only systematic JP to EN name
  mapping in the corpus and it is worth indexing on its own. Not every page has
  the English half.
- FFO links are site-relative (`/html/255.html`). Resolve against
  `https://wiki.ffo.jp` before showing a user a link.
- FFO has no `revid`/`timestamp`.
- Redirect files are one direction only, `from` (alias) to `to` (real title).
  Building the reverse map is on you if you want "known aliases of X".

## 2. Trust hierarchy, not optional

These are community wikis. They contain mistakes, anecdote, and era confusion.
The skill must encode this ordering:

1. **FFO Wiki (JP)** is the primary reference. The Japanese community is closer to
   dev information and more rigorous about datamining. Prefer it even when it
   stands alone.
2. **BG Wiki** is a distant second. Good for retail mechanics in English. Defer to
   JP on conflicts.
3. **FFXIclopedia** is last. Legacy Fandom wiki, much of it never updated past the
   pre-Abyssea era. Use when the other two say nothing, or for old quest flows and
   classic-era data.

Confidence rule: JP plus at least one other wiki agreeing is high confidence. JP
alone still beats BG alone or FFXIclopedia alone. A single-wiki claim must be
reported as such ("BG says X, JP has no entry").

On conflict, JP wins, but say that the wikis disagree instead of silently picking.

## 3. Quality markers to surface

Present in the wikitext or categories, and the skill should mention them rather
than quietly passing the claim through:

- `{{verification}}` / `{{verify}}`: claim is flagged unverified
- `{{Question}}`: value unknown
- `Information Needed` or `Verification` in `categories`: page is incomplete

## 4. Era warning

All three wikis describe **retail** FFXI. For private server or era work (75 cap,
ToAU era, LSB), post-era job, ability and item data can be actively misleading.
When a query looks era-related, say so explicitly.

## 5. What the skill needs to do

Minimum surface:

- **Install / refresh**: pull the latest release, verify against `manifest.json`
  checksums, decompress. Should be idempotent and tell the user which release
  version is on disk.
- **Title lookup**: exact, case-insensitive, redirect-resolved, across all three
  sources or one.
- **Title search**: substring and regex.
- **Body search**: substring and regex inside `wikitext`/`text`, with a snippet of
  surrounding context.
- **Category listing**: pages in a given category.
- **JP to EN and EN to JP name lookup** via FFO `reading`. This is the piece that
  makes cross-wiki work possible and it did not exist before.

Every result must carry the source wiki name and the page URL. The user needs to
be able to verify.

## 6. Performance

Uncompressed the corpus is roughly 400 MB across seven files. A naive line scan
of `ffo-wiki.jsonl` is a few seconds, and an agent doing three lookups per
question will feel it.

Build an index on install. SQLite with FTS5 is in the Python standard library, one
file, no dependency, and gives title lookup, prefix search and full-text search in
one place. That is the obvious choice unless you have a better one. Whatever you
pick, keep the JSONL as the source of truth so a corrupted index can be rebuilt
without a re-download.

## 7. Output discipline

- Extract the answer, cite the page, do not dump raw wikitext at the user.
- Stats live in templates (`{{Infobox Weapon|...}}`, `{{item|...}}`,
  `{{Bestiary Abilities Row|...}}`). Pull parameters out of those for "what are
  the stats of X" questions.
- FFO bodies are Japanese. Translate the relevant part, do not paste the raw
  Japanese Markdown at an English-speaking user.
- Say when something was not found, rather than reaching for training data about
  FFXI. Training data is exactly what this corpus exists to replace.

## 8. Common content shapes

- Items: `{{Item|...}}`, `{{Infobox Weapon|...}}`, `{{Infobox Armor|...}}`
- Mobs: `{{Bestiary Description 2|...}}`, `{{Bestiary Abilities Row|...}}`,
  `{{Bestiary NM Row|...}}`, often on the **Category:** page, not the article
- Quests: `{{Quest|...}}` plus a `==Walkthrough==` section
- Missions: `{{Mission|...}}` with prereqs and rewards
- Spells and abilities: `{{Spell|...}}`, `{{Job Ability|...}}`

## 9. Non-goals

- Do not scrape. The repo handles that on a schedule.
- Do not normalise wikitext into a fixed schema up front. Template shapes vary
  per page type and per wiki, and any lossy normalisation will be wrong for some
  category of query. Parse on demand instead.
- Do not merge the three wikis into one answer without attribution.

## 10. Acceptance checks

The skill is working if these behave sensibly:

1. `Joyeuse` returns BG stats and the FFO page, cites both, notes agreement.
2. A redirect alias (for example `13 Knot Quipus`) resolves to the real page.
3. `Acrolith` finds the bestiary data even though it lives on the Category page.
4. `Dead Aim` finds `デッドエイム` through the FFO `reading` field.
5. A page carrying `{{verification}}` produces an answer that mentions the flag.
6. A BG-only claim is reported as BG-only, not stated as fact.
