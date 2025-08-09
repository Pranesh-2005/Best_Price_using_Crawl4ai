from flask import Flask, request, jsonify
from flask_cors import CORS
import asyncio
import re
import os
from crawl4ai import AsyncWebCrawler, BrowserConfig
import urllib.parse

app = Flask(__name__)
CORS(app)

SEARCH_ENGINE = "https://www.google.com/search?q="

# Extract price from text/HTML with better patterns
def extract_price(text, url):
    # Multiple price patterns for different sites
    price_patterns = [
        # Amazon patterns
        r'₹\s*([\d,]+(?:\.\d{1,2})?)',  # ₹25,999
        r'"price":\s*"₹([\d,]+(?:\.\d{1,2})?)"',  # JSON price
        r'priceblock_dealprice[^>]*>₹\s*([\d,]+(?:\.\d{1,2})?)',  # Amazon deal price
        r'priceblock_ourprice[^>]*>₹\s*([\d,]+(?:\.\d{1,2})?)',  # Amazon our price
        r'a-price-whole[^>]*>([\d,]+)',  # Amazon price whole
        
        # Flipkart patterns
        r'₹([\d,]+)\s*</div>',  # Flipkart price div
        r'"sellingPrice":\s*{\s*"amount":\s*([\d,]+)',  # Flipkart JSON
        r'_30jeq3[^>]*>₹([\d,]+)',  # Flipkart price class
        
        # Generic patterns
        r'(?:price|cost|amount)[^₹]*₹\s*([\d,]+(?:\.\d{1,2})?)',
        r'₹\s*([\d,]+(?:\.\d{1,2})?)\s*(?:only|/-)',
    ]
    
    prices = []
    for pattern in price_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                price = float(match.replace(",", ""))
                # Filter out unrealistic prices (too low or too high)
                if 1000 <= price <= 200000:  # Reasonable phone price range
                    prices.append(price)
            except ValueError:
                continue
    
    # Return the most common price or the median if multiple found
    if prices:
        # Remove duplicates and sort
        unique_prices = list(set(prices))
        if len(unique_prices) == 1:
            return unique_prices[0]
        # If multiple prices, return the median (likely most accurate)
        unique_prices.sort()
        return unique_prices[len(unique_prices) // 2]
    
    return None

# Crawl a single page and get price
async def crawl_price(url):
    try:
        config = BrowserConfig(
            headless=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
        async with AsyncWebCrawler(config=config) as crawler:
            result = await crawler.arun(
                url=url,
                wait_for="body",
                timeout=15000,
                # Add some delay to let page load
                delay_before_return_html=2.0
            )
        
        # Use both cleaned HTML and markdown for better extraction
        content = (result.cleaned_html or "") + " " + (result.markdown or "")
        price = extract_price(content, url)
        
        return {"url": url, "price": price}
    except Exception as e:
        print(f"Error crawling {url}: {e}")
        return {"url": url, "price": None, "error": str(e)}

# Search for product links using Google SERP
async def search_links(product):
    query = f'{product} price site:amazon.in OR site:flipkart.com OR site:ebay.com'
    search_url = SEARCH_ENGINE + urllib.parse.quote(query)
    
    try:
        async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
            result = await crawler.arun(url=search_url)
        
        content = result.cleaned_html or result.markdown or ""
        
        # More specific URL patterns for product pages
        url_patterns = [
            r'https://www\.amazon\.in/[^/]+/dp/[A-Z0-9]+[^\s<>"\']*',  # Amazon product pages
            r'https://www\.flipkart\.com/[^/]+/p/[a-z0-9]+[^\s<>"\']*',  # Flipkart product pages
            r'https://www\.ebay\.com/itm/[^\s<>"\']*',  # eBay listings
        ]
        
        all_links = []
        for pattern in url_patterns:
            links = re.findall(pattern, content)
            all_links.extend(links)
        
        # Clean and validate URLs
        clean_links = []
        for link in all_links:
            # Remove any trailing unwanted characters
            clean_link = re.sub(r'[<>"\']+.*$', '', link)
            clean_link = re.sub(r'&amp;.*$', '', clean_link)  # Remove URL parameters after &amp;
            
            # Validate URL format and ensure it's a product page
            if (re.match(r'^https?://', clean_link) and 
                len(clean_link) > 30 and 
                ('dp/' in clean_link or '/p/' in clean_link or 'itm/' in clean_link)):
                clean_links.append(clean_link)
        
        # Remove duplicates and limit to 3-5 for faster processing
        unique_links = list(dict.fromkeys(clean_links))[:3]
        print(f"Found {len(unique_links)} valid product links: {unique_links}")
        return unique_links
        
    except Exception as e:
        print(f"Error searching for links: {e}")
        return []

@app.route("/best-price", methods=["POST"])
def best_price():
    product = request.json.get("product", "").strip()
    if not product:
        return jsonify({"error": "No product name provided"}), 400

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        links = loop.run_until_complete(search_links(product))
        if not links:
            return jsonify({"error": "No product links found"}), 404

        print(f"Crawling {len(links)} links for prices...")
        results = loop.run_until_complete(asyncio.gather(*[crawl_price(l) for l in links]))
        
        priced = [r for r in results if r["price"] is not None]
        best = min(priced, key=lambda x: x["price"]) if priced else None
        
        return jsonify({
            "product": product, 
            "best": best, 
            "all": results,
            "found_links": len(links)
        })
        
    except Exception as e:
        return jsonify({"error": f"Server error: {str(e)}"}), 500
    finally:
        loop.close()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)