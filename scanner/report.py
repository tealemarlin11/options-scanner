"""Renders the monthly report as one self-contained HTML page (works offline too)."""
import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("template.html")


def render(payload: dict, months: list[str]) -> str:
    data = json.dumps({**payload, "months": months}, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data)


def summary_md(p: dict, n: int = 15) -> str:
    """Short markdown summary used for the monthly GitHub Issue (which emails you)."""
    good = [t for t in p["trades"] if t["kind"] == "put" and not t["earnings"] and t["score"] >= 4]
    calls = [t for t in p["trades"] if t["kind"] == "call" and not t["earnings"]]
    c = p["counts"]
    lines = [
        f"**Signal month:** {p['signal_month']}  ·  in market {c['in_market']}/{p['scanned']}  ·  "
        f"fresh BUY {c['fresh_buy']}  ·  fresh EXIT {c['fresh_exit']}",
        "",
        f"### Top {min(n, len(good))} put spreads by option volume (score ≥ 4/6, no earnings)",
        "",
        "| # | Ticker | Expiry | Short/Long | Δ | Credit | Cr/W | POP | Score |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, t in enumerate(good[:n], 1):
        lines.append(f"| {i} | **{t['ticker']}**{' 🆕' if t['fresh_buy'] else ''} | {t['expiry']} ({t['dte']}d) | "
                     f"{t['short_strike']:g}/{t['long_strike']:g} | {abs(t['short_delta']):.2f} | "
                     f"${t['credit']:.2f} | {t['credit_pct']:.0f}% | {t['pop']:.0f}% | {t['score']}/6 |")
    if calls:
        lines += ["", "### Fresh EXIT — bear call spread ideas", ""]
        lines += [f"- **{t['ticker']}** {t['short_strike']:g}/{t['long_strike']:g}C {t['expiry']} · "
                  f"credit ${t['credit']:.2f} · POP {t['pop']:.0f}%" for t in calls[:10]]
    lines += ["", "_Education only — not advice. Check live quotes before trading._"]
    return "\n".join(lines)
