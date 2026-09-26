import asyncio
import logging
import sqlite3
import random
import string
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandObject, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    Message,
    CallbackQuery,
    BotCommand
)

# ----------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------
API_TOKEN = os.getenv("BOT_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0"))
DB_NAME = os.getenv("DB_PATH", "fembot_database.db")

# Safety directory check for database
db_dir = os.path.dirname(DB_NAME)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)

# Logging configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Rarity settings
RARITIES_WEIGHTS = {
    "ÑÐµÐ´ÐºÐ¾Ðµ": 40,
    "ÑÐ»ÑÑÑÐ° ÑÐµÐ´ÐºÐ¾Ðµ": 25,
    "Ð¼Ð¸ÑÐ¸ÑÐµÑÐºÐ¾Ðµ": 15,
    "Ð»ÐµÐ³ÐµÐ½Ð´Ð°ÑÐ½Ð¾Ðµ": 12,
    "ÑÐµÐ¼Ð±Ð¾Ð¹ÑÐºÐ¸Ð¹ Ð°ÑÑÐµÑÐ°ÐºÑ": 6,
    "ÑÐµÐ´ÑÐ°Ð¹ÑÐ¸Ð¹ ÑÐµÐ¼": 2
}

RARITY_ORDER = {
    "ÑÐµÐ´ÑÐ°Ð¹ÑÐ¸Ð¹ ÑÐµÐ¼": 1,
    "ÑÐµÐ¼Ð±Ð¾Ð¹ÑÐºÐ¸Ð¹ Ð°ÑÑÐµÑÐ°ÐºÑ": 2,
    "Ð»ÐµÐ³ÐµÐ½Ð´Ð°ÑÐ½Ð¾Ðµ": 3,
    "Ð¼Ð¸ÑÐ¸ÑÐµÑÐºÐ¾Ðµ": 4,
    "ÑÐ»ÑÑÑÐ° ÑÐµÐ´ÐºÐ¾Ðµ": 5,
    "ÑÐµÐ´ÐºÐ¾Ðµ": 6
}

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
            last_foto_time TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY,
            chat_type TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT,
            rarity TEXT,
            description TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_photos (
            user_id INTEGER,
            photo_id INTEGER,
            PRIMARY KEY (user_id, photo_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (photo_id) REFERENCES photos(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_codes (
            code TEXT PRIMARY KEY,
            created_at TEXT,
            used INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_info', 'ÐÐ´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¸Ñ ÐµÑÐµ Ð½Ðµ Ð½Ð°ÑÑÑÐ¾Ð¸Ð»Ð° ÑÑÐ¾Ñ ÑÐµÐºÑÑ. ÐÑÐ¿Ð¾Ð»ÑÐ·ÑÐ¹ÑÐµ FSM Ð´Ð»Ñ ÐµÐ³Ð¾ Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸Ñ.')")
    
    if SUPER_ADMIN_ID != 0:
        cursor.execute("INSERT OR IGNORE INTO users (user_id, username, is_admin) VALUES (?, ?, 1)", (SUPER_ADMIN_ID, "SuperAdmin"))
        cursor.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (SUPER_ADMIN_ID,))
    
    conn.commit()
    conn.close()

def execute_query(query, params=(), fetchone=False, fetchall=False, commit=True):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(query, params)
    result = None
    if fetchone:
        result = cursor.fetchone()
    elif fetchall:
        result = cursor.fetchall()
    if commit:
        conn.commit()
    conn.close()
    return result

def register_user_and_chat(user_id: int, username: str, chat_id: int, chat_type: str):
    user_exists = execute_query("SELECT user_id FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    if not user_exists:
        execute_query(
            "INSERT INTO users (user_id, username, is_admin, last_foto_time) VALUES (?, ?, 0, NULL)", 
            (user_id, username)
        )
    else:
        execute_query("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
        
    chat_exists = execute_query("SELECT chat_id FROM chats WHERE chat_id = ?", (chat_id,), fetchone=True)
    if not chat_exists:
        execute_query("INSERT INTO chats (chat_id, chat_type) VALUES (?, ?)", (chat_id, chat_type))

def is_admin_user(user_id: int) -> bool:
    res = execute_query("SELECT is_admin FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    return bool(res and res[0] == 1)

# ----------------------------------------------------------------------
# FSM STATES
# ----------------------------------------------------------------------
class AdminStates(StatesGroup):
    waiting_for_photo = State()
    waiting_for_rarity = State()
    waiting_for_description = State()
    waiting_for_admin_text = State()
    waiting_for_broadcast_messages = State()
    waiting_for_admin_code = State()

class IsAdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return is_admin_user(message.from_user.id)

# Bot & Dispatcher setup
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

@dp.my_chat_member()
async def on_my_chat_member(update: types.ChatMemberUpdated):
    chat_id = update.chat.id
    chat_type = update.chat.type
    if update.new_chat_member.status in ["member", "administrator"]:
        execute_query("INSERT OR IGNORE INTO chats (chat_id, chat_type) VALUES (?, ?)", (chat_id, chat_type))
    elif update.new_chat_member.status in ["left", "kicked"]:
        execute_query("DELETE FROM chats WHERE chat_id = ?", (chat_id,))

@dp.message(F.text)
async def global_message_handler(message: Message, state: FSMContext):
    register_user_and_chat(
        user_id=message.from_user.id,
        username=message.from_user.username or "Unknown",
        chat_id=message.chat.id,
        chat_type=message.chat.type
    )
    text_lower = message.text.lower().strip()
    if text_lower == "ÑÐµÐ¼":
        await handle_foto_cmd(message)
    elif text_lower == "Ð¿ÑÐ¾ÑÐ¸Ð»Ñ":
        await handle_profile_cmd(message)
    elif text_lower == "ÐºÑÐ¾ Ð°Ð´Ð¼Ð¸Ð½":
        await handle_admin_info_cmd(message)
    else:
        await dp.propagate_event(update=message.by_custom_key, message=message, state=state)

@dp.message(Command("foto"))
async def handle_foto_cmd(message: Message):
    user_id = message.from_user.id
    register_user_and_chat(user_id, message.from_user.username or "Unknown", message.chat.id, message.chat.type)
    
    user_data = execute_query("SELECT last_foto_time FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    now = datetime.now()
    
    if user_data and user_data[0]:
        last_time = datetime.strptime(user_data[0], "%Y-%m-%d %H:%M:%S")
        time_diff = now - last_time
        cooldown_period = timedelta(hours=3)
        if time_diff < cooldown_period:
            remaining = cooldown_period - time_diff
            hours, remainder = divmod(remaining.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            await message.reply(
                f"â³ ÐÑ ÑÐ¶Ðµ Ð±ÑÐ°Ð»Ð¸ ÑÐ¾ÑÐ¾! ÐÐ¾Ð¿ÑÐ¾Ð±ÑÐ¹ÑÐµ ÑÐ½Ð¾Ð²Ð° ÑÐµÑÐµÐ·:\n*{hours:02d}:{minutes:02d}:{seconds:02d}*.",
                parse_mode="Markdown"
            )
            return

    rarities = list(RARITIES_WEIGHTS.keys())
    weights = list(RARITIES_WEIGHTS.values())
    chosen_rarity = random.choices(rarities, weights=weights, k=1)[0]
    
    photos = execute_query("SELECT id, file_id, description FROM photos WHERE rarity = ?", (chosen_rarity,), fetchall=True)
    if not photos:
        photos = execute_query("SELECT id, file_id, rarity, description FROM photos ORDER BY RANDOM() LIMIT 1", fetchall=True)
        if not photos:
            await message.reply("ð Ð Ð±Ð°Ð·Ðµ Ð¿Ð¾ÐºÐ° Ð½ÐµÑ ÑÐ¾ÑÐ¾. ÐÐ±ÑÐ°ÑÐ¸ÑÐµÑÑ Ðº Ð°Ð´Ð¼Ð¸Ð½Ð°Ð¼.")
            return
        photo_id, file_id, real_rarity, description = photos[0]
        chosen_rarity = real_rarity
    else:
        photo_id, file_id, description = random.choice(photos)
        
    execute_query("INSERT OR IGNORE INTO user_photos (user_id, photo_id) VALUES (?, ?)", (user_id, photo_id))
    execute_query("UPDATE users SET last_foto_time = ? WHERE user_id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), user_id))
    
    caption = f"ð *ÐÑ Ð¿Ð¾Ð»ÑÑÐ¸Ð»Ð¸ Ð½Ð¾Ð²Ð¾Ðµ ÑÐ¾ÑÐ¾!*\n\nâ­ *Ð ÐµÐ´ÐºÐ¾ÑÑÑ:* {chosen_rarity.capitalize()}\nð *ÐÐ¿Ð¸ÑÐ°Ð½Ð¸Ðµ:* {description}"
    await bot.send_photo(chat_id=message.chat.id, photo=file_id, caption=caption, parse_mode="Markdown")

@dp.message(Command("profil"))
async def handle_profile_cmd(message: Message):
    user_id = message.from_user.id
    register_user_and_chat(user_id, message.from_user.username or "Unknown", message.chat.id, message.chat.type)
    
    user_photos_count = execute_query("SELECT COUNT(DISTINCT photo_id) FROM user_photos WHERE user_id = ?", (user_id,), fetchone=True)[0]
    total_photos_count = execute_query("SELECT COUNT(*) FROM photos", fetchone=True)[0]
    
    avatar_url = DEFAULT_AVATAR
    try:
        user_profile_photos = await bot.get_user_profile_photos(user_id, limit=1)
        if user_profile_photos.total_count > 0:
            avatar_url = user_profile_photos.photos[0][-1].file_id
    except Exception as e:
        logging.warning(f"ÐÑÐ¸Ð±ÐºÐ° Ð°Ð²Ð°ÑÐ°ÑÐ°: {e}")

    caption = f"ð¤ *ÐÐ°Ñ ÐÑÐ¾ÑÐ¸Ð»Ñ:*\n\nð· *ÐÐ¼Ñ:* {message.from_user.full_name}\nð¦ *ÐÐ°ÑÐ° ÐºÐ¾Ð»Ð»ÐµÐºÑÐ¸Ñ:* {user_photos_count} Ð¸Ð· {total_photos_count} ÑÐ¾ÑÐ¾.\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ð¼ Ð¡Ð¼Ð¾ÑÑÐµÑÑ Ð²ÑÐµ ÑÐ¾ÑÐ¾", callback_data="view_collection:0")]])
    await message.reply_photo(photo=avatar_url, caption=caption, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("view_collection:"))
async def view_collection_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    index = int(callback.data.split(":")[1])
    
    user_photos = execute_query(
        "SELECT p.id, p.file_id, p.rarity, p.description FROM user_photos up JOIN photos p ON up.photo_id = p.id WHERE up.user_id = ?", 
        (user_id,), fetchall=True
    )
    if not user_photos:
        await callback.answer("ð¢ Ð£ Ð²Ð°Ñ Ð¿Ð¾ÐºÐ° Ð½ÐµÑ Ð½Ð¸ Ð¾Ð´Ð½Ð¾Ð³Ð¾ ÑÐ¾ÑÐ¾ Ð² ÐºÐ¾Ð»Ð»ÐµÐºÑÐ¸Ð¸!", show_alert=True)
        return
        
    sorted_photos = sorted(user_photos, key=lambda x: RARITY_ORDER.get(x[2].lower(), 99))
    total_photos = len(sorted_photos)
    index = max(0, min(index, total_photos - 1))
    
    photo_id, file_id, rarity, description = sorted_photos[index]
    caption = f"ð¼ *ÐÐ°ÑÐ° ÐÐ¾Ð»Ð»ÐµÐºÑÐ¸Ñ* ({index + 1} Ð¸Ð· {total_photos})\n\nâ­ *Ð ÐµÐ´ÐºÐ¾ÑÑÑ:* {rarity.capitalize()}\nð *ÐÐ¿Ð¸ÑÐ°Ð½Ð¸Ðµ:* {description}"
    
    buttons = []
    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton(text="â¬ï¸ ÐÑÐµÐ´.", callback_data=f"view_collection:{index - 1}"))
    if index < total_photos - 1:
        nav_row.append(InlineKeyboardButton(text="Ð¡Ð»ÐµÐ´. â¡ï¸", callback_data=f"view_collection:{index + 1}"))
    if nav_row:
        buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(text="ð¤ ÐÐµÑÐ½ÑÑÑÑÑ Ð² Ð¿ÑÐ¾ÑÐ¸Ð»Ñ", callback_data="back_to_profile")])
    
    await callback.message.edit_media(media=InputMediaPhoto(media=file_id, caption=caption, parse_mode="Markdown"), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data == "back_to_profile")
async def back_to_profile_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    user_photos_count = execute_query("SELECT COUNT(DISTINCT photo_id) FROM user_photos WHERE user_id = ?", (user_id,), fetchone=True)[0]
    total_photos_count = execute_query("SELECT COUNT(*) FROM photos", fetchone=True)[0]
    
    avatar_url = DEFAULT_AVATAR
    try:
        user_profile_photos = await bot.get_user_profile_photos(user_id, limit=1)
        if user_profile_photos.total_count > 0:
            avatar_url = user_profile_photos.photos[0][-1].file_id
    except Exception:
        pass

    caption = f"ð¤ *ÐÐ°Ñ ÐÑÐ¾ÑÐ¸Ð»Ñ:*\n\nð· *ÐÐ¼Ñ:* {callback.from_user.full_name}\nð¦ *ÐÐ°ÑÐ° ÐºÐ¾Ð»Ð»ÐµÐºÑÐ¸Ñ:* {user_photos_count} Ð¸Ð· {total_photos_count} ÑÐ¾ÑÐ¾.\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ð¼ Ð¡Ð¼Ð¾ÑÑÐµÑÑ Ð²ÑÐµ ÑÐ¾ÑÐ¾", callback_data="view_collection:0")]])
    await callback.message.edit_media(media=InputMediaPhoto(media=avatar_url, caption=caption, parse_mode="Markdown"), reply_markup=keyboard)

@dp.message(Command("admin"))
async def handle_admin_info_cmd(message: Message):
    register_user_and_chat(message.from_user.id, message.from_user.username or "Unknown", message.chat.id, message.chat.type)
    admin_info = execute_query("SELECT value FROM settings WHERE key = 'admin_info'", fetchone=True)
    await message.reply(admin_info[0] if admin_info else "ÐÐ´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¸Ñ Ð½Ðµ Ð½Ð°ÑÑÑÐ¾ÐµÐ½Ð°.")

# ADMIN COMMANDS
@dp.message(Command("set_admin_text"), IsAdminFilter())
async def set_admin_text_start(message: Message, state: FSMContext):
    await message.reply("ÐÑÐ¿ÑÐ°Ð²ÑÑÐµ Ð½Ð¾Ð²ÑÐ¹ ÑÐµÐºÑÑ Ð´Ð»Ñ ÐºÐ¾Ð¼Ð°Ð½Ð´Ñ /admin:")
    await state.set_state(AdminStates.waiting_for_admin_text)

@dp.message(AdminStates.waiting_for_admin_text, IsAdminFilter())
async def set_admin_text_save(message: Message, state: FSMContext):
    execute_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_info', ?)", (message.text,))
    await message.reply("â Ð¡Ð¾Ð¾Ð±ÑÐµÐ½Ð¸Ðµ Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¾!")
    await state.clear()

@dp.message(Command("plus_foto"), IsAdminFilter())
async def plus_foto_start(message: Message, state: FSMContext):
    await message.reply("ð¸ ÐÑÐ¿ÑÐ°Ð²ÑÑÐµ ÑÐ¾ÑÐ¾Ð³ÑÐ°ÑÐ¸Ñ:")
    await state.set_state(AdminStates.waiting_for_photo)

@dp.message(AdminStates.waiting_for_photo, IsAdminFilter(), F.photo)
async def plus_foto_photo(message: Message, state: FSMContext):
    await state.update_data(file_id=message.photo[-1].file_id)
    buttons = [[InlineKeyboardButton(text=r.capitalize(), callback_data=f"set_rarity:{r}")] for r in RARITIES_WEIGHTS.keys()]
    await message.reply("â¨ ÐÑÐ±ÐµÑÐ¸ÑÐµ ÑÐµÐ´ÐºÐ¾ÑÑÑ:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(AdminStates.waiting_for_rarity)

@dp.callback_query(AdminStates.waiting_for_rarity, F.data.startswith("set_rarity:"), IsAdminFilter())
async def plus_foto_rarity(callback: CallbackQuery, state: FSMContext):
    rarity = callback.data.split(":")[1]
    await state.update_data(rarity=rarity)
    await callback.message.edit_text(f"Ð ÐµÐ´ÐºÐ¾ÑÑÑ: *{rarity.capitalize()}*. ÐÑÐ¿ÑÐ°Ð²ÑÑÐµ Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸Ðµ Ðº ÑÐ¾ÑÐ¾:", parse_mode="Markdown")
    await state.set_state(AdminStates.waiting_for_description)

@dp.message(AdminStates.waiting_for_description, IsAdminFilter(), F.text)
async def plus_foto_desc(message: Message, state: FSMContext):
    data = await state.get_data()
    execute_query("INSERT INTO photos (file_id, rarity, description) VALUES (?, ?, ?)", (data.get("file_id"), data.get("rarity"), message.text))
    await message.reply("ð Ð¤Ð¾ÑÐ¾Ð³ÑÐ°ÑÐ¸Ñ ÑÑÐ¿ÐµÑÐ½Ð¾ Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð° Ð² Ð±Ð°Ð·Ñ!")
    await state.clear()

@dp.message(Command("add"), IsAdminFilter())
async def add_broadcast_start(message: Message, state: FSMContext):
    await message.reply("ð¢ *Ð¡Ð¾Ð·Ð´Ð°Ð½Ð¸Ðµ ÑÐ°ÑÑÑÐ»ÐºÐ¸.*\nÐÑÐ¿ÑÐ°Ð²Ð»ÑÐ¹ÑÐµ ÑÐ¾Ð¾Ð±ÑÐµÐ½Ð¸Ñ. ÐÐ»Ñ Ð·Ð°Ð²ÐµÑÑÐµÐ½Ð¸Ñ Ð¾ÑÐ¿ÑÐ°Ð²ÑÑÐµ ÐºÐ¾Ð¼Ð°Ð½Ð´Ñ `/save`.", parse_mode="Markdown")
    await state.update_data(broadcast_messages=[])
    await state.set_state(AdminStates.waiting_for_broadcast_messages)

@dp.message(AdminStates.waiting_for_broadcast_messages, IsAdminFilter())
async def add_broadcast_collect(message: Message, state: FSMContext):
    if message.text and message.text.strip().lower() == "/save":
        await run_broadcast(message, state)
        return
    data = await state.get_data()
    messages_list = data.get("broadcast_messages", [])
    messages_list.append({"chat_id": message.chat.id, "message_id": message.message_id})
    await state.update_data(broadcast_messages=messages_list)
    await message.reply(f"ð¥ Ð¡Ð¾Ð¾Ð±ÑÐµÐ½Ð¸Ðµ #{len(messages_list)} Ð·Ð°Ð¿Ð¸ÑÐ°Ð½Ð¾.")

async def run_broadcast(message: Message, state: FSMContext):
    data = await state.get_data()
    messages_list = data.get("broadcast_messages", [])
    if not messages_list:
        await message.reply("â ï¸ Ð¡Ð¿Ð¸ÑÐ¾Ðº Ð¿ÑÑÑ.")
        await state.clear()
        return
        
    targets = {row[0] for row in execute_query("SELECT chat_id FROM chats", fetchall=True)} | {row[0] for row in execute_query("SELECT user_id FROM users", fetchall=True)}
    await message.reply(f"ð Ð Ð°ÑÑÑÐ»Ð°Ñ Ð¿Ð¾ {len(targets)} ÑÐ°ÑÐ°Ð¼...")
    
    success, fail = 0, 0
    for target_chat_id in targets:
        try:
            for msg in messages_list:
                await bot.copy_message(chat_id=target_chat_id, from_chat_id=msg["chat_id"], message_id=msg["message_id"])
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail += 1
            
    await message.reply(f"ð *Ð Ð°ÑÑÑÐ»ÐºÐ° Ð·Ð°Ð²ÐµÑÑÐµÐ½Ð°!*\n\nâ Ð£ÑÐ¿ÐµÑÐ½Ð¾: *{success}*\nâ ÐÑÐ¸Ð±ÐºÐ¸: *{fail}*", parse_mode="Markdown")
    await state.clear()

@dp.message(Command("now_admin_cod"), IsAdminFilter())
async def generate_admin_code_cmd(message: Message):
    code = "".join(random.sample(string.digits, 7) + random.sample(string.ascii_letters, 3))
    execute_query("INSERT INTO admin_codes (code, created_at, used) VALUES (?, ?, 0)", (code, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    await message.reply(f"ð ÐÐ¾Ð´ Ð°Ð´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¾ÑÐ°:\n`{code}`\n\nÐÐµÐ¹ÑÑÐ²ÑÐµÑ 3 ÑÐ°ÑÐ°. ÐÐ²Ð¾Ð´Ð¸ÑÑ ÑÐµÑÐµÐ· `/I_am_admin`.", parse_mode="Markdown")

@dp.message(Command("I_am_admin"))
async def I_am_admin_start(message: Message, state: FSMContext):
    if is_admin_user(message.from_user.id):
        await message.reply("ÐÑ ÑÐ¶Ðµ Ð°Ð´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¾Ñ!")
        return
    await message.reply("ð ÐÐ²ÐµÐ´Ð¸ÑÐµ 10-Ð·Ð½Ð°ÑÐ½ÑÐ¹ ÐºÐ¾Ð´:")
    await state.set_state(AdminStates.waiting_for_admin_code)

@dp.message(AdminStates.waiting_for_admin_code)
async def I_am_admin_verify(message: Message, state: FSMContext):
    input_code = message.text.strip()
    code_data = execute_query("SELECT created_at, used FROM admin_codes WHERE code = ?", (input_code,), fetchone=True)
    
    if not code_data or code_data[1] == 1 or datetime.now() - datetime.strptime(code_data[0], "%Y-%m-%d %H:%M:%S") > timedelta(hours=3):
        await message.reply("â ÐÐµÐ²ÐµÑÐ½ÑÐ¹, Ð¸ÑÐ¿Ð¾Ð»ÑÐ·Ð¾Ð²Ð°Ð½Ð½ÑÐ¹ Ð¸Ð»Ð¸ Ð¸ÑÑÐµÐºÑÐ¸Ð¹ ÐºÐ¾Ð´.")
        await state.clear()
        return
        
    execute_query("UPDATE admin_codes SET used = 1 WHERE code = ?", (input_code,))
    execute_query("UPDATE users SET is_admin = 1 WHERE user_id = ?", (message.from_user.id,))
    await message.reply("ð ÐÑ ÑÑÐ¿ÐµÑÐ½Ð¾ ÑÑÐ°Ð»Ð¸ Ð°Ð´Ð¼Ð¸Ð½Ð¸ÑÑÑÐ°ÑÐ¾ÑÐ¾Ð¼ Ð±Ð¾ÑÐ°!", parse_mode="Markdown")
    await state.clear()

async def main():
    if not API_TOKEN:
        raise ValueError("BOT_TOKEN Ð½Ðµ ÑÑÑÐ°Ð½Ð¾Ð²Ð»ÐµÐ½ Ð² Ð¿ÐµÑÐµÐ¼ÐµÐ½Ð½ÑÑ Ð¾ÐºÑÑÐ¶ÐµÐ½Ð¸Ñ!")
    init_db()
    await bot.set_my_commands([
        BotCommand(command="foto", description="ÐÐ¾Ð»ÑÑÐ¸ÑÑ ÑÐµÐ¼-ÑÐ¾ÑÐ¾ (ÑÐ°Ð· Ð² 3 ÑÐ°ÑÐ°)"),
        BotCommand(command="profil", description="ÐÑÐ¾ÑÐ¸Ð»Ñ Ð¸ ÐºÐ¾Ð»Ð»ÐµÐºÑÐ¸Ñ"),
        BotCommand(command="admin", description="ÐÐ½ÑÐ¾ÑÐ¼Ð°ÑÐ¸Ñ Ð¾Ð± Ð°Ð´Ð¼Ð¸Ð½Ð°Ñ"),
        BotCommand(command="I_am_admin", description="Ð¡ÑÐ°ÑÑ Ð°Ð´Ð¼Ð¸Ð½Ð¾Ð¼")
    ])
    logging.info("ÐÐ¾Ñ Ð·Ð°Ð¿ÑÑÐµÐ½!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
