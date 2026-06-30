#!/usr/bin/env python3
"""
Just The Good News — Content Pipeline
Fetches RSS feeds, scores articles with the configured AI provider, summarises qualifying ones,
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

from ai_client import AIProviderError, create_ai_client

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

FATAL_AI_ERROR_MARKERS = (
    "credit balance is too low",
    "insufficient_quota",
    "quota",
    "billing",
    "authentication_error",
    "invalid api key",
    "invalid_api_key",
    "invalid x-api-key",
    "permission denied",
    "permission_denied",
    "permission_error",
)


def abort_on_fatal_ai_error(exc: Exception, context: str) -> None:
    """Fail CI when the AI provider rejects requests instead of publishing stale content."""
    if isinstance(exc, AIProviderError):
        print(f"ERROR: {context} failed because the AI provider rejected the request: {exc}")
        sys.exit(1)

    message = str(exc).lower()
    if any(marker in message for marker in FATAL_AI_ERROR_MARKERS):
        print(f"ERROR: {context} failed because the AI provider rejected the request: {exc}")
        sys.exit(1)

# ---------------------------------------------------------------------------
# RSS Sources
# ---------------------------------------------------------------------------

SOURCES = [
    # Primary — dedicated positive news outlets
    {"name": "Positive News",        "feed": "https://www.positive.news/feed/"},
    {"name": "Good News Network",    "feed": "https://www.goodnewsnetwork.org/feed/"},
    {"name": "Reasons to be Cheerful","feed": "https://reasonstobecheerful.world/feed/"},
    {"name": "Good Good Good",       "feed": "https://goodgoodgood.co/feed/"},
    {"name": "Future Crunch",        "feed": "https://medium.com/feed/future-crunch"},
    {"name": "The Optimist Daily",   "feed": "https://www.optimistdaily.com/feed/"},
    # Science & environment
    {"name": "ScienceDaily",         "feed": "https://www.sciencedaily.com/rss/top/science.xml"},
    {"name": "Mongabay",             "feed": "https://news.mongabay.com/feed/?post_type=post&feedtype=bulletpoints&topic=happy-upbeat-environmental"},
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

class UnsplashRateLimited(Exception):
    """Raised when Unsplash returns a 403/429 rate-limit response (demo tier =
    50 req/hour). Lets callers stop the query cascade instead of silently
    treating the block as 'no results' and leaving articles imageless."""


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
        if resp.status_code in (403, 429):
            if verbose:
                print(f"    Unsplash RATE LIMITED (HTTP {resp.status_code}) — hourly quota exhausted")
            raise UnsplashRateLimited()
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

    except UnsplashRateLimited:
        raise  # let the caller stop the cascade instead of swallowing it
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

BATCH_SCORING_PROMPT = """\
You are a content curator for "Just The Good News", a positive news website.
Your job is to evaluate whether a news article represents genuinely good news.

Rate each article 1-10:
- 10: Unambiguously wonderful (disease cured, species saved, record achievement)
- 7-9: Clearly positive (community achievement, scientific progress, environmental win)
- 4-6: Mixed, mildly positive, or uncertain
- 1-3: Negative news with positive spin ("despite the tragedy, one survivor...")
- 0: Not news / promotional / political propaganda / opinion piece

Rules:
- Exclude any article primarily about death, violence, conflict, or disaster
- Exclude political content or partisan framing
- Exclude promotional or sponsored content
- Exclude "silver lining" framing of bad events

Articles to evaluate:
{articles_text}

Respond ONLY with a JSON array of objects (no markdown, no explanation) matching this schema:
[
  {{"index": <integer corresponding to the article index>, "score": <integer 0-10>, "reason": "<one sentence>"}}
]

JSON Formatting Rules:
- The "reason" string MUST NOT contain any double quotes ("). If you need to quote or refer to anything, use single quotes (').
- Keep the "reason" short and concise (under 15 words)."""


def score_articles_batch(articles: List[Dict], client) -> List[Dict]:
    """Score a batch of articles in a single API call for positivity."""
    if not articles:
        return []

    articles_text = ""
    for idx, art in enumerate(articles, 1):
        articles_text += f"Article #{idx}:\nTitle: {art['title']}\nDescription: {art['description'][:600]}\n\n"

    prompt = BATCH_SCORING_PROMPT.format(articles_text=articles_text)
    raw = client.complete(prompt, max_tokens=2000)
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    
    try:
        results_list = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse JSON from AI response.\nRaw Response Content:\n{raw}\n---")
        raise e
        
    if not isinstance(results_list, list):
        raise ValueError("AI response is not a JSON array")

    scores_by_index = {}
    for item in results_list:
        try:
            item_idx = int(item.get("index"))
            score = int(item.get("score"))
            reason = item.get("reason", "")
            scores_by_index[item_idx] = {"score": score, "reason": reason}
        except (ValueError, TypeError, KeyError):
            continue

    scored_articles = []
    for idx, art in enumerate(articles, 1):
        if idx in scores_by_index:
            scored_articles.append({
                **art,
                "_score": scores_by_index[idx]["score"],
                "_reason": scores_by_index[idx]["reason"]
            })
        else:
            scored_articles.append({
                **art,
                "_score": 0,
                "_reason": "Skipped (failed to return score in batch)"
            })
    return scored_articles


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
1. A fresh, engaging headline (DO NOT copy the original title verbatim)
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
    """Ask the configured AI provider to generate headline and metadata."""
    prompt = SUMMARISE_PROMPT.format(
        title=article["title"],
        description=article["description"][:800],
        source=article["source"],
    )
    raw = client.complete(prompt, max_tokens=500)
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

    # Positivity score (saved so the homepage can feature the best story)
    score = article.get("_score", 0)

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
summary: "{_escape_yaml(processed['summary'])}"
featured_score: {score}{image_yaml}
---

{processed['summary']}

[Read the full story at {article['source']}]({article['link']})
"""

    if not dry_run:
        CONTENT_DIR.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")

    return filepath


# ---------------------------------------------------------------------------
# Today's observance  (Checkiday free API — no key required)
# ---------------------------------------------------------------------------

DATA_DIR = REPO_ROOT / "data"

# Keywords that flag a sombre/awareness day — exclude these from selection
_SOMBRE_KEYWORDS = [
    "awareness", "prevention", "remembrance", "memorial", "genocide",
    "holocaust", "tragedy", "survivors", "reflection on", "day of silence",
    "day of action", "suicide", "abuse", "violence against", "missing",
    "slavery", "victims", "ptsd", "trauma",
]

TODAY_PICK_PROMPT = """\
Here is a list of today's national days and observances:

{observances}

Pick the single most fun, lighthearted, or delightful observance.
Prefer: food days, animal days, nature days, quirky or playful days, positive achievements.
Avoid anything that sounds like a health-awareness day, disease awareness, or sombre topic.

Respond with ONLY the exact name of your chosen observance — nothing else.
If none are suitable, respond with exactly the word: NONE"""


def fetch_today_observance(client, dry_run: bool = False, verbose: bool = False) -> None:
    """Fetch today's best observance from Checkiday and write data/today.json."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            f"https://www.checkiday.com/api/3/?d={today}",
            timeout=10,
            headers={"User-Agent": "JustTheGoodNews/1.0"},
        )
        if resp.status_code != 200:
            print(f"  Checkiday API HTTP {resp.status_code} — skipping today banner")
            return

        holidays = resp.json().get("holidays", [])
        names = [h["name"] for h in holidays]

        # Filter out sombre / awareness days
        def is_sombre(name: str) -> bool:
            n = name.lower()
            return any(kw in n for kw in _SOMBRE_KEYWORDS)

        candidates = [n for n in names if not is_sombre(n)]

        if not candidates:
            print("  Today's observance: no suitable candidates after filtering")
            return

        if verbose:
            print(f"  Checkiday: {len(names)} total → {len(candidates)} after filter: {candidates}")

        # Ask the AI provider to pick the most delightful one
        prompt = TODAY_PICK_PROMPT.format(observances="\n".join(f"- {c}" for c in candidates))
        chosen = client.complete(prompt, max_tokens=200).strip().strip('"').strip("'")

        if chosen == "NONE" or chosen not in candidates:
            # Fall back to first candidate if AI response is unexpected
            chosen = candidates[0]

        print(f"  Today is: {chosen}")

        if not dry_run:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            (DATA_DIR / "today.json").write_text(
                json.dumps({"date": today, "observance": chosen}, ensure_ascii=False),
                encoding="utf-8",
            )

    except Exception as e:
        print(f"  Today's observance fetch failed: {e}")


# ---------------------------------------------------------------------------
# Step 5: Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(dry_run: bool = False, verbose: bool = False) -> None:
    """Main entry point. Fetches, scores, processes, and publishes articles."""
    print(f"{'[DRY RUN] ' if dry_run else ''}Just The Good News pipeline starting — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")

    try:
        client = create_ai_client()
    except Exception as e:
        print(f"ERROR initialising AI client: {e}")
        sys.exit(1)

    if verbose:
        print(f"  AI provider: {client.provider} ({client.model})")

    unsplash_key = os.environ.get("UNSPLASH_ACCESS_KEY", "")
    if not unsplash_key and verbose:
        print("  NOTE: UNSPLASH_ACCESS_KEY not set — articles will have no images")

    # --- Today's observance ---
    print("\n[0/4] Fetching today's observance...")
    fetch_today_observance(client, dry_run=dry_run, verbose=verbose)

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
    score_attempts = 0
    score_failures = 0

    batch_size = 15
    batches = [candidates[i:i + batch_size] for i in range(0, len(candidates), batch_size)]

    for b_idx, batch in enumerate(batches):
        score_attempts += 1
        try:
            scored_batch = score_articles_batch(batch, client)
            for item_idx, article in enumerate(scored_batch, 1):
                global_idx = b_idx * batch_size + item_idx
                score = article.get("_score", 0)
                reason = article.get("_reason", "")
                if verbose:
                    print(f"  [{global_idx:2}/{len(candidates)}] {score}/10 — {article['title'][:60]}")
                    if score >= MIN_SCORE:
                        print(f"           PASS: {reason}")
                if score >= MIN_SCORE:
                    passing.append(article)
            if len(passing) >= MAX_ARTICLES_PER_RUN * 2:
                break  # Enough candidates, stop scoring
            import time
            time.sleep(2)
        except json.JSONDecodeError as e:
            score_failures += 1
            print(f"  [Batch {b_idx+1}] Score parse error: {e}")
        except Exception as e:
            score_failures += 1
            abort_on_fatal_ai_error(e, "Scoring")
            print(f"  [Batch {b_idx+1}] Score error: {e}")

    if score_attempts and score_failures == score_attempts and not passing:
        print("ERROR: Every scoring attempt failed; refusing to continue with stale content.")
        sys.exit(1)

    print(f"  {len(passing)} articles passed the positivity threshold (score ≥ {MIN_SCORE})")

    # --- Step 3: Process ---
    print(f"\n[3/4] Summarising and categorising up to {MAX_ARTICLES_PER_RUN} articles...")
    published_links = []
    process_attempts = 0
    process_failures = 0
    for article in passing[:MAX_ARTICLES_PER_RUN]:
        process_attempts += 1
        try:
            processed = process_article(article, client)

            # Fetch Unsplash image — try progressively simpler queries
            slug = slugify(processed["headline"], max_length=60, word_boundary=True)
            date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            img_slug = f"{date_prefix}-{slug}"

            # Build fallback query chain: headline → key nouns → category → generic
            cat_slug = processed["categories"][0] if processed["categories"] else "community"
            cat_phrase = cat_slug.replace("-", " ")
            # Extract first ~4 meaningful words from the headline as a simpler query
            stop = {"the","a","an","of","in","on","at","to","for","and","or","but",
                    "how","why","what","when","where","who","as","by","from","with",
                    "its","this","that","these","those","is","are","was","were"}
            headline_words = [w for w in processed["headline"].replace(":", " ").replace("—", " ").split()
                              if w.lower() not in stop and len(w) > 2]
            short_query = " ".join(headline_words[:4])

            # Category-to-photo-topic mapping for final meaningful fallback
            cat_fallbacks = {
                "environment":      "nature landscape green",
                "health-science":   "medical research laboratory",
                "community":        "community people together",
                "tech-for-good":    "technology innovation future",
                "education":        "education learning students",
                "arts-culture":     "art culture creative",
                "justice-equality": "diverse community justice",
                "economy-work":     "work collaboration success",
            }
            cat_photo = cat_fallbacks.get(cat_slug, "positive people nature")

            image = None
            if unsplash_key:
                for q in [processed["headline"], short_query, cat_phrase, cat_photo]:
                    if not q.strip():
                        continue
                    try:
                        image = fetch_unsplash_image(
                            query=q,
                            slug=img_slug,
                            access_key=unsplash_key,
                            verbose=verbose,
                        )
                    except UnsplashRateLimited:
                        print("    Unsplash rate limit reached — publishing without image (backfill will fill it)")
                        break
                    if image:
                        if verbose:
                            print(f"    Image query: '{q}'")
                        break

            filepath = create_hugo_post(article, processed, image=image, dry_run=dry_run)

            if verbose:
                print(f"  {'(dry run) ' if dry_run else ''}→ {filepath.name}")
                print(f"    Headline:   {processed['headline']}")
                print(f"    Categories: {processed['categories']}  Region: {processed['region']}")
                print(f"    Image:      {'✓ ' + image['photographer'] if image else '✗ none'}")
            else:
                print(f"  {'(dry run) ' if dry_run else ''}→ {filepath.name}")

            published_links.append(article["link"])
            import time
            time.sleep(2)
        except json.JSONDecodeError as e:
            process_failures += 1
            print(f"  Process parse error for '{article['title'][:40]}': {e}")
        except Exception as e:
            process_failures += 1
            abort_on_fatal_ai_error(e, "Processing")
            print(f"  Process error for '{article['title'][:40]}': {e}")

    if process_attempts and process_failures == process_attempts and not published_links:
        print("ERROR: Every processing attempt failed; refusing to continue with stale content.")
        sys.exit(1)

    # --- Step 4: Save deduplication state ---
    if not dry_run and published_links:
        print(f"\n[4/4] Saving {len(published_links)} new URLs to deduplication store...")
        save_published_urls(existing_urls, published_links)
    elif dry_run:
        print(f"\n[4/4] Dry run — skipping deduplication state update")
    else:
        print(f"\n[4/4] No new articles published")

    # --- Step 5: Backfill images for any existing posts still missing one ---
    if unsplash_key and not dry_run:
        missing = [p for p in CONTENT_DIR.glob("*.md") if "image:" not in p.read_text()]
        if missing:
            print(f"\n[5/5] Backfilling images for {len(missing)} post(s) without images...")
            for post_path in missing:
                text = post_path.read_text()
                # Extract title for query
                title_match = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', text, re.MULTILINE)
                cat_match   = re.search(r'^categories:\s*\["?([^"\],]+)', text, re.MULTILINE)
                if not title_match:
                    continue
                title    = title_match.group(1)
                cat_slug = cat_match.group(1).strip() if cat_match else "community"
                cat_phrase = cat_slug.replace("-", " ")
                stop = {"the","a","an","of","in","on","at","to","for","and","or","but",
                        "how","why","what","when","where","who","as","by","from","with",
                        "its","this","that","these","those","is","are","was","were"}
                words = [w for w in title.replace(":", " ").replace("—", " ").split()
                         if w.lower() not in stop and len(w) > 2]
                short = " ".join(words[:4])
                cat_fallbacks = {
                    "environment":      "nature landscape green",
                    "health-science":   "medical research laboratory",
                    "community":        "community people together",
                    "tech-for-good":    "technology innovation future",
                    "education":        "education learning students",
                    "arts-culture":     "art culture creative",
                    "justice-equality": "diverse community justice",
                    "economy-work":     "work collaboration success",
                }
                cat_photo = cat_fallbacks.get(cat_slug, "positive people nature")
                img_slug  = post_path.stem

                img_meta = None
                rate_limited = False
                for q in [title, short, cat_phrase, cat_photo]:
                    if not q.strip():
                        continue
                    try:
                        img_meta = fetch_unsplash_image(q, img_slug, unsplash_key, verbose=verbose)
                    except UnsplashRateLimited:
                        rate_limited = True
                        break
                    if img_meta:
                        break

                if rate_limited:
                    print("  Unsplash rate limit reached — stopping backfill (more images remain to fill on a later run)")
                    break

                if not img_meta:
                    print(f"  ✗ No image found for: {post_path.name}")
                    continue

                # Splice image fields into the front matter
                parts = text.split("---", 2)
                if len(parts) < 3:
                    continue
                fm = parts[1]
                img_block = (
                    f'image: "images/articles/{img_slug}.jpg"\n'
                    f'image_credit: "{img_meta["photographer"]}"\n'
                    f'image_credit_url: "{img_meta["photographer_url"]}"\n'
                )
                fm = fm.rstrip() + "\n" + img_block
                post_path.write_text("---" + fm + "---" + parts[2])
                print(f"  ✓ {post_path.name} — {img_meta['photographer']}")
        else:
            print(f"\n[5/5] All posts have images — nothing to backfill.")

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
