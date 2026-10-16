import asyncio
import logging
import random
import string
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from aiogram import Bot, Dispatcher, F, types
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
import aiosqlite

# ----------------- КОНФИГУРАЦИЯ -----------------
BOT_TOKEN = "ВАШ_ТОКЕН_БОТА"
SUPER_ADMIN_ID = 123456789  # Замените на ваш Telegram ID
DB_NAME = "fem_bot.db"

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

# ----------------- FSM СОСТОЯНИЯ -----------------
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
                first_seen REAL DEFAULT 0
            )
        """)
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
                p_type TEXT, -- L или M
                reward_type TEXT, -- f или k
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

# Хелперы работы с БД
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
            if not row:
                now = time.time()
                await db.execute(
                    "INSERT INTO users (user_id, username, first_seen) VALUES (?, ?, ?)",
                    (user.id, user.username or user.full_name, now),
                )
                await db.commit()
            else:
                if user.username and user.username != row[1]:
                    await db.execute(
                        "UPDATE users SET username = ? WHERE user_id = ?",
                        (user.username, user.id),
                    )
                    await db.commit()

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

# ----------------- БОТ И ДИСПЕТЧЕР -----------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Мидлварь регистрации
@dp.message.outer_middleware()
async def register_middleware(handler, event: Message, data: dict):
    if event.from_user:
        await get_or_create_user(event.from_user)
    if event.chat:
        await register_chat(event.chat.id, event.chat.type)
    return await handler(event, data)

# ----------------- ПОЛЬЗОВАТЕЛЬСКИЙ БЛОК -----------------

# 1. /foto и триггер "фем"
@dp.message(F.text.lower().in_(["/foto", "фем"]))
async def fem_command(message: Message):
    uid = message.from_user.id
    now = time.time()
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT last_fem_time, custom_cd_hours, custom_cd_expires, has_cd_reset, cd_reset_expires, luck_expires FROM users WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
        
        last_fem, custom_cd, custom_exp, has_reset, reset_exp, luck_exp = row
        
        # Проверка кд
        effective_cd = 3.0
        if custom_exp == -1 or custom_exp > now:
            effective_cd = custom_cd
        
        cd_seconds = effective_cd * 3600
        time_passed = now - last_fem
        
        if time_passed < cd_seconds:
            # Проверяем, есть ли купленный сброс кд
            if has_reset and (reset_exp > now):
                # Тратим сброс
                await db.execute("UPDATE users SET has_cd_reset = 0 WHERE user_id = ?", (uid,))
                await db.commit()
                await message.reply("🔄 Использован купленный сброс ожидания! Крутим фото...")
            else:
                rem = cd_seconds - time_passed
                hours = int(rem // 3600)
                minutes = int((rem % 3600) // 60)
                seconds = int(rem % 60)
                return await message.reply(f"⏳ Рано! До следующей попытки осталось: {hours}ч {minutes}м {seconds}с.")

        # Определение шансов (с учётом эликсира)
        is_lucky = luck_exp > now
        weights = LUCK_WEIGHTS if is_lucky else BASE_WEIGHTS
        chosen_rarity = random.choices(list(weights.keys()), weights=list(weights.values()))[0]

        # Выбираем случайную карту этой редкости
        async with db.execute("SELECT id, file_id, description FROM cards WHERE rarity = ? ORDER BY RANDOM() LIMIT 1", (chosen_rarity,)) as cur:
            card = await cur.fetchone()

        if not card:
            # Fallback на любую доступную карту
            async with db.execute("SELECT id, file_id, description, rarity FROM cards ORDER BY RANDOM() LIMIT 1") as cur:
                card = await cur.fetchone()
                if not card:
                    return await message.reply("😔 Карты ещё не добавлены администраторами. Подождите немного!")
                chosen_rarity = card[3]

        c_id, f_id, desc = card[0], card[1], card[2]

        # Добавляем в инвентарь
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

    # Инлайн кнопки под постом (настраиваемые в settings: user_post_buttons)
    kb = None
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM settings WHERE key = 'post_btn_url'") as cur:
            btn_row = await cur.fetchone()
            if btn_row and btn_row[0]:
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Присоединиться / Подробнее", url=btn_row[0])]])

    await message.answer_photo(photo=f_id, caption=caption, parse_mode="HTML", reply_markup=kb)

# 2. /profil и триггер "профиль"
@dp.message(F.text.lower().in_(["/profil", "профиль"]))
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
    caption = (
        f"👤 <b>Профиль игрока:</b> {user.full_name}\n"
        f"💰 <b>Фемкоины:</b> {coins} 🪙\n"
        f"🎴 <b>Собрано карт:</b> {user_cards_count} из {total_cards}"
    )

    if photos.total_count > 0:
        await bot.send_photo(chat_id, photos.photos[0][-1].file_id, caption=caption, parse_mode="HTML", reply_markup=kb, reply_to_message_id=reply_to)
    else:
        await bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=kb, reply_to_message_id=reply_to)

# Листалка карт в профиле
@dp.callback_query(F.data.startswith("viewcards_"))
async def browse_cards(callback: CallbackQuery):
    _, uid_str, idx_str = callback.data.split("_")
    uid = int(uid_str)
    idx = int(idx_str)

    if callback.from_user.id != uid:
        return await callback.answer("Это не ваш профиль!", show_alert=True)

    async with aiosqlite.connect(DB_NAME) as db:
        # Сортируем от самых редких к менее редким
        await db.execute("""
            SELECT c.id, c.file_id, c.rarity, c.description, inv.count
            FROM inventory inv
            JOIN cards c ON inv.card_id = c.id
            WHERE inv.user_id = ?
        """, (uid,))
        async with db.execute("""
            SELECT c.id, c.file_id, c.rarity, c.description, inv.count
            FROM inventory inv
            JOIN cards c ON inv.card_id = c.id
            WHERE inv.user_id = ?
        """, (uid,)) as cur:
            cards = await cur.fetchall()

    if not cards:
        return await callback.answer("У вас ещё нет карт!", show_alert=True)

    # Сортируем в Python по убыванию ранга (от Редчайший фем к Редкое)
    cards.sort(key=lambda x: RARITY_ORDER.get(x[2], 0), reverse=True)
    idx = max(0, min(idx, len(cards) - 1))
    c = cards[idx]

    c_id, f_id, rarity, desc, cnt = c

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
            media=InputMediaPhoto(media=f_id, caption=text, parse_mode="HTML"),
            reply_markup=kb,
        )
    except Exception:
        # Если было текстовое сообщение, удаляем и шлем фото
        await callback.message.delete()
        await callback.message.answer_photo(photo=f_id, caption=text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()

# 3. /admin и триггер "кто админ"
@dp.message(F.text.lower().in_(["/admin", "кто админ"]))
async def who_admin(message: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM settings WHERE key = 'admin_info'") as cur:
            row = await cur.fetchone()
    text = row[0] if row else "Администратор ещё не настроил информацию о себе."
    await message.reply(text, parse_mode="HTML")

# 4. /craft и триггер "крафт"
@dp.message(F.text.lower().in_(["/craft", "крафт"]))
async def craft_command(message: Message):
    uid = message.from_user.id
    # Ищем редкости, где сумма карт >= 3
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

    await message.reply("⚒ <b>Выберите редкость карт для скрещивания 3-в-1:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@dp.callback_query(F.data.startswith("do_craft_"))
async def execute_craft(callback: CallbackQuery):
    rarity = callback.data.replace("do_craft_", "")
    uid = callback.from_user.id
    current_idx = RARITY_ORDER.get(rarity, 0)
    target_rarity = RARITIES[current_idx + 1]

    async with aiosqlite.connect(DB_NAME) as db:
        # Извлекаем 3 карты
        async with db.execute("""
            SELECT inv.id, inv.card_id, inv.count 
            FROM inventory inv
            JOIN cards c ON inv.card_id = c.id
            WHERE inv.user_id = ? AND c.rarity = ? AND inv.count > 0
            LIMIT 3
        """, (uid, rarity)) as cur:
            rows = await cur.fetchall()

        needed = 3
        taken_ids = []
        for r_id, c_id, count in rows:
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

        # Проверяем лотерею для Легендарных и выше
        is_lottery = current_idx >= RARITY_ORDER["Легендарное"]
        success = True
        lottery_coins = 0
        if is_lottery:
            # 35% шанс успеха
            if random.random() > 0.35:
                success = False
                # Награда фемкоинов 50..350
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
            await callback.message.answer_photo(photo=f_id, caption=text, parse_mode="HTML")
        else:
            await db.commit()
            await callback.message.edit_text(
                f"💥 <b>Крафт не удался!</b>\n"
                f"Слияние карт ранга <i>{rarity}</i> оказалось нестабильным, но вы извлекли чистую энергию: "
                f"<b>+{lottery_coins} 🪙 фемкоинов!</b>",
                parse_mode="HTML"
            )

# 5. Трейд (Ответ на сообщение)
trades: Dict[str, dict] = {}

@dp.message(F.text.lower().startswith("трейд"))
async def trade_init(message: Message):
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return await message.reply("❗ Команда должна быть ответом на сообщение игрока, с которым вы хотите обменяться!")
    
    sender = message.from_user
    receiver = message.reply_to_message.from_user

    if receiver.id == sender.id or receiver.is_bot:
        return await message.reply("❌ Нельзя торговать с самим собой или ботами!")

    trade_id = f"tr_{sender.id}_{receiver.id}_{int(time.time())}"
    trades[trade_id] = {
        "p1": sender.id,
        "p2": receiver.id,
        "p1_name": sender.full_name,
        "p2_name": receiver.full_name,
        "p1_card": None,
        "p2_card": None,
        "p1_ok": False,
        "p2_ok": False,
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Принять вызов на обмен", callback_data=f"trade_accept_{trade_id}")]
    ])
    await message.reply(
        f"🤝 <b>{sender.full_name}</b> предлагает обмен картами игроку <b>{receiver.full_name}</b>!\n"
        f"<i>Правило: меняться можно только картами равной редкости, и только повторными (единственную отдать нельзя)!</i>",
        reply_markup=kb,
        parse_mode="HTML",
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
    await callback.message.edit_text("🔄 <b>Окно обмена открыто!</b> Оба игрока должны выбрать карту дубликат одинаковой редкости.", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("tr_pick_"))
async def trade_pick(callback: CallbackQuery):
    _, _, trade_id, uid_str = callback.data.split("_")
    uid = int(uid_str)
    if callback.from_user.id != uid:
        return await callback.answer("Это кнопка для другого игрока!", show_alert=True)

    # Ищем карты, где count > 1 (только повторы!)
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

    # Проверяем одинаковость редкостей
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT rarity FROM cards WHERE id = ?", (t["p1_card"],)) as cur:
            r1 = (await cur.fetchone())[0]
        async with db.execute("SELECT rarity FROM cards WHERE id = ?", (t["p2_card"],)) as cur:
            r2 = (await cur.fetchone())[0]

        if r1 != r2:
            return await callback.message.reply(f"❌ Обмен невозможен! Редкости не совпадают ({r1} ≠ {r2}).")

        # Передаем карты: у P1 забираем P1_card, добавляем P2_card. У P2 наоборот.
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
    await callback.message.edit_text("🎉 <b>Обмен успешно завершён!</b> Игроки обменялись картами равной редкости.", parse_mode="HTML")

# 6. Дейлик (Ежедневная серия)
@dp.message(F.text.lower().in_(["дейлик", "/daily"]))
async def daily_command(message: Message):
    uid = message.from_user.id
    now = time.time()

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT daily_streak, last_daily_time FROM users WHERE user_id = ?", (uid,)) as cur:
            streak, last_time = await cur.fetchone()

        diff = now - last_time
        if diff < 86400:
            rem = 86400 - diff
            h, m = int(rem // 3600), int((rem % 3600) // 60)
            return await message.reply(f"⏳ Вы уже забрали дейлик! Приходите через {h}ч {m}м.")

        if diff <= 86400 * 2:
            streak += 1
        else:
            streak = 1  # Серия сгорела

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
        f"<i>Не пропускайте дни, чтобы увеличивать бонус!</i>",
        parse_mode="HTML"
    )

# 7. Топ 10 игроков и Топ 100
@dp.message(F.text.lower().in_(["/top", "топ"]))
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
        text += f"{i}. <b>{uname or 'Аноним'}</b> — {total} карт\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Показать ТОП 100 в ЛС", callback_data="top_100_dm")]
    ])
    await message.reply(text, reply_markup=kb, parse_mode="HTML")

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
        text += f"{i}. {uname or 'Аноним'} — {total} шт.\n"

    try:
        await bot.send_message(callback.from_user.id, text, parse_mode="HTML")
        await callback.answer("Топ 100 отправлен вам в ЛС!", show_alert=True)
    except Exception:
        bot_user = await bot.get_me()
        await callback.answer("Сначала откройте диалог с ботом!", show_alert=True)

# 8. /shop и триггер "магазин" (Только в ЛС)
@dp.message(F.text.lower().in_(["/shop", "магазин"]))
async def shop_command(message: Message):
    bot_info = await bot.get_me()
    if message.chat.type != "private":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏪 Перейти в магазин", url=f"https://t.me/{bot_info.username}?start=shop")]
        ])
        return await message.reply("🏪 Магазин доступен только в личных сообщениях с ботом!", reply_markup=kb)

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT femcoins FROM users WHERE user_id = ?", (message.from_user.id,)) as cur:
            coins = (await cur.fetchone())[0]

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
        f"• <b>Сброс КД:</b> сбрасывает 3-часовой кулдаун на вызов фото (сгорает через 3ч если не использовать).\n"
        f"• <b>Эликсир удачи:</b> повышает шансы на редкие ранги на 3 часа.\n"
        f"• <b>Новая карта:</b> гарантированно выдает карту, которой у вас еще нет!\n"
        f"• <b>Звезды Telegram:</b> пополнение запаса фемкоинов.",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.message(CommandStart())
async def start_cmd(message: Message):
    if len(message.text.split()) > 1 and message.text.split()[1] == "shop":
        return await shop_command(message)
    await message.reply("👋 Привет! Я игровой бот по сбору коллекционных карт! Напиши <b>фем</b> или /foto, чтобы начать.", parse_mode="HTML")

# Покупки за фемкоины
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
    await callback.answer("Сброс КД куплен! Он будет автоматически потрачен при следующем вызове 'фем'.", show_alert=True)

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

    await callback.message.answer_photo(photo=f_id, caption=f"🎁 <b>Куплена новая карта #{c_id}!</b>\nРедкость: {rarity}\n{desc}", parse_mode="HTML")
    await callback.answer()

# Telegram Stars инвойсы
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
        provider_token="",  # Для Telegram Stars оставляется пустым
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
        await message.reply(f"⭐ Оплата прошла успешно! Вам начислено <b>+{coins_to_add} 🪙 фемкоинов</b>.", parse_mode="HTML")

# 9. /promo (Активация промокода)
@dp.message(Command("promo"))
async def promo_command(message: Message, state: FSMContext):
    await message.reply("🔑 Пожалуйста, отправьте промокод следующим сообщением:")
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

        # Активация
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
    await message.reply(f"🎉 Промокод успешно активирован! Ваша награда: <b>{reward_msg}</b>", parse_mode="HTML")

# 10. Казино /kaz и "казино"
@dp.message(F.text.lower().in_(["/kaz", "казино"]))
async def casino_menu(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Бросить кости (Ставка 20 🪙)", callback_data="kaz_dice_20")],
        [InlineKeyboardButton(text="🔴 Красное (x2, 50 🪙)", callback_data="kaz_roul_red"), InlineKeyboardButton(text="⚫ Черное (x2, 50 🪙)", callback_data="kaz_roul_black")],
        [InlineKeyboardButton(text="🟢 Зеро (x14, 50 🪙)", callback_data="kaz_roul_zero")],
        [InlineKeyboardButton(text="🎰 Слоты 777 (Ставка 30 🪙)", callback_data="kaz_slots_30")]
    ])
    await message.reply("🎰 <b>Добро пожаловать в Фем-Казино!</b>\nИспытайте свою удачу в играх ниже:", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("kaz_"))
async def casino_play(callback: CallbackQuery):
    uid = callback.from_user.id
    act = callback.data

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT femcoins FROM users WHERE user_id = ?", (uid,)) as cur:
            coins = (await cur.fetchone())[0]

        if act == "kaz_dice_20":
            if coins < 20:
                return await callback.answer("Не хватает фемкоинов!", show_alert=True)
            user_roll = random.randint(1, 6)
            bot_roll = random.randint(1, 6)
            if user_roll > bot_roll:
                win = 40
                await db.execute("UPDATE users SET femcoins = femcoins + 20 WHERE user_id = ?", (uid,))
                res = f"Вы выиграли! ({user_roll} против {bot_roll} у бота). Награда: +20 🪙"
            elif user_roll < bot_roll:
                await db.execute("UPDATE users SET femcoins = femcoins - 20 WHERE user_id = ?", (uid,))
                res = f"Вы проиграли! ({user_roll} против {bot_roll} у бота). -20 🪙"
            else:
                res = f"Ничья! ({user_roll} и {bot_roll}). Монеты сохранены."

        elif act.startswith("kaz_roul_"):
            choice = act.replace("kaz_roul_", "")
            if coins < 50:
                return await callback.answer("Нужно минимум 50 монет!", show_alert=True)
            
            # Рулетка 0-36 (0 - зеро, 1-18 red, 19-36 black)
            num = random.randint(0, 36)
            if num == 0:
                color = "zero"
            elif num % 2 == 1:
                color = "red"
            else:
                color = "black"

            if choice == color:
                mult = 14 if choice == "zero" else 2
                winnings = 50 * mult - 50
                await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (winnings, uid))
                res = f"🎉 Выпало {num} ({color})! Вы выиграли +{winnings} 🪙!"
            else:
                await db.execute("UPDATE users SET femcoins = femcoins - 50 WHERE user_id = ?", (uid,))
                res = f"💀 Выпало {num} ({color})! Вы потеряли 50 🪙."

        elif act == "kaz_slots_30":
            if coins < 30:
                return await callback.answer("Нужно 30 монет!", show_alert=True)
            emojis = ["🍒", "🍋", "🍇", "💎", "7️⃣"]
            s1, s2, s3 = random.choices(emojis, k=3)
            if s1 == s2 == s3 == "7️⃣":
                win = 500
                await db.execute("UPDATE users SET femcoins = femcoins + 470 WHERE user_id = ?", (uid,))
                res = f"[{s1}|{s2}|{s3}] ДЖЕКПОТ 777! +470 🪙!"
            elif s1 == s2 == s3:
                win = 150
                await db.execute("UPDATE users SET femcoins = femcoins + 120 WHERE user_id = ?", (uid,))
                res = f"[{s1}|{s2}|{s3}] ТРИ В РЯД! +120 🪙!"
            else:
                await db.execute("UPDATE users SET femcoins = femcoins - 30 WHERE user_id = ?", (uid,))
                res = f"[{s1}|{s2}|{s3}] Не повезло! -30 🪙."

        await db.commit()

    await callback.message.reply(res)
    await callback.answer()

# ----------------- БЛОК АДМИНИСТРАЦИИ -----------------

# 1. /plus_foto
@dp.message(Command("plus_foto"))
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

    await add_log(message.from_user.id, f"Добавлена карта #{card_id} [{rarity}]")
    await state.clear()
    await message.reply(f"✅ Карта #{card_id} успешно создана и добавлена в базу!")

# 2. all card и 100 card (Отправка альбомами)
@dp.message(F.text.lower().in_(["all card", "100 card"]))
async def view_cards_album(message: Message):
    if not await is_admin(message.from_user.id):
        return

    limit = 100 if message.text.lower() == "100 card" else 500
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, file_id, rarity FROM cards ORDER BY id DESC LIMIT ?", (limit,)) as cur:
            cards = await cur.fetchall()

    if not cards:
        return await message.reply("В базе нет карт.")

    # Альбомы в TG отправляются порциями по 2-10 штук
    for i in range(0, len(cards), 10):
        chunk = cards[i:i+10]
        media_group = [
            InputMediaPhoto(media=c[1], caption=f"ID: {c[0]} | {c[2]}")
            for c in chunk
        ]
        await message.answer_media_group(media=media_group)
        await asyncio.sleep(0.5)

# 4. /card <id>
@dp.message(F.text.regexp(r"^/card\s+(\d+)$"))
async def card_manage(message: Message):
    if not await is_admin(message.from_user.id):
        return
    c_id = int(message.text.split()[1])

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT file_id, rarity, description FROM cards WHERE id = ?", (c_id,)) as cur:
            card = await cur.fetchone()

        if not card:
            return await message.reply(f"Карта #{c_id} не найдена.")

        # Статистика
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
    await message.answer_photo(photo=f_id, caption=caption, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data.startswith("editrar_"))
async def edit_card_rarity(callback: CallbackQuery):
    c_id = callback.data.split("_")[1]
    btns = [[InlineKeyboardButton(text=r, callback_data=f"saverar_{c_id}_{r}")] for r in RARITIES]
    await callback.message.reply("Выберите новую редкость:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await callback.answer()

@dp.callback_query(F.data.startswith("saverar_"))
async def save_card_rarity(callback: CallbackQuery):
    _, c_id, r = callback.data.split("_")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE cards SET rarity = ? WHERE id = ?", (r, int(c_id)))
        await db.commit()
    await add_log(callback.from_user.id, f"Изменена редкость карты #{c_id} на {r}")
    await callback.message.edit_text(f"Редкость карты #{c_id} изменена на {r}!")

@dp.callback_query(F.data.startswith("delcard_"))
async def delete_card(callback: CallbackQuery):
    c_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM cards WHERE id = ?", (c_id,))
        await db.execute("DELETE FROM inventory WHERE card_id = ?", (c_id,))
        # Сдвиг ID
        await db.execute("UPDATE cards SET id = id - 1 WHERE id > ?", (c_id,))
        await db.execute("UPDATE inventory SET card_id = card_id - 1 WHERE card_id > ?", (c_id,))
        await db.commit()
    await add_log(callback.from_user.id, f"Удалена карта #{c_id} со сдвигом ID")
    await callback.message.edit_text(f"🗑 Карта #{c_id} удалена, номера последующих карт сдвинуты на -1.")

# 5. Принудительный профиль
@dp.message(F.text.lower().startswith("твой профиль"))
async def forced_profile(message: Message):
    if not await is_admin(message.from_user.id):
        return

    target_user = None
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
    else:
        parts = message.text.split()
        if len(parts) > 2:
            uname = parts[2].replace("@", "")
            async with aiosqlite.connect(DB_NAME) as db:
                async with db.execute("SELECT user_id FROM users WHERE username = ?", (uname,)) as cur:
                    row = await cur.fetchone()
                    if row:
                        target_user = types.User(id=row[0], is_bot=False, first_name=uname)

    if not target_user:
        return await message.reply("Укажите пользователя ответом или @username!")

    await show_user_profile(message.chat.id, target_user)

# ----------------- СУПЕР АДМИНИСТРАТОР -----------------

# 1. /admins
@dp.message(Command("admins"))
async def list_admins(message: Message):
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM admins") as cur:
            rows = await cur.fetchall()

    if not rows:
        return await message.reply("Дополнительных админов нет.")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Снять админа {uid}", callback_data=f"removeadm_{uid}")]
        for (uid,) in rows
    ])
    await message.reply("Список действующих администраторов:", reply_markup=kb)

@dp.callback_query(F.data.startswith("removeadm_"))
async def remove_adm_cb(callback: CallbackQuery):
    if callback.from_user.id != SUPER_ADMIN_ID:
        return
    uid = int(callback.data.replace("removeadm_", ""))
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (uid,))
        await db.commit()
    await callback.message.edit_text(f"Администратор {uid} успешно снят.")

# 2. Рассылка /add ... /save
broadcast_buffer: Dict[int, List[Message]] = {}

@dp.message(Command("add"))
async def start_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != SUPER_ADMIN_ID:
        return
    broadcast_buffer[message.from_user.id] = []
    await state.set_state(AdminStates.broadcast_collecting)
    await message.reply("Отправляйте сообщения для рассылки. Когда закончите, напишите /save.")

@dp.message(AdminStates.broadcast_collecting)
async def collect_broadcast(message: Message, state: FSMContext):
    if message.text == "/save":
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

# 3. Инвайт код для админа
@dp.message(Command("now_admin_cod"))
async def gen_admin_code(message: Message):
    if not await is_admin(message.from_user.id):
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

    await message.reply(f"🔑 Одноразовый код администратора: <code>{code}</code>\nДействует 3 часа.", parse_mode="HTML")

@dp.message(Command("I_am_admin"))
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
    await add_log(message.from_user.id, "Стал администратором через код")
    await message.reply("🎉 Поздравляем! Вы получили права администратора бота.")

# 4. Создание промокодов /create_p
@dp.message(F.text.startswith("/create_p"))
async def create_promo_cmd(message: Message, state: FSMContext):
    if message.from_user.id != SUPER_ADMIN_ID:
        return
    parts = message.text.split()
    # /create_p L/M f coins hours ИЛИ /create_p L/M k hours
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
    await callback.message.edit_text(f"✅ Промокод успешно создан: <code>{code}</code>", parse_mode="HTML")

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
    await message.reply(f"✅ Промокод сохранен: <code>{code}</code>", parse_mode="HTML")

# 5. /log
@dp.message(Command("log"))
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
        text += f"[{dt}] <b>Admin {a_id}:</b> {act}\n"

    await message.reply(text, parse_mode="HTML")

# Хелпер для извлечения целевого пользователя в командах /бот ...
async def resolve_target(message: Message, arg_index: int = -1) -> Optional[int]:
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    parts = message.text.split()
    if len(parts) > arg_index and parts[arg_index].startswith("@"):
        uname = parts[arg_index].replace("@", "")
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT user_id FROM users WHERE username = ?", (uname,)) as cur:
                row = await cur.fetchone()
                return row[0] if row else None
    return None

# 6. /бот +коины и -коины
@dp.message(F.text.startswith("/бот +коины") | F.text.startswith("/бот -коины"))
async def mod_coins(message: Message):
    if message.from_user.id != SUPER_ADMIN_ID:
        return
    parts = message.text.split()
    amount = int(parts[2])
    target_id = await resolve_target(message, 3)
    if not target_id:
        return await message.reply("Укажите пользователя тегом или реплаем!")

    is_plus = "+коины" in parts[1]
    async with aiosqlite.connect(DB_NAME) as db:
        if is_plus:
            await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (amount, target_id))
        else:
            await db.execute("UPDATE users SET femcoins = MAX(0, femcoins - ?) WHERE user_id = ?", (amount, target_id))
        await db.commit()

    await add_log(message.from_user.id, f"{'+' if is_plus else '-'}{amount} коинов юзеру {target_id}")
    await message.reply(f"Баланс игрока {target_id} изменен на {'+' if is_plus else '-'}{amount} 🪙.")

# 7. /бот +карта и -карта
@dp.message(F.text.startswith("/бот +карта") | F.text.startswith("/бот -карта"))
async def mod_cards(message: Message):
    if message.from_user.id != SUPER_ADMIN_ID:
        return
    parts = message.text.split()
    card_id = int(parts[2])
    target_id = await resolve_target(message, 3)
    if not target_id:
        return await message.reply("Укажите пользователя тегом или реплаем!")

    is_plus = "+карта" in parts[1]
    async with aiosqlite.connect(DB_NAME) as db:
        if is_plus:
            async with db.execute("SELECT id FROM inventory WHERE user_id = ? AND card_id = ?", (target_id, card_id)) as cur:
                row = await cur.fetchone()
                if row:
                    await db.execute("UPDATE inventory SET count = count + 1 WHERE id = ?", (row[0],))
                else:
                    await db.execute("INSERT INTO inventory (user_id, card_id, count) VALUES (?, ?, 1)", (target_id, card_id))
            action_desc = f"Выдана карта #{card_id} юзеру {target_id}"
        else:
            await db.execute("DELETE FROM inventory WHERE user_id = ? AND card_id = ?", (target_id, card_id))
            action_desc = f"Изъята карта #{card_id} со всеми дубликатами у {target_id}"
        await db.commit()

    await add_log(message.from_user.id, action_desc)
    await message.reply(f"Готово: {action_desc}.")

# 8. /бот кд <ч> <срок>
@dp.message(F.text.startswith("/бот кд"))
async def mod_cd(message: Message):
    if message.from_user.id != SUPER_ADMIN_ID:
        return
    parts = message.text.split()
    new_cd = float(parts[2])
    duration_hours = float(parts[3])
    target_id = await resolve_target(message, 4)

    if not target_id:
        return await message.reply("Укажите пользователя тегом или ответом!")

    exp = -1 if duration_hours == -1 else time.time() + duration_hours * 3600
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE users 
            SET custom_cd_hours = ?, custom_cd_expires = ?
            WHERE user_id = ?
        """, (new_cd, exp, target_id))
        await db.commit()

    await add_log(message.from_user.id, f"Установлен КД {new_cd}ч игроку {target_id} на {duration_hours}ч")
    await message.reply(f"Игроку {target_id} установлен КД {new_cd} ч на {duration_hours} ч.")

# 9. /set_adm_text
@dp.message(Command("set_adm_text"))
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
    await add_log(message.from_user.id, "Обновлен текст 'кто админ'")
    await message.reply("✅ Текст администратора успешно сохранен!")

# ----------------- ЗАПУСК -----------------
async def main():
    await init_db()
    logger.info("База данных инициализирована. Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
