"""
Scraper for compassgm.co.uk product pages.

Extracts product data and maps it to the MowDirect Shopify data structure.

Usage:
    # Single URL
    python scripts/compassgm_scraper.py https://compassgm.co.uk/product/some-product/

    # From a file of URLs (one per line)
    python scripts/compassgm_scraper.py --url-file urls.txt

    # Save output to JSON
    python scripts/compassgm_scraper.py https://... --output product.json
"""

import asyncio
import json
import re
import sys
import argparse
from pathlib import Path
from playwright.async_api import async_playwright, Browser, Page


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug_to_code(cls_name: str) -> str:
    """Convert a WooCommerce attribute CSS class to a display_attributes code.

    e.g. 'woocommerce-product-attributes-item--attribute_pa_cutting-capacity'
         → 'cutting_capacity'
    """
    match = re.search(r"attribute_pa_([a-z0-9\-]+)", cls_name)
    if match:
        return match.group(1).replace("-", "_")
    # Fallback: use last segment of class
    return cls_name.split("--")[-1].replace("-", "_")


def _clean_title(raw: str) -> str:
    """Strip ' - Compass GM' suffix if present."""
    return re.sub(r"\s*-\s*Compass GM\s*$", "", raw).strip()


_NOISY_ATTRS = re.compile(
    r'\s*(data-start|data-end|data-section-id|data-csa-[a-z\-]+|data-cel-widget'
    r'|data-feature-name|data-template-name)="[^"]*"',
    re.IGNORECASE,
)


def _clean_html(html: str) -> str:
    """Strip noisy data-* attributes inserted by Amazon/WooCommerce editors."""
    return _NOISY_ATTRS.sub("", html)


# ---------------------------------------------------------------------------
# Core scraper
# ---------------------------------------------------------------------------

async def scrape_product(page: Page, url: str) -> dict:
    """Scrape a single compassgm.co.uk product page and return a Shopify-ready dict."""
    await page.goto(url, wait_until="networkidle", timeout=45_000)

    # -----------------------------------------------------------------------
    # 1. JSON-LD — structured product data
    # -----------------------------------------------------------------------
    ld_texts = await page.evaluate("""() => {
        const tags = document.querySelectorAll('script[type="application/ld+json"]');
        return Array.from(tags).map(t => t.textContent);
    }""")

    product_ld = {}
    for txt in ld_texts:
        try:
            data = json.loads(txt)
            # Handle both top-level Product and @graph arrays
            if isinstance(data, dict):
                if data.get("@type") == "Product":
                    product_ld = data
                    break
                for item in data.get("@graph", []):
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        product_ld = item
                        break
        except json.JSONDecodeError:
            continue

    offers = product_ld.get("offers", {})

    # -----------------------------------------------------------------------
    # 2. Description HTML — from #productDescription inside the tab
    # -----------------------------------------------------------------------
    desc_html_raw = await page.evaluate("""() => {
        // Prefer the inner #productDescription div (richer HTML, no Amazon widget wrapper)
        const inner = document.querySelector('#productDescription');
        if (inner) return inner.innerHTML.trim();
        // Fallback: full tab panel minus the heading
        const panel = document.querySelector('#tab-description');
        if (panel) return panel.innerHTML.trim();
        return '';
    }""")
    desc_html = _clean_html(desc_html_raw)

    # -----------------------------------------------------------------------
    # 3. Spec table → display_attributes
    # -----------------------------------------------------------------------
    spec_rows = await page.evaluate("""() => {
        const rows = document.querySelectorAll('.woocommerce-product-attributes-item');
        return Array.from(rows).map(r => ({
            label: r.querySelector('th')?.innerText?.trim() || '',
            value: r.querySelector('td')?.innerText?.trim() || '',
            cls:   r.className
        }));
    }""")

    display_attributes = []
    for row in spec_rows:
        if not row["label"] or not row["value"]:
            continue
        display_attributes.append({
            "code":  _slug_to_code(row["cls"]),
            "label": row["label"],
            "value": row["value"],
        })

    # -----------------------------------------------------------------------
    # 4. Product images — full-size hrefs from Swiper gallery anchors
    # -----------------------------------------------------------------------
    swiper_images = await page.evaluate("""() => {
        // Full-size image is the href of .swiper-slide-imglink anchors
        const links = document.querySelectorAll('.cg-psp-gallery .swiper-slide-imglink');
        if (links.length) {
            return Array.from(links).map(a => a.href).filter(Boolean);
        }
        // Fallback: img src (still exclude thumbnails)
        const imgs = document.querySelectorAll('.swiper-slide img');
        return Array.from(imgs)
            .map(i => i.getAttribute('data-large_image') || i.getAttribute('data-src') || i.src)
            .filter(src => src && src.includes('compassgm.co.uk') && !src.includes('-150x150') && !src.includes('-300x300'));
    }""")

    # Deduplicate while preserving order
    seen = set()
    images = []
    for src in swiper_images:
        if src not in seen:
            seen.add(src)
            images.append(src)

    # JSON-LD images as fallback / supplement
    for img in product_ld.get("image", []):
        src = img.get("url") if isinstance(img, dict) else str(img)
        if src and src not in seen:
            seen.add(src)
            images.append(src)

    # -----------------------------------------------------------------------
    # 5. Vendor / brand
    # -----------------------------------------------------------------------
    vendor = ""
    for prop in product_ld.get("additionalProperty", []):
        if prop.get("name") == "pa_manufacturer":
            vendor = prop.get("value", "")
            break
    if not vendor:
        brand = product_ld.get("brand", {})
        vendor = brand.get("name", "") if isinstance(brand, dict) else str(brand)

    # -----------------------------------------------------------------------
    # 6. Product type — last segment of category breadcrumb
    # -----------------------------------------------------------------------
    raw_category = product_ld.get("category", "")
    # "Hedgetrimmers &gt; Cordless Hedgetrimmers" → "Cordless Hedgetrimmers"
    product_type = re.sub(r"&gt;", ">", raw_category).split(">")[-1].strip()

    # -----------------------------------------------------------------------
    # 7. Feature bullets — from WooCommerce short description <ul>
    # -----------------------------------------------------------------------
    bullet_lines = await page.evaluate("""() => {
        const items = document.querySelectorAll(
            '.woocommerce-product-details__short-description li'
        );
        return Array.from(items).map(li => li.innerText.trim()).filter(Boolean);
    }""")

    if bullet_lines:
        items = "".join(f"<li>{ln}</li>" for ln in bullet_lines)
        feature_bullets_html = f"<ul>\n{items}\n</ul>"
    else:
        # Fallback: plain-text description from JSON-LD
        plain_desc = product_ld.get("description", "")
        items = "".join(f"<li>{ln}</li>" for ln in plain_desc.split("  ") if ln.strip())
        feature_bullets_html = f"<ul>\n{items}\n</ul>" if items else ""

    bullet_two   = bullet_lines[1] if len(bullet_lines) > 1 else ""
    bullet_three = bullet_lines[2] if len(bullet_lines) > 2 else ""

    # -----------------------------------------------------------------------
    # 8. Availability / condition
    # -----------------------------------------------------------------------
    availability = offers.get("availability", "")
    in_stock = "InStock" in availability
    condition_raw = offers.get("itemCondition", "")
    condition = "New" if "New" in condition_raw else condition_raw

    # -----------------------------------------------------------------------
    # Assemble output
    # -----------------------------------------------------------------------
    return {
        # ── Core product ──────────────────────────────────────────────────
        "title":        _clean_title(product_ld.get("name", "")),
        "description_html": desc_html,
        "vendor":       vendor,
        "product_type": product_type,
        "status":       "active" if in_stock else "draft",

        # ── Variant ───────────────────────────────────────────────────────
        "sku":          product_ld.get("sku", ""),
        "barcode":      product_ld.get("gtin", ""),
        "price":        offers.get("price", ""),

        # ── Metafields ────────────────────────────────────────────────────
        "metafields": {
            # Shown as a spec table on the PDP
            "custom.display_attributes": display_attributes,
            # Feature bullet list HTML
            "custom.feature_bullets":    feature_bullets_html,
            # Individual highlight bullets
            "custom.bullet_two":         bullet_two,
            "custom.bullet_three":       bullet_three,
            # Filter / facet metafields
            "filter.brand":              vendor,
            "filter.condition":          condition,
        },

        # ── Media ─────────────────────────────────────────────────────────
        "images": images,

        # ── Source metadata ───────────────────────────────────────────────
        "source_url": url,
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run(urls: list[str], output_path: str | None = None):
    results = []
    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        for url in urls:
            print(f"Scraping: {url}", file=sys.stderr)
            try:
                data = await scrape_product(page, url)
                results.append(data)
                print(f"  ✓ {data['sku']} — {data['title']}", file=sys.stderr)
            except Exception as exc:
                print(f"  ✗ Error: {exc}", file=sys.stderr)
                results.append({"source_url": url, "error": str(exc)})
        await browser.close()

    output = json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False)

    if output_path:
        Path(output_path).write_text(output, encoding="utf-8")
        print(f"Saved to {output_path}", file=sys.stderr)
    else:
        print(output)

    return results


def main():
    parser = argparse.ArgumentParser(description="Scrape compassgm.co.uk product pages")
    parser.add_argument("urls", nargs="*", help="One or more product URLs")
    parser.add_argument("--url-file", help="Text file with one URL per line")
    parser.add_argument("--output", help="Save JSON output to this file")
    args = parser.parse_args()

    urls = list(args.urls)
    if args.url_file:
        urls += [ln.strip() for ln in Path(args.url_file).read_text().splitlines() if ln.strip()]

    if not urls:
        parser.error("Provide at least one URL or --url-file")

    asyncio.run(run(urls, output_path=args.output))


if __name__ == "__main__":
    main()
