"""Weekly Kraken spot crypto scan — 20-week average + Bitcoin filter, with volume checks.

Run:  python -m crypto.scan            (skips if this week is already done)
      python -m crypto.scan --force
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import config as C

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "crypto"
HERE = Path(__file__).resolve().parent
API = "https://api.kraken.com/0/public/"
UA = {"User-Agent": "trend-timing-crypto-scanner (personal use)"}
SPARK_WEEKS = 52


def _num(x, nd=6):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, nd) if abs(x) >= 1 else float(f"{x:.6g}")


# ── Kraken public API ─────────────────────────────────────────────────────────
def kraken(method: str, **params):
    for attempt in range(5):
        try:
            r = requests.get(API + method, params=params, headers=UA, timeout=30)
            j = r.json()
            if j.get("error"):
                msg = ";".join(j["error"])
                if "Too many requests" in msg or "Throttled" in msg:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise RuntimeError(msg)
            return j["result"]
        except (requests.RequestException, ValueError):
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Kraken {method} failed after retries")


def norm(code: str) -> str:
    return C.RENAME.get(code, code)


def list_pairs():
    """USD spot pairs for real crypto, plus which bases also have a GBP pair."""
    pairs = kraken("AssetPairs")
    usd, gbp = {}, set()
    for key, p in pairs.items():
        ws = p.get("wsname") or ""
        if "/" not in ws or p.get("status", "online") != "online" or key.endswith(".d"):
            continue
        base, quote = ws.split("/")
        base = norm(base)
        if quote == "GBP":
            gbp.add(base)
        if quote != C.QUOTE:
            continue
        if base in C.EXCLUDE or base in C.EXTRA_EXCLUDE or re.fullmatch(r"[A-Z0-9]+x", base):
            continue
        usd[base] = key
    return usd, gbp


def tickers():
    return kraken("Ticker")


def ohlc_daily(pair_key: str) -> pd.DataFrame:
    res = kraken("OHLC", pair=pair_key, interval=1440)
    rows = next(v for k, v in res.items() if k != "last")
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "vwap", "volume", "count"])
    df.index = pd.to_datetime(df.pop("t").astype(int), unit="s")
    df = df.astype(float)
    px = df["vwap"].where(df["vwap"] > 0, df["close"])
    df["usd_vol"] = px * df["volume"]
    return df


# ── analysis ──────────────────────────────────────────────────────────────────
def last_sunday(now: datetime) -> pd.Timestamp:
    d = now.date() - timedelta(days=1)          # the most recent fully closed day
    while d.weekday() != 6:
        d -= timedelta(days=1)
    return pd.Timestamp(d)


def weekly(df: pd.DataFrame, sunday: pd.Timestamp) -> pd.DataFrame:
    d = df[df.index <= sunday]                  # drops the uncommitted candle and anything after Sunday
    w = pd.DataFrame({"close": d["close"].resample("W-SUN").last(),
                      "usd_vol": d["usd_vol"].resample("W-SUN").sum()}).dropna(subset=["close"])
    w["sma"] = w["close"].rolling(C.SMA_WEEKS).mean()
    return w


def liq_tier(adv):
    for floor, name in C.LIQ_TIERS:
        if adv >= floor:
            return name
    return C.LIQ_TIERS[-1][1]


def volume_checks(df: pd.DataFrame, w: pd.DataFrame, sunday, tick):
    d = df[df.index <= sunday].iloc[-30:]
    adv30 = float(d["usd_vol"].mean()) if len(d) else 0.0
    zero_days = int((d["volume"] <= 0).sum())
    wv = w["usd_vol"]
    prev4 = wv.iloc[-5:-1].mean() if len(wv) >= 5 else np.nan
    prev20 = wv.iloc[-21:-1].mean() if len(wv) >= 21 else np.nan
    trend = wv.iloc[-1] / prev4 if prev4 and prev4 > 0 else None
    sig_ratio = wv.iloc[-1] / prev20 if prev20 and prev20 > 0 else None
    spread = None
    if tick:
        try:
            a, b = float(tick["a"][0]), float(tick["b"][0])
            spread = (a - b) / ((a + b) / 2) * 100 if a > 0 and b > 0 else None
        except (KeyError, IndexError, ValueError):
            pass
    tier = liq_tier(adv30)
    warn = []
    if tier in ("Thin", "Avoid"):
        warn.append("thin volume" if tier == "Thin" else "very low volume")
    if spread is not None and spread > C.MAX_SPREAD_PCT:
        warn.append("wide spread")
    if zero_days > C.MAX_ZERO_VOL_DAYS:
        warn.append(f"{zero_days} days with no trades")
    return dict(adv30=_num(adv30, 0), week_vol=_num(wv.iloc[-1], 0), vol_trend=_num(trend, 2),
                sig_vol_ratio=_num(sig_ratio, 2), spread=_num(spread, 3), zero_days=zero_days,
                liq=tier, vol_warn=warn)


def analyse(base, df, tick, btc_long: pd.Series, sunday, gbp: bool):
    w = weekly(df, sunday)
    if len(w) < C.SMA_WEEKS + 1 or pd.isna(w["sma"].iloc[-1]):
        return dict(base=base, status="TOO_NEW", weeks=len(w), gbp=gbp,
                    close=_num(w["close"].iloc[-1]) if len(w) else None,
                    **volume_checks(df, w, sunday, tick))
    above = w["close"] > w["sma"]
    btc = btc_long.reindex(w.index).fillna(False).astype(bool)
    long_ = (above & btc & w["sma"].notna())
    now, prev = bool(long_.iloc[-1]), bool(long_.iloc[-2])
    # when did the current state start?
    chg = long_.ne(long_.shift()).cumsum()
    start = long_[chg == chg.iloc[-1]].index[0]
    first_valid = w["sma"].first_valid_index()
    since_known = start > first_valid
    px_start = float(w.loc[start, "close"])
    close, sma = float(w["close"].iloc[-1]), float(w["sma"].iloc[-1])
    dist = (close / sma - 1) * 100

    if now:
        reason = "Coin and BTC both above their averages"
    elif not above.iloc[-1]:
        reason = "Coin below its average" + ("" if btc.iloc[-1] else " · BTC filter off")
    else:
        reason = "Coin above its average — blocked by BTC filter"
    fresh_buy, fresh_sell = now and not prev, prev and not now
    sell_why = None
    if fresh_sell:
        sell_why = " + ".join(x for x, hit in [("coin fell below average", not above.iloc[-1]),
                                               ("BTC filter turned off", not btc.iloc[-1])] if hit)

    r = np.log(df["close"][df.index <= sunday]).diff().iloc[-30:]
    vol_ann = float(r.std() * math.sqrt(365) * 100) if len(r) > 10 else None

    out = dict(
        base=base, gbp=gbp, status="LONG" if now else "CASH",
        fresh_buy=fresh_buy, fresh_sell=fresh_sell, sell_why=sell_why, reason=reason,
        blocked=bool(above.iloc[-1] and not btc.iloc[-1]),
        close=_num(close), sma=_num(sma), dist=_num(dist, 2), near=bool(abs(dist) <= C.WARN_PCT),
        slope="up" if sma > float(w["sma"].iloc[-2]) else "down",
        since=start.strftime("%d %b %Y") if since_known else None,
        weeks_in=int((w.index > start).sum()) if since_known else None,
        move_since=_num((close / px_start - 1) * 100, 2) if since_known else None,
        vol_ann=_num(vol_ann, 0), weeks=len(w),
        spark=dict(c=[_num(x) for x in w["close"].iloc[-SPARK_WEEKS:]],
                   s=[_num(x) for x in w["sma"].iloc[-SPARK_WEEKS:]],
                   l=[bool(x) for x in long_.iloc[-SPARK_WEEKS:]]),
    )
    out.update(volume_checks(df, w, sunday, tick))
    out["vol_confirms"] = (out["sig_vol_ratio"] is not None and out["sig_vol_ratio"] >= C.VOL_CONFIRM_RATIO
                           and out["liq"] != "Avoid")
    return out


# ── orchestration ─────────────────────────────────────────────────────────────
def run(force=False):
    now = datetime.now(timezone.utc)
    sunday = last_sunday(now)
    iso = sunday.isocalendar()
    tag = f"{iso.year}-W{iso.week:02d}"
    if not force and (OUT / "data" / f"{tag}.json").exists():
        print(f"crypto {tag} already scanned — nothing to do.")
        return None

    usd, gbp = list_pairs()
    tk = tickers()
    pair_tick = {base: tk.get(key) for base, key in usd.items()}

    def vol24(t):
        try:
            return float(t["v"][1]) * float(t["p"][1])
        except (TypeError, KeyError, IndexError, ValueError):
            return 0.0

    scan = {b: k for b, k in usd.items() if b == "BTC" or vol24(pair_tick.get(b)) >= C.MIN_24H_USD_TO_SCAN}
    skipped = sorted(set(usd) - set(scan))
    print(f"Kraken {C.QUOTE} pairs: {len(usd)} · scanning {len(scan)} · skipped (low 24h volume) {len(skipped)}")

    btc_df = ohlc_daily(usd["BTC"])
    bw = weekly(btc_df, sunday)
    btc_long = (bw["close"] > bw["sma"]) & bw["sma"].notna()

    rows, errors = [], []
    for i, (base, key) in enumerate(sorted(scan.items())):
        try:
            df = btc_df if base == "BTC" else ohlc_daily(key)
            rows.append(analyse(base, df, pair_tick.get(base), btc_long, sunday, base in gbp))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{base}: {str(e)[:120]}")
        if base != "BTC":
            time.sleep(C.PAUSE_S)
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(scan)}")

    rows.sort(key=lambda r: -(r.get("adv30") or 0))
    btc_row = next((r for r in rows if r["base"] == "BTC"), {})
    ok = [r for r in rows if r["status"] in ("LONG", "CASH")]
    btc_on, btc_prev = bool(btc_long.iloc[-1]), bool(btc_long.iloc[-2])
    payload = dict(
        week=tag, signal_date=sunday.strftime("%a %d %b %Y"), generated=now.isoformat(timespec="minutes"),
        btc=dict(on=btc_on, flipped=btc_on != btc_prev, close=btc_row.get("close"), sma=btc_row.get("sma"),
                 dist=btc_row.get("dist")),
        counts=dict(long=sum(r["status"] == "LONG" for r in ok), cash=sum(r["status"] == "CASH" for r in ok),
                    new_buy=sum(r["fresh_buy"] for r in ok), new_sell=sum(r["fresh_sell"] for r in ok),
                    blocked=sum(r["blocked"] for r in ok), too_new=sum(r["status"] == "TOO_NEW" for r in rows),
                    scanned=len(rows), pairs=len(usd), skipped=len(skipped)),
        config=dict(sma=C.SMA_WEEKS, warn_pct=C.WARN_PCT, max_spread=C.MAX_SPREAD_PCT,
                    min24=C.MIN_24H_USD_TO_SCAN, tiers=[[f, n] for f, n in C.LIQ_TIERS],
                    confirm=C.VOL_CONFIRM_RATIO, quote=C.QUOTE),
        rows=rows, skipped=skipped, errors=errors,
    )
    write_outputs(payload)
    print(f"crypto {tag}: {len(ok)} with signals · BUY {payload['counts']['new_buy']} · SELL {payload['counts']['new_sell']}"
          + (f" · errors {len(errors)}" if errors else ""))
    return payload


def render(payload, weeks):
    data = json.dumps({**payload, "weeks": weeks}, separators=(",", ":")).replace("</", "<\\/")
    return (HERE / "template.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", data)


def summary_md(p):
    c, b = p["counts"], p["btc"]
    rows = [r for r in p["rows"] if r["status"] in ("LONG", "CASH")]
    lines = [f"**Week ending {p['signal_date']}** · BTC filter **{'ON 🟢' if b['on'] else 'OFF 🔴'}**"
             + (" (changed this week!)" if b["flipped"] else "")
             + f" · LONG {c['long']} · CASH {c['cash']}", ""]
    buys = [r for r in rows if r["fresh_buy"]]
    sells = [r for r in rows if r["fresh_sell"]]
    if buys:
        lines += ["### 🟢 BUY signals", "", "| Coin | vs 20-wk avg | Avg daily vol (Kraken) | Volume | Liquidity |", "|---|---|---|---|---|"]
        lines += [f"| **{r['base']}** | {r['dist']:+.1f}% | ${(r['adv30'] or 0) / 1e6:.2f}M | "
                  f"{'✅ confirms' if r['vol_confirms'] else '⚠️ weak'} | {r['liq']} |" for r in buys]
    if sells:
        lines += ["", "### 🔴 SELL signals (move to cash)", ""]
        lines += [f"- **{r['base']}** — {r['sell_why']}" for r in sells]
    lines += ["", "_Education only — not advice. Check the live price and order book on Kraken before trading._"]
    return "\n".join(lines)


def write_outputs(payload):
    (OUT / "data").mkdir(parents=True, exist_ok=True)
    tag = payload["week"]
    (OUT / "data" / f"{tag}.json").write_text(json.dumps(payload, indent=1))
    weeks = sorted((p.stem for p in (OUT / "data").glob("????-W??.json")), reverse=True)
    html = render(payload, weeks)
    (OUT / f"{tag}.html").write_text(html, encoding="utf-8")
    (OUT / "index.html").write_text(html, encoding="utf-8")
    for wk in weeks[1:13]:                                   # refresh the picker on the last 12 weeks
        old = json.loads((OUT / "data" / f"{wk}.json").read_text())
        (OUT / f"{wk}.html").write_text(render(old, weeks), encoding="utf-8")
    s = HERE / "summary.md"
    c = payload["counts"]
    if c["new_buy"] or c["new_sell"] or payload["btc"]["flipped"]:
        s.write_text(summary_md(payload), encoding="utf-8")   # only email when something changed
    elif s.exists():
        s.unlink()
    print(f"Wrote docs/crypto/{tag}.html")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    run(ap.parse_args().force)
