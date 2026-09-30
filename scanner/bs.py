"""Black-Scholes helpers (European approximation, continuous dividend yield)."""
from math import exp, log, sqrt

from scipy.optimize import brentq
from scipy.stats import norm


def _d1d2(S, K, T, r, q, iv):
    d1 = (log(S / K) + (r - q + 0.5 * iv * iv) * T) / (iv * sqrt(T))
    return d1, d1 - iv * sqrt(T)


def price(kind, S, K, T, r, q, iv):
    d1, d2 = _d1d2(S, K, T, r, q, iv)
    if kind == "put":
        return K * exp(-r * T) * norm.cdf(-d2) - S * exp(-q * T) * norm.cdf(-d1)
    return S * exp(-q * T) * norm.cdf(d1) - K * exp(-r * T) * norm.cdf(d2)


def greeks(kind, S, K, T, r, q, iv):
    """delta, theta per calendar day ($ per share), vega per 1 vol point ($ per share)."""
    d1, d2 = _d1d2(S, K, T, r, q, iv)
    pdf = norm.pdf(d1)
    if kind == "put":
        delta = exp(-q * T) * (norm.cdf(d1) - 1)
        theta = (-S * exp(-q * T) * pdf * iv / (2 * sqrt(T))
                 + r * K * exp(-r * T) * norm.cdf(-d2) - q * S * exp(-q * T) * norm.cdf(-d1))
    else:
        delta = exp(-q * T) * norm.cdf(d1)
        theta = (-S * exp(-q * T) * pdf * iv / (2 * sqrt(T))
                 - r * K * exp(-r * T) * norm.cdf(d2) + q * S * exp(-q * T) * norm.cdf(d1))
    vega = S * exp(-q * T) * pdf * sqrt(T) / 100
    return delta, theta / 365, vega


def implied_vol(kind, mid, S, K, T, r, q):
    """Solve IV from the mid price. Returns None if no sensible solution."""
    if mid is None or mid <= 0 or T <= 0:
        return None
    try:
        lo, hi = price(kind, S, K, T, r, q, 0.005), price(kind, S, K, T, r, q, 5.0)
        if not lo < mid < hi:
            return None
        return brentq(lambda v: price(kind, S, K, T, r, q, v) - mid, 0.005, 5.0, xtol=1e-6)
    except (ValueError, ZeroDivisionError):
        return None


def prob_above(S, level, T, r, q, iv):
    """Risk-neutral probability that the price finishes ABOVE `level` at expiry."""
    if level <= 0:
        return 1.0
    _, d2 = _d1d2(S, level, T, r, q, iv)
    return norm.cdf(d2)
