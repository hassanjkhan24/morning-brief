#!/usr/bin/env python3
"""
Overnight -> pre-market trading brief.

Runs multiple times overnight (see .github/workflows/morning-brief.yml).
Each run:
  1. Pulls fresh headlines from RSS sources, merges them into a rolling
     18-hour log stored in data/headline_log.json (so nothing pulled at
     9pm is lost by the time the 8:45am run happens).
  2. Checks the confirmed macro calendar for anything landing this week
     (not just today/tomorrow) -- a Wednesday FOMC meeting shows up
     starting Monday and stays flagged every run until it passes.
  3. If ANTHROPIC_API_KEY is set, sends the accumulated headlines +
     calendar to Claude to write a synthesized briefing. If not set,
     the dashboard still works, just without the AI section.
  4. Renders docs/index.html.
"""

import os
import json
import html
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import feedparser

ET = ZoneInfo("America/New_York")
LOG_PATH = "data/headline_log.json"
BRIEFING_CACHE_PATH = "data/last_briefing.json"
LOG_WINDOW_HOURS = 18
TRADER_NAME = "Hassan"  # personalize or leave generic

# --- Add or remove feed URLs here anytime ---
SOURCES = {
    "Yahoo Finance": [
        "https://finance.yahoo.com/news/rssindex",
    ],
    "CNBC": [
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",  # Top News
        "https://www.cnbc.com/id/10000664/device/rss/rss.html",   # Finance
        "https://www.cnbc.com/id/15839069/device/rss/rss.html",   # Markets
    ],
    "MarketWatch": [
        "https://www.marketwatch.com/rss/topstories",
    ],
}

MAX_PER_SOURCE_PER_RUN = 15
WEEK_LOOKAHEAD_DAYS = 6  # how many days ahead counts as "this week"

# --- Confirmed high-impact macro calendar for 2026 ---
# Sourced directly from federalreserve.gov (FOMC) and bls.gov (CPI, NFP).
# These are official/confirmed dates, not headline-inferred -- treat them
# as ground truth. Update this list once a year when the Fed/BLS publish
# the next year's schedule.
ECON_CALENDAR = [
    # FOMC rate decisions -- statement + press conference, 2:00 PM ET
    {"date": "2026-01-28", "event": "FOMC rate decision", "time": "2:00 PM ET"},
    {"date": "2026-03-18", "event": "FOMC rate decision (+ dot plot)", "time": "2:00 PM ET"},
    {"date": "2026-04-29", "event": "FOMC rate decision", "time": "2:00 PM ET"},
    {"date": "2026-06-17", "event": "FOMC rate decision (+ dot plot)", "time": "2:00 PM ET"},
    {"date": "2026-07-29", "event": "FOMC rate decision", "time": "2:00 PM ET"},
    {"date": "2026-09-16", "event": "FOMC rate decision (+ dot plot)", "time": "2:00 PM ET"},
    {"date": "2026-10-28", "event": "FOMC rate decision", "time": "2:00 PM ET"},
    {"date": "2026-12-09", "event": "FOMC rate decision (+ dot plot)", "time": "2:00 PM ET"},
    # CPI (inflation)
    {"date": "2026-01-13", "event": "CPI (Dec 2025)", "time": "8:30 AM ET"},
    {"date": "2026-02-13", "event": "CPI (Jan 2026)", "time": "8:30 AM ET"},
    {"date": "2026-03-11", "event": "CPI (Feb 2026)", "time": "8:30 AM ET"},
    {"date": "2026-04-10", "event": "CPI (Mar 2026)", "time": "8:30 AM ET"},
    {"date": "2026-05-12", "event": "CPI (Apr 2026)", "time": "8:30 AM ET"},
    {"date": "2026-06-10", "event": "CPI (May 2026)", "time": "8:30 AM ET"},
    {"date": "2026-07-14", "event": "CPI (Jun 2026)", "time": "8:30 AM ET"},
    {"date": "2026-08-12", "event": "CPI (Jul 2026)", "time": "8:30 AM ET"},
    {"date": "2026-09-11", "event": "CPI (Aug 2026)", "time": "8:30 AM ET"},
    {"date": "2026-10-14", "event": "CPI (Sep 2026)", "time": "8:30 AM ET"},
    {"date": "2026-11-10", "event": "CPI (Oct 2026)", "time": "8:30 AM ET"},
    {"date": "2026-12-10", "event": "CPI (Nov 2026)", "time": "8:30 AM ET"},
    # NFP / Employment Situation
    {"date": "2026-01-09", "event": "Jobs report / NFP (Dec 2025)", "time": "8:30 AM ET"},
    {"date": "2026-02-11", "event": "Jobs report / NFP (Jan 2026)", "time": "8:30 AM ET"},
    {"date": "2026-03-06", "event": "Jobs report / NFP (Feb 2026)", "time": "8:30 AM ET"},
    {"date": "2026-04-03", "event": "Jobs report / NFP (Mar 2026)", "time": "8:30 AM ET"},
    {"date": "2026-05-08", "event": "Jobs report / NFP (Apr 2026)", "time": "8:30 AM ET"},
    {"date": "2026-06-05", "event": "Jobs report / NFP (May 2026)", "time": "8:30 AM ET"},
    {"date": "2026-07-02", "event": "Jobs report / NFP (Jun 2026)", "time": "8:30 AM ET"},
    {"date": "2026-08-07", "event": "Jobs report / NFP (Jul 2026)", "time": "8:30 AM ET"},
    {"date": "2026-09-04", "event": "Jobs report / NFP (Aug 2026)", "time": "8:30 AM ET"},
    {"date": "2026-10-02", "event": "Jobs report / NFP (Sep 2026)", "time": "8:30 AM ET"},
    {"date": "2026-11-06", "event": "Jobs report / NFP (Oct 2026)", "time": "8:30 AM ET"},
    # PPI (producer prices)
    {"date": "2026-01-14", "event": "PPI (Nov 2025)", "time": "8:30 AM ET"},
    {"date": "2026-01-30", "event": "PPI (Dec 2025)", "time": "8:30 AM ET"},
    {"date": "2026-02-27", "event": "PPI (Jan 2026)", "time": "8:30 AM ET"},
    {"date": "2026-03-18", "event": "PPI (Feb 2026)", "time": "8:30 AM ET"},
    {"date": "2026-04-14", "event": "PPI (Mar 2026)", "time": "8:30 AM ET"},
    {"date": "2026-05-13", "event": "PPI (Apr 2026)", "time": "8:30 AM ET"},
    {"date": "2026-06-11", "event": "PPI (May 2026)", "time": "8:30 AM ET"},
    {"date": "2026-07-15", "event": "PPI (Jun 2026)", "time": "8:30 AM ET"},
    {"date": "2026-08-13", "event": "PPI (Jul 2026)", "time": "8:30 AM ET"},
    {"date": "2026-09-10", "event": "PPI (Aug 2026)", "time": "8:30 AM ET"},
    {"date": "2026-10-15", "event": "PPI (Sep 2026)", "time": "8:30 AM ET"},
    {"date": "2026-11-13", "event": "PPI (Oct 2026)", "time": "8:30 AM ET"},
    {"date": "2026-12-15", "event": "PPI (Nov 2026)", "time": "8:30 AM ET"},
]


def get_calendar_matches():
    """Return confirmed macro events landing within the next WEEK_LOOKAHEAD_DAYS
    days (ET), including today. An event stays in this list -- and therefore
    stays flagged on the dashboard -- every run until its date passes."""
    today = datetime.now(ET).date()
    matches = []
    for ev in ECON_CALENDAR:
        ev_date = datetime.strptime(ev["date"], "%Y-%m-%d").date()
        delta_days = (ev_date - today).days
        if 0 <= delta_days <= WEEK_LOOKAHEAD_DAYS:
            if delta_days == 0:
                when = "TODAY"
            elif delta_days == 1:
                when = "tomorrow"
            else:
                when = f"{ev_date.strftime('%A')} (in {delta_days} days)"
            matches.append({**ev, "when": when, "days_until": delta_days})
    matches.sort(key=lambda m: m["days_until"])
    return matches


# ---------------------------------------------------------------------------
# 1. Fetch + accumulate headlines
# ---------------------------------------------------------------------------

def fetch_entries(source_name, feed_urls):
    entries = []
    for url in feed_urls:
        try:
            parsed = feedparser.parse(url)
        except Exception as exc:
            print(f"WARN: failed to parse {url}: {exc}")
            continue
        for e in parsed.entries[:MAX_PER_SOURCE_PER_RUN]:
            title = html.unescape(getattr(e, "title", "")).strip()
            link = getattr(e, "link", "")
            summary = html.unescape(getattr(e, "summary", "")).strip()
            summary = summary.split("<")[0].strip()[:200]
            published_struct = getattr(e, "published_parsed", None)
            if published_struct:
                pub_dt = datetime(*published_struct[:6], tzinfo=timezone.utc)
            else:
                pub_dt = datetime.now(timezone.utc)
            if title and link:
                entries.append({
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "source": source_name,
                    "pub_iso": pub_dt.isoformat(),
                })
    return entries


def load_log():
    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_log(entries):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def merge_and_prune(existing, new_entries):
    by_key = {e["title"].lower(): e for e in existing}
    for e in new_entries:
        key = e["title"].lower()
        if key not in by_key:
            by_key[key] = e
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOG_WINDOW_HOURS)
    merged = [
        e for e in by_key.values()
        if datetime.fromisoformat(e["pub_iso"]) > cutoff
    ]
    merged.sort(key=lambda x: x["pub_iso"], reverse=True)
    return merged


def load_cached_briefing():
    if os.path.exists(BRIEFING_CACHE_PATH):
        try:
            with open(BRIEFING_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_cached_briefing(text):
    os.makedirs(os.path.dirname(BRIEFING_CACHE_PATH), exist_ok=True)
    with open(BRIEFING_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({"text": text, "generated_at": datetime.now(timezone.utc).isoformat()}, f)


# ---------------------------------------------------------------------------
# 2. Confirmed macro calendar (see ECON_CALENDAR + get_calendar_matches above)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 3. AI synthesis via Claude API
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = f"""You are {TRADER_NAME}'s overnight/pre-market markets analyst \
-- think of the voice as a senior, quant-minded portfolio manager: calm, \
precise, no hype, no filler. {TRADER_NAME} scalps short-dated options on \
major US indices/names Monday-Friday, 9:00am-1:00pm ET.

Your job each run is to turn a pile of raw overnight/pre-market headlines \
plus a confirmed macro calendar into a short, decision-useful briefing.

Hard rules:
- Never issue an explicit trade directive (no "buy calls on X", no position \
sizing, no "go long/short"). You surface facts, context, and risk conditions; \
{TRADER_NAME} makes the call.
- Do flag, explicitly and plainly, conditions that historically raise gap/whipsaw \
risk in the first hour of trading: FOMC days, CPI/PPI/NFP prints, major \
unscheduled geopolitical shocks, unusually large overnight moves, thin \
holiday liquidity. Call these out as "reduce size / expect chop / wait for \
range to establish" style risk-management context, not instructions.
- You will be given a CONFIRMED CALENDAR EVENTS block sourced directly from \
the Fed and BLS, covering the next several days, not just today. Treat it as \
ground truth. If today's date matches an event, lead with it as the top risk \
flag. If an event is later this week (e.g. an FOMC decision on Wednesday and \
today is Monday), still surface it clearly under its own note so {TRADER_NAME} \
has it on the radar days in advance -- don't bury a Wednesday FOMC meeting on \
a Monday brief just because it isn't today.
- If headlines are thin or stale, say so plainly instead of padding.
- Be specific with names, numbers, and directions when the source material \
supports it. Don't invent figures that aren't in the provided data.
- Keep it tight: a trader reading this before the open shouldn't need more \
than 60-90 seconds.

Output in this exact structure, using markdown:

**Bottom line:** one or two sentences, the single most important thing to know.

**This week's key events:** any confirmed calendar item landing within the next \
several days, each tagged with when it lands (today / tomorrow / day name + days \
away). If today itself is a confirmed event day, say so plainly here too.

**Overnight recap:** what moved Asia/Europe and why, in bullets.

**Today's catalysts:** anything on the calendar or in the headlines that could \
move markets during today's session specifically, with approximate times ET if known.

**On the radar:** SPY/QQQ/META plus anything else surfaced in the headlines \
that's directly relevant to a US equity/options scalper today.

**Risk flags:** explicit bullets on anything that should make {TRADER_NAME} \
trade smaller, wait, or be extra careful today or in the days leading up to a \
known event this week. If there's nothing notable, say so.

Additional instruction: the user message tells you the current time. If it is \
8:30 AM ET or later, after your Overnight Brief above, add a line containing \
EXACTLY "===PREMARKET===" and nothing else, then write a second, shorter \
briefing titled "Pre-Market Brief" covering specifically what's new in \
headlines timestamped 8:30 AM ET or later today (fold in the overnight \
context briefly, but focus on what's changed since 8:30). Use this structure:

**Bottom line:** one sentence on where things stand right at/near the open.

**What's new since 8:30:** bullets on headlines/moves from 8:30 AM ET onward. \
If nothing new has come in since 8:30, say so plainly.

**Into the open:** one or two sentences of risk-management context for the \
first minutes of trading specifically.

If it is before 8:30 AM ET, do NOT include the marker or the Pre-Market Brief \
-- output only the Overnight Brief.
"""


def build_user_content(log_entries, calendar_matches):
    now_et = datetime.now(ET)
    calendar_lines = "\n".join(
        f"- {ev['when']}: {ev['event']} at {ev['time']}" for ev in calendar_matches
    ) or f"(no confirmed high-impact releases in the next {WEEK_LOOKAHEAD_DAYS} days)"

    headline_lines = "\n".join(
        f"- [{e['source']}, {datetime.fromisoformat(e['pub_iso']).astimezone(ET).strftime('%-I:%M %p ET')}] "
        f"{e['title']}" + (f" -- {e['summary']}" if e.get("summary") else "")
        for e in log_entries[:60]
    ) or "(no headlines collected yet this cycle)"

    return f"""Current time: {now_et.strftime('%A, %B %-d, %-I:%M %p ET')}

CONFIRMED CALENDAR EVENTS -- next {WEEK_LOOKAHEAD_DAYS} days (official Fed/BLS \
dates -- treat as ground truth, higher confidence than anything inferred from \
headlines below):
{calendar_lines}

ACCUMULATED HEADLINES (last {LOG_WINDOW_HOURS}h, newest first):
{headline_lines}
"""


def call_gemini(user_content, api_key):
    """Free path: Google's Gemini API (no card required, rate-limited)."""
    model = "gemini-3.6-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps({
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {"maxOutputTokens": 1200},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        parts = data["candidates"][0]["content"]["parts"]
        return "\n".join(p.get("text", "") for p in parts).strip() or None
    except urllib.error.HTTPError as exc:
        print(f"WARN: Gemini API error {exc.code}: {exc.read().decode('utf-8', 'ignore')}")
        return None
    except Exception as exc:
        print(f"WARN: Gemini API call failed: {exc}")
        return None


def call_claude(user_content, api_key):
    """Paid path: Anthropic's Claude API, used if ANTHROPIC_API_KEY is set instead."""
    body = json.dumps({
        "model": "claude-sonnet-5",
        "max_tokens": 1000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        return "\n".join(text_blocks).strip() or None
    except urllib.error.HTTPError as exc:
        print(f"WARN: Claude API error {exc.code}: {exc.read().decode('utf-8', 'ignore')}")
        return None
    except Exception as exc:
        print(f"WARN: Claude API call failed: {exc}")
        return None


def call_ai(log_entries, calendar_matches):
    if os.environ.get("RUN_AI", "true").lower() == "false":
        print("INFO: RUN_AI=false (fetch-only run); skipping AI synthesis, will reuse cached briefing.")
        return None

    gemini_key = os.environ.get("GEMINI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not gemini_key and not anthropic_key:
        print("INFO: no GEMINI_API_KEY or ANTHROPIC_API_KEY set; skipping AI synthesis.")
        return None

    user_content = build_user_content(log_entries, calendar_matches)

    if gemini_key:
        result = call_gemini(user_content, gemini_key)
        if result:
            return result
        print("WARN: Gemini call failed; falling back to Claude if a key is set.")

    if anthropic_key:
        return call_claude(user_content, anthropic_key)

    return None


# ---------------------------------------------------------------------------
# 4. Render
# ---------------------------------------------------------------------------

def markdown_lite_to_html(text):
    """Very small markdown->HTML pass: **bold** headers and bullet lists."""
    lines = text.split("\n")
    out = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("**") and stripped.endswith("**") and stripped.count("**") == 2:
            if in_list:
                out.append("</ul>")
                in_list = False
            label = stripped.strip("*")
            out.append(f"<h3>{html.escape(label)}</h3>")
        elif stripped.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            content = html.escape(stripped[2:])
            content = content.replace("**", "")  # strip stray bold markers
            out.append(f"<li>{content}</li>")
        elif stripped:
            if in_list:
                out.append("</ul>")
                in_list = False
            content = html.escape(stripped)
            out.append(f"<p>{content}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


TOP_TECH_STOCKS = [
    "apple", "microsoft", "nvidia", "alphabet", "google", "amazon", "meta",
    "tsmc", "taiwan semiconductor", "broadcom", "tesla", "oracle",
    "samsung", "tencent", "asml", "sap", "netflix", "amd", "salesforce",
    "adobe", "qualcomm", "ibm",
]
TOP_SP_STOCKS = [
    "berkshire hathaway", "jpmorgan", "jp morgan", "eli lilly", "visa",
    "mastercard", "exxon", "walmart", "unitedhealth", "johnson & johnson",
]
MAJOR_WORLD_KEYWORDS = [
    "federal reserve", "fomc", "fed chair", "rate decision", "rate cut",
    "rate hike", "cpi", "inflation report", "jobs report", "nonfarm payroll",
    "unemployment rate", "recession", "gdp", "central bank", "ecb",
    "war", "invasion", "ceasefire", "sanctions", "tariff", "trade war",
    "government shutdown", "debt ceiling", "election", "geopolitical",
    "oil price", "opec",
]
MEDIUM_KEYWORDS = [
    "s&p 500", "nasdaq", "dow jones", "treasury", "yield", "bond market",
    "earnings", "ipo", "merger", "acquisition", "stock market", "wall street",
    "dollar", "crude oil", "bitcoin", "crypto", "market volatility", "vix",
    "premarket", "pre-market", "futures",
]


def classify_importance(entry):
    text = f"{entry['title']} {entry.get('summary', '')}".lower()
    if any(k in text for k in TOP_TECH_STOCKS) or any(k in text for k in TOP_SP_STOCKS) \
            or any(k in text for k in MAJOR_WORLD_KEYWORDS):
        return "major"
    if any(k in text for k in MEDIUM_KEYWORDS):
        return "medium"
    return "minor"


def render_today_highlight(calendar_matches):
    today_events = [ev for ev in calendar_matches if ev["days_until"] == 0]
    if not today_events:
        return ""
    parts = " &middot; ".join(f"{html.escape(ev['event'])} {html.escape(ev['time'])}" for ev in today_events)
    return f'<span class="today-highlight">&#9888; TODAY: {parts}</span>'


def render_calendar_banner(calendar_matches):
    if not calendar_matches:
        return ""
    chips = "".join(
        f'<span class="cal-chip{" cal-chip-today" if ev["days_until"] == 0 else ""}">'
        f'{html.escape(ev["when"])}: {html.escape(ev["event"])} &middot; {html.escape(ev["time"])}</span>'
        for ev in calendar_matches
    )
    label = "Confirmed calendar &mdash; this week" if any(m["days_until"] > 0 for m in calendar_matches) else "Confirmed calendar"
    return f"""
    <div class="calendar-banner">
      <span class="cal-label">{label}</span>
      {chips}
    </div>"""


def render_items(entries):
    rows = []
    for e in entries:
        time_str = datetime.fromisoformat(e["pub_iso"]).astimezone(ET).strftime("%-I:%M %p ET")
        summary_html = f'<p class="item-summary">{html.escape(e["summary"])}&hellip;</p>' if e.get("summary") else ""
        rows.append(f"""
        <a class="item" href="{html.escape(e['link'])}" target="_blank" rel="noopener">
          <div class="item-time">{time_str}<span class="item-source">{html.escape(e['source'])}</span></div>
          <div class="item-body">
            <p class="item-title">{html.escape(e['title'])}</p>
            {summary_html}
          </div>
        </a>""")
    return "\n".join(rows) if rows else '<p class="empty">No headlines collected yet.</p>'


def build():
    # 1. fetch + merge
    existing_log = load_log()
    new_entries = []
    for source_name, urls in SOURCES.items():
        new_entries.extend(fetch_entries(source_name, urls))
    merged_log = merge_and_prune(existing_log, new_entries)
    save_log(merged_log)

    # 2. calendar + AI synthesis (with cache fallback for fetch-only runs)
    calendar_matches = get_calendar_matches()
    briefing_md = call_ai(merged_log, calendar_matches)
    briefing_generated_dt = None

    if briefing_md:
        save_cached_briefing(briefing_md)
        briefing_generated_dt = datetime.now(ET)
    else:
        cached = load_cached_briefing()
        if cached:
            briefing_md = cached["text"]
            briefing_generated_dt = datetime.fromisoformat(cached["generated_at"]).astimezone(ET)

    overnight_md, premarket_md = None, None
    if briefing_md:
        if "===PREMARKET===" in briefing_md:
            overnight_md, premarket_md = briefing_md.split("===PREMARKET===", 1)
        else:
            overnight_md = briefing_md

    overnight_html = markdown_lite_to_html(overnight_md.strip()) if overnight_md else None
    premarket_html = markdown_lite_to_html(premarket_md.strip()) if premarket_md else None


    # 4. render
    now_et = datetime.now(ET)
    generated_str = now_et.strftime("%A, %B %-d &middot; %-I:%M %p ET")

    if overnight_html:
        refreshed_str = briefing_generated_dt.strftime("%-I:%M %p ET")
        ai_section = f"""
    <section class="briefing">
      <p class="source-label"><span class="dot amber"></span>Overnight Brief <span class="refreshed">last refreshed {refreshed_str}</span></p>
      {overnight_html}
    </section>"""
        if premarket_html:
            ai_section += f"""
    <section class="briefing briefing-premarket">
      <p class="source-label"><span class="dot dot-major"></span>Pre-Market Brief <span class="refreshed">8:30-9:30 AM ET &middot; last refreshed {refreshed_str}</span></p>
      {premarket_html}
    </section>"""
    else:
        ai_section = """
    <section class="briefing">
      <p class="source-label"><span class="dot amber"></span>Overnight Brief</p>
      <p class="empty">No AI briefing yet &mdash; add a free GEMINI_API_KEY repo secret to turn it on. Headlines below keep updating either way.</p>
    </section>"""

    tiers = {"major": [], "medium": [], "minor": []}
    for e in merged_log:
        tiers[classify_importance(e)].append(e)

    tier_meta = [
        ("major", "Most important", 8),
        ("medium", "Mediocre", 8),
        ("minor", "Least important", 8),
    ]
    source_sections = "\n".join(f"""
    <section>
      <p class="source-label"><span class="dot dot-{key}"></span>{label}</p>
      {render_items(tiers[key][:cap])}
    </section>""" for key, label, cap in tier_meta)

    html_out = TEMPLATE.format(
        generated=generated_str,
        today_highlight=render_today_highlight(calendar_matches),
        calendar=render_calendar_banner(calendar_matches),
        briefing=ai_section,
        sources=source_sections,
    )

    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html_out)

    run_ai_flag = os.environ.get("RUN_AI", "true").lower() != "false"
    print(f"Wrote docs/index.html: {len(merged_log)} headlines in log, "
          f"this run RUN_AI={run_ai_flag}, "
          f"briefing displayed: {'YES' if overnight_html else 'NO'}.")


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pre-Market Brief</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #0B0E14;
    --surface: #12161F;
    --line: #232A38;
    --text: #E7ECF3;
    --muted: #7C8798;
    --amber: #FF9F1C;
    --green: #00D26A;
    --red: #FF4757;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: 'IBM Plex Sans', sans-serif;
    min-height: 100vh;
  }}
  header {{
    padding: 28px 32px 20px;
    border-bottom: 1px solid var(--line);
  }}
  .eyebrow {{
    font-family: 'IBM Plex Mono', monospace;
    color: var(--amber);
    font-size: 12px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    margin: 0 0 6px;
  }}
  h1 {{
    font-family: 'Space Grotesk', sans-serif;
    font-size: 28px;
    font-weight: 700;
    margin: 0 0 6px;
    letter-spacing: -0.01em;
  }}
  .title-row {{
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
  }}
  .today-highlight {{
    font-family: 'Space Grotesk', sans-serif;
    font-size: 18px;
    font-weight: 700;
    color: var(--red);
    background: rgba(255, 71, 87, 0.12);
    border: 1px solid var(--red);
    border-radius: 6px;
    padding: 6px 14px;
    margin-bottom: 6px;
    letter-spacing: 0.01em;
  }}
  .generated {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 13px;
    color: var(--muted);
    margin: 0;
  }}
  .calendar-banner {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    padding: 12px 32px;
    background: rgba(255, 71, 87, 0.08);
    border-bottom: 1px solid var(--line);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
  }}
  .cal-label {{
    color: var(--red);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 500;
  }}
  .cal-chip {{
    color: var(--text);
    border: 1px solid rgba(255, 71, 87, 0.4);
    border-radius: 6px;
    padding: 4px 10px;
  }}
  .cal-chip-today {{
    background: rgba(255, 71, 87, 0.18);
    border-color: var(--red);
    font-weight: 500;
  }}
  .briefing {{
    padding: 26px 32px 30px;
    background: linear-gradient(180deg, rgba(255,159,28,0.06), transparent 40%);
    border-bottom: 1px solid var(--line);
  }}
  .briefing-premarket {{
    background: linear-gradient(180deg, rgba(255,71,87,0.08), transparent 40%);
  }}
  .briefing h3 {{
    font-family: 'Space Grotesk', sans-serif;
    font-size: 15px;
    color: var(--amber);
    margin: 20px 0 8px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  .briefing h3:first-of-type {{ margin-top: 0; }}
  .briefing p {{ font-size: 14.5px; line-height: 1.6; margin: 0 0 8px; color: var(--text); }}
  .briefing ul {{ margin: 0 0 8px; padding-left: 20px; }}
  .briefing li {{ font-size: 14.5px; line-height: 1.6; margin-bottom: 6px; }}
  main {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 1px;
    background: var(--line);
  }}
  @media (max-width: 900px) {{
    main {{ grid-template-columns: 1fr; }}
  }}
  main section {{
    background: var(--bg);
    padding: 24px 28px 40px;
  }}
  .source-label {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
    margin: 0 0 18px;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .dot {{
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--amber);
    display: inline-block;
  }}
  .dot.amber {{ background: var(--amber); }}
  .dot-major {{ background: var(--red); }}
  .dot-medium {{ background: var(--amber); }}
  .dot-minor {{ background: var(--muted); }}
  .refreshed {{
    color: var(--muted);
    text-transform: none;
    letter-spacing: normal;
    font-weight: 400;
    margin-left: 6px;
  }}
  .item {{
    display: flex;
    gap: 14px;
    text-decoration: none;
    color: inherit;
    padding: 14px 0;
    border-bottom: 1px solid var(--line);
  }}
  .item:last-child {{ border-bottom: none; }}
  .item:hover .item-title {{ color: var(--amber); }}
  .item-time {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: var(--muted);
    min-width: 78px;
    padding-top: 2px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }}
  .item-source {{ opacity: 0.6; }}
  .item-title {{
    font-size: 14.5px;
    font-weight: 500;
    line-height: 1.4;
    margin: 0 0 4px;
    transition: color 0.15s ease;
  }}
  .item-summary {{
    font-size: 13px;
    color: var(--muted);
    line-height: 1.5;
    margin: 0;
  }}
  .empty {{ color: var(--muted); font-family: 'IBM Plex Mono', monospace; font-size: 13px; }}
  footer {{
    padding: 20px 32px 32px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: var(--muted);
    text-align: center;
  }}
</style>
</head>
<body>
  <header>
    <p class="eyebrow">Pre-Market Brief</p>
    <div class="title-row">
      <h1>Today's Setup</h1>
      {today_highlight}
    </div>
    <p class="generated">Generated {generated}</p>
  </header>
  {calendar}
  {briefing}
  <main>
    {sources}
  </main>
  <footer>Auto-generated overnight through pre-market &middot; informational synthesis only, not investment advice &middot; verify before trading</footer>
  <script>
    // Keep this page live if it's left open -- reload every 15 minutes
    // so headlines/briefing stay current without a manual refresh.
    setTimeout(function() {{ window.location.reload(); }}, 15 * 60 * 1000);
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    build()
