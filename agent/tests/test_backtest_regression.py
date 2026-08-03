"""Regression detection 的 cadence bug 回歸測試（2026-08-03，同日 rayAgent 同款修法）。

Bug:偵測取「最近 3 筆成熟掃描」,週度設計時代 3 筆=3 週;2026-05-13 改每日掃描後
3 筆=3 個相鄰交易日(30d 窗口重疊 ~93%),誤報「連 3 週」。
修正後:按 ISO 週分組,最近 3 個有成熟資料的週「週平均 30d alpha」皆 < -5% 才觸發。
"""
from agent.backtest import aggregate, ScanBacktest, HorizonResult


def _scan(date: str, alpha: float) -> ScanBacktest:
    s = ScanBacktest(scan_date=date, buy_tickers=["X"] * 10)
    s.horizons[30] = HorizonResult(horizon_days=30, ready=True, days_elapsed=40,
                                   basket_return=alpha, benchmark_return=0.0,
                                   alpha=alpha, hit_rate=0.5)
    return s


def test_three_bad_days_same_week_do_not_trigger():
    """bug 最小重現:同一 ISO 週的 3 個連續交易日 < -5% ≠ 連 3 週,不得觸發。"""
    scans = ([_scan(f"2026-06-{d:02d}", 0.05) for d in range(1, 20)]
             # 2026-07-01(三)~07-03(五) 同屬 ISO 2026-W27
             + [_scan("2026-07-01", -0.08), _scan("2026-07-02", -0.10),
                _scan("2026-07-03", -0.09)])
    s = aggregate(scans)
    assert not s.regression_alert, "同週相鄰交易日不構成連續三週劣化"


def test_three_bad_weeks_trigger():
    """三個不同 ISO 週的週平均皆 < -5% → 必須觸發。"""
    # 2026-07-06/13/20 各為 W28/W29/W30 的週一
    scans = [_scan(f"2026-07-{d:02d}", -0.07) for d in (6, 7, 13, 14, 20, 21)]
    s = aggregate(scans)
    assert s.regression_alert
    assert s.consecutive_underperforming_weeks == 3


def test_recent_good_week_resets():
    """前兩週爛但最近週轉正 → 不觸發,consecutive 歸 0。"""
    scans = ([_scan("2026-07-06", -0.08), _scan("2026-07-13", -0.09)]
             + [_scan("2026-07-20", 0.02), _scan("2026-07-21", 0.03)])
    s = aggregate(scans)
    assert not s.regression_alert
    assert s.consecutive_underperforming_weeks == 0


def test_mixed_week_uses_week_mean():
    """週內好壞混雜以週平均判定:W30 平均 (-0.02-0.04)/2 = -0.03 > -5% → 不觸發。"""
    scans = ([_scan("2026-07-06", -0.06), _scan("2026-07-13", -0.07)]
             + [_scan("2026-07-20", -0.02), _scan("2026-07-21", -0.04)])
    s = aggregate(scans)
    assert not s.regression_alert
