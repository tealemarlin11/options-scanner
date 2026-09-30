# Trend Timing · Spread Scanner

Monthly scan built on the **Faber 10-month average** (the Trend Timing indicator).

- **In-market tickers** → bull put spread ideas (~15–20 delta, 30–45 days).
- **Fresh EXIT tickers** → bear call spread ideas.
- Sorted by **option volume**, with delta, POP, credit/width, theta, vega, IV/HV, earnings and ex-div checks.
- Runs by itself on the **first trading day of each month** on GitHub (free). Your PC can be off.
- Publishes a web page you can open on any computer or phone, and emails you via a GitHub Issue.

Education only — not advice.

---

## One-time setup (about 10 minutes)

### 1. Create the repository
1. Sign in at **github.com** (create a free account if needed).
2. Top right **+** → **New repository**.
3. Name: `options-scanner`. Choose **Public** (free GitHub Pages needs public; the page only shows trade ideas, nothing personal).
4. Click **Create repository**.

### 2. Upload the files
1. On the new repo page click **uploading an existing file**.
2. Unzip `options-scanner.zip` on your PC. Open the folder, select **everything inside it** (including the `.github` folder) and drag it into the browser.
   - Windows hides `.github` by default: in File Explorer → **View → Show → Hidden items**.
3. Click **Commit changes**.
4. Check the repo shows `.github/workflows/monthly-scan.yml`. If not, click **Add file → Create new file**, type `.github/workflows/monthly-scan.yml` as the name and paste the file's contents.

### 3. Allow the scanner to save its report
**Settings → Actions → General → Workflow permissions →** tick **Read and write permissions** → **Save**.

### 4. Turn on the web page
**Settings → Pages →** Source: **Deploy from a branch** · Branch: **main** · Folder: **/docs** → **Save**.

### 5. Run it once now
**Actions** tab → **Monthly spread scan** → **Run workflow** → **Run workflow**.
Takes ~10–20 minutes. Best run while the US market is open (14:30–21:00 UK time) so the quotes are live.

### 6. Open it anywhere
Your page: `https://<your-username>.github.io/options-scanner/`

- **Desktop icon (Windows):** open the page in Edge → **⋯ → Apps → Install this site as an app**, or drag the padlock from the address bar onto the desktop.
- **Other computers / phone:** just bookmark the same link.
- **Email:** each month a new Issue is created in the repo; GitHub emails it to you with the top trades and the link. (Make sure **Watch → All activity** is on for the repo.)

---

## Changing settings
Edit `scanner/config.py` on GitHub (pencil icon) — delta, days to expiry, spread widths, check thresholds, extra tickers. Commit, and the next run uses them.

## Notes
- The schedule runs on days 1–7 at 18:30 UTC and skips once the month is done, so weekends and US holidays are handled.
- GitHub pauses scheduled jobs in repos with no activity for 60 days. The monthly report commit counts as activity, but if a month is missed, re-enable it in the **Actions** tab.
- Data: Yahoo Finance via `yfinance` (free, delayed snapshot). If Yahoo blocks a run, it retries the next day.
