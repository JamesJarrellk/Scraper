from fastapi import FastAPI, Query
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import re, asyncio

app = FastAPI()

# Hub cities approximating nationwide coverage. Facebook Marketplace has no
# true "nationwide" search - every query is scoped to a location - so we fan
# out across major metros and merge/dedupe the results.
#
# FIX: values are real Facebook Marketplace city slugs, not guessed from the
# display name. Guessing (lowercasing "Nashville, TN" into "nashvilletn")
# produced dead URLs on every single city - that alone could explain 100% of
# the empty results, independent of any bot-detection issue.
HUB_CITIES = {
    "Nashville, TN": "nashville",
    "Atlanta, GA": "atlanta",
    "Dallas, TX": "dallas",
    "Charlotte, NC": "charlotte",
    "Chicago, IL": "chicago",
    "Phoenix, AZ": "phoenix",
    "Los Angeles, CA": "la",
    "Denver, CO": "denver",
    "Columbus, OH": "columbus",
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# Text that shows up on FB's logged-out / login-wall page. If we see this,
# the scrape didn't fail silently - we know it's a login wall, not "no
# listings", and can say so in the response instead of returning empty
# and looking like a clean zero-result search.
LOGIN_WALL_MARKERS = ("log into facebook", "you must log in", "log in to continue")


async def search_one_city(browser, query: str, display_name: str, slug: str, per_city_limit: int):
    page = await browser.new_page(user_agent=UA)
    status = "ok"
    results = []
    try:
        url = f"https://www.facebook.com/marketplace/{slug}/search?query={query}"
        await page.goto(url, timeout=25000)

        # FIX: a blind sleep doesn't know whether the listing grid ever
        # rendered. Wait for an actual listing link (or time out and say so),
        # so a slow/failed hydration is reported, not silently scraped as
        # "zero results".
        try:
            await page.wait_for_selector("a[href*='/marketplace/item/']", timeout=8000)
        except Exception:
            pass  # fall through to content check below - may be a real zero-result search

        html = await page.content()
        lower_html = html.lower()

        if any(marker in lower_html for marker in LOGIN_WALL_MARKERS):
            status = "login_wall"
        else:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.select("a[href*='/marketplace/item/']")[:per_city_limit]:
                href = a.get("href", "")
                m = re.search(r"/item/(\d+)", href)
                if not m:
                    continue
                text = a.get_text(separator="|", strip=True)
                parts = [t for t in text.split("|") if t]
                price = next((t for t in parts if "$" in t), None)
                title = next((t for t in parts if "$" not in t and len(t) > 3), None)
                results.append({
                    "listing_id": m.group(1),
                    "title": title,
                    "price": price,
                    "url": f"https://www.facebook.com{href.split('?')[0]}",
                    "location": display_name,
                })
            if not results:
                status = "zero_results"
    except Exception as e:
        status = f"error: {e.__class__.__name__}"
    finally:
        await page.close()
    return {"location": display_name, "status": status, "results": results}


@app.get("/search")
async def search_marketplace(
    query: str = Query(..., description="Search term, e.g. 'Shelby GT500'"),
    max_results: int = Query(30, description="Total results across all hub cities, after dedup"),
    per_city_limit: int = Query(10, description="Max results pulled per city before merge")
):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            tasks = [
                search_one_city(browser, query, display_name, slug, per_city_limit)
                for display_name, slug in HUB_CITIES.items()
            ]
            city_reports = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await browser.close()

    all_results = []
    city_statuses = {}
    for r in city_reports:
        if isinstance(r, dict):
            city_statuses[r["location"]] = r["status"]
            all_results.extend(r["results"])
        else:
            city_statuses["unknown"] = f"error: {r.__class__.__name__}"

    seen = set()
    deduped = []
    for item in all_results:
        if item["listing_id"] in seen:
            continue
        seen.add(item["listing_id"])
        deduped.append(item)

    # FIX: city_statuses makes failures visible instead of indistinguishable
    # from a genuine zero-result search. If every city comes back
    # "login_wall", that's Facebook blocking the request - not an empty
    # market - and now the response says so instead of hiding it.
    return {
        "query": query,
        "cities_searched": list(HUB_CITIES.keys()),
        "city_statuses": city_statuses,
        "count": len(deduped[:max_results]),
        "results": deduped[:max_results],
    }


@app.get("/health")
async def health():
    return {"status": "ok", "hub_cities": len(HUB_CITIES)}
