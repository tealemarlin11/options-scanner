"""Offline test for the futures scanner (fake data, no network).

    python -m tests.test_futures_offline     -> writes a DEMO page into demo_futures/
"""
from pathlib import Path

import numpy as np
import pandas as pd

from futures import contracts, scan

rng = np.random.default_rng(11)
TODAY = scan.datetime.now(scan.LDN).date()
IDX = pd.bdate_range(end=pd.Timestamp(TODAY), start="2004-01-01")


def walk(start, vol, drift=0.0, idx=IDX):
    r = rng.normal(drift / 252, vol / np.sqrt(252), len(idx))
    return pd.Series(start * np.exp(np.cumsum(r)), idx)


def fake_download(symbols, **_):
    cols = {s: walk(rng.uniform(1, 5000), rng.uniform(0.1, 0.45), rng.uniform(-0.2, 0.2)) for s in symbols}
    cols["UB=F"] = cols["UB=F"][cols["UB=F"].index > "2018-01-01"]           # short history
    del cols["PA=F"]                                                        # missing contract
    close = pd.DataFrame(cols)
    vol = pd.DataFrame({s: rng.integers(1000, 500000, len(close)).astype(float) for s in close})
    return pd.concat({"Close": close, "Volume": vol}, axis=1)


def fake_bbk(key):
    return (2.5 + walk(1, 0.25) - 1).clip(lower=-1)                       # yield-like, %


def fake_ecb(path):
    s = 3 + walk(1, 0.3) - 1
    m = s.resample("ME").mean()
    return m.iloc[:-2] if "IT" in path else m                               # Italy lags a month


def check_backtest():
    """Hand-built series: verify flips, P/L sign, and yield inversion."""
    idx = pd.date_range("2020-01-31", periods=14, freq="ME")
    m = pd.Series([10] * 10 + [12, 8, 9, 13], idx, dtype=float)
    sma = m.rolling(10).mean()
    trades, cur = scan.backtest(m, sma, "price")
    # month 10: 10 vs 10 -> SHORT (not >) first segment; 12 > 10.2 LONG; 8 < SHORT; 9 SHORT; 13 LONG
    assert [t["side"] for t in trades] == ["LONG", "SHORT"], trades
    assert trades[0]["pnl"] == (8 / 12 - 1) * 100            # long 12 -> 8 = -33%
    assert abs(trades[1]["pnl"] - (1 - 13 / 8) * 100) < 1e-9   # short 8 -> 13 = -62.5%
    assert cur["side"] == "LONG" and cur["entry"] == 13
    ty, cy = scan.backtest(m, sma, "yield")                    # yields: below avg = LONG
    assert [t["side"] for t in ty] == ["LONG"], ty           # first segment SHORT (no entry), LONG at 8, out at 13
    assert ty[0]["pnl"] == (8 - 13) * 100                       # yield rose 500bp -> long bond lost 500bp
    assert cy["side"] == "SHORT"
    assert scan.pnl("LONG", 3.0, 2.5, "yield") == 50.0         # yield fell 50bp -> long bond wins
    assert scan.pnl("SHORT", 3.0, 2.5, "yield") == -50.0


def main():
    check_backtest()
    scan.yf.download = fake_download
    scan.load_bbk, scan.load_ecb = fake_bbk, fake_ecb
    out = Path(__file__).resolve().parent.parent / "demo_futures"
    scan.OUT = out
    scan.HERE = Path(scan.__file__).resolve().parent
    orig = scan.write_outputs
    scan.write_outputs = lambda p: orig({**p, "demo": True})
    p = scan.run(force=True)

    ok = [r for r in p["rows"] if r.get("signal")]
    assert len(p["rows"]) == len(contracts.CONTRACTS)
    assert any(r["code"] == "PA" and not r.get("signal") for r in p["rows"]), "missing data should show as a row"
    assert any(r["code"] == "FBTP" and r["stale"] for r in ok), "lagging ECB series should be flagged stale"
    for r in ok:
        is_y = r["kind"] == "yield"
        long_ = (r["close"] < r["sma"]) if is_y else (r["close"] > r["sma"])
        assert (r["signal"] == "LONG") == long_, r["code"]
        if r["to_line"] is not None:
            c = next(c for c in contracts.CONTRACTS if c.code == r["code"])
            exp = abs(r["close"] - r["sma"]) * c.mult
            assert abs(r["to_line"] - exp) <= max(1, exp * 1e-6), (r["code"], r["to_line"], exp)
    assert len(p["errors"]) == 1, p["errors"]
    print(scan.summary_md(p))
    print(f"\nOK — {len(ok)} contracts with signals, {len(p['errors'])} failed (expected 1)")


if __name__ == "__main__":
    main()
