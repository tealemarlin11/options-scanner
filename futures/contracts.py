"""Futures universe. Edit freely: add/remove rows, commit, next run uses them.

kind  : "price" = normal price series · "yield" = bond yield (signal inverted: falling yield = LONG)
src   : "yahoo" (continuous front-month or cash index) · "bbk" (Bundesbank daily yields) · "ecb" (ECB monthly yields)
mult  : value of a 1.00 move in the quoted price, per contract, in `ccy`
proxy : None if the series IS the future; otherwise a short note shown on the page
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class C:
    code: str
    name: str
    exch: str
    group: str
    src: str
    sym: str
    mult: float | None = None
    ccy: str = "USD"
    kind: str = "price"
    proxy: str | None = None


_BBK = "D.I.ZAR.ZI.EUR.S1311.B.A604.R{m}XX.R.A.A._Z._Z.A"   # Bundesbank: German Federal yield, residual maturity m years

CONTRACTS = [
    # ── Equity index ─────────────────────────────────────────────────────────
    C("ES", "S&P 500 E-mini", "CME", "Equity index", "yahoo", "ES=F", 50),
    C("NQ", "Nasdaq-100 E-mini", "CME", "Equity index", "yahoo", "NQ=F", 20),
    C("RTY", "Russell 2000 E-mini", "CME", "Equity index", "yahoo", "RTY=F", 50),
    C("YM", "Dow E-mini", "CBOT", "Equity index", "yahoo", "YM=F", 5),
    C("FDAX", "DAX", "EUREX", "Equity index", "yahoo", "^GDAXI", 25, "EUR", proxy="DAX cash index"),
    C("FESX", "Euro Stoxx 50", "EUREX", "Equity index", "yahoo", "^STOXX50E", 10, "EUR", proxy="Euro Stoxx 50 cash index"),
    C("FSMI", "Swiss Market Index", "EUREX", "Equity index", "yahoo", "^SSMI", 10, "CHF", proxy="SMI cash index"),
    C("F2MX", "MDAX", "EUREX", "Equity index", "yahoo", "^MDAXI", 5, "EUR", proxy="MDAX cash index"),
    C("FTDX", "TecDAX", "EUREX", "Equity index", "yahoo", "^TECDAX", 10, "EUR", proxy="TecDAX cash index"),
    C("FXXP", "Stoxx Europe 600", "EUREX", "Equity index", "yahoo", "^STOXX", 50, "EUR", proxy="Stoxx 600 cash index"),
    # ── Interest rates ───────────────────────────────────────────────────────
    C("ZT", "US 2-Year Note", "CBOT", "Rates", "yahoo", "ZT=F", 2000),
    C("ZF", "US 5-Year Note", "CBOT", "Rates", "yahoo", "ZF=F", 1000),
    C("ZN", "US 10-Year Note", "CBOT", "Rates", "yahoo", "ZN=F", 1000),
    C("ZB", "US Treasury Bond", "CBOT", "Rates", "yahoo", "ZB=F", 1000),
    C("UB", "US Ultra Bond", "CBOT", "Rates", "yahoo", "UB=F", 1000),
    C("FGBS", "Euro-Schatz (2y)", "EUREX", "Rates", "bbk", _BBK.format(m="02"), kind="yield", ccy="EUR", proxy="German 2y yield (Bundesbank)"),
    C("FGBM", "Euro-Bobl (5y)", "EUREX", "Rates", "bbk", _BBK.format(m="05"), kind="yield", ccy="EUR", proxy="German 5y yield (Bundesbank)"),
    C("FGBL", "Euro-Bund (10y)", "EUREX", "Rates", "bbk", _BBK.format(m="10"), kind="yield", ccy="EUR", proxy="German 10y yield (Bundesbank)"),
    C("FGBX", "Euro-Buxl (30y)", "EUREX", "Rates", "bbk", _BBK.format(m="30"), kind="yield", ccy="EUR", proxy="German 30y yield (Bundesbank)"),
    C("FBTP", "Euro-BTP (Italy 10y)", "EUREX", "Rates", "ecb", "IRS/M.IT.L.L40.CI.0000.EUR.N.Z", kind="yield", ccy="EUR", proxy="Italy 10y yield, monthly average (ECB)"),
    C("FOAT", "Euro-OAT (France 10y)", "EUREX", "Rates", "ecb", "IRS/M.FR.L.L40.CI.0000.EUR.N.Z", kind="yield", ccy="EUR", proxy="France 10y yield, monthly average (ECB)"),
    # ── Energy ───────────────────────────────────────────────────────────────
    C("CL", "WTI Crude Oil", "NYMEX", "Energy", "yahoo", "CL=F", 1000),
    C("BZ", "Brent Crude (last day)", "NYMEX", "Energy", "yahoo", "BZ=F", 1000),
    C("NG", "Natural Gas", "NYMEX", "Energy", "yahoo", "NG=F", 10000),
    C("RB", "RBOB Gasoline", "NYMEX", "Energy", "yahoo", "RB=F", 42000),
    C("HO", "Heating Oil", "NYMEX", "Energy", "yahoo", "HO=F", 42000),
    # ── Metals ───────────────────────────────────────────────────────────────
    C("GC", "Gold", "COMEX", "Metals", "yahoo", "GC=F", 100),
    C("SI", "Silver", "COMEX", "Metals", "yahoo", "SI=F", 5000),
    C("HG", "Copper", "COMEX", "Metals", "yahoo", "HG=F", 25000),
    C("PL", "Platinum", "NYMEX", "Metals", "yahoo", "PL=F", 50),
    C("PA", "Palladium", "NYMEX", "Metals", "yahoo", "PA=F", 100),
    # ── Agriculture (grains quoted in cents) ─────────────────────────────────
    C("ZC", "Corn", "CBOT", "Agriculture", "yahoo", "ZC=F", 50),
    C("ZS", "Soybeans", "CBOT", "Agriculture", "yahoo", "ZS=F", 50),
    C("ZW", "Chicago Wheat", "CBOT", "Agriculture", "yahoo", "ZW=F", 50),
    C("KE", "KC HRW Wheat", "CBOT", "Agriculture", "yahoo", "KE=F", 50),
    C("ZL", "Soybean Oil", "CBOT", "Agriculture", "yahoo", "ZL=F", 600),
    C("ZM", "Soybean Meal", "CBOT", "Agriculture", "yahoo", "ZM=F", 100),
    C("LE", "Live Cattle", "CME", "Agriculture", "yahoo", "LE=F", 400),
    C("GF", "Feeder Cattle", "CME", "Agriculture", "yahoo", "GF=F", 500),
    C("HE", "Lean Hogs", "CME", "Agriculture", "yahoo", "HE=F", 400),
    # ── Currencies ───────────────────────────────────────────────────────────
    C("6E", "Euro FX", "CME", "Currencies", "yahoo", "6E=F", 125000),
    C("6B", "British Pound", "CME", "Currencies", "yahoo", "6B=F", 62500),
    C("6J", "Japanese Yen", "CME", "Currencies", "yahoo", "6J=F", 12500000),
    C("6A", "Australian Dollar", "CME", "Currencies", "yahoo", "6A=F", 100000),
    C("6C", "Canadian Dollar", "CME", "Currencies", "yahoo", "6C=F", 100000),
    C("6S", "Swiss Franc", "CME", "Currencies", "yahoo", "6S=F", 125000),
    # ── Crypto ───────────────────────────────────────────────────────────────
    C("BTC", "Bitcoin", "CME", "Crypto", "yahoo", "BTC=F", 5),
    C("ETH", "Ether", "CME", "Crypto", "yahoo", "ETH=F", 50),
]

GROUP_ORDER = ["Equity index", "Rates", "Energy", "Metals", "Agriculture", "Currencies", "Crypto"]

SMA_MONTHS = 10
WARN_PCT = 3.0     # price series: "near the line" within ±3%
WARN_BP = 10.0     # yield series: "near the line" within ±10 basis points
