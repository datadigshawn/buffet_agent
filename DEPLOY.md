# buffetAgent 部署說明

目前主部署方式是 **Mac mini + Gunicorn + Cloudflare Tunnel + launchd**。

公開網址：

- 知識庫首頁：`https://buffetagent.shawny-project42.com/`
- Buffett Scan：`https://buffetagent.shawny-project42.com/scan.html`
- Backtest：`https://buffetagent.shawny-project42.com/backtest.html`

## 主部署架構

```text
content/*.md / scan data
        |
        v
scripts/render.py / src/build_scan_html.py / scripts/run_backtest.py
        |
        v
simple-html/
        |
        v
app.py + gunicorn on 127.0.0.1:8087
        |
        v
cloudflared tunnel
        |
        v
https://buffetagent.shawny-project42.com
```

## 站台服務

站台由 `com.buffetagent.site` 常駐：

```bash
launchctl list | grep com.buffetagent
curl -s http://127.0.0.1:8087/api/health
```

對應設定：

- `app.py`
- `site_config.py`
- `deploy/launchd/com.buffetagent.site.plist`
- `deploy/cloudflared/buffetagent-ingress-snippet.yml`

## 排程

主要排程由 Mac mini launchd 負責：

| Label | 用途 | 時間 |
|---|---|---|
| `com.buffetagent.scan` | daily scan + war-room notify | 週二到週六 06:30 台北時間 |
| `com.buffetagent.backtest` | weekly backtest | 週一 07:00 台北時間 |

手動觸發：

```bash
launchctl kickstart -k gui/$(id -u)/com.buffetagent.scan
launchctl kickstart -k gui/$(id -u)/com.buffetagent.backtest
```

## Cloudflare Tunnel

`~/.cloudflared/config.yml` 需要在 catch-all 前包含：

```yaml
  # Buffett Agent static site
  - hostname: buffetagent.shawny-project42.com
    service: http://localhost:8087
```

套用後：

```bash
launchctl kickstart -k gui/$(id -u)/com.cloudflare.cloudflared
curl -s https://buffetagent.shawny-project42.com/api/health
```

## GitHub Actions

GitHub Actions 只保留 `workflow_dispatch` 手動備援，不再負責主要排程。

## 更新靜態知識庫

```bash
scripts/render.py
```

或使用：

```bash
./update.sh
```

`simple-html/` 產出後會立刻由 Mac mini 站台服務讀取。
