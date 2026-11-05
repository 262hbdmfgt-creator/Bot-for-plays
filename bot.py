# ============================================================
# BLOCK 1/10 — ОСНОВА, КОНФИГУРАЦИЯ, БД, МИГРАЦИИ И УТИЛИТЫ
# ============================================================

from __future__ import annotations

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
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
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


# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0") or "0")

CHANNEL_USERNAME = os.getenv(
    "CHANNEL_USERNAME",
    "@FM_femboy",
).strip()

CHANNEL_URL = os.getenv(
    "CHANNEL_URL",
    "https://t.me/FM_femboy",
).strip()

DB_NAME = "fem_bot.db"

if not BOT_TOKEN:
    raise RuntimeError("В .env отсутствует BOT_TOKEN")

if not SUPER_ADMIN_ID:
    raise RuntimeError("В .env отсутствует SUPER_ADMIN_ID")


# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("femboy_bot")


# ============================================================
# BOT / DISPATCHER / ROUTER
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
    ),
)

dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


# ============================================================
# ИГРОВЫЕ КОНСТАНТЫ
# ============================================================

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

SHIELD_TYPES = {
    "weak": "слабый",
    "reinforced": "усиленный",
    "femboy": "фембойский",
}

SHIELD_ALIASES = {
    "weak": "weak",
    "слабый": "weak",
    "слабый_щит": "weak",
    "reinforced": "reinforced",
    "усиленный": "reinforced",
    "усиленный_щит": "reinforced",
    "femboy": "femboy",
    "фембойский": "femboy",
    "фембойский_щит": "femboy",
}

LEAGUES = [
    "Деревянная",
    "Каменная",
    "Медная",
    "Железная",
    "Бронзовая",
    "Серебряная",
    "Золотая",
    "Платиновая",
    "Титановая",
    "Ультра фембойская",
]

STAGES = [
    ("Начинающий", 5),
    ("Опытный", 10),
    ("Профессионал", 15),
]

CASINO_BETS = {10, 20, 50, 100}

VIP_FEMCOINS_PRICES = {
    1: 1500,
    7: 4500,
    30: 10000,
}

VIP_STARS_PRICES = {
    1: 250,
    7: 800,
    30: 2000,
}

COINS_STARS_PRICES = {
    100: 15,
    500: 70,
    1000: 135,
}

GROUP_REWARDS = {
    15: 50,
    500: 300,
    2000: 500,
}


# ============================================================
# FSM-СОСТОЯНИЯ
# ============================================================

class AdminStates(StatesGroup):
    waiting_photo = State()
    waiting_card_description = State()
    waiting_card_edit_description = State()
    waiting_admin_code = State()
    waiting_broadcast = State()
    waiting_promo = State()
    waiting_admin_text = State()


class UserStates(StatesGroup):
    waiting_promo = State()
    waiting_clan_name = State()


# ============================================================
# ВРЕМЕННЫЕ LOCK-ОБЪЕКТЫ
# ============================================================

user_locks: dict[int, asyncio.Lock] = {}
trade_locks: dict[str, asyncio.Lock] = {}
casino_locks: dict[int, asyncio.Lock] = {}
clan_locks: dict[int, asyncio.Lock] = {}

casino_bets: dict[int, int] = {}
broadcast_buffer: dict[int, list[Message]] = {}
running_tasks: set[asyncio.Task] = set()


def get_lock(
    storage: dict[Any, asyncio.Lock],
    key: Any,
) -> asyncio.Lock:
    lock = storage.get(key)

    if lock is None:
        lock = asyncio.Lock()
        storage[key] = lock

    return lock


# ============================================================
# ОБЩИЕ УТИЛИТЫ
# ============================================================

def now() -> float:
    return time.time()


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def fmt_date(timestamp: float) -> str:
    if not timestamp:
        return ""

    return datetime.fromtimestamp(timestamp).strftime(
        "%d.%m.%Y %H:%M"
    )


def fmt_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))

    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}ч {minutes}м"

    if minutes:
        return f"{minutes}м {seconds}с"

    return f"{seconds}с"


def normalize_command(text: Optional[str]) -> str:
    if not text:
        return ""

    token = text.strip().split(maxsplit=1)[0].lower()

    if "@" in token:
        token = token.split("@", 1)[0]

    return token


def command_is(
    message: Message,
    *aliases: str,
) -> bool:
    if not message.text:
        return False

    text = message.text.strip().lower()
    first = normalize_command(text)

    for alias in aliases:
        alias = alias.strip().lower()

        if " " in alias:
            if text in {alias, f"/{alias}"}:
                return True
            continue

        clean_alias = alias.lstrip("/")

        if first in {
            clean_alias,
            f"/{clean_alias}",
        }:
            return True

    return False


def callback_payload(
    data: Optional[str],
    prefix: str,
) -> Optional[str]:
    if not data or not data.startswith(prefix):
        return None

    return data[len(prefix):]


def make_token(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits

    return "".join(
        secrets.choice(alphabet)
        for _ in range(length)
    )


def user_name(user: types.User) -> str:
    if user.username:
        return f"@{esc(user.username)}"

    return esc(
        user.full_name
        or user.first_name
        or str(user.id)
    )


def user_name_without_at(user: types.User) -> str:
    if user.username:
        return esc(user.username.lstrip("@"))

    return esc(
        user.full_name
        or user.first_name
        or str(user.id)
    )


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_shield(value: str) -> Optional[str]:
    return SHIELD_ALIASES.get(
        value.strip().lower()
    )


def xp_for_next_level(level: int) -> int:
    """
    Уровень 1 -> 2 требует 10 опыта.
    Уровень 2 -> 3 требует 12.
    Далее прибавляется по 4.
    """
    level = max(1, level)

    if level == 1:
        return 10

    if level == 2:
        return 12

    return 12 + (level - 2) * 4


def league_stage_level(
    total_level: int,
) -> tuple[str, str, int]:
    """
    Общая последовательность уровней:
    деревянная начинающий 1..5,
    деревянная опытный 6..15,
    деревянная профессионал 16..30,
    затем следующая лига.
    """
    total_level = max(1, total_level)
    level_index = total_level - 1
    league_index = level_index // 30

    if league_index >= len(LEAGUES):
        league_index = len(LEAGUES) - 1

    inside_league = level_index % 30

    if inside_league < 5:
        stage_name = STAGES[0][0]
        level_in_stage = inside_league + 1
    elif inside_league < 15:
        stage_name = STAGES[1][0]
        level_in_stage = inside_league - 5 + 6
    else:
        stage_name = STAGES[2][0]
        level_in_stage = inside_league - 15 + 16

    return (
        LEAGUES[league_index],
        stage_name,
        level_in_stage,
    )


# ============================================================
# CALLBACK И СЕТЕВЫЕ УТИЛИТЫ
# ============================================================

async def answer_callback(
    callback: CallbackQuery,
    text: str = "",
    *,
    alert: bool = False,
) -> None:
    try:
        await callback.answer(
            text,
            show_alert=alert,
        )
    except TelegramBadRequest as error:
        message = str(error).lower()

        if any(
            phrase in message
            for phrase in (
                "query is too old",
                "query id is invalid",
                "response timeout expired",
            )
        ):
            return

        logger.warning(
            "Telegram callback error: %s",
            error,
        )
    except TelegramNetworkError:
        logger.warning(
            "Сетевая ошибка при callback.answer"
        )
    except Exception:
        logger.exception(
            "Неожиданная ошибка callback.answer"
        )


async def edit_text_safely(
    message: Optional[Message],
    text: str,
    markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    if not message:
        return

    try:
        await message.edit_text(
            text,
            reply_markup=markup,
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            logger.warning(
                "Ошибка редактирования сообщения: %s",
                error,
            )
    except TelegramNetworkError:
        logger.warning(
            "Сетевая ошибка редактирования сообщения"
        )
    except Exception:
        logger.exception(
            "Ошибка редактирования сообщения"
        )


async def send_with_retry(
    method,
    *,
    attempts: int = 3,
    delay: float = 0.7,
):
    last_error = None

    for attempt in range(attempts):
        try:
            return await method()
        except TelegramNetworkError as error:
            last_error = error

            if attempt + 1 < attempts:
                await asyncio.sleep(
                    delay * (attempt + 1)
                )
        except Exception:
            raise

    if last_error:
        raise last_error

    return None


# ============================================================
# SQLITE-ПОДКЛЮЧЕНИЕ И ТРАНЗАКЦИИ
# ============================================================

@asynccontextmanager
async def db_connection():
    db = await aiosqlite.connect(DB_NAME)

    db.row_factory = aiosqlite.Row

    await db.execute(
        "PRAGMA journal_mode = WAL"
    )
    await db.execute(
        "PRAGMA synchronous = NORMAL"
    )
    await db.execute(
        "PRAGMA foreign_keys = ON"
    )
    await db.execute(
        "PRAGMA busy_timeout = 15000"
    )
    await db.execute(
        "PRAGMA temp_store = MEMORY"
    )

    try:
        yield db
    finally:
        await db.close()


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


async def get_columns(
    db: aiosqlite.Connection,
    table: str,
) -> set[str]:
    columns: set[str] = set()

    async with db.execute(
        f"PRAGMA table_info({table})"
    ) as cursor:
        rows = await cursor.fetchall()

    for row in rows:
        columns.add(str(row["name"]))

    return columns


async def add_missing_columns(
    db: aiosqlite.Connection,
    table: str,
    definitions: dict[str, str],
) -> None:
    existing = await get_columns(db, table)

    for column, definition in definitions.items():
        if column in existing:
            continue

        try:
            await db.execute(
                f"ALTER TABLE {table} "
                f"ADD COLUMN {column} {definition}"
            )
            logger.info(
                "Миграция: добавлена %s.%s",
                table,
                column,
            )
        except Exception:
            logger.exception(
                "Ошибка миграции %s.%s",
                table,
                column,
            )


# ============================================================
# ИНИЦИАЛИЗАЦИЯ И БЕЗОПАСНЫЕ МИГРАЦИИ
# ============================================================

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
                shield_hidden INTEGER NOT NULL DEFAULT 0,
                first_seen REAL NOT NULL DEFAULT 0,
                experience INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 1,
                league_index INTEGER NOT NULL DEFAULT 0,
                clan_id INTEGER,
                personal_experience INTEGER NOT NULL DEFAULT 0
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
                payload TEXT NOT NULL DEFAULT '{}',
                claimed_by INTEGER,
                claimed_at REAL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS clans (
                clan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                clan_number INTEGER UNIQUE,
                name TEXT NOT NULL UNIQUE,
                owner_id INTEGER NOT NULL,
                clan_coins INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                experience INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS clan_members (
                clan_id INTEGER NOT NULL,
                user_id INTEGER PRIMARY KEY,
                joined_at REAL NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                donated_coins INTEGER NOT NULL DEFAULT 0,
                personal_experience INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS clan_quests (
                quest_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                quest_date TEXT NOT NULL,
                slot INTEGER NOT NULL,
                quest_type TEXT NOT NULL,
                target INTEGER NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                difficulty TEXT NOT NULL,
                clan_reward INTEGER NOT NULL,
                fem_reward INTEGER NOT NULL,
                xp_reward INTEGER NOT NULL,
                claimed INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id, quest_date, slot)
            );

            CREATE TABLE IF NOT EXISTS clan_shop_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clan_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                product TEXT NOT NULL,
                price INTEGER NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS clan_stars_orders (
                order_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                clan_id INTEGER NOT NULL,
                stars INTEGER NOT NULL,
                clan_coins INTEGER NOT NULL,
                created_at REAL NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0
            );
            """
        )

        await add_missing_columns(
            db,
            "users",
            {
                "shield_hidden": (
                    "INTEGER NOT NULL DEFAULT 0"
                ),
                "experience": (
                    "INTEGER NOT NULL DEFAULT 0"
                ),
                "level": (
                    "INTEGER NOT NULL DEFAULT 1"
                ),
                "league_index": (
                    "INTEGER NOT NULL DEFAULT 0"
                ),
                "clan_id": "INTEGER",
                "personal_experience": (
                    "INTEGER NOT NULL DEFAULT 0"
                ),
                "attack_block_until": (
                    "REAL NOT NULL DEFAULT 0"
                ),
            },
        )

        await add_missing_columns(
            db,
            "chats",
            {
                "last_activity": (
                    "REAL NOT NULL DEFAULT 0"
                ),
                "last_chest_at": (
                    "REAL NOT NULL DEFAULT 0"
                ),
            },
        )

        await add_missing_columns(
            db,
            "group_rewards",
            {
                "member_count": (
                    "INTEGER NOT NULL DEFAULT 0"
                ),
                "rewarded_at": (
                    "REAL NOT NULL DEFAULT 0"
                ),
            },
        )

        await db.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_inventory_user_card
            ON inventory(user_id, card_id);

            CREATE INDEX IF NOT EXISTS
            idx_inventory_user
            ON inventory(user_id);

            CREATE INDEX IF NOT EXISTS
            idx_inventory_card
            ON inventory(card_id);

            CREATE INDEX IF NOT EXISTS
            idx_artifacts_user
            ON artifacts(user_id);

            CREATE INDEX IF NOT EXISTS
            idx_clan_members_clan
            ON clan_members(clan_id);

            CREATE INDEX IF NOT EXISTS
            idx_quests_user_date
            ON clan_quests(user_id, quest_date);

            CREATE INDEX IF NOT EXISTS
            idx_logs_timestamp
            ON logs(timestamp);
            """
        )

        await db.execute(
            """
            UPDATE users
            SET first_seen = ?
            WHERE first_seen = 0
            """,
            (now(),),
        )

        await db.execute(
            """
            UPDATE users
            SET level = 1
            WHERE level IS NULL OR level < 1
            """
        )

        await db.commit()

    logger.info(
        "База данных инициализирована и миграции завершены"
    )


# ============================================================
# ПОЛЬЗОВАТЕЛИ И АДМИНИСТРАТОРЫ
# ============================================================

async def ensure_user(
    user: types.User,
    referrer_id: int = 0,
) -> bool:
    username = (
        user.username
        or user.full_name
        or user.first_name
        or str(user.id)
    )

    valid_referrer = (
        referrer_id
        if referrer_id > 0 and referrer_id != user.id
        else 0
    )

    async with transaction() as db:
        async with db.execute(
            """
            SELECT user_id
            FROM users
            WHERE user_id = ?
            """,
            (user.id,),
        ) as cursor:
            existing = await cursor.fetchone()

        if existing:
            await db.execute(
                """
                UPDATE users
                SET username = ?
                WHERE user_id = ?
                """,
                (username, user.id),
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
                user.id,
                username,
                valid_referrer,
                now(),
            ),
        )

        return True


async def user_exists(user_id: int) -> bool:
    async with db_connection() as db:
        async with db.execute(
            """
            SELECT 1
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            return await cursor.fetchone() is not None


async def is_vip(user_id: int) -> bool:
    async with db_connection() as db:
        async with db.execute(
            """
            SELECT vip_until
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

    return bool(
        row
        and safe_float(row["vip_until"]) > now()
    )


async def is_admin(user_id: int) -> bool:
    if user_id == SUPER_ADMIN_ID:
        return True

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT 1
            FROM admins
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            return await cursor.fetchone() is not None


async def db_username(user_id: int) -> str:
    async with db_connection() as db:
        async with db.execute(
            """
            SELECT username
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

    if not row or not row["username"]:
        return f"ID:{user_id}"

    return esc(
        str(row["username"]).lstrip("@")
    )


async def log_action(
    admin_id: int,
    action: str,
) -> None:
    async with db_connection() as db:
        await db.execute(
            """
            INSERT INTO logs(
                admin_id,
                action,
                timestamp
            )
            VALUES (?, ?, ?)
            """,
            (
                admin_id,
                action[:2000],
                now(),
            ),
        )
        await db.commit()


# ============================================================
# АРТЕФАКТЫ
# ============================================================

async def add_artifact(
    db: aiosqlite.Connection,
    user_id: int,
    artifact_type: str,
    subtype: str = "",
    amount: int = 1,
) -> None:
    if amount <= 0:
        return

    await db.execute(
        """
        INSERT INTO artifacts(
            user_id,
            artifact_type,
            subtype,
            amount,
            created_at
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
            now(),
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
            (
                user_id,
                artifact_type,
                subtype,
            ),
        ) as cursor:
            row = await cursor.fetchone()

    return safe_int(
        row["amount"]
        if row
        else 0
    )


# ============================================================
# ОПЫТ И УРОВНИ
# ============================================================

async def add_experience(
    db: aiosqlite.Connection,
    user_id: int,
    amount: int,
) -> list[int]:
    """
    Добавляет опыт и выдаёт награды за каждый достигнутый уровень.

    За обычный уровень:
      5 + 2 * номер_уровня фемкоинов.

    За уровень, кратный 5:
      дополнительный бонус 10 + 3 * номер_уровня.

    При входе в новую ступень:
      фемкоины по формуле для ступени
      и случайный артефакт:
        клинок — 10%;
        усиленный щит — 50%;
        броня — 40%.

    При переходе в следующую лигу выдаётся
    заданная награда лиги.
    """
    if amount <= 0:
        return []

    async with db.execute(
        """
        SELECT experience, level
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    ) as cursor:
        user = await cursor.fetchone()

    if not user:
        return []

    experience = max(
        0,
        safe_int(user["experience"]),
    )
    level = max(
        1,
        safe_int(user["level"], 1),
    )

    experience += amount
    gained_levels: list[int] = []

    # Ключ — номер новой лиги, в которую перешёл игрок.
    # Индексы лиг: 0 деревянная, 1 каменная, ..., 9 фембойская.
    league_rewards: dict[int, tuple[int, dict[str, int]]] = {
        1: (500, {}),
        2: (600, {"shield_weak": 1}),
        3: (800, {"shield_weak": 1, "shield_reinforced": 1}),
        4: (
            1000,
            {
                "blade": 1,
                "armor": 1,
                "shield_reinforced": 3,
            },
        ),
        5: (
            1500,
            {
                "blade": 3,
                "armor": 3,
                "shield_femboy": 2,
                "shield_reinforced": 4,
            },
        ),
        6: (
            2500,
            {
                "blade": 5,
                "armor": 5,
                "shield_femboy": 5,
            },
        ),
        7: (
            5000,
            {"blade": 15},
        ),
        8: (
            10000,
            {"blade": 25},
        ),
        9: (
            25000,
            {"blade": 50},
        ),
    }

    def level_location(
        total_level: int,
    ) -> tuple[int, str]:
        """
        Возвращает индекс лиги и ступень уровня.

        В каждой лиге:
          уровни 1–5 цикла — начинающий;
          6–15 — опытный;
          16–30 — профессионал.
        """
        index = max(0, total_level - 1)
        league_index = min(
            len(LEAGUES) - 1,
            index // 30,
        )
        within_league = index % 30

        if within_league < 5:
            stage = "Начинающий"
        elif within_league < 15:
            stage = "Опытный"
        else:
            stage = "Профессионал"

        return league_index, stage

    while experience >= xp_for_next_level(level):
        experience -= xp_for_next_level(level)
        level += 1
        gained_levels.append(level)

        # Небольшая награда за обычное повышение.
        level_coins = 5 + level * 2

        # Дополнительная награда за каждый уровень, кратный пяти.
        if level % 5 == 0:
            level_coins += 10 + level * 3

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins + ?
            WHERE user_id = ?
            """,
            (level_coins, user_id),
        )

        old_league, old_stage = level_location(level - 1)
        new_league, new_stage = level_location(level)

        # Награда за переход в новую ступень.
        if (
            new_league != old_league
            or new_stage != old_stage
        ):
            league_number = new_league + 1

            stage_coins = {
                "Начинающий": 20 * league_number + 200,
                "Опытный": 30 * league_number + 300,
                "Профессионал": 50 * league_number + 500,
            }[new_stage]

            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins + ?
                WHERE user_id = ?
                """,
                (stage_coins, user_id),
            )

            # Случайная награда за новую ступень:
            # клинок 10%, усиленный щит 50%, броня 40%.
            artifact = random.choices(
                ["blade", "reinforced_shield", "armor"],
                weights=[10, 50, 40],
                k=1,
            )[0]

            if artifact == "blade":
                await add_artifact(
                    db,
                    user_id,
                    "blade",
                    "",
                    1,
                )
            elif artifact == "reinforced_shield":
                await add_artifact(
                    db,
                    user_id,
                    "shield",
                    "reinforced",
                    1,
                )
            else:
                await add_artifact(
                    db,
                    user_id,
                    "armor",
                    "",
                    1,
                )

        # Переход в следующую лигу происходит на уровнях
        # 31, 61, 91 и далее.
        if new_league > old_league:
            reward = league_rewards.get(new_league)

            if reward:
                league_coins, artifacts = reward

                await db.execute(
                    """
                    UPDATE users
                    SET femcoins = femcoins + ?
                    WHERE user_id = ?
                    """,
                    (league_coins, user_id),
                )

                if artifacts.get("blade"):
                    await add_artifact(
                        db,
                        user_id,
                        "blade",
                        "",
                        artifacts["blade"],
                    )

                if artifacts.get("armor"):
                    await add_artifact(
                        db,
                        user_id,
                        "armor",
                        "",
                        artifacts["armor"],
                    )

                if artifacts.get("shield_weak"):
                    await add_artifact(
                        db,
                        user_id,
                        "shield",
                        "weak",
                        artifacts["shield_weak"],
                    )

                if artifacts.get("shield_reinforced"):
                    await add_artifact(
                        db,
                        user_id,
                        "shield",
                        "reinforced",
                        artifacts["shield_reinforced"],
                    )

                if artifacts.get("shield_femboy"):
                    await add_artifact(
                        db,
                        user_id,
                        "shield",
                        "femboy",
                        artifacts["shield_femboy"],
                    )

    await db.execute(
        """
        UPDATE users
        SET experience = ?,
            level = ?,
            league_index = ?
        WHERE user_id = ?
        """,
        (
            experience,
            level,
            min(
                len(LEAGUES) - 1,
                (level - 1) // 30,
            ),
            user_id,
        ),
    )

    return gained_levels
async def grant_reward_with_xp(
    db: aiosqlite.Connection,
    user_id: int,
    coins: int = 0,
    xp: int = 0,
) -> list[int]:
    if coins:
        await db.execute(
            """
            UPDATE users
            SET femcoins = MAX(0, femcoins + ?)
            WHERE user_id = ?
            """,
            (coins, user_id),
        )

    return await add_experience(
        db,
        user_id,
        xp,
    )


# ============================================================
# ПОЛЬЗОВАТЕЛЬСКИЙ MIDDLEWARE
# ============================================================

@router.message.outer_middleware()
async def register_message_middleware(
    handler,
    event,
    data: dict,
):
    if isinstance(event, Message):
        try:
            if event.from_user:
                await ensure_user(event.from_user)

            if event.chat.type in {
                ChatType.GROUP,
                ChatType.SUPERGROUP,
            }:
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
                        ON CONFLICT(chat_id)
                        DO UPDATE SET
                            chat_type = excluded.chat_type,
                            last_activity = excluded.last_activity
                        """,
                        (
                            event.chat.id,
                            event.chat.type,
                            now(),
                        ),
                    )
        except Exception:
            logger.exception(
                "Ошибка middleware регистрации"
            )

    return await handler(event, data)


# ============================================================
# ОБРАБОТЧИК ГЛОБАЛЬНЫХ ОШИБОК
# ============================================================

@dp.error()
async def global_error_handler(event):
    logger.exception(
        "Ошибка обработки события: %r",
        event,
    )
    return True

# ============================================================
# BLOCK 2/10 — ПРОФИЛЬ, КАРТЫ, ФЕМ И VIP-КАРТА
# ============================================================


# ------------------------------------------------------------
# КЛАВИАТУРЫ
# ------------------------------------------------------------

async def card_bonus_keyboard() -> InlineKeyboardMarkup:
    bot_info = await bot.get_me()

    buttons = [
        [
            InlineKeyboardButton(
                text="🎁 Забрать бонус ✨",
                url=(
                    f"https://t.me/{bot_info.username}"
                    "?start=bonus"
                ),
            )
        ]
    ]

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT value
            FROM settings
            WHERE key = 'post_btn_url'
            """
        ) as cursor:
            row = await cursor.fetchone()

    if row and row["value"]:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="💖 Присоединиться / Подробнее",
                    url=str(row["value"]),
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


def profile_keyboard(
    user_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎴 Мои карты 💖",
                    callback_data=(
                        f"profile_cards:{user_id}:0"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗡 Мои клинки",
                    callback_data=(
                        f"profile_artifacts:{user_id}:blade"
                    ),
                ),
                InlineKeyboardButton(
                    text="🛡 Моя броня",
                    callback_data=(
                        f"profile_artifacts:{user_id}:armor"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔰 Мои щиты",
                    callback_data=(
                        f"profile_artifacts:{user_id}:shield"
                    ),
                )
            ],
        ]
    )


# ------------------------------------------------------------
# ПРОФИЛЬ
# ------------------------------------------------------------

async def build_profile_text(
    user_id: int,
) -> str:
    current_time = now()

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT femcoins,
                   vip_until,
                   cd_resets_count,
                   cd_reset_expires,
                   attack_block_until,
                   blade_hidden,
                   armor_hidden,
                   shield_hidden,
                   experience,
                   level,
                   clan_id
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        if not user:
            return (
                "💔 <b>Профиль ещё не создан!</b>"
            )

        async with db.execute(
            """
            SELECT COUNT(DISTINCT card_id) AS unique_cards,
                   COALESCE(SUM(count), 0) AS copies
            FROM inventory
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            card_stats = await cursor.fetchone()

        async with db.execute(
            """
            SELECT COUNT(*) AS total
            FROM cards
            """
        ) as cursor:
            card_total = await cursor.fetchone()

        async with db.execute(
            """
            SELECT artifact_type,
                   subtype,
                   SUM(amount) AS amount,
                   SUM(hidden_amount) AS hidden_amount
            FROM artifacts
            WHERE user_id = ?
            GROUP BY artifact_type, subtype
            """,
            (user_id,),
        ) as cursor:
            artifact_rows = await cursor.fetchall()

        clan = None

        if user["clan_id"]:
            async with db.execute(
                """
                SELECT name,
                       clan_number,
                       level
                FROM clans
                WHERE clan_id = ?
                """,
                (user["clan_id"],),
            ) as cursor:
                clan = await cursor.fetchone()

    league, stage, stage_level = league_stage_level(
        safe_int(user["level"], 1)
    )

    lines = [
        "💖✨ <b>ФЕМБОЙСКИЙ ПРОФИЛЬ</b> ✨💖",
        "",
        f"💰 Фемкоины: "
        f"<b>{safe_int(user['femcoins'])} 🪙</b>",
        f"🏆 Лига: <b>{esc(league)}</b>",
        f"🌟 Ступень: <b>{esc(stage)}</b>",
        f"📈 Уровень: <b>{stage_level}</b>",
        f"💫 Опыт: <b>{safe_int(user['experience'])}</b>",
    ]

    vip_until = safe_float(user["vip_until"])

    if vip_until > current_time:
        lines.append(
            "👑 VIP до: "
            f"<b>{esc(fmt_date(vip_until))}</b>"
        )

    unique_cards = safe_int(
        card_stats["unique_cards"]
    )
    copies = safe_int(
        card_stats["copies"]
    )
    total_cards = safe_int(
        card_total["total"]
    )

    if unique_cards > 0:
        lines.append(
            f"🎴 Собрано карт: "
            f"<b>{unique_cards}/{total_cards}</b> "
            f"✨ Копий: <b>{copies}</b>"
        )

    skip_count = safe_int(
        user["cd_resets_count"]
    )
    skip_expires = safe_float(
        user["cd_reset_expires"]
    )

    if (
        skip_count > 0
        and skip_expires > current_time
    ):
        lines.append(
            f"⚡ Скипы КД: <b>{skip_count}</b> "
            f"⏳ ещё {fmt_seconds(skip_expires - current_time)}"
        )

    visible_blades = 0
    visible_armor = 0
    visible_shields: dict[str, int] = {}

    for row in artifact_rows:
        artifact_type = str(
            row["artifact_type"]
        )
        subtype = str(
            row["subtype"] or ""
        )
        amount = safe_int(
            row["amount"]
        )

        if amount <= 0:
            continue

        if artifact_type == "blade":
            visible_blades += amount

        elif artifact_type == "armor":
            visible_armor += amount

        elif artifact_type == "shield":
            visible_shields[subtype] = (
                visible_shields.get(subtype, 0)
                + amount
            )

    if (
        visible_blades > 0
        and not safe_int(user["blade_hidden"])
    ):
        lines.append(
            f"🗡 Открытые клинки: "
            f"<b>{visible_blades}</b>"
        )

    if (
        visible_armor > 0
        and not safe_int(user["armor_hidden"])
    ):
        lines.append(
            f"🛡 Открытая броня: "
            f"<b>{visible_armor}</b>"
        )

    shield_names = {
        "weak": "слабых",
        "reinforced": "усиленных",
        "femboy": "фембойских",
    }

    if (
        visible_shields
        and not safe_int(user["shield_hidden"])
    ):
        shield_text = []

        for subtype, amount in visible_shields.items():
            if amount > 0:
                shield_text.append(
                    f"{shield_names.get(subtype, subtype)}: "
                    f"{amount}"
                )

        if shield_text:
            lines.append(
                "🔰 Открытые щиты: <b>"
                + ", ".join(shield_text)
                + "</b>"
            )

    if clan:
        lines.append(
            f"🏳️ Клан: <b>{esc(clan['name'])}</b> "
            f"№{safe_int(clan['clan_number'])} "
            f"🌟 Ур. {safe_int(clan['level'])}"
        )

    attack_block = safe_float(
        user["attack_block_until"]
    )

    if attack_block > current_time:
        lines.append(
            "🚫 Сбор карт запрещён ещё "
            f"<b>{fmt_seconds(attack_block - current_time)}</b>"
        )

    return "\n".join(lines)


async def send_profile(
    message: Message,
    user_id: int,
    *,
    reply_to: Optional[int] = None,
) -> None:
    text = await build_profile_text(user_id)

    markup = profile_keyboard(user_id)

    try:
        photos = await bot.get_user_profile_photos(
            user_id=user_id,
            limit=1,
        )
    except Exception:
        logger.exception(
            "Не удалось получить аватар пользователя %s",
            user_id,
        )
        photos = None

    if photos and photos.total_count > 0:
        photo = photos.photos[0][-1].file_id

        await message.answer_photo(
            photo=photo,
            caption=text,
            reply_markup=markup,
            reply_to_message_id=reply_to,
        )
    else:
        await message.answer(
            text,
            reply_markup=markup,
            reply_to_message_id=reply_to,
        )


@router.message(
    lambda message: command_is(
        message,
        "профиль",
        "profile",
        "profil",
    )
)
async def profile_command(
    message: Message,
) -> None:
    await send_profile(
        message,
        message.from_user.id,
        reply_to=message.message_id,
    )


@router.callback_query(
    F.data.startswith("profile_cards:")
)
async def profile_cards_callback(
    callback: CallbackQuery,
) -> None:
    payload = callback_payload(
        callback.data,
        "profile_cards:",
    )

    if not payload:
        await answer_callback(
            callback,
            "Некорректная кнопка",
            alert=True,
        )
        return

    try:
        owner_raw, index_raw = payload.rsplit(":", 1)
        owner_id = int(owner_raw)
        index = int(index_raw)
    except ValueError:
        await answer_callback(
            callback,
            "Некорректные данные",
            alert=True,
        )
        return

    if callback.from_user.id != owner_id:
        await answer_callback(
            callback,
            "💗 Это не ваш профиль!",
            alert=True,
        )
        return

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT c.id,
                   c.file_id,
                   c.rarity,
                   c.description,
                   i.count
            FROM inventory AS i
            JOIN cards AS c
              ON c.id = i.card_id
            WHERE i.user_id = ?
            """,
            (owner_id,),
        ) as cursor:
            cards = await cursor.fetchall()

    if not cards:
        await answer_callback(
            callback,
            "🎴 У вас пока нет карт!",
            alert=True,
        )
        return

    cards.sort(
        key=lambda row: (
            RARITY_ORDER.get(
                str(row["rarity"]),
                -1,
            ),
            safe_int(row["id"]),
        ),
        reverse=True,
    )

    index = max(
        0,
        min(index, len(cards) - 1),
    )

    card = cards[index]

    navigation = []

    if index > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=(
                    f"profile_cards:"
                    f"{owner_id}:{index - 1}"
                ),
            )
        )

    if index < len(cards) - 1:
        navigation.append(
            InlineKeyboardButton(
                text="Вперёд ➡️",
                callback_data=(
                    f"profile_cards:"
                    f"{owner_id}:{index + 1}"
                ),
            )
        )

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            navigation
        ] if navigation else []
    )

    caption = (
        f"🎴✨ <b>КАРТА #{safe_int(card['id'])}</b> ✨🎴\n"
        f"📚 {index + 1}/{len(cards)}\n\n"
        f"🏷 Редкость: <b>{esc(card['rarity'])}</b>\n"
        f"🔢 Копий: <b>{safe_int(card['count'])}</b>\n"
        f"📝 {esc(card['description']) or 'Без описания 💔'}"
    )

    try:
        if callback.message and callback.message.photo:
            await callback.message.edit_media(
                media=InputMediaPhoto(
                    media=card["file_id"],
                    caption=caption,
                ),
                reply_markup=markup,
            )
        elif callback.message:
            await callback.message.delete()

            await callback.message.answer_photo(
                photo=card["file_id"],
                caption=caption,
                reply_markup=markup,
            )
    except Exception:
        logger.exception(
            "Ошибка просмотра карты"
        )

    await answer_callback(callback)


# ------------------------------------------------------------
# ОТКРЫТЫЕ АРТЕФАКТЫ
# ------------------------------------------------------------

@router.callback_query(
    F.data.startswith("profile_artifacts:")
)
async def profile_artifacts_callback(
    callback: CallbackQuery,
) -> None:
    payload = callback_payload(
        callback.data,
        "profile_artifacts:",
    )

    if not payload:
        await answer_callback(
            callback,
            "Некорректная кнопка",
            alert=True,
        )
        return

    try:
        owner_raw, artifact_type = payload.rsplit(":", 1)
        owner_id = int(owner_raw)
    except ValueError:
        await answer_callback(
            callback,
            "Некорректные данные",
            alert=True,
        )
        return

    if callback.from_user.id != owner_id:
        await answer_callback(
            callback,
            "💗 Это не ваш профиль!",
            alert=True,
        )
        return

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT artifact_type,
                   subtype,
                   amount,
                   blocked_until
            FROM artifacts
            WHERE user_id = ?
              AND artifact_type = ?
              AND amount > 0
            ORDER BY subtype
            """,
            (
                owner_id,
                artifact_type,
            ),
        ) as cursor:
            rows = await cursor.fetchall()

        async with db.execute(
            """
            SELECT blade_hidden,
                   armor_hidden,
                   shield_hidden
            FROM users
            WHERE user_id = ?
            """,
            (owner_id,),
        ) as cursor:
            user = await cursor.fetchone()

    names = {
        "blade": "🗡✨ КЛИНКИ",
        "armor": "🛡✨ БРОНЯ",
        "shield": "🔰✨ ЩИТЫ",
    }

    subtype_names = {
        "": "",
        "weak": "слабый",
        "reinforced": "усиленный",
        "femboy": "фембойский",
    }

    lines = [
        f"<b>{names.get(artifact_type, 'АРТЕФАКТЫ')}</b>",
        "",
    ]

    if not rows:
        lines.append(
            "💔 Пока здесь пусто!"
        )

    for row in rows:
        subtype = str(
            row["subtype"] or ""
        )
        amount = safe_int(
            row["amount"]
        )

        title = subtype_names.get(
            subtype,
            subtype,
        )

        if title:
            title += " "

        title += names.get(
            artifact_type,
            artifact_type,
        )

        lines.append(
            f"• {esc(title)}: <b>{amount}</b>"
        )

        blocked_until = safe_float(
            row["blocked_until"]
        )

        if blocked_until > now():
            lines.append(
                "  🔒 Заблокировано ещё "
                f"{fmt_seconds(blocked_until - now())}"
            )

    hidden = False

    if user:
        if artifact_type == "blade":
            hidden = bool(
                safe_int(user["blade_hidden"])
            )
        elif artifact_type == "armor":
            hidden = bool(
                safe_int(user["armor_hidden"])
            )
        elif artifact_type == "shield":
            hidden = bool(
                safe_int(user["shield_hidden"])
            )

    lines.append(
        "\n👁️ Статус отображения: "
        + (
            "скрыто"
            if hidden
            else "открыто"
        )
    )

    await edit_text_safely(
        callback.message,
        "\n".join(lines),
        profile_keyboard(owner_id),
    )

    await answer_callback(callback)


@router.message(
    lambda message: command_is(
        message,
        "мои клинки",
    )
)
async def my_blades_command(
    message: Message,
) -> None:
    await show_artifacts_message(
        message,
        "blade",
    )


@router.message(
    lambda message: command_is(
        message,
        "моя броня",
    )
)
async def my_armor_command(
    message: Message,
) -> None:
    await show_artifacts_message(
        message,
        "armor",
    )


@router.message(
    lambda message: command_is(
        message,
        "мои щиты",
    )
)
async def my_shields_command(
    message: Message,
) -> None:
    await show_artifacts_message(
        message,
        "shield",
    )


async def show_artifacts_message(
    message: Message,
    artifact_type: str,
) -> None:
    user_id = message.from_user.id

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT artifact_type,
                   subtype,
                   amount,
                   blocked_until
            FROM artifacts
            WHERE user_id = ?
              AND artifact_type = ?
              AND amount > 0
            ORDER BY subtype
            """,
            (
                user_id,
                artifact_type,
            ),
        ) as cursor:
            rows = await cursor.fetchall()

    title = {
        "blade": "🗡✨ МОИ КЛИНКИ",
        "armor": "🛡✨ МОЯ БРОНЯ",
        "shield": "🔰✨ МОИ ЩИТЫ",
    }.get(
        artifact_type,
        "✨ МОИ АРТЕФАКТЫ",
    )

    names = {
        "weak": "слабый",
        "reinforced": "усиленный",
        "femboy": "фембойский",
        "": "",
    }

    lines = [
        f"<b>{title}</b>",
        "",
    ]

    if not rows:
        lines.append(
            "💔 У вас пока ничего нет!"
        )

    for row in rows:
        subtype = str(
            row["subtype"] or ""
        )

        prefix = names.get(
            subtype,
            subtype,
        )

        if prefix:
            prefix += " "

        lines.append(
            f"• {esc(prefix)}"
            f"{esc(artifact_type)}: "
            f"<b>{safe_int(row['amount'])}</b>"
        )

        blocked_until = safe_float(
            row["blocked_until"]
        )

        if blocked_until > now():
            lines.append(
                f"🔒 Блокировка: "
                f"{fmt_seconds(blocked_until - now())}"
            )

    await message.answer(
        "\n".join(lines)
    )


# ------------------------------------------------------------
# ВЫБОР И ВЫДАЧА КАРТ
# ------------------------------------------------------------

async def choose_card(
    db: aiosqlite.Connection,
    weights: dict[str, float],
) -> Optional[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT DISTINCT rarity
        FROM cards
        """
    ) as cursor:
        rarity_rows = await cursor.fetchall()

    available = {
        str(row["rarity"])
        for row in rarity_rows
    }

    candidates = [
        rarity
        for rarity in weights
        if rarity in available
    ]

    if candidates:
        selected_rarity = random.choices(
            candidates,
            weights=[
                weights[rarity]
                for rarity in candidates
            ],
            k=1,
        )[0]

        async with db.execute(
            """
            SELECT id,
                   file_id,
                   rarity,
                   description
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
        SELECT id,
               file_id,
               rarity,
               description
        FROM cards
        ORDER BY RANDOM()
        LIMIT 1
        """
    ) as cursor:
        return await cursor.fetchone()


async def add_card(
    db: aiosqlite.Connection,
    user_id: int,
    card_id: int,
) -> bool:
    async with db.execute(
        """
        SELECT count
        FROM inventory
        WHERE user_id = ?
          AND card_id = ?
        """,
        (
            user_id,
            card_id,
        ),
    ) as cursor:
        row = await cursor.fetchone()

    if row:
        await db.execute(
            """
            UPDATE inventory
            SET count = count + 1
            WHERE user_id = ?
              AND card_id = ?
            """,
            (
                user_id,
                card_id,
            ),
        )
        return True

    await db.execute(
        """
        INSERT INTO inventory(
            user_id,
            card_id,
            count
        )
        VALUES (?, ?, 1)
        """,
        (
            user_id,
            card_id,
        ),
    )

    return False


async def draw_card(
    user_id: int,
    *,
    vip: bool,
    lucky: bool,
    force_duplicate: bool = False,
) -> Optional[dict[str, Any]]:
    if vip and not force_duplicate:
        weights = VIP_WEIGHTS
    elif lucky:
        weights = LUCK_WEIGHTS
    else:
        weights = BASE_WEIGHTS

    async with transaction() as db:
        card = None

        if force_duplicate:
            async with db.execute(
                """
                SELECT c.id,
                       c.file_id,
                       c.rarity,
                       c.description
                FROM inventory AS i
                JOIN cards AS c
                  ON c.id = i.card_id
                WHERE i.user_id = ?
                  AND i.count >= 1
                ORDER BY RANDOM()
                LIMIT 1
                """,
                (user_id,),
            ) as cursor:
                card = await cursor.fetchone()

        if not card:
            card = await choose_card(
                db,
                weights,
            )

        if not card:
            return None

        card_id = safe_int(
            card["id"]
        )
        rarity = str(
            card["rarity"]
        )

        duplicate = await add_card(
            db,
            user_id,
            card_id,
        )

        compensation = 0

        if duplicate and vip:
            compensation = DUPLICATE_COMPENSATION.get(
                rarity,
                5,
            )

            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins + ?
                WHERE user_id = ?
                """,
                (
                    compensation,
                    user_id,
                ),
            )

        await add_experience(
            db,
            user_id,
            2,
        )

        return {
            "id": card_id,
            "file_id": card["file_id"],
            "rarity": rarity,
            "description": (
                card["description"]
                or ""
            ),
            "duplicate": duplicate,
            "compensation": compensation,
        }


def card_caption(
    card: dict[str, Any],
    *,
    second: bool = False,
) -> str:
    if second:
        title = (
            "👑💖 <b>VIP-БОНУС: ВТОРАЯ КАРТА!</b> 💖👑"
        )
    else:
        title = (
            "✨🎴 <b>ВАМ ВЫПАЛА КАРТА!</b> 🎴✨"
        )

    text = (
        f"{title}\n\n"
        f"🎴 Номер: <b>#{safe_int(card['id'])}</b>\n"
        f"🏷 Редкость: <b>{esc(card['rarity'])}</b>\n"
        f"📝 Описание: "
        f"<b>{esc(card['description']) or 'Без описания'}</b>\n"
    )

    if card["duplicate"]:
        text += (
            "\n🔁 <i>Дубликат! Можно использовать "
            "для крафта или трейда.</i>"
        )
    else:
        text += (
            "\n🆕 <i>Новая карточка в коллекции!</i>"
        )

    if card["compensation"]:
        text += (
            "\n💰 VIP-компенсация: "
            f"<b>+{safe_int(card['compensation'])} 🪙</b>"
        )

    text += "\n💫 Опыт: <b>+2</b>"

    return text


# ------------------------------------------------------------
# КОМАНДА ФЕМ
# ------------------------------------------------------------

async def get_fem_cooldown(
    db: aiosqlite.Connection,
    user_id: int,
    current_time: float,
) -> float:
    async with db.execute(
        """
        SELECT custom_cd_hours,
               custom_cd_expires,
               vip_until
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    ) as cursor:
        row = await cursor.fetchone()

    if not row:
        return 3 * 3600

    custom_expires = safe_float(
        row["custom_cd_expires"]
    )

    custom_hours = safe_float(
        row["custom_cd_hours"],
        3,
    )

    if (
        custom_expires == -1
        or custom_expires > current_time
    ):
        return max(
            0,
            custom_hours * 3600,
        )

    if safe_float(row["vip_until"]) > current_time:
        return 2 * 3600

    return 3 * 3600


@router.message(
    lambda message: command_is(
        message,
        "фем",
        "fem",
        "foto",
    )
)
async def fem_command(
    message: Message,
) -> None:
    user_id = message.from_user.id
    lock = get_lock(
        user_locks,
        user_id,
    )

    async with lock:
        current_time = now()

        async with transaction() as db:
            async with db.execute(
                """
                SELECT last_fem_time,
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
                await message.answer(
                    "💔 Профиль не найден!"
                )
                return

            attack_block = safe_float(
                user["attack_block_until"]
            )

            if attack_block > current_time:
                await message.answer(
                    "🚫💔 <b>Сбор карт запрещён!</b>\n"
                    f"Осталось: "
                    f"<b>{fmt_seconds(attack_block - current_time)}</b>"
                )
                return

            cooldown = await get_fem_cooldown(
                db,
                user_id,
                current_time,
            )

            last_fem = safe_float(
                user["last_fem_time"]
            )

            elapsed = current_time - last_fem

            skips = safe_int(
                user["cd_resets_count"]
            )

            skip_expires = safe_float(
                user["cd_reset_expires"]
            )

            if skip_expires <= current_time:
                skips = 0

            if elapsed < cooldown:
                if skips <= 0:
                    await message.answer(
                        "⏳💗 <b>Ещё рано!</b>\n"
                        f"До следующего фема: "
                        f"<b>{fmt_seconds(cooldown - elapsed)}</b>"
                    )
                    return

                skips -= 1

                await db.execute(
                    """
                    UPDATE users
                    SET cd_resets_count = ?,
                        cd_reset_expires = ?
                    WHERE user_id = ?
                    """,
                    (
                        skips,
                        skip_expires if skips else 0,
                        user_id,
                    ),
                )

            await db.execute(
                """
                UPDATE users
                SET last_fem_time = ?
                WHERE user_id = ?
                """,
                (
                    current_time,
                    user_id,
                ),
            )

            vip = (
                safe_float(user["vip_until"])
                > current_time
            )

            lucky = (
                safe_float(user["luck_expires"])
                > current_time
            )

        first_card = await draw_card(
            user_id,
            vip=vip,
            lucky=lucky,
        )

        if not first_card:
            await message.answer(
                "😿🎴 В библиотеке пока нет карт!"
            )
            return

        markup = await card_bonus_keyboard()

        await message.answer_photo(
            photo=first_card["file_id"],
            caption=card_caption(first_card),
            reply_markup=markup,
        )

        if (
            vip
            and random.random() < 0.20
        ):
            second_card = await draw_card(
                user_id,
                vip=vip,
                lucky=lucky,
                force_duplicate=True,
            )

            if second_card:
                await asyncio.sleep(0.25)

                await message.answer_photo(
                    photo=second_card["file_id"],
                    caption=card_caption(
                        second_card,
                        second=True,
                    ),
                    reply_markup=markup,
                )

# ============================================================
# BLOCK 2/10 — ПРОФИЛЬ, КАРТЫ, ФЕМ И VIP-КАРТА
# ============================================================


# ------------------------------------------------------------
# КЛАВИАТУРЫ
# ------------------------------------------------------------

async def card_bonus_keyboard() -> InlineKeyboardMarkup:
    bot_info = await bot.get_me()

    buttons = [
        [
            InlineKeyboardButton(
                text="🎁 Забрать бонус ✨",
                url=(
                    f"https://t.me/{bot_info.username}"
                    "?start=bonus"
                ),
            )
        ]
    ]

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT value
            FROM settings
            WHERE key = 'post_btn_url'
            """
        ) as cursor:
            row = await cursor.fetchone()

    if row and row["value"]:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="💖 Присоединиться / Подробнее",
                    url=str(row["value"]),
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


def profile_keyboard(
    user_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎴 Мои карты 💖",
                    callback_data=(
                        f"profile_cards:{user_id}:0"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗡 Мои клинки",
                    callback_data=(
                        f"profile_artifacts:{user_id}:blade"
                    ),
                ),
                InlineKeyboardButton(
                    text="🛡 Моя броня",
                    callback_data=(
                        f"profile_artifacts:{user_id}:armor"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔰 Мои щиты",
                    callback_data=(
                        f"profile_artifacts:{user_id}:shield"
                    ),
                )
            ],
        ]
    )


# ------------------------------------------------------------
# ПРОФИЛЬ
# ------------------------------------------------------------

async def build_profile_text(
    user_id: int,
) -> str:
    current_time = now()

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT femcoins,
                   vip_until,
                   cd_resets_count,
                   cd_reset_expires,
                   attack_block_until,
                   blade_hidden,
                   armor_hidden,
                   shield_hidden,
                   experience,
                   level,
                   clan_id
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        if not user:
            return (
                "💔 <b>Профиль ещё не создан!</b>"
            )

        async with db.execute(
            """
            SELECT COUNT(DISTINCT card_id) AS unique_cards,
                   COALESCE(SUM(count), 0) AS copies
            FROM inventory
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            card_stats = await cursor.fetchone()

        async with db.execute(
            """
            SELECT COUNT(*) AS total
            FROM cards
            """
        ) as cursor:
            card_total = await cursor.fetchone()

        async with db.execute(
            """
            SELECT artifact_type,
                   subtype,
                   SUM(amount) AS amount,
                   SUM(hidden_amount) AS hidden_amount
            FROM artifacts
            WHERE user_id = ?
            GROUP BY artifact_type, subtype
            """,
            (user_id,),
        ) as cursor:
            artifact_rows = await cursor.fetchall()

        clan = None

        if user["clan_id"]:
            async with db.execute(
                """
                SELECT name,
                       clan_number,
                       level
                FROM clans
                WHERE clan_id = ?
                """,
                (user["clan_id"],),
            ) as cursor:
                clan = await cursor.fetchone()

    league, stage, stage_level = league_stage_level(
        safe_int(user["level"], 1)
    )

    lines = [
        "💖✨ <b>ФЕМБОЙСКИЙ ПРОФИЛЬ</b> ✨💖",
        "",
        f"💰 Фемкоины: "
        f"<b>{safe_int(user['femcoins'])} 🪙</b>",
        f"🏆 Лига: <b>{esc(league)}</b>",
        f"🌟 Ступень: <b>{esc(stage)}</b>",
        f"📈 Уровень: <b>{stage_level}</b>",
        f"💫 Опыт: <b>{safe_int(user['experience'])}</b>",
    ]

    vip_until = safe_float(user["vip_until"])

    if vip_until > current_time:
        lines.append(
            "👑 VIP до: "
            f"<b>{esc(fmt_date(vip_until))}</b>"
        )

    unique_cards = safe_int(
        card_stats["unique_cards"]
    )
    copies = safe_int(
        card_stats["copies"]
    )
    total_cards = safe_int(
        card_total["total"]
    )

    if unique_cards > 0:
        lines.append(
            f"🎴 Собрано карт: "
            f"<b>{unique_cards}/{total_cards}</b> "
            f"✨ Копий: <b>{copies}</b>"
        )

    skip_count = safe_int(
        user["cd_resets_count"]
    )
    skip_expires = safe_float(
        user["cd_reset_expires"]
    )

    if (
        skip_count > 0
        and skip_expires > current_time
    ):
        lines.append(
            f"⚡ Скипы КД: <b>{skip_count}</b> "
            f"⏳ ещё {fmt_seconds(skip_expires - current_time)}"
        )

    visible_blades = 0
    visible_armor = 0
    visible_shields: dict[str, int] = {}

    for row in artifact_rows:
        artifact_type = str(
            row["artifact_type"]
        )
        subtype = str(
            row["subtype"] or ""
        )
        amount = safe_int(
            row["amount"]
        )

        if amount <= 0:
            continue

        if artifact_type == "blade":
            visible_blades += amount

        elif artifact_type == "armor":
            visible_armor += amount

        elif artifact_type == "shield":
            visible_shields[subtype] = (
                visible_shields.get(subtype, 0)
                + amount
            )

    if (
        visible_blades > 0
        and not safe_int(user["blade_hidden"])
    ):
        lines.append(
            f"🗡 Открытые клинки: "
            f"<b>{visible_blades}</b>"
        )

    if (
        visible_armor > 0
        and not safe_int(user["armor_hidden"])
    ):
        lines.append(
            f"🛡 Открытая броня: "
            f"<b>{visible_armor}</b>"
        )

    shield_names = {
        "weak": "слабых",
        "reinforced": "усиленных",
        "femboy": "фембойских",
    }

    if (
        visible_shields
        and not safe_int(user["shield_hidden"])
    ):
        shield_text = []

        for subtype, amount in visible_shields.items():
            if amount > 0:
                shield_text.append(
                    f"{shield_names.get(subtype, subtype)}: "
                    f"{amount}"
                )

        if shield_text:
            lines.append(
                "🔰 Открытые щиты: <b>"
                + ", ".join(shield_text)
                + "</b>"
            )

    if clan:
        lines.append(
            f"🏳️ Клан: <b>{esc(clan['name'])}</b> "
            f"№{safe_int(clan['clan_number'])} "
            f"🌟 Ур. {safe_int(clan['level'])}"
        )

    attack_block = safe_float(
        user["attack_block_until"]
    )

    if attack_block > current_time:
        lines.append(
            "🚫 Сбор карт запрещён ещё "
            f"<b>{fmt_seconds(attack_block - current_time)}</b>"
        )

    return "\n".join(lines)


async def send_profile(
    message: Message,
    user_id: int,
    *,
    reply_to: Optional[int] = None,
) -> None:
    text = await build_profile_text(user_id)

    markup = profile_keyboard(user_id)

    try:
        photos = await bot.get_user_profile_photos(
            user_id=user_id,
            limit=1,
        )
    except Exception:
        logger.exception(
            "Не удалось получить аватар пользователя %s",
            user_id,
        )
        photos = None

    if photos and photos.total_count > 0:
        photo = photos.photos[0][-1].file_id

        await message.answer_photo(
            photo=photo,
            caption=text,
            reply_markup=markup,
            reply_to_message_id=reply_to,
        )
    else:
        await message.answer(
            text,
            reply_markup=markup,
            reply_to_message_id=reply_to,
        )


@router.message(
    lambda message: command_is(
        message,
        "профиль",
        "profile",
        "profil",
    )
)
async def profile_command(
    message: Message,
) -> None:
    await send_profile(
        message,
        message.from_user.id,
        reply_to=message.message_id,
    )


@router.callback_query(
    F.data.startswith("profile_cards:")
)
async def profile_cards_callback(
    callback: CallbackQuery,
) -> None:
    payload = callback_payload(
        callback.data,
        "profile_cards:",
    )

    if not payload:
        await answer_callback(
            callback,
            "Некорректная кнопка",
            alert=True,
        )
        return

    try:
        owner_raw, index_raw = payload.rsplit(":", 1)
        owner_id = int(owner_raw)
        index = int(index_raw)
    except ValueError:
        await answer_callback(
            callback,
            "Некорректные данные",
            alert=True,
        )
        return

    if callback.from_user.id != owner_id:
        await answer_callback(
            callback,
            "💗 Это не ваш профиль!",
            alert=True,
        )
        return

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT c.id,
                   c.file_id,
                   c.rarity,
                   c.description,
                   i.count
            FROM inventory AS i
            JOIN cards AS c
              ON c.id = i.card_id
            WHERE i.user_id = ?
            """,
            (owner_id,),
        ) as cursor:
            cards = await cursor.fetchall()

    if not cards:
        await answer_callback(
            callback,
            "🎴 У вас пока нет карт!",
            alert=True,
        )
        return

    cards.sort(
        key=lambda row: (
            RARITY_ORDER.get(
                str(row["rarity"]),
                -1,
            ),
            safe_int(row["id"]),
        ),
        reverse=True,
    )

    index = max(
        0,
        min(index, len(cards) - 1),
    )

    card = cards[index]

    navigation = []

    if index > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=(
                    f"profile_cards:"
                    f"{owner_id}:{index - 1}"
                ),
            )
        )

    if index < len(cards) - 1:
        navigation.append(
            InlineKeyboardButton(
                text="Вперёд ➡️",
                callback_data=(
                    f"profile_cards:"
                    f"{owner_id}:{index + 1}"
                ),
            )
        )

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            navigation
        ] if navigation else []
    )

    caption = (
        f"🎴✨ <b>КАРТА #{safe_int(card['id'])}</b> ✨🎴\n"
        f"📚 {index + 1}/{len(cards)}\n\n"
        f"🏷 Редкость: <b>{esc(card['rarity'])}</b>\n"
        f"🔢 Копий: <b>{safe_int(card['count'])}</b>\n"
        f"📝 {esc(card['description']) or 'Без описания 💔'}"
    )

    try:
        if callback.message and callback.message.photo:
            await callback.message.edit_media(
                media=InputMediaPhoto(
                    media=card["file_id"],
                    caption=caption,
                ),
                reply_markup=markup,
            )
        elif callback.message:
            await callback.message.delete()

            await callback.message.answer_photo(
                photo=card["file_id"],
                caption=caption,
                reply_markup=markup,
            )
    except Exception:
        logger.exception(
            "Ошибка просмотра карты"
        )

    await answer_callback(callback)


# ------------------------------------------------------------
# ОТКРЫТЫЕ АРТЕФАКТЫ
# ------------------------------------------------------------

@router.callback_query(
    F.data.startswith("profile_artifacts:")
)
async def profile_artifacts_callback(
    callback: CallbackQuery,
) -> None:
    payload = callback_payload(
        callback.data,
        "profile_artifacts:",
    )

    if not payload:
        await answer_callback(
            callback,
            "Некорректная кнопка",
            alert=True,
        )
        return

    try:
        owner_raw, artifact_type = payload.rsplit(":", 1)
        owner_id = int(owner_raw)
    except ValueError:
        await answer_callback(
            callback,
            "Некорректные данные",
            alert=True,
        )
        return

    if callback.from_user.id != owner_id:
        await answer_callback(
            callback,
            "💗 Это не ваш профиль!",
            alert=True,
        )
        return

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT artifact_type,
                   subtype,
                   amount,
                   blocked_until
            FROM artifacts
            WHERE user_id = ?
              AND artifact_type = ?
              AND amount > 0
            ORDER BY subtype
            """,
            (
                owner_id,
                artifact_type,
            ),
        ) as cursor:
            rows = await cursor.fetchall()

        async with db.execute(
            """
            SELECT blade_hidden,
                   armor_hidden,
                   shield_hidden
            FROM users
            WHERE user_id = ?
            """,
            (owner_id,),
        ) as cursor:
            user = await cursor.fetchone()

    names = {
        "blade": "🗡✨ КЛИНКИ",
        "armor": "🛡✨ БРОНЯ",
        "shield": "🔰✨ ЩИТЫ",
    }

    subtype_names = {
        "": "",
        "weak": "слабый",
        "reinforced": "усиленный",
        "femboy": "фембойский",
    }

    lines = [
        f"<b>{names.get(artifact_type, 'АРТЕФАКТЫ')}</b>",
        "",
    ]

    if not rows:
        lines.append(
            "💔 Пока здесь пусто!"
        )

    for row in rows:
        subtype = str(
            row["subtype"] or ""
        )
        amount = safe_int(
            row["amount"]
        )

        title = subtype_names.get(
            subtype,
            subtype,
        )

        if title:
            title += " "

        title += names.get(
            artifact_type,
            artifact_type,
        )

        lines.append(
            f"• {esc(title)}: <b>{amount}</b>"
        )

        blocked_until = safe_float(
            row["blocked_until"]
        )

        if blocked_until > now():
            lines.append(
                "  🔒 Заблокировано ещё "
                f"{fmt_seconds(blocked_until - now())}"
            )

    hidden = False

    if user:
        if artifact_type == "blade":
            hidden = bool(
                safe_int(user["blade_hidden"])
            )
        elif artifact_type == "armor":
            hidden = bool(
                safe_int(user["armor_hidden"])
            )
        elif artifact_type == "shield":
            hidden = bool(
                safe_int(user["shield_hidden"])
            )

    lines.append(
        "\n👁️ Статус отображения: "
        + (
            "скрыто"
            if hidden
            else "открыто"
        )
    )

    await edit_text_safely(
        callback.message,
        "\n".join(lines),
        profile_keyboard(owner_id),
    )

    await answer_callback(callback)


@router.message(
    lambda message: command_is(
        message,
        "мои клинки",
    )
)
async def my_blades_command(
    message: Message,
) -> None:
    await show_artifacts_message(
        message,
        "blade",
    )


@router.message(
    lambda message: command_is(
        message,
        "моя броня",
    )
)
async def my_armor_command(
    message: Message,
) -> None:
    await show_artifacts_message(
        message,
        "armor",
    )


@router.message(
    lambda message: command_is(
        message,
        "мои щиты",
    )
)
async def my_shields_command(
    message: Message,
) -> None:
    await show_artifacts_message(
        message,
        "shield",
    )


async def show_artifacts_message(
    message: Message,
    artifact_type: str,
) -> None:
    user_id = message.from_user.id

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT artifact_type,
                   subtype,
                   amount,
                   blocked_until
            FROM artifacts
            WHERE user_id = ?
              AND artifact_type = ?
              AND amount > 0
            ORDER BY subtype
            """,
            (
                user_id,
                artifact_type,
            ),
        ) as cursor:
            rows = await cursor.fetchall()

    title = {
        "blade": "🗡✨ МОИ КЛИНКИ",
        "armor": "🛡✨ МОЯ БРОНЯ",
        "shield": "🔰✨ МОИ ЩИТЫ",
    }.get(
        artifact_type,
        "✨ МОИ АРТЕФАКТЫ",
    )

    names = {
        "weak": "слабый",
        "reinforced": "усиленный",
        "femboy": "фембойский",
        "": "",
    }

    lines = [
        f"<b>{title}</b>",
        "",
    ]

    if not rows:
        lines.append(
            "💔 У вас пока ничего нет!"
        )

    for row in rows:
        subtype = str(
            row["subtype"] or ""
        )

        prefix = names.get(
            subtype,
            subtype,
        )

        if prefix:
            prefix += " "

        lines.append(
            f"• {esc(prefix)}"
            f"{esc(artifact_type)}: "
            f"<b>{safe_int(row['amount'])}</b>"
        )

        blocked_until = safe_float(
            row["blocked_until"]
        )

        if blocked_until > now():
            lines.append(
                f"🔒 Блокировка: "
                f"{fmt_seconds(blocked_until - now())}"
            )

    await message.answer(
        "\n".join(lines)
    )


# ------------------------------------------------------------
# ВЫБОР И ВЫДАЧА КАРТ
# ------------------------------------------------------------

async def choose_card(
    db: aiosqlite.Connection,
    weights: dict[str, float],
) -> Optional[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT DISTINCT rarity
        FROM cards
        """
    ) as cursor:
        rarity_rows = await cursor.fetchall()

    available = {
        str(row["rarity"])
        for row in rarity_rows
    }

    candidates = [
        rarity
        for rarity in weights
        if rarity in available
    ]

    if candidates:
        selected_rarity = random.choices(
            candidates,
            weights=[
                weights[rarity]
                for rarity in candidates
            ],
            k=1,
        )[0]

        async with db.execute(
            """
            SELECT id,
                   file_id,
                   rarity,
                   description
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
        SELECT id,
               file_id,
               rarity,
               description
        FROM cards
        ORDER BY RANDOM()
        LIMIT 1
        """
    ) as cursor:
        return await cursor.fetchone()


async def add_card(
    db: aiosqlite.Connection,
    user_id: int,
    card_id: int,
) -> bool:
    async with db.execute(
        """
        SELECT count
        FROM inventory
        WHERE user_id = ?
          AND card_id = ?
        """,
        (
            user_id,
            card_id,
        ),
    ) as cursor:
        row = await cursor.fetchone()

    if row:
        await db.execute(
            """
            UPDATE inventory
            SET count = count + 1
            WHERE user_id = ?
              AND card_id = ?
            """,
            (
                user_id,
                card_id,
            ),
        )
        return True

    await db.execute(
        """
        INSERT INTO inventory(
            user_id,
            card_id,
            count
        )
        VALUES (?, ?, 1)
        """,
        (
            user_id,
            card_id,
        ),
    )

    return False


async def draw_card(
    user_id: int,
    *,
    vip: bool,
    lucky: bool,
    force_duplicate: bool = False,
) -> Optional[dict[str, Any]]:
    if vip and not force_duplicate:
        weights = VIP_WEIGHTS
    elif lucky:
        weights = LUCK_WEIGHTS
    else:
        weights = BASE_WEIGHTS

    async with transaction() as db:
        card = None

        if force_duplicate:
            async with db.execute(
                """
                SELECT c.id,
                       c.file_id,
                       c.rarity,
                       c.description
                FROM inventory AS i
                JOIN cards AS c
                  ON c.id = i.card_id
                WHERE i.user_id = ?
                  AND i.count >= 1
                ORDER BY RANDOM()
                LIMIT 1
                """,
                (user_id,),
            ) as cursor:
                card = await cursor.fetchone()

        if not card:
            card = await choose_card(
                db,
                weights,
            )

        if not card:
            return None

        card_id = safe_int(
            card["id"]
        )
        rarity = str(
            card["rarity"]
        )

        duplicate = await add_card(
            db,
            user_id,
            card_id,
        )

        compensation = 0

        if duplicate and vip:
            compensation = DUPLICATE_COMPENSATION.get(
                rarity,
                5,
            )

            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins + ?
                WHERE user_id = ?
                """,
                (
                    compensation,
                    user_id,
                ),
            )

        await add_experience(
            db,
            user_id,
            2,
        )

        return {
            "id": card_id,
            "file_id": card["file_id"],
            "rarity": rarity,
            "description": (
                card["description"]
                or ""
            ),
            "duplicate": duplicate,
            "compensation": compensation,
        }


def card_caption(
    card: dict[str, Any],
    *,
    second: bool = False,
) -> str:
    if second:
        title = (
            "👑💖 <b>VIP-БОНУС: ВТОРАЯ КАРТА!</b> 💖👑"
        )
    else:
        title = (
            "✨🎴 <b>ВАМ ВЫПАЛА КАРТА!</b> 🎴✨"
        )

    text = (
        f"{title}\n\n"
        f"🎴 Номер: <b>#{safe_int(card['id'])}</b>\n"
        f"🏷 Редкость: <b>{esc(card['rarity'])}</b>\n"
        f"📝 Описание: "
        f"<b>{esc(card['description']) or 'Без описания'}</b>\n"
    )

    if card["duplicate"]:
        text += (
            "\n🔁 <i>Дубликат! Можно использовать "
            "для крафта или трейда.</i>"
        )
    else:
        text += (
            "\n🆕 <i>Новая карточка в коллекции!</i>"
        )

    if card["compensation"]:
        text += (
            "\n💰 VIP-компенсация: "
            f"<b>+{safe_int(card['compensation'])} 🪙</b>"
        )

    text += "\n💫 Опыт: <b>+2</b>"

    return text


# ------------------------------------------------------------
# КОМАНДА ФЕМ
# ------------------------------------------------------------

async def get_fem_cooldown(
    db: aiosqlite.Connection,
    user_id: int,
    current_time: float,
) -> float:
    async with db.execute(
        """
        SELECT custom_cd_hours,
               custom_cd_expires,
               vip_until
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    ) as cursor:
        row = await cursor.fetchone()

    if not row:
        return 3 * 3600

    custom_expires = safe_float(
        row["custom_cd_expires"]
    )

    custom_hours = safe_float(
        row["custom_cd_hours"],
        3,
    )

    if (
        custom_expires == -1
        or custom_expires > current_time
    ):
        return max(
            0,
            custom_hours * 3600,
        )

    if safe_float(row["vip_until"]) > current_time:
        return 2 * 3600

    return 3 * 3600


@router.message(
    lambda message: command_is(
        message,
        "фем",
        "fem",
        "foto",
    )
)
async def fem_command(
    message: Message,
) -> None:
    user_id = message.from_user.id
    lock = get_lock(
        user_locks,
        user_id,
    )

    async with lock:
        current_time = now()

        async with transaction() as db:
            async with db.execute(
                """
                SELECT last_fem_time,
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
                await message.answer(
                    "💔 Профиль не найден!"
                )
                return

            attack_block = safe_float(
                user["attack_block_until"]
            )

            if attack_block > current_time:
                await message.answer(
                    "🚫💔 <b>Сбор карт запрещён!</b>\n"
                    f"Осталось: "
                    f"<b>{fmt_seconds(attack_block - current_time)}</b>"
                )
                return

            cooldown = await get_fem_cooldown(
                db,
                user_id,
                current_time,
            )

            last_fem = safe_float(
                user["last_fem_time"]
            )

            elapsed = current_time - last_fem

            skips = safe_int(
                user["cd_resets_count"]
            )

            skip_expires = safe_float(
                user["cd_reset_expires"]
            )

            if skip_expires <= current_time:
                skips = 0

            if elapsed < cooldown:
                if skips <= 0:
                    await message.answer(
                        "⏳💗 <b>Ещё рано!</b>\n"
                        f"До следующего фема: "
                        f"<b>{fmt_seconds(cooldown - elapsed)}</b>"
                    )
                    return

                skips -= 1

                await db.execute(
                    """
                    UPDATE users
                    SET cd_resets_count = ?,
                        cd_reset_expires = ?
                    WHERE user_id = ?
                    """,
                    (
                        skips,
                        skip_expires if skips else 0,
                        user_id,
                    ),
                )

            await db.execute(
                """
                UPDATE users
                SET last_fem_time = ?
                WHERE user_id = ?
                """,
                (
                    current_time,
                    user_id,
                ),
            )

            vip = (
                safe_float(user["vip_until"])
                > current_time
            )

            lucky = (
                safe_float(user["luck_expires"])
                > current_time
            )

        first_card = await draw_card(
            user_id,
            vip=vip,
            lucky=lucky,
        )

        if not first_card:
            await message.answer(
                "😿🎴 В библиотеке пока нет карт!"
            )
            return

        markup = await card_bonus_keyboard()

        await message.answer_photo(
            photo=first_card["file_id"],
            caption=card_caption(first_card),
            reply_markup=markup,
        )

        if (
            vip
            and random.random() < 0.20
        ):
            second_card = await draw_card(
                user_id,
                vip=vip,
                lucky=lucky,
                force_duplicate=True,
            )

            if second_card:
                await asyncio.sleep(0.25)

                await message.answer_photo(
                    photo=second_card["file_id"],
                    caption=card_caption(
                        second_card,
                        second=True,
                    ),
                    reply_markup=markup,
                )

# ============================================================
# BLOCK 4/9 — КРАФТ ЩИТОВ, МАГАЗИН И ПЕРЕВОДЫ
# ============================================================


# ------------------------------------------------------------
# КРАФТ ЩИТОВ
# ------------------------------------------------------------

SHIELD_CRAFT_RECIPES = {
    "weak": ("reinforced", "3 слабых щита → 1 усиленный"),
    "reinforced": ("femboy", "3 усиленных щита → 1 фембойский"),
    "femboy": ("armor", "3 фембойских щита → 1 броня"),
}


def shield_craft_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔰 3 слабых → усиленный ✨",
                    callback_data="craft_shield:weak",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💎 3 усиленных → фембойский 💖",
                    callback_data="craft_shield:reinforced",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👑 3 фембойских → броня 🛡",
                    callback_data="craft_shield:femboy",
                )
            ],
        ]
    )


@router.message(
    lambda message: command_is(
        message,
        "крафт щитов",
        "крафт щит",
        "shield craft",
    )
)
async def shield_craft_command(
    message: Message,
) -> None:
    await message.answer(
        "⚒️✨ <b>КРАФТ ЩИТОВ</b> ✨⚒️\n\n"
        "Объедини 3 одинаковых щита и получи "
        "более редкий предмет!\n\n"
        "🔰 3 слабых щита = 1 усиленный\n"
        "💎 3 усиленных щита = 1 фембойский\n"
        "👑 3 фембойских щита = 1 броня",
        reply_markup=shield_craft_keyboard(),
    )


@router.callback_query(
    F.data.startswith("craft_shield:")
)
async def shield_craft_callback(
    callback: CallbackQuery,
) -> None:
    source = callback_payload(
        callback.data,
        "craft_shield:",
    )

    if source not in SHIELD_CRAFT_RECIPES:
        await answer_callback(
            callback,
            "Некорректный рецепт 💔",
            alert=True,
        )
        return

    target, recipe_text = SHIELD_CRAFT_RECIPES[source]
    user_id = callback.from_user.id

    lock = get_lock(
        user_locks,
        user_id,
    )

    async with lock:
        async with transaction() as db:
            async with db.execute(
                """
                SELECT amount
                FROM artifacts
                WHERE user_id = ?
                  AND artifact_type = 'shield'
                  AND subtype = ?
                """,
                (
                    user_id,
                    source,
                ),
            ) as cursor:
                row = await cursor.fetchone()

            available = safe_int(
                row["amount"]
                if row
                else 0
            )

            if available < 3:
                await answer_callback(
                    callback,
                    "Нужно 3 одинаковых щита 🔰",
                    alert=True,
                )
                return

            changed = await db.execute(
                """
                UPDATE artifacts
                SET amount = amount - 3
                WHERE user_id = ?
                  AND artifact_type = 'shield'
                  AND subtype = ?
                  AND amount >= 3
                """,
                (
                    user_id,
                    source,
                ),
            )

            if changed.rowcount != 1:
                await answer_callback(
                    callback,
                    "Крафт уже выполнен или щитов не хватает 💔",
                    alert=True,
                )
                return

            if target == "armor":
                await add_artifact(
                    db,
                    user_id,
                    "armor",
                    "",
                    1,
                )
            else:
                await add_artifact(
                    db,
                    user_id,
                    "shield",
                    target,
                    1,
                )

            await add_experience(
                db,
                user_id,
                2,
            )

    await answer_callback(
        callback,
        "Крафт выполнен! ✨",
        alert=True,
    )

    await callback.message.answer(
        "🎉💖 <b>КРАФТ УСПЕШЕН!</b> 💖🎉\n\n"
        f"⚒️ {esc(recipe_text)}\n"
        f"🎁 Получено: <b>{'🛡 броня' if target == 'armor' else '🔰 ' + SHIELD_TYPES[target]}</b>\n"
        "💫 Опыт: <b>+2</b>",
    )


# ------------------------------------------------------------
# МАГАЗИН
# ------------------------------------------------------------

def shop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Сбросить КД — 150 🪙",
                    callback_data="shop_item:skip",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🍀 Эликсир удачи — 250 🪙",
                    callback_data="shop_item:luck",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎴 Новая карта — 400 🪙",
                    callback_data="shop_item:card",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛡 Артефакты 🔰",
                    callback_data="shop_menu:artifacts",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👑 Купить VIP ✨",
                    callback_data="shop_menu:vip",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Купить фемкоины ⭐",
                    callback_data="shop_menu:coins",
                )
            ],
        ]
    )


async def shop_text(
    user_id: int,
) -> str:
    async with db_connection() as db:
        async with db.execute(
            """
            SELECT femcoins
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

    balance = safe_int(
        row["femcoins"]
        if row
        else 0
    )

    return (
        "🏪✨ <b>ФЕМБОЙСКИЙ МАГАЗИН</b> ✨🏪\n\n"
        f"💰 Ваш баланс: <b>{balance} 🪙</b>\n\n"
        "Выбирай, что хочешь приобрести! 💖"
    )


@router.message(
    lambda message: command_is(
        message,
        "магазин",
        "shop",
    )
)
async def shop_command(
    message: Message,
) -> None:
    if message.chat.type != ChatType.PRIVATE:
        bot_info = await bot.get_me()

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🏪 Открыть магазин в ЛС 💖",
                        url=(
                            f"https://t.me/{bot_info.username}"
                            "?start=shop"
                        ),
                    )
                ]
            ]
        )

        await message.answer(
            "🏪 Магазин работает только в личных сообщениях!",
            reply_markup=markup,
        )
        return

    await message.answer(
        await shop_text(message.from_user.id),
        reply_markup=shop_keyboard(),
    )


@router.callback_query(
    F.data == "shop:main"
)
async def shop_main_callback(
    callback: CallbackQuery,
) -> None:
    await edit_text_safely(
        callback.message,
        await shop_text(callback.from_user.id),
        shop_keyboard(),
    )

    await answer_callback(callback)


def artifact_shop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔰 Слабый щит — 30 🪙",
                    callback_data="artifact_buy:weak",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛡 Броня — 300 🪙",
                    callback_data="artifact_buy:armor",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад в магазин",
                    callback_data="shop:main",
                )
            ],
        ]
    )


@router.callback_query(
    F.data == "shop_menu:artifacts"
)
async def artifact_shop_callback(
    callback: CallbackQuery,
) -> None:
    await edit_text_safely(
        callback.message,
        "🛡✨ <b>МАГАЗИН АРТЕФАКТОВ</b> ✨🛡\n\n"
        "🔰 Слабый щит можно купить напрямую.\n"
        "💎 Усиленный и фембойский щиты "
        "получаются только крафтом.",
        artifact_shop_keyboard(),
    )

    await answer_callback(callback)


@router.callback_query(
    F.data.startswith("artifact_buy:")
)
async def artifact_buy_callback(
    callback: CallbackQuery,
) -> None:
    product = callback_payload(
        callback.data,
        "artifact_buy:",
    )

    prices = {
        "weak": 30,
        "armor": 300,
    }

    if product not in prices:
        await answer_callback(
            callback,
            "Товар не найден 💔",
            alert=True,
        )
        return

    price = prices[product]
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
            row = await cursor.fetchone()

        balance = safe_int(
            row["femcoins"]
            if row
            else 0
        )

        if balance < price:
            await answer_callback(
                callback,
                "Недостаточно фемкоинов 🪙",
                alert=True,
            )
            return

        updated = await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins - ?
            WHERE user_id = ?
              AND femcoins >= ?
            """,
            (
                price,
                user_id,
                price,
            ),
        )

        if updated.rowcount != 1:
            await answer_callback(
                callback,
                "Баланс изменился, повторите покупку 💔",
                alert=True,
            )
            return

        if product == "weak":
            await add_artifact(
                db,
                user_id,
                "shield",
                "weak",
                1,
            )
        else:
            await add_artifact(
                db,
                user_id,
                "armor",
                "",
                1,
            )

    title = (
        "🔰 слабый щит"
        if product == "weak"
        else "🛡 броня"
    )

    await answer_callback(
        callback,
        f"Куплено: {title}! ✨",
        alert=True,
    )


@router.callback_query(
    F.data.startswith("shop_item:")
)
async def shop_item_callback(
    callback: CallbackQuery,
) -> None:
    item = callback_payload(
        callback.data,
        "shop_item:",
    )

    user_id = callback.from_user.id
    current_time = now()

    prices = {
        "skip": 150,
        "luck": 250,
        "card": 400,
    }

    if item not in prices:
        await answer_callback(
            callback,
            "Товар не найден 💔",
            alert=True,
        )
        return

    price = prices[item]

    async with transaction() as db:
        async with db.execute(
            """
            SELECT femcoins,
                   luck_expires,
                   cd_resets_count,
                   cd_reset_expires
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        if not user:
            await answer_callback(
                callback,
                "Профиль не найден 💔",
                alert=True,
            )
            return

        if safe_int(user["femcoins"]) < price:
            await answer_callback(
                callback,
                "Недостаточно фемкоинов 🪙",
                alert=True,
            )
            return

        if item == "card":
            async with db.execute(
                """
                SELECT id,
                       file_id,
                       rarity,
                       description
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
                await answer_callback(
                    callback,
                    "Все карты уже собраны 🎴",
                    alert=True,
                )
                return

            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins - ?
                WHERE user_id = ?
                  AND femcoins >= ?
                """,
                (
                    price,
                    user_id,
                    price,
                ),
            )

            await db.execute(
                """
                INSERT INTO inventory(
                    user_id,
                    card_id,
                    count
                )
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, card_id)
                DO UPDATE SET count = count + 1
                """,
                (
                    user_id,
                    safe_int(card["id"]),
                ),
            )

            await add_experience(
                db,
                user_id,
                2,
            )

            card_to_send = dict(card)

        elif item == "skip":
            old_expires = safe_float(
                user["cd_reset_expires"]
            )
            old_count = safe_int(
                user["cd_resets_count"]
            )

            if old_expires <= current_time:
                old_count = 0

            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins - ?,
                    cd_resets_count = ?,
                    cd_reset_expires = ?
                WHERE user_id = ?
                  AND femcoins >= ?
                """,
                (
                    price,
                    old_count + 1,
                    current_time + 3 * 3600,
                    user_id,
                    price,
                ),
            )

            card_to_send = None

        else:
            old_luck = safe_float(
                user["luck_expires"]
            )

            luck_start = max(
                current_time,
                old_luck,
            )

            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins - ?,
                    luck_expires = ?
                WHERE user_id = ?
                  AND femcoins >= ?
                """,
                (
                    price,
                    luck_start + 3 * 3600,
                    user_id,
                    price,
                ),
            )

            card_to_send = None

    if item == "skip":
        await answer_callback(
            callback,
            "⚡ Скип КД куплен!",
            alert=True,
        )
        return

    if item == "luck":
        await answer_callback(
            callback,
            "🍀 Удача активирована на 3 часа!",
            alert=True,
        )
        return

    if card_to_send:
        await callback.message.answer_photo(
            photo=card_to_send["file_id"],
            caption=(
                "🎁✨ <b>НОВАЯ КАРТА КУПЛЕНА!</b> ✨🎁\n\n"
                f"🎴 Карта: <b>#{safe_int(card_to_send['id'])}</b>\n"
                f"🏷 Редкость: "
                f"<b>{esc(card_to_send['rarity'])}</b>\n"
                f"📝 {esc(card_to_send['description']) or 'Без описания'}\n"
                "💫 Опыт: <b>+2</b>"
            ),
            reply_markup=await card_bonus_keyboard(),
        )

    await answer_callback(
        callback,
        "Новая карта куплена! 🎴",
        alert=True,
    )


# ------------------------------------------------------------
# ПЕРЕВОДЫ ФЕМКОИНОВ
# ------------------------------------------------------------

async def find_user_by_username(
    username: str,
) -> Optional[int]:
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

    return (
        safe_int(row["user_id"])
        if row
        else None
    )


async def resolve_target_user(
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
async def transfer_command(
    message: Message,
) -> None:
    if not message.text:
        return

    amount = None

    for part in message.text.split()[1:]:
        if part.isdigit():
            amount = int(part)
            break

    if not amount or amount <= 0:
        await message.answer(
            "💸 Укажи положительную сумму!\n"
            "Пример: <code>передать 100 @username</code>"
        )
        return

    target_id = await resolve_target_user(message)
    sender_id = message.from_user.id

    if not target_id:
        await message.answer(
            "🎯 Укажи получателя ответом "
            "или через @username!"
        )
        return

    if target_id in {
        sender_id,
        bot.id,
    }:
        await message.answer(
            "❌ Нельзя переводить монеты себе или боту!"
        )
        return

    if not await user_exists(target_id):
        await message.answer(
            "❌ Получатель ещё не зарегистрирован!"
        )
        return

    vip = await is_vip(sender_id)

    commission_percent = 5 if vip else 10
    commission = max(
        1,
        amount * commission_percent // 100,
    )
    received = amount - commission

    if received <= 0:
        await message.answer(
            "❌ Сумма слишком мала после комиссии!"
        )
        return

    async with transaction() as db:
        updated = await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins - ?
            WHERE user_id = ?
              AND femcoins >= ?
            """,
            (
                amount,
                sender_id,
                amount,
            ),
        )

        if updated.rowcount != 1:
            await message.answer(
                "❌ Недостаточно фемкоинов!"
            )
            return

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins + ?
            WHERE user_id = ?
            """,
            (
                received,
                target_id,
            ),
        )

        await add_experience(
            db,
            sender_id,
            1,
        )

    await message.answer(
        "💸✨ <b>ПЕРЕВОД ВЫПОЛНЕН!</b> ✨💸\n\n"
        f"📤 Отправлено: <b>{amount} 🪙</b>\n"
        f"📥 Получено: <b>{received} 🪙</b>\n"
        f"🔥 Комиссия: <b>{commission} 🪙</b>\n"
        f"👑 Комиссия VIP: "
        f"<b>{commission_percent}%</b>"
        if vip
        else
        "💸✨ <b>ПЕРЕВОД ВЫПОЛНЕН!</b> ✨💸\n\n"
        f"📤 Отправлено: <b>{amount} 🪙</b>\n"
        f"📥 Получено: <b>{received} 🪙</b>\n"
        f"🔥 Комиссия: <b>{commission} 🪙</b>"
    )
# ============================================================
# BLOCK 5/9 — КАЗИНО И ПРОМОКОДЫ
# ============================================================


# ------------------------------------------------------------
# КАЗИНО
# ------------------------------------------------------------

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
            [
                InlineKeyboardButton(
                    text=f"🎲 Кости — ставка {bet} 🪙",
                    callback_data="casino_game:dice",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🔴 Красное ×2 — {bet}",
                    callback_data="casino_game:red",
                ),
                InlineKeyboardButton(
                    text=f"⚫ Чёрное ×2 — {bet}",
                    callback_data="casino_game:black",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"🟢 Зеро ×14 — {bet}",
                    callback_data="casino_game:zero",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🎰 Слоты — ставка {bet} 🪙",
                    callback_data="casino_game:slots",
                )
            ],
        ]
    )


def casino_text(user_id: int, bet: int) -> str:
    return (
        "🎰✨ <b>ФЕМБОЙСКОЕ КАЗИНО</b> ✨🎰\n\n"
        f"💰 Текущая ставка: <b>{bet} 🪙</b>\n\n"
        "🎲 Кости — победа даёт чистую прибыль ставки!\n"
        "🔴 Красное / ⚫ чёрное — множитель ×2!\n"
        "🟢 Зеро — множитель ×14!\n"
        "🎰 Три 7️⃣ — множитель ×15!"
    )


@router.message(
    lambda message: command_is(message, "казино", "kaz")
)
async def casino_command(message: Message) -> None:
    user_id = message.from_user.id
    bet = casino_bets.get(user_id, 20)

    await message.answer(
        casino_text(user_id, bet),
        reply_markup=casino_keyboard(bet),
    )


@router.callback_query(
    F.data.startswith("casino_bet:")
)
async def casino_bet_callback(
    callback: CallbackQuery,
) -> None:
    raw = callback_payload(
        callback.data,
        "casino_bet:",
    )

    try:
        bet = int(raw or "0")
    except ValueError:
        bet = 0

    if bet not in CASINO_BETS:
        await answer_callback(
            callback,
            "Некорректная ставка 💔",
            alert=True,
        )
        return

    casino_bets[callback.from_user.id] = bet

    await edit_text_safely(
        callback.message,
        casino_text(callback.from_user.id, bet),
        casino_keyboard(bet),
    )

    await answer_callback(
        callback,
        f"Ставка выбрана: {bet} 🪙",
    )


@router.callback_query(
    F.data.startswith("casino_game:")
)
async def casino_game_callback(
    callback: CallbackQuery,
) -> None:
    action = callback_payload(
        callback.data,
        "casino_game:",
    )

    if action not in {
        "dice",
        "red",
        "black",
        "zero",
        "slots",
    }:
        await answer_callback(
            callback,
            "Неизвестная игра 💔",
            alert=True,
        )
        return

    user_id = callback.from_user.id
    bet = casino_bets.get(user_id, 20)
    lock = get_lock(casino_locks, user_id)

    async with lock:
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

            if not user or safe_int(user["femcoins"]) < bet:
                await answer_callback(
                    callback,
                    "Недостаточно фемкоинов 🪙",
                    alert=True,
                )
                return

            if action in {"red", "black", "zero"} and bet < 20:
                await answer_callback(
                    callback,
                    "Для рулетки минимум 20 🪙",
                    alert=True,
                )
                return

            # Сначала списываем ставку.
            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins - ?
                WHERE user_id = ?
                  AND femcoins >= ?
                """,
                (bet, user_id, bet),
            )

            if action == "dice":
                player_roll = random.randint(1, 6)
                bot_roll = random.randint(1, 6)

                if player_roll > bot_roll:
                    await db.execute(
                        """
                        UPDATE users
                        SET femcoins = femcoins + ?
                        WHERE user_id = ?
                        """,
                        (bet * 2, user_id),
                    )
                    result = (
                        f"🎲 Выпало: {player_roll} против {bot_roll}!\n"
                        f"🎉 Победа игрока "
                        f"<b>{user_name(callback.from_user)}</b>!\n"
                        f"💰 Чистая прибыль: <b>+{bet} 🪙</b>"
                    )
                elif player_roll == bot_roll:
                    await db.execute(
                        """
                        UPDATE users
                        SET femcoins = femcoins + ?
                        WHERE user_id = ?
                        """,
                        (bet, user_id),
                    )
                    result = (
                        f"🎲 Ничья: {player_roll} и {bot_roll}!\n"
                        "💰 Ставка возвращена."
                    )
                else:
                    result = (
                        f"🎲 Выпало: {player_roll} против {bot_roll}!\n"
                        f"💔 Игрок "
                        f"<b>{user_name(callback.from_user)}</b> "
                        f"проиграл <b>{bet} 🪙</b>."
                    )

            elif action in {"red", "black", "zero"}:
                number = random.randint(0, 36)

                if number == 0:
                    color = "zero"
                    color_text = "🟢 зеро"
                elif number % 2:
                    color = "red"
                    color_text = "🔴 красное"
                else:
                    color = "black"
                    color_text = "⚫ чёрное"

                if color == action:
                    multiplier = 14 if action == "zero" else 2
                    total_return = bet * multiplier

                    await db.execute(
                        """
                        UPDATE users
                        SET femcoins = femcoins + ?
                        WHERE user_id = ?
                        """,
                        (total_return, user_id),
                    )

                    result = (
                        f"🎡 Выпало число <b>{number}</b> "
                        f"({color_text})!\n"
                        f"🎉 Победа игрока "
                        f"<b>{user_name(callback.from_user)}</b>!\n"
                        f"💰 Чистая прибыль: "
                        f"<b>+{total_return - bet} 🪙</b>"
                    )
                else:
                    result = (
                        f"🎡 Выпало число <b>{number}</b> "
                        f"({color_text})!\n"
                        f"💔 Игрок потерял <b>{bet} 🪙</b>."
                    )

            else:
                symbols = ["🍒", "🍋", "🍇", "💎", "7️⃣"]
                slots = random.choices(symbols, k=3)

                if slots == ["7️⃣", "7️⃣", "7️⃣"]:
                    multiplier = 15
                elif slots[0] == slots[1] == slots[2]:
                    multiplier = 4
                else:
                    multiplier = 0

                if multiplier:
                    total_return = bet * multiplier

                    await db.execute(
                        """
                        UPDATE users
                        SET femcoins = femcoins + ?
                        WHERE user_id = ?
                        """,
                        (total_return, user_id),
                    )

                    result = (
                        f"🎰 {' | '.join(slots)}\n"
                        f"🎉 Игрок "
                        f"<b>{user_name(callback.from_user)}</b> "
                        f"выиграл!\n"
                        f"💰 Прибыль: "
                        f"<b>+{total_return - bet} 🪙</b>"
                    )
                else:
                    result = (
                        f"🎰 {' | '.join(slots)}\n"
                        f"💔 Игрок "
                        f"<b>{user_name(callback.from_user)}</b> "
                        f"проиграл <b>{bet} 🪙</b>."
                    )

            await add_experience(db, user_id, 1)

    # Ответ именно на сообщение с меню казино.
    try:
        await callback.message.reply(result)
    except Exception:
        logger.exception("Ошибка публикации результата казино")

    await answer_callback(callback)


# ------------------------------------------------------------
# ПРОМОКОДЫ
# ------------------------------------------------------------

@router.message(
    lambda message: command_is(message, "промо", "promo")
)
async def promo_command(
    message: Message,
    state: FSMContext,
) -> None:
    await state.set_state(UserStates.waiting_promo)

    await message.answer(
        "🔑✨ <b>АКТИВАЦИЯ ПРОМОКОДА</b> ✨🔑\n\n"
        "Отправь промокод следующим сообщением!"
    )


@router.message(UserStates.waiting_promo)
async def promo_activate_handler(
    message: Message,
    state: FSMContext,
) -> None:
    if not message.text:
        await message.answer(
            "❌ Промокод должен быть текстом!"
        )
        return

    code = message.text.strip().upper()
    user_id = message.from_user.id
    current_time = now()

    async with transaction() as db:
        async with db.execute(
            """
            SELECT p_type,
                   reward_type,
                   reward_val,
                   expires_at,
                   is_used
            FROM promo_codes
            WHERE UPPER(code) = ?
            """,
            (code,),
        ) as cursor:
            promo = await cursor.fetchone()

        if not promo:
            await state.clear()
            await message.answer(
                "❌ Такого промокода нет!"
            )
            return

        if safe_float(promo["expires_at"]) <= current_time:
            await state.clear()
            await message.answer(
                "⌛ Срок действия промокода истёк!"
            )
            return

        promo_type = str(
            promo["p_type"]
        ).upper()

        if promo_type == "L" and safe_int(
            promo["is_used"]
        ):
            await state.clear()
            await message.answer(
                "❌ Этот одноразовый код уже использован!"
            )
            return

        async with db.execute(
            """
            SELECT 1
            FROM promo_claims
            WHERE user_id = ?
              AND code = ?
            """,
            (user_id, code),
        ) as cursor:
            already_claimed = await cursor.fetchone()

        if already_claimed:
            await state.clear()
            await message.answer(
                "❌ Ты уже активировал этот код!"
            )
            return

        reward_type = str(
            promo["reward_type"]
        ).lower()
        reward_value = safe_int(
            promo["reward_val"]
        )

        reward_text = ""

        if reward_type == "f":
            await db.execute(
                """
                UPDATE users
                SET femcoins = femcoins + ?
                WHERE user_id = ?
                """,
                (reward_value, user_id),
            )
            reward_text = (
                f"+{reward_value} 🪙 фемкоинов"
            )

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
            reward_text = "⚡ +1 скип КД"

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

            old_vip = safe_float(
                user["vip_until"]
                if user
                else 0
            )

            new_vip = max(
                current_time,
                old_vip,
            ) + reward_value * 3600

            await db.execute(
                """
                UPDATE users
                SET vip_until = ?
                WHERE user_id = ?
                """,
                (new_vip, user_id),
            )

            reward_text = (
                f"👑 VIP на {reward_value} часов"
            )

        else:
            await state.clear()
            await message.answer(
                "❌ Неизвестный тип награды!"
            )
            return

        await db.execute(
            """
            INSERT INTO promo_claims(
                user_id,
                code,
                claimed_at
            )
            VALUES (?, ?, ?)
            """,
            (
                user_id,
                code,
                current_time,
            ),
        )

        if promo_type == "L":
            await db.execute(
                """
                UPDATE promo_codes
                SET is_used = 1
                WHERE code = ?
                """,
                (code,),
            )

        await add_experience(
            db,
            user_id,
            1,
        )

    await state.clear()

    await message.answer(
        "🎉✨ <b>ПРОМОКОД АКТИВИРОВАН!</b> ✨🎉\n\n"
        f"🎁 Награда: <b>{esc(reward_text)}</b>"
    )


@router.message(
    lambda message: (
        message.text
        and message.text.lower().startswith("/create_p")
    )
)
async def create_promo_command(
    message: Message,
) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    parts = message.text.split()

    if len(parts) != 5:
        await message.answer(
            "🧾 <b>Формат команды:</b>\n\n"
            "<code>/create_p M v 5 24</code>\n\n"
            "🔹 M — многоразовый код для разных игроков\n"
            "🔹 L — одноразовый код\n"
            "🔹 f — фемкоины\n"
            "🔹 k — скип КД\n"
            "🔹 v — VIP в часах\n"
            "🔹 4-й аргумент — размер награды\n"
            "🔹 5-й аргумент — срок действия кода в часах"
        )
        return

    promo_type = parts[1].upper()
    reward_type = parts[2].lower()

    if promo_type not in {"M", "L"}:
        await message.answer(
            "❌ Первый параметр должен быть M или L!"
        )
        return

    if reward_type not in {"f", "k", "v"}:
        await message.answer(
            "❌ Тип награды должен быть f, k или v!"
        )
        return

    try:
        reward_value = int(parts[3])
        lifetime_hours = float(parts[4])
    except ValueError:
        await message.answer(
            "❌ Размер и срок должны быть числами!"
        )
        return

    if reward_value <= 0 or lifetime_hours <= 0:
        await message.answer(
            "❌ Значения должны быть больше нуля!"
        )
        return

    code = make_token(10).upper()
    expires_at = now() + lifetime_hours * 3600

    async with transaction() as db:
        await db.execute(
            """
            INSERT INTO promo_codes(
                code,
                p_type,
                reward_type,
                reward_val,
                expires_at,
                is_used
            )
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (
                code,
                promo_type,
                reward_type,
                reward_value,
                expires_at,
            ),
        )

    await log_action(
        message.from_user.id,
        f"Создан промокод {code}",
    )

    await message.answer(
        "✅✨ <b>ПРОМОКОД СОЗДАН!</b> ✨✅\n\n"
        f"🔑 Код: <code>{esc(code)}</code>\n"
        f"🎁 Награда: <b>{reward_value}</b>\n"
        f"⏳ Код сгорит через: "
        f"<b>{lifetime_hours:g} ч.</b>"
    )

# ============================================================
# BLOCK 6/9 — КРАФТ КАРТ И ТРЕЙД
# ============================================================


# ------------------------------------------------------------
# КРАФТ КАРТ
# ------------------------------------------------------------

def card_craft_keyboard(
    rarities: list[str],
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        f"⚒️ {rarity} "
                        f"➡️ {RARITIES[RARITY_ORDER[rarity] + 1]}"
                    ),
                    callback_data=f"craft_card:{rarity}",
                )
            ]
            for rarity in rarities
            if RARITY_ORDER.get(rarity, -1)
            < len(RARITIES) - 1
        ]
    )


@router.message(
    lambda message: command_is(
        message,
        "крафт",
        "craft",
    )
)
async def card_craft_command(
    message: Message,
) -> None:
    user_id = message.from_user.id

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT c.rarity,
                   COALESCE(SUM(i.count - 1), 0) AS duplicates
            FROM inventory AS i
            JOIN cards AS c
              ON c.id = i.card_id
            WHERE i.user_id = ?
              AND i.count > 1
            GROUP BY c.rarity
            HAVING duplicates >= 3
            """,
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()

    available = [
        str(row["rarity"])
        for row in rows
        if str(row["rarity"]) in RARITY_ORDER
        and RARITY_ORDER[str(row["rarity"])]
        < len(RARITIES) - 1
    ]

    if not available:
        await message.answer(
            "💔✨ <b>Недостаточно дубликатов!</b>\n\n"
            "Для крафта нужны 3 свободные копии карт "
            "одной редкости.\n"
            "Основная копия каждой карты не расходуется."
        )
        return

    await message.answer(
        "⚒️✨ <b>КРАФТ КАРТ</b> ✨⚒️\n\n"
        "Выбери редкость для объединения:",
        reply_markup=card_craft_keyboard(available),
    )


@router.callback_query(
    F.data.startswith("craft_card:")
)
async def card_craft_callback(
    callback: CallbackQuery,
) -> None:
    rarity = callback_payload(
        callback.data,
        "craft_card:",
    )

    if rarity not in RARITY_ORDER:
        await answer_callback(
            callback,
            "Некорректная редкость 💔",
            alert=True,
        )
        return

    current_index = RARITY_ORDER[rarity]

    if current_index >= len(RARITIES) - 1:
        await answer_callback(
            callback,
            "Следующей редкости нет!",
            alert=True,
        )
        return

    target_rarity = RARITIES[current_index + 1]
    user_id = callback.from_user.id

    async with get_lock(user_locks, user_id):
        async with transaction() as db:
            async with db.execute(
                """
                SELECT i.id, i.count
                FROM inventory AS i
                JOIN cards AS c
                  ON c.id = i.card_id
                WHERE i.user_id = ?
                  AND c.rarity = ?
                  AND i.count > 1
                """,
                (
                    user_id,
                    rarity,
                ),
            ) as cursor:
                inventory_rows = await cursor.fetchall()

            free_duplicates = sum(
                max(0, safe_int(row["count"]) - 1)
                for row in inventory_rows
            )

            if free_duplicates < 3:
                await answer_callback(
                    callback,
                    "Дубликатов уже недостаточно 💔",
                    alert=True,
                )
                return

            async with db.execute(
                """
                SELECT id,
                       file_id,
                       rarity,
                       description
                FROM cards
                WHERE rarity = ?
                ORDER BY RANDOM()
                LIMIT 1
                """,
                (target_rarity,),
            ) as cursor:
                target_card = await cursor.fetchone()

            if not target_card:
                await answer_callback(
                    callback,
                    "Карт следующей редкости пока нет!",
                    alert=True,
                )
                return

            remaining = 3

            for row in inventory_rows:
                free_here = max(
                    0,
                    safe_int(row["count"]) - 1,
                )
                take = min(
                    remaining,
                    free_here,
                )

                if take:
                    await db.execute(
                        """
                        UPDATE inventory
                        SET count = count - ?
                        WHERE id = ?
                          AND count >= ?
                        """,
                        (
                            take,
                            safe_int(row["id"]),
                            take + 1,
                        ),
                    )

                remaining -= take

                if remaining == 0:
                    break

            # С крафта на Легендарную и выше — шанс 35%.
            lottery = (
                current_index
                >= RARITY_ORDER["Легендарное"]
            )

            success = (
                random.random() < 0.35
                if lottery
                else True
            )

            if not success:
                coins = random.choices(
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
                    (
                        coins,
                        user_id,
                    ),
                )

                await add_experience(
                    db,
                    user_id,
                    2,
                )

                result_text = (
                    "💥💔 <b>КРАФТ НЕ УДАЛСЯ!</b>\n\n"
                    f"⚒️ Потрачено дубликатов: <b>3</b>\n"
                    f"💰 Компенсация: <b>+{coins} 🪙</b>\n"
                    "💫 Опыт: <b>+2</b>"
                )
            else:
                await db.execute(
                    """
                    INSERT INTO inventory(
                        user_id,
                        card_id,
                        count
                    )
                    VALUES (?, ?, 1)
                    ON CONFLICT(user_id, card_id)
                    DO UPDATE SET count = count + 1
                    """,
                    (
                        user_id,
                        safe_int(target_card["id"]),
                    ),
                )

                await add_experience(
                    db,
                    user_id,
                    2,
                )

                result_text = (
                    "🎉💖 <b>КРАФТ УСПЕШЕН!</b> 💖🎉\n\n"
                    f"🎴 Получена карта "
                    f"<b>#{safe_int(target_card['id'])}</b>\n"
                    f"🏷 Редкость: "
                    f"<b>{esc(target_rarity)}</b>\n"
                    f"📝 "
                    f"{esc(target_card['description']) or 'Без описания'}\n"
                    "💫 Опыт: <b>+2</b>"
                )

    await callback.message.answer(
        result_text
    )

    await answer_callback(
        callback,
        "Крафт обработан! ✨",
    )


# ------------------------------------------------------------
# ТРЕЙД
# ------------------------------------------------------------

def trade_invite_keyboard(
    trade_id: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🤝 Принять обмен",
                    callback_data=f"trade_accept:{trade_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"trade_cancel:{trade_id}",
                )
            ],
        ]
    )


def trade_action_keyboard(
    trade_id: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎴 Выбрать карту",
                    callback_data=f"trade_pick:{trade_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"trade_confirm:{trade_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить обмен",
                    callback_data=f"trade_cancel:{trade_id}",
                )
            ],
        ]
    )


@router.message(
    lambda message: command_is(
        message,
        "трейд",
        "trade",
    )
)
async def trade_start_command(
    message: Message,
) -> None:
    if not message.reply_to_message:
        await message.answer(
            "🤝 Команду нужно отправить ответом "
            "на сообщение игрока!"
        )
        return

    target = message.reply_to_message.from_user
    sender = message.from_user

    if not target:
        return

    if target.is_bot or target.id == sender.id:
        await message.answer(
            "❌ Нельзя торговать с собой или ботом!"
        )
        return

    await ensure_user(sender)
    await ensure_user(target)

    trade_id = make_token(24)

    async with transaction() as db:
        await db.execute(
            """
            INSERT INTO trades(
                trade_id,
                p1,
                p2,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (
                trade_id,
                sender.id,
                target.id,
                now(),
                now(),
            ),
        )

    await message.answer(
        "🤝✨ <b>ПРЕДЛОЖЕНИЕ ОБМЕНА!</b> ✨🤝\n\n"
        f"👤 Отправитель: {user_name(sender)}\n"
        f"🎯 Получатель: {user_name(target)}\n\n"
        "Оба игрока выбирают по одному дубликату.\n"
        "Редкости карт должны совпадать!",
        reply_markup=trade_invite_keyboard(trade_id),
    )


async def get_trade(
    trade_id: str,
) -> Optional[aiosqlite.Row]:
    async with db_connection() as db:
        async with db.execute(
            """
            SELECT *
            FROM trades
            WHERE trade_id = ?
            """,
            (trade_id,),
        ) as cursor:
            return await cursor.fetchone()


@router.callback_query(
    F.data.startswith("trade_accept:")
)
async def trade_accept_callback(
    callback: CallbackQuery,
) -> None:
    trade_id = callback_payload(
        callback.data,
        "trade_accept:",
    )

    if not trade_id:
        await answer_callback(
            callback,
            "Сделка не найдена 💔",
            alert=True,
        )
        return

    async with transaction() as db:
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
            await answer_callback(
                callback,
                "Сделка не найдена 💔",
                alert=True,
            )
            return

        if callback.from_user.id != safe_int(
            trade["p2"]
        ):
            await answer_callback(
                callback,
                "Это предложение не вам!",
                alert=True,
            )
            return

        if trade["status"] != "pending":
            await answer_callback(
                callback,
                "Сделка уже закрыта!",
                alert=True,
            )
            return

        await db.execute(
            """
            UPDATE trades
            SET status = 'active',
                updated_at = ?
            WHERE trade_id = ?
              AND status = 'pending'
            """,
            (
                now(),
                trade_id,
            ),
        )

    await edit_text_safely(
        callback.message,
        "🔄✨ <b>ОБМЕН ОТКРЫТ!</b> ✨🔄\n\n"
        "Каждый участник должен выбрать "
        "по одному дубликату карты.",
        trade_action_keyboard(trade_id),
    )

    await answer_callback(
        callback,
        "Обмен принят! 🤝",
    )


@router.callback_query(
    F.data.startswith("trade_pick:")
)
async def trade_pick_callback(
    callback: CallbackQuery,
) -> None:
    trade_id = callback_payload(
        callback.data,
        "trade_pick:",
    )

    trade = await get_trade(
        trade_id or ""
    )

    if not trade or trade["status"] != "active":
        await answer_callback(
            callback,
            "Сделка закрыта 💔",
            alert=True,
        )
        return

    user_id = callback.from_user.id

    if user_id not in {
        safe_int(trade["p1"]),
        safe_int(trade["p2"]),
    }:
        await answer_callback(
            callback,
            "Ты не участник этой сделки!",
            alert=True,
        )
        return

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT c.id,
                   c.rarity,
                   i.count
            FROM inventory AS i
            JOIN cards AS c
              ON c.id = i.card_id
            WHERE i.user_id = ?
              AND i.count >= 2
            ORDER BY c.id
            """,
            (user_id,),
        ) as cursor:
            cards = await cursor.fetchall()

    if not cards:
        await answer_callback(
            callback,
            "У тебя нет свободных дубликатов!",
            alert=True,
        )
        return

    buttons = [
        [
            InlineKeyboardButton(
                text=(
                    f"🎴 #{safe_int(row['id'])} "
                    f"{row['rarity']} "
                    f"×{safe_int(row['count'])}"
                ),
                callback_data=(
                    f"trade_set:{trade_id}:"
                    f"{safe_int(row['id'])}"
                ),
            )
        ]
        for row in cards
    ]

    await callback.message.answer(
        "🎴✨ <b>ВЫБЕРИ КАРТУ ДЛЯ ОБМЕНА:</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await answer_callback(callback)


@router.callback_query(
    F.data.startswith("trade_set:")
)
async def trade_set_callback(
    callback: CallbackQuery,
) -> None:
    payload = callback_payload(
        callback.data,
        "trade_set:",
    )

    try:
        trade_id, card_id_raw = (
            payload or ""
        ).rsplit(":", 1)
        card_id = int(card_id_raw)
    except (ValueError, AttributeError):
        await answer_callback(
            callback,
            "Некорректные данные 💔",
            alert=True,
        )
        return

    user_id = callback.from_user.id

    async with transaction() as db:
        async with db.execute(
            """
            SELECT p1,
                   p2,
                   status
            FROM trades
            WHERE trade_id = ?
            """,
            (trade_id,),
        ) as cursor:
            trade = await cursor.fetchone()

        if not trade or trade["status"] != "active":
            await answer_callback(
                callback,
                "Сделка закрыта 💔",
                alert=True,
            )
            return

        if user_id not in {
            safe_int(trade["p1"]),
            safe_int(trade["p2"]),
        }:
            await answer_callback(
                callback,
                "Ты не участник сделки!",
                alert=True,
            )
            return

        async with db.execute(
            """
            SELECT count
            FROM inventory
            WHERE user_id = ?
              AND card_id = ?
            """,
            (
                user_id,
                card_id,
            ),
        ) as cursor:
            inventory = await cursor.fetchone()

        if not inventory or safe_int(
            inventory["count"]
        ) < 2:
            await answer_callback(
                callback,
                "Карта больше не является дубликатом!",
                alert=True,
            )
            return

        if user_id == safe_int(trade["p1"]):
            await db.execute(
                """
                UPDATE trades
                SET p1_card = ?,
                    p1_confirmed = 0,
                    p2_confirmed = 0,
                    updated_at = ?
                WHERE trade_id = ?
                """,
                (
                    card_id,
                    now(),
                    trade_id,
                ),
            )
        else:
            await db.execute(
                """
                UPDATE trades
                SET p2_card = ?,
                    p1_confirmed = 0,
                    p2_confirmed = 0,
                    updated_at = ?
                WHERE trade_id = ?
                """,
                (
                    card_id,
                    now(),
                    trade_id,
                ),
            )

    await answer_callback(
        callback,
        "Карта выбрана! Подтверждения сброшены 🔄",
        alert=True,
    )


@router.callback_query(
    F.data.startswith("trade_confirm:")
)
async def trade_confirm_callback(
    callback: CallbackQuery,
) -> None:
    trade_id = callback_payload(
        callback.data,
        "trade_confirm:",
    )

    if not trade_id:
        await answer_callback(
            callback,
            "Сделка не найдена 💔",
            alert=True,
        )
        return

    lock = get_lock(
        trade_locks,
        trade_id,
    )

    async with lock:
        async with transaction() as db:
            async with db.execute(
                """
                SELECT p1,
                       p2,
                       p1_card,
                       p2_card,
                       p1_confirmed,
                       p2_confirmed,
                       status
                FROM trades
                WHERE trade_id = ?
                """,
                (trade_id,),
            ) as cursor:
                trade = await cursor.fetchone()

            if not trade or trade["status"] != "active":
                await answer_callback(
                    callback,
                    "Сделка уже закрыта 💔",
                    alert=True,
                )
                return

            p1 = safe_int(trade["p1"])
            p2 = safe_int(trade["p2"])
            user_id = callback.from_user.id

            if user_id not in {p1, p2}:
                await answer_callback(
                    callback,
                    "Ты не участник сделки!",
                    alert=True,
                )
                return

            if not trade["p1_card"] or not trade["p2_card"]:
                await answer_callback(
                    callback,
                    "Оба игрока должны выбрать карты!",
                    alert=True,
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
                    (
                        now(),
                        trade_id,
                    ),
                )
            else:
                await db.execute(
                    """
                    UPDATE trades
                    SET p2_confirmed = 1,
                        updated_at = ?
                    WHERE trade_id = ?
                    """,
                    (
                        now(),
                        trade_id,
                    ),
                )

            async with db.execute(
                """
                SELECT p1_confirmed,
                       p2_confirmed,
                       p1_card,
                       p2_card
                FROM trades
                WHERE trade_id = ?
                """,
                (trade_id,),
            ) as cursor:
                updated_trade = await cursor.fetchone()

            if not (
                safe_int(updated_trade["p1_confirmed"])
                and safe_int(updated_trade["p2_confirmed"])
            ):
                await answer_callback(
                    callback,
                    "Ожидаем подтверждение второго игрока ⏳",
                )
                return

            card_one = safe_int(
                updated_trade["p1_card"]
            )
            card_two = safe_int(
                updated_trade["p2_card"]
            )

            async with db.execute(
                """
                SELECT id, rarity
                FROM cards
                WHERE id IN (?, ?)
                """,
                (
                    card_one,
                    card_two,
                ),
            ) as cursor:
                card_rows = await cursor.fetchall()

            rarity_map = {
                safe_int(row["id"]): str(row["rarity"])
                for row in card_rows
            }

            if (
                card_one not in rarity_map
                or card_two not in rarity_map
                or rarity_map[card_one]
                != rarity_map[card_two]
            ):
                await answer_callback(
                    callback,
                    "Редкости карт должны совпадать!",
                    alert=True,
                )
                return

            async with db.execute(
                """
                SELECT user_id,
                       card_id,
                       count
                FROM inventory
                WHERE (user_id = ? AND card_id = ?)
                   OR (user_id = ? AND card_id = ?)
                """,
                (
                    p1,
                    card_one,
                    p2,
                    card_two,
                ),
            ) as cursor:
                inventory_rows = await cursor.fetchall()

            inventory_map = {
                (
                    safe_int(row["user_id"]),
                    safe_int(row["card_id"]),
                ): safe_int(row["count"])
                for row in inventory_rows
            }

            if (
                inventory_map.get((p1, card_one), 0) < 2
                or inventory_map.get((p2, card_two), 0) < 2
            ):
                await answer_callback(
                    callback,
                    "Одной из карт уже не хватает!",
                    alert=True,
                )
                return

            await db.execute(
                """
                UPDATE inventory
                SET count = count - 1
                WHERE user_id = ?
                  AND card_id = ?
                  AND count >= 2
                """,
                (
                    p1,
                    card_one,
                ),
            )

            await db.execute(
                """
                UPDATE inventory
                SET count = count - 1
                WHERE user_id = ?
                  AND card_id = ?
                  AND count >= 2
                """,
                (
                    p2,
                    card_two,
                ),
            )

            await db.execute(
                """
                INSERT INTO inventory(
                    user_id,
                    card_id,
                    count
                )
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, card_id)
                DO UPDATE SET count = count + 1
                """,
                (
                    p1,
                    card_two,
                ),
            )

            await db.execute(
                """
                INSERT INTO inventory(
                    user_id,
                    card_id,
                    count
                )
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, card_id)
                DO UPDATE SET count = count + 1
                """,
                (
                    p2,
                    card_one,
                ),
            )

            await db.execute(
                """
                UPDATE trades
                SET status = 'completed',
                    updated_at = ?
                WHERE trade_id = ?
                """,
                (
                    now(),
                    trade_id,
                ),
            )

    await edit_text_safely(
        callback.message,
        "🎉💖 <b>ОБМЕН УСПЕШНО ЗАВЕРШЁН!</b> 💖🎉\n\n"
        "Карты атомарно переданы между игроками!",
    )

    await answer_callback(
        callback,
        "Обмен завершён! 🤝",
    )


@router.callback_query(
    F.data.startswith("trade_cancel:")
)
async def trade_cancel_callback(
    callback: CallbackQuery,
) -> None:
    trade_id = callback_payload(
        callback.data,
        "trade_cancel:",
    )

    trade = await get_trade(
        trade_id or ""
    )

    if not trade:
        await answer_callback(
            callback,
            "Сделка уже удалена 💨",
            alert=True,
        )
        return

    if callback.from_user.id not in {
        safe_int(trade["p1"]),
        safe_int(trade["p2"]),
    }:
        await answer_callback(
            callback,
            "Ты не участник сделки!",
            alert=True,
        )
        return

    async with transaction() as db:
        await db.execute(
            """
            UPDATE trades
            SET status = 'cancelled',
                updated_at = ?
            WHERE trade_id = ?
            """,
            (
                now(),
                trade_id,
            ),
        )

    await edit_text_safely(
        callback.message,
        "❌💔 <b>ОБМЕН ОТМЕНЁН!</b>",
    )

    await answer_callback(
        callback,
        "Сделка отменена",
    )

# ============================================================
# BLOCK 7/9 — КЛАНЫ И КЛАН-МАГАЗИН
# ============================================================

CLAN_NAME_RE = re.compile(
    r"^[А-Яа-яA-Za-z0-9(),;:!?/\-]{3,32}$"
)


def clan_create_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✨ Ввести название клана",
                    callback_data="clan_create:confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data="clan_create:cancel",
                )
            ],
        ]
    )


def clan_join_keyboard(
    clan_number: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💖 Вступить в клан",
                    callback_data=f"clan_join:{clan_number}",
                )
            ]
        ]
    )


def clan_leave_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💔 Покинуть клан",
                    callback_data="clan_leave:confirm",
                )
            ]
        ]
    )


def clan_shop_keyboard(
    owner: bool,
) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text="🍀 Эликсир для всех — кланкоины",
                callback_data="clan_shop:luck",
            )
        ],
        [
            InlineKeyboardButton(
                text="🗡 Клинок каждому — кланкоины",
                callback_data="clan_shop:blade",
            )
        ],
        [
            InlineKeyboardButton(
                text="📈 Личный опыт — кланкоины",
                callback_data="clan_shop:xp",
            )
        ],
        [
            InlineKeyboardButton(
                text="🏰 Улучшить клан",
                callback_data="clan_shop:level",
            )
        ],
        [
            InlineKeyboardButton(
                text="⭐ Купить кланкоины",
                callback_data="clan_shop:stars",
            )
        ],
    ]

    if not owner:
        buttons = [
            [
                InlineKeyboardButton(
                    text="👀 Просмотр для участника",
                    callback_data="clan_shop:readonly",
                )
            ]
        ]

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


async def get_user_clan(
    user_id: int,
) -> Optional[aiosqlite.Row]:
    async with db_connection() as db:
        async with db.execute(
            """
            SELECT c.*
            FROM users AS u
            JOIN clans AS c
              ON c.clan_id = u.clan_id
            WHERE u.user_id = ?
            """,
            (user_id,),
        ) as cursor:
            return await cursor.fetchone()


async def get_clan_by_number(
    clan_number: int,
) -> Optional[aiosqlite.Row]:
    async with db_connection() as db:
        async with db.execute(
            """
            SELECT *
            FROM clans
            WHERE clan_number = ?
            """,
            (clan_number,),
        ) as cursor:
            return await cursor.fetchone()


async def clan_members_count(
    db: aiosqlite.Connection,
    clan_id: int,
) -> int:
    async with db.execute(
        """
        SELECT COUNT(*) AS total
        FROM clan_members
        WHERE clan_id = ?
        """,
        (clan_id,),
    ) as cursor:
        row = await cursor.fetchone()

    return safe_int(row["total"] if row else 0)


async def build_clan_text(
    clan: aiosqlite.Row,
) -> str:
    clan_id = safe_int(clan["clan_id"])

    async with db_connection() as db:
        members_count = await clan_members_count(
            db,
            clan_id,
        )

        async with db.execute(
            """
            SELECT u.username,
                   u.user_id,
                   cm.role,
                   COALESCE(SUM(i.count), 0) AS cards
            FROM clan_members AS cm
            JOIN users AS u
              ON u.user_id = cm.user_id
            LEFT JOIN inventory AS i
              ON i.user_id = u.user_id
            WHERE cm.clan_id = ?
            GROUP BY u.user_id
            ORDER BY
                CASE WHEN cm.role = 'owner'
                     THEN 0 ELSE 1 END,
                cards DESC,
                cm.joined_at ASC
            LIMIT 5
            """,
            (clan_id,),
        ) as cursor:
            top_members = await cursor.fetchall()

    lines = [
        "🏳️✨ <b>КЛАНОВАЯ ИНФОРМАЦИЯ</b> ✨🏳️",
        "",
        f"🔢 Номер: <b>#{safe_int(clan['clan_number'])}</b>",
        f"🏷 Название: <b>{esc(clan['name'])}</b>",
        f"👑 Владелец: <code>{safe_int(clan['owner_id'])}</code>",
        f"👥 Участников: <b>{members_count}</b>",
        f"💰 Кланкоинов: <b>{safe_int(clan['clan_coins'])}</b>",
        f"🏰 Уровень клана: <b>{safe_int(clan['level'])}</b>",
        "",
        "🏆 <b>ТОП-5 УЧАСТНИКОВ:</b>",
    ]

    if not top_members:
        lines.append("💔 Участников пока нет.")
    else:
        for index, member in enumerate(top_members, 1):
            username = str(
                member["username"] or "Игрок"
            ).lstrip("@").split()[0]

            crown = "👑 " if index == 1 else ""

            lines.append(
                f"{index}. {crown}{esc(username)} — "
                f"<b>{safe_int(member['cards'])}</b> карт"
            )

    return "\n".join(lines)


@router.message(
    lambda message: command_is(message, "кланы", "clans")
)
async def clans_command(
    message: Message,
) -> None:
    async with db_connection() as db:
        async with db.execute(
            """
            SELECT clan_number,
                   name,
                   clan_coins,
                   level
            FROM clans
            ORDER BY clan_coins DESC,
                     level DESC,
                     clan_number ASC
            LIMIT 10
            """
        ) as cursor:
            clans = await cursor.fetchall()

    if not clans:
        await message.answer(
            "🏳️💔 Кланов пока нет!\n"
            "Создай первый клан командой "
            "<b>создать клан</b>!"
        )
        return

    lines = [
        "🏳️✨ <b>ТОП-10 КЛАНОВ</b> ✨🏳️",
        "",
    ]

    for index, clan in enumerate(clans, 1):
        lines.append(
            f"{index}. 🔢 №{safe_int(clan['clan_number'])} "
            f"<b>{esc(clan['name'])}</b> — "
            f"💰 {safe_int(clan['clan_coins'])}"
        )

    await message.answer("\n".join(lines))


@router.message(
    lambda message: (
        message.text
        and re.fullmatch(
            r"вступить в клан\s+\d+",
            message.text.strip(),
            re.IGNORECASE,
        )
    )
)
async def join_clan_command(
    message: Message,
) -> None:
    try:
        clan_number = int(
            message.text.strip().split()[-1]
        )
    except (ValueError, IndexError):
        await message.answer(
            "❌ Укажи номер клана!"
        )
        return

    clan = await get_clan_by_number(clan_number)

    if not clan:
        await message.answer(
            "💔 Клан с таким номером не найден!"
        )
        return

    await join_clan(
        message,
        clan,
    )


async def join_clan(
    message: Message,
    clan: aiosqlite.Row,
) -> None:
    user_id = message.from_user.id
    clan_id = safe_int(clan["clan_id"])

    async with transaction() as db:
        async with db.execute(
            """
            SELECT clan_id
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        if user and user["clan_id"]:
            await message.answer(
                "💔 Сначала покинь текущий клан!"
            )
            return

        await db.execute(
            """
            UPDATE users
            SET clan_id = ?
            WHERE user_id = ?
            """,
            (clan_id, user_id),
        )

        await db.execute(
            """
            INSERT OR REPLACE INTO clan_members(
                clan_id,
                user_id,
                joined_at,
                role
            )
            VALUES (?, ?, ?, 'member')
            """,
            (
                clan_id,
                user_id,
                now(),
            ),
        )

    await message.answer(
        "🎉💖 <b>ТЫ ВСТУПИЛ В КЛАН!</b>\n\n"
        f"🏳️ Клан: <b>{esc(clan['name'])}</b>\n"
        f"🔢 Номер: <b>#{safe_int(clan['clan_number'])}</b>"
    )


@router.callback_query(
    F.data.startswith("clan_join:")
)
async def clan_join_callback(
    callback: CallbackQuery,
) -> None:
    raw = callback_payload(
        callback.data,
        "clan_join:",
    )

    try:
        clan_number = int(raw or "0")
    except ValueError:
        await answer_callback(
            callback,
            "Некорректный номер",
            alert=True,
        )
        return

    clan = await get_clan_by_number(clan_number)

    if not clan:
        await answer_callback(
            callback,
            "Клан не найден",
            alert=True,
        )
        return

    await join_clan(
        callback.message,
        clan,
    )

    await answer_callback(callback)


@router.message(
    lambda message: command_is(
        message,
        "клан",
    )
    or (
        message.text
        and re.fullmatch(
            r"клан\s+\d+",
            message.text.strip(),
            re.IGNORECASE,
        )
    )
)
async def clan_info_command(
    message: Message,
) -> None:
    clan_number = None

    if message.text:
        parts = message.text.strip().split()

        if len(parts) == 2 and parts[1].isdigit():
            clan_number = int(parts[1])

    if clan_number is None:
        await message.answer(
            "🏳️ Укажи номер клана:\n"
            "<code>клан 1</code>"
        )
        return

    clan = await get_clan_by_number(clan_number)

    if not clan:
        await message.answer(
            "💔 Такой клан не найден!"
        )
        return

    await message.answer(
        await build_clan_text(clan),
        reply_markup=clan_join_keyboard(clan_number),
    )


@router.message(
    lambda message: command_is(
        message,
        "мой клан",
    )
)
async def my_clan_command(
    message: Message,
) -> None:
    clan = await get_user_clan(
        message.from_user.id
    )

    if not clan:
        await message.answer(
            "💔 Ты пока не состоишь ни в одном клане!"
        )
        return

    await message.answer(
        await build_clan_text(clan),
        reply_markup=clan_leave_keyboard(),
    )


@router.message(
    lambda message: command_is(
        message,
        "покинуть клан",
    )
)
async def leave_clan_command(
    message: Message,
) -> None:
    user_id = message.from_user.id

    clan = await get_user_clan(user_id)

    if not clan:
        await message.answer(
            "💔 Ты не состоишь в клане!"
        )
        return

    if safe_int(clan["owner_id"]) == user_id:
        await message.answer(
            "👑 Владелец не может покинуть клан.\n"
            "Сначала клан должен быть передан "
            "или удалён."
        )
        return

    async with transaction() as db:
        await db.execute(
            """
            DELETE FROM clan_members
            WHERE user_id = ?
            """,
            (user_id,),
        )

        await db.execute(
            """
            UPDATE users
            SET clan_id = NULL
            WHERE user_id = ?
            """,
            (user_id,),
        )

    await message.answer(
        "💔 Ты покинул клан!"
    )


@router.callback_query(
    F.data == "clan_leave:confirm"
)
async def clan_leave_callback(
    callback: CallbackQuery,
) -> None:
    user_id = callback.from_user.id
    clan = await get_user_clan(user_id)

    if not clan:
        await answer_callback(
            callback,
            "Ты не состоишь в клане",
            alert=True,
        )
        return

    if safe_int(clan["owner_id"]) == user_id:
        await answer_callback(
            callback,
            "Владелец не может покинуть клан",
            alert=True,
        )
        return

    async with transaction() as db:
        await db.execute(
            """
            DELETE FROM clan_members
            WHERE user_id = ?
            """,
            (user_id,),
        )

        await db.execute(
            """
            UPDATE users
            SET clan_id = NULL
            WHERE user_id = ?
            """,
            (user_id,),
        )

    await edit_text_safely(
        callback.message,
        "💔 Ты покинул клан!",
    )

    await answer_callback(callback)


@router.message(
    lambda message: command_is(
        message,
        "создать клан",
    )
)
async def create_clan_command(
    message: Message,
) -> None:
    user_id = message.from_user.id

    clan = await get_user_clan(user_id)

    if clan:
        await message.answer(
            "💔 Ты уже состоишь в клане!"
        )
        return

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT femcoins
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

    if not user or safe_int(user["femcoins"]) < 50:
        await message.answer(
            "💔 Для создания клана нужно 50 🪙!"
        )
        return

    await message.answer(
        "🏳️✨ <b>СОЗДАНИЕ КЛАНА</b> ✨🏳️\n\n"
        "Создание стоит <b>50 🪙</b>.\n"
        "После подтверждения монеты списываются "
        "и не возвращаются.\n\n"
        "Разрешены: русские и английские буквы, "
        "цифры и символы <code>(),;:!?/-</code>.",
        reply_markup=clan_create_keyboard(),
    )


@router.callback_query(
    F.data == "clan_create:confirm"
)
async def create_clan_confirm_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    user_id = callback.from_user.id

    async with transaction() as db:
        async with db.execute(
            """
            SELECT clan_id,
                   femcoins
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        if not user or user["clan_id"]:
            await answer_callback(
                callback,
                "Ты уже состоишь в клане",
                alert=True,
            )
            return

        if safe_int(user["femcoins"]) < 50:
            await answer_callback(
                callback,
                "Недостаточно фемкоинов",
                alert=True,
            )
            return

        updated = await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins - 50
            WHERE user_id = ?
              AND femcoins >= 50
            """,
            (user_id,),
        )

        if updated.rowcount != 1:
            await answer_callback(
                callback,
                "Не удалось списать монеты",
                alert=True,
            )
            return

    await state.set_state(UserStates.waiting_clan_name)

    await edit_text_safely(
        callback.message,
        "🏳️💖 <b>Отправь название клана!</b>\n\n"
        "От 3 до 32 символов.\n"
        "Разрешены буквы, цифры и символы "
        "<code>(),;:!?/-</code>.\n\n"
        "💔 Монеты уже списаны и не возвращаются.",
    )

    await answer_callback(callback)


@router.callback_query(
    F.data == "clan_create:cancel"
)
async def cancel_clan_creation_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()

    await edit_text_safely(
        callback.message,
        "❌ Создание клана отменено.",
    )

    await answer_callback(callback)


@router.message(UserStates.waiting_clan_name)
async def clan_name_handler(
    message: Message,
    state: FSMContext,
) -> None:
    if not message.text:
        await message.answer(
            "❌ Название должно быть текстом!"
        )
        return

    clan_name = message.text.strip()

    if not CLAN_NAME_RE.fullmatch(clan_name):
        await message.answer(
            "❌ Некорректное название!\n\n"
            "Разрешены только:\n"
            "🇷🇺 буквы А–Я\n"
            "🇬🇧 буквы A–Z\n"
            "🔢 цифры\n"
            "✨ символы <code>(),;:!?/-</code>\n"
            "📏 длина от 3 до 32 символов.\n\n"
            "Попробуй ещё раз!"
        )
        return

    user_id = message.from_user.id

    async with transaction() as db:
        async with db.execute(
            """
            SELECT 1
            FROM clans
            WHERE LOWER(name) = LOWER(?)
            """,
            (clan_name,),
        ) as cursor:
            exists = await cursor.fetchone()

        if exists:
            await message.answer(
                "💔 Такое название уже занято!"
            )
            return

        async with db.execute(
            """
            SELECT clan_number
            FROM clans
            ORDER BY clan_number ASC
            """
        ) as cursor:
            numbers = [
                safe_int(row["clan_number"])
                for row in await cursor.fetchall()
            ]

        clan_number = 1

        while clan_number in numbers:
            clan_number += 1

        created = now()

        cursor = await db.execute(
            """
            INSERT INTO clans(
                clan_number,
                name,
                owner_id,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                clan_number,
                clan_name,
                user_id,
                created,
            ),
        )

        clan_id = cursor.lastrowid

        await db.execute(
            """
            UPDATE users
            SET clan_id = ?
            WHERE user_id = ?
            """,
            (
                clan_id,
                user_id,
            ),
        )

        await db.execute(
            """
            INSERT INTO clan_members(
                clan_id,
                user_id,
                joined_at,
                role
            )
            VALUES (?, ?, ?, 'owner')
            """,
            (
                clan_id,
                user_id,
                created,
            ),
        )

    await state.clear()

    await message.answer(
        "🎉🏳️✨ <b>КЛАН СОЗДАН!</b> ✨🏳️🎉\n\n"
        f"🏷 Название: <b>{esc(clan_name)}</b>\n"
        f"🔢 Номер: <b>#{clan_number}</b>\n"
        "👑 Ты стал его владельцем!"
    )


# ------------------------------------------------------------
# КЛАН-МАГАЗИН
# ------------------------------------------------------------

def clan_shop_prices(
    members: int,
) -> dict[str, int]:
    if members <= 5:
        luck = 100
    elif members <= 15:
        luck = 300
    elif members <= 30:
        luck = 500
    elif members <= 100:
        luck = 999
    else:
        luck = 1000 + (members - 100)

    if members <= 50:
        blades = 250 + max(0, members - 1) * 200
    else:
        blades = 10050 + (members - 51) * 250

    if members <= 25:
        xp_price = 1000
    elif members <= 50:
        xp_price = 3000
    else:
        xp_price = 8500

    return {
        "luck": luck,
        "blade": blades,
        "xp": xp_price,
        "level": 100 * (2 ** 0),
    }


@router.message(
    lambda message: command_is(
        message,
        "клан шоп",
        "клан магазин",
    )
)
async def clan_shop_command(
    message: Message,
) -> None:
    clan = await get_user_clan(
        message.from_user.id
    )

    if not clan:
        await message.answer(
            "💔 Сначала вступи в клан!"
        )
        return

    async with db_connection() as db:
        members = await clan_members_count(
            db,
            safe_int(clan["clan_id"]),
        )

    owner = (
        safe_int(clan["owner_id"])
        == message.from_user.id
    )

    prices = clan_shop_prices(members)

    text = (
        "🏪🏳️✨ <b>КЛАНОВЫЙ МАГАЗИН</b> ✨🏳️🏪\n\n"
        f"💰 Баланс клана: "
        f"<b>{safe_int(clan['clan_coins'])} кланкоинов</b>\n"
        f"👥 Участников: <b>{members}</b>\n\n"
        f"🍀 Эликсир для всех: "
        f"<b>{prices['luck']}</b>\n"
        f"🗡 Клинок каждому: "
        f"<b>{prices['blade']}</b>\n"
        f"💫 Личный опыт: "
        f"<b>{prices['xp']}</b>\n"
        "🏰 Улучшение клана: цена зависит от уровня\n\n"
    )

    if owner:
        text += "👑 Ты владелец — покупки доступны!"
    else:
        text += "👀 Режим просмотра: покупать может только владелец."

    await message.answer(
        text,
        reply_markup=clan_shop_keyboard(owner),
    )


@router.callback_query(
    F.data == "clan_shop:readonly"
)
async def clan_shop_readonly_callback(
    callback: CallbackQuery,
) -> None:
    await answer_callback(
        callback,
        "Покупать может только владелец клана 👑",
        alert=True,
    )

# ============================================================
# BLOCK 8/9 — КВЕСТЫ, КЛАН-МАГАЗИН И ТОП
# ============================================================

QUEST_TEMPLATES = [
    ("fem", "Заберите {n} карт", "easy", 15, 5, 0),
    ("daily", "Заберите {n} дейлик", "easy", 15, 5, 0),
    ("transfer", "Переведите {n} фемкоинов", "easy", 15, 5, 0),
    ("profile", "Откройте профиль {n} раз", "easy", 15, 5, 0),
    ("casino", "Сыграйте {n} раз в казино", "easy", 15, 5, 0),
    ("craft", "Выполните {n} крафт", "medium", 25, 10, 0),
    ("femcoins", "Заработайте {n} фемкоинов", "medium", 25, 10, 0),
    ("cards", "Соберите {n} разных карт", "medium", 25, 10, 0),
    ("referral", "Пригласите {n} пользователя", "medium", 25, 10, 0),
    ("chest", "Откройте {n} сундук", "medium", 25, 10, 0),
    ("trade", "Завершите {n} трейд", "medium", 25, 10, 0),
    ("artifact", "Получите {n} артефакт", "medium", 25, 10, 0),
    ("vip_cards", "Получите {n} VIP-карту", "hard", 50, 25, 3),
    ("rare_card", "Получите {n} редкую карту", "hard", 50, 25, 3),
    ("clan_join", "Сделайте {n} вклад в клан", "hard", 50, 25, 3),
    ("shield_craft", "Скрафтите {n} щит", "hard", 50, 25, 3),
    ("blade", "Получите {n} клинок", "hard", 50, 25, 3),
]

QUEST_RANGES = {
    "easy": (1, 5),
    "medium": (1, 3),
    "hard": (1, 2),
}


def quest_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def quest_keyboard(
    quests: list[aiosqlite.Row],
) -> InlineKeyboardMarkup:
    buttons = []

    all_completed = True

    for quest in quests:
        progress = min(
            safe_int(quest["progress"]),
            safe_int(quest["target"]),
        )
        target = safe_int(quest["target"])
        claimed = safe_int(quest["claimed"]) == 1

        if progress < target or claimed:
            all_completed = False

        if progress >= target and not claimed:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"🎁 Забрать квест "
                            f"{safe_int(quest['slot'])}"
                        ),
                        callback_data=(
                            f"quest_claim:{safe_int(quest['quest_id'])}"
                        ),
                    )
                ]
            )

    if all_completed and quests:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="✨ Забрать всё ✨",
                    callback_data="quest_claim_all",
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


async def create_daily_quests(
    user_id: int,
) -> None:
    today = quest_date()

    async with transaction() as db:
        async with db.execute(
            """
            SELECT COUNT(*) AS total
            FROM clan_quests
            WHERE user_id = ?
              AND quest_date = ?
            """,
            (user_id, today),
        ) as cursor:
            row = await cursor.fetchone()

        if safe_int(row["total"]) >= 3:
            return

        selected = random.sample(
            QUEST_TEMPLATES,
            3,
        )

        difficulties = [
            item[2]
            for item in selected
        ]

        if difficulties.count("hard") > 1:
            selected = [
                item for item in QUEST_TEMPLATES
                if item[2] != "hard"
            ][:2] + [
                item for item in QUEST_TEMPLATES
                if item[2] == "hard"
            ][:1]
            random.shuffle(selected)

        for slot, template in enumerate(selected, 1):
            quest_type, _, difficulty, fem_reward, clan_reward, xp_reward = template
            low, high = QUEST_RANGES[difficulty]
            target = random.randint(low, high)

            description = template[1].format(n=target)

            await db.execute(
                """
                INSERT OR IGNORE INTO clan_quests(
                    user_id,
                    quest_date,
                    slot,
                    quest_type,
                    target,
                    progress,
                    difficulty,
                    clan_reward,
                    fem_reward,
                    xp_reward,
                    claimed
                )
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 0)
                """,
                (
                    user_id,
                    today,
                    slot,
                    quest_type,
                    target,
                    difficulty,
                    clan_reward,
                    fem_reward,
                    xp_reward,
                ),
            )


def quest_description(row: aiosqlite.Row) -> str:
    template = next(
        (
            item[1]
            for item in QUEST_TEMPLATES
            if item[0] == row["quest_type"]
        ),
        "Выполните задание: {n}",
    )

    return template.format(
        n=safe_int(row["target"])
    )


@router.message(
    lambda message: command_is(message, "квесты", "quests")
)
async def quests_command(
    message: Message,
) -> None:
    user_id = message.from_user.id

    await create_daily_quests(user_id)

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT *
            FROM clan_quests
            WHERE user_id = ?
              AND quest_date = ?
            ORDER BY slot
            """,
            (user_id, quest_date()),
        ) as cursor:
            quests = await cursor.fetchall()

    lines = [
        "🧩✨ <b>ТВОИ КВЕСТЫ НА СЕГОДНЯ</b> ✨🧩",
        "",
    ]

    icons = {
        "easy": "🟢",
        "medium": "🟡",
        "hard": "🔴",
    }

    for quest in quests:
        progress = min(
            safe_int(quest["progress"]),
            safe_int(quest["target"]),
        )
        claimed = safe_int(quest["claimed"]) == 1

        status = "✅ Получено" if claimed else (
            "🎁 Готово" if progress >= safe_int(quest["target"])
            else "⏳ В процессе"
        )

        lines.append(
            f"{icons.get(quest['difficulty'], '✨')} "
            f"<b>{safe_int(quest['slot'])}.</b> "
            f"{esc(quest_description(quest))}\n"
            f"   📊 {progress}/{safe_int(quest['target'])} "
            f"— {status}\n"
            f"   💰 +{safe_int(quest['fem_reward'])} 🪙"
            f" | 🏳️ +{safe_int(quest['clan_reward'])}"
        )

        if safe_int(quest["xp_reward"]):
            lines.append(
                f"   💫 +{safe_int(quest['xp_reward'])} опыта"
            )

        lines.append("")

    await message.answer(
        "\n".join(lines),
        reply_markup=quest_keyboard(quests),
    )


async def claim_quest(
    user_id: int,
    quest_id: int,
) -> Optional[str]:
    async with transaction() as db:
        async with db.execute(
            """
            SELECT *
            FROM clan_quests
            WHERE quest_id = ?
              AND user_id = ?
            """,
            (quest_id, user_id),
        ) as cursor:
            quest = await cursor.fetchone()

        if not quest:
            return None

        if safe_int(quest["claimed"]) == 1:
            return "already"

        if safe_int(quest["progress"]) < safe_int(quest["target"]):
            return "not_ready"

        await db.execute(
            """
            UPDATE clan_quests
            SET claimed = 1
            WHERE quest_id = ?
              AND user_id = ?
              AND claimed = 0
            """,
            (quest_id, user_id),
        )

        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins + ?
            WHERE user_id = ?
            """,
            (
                safe_int(quest["fem_reward"]),
                user_id,
            ),
        )

        clan_id = None

        async with db.execute(
            """
            SELECT clan_id
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        if user and user["clan_id"]:
            clan_id = safe_int(user["clan_id"])

        if clan_id:
            await db.execute(
                """
                UPDATE clans
                SET clan_coins = clan_coins + ?
                WHERE clan_id = ?
                """,
                (
                    safe_int(quest["clan_reward"]),
                    clan_id,
                ),
            )

        await add_experience(
            db,
            user_id,
            safe_int(quest["xp_reward"]),
        )

    return "ok"


@router.callback_query(
    F.data.startswith("quest_claim:")
)
async def quest_claim_callback(
    callback: CallbackQuery,
) -> None:
    raw = callback_payload(
        callback.data,
        "quest_claim:",
    )

    try:
        quest_id = int(raw or "0")
    except ValueError:
        await answer_callback(
            callback,
            "Некорректный квест 💔",
            alert=True,
        )
        return

    result = await claim_quest(
        callback.from_user.id,
        quest_id,
    )

    if result == "ok":
        await answer_callback(
            callback,
            "Награда получена! 🎁",
            alert=True,
        )
        await quests_command(callback.message)
    elif result == "already":
        await answer_callback(
            callback,
            "Награда уже получена",
            alert=True,
        )
    else:
        await answer_callback(
            callback,
            "Квест ещё не выполнен",
            alert=True,
        )


@router.callback_query(
    F.data == "quest_claim_all"
)
async def quest_claim_all_callback(
    callback: CallbackQuery,
) -> None:
    user_id = callback.from_user.id
    await create_daily_quests(user_id)

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT *
            FROM clan_quests
            WHERE user_id = ?
              AND quest_date = ?
            ORDER BY slot
            """,
            (user_id, quest_date()),
        ) as cursor:
            quests = await cursor.fetchall()

    if not quests or any(
        safe_int(q["progress"]) < safe_int(q["target"])
        for q in quests
    ):
        await answer_callback(
            callback,
            "Сначала выполни все три квеста ✨",
            alert=True,
        )
        return

    claimed_count = 0

    for quest in quests:
        if safe_int(quest["claimed"]) == 0:
            result = await claim_quest(
                user_id,
                safe_int(quest["quest_id"]),
            )
            if result == "ok":
                claimed_count += 1

    async with transaction() as db:
        await db.execute(
            """
            UPDATE users
            SET femcoins = femcoins + 10
            WHERE user_id = ?
            """,
            (user_id,),
        )

        async with db.execute(
            """
            SELECT clan_id
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        if user and user["clan_id"]:
            await db.execute(
                """
                UPDATE clans
                SET clan_coins = clan_coins + 10
                WHERE clan_id = ?
                """,
                (safe_int(user["clan_id"]),),
            )

        await add_experience(
            db,
            user_id,
            10,
        )

    await answer_callback(
        callback,
        f"Забрано квестов: {claimed_count}! ✨",
        alert=True,
    )

    await callback.message.answer(
        "🎉✨ <b>ЗАБРАНЫ ВСЕ НАГРАДЫ!</b> ✨🎉\n\n"
        "💰 Бонус: <b>+10 🪙</b>\n"
        "🏳️ Бонус: <b>+10 кланкоинов</b>\n"
        "💫 Опыт: <b>+10</b>"
    )


# ------------------------------------------------------------
# КЛАНОВЫЙ МАГАЗИН: ПОКУПКИ
# ------------------------------------------------------------

async def clan_owner_data(
    user_id: int,
) -> Optional[tuple[aiosqlite.Row, int]]:
    clan = await get_user_clan(user_id)

    if not clan:
        return None

    if safe_int(clan["owner_id"]) != user_id:
        return None

    async with db_connection() as db:
        members = await clan_members_count(
            db,
            safe_int(clan["clan_id"]),
        )

    return clan, members


async def clan_shop_purchase(
    user_id: int,
    product: str,
) -> str:
    owner_data = await clan_owner_data(user_id)

    if not owner_data:
        return "not_owner"

    clan, members = owner_data
    prices = clan_shop_prices(members)
    price = prices.get(product)

    if price is None:
        return "invalid"

    clan_id = safe_int(clan["clan_id"])

    async with transaction() as db:
        async with db.execute(
            """
            SELECT clan_coins,
                   level
            FROM clans
            WHERE clan_id = ?
            """,
            (clan_id,),
        ) as cursor:
            current = await cursor.fetchone()

        if not current or safe_int(
            current["clan_coins"]
        ) < price:
            return "no_money"

        if product == "level":
            price = 100 * (
                2 ** max(
                    0,
                    safe_int(current["level"]) - 1,
                )
            )

            if safe_int(current["clan_coins"]) < price:
                return "no_money"

            await db.execute(
                """
                UPDATE clans
                SET clan_coins = clan_coins - ?,
                    level = level + 1
                WHERE clan_id = ?
                  AND clan_coins >= ?
                """,
                (
                    price,
                    clan_id,
                    price,
                ),
            )
            return "level"

        if product == "luck":
            await db.execute(
                """
                UPDATE clans
                SET clan_coins = clan_coins - ?
                WHERE clan_id = ?
                  AND clan_coins >= ?
                """,
                (
                    price,
                    clan_id,
                    price,
                ),
            )

            await db.execute(
                """
                UPDATE users
                SET luck_expires = MAX(
                    luck_expires,
                    ?
                )
                WHERE clan_id = ?
                """,
                (
                    now() + 3 * 3600,
                    clan_id,
                ),
            )

        elif product == "blade":
            await db.execute(
                """
                UPDATE clans
                SET clan_coins = clan_coins - ?
                WHERE clan_id = ?
                  AND clan_coins >= ?
                """,
                (
                    price,
                    clan_id,
                    price,
                ),
            )

            async with db.execute(
                """
                SELECT user_id
                FROM clan_members
                WHERE clan_id = ?
                """,
                (clan_id,),
            ) as cursor:
                members_rows = await cursor.fetchall()

            for member in members_rows:
                await add_artifact(
                    db,
                    safe_int(member["user_id"]),
                    "blade",
                    "",
                    1,
                )

        elif product == "xp":
            await db.execute(
                """
                UPDATE clans
                SET clan_coins = clan_coins - ?
                WHERE clan_id = ?
                  AND clan_coins >= ?
                """,
                (
                    price,
                    clan_id,
                    price,
                ),
            )

            await add_experience(
                db,
                user_id,
                10 if members <= 25 else (
                    25 if members <= 50 else 50
                ),
            )

        else:
            return "invalid"

        await db.execute(
            """
            INSERT INTO clan_shop_purchases(
                clan_id,
                user_id,
                product,
                price,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                clan_id,
                user_id,
                product,
                price,
                now(),
            ),
        )

    return "ok"


@router.callback_query(
    F.data.startswith("clan_shop:")
)
async def clan_shop_callback(
    callback: CallbackQuery,
) -> None:
    product = callback_payload(
        callback.data,
        "clan_shop:",
    )

    if product in {
        "readonly",
        "stars",
    }:
        await answer_callback(
            callback,
            "Этот раздел пока доступен только для просмотра 👀",
            alert=True,
        )
        return

    result = await clan_shop_purchase(
        callback.from_user.id,
        product or "",
    )

    messages = {
        "not_owner": "Покупать может только владелец клана 👑",
        "no_money": "Недостаточно кланкоинов 💰",
        "invalid": "Товар не найден 💔",
        "level": "Клан улучшен! 🏰✨",
        "ok": "Покупка выполнена! 🎉",
    }

    await answer_callback(
        callback,
        messages.get(result, "Готово!"),
        alert=True,
    )


# ------------------------------------------------------------
# ТОП КОЛЛЕКЦИОНЕРОВ
# ------------------------------------------------------------

@router.message(
    lambda message: command_is(message, "топ", "top")
)
async def top_command(
    message: Message,
) -> None:
    async with db_connection() as db:
        async with db.execute(
            """
            SELECT u.username,
                   COALESCE(SUM(i.count), 0) AS cards
            FROM users AS u
            LEFT JOIN inventory AS i
              ON i.user_id = u.user_id
            GROUP BY u.user_id
            ORDER BY cards DESC,
                     u.first_seen ASC
            LIMIT 10
            """
        ) as cursor:
            rows = await cursor.fetchall()

    lines = [
        "🏆✨ <b>ТОП-10 КОЛЛЕКЦИОНЕРОВ</b> ✨🏆",
        "",
    ]

    medals = ["🥇", "🥈", "🥉"]

    for index, row in enumerate(rows, 1):
        username = str(
            row["username"] or "Игрок"
        ).lstrip("@").split()[0]

        medal = medals[index - 1] if index <= 3 else f"{index}."

        lines.append(
            f"{medal} <b>{esc(username)}</b> — "
            f"{safe_int(row['cards'])} 🎴"
        )

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📜 Отправить ТОП-100 в ЛС 💌",
                    callback_data="top100:send",
                )
            ]
        ]
    )

    await message.answer(
        "\n".join(lines),
        reply_markup=markup,
    )


@router.callback_query(
    F.data == "top100:send"
)
async def top100_callback(
    callback: CallbackQuery,
) -> None:
    async with db_connection() as db:
        async with db.execute(
            """
            SELECT u.username,
                   COALESCE(SUM(i.count), 0) AS cards
            FROM users AS u
            LEFT JOIN inventory AS i
              ON i.user_id = u.user_id
            GROUP BY u.user_id
            ORDER BY cards DESC,
                     u.first_seen ASC
            LIMIT 100
            """
        ) as cursor:
            rows = await cursor.fetchall()

    lines = [
        "📜✨ <b>ТОП-100 КОЛЛЕКЦИОНЕРОВ</b> ✨📜",
        "",
    ]

    for index, row in enumerate(rows, 1):
        username = str(
            row["username"] or "Игрок"
        ).lstrip("@").split()[0]

        lines.append(
            f"{index}. {esc(username)} — "
            f"{safe_int(row['cards'])} 🎴"
        )

    try:
        await bot.send_message(
            callback.from_user.id,
            "\n".join(lines),
        )
        await answer_callback(
            callback,
            "ТОП-100 отправлен в личку! 💌",
            alert=True,
        )
    except Exception:
        logger.exception(
            "Ошибка отправки ТОП-100"
        )
        await answer_callback(
            callback,
            "Сначала открой личные сообщения с ботом!",
            alert=True,
        )

# ============================================================
# BLOCK 9/11 — АРТЕФАКТЫ, АТАКИ И СКРЫТИЕ
# ============================================================


def artifact_visibility_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗡 Скрыть клинки",
                    callback_data="hide_artifact:blade",
                ),
                InlineKeyboardButton(
                    text="🛡 Скрыть броню",
                    callback_data="hide_artifact:armor",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔰 Скрыть щиты",
                    callback_data="hide_artifact:shield",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👁 Раскрыть всё",
                    callback_data="show_artifacts:all",
                )
            ],
        ]
    )


@router.message(
    lambda message: (
        message.text
        and re.fullmatch(
            r"скрыть\s+(клинки|броню|щиты)",
            message.text.strip(),
            re.IGNORECASE,
        )
    )
)
async def hide_artifact_command(
    message: Message,
) -> None:
    if not await is_vip(message.from_user.id):
        await message.answer(
            "👑 Скрытие артефактов доступно только VIP!"
        )
        return

    value = message.text.lower().split()[-1]

    column = {
        "клинки": "blade_hidden",
        "броню": "armor_hidden",
        "щиты": "shield_hidden",
    }.get(value)

    if not column:
        return

    async with transaction() as db:
        await db.execute(
            f"""
            UPDATE users
            SET {column} = 1
            WHERE user_id = ?
            """,
            (message.from_user.id,),
        )

    await message.answer(
        f"🙈✨ Отображение: <b>{esc(value)}</b> скрыто!"
    )


@router.message(
    lambda message: (
        message.text
        and re.fullmatch(
            r"раскрыть\s+(клинки|броню|щиты|всё|все)",
            message.text.strip(),
            re.IGNORECASE,
        )
    )
)
async def show_artifact_command(
    message: Message,
) -> None:
    if not await is_vip(message.from_user.id):
        await message.answer(
            "👑 Раскрытие артефактов доступно только VIP!"
        )
        return

    value = message.text.lower().split()[-1]

    async with transaction() as db:
        if value in {"всё", "все"}:
            await db.execute(
                """
                UPDATE users
                SET blade_hidden = 0,
                    armor_hidden = 0,
                    shield_hidden = 0
                WHERE user_id = ?
                """,
                (message.from_user.id,),
            )
        else:
            column = {
                "клинки": "blade_hidden",
                "броню": "armor_hidden",
                "щиты": "shield_hidden",
            }.get(value)

            if not column:
                return

            await db.execute(
                f"""
                UPDATE users
                SET {column} = 0
                WHERE user_id = ?
                """,
                (message.from_user.id,),
            )

    await message.answer(
        "👁️✨ Артефакты снова отображаются в профиле!"
    )


@router.callback_query(
    F.data.startswith("hide_artifact:")
)
async def hide_artifact_callback(
    callback: CallbackQuery,
) -> None:
    if not await is_vip(callback.from_user.id):
        await answer_callback(
            callback,
            "Нужен VIP 👑",
            alert=True,
        )
        return

    artifact_type = callback_payload(
        callback.data,
        "hide_artifact:",
    )

    columns = {
        "blade": "blade_hidden",
        "armor": "armor_hidden",
        "shield": "shield_hidden",
    }

    column = columns.get(artifact_type or "")

    if not column:
        await answer_callback(
            callback,
            "Некорректный артефакт",
            alert=True,
        )
        return

    async with transaction() as db:
        await db.execute(
            f"""
            UPDATE users
            SET {column} = 1
            WHERE user_id = ?
            """,
            (callback.from_user.id,),
        )

    await answer_callback(
        callback,
        "Артефакт скрыт 🙈",
        alert=True,
    )


@router.callback_query(
    F.data.startswith("show_artifacts:")
)
async def show_artifacts_callback(
    callback: CallbackQuery,
) -> None:
    if not await is_vip(callback.from_user.id):
        await answer_callback(
            callback,
            "Нужен VIP 👑",
            alert=True,
        )
        return

    async with transaction() as db:
        await db.execute(
            """
            UPDATE users
            SET blade_hidden = 0,
                armor_hidden = 0,
                shield_hidden = 0
            WHERE user_id = ?
            """,
            (callback.from_user.id,),
        )

    await answer_callback(
        callback,
        "Артефакты раскрыты 👁️",
        alert=True,
    )


async def resolve_admin_target(
    message: Message,
) -> Optional[int]:
    if (
        message.reply_to_message
        and message.reply_to_message.from_user
    ):
        return message.reply_to_message.from_user.id

    if not message.text:
        return None

    for part in message.text.split():
        if part.startswith("@"):
            return await find_user_by_username(part)

    return None


def parse_admin_operation(
    message: Message,
) -> Optional[tuple[str, int, str]]:
    if not message.text:
        return None

    parts = message.text.lower().split()

    if len(parts) < 3:
        return None

    operation = parts[1]

    if operation not in {
        "+клинок",
        "-клинок",
        "+щит",
        "-щит",
        "+броня",
        "-броня",
    }:
        return None

    try:
        amount = int(
            next(
                item
                for item in parts[2:]
                if item.isdigit()
            )
        )
    except (StopIteration, ValueError):
        return None

    if amount <= 0:
        return None

    subtype = ""

    if "щит" in operation:
        subtype = "weak"

        for item in parts[2:]:
            normalized = normalize_shield(item)

            if normalized:
                subtype = normalized
                break

    return operation, amount, subtype


@router.message(
    lambda message: (
        message.text
        and message.text.lower().startswith(
            ("бот ", "/бот ")
        )
        and any(
            token in message.text.lower()
            for token in (
                "+клинок",
                "-клинок",
                "+щит",
                "-щит",
                "+броня",
                "-броня",
            )
        )
    )
)
async def admin_artifact_command(
    message: Message,
) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    parsed = parse_admin_operation(message)
    target_id = await resolve_admin_target(message)

    if not parsed or not target_id:
        await message.answer(
            "🧾 Формат:\n"
            "<code>/бот +клинок 1 @username</code>\n"
            "<code>/бот +щит weak 4 @username</code>\n"
            "<code>/бот +броня 2 @username</code>\n\n"
            "Можно также использовать ответ на сообщение."
        )
        return

    operation, amount, subtype = parsed

    target_user = None

    if (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == target_id
    ):
        target_user = message.reply_to_message.from_user

    if target_user:
        await ensure_user(target_user)
    elif not await user_exists(target_id):
        await message.answer(
            "❌ Пользователь ещё не зарегистрирован."
        )
        return

    if "клинок" in operation:
        artifact_type = "blade"
        subtype = ""
    elif "броня" in operation:
        artifact_type = "armor"
        subtype = ""
    else:
        artifact_type = "shield"

    is_add = operation.startswith("+")

    async with transaction() as db:
        if is_add:
            await add_artifact(
                db,
                target_id,
                artifact_type,
                subtype,
                amount,
            )
        else:
            changed = await db.execute(
                """
                UPDATE artifacts
                SET amount = amount - ?
                WHERE user_id = ?
                  AND artifact_type = ?
                  AND subtype = ?
                  AND amount >= ?
                """,
                (
                    amount,
                    target_id,
                    artifact_type,
                    subtype,
                    amount,
                ),
            )

            if changed.rowcount != 1:
                await message.answer(
                    "❌ У пользователя недостаточно предметов."
                )
                return

    await log_action(
        message.from_user.id,
        f"{operation} {amount} "
        f"{artifact_type}/{subtype} "
        f"пользователю {target_id}",
    )

    await message.answer(
        "✅✨ Артефактная операция выполнена!\n"
        f"Операция: <b>{esc(operation)}</b>\n"
        f"Количество: <b>{amount}</b>"
    )


@router.message(
    lambda message: command_is(
        message,
        "клинок",
    )
)
async def blade_attack_command(
    message: Message,
) -> None:
    if not message.reply_to_message:
        await message.answer(
            "🗡️ Команда должна быть ответом "
            "на сообщение жертвы!"
        )
        return

    attacker = message.from_user
    victim = message.reply_to_message.from_user

    if not victim or victim.is_bot:
        await message.answer(
            "❌ Нельзя атаковать бота."
        )
        return

    if victim.id == attacker.id:
        await message.answer(
            "❌ Нельзя атаковать себя."
        )
        return

    await ensure_user(attacker)
    await ensure_user(victim)

    attacker_id = attacker.id
    victim_id = victim.id

    async with get_lock(user_locks, attacker_id):
        async with transaction() as db:
            async with db.execute(
                """
                SELECT amount, blocked_until
                FROM artifacts
                WHERE user_id = ?
                  AND artifact_type = 'blade'
                  AND subtype = ''
                """,
                (attacker_id,),
            ) as cursor:
                blade = await cursor.fetchone()

            if not blade or safe_int(blade["amount"]) <= 0:
                await message.answer(
                    "💔 У тебя нет доступного клинка!"
                )
                return

            if safe_float(blade["blocked_until"]) > now():
                await message.answer(
                    "🔒 Клинок временно заблокирован!\n"
                    f"Осталось: "
                    f"<b>{fmt_seconds(safe_float(blade['blocked_until']) - now())}</b>"
                )
                return

            async with db.execute(
                """
                SELECT femcoins,
                       vip_until
                FROM users
                WHERE user_id = ?
                """,
                (victim_id,),
            ) as cursor:
                victim_row = await cursor.fetchone()

            if not victim_row:
                await message.answer(
                    "❌ Жертва не зарегистрирована."
                )
                return

            theft = random.randint(
                50,
                500,
            )

            theft = min(
                theft,
                safe_int(victim_row["femcoins"]),
            )

            shield_type = None

            async with db.execute(
                """
                SELECT subtype
                FROM artifacts
                WHERE user_id = ?
                  AND artifact_type = 'shield'
                  AND amount > 0
                ORDER BY RANDOM()
                LIMIT 1
                """,
                (victim_id,),
            ) as cursor:
                shield = await cursor.fetchone()

            if shield:
                shield_type = str(
                    shield["subtype"]
                )

                await db.execute(
                    """
                    UPDATE artifacts
                    SET amount = amount - 1
                    WHERE user_id = ?
                      AND artifact_type = 'shield'
                      AND subtype = ?
                      AND amount > 0
                    """,
                    (
                        victim_id,
                        shield_type,
                    ),
                )

            if shield_type == "weak":
                reflected = random.random() >= 0.50

                if reflected:
                    await db.execute(
                        """
                        UPDATE artifacts
                        SET blocked_until = ?
                        WHERE user_id = ?
                          AND artifact_type = 'blade'
                          AND subtype = ''
                        """,
                        (
                            now() + 30 * 60,
                            attacker_id,
                        ),
                    )

                    result = (
                        "🔰✨ <b>СЛАБЫЙ ЩИТ ОТРАЗИЛ АТАКУ!</b>\n"
                        "Щит сломался, а клинок заблокирован на 30 минут."
                    )
                else:
                    result = (
                        "🔰 Щит сломался и пропустил атаку!"
                    )

            elif shield_type == "reinforced":
                reflected = random.random() >= 0.50

                if reflected:
                    await db.execute(
                        """
                        UPDATE artifacts
                        SET blocked_until = ?
                        WHERE user_id = ?
                          AND artifact_type = 'blade'
                          AND subtype = ''
                        """,
                        (
                            now() + 90 * 60,
                            attacker_id,
                        ),
                    )

                    result = (
                        "💎✨ <b>УСИЛЕННЫЙ ЩИТ ОТРАЗИЛ АТАКУ!</b>\n"
                        "Клинок заблокирован на 1 час 30 минут."
                    )
                else:
                    theft = int(theft * 0.90)
                    result = (
                        "💎 Щит пропустил атаку, "
                        "но уменьшил кражу на 10%!"
                    )

            elif shield_type == "femboy":
                reflected = random.random() >= 0.40

                if reflected:
                    await db.execute(
                        """
                        UPDATE artifacts
                        SET blocked_until = ?
                        WHERE user_id = ?
                          AND artifact_type = 'blade'
                          AND subtype = ''
                        """,
                        (
                            now() + 3 * 3600,
                            attacker_id,
                        ),
                    )

                    result = (
                        "🩷✨ <b>ФЕМБОЙСКИЙ ЩИТ ОТРАЗИЛ АТАКУ!</b>\n"
                        "Клинок заблокирован на 3 часа."
                    )
                else:
                    theft = int(theft * 0.50)

                    result = (
                        "🩷 Щит пропустил атаку, "
                        "но уменьшил кражу на 50%!"
                    )

            else:
                result = ""

            # Клинок расходуется только если атака не отражена.
            reflected_attack = (
                "отразил атаку" in result
            )

            if reflected_attack:
                await db.execute(
                    """
                    UPDATE artifacts
                    SET blocked_until = MAX(
                        blocked_until,
                        ?
                    )
                    WHERE user_id = ?
                      AND artifact_type = 'blade'
                      AND subtype = ''
                    """,
                    (
                        now(),
                        attacker_id,
                    ),
                )
            else:
                await db.execute(
                    """
                    UPDATE artifacts
                    SET amount = amount - 1
                    WHERE user_id = ?
                      AND artifact_type = 'blade'
                      AND subtype = ''
                      AND amount > 0
                    """,
                    (attacker_id,),
                )

                if theft > 0:
                    await db.execute(
                        """
                        UPDATE users
                        SET femcoins = femcoins - ?
                        WHERE user_id = ?
                          AND femcoins >= ?
                        """,
                        (
                            theft,
                            victim_id,
                            theft,
                        ),
                    )

                    await db.execute(
                        """
                        UPDATE users
                        SET femcoins = femcoins + ?
                        WHERE user_id = ?
                        """,
                        (
                            theft,
                            attacker_id,
                        ),
                    )

                duration = random.choices(
                    [3, 6, 9],
                    weights=[60, 30, 10],
                    k=1,
                )[0]

                if safe_float(
                    victim_row["vip_until"]
                ) > now():
                    duration = random.choices(
                        [1, 2, 3],
                        weights=[25, 50, 25],
                        k=1,
                    )[0]

                await db.execute(
                    """
                    UPDATE users
                    SET attack_block_until = MAX(
                        attack_block_until,
                        ?
                    )
                    WHERE user_id = ?
                    """,
                    (
                        now() + duration * 3600,
                        victim_id,
                    ),
                )

                result += (
                    f"\n💰 Украдено: <b>{theft} 🪙</b>\n"
                    f"🚫 Запрет сбора карт: "
                    f"<b>{duration} ч.</b>"
                )

            await add_experience(
                db,
                attacker_id,
                2,
            )

    await message.answer(
        "🗡️💥 <b>АТАКА КЛИНКОМ!</b>\n\n"
        f"⚔️ Атакующий: {user_name(attacker)}\n"
        f"🎯 Жертва: {user_name(victim)}\n\n"
        f"{result or 'Атака выполнена!'}"
    )

# ============================================================
# BLOCK 10/11 — АДМИНИСТРИРОВАНИЕ, КАРТЫ И ПРОФИЛЬ
# ============================================================


async def admin_target(
    message: Message,
) -> Optional[int]:
    if (
        message.reply_to_message
        and message.reply_to_message.from_user
    ):
        return message.reply_to_message.from_user.id

    if message.text:
        for part in message.text.split():
            if part.startswith("@"):
                return await find_user_by_username(part)

    return None


def admin_rarity_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🏷 {rarity}",
                    callback_data=f"admin_card_rarity:{index}",
                )
            ]
            for index, rarity in enumerate(RARITIES)
        ]
    )


# ------------------------------------------------------------
# ДОБАВЛЕНИЕ КАРТЫ
# ------------------------------------------------------------

@router.message(
    lambda message: command_is(message, "plus_foto")
)
async def plus_foto_command(
    message: Message,
    state: FSMContext,
) -> None:
    if not await is_admin(message.from_user.id):
        return

    await state.clear()
    await state.set_state(AdminStates.waiting_photo)

    await message.answer(
        "📸✨ <b>ДОБАВЛЕНИЕ НОВОЙ КАРТЫ</b> ✨📸\n\n"
        "Отправь фотографию карты!"
    )


@router.message(
    AdminStates.waiting_photo,
    F.photo,
)
async def admin_photo_received(
    message: Message,
    state: FSMContext,
) -> None:
    if not await is_admin(message.from_user.id):
        await state.clear()
        return

    await state.update_data(
        file_id=message.photo[-1].file_id,
    )
    await state.set_state(
        AdminStates.waiting_card_description
    )

    await message.answer(
        "🌈 Выбери редкость карты:",
        reply_markup=admin_rarity_keyboard(),
    )


@router.callback_query(
    F.data.startswith("admin_card_rarity:")
)
async def admin_rarity_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if not await is_admin(callback.from_user.id):
        await answer_callback(
            callback,
            "Нет прав администратора",
            alert=True,
        )
        return

    raw = callback_payload(
        callback.data,
        "admin_card_rarity:",
    )

    try:
        rarity = RARITIES[int(raw or "-1")]
    except (ValueError, IndexError):
        await answer_callback(
            callback,
            "Некорректная редкость",
            alert=True,
        )
        return

    await state.update_data(
        rarity=rarity,
    )

    await edit_text_safely(
        callback.message,
        "📝 Теперь отправь описание карты текстом.\n"
        "Можно написать: <i>Без описания</i>.",
    )

    await answer_callback(callback)


@router.message(
    AdminStates.waiting_card_description
)
async def admin_description_received(
    message: Message,
    state: FSMContext,
) -> None:
    if not await is_admin(message.from_user.id):
        await state.clear()
        return

    if not message.text:
        await message.answer(
            "❌ Описание должно быть текстом!"
        )
        return

    data = await state.get_data()

    if not data.get("file_id") or not data.get("rarity"):
        await state.clear()
        await message.answer(
            "❌ Сессия добавления карты устарела."
        )
        return

    description = message.text.strip()

    if description.lower() in {
        "без описания",
        "-",
        "нет",
    }:
        description = ""

    async with transaction() as db:
        cursor = await db.execute(
            """
            INSERT INTO cards(
                file_id,
                rarity,
                description
            )
            VALUES (?, ?, ?)
            """,
            (
                data["file_id"],
                data["rarity"],
                description,
            ),
        )
        card_id = cursor.lastrowid

    await log_action(
        message.from_user.id,
        f"Добавлена карта #{card_id}",
    )

    await state.clear()

    await message.answer(
        "✅🎴 <b>КАРТА ДОБАВЛЕНА!</b>\n\n"
        f"🔢 ID: <b>#{card_id}</b>\n"
        f"🏷 Редкость: <b>{esc(data['rarity'])}</b>"
    )


# ------------------------------------------------------------
# АЛЬБОМЫ КАРТ
# ------------------------------------------------------------

async def send_cards_album(
    message: Message,
    limit: int,
) -> None:
    if not await is_admin(message.from_user.id):
        return

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT id,
                   file_id,
                   rarity,
                   description
            FROM cards
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            cards = await cursor.fetchall()

    if not cards:
        await message.answer(
            "🎴 Библиотека карт пока пуста!"
        )
        return

    await message.answer(
        f"🎴✨ Отправляю альбом из "
        f"<b>{len(cards)}</b> карт!"
    )

    # Telegram принимает media_group максимум из 10 элементов.
    for start in range(0, len(cards), 10):
        chunk = cards[start:start + 10]

        media = [
            InputMediaPhoto(
                media=card["file_id"],
                caption=(
                    f"🎴 #{safe_int(card['id'])}\n"
                    f"🏷 {esc(card['rarity'])}\n"
                    f"📝 {esc(card['description']) or 'Без описания'}"
                ),
            )
            for card in chunk
        ]

        try:
            await message.answer_media_group(media)
        except Exception:
            logger.exception(
                "Ошибка отправки альбома карт"
            )

        await asyncio.sleep(0.25)


@router.message(
    lambda message: (
        message.text
        and message.text.strip().lower()
        in {
            "all card",
            "100 card",
            "card 100",
        }
    )
)
async def cards_album_command(
    message: Message,
) -> None:
    limit = (
        100
        if message.text.strip().lower()
        in {"100 card", "card 100"}
        else 500
    )

    await send_cards_album(
        message,
        limit,
    )


# ------------------------------------------------------------
# УПРАВЛЕНИЕ КАРТОЙ
# ------------------------------------------------------------

@router.message(
    lambda message: (
        message.text
        and re.fullmatch(
            r"/?card\s+\d+",
            message.text.strip(),
            re.IGNORECASE,
        )
        and message.text.strip().lower()
        not in {"card 100", "/card 100"}
    )
)
async def card_admin_command(
    message: Message,
) -> None:
    if not await is_admin(message.from_user.id):
        return

    try:
        card_id = int(
            message.text.split()[1]
        )
    except (IndexError, ValueError):
        await message.answer(
            "Формат: <code>/card 15</code>"
        )
        return

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT id,
                   file_id,
                   rarity,
                   description
            FROM cards
            WHERE id = ?
            """,
            (card_id,),
        ) as cursor:
            card = await cursor.fetchone()

        if not card:
            await message.answer(
                "❌ Карта не найдена!"
            )
            return

        async with db.execute(
            """
            SELECT COUNT(DISTINCT user_id) AS owners,
                   COALESCE(SUM(count), 0) AS copies
            FROM inventory
            WHERE card_id = ?
            """,
            (card_id,),
        ) as cursor:
            stats = await cursor.fetchone()

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить карту",
                    callback_data=f"admin_delete_card:{card_id}",
                )
            ]
        ]
    )

    caption = (
        f"🎴✨ <b>КАРТА #{card_id}</b> ✨🎴\n\n"
        f"🏷 Редкость: <b>{esc(card['rarity'])}</b>\n"
        f"📝 {esc(card['description']) or 'Без описания'}\n\n"
        f"👥 Владельцев: "
        f"<b>{safe_int(stats['owners'])}</b>\n"
        f"📦 Копий: "
        f"<b>{safe_int(stats['copies'])}</b>"
    )

    await message.answer_photo(
        photo=card["file_id"],
        caption=caption,
        reply_markup=markup,
    )


@router.callback_query(
    F.data.startswith("admin_delete_card:")
)
async def delete_card_callback(
    callback: CallbackQuery,
) -> None:
    if not await is_admin(callback.from_user.id):
        await answer_callback(
            callback,
            "Нет прав",
            alert=True,
        )
        return

    raw = callback_payload(
        callback.data,
        "admin_delete_card:",
    )

    try:
        card_id = int(raw or "0")
    except ValueError:
        await answer_callback(
            callback,
            "Некорректный ID",
            alert=True,
        )
        return

    async with transaction() as db:
        await db.execute(
            """
            DELETE FROM inventory
            WHERE card_id = ?
            """,
            (card_id,),
        )

        result = await db.execute(
            """
            DELETE FROM cards
            WHERE id = ?
            """,
            (card_id,),
        )

        if result.rowcount != 1:
            await answer_callback(
                callback,
                "Карта уже удалена",
                alert=True,
            )
            return

    await log_action(
        callback.from_user.id,
        f"Удалена карта #{card_id} без сдвига ID",
    )

    await edit_text_safely(
        callback.message,
        "🗑✨ Карта удалена!\n"
        "ID остальных карт не изменены.",
    )

    await answer_callback(callback)


# ------------------------------------------------------------
# ПРИНУДИТЕЛЬНЫЙ ПРОФИЛЬ
# ------------------------------------------------------------

@router.message(
    lambda message: (
        message.text
        and (
            message.text.lower().startswith(
                "твой профиль"
            )
            or message.text.lower().startswith(
                "/твой профиль"
            )
        )
    )
)
async def forced_profile_command(
    message: Message,
) -> None:
    if not await is_admin(message.from_user.id):
        return

    target_id = await admin_target(message)

    if not target_id:
        await message.answer(
            "👤 Используй команду ответом "
            "или укажи @username."
        )
        return

    await send_profile(
        message,
        target_id,
        reply_to=message.message_id,
    )


# ------------------------------------------------------------
# АДМИНИСТРАТОРЫ
# ------------------------------------------------------------

@router.message(
    lambda message: command_is(message, "admins")
)
async def admins_command(
    message: Message,
) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    async with db_connection() as db:
        async with db.execute(
            """
            SELECT user_id,
                   added_at
            FROM admins
            ORDER BY added_at
            """
        ) as cursor:
            admins = await cursor.fetchall()

    if not admins:
        await message.answer(
            "👑 Дополнительных администраторов нет."
        )
        return

    buttons = []

    for admin in admins:
        admin_id = safe_int(
            admin["user_id"]
        )
        name = await db_username(admin_id)

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"❌ Снять {name}",
                    callback_data=(
                        f"remove_admin:{admin_id}"
                    ),
                )
            ]
        )

    await message.answer(
        "👑✨ <b>АДМИНИСТРАТОРЫ БОТА</b> ✨👑",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )


@router.callback_query(
    F.data.startswith("remove_admin:")
)
async def remove_admin_callback(
    callback: CallbackQuery,
) -> None:
    if callback.from_user.id != SUPER_ADMIN_ID:
        await answer_callback(
            callback,
            "Только супер-администратор",
            alert=True,
        )
        return

    raw = callback_payload(
        callback.data,
        "remove_admin:",
    )

    try:
        admin_id = int(raw or "0")
    except ValueError:
        await answer_callback(
            callback,
            "Некорректный ID",
            alert=True,
        )
        return

    async with transaction() as db:
        await db.execute(
            """
            DELETE FROM admins
            WHERE user_id = ?
            """,
            (admin_id,),
        )

    await log_action(
        callback.from_user.id,
        f"Снят администратор {admin_id}",
    )

    await edit_text_safely(
        callback.message,
        "✅ Администратор снят!",
    )

    await answer_callback(callback)


@router.message(
    lambda message: command_is(
        message,
        "now_admin_cod",
    )
)
async def create_admin_code_command(
    message: Message,
) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    code = make_token(10).upper()
    expires_at = now() + 3 * 3600

    async with transaction() as db:
        await db.execute(
            """
            INSERT INTO admin_codes(
                code,
                created_by,
                expires_at,
                used
            )
            VALUES (?, ?, ?, 0)
            """,
            (
                code,
                message.from_user.id,
                expires_at,
            ),
        )

    await message.answer(
        "🔐✨ <b>ОДНОРАЗОВЫЙ КОД АДМИНИСТРАТОРА</b> ✨\n\n"
        f"<code>{esc(code)}</code>\n"
        "⏳ Действует 3 часа."
    )


@router.message(
    lambda message: command_is(
        message,
        "I_am_admin",
    )
)
async def iam_admin_command(
    message: Message,
    state: FSMContext,
) -> None:
    await state.set_state(
        AdminStates.waiting_admin_code
    )

    await message.answer(
        "🔐 Отправь код администратора следующим сообщением."
    )


@router.message(
    AdminStates.waiting_admin_code
)
async def admin_code_handler(
    message: Message,
    state: FSMContext,
) -> None:
    if not message.text:
        await message.answer(
            "❌ Код должен быть текстом!"
        )
        return

    code = message.text.strip().upper()

    async with transaction() as db:
        async with db.execute(
            """
            SELECT created_by,
                   expires_at,
                   used
            FROM admin_codes
            WHERE code = ?
            """,
            (code,),
        ) as cursor:
            row = await cursor.fetchone()

        if (
            not row
            or safe_int(row["used"])
            or safe_float(row["expires_at"]) <= now()
        ):
            await state.clear()
            await message.answer(
                "❌ Код недействителен или истёк."
            )
            return

        await db.execute(
            """
            UPDATE admin_codes
            SET used = 1
            WHERE code = ?
            """,
            (code,),
        )

        await db.execute(
            """
            INSERT OR IGNORE INTO admins(
                user_id,
                added_by,
                added_at
            )
            VALUES (?, ?, ?)
            """,
            (
                message.from_user.id,
                safe_int(row["created_by"]),
                now(),
            ),
        )

    await state.clear()

    await log_action(
        message.from_user.id,
        "Получены права администратора",
    )

    await message.answer(
        "🎉👑 Ты стал администратором бота!"
    )

# ============================================================
# BLOCK 11/11 — АДМИН-КОМАНДЫ, РАССЫЛКА, ОЧИСТКА И ЗАПУСК
# ============================================================


# ------------------------------------------------------------
# ПОИСК ЦЕЛИ ДЛЯ АДМИН-КОМАНД
# Поддерживаются ответ на сообщение и @username в конце.
# ------------------------------------------------------------

async def resolve_admin_target(
    message: Message,
) -> Optional[int]:
    if (
        message.reply_to_message
        and message.reply_to_message.from_user
    ):
        return message.reply_to_message.from_user.id

    if not message.text:
        return None

    for token in reversed(message.text.split()):
        if token.startswith("@"):
            return await find_user_by_username(token)

    return None


def admin_command_tokens(
    message: Message,
) -> list[str]:
    if not message.text:
        return []

    parts = message.text.strip().split()

    if parts and parts[0].lower() in {
        "бот",
        "/бот",
    }:
        return parts[1:]

    return parts


# ------------------------------------------------------------
# ВЫДАЧА/ИЗЪЯТИЕ МОНЕТ, КАРТ И АРТЕФАКТОВ
# Примеры, ответом или с @username:
# /бот +коины 100 @username
# /бот +карта 12  @username
# /бот +щит weak 4 @username
# /бот +клинок 1 @username
# /бот +броня 2 @username
# ------------------------------------------------------------

@router.message(
    lambda message: (
        message.text
        and re.match(
            r"^/?бот\s+[+-](коины|карта|щит|клинок|броня)\b",
            message.text.strip(),
            re.IGNORECASE,
        )
    )
)
async def admin_grant_command(
    message: Message,
) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    parts = admin_command_tokens(message)

    if len(parts) < 2:
        await message.answer(
            "💖 <b>Формат команды:</b>\n"
            "<code>/бот +коины 100 @username</code>\n"
            "<code>/бот +карта 12 @username</code>\n"
            "<code>/бот +щит weak 4 @username</code>\n"
            "<code>/бот +клинок 1 @username</code>\n"
            "<code>/бот +броня 2 @username</code>\n\n"
            "Также можно ответить командой на сообщение игрока."
        )
        return

    operation = parts[0].lower()
    is_add = operation.startswith("+")
    entity = operation[1:]

    target_id = await resolve_admin_target(message)

    if target_id is None:
        await message.answer(
            "❌ Не найден пользователь. Ответь на его сообщение "
            "или добавь @username в конец."
        )
        return

    if not await user_exists(target_id):
        if (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == target_id
        ):
            await ensure_user(
                message.reply_to_message.from_user
            )
        else:
            await message.answer(
                "❌ Пользователь ещё не запускал бота."
            )
            return

    target_name = await db_username(target_id)

    try:
        if entity == "коины":
            amount_token = next(
                token for token in parts[1:]
                if re.fullmatch(r"-?\d+", token)
            )
            amount = int(amount_token)

            if amount <= 0:
                raise ValueError

            async with transaction() as db:
                if is_add:
                    await db.execute(
                        """
                        UPDATE users
                        SET femcoins = femcoins + ?
                        WHERE user_id = ?
                        """,
                        (amount, target_id),
                    )
                else:
                    await db.execute(
                        """
                        UPDATE users
                        SET femcoins = MAX(0, femcoins - ?)
                        WHERE user_id = ?
                        """,
                        (amount, target_id),
                    )

            description = (
                f"{'Начислено' if is_add else 'Снято'} "
                f"{amount} монет пользователю {target_id}"
            )

        elif entity == "карта":
            amount_token = next(
                token for token in parts[1:]
                if token.isdigit()
            )
            card_id = int(amount_token)

            async with transaction() as db:
                async with db.execute(
                    "SELECT id FROM cards WHERE id = ?",
                    (card_id,),
                ) as cursor:
                    card = await cursor.fetchone()

                if not card:
                    await message.answer(
                        "❌ Карта с таким ID не найдена."
                    )
                    return

                if is_add:
                    await db.execute(
                        """
                        INSERT INTO inventory(user_id, card_id, count)
                        VALUES (?, ?, 1)
                        ON CONFLICT(user_id, card_id)
                        DO UPDATE SET count = count + 1
                        """,
                        (target_id, card_id),
                    )
                else:
                    await db.execute(
                        """
                        UPDATE inventory
                        SET count = count - 1
                        WHERE user_id = ?
                          AND card_id = ?
                          AND count > 1
                        """,
                        (target_id, card_id),
                    )

                    await db.execute(
                        """
                        DELETE FROM inventory
                        WHERE user_id = ?
                          AND card_id = ?
                          AND count <= 1
                        """,
                        (target_id, card_id),
                    )

            description = (
                f"{'Выдана' if is_add else 'Изъята'} "
                f"одна копия карты #{card_id} "
                f"у пользователя {target_id}"
            )

        elif entity in {"клинок", "броня", "щит"}:
            if entity == "клинок":
                artifact_type = "blade"
                subtype = ""
            elif entity == "броня":
                artifact_type = "armor"
                subtype = ""
            else:
                artifact_type = "shield"
                subtype = "weak"

                for token in parts[1:]:
                    normalized = normalize_shield(token)
                    if normalized:
                        subtype = normalized
                        break

            amount_token = next(
                token for token in parts[1:]
                if token.isdigit()
            )
            amount = int(amount_token)

            if amount <= 0:
                raise ValueError

            async with transaction() as db:
                if is_add:
                    await add_artifact(
                        db,
                        target_id,
                        artifact_type,
                        subtype,
                        amount,
                    )
                else:
                    result = await db.execute(
                        """
                        UPDATE artifacts
                        SET amount = amount - ?
                        WHERE user_id = ?
                          AND artifact_type = ?
                          AND subtype = ?
                          AND amount >= ?
                        """,
                        (
                            amount,
                            target_id,
                            artifact_type,
                            subtype,
                            amount,
                        ),
                    )

                    if result.rowcount != 1:
                        await message.answer(
                            "❌ У пользователя недостаточно "
                            "таких предметов."
                        )
                        return

            description = (
                f"{'Выдано' if is_add else 'Изъято'} "
                f"{amount} предметов типа {entity} "
                f"пользователю {target_id}"
            )

        else:
            await message.answer(
                "❌ Неизвестный тип операции."
            )
            return

    except (StopIteration, ValueError):
        await message.answer(
            "❌ Проверь количество и формат команды."
        )
        return

    await log_action(
        message.from_user.id,
        description,
    )

    await message.answer(
        "✅✨ <b>ОПЕРАЦИЯ ВЫПОЛНЕНА!</b>\n\n"
        f"👤 Игрок: <b>{target_name}</b>\n"
        f"📝 {esc(description)}"
    )


# ------------------------------------------------------------
# АДМИНСКИЙ CUSTOM-КУЛДАУН
# Формат: /бот кд 1 24 @username
# Последнее число — срок действия в часах.
# -1 — бессрочно.
# ------------------------------------------------------------

@router.message(
    lambda message: (
        message.text
        and re.match(
            r"^/?бот\s+кд\b",
            message.text.strip(),
            re.IGNORECASE,
        )
    )
)
async def admin_cooldown_command(
    message: Message,
) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    parts = admin_command_tokens(message)

    if len(parts) < 3:
        await message.answer(
            "🕒 Формат:\n"
            "<code>/бот кд 1 24 @username</code>\n"
            "Первое число — длительность КД в часах.\n"
            "Второе число — срок действия настройки.\n"
            "Срок <code>-1</code> означает бессрочно."
        )
        return

    try:
        cooldown_hours = float(parts[1])
        duration_hours = float(parts[2])
    except ValueError:
        await message.answer(
            "❌ Часы должны быть числами."
        )
        return

    target_id = await resolve_admin_target(message)

    if target_id is None:
        await message.answer(
            "❌ Укажи игрока ответом или через @username."
        )
        return

    if not await user_exists(target_id):
        await message.answer(
            "❌ Пользователь ещё не зарегистрирован."
        )
        return

    if cooldown_hours < 0 or duration_hours == 0:
        await message.answer(
            "❌ Некорректное значение КД или срока."
        )
        return

    expires_at = (
        -1
        if duration_hours == -1
        else now() + duration_hours * 3600
    )

    async with transaction() as db:
        await db.execute(
            """
            UPDATE users
            SET custom_cd_hours = ?,
                custom_cd_expires = ?
            WHERE user_id = ?
            """,
            (
                cooldown_hours,
                expires_at,
                target_id,
            ),
        )

    await log_action(
        message.from_user.id,
        f"Установлен КД {cooldown_hours}ч "
        f"пользователю {target_id}",
    )

    await message.answer(
        "✅🕒 <b>КУЛДАУН ИЗМЕНЁН!</b>\n"
        f"👤 Игрок: <code>{target_id}</code>\n"
        f"⏳ КД: <b>{cooldown_hours:g} ч.</b>\n"
        f"📅 Срок: "
        f"<b>{'бессрочно' if duration_hours == -1 else f'{duration_hours:g} ч.'}</b>"
    )


# ------------------------------------------------------------
# АДМИНСКОЕ УПРАВЛЕНИЕ VIP
# /бот +вип 12 @username
# /бот -вип 12 @username
# ------------------------------------------------------------

@router.message(
    lambda message: (
        message.text
        and re.match(
            r"^/?бот\s+[+-]вип\b",
            message.text.strip(),
            re.IGNORECASE,
        )
    )
)
async def admin_vip_command(
    message: Message,
) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    parts = admin_command_tokens(message)

    if len(parts) < 2:
        await message.answer(
            "👑 Формат:\n"
            "<code>/бот +вип 12 @username</code>\n"
            "<code>/бот -вип 12 @username</code>\n"
            "Можно также ответить на сообщение."
        )
        return

    operation = parts[0].lower()

    try:
        hours = int(parts[1])
    except ValueError:
        await message.answer(
            "❌ Количество часов должно быть целым числом."
        )
        return

    if hours <= 0:
        await message.answer(
            "❌ Часы должны быть положительными."
        )
        return

    target_id = await resolve_admin_target(message)

    if target_id is None:
        await message.answer(
            "❌ Укажи игрока ответом или через @username."
        )
        return

    if not await user_exists(target_id):
        await message.answer(
            "❌ Пользователь ещё не зарегистрирован."
        )
        return

    current_time = now()

    async with transaction() as db:
        async with db.execute(
            """
            SELECT vip_until
            FROM users
            WHERE user_id = ?
            """,
            (target_id,),
        ) as cursor:
            row = await cursor.fetchone()

        old_until = safe_float(
            row["vip_until"]
            if row
            else 0
        )

        if operation == "+вип":
            new_until = (
                max(current_time, old_until)
                + hours * 3600
            )
        else:
            new_until = max(
                0,
                old_until - hours * 3600,
            )

        await db.execute(
            """
            UPDATE users
            SET vip_until = ?
            WHERE user_id = ?
            """,
            (new_until, target_id),
        )

    await log_action(
        message.from_user.id,
        f"{operation} {hours}ч для {target_id}",
    )

    await message.answer(
        "✅👑 <b>VIP ОБНОВЛЁН!</b>\n"
        f"👤 Пользователь: <code>{target_id}</code>\n"
        f"📅 До: "
        f"<b>{fmt_date(new_until) if new_until > current_time else 'VIP не активен'}</b>"
    )


# ------------------------------------------------------------
# УПРАВЛЕНИЕ ТЕКСТОМ «КТО АДМИН»
# ------------------------------------------------------------

@router.message(
    lambda message: command_is(
        message,
        "set_adm_text",
    )
)
async def set_admin_text_command(
    message: Message,
    state: FSMContext,
) -> None:
    if not await is_admin(message.from_user.id):
        return

    await state.set_state(
        AdminStates.waiting_admin_text
    )

    await message.answer(
        "📝 Отправь текст, который будет показан "
        "по команде <b>admin</b> или <b>кто админ</b>."
    )


@router.message(
    AdminStates.waiting_admin_text
)
async def save_admin_text(
    message: Message,
    state: FSMContext,
) -> None:
    if not await is_admin(message.from_user.id):
        await state.clear()
        return

    if not message.text:
        await message.answer(
            "❌ Текст должен быть обычным текстом."
        )
        return

    async with transaction() as db:
        await db.execute(
            """
            INSERT INTO settings(key, value)
            VALUES ('admin_info', ?)
            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value
            """,
            (message.text[:3500],),
        )

    await state.clear()

    await log_action(
        message.from_user.id,
        "Изменён текст информации об администраторе",
    )

    await message.answer(
        "✅✨ Текст информации об администраторе сохранён!"
    )


@router.message(
    lambda message: command_is(
        message,
        "admin",
        "кто админ",
    )
)
async def admin_info_command(
    message: Message,
) -> None:
    async with db_connection() as db:
        async with db.execute(
            """
            SELECT value
            FROM settings
            WHERE key = 'admin_info'
            """
        ) as cursor:
            row = await cursor.fetchone()

    text = (
        str(row["value"])
        if row and row["value"]
        else "Информация об администраторе пока не задана."
    )

    await message.answer(
        f"👑✨ <b>ИНФОРМАЦИЯ</b> ✨👑\n\n{text}"
    )


# ------------------------------------------------------------
# РАССЫЛКА SUPER ADMIN
# «add» начинает сбор сообщений, «/save» отправляет.
# ------------------------------------------------------------

@router.message(
    lambda message: command_is(message, "add")
)
async def broadcast_start(
    message: Message,
    state: FSMContext,
) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        return

    broadcast_buffer[message.from_user.id] = []
    await state.set_state(
        AdminStates.waiting_broadcast
    )

    await message.answer(
        "📣✨ <b>РЕЖИМ РАССЫЛКИ</b> ✨📣\n\n"
        "Перешли или отправь сообщения для рассылки.\n"
        "Для завершения отправь <code>/save</code>.\n"
        "Для отмены отправь <code>/cancel</code>."
    )


@router.message(
    AdminStates.waiting_broadcast
)
async def broadcast_collect(
    message: Message,
    state: FSMContext,
) -> None:
    if message.from_user.id != SUPER_ADMIN_ID:
        await state.clear()
        return

    if message.text and command_is(message, "cancel"):
        broadcast_buffer.pop(
            message.from_user.id,
            None,
        )
        await state.clear()
        await message.answer(
            "❌ Рассылка отменена."
        )
        return

    if message.text and command_is(message, "save"):
        messages = broadcast_buffer.pop(
            message.from_user.id,
            [],
        )
        await state.clear()

        if not messages:
            await message.answer(
                "❌ Нет сообщений для отправки."
            )
            return

        async with db_connection() as db:
            async with db.execute(
                "SELECT user_id FROM users"
            ) as cursor:
                user_rows = await cursor.fetchall()

            async with db.execute(
                """
                SELECT chat_id
                FROM chats
                WHERE chat_type IN ('group', 'supergroup')
                """
            ) as cursor:
                chat_rows = await cursor.fetchall()

        destinations = {
            safe_int(row["user_id"])
            for row in user_rows
        }
        destinations.update(
            safe_int(row["chat_id"])
            for row in chat_rows
        )

        sent = 0
        failed = 0

        for destination in destinations:
            try:
                for item in messages:
                    await bot.copy_message(
                        chat_id=destination,
                        from_chat_id=item.chat.id,
                        message_id=item.message_id,
                    )
                sent += 1
            except Exception:
                failed += 1
                logger.warning(
                    "Рассылка не отправлена в %s",
                    destination,
                    exc_info=True,
                )

            await asyncio.sleep(0.04)

        await message.answer(
            "📣✨ <b>РАССЫЛКА ЗАВЕРШЕНА!</b>\n\n"
            f"✅ Получателей: <b>{sent}</b>\n"
            f"❌ Ошибок: <b>{failed}</b>"
        )
        return

    broadcast_buffer.setdefault(
        message.from_user.id,
        [],
    ).append(message)

    await message.answer(
        "✅ Сообщение добавлено. "
        "Отправь ещё или заверши командой <code>/save</code>."
    )


# ------------------------------------------------------------
# ПРОСТОЙ ТЕКСТОВЫЙ TOP-100 CALLBACK
# Если другой блок уже зарегистрировал top100:send,
# оставь только один обработчик с этим callback.
# ------------------------------------------------------------

# ------------------------------------------------------------
# ОЧИСТКА ПРОСРОЧЕННЫХ СДЕЛОК И СОБЫТИЙ
# ------------------------------------------------------------

async def cleanup_worker() -> None:
    while True:
        try:
            current_time = now()

            async with transaction() as db:
                await db.execute(
                    """
                    DELETE FROM chests
                    WHERE expires_at <= ?
                    """,
                    (current_time,),
                )

                await db.execute(
                    """
                    DELETE FROM trades
                    WHERE status IN ('completed', 'cancelled')
                       OR updated_at <= ?
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

                await db.execute(
                    """
                    DELETE FROM clan_quests
                    WHERE quest_date < ?
                    """,
                    (
                        datetime.now().strftime("%Y-%m-%d"),
                    ),
                )

        except Exception:
            logger.exception(
                "Ошибка периодической очистки"
            )

        await asyncio.sleep(300)


# ------------------------------------------------------------
# ФОНОВОЕ СОХРАНЕНИЕ АКТИВНОСТИ ГРУПП
# Функция сундуков тишины будет добавлена отдельным блоком.
# ------------------------------------------------------------

async def inactivity_worker() -> None:
    while True:
        try:
            current_time = now()

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
                chat_id = safe_int(chat["chat_id"])
                interval = max(
                    60,
                    min(
                        86400,
                        safe_int(
                            chat["inactivity_interval"],
                            3600,
                        ),
                    ),
                )

                last_activity = safe_float(
                    chat["last_activity"]
                )

                if (
                    last_activity > 0
                    and current_time - last_activity >= interval
                ):
                    try:
                        members = await bot.get_chat_member_count(
                            chat_id
                        )
                    except Exception:
                        logger.warning(
                            "Размер группы %s не подтверждён; "
                            "событие не создаётся",
                            chat_id,
                        )
                        continue

                    if members < 30:
                        continue

                    # Таймер сбрасываем только после подтверждения API.
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

                    # Создание перекати-поля будет подключено
                    # в финальном игровом блоке.

        except Exception:
            logger.exception(
                "Ошибка inactivity_worker"
            )

        await asyncio.sleep(60)


# ------------------------------------------------------------
# STARTUP / SHUTDOWN
# ------------------------------------------------------------

async def main() -> None:
    await init_database()

    cleanup_task = asyncio.create_task(
        cleanup_worker(),
        name="cleanup_worker",
    )
    inactivity_task = asyncio.create_task(
        inactivity_worker(),
        name="inactivity_worker",
    )

    running_tasks.update(
        {
            cleanup_task,
            inactivity_task,
        }
    )

    try:
        await bot.delete_webhook(
            drop_pending_updates=True,
        )

        logger.info(
            "Бот запущен. База: %s",
            DB_NAME,
        )

        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )

    finally:
        for task in tuple(running_tasks):
            task.cancel()

        if running_tasks:
            await asyncio.gather(
                *running_tasks,
                return_exceptions=True,
            )

        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")


