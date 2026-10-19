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
RARITIES = [
    "Редкое",
    "Ультра редкое",
    "Мифическое",
    "Легендарное",
    "Фембойский артефакт",
    "Редчайший фем",
]

BASE_WEIGHTS = {
    "Редкое": 58.0,
    "Ультра редкое": 28.0,
    "Мифическое": 10.0,
    "Легендарное": 0.9,
    "Фембойский артефакт": 0.3,
    "Редчайший фем": 0.1,
}

LUCK_WEIGHTS = {
    "Редкое": 20.0,
    "Ультра редкое": 35.0,
    "Мифическое": 30.0,
    "Легендарное": 10.0,
    "Фембойский артефакт": 3.5,
    "Редчайший фем": 1.5,
}

RARITY_ORDER = {r: i for i, r in enumerate(RARITIES)}

active_chests: Dict[str, dict] = {}
user_casino_bets: Dict[int, int] = {}
chat_last_activity: Dict[int, float] = {}

class AdminStates(StatesGroup):
    waiting_for_photo = State()
    waiting_for_custom_desc = State()
    waiting_for_new_desc = State()
    broadcast_collecting = State()
    waiting_admin_code = State()
    waiting_custom_promo = State()
    waiting_set_adm_text = State()

class UserStates(StatesGroup):
    waiting_for_promo_code = State()
    waiting_for_trade_offer = State()

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
                first_seen REAL DEFAULT 0
            )
        """)
        for col_def in [
            "channel_bonus_claimed REAL DEFAULT 0",
            "last_weekly_bonus REAL DEFAULT 0",
            "first_seen REAL DEFAULT 0"
        ]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col_def}")
                await db.commit()
            except Exception:
                pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER,
                added_at REAL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT NOT NULL,
                rarity TEXT NOT NULL,
                description TEXT DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                card_id INTEGER,
                count INTEGER DEFAULT 1,
                FOREIGN KEY (card_id) REFERENCES cards(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                chat_type TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                p_type TEXT,
                reward_type TEXT,
                reward_val INTEGER,
                expires_at REAL,
                is_used INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promo_claims (
                user_id INTEGER,
                code TEXT,
                claimed_at REAL,
                PRIMARY KEY(user_id, code)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admin_codes (
                code TEXT PRIMARY KEY,
                created_by INTEGER,
                expires_at REAL,
                used INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action TEXT,
                timestamp REAL
            )
        """)
        await db.commit()

async def register_chat(chat_id: int, chat_type: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO chats (chat_id, chat_type) VALUES (?, ?)",
            (chat_id, chat_type),
        )
        await db.commit()

async def get_or_create_user(user: types.User):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)) as cur:
            row = await cur.fetchone()
            curr_name = user.username or user.full_name
            if not row:
                now = time.time()
                await db.execute(
                    "INSERT INTO users (user_id, username, first_seen) VALUES (?, ?, ?)",
                    (user.id, curr_name, now),
                )
                await db.commit()
            else:
                if row[1] != curr_name:
                    await db.execute(
                        "UPDATE users SET username = ? WHERE user_id = ?",
                        (curr_name, user.id),
                    )
                    await db.commit()

async def get_user_display_name(user_id: int, clean: bool = False) -> str:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT username FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if row and row[0]:
                uname = row[0].lstrip("@")
                if clean:
                    return uname
                return f"@{uname}" if " " not in uname else uname
    return f"ID:{user_id}"

async def is_admin(user_id: int) -> bool:
    if user_id == SUPER_ADMIN_ID:
        return True
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cur:
            return bool(await cur.fetchone())

async def add_log(admin_id: int, action: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO logs (admin_id, action, timestamp) VALUES (?, ?, ?)",
            (admin_id, action, time.time()),
        )
        await db.commit()

# ----------------- СЕТЬ -----------------
class TermuxAiohttpSession(AiohttpSession):
    async def create_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(family=socket.AF_INET, ssl=False)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                json_serialize=self.json_dumps,
            )
        return self._session

session = TermuxAiohttpSession(
    api=TelegramAPIServer.from_base("https://api.telegram.org"),
    timeout=90.0
)

bot = Bot(
    token=BOT_TOKEN,
    session=session,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher(storage=MemoryStorage())

def get_normalized_command(text: Optional[str]) -> str:
    if not text:
        return ""
    token = text.strip().split()[0].lower()
    if "@" in token:
        token = token.split("@")[0]
    return token

def match_command(message: Message, aliases: List[str]) -> bool:
    if not message.text:
        return False
    norm = get_normalized_command(message.text)
    full_lower = message.text.strip().lower()
    for a in aliases:
        a_low = a.lower()
        if norm == a_low or full_lower == a_low:
            return True
        if norm == f"/{a_low}" or full_lower == f"/{a_low}":
            return True
    return False

# Проверка подписки с обработкой ошибок
async def check_channel_subscription(user_id: int) -> tuple[bool, str]:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        status = str(getattr(member, "status", "")).lower()
        if any(s in status for s in ["creator", "administrator", "member", "restricted"]):
            return True, "ok"
        return False, "not_member"
    except Exception as e:
        err_msg = str(e).lower()
        logger.error(f"Subscription check error: {e}")
        if "chat not found" in err_msg or "bot is not a member" in err_msg or "user not found" in err_msg:
            return False, "bot_not_admin"
        return False, "error"

@dp.message.outer_middleware()
async def register_middleware(handler, event: Message, data: dict):
    if event.from_user:
        await get_or_create_user(event.from_user)
    if event.chat:
        await register_chat(event.chat.id, event.chat.type)
        # Отслеживаем активность только в публичных/приватных группах и супергруппах (НЕ в ЛС)
        if event.chat.type in ["group", "supergroup"]:
            chat_last_activity[event.chat.id] = time.time()
    return await handler(event, data)

# ----------------- КОМАНДЫ ИГРОКОВ -----------------

# 1. /foto или фем
@dp.message(lambda msg: match_command(msg, ["foto", "fem", "фем"]))
async def fem_command(message: Message):
    uid = message.from_user.id
    now = time.time()

    async with aiosqlite.connect(DB_NAME) as db:
        try:
            async with db.execute("SELECT channel_bonus_claimed, last_weekly_bonus FROM users WHERE user_id = ?", (uid,)) as cur:
                b_row = await cur.fetchone()
                if b_row and (b_row[0] or 0) > 0:
                    last_weekly = b_row[1] or 0
                    if now - last_weekly >= 604800:
                        is_sub, _ = await check_channel_subscription(uid)
                        if is_sub:
                            await db.execute("UPDATE users SET femcoins = femcoins + 50, last_weekly_bonus = ? WHERE user_id = ?", (now, uid))
                            await db.commit()
                            try:
                                await bot.send_message(uid, "🎁 <b>Еженедельный бонус!</b>\nВам начислено <b>+50 🪙 фемкоинов</b> за подписку на ТГК!")
                            except Exception:
                                pass
                        else:
                            await db.execute("UPDATE users SET channel_bonus_claimed = 0 WHERE user_id = ?", (uid,))
                            await db.commit()
        except Exception as e:
            logger.warning(f"Weekly bonus check exception: {e}")

        async with db.execute(
            "SELECT last_fem_time, custom_cd_hours, custom_cd_expires, has_cd_reset, cd_reset_expires, luck_expires FROM users WHERE user_id = ?",
            (uid,)
        ) as cur:
            row = await cur.fetchone()

        if not row:
            last_fem, custom_cd, custom_exp, has_reset, reset_exp, luck_exp = 0, 3.0, 0, 0, 0, 0
        else:
            last_fem, custom_cd, custom_exp, has_reset, reset_exp, luck_exp = row

        effective_cd = 3.0
        if custom_exp == -1 or custom_exp > now:
            effective_cd = custom_cd

        cd_seconds = effective_cd * 3600
        time_passed = now - last_fem

        if time_passed < cd_seconds:
            if has_reset and (reset_exp > now):
                await db.execute("UPDATE users SET has_cd_reset = 0 WHERE user_id = ?", (uid,))
                await db.commit()
                await message.reply("🔄 Использован купленный сброс ожидания! Крутим фото...")
            else:
                rem = cd_seconds - time_passed
                h = int(rem // 3600)
                m = int((rem % 3600) // 60)
                s = int(rem % 60)
                return await message.reply(f"⏳ Рано! До следующей попытки осталось: {h}ч {m}м {s}с.")

        is_lucky = luck_exp > now
        weights = LUCK_WEIGHTS if is_lucky else BASE_WEIGHTS
        chosen_rarity = random.choices(list(weights.keys()), weights=list(weights.values()))[0]

        async with db.execute("SELECT id, file_id, description FROM cards WHERE rarity = ? ORDER BY RANDOM() LIMIT 1", (chosen_rarity,)) as cur:
            card = await cur.fetchone()

        if not card:
            async with db.execute("SELECT id, file_id, description, rarity FROM cards ORDER BY RANDOM() LIMIT 1") as cur:
                card = await cur.fetchone()
                if not card:
                    return await message.reply("😔 Карты ещё не добавлены администраторами. Подождите немного!")
                chosen_rarity = card[3]

        c_id, f_id, desc = card[0], card[1], card[2]

        async with db.execute("SELECT count FROM inventory WHERE user_id = ? AND card_id = ?", (uid, c_id)) as cur:
            inv_row = await cur.fetchone()
            if inv_row:
                await db.execute("UPDATE inventory SET count = count + 1 WHERE user_id = ? AND card_id = ?", (uid, c_id))
                is_dup = True
            else:
                await db.execute("INSERT INTO inventory (user_id, card_id, count) VALUES (?, ?, 1)", (uid, c_id))
                is_dup = False

        await db.execute("UPDATE users SET last_fem_time = ? WHERE user_id = ?", (now, uid))
        await db.commit()

    caption = (
        f"✨ <b>Вам выпала карта #{c_id}!</b>\n\n"
        f"🏷 <b>Редкость:</b> {chosen_rarity}\n"
        f"📝 <b>Описание:</b> {desc or 'Без описания'}\n"
        f"{'<i>(Это дубликат! Пойдет на крафт или обмен)</i>' if is_dup else '🆕 <i>Новая карта в коллекции!</i>'}"
    )

    bot_me = await bot.get_me()
    kb_buttons = [
        [InlineKeyboardButton(text="🎁 Забрать бонус", url=f"https://t.me/{bot_me.username}?start=bonus")]
    ]
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM settings WHERE key = 'post_btn_url'") as cur:
            btn_row = await cur.fetchone()
            if btn_row and btn_row[0]:
                kb_buttons.append([InlineKeyboardButton(text="Присоединиться / Подробнее", url=btn_row[0])])

    await message.answer_photo(photo=f_id, caption=caption, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons))

    # Сундук спавнится с 10% шансом ТОЛЬКО в группах (не в ЛС)
    if message.chat.type in ["group", "supergroup"] and random.random() < 0.10:
        await asyncio.sleep(0.8)
        chest_id = f"ch_{int(time.time() * 1000)}_{random.randint(100, 999)}"
        reward_coins = random.choices([15, 25, 40, 60, 90], weights=[50, 25, 15, 7, 3])[0]
        active_chests[chest_id] = {"coins": reward_coins, "claimed": False, "winner_name": None}

        chest_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Забрать сундук!", callback_data=f"claim_chest_{chest_id}")]
        ])
        await message.answer(
            "📦 <b>Рядом упал тайный фем-сундук!</b>\n"
            "<i>Кто первым нажмет на кнопку ниже, заберет все фемкоины себе!</i>",
            reply_markup=chest_kb
        )

@dp.callback_query(F.data.startswith("claim_chest_"))
async def open_chest_handler(callback: CallbackQuery):
    chest_id = callback.data.replace("claim_chest_", "")
    chest = active_chests.get(chest_id)

    if not chest:
        return await callback.answer("Сундук уже исчез или был забран!", show_alert=True)

    if chest["claimed"]:
        return await callback.answer(f"Сундук уже забрал {chest['winner_name']}!", show_alert=True)

    chest["claimed"] = True
    uid = callback.from_user.id
    u_name = callback.from_user.username or callback.from_user.full_name
    chest["winner_name"] = u_name
    reward = chest["coins"]

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (reward, uid))
        await db.commit()

    await callback.message.edit_text(
        f"🎉 <b>Сундук открыт!</b>\n\n"
        f"⚡ Самым быстрым оказался: <b>{u_name}</b>\n"
        f"💰 Награда: <b>+{reward} 🪙 фемкоинов</b> зачислена на баланс!"
    )
    await callback.answer(f"Поздравляем! +{reward} фемкоинов ваши!")

@dp.message(CommandStart())
async def start_cmd(message: Message):
    args = message.text.split()
    if len(args) > 1 and args[1] == "bonus":
        if message.chat.type != "private":
            bot_me = await bot.get_me()
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎁 Получить бонус в ЛС", url=f"https://t.me/{bot_me.username}?start=bonus")]])
            return await message.reply("🎁 Бонус выдается в личных сообщениях с ботом!", reply_markup=kb)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🩷 ТГК Фембойчик", url=CHANNEL_URL)],
            [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_channel_sub")]
        ])
        return await message.answer(
            "🎁 <b>Еженедельный бонус и скип КД!</b>\n\n"
            "Подпишитесь на наш Telegram-канал, чтобы получить:\n"
            "• Мгновенный сброс кулдауна попыток!\n"
            "• <b>+50 🪙 фемкоинов</b> сразу и каждую неделю пока вы подписаны!",
            reply_markup=kb
        )

    if len(args) > 1 and args[1] == "shop":
        return await shop_command(message)

    await message.reply("👋 Привет! Я игровой бот. Напиши <b>фем</b> или /foto, чтобы начать.")

@dp.callback_query(F.data == "check_channel_sub")
async def check_sub_callback(callback: CallbackQuery):
    uid = callback.from_user.id
    is_sub, err = await check_channel_subscription(uid)

    if not is_sub:
        if err == "bot_not_admin":
            return await callback.answer(
                "⚠️ Ошибка проверки: добавьте бота администратором в канал @FM_femboy, чтобы он мог видеть подписчиков!",
                show_alert=True
            )
        return await callback.answer("❌ Вы ещё не подписались на канал! Подпишитесь и нажмите снова.", show_alert=True)

    now = time.time()
    async with aiosqlite.connect(DB_NAME) as db:
        # Проверяем, забирал ли пользователь приветственный бонус ранее
        async with db.execute("SELECT channel_bonus_claimed, last_weekly_bonus FROM users WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
            claimed = row[0] if row else 0
            last_weekly = row[1] if row else 0

        # Если бонус уже активирован:
        if claimed > 0:
            time_until_weekly = 604800 - (now - last_weekly)
            if time_until_weekly > 0:
                days = int(time_until_weekly // 86400)
                hours = int((time_until_weekly % 86400) // 3600)
                return await callback.answer(
                    f"ℹ️ Вы уже активировали бонус за подписку!\nСледующие +50 🪙 будут начислены через {days}д {hours}ч.",
                    show_alert=True
                )
            else:
                # Если прошло 7 дней, выдаем очередной еженедельный бонус
                await db.execute("UPDATE users SET femcoins = femcoins + 50, last_weekly_bonus = ? WHERE user_id = ?", (now, uid))
                await db.commit()
                await callback.message.edit_text(
                    "🎁 <b>Еженедельный бонус начислен!</b>\n\n"
                    "Вам зачислено <b>+50 🪙 фемкоинов</b> за продолжение подписки на наш канал.\n"
                    "Следующая награда через 7 дней!"
                )
                return await callback.answer("Еженедельные +50 фемкоинов зачислены!")

        # ПЕРВОЕ ПОЛУЧЕНИЕ БОНУСА: сброс КД + 50 монет
        await db.execute("""
            UPDATE users 
            SET femcoins = femcoins + 50, last_fem_time = 0, channel_bonus_claimed = 1, last_weekly_bonus = ? 
            WHERE user_id = ?
        """, (now, uid))
        await db.commit()

    await callback.message.edit_text(
        "✅ <b>Подписка успешно подтверждена!</b>\n\n"
        "🔄 Кулдаун команды <code>фем</code> полностью сброшен (можно крутить прямо сейчас)!\n"
        "💰 Начислено: <b>+50 🪙 фемкоинов</b>.\n\n"
        "⭐ <i>50 фемкоинов будут автоматически начисляться вам каждую неделю, пока вы подписаны на канал!</i>"
    )
    await callback.answer("Бонус успешно получен!")

# 2. Передача фемкоинов
@dp.message(lambda msg: msg.text and any(get_normalized_command(msg.text) == p or msg.text.lower().strip().startswith(p) for p in ["передать", "перевод", "/pay", "/transfer", "дать"]))
async def transfer_coins_cmd(message: Message):
    sender_id = message.from_user.id
    parts = message.text.strip().split()

    amount: Optional[int] = None
    target_id: Optional[int] = None

    for p in parts[1:]:
        if p.isdigit():
            amount = int(p)
            break

    if not amount or amount <= 0:
        return await message.reply("❗ Укажите сумму для передачи!\nПример: <code>передать 100</code> (ответом на сообщение) или <code>передать @username 100</code>")

    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    else:
        for p in parts[1:]:
            if p.startswith("@"):
                uname = p.lstrip("@")
                async with aiosqlite.connect(DB_NAME) as db:
                    async with db.execute("SELECT user_id FROM users WHERE username = ? OR username = ?", (uname, f"@{uname}")) as cur:
                        row = await cur.fetchone()
                        if row:
                            target_id = row[0]
                break

    if not target_id:
        return await message.reply("❗ Укажите получателя ответом на его сообщение или тегом @username!")

    if target_id == sender_id:
        return await message.reply("❌ Нельзя переводить фемкоины самому себе!")

    COMMISSION_PERCENT = 10
    burned = max(1, int(amount * COMMISSION_PERCENT / 100))
    received = amount - burned

    if received <= 0:
        return await message.reply("❌ Сумма слишком мала для передачи с учетом комиссии!")

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT femcoins FROM users WHERE user_id = ?", (sender_id,)) as cur:
            s_row = await cur.fetchone()
            sender_coins = s_row[0] if s_row else 0

        if sender_coins < amount:
            return await message.reply(f"❌ Недостаточно фемкоинов! Ваш баланс: {sender_coins} 🪙, а нужно: {amount} 🪙.")

        await db.execute("UPDATE users SET femcoins = femcoins - ? WHERE user_id = ?", (amount, sender_id))
        await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (received, target_id))
        await db.commit()

    sender_name = await get_user_display_name(sender_id, clean=True)
    target_name = await get_user_display_name(target_id, clean=True)

    await message.reply(
        f"💸 <b>Успешный перевод фемкоинов!</b>\n\n"
        f"👤 Отправитель: <b>{sender_name}</b>\n"
        f"🎯 Получатель: <b>{target_name}</b>\n"
        f"💰 Отправлено: <b>{amount} 🪙</b>\n"
        f"📥 Получено: <b>+{received} 🪙</b>\n"
        f"🔥 Комиссия (сгорело): <b>{burned} 🪙</b> ({COMMISSION_PERCENT}%)"
    )

# 3. Профиль
@dp.message(lambda msg: match_command(msg, ["profil", "profile", "профиль"]))
async def profile_command(message: Message):
    await show_user_profile(message.chat.id, message.from_user, reply_to=message.message_id)

async def show_user_profile(chat_id: int, user: types.User, reply_to: int = None):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(DISTINCT card_id) FROM inventory WHERE user_id = ?", (user.id,)) as cur:
            user_cards_count = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM cards") as cur:
            total_cards = (await cur.fetchone())[0]
        async with db.execute("SELECT femcoins FROM users WHERE user_id = ?", (user.id,)) as cur:
            coins = (await cur.fetchone())[0]

    photos = await bot.get_user_profile_photos(user.id, limit=1)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Смотреть все фото", callback_data=f"viewcards_{user.id}_0")]
    ])
    u_display = user.username or user.full_name
    caption = (
        f"👤 <b>Профиль игрока:</b> {u_display}\n"
        f"💰 <b>Фемкоины:</b> {coins} 🪙\n"
        f"🎴 <b>Собрано карт:</b> {user_cards_count} из {total_cards}"
    )

    if photos.total_count > 0:
        await bot.send_photo(chat_id, photos.photos[0][-1].file_id, caption=caption, reply_markup=kb, reply_to_message_id=reply_to)
    else:
        await bot.send_message(chat_id, caption, reply_markup=kb, reply_to_message_id=reply_to)

@dp.callback_query(F.data.startswith("viewcards_"))
async def browse_cards(callback: CallbackQuery):
    _, uid_str, idx_str = callback.data.split("_")
    uid = int(uid_str)
    idx = int(idx_str)

    if callback.from_user.id != uid:
        return await callback.answer("Это не ваш профиль!", show_alert=True)

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT c.id, c.file_id, c.rarity, c.description, inv.count
            FROM inventory inv
            JOIN cards c ON inv.card_id = c.id
            WHERE inv.user_id = ?
        """, (uid,)) as cur:
            cards = await cur.fetchall()

    if not cards:
        return await callback.answer("У вас ещё нет карт!", show_alert=True)

    cards.sort(key=lambda x: RARITY_ORDER.get(x[2], 0), reverse=True)
    idx = max(0, min(idx, len(cards) - 1))
    c_id, f_id, rarity, desc, cnt = cards[idx]

    nav_btns = []
    if idx > 0:
        nav_btns.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"viewcards_{uid}_{idx-1}"))
    if idx < len(cards) - 1:
        nav_btns.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"viewcards_{uid}_{idx+1}"))

    kb = InlineKeyboardMarkup(inline_keyboard=[nav_btns] if nav_btns else [])

    text = (
        f"🎴 <b>Карта #{c_id}</b> ({idx+1}/{len(cards)})\n"
        f"🌟 <b>Редкость:</b> {rarity}\n"
        f"🔢 <b>Количество дубликатов у вас:</b> {cnt}\n"
        f"📝 <b>Описание:</b> {desc or 'Без описания'}"
    )

    try:
        await callback.message.edit_media(
            media=InputMediaPhoto(media=f_id, caption=text),
            reply_markup=kb,
        )
    except Exception:
        await callback.message.delete()
        await callback.message.answer_photo(photo=f_id, caption=text, reply_markup=kb)
    await callback.answer()

# 4. Кто админ
@dp.message(lambda msg: match_command(msg, ["admin", "кто админ"]))
async def who_admin(message: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM settings WHERE key = 'admin_info'") as cur:
            row = await cur.fetchone()
    text = row[0] if row else "Администратор ещё не настроил информацию о себе."
    await message.reply(text)

# 5. Крафт
@dp.message(lambda msg: match_command(msg, ["craft", "крафт"]))
async def craft_command(message: Message):
    uid = message.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT c.rarity, SUM(inv.count) as total
            FROM inventory inv
            JOIN cards c ON inv.card_id = c.id
            WHERE inv.user_id = ?
            GROUP BY c.rarity
            HAVING total >= 3
        """, (uid,)) as cur:
            avail = await cur.fetchall()

    if not avail:
        return await message.reply("❌ Для крафта нужно минимум 3 любые карты одной редкости!")

    buttons = [
        [InlineKeyboardButton(text=f"{r} (Всего: {cnt})", callback_data=f"do_craft_{r}")]
        for r, cnt in avail if RARITY_ORDER.get(r, 0) < len(RARITIES) - 1
    ]
    if not buttons:
        return await message.reply("❌ У вас есть дубликаты только максимальной редкости, их нельзя улучшить!")

    await message.reply("⚒ <b>Выберите редкость карт для скрещивания 3-в-1:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("do_craft_"))
async def execute_craft(callback: CallbackQuery):
    rarity = callback.data.replace("do_craft_", "")
    uid = callback.from_user.id
    current_idx = RARITY_ORDER.get(rarity, 0)
    target_rarity = RARITIES[current_idx + 1]

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT inv.id, inv.card_id, inv.count 
            FROM inventory inv
            JOIN cards c ON inv.card_id = c.id
            WHERE inv.user_id = ? AND c.rarity = ? AND inv.count > 0
            LIMIT 3
        """, (uid, rarity)) as cur:
            rows = await cur.fetchall()

        needed = 3
        for r_id, _, count in rows:
            take = min(needed, count)
            if take == count:
                await db.execute("DELETE FROM inventory WHERE id = ?", (r_id,))
            else:
                await db.execute("UPDATE inventory SET count = count - ? WHERE id = ?", (take, r_id))
            needed -= take
            if needed == 0:
                break

        if needed > 0:
            return await callback.answer("Недостаточно карт для крафта!", show_alert=True)

        is_lottery = current_idx >= RARITY_ORDER["Легендарное"]
        success = True
        lottery_coins = 0
        if is_lottery:
            if random.random() > 0.35:
                success = False
                lottery_coins = random.choices([50, 100, 150, 200, 350], weights=[40, 25, 18, 12, 5])[0]
                await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (lottery_coins, uid))

        if success:
            async with db.execute("SELECT id, file_id, description FROM cards WHERE rarity = ? ORDER BY RANDOM() LIMIT 1", (target_rarity,)) as cur:
                new_card = await cur.fetchone()
            if not new_card:
                await db.rollback()
                return await callback.message.edit_text(f"❌ В базе пока нет карт ранга {target_rarity}! Карты не были списаны.")

            c_id, f_id, desc = new_card
            async with db.execute("SELECT id FROM inventory WHERE user_id = ? AND card_id = ?", (uid, c_id)) as cur:
                exists = await cur.fetchone()
                if exists:
                    await db.execute("UPDATE inventory SET count = count + 1 WHERE id = ?", (exists[0],))
                else:
                    await db.execute("INSERT INTO inventory (user_id, card_id, count) VALUES (?, ?, 1)", (uid, c_id))
            await db.commit()

            text = (
                f"🎉 <b>УСПЕШНЫЙ КРАФТ!</b>\n\n"
                f"Скрестив 3 карты ранга <i>{rarity}</i>, вы получили:\n"
                f"🎴 <b>Карта #{c_id}</b> ({target_rarity})\n"
                f"📝 {desc}"
            )
            await callback.message.delete()
            await callback.message.answer_photo(photo=f_id, caption=text)
        else:
            await db.commit()
            await callback.message.edit_text(
                f"💥 <b>Крафт не удался!</b>\n"
                f"Слияние карт ранга <i>{rarity}</i> оказалось нестабильным, но вы извлекли чистую энергию: "
                f"<b>+{lottery_coins} 🪙 фемкоинов!</b>"
            )

# 6. Трейд
trades: Dict[str, dict] = {}

@dp.message(lambda msg: msg.text and any(get_normalized_command(msg.text) == p or msg.text.lower().strip().startswith(p) for p in ["трейд", "/trade", "trade"]))
async def trade_init(message: Message):
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return await message.reply("❗ Команда должна быть ответом на сообщение игрока, с которым вы хотите обменяться!")

    sender = message.from_user
    receiver = message.reply_to_message.from_user

    if receiver.id == sender.id or receiver.is_bot:
        return await message.reply("❌ Нельзя торговать с самим собой или ботами!")

    p1_name = sender.username or sender.full_name
    p2_name = receiver.username or receiver.full_name

    trade_id = f"tr_{sender.id}_{receiver.id}_{int(time.time())}"
    trades[trade_id] = {
        "p1": sender.id,
        "p2": receiver.id,
        "p1_name": p1_name,
        "p2_name": p2_name,
        "p1_card": None,
        "p2_card": None,
        "p1_ok": False,
        "p2_ok": False,
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Принять вызов на обмен", callback_data=f"trade_accept_{trade_id}")]
    ])
    await message.reply(
        f"🤝 <b>{p1_name}</b> предлагает обмен картами игроку <b>{p2_name}</b>!\n"
        f"<i>Правило: меняться можно только картами равной редкости, и только повторными (единственную отдать нельзя)!</i>",
        reply_markup=kb,
    )

@dp.callback_query(F.data.startswith("trade_accept_"))
async def trade_accepted(callback: CallbackQuery):
    trade_id = callback.data.replace("trade_accept_", "")
    t = trades.get(trade_id)
    if not t:
        return await callback.answer("Сессия обмена истекла!", show_alert=True)
    if callback.from_user.id != t["p2"]:
        return await callback.answer("Этот обмен предложен не вам!", show_alert=True)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Выбрать карту ({t['p1_name']})", callback_data=f"tr_pick_{trade_id}_{t['p1']}")],
        [InlineKeyboardButton(text=f"Выбрать карту ({t['p2_name']})", callback_data=f"tr_pick_{trade_id}_{t['p2']}")],
        [InlineKeyboardButton(text="✅ Подтвердить обмен", callback_data=f"tr_confirm_{trade_id}")]
    ])
    await callback.message.edit_text("🔄 <b>Окно обмена открыто!</b> Оба игрока должны выбрать карту дубликат одинаковой редкости.", reply_markup=kb)

@dp.callback_query(F.data.startswith("tr_pick_"))
async def trade_pick(callback: CallbackQuery):
    _, _, trade_id, uid_str = callback.data.split("_")
    uid = int(uid_str)
    if callback.from_user.id != uid:
        return await callback.answer("Это кнопка для другого игрока!", show_alert=True)

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT c.id, c.rarity, inv.count
            FROM inventory inv
            JOIN cards c ON inv.card_id = c.id
            WHERE inv.user_id = ? AND inv.count > 1
        """, (uid,)) as cur:
            duplicates = await cur.fetchall()

    if not duplicates:
        return await callback.answer("У вас нет повторных карт для обмена!", show_alert=True)

    btns = [
        [InlineKeyboardButton(text=f"#{c_id} ({r}) [x{cnt}]", callback_data=f"tr_set_{trade_id}_{c_id}")]
        for c_id, r, cnt in duplicates
    ]
    await callback.message.reply("Выберите дубликат для предложения:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await callback.answer()

@dp.callback_query(F.data.startswith("tr_set_"))
async def trade_set_card(callback: CallbackQuery):
    _, _, trade_id, card_id_str = callback.data.split("_")
    c_id = int(card_id_str)
    t = trades.get(trade_id)
    if not t:
        return await callback.answer("Сессия истекла!", show_alert=True)

    uid = callback.from_user.id
    if uid == t["p1"]:
        t["p1_card"] = c_id
    elif uid == t["p2"]:
        t["p2_card"] = c_id
    else:
        return await callback.answer("Вы не участник сделки!", show_alert=True)

    await callback.message.edit_text(f"Вы выбрали карту #{c_id} для обмена. Вернитесь к основному окну.")

@dp.callback_query(F.data.startswith("tr_confirm_"))
async def trade_confirm(callback: CallbackQuery):
    trade_id = callback.data.replace("tr_confirm_", "")
    t = trades.get(trade_id)
    if not t:
        return await callback.answer("Сессия истекла!", show_alert=True)

    uid = callback.from_user.id
    if uid == t["p1"]:
        t["p1_ok"] = True
    elif uid == t["p2"]:
        t["p2_ok"] = True

    if not (t["p1_card"] and t["p2_card"]):
        return await callback.answer("Оба участника должны выбрать карты!", show_alert=True)

    if not (t["p1_ok"] and t["p2_ok"]):
        return await callback.answer("Ожидание подтверждения второго участника!", show_alert=True)

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT rarity FROM cards WHERE id = ?", (t["p1_card"],)) as cur:
            r1 = (await cur.fetchone())[0]
        async with db.execute("SELECT rarity FROM cards WHERE id = ?", (t["p2_card"],)) as cur:
            r2 = (await cur.fetchone())[0]

        if r1 != r2:
            return await callback.message.reply(f"❌ Обмен невозможен! Редкости не совпадают ({r1} ≠ {r2}).")

        await db.execute("UPDATE inventory SET count = count - 1 WHERE user_id = ? AND card_id = ?", (t["p1"], t["p1_card"]))
        await db.execute("UPDATE inventory SET count = count - 1 WHERE user_id = ? AND card_id = ?", (t["p2"], t["p2_card"]))

        for u, c in [(t["p1"], t["p2_card"]), (t["p2"], t["p1_card"])]:
            async with db.execute("SELECT id FROM inventory WHERE user_id = ? AND card_id = ?", (u, c)) as cur:
                row = await cur.fetchone()
                if row:
                    await db.execute("UPDATE inventory SET count = count + 1 WHERE id = ?", (row[0],))
                else:
                    await db.execute("INSERT INTO inventory (user_id, card_id, count) VALUES (?, ?, 1)", (u, c))

        await db.commit()

    del trades[trade_id]
    await callback.message.edit_text("🎉 <b>Обмен успешно завершён!</b> Игроки обменялись картами равной редкости.")

# 7. Дейлик
@dp.message(lambda msg: match_command(msg, ["daily", "дейлик"]))
async def daily_command(message: Message):
    uid = message.from_user.id
    now = time.time()

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT daily_streak, last_daily_time FROM users WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
            streak, last_time = row[0] or 0, row[1] or 0

        diff = now - last_time
        if diff < 86400:
            rem = 86400 - diff
            h, m = int(rem // 3600), int((rem % 3600) // 60)
            return await message.reply(f"⏳ Вы уже забрали дейлик! Приходите через {h}ч {m}м.")

        if diff <= 86400 * 2:
            streak += 1
        else:
            streak = 1

        streak = min(streak, 30)
        reward = 20 + (streak * 10)

        await db.execute("""
            UPDATE users 
            SET daily_streak = ?, last_daily_time = ?, femcoins = femcoins + ? 
            WHERE user_id = ?
        """, (streak, now, reward, uid))
        await db.commit()

    await message.reply(
        f"📅 <b>Ежедневная награда получена!</b>\n\n"
        f"🔥 <b>Ваша текущая серия:</b> {streak} дн.\n"
        f"💰 <b>Начислено:</b> +{reward} 🪙 фемкоинов!\n"
        f"<i>Не пропускайте дни, чтобы увеличивать бонус!</i>"
    )

# 8. Топ без @
@dp.message(lambda msg: match_command(msg, ["top", "топ"]))
async def top_command(message: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT u.username, COUNT(inv.id) as total_c, u.first_seen
            FROM users u
            LEFT JOIN inventory inv ON u.user_id = inv.user_id
            GROUP BY u.user_id
            ORDER BY total_c DESC, u.first_seen ASC
            LIMIT 10
        """) as cur:
            rows = await cur.fetchall()

    text = "🏆 <b>ТОП 10 КОЛЛЕКЦИОНЕРОВ</b>\n\n"
    for i, (uname, total, _) in enumerate(rows, 1):
        clean_name = (uname or "Аноним").lstrip("@")
        text += f"{i}. <b>{clean_name}</b> — {total} карт\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Показать ТОП 100 в ЛС", callback_data="top_100_dm")]
    ])
    await message.reply(text, reply_markup=kb)

@dp.callback_query(F.data == "top_100_dm")
async def send_top_100(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT u.username, COUNT(inv.id) as total_c, u.first_seen
            FROM users u
            LEFT JOIN inventory inv ON u.user_id = inv.user_id
            GROUP BY u.user_id
            ORDER BY total_c DESC, u.first_seen ASC
            LIMIT 100
        """) as cur:
            rows = await cur.fetchall()

    text = "📜 <b>ТОП 100 ИГРОКОВ:</b>\n\n"
    for i, (uname, total, _) in enumerate(rows, 1):
        clean_name = (uname or "Аноним").lstrip("@")
        text += f"{i}. {clean_name} — {total} шт.\n"

    try:
        await bot.send_message(callback.from_user.id, text)
        await callback.answer("Топ 100 отправлен вам в ЛС!", show_alert=True)
    except Exception:
        await callback.answer("Сначала откройте диалог с ботом!", show_alert=True)

# 9. Магазин
@dp.message(lambda msg: match_command(msg, ["shop", "магазин"]))
async def shop_command(message: Message):
    bot_me = await bot.get_me()
    if message.chat.type != "private":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏪 Перейти в магазин", url=f"https://t.me/{bot_me.username}?start=shop")]
        ])
        return await message.reply("🏪 Магазин доступен только в личных сообщениях с ботом!", reply_markup=kb)

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT femcoins FROM users WHERE user_id = ?", (message.from_user.id,)) as cur:
            row = await cur.fetchone()
            coins = row[0] if row else 0

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Сброс КД (150 🪙)", callback_data="buy_cd_reset")],
        [InlineKeyboardButton(text="🍀 Эликсир удачи на 3ч (250 🪙)", callback_data="buy_luck")],
        [InlineKeyboardButton(text="🎴 Случайная новая карта (400 🪙)", callback_data="buy_new_card")],
        [InlineKeyboardButton(text="⭐ Купить 250 🪙 за 15 TG Stars", callback_data="stars_250")],
        [InlineKeyboardButton(text="⭐ Купить 1000 🪙 за 50 TG Stars", callback_data="stars_1000")],
    ])

    await message.reply(
        f"🏪 <b>Магазин предметов</b>\n\n"
        f"Ваш баланс: <b>{coins} 🪙 фемкоинов</b>\n\n"
        f"• <b>Сброс КД:</b> сбрасывает ожидание 3ч (сгорает через 3ч, если не использовать).\n"
        f"• <b>Эликсир удачи:</b> повышает шансы на редкие ранги на 3 часа.\n"
        f"• <b>Новая карта:</b> гарантированно дает карту, которой у вас еще нет!\n"
        f"• <b>TG Stars:</b> покупка валюты напрямую за звезды.",
        reply_markup=kb,
    )

@dp.callback_query(F.data == "buy_cd_reset")
async def buy_cd(callback: CallbackQuery):
    uid = callback.from_user.id
    now = time.time()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT femcoins FROM users WHERE user_id = ?", (uid,)) as cur:
            coins = (await cur.fetchone())[0]
        if coins < 150:
            return await callback.answer("Недостаточно фемкоинов (нужно 150)!", show_alert=True)
        await db.execute("""
            UPDATE users 
            SET femcoins = femcoins - 150, has_cd_reset = 1, cd_reset_expires = ?
            WHERE user_id = ?
        """, (now + 3 * 3600, uid))
        await db.commit()
    await callback.answer("Сброс КД куплен! Он потратится при следующем вызове 'фем'.", show_alert=True)

@dp.callback_query(F.data == "buy_luck")
async def buy_luck(callback: CallbackQuery):
    uid = callback.from_user.id
    now = time.time()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT femcoins FROM users WHERE user_id = ?", (uid,)) as cur:
            coins = (await cur.fetchone())[0]
        if coins < 250:
            return await callback.answer("Недостаточно фемкоинов (нужно 250)!", show_alert=True)
        await db.execute("""
            UPDATE users 
            SET femcoins = femcoins - 250, luck_expires = ?
            WHERE user_id = ?
        """, (now + 3 * 3600, uid))
        await db.commit()
    await callback.answer("🍀 Эликсир активирован на 3 часа! Шансы повышены.", show_alert=True)

@dp.callback_query(F.data == "buy_new_card")
async def buy_new_card(callback: CallbackQuery):
    uid = callback.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT femcoins FROM users WHERE user_id = ?", (uid,)) as cur:
            coins = (await cur.fetchone())[0]
        if coins < 400:
            return await callback.answer("Недостаточно фемкоинов (нужно 400)!", show_alert=True)

        async with db.execute("""
            SELECT id, file_id, rarity, description FROM cards 
            WHERE id NOT IN (SELECT card_id FROM inventory WHERE user_id = ?)
            ORDER BY RANDOM() LIMIT 1
        """, (uid,)) as cur:
            card = await cur.fetchone()

        if not card:
            return await callback.answer("У вас уже есть ВСЕ существующие карты!", show_alert=True)

        c_id, f_id, rarity, desc = card
        await db.execute("UPDATE users SET femcoins = femcoins - 400 WHERE user_id = ?", (uid,))
        await db.execute("INSERT INTO inventory (user_id, card_id, count) VALUES (?, ?, 1)", (uid, c_id))
        await db.commit()

    await callback.message.answer_photo(photo=f_id, caption=f"🎁 <b>Куплена новая карта #{c_id}!</b>\nРедкость: {rarity}\n{desc}")
    await callback.answer()

@dp.callback_query(F.data.startswith("stars_"))
async def buy_stars_invoice(callback: CallbackQuery):
    amount = int(callback.data.replace("stars_", ""))
    stars_price = 15 if amount == 250 else 50
    prices = [LabeledPrice(label=f"{amount} Фемкоинов", amount=stars_price)]
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"Покупка {amount} Фемкоинов",
        description=f"Пакет игровой валюты {amount} фемкоинов.",
        payload=f"buy_coins_{amount}",
        currency="XTR",
        prices=prices,
        provider_token="",
    )
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_q: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    payload = message.successful_payment.invoice_payload
    if payload.startswith("buy_coins_"):
        coins_to_add = int(payload.replace("buy_coins_", ""))
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (coins_to_add, message.from_user.id))
            await db.commit()
        await message.reply(f"⭐ Оплата прошла успешно! Начислено <b>+{coins_to_add} 🪙 фемкоинов</b>.")

# 10. Промокод
@dp.message(lambda msg: match_command(msg, ["promo", "промо"]))
async def promo_command(message: Message, state: FSMContext):
    await message.reply("🔑 Отправьте промокод следующим сообщением:")
    await state.set_state(UserStates.waiting_for_promo_code)

@dp.message(UserStates.waiting_for_promo_code)
async def process_promo(message: Message, state: FSMContext):
    code = message.text.strip()
    uid = message.from_user.id
    now = time.time()

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT p_type, reward_type, reward_val, expires_at, is_used FROM promo_codes WHERE code = ?", (code,)) as cur:
            promo = await cur.fetchone()

        if not promo:
            await state.clear()
            return await message.reply("❌ Промокод не существует или введен неверно.")

        p_type, r_type, r_val, exp, is_used = promo

        if now > exp:
            await state.clear()
            return await message.reply("⌛ Срок действия этого промокода уже истек.")

        if p_type == "L" and is_used:
            await state.clear()
            return await message.reply("❌ Этот одноразовый промокод уже был активирован кем-то другим.")

        async with db.execute("SELECT 1 FROM promo_claims WHERE user_id = ? AND code = ?", (uid, code)) as cur:
            if await cur.fetchone():
                await state.clear()
                return await message.reply("❌ Вы уже активировали этот промокод!")

        if r_type == "f":
            await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (r_val, uid))
            reward_msg = f"+{r_val} 🪙 фемкоинов"
        else:
            await db.execute("UPDATE users SET has_cd_reset = 1, cd_reset_expires = ? WHERE user_id = ?", (now + 3 * 3600, uid))
            reward_msg = "Сброс КД на 3 часа"

        if p_type == "L":
            await db.execute("UPDATE promo_codes SET is_used = 1 WHERE code = ?", (code,))

        await db.execute("INSERT INTO promo_claims (user_id, code, claimed_at) VALUES (?, ?, ?)", (uid, code, now))
        await db.commit()

    await state.clear()
    await message.reply(f"🎉 Промокод успешно активирован! Ваша награда: <b>{reward_msg}</b>")

# 11. Казино
def build_casino_kb(current_bet: int) -> InlineKeyboardMarkup:
    b10 = f"[{current_bet}🪙]" if current_bet == 10 else "10 🪙"
    b20 = f"[{current_bet}🪙]" if current_bet == 20 else "20 🪙"
    b50 = f"[{current_bet}🪙]" if current_bet == 50 else "50 🪙"
    b100 = f"[{current_bet}🪙]" if current_bet == 100 else "100 🪙"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"{'🔘 ' if current_bet==10 else ''}{b10}", callback_data="setbet_10"),
            InlineKeyboardButton(text=f"{'🔘 ' if current_bet==20 else ''}{b20}", callback_data="setbet_20"),
            InlineKeyboardButton(text=f"{'🔘 ' if current_bet==50 else ''}{b50}", callback_data="setbet_50"),
            InlineKeyboardButton(text=f"{'🔘 ' if current_bet==100 else ''}{b100}", callback_data="setbet_100")
        ],
        [InlineKeyboardButton(text=f"🎲 Бросить кости (Ставка: {current_bet} 🪙)", callback_data="kaz_dice")],
        [
            InlineKeyboardButton(text=f"🔴 Красное (x2, {current_bet})", callback_data="kaz_roul_red"),
            InlineKeyboardButton(text=f"⚫ Черное (x2, {current_bet})", callback_data="kaz_roul_black")
        ],
        [InlineKeyboardButton(text=f"🟢 Зеро (x14, {current_bet})", callback_data="kaz_roul_zero")],
        [InlineKeyboardButton(text=f"🎰 Слоты 777 (Ставка: {current_bet} 🪙)", callback_data="kaz_slots")]
    ])

@dp.message(lambda msg: match_command(msg, ["kaz", "казино"]))
async def casino_menu(message: Message):
    uid = message.from_user.id
    current_bet = user_casino_bets.get(uid, 20)
    kb = build_casino_kb(current_bet)
    await message.reply(
        f"🎰 <b>Добро пожаловать в Фем-Казино!</b>\n"
        f"🎯 Активная ставка: <b>{current_bet} 🪙</b> (переключайте кнопками в первом ряду):\n"
        f"<i>Для Зеро и Рулетки минимальная ставка 20 коинов.</i>",
        reply_markup=kb
    )

@dp.callback_query(F.data.startswith("setbet_"))
async def set_casino_bet(callback: CallbackQuery):
    bet_val = int(callback.data.replace("setbet_", ""))
    uid = callback.from_user.id
    user_casino_bets[uid] = bet_val
    kb = build_casino_kb(bet_val)

    await callback.message.edit_text(
        f"🎰 <b>Добро пожаловать в Фем-Казино!</b>\n"
        f"🎯 Активная ставка переключена на: <b>{bet_val} 🪙</b>\n"
        f"<i>Выберите игру ниже:</i>",
        reply_markup=kb
    )
    await callback.answer(f"Ставка выбрана: {bet_val} 🪙")

@dp.callback_query(F.data.startswith("kaz_"))
async def casino_play(callback: CallbackQuery):
    uid = callback.from_user.id
    u_name = callback.from_user.username or callback.from_user.full_name
    act = callback.data
    bet = user_casino_bets.get(uid, 20)

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT femcoins FROM users WHERE user_id = ?", (uid,)) as cur:
            coins = (await cur.fetchone())[0]

        if act == "kaz_dice":
            if coins < bet:
                return await callback.answer(f"Не хватает фемкоинов (нужно {bet})!", show_alert=True)
            u_roll = random.randint(1, 6)
            b_roll = random.randint(1, 6)
            if u_roll > b_roll:
                await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (bet, uid))
                res = f"🎲 Игрок <b>{u_name}</b> выиграл! ({u_roll} против {b_roll} у бота). Награда: +{bet} 🪙"
            elif u_roll < b_roll:
                await db.execute("UPDATE users SET femcoins = femcoins - ? WHERE user_id = ?", (bet, uid))
                res = f"🎲 Игрок <b>{u_name}</b> проиграл! ({u_roll} против {b_roll} у бота). -{bet} 🪙"
            else:
                res = f"🎲 Игрок <b>{u_name}</b> сыграл вничью! ({u_roll} и {b_roll}). Монеты сохранены."

        elif act.startswith("kaz_roul_"):
            if bet < 20:
                return await callback.answer("Для рулетки и зеро минимальная ставка 20 монет!", show_alert=True)
            choice = act.replace("kaz_roul_", "")
            if coins < bet:
                return await callback.answer(f"Нужно минимум {bet} монет!", show_alert=True)

            num = random.randint(0, 36)
            color = "zero" if num == 0 else ("red" if num % 2 == 1 else "black")

            if choice == color:
                mult = 14 if choice == "zero" else 2
                winnings = bet * mult - bet
                await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (winnings, uid))
                res = f"🎉 <b>{u_name}</b>, выпало {num} ({color})! Вы выиграли +{winnings} 🪙!"
            else:
                await db.execute("UPDATE users SET femcoins = femcoins - ? WHERE user_id = ?", (bet, uid))
                res = f"💀 <b>{u_name}</b>, выпало {num} ({color})! Вы потеряли {bet} 🪙."

        elif act == "kaz_slots":
            if coins < bet:
                return await callback.answer(f"Нужно {bet} монет!", show_alert=True)
            emojis = ["🍒", "🍋", "🍇", "💎", "7️⃣"]
            s1, s2, s3 = random.choices(emojis, k=3)
            if s1 == s2 == s3 == "7️⃣":
                jackpot = bet * 15
                await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (jackpot, uid))
                res = f"🎰 <b>{u_name}</b> [{s1}|{s2}|{s3}] ДЖЕКПОТ 777! +{jackpot} 🪙!"
            elif s1 == s2 == s3:
                win_slots = bet * 4
                await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (win_slots, uid))
                res = f"🎰 <b>{u_name}</b> [{s1}|{s2}|{s3}] ТРИ В РЯД! +{win_slots} 🪙!"
            else:
                await db.execute("UPDATE users SET femcoins = femcoins - ? WHERE user_id = ?", (bet, uid))
                res = f"🎰 <b>{u_name}</b> [{s1}|{s2}|{s3}] Не повезло! -{bet} 🪙."

        await db.commit()

    await callback.message.reply(res)
    await callback.answer()

# Перекати-поле (Сундук ТОЛЬКО в группах при неактивности 15 минут)
async def inactivity_chest_task():
    await asyncio.sleep(10)
    while True:
        try:
            now = time.time()
            async with aiosqlite.connect(DB_NAME) as db:
                # Берем только групповые чаты (исключаем private)
                async with db.execute("SELECT chat_id FROM chats WHERE chat_type IN ('group', 'supergroup')") as cur:
                    chats = await cur.fetchall()

            for (chat_id,) in chats:
                last_act = chat_last_activity.get(chat_id, 0)
                if last_act > 0 and (now - last_act >= 900):
                    chat_last_activity[chat_id] = now

                    chest_id = f"ch_idle_{int(now)}_{random.randint(100, 999)}"
                    reward_coins = random.choices([20, 35, 50, 75, 120], weights=[45, 25, 18, 8, 4])[0]
                    active_chests[chest_id] = {"coins": reward_coins, "claimed": False, "winner_name": None}

                    chest_kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🎁 Забрать сундук!", callback_data=f"claim_chest_{chest_id}")]
                    ])
                    try:
                        await bot.send_message(
                            chat_id,
                            "🌵 <b>В чате тихо... Тут прокатилось перекати-поле и оставило сундук!</b>\n"
                            "<i>Кто первый нажмет на кнопку, заберет сокровища за простой чата!</i>",
                            reply_markup=chest_kb
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Inactivity chest error: {e}")

        await asyncio.sleep(60)

# ----------------- АДМИНКА -----------------
@dp.message(lambda msg: match_command(msg, ["plus_foto"]))
async def plus_foto_cmd(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    await message.reply("Отправьте фото, которое нужно добавить в базу:")
    await state.set_state(AdminStates.waiting_for_photo)

@dp.message(AdminStates.waiting_for_photo, F.photo)
async def process_admin_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(file_id=photo_id)
    btns = [[InlineKeyboardButton(text=r, callback_data=f"setrar_{r}")] for r in RARITIES]
    await message.reply("Выберите редкость для этой карты:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("setrar_"))
async def process_admin_rarity(callback: CallbackQuery, state: FSMContext):
    rarity = callback.data.replace("setrar_", "")
    await state.update_data(rarity=rarity)
    await callback.message.edit_text(f"Редкость: {rarity}. Теперь отправьте текстовое описание для карты:")
    await state.set_state(AdminStates.waiting_for_custom_desc)

@dp.message(AdminStates.waiting_for_custom_desc)
async def process_admin_desc(message: Message, state: FSMContext):
    data = await state.get_data()
    file_id = data["file_id"]
    rarity = data["rarity"]
    desc = message.text

    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("INSERT INTO cards (file_id, rarity, description) VALUES (?, ?, ?)", (file_id, rarity, desc))
        card_id = cur.lastrowid
        await db.commit()

    admin_display = await get_user_display_name(message.from_user.id, clean=True)
    await add_log(message.from_user.id, f"{admin_display} добавил карту #{card_id} [{rarity}]")
    await state.clear()
    await message.reply(f"✅ Карта #{card_id} успешно создана и добавлена в базу!")

@dp.message(lambda msg: msg.text and msg.text.lower().strip() in ["all card", "100 card"])
async def view_cards_album(message: Message):
    if not await is_admin(message.from_user.id):
        return

    limit = 100 if message.text.lower().strip() == "100 card" else 500
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, file_id, rarity FROM cards ORDER BY id DESC LIMIT ?", (limit,)) as cur:
            cards = await cur.fetchall()

    if not cards:
        return await message.reply("В базе нет карт.")

    for i in range(0, len(cards), 10):
        chunk = cards[i:i+10]
        media_group = [InputMediaPhoto(media=c[1], caption=f"ID: {c[0]} | {c[2]}") for c in chunk]
        await message.answer_media_group(media=media_group)
        await asyncio.sleep(0.5)

@dp.message(lambda msg: msg.text and (msg.text.startswith("/card") or msg.text.startswith("card")))
async def card_manage(message: Message):
    if not await is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return
    c_id = int(parts[1])

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT file_id, rarity, description FROM cards WHERE id = ?", (c_id,)) as cur:
            card = await cur.fetchone()

        if not card:
            return await message.reply(f"Карта #{c_id} не найдена.")

        async with db.execute("SELECT COUNT(DISTINCT user_id), SUM(count) FROM inventory WHERE card_id = ?", (c_id,)) as cur:
            stats = await cur.fetchone()
            unique_owners = stats[0] or 0
            total_copies = stats[1] or 0

    f_id, rarity, desc = card
    caption = (
        f"🎴 <b>Профиль карты #{c_id}</b>\n"
        f"🏷 <b>Редкость:</b> {rarity}\n"
        f"📝 <b>Описание:</b> {desc}\n\n"
        f"👥 Уникальных владельцев: {unique_owners}\n"
        f"📦 Всего дубликатов на руках: {total_copies}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить редкость", callback_data=f"editrar_{c_id}")],
        [InlineKeyboardButton(text="📝 Изменить описание", callback_data=f"editdesc_{c_id}")],
        [InlineKeyboardButton(text="🗑 Удалить карту", callback_data=f"delcard_{c_id}")]
    ])
    await message.answer_photo(photo=f_id, caption=caption, reply_markup=kb)

@dp.callback_query(F.data.startswith("editrar_"))
async def edit_card_rarity(callback: CallbackQuery):
    c_id = callback.data.split("_")[1]
    btns = [[InlineKeyboardButton(text=r, callback_data=f"saverar_{c_id}_{r}")] for r in RARITIES]
    await callback.message.reply("Выберите новую редкость:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await callback.answer()

@dp.callback_query(F.data.startswith("saverar_"))
async def save_card_rarity(callback: CallbackQuery):
    _, c_id, r = callback.data.split("_")
    admin_display = await get_user_display_name(callback.from_user.id, clean=True)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE cards SET rarity = ? WHERE id = ?", (r, int(c_id)))
        await db.commit()
    await add_log(callback.from_user.id, f"{admin_display} изменил редкость карты #{c_id} на {r}")
    await callback.message.edit_text(f"Редкость карты #{c_id} изменена на {r}!")

@dp.callback_query(F.data.startswith("delcard_"))
async def delete_card(callback: CallbackQuery):
    c_id = int(callback.data.split("_")[1])
    admin_display = await get_user_display_name(callback.from_user.id, clean=True)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM cards WHERE id = ?", (c_id,))
        await db.execute("DELETE FROM inventory WHERE card_id = ?", (c_id,))
        await db.execute("UPDATE cards SET id = id - 1 WHERE id > ?", (c_id,))
        await db.execute("UPDATE inventory SET card_id = card_id - 1 WHERE card_id > ?", (c_id,))
        await db.commit()
    await add_log(callback.from_user.id, f"{admin_display} удалил карту #{c_id} со сдвигом ID")
    await callback.message.edit_text(f"🗑 Карта #{c_id} удалена, номера последующих карт сдвинуты на -1.")

@dp.message(lambda msg: msg.text and msg.text.lower().strip().startswith("твой профиль"))
async def forced_profile(message: Message):
    if not await is_admin(message.from_user.id):
        return

    target_user = None
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
    else:
        parts = message.text.split()
        if len(parts) > 2:
            uname = parts[2].lstrip("@")
            async with aiosqlite.connect(DB_NAME) as db:
                async with db.execute("SELECT user_id FROM users WHERE username = ? OR username = ?", (uname, f"@{uname}")) as cur:
                    row = await cur.fetchone()
                    if row:
                        target_user = types.User(id=row[0], is_bot=False, first_name=uname)

    if not target_user:
        return await message.reply("Укажите пользователя ответом или @username!")

    await show_user_profile(message.chat.id, target_user)

@dp.message(lambda msg: match_command(msg, ["admins"]))
async def list_admins(message: Message):
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM admins") as cur:
            rows = await cur.fetchall()

    if not rows:
        return await message.reply("Дополнительных админов нет.")

    buttons = []
    for (uid,) in rows:
        disp_name = await get_user_display_name(uid, clean=True)
        buttons.append([InlineKeyboardButton(text=f"❌ Снять {disp_name}", callback_data=f"removeadm_{uid}")])

    await message.reply("Список действующих администраторов:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("removeadm_"))
async def remove_adm_cb(callback: CallbackQuery):
    if callback.from_user.id != SUPER_ADMIN_ID:
        return
    uid = int(callback.data.replace("removeadm_", ""))
    disp_name = await get_user_display_name(uid, clean=True)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (uid,))
        await db.commit()
    await callback.message.edit_text(f"Администратор {disp_name} успешно снят.")

broadcast_buffer: Dict[int, List[Message]] = {}

@dp.message(lambda msg: match_command(msg, ["add"]))
async def start_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != SUPER_ADMIN_ID:
        return
    broadcast_buffer[message.from_user.id] = []
    await state.set_state(AdminStates.broadcast_collecting)
    await message.reply("Отправляйте сообщения для рассылки. Когда закончите, напишите /save.")

@dp.message(AdminStates.broadcast_collecting)
async def collect_broadcast(message: Message, state: FSMContext):
    if message.text and match_command(message, ["save"]):
        msgs = broadcast_buffer.get(message.from_user.id, [])
        await state.clear()
        if not msgs:
            return await message.reply("Ни одного сообщения не было отправлено. Рассылка отменена.")

        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT chat_id FROM chats") as cur:
                chats = await cur.fetchall()

        count = 0
        for (c_id,) in chats:
            try:
                for m in msgs:
                    await bot.copy_message(chat_id=c_id, from_chat_id=m.chat.id, message_id=m.message_id)
                count += 1
                await asyncio.sleep(0.05)
            except Exception:
                continue

        del broadcast_buffer[message.from_user.id]
        return await message.reply(f"✅ Рассылка успешно завершена в {count} чатов!")

    broadcast_buffer[message.from_user.id].append(message)
    await message.reply("Сообщение добавлено в буфер рассылки.")

@dp.message(lambda msg: match_command(msg, ["now_admin_cod"]))
async def gen_admin_code(message: Message):
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    digits = "".join(random.choices(string.digits, k=7))
    letters = "".join(random.choices(string.ascii_letters, k=3))
    code_list = list(digits + letters)
    random.shuffle(code_list)
    code = "".join(code_list)

    exp = time.time() + 3 * 3600
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO admin_codes (code, created_by, expires_at) VALUES (?, ?, ?)", (code, message.from_user.id, exp))
        await db.commit()

    await message.reply(f"🔑 Одноразовый код администратора: <code>{code}</code>\nДействует 3 часа.")

@dp.message(lambda msg: match_command(msg, ["I_am_admin"]))
async def claim_admin(message: Message, state: FSMContext):
    await message.reply("Введите 10-значный код администратора:")
    await state.set_state(AdminStates.waiting_admin_code)

@dp.message(AdminStates.waiting_admin_code)
async def check_admin_code(message: Message, state: FSMContext):
    code = message.text.strip()
    now = time.time()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT created_by, expires_at, used FROM admin_codes WHERE code = ?", (code,)) as cur:
            row = await cur.fetchone()

        if not row or row[2] == 1 or now > row[1]:
            await state.clear()
            return await message.reply("❌ Недействительный или истекший код.")

        await db.execute("UPDATE admin_codes SET used = 1 WHERE code = ?", (code,))
        await db.execute("INSERT OR IGNORE INTO admins (user_id, added_by, added_at) VALUES (?, ?, ?)", (message.from_user.id, row[0], now))
        await db.commit()

    await state.clear()
    disp_name = await get_user_display_name(message.from_user.id, clean=True)
    await add_log(message.from_user.id, f"{disp_name} стал администратором через инвайт-код")
    await message.reply("🎉 Поздравляем! Вы получили права администратора бота.")

@dp.message(lambda msg: msg.text and msg.text.startswith("/create_p"))
async def create_promo_cmd(message: Message, state: FSMContext):
    if message.from_user.id != SUPER_ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 4:
        return await message.reply("Формат:\n/create_p <L/M> <f/k> <монеты> <часы>\nили\n/create_p <L/M> k <часы>")

    p_type = parts[1].upper()
    r_type = parts[2].lower()

    if r_type == "f":
        coins = int(parts[3])
        hours = float(parts[4])
    else:
        coins = 0
        hours = float(parts[3])

    exp = time.time() + hours * 3600
    await state.update_data(p_type=p_type, r_type=r_type, r_val=coins, exp=exp)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Автогенерация", callback_data="gen_promo_auto")],
        [InlineKeyboardButton(text="✍️ Самостоятельно", callback_data="gen_promo_manual")]
    ])
    await message.reply("Выберите способ создания кода:", reply_markup=kb)

@dp.callback_query(F.data == "gen_promo_auto")
async def promo_auto(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO promo_codes (code, p_type, reward_type, reward_val, expires_at)
            VALUES (?, ?, ?, ?, ?)
        """, (code, data["p_type"], data["r_type"], data["r_val"], data["exp"]))
        await db.commit()
    await state.clear()
    await callback.message.edit_text(f"✅ Промокод успешно создан: <code>{code}</code>")

@dp.callback_query(F.data == "gen_promo_manual")
async def promo_manual(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Отправьте промокод следующим сообщением:")
    await state.set_state(AdminStates.waiting_custom_promo)

@dp.message(AdminStates.waiting_custom_promo)
async def promo_manual_save(message: Message, state: FSMContext):
    code = message.text.strip()
    data = await state.get_data()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO promo_codes (code, p_type, reward_type, reward_val, expires_at)
            VALUES (?, ?, ?, ?, ?)
        """, (code, data["p_type"], data["r_type"], data["r_val"], data["exp"]))
        await db.commit()
    await state.clear()
    await message.reply(f"✅ Промокод сохранен: <code>{code}</code>")

@dp.message(lambda msg: match_command(msg, ["log"]))
async def view_logs(message: Message):
    if message.from_user.id != SUPER_ADMIN_ID:
        return
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT admin_id, action, timestamp FROM logs ORDER BY id DESC LIMIT 25") as cur:
            rows = await cur.fetchall()

    if not rows:
        return await message.reply("Логи пусты.")

    text = "📋 <b>Последние 25 действий администрации:</b>\n\n"
    for a_id, act, ts in rows:
        dt = datetime.fromtimestamp(ts).strftime("%d.%m %H:%M")
        adm_name = await get_user_display_name(a_id, clean=True)
        text += f"[{dt}] <b>{adm_name}:</b> {act}\n"

    await message.reply(text)

async def resolve_target(message: Message, arg_index: int = -1) -> Optional[int]:
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    parts = message.text.split()
    if len(parts) > arg_index and parts[arg_index].startswith("@"):
        uname = parts[arg_index].lstrip("@")
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT user_id FROM users WHERE username = ? OR username = ?", (uname, f"@{uname}")) as cur:
                row = await cur.fetchone()
                return row[0] if row else None
    return None

@dp.message(lambda msg: msg.text and any(msg.text.startswith(p) for p in ["/бот +коины", "/бот -коины", "бот +коины", "бот -коины"]))
async def mod_coins(message: Message):
    if message.from_user.id != SUPER_ADMIN_ID:
        return
    parts = message.text.split()
    amount = int(parts[2])
    target_id = await resolve_target(message, 3)
    if not target_id:
        return await message.reply("Укажите пользователя тегом или реплаем!")

    target_name = await get_user_display_name(target_id, clean=True)
    is_plus = "+коины" in parts[1]
    async with aiosqlite.connect(DB_NAME) as db:
        if is_plus:
            await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (amount, target_id))
        else:
            await db.execute("UPDATE users SET femcoins = MAX(0, femcoins - ?) WHERE user_id = ?", (amount, target_id))
        await db.commit()

    await add_log(message.from_user.id, f"{'+' if is_plus else '-'}{amount} коинов для {target_name}")
    await message.reply(f"Баланс игрока <b>{target_name}</b> изменен на {'+' if is_plus else '-'}{amount} 🪙.")

@dp.message(lambda msg: msg.text and any(msg.text.startswith(p) for p in ["/бот +карта", "/бот -карта", "бот +карта", "бот -карта"]))
async def mod_cards(message: Message):
    if message.from_user.id != SUPER_ADMIN_ID:
        return
    parts = message.text.split()
    card_id = int(parts[2])
    target_id = await resolve_target(message, 3)
    if not target_id:
        return await message.reply("Укажите пользователя тегом или реплаем!")

    target_name = await get_user_display_name(target_id, clean=True)
    is_plus = "+карта" in parts[1]
    async with aiosqlite.connect(DB_NAME) as db:
        if is_plus:
            async with db.execute("SELECT id FROM inventory WHERE user_id = ? AND card_id = ?", (target_id, card_id)) as cur:
                row = await cur.fetchone()
                if row:
                    await db.execute("UPDATE inventory SET count = count + 1 WHERE id = ?", (row[0],))
                else:
                    await db.execute("INSERT INTO inventory (user_id, card_id, count) VALUES (?, ?, 1)", (target_id, card_id))
            action_desc = f"Выдана карта #{card_id} для {target_name}"
        else:
            await db.execute("DELETE FROM inventory WHERE user_id = ? AND card_id = ?", (target_id, card_id))
            action_desc = f"Изъята карта #{card_id} со всеми копиями у {target_name}"
        await db.commit()

    await add_log(message.from_user.id, action_desc)
    await message.reply(f"Готово: {action_desc}.")

@dp.message(lambda msg: msg.text and any(msg.text.startswith(p) for p in ["/бот кд", "бот кд"]))
async def mod_cd(message: Message):
    if message.from_user.id != SUPER_ADMIN_ID:
        return
    parts = message.text.split()
    new_cd = float(parts[2])
    duration_hours = float(parts[3])
    target_id = await resolve_target(message, 4)

    if not target_id:
        return await message.reply("Укажите пользователя тегом или ответом!")

    target_name = await get_user_display_name(target_id, clean=True)
    exp = -1 if duration_hours == -1 else time.time() + duration_hours * 3600
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE users 
            SET custom_cd_hours = ?, custom_cd_expires = ?
            WHERE user_id = ?
        """, (new_cd, exp, target_id))
        await db.commit()

    await add_log(message.from_user.id, f"Установлен КД {new_cd}ч игроку {target_name} на {duration_hours}ч")
    await message.reply(f"Игроку <b>{target_name}</b> установлен КД {new_cd} ч на {duration_hours} ч.")

@dp.message(lambda msg: match_command(msg, ["set_adm_text"]))
async def set_adm_text_cmd(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    await message.reply("Отправьте текст, который будет выводиться по команде «кто админ»:")
    await state.set_state(AdminStates.waiting_set_adm_text)

@dp.message(AdminStates.waiting_set_adm_text)
async def save_adm_text(message: Message, state: FSMContext):
    text = message.text
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_info', ?)", (text,))
        await db.commit()
    await state.clear()
    admin_name = await get_user_display_name(message.from_user.id, clean=True)
    await add_log(message.from_user.id, f"{admin_name} обновил текст 'кто админ'")
    await message.reply("✅ Текст администратора успешно сохранен!")

# ----------------- ЗАПУСК -----------------
async def main():
    await init_db()
    asyncio.create_task(inactivity_chest_task())
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logger.warning(f"Webhook reset warning: {e}")
    logger.info("База данных инициализирована. Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
