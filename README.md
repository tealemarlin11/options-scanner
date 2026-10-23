# Option desk (add-on)

Adds ONLY new files. Nothing existing is edited.

| Folder in this zip | Put it here in your repo |
|---|---|
| optdesk/ | repo root, next to scanner/ and futures/ |
| docs/optdesk/ | inside docs/ |
| .github/workflows/optdesk.yml | inside .github/workflows/ (Add file > Create new file, paste) |

Run: Actions > Option desk > Run workflow (do it while the US market is open, 14:30-21:00 UK time).
Page: /optdesk/ on your site.

## Logging a trade
Add a line to optdesk/journal.csv:
id,open_date,ticker,structure,expiry,short_strike,long_strike,contracts,price,status,close_date,close_price,notes
structure: PCS, CCS, CDS, PDS or TAIL. price = credit or debit per share. status: open or closed.
For debit spreads, short_strike is the strike you sold and long_strike the one you bought.

## Rules
All thresholds, including the liquidity filter, are in optdesk/config.json.
Tests: python -m optdesk.test_optdesk
