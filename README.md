# Just The Good News

A nonprofit, AI-powered positive news aggregation website. Publishes 5-10 genuine good news stories daily, automatically.

**Live site:** https://justthegoodnews.github.io/ (or your custom domain)

---

## How it works

1. GitHub Actions triggers the Python content pipeline twice daily (8am and 8pm NZST)
2. The pipeline fetches RSS feeds from positive news sources
3. Claude AI scores each article for genuine positivity (7+/10 threshold)
4. Qualifying articles are summarised, categorised, and saved as Hugo markdown files
5. Hugo builds the static site, which is deployed to GitHub Pages
6. New articles are live within minutes — no human intervention needed

---

## Setup (one-time)

### 1. Fork or clone this repository

```bash
git clone https://github.com/YOUR_USERNAME/just-the-good-news.git
cd just-the-good-news
```

### 2. Install Hugo

**macOS (Homebrew):**
```bash
brew install hugo
```

**macOS (manual):** Download the extended binary from [Hugo releases](https://github.com/gohugoio/hugo/releases) and place it on your PATH.

**Linux:**
```bash
sudo snap install hugo
# or
sudo apt install hugo
```

### 3. Install Python dependencies

```bash
pip install -r scripts/requirements.txt
```

### 4. Set up your Anthropic API key

Get a key at [console.anthropic.com](https://console.anthropic.com).

**For local runs:**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

**For GitHub Actions:**
1. Go to your repo → Settings → Secrets and variables → Actions
2. Add a secret named `ANTHROPIC_API_KEY` with your key

### 5. Enable GitHub Pages

1. Go to your repo → Settings → Pages
2. Set Source to **Deploy from a branch**
3. Set Branch to `gh-pages` / `/ (root)`
4. Save

The first deploy happens automatically when the GitHub Action runs.

### 6. (Optional) Custom domain

1. Add your domain to the `cname:` field in `.github/workflows/update-content.yml`
2. Configure your domain's DNS with a CNAME pointing to `YOUR_USERNAME.github.io`
3. GitHub will automatically provision HTTPS

---

## Local development

```bash
# Run the dev server
hugo server

# Visit http://localhost:1313
```

### Run the content pipeline locally

```bash
# Dry run — fetches and scores but does not write files
python scripts/pipeline.py --dry-run --verbose

# Full run — writes new articles and updates dedup store
python scripts/pipeline.py --verbose
```

---

## Project structure

```
just-the-good-news/
├── .github/
│   └── workflows/
│       └── update-content.yml   # Automated twice-daily pipeline
├── archetypes/
│   └── default.md               # Template for new posts
├── content/
│   ├── posts/                   # All aggregated articles (auto-generated)
│   └── about.md                 # About page
├── data/
│   └── published_urls.json      # Deduplication tracker
├── layouts/
│   ├── _default/
│   │   ├── baseof.html          # Base HTML template
│   │   ├── list.html            # Article list / category pages
│   │   └── single.html          # Individual article page
│   ├── partials/
│   │   ├── header.html
│   │   ├── footer.html
│   │   ├── article-card.html
│   │   └── category-filter.html
│   └── index.html               # Homepage
├── static/
│   ├── css/style.css            # All styles
│   ├── js/filter.js             # Client-side category filtering
│   └── images/placeholders/     # Category placeholder SVGs
├── scripts/
│   ├── pipeline.py              # Main content pipeline
│   └── requirements.txt
└── hugo.toml                    # Hugo configuration
```

---

## Content pipeline details

### RSS Sources

The pipeline pulls from these sources (edit `SOURCES` in `scripts/pipeline.py` to add/remove):

| Source | Status |
|--------|--------|
| Positive News | ✅ Active |
| Good News Network | ✅ Active |
| Reasons to be Cheerful | ✅ Active |
| The Good News Hub | ⚠️ Feed XML issues (check periodically) |
| Good Good Good | ✅ Active |
| Future Crunch | ⚠️ Feed XML issues (check periodically) |
| HuffPost Good News | ⚠️ Feed XML issues (check periodically) |
| ScienceDaily | ✅ Active |
| Treehugger | ⚠️ Feed XML issues (check periodically) |

### AI model used

- **Scoring:** `claude-haiku-4-5` (fast, cheap — ~$0.001 per article scored)
- **Summarisation:** `claude-haiku-4-5` (fast, cheap — ~$0.005 per article processed)
- **Estimated cost:** ~$0.50–1.00/month at current usage

### Positivity scoring

Articles scoring **7 or higher** out of 10 are published. The scoring prompt instructs Claude to:
- Exclude negative news with positive spin
- Exclude political/partisan content
- Exclude promotional or sponsored content
- Score 0 for anything not genuinely newsworthy

---

## Customisation

### Adding RSS sources

Edit the `SOURCES` list in `scripts/pipeline.py`:

```python
{"name": "Your Source Name", "feed": "https://yoursource.com/feed/"},
```

### Changing the scoring threshold

Edit `MIN_SCORE` in `scripts/pipeline.py` (default: 7). Higher = stricter.

### Changing article volume

Edit `MAX_ARTICLES_PER_RUN` (default: 10) and `MAX_CANDIDATES_PER_RUN` (default: 30).

### Changing the pipeline schedule

Edit the `cron:` values in `.github/workflows/update-content.yml`. Times are UTC.

---

## Legal & ethics

- We never reproduce full articles — only AI-written summaries
- Every article links to its original source
- Headlines are always rewritten (not copied verbatim)
- No image scraping — we use category placeholder SVGs only (Phase 1)
- Content is AI-curated: the About page clearly states this
- No tracking cookies; analytics (if added) must be privacy-friendly

---

## Roadmap

**Phase 2 (next):** Client-side search (Pagefind), RSS output for subscribers, social sharing, SEO meta tags, "Feel Good Counter"

**Phase 3:** Email newsletter (Buttondown), custom domain, social auto-posting, story submissions

**Phase 4:** Multi-language, ethical funding, community moderation

---

## Contributing

This is a nonprofit project. If you spot a broken RSS feed, a bug, or have a great positive news source to add, open an issue or pull request.

---

*Built with Hugo + GitHub Pages + Claude AI. Spreading good news since 2026.*
