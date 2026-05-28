"""
Seasonal idea-harvester for the /blog-ideas skill.

Mines the *retired* MowDirect blog (https://old.mowdirect.co.uk/blog/) for
articles published at the same time of year, so the skill can brainstorm fresh
angles off them. The old blog is WordPress with date-based URLs
(/blog/YYYY/MM/DD/slug/) and working month archives (/blog/YYYY/MM/), which is
how we enumerate "same time of year": fetch each year's month-archive and pool.

The retired site is Wayne's own; we fetch a handful of specific archive pages per
run (not a crawl), with a polite delay.

Subcommands:
  harvest   pool same-month articles across all years, dedupe against the LIVE
            blog (already-revived posts are dropped silently), emit JSON

Claude does the creative work (re-angling, product grounding, shortlist). For a
deep read of a finalist's full text, the skill calls
`scripts/blog_publish.py fetch-source --url <old article url>` (no duplication).

    PYTHONPATH=. .venv/bin/python scripts/blog_ideas.py harvest --month 5
"""
import argparse
import datetime as dt
import html as html_lib
import json
import re
import sys
import time

import requests

from scripts.blog_publish import DEFAULT_BLOG_HANDLE, _existing_titles, resolve_blog
from scripts.shopify_client import ShopifyClient

OLD_BLOG = "https://old.mowdirect.co.uk/blog"
UA = {"User-Agent": "Mozilla/5.0 (blog-ideas harvester; MowDirect own-site)"}
MONTHS = ["", "January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

# /blog/YYYY/MM/DD/slug/
ARTICLE_RE = re.compile(
    r'https?://old\.mowdirect\.co\.uk/blog/(\d{4})/(\d{2})/(\d{2})/([a-z0-9\-]+)/?')
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _strip(html: str) -> str:
    text = TAG_RE.sub(" ", html)
    text = html_lib.unescape(text)  # decode &#8211; &#8217; &amp; etc.
    return WS_RE.sub(" ", text).strip()


def _slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").strip().title()


def _norm_title(t: str) -> set[str]:
    """Token set for fuzzy title comparison (lowercased alnum words ≥3 chars)."""
    words = re.findall(r"[a-z0-9]+", t.lower())
    stop = {"the", "and", "for", "you", "your", "how", "what", "are", "with",
            "some", "a", "an", "to", "of", "in", "on", "do", "i", "my", "is"}
    return {w for w in words if len(w) >= 3 and w not in stop}


def _is_revived(old_title: str, old_slug: str, live: list[dict]) -> bool:
    """True if this old article already exists on the live blog (fuzzy)."""
    old_tokens = _norm_title(old_title)
    for art in live:
        # slug/handle exact-ish match
        if old_slug and old_slug == art.get("handle"):
            return True
        live_tokens = _norm_title(art["title"])
        if not old_tokens or not live_tokens:
            continue
        overlap = len(old_tokens & live_tokens)
        union = len(old_tokens | live_tokens)
        jaccard = overlap / union if union else 0.0
        # high overlap OR one title's distinctive words mostly inside the other
        contained = overlap / min(len(old_tokens), len(live_tokens))
        if jaccard >= 0.6 or contained >= 0.8:
            return True
    return False


# ---------------------------------------------------------------------------
# Archive parsing
# ---------------------------------------------------------------------------

def _parse_archive(html: str) -> list[dict]:
    """Extract {url, date, slug, title, excerpt} from a month-archive page."""
    # de-dupe article URLs in document order
    urls: list[tuple] = []
    seen = set()
    for m in ARTICLE_RE.finditer(html):
        url = m.group(0)
        if not url.endswith("/"):
            url += "/"
        if url in seen:
            continue
        seen.add(url)
        urls.append((url, m.group(1), m.group(2), m.group(3), m.group(4)))

    out = []
    for i, (url, y, mo, d, slug) in enumerate(urls):
        # title: longest anchor text pointing at this url
        title = ""
        for am in re.finditer(
                re.escape(url.rstrip("/")) + r'/?["\'][^>]*>(.*?)</a>', html, re.S):
            cand = _strip(am.group(1))
            if len(cand) > len(title):
                title = cand
        if not title:
            title = _slug_to_title(slug)

        # excerpt: text between this article's first mention and the next
        # article URL (or +4000 chars), tags stripped, title + URLs removed
        start = html.find(url.rstrip("/"))
        end = (html.find(urls[i + 1][0].rstrip("/"), start + 1)
               if i + 1 < len(urls) else start + 4000)
        if end <= start:
            end = start + 4000
        chunk_raw = html[start:end]
        # skip past the end of the title link so the href/markup doesn't leak in
        cut = chunk_raw.find("</a>")
        if 0 <= cut < 600:
            chunk_raw = chunk_raw[cut + 4:]
        chunk = _strip(chunk_raw)
        chunk = re.sub(r"https?://\S+", " ", chunk)  # strip stray URLs
        chunk = WS_RE.sub(" ", chunk).replace(title, " ", 1).strip()
        excerpt = chunk[:320].strip()

        out.append({
            "url": url, "date": f"{y}-{mo}-{d}", "year": int(y),
            "slug": slug, "title": title, "excerpt": excerpt,
        })
    return out


def _fetch_archive(year: int, month: int) -> list[dict]:
    url = f"{OLD_BLOG}/{year}/{month:02d}/"
    try:
        r = requests.get(url, timeout=20, headers=UA)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] {url}: {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        return []
    return _parse_archive(r.text)


# ---------------------------------------------------------------------------
# harvest
# ---------------------------------------------------------------------------

def cmd_harvest(args) -> None:
    month = args.month
    if not 1 <= month <= 12:
        print("ERROR: --month must be 1-12", file=sys.stderr)
        sys.exit(1)
    year_to = args.year_to or dt.date.today().year
    year_from = args.year_from

    # live blog titles for dedupe (already-revived -> dropped silently)
    with ShopifyClient() as client:
        blog = resolve_blog(client, args.blog_handle)
        live = _existing_titles(client, blog["id"])

    candidates: list[dict] = []
    years_with_posts: list[int] = []
    revived_dropped = 0
    for year in range(year_from, year_to + 1):
        arts = _fetch_archive(year, month)
        if arts:
            years_with_posts.append(year)
        for a in arts:
            if _is_revived(a["title"], a["slug"], live):
                revived_dropped += 1
                continue
            candidates.append(a)
        time.sleep(0.3)  # polite to the retired site

    # newest first
    candidates.sort(key=lambda a: a["date"], reverse=True)

    print(json.dumps({
        "target_month": month,
        "month_name": MONTHS[month],
        "years_probed": [year_from, year_to],
        "years_with_posts": years_with_posts,
        "candidate_count": len(candidates),
        "revived_dropped": revived_dropped,
        "live_blog": blog["title"],
        "candidates": candidates,
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Seasonal idea-harvester for /blog-ideas")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("harvest", help="pool same-month old articles across years")
    s.add_argument("--month", type=int, required=True, help="1-12")
    s.add_argument("--year-from", type=int, default=2015)
    s.add_argument("--year-to", type=int, default=None, help="defaults to this year")
    s.add_argument("--blog-handle", default=DEFAULT_BLOG_HANDLE)
    s.set_defaults(func=cmd_harvest)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
