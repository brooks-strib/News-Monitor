"""
Minnesota News Monitor v1.1
======================
Checks a list of Minnesota news sources every 15 minutes (via GitHub Actions).
Sends new stories matching your criteria to a Slack channel.

SETUP:
  1. Add your Slack webhook URL to GitHub Secrets as SLACK_WEBHOOK_URL
  2. Commit this file + seen_stories.json + .github/workflows/monitor.yml to your repo
  3. That's it — GitHub Actions handles the scheduling.

CUSTOMIZATION:
  - Add/remove sites in SOURCES below
  - Edit WIRE_BYLINES to flag wire stories
  - Edit the keywords in SOURCES entries for non-MN-specific sites
"""

import json
import os
import hashlib
import requests
import feedparser
from datetime import datetime, timezone
from bs4 import BeautifulSoup

# ============================================================
# CONFIGURATION — edit these as needed
# ============================================================

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# File that tracks which stories you've already seen
SEEN_FILE = "seen_stories.json"

# Wire service byline fragments — stories containing these will be flagged
WIRE_BYLINES = [
    "(AP)", "(Reuters)", "(CNN Wire)", "Associated Press",
    "Reuters Staff", "CNN Wire Service", "The Associated Press",
]

# Minnesota keywords — used to filter stories on non-MN-dedicated feeds
# (Most of your sources are MN-only, so this is a fallback safety net)
MN_KEYWORDS = [
    # Geography
    "Minnesota", "Minneapolis", "St. Paul", "Saint Paul",
    "Twin Cities", "Duluth", "Rochester", "St. Cloud", "Bemidji",
    "Grand Rapids", "Hibbing", "Mankato", "Moorhead", "Brainerd",
    "Boundary Waters", "BWCA", "Boundary Waters Canoe",
    "Boundary Waters Wilderness",
    # Politics
    "Walz", "Klobuchar", "Ilhan Omar", "Angie Craig",
    "Tom Emmer", "Pete Stauber", "Brad Finstad",
    "Minnesota Legislature", "Minnesota Senate", "Minnesota House", 
    # Companies / Institutions
    "Mayo Clinic", "3M Company", "Medtronic", "Target Corp",
    "Best Buy", "General Mills", "Cargill", "US Bancorp",
    "UnitedHealth", "Polaris Industries",
    # Sports
    "Timberwolves", "Vikings", "Twins", "Wild", "Lynx",
    "Minnesota United", "Gophers", "University of Minnesota",
    # Immigration / federal
    "ICE Minnesota", "immigration Minnesota",
]

# ============================================================
# SOURCES
# Each entry:
#   name        — display name for Slack messages
#   type        — "rss" or "scrape"
#   url         — RSS feed URL, or homepage to scrape
#   mn_filter   — True = only pass stories matching MN_KEYWORDS
#                 False = pass all stories (site is MN-focused)
# ============================================================

SOURCES = [
    # ---- Twin Cities / Statewide ----
    {
        "name": "Pioneer Press",
        "type": "rss",
        "url": "https://www.twincities.com/feed",
        "mn_filter": False,
        "skip_wire": False,
    },
    {
        "name": "KSTP",
        "type": "rss",
        "url": "https://kstp.com/feed/",
        "mn_filter": False,
    },
    {
        "name": "KARE 11",
        "type": "rss",
        "url": "https://www.kare11.com/feeds/syndication/rss/news/",
        "mn_filter": True,
    },
    {
        "name": "CBS News Minnesota",
        "type": "rss",
        "url": "https://www.cbsnews.com/minnesota/latest/rss/",
        "mn_filter": False,
    },
    {
        "name": "FOX 9",
        "type": "rss",
        "url": "https://www.fox9.com/feeds/category/news.rss",
        "mn_filter": False,
    },
    {
        "name": "MPR News",
        "type": "rss",
        "url": "https://www.mprnews.org/feed/homepage",
        "mn_filter": False,
    },
    {
        "name": "Bring Me The News",
        "type": "rss",
        "url": "https://bringmethenews.com/feed",
        "mn_filter": False,
    },
    {
        "name": "MinnPost",
        "type": "rss",
        "url": "https://www.minnpost.com/feed",
        "mn_filter": False,
    },
    {
        "name": "Twin Cities Business",
        "type": "rss",
        "url": "https://tcbmag.com/feed",
        "mn_filter": False,
    },
    {
        "name": "Minneapolis/St. Paul Business Journal",
        "type": "rss",
        "url": "https://feeds.bizjournals.com/bizj_twincities",
        "mn_filter": False,
    },
    # ---- Greater Minnesota ----
    {
        "name": "Duluth News Tribune",
        "type": "rss",
        "url": "https://www.duluthnewstribune.com/news.rss",
        "mn_filter": False,
    },
      {
        "name": "Post Bulletin (Rochester)",
        "type": "scrape",
        "url": "https://www.postbulletin.com",
        "mn_filter": True,
    },
    {
        "name": "Bemidji Pioneer",
        "type": "rss",
        "url": "https://www.bemidjipioneer.com/news.rss",
        "mn_filter": False,
    },
    {
        "name": "St. Cloud Times",
        "type": "rss",
        "url": "https://www.sctimes.com/rss/news/local.xml",
        "mn_filter": False,
    },
  # ---- National Outlets (MN keyword filtered) ----
    {
        "name": "New York Times",
        "type": "rss",
        "url": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "mn_filter": True,
    },
    {
        "name": "Washington Post",
        "type": "rss",
        "url": "https://feeds.washingtonpost.com/rss/national",
        "mn_filter": True,
    },
    {
        "name": "The Guardian",
        "type": "rss",
        "url": "https://www.theguardian.com/us-news/rss",
        "mn_filter": True,
    },
    {
        "name": "Politico",
        "type": "rss",
        "url": "https://www.politico.com/rss/politicopicks.xml",
        "mn_filter": True,
    },
    {
        "name": "Wall Street Journal",
        "type": "rss",
        "url": "https://feeds.content.dowjones.io/public/rss/RSSWorldNews",
        "mn_filter": True,
    },
]

# ============================================================
# CORE LOGIC — no need to edit below this line
# ============================================================

def load_seen():
    """Load the set of story IDs we've already sent to Slack."""
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    """Save seen story IDs, keeping only the most recent 2000."""
    seen_list = list(seen)[-2000:]
    with open(SEEN_FILE, "w") as f:
        json.dump(seen_list, f)


def story_id(url, title):
    """Create a stable unique ID for a story."""
    raw = (url or "") + (title or "")
    return hashlib.md5(raw.encode()).hexdigest()


def is_wire(title, summary, author):
    """Return True if the story looks like wire-service content."""
    text = " ".join(filter(None, [title, summary, author]))
    for marker in WIRE_BYLINES:
        if marker.lower() in text.lower():
            return True
    return False


def matches_mn(title, summary):
    """Return True if the story mentions Minnesota or related terms."""
    text = " ".join(filter(None, [title, summary])).lower()
    return any(kw.lower() in text for kw in MN_KEYWORDS)


def fetch_rss(source):
    """Fetch and parse an RSS feed, return list of story dicts."""
    stories = []
    try:
        feed = feedparser.parse(source["url"])
        for entry in feed.entries:
            title   = entry.get("title", "").strip()
            url     = entry.get("link", "").strip()
            summary = entry.get("summary", "").strip()
            author  = entry.get("author", "").strip()

            if source["mn_filter"] and not matches_mn(title, summary):
                continue

            stories.append({
                "title":   title,
                "url":     url,
                "summary": summary,
                "author":  author,
                "source":  source["name"],
            })
    except Exception as e:
        print(f"  RSS error for {source['name']}: {e}")
    return stories


def fetch_scrape(source):
    """Scrape a standard news homepage for article links."""
    stories = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsMonitor/1.0)"}
        resp = requests.get(source["url"], headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        seen_hrefs = set()
        for a in soup.find_all("a", href=True):
            href  = a["href"].strip()
            title = a.get_text(strip=True)

            # Skip very short link text (navigation, icons, etc.)
            if len(title) < 20:
                continue
            # Skip duplicates within this scrape
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)

            # Make relative URLs absolute
            if href.startswith("/"):
                from urllib.parse import urlparse
                base = source["url"]
                parsed = urlparse(base)
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            elif not href.startswith("http"):
                continue

            # Only include links that look like article paths
            # (skip homepage, section pages, etc.)
            path = href.replace(source["url"], "")
            if path.count("/") < 1 or len(path) < 10:
                continue

            if source["mn_filter"] and not matches_mn(title, ""):
                continue

            stories.append({
                "title":   title,
                "url":     href,
                "summary": "",
                "author":  "",
                "source":  source["name"],
            })

    except Exception as e:
        print(f"  Scrape error for {source['name']}: {e}")
    return stories


def send_slack(source_name, stories):
    """Post all new stories from one source as a single Slack message."""
    if not SLACK_WEBHOOK_URL:
        for story in stories:
            print(f"  [NO WEBHOOK] Would send: {story['title']}")
        return

    if len(stories) == 1:
        story = stories[0]
        text = f"*{source_name}*\n• <{story['url']}|{story['title']}>"
        if story.get("wire_flag"):
            text += "  ⚠️ _Possible wire content_"
    else:
        lines = [f"*{source_name}* — {len(stories)} new stories"]
        for story in stories:
            wire_note = "  ⚠️" if story.get("wire_flag") else ""
            lines.append(f"• <{story['url']}|{story['title']}>{wire_note}")
        text = "\n".join(lines)

    try:
        resp = requests.post(
            SLACK_WEBHOOK_URL,
            json={
                "text": text,
                "unfurl_links": False,
                "unfurl_media": False,
            },
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"  Slack error: {e}")


def run():
    print(f"\n{'='*50}")
    print(f"Minnesota News Monitor — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*50}")

    seen = load_seen()
    seen_headlines = set()
    new_count = 0

    for source in SOURCES:
        print(f"\nChecking: {source['name']} ({source['type'].upper()})")

        if source["type"] == "rss":
            stories = fetch_rss(source)
        else:
            stories = fetch_scrape(source)

        print(f"  Found {len(stories)} items")

        new_stories = []
        for story in stories:
            sid = story_id(story["url"], story["title"])
            if sid in seen:
                continue

            seen.add(sid)
            headline_key = story["title"].strip().lower()
            if headline_key in seen_headlines:
                print(f"  ✗ DUPLICATE HEADLINE: {story['title'][:80]}")
                continue
            seen_headlines.add(headline_key)
            wire_flag = is_wire(story["title"], story["summary"], story["author"])

            if wire_flag and source.get("skip_wire"):
                print(f"  ✗ SKIPPED WIRE: {story['title'][:80]}")
                continue

            story["wire_flag"] = wire_flag
            new_stories.append(story)
            wire_label = " [WIRE?]" if wire_flag else ""
            print(f"  ✓ NEW{wire_label}: {story['title'][:80]}")
            new_count += 1

        if new_stories:
            send_slack(source["name"], new_stories)

    save_seen(seen)
    print(f"\nDone. {new_count} new stories sent to Slack.")


if __name__ == "__main__":
    run()
