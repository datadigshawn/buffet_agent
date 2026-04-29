# 📚 buffet_agent

巴菲特股東信知識庫靜態網站 — 從 Obsidian vault 打包而成，部署於 Netlify，可在桌機與手機隨時查閱。

---

## 📂 目錄結構

```
buffet_agent/
├── README.md                   本文件
├── DEPLOY.md                   完整部署選項說明（Quartz / GitHub Pages / Netlify / Vercel）
├── netlify.toml                Netlify 部署設定（publish = simple-html）
├── .gitignore
│
├── simple-html/                🌐 Netlify publish 目錄（純靜態 HTML，已 RWD）
│   ├── index.html              首頁
│   ├── 00-索引/                MOC 索引
│   ├── 01-信件/                巴菲特股東信節點
│   ├── 02-投資概念/            投資概念筆記
│   ├── 03-公司檔案/            公司檔案
│   └── 04-人物檔案/            人物檔案
│
├── content/                    📝 Obsidian vault 原始 .md 來源（180 個檔案）
│   ├── 00-索引/ … 05-模板/
│   └── index.md
│
└── quartz-config-files/        ⚙️ Quartz 4 設定檔（如未來改用 Quartz 部署可用）
    ├── quartz.config.ts
    └── quartz.layout.ts
```

---

## 🚀 部署到 Netlify（手機可隨時查閱）

採與 [`stockTracker`](../../stockTracker) 相同的 Netlify 靜態部署模式。

### 一次性設定

1. **建立 GitHub repo**

   ```bash
   cd /Users/apple/Projects/agentS/buffet_agent
   git init
   git add .
   git commit -m "Initial commit: buffet knowledge base"
   gh repo create buffet_agent --public --source=. --push
   ```

2. **連接 Netlify**
   - 前往 <https://app.netlify.com/start>
   - 選擇 GitHub → 授權後選 `buffet_agent` repo
   - Build settings 會自動讀 `netlify.toml`：
     - Publish directory：`simple-html`
     - Build command：（無，純靜態）
   - 點 **Deploy site**，1–2 分鐘後即上線

3. **取得網址**
   - 預設網址：`https://<random-name>.netlify.app`
   - 可在 Site settings → Change site name 改成 `buffet-agent.netlify.app`

### 之後更新流程

```bash
# 修改 simple-html/ 內容後
git add simple-html/
git commit -m "Update content"
git push
# Netlify 偵測到 push 自動重新部署
```

---

## 📱 手機查閱

部署後直接用手機瀏覽器開 Netlify 網址即可。`simple-html/` 已內建 RWD（`@media (max-width: 768px)`），左側資料夾導覽會自動收摺。

建議：在 iOS Safari 點 **分享 → 加到主畫面**，當作 PWA 圖示快速開啟。

---

## 🔧 本機預覽

```bash
cd /Users/apple/Projects/agentS/buffet_agent
open simple-html/index.html        # 直接開瀏覽器
# 或啟動本機伺服器避免 file:// 限制
python3 -m http.server -d simple-html 8000
# 然後訪問 http://localhost:8000
```

---

## 🔄 從 Obsidian 同步新內容

`content/` 是 Obsidian vault 來源，`simple-html/` 是已渲染的成品。  
若要在 Obsidian 編修後重新渲染，可改走 Quartz 流程（見 [`DEPLOY.md`](./DEPLOY.md)）並把產出覆蓋 `simple-html/`。

---

## 📖 進階部署選項

完整的四種部署方式（本機 Quartz / GitHub Pages / Cloudflare Pages / Netlify）與主題色客製化，請見 [`DEPLOY.md`](./DEPLOY.md)。
