"""Offline test for the crypto scanner with a fake Kraken API (no network).

    python -m tests.test_crypto_offline      -> writes a DEMO page into demo_crypto/
"""
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from crypto import config as C
from crypto import scan

rng = np.random.default_rng(5)
NOW = datetime.now(timezone.utc)
DAYS = pd.date_range(end=pd.Timestamp(NOW.date()), periods=720, freq="D")   # last one = today's open candle
COINS = {"XBT": (60000, .55, 2e9), "ETH": (3000, .7, 8e8), "SOL": (150, .9, 3e8), "XRP": (.6, .8, 2e8),
         "XDG": (.15, 1.0, 9e7), "ADA": (.5, .85, 4e7), "LINK": (15, .9, 2e7), "DOT": (6, .9, 6e6),
         "AAVE": (150, 1.0, 3e6), "PEPE": (1e-5, 1.3, 9e5), "TIA": (5, 1.2, 4e5), "OBSCURE": (2, 1.4, 1e5),
         "NEWCOIN": (1, 1.5, 2e6), "TINY": (0.3, 1.5, 5e3)}
PATHS = {}


def path(code, p0, vol, adv):
    r = rng.normal(rng.uniform(-.3, .6) / 365, vol / np.sqrt(365), len(DAYS))
    close = p0 * np.exp(np.cumsum(r))
    v = adv / close * rng.lognormal(0, .5, len(DAYS))
    if code == "OBSCURE":
        v[-25:-15] = 0                                   # gappy trading
    df = pd.DataFrame({"t": ((DAYS - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)).astype(int), "o": close, "h": close * 1.02,
                       "l": close * .98, "c": close, "vwap": close, "vol": v, "n": 100})
    if code == "NEWCOIN":
        df = df.iloc[-100:]                              # too new for a 20-week average
    return df


def fake_kraken(method, **p):
    if method == "AssetPairs":
        out = {f"{c}USD": {"wsname": f"{c}/USD", "status": "online"} for c in COINS}
        out.update({"USDTUSD": {"wsname": "USDT/USD"}, "AAPLxUSD": {"wsname": "AAPLx/USD"},
                    "XBTGBP": {"wsname": "XBT/GBP"}, "ETHGBP": {"wsname": "ETH/GBP"}})
        return out
    if method == "Ticker":
        out = {}
        for c, (p0, vol, adv) in COINS.items():
            last = PATHS[c]["c"].iloc[-1]
            spr = .0005 if adv > 1e7 else .006
            out[f"{c}USD"] = {"a": [str(last * (1 + spr / 2))], "b": [str(last * (1 - spr / 2))],
                              "v": ["0", str(adv / last)], "p": ["0", str(last)]}
        return out
    if method == "OHLC":
        c = p["pair"][:-3]
        return {p["pair"]: PATHS[c].astype(str).values.tolist(), "last": 0}
    raise ValueError(method)


def check_units():
    """Hand-built: BTC filter blocks a coin that is above its own average."""
    idx = pd.date_range("2024-01-07", periods=30, freq="W-SUN")
    sunday = idx[-1]
    days = pd.date_range(idx[0] - pd.Timedelta(days=6), sunday + pd.Timedelta(days=1), freq="D")

    def daily(weekly_closes):
        s = pd.Series(weekly_closes, idx).reindex(days).bfill().ffill()
        return pd.DataFrame({"close": s, "vwap": s, "volume": 1000.0, "usd_vol": s * 1000.0})
    coin = daily([10.0] * 25 + [12, 12, 12, 12, 12])               # clearly above its average
    btc_long = pd.Series([True] * 28 + [True, False], idx)        # BTC filter turns off at the last close
    r = scan.analyse("ABC", coin, None, btc_long, sunday, False)
    assert r["status"] == "CASH" and r["fresh_sell"] and r["blocked"], r
    assert r["sell_why"] == "BTC filter turned off", r["sell_why"]
    btc_long.iloc[-1] = True
    r = scan.analyse("ABC", coin, None, btc_long, sunday, False)
    assert r["status"] == "LONG" and not r["fresh_buy"] and r["weeks_in"] == 4, r
    # volume: last week double the normal -> ratio ~2
    coin["usd_vol"] = 1000.0
    coin.loc[coin.index > sunday - pd.Timedelta(days=7), "usd_vol"] *= 2
    w = scan.weekly(coin, sunday)
    v = scan.volume_checks(coin, w, sunday, None)
    assert 1.9 < v["sig_vol_ratio"] < 2.1, v
    assert scan.liq_tier(6e6) == "High" and scan.liq_tier(2e5) == "Avoid"


def main():
    check_units()
    for c, a in COINS.items():
        PATHS[c] = path(c, *a)
    scan.kraken = fake_kraken
    C.PAUSE_S = 0
    out = Path(__file__).resolve().parent.parent / "demo_crypto"
    scan.OUT = out
    orig = scan.write_outputs
    scan.write_outputs = lambda p: orig({**p, "demo": True})
    p = scan.run(force=True)

    bases = {r["base"] for r in p["rows"]}
    assert "BTC" in bases and "DOGE" in bases, bases               # renamed from XBT / XDG
    assert not ({"USDT", "AAPLx", "TINY"} & bases), bases          # stablecoin, stock token, low volume
    assert "TINY" in p["skipped"]
    assert next(r for r in p["rows"] if r["base"] == "NEWCOIN")["status"] == "TOO_NEW"
    ob = next(r for r in p["rows"] if r["base"] == "OBSCURE")
    assert any("no trades" in w for w in ob["vol_warn"]), ob["vol_warn"]
    assert next(r for r in p["rows"] if r["base"] == "BTC")["gbp"]
    btc_on = p["btc"]["on"]
    for r in p["rows"]:
        if r["status"] == "LONG":
            assert btc_on and r["close"] > r["sma"], r["base"]
        if r["status"] == "CASH" and btc_on:
            assert r["close"] <= r["sma"], r["base"]
    vols = [r["adv30"] for r in p["rows"]]
    assert vols == sorted(vols, reverse=True), "not sorted by volume"
    print(scan.summary_md(p))
    print(f"\nOK — {len(p['rows'])} coins, BTC filter {'ON' if btc_on else 'OFF'}")


if __name__ == "__main__":
    main()
