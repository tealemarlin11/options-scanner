"""Offline end-to-end test with a fake yfinance (no network needed).

    python -m tests.test_offline        -> writes a DEMO report into demo/
"""
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from scanner import bs, config, report, scan

rng = np.random.default_rng(7)
TODAY = scan.datetime.now(scan.NY).date()
TICKERS = ["SPY", "QQQ", "IWM", "XLE", "GLD", "TLT", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA",
           "JPM", "XOM", "UNH", "HD", "KO", "PFE", "INTC", "NKE", "DIS", "BA", "CAT", "AMD", "COST"]
ETFS = {"SPY", "QQQ", "IWM", "XLE", "GLD", "TLT"}
VOLS = {t: v for t, v in zip(TICKERS, rng.uniform(0.14, 0.55, len(TICKERS)))}
PATHS = {}


def _path(t):
    idx = pd.bdate_range(end=pd.Timestamp(TODAY), periods=520)
    drift = rng.uniform(-0.35, 0.45)
    ret = rng.normal(drift / 252, VOLS[t] / np.sqrt(252), len(idx))
    return pd.Series(rng.uniform(40, 700) * np.exp(np.cumsum(ret)), idx)


def fake_download(symbols, **_):
    closes, divs = {}, {}
    for s in symbols:
        p = PATHS.setdefault(s, _path(s) if s != "^IRX" else pd.Series(4.1, pd.bdate_range(end=pd.Timestamp(TODAY), periods=520)))
        closes[s] = p
        d = pd.Series(0.0, p.index)
        if s in ("KO", "XOM", "JPM", "PFE", "SPY"):
            d.iloc[::63] = p.iloc[::63] * 0.008
        divs[s] = d
    return pd.concat({"Close": pd.DataFrame(closes), "Dividends": pd.DataFrame(divs)}, axis=1)


class FakeTicker:
    def __init__(self, sym):
        self.sym = sym
        self.S = PATHS[sym].iloc[-1]

    @property
    def options(self):
        d = TODAY + timedelta(days=1)
        out = []
        while len(out) < 12:
            if d.weekday() == 4:
                out.append(d.isoformat())
            d += timedelta(days=1)
        return tuple(out)

    def option_chain(self, exp):
        T = (date.fromisoformat(exp) - TODAY).days / 365
        step = scan.width_for(self.S) / 2
        ks = np.arange(round(self.S * 0.6 / step) * step, self.S * 1.4, step)
        base = VOLS[self.sym] * rng.uniform(0.9, 1.35)

        def side(kind):
            rows = []
            for k in ks:
                iv = base * (1 + 0.9 * max(0, np.log(self.S / k)) if kind == "put" else 1)
                px = bs.price(kind, self.S, k, T, 0.041, 0, iv)
                spr = max(0.01, px * rng.uniform(0.03, 0.18))
                bid = max(0.0, px - spr / 2)
                rows.append(dict(strike=k, bid=round(bid, 2), ask=round(px + spr / 2, 2), lastPrice=round(px, 2),
                                 volume=float(rng.integers(0, 5000)) * (3 if self.sym in ETFS else 1),
                                 openInterest=float(rng.integers(0, 20000)), impliedVolatility=iv))
            return pd.DataFrame(rows)
        return SimpleNamespace(puts=side("put"), calls=side("call"))

    @property
    def calendar(self):
        if self.sym in ETFS:
            return {}
        return {"Earnings Date": [TODAY + timedelta(days=int(rng.integers(5, 90)))],
                "Ex-Dividend Date": TODAY + timedelta(days=int(rng.integers(1, 60)))}


def main():
    scan.yf.download = fake_download
    scan.yf.Ticker = FakeTicker
    scan.universe.load = lambda: pd.DataFrame({"symbol": TICKERS, "name": [t + " Inc" for t in TICKERS],
                                               "sector": ["ETF" if t in ETFS else "Demo" for t in TICKERS],
                                               "is_etf": [t in ETFS for t in TICKERS]})
    out = Path(__file__).resolve().parent.parent / "demo"
    scan.DOCS, scan.ROOT = out, out
    config.PAUSE_S = 0
    orig = scan.write_outputs
    scan.write_outputs = lambda p: orig({**p, "demo": True})
    p = scan.run(force=True)
    assert p and p["trades"], "no trades produced"

    # sanity checks on every trade
    for t in p["trades"]:
        assert 0 < t["credit"] < t["width"], t
        assert abs(t["max_loss"] + t["max_profit"] - t["width"] * 100) < 1e-6
        assert config.DTE_MIN <= t["dte"] <= config.DTE_MAX
        assert 0 < t["pop"] < 100
        assert t["theta"] > 0, "credit spread should earn theta"
        if t["kind"] == "put":
            assert t["long_strike"] < t["short_strike"] < t["spot"]
            assert t["breakeven"] < t["short_strike"]
        else:
            assert t["spot"] < t["short_strike"] < t["long_strike"]
    vols = [t["opt_volume"] for t in p["trades"]]
    assert vols == sorted(vols, reverse=True), "not sorted by volume"
    print(report.summary_md(p))
    print(f"\nOK — {len(p['trades'])} trades, all checks passed")


if __name__ == "__main__":
    main()
