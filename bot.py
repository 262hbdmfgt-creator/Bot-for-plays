import asyncio
import html
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
from aiogram.utils.keyboard import InlineKeyboardBuilder


# -------------------------
# CONFIG
# -------------------------

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


# -------------------------
# LOGGING
# -------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("fembot")


# -------------------------
# BOT
# -------------------------

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
    ),
)

dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


# -------------------------
# CONSTANTS
# -------------------------

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

ARTIFACT_TYPES = {
    "blade",
    "armor",
    "shield",
}

SHIELD_TYPES = {
    "weak",
    "reinforced",
    "femboy",
}

CASINO_BETS = {10, 20, 50, 100}

# Временные данные интерфейса.
# Денежные операции и награды в дальнейшем будут дополнительно
# защищены транзакциями SQLite.
user_casino_bets: dict[int, int] = {}
active_tasks: set[asyncio.Task] = set()
chat_activity_memory: dict[int, float] = {}
trade_locks: dict[str, asyncio.Lock] = {}
callback_locks: dict[str, asyncio.Lock] = {}


# -------------------------
# FSM
# -------------------------

class AdminStates(StatesGroup):
    waiting_photo = State()
    waiting_card_description = State()
    waiting_admin_code = State()
    waiting_broadcast = State()
    waiting_promo_code = State()
    waiting_admin_text = State()


class UserStates(StatesGroup):
    waiting_promo_code = State()


# -------------------------
# BASIC HELPERS
# -------------------------

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
    """
    Безопасный разбор callback_data.
    Не использует split('_'), поэтому ID с подчёркиваниями поддерживаются.
    """
    if not callback_data.startswith(prefix):
        return None
    return callback_data[len(prefix):]


def make_token(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


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


# -------------------------
# DATABASE CONNECTION
# -------------------------

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

    async with db.execute(
        f"PRAGMA table_info({table_name})"
    ) as cursor:
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
                f"ALTER TABLE {table_name} ADD COLUMN "
                f"{column} {definition}"
            )
            logger.info(
                "Добавлена колонка %s.%s",
                table_name,
                column,
            )
        except Exception:
            logger.exception(
                "Ошибка миграции %s.%s",
                table_name,
                column,
            )


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
                last_chest_at REAL NOT NULL DEFAULT 0
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
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_inventory_user_card
            ON inventory(user_id, card_id)
            """
        )

        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_inventory_user
            ON inventory(user_id)
            """
        )

        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_artifacts_user
            ON artifacts(user_id)
            """
        )

        await db.execute(
            """
            UPDATE users
            SET first_seen = ?
            WHERE first_seen = 0
            """,
            (now_ts(),),
        )

        await db.commit()

    logger.info("База данных инициализирована")


# -------------------------
# TRANSACTION HELPERS
# -------------------------

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
    """
    Создаёт пользователя при необходимости.
    Возвращает True, если пользователь был создан впервые.
    """
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
                """
                UPDATE users
                SET username = ?
                WHERE user_id = ?
                """,
                (username, user_id),
            )
            return False

        await db.execute(
            """
            INSERT INTO users(
                user_id,
                username,
                referred_by,
                first_seen
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                user_id,
                username,
                valid_referrer,
                now_ts(),
            ),
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


async def is_admin(user_id: int) -> bool:
    if user_id == SUPER_ADMIN_ID:
        return True

    async with db_connection() as db:
        async with db.execute(
            "SELECT 1 FROM admins WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            return await cursor.fetchone() is not None


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


async def register_chat(message: Message) -> None:
    if message.chat.type not in {
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    }:
        return

    timestamp = now_ts()

    async with transaction() as db:
        await db.execute(
            """
            INSERT INTO chats(
                chat_id,
                chat_type,
                inactivity_interval,
                last_activity
            )
            VALUES (?, ?, 3600, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                chat_type = excluded.chat_type,
                last_activity = excluded.last_activity
            """,
            (
                message.chat.id,
                message.chat.type,
                timestamp,
            ),
        )

    chat_activity_memory[message.chat.id] = timestamp


# -------------------------
# GLOBAL MIDDLEWARE
# -------------------------

@router.message.outer_middleware()
async def message_registration_middleware(handler, event, data):
    if isinstance(event, Message):
        try:
            await register_chat(event)
        except Exception:
            logger.exception("Ошибка регистрации чата")

    return await handler(event, data)


# -------------------------
# GLOBAL ERROR HANDLER
# -------------------------

@dp.error()
async def global_error_handler(event):
    logger.exception("Глобальная ошибка обработки события: %r", event)
    return True

def bonus_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text="🎁 Забрать бонус",
                url=f"https://t.me/{bot_username}?start=bonus",
            )
        ]
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


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
        result += (
            f"\n💰 VIP-компенсация: "
            f"<b>+{compensation} 🪙</b>"
        )

    return result


async def choose_card(
    db: aiosqlite.Connection,
    weights: dict[str, float],
) -> Optional[aiosqlite.Row]:
    available_rarities: list[str] = []

    async with db.execute(
        "SELECT DISTINCT rarity FROM cards"
    ) as cursor:
        rows = await cursor.fetchall()

    existing = {
        str(row["rarity"])
        for row in rows
    }

    for rarity in weights:
        if rarity in existing:
            available_rarities.append(rarity)

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


async def check_attack_block(
    user_id: int,
    current_time: float,
) -> float:
    async with db_connection() as db:
        async with db.execute(
            """
            SELECT attack_block_until
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        return 0

    return max(0, float(row["attack_block_until"] or 0) - current_time)


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

        if referrer_id <= 0 or referrer_id == new_user_id:
            return None

        if reward_given != 0:
            return None

        async with db.execute(
            "SELECT 1 FROM users WHERE user_id = ?",
            (referrer_id,),
        ) as cursor:
            referrer_exists = await cursor.fetchone()

        if not referrer_exists:
            await db.execute(
                """
                UPDATE users
                SET referral_reward_given = 2
                WHERE user_id = ?
                """,
                (new_user_id,),
            )
            return None

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins + 25
            WHERE user_id = ?
            """,
            (referrer_id,),
        )

        await db.execute(
            """
            UPDATE users
            SET referral_reward_given = 1
            WHERE user_id = ?
            """,
            (new_user_id,),
        )

        return referrer_id


async def get_profile_text(
    user_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    current_time = now_ts()

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT femcoins, vip_until, cd_resets_count,
                   cd_reset_expires, attack_block_until
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

        async with db.execute(
            """
            SELECT COUNT(*) AS total
            FROM cards
            """
        ) as cursor:
            card_total = await cursor.fetchone()

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
        f"💰 Баланс: <b>{int(user['femcoins'])} 🪙</b>",
    ]

    vip_until = float(user["vip_until"] or 0)

    if vip_until > current_time:
        lines.append(
            f"👑 VIP до: <b>{esc(format_date(vip_until))}</b>"
        )

    unique_cards = int(cards["unique_cards"] or 0)
    total_cards = int(cards["total_cards"] or 0)
    total_available = int(card_total["total"] or 0)

    if unique_cards:
        lines.append(
            f"🎴 Собранные карты: "
            f"<b>{unique_cards}/{total_available}</b> "
            f"(копий: {total_cards})"
        )

    skip_count = int(user["cd_resets_count"] or 0)
    skip_expires = float(user["cd_reset_expires"] or 0)

    if skip_count and skip_expires > current_time:
        lines.append(
            f"⚡ Скипы: <b>{skip_count}</b> "
            f"(ещё {short_time(skip_expires - current_time)})"
        )

    blades = 0
    armor = 0
    shields: dict[str, int] = {}

    for row in artifact_rows:
        artifact_type = str(row["artifact_type"])
        subtype = str(row["subtype"] or "")
        amount = int(row["amount"] or 0)

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

    shield_parts = [
        f"{shield_names.get(kind, kind)}: {amount}"
        for kind, amount in shields.items()
        if amount > 0
    ]

    if shield_parts:
        lines.append(
            "🔰 Открытые щиты: <b>"
            + ", ".join(shield_parts)
            + "</b>"
        )

    attack_block_until = float(user["attack_block_until"] or 0)

    if attack_block_until > current_time:
        lines.append(
            "🚫 Запрет сбора карт: "
            f"<b>{short_time(attack_block_until - current_time)}</b>"
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

    text, keyboard = await get_profile_text(
        message.from_user.id,
    )

    await message.answer(
        text,
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("profile_cards:"))
async def profile_cards_callback(
    callback: CallbackQuery,
) -> None:
    payload = parse_callback(
        callback.data or "",
        "profile_cards:",
    )

    if not payload:
        await safe_callback_answer(
            callback,
            "Некорректная кнопка",
            show_alert=True,
        )
        return

    try:
        owner_raw, index_raw = payload.rsplit(":", 1)
        owner_id = int(owner_raw)
        index = int(index_raw)
    except (ValueError, TypeError):
        await safe_callback_answer(
            callback,
            "Некорректные данные",
            show_alert=True,
        )
        return

    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Это не ваш профиль",
            show_alert=True,
        )
        return

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT c.id, c.file_id, c.rarity,
                   c.description, i.count
            FROM inventory AS i
            JOIN cards AS c ON c.id = i.card_id
            WHERE i.user_id = ?
            """,
            (owner_id,),
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await safe_callback_answer(
            callback,
            "У вас пока нет карт",
            show_alert=True,
        )
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
                callback_data=(
                    f"profile_cards:{owner_id}:{index - 1}"
                ),
            )
        )

    if index < len(rows) - 1:
        navigation.append(
            InlineKeyboardButton(
                text="Вперёд ➡️",
                callback_data=(
                    f"profile_cards:{owner_id}:{index + 1}"
                ),
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
                InputMediaPhoto(
                    media=card["file_id"],
                    caption=caption,
                ),
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
                f"Следующая награда через "
                f"<b>{short_time(interval - elapsed)}</b>."
            )
            return

        streak = int(user["daily_streak"] or 0)

        if last_daily and elapsed <= interval + 86400:
            streak += 1
        else:
            streak = 1

        streak = min(
            streak,
            DAILY_REWARDS["maximum_streak"],
        )

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
            (
                streak,
                current_time,
                reward,
                user_id,
            ),
        )

    referrer_id = await process_referral_reward(user_id)

    if referrer_id:
        try:
            await bot.send_message(
                referrer_id,
                "👥 <b>Реферальный бонус!</b>\n"
                f"Игрок <b>{display_name(message.from_user)}</b> "
                "перешёл по вашей ссылке.\n"
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
    link = (
        f"https://t.me/{bot_info.username}"
        f"?start=ref{message.from_user.id}"
    )

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT COUNT(*) AS total
            FROM users
            WHERE referred_by = ?
            """,
            (message.from_user.id,),
        ) as cursor:
            total = await cursor.fetchone()

        async with db.execute(
            """
            SELECT COUNT(*) AS completed
            FROM users
            WHERE referred_by = ?
              AND referral_reward_given = 1
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
                    url=(
                        f"https://t.me/{bot_info.username}"
                        "?startgroup=true"
                    ),
                )
            ]
        ]
    )

    await message.answer(
        "👥 <b>Реферальная система</b>\n\n"
        "Приглашайте друзей по ссылке ниже.\n"
        "За каждого нового пользователя начисляется "
        "<b>+25 🪙</b> сразу после корректного перехода.\n\n"
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

    is_new = await ensure_user(
        message.from_user,
        referrer_id,
    )

    if argument.lower() == "bonus":
        if message.chat.type != ChatType.PRIVATE:
            bot_info = await bot.get_me()

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🎁 Получить бонус в ЛС",
                            url=(
                                f"https://t.me/{bot_info.username}"
                                "?start=bonus"
                            ),
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
            "🎁 <b>Бонус за подписку</b>\n\n"
            f"Подпишитесь на канал {esc(CHANNEL_USERNAME)}, "
            "затем нажмите кнопку проверки.",
            reply_markup=keyboard,
        )
        return

    if argument.lower() == "shop":
        await message.answer(
            "🏪 Магазин будет доступен в следующем разделе.",
        )
        return

    if is_new and referrer_id:
        ref_owner = await process_referral_reward(
            message.from_user.id,
        )

        if ref_owner:
            try:
                await bot.send_message(
                    ref_owner,
                    "👥 <b>Новый реферал!</b>\n"
                    f"Игрок <b>{display_name(message.from_user)}</b> "
                    "перешёл по вашей ссылке.\n"
                    "Вам начислено <b>+25 🪙</b>.",
                )
            except Exception:
                logger.exception(
                    "Ошибка уведомления о реферале"
                )

        await message.answer(
            "🎉 Вы зарегистрированы по реферальной ссылке!\n"
            "Пригласившему начислено <b>+25 🪙</b>.",
        )
        return

    await message.answer(
        "👋 <b>Добро пожаловать!</b>\n\n"
        "🎴 Напишите <b>фем</b>, чтобы получить карту.\n"
        "📅 Напишите <b>дейлик</b>, чтобы забрать награду.\n"
        "👤 Команда <b>профиль</b> покажет вашу коллекцию.",
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
        await safe_callback_answer(
            callback,
            "Не удалось проверить подписку",
            show_alert=True,
        )
        return

    status = str(member.status)

    if status not in {
        "creator",
        "administrator",
        "member",
        "restricted",
    }:
        await safe_callback_answer(
            callback,
            "Сначала подпишитесь на канал",
            show_alert=True,
        )
        return

    async with transaction() as db:
        async with db.execute(
            """
            SELECT channel_bonus_claimed, last_weekly_bonus
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            await safe_callback_answer(
                callback,
                "Профиль не найден",
                show_alert=True,
            )
            return

        last_bonus = float(row["last_weekly_bonus"] or 0)

        if last_bonus and current_time - last_bonus < 7 * 86400:
            await safe_callback_answer(
                callback,
                "Еженедельный бонус ещё недоступен",
                show_alert=True,
            )
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
            (
                current_time,
                current_time + 3 * 3600,
                user_id,
            ),
        )

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins + 50
            WHERE user_id = ?
            """,
            (user_id,),
        )

    await safe_callback_answer(
        callback,
        "Начислено +50 🪙 и выдан скип КД",
        show_alert=True,
    )

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


async def resolve_transfer_target(
    message: Message,
) -> Optional[int]:
    if (
        message.reply_to_message
        and message.reply_to_message.from_user
    ):
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
            "Пример: <code>передать 100</code> ответом "
            "на сообщение пользователя."
        )
        return

    target_id = await resolve_transfer_target(message)

    if not target_id:
        await message.answer(
            "❗ Укажите получателя ответом на сообщение "
            "или через @username."
        )
        return

    sender_id = message.from_user.id

    if sender_id == target_id:
        await message.answer("❌ Нельзя переводить монеты самому себе.")
        return

    if target_id == bot.id:
        await message.answer("❌ Нельзя переводить монеты боту.")
        return

    if not await user_exists(target_id):
        await message.answer("❌ Получатель ещё не зарегистрирован.")
        return

    vip = await is_vip(sender_id)
    commission_percent = 5 if vip else 10
    commission = max(
        1,
        amount * commission_percent // 100,
    )
    received = amount - commission

    if received <= 0:
        await message.answer("❌ Сумма слишком мала.")
        return

    async with transaction() as db:
        async with db.execute(
            """
            SELECT femcoins
            FROM users
            WHERE user_id = ?
            """,
            (sender_id,),
        ) as cursor:
            sender = await cursor.fetchone()

        if not sender or int(sender["femcoins"]) < amount:
            balance = int(sender["femcoins"]) if sender else 0
            await message.answer(
                f"❌ Недостаточно монет.\n"
                f"Ваш баланс: <b>{balance} 🪙</b>"
            )
            return

        updated = await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins - ?
            WHERE user_id = ?
              AND femcoins >= ?
            """,
            (amount, sender_id, amount),
        )

        if updated.rowcount != 1:
            await message.answer("❌ Баланс изменился. Повторите перевод.")
            return

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins + ?
            WHERE user_id = ?
            """,
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
        f"🔥 Комиссия: <b>{commission} 🪙</b> "
        f"({commission_percent}%)"
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
                        url=(
                            f"https://t.me/{bot_info.username}"
                            "?start=shop"
                        ),
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


async def render_shop(
    callback: CallbackQuery,
) -> None:
    async with db_connection() as db:
        async with db.execute(
            "SELECT femcoins FROM users WHERE user_id = ?",
            (callback.from_user.id,),
        ) as cursor:
            row = await cursor.fetchone()

    balance = int(row["femcoins"]) if row else 0

    await safe_edit_text(
        callback.message,
        "🏪 <b>Магазин</b>\n\n"
        f"Ваш баланс: <b>{balance} 🪙</b>\n\n"
        "Выберите товар:",
        shop_keyboard(),
    )


@router.callback_query(F.data == "shop:main")
async def shop_main_callback(callback: CallbackQuery) -> None:
    await render_shop(callback)
    await safe_callback_answer(callback)


@router.callback_query(F.data == "shop:cd")
async def shop_cd_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    current_time = now_ts()

    async with transaction() as db:
        async with db.execute(
            """
            SELECT femcoins, cd_resets_count,
                   cd_reset_expires
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row or int(row["femcoins"]) < 150:
            await safe_callback_answer(
                callback,
                "Недостаточно монет",
                show_alert=True,
            )
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
            WHERE user_id = ?
              AND femcoins >= 150
            """,
            (
                old_count + 1,
                current_time + 3 * 3600,
                user_id,
            ),
        )

    await safe_callback_answer(
        callback,
        f"Скипов КД: {old_count + 1}",
        show_alert=True,
    )


@router.callback_query(F.data == "shop:luck")
async def shop_luck_callback(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    current_time = now_ts()

    async with transaction() as db:
        async with db.execute(
            """
            SELECT femcoins, luck_expires
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row or int(row["femcoins"]) < 250:
            await safe_callback_answer(
                callback,
                "Недостаточно монет",
                show_alert=True,
            )
            return

        old_expires = float(row["luck_expires"] or 0)
        start = max(current_time, old_expires)

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins - 250,
                luck_expires = ?
            WHERE user_id = ?
              AND femcoins >= 250
            """,
            (start + 3 * 3600, user_id),
        )

    await safe_callback_answer(
        callback,
        "Эликсир удачи активирован на 3 часа",
        show_alert=True,
    )


@router.callback_query(F.data == "shop:newcard")
async def shop_new_card_callback(
    callback: CallbackQuery,
) -> None:
    user_id = callback.from_user.id

    async with transaction() as db:
        async with db.execute(
            """
            SELECT femcoins
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        if not user or int(user["femcoins"]) < 400:
            await safe_callback_answer(
                callback,
                "Недостаточно монет",
                show_alert=True,
            )
            return

        async with db.execute(
            """
            SELECT id, file_id, rarity, description
            FROM cards
            WHERE id NOT IN (
                SELECT card_id
                FROM inventory
                WHERE user_id = ?
            )
            ORDER BY RANDOM()
            LIMIT 1
            """,
            (user_id,),
        ) as cursor:
            card = await cursor.fetchone()

        if not card:
            await safe_callback_answer(
                callback,
                "У вас уже собраны все карты",
                show_alert=True,
            )
            return

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins - 400
            WHERE user_id = ?
              AND femcoins >= 400
            """,
            (user_id,),
        )

        await db.execute(
            """
            INSERT INTO inventory(user_id, card_id, count)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, card_id)
            DO UPDATE SET count = count + 1
            """,
            (user_id, int(card["id"])),
        )

    caption = (
        f"🎴 <b>Новая карта #{int(card['id'])}!</b>\n\n"
        f"🏷 Редкость: <b>{esc(card['rarity'])}</b>\n"
        f"📝 {esc(card['description']) or 'Без описания'}"
    )

    await callback.message.answer_photo(
        card["file_id"],
        caption=caption,
    )

    await safe_callback_answer(callback, "Карта куплена")


@router.callback_query(F.data == "shop:artifacts")
async def shop_artifacts_callback(
    callback: CallbackQuery,
) -> None:
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
        "🛡 <b>Артефакты</b>\n\n"
        "Усиленный и фембойский щиты напрямую не продаются.",
        keyboard,
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "artifact_shop:weak")
async def buy_weak_shield_callback(
    callback: CallbackQuery,
) -> None:
    user_id = callback.from_user.id

    async with transaction() as db:
        async with db.execute(
            """
            SELECT femcoins
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        if not user or int(user["femcoins"]) < 30:
            await safe_callback_answer(
                callback,
                "Недостаточно монет",
                show_alert=True,
            )
            return

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins - 30
            WHERE user_id = ?
              AND femcoins >= 30
            """,
            (user_id,),
        )

        await db.execute(
            """
            INSERT INTO artifacts(
                user_id, artifact_type, subtype,
                amount, created_at
            )
            VALUES (?, 'shield', 'weak', 1, ?)
            ON CONFLICT(user_id, artifact_type, subtype)
            DO UPDATE SET amount = amount + 1
            """,
            (user_id, now_ts()),
        )

    await safe_callback_answer(
        callback,
        "Слабый щит куплен",
        show_alert=True,
    )


@router.callback_query(F.data == "artifact_shop:armor")
async def buy_armor_callback(
    callback: CallbackQuery,
) -> None:
    user_id = callback.from_user.id

    async with transaction() as db:
        async with db.execute(
            """
            SELECT femcoins
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        if not user or int(user["femcoins"]) < 300:
            await safe_callback_answer(
                callback,
                "Недостаточно монет",
                show_alert=True,
            )
            return

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins - 300
            WHERE user_id = ?
              AND femcoins >= 300
            """,
            (user_id,),
        )

        await db.execute(
            """
            INSERT INTO artifacts(
                user_id, artifact_type, subtype,
                amount, created_at
            )
            VALUES (?, 'armor', '', 1, ?)
            ON CONFLICT(user_id, artifact_type, subtype)
            DO UPDATE SET amount = amount + 1
            """,
            (user_id, now_ts()),
        )

    await safe_callback_answer(
        callback,
        "Броня куплена",
        show_alert=True,
    )


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
    await safe_edit_text(
        callback.message,
        VIP_TEXT,
        vip_keyboard(),
    )
    await safe_callback_answer(callback)


@router.message(
    lambda message: command_is(
        message,
        "vip",
        "вип",
        "купить вип",
    )
)
async def vip_command(message: Message) -> None:
    await ensure_user(message.from_user)

    await message.answer(
        VIP_TEXT,
        reply_markup=vip_keyboard(),
    )


@router.callback_query(F.data == "vip:coins")
async def vip_coins_menu_callback(
    callback: CallbackQuery,
) -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="1 день — 1500 🪙",
                    callback_data="vip_coins:1",
                )
            ],
            [
                InlineKeyboardButton(
                    text="7 дней — 4500 🪙",
                    callback_data="vip_coins:7",
                )
            ],
            [
                InlineKeyboardButton(
                    text="30 дней — 10000 🪙",
                    callback_data="vip_coins:30",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="shop:vip",
                )
            ],
        ]
    )

    await safe_edit_text(
        callback.message,
        VIP_TEXT + "\n\nВыберите срок:",
        keyboard,
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("vip_coins:"))
async def buy_vip_coins_callback(
    callback: CallbackQuery,
) -> None:
    raw_days = parse_callback(
        callback.data or "",
        "vip_coins:",
    )

    try:
        days = int(raw_days or "0")
    except ValueError:
        await safe_callback_answer(
            callback,
            "Некорректный срок",
            show_alert=True,
        )
        return

    prices = {
        1: 1500,
        7: 4500,
        30: 10000,
    }

    if days not in prices:
        await safe_callback_answer(
            callback,
            "Некорректный срок",
            show_alert=True,
        )
        return

    user_id = callback.from_user.id
    current_time = now_ts()

    async with transaction() as db:
        async with db.execute(
            """
            SELECT femcoins, vip_until
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        if not user or int(user["femcoins"]) < prices[days]:
            await safe_callback_answer(
                callback,
                "Недостаточно фемкоинов",
                show_alert=True,
            )
            return

        vip_until = max(
            current_time,
            float(user["vip_until"] or 0),
        )

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins - ?,
                vip_until = ?
            WHERE user_id = ?
              AND femcoins >= ?
            """,
            (
                prices[days],
                vip_until + days * 86400,
                user_id,
                prices[days],
            ),
        )

    await safe_callback_answer(
        callback,
        f"VIP активирован на {days} дн.",
        show_alert=True,
    )


@router.callback_query(F.data == "vip:stars")
async def vip_stars_menu_callback(
    callback: CallbackQuery,
) -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="1 день — 250 ⭐",
                    callback_data="vip_stars:1",
                )
            ],
            [
                InlineKeyboardButton(
                    text="7 дней — 800 ⭐",
                    callback_data="vip_stars:7",
                )
            ],
            [
                InlineKeyboardButton(
                    text="30 дней — 2000 ⭐",
                    callback_data="vip_stars:30",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="shop:vip",
                )
            ],
        ]
    )

    await safe_edit_text(
        callback.message,
        VIP_TEXT + "\n\nВыберите срок:",
        keyboard,
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("vip_stars:"))
async def vip_stars_invoice_callback(
    callback: CallbackQuery,
) -> None:
    raw_days = parse_callback(
        callback.data or "",
        "vip_stars:",
    )

    try:
        days = int(raw_days or "0")
    except ValueError:
        await safe_callback_answer(
            callback,
            "Некорректный срок",
            show_alert=True,
        )
        return

    prices = {
        1: 250,
        7: 800,
        30: 2000,
    }

    if days not in prices:
        await safe_callback_answer(
            callback,
            "Некорректный срок",
            show_alert=True,
        )
        return

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"VIP на {days} дн.",
        description=f"VIP-статус на {days} дней",
        payload=f"vip:{days}",
        currency="XTR",
        prices=[
            LabeledPrice(
                label=f"VIP на {days} дн.",
                amount=prices[days],
            )
        ],
        provider_token="",
    )

    await safe_callback_answer(callback)


@router.callback_query(F.data == "shop:coins")
async def shop_coins_callback(
    callback: CallbackQuery,
) -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="100 монет — 15 ⭐",
                    callback_data="coins_stars:100",
                )
            ],
            [
                InlineKeyboardButton(
                    text="500 монет — 70 ⭐",
                    callback_data="coins_stars:500",
                )
            ],
            [
                InlineKeyboardButton(
                    text="1000 монет — 135 ⭐",
                    callback_data="coins_stars:1000",
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
        "💰 <b>Покупка фемкоинов</b>",
        keyboard,
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("coins_stars:"))
async def coins_stars_invoice_callback(
    callback: CallbackQuery,
) -> None:
    raw_amount = parse_callback(
        callback.data or "",
        "coins_stars:",
    )

    try:
        amount = int(raw_amount or "0")
    except ValueError:
        await safe_callback_answer(
            callback,
            "Некорректная сумма",
            show_alert=True,
        )
        return

    prices = {
        100: 15,
        500: 70,
        1000: 135,
    }

    if amount not in prices:
        await safe_callback_answer(
            callback,
            "Некорректная сумма",
            show_alert=True,
        )
        return

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"{amount} фемкоинов",
        description=f"Покупка {amount} игровых монет",
        payload=f"coins:{amount}",
        currency="XTR",
        prices=[
            LabeledPrice(
                label=f"{amount} фемкоинов",
                amount=prices[amount],
            )
        ],
        provider_token="",
    )

    await safe_callback_answer(callback)


@router.pre_checkout_query()
async def pre_checkout_handler(
    query: PreCheckoutQuery,
) -> None:
    try:
        await query.answer(ok=True)
    except Exception:
        logger.exception("Ошибка pre-checkout")


@router.message(F.successful_payment)
async def successful_payment_handler(
    message: Message,
) -> None:
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

        if amount not in {100, 500, 1000}:
            return

        async with transaction() as db:
            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins + ?
                WHERE user_id = ?
                """,
                (amount, user_id),
            )

        await message.answer(
            f"⭐ Оплата прошла успешно.\n"
            f"Начислено: <b>+{amount} 🪙</b>"
        )
        return

    if payload.startswith("vip:"):
        try:
            days = int(payload.split(":", 1)[1])
        except ValueError:
            return

        if days not in {1, 7, 30}:
            return

        current_time = now_ts()

        async with transaction() as db:
            async with db.execute(
                """
                SELECT vip_until
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()

            old_vip = float(row["vip_until"] or 0) if row else 0
            new_vip = max(current_time, old_vip) + days * 86400

            await db.execute(
                """
                UPDATE users
                SET vip_until = ?
                WHERE user_id = ?
                """,
                (new_vip, user_id),
            )

        await message.answer(
            "⭐ Оплата прошла успешно.\n"
            f"VIP активирован на <b>{days} дн.</b>"
        )


@router.message(
    lambda message: command_is(
        message,
        "промо",
        "promo",
    )
)
async def promo_command(message: Message, state: FSMContext) -> None:
    await ensure_user(message.from_user)

    await state.set_state(UserStates.waiting_promo_code)

    await message.answer(
        "🔑 Отправьте промокод отдельным текстовым сообщением."
    )


@router.message(UserStates.waiting_promo_code)
async def promo_code_handler(
    message: Message,
    state: FSMContext,
) -> None:
    if not message.text:
        await message.answer("❌ Промокод должен быть текстом.")
        return

    code = message.text.strip().upper()
    user_id = message.from_user.id
    current_time = now_ts()

    async with transaction() as db:
        async with db.execute(
            """
            SELECT p_type, reward_type, reward_val,
                   expires_at, is_used
            FROM promo_codes
            WHERE UPPER(code) = ?
            """,
            (code,),
        ) as cursor:
            promo = await cursor.fetchone()

        if not promo:
            await state.clear()
            await message.answer("❌ Промокод не найден.")
            return

        if float(promo["expires_at"]) <= current_time:
            await state.clear()
            await message.answer("⌛ Срок действия промокода истёк.")
            return

        if (
            str(promo["p_type"]).upper() == "L"
            and int(promo["is_used"] or 0) == 1
        ):
            await state.clear()
            await message.answer("❌ Промокод уже использован.")
            return

        async with db.execute(
            """
            SELECT 1
            FROM promo_claims
            WHERE user_id = ? AND code = ?
            """,
            (user_id, code),
        ) as cursor:
            already_claimed = await cursor.fetchone()

        if already_claimed:
            await state.clear()
            await message.answer("❌ Вы уже использовали этот промокод.")
            return

        reward_type = str(promo["reward_type"]).lower()
        reward_value = int(promo["reward_val"] or 0)

        if reward_type == "f":
            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins + ?
                WHERE user_id = ?
                """,
                (reward_value, user_id),
            )
            reward_text = f"+{reward_value} 🪙"

        elif reward_type == "k":
            await db.execute(
                """
                UPDATE users
                SET cd_resets_count = cd_resets_count + 1,
                    cd_reset_expires = ?
                WHERE user_id = ?
                """,
                (
                    current_time + 3 * 3600,
                    user_id,
                ),
            )
            reward_text = "скип кулдауна"

        elif reward_type == "v":
            async with db.execute(
                """
                SELECT vip_until
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ) as cursor:
                user = await cursor.fetchone()

            old_vip = float(user["vip_until"] or 0) if user else 0
            new_vip = max(current_time, old_vip) + reward_value * 3600

            await db.execute(
                """
                UPDATE users
                SET vip_until = ?
                WHERE user_id = ?
                """,
                (new_vip, user_id),
            )
            reward_text = f"VIP на {reward_value} ч"

        else:
            await state.clear()
            await message.answer("❌ Неизвестный тип награды.")
            return

        await db.execute(
            """
            INSERT INTO promo_claims(user_id, code, claimed_at)
            VALUES (?, ?, ?)
            """,
            (user_id, code, current_time),
        )

        if str(promo["p_type"]).upper() == "L":
            await db.execute(
                """
                UPDATE promo_codes
                SET is_used = 1
                WHERE code = ?
                """,
                (code,),
            )

    await state.clear()

    await message.answer(
        "🎉 <b>Промокод активирован!</b>\n"
        f"Награда: <b>{esc(reward_text)}</b>"
    )


def casino_keyboard(bet: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅ ' if bet == value else ''}{value} 🪙",
                callback_data=f"casino_bet:{value}",
            )
            for value in sorted(CASINO_BETS)
        ],
        [
            InlineKeyboardButton(
                text=f"🎲 Кости — {bet} 🪙",
                callback_data="casino: dice",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"🔴 Красное x2 — {bet}",
                callback_data="casino:red",
            ),
            InlineKeyboardButton(
                text=f"⚫ Чёрное x2 — {bet}",
                callback_data="casino:black",
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"🟢 Зеро x14 — {bet}",
                callback_data="casino:zero",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"🎰 Слоты — {bet} 🪙",
                callback_data="casino:slots",
            )
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=rows)


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
async def casino_bet_callback(
    callback: CallbackQuery,
) -> None:
    raw_bet = parse_callback(
        callback.data or "",
        "casino_bet:",
    )

    try:
        bet = int(raw_bet or "0")
    except ValueError:
        await safe_callback_answer(
            callback,
            "Некорректная ставка",
            show_alert=True,
        )
        return

    if bet not in CASINO_BETS:
        await safe_callback_answer(
            callback,
            "Некорректная ставка",
            show_alert=True,
        )
        return

    user_casino_bets[callback.from_user.id] = bet

    await safe_edit_text(
        callback.message,
        "🎰 <b>Фем-казино</b>\n\n"
        f"Текущая ставка: <b>{bet} 🪙</b>",
        casino_keyboard(bet),
    )

    await safe_callback_answer(
        callback,
        f"Ставка: {bet} 🪙",
    )


@router.callback_query(F.data.startswith("casino:"))
async def casino_game_callback(
    callback: CallbackQuery,
) -> None:
    action = parse_callback(
        callback.data or "",
        "casino:",
    )

    if not action:
        await safe_callback_answer(
            callback,
            "Некорректная игра",
            show_alert=True,
        )
        return

    action = action.strip()
    user_id = callback.from_user.id
    bet = user_casino_bets.get(user_id, 20)

    if action in {"red", "black", "zero"} and bet < 20:
        await safe_callback_answer(
            callback,
            "Для рулетки нужна ставка минимум 20",
            show_alert=True,
        )
        return

    async with transaction() as db:
        async with db.execute(
            """
            SELECT femcoins
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        if not user or int(user["femcoins"]) < bet:
            await safe_callback_answer(
                callback,
                "Недостаточно монет",
                show_alert=True,
            )
            return

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins - ?
            WHERE user_id = ?
              AND femcoins >= ?
            """,
            (bet, user_id, bet),
        )

        result_text = ""

        if action == "dice":
            player = random.randint(1, 6)
            dealer = random.randint(1, 6)

            if player > dealer:
                net = bet
                await db.execute(
                    """
                    UPDATE users
                    SET femcoins = femcoins + ?
                    WHERE user_id = ?
                    """,
                    (net, user_id),
                )
                result_text = (
                    f"🎲 Вы выиграли: "
                    f"{player} против {dealer}.\n"
                    f"Прибыль: <b>+{net} 🪙</b>"
                )
            elif player < dealer:
                result_text = (
                    f"🎲 Вы проиграли: "
                    f"{player} против {dealer}.\n"
                    f"Потеря: <b>-{bet} 🪙</b>"
                )
            else:
                await db.execute(
                    """
                    UPDATE users
                    SET femcoins = femcoins + ?
                    WHERE user_id = ?
                    """,
                    (bet, user_id),
                )
                result_text = (
                    f"🎲 Ничья: {player} и {dealer}.\n"
                    "Ставка возвращена."
                )

        elif action in {"red", "black", "zero"}:
            number = random.randint(0, 36)

            if number == 0:
                color = "zero"
            elif number % 2:
                color = "red"
            else:
                color = "black"

            if action == color:
                multiplier = 14 if action == "zero" else 2
                net = bet * multiplier - bet

                await db.execute(
                    """
                    UPDATE users
                    SET femcoins = femcoins + ?
                    WHERE user_id = ?
                    """,
                    (bet + net, user_id),
                )

                result_text = (
                    f"🎡 Выпало число <b>{number}</b>.\n"
                    f"Победа! Прибыль: <b>+{net} 🪙</b>"
                )
            else:
                result_text = (
                    f"🎡 Выпало число <b>{number}</b>.\n"
                    f"Потеря: <b>-{bet} 🪙</b>"
                )

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

                await db.execute(
                    """
                    UPDATE users
                    SET femcoins = femcoins + ?
                    WHERE user_id = ?
                    """,
                    (total_return, user_id),
                )

                result_text = (
                    f"🎰 {' | '.join(result)}\n"
                    f"Победа! Прибыль: <b>+{net} 🪙</b>"
                )
            else:
                result_text = (
                    f"🎰 {' | '.join(result)}\n"
                    f"Потеря: <b>-{bet} 🪙</b>"
                )

        else:
            await db.rollback()
            await safe_callback_answer(
                callback,
                "Неизвестная игра",
                show_alert=True,
            )
            return

    await callback.message.answer(result_text)
    await safe_callback_answer(callback)

import json


user_fem_locks: dict[int, asyncio.Lock] = {}


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
            INSERT INTO chests(
                chest_id,
                chat_id,
                chest_type,
                payload,
                created_at,
                expires_at
            )
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
        return "legendary", {
            "coins": random.randint(100, 199),
        }

    if roll < 0.05:
        return "rare", {
            "coins": random.randint(50, 99),
        }

    return "normal", {
        "coins": random.randint(5, 49),
    }


async def send_group_chest(chat_id: int) -> None:
    chest_type, payload = await choose_group_chest()
    chest_id = await create_chest(
        chat_id,
        chest_type,
        payload,
    )

    if chest_type == "normal":
        text = (
            "📦 <b>Появился обычный сундук!</b>\n"
            "Кто первым нажмёт кнопку, получит награду."
        )
    elif chest_type == "rare":
        text = (
            "💎 <b>Появился редкий сундук!</b>\n"
            "Внутри спрятана ценная награда."
        )
    elif chest_type == "legendary":
        text = (
            "🌟 <b>Появился легендарный сундук!</b>\n"
            "Внутри монеты и независимый шанс получить клинок."
        )
    else:
        text = (
            "🩷 <b>Появился фембойский артефактный сундук!</b>\n"
            "Внутри находятся редкие предметы."
        )

    try:
        sent = await bot.send_message(
            chat_id,
            text,
            reply_markup=chest_keyboard(chest_id),
        )

        async with transaction() as db:
            await db.execute(
                """
                UPDATE chests
                SET message_id = ?
                WHERE chest_id = ?
                """,
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

    if chest_type == "normal":
        coins = int(payload.get("coins", 5))
        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins + ?
            WHERE user_id = ?
            """,
            (coins, user_id),
        )
        rewards.append(f"+{coins} 🪙")

    elif chest_type == "rare":
        coins = int(payload.get("coins", 50))
        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins + ?
            WHERE user_id = ?
            """,
            (coins, user_id),
        )
        rewards.append(f"+{coins} 🪙")

    elif chest_type == "legendary":
        coins = int(payload.get("coins", 100))

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins + ?
            WHERE user_id = ?
            """,
            (coins, user_id),
        )
        rewards.append(f"+{coins} 🪙")

        if random.random() < 0.05:
            await db.execute(
                """
                INSERT INTO artifacts(
                    user_id,
                    artifact_type,
                    subtype,
                    amount,
                    created_at
                )
                VALUES (?, 'blade', '', 1, ?)
                ON CONFLICT(user_id, artifact_type, subtype)
                DO UPDATE SET amount = amount + 1
                """,
                (user_id, now_ts()),
            )
            rewards.append("+1 клинок")

    elif chest_type == "artifact":
        roll = random.random()

        if roll < 0.50:
            coins = random.randint(200, 299)

            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins + ?
                WHERE user_id = ?
                """,
                (coins, user_id),
            )

            await db.execute(
                """
                INSERT INTO artifacts(
                    user_id,
                    artifact_type,
                    subtype,
                    amount,
                    created_at
                )
                VALUES (?, 'blade', '', 1, ?)
                ON CONFLICT(user_id, artifact_type, subtype)
                DO UPDATE SET amount = amount + 1
                """,
                (user_id, now_ts()),
            )

            rewards.extend([f"+{coins} 🪙", "+1 клинок"])

        elif roll < 0.75:
            coins = random.randint(300, 349)

            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins + ?,
                    cd_resets_count = cd_resets_count + 1,
                    cd_reset_expires = ?
                WHERE user_id = ?
                """,
                (
                    coins,
                    now_ts() + 3 * 3600,
                    user_id,
                ),
            )

            await db.execute(
                """
                INSERT INTO artifacts(
                    user_id,
                    artifact_type,
                    subtype,
                    amount,
                    created_at
                )
                VALUES (?, 'blade', '', 1, ?)
                ON CONFLICT(user_id, artifact_type, subtype)
                DO UPDATE SET amount = amount + 1
                """,
                (user_id, now_ts()),
            )

            rewards.extend(
                [
                    f"+{coins} 🪙",
                    "+1 клинок",
                    "+1 скип КД",
                ]
            )

        elif roll < 0.92:
            coins = random.randint(300, 349)

            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins + ?,
                    cd_resets_count = cd_resets_count + 1,
                    cd_reset_expires = ?
                WHERE user_id = ?
                """,
                (
                    coins,
                    now_ts() + 3 * 3600,
                    user_id,
                ),
            )

            await db.execute(
                """
                INSERT INTO artifacts(
                    user_id,
                    artifact_type,
                    subtype,
                    amount,
                    created_at
                )
                VALUES (?, 'blade', '', 1, ?)
                ON CONFLICT(user_id, artifact_type, subtype)
                DO UPDATE SET amount = amount + 1
                """,
                (user_id, now_ts()),
            )

            await db.execute(
                """
                INSERT INTO artifacts(
                    user_id,
                    artifact_type,
                    subtype,
                    amount,
                    created_at
                )
                VALUES (?, 'shield', 'weak', 1, ?)
                ON CONFLICT(user_id, artifact_type, subtype)
                DO UPDATE SET amount = amount + 1
                """,
                (user_id, now_ts()),
            )

            rewards.extend(
                [
                    f"+{coins} 🪙",
                    "+1 клинок",
                    "+1 слабый щит",
                    "+1 скип КД",
                ]
            )

        else:
            await db.execute(
                """
                INSERT INTO artifacts(
                    user_id,
                    artifact_type,
                    subtype,
                    amount,
                    created_at
                )
                VALUES (?, 'blade', '', 2, ?)
                ON CONFLICT(user_id, artifact_type, subtype)
                DO UPDATE SET amount = amount + 2
                """,
                (user_id, now_ts()),
            )

            await db.execute(
                """
                INSERT INTO artifacts(
                    user_id,
                    artifact_type,
                    subtype,
                    amount,
                    created_at
                )
                VALUES (?, 'shield', 'weak', 2, ?)
                ON CONFLICT(user_id, artifact_type, subtype)
                DO UPDATE SET amount = amount + 2
                """,
                (user_id, now_ts()),
            )

            await db.execute(
                """
                UPDATE users
                SET cd_resets_count = cd_resets_count + 1,
                    cd_reset_expires = ?
                WHERE user_id = ?
                """,
                (
                    now_ts() + 3 * 3600,
                    user_id,
                ),
            )

            rewards.extend(
                [
                    "+2 клинка",
                    "+2 слабых щита",
                    "+1 сброс КД",
                ]
            )

    return ", ".join(rewards)


@router.callback_query(F.data.startswith("chest:"))
async def chest_claim_callback(
    callback: CallbackQuery,
) -> None:
    chest_id = parse_callback(
        callback.data or "",
        "chest:",
    )

    if not chest_id:
        await safe_callback_answer(
            callback,
            "Некорректный сундук",
            show_alert=True,
        )
        return

    user_id = callback.from_user.id

    async with transaction() as db:
        async with db.execute(
            """
            SELECT chest_type, payload, claimed_by,
                   expires_at
            FROM chests
            WHERE chest_id = ?
            """,
            (chest_id,),
        ) as cursor:
            chest = await cursor.fetchone()

        if not chest:
            await safe_callback_answer(
                callback,
                "Сундук не найден или уже удалён",
                show_alert=True,
            )
            return

        if chest["claimed_by"] is not None:
            await safe_callback_answer(
                callback,
                "Сундук уже забрали",
                show_alert=True,
            )
            return

        if float(chest["expires_at"]) <= now_ts():
            await safe_callback_answer(
                callback,
                "Срок действия сундука истёк",
                show_alert=True,
            )
            return

        claimed = await db.execute(
            """
            UPDATE chests
            SET claimed_by = ?,
                claimed_at = ?
            WHERE chest_id = ?
              AND claimed_by IS NULL
              AND expires_at > ?
            """,
            (
                user_id,
                now_ts(),
                chest_id,
                now_ts(),
            ),
        )

        if claimed.rowcount != 1:
            await safe_callback_answer(
                callback,
                "Сундук уже забрали",
                show_alert=True,
            )
            return

        try:
            payload = json.loads(chest["payload"] or "{}")
        except json.JSONDecodeError:
            payload = {}

        reward_text = await grant_chest_reward(
            db,
            user_id,
            str(chest["chest_type"]),
            payload,
        )

    try:
        await callback.message.edit_text(
            "🎁 <b>Сундук забран!</b>\n\n"
            f"Игрок: <b>{display_name(callback.from_user)}</b>\n"
            f"Награда: <b>{esc(reward_text)}</b>"
        )
    except Exception:
        logger.exception("Ошибка изменения сообщения сундука")

    await safe_callback_answer(
        callback,
        "Награда зачислена",
    )


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
                SELECT last_fem_time,
                       custom_cd_hours,
                       custom_cd_expires,
                       cd_resets_count,
                       cd_reset_expires,
                       luck_expires,
                       vip_until,
                       attack_block_until
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ) as cursor:
                user = await cursor.fetchone()

            if not user:
                await message.answer("Профиль не найден.")
                return

            attack_until = float(
                user["attack_block_until"] or 0
            )

            if attack_until > current_time:
                await message.answer(
                    "🚫 Сбор карт временно запрещён.\n"
                    f"Осталось: "
                    f"<b>{short_time(attack_until - current_time)}</b>"
                )
                return

            last_fem = float(user["last_fem_time"] or 0)
            cooldown = await get_effective_fem_cooldown(
                db,
                user_id,
                current_time,
            )

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
                        SET cd_resets_count = ?,
                            cd_reset_expires = ?
                        WHERE user_id = ?
                        """,
                        (
                            skips - 1,
                            skip_expires if skips > 1 else 0,
                            user_id,
                        ),
                    )
                else:
                    await message.answer(
                        "⏳ Рано.\n"
                        f"До следующей попытки: "
                        f"<b>{short_time(cooldown - elapsed)}</b>"
                    )
                    return

            await db.execute(
                """
                UPDATE users
                SET last_fem_time = ?
                WHERE user_id = ?
                """,
                (current_time, user_id),
            )

            vip = float(user["vip_until"] or 0) > current_time
            lucky = float(user["luck_expires"] or 0) > current_time

        first_card = await draw_card(
            user_id,
            lucky=lucky,
            vip=vip,
        )

        if not first_card:
            await message.answer(
                "😔 В базе пока нет доступных карт."
            )
            return

        keyboard = await post_card_keyboard()

        caption = format_card_caption(
            int(first_card["id"]),
            str(first_card["rarity"]),
            str(first_card["description"]),
            bool(first_card["duplicate"]),
            int(first_card["compensation"]),
        )

        await message.answer_photo(
            first_card["file_id"],
            caption=caption,
            reply_markup=keyboard,
        )

        if (
            vip
            and random.random() < 0.20
        ):
            second_card = await draw_card(
                user_id,
                lucky=lucky,
                vip=vip,
                force_duplicate=True,
            )

            if second_card:
                second_caption = format_card_caption(
                    int(second_card["id"]),
                    str(second_card["rarity"]),
                    str(second_card["description"]),
                    True,
                    int(second_card["compensation"]),
                    prefix="👑 VIP-бонус: вторая карта",
                )

                await message.answer_photo(
                    second_card["file_id"],
                    caption=second_caption,
                    reply_markup=keyboard,
                )

        if message.chat.type in {
            ChatType.GROUP,
            ChatType.SUPERGROUP,
        }:
            if random.random() < 0.10:
                await send_group_chest(message.chat.id)


@router.my_chat_member()
async def bot_membership_handler(
    event: types.ChatMemberUpdated,
) -> None:
    chat = event.chat

    if chat.type not in {
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    }:
        return

    old_status = str(event.old_chat_member.status)
    new_status = str(event.new_chat_member.status)

    if new_status not in {"member", "administrator"}:
        return

    if old_status not in {"left", "kicked"}:
        return

    added_by = event.from_user

    if not added_by:
        return

    try:
        member_count = await bot.get_chat_member_count(chat.id)
    except Exception:
        logger.exception("Не удалось подтвердить размер группы")
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
            """
            SELECT reward_claimed
            FROM group_rewards
            WHERE chat_id = ?
            """,
            (chat.id,),
        ) as cursor:
            previous = await cursor.fetchone()

        if previous and int(previous["reward_claimed"]) == 1:
            return

        async with db.execute(
            """
            SELECT count
            FROM owner_group_counts
            WHERE owner_id = ?
            """,
            (user_id,),
        ) as cursor:
            owner_row = await cursor.fetchone()

        owner_count = int(owner_row["count"]) if owner_row else 0

        if owner_count >= 3:
            await db.execute(
                """
                INSERT INTO group_rewards(
                    chat_id,
                    owner_id,
                    reward_claimed,
                    member_count,
                    rewarded_at
                )
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    reward_claimed = 1
                """,
                (
                    chat.id,
                    user_id,
                    member_count,
                    now_ts(),
                ),
            )
            return

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins + ?
            WHERE user_id = ?
            """,
            (reward, user_id),
        )

        await db.execute(
            """
            INSERT INTO owner_group_counts(owner_id, count)
            VALUES (?, 1)
            ON CONFLICT(owner_id)
            DO UPDATE SET count = count + 1
            """,
            (user_id,),
        )

        await db.execute(
            """
            INSERT INTO group_rewards(
                chat_id,
                owner_id,
                reward_claimed,
                member_count,
                rewarded_at
            )
            VALUES (?, ?, 1, ?, ?)
            """,
            (
                chat.id,
                user_id,
                member_count,
                now_ts(),
            ),
        )

    try:
        await bot.send_message(
            chat.id,
            "🎉 <b>Спасибо за добавление бота!</b>\n"
            f"Пользователю <b>{display_name(added_by)}</b> "
            f"начислено <b>+{reward} 🪙</b>.\n"
            f"Размер группы подтверждён: <b>{member_count}</b>.",
        )
    except Exception:
        logger.exception("Ошибка уведомления о награде группы")


def parse_interval(value: str) -> Optional[int]:
    value = value.strip().lower()

    try:
        if value.endswith("m"):
            seconds = int(value[:-1]) * 60
        elif value.endswith("h"):
            seconds = int(value[:-1]) * 3600
        elif value.endswith("s"):
            seconds = int(value[:-1])
        else:
            seconds = int(value)
    except ValueError:
        return None

    return max(60, min(86400, seconds))


@router.message(
    lambda message: (
        message.text
        and (
            normalize_command(message.text)
            in {"/set_inactivity", "/интервал", "/интервал_сундука"}
            or message.text.lower().startswith("интервал сундука")
        )
    )
)
async def set_inactivity_command(message: Message) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    if message.chat.type not in {
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    }:
        await message.answer(
            "Команду нужно использовать в группе."
        )
        return

    parts = message.text.split()
    value = parts[-1] if len(parts) > 1 else ""

    seconds = parse_interval(value)

    if seconds is None:
        await message.answer(
            "Формат: <code>/set_inactivity 30m</code>\n"
            "Минимум — 60 секунд, максимум — 24 часа."
        )
        return

    async with transaction() as db:
        await db.execute(
            """
            INSERT INTO chats(
                chat_id,
                chat_type,
                inactivity_interval,
                last_activity
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                inactivity_interval = excluded.inactivity_interval,
                last_activity = excluded.last_activity
            """,
            (
                message.chat.id,
                message.chat.type,
                seconds,
                now_ts(),
            ),
        )

    chat_activity_memory[message.chat.id] = now_ts()

    await message.answer(
        "✅ Интервал перекати-поля установлен: "
        f"<b>{short_time(seconds)}</b>."
    )


async def inactivity_chest_worker() -> None:
    while True:
        try:
            current_time = now_ts()

            async with db_connection() as db:
                async with db.execute(
                    """
                    SELECT chat_id,
                           inactivity_interval,
                           last_activity
                    FROM chats
                    WHERE chat_type IN ('group', 'supergroup')
                    """
                ) as cursor:
                    chats = await cursor.fetchall()

            for chat in chats:
                chat_id = int(chat["chat_id"])

                try:
                    member_count = await bot.get_chat_member_count(
                        chat_id
                    )
                except Exception:
                    logger.warning(
                        "Не подтверждён размер группы %s",
                        chat_id,
                    )
                    continue

                if member_count < 30:
                    continue

                last_activity = float(
                    chat_activity_memory.get(
                        chat_id,
                        chat["last_activity"] or 0,
                    )
                )

                interval = max(
                    60,
                    min(
                        86400,
                        int(chat["inactivity_interval"] or 3600),
                    ),
                )

                if (
                    last_activity > 0
                    and current_time - last_activity >= interval
                ):
                    async with transaction() as db:
                        await db.execute(
                            """
                            UPDATE chats
                            SET last_activity = ?,
                                last_chest_at = ?
                            WHERE chat_id = ?
                            """,
                            (
                                current_time,
                                current_time,
                                chat_id,
                            ),
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

                    chest_id = await create_chest(
                        chat_id,
                        chest_type,
                        payload,
                    )

                    try:
                        await bot.send_message(
                            chat_id,
                            "🌵 <b>Перекати-поле!</b>\n"
                            "В чате было тихо, и появился сундук.",
                            reply_markup=chest_keyboard(chest_id),
                        )
                    except Exception:
                        logger.exception(
                            "Ошибка отправки сундука тишины"
                        )

        except Exception:
            logger.exception("Ошибка worker перекати-поля")

        await asyncio.sleep(60)


def golden_rain_command_data(
    message: Message,
) -> tuple[int, float]:
    parts = (message.text or "").split()

    count = 10
    interval = 3.0

    numeric = [
        part.replace(",", ".")
        for part in parts[1:]
    ]

    if numeric:
        try:
            count = int(float(numeric[0]))
        except ValueError:
            pass

    if len(numeric) > 1:
        try:
            interval = float(numeric[1])
        except ValueError:
            pass

    count = max(1, min(50, count))
    interval = max(1.0, interval)

    return count, interval


@router.message(
    lambda message: (
        message.text
        and message.text.lower().strip().split()[0]
        in {
            "золотой",
            "/golden_rain",
            "golden_rain",
            "золотой_дождь",
            "/золотой_дождь",
        }
    )
)
async def golden_rain_command(message: Message) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    if message.chat.type not in {
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    }:
        await message.answer(
            "Золотой дождь запускается только в группе."
        )
        return

    text = (message.text or "").lower()

    if not (
        "золотой дождь" in text
        or "золотой_дождь" in text
        or "golden_rain" in text
    ):
        return

    count, interval = golden_rain_command_data(message)

    await message.answer(
        "🌧 <b>Золотой дождь начинается!</b>\n"
        f"Сундуков: <b>{count}</b>\n"
        f"Интервал: <b>{interval:g} сек.</b>"
    )

    task = asyncio.create_task(
        golden_rain_worker(
            message.chat.id,
            count,
            interval,
        )
    )

    active_tasks.add(task)
    task.add_done_callback(active_tasks.discard)


async def golden_rain_worker(
    chat_id: int,
    count: int,
    interval: float,
) -> None:
    legendary_count = 2 if count == 50 else (
        1 if 10 <= count <= 49 else 0
    )

    legendary_positions = set(
        random.sample(
            range(count),
            legendary_count,
        )
    ) if legendary_count else set()

    for index in range(count):
        await asyncio.sleep(interval)

        if index in legendary_positions:
            chest_type = "legendary"
            payload = {
                "coins": 250,
                "golden_rain": True,
            }
            title = "🌟 <b>Легендарный сундук!</b>"
        elif random.random() < 0.25:
            chest_type = "rare"
            payload = {
                "coins": random.randint(50, 100),
                "golden_rain": True,
            }
            title = "💎 <b>Редкий сундук!</b>"
        else:
            chest_type = "normal"
            payload = {
                "coins": random.randint(3, 15),
                "golden_rain": True,
            }
            title = "📦 <b>Обычный сундук!</b>"

        chest_id = await create_chest(
            chat_id,
            chest_type,
            payload,
            lifetime=1800,
        )

        try:
            await bot.send_message(
                chat_id,
                f"{title}\n"
                f"Сундук {index + 1}/{count}.",
                reply_markup=chest_keyboard(chest_id),
            )
        except Exception:
            logger.exception(
                "Ошибка отправки сундука золотого дождя"
            )

def artifact_amount(
    row: Optional[aiosqlite.Row],
) -> int:
    return int(row["amount"] or 0) if row else 0


async def add_artifact(
    db: aiosqlite.Connection,
    user_id: int,
    artifact_type: str,
    subtype: str = "",
    amount: int = 1,
) -> None:
    await db.execute(
        """
        INSERT INTO artifacts(
            user_id, artifact_type, subtype,
            amount, created_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, artifact_type, subtype)
        DO UPDATE SET amount = amount + excluded.amount
        """,
        (
            user_id,
            artifact_type,
            subtype,
            amount,
            now_ts(),
        ),
    )


async def get_artifact_count(
    user_id: int,
    artifact_type: str,
    subtype: str = "",
) -> int:
    async with db_connection() as db:
        async with db.execute(
            """
            SELECT amount
            FROM artifacts
            WHERE user_id = ?
              AND artifact_type = ?
              AND subtype = ?
            """,
            (user_id, artifact_type, subtype),
        ) as cursor:
            row = await cursor.fetchone()

    return artifact_amount(row)


async def show_artifacts(
    message: Message,
    artifact_type: Optional[str] = None,
) -> None:
    await ensure_user(message.from_user)

    user_id = message.from_user.id

    async with db_connection() as db:
        if artifact_type:
            async with db.execute(
                """
                SELECT artifact_type, subtype, amount,
                       hidden_amount, blocked_until
                FROM artifacts
                WHERE user_id = ?
                  AND artifact_type = ?
                ORDER BY artifact_type, subtype
                """,
                (user_id, artifact_type),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                """
                SELECT artifact_type, subtype, amount,
                       hidden_amount, blocked_until
                FROM artifacts
                WHERE user_id = ?
                ORDER BY artifact_type, subtype
                """,
                (user_id,),
            ) as cursor:
                rows = await cursor.fetchall()

    names = {
        "blade": "🗡 Клинки",
        "armor": "🛡 Броня",
        "shield": "🔰 Щиты",
    }

    subtype_names = {
        "weak": "слабый",
        "reinforced": "усиленный",
        "femboy": "фембойский",
        "": "",
    }

    lines = [
        f"<b>{names.get(artifact_type, 'Артефакты')}</b>",
        "",
    ]

    for row in rows:
        amount = int(row["amount"] or 0)

        if amount <= 0:
            continue

        subtype = subtype_names.get(
            str(row["subtype"] or ""),
            str(row["subtype"] or ""),
        )

        title = (
            f"{subtype} "
            f"{names.get(str(row['artifact_type']), str(row['artifact_type']))}"
        ).strip()

        lines.append(f"• {esc(title)}: <b>{amount}</b>")

        blocked = float(row["blocked_until"] or 0)

        if blocked > now_ts():
            lines.append(
                f"  Заблокировано ещё "
                f"{short_time(blocked - now_ts())}"
            )

    if len(lines) == 2:
        lines.append("Пока ничего нет.")

    await message.answer("\n".join(lines))


@router.message(
    lambda message: command_is(message, "мои клинки")
)
async def my_blades_command(message: Message) -> None:
    await show_artifacts(message, "blade")


@router.message(
    lambda message: command_is(message, "моя броня")
)
async def my_armor_command(message: Message) -> None:
    await show_artifacts(message, "armor")


@router.message(
    lambda message: command_is(message, "мои щиты")
)
async def my_shields_command(message: Message) -> None:
    await show_artifacts(message, "shield")


async def craft_shield(
    user_id: int,
    source: str,
    target: str,
) -> bool:
    async with transaction() as db:
        async with db.execute(
            """
            SELECT amount
            FROM artifacts
            WHERE user_id = ?
              AND artifact_type = 'shield'
              AND subtype = ?
            """,
            (user_id, source),
        ) as cursor:
            row = await cursor.fetchone()

        if not row or int(row["amount"] or 0) < 3:
            return False

        await db.execute(
            """
            UPDATE artifacts
            SET amount = amount - 3
            WHERE user_id = ?
              AND artifact_type = 'shield'
              AND subtype = ?
              AND amount >= 3
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


@router.message(
    lambda message: command_is(message, "крафт щитов")
)
async def shield_craft_command(message: Message) -> None:
    user_id = message.from_user.id

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="3 слабых → усиленный",
                    callback_data="shieldcraft:weak",
                )
            ],
            [
                InlineKeyboardButton(
                    text="3 усиленных → фембойский",
                    callback_data="shieldcraft:reinforced",
                )
            ],
            [
                InlineKeyboardButton(
                    text="3 фембойских → броня",
                    callback_data="shieldcraft:femboy",
                )
            ],
        ]
    )

    await message.answer(
        "⚒ <b>Крафт щитов</b>\n"
        "Крафт выполняется атомарно.",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("shieldcraft:"))
async def shield_craft_callback(
    callback: CallbackQuery,
) -> None:
    source = parse_callback(
        callback.data or "",
        "shieldcraft:",
    )

    targets = {
        "weak": "reinforced",
        "reinforced": "femboy",
        "femboy": "armor",
    }

    target = targets.get(source or "")

    if not target:
        await safe_callback_answer(
            callback,
            "Некорректный крафт",
            show_alert=True,
        )
        return

    success = await craft_shield(
        callback.from_user.id,
        source,
        target,
    )

    if not success:
        await safe_callback_answer(
            callback,
            "Нужно 3 предмета одного типа",
            show_alert=True,
        )
        return

    await safe_callback_answer(
        callback,
        "Крафт успешно выполнен",
        show_alert=True,
    )


@router.message(
    lambda message: command_is(message, "крафт")
)
async def card_craft_command(message: Message) -> None:
    user_id = message.from_user.id

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT c.rarity,
                   SUM(i.count - 1) AS duplicates
            FROM inventory AS i
            JOIN cards AS c ON c.id = i.card_id
            WHERE i.user_id = ?
              AND i.count > 1
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

        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{rarity} "
                        f"(дубликатов: {int(row['duplicates'])})"
                    ),
                    callback_data=f"cardcraft:{rarity}",
                )
            ]
        )

    if not buttons:
        await message.answer(
            "❌ Для крафта нужны 3 свободных дубликата "
            "одной редкости."
        )
        return

    await message.answer(
        "⚒ <b>Крафт карт</b>\n"
        "Основная копия карты не расходуется.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )


@router.callback_query(F.data.startswith("cardcraft:"))
async def card_craft_callback(
    callback: CallbackQuery,
) -> None:
    rarity = parse_callback(
        callback.data or "",
        "cardcraft:",
    )

    if not rarity or rarity not in RARITY_ORDER:
        await safe_callback_answer(
            callback,
            "Некорректная редкость",
            show_alert=True,
        )
        return

    index = RARITY_ORDER[rarity]

    if index + 1 >= len(RARITIES):
        await safe_callback_answer(
            callback,
            "Следующей редкости нет",
            show_alert=True,
        )
        return

    target_rarity = RARITIES[index + 1]
    user_id = callback.from_user.id

    async with transaction() as db:
        async with db.execute(
            """
            SELECT i.id, i.count
            FROM inventory AS i
            JOIN cards AS c ON c.id = i.card_id
            WHERE i.user_id = ?
              AND c.rarity = ?
              AND i.count > 1
            """,
            (user_id, rarity),
        ) as cursor:
            rows = await cursor.fetchall()

        available = sum(
            max(0, int(row["count"]) - 1)
            for row in rows
        )

        if available < 3:
            await safe_callback_answer(
                callback,
                "Недостаточно дубликатов",
                show_alert=True,
            )
            return

        target = None

        async with db.execute(
            """
            SELECT id, file_id, description
            FROM cards
            WHERE rarity = ?
            ORDER BY RANDOM()
            LIMIT 1
            """,
            (target_rarity,),
        ) as cursor:
            target = await cursor.fetchone()

        if not target:
            await safe_callback_answer(
                callback,
                "Карт следующей редкости пока нет",
                show_alert=True,
            )
            return

        left = 3

        for row in rows:
            available_here = int(row["count"]) - 1
            take = min(left, available_here)

            await db.execute(
                """
                UPDATE inventory
                SET count = count - ?
                WHERE id = ?
                  AND count - 1 >= ?
                """,
                (take, int(row["id"]), take),
            )

            left -= take

            if left == 0:
                break

        if index >= RARITY_ORDER["Легендарное"]:
            success = random.random() < 0.35
        else:
            success = True

        if not success:
            reward = random.choices(
                [50, 100, 150, 200, 350],
                weights=[40, 25, 18, 12, 5],
                k=1,
            )[0]

            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins + ?
                WHERE user_id = ?
                """,
                (reward, user_id),
            )

            result = (
                "💥 <b>Крафт не удался.</b>\n"
                f"Компенсация: <b>+{reward} 🪙</b>"
            )
        else:
            await db.execute(
                """
                INSERT INTO inventory(user_id, card_id, count)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, card_id)
                DO UPDATE SET count = count + 1
                """,
                (user_id, int(target["id"])),
            )

            result = (
                "🎉 <b>Крафт успешен!</b>\n"
                f"Получена карта #{int(target['id'])}\n"
                f"Редкость: <b>{esc(target_rarity)}</b>"
            )

    await callback.message.answer(result)
    await safe_callback_answer(callback)


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


@router.message(
    lambda message: command_is(
        message,
        "трейд",
        "trade",
    )
)
async def trade_start_command(message: Message) -> None:
    if not message.reply_to_message:
        await message.answer(
            "Команда должна быть ответом на сообщение игрока."
        )
        return

    target = message.reply_to_message.from_user
    sender = message.from_user

    if not target or target.is_bot or target.id == sender.id:
        await message.answer(
            "❌ Нельзя торговать с собой или ботом."
        )
        return

    await ensure_user(sender)
    await ensure_user(target)

    trade_id = secrets.token_urlsafe(12)

    async with transaction() as db:
        await db.execute(
            """
            INSERT INTO trades(
                trade_id, p1, p2, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                sender.id,
                target.id,
                now_ts(),
                now_ts(),
            ),
        )

    await message.answer(
        "🤝 <b>Предложение обмена</b>\n\n"
        f"От: {display_name(sender)}\n"
        f"Кому: {display_name(target)}\n\n"
        "Оба игрока должны выбрать по одному дубликату "
        "одинаковой редкости.",
        reply_markup=trade_keyboard(trade_id),
    )


@router.callback_query(F.data.startswith("trade_accept:"))
async def trade_accept_callback(
    callback: CallbackQuery,
) -> None:
    trade_id = parse_callback(
        callback.data or "",
        "trade_accept:",
    )

    if not trade_id:
        await safe_callback_answer(
            callback,
            "Некорректная сделка",
            show_alert=True,
        )
        return

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT p1, p2, status
            FROM trades
            WHERE trade_id = ?
            """,
            (trade_id,),
        ) as cursor:
            trade = await cursor.fetchone()

    if not trade:
        await safe_callback_answer(
            callback,
            "Сделка не найдена",
            show_alert=True,
        )
        return

    if callback.from_user.id != int(trade["p2"]):
        await safe_callback_answer(
            callback,
            "Это предложение не вам",
            show_alert=True,
        )
        return

    if trade["status"] != "pending":
        await safe_callback_answer(
            callback,
            "Сделка уже закрыта",
            show_alert=True,
        )
        return

    async with transaction() as db:
        await db.execute(
            """
            UPDATE trades
            SET status = 'active',
                updated_at = ?
            WHERE trade_id = ?
              AND status = 'pending'
            """,
            (now_ts(), trade_id),
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Выбрать карту",
                    callback_data=f"trade_pick:{trade_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"trade_confirm:{trade_id}",
                )
            ],
        ]
    )

    await safe_edit_text(
        callback.message,
        "🔄 <b>Обмен открыт.</b>\n"
        "Каждый участник выбирает один дубликат.",
        keyboard,
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("trade_pick:"))
async def trade_pick_callback(
    callback: CallbackQuery,
) -> None:
    trade_id = parse_callback(
        callback.data or "",
        "trade_pick:",
    )

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT p1, p2, status
            FROM trades
            WHERE trade_id = ?
            """,
            (trade_id,),
        ) as cursor:
            trade = await cursor.fetchone()

        if not trade:
            await safe_callback_answer(
                callback,
                "Сделка не найдена",
                show_alert=True,
            )
            return

        user_id = callback.from_user.id

        if user_id not in {
            int(trade["p1"]),
            int(trade["p2"]),
        }:
            await safe_callback_answer(
                callback,
                "Вы не участник сделки",
                show_alert=True,
            )
            return

        async with db.execute(
            """
            SELECT c.id, c.rarity, i.count
            FROM inventory AS i
            JOIN cards AS c ON c.id = i.card_id
            WHERE i.user_id = ?
              AND i.count > 1
            ORDER BY c.id
            """,
            (user_id,),
        ) as cursor:
            cards = await cursor.fetchall()

    buttons = [
        [
            InlineKeyboardButton(
                text=(
                    f"#{int(row['id'])} "
                    f"{row['rarity']} x{int(row['count'])}"
                ),
                callback_data=(
                    f"trade_set:{trade_id}:{int(row['id'])}"
                ),
            )
        ]
        for row in cards
    ]

    if not buttons:
        await safe_callback_answer(
            callback,
            "Нет доступных дубликатов",
            show_alert=True,
        )
        return

    await callback.message.answer(
        "Выберите карту для обмена:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("trade_set:"))
async def trade_set_callback(
    callback: CallbackQuery,
) -> None:
    payload = parse_callback(
        callback.data or "",
        "trade_set:",
    )

    try:
        trade_id, card_id_raw = payload.rsplit(":", 1)
        card_id = int(card_id_raw)
    except (ValueError, AttributeError):
        await safe_callback_answer(
            callback,
            "Некорректные данные",
            show_alert=True,
        )
        return

    user_id = callback.from_user.id

    async with transaction() as db:
        async with db.execute(
            """
            SELECT p1, p2, p1_card, p2_card, status
            FROM trades
            WHERE trade_id = ?
            """,
            (trade_id,),
        ) as cursor:
            trade = await cursor.fetchone()

        if not trade or trade["status"] != "active":
            await safe_callback_answer(
                callback,
                "Сделка закрыта",
                show_alert=True,
            )
            return

        async with db.execute(
            """
            SELECT count
            FROM inventory
            WHERE user_id = ? AND card_id = ?
            """,
            (user_id, card_id),
        ) as cursor:
            inventory = await cursor.fetchone()

        if not inventory or int(inventory["count"]) < 2:
            await safe_callback_answer(
                callback,
                "Карта больше не является дубликатом",
                show_alert=True,
            )
            return

        if user_id == int(trade["p1"]):
            await db.execute(
                """
                UPDATE trades
                SET p1_card = ?,
                    p1_confirmed = 0,
                    p2_confirmed = 0,
                    updated_at = ?
                WHERE trade_id = ?
                """,
                (card_id, now_ts(), trade_id),
            )
        elif user_id == int(trade["p2"]):
            await db.execute(
                """
                UPDATE trades
                SET p2_card = ?,
                    p1_confirmed = 0,
                    p2_confirmed = 0,
                    updated_at = ?
                WHERE trade_id = ?
                """,
                (card_id, now_ts(), trade_id),
            )
        else:
            await safe_callback_answer(
                callback,
                "Вы не участник сделки",
                show_alert=True,
            )
            return

    await safe_callback_answer(
        callback,
        "Карта выбрана, подтверждения сброшены",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("trade_confirm:"))
async def trade_confirm_callback(
    callback: CallbackQuery,
) -> None:
    trade_id = parse_callback(
        callback.data or "",
        "trade_confirm:",
    )

    if not trade_id:
        await safe_callback_answer(
            callback,
            "Некорректная сделка",
            show_alert=True,
        )
        return

    lock = trade_locks.setdefault(trade_id, asyncio.Lock())

    async with lock:
        async with transaction() as db:
            async with db.execute(
                """
                SELECT p1, p2, p1_card, p2_card,
                       p1_confirmed, p2_confirmed, status
                FROM trades
                WHERE trade_id = ?
                """,
                (trade_id,),
            ) as cursor:
                trade = await cursor.fetchone()

            if not trade or trade["status"] != "active":
                await safe_callback_answer(
                    callback,
                    "Сделка закрыта",
                    show_alert=True,
                )
                return

            user_id = callback.from_user.id
            p1 = int(trade["p1"])
            p2 = int(trade["p2"])

            if user_id not in {p1, p2}:
                await safe_callback_answer(
                    callback,
                    "Вы не участник сделки",
                    show_alert=True,
                )
                return

            if not trade["p1_card"] or not trade["p2_card"]:
                await safe_callback_answer(
                    callback,
                    "Оба игрока должны выбрать карту",
                    show_alert=True,
                )
                return

            if user_id == p1:
                await db.execute(
                    """
                    UPDATE trades
                    SET p1_confirmed = 1,
                        updated_at = ?
                    WHERE trade_id = ?
                    """,
                    (now_ts(), trade_id),
                )
            else:
                await db.execute(
                    """
                    UPDATE trades
                    SET p2_confirmed = 1,
                        updated_at = ?
                    WHERE trade_id = ?
                    """,
                    (now_ts(), trade_id),
                )

            if not (
                (
                    user_id == p1
                    and int(trade["p2_confirmed"]) == 1
                )
                or (
                    user_id == p2
                    and int(trade["p1_confirmed"]) == 1
                )
            ):
                await safe_callback_answer(
                    callback,
                    "Ожидается подтверждение второго игрока",
                )
                return

            card_one = int(trade["p1_card"])
            card_two = int(trade["p2_card"])

            async with db.execute(
                """
                SELECT id, rarity
                FROM cards
                WHERE id IN (?, ?)
                """,
                (card_one, card_two),
            ) as cursor:
                card_rows = await cursor.fetchall()

            rarity_by_id = {
                int(row["id"]): str(row["rarity"])
                for row in card_rows
            }

            if (
                card_one not in rarity_by_id
                or card_two not in rarity_by_id
                or rarity_by_id[card_one] != rarity_by_id[card_two]
            ):
                await safe_callback_answer(
                    callback,
                    "Редкости карт должны совпадать",
                    show_alert=True,
                )
                return

            async with db.execute(
                """
                SELECT user_id, card_id, count
                FROM inventory
                WHERE (user_id = ? AND card_id = ?)
                   OR (user_id = ? AND card_id = ?)
                """,
                (p1, card_one, p2, card_two),
            ) as cursor:
                inventories = await cursor.fetchall()

            inventory_map = {
                (int(row["user_id"]), int(row["card_id"])): int(row["count"])
                for row in inventories
            }

            if (
                inventory_map.get((p1, card_one), 0) < 2
                or inventory_map.get((p2, card_two), 0) < 2
            ):
                await safe_callback_answer(
                    callback,
                    "Недостаточно дубликатов",
                    show_alert=True,
                )
                return

            await db.execute(
                """
                UPDATE inventory
                SET count = count - 1
                WHERE user_id = ? AND card_id = ? AND count >= 2
                """,
                (p1, card_one),
            )

            await db.execute(
                """
                UPDATE inventory
                SET count = count - 1
                WHERE user_id = ? AND card_id = ? AND count >= 2
                """,
                (p2, card_two),
            )

            await db.execute(
                """
                INSERT INTO inventory(user_id, card_id, count)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, card_id)
                DO UPDATE SET count = count + 1
                """,
                (p1, card_two),
            )

            await db.execute(
                """
                INSERT INTO inventory(user_id, card_id, count)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, card_id)
                DO UPDATE SET count = count + 1
                """,
                (p2, card_one),
            )

            await db.execute(
                """
                UPDATE trades
                SET status = 'completed',
                    updated_at = ?
                WHERE trade_id = ?
                """,
                (now_ts(), trade_id),
            )

    await callback.message.edit_text(
        "🎉 <b>Обмен завершён!</b>\n"
        "Карты успешно переданы."
    )

    await safe_callback_answer(callback)


@router.message(
    lambda message: (
        message.text
        and re.match(
            r"^/?бот\s+[+-]вип\s+\d+",
            message.text.strip(),
            re.IGNORECASE,
        )
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
        await message.answer(
            "Формат: <code>/бот +вип 12</code> ответом "
            "на сообщение пользователя."
        )
        return

    target = (
        message.reply_to_message.from_user
        if message.reply_to_message
        else None
    )

    if not target:
        await message.answer(
            "Используйте команду ответом на сообщение пользователя."
        )
        return

    await ensure_user(target)

    async with transaction() as db:
        async with db.execute(
            """
            SELECT vip_until
            FROM users
            WHERE user_id = ?
            """,
            (target.id,),
        ) as cursor:
            row = await cursor.fetchone()

        old_until = float(row["vip_until"] or 0)
        current = now_ts()

        if operation == "+вип":
            new_until = max(current, old_until) + hours * 3600
        else:
            new_until = max(0, old_until - hours * 3600)

        await db.execute(
            """
            UPDATE users
            SET vip_until = ?
            WHERE user_id = ?
            """,
            (new_until, target.id),
        )

    await log_action(
        message.from_user.id,
        f"{operation} {hours}ч пользователю {target.id}",
    )

    await message.answer(
        f"✅ VIP пользователя изменён.\n"
        f"Текущий срок: "
        f"<b>{format_date(new_until) if new_until else 'отсутствует'}</b>"
    )


@router.message(
    lambda message: (
        message.text
        and re.match(
            r"^/?бот\s+[+-]коины\s+\d+",
            message.text.strip(),
            re.IGNORECASE,
        )
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
        await message.answer(
            "Формат: <code>/бот +коины 100</code> ответом."
        )
        return

    target = (
        message.reply_to_message.from_user
        if message.reply_to_message
        else None
    )

    if not target or amount < 0:
        await message.answer(
            "Команда должна быть ответом на сообщение пользователя."
        )
        return

    await ensure_user(target)

    async with transaction() as db:
        if operation == "+коины":
            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins + ?
                WHERE user_id = ?
                """,
                (amount, target.id),
            )
        else:
            await db.execute(
                """
                UPDATE users
                SET femcoins = MAX(0, femcoins - ?)
                WHERE user_id = ?
                """,
                (amount, target.id),
            )

    await log_action(
        message.from_user.id,
        f"{operation} {amount} пользователю {target.id}",
    )

    await message.answer("✅ Баланс изменён.")


@router.message(
    lambda message: command_is(message, "log")
)
async def logs_command(message: Message) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT admin_id, action, timestamp
            FROM logs
            ORDER BY id DESC
            LIMIT 25
            """
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await message.answer("Логи пусты.")
        return

    lines = ["📋 <b>Последние действия</b>", ""]

    for row in rows:
        date = format_date(float(row["timestamp"]))
        lines.append(
            f"[{esc(date)}] "
            f"<code>{int(row['admin_id'])}</code> "
            f"{esc(row['action'])}"
        )

    await message.answer("\n".join(lines))


async def cleanup_worker() -> None:
    while True:
        try:
            current_time = now_ts()

            async with transaction() as db:
                await db.execute(
                    """
                    DELETE FROM chests
                    WHERE expires_at <= ?
                       OR claimed_by IS NOT NULL
                    """,
                    (current_time - 3600,),
                )

                await db.execute(
                    """
                    DELETE FROM trades
                    WHERE status IN ('completed', 'cancelled')
                       OR updated_at < ?
                    """,
                    (current_time - 86400,),
                )

                await db.execute(
                    """
                    DELETE FROM admin_codes
                    WHERE expires_at <= ?
                       OR used = 1
                    """,
                    (current_time,),
                )

        except Exception:
            logger.exception("Ошибка очистки временных данных")

        await asyncio.sleep(300)


async def main() -> None:
    await init_database()

    inactivity_task = asyncio.create_task(
        inactivity_chest_worker()
    )
    cleanup_task = asyncio.create_task(
        cleanup_worker()
    )

    active_tasks.update(
        {
            inactivity_task,
            cleanup_task,
        }
    )

    try:
        await bot.delete_webhook(
            drop_pending_updates=True,
        )

        logger.info("Бот запущен")

        await dp.start_polling(bot)

    finally:
        for task in active_tasks:
            task.cancel()

        await asyncio.gather(
            *active_tasks,
            return_exceptions=True,
        )

        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
