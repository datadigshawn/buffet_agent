# buffetAgent on Mac mini via launchd

這套部署沿用 `war-room` / `tradefox` 的模式：

- `cloudflared` 在 Mac mini 上維持 shared tunnel
- `buffetAgent` site 只 listen `127.0.0.1:8087`
- Cloudflare Tunnel 將 `buffetagent.shawny-project42.com` 轉到本機 `:8087`
- daily scan / weekly backtest 由 Mac mini launchd 跑

## Services

| Label | 作用 | 時間 |
|---|---|---|
| `com.buffetagent.site` | WSGI static site via gunicorn，serve `simple-html/` | 常駐 |
| `com.buffetagent.scan` | daily scan + 寫戰情室 lobby | Tue-Sat 06:30 台北 |
| `com.buffetagent.backtest` | weekly backtest page | Mon 07:00 台北 |
| `com.buffetagent.notify-warroom` | old notify-only job, should stay unloaded | 每日 07:00 台北 |

`notify-warroom` 是舊流程備援。正式切到 Mac mini scan 後，可停用它，避免重複推 lobby。

## 1. 準備 venv

```bash
cd /Users/shawnclaw/autobot/investing/agent/buffetAgent
[[ -d venv ]] || python3 -m venv venv
./venv/bin/pip install -q -r scripts/requirements.txt
```

可選 `.env`：

```bash
BUFFET_PUBLIC_BASE_URL=https://buffetagent.shawny-project42.com
OPENROUTER_API_KEY=...
BUFFET_LLM_BACKEND=openrouter
BUFFET_LLM_MODEL=minimax/minimax-m2.5
SEC_USER_AGENT=buffetAgent contact@datadigshawn.local
```

## 2. 本機驗證

```bash
cd /Users/shawnclaw/autobot/investing/agent/buffetAgent
venv/bin/python app.py --host 127.0.0.1 --port 8087
curl http://127.0.0.1:8087/api/health
curl -I http://127.0.0.1:8087/manifest.webmanifest
```

## 3. 安裝 launchd jobs

```bash
cd /Users/shawnclaw/autobot/investing/agent/buffetAgent
chmod +x scripts/run_daily_scan_macmini.sh scripts/run_daily_backtest_macmini.sh

cp deploy/launchd/com.buffetagent.site.plist ~/Library/LaunchAgents/
cp deploy/launchd/com.buffetagent.scan.plist ~/Library/LaunchAgents/
cp deploy/launchd/com.buffetagent.backtest.plist ~/Library/LaunchAgents/

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.buffetagent.site.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.buffetagent.scan.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.buffetagent.backtest.plist
```

驗證：

```bash
launchctl list | grep buffetagent
curl http://127.0.0.1:8087/api/health
```

## 4. Cloudflare Tunnel ingress

將 `deploy/cloudflared/buffetagent-ingress-snippet.yml` 內這段加到 `~/.cloudflared/config.yml` 的 `ingress:` 裡，放在 catch-all `http_status:404` 前面：

```yaml
- hostname: buffetagent.shawny-project42.com
  service: http://localhost:8087
```

重啟 shared tunnel 後驗證：

```bash
curl https://buffetagent.shawny-project42.com/api/health
curl -I https://buffetagent.shawny-project42.com/scan.html
```

## 5. 手動觸發

```bash
launchctl kickstart -k gui/$(id -u)/com.buffetagent.scan
launchctl kickstart -k gui/$(id -u)/com.buffetagent.backtest
```

也可以不經 launchd：

```bash
cd /Users/shawnclaw/autobot/investing/agent/buffetAgent
scripts/run_daily_scan_macmini.sh
scripts/run_daily_backtest_macmini.sh
```

## 6. 停用舊 notify-only job

正式切到 `com.buffetagent.scan` 後：

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.buffetagent.notify-warroom.plist
```

保留 plist 檔可當回滾備援。

## 7. Logs

```bash
tail -f output/site.log
tail -f output/site.error.log
tail -f output/scan.launchd.log
tail -f output/scan.launchd.error.log
tail -f output/backtest.launchd.log
tail -f output/backtest.launchd.error.log
```

確認 war-room lobby：

```bash
sqlite3 /Users/shawnclaw/autobot/investing/war-room/data/war-room.db \
  "SELECT created_at, role, substr(content,1,100) FROM lobby WHERE role LIKE 'buffett%' ORDER BY id DESC LIMIT 5;"
```
