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

BOOKMARK_CATEGORY_NAME = "📚 BOOKMARKS"
user_bookmark_channels = {}

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
    
    print("🔧 Initializing database...")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Create listings table
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
    print("✅ Created listings table")
    
    # Create reactions table
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
    print("✅ Created reactions table")
    
    # Create user_preferences table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id INTEGER PRIMARY KEY,
            proxy_service TEXT DEFAULT 'zenmarket',
            setup_complete BOOLEAN DEFAULT FALSE,
            notifications_enabled BOOLEAN DEFAULT TRUE,
            min_quality_threshold REAL DEFAULT 0.3,
            max_price_alert REAL DEFAULT 1000.0,
            bookmark_method TEXT DEFAULT 'private_channel',
            auto_bookmark_likes BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    print("✅ Created user_preferences table")
    
    # Create user_bookmarks table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            auction_id TEXT,
            bookmark_message_id INTEGER,
            bookmark_channel_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (auction_id) REFERENCES listings (auction_id),
            UNIQUE(user_id, auction_id)
        )
    ''')
    print("✅ Created user_bookmarks table")
    
    # Create scraper_stats table
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
    print("✅ Created scraper_stats table")
    
    # Create preference learning tables
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
    print("✅ Created user_seller_preferences table")
    
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
    print("✅ Created user_brand_preferences table")
    
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
    print("✅ Created user_item_preferences table")
    
    # Check if we need to add new columns to existing tables
    try:
        cursor.execute("PRAGMA table_info(user_preferences)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'bookmark_method' not in columns:
            cursor.execute('ALTER TABLE user_preferences ADD COLUMN bookmark_method TEXT DEFAULT "private_channel"')
            print("✅ Added bookmark_method column")
        
        if 'auto_bookmark_likes' not in columns:
            cursor.execute('ALTER TABLE user_preferences ADD COLUMN auto_bookmark_likes BOOLEAN DEFAULT TRUE')
            print("✅ Added auto_bookmark_likes column")
            
    except Exception as e:
        print(f"⚠️ Column addition warning: {e}")
    
    conn.commit()
    conn.close()
    print("✅ Database initialization complete")
    
    # Initialize preference learner
    preference_learner = UserPreferenceLearner(DB_FILE)
    print("✅ Preference learner initialized")


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


# ADD THESE FUNCTIONS AFTER YOUR EXISTING FUNCTIONS BUT BEFORE @bot.command DEFINITIONS

async def get_or_create_user_bookmark_channel(user):
    """Get or create a private bookmark channel for the user"""
    global guild, user_bookmark_channels
    
    if not guild:
        return None
    
    user_id = user.id
    channel_name = f"📚-{user.name.lower().replace(' ', '-')}-bookmarks"
    
    # Check cache first
    if user_id in user_bookmark_channels:
        channel = user_bookmark_channels[user_id]
        if channel and channel.guild:
            return channel
    
    # Look for existing channel
    for channel in guild.text_channels:
        if channel.name == channel_name:
            user_bookmark_channels[user_id] = channel
            return channel
    
    try:
        # Find or create bookmark category
        category = None
        for cat in guild.categories:
            if cat.name == BOOKMARK_CATEGORY_NAME:
                category = cat
                break
        
        if not category:
            # Create category with restricted permissions
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            category = await guild.create_category(BOOKMARK_CATEGORY_NAME, overwrites=overwrites)
        
        # Create private channel only visible to the user and bot
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, add_reactions=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        bookmark_channel = await guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"Private bookmarks for {user.display_name} - Your liked auction listings"
        )
        
        # Send welcome message
        welcome_embed = discord.Embed(
            title="📚 Your Personal Bookmark Channel!",
            description=f"Welcome {user.mention}! This is your private bookmark channel where all your liked auction listings will be saved.",
            color=0x00ff00
        )
        welcome_embed.add_field(
            name="🎯 How it works:",
            value="• React with 👍 to any auction listing\n• It will automatically appear here\n• Use reactions to organize: ⭐ (priority), ❤️ (love), 🔥 (must-have)\n• Use `!bookmark_settings` to customize",
            inline=False
        )
        
        await bookmark_channel.send(embed=welcome_embed)
        
        user_bookmark_channels[user_id] = bookmark_channel
        return bookmark_channel
        
    except Exception as e:
        print(f"❌ Error creating bookmark channel for {user.name}: {e}")
        return None

async def send_bookmark_to_user(user, auction_data, original_message):
    """Send a bookmarked listing to user's preferred location"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        # Get user's bookmark preferences
        cursor.execute('SELECT bookmark_method, auto_bookmark_likes FROM user_preferences WHERE user_id = ?', (user.id,))
        prefs = cursor.fetchone()
        
        bookmark_method = prefs[0] if prefs and prefs[0] else "private_channel"
        auto_bookmark = prefs[1] if prefs and prefs[1] is not None else True
        
        if not auto_bookmark:
            return
        
        # Check if already bookmarked
        cursor.execute('SELECT id FROM user_bookmarks WHERE user_id = ? AND auction_id = ?', 
                      (user.id, auction_data['auction_id']))
        if cursor.fetchone():
            return  # Already bookmarked
        
        bookmark_message = None
        bookmark_channel_id = None
        
        if bookmark_method == "dm":
            # Send to DM
            try:
                dm_channel = await user.create_dm()
                bookmark_message = await send_bookmark_message(dm_channel, auction_data, user)
                bookmark_channel_id = dm_channel.id
            except discord.Forbidden:
                # Fallback to private channel if DMs are disabled
                bookmark_method = "private_channel"
        
        if bookmark_method == "private_channel":
            # Send to private bookmark channel
            bookmark_channel = await get_or_create_user_bookmark_channel(user)
            if bookmark_channel:
                bookmark_message = await send_bookmark_message(bookmark_channel, auction_data, user)
                bookmark_channel_id = bookmark_channel.id
        
        if bookmark_message:
            # Save bookmark to database
            cursor.execute('''
                INSERT OR REPLACE INTO user_bookmarks 
                (user_id, auction_id, bookmark_message_id, bookmark_channel_id)
                VALUES (?, ?, ?, ?)
            ''', (user.id, auction_data['auction_id'], bookmark_message.id, bookmark_channel_id))
            
            conn.commit()
            print(f"📚 Bookmarked {auction_data['brand']} item for {user.name}")
            
            # Add some useful reactions for organization
            await bookmark_message.add_reaction("⭐")  # Priority
            await bookmark_message.add_reaction("❤️")  # Love it
            await bookmark_message.add_reaction("🔥")  # Must have
            await bookmark_message.add_reaction("💰")  # Good price
            await bookmark_message.add_reaction("🗑️")  # Remove bookmark
    
    except Exception as e:
        print(f"❌ Error bookmarking for {user.name}: {e}")
    finally:
        conn.close()

async def send_bookmark_message(channel, auction_data, user):
    """Create and send the bookmark message"""
    price_usd = auction_data['price_usd']
    deal_quality = auction_data.get('deal_quality', 0.5)
    priority = auction_data.get('priority', 0.0)
    
    # Enhanced embed for bookmarks
    embed = discord.Embed(
        title=f"📚 {auction_data['title'][:80]}{'...' if len(auction_data['title']) > 80 else ''}",
        url=auction_data['zenmarket_url'],
        color=0x00ff00,
        timestamp=datetime.now(timezone.utc)
    )
    
    # Price and brand info
    embed.add_field(
        name="💰 Price",
        value=f"¥{auction_data['price_jpy']:,}\n~${price_usd:.2f} USD",
        inline=True
    )
    
    embed.add_field(
        name="🏷️ Brand",
        value=auction_data['brand'].replace('_', ' ').title(),
        inline=True
    )
    
    embed.add_field(
        name="⭐ Quality Score",
        value=f"{deal_quality:.1%}",
        inline=True
    )
    
    # Seller info
    embed.add_field(
        name="👤 Seller",
        value=auction_data.get('seller_id', 'Unknown'),
        inline=True
    )
    
    # Priority score
    if priority > 0:
        embed.add_field(
            name="🔥 Priority",
            value=f"{priority:.0f}",
            inline=True
        )
    
    # Bookmarked timestamp
    embed.add_field(
        name="📅 Bookmarked",
        value=f"<t:{int(datetime.now().timestamp())}:R>",
        inline=True
    )
    
    # Proxy links
    auction_id = auction_data['auction_id'].replace('yahoo_', '')
    proxy_links = []
    for key, proxy_info in SUPPORTED_PROXIES.items():
        proxy_url = generate_proxy_url(auction_id, key)
        proxy_links.append(f"{proxy_info['emoji']} [{proxy_info['name']}]({proxy_url})")
    
    embed.add_field(
        name="🛒 Purchase Links",
        value="\n".join(proxy_links),
        inline=False
    )
    
    if auction_data.get('image_url'):
        embed.set_thumbnail(url=auction_data['image_url'])
    
    embed.set_footer(text=f"Auction ID: {auction_data['auction_id']} | React to organize your bookmarks")
    
    return await channel.send(embed=embed)

async def handle_bookmark_reaction(reaction, user):
    """Handle reactions on bookmark messages"""
    if str(reaction.emoji) == "🗑️":
        # Remove bookmark
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            
            cursor.execute('DELETE FROM user_bookmarks WHERE user_id = ? AND bookmark_message_id = ?',
                          (user.id, reaction.message.id))
            
            if cursor.rowcount > 0:
                await reaction.message.delete()
                conn.commit()
                print(f"🗑️ Removed bookmark for {user.name}")
            
            conn.close()
            
        except Exception as e:
            print(f"❌ Error removing bookmark: {e}")


# ADD THESE FUNCTIONS BEFORE YOUR @bot.command DEFINITIONS
# (Find where you have functions like add_listing, get_user_proxy_preference, etc.)

async def get_or_create_user_bookmark_channel(user):
    """Get or create a private bookmark channel for the user"""
    global guild, user_bookmark_channels
    
    if not guild:
        return None
    
    user_id = user.id
    # Make channel name safe for Discord
    safe_username = ''.join(c for c in user.name.lower() if c.isalnum() or c in '-_')[:20]
    channel_name = f"📚-{safe_username}-bookmarks"
    
    # Check cache first
    if user_id in user_bookmark_channels:
        channel = user_bookmark_channels[user_id]
        if channel and channel.guild:
            return channel
    
    # Look for existing channel
    for channel in guild.text_channels:
        if channel.name == channel_name:
            user_bookmark_channels[user_id] = channel
            return channel
    
    try:
        # Find or create bookmark category
        category = None
        for cat in guild.categories:
            if cat.name == BOOKMARK_CATEGORY_NAME:
                category = cat
                break
        
        if not category:
            # Create category with restricted permissions
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            category = await guild.create_category(BOOKMARK_CATEGORY_NAME, overwrites=overwrites)
            print(f"✅ Created bookmark category: {BOOKMARK_CATEGORY_NAME}")
        
        # Create private channel only visible to the user and bot
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, add_reactions=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        bookmark_channel = await guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"Private bookmarks for {user.display_name} - Your liked auction listings"
        )
        
        # Send welcome message
        welcome_embed = discord.Embed(
            title="📚 Your Personal Bookmark Channel!",
            description=f"Welcome {user.mention}! This is your private bookmark channel where all your liked auction listings will be saved.",
            color=0x00ff00
        )
        welcome_embed.add_field(
            name="🎯 How it works:",
            value="• React with 👍 to any auction listing\n• It will automatically appear here\n• Use reactions to organize: ⭐ (priority), ❤️ (love), 🔥 (must-have)\n• React with 🗑️ to remove bookmarks",
            inline=False
        )
        
        await bookmark_channel.send(embed=welcome_embed)
        
        user_bookmark_channels[user_id] = bookmark_channel
        print(f"✅ Created bookmark channel for {user.name}: {channel_name}")
        return bookmark_channel
        
    except Exception as e:
        print(f"❌ Error creating bookmark channel for {user.name}: {e}")
        return None

async def send_bookmark_message(channel, auction_data, user):
    """Create and send the bookmark message"""
    try:
        price_usd = auction_data['price_usd']
        deal_quality = auction_data.get('deal_quality', 0.5)
        priority = auction_data.get('priority', 0.0)
        
        # Enhanced embed for bookmarks
        embed = discord.Embed(
            title=f"📚 {auction_data['title'][:80]}{'...' if len(auction_data['title']) > 80 else ''}",
            url=auction_data.get('zenmarket_url', ''),
            color=0x00ff00,
            timestamp=datetime.now(timezone.utc)
        )
        
        # Price and brand info
        embed.add_field(
            name="💰 Price",
            value=f"¥{auction_data['price_jpy']:,}\n~${price_usd:.2f} USD",
            inline=True
        )
        
        embed.add_field(
            name="🏷️ Brand",
            value=auction_data['brand'].replace('_', ' ').title(),
            inline=True
        )
        
        embed.add_field(
            name="⭐ Quality Score",
            value=f"{deal_quality:.1%}",
            inline=True
        )
        
        # Seller info
        embed.add_field(
            name="👤 Seller",
            value=auction_data.get('seller_id', 'Unknown'),
            inline=True
        )
        
        # Priority score
        if priority > 0:
            embed.add_field(
                name="🔥 Priority",
                value=f"{priority:.0f}",
                inline=True
            )
        
        # Bookmarked timestamp
        embed.add_field(
            name="📅 Bookmarked",
            value=f"<t:{int(datetime.now().timestamp())}:R>",
            inline=True
        )
        
        # Proxy links
        auction_id = auction_data['auction_id'].replace('yahoo_', '')
        proxy_links = []
        for key, proxy_info in SUPPORTED_PROXIES.items():
            proxy_url = generate_proxy_url(auction_id, key)
            proxy_links.append(f"{proxy_info['emoji']} [{proxy_info['name']}]({proxy_url})")
        
        embed.add_field(
            name="🛒 Purchase Links",
            value="\n".join(proxy_links),
            inline=False
        )
        
        if auction_data.get('image_url'):
            embed.set_thumbnail(url=auction_data['image_url'])
        
        embed.set_footer(text=f"Auction ID: {auction_data['auction_id']} | React 🗑️ to remove")
        
        message = await channel.send(embed=embed)
        
        # Add organization reactions
        await message.add_reaction("⭐")  # Priority
        await message.add_reaction("❤️")  # Love it
        await message.add_reaction("🔥")  # Must have
        await message.add_reaction("💰")  # Good price
        await message.add_reaction("🗑️")  # Remove bookmark
        
        return message
        
    except Exception as e:
        print(f"❌ Error creating bookmark message: {e}")
        return None

async def save_bookmark_to_user(user, auction_data):
    """Save a bookmark when user likes an item"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Get user's bookmark preferences
        cursor.execute('SELECT bookmark_method, auto_bookmark_likes FROM user_preferences WHERE user_id = ?', (user.id,))
        prefs = cursor.fetchone()
        
        if not prefs:
            print(f"⚠️ No preferences found for user {user.name}")
            conn.close()
            return
        
        bookmark_method = prefs[0] if prefs[0] else "private_channel"
        auto_bookmark = prefs[1] if prefs[1] is not None else True
        
        if not auto_bookmark:
            print(f"📚 Auto-bookmark disabled for {user.name}")
            conn.close()
            return
        
        # Check if already bookmarked
        cursor.execute('SELECT id FROM user_bookmarks WHERE user_id = ? AND auction_id = ?', 
                      (user.id, auction_data['auction_id']))
        if cursor.fetchone():
            print(f"📚 Already bookmarked for {user.name}")
            conn.close()
            return
        
        bookmark_message = None
        bookmark_channel_id = None
        
        if bookmark_method == "dm":
            # Send to DM
            try:
                dm_channel = await user.create_dm()
                bookmark_message = await send_bookmark_message(dm_channel, auction_data, user)
                bookmark_channel_id = dm_channel.id
                print(f"📚 Sent bookmark DM to {user.name}")
            except discord.Forbidden:
                # Fallback to private channel if DMs are disabled
                print(f"⚠️ DMs disabled for {user.name}, falling back to private channel")
                bookmark_method = "private_channel"
        
        if bookmark_method == "private_channel":
            # Send to private bookmark channel
            bookmark_channel = await get_or_create_user_bookmark_channel(user)
            if bookmark_channel:
                bookmark_message = await send_bookmark_message(bookmark_channel, auction_data, user)
                bookmark_channel_id = bookmark_channel.id
                print(f"📚 Sent bookmark to channel for {user.name}")
        
        if bookmark_message:
            # Save bookmark to database
            cursor.execute('''
                INSERT OR REPLACE INTO user_bookmarks 
                (user_id, auction_id, bookmark_message_id, bookmark_channel_id)
                VALUES (?, ?, ?, ?)
            ''', (user.id, auction_data['auction_id'], bookmark_message.id, bookmark_channel_id))
            
            conn.commit()
            print(f"📚 Bookmarked {auction_data['brand']} item for {user.name}")
        else:
            print(f"❌ Failed to create bookmark message for {user.name}")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Error bookmarking for {user.name}: {e}")

# ADD THIS TEST COMMAND TO TEST THE BOOKMARK FUNCTION
@bot.command(name='test_bookmark_channel')
async def test_bookmark_channel_command(ctx):
    """Test creating a bookmark channel"""
    try:
        channel = await get_or_create_user_bookmark_channel(ctx.author)
        if channel:
            await ctx.send(f"✅ Created/found your bookmark channel: {channel.mention}")
        else:
            await ctx.send("❌ Failed to create bookmark channel")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

@bot.command(name='test_bookmark')
async def test_bookmark_command(ctx):
    """Test if bookmark commands are working"""
    await ctx.send("✅ Bookmark system is loading!")

@bot.command(name='check_tables')
async def check_tables_command(ctx):
    """Check what database tables exist"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        
        # Check if user_bookmarks table exists
        if 'user_bookmarks' not in tables:
            await ctx.send("❌ user_bookmarks table missing - need to update database")
        else:
            cursor.execute("SELECT COUNT(*) FROM user_bookmarks")
            count = cursor.fetchone()[0]
            await ctx.send(f"✅ user_bookmarks table exists with {count} bookmarks")
        
        # Check if user_preferences has bookmark columns
        cursor.execute("PRAGMA table_info(user_preferences)")
        columns = [col[1] for col in cursor.fetchall()]
        
        missing_cols = []
        if 'bookmark_method' not in columns:
            missing_cols.append('bookmark_method')
        if 'auto_bookmark_likes' not in columns:
            missing_cols.append('auto_bookmark_likes')
        
        if missing_cols:
            await ctx.send(f"❌ Missing columns: {', '.join(missing_cols)}")
        else:
            await ctx.send("✅ user_preferences table has bookmark columns")
        
        conn.close()
        
    except Exception as e:
        await ctx.send(f"❌ Database error: {e}")


@bot.command(name='add_bookmark_tables')
async def add_bookmark_tables_command(ctx):
    """Add missing bookmark tables and columns"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Create user_bookmarks table if it doesn't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                auction_id TEXT,
                bookmark_message_id INTEGER,
                bookmark_channel_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (auction_id) REFERENCES listings (auction_id),
                UNIQUE(user_id, auction_id)
            )
        ''')
        
        # Add bookmark columns to user_preferences if they don't exist
        try:
            cursor.execute('ALTER TABLE user_preferences ADD COLUMN bookmark_method TEXT DEFAULT "private_channel"')
            await ctx.send("✅ Added bookmark_method column")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                await ctx.send("✅ bookmark_method column already exists")
            else:
                await ctx.send(f"❌ Error adding bookmark_method: {e}")
        
        try:
            cursor.execute('ALTER TABLE user_preferences ADD COLUMN auto_bookmark_likes BOOLEAN DEFAULT TRUE')
            await ctx.send("✅ Added auto_bookmark_likes column")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                await ctx.send("✅ auto_bookmark_likes column already exists")
            else:
                await ctx.send(f"❌ Error adding auto_bookmark_likes: {e}")
        
        conn.commit()
        conn.close()
        
        await ctx.send("✅ Database updated for bookmarks!")
        
    except Exception as e:
        await ctx.send(f"❌ Error updating database: {e}")


@bot.command(name='bookmark_settings')
async def bookmark_settings_command(ctx):
    """Configure bookmark preferences"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute('SELECT bookmark_method, auto_bookmark_likes FROM user_preferences WHERE user_id = ?', 
                       (ctx.author.id,))
        prefs = cursor.fetchone()
        
        if not prefs:
            await ctx.send("❌ Please run `!setup` first!")
            conn.close()
            return
        
        bookmark_method = prefs[0] if prefs[0] else "private_channel"
        auto_bookmark = prefs[1] if prefs[1] is not None else True
        
        embed = discord.Embed(
            title="📚 Bookmark Settings",
            description="Configure how your liked items are bookmarked",
            color=0x0099ff
        )
        
        embed.add_field(
            name="📍 Current Method",
            value=f"{'🔒 Private Channel' if bookmark_method == 'private_channel' else '📨 Direct Message'}",
            inline=True
        )
        
        embed.add_field(
            name="🤖 Auto-Bookmark Likes",
            value=f"{'✅ Enabled' if auto_bookmark else '❌ Disabled'}",
            inline=True
        )
        
        embed.add_field(
            name="📋 Next Steps",
            value="Run `!add_bookmark_tables` if you see errors\nThen try `!test_bookmark` to test",
            inline=False
        )
        
        conn.close()
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


@bot.command(name='test_reaction')
async def test_reaction_command(ctx):
    """Test command to manually insert a reaction"""
    try:
        # Insert a test reaction
        success = add_reaction(ctx.author.id, "test123", "thumbs_up")
        
        if success:
            await ctx.send("✅ Test reaction inserted successfully!")
            
            # Check if it's there
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM reactions WHERE user_id = ?', (ctx.author.id,))
            reactions = cursor.fetchall()
            conn.close()
            
            await ctx.send(f"Your reactions in DB: {len(reactions)}")
            for reaction in reactions:
                await ctx.send(f"ID: {reaction[0]}, User: {reaction[1]}, Auction: {reaction[2]}, Type: {reaction[3]}, Time: {reaction[4]}")
        else:
            await ctx.send("❌ Failed to insert test reaction")
            
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

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
    
    # Handle setup reactions
    if reaction.message.embeds and len(reaction.message.embeds) > 0:
        embed = reaction.message.embeds[0]
        if embed.title and "Setup" in embed.title:
            await handle_setup_reaction(reaction, user)
            return
    
    # Handle bookmark removal in bookmark channels
    if (str(reaction.emoji) == "🗑️" and 
        reaction.message.channel.name and 
        reaction.message.channel.name.endswith("-bookmarks")):
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            
            cursor.execute('DELETE FROM user_bookmarks WHERE user_id = ? AND bookmark_message_id = ?',
                          (user.id, reaction.message.id))
            
            if cursor.rowcount > 0:
                await reaction.message.delete()
                conn.commit()
                print(f"🗑️ Removed bookmark for {user.name}")
            
            conn.close()
            return
            
        except Exception as e:
            print(f"❌ Error removing bookmark: {e}")
            return
    
    # Only process thumbs up/down for auction listings
    if str(reaction.emoji) not in ["👍", "👎"]:
        return
    
    # Check if user has completed setup
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
    
    # Check if message has embeds
    if not reaction.message.embeds:
        return
    
    # Extract auction ID from footer
    embed = reaction.message.embeds[0]
    footer_text = embed.footer.text if embed.footer else ""
    
    auction_id_match = re.search(r'ID: (\w+)', footer_text)
    if not auction_id_match:
        return
    
    auction_id = auction_id_match.group(1)
    reaction_type = "thumbs_up" if str(reaction.emoji) == "👍" else "thumbs_down"
    
    print(f"👆 Processing {reaction_type} for auction {auction_id} from {user.name}")
    
    # Find the listing in database
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM listings WHERE auction_id = ?', (auction_id,))
    result = cursor.fetchone()
    
    if not result:
        # Try with yahoo_ prefix
        cursor.execute('SELECT * FROM listings WHERE auction_id = ?', (f"yahoo_{auction_id}",))
        result = cursor.fetchone()
        if result:
            auction_id = f"yahoo_{auction_id}"
    
    if result:
        # Get listing details
        cursor.execute('''
            SELECT title, brand, price_jpy, price_usd, seller_id, yahoo_url, deal_quality, zenmarket_url, image_url
            FROM listings WHERE auction_id = ?
        ''', (auction_id,))
        listing_result = cursor.fetchone()
        
        if listing_result:
            title, brand, price_jpy, price_usd, seller_id, yahoo_url, deal_quality, zenmarket_url, image_url = listing_result
            
            # Save the reaction
            success = add_reaction(user.id, auction_id, reaction_type)
            
            if success:
                # Add confirmation emoji
                if reaction_type == "thumbs_up":
                    await reaction.message.add_reaction("✅")
                    
                    # Create bookmark for liked item
                    auction_data = {
                        'auction_id': auction_id,
                        'title': title,
                        'brand': brand,
                        'price_jpy': price_jpy,
                        'price_usd': price_usd,
                        'seller_id': seller_id,
                        'deal_quality': deal_quality,
                        'zenmarket_url': zenmarket_url,
                        'image_url': image_url,
                        'priority': 50  # Default priority
                    }
                    
                    # Save bookmark
                    await save_bookmark_to_user(user, auction_data)
                    
                else:
                    await reaction.message.add_reaction("❌")
                
                print(f"✅ Processed {reaction_type} from {user.name}: {title[:30]}...")
                
                # Update preference learner
                if preference_learner:
                    try:
                        auction_data_for_learner = {
                            'auction_id': auction_id,
                            'title': title,
                            'brand': brand,
                            'price_jpy': price_jpy,
                            'price_usd': price_usd,
                            'seller_id': seller_id,
                            'deal_quality': deal_quality
                        }
                        preference_learner.learn_from_reaction(user.id, auction_data_for_learner, reaction_type)
                    except Exception as e:
                        print(f"⚠️ Preference learner error: {e}")
            else:
                print(f"❌ Failed to save reaction from {user.name}")
        else:
            print(f"❌ Could not get listing details for {auction_id}")
    else:
        print(f"❌ No listing found for auction ID: {auction_id}")
    
    conn.close()
        
    except Exception as e:
        print(f"❌ REACTION DEBUG: Database error: {e}")
        import traceback
        traceback.print_exc()

# Also debug the add_reaction function
def add_reaction(user_id, auction_id, reaction_type):
    print(f"🔍 ADD_REACTION DEBUG: Called with user_id={user_id}, auction_id={auction_id}, reaction_type={reaction_type}")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        # Check if reactions table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reactions'")
        table_exists = cursor.fetchone() is not None
        print(f"🔍 ADD_REACTION DEBUG: Reactions table exists: {table_exists}")
        
        if not table_exists:
            print("❌ ADD_REACTION DEBUG: Reactions table doesn't exist!")
            return False
        
        # Delete existing reaction from this user for this auction
        cursor.execute('DELETE FROM reactions WHERE user_id = ? AND auction_id = ?', (user_id, auction_id))
        deleted_count = cursor.rowcount
        print(f"🔍 ADD_REACTION DEBUG: Deleted {deleted_count} existing reactions")
        
        # Insert new reaction
        cursor.execute('''
            INSERT INTO reactions (user_id, auction_id, reaction_type)
            VALUES (?, ?, ?)
        ''', (user_id, auction_id, reaction_type))
        
        conn.commit()
        print(f"✅ ADD_REACTION DEBUG: Successfully inserted reaction")
        
        # Verify insertion
        cursor.execute('SELECT COUNT(*) FROM reactions WHERE user_id = ? AND auction_id = ?', (user_id, auction_id))
        count = cursor.fetchone()[0]
        print(f"🔍 ADD_REACTION DEBUG: Verification - reactions in DB: {count}")
        
        return True
        
    except Exception as e:
        print(f"❌ ADD_REACTION DEBUG: Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        conn.close()

# Debug stats command
@bot.command(name='debug_db')
async def debug_db_command(ctx):
    """Debug command to check database state"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Check tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    
    # Check reactions table structure if it exists
    reactions_info = "Table doesn't exist"
    if 'reactions' in tables:
        cursor.execute("PRAGMA table_info(reactions)")
        columns = cursor.fetchall()
        reactions_info = f"Columns: {[col[1] for col in columns]}"
        
        cursor.execute("SELECT COUNT(*) FROM reactions")
        total_reactions = cursor.fetchone()[0]
        reactions_info += f"\nTotal reactions: {total_reactions}"
        
        cursor.execute("SELECT user_id, COUNT(*) FROM reactions GROUP BY user_id")
        user_counts = cursor.fetchall()
        reactions_info += f"\nReactions by user: {dict(user_counts)}"
    
    # Check listings
    listings_info = "Table doesn't exist"
    if 'listings' in tables:
        cursor.execute("SELECT COUNT(*) FROM listings")
        total_listings = cursor.fetchone()[0]
        listings_info = f"Total listings: {total_listings}"
        
        cursor.execute("SELECT auction_id FROM listings LIMIT 3")
        sample_ids = [row[0] for row in cursor.fetchall()]
        listings_info += f"\nSample IDs: {sample_ids}"
    
    embed = discord.Embed(title="🔧 Database Debug Info", color=0xff9900)
    embed.add_field(name="Tables", value=str(tables), inline=False)
    embed.add_field(name="Reactions Table", value=reactions_info, inline=False)
    embed.add_field(name="Listings Table", value=listings_info, inline=False)
    embed.add_field(name="Your User ID", value=str(ctx.author.id), inline=False)
    
    conn.close()
    await ctx.send(embed=embed)

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

@bot.command(name='bookmark_method')
async def bookmark_method_command(ctx, method: str):
    """Change bookmark method: dm or channel"""
    if method.lower() not in ['dm', 'channel']:
        await ctx.send("❌ Method must be `dm` or `channel`")
        return
    
    bookmark_method = "dm" if method.lower() == "dm" else "private_channel"
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE user_preferences 
        SET bookmark_method = ?, updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
    ''', (bookmark_method, ctx.author.id))
    
    if cursor.rowcount == 0:
        # User doesn't exist in preferences, create them
        cursor.execute('''
            INSERT INTO user_preferences 
            (user_id, bookmark_method, proxy_service, setup_complete)
            VALUES (?, ?, 'zenmarket', TRUE)
        ''', (ctx.author.id, bookmark_method))
    
    conn.commit()
    conn.close()
    
    method_name = "Direct Messages" if bookmark_method == "dm" else "Private Channel"
    await ctx.send(f"✅ Bookmark method changed to **{method_name}**")

@bot.command(name='bookmark_toggle')
async def bookmark_toggle_command(ctx):
    """Toggle auto-bookmarking of liked items"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('SELECT auto_bookmark_likes FROM user_preferences WHERE user_id = ?', (ctx.author.id,))
    current = cursor.fetchone()
    current_value = current[0] if current and current[0] is not None else True
    
    new_value = not current_value
    
    cursor.execute('''
        UPDATE user_preferences 
        SET auto_bookmark_likes = ?, updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
    ''', (new_value, ctx.author.id))
    
    conn.commit()
    conn.close()
    
    status = "enabled" if new_value else "disabled"
    await ctx.send(f"✅ Auto-bookmarking **{status}**")

@bot.command(name='bookmarks')
async def bookmarks_command(ctx, page: int = 1):
    """View your bookmarked items"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Get total count
    cursor.execute('SELECT COUNT(*) FROM user_bookmarks WHERE user_id = ?', (ctx.author.id,))
    total = cursor.fetchone()[0]
    
    if total == 0:
        embed = discord.Embed(
            title="📚 Your Bookmarks",
            description="You haven't bookmarked any items yet! React with 👍 to auction listings to bookmark them.",
            color=0xff9900
        )
        await ctx.send(embed=embed)
        conn.close()
        return
    
    # Pagination
    items_per_page = 5
    offset = (page - 1) * items_per_page
    total_pages = (total + items_per_page - 1) // items_per_page
    
    cursor.execute('''
        SELECT l.title, l.brand, l.price_usd, l.zenmarket_url, ub.created_at
        FROM user_bookmarks ub
        JOIN listings l ON ub.auction_id = l.auction_id
        WHERE ub.user_id = ?
        ORDER BY ub.created_at DESC
        LIMIT ? OFFSET ?
    ''', (ctx.author.id, items_per_page, offset))
    
    bookmarks = cursor.fetchall()
    
    embed = discord.Embed(
        title=f"📚 Your Bookmarks (Page {page}/{total_pages})",
        description=f"Showing {len(bookmarks)} of {total} bookmarked items",
        color=0x0099ff
    )
    
    for i, (title, brand, price_usd, url, created_at) in enumerate(bookmarks, 1):
        embed.add_field(
            name=f"{i + offset}. {brand.replace('_', ' ').title()}",
            value=f"[{title[:60]}{'...' if len(title) > 60 else ''}]({url})\n💰 ${price_usd:.2f} | 📅 {created_at[:10]}",
            inline=False
        )
    
    if total_pages > 1:
        embed.set_footer(text=f"Use !bookmarks {page + 1} for next page" if page < total_pages else "End of bookmarks")
    
    conn.close()
    await ctx.send(embed=embed)

@bot.command(name='test')
async def test_command(ctx):
    await ctx.send("✅ Bot is working!")

@bot.command(name='stats')
async def stats_command(ctx):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Debug: Check if reactions table exists and has data
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reactions'")
    reactions_table_exists = cursor.fetchone() is not None
    
    cursor.execute("SELECT COUNT(*) FROM reactions")
    total_reactions_all = cursor.fetchone()[0]
    
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
    
    # Debug: Get recent reactions for this user
    cursor.execute('''
        SELECT auction_id, reaction_type, created_at 
        FROM reactions 
        WHERE user_id = ? 
        ORDER BY created_at DESC 
        LIMIT 5
    ''', (ctx.author.id,))
    recent_reactions = cursor.fetchall()
    
    cursor.execute('''
        SELECT brand, preference_score FROM user_brand_preferences 
        WHERE user_id = ? ORDER BY preference_score DESC LIMIT 3
    ''', (ctx.author.id,))
    top_brands = cursor.fetchall()
    
    embed = discord.Embed(
        title=f"📊 Stats for {ctx.author.display_name}",
        color=0x0099ff
    )
    
    # Debug info
    debug_info = f"Reactions table exists: {reactions_table_exists}\n"
    debug_info += f"Total reactions (all users): {total_reactions_all}\n"
    debug_info += f"Your user ID: {ctx.author.id}"
    
    embed.add_field(
        name="🔧 Debug Info",
        value=debug_info,
        inline=False
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
    
    if recent_reactions:
        recent_text = "\n".join([f"{auction_id}: {reaction_type} ({created_at})" for auction_id, reaction_type, created_at in recent_reactions])
        embed.add_field(
            name="🕒 Recent Reactions",
            value=recent_text[:1024],
            inline=False
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
