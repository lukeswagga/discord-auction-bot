import discord
from discord.ext import commands
import sqlite3
import re
from datetime import datetime, timezone
import asyncio
from flask import Flask, request, jsonify
import threading
import os
import logging
import time

# Initialize Flask app BEFORE using @app.route
app = Flask(__name__)
start_time = time.time()

# Update the health endpoint to be more Railway-friendly
@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy" if bot.is_ready() and guild else "starting",
        "bot_ready": bot.is_ready(),
        "guild_connected": guild is not None,
        "buffer_size": len(batch_buffer),
        "uptime_seconds": int(time.time() - start_time),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), 200

# === SECURE CONFIG LOADING ===
def load_secure_config():
    """Load sensitive configuration from environment variables ONLY"""
    bot_token = os.getenv('DISCORD_BOT_TOKEN')
    guild_id = os.getenv('GUILD_ID')
    
    if not bot_token:
        print("❌ SECURITY ERROR: DISCORD_BOT_TOKEN environment variable not set!")
        exit(1)
    
    if not guild_id:
        print("❌ SECURITY ERROR: GUILD_ID environment variable not set!")
        exit(1)
    
    if len(bot_token) < 50 or not bot_token.startswith('M'):
        print("❌ SECURITY ERROR: Invalid token format detected!")
        exit(1)
    
    print("✅ SECURITY: Secure configuration loaded from environment variables")
    print("🔒 Token length:", len(bot_token), "characters (hidden for security)")
    
    return {
        'bot_token': bot_token,
        'guild_id': int(guild_id)
    }

try:
    config = load_secure_config()
    BOT_TOKEN = config['bot_token']
    GUILD_ID = config['guild_id']
except Exception as e:
    print(f"❌ SECURITY FAILURE: Could not load secure config: {e}")
    exit(1)

# === CONFIGURATION ===
AUCTION_CATEGORY_NAME = "🎯 AUCTION SNIPES"
AUCTION_CHANNEL_NAME = "🎯-auction-alerts"

batch_buffer = []
BATCH_SIZE = 4
BATCH_TIMEOUT = 30
last_batch_time = None

# === PROXY CONFIGURATION ===
SUPPORTED_PROXIES = {
    "zenmarket": {
        "name": "ZenMarket",
        "emoji": "🛒",
        "url_template": "https://zenmarket.jp/en/auction.aspx?itemCode={auction_id}",
        "description": "Popular proxy service with English support"
    },
    "buyee": {
        "name": "Buyee", 
        "emoji": "📦",
        "url_template": "https://buyee.jp/item/yahoo/auction/{auction_id}",
        "description": "Official partner of Yahoo Auctions"
    },
    "yahoo_japan": {
        "name": "Yahoo Japan Direct",
        "emoji": "🇯🇵", 
        "url_template": "https://page.auctions.yahoo.co.jp/jp/auction/{auction_id}",
        "description": "Direct access (requires Japanese address)"
    }
}

BRAND_CHANNEL_MAP = {
    "Vetements": "vetements",
    "Alyx": "alyx", 
    "Anonymous Club": "anonymous-club",
    "Balenciaga": "balenciaga",
    "Bottega Veneta": "bottega-veneta",
    "Celine": "celine",
    "Chrome Hearts": "chrome-hearts",
    "Comme Des Garcons": "comme-des-garcons",
    "Gosha Rubchinskiy": "gosha-rubchinskiy",
    "Helmut Lang": "helmut-lang",
    "Hood By Air": "hood-by-air",
    "Miu Miu": "miu-miu",
    "Hysteric Glamour": "hysteric-glamour",
    "Junya Watanabe": "junya-watanabe",
    "Kiko Kostadinov": "kiko-kostadinov",
    "Maison Margiela": "maison-margiela",
    "Martine Rose": "martine-rose",
    "Prada": "prada",
    "Raf Simons": "raf-simons",
    "Rick Owens": "rick-owens",
    "Undercover": "undercover",
    "Jean Paul Gaultier": "jean-paul-gaultier"
}

DB_FILE = "auction_tracking.db"

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix='!', intents=intents)

guild = None
auction_channel = None
brand_channels_cache = {}

class UserPreferenceLearner:
    def __init__(self, db_file="auction_tracking.db"):
        self.db_file = db_file
        self.init_learning_tables()
    
    def init_learning_tables(self):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_seller_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                seller_id TEXT,
                likes INTEGER DEFAULT 0,
                dislikes INTEGER DEFAULT 0,
                trust_score REAL DEFAULT 0.5,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, seller_id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_brand_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                brand TEXT,
                likes INTEGER DEFAULT 0,
                dislikes INTEGER DEFAULT 0,
                preference_score REAL DEFAULT 0.5,
                avg_liked_price REAL DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, brand)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_item_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item_category TEXT,
                size_preference TEXT,
                max_price_usd REAL,
                min_quality_score REAL DEFAULT 0.3,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def learn_from_reaction(self, user_id, auction_data, reaction_type):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        try:
            is_positive = (reaction_type == "thumbs_up")
            
            self._update_seller_preference(cursor, user_id, auction_data, is_positive)
            self._update_brand_preference(cursor, user_id, auction_data, is_positive)
            self._update_item_preferences(cursor, user_id, auction_data, is_positive)
            
            conn.commit()
            print(f"🧠 Updated preferences for user {user_id} based on {reaction_type}")
            
        except Exception as e:
            print(f"❌ Error learning from reaction: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def _update_seller_preference(self, cursor, user_id, auction_data, is_positive):
        seller_id = auction_data.get('seller_id', 'unknown')
        
        cursor.execute('''
            INSERT OR IGNORE INTO user_seller_preferences (user_id, seller_id)
            VALUES (?, ?)
        ''', (user_id, seller_id))
        
        if is_positive:
            cursor.execute('''
                UPDATE user_seller_preferences 
                SET likes = likes + 1, last_updated = CURRENT_TIMESTAMP
                WHERE user_id = ? AND seller_id = ?
            ''', (user_id, seller_id))
        else:
            cursor.execute('''
                UPDATE user_seller_preferences 
                SET dislikes = dislikes + 1, last_updated = CURRENT_TIMESTAMP
                WHERE user_id = ? AND seller_id = ?
            ''', (user_id, seller_id))
        
        cursor.execute('''
            SELECT likes, dislikes FROM user_seller_preferences 
            WHERE user_id = ? AND seller_id = ?
        ''', (user_id, seller_id))
        
        likes, dislikes = cursor.fetchone()
        total_reactions = likes + dislikes
        trust_score = likes / total_reactions if total_reactions > 0 else 0.5
        
        cursor.execute('''
            UPDATE user_seller_preferences 
            SET trust_score = ? WHERE user_id = ? AND seller_id = ?
        ''', (trust_score, user_id, seller_id))
    
    def _update_brand_preference(self, cursor, user_id, auction_data, is_positive):
        brand = auction_data.get('brand', '')
        
        cursor.execute('''
            INSERT OR IGNORE INTO user_brand_preferences (user_id, brand)
            VALUES (?, ?)
        ''', (user_id, brand))
        
        if is_positive:
            cursor.execute('''
                UPDATE user_brand_preferences 
                SET likes = likes + 1, last_updated = CURRENT_TIMESTAMP
                WHERE user_id = ? AND brand = ?
            ''', (user_id, brand))
            
            cursor.execute('''
                SELECT avg_liked_price, likes FROM user_brand_preferences 
                WHERE user_id = ? AND brand = ?
            ''', (user_id, brand))
            
            result = cursor.fetchone()
            if result:
                current_avg, likes = result
                new_price = auction_data.get('price_usd', 0)
                new_avg = ((current_avg * (likes - 1)) + new_price) / likes if likes > 0 else new_price
                
                cursor.execute('''
                    UPDATE user_brand_preferences 
                    SET avg_liked_price = ? WHERE user_id = ? AND brand = ?
                ''', (new_avg, user_id, brand))
        else:
            cursor.execute('''
                UPDATE user_brand_preferences 
                SET dislikes = dislikes + 1, last_updated = CURRENT_TIMESTAMP
                WHERE user_id = ? AND brand = ?
            ''', (user_id, brand))
        
        cursor.execute('''
            SELECT likes, dislikes FROM user_brand_preferences 
            WHERE user_id = ? AND brand = ?
        ''', (user_id, brand))
        
        likes, dislikes = cursor.fetchone()
        total_reactions = likes + dislikes
        preference_score = likes / total_reactions if total_reactions > 0 else 0.5
        
        cursor.execute('''
            UPDATE user_brand_preferences 
            SET preference_score = ? WHERE user_id = ? AND brand = ?
        ''', (preference_score, user_id, brand))
    
    def _update_item_preferences(self, cursor, user_id, auction_data, is_positive):
        if is_positive:
            price_usd = auction_data.get('price_usd', 0)
            quality_score = auction_data.get('deal_quality', 0.5)
            
            cursor.execute('''
                INSERT OR REPLACE INTO user_item_preferences 
                (user_id, max_price_usd, min_quality_score)
                VALUES (?, 
                    COALESCE((SELECT MAX(max_price_usd, ?) FROM user_item_preferences WHERE user_id = ?), ?),
                    COALESCE((SELECT MIN(min_quality_score, ?) FROM user_item_preferences WHERE user_id = ?), ?)
                )
            ''', (user_id, price_usd, user_id, price_usd, quality_score, user_id, quality_score))
    
    def should_show_to_user(self, user_id, auction_data):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        try:
            brand = auction_data.get('brand', '')
            seller_id = auction_data.get('seller_id', 'unknown')
            price_usd = auction_data.get('price_usd', 0)
            deal_quality = auction_data.get('deal_quality', 0.5)
            
            cursor.execute('''
                SELECT preference_score FROM user_brand_preferences 
                WHERE user_id = ? AND brand = ?
            ''', (user_id, brand))
            brand_pref = cursor.fetchone()
            brand_score = brand_pref[0] if brand_pref else 0.5
            
            cursor.execute('''
                SELECT trust_score FROM user_seller_preferences 
                WHERE user_id = ? AND seller_id = ?
            ''', (user_id, seller_id))
            seller_pref = cursor.fetchone()
            seller_score = seller_pref[0] if seller_pref else 0.5
            
            cursor.execute('''
                SELECT max_price_usd, min_quality_score FROM user_item_preferences 
                WHERE user_id = ?
            ''', (user_id,))
            item_pref = cursor.fetchone()
            
            if item_pref:
                max_price, min_quality = item_pref
                if price_usd > max_price or deal_quality < min_quality:
                    return False, "Price/quality outside user preferences"
            
            combined_score = (brand_score * 0.4) + (seller_score * 0.3) + (deal_quality * 0.3)
            
            return combined_score >= 0.4, f"Combined score: {combined_score:.2f}"
            
        except Exception as e:
            print(f"❌ Error checking user preferences: {e}")
            return True, "Error checking preferences"
        finally:
            conn.close()
    
    def is_likely_spam(self, title, brand):
        title_lower = title.lower()
        
        LUXURY_SPAM_PATTERNS = {
            "Celine": [
                "レディース", "women", "femme", "ladies",
                "wallet", "財布", "purse", "bag", "バッグ", "ポーチ", "pouch",
                "earring", "pierce", "ピアス", "イヤリング", "ring", "指輪",
                "necklace", "ネックレス", "bracelet", "ブレスレット",
                "perfume", "香水", "fragrance", "cologne", "cosmetic", "化粧品",
                "keychain", "キーホルダー", "sticker", "ステッカー"
            ],
            "Bottega Veneta": [
                "wallet", "財布", "purse", "clutch", "クラッチ",
                "bag", "バッグ", "handbag", "ハンドバッグ", "tote", "トート",
                "pouch", "ポーチ", "case", "ケース",
                "earring", "pierce", "ピアス", "イヤリング", "ring", "指輪",
                "necklace", "ネックレス", "bracelet", "ブレスレット",
                "heel", "ヒール", "pump", "パンプ", "sandal", "サンダル",
                "dress", "ドレス", "skirt", "スカート",
                "perfume", "香水", "fragrance"
            ],
            "Undercover": [
                "cb400sf", "cb1000sf", "cb1300sf", "cb400sb", "cbx400f", "cb750f",
                "vtr250", "ジェイド", "ホーネット", "undercowl", "アンダーカウル",
                "mr2", "bmw", "エンジン", "motorcycle", "engine", "5upj",
                "アンダーカバー", "under cover", "フロント", "リア"
            ],
            "Rick Owens": [
                "ifsixwasnine", "share spirit", "kmrii", "14th addiction", "goa",
                "civarize", "fuga", "tornado mart", "l.g.b", "midas", "ekam"
            ],
            "Chrome Hearts": [
                "luxe", "luxe/r", "luxe r", "ラグジュ", "LUXE/R", "doll bear"
            ]
        }
        
        if brand in LUXURY_SPAM_PATTERNS:
            for pattern in LUXURY_SPAM_PATTERNS[brand]:
                if pattern.lower() in title_lower:
                    print(f"🚫 {brand} spam detected: {pattern}")
                    return True
        
        ARCHIVE_KEYWORDS = [
            "archive", "アーカイブ", "vintage", "ヴィンテージ", "rare", "レア",
            "runway", "ランウェイ", "collection", "コレクション", "fw", "ss",
            "mainline", "メインライン", "homme", "オム"
        ]
        
        for keyword in ARCHIVE_KEYWORDS:
            if keyword.lower() in title_lower:
                print(f"✅ Archive item detected: {keyword} - allowing through")
                return False
        
        generic_spam = ["motorcycle", "engine", "server", "perfume", "香水"]
        
        for pattern in generic_spam:
            if pattern in title_lower:
                return True
        
        return False

preference_learner = None

def init_database():
    global preference_learner
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            auction_id TEXT UNIQUE,
            title TEXT,
            brand TEXT,
            price_jpy INTEGER,
            price_usd REAL,
            seller_id TEXT,
            zenmarket_url TEXT,
            yahoo_url TEXT,
            image_url TEXT,
            deal_quality REAL DEFAULT 0.5,
            priority_score REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            message_id INTEGER
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            auction_id TEXT,
            reaction_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (auction_id) REFERENCES listings (auction_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id INTEGER PRIMARY KEY,
            proxy_service TEXT DEFAULT 'zenmarket',
            setup_complete BOOLEAN DEFAULT FALSE,
            notifications_enabled BOOLEAN DEFAULT TRUE,
            min_quality_threshold REAL DEFAULT 0.3,
            max_price_alert REAL DEFAULT 1000.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
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
    
    conn.commit()
    conn.close()
    print("✅ Database initialized with enhanced schema")
    
    preference_learner = UserPreferenceLearner(DB_FILE)

def add_listing(auction_data, message_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        # First, check if deal_quality column exists, if not add it
        cursor.execute("PRAGMA table_info(listings)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'deal_quality' not in columns:
            cursor.execute('ALTER TABLE listings ADD COLUMN deal_quality REAL DEFAULT 0.5')
            print("✅ Added deal_quality column to listings table")
        
        if 'priority_score' not in columns:
            cursor.execute('ALTER TABLE listings ADD COLUMN priority_score REAL DEFAULT 0.0')
            print("✅ Added priority_score column to listings table")
        
        cursor.execute('''
            INSERT OR REPLACE INTO listings 
            (auction_id, title, brand, price_jpy, price_usd, seller_id, 
             zenmarket_url, yahoo_url, image_url, deal_quality, priority_score, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            auction_data['auction_id'],
            auction_data['title'],
            auction_data['brand'],
            auction_data['price_jpy'],
            auction_data['price_usd'],
            auction_data.get('seller_id', 'unknown'),
            auction_data['zenmarket_url'],
            auction_data.get('yahoo_url', ''),
            auction_data.get('image_url', ''),
            auction_data.get('deal_quality', 0.5),
            auction_data.get('priority', 0.0),
            message_id
        ))
        
        conn.commit()
        return True
    except Exception as e:
        print(f"❌ Database error: {e}")
        return False
    finally:
        conn.close()

def add_reaction(user_id, auction_id, reaction_type):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            DELETE FROM reactions 
            WHERE user_id = ? AND auction_id = ?
        ''', (user_id, auction_id))
        
        cursor.execute('''
            INSERT INTO reactions (user_id, auction_id, reaction_type)
            VALUES (?, ?, ?)
        ''', (user_id, auction_id, reaction_type))
        
        conn.commit()
        return True
        
    except Exception as e:
        print(f"❌ Error adding reaction: {e}")
        return False
    finally:
        conn.close()

def get_user_proxy_preference(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT proxy_service, setup_complete FROM user_preferences 
            WHERE user_id = ?
        ''', (user_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return result[0], result[1]
        else:
            return "zenmarket", False
            
    except sqlite3.OperationalError:
        conn.close()
        return "zenmarket", False

def set_user_proxy_preference(user_id, proxy_service):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO user_preferences 
        (user_id, proxy_service, setup_complete, updated_at)
        VALUES (?, ?, TRUE, CURRENT_TIMESTAMP)
    ''', (user_id, proxy_service))
    
    conn.commit()
    conn.close()

def generate_proxy_url(auction_id, proxy_service):
    if proxy_service not in SUPPORTED_PROXIES:
        proxy_service = "zenmarket"
    
    clean_auction_id = auction_id.replace("yahoo_", "")
    template = SUPPORTED_PROXIES[proxy_service]["url_template"]
    return template.format(auction_id=clean_auction_id)

async def get_or_create_auction_channel():
    global guild, auction_channel
    
    if not guild:
        return None
    
    if auction_channel and auction_channel.guild:
        return auction_channel
    
    for channel in guild.text_channels:
        if channel.name == AUCTION_CHANNEL_NAME:
            auction_channel = channel
            return auction_channel
    
    try:
        category = None
        for cat in guild.categories:
            if cat.name == AUCTION_CATEGORY_NAME:
                category = cat
                break
        
        if not category:
            category = await guild.create_category(AUCTION_CATEGORY_NAME)
        
        auction_channel = await guild.create_text_channel(
            AUCTION_CHANNEL_NAME,
            category=category,
            topic="All auction listings - React with 👍/👎 to help the bot learn!"
        )
        
        return auction_channel
        
    except Exception as e:
        print(f"❌ Error creating auction channel: {e}")
        return None

async def get_or_create_brand_channel(brand_name):
    global guild, brand_channels_cache
    
    if not guild:
        print(f"❌ No guild available for brand channel creation")
        return None
        
    if brand_name not in BRAND_CHANNEL_MAP:
        print(f"❌ Brand '{brand_name}' not in channel map")
        return None
    
    channel_name = BRAND_CHANNEL_MAP[brand_name]
    full_channel_name = f"🏷️-{channel_name}"
    
    print(f"🔍 Looking for channel: {full_channel_name}")
    
    # Check cache first
    if full_channel_name in brand_channels_cache:
        channel = brand_channels_cache[full_channel_name]
        if channel and channel.guild:
            print(f"✅ Found cached channel: {full_channel_name}")
            return channel
    
    # Search for existing channel - check all channels in guild
    for channel in guild.text_channels:
        print(f"🔍 Checking existing channel: '{channel.name}' vs target: '{full_channel_name}'")
        if channel.name == full_channel_name:
            brand_channels_cache[full_channel_name] = channel
            print(f"✅ Found existing channel: {full_channel_name}")
            return channel
    
    # If we get here, the channel doesn't exist, so we'll use the main auction channel
    print(f"⚠️ Channel {full_channel_name} doesn't exist, falling back to main channel")
    return None

async def process_batch_buffer():
    global batch_buffer, last_batch_time
    
    while True:
        await asyncio.sleep(1)  # Check more frequently
        
        if not batch_buffer:
            continue
            
        current_time = datetime.now(timezone.utc)
        buffer_size = len(batch_buffer)
        
        time_since_batch = 0
        if last_batch_time:
            time_since_batch = (current_time - last_batch_time).total_seconds()
        
        # Process immediately when buffer is full OR after timeout
        should_send = (
            buffer_size >= BATCH_SIZE or 
            time_since_batch >= BATCH_TIMEOUT
        )
        
        if should_send:
            # Take exactly BATCH_SIZE items or all remaining items
            items_to_send = batch_buffer[:BATCH_SIZE]
            batch_buffer = batch_buffer[BATCH_SIZE:]  # Remove processed items
            
            last_batch_time = current_time
            
            print(f"📤 Processing {len(items_to_send)} items from buffer (remaining: {len(batch_buffer)})...")
            await send_individual_listings_with_rate_limit(items_to_send)

async def send_single_listing(auction_data):
    try:
        brand = auction_data.get('brand', '')
        title = auction_data.get('title', '')
        
        if preference_learner and preference_learner.is_likely_spam(title, brand):
            print(f"🚫 Blocking spam listing: {title[:50]}...")
            return False
        
        # Debug: Print brand and check mapping
        print(f"🏷️ Processing brand: '{brand}' -> Channel mapping exists: {brand in BRAND_CHANNEL_MAP}")
        
        target_channel = None
        if brand and brand in BRAND_CHANNEL_MAP:
            target_channel = await get_or_create_brand_channel(brand)
            if target_channel:
                print(f"📍 Target brand channel: {target_channel.name}")
            else:
                print(f"❌ Failed to create brand channel for: {brand}")
        else:
            print(f"⚠️ Brand '{brand}' not in channel map or empty")
        
        if not target_channel:
            if not auction_channel:
                target_channel = await get_or_create_auction_channel()
            else:
                target_channel = auction_channel
            print(f"📍 Fallback to main channel: {target_channel.name if target_channel else 'None'}")
        
        if not target_channel:
            print("❌ No target channel available")
            return False
        
        # Check for duplicates (like the original working version)
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT message_id FROM listings WHERE auction_id = ?', (auction_data['auction_id'],))
        existing = cursor.fetchone()
        conn.close()
        
        if existing:
            return False
        
        price_usd = auction_data['price_usd']
        deal_quality = auction_data.get('deal_quality', 0.5)
        priority = auction_data.get('priority', 0.0)
        
        if deal_quality >= 0.8 or priority >= 100:
            color = 0x00ff00
            quality_emoji = "🔥"
        elif deal_quality >= 0.6 or priority >= 70:
            color = 0xffa500
            quality_emoji = "🌟"
        else:
            color = 0xff4444
            quality_emoji = "⭐"
        
        display_title = title
        if len(display_title) > 100:
            display_title = display_title[:97] + "..."
        
        price_jpy = auction_data['price_jpy']
        
        description = f"💴 **¥{price_jpy:,}** (~${price_usd:.2f})\n"
        description += f"🏷️ **{auction_data['brand'].replace('_', ' ').title()}**\n"
        description += f"{quality_emoji} **Quality: {deal_quality:.1%}** | **Priority: {priority:.0f}**\n"
        description += f"👤 **Seller:** {auction_data.get('seller_id', 'unknown')}\n"
        
        auction_id = auction_data['auction_id'].replace('yahoo_', '')
        link_section = "\n**🛒 Proxy Links:**\n"
        for key, proxy_info in SUPPORTED_PROXIES.items():
            proxy_url = generate_proxy_url(auction_id, key)
            link_section += f"{proxy_info['emoji']} [{proxy_info['name']}]({proxy_url})\n"
        
        description += link_section
        
        embed = discord.Embed(
            title=display_title,
            url=auction_data['zenmarket_url'],
            description=description,
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        
        if auction_data.get('image_url'):
            embed.set_thumbnail(url=auction_data['image_url'])
        
        embed.set_footer(text=f"ID: {auction_data['auction_id']} | !setup for proxy config | React 👍/👎 to train")
        
        message = await target_channel.send(embed=embed)
        
        add_listing(auction_data, message.id)
        
        print(f"✅ Sent to #{target_channel.name}: {display_title}")
        return True
        
    except Exception as e:
        print(f"❌ Error sending individual listing: {e}")
        return False

async def send_individual_listings_with_rate_limit(batch_data):
    try:
        for i, auction_data in enumerate(batch_data, 1):
            success = await send_single_listing(auction_data)
            if success:
                print(f"✅ Sent {i}/{len(batch_data)}")
            else:
                print(f"⚠️ Skipped {i}/{len(batch_data)}")
            
            if i < len(batch_data):
                await asyncio.sleep(3)
        
    except Exception as e:
        print(f"❌ Error in rate-limited sending: {e}")

@bot.event
async def on_ready():
    global guild, auction_channel
    print(f'✅ Bot connected as {bot.user}!')
    guild = bot.get_guild(GUILD_ID)
    
    if guild:
        print(f'🎯 Connected to server: {guild.name}')
        auction_channel = await get_or_create_auction_channel()
        
        bot.loop.create_task(process_batch_buffer())
        print("⏰ Started batch buffer processor")
    else:
        print(f'❌ Could not find server with ID: {GUILD_ID}')

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    
    if reaction.message.embeds and len(reaction.message.embeds) > 0:
        embed = reaction.message.embeds[0]
        if embed.title and "Setup" in embed.title:
            await handle_setup_reaction(reaction, user)
            return
    
    if str(reaction.emoji) not in ["👍", "👎"]:
        return
    
    proxy_service, setup_complete = get_user_proxy_preference(user.id)
    if not setup_complete:
        embed = discord.Embed(
            title="⚠️ Setup Required",
            description="Please complete your setup first using `!setup`!",
            color=0xff9900
        )
        dm_channel = await user.create_dm()
        await dm_channel.send(embed=embed)
        return
    
    if not (reaction.message.channel.name == AUCTION_CHANNEL_NAME or 
            reaction.message.channel.name.startswith("🏷️-")):
        return
    
    if not reaction.message.embeds:
        return
    
    embed = reaction.message.embeds[0]
    footer_text = embed.footer.text if embed.footer else ""
    
    auction_id_match = re.search(r'ID: (\w+)', footer_text)
    if not auction_id_match:
        return
    
    auction_id = auction_id_match.group(1)
    reaction_type = "thumbs_up" if str(reaction.emoji) == "👍" else "thumbs_down"
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT title, brand, price_jpy, price_usd, seller_id, yahoo_url, deal_quality
        FROM listings WHERE auction_id = ?
    ''', (auction_id,))
    result = cursor.fetchone()
    
    if result:
        title, brand, price_jpy, price_usd, seller_id, yahoo_url, deal_quality = result
        
        auction_data = {
            'auction_id': auction_id,
            'title': title,
            'brand': brand,
            'price_jpy': price_jpy,
            'price_usd': price_usd,
            'seller_id': seller_id,
            'deal_quality': deal_quality
        }
        
        if preference_learner:
            preference_learner.learn_from_reaction(user.id, auction_data, reaction_type)
        
        add_reaction(user.id, auction_id, reaction_type)
        
        if reaction_type == "thumbs_up":
            await reaction.message.add_reaction("✅")
        else:
            await reaction.message.add_reaction("❌")
        
        print(f"✅ Learned from {user.name}'s {reaction_type} on {brand} item")
    
    conn.close()

async def handle_setup_reaction(reaction, user):
    emoji = str(reaction.emoji)
    
    selected_proxy = None
    for key, proxy in SUPPORTED_PROXIES.items():
        if proxy['emoji'] == emoji:
            selected_proxy = key
            break
    
    if not selected_proxy:
        return
    
    set_user_proxy_preference(user.id, selected_proxy)
    
    proxy_info = SUPPORTED_PROXIES[selected_proxy]
    embed = discord.Embed(
        title="✅ Setup Complete!",
        description=f"Great choice! You've selected **{proxy_info['name']}** {proxy_info['emoji']}",
        color=0x00ff00
    )
    
    embed.add_field(
        name="🎯 What happens now?",
        value=f"All auction listings will now include links formatted for {proxy_info['name']}. You can start reacting to listings with 👍/👎 to train your personal AI!",
        inline=False
    )
    
    dm_channel = await user.create_dm()
    await dm_channel.send(embed=embed)
    
    await reaction.message.channel.send(f"✅ {user.mention} - Setup complete! Check your DMs.", delete_after=10)

@bot.command(name='setup')
async def setup_command(ctx):
    user_id = ctx.author.id
    
    proxy_service, setup_complete = get_user_proxy_preference(user_id)
    
    if setup_complete:
        current_proxy = SUPPORTED_PROXIES[proxy_service]
        embed = discord.Embed(
            title="⚙️ Your Current Setup",
            description=f"You're already set up! Your current proxy service is **{current_proxy['name']}** {current_proxy['emoji']}",
            color=0x00ff00
        )
        await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        title="🎯 Welcome to Auction Sniper Setup!",
        description="Let's get you set up to receive auction listings. First, I need to know which proxy service you use to buy from Yahoo Auctions Japan.",
        color=0x0099ff
    )
    
    proxy_options = ""
    for key, proxy in SUPPORTED_PROXIES.items():
        proxy_options += f"{proxy['emoji']} **{proxy['name']}**\n{proxy['description']}\n\n"
    
    embed.add_field(
        name="📋 Available Proxy Services",
        value=proxy_options,
        inline=False
    )
    
    embed.add_field(
        name="🎮 How to choose:",
        value="React with the emoji below that matches your proxy service!",
        inline=False
    )
    
    message = await ctx.send(embed=embed)
    
    for proxy in SUPPORTED_PROXIES.values():
        await message.add_reaction(proxy['emoji'])

@bot.command(name='test')
async def test_command(ctx):
    await ctx.send("✅ Bot is working!")

@bot.command(name='stats')
async def stats_command(ctx):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN reaction_type = 'thumbs_up' THEN 1 ELSE 0 END) as thumbs_up,
            SUM(CASE WHEN reaction_type = 'thumbs_down' THEN 1 ELSE 0 END) as thumbs_down
        FROM reactions 
        WHERE user_id = ?
    ''', (ctx.author.id,))
    
    stats = cursor.fetchone()
    total, thumbs_up, thumbs_down = stats[0], stats[1] or 0, stats[2] or 0
    
    cursor.execute('''
        SELECT brand, preference_score FROM user_brand_preferences 
        WHERE user_id = ? ORDER BY preference_score DESC LIMIT 3
    ''', (ctx.author.id,))
    top_brands = cursor.fetchall()
    
    embed = discord.Embed(
        title=f"📊 Stats for {ctx.author.display_name}",
        color=0x0099ff
    )
    
    embed.add_field(
        name="📈 Reaction Summary", 
        value=f"Total: {total}\n👍 Likes: {thumbs_up}\n👎 Dislikes: {thumbs_down}",
        inline=True
    )
    
    if total > 0:
        positivity = thumbs_up / total * 100
        embed.add_field(
            name="🎯 Positivity Rate",
            value=f"{positivity:.1f}%",
            inline=True
        )
    
    if top_brands:
        brand_text = "\n".join([f"{brand.replace('_', ' ').title()}: {score:.1%}" for brand, score in top_brands])
        embed.add_field(
            name="🏷️ Top Preferred Brands",
            value=brand_text,
            inline=False
        )
    
    conn.close()
    await ctx.send(embed=embed)

@bot.command(name='preferences')
async def preferences_command(ctx):
    user_id = ctx.author.id
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT proxy_service, notifications_enabled, min_quality_threshold, max_price_alert 
        FROM user_preferences WHERE user_id = ?
    ''', (user_id,))
    
    prefs = cursor.fetchone()
    if not prefs:
        await ctx.send("❌ No preferences found. Run `!setup` first!")
        return
    
    proxy_service, notifications, min_quality, max_price = prefs
    proxy_info = SUPPORTED_PROXIES.get(proxy_service, {"name": "Unknown", "emoji": "❓"})
    
    embed = discord.Embed(
        title="⚙️ Your Preferences",
        color=0x0099ff
    )
    
    embed.add_field(
        name="🛒 Proxy Service",
        value=f"{proxy_info['emoji']} {proxy_info['name']}",
        inline=True
    )
    
    embed.add_field(
        name="🔔 Notifications",
        value="✅ Enabled" if notifications else "❌ Disabled",
        inline=True
    )
    
    embed.add_field(
        name="⭐ Min Quality",
        value=f"{min_quality:.1%}",
        inline=True
    )
    
    embed.add_field(
        name="💰 Max Price Alert",
        value=f"${max_price:.0f}",
        inline=True
    )
    
    conn.close()
    await ctx.send(embed=embed)

@bot.command(name='export')
async def export_command(ctx):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT r.reaction_type, r.created_at, l.title, l.brand, l.price_jpy, 
               l.price_usd, l.seller_id, l.zenmarket_url, l.yahoo_url, l.auction_id,
               l.deal_quality, l.priority_score
        FROM reactions r
        JOIN listings l ON r.auction_id = l.auction_id
        WHERE r.user_id = ?
        ORDER BY r.created_at DESC
    ''', (ctx.author.id,))
    
    all_reactions = cursor.fetchall()
    conn.close()
    
    if not all_reactions:
        await ctx.send("❌ No reactions found!")
        return
    
    export_text = f"# {ctx.author.display_name}'s Auction Reactions Export\n"
    export_text += f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
    export_text += f"# Total Reactions: {len(all_reactions)}\n\n"
    
    liked_count = sum(1 for r in all_reactions if r[0] == 'thumbs_up')
    disliked_count = len(all_reactions) - liked_count
    
    export_text += f"## Summary\n"
    export_text += f"👍 Liked: {liked_count}\n"
    export_text += f"👎 Disliked: {disliked_count}\n"
    export_text += f"Positivity Rate: {liked_count/len(all_reactions)*100:.1f}%\n\n"
    
    for reaction_type in ['thumbs_up', 'thumbs_down']:
        emoji = "👍 LIKED" if reaction_type == 'thumbs_up' else "👎 DISLIKED"
        export_text += f"## {emoji} LISTINGS\n\n"
        
        filtered_reactions = [r for r in all_reactions if r[0] == reaction_type]
        
        for i, (_, created_at, title, brand, price_jpy, price_usd, seller_id, zenmarket_url, yahoo_url, auction_id, deal_quality, priority) in enumerate(filtered_reactions, 1):
            export_text += f"{i}. **{title}**\n"
            export_text += f"   Brand: {brand.replace('_', ' ').title()}\n"
            export_text += f"   Price: ¥{price_jpy:,} (~${price_usd:.2f})\n"
            export_text += f"   Quality: {deal_quality:.1%} | Priority: {priority:.0f}\n"
            export_text += f"   Seller: {seller_id}\n"
            export_text += f"   Date: {created_at}\n"
            export_text += f"   ZenMarket: {zenmarket_url}\n"
            export_text += f"   Yahoo: {yahoo_url}\n\n"
    
    filename = f"auction_reactions_{ctx.author.id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.txt"
    
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(export_text)
        
        with open(filename, 'rb') as f:
            file = discord.File(f, filename)
            embed = discord.Embed(
                title="📋 Your Complete Reaction Export",
                description=f"**Total Reactions:** {len(all_reactions)}\n**Liked:** {liked_count}\n**Disliked:** {disliked_count}",
                color=0x0099ff
            )
            await ctx.send(embed=embed, file=file)
        
        os.remove(filename)
        
    except Exception as e:
        await ctx.send(f"❌ Error creating export file: {e}")

@bot.command(name='scraper_stats')
async def scraper_stats_command(ctx):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT timestamp, total_found, quality_filtered, sent_to_discord, errors_count, keywords_searched
        FROM scraper_stats 
        ORDER BY timestamp DESC 
        LIMIT 5
    ''', )
    
    recent_stats = cursor.fetchall()
    
    if not recent_stats:
        await ctx.send("❌ No scraper statistics found!")
        return
    
    embed = discord.Embed(
        title="🤖 Recent Scraper Statistics",
        color=0x0099ff
    )
    
    for i, (timestamp, total_found, quality_filtered, sent_to_discord, errors_count, keywords_searched) in enumerate(recent_stats, 1):
        success_rate = (sent_to_discord / total_found * 100) if total_found > 0 else 0
        
        embed.add_field(
            name=f"Run #{i} - {timestamp}",
            value=f"🔍 Keywords: {keywords_searched}\n📊 Found: {total_found}\n✅ Quality: {quality_filtered}\n📤 Sent: {sent_to_discord}\n❌ Errors: {errors_count}\n📈 Success: {success_rate:.1f}%",
            inline=True
        )
    
    conn.close()
    await ctx.send(embed=embed)

@bot.command(name='commands')
async def commands_command(ctx):
    embed = discord.Embed(
        title="🤖 Auction Bot Commands",
        description="All available commands for the auction tracking bot",
        color=0x0099ff
    )
    
    embed.add_field(
        name="⚙️ Setup & Configuration",
        value="**!setup** - Initial setup for new users\n**!preferences** - View your current preferences",
        inline=False
    )
    
    embed.add_field(
        name="📊 Statistics & Data",
        value="**!stats** - Your reaction statistics\n**!scraper_stats** - Recent scraper performance\n**!export** - Export your reaction data",
        inline=False
    )
    
    embed.add_field(
        name="🧠 Bot Testing",
        value="**!test** - Test if bot is working\n**!commands** - Show this help",
        inline=False
    )
    
    embed.set_footer(text="New users: Start with !setup | React with 👍/👎 to auction listings to train the bot!")
    
    await ctx.send(embed=embed)

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    global batch_buffer, last_batch_time
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data"}), 400
        
        required_fields = ['auction_id', 'title', 'brand', 'price_jpy', 'price_usd', 'zenmarket_url']
        if not all(field in data for field in required_fields):
            return jsonify({"error": "Missing required fields"}), 400
        
        # Add to buffer - scraper already checked for duplicates
        batch_buffer.append(data)
        
        if len(batch_buffer) == 1:
            last_batch_time = datetime.now(timezone.utc)
        
        print(f"📥 Added to buffer: {data['title'][:30]}... (Buffer: {len(batch_buffer)}/4)")
        
        # If buffer is full, the processor will handle it within 1 second
        
        return jsonify({
            "status": "queued",
            "buffer_size": len(batch_buffer),
            "auction_id": data['auction_id']
        }), 200
        
    except Exception as e:
        print(f"❌ Webhook error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "bot_ready": bot.is_ready(),
        "guild_connected": guild is not None,
        "buffer_size": len(batch_buffer),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), 200

@app.route('/stats', methods=['GET'])
def api_stats():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM listings')
    total_listings = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM reactions')
    total_reactions = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(DISTINCT user_id) FROM user_preferences WHERE setup_complete = 1')
    active_users = cursor.fetchone()[0]
    
    conn.close()
    
    return jsonify({
        "total_listings": total_listings,
        "total_reactions": total_reactions,
        "active_users": active_users,
        "buffer_size": len(batch_buffer)
    }), 200

def run_flask():
    app.run(host='0.0.0.0', port=8000, debug=False)

def main():
    print("🔧 Initializing database...")
    init_database()
    
    print("🔒 SECURITY: Performing startup security checks...")
    
    if not BOT_TOKEN or len(BOT_TOKEN) < 50:
        print("❌ SECURITY FAILURE: Invalid bot token!")
        return
    
    if not GUILD_ID or GUILD_ID == 1234567890:
        print("❌ SECURITY FAILURE: Invalid guild ID!")
        return
    
    print("✅ SECURITY: All security checks passed")
    print(f"🎯 Target server ID: {GUILD_ID}")
    print(f"📺 Main auction channel: #{AUCTION_CHANNEL_NAME}")
    print(f"📦 Batch size: {BATCH_SIZE} listings per message")
    print(f"🧠 AI learning system: Enabled")
    
    print("🌐 Starting webhook server...")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("🌐 Webhook server started on port 8000")
    
    print("🤖 Connecting to Discord...")
    try:
        bot.run(BOT_TOKEN)
    except discord.errors.LoginFailure:
        print("❌ SECURITY FAILURE: Invalid bot token - login failed!")
    except Exception as e:
        print(f"❌ Error starting bot: {e}")

if __name__ == "__main__":
    main()
