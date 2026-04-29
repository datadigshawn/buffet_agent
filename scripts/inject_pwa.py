#!/usr/bin/env python3
"""一次性把 PWA + theme-color tags 注入到 simple-html/ 下所有 .html 檔的 <head>。

幂等:已注入過會跳過。
從子目錄 (例 04-人物檔案/foo.html) 注入時自動補上 ../ 前綴。
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
HTML_ROOT = ROOT / "simple-html"

MARKER = "<!-- PWA -->"

def pwa_tags(depth: int) -> str:
    prefix = "../" * depth
    return f"""{MARKER}
<link rel="manifest" href="{prefix}manifest.webmanifest">
<meta name="theme-color" content="#8b3a2f" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#1a1816" media="(prefers-color-scheme: dark)">
<link rel="icon" type="image/svg+xml" href="{prefix}icon.svg">
<link rel="apple-touch-icon" href="{prefix}icon.svg">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="巴菲特">
<meta name="mobile-web-app-capable" content="yes">"""


def inject(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return False
    depth = len(path.relative_to(HTML_ROOT).parts) - 1
    tags = pwa_tags(depth)
    new_text, n = re.subn(
        r"(<meta name=\"viewport\"[^>]*>)",
        r"\1\n" + tags,
        text,
        count=1,
    )
    if n == 0:
        new_text, n = re.subn(r"</head>", tags + "\n</head>", text, count=1)
    if n == 0:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def main() -> None:
    files = sorted(HTML_ROOT.rglob("*.html"))
    changed = sum(inject(p) for p in files)
    print(f"injected: {changed} / {len(files)}")


if __name__ == "__main__":
    main()
