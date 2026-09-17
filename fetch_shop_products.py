#!/usr/bin/env python3
"""Pull the merch shop's product catalog from the Fourthwall storefront.

Writes historical_data/shop_catalog.json (git-committed, so the last good
catalog survives a run where Fourthwall is unreachable) and refreshes
belt_data/shop_thumbs/ with a 600px WebP thumbnail of every product's
featured mockup, regenerated each run from the live images so shop.html
shows the current design as soon as a product is edited in Fourthwall.

Why this exists: shop.html used to be built from a hand-maintained table
of product slugs and thumbnail URLs in build_site.py. Fourthwall
regenerates a product's mockups every time its design is edited (new
image ids, new signed thumbnail URLs), so the table went stale the first
time the artwork changed -- the site kept showing the old design while
the store sold the new one (2026-09-17: 32 of 48 pictures were out of
date within a day). Reading the store itself each run means the shop
page always lists exactly what the store sells, at the store's prices,
with the store's current pictures -- and adding, hiding, repricing or
redesigning a product in Fourthwall needs no code change at all.

How: Fourthwall's hosted storefront exposes a Shopify-style JSON document
for each product at /products/<slug>.js (no token or login needed -- it
is what the storefront's own pages read) and lists every public product
in /sitemap.xml. Hidden and private products are absent from the sitemap
and carry a status other than PUBLIC, so they never reach the site.

Optional stage: any failure (network down, store in maintenance, nothing
discovered) prints a warning and exits 0, leaving the previous catalog
and thumbnails in place, so a Fourthwall hiccup never blocks a build.
"""

import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

try:
    from build_site import FOURTHWALL_STORE_DOMAIN
except Exception:  # a broken build_site.py fails the pipeline at its own stage
    FOURTHWALL_STORE_DOMAIN = "college-football-belt-shop.fourthwall.com"

STORE_URL = f"https://{FOURTHWALL_STORE_DOMAIN}"
CATALOG_PATH = os.path.join("historical_data", "shop_catalog.json")
THUMBS_DIR = os.path.join("belt_data", "shop_thumbs")
THUMB_WIDTH = 600            # cards render at ~230-400 CSS px; 600 covers 2x screens
THUMB_QUALITY = 82
TIMEOUT = 25
USER_AGENT = "collegefootballbelt.com site build (+https://collegefootballbelt.com/about.html)"
SLUG_RE = re.compile(r"/products/([a-z0-9][a-z0-9-]*)")


def fetch(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = r.read()
            return data if binary else data.decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def discover_slugs():
    """Every public product's slug, from the storefront sitemap; the
    all-products collection page is the fallback if the sitemap is empty."""
    slugs = []
    seen = set()
    for source in (f"{STORE_URL}/sitemap.xml", f"{STORE_URL}/collections/all"):
        try:
            text = fetch(source)
        except RuntimeError as e:
            print(f"  couldn't read {source}: {e}")
            continue
        for slug in SLUG_RE.findall(text):
            if slug not in seen:
                seen.add(slug)
                slugs.append(slug)
        if slugs:
            break
    return slugs


def money(p):
    """Fourthwall's {"cents": 2500, "currency_iso": "USD"} -> (25.0, "USD")."""
    if not p:
        return None, None
    return round(int(p.get("cents") or 0) / 100, 2), p.get("currency_iso") or "USD"


def clean_html(s):
    s = re.sub(r"<[^>]*$", "", s or "")          # a tag cut off mid-way at the end
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def read_product(slug):
    """The fields shop.html needs, from one product's storefront JSON."""
    doc = json.loads(fetch(f"{STORE_URL}/products/{slug}.js"))
    if doc.get("status") != "PUBLIC":
        return None
    price_min, currency = money(doc.get("price_min") or doc.get("price"))
    price_max, _ = money(doc.get("price_max") or doc.get("price"))
    variants = doc.get("variants") or []
    featured = doc.get("featured_image") or {}
    images = [i.get("url") for i in (doc.get("images") or []) if i.get("url")]
    options = doc.get("options_by_name") or {}
    colors = (options.get("Color") or {}).get("values") or []
    sizes = (options.get("Size") or {}).get("values") or []
    return {
        "id": doc.get("id"),
        "slug": doc.get("slug") or slug,
        "name": doc.get("name") or doc.get("title") or slug,
        "url": f"{STORE_URL}/products/{doc.get('slug') or slug}",
        "available": bool(doc.get("available")) or any(v.get("available") for v in variants),
        "price_min": price_min,
        "price_max": price_max,
        "currency": currency or "USD",
        "colors": colors,
        "sizes": sizes,
        "description": clean_html(doc.get("description"))[:300],
        "image": {
            "url": featured.get("url"),
            "id": featured.get("id"),
            "width": featured.get("width"),
            "height": featured.get("height"),
        },
        "images": images[:3],
        "published_at": doc.get("published_at"),
        "updated_at": doc.get("updated_at"),
    }


def thumb_filename(product):
    """shop/<slug>-<image id prefix>.webp -- the image id changes whenever
    the design does, so browsers and CDNs never serve a stale picture."""
    img_id = (product["image"].get("id") or "")[:8] or "img"
    return f"{product['slug']}-{img_id}.webp"


def make_thumbnail(product, out_path):
    from PIL import Image
    url = product["image"].get("url")
    if not url:
        return False
    raw = fetch(url, binary=True)
    im = Image.open(io.BytesIO(raw))
    im.load()
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB")
    if im.width > THUMB_WIDTH:
        im = im.resize((THUMB_WIDTH, round(im.height * THUMB_WIDTH / im.width)), Image.LANCZOS)
    # transparent mockups (the tees) stay transparent so the card's paper
    # background shows through in both themes; opaque studio shots stay opaque
    im.save(out_path, "WEBP", quality=THUMB_QUALITY, method=4)
    return True


def main():
    print(f"Reading the product catalog from {STORE_URL} ...")
    try:
        slugs = discover_slugs()
    except Exception as e:
        print(f"  WARNING: couldn't reach the store ({e}); keeping the previous catalog.")
        return
    if not slugs:
        print("  WARNING: no products discovered; keeping the previous catalog.")
        return

    products, failures = [], []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for slug, result in zip(slugs, pool.map(lambda s: _safe(read_product, s), slugs)):
            if isinstance(result, Exception):
                failures.append(f"{slug}: {result}")
            elif result:
                products.append(result)
    if failures:
        print(f"  {len(failures)} product page(s) couldn't be read:")
        for f in failures[:5]:
            print(f"    {f}")
    if not products or len(failures) > len(slugs) // 2:
        print("  WARNING: the store answered for too few products; keeping the previous catalog.")
        return
    products.sort(key=lambda p: p["slug"])

    # thumbnails, regenerated from the live mockups every run
    os.makedirs(THUMBS_DIR, exist_ok=True)
    wanted = {}
    for p in products:
        wanted[p["slug"]] = thumb_filename(p)
    made, kept, missing = 0, 0, 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = []
        for p in products:
            out_path = os.path.join(THUMBS_DIR, wanted[p["slug"]])
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                kept += 1
                p["thumb"] = wanted[p["slug"]]
                continue
            jobs.append((p, out_path, pool.submit(_safe, make_thumbnail, p, out_path)))
        for p, out_path, job in jobs:
            result = job.result()
            if result is True:
                made += 1
                p["thumb"] = wanted[p["slug"]]
            else:
                missing += 1
                p["thumb"] = None
                if isinstance(result, Exception):
                    print(f"  thumbnail failed for {p['slug']}: {result}")
                try:
                    os.remove(out_path)
                except OSError:
                    pass
    # drop thumbnails for products that are gone or whose picture changed
    for name in os.listdir(THUMBS_DIR):
        if name not in wanted.values():
            os.remove(os.path.join(THUMBS_DIR, name))

    catalog = {
        "store_url": STORE_URL,
        "source": "Fourthwall storefront: /sitemap.xml + /products/<slug>.js",
        "products": products,
    }
    os.makedirs(os.path.dirname(CATALOG_PATH), exist_ok=True)
    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=1, ensure_ascii=False)
        f.write("\n")
    n_avail = sum(1 for p in products if p["available"])
    print(f"  {len(products)} public products ({n_avail} available) -> {CATALOG_PATH}")
    print(f"  thumbnails: {made} made, {kept} kept, {missing} missing (those cards fall back to the store's own image)")


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # reported by the caller; one bad product never kills the run
        return e


if __name__ == "__main__":
    main()
