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

# Названия редкостей
RARITIES_WEIGHTS = {
    "редкое": 40, 
    "ультра редкое": 25, 
    "мифическое": 15,
    "легендарное": 12, 
    "фембойский артефакт": 6, 
    "редчайший фем": 2
}

RARITY_ORDER = {
    "редчайший фем": 1, 
    "фембойский артефакт": 2, 
    "легендарное": 3,
    "мифическое": 4, 
    "ультра редкое": 5, 
    "редкое": 6
}

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
            daily_streak INTEGER DEFAULT 0
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
    
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN coins INTEGER DEFAULT 0")
    except: pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN last_daily TEXT")
    except: pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN daily_streak INTEGER DEFAULT 0")
    except: pass
    try:
        cursor.execute("ALTER TABLE user_photos ADD COLUMN count INTEGER DEFAULT 1")
    except: pass

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

# ----------------------------------------------------------------------
# MIDDLEWARES / FILTERS
# ----------------------------------------------------------------------
class RegisterMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.from_user:
            register_user_and_chat(event.from_user.id, event.from_user.username or "Unknown", event.chat.id, event.chat.type)
        return await handler(event, data)

class FsmStates(StatesGroup):
    broadcast = State()
    plus_photo = State()
    admin_text = State()
    admin_code = State()
    craft_select = State()

class IsAdminFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        res = execute_query("SELECT is_admin FROM users WHERE user_id = ?", (message.from_user.id,), fetchone=True)
        return bool(res and res[0] == 1)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
dp.message.outer_middleware(RegisterMiddleware())

# ----------------------------------------------------------------------
# ECONOMY & GAME
# ----------------------------------------------------------------------
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
        return await message.reply("🛍 Магазин доступен только в личке с ботом!")
    
    res = execute_query("SELECT coins FROM users WHERE user_id = ?", (message.from_user.id,), fetchone=True)
    coins = res[0] if res else 0
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚡️ Сброс КД (100 🪙)", callback_data="buy_reset")]])
    await message.answer(f"🛒 *Магазин Фем-товаров*\n\nВаш баланс: {coins} 🪙", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "buy_reset")
async def buy_reset(call: CallbackQuery):
    user_id = call.from_user.id
    res = execute_query("SELECT coins FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    if not res or res[0] < 100:
        return await call.answer("❌ Недостаточно коинов!", show_alert=True)
    
    add_coins(user_id, -100)
    execute_query("UPDATE users SET last_foto_time = NULL WHERE user_id = ?", (user_id,))
    await call.message.edit_text("⚡️ Кулдаун сброшен! Теперь вы можете использовать /foto.")

@dp.message(Command("craft"))
async def cmd_craft(message: Message):
    if message.chat.type != "private":
        return await message.reply("🛠 Крафт доступен только в ЛС.")
    
    user_id = message.from_user.id
    counts = execute_query(
        "SELECT p.rarity, SUM(up.count) FROM user_photos up JOIN photos p ON up.photo_id = p.id "
        "WHERE up.user_id = ? GROUP BY p.rarity", (user_id,), fetchall=True
    )
    
    btns = []
    for rar, total in counts:
        if total >= 3 and rar in CRAFT_PROGRESSION[:-1]:
            btns.append([InlineKeyboardButton(text=f"{rar.capitalize()} (x3 ➔ 1 выше)", callback_data=f"do_craft:{rar}")])
            
    if not btns:
        return await message.reply("🛠 У вас недостаточно дубликатов (нужно 3 карты одной редкости).")
    
    await message.answer("🛠 *Меню Синтеза*\nВыберите, что переработать:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("do_craft:"))
async def do_craft(call: CallbackQuery):
    rarity = call.data.split(":")[1]
    user_id = call.from_user.id
    
    photos = execute_query(
        "SELECT up.photo_id, up.count FROM user_photos up JOIN photos p ON up.photo_id = p.id "
        "WHERE up.user_id = ? AND p.rarity = ?", (user_id, rarity), fetchall=True
    )
    if sum(p[1] for p in photos) < 3:
        return await call.answer("❌ Недостаточно карт!")

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
        await call.message.edit_text("🎁 Крафт успешен! Вам начислено 50 коинов (карт выше нет в базе).")

@dp.callback_query(F.data.startswith("get_chest:"))
async def get_chest(call: CallbackQuery):
    reward = int(call.data.split(":")[1])
    add_coins(call.from_user.id, reward)
    await call.message.edit_text(f"🎉 Сундук открыл @{call.from_user.username or call.from_user.id}!\nНаграда: {reward} 🪙")

# ----------------------------------------------------------------------
# CORE LOGIC
# ----------------------------------------------------------------------
@dp.message(Command("foto"))
@dp.message(F.text.lower() == "фем")
async def handle_foto(message: Message):
    user_id = message.from_user.id
    now = datetime.now()
    res = execute_query("SELECT last_foto_time FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    
    if res and res[0]:
        last = datetime.strptime(res[0], "%Y-%m-%d %H:%M:%S")
        if now - last < timedelta(hours=3):
            rem = timedelta(hours=3) - (now - last)
            return await message.reply(f"⏳ Ждите {rem.seconds // 3600} ч. { (rem.seconds // 60) % 60} мин.")

    chosen = random.choices(list(RARITIES_WEIGHTS.keys()), weights=list(RARITIES_WEIGHTS.values()), k=1)[0]
    photo = execute_query("SELECT id, file_id, rarity, description FROM photos WHERE rarity = ? ORDER BY RANDOM() LIMIT 1", (chosen,), fetchone=True)
    if not photo: photo = execute_query("SELECT id, file_id, rarity, description FROM photos ORDER BY RANDOM() LIMIT 1", fetchone=True)
    
    if not photo: return await message.reply("База пуста.")
    
    p_id, f_id, rar, desc = photo
    is_dub = execute_query("SELECT 1 FROM user_photos WHERE user_id = ? AND photo_id = ?", (user_id, p_id), fetchone=True)
    
    if is_dub:
        execute_query("UPDATE user_photos SET count = count + 1 WHERE user_id = ? AND photo_id = ?", (user_id, p_id))
        add_coins(user_id, 15)
        caption = f"🃏 *ДУБЛИКАТ!*\n\n+15 коинов 🪙\n⭐ {rar.capitalize()}"
    else:
        execute_query("INSERT INTO user_photos (user_id, photo_id, count) VALUES (?, ?, 1)", (user_id, p_id))
        caption = f"🎉 *НОВАЯ КАРТА!*\n\n⭐ Редкость: {rar.capitalize()}\n📝 {desc}"

    execute_query("UPDATE users SET last_foto_time = ? WHERE user_id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), user_id))
    await bot.send_photo(message.chat.id, f_id, caption=caption, parse_mode="Markdown")

    if message.chat.type != "private" and random.random() < 0.10:
        reward = random.randint(20, 50)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📦 ОТКРЫТЬ!", callback_data=f"get_chest:{reward}")]])
        await bot.send_message(message.chat.id, "🎁 *ПОДАРОК В ЧАТЕ!*\nКто-то уронил сундук. Жми!", reply_markup=kb, parse_mode="Markdown")

@dp.message(Command("profil"))
@dp.message(F.text.lower() == "профиль")
async def handle_profile(message: Message):
    user_id = message.from_user.id
    res = execute_query("SELECT coins FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    coins = res[0] if res else 0
    total = execute_query("SELECT SUM(count) FROM user_photos WHERE user_id = ?", (user_id,), fetchone=True)[0] or 0
    unique = execute_query("SELECT COUNT(*) FROM user_photos WHERE user_id = ?", (user_id,), fetchone=True)[0] or 0
    
    avatar = DEFAULT_AVATAR
    try:
        p = await bot.get_user_profile_photos(user_id, limit=1)
        if p.total_count > 0: avatar = p.photos[0][-1].file_id
    except: pass
    
    text = f"👤 *ПРОФИЛЬ*\n💰 Коины: {coins} 🪙\n🗂 Всего карт: {total}\n✨ Уникальных: {unique}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🖼 Коллекция", callback_data="view_collection:0")]])
    await message.reply_photo(avatar, caption=text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("view_collection:"))
async def view_coll(call: CallbackQuery):
    user_id, idx = call.from_user.id, int(call.data.split(":")[1])
    photos = execute_query("SELECT p.id, p.file_id, p.rarity, p.description, up.count FROM user_photos up JOIN photos p ON up.photo_id = p.id WHERE up.user_id = ?", (user_id,), fetchall=True)
    if not photos: return await call.answer("Пусто!")
    
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
async def back_p(call: CallbackQuery):
    user_id = call.from_user.id
    res = execute_query("SELECT coins FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    coins, total = res[0], execute_query("SELECT SUM(count) FROM user_photos WHERE user_id = ?", (user_id,), fetchone=True)[0] or 0
    unique = execute_query("SELECT COUNT(*) FROM user_photos WHERE user_id = ?", (user_id,), fetchone=True)[0] or 0
    text = f"👤 *ПРОФИЛЬ*\n💰 Коины: {coins} 🪙\n🗂 Всего карт: {total}\n✨ Уникальных: {unique}"
    await call.message.edit_media(InputMediaPhoto(media=DEFAULT_AVATAR, caption=text, parse_mode="Markdown"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🖼 Коллекция", callback_data="view_collection:0")]]))

@dp.message(F.text.lower() == "обмен")
async def trade_init(message: Message):
    if not message.reply_to_message: return await message.reply("Ответьте `Обмен` на сообщение игрока!")
    target = message.reply_to_message.from_user
    if target.id == message.from_user.id: return
    me = await bot.get_me()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🤝 В ЛС", url=f"t.me/{me.username}?start=trade")]])
    await message.reply(f"🤝 Обмен с @{target.username}! Механика в ЛС.", reply_markup=kb)

@dp.message(Command("top"))
async def cmd_top(message: Message):
    rows = execute_query("SELECT u.username, u.user_id, SUM(up.count) as c, u.joined_at FROM users u LEFT JOIN user_photos up ON u.user_id = up.user_id GROUP BY u.user_id ORDER BY c DESC, u.joined_at ASC LIMIT 10", fetchall=True)
    text = "🏆 *ТОП ИГРОКОВ:*\n\n"
    for i, (name, uid, c, _) in enumerate(rows):
        text += f"{i+1}. *{name or uid}* — {c or 0} фото\n"
    await message.reply(text, parse_mode="Markdown")

# ----------------------------------------------------------------------
# ADMIN CMDS
# ----------------------------------------------------------------------
@dp.message(Command("plus_foto"), IsAdminFilter())
async def pf_start(message: Message, state: FSMContext):
    await message.reply("Отправьте фото:"); await state.set_state(FsmStates.plus_photo)

@dp.message(FsmStates.plus_photo, F.photo)
async def pf_photo(message: Message, state: FSMContext):
    await state.update_data(f_id=message.photo[-1].file_id)
    kb = [[InlineKeyboardButton(text=r.capitalize(), callback_data=f"rar:{r}")] for r in RARITIES_WEIGHTS.keys()]
    await message.reply("Редкость:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("rar:"))
async def pf_rar(call: CallbackQuery, state: FSMContext):
    await state.update_data(rar=call.data.split(":")[1])
    await call.message.edit_text("Введите описание:")
    await state.set_state(FsmStates.broadcast) # Используем как временный стейт для текста

@dp.message(FsmStates.broadcast, IsAdminFilter())
async def pf_fin(message: Message, state: FSMContext):
    d = await state.get_data()
    if 'f_id' in d and 'rar' in d:
        execute_query("INSERT INTO photos (file_id, rarity, description) VALUES (?, ?, ?)", (d['f_id'], d['rar'], message.text))
        await message.reply("✅ Добавлено!"); await state.clear()

# ----------------------------------------------------------------------
# INIT
# ----------------------------------------------------------------------
async def main():
    init_db()
    await bot.set_my_commands([
        BotCommand(command="foto", description="Получить фото"),
        BotCommand(command="profil", description="Профиль"),
        BotCommand(command="daily", description="Бонус дня"),
        BotCommand(command="shop", description="Магазин"),
        BotCommand(command="craft", description="Крафт"),
        BotCommand(command="top", description="Топ игроков")
    ])
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
