# launchd jobs (Mac mini)

## com.buffetagent.notify-warroom

**作用**：每天 07:00 台北時間，git pull buffetAgent repo（拉 GitHub Actions 寫好的 `output/latest.json`），把巴菲特掃描結果摘要寫入 `war-room.db` lobby（role=`buffett_scan`）。

**為何在 Mac mini 跑**：war-room.db 在 Mac mini 本地（`~/autobot/war-room/data/war-room.db`），GitHub Actions 跑不到。整個 daily 流程是：

```
22:30 UTC  GitHub Actions: buffetAgent scan → 寫 output/*.json + commit + push
23:00 UTC  Mac mini launchd: git pull + notify_warroom.py → 寫入 war-room.db lobby
                              ↓
                              war-room chat UI 即時看到「📚 [Buffett] ...」卡片
```

## 安裝

```bash
# 1) 確認 venv 與 deps 存在
cd /Users/shawnclaw/autobot/agent/buffetAgent
[[ -d venv ]] || python3 -m venv venv
./venv/bin/pip install -q -r scripts/requirements.txt

# 2) 安裝 launchd job
cp deploy/launchd/com.buffetagent.notify-warroom.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.buffetagent.notify-warroom.plist

# 3) 手動測一次
launchctl kickstart -k gui/$(id -u)/com.buffetagent.notify-warroom
tail output/notify_warroom.log
```

## 解除安裝

```bash
launchctl unload ~/Library/LaunchAgents/com.buffetagent.notify-warroom.plist
rm ~/Library/LaunchAgents/com.buffetagent.notify-warroom.plist
```

## 故障排除

```bash
# 確認 plist 已載入
launchctl list | grep buffetagent

# 看錯誤 log
tail output/notify_warroom.error.log

# 確認 war-room.db 寫入成功
sqlite3 ~/autobot/war-room/data/war-room.db \
  "SELECT created_at, role, substr(content,1,80) FROM lobby WHERE role='buffett_scan' ORDER BY id DESC LIMIT 5;"
```
