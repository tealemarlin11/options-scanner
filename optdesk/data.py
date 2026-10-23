"""Free data via yfinance, imported lazily so the rules can be tested offline."""
import pandas as pd


def history(sym, period="14mo"):
    import yfinance as yf
    h = yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=False)
    return h[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])


def expiries(sym):
    import yfinance as yf
    return list(yf.Ticker(sym).options)


def chain(sym, expiry):
    import yfinance as yf
    oc = yf.Ticker(sym).option_chain(expiry)
    frames = []
    for df, is_call in ((oc.calls, True), (oc.puts, False)):
        d = df[["strike", "bid", "ask", "volume", "openInterest", "impliedVolatility"]].copy()
        d.columns = ["strike", "bid", "ask", "volume", "oi", "iv"]
        d["is_call"] = is_call
        frames.append(d)
    return pd.concat(frames, ignore_index=True)
