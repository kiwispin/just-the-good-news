#!/usr/bin/env python3
"""
Backfill kids evaluation for existing articles.

Reads every post in content/posts/ that doesn't already have a `kids:` field,
evaluates it with Claude Haiku for kid-friendliness, and writes kids: true +
kids_summary into the front matter if it qualifies.

Run:  python scripts/backfill_kids.py [--dry-run] [--verbose]
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import anthropic

REPO_ROOT   = Path(__file__).parent.parent
CONTENT_DIR = REPO_ROOT / "content" / "posts"
KIDS_THRESHOLD = 7

KIDS_PROMPT = """\
A positive news article has already been approved for publication. Evaluate
whether it is appropriate and genuinely interesting for children aged 8–14.

Headline: {headline}
Summary: {summary}

Score 1–10 for kid-friendliness. Exclude articles involving:
- Crime or violence (even if the outcome is positive)
- Illness, injury, or death (even if someone recovered)
- Financial hardship, bankruptcy, or poverty
- Complex political or geopolitical topics
- Natural disasters (even if aid was provided)
- Adult relationship issues

High-scoring articles (7+) feature things like:
- Animals, nature, space, sports, inventions, art, acts of kindness, records broken
- Stories a child could retell excitedly to a friend
- Concepts an 8-year-old can understand without adult context

If the score is 7 or higher, also write a 2-sentence summary for ages 8–14:
- Simple, vivid, enthusiastic language — short sentences
- Focus on the "wow" or heartwarming element
- No jargon, no political framing, no nuance required

Respond ONLY with valid JSON (no markdown):
{{"kids_score": <integer 1-10>, "kids_summary": "<2-sentence summary, or empty string if score < 7>"}}"""


def _escape_yaml(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def evaluate(headline: str, summary: str, client) -> dict:
    prompt = KIDS_PROMPT.format(headline=headline, summary=summary)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    result = json.loads(raw)
    score = int(result.get("kids_score", 0))
    kids_summary = result.get("kids_summary", "").strip() if score >= KIDS_THRESHOLD else ""
    return {"kids": score >= KIDS_THRESHOLD, "kids_score": score, "kids_summary": kids_summary}


def patch_frontmatter(path: Path, kids_summary: str, dry_run: bool) -> bool:
    """Splice kids: true + kids_summary into the file's YAML front matter."""
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        return False
    fm = parts[1]
    addition = f'kids: true\nkids_summary: "{_escape_yaml(kids_summary)}"\n'
    new_fm = fm.rstrip("\n") + "\n" + addition
    new_text = "---" + new_fm + "---" + parts[2]
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return True


def main():
    parser = argparse.ArgumentParser(description="Backfill kids evaluation for existing articles")
    parser.add_argument("--dry-run",  action="store_true", help="Print results without writing files")
    parser.add_argument("--verbose",  action="store_true", help="Show score for every article")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        sys.exit("Error: ANTHROPIC_API_KEY not set.")

    client = anthropic.Anthropic(api_key=api_key)

    posts = sorted(CONTENT_DIR.glob("*.md"))
    to_process = [p for p in posts if "kids:" not in p.read_text(encoding="utf-8")]

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Kids backfill — {len(to_process)} articles to evaluate")
    print()

    tagged = 0
    skipped = 0
    errors = 0

    for i, path in enumerate(to_process, 1):
        text = path.read_text(encoding="utf-8")

        # Extract title and summary from front matter
        title_m   = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$',   text, re.MULTILINE)
        summary_m = re.search(r'^summary:\s*["\']?(.+?)["\']?\s*$', text, re.MULTILINE)

        if not title_m or not summary_m:
            print(f"  [{i:2}/{len(to_process)}] SKIP  {path.name}  (no title/summary found)")
            skipped += 1
            continue

        headline = title_m.group(1).strip()
        summary  = summary_m.group(1).strip()

        try:
            result = evaluate(headline, summary, client)
            score  = result["kids_score"]

            if result["kids"]:
                patch_frontmatter(path, result["kids_summary"], args.dry_run)
                tagged += 1
                marker = "⭐" if not args.dry_run else "⭐ (dry)"
                print(f"  [{i:2}/{len(to_process)}] {marker}  {score}/10  {path.name}")
                if args.verbose:
                    print(f"            {result['kids_summary'][:100]}…")
            else:
                skipped += 1
                if args.verbose:
                    print(f"  [{i:2}/{len(to_process)}] ✗     {score}/10  {path.name}")

        except Exception as e:
            errors += 1
            print(f"  [{i:2}/{len(to_process)}] ERROR {path.name}: {e}")

    print()
    print(f"Done. Tagged: {tagged}  |  Skipped/not qualifying: {skipped}  |  Errors: {errors}")


if __name__ == "__main__":
    main()
