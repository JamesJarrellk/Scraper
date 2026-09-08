from fastapi import FastAPI, Query
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import re

app = FastAPI()

# Default: Lebanon, TN. Facebook resolves a plain city string in the search URL.
DEFAULT_LOCATION = "Lebanon, TN"

@app.get("/search")
async def search_marketplace(
    query: str = Query(..., description="Search term, e.g. 'Shelby GT500'"),
    location: str = Query(DEFAULT_LOCATION),
    max_results: int = Query(20)
):
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
        ))
        loc_slug = location.replace(" ", "").replace(",", "").lower()
        url = f"https://www.facebook.com/marketplace/{loc_slug}/search?query={query}"
        await page.goto(url, timeout=30000)
        await page.wait_for_timeout(3000)
        html = await page.content()
        await browser.close()

    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a[href*='/marketplace/item/']")[:max_results]:
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
        })
    return {"query": query, "location": location, "count": len(results), "results": results}

@app.get("/health")
async def health():
    return {"status": "ok"}
