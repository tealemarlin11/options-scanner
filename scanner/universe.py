"""S&P 500 constituents + ETFs. Refreshed each run, cached in data/sp500.csv as a fallback."""
import io
from pathlib import Path

import pandas as pd
import requests

from . import config

CACHE = Path(__file__).resolve().parent.parent / "data" / "sp500.csv"
UA = {"User-Agent": "Mozilla/5.0 (options-scanner; personal use)"}


def _from_wikipedia() -> pd.DataFrame:
    html = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                        headers=UA, timeout=30).text
    df = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})[0]
    return df.rename(columns={"Symbol": "symbol", "Security": "name", "GICS Sector": "sector"})


def _from_github() -> pd.DataFrame:
    url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
    df = pd.read_csv(io.StringIO(requests.get(url, headers=UA, timeout=30).text))
    return df.rename(columns={"Symbol": "symbol", "Security": "name", "Name": "name",
                              "GICS Sector": "sector", "Sector": "sector"})


def load() -> pd.DataFrame:
    """Return columns: symbol (Yahoo format), name, sector, is_etf."""
    df = None
    for src in (_from_wikipedia, _from_github):
        try:
            d = src()[["symbol", "name", "sector"]]
            if len(d) > 450:
                df = d
                break
        except Exception as e:  # noqa: BLE001
            print(f"universe: {src.__name__} failed: {e}")
    if df is None:
        if not CACHE.exists():
            raise RuntimeError("Could not download the S&P 500 list and no cache exists.")
        print("universe: using cached list")
        df = pd.read_csv(CACHE)
    else:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(CACHE, index=False)

    df = df.copy()
    df["symbol"] = df["symbol"].str.replace(".", "-", regex=False).str.strip()
    df["is_etf"] = False
    extra = [s for s in config.ETFS + config.EXTRA_TICKERS if s not in set(df["symbol"])]
    etf_rows = pd.DataFrame({"symbol": extra,
                             "name": extra,
                             "sector": ["ETF" if s in config.ETFS else "Extra" for s in extra],
                             "is_etf": [s in config.ETFS for s in extra]})
    return pd.concat([etf_rows, df], ignore_index=True).drop_duplicates("symbol")
