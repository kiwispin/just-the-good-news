# Kids Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully separate kids content pipeline that fetches from four verified RSS sources, rewrites articles in child-friendly language, and publishes to a dedicated `/kids/` section with teal/green visual design — completely isolated from the main site.

**Architecture:** `scripts/pipeline-kids.py` writes markdown to `content/kids/` (existing Hugo section); `layouts/kids/list.html` and `layouts/kids/single.html` render the listing and article pages; a new GitHub Actions workflow fires at 20:00 UTC daily. No shared state with the main pipeline.

**Tech Stack:** Python 3.12, feedparser, anthropic (Claude Haiku), python-slugify, requests, Hugo, GitHub Actions

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `scripts/pipeline-kids.py` | CREATE | Standalone kids pipeline |
| `data/kids_published_urls.json` | CREATE | Separate dedup store |
| `layouts/kids/list.html` | REWRITE | Teal banner + hero + 2-col grid |
| `layouts/kids/single.html` | CREATE | Kid-friendly single article page |
| `layouts/partials/kids-card.html` | REWRITE | Category badge + consistent CTA |
| `static/css/style.css` | MODIFY | Replace old kids styles with teal/green design |
| `.github/workflows/kids-pipeline.yml` | CREATE | Daily cron at 20:00 UTC |
| `scripts/pipeline.py` | MODIFY | Remove `evaluate_kids()` and kids front matter |
| `scripts/backfill_kids.py` | DELETE | Retired |
| `.github/workflows/backfill-kids.yml` | DELETE | Retired |

---

## Task 1: Retire kids logic from the main pipeline

**Files:**
- Modify: `scripts/pipeline.py`
- Delete: `scripts/backfill_kids.py`
- Delete: `.github/workflows/backfill-kids.yml`

- [ ] **Step 1.1: Remove `evaluate_kids()` and its call from `pipeline.py`**

Delete the entire `evaluate_kids()` function (lines 314–333) and the `KIDS_THRESHOLD` and `KIDS_PROMPT` constants (lines 283–311). Also remove the kids evaluation block inside `run_pipeline()` (lines 620–629). Replace this block:

```python
# Kids suitability evaluation
try:
    kids_data = evaluate_kids(processed, client)
    processed["kids"]         = kids_data["kids"]
    processed["kids_summary"] = kids_data.get("kids_summary", "")
except Exception as e:
    processed["kids"]         = False
    processed["kids_summary"] = ""
    if verbose:
        print(f"    Kids eval error: {e}")
```

With nothing — delete it entirely.

- [ ] **Step 1.2: Remove `kids_yaml` block from `create_hugo_post()`**

In `create_hugo_post()`, delete these lines:

```python
# Kids fields
kids_yaml = ""
if processed.get("kids"):
    kids_yaml = (
        f'\nkids: true'
        f'\nkids_summary: "{_escape_yaml(processed.get("kids_summary", ""))}"'
    )
```

And remove `{kids_yaml}` from the `content` f-string so the front matter line becomes:

```python
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
```

Also remove the kids print line from the verbose output block:

```python
print(f"    Kids:       {'✓ kid-friendly' if processed.get('kids') else '✗'}")
```

- [ ] **Step 1.3: Delete retired files**

```bash
rm scripts/backfill_kids.py
rm .github/workflows/backfill-kids.yml
```

- [ ] **Step 1.4: Verify pipeline.py still runs**

```bash
python scripts/pipeline.py --dry-run --verbose 2>&1 | head -30
```

Expected: pipeline starts, fetches feeds, prints candidates. No `NameError` or `AttributeError`.

- [ ] **Step 1.5: Commit**

```bash
git add scripts/pipeline.py
git rm scripts/backfill_kids.py .github/workflows/backfill-kids.yml
git commit -m "refactor: remove kids evaluation from main pipeline

The kids pipeline now has its own dedicated script. The main
pipeline no longer tags articles with kids: true or kids_summary.
Retiring backfill_kids.py and backfill-kids.yml workflow.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 2: Create the kids pipeline script

**Files:**
- Create: `scripts/pipeline-kids.py`
- Create: `data/kids_published_urls.json`

- [ ] **Step 2.1: Create `data/kids_published_urls.json`**

```bash
echo '{"urls": []}' > data/kids_published_urls.json
```

- [ ] **Step 2.2: Create `scripts/pipeline-kids.py`**

Write the full file:

```python
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
MAX_AGE_DAYS = 7

VALID_CATEGORIES = [
    "Animals", "Space", "Dinosaurs", "Records",
    "Inventors", "Sport", "Nature", "Science", "Funny",
]

CATEGORY_PHOTO_FALLBACK = {
    "Animals":   "wildlife animals nature",
    "Space":     "space stars astronomy",
    "Dinosaurs": "prehistoric fossils museum",
    "Records":   "achievement trophy sport",
    "Inventors": "invention technology innovation",
    "Sport":     "sport athlete active",
    "Nature":    "nature landscape forest",
    "Science":   "science laboratory discovery",
    "Funny":     "playful joy happy",
}

# ---------------------------------------------------------------------------
# RSS Sources (verified working 2026-04-07)
# ---------------------------------------------------------------------------

KIDS_SOURCES = [
    {"name": "Newsround (BBC)",           "feed": "https://www.bbc.co.uk/newsround/rss.xml"},
    {"name": "Science News for Students", "feed": "https://www.snexplores.org/feed"},
    {"name": "Mongabay Kids",             "feed": "https://kids.mongabay.com/feed/"},
    {"name": "ScienceDaily",              "feed": "https://www.sciencedaily.com/rss/top.xml"},
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

        photo = results[0]
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

Use active voice. Explain any technical terms in plain English inside brackets.
End the summary with something that sparks curiosity or makes the reader smile.

Reply with JSON: {{"headline": "...", "summary": "...", "category": "..."}}

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

            # 4-query Unsplash cascade
            stop = {"the","a","an","of","in","on","at","to","for","and","or","but",
                    "how","why","what","when","where","who","as","by","from","with",
                    "its","this","that","these","those","is","are","was","were"}
            headline_words = [
                w for w in rewritten["headline"].replace(":", " ").replace("\u2014", " ").split()
                if w.lower() not in stop and len(w) > 2
            ]
            short_query = " ".join(headline_words[:4])
            cat_phrase = rewritten["category"].lower()
            cat_photo = CATEGORY_PHOTO_FALLBACK.get(rewritten["category"], "nature discovery")

            slug = slugify(rewritten["headline"], max_length=60, word_boundary=True)
            date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            img_slug = f"{date_prefix}-{slug}"

            image = None
            if unsplash_key:
                for q in [rewritten["headline"], short_query, cat_phrase, cat_photo]:
                    if not q.strip():
                        continue
                    image = fetch_unsplash_image(q, img_slug, unsplash_key, verbose=verbose)
                    if image:
                        break

            filepath = create_kids_post(article, rewritten, image=image, dry_run=dry_run)

            if verbose:
                print(f"  {'(dry) ' if dry_run else ''}→ {filepath.name}")
                print(f"    Headline:  {rewritten['headline']}")
                print(f"    Category:  {rewritten['category']}  Score: {article.get('_score', '?')}")
                print(f"    Image:     {'✓ ' + image['photographer'] if image else '✗ none'}")
            else:
                print(f"  {'(dry) ' if dry_run else ''}→ {filepath.name}")

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
```

- [ ] **Step 2.3: Verify the script is importable with no errors**

```bash
python -c "import scripts.pipeline_kids" 2>/dev/null || python scripts/pipeline-kids.py --help
```

Expected: prints usage/help without error. (Note: hyphens in filename mean you use `python scripts/pipeline-kids.py` directly, not as a module.)

```bash
python scripts/pipeline-kids.py --help
```

Expected output:
```
usage: pipeline-kids.py [-h] [--dry-run] [--verbose]
...
```

- [ ] **Step 2.4: Run a feed-only dry run (no API key needed)**

```bash
ANTHROPIC_API_KEY=test python scripts/pipeline-kids.py --dry-run --verbose 2>&1 | head -40
```

Expected: The script starts, fetches all four feeds, prints candidate counts, then hits the Anthropic API and fails with an auth error (expected — we passed a fake key). The key thing to verify is that all four feeds return entries before the API call.

- [ ] **Step 2.5: Commit**

```bash
git add scripts/pipeline-kids.py data/kids_published_urls.json
git commit -m "feat: add kids content pipeline

Standalone pipeline-kids.py fetches from Newsround, Science News for
Students, Mongabay Kids, and ScienceDaily. Scores 1-10 for kid-
suitability, rewrites with enthusiastic teacher prompt, fetches
Unsplash images, writes to content/kids/. Separate dedup store.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 3: Rewrite the kids listing layout

**Files:**
- Rewrite: `layouts/kids/list.html`

- [ ] **Step 3.1: Write the new `layouts/kids/list.html`**

Replace the entire file content:

```html
{{ define "main" }}
{{ $pages := .Pages.ByDate.Reverse }}

{{/* Category emoji map */}}
{{ $emoji := dict
  "Animals"   "🦁"
  "Space"     "🚀"
  "Dinosaurs" "🦕"
  "Records"   "🏆"
  "Inventors" "💡"
  "Sport"     "🏅"
  "Nature"    "🌿"
  "Science"   "🔬"
  "Funny"     "😄"
}}

<div class="kids-page">

  {{/* ── Banner ── */}}
  <div class="kids-banner">
    <div class="kids-banner-inner">
      <div class="kids-banner-pill">✨ Good News for Kids</div>
      <h1 class="kids-banner-title">Today's Amazing Stories</h1>
      <p class="kids-banner-sub">Fresh every morning — brilliant things happening in our world</p>
    </div>
  </div>

  <div class="kids-content">

    {{ if $pages }}

      {{/* ── Hero card ── */}}
      {{ with index $pages 0 }}
        {{ $cat := .Params.category | default "Science" }}
        {{ $em  := index $emoji $cat | default "🔬" }}
        <div class="kids-hero-card">
          <div class="kids-hero-image">
            {{ if .Params.image }}
              <img src="{{ .Params.image | relURL }}" alt="{{ .Title }}" loading="eager">
            {{ else }}
              <div class="kids-hero-image-placeholder">{{ $em }}</div>
            {{ end }}
            <div class="kids-cat-badge kids-cat-badge--{{ $cat | lower }}">
              <span>{{ $em }}</span>
              <span>{{ $cat | upper }}</span>
            </div>
            <div class="kids-top-badge">⭐ Top Story</div>
          </div>
          <div class="kids-hero-body">
            <h2 class="kids-hero-title">
              <a href="{{ .Permalink }}">{{ .Title }}</a>
            </h2>
            <p class="kids-hero-summary">{{ .Params.summary }}</p>
            <a href="{{ .Permalink }}" class="kids-hero-btn">Read this story →</a>
          </div>
        </div>
      {{ end }}

      {{/* ── Grid of remaining stories ── */}}
      {{ if gt (len $pages) 1 }}
        <div class="kids-grid">
          {{ range after 1 $pages }}
            {{ $cat := .Params.category | default "Science" }}
            {{ $em  := index $emoji $cat | default "🔬" }}
            {{ partial "kids-card.html" (dict "Page" . "Cat" $cat "Emoji" $em) }}
          {{ end }}
        </div>
      {{ end }}

    {{ else }}
      {{/* ── Empty state ── */}}
      <div class="kids-empty">
        <div class="kids-empty-icon">🌟</div>
        <p class="kids-empty-text">Amazing stories are on their way — check back tomorrow morning!</p>
      </div>
    {{ end }}

    <p class="kids-footer-note">
      New stories every morning ✨ &nbsp;—&nbsp;
      <a href="{{ "" | relURL }}">back to main site</a>
    </p>

  </div>
</div>
{{ end }}
```

- [ ] **Step 3.2: Verify Hugo can parse the template**

```bash
hugo --buildDrafts 2>&1 | grep -E "ERROR|WARN|kids" | head -20
```

Expected: No `ERROR` lines. `WARN` lines about missing images are acceptable. Build completes.

- [ ] **Step 3.3: Commit**

```bash
git add layouts/kids/list.html
git commit -m "feat: rewrite kids listing page with teal/green design

Teal gradient banner, hero card for top story, 2-col grid for
remaining articles. Uses .Pages from kids section directly — no
cross-contamination with content/posts/.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 4: Create the kids single article layout

**Files:**
- Create: `layouts/kids/single.html`

- [ ] **Step 4.1: Write `layouts/kids/single.html`**

```html
{{ define "main" }}
{{ $cat   := .Params.category | default "Science" }}
{{ $src   := .Params.source_name | default .Params.source }}
{{ $srcUrl := .Params.source_url }}

{{ $emoji := dict
  "Animals"   "🦁"
  "Space"     "🚀"
  "Dinosaurs" "🦕"
  "Records"   "🏆"
  "Inventors" "💡"
  "Sport"     "🏅"
  "Nature"    "🌿"
  "Science"   "🔬"
  "Funny"     "😄"
}}
{{ $em := index $emoji $cat | default "🔬" }}

<div class="kids-page kids-single-page">
  <div class="kids-single-wrap">

    <a href="{{ "kids/" | relURL }}" class="kids-back-link">← Back to Kids</a>

    {{/* Category badge */}}
    <div class="kids-cat-badge kids-cat-badge--{{ $cat | lower }}">
      <span>{{ $em }}</span>
      <span>{{ $cat | upper }}</span>
    </div>

    {{/* Headline */}}
    <h1 class="kids-single-title">{{ .Title }}</h1>

    {{/* Meta */}}
    <div class="kids-single-meta">
      <span>{{ .Date.Format "Monday 2 January 2006" }}</span>
      {{ with $src }}<span class="kids-single-meta-sep">•</span><span>{{ . }}</span>{{ end }}
    </div>

    {{/* Hero image */}}
    {{ if .Params.image }}
      <div class="kids-single-image">
        <img src="{{ .Params.image | relURL }}" alt="{{ .Title }}" loading="eager">
        {{ if .Params.image_credit }}
          <p class="kids-single-image-credit">
            Photo: <a href="{{ .Params.image_credit_url }}" target="_blank" rel="noopener">{{ .Params.image_credit }}</a> / Unsplash
          </p>
        {{ end }}
      </div>
    {{ end }}

    {{/* Article body */}}
    <div class="kids-single-body">
      {{ .Params.summary }}
    </div>

    {{/* Source attribution */}}
    {{ with $srcUrl }}
      <div class="kids-single-source">
        <div class="kids-single-source-label">Original source</div>
        <div class="kids-single-source-row">
          <span class="kids-single-source-name">{{ $src }}</span>
          <a href="{{ . }}" target="_blank" rel="noopener noreferrer" class="kids-single-source-link">
            Read original →
          </a>
        </div>
      </div>
    {{ end }}

    {{/* CTA back */}}
    <div class="kids-single-cta">
      <a href="{{ "kids/" | relURL }}" class="kids-hero-btn">← More great stories</a>
    </div>

  </div>
</div>
{{ end }}
```

- [ ] **Step 4.2: Check a kids article page builds without errors**

If there are no kids articles yet, create a test one first:

```bash
cat > content/kids/test-article.md << 'EOF'
---
title: "Scientists find a glowing shark that nobody has ever seen before"
date: 2026-04-07T08:00:00Z
draft: false
summary: "Deep in the ocean, scientists have discovered a brand-new species of shark that glows in the dark — and nobody has ever seen it before! The shark uses special chemicals in its skin to light up, which may help it talk to other sharks in the deep, dark sea. Who knew the ocean was hiding so many glowing secrets?"
category: "Animals"
source_url: "https://example.com/glowing-shark"
source_name: "Science News for Students"
---
EOF
```

```bash
hugo --buildDrafts 2>&1 | grep -E "ERROR|kids" | head -10
```

Expected: no ERROR lines.

```bash
ls public/kids/
```

Expected: `index.html` (listing) and `test-article/index.html` (single).

- [ ] **Step 4.3: Delete the test article**

```bash
rm content/kids/test-article.md
```

- [ ] **Step 4.4: Commit**

```bash
git add layouts/kids/single.html
git commit -m "feat: add kids single article layout

Kid-friendly template with large text, white content card on green
background, category badge, source attribution, back navigation.
Body text comes from summary front matter field.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 5: Rewrite the kids card partial

**Files:**
- Rewrite: `layouts/partials/kids-card.html`

The partial now receives a dict context (`.Page`, `.Cat`, `.Emoji`) from the list template.

- [ ] **Step 5.1: Write the new `layouts/partials/kids-card.html`**

```html
{{ $page  := .Page }}
{{ $cat   := .Cat }}
{{ $em    := .Emoji }}

<article class="kids-card">
  <a href="{{ $page.Permalink }}" class="kids-card-image-link">
    <div class="kids-card-image">
      {{ if $page.Params.image }}
        <img src="{{ $page.Params.image | relURL }}"
             alt="{{ $page.Title }}"
             loading="lazy">
      {{ else }}
        <div class="kids-card-image-placeholder">{{ $em }}</div>
      {{ end }}
      <div class="kids-cat-badge kids-cat-badge--{{ $cat | lower }} kids-cat-badge--sm">
        <span>{{ $em }}</span>
        <span>{{ $cat | upper }}</span>
      </div>
    </div>
  </a>
  <div class="kids-card-body">
    <h3 class="kids-card-title">
      <a href="{{ $page.Permalink }}">{{ $page.Title }}</a>
    </h3>
    <p class="kids-card-summary">{{ $page.Params.summary }}</p>
    <a href="{{ $page.Permalink }}" class="kids-card-link">Read this story →</a>
  </div>
</article>
```

- [ ] **Step 5.2: Build to verify no template errors**

```bash
hugo --buildDrafts 2>&1 | grep ERROR | head -10
```

Expected: no ERROR lines.

- [ ] **Step 5.3: Commit**

```bash
git add layouts/partials/kids-card.html
git commit -m "feat: rewrite kids card partial with category badges

Consistent 'Read this story →' CTA. Category badge with emoji.
Receives dict context from list template. Image or emoji placeholder.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 6: Replace kids CSS

**Files:**
- Modify: `static/css/style.css`

The existing kids CSS block starts at line 475 (`/* ============================================================`) and ends at line 626 (just before `/* ============================================================` for ARTICLE GRID). Replace that entire block.

- [ ] **Step 6.1: Replace the kids CSS section in `static/css/style.css`**

Find the block that starts with:
```css
/* ============================================================
   KIDS PAGE  (/kids/)
```

And ends just before:
```css
/* ============================================================
   ARTICLE GRID
```

Replace the entire kids block with:

```css
/* ============================================================
   KIDS SECTION  (/kids/ listing + /kids/article-slug/ single)
   Colour palette: teal #0D9488, green-50 #F0FDF4, emerald-900 #064E3B
   ============================================================ */

/* Page wrapper — green background fills the flex-1 main area */
.kids-page {
  background: #F0FDF4;
  min-height: 100%;
  flex: 1;
}

/* ── Banner ── */
.kids-banner {
  background: linear-gradient(135deg, #0D9488 0%, #059669 100%);
  padding: 2rem 1.5rem 1.75rem;
  text-align: center;
}
.kids-banner-inner { max-width: 640px; margin: 0 auto; }

.kids-banner-pill {
  display: inline-block;
  background: rgba(255,255,255,0.18);
  color: white;
  font-family: var(--font-head);
  font-size: 0.8rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 0.35rem 1rem;
  border-radius: 999px;
  margin-bottom: 0.75rem;
}

.kids-banner-title {
  font-family: var(--font-head);
  font-size: clamp(1.6rem, 4vw, 2.2rem);
  font-weight: 900;
  color: white;
  margin: 0 0 0.5rem;
  line-height: 1.2;
}

.kids-banner-sub {
  font-family: var(--font-body);
  font-size: 0.95rem;
  color: rgba(255,255,255,0.85);
  margin: 0;
}

/* ── Content area ── */
.kids-content {
  max-width: 720px;
  margin: 0 auto;
  padding: 1.5rem 1.25rem 3rem;
}

/* ── Category badges ── */
.kids-cat-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-family: var(--font-head);
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.05em;
  padding: 0.3rem 0.75rem;
  border-radius: 999px;
  white-space: nowrap;
}
.kids-cat-badge--sm { font-size: 0.65rem; padding: 0.2rem 0.6rem; }

/* Per-category colours */
.kids-cat-badge--animals   { background: #DCFCE7; color: #166534; }
.kids-cat-badge--space     { background: #DBEAFE; color: #1E40AF; }
.kids-cat-badge--dinosaurs { background: #D1FAE5; color: #065F46; }
.kids-cat-badge--records   { background: #FEF3C7; color: #92400E; }
.kids-cat-badge--inventors { background: #FEF9C3; color: #713F12; }
.kids-cat-badge--sport     { background: #FEE2E2; color: #991B1B; }
.kids-cat-badge--nature    { background: #DCFCE7; color: #166534; }
.kids-cat-badge--science   { background: #F3E8FF; color: #6B21A8; }
.kids-cat-badge--funny     { background: #FFF7ED; color: #9A3412; }

/* ── Hero card ── */
.kids-hero-card {
  background: white;
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 4px 24px rgba(13,148,136,0.13);
  margin-bottom: 1.25rem;
}

.kids-hero-image {
  position: relative;
  height: 220px;
  background: linear-gradient(135deg, #A7F3D0, #34D399);
  overflow: hidden;
}
.kids-hero-image img {
  width: 100%; height: 100%;
  object-fit: cover; display: block;
}
.kids-hero-image-placeholder {
  display: flex; align-items: center; justify-content: center;
  height: 100%; font-size: 4rem;
  background: linear-gradient(135deg, #A7F3D0, #34D399);
}
.kids-hero-image .kids-cat-badge {
  position: absolute; top: 12px; left: 12px;
  background: white !important; color: #065F46 !important;
  box-shadow: 0 2px 8px rgba(0,0,0,0.12);
}
.kids-top-badge {
  position: absolute; top: 12px; right: 12px;
  background: #FCD34D; color: #78350F;
  font-family: var(--font-head); font-size: 0.68rem; font-weight: 800;
  padding: 0.25rem 0.7rem; border-radius: 999px;
}

.kids-hero-body { padding: 1.25rem 1.5rem 1.5rem; }

.kids-hero-title {
  font-family: var(--font-head);
  font-size: 1.25rem;
  font-weight: 900;
  color: #064E3B;
  line-height: 1.25;
  margin: 0 0 0.75rem;
}
.kids-hero-title a { color: inherit; text-decoration: none; }
.kids-hero-title a:hover { color: var(--teal); }

.kids-hero-summary {
  font-family: var(--font-body);
  font-size: 0.97rem;
  line-height: 1.75;
  color: #374151;
  margin: 0 0 1rem;
}

.kids-hero-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  background: #0D9488;
  color: white;
  font-family: var(--font-head);
  font-size: 0.85rem;
  font-weight: 700;
  padding: 0.55rem 1.25rem;
  border-radius: 999px;
  text-decoration: none;
  transition: background 0.15s;
}
.kids-hero-btn:hover { background: #0F766E; color: white; }

/* ── Story grid ── */
.kids-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
  margin-bottom: 1.5rem;
}

/* ── Story card ── */
.kids-card {
  background: white;
  border-radius: 14px;
  overflow: hidden;
  box-shadow: 0 2px 14px rgba(13,148,136,0.08);
  display: flex;
  flex-direction: column;
  transition: box-shadow 0.2s, transform 0.2s;
}
.kids-card:hover { box-shadow: 0 6px 24px rgba(13,148,136,0.18); transform: translateY(-2px); }

.kids-card-image-link { display: block; text-decoration: none; }
.kids-card-image {
  position: relative;
  height: 110px;
  background: linear-gradient(135deg, #A7F3D0, #6EE7B7);
  overflow: hidden;
}
.kids-card-image img {
  width: 100%; height: 100%;
  object-fit: cover; display: block;
}
.kids-card-image-placeholder {
  display: flex; align-items: center; justify-content: center;
  height: 100%; font-size: 2.5rem; opacity: 0.7;
}
.kids-card-image .kids-cat-badge {
  position: absolute; bottom: 7px; left: 8px;
  background: white !important;
  box-shadow: 0 1px 6px rgba(0,0,0,0.10);
}

.kids-card-body {
  padding: 0.85rem 1rem 1rem;
  display: flex;
  flex-direction: column;
  flex: 1;
  gap: 0.5rem;
}

.kids-card-title {
  font-family: var(--font-head);
  font-size: 0.9rem;
  font-weight: 800;
  color: #1E293B;
  line-height: 1.35;
  margin: 0;
}
.kids-card-title a { color: inherit; text-decoration: none; }
.kids-card-title a:hover { color: #0D9488; }

.kids-card-summary {
  font-family: var(--font-body);
  font-size: 0.82rem;
  line-height: 1.6;
  color: #6B7280;
  margin: 0;
  flex: 1;
  /* Show max 3 lines */
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.kids-card-link {
  font-family: var(--font-head);
  font-size: 0.78rem;
  font-weight: 700;
  color: #0D9488;
  text-decoration: none;
  margin-top: auto;
}
.kids-card-link:hover { color: #0F766E; text-decoration: underline; }

/* ── Empty state ── */
.kids-empty {
  text-align: center;
  padding: 4rem 1.5rem;
}
.kids-empty-icon { font-size: 3rem; margin-bottom: 1rem; }
.kids-empty-text {
  font-family: var(--font-head);
  font-size: 1.1rem;
  color: var(--muted);
}

/* ── Footer note ── */
.kids-footer-note {
  text-align: center;
  font-family: var(--font-body);
  font-size: 0.82rem;
  color: #9CA3AF;
  margin-top: 1.5rem;
}
.kids-footer-note a { color: #0D9488; }

/* ── Single article page ── */
.kids-single-page { padding-top: 0; }

.kids-single-wrap {
  max-width: 640px;
  margin: 0 auto;
  padding: 1.25rem 1.25rem 3rem;
}

.kids-back-link {
  display: inline-block;
  font-family: var(--font-head);
  font-size: 0.82rem;
  font-weight: 700;
  color: #0D9488;
  text-decoration: none;
  margin-bottom: 1rem;
}
.kids-back-link:hover { text-decoration: underline; }

.kids-single-title {
  font-family: var(--font-head);
  font-size: clamp(1.4rem, 4vw, 1.9rem);
  font-weight: 900;
  color: #064E3B;
  line-height: 1.2;
  margin: 0.75rem 0 0.6rem;
}

.kids-single-meta {
  font-family: var(--font-body);
  font-size: 0.82rem;
  color: #9CA3AF;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-bottom: 1.1rem;
}
.kids-single-meta-sep { color: #D1D5DB; }

.kids-single-image {
  border-radius: 14px;
  overflow: hidden;
  margin-bottom: 1.25rem;
}
.kids-single-image img {
  width: 100%; display: block;
  aspect-ratio: 16/9; object-fit: cover;
}
.kids-single-image-credit {
  font-size: 0.72rem; color: #9CA3AF;
  margin: 0.4rem 0 0;
  font-family: var(--font-body);
}
.kids-single-image-credit a { color: #9CA3AF; }

.kids-single-body {
  background: white;
  border-radius: 14px;
  padding: 1.5rem;
  box-shadow: 0 2px 16px rgba(13,148,136,0.08);
  font-family: var(--font-body);
  font-size: 1rem;
  line-height: 1.85;
  color: #1E293B;
  margin-bottom: 1.1rem;
}

.kids-single-source {
  background: white;
  border-radius: 12px;
  padding: 0.9rem 1.1rem;
  box-shadow: 0 2px 10px rgba(0,0,0,0.05);
  margin-bottom: 1.5rem;
}
.kids-single-source-label {
  font-family: var(--font-head);
  font-size: 0.7rem;
  font-weight: 700;
  color: #9CA3AF;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 0.25rem;
}
.kids-single-source-row {
  display: flex; align-items: center;
  justify-content: space-between; gap: 0.75rem;
}
.kids-single-source-name {
  font-family: var(--font-head);
  font-size: 0.88rem;
  font-weight: 700;
  color: #1E293B;
}
.kids-single-source-link {
  font-family: var(--font-head);
  font-size: 0.8rem;
  font-weight: 700;
  color: #0D9488;
  text-decoration: none;
  white-space: nowrap;
}
.kids-single-source-link:hover { text-decoration: underline; }

.kids-single-cta { text-align: center; }

/* ── Kids nav link — teal to match section ── */
.nav-kids { color: #0D9488 !important; }
.nav-kids:hover {
  color: #0F766E !important;
  border-bottom-color: #0D9488 !important;
}

/* ── Responsive ── */
@media (max-width: 600px) {
  .kids-grid { grid-template-columns: 1fr; }
  .kids-hero-image { height: 160px; }
  .kids-banner { padding: 1.5rem 1rem 1.25rem; }
  .kids-content { padding: 1rem 1rem 2rem; }
  .kids-single-wrap { padding: 1rem 1rem 2rem; }
  .kids-single-body { font-size: 0.95rem; }
}
```

- [ ] **Step 6.2: Verify build succeeds**

```bash
hugo --buildDrafts 2>&1 | grep ERROR | head -10
```

Expected: no errors.

- [ ] **Step 6.3: Commit**

```bash
git add static/css/style.css
git commit -m "feat: replace kids CSS with teal/green design system

New styles: teal gradient banner, hero card, 2-col grid, category
badges with per-category colours, single article page. Nav kids
link updated from amber to teal.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 7: Create the GitHub Actions workflow

**Files:**
- Create: `.github/workflows/kids-pipeline.yml`

- [ ] **Step 7.1: Create `.github/workflows/kids-pipeline.yml`**

```yaml
name: Kids Content Pipeline

on:
  schedule:
    - cron: '0 20 * * *'  # 8am NZST daily (UTC+12)
  workflow_dispatch:        # Manual trigger in GitHub UI

jobs:
  update:
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'
          cache-dependency-path: scripts/requirements.txt

      - name: Install Python dependencies
        run: pip install -r scripts/requirements.txt

      - name: Run kids content pipeline
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          UNSPLASH_ACCESS_KEY: ${{ secrets.UNSPLASH_ACCESS_KEY }}
        run: python scripts/pipeline-kids.py --verbose

      - name: Commit new kids articles
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add content/kids/ data/kids_published_urls.json static/images/kids/ || true
          git diff --cached --quiet && echo "No new articles today" || \
            git commit -m "chore: add kids articles $(date -u +'%Y-%m-%d') [skip ci]"

      - name: Pull before push (avoid conflicts with main pipeline)
        run: git pull --rebase origin main || true

      - name: Push new articles
        run: git push origin main || true

      - name: Setup Hugo
        uses: peaceiris/actions-hugo@v3
        with:
          hugo-version: 'latest'
          extended: true

      - name: Build Hugo site
        run: hugo --minify

      - name: Index site with Pagefind
        run: npx -y pagefind@latest --site public

      - name: Deploy to GitHub Pages
        uses: peaceiris/actions-gh-pages@v4
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_dir: ./public
          cname: justthegood.news
```

- [ ] **Step 7.2: Validate the YAML is well-formed**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/kids-pipeline.yml'))" && echo "YAML valid"
```

Expected: `YAML valid`

- [ ] **Step 7.3: Commit**

```bash
git add .github/workflows/kids-pipeline.yml
git commit -m "feat: add kids pipeline GitHub Actions workflow

Runs at 20:00 UTC daily (8am NZST). Independent of main pipeline —
failure here cannot break the main site deploy. Commits new kids
articles, rebuilds and deploys site.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 8: Push and trigger a first run

- [ ] **Step 8.1: Push all commits to GitHub**

```bash
git push origin main
```

Expected: push succeeds, no conflicts.

- [ ] **Step 8.2: Trigger the kids pipeline via workflow_dispatch**

```bash
gh workflow run kids-pipeline.yml
```

Expected: `Created workflow dispatch event` (or equivalent success message).

- [ ] **Step 8.3: Monitor the workflow**

```bash
gh run list --workflow=kids-pipeline.yml --limit=5
```

Wait ~3–4 minutes, then:

```bash
gh run view --log | tail -60
```

Expected: Pipeline fetches feeds, scores articles, rewrites and publishes 5–8 stories, deploys site.

- [ ] **Step 8.4: Verify the live kids page**

```bash
curl -sI https://justthegood.news/kids/ | head -5
```

Expected: `HTTP/2 200`

```bash
curl -s https://justthegood.news/kids/ | grep -o 'kids-hero-title[^<]*<[^>]*>[^<]*' | head -3
```

Expected: One or more story headlines appear.

- [ ] **Step 8.5: Verify separation — main site shows no kids articles**

```bash
curl -s https://justthegood.news/ | grep -i "kids/" | head -5
```

Expected: Only the nav link `href="/kids/"` — no kids article URLs in the main feed.

---

## Spec Coverage Check

| Spec requirement | Task |
|---|---|
| Separate `pipeline-kids.py` script | Task 2 |
| 4 verified RSS sources | Task 2 (KIDS_SOURCES constant) |
| Score ≥ 7 threshold | Task 2 (MIN_KIDS_SCORE) |
| Max 8 articles per run | Task 2 (MAX_ARTICLES_PER_RUN) |
| Kid-friendly rewrite prompt | Task 2 (KIDS_REWRITE_PROMPT) |
| 9 category system | Task 2 (VALID_CATEGORIES) |
| Unsplash 4-query cascade | Task 2 (fetch_unsplash_image + cascade loop) |
| content/kids/ content directory | Task 2 (CONTENT_DIR) |
| Separate dedup store | Task 2 (kids_published_urls.json) |
| kids/list.html teal banner + hero + grid | Task 3 |
| kids/single.html dedicated template | Task 4 |
| kids-card.html category badges + CTA | Task 5 |
| Teal/green CSS design system | Task 6 |
| Category badge colours (9 categories) | Task 6 |
| Large text on single page | Task 6 (.kids-single-body 1rem/1.85) |
| Consistent "Read this story →" CTA | Tasks 3, 5 |
| Empty state on listing page | Task 3 |
| Source attribution on single page | Task 4 |
| GitHub Actions workflow 20:00 UTC | Task 7 |
| workflow_dispatch manual trigger | Task 7 |
| `cname: justthegood.news` in deploy | Task 7 |
| Remove evaluate_kids() from main pipeline | Task 1 |
| Delete backfill_kids.py | Task 1 |
| Delete backfill-kids.yml | Task 1 |
| pipeline.py never writes to content/kids/ | Task 1 (CONTENT_DIR unchanged) |
| Separation verified on live site | Task 8 |
