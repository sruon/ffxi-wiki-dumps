"""Offline self-check: python test_scrapers.py"""

import scrape_ffo
from scrape_mediawiki import Wiki, strip_prefix

FFO_HTML = """<!DOCTYPE html><html lang="ja"><head><title>t</title>
<script>tracking()</script></head><body>
<div class="navbar"><a href="/wiki.cgi?Command=Search">menu</a></div>
<div class="container">
  <nav><ol class="breadcrumb">
    <li><a href="/wiki.cgi?Command=Category&amp;cid=1">ジョブ</a></li>
    <li><a href="/wiki.cgi?Command=Category&amp;cid=2">ジョブ特性</a></li>
  </ol></nav>
  <h1 class="title">デッドエイム <i class="bi bi-chat-text"></i></h1>
  <div class="offcanvas offcanvas-bottom" id="offcanvasYomi">
    <div class="offcanvas-body">でっどえいむ/Dead Aim</div></div>
  <div class="col"><a href="/html/255.html" title="狩人">狩人</a>の特性。<br>
  <table><thead>
    <tr><td rowspan="2">ランク</td><td>習得レベル</td></tr>
    <tr><td>狩</td></tr>
  </thead><tbody><tr><td>1</td><td>50</td></tr></tbody></table>
  </div>
  <div class="row h3"><div class="col-auto me-auto">関連装備
    <a href="/wiki.cgi?Command=Write&amp;id=1">edit</a></div></div>
  <div class="col">ダサバクロスボウ<img src="/images/x.png"></div>
  <hr class="comment">
</div>
<div id="footer">footer junk</div></body></html>"""


def test_ffo():
    page = scrape_ffo.parse_page(FFO_HTML, 23695)
    assert page["title"] == "デッドエイム", page["title"]
    assert page["reading"] == "でっどえいむ/Dead Aim"
    assert page["categories"] == ["ジョブ", "ジョブ特性"]
    assert page["url"] == "https://wiki.ffo.jp/html/23695.html"

    text = page["text"]
    for junk in ("footer junk", "menu", "tracking()", "edit", "ジョブ特性\n", "デッドエイム\n"):
        assert junk not in text, f"chrome leaked: {junk!r}\n{text}"
    assert "[狩人](/html/255.html)" in text, text          # link graph kept, title attr dropped
    assert "### 関連装備" in text, text                     # styled div -> real heading
    assert "| ランク | 習得レベル |" in text, text          # multi-row thead -> real header
    assert "| --- | --- |" in text, text
    assert "![" not in text, text                          # decorative images dropped


def fake_wiki(responses):
    wiki = object.__new__(Wiki)
    wiki.delay = 0
    wiki.server = "https://www.bg-wiki.com"
    wiki.articlepath = "/ffxi/$1"
    wiki.query = lambda **params: responses.pop(0)
    return wiki


def test_url_encoding():
    wiki = fake_wiki([])
    assert wiki.page_url("Adaman Hauberk") == "https://www.bg-wiki.com/ffxi/Adaman_Hauberk"
    assert wiki.page_url('"A" Egg') == "https://www.bg-wiki.com/ffxi/%22A%22_Egg"


def test_record():
    wiki = fake_wiki([])
    page = {
        "pageid": 1, "ns": 0, "title": "Joyeuse",
        "revisions": [{"revid": 9, "timestamp": "2024-01-01T00:00:00Z",
                       "slots": {"main": {"content": "{{item}}"}}}],
        "categories": [{"title": "Category:Sword"}, {"title": "Category:Item"}],
    }
    assert wiki.record(page) == {
        "title": "Joyeuse", "pageid": 1, "ns": 0,
        "url": "https://www.bg-wiki.com/ffxi/Joyeuse",
        "revid": 9, "timestamp": "2024-01-01T00:00:00Z",
        "categories": ["Item", "Sword"], "wikitext": "{{item}}",
    }
    # A page with no revision must not blow up the whole dump.
    assert wiki.record({"pageid": 2, "ns": 0, "title": "Empty"})["wikitext"] == ""


def test_resume_title():
    assert strip_prefix("Ballista: Points", 0) == "Ballista: Points"
    assert strip_prefix("Category:Acrolith", 14) == "Acrolith"


def test_split_category_batches():
    """Categories continued across responses must be merged, not lost."""
    wiki = fake_wiki([
        {"continue": {"clcontinue": "1|x"},
         "query": {"pages": [{"pageid": 1, "ns": 0, "title": "A",
                              "categories": [{"title": "Category:One"}]}]}},
        {"batchcomplete": True, "continue": {"gapcontinue": "B"},
         "query": {"pages": [{"pageid": 1, "ns": 0, "title": "A",
                              "categories": [{"title": "Category:Two"}]}]}},
        {"batchcomplete": True,
         "query": {"pages": [{"pageid": 2, "ns": 0, "title": "B"}]}},
    ])
    pages = list(wiki.query_pages(action="query"))
    assert [p["pageid"] for p in pages] == [1, 2], pages
    assert wiki.record(pages[0])["categories"] == ["One", "Two"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
