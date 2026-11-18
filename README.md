# Vecancy-Watch: Assistant Professor vacancy alerts

Vecancy-Watch checks government universities, colleges, institutes and recruitment commissions in the Indian states you choose. When an **Assistant Professor** vacancy in **your subjects** appears, it alerts you on Telegram or email. It reads each notification for the important details and keeps a full history you can browse anytime.

It runs **free on GitHub**, with no computer needed. GitHub checks the websites every 6 hours and publishes your dashboard as a web page you can open on your phone or laptop.

**Your dashboard:** https://s-shorts.github.io/Vecancy-Watch/

---

## What it does

- **Watches 340+ government recruiters** in all 28 states and 8 union territories:
  - state public service commissions and college service commissions (for example UPESSC, UPPSC, WBCSC, TN TRB, BSUSC);
  - central and state universities;
  - IITs, NITs, IIITs, IISERs, AIIMS and IIMs;
  - constituent colleges (for example C.M.P. Degree College, Ewing Christian College, SPM Govt. Degree College and Delhi University colleges);
  - UPSC and Employment News.
- **Finds more colleges by itself:** it adds constituent and government colleges listed on each university's website.
- **Your subjects:** Computer Science, Data Science, Software Engineering and Information Technology are selected by default, and you can add more. It recognises CSE, MCA, IT, AI & DS and Hindi notices. Notices it can't read (scanned images) are marked **"Subject not confirmed"** so you don't miss them.
- **Reads the notification** (PDF or web page) and shows:
  - the subjects listed and total posts;
  - the advertisement number, pay level and qualification;
  - how to apply, the hard-copy deadline and the fee;
  - the exact lines about your subject.
- **Dates:**
  - It knows when applications open and the **online last date**.
  - The hard-copy date is shown separately.
  - Statuses: Open, Opening soon, Possibly open, Closed.
- **One card per vacancy:** when the same advertisement appears on several pages or sites (for example the portal, the main website, "View advertisement" links and per-post boxes), these are merged into one card.
- **Alerts:** you get an alert for vacancies that are already open when you start. After that you get alerts for new vacancies, *applications now open*, *deadline extended*, and reminders **7, 3 and 1 days** before the last date.
- **History:** everything found is kept. You can search and filter it, save ★ or mark applied (saved in your browser), and download it as a spreadsheet.

## One-time setup on GitHub (about 5 minutes)

1. **Turn on the web page:** go to the repository's **Settings → Pages**. Under *Build and deployment → Source*, choose **GitHub Actions**.
2. **Run the first check:** open the **Actions** tab, then **Check vacancies → Run workflow**. If GitHub asks, click *"I understand my workflows, enable them"* first.
3. After about 2 minutes, open **https://s-shorts.github.io/Vecancy-Watch/**. Its **Setup** page asks for your **subjects and states**, then press **Copy settings & open GitHub**. Paste with Ctrl+A then Ctrl+V and press **Commit changes**. A new check starts automatically. The first full check takes about 20–40 minutes.
4. **Alerts on your phone (recommended):** in **Settings → Secrets and variables → Actions → New repository secret**, add:

| Secret name | Value |
|---|---|
| `TELEGRAM_TOKEN` | the token from **@BotFather** (send `/newbot` in Telegram) |
| `TELEGRAM_CHAT_ID` | your chat ID: open your bot, press **Start**, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the number after `"chat":{"id":` |
| `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` | *(optional, for email)* your Gmail address, a Gmail **App password**, and the address to send alerts to |

Secrets stay hidden, even though the repository is public.

**Check now:** press **↻ Check now** on the dashboard, then **Run workflow** on GitHub.
**Change subjects or states:** use the dashboard's **Setup** page again, or edit `config.json` directly.
**Add a college:** in Setup, under "Extra institutions", add a line `State | Name | link to its recruitment page`.

## Run it on your own computer instead (optional)

1. Install Python 3.10+ from python.org and tick **"Add Python to PATH"**.
2. Download this repository (**Code → Download ZIP**) and unzip it.
3. Double-click **`start-windows.bat`**. On Mac or Linux, run `./start-mac-linux.sh` instead.
4. Your browser opens the app and a setup wizard. It checks every 6 hours while the window is open, and adds desktop pop-ups and a full local app with Settings.

## Good to know

- Government websites change often. If a site can't be reached, the **Institutions** page shows a red dot; add its direct recruitment-page link as an extra institution.
- Some government websites may block or slow down overseas servers, and GitHub's servers are outside India. If many sites show errors on GitHub but work at home, run the PC version.
- Dates and details are read automatically. **Always confirm in the official notice before applying.**
- If the repository gets no activity for 60 days, GitHub pauses scheduled checks. Each check saves its results to the repository, which normally keeps it active.

## Files

| Path | What it is |
|---|---|
| `config.json` | your subjects, states and options (written by the Setup page) |
| `data/` | history of every vacancy found (updated by each check) |
| `vacancywatch/` | the checker: crawling, reading notices, dates, subjects, merging duplicates, alerts |
| `github_run.py`, `.github/workflows/` | runs a check on GitHub and publishes the dashboard |
| `web/` | the dashboard page |
| `run.py`, `start-*.bat/.sh` | the PC version |
| `tests/` | an automated test against a mock university and college portal (`pip install pytest reportlab && python -m pytest`) |

Licence: MIT
