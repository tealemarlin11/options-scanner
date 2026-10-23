"""Offline tests with made-up chains. Run: python -m optdesk.test_optdesk"""
import datetime as dt
import json
import math
import os
import numpy as np
import pandas as pd
from . import core

CFG = json.load(open(os.path.join(os.path.dirname(__file__), "config.json")))


def bs_price(S, K, T, iv, is_call, r=0.04):
    d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)
    c = core._cdf
    return S * c(d1) - K * math.exp(-r * T) * c(d2) if is_call else K * math.exp(-r * T) * c(-d2) - S * c(-d1)


def make_chain(spot=500.0, dte=40, iv=0.22, oi=5000, vol=500, half_spread=0.02, step=5.0):
    rows = []
    for K in np.arange(spot * 0.7, spot * 1.3 + 1, step):
        for ic in (True, False):
            p = max(bs_price(spot, K, dte / 365, iv, ic), 0.03)
            rows.append((K, max(p - half_spread, 0.01), p + half_spread, vol, oi, iv, ic))
    return pd.DataFrame(rows, columns=["strike", "bid", "ask", "volume", "oi", "iv", "is_call"])


def px(n=300, drift=0.0004, start=400.0):
    rng = np.random.RandomState(3)
    return pd.Series(start * np.exp(np.cumsum(drift + rng.randn(n) * 0.004)), index=pd.bdate_range("2025-06-01", periods=n))


def test_trend_and_structure():
    up = core.trend_state(px())
    assert up["state"] == "UP"
    dn = core.trend_state(px(drift=-0.0008))
    assert dn["state"] in ("DOWN", "MIXED")
    assert core.choose_structure(up, 55, False, CFG)[0] == "PCS"
    assert core.choose_structure(up, 20, False, CFG)[0] == "CDS"
    assert core.choose_structure(up, 55, True, CFG)[0] is None
    assert core.choose_structure(up, 20, True, CFG)[0] == "CDS"
    d = {"state": "DOWN"}
    assert core.choose_structure(d, 60, False, CFG)[0] == "CCS"
    assert core.choose_structure(d, 30, False, CFG)[0] == "PDS"
    assert core.choose_structure({"state": "MIXED"}, 60, False, CFG)[0] is None


def test_credit_ticket():
    spot = 500.0
    df = core.prep(make_chain(spot), spot, 40)
    t, why = core.build_spread("PCS", df, spot, 40, CFG)
    assert t and t["pass"], why
    short, long_ = t["legs"]
    assert short["action"] == "SELL" and long_["strike"] < short["strike"]
    assert abs(abs(short["delta"]) - 0.20) < 0.06, short
    assert t["net_mid"] >= 0.18 * t["width"] - 1e-9
    assert abs(t["max_loss_per_contract"] - (t["width"] - t["net_mid"]) * 100) < 0.01
    assert t["stop_close_at"] > t["net_mid"] and t["take_profit_close_at"] < t["net_mid"]
    assert t["worst_fill"] <= t["net_mid"] and t["worst_fill"] >= t["natural"]
    c, _ = core.build_spread("CCS", df, spot, 40, CFG)
    assert c["legs"][1]["strike"] > c["legs"][0]["strike"]


def test_debit_ticket():
    spot = 500.0
    df = core.prep(make_chain(spot, dte=52), spot, 52)
    t, why = core.build_spread("CDS", df, spot, 52, CFG)
    assert t is not None
    lg, sh = t["legs"]
    assert lg["action"] == "BUY" and sh["strike"] > lg["strike"]
    assert abs(lg["delta"] - 0.60) < 0.08 and abs(sh["delta"] - 0.30) < 0.08
    p, _ = core.build_spread("PDS", df, spot, 52, CFG)
    assert p["legs"][1]["strike"] < p["legs"][0]["strike"]
    assert core.risk_mult("PDS", CFG) == 0.5 and core.risk_mult("CDS", CFG) == 1.0


def test_liquidity_blocks():
    spot = 500.0
    thin = core.prep(make_chain(spot, oi=200), spot, 40)
    t, why = core.build_spread("PCS", thin, spot, 40, CFG)
    assert not t["pass"] and any("open interest" in w for w in t["fails"])
    quiet = core.prep(make_chain(spot, vol=5), spot, 40)
    t, _ = core.build_spread("PCS", quiet, spot, 40, CFG)
    assert not t["pass"] and any("volume" in w for w in t["fails"])
    wide = core.prep(make_chain(spot, half_spread=0.30), spot, 40)
    t, _ = core.build_spread("PCS", wide, spot, 40, CFG)
    assert not t["pass"] and any("bid-ask" in w or "slippage" in w for w in t["fails"])


def test_selection_limits():
    mk = lambda tk, s: {"ticker": tk, "pass": True, "slip_pct": s}
    ch = core.select_trades([mk("SPY", 1), mk("QQQ", 2), mk("GLD", 3), mk("TLT", 4), mk("XLE", 5)], CFG)
    assert [t["ticker"] for t in ch] == ["SPY", "GLD", "TLT"]


def row(**k):
    r = {"id": "1", "ticker": "SPY", "structure": "PCS", "expiry": str(dt.date.today() + dt.timedelta(days=30)),
         "short_strike": 480.0, "long_strike": 472.0, "contracts": 2, "price": 1.60, "status": "open", "close_price": None, "open_date": ""}
    r.update(k)
    return r


def test_manage():
    today = dt.date.today()
    up = {"state": "UP"}
    q = lambda s, l: {("put", 480.0): s, ("put", 472.0): l}
    a = core.manage(row(), q(0.90, 0.20), 500, up, today, CFG)
    assert any("take profit" in x for x in a["actions"]), a
    a = core.manage(row(), q(3.60, 0.30), 500, up, today, CFG)
    assert any("stop" in x for x in a["actions"]), a
    a = core.manage(row(expiry=str(today + dt.timedelta(days=20))), q(1.5, 0.5), 500, up, today, CFG)
    assert any("time exit" in x for x in a["actions"])
    a = core.manage(row(), q(1.5, 0.5), 500, {"state": "MIXED"}, today, CFG)
    assert any("trend exit" in x for x in a["actions"])
    a = core.manage(row(), q(1.5, 0.5), 500, up, today, CFG)
    assert not a["actions"] and a["pnl"] == round((1.6 - 1.0) * 200, 2)
    d = row(structure="CDS", short_strike=520.0, long_strike=500.0, price=6.0)
    qq = {("call", 520.0): 5.0, ("call", 500.0): 14.0}
    a = core.manage(d, qq, 500, up, today, CFG)
    assert any("take profit" in x for x in a["actions"]), a


def test_journal_and_stats(tmp="/tmp/j_test.csv"):
    open(tmp, "w").write("id,open_date,ticker,structure,expiry,short_strike,long_strike,contracts,price,status,close_date,close_price,notes\n"
                         "1,2026-10-26,SPY,PCS,2026-12-18,480,472,2,1.60,closed,2026-11-10,0.70,\n"
                         "2,2026-10-26,QQQ,CDS,2026-12-18,520,500,1,6.00,closed,2026-11-12,4.00,\n"
                         "3,2026-10-27,SPY,TAIL,2026-12-31,440,400,1,1.50,open,,,\n"
                         "4,bad,row\n")
    rows, notes = core.parse_journal(tmp)
    assert len(rows) == 3 and len(notes) == 1
    s = core.stats(rows, CFG)
    assert s["closed"] == 2 and abs(s["total_pnl"] - ((1.6 - 0.7) * 200 + (4.0 - 6.0) * 100)) < 1e-6
    assert abs(s["tithe_banked"] - 1.6 * 200 * 0.2) < 1e-6 and abs(s["tail_spent"] - 150) < 1e-6


def test_tail_and_expiry():
    spot = 500.0
    df = core.prep(make_chain(spot, dte=62), spot, 62)
    t, why = core.build_tail(df, spot, 62, CFG)
    assert t["legs"][0]["strike"] > t["legs"][1]["strike"] and t["net_mid"] > 0
    today = dt.date(2026, 10, 26)
    ex = [str(today + dt.timedelta(days=d)) for d in (7, 30, 38, 44, 70)]
    r = core.pick_expiry(ex, today, 35, 45, 40)
    assert [d for _, d in r] == [38, 44]




def test_events_pause_shock(tmp="/tmp/pause_test.txt"):
    ev = {"FOMC": ["2026-10-28"], "CPI": ["2026-11-10"], "JOBS": ["2026-11-06"]}
    mon = dt.date(2026, 10, 26)
    r = core.event_check(ev, mon, 2)
    assert r["blocked"] and "FOMC" in r["reason"]
    assert not core.event_check(ev, dt.date(2026, 10, 29), 2)["blocked"]
    assert core.event_check(ev, dt.date(2026, 11, 9), 2)["blocked"]
    assert core.event_check(ev, dt.date(2026, 11, 6), 0)["blocked"]
    assert core.event_check(ev, dt.date(2026, 10, 26), 2)["stale"] is True
    open(tmp, "w").write("# comment\n2026-11-20 Geopolitical shock\n")
    p = core.pause_check(tmp, dt.date(2026, 11, 10))
    assert p and p["until"] == "2026-11-20" and "Geopolitical" in p["reason"]
    assert core.pause_check(tmp, dt.date(2026, 11, 21)) is None
    open(tmp, "w").write("# only comments\n")
    assert core.pause_check(tmp, dt.date(2026, 11, 10)) is None
    s = px(100)
    assert core.shock_check(s, 2.5)[0] is False
    s2 = s.copy()
    s2.iloc[-1] = s2.iloc[-2] * 0.94
    hit, mv = core.shock_check(s2, 2.5)
    assert hit and mv < -5


def test_greeks_and_portfolio():
    g = core.greeks(500, 500, 40 / 365, 0.2, True)
    assert 0.5 < g["delta"] < 0.6 and g["theta"] < 0 and g["vega"] > 0 and g["gamma"] > 0
    gp = core.greeks(500, 500, 40 / 365, 0.2, False)
    assert abs((g["delta"] - gp["delta"]) - 1.0) < 1e-9            # call minus put delta = 1
    today = dt.date.today()
    r = row(expiry=str(today + dt.timedelta(days=40)), short_strike=480.0, long_strike=470.0, price=1.5)
    ivs = {("put", 480.0): 0.22, ("put", 470.0): 0.23}
    pg = core.position_greeks(r, ivs, 500.0, today)
    assert pg["delta"] > 0 and pg["theta"] > 0 and pg["vega"] < 0          # short put spread
    d = row(structure="CDS", short_strike=520.0, long_strike=500.0, price=6.0, expiry=str(today + dt.timedelta(days=50)))
    dg = core.position_greeks(d, {("call", 500.0): 0.2, ("call", 520.0): 0.2}, 500.0, today)
    assert dg["delta"] > 0 and dg["theta"] < 0
    assert core.position_greeks(r, {}, 500.0, today) is None
    assert core.max_loss(r) == (10 - 1.5) * 100 * 2 and core.max_loss(d) == 6.0 * 100 * 2
    s = core.portfolio_summary([{"ticker": "SPY", "structure": "PCS", "spot": 500, "beta": 1.0, "greeks": pg, "max_loss": 1700},
                                {"ticker": "QQQ", "structure": "CDS", "spot": 400, "beta": 1.2, "greeks": dg, "max_loss": 600},
                                {"ticker": "GLD", "structure": "PCS", "spot": 300, "beta": 0.1, "greeks": None, "max_loss": 200}], 500.0)
    assert s["max_loss_total"] == 2500 and s["greeks_missing_for"] == ["GLD"] and s["open_trades"] == 3


def test_selection_counts_open_book():
    mk = lambda tk, s: {"ticker": tk, "pass": True, "slip_pct": s}
    held = [{"ticker": "QQQ", "structure": "PCS", "status": "open"}, {"ticker": "GLD", "structure": "CCS", "status": "open"}]
    ch = core.select_trades([mk("SPY", 1), mk("TLT", 2), mk("XLE", 3)], CFG, held)
    assert [t["ticker"] for t in ch] == ["TLT"], ch                      # SPY blocked by QQQ, XLE by cap
    tail_only = [{"ticker": "SPY", "structure": "TAIL", "status": "open"}]
    assert len(core.select_trades([mk("SPY", 1)], CFG, tail_only)) == 1


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
