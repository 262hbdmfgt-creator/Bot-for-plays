import asyncio
import logging
import sqlite3
import random
import string
import os
import re
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types, BaseMiddleware
from aiogram.filters import Command, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    Message,
    CallbackQuery,
    BotCommand,
    WebAppInfo
)

# ----------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------
API_TOKEN = os.getenv("BOT_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0"))
DB_NAME = os.getenv("DB_PATH", "fembot_database.db")

db_dir = os.path.dirname(DB_NAME)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ÐÐ°Ð·Ð²Ð°Ð½Ð¸Ñ ÑÐµÐ´ÐºÐ¾ÑÑÐµÐ¹ (ÑÐ¸ÑÑÑÐ¹ Python 3 UTF-8)
RARITY_RARE = "ÑÐµÐ´ÐºÐ¾Ðµ"
RARITY_ULTRA = "ÑÐ»ÑÑÑÐ° ÑÐµÐ´ÐºÐ¾Ðµ"
RARITY_MYTHIC = "Ð¼Ð¸ÑÐ¸ÑÐµÑÐºÐ¾Ðµ"
RARITY_LEGENDARY = "Ð»ÐµÐ³ÐµÐ½Ð´Ð°ÑÐ½Ð¾Ðµ"
RARITY_ARTIFACT = "ÑÐµÐ¼Ð±Ð¾Ð¹ÑÐºÐ¸Ð¹ Ð°ÑÑÐµÑÐ°ÐºÑ"
RARITY_RAREST = "ÑÐµÐ´ÑÐ°Ð¹ÑÐ¸Ð¹ ÑÐµÐ¼"

DEFAULT_WEIGHTS = {
    RARITY_RARE: 40,
    RARITY_ULTRA: 25,
    RARITY_MYTHIC: 15,
    RARITY_LEGENDARY: 12,
    RARITY_ARTIFACT: 6,
    RARITY_RAREST: 2
}

RARITY_ORDER = {
    RARITY_RAREST: 1,
    RARITY_ARTIFACT: 2,
    RARITY_LEGENDARY: 3,
    RARITY_MYTHIC: 4,
    RARITY_ULTRA: 5,
    RARITY_RARE: 6
}

CRAFT_PROGRESSION = [
    RARITY_RARE,
    RARITY_ULTRA,
    RARITY_MYTHIC,
    RARITY_LEGENDARY,
    RARITY_ARTIFACT,
    RARITY_RAREST
]

DEFAULT_AVATAR = "https://placehold.co/600x600/png?text=No+Avatar"

# ----------------------------------------------------------------------
# DATABASE ENGINE
# ----------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            is_admin INTEGER DEFAULT 0,
            last_foto_time TEXT,
            joined_at TEXT,
            coins INTEGER DEFAULT 0,
            last_daily TEXT,
            daily_streak INTEGER DEFAULT 0,
            luck_potion_expires TEXT,
            penalty_extra_hours INTEGER DEFAULT 0,
            penalty_expires TEXT
        )
    ''')
    cursor.execute('CREATE TABLE IF NOT EXISTS chats (chat_id INTEGER PRIMARY KEY, chat_type TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS photos (id INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT, rarity TEXT, description TEXT)')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_photos (
            user_id INTEGER,
            photo_id INTEGER,
            count INTEGER DEFAULT 1,
            PRIMARY KEY (user_id, photo_id)
        )
    ''')
    cursor.execute('CREATE TABLE IF NOT EXISTS admin_codes (code TEXT PRIMARY KEY, created_at TEXT, used INTEGER DEFAULT 0)')
    cursor.execute('CREATE TABLE IF NOT EXISTS promo_codes (code TEXT PRIMARY KEY, promo_type TEXT, expires_at TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS promo_uses (code TEXT, user_id INTEGER, PRIMARY KEY (code, user_id))')
    cursor.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    cursor.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_info', ?)",
        ("ÐÐ´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¸Ñ ÐµÑÐµ Ð½Ðµ Ð½Ð°ÑÑÑÐ¾Ð¸Ð»Ð° ÑÑÐ¾Ñ ÑÐµÐºÑÑ.",)
    )
    
    migrations = [
        "ALTER TABLE users ADD COLUMN coins INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN last_daily TEXT",
        "ALTER TABLE users ADD COLUMN daily_streak INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN luck_potion_expires TEXT",
        "ALTER TABLE users ADD COLUMN penalty_extra_hours INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN penalty_expires TEXT",
        "ALTER TABLE user_photos ADD COLUMN count INTEGER DEFAULT 1"
    ]
    for m in migrations:
        try: cursor.execute(m)
        except: pass

    if SUPER_ADMIN_ID != 0:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT OR IGNORE INTO users (user_id, username, is_admin, joined_at) VALUES (?, ?, 1, ?)", (SUPER_ADMIN_ID, "SuperAdmin", now_str))
        cursor.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (SUPER_ADMIN_ID,))

    conn.commit()
    conn.close()

def execute_query(query, params=(), fetchone=False, fetchall=False, commit=True):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(query, params)
    res = cursor.fetchone() if fetchone else (cursor.fetchall() if fetchall else None)
    if commit: conn.commit()
    conn.close()
    return res

def clean_surrogates(text: str) -> str:
    if not text:
        return ""
    return text.encode('utf-8', 'ignore').decode('utf-8')

def register_user_and_chat(user_id, username, chat_id, chat_type):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_username = clean_surrogates(username.lstrip("@")) if username else "Unknown"
    if not execute_query("SELECT user_id FROM users WHERE user_id = ?", (user_id,), fetchone=True):
        execute_query("INSERT INTO users (user_id, username, joined_at) VALUES (?, ?, ?)", (user_id, clean_username, now_str))
    else:
        execute_query("UPDATE users SET username = ? WHERE user_id = ?", (clean_username, user_id))
    if not execute_query("SELECT chat_id FROM chats WHERE chat_id = ?", (chat_id,), fetchone=True):
        execute_query("INSERT INTO chats (chat_id, chat_type) VALUES (?, ?)", (chat_id, chat_type))

def is_admin_user(user_id):
    res = execute_query("SELECT is_admin FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    return bool(res and res[0] == 1)

def is_super_admin(user_id):
    return user_id == SUPER_ADMIN_ID

# ----------------------------------------------------------------------
# MIDDLEWARES & STATES
# ----------------------------------------------------------------------
class RegisterMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.from_user:
            uname = event.from_user.username or event.from_user.first_name or "Unknown"
            register_user_and_chat(event.from_user.id, uname, event.chat.id, event.chat.type)
        return await handler(event, data)

class AdminStates(StatesGroup):
    waiting_for_photo = State()
    waiting_for_rarity = State()
    waiting_for_description = State()
    waiting_for_admin_text = State()
    waiting_for_broadcast = State()
    waiting_for_admin_code = State()
    edit_waiting_desc = State()

class IsAdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return is_admin_user(message.from_user.id)

class IsSuperAdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return is_super_admin(message.from_user.id)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
dp.message.outer_middleware(RegisterMiddleware())

@dp.my_chat_member()
async def on_my_chat_member(update: types.ChatMemberUpdated):
    chat_id = update.chat.id
    chat_type = update.chat.type
    if update.new_chat_member.status in ["member", "administrator"]:
        execute_query("INSERT OR IGNORE INTO chats (chat_id, chat_type) VALUES (?, ?)", (chat_id, chat_type))
    elif update.new_chat_member.status in ["left", "kicked"]:
        execute_query("DELETE FROM chats WHERE chat_id = ?", (chat_id,))

# ----------------------------------------------------------------------
# HELPER: ÐÐÐÐ¡Ð ÐÐÐÐ¬ÐÐÐÐÐ¢ÐÐÐ¯
# ----------------------------------------------------------------------
def resolve_target_user(message: Message, explicit_arg: str = None):
    if explicit_arg:
        explicit_arg = explicit_arg.strip()
        if explicit_arg.startswith("@"):
            uname = explicit_arg[1:]
            row = execute_query("SELECT user_id, username FROM users WHERE LOWER(username) = LOWER(?)", (uname,), fetchone=True)
            if row: return row[0], clean_surrogates(row[1])
        elif explicit_arg.isdigit():
            uid = int(explicit_arg)
            row = execute_query("SELECT user_id, username FROM users WHERE user_id = ?", (uid,), fetchone=True)
            if row: return row[0], clean_surrogates(row[1])
            return uid, str(uid)
        else:
            row = execute_query("SELECT user_id, username FROM users WHERE LOWER(username) = LOWER(?)", (explicit_arg,), fetchone=True)
            if row: return row[0], clean_surrogates(row[1])

    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        register_user_and_chat(target.id, target.username or target.first_name or "Unknown", message.chat.id, message.chat.type)
        return target.id, clean_surrogates(target.username or target.full_name)

    return None, None

def get_effective_cooldown_hours(user_id):
    res = execute_query("SELECT penalty_extra_hours, penalty_expires FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    if not res: return 3
    extra_h, exp_str = res[0] or 0, res[1]
    if exp_str:
        if datetime.now() < datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S"):
            return 3 + extra_h
        else:
            execute_query("UPDATE users SET penalty_extra_hours = 0, penalty_expires = NULL WHERE user_id = ?", (user_id,))
    return 3

# ----------------------------------------------------------------------
# ECONOMY & SHOP LOGIC
# ----------------------------------------------------------------------
def get_user_coins(user_id):
    res = execute_query("SELECT coins FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    return res[0] if res else 0

def add_coins(user_id, amount):
    execute_query("UPDATE users SET coins = MAX(0, coins + ?) WHERE user_id = ?", (amount, user_id))

@dp.message(Command("daily"))
async def cmd_daily(message: Message):
    user_id = message.from_user.id
    res = execute_query("SELECT last_daily, daily_streak FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    last_daily_str, streak = res[0], res[1]
    now = datetime.now()
    
    if last_daily_str:
        last_daily = datetime.strptime(last_daily_str, "%Y-%m-%d %H:%M:%S")
        if now - last_daily < timedelta(hours=24):
            rem = timedelta(hours=24) - (now - last_daily)
            hours, rem_sec = divmod(rem.seconds, 3600)
            return await message.reply(
                f"â³ ÐÑ ÑÐ¶Ðµ Ð·Ð°Ð±Ð¸ÑÐ°Ð»Ð¸ Ð±Ð¾Ð½ÑÑ! ÐÑÐ¸ÑÐ¾Ð´Ð¸ÑÐµ ÑÐµÑÐµÐ· {hours} Ñ. {rem_sec//60} Ð¼Ð¸Ð½."
            )
        if now - last_daily > timedelta(hours=48):
            streak = 0

    new_streak = streak + 1
    reward = min(10 + (new_streak * 5), 100)
    add_coins(user_id, reward)
    execute_query("UPDATE users SET last_daily = ?, daily_streak = ? WHERE user_id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), new_streak, user_id))
    await message.reply(
        f"ð ÐÐ°Ð³ÑÐ°Ð´Ð° Ð¿Ð¾Ð»ÑÑÐµÐ½Ð°! +{reward} ÐºÐ¾Ð¸Ð½Ð¾Ð² ðª\nÐ¡ÐµÑÐ¸Ñ: {new_streak} Ð´Ð½."
    )

@dp.message(Command("shop"))
async def cmd_shop(message: Message):
    if message.chat.type != "private":
        return await message.reply("ð ÐÐ°Ð³Ð°Ð·Ð¸Ð½ Ð´Ð¾ÑÑÑÐ¿ÐµÐ½ ÑÐ¾Ð»ÑÐºÐ¾ Ð² Ð»Ð¸ÑÐ½ÑÑ ÑÐ¾Ð¾Ð±ÑÐµÐ½Ð¸ÑÑ Ñ Ð±Ð¾ÑÐ¾Ð¼! ÐÐ°Ð¿Ð¸ÑÐ¸ÑÐµ Ð¼Ð½Ðµ ÑÐ°Ð¼.")
    
    coins = get_user_coins(message.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="â¡ï¸ Ð¡Ð±ÑÐ¾Ñ ÐÐ (100 ðª)", callback_data="buy_reset")],
        [InlineKeyboardButton(text="ð Ð­Ð»Ð¸ÐºÑÐ¸Ñ ÑÐ´Ð°ÑÐ¸ x2 (250 ðª)", callback_data="buy_potion")],
        [InlineKeyboardButton(text="ð¦ Ð¡Ð»ÑÑÐ°Ð¹Ð½Ð°Ñ Ð­Ð¿Ð¸ÑÐµÑÐºÐ°Ñ ÐºÐ°ÑÑÐ° (350 ðª)", callback_data="buy_epic")]
    ])
    await message.answer(f"ð *ÐÐ°Ð³Ð°Ð·Ð¸Ð½ Ð¤ÐµÐ¼-ÑÐ¾Ð²Ð°ÑÐ¾Ð²*\n\nÐÐ°Ñ Ð±Ð°Ð»Ð°Ð½Ñ: {coins} ðª", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "buy_reset")
async def buy_reset(call: CallbackQuery):
    user_id = call.from_user.id
    if get_user_coins(user_id) < 100:
        return await call.answer("â ÐÐµÐ´Ð¾ÑÑÐ°ÑÐ¾ÑÐ½Ð¾ ÐºÐ¾Ð¸Ð½Ð¾Ð²!", show_alert=True)
    add_coins(user_id, -100)
    execute_query("UPDATE users SET last_foto_time = NULL WHERE user_id = ?", (user_id,))
    await call.message.edit_text("â¡ï¸ ÐÑÐ»Ð´Ð°ÑÐ½ ÑÑÐ¿ÐµÑÐ½Ð¾ ÑÐ±ÑÐ¾ÑÐµÐ½! Ð¢ÐµÐ¿ÐµÑÑ Ð²Ñ Ð¼Ð¾Ð¶ÐµÑÐµ Ð¸ÑÐ¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÑ /foto.")

@dp.callback_query(F.data == "buy_potion")
async def buy_potion(call: CallbackQuery):
    user_id = call.from_user.id
    if get_user_coins(user_id) < 250:
        return await call.answer("â ÐÐµÐ´Ð¾ÑÑÐ°ÑÐ¾ÑÐ½Ð¾ ÐºÐ¾Ð¸Ð½Ð¾Ð²!", show_alert=True)
    add_coins(user_id, -250)
    expires = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    execute_query("UPDATE users SET luck_potion_expires = ? WHERE user_id = ?", (expires, user_id))
    await call.message.edit_text("ð ÐÑ ÐºÑÐ¿Ð¸Ð»Ð¸ *Ð­Ð»Ð¸ÐºÑÐ¸Ñ ÑÐ´Ð°ÑÐ¸*! Ð ÑÐµÑÐµÐ½Ð¸Ðµ ÑÐ»ÐµÐ´ÑÑÑÐ¸Ñ 2 ÑÐ°ÑÐ¾Ð² ÑÐ°Ð½ÑÑ Ð½Ð° ÑÐµÐ´ÐºÐ¸Ðµ ÐºÐ°ÑÑÑ Ð·Ð½Ð°ÑÐ¸ÑÐµÐ»ÑÐ½Ð¾ Ð²ÑÑÐµ.", parse_mode="Markdown")

@dp.callback_query(F.data == "buy_epic")
async def buy_epic(call: CallbackQuery):
    user_id = call.from_user.id
    if get_user_coins(user_id) < 350:
        return await call.answer("â ÐÐµÐ´Ð¾ÑÑÐ°ÑÐ¾ÑÐ½Ð¾ ÐºÐ¾Ð¸Ð½Ð¾Ð²!", show_alert=True)
    
    epic_pool = execute_query(
        "SELECT id, file_id, rarity, description FROM photos WHERE rarity IN (?, ?, ?)",
        (RARITY_MYTHIC, RARITY_LEGENDARY, RARITY_ARTIFACT),
        fetchall=True
    )
    if not epic_pool:
        epic_pool = execute_query("SELECT id, file_id, rarity, description FROM photos", fetchall=True)
    if not epic_pool:
        return await call.answer("â Ð Ð±Ð°Ð·Ðµ Ð±Ð¾ÑÐ° Ð¿Ð¾ÐºÐ° Ð½ÐµÑ Ð¿Ð¾Ð´ÑÐ¾Ð´ÑÑÐ¸Ñ ÐºÐ°ÑÑ!", show_alert=True)
        
    p_id, f_id, rar, desc = random.choice(epic_pool)
    add_coins(user_id, -350)
    
    exists = execute_query("SELECT count FROM user_photos WHERE user_id = ? AND photo_id = ?", (user_id, p_id), fetchone=True)
    if exists:
        execute_query("UPDATE user_photos SET count = count + 1 WHERE user_id = ? AND photo_id = ?", (user_id, p_id))
    else:
        execute_query("INSERT INTO user_photos (user_id, photo_id, count) VALUES (?, ?, 1)", (user_id, p_id))
        
    await call.message.answer_photo(f_id, caption=f"ð¦ *ÐÐ¾ÐºÑÐ¿ÐºÐ° Ð¸Ð· Ð¼Ð°Ð³Ð°Ð·Ð¸Ð½Ð°:*\nâ­ Ð ÐµÐ´ÐºÐ¾ÑÑÑ: {rar.capitalize()}\nð {desc}", parse_mode="Markdown")
    await call.answer("Ð£ÑÐ¿ÐµÑÐ½Ð°Ñ Ð¿Ð¾ÐºÑÐ¿ÐºÐ°!")

# ----------------------------------------------------------------------
# CRAFT (Ð¡ÐÐÐ¢ÐÐ)
# ----------------------------------------------------------------------
@dp.message(Command("craft"))
async def cmd_craft(message: Message):
    if message.chat.type != "private":
        return await message.reply("ð  ÐÑÐ°ÑÑ Ð´Ð¾ÑÑÑÐ¿ÐµÐ½ ÑÐ¾Ð»ÑÐºÐ¾ Ð² ÐÐ¡.")
    user_id = message.from_user.id
    counts = execute_query(
        "SELECT p.rarity, SUM(up.count) FROM user_photos up JOIN photos p ON up.photo_id = p.id WHERE up.user_id = ? GROUP BY p.rarity",
        (user_id,),
        fetchall=True
    )
    
    btns = []
    for rar, total in counts:
        if total >= 3 and rar in CRAFT_PROGRESSION[:-1]:
            btns.append([InlineKeyboardButton(text=f"{rar.capitalize()} (x3 â 1 Ð²ÑÑÐµ)", callback_data=f"do_craft:{rar}")])
            
    if not btns:
        return await message.reply("ð  Ð£ Ð²Ð°Ñ Ð½ÐµÐ´Ð¾ÑÑÐ°ÑÐ¾ÑÐ½Ð¾ Ð´ÑÐ±Ð»Ð¸ÐºÐ°ÑÐ¾Ð² (Ð½ÑÐ¶Ð½Ð¾ Ð¼Ð¸Ð½Ð¸Ð¼ÑÐ¼ 3 ÐºÐ°ÑÑÑ Ð¾Ð´Ð½Ð¾Ð¹ ÑÐµÐ´ÐºÐ¾ÑÑÐ¸).")
    await message.answer("ð  *ÐÐµÐ½Ñ Ð¡Ð¸Ð½ÑÐµÐ·Ð°*\nÐÑÐ±ÐµÑÐ¸ÑÐµ, ÑÑÐ¾ Ð¿ÐµÑÐµÑÐ°Ð±Ð¾ÑÐ°ÑÑ:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("do_craft:"))
async def do_craft(call: CallbackQuery):
    rarity = call.data.split(":")[1]
    user_id = call.from_user.id
    photos = execute_query(
        "SELECT up.photo_id, up.count FROM user_photos up JOIN photos p ON up.photo_id = p.id WHERE up.user_id = ? AND p.rarity = ?",
        (user_id, rarity),
        fetchall=True
    )
    
    if sum(p[1] for p in photos) < 3:
        return await call.answer("â ÐÐµÐ´Ð¾ÑÑÐ°ÑÐ¾ÑÐ½Ð¾ ÐºÐ°ÑÑ!")

    rem = 0
    for p_id, count in photos:
        to_rem = min(3 - rem, count)
        if count - to_rem <= 0:
            execute_query("DELETE FROM user_photos WHERE user_id = ? AND photo_id = ?", (user_id, p_id))
        else:
            execute_query("UPDATE user_photos SET count = count - ? WHERE user_id = ? AND photo_id = ?", (to_rem, user_id, p_id))
        rem += to_rem
        if rem >= 3:
            break
        
    target = CRAFT_PROGRESSION[CRAFT_PROGRESSION.index(rarity) + 1]
    new_p = execute_query("SELECT id FROM photos WHERE rarity = ? ORDER BY RANDOM() LIMIT 1", (target,), fetchone=True)
    
    if new_p:
        p_id = new_p[0]
        exists = execute_query("SELECT count FROM user_photos WHERE user_id = ? AND photo_id = ?", (user_id, p_id), fetchone=True)
        if exists:
            execute_query("UPDATE user_photos SET count = count + 1 WHERE user_id = ? AND photo_id = ?", (user_id, p_id))
        else:
            execute_query("INSERT INTO user_photos (user_id, photo_id, count) VALUES (?, ?, 1)", (user_id, p_id))
        await call.message.edit_text(f"â¨ Ð£ÑÐ¿ÐµÑÐ½ÑÐ¹ ÐºÑÐ°ÑÑ! ÐÐ¾Ð»ÑÑÐµÐ½Ð° ÐºÐ°ÑÑÐ°: *{target.upper()}*!", parse_mode="Markdown")
    else:
        add_coins(user_id, 50)
        await call.message.edit_text("ð ÐÑÐ°ÑÑ ÑÑÐ¿ÐµÑÐµÐ½! ÐÐ°Ð¼ Ð½Ð°ÑÐ¸ÑÐ»ÐµÐ½Ð¾ 50 ÐºÐ¾Ð¸Ð½Ð¾Ð².")

# ----------------------------------------------------------------------
# CORE GAME LOGIC (/foto & triggers)
# ----------------------------------------------------------------------
@dp.message(Command("foto"))
@dp.message(F.text.lower() == "ÑÐµÐ¼")
async def handle_foto(message: Message):
    user_id = message.from_user.id
    now = datetime.now()
    res = execute_query("SELECT last_foto_time, luck_potion_expires FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    
    effective_cooldown_h = get_effective_cooldown_hours(user_id)
    
    if res and res[0]:
        last = datetime.strptime(res[0], "%Y-%m-%d %H:%M:%S")
        time_diff = now - last
        cooldown_delta = timedelta(hours=effective_cooldown_h)
        if time_diff < cooldown_delta:
            rem = cooldown_delta - time_diff
            hours, remainder = divmod(rem.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            return await message.reply(
                f"â³ ÐÑ ÑÐ¶Ðµ Ð±ÑÐ°Ð»Ð¸ ÑÐ¾ÑÐ¾! ÐÐ°Ñ ÐºÑÐ»Ð´Ð°ÑÐ½: *{effective_cooldown_h} Ñ.*\n"
                f"ÐÐ¾Ð¿ÑÐ¾Ð±ÑÐ¹ÑÐµ ÑÐ½Ð¾Ð²Ð° ÑÐµÑÐµÐ·: *{hours:02d}:{minutes:02d}:{seconds:02d}*.\n\n"
                f"ð¡ ÐÑÐ¿Ð¾Ð»ÑÐ·ÑÐ¹ `/promo ÐÐÐ` Ð¸Ð»Ð¸ `/shop` Ð´Ð»Ñ ÑÐ±ÑÐ¾ÑÐ°!",
                parse_mode="Markdown"
            )

    weights = DEFAULT_WEIGHTS.copy()
    luck_expires = res[1] if res else None
    is_lucky = False
    if luck_expires and datetime.strptime(luck_expires, "%Y-%m-%d %H:%M:%S") > now:
        is_lucky = True
        weights[RARITY_RARE] = 20
        weights[RARITY_ULTRA] = 25
        weights[RARITY_MYTHIC] = 20
        weights[RARITY_LEGENDARY] = 20
        weights[RARITY_ARTIFACT] = 10
        weights[RARITY_RAREST] = 5

    chosen = random.choices(list(weights.keys()), weights=list(weights.values()), k=1)[0]
    photo = execute_query("SELECT id, file_id, rarity, description FROM photos WHERE rarity = ? ORDER BY RANDOM() LIMIT 1", (chosen,), fetchone=True)
    if not photo:
        photo = execute_query("SELECT id, file_id, rarity, description FROM photos ORDER BY RANDOM() LIMIT 1", fetchone=True)
    
    if not photo:
        return await message.reply("Ð Ð±Ð°Ð·Ðµ Ð±Ð¾ÑÐ° Ð¿Ð¾ÐºÐ° Ð½ÐµÑ ÑÐ¾ÑÐ¾Ð³ÑÐ°ÑÐ¸Ð¹.")
    
    p_id, f_id, rar, desc = photo
    is_dub = execute_query("SELECT 1 FROM user_photos WHERE user_id = ? AND photo_id = ?", (user_id, p_id), fetchone=True)
    
    prefix = "ð *Ð­Ð»Ð¸ÐºÑÐ¸Ñ ÑÐ´Ð°ÑÐ¸ ÑÑÐ°Ð±Ð¾ÑÐ°Ð»!*\n" if is_lucky else ""
    if is_dub:
        execute_query("UPDATE user_photos SET count = count + 1 WHERE user_id = ? AND photo_id = ?", (user_id, p_id))
        add_coins(user_id, 15)
        caption = f"{prefix}ð *ÐÐ£ÐÐÐÐÐÐ¢!*\n\n+15 ÐºÐ¾Ð¸Ð½Ð¾Ð² ðª\nâ­ {rar.capitalize()}\nð ÐÐ°ÑÑÑ: #{p_id}"
    else:
        execute_query("INSERT INTO user_photos (user_id, photo_id, count) VALUES (?, ?, 1)", (user_id, p_id))
        caption = f"{prefix}ð *ÐÐÐÐÐ¯ ÐÐÐ Ð¢Ð!*\n\nâ­ Ð ÐµÐ´ÐºÐ¾ÑÑÑ: {rar.capitalize()}\nð {desc}\nð ÐÐ°ÑÑÑ: #{p_id}"

    execute_query("UPDATE users SET last_foto_time = ? WHERE user_id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), user_id))
    await bot.send_photo(message.chat.id, f_id, caption=caption, parse_mode="Markdown")

    if message.chat.type != "private" and random.random() < 0.10:
        reward = random.randint(20, 50)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ð¦ ÐÐ¢ÐÐ Ð«Ð¢Ð¬!", callback_data=f"get_chest:{reward}")]])
        await bot.send_message(message.chat.id, "ð *ÐÐÐÐÐ ÐÐ Ð Ð§ÐÐ¢Ð!*\nÐÑÐ¾-ÑÐ¾ ÑÑÐ¾Ð½Ð¸Ð» ÑÑÐ½Ð´ÑÐº Ñ Ð¼Ð¾Ð½ÐµÑÐ°Ð¼Ð¸. ÐÐ¼Ð¸!", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("get_chest:"))
async def get_chest(call: CallbackQuery):
    reward = int(call.data.split(":")[1])
    add_coins(call.from_user.id, reward)
    uname = clean_surrogates(call.from_user.username or str(call.from_user.id))
    await call.message.edit_text(f"ð Ð¡ÑÐ½Ð´ÑÐº Ð¾ÑÐºÑÑÐ» @{uname}!\nÐÐ°Ð³ÑÐ°Ð´Ð°: {reward} ðª")

# ----------------------------------------------------------------------
# PROFILE & COLLECTION
# ----------------------------------------------------------------------
async def show_profile_for_user(target_id: int, target_name: str, send_to_chat_id: int, reply_to_msg_id: int = None):
    res = execute_query("SELECT coins, luck_potion_expires, penalty_extra_hours, penalty_expires FROM users WHERE user_id = ?", (target_id,), fetchone=True)
    coins = res[0] if res else 0
    potion = res[1] if res else None
    penalty_h = res[2] if res else 0
    penalty_exp = res[3] if res else None
    
    total = execute_query("SELECT SUM(count) FROM user_photos WHERE user_id = ?", (target_id,), fetchone=True)[0] or 0
    unique = execute_query("SELECT COUNT(*) FROM user_photos WHERE user_id = ?", (target_id,), fetchone=True)[0] or 0
    
    avatar = DEFAULT_AVATAR
    try:
        p = await bot.get_user_profile_photos(target_id, limit=1)
        if p.total_count > 0: avatar = p.photos[0][-1].file_id
    except: pass
    
    extra_info = ""
    if potion and datetime.strptime(potion, "%Y-%m-%d %H:%M:%S") > datetime.now():
        extra_info += "\nð *Ð­Ð»Ð¸ÐºÑÐ¸Ñ ÑÐ´Ð°ÑÐ¸ Ð°ÐºÑÐ¸Ð²ÐµÐ½!*"
        
    if penalty_exp and datetime.strptime(penalty_exp, "%Y-%m-%d %H:%M:%S") > datetime.now():
        extra_info += f"\nâ ï¸ *Ð¨ÑÑÐ°Ñ ÐÐ:* +{penalty_h} Ñ. (Ð´Ð¾ {penalty_exp})"

    clean_name = clean_surrogates(target_name)
    text = f"ð¤ *ÐÐ ÐÐ¤ÐÐÐ¬: {clean_name}*\nð° ÐÐ¾Ð¸Ð½Ñ: {coins} ðª\nð ÐÑÐµÐ³Ð¾ ÐºÐ°ÑÑ: {total}\nâ¨ Ð£Ð½Ð¸ÐºÐ°Ð»ÑÐ½ÑÑ: {unique}{extra_info}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ð¼ ÐÐ¾Ð»Ð»ÐµÐºÑÐ¸Ñ", callback_data=f"view_coll_user:{target_id}:0")]])
    await bot.send_photo(send_to_chat_id, photo=avatar, caption=text, reply_markup=kb, parse_mode="Markdown", reply_to_message_id=reply_to_msg_id)

@dp.message(Command("profil"))
@dp.message(F.text.lower() == "Ð¿ÑÐ¾ÑÐ¸Ð»Ñ")
async def handle_profile(message: Message):
    uname = message.from_user.username or message.from_user.first_name
    await show_profile_for_user(message.from_user.id, uname, message.chat.id, message.message_id)

@dp.callback_query(F.data.startswith("view_collection:"))
async def view_coll_self(call: CallbackQuery):
    idx = int(call.data.split(":")[1])
    await view_collection_renderer(call, call.from_user.id, idx)

@dp.callback_query(F.data.startswith("view_coll_user:"))
async def view_coll_any(call: CallbackQuery):
    parts = call.data.split(":")
    u_id = int(parts[1])
    idx = int(parts[2])
    await view_collection_renderer(call, u_id, idx)

async def view_collection_renderer(call: CallbackQuery, user_id: int, idx: int):
    photos = execute_query("SELECT p.id, p.file_id, p.rarity, p.description, up.count FROM user_photos up JOIN photos p ON up.photo_id = p.id WHERE up.user_id = ?", (user_id,), fetchall=True)
    if not photos:
        return await call.answer("Ð ÐºÐ¾Ð»Ð»ÐµÐºÑÐ¸Ð¸ Ð¿Ð¾ÐºÐ° Ð½ÐµÑ ÐºÐ°ÑÑ!", show_alert=True)
    
    sorted_p = sorted(photos, key=lambda x: RARITY_ORDER.get(x[2].lower(), 99))
    idx = max(0, min(idx, len(sorted_p) - 1))
    p_id, f_id, rar, desc, cnt = sorted_p[idx]
    
    text = f"ð¼ *ÐÐ¾Ð»Ð»ÐµÐºÑÐ¸Ñ* ({idx+1}/{len(sorted_p)})\nð ID: #{p_id}\nâ­ Ð ÐµÐ´ÐºÐ¾ÑÑÑ: {rar.capitalize()}\nð {desc}\nð¦ Ð Ð½Ð°Ð»Ð¸ÑÐ¸Ð¸: {cnt} ÑÑ."
    btns, nav = [], []
    if idx > 0: nav.append(InlineKeyboardButton(text="â¬ï¸", callback_data=f"view_coll_user:{user_id}:{idx-1}"))
    if idx < len(sorted_p)-1: nav.append(InlineKeyboardButton(text="â¡ï¸", callback_data=f"view_coll_user:{user_id}:{idx+1}"))
    if nav: btns.append(nav)
    btns.append([InlineKeyboardButton(text="ð¤ ÐÑÐ¾ÑÐ¸Ð»Ñ", callback_data=f"back_prof_user:{user_id}")])
    await call.message.edit_media(InputMediaPhoto(media=f_id, caption=text, parse_mode="Markdown"), reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("back_prof_user:"))
async def back_prof_handler(call: CallbackQuery):
    target_id = int(call.data.split(":")[1])
    res = execute_query("SELECT username, coins, luck_potion_expires, penalty_extra_hours, penalty_expires FROM users WHERE user_id = ?", (target_id,), fetchone=True)
    uname = clean_surrogates(res[0] or f"ID: {target_id}")
    coins = res[1] or 0
    potion = res[2]
    penalty_h = res[3] or 0
    penalty_exp = res[4]
    
    total = execute_query("SELECT SUM(count) FROM user_photos WHERE user_id = ?", (target_id,), fetchone=True)[0] or 0
    unique = execute_query("SELECT COUNT(*) FROM user_photos WHERE user_id = ?", (target_id,), fetchone=True)[0] or 0
    
    extra_info = ""
    if potion and datetime.strptime(potion, "%Y-%m-%d %H:%M:%S") > datetime.now():
        extra_info += "\nð *Ð­Ð»Ð¸ÐºÑÐ¸Ñ ÑÐ´Ð°ÑÐ¸ Ð°ÐºÑÐ¸Ð²ÐµÐ½!*"
    if penalty_exp and datetime.strptime(penalty_exp, "%Y-%m-%d %H:%M:%S") > datetime.now():
        extra_info += f"\nâ ï¸ *Ð¨ÑÑÐ°Ñ ÐÐ:* +{penalty_h} Ñ. (Ð´Ð¾ {penalty_exp})"
        
    text = f"ð¤ *ÐÐ ÐÐ¤ÐÐÐ¬: @{uname}*\nð° ÐÐ¾Ð¸Ð½Ñ: {coins} ðª\nð ÐÑÐµÐ³Ð¾ ÐºÐ°ÑÑ: {total}\nâ¨ Ð£Ð½Ð¸ÐºÐ°Ð»ÑÐ½ÑÑ: {unique}{extra_info}"
    await call.message.edit_media(InputMediaPhoto(media=DEFAULT_AVATAR, caption=text, parse_mode="Markdown"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ð¼ ÐÐ¾Ð»Ð»ÐµÐºÑÐ¸Ñ", callback_data=f"view_coll_user:{target_id}:0")]]))

# ----------------------------------------------------------------------
# Ð¡ÐÐ¡Ð¢ÐÐÐ ÐÐÐÐÐÐÐÐÐ Ð ÐÐÐÐÐ ÐÐ¦ÐÐ
# ----------------------------------------------------------------------
@dp.message(F.text.lower().startswith("ÑÐ²Ð¾Ð¹ Ð¿ÑÐ¾ÑÐ¸Ð»Ñ"), IsAdminFilter())
async def admin_view_target_profile(message: Message):
    parts = message.text.split(maxsplit=2)
    explicit_user = parts[2] if len(parts) > 2 else None
    t_id, t_name = resolve_target_user(message, explicit_user)
    if not t_id:
        return await message.reply("â ï¸ Ð£ÐºÐ°Ð¶Ð¸ÑÐµ Ð¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»Ñ ÑÐµÑÐµÐ· @ÑÐ·ÐµÑÐ½ÐµÐ¹Ð¼ Ð¸Ð»Ð¸ Ð¾ÑÐ²ÐµÑÐ¾Ð¼ Ð½Ð° ÑÐ¾Ð¾Ð±ÑÐµÐ½Ð¸Ðµ!")
    await show_profile_for_user(t_id, t_name, message.chat.id, message.message_id)

@dp.message(F.text.lower().startswith(("/Ð±Ð¾Ñ +", "!Ð±Ð¾Ñ +")), IsAdminFilter())
async def admin_penalty_kd(message: Message):
    text = message.text.strip()
    match = re.search(r"/\u0431\u043e\u0442\s+\+(\d+)\s*\u043a\u0434\s+(\d+)\s*(\u0447\u0430\u0441\u0430|\u0447\u0430\u0441\u043e\u0432|\u0447\u0430\u0441|\u0447|\u0434\u043d\u0435\u0439|\u0434\u043d\u044f|\u0434)(?:\s+(@\w+|\d+))?", text, re.IGNORECASE)
    if not match:
        return await message.reply("â ï¸ Ð¤Ð¾ÑÐ¼Ð°Ñ: `/Ð±Ð¾Ñ +3ÐºÐ´ 6 ÑÐ°ÑÐ¾Ð² [@ÑÐ·ÐµÑ]`", parse_mode="Markdown")
        
    extra_h = int(match.group(1))
    dur_num = int(match.group(2))
    dur_unit = match.group(3).lower()
    explicit_user = match.group(4)
    
    t_id, t_name = resolve_target_user(message, explicit_user)
    if not t_id:
        return await message.reply("â ï¸ ÐÐ¾Ð»ÑÐ·Ð¾Ð²Ð°ÑÐµÐ»Ñ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½. Ð£ÐºÐ°Ð¶Ð¸ÑÐµ @ÑÐ·ÐµÑÐ½ÐµÐ¹Ð¼ Ð¸Ð»Ð¸ Ð¾ÑÐ²ÐµÑÑÑÐµ Ð½Ð° ÑÐ¾Ð¾Ð±ÑÐµÐ½Ð¸Ðµ!")
        
    if "Ð´" in dur_unit:
        dur_delta = timedelta(days=dur_num)
    else:
        dur_delta = timedelta(hours=dur_num)
        
    exp_time = (datetime.now() + dur_delta).strftime("%Y-%m-%d %H:%M:%S")
    execute_query("UPDATE users SET penalty_extra_hours = ?, penalty_expires = ? WHERE user_id = ?", (extra_h, exp_time, t_id))
    
    total_kd = 3 + extra_h
    await message.reply(
        f"âï¸ *ÐÐ°ÐºÐ°Ð·Ð°Ð½Ð¸Ðµ Ð¿ÑÐ¸Ð¼ÐµÐ½ÐµÐ½Ð¾!*\n\n"
        f"ð¤ ÐÐ³ÑÐ¾Ðº: *{clean_surrogates(t_name)}*\n"
        f"â³ ÐÑÐ»Ð´Ð°ÑÐ½: *{total_kd} Ñ.* (3 Ñ. + {extra_h} Ñ. ÑÑÑÐ°ÑÐ°)\n"
        f"âï¸ Ð¡ÑÐ¾Ðº Ð´ÐµÐ¹ÑÑÐ²Ð¸Ñ ÑÑÑÐ°ÑÐ°: Ð´Ð¾ *{exp_time}*",
        parse_mode="Markdown"
    )

@dp.message(F.text.lower().startswith(("/Ð±Ð¾Ñ -ÐºÐ°ÑÑÐ°", "!Ð±Ð¾Ñ -ÐºÐ°ÑÑÐ°")), IsAdminFilter())
async def admin_remove_card(message: Message):
    parts = message.text.split()
    if len(parts) < 3 or not parts[2].isdigit():
        return await message.reply("â ï¸ Ð¤Ð¾ÑÐ¼Ð°Ñ: `/Ð±Ð¾Ñ -ÐºÐ°ÑÑÐ° <ID> [@ÑÐ·ÐµÑ]`", parse_mode="Markdown")
        
    card_id = int(parts[2])
    explicit_user = parts[3] if len(parts) > 3 else None
    t_id, t_name = resolve_target_user(message, explicit_user)
    if not t_id:
        return await message.reply("â ï¸ Ð£ÐºÐ°Ð¶Ð¸ÑÐµ @ÑÐ·ÐµÑÐ½ÐµÐ¹Ð¼ Ð¸Ð»Ð¸ Ð¾ÑÐ²ÐµÑÑÑÐµ Ð½Ð° ÑÐ¾Ð¾Ð±ÑÐµÐ½Ð¸Ðµ!")
        
    has_card = execute_query("SELECT count FROM user_photos WHERE user_id = ? AND photo_id = ?", (t_id, card_id), fetchone=True)
    if not has_card:
        return await message.reply(f"â Ð£ Ð¸Ð³ÑÐ¾ÐºÐ° {clean_surrogates(t_name)} Ð½ÐµÑ ÐºÐ°ÑÑÑ #{card_id}!")
        
    cnt = has_card[0]
    if cnt <= 1:
        execute_query("DELETE FROM user_photos WHERE user_id = ? AND photo_id = ?", (t_id, card_id))
    else:
        execute_query("UPDATE user_photos SET count = count - 1 WHERE user_id = ? AND photo_id = ?", (t_id, card_id))
        
    await message.reply(f"ð ÐÐ°ÑÑÐ° #{card_id} ÑÑÐ¿ÐµÑÐ½Ð¾ Ð¸Ð·ÑÑÑÐ° Ñ Ð¸Ð³ÑÐ¾ÐºÐ° *{clean_surrogates(t_name)}*!", parse_mode="Markdown")

@dp.message(F.text.lower().startswith(("/Ð±Ð¾Ñ -ÑÐµÐ¼ÐºÐ¾Ð¸Ð½Ñ", "!Ð±Ð¾Ñ -ÑÐµÐ¼ÐºÐ¾Ð¸Ð½Ñ")), IsAdminFilter())
async def admin_remove_coins(message: Message):
    parts = message.text.split()
    if len(parts) < 3 or not parts[2].isdigit():
        return await message.reply("â ï¸ Ð¤Ð¾ÑÐ¼Ð°Ñ: `/Ð±Ð¾Ñ -ÑÐµÐ¼ÐºÐ¾Ð¸Ð½Ñ 300 [@ÑÐ·ÐµÑ]`", parse_mode="Markdown")
        
    amount = int(parts[2])
    explicit_user = parts[3] if len(parts) > 3 else None
    t_id, t_name = resolve_target_user(message, explicit_user)
    if not t_id:
        return await message.reply("â ï¸ Ð£ÐºÐ°Ð¶Ð¸ÑÐµ @ÑÐ·ÐµÑÐ½ÐµÐ¹Ð¼ Ð¸Ð»Ð¸ Ð¾ÑÐ²ÐµÑÑÑÐµ Ð½Ð° ÑÐ¾Ð¾Ð±ÑÐµÐ½Ð¸Ðµ!")
        
    add_coins(t_id, -amount)
    new_bal = get_user_coins(t_id)
    await message.reply(f"ð¸ Ð£ Ð¸Ð³ÑÐ¾ÐºÐ° *{clean_surrogates(t_name)}* ÑÐ¿Ð¸ÑÐ°Ð½Ð¾ *{amount}* ÑÐµÐ¼ÐºÐ¾Ð¸Ð½Ð¾Ð² ðª.\nÐ¢ÐµÐºÑÑÐ¸Ð¹ Ð±Ð°Ð»Ð°Ð½Ñ: *{new_bal}* ðª", parse_mode="Markdown")

# ----------------------------------------------------------------------
# TRADE & STATS
# ----------------------------------------------------------------------
@dp.message(F.text.lower() == "Ð¾Ð±Ð¼ÐµÐ½")
async def trade_init(message: Message):
    if not message.reply_to_message:
        return await message.reply("ÐÑÐ²ÐµÑÑÑÐµ `ÐÐ±Ð¼ÐµÐ½` Ð½Ð° ÑÐ¾Ð¾Ð±ÑÐµÐ½Ð¸Ðµ Ð¸Ð³ÑÐ¾ÐºÐ°!")
    target = message.reply_to_message.from_user
    if target.id == message.from_user.id:
        return
    me = await bot.get_me()
    uname = clean_surrogates(target.username or target.first_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ð¤ Ð ÐÐ¡", url=f"t.me/{me.username}?start=trade")]])
    await message.reply(f"ð¤ ÐÐ±Ð¼ÐµÐ½ Ñ @{uname}! ÐÐµÑÐ°Ð½Ð¸ÐºÐ° Ð² ÐÐ¡.", reply_markup=kb)

@dp.message(Command("top"))
@dp.message(F.text.lower().in_(["ÑÐ¾Ð¿", "ÑÑÐ°ÑÐ¸ÑÑÐ¸ÐºÐ°"]))
async def cmd_top(message: Message):
    rows = execute_query("SELECT u.username, u.user_id, SUM(up.count) as c, u.joined_at FROM users u LEFT JOIN user_photos up ON u.user_id = up.user_id GROUP BY u.user_id ORDER BY c DESC, u.joined_at ASC LIMIT 10", fetchall=True)
    text = "ð *Ð¢ÐÐ-10 ÐÐÐ ÐÐÐÐ:*\n\n"
    medals = ["ð¥", "ð¥", "ð¥", "4ï¸â£", "5ï¸â£", "6ï¸â£", "7ï¸â£", "8ï¸â£", "9ï¸â£", "ð"]
    for i, (name, uid, c, _) in enumerate(rows):
        m = medals[i] if i < len(medals) else f"{i+1}."
        safe_name = clean_surrogates(name or str(uid))
        text += f"{m} *{safe_name}* â ð¦ {c or 0} ÑÐ¾ÑÐ¾\n"
    await message.reply(text, parse_mode="Markdown")

# ----------------------------------------------------------------------
# PROMO & ADMIN COMMANDS
# ----------------------------------------------------------------------
def parse_duration(duration_str: str) -> timedelta:
    duration_str = duration_str.lower().strip()
    num = int(''.join(filter(str.isdigit, duration_str)) or 1)
    if "Ð³Ð¾Ð´" in duration_str or "Ð»ÐµÑ" in duration_str:
        return timedelta(days=365 * num)
    elif "Ð¼ÐµÑ" in duration_str:
        return timedelta(days=30 * num)
    elif "Ð´Ð½" in duration_str:
        return timedelta(days=num)
    else:
        return timedelta(hours=num)

@dp.message(Command("creat_promo_1"), IsAdminFilter())
async def create_p1(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply("Ð£ÐºÐ°Ð¶Ð¸ÑÐµ ÑÑÐ¾Ðº! ÐÑÐ¸Ð¼ÐµÑ: `/creat_promo_1 24 ÑÐ°ÑÐ°`", parse_mode="Markdown")
    exp = (datetime.now() + parse_duration(args[1])).strftime("%Y-%m-%d %H:%M:%S")
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    execute_query("INSERT INTO promo_codes (code, promo_type, expires_at) VALUES (?, 'single', ?)", (code, exp))
    await message.reply(f"ð ÐÐ´Ð½Ð¾ÑÐ°Ð·Ð¾Ð²ÑÐ¹ Ð¿ÑÐ¾Ð¼Ð¾ÐºÐ¾Ð´:\n`{code}`\nÐ¡ÑÐ¾Ðº: Ð´Ð¾ {exp}", parse_mode="Markdown")

@dp.message(Command("create_promo_2"), IsAdminFilter())
async def create_p2(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply("Ð£ÐºÐ°Ð¶Ð¸ÑÐµ ÑÑÐ¾Ðº! ÐÑÐ¸Ð¼ÐµÑ: `/create_promo_2 30 Ð´Ð½ÐµÐ¹`", parse_mode="Markdown")
    exp = (datetime.now() + parse_duration(args[1])).strftime("%Y-%m-%d %H:%M:%S")
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    execute_query("INSERT INTO promo_codes (code, promo_type, expires_at) VALUES (?, 'multi', ?)", (code, exp))
    await message.reply(f"ð ÐÐ½Ð¾Ð³Ð¾ÑÐ°Ð·Ð¾Ð²ÑÐ¹ Ð¿ÑÐ¾Ð¼Ð¾ÐºÐ¾Ð´:\n`{code}`\nÐ¡ÑÐ¾Ðº: Ð´Ð¾ {exp}", parse_mode="Markdown")

@dp.message(Command("promo"))
async def activate_promo(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply("ÐÐ²ÐµÐ´Ð¸ÑÐµ ÐºÐ¾Ð´: `/promo ÐÐÐ`", parse_mode="Markdown")
    code = args[1].strip().upper()
    user_id = message.from_user.id
    
    promo = execute_query("SELECT promo_type, expires_at FROM promo_codes WHERE code = ?", (code,), fetchone=True)
    if not promo or datetime.now() > datetime.strptime(promo[1], "%Y-%m-%d %H:%M:%S"):
        return await message.reply("â ÐÑÐ¾Ð¼Ð¾ÐºÐ¾Ð´ Ð½Ðµ ÑÑÑÐµÑÑÐ²ÑÐµÑ Ð¸Ð»Ð¸ Ð¸ÑÑÑÐº.")
        
    p_type = promo[0]
    if p_type == 'single' and execute_query("SELECT COUNT(*) FROM promo_uses WHERE code = ?", (code,), fetchone=True)[0] > 0:
        return await message.reply("â ÐÑÐ¾Ð¼Ð¾ÐºÐ¾Ð´ ÑÐ¶Ðµ Ð¸ÑÐ¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°Ð½.")
    if p_type == 'multi' and execute_query("SELECT 1 FROM promo_uses WHERE code = ? AND user_id = ?", (code, user_id), fetchone=True):
        return await message.reply("â ÐÑ ÑÐ¶Ðµ Ð°ÐºÑÐ¸Ð²Ð¸ÑÐ¾Ð²Ð°Ð»Ð¸ ÑÑÐ¾Ñ ÐºÐ¾Ð´.")
        
    execute_query("INSERT INTO promo_uses (code, user_id) VALUES (?, ?)", (code, user_id))
    execute_query("UPDATE users SET last_foto_time = NULL WHERE user_id = ?", (user_id,))
    await message.reply("ð ÐÑÐ¾Ð¼Ð¾ÐºÐ¾Ð´ Ð°ÐºÑÐ¸Ð²Ð¸ÑÐ¾Ð²Ð°Ð½! ÐÑÐ»Ð´Ð°ÑÐ½ /foto ÑÐ±ÑÐ¾ÑÐµÐ½.", parse_mode="Markdown")

@dp.message(Command("admin"))
@dp.message(F.text.lower() == "ÐºÑÐ¾ Ð°Ð´Ð¼Ð¸Ð½")
async def cmd_admin_info(message: Message):
    res = execute_query("SELECT value FROM settings WHERE key = 'admin_info'", fetchone=True)
    await message.reply(res[0] if res else "ÐÐ½ÑÐ¾ÑÐ¼Ð°ÑÐ¸Ñ Ð½Ðµ Ð½Ð°ÑÑÑÐ¾ÐµÐ½Ð°.")

@dp.message(Command("set_admin_text"), IsAdminFilter())
async def set_admin_text(message: Message, state: FSMContext):
    await message.reply("ÐÑÐ¿ÑÐ°Ð²ÑÑÐµ Ð½Ð¾Ð²ÑÐ¹ ÑÐµÐºÑÑ Ð´Ð»Ñ /admin:")
    await state.set_state(AdminStates.waiting_for_admin_text)

@dp.message(AdminStates.waiting_for_admin_text, IsAdminFilter())
async def save_admin_text(message: Message, state: FSMContext):
    execute_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_info', ?)", (message.text,))
    await message.reply("â Ð¢ÐµÐºÑÑ Ð¾Ð±Ð½Ð¾Ð²Ð»ÐµÐ½!")
    await state.clear()

@dp.message(Command("plus_foto"), IsAdminFilter())
async def pf_start(message: Message, state: FSMContext):
    await message.reply("ð¸ ÐÑÐ¿ÑÐ°Ð²ÑÑÐµ ÑÐ¾ÑÐ¾:")
    await state.set_state(AdminStates.waiting_for_photo)

@dp.message(AdminStates.waiting_for_photo, IsAdminFilter(), F.photo)
async def pf_photo(message: Message, state: FSMContext):
    await state.update_data(f_id=message.photo[-1].file_id)
    kb = [[InlineKeyboardButton(text=r.capitalize(), callback_data=f"rar:{r}")] for r in DEFAULT_WEIGHTS.keys()]
    await message.reply("ÐÑÐ±ÐµÑÐ¸ÑÐµ ÑÐµÐ´ÐºÐ¾ÑÑÑ:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(AdminStates.waiting_for_rarity)

@dp.callback_query(AdminStates.waiting_for_rarity, F.data.startswith("rar:"), IsAdminFilter())
async def pf_rar(call: CallbackQuery, state: FSMContext):
    await state.update_data(rar=call.data.split(":")[1])
    await call.message.edit_text("Ð¢ÐµÐ¿ÐµÑÑ Ð¾ÑÐ¿ÑÐ°Ð²ÑÑÐµ ÑÐµÐºÑÑÐ¾Ð²Ð¾Ðµ Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸Ðµ:")
    await state.set_state(AdminStates.waiting_for_description)

@dp.message(AdminStates.waiting_for_description, IsAdminFilter(), F.text)
async def pf_desc(message: Message, state: FSMContext):
    data = await state.get_data()
    execute_query("INSERT INTO photos (file_id, rarity, description) VALUES (?, ?, ?)", (data['f_id'], data['rar'], message.text))
    await message.reply("ð Ð¤Ð¾ÑÐ¾Ð³ÑÐ°ÑÐ¸Ñ ÑÑÐ¿ÐµÑÐ½Ð¾ Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð° Ð² Ð±Ð°Ð·Ñ!")
    await state.clear()

@dp.message(Command("add"), IsAdminFilter())
async def add_broad(message: Message, state: FSMContext):
    await message.reply("ð¢ Ð ÐµÐ¶Ð¸Ð¼ ÑÐ°ÑÑÑÐ»ÐºÐ¸. ÐÑÐ¿ÑÐ°Ð²Ð»ÑÐ¹ÑÐµ ÑÐ¾Ð¾Ð±ÑÐµÐ½Ð¸Ñ. ÐÐ»Ñ ÑÑÐ°ÑÑÐ° Ð¾ÑÐ¿ÑÐ°Ð²ÑÑÐµ `/save`.", parse_mode="Markdown")
    await state.update_data(msgs=[])
    await state.set_state(AdminStates.waiting_for_broadcast)

@dp.message(AdminStates.waiting_for_broadcast, IsAdminFilter())
async def collect_broad(message: Message, state: FSMContext):
    if message.text and message.text.lower().strip() == "/save":
        d = await state.get_data()
        msgs = d.get("msgs", [])
        if not msgs:
            return await message.reply("ÐÑÐµÑÐµÐ´Ñ Ð¿ÑÑÑÐ°.")
        
        targets = {r[0] for r in execute_query("SELECT chat_id FROM chats", fetchall=True)} | {r[0] for r in execute_query("SELECT user_id FROM users", fetchall=True)}
        await message.reply(f"ð Ð Ð°ÑÑÑÐ»ÐºÐ° Ð¿Ð¾ÑÐ»Ð° Ð¿Ð¾ {len(targets)} Ð°Ð´ÑÐµÑÐ°ÑÐ°Ð¼...")
        succ, err = 0, 0
        for t in targets:
            try:
                for m in msgs:
                    await bot.copy_message(chat_id=t, from_chat_id=m['chat_id'], message_id=m['message_id'])
                succ += 1
                await asyncio.sleep(0.05)
            except:
                err += 1
        await message.reply(f"ð ÐÐ¾ÑÐ¾Ð²Ð¾!\nÐ£ÑÐ¿ÐµÑÐ½Ð¾: {succ}\nÐÑÐ¸Ð±Ð¾Ðº: {err}")
        await state.clear()
        return
        
    d = await state.get_data()
    msgs = d.get("msgs", [])
    msgs.append({"chat_id": message.chat.id, "message_id": message.message_id})
    await state.update_data(msgs=msgs)
    await message.reply(f"ð¥ Ð¡Ð¾Ð¾Ð±ÑÐµÐ½Ð¸Ðµ #{len(msgs)} Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾ Ð² Ð¾ÑÐµÑÐµÐ´Ñ.")

# ----------------------------------------------------------------------
# Ð¡Ð£ÐÐÐ -ÐÐÐÐÐÐ¡ÐÐÐ Ð¤Ð£ÐÐÐ¦ÐÐ
# ----------------------------------------------------------------------
@dp.message(Command("admins"), IsSuperAdminFilter())
async def list_admins(message: Message):
    admins = execute_query("SELECT user_id, username, joined_at FROM users WHERE is_admin = 1", fetchall=True)
    text = "ð *Ð¡Ð¿Ð¸ÑÐ¾Ðº Ð°Ð´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¾ÑÐ¾Ð² Ð±Ð¾ÑÐ°:*\n\n"
    kb_buttons = []
    for u_id, uname, joined in admins:
        safe_name = clean_surrogates(uname)
        name = f"@{safe_name}" if safe_name and safe_name != "Unknown" else f"ID: {u_id}"
        is_super = " (Ð¡ÑÐ¿ÐµÑ-Ð°Ð´Ð¼Ð¸Ð½)" if u_id == SUPER_ADMIN_ID else ""
        text += f"â¢ *{name}*{is_super}\n"
        if u_id != SUPER_ADMIN_ID:
            kb_buttons.append([InlineKeyboardButton(text=f"â Ð¡Ð½ÑÑÑ Ñ Ð¿Ð¾ÑÑÐ° {name}", callback_data=f"remove_admin:{u_id}")])
            
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons) if kb_buttons else None
    await message.reply(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("remove_admin:"), IsSuperAdminFilter())
async def remove_admin_action(call: CallbackQuery):
    target_id = int(call.data.split(":")[1])
    if target_id == SUPER_ADMIN_ID:
        return await call.answer("ÐÐµÐ»ÑÐ·Ñ ÑÐ½ÑÑÑ Ð³Ð»Ð°Ð²Ð½Ð¾Ð³Ð¾ ÑÑÐ¿ÐµÑ-Ð°Ð´Ð¼Ð¸Ð½Ð°!", show_alert=True)
    execute_query("UPDATE users SET is_admin = 0 WHERE user_id = ?", (target_id,))
    await call.answer("â ÐÐ´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¾Ñ ÑÐ°Ð·Ð¶Ð°Ð»Ð¾Ð²Ð°Ð½!", show_alert=True)
    await call.message.delete()

@dp.message(Command("now_admin_cod"), IsSuperAdminFilter())
async def gen_admin_code(message: Message):
    code = "".join(random.sample(string.digits, 7) + random.sample(string.ascii_letters, 3))
    execute_query("INSERT INTO admin_codes (code, created_at, used) VALUES (?, ?, 0)", (code, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    await message.reply(f"ð ÐÐ¾Ð´ Ð°Ð´Ð¼Ð¸Ð½Ð° (ÑÐ¾Ð·Ð´Ð°Ð½ Ð¡ÑÐ¿ÐµÑ-Ð°Ð´Ð¼Ð¸Ð½Ð¾Ð¼, Ð´ÐµÐ¹ÑÑÐ²ÑÐµÑ 3 ÑÐ°ÑÐ°):\n`{code}`\nÐÐ²Ð¾Ð´Ð¸ÑÑ ÑÐµÑÐµÐ· `/i_am_admin`", parse_mode="Markdown")

@dp.message(Command("i_am_admin", "I_am_admin"))
async def become_admin(message: Message, state: FSMContext):
    if is_admin_user(message.from_user.id):
        return await message.reply("ÐÑ ÑÐ¶Ðµ Ð°Ð´Ð¼Ð¸Ð½!")
    await message.reply("ð ÐÐ²ÐµÐ´Ð¸ÑÐµ 10-Ð·Ð½Ð°ÑÐ½ÑÐ¹ ÐºÐ¾Ð´, Ð²ÑÐ´Ð°Ð½Ð½ÑÐ¹ Ð¡ÑÐ¿ÐµÑ-Ð°Ð´Ð¼Ð¸Ð½Ð¾Ð¼:")
    await state.set_state(AdminStates.waiting_for_admin_code)

@dp.message(AdminStates.waiting_for_admin_code)
async def verify_admin_code(message: Message, state: FSMContext):
    code = message.text.strip()
    res = execute_query("SELECT created_at, used FROM admin_codes WHERE code = ?", (code,), fetchone=True)
    if not res or res[1] == 1 or datetime.now() - datetime.strptime(res[0], "%Y-%m-%d %H:%M:%S") > timedelta(hours=3):
        await message.reply("â ÐÐµÐ²ÐµÑÐ½ÑÐ¹ Ð¸Ð»Ð¸ Ð¿ÑÐ¾ÑÑÐ¾ÑÐµÐ½Ð½ÑÐ¹ ÐºÐ¾Ð´.")
        await state.clear()
        return
    execute_query("UPDATE admin_codes SET used = 1 WHERE code = ?", (code,))
    execute_query("UPDATE users SET is_admin = 1 WHERE user_id = ?", (message.from_user.id,))
    await message.reply("ð ÐÐ¾Ð·Ð´ÑÐ°Ð²Ð»ÑÐµÐ¼! ÐÑ ÑÐµÐ¿ÐµÑÑ Ð°Ð´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¾Ñ.")
    await state.clear()

@dp.message(Command("card"), IsAdminFilter())
async def manage_card(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].isdigit():
        return await message.reply("â ï¸ Ð£ÐºÐ°Ð¶Ð¸ÑÐµ ID ÐºÐ°ÑÑÑ!\nÐÑÐ¸Ð¼ÐµÑ: `/card 5`", parse_mode="Markdown")
    p_id = int(args[1])
    photo = execute_query("SELECT id, file_id, rarity, description FROM photos WHERE id = ?", (p_id,), fetchone=True)
    if not photo:
        return await message.reply(f"â ÐÐ°ÑÑÐ° #{p_id} Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð°.")
    
    _, f_id, rar, desc = photo
    caption = f"âï¸ *Ð£Ð¿ÑÐ°Ð²Ð»ÐµÐ½Ð¸Ðµ ÐºÐ°ÑÑÐ¾Ð¹ #{p_id}*\n\nâ­ Ð ÐµÐ´ÐºÐ¾ÑÑÑ: {rar.capitalize()}\nð ÐÐ¿Ð¸ÑÐ°Ð½Ð¸Ðµ: {desc}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="âï¸ ÐÐ·Ð¼ÐµÐ½Ð¸ÑÑ ÑÐµÐ´ÐºÐ¾ÑÑÑ", callback_data=f"edit_rar:{p_id}")],
        [InlineKeyboardButton(text="ð ÐÐ·Ð¼ÐµÐ½Ð¸ÑÑ Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸Ðµ", callback_data=f"edit_desc:{p_id}")],
        [InlineKeyboardButton(text="ð Ð£ÐÐÐÐÐ¢Ð¬ ÐÐÐ Ð¢Ð£", callback_data=f"del_card:{p_id}")]
    ])
    await message.reply_photo(photo=f_id, caption=caption, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("edit_rar:"), IsAdminFilter())
async def edit_rar_menu(call: CallbackQuery):
    p_id = int(call.data.split(":")[1])
    kb = [[InlineKeyboardButton(text=r.capitalize(), callback_data=f"set_new_rar:{p_id}:{r}")] for r in DEFAULT_WEIGHTS.keys()]
    await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("set_new_rar:"), IsAdminFilter())
async def set_new_rar(call: CallbackQuery):
    _, p_id, new_rar = call.data.split(":")
    execute_query("UPDATE photos SET rarity = ? WHERE id = ?", (new_rar, int(p_id)))
    await call.answer(f"â Ð ÐµÐ´ÐºÐ¾ÑÑÑ Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð° Ð½Ð° {new_rar}!", show_alert=True)
    await call.message.delete()

@dp.callback_query(F.data.startswith("edit_desc:"), IsAdminFilter())
async def edit_desc_prompt(call: CallbackQuery, state: FSMContext):
    p_id = int(call.data.split(":")[1])
    await state.update_data(edit_p_id=p_id)
    await call.message.reply(f"ÐÑÐ¿ÑÐ°Ð²ÑÑÐµ Ð½Ð¾Ð²Ð¾Ðµ ÑÐµÐºÑÑÐ¾Ð²Ð¾Ðµ Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸Ðµ Ð´Ð»Ñ ÐºÐ°ÑÑÑ #{p_id}:")
    await state.set_state(AdminStates.edit_waiting_desc)
    await call.answer()

@dp.message(AdminStates.edit_waiting_desc, IsAdminFilter())
async def save_new_desc(message: Message, state: FSMContext):
    d = await state.get_data()
    p_id = d.get("edit_p_id")
    execute_query("UPDATE photos SET description = ? WHERE id = ?", (message.text, p_id))
    await message.reply(f"â ÐÐ¿Ð¸ÑÐ°Ð½Ð¸Ðµ ÐºÐ°ÑÑÑ #{p_id} Ð¾Ð±Ð½Ð¾Ð²Ð»ÐµÐ½Ð¾!")
    await state.clear()

@dp.callback_query(F.data.startswith("del_card:"), IsAdminFilter())
async def del_card_action(call: CallbackQuery):
    p_id = int(call.data.split(":")[1])
    execute_query("DELETE FROM photos WHERE id = ?", (p_id,))
    execute_query("DELETE FROM user_photos WHERE photo_id = ?", (p_id,))
    await call.message.delete()
    await call.answer(f"ð ÐÐ°ÑÑÐ° #{p_id} ÑÐ´Ð°Ð»ÐµÐ½Ð°!", show_alert=True)

# ----------------------------------------------------------------------
# MINI APP (ÐÐÐ-ÐÐ ÐÐÐÐÐÐÐÐ Ð TELEGRAM)
# ----------------------------------------------------------------------
@dp.message(Command("app", "menu"))
async def open_webapp(message: Message):
    webapp_url = os.getenv("WEBAPP_URL", "https://example.com/fembot_webapp")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ð± ÐÑÐºÑÑÑÑ ÐÐ³ÑÐ¾Ð²Ð¾Ð¹ ÐÐµÐ½Ñ-Ð¦ÐµÐ½ÑÑ (Mini App)", web_app=WebAppInfo(url=webapp_url))]
    ])
    await message.reply("ð® ÐÐ°Ð¶Ð¼Ð¸ÑÐµ ÐºÐ½Ð¾Ð¿ÐºÑ Ð½Ð¸Ð¶Ðµ, ÑÑÐ¾Ð±Ñ Ð¾ÑÐºÑÑÑÑ Ð¸Ð½ÑÐµÑÐ°ÐºÑÐ¸Ð²Ð½Ð¾Ðµ Telegram Mini App Ð¿ÑÐ¸Ð»Ð¾Ð¶ÐµÐ½Ð¸Ðµ:", reply_markup=kb)

# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
async def main():
    init_db()
    await bot.set_my_commands([
        BotCommand(command="foto", description="ÐÐ¾Ð»ÑÑÐ¸ÑÑ ÑÐ¾ÑÐ¾ (ÑÐ°Ð· Ð² 3 ÑÐ°ÑÐ°)"),
        BotCommand(command="profil", description="ÐÑÐ¾ÑÐ¸Ð»Ñ Ð¸ ÐºÐ¾Ð»Ð»ÐµÐºÑÐ¸Ñ"),
        BotCommand(command="daily", description="ÐÐ¶ÐµÐ´Ð½ÐµÐ²Ð½ÑÐ¹ Ð±Ð¾Ð½ÑÑ Ð¼Ð¾Ð½ÐµÑ"),
        BotCommand(command="shop", description="ÐÐ°Ð³Ð°Ð·Ð¸Ð½ ÑÐ»ÑÑÑÐµÐ½Ð¸Ð¹"),
        BotCommand(command="craft", description="Ð¡Ð¸Ð½ÑÐµÐ· (ÐºÑÐ°ÑÑ ÐºÐ°ÑÑ)"),
        BotCommand(command="top", description="Ð¢Ð¾Ð¿-10 Ð¸Ð³ÑÐ¾ÐºÐ¾Ð²"),
        BotCommand(command="promo", description="ÐÐºÑÐ¸Ð²Ð¸ÑÐ¾Ð²Ð°ÑÑ Ð¿ÑÐ¾Ð¼Ð¾ÐºÐ¾Ð´"),
        BotCommand(command="admins", description="[Ð¡ÑÐ¿ÐµÑ-Ð°Ð´Ð¼Ð¸Ð½] Ð£Ð¿ÑÐ°Ð²Ð»ÐµÐ½Ð¸Ðµ Ð°Ð´Ð¼Ð¸Ð½Ð°Ð¼Ð¸"),
        BotCommand(command="admin", description="ÐÐ½ÑÐ¾ÑÐ¼Ð°ÑÐ¸Ñ Ð¾Ð± Ð°Ð´Ð¼Ð¸Ð½Ð°Ñ")
    ])
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
