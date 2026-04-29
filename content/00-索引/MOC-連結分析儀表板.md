---
type: moc
title: 📊 連結分析儀表板
created: 2026-04-29
tags: [MOC, dataview, 分析]
---

# 📊 連結分析儀表板

> 這份檔案利用 **Dataview 外掛**(社群外掛)動態查詢整個 vault 的結構性指標。
> **要看到表格資料**,請先安裝 Obsidian 社群外掛 **Dataview**。
> 若沒安裝 Dataview,本檔下方的「**靜態分析報告**」段落仍可正常閱讀。

## ⚙️ 啟用 Dataview

1. Obsidian → 設定 → 社群外掛(Community plugins)→ 啟用
2. 瀏覽(Browse)→ 搜尋「Dataview」→ 安裝 → 啟用
3. 設定中啟用 「Enable JavaScript Queries」(可選)
4. 回到本檔,所有 ` ```dataview ` 區塊會自動變成可互動表格

---

## 🌟 1. 最中央的概念(被引用最多)

```dataview
TABLE 
  length(file.inlinks) AS "入連",
  length(file.outlinks) AS "出連",
  default(first_appearance_year, "—") AS "首見年份"
FROM "02-投資概念"
SORT length(file.inlinks) DESC
LIMIT 15
```

## 🏢 2. 最被引用的公司

```dataview
TABLE 
  length(file.inlinks) AS "入連",
  default(sector, "—") AS "行業",
  default(status_in_portfolio, "—") AS "持倉狀態"
FROM "03-公司檔案"
SORT length(file.inlinks) DESC
LIMIT 15
```

## 👥 3. 人物的影響力

```dataview
TABLE 
  length(file.inlinks) AS "入連",
  length(file.outlinks) AS "出連"
FROM "04-人物檔案"
SORT length(file.inlinks) DESC
```

## ⭐ 4. 經典必讀信件(⭐⭐⭐)

```dataview
TABLE 
  year AS "年份",
  importance AS "重要性",
  length(file.inlinks) AS "被引用"
FROM "01-信件"
WHERE contains(string(importance), "⭐⭐⭐")
SORT year ASC
```

## 📅 5. 全部信件按年份(時間軸)

```dataview
TABLE 
  year AS "年",
  default(importance, "—") AS "重要性",
  length(file.inlinks) AS "入連"
FROM "01-信件"
SORT year ASC
```

## 🔗 6. 弱連結的公司(可補強)

```dataview
TABLE
  length(file.inlinks) AS "入連",
  default(sector, "—") AS "行業"
FROM "03-公司檔案"
WHERE length(file.inlinks) <= 3
SORT length(file.inlinks) ASC
```

## 🚀 7. 出度最高的探索型節點

```dataview
TABLE
  length(file.outlinks) AS "出連",
  length(file.inlinks) AS "入連"
FROM "02-投資概念" OR "03-公司檔案" OR "04-人物檔案"
SORT length(file.outlinks) DESC
LIMIT 15
```

---

# 📜 靜態分析報告(無需 Dataview)

> 以下為 Round 4 執行時拍攝的快照,即使沒有 Dataview 也可閱讀。
> 動態統計請看上方 Dataview 區塊。

## TOP 10 最中央節點

| 排名 | 節點 | 類別 | 入連 |
|------|------|------|------|
| 1 | [[資本配置]] | 概念 | 68 |
| 2 | [[失誤與認錯]] | 概念 | 49 |
| 3 | [[安全邊際]] | 概念 | 48 |
| 4 | [[長期持有]] | 概念 | 45 |
| 5 | [[經理人選擇]] | 概念 | 44 |
| 6 | [[誠信與品格]] | 概念 | 36 |
| 7 | [[護城河]] | 概念 | 30 |
| 8 | [[喜詩糖果]] | 公司 | 27 |
| 9 | [[市場先生]] | 概念 | 27 |
| 10 | [[能力圈]] | 概念 | 26 |

**觀察**:概念類節點佔據前 7 名——這驗證了**概念是知識城堡的真正脊椎**,公司與信件圍繞概念旋轉。

## 跨類別連結密度

|  從 ↓  →  到 | 概念 | 公司 | 人物 | BH 信 | BPL 信 | MOC |
|-------------|------|------|------|--------|---------|------|
| **概念** | 162 | 99 | 21 | 1 | 0 | 0 |
| **公司** | 203 | 82 | 15 | 31 | 1 | 0 |
| **人物** | 20 | 15 | 14 | 1 | 0 | 0 |
| **BH 信** | 170 | 62 | 11 | 97 | 1 | 0 |
| **BPL 信** | 40 | 15 | 1 | 1 | 24 | 0 |
| **MOC** | 36 | 63 | 7 | 48 | 13 | 4 |

**觀察**:
- **公司 → 概念(203)**和**BH 信 → 概念(170)**是兩條最密的單向流——所有具體事物都指回抽象原則
- **概念 → 公司(99)**也很高——概念用具體案例支撐
- 這構成**「**抽象 ↔ 具體**」的完整雙向迴路**
- BH 信彼此之間互聯密度高(97),代表時間軸內有強連動性

## 全 vault 健康度

| 指標 | 數值 |
|------|------|
| 總檔案 | 178 |
| 總有向連結 | 1,278 |
| 平均出度 | 7.18 |
| 平均入度 | 7.18 |
| 最高入連節點 | [[資本配置]](68) |

> **業界經驗值**:Obsidian vault 平均連結密度約 3-5,**這個 vault 達 7.18**,屬高密度知識網。

## 弱連結節點(待補強)

### 概念類
- [[投資 vs 投機]](入 4)
- [[效率市場理論的批判]](入 5)
- [[股票即企業所有權]](入 5)
- [[機會成本]](入 6)
- [[透視盈餘]](入 6)

### 公司類(僅 1 個入連)
- [[麥當勞]]、[[嬌生]]、[[肖氏工業]]、[[百威英博]]、[[NetJets]]、[[克萊頓房屋]]、[[內布拉斯加家具城]]

### BH 信(僅 1-2 個入連)
- [[1967-伯克希爾股東信]](早期 stub)
- [[1976-伯克希爾股東信]](早期 stub)

## 連結補強紀錄

詳見 [[連結補強紀錄]] —— Round 4 自動補強的清單。
