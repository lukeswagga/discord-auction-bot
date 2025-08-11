import asyncio
import json
import os
import re
import sqlite3
import time
import urllib.parse
from datetime import datetime, timezone, timedelta

import aiohttp
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
from enhanced_filtering import EnhancedSpamDetector, QualityChecker
from flask import Flask
import statistics
import random
import argparse
import threading

scraper_app = Flask(__name__)

@scraper_app.route('/health', methods=['GET'])
def health():
    return {"status": "healthy", "service": "auction-scraper"}, 200

@scraper_app.route('/', methods=['GET'])
def root():
    return {"service": "Yahoo Auction Scraper", "status": "running"}, 200

SCRAPER_HEALTH_PORT = int(os.getenv("SCRAPER_HEALTH_PORT") or os.getenv("PORT", 8000))
health_thread = None

def run_health_server():
    scraper_app.run(host="0.0.0.0", port=SCRAPER_HEALTH_PORT, debug=False)

def start_health_server():
    global health_thread
    if health_thread and health_thread.is_alive():
        return
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    print(f"🌐 Health server started on port {SCRAPER_HEALTH_PORT}")

DISCORD_BOT_WEBHOOK = os.getenv('DISCORD_BOT_WEBHOOK', "http://localhost:8000/webhook")
DISCORD_BOT_HEALTH = os.getenv('DISCORD_BOT_HEALTH', "http://localhost:8000/health") 
DISCORD_BOT_STATS = os.getenv('DISCORD_BOT_STATS', "http://localhost:8000/stats")
USE_DISCORD_BOT = True

MAX_PRICE_YEN = 100000
SEEN_FILE = "seen_yahoo.json"
BRANDS_FILE = "brands.json"
EXCHANGE_RATE_FILE = "exchange_rate.json"
SCRAPER_DB = "auction_tracking.db"

MAX_PRICE_USD = 1500
MIN_PRICE_USD = 2
MAX_LISTINGS_PER_BRAND = 999
ONLY_BUY_IT_NOW = False
PRICE_QUALITY_THRESHOLD = 0.01
ENABLE_RESALE_BOOST = True
ENABLE_INTELLIGENT_FILTERING = True

SORT_ORDERS = [
    "s1=new&o1=d",      
    "s1=cbids&o1=a",    
    "s1=end&o1=a",      
    "s1=bids&o1=d",     
    "s1=featured"       
]

BASE_URL = "https://auctions.yahoo.co.jp/search/search?p={}&n=50&b={}&{}&minPrice=1&maxPrice={}"

exchange_rate_cache = {"rate": 150.0, "timestamp": 0}
JUST_LISTED_INTERVAL = 60
JUST_LISTED_WINDOW = 5

class IntensiveKeywordGenerator:
    def __init__(self):
        self.clothing_categories = [
            "shirt", "jacket", "pants", "hoodie", "coat", "sweater", "tee", 
            "denim", "blazer", "bomber", "cargo", "trench", "vest", "knit",
            "シャツ", "ジャケット", "パンツ", "パーカー", "コート", "Tシャツ", "ニット"
        ]
        
        self.archive_terms = [
            "archive", "rare", "vintage", "fw", "ss", "aw", "runway", 
            "collection", "sample", "prototype", "limited", "exclusive",
            "アーカイブ", "レア", "ヴィンテージ", "限定", "サンプル", "コレクション"
        ]
        
        self.year_seasons = []
        current_year = datetime.now().year
        for year in range(current_year - 15, current_year + 1):
            for season in ["fw", "ss", "aw"]:
                self.year_seasons.append(f"{season}{str(year)[-2:]}")
    
    def generate_comprehensive_keywords(self, brand, brand_variants, cycle_num):
        keywords = []
        
        primary_variant = brand_variants[0] if brand_variants else brand
        
        for variant in brand_variants[:2]:
            keywords.append(variant)
            
            for category in self.clothing_categories:
                keywords.append(f"{variant} {category}")
            
            for term in self.archive_terms[:5]:
                keywords.append(f"{variant} {term}")
            
            season_samples = random.sample(self.year_seasons, min(5, len(self.year_seasons)))
            for season in season_samples:
                keywords.append(f"{variant} {season}")
        
        keywords = [kw for kw in keywords if "femme" not in kw.lower()]
        
        random.shuffle(keywords)
        
        return keywords

class AdaptiveTieredSystem:
    def __init__(self):
        self.tier_config = {
            'tier_1_premium': {
                'brands': ['Raf Simons', 'Rick Owens'],
                'base_keywords': 12,
                'base_pages': 8,
                'search_frequency': 1,
                'delay': 0.5,
                'max_listings': 999,
                'sort_orders': ['new', 'cbids', 'end']
            },
            'tier_1_high': {
                'brands': ['Maison Margiela', 'Jean Paul Gaultier'],
                'base_keywords': 10,
                'base_pages': 6,
                'search_frequency': 1,
                'delay': 0.8,
                'max_listings': 999,
                'sort_orders': ['new', 'cbids']
            },
            'tier_2': {
                'brands': ['Yohji Yamamoto', 'Junya Watanabe', 'Undercover', 'Vetements'],
                'base_keywords': 8,
                'base_pages': 5,
                'search_frequency': 1,
                'delay': 1,
                'max_listings': 999,
                'sort_orders': ['new', 'cbids']
            },
            'tier_3': {
                'brands': ['Comme Des Garcons', 'Martine Rose', 'Balenciaga', 'Alyx'],
                'base_keywords': 7,
                'base_pages': 4,
                'search_frequency': 1,
                'delay': 1.5,
                'max_listings': 999,
                'sort_orders': ['new']
            },
            'tier_4': {
                'brands': ['Celine', 'Bottega Veneta', 'Kiko Kostadinov', 'Chrome Hearts'],
                'base_keywords': 6,
                'base_pages': 3,
                'search_frequency': 2,
                'max_listings': 999,
                'delay': 2,
                'sort_orders': ['new']
            },
            'tier_5_minimal': {
                'brands': ['Prada', 'Miu Miu', 'Hysteric Glamour'],
                'base_keywords': 5,
                'base_pages': 3,
                'search_frequency': 2,
                'delay': 2.5,
                'max_listings': 999,
                'sort_orders': ['new']
            }
        }
        
        self.performance_tracker = {}
        self.iteration_counter = 0
        self.low_volume_cycles = 0
        self.load_performance_data()
    
    def load_performance_data(self):
        try:
            with open('tier_performance.json', 'r') as f:
                self.performance_tracker = json.load(f)
        except FileNotFoundError:
            for tier_name in self.tier_config.keys():
                self.performance_tracker[tier_name] = {
                    'total_searches': 0,
                    'successful_finds': 0,
                    'last_find': None,
                    'avg_efficiency': 0.0
                }
    
    def save_performance_data(self):
        with open('tier_performance.json', 'w') as f:
            json.dump(self.performance_tracker, f, indent=2)
    
    def get_tier_for_brand(self, brand):
        for tier_name, config in self.tier_config.items():
            if brand in config['brands']:
                return tier_name, config
        return 'tier_5_minimal', self.tier_config['tier_5_minimal']
    
    def should_search_tier(self, tier_name):
        frequency = self.tier_config[tier_name]['search_frequency']
        return (self.iteration_counter % frequency) == 0
    
    def get_adaptive_config(self, tier_name, is_low_volume=False):
        config = self.tier_config[tier_name].copy()
        
        if is_low_volume:
            config['max_keywords'] = min(20, config['base_keywords'] * 2)
            config['max_pages'] = min(10, config['base_pages'] * 2)
            config['delay'] = max(0.3, config['delay'] * 0.5)
        else:
            config['max_keywords'] = config['base_keywords']
            config['max_pages'] = config['base_pages']
        
        return config
    
    def update_performance(self, tier_name, searches_made, finds_count):
        if tier_name not in self.performance_tracker:
            self.performance_tracker[tier_name] = {
                'total_searches': 0,
                'successful_finds': 0,
                'last_find': None,
                'avg_efficiency': 0.0
            }
        
        tracker = self.performance_tracker[tier_name]
        tracker['total_searches'] += searches_made
        tracker['successful_finds'] += finds_count
        
        if finds_count > 0:
            tracker['last_find'] = datetime.now().isoformat()
        
        if tracker['total_searches'] > 0:
            tracker['avg_efficiency'] = tracker['successful_finds'] / tracker['total_searches']
    
    def detect_low_volume(self, total_sent):
        if total_sent <= 5:
            self.low_volume_cycles += 1
        else:
            self.low_volume_cycles = 0
        
        return self.low_volume_cycles >= 2
    
    def next_iteration(self):
        self.iteration_counter += 1

class AdaptiveKeywordManager:
    def __init__(self):
        self.keyword_performance = {}
        self.brand_keyword_success = {}
        self.dead_keywords = set()
        self.hot_keywords = set()
        self.revive_threshold = 50
        self.load_keyword_data()
    
    def load_keyword_data(self):
        try:
            with open('keyword_performance.json', 'r') as f:
                data = json.load(f)
                self.keyword_performance = data.get('keyword_performance', {})
                self.brand_keyword_success = data.get('brand_keyword_success', {})
                self.dead_keywords = set(data.get('dead_keywords', []))
                self.hot_keywords = set(data.get('hot_keywords', []))
        except FileNotFoundError:
            pass
    
    def save_keyword_data(self):
        data = {
            'keyword_performance': self.keyword_performance,
            'brand_keyword_success': self.brand_keyword_success,
            'dead_keywords': list(self.dead_keywords),
            'hot_keywords': list(self.hot_keywords),
            'last_updated': datetime.now().isoformat()
        }
        with open('keyword_performance.json', 'w') as f:
            json.dump(data, f, indent=2)
    
    def record_keyword_result(self, keyword, brand, finds_count, search_time):
        if keyword not in self.keyword_performance:
            self.keyword_performance[keyword] = {
                'searches': 0, 'finds': 0, 'avg_time': 0.0, 'consecutive_fails': 0,
                'last_searched': None, 'cycles_dead': 0
            }
        
        perf = self.keyword_performance[keyword]
        perf['searches'] += 1
        perf['finds'] += finds_count
        perf['avg_time'] = (perf['avg_time'] + search_time) / 2
        perf['last_searched'] = datetime.now().isoformat()
        
        if finds_count > 0:
            perf['consecutive_fails'] = 0
            perf['cycles_dead'] = 0
            self.hot_keywords.add(keyword)
            self.dead_keywords.discard(keyword)
            
            if brand not in self.brand_keyword_success:
                self.brand_keyword_success[brand] = {}
            self.brand_keyword_success[brand][keyword] = self.brand_keyword_success[brand].get(keyword, 0) + finds_count
        else:
            perf['consecutive_fails'] += 1
            if perf['consecutive_fails'] >= 15:
                self.dead_keywords.add(keyword)
                self.hot_keywords.discard(keyword)
                perf['cycles_dead'] += 1
    
    def should_revive_keyword(self, keyword):
        if keyword not in self.keyword_performance:
            return True
        
        perf = self.keyword_performance[keyword]
        cycles_dead = perf.get('cycles_dead', 0)
        
        if cycles_dead >= self.revive_threshold:
            perf['cycles_dead'] = 0
            perf['consecutive_fails'] = 0
            self.dead_keywords.discard(keyword)
            return True
        
        return False
    
    def get_best_keywords_for_brand(self, brand, max_count):
        brand_keywords = self.brand_keyword_success.get(brand, {})
        
        active_keywords = []
        for kw, count in brand_keywords.items():
            if kw not in self.dead_keywords or self.should_revive_keyword(kw):
                active_keywords.append((kw, count))
        
        active_keywords.sort(key=lambda x: x[1], reverse=True)
        best_keywords = [kw for kw, count in active_keywords[:max_count]]
        
        if len(best_keywords) < max_count:
            best_keywords.extend(self.generate_fallback_keywords(brand, max_count - len(best_keywords)))
        
        return best_keywords
    
    def generate_fallback_keywords(self, brand, max_count):
        if brand not in BRAND_DATA:
            return [brand.lower()]
        
        brand_info = BRAND_DATA[brand]
        primary_variant = brand_info['variants'][0]
        
        fallback_keywords = [
            primary_variant,
            f"{primary_variant} archive",
            f"{primary_variant} jacket",
            f"{primary_variant} shirt",
            f"{primary_variant} rare",
            f"{primary_variant} vintage",
            f"{primary_variant} fw",
            f"{primary_variant} ss"
        ]
        
        if len(brand_info['variants']) > 1:
            fallback_keywords.append(brand_info['variants'][1])
        
        return fallback_keywords[:max_count]

def load_brand_data():
    try:
        with open(BRANDS_FILE, "r", encoding="utf-8") as f:
            brand_data = json.load(f)
            
        filtered_data = {}
        for brand_key, brand_info in brand_data.items():
            brand_lower = brand_key.lower()
            excluded_brands = {"undercoverism"}
            if not any(excluded in brand_lower for excluded in excluded_brands):
                filtered_data[brand_key] = brand_info
                
        return filtered_data
        
    except FileNotFoundError:
        print(f"❌ {BRANDS_FILE} not found")
        return {}

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

def extract_auction_end_time(soup, item):
    try:
        time_element = item.select_one(".Product__time, .Product__remaining")
        if time_element:
            time_text = time_element.get_text(strip=True)
            
            if "時間" in time_text or "分" in time_text:
                hours = 0
                minutes = 0
                
                hour_match = re.search(r'(\d+)\s*時間', time_text)
                if hour_match:
                    hours = int(hour_match.group(1))
                
                min_match = re.search(r'(\d+)\s*分', time_text)
                if min_match:
                    minutes = int(min_match.group(1))
                
                end_time = datetime.now(timezone.utc) + timedelta(hours=hours, minutes=minutes)
                return end_time.isoformat()
        
        date_element = item.select_one(".Product__endDate")
        if date_element:
            date_text = date_element.get_text(strip=True)
            return None
            
    except Exception:
        pass
    
    return None

def calculate_deal_quality(price_usd, brand, title):
    title_lower = title.lower()
    
    if any(word in title_lower for word in ["tee", "t-shirt", "シャツ", "Tシャツ"]):
        base_price = 40
    elif any(word in title_lower for word in ["shirt", "button", "dress shirt"]):
        base_price = 60
    elif any(word in title_lower for word in ["jacket", "blazer", "ジャケット"]):
        base_price = 120
    elif any(word in title_lower for word in ["coat", "outerwear", "コート"]):
        base_price = 150
    elif any(word in title_lower for word in ["hoodie", "sweatshirt", "パーカー"]):
        base_price = 80
    elif any(word in title_lower for word in ["pants", "trousers", "jeans", "パンツ"]):
        base_price = 80
    else:
        base_price = 60
    
    brand_multipliers = {
        "raf_simons": 3.0,
        "rick_owens": 2.5,
        "maison_margiela": 2.2,
        "jean_paul_gaultier": 2.0,
        "yohji_yamamoto": 1.8,
        "junya_watanabe": 1.6,
        "comme_des_garcons": 1.5,
        "undercover": 1.4,
        "martine_rose": 1.5,
        "miu_miu": 1.3,
        "vetements": 1.4,
        "balenciaga": 1.3,
        "chrome_hearts": 1.4,
        "celine": 1.2,
        "bottega_veneta": 1.2,
        "alyx": 1.3,
        "kiko_kostadinov": 1.3,
        "prada": 1.2,
        "hysteric_glamour": 1.0
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
            print(f"🔥 Archive boost: {keyword} found")
            break
    
    brand_lower = brand.lower() if brand else ""
    
    if "raf" in brand_lower:
        if any(word in title_lower for word in ["tee", "t-shirt", "shirt", "シャツ", "Tシャツ"]):
            boost += 0.4
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

def is_quality_listing(price_usd, brand, title):
    if price_usd < MIN_PRICE_USD or price_usd > MAX_PRICE_USD:
        return False, f"Price outside range"
    
    if not is_clothing_item(title):
        return False, f"Not clothing"
    
    deal_quality = calculate_deal_quality(price_usd, brand, title)
    threshold = PRICE_QUALITY_THRESHOLD
    
    return deal_quality >= threshold, f"Quality: {deal_quality:.1%}"

def is_clothing_item(title):
    title_lower = title.lower()
    
    excluded_items = {
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
        "fred perry", "フレッドペリー", "femme"
    }
    
    for excluded in excluded_items:
        if excluded in title_lower:
            return False
    
    clothing_keywords = {
        "shirt", "tee", "tshirt", "t-shirt", "polo", "button-up", "dress shirt",
        "jacket", "blazer", "coat", "outerwear", "bomber", "varsity", "denim jacket",
        "pants", "trousers", "jeans", "chinos", "slacks", "cargo", "sweatpants",
        "hoodie", "sweatshirt", "pullover", "sweater", "jumper", "cardigan",
        "shorts", "bermuda", "cargo shorts", "denim shorts",
        "tank top", "vest", "camisole", "blouse", "top",
        "シャツ", "Tシャツ", "ポロシャツ", "ブラウス", "トップス",
        "ジャケット", "ブレザー", "コート", "アウター", "ボンバー",
        "パンツ", "ズボン", "ジーンズ", "チノパン", "スラックス",
        "パーカー", "スウェット", "プルオーバー", "セーター", "ニット",
        "ショーツ", "ショートパンツ", "タンクトップ", "ベスト"
    }
    
    for clothing_word in clothing_keywords:
        if clothing_word in title_lower:
            return True
    
    return True

def is_valid_brand_item(title):
    title_lower = title.lower()
    
    if "femme" in title_lower:
        return False, None
    
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
    
    excluded_brands = {"undercoverism", "thrasher", "gap", "adidas", "uniqlo", "gu", "zara", "h&m"}
    for excluded in excluded_brands:
        if excluded.lower() in matched_brand.lower() or excluded.lower() in title_lower:
            return False, None
    
    if not is_clothing_item(title):
        return False, None
    
    return True, matched_brand

def extract_size_info(title):
    title_lower = title.lower()
    
    sizes_found = []
    
    size_patterns = [
        r'\b(xs|s|m|l|xl|xxl|xxxl)\b',
        r'\b(small|medium|large|x-large|xx-large)\b',
        r'\b(44|46|48|50|52|54|56)\b',
        r'\b(サイズ[SML])\b',
        r'size\s*[:：]\s*(\w+)',
    ]
    
    for pattern in size_patterns:
        matches = re.findall(pattern, title_lower)
        sizes_found.extend(matches)
    
    return list(set(sizes_found))

def calculate_listing_priority(listing_data):
    price_usd = listing_data["price_usd"]
    deal_quality = listing_data["deal_quality"]
    title = listing_data["title"].lower()
    brand = listing_data["brand"].lower()
    
    priority = deal_quality * 100
    
    if any(hrb in brand for hrb in ["raf_simons", "rick_owens", "margiela", "martine_rose"]):
        priority += 30
    
    if price_usd <= 100:
        priority += 25
    elif price_usd <= 200:
        priority += 15
    
    if any(word in title for word in ["archive", "rare", "fw", "ss", "アーカイブ", "レア"]):
        priority += 30
    
    if "raf" in brand and any(word in title for word in ["tee", "t-shirt", "shirt"]):
        priority += 25
    
    is_new_listing = listing_data.get("is_new_listing", False)
    if is_new_listing:
        priority += 50
    
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
    try:
        conn = sqlite3.connect(SCRAPER_DB)
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS scraped_items (auction_id TEXT PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
        cursor.execute('SELECT auction_id FROM scraped_items WHERE auction_id = ?', (auction_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    except Exception:
        return False

def add_to_scraper_db(auction_id):
    try:
        conn = sqlite3.connect(SCRAPER_DB)
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS scraped_items (auction_id TEXT PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
        cursor.execute('INSERT OR IGNORE INTO scraped_items (auction_id) VALUES (?)', (auction_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ Could not add to scraper DB: {e}")
        return False

def search_yahoo_multi_page_intensive(keyword_combo, max_pages, brand, keyword_manager, sort_order="new"):
    start_time = time.time()
    all_listings = []
    total_errors = 0
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    quality_checker = QualityChecker()
    
    sort_param = {
        "new": "s1=new&o1=d",
        "cbids": "s1=cbids&o1=a",
        "end": "s1=end&o1=a",
        "bids": "s1=bids&o1=d",
        "featured": "s1=featured"
    }.get(sort_order, "s1=new&o1=d")
    
    for page in range(1, max_pages + 1):
        try:
            encoded_kw = urllib.parse.quote(keyword_combo)
            b_param = (page - 1) * 50 + 1
            url = BASE_URL.format(encoded_kw, b_param, sort_param, MAX_PRICE_YEN)
            
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                print(f"❌ HTTP {resp.status_code} for {keyword_combo} page {page}")
                total_errors += 1
                continue
                
            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.select("li.Product")
            
            if len(items) < 10 and page > 1:
                break
            
            page_listings = []
            page_quality_count = 0
            
            for item in items:
                try:
                    link_tag = item.select_one("a.Product__titleLink")
                    if not link_tag:
                        continue
                        
                    link = link_tag["href"]
                    if not link.startswith("http"):
                        link = "https://auctions.yahoo.co.jp" + link
                        
                    title = link_tag.get_text(strip=True)
                    
                    if "femme" in title.lower():
                        continue
                    
                    auc_id = link.split("/")[-1].split("?")[0]
                    
                    if auc_id in seen_ids:
                        continue
                    
                    if check_if_auction_exists_in_db(auc_id):
                        seen_ids.add(auc_id)
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
                    auction_end_time = extract_auction_end_time(soup, item)
                    sizes = extract_size_info(title)

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
                        "deal_quality": deal_quality,
                        "auction_end_time": auction_end_time,
                        "sizes": sizes,
                        "is_new_listing": (sort_order == "new" and page == 1)
                    }
                    
                    add_to_scraper_db(auc_id)
                    page_listings.append(listing_data)
                    page_quality_count += 1

                except Exception as e:
                    total_errors += 1
                    continue
            
            all_listings.extend(page_listings)
            
            print(f"📄 {sort_order.upper()} Page {page}/{max_pages} for '{keyword_combo}': {page_quality_count} quality items")
            
            if page_quality_count == 0 and page > 2:
                break
            
            if page < max_pages:
                time.sleep(0.5)
                
        except Exception as e:
            print(f"❌ Error fetching page {page} for {keyword_combo}: {e}")
            total_errors += 1
            continue
    
    for listing in all_listings:
        listing["priority"] = calculate_listing_priority(listing)
    
    all_listings.sort(key=lambda x: x["priority"], reverse=True)
    
    search_duration = time.time() - start_time
    if keyword_manager:
        keyword_manager.record_keyword_result(keyword_combo, brand, len(all_listings), search_duration)
    
    return all_listings, total_errors

def send_to_discord_bot(auction_data):
    try:
        print(f"📡 Attempting to send to Discord: {auction_data['title'][:50]}...")
        print(f"   URL: {DISCORD_BOT_WEBHOOK}")
        print(f"   Auction ID: {auction_data['auction_id']}")
        
        response = requests.post(DISCORD_BOT_WEBHOOK, json=auction_data, timeout=10)
        
        print(f"   Response Status: {response.status_code}")
        print(f"   Response Text: {response.text[:200] if response.text else 'No response text'}")
        
        if response.status_code == 200:
            print(f"✅ Successfully sent: {auction_data['title'][:50]}...")
            return True
        else:
            print(f"❌ Discord bot failed with status {response.status_code}")
            print(f"   Full response: {response.text}")
            return False
    except requests.exceptions.ConnectionError as e:
        print(f"❌ Connection error to Discord bot: {e}")
        print(f"   Is the Discord bot running? Check the webhook URL: {DISCORD_BOT_WEBHOOK}")
        return False
    except requests.exceptions.Timeout:
        print(f"❌ Timeout sending to Discord bot (10s exceeded)")
        return False
    except Exception as e:
        print(f"❌ Unexpected error sending to Discord bot: {e}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        return False

async def send_to_discord_bot_async(session, auction_data):
    try:
        async with session.post(DISCORD_BOT_WEBHOOK, json=auction_data, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False

def get_discord_bot_stats():
    try:
        response = requests.get(DISCORD_BOT_STATS, timeout=5)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None

def log_scraper_stats(total_found, quality_filtered, sent_to_discord, errors_count, keywords_searched):
    try:
        conn = sqlite3.connect(SCRAPER_DB)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scraper_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_found INTEGER DEFAULT 0,
                quality_filtered INTEGER DEFAULT 0,
                sent_to_discord INTEGER DEFAULT 0,
                errors_count INTEGER DEFAULT 0,
                keywords_searched INTEGER DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            INSERT INTO scraper_stats
            (total_found, quality_filtered, sent_to_discord, errors_count, keywords_searched)
            VALUES (?, ?, ?, ?, ?)
        ''', (total_found, quality_filtered, sent_to_discord, errors_count, keywords_searched))
        
        conn.commit()
        conn.close()

    except Exception as e:
        print(f"⚠️ Could not log scraper stats: {e}")

async def fetch_listing_start_time(session, url):
    try:
        async with session.get(url, timeout=10) as resp:
            text = await resp.text()
        m = re.search(r"開始日時[^0-9]*(\d{4})年(\d{1,2})月(\d{1,2})日[^0-9]*(\d{1,2})時(\d{1,2})分", text)
        if m:
            y, mo, d, h, mi = map(int, m.groups())
            jp = datetime(y, mo, d, h, mi, tzinfo=timezone(timedelta(hours=9)))
            return jp.astimezone(timezone.utc)
    except Exception:
        return None

async def fetch_recent_listings(session, brand, variants):
    keyword = variants[0] if variants else brand
    url = BASE_URL.format(urllib.parse.quote(keyword), 1, "s1=new&o1=d", MAX_PRICE_YEN)
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with session.get(url, headers=headers, timeout=15) as resp:
            html = await resp.text()
    except Exception:
        return []
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("li.Product")
    prelim = []
    for item in items:
        link_tag = item.select_one("a.Product__titleLink")
        if not link_tag:
            continue
        link = link_tag["href"]
        if not link.startswith("http"):
            link = "https://auctions.yahoo.co.jp" + link
        title = link_tag.get_text(strip=True)
        if "femme" in title.lower():
            continue
        auc_id = link.split("/")[-1].split("?")[0]
        if auc_id in seen_ids or check_if_auction_exists_in_db(auc_id):
            continue
        is_valid, matched_brand = is_valid_brand_item(title)
        if not is_valid:
            continue
        price_tag = item.select_one(".Product__priceValue")
        if not price_tag:
            continue
        try:
            price = int(price_tag.text.replace("円", "").replace(",", ""))
        except ValueError:
            continue
        if price > MAX_PRICE_YEN:
            continue
        usd_price = convert_jpy_to_usd(price)
        ok, _ = is_quality_listing(usd_price, matched_brand, title)
        if not ok:
            continue
        deal_quality = calculate_deal_quality(usd_price, matched_brand, title)
        sizes = extract_size_info(title)
        img_tag = item.select_one("img")
        img = img_tag["src"] if img_tag and img_tag.has_attr("src") else ""
        if img and not img.startswith("http"):
            img = "https:" + img if img.startswith("//") else "https://auctions.yahoo.co.jp" + img
        zen_link = f"https://zenmarket.jp/en/auction.aspx?itemCode={auc_id}"
        prelim.append({
            "auction_id": auc_id,
            "title": title,
            "price_jpy": price,
            "price_usd": round(usd_price, 2),
            "brand": matched_brand,
            "zenmarket_url": zen_link,
            "yahoo_url": link,
            "image_url": img,
            "deal_quality": deal_quality,
            "sizes": sizes
        })
    tasks = [fetch_listing_start_time(session, p["yahoo_url"]) for p in prelim]
    starts = await asyncio.gather(*tasks)
    now = datetime.now(timezone.utc)
    results = []
    for p, st in zip(prelim, starts):
        if not st or now - st > timedelta(minutes=JUST_LISTED_WINDOW):
            continue
        p["target_channel"] = "just-listed"
        p["priority"] = calculate_listing_priority(p)
        results.append(p)
    return results

async def just_listed_loop():
    start_health_server()
    get_usd_jpy_rate()
    while True:
        async with aiohttp.ClientSession() as session:
            tasks = [fetch_recent_listings(session, b, info["variants"]) for b, info in BRAND_DATA.items()]
            groups = await asyncio.gather(*tasks)
            all_listings = [item for group in groups for item in group]
            all_listings.sort(key=lambda x: x.get("priority", 0), reverse=True)
            for listing in all_listings:
                if listing["auction_id"] in seen_ids:
                    continue
                sent = await send_to_discord_bot_async(session, listing)
                if sent:
                    seen_ids.add(listing["auction_id"])
            save_seen_ids()
        await asyncio.sleep(JUST_LISTED_INTERVAL)

def main_loop():
    print("🎯 Starting INTENSIVE Yahoo Japan Sniper with MAXIMUM VOLUME...")

    start_health_server()

    tiered_system = AdaptiveTieredSystem()
    keyword_manager = AdaptiveKeywordManager()
    keyword_generator = IntensiveKeywordGenerator()
    
    print("\n🚀 INTENSIVE SCRAPING SYSTEM:")
    print("✅ Multi-sort order scraping (new, low bids, ending soon)")
    print("✅ Adaptive volume detection")
    print("✅ Keyword revival system")
    print("✅ Size detection for alerts")
    print("✅ Auction end time tracking")
    print("✅ REMOVED femme listings")
    print(f"💰 Price range: ${MIN_PRICE_USD} - ${MAX_PRICE_USD}")
    print(f"⭐ Quality threshold: {PRICE_QUALITY_THRESHOLD:.1%}")
    
    get_usd_jpy_rate()
    
    try:
        while True:
            cycle_start_time = datetime.now()
            tiered_system.next_iteration()
            
            print(f"\n🔄 CYCLE {tiered_system.iteration_counter} - {cycle_start_time.strftime('%H:%M:%S')}")
            
            total_found = 0
            quality_filtered = 0
            sent_to_discord = 0
            total_errors = 0
            total_searches = 0
            
            is_low_volume = tiered_system.detect_low_volume(sent_to_discord)
            
            if is_low_volume:
                print("⚠️ LOW VOLUME DETECTED - INTENSIFYING SEARCH")
            
            # Collect all results from searches
            all_results = []
            
            for tier_name, tier_config_base in tiered_system.tier_config.items():
                if not tiered_system.should_search_tier(tier_name):
                    continue
                
                tier_config = tiered_system.get_adaptive_config(tier_name, is_low_volume)
                
                print(f"\n🎯 Processing {tier_name.upper()} - {len(tier_config_base['brands'])} brands")
                print(f"   Keywords: {tier_config['max_keywords']}, Pages: {tier_config['max_pages']}")
                
                tier_searches = 0
                tier_finds = 0
                
                for brand in tier_config_base['brands']:
                    if brand not in BRAND_DATA:
                        continue
                    
                    brand_info = BRAND_DATA[brand]
                    keywords = keyword_generator.generate_comprehensive_keywords(
                        brand, 
                        brand_info['variants'], 
                        tiered_system.iteration_counter
                    )[:tier_config['max_keywords']]
                    
                    brand_listings = []
                    
                    for keyword in keywords:
                        # Skip dead keywords unless they should be revived
                        if keyword in keyword_manager.dead_keywords and not keyword_manager.should_revive_keyword(keyword):
                            print(f"⏭️ Skipping dead keyword: {keyword}")
                            continue
                        
                        # Search with different sort orders
                        for sort_order in tier_config_base.get('sort_orders', ['new']):
                            print(f"🔍 Searching: {keyword} ({sort_order} - up to {tier_config['max_pages']} pages)")
                            
                            listings, errors = search_yahoo_multi_page_intensive(
                                keyword,
                                tier_config['max_pages'],
                                brand,
                                keyword_manager,
                                sort_order
                            )
                            
                            total_found += len(listings)
                            total_errors += errors
                            total_searches += 1
                            tier_searches += 1
                            
                            if len(listings) > 0:
                                brand_listings.extend(listings)
                                tier_finds += len(listings)
                                print(f"✅ Found {len(listings)} items for {keyword} ({sort_order})")
                            
                            time.sleep(tier_config['delay'])
                    
                    # Sort brand listings by priority and add to results
                    brand_listings.sort(key=lambda x: x["priority"], reverse=True)
                    limited_brand_listings = brand_listings[:tier_config.get('max_listings', 999)]
                    all_results.extend(limited_brand_listings)
                    
                    print(f"📦 Brand {brand}: {len(limited_brand_listings)} items ready to send")
                
                tiered_system.update_performance(tier_name, tier_searches, tier_finds)
                
                if tier_finds > 0:
                    efficiency = tier_finds / max(1, tier_searches)
                    print(f"📊 {tier_name.upper()}: {tier_finds} finds from {tier_searches} searches (efficiency: {efficiency:.2f})")
            
            # Sort all results by priority
            all_results.sort(key=lambda x: x["priority"], reverse=True)
            
            print(f"\n📦 Processing {len(all_results)} total listings for Discord...")
            
            # Send all results to Discord
            for listing_data in all_results:
                quality_filtered += 1
                
                success = send_to_discord_bot(listing_data)
                
                if success:
                    seen_ids.add(listing_data["auction_id"])
                    sent_to_discord += 1
                    
                    priority_emoji = "🔥" if listing_data["priority"] >= 100 else "🌟" if listing_data["priority"] >= 70 else "✨"
                    is_new = "🆕" if listing_data.get("is_new_listing") else ""
                    sizes = f" [{','.join(listing_data.get('sizes', []))}]" if listing_data.get('sizes') else ""
                    
                    print(f"{priority_emoji}{is_new} {listing_data['brand']}: {listing_data['title'][:40]}... - ${listing_data['price_usd']:.2f}{sizes}")
                else:
                    print(f"⚠️ Failed to send: {listing_data['title'][:40]}...")
                
                # Small delay between sends to avoid overwhelming
                time.sleep(0.3)
            
            # Clear seen items periodically
            if tiered_system.iteration_counter % 25 == 0:
                items_before = len(seen_ids)
                print(f"🗑️ CYCLE {tiered_system.iteration_counter}: Clearing {items_before} seen items...")
                seen_ids.clear()
                save_seen_ids()
                
                try:
                    conn = sqlite3.connect(SCRAPER_DB)
                    cursor = conn.cursor()
                    cursor.execute('DELETE FROM scraped_items')
                    conn.commit()
                    conn.close()
                    print(f"✅ Cleared cache for fresh searches")
                except Exception as e:
                    print(f"⚠️ Could not clear database: {e}")
            
            # Save state
            save_seen_ids()
            keyword_manager.save_keyword_data()
            tiered_system.save_performance_data()
            
            cycle_end_time = datetime.now()
            cycle_duration = (cycle_end_time - cycle_start_time).total_seconds()
            
            cycle_efficiency = sent_to_discord / max(1, total_searches)
            
            print(f"\n📊 CYCLE {tiered_system.iteration_counter} SUMMARY:")
            print(f"⏱️  Duration: {cycle_duration:.1f}s")
            print(f"🔍 Total searches: {total_searches}")
            print(f"📊 Raw items found: {total_found}")
            print(f"✅ Quality filtered: {quality_filtered}")
            print(f"📤 Sent to Discord: {sent_to_discord}")
            print(f"❌ Errors: {total_errors}")
            print(f"⚡ Efficiency: {cycle_efficiency:.3f} finds per search")
            
            # Performance insights every 10 cycles
            if tiered_system.iteration_counter % 10 == 0:
                print(f"\n🧠 PERFORMANCE INSIGHTS:")
                
                active_keywords = len([k for k, v in keyword_manager.keyword_performance.items() 
                                     if v.get('consecutive_fails', 0) < 15])
                dead_keywords = len(keyword_manager.dead_keywords)
                hot_keywords = len(keyword_manager.hot_keywords)
                
                print(f"📈 Keywords: {active_keywords} active, {hot_keywords} hot, {dead_keywords} dead")
                
                for tier_name, tracker in tiered_system.performance_tracker.items():
                    if tracker['total_searches'] > 0:
                        print(f"📊 {tier_name.upper()}: {tracker['avg_efficiency']:.2f} avg efficiency")
                
                # Show top performing keywords
                if keyword_manager.hot_keywords:
                    best_keywords = []
                    for kw in list(keyword_manager.hot_keywords)[:5]:
                        perf = keyword_manager.keyword_performance.get(kw, {})
                        if perf.get('searches', 0) > 0:
                            rate = perf['finds'] / perf['searches']
                            best_keywords.append(f"{kw}({rate:.1%})")
                    if best_keywords:
                        print(f"🔥 Top keywords: {best_keywords}")
            
            log_scraper_stats(total_found, quality_filtered, sent_to_discord, total_errors, total_searches)
            
            # Adaptive sleep time
            base_sleep_time = 180
            if cycle_efficiency > 0.2:
                sleep_time = base_sleep_time - 60
                print(f"🚀 High efficiency detected, reducing sleep")
            elif cycle_efficiency < 0.05:
                sleep_time = 60
                print(f"⚠️ Low efficiency, quick retry")
            else:
                sleep_time = base_sleep_time
            
            actual_sleep = max(30, sleep_time - cycle_duration)
            print(f"⏳ Sleeping for {actual_sleep:.0f} seconds...")
            time.sleep(actual_sleep)
            
    except KeyboardInterrupt:
        save_seen_ids()
        keyword_manager.save_keyword_data()
        tiered_system.save_performance_data()
        print("✅ Exiting gracefully.")

load_exchange_rate()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--just-listed", action="store_true")
    args = parser.parse_args()
    if args.just_listed or os.getenv("JUST_LISTED_MODE") == "1":
        asyncio.run(just_listed_loop())
    else:
        main_loop()
