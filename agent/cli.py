"""命令列入口:python -m agent AAPL"""
from __future__ import annotations

import argparse
import json
import sys

from . import verdict as verdict_mod
from . import screener


def cmd_single(ticker: str, as_json: bool = False) -> int:
    v = verdict_mod.evaluate(ticker)
    if as_json:
        print(json.dumps(v.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(v.rationale_md)
    return 0


def cmd_watchlist(as_json: bool = False) -> int:
    scores = screener.score_watchlist()
    scores.sort(key=lambda s: s.total, reverse=True)
    if as_json:
        print(json.dumps([
            {
                "ticker": s.ticker, "bias": s.bias, "total": s.total,
                "base": s.base, "bonus": s.bonus,
                "triggered_disqualifier": s.triggered_disqualifier,
            } for s in scores
        ], indent=2, ensure_ascii=False))
    else:
        print(f"{'Ticker':<10}{'Bias':<18}{'Score':<8}Reason")
        print("-" * 80)
        for s in scores:
            reason = s.triggered_disqualifier or ""
            if not reason:
                passed = sum(1 for r in s.rule_results if r.passed)
                total_rules = len(s.rule_results)
                reason = f"{passed}/{total_rules} rules passed"
            print(f"{s.ticker:<10}{s.bias:<18}{s.total:<8}{reason}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description="Buffett-style stock evaluator")
    parser.add_argument("ticker", nargs="?", help="ticker symbol (e.g. AAPL)")
    parser.add_argument("--watchlist", action="store_true", help="run all stockTracker watchlist tickers")
    parser.add_argument("--json", action="store_true", help="output JSON")
    args = parser.parse_args(argv)

    if args.watchlist:
        return cmd_watchlist(as_json=args.json)
    if not args.ticker:
        parser.print_help()
        return 1
    return cmd_single(args.ticker, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
