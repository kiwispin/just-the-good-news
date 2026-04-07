# Kids Pipeline — Design Spec
**Date:** 2026-04-07
**Status:** Approved for implementation

---

## Overview

A fully separate kids content pipeline for Just The Good News. Kids articles are produced by a dedicated pipeline, stored in their own Hugo section, and rendered with a child-friendly layout at `/kids/`. They never appear on the main site. Main site articles never appear on the kids page.

---

## Decisions Made

| Question | Decision |
|---|---|
| Separate pipeline or flag on existing? | Fully separate script (`pipeline-kids.py`) |
| Content location | Existing Hugo section: `content/kids/` (alongside `_index.md`) |
| URL structure | `/kids/article-slug/` with dedicated single template |
| Schedule | Once daily, 8am NZST (20:00 UTC), own workflow |
| Articles per run | Target 5–8; publish whatever passes threshold (min 1) |
| Category filtering on /kids/ page | None — clean chronological grid |
| Visual direction | "Fresh & Friendly" — teal/green, warm cards, pastel badges |
| Image source | Unsplash, same 4-query cascade as main pipeline |

---

## RSS Sources

All four feeds verified working with feedparser before being included:

| Source | URL | Entries | Notes |
|---|---|---|---|
| Newsround (BBC) | `https://www.bbc.co.uk/newsround/rss.xml` | 76 | Explicitly for kids; some video entries (scored low, filtered out) |
| Science News for Students | `https://www.snexplores.org/feed` | 20 | Student-targeted science journalism |
| Mongabay Kids | `https://kids.mongabay.com/feed/` | 10 | Kids environmental/nature stories; small but high hit rate |
| ScienceDaily | `https://www.sciencedaily.com/rss/top.xml` | 60 | Adult science but consistently produces discovery stories kids love; scoring filters adult topics |

**Rejected sources (broken or paywalled):** DOGOnews (404), The Week Junior (DNS failure), Kiwi Kids News (DNS failure), Nat Geo Kids (404), Time for Kids (paywall risk on body text).

---

## New Files

```
scripts/pipeline-kids.py               Standalone kids pipeline (no imports from pipeline.py)
data/kids_published_urls.json          Separate dedup store — never shared with main pipeline
layouts/kids/single.html               Kid-friendly single article layout (new — was missing)
.github/workflows/kids-pipeline.yml    Cron workflow: 20:00 UTC daily + workflow_dispatch
docs/superpowers/specs/                (this file)
```

Note: `content/kids/` section and `content/kids/_index.md` already exist. Articles written by
the kids pipeline go directly into `content/kids/` alongside `_index.md`, giving them the URL
`/kids/article-slug/` automatically via Hugo's section routing. No separate `kids-articles`
section is needed.

---

## Changed Files

| File | Change |
|---|---|
| `layouts/kids/list.html` | Rewritten to query `content/kids-articles/` instead of filtering `content/posts/` by `kids: true` |
| `layouts/partials/kids-card.html` | Updated for teal/green design language |
| `static/css/style.css` | New kids section styles added (listing page, single article, card, header banner) |
| `scripts/pipeline.py` | Remove `evaluate_kids()` function; remove `kids:` and `kids_summary:` from `create_hugo_post()` front matter |

---

## Retired

- `evaluate_kids()` function in `pipeline.py` — delete; main pipeline no longer produces kids content
- `kids: true` and `kids_summary:` from `create_hugo_post()` in `pipeline.py` — remove from output; existing posts keep the fields but layouts ignore them
- `scripts/backfill_kids.py` — purpose served, delete
- `.github/workflows/backfill-kids.yml` — purpose served, delete

---

## Pipeline Logic (`pipeline-kids.py`)

### Step-by-step

1. **Load dedup store** — read `data/kids_published_urls.json`; create empty file if missing
2. **Fetch feeds** — parse all four RSS sources with feedparser; collect raw entries
3. **Deduplicate** — skip any entry whose URL already exists in the dedup store
4. **Score for kid-suitability** — send each article's title + description to Claude Haiku with the scoring prompt (see below); keep entries scoring ≥ 7
5. **Rank and cap** — sort by score descending; take top 8 maximum
6. **Rewrite** — for each kept article, call Claude Haiku with the kid-friendly rewrite prompt to produce a headline (≤ 80 chars) and a 2–3 sentence summary
7. **Assign category** — Claude assigns one category from the fixed list during the rewrite step
8. **Fetch image** — Unsplash 4-query cascade: full headline → key nouns (stop-word filtered) → category phrase → category fallback mapping
9. **Write content file** — create `content/kids/SLUG.md` with front matter
10. **Update dedup store** — append new URLs to `data/kids_published_urls.json`

### Kid-suitability scoring prompt

```
Rate this article 1–10 for how much an 8–14 year old would enjoy reading it.

Score HIGH (8–10) for: animals, wildlife, space, dinosaurs/prehistoric life, world records,
young achievers, cool inventions, sport victories, nature discoveries, funny or weird stories,
science breakthroughs explained simply.

Score LOW (1–3) for: crime, violence, illness, death, war, politics, finance, economics,
workplace news, natural disasters, anything distressing.

Score MEDIUM (4–6) for: general human interest, community stories, technology (non-invention).

Reply with only a JSON object: {"score": N, "reason": "one sentence"}
```

### Kid-friendly rewrite prompt

```
You are an enthusiastic primary school teacher telling a curious 10-year-old about something
amazing that just happened in the world.

Write:
1. A headline — maximum 80 characters, exciting and clear, no jargon
2. A summary — exactly 2–3 sentences, simple language, sense of wonder, age 8–14 reading level
3. A category — pick exactly one: Animals, Space, Dinosaurs, Records, Inventors, Sport, Nature, Science, Funny

Use active voice. Explain any technical terms in plain English inside brackets.
End the summary with something that sparks curiosity or makes the reader smile.

Reply with JSON: {"headline": "...", "summary": "...", "category": "..."}

Article title: {title}
Article text: {text}
```

### Content front matter

```yaml
---
title: "..."           # rewritten headline from Claude
date: 2026-04-07T08:00:00+12:00
image: "https://images.unsplash.com/..."
summary: "..."         # 2–3 sentence kid-friendly summary
source_url: "https://..."
source_name: "Science News for Students"
category: "Animals"    # one of the 9 fixed categories
---
```

No `kids: true` flag needed — everything in `content/kids/` is kids content by definition.

---

## Hugo Layout — Listing Page (`layouts/kids/list.html`)

**Query:** `.Pages` (pages within the `kids` section) sorted by date descending. Because `layouts/kids/list.html` is the section template, Hugo automatically scopes `.Pages` to `content/kids/` — no explicit `where` filter needed.

**Structure:**
- Site nav (shared `header.html` partial) with Kids link highlighted
- Teal gradient header banner: "✨ GOOD NEWS FOR KIDS" pill + "Today's Amazing Stories" heading + subtext
- **Hero card** (first/newest article): full-width, large image, category badge, headline, 2–3 sentence summary, "Read this story →" teal button
- **Grid** (remaining articles): 2-column, smaller image, category badge, headline, short summary, "Read this story →" teal link
- Footer note: "New stories every morning ✨ — back to main site"
- **Empty state:** shown if no articles in section yet — "Check back tomorrow morning!"

---

## Hugo Layout — Single Article Page (`layouts/kids/single.html`)

**Structure:**
- Site nav (shared `header.html` partial)
- "← Back to Kids" link
- Category emoji badge (e.g., `🦕 DINOSAURS`)
- H1 headline — large, bold, teal-dark colour
- Meta line: date + source name
- Hero image (full-width, rounded corners)
- Body copy in a white card on the green background — 16px base, 1.8 line-height
- Source attribution box: "Original source: [name] — Read original →"
- "← More great stories" button back to `/kids/`

**Body copy rendering:** The `summary` front matter field contains the full 2–3 sentence kid-friendly rewrite. This is the article body — there is no separate long-form body content. The template renders `.Params.summary` as the article text.

---

## Visual Design

### Colour tokens (kids section only)

| Token | Value | Use |
|---|---|---|
| `kids-primary` | `#0D9488` (teal-600) | Buttons, links, active nav badge |
| `kids-primary-dark` | `#064E3B` (emerald-900) | H1 headlines |
| `kids-bg` | `#F0FDF4` (green-50) | Page background |
| `kids-card-shadow` | `rgba(13,148,136,0.10)` | Card shadows |
| `kids-banner-start` | `#0D9488` | Header gradient start |
| `kids-banner-end` | `#059669` | Header gradient end |

### Category colour mapping (badge background / text)

| Category | Emoji | Badge bg | Badge text |
|---|---|---|---|
| Animals | 🦁 | `#DCFCE7` | `#166534` |
| Space | 🚀 | `#DBEAFE` | `#1E40AF` |
| Dinosaurs | 🦕 | `#DCFCE7` | `#065F46` |
| Records | 🏆 | `#FEF3C7` | `#92400E` |
| Inventors | 💡 | `#FEF9C3` | `#713F12` |
| Sport | 🏅 | `#FEE2E2` | `#991B1B` |
| Nature | 🌿 | `#DCFCE7` | `#166534` |
| Science | 🔬 | `#F3E8FF` | `#6B21A8` |
| Funny | 😄 | `#FFF7ED` | `#9A3412` |

### Typography (kids section)

- Body text: 15–16px, line-height 1.8
- Headlines (listing): 12px bold on grid cards, 18px bold on hero
- Headlines (single): 24px, weight 900
- All text: system font stack

---

## GitHub Actions Workflow (`.github/workflows/kids-pipeline.yml`)

```yaml
name: Kids Content Pipeline
on:
  schedule:
    - cron: '0 20 * * *'   # 8am NZST daily
  workflow_dispatch:         # manual trigger for testing
jobs:
  run-kids-pipeline:
    runs-on: ubuntu-latest
    steps:
      - checkout
      - setup python
      - pip install feedparser anthropic python-slugify requests Pillow
      - run: python scripts/pipeline-kids.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          UNSPLASH_ACCESS_KEY: ${{ secrets.UNSPLASH_ACCESS_KEY }}
      - commit new kids articles (if any)
      - hugo build + pagefind index
      - deploy to GitHub Pages
```

Runs independently of the main pipeline. If the kids pipeline fails, the main site is unaffected.

---

## Separation Guarantee

- `pipeline.py` never writes to `content/kids/`
- `pipeline-kids.py` never writes to `content/posts/`
- `data/published_urls.json` and `data/kids_published_urls.json` are separate files
- `layouts/kids/list.html` uses `.Pages` scoped to `Section = "kids"` automatically
- `layouts/index.html` and `layouts/_default/list.html` query only `Section = "posts"`
- No shared front matter flags (`kids: true` is retired)

---

## Out of Scope

- Category filtering UI on the kids page (not needed for 5–8 stories)
- Kids-specific search (Pagefind indexes everything; acceptable)
- Comments or interactivity
- Parent/teacher mode or reading level toggle
- Newsletter or email for kids content
