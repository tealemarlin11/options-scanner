"""Option desk rules. Pure functions, no network. Thresholds live in config.json.

Structures: PCS put credit spread, CCS call credit spread,
            CDS call debit spread, PDS put debit spread, TAIL far put debit spread.
"""
import datetime as dt
import math

import numpy as np
import pandas as pd


def _cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_delta(S, K, T, iv, is_call, r=0.04):
    if not (iv and iv > 0 and T > 0 and S > 0 and K > 0):
        return np.nan
    d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    c = _cdf(d1)
    return c if is_call else c - 1.0


def prep(chain, spot, dte, r=0.04):
    """chain: strike, bid, ask, volume, oi, iv, is_call -> adds mid, spread, delta."""
    c = chain.copy()
    for col in ("bid", "ask", "volume", "oi", "iv"):
        c[col] = pd.to_numeric(c[col], errors="coerce").fillna(0.0)
    c["mid"] = (c["bid"] + c["ask"]) / 2
    c["spread"] = c["ask"] - c["bid"]
    T = dte / 365.0
    c["delta"] = [bs_delta(spot, k, T, iv, bool(ic), r) for k, iv, ic in zip(c["strike"], c["iv"], c["is_call"])]
    return c[(c["ask"] > 0) & c["delta"].notna()].reset_index(drop=True)


def trend_state(close, slow=200, fast=50):
    c = float(close.iloc[-1])
    s, f = float(close.tail(slow).mean()), float(close.tail(fast).mean())
    if len(close) < slow:
        return {"state": "NONE", "close": c, "sma200": None, "sma50": f}
    if c > s:
        st = "UP"
    elif c < s and c < f:
        st = "DOWN"
    else:
        st = "MIXED"
    return {"state": st, "close": round(c, 2), "sma200": round(s, 2), "sma50": round(f, 2)}


def rank_pct(series):
    s = series.dropna().tail(252)
    if len(s) < 60 or s.max() == s.min():
        return None
    return round(float((s.iloc[-1] - s.min()) / (s.max() - s.min()) * 100), 1)


def realized_vol_series(close, win=20):
    return close.pct_change().rolling(win).std() * math.sqrt(252)


def choose_structure(trend, rank, stress, cfg):
    t, rk = trend["state"], rank
    if t in ("MIXED", "NONE"):
        return None, "Trend mixed: close is between the 50 and 200 day averages"
    if rk is None:
        return None, "No volatility rank available"
    if t == "UP":
        if rk >= cfg["ranks"]["up_sell_min"]:
            return ("PCS", None) if not stress else (None, "Stress brake on: no new credit spreads")
        return "CDS", None
    if rk >= cfg["ranks"]["down_sell_min"]:
        return ("CCS", None) if not stress else (None, "Stress brake on: no new credit spreads")
    return "PDS", None


def leg_fails(row, cfg):
    L = cfg["liquidity"]
    f = []
    if not row["bid"] > 0:
        f.append("no bid")
    if row["oi"] < L["min_oi"]:
        f.append(f"open interest {int(row['oi'])} under {L['min_oi']}")
    if row["volume"] < L["min_volume"]:
        f.append(f"volume {int(row['volume'])} under {L['min_volume']}")
    if row["spread"] > max(L["max_spread_abs"], L["max_spread_pct"] * row["mid"]):
        f.append(f"bid-ask {row['spread']:.2f} too wide")
    return f


def _near(df, col, target):
    return df.iloc[(df[col] - target).abs().argsort()[:1]].iloc[0]


def _leg(row, action, kind):
    return {"action": action, "type": kind, "strike": float(row["strike"]), "bid": round(float(row["bid"]), 2),
            "ask": round(float(row["ask"]), 2), "mid": round(float(row["mid"]), 2), "oi": int(row["oi"]),
            "volume": int(row["volume"]), "delta": round(float(row["delta"]), 2)}


def build_spread(structure, df, spot, dte, cfg):
    """Return (ticket or None, list of reasons). df already passed through prep()."""
    is_put = structure in ("PCS", "PDS")
    side = df[df["is_call"] == (not is_put)].copy()
    side["ad"] = side["delta"].abs()
    if len(side) < 6:
        return None, ["thin chain"]
    kind = "put" if is_put else "call"
    credit = structure in ("PCS", "CCS")
    L = cfg["liquidity"]
    if credit:
        cc = cfg["credit"]
        short = _near(side, "ad", cc["delta"])
        tw = min(max(round(spot * cfg["width_pct"]), 1), cfg["max_width"])
        want = short["strike"] - tw if is_put else short["strike"] + tw
        far = side[side["strike"] < short["strike"]] if is_put else side[side["strike"] > short["strike"]]
        if far.empty:
            return None, ["no strike for the long leg"]
        long_ = _near(far, "strike", want)
        width = abs(short["strike"] - long_["strike"])
        price = round(short["mid"] - long_["mid"], 2)
        natural = round(short["bid"] - long_["ask"], 2)
        slip = (short["spread"] + long_["spread"]) / 2
        why = leg_fails(short, cfg) + leg_fails(long_, cfg)
        if price < cc["min_credit_frac"] * width:
            why.append(f"credit {price:.2f} under {cc['min_credit_frac'] * 100:.0f}% of the {width:g} width")
        if price > 0 and slip > L["max_slippage_frac"] * price:
            why.append(f"slippage {slip:.2f} is {slip / price * 100:.0f}% of the credit")
        max_loss = (width - price) * 100
        stop_val = min(cc["stop_multiple"] * price, cc["stop_cap_frac"] * width)
        t = {"legs": [_leg(short, "SELL", kind), _leg(long_, "BUY", kind)], "net_mid": round(price, 2),
             "natural": round(natural, 2), "width": width, "max_loss_per_contract": round(max_loss, 2),
             "max_profit_per_contract": round(price * 100, 2),
             "take_profit_close_at": round(price * (1 - cc["take_profit"]), 2),
             "stop_close_at": round(stop_val, 2),
             "worst_fill": round(price - L["walk_fraction"] * (price - natural), 2), "slip_pct": round(slip / price * 100, 1) if price > 0 else None}
    else:
        dc = cfg["debit"]
        lg = _near(side, "ad", dc["long_delta"])
        sh = _near(side, "ad", dc["short_delta"])
        ok_dir = (sh["strike"] < lg["strike"]) if is_put else (sh["strike"] > lg["strike"])
        if not ok_dir:
            return None, ["could not pair strikes by delta"]
        width = abs(sh["strike"] - lg["strike"])
        price = round(lg["mid"] - sh["mid"], 2)
        natural = round(lg["ask"] - sh["bid"], 2)
        slip = (lg["spread"] + sh["spread"]) / 2
        why = leg_fails(lg, cfg) + leg_fails(sh, cfg)
        if price > dc["max_cost_frac"] * width:
            why.append(f"cost {price:.2f} over {dc['max_cost_frac'] * 100:.0f}% of the {width:g} width")
        if price > 0 and slip > L["max_slippage_frac"] * price:
            why.append(f"slippage {slip:.2f} is {slip / price * 100:.0f}% of the cost")
        t = {"legs": [_leg(lg, "BUY", kind), _leg(sh, "SELL", kind)], "net_mid": round(price, 2),
             "natural": round(natural, 2), "width": width, "max_loss_per_contract": round(price * 100, 2),
             "max_profit_per_contract": round((width - price) * 100, 2),
             "take_profit_close_at": round(price * (1 + dc["take_profit"]), 2),
             "stop_close_at": round(price * (1 - dc["stop_loss"]), 2),
             "worst_fill": round(price + L["walk_fraction"] * (natural - price), 2), "slip_pct": round(slip / price * 100, 1) if price > 0 else None}
    t.update({"structure": structure, "dte": dte, "pass": not why, "fails": why})
    return t, why


def risk_mult(structure, cfg):
    return 0.5 if (structure == "PDS" and cfg["debit"]["half_size_for_put_debit"]) else 1.0


def order_text(t, sym, expiry):
    """Spell out every leg so the direction cannot be misread."""
    names = {"PCS": "Put credit spread", "CCS": "Call credit spread", "CDS": "Call debit spread",
             "PDS": "Put debit spread", "TAIL": "Put debit spread (hedge)"}
    credit = t["structure"] in ("PCS", "CCS")
    legs = " and ".join(f"{l['action']} 1 {sym} {expiry} {l['strike']:g} {l['type']}" for l in t["legs"])
    way = "You receive" if credit else "You pay"
    return (f"{names[t['structure']]}: {legs}. Limit {t['net_mid']:.2f} {'credit' if credit else 'debit'} "
            f"({way}). Do not accept worse than {t['worst_fill']:.2f}.")


def pick_expiry(expiries, today, lo, hi, target):
    """expiries: list of ISO dates. Returns [(iso, dte)] in range, nearest to target first."""
    out = []
    for e in expiries:
        d = (dt.date.fromisoformat(e) - today).days
        if lo <= d <= hi:
            out.append((e, d))
    return sorted(out, key=lambda x: abs(x[1] - target))


def select_trades(tickets, cfg, open_rows=None):
    """Rank passing tickets by slippage. Respect correlation groups and the open-trade cap,
    counting trades you already hold."""
    ok = sorted([t for t in tickets if t.get("pass")], key=lambda t: (t.get("slip_pct") or 99))
    held = [r for r in (open_rows or []) if r.get("status") == "open" and r.get("structure") != "TAIL"]
    groups = {}
    for r in held:
        g = cfg["groups"].get(r["ticker"], r["ticker"])
        groups[g] = groups.get(g, 0) + 1
    n_open = len(held)
    chosen = []
    for t in ok:
        g = cfg["groups"].get(t["ticker"], t["ticker"])
        if groups.get(g, 0) >= cfg["max_per_group"]:
            t["skipped"] = "You already hold a trade in this group" if any(
                cfg["groups"].get(r["ticker"], r["ticker"]) == g for r in held) else "Group limit"
            continue
        if n_open + len(chosen) >= cfg["max_open"]:
            t["skipped"] = "Open-trade limit reached"
            continue
        groups[g] = groups.get(g, 0) + 1
        chosen.append(t)
        t["priority"] = len(chosen)
    return chosen


def manage(row, quotes, spot, trend, today, cfg):
    """row: journal dict. quotes: {(type, strike): mid}. Returns dict with action list."""
    s = row["structure"]
    exp = dt.date.fromisoformat(row["expiry"])
    dte = (exp - today).days
    kind = "put" if s in ("PCS", "PDS", "TAIL") else "call"
    sm, lm = quotes.get((kind, row["short_strike"])), quotes.get((kind, row["long_strike"]))
    out = {"id": row["id"], "ticker": row["ticker"], "structure": s, "expiry": row["expiry"], "dte": dte,
           "contracts": row["contracts"], "actions": [], "value": None, "pnl": None}
    if sm is None or lm is None:
        out["actions"].append("No quote found, check manually")
        return out
    p = row["price"]
    width = abs(row["short_strike"] - row["long_strike"])
    if s in ("PCS", "CCS"):
        val = sm - lm
        pnl = (p - val) * 100 * row["contracts"]
        cc = cfg["credit"]
        if val <= p * (1 - cc["take_profit"]):
            out["actions"].append(f"ACTION take profit: close at {val:.2f} or better")
        if val >= min(cc["stop_multiple"] * p, cc["stop_cap_frac"] * width):
            out["actions"].append(f"ACTION stop: spread value {val:.2f}, close")
        flip = (trend["state"] != "UP") if s == "PCS" else (trend["state"] == "UP")
    elif s in ("CDS", "PDS", "TAIL"):
        val = lm - sm
        pnl = (val - p) * 100 * row["contracts"]
        dc = cfg["debit"]
        if s != "TAIL":
            if val >= p * (1 + dc["take_profit"]):
                out["actions"].append(f"ACTION take profit: close at {val:.2f} or better")
            if val <= p * (1 - dc["stop_loss"]):
                out["actions"].append(f"ACTION stop: spread value {val:.2f}, close")
        flip = (trend["state"] != "UP") if s == "CDS" else (trend["state"] == "UP")
        if s == "TAIL":
            flip = False
    else:
        out["actions"].append("Unknown structure")
        return out
    if s != "TAIL" and dte <= cfg["exit_dte"]:
        out["actions"].append(f"ACTION time exit: {dte} days left, close")
    if flip and s != "TAIL":
        out["actions"].append("ACTION trend exit: trend no longer supports this trade")
    out["value"], out["pnl"] = round(val, 2), round(pnl, 2)
    return out


def parse_journal(path):
    import csv
    rows, notes = [], []
    try:
        fh = open(path, newline="")
    except FileNotFoundError:
        return rows, notes
    with fh:
        for i, r in enumerate(csv.DictReader(fh), start=2):
            if not (r.get("id") or "").strip():
                continue
            try:
                rows.append({"id": r["id"].strip(), "ticker": r["ticker"].strip().upper(),
                             "structure": r["structure"].strip().upper(), "expiry": r["expiry"].strip(),
                             "short_strike": float(r["short_strike"]), "long_strike": float(r["long_strike"]),
                             "contracts": int(float(r["contracts"])), "price": float(r["price"]),
                             "status": (r.get("status") or "open").strip().lower(),
                             "close_price": float(r["close_price"]) if (r.get("close_price") or "").strip() else None,
                             "open_date": (r.get("open_date") or "").strip()})
            except (ValueError, KeyError, TypeError, AttributeError):
                notes.append(f"journal.csv line {i}: could not read, skipped")
    return rows, notes


def stats(rows, cfg):
    closed = [r for r in rows if r["status"] == "closed" and r["close_price"] is not None]
    pnls = []
    for r in closed:
        mult = 100 * r["contracts"]
        credit = r["structure"] in ("PCS", "CCS")
        pnls.append((r["price"] - r["close_price"]) * mult if credit else (r["close_price"] - r["price"]) * mult)
    tithe_in = sum(r["price"] * 100 * r["contracts"] * cfg["tail"]["tithe"] for r in rows if r["structure"] in ("PCS", "CCS"))
    tail_out = sum(r["price"] * 100 * r["contracts"] for r in rows if r["structure"] == "TAIL")
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    return {"closed": n, "total_pnl": round(sum(pnls), 2),
            "win_rate": round(len(wins) / n, 3) if n else None,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
            "expectancy": round(sum(pnls) / n, 2) if n else None,
            "tithe_banked": round(tithe_in, 2), "tail_spent": round(tail_out, 2),
            "tithe_balance": round(tithe_in - tail_out, 2)}


def build_tail(df, spot, dte, cfg):
    """Far put spread on the market ETF, bought with the banked tithe."""
    tc, L = cfg["tail"], cfg["liquidity"]
    puts = df[~df["is_call"].astype(bool)]
    if len(puts) < 6:
        return None, ["thin chain"]
    lg = _near(puts, "strike", spot * (1 - tc["long_otm"]))
    sh = _near(puts[puts["strike"] < lg["strike"]], "strike", spot * (1 - tc["short_otm"])) if (puts["strike"] < lg["strike"]).any() else None
    if sh is None:
        return None, ["no short strike"]
    cost = lg["mid"] - sh["mid"]
    why = leg_fails(lg, cfg) + leg_fails(sh, cfg)
    slip = (lg["spread"] + sh["spread"]) / 2
    if cost > 0 and slip > L["max_slippage_frac"] * cost:
        why.append(f"slippage {slip:.2f} is {slip / cost * 100:.0f}% of the cost")
    t = {"structure": "TAIL", "dte": dte, "legs": [_leg(lg, "BUY", "put"), _leg(sh, "SELL", "put")],
         "net_mid": round(cost, 2), "natural": round(lg["ask"] - sh["bid"], 2),
         "width": float(lg["strike"] - sh["strike"]), "max_loss_per_contract": round(cost * 100, 2),
         "pass": not why, "fails": why}
    return t, why


def greeks(S, K, T, iv, is_call, r=0.04):
    """Black-Scholes greeks per share. theta per day, vega per 1 volatility point."""
    if not (iv and iv > 0 and T > 0 and S > 0 and K > 0):
        return None
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sq)
    d2 = d1 - iv * sq
    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    delta = _cdf(d1) if is_call else _cdf(d1) - 1.0
    base = -(S * pdf * iv) / (2 * sq)
    th = base - r * K * math.exp(-r * T) * _cdf(d2) if is_call else base + r * K * math.exp(-r * T) * _cdf(-d2)
    return {"delta": delta, "gamma": pdf / (S * iv * sq), "vega": S * pdf * sq / 100.0, "theta": th / 365.0}


def position_greeks(row, ivs, spot, today, r=0.04):
    """Net greeks for one journal row. Long leg at long_strike, short leg at short_strike."""
    kind = "put" if row["structure"] in ("PCS", "PDS", "TAIL") else "call"
    T = max((dt.date.fromisoformat(row["expiry"]) - today).days, 1) / 365.0
    out = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    for strike, sign in ((row["long_strike"], 1), (row["short_strike"], -1)):
        g = greeks(spot, strike, T, ivs.get((kind, strike)), kind == "call", r)
        if g is None:
            return None
        for k in out:
            out[k] += sign * g[k] * 100 * row["contracts"]
    return out


def max_loss(row):
    width = abs(row["short_strike"] - row["long_strike"])
    credit = row["structure"] in ("PCS", "CCS")
    per = (width - row["price"]) if credit else row["price"]
    return max(per, 0.0) * 100 * row["contracts"]


def portfolio_summary(items, spy_spot):
    """items: dicts with greeks (or None), spot, beta, max_loss, ticker, structure."""
    d = th = ve = ml = 0.0
    missing = []
    for it in items:
        ml += it["max_loss"]
        g = it.get("greeks")
        if g is None:
            missing.append(it["ticker"])
            continue
        d += g["delta"] * it["spot"] * (it.get("beta") or 1.0) / spy_spot
        th += g["theta"]
        ve += g["vega"]
    return {"open_trades": len([i for i in items if i["structure"] != "TAIL"]),
            "spy_equiv_delta_shares": round(d, 1), "spy_equiv_delta_dollars": round(d * spy_spot, 0),
            "theta_per_day": round(th, 2), "vega_per_vol_point": round(ve, 2),
            "max_loss_total": round(ml, 0), "greeks_missing_for": missing}


def beta_to(close, spy_close, win=252):
    a = close.pct_change().dropna().tail(win)
    b = spy_close.pct_change().dropna().reindex(a.index).dropna()
    a = a.reindex(b.index)
    if len(b) < 60 or b.var() == 0:
        return 1.0
    return round(float(a.cov(b) / b.var()), 2)


def event_check(events, today, window, warn_days=30):
    ups, last = [], None
    for name, dates in events.items():
        if name.startswith("_"):
            continue
        for d in dates:
            day = dt.date.fromisoformat(d)
            last = day if last is None or day > last else last
            n = (day - today).days
            if 0 <= n <= 14:
                ups.append({"name": name, "date": d, "days": n})
    ups.sort(key=lambda x: x["days"])
    hit = [u for u in ups if u["days"] <= window]
    return {"blocked": bool(hit),
            "reason": ("Event blackout: " + ", ".join(f"{u['name']} {u['date']}" for u in hit)) if hit else None,
            "upcoming": ups,
            "stale": last is None or (last - today).days < warn_days}


def pause_check(path, today):
    try:
        lines = open(path).read().splitlines()
    except FileNotFoundError:
        return None
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        head, _, rest = ln.partition(" ")
        try:
            until = dt.date.fromisoformat(head)
        except ValueError:
            return {"until": None, "reason": ln}
        if until >= today:
            return {"until": str(until), "reason": rest.strip() or "Manual pause"}
    return None


def shock_check(close, sigma_mult, win=20):
    r = close.pct_change().dropna()
    if len(r) < win + 2:
        return False, None
    last, sd = float(r.iloc[-1]), float(r.iloc[-(win + 1):-1].std())
    if sd > 0 and abs(last) >= sigma_mult * sd:
        return True, round(last * 100, 2)
    return False, None
