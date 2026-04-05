# Unsplash Image Integration — Design Spec
**Date:** 2026-04-05
**Status:** Approved

---

## Overview

Replace per-category SVG placeholder images with unique, royalty-free photographs sourced from the Unsplash API. Images are downloaded and committed to the repository at article-creation time — no hotlinking, no per-build re-downloading.

---

## Architecture

### Image Lifecycle

1. **Pipeline runs** → article is scored and processed by Claude AI
2. **Unsplash search** → query the Unsplash API using the article's generated headline as the search term
3. **Download** → fetch the photo at 1200px width and write to `static/images/articles/{date}-{slug}.jpg`
4. **Attribution stored** → photo ID, photographer name, and photographer URL saved in article front matter
5. **Unsplash download trigger** → call `photo.links.download_location` as required by Unsplash API guidelines
6. **Commit** → image file and markdown are committed to the repo together by the GitHub Actions workflow
7. **Hugo serves** → the image is a static file served from GitHub Pages — no external dependency at serve time

### Fallback Chain

If the Unsplash API call fails (rate limit, network error, no results, key missing):
- Log a warning
- Article is still published without an image field
- Templates fall back to the category SVG placeholder — nothing breaks

---

## Changes Required

### `scripts/pipeline.py`

Add `fetch_unsplash_image(headline, slug, access_key)` function:
- `GET https://api.unsplash.com/search/photos?query={headline}&per_page=3&orientation=landscape`
- Pick the first result with a landscape aspect ratio
- Download via `photo.urls.regular` with `?w=1200&q=85`
- Trigger `photo.links.download_location` (required by Unsplash TOS)
- Save to `static/images/articles/{date}-{slug}.jpg`
- Return `{path, photographer, photographer_url, unsplash_id}` or `None` on failure

Update `create_hugo_post()` to add to front matter when image is present:
```yaml
image: "images/articles/2026-04-05-slug.jpg"
image_credit: "Photographer Name"
image_credit_url: "https://unsplash.com/@handle"
```

`UNSPLASH_ACCESS_KEY` read from environment variable (already set as GitHub Secret).

### `scripts/requirements.txt`

Add `requests>=2.31.0`

### `scripts/backfill_images.py` (new)

One-off script to add Unsplash images to the 24 existing articles that have no `image` field:
- Reads all markdown files in `content/posts/`
- Skips any that already have an `image` field
- Fetches and downloads image using the article title as query
- Rewrites the front matter with the image fields added
- Prints progress

Run once locally: `UNSPLASH_ACCESS_KEY=... python scripts/backfill_images.py`

### `layouts/partials/article-card.html`

```html
{{ if .Params.image }}
  <img src="{{ .Params.image | relURL }}" ...>
{{ else }}
  <img src="{{ (printf "images/placeholders/%s.svg" $cat) | relURL }}" ...>
{{ end }}
```

### `layouts/index.html` (hero)

Same conditional — use `.Params.image` if present, SVG otherwise.

### `layouts/_default/single.html`

Same conditional for the hero image. Add photographer credit below the image when present:
```html
{{ with .Params.image_credit }}
  <p class="photo-credit">
    Photo: <a href="{{ $.Params.image_credit_url }}" target="_blank" rel="noopener">{{ . }}</a> / Unsplash
  </p>
{{ end }}
```

Add `.photo-credit` style to `style.css`: small, muted, right-aligned below image.

### `.github/workflows/update-content.yml`

Pass the Unsplash key to the pipeline step:
```yaml
- name: Run content pipeline
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    UNSPLASH_ACCESS_KEY: ${{ secrets.UNSPLASH_ACCESS_KEY }}
```

### `.gitignore`

Confirm `static/images/articles/` is NOT ignored (it isn't currently — just needs to remain that way).

---

## Unsplash API Compliance

Per Unsplash API guidelines:
- Trigger the download endpoint on every photo used (handled in pipeline)
- Display photographer attribution (handled in single article template)
- Do not cache/store the API response itself beyond the pipeline run

---

## Out of Scope

- Choosing between multiple search results based on quality/relevance (always use first landscape result)
- Storing image dimensions or dominant colours
- Alt text generation (headline used as alt text)
- Removing old placeholder SVGs (kept as fallback)
