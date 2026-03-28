#!/usr/bin/env python3
"""
Witcher 3 Wiki Scraper
Fetches all Witcher 3 wiki pages via MediaWiki API and saves as markdown.
Licensed content is CC-BY-SA 3.0 from https://witcher.fandom.com
"""

import requests
import os
import re
import time
import sys
from bs4 import BeautifulSoup
import html2text

API_URL = "https://witcher.fandom.com/api.php"
FANDOM_BASE = "https://witcher.fandom.com/wiki/"
DOCS_DIR = "docs"
DELAY = 0.8  # seconds between requests

# Priority order matters: if a page is in multiple categories,
# the first matching category in this list wins.
CATEGORY_FOLDER_MAP = [
    ("The Witcher 3 secondary quests",  "quests/secondary"),
    ("The Witcher 3 quests",            "quests"),
    ("The Witcher 3 characters",        "characters"),
    ("The Witcher 3 locations",         "locations"),
    ("The Witcher 3 bestiary",          "bestiary"),
    ("The Witcher 3 steel weapons",     "equipment/weapons"),
    ("The Witcher 3 weapons",           "equipment/weapons"),
    ("The Witcher 3 armor",             "equipment/armor"),
    ("The Witcher 3 relics",            "equipment/relics"),
    ("The Witcher 3 potions",           "alchemy/potions"),
    ("The Witcher 3 alchemy formulae",  "alchemy/formulae"),
    ("The Witcher 3 ingredients",       "alchemy/ingredients"),
    ("The Witcher 3 crafting diagrams", "crafting/diagrams"),
    ("The Witcher 3 crafting components", "crafting/components"),
    ("The Witcher 3 quest items",       "items/quest"),
    ("The Witcher 3 items",             "items"),
    ("The Witcher 3 books",             "books"),
    ("The Witcher 3 DLC",               "dlc"),
    ("The Witcher 3",                   "general"),
]

ALL_CATEGORIES = [cat for cat, _ in CATEGORY_FOLDER_MAP]


def slugify(title):
    """Convert a wiki page title to a filename-safe slug."""
    slug = title.replace(" ", "_")
    slug = re.sub(r'[<>:"/\\|?*]', "-", slug)
    return slug


def get_category_members(category):
    """Return all page titles (namespace 0) in a category, handling pagination."""
    pages = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmtype": "page",
        "cmnamespace": 0,
        "cmlimit": 500,
        "format": "json",
    }
    while True:
        resp = requests.get(API_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        members = data.get("query", {}).get("categorymembers", [])
        pages.extend(m["title"] for m in members)
        if "continue" not in data:
            break
        params["cmcontinue"] = data["continue"]["cmcontinue"]
        time.sleep(DELAY)
    return pages


def fetch_page_html(title):
    """Fetch the parsed HTML for a wiki page via the API."""
    params = {
        "action": "parse",
        "page": title,
        "prop": "text",
        "disablelimitreport": 1,
        "disableeditsection": 1,
        "disabletoc": 0,
        "format": "json",
    }
    resp = requests.get(API_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        return None
    return data.get("parse", {}).get("text", {}).get("*", "")


def clean_html(html, page_title):
    """Strip Fandom-specific noise from parsed HTML, fix image/link URLs."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove edit section links, nav boxes, noprint, references section chrome
    for el in soup.select(
        ".mw-editsection, .navbox, .noprint, .noexcerpt, "
        ".mw-references-wrap .mw-references-columns, "
        ".wikia-ad, .fandom-community-header, "
        ".canontabs, .page-header, .toc"
    ):
        el.decompose()

    # Fix internal wiki links -> keep as fandom URLs
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/wiki/"):
            a["href"] = f"https://witcher.fandom.com{href}"

    # Fix image src -> use fandom CDN URL (absolute)
    for img in soup.find_all("img"):
        src = img.get("src", "")
        data_src = img.get("data-src", "")
        # Prefer data-src (lazy-loaded actual image) over placeholder
        final_src = data_src if data_src else src
        if final_src:
            img["src"] = final_src
        # Remove srcset to keep markdown clean
        if "srcset" in img.attrs:
            del img["srcset"]

    return str(soup)


def html_to_markdown(html, page_title):
    """Convert cleaned HTML to markdown."""
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = False
    h.body_width = 0          # don't wrap lines
    h.protect_links = False
    h.wrap_links = False
    h.images_as_html = False
    h.single_line_break = False
    h.bypass_tables = False
    h.ignore_tables = False

    md = h.handle(html)

    # Collapse excessive blank lines
    md = re.sub(r'\n{3,}', '\n\n', md)
    md = md.strip()

    # Add page title as H1 if not already present
    if not md.startswith("# "):
        md = f"# {page_title}\n\n{md}"

    # Append attribution footer
    md += (
        f"\n\n---\n"
        f"*Source: [{page_title}]({FANDOM_BASE}{page_title.replace(' ', '_')}) "
        f"on Witcher Wiki, licensed under [CC-BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/).*\n"
    )

    return md


def assign_folder(title, page_categories):
    """Pick the best docs subfolder for a page based on its categories."""
    for cat, folder in CATEGORY_FOLDER_MAP:
        if cat in page_categories:
            return folder
    return "general"


def get_page_categories(title):
    """Return categories (names only) that a page belongs to."""
    params = {
        "action": "query",
        "titles": title,
        "prop": "categories",
        "cllimit": 50,
        "format": "json",
    }
    resp = requests.get(API_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        cats = page.get("categories", [])
        return [c["title"].replace("Category:", "") for c in cats]
    return []


def save_page(title, folder, md):
    out_dir = os.path.join(DOCS_DIR, folder)
    os.makedirs(out_dir, exist_ok=True)
    filename = slugify(title) + ".md"
    filepath = os.path.join(out_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md)
    return filepath


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    print("Collecting page lists from all categories...")
    # page_title -> set of matching W3 categories
    page_to_cats = {}
    for cat in ALL_CATEGORIES:
        print(f"  Category: {cat}")
        members = get_category_members(cat)
        print(f"    {len(members)} pages")
        for title in members:
            page_to_cats.setdefault(title, set()).add(cat)
        time.sleep(DELAY)

    all_titles = sorted(page_to_cats.keys())
    print(f"\nTotal unique pages: {len(all_titles)}")

    os.makedirs(DOCS_DIR, exist_ok=True)

    skipped = 0
    errors = []

    for i, title in enumerate(all_titles, 1):
        cats = page_to_cats[title]
        folder = assign_folder(title, cats)
        out_path = os.path.join(DOCS_DIR, folder, slugify(title) + ".md")

        if os.path.exists(out_path):
            skipped += 1
            continue

        print(f"[{i}/{len(all_titles)}] {title} -> {folder}/")

        try:
            html = fetch_page_html(title)
            if not html:
                print(f"  WARNING: no content for {title}")
                errors.append(title)
                continue

            cleaned = clean_html(html, title)
            md = html_to_markdown(cleaned, title)
            save_page(title, folder, md)
        except Exception as e:
            print(f"  ERROR: {title}: {e}")
            errors.append(title)

        time.sleep(DELAY)

    print(f"\nDone. Skipped (already exist): {skipped}. Errors: {len(errors)}")
    if errors:
        print("Failed pages:")
        for t in errors:
            print(f"  {t}")


if __name__ == "__main__":
    main()
