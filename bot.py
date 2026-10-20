import asyncio
import logging
import os
import random
import socket
import string
import time
from datetime import datetime
from typing import Optional, List, Dict

import aiohttp
import aiosqlite
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    CallbackQuery,
    Message,
    LabeledPrice,
    PreCheckoutQuery,
)

# Загружаем настройки из .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0").strip() or "0")
DB_NAME = "fem_bot.db"

CHANNEL_USERNAME = "@FM_femboy"
CHANNEL_URL = "https://t.me/FM_femboy"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Редкости и веса
RARITIES = ["Редкое", "Ультра редкое", "Мифическое", "Легендарное", "Фембойский артефакт", "Редчайший фем"]
BASE_WEIGHTS = {"Редкое": 58.0, "Ультра редкое": 28.0, "Мифическое": 10.0, "Легендарное": 0.9, "Фембойский артефакт": 0.3, "Редчайший фем": 0.1}
LUCK_WEIGHTS = {"Редкое": 20.0, "Ультра редкое": 35.0, "Мифическое": 30.0, "Легендарное": 10.0, "Фембойский артефакт": 3.5, "Редчайший фем": 1.5}
RARITY_ORDER = {r: i for i, r in enumerate(RARITIES)}

active_chests: Dict[str, dict] = {}
user_casino_bets: Dict[int, int] = {}
chat_last_activity: Dict[int, float] = {}

class AdminStates(StatesGroup):
    waiting_for_photo = State()
    waiting_for_custom_desc = State()
    broadcast_collecting = State()
    waiting_admin_code = State()
    waiting_custom_promo = State()
    waiting_set_adm_text = State()

class UserStates(StatesGroup):
    waiting_for_promo_code = State()

# ----------------- БАЗА ДАННЫХ -----------------
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                femcoins INTEGER DEFAULT 100,
                last_fem_time REAL DEFAULT 0,
                custom_cd_hours REAL DEFAULT 3.0,
                custom_cd_expires REAL DEFAULT 0,
                has_cd_reset INTEGER DEFAULT 0,
                cd_reset_expires REAL DEFAULT 0,
                luck_expires REAL DEFAULT 0,
                daily_streak INTEGER DEFAULT 0,
                last_daily_time REAL DEFAULT 0,
                channel_bonus_claimed REAL DEFAULT 0,
                last_weekly_bonus REAL DEFAULT 0,
                referred_by INTEGER DEFAULT 0,
                referral_reward_given INTEGER DEFAULT 0,
                first_seen REAL DEFAULT 0
            )
        """)
        # referral_reward_given: 0 - чистый, 1 - бонус выдан, 2 - дисквалифицирован (не чист)

        await db.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, added_by INTEGER, added_at REAL)")
        await db.execute("CREATE TABLE IF NOT EXISTS cards (id INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT NOT NULL, rarity TEXT NOT NULL, description TEXT DEFAULT '')")
        await db.execute("CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, card_id INTEGER, count INTEGER DEFAULT 1, FOREIGN KEY (card_id) REFERENCES cards(id))")
        await db.execute("CREATE TABLE IF NOT EXISTS chats (chat_id INTEGER PRIMARY KEY, chat_type TEXT, inactivity_interval REAL DEFAULT 3600)")
        await db.execute("CREATE TABLE IF NOT EXISTS group_rewards (chat_id INTEGER PRIMARY KEY, owner_id INTEGER, reward_claimed INTEGER DEFAULT 1)")
        await db.execute("CREATE TABLE IF NOT EXISTS owner_group_counts (owner_id INTEGER PRIMARY KEY, count INTEGER DEFAULT 0)")
        await db.execute("CREATE TABLE IF NOT EXISTS promo_codes (code TEXT PRIMARY KEY, p_type TEXT, reward_type TEXT, reward_val INTEGER, expires_at REAL, is_used INTEGER DEFAULT 0)")
        await db.execute("CREATE TABLE IF NOT EXISTS promo_claims (user_id INTEGER, code TEXT, claimed_at REAL, PRIMARY KEY(user_id, code))")
        await db.execute("CREATE TABLE IF NOT EXISTS admin_codes (code TEXT PRIMARY KEY, created_by INTEGER, expires_at REAL, used INTEGER DEFAULT 0)")
        await db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, action TEXT, timestamp REAL)")
        await db.commit()

async def get_or_create_user(user: types.User, ref_id: int = 0):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, referred_by FROM users WHERE user_id = ?", (user.id,)) as cur:
            row = await cur.fetchone()
            if not row:
                # Абсолютно новый юзер
                valid_ref = ref_id if (ref_id > 0 and ref_id != user.id) else 0
                await db.execute("INSERT INTO users (user_id, username, referred_by, first_seen) VALUES (?, ?, ?, ?)", (user.id, user.username or user.full_name, valid_ref, time.time()))
                await db.commit()
                return True # Новый
            else:
                # Если он уже был в базе, но перешел по рефке — он уже не "чистый" для бонуса
                return False # Старый

async def disqualify_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET referral_reward_given = 2 WHERE user_id = ? AND referral_reward_given = 0", (user_id,))
        await db.commit()

async def is_admin(user_id: int) -> bool:
    if user_id == SUPER_ADMIN_ID: return True
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cur:
            return bool(await cur.fetchone())

# ----------------- СЕТЬ И БОТ -----------------
class TermuxAiohttpSession(AiohttpSession):
    async def create_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(family=socket.AF_INET, ssl=False), timeout=aiohttp.ClientTimeout(total=90))
        return self._session

bot = Bot(token=BOT_TOKEN, session=TermuxAiohttpSession(api=TelegramAPIServer.from_base("https://api.telegram.org")), default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

def match_command(message: Message, aliases: List[str]) -> bool:
    if not message.text: return False
    text = message.text.split()[0].lower().replace("/", "").split("@")[0]
    return text in aliases

# ----------------- ГРУППОВЫЕ НАГРАДЫ -----------------
@dp.my_chat_member()
async def bot_added_handler(event: types.ChatMemberUpdated):
    if event.new_chat_member.status in ["member", "administrator"] and event.old_chat_member.status in ["left", "kicked"]:
        if event.chat.type not in ["group", "supergroup"]: return
        
        chat_id = event.chat.id
        owner_id = event.from_user.id
        
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT 1 FROM group_rewards WHERE chat_id = ?", (chat_id,)) as cur:
                if await cur.fetchone(): return # Чат уже использован
            
            async with db.execute("SELECT count FROM owner_group_counts WHERE owner_id = ?", (owner_id,)) as cur:
                res = await cur.fetchone()
                count = res[0] if res else 0
            
            if count >= 3: return # Лимит 3 чата на владельца
            
            m_count = await bot.get_chat_member_count(chat_id)
            if m_count < 15: return
            
            reward = 50
            if m_count >= 2000: reward = 500
            elif m_count >= 500: reward = 300
            
            await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (reward, owner_id))
            await db.execute("INSERT INTO group_rewards (chat_id, owner_id) VALUES (?, ?)", (chat_id, owner_id))
            await db.execute("INSERT OR REPLACE INTO owner_group_counts (owner_id, count) VALUES (?, ?)", (owner_id, count + 1))
            await db.commit()
            
            await bot.send_message(chat_id, f"🎉 <b>Спасибо за добавление!</b>\nВладелец чата получил бонус: <b>+{reward} 🪙</b>")

# ----------------- ОСНОВНЫЕ КОМАНДЫ -----------------
@dp.message(CommandStart())
async def start_cmd(message: Message):
    args = message.text.split()
    is_new = await get_or_create_user(message.from_user)
    
    if len(args) > 1 and args[1].startswith("ref"):
        ref_id = int(args[1].replace("ref", ""))
        if is_new and ref_id != message.from_user.id:
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("UPDATE users SET referred_by = ? WHERE user_id = ?", (ref_id, message.from_user.id))
                await db.commit()
        elif not is_new:
            await message.reply("❌ Вы уже запускали этого бота ранее, реферальный бонус не будет начислен.")

    bot_info = await bot.get_me()
    await message.reply(f"👋 Привет! Я игровой бот.\nПиши <b>фем</b> чтобы получить карту, или <b>реф</b> чтобы позвать друзей!")

@dp.message(lambda m: match_command(m, ["ref", "реф", "рефералы"]))
async def ref_cmd(message: Message):
    uid = message.from_user.id
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref{uid}"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить в свою группу", url=f"https://t.me/{bot_info.username}?startgroup=true")]
    ])
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users WHERE referred_by = ? AND referral_reward_given = 1", (uid,)) as cur:
            count = (await cur.fetchone())[0]
            
    await message.reply(f"👥 <b>Реферальная система</b>\n\nТвоя ссылка:\n<code>{link}</code>\n\nПриглашай друзей и получай <b>+25 🪙</b>, когда они заберут свой первый дейлик (если они новички).\n\nНачислено бонусов: {count}", reply_markup=kb)

@dp.message(lambda m: match_command(m, ["daily", "дейлик"]))
async def daily_cmd(message: Message):
    uid = message.from_user.id
    now = time.time()
    await get_or_create_user(message.from_user)
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT last_daily_time, daily_streak, referred_by, referral_reward_given FROM users WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
            last_time, streak, ref_id, ref_status = row
        
        if now - last_time < 86400:
            return await message.reply("⏳ Заходи завтра!")
            
        new_streak = streak + 1 if now - last_time < 172800 else 1
        reward = 20 + (min(new_streak, 30) * 10)
        
        await db.execute("UPDATE users SET femcoins = femcoins + ?, daily_streak = ?, last_daily_time = ? WHERE user_id = ?", (reward, new_streak, now, uid))
        
        # Начисление рефереру
        if ref_id > 0 and ref_status == 0:
            await db.execute("UPDATE users SET femcoins = femcoins + 25 WHERE user_id = ?", (ref_id,))
            await db.execute("UPDATE users SET referral_reward_given = 1 WHERE user_id = ?", (uid,))
            try: await bot.send_message(ref_id, f"👥 Твой реферал активен! +25 🪙")
            except: pass
            
        await db.commit()
    await message.reply(f"📅 Награда получена: <b>+{reward} 🪙</b>")

@dp.message(lambda m: match_command(m, ["fem", "фем", "foto"]))
async def fem_cmd(message: Message):
    uid = message.from_user.id
    await disqualify_user(uid) # Любое действие кроме дейлика делает юзера "не чистым"
    now = time.time()
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT last_fem_time, custom_cd_hours FROM users WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
            last_time, cd = row
        
        if now - last_time < cd * 3600:
            rem = int(cd * 3600 - (now - last_time))
            return await message.reply(f"⏳ Жди {rem // 60} мин.")

        # Выпадение карты
        rarity = random.choices(list(BASE_WEIGHTS.keys()), weights=list(BASE_WEIGHTS.values()))[0]
        async with db.execute("SELECT id, file_id, description FROM cards WHERE rarity = ? ORDER BY RANDOM() LIMIT 1", (rarity,)) as cur:
            card = await cur.fetchone()
            if not card: return await message.reply("Пусто.")
        
        await db.execute("INSERT INTO inventory (user_id, card_id) VALUES (?, ?)", (uid, card[0]))
        await db.execute("UPDATE users SET last_fem_time = ? WHERE user_id = ?", (now, uid))
        await db.commit()
        
    await message.answer_photo(card[1], caption=f"✨ Выпала карта #{card[0]} ({rarity})\n{card[2]}")

# ----------------- ЗОЛОТОЙ ДОЖДЬ -----------------
@dp.message(lambda m: match_command(m, ["золотой дождь", "golden_rain"]))
async def golden_rain_cmd(message: Message):
    if message.from_user.id != SUPER_ADMIN_ID: return
    if message.chat.type == "private": return await message.reply("Только в группах!")
    
    await message.answer("🌧 <b>Начинается Золотой Дождь!</b> Ловите сундуки!")
    asyncio.create_task(spawn_golden_chests(message.chat.id))

async def spawn_golden_chests(chat_id):
    total = random.randint(10, 20)
    leg_idx = random.randint(1, total)
    for i in range(1, total + 1):
        await asyncio.sleep(random.randint(2, 5))
        cid = f"gr_{int(time.time()*1000)}"
        if i == leg_idx:
            active_chests[cid] = {"type": "legendary", "claimed": False}
            await bot.send_message(chat_id, "🌟 <b>ЛЕГЕНДАРНЫЙ СУНДУК!</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ЗАБРАТЬ!", callback_data=f"claim_{cid}")]]))
        else:
            coins = random.randint(3, 15)
            if random.random() < 0.2: 
                coins = random.randint(50, 100)
                active_chests[cid] = {"type": "rare", "coins": coins, "claimed": False}
                await bot.send_message(chat_id, "💎 <b>РЕДКИЙ СУНДУК!</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ЗАБРАТЬ!", callback_data=f"claim_{cid}")]]))
            else:
                active_chests[cid] = {"type": "normal", "coins": coins, "claimed": False}
                await bot.send_message(chat_id, "📦 Обычный сундук", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Забрать", callback_data=f"claim_{cid}")]]))

@dp.callback_query(F.data.startswith("claim_"))
async def claim_chest(cb: CallbackQuery):
    cid = cb.data.replace("claim_", "")
    chest = active_chests.get(cid)
    if not chest or chest["claimed"]: return await cb.answer("Уже забрали!")
    
    chest["claimed"] = True
    uid = cb.from_user.id
    
    async with aiosqlite.connect(DB_NAME) as db:
        if chest["type"] == "legendary":
            async with db.execute("SELECT id FROM cards WHERE rarity = 'Легендарное' ORDER BY RANDOM() LIMIT 1") as cur:
                card = await cur.fetchone()
                if card: await db.execute("INSERT INTO inventory (user_id, card_id) VALUES (?, ?)", (uid, card[0]))
            await db.execute("UPDATE users SET femcoins = femcoins + 250 WHERE user_id = ?", (uid,))
            await cb.message.edit_text(f"👑 <b>{cb.from_user.full_name}</b> забрал Легендарный сундук (+250 🪙 и карта)!")
        else:
            await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (chest["coins"], uid))
            await cb.message.edit_text(f"✅ <b>{cb.from_user.full_name}</b> забрал сундук (+{chest['coins']} 🪙)!")
        await db.commit()

# ----------------- ОСТАЛЬНОЕ -----------------
@dp.message(lambda m: match_command(m, ["профиль", "profile"]))
async def profile_cmd(message: Message):
    await disqualify_user(message.from_user.id)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT femcoins FROM users WHERE user_id = ?", (message.from_user.id,)) as cur:
            coins = (await cur.fetchone())[0]
    await message.reply(f"👤 {message.from_user.full_name}\n💰 Баланс: {coins} 🪙")

@dp.message(lambda m: match_command(m, ["казино", "kaz"]))
async def kaz_cmd(message: Message):
    await disqualify_user(message.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎰 Крутить", callback_data="kaz_play")]])
    await message.reply("Добро пожаловать в казино!", reply_markup=kb)

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
