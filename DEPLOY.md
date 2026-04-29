# 🌐 巴菲特知識庫 — 靜態網站打包

> 本資料夾包含**將 Obsidian vault 變成可分享靜態網站**的所有檔案。

## 📦 內容

```
buffett-site/
├── content/                    ← 你的 Obsidian vault 內容(180 個 .md 檔)
├── quartz-config-files/        ← Quartz 4 設定檔(2 個)
│   ├── quartz.config.ts       # 主設定(主題、外掛、字體)
│   └── quartz.layout.ts       # 版面(左右側邊欄、Graph view 設定)
├── simple-html/                ← 🆕 純 HTML 預覽版(無需 Node.js!)
│   ├── index.html             # 直接打開即可瀏覽
│   ├── 00-索引/                # MOC 索引
│   ├── 01-信件/                # 信件節點
│   └── ...                     # 共 176 個 HTML 頁面
└── README.md                   ← 你正在讀的這份
```

---

# 🚀 四種部署方式 — 任選一種

## 🎯 方式 0(最快):零依賴本機預覽(0 分鐘)

如果只想**先快速看看效果**:

```bash
# 直接打開
open /path/to/buffett-site/simple-html/index.html

# Windows: 用瀏覽器拖入 simple-html/index.html
# macOS/Linux: 直接雙擊 index.html
```

純 HTML、無需任何安裝。  
**特性**:
- ✅ 雙向連結都能點(wikilinks → HTML 連結)
- ✅ 反向連結(Backlinks)自動顯示
- ✅ 巴菲特紙紅 + 奧馬哈白主題色
- ✅ 自動切換深色 / 淺色模式
- ✅ 響應式(手機可看)
- ✅ 左側資料夾導覽
- ⚠️ 沒有 Graph view、沒有全文搜尋(那是 Quartz 才有的)

如果這就夠用了,**你已經有可分享的網站**!  
把 simple-html/ 整個資料夾傳給朋友、上傳到任何靜態主機(Cloudflare Pages、Netlify、Vercel、GitHub Pages)即可。

---

## 方式 A:本機 Quartz 預覽(5 分鐘,需 Node.js)

最簡單,可以**先在自己電腦看效果**:

```bash
# 1. 安裝 Node.js 22+(若還沒裝)
#    下載:https://nodejs.org/

# 2. 取得 Quartz
git clone https://github.com/jackyzha0/quartz.git
cd quartz
npm install

# 3. 把本資料夾的 content/ 複製到 quartz/content/
#    (把 quartz 自帶的 sample content 取代掉)
rm -rf content
cp -r /path/to/buffett-site/content ./content

# 4. 把設定檔覆蓋過去
cp /path/to/buffett-site/quartz-config-files/quartz.config.ts .
cp /path/to/buffett-site/quartz-config-files/quartz.layout.ts .

# 5. 啟動本機伺服器
npx quartz build --serve
```

打開瀏覽器訪問 `http://localhost:8080` 就能看到網站。  
修改 content/ 任何檔案,網站會自動 hot reload。

---

## 方式 B:免費部署到 GitHub Pages(推薦,30 分鐘)

完整步驟:

### B1. 建立 GitHub 帳號(若還沒有)
- 至 https://github.com 註冊

### B2. fork Quartz 倉庫
- 開 https://github.com/jackyzha0/quartz
- 右上角點 **Fork**
- fork 到自己帳號下,例如 `your-username/buffett-kb`

### B3. clone 到本機
```bash
git clone https://github.com/your-username/buffett-kb.git
cd buffett-kb
npm install
```

### B4. 替換 content/ 與設定
```bash
rm -rf content
cp -r /path/to/buffett-site/content ./content
cp /path/to/buffett-site/quartz-config-files/quartz.config.ts .
cp /path/to/buffett-site/quartz-config-files/quartz.layout.ts .
```

### B5. 修改 quartz.config.ts 中的 baseUrl
打開 `quartz.config.ts`,找到這行:
```ts
baseUrl: "buffett-kb.example.com",
```
改成 GitHub Pages 的 URL 格式:
```ts
baseUrl: "your-username.github.io/buffett-kb",
```

### B6. push 到 GitHub
```bash
git add .
git commit -m "Initial Buffett KB content"
git push
```

### B7. 啟用 GitHub Pages + Actions
- 進入你的 GitHub 倉庫頁面
- Settings → Pages → Source: **GitHub Actions**
- Actions → 應該已有 Quartz 的 workflow 在跑(若沒有,手動觸發)
- 第一次 build 約需 3-5 分鐘
- 完成後訪問:`https://your-username.github.io/buffett-kb`

🎉 你的個人專屬巴菲特知識網站上線。

---

## 方式 C:部署到 Cloudflare Pages / Netlify / Vercel(免費,最快)

這幾個服務都有「**讀取 GitHub repo 自動 build**」的功能:

### Cloudflare Pages
1. https://pages.cloudflare.com → 連接 GitHub
2. 選擇 fork 後的 quartz 倉庫
3. Build 設定:
   - Framework preset: **None**
   - Build command: `npx quartz build`
   - Build output directory: `public`
   - Node version: `22`

### Netlify
1. https://app.netlify.com → New site from Git
2. Build 設定同上(Build command + output 一致)

### Vercel
類似上述,Build 配置相同。

---

# 🎨 自訂主題色

`quartz.config.ts` 中的 `colors.lightMode` / `colors.darkMode` 是**巴菲特紙紅 + 奧馬哈白**配色。
若想改:

```ts
colors: {
  lightMode: {
    light: "#faf8f5",         // 背景
    dark: "#2a2521",          // 主文字
    secondary: "#8b3a2f",     // 連結色
    tertiary: "#b87333",      // 標題強調
    // ...
  }
}
```

每改一次 push,網站自動重 build。

---

# 🔧 常見問題

**Q: build 失敗,提示「too many files」?**  
A: Node 22+ 必要。若用 18 會失敗。

**Q: 中文字符在 URL 變亂碼?**  
A: 這是預期行為。Quartz 會把中文檔名轉成 `%E4%B8%AD%E6%96%87` 形式,可正常運作。

**Q: Graph view 太擁擠?**  
A: 修改 `quartz.layout.ts` 中 `globalGraph: { depth: -1 }` 改成 `depth: 2` 限制深度。

**Q: 想關掉某些檔案不發佈?**  
A: 在那個檔案的 YAML 加 `draft: true`。

**Q: 想加密 / 設密碼?**  
A: Quartz 本身不支援。可考慮 Cloudflare Pages 的 Access 功能,或 Obsidian Publish(收費)。

**Q: 我不想用 Quartz,只想要原始 vault?**  
A: 直接用 [上一輪打包的 zip](../buffett-kb-round4.zip),不用做這一步。Quartz 只是把它變成網站,**vault 本身已經完整可用**。

---

# 📚 替代方案

如果 Quartz 不合你味,還有這些選擇:

| 方案 | 特性 | 連結 |
|------|------|------|
| **Obsidian Publish** | 官方收費 $8/月,最簡單 | https://obsidian.md/publish |
| **Mkdocs + Material** | 文件型,搜尋強 | https://squidfunk.github.io/mkdocs-material/ |
| **Hugo + 任一 theme** | 部落格型 | https://gohugo.io |
| **Jekyll + GitHub Pages** | 老牌、穩定 | https://jekyllrb.com/ |

但對 Obsidian 風格的雙向連結 + Graph view,**Quartz 4 是目前最完整的免費方案**。

---

> 部署成功後,記得把網站連結傳給我看! 🎉
