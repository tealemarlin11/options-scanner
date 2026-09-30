"""All the knobs in one place. Edit these, commit, and the next run uses them."""

# ── Signal (matches the Trend Timing Pine script) ─────────────────────────────
SMA_MONTHS = 10          # Faber 10-month simple moving average
WARN_PCT = 3.0           # "near the line" warning zone, % from the average

# ── Universe ──────────────────────────────────────────────────────────────────
ETFS = [
    "SPY", "QQQ", "IWM", "DIA",                      # broad market
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP",        # sectors
    "XLI", "XLU", "XLB", "XLRE", "XLC",
    "SMH", "XBI", "KRE", "GDX",                      # popular sub-sectors
    "GLD", "SLV", "TLT", "HYG", "EEM", "EFA",        # other assets
]
EXTRA_TICKERS: list[str] = []   # add anything else you want scanned, e.g. ["PLTR", "COIN"]
MIN_STOCK_PRICE = 20.0          # skip cheap stocks (strikes too coarse, credits too small)

# ── Spread construction ───────────────────────────────────────────────────────
DTE_TARGET = 45          # ~45 days: early in the month this lands on NEXT month's standard (3rd Friday) expiry
DTE_MIN, DTE_MAX = 28, 52
DELTA_TARGET = 0.175     # short strike ~15–20 delta
DELTA_MIN, DELTA_MAX = 0.12, 0.25

# Spread width by share price: (max price, width in $)
WIDTH_TIERS = [(40, 1.0), (100, 2.5), (250, 5.0), (600, 10.0), (1500, 20.0), (1e12, 50.0)]

# ── Quality checks (used for the ✓ score and the default filters) ─────────────
MIN_CREDIT_PCT = 20.0    # credit ≥ 20% of width
MIN_IV_HV = 1.0          # implied vol at least equal to 30-day realised vol
MIN_SHORT_OI = 100       # open interest at the short strike
MAX_BIDASK_PCT = 20.0    # short leg bid-ask spread, % of mid

# ── Politeness towards Yahoo ──────────────────────────────────────────────────
WORKERS = 4
PAUSE_S = 0.25
