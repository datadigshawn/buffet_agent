"""pytest 設定:確保 agent/ 所在目錄在 sys.path,並註冊 e2e marker。"""
from __future__ import annotations

import sys
from pathlib import Path

# 讓 `from agent import ...` 在任何位置都能 work
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: end-to-end tests requiring network/yfinance")
