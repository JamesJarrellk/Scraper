from fastapi import FastAPI, Query
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import re, asyncio

app = FastAPI()

# Hub cities approximating nationwide coverage. Facebook Marketplace has no
# true "nationwide" search - every query is scoped to a location - so we fan
# out across major metros and merge/dedupe the results.
HUB_CITIES = [
    "Nashville, TN", "Atlanta, GA", "Dallas, TX", "Charlotte, NC",
    "Chicago, IL", "Phoenix, AZ", "Los Angeles, CA", "Denver, CO",
    "Columbus, OH",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

async def search_one_city(browser, query: str, location: str, per_city_limit: int):
    results = []
    page = await browser.new_page(user_agent=UA)
    try:
        loc_slug = location.replace(" ", "").replace(",", "").lower()
        url = f"https://www.facebook.com/marketplace/{loc_slug}/search?query={query}"
        await page.goto(url, timeout=25000)
        await page.wait_for_timeout(2500)
        html = await page.content()
    except Exception:
        html = ""
    finally:
        await page.close()

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
            "location": location,
        })
    return results

@app.get("/search")
async def search_marketplace(
    query: str = Query(..., description="Search term, e.g. 'Shelby GT500'"),
    max_results: int = Query(30, description="Total results across all hub cities, after dedup"),
    per_city_limit: int = Query(10, description="Max results pulled per city before merge")
):
    all_results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            tasks = [search_one_city(browser, query, city, per_city_limit) for city in HUB_CITIES]
            city_results = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await browser.close()

    for r in city_results:
        if isinstance(r, list):
            all_results.extend(r)

    seen = set()
    deduped = []
    for item in all_results:
        if item["listing_id"] in seen:
            continue
        seen.add(item["listing_id"])
        deduped.append(item)

    return {
        "query": query,
        "cities_searched": HUB_CITIES,
        "count": len(deduped[:max_results]),
        "results": deduped[:max_results],
    }

@app.get("/health")
async def health():
    return {"status": "ok", "hub_cities": len(HUB_CITIES)}
