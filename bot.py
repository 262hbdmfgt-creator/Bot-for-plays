# =====================================================================
# FEMBOY BOT — ПОЛНАЯ СБОРКА С ИСПРАВЛЕНИЯМИ
# =====================================================================

import asyncio
import html
import json
import logging
import os
import random
import re
import secrets
import string
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Optional

import aiosqlite
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)


# =====================================================================
# CONFIG & LOGGING
# =====================================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPER_ADMIN_ID_RAW = os.getenv("SUPER_ADMIN_ID", "").strip()
SUPER_ADMIN_ID = int(SUPER_ADMIN_ID_RAW or "0")

CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@FM_femboy").strip()
CHANNEL_URL = os.getenv(
    "CHANNEL_URL",
    "https://t.me/FM_femboy",
).strip()

DB_NAME = "fem_bot.db"

if not BOT_TOKEN:
    raise RuntimeError("В .env не указан BOT_TOKEN")

if not SUPER_ADMIN_ID:
    raise RuntimeError("В .env не указан SUPER_ADMIN_ID")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("fembot")


# =====================================================================
# BOT & DISPATCHER
# =====================================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
    ),
)

dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


# =====================================================================
# CONSTANTS & MEMORY CACHES
# =====================================================================

RARITIES = [
    "Редкое",
    "Ультра редкое",
    "Мифическое",
    "Легендарное",
    "Фембойский артефакт",
    "Редчайший фем",
]

RARITY_ORDER = {
    rarity: index
    for index, rarity in enumerate(RARITIES)
}

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

VIP_WEIGHTS = {
    "Редкое": 52.0,
    "Ультра редкое": 31.0,
    "Мифическое": 13.0,
    "Легендарное": 0.9,
    "Фембойский артефакт": 0.3,
    "Редчайший фем": 0.1,
}

DUPLICATE_COMPENSATION = {
    "Редкое": 5,
    "Ультра редкое": 10,
    "Мифическое": 15,
    "Легендарное": 20,
    "Фембойский артефакт": 25,
    "Редчайший фем": 30,
}

DAILY_REWARDS = {
    "normal_hours": 24,
    "vip_hours": 22,
    "maximum_streak": 30,
}

CHEST_REWARDS = {
    "normal": (5, 49),
    "rare": (50, 99),
    "legendary": (100, 199),
}

CASINO_BETS = {10, 20, 50, 100}

user_casino_bets: dict[int, int] = {}
active_tasks: set[asyncio.Task] = set()
chat_activity_memory: dict[int, float] = {}
trade_locks: dict[str, asyncio.Lock] = {}
callback_locks: dict[str, asyncio.Lock] = {}
user_fem_locks: dict[int, asyncio.Lock] = {}


# =====================================================================
# FSM
# =====================================================================

class AdminStates(StatesGroup):
    waiting_photo = State()
    waiting_card_description = State()
    waiting_admin_code = State()
    waiting_broadcast = State()
    waiting_promo_code = State()
    waiting_admin_text = State()


class UserStates(StatesGroup):
    waiting_promo_code = State()


# =====================================================================
# BASIC HELPERS
# =====================================================================

def now_ts() -> float:
    return time.time()


def esc(value: Any) -> str:
    """Безопасное HTML-экранирование пользовательского текста."""
    return html.escape(str(value or ""), quote=False)


def short_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}ч {minutes}м"
    if minutes:
        return f"{minutes}м {secs}с"
    return f"{secs}с"


def format_date(timestamp: float) -> str:
    if not timestamp:
        return ""
    return datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y %H:%M")


def normalize_command(text: Optional[str]) -> str:
    if not text:
        return ""

    first = text.strip().split(maxsplit=1)[0].lower()

    if "@" in first:
        first = first.split("@", 1)[0]

    return first


def command_is(message: Message, *aliases: str) -> bool:
    command = normalize_command(message.text)
    normalized = {
        alias.lower() if alias.startswith("/") else f"/{alias.lower()}"
        for alias in aliases
    }
    return command in normalized


def parse_callback(callback_data: str, prefix: str) -> Optional[str]:
    if not callback_data.startswith(prefix):
        return None
    return callback_data[len(prefix):]


def display_name(user: types.User) -> str:
    if user.username:
        return f"@{esc(user.username)}"
    return esc(user.full_name or str(user.id))


async def safe_callback_answer(
    callback: CallbackQuery,
    text: Optional[str] = None,
    *,
    show_alert: bool = False,
) -> None:
    try:
        await callback.answer(text or "", show_alert=show_alert)
    except Exception:
        logger.exception("Ошибка callback.answer")


async def safe_edit_text(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            logger.warning("Не удалось изменить сообщение: %s", exc)
    except Exception:
        logger.exception("Ошибка редактирования сообщения")


async def log_action(
    admin_id: int,
    action: str,
) -> None:
    try:
        async with db_connection() as db:
            await db.execute(
                """
                INSERT INTO logs(admin_id, action, timestamp)
                VALUES (?, ?, ?)
                """,
                (admin_id, action[:2000], now_ts()),
            )
            await db.commit()
    except Exception:
        logger.exception("Ошибка записи лога")


# =====================================================================
# DATABASE CONNECTION & SCHEMA
# =====================================================================

@asynccontextmanager
async def db_connection():
    db = await aiosqlite.connect(DB_NAME)
    db.row_factory = aiosqlite.Row

    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute("PRAGMA busy_timeout = 10000")

    try:
        yield db
    finally:
        await db.close()


async def table_columns(
    db: aiosqlite.Connection,
    table_name: str,
) -> set[str]:
    result: set[str] = set()
    async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
        async for row in cursor:
            result.add(row["name"])
    return result


async def ensure_columns(
    db: aiosqlite.Connection,
    table_name: str,
    columns: dict[str, str],
) -> None:
    existing = await table_columns(db, table_name)

    for column, definition in columns.items():
        if column in existing:
            continue

        try:
            await db.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column} {definition}"
            )
            logger.info("Добавлена колонка %s.%s", table_name, column)
        except Exception:
            logger.exception("Ошибка миграции %s.%s", table_name, column)


async def init_database() -> None:
    async with db_connection() as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                femcoins INTEGER NOT NULL DEFAULT 100,
                last_fem_time REAL NOT NULL DEFAULT 0,
                custom_cd_hours REAL NOT NULL DEFAULT 3,
                custom_cd_expires REAL NOT NULL DEFAULT 0,
                daily_streak INTEGER NOT NULL DEFAULT 0,
                last_daily_time REAL NOT NULL DEFAULT 0,
                referred_by INTEGER NOT NULL DEFAULT 0,
                referral_reward_given INTEGER NOT NULL DEFAULT 0,
                vip_until REAL NOT NULL DEFAULT 0,
                luck_expires REAL NOT NULL DEFAULT 0,
                cd_resets_count INTEGER NOT NULL DEFAULT 0,
                cd_reset_expires REAL NOT NULL DEFAULT 0,
                channel_bonus_claimed REAL NOT NULL DEFAULT 0,
                last_weekly_bonus REAL NOT NULL DEFAULT 0,
                attack_block_until REAL NOT NULL DEFAULT 0,
                blade_hidden INTEGER NOT NULL DEFAULT 0,
                armor_hidden INTEGER NOT NULL DEFAULT 0,
                first_seen REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT NOT NULL,
                rarity TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                card_id INTEGER NOT NULL,
                count INTEGER NOT NULL DEFAULT 1,
                UNIQUE(user_id, card_id)
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                artifact_type TEXT NOT NULL,
                subtype TEXT NOT NULL DEFAULT '',
                amount INTEGER NOT NULL DEFAULT 1,
                hidden_amount INTEGER NOT NULL DEFAULT 0,
                blocked_until REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL DEFAULT 0,
                UNIQUE(user_id, artifact_type, subtype)
            );

            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER NOT NULL DEFAULT 0,
                added_at REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                chat_type TEXT NOT NULL DEFAULT '',
                inactivity_interval REAL NOT NULL DEFAULT 3600,
                last_activity REAL NOT NULL DEFAULT 0,
                last_chest_at REAL NOT NULL DEFAULT 0,
                has_admin_rights INTEGER NOT NULL DEFAULT 0,
                admin_warned INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS group_rewards (
                chat_id INTEGER PRIMARY KEY,
                owner_id INTEGER NOT NULL DEFAULT 0,
                reward_claimed INTEGER NOT NULL DEFAULT 0,
                member_count INTEGER NOT NULL DEFAULT 0,
                rewarded_at REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS owner_group_counts (
                owner_id INTEGER PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                p_type TEXT NOT NULL,
                reward_type TEXT NOT NULL,
                reward_val INTEGER NOT NULL DEFAULT 0,
                expires_at REAL NOT NULL,
                is_used INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS promo_claims (
                user_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                claimed_at REAL NOT NULL,
                PRIMARY KEY(user_id, code)
            );

            CREATE TABLE IF NOT EXISTS admin_codes (
                code TEXT PRIMARY KEY,
                created_by INTEGER NOT NULL,
                expires_at REAL NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL DEFAULT 0,
                action TEXT NOT NULL,
                timestamp REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                p1 INTEGER NOT NULL,
                p2 INTEGER NOT NULL,
                p1_card INTEGER,
                p2_card INTEGER,
                p1_confirmed INTEGER NOT NULL DEFAULT 0,
                p2_confirmed INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chests (
                chest_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                message_id INTEGER,
                chest_type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '',
                claimed_by INTEGER,
                claimed_at REAL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
            """
        )

        await ensure_columns(
            db,
            "users",
            {
                "attack_block_until": "REAL NOT NULL DEFAULT 0",
                "blade_hidden": "INTEGER NOT NULL DEFAULT 0",
                "armor_hidden": "INTEGER NOT NULL DEFAULT 0",
                "first_seen": "REAL NOT NULL DEFAULT 0",
                "referral_reward_given": "INTEGER NOT NULL DEFAULT 0",
                "vip_until": "REAL NOT NULL DEFAULT 0",
                "luck_expires": "REAL NOT NULL DEFAULT 0",
                "cd_resets_count": "INTEGER NOT NULL DEFAULT 0",
                "cd_reset_expires": "REAL NOT NULL DEFAULT 0",
            },
        )

        await ensure_columns(
            db,
            "chats",
            {
                "last_activity": "REAL NOT NULL DEFAULT 0",
                "last_chest_at": "REAL NOT NULL DEFAULT 0",
                "has_admin_rights": "INTEGER NOT NULL DEFAULT 0",
                "admin_warned": "INTEGER NOT NULL DEFAULT 0",
            },
        )

        await ensure_columns(
            db,
            "group_rewards",
            {
                "member_count": "INTEGER NOT NULL DEFAULT 0",
                "rewarded_at": "REAL NOT NULL DEFAULT 0",
            },
        )

        await db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_user_card
            ON inventory(user_id, card_id)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_inventory_user
            ON inventory(user_id)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_artifacts_user
            ON artifacts(user_id)
            """
        )

        await db.execute(
            "UPDATE users SET first_seen = ? WHERE first_seen = 0",
            (now_ts(),),
        )

        await db.commit()

    logger.info("База данных инициализирована")


@asynccontextmanager
async def transaction():
    async with db_connection() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            yield db
        except Exception:
            await db.rollback()
            raise
        else:
            await db.commit()


async def ensure_user(
    user: types.User,
    referred_by: int = 0,
) -> bool:
    user_id = user.id
    username = user.username or user.full_name or str(user_id)
    valid_referrer = (
        referred_by
        if referred_by > 0 and referred_by != user_id
        else 0
    )

    async with transaction() as db:
        async with db.execute(
            "SELECT user_id FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            existing = await cursor.fetchone()

        if existing:
            await db.execute(
                "UPDATE users SET username = ? WHERE user_id = ?",
                (username, user_id),
            )
            return False

        await db.execute(
            """
            INSERT INTO users(user_id, username, referred_by, first_seen)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, username, valid_referrer, now_ts()),
        )
        return True


async def user_exists(user_id: int) -> bool:
    async with db_connection() as db:
        async with db.execute(
            "SELECT 1 FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            return await cursor.fetchone() is not None


async def is_vip(user_id: int) -> bool:
    async with db_connection() as db:
        async with db.execute(
            "SELECT vip_until FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
    return bool(row and float(row["vip_until"] or 0) > now_ts())


async def get_username_from_db(user_id: int) -> str:
    async with db_connection() as db:
        async with db.execute(
            "SELECT username FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

    if not row or not row["username"]:
        return f"ID:{user_id}"
    return esc(str(row["username"]).lstrip("@"))


# =====================================================================
# GROUP ADMIN RIGHTS & CHAT HELPERS
# =====================================================================

async def get_chat_owner(chat_id: int) -> Optional[types.User]:
    """Получает владельца (создателя) группы."""
    try:
        admins = await bot.get_chat_administrators(chat_id)
        for admin in admins:
            if admin.status == "creator":
                return admin.user
    except Exception as exc:
        logger.warning("Не удалось получить администраторов группы %s: %s", chat_id, exc)
    return None


async def check_bot_is_admin(chat_id: int) -> bool:
    """Проверяет, имеет ли бот права администратора в чате."""
    try:
        me = await bot.get_chat_member(chat_id=chat_id, user_id=bot.id)
        return me.status in ("administrator", "creator")
    except Exception:
        return False


def admin_check_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Проверить права",
                    callback_data=f"check_admin_rights:{chat_id}",
                )
            ]
        ]
    )


async def send_admin_rights_request(chat_id: int) -> None:
    """Отправляет однократное сообщение о необходимости прав админа с тегом владельца."""
    owner = await get_chat_owner(chat_id)

    if owner and owner.username:
        owner_mention = f"@{owner.username}"
    elif owner:
        owner_mention = f'<a href="tg://user?id={owner.id}">{esc(owner.full_name)}</a>'
    else:
        owner_mention = "Владелец группы"

    text = (
        f"⚠️ {owner_mention}, для корректной работы бота (подсчёт участников, "
        "выдача наград и появление сундуков) мне необходимы <b>права администратора</b>!\n\n"
        "Пожалуйста, выдайте боту права администратора и нажмите кнопку ниже:"
    )

    try:
        await bot.send_message(
            chat_id,
            text,
            reply_markup=admin_check_keyboard(chat_id),
        )
    except Exception as exc:
        logger.warning("Не удалось отправить запрос прав в чат %s: %s", chat_id, exc)


async def register_chat(message: Message) -> None:
    if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return

    timestamp = now_ts()
    chat_id = message.chat.id

    async with transaction() as db:
        async with db.execute(
            "SELECT has_admin_rights, admin_warned FROM chats WHERE chat_id = ?",
            (chat_id,),
        ) as cursor:
            chat_row = await cursor.fetchone()

        if not chat_row:
            await db.execute(
                """
                INSERT INTO chats(
                    chat_id, chat_type, inactivity_interval,
                    last_activity, has_admin_rights, admin_warned
                )
                VALUES (?, ?, 3600, ?, 0, 0)
                """,
                (chat_id, message.chat.type, timestamp),
            )
            has_rights = 0
            admin_warned = 0
        else:
            await db.execute(
                """
                UPDATE chats
                SET chat_type = ?, last_activity = ?
                WHERE chat_id = ?
                """,
                (message.chat.type, timestamp, chat_id),
            )
            has_rights = int(chat_row["has_admin_rights"] or 0)
            admin_warned = int(chat_row["admin_warned"] or 0)

    chat_activity_memory[chat_id] = timestamp

    # Если бот ещё не подтверждён как админ и предупреждение ещё не отправлялось:
    if not has_rights and not admin_warned:
        is_adm = await check_bot_is_admin(chat_id)
        if is_adm:
            async with transaction() as db:
                await db.execute(
                    "UPDATE chats SET has_admin_rights = 1 WHERE chat_id = ?",
                    (chat_id,),
                )
        else:
            async with transaction() as db:
                await db.execute(
                    "UPDATE chats SET admin_warned = 1 WHERE chat_id = ?",
                    (chat_id,),
                )
            await send_admin_rights_request(chat_id)


# =====================================================================
# GLOBAL MIDDLEWARE & HANDLER
# =====================================================================

@router.message.outer_middleware()
async def message_registration_middleware(handler, event, data):
    if isinstance(event, Message):
        try:
            await register_chat(event)
        except Exception:
            logger.exception("Ошибка регистрации чата")

    return await handler(event, data)


@dp.error()
async def global_error_handler(event):
    logger.exception("Глобальная ошибка обработки события: %r", event)
    return True


# =====================================================================
# CARDS, PROFILE & REWARDS
# =====================================================================

def bonus_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎁 Забрать бонус",
                    url=f"https://t.me/{bot_username}?start=bonus",
                )
            ]
        ]
    )


async def post_card_keyboard() -> InlineKeyboardMarkup:
    bot_info = await bot.get_me()

    buttons = [
        [
            InlineKeyboardButton(
                text="🎁 Забрать бонус",
                url=f"https://t.me/{bot_info.username}?start=bonus",
            )
        ]
    ]

    async with db_connection() as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key = 'post_btn_url'"
        ) as cursor:
            row = await cursor.fetchone()

    if row and row["value"]:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="Присоединиться / Подробнее",
                    url=row["value"],
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def format_card_caption(
    card_id: int,
    rarity: str,
    description: str,
    duplicate: bool,
    compensation: int = 0,
    prefix: str = "✨ Выпала карта",
) -> str:
    result = (
        f"{prefix} <b>#{card_id}</b>\n\n"
        f"🏷 <b>Редкость:</b> {esc(rarity)}\n"
        f"📝 <b>Описание:</b> "
        f"{esc(description) if description else 'Без описания'}\n"
    )

    if duplicate:
        result += "\n🔁 <i>Это дубликат карты.</i>"
    else:
        result += "\n🆕 <i>Новая карта в коллекции!</i>"

    if compensation:
        result += f"\n💰 VIP-компенсация: <b>+{compensation} 🪙</b>"

    return result


async def choose_card(
    db: aiosqlite.Connection,
    weights: dict[str, float],
) -> Optional[aiosqlite.Row]:
    async with db.execute("SELECT DISTINCT rarity FROM cards") as cursor:
        rows = await cursor.fetchall()

    existing = {str(row["rarity"]) for row in rows}
    available_rarities = [r for r in weights if r in existing]

    if available_rarities:
        selected_rarity = random.choices(
            available_rarities,
            weights=[weights[item] for item in available_rarities],
            k=1,
        )[0]

        async with db.execute(
            """
            SELECT id, file_id, rarity, description
            FROM cards
            WHERE rarity = ?
            ORDER BY RANDOM()
            LIMIT 1
            """,
            (selected_rarity,),
        ) as cursor:
            card = await cursor.fetchone()

        if card:
            return card

    async with db.execute(
        """
        SELECT id, file_id, rarity, description
        FROM cards
        ORDER BY RANDOM()
        LIMIT 1
        """
    ) as cursor:
        return await cursor.fetchone()


async def add_card_to_inventory(
    db: aiosqlite.Connection,
    user_id: int,
    card_id: int,
) -> tuple[bool, int]:
    async with db.execute(
        """
        SELECT count
        FROM inventory
        WHERE user_id = ? AND card_id = ?
        """,
        (user_id, card_id),
    ) as cursor:
        row = await cursor.fetchone()

    if row:
        await db.execute(
            """
            UPDATE inventory
            SET count = count + 1
            WHERE user_id = ? AND card_id = ?
            """,
            (user_id, card_id),
        )
        return True, int(row["count"])

    await db.execute(
        """
        INSERT INTO inventory(user_id, card_id, count)
        VALUES (?, ?, 1)
        """,
        (user_id, card_id),
    )
    return False, 0


async def draw_card(
    user_id: int,
    *,
    lucky: bool,
    vip: bool,
    force_duplicate: bool = False,
) -> Optional[dict[str, Any]]:
    if vip and not force_duplicate:
        weights = VIP_WEIGHTS
    elif lucky:
        weights = LUCK_WEIGHTS
    else:
        weights = BASE_WEIGHTS

    async with transaction() as db:
        card = await choose_card(db, weights)

        if not card:
            return None

        if force_duplicate:
            async with db.execute(
                """
                SELECT c.id, c.file_id, c.rarity, c.description
                FROM inventory AS i
                JOIN cards AS c ON c.id = i.card_id
                WHERE i.user_id = ?
                ORDER BY RANDOM()
                LIMIT 1
                """,
                (user_id,),
            ) as cursor:
                duplicate_card = await cursor.fetchone()

            if duplicate_card:
                card = duplicate_card

        card_id = int(card["id"])
        rarity = str(card["rarity"])

        duplicate, old_count = await add_card_to_inventory(
            db,
            user_id,
            card_id,
        )

        compensation = 0
        if duplicate and vip:
            compensation = DUPLICATE_COMPENSATION.get(rarity, 5)
            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins + ?
                WHERE user_id = ?
                """,
                (compensation, user_id),
            )

        return {
            "id": card_id,
            "file_id": card["file_id"],
            "rarity": rarity,
            "description": card["description"] or "",
            "duplicate": duplicate,
            "compensation": compensation,
            "old_count": old_count,
        }


async def get_effective_fem_cooldown(
    db: aiosqlite.Connection,
    user_id: int,
    current_time: float,
) -> float:
    async with db.execute(
        """
        SELECT custom_cd_hours, custom_cd_expires, vip_until
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    ) as cursor:
        row = await cursor.fetchone()

    if not row:
        return 3 * 3600

    custom_hours = float(row["custom_cd_hours"] or 3)
    custom_expires = float(row["custom_cd_expires"] or 0)
    vip_until = float(row["vip_until"] or 0)

    if custom_expires == -1 or custom_expires > current_time:
        return max(0, custom_hours * 3600)

    if vip_until > current_time:
        return 2 * 3600

    return 3 * 3600


async def process_referral_reward(
    new_user_id: int,
) -> Optional[int]:
    async with transaction() as db:
        async with db.execute(
            """
            SELECT referred_by, referral_reward_given
            FROM users
            WHERE user_id = ?
            """,
            (new_user_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            return None

        referrer_id = int(row["referred_by"] or 0)
        reward_given = int(row["referral_reward_given"] or 0)

        if referrer_id <= 0 or referrer_id == new_user_id or reward_given != 0:
            return None

        async with db.execute(
            "SELECT 1 FROM users WHERE user_id = ?",
            (referrer_id,),
        ) as cursor:
            referrer_exists = await cursor.fetchone()

        if not referrer_exists:
            await db.execute(
                "UPDATE users SET referral_reward_given = 2 WHERE user_id = ?",
                (new_user_id,),
            )
            return None

        await db.execute(
            "UPDATE users SET femcoins = femcoins + 25 WHERE user_id = ?",
            (referrer_id,),
        )
        await db.execute(
            "UPDATE users SET referral_reward_given = 1 WHERE user_id = ?",
            (new_user_id,),
        )
        return referrer_id


# -------------------------
# PROFILE LOGIC (С ИСПРАВЛЕНИЕМ BINDINGS)
# -------------------------

async def get_profile_text(
    user_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    current_time = now_ts()

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT femcoins, vip_until,
                   cd_resets_count, cd_reset_expires,
                   attack_block_until
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        if not user:
            return (
                "Профиль ещё не создан.",
                InlineKeyboardMarkup(inline_keyboard=[]),
            )

        async with db.execute(
            """
            SELECT COUNT(DISTINCT card_id) AS unique_cards,
                   COALESCE(SUM(count), 0) AS total_cards
            FROM inventory
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            cards = await cursor.fetchone()

        async with db.execute("SELECT COUNT(*) AS total FROM cards") as cursor:
            card_total = await cursor.fetchone()

        # Здесь исправлена передача binding (user_id,)
        async with db.execute(
            """
            SELECT artifact_type, subtype,
                   SUM(amount) AS amount,
                   SUM(hidden_amount) AS hidden_amount
            FROM artifacts
            WHERE user_id = ?
            GROUP BY artifact_type, subtype
            """,
            (user_id,),
        ) as cursor:
            artifact_rows = await cursor.fetchall()

    lines = [
        "👤 <b>Профиль игрока</b>",
        "",
        f"💰 Баланс: <b>{int(user['femcoins'] or 0)} 🪙</b>",
    ]

    vip_until = float(user["vip_until"] or 0)
    if vip_until > current_time:
        lines.append(f"👑 VIP до: <b>{esc(format_date(vip_until))}</b>")

    unique_cards = int(cards["unique_cards"] or 0)
    total_cards = int(cards["total_cards"] or 0)
    available_cards = int(card_total["total"] or 0)

    if unique_cards:
        lines.append(
            f"🎴 Собранные карты: <b>{unique_cards}/{available_cards}</b> (копий: {total_cards})"
        )

    skip_count = int(user["cd_resets_count"] or 0)
    skip_expires = float(user["cd_reset_expires"] or 0)

    if skip_count > 0 and skip_expires > current_time:
        lines.append(
            f"⚡ Скипы: <b>{skip_count}</b> "
            f"(осталось {short_time(skip_expires - current_time)})"
        )

    blades = 0
    armor = 0
    shields: dict[str, int] = {}

    for row in artifact_rows:
        artifact_type = str(row["artifact_type"])
        subtype = str(row["subtype"] or "")
        amount = int(row["amount"] or 0)

        if amount <= 0:
            continue

        if artifact_type == "blade":
            blades += amount
        elif artifact_type == "armor":
            armor += amount
        elif artifact_type == "shield":
            shields[subtype] = shields.get(subtype, 0) + amount

    if blades:
        lines.append(f"🗡 Открытые клинки: <b>{blades}</b>")

    if armor:
        lines.append(f"🛡 Открытая броня: <b>{armor}</b>")

    shield_names = {
        "weak": "слабых",
        "reinforced": "усиленных",
        "femboy": "фембойских",
    }

    shield_lines = [
        f"{shield_names.get(subtype, subtype)}: {amount}"
        for subtype, amount in shields.items()
        if amount > 0
    ]

    if shield_lines:
        lines.append("🔰 Открытые щиты: <b>" + ", ".join(shield_lines) + "</b>")

    attack_block_until = float(user["attack_block_until"] or 0)
    if attack_block_until > current_time:
        lines.append(
            f"🚫 Запрет сбора карт: <b>{short_time(attack_block_until - current_time)}</b>"
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎴 Мои карты",
                    callback_data=f"profile_cards:{user_id}:0",
                )
            ]
        ]
    )

    return "\n".join(lines), keyboard


@router.message(
    lambda message: command_is(
        message,
        "профиль",
        "profile",
        "profil",
    )
)
async def profile_command(message: Message) -> None:
    await ensure_user(message.from_user)
    text, keyboard = await get_profile_text(message.from_user.id)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("profile_cards:"))
async def profile_cards_callback(
    callback: CallbackQuery,
) -> None:
    payload = parse_callback(callback.data or "", "profile_cards:")
    if not payload:
        await safe_callback_answer(callback, "Некорректная кнопка", show_alert=True)
        return

    try:
        owner_raw, index_raw = payload.rsplit(":", 1)
        owner_id = int(owner_raw)
        index = int(index_raw)
    except (ValueError, TypeError):
        await safe_callback_answer(callback, "Некорректные данные", show_alert=True)
        return

    if callback.from_user.id != owner_id:
        await safe_callback_answer(callback, "Это не ваш профиль", show_alert=True)
        return

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT c.id, c.file_id, c.rarity, c.description, i.count
            FROM inventory AS i
            JOIN cards AS c ON c.id = i.card_id
            WHERE i.user_id = ?
            """,
            (owner_id,),
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await safe_callback_answer(callback, "У вас пока нет карт", show_alert=True)
        return

    rows = sorted(
        rows,
        key=lambda row: (
            RARITY_ORDER.get(str(row["rarity"]), -1),
            int(row["id"]),
        ),
        reverse=True,
    )

    index = max(0, min(index, len(rows) - 1))
    card = rows[index]

    navigation: list[InlineKeyboardButton] = []
    if index > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"profile_cards:{owner_id}:{index - 1}",
            )
        )
    if index < len(rows) - 1:
        navigation.append(
            InlineKeyboardButton(
                text="Вперёд ➡️",
                callback_data=f"profile_cards:{owner_id}:{index + 1}",
            )
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[navigation] if navigation else []
    )

    caption = (
        f"🎴 <b>Карта #{int(card['id'])}</b>\n"
        f"📚 {index + 1}/{len(rows)}\n\n"
        f"🏷 <b>Редкость:</b> {esc(card['rarity'])}\n"
        f"🔢 Количество: <b>{int(card['count'])}</b>\n"
        f"📝 <b>Описание:</b> "
        f"{esc(card['description']) if card['description'] else 'Без описания'}"
    )

    try:
        if callback.message and callback.message.photo:
            await callback.message.edit_media(
                InputMediaPhoto(media=card["file_id"], caption=caption),
                reply_markup=keyboard,
            )
        elif callback.message:
            await callback.message.delete()
            await callback.message.answer_photo(
                card["file_id"],
                caption=caption,
                reply_markup=keyboard,
            )
    except Exception:
        logger.exception("Ошибка просмотра коллекции")

    await safe_callback_answer(callback)


@router.message(
    lambda message: command_is(
        message,
        "дейлик",
        "daily",
    )
)
async def daily_command(message: Message) -> None:
    await ensure_user(message.from_user)
    user_id = message.from_user.id
    current_time = now_ts()

    async with transaction() as db:
        async with db.execute(
            """
            SELECT daily_streak, last_daily_time, vip_until
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        vip = float(user["vip_until"] or 0) > current_time
        interval = (
            DAILY_REWARDS["vip_hours"] if vip else
            DAILY_REWARDS["normal_hours"]
        ) * 3600

        last_daily = float(user["last_daily_time"] or 0)
        elapsed = current_time - last_daily

        if last_daily and elapsed < interval:
            await message.answer(
                "⏳ Дейлик уже получен.\n"
                f"Следующая награда через <b>{short_time(interval - elapsed)}</b>."
            )
            return

        streak = int(user["daily_streak"] or 0)
        if last_daily and elapsed <= interval + 86400:
            streak += 1
        else:
            streak = 1

        streak = min(streak, DAILY_REWARDS["maximum_streak"])
        reward = 20 + 10 * streak
        if vip:
            reward += 5

        await db.execute(
            """
            UPDATE users
            SET daily_streak = ?,
                last_daily_time = ?,
                femcoins = femcoins + ?
            WHERE user_id = ?
            """,
            (streak, current_time, reward, user_id),
        )

    referrer_id = await process_referral_reward(user_id)
    if referrer_id:
        try:
            await bot.send_message(
                referrer_id,
                "👥 <b>Реферальный бонус!</b>\n"
                f"Игрок <b>{display_name(message.from_user)}</b> перешёл по вашей ссылке.\n"
                "Вам начислено <b>+25 🪙</b>.",
            )
        except Exception:
            logger.exception("Не удалось уведомить реферера")

    await message.answer(
        "📅 <b>Ежедневная награда получена!</b>\n\n"
        f"🔥 Серия: <b>{streak} дн.</b>\n"
        f"💰 Награда: <b>+{reward} 🪙</b>"
        + ("\n👑 VIP-бонус: +5 🪙" if vip else ""),
    )


@router.message(
    lambda message: command_is(
        message,
        "реф",
        "ref",
        "рефералы",
        "рефералка",
        "пригласить",
    )
)
async def referral_command(message: Message) -> None:
    await ensure_user(message.from_user)
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref{message.from_user.id}"

    async with db_connection() as db:
        async with db.execute(
            "SELECT COUNT(*) AS total FROM users WHERE referred_by = ?",
            (message.from_user.id,),
        ) as cursor:
            total = await cursor.fetchone()

        async with db.execute(
            """
            SELECT COUNT(*) AS completed
            FROM users
            WHERE referred_by = ? AND referral_reward_given = 1
            """,
            (message.from_user.id,),
        ) as cursor:
            completed = await cursor.fetchone()

    total_refs = int(total["total"] or 0)
    completed_refs = int(completed["completed"] or 0)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить бота в группу",
                    url=f"https://t.me/{bot_info.username}?startgroup=true",
                )
            ]
        ]
    )

    await message.answer(
        "👥 <b>Реферальная система</b>\n\n"
        "Приглашайте друзей по ссылке ниже.\n"
        "За каждого нового пользователя начисляется <b>+25 🪙</b> сразу после перехода.\n\n"
        f"🔗 <code>{esc(link)}</code>\n\n"
        f"📊 Перешло пользователей: <b>{total_refs}</b>\n"
        f"✅ Начислено бонусов: <b>{completed_refs}</b>\n"
        f"💰 Всего заработано: <b>{completed_refs * 25} 🪙</b>",
        reply_markup=keyboard,
    )


@router.message(CommandStart())
async def start_command(
    message: Message,
    command: CommandObject,
) -> None:
    argument = (command.args or "").strip()
    referrer_id = 0

    if argument.lower().startswith("ref"):
        try:
            referrer_id = int(argument[3:])
        except ValueError:
            referrer_id = 0

    is_new = await ensure_user(message.from_user, referrer_id)

    if argument.lower() == "bonus":
        if message.chat.type != ChatType.PRIVATE:
            bot_info = await bot.get_me()
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🎁 Получить бонус в ЛС",
                            url=f"https://t.me/{bot_info.username}?start=bonus",
                        )
                    ]
                ]
            )
            await message.answer(
                "🎁 Бонус можно получить только в личных сообщениях.",
                reply_markup=keyboard,
            )
            return

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🩷 Канал",
                        url=CHANNEL_URL,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="✅ Проверить подписку",
                        callback_data="bonus_check",
                    )
                ],
            ]
        )
        await message.answer(
            f"🎁 <b>Бонус за подписку</b>\n\n"
            f"Подпишитесь на канал {esc(CHANNEL_USERNAME)}, затем нажмите кнопку проверки.",
            reply_markup=keyboard,
        )
        return

    if is_new and referrer_id:
        ref_owner = await process_referral_reward(message.from_user.id)
        if ref_owner:
            try:
                await bot.send_message(
                    ref_owner,
                    "👥 <b>Новый реферал!</b>\n"
                    f"Игрок <b>{display_name(message.from_user)}</b> перешёл по вашей ссылке.\n"
                    "Вам начислено <b>+25 🪙</b>.",
                )
            except Exception:
                logger.exception("Ошибка уведомления о реферале")

        await message.answer(
            "🎉 Вы зарегистрированы по реферальной ссылке!\n"
            "Пригласившему начислено <b>+25 🪙</b>."
        )
        return

    await message.answer(
        "👋 <b>Добро пожаловать!</b>\n\n"
        "🎴 Напишите <b>фем</b>, чтобы получить карту.\n"
        "📅 Напишите <b>дейлик</b>, чтобы забрать награду.\n"
        "👤 Команда <b>профиль</b> покажет вашу коллекцию."
    )


@router.callback_query(F.data == "bonus_check")
async def bonus_check_callback(
    callback: CallbackQuery,
) -> None:
    user_id = callback.from_user.id
    current_time = now_ts()

    try:
        member = await bot.get_chat_member(
            chat_id=CHANNEL_USERNAME,
            user_id=user_id,
        )
    except Exception:
        logger.exception("Ошибка проверки подписки")
        await safe_callback_answer(callback, "Не удалось проверить подписку", show_alert=True)
        return

    if str(member.status) not in {"creator", "administrator", "member", "restricted"}:
        await safe_callback_answer(callback, "Сначала подпишитесь на канал", show_alert=True)
        return

    async with transaction() as db:
        async with db.execute(
            """
            SELECT last_weekly_bonus
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            await safe_callback_answer(callback, "Профиль не найден", show_alert=True)
            return

        last_bonus = float(row["last_weekly_bonus"] or 0)
        if last_bonus and current_time - last_bonus < 7 * 86400:
            await safe_callback_answer(callback, "Еженедельный бонус ещё недоступен", show_alert=True)
            return

        await db.execute(
            """
            UPDATE users
            SET channel_bonus_claimed = 1,
                last_weekly_bonus = ?,
                cd_resets_count = cd_resets_count + 1,
                cd_reset_expires = ?
            WHERE user_id = ?
            """,
            (current_time, current_time + 3 * 3600, user_id),
        )

        await db.execute(
            "UPDATE users SET femcoins = femcoins + 50 WHERE user_id = ?",
            (user_id,),
        )

    await safe_callback_answer(callback, "Начислено +50 🪙 и выдан скип КД", show_alert=True)


# =====================================================================
# GROUP ADMIN RIGHTS CHECK CALLBACK
# =====================================================================

async def process_group_addition_reward(
    chat: types.Chat,
    added_by: types.User,
) -> None:
    """Выдаёт награду за добавление бота после подтверждения прав админа."""
    try:
        member_count = await bot.get_chat_member_count(chat.id)
    except Exception:
        return

    if member_count < 15:
        return

    if member_count >= 2000:
        reward = 500
    elif member_count >= 500:
        reward = 300
    else:
        reward = 50

    user_id = added_by.id
    await ensure_user(added_by)

    async with transaction() as db:
        async with db.execute(
            "SELECT reward_claimed FROM group_rewards WHERE chat_id = ?",
            (chat.id,),
        ) as cursor:
            previous = await cursor.fetchone()

        if previous and int(previous["reward_claimed"]) == 1:
            return

        async with db.execute(
            "SELECT count FROM owner_group_counts WHERE owner_id = ?",
            (user_id,),
        ) as cursor:
            owner_row = await cursor.fetchone()

        owner_count = int(owner_row["count"]) if owner_row else 0
        if owner_count >= 3:
            await db.execute(
                """
                INSERT INTO group_rewards(chat_id, owner_id, reward_claimed, member_count, rewarded_at)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET reward_claimed = 1
                """,
                (chat.id, user_id, member_count, now_ts()),
            )
            return

        await db.execute(
            "UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?",
            (reward, user_id),
        )

        await db.execute(
            """
            INSERT INTO owner_group_counts(owner_id, count)
            VALUES (?, 1)
            ON CONFLICT(owner_id) DO UPDATE SET count = count + 1
            """,
            (user_id,),
        )

        await db.execute(
            """
            INSERT INTO group_rewards(chat_id, owner_id, reward_claimed, member_count, rewarded_at)
            VALUES (?, ?, 1, ?, ?)
            """,
            (chat.id, user_id, member_count, now_ts()),
        )

    try:
        await bot.send_message(
            chat.id,
            "🎉 <b>Спасибо за добавление бота!</b>\n"
            f"Пользователю <b>{display_name(added_by)}</b> начислено <b>+{reward} 🪙</b>.\n"
            f"Размер группы: <b>{member_count}</b> участников.",
        )
    except Exception:
        logger.exception("Ошибка уведомления о награде группы")


@router.callback_query(F.data.startswith("check_admin_rights:"))
async def check_admin_rights_callback(callback: CallbackQuery) -> None:
    chat_id_str = parse_callback(callback.data or "", "check_admin_rights:")
    try:
        chat_id = int(chat_id_str or "0")
    except ValueError:
        await safe_callback_answer(callback, "Некорректный ID группы", show_alert=True)
        return

    is_adm = await check_bot_is_admin(chat_id)
    if not is_adm:
        await safe_callback_answer(
            callback,
            "❌ Права администратора всё ещё не выданы!\n"
            "Перейдите в настройки группы и сделайте бота администратором.",
            show_alert=True,
        )
        return

    # Права выданы!
    async with transaction() as db:
        await db.execute(
            """
            UPDATE chats
            SET has_admin_rights = 1,
                admin_warned = 0
            WHERE chat_id = ?
            """,
            (chat_id,),
        )

    if callback.message and callback.message.chat:
        await process_group_addition_reward(callback.message.chat, callback.from_user)

    await safe_callback_answer(callback, "✅ Права администратора подтверждены!", show_alert=True)

    try:
        if callback.message:
            await callback.message.edit_text(
                "✅ <b>Права администратора успешно подтверждены!</b>\n"
                "Бот готов к работе в группе."
            )
    except Exception:
        pass


@router.my_chat_member()
async def bot_membership_handler(
    event: types.ChatMemberUpdated,
) -> None:
    chat = event.chat
    if chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return

    new_status = str(event.new_chat_member.status)

    if new_status in {"left", "kicked"}:
        async with transaction() as db:
            await db.execute(
                "UPDATE chats SET has_admin_rights = 0, admin_warned = 0 WHERE chat_id = ?",
                (chat.id,),
            )
        return

    added_by = event.from_user
    if not added_by:
        return

    is_admin = new_status == "administrator"

    if not is_admin:
        async with transaction() as db:
            await db.execute(
                """
                INSERT INTO chats(chat_id, chat_type, inactivity_interval, last_activity, has_admin_rights, admin_warned)
                VALUES (?, ?, 3600, ?, 0, 1)
                ON CONFLICT(chat_id) DO UPDATE SET
                    has_admin_rights = 0,
                    admin_warned = 1
                """,
                (chat.id, chat.type, now_ts()),
            )
        await send_admin_rights_request(chat.id)
        return

    async with transaction() as db:
        await db.execute(
            """
            INSERT INTO chats(chat_id, chat_type, inactivity_interval, last_activity, has_admin_rights, admin_warned)
            VALUES (?, ?, 3600, ?, 1, 0)
            ON CONFLICT(chat_id) DO UPDATE SET
                has_admin_rights = 1,
                admin_warned = 0
            """,
            (chat.id, chat.type, now_ts()),
        )

    await process_group_addition_reward(chat, added_by)


# =====================================================================
# ECONOMY, SHOP, STARS & CASINO
# =====================================================================

async def find_user_by_username(username: str) -> Optional[int]:
    clean = username.strip().lstrip("@").lower()

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT user_id
            FROM users
            WHERE LOWER(REPLACE(username, '@', '')) = ?
            LIMIT 1
            """,
            (clean,),
        ) as cursor:
            row = await cursor.fetchone()

    return int(row["user_id"]) if row else None


async def resolve_transfer_target(message: Message) -> Optional[int]:
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id

    if not message.text:
        return None

    for part in message.text.split()[1:]:
        if part.startswith("@"):
            return await find_user_by_username(part)

    return None


@router.message(
    lambda message: command_is(
        message,
        "передать",
        "перевод",
        "дать",
        "pay",
        "transfer",
    )
)
async def transfer_command(message: Message) -> None:
    await ensure_user(message.from_user)
    if not message.text:
        return

    amount: Optional[int] = None
    for part in message.text.split()[1:]:
        if part.isdigit():
            amount = int(part)
            break

    if not amount or amount <= 0:
        await message.answer(
            "❗ Укажите положительную сумму.\n"
            "Пример: <code>передать 100</code> ответом на сообщение игрока."
        )
        return

    target_id = await resolve_transfer_target(message)
    if not target_id:
        await message.answer(
            "❗ Укажите получателя ответом на сообщение или через @username."
        )
        return

    sender_id = message.from_user.id
    if sender_id == target_id or target_id == bot.id:
        await message.answer("❌ Нельзя выполнить перевод.")
        return

    if not await user_exists(target_id):
        await message.answer("❌ Получатель ещё не зарегистрирован.")
        return

    vip = await is_vip(sender_id)
    commission_percent = 5 if vip else 10
    commission = max(1, amount * commission_percent // 100)
    received = amount - commission

    if received <= 0:
        await message.answer("❌ Сумма слишком мала.")
        return

    async with transaction() as db:
        async with db.execute(
            "SELECT femcoins FROM users WHERE user_id = ?",
            (sender_id,),
        ) as cursor:
            sender = await cursor.fetchone()

        if not sender or int(sender["femcoins"]) < amount:
            balance = int(sender["femcoins"]) if sender else 0
            await message.answer(f"❌ Недостаточно монет.\nВаш баланс: <b>{balance} 🪙</b>")
            return

        updated = await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins - ?
            WHERE user_id = ? AND femcoins >= ?
            """,
            (amount, sender_id, amount),
        )

        if updated.rowcount != 1:
            await message.answer("❌ Баланс изменился. Повторите перевод.")
            return

        await db.execute(
            "UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?",
            (received, target_id),
        )

    sender_name = await get_username_from_db(sender_id)
    target_name = await get_username_from_db(target_id)

    await message.answer(
        "💸 <b>Перевод выполнен!</b>\n\n"
        f"👤 Отправитель: <b>{sender_name}</b>\n"
        f"🎯 Получатель: <b>{target_name}</b>\n"
        f"📤 Отправлено: <b>{amount} 🪙</b>\n"
        f"📥 Получено: <b>{received} 🪙</b>\n"
        f"🔥 Комиссия: <b>{commission} 🪙</b> ({commission_percent}%)"
    )


def shop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Сброс КД — 150 🪙",
                    callback_data="shop:cd",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🍀 Эликсир удачи — 250 🪙",
                    callback_data="shop:luck",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎴 Новая карта — 400 🪙",
                    callback_data="shop:newcard",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛡 Артефакты",
                    callback_data="shop:artifacts",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👑 Купить VIP",
                    callback_data="shop:vip",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Купить фемкоины",
                    callback_data="shop:coins",
                )
            ],
        ]
    )


@router.message(
    lambda message: command_is(
        message,
        "магазин",
        "shop",
    )
)
async def shop_command(message: Message) -> None:
    await ensure_user(message.from_user)

    if message.chat.type != ChatType.PRIVATE:
        bot_info = await bot.get_me()
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🏪 Открыть магазин",
                        url=f"https://t.me/{bot_info.username}?start=shop",
                    )
                ]
            ]
        )
        await message.answer(
            "🏪 Магазин доступен только в личных сообщениях.",
            reply_markup=keyboard,
        )
        return

    async with db_connection() as db:
        async with db.execute(
            "SELECT femcoins FROM users WHERE user_id = ?",
            (message.from_user.id,),
        ) as cursor:
            row = await cursor.fetchone()

    balance = int(row["femcoins"]) if row else 0

    await message.answer(
        "🏪 <b>Магазин</b>\n\n"
        f"Ваш баланс: <b>{balance} 🪙</b>\n\n"
        "Выберите товар:",
        reply_markup=shop_keyboard(),
    )


@router.callback_query(F.data == "shop:main")
async def shop_main_callback(callback: CallbackQuery) -> None:
    async with db_connection() as db:
        async with db.execute(
            "SELECT femcoins FROM users WHERE user_id = ?",
            (callback.from_user.id,),
        ) as cursor:
            row = await cursor.fetchone()

    balance = int(row["femcoins"]) if row else 0
    await safe_edit_text(
        callback.message,
        f"🏪 <b>Магазин</b>\n\nВаш баланс: <b>{balance} 🪙</b>\n\nВыберите товар:",
        shop_keyboard(),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "shop:cd")
async def shop_cd_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    current_time = now_ts()

    async with transaction() as db:
        async with db.execute(
            "SELECT femcoins, cd_resets_count, cd_reset_expires FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row or int(row["femcoins"]) < 150:
            await safe_callback_answer(callback, "Недостаточно монет", show_alert=True)
            return

        old_count = int(row["cd_resets_count"] or 0)
        old_expires = float(row["cd_reset_expires"] or 0)
        if old_expires <= current_time:
            old_count = 0

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins - 150,
                cd_resets_count = ?,
                cd_reset_expires = ?
            WHERE user_id = ? AND femcoins >= 150
            """,
            (old_count + 1, current_time + 3 * 3600, user_id),
        )

    await safe_callback_answer(callback, f"Скипов КД: {old_count + 1}", show_alert=True)


@router.callback_query(F.data == "shop:luck")
async def shop_luck_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    current_time = now_ts()

    async with transaction() as db:
        async with db.execute(
            "SELECT femcoins, luck_expires FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row or int(row["femcoins"]) < 250:
            await safe_callback_answer(callback, "Недостаточно монет", show_alert=True)
            return

        old_expires = float(row["luck_expires"] or 0)
        start = max(current_time, old_expires)

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins - 250,
                luck_expires = ?
            WHERE user_id = ? AND femcoins >= 250
            """,
            (start + 3 * 3600, user_id),
        )

    await safe_callback_answer(callback, "Эликсир удачи активирован на 3 часа", show_alert=True)


@router.callback_query(F.data == "shop:newcard")
async def shop_new_card_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id

    async with transaction() as db:
        async with db.execute(
            "SELECT femcoins FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        if not user or int(user["femcoins"]) < 400:
            await safe_callback_answer(callback, "Недостаточно монет", show_alert=True)
            return

        async with db.execute(
            """
            SELECT id, file_id, rarity, description
            FROM cards
            WHERE id NOT IN (
                SELECT card_id FROM inventory WHERE user_id = ?
            )
            ORDER BY RANDOM()
            LIMIT 1
            """,
            (user_id,),
        ) as cursor:
            card = await cursor.fetchone()

        if not card:
            await safe_callback_answer(callback, "У вас уже собраны все карты", show_alert=True)
            return

        await db.execute(
            "UPDATE users SET femcoins = femcoins - 400 WHERE user_id = ? AND femcoins >= 400",
            (user_id,),
        )
        await db.execute(
            """
            INSERT INTO inventory(user_id, card_id, count)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, card_id) DO UPDATE SET count = count + 1
            """,
            (user_id, int(card["id"])),
        )

    caption = (
        f"🎴 <b>Новая карта #{int(card['id'])}!</b>\n\n"
        f"🏷 Редкость: <b>{esc(card['rarity'])}</b>\n"
        f"📝 {esc(card['description']) or 'Без описания'}"
    )

    await callback.message.answer_photo(card["file_id"], caption=caption)
    await safe_callback_answer(callback, "Карта куплена")


@router.callback_query(F.data == "shop:artifacts")
async def shop_artifacts_callback(callback: CallbackQuery) -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔰 Слабый щит — 30 🪙",
                    callback_data="artifact_shop:weak",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛡 Броня — 300 🪙",
                    callback_data="artifact_shop:armor",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="shop:main",
                )
            ],
        ]
    )
    await safe_edit_text(
        callback.message,
        "🛡 <b>Артефакты</b>\n\nУсиленный и фембойский щиты собираются в крафте.",
        keyboard,
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "artifact_shop:weak")
async def buy_weak_shield_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    async with transaction() as db:
        async with db.execute("SELECT femcoins FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()

        if not user or int(user["femcoins"]) < 30:
            await safe_callback_answer(callback, "Недостаточно монет", show_alert=True)
            return

        await db.execute(
            "UPDATE users SET femcoins = femcoins - 30 WHERE user_id = ? AND femcoins >= 30",
            (user_id,),
        )
        await db.execute(
            """
            INSERT INTO artifacts(user_id, artifact_type, subtype, amount, created_at)
            VALUES (?, 'shield', 'weak', 1, ?)
            ON CONFLICT(user_id, artifact_type, subtype) DO UPDATE SET amount = amount + 1
            """,
            (user_id, now_ts()),
        )

    await safe_callback_answer(callback, "Слабый щит куплен", show_alert=True)


@router.callback_query(F.data == "artifact_shop:armor")
async def buy_armor_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    async with transaction() as db:
        async with db.execute("SELECT femcoins FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()

        if not user or int(user["femcoins"]) < 300:
            await safe_callback_answer(callback, "Недостаточно монет", show_alert=True)
            return

        await db.execute(
            "UPDATE users SET femcoins = femcoins - 300 WHERE user_id = ? AND femcoins >= 300",
            (user_id,),
        )
        await db.execute(
            """
            INSERT INTO artifacts(user_id, artifact_type, subtype, amount, created_at)
            VALUES (?, 'armor', '', 1, ?)
            ON CONFLICT(user_id, artifact_type, subtype) DO UPDATE SET amount = amount + 1
            """,
            (user_id, now_ts()),
        )

    await safe_callback_answer(callback, "Броня куплена", show_alert=True)


VIP_TEXT = (
    "👑 <b>VIP-статус</b>\n\n"
    "• 20% шанс получить вторую карту;\n"
    "• кулдаун фема — 2 часа;\n"
    "• компенсация VIP-дубликатов;\n"
    "• улучшенные веса карт;\n"
    "• комиссия переводов — 5%;\n"
    "• дейлик каждые 22 часа;\n"
    "• +5 монет к дейлику."
)


def vip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🪙 За фемкоины",
                    callback_data="vip:coins",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ За Stars",
                    callback_data="vip:stars",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="shop:main",
                )
            ],
        ]
    )


@router.callback_query(F.data == "shop:vip")
async def shop_vip_callback(callback: CallbackQuery) -> None:
    await safe_edit_text(callback.message, VIP_TEXT, vip_keyboard())
    await safe_callback_answer(callback)


@router.callback_query(F.data == "vip:coins")
async def vip_coins_menu_callback(callback: CallbackQuery) -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1 день — 1500 🪙", callback_data="vip_coins:1")],
            [InlineKeyboardButton(text="7 дней — 4500 🪙", callback_data="vip_coins:7")],
            [InlineKeyboardButton(text="30 дней — 10000 🪙", callback_data="vip_coins:30")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="shop:vip")],
        ]
    )
    await safe_edit_text(callback.message, VIP_TEXT + "\n\nВыберите срок:", keyboard)
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("vip_coins:"))
async def buy_vip_coins_callback(callback: CallbackQuery) -> None:
    raw_days = parse_callback(callback.data or "", "vip_coins:")
    try:
        days = int(raw_days or "0")
    except ValueError:
        await safe_callback_answer(callback, "Некорректный срок", show_alert=True)
        return

    prices = {1: 1500, 7: 4500, 30: 10000}
    if days not in prices:
        return

    user_id = callback.from_user.id
    current_time = now_ts()

    async with transaction() as db:
        async with db.execute("SELECT femcoins, vip_until FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()

        if not user or int(user["femcoins"]) < prices[days]:
            await safe_callback_answer(callback, "Недостаточно фемкоинов", show_alert=True)
            return

        vip_until = max(current_time, float(user["vip_until"] or 0))
        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins - ?,
                vip_until = ?
            WHERE user_id = ? AND femcoins >= ?
            """,
            (prices[days], vip_until + days * 86400, user_id, prices[days]),
        )

    await safe_callback_answer(callback, f"VIP активирован на {days} дн.", show_alert=True)


@router.callback_query(F.data == "vip:stars")
async def vip_stars_menu_callback(callback: CallbackQuery) -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1 день — 250 ⭐", callback_data="vip_stars:1")],
            [InlineKeyboardButton(text="7 дней — 800 ⭐", callback_data="vip_stars:7")],
            [InlineKeyboardButton(text="30 дней — 2000 ⭐", callback_data="vip_stars:30")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="shop:vip")],
        ]
    )
    await safe_edit_text(callback.message, VIP_TEXT + "\n\nВыберите срок:", keyboard)
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("vip_stars:"))
async def vip_stars_invoice_callback(callback: CallbackQuery) -> None:
    raw_days = parse_callback(callback.data or "", "vip_stars:")
    try:
        days = int(raw_days or "0")
    except ValueError:
        return

    prices = {1: 250, 7: 800, 30: 2000}
    if days not in prices:
        return

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"VIP на {days} дн.",
        description=f"VIP-статус на {days} дней",
        payload=f"vip:{days}",
        currency="XTR",
        prices=[LabeledPrice(label=f"VIP на {days} дн.", amount=prices[days])],
        provider_token="",
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "shop:coins")
async def shop_coins_callback(callback: CallbackQuery) -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="100 монет — 15 ⭐", callback_data="coins_stars:100")],
            [InlineKeyboardButton(text="500 монет — 70 ⭐", callback_data="coins_stars:500")],
            [InlineKeyboardButton(text="1000 монет — 135 ⭐", callback_data="coins_stars:1000")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="shop:main")],
        ]
    )
    await safe_edit_text(callback.message, "💰 <b>Покупка фемкоинов</b>", keyboard)
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("coins_stars:"))
async def coins_stars_invoice_callback(callback: CallbackQuery) -> None:
    raw_amount = parse_callback(callback.data or "", "coins_stars:")
    try:
        amount = int(raw_amount or "0")
    except ValueError:
        return

    prices = {100: 15, 500: 70, 1000: 135}
    if amount not in prices:
        return

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"{amount} фемкоинов",
        description=f"Покупка {amount} игровых монет",
        payload=f"coins:{amount}",
        currency="XTR",
        prices=[LabeledPrice(label=f"{amount} фемкоинов", amount=prices[amount])],
        provider_token="",
    )
    await safe_callback_answer(callback)


@router.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery) -> None:
    try:
        await query.answer(ok=True)
    except Exception:
        logger.exception("Ошибка pre-checkout")


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message) -> None:
    payment = message.successful_payment
    if not payment:
        return

    payload = payment.invoice_payload
    user_id = message.from_user.id

    if payload.startswith("coins:"):
        try:
            amount = int(payload.split(":", 1)[1])
        except ValueError:
            return

        if amount in {100, 500, 1000}:
            async with transaction() as db:
                await db.execute(
                    "UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?",
                    (amount, user_id),
                )
            await message.answer(f"⭐ Оплата прошла успешно.\nНачислено: <b>+{amount} 🪙</b>")
            return

    if payload.startswith("vip:"):
        try:
            days = int(payload.split(":", 1)[1])
        except ValueError:
            return

        if days in {1, 7, 30}:
            current_time = now_ts()
            async with transaction() as db:
                async with db.execute("SELECT vip_until FROM users WHERE user_id = ?", (user_id,)) as cursor:
                    row = await cursor.fetchone()

                old_vip = float(row["vip_until"] or 0) if row else 0
                new_vip = max(current_time, old_vip) + days * 86400
                await db.execute("UPDATE users SET vip_until = ? WHERE user_id = ?", (new_vip, user_id))

            await message.answer(f"⭐ Оплата прошла успешно.\nVIP активирован на <b>{days} дн.</b>")


# -------------------------
# CASINO
# -------------------------

def casino_keyboard(bet: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'✅ ' if bet == value else ''}{value} 🪙",
                    callback_data=f"casino_bet:{value}",
                )
                for value in sorted(CASINO_BETS)
            ],
            [InlineKeyboardButton(text=f"🎲 Кости — {bet} 🪙", callback_data="casino:dice")],
            [
                InlineKeyboardButton(text=f"🔴 Красное x2 — {bet}", callback_data="casino:red"),
                InlineKeyboardButton(text=f"⚫ Чёрное x2 — {bet}", callback_data="casino:black"),
            ],
            [InlineKeyboardButton(text=f"🟢 Зеро x14 — {bet}", callback_data="casino:zero")],
            [InlineKeyboardButton(text=f"🎰 Слоты — {bet} 🪙", callback_data="casino:slots")],
        ]
    )


@router.message(
    lambda message: command_is(
        message,
        "казино",
        "kaz",
    )
)
async def casino_command(message: Message) -> None:
    await ensure_user(message.from_user)
    bet = user_casino_bets.get(message.from_user.id, 20)
    await message.answer(
        "🎰 <b>Фем-казино</b>\n\n"
        f"Текущая ставка: <b>{bet} 🪙</b>\n"
        "Для рулетки минимальная ставка — 20 монет.",
        reply_markup=casino_keyboard(bet),
    )


@router.callback_query(F.data.startswith("casino_bet:"))
async def casino_bet_callback(callback: CallbackQuery) -> None:
    raw_bet = parse_callback(callback.data or "", "casino_bet:")
    try:
        bet = int(raw_bet or "0")
    except ValueError:
        return

    if bet not in CASINO_BETS:
        return

    user_casino_bets[callback.from_user.id] = bet
    await safe_edit_text(
        callback.message,
        f"🎰 <b>Фем-казино</b>\n\nТекущая ставка: <b>{bet} 🪙</b>",
        casino_keyboard(bet),
    )
    await safe_callback_answer(callback, f"Ставка: {bet} 🪙")


@router.callback_query(F.data.startswith("casino:"))
async def casino_game_callback(callback: CallbackQuery) -> None:
    action = parse_callback(callback.data or "", "casino:")
    if not action:
        return

    action = action.strip()
    user_id = callback.from_user.id
    bet = user_casino_bets.get(user_id, 20)

    if action in {"red", "black", "zero"} and bet < 20:
        await safe_callback_answer(callback, "Для рулетки нужна ставка минимум 20", show_alert=True)
        return

    async with transaction() as db:
        async with db.execute("SELECT femcoins FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()

        if not user or int(user["femcoins"]) < bet:
            await safe_callback_answer(callback, "Недостаточно монет", show_alert=True)
            return

        await db.execute(
            "UPDATE users SET femcoins = femcoins - ? WHERE user_id = ? AND femcoins >= ?",
            (bet, user_id, bet),
        )

        result_text = ""

        if action == "dice":
            player = random.randint(1, 6)
            dealer = random.randint(1, 6)
            if player > dealer:
                net = bet
                await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (bet + net, user_id))
                result_text = f"🎲 Вы выиграли: {player} против {dealer}.\nПрибыль: <b>+{net} 🪙</b>"
            elif player < dealer:
                result_text = f"🎲 Вы проиграли: {player} против {dealer}.\nПотеря: <b>-{bet} 🪙</b>"
            else:
                await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (bet, user_id))
                result_text = f"🎲 Ничья: {player} и {dealer}.\nСтавка возвращена."

        elif action in {"red", "black", "zero"}:
            number = random.randint(0, 36)
            color = "zero" if number == 0 else ("red" if number % 2 else "black")

            if action == color:
                multiplier = 14 if action == "zero" else 2
                net = bet * multiplier - bet
                await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (bet + net, user_id))
                result_text = f"🎡 Выпало число <b>{number}</b>.\nПобеда! Прибыль: <b>+{net} 🪙</b>"
            else:
                result_text = f"🎡 Выпало число <b>{number}</b>.\nПотеря: <b>-{bet} 🪙</b>"

        elif action == "slots":
            symbols = ["🍒", "🍋", "🍇", "💎", "7️⃣"]
            result = random.choices(symbols, k=3)
            if result[0] == result[1] == result[2] == "7️⃣":
                multiplier = 15
            elif result[0] == result[1] == result[2]:
                multiplier = 4
            else:
                multiplier = 0

            if multiplier:
                total_return = bet * multiplier
                net = total_return - bet
                await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (total_return, user_id))
                result_text = f"🎰 {' | '.join(result)}\nПобеда! Прибыль: <b>+{net} 🪙</b>"
            else:
                result_text = f"🎰 {' | '.join(result)}\nПотеря: <b>-{bet} 🪙</b>"

    await callback.message.answer(result_text)
    await safe_callback_answer(callback)


# =====================================================================
# FEM CARD DRAW & CHESTS
# =====================================================================

def get_user_fem_lock(user_id: int) -> asyncio.Lock:
    lock = user_fem_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        user_fem_locks[user_id] = lock
    return lock


def chest_keyboard(chest_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎁 Забрать сундук",
                    callback_data=f"chest:{chest_id}",
                )
            ]
        ]
    )


async def create_chest(
    chat_id: int,
    chest_type: str,
    payload: dict[str, Any],
    *,
    lifetime: int = 3600,
) -> str:
    chest_id = secrets.token_urlsafe(10).replace("-", "").replace("_", "")
    created_at = now_ts()

    async with transaction() as db:
        await db.execute(
            """
            INSERT INTO chests(chest_id, chat_id, chest_type, payload, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                chest_id,
                chat_id,
                chest_type,
                json.dumps(payload, ensure_ascii=False),
                created_at,
                created_at + lifetime,
            ),
        )

    return chest_id


async def choose_group_chest() -> tuple[str, dict[str, Any]]:
    roll = random.random()
    if roll < 0.005:
        return "artifact", {}
    if roll < 0.025:
        return "legendary", {"coins": random.randint(100, 199)}
    if roll < 0.05:
        return "rare", {"coins": random.randint(50, 99)}
    return "normal", {"coins": random.randint(5, 49)}


async def send_group_chest(chat_id: int) -> None:
    chest_type, payload = await choose_group_chest()
    chest_id = await create_chest(chat_id, chest_type, payload)

    titles = {
        "normal": "📦 <b>Появился обычный сундук!</b>\nКто первым нажмёт кнопку, получит награду.",
        "rare": "💎 <b>Появился редкий сундук!</b>\nВнутри спрятана ценная награда.",
        "legendary": "🌟 <b>Появился легендарный сундук!</b>\nВнутри монеты и шанс получить клинок.",
        "artifact": "🩷 <b>Появился фембойский артефактный сундук!</b>\nВнутри редкие предметы.",
    }

    try:
        sent = await bot.send_message(
            chat_id,
            titles.get(chest_type, "📦 Сундук!"),
            reply_markup=chest_keyboard(chest_id),
        )
        async with transaction() as db:
            await db.execute(
                "UPDATE chests SET message_id = ? WHERE chest_id = ?",
                (sent.message_id, chest_id),
            )
    except Exception:
        logger.exception("Ошибка отправки сундука")


async def grant_chest_reward(
    db: aiosqlite.Connection,
    user_id: int,
    chest_type: str,
    payload: dict[str, Any],
) -> str:
    rewards: list[str] = []

    if chest_type in ("normal", "rare"):
        coins = int(payload.get("coins", 15))
        await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (coins, user_id))
        rewards.append(f"+{coins} 🪙")

    elif chest_type == "legendary":
        coins = int(payload.get("coins", 100))
        await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (coins, user_id))
        rewards.append(f"+{coins} 🪙")

        if random.random() < 0.05:
            await db.execute(
                """
                INSERT INTO artifacts(user_id, artifact_type, subtype, amount, created_at)
                VALUES (?, 'blade', '', 1, ?)
                ON CONFLICT(user_id, artifact_type, subtype) DO UPDATE SET amount = amount + 1
                """,
                (user_id, now_ts()),
            )
            rewards.append("+1 клинок")

    elif chest_type == "artifact":
        coins = random.randint(200, 350)
        await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (coins, user_id))
        await db.execute(
            """
            INSERT INTO artifacts(user_id, artifact_type, subtype, amount, created_at)
            VALUES (?, 'blade', '', 1, ?)
            ON CONFLICT(user_id, artifact_type, subtype) DO UPDATE SET amount = amount + 1
            """,
            (user_id, now_ts()),
        )
        rewards.extend([f"+{coins} 🪙", "+1 клинок"])

    return ", ".join(rewards)


@router.callback_query(F.data.startswith("chest:"))
async def chest_claim_callback(callback: CallbackQuery) -> None:
    chest_id = parse_callback(callback.data or "", "chest:")
    if not chest_id:
        return

    user_id = callback.from_user.id

    async with transaction() as db:
        async with db.execute(
            "SELECT chest_type, payload, claimed_by, expires_at FROM chests WHERE chest_id = ?",
            (chest_id,),
        ) as cursor:
            chest = await cursor.fetchone()

        if not chest or chest["claimed_by"] is not None or float(chest["expires_at"]) <= now_ts():
            await safe_callback_answer(callback, "Сундук уже забран или истёк", show_alert=True)
            return

        claimed = await db.execute(
            """
            UPDATE chests
            SET claimed_by = ?, claimed_at = ?
            WHERE chest_id = ? AND claimed_by IS NULL AND expires_at > ?
            """,
            (user_id, now_ts(), chest_id, now_ts()),
        )

        if claimed.rowcount != 1:
            await safe_callback_answer(callback, "Сундук уже забрали", show_alert=True)
            return

        try:
            payload = json.loads(chest["payload"] or "{}")
        except json.JSONDecodeError:
            payload = {}

        reward_text = await grant_chest_reward(db, user_id, str(chest["chest_type"]), payload)

    try:
        await callback.message.edit_text(
            f"🎁 <b>Сундук забран!</b>\n\nИгрок: <b>{display_name(callback.from_user)}</b>\nНаграда: <b>{esc(reward_text)}</b>"
        )
    except Exception:
        pass

    await safe_callback_answer(callback, "Награда зачислена")


@router.message(
    lambda message: command_is(
        message,
        "фем",
        "fem",
        "foto",
    )
)
async def fem_command(message: Message) -> None:
    await ensure_user(message.from_user)
    user_id = message.from_user.id
    lock = get_user_fem_lock(user_id)

    async with lock:
        current_time = now_ts()

        async with transaction() as db:
            async with db.execute(
                """
                SELECT last_fem_time, cd_resets_count, cd_reset_expires,
                       luck_expires, vip_until, attack_block_until
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ) as cursor:
                user = await cursor.fetchone()

            if not user:
                return

            attack_until = float(user["attack_block_until"] or 0)
            if attack_until > current_time:
                await message.answer(
                    f"🚫 Сбор карт временно запрещён.\nОсталось: <b>{short_time(attack_until - current_time)}</b>"
                )
                return

            last_fem = float(user["last_fem_time"] or 0)
            cooldown = await get_effective_fem_cooldown(db, user_id, current_time)
            elapsed = current_time - last_fem

            skips = int(user["cd_resets_count"] or 0)
            skip_expires = float(user["cd_reset_expires"] or 0)
            if skip_expires <= current_time:
                skips = 0

            if elapsed < cooldown:
                if skips > 0:
                    await db.execute(
                        """
                        UPDATE users
                        SET cd_resets_count = ?, cd_reset_expires = ?
                        WHERE user_id = ?
                        """,
                        (skips - 1, skip_expires if skips > 1 else 0, user_id),
                    )
                else:
                    await message.answer(
                        f"⏳ Рано.\nДо следующей попытки: <b>{short_time(cooldown - elapsed)}</b>"
                    )
                    return

            await db.execute("UPDATE users SET last_fem_time = ? WHERE user_id = ?", (current_time, user_id))
            vip = float(user["vip_until"] or 0) > current_time
            lucky = float(user["luck_expires"] or 0) > current_time

        first_card = await draw_card(user_id, lucky=lucky, vip=vip)
        if not first_card:
            await message.answer("😔 В базе пока нет доступных карт.")
            return

        keyboard = await post_card_keyboard()
        caption = format_card_caption(
            int(first_card["id"]),
            str(first_card["rarity"]),
            str(first_card["description"]),
            bool(first_card["duplicate"]),
            int(first_card["compensation"]),
        )

        await message.answer_photo(first_card["file_id"], caption=caption, reply_markup=keyboard)

        if vip and random.random() < 0.20:
            second_card = await draw_card(user_id, lucky=lucky, vip=vip, force_duplicate=True)
            if second_card:
                second_caption = format_card_caption(
                    int(second_card["id"]),
                    str(second_card["rarity"]),
                    str(second_card["description"]),
                    True,
                    int(second_card["compensation"]),
                    prefix="👑 VIP-бонус: вторая карта",
                )
                await message.answer_photo(second_card["file_id"], caption=second_caption, reply_markup=keyboard)

        if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            if random.random() < 0.10:
                await send_group_chest(message.chat.id)


# =====================================================================
# INACTIVITY CHEST WORKER (ТОЛЬКО ДЛЯ АВТОРИЗОВАННЫХ ГРУПП)
# =====================================================================

async def inactivity_chest_worker() -> None:
    """
    Периодический воркер перекати-поля.
    НЕ проверяет группы постоянно, а работает только по тем, где права подтверждены!
    """
    while True:
        try:
            current_time = now_ts()

            async with db_connection() as db:
                async with db.execute(
                    """
                    SELECT chat_id, inactivity_interval, last_activity
                    FROM chats
                    WHERE chat_type IN ('group', 'supergroup')
                      AND has_admin_rights = 1
                    """
                ) as cursor:
                    chats = await cursor.fetchall()

            for chat in chats:
                chat_id = int(chat["chat_id"])

                try:
                    member_count = await bot.get_chat_member_count(chat_id)
                except Exception:
                    # Если права сняли в процессе работы:
                    async with transaction() as db:
                        await db.execute(
                            "UPDATE chats SET has_admin_rights = 0, admin_warned = 1 WHERE chat_id = ?",
                            (chat_id,),
                        )
                    await send_admin_rights_request(chat_id)
                    continue

                if member_count < 30:
                    continue

                last_activity = float(
                    chat_activity_memory.get(chat_id, chat["last_activity"] or 0)
                )
                interval = max(60, min(86400, int(chat["inactivity_interval"] or 3600)))

                if last_activity > 0 and current_time - last_activity >= interval:
                    async with transaction() as db:
                        await db.execute(
                            """
                            UPDATE chats
                            SET last_activity = ?, last_chest_at = ?
                            WHERE chat_id = ?
                            """,
                            (current_time, current_time, chat_id),
                        )

                    chat_activity_memory[chat_id] = current_time

                    chest_type = "normal"
                    payload = {
                        "coins": random.choices(
                            [20, 35, 50, 75, 120],
                            weights=[45, 25, 18, 8, 4],
                            k=1,
                        )[0]
                    }

                    chest_id = await create_chest(chat_id, chest_type, payload)

                    try:
                        await bot.send_message(
                            chat_id,
                            "🌵 <b>Перекати-поле!</b>\nВ чате было тихо, и появился сундук.",
                            reply_markup=chest_keyboard(chest_id),
                        )
                    except Exception:
                        logger.exception("Ошибка отправки сундука тишины")

        except Exception:
            logger.exception("Ошибка worker перекати-поля")

        await asyncio.sleep(60)


# =====================================================================
# ARTIFACTS, CRAFT, TRADE & ADMIN COMMANDS
# =====================================================================

async def add_artifact(
    db: aiosqlite.Connection,
    user_id: int,
    artifact_type: str,
    subtype: str = "",
    amount: int = 1,
) -> None:
    await db.execute(
        """
        INSERT INTO artifacts(user_id, artifact_type, subtype, amount, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, artifact_type, subtype)
        DO UPDATE SET amount = amount + excluded.amount
        """,
        (user_id, artifact_type, subtype, amount, now_ts()),
    )


async def show_artifacts(message: Message, artifact_type: Optional[str] = None) -> None:
    await ensure_user(message.from_user)
    user_id = message.from_user.id

    async with db_connection() as db:
        if artifact_type:
            async with db.execute(
                """
                SELECT artifact_type, subtype, amount, blocked_until
                FROM artifacts
                WHERE user_id = ? AND artifact_type = ?
                ORDER BY artifact_type, subtype
                """,
                (user_id, artifact_type),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                """
                SELECT artifact_type, subtype, amount, blocked_until
                FROM artifacts
                WHERE user_id = ?
                ORDER BY artifact_type, subtype
                """,
                (user_id,),
            ) as cursor:
                rows = await cursor.fetchall()

    names = {"blade": "🗡 Клинки", "armor": "🛡 Броня", "shield": "🔰 Щиты"}
    subtype_names = {"weak": "слабый", "reinforced": "усиленный", "femboy": "фембойский", "": ""}

    lines = [f"<b>{names.get(artifact_type, 'Артефакты')}</b>", ""]

    for row in rows:
        amount = int(row["amount"] or 0)
        if amount <= 0:
            continue
        subtype = subtype_names.get(str(row["subtype"] or ""), str(row["subtype"] or ""))
        title = f"{subtype} {names.get(str(row['artifact_type']), str(row['artifact_type']))}".strip()
        lines.append(f"• {esc(title)}: <b>{amount}</b>")

    if len(lines) == 2:
        lines.append("Пока ничего нет.")

    await message.answer("\n".join(lines))


@router.message(lambda message: command_is(message, "мои клинки"))
async def my_blades_command(message: Message) -> None:
    await show_artifacts(message, "blade")


@router.message(lambda message: command_is(message, "моя броня"))
async def my_armor_command(message: Message) -> None:
    await show_artifacts(message, "armor")


@router.message(lambda message: command_is(message, "мои щиты"))
async def my_shields_command(message: Message) -> None:
    await show_artifacts(message, "shield")


async def craft_shield(user_id: int, source: str, target: str) -> bool:
    async with transaction() as db:
        async with db.execute(
            "SELECT amount FROM artifacts WHERE user_id = ? AND artifact_type = 'shield' AND subtype = ?",
            (user_id, source),
        ) as cursor:
            row = await cursor.fetchone()

        if not row or int(row["amount"] or 0) < 3:
            return False

        await db.execute(
            """
            UPDATE artifacts
            SET amount = amount - 3
            WHERE user_id = ? AND artifact_type = 'shield' AND subtype = ? AND amount >= 3
            """,
            (user_id, source),
        )
        await add_artifact(
            db,
            user_id,
            "shield" if target != "armor" else "armor",
            "" if target == "armor" else target,
            1,
        )
    return True


@router.message(lambda message: command_is(message, "крафт щитов"))
async def shield_craft_command(message: Message) -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="3 слабых → усиленный", callback_data="shieldcraft:weak")],
            [InlineKeyboardButton(text="3 усиленных → фембойский", callback_data="shieldcraft:reinforced")],
            [InlineKeyboardButton(text="3 фембойских → броня", callback_data="shieldcraft:femboy")],
        ]
    )
    await message.answer("⚒ <b>Крафт щитов</b>\nКрафт выполняется атомарно.", reply_markup=keyboard)


@router.callback_query(F.data.startswith("shieldcraft:"))
async def shield_craft_callback(callback: CallbackQuery) -> None:
    source = parse_callback(callback.data or "", "shieldcraft:")
    targets = {"weak": "reinforced", "reinforced": "femboy", "femboy": "armor"}
    target = targets.get(source or "")

    if not target:
        return

    success = await craft_shield(callback.from_user.id, source, target)
    if not success:
        await safe_callback_answer(callback, "Нужно 3 предмета одного типа", show_alert=True)
        return

    await safe_callback_answer(callback, "Крафт успешно выполнен", show_alert=True)


@router.message(lambda message: command_is(message, "крафт"))
async def card_craft_command(message: Message) -> None:
    user_id = message.from_user.id

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT c.rarity, SUM(i.count - 1) AS duplicates
            FROM inventory AS i
            JOIN cards AS c ON c.id = i.card_id
            WHERE i.user_id = ? AND i.count > 1
            GROUP BY c.rarity
            HAVING duplicates >= 3
            """,
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()

    buttons = []
    for row in rows:
        rarity = str(row["rarity"])
        if RARITY_ORDER.get(rarity, -1) >= len(RARITIES) - 1:
            continue
        buttons.append([
            InlineKeyboardButton(
                text=f"{rarity} (дубликатов: {int(row['duplicates'])})",
                callback_data=f"cardcraft:{rarity}",
            )
        ])

    if not buttons:
        await message.answer("❌ Для крафта нужны 3 свободных дубликата одной редкости.")
        return

    await message.answer(
        "⚒ <b>Крафт карт</b>\nОсновная копия карты не расходуется.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("cardcraft:"))
async def card_craft_callback(callback: CallbackQuery) -> None:
    rarity = parse_callback(callback.data or "", "cardcraft:")
    if not rarity or rarity not in RARITY_ORDER:
        return

    index = RARITY_ORDER[rarity]
    if index + 1 >= len(RARITIES):
        return

    target_rarity = RARITIES[index + 1]
    user_id = callback.from_user.id

    async with transaction() as db:
        async with db.execute(
            """
            SELECT i.id, i.count
            FROM inventory AS i
            JOIN cards AS c ON c.id = i.card_id
            WHERE i.user_id = ? AND c.rarity = ? AND i.count > 1
            """,
            (user_id, rarity),
        ) as cursor:
            rows = await cursor.fetchall()

        available = sum(max(0, int(row["count"]) - 1) for row in rows)
        if available < 3:
            await safe_callback_answer(callback, "Недостаточно дубликатов", show_alert=True)
            return

        async with db.execute(
            "SELECT id FROM cards WHERE rarity = ? ORDER BY RANDOM() LIMIT 1",
            (target_rarity,),
        ) as cursor:
            target = await cursor.fetchone()

        if not target:
            await safe_callback_answer(callback, "Карт следующей редкости пока нет", show_alert=True)
            return

        left = 3
        for row in rows:
            take = min(left, int(row["count"]) - 1)
            await db.execute(
                "UPDATE inventory SET count = count - ? WHERE id = ?",
                (take, int(row["id"])),
            )
            left -= take
            if left == 0:
                break

        await db.execute(
            """
            INSERT INTO inventory(user_id, card_id, count)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, card_id) DO UPDATE SET count = count + 1
            """,
            (user_id, int(target["id"])),
        )

    await callback.message.answer(
        f"🎉 <b>Крафт успешен!</b>\nПолучена карта #{int(target['id'])}\nРедкость: <b>{esc(target_rarity)}</b>"
    )
    await safe_callback_answer(callback)


# -------------------------
# TRADE
# -------------------------

def trade_keyboard(trade_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять обмен",
                    callback_data=f"trade_accept:{trade_id}",
                )
            ]
        ]
    )


@router.message(lambda message: command_is(message, "трейд", "trade"))
async def trade_start_command(message: Message) -> None:
    if not message.reply_to_message:
        await message.answer("Команда должна быть ответом на сообщение игрока.")
        return

    target = message.reply_to_message.from_user
    sender = message.from_user

    if not target or target.is_bot or target.id == sender.id:
        await message.answer("❌ Нельзя торговать с собой или ботом.")
        return

    await ensure_user(sender)
    await ensure_user(target)

    trade_id = secrets.token_urlsafe(12)

    async with transaction() as db:
        await db.execute(
            """
            INSERT INTO trades(trade_id, p1, p2, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (trade_id, sender.id, target.id, now_ts(), now_ts()),
        )

    await message.answer(
        f"🤝 <b>Предложение обмена</b>\n\nОт: {display_name(sender)}\nКому: {display_name(target)}\n\n"
        "Оба игрока должны выбрать по одному дубликату одинаковой редкости.",
        reply_markup=trade_keyboard(trade_id),
    )


@router.callback_query(F.data.startswith("trade_accept:"))
async def trade_accept_callback(callback: CallbackQuery) -> None:
    trade_id = parse_callback(callback.data or "", "trade_accept:")
    if not trade_id:
        return

    async with db_connection() as db:
        async with db.execute(
            "SELECT p1, p2, status FROM trades WHERE trade_id = ?",
            (trade_id,),
        ) as cursor:
            trade = await cursor.fetchone()

    if not trade or callback.from_user.id != int(trade["p2"]) or trade["status"] != "pending":
        await safe_callback_answer(callback, "Сделка недоступна", show_alert=True)
        return

    async with transaction() as db:
        await db.execute(
            "UPDATE trades SET status = 'active', updated_at = ? WHERE trade_id = ?",
            (now_ts(), trade_id),
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Выбрать карту", callback_data=f"trade_pick:{trade_id}")],
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"trade_confirm:{trade_id}")],
        ]
    )
    await safe_edit_text(callback.message, "🔄 <b>Обмен открыт.</b> Выберите дубликат.", keyboard)
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("trade_pick:"))
async def trade_pick_callback(callback: CallbackQuery) -> None:
    trade_id = parse_callback(callback.data or "", "trade_pick:")
    user_id = callback.from_user.id

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT c.id, c.rarity, i.count
            FROM inventory AS i
            JOIN cards AS c ON c.id = i.card_id
            WHERE i.user_id = ? AND i.count > 1
            ORDER BY c.id
            """,
            (user_id,),
        ) as cursor:
            cards = await cursor.fetchall()

    buttons = [
        [
            InlineKeyboardButton(
                text=f"#{int(row['id'])} {row['rarity']} x{int(row['count'])}",
                callback_data=f"trade_set:{trade_id}:{int(row['id'])}",
            )
        ]
        for row in cards
    ]

    if not buttons:
        await safe_callback_answer(callback, "Нет доступных дубликатов", show_alert=True)
        return

    await callback.message.answer(
        "Выберите карту для обмена:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("trade_set:"))
async def trade_set_callback(callback: CallbackQuery) -> None:
    payload = parse_callback(callback.data or "", "trade_set:")
    try:
        trade_id, card_id_raw = payload.rsplit(":", 1)
        card_id = int(card_id_raw)
    except Exception:
        return

    user_id = callback.from_user.id

    async with transaction() as db:
        async with db.execute(
            "SELECT p1, p2, status FROM trades WHERE trade_id = ?",
            (trade_id,),
        ) as cursor:
            trade = await cursor.fetchone()

        if not trade or trade["status"] != "active":
            return

        if user_id == int(trade["p1"]):
            await db.execute(
                "UPDATE trades SET p1_card = ?, p1_confirmed = 0, p2_confirmed = 0 WHERE trade_id = ?",
                (card_id, trade_id),
            )
        elif user_id == int(trade["p2"]):
            await db.execute(
                "UPDATE trades SET p2_card = ?, p1_confirmed = 0, p2_confirmed = 0 WHERE trade_id = ?",
                (card_id, trade_id),
            )

    await safe_callback_answer(callback, "Карта выбрана", show_alert=True)


@router.callback_query(F.data.startswith("trade_confirm:"))
async def trade_confirm_callback(callback: CallbackQuery) -> None:
    trade_id = parse_callback(callback.data or "", "trade_confirm:")
    if not trade_id:
        return

    lock = trade_locks.setdefault(trade_id, asyncio.Lock())

    async with lock:
        async with transaction() as db:
            async with db.execute(
                "SELECT p1, p2, p1_card, p2_card, p1_confirmed, p2_confirmed, status FROM trades WHERE trade_id = ?",
                (trade_id,),
            ) as cursor:
                trade = await cursor.fetchone()

            if not trade or trade["status"] != "active":
                return

            user_id = callback.from_user.id
            p1 = int(trade["p1"])
            p2 = int(trade["p2"])

            if not trade["p1_card"] or not trade["p2_card"]:
                await safe_callback_answer(callback, "Оба игрока должны выбрать карту", show_alert=True)
                return

            if user_id == p1:
                await db.execute("UPDATE trades SET p1_confirmed = 1 WHERE trade_id = ?", (trade_id,))
            elif user_id == p2:
                await db.execute("UPDATE trades SET p2_confirmed = 1 WHERE trade_id = ?", (trade_id,))

            if not ((user_id == p1 and int(trade["p2_confirmed"]) == 1) or (user_id == p2 and int(trade["p1_confirmed"]) == 1)):
                await safe_callback_answer(callback, "Ожидается подтверждение второго игрока")
                return

            c1, c2 = int(trade["p1_card"]), int(trade["p2_card"])

            await db.execute("UPDATE inventory SET count = count - 1 WHERE user_id = ? AND card_id = ?", (p1, c1))
            await db.execute("UPDATE inventory SET count = count - 1 WHERE user_id = ? AND card_id = ?", (p2, c2))
            await db.execute(
                "INSERT INTO inventory(user_id, card_id, count) VALUES (?, ?, 1) ON CONFLICT(user_id, card_id) DO UPDATE SET count = count + 1",
                (p1, c2),
            )
            await db.execute(
                "INSERT INTO inventory(user_id, card_id, count) VALUES (?, ?, 1) ON CONFLICT(user_id, card_id) DO UPDATE SET count = count + 1",
                (p2, c1),
            )
            await db.execute("UPDATE trades SET status = 'completed', updated_at = ? WHERE trade_id = ?", (now_ts(), trade_id))

    await callback.message.edit_text("🎉 <b>Обмен завершён!</b> Карты успешно переданы.")
    await safe_callback_answer(callback)


# -------------------------
# ADMIN COMMANDS
# -------------------------

@router.message(
    lambda message: (
        message.text and re.match(r"^/?бот\s+[+-]вип\s+\d+", message.text.strip(), re.IGNORECASE)
    )
)
async def admin_vip_command(message: Message) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    parts = message.text.split()
    try:
        hours = int(parts[2])
        operation = parts[1].lower()
    except (IndexError, ValueError):
        return

    target = message.reply_to_message.from_user if message.reply_to_message else None
    if not target:
        return

    await ensure_user(target)

    async with transaction() as db:
        async with db.execute("SELECT vip_until FROM users WHERE user_id = ?", (target.id,)) as cursor:
            row = await cursor.fetchone()

        old_until = float(row["vip_until"] or 0)
        current = now_ts()
        new_until = (max(current, old_until) + hours * 3600) if operation == "+вип" else max(0, old_until - hours * 3600)

        await db.execute("UPDATE users SET vip_until = ? WHERE user_id = ?", (new_until, target.id))

    await log_action(message.from_user.id, f"{operation} {hours}ч пользователю {target.id}")
    await message.answer(f"✅ VIP изменён: <b>{format_date(new_until) if new_until else 'отсутствует'}</b>")


@router.message(
    lambda message: (
        message.text and re.match(r"^/?бот\s+[+-]коины\s+\d+", message.text.strip(), re.IGNORECASE)
    )
)
async def admin_coins_command(message: Message) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    parts = message.text.split()
    try:
        operation = parts[1].lower()
        amount = int(parts[2])
    except (IndexError, ValueError):
        return

    target = message.reply_to_message.from_user if message.reply_to_message else None
    if not target or amount < 0:
        return

    await ensure_user(target)

    async with transaction() as db:
        if operation == "+коины":
            await db.execute("UPDATE users SET femcoins = femcoins + ? WHERE user_id = ?", (amount, target.id))
        else:
            await db.execute("UPDATE users SET femcoins = MAX(0, femcoins - ?) WHERE user_id = ?", (amount, target.id))

    await log_action(message.from_user.id, f"{operation} {amount} пользователю {target.id}")
    await message.answer("✅ Баланс изменён.")


@router.message(lambda message: command_is(message, "log"))
async def logs_command(message: Message) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    async with db_connection() as db:
        async with db.execute(
            "SELECT admin_id, action, timestamp FROM logs ORDER BY id DESC LIMIT 25"
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await message.answer("Логи пусты.")
        return

    lines = ["📋 <b>Последние действия</b>", ""]
    for row in rows:
        date = format_date(float(row["timestamp"]))
        lines.append(f"[{esc(date)}] <code>{int(row['admin_id'])}</code> {esc(row['action'])}")

    await message.answer("\n".join(lines))


async def cleanup_worker() -> None:
    while True:
        try:
            current_time = now_ts()
            async with transaction() as db:
                await db.execute(
                    "DELETE FROM chests WHERE expires_at <= ? OR claimed_by IS NOT NULL",
                    (current_time - 3600,),
                )
                await db.execute(
                    "DELETE FROM trades WHERE status IN ('completed', 'cancelled') OR updated_at < ?",
                    (current_time - 86400,),
                )
                await db.execute("DELETE FROM admin_codes WHERE expires_at <= ? OR used = 1", (current_time,))
        except Exception:
            logger.exception("Ошибка очистки временных данных")

        await asyncio.sleep(300)


# =====================================================================
# MAIN
# =====================================================================

async def main() -> None:
    await init_database()

    inactivity_task = asyncio.create_task(inactivity_chest_worker())
    cleanup_task = asyncio.create_task(cleanup_worker())

    active_tasks.update({inactivity_task, cleanup_task})

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Бот запущен")
        await dp.start_polling(bot)
    finally:
        for task in active_tasks:
            task.cancel()

        await asyncio.gather(*active_tasks, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
