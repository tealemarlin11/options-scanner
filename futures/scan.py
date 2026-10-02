"""Monthly futures trend scan — Faber 10-month average, long above / short below.

Separate from the options scanner: own data, own page (docs/futures/), own workflow.

Run:  python -m futures.scan            (skips if this month is already done)
      python -m futures.scan --force
"""
from __future__ import annotations

import argparse
import io
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

from .contracts import CONTRACTS, GROUP_ORDER, SMA_MONTHS, WARN_BP, WARN_PCT

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "futures"
HERE = Path(__file__).resolve().parent
LDN = ZoneInfo("Europe/London")
UA = {"User-Agent": "Mozilla/5.0 (futures-trend-scanner; personal use)"}
SPARK_MONTHS = 36


def _num(x, nd=6):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, nd) if abs(x) >= 1 else float(f"{x:.6g}")   # keep precision on tiny FX quotes (6J)


# ── data sources ──────────────────────────────────────────────────────────────
def load_yahoo(symbols: list[str]):
    data = yf.download(symbols, period="max", interval="1d", auto_adjust=False,
                       group_by="column", threads=True, progress=False)
    close, vol = data["Close"], data.get("Volume")
    if isinstance(close, pd.Series):          # single symbol edge case
        close, vol = close.to_frame(symbols[0]), (vol.to_frame(symbols[0]) if vol is not None else None)
    return close, vol


def _sdmx_csv(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text), sep=None, engine="python")
    cols = {c.upper(): c for c in df.columns}
    t, v = cols.get("TIME_PERIOD"), cols.get("OBS_VALUE")
    if not t or not v:
        raise ValueError(f"unexpected columns {list(df.columns)[:8]}")
    s = pd.Series(pd.to_numeric(df[v], errors="coerce").values,
                  index=pd.to_datetime(df[t].astype(str)), dtype=float).dropna().sort_index()
    return s[~s.index.duplicated(keep="last")]


def load_bbk(key: str) -> pd.Series:
    url = f"https://api.statistiken.bundesbank.de/rest/data/BBSIS/{key}"
    r = requests.get(url, params={"startPeriod": "1999-01-01"}, timeout=60,
                     headers={**UA, "Accept": "application/vnd.sdmx.data+csv;version=1.0.0"})
    r.raise_for_status()
    return _sdmx_csv(r.content.decode("utf-8-sig"))


def load_ecb(path: str) -> pd.Series:
    r = requests.get(f"https://data-api.ecb.europa.eu/service/data/{path}",
                     params={"format": "csvdata", "startPeriod": "1999-01"}, headers=UA, timeout=60)
    r.raise_for_status()
    s = _sdmx_csv(r.text)
    s.index = s.index + pd.offsets.MonthEnd(0)       # monthly periods -> month-end
    return s


# ── signal + history ──────────────────────────────────────────────────────────
def completed_monthly(s: pd.Series, month_start: pd.Timestamp) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").dropna()
    s.index = pd.to_datetime(s.index, utc=True).tz_convert(None)   # tolerate tz-aware / object indexes
    return s[s.index < month_start].resample("ME").last().dropna()


def backtest(m: pd.Series, sma: pd.Series, kind: str):
    """Always-in long/short at monthly closes. Returns (closed trades, open trade)."""
    long_ = (m < sma) if kind == "yield" else (m > sma)
    valid = sma.notna()
    trades, cur = [], None
    for dt in m.index[valid]:
        side = "LONG" if long_[dt] else "SHORT"
        px = float(m[dt])
        if cur is None:
            cur = {"side": side, "entry": px, "t": dt, "first": True}
        elif side != cur["side"]:
            if not cur["first"]:                     # first segment has no real entry signal
                trades.append({**cur, "exit": px, "exit_t": dt, "pnl": pnl(cur["side"], cur["entry"], px, kind)})
            cur = {"side": side, "entry": px, "t": dt, "first": False}
    return trades, cur


def pnl(side, entry, exit_, kind):
    if kind == "yield":                              # basis points in your favour
        bp = (entry - exit_) * 100
        return bp if side == "LONG" else -bp
    r = (exit_ / entry - 1) * 100                    # % in your favour
    return r if side == "LONG" else -r


def _stats(trades, side):
    t = [x["pnl"] for x in trades if x["side"] == side]
    if not t:
        return dict(n=0, win=None, avg=None)
    return dict(n=len(t), win=_num(sum(p > 0 for p in t) / len(t) * 100, 1), avg=_num(sum(t) / len(t), 2))


def analyse(c, m: pd.Series, vol: pd.Series | None, expected_month: str) -> dict:
    sma = m.rolling(SMA_MONTHS).mean()
    if sma.notna().sum() < 2:
        raise ValueError(f"only {len(m)} months of data")
    close, avg, avg_prev = float(m.iloc[-1]), float(sma.iloc[-1]), float(sma.iloc[-2])
    prev_close = float(m.iloc[-2])
    is_y = c.kind == "yield"
    long_now = close < avg if is_y else close > avg
    long_prev = prev_close < avg_prev if is_y else prev_close > avg_prev
    dist = (close - avg) * 100 if is_y else (close / avg - 1) * 100       # bp or %
    near = abs(dist) <= (WARN_BP if is_y else WARN_PCT)

    trades, cur = backtest(m, sma, c.kind)
    months_in = int((m.index > cur["t"]).sum()) if not cur["first"] else None
    open_pnl = pnl(cur["side"], cur["entry"], close, c.kind) if not cur["first"] else None

    mom = None
    if len(m) > 12:
        mom = (close - float(m.iloc[-13])) * 100 if is_y else (close / float(m.iloc[-13]) - 1) * 100
    mom_agrees = None if mom is None else ((mom < 0) if is_y else (mom > 0)) == long_now
    slope_up = avg > avg_prev
    slope_agrees = (not slope_up if is_y else slope_up) == long_now

    tail = m.iloc[-SPARK_MONTHS:]
    vol20 = None
    if vol is not None and vol.dropna().size:
        vol20 = _num(vol.dropna().iloc[-20:].mean(), 0)

    last_month = m.index[-1].strftime("%Y-%m")
    return dict(
        code=c.code, name=c.name, exch=c.exch, group=c.group, src=c.src, sym=c.sym, proxy=c.proxy,
        kind=c.kind, ccy=c.ccy,
        signal="LONG" if long_now else "SHORT", fresh=bool(long_now != long_prev), near=bool(near),
        close=_num(close), sma=_num(avg), dist=_num(dist, 2), unit="bp" if is_y else "%",
        slope="up" if slope_up else "down", slope_agrees=bool(slope_agrees),
        mom12=_num(mom, 2), mom_agrees=mom_agrees,
        since=None if cur["first"] else cur["t"].strftime("%b %Y"), months_in=months_in,
        entry=_num(cur["entry"]) if not cur["first"] else None, open_pnl=_num(open_pnl, 2),
        to_line=_num(abs(close - avg) * c.mult, 0) if (c.mult and not is_y) else None,
        vol20=vol20,
        long=_stats(trades, "LONG"), short=_stats(trades, "SHORT"),
        history_from=str(m.index[0].year), last_month=m.index[-1].strftime("%b %Y"),
        stale=last_month != expected_month,
        spark=dict(c=[_num(x, 4) for x in tail], s=[_num(x, 4) for x in sma.iloc[-SPARK_MONTHS:]]),
    )


# ── orchestration ─────────────────────────────────────────────────────────────
def run(force=False):
    now = datetime.now(LDN)
    today = now.date()
    tag = today.strftime("%Y-%m")
    if not force and (OUT / "data" / f"{tag}.json").exists():
        print(f"futures {tag} already scanned — nothing to do.")
        return None

    month_start = pd.Timestamp(today.replace(day=1))
    expected = (month_start - pd.Timedelta(days=1)).strftime("%Y-%m")
    ysyms = [c.sym for c in CONTRACTS if c.src == "yahoo"]
    close, vol = load_yahoo(ysyms)

    rows, errors = [], []
    for c in CONTRACTS:
        try:
            if c.src == "yahoo":
                if c.sym not in close or close[c.sym].dropna().empty:
                    raise ValueError("no data from Yahoo")
                m = completed_monthly(close[c.sym], month_start)
                v = vol[c.sym] if vol is not None and c.sym in vol else None
            elif c.src == "bbk":
                m, v = completed_monthly(load_bbk(c.sym), month_start), None
            elif c.src == "ecb":
                m, v = completed_monthly(load_ecb(c.sym), month_start), None
            else:
                raise ValueError(f"unknown source {c.src}")
            rows.append(analyse(c, m, v, expected))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{c.code}: {e}")
            rows.append(dict(code=c.code, name=c.name, exch=c.exch, group=c.group, proxy=c.proxy,
                             src=c.src, kind=c.kind, signal=None, error=str(e)[:160]))

    order = {c.code: i for i, c in enumerate(CONTRACTS)}
    rows.sort(key=lambda r: (GROUP_ORDER.index(r["group"]), order[r["code"]]))
    ok = [r for r in rows if r.get("signal")]
    payload = dict(
        month=tag, generated=now.isoformat(timespec="minutes"),
        signal_month=pd.Timestamp(expected + "-01").strftime("%b %Y"),
        counts=dict(long=sum(r["signal"] == "LONG" for r in ok), short=sum(r["signal"] == "SHORT" for r in ok),
                    new_long=sum(r["signal"] == "LONG" and r["fresh"] for r in ok),
                    new_short=sum(r["signal"] == "SHORT" and r["fresh"] for r in ok),
                    near=sum(r["near"] for r in ok), total=len(rows), failed=len(errors)),
        config=dict(sma=SMA_MONTHS, warn_pct=WARN_PCT, warn_bp=WARN_BP),
        rows=rows, errors=errors,
    )
    write_outputs(payload)
    print(f"futures: {len(ok)}/{len(rows)} contracts OK" + (f" · failed: {errors}" if errors else ""))
    return payload


def render(payload, months):
    data = json.dumps({**payload, "months": months}, separators=(",", ":")).replace("</", "<\\/")
    return (HERE / "template.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", data)


def summary_md(p):
    c = p["counts"]
    ok = [r for r in p["rows"] if r.get("signal")]
    lines = [f"**Signal month:** {p['signal_month']} · LONG {c['long']} · SHORT {c['short']} · "
             f"near the line {c['near']}", ""]
    fresh = [r for r in ok if r["fresh"]]
    if fresh:
        lines += ["### 🔄 New signals this month", "", "| Contract | Exchange | New signal | vs 10-mo avg |", "|---|---|---|---|"]
        lines += [f"| **{r['code']}** {r['name']} | {r['exch']} | {'🟢 LONG' if r['signal'] == 'LONG' else '🔴 SHORT'} | "
                  f"{r['dist']:+.1f}{' bp' if r['unit'] == 'bp' else '%'} |" for r in fresh]
    else:
        lines += ["No new signals this month — all positions unchanged."]
    if p["errors"]:
        lines += ["", f"⚠️ {len(p['errors'])} contract(s) had no data: " + ", ".join(e.split(':')[0] for e in p["errors"])]
    lines += ["", "_Education only — not advice._"]
    return "\n".join(lines)


def write_outputs(payload):
    (OUT / "data").mkdir(parents=True, exist_ok=True)
    tag = payload["month"]
    (OUT / "data" / f"{tag}.json").write_text(json.dumps(payload, indent=1))
    months = sorted((p.stem for p in (OUT / "data").glob("????-??.json")), reverse=True)
    html = render(payload, months)
    (OUT / f"{tag}.html").write_text(html, encoding="utf-8")
    (OUT / "index.html").write_text(html, encoding="utf-8")
    for m in months[1:]:
        old = json.loads((OUT / "data" / f"{m}.json").read_text())
        (OUT / f"{m}.html").write_text(render(old, months), encoding="utf-8")
    (HERE / "summary.md").write_text(summary_md(payload), encoding="utf-8")
    print(f"Wrote docs/futures/{tag}.html")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    run(ap.parse_args().force)
