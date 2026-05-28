"""
Blog-post plumbing CLI for the /blog-post skill.

Deterministic Shopify + image plumbing for creating MowDirect blog articles.
The skill (Claude) does the writing, link curation and image-prompt work;
this script does everything mechanical:

  search-catalogue   live Shopify product/collection search for internal links
                     (Spectrum-first ranking) and image sourcing
  fetch-source       fetch an arbitrary MowDirect/Compass URL -> text + image URLs
                     (the "repurpose existing content" input mode)
  check-title        exact-title collision check against the target blog
  gen-image          Gemini image generation with optional reference images
                     (default model gemini-3-pro-image-preview / Nano Banana Pro)
  upload-image       stagedUploadsCreate + fileCreate -> permanent Shopify CDN url
  create-article     articleCreate as an UNPUBLISHED draft, SEO metafields + tags
                     inline, author defaulted, exact-title collision auto-renamed

Run from the repo root with PYTHONPATH=. so `scripts.shopify_client` imports:

    PYTHONPATH=. .venv/bin/python scripts/blog_publish.py <subcommand> ...

Env vars (in .env):
    SHOPIFY_*            (see scripts/shopify_client.py)
    GEMINI_API_KEY       required only for `gen-image` (API loop image mode)
    BLOG_IMAGE_MODEL     optional, default "gemini-3-pro-image-preview"
    BLOG_DEFAULT_AUTHOR  optional, default "Wayne Theisinger"
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

from scripts.shopify_client import ShopifyClient

DEFAULT_BLOG_HANDLE = "news"  # "How tos and Garden Advice"
DEFAULT_AUTHOR = os.environ.get("BLOG_DEFAULT_AUTHOR", "Wayne Theisinger")
DEFAULT_IMAGE_MODEL = os.environ.get("BLOG_IMAGE_MODEL", "gemini-3-pro-image-preview")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _emit(obj) -> None:
    """Print a JSON result to stdout for the skill to read."""
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def clean_handle(text: str) -> str:
    """Slugify a title into a clean, keyword-bearing URL handle.

    Strips emoji, &amp; and other junk that has leaked into existing handles.
    """
    text = text.lower()
    text = text.replace("&amp;", " and ").replace("&", " and ")
    # drop anything that isn't a-z, 0-9, space or hyphen (kills emoji/punctuation)
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:120]


def _user_errors(result: dict, key: str) -> list[dict]:
    return (result.get(key) or {}).get("userErrors", []) or []


# ---------------------------------------------------------------------------
# Blog resolution
# ---------------------------------------------------------------------------

BLOGS_QUERY = """
{ blogs(first: 50) { edges { node { id handle title } } } }
"""


def resolve_blog(client: ShopifyClient, handle: str) -> dict:
    data = client.execute(BLOGS_QUERY)
    edges = data["blogs"]["edges"]
    for e in edges:
        if e["node"]["handle"] == handle:
            return e["node"]
    if edges:  # fall back to the first blog if the handle wasn't found
        return edges[0]["node"]
    _die("no blogs exist on this store; create one in Shopify admin first")


# ---------------------------------------------------------------------------
# search-catalogue
# ---------------------------------------------------------------------------

CATALOGUE_QUERY = """
query($q: String!, $pq: String!) {
  products(first: 15, query: $pq) {
    edges { node {
      title handle vendor productType status
      featuredImage { url altText }
    } }
  }
  collections(first: 8, query: $q) {
    edges { node { title handle } }
  }
}
"""


def cmd_search_catalogue(args) -> None:
    """Live Shopify product + collection search, Spectrum-first ranking.

    Emits relative on-store links (/products/<handle>, /collections/<handle>)
    so they are domain-agnostic, plus featured image URLs for the image cascade.
    """
    term = args.query.strip()
    # title/keyword search; only surface active products
    pq = f"title:*{term}* AND status:active"
    with ShopifyClient() as client:
        data = client.execute(CATALOGUE_QUERY, {"q": term, "pq": pq})

    products = []
    for e in data["products"]["edges"]:
        n = e["node"]
        products.append({
            "title": n["title"],
            "handle": n["handle"],
            "path": f"/products/{n['handle']}",
            "vendor": n.get("vendor"),
            "product_type": n.get("productType"),
            "is_spectrum": (n.get("vendor") or "").strip().lower() == "spectrum",
            "image_url": (n.get("featuredImage") or {}).get("url"),
            "image_alt": (n.get("featuredImage") or {}).get("altText"),
        })
    # Spectrum-first ranking, then title order preserved
    products.sort(key=lambda p: (not p["is_spectrum"],))

    collections = [
        {"title": e["node"]["title"], "handle": e["node"]["handle"],
         "path": f"/collections/{e['node']['handle']}"}
        for e in data["collections"]["edges"]
    ]
    _emit({"query": term, "products": products, "collections": collections})


# ---------------------------------------------------------------------------
# fetch-source  (repurpose-existing-content mode)
# ---------------------------------------------------------------------------

def cmd_fetch_source(args) -> None:
    """Fetch an arbitrary page and extract readable text + candidate images.

    Used for the 'repurpose a MowDirect/Compass URL' input mode. Images from
    these pages are fair game per the brief, so we surface og:image plus inline
    content images as candidates for the hero/inline cascade.
    """
    try:
        resp = requests.get(args.url, timeout=30,
                            headers={"User-Agent": "Mozilla/5.0 (blog-post skill)"})
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        _die(f"fetch failed: {e}")
    html = resp.text

    # og:image first (usually the lifestyle hero)
    images: list[str] = []
    for m in re.finditer(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html, re.I):
        images.append(m.group(1))
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)', html, re.I):
        src = m.group(1)
        if src.startswith("data:"):
            continue
        images.append(src)
    # de-dupe, keep order
    seen = set()
    images = [i for i in images if not (i in seen or seen.add(i))]

    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else None

    # crude readable-text extraction: strip scripts/styles/tags, collapse ws
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    out = {"url": args.url, "title": title,
           "text": text[:20000], "text_truncated": len(text) > 20000,
           "image_candidates": images[:25]}
    if args.out:
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2))
        _emit({"written": args.out, "title": title,
               "image_candidate_count": len(images), "chars": len(text)})
    else:
        _emit(out)


# ---------------------------------------------------------------------------
# check-title
# ---------------------------------------------------------------------------

ARTICLES_TITLES_QUERY = """
query($id: ID!) {
  blog(id: $id) {
    articles(first: 250) { edges { node { title handle } } }
  }
}
"""


def _existing_titles(client: ShopifyClient, blog_id: str) -> list[dict]:
    data = client.execute(ARTICLES_TITLES_QUERY, {"id": blog_id})
    return [e["node"] for e in data["blog"]["articles"]["edges"]]


def cmd_check_title(args) -> None:
    with ShopifyClient() as client:
        blog = resolve_blog(client, args.blog_handle)
        existing = _existing_titles(client, blog["id"])
    title_norm = args.title.strip().lower()
    exact = [a for a in existing if a["title"].strip().lower() == title_norm]
    _emit({
        "blog": blog["title"], "blog_id": blog["id"],
        "title": args.title,
        "exact_collision": bool(exact),
        "colliding": exact,
        "all_titles": [a["title"] for a in existing],
    })


# ---------------------------------------------------------------------------
# gen-image  (Gemini API loop)
# ---------------------------------------------------------------------------

def _load_reference(ref: str):
    """Load a reference image (local path or URL) into a PIL.Image."""
    from io import BytesIO

    from PIL import Image
    if ref.startswith(("http://", "https://")):
        r = requests.get(ref, timeout=30,
                        headers={"User-Agent": "Mozilla/5.0 (blog-post skill)"})
        r.raise_for_status()
        return Image.open(BytesIO(r.content))
    return Image.open(ref)


def cmd_gen_image(args) -> None:
    """Generate a hero/inline image via Gemini, optionally conditioned on
    reference images (the real product photo, for likeness preservation)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        _die("GEMINI_API_KEY not set. Add it to .env, or use the handoff image "
             "mode (write the prompt to a file and generate in an external UI).")
    try:
        from google import genai  # type: ignore
    except ImportError:
        _die("google-genai not installed. Run: .venv/bin/pip install google-genai")

    prompt = Path(args.prompt_file).read_text() if args.prompt_file else args.prompt
    if not prompt:
        _die("provide --prompt or --prompt-file")

    contents = [prompt]
    for ref in args.reference or []:
        try:
            contents.append(_load_reference(ref))
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] could not load reference {ref}: {e}", file=sys.stderr)

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(model=args.model, contents=contents)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    text_parts: list[str] = []
    idx = 0
    for cand in (resp.candidates or []):
        for part in (cand.content.parts if cand.content else []):
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                idx += 1
                path = out_dir / f"{args.name}_{idx}.png"
                path.write_bytes(inline.data)
                saved.append(str(path))
            elif getattr(part, "text", None):
                text_parts.append(part.text)

    if not saved:
        _die(f"model returned no image. Model said: {' '.join(text_parts)[:500]}")
    _emit({"model": args.model, "images": saved,
           "reference_count": len(contents) - 1,
           "model_text": " ".join(text_parts)[:500] or None})


# ---------------------------------------------------------------------------
# upload-image  (staged upload -> fileCreate -> permanent CDN url)
# ---------------------------------------------------------------------------

STAGED_UPLOADS = """
mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) {
    stagedTargets { url resourceUrl parameters { name value } }
    userErrors { field message }
  }
}
"""

FILE_CREATE = """
mutation fileCreate($files: [FileCreateInput!]!) {
  fileCreate(files: $files) {
    files { id fileStatus alt
            ... on MediaImage { image { url } } }
    userErrors { field message }
  }
}
"""

FILE_POLL = """
query($id: ID!) {
  node(id: $id) {
    ... on MediaImage { fileStatus image { url } }
  }
}
"""


def cmd_upload_image(args) -> None:
    path = Path(args.file)
    if not path.exists():
        _die(f"file not found: {path}")
    data = path.read_bytes()
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"

    with ShopifyClient() as client:
        staged = client.execute(STAGED_UPLOADS, {"input": [{
            "filename": path.name, "mimeType": mime,
            "resource": "FILE", "httpMethod": "POST",
        }]})
        errs = _user_errors(staged, "stagedUploadsCreate")
        if errs:
            _die(f"stagedUploadsCreate: {errs}")
        target = staged["stagedUploadsCreate"]["stagedTargets"][0]

        # POST the bytes to the staged target
        form = {p["name"]: p["value"] for p in target["parameters"]}
        up = requests.post(target["url"], data=form,
                          files={"file": (path.name, data, mime)}, timeout=120)
        if up.status_code not in (200, 201, 204):
            _die(f"staged upload POST failed ({up.status_code}): {up.text[:300]}")

        created = client.execute(FILE_CREATE, {"files": [{
            "originalSource": target["resourceUrl"],
            "contentType": "IMAGE",
            "alt": args.alt or "",
        }]})
        errs = _user_errors(created, "fileCreate")
        if errs:
            _die(f"fileCreate: {errs}")
        file_node = created["fileCreate"]["files"][0]
        file_id = file_node["id"]

        # poll until the image is processed and a CDN url is available
        cdn_url = (file_node.get("image") or {}).get("url")
        for _ in range(30):
            if cdn_url:
                break
            time.sleep(2)
            node = client.execute(FILE_POLL, {"id": file_id})["node"] or {}
            if node.get("fileStatus") == "READY":
                cdn_url = (node.get("image") or {}).get("url")
            elif node.get("fileStatus") == "FAILED":
                _die("Shopify reported fileStatus FAILED for the upload")
        if not cdn_url:
            _die("timed out waiting for Shopify to process the uploaded image")
    _emit({"file_id": file_id, "cdn_url": cdn_url, "alt": args.alt or ""})


# ---------------------------------------------------------------------------
# create-article
# ---------------------------------------------------------------------------

ARTICLE_CREATE = """
mutation articleCreate($article: ArticleCreateInput!) {
  articleCreate(article: $article) {
    article { id title handle isPublished }
    userErrors { field message code }
  }
}
"""


def _seo_metafields(seo_title: str | None, seo_desc: str | None) -> list[dict]:
    mfs = []
    if seo_title:
        mfs.append({"namespace": "global", "key": "title_tag",
                    "type": "single_line_text_field", "value": seo_title})
    if seo_desc:
        mfs.append({"namespace": "global", "key": "description_tag",
                    "type": "single_line_text_field", "value": seo_desc})
    return mfs


def _dedupe_title(title: str, existing: list[dict]) -> tuple[str, bool]:
    """Defensive auto-rename if an exact title collision somehow remains.

    The skill is expected to rewrite the title itself on collision; this is a
    last-resort guard so create never hard-fails on a clash.
    """
    norm = {a["title"].strip().lower() for a in existing}
    if title.strip().lower() not in norm:
        return title, False
    n = 2
    while f"{title} ({n})".strip().lower() in norm:
        n += 1
    return f"{title} ({n})", True


def cmd_create_article(args) -> None:
    spec = json.loads(Path(args.json).read_text())
    title = spec["title"].strip()
    body = spec.get("body_html", "")
    if not body:
        _die("article JSON missing body_html")

    author = spec.get("author") or DEFAULT_AUTHOR
    handle = spec.get("handle") or clean_handle(title)
    tags = spec.get("tags") or []
    image_url = spec.get("image_url")
    image_alt = spec.get("image_alt") or title
    metafields = _seo_metafields(spec.get("seo_title"), spec.get("seo_description"))

    with ShopifyClient() as client:
        blog = resolve_blog(client, args.blog_handle)
        existing = _existing_titles(client, blog["id"])
        title, renamed = _dedupe_title(title, existing)
        if renamed:
            handle = clean_handle(title)
            print(f"  [warn] exact-title collision; auto-renamed to: {title}",
                  file=sys.stderr)

        article_input = {
            "blogId": blog["id"],
            "title": title,
            "author": {"name": author},
            "body": body,
            "handle": handle,
            "isPublished": False,  # always a draft; Wayne publishes in admin
            "tags": tags,
        }
        if spec.get("summary_html"):
            article_input["summary"] = spec["summary_html"]
        if metafields:
            article_input["metafields"] = metafields
        if image_url:
            article_input["image"] = {"url": image_url, "altText": image_alt}

        if args.dry_run:
            _emit({"dry_run": True, "blog": blog["title"],
                   "article_input": article_input,
                   "auto_renamed": renamed})
            return

        result = client.execute(ARTICLE_CREATE, {"article": article_input})
        errs = _user_errors(result, "articleCreate")
        if errs:
            _die(f"articleCreate: {errs}")
        art = result["articleCreate"]["article"]

    numeric_id = art["id"].rsplit("/", 1)[-1]
    store = os.environ["SHOPIFY_STORE_DOMAIN"]
    admin_url = (f"https://admin.shopify.com/store/"
                 f"{store.replace('.myshopify.com', '')}/articles/{numeric_id}")
    _emit({
        "created": True, "id": art["id"], "title": art["title"],
        "handle": art["handle"], "is_published": art["isPublished"],
        "auto_renamed": renamed, "blog": blog["title"],
        "admin_url": admin_url,
        "note": "Draft created (unpublished). Review and publish in Shopify admin.",
    })


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Blog-post plumbing for /blog-post")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search-catalogue", help="Shopify product/collection search")
    s.add_argument("--query", required=True)
    s.set_defaults(func=cmd_search_catalogue)

    s = sub.add_parser("fetch-source", help="fetch a URL -> text + image candidates")
    s.add_argument("--url", required=True)
    s.add_argument("--out", help="write JSON here instead of stdout")
    s.set_defaults(func=cmd_fetch_source)

    s = sub.add_parser("check-title", help="exact-title collision check")
    s.add_argument("--title", required=True)
    s.add_argument("--blog-handle", default=DEFAULT_BLOG_HANDLE)
    s.set_defaults(func=cmd_check_title)

    s = sub.add_parser("gen-image", help="Gemini image generation")
    s.add_argument("--prompt")
    s.add_argument("--prompt-file")
    s.add_argument("--reference", action="append",
                  help="reference image (path or URL); repeatable")
    s.add_argument("--out-dir", default="workdir/blog")
    s.add_argument("--name", default="hero")
    s.add_argument("--model", default=DEFAULT_IMAGE_MODEL)
    s.set_defaults(func=cmd_gen_image)

    s = sub.add_parser("upload-image", help="upload a local image -> Shopify CDN url")
    s.add_argument("--file", required=True)
    s.add_argument("--alt")
    s.set_defaults(func=cmd_upload_image)

    s = sub.add_parser("create-article", help="create an unpublished article")
    s.add_argument("--json", required=True, help="path to the article spec JSON")
    s.add_argument("--blog-handle", default=DEFAULT_BLOG_HANDLE)
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_create_article)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
