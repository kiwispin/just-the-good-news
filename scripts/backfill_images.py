#!/usr/bin/env python3
"""
One-off backfill script: adds Unsplash images to existing articles that have none.

Usage:
    UNSPLASH_ACCESS_KEY=your_key python scripts/backfill_images.py
    UNSPLASH_ACCESS_KEY=your_key python scripts/backfill_images.py --dry-run
    UNSPLASH_ACCESS_KEY=your_key python scripts/backfill_images.py --verbose
"""
import argparse
import os
import re
import sys
from pathlib import Path

# Reuse helpers from the pipeline
sys.path.insert(0, str(Path(__file__).parent))
from pipeline import fetch_unsplash_image, _escape_yaml

REPO_ROOT = Path(__file__).parent.parent
CONTENT_DIR = REPO_ROOT / "content" / "posts"


def add_image_to_post(filepath: Path, image: dict, dry_run: bool = False) -> bool:
    """Insert image fields into a post's front matter. Returns True if changed."""
    text = filepath.read_text(encoding="utf-8")

    # Find the closing --- of the front matter
    parts = text.split("---", 2)
    if len(parts) < 3:
        return False  # Can't parse front matter

    front_matter = parts[1]
    body = parts[2]

    # Skip if already has an image field
    if re.search(r"^image:", front_matter, re.MULTILINE):
        return False

    image_yaml = (
        f'\nimage: "{image["path"]}"'
        f'\nimage_credit: "{_escape_yaml(image["photographer"])}"'
        f'\nimage_credit_url: "{image["photographer_url"]}"'
    )

    # Append image fields to front matter (before closing ---)
    new_front_matter = front_matter.rstrip() + image_yaml + "\n"
    new_text = f"---{new_front_matter}---{body}"

    if not dry_run:
        filepath.write_text(new_text, encoding="utf-8")
    return True


def run_backfill(dry_run: bool = False, verbose: bool = False) -> None:
    access_key = os.environ.get("UNSPLASH_ACCESS_KEY", "")
    if not access_key:
        print("ERROR: UNSPLASH_ACCESS_KEY environment variable not set")
        sys.exit(1)

    posts = sorted(CONTENT_DIR.glob("*.md"))
    print(f"{'[DRY RUN] ' if dry_run else ''}Backfilling images for {len(posts)} posts...")

    updated = 0
    skipped = 0
    failed = 0

    for post in posts:
        text = post.read_text(encoding="utf-8")

        # Skip if already has an image
        if re.search(r"^image:", text, re.MULTILINE):
            skipped += 1
            if verbose:
                print(f"  SKIP (already has image): {post.name}")
            continue

        # Extract title from front matter for the search query
        title_match = re.search(r'^title:\s*"(.+)"', text, re.MULTILINE)
        if not title_match:
            skipped += 1
            if verbose:
                print(f"  SKIP (no title found): {post.name}")
            continue

        title = title_match.group(1).replace('\\"', '"')

        # Use the post filename stem as the image slug (without .md)
        slug = post.stem

        if verbose:
            print(f"  Fetching image for: {post.name}")
            print(f"    Query: {title[:60]}")

        image = fetch_unsplash_image(
            query=title,
            slug=slug,
            access_key=access_key,
            verbose=verbose,
        )

        if not image:
            print(f"  FAILED (no image): {post.name}")
            failed += 1
            continue

        changed = add_image_to_post(post, image, dry_run=dry_run)
        if changed:
            print(f"  {'(dry run) ' if dry_run else ''}✓ {post.name} — {image['photographer']}")
            updated += 1
        else:
            skipped += 1

    print(f"\nDone. Updated: {updated}, Skipped: {skipped}, Failed: {failed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill Unsplash images for existing articles")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    run_backfill(dry_run=args.dry_run, verbose=args.verbose)
