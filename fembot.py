# -*- coding: utf-8 -*-
import asyncio
import logging
import sqlite3
import random
import string
import os
import re
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types, BaseMiddleware
from aiogram.filters import Command, BaseFilter, or_f
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
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ КОДИРОВКИ (ИСПРАВЛЕНИЕ "КРАКОЗЯБР")
# ----------------------------------------------------------------------
def fix_mojibake(text: str) -> str:
    """
    Автоматически восстанавливает русский текст, если он был случайно
    сохранен или прочитан в кодировке Latin-1 / Windows-1252 вместо UTF-8.
    """
    if not text or not isinstance(text, str):
        return text or ""
    try:
        if any(marker in text for marker in ("Ð", "Ñ", "ðŸ", "â€", "Ã")):
            decoded = text.encode("latin-1").decode("utf-8")
            return decoded
    except Exception:
        pass
    return text

def clean_surrogates(text: str) -> str:
    """Очищает текст от суррогатных символов и исправляет кодировку."""
    if not text:
        return ""
    fixed = fix_mojibake(str(text))
    return fixed.encode("utf-8", "ignore").decode("utf-8")

# ----------------------------------------------------------------------
# КОНФИГУРАЦИЯ И ПУТИ К БАЗЕ ДАННЫХ
# ----------------------------------------------------------------------
API_TOKEN = os.getenv("BOT_TOKEN", "")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0"))

# Абсолютный путь к файлу БД, чтобы гарантировать постоянство данных при любых перезапусках
DB_NAME = os.getenv("DB_PATH", os.path.abspath("fembot_database.db"))

db_dir = os.path.dirname(DB_NAME)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Названия редкостей
RARITY_RARE = "редкое"
RARITY_ULTRA = "ультра редкое"
RARITY_MYTHIC = "мифическое"
RARITY_LEGENDARY = "легендарное"
RARITY_ARTIFACT = "фембойский артефакт"
RARITY_RAREST = "редчайший фем"

# ----------------------------------------------------------------------
# ОБНОВЛЕННЫЙ БАЛАНС ДРОПА (ШКАЛА 1000 ЕДИНИЦ)
# ----------------------------------------------------------------------
# Редкое:               580 / 1000 = 58.0%  (основной дроп)
# Ультра редкое:        280 / 1000 = 28.0%  (частый дроп)
# Мифическое:           100 / 1000 = 10.0%  (приятная редкость)
# Легендарное:           30 / 1000 =  3.0%  (тяжело выбить)
# Фембойский артефакт:    9 / 1000 =  0.9%  (очень тяжело, < 1%)
# Редчайший фем:          1 / 1000 =  0.1%  (джекпот, 1 из 1000!)
DEFAULT_WEIGHTS = {
    RARITY_RARE: 580,
    RARITY_ULTRA: 280,
    RARITY_MYTHIC: 100,
    RARITY_LEGENDARY: 30,
    RARITY_ARTIFACT: 9,
    RARITY_RAREST: 1
}

LUCKY_WEIGHTS = {
    RARITY_RARE: 420,
    RARITY_ULTRA: 340,
    RARITY_MYTHIC: 160,
    RARITY_LEGENDARY: 60,
    RARITY_ARTIFACT: 17,
    RARITY_RAREST: 3
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
# БАЗА ДАННЫХ (SQLITE С WAL-РЕЖИМОМ ДЛЯ НАДЕЖНОГО СОХРАНЕНИЯ)
# ----------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Включаем WAL-режим для защиты базы от сбоев при внезапных редеплоях
    cursor.execute("PRAGMA journal_mode=WAL;")
    
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
        ("Администрация еще не настроила этот текст.",)
    )
    
    cursor.execute("SELECT value FROM settings WHERE key = 'admin_info'")
    row = cursor.fetchone()
    if row and row[0]:
        fixed_val = fix_mojibake(row[0])
        if fixed_val != row[0]:
            cursor.execute("UPDATE settings SET value = ? WHERE key = 'admin_info'", (fixed_val,))

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
        try:
            cursor.execute(m)
        except Exception:
            pass

    if SUPER_ADMIN_ID != 0:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, is_admin, joined_at) VALUES (?, ?, 1, ?)",
            (SUPER_ADMIN_ID, "SuperAdmin", now_str)
        )
        cursor.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (SUPER_ADMIN_ID,))

    conn.commit()
    conn.close()

def execute_query(query, params=(), fetchone=False, fetchall=False, commit=True):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(query, params)
    res = cursor.fetchone() if fetchone else (cursor.fetchall() if fetchall else None)
    if commit:
        conn.commit()
    conn.close()
    return res

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
# AIOGRAM MIDDLEWARE И СОСТОЯНИЯ (FSM)
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
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ----------------------------------------------------------------------
def resolve_target_user(message: Message, explicit_arg: str = None):
    if explicit_arg:
        explicit_arg = explicit_arg.strip()
        if explicit_arg.startswith("@"):
            uname = explicit_arg[1:]
            row = execute_query("SELECT user_id, username FROM users WHERE LOWER(username) = LOWER(?)", (uname,), fetchone=True)
            if row:
                return row[0], clean_surrogates(row[1])
        elif explicit_arg.isdigit():
            uid = int(explicit_arg)
            row = execute_query("SELECT user_id, username FROM users WHERE user_id = ?", (uid,), fetchone=True)
            if row:
                return row[0], clean_surrogates(row[1])
            return uid, str(uid)
        else:
            row = execute_query("SELECT user_id, username FROM users WHERE LOWER(username) = LOWER(?)", (explicit_arg,), fetchone=True)
            if row:
                return row[0], clean_surrogates(row[1])

    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        register_user_and_chat(target.id, target.username or target.first_name or "Unknown", message.chat.id, message.chat.type)
        return target.id, clean_surrogates(target.username or target.full_name)

    return None, None

def get_effective_cooldown_hours(user_id):
    res = execute_query("SELECT penalty_extra_hours, penalty_expires FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    if not res:
        return 3
    extra_h, exp_str = res[0] or 0, res[1]
    if exp_str:
        if datetime.now() < datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S"):
            return 3 + extra_h
        else:
            execute_query("UPDATE users SET penalty_extra_hours = 0, penalty_expires = NULL WHERE user_id = ?", (user_id,))
    return 3

# ----------------------------------------------------------------------
# ЭКОНОМИКА И МАГАЗИН
# ----------------------------------------------------------------------
def get_user_coins(user_id):
    res = execute_query("SELECT coins FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    return res[0] if res else 0

def add_coins(user_id, amount):
    execute_query("UPDATE users SET coins = MAX(0, coins + ?) WHERE user_id = ?", (amount, user_id))

# --- ЕЖЕДНЕВНЫЙ БОНУС (/daily ИЛИ "дейлик") ---
@dp.message(or_f(Command("daily"), F.text.lower() == "дейлик"))
async def cmd_daily(message: Message):
    user_id = message.from_user.id
    res = execute_query("SELECT last_daily, daily_streak FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    last_daily_str, streak = res[0], res[1] or 0
    now = datetime.now()
    
    if last_daily_str:
        last_daily = datetime.strptime(last_daily_str, "%Y-%m-%d %H:%M:%S")
        if now - last_daily < timedelta(hours=24):
            rem = timedelta(hours=24) - (now - last_daily)
            hours, rem_sec = divmod(rem.seconds, 3600)
            return await message.reply(
                f"⏳ Вы уже забирали бонус! Приходите через {hours} ч. {rem_sec//60} мин."
            )
        if now - last_daily > timedelta(hours=48):
            streak = 0

    new_streak = streak + 1
    reward = min(10 + (new_streak * 5), 100)
    add_coins(user_id, reward)
    execute_query(
        "UPDATE users SET last_daily = ?, daily_streak = ? WHERE user_id = ?",
        (now.strftime("%Y-%m-%d %H:%M:%S"), new_streak, user_id)
    )
    await message.reply(
        f"🎁 Награда получена! +{reward} коинов 🪙\nСерия: {new_streak} дн."
    )

# --- МАГАЗИН (/shop ИЛИ "магазин") ---
@dp.message(or_f(Command("shop"), F.text.lower() == "магазин"))
async def cmd_shop(message: Message):
    if message.chat.type != "private":
        return await message.reply("🛍 Магазин доступен только в личных сообщениях с ботом! Напишите мне там.")
    
    coins = get_user_coins(message.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡️ Сброс КД (100 🪙)", callback_data="buy_reset")],
        [InlineKeyboardButton(text="🍀 Эликсир удачи x2 (250 🪙)", callback_data="buy_potion")],
        [InlineKeyboardButton(text="📦 Случайная Эпическая карта (350 🪙)", callback_data="buy_epic")]
    ])
    await message.answer(f"🛒 *Магазин Фем-товаров*\n\nВаш баланс: {coins} 🪙", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "buy_reset")
async def buy_reset(call: CallbackQuery):
    user_id = call.from_user.id
    if get_user_coins(user_id) < 100:
        return await call.answer("❌ Недостаточно коинов!", show_alert=True)
    add_coins(user_id, -100)
    execute_query("UPDATE users SET last_foto_time = NULL WHERE user_id = ?", (user_id,))
    await call.message.edit_text("⚡️ Кулдаун успешно сброшен! Теперь вы можете использовать /foto.")

@dp.callback_query(F.data == "buy_potion")
async def buy_potion(call: CallbackQuery):
    user_id = call.from_user.id
    if get_user_coins(user_id) < 250:
        return await call.answer("❌ Недостаточно коинов!", show_alert=True)
    add_coins(user_id, -250)
    expires = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    execute_query("UPDATE users SET luck_potion_expires = ? WHERE user_id = ?", (expires, user_id))
    await call.message.edit_text("🍀 Вы купили *Эликсир удачи*! В течение следующих 2 часов шансы на легендарные и мифические карты значительно выше.", parse_mode="Markdown")

@dp.callback_query(F.data == "buy_epic")
async def buy_epic(call: CallbackQuery):
    user_id = call.from_user.id
    if get_user_coins(user_id) < 350:
        return await call.answer("❌ Недостаточно коинов!", show_alert=True)
    
    epic_pool = execute_query(
        "SELECT id, file_id, rarity, description FROM photos WHERE rarity IN (?, ?, ?)",
        (RARITY_MYTHIC, RARITY_LEGENDARY, RARITY_ARTIFACT),
        fetchall=True
    )
    if not epic_pool:
        epic_pool = execute_query("SELECT id, file_id, rarity, description FROM photos", fetchall=True)
    if not epic_pool:
        return await call.answer("❌ В базе бота пока нет подходящих карт!", show_alert=True)
        
    p_id, f_id, rar, desc = random.choice(epic_pool)
    add_coins(user_id, -350)
    
    clean_desc = fix_mojibake(desc or "")
    clean_rar = fix_mojibake(rar or "")
    
    exists = execute_query("SELECT count FROM user_photos WHERE user_id = ? AND photo_id = ?", (user_id, p_id), fetchone=True)
    if exists:
        execute_query("UPDATE user_photos SET count = count + 1 WHERE user_id = ? AND photo_id = ?", (user_id, p_id))
    else:
        execute_query("INSERT INTO user_photos (user_id, photo_id, count) VALUES (?, ?, 1)", (user_id, p_id))
        
    await call.message.answer_photo(
        f_id,
        caption=f"📦 *Покупка из магазина:*\n⭐ Редкость: {clean_rar.capitalize()}\n📝 {clean_desc}",
        parse_mode="Markdown"
    )
    await call.answer("Успешная покупка!")

# ----------------------------------------------------------------------
# СИНТЕЗ КАРТ (/craft ИЛИ "крафт")
# ----------------------------------------------------------------------
@dp.message(or_f(Command("craft"), F.text.lower() == "крафт"))
async def cmd_craft(message: Message):
    if message.chat.type != "private":
        return await message.reply("🛠 Крафт доступен только в ЛС.")
    user_id = message.from_user.id
    counts = execute_query(
        "SELECT p.rarity, SUM(up.count) FROM user_photos up JOIN photos p ON up.photo_id = p.id WHERE up.user_id = ? GROUP BY p.rarity",
        (user_id,),
        fetchall=True
    )
    
    btns = []
    for rar, total in counts:
        rar_clean = fix_mojibake(rar)
        if total >= 3 and rar_clean in CRAFT_PROGRESSION[:-1]:
            btns.append([InlineKeyboardButton(text=f"{rar_clean.capitalize()} (x3 ➔ 1 выше)", callback_data=f"do_craft:{rar_clean}")])
            
    if not btns:
        return await message.reply("🛠 У вас недостаточно дубликатов (нужно минимум 3 карты одной редкости для крафта следующего ранга).")
    await message.answer("🛠 *Меню Синтеза*\nВыберите, что переработать:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="Markdown")

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
        return await call.answer("❌ Недостаточно карт!")

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
        await call.message.edit_text(f"✨ Успешный крафт! Получена карта: *{target.upper()}*!", parse_mode="Markdown")
    else:
        add_coins(user_id, 50)
        await call.message.edit_text("🎁 Крафт успешен! Вам начислено 50 коинов.")

# ----------------------------------------------------------------------
# ОСНОВНАЯ ИГРОВАЯ ЛОГИКА (/foto И ТРИГГЕРЫ)
# ----------------------------------------------------------------------
@dp.message(or_f(Command("foto"), F.text.lower() == "фем"))
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
                f"⏳ Вы уже брали фото! Ваш кулдаун: *{effective_cooldown_h} ч.*\n"
                f"Попробуйте снова через: *{hours:02d}:{minutes:02d}:{seconds:02d}*.\n\n"
                f"💡 Используйте `/promo КОД` или `/shop` для сброса!",
                parse_mode="Markdown"
            )

    luck_expires = res[1] if res else None
    is_lucky = False
    if luck_expires and datetime.strptime(luck_expires, "%Y-%m-%d %H:%M:%S") > now:
        is_lucky = True
        weights = LUCKY_WEIGHTS.copy()
    else:
        weights = DEFAULT_WEIGHTS.copy()

    chosen = random.choices(list(weights.keys()), weights=list(weights.values()), k=1)[0]
    photo = execute_query("SELECT id, file_id, rarity, description FROM photos WHERE rarity = ? ORDER BY RANDOM() LIMIT 1", (chosen,), fetchone=True)
    if not photo:
        photo = execute_query("SELECT id, file_id, rarity, description FROM photos ORDER BY RANDOM() LIMIT 1", fetchone=True)
    
    if not photo:
        return await message.reply("В базе бота пока нет фотографий.")
    
    p_id, f_id, rar, desc = photo
    rar = fix_mojibake(rar)
    desc = fix_mojibake(desc)
    
    is_dub = execute_query("SELECT 1 FROM user_photos WHERE user_id = ? AND photo_id = ?", (user_id, p_id), fetchone=True)
    
    prefix = "🍀 *Эликсир удачи сработал!*\n" if is_lucky else ""
    
    if rar == RARITY_RAREST:
        celebration = "🌟🔥 **НЕВЕРОЯТНЫЙ ДЖЕКПОТ! ВЫБИТ РЕДЧАЙШИЙ ФЕМ (0.1%)!** 🔥🌟\n\n"
    elif rar == RARITY_ARTIFACT:
        celebration = "✨💎 **АРТЕФАКТНЫЙ ДРОП! (0.9%)!** 💎✨\n\n"
    elif rar == RARITY_LEGENDARY:
        celebration = "👑 **ЛЕГЕНДАРНЫЙ ДРОП!** 👑\n\n"
    else:
        celebration = ""

    if is_dub:
        execute_query("UPDATE user_photos SET count = count + 1 WHERE user_id = ? AND photo_id = ?", (user_id, p_id))
        add_coins(user_id, 15)
        caption = f"{celebration}{prefix}🃏 *ДУБЛИКАТ!*\n\n+15 коинов 🪙\n⭐ {rar.capitalize()}\n🆔 Карты: #{p_id}"
    else:
        execute_query("INSERT INTO user_photos (user_id, photo_id, count) VALUES (?, ?, 1)", (user_id, p_id))
        caption = f"{celebration}{prefix}🎉 *НОВАЯ КАРТА!*\n\n⭐ Редкость: {rar.capitalize()}\n📝 {desc}\n🆔 Карты: #{p_id}"

    execute_query("UPDATE users SET last_foto_time = ? WHERE user_id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), user_id))
    await bot.send_photo(message.chat.id, f_id, caption=caption, parse_mode="Markdown")

    if message.chat.type != "private" and random.random() < 0.10:
        reward = random.randint(20, 50)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📦 ОТКРЫТЬ!", callback_data=f"get_chest:{reward}")]])
        await bot.send_message(message.chat.id, "🎁 *ПОДАРОК В ЧАТЕ!*\nКто-то уронил сундук с монетами. Жми!", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("get_chest:"))
async def get_chest(call: CallbackQuery):
    reward = int(call.data.split(":")[1])
    add_coins(call.from_user.id, reward)
    uname = clean_surrogates(call.from_user.username or str(call.from_user.id))
    await call.message.edit_text(f"🎉 Сундук открыл @{uname}!\nНаграда: {reward} 🪙")

# ----------------------------------------------------------------------
# ПРОФИЛЬ И КОЛЛЕКЦИЯ (/profil ИЛИ "профиль")
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
        if p.total_count > 0:
            avatar = p.photos[0][-1].file_id
    except Exception:
        pass
    
    extra_info = ""
    if potion and datetime.strptime(potion, "%Y-%m-%d %H:%M:%S") > datetime.now():
        extra_info += "\n🍀 *Эликсир удачи активен!*"
        
    if penalty_exp and datetime.strptime(penalty_exp, "%Y-%m-%d %H:%M:%S") > datetime.now():
        extra_info += f"\n⚠️ *Штраф КД:* +{penalty_h} ч. (до {penalty_exp})"

    clean_name = clean_surrogates(target_name)
    text = f"👤 *ПРОФИЛЬ: {clean_name}*\n💰 Коины: {coins} 🪙\n🗂 Всего карт: {total}\n✨ Уникальных: {unique}{extra_info}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🖼 Коллекция", callback_data=f"view_coll_user:{target_id}:0")]])
    await bot.send_photo(send_to_chat_id, photo=avatar, caption=text, reply_markup=kb, parse_mode="Markdown", reply_to_message_id=reply_to_msg_id)

@dp.message(or_f(Command("profil"), F.text.lower() == "профиль"))
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
    photos = execute_query(
        "SELECT p.id, p.file_id, p.rarity, p.description, up.count FROM user_photos up JOIN photos p ON up.photo_id = p.id WHERE up.user_id = ?",
        (user_id,),
        fetchall=True
    )
    if not photos:
        return await call.answer("В коллекции пока нет карт!", show_alert=True)
    
    sorted_p = sorted(photos, key=lambda x: RARITY_ORDER.get(fix_mojibake(x[2]).lower(), 99))
    idx = max(0, min(idx, len(sorted_p) - 1))
    p_id, f_id, rar, desc, cnt = sorted_p[idx]
    
    rar = fix_mojibake(rar)
    desc = fix_mojibake(desc)
    
    text = f"🖼 *Коллекция* ({idx+1}/{len(sorted_p)})\n🆔 ID: #{p_id}\n⭐ Редкость: {rar.capitalize()}\n📝 {desc}\n📦 В наличии: {cnt} шт."
    btns, nav = [], []
    if idx > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"view_coll_user:{user_id}:{idx-1}"))
    if idx < len(sorted_p) - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"view_coll_user:{user_id}:{idx+1}"))
    if nav:
        btns.append(nav)
    btns.append([InlineKeyboardButton(text="👤 Профиль", callback_data=f"back_prof_user:{user_id}")])
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
        extra_info += "\n🍀 *Эликсир удачи активен!*"
    if penalty_exp and datetime.strptime(penalty_exp, "%Y-%m-%d %H:%M:%S") > datetime.now():
        extra_info += f"\n⚠️ *Штраф КД:* +{penalty_h} ч. (до {penalty_exp})"
        
    text = f"👤 *ПРОФИЛЬ: @{uname}*\n💰 Коины: {coins} 🪙\n🗂 Всего карт: {total}\n✨ Уникальных: {unique}{extra_info}"
    await call.message.edit_media(
        InputMediaPhoto(media=DEFAULT_AVATAR, caption=text, parse_mode="Markdown"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🖼 Коллекция", callback_data=f"view_coll_user:{target_id}:0")]])
    )

# ----------------------------------------------------------------------
# СИСТЕМА НАКАЗАНИЙ И ПООЩРЕНИЙ (МОДЕРАЦИЯ И ВЫДАЧА)
# ----------------------------------------------------------------------
@dp.message(F.text.lower().startswith("твой профиль"), IsAdminFilter())
async def admin_view_target_profile(message: Message):
    parts = message.text.split(maxsplit=2)
    explicit_user = parts[2] if len(parts) > 2 else None
    t_id, t_name = resolve_target_user(message, explicit_user)
    if not t_id:
        return await message.reply("⚠️ Укажите пользователя через @юзернейм или ответом на сообщение!")
    await show_profile_for_user(t_id, t_name, message.chat.id, message.message_id)

# --- ПООЩРЕНИЕ: ВЫДАЧА КАРТЫ (ФОТО) ---
@dp.message(F.text.lower().startswith(("/бот +карта", "!бот +карта", "/бот +фото", "!бот +фото")), IsAdminFilter())
async def admin_add_card(message: Message):
    parts = message.text.split()
    if len(parts) < 3 or not parts[2].isdigit():
        return await message.reply("⚠️ Формат: `/бот +карта <ID> [@юзер]`", parse_mode="Markdown")
        
    card_id = int(parts[2])
    explicit_user = parts[3] if len(parts) > 3 else None
    t_id, t_name = resolve_target_user(message, explicit_user)
    if not t_id:
        return await message.reply("⚠️ Укажите @юзернейм или ответьте на сообщение!")
        
    card = execute_query("SELECT id, rarity, description, file_id FROM photos WHERE id = ?", (card_id,), fetchone=True)
    if not card:
        return await message.reply(f"❌ Карты #{card_id} не существует в базе бота!")
        
    rar = fix_mojibake(card[1])
    desc = fix_mojibake(card[2])
    
    exists = execute_query("SELECT count FROM user_photos WHERE user_id = ? AND photo_id = ?", (t_id, card_id), fetchone=True)
    if exists:
        execute_query("UPDATE user_photos SET count = count + 1 WHERE user_id = ? AND photo_id = ?", (t_id, card_id))
    else:
        execute_query("INSERT INTO user_photos (user_id, photo_id, count) VALUES (?, ?, 1)", (t_id, card_id))
        
    await message.reply(
        f"🎁 Карта #{card_id} (*{rar.capitalize()}*) успешно выдана игроку *{clean_surrogates(t_name)}*!\n📝 {desc}",
        parse_mode="Markdown"
    )

# --- ПООЩРЕНИЕ: ВЫДАЧА ФЕМКОИНОВ ---
@dp.message(F.text.lower().startswith(("/бот +фемкоины", "!бот +фемкоины", "/бот +коины", "!бот +коины")), IsAdminFilter())
async def admin_add_coins(message: Message):
    parts = message.text.split()
    if len(parts) < 3 or not parts[2].isdigit():
        return await message.reply("⚠️ Формат: `/бот +фемкоины 300 [@юзер]`", parse_mode="Markdown")
        
    amount = int(parts[2])
    explicit_user = parts[3] if len(parts) > 3 else None
    t_id, t_name = resolve_target_user(message, explicit_user)
    if not t_id:
        return await message.reply("⚠️ Укажите @юзернейм или ответьте на сообщение!")
        
    add_coins(t_id, amount)
    new_bal = get_user_coins(t_id)
    await message.reply(
        f"💰 Игроку *{clean_surrogates(t_name)}* успешно начислено *+{amount}* фемкоинов 🪙.\nТекущий баланс: *{new_bal}* 🪙",
        parse_mode="Markdown"
    )

# --- ПООЩРЕНИЕ: СБРОС КУЛДАУНА И СНЯТИЕ ШТРАФА (-КД) ---
@dp.message(F.text.lower().startswith(("/бот -кд", "!бот -кд", "/бот сброс кд", "!бот сброс кд")), IsAdminFilter())
async def admin_reset_cooldown(message: Message):
    parts = message.text.split()
    explicit_user = None
    if len(parts) >= 4 and parts[1].lower() == "сброс":
        explicit_user = parts[3]
    elif len(parts) >= 3:
        explicit_user = parts[2]
        
    t_id, t_name = resolve_target_user(message, explicit_user)
    if not t_id:
        return await message.reply("⚠️ Укажите @юзернейм или ответьте на сообщение!")
        
    execute_query("UPDATE users SET last_foto_time = NULL, penalty_extra_hours = 0, penalty_expires = NULL WHERE user_id = ?", (t_id,))
    await message.reply(
        f"⚡️ Кулдаун и все штрафы для игрока *{clean_surrogates(t_name)}* успешно аннулированы!",
        parse_mode="Markdown"
    )

# --- ШТРАФ: ИЗЪЯТИЕ КАРТЫ ---
@dp.message(F.text.lower().startswith(("/бот -карта", "!бот -карта", "/бот -фото", "!бот -фото")), IsAdminFilter())
async def admin_remove_card(message: Message):
    parts = message.text.split()
    if len(parts) < 3 or not parts[2].isdigit():
        return await message.reply("⚠️ Формат: `/бот -карта <ID> [@юзер]`", parse_mode="Markdown")
        
    card_id = int(parts[2])
    explicit_user = parts[3] if len(parts) > 3 else None
    t_id, t_name = resolve_target_user(message, explicit_user)
    if not t_id:
        return await message.reply("⚠️ Укажите @юзернейм или ответьте на сообщение!")
        
    has_card = execute_query("SELECT count FROM user_photos WHERE user_id = ? AND photo_id = ?", (t_id, card_id), fetchone=True)
    if not has_card:
        return await message.reply(f"❌ У игрока {clean_surrogates(t_name)} нет карты #{card_id}!")
        
    cnt = has_card[0]
    if cnt <= 1:
        execute_query("DELETE FROM user_photos WHERE user_id = ? AND photo_id = ?", (t_id, card_id))
    else:
        execute_query("UPDATE user_photos SET count = count - 1 WHERE user_id = ? AND photo_id = ?", (t_id, card_id))
        
    await message.reply(f"🗑 Карта #{card_id} успешно изъята у игрока *{clean_surrogates(t_name)}*!", parse_mode="Markdown")

# --- ШТРАФ: СПИСАНИЕ ФЕМКОИНОВ ---
@dp.message(F.text.lower().startswith(("/бот -фемкоины", "!бот -фемкоины", "/бот -коины", "!бот -коины")), IsAdminFilter())
async def admin_remove_coins(message: Message):
    parts = message.text.split()
    if len(parts) < 3 or not parts[2].isdigit():
        return await message.reply("⚠️ Формат: `/бот -фемкоины 300 [@юзер]`", parse_mode="Markdown")
        
    amount = int(parts[2])
    explicit_user = parts[3] if len(parts) > 3 else None
    t_id, t_name = resolve_target_user(message, explicit_user)
    if not t_id:
        return await message.reply("⚠️ Укажите @юзернейм или ответьте на сообщение!")
        
    add_coins(t_id, -amount)
    new_bal = get_user_coins(t_id)
    await message.reply(f"💸 У игрока *{clean_surrogates(t_name)}* списано *{amount}* фемкоинов 🪙.\nТекущий баланс: *{new_bal}* 🪙", parse_mode="Markdown")

# --- ШТРАФ: УВЕЛИЧЕНИЕ КУЛДАУНА (+КД) ---
@dp.message(F.text.regexp(r"^[/!](?i:бот)\s+\+\d+\s*(?i:кд)"), IsAdminFilter())
async def admin_penalty_kd(message: Message):
    text = message.text.strip()
    match = re.search(r"[/!]бот\s+\+(\d+)\s*кд\s+(\d+)\s*(часа|часов|час|ч|дней|дня|д)(?:\s+(@\w+|\d+))?", text, re.IGNORECASE)
    if not match:
        return await message.reply("⚠️ Формат: `/бот +3кд 6 часов [@юзер]`", parse_mode="Markdown")
        
    extra_h = int(match.group(1))
    dur_num = int(match.group(2))
    dur_unit = match.group(3).lower()
    explicit_user = match.group(4)
    
    t_id, t_name = resolve_target_user(message, explicit_user)
    if not t_id:
        return await message.reply("⚠️ Пользователь не найден. Укажите @юзернейм или ответьте на сообщение!")
        
    if "д" in dur_unit:
        dur_delta = timedelta(days=dur_num)
    else:
        dur_delta = timedelta(hours=dur_num)
        
    exp_time = (datetime.now() + dur_delta).strftime("%Y-%m-%d %H:%M:%S")
    execute_query("UPDATE users SET penalty_extra_hours = ?, penalty_expires = ? WHERE user_id = ?", (extra_h, exp_time, t_id))
    
    total_kd = 3 + extra_h
    await message.reply(
        f"⛔️ *Наказание применено!*\n\n"
        f"👤 Игрок: *{clean_surrogates(t_name)}*\n"
        f"⏳ Кулдаун: *{total_kd} ч.* (3 ч. + {extra_h} ч. штрафа)\n"
        f"⌛️ Срок действия штрафа: до *{exp_time}*",
        parse_mode="Markdown"
    )

# ----------------------------------------------------------------------
# ОБМЕН И ТОП
# ----------------------------------------------------------------------
@dp.message(F.text.lower() == "обмен")
async def trade_init(message: Message):
    if not message.reply_to_message:
        return await message.reply("Ответьте `Обмен` на сообщение игрока!")
    target = message.reply_to_message.from_user
    if target.id == message.from_user.id:
        return
    me = await bot.get_me()
    uname = clean_surrogates(target.username or target.first_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🤝 В ЛС", url=f"https://t.me/{me.username}?start=trade")]])
    await message.reply(f"🤝 Обмен с @{uname}! Механика в ЛС.", reply_markup=kb)

@dp.message(or_f(Command("top"), F.text.lower().in_(["топ", "статистика"])))
async def cmd_top(message: Message):
    rows = execute_query(
        "SELECT u.username, u.user_id, SUM(up.count) as c, u.joined_at FROM users u LEFT JOIN user_photos up ON u.user_id = up.user_id GROUP BY u.user_id ORDER BY c DESC, u.joined_at ASC LIMIT 10",
        fetchall=True
    )
    text = "🏆 *ТОП-10 ИГРОКОВ:*\n\n"
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, (name, uid, c, _) in enumerate(rows):
        m = medals[i] if i < len(medals) else f"{i+1}."
        safe_name = clean_surrogates(name or str(uid))
        text += f"{m} *{safe_name}* — 📦 {c or 0} фото\n"
    await message.reply(text, parse_mode="Markdown")

# ----------------------------------------------------------------------
# ПРОМОКОДЫ И АДМИНИСТРАТИВНЫЕ КОМАНДЫ
# ----------------------------------------------------------------------
def parse_duration(duration_str: str) -> timedelta:
    duration_str = duration_str.lower().strip()
    num = int(''.join(filter(str.isdigit, duration_str)) or 1)
    if "год" in duration_str or "лет" in duration_str:
        return timedelta(days=365 * num)
    elif "мес" in duration_str:
        return timedelta(days=30 * num)
    elif "дн" in duration_str or "дня" in duration_str:
        return timedelta(days=num)
    else:
        return timedelta(hours=num)

@dp.message(Command("creat_promo_1"), IsAdminFilter())
async def create_p1(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply("Укажите срок! Пример: `/creat_promo_1 24 часа`", parse_mode="Markdown")
    exp = (datetime.now() + parse_duration(args[1])).strftime("%Y-%m-%d %H:%M:%S")
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    execute_query("INSERT INTO promo_codes (code, promo_type, expires_at) VALUES (?, 'single', ?)", (code, exp))
    await message.reply(f"🎟 Одноразовый промокод:\n`{code}`\nСрок: до {exp}", parse_mode="Markdown")

@dp.message(Command("create_promo_2"), IsAdminFilter())
async def create_p2(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply("Укажите срок! Пример: `/create_promo_2 30 дней`", parse_mode="Markdown")
    exp = (datetime.now() + parse_duration(args[1])).strftime("%Y-%m-%d %H:%M:%S")
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    execute_query("INSERT INTO promo_codes (code, promo_type, expires_at) VALUES (?, 'multi', ?)", (code, exp))
    await message.reply(f"🎟 Многоразовый промокод:\n`{code}`\nСрок: до {exp}", parse_mode="Markdown")

@dp.message(Command("promo"))
async def activate_promo(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply("Введите код: `/promo КОД`", parse_mode="Markdown")
    code = args[1].strip().upper()
    user_id = message.from_user.id
    
    promo = execute_query("SELECT promo_type, expires_at FROM promo_codes WHERE code = ?", (code,), fetchone=True)
    if not promo or datetime.now() > datetime.strptime(promo[1], "%Y-%m-%d %H:%M:%S"):
        return await message.reply("❌ Промокод не существует или истёк.")
        
    p_type = promo[0]
    if p_type == 'single' and execute_query("SELECT COUNT(*) FROM promo_uses WHERE code = ?", (code,), fetchone=True)[0] > 0:
        return await message.reply("❌ Промокод уже использован.")
    if p_type == 'multi' and execute_query("SELECT 1 FROM promo_uses WHERE code = ? AND user_id = ?", (code, user_id), fetchone=True):
        return await message.reply("❌ Вы уже активировали этот код.")
        
    execute_query("INSERT INTO promo_uses (code, user_id) VALUES (?, ?)", (code, user_id))
    execute_query("UPDATE users SET last_foto_time = NULL WHERE user_id = ?", (user_id,))
    await message.reply("🎉 Промокод активирован! Кулдаун /foto сброшен.", parse_mode="Markdown")

@dp.message(or_f(Command("admin"), F.text.lower() == "кто админ"))
async def cmd_admin_info(message: Message):
    res = execute_query("SELECT value FROM settings WHERE key = 'admin_info'", fetchone=True)
    text = fix_mojibake(res[0]) if res and res[0] else "Информация не настроена."
    await message.reply(text)

@dp.message(Command("set_admin_text"), IsAdminFilter())
async def set_admin_text(message: Message, state: FSMContext):
    await message.reply("Отправьте новый текст для /admin:")
    await state.set_state(AdminStates.waiting_for_admin_text)

@dp.message(AdminStates.waiting_for_admin_text, IsAdminFilter())
async def save_admin_text(message: Message, state: FSMContext):
    clean_text = clean_surrogates(message.text)
    execute_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_info', ?)", (clean_text,))
    await message.reply("✅ Текст обновлен!")
    await state.clear()

@dp.message(Command("plus_foto"), IsAdminFilter())
async def pf_start(message: Message, state: FSMContext):
    await message.reply("📸 Отправьте фото:")
    await state.set_state(AdminStates.waiting_for_photo)

@dp.message(AdminStates.waiting_for_photo, IsAdminFilter(), F.photo)
async def pf_photo(message: Message, state: FSMContext):
    await state.update_data(f_id=message.photo[-1].file_id)
    kb = [[InlineKeyboardButton(text=r.capitalize(), callback_data=f"rar:{r}")] for r in DEFAULT_WEIGHTS.keys()]
    await message.reply("Выберите редкость:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(AdminStates.waiting_for_rarity)

@dp.callback_query(AdminStates.waiting_for_rarity, F.data.startswith("rar:"), IsAdminFilter())
async def pf_rar(call: CallbackQuery, state: FSMContext):
    await state.update_data(rar=call.data.split(":")[1])
    await call.message.edit_text("Теперь отправьте текстовое описание:")
    await state.set_state(AdminStates.waiting_for_description)

@dp.message(AdminStates.waiting_for_description, IsAdminFilter(), F.text)
async def pf_desc(message: Message, state: FSMContext):
    data = await state.get_data()
    clean_d = clean_surrogates(message.text)
    execute_query("INSERT INTO photos (file_id, rarity, description) VALUES (?, ?, ?)", (data['f_id'], data['rar'], clean_d))
    await message.reply("🎉 Фотография успешно добавлена в базу!")
    await state.clear()

@dp.message(Command("add"), IsAdminFilter())
async def add_broad(message: Message, state: FSMContext):
    await message.reply("📢 Режим рассылки. Отправляйте сообщения. Для старта отправьте `/save`.", parse_mode="Markdown")
    await state.update_data(msgs=[])
    await state.set_state(AdminStates.waiting_for_broadcast)

@dp.message(AdminStates.waiting_for_broadcast, IsAdminFilter())
async def collect_broad(message: Message, state: FSMContext):
    if message.text and message.text.lower().strip() == "/save":
        d = await state.get_data()
        msgs = d.get("msgs", [])
        if not msgs:
            return await message.reply("Очередь пуста.")
        
        targets = {r[0] for r in execute_query("SELECT chat_id FROM chats", fetchall=True)} | {r[0] for r in execute_query("SELECT user_id FROM users", fetchall=True)}
        await message.reply(f"🚀 Рассылка пошла по {len(targets)} адресатам...")
        succ, err = 0, 0
        for t in targets:
            try:
                for m in msgs:
                    await bot.copy_message(chat_id=t, from_chat_id=m['chat_id'], message_id=m['message_id'])
                succ += 1
                await asyncio.sleep(0.05)
            except Exception:
                err += 1
        await message.reply(f"🏁 Готово!\nУспешно: {succ}\nОшибок: {err}")
        await state.clear()
        return
        
    d = await state.get_data()
    msgs = d.get("msgs", [])
    msgs.append({"chat_id": message.chat.id, "message_id": message.message_id})
    await state.update_data(msgs=msgs)
    await message.reply(f"📥 Сообщение #{len(msgs)} добавлено в очередь.")

# ----------------------------------------------------------------------
# СУПЕР-АДМИНСКИЕ ФУНКЦИИ
# ----------------------------------------------------------------------
@dp.message(Command("admins"), IsSuperAdminFilter())
async def list_admins(message: Message):
    admins = execute_query("SELECT user_id, username, joined_at FROM users WHERE is_admin = 1", fetchall=True)
    text = "👑 *Список администраторов бота:*\n\n"
    kb_buttons = []
    for u_id, uname, joined in admins:
        safe_name = clean_surrogates(uname)
        name = f"@{safe_name}" if safe_name and safe_name != "Unknown" else f"ID: {u_id}"
        is_super = " (Супер-админ)" if u_id == SUPER_ADMIN_ID else ""
        text += f"• *{name}*{is_super}\n"
        if u_id != SUPER_ADMIN_ID:
            kb_buttons.append([InlineKeyboardButton(text=f"❌ Снять с поста {name}", callback_data=f"remove_admin:{u_id}")])
            
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons) if kb_buttons else None
    await message.reply(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("remove_admin:"), IsSuperAdminFilter())
async def remove_admin_action(call: CallbackQuery):
    target_id = int(call.data.split(":")[1])
    if target_id == SUPER_ADMIN_ID:
        return await call.answer("Нельзя снять главного супер-админа!", show_alert=True)
    execute_query("UPDATE users SET is_admin = 0 WHERE user_id = ?", (target_id,))
    await call.answer("✅ Администратор разжалован!", show_alert=True)
    await call.message.delete()

@dp.message(Command("now_admin_cod"), IsSuperAdminFilter())
async def gen_admin_code(message: Message):
    code = "".join(random.sample(string.digits, 7) + random.sample(string.ascii_letters, 3))
    execute_query("INSERT INTO admin_codes (code, created_at, used) VALUES (?, ?, 0)", (code, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    await message.reply(f"🔑 Код админа (создан Супер-админом, действует 3 часа):\n`{code}`\nВводить через `/i_am_admin`", parse_mode="Markdown")

@dp.message(Command("i_am_admin", "I_am_admin"))
async def become_admin(message: Message, state: FSMContext):
    if is_admin_user(message.from_user.id):
        return await message.reply("Вы уже админ!")
    await message.reply("🔑 Введите 10-значный код, выданный Супер-админом:")
    await state.set_state(AdminStates.waiting_for_admin_code)

@dp.message(AdminStates.waiting_for_admin_code)
async def verify_admin_code(message: Message, state: FSMContext):
    code = message.text.strip()
    res = execute_query("SELECT created_at, used FROM admin_codes WHERE code = ?", (code,), fetchone=True)
    if not res or res[1] == 1 or datetime.now() - datetime.strptime(res[0], "%Y-%m-%d %H:%M:%S") > timedelta(hours=3):
        await message.reply("❌ Неверный или просроченный код.")
        await state.clear()
        return
    execute_query("UPDATE admin_codes SET used = 1 WHERE code = ?", (code,))
    execute_query("UPDATE users SET is_admin = 1 WHERE user_id = ?", (message.from_user.id,))
    await message.reply("🎉 Поздравляем! Вы теперь администратор.")
    await state.clear()

@dp.message(Command("card"), IsAdminFilter())
async def manage_card(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].isdigit():
        return await message.reply("⚠️ Укажите ID карты!\nПример: `/card 5`", parse_mode="Markdown")
    p_id = int(args[1])
    photo = execute_query("SELECT id, file_id, rarity, description FROM photos WHERE id = ?", (p_id,), fetchone=True)
    if not photo:
        return await message.reply(f"❌ Карта #{p_id} не найдена.")
    
    _, f_id, rar, desc = photo
    rar = fix_mojibake(rar)
    desc = fix_mojibake(desc)
    
    caption = f"⚙️ *Управление картой #{p_id}*\n\n⭐ Редкость: {rar.capitalize()}\n📝 Описание: {desc}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить редкость", callback_data=f"edit_rar:{p_id}")],
        [InlineKeyboardButton(text="📝 Изменить описание", callback_data=f"edit_desc:{p_id}")],
        [InlineKeyboardButton(text="🗑 УДАЛИТЬ КАРТУ", callback_data=f"del_card:{p_id}")]
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
    await call.answer(f"✅ Редкость изменена на {new_rar}!", show_alert=True)
    await call.message.delete()

@dp.callback_query(F.data.startswith("edit_desc:"), IsAdminFilter())
async def edit_desc_prompt(call: CallbackQuery, state: FSMContext):
    p_id = int(call.data.split(":")[1])
    await state.update_data(edit_p_id=p_id)
    await call.message.reply(f"Отправьте новое текстовое описание для карты #{p_id}:")
    await state.set_state(AdminStates.edit_waiting_desc)
    await call.answer()

@dp.message(AdminStates.edit_waiting_desc, IsAdminFilter())
async def save_new_desc(message: Message, state: FSMContext):
    d = await state.get_data()
    p_id = d.get("edit_p_id")
    clean_desc = clean_surrogates(message.text)
    execute_query("UPDATE photos SET description = ? WHERE id = ?", (clean_desc, p_id))
    await message.reply(f"✅ Описание карты #{p_id} обновлено!")
    await state.clear()

@dp.callback_query(F.data.startswith("del_card:"), IsAdminFilter())
async def del_card_action(call: CallbackQuery):
    p_id = int(call.data.split(":")[1])
    execute_query("DELETE FROM photos WHERE id = ?", (p_id,))
    execute_query("DELETE FROM user_photos WHERE photo_id = ?", (p_id,))
    await call.message.delete()
    await call.answer(f"🗑 Карта #{p_id} удалена!", show_alert=True)

# ----------------------------------------------------------------------
# MINI APP (ВЕБ-ПРИЛОЖЕНИЕ В TELEGRAM)
# ----------------------------------------------------------------------
@dp.message(Command("app", "menu"))
async def open_webapp(message: Message):
    webapp_url = os.getenv("WEBAPP_URL", "https://example.com/fembot_webapp")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Открыть Игровой Меню-Центр (Mini App)", web_app=WebAppInfo(url=webapp_url))]
    ])
    await message.reply("🎮 Нажмите кнопку ниже, чтобы открыть интерактивное Telegram Mini App приложение:", reply_markup=kb)

# ----------------------------------------------------------------------
# ТОЧКА ВХОДА (MAIN)
# ----------------------------------------------------------------------
async def main():
    init_db()
    await bot.set_my_commands([
        BotCommand(command="foto", description="Получить фото (раз в 3 часа)"),
        BotCommand(command="profil", description="Профиль и коллекция"),
        BotCommand(command="daily", description="Ежедневный бонус монет"),
        BotCommand(command="shop", description="Магазин улучшений"),
        BotCommand(command="craft", description="Синтез (крафт карт)"),
        BotCommand(command="top", description="Топ-10 игроков"),
        BotCommand(command="promo", description="Активировать промокод"),
        BotCommand(command="admins", description="[Супер-админ] Управление админами"),
        BotCommand(command="admin", description="Информация об админах")
    ])
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
