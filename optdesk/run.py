"""Run: python -m optdesk.run (from the repo root). Run during US market hours so quotes are live."""
import datetime as dt
import json
import os
import sys

from . import core, data

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "optdesk", "data")


def _j(o):
    try:
        return float(o)
    except Exception:
        return str(o)


def main():
    cfg = json.load(open(os.path.join(ROOT, "optdesk", "config.json")))
    today = dt.datetime.now(dt.timezone.utc).date()
    os.makedirs(OUT, exist_ok=True)
    notes = []

    try:
        vix, v3 = data.history("^VIX", "1mo")["Close"].iloc[-1], data.history("^VIX3M", "1mo")["Close"].iloc[-1]
        ratio = float(vix / v3)
        stress = {"vix": round(float(vix), 2), "vix3m": round(float(v3), 2), "ratio": round(ratio, 3), "on": ratio >= cfg["stress_ratio"]}
    except Exception as ex:
        stress = {"vix": None, "vix3m": None, "ratio": None, "on": True}
        notes.append(f"VIX data failed, stress brake forced on: {ex}")

    try:
        events = json.load(open(os.path.join(ROOT, cfg["events_file"])))
    except Exception as ex:
        events = {}
        notes.append(f"Event calendar could not be read: {ex}")
    ev = core.event_check(events, today, cfg["event_blackout_days"])
    if ev["stale"]:
        notes.append("Event calendar is running out. Add next year's FOMC, CPI and jobs dates to optdesk/events.json.")
    pause = core.pause_check(os.path.join(ROOT, cfg["pause_file"]), today)
    global_block = pause and f"Manual pause: {pause['reason']}" + (f" (until {pause['until']})" if pause["until"] else "")
    global_block = global_block or ev["reason"]

    tickets, no_trade, trends, closes = [], [], {}, {}
    if global_block:
        no_trade.append({"ticker": "ALL", "reason": global_block})
    for sym in cfg["universe"]:
        try:
            h = data.history(sym)
            close = h["Close"]
            closes[sym] = close
            tr = core.trend_state(close, cfg["trend"]["slow"], cfg["trend"]["fast"])
            trends[sym] = tr
            if global_block:
                continue
            shock, move = core.shock_check(close, cfg["shock_sigma"])
            idx = cfg["vol_index"].get(sym)
            rank = None
            if idx:
                try:
                    rank = core.rank_pct(data.history(idx)["Close"])
                except Exception:
                    rank = None
            if rank is None:
                rank = core.rank_pct(core.realized_vol_series(close))
                idx = "20-day realized vol (fallback)"
            info = {"ticker": sym, "spot": round(float(close.iloc[-1]), 2), "trend": tr, "iv_rank": rank,
                    "rank_source": idx or "20-day realized vol"}
            if shock:
                no_trade.append({**info, "reason": f"Price shock: last move {move:+.1f}% is over {cfg['shock_sigma']} sigma"})
                continue
            structure, reason = core.choose_structure(tr, rank, stress["on"], cfg)
            if structure is None:
                no_trade.append({**info, "reason": reason})
                continue
            dc = cfg["credit"] if structure in ("PCS", "CCS") else cfg["debit"]
            cands = core.pick_expiry(data.expiries(sym), today, dc["dte_min"], dc["dte_max"], dc["dte_target"])[:3]
            if not cands:
                no_trade.append({**info, "reason": "No expiry in the target window"})
                continue
            best, first_fail, notes_fail = None, None, []
            for e, dte in cands:
                df = core.prep(data.chain(sym, e), info["spot"], dte, cfg["rate"])
                t, why = core.build_spread(structure, df, info["spot"], dte, cfg)
                if t is None:
                    notes_fail.append(f"{e}: {why[0]}")
                    continue
                t.update({"ticker": sym, "expiry": e, "spot": info["spot"], "trend": tr["state"], "iv_rank": rank,
                          "risk_mult": core.risk_mult(structure, cfg), "order": core.order_text(t, sym, e)})
                if t["pass"]:
                    best = t
                    break
                if first_fail is None:
                    first_fail = t
                notes_fail.append(f"{e}: " + "; ".join(why))
            if best:
                tickets.append(best)
            else:
                no_trade.append({**info, "reason": "Liquidity or pricing rules not met", "details": notes_fail[:3],
                                 "structure": structure})
        except Exception as ex:
            no_trade.append({"ticker": sym, "reason": f"Data error: {ex}"})

    rows, jn = core.parse_journal(os.path.join(ROOT, "optdesk", "journal.csv"))
    notes += jn
    chosen = core.select_trades(tickets, cfg, rows)
    for t in tickets:
        t["chosen"] = t in chosen

    open_rows = [x for x in rows if x["status"] == "open"]
    spy_close = closes.get("SPY")
    if spy_close is None:
        spy_close = data.history("SPY")["Close"]
    positions, items = [], []
    cache = {}
    for r in open_rows:
        try:
            key = (r["ticker"], r["expiry"])
            if key not in cache:
                c = data.chain(*key)
                mids, ivs = {}, {}
                for k, b, a, v, ic in zip(c["strike"], c["bid"], c["ask"], c["iv"], c["is_call"]):
                    kk = ("call" if ic else "put", float(k))
                    mids[kk] = (float(b) + float(a)) / 2
                    ivs[kk] = float(v)
                cache[key] = (mids, ivs)
            mids, ivs = cache[key]
            if r["ticker"] not in closes:
                closes[r["ticker"]] = data.history(r["ticker"])["Close"]
            tr = trends.get(r["ticker"]) or core.trend_state(closes[r["ticker"]])
            spot = float(closes[r["ticker"]].iloc[-1])
            m = core.manage(r, mids, spot, tr, today, cfg)
            g = core.position_greeks(r, ivs, spot, today, cfg["rate"])
            m["greeks"] = None if g is None else {k: round(v, 2) for k, v in g.items()}
            positions.append(m)
            items.append({"ticker": r["ticker"], "structure": r["structure"], "spot": spot,
                          "beta": core.beta_to(closes[r["ticker"]], spy_close), "greeks": g, "max_loss": core.max_loss(r)})
        except Exception as ex:
            notes.append(f"{r['id']} check failed: {ex}")
    portfolio = core.portfolio_summary(items, float(spy_close.iloc[-1]))
    groups_held = {}
    for r in open_rows:
        if r["structure"] != "TAIL":
            g = cfg["groups"].get(r["ticker"], r["ticker"])
            groups_held[g] = groups_held.get(g, 0) + 1
    portfolio["groups_held"] = groups_held
    portfolio["max_open"] = cfg["max_open"]

    st = core.stats(rows, cfg)
    tail = None
    try:
        tc = cfg["tail"]
        spot = trends.get(tc["underlying"], {}).get("close") or float(data.history(tc["underlying"])["Close"].iloc[-1])
        cands = core.pick_expiry(data.expiries(tc["underlying"]), today, tc["dte_min"], tc["dte_max"], 62)
        if cands:
            e, dte = cands[0]
            t, _ = core.build_tail(core.prep(data.chain(tc["underlying"], e), spot, dte, cfg["rate"]), spot, dte, cfg)
            if t:
                t.update({"ticker": tc["underlying"], "expiry": e, "balance": st["tithe_balance"],
                          "can_buy": st["tithe_balance"] >= t["max_loss_per_contract"] and t["pass"]})
                a, b = t["legs"]
                t["worst_fill"] = round(t["net_mid"] + cfg["liquidity"]["walk_fraction"] * (t["natural"] - t["net_mid"]), 2)
                t["order"] = core.order_text(t, tc["underlying"], e)
                t["order"] += " In tastytrade this is a Long Put Vertical (debit), not a Short Put Vertical."
                tail = t
    except Exception as ex:
        notes.append(f"Tail hedge check failed: {ex}")

    payload = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "rules_version": cfg["rules_version"], "is_monday": today.weekday() == 0, "stress": stress,
               "events": ev, "pause": pause, "blocked": global_block,
               "risk_pct": cfg["risk_pct"], "max_open": cfg["max_open"], "tickets": tickets, "no_trade": no_trade,
               "positions": positions, "portfolio": portfolio, "tail": tail, "stats": st, "notes": notes}
    json.dump(payload, open(os.path.join(OUT, "latest.json"), "w"), indent=1, default=_j)

    lines = [f"Option desk, {today}", f"Stress brake: {'ON' if stress['on'] else 'off'}"]
    if global_block:
        lines.append(f"NEW TRADES BLOCKED: {global_block}")
    lines.append("")
    if payload["is_monday"]:
        lines.append("New trades (Monday): " + (", ".join(f"{t['ticker']} {t['structure']}" for t in chosen) or "none"))
    for p in positions:
        for a in p["actions"]:
            lines.append(f"{p['ticker']} {p['structure']} {p['expiry']}: {a}")
    lines.append(f"Open risk: {portfolio['max_loss_total']:.0f}, SPY-equivalent delta {portfolio['spy_equiv_delta_shares']} shares, theta/day {portfolio['theta_per_day']}")
    open(os.path.join(OUT, "summary.md"), "w").write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
