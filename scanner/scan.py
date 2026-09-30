"""Monthly Trend-Timing options scan.

1. Faber 10-month SMA signal on every ticker (last COMPLETED month only).
2. In-market tickers  -> bull put spread candidates (~15-20 delta, 30-45 DTE).
   Fresh EXIT tickers -> bear call spread candidates.
3. Greeks, POP, IV vs realised vol, earnings / ex-div, liquidity.
4. Writes docs/<YYYY-MM>.html, docs/index.html and docs/data/<YYYY-MM>.json.

Run:  python -m scanner.scan            (skips if this month is already done)
      python -m scanner.scan --force    (always run)
"""
from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from . import bs, config, report, universe

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
NY = ZoneInfo("America/New_York")


# ── helpers ───────────────────────────────────────────────────────────────────
def _num(x):
    """float or None (JSON-safe)."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def _retry(fn, tries=3):
    for i in range(tries):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def width_for(price: float) -> float:
    for cap, w in config.WIDTH_TIERS:
        if price <= cap:
            return w
    return config.WIDTH_TIERS[-1][1]


def is_monthly(d: date) -> bool:
    return d.weekday() == 4 and 15 <= d.day <= 21


def pick_expiry(expiries: list[str], today: date) -> date | None:
    opts = []
    for s in expiries:
        d = date.fromisoformat(s)
        dte = (d - today).days
        if config.DTE_MIN <= dte <= config.DTE_MAX:
            # prefer standard monthlies (best liquidity), then closest to target
            opts.append((0 if is_monthly(d) else 1, abs(dte - config.DTE_TARGET), d))
    return min(opts)[2] if opts else None


# ── 1. signals ────────────────────────────────────────────────────────────────
def compute_signals(close: pd.DataFrame, divs: pd.DataFrame, today: date) -> pd.DataFrame:
    """One row per ticker with the signal state after the last completed month."""
    month_start = pd.Timestamp(today.replace(day=1))
    completed = close[close.index < month_start]
    monthly = completed.resample("ME").last()
    sma = monthly.rolling(config.SMA_MONTHS).mean()

    logret = np.log(close / close.shift(1))
    hv21 = logret.rolling(21).std() * np.sqrt(252)
    rows = []
    for t in close.columns:
        m, s = monthly[t].dropna(), sma[t].dropna()
        if len(s) < 2 or close[t].dropna().empty:
            continue
        c, a = m.iloc[-1], s.iloc[-1]
        c1, a1 = m.iloc[-2], s.iloc[-2]
        spot = close[t].dropna().iloc[-1]
        h = hv21[t].dropna().iloc[-252:]
        hv_now = h.iloc[-1] if len(h) else np.nan
        hv_rank = (hv_now - h.min()) / (h.max() - h.min()) * 100 if len(h) > 20 and h.max() > h.min() else np.nan
        d12 = divs[t][divs.index >= pd.Timestamp(today) - pd.Timedelta(days=365)].sum() if t in divs else 0.0
        in_mkt = c > a
        dist = (c / a - 1) * 100
        rows.append(dict(
            ticker=t, month_close=c, sma=a, dist_pct=dist,
            in_market=bool(in_mkt),
            fresh_buy=bool(in_mkt and c1 <= a1),
            fresh_exit=bool((not in_mkt) and c1 > a1),
            near=bool(abs(dist) <= config.WARN_PCT),
            spot=spot, spot_vs_sma=(spot / a - 1) * 100,
            hv30=hv_now, hv_rank=hv_rank,
            div_yield=(d12 / spot) if spot else 0.0,
            signal_month=m.index[-1].strftime("%b %Y"),
        ))
    return pd.DataFrame(rows).set_index("ticker")


# ── 2. spread builder ─────────────────────────────────────────────────────────
def _prep(df: pd.DataFrame, kind, S, T, r, q) -> pd.DataFrame:
    df = df.copy()
    for col in ("bid", "ask", "lastPrice", "volume", "openInterest", "impliedVolatility"):
        df[col] = pd.to_numeric(df.get(col), errors="coerce").fillna(0.0)
    two_sided = (df.bid > 0) & (df.ask > 0)
    df["mid"] = np.where(two_sided, (df.bid + df.ask) / 2, df.lastPrice)
    df["two_sided"] = two_sided
    ivs = []
    for k, mid, yiv in zip(df.strike, df.mid, df.impliedVolatility):
        iv = bs.implied_vol(kind, mid, S, k, T, r, q)
        ivs.append(iv if iv else (yiv if yiv > 0.01 else np.nan))
    df["iv"] = ivs
    df["delta"] = [bs.greeks(kind, S, k, T, r, q, v)[0] if v == v else np.nan
                   for k, v in zip(df.strike, df.iv)]
    return df


def build_spread(kind, chain_side, other_side, sig, today, expiry, r, events):
    S, q = float(sig.spot), float(sig.div_yield or 0.0)
    dte = (expiry - today).days
    T = max(dte, 1) / 365
    side = _prep(chain_side, kind, S, T, r, q)

    otm = side[(side.strike < S) if kind == "put" else (side.strike > S)]
    otm = otm[otm.delta.notna() & (otm.mid > 0)]
    if otm.empty:
        return None
    ad = otm.delta.abs()
    band = otm[(ad >= config.DELTA_MIN) & (ad <= config.DELTA_MAX)]
    pool = band if not band.empty else otm
    short = pool.loc[(pool.delta.abs() - config.DELTA_TARGET).abs().idxmin()]
    if band.empty and abs(abs(short.delta) - config.DELTA_TARGET) > 0.08:
        return None

    w = width_for(S)
    if kind == "put":
        longs = side[side.strike <= short.strike - w + 1e-9]
        long = longs.loc[longs.strike.idxmax()] if not longs.empty else None
    else:
        longs = side[side.strike >= short.strike + w - 1e-9]
        long = longs.loc[longs.strike.idxmin()] if not longs.empty else None
    if long is None:
        return None

    width = abs(short.strike - long.strike)
    credit = short.mid - long.mid
    if credit <= 0 or credit >= width:
        return None
    natural = short.bid - long.ask

    iv_l = long.iv if long.iv == long.iv else short.iv
    d_s, th_s, v_s = bs.greeks(kind, S, short.strike, T, r, q, short.iv)
    d_l, th_l, v_l = bs.greeks(kind, S, long.strike, T, r, q, iv_l)

    # ATM IV = average of nearest-strike put & call IVs where available
    atm = side.iloc[(side.strike - S).abs().argsort()[:1]]
    atm_iv = float(atm.iv.iloc[0]) if atm.iv.notna().any() else float(short.iv)
    if other_side is not None and not other_side.empty:
        o = other_side.iloc[(other_side.strike - S).abs().argsort()[:1]].iloc[0]
        b, a = _num(o.get("bid")) or 0.0, _num(o.get("ask")) or 0.0
        omid = (b + a) / 2 if b > 0 and a > 0 else (_num(o.get("lastPrice")) or 0.0)
        oiv = bs.implied_vol("call" if kind == "put" else "put", omid, S, float(o.strike), T, r, q)
        if oiv:
            atm_iv = (atm_iv + oiv) / 2

    if kind == "put":
        be = short.strike - credit
        pop = bs.prob_above(S, be, T, r, q, short.iv)
        otm_pct = (1 - short.strike / S) * 100
    else:
        be = short.strike + credit
        pop = 1 - bs.prob_above(S, be, T, r, q, short.iv)
        otm_pct = (short.strike / S - 1) * 100
    exp_move = S * atm_iv * math.sqrt(T)
    sigma_dist = abs(S - short.strike) / exp_move if exp_move else None

    vol_total = float(side.volume.sum() + (other_side["volume"].fillna(0).sum() if other_side is not None else 0))
    oi_total = float(side.openInterest.sum() + (other_side["openInterest"].fillna(0).sum() if other_side is not None else 0))
    bidask = (short.ask - short.bid) / short.mid * 100 if short.two_sided and short.mid > 0 else None

    earn = [d for d in events.get("earnings", []) if today <= d <= expiry]
    exdiv = events.get("exdiv")
    exdiv_hit = exdiv if exdiv and today <= exdiv <= expiry else None

    strike_vs_sma = (short.strike / sig.sma - 1) * 100
    iv_hv = atm_iv / sig.hv30 if sig.hv30 and sig.hv30 == sig.hv30 else None
    liquid = (short.openInterest >= config.MIN_SHORT_OI and bidask is not None
              and bidask <= config.MAX_BIDASK_PCT)
    credit_pct = credit / width * 100

    checks = {
        "credit": credit_pct >= config.MIN_CREDIT_PCT,
        "iv_hv": iv_hv is not None and iv_hv >= config.MIN_IV_HV,
        "no_earnings": not earn,
        "strike_vs_sma": strike_vs_sma < 0 if kind == "put" else strike_vs_sma > 0,
        "liquidity": bool(liquid),
        "not_near": not sig.near,
    }
    return dict(
        kind=kind, expiry=expiry.isoformat(), dte=dte, monthly=is_monthly(expiry),
        short_strike=_num(short.strike), long_strike=_num(long.strike), width=_num(width),
        short_mid=_num(short.mid), long_mid=_num(long.mid),
        credit=_num(credit), credit_natural=_num(natural), credit_pct=_num(credit_pct),
        max_loss=_num((width - credit) * 100), max_profit=_num(credit * 100),
        ror=_num(credit / (width - credit) * 100), breakeven=_num(be),
        pop=_num(pop * 100), short_delta=_num(d_s), long_delta=_num(d_l),
        pos_delta=_num((-d_s + d_l) * 100), theta=_num((-th_s + th_l) * 100),
        vega=_num((-v_s + v_l) * 100), short_iv=_num(short.iv * 100), atm_iv=_num(atm_iv * 100),
        iv_hv=_num(iv_hv), otm_pct=_num(otm_pct), exp_move=_num(exp_move),
        sigma_dist=_num(sigma_dist), strike_vs_sma=_num(strike_vs_sma),
        short_oi=_num(short.openInterest), long_oi=_num(long.openInterest),
        short_vol=_num(short.volume), bidask_pct=_num(bidask),
        stale_quotes=not bool(short.two_sided and long.two_sided),  # noqa
        opt_volume=_num(vol_total), opt_oi=_num(oi_total),
        earnings=earn[0].isoformat() if earn else None,
        exdiv=exdiv_hit.isoformat() if exdiv_hit else None,
        checks={k: bool(v) for k, v in checks.items()},
        score=int(sum(bool(v) for v in checks.values())),
    )


def _events(tk) -> dict:
    out = {"earnings": [], "exdiv": None}
    try:
        cal = tk.calendar or {}
    except Exception:  # noqa: BLE001  (ETFs often have no calendar)
        return out
    ed = cal.get("Earnings Date") or []
    out["earnings"] = [d if isinstance(d, date) else pd.Timestamp(d).date()
                       for d in (ed if isinstance(ed, (list, tuple)) else [ed])]
    x = cal.get("Ex-Dividend Date")
    if x is not None:
        out["exdiv"] = x if isinstance(x, date) else pd.Timestamp(x).date()
    return out


def scan_ticker(sym, kind, sig, today, r):
    tk = yf.Ticker(sym)
    expiries = _retry(lambda: tk.options)
    expiry = pick_expiry(list(expiries or []), today)
    if expiry is None:
        return None
    ch = _retry(lambda: tk.option_chain(expiry.isoformat()))
    time.sleep(config.PAUSE_S)
    side, other = (ch.puts, ch.calls) if kind == "put" else (ch.calls, ch.puts)
    return build_spread(kind, side, other, sig, today, expiry, r, _events(tk))


# ── 3. orchestration ──────────────────────────────────────────────────────────
def download_prices(symbols: list[str]):
    data = yf.download(symbols + ["^IRX"], period="2y", interval="1d", auto_adjust=False,
                       actions=True, group_by="column", threads=True, progress=False)
    close = data["Close"].dropna(how="all")
    divs = data["Dividends"].fillna(0.0) if "Dividends" in data.columns.get_level_values(0) else pd.DataFrame()
    irx = close.pop("^IRX").dropna() if "^IRX" in close else pd.Series(dtype=float)
    r = float(irx.iloc[-1]) / 100 if len(irx) else 0.04
    return close, divs, r


def run(force: bool = False) -> dict | None:
    now = datetime.now(NY)
    today = now.date()
    tag = today.strftime("%Y-%m")
    if not force and (DOCS / "data" / f"{tag}.json").exists():
        print(f"{tag} already scanned — nothing to do.")
        return None

    uni = universe.load()
    symbols = uni.symbol.tolist()
    print(f"Universe: {len(symbols)} tickers")
    close, divs, r = download_prices(symbols)

    last_bar = close.index[-1].date()
    if not force and last_bar != today:
        print(f"No market data for {today} yet (last bar {last_bar}) — market closed? Skipping.")
        return None

    sigs = compute_signals(close, divs, today)
    meta = uni.set_index("symbol")
    sigs = sigs.join(meta[["name", "sector", "is_etf"]], how="left")
    sigs = sigs[(sigs.is_etf == True) | (sigs.spot >= config.MIN_STOCK_PRICE)]  # noqa: E712

    jobs = [(t, "put") for t in sigs.index[sigs.in_market]] + \
           [(t, "call") for t in sigs.index[sigs.fresh_exit]]
    print(f"In market: {int(sigs.in_market.sum())} · fresh BUY: {int(sigs.fresh_buy.sum())} · "
          f"fresh EXIT: {int(sigs.fresh_exit.sum())} · option scans: {len(jobs)}")

    results, errors = [], []
    with ThreadPoolExecutor(config.WORKERS) as pool:
        futs = {pool.submit(scan_ticker, t, k, sigs.loc[t], today, r): (t, k) for t, k in jobs}
        for i, f in enumerate(as_completed(futs), 1):
            t, k = futs[f]
            try:
                res = f.result()
                if res:
                    s = sigs.loc[t]
                    res.update(ticker=t, name=s["name"], sector=s["sector"], is_etf=bool(s.is_etf),
                               spot=_num(s.spot), sma=_num(s.sma), dist_pct=_num(s.dist_pct),
                               fresh_buy=bool(s.fresh_buy), fresh_exit=bool(s.fresh_exit),
                               near=bool(s.near), hv30=_num(s.hv30 * 100), hv_rank=_num(s.hv_rank))
                    results.append(res)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{t}: {e}")
            if i % 50 == 0:
                print(f"  {i}/{len(jobs)} scanned")

    board = [dict(ticker=t, name=s["name"], sector=s["sector"], spot=_num(s.spot),
                  month_close=_num(s.month_close), sma=_num(s.sma), dist_pct=_num(s.dist_pct),
                  in_market=bool(s.in_market), fresh_buy=bool(s.fresh_buy),
                  fresh_exit=bool(s.fresh_exit), near=bool(s.near))
             for t, s in sigs.iterrows()]

    payload = dict(
        month=tag, generated=now.isoformat(timespec="minutes"), signal_month=sigs.signal_month.iloc[0],
        risk_free=_num(r * 100), universe=len(symbols), scanned=len(board),
        counts=dict(in_market=int(sigs.in_market.sum()), fresh_buy=int(sigs.fresh_buy.sum()),
                    fresh_exit=int(sigs.fresh_exit.sum()),
                    puts=sum(x["kind"] == "put" for x in results),
                    calls=sum(x["kind"] == "call" for x in results)),
        config=dict(dte=[config.DTE_MIN, config.DTE_MAX], delta=[config.DELTA_MIN, config.DELTA_MAX],
                    min_credit_pct=config.MIN_CREDIT_PCT, min_iv_hv=config.MIN_IV_HV,
                    min_short_oi=config.MIN_SHORT_OI, max_bidask_pct=config.MAX_BIDASK_PCT,
                    warn_pct=config.WARN_PCT),
        trades=sorted(results, key=lambda x: -(x["opt_volume"] or 0)),
        board=board, errors=errors[:50],
    )
    write_outputs(payload)
    return payload


def write_outputs(payload: dict):
    (DOCS / "data").mkdir(parents=True, exist_ok=True)
    tag = payload["month"]
    (DOCS / "data" / f"{tag}.json").write_text(json.dumps(payload, indent=1))
    months = sorted((p.stem for p in (DOCS / "data").glob("????-??.json")), reverse=True)
    html = report.render(payload, months)
    (DOCS / f"{tag}.html").write_text(html, encoding="utf-8")
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    (DOCS / ".nojekyll").touch()
    # refresh the month picker on older pages
    for m in months[1:]:
        old = json.loads((DOCS / "data" / f"{m}.json").read_text())
        (DOCS / f"{m}.html").write_text(report.render(old, months), encoding="utf-8")
    (ROOT / "summary.md").write_text(report.summary_md(payload), encoding="utf-8")
    print(f"Wrote docs/{tag}.html  ·  {len(payload['trades'])} trade ideas")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="run even if this month is done / market closed")
    run(ap.parse_args().force)
