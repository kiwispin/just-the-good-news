#!/usr/bin/env python3
"""
Just The Good News — Kids Content Pipeline
Fetches RSS feeds from kid-friendly sources, scores for kid-suitability,
rewrites with child-friendly language, and writes Hugo markdown to content/kids/.

Run manually:   python scripts/pipeline-kids.py
Dry run:        python scripts/pipeline-kids.py --dry-run
Verbose:        python scripts/pipeline-kids.py --verbose
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
CONTENT_DIR = REPO_ROOT / "content" / "kids"
PUBLISHED_URLS_FILE = REPO_ROOT / "data" / "kids_published_urls.json"
IMAGES_DIR = REPO_ROOT / "static" / "images" / "kids"
UNSPLASH_API = "https://api.unsplash.com"

MIN_KIDS_SCORE = 7
MAX_ARTICLES_PER_RUN = 8
MAX_CANDIDATES_PER_RUN = 40
MAX_AGE_DAYS = 7

VALID_CATEGORIES = [
    "Animals", "Space", "Dinosaurs", "Records",
    "Inventors", "Sport", "Nature", "Science", "Funny",
]

# ---------------------------------------------------------------------------
# RSS Sources (verified working 2026-04-08)
# ---------------------------------------------------------------------------

KIDS_SOURCES = [
    {"name": "Newsround (BBC)",           "feed": "https://www.bbc.co.uk/newsround/rss.xml"},
    {"name": "Science News for Students", "feed": "https://www.snexplores.org/feed"},
    {"name": "Mongabay Kids",             "feed": "https://kids.mongabay.com/feed/"},
    {"name": "ScienceDaily",              "feed": "https://www.sciencedaily.com/rss/top.xml"},
    {"name": "NewsForKids.net",           "feed": "https://newsforkids.net/feed"},
    {"name": "Good News Network Kids",    "feed": "https://www.goodnewsnetwork.org/category/news/kids/feed/"},
    {"name": "Jane Goodall Institute",    "feed": "https://news.janegoodall.org/feed"},
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
# Unsplash image fetch — same 4-query cascade as main pipeline
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
        resp = requests.get(
            f"{UNSPLASH_API}/search/photos",
            params={"query": query, "per_page": 3, "orientation": "landscape"},
            headers={"Authorization": f"Client-ID {access_key}"},
            timeout=15,
        )
        if resp.status_code != 200:
            if verbose:
                print(f"    Unsplash HTTP {resp.status_code} for: {query[:50]}")
            return None

        results = resp.json().get("results", [])
        if not results:
            if verbose:
                print(f"    Unsplash: no results for: {query[:50]}")
            return None

        # Relevance check: ensure the photo actually matches the query.
        # Unsplash returns *something* even when the match is poor — a polar bear
        # for "tarantula spider", chips for "computer chip". Reject if none of the
        # meaningful query words appear in the photo's alt_description or description.
        query_keywords = {w.lower() for w in query.split() if len(w) > 3}
        photo = None
        for candidate in results:
            alt = (candidate.get("alt_description") or "").lower()
            desc = (candidate.get("description") or "").lower()
            photo_text = alt + " " + desc
            if not query_keywords or any(kw in photo_text for kw in query_keywords):
                photo = candidate
                break
        if photo is None:
            if verbose:
                best_alt = (results[0].get("alt_description") or "no description")[:60]
                print(f"    Unsplash: relevance mismatch for {query!r} — best photo: {best_alt!r}")
            return None
        photo_id = photo["id"]
        download_location = photo["links"]["download_location"]
        img_url = photo["urls"]["regular"]
        photographer = photo["user"]["name"]
        photographer_url = photo["user"]["links"]["html"]

        # Trigger download endpoint (required by Unsplash API guidelines)
        requests.get(
            download_location,
            headers={"Authorization": f"Client-ID {access_key}"},
            timeout=10,
        )

        img_resp = requests.get(
            img_url,
            params={"w": "1200", "q": "85", "fit": "crop", "auto": "format"},
            timeout=30,
        )
        if img_resp.status_code != 200:
            return None

        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        img_path = IMAGES_DIR / f"{slug}.jpg"
        img_path.write_bytes(img_resp.content)

        if verbose:
            print(f"    Image saved: {img_path.name} ({len(img_resp.content)//1024}KB) — {photographer}")

        return {
            "path": f"images/kids/{slug}.jpg",
            "photographer": photographer,
            "photographer_url": photographer_url,
            "unsplash_id": photo_id,
        }

    except Exception as e:
        if verbose:
            print(f"    Unsplash error: {e}")
        return None


# ---------------------------------------------------------------------------
# Step 1: Fetch candidates
# ---------------------------------------------------------------------------

def _parse_pub_date(entry) -> Optional[datetime]:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def fetch_candidates(existing_urls: Set[str], verbose: bool = False) -> List[Dict]:
    """Pull articles from all kids RSS sources, return unseen candidates."""
    candidates = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)

    for source in KIDS_SOURCES:
        if verbose:
            print(f"  Fetching: {source['name']}")
        try:
            feed = feedparser.parse(source["feed"], agent="JustTheGoodNews/1.0")
            if feed.bozo and not feed.entries:
                if verbose:
                    print(f"    WARNING: feed error — {feed.bozo_exception}")
                continue

            for entry in feed.entries:
                url = getattr(entry, "link", "").strip()
                if not url or url in existing_urls:
                    continue

                pub_date = _parse_pub_date(entry)
                if pub_date and pub_date < cutoff:
                    continue

                title = getattr(entry, "title", "").strip()
                if not title:
                    continue

                description = getattr(entry, "summary", "") or getattr(entry, "description", "")
                description = re.sub(r"<[^>]+>", " ", description).strip()
                description = re.sub(r"\s+", " ", description)[:1000]

                candidates.append({
                    "title": title,
                    "link": url,
                    "description": description,
                    "source": source["name"],
                    "pub_date": pub_date.isoformat() if pub_date else "",
                })

        except Exception as e:
            if verbose:
                print(f"    ERROR: {e}")
            continue

    if verbose:
        print(f"  Total new candidates: {len(candidates)}")
    return candidates


# ---------------------------------------------------------------------------
# Step 2: Score for kid-suitability
# ---------------------------------------------------------------------------

KIDS_SCORE_PROMPT = """\
Rate this article 1-10 for how much an 8-14 year old would enjoy reading it.

Score HIGH (8-10) for: animals, wildlife, space, dinosaurs/prehistoric life, world records,
young achievers, cool inventions, sport victories, nature discoveries, funny or weird stories,
science breakthroughs explained simply.

Score LOW (1-3) for: crime, violence, illness, death, war, politics, finance, economics,
workplace news, natural disasters, anything distressing.

Score MEDIUM (4-6) for: general human interest, community stories, technology (non-invention).

Title: {title}
Description: {description}

Reply with only a JSON object: {{"score": N, "reason": "one sentence"}}"""


def score_article(article: Dict, client) -> Dict:
    prompt = KIDS_SCORE_PROMPT.format(
        title=article["title"],
        description=article["description"][:600],
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    result = json.loads(raw)
    return {"score": int(result["score"]), "reason": result.get("reason", "")}


# ---------------------------------------------------------------------------
# Step 3: Rewrite with kid-friendly language
# ---------------------------------------------------------------------------

KIDS_REWRITE_PROMPT = """\
You are an enthusiastic primary school teacher telling a curious 10-year-old about something
amazing that just happened in the world.

Write:
1. A headline - maximum 80 characters, exciting and clear, no jargon
2. A summary - exactly 2-3 sentences, simple language, sense of wonder, age 8-14 reading level
3. A category - pick exactly one: Animals, Space, Dinosaurs, Records, Inventors, Sport, Nature, Science, Funny

HEADLINE RULES:
- NEVER start with "Scientists", "Researchers", "Experts", or "A new study"
- Lead with the amazing thing itself — the animal, the place, the discovery, the record
- Use vivid action verbs or exciting openers
- Good examples: "A Baby Elephant Just...", "Hidden Deep in the Ocean...", "Meet the Robot That...",
  "This Tiny Frog Can...", "For the First Time Ever...", "World Record Broken!", "Inside the Cave Where..."

Use active voice. Explain any technical terms in plain English inside brackets.
End the summary with something that sparks curiosity or makes the reader smile.

Also write:
4. An image_query - 2-3 words for an Unsplash photo search that will find the RIGHT image.
   RULES (follow exactly):
   - Describe only what should PHYSICALLY APPEAR in the photo — concrete, visible, real things
   - NO metaphors or figures of speech ("hotter than lava" is a metaphor — do NOT use "lava")
   - NO ambiguous words that mean two different things: "chip" (food or circuit?), "bat" (animal or sport?),
     "seal" (animal or action?), "crane" (bird or machine?), "bear" (animal or stock market?)
   - For technology/science: describe what it LOOKS LIKE, not what it does:
     "circuit board close-up" not "computer chip", "telescope observatory" not "space research"
   - For animals: use the common English name, not Latin species names: "tarantula spider" not "Satyrex"
   - For space: be specific: "saturn rings planet" not just "space"
   - If the subject is microscopic, abstract, or so rare Unsplash won't have it — write "" (empty)
   Good examples: "tarantula spider", "giant panda cub", "circuit board close-up", "coral reef fish",
   "cheetah running savanna", "astronaut spacewalk", "T-rex skeleton museum", "emperor penguin colony"

Reply with JSON: {{"headline": "...", "summary": "...", "category": "...", "image_query": "..."}}

Article title: {title}
Article text: {text}"""


def rewrite_article(article: Dict, client) -> Dict:
    prompt = KIDS_REWRITE_PROMPT.format(
        title=article["title"],
        text=article["description"][:800],
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    result = json.loads(raw)

    category = result.get("category", "Science")
    if category not in VALID_CATEGORIES:
        category = "Science"

    return {
        "headline": result.get("headline", article["title"])[:80],
        "summary": result.get("summary", "")[:600],
        "category": category,
        "image_query": result.get("image_query", "")[:80],
    }


# ---------------------------------------------------------------------------
# Step 4: Write Hugo markdown file
# ---------------------------------------------------------------------------

def _escape_yaml(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def create_kids_post(
    article: Dict,
    rewritten: Dict,
    image: Optional[Dict] = None,
    dry_run: bool = False,
) -> Path:
    now = datetime.now(timezone.utc)
    date_prefix = now.strftime("%Y-%m-%d")
    slug = slugify(rewritten["headline"], max_length=60, word_boundary=True)
    filename = f"{date_prefix}-{slug}.md"
    filepath = CONTENT_DIR / filename

    image_yaml = ""
    if image:
        image_yaml = (
            f'\nimage: "{image["path"]}"'
            f'\nimage_credit: "{_escape_yaml(image["photographer"])}"'
            f'\nimage_credit_url: "{image["photographer_url"]}"'
        )

    content = f"""---
title: "{_escape_yaml(rewritten['headline'])}"
date: {now.isoformat()}
draft: false
summary: "{_escape_yaml(rewritten['summary'])}"
category: "{rewritten['category']}"
source_url: "{article['link']}"
source_name: "{_escape_yaml(article['source'])}"{image_yaml}
---
"""

    if not dry_run:
        CONTENT_DIR.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")

    return filepath


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(dry_run: bool = False, verbose: bool = False) -> None:
    print(f"{'[DRY RUN] ' if dry_run else ''}Kids pipeline — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")

    try:
        from anthropic import Anthropic
        client = Anthropic()
    except ImportError:
        print("ERROR: anthropic package not installed. Run: pip install anthropic")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR initialising Anthropic client: {e}")
        sys.exit(1)

    unsplash_key = os.environ.get("UNSPLASH_ACCESS_KEY", "")
    if not unsplash_key and verbose:
        print("  NOTE: UNSPLASH_ACCESS_KEY not set — articles will have no images")

    # Step 1: Fetch
    print("\n[1/4] Fetching RSS feeds...")
    existing_urls = load_published_urls()
    candidates = fetch_candidates(existing_urls, verbose=verbose)
    candidates = candidates[:MAX_CANDIDATES_PER_RUN]

    if not candidates:
        print("  No new candidates found. Pipeline complete.")
        return

    # Step 2: Score
    print(f"\n[2/4] Scoring {len(candidates)} candidates...")
    passing = []
    for i, article in enumerate(candidates, 1):
        try:
            result = score_article(article, client)
            score = result["score"]
            if verbose:
                status = "PASS" if score >= MIN_KIDS_SCORE else "skip"
                print(f"  [{i:2}/{len(candidates)}] {score}/10 {status} — {article['title'][:55]}")
            if score >= MIN_KIDS_SCORE:
                article["_score"] = score
                passing.append(article)
        except json.JSONDecodeError as e:
            if verbose:
                print(f"  [{i:2}] Score parse error: {e}")
        except Exception as e:
            if verbose:
                print(f"  [{i:2}] Score error: {e}")

    passing.sort(key=lambda a: a.get("_score", 0), reverse=True)
    passing = passing[:MAX_ARTICLES_PER_RUN]
    print(f"  {len(passing)} articles passed (score >= {MIN_KIDS_SCORE})")

    if not passing:
        print("  No articles passed. Pipeline complete.")
        return

    # Step 3: Rewrite and publish
    print(f"\n[3/4] Rewriting {len(passing)} articles...")
    published_links = []
    for article in passing:
        try:
            rewritten = rewrite_article(article, client)

            # Single specific query — no generic fallbacks.
            # A wrong image is worse than no image; the card shows an emoji placeholder
            # when image is None, which is always better than a mismatched stock photo.
            image_query = rewritten.get("image_query", "").strip()

            slug = slugify(rewritten["headline"], max_length=60, word_boundary=True)
            date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            img_slug = f"{date_prefix}-{slug}"

            image = None
            if unsplash_key and image_query:
                image = fetch_unsplash_image(image_query, img_slug, unsplash_key, verbose=verbose)

            filepath = create_kids_post(article, rewritten, image=image, dry_run=dry_run)

            if verbose:
                print(f"  {'(dry) ' if dry_run else ''}-> {filepath.name}")
                print(f"    Headline:  {rewritten['headline']}")
                print(f"    Category:  {rewritten['category']}  Score: {article.get('_score', '?')}")
                print(f"    Image:     {'OK ' + image['photographer'] if image else 'none'}")
            else:
                print(f"  {'(dry) ' if dry_run else ''}-> {filepath.name}")

            published_links.append(article["link"])

        except json.JSONDecodeError as e:
            print(f"  Parse error for '{article['title'][:40]}': {e}")
        except Exception as e:
            print(f"  Error for '{article['title'][:40]}': {e}")

    # Step 4: Save dedup state
    if not dry_run and published_links:
        print(f"\n[4/4] Saving {len(published_links)} URLs to dedup store...")
        save_published_urls(existing_urls, published_links)
    elif dry_run:
        print(f"\n[4/4] Dry run — skipping dedup state update")
    else:
        print(f"\n[4/4] No new articles published")

    print(f"\nDone. Published {len(published_links)} new kid article(s).")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Just The Good News — Kids content pipeline")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and score but do not write files or update state")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print detailed progress")
    args = parser.parse_args()
    run_pipeline(dry_run=args.dry_run, verbose=args.verbose)
