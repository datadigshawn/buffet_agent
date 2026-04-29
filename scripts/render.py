#!/usr/bin/env python3
"""把 content/ 的 Obsidian markdown 重新渲染成 simple-html/ 的靜態 HTML。

用法:
    python scripts/render.py              # 渲染到預設 simple-html/
    python scripts/render.py --out tmp/   # 渲染到指定目錄(用於測試)

依賴:
    pip install markdown python-frontmatter
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
from collections import defaultdict
from pathlib import Path

import frontmatter
import markdown as md_lib

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "content"
DEFAULT_DST = ROOT / "simple-html"
SKIP_DIRS = {"05-模板"}

SIDEBAR_GROUPS = [
    ("🗺️ 索引 MOC", "00-索引"),
    ("📜 合夥人信 (1957-1969)", "01-信件/合夥人時期_1957-1969"),
    ("📜 BH 股東信 (1977-2024)", "01-信件/伯克希爾時期_1977-2024"),
    ("💡 投資概念", "02-投資概念"),
    ("🏢 公司檔案", "03-公司檔案"),
    ("👥 人物", "04-人物檔案"),
]

CSS = """@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&family=Noto+Serif+TC:wght@500;700&display=swap');
:root{--bg:#faf8f5;--fg:#2a2521;--muted:#6c6258;--accent:#8b3a2f;--accent-2:#b87333;--line:#e5e0d4;--highlight:rgba(184,115,51,0.15);--code-bg:#f0ece3;}
@media (prefers-color-scheme: dark){:root{--bg:#1a1816;--fg:#ebe6db;--muted:#a09787;--accent:#d4815a;--accent-2:#e0a070;--line:#3a352e;--highlight:rgba(212,129,90,0.18);--code-bg:#2a2521;}}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;background:var(--bg);color:var(--fg);font-family:'Noto Sans TC',-apple-system,BlinkMacSystemFont,sans-serif;line-height:1.75;font-size:16px;}
.container{max-width:1100px;margin:0 auto;display:grid;grid-template-columns:220px 1fr;gap:30px;padding:30px 20px;}
@media (max-width: 768px){.container{grid-template-columns:1fr;padding:20px 15px;}.sidebar{position:static;max-height:none;}}
.sidebar{position:sticky;top:30px;align-self:start;max-height:calc(100vh - 60px);overflow-y:auto;font-size:13px;border-right:1px solid var(--line);padding-right:15px;}
.sidebar h3{font-family:'Noto Serif TC',serif;font-size:14px;margin:12px 0 6px;color:var(--accent);}
.sidebar a{display:block;color:var(--muted);text-decoration:none;padding:2px 0;font-size:12px;line-height:1.5;}
.sidebar a:hover{color:var(--accent);}
.sidebar a.current{color:var(--accent);font-weight:700;}
main{min-width:0;}
.breadcrumb{font-size:13px;color:var(--muted);margin-bottom:20px;padding-bottom:10px;border-bottom:1px solid var(--line);}
.breadcrumb a{color:var(--muted);text-decoration:none;}
.breadcrumb a:hover{color:var(--accent);}
h1,h2,h3,h4{font-family:'Noto Serif TC',serif;color:var(--fg);line-height:1.4;margin-top:1.6em;margin-bottom:0.6em;}
h1{font-size:28px;border-bottom:2px solid var(--accent);padding-bottom:8px;margin-top:0.5em;}
h2{font-size:22px;color:var(--accent);}
h3{font-size:18px;color:var(--accent-2);}
h4{font-size:16px;}
p{margin:0.8em 0;}
a{color:var(--accent);text-decoration:none;border-bottom:1px dotted var(--accent);}
a:hover{background:var(--highlight);}
.wikilink{color:var(--accent);background:var(--highlight);padding:2px 4px;border-radius:3px;border-bottom:none;}
.wikilink:hover{background:var(--accent-2);color:var(--bg);}
.wikilink-broken{color:var(--muted);text-decoration:line-through;border-bottom:none;}
ul,ol{padding-left:24px;}
li{margin:4px 0;}
blockquote{border-left:4px solid var(--accent-2);background:var(--highlight);padding:12px 16px;margin:16px 0;color:var(--fg);border-radius:0 4px 4px 0;}
blockquote p:first-child{margin-top:0;}
blockquote p:last-child{margin-bottom:0;}
code{font-family:'JetBrains Mono',Menlo,monospace;background:var(--code-bg);padding:1px 6px;border-radius:3px;font-size:0.92em;}
pre{background:var(--code-bg);padding:14px;overflow-x:auto;border-radius:4px;font-size:13px;border-left:3px solid var(--accent-2);}
pre code{background:none;padding:0;}
table{border-collapse:collapse;margin:16px 0;width:100%;font-size:14px;}
th,td{border:1px solid var(--line);padding:8px 12px;text-align:left;}
th{background:var(--code-bg);font-weight:700;color:var(--accent);}
hr{border:none;border-top:1px solid var(--line);margin:32px 0;}
strong{color:var(--fg);font-weight:700;}
em{color:var(--muted);font-style:italic;}
.backlinks{margin-top:60px;padding-top:20px;border-top:1px solid var(--line);font-size:14px;}
.backlinks h3{font-size:14px;color:var(--muted);margin-bottom:10px;font-family:'Noto Sans TC',sans-serif;font-weight:500;}
.backlinks ul{list-style:none;padding-left:0;}
.backlinks li{display:inline-block;margin:3px 5px 3px 0;}
.backlinks a{background:var(--code-bg);padding:3px 8px;border-radius:3px;font-size:12px;border-bottom:none;color:var(--muted);}
.backlinks a:hover{color:var(--accent);background:var(--highlight);}
footer{margin-top:60px;padding-top:20px;border-top:1px solid var(--line);text-align:center;font-size:12px;color:var(--muted);}
footer a{color:var(--muted);}"""

PWA_TAGS = """<!-- PWA -->
<link rel="manifest" href="{prefix}manifest.webmanifest">
<meta name="theme-color" content="#8b3a2f" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#1a1816" media="(prefers-color-scheme: dark)">
<link rel="icon" type="image/svg+xml" href="{prefix}icon.svg">
<link rel="apple-touch-icon" href="{prefix}icon.svg">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="巴菲特">
<meta name="mobile-web-app-capable" content="yes">"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
{pwa}
<title>{title} | 巴菲特股東信知識庫</title>
<style>
{css}
</style>
</head>
<body>
<div class="container">
<aside class="sidebar">
{sidebar}
</aside>
<main>
<div class="breadcrumb">{breadcrumb}</div>
{body}
{backlinks}
<footer>🏗️ 巴菲特股東信知識庫 · 自動產生 · <a href="https://www.berkshirehathaway.com/letters/letters.html" target="_blank">原信件來源</a></footer>
</main>
</div>
</body>
</html>
"""

WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")


class Page:
    __slots__ = ("src", "rel", "slug", "title", "body", "aliases")

    def __init__(self, src_path: Path):
        post = frontmatter.load(src_path)
        self.src = src_path
        self.rel = src_path.relative_to(SRC).with_suffix(".html")
        self.slug = src_path.stem
        self.body = post.content
        self.aliases = [str(a) for a in (post.metadata.get("aliases") or [])]
        m = re.search(r"^#\s+(.+?)\s*$", post.content, re.M)
        self.title = m.group(1).strip() if m else self.slug


def walk_pages() -> list[Page]:
    out: list[Page] = []
    for p in sorted(SRC.rglob("*.md")):
        rel = p.relative_to(SRC)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.name == "index.md":
            # 首頁 — special-case rel path
            page = Page(p)
            page.rel = Path("index.html")
            out.append(page)
        else:
            out.append(Page(p))
    return out


def build_index(pages: list[Page]) -> dict[str, Page]:
    idx: dict[str, Page] = {}
    for p in pages:
        for key in [p.slug, p.slug.lower(), *p.aliases, *(a.lower() for a in p.aliases)]:
            idx.setdefault(key, p)
    return idx


def parse_wikilink(raw: str) -> tuple[str, str | None, str]:
    target_part, _, display = raw.partition("|")
    display = display or target_part
    target, _, anchor = target_part.partition("#")
    return target.strip(), (anchor.strip() or None), display.strip()


def relhref(from_page: Page, to_page: Page) -> str:
    from_dir = from_page.rel.parent
    return os.path.relpath(to_page.rel, from_dir).replace(os.sep, "/")


def replace_wikilinks(body: str, page: Page, idx: dict[str, Page]) -> str:
    def sub(m: re.Match) -> str:
        target, anchor, display = parse_wikilink(m.group(1))
        tp = idx.get(target) or idx.get(target.lower())
        if tp is None:
            return f'<span class="wikilink-broken">{display}</span>'
        href = relhref(page, tp)
        if anchor:
            href += "#" + anchor
        return f'<a class="wikilink" href="{href}">{display}</a>'
    return WIKILINK_RE.sub(sub, body)


def collect_backlinks(pages: list[Page], idx: dict[str, Page]) -> dict[str, list[Page]]:
    bl: dict[str, list[Page]] = defaultdict(list)
    for sp in pages:
        seen: set[str] = set()
        for m in WIKILINK_RE.finditer(sp.body):
            target, _, _ = parse_wikilink(m.group(1))
            tp = idx.get(target) or idx.get(target.lower())
            if tp is None or tp.slug == sp.slug or tp.slug in seen:
                continue
            seen.add(tp.slug)
            bl[tp.slug].append(sp)
    return bl


def sidebar_html(pages: list[Page], current: Page) -> str:
    groups: dict[str, list[Page]] = defaultdict(list)
    for p in pages:
        rel_dir = str(p.rel.parent).replace("\\", "/")
        groups[rel_dir].append(p)

    home_href = ("../" * (len(current.rel.parts) - 1)) + "index.html"
    parts = [f'<h3><a href="{home_href}" style="font-weight:700;color:var(--accent);">📚 首頁</a></h3>']
    for label, dir_path in SIDEBAR_GROUPS:
        items = groups.get(dir_path, [])
        if not items:
            continue
        parts.append(f"<h3>{label}</h3>")
        for p in items:
            href = relhref(current, p)
            cls = ' class="current"' if p.slug == current.slug and p.rel == current.rel else ""
            parts.append(f'<a href="{href}"{cls}>{p.slug}</a>')
    return "".join(parts)


def breadcrumb_html(page: Page) -> str:
    home_href = ("../" * (len(page.rel.parts) - 1)) + "index.html"
    crumbs = [f'<a href="{home_href}">🏠 首頁</a>']
    for part in page.rel.parts[:-1]:
        crumbs.append(f"<span>{part}</span>")
    crumbs.append(f"<span>{page.slug}</span>")
    return " / ".join(crumbs)


def render_page(page: Page, pages: list[Page], idx, backlinks) -> str:
    depth = len(page.rel.parts) - 1
    prefix = "../" * depth
    body_md = replace_wikilinks(page.body, page, idx)
    html_body = md_lib.markdown(
        body_md,
        extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
        output_format="html5",
    )
    bl_items = backlinks.get(page.slug, [])
    if bl_items:
        lis = "".join(f'<li><a href="{relhref(page, sp)}">{sp.slug}</a></li>' for sp in bl_items)
        bl_html = f'<div class="backlinks"><h3>🔗 反向連結 ({len(bl_items)})</h3><ul>{lis}</ul></div>'
    else:
        bl_html = ""
    return PAGE_TEMPLATE.format(
        pwa=PWA_TAGS.format(prefix=prefix),
        title=page.title,
        css=CSS,
        sidebar=sidebar_html(pages, page),
        breadcrumb=breadcrumb_html(page),
        body=html_body,
        backlinks=bl_html,
    )


def copy_assets(dst: Path) -> None:
    """確保 manifest 與 icon 在輸出目錄。"""
    for fname in ("manifest.webmanifest", "icon.svg"):
        src = DEFAULT_DST / fname
        if src.exists() and dst != DEFAULT_DST:
            shutil.copy2(src, dst / fname)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DEFAULT_DST), help="輸出目錄(預設 simple-html/)")
    parser.add_argument("--clean", action="store_true", help="渲染前清空輸出目錄(保留 manifest/icon)")
    args = parser.parse_args()

    dst = Path(args.out).resolve()
    pages = walk_pages()
    idx = build_index(pages)
    backlinks = collect_backlinks(pages, idx)

    if args.clean and dst.exists():
        for child in dst.iterdir():
            if child.name in {"manifest.webmanifest", "icon.svg"}:
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    dst.mkdir(parents=True, exist_ok=True)
    copy_assets(dst)

    for p in pages:
        out_path = dst / p.rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_page(p, pages, idx, backlinks), encoding="utf-8")

    broken = sum(
        1
        for sp in pages
        for m in WIKILINK_RE.finditer(sp.body)
        if (lambda t: idx.get(t) or idx.get(t.lower()))(parse_wikilink(m.group(1))[0]) is None
    )
    print(f"✅ rendered {len(pages)} pages → {dst}")
    print(f"   backlinks for {len(backlinks)} nodes, {broken} broken wikilinks")


if __name__ == "__main__":
    main()
