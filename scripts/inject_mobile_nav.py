#!/usr/bin/env python3
"""把手機抽屜式導覽 (hamburger menu) 注入到所有 simple-html/*.html。

策略:
  - <body> 後插入 checkbox + label 按鈕 + backdrop (純 CSS hack, 免 JS 即可開關)
  - </style> 前追加 mobile drawer CSS (≤768px 才生效, 桌機/平板維持原樣)
  - </body> 前加極簡 JS, 點 sidebar 連結後自動關閉抽屜 (僅 anchor 內頁跳轉用)

幂等:已注入過會跳過。
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
HTML_ROOT = ROOT / "simple-html"
MARKER = "<!-- MOBILE_NAV -->"

CSS_BLOCK = """
/* ===== mobile drawer nav (≤768px) ===== */
.nav-toggle, .nav-toggle-btn, .nav-backdrop { display: none; }
@media (max-width: 768px) {
  .nav-toggle-btn {
    display: flex; align-items: center; justify-content: center;
    position: fixed; top: 12px; right: 12px; z-index: 110;
    width: 44px; height: 44px; border-radius: 8px;
    background: var(--accent); color: var(--bg);
    font-size: 22px; cursor: pointer; user-select: none;
    box-shadow: 0 2px 8px rgba(0,0,0,0.25);
  }
  .nav-toggle-btn::before { content: "\\2630"; }
  .nav-toggle:checked ~ .nav-toggle-btn::before { content: "\\2715"; }

  .container { padding: 60px 16px 20px; grid-template-columns: 1fr; }

  .sidebar {
    position: fixed; top: 0; left: 0;
    width: 82%; max-width: 320px; height: 100vh;
    background: var(--bg); z-index: 105;
    transform: translateX(-100%);
    transition: transform 0.25s ease;
    padding: 60px 18px 20px; padding-right: 12px;
    border-right: 1px solid var(--line);
    box-shadow: 2px 0 12px rgba(0,0,0,0.2);
    max-height: none; overflow-y: auto;
  }
  .nav-toggle:checked ~ .container .sidebar { transform: translateX(0); }

  .nav-backdrop {
    display: block; position: fixed; inset: 0;
    background: rgba(0,0,0,0.4); z-index: 100;
    opacity: 0; pointer-events: none;
    transition: opacity 0.25s ease;
  }
  .nav-toggle:checked ~ .nav-backdrop { opacity: 1; pointer-events: auto; }

  body.nav-open { overflow: hidden; }
}
"""

BODY_INSERT = f"""{MARKER}
<input type="checkbox" id="nav-toggle" class="nav-toggle" aria-label="\u5207\u63db\u5c0e\u89bd">
<label for="nav-toggle" class="nav-toggle-btn" aria-label="\u958b\u95dc\u5c0e\u89bd"></label>
<label for="nav-toggle" class="nav-backdrop" aria-hidden="true"></label>"""

JS_BLOCK = """<script>
(function(){
  var t = document.getElementById('nav-toggle');
  if (!t) return;
  document.querySelectorAll('.sidebar a').forEach(function(a){
    a.addEventListener('click', function(){ t.checked = false; });
  });
  t.addEventListener('change', function(){
    document.body.classList.toggle('nav-open', t.checked);
  });
})();
</script>
"""


def inject(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return False
    new_text, n1 = re.subn(r"</style>", CSS_BLOCK + "\n</style>", text, count=1)
    if not n1:
        return False
    new_text, n2 = re.subn(r"<body>", "<body>\n" + BODY_INSERT, new_text, count=1)
    if not n2:
        return False
    new_text, n3 = re.subn(r"</body>", JS_BLOCK + "</body>", new_text, count=1)
    if not n3:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def main() -> None:
    files = sorted(HTML_ROOT.rglob("*.html"))
    changed = sum(inject(p) for p in files)
    print(f"injected mobile nav: {changed} / {len(files)}")


if __name__ == "__main__":
    main()
