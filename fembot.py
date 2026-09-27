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

# ÐÐ°Ð·Ð²Ð°Ð½Ð¸Ñ ÑÐµÐ´ÐºÐ¾ÑÑÐµÐ¹ ÑÐµÑÐµÐ· Unicode escape
RARITY_RARE = "\u0440\u0435\u0434\u043a\u043e\u0435"
RARITY_ULTRA = "\u0443\u043b\u044c\u0442\u0440\u0430 \u0440\u0435\u0434\u043a\u043e\u0435"
RARITY_MYTHIC = "\u043c\u0438\u0444\u0438\u0447\u0435\u0441\u043a\u043e\u0435"
RARITY_LEGENDARY = "\u043b\u0435\u0433\u0435\u043d\u0434\u0430\u0440\u043d\u043e\u0435"
RARITY_ARTIFACT = "\u0444\u0435\u043c\u0431\u043e\u0439\u0441\u043a\u0438\u0439 \u0430\u0440\u0442\u0435\u0444\u0430\u043a\u0442"
RARITY_RAREST = "\u0440\u0435\u0434\u0447\u0430\u0439\u0448\u0438\u0439 \u0444\u0435\u043c"

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
        ("\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u044f \u0435\u0449\u0435 \u043d\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0438\u043b\u0430 \u044d\u0442\u043e\u0442 \u0442\u0435\u043a\u0441\u0442.",)
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

def register_user_and_chat(user_id, username, chat_id, chat_type):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_username = username.lstrip("@") if username else "Unknown"
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
            register_user_and_chat(event.from_user.id, event.from_user.username or "Unknown", event.chat.id, event.chat.type)
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
# HELPER: ÐÐÐÐ¡Ð ÐÐÐÐ¬ÐÐÐÐÐ¢ÐÐÐ¯ (Ð ÐÐÐÐÐ ÐÐÐ @Ð®ÐÐÐ ÐÐÐÐ/ID)
# ----------------------------------------------------------------------
def resolve_target_user(message: Message, explicit_arg: str = None):
    # 1. ÐÑÐ»Ð¸ Ð¿ÐµÑÐµÐ´Ð°Ð»Ð¸ Ð°ÑÐ³ÑÐ¼ÐµÐ½Ñ (@username Ð¸Ð»Ð¸ id)
    if explicit_arg:
        explicit_arg = explicit_arg.strip()
        if explicit_arg.startswith("@"):
            uname = explicit_arg[1:]
            row = execute_query("SELECT user_id, username FROM users WHERE LOWER(username) = LOWER(?)", (uname,), fetchone=True)
            if row: return row[0], row[1]
        elif explicit_arg.isdigit():
            uid = int(explicit_arg)
            row = execute_query("SELECT user_id, username FROM users WHERE user_id = ?", (uid,), fetchone=True)
            if row: return row[0], row[1]
            return uid, str(uid)
        else:
            row = execute_query("SELECT user_id, username FROM users WHERE LOWER(username) = LOWER(?)", (explicit_arg,), fetchone=True)
            if row: return row[0], row[1]

    # 2. ÐÑÐ»Ð¸ Ð¾ÑÐ²ÐµÑ (reply) Ð½Ð° ÑÐ¾Ð¾Ð±ÑÐµÐ½Ð¸Ðµ
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        register_user_and_chat(target.id, target.username or "Unknown", message.chat.id, message.chat.type)
        return target.id, target.username or target.full_name

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
                f"\u23f3 \u0412\u044b \u0443\u0436\u0435 \u0437\u0430\u0431\u0438\u0440\u0430\u043b\u0438 \u0431\u043e\u043d\u0443\u0441! \u041f\u0440\u0438\u0445\u043e\u0434\u0438\u0442\u0435 \u0447\u0435\u0440\u0435\u0437 {hours} \u0447. {rem_sec//60} \u043c\u0438\u043d."
            )
        if now - last_daily > timedelta(hours=48):
            streak = 0

    new_streak = streak + 1
    reward = min(10 + (new_streak * 5), 100)
    add_coins(user_id, reward)
    execute_query("UPDATE users SET last_daily = ?, daily_streak = ? WHERE user_id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), new_streak, user_id))
    await message.reply(
        f"\ud83c\udf81 \u041d\u0430\u0433\u0440\u0430\u0434\u0430 \u043f\u043e\u043b\u0443\u0447\u0435\u043d\u0430! +{reward} \u043a\u043e\u0438\u043d\u043e\u0432 \ud83e\ude99\n\u0421\u0435\u0440\u0438\u044f: {new_streak} \u0434\u043d."
    )

@dp.message(Command("shop"))
async def cmd_shop(message: Message):
    if message.chat.type != "private":
        return await message.reply("\ud83d\udecd \u041c\u0430\u0433\u0430\u0437\u0438\u043d \u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d \u0442\u043e\u043b\u044c\u043a\u043e \u0432 \u043b\u0438\u0447\u043d\u044b\u0445 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u044f\u0445 \u0441 \u0431\u043e\u0442\u043e\u043c! \u041d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \u043c\u043d\u0435 \u0442\u0430\u043c.")
    
    coins = get_user_coins(message.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u26a1\ufe0f \u0421\u0431\u0440\u043e\u0441 \u041a\u0414 (100 \ud83e\ude99)", callback_data="buy_reset")],
        [InlineKeyboardButton(text="\ud83c\udf40 \u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0443\u0434\u0430\u0447\u0438 x2 (250 \ud83e\ude99)", callback_data="buy_potion")],
        [InlineKeyboardButton(text="\ud83d\udce6 \u0421\u043b\u0443\u0447\u0430\u0439\u043d\u0430\u044f \u042d\u043f\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u043a\u0430\u0440\u0442\u0430 (350 \ud83e\ude99)", callback_data="buy_epic")]
    ])
    await message.answer(f"\ud83d\uded2 *\u041c\u0430\u0433\u0430\u0437\u0438\u043d \u0424\u0435\u043c-\u0442\u043e\u0432\u0430\u0440\u043e\u0432*\n\n\u0412\u0430\u0448 \u0431\u0430\u043b\u0430\u043d\u0441: {coins} \ud83e\ude99", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "buy_reset")
async def buy_reset(call: CallbackQuery):
    user_id = call.from_user.id
    if get_user_coins(user_id) < 100:
        return await call.answer("\u274c \u041d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e \u043a\u043e\u0438\u043d\u043e\u0432!", show_alert=True)
    add_coins(user_id, -100)
    execute_query("UPDATE users SET last_foto_time = NULL WHERE user_id = ?", (user_id,))
    await call.message.edit_text("\u26a1\ufe0f \u041a\u0443\u043b\u0434\u0430\u0443\u043d \u0443\u0441\u043f\u0435\u0448\u043d\u043e \u0441\u0431\u0440\u043e\u0448\u0435\u043d! \u0422\u0435\u043f\u0435\u0440\u044c \u0432\u044b \u043c\u043e\u0436\u0435\u0442\u0435 \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c /foto.")

@dp.callback_query(F.data == "buy_potion")
async def buy_potion(call: CallbackQuery):
    user_id = call.from_user.id
    if get_user_coins(user_id) < 250:
        return await call.answer("\u274c \u041d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e \u043a\u043e\u0438\u043d\u043e\u0432!", show_alert=True)
    add_coins(user_id, -250)
    expires = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    execute_query("UPDATE users SET luck_potion_expires = ? WHERE user_id = ?", (expires, user_id))
    await call.message.edit_text("\ud83c\udf40 \u0412\u044b \u043a\u0443\u043f\u0438\u043b\u0438 *\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0443\u0434\u0430\u0447\u0438*! \u0412 \u0442\u0435\u0447\u0435\u043d\u0438\u0435 \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0445 2 \u0447\u0430\u0441\u043e\u0432 \u0448\u0430\u043d\u0441\u044b \u043d\u0430 \u0440\u0435\u0434\u043a\u0438\u0435 \u043a\u0430\u0440\u0442\u044b \u0437\u043d\u0430\u0447\u0438\u0442\u0435\u043b\u044c\u043d\u043e \u0432\u044b\u0448\u0435.", parse_mode="Markdown")

@dp.callback_query(F.data == "buy_epic")
async def buy_epic(call: CallbackQuery):
    user_id = call.from_user.id
    if get_user_coins(user_id) < 350:
        return await call.answer("\u274c \u041d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e \u043a\u043e\u0438\u043d\u043e\u0432!", show_alert=True)
    
    epic_pool = execute_query(
        "SELECT id, file_id, rarity, description FROM photos WHERE rarity IN (?, ?, ?)",
        (RARITY_MYTHIC, RARITY_LEGENDARY, RARITY_ARTIFACT),
        fetchall=True
    )
    if not epic_pool:
        epic_pool = execute_query("SELECT id, file_id, rarity, description FROM photos", fetchall=True)
    if not epic_pool:
        return await call.answer("\u274c \u0412 \u0431\u0430\u0437\u0435 \u0431\u043e\u0442\u0430 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442 \u043f\u043e\u0434\u0445\u043e\u0434\u044f\u0449\u0438\u0445 \u043a\u0430\u0440\u0442!", show_alert=True)
        
    p_id, f_id, rar, desc = random.choice(epic_pool)
    add_coins(user_id, -350)
    
    exists = execute_query("SELECT count FROM user_photos WHERE user_id = ? AND photo_id = ?", (user_id, p_id), fetchone=True)
    if exists:
        execute_query("UPDATE user_photos SET count = count + 1 WHERE user_id = ? AND photo_id = ?", (user_id, p_id))
    else:
        execute_query("INSERT INTO user_photos (user_id, photo_id, count) VALUES (?, ?, 1)", (user_id, p_id))
        
    await call.message.answer_photo(f_id, caption=f"\ud83d\udce6 *\u041f\u043e\u043a\u0443\u043f\u043a\u0430 \u0438\u0437 \u043c\u0430\u0433\u0430\u0437\u0438\u043d\u0430:*\n\u2b50 \u0420\u0435\u0434\u043a\u043e\u0441\u0442\u044c: {rar.capitalize()}\n\ud83d\udcdd {desc}", parse_mode="Markdown")
    await call.answer("\u0423\u0441\u043f\u0435\u0448\u043d\u0430\u044f \u043f\u043e\u043a\u0443\u043f\u043a\u0430!")

# ----------------------------------------------------------------------
# CRAFT (Ð¡ÐÐÐ¢ÐÐ)
# ----------------------------------------------------------------------
@dp.message(Command("craft"))
async def cmd_craft(message: Message):
    if message.chat.type != "private":
        return await message.reply("\ud83d\udee0 \u041a\u0440\u0430\u0444\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d \u0442\u043e\u043b\u044c\u043a\u043e \u0432 \u041b\u0421.")
    user_id = message.from_user.id
    counts = execute_query(
        "SELECT p.rarity, SUM(up.count) FROM user_photos up JOIN photos p ON up.photo_id = p.id WHERE up.user_id = ? GROUP BY p.rarity",
        (user_id,),
        fetchall=True
    )
    
    btns = []
    for rar, total in counts:
        if total >= 3 and rar in CRAFT_PROGRESSION[:-1]:
            btns.append([InlineKeyboardButton(text=f"{rar.capitalize()} (x3 \u2794 1 \u0432\u044b\u0448\u0435)", callback_data=f"do_craft:{rar}")])
            
    if not btns:
        return await message.reply("\ud83d\udee0 \u0423 \u0432\u0430\u0441 \u043d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e \u0434\u0443\u0431\u043b\u0438\u043a\u0430\u0442\u043e\u0432 (\u043d\u0443\u0436\u043d\u043e \u043c\u0438\u043d\u0438\u043c\u0443\u043c 3 \u043a\u0430\u0440\u0442\u044b \u043e\u0434\u043d\u043e\u0439 \u0440\u0435\u0434\u043a\u043e\u0441\u0442\u0438).")
    await message.answer("\ud83d\udee0 *\u041c\u0435\u043d\u044e \u0421\u0438\u043d\u0442\u0435\u0437\u0430*\n\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435, \u0447\u0442\u043e \u043f\u0435\u0440\u0435\u0440\u0430\u0431\u043e\u0442\u0430\u0442\u044c:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="Markdown")

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
        return await call.answer("\u274c \u041d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e \u043a\u0430\u0440\u0442!")

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
        await call.message.edit_text(f"\u2728 \u0423\u0441\u043f\u0435\u0448\u043d\u044b\u0439 \u043a\u0440\u0430\u0444\u0442! \u041f\u043e\u043b\u0443\u0447\u0435\u043d\u0430 \u043a\u0430\u0440\u0442\u0430: *{target.upper()}*!", parse_mode="Markdown")
    else:
        add_coins(user_id, 50)
        await call.message.edit_text("\ud83c\udf81 \u041a\u0440\u0430\u0444\u0442 \u0443\u0441\u043f\u0435\u0448\u0435\u043d! \u0412\u0430\u043c \u043d\u0430\u0447\u0438\u0441\u043b\u0435\u043d\u043e 50 \u043a\u043e\u0438\u043d\u043e\u0432.")

# ----------------------------------------------------------------------
# CORE GAME LOGIC (/foto & triggers)
# ----------------------------------------------------------------------
@dp.message(Command("foto"))
@dp.message(F.text.lower() == "\u0444\u0435\u043c")
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
                f"\u23f3 \u0412\u044b \u0443\u0436\u0435 \u0431\u0440\u0430\u043b\u0438 \u0444\u043e\u0442\u043e! \u0412\u0430\u0448 \u043a\u0443\u043b\u0434\u0430\u0443\u043d: *{effective_cooldown_h} \u0447.*\n"
                f"\u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0441\u043d\u043e\u0432\u0430 \u0447\u0435\u0440\u0435\u0437: *{hours:02d}:{minutes:02d}:{seconds:02d}*.\n\n"
                f"\ud83d\udca1 \u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439 `/promo \u041a\u041e\u0414` \u0438\u043b\u0438 `/shop` \u0434\u043b\u044f \u0441\u0431\u0440\u043e\u0441\u0430!",
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
        return await message.reply("\u0412 \u0431\u0430\u0437\u0435 \u0431\u043e\u0442\u0430 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442 \u0444\u043e\u0442\u043e\u0433\u0440\u0430\u0444\u0438\u0439.")
    
    p_id, f_id, rar, desc = photo
    is_dub = execute_query("SELECT 1 FROM user_photos WHERE user_id = ? AND photo_id = ?", (user_id, p_id), fetchone=True)
    
    prefix = "\ud83c\udf40 *\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0443\u0434\u0430\u0447\u0438 \u0441\u0440\u0430\u0431\u043e\u0442\u0430\u043b!*\n" if is_lucky else ""
    if is_dub:
        execute_query("UPDATE user_photos SET count = count + 1 WHERE user_id = ? AND photo_id = ?", (user_id, p_id))
        add_coins(user_id, 15)
        caption = f"{prefix}\ud83c\udccf *\u0414\u0423\u0411\u041b\u0418\u041a\u0410\u0422!*\n\n+15 \u043a\u043e\u0438\u043d\u043e\u0432 \ud83e\ude99\n\u2b50 {rar.capitalize()}\n\ud83c\udd94 \u041a\u0430\u0440\u0442\u044b: #{p_id}"
    else:
        execute_query("INSERT INTO user_photos (user_id, photo_id, count) VALUES (?, ?, 1)", (user_id, p_id))
        caption = f"{prefix}\ud83c\udf89 *\u041d\u041e\u0412\u0410\u042f \u041a\u0410\u0420\u0422\u0410!*\n\n\u2b50 \u0420\u0435\u0434\u043a\u043e\u0441\u0442\u044c: {rar.capitalize()}\n\ud83d\udcdd {desc}\n\ud83c\udd94 \u041a\u0430\u0440\u0442\u044b: #{p_id}"

    execute_query("UPDATE users SET last_foto_time = ? WHERE user_id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), user_id))
    await bot.send_photo(message.chat.id, f_id, caption=caption, parse_mode="Markdown")

    if message.chat.type != "private" and random.random() < 0.10:
        reward = random.randint(20, 50)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="\ud83d\udce6 \u041e\u0422\u041a\u0420\u042b\u0422\u042c!", callback_data=f"get_chest:{reward}")]])
        await bot.send_message(message.chat.id, "\ud83c\udf81 *\u041f\u041e\u0414\u0410\u0420\u041e\u041a \u0412 \u0427\u0410\u0422\u0415!*\n\u041a\u0442\u043e-\u0442\u043e \u0443\u0440\u043e\u043d\u0438\u043b \u0441\u0443\u043d\u0434\u0443\u043a \u0441 \u043c\u043e\u043d\u0435\u0442\u0430\u043c\u0438. \u0416\u043c\u0438!", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("get_chest:"))
async def get_chest(call: CallbackQuery):
    reward = int(call.data.split(":")[1])
    add_coins(call.from_user.id, reward)
    await call.message.edit_text(f"\ud83c\udf89 \u0421\u0443\u043d\u0434\u0443\u043a \u043e\u0442\u043a\u0440\u044b\u043b @{call.from_user.username or call.from_user.id}!\n\u041d\u0430\u0433\u0440\u0430\u0434\u0430: {reward} \ud83e\ude99")

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
        extra_info += "\n\ud83c\udf40 *\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0443\u0434\u0430\u0447\u0438 \u0430\u043a\u0442\u0438\u0432\u0435\u043d!*"
        
    if penalty_exp and datetime.strptime(penalty_exp, "%Y-%m-%d %H:%M:%S") > datetime.now():
        extra_info += f"\n\u26a0\ufe0f *\u0428\u0442\u0440\u0430\u0444 \u041a\u0414:* +{penalty_h} \u0447. (\u0434\u043e {penalty_exp})"

    text = f"\ud83d\udc64 *\u041f\u0420\u041e\u0424\u0418\u041b\u042c: {target_name}*\n\ud83d\udcb0 \u041a\u043e\u0438\u043d\u044b: {coins} \ud83e\ude99\n\ud83d\uddc2 \u0412\u0441\u0435\u0433\u043e \u043a\u0430\u0440\u0442: {total}\n\u2728 \u0423\u043d\u0438\u043a\u0430\u043b\u044c\u043d\u044b\u0445: {unique}{extra_info}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="\ud83d\uddbc \u041a\u043e\u043b\u043b\u0435\u043a\u0446\u0438\u044f", callback_data=f"view_coll_user:{target_id}:0")]])
    await bot.send_photo(send_to_chat_id, photo=avatar, caption=text, reply_markup=kb, parse_mode="Markdown", reply_to_message_id=reply_to_msg_id)

@dp.message(Command("profil"))
@dp.message(F.text.lower() == "\u043f\u0440\u043e\u0444\u0438\u043b\u044c")
async def handle_profile(message: Message):
    await show_profile_for_user(message.from_user.id, message.from_user.full_name, message.chat.id, message.message_id)

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
        return await call.answer("\u0412 \u043a\u043e\u043b\u043b\u0435\u043a\u0446\u0438\u0438 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442 \u043a\u0430\u0440\u0442!", show_alert=True)
    
    sorted_p = sorted(photos, key=lambda x: RARITY_ORDER.get(x[2].lower(), 99))
    idx = max(0, min(idx, len(sorted_p) - 1))
    p_id, f_id, rar, desc, cnt = sorted_p[idx]
    
    text = f"\ud83d\uddbc *\u041a\u043e\u043b\u043b\u0435\u043a\u0446\u0438\u044f* ({idx+1}/{len(sorted_p)})\n\ud83c\udd94 ID: #{p_id}\n\u2b50 \u0420\u0435\u0434\u043a\u043e\u0441\u0442\u044c: {rar.capitalize()}\n\ud83d\udcdd {desc}\n\ud83d\udce6 \u0412 \u043d\u0430\u043b\u0438\u0447\u0438\u0438: {cnt} \u0448\u0442."
    btns, nav = [], []
    if idx > 0: nav.append(InlineKeyboardButton(text="\u2b05\ufe0f", callback_data=f"view_coll_user:{user_id}:{idx-1}"))
    if idx < len(sorted_p)-1: nav.append(InlineKeyboardButton(text="\u27a1\ufe0f", callback_data=f"view_coll_user:{user_id}:{idx+1}"))
    if nav: btns.append(nav)
    btns.append([InlineKeyboardButton(text="\ud83d\udc64 \u041f\u0440\u043e\u0444\u0438\u043b\u044c", callback_data=f"back_prof_user:{user_id}")])
    await call.message.edit_media(InputMediaPhoto(media=f_id, caption=text, parse_mode="Markdown"), reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("back_prof_user:"))
async def back_prof_handler(call: CallbackQuery):
    target_id = int(call.data.split(":")[1])
    res = execute_query("SELECT username, coins, luck_potion_expires, penalty_extra_hours, penalty_expires FROM users WHERE user_id = ?", (target_id,), fetchone=True)
    uname = res[0] or f"ID: {target_id}"
    coins = res[1] or 0
    potion = res[2]
    penalty_h = res[3] or 0
    penalty_exp = res[4]
    
    total = execute_query("SELECT SUM(count) FROM user_photos WHERE user_id = ?", (target_id,), fetchone=True)[0] or 0
    unique = execute_query("SELECT COUNT(*) FROM user_photos WHERE user_id = ?", (target_id,), fetchone=True)[0] or 0
    
    extra_info = ""
    if potion and datetime.strptime(potion, "%Y-%m-%d %H:%M:%S") > datetime.now():
        extra_info += "\n\ud83c\udf40 *\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0443\u0434\u0430\u0447\u0438 \u0430\u043a\u0442\u0438\u0432\u0435\u043d!*"
    if penalty_exp and datetime.strptime(penalty_exp, "%Y-%m-%d %H:%M:%S") > datetime.now():
        extra_info += f"\n\u26a0\ufe0f *\u0428\u0442\u0440\u0430\u0444 \u041a\u0414:* +{penalty_h} \u0447. (\u0434\u043e {penalty_exp})"
        
    text = f"\ud83d\udc64 *\u041f\u0420\u041e\u0424\u0418\u041b\u042c: @{uname}*\n\ud83d\udcb0 \u041a\u043e\u0438\u043d\u044b: {coins} \ud83e\ude99\n\ud83d\uddc2 \u0412\u0441\u0435\u0433\u043e \u043a\u0430\u0440\u0442: {total}\n\u2728 \u0423\u043d\u0438\u043a\u0430\u043b\u044c\u043d\u044b\u0445: {unique}{extra_info}"
    await call.message.edit_media(InputMediaPhoto(media=DEFAULT_AVATAR, caption=text, parse_mode="Markdown"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="\ud83d\uddbc \u041a\u043e\u043b\u043b\u0435\u043a\u0446\u0438\u044f", callback_data=f"view_coll_user:{target_id}:0")]]))

# ----------------------------------------------------------------------
# Ð¡ÐÐ¡Ð¢ÐÐÐ ÐÐÐÐÐÐÐÐÐ Ð ÐÐÐÐÐ ÐÐ¦ÐÐ (ÐÐÐ¯ ÐÐÐÐÐÐÐ¡Ð¢Ð ÐÐ¢ÐÐ ÐÐ)
# ----------------------------------------------------------------------

# 1. Ð¢Ð²Ð¾Ð¹ Ð¿ÑÐ¾ÑÐ¸Ð»Ñ (Ð¿ÑÐ¾ÑÐ¼Ð¾ÑÑ ÑÑÐ¶Ð¾Ð³Ð¾ Ð¿ÑÐ¾ÑÐ¸Ð»Ñ Ð°Ð´Ð¼Ð¸Ð½Ð¾Ð¼)
@dp.message(F.text.lower().startswith("\u0442\u0432\u043e\u0439 \u043f\u0440\u043e\u0444\u0438\u043b\u044c"), IsAdminFilter())
async def admin_view_target_profile(message: Message):
    parts = message.text.split(maxsplit=2)
    explicit_user = parts[2] if len(parts) > 2 else None
    t_id, t_name = resolve_target_user(message, explicit_user)
    if not t_id:
        return await message.reply("\u26a0\ufe0f \u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f \u0447\u0435\u0440\u0435\u0437 @\u044e\u0437\u0435\u0440\u043d\u0435\u0439\u043c \u0438\u043b\u0438 \u043e\u0442\u0432\u0435\u0442\u043e\u043c \u043d\u0430 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435!")
    await show_profile_for_user(t_id, t_name, message.chat.id, message.message_id)

# 2. Ð£Ð²ÐµÐ»Ð¸ÑÐµÐ½Ð¸Ðµ ÐÐ: /Ð±Ð¾Ñ +3ÐºÐ´ 6 ÑÐ°ÑÐ¾Ð² [@ÑÐ·ÐµÑ]
@dp.message(F.text.lower().startswith(("/\u0431\u043e\u0442 +", "!Ð±Ð¾Ñ +")), IsAdminFilter())
async def admin_penalty_kd(message: Message):
    text = message.text.strip()
    match = re.search(r"/\u0431\u043e\u0442\s+\+(\d+)\s*\u043a\u0434\s+(\d+)\s*(\u0447\u0430\u0441\u0430|\u0447\u0430\u0441\u043e\u0432|\u0447\u0430\u0441|\u0447|\u0434\u043d\u0435\u0439|\u0434\u043d\u044f|\u0434)(?:\s+(@\w+|\d+))?", text, re.IGNORECASE)
    if not match:
        return await message.reply("\u26a0\ufe0f \u0424\u043e\u0440\u043c\u0430\u0442: `/\u0431\u043e\u0442 +3\u043a\u0434 6 \u0447\u0430\u0441\u043e\u0432 [@\u044e\u0437\u0435\u0440]`", parse_mode="Markdown")
        
    extra_h = int(match.group(1))
    dur_num = int(match.group(2))
    dur_unit = match.group(3).lower()
    explicit_user = match.group(4)
    
    t_id, t_name = resolve_target_user(message, explicit_user)
    if not t_id:
        return await message.reply("\u26a0\ufe0f \u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d. \u0423\u043a\u0430\u0436\u0438\u0442\u0435 @\u044e\u0437\u0435\u0440\u043d\u0435\u0439\u043c \u0438\u043b\u0438 \u043e\u0442\u0432\u0435\u0442\u044c\u0442\u0435 \u043d\u0430 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435!")
        
    if "Ð´" in dur_unit:
        dur_delta = timedelta(days=dur_num)
    else:
        dur_delta = timedelta(hours=dur_num)
        
    exp_time = (datetime.now() + dur_delta).strftime("%Y-%m-%d %H:%M:%S")
    execute_query("UPDATE users SET penalty_extra_hours = ?, penalty_expires = ? WHERE user_id = ?", (extra_h, exp_time, t_id))
    
    total_kd = 3 + extra_h
    await message.reply(
        f"\u26d4 *\u041d\u0430\u043a\u0430\u0437\u0430\u043d\u0438\u0435 \u043f\u0440\u0438\u043c\u0435\u043d\u0435\u043d\u043e!*\n\n"
        f"\ud83d\udc64 \u0418\u0433\u0440\u043e\u043a: *{t_name}*\n"
        f"\u23f3 \u041a\u0443\u043b\u0434\u0430\u0443\u043d: *{total_kd} \u0447.* (3 \u0447. + {extra_h} \u0447. \u0448\u0442\u0440\u0430\u0444\u0430)\n"
        f"\u231b \u0421\u0440\u043e\u043a \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044f \u0448\u0442\u0440\u0430\u0444\u0430: \u0434\u043e *{exp_time}*",
        parse_mode="Markdown"
    )

# 3. ÐÑÐ±Ð¾Ñ ÐºÐ°ÑÑÑ: /Ð±Ð¾Ñ -ÐºÐ°ÑÑÐ° <ID> [@ÑÐ·ÐµÑ]
@dp.message(F.text.lower().startswith(("/\u0431\u043e\u0442 -\u043a\u0430\u0440\u0442\u0430", "!Ð±Ð¾Ñ -\u043a\u0430\u0440\u0442\u0430")), IsAdminFilter())
async def admin_remove_card(message: Message):
    parts = message.text.split()
    if len(parts) < 3 or not parts[2].isdigit():
        return await message.reply("\u26a0\ufe0f \u0424\u043e\u0440\u043c\u0430\u0442: `/\u0431\u043e\u0442 -\u043a\u0430\u0440\u0442\u0430 <ID> [@\u044e\u0437\u0435\u0440]`", parse_mode="Markdown")
        
    card_id = int(parts[2])
    explicit_user = parts[3] if len(parts) > 3 else None
    t_id, t_name = resolve_target_user(message, explicit_user)
    if not t_id:
        return await message.reply("\u26a0\ufe0f \u0423\u043a\u0430\u0436\u0438\u0442\u0435 @\u044e\u0437\u0435\u0440\u043d\u0435\u0439\u043c \u0438\u043b\u0438 \u043e\u0442\u0432\u0435\u0442\u044c\u0442\u0435 \u043d\u0430 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435!")
        
    has_card = execute_query("SELECT count FROM user_photos WHERE user_id = ? AND photo_id = ?", (t_id, card_id), fetchone=True)
    if not has_card:
        return await message.reply(f"\u274c \u0423 \u0438\u0433\u0440\u043e\u043a\u0430 {t_name} \u043d\u0435\u0442 \u043a\u0430\u0440\u0442\u044b #{card_id}!")
        
    cnt = has_card[0]
    if cnt <= 1:
        execute_query("DELETE FROM user_photos WHERE user_id = ? AND photo_id = ?", (t_id, card_id))
    else:
        execute_query("UPDATE user_photos SET count = count - 1 WHERE user_id = ? AND photo_id = ?", (t_id, card_id))
        
    await message.reply(f"\ud83d\uddd1 \u041a\u0430\u0440\u0442\u0430 #{card_id} \u0443\u0441\u043f\u0435\u0448\u043d\u043e \u0438\u0437\u044a\u044f\u0442\u0430 \u0443 \u0438\u0433\u0440\u043e\u043a\u0430 *{t_name}*!", parse_mode="Markdown")

# 4. Ð¡Ð¿Ð¸ÑÐ°Ð½Ð¸Ðµ ÑÐµÐ¼ÐºÐ¾Ð¸Ð½Ð¾Ð²: /Ð±Ð¾Ñ -ÑÐµÐ¼ÐºÐ¾Ð¸Ð½Ñ <ÑÐ¸ÑÐ»Ð¾> [@ÑÐ·ÐµÑ]
@dp.message(F.text.lower().startswith(("/\u0431\u043e\u0442 -\u0444\u0435\u043c\u043a\u043e\u0438\u043d\u044b", "!Ð±Ð¾Ñ -\u0444\u0435\u043c\u043a\u043e\u0438\u043d\u044b")), IsAdminFilter())
async def admin_remove_coins(message: Message):
    parts = message.text.split()
    if len(parts) < 3 or not parts[2].isdigit():
        return await message.reply("\u26a0\ufe0f \u0424\u043e\u0440\u043c\u0430\u0442: `/\u0431\u043e\u0442 -\u0444\u0435\u043c\u043a\u043e\u0438\u043d\u044b 300 [@\u044e\u0437\u0435\u0440]`", parse_mode="Markdown")
        
    amount = int(parts[2])
    explicit_user = parts[3] if len(parts) > 3 else None
    t_id, t_name = resolve_target_user(message, explicit_user)
    if not t_id:
        return await message.reply("\u26a0\ufe0f \u0423\u043a\u0430\u0436\u0438\u0442\u0435 @\u044e\u0437\u0435\u0440\u043d\u0435\u0439\u043c \u0438\u043b\u0438 \u043e\u0442\u0432\u0435\u0442\u044c\u0442\u0435 \u043d\u0430 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435!")
        
    add_coins(t_id, -amount)
    new_bal = get_user_coins(t_id)
    await message.reply(f"\ud83d\udcb8 \u0423 \u0438\u0433\u0440\u043e\u043a\u0430 *{t_name}* \u0441\u043f\u0438\u0441\u0430\u043d\u043e *{amount}* \u0444\u0435\u043c\u043a\u043e\u0438\u043d\u043e\u0432 \ud83e\ude99.\n\u0422\u0435\u043a\u0443\u0449\u0438\u0439 \u0431\u0430\u043b\u0430\u043d\u0441: *{new_bal}* \ud83e\ude99", parse_mode="Markdown")

# ----------------------------------------------------------------------
# TRADE & STATS
# ----------------------------------------------------------------------
@dp.message(F.text.lower() == "\u043e\u0431\u043c\u0435\u043d")
async def trade_init(message: Message):
    if not message.reply_to_message:
        return await message.reply("\u041e\u0442\u0432\u0435\u0442\u044c\u0442\u0435 `\u041e\u0431\u043c\u0435\u043d` \u043d\u0430 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u0438\u0433\u0440\u043e\u043a\u0430!")
    target = message.reply_to_message.from_user
    if target.id == message.from_user.id:
        return
    me = await bot.get_me()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="\ud83e\udd1d \u0412 \u041b\u0421", url=f"t.me/{me.username}?start=trade")]])
    await message.reply(f"\ud83e\udd1d \u041e\u0431\u043c\u0435\u043d \u0441 @{target.username}! \u041c\u0435\u0445\u0430\u043d\u0438\u043a\u0430 \u0432 \u041b\u0421.", reply_markup=kb)

@dp.message(Command("top"))
@dp.message(F.text.lower().in_(["\u0442\u043e\u043f", "\u0441\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430"]))
async def cmd_top(message: Message):
    rows = execute_query("SELECT u.username, u.user_id, SUM(up.count) as c, u.joined_at FROM users u LEFT JOIN user_photos up ON u.user_id = up.user_id GROUP BY u.user_id ORDER BY c DESC, u.joined_at ASC LIMIT 10", fetchall=True)
    text = "\ud83c\udfc6 *\u0422\u041e\u041f-10 \u0418\u0413\u0420\u041e\u041a\u041e\u0412:*\n\n"
    medals = ["\ud83e\udd47", "\ud83e\udd48", "\ud83e\udd49", "4\ufe0f\u20e3", "5\ufe0f\u20e3", "6\ufe0f\u20e3", "7\ufe0f\u20e3", "8\ufe0f\u20e3", "9\ufe0f\u20e3", "\ud83d\udd1f"]
    for i, (name, uid, c, _) in enumerate(rows):
        m = medals[i] if i < len(medals) else f"{i+1}."
        text += f"{m} *{name or uid}* \u2014 \ud83d\udce6 {c or 0} \u0444\u043e\u0442\u043e\n"
    await message.reply(text, parse_mode="Markdown")

# ----------------------------------------------------------------------
# PROMO & ADMIN COMMANDS
# ----------------------------------------------------------------------
def parse_duration(duration_str: str) -> timedelta:
    duration_str = duration_str.lower().strip()
    num = int(''.join(filter(str.isdigit, duration_str)) or 1)
    if "\u0433\u043e\u0434" in duration_str or "\u043b\u0435\u0442" in duration_str:
        return timedelta(days=365 * num)
    elif "\u043c\u0435\u0441" in duration_str:
        return timedelta(days=30 * num)
    elif "\u0434\u043d" in duration_str:
        return timedelta(days=num)
    else:
        return timedelta(hours=num)

@dp.message(Command("creat_promo_1"), IsAdminFilter())
async def create_p1(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply("\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u0441\u0440\u043e\u043a! \u041f\u0440\u0438\u043c\u0435\u0440: `/creat_promo_1 24 \u0447\u0430\u0441\u0430`", parse_mode="Markdown")
    exp = (datetime.now() + parse_duration(args[1])).strftime("%Y-%m-%d %H:%M:%S")
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    execute_query("INSERT INTO promo_codes (code, promo_type, expires_at) VALUES (?, 'single', ?)", (code, exp))
    await message.reply(f"\ud83c\udf9f \u041e\u0434\u043d\u043e\u0440\u0430\u0437\u043e\u0432\u044b\u0439 \u043f\u0440\u043e\u043c\u043e\u043a\u043e\u0434:\n`{code}`\n\u0421\u0440\u043e\u043a: \u0434\u043e {exp}", parse_mode="Markdown")

@dp.message(Command("create_promo_2"), IsAdminFilter())
async def create_p2(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply("\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u0441\u0440\u043e\u043a! \u041f\u0440\u0438\u043c\u0435\u0440: `/create_promo_2 30 \u0434\u043d\u0435\u0439`", parse_mode="Markdown")
    exp = (datetime.now() + parse_duration(args[1])).strftime("%Y-%m-%d %H:%M:%S")
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    execute_query("INSERT INTO promo_codes (code, promo_type, expires_at) VALUES (?, 'multi', ?)", (code, exp))
    await message.reply(f"\ud83c\udf9f \u041c\u043d\u043e\u0433\u043e\u0440\u0430\u0437\u043e\u0432\u044b\u0439 \u043f\u0440\u043e\u043c\u043e\u043a\u043e\u0434:\n`{code}`\n\u0421\u0440\u043e\u043a: \u0434\u043e {exp}", parse_mode="Markdown")

@dp.message(Command("promo"))
async def activate_promo(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply("\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043a\u043e\u0434: `/promo \u041a\u041e\u0414`", parse_mode="Markdown")
    code = args[1].strip().upper()
    user_id = message.from_user.id
    
    promo = execute_query("SELECT promo_type, expires_at FROM promo_codes WHERE code = ?", (code,), fetchone=True)
    if not promo or datetime.now() > datetime.strptime(promo[1], "%Y-%m-%d %H:%M:%S"):
        return await message.reply("\u274c \u041f\u0440\u043e\u043c\u043e\u043a\u043e\u0434 \u043d\u0435 \u0441\u0443\u0449\u0435\u0441\u0442\u0432\u0443\u0435\u0442 \u0438\u043b\u0438 \u0438\u0441\u0442\u0451\u043a.")
        
    p_type = promo[0]
    if p_type == 'single' and execute_query("SELECT COUNT(*) FROM promo_uses WHERE code = ?", (code,), fetchone=True)[0] > 0:
        return await message.reply("\u274c \u041f\u0440\u043e\u043c\u043e\u043a\u043e\u0434 \u0443\u0436\u0435 \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d.")
    if p_type == 'multi' and execute_query("SELECT 1 FROM promo_uses WHERE code = ? AND user_id = ?", (code, user_id), fetchone=True):
        return await message.reply("\u274c \u0412\u044b \u0443\u0436\u0435 \u0430\u043a\u0442\u0438\u0432\u0438\u0440\u043e\u0432\u0430\u043b\u0438 \u044d\u0442\u043e\u0442 \u043a\u043e\u0434.")
        
    execute_query("INSERT INTO promo_uses (code, user_id) VALUES (?, ?)", (code, user_id))
    execute_query("UPDATE users SET last_foto_time = NULL WHERE user_id = ?", (user_id,))
    await message.reply("\ud83c\udf89 \u041f\u0440\u043e\u043c\u043e\u043a\u043e\u0434 \u0430\u043a\u0442\u0438\u0432\u0438\u0440\u043e\u0432\u0430\u043d! \u041a\u0443\u043b\u0434\u0430\u0443\u043d /foto \u0441\u0431\u0440\u043e\u0448\u0435\u043d.", parse_mode="Markdown")

@dp.message(Command("admin"))
@dp.message(F.text.lower() == "\u043a\u0442\u043e \u0430\u0434\u043c\u0438\u043d")
async def cmd_admin_info(message: Message):
    res = execute_query("SELECT value FROM settings WHERE key = 'admin_info'", fetchone=True)
    await message.reply(res[0] if res else "\u0418\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f \u043d\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d\u0430.")

@dp.message(Command("set_admin_text"), IsAdminFilter())
async def set_admin_text(message: Message, state: FSMContext):
    await message.reply("\u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u043d\u043e\u0432\u044b\u0439 \u0442\u0435\u043a\u0441\u0442 \u0434\u043b\u044f /admin:")
    await state.set_state(AdminStates.waiting_for_admin_text)

@dp.message(AdminStates.waiting_for_admin_text, IsAdminFilter())
async def save_admin_text(message: Message, state: FSMContext):
    execute_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_info', ?)", (message.text,))
    await message.reply("\u2705 \u0422\u0435\u043a\u0441\u0442 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d!")
    await state.clear()

@dp.message(Command("plus_foto"), IsAdminFilter())
async def pf_start(message: Message, state: FSMContext):
    await message.reply("\ud83d\udcf8 \u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0444\u043e\u0442\u043e:")
    await state.set_state(AdminStates.waiting_for_photo)

@dp.message(AdminStates.waiting_for_photo, IsAdminFilter(), F.photo)
async def pf_photo(message: Message, state: FSMContext):
    await state.update_data(f_id=message.photo[-1].file_id)
    kb = [[InlineKeyboardButton(text=r.capitalize(), callback_data=f"rar:{r}")] for r in DEFAULT_WEIGHTS.keys()]
    await message.reply("\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0440\u0435\u0434\u043a\u043e\u0441\u0442\u044c:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(AdminStates.waiting_for_rarity)

@dp.callback_query(AdminStates.waiting_for_rarity, F.data.startswith("rar:"), IsAdminFilter())
async def pf_rar(call: CallbackQuery, state: FSMContext):
    await state.update_data(rar=call.data.split(":")[1])
    await call.message.edit_text("\u0422\u0435\u043f\u0435\u0440\u044c \u043e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0442\u0435\u043a\u0441\u0442\u043e\u0432\u043e\u0435 \u043e\u043f\u0438\u0441\u0430\u043d\u0438\u0435:")
    await state.set_state(AdminStates.waiting_for_description)

@dp.message(AdminStates.waiting_for_description, IsAdminFilter(), F.text)
async def pf_desc(message: Message, state: FSMContext):
    data = await state.get_data()
    execute_query("INSERT INTO photos (file_id, rarity, description) VALUES (?, ?, ?)", (data['f_id'], data['rar'], message.text))
    await message.reply("\ud83c\udf89 \u0424\u043e\u0442\u043e\u0433\u0440\u0430\u0444\u0438\u044f \u0443\u0441\u043f\u0435\u0448\u043d\u043e \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u0430 \u0432 \u0431\u0430\u0437\u0443!")
    await state.clear()

@dp.message(Command("add"), IsAdminFilter())
async def add_broad(message: Message, state: FSMContext):
    await message.reply("\ud83d\udce2 \u0420\u0435\u0436\u0438\u043c \u0440\u0430\u0441\u0441\u044b\u043b\u043a\u0438. \u041e\u0442\u043f\u0440\u0430\u0432\u043b\u044f\u0439\u0442\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u044f. \u0414\u043b\u044f \u0441\u0442\u0430\u0440\u0442\u0430 \u043e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 `/save`.", parse_mode="Markdown")
    await state.update_data(msgs=[])
    await state.set_state(AdminStates.waiting_for_broadcast)

@dp.message(AdminStates.waiting_for_broadcast, IsAdminFilter())
async def collect_broad(message: Message, state: FSMContext):
    if message.text and message.text.lower().strip() == "/save":
        d = await state.get_data()
        msgs = d.get("msgs", [])
        if not msgs:
            return await message.reply("\u041e\u0447\u0435\u0440\u0435\u0434\u044c \u043f\u0443\u0441\u0442\u0430.")
        
        targets = {r[0] for r in execute_query("SELECT chat_id FROM chats", fetchall=True)} | {r[0] for r in execute_query("SELECT user_id FROM users", fetchall=True)}
        await message.reply(f"\ud83d\ude80 \u0420\u0430\u0441\u0441\u044b\u043b\u043a\u0430 \u043f\u043e\u0448\u043b\u0430 \u043f\u043e {len(targets)} \u0430\u0434\u0440\u0435\u0441\u0430\u0442\u0430\u043c...")
        succ, err = 0, 0
        for t in targets:
            try:
                for m in msgs:
                    await bot.copy_message(chat_id=t, from_chat_id=m['chat_id'], message_id=m['message_id'])
                succ += 1
                await asyncio.sleep(0.05)
            except:
                err += 1
        await message.reply(f"\ud83c\udfc1 \u0413\u043e\u0442\u043e\u0432\u043e!\n\u0423\u0441\u043f\u0435\u0448\u043d\u043e: {succ}\n\u041e\u0448\u0438\u0431\u043e\u043a: {err}")
        await state.clear()
        return
        
    d = await state.get_data()
    msgs = d.get("msgs", [])
    msgs.append({"chat_id": message.chat.id, "message_id": message.message_id})
    await state.update_data(msgs=msgs)
    await message.reply(f"\ud83d\udce5 \u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 #{len(msgs)} \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u043e \u0432 \u043e\u0447\u0435\u0440\u0435\u0434\u044c.")

# ----------------------------------------------------------------------
# Ð¡Ð£ÐÐÐ -ÐÐÐÐÐÐ¡ÐÐÐ Ð¤Ð£ÐÐÐ¦ÐÐ
# ----------------------------------------------------------------------
@dp.message(Command("admins"), IsSuperAdminFilter())
async def list_admins(message: Message):
    admins = execute_query("SELECT user_id, username, joined_at FROM users WHERE is_admin = 1", fetchall=True)
    text = "\ud83d\udc51 *\u0421\u043f\u0438\u0441\u043e\u043a \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u043e\u0432 \u0431\u043e\u0442\u0430:*\n\n"
    kb_buttons = []
    for u_id, uname, joined in admins:
        name = f"@{uname}" if uname and uname != "Unknown" else f"ID: {u_id}"
        is_super = " (\u0421\u0443\u043f\u0435\u0440-\u0430\u0434\u043c\u0438\u043d)" if u_id == SUPER_ADMIN_ID else ""
        text += f"\u2022 *{name}*{is_super}\n"
        if u_id != SUPER_ADMIN_ID:
            kb_buttons.append([InlineKeyboardButton(text=f"\u274c \u0421\u043d\u044f\u0442\u044c \u0441 \u043f\u043e\u0441\u0442\u0430 {name}", callback_data=f"remove_admin:{u_id}")])
            
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons) if kb_buttons else None
    await message.reply(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("remove_admin:"), IsSuperAdminFilter())
async def remove_admin_action(call: CallbackQuery):
    target_id = int(call.data.split(":")[1])
    if target_id == SUPER_ADMIN_ID:
        return await call.answer("\u041d\u0435\u043b\u044c\u0437\u044f \u0441\u043d\u044f\u0442\u044c \u0433\u043b\u0430\u0432\u043d\u043e\u0433\u043e \u0441\u0443\u043f\u0435\u0440-\u0430\u0434\u043c\u0438\u043d\u0430!", show_alert=True)
    execute_query("UPDATE users SET is_admin = 0 WHERE user_id = ?", (target_id,))
    await call.answer("\u2705 \u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440 \u0440\u0430\u0437\u0436\u0430\u043b\u043e\u0432\u0430\u043d!", show_alert=True)
    await call.message.delete()

@dp.message(Command("now_admin_cod"), IsSuperAdminFilter())
async def gen_admin_code(message: Message):
    code = "".join(random.sample(string.digits, 7) + random.sample(string.ascii_letters, 3))
    execute_query("INSERT INTO admin_codes (code, created_at, used) VALUES (?, ?, 0)", (code, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    await message.reply(f"\ud83d\udd11 \u041a\u043e\u0434 \u0430\u0434\u043c\u0438\u043d\u0430 (\u0441\u043e\u0437\u0434\u0430\u043d \u0421\u0443\u043f\u0435\u0440-\u0430\u0434\u043c\u0438\u043d\u043e\u043c, \u0434\u0435\u0439\u0441\u0442\u0432\u0443\u0435\u0442 3 \u0447\u0430\u0441\u0430):\n`{code}`\n\u0412\u0432\u043e\u0434\u0438\u0442\u044c \u0447\u0435\u0440\u0435\u0437 `/i_am_admin`", parse_mode="Markdown")

@dp.message(Command("i_am_admin", "I_am_admin"))
async def become_admin(message: Message, state: FSMContext):
    if is_admin_user(message.from_user.id):
        return await message.reply("\u0412\u044b \u0443\u0436\u0435 \u0430\u0434\u043c\u0438\u043d!")
    await message.reply("\ud83d\udd11 \u0412\u0432\u0435\u0434\u0438\u0442\u0435 10-\u0437\u043d\u0430\u0447\u043d\u044b\u0439 \u043a\u043e\u0434, \u0432\u044b\u0434\u0430\u043d\u043d\u044b\u0439 \u0421\u0443\u043f\u0435\u0440-\u0430\u0434\u043c\u0438\u043d\u043e\u043c:")
    await state.set_state(AdminStates.waiting_for_admin_code)

@dp.message(AdminStates.waiting_for_admin_code)
async def verify_admin_code(message: Message, state: FSMContext):
    code = message.text.strip()
    res = execute_query("SELECT created_at, used FROM admin_codes WHERE code = ?", (code,), fetchone=True)
    if not res or res[1] == 1 or datetime.now() - datetime.strptime(res[0], "%Y-%m-%d %H:%M:%S") > timedelta(hours=3):
        await message.reply("\u274c \u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u0438\u043b\u0438 \u043f\u0440\u043e\u0441\u0440\u043e\u0447\u0435\u043d\u043d\u044b\u0439 \u043a\u043e\u0434.")
        await state.clear()
        return
    execute_query("UPDATE admin_codes SET used = 1 WHERE code = ?", (code,))
    execute_query("UPDATE users SET is_admin = 1 WHERE user_id = ?", (message.from_user.id,))
    await message.reply("\ud83c\udf89 \u041f\u043e\u0437\u0434\u0440\u0430\u0432\u043b\u044f\u0435\u043c! \u0412\u044b \u0442\u0435\u043f\u0435\u0440\u044c \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440.")
    await state.clear()

@dp.message(Command("card"), IsAdminFilter())
async def manage_card(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].isdigit():
        return await message.reply("\u26a0\ufe0f \u0423\u043a\u0430\u0436\u0438\u0442\u0435 ID \u043a\u0430\u0440\u0442\u044b!\n\u041f\u0440\u0438\u043c\u0435\u0440: `/card 5`", parse_mode="Markdown")
    p_id = int(args[1])
    photo = execute_query("SELECT id, file_id, rarity, description FROM photos WHERE id = ?", (p_id,), fetchone=True)
    if not photo:
        return await message.reply(f"\u274c \u041a\u0430\u0440\u0442\u0430 #{p_id} \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430.")
    
    _, f_id, rar, desc = photo
    caption = f"\u2699\ufe0f *\u0423\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u043a\u0430\u0440\u0442\u043e\u0439 #{p_id}*\n\n\u2b50 \u0420\u0435\u0434\u043a\u043e\u0441\u0442\u044c: {rar.capitalize()}\n\ud83d\udcdd \u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435: {desc}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u270f\ufe0f \u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u0440\u0435\u0434\u043a\u043e\u0441\u0442\u044c", callback_data=f"edit_rar:{p_id}")],
        [InlineKeyboardButton(text="\ud83d\udcdd \u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u043e\u043f\u0438\u0441\u0430\u043d\u0438\u0435", callback_data=f"edit_desc:{p_id}")],
        [InlineKeyboardButton(text="\ud83d\uddd1 \u0423\u0414\u0410\u041b\u0418\u0422\u042c \u041a\u0410\u0420\u0422\u0423", callback_data=f"del_card:{p_id}")]
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
    await call.answer(f"\u2705 \u0420\u0435\u0434\u043a\u043e\u0441\u0442\u044c \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0430 \u043d\u0430 {new_rar}!", show_alert=True)
    await call.message.delete()

@dp.callback_query(F.data.startswith("edit_desc:"), IsAdminFilter())
async def edit_desc_prompt(call: CallbackQuery, state: FSMContext):
    p_id = int(call.data.split(":")[1])
    await state.update_data(edit_p_id=p_id)
    await call.message.reply(f"\u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u043d\u043e\u0432\u043e\u0435 \u0442\u0435\u043a\u0441\u0442\u043e\u0432\u043e\u0435 \u043e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u0434\u043b\u044f \u043a\u0430\u0440\u0442\u044b #{p_id}:")
    await state.set_state(AdminStates.edit_waiting_desc)
    await call.answer()

@dp.message(AdminStates.edit_waiting_desc, IsAdminFilter())
async def save_new_desc(message: Message, state: FSMContext):
    d = await state.get_data()
    p_id = d.get("edit_p_id")
    execute_query("UPDATE photos SET description = ? WHERE id = ?", (message.text, p_id))
    await message.reply(f"\u2705 \u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043a\u0430\u0440\u0442\u044b #{p_id} \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u043e!")
    await state.clear()

@dp.callback_query(F.data.startswith("del_card:"), IsAdminFilter())
async def del_card_action(call: CallbackQuery):
    p_id = int(call.data.split(":")[1])
    execute_query("DELETE FROM photos WHERE id = ?", (p_id,))
    execute_query("DELETE FROM user_photos WHERE photo_id = ?", (p_id,))
    await call.message.delete()
    await call.answer(f"\ud83d\uddd1 \u041a\u0430\u0440\u0442\u0430 #{p_id} \u0443\u0434\u0430\u043b\u0435\u043d\u0430!", show_alert=True)

# ----------------------------------------------------------------------
# MINI APP (ÐÐÐ-ÐÐ ÐÐÐÐÐÐÐÐ Ð TELEGRAM)
# ----------------------------------------------------------------------
@dp.message(Command("app", "menu"))
async def open_webapp(message: Message):
    webapp_url = os.getenv("WEBAPP_URL", "https://example.com/fembot_webapp")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\ud83d\udcf1 \u041e\u0442\u043a\u0440\u044b\u0442\u044c \u0418\u0433\u0440\u043e\u0432\u043e\u0439 \u041c\u0435\u043d\u044e-\u0426\u0435\u043d\u0442\u0440 (Mini App)", web_app=WebAppInfo(url=webapp_url))]
    ])
    await message.reply("\ud83c\udfae \u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u043a\u043d\u043e\u043f\u043a\u0443 \u043d\u0438\u0436\u0435, \u0447\u0442\u043e\u0431\u044b \u043e\u0442\u043a\u0440\u044b\u0442\u044c \u0438\u043d\u0442\u0435\u0440\u0430\u043a\u0442\u0438\u0432\u043d\u043e\u0435 Telegram Mini App \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435:", reply_markup=kb)

# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
async def main():
    init_db()
    await bot.set_my_commands([
        BotCommand(command="foto", description="\u041f\u043e\u043b\u0443\u0447\u0438\u0442\u044c \u0444\u043e\u0442\u043e (\u0440\u0430\u0437 \u0432 3 \u0447\u0430\u0441\u0430)"),
        BotCommand(command="profil", description="\u041f\u0440\u043e\u0444\u0438\u043b\u044c \u0438 \u043a\u043e\u043b\u043b\u0435\u043a\u0446\u0438\u044f"),
        BotCommand(command="daily", description="\u0415\u0436\u0435\u0434\u043d\u0435\u0432\u043d\u044b\u0439 \u0431\u043e\u043d\u0443\u0441 \u043c\u043e\u043d\u0435\u0442"),
        BotCommand(command="shop", description="\u041c\u0430\u0433\u0430\u0437\u0438\u043d \u0443\u043b\u0443\u0447\u0448\u0435\u043d\u0438\u0439"),
        BotCommand(command="craft", description="\u0421\u0438\u043d\u0442\u0435\u0437 (\u043a\u0440\u0430\u0444\u0442 \u043a\u0430\u0440\u0442)"),
        BotCommand(command="top", description="\u0422\u043e\u043f-10 \u0438\u0433\u0440\u043e\u043a\u043e\u0432"),
        BotCommand(command="promo", description="\u0410\u043a\u0442\u0438\u0432\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043f\u0440\u043e\u043c\u043e\u043a\u043e\u0434"),
        BotCommand(command="admins", description="[\u0421\u0443\u043f\u0435\u0440-\u0430\u0434\u043c\u0438\u043d] \u0423\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u0430\u0434\u043c\u0438\u043d\u0430\u043c\u0438"),
        BotCommand(command="admin", description="\u0418\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f \u043e\u0431 \u0430\u0434\u043c\u0438\u043d\u0430\u0445")
    ])
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
