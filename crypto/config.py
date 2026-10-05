"""Weekly crypto trend scan (Kraken spot) — the "orange" rule.

Signal, checked on the Sunday close (00:00 UTC Monday):
    LONG  = coin's weekly close above its 20-week average  AND  Bitcoin above its own 20-week average
    CASH  = otherwise
Edit any value below, commit, and the next weekly run uses it.
"""

SMA_WEEKS = 20
WARN_PCT = 10.0            # "near the line" zone, ± % from the average (crypto moves ~3-5x more than shares)

QUOTE = "USD"              # pairs scanned on Kraken (USD has the deepest books); a £ badge shows if a GBP pair exists

# ── Volume / liquidity checks (Kraken-only volume, in US$) ───────────────────
MIN_24H_USD_TO_SCAN = 25_000      # pre-filter: skip pairs that traded less than this in the last 24h
LIQ_TIERS = [                     # 30-day average daily volume on Kraken
    (5_000_000, "High"),
    (1_000_000, "OK"),
    (250_000, "Thin"),
    (0, "Avoid"),
]
MAX_SPREAD_PCT = 0.30             # bid-ask spread above this is flagged
MAX_ZERO_VOL_DAYS = 3             # days with no trades in the last 30 before flagging "gappy"
VOL_CONFIRM_RATIO = 1.0           # signal-week volume vs the 20-week average needed to call it "volume confirms"

# ── Not scanned: stablecoins, fiat, gold tokens. Tokenised stocks (e.g. AAPLx) are skipped automatically ──
EXCLUDE = {
    "USDT", "USDC", "DAI", "PYUSD", "TUSD", "USDP", "USDE", "FDUSD", "USDS", "RLUSD", "USDG", "GUSD",
    "USD1", "USDQ", "USDR", "UST", "BUSD", "EURT", "EURC", "EUROP", "EURQ", "EURR", "AUDX",
    "USD", "EUR", "GBP", "AUD", "CAD", "CHF", "JPY",
    "PAXG", "XAUT",
}
EXTRA_EXCLUDE: set[str] = set()   # add any coin you never want listed, e.g. {"SHIB"}

RENAME = {"XBT": "BTC", "XDG": "DOGE"}   # Kraken's legacy codes -> common tickers
PAUSE_S = 1.1                            # Kraken public API: stay under ~1 request/second
