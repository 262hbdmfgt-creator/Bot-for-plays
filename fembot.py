import asyncio
import logging
import sqlite3
import random
import string
import os
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
    BotCommand
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

# Стандартные веса редкости (сумма = 100%)
DEFAULT_WEIGHTS = {
    "редкое": 40,
    "ультра редкое": 25,
    "мифическое": 15,
    "легендарное": 12,
    "фембойский артефакт": 6,
    "редчайший фем": 2
}

# Порядок для сортировки в коллекции
RARITY_ORDER = {
    "редчайший фем": 1,
    "фембойский артефакт": 2,
    "легендарное": 3,
    "мифическое": 4,
    "ультра редкое": 5,
    "редкое": 6
}

# Порядок для крафта (из чего во что)
CRAFT_PROGRESSION = ["редкое", "ультра редкое", "мифическое", "легендарное", "фембойский артефакт", "редчайший фем"]

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
            luck_potion_expires TEXT
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
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_info', 'Администрация еще не настроила этот текст.')")
    
    # Автоматические миграции колонок для существующих баз
    migrations = [
        "ALTER TABLE users ADD COLUMN coins INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN last_daily TEXT",
        "ALTER TABLE users ADD COLUMN daily_streak INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN luck_potion_expires TEXT",
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
    if not execute_query("SELECT user_id FROM users WHERE user_id = ?", (user_id,), fetchone=True):
        execute_query("INSERT INTO users (user_id, username, joined_at) VALUES (?, ?, ?)", (user_id, username, now_str))
    else:
        execute_query("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
    if not execute_query("SELECT chat_id FROM chats WHERE chat_id = ?", (chat_id,), fetchone=True):
        execute_query("INSERT INTO chats (chat_id, chat_type) VALUES (?, ?)", (chat_id, chat_type))

def is_admin_user(user_id):
    res = execute_query("SELECT is_admin FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    return bool(res and res[0] == 1)

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

class IsAdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return is_admin_user(message.from_user.id)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
dp.message.outer_middleware(RegisterMiddleware())

# Отслеживание добавления/удаления бота из чатов
@dp.my_chat_member()
async def on_my_chat_member(update: types.ChatMemberUpdated):
    chat_id = update.chat.id
    chat_type = update.chat.type
    if update.new_chat_member.status in ["member", "administrator"]:
        execute_query("INSERT OR IGNORE INTO chats (chat_id, chat_type) VALUES (?, ?)", (chat_id, chat_type))
    elif update.new_chat_member.status in ["left", "kicked"]:
        execute_query("DELETE FROM chats WHERE chat_id = ?", (chat_id,))

# ----------------------------------------------------------------------
# ECONOMY & SHOP LOGIC
# ----------------------------------------------------------------------
def get_user_coins(user_id):
    res = execute_query("SELECT coins FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    return res[0] if res else 0

def add_coins(user_id, amount):
    execute_query("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, user_id))

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
            return await message.reply(f"⏳ Вы уже забирали бонус! Приходите через {hours} ч. {rem_sec//60} мин.")
        if now - last_daily > timedelta(hours=48): streak = 0

    new_streak = streak + 1
    reward = min(10 + (new_streak * 5), 100)
    add_coins(user_id, reward)
    execute_query("UPDATE users SET last_daily = ?, daily_streak = ? WHERE user_id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), new_streak, user_id))
    await message.reply(f"🎁 Награда получена! +{reward} коинов 🪙\nСерия: {new_streak} дн.")

@dp.message(Command("shop"))
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
    if get_user_coins(user_id) < 100: return await call.answer("❌ Недостаточно коинов!", show_alert=True)
    add_coins(user_id, -100)
    execute_query("UPDATE users SET last_foto_time = NULL WHERE user_id = ?", (user_id,))
    await call.message.edit_text("⚡️ Кулдаун успешно сброшен! Теперь вы можете использовать /foto.")

@dp.callback_query(F.data == "buy_potion")
async def buy_potion(call: CallbackQuery):
    user_id = call.from_user.id
    if get_user_coins(user_id) < 250: return await call.answer("❌ Недостаточно коинов!", show_alert=True)
    
    add_coins(user_id, -250)
    expires = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    execute_query("UPDATE users SET luck_potion_expires = ? WHERE user_id = ?", (expires, user_id))
    await call.message.edit_text("🍀 Вы купили **Эликсир удачи**! В течение следующих 2 часов шансы на редкие карты значительно выше.")

@dp.callback_query(F.data == "buy_epic")
async def buy_epic(call: CallbackQuery):
    user_id = call.from_user.id
    if get_user_coins(user_id) < 350: return await call.answer("❌ Недостаточно коинов!", show_alert=True)
    
    # Ищем карту высоких рангов (мифическое или легендарное) как эпический эквивалент
    epic_pool = execute_query("SELECT id, file_id, rarity, description FROM photos WHERE rarity IN ('мифическое', 'легендарное', 'фембойский артефакт')", fetchall=True)
    if not epic_pool:
        epic_pool = execute_query("SELECT id, file_id, rarity, description FROM photos", fetchall=True)
    if not epic_pool:
        return await call.answer("❌ В базе бота пока нет подходящих карт!", show_alert=True)
        
    p_id, f_id, rar, desc = random.choice(epic_pool)
    add_coins(user_id, -350)
    
    exists = execute_query("SELECT count FROM user_photos WHERE user_id = ? AND photo_id = ?", (user_id, p_id), fetchone=True)
    if exists:
        execute_query("UPDATE user_photos SET count = count + 1 WHERE user_id = ? AND photo_id = ?", (user_id, p_id))
    else:
        execute_query("INSERT INTO user_photos (user_id, photo_id, count) VALUES (?, ?, 1)", (user_id, p_id))
        
    await call.message.answer_photo(f_id, caption=f"📦 Покупка из магазина:\n⭐ Редкость: {rar.capitalize()}\n📝 {desc}", parse_mode="Markdown")
    await call.answer("Успешная покупка!")

# ----------------------------------------------------------------------
# CRAFT (СИНТЕЗ)
# ----------------------------------------------------------------------
@dp.message(Command("craft"))
async def cmd_craft(message: Message):
    if message.chat.type != "private": return await message.reply("🛠 Крафт доступен только в ЛС.")
    user_id = message.from_user.id
    counts = execute_query("SELECT p.rarity, SUM(up.count) FROM user_photos up JOIN photos p ON up.photo_id = p.id WHERE up.user_id = ? GROUP BY p.rarity", (user_id,), fetchall=True)
    
    btns = []
    for rar, total in counts:
        if total >= 3 and rar in CRAFT_PROGRESSION[:-1]:
            btns.append([InlineKeyboardButton(text=f"{rar.capitalize()} (x3 ➔ 1 выше)", callback_data=f"do_craft:{rar}")])
            
    if not btns: return await message.reply("🛠 У вас недостаточно дубликатов (нужно минимум 3 карты одной редкости).")
    await message.answer("🛠 *Меню Синтеза*\nВыберите, что переработать:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("do_craft:"))
async def do_craft(call: CallbackQuery):
    rarity = call.data.split(":")[1]
    user_id = call.from_user.id
    photos = execute_query("SELECT up.photo_id, up.count FROM user_photos up JOIN photos p ON up.photo_id = p.id WHERE up.user_id = ? AND p.rarity = ?", (user_id, rarity), fetchall=True)
    
    if sum(p[1] for p in photos) < 3: return await call.answer("❌ Недостаточно карт!")

    rem = 0
    for p_id, count in photos:
        to_rem = min(3 - rem, count)
        if count - to_rem <= 0: execute_query("DELETE FROM user_photos WHERE user_id = ? AND photo_id = ?", (user_id, p_id))
        else: execute_query("UPDATE user_photos SET count = count - ? WHERE user_id = ? AND photo_id = ?", (to_rem, user_id, p_id))
        rem += to_rem
        if rem >= 3: break
        
    target = CRAFT_PROGRESSION[CRAFT_PROGRESSION.index(rarity) + 1]
    new_p = execute_query("SELECT id FROM photos WHERE rarity = ? ORDER BY RANDOM() LIMIT 1", (target,), fetchone=True)
    
    if new_p:
        p_id = new_p[0]
        exists = execute_query("SELECT count FROM user_photos WHERE user_id = ? AND photo_id = ?", (user_id, p_id), fetchone=True)
        if exists: execute_query("UPDATE user_photos SET count = count + 1 WHERE user_id = ? AND photo_id = ?", (user_id, p_id))
        else: execute_query("INSERT INTO user_photos (user_id, photo_id, count) VALUES (?, ?, 1)", (user_id, p_id))
        await call.message.edit_text(f"✨ Успешный крафт! Получена карта: *{target.upper()}*!", parse_mode="Markdown")
    else:
        add_coins(user_id, 50)
        await call.message.edit_text("🎁 Крафт успешен! Вам начислено 50 коинов.")

# ----------------------------------------------------------------------
# CORE GAME LOGIC (/foto & triggers)
# ----------------------------------------------------------------------
@dp.message(Command("foto"))
@dp.message(F.text.lower() == "фем")
async def handle_foto(message: Message):
    user_id = message.from_user.id
    now = datetime.now()
    res = execute_query("SELECT last_foto_time, luck_potion_expires FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    
    if res and res[0]:
        last = datetime.strptime(res[0], "%Y-%m-%d %H:%M:%S")
        if now - last < timedelta(hours=3):
            rem = timedelta(hours=3) - (now - last)
            return await message.reply(f"⏳ Ждите {rem.seconds // 3600} ч. { (rem.seconds // 60) % 60} мин.\n💡 Используй `/promo КОД` или `/shop` для сброса!", parse_mode="Markdown")

    # Проверка зелья удачи
    weights = DEFAULT_WEIGHTS.copy()
    luck_expires = res[1] if res else None
    is_lucky = False
    if luck_expires and datetime.strptime(luck_expires, "%Y-%m-%d %H:%M:%S") > now:
        is_lucky = True
        # Увеличиваем шансы на редкие карты за счет обычных
        weights["редкое"] = 20
        weights["ультра редкое"] = 25
        weights["мифическое"] = 20
        weights["легендарное"] = 20
        weights["фембойский артефакт"] = 10
        weights["редчайший фем"] = 5

    chosen = random.choices(list(weights.keys()), weights=list(weights.values()), k=1)[0]
    photo = execute_query("SELECT id, file_id, rarity, description FROM photos WHERE rarity = ? ORDER BY RANDOM() LIMIT 1", (chosen,), fetchone=True)
    if not photo: photo = execute_query("SELECT id, file_id, rarity, description FROM photos ORDER BY RANDOM() LIMIT 1", fetchone=True)
    
    if not photo: return await message.reply("В базе бота пока нет фотографий.")
    
    p_id, f_id, rar, desc = photo
    is_dub = execute_query("SELECT 1 FROM user_photos WHERE user_id = ? AND photo_id = ?", (user_id, p_id), fetchone=True)
    
    prefix = "🍀 *Эликсир удачи сработал!*\n" if is_lucky else ""
    if is_dub:
        execute_query("UPDATE user_photos SET count = count + 1 WHERE user_id = ? AND photo_id = ?", (user_id, p_id))
        add_coins(user_id, 15)
        caption = f"{prefix}🃏 *ДУБЛИКАТ!*\n\n+15 коинов 🪙\n⭐ {rar.capitalize()}"
    else:
        execute_query("INSERT INTO user_photos (user_id, photo_id, count) VALUES (?, ?, 1)", (user_id, p_id))
        caption = f"{prefix}🎉 *НОВАЯ КАРТА!*\n\n⭐ Редкость: {rar.capitalize()}\n📝 {desc}"

    execute_query("UPDATE users SET last_foto_time = ? WHERE user_id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), user_id))
    await bot.send_photo(message.chat.id, f_id, caption=caption, parse_mode="Markdown")

    # Шанс 10% на сундук в группе
    if message.chat.type != "private" and random.random() < 0.10:
        reward = random.randint(20, 50)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📦 ОТКРЫТЬ!", callback_data=f"get_chest:{reward}")]])
        await bot.send_message(message.chat.id, "🎁 *ПОДАРОК В ЧАТЕ!*\nКто-то уронил сундук с монетами. Жми!", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("get_chest:"))
async def get_chest(call: CallbackQuery):
    reward = int(call.data.split(":")[1])
    add_coins(call.from_user.id, reward)
    await call.message.edit_text(f"🎉 Сундук открыл @{call.from_user.username or call.from_user.id}!\nНаграда: {reward} 🪙")

# ----------------------------------------------------------------------
# PROFILE & COLLECTION
# ----------------------------------------------------------------------
@dp.message(Command("profil"))
@dp.message(F.text.lower() == "профиль")
async def handle_profile(message: Message):
    user_id = message.from_user.id
    res = execute_query("SELECT coins, luck_potion_expires FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    coins = res[0] if res else 0
    potion = res[1] if res else None
    
    total = execute_query("SELECT SUM(count) FROM user_photos WHERE user_id = ?", (user_id,), fetchone=True)[0] or 0
    unique = execute_query("SELECT COUNT(*) FROM user_photos WHERE user_id = ?", (user_id,), fetchone=True)[0] or 0
    
    avatar = DEFAULT_AVATAR
    try:
        p = await bot.get_user_profile_photos(user_id, limit=1)
        if p.total_count > 0: avatar = p.photos[0][-1].file_id
    except: pass
    
    potion_text = ""
    if potion and datetime.strptime(potion, "%Y-%m-%d %H:%M:%S") > datetime.now():
        potion_text = "\n🍀 *Эликсир удачи активен!*"

    text = f"👤 *ПРОФИЛЬ*\n💰 Коины: {coins} 🪙\n🗂 Всего карт: {total}\n✨ Уникальных: {unique}{potion_text}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🖼 Коллекция", callback_data="view_collection:0")]])
    await message.reply_photo(avatar, caption=text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("view_collection:"))
async def view_coll(call: CallbackQuery):
    user_id, idx = call.from_user.id, int(call.data.split(":")[1])
    photos = execute_query("SELECT p.id, p.file_id, p.rarity, p.description, up.count FROM user_photos up JOIN photos p ON up.photo_id = p.id WHERE up.user_id = ?", (user_id,), fetchall=True)
    if not photos: return await call.answer("У вас пока нет карт!")
    
    sorted_p = sorted(photos, key=lambda x: RARITY_ORDER.get(x[2].lower(), 99))
    idx = max(0, min(idx, len(sorted_p) - 1))
    p_id, f_id, rar, desc, cnt = sorted_p[idx]
    
    text = f"🖼 *Коллекция* ({idx+1}/{len(sorted_p)})\n⭐ Редкость: {rar.capitalize()}\n📝 {desc}\n📦 В наличии: {cnt} шт."
    btns = []
    nav = []
    if idx > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"view_collection:{idx-1}"))
    if idx < len(sorted_p)-1: nav.append(InlineKeyboardButton(text="➡️", callback_data=f"view_collection:{idx+1}"))
    if nav: btns.append(nav)
    btns.append([InlineKeyboardButton(text="👤 Профиль", callback_data="back_prof")])
    await call.message.edit_media(InputMediaPhoto(media=f_id, caption=text, parse_mode="Markdown"), reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data == "back_prof")
async def back_prof(call: CallbackQuery):
    user_id = call.from_user.id
    res = execute_query("SELECT coins, luck_potion_expires FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    coins, potion = res[0], res[1]
    total = execute_query("SELECT SUM(count) FROM user_photos WHERE user_id = ?", (user_id,), fetchone=True)[0] or 0
    unique = execute_query("SELECT COUNT(*) FROM user_photos WHERE user_id = ?", (user_id,), fetchone=True)[0] or 0
    
    potion_text = "\n🍀 *Эликсир удачи активен!*" if potion and datetime.strptime(potion, "%Y-%m-%d %H:%M:%S") > datetime.now() else ""
    text = f"👤 *ПРОФИЛЬ*\n💰 Коины: {coins} 🪙\n🗂 Всего карт: {total}\n✨ Уникальных: {unique}{potion_text}"
    await call.message.edit_media(InputMediaPhoto(media=DEFAULT_AVATAR, caption=text, parse_mode="Markdown"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🖼 Коллекция", callback_data="view_collection:0")]]))

# ----------------------------------------------------------------------
# TRADE & STATS
# ----------------------------------------------------------------------
@dp.message(F.text.lower() == "обмен")
async def trade_init(message: Message):
    if not message.reply_to_message: return await message.reply("Ответьте `Обмен` на сообщение игрока!")
    target = message.reply_to_message.from_user
    if target.id == message.from_user.id: return
    me = await bot.get_me()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🤝 В ЛС", url=f"t.me/{me.username}?start=trade")]])
    await message.reply(f"🤝 Обмен с @{target.username}! Механика в ЛС.", reply_markup=kb)

@dp.message(Command("top"))
@dp.message(F.text.lower().in_(["топ", "статистика"]))
async def cmd_top(message: Message):
    rows = execute_query("SELECT u.username, u.user_id, SUM(up.count) as c, u.joined_at FROM users u LEFT JOIN user_photos up ON u.user_id = up.user_id GROUP BY u.user_id ORDER BY c DESC, u.joined_at ASC LIMIT 10", fetchall=True)
    text = "🏆 *ТОП-10 ИГРОКОВ:*\n\n"
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, (name, uid, c, _) in enumerate(rows):
        m = medals[i] if i < len(medals) else f"{i+1}."
        text += f"{m} *{name or uid}* — 📦 {c or 0} фото\n"
    await message.reply(text, parse_mode="Markdown")

# ----------------------------------------------------------------------
# PROMO & ADMIN COMMANDS (ОБЪЕДИНЕНЫ ВСЕ)
# ----------------------------------------------------------------------
def parse_duration(duration_str: str) -> timedelta:
    duration_str = duration_str.lower().strip()
    num = int(''.join(filter(str.isdigit, duration_str)) or 1)
    if "год" in duration_str or "лет" in duration_str: return timedelta(days=365 * num)
    elif "мес" in duration_str: return timedelta(days=30 * num)
    elif "дн" in duration_str: return timedelta(days=num)
    else: return timedelta(hours=num)

@dp.message(Command("creat_promo_1"), IsAdminFilter())
async def create_p1(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2: return await message.reply("Укажите срок! Пример: `/creat_promo_1 24 часа`", parse_mode="Markdown")
    exp = (datetime.now() + parse_duration(args[1])).strftime("%Y-%m-%d %H:%M:%S")
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    execute_query("INSERT INTO promo_codes (code, promo_type, expires_at) VALUES (?, 'single', ?)", (code, exp))
    await message.reply(f"🎟 Одноразовый промокод:\n`{code}`\nСрок: до {exp}", parse_mode="Markdown")

@dp.message(Command("create_promo_2"), IsAdminFilter())
async def create_p2(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2: return await message.reply("Укажите срок! Пример: `/create_promo_2 30 дней`", parse_mode="Markdown")
    exp = (datetime.now() + parse_duration(args[1])).strftime("%Y-%m-%d %H:%M:%S")
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    execute_query("INSERT INTO promo_codes (code, promo_type, expires_at) VALUES (?, 'multi', ?)", (code, exp))
    await message.reply(f"🎟 Многоразовый промокод:\n`{code}`\nСрок: до {exp}", parse_mode="Markdown")

@dp.message(Command("promo"))
async def activate_promo(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2: return await message.reply("Введите код: `/promo КОД`", parse_mode="Markdown")
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

@dp.message(Command("admin"))
@dp.message(F.text.lower() == "кто админ")
async def cmd_admin_info(message: Message):
    res = execute_query("SELECT value FROM settings WHERE key = 'admin_info'", fetchone=True)
    await message.reply(res[0] if res else "Информация не настроена.")

@dp.message(Command("set_admin_text"), IsAdminFilter())
async def set_admin_text(message: Message, state: FSMContext):
    await message.reply("Отправьте новый текст для /admin:")
    await state.set_state(AdminStates.waiting_for_admin_text)

@dp.message(AdminStates.waiting_for_admin_text, IsAdminFilter())
async def save_admin_text(message: Message, state: FSMContext):
    execute_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_info', ?)", (message.text,))
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
    execute_query("INSERT INTO photos (file_id, rarity, description) VALUES (?, ?, ?)", (data['f_id'], data['rar'], message.text))
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
        if not msgs: return await message.reply("Очередь пуста.")
        
        targets = {r[0] for r in execute_query("SELECT chat_id FROM chats", fetchall=True)} | {r[0] for r in execute_query("SELECT user_id FROM users", fetchall=True)}
        await message.reply(f"🚀 Рассылка пошла по {len(targets)} адресатам...")
        succ, err = 0, 0
        for t in targets:
            try:
                for m in msgs:
                    await bot.copy_message(chat_id=t, from_chat_id=m['chat_id'], message_id=m['message_id'])
                succ += 1
                await asyncio.sleep(0.05)
            except: err += 1
        await message.reply(f"🏁 Готово!\nУспешно: {succ}\nОшибок: {err}")
        await state.clear()
        return
        
    d = await state.get_data()
    msgs = d.get("msgs", [])
    msgs.append({"chat_id": message.chat.id, "message_id": message.message_id})
    await state.update_data(msgs=msgs)
    await message.reply(f"📥 Сообщение #{len(msgs)} добавлено в очередь.")

@dp.message(Command("now_admin_cod"), IsAdminFilter())
async def gen_admin_code(message: Message):
    code = "".join(random.sample(string.digits, 7) + random.sample(string.ascii_letters, 3))
    execute_query("INSERT INTO admin_codes (code, created_at, used) VALUES (?, ?, 0)", (code, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    await message.reply(f"🔑 Код админа (действует 3 часа):\n`{code}`\nВводить через `/i_am_admin`", parse_mode="Markdown")

@dp.message(Command("i_am_admin", "I_am_admin"))
async def become_admin(message: Message, state: FSMContext):
    if is_admin_user(message.from_user.id): return await message.reply("Вы уже админ!")
    await message.reply("🔑 Введите 10-значный код:")
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

# ----------------------------------------------------------------------
# MAIN
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
        BotCommand(command="admin", description="Информация об админах")
    ])
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
