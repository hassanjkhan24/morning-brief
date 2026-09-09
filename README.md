# Overnight Brief — Pre-Market Trading Dashboard

Runs roughly every 30 minutes overnight on weekdays (9pm ET through 9:15am
ET), so it feels like something is always watching rather than checking in
a few times a night. Each run:
- Pulls fresh headlines from Yahoo Finance, CNBC, and MarketWatch and merges
  them into a rolling 18-hour log, so nothing gets lost between runs
- Checks the confirmed FOMC/CPI/NFP calendar for anything landing in the
  next week, not just today — a Wednesday FOMC meeting shows up starting
  Monday and stays flagged every run until it passes
- Publishes it all to one dashboard page you bookmark — and the page
  auto-refreshes itself every 15 minutes if you leave it open, so it stays
  current without you touching it

**Two speeds, to control cost:**
- Every ~30 min: headlines + calendar refresh (free, no AI call)
- At 7 key moments (9pm, 1am, 5am, 7:30am, 8:45am, 9:00am, 9:15am ET): the
  accumulated data gets sent to Claude, which writes the synthesized
  briefing (this week's key events, overnight recap, today's catalysts,
  names on your radar, explicit risk flags) — this is the "Jarvis" layer.
  The last three of those are clustered right around your 9-9:30 prep
  window on purpose, so what you're reading is genuinely fresh, not
  something written at 8:45 and stale by the time you sit down.
- Between AI refreshes, the dashboard keeps showing the last briefing it
  generated (with a "last refreshed HH:MM ET" label) rather than going
  blank — headlines keep updating underneath it the whole time.

Runs on GitHub's free tier (Actions + Pages) — no server to maintain, and
public repos get unlimited free Actions minutes, so the ~27 runs/night cost
nothing extra on the GitHub side. The only ongoing cost is the Claude API
calls at the 7 AI checkpoints — roughly $3–6/month at this frequency.

**Important:** the AI briefing is designed to give you situational
awareness and explicit risk flags (FOMC days, CPI prints, unusual gaps),
not trade signals. It won't tell you to buy or sell anything — that call,
and the risk management around it, stays yours.

## Setup (10 minutes, one time)

1. **Create a GitHub account** if you don't have one: https://github.com/signup

2. **Create a new repository**
   - Go to https://github.com/new
   - Name it something like `morning-brief`
   - Set it to **Public** (required for free GitHub Pages)
   - Click "Create repository"

3. **Upload these files**
   - On the new repo page, click "uploading an existing file"
   - Drag in this entire folder's contents, keeping the folder structure
     (`.github/workflows/morning-brief.yml`, `scripts/build_brief.py`,
     `requirements.txt`, `docs/index.html`)
   - Commit directly to the `main` branch

4. **Turn on GitHub Pages**
   - Go to repo **Settings → Pages**
   - Under "Build and deployment", set Source to **Deploy from a branch**
   - Branch: `main`, folder: `/docs`
   - Save. GitHub will give you a URL like:
     `https://YOUR-USERNAME.github.io/morning-brief/`
   - Bookmark that URL — that's your dashboard.

5. **Turn on Actions permissions**
   - Go to repo **Settings → Actions → General**
   - Under "Workflow permissions", select **Read and write permissions**
   - Save

6. **Add your Anthropic API key (enables the AI briefing)**
   - Get a key at https://console.anthropic.com (API access is separate
     from your claude.ai login — you'll need to add a small amount of
     prepaid credit)
   - In your repo, go to **Settings → Secrets and variables → Actions**
   - Click **New repository secret**
   - Name: `ANTHROPIC_API_KEY`, Value: paste your key, Save
   - Skip this step if you just want raw headlines with no AI synthesis —
     the dashboard still works fine without it, just without the amber
     "Overnight Brief" section

7. **Run it once manually to test**
   - Go to the **Actions** tab → "Build Overnight Brief" workflow → **Run workflow**
   - Wait ~30-60 seconds, then refresh your GitHub Pages URL — you should
     see real headlines and (if you added the API key) a synthesized brief

That's it. From now on it runs automatically 5x every weekday overnight
and updates the same URL, culminating in a final brief around 8:45 AM ET.

## Adjusting things later

- **Add/remove news sources**: edit the `SOURCES` dict at the top of
  `scripts/build_brief.py`. Any RSS feed URL works.
- **Change the persona/output format**: edit `SYSTEM_PROMPT` in
  `scripts/build_brief.py` — this is the full instruction set that shapes
  the AI briefing's voice and structure.
- **Change how far ahead the calendar looks**: `WEEK_LOOKAHEAD_DAYS` in
  `scripts/build_brief.py`, currently 6 (i.e. "this week"). A confirmed
  event stays flagged on every run once it's within this window, and
  keeps showing until its date passes.
- **Add/remove confirmed calendar events**: edit `ECON_CALENDAR` in
  `scripts/build_brief.py` — see the annual-update note further down.
- **Change run times**: edit the `cron` lines in
  `.github/workflows/morning-brief.yml`. Times are in UTC and currently
  assume EDT (ET = UTC-4).
  - GitHub Actions doesn't handle daylight saving automatically — when
    Toronto switches to EST (winter), add 1 hour to each cron time
    **and** update the matching strings in the "Decide whether this run
    should call the AI" step's `case` statement to match (they compare
    exact cron strings, so if you shift the schedule you have to shift
    those too).
- **Change which checkpoints call the AI**: edit the `case` statement in
  the "Decide whether this run should call the AI" workflow step — the
  cron strings listed there are the ones that get `run_ai=true`. Move a
  time from the fetch-only list to that case block (or vice versa) to
  shift where the API cost goes.
- **Change the auto-refresh interval**: the `setTimeout` line near the
  bottom of `TEMPLATE` in `scripts/build_brief.py` (currently 15 minutes).
- **Weekends**: currently skipped (`1-5` = Mon–Fri only).
- **How far back the log looks**: `LOG_WINDOW_HOURS` in
  `scripts/build_brief.py`, currently 18 hours.

## Notes

- X/Twitter was intentionally left out — their API pricing makes this
  impractical for a personal script. If you later want tweets from a
  specific account pulled in, the cleanest free-ish option is an RSS
  bridge service (e.g. RSS.app or Nitter-based bridges), which can be
  slotted into `SOURCES` the same way as the other feeds.
- If a feed source changes its RSS URL, the script will silently return
  fewer/no headlines for that source rather than erroring — check the
  Actions run log if the dashboard looks thin.
- The AI briefing is generated from whatever headlines/calendar data the
  script successfully pulled — it can't see anything beyond that (no live
  price feed, no order flow, no options data, no level 2). Treat it as a
  fast, structured read of the news and the known calendar, not a
  replacement for your own market read at the open.
- The confirmed calendar (`ECON_CALENDAR`) needs a five-minute manual
  update once a year — when the Fed publishes next year's FOMC dates
  (usually announced late in the current year) and when the BLS publishes
  next year's CPI/NFP release schedule (usually available well in
  advance at bls.gov/schedule), add the new dates to the list in the same
  format. This is the one part of the pipeline that isn't fully hands-off.
