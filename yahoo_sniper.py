import requests
from bs4 import BeautifulSoup
import time
import json
import os
import urllib.parse
from datetime import datetime, timedelta, timezone
import re
import hashlib
import sqlite3
from flask import Flask
import threading


scraper_app = Flask(__name__)

@scraper_app.route('/health', methods=['GET'])
def health():
    return {"status": "healthy", "service": "auction-scraper"}, 200

@scraper_app.route('/', methods=['GET'])
def root():
    return {"service": "Yahoo Auction Scraper", "status": "running"}, 200

def run_health_server():
    port = int(os.environ.get('PORT', 8000))
    scraper_app.run(host='0.0.0.0', port=port, debug=False)

# At the top of the file, replace the hardcoded URLs with:
DISCORD_BOT_WEBHOOK = os.getenv('DISCORD_BOT_WEBHOOK', "http://localhost:8000/webhook")
DISCORD_BOT_HEALTH = os.getenv('DISCORD_BOT_HEALTH', "http://localhost:8000/health") 
DISCORD_BOT_STATS = os.getenv('DISCORD_BOT_STATS', "http://localhost:8000/stats")
USE_DISCORD_BOT = True

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1389882074097520740/k1NDpWtPt1q116RF0Ndf2DDLmYCtwGSUGZla7n9kVD5d2ZX-vVerVx4RROCpGiyEtHzu"
MAX_PRICE_YEN = 100000
SEEN_FILE = "seen_yahoo.json"
BRANDS_FILE = "brands.json"
EXCHANGE_RATE_FILE = "exchange_rate.json"
SCRAPER_DB = "auction_tracking.db"

MAX_PRICE_USD = 1200
MIN_PRICE_USD = 3
MAX_LISTINGS_PER_BRAND = 25
ONLY_BUY_IT_NOW = False
PRICE_QUALITY_THRESHOLD = 0.15
ENABLE_RESALE_BOOST = True
ENABLE_INTELLIGENT_FILTERING = True

BASE_URL = "https://auctions.yahoo.co.jp/search/search?p={}&n=50&b=1&s1=new&o1=d&minPrice=1&maxPrice={}"

exchange_rate_cache = {"rate": 150.0, "timestamp": 0}

EXCLUDED_ITEMS = {
    "perfume", "cologne", "fragrance", "香水",
    "watch", "時計", 
    "motorcycle", "engine", "エンジン", "cb400", "vtr250",
    "server", "raid", "pci", "computer",
    "食品", "food", "snack", "チップ",
    "財布", "バッグ", "鞄", "カバン", "ハンドバッグ", "トートバッグ", "クラッチ", "ポーチ",
    "香水", "フレグランス", "コロン", "スプレー",
    "時計", "ネックレス", "ブレスレット", "指輪", "イヤリング",
    "ベルト", "ネクタイ", "スカーフ", "手袋", "帽子", "キャップ", "ビーニー",
    "chip", "chips", "チップ", "スナック", "食品", "food", "snack",
    "poster", "ポスター", "sticker", "ステッカー", "magazine", "雑誌",
    "dvd", "book", "本", "figure", "フィギュア", "toy", "おもちゃ",
    "phone case", "ケース", "iphone", "samsung", "tech", "電子",
    "fred perry", "フレッドペリー"
}

CLOTHING_KEYWORDS = {
    "shirt", "tee", "tshirt", "t-shirt", "polo", "button-up", "dress shirt",
    "jacket", "blazer", "coat", "outerwear", "bomber", "varsity", "denim jacket",
    "pants", "trousers", "jeans", "chinos", "slacks", "cargo", "sweatpants",
    "hoodie", "sweatshirt", "pullover", "sweater", "jumper", "cardigan",
    "dress", "gown", "midi", "maxi", "mini dress", "cocktail dress",
    "skirt", "mini skirt", "pencil skirt", "pleated", "a-line",
    "shorts", "bermuda", "cargo shorts", "denim shorts",
    "tank top", "vest", "camisole", "blouse", "top",
    "シャツ", "Tシャツ", "ポロシャツ", "ブラウス", "トップス",
    "ジャケット", "ブレザー", "コート", "アウター", "ボンバー",
    "パンツ", "ズボン", "ジーンズ", "チノパン", "スラックス",
    "パーカー", "スウェット", "プルオーバー", "セーター", "ニット",
    "ワンピース", "ドレス", "ガウン", "ミディ", "マキシ",
    "スカート", "ミニスカート", "ペンシル", "プリーツ",
    "ショーツ", "ショートパンツ", "タンクトップ", "ベスト"
}

EXCLUDED_BRANDS = {
    "thrasher", "gap", "adidas", "uniqlo", "gu", "zara", "h&m",
    "スラッシャー", "シュプリーム", "ナイキ", "アディダス", "ユニクロ"
}

COMPLETELY_EXCLUDED_BRANDS = {
    "undercoverism"
}

def load_brand_data():
    try:
        with open(BRANDS_FILE, "r", encoding="utf-8") as f:
            brand_data = json.load(f)
            
        filtered_data = {}
        for brand_key, brand_info in brand_data.items():
            brand_lower = brand_key.lower()
            if not any(excluded in brand_lower for excluded in COMPLETELY_EXCLUDED_BRANDS):
                filtered_data[brand_key] = brand_info
            else:
                print(f"🚫 Excluding brand: {brand_key}")
                
        return filtered_data
        
    except FileNotFoundError:
        example_brands = {
            "raf_simons": {
                "variants": ["raf simons", "ラフシモンズ", "raf"],
                "subcategories": ["shirt", "tee", "jacket", "pants", "シャツ", "Tシャツ", "ジャケット"]
            },
            "rick_owens": {
                "variants": ["rick owens", "リックオウエンス", "rick"],
                "subcategories": ["shirt", "jacket", "pants", "シャツ", "ジャケット"]
            },
            "comme_des_garcons": {
                "variants": ["comme des garcons", "コムデギャルソン", "cdg"],
                "subcategories": ["shirt", "jacket", "pants", "シャツ", "ジャケット"]
            },
            "maison_margiela": {
                "variants": ["maison margiela", "メゾンマルジェラ", "margiela"],
                "subcategories": ["shirt", "jacket", "pants", "シャツ", "ジャケット"]
            },
            "jean_paul_gaultier": {
                "variants": ["jean paul gaultier", "ジャンポールゴルチエ", "gaultier"],
                "subcategories": ["shirt", "jacket", "pants", "シャツ", "ジャケット"]
            }
        }
        with open(BRANDS_FILE, "w", encoding="utf-8") as f:
            json.dump(example_brands, f, ensure_ascii=False, indent=2)
        print(f"✅ Created example {BRANDS_FILE}")
        return example_brands

BRAND_DATA = load_brand_data()

seen_ids = set(json.load(open(SEEN_FILE))) if os.path.exists(SEEN_FILE) else set()

def save_seen_ids():
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen_ids), f)

def load_exchange_rate():
    global exchange_rate_cache
    try:
        if os.path.exists(EXCHANGE_RATE_FILE):
            with open(EXCHANGE_RATE_FILE, "r") as f:
                exchange_rate_cache = json.load(f)
    except Exception as e:
        print(f"Error loading exchange rate: {e}")

def save_exchange_rate():
    try:
        with open(EXCHANGE_RATE_FILE, "w") as f:
            json.dump(exchange_rate_cache, f)
    except Exception as e:
        print(f"Error saving exchange rate: {e}")

def get_usd_jpy_rate():
    global exchange_rate_cache
    current_time = time.time()
    
    if current_time - exchange_rate_cache.get("timestamp", 0) < 3600:
        return exchange_rate_cache["rate"]
    
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=10)
        if response.status_code == 200:
            data = response.json()
            rate = data["rates"]["JPY"]
            if rate and 100 < rate < 200:
                exchange_rate_cache = {"rate": float(rate), "timestamp": current_time}
                save_exchange_rate()
                print(f"✅ Updated exchange rate: 1 USD = {rate:.2f} JPY")
                return rate
    except Exception as e:
        print(f"Failed to get exchange rate: {e}")
    
    fallback_rate = exchange_rate_cache.get("rate", 150.0)
    print(f"⚠️  Using fallback exchange rate: 1 USD = {fallback_rate:.2f} JPY")
    return fallback_rate

def convert_jpy_to_usd(jpy_amount):
    rate = get_usd_jpy_rate()
    return jpy_amount / rate

def check_discord_bot_health():
    try:
        response = requests.get(DISCORD_BOT_HEALTH, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("bot_ready") and data.get("guild_connected"):
                return True, "Bot healthy"
            else:
                return False, f"Bot not ready: {data}"
        else:
            return False, f"HTTP {response.status_code}"
    except Exception as e:
        return False, f"Connection error: {e}"

def log_scraper_stats(total_found, quality_filtered, sent_to_discord, errors_count, keywords_searched):
    try:
        conn = sqlite3.connect(SCRAPER_DB)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO scraper_stats 
            (total_found, quality_filtered, sent_to_discord, errors_count, keywords_searched)
            VALUES (?, ?, ?, ?, ?)
        ''', (total_found, quality_filtered, sent_to_discord, errors_count, keywords_searched))
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        print(f"⚠️ Could not log scraper stats: {e}")

def is_clothing_item(title):
    title_lower = title.lower()
    
    for excluded in EXCLUDED_ITEMS:
        if excluded in title_lower:
            return False
    
    for clothing_word in CLOTHING_KEYWORDS:
        if clothing_word in title_lower:
            return True
    
    return True

def calculate_resale_value_boost(title, brand, price_usd):
    title_lower = title.lower()
    boost = 0.0
    
    archive_keywords = [
        "archive", "rare", "vintage", "fw", "ss", "runway", "campaign",
        "limited", "exclusive", "sample", "prototype", "deadstock",
        "アーカイブ", "レア", "ヴィンテージ", "限定", "サンプル",
        "collaboration", "collab", "コラボ"
    ]
    
    for keyword in archive_keywords:
        if keyword in title_lower:
            boost += 0.4
            print(f"🔥 Archive boost: {keyword} found in {title[:30]}...")
            break
            
    brand_lower = brand.lower() if brand else ""
    
    if "raf" in brand_lower:
        if any(word in title_lower for word in ["tee", "t-shirt", "shirt", "シャツ", "Tシャツ"]):
            boost += 0.4
            print(f"🌟 Raf Simons tee boost: {title[:30]}...")
        elif any(word in title_lower for word in ["jacket", "hoodie", "sweater", "pants"]):
            boost += 0.25
    elif "rick" in brand_lower:
        boost += 0.2
    elif any(designer in brand_lower for designer in ["margiela", "gaultier", "yohji", "junya"]):
        boost += 0.15
    
    collab_keywords = ["collaboration", "collab", "x ", " x ", "コラボ"]
    for keyword in collab_keywords:
        if keyword in title_lower:
            boost += 0.2
            break
    
    size_keywords = ["xl", "xxl", "large", "l ", "50", "52", "54"]
    for size in size_keywords:
        if size in title_lower:
            boost += 0.05
            break
    
    return min(boost, 0.8)

def calculate_deal_quality(price_usd, brand, title):
    title_lower = title.lower()
    
    if any(word in title_lower for word in ["tee", "t-shirt", "シャツ", "Tシャツ"]):
        base_price = 40
        item_type = "tee"
    elif any(word in title_lower for word in ["shirt", "button", "dress shirt"]):
        base_price = 60
        item_type = "shirt"
    elif any(word in title_lower for word in ["jacket", "blazer", "ジャケット"]):
        base_price = 120
        item_type = "jacket"
    elif any(word in title_lower for word in ["coat", "outerwear", "コート"]):
        base_price = 150
        item_type = "coat"
    elif any(word in title_lower for word in ["hoodie", "sweatshirt", "パーカー"]):
        base_price = 80
        item_type = "hoodie"
    elif any(word in title_lower for word in ["pants", "trousers", "jeans", "パンツ"]):
        base_price = 80
        item_type = "pants"
    else:
        base_price = 60
        item_type = "other"
    
    brand_multipliers = {
        "raf_simons": 2.0,
        "rick_owens": 1.8,
        "maison_margiela": 1.7,
        "jean_paul_gaultier": 1.6,
        "yohji_yamamoto": 1.5,
        "junya_watanabe": 1.4,
        "comme_des_garcons": 1.3,
        "undercover": 1.2,
        "gosha_rubchinskiy": 1.1,
        "helmut_lang": 1.1,
        "hood_by_air": 1.1,
        "alyx": 1.1,
        "bottega_veneta": 1.0,
        "vetements": 1.2,
        "balenciaga": 1.1,
        "chrome_hearts": 1.2,
        "celine": 1.0
    }
    
    brand_key = brand.lower().replace(" ", "_") if brand else "unknown"
    brand_multiplier = brand_multipliers.get(brand_key, 1.0)
    market_price = base_price * brand_multiplier
    
    if price_usd >= market_price * 1.5:
        base_quality = 0.2
    elif price_usd >= market_price:
        base_quality = 0.5
    else:
        base_quality = min(1.0, 0.8 + (market_price - price_usd) / market_price)
    
    resale_boost = calculate_resale_value_boost(title, brand, price_usd)
    final_quality = min(1.0, base_quality + resale_boost)
    
    return max(0.0, final_quality)

def is_quality_listing(price_usd, brand, title):
    if price_usd < MIN_PRICE_USD or price_usd > MAX_PRICE_USD:
        return False, f"Price ${price_usd:.2f} outside range ${MIN_PRICE_USD}-{MAX_PRICE_USD}"
    
    if not is_clothing_item(title):
        return False, f"Not clothing item"
    
    deal_quality = calculate_deal_quality(price_usd, brand, title)
    
    brand_key = brand.lower().replace(" ", "_") if brand else "unknown"
    high_resale_brands = ["raf_simons", "rick_owens", "maison_margiela", "jean_paul_gaultier"]
    
    if any(hrb in brand_key for hrb in high_resale_brands):
        threshold = 0.1
    else:
        threshold = PRICE_QUALITY_THRESHOLD
    
    if deal_quality < threshold:
        return False, f"Deal quality {deal_quality:.1%} below threshold {threshold:.1%}"
    
    if price_usd <= 100:
        return True, f"Potential steal: ${price_usd:.2f}"
    
    if price_usd >= 200 and deal_quality >= 0.3:
        return True, f"High-value find: ${price_usd:.2f} with {deal_quality:.1%} quality"
    
    return True, f"Quality listing: {deal_quality:.1%} deal quality"

def is_valid_brand_item(title):
    title_lower = title.lower()
    
    brand_match = False
    matched_brand = None
    for brand, details in BRAND_DATA.items():
        for variant in details["variants"]:
            if variant.lower() in title_lower:
                brand_match = True
                matched_brand = brand
                break
        if brand_match:
            break
    
    if not brand_match:
        return False, None
    
    for excluded in COMPLETELY_EXCLUDED_BRANDS:
        if excluded.lower() in matched_brand.lower():
            return False, None
    
    for excluded in EXCLUDED_BRANDS:
        if excluded.lower() in title_lower:
            return False, None
    
    if not is_clothing_item(title):
        return False, None
    
    return True, matched_brand

def generate_intelligent_keywords():
    all_keywords = []
    
    for brand, details in BRAND_DATA.items():
        variants = details["variants"]
        
        for variant in variants[:2]:
            clothing_terms = ["shirt", "tee", "jacket", "pants", "hoodie", "coat", "sweater"]
            for term in clothing_terms:
                all_keywords.append(f"{variant} {term}")
            
            jp_clothing_terms = ["シャツ", "Tシャツ", "ジャケット", "パンツ", "パーカー"]
            for term in jp_clothing_terms:
                all_keywords.append(f"{variant} {term}")
            
            archive_terms = ["archive", "rare", "vintage", "fw", "ss"]
            for term in archive_terms:
                all_keywords.append(f"{variant} {term}")
        
        for variant in variants[:1]:
            all_keywords.append(variant)
    
    print(f"Generated {len(all_keywords)} intelligent keywords")
    return all_keywords

def calculate_listing_priority(listing_data):
    price_usd = listing_data["price_usd"]
    deal_quality = listing_data["deal_quality"]
    title = listing_data["title"].lower()
    brand = listing_data["brand"].lower()
    
    priority = deal_quality * 100
    
    if any(hrb in brand for hrb in ["raf_simons", "rick_owens", "margiela"]):
        priority += 30
    
    if price_usd <= 100:
        priority += 25
    elif price_usd <= 200:
        priority += 15
    
    if any(word in title for word in ["archive", "rare", "fw", "ss", "アーカイブ", "レア"]):
        priority += 30
    
    if "raf" in brand and any(word in title for word in ["tee", "t-shirt", "shirt"]):
        priority += 25
    
    return priority

def extract_seller_info(soup, item):
    try:
        seller_link = item.select_one("a[href*='sellerID']")
        if seller_link:
            href = seller_link.get('href', '')
            seller_match = re.search(r'sellerID=([^&]+)', href)
            if seller_match:
                return seller_match.group(1)
        
        seller_span = item.select_one(".Product__seller")
        if seller_span:
            return seller_span.get_text(strip=True)
        
        return "unknown"
        
    except Exception:
        return "unknown"

def check_if_auction_exists_in_db(auction_id):
    """Check if auction already exists in Discord bot's database"""
    try:
        conn = sqlite3.connect(SCRAPER_DB)
        cursor = conn.cursor()
        cursor.execute('SELECT auction_id FROM listings WHERE auction_id = ?', (auction_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    except Exception:
        # If we can't check the database, assume it doesn't exist to avoid missing items
        return False

def search_yahoo(keyword_combo):
    encoded_kw = urllib.parse.quote(keyword_combo)
    url = BASE_URL.format(encoded_kw, MAX_PRICE_YEN)
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"❌ HTTP {resp.status_code} for {keyword_combo}")
            return [], 1
            
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("li.Product")
        
        print(f"🔍 Found {len(items)} raw items for '{keyword_combo}'")
        
        quality_listings = []
        error_count = 0
        skipped_seen = 0
        skipped_db = 0
        
        for item in items:
            try:
                link_tag = item.select_one("a.Product__titleLink")
                if not link_tag:
                    continue
                    
                link = link_tag["href"]
                if not link.startswith("http"):
                    link = "https://auctions.yahoo.co.jp" + link
                    
                title = link_tag.get_text(strip=True)
                
                auc_id = link.split("/")[-1].split("?")[0]
                
                # Check local seen_ids first (fastest)
                if auc_id in seen_ids:
                    skipped_seen += 1
                    continue
                
                # Check database for duplicates
                if check_if_auction_exists_in_db(auc_id):
                    skipped_db += 1
                    seen_ids.add(auc_id)  # Add to local cache to avoid future DB checks
                    continue

                is_valid, matched_brand = is_valid_brand_item(title)
                if not is_valid:
                    continue

                price_tag = item.select_one(".Product__priceValue")
                if not price_tag:
                    continue
                    
                price_text = price_tag.text.replace("円", "").replace(",", "").strip()
                try:
                    price = int(price_text)
                except ValueError:
                    continue
                    
                if price > MAX_PRICE_YEN:
                    continue

                usd_price = convert_jpy_to_usd(price)

                is_quality, quality_reason = is_quality_listing(usd_price, matched_brand, title)
                if not is_quality:
                    continue

                img_tag = item.select_one("img")
                img = img_tag["src"] if img_tag and img_tag.has_attr("src") else ""
                if img and not img.startswith("http"):
                    img = "https:" + img if img.startswith("//") else "https://auctions.yahoo.co.jp" + img

                zen_link = f"https://zenmarket.jp/en/auction.aspx?itemCode={auc_id}"
                
                seller_id = extract_seller_info(soup, item)
                deal_quality = calculate_deal_quality(usd_price, matched_brand, title)

                listing_data = {
                    "auction_id": auc_id,
                    "title": title,
                    "price_jpy": price,
                    "price_usd": round(usd_price, 2),
                    "brand": matched_brand,
                    "seller_id": seller_id,
                    "zenmarket_url": zen_link,
                    "yahoo_url": link,
                    "image_url": img,
                    "deal_quality": deal_quality
                }
                
                quality_listings.append(listing_data)

            except Exception as e:
                error_count += 1
                print(f"Error processing item: {e}")
                continue
        
        for listing in quality_listings:
            listing["priority"] = calculate_listing_priority(listing)
        
        quality_listings.sort(key=lambda x: x["priority"], reverse=True)
        limited_listings = quality_listings[:MAX_LISTINGS_PER_BRAND]
        
        print(f"✅ Found {len(quality_listings)} quality items (skipped {skipped_seen} seen, {skipped_db} in DB), showing top {len(limited_listings)} for '{keyword_combo}'")
        
        return limited_listings, error_count
        
    except Exception as e:
        print(f"❌ Error fetching {keyword_combo}: {e}")
        return [], 1

def send_to_discord_bot(auction_data):
    try:
        response = requests.post(DISCORD_BOT_WEBHOOK, json=auction_data, timeout=10)
        if response.status_code == 200:
            print(f"✅ Sent to Discord bot: {auction_data['title'][:50]}...")
            return True
        else:
            print(f"❌ Discord bot failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"❌ Discord bot error: {e}")
        return False

def send_discord_alert_fallback(title, price, link, image, item_id):
    usd_price = convert_jpy_to_usd(price)
    
    embed = {
        "title": title[:100] + "..." if len(title) > 100 else title,
        "url": link,
        "description": f"💴 ¥{price:,} (~${usd_price:.2f} USD)\n[View on ZenMarket]({link})",
        "image": {"url": image} if image else None,
        "color": 0x00ff00 if usd_price < 200 else 0xffa500,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    if not image:
        embed.pop("image", None)
    
    data = {"content": f"🎯 Clothing Find - ${usd_price:.2f}", "embeds": [embed]}
    
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=10)
        if response.status_code in [200, 204]:
            print(f"✅ Discord alert sent: {title[:50]}...")
            return True
        else:
            print(f"❌ Discord alert failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Discord alert error: {e}")
        return False

def get_discord_bot_stats():
    try:
        response = requests.get(DISCORD_BOT_STATS, timeout=5)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None

def main_loop():
    print("🎯 Starting Enhanced Yahoo Japan Sniper...")
    
  # Start health server for Railway
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    print(f"🌐 Health server started on port {os.environ.get('PORT', 8000)}")
    
    print(f"👕 CLOTHING ONLY - Advanced filtering enabled")
    # ... rest of your existing code continues unchanged ...nced filtering enabled")
    print(f"🚫 Excluded brands: {', '.join(COMPLETELY_EXCLUDED_BRANDS)}")
    print(f"💰 Max Price: ¥{MAX_PRICE_YEN:,} (~${convert_jpy_to_usd(MAX_PRICE_YEN):.2f} USD)")
    print(f"🔥 High-resale focus: Enhanced brand detection")
    print(f"💾 Currently tracking {len(seen_ids)} seen items")
    print(f"🤖 Discord Bot Mode: {'Enabled' if USE_DISCORD_BOT else 'Disabled'}")
    
    get_usd_jpy_rate()
    
    if USE_DISCORD_BOT:
        bot_healthy, status = check_discord_bot_health()
        if bot_healthy:
            print("✅ Discord bot is healthy and ready")
        else:
            print(f"⚠️ Discord bot status: {status}")
    
    try:
        iteration = 0
        while True:
            iteration += 1
            start_time = datetime.now()
            print(f"\n🔄 Starting iteration {iteration} at {start_time.strftime('%H:%M:%S')}")
            
            if USE_DISCORD_BOT and iteration % 5 == 0:
                bot_healthy, status = check_discord_bot_health()
                if not bot_healthy:
                    print(f"⚠️ Discord bot health check failed: {status}")
            
            keywords = generate_intelligent_keywords()
            total_found = 0
            quality_filtered = 0
            sent_to_discord = 0
            total_errors = 0
            
            for i, kw in enumerate(keywords, 1):
                print(f"\n🔍 [{i}/{len(keywords)}] Searching: {kw}")
                
                listings, errors = search_yahoo(kw)
                total_found += len(listings)
                total_errors += errors
                
                for listing_data in listings:
                    quality_filtered += 1
                    
                    success = send_to_discord_bot(listing_data) if USE_DISCORD_BOT else send_discord_alert_fallback(
                        listing_data["title"], 
                        listing_data["price_jpy"], 
                        listing_data["zenmarket_url"], 
                        listing_data["image_url"], 
                        listing_data["auction_id"]
                    )
                    
                    if success:
                        seen_ids.add(listing_data["auction_id"])
                        sent_to_discord += 1
                        priority_emoji = "🔥" if listing_data["priority"] >= 100 else "🌟" if listing_data["priority"] >= 70 else "✨"
                        print(f"{priority_emoji} FIND: {listing_data['brand']} - {listing_data['title'][:40]}... - ¥{listing_data['price_jpy']:,} (${listing_data['price_usd']:.2f}) - {listing_data['deal_quality']:.1%} deal")
                    
                    # Small delay between sends to avoid overwhelming the buffer
                    time.sleep(0.5)
                
                time.sleep(3)
            
            save_seen_ids()
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            log_scraper_stats(total_found, quality_filtered, sent_to_discord, total_errors, len(keywords))
            
            print(f"\n📊 Iteration {iteration} Summary:")
            print(f"⏱️  Duration: {duration:.1f}s")
            print(f"🔍 Keywords searched: {len(keywords)}")
            print(f"📊 Total found: {total_found}")
            print(f"✅ Quality filtered: {quality_filtered}")
            print(f"📤 Sent to Discord: {sent_to_discord}")
            print(f"❌ Errors: {total_errors}")
            
            if USE_DISCORD_BOT:
                bot_stats = get_discord_bot_stats()
                if bot_stats:
                    print(f"🤖 Discord Bot: {bot_stats.get('total_listings', 0)} total listings, {bot_stats.get('active_users', 0)} active users")
            
            print(f"⏳ Search cycle complete. Sleeping for 5 minutes...")
            time.sleep(300)
            
    except KeyboardInterrupt:
        save_seen_ids()
        print("✅ Exiting gracefully.")

load_exchange_rate()

if __name__ == "__main__":
    main_loop()
