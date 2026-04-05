#!/usr/bin/env python3
"""
Just The Good News — Content Pipeline
Fetches RSS feeds, scores articles with Claude AI, summarises qualifying ones,
and writes Hugo-compatible markdown files.

Run manually:   python scripts/pipeline.py
Dry run:        python scripts/pipeline.py --dry-run
Verbose:        python scripts/pipeline.py --verbose
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Set, List, Dict

import feedparser
import requests
from slugify import slugify

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
CONTENT_DIR = REPO_ROOT / "content" / "posts"
PUBLISHED_URLS_FILE = REPO_ROOT / "data" / "published_urls.json"
IMAGES_DIR = REPO_ROOT / "static" / "images" / "articles"
UNSPLASH_API = "https://api.unsplash.com"

MIN_SCORE = 7
MAX_ARTICLES_PER_RUN = 10
MAX_CANDIDATES_PER_RUN = 30
MAX_AGE_DAYS = 7

VALID_CATEGORIES = [
    "environment",
    "health-science",
    "community",
    "tech-for-good",
    "education",
    "arts-culture",
    "justice-equality",
    "economy-work",
]

VALID_REGIONS = ["nz", "au", "uk", "us", "ca", "global"]

# ---------------------------------------------------------------------------
# RSS Sources
# ---------------------------------------------------------------------------

SOURCES = [
    # Primary — dedicated positive news outlets
    {"name": "Positive News",        "feed": "https://www.positive.news/feed/"},
    {"name": "Good News Network",    "feed": "https://www.goodnewsnetwork.org/feed/"},
    {"name": "Reasons to be Cheerful","feed": "https://reasonstobecheerful.world/feed/"},
    {"name": "The Good News Hub",    "feed": "https://www.goodnewshub.com/feed/"},
    {"name": "Good Good Good",       "feed": "https://goodgoodgood.co/feed/"},
    {"name": "Future Crunch",        "feed": "https://futurecrunch.com/feed/"},
    # Secondary — curated sections from mainstream outlets
    {"name": "HuffPost Good News",   "feed": "https://www.huffpost.com/section/good-news/feed"},
    # Niche sources
    {"name": "ScienceDaily",         "feed": "https://www.sciencedaily.com/rss/top/science.xml"},
    {"name": "Treehugger",           "feed": "https://www.treehugger.com/feeds/latest"},
]


# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------

def load_published_urls() -> Set[str]:
    if PUBLISHED_URLS_FILE.exists():
        with open(PUBLISHED_URLS_FILE) as f:
            data = json.load(f)
        return set(data.get("urls", []))
    return set()


def save_published_urls(existing: Set[str], new_urls: List[str]) -> None:
    PUBLISHED_URLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    all_urls = sorted(existing | set(new_urls))
    with open(PUBLISHED_URLS_FILE, "w") as f:
        json.dump({"urls": all_urls}, f, indent=2)


# ---------------------------------------------------------------------------
# Unsplash image fetch
# ---------------------------------------------------------------------------

def fetch_unsplash_image(
    query: str,
    slug: str,
    access_key: str,
    verbose: bool = False,
) -> Optional[Dict]:
    """Search Unsplash, download a landscape photo, return metadata or None."""
    if not access_key:
        return None
    try:
        # Search for a landscape photo
        resp = requests.get(
            f"{UNSPLASH_API}/search/photos",
            params={"query": query, "per_page": 3, "orientation": "landscape"},
            headers={"Authorization": f"Client-ID {access_key}"},
            timeout=15,
        )
        if resp.status_code != 200:
            if verbose:
                print(f"    Unsplash search HTTP {resp.status_code} for: {query[:50]}")
            return None

        results = resp.json().get("results", [])
        if not results:
            if verbose:
                print(f"    Unsplash: no results for: {query[:50]}")
            return None

        photo = results[0]
        photo_id = photo["id"]
        download_location = photo["links"]["download_location"]
        img_url = photo["urls"]["regular"]  # ~1080px wide
        photographer = photo["user"]["name"]
        photographer_url = photo["user"]["links"]["html"]

        # Trigger download endpoint (required by Unsplash API guidelines)
        requests.get(
            download_location,
            headers={"Authorization": f"Client-ID {access_key}"},
            timeout=10,
        )

        # Download the image at 1200px width
        img_resp = requests.get(
            img_url,
            params={"w": "1200", "q": "85", "fit": "crop", "auto": "format"},
            timeout=30,
        )
        if img_resp.status_code != 200:
            if verbose:
                print(f"    Unsplash: image download failed HTTP {img_resp.status_code}")
            return None

        # Save to static/images/articles/
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        img_path = IMAGES_DIR / f"{slug}.jpg"
        img_path.write_bytes(img_resp.content)

        if verbose:
            print(f"    Unsplash: saved {img_path.name} ({len(img_resp.content)//1024}KB) — {photographer}")

        return {
            "path": f"images/articles/{slug}.jpg",
            "photographer": photographer,
            "photographer_url": photographer_url,
            "unsplash_id": photo_id,
        }

    except Exception as e:
        if verbose:
            print(f"    Unsplash: error — {e}")
        return None


# ---------------------------------------------------------------------------
# Step 1: Fetch candidates from RSS feeds
# ---------------------------------------------------------------------------

def _parse_pub_date(entry) -> Optional[datetime]:
    """Extract and normalise publication date from a feed entry."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def fetch_candidates(existing_urls: Set[str], verbose: bool = False) -> List[Dict]:
    """Pull articles from all RSS sources, return candidates not yet published."""
    candidates = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)

    for source in SOURCES:
        if verbose:
            print(f"  Fetching: {source['name']} ({source['feed']})")
        try:
            feed = feedparser.parse(source["feed"], agent="JustTheGoodNews/1.0")
            if feed.bozo and not feed.entries:
                if verbose:
                    print(f"    WARNING: Feed error for {source['name']}: {feed.bozo_exception}")
                continue

            for entry in feed.entries:
                url = getattr(entry, "link", "").strip()
                if not url or url in existing_urls:
                    continue

                pub_date = _parse_pub_date(entry)
                if pub_date and pub_date < cutoff:
                    continue  # Too old

                title = getattr(entry, "title", "").strip()
                description = getattr(entry, "summary", "") or getattr(entry, "description", "")
                # Strip HTML tags from description
                description = re.sub(r"<[^>]+>", " ", description).strip()
                description = re.sub(r"\s+", " ", description)[:1000]

                if not title:
                    continue

                candidates.append({
                    "title": title,
                    "link": url,
                    "description": description,
                    "source": source["name"],
                    "pub_date": pub_date.isoformat() if pub_date else "",
                })

        except Exception as e:
            if verbose:
                print(f"    ERROR fetching {source['name']}: {e}")
            continue

    if verbose:
        print(f"  Found {len(candidates)} new candidates across all feeds")
    return candidates


# ---------------------------------------------------------------------------
# Step 2: Score articles for positivity
# ---------------------------------------------------------------------------

SCORING_PROMPT = """\
You are a content curator for "Just The Good News", a positive news website.
Your job is to evaluate whether a news article represents genuinely good news.

Rate this article 1-10:
- 10: Unambiguously wonderful (disease cured, species saved, record achievement)
- 7-9: Clearly positive (community achievement, scientific progress, environmental win)
- 4-6: Mixed, mildly positive, or uncertain
- 1-3: Negative news with positive spin ("despite the tragedy, one survivor...")
- 0: Not news / promotional / political propaganda / opinion piece

Title: {title}
Description: {description}

Rules:
- Exclude any article primarily about death, violence, conflict, or disaster
- Exclude political content or partisan framing
- Exclude promotional or sponsored content
- Exclude "silver lining" framing of bad events

Respond ONLY with a JSON object (no markdown, no explanation):
{{"score": <integer 0-10>, "reason": "<one sentence>"}}"""


def score_article(article: Dict, client) -> Dict:
    """Ask Claude to rate article positivity. Returns {score, reason}."""
    prompt = SCORING_PROMPT.format(
        title=article["title"],
        description=article["description"][:600],
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",  # Fast + cheap for scoring
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    result = json.loads(raw)
    return {"score": int(result["score"]), "reason": result.get("reason", "")}


# ---------------------------------------------------------------------------
# Step 3: Summarise and categorise passing articles
# ---------------------------------------------------------------------------

SUMMARISE_PROMPT = """\
You are writing for "Just The Good News", a positive news aggregation site.
Tone: warm, factual, uplifting — not sensational or saccharine.

Given this source article:
Title: {title}
Description: {description}
Source: {source}

Create:
1. A fresh, punchy newspaper-style headline — STRICT maximum 80 characters.
   - Count every character including spaces. If it hits 80, cut it shorter.
   - Think front-page tabloid energy: short, active, vivid. No subtitles with colons.
   - DO NOT copy the original title verbatim.
   - BAD: "Ancient Gaming Tradition: New Research Reveals Native Americans Invented Dice"
   - GOOD: "Native Americans Invented Dice Thousands of Years Ago"
2. A 2-3 sentence summary capturing the key positive outcome
3. One or two category tags from this exact list:
   environment, health-science, community, tech-for-good, education,
   arts-culture, justice-equality, economy-work
4. The best region tag from: nz, au, uk, us, ca, global

Respond ONLY with a JSON object (no markdown, no explanation):
{{
  "headline": "...",
  "summary": "...",
  "categories": ["..."],
  "region": "..."
}}"""


def process_article(article: Dict, client) -> Dict:
    """Ask Claude to generate headline, summary, categories, region."""
    prompt = SUMMARISE_PROMPT.format(
        title=article["title"],
        description=article["description"][:800],
        source=article["source"],
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    result = json.loads(raw)

    # Sanitise categories and region against allowed values
    categories = [c for c in result.get("categories", []) if c in VALID_CATEGORIES]
    if not categories:
        categories = ["community"]
    region = result.get("region", "global")
    if region not in VALID_REGIONS:
        region = "global"

    return {
        "headline": result.get("headline", article["title"])[:150],
        "summary": result.get("summary", "")[:600],
        "categories": categories,
        "region": region,
    }


# ---------------------------------------------------------------------------
# Step 4: Write Hugo markdown file
# ---------------------------------------------------------------------------

def _escape_yaml(text: str) -> str:
    """Escape text for use in a YAML double-quoted string."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def create_hugo_post(
    article: Dict,
    processed: Dict,
    image: Optional[Dict] = None,
    dry_run: bool = False,
) -> Path:
    """Write a Hugo-compatible markdown file for this article."""
    now = datetime.now(timezone.utc)
    date_prefix = now.strftime("%Y-%m-%d")

    slug = slugify(processed["headline"], max_length=60, word_boundary=True)
    filename = f"{date_prefix}-{slug}.md"
    filepath = CONTENT_DIR / filename

    categories_yaml = json.dumps(processed["categories"])

    # Build optional image front matter lines
    image_yaml = ""
    if image:
        image_yaml = (
            f'\nimage: "{image["path"]}"'
            f'\nimage_credit: "{_escape_yaml(image["photographer"])}"'
            f'\nimage_credit_url: "{image["photographer_url"]}"'
        )

    content = f"""---
title: "{_escape_yaml(processed['headline'])}"
date: {now.isoformat()}
draft: false
categories: {categories_yaml}
region: "{processed['region']}"
source: "{_escape_yaml(article['source'])}"
source_url: "{article['link']}"
summary: "{_escape_yaml(processed['summary'])}"{image_yaml}
---

{processed['summary']}

[Read the full story at {article['source']}]({article['link']})
"""

    if not dry_run:
        CONTENT_DIR.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")

    return filepath


# ---------------------------------------------------------------------------
# Step 5: Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(dry_run: bool = False, verbose: bool = False) -> None:
    """Main entry point. Fetches, scores, processes, and publishes articles."""
    print(f"{'[DRY RUN] ' if dry_run else ''}Just The Good News pipeline starting — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")

    # Lazy import Anthropic (not needed for feed-only dry runs without AI)
    try:
        from anthropic import Anthropic
        client = Anthropic()  # reads ANTHROPIC_API_KEY from environment
    except ImportError:
        print("ERROR: anthropic package not installed. Run: pip install anthropic")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR initialising Anthropic client: {e}")
        sys.exit(1)

    unsplash_key = os.environ.get("UNSPLASH_ACCESS_KEY", "")
    if not unsplash_key and verbose:
        print("  NOTE: UNSPLASH_ACCESS_KEY not set — articles will have no images")

    # --- Step 1: Fetch ---
    print("\n[1/4] Fetching RSS feeds...")
    existing_urls = load_published_urls()
    candidates = fetch_candidates(existing_urls, verbose=verbose)
    candidates = candidates[:MAX_CANDIDATES_PER_RUN]

    if not candidates:
        print("  No new candidates found. Pipeline complete.")
        return

    # --- Step 2: Score ---
    print(f"\n[2/4] Scoring {len(candidates)} candidates for positivity...")
    passing = []
    for i, article in enumerate(candidates, 1):
        try:
            result = score_article(article, client)
            score = result["score"]
            reason = result["reason"]
            if verbose:
                print(f"  [{i:2}/{len(candidates)}] {score}/10 — {article['title'][:60]}")
                if score >= MIN_SCORE:
                    print(f"           PASS: {reason}")
            if score >= MIN_SCORE:
                article["_score"] = score
                passing.append(article)
            if len(passing) >= MAX_ARTICLES_PER_RUN * 2:
                break  # Enough candidates, stop scoring
        except json.JSONDecodeError as e:
            if verbose:
                print(f"  [{i:2}] Score parse error: {e}")
        except Exception as e:
            if verbose:
                print(f"  [{i:2}] Score error: {e}")

    print(f"  {len(passing)} articles passed the positivity threshold (score ≥ {MIN_SCORE})")

    # --- Step 3: Process ---
    print(f"\n[3/4] Summarising and categorising up to {MAX_ARTICLES_PER_RUN} articles...")
    published_links = []
    for article in passing[:MAX_ARTICLES_PER_RUN]:
        try:
            processed = process_article(article, client)

            # Fetch Unsplash image using the generated headline as query
            slug = slugify(processed["headline"], max_length=60, word_boundary=True)
            date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            img_slug = f"{date_prefix}-{slug}"
            image = fetch_unsplash_image(
                query=processed["headline"],
                slug=img_slug,
                access_key=unsplash_key,
                verbose=verbose,
            )

            filepath = create_hugo_post(article, processed, image=image, dry_run=dry_run)

            if verbose:
                print(f"  {'(dry run) ' if dry_run else ''}→ {filepath.name}")
                print(f"    Headline:   {processed['headline']}")
                print(f"    Categories: {processed['categories']}  Region: {processed['region']}")
                print(f"    Image:      {'✓ ' + image['photographer'] if image else '✗ none'}")
            else:
                print(f"  {'(dry run) ' if dry_run else ''}→ {filepath.name}")

            published_links.append(article["link"])
        except json.JSONDecodeError as e:
            print(f"  Process parse error for '{article['title'][:40]}': {e}")
        except Exception as e:
            print(f"  Process error for '{article['title'][:40]}': {e}")

    # --- Step 4: Save deduplication state ---
    if not dry_run and published_links:
        print(f"\n[4/4] Saving {len(published_links)} new URLs to deduplication store...")
        save_published_urls(existing_urls, published_links)
    elif dry_run:
        print(f"\n[4/4] Dry run — skipping deduplication state update")
    else:
        print(f"\n[4/4] No new articles published")

    print(f"\nDone. Published {len(published_links)} new article(s).")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Just The Good News content pipeline")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and score articles but do not write files or update state"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print detailed progress"
    )
    args = parser.parse_args()
    run_pipeline(dry_run=args.dry_run, verbose=args.verbose)
