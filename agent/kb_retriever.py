"""從 knowledge_base 找與 ticker 相關的概念與公司檔。

簡易策略 (MVP):
1. 公司檔: 用 ticker → 中英文名稱表查 03-公司檔案/ 中對應檔
2. 概念檔: 從觸發的規則 source_concept 反查 02-投資概念/
3. 不上 embedding;用檔名字串比對即可
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# 知識庫路徑
KB_ROOT = Path(__file__).resolve().parent.parent / "content"

# Ticker → 中文公司名映射(常見大型股)
# 用於從 03-公司檔案/ 找對應 .md
TICKER_TO_CN_NAME: dict[str, list[str]] = {
    "AAPL": ["蘋果"],
    "KO": ["可口可樂"],
    "AXP": ["美國運通"],
    "MCO": ["穆迪"],
    "BAC": ["美國銀行"],
    "WFC": ["富國銀行"],
    "CVX": ["雪佛龍"],
    "OXY": ["西方石油"],
    "KHC": ["卡夫亨氏"],
    "IBM": ["IBM"],
    "DIS": ["迪士尼"],
    "GS": ["高盛"],
    "MCD": ["麥當勞"],
    "JNJ": ["嬌生"],
    "PG": ["寶僑"],
    "V": ["VISA"],
    "MA": ["萬事達"],
    "GOOGL": ["谷歌", "Alphabet"],
    "ORCL": ["甲骨文"],
    "BRK": ["伯克希爾"],
    "BRK.B": ["伯克希爾"],
    "DVA": ["達維塔"],
    "KR": ["克羅格"],
    "COST": ["好市多"],
    "WMT": ["沃爾瑪"],
    "TSCO": ["樂購"],
    "BNSF": ["BNSF 鐵路"],
    "GEICO": ["GEICO 蓋可保險"],
}


@dataclass
class KBNode:
    title: str         # 檔案標題(來自 H1 或 frontmatter)
    path: str          # 相對於 KB_ROOT
    excerpt: str       # 前 300 字
    category: str      # "concept" / "company" / "letter" / ...

    @property
    def online_url(self) -> str:
        """Netlify 上對應 URL。"""
        from urllib.parse import quote
        rel = self.path.replace(".md", ".html")
        return f"https://buffetagent.netlify.app/{quote(rel)}"


def _read_excerpt(path: Path, max_chars: int = 300) -> tuple[str, str]:
    """回傳 (title, excerpt)。"""
    text = path.read_text(encoding="utf-8")
    # 跳過 frontmatter
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2]
    text = text.strip()
    # 找 H1
    m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    title = m.group(1).strip() if m else path.stem
    # 取前 N 字(跳過 H1 行)
    body = re.sub(r"^#\s+.+?\n", "", text, count=1, flags=re.M).strip()
    excerpt = body[:max_chars].strip()
    if len(body) > max_chars:
        excerpt += "…"
    return title, excerpt


def find_company_file(ticker: str) -> KBNode | None:
    """從 03-公司檔案/ 找對應 ticker 的檔案。"""
    cn_names = TICKER_TO_CN_NAME.get(ticker.upper(), [ticker.upper()])
    company_dir = KB_ROOT / "03-公司檔案"
    if not company_dir.exists():
        return None
    for name in cn_names + [ticker.upper()]:
        # 精確匹配
        for f in company_dir.iterdir():
            if not f.suffix == ".md":
                continue
            if f.stem == name or name in f.stem:
                title, excerpt = _read_excerpt(f)
                rel = f.relative_to(KB_ROOT).as_posix()
                return KBNode(title=title, path=rel, excerpt=excerpt, category="company")
    return None


def find_concept_files(concept_names: list[str]) -> list[KBNode]:
    """從 02-投資概念/ 找對應概念檔。輸入是檔名(中文名)列表。"""
    out = []
    seen = set()
    concept_dir = KB_ROOT / "02-投資概念"
    if not concept_dir.exists():
        return []
    for name in concept_names:
        if not name or name in seen:
            continue
        for f in concept_dir.iterdir():
            if f.suffix == ".md" and f.stem == name:
                title, excerpt = _read_excerpt(f)
                rel = f.relative_to(KB_ROOT).as_posix()
                out.append(KBNode(title=title, path=rel, excerpt=excerpt, category="concept"))
                seen.add(name)
                break
    return out


def find_relevant(ticker: str, source_concepts: list[str]) -> dict:
    """主入口:回傳 {company: KBNode|None, concepts: [KBNode], guidebook: KBNode|None}。"""
    company = find_company_file(ticker)
    concepts = find_concept_files(source_concepts)

    # 永遠附上總綱
    guidebook = None
    guide_path = KB_ROOT / "02-投資概念" / "巴菲特交易邏輯總綱.md"
    if guide_path.exists():
        title, excerpt = _read_excerpt(guide_path, max_chars=500)
        guidebook = KBNode(
            title=title,
            path=guide_path.relative_to(KB_ROOT).as_posix(),
            excerpt=excerpt,
            category="guidebook",
        )

    return {"company": company, "concepts": concepts, "guidebook": guidebook}
