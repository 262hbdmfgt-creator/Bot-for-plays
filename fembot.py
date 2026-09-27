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
# КОНФИГУРАЦИЯ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ (RAILWAY)
# ----------------------------------------------------------------------
API_TOKEN = os.getenv("BOT_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0"))
DB_NAME = os.getenv("DB_PATH", "fembot_database.db")

# Создаем папку для базы данных, если она указана (например, /data/)
db_dir = os.path.dirname(DB_NAME)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)

# Логирование
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Веса редкости для генерации фото (в процентах)
RARITIES_WEIGHTS = {
    "редкое": 40,
    "ультра редкое": 25,
    "мифическое": 15,
    "легендарное": 12,
    "фембойский артефакт": 6,
    "редчайший фем": 2
}

# Порядок сортировки в коллекции (1 — самое редкое, 6 — менее редкое)
RARITY_ORDER = {
    "редчайший фем": 1,
    "фембойский артефакт": 2,
    "легендарное": 3,
    "мифическое": 4,
    "ультра редкое": 5,
    "редкое": 6
}

# Аватар по умолчанию
DEFAULT_AVATAR = "https://placehold.co/600x600/png?text=No+Avatar"

# ----------------------------------------------------------------------
# РАБОТА С БАЗОЙ ДАННЫХ SQLite
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
            joined_at TEXT
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
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            promo_type TEXT,
            expires_at TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promo_uses (
            code TEXT,
            user_id INTEGER,
            PRIMARY KEY (code, user_id),
            FOREIGN KEY (code) REFERENCES promo_codes(code),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_info', 'Администрация еще не настроила этот текст. Используйте /set_admin_text для его изменения.')")
    
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
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user_exists = execute_query("SELECT user_id FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    if not user_exists:
        execute_query(
            "INSERT INTO users (user_id, username, is_admin, last_foto_time, joined_at) VALUES (?, ?, 0, NULL, ?)", 
            (user_id, username, now_str)
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
# MIDDLEWARE И ФИЛЬТРЫ
# ----------------------------------------------------------------------
class RegisterMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.from_user:
            register_user_and_chat(
                user_id=event.from_user.id,
                username=event.from_user.username or "Unknown",
                chat_id=event.chat.id,
                chat_type=event.chat.type
            )
        return await handler(event, data)

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

# Инициализация бота
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
# АДМИНИСТРАТИВНЫЕ КОМАНДЫ
# ----------------------------------------------------------------------

@dp.message(Command("set_admin_text"), IsAdminFilter())
async def set_admin_text_start(message: Message, state: FSMContext):
    await message.reply("Отправьте новый текст для команды /admin:")
    await state.set_state(AdminStates.waiting_for_admin_text)

@dp.message(AdminStates.waiting_for_admin_text, IsAdminFilter())
async def set_admin_text_save(message: Message, state: FSMContext):
    execute_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_info', ?)", (message.text,))
    await message.reply("✅ Сообщение администрации успешно изменено!")
    await state.clear()

# 1) Команда /plus_foto
@dp.message(Command("plus_foto"), IsAdminFilter())
async def plus_foto_start(message: Message, state: FSMContext):
    await message.reply("📸 Отправьте фотографию, которую хотите добавить в базу:")
    await state.set_state(AdminStates.waiting_for_photo)

@dp.message(AdminStates.waiting_for_photo, IsAdminFilter(), F.photo)
async def plus_foto_photo(message: Message, state: FSMContext):
    await state.update_data(file_id=message.photo[-1].file_id)
    buttons = [[InlineKeyboardButton(text=r.capitalize(), callback_data=f"set_rarity:{r}")] for r in RARITIES_WEIGHTS.keys()]
    await message.reply("✨ Выберите редкость для этого фото:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(AdminStates.waiting_for_rarity)

@dp.callback_query(AdminStates.waiting_for_rarity, F.data.startswith("set_rarity:"), IsAdminFilter())
async def plus_foto_rarity(callback: CallbackQuery, state: FSMContext):
    rarity = callback.data.split(":")[1]
    await state.update_data(rarity=rarity)
    await callback.message.edit_text(f"Вы выбрали редкость: *{rarity.capitalize()}*.\nТеперь отправьте описание к фото:", parse_mode="Markdown")
    await state.set_state(AdminStates.waiting_for_description)

@dp.message(AdminStates.waiting_for_description, IsAdminFilter(), F.text)
async def plus_foto_desc(message: Message, state: FSMContext):
    data = await state.get_data()
    execute_query("INSERT INTO photos (file_id, rarity, description) VALUES (?, ?, ?)", (data.get("file_id"), data.get("rarity"), message.text))
    await message.reply("🎉 Фотография успешно добавлена в базу данных!")
    await state.clear()

# 2) Команда /add ... /save
@dp.message(Command("add"), IsAdminFilter())
async def add_broadcast_start(message: Message, state: FSMContext):
    await message.reply("📢 *Режим создания рассылки активирован.*\n\nОтправляйте любые сообщения. Для завершения и запуска отправьте `/save`.", parse_mode="Markdown")
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
    await message.reply(f"📥 Сообщение #{len(messages_list)} записано в очередь.")

async def run_broadcast(message: Message, state: FSMContext):
    data = await state.get_data()
    messages_list = data.get("broadcast_messages", [])
    if not messages_list:
        await message.reply("⚠️ Очередь рассылки пуста.")
        await state.clear()
        return
        
    targets = {row[0] for row in execute_query("SELECT chat_id FROM chats", fetchall=True)} | {row[0] for row in execute_query("SELECT user_id FROM users", fetchall=True)}
    await message.reply(f"🚀 Начинаю рассылку по {len(targets)} чатам...")
    
    success, fail = 0, 0
    for target_chat_id in targets:
        try:
            for msg in messages_list:
                await bot.copy_message(chat_id=target_chat_id, from_chat_id=msg["chat_id"], message_id=msg["message_id"])
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail += 1
            
    await message.reply(f"🏁 *Рассылка завершена!*\n\n✅ Успешно: *{success}*\n❌ Ошибки: *{fail}*", parse_mode="Markdown")
    await state.clear()

# 3) Команда /now_admin_cod
@dp.message(Command("now_admin_cod"), IsAdminFilter())
async def generate_admin_code_cmd(message: Message):
    code = "".join(random.sample(string.digits, 7) + random.sample(string.ascii_letters, 3))
    execute_query("INSERT INTO admin_codes (code, created_at, used) VALUES (?, ?, 0)", (code, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    await message.reply(f"🔑 *Сгенерирован код администратора:*\n\n`{code}`\n\nДействует 3 часа. Вводить через `/i_am_admin`.", parse_mode="Markdown")

@dp.message(Command("i_am_admin", "I_am_admin"))
async def I_am_admin_start(message: Message, state: FSMContext):
    if is_admin_user(message.from_user.id):
        await message.reply("Вы уже являетесь администратором бота!")
        return
    await message.reply("🔑 Введите 10-значный код администратора:")
    await state.set_state(AdminStates.waiting_for_admin_code)

@dp.message(AdminStates.waiting_for_admin_code)
async def I_am_admin_verify(message: Message, state: FSMContext):
    input_code = message.text.strip()
    code_data = execute_query("SELECT created_at, used FROM admin_codes WHERE code = ?", (input_code,), fetchone=True)
    
    if not code_data or code_data[1] == 1 or datetime.now() - datetime.strptime(code_data[0], "%Y-%m-%d %H:%M:%S") > timedelta(hours=3):
        await message.reply("❌ Неверный, использованный или истекший код.")
        await state.clear()
        return
        
    execute_query("UPDATE admin_codes SET used = 1 WHERE code = ?", (input_code,))
    execute_query("UPDATE users SET is_admin = 1 WHERE user_id = ?", (message.from_user.id,))
    await message.reply("🎉 Поздравляем! Вы успешно получили права администратора бота.", parse_mode="Markdown")
    await state.clear()


# ----------------------------------------------------------------------
# НОВЫЙ ФУНКЦИОНАЛ: ПРОМОКОДЫ (1 и 2)
# ----------------------------------------------------------------------

# Вспомогательная функция парсинга срока действия (от 1 часа до 1 года)
def parse_duration(duration_str: str) -> timedelta:
    duration_str = duration_str.lower().strip()
    num = int(''.join(filter(str.isdigit, duration_str)) or 1)
    
    if "год" in duration_str or "лет" in duration_str or "year" in duration_str:
        return timedelta(days=365 * num)
    elif "мес" in duration_str or "month" in duration_str:
        return timedelta(days=30 * num)
    elif "дн" in duration_str or "день" in duration_str or "day" in duration_str:
        return timedelta(days=num)
    elif "час" in duration_str or "hour" in duration_str:
        return timedelta(hours=num)
    else:
        # По умолчанию считаем часы
        return timedelta(hours=num)

# Команда /creat_promo_1 (одноразовый промокод для сброса кд)
@dp.message(Command("creat_promo_1"), IsAdminFilter())
async def create_promo_1_cmd(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply(
            "⚠️ Укажите срок действия промокода!\n"
            "Пример: `/creat_promo_1 24 часа` или `/creat_promo_1 7 дней`",
            parse_mode="Markdown"
        )
        return
        
    duration_delta = parse_duration(args[1])
    expires_at = (datetime.now() + duration_delta).strftime("%Y-%m-%d %H:%M:%S")
    
    # Генерация случайного красивого промокода (8 символов)
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    
    execute_query(
        "INSERT INTO promo_codes (code, promo_type, expires_at) VALUES (?, 'single', ?)",
        (code, expires_at)
    )
    
    await message.reply(
        f"🎟 *Создан ОДНОРАЗОВЫЙ промокод (сброс кд)*:\n\n"
        f"`{code}`\n\n"
        f"⏳ Срок действия: до *{expires_at}*\n"
        f"Использовать могут разные люди, но каждый промокод действует суммарно на 1 активацию (или можно сделать индивидуальным — см. логику ниже).",
        parse_mode="Markdown"
    )

# Команда /create_promo_2 (многоразовый промокод, каждый юзер может использовать 1 раз)
@dp.message(Command("create_promo_2"), IsAdminFilter())
async def create_promo_2_cmd(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply(
            "⚠️ Укажите срок действия промокода!\n"
            "Пример: `/create_promo_2 30 дней` или `/create_promo_2 1 год`",
            parse_mode="Markdown"
        )
        return
        
    duration_delta = parse_duration(args[1])
    expires_at = (datetime.now() + duration_delta).strftime("%Y-%m-%d %H:%M:%S")
    
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    
    execute_query(
        "INSERT INTO promo_codes (code, promo_type, expires_at) VALUES (?, 'multi', ?)",
        (code, expires_at)
    )
    
    await message.reply(
        f"🎟 *Создан МНОГОРАЗОВЫЙ промокод (сброс кд)*:\n\n"
        f"`{code}`\n\n"
        f"⏳ Срок действия: до *{expires_at}*\n"
        f"Каждый пользователь может активировать этот код ровно 1 раз.",
        parse_mode="Markdown"
    )

# Активация промокода пользователем: /promo КОД
@dp.message(Command("promo"))
async def activate_promo_cmd(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("⚠️ Введите промокод после команды. Пример: `/promo ABC123XYZ`", parse_mode="Markdown")
        return
        
    code = args[1].strip().upper()
    user_id = message.from_user.id
    
    promo = execute_query("SELECT promo_type, expires_at FROM promo_codes WHERE code = ?", (code,), fetchone=True)
    if not promo:
        await message.reply("❌ Такого промокода не существует или он введен неверно.")
        return
        
    promo_type, expires_at_str = promo
    
    # Проверка срока действия
    if datetime.now() > datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S"):
        await message.reply("⏳ Срок действия этого промокода истёк.")
        return
        
    if promo_type == 'single':
        # Проверяем, использовал ли его хоть кто-то
        uses_count = execute_query("SELECT COUNT(*) FROM promo_uses WHERE code = ?", (code,), fetchone=True)[0]
        if uses_count > 0:
            await message.reply("❌ Этот одноразовый промокод уже был кем-то активирован.")
            return
        # Фиксируем использование и сбрасываем КД
        execute_query("INSERT INTO promo_uses (code, user_id) VALUES (?, ?)", (code, user_id))
        execute_query("UPDATE users SET last_foto_time = NULL WHERE user_id = ?", (user_id,))
        await message.reply("🎉 *Промокод успешно активирован!*\n\n⏳ Ваше 3-часовое ожидание сброшено! Вы можете прямо сейчас получить фото через `/foto`.", parse_mode="Markdown")
        
    elif promo_type == 'multi':
        # Проверяем, использовал ли его именно этот юзер
        already_used = execute_query("SELECT 1 FROM promo_uses WHERE code = ? AND user_id = ?", (code, user_id), fetchone=True)
        if already_used:
            await message.reply("❌ Вы уже использовали этот многоразовый промокод ранее.")
            return
            
        execute_query("INSERT INTO promo_uses (code, user_id) VALUES (?, ?)", (code, user_id))
        execute_query("UPDATE users SET last_foto_time = NULL WHERE user_id = ?", (user_id,))
        await message.reply("🎉 *Промокод успешно активирован!*\n\n⏳ Ваше 3-часовое ожидание сброшено! Вы можете прямо сейчас получить фото через `/foto`.", parse_mode="Markdown")


# ----------------------------------------------------------------------
# НОВЫЙ ФУНКЦИОНАЛ: СТАТИСТИКА И ТОП-10 ИГРОКОВ (2)
# ----------------------------------------------------------------------
@dp.message(Command("top"), F.text.func(lambda text: text and text.lower().strip() in ["статистика", "топ", "top"]))
@dp.message(Command("stat"))
async def handle_stats_cmd(message: Message):
    # Запрос на топ-10 игроков:
    # Считаем количество уникальных фото у каждого пользователя.
    # Сортировка: 
    # 1) по количеству фото (по убыванию DESC)
    # 2) по дате регистрации / старости (по возрастанию ASC — кто раньше зарегистрировался, тот выше)
    top_users = execute_query(
        """
        SELECT u.username, u.user_id, COUNT(up.photo_id) as foto_count, u.joined_at
        FROM users u
        LEFT JOIN user_photos up ON u.user_id = up.user_id
        GROUP BY u.user_id
        ORDER BY foto_count DESC, u.joined_at ASC
        LIMIT 10
        """,
        fetchall=True
    )
    
    if not top_users:
        await message.reply("📊 Статистика пока пуста.")
        return
        
    text = "🏆 *Топ-10 Игроков Коллекционеров:*\n\n"
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    for idx, (username, u_id, count, joined_at) in enumerate(top_users):
        medal = medals[idx] if idx < len(medals) else f"{idx+1}."
        name = f"@{username}" if username and username != "Unknown" else f"ID: {u_id}"
        text += f"{medal} *{name}* — 📦 *{count}* фото\n"
        
    await message.reply(text, parse_mode="Markdown")


# ----------------------------------------------------------------------
# ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ И ТРИГГЕРЫ (Фем, Профиль, Кто админ)
# ----------------------------------------------------------------------

# 1) Команда /foto или триггер «фем»
@dp.message(Command("foto"))
@dp.message(F.text.func(lambda text: text and text.lower().strip() == "фем"))
async def handle_foto_cmd(message: Message):
    user_id = message.from_user.id
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
                f"⏳ Вы уже брали фото! Попробуйте снова через:\n*{hours:02d}:{minutes:02d}:{seconds:02d}*.\n\n"
                f"💡 _Активируйте промокод командой `/promo КОД`, чтобы пропустить ожидание!_",
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
            await message.reply("😔 В базе пока нет фото. Обратитесь к администраторам.")
            return
        photo_id, file_id, real_rarity, description = photos[0]
        chosen_rarity = real_rarity
    else:
        photo_id, file_id, description = random.choice(photos)
        
    execute_query("INSERT OR IGNORE INTO user_photos (user_id, photo_id) VALUES (?, ?)", (user_id, photo_id))
    execute_query("UPDATE users SET last_foto_time = ? WHERE user_id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), user_id))
    
    caption = f"🎉 *Вы получили новое фото!*\n\n⭐ *Редкость:* {chosen_rarity.capitalize()}\n📝 *Описание:* {description}"
    await bot.send_photo(chat_id=message.chat.id, photo=file_id, caption=caption, parse_mode="Markdown")

# 2) Команда /profil или триггер «профиль»
@dp.message(Command("profil"))
@dp.message(F.text.func(lambda text: text and text.lower().strip() == "профиль"))
async def handle_profile_cmd(message: Message):
    user_id = message.from_user.id
    user_photos_count = execute_query("SELECT COUNT(DISTINCT photo_id) FROM user_photos WHERE user_id = ?", (user_id,), fetchone=True)[0]
    total_photos_count = execute_query("SELECT COUNT(*) FROM photos", fetchone=True)[0]
    
    avatar_url = DEFAULT_AVATAR
    try:
        user_profile_photos = await bot.get_user_profile_photos(user_id, limit=1)
        if user_profile_photos.total_count > 0:
            avatar_url = user_profile_photos.photos[0][-1].file_id
    except Exception as e:
        logging.warning(f"Ошибка получения аватара: {e}")

    caption = f"👤 *Ваш Профиль:*\n\n🏷 *Имя:* {message.from_user.full_name}\n📦 *Ваша коллекция:* {user_photos_count} из {total_photos_count} фото.\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🖼 Смотреть все фото", callback_data="view_collection:0")]])
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
        await callback.answer("😢 У вас пока нет ни одного фото в коллекции!", show_alert=True)
        return
        
    sorted_photos = sorted(user_photos, key=lambda x: RARITY_ORDER.get(x[2].lower(), 99))
    total_photos = len(sorted_photos)
    index = max(0, min(index, total_photos - 1))
    
    photo_id, file_id, rarity, description = sorted_photos[index]
    caption = f"🖼 *Ваша Коллекция* ({index + 1} из {total_photos})\n\n⭐ *Редкость:* {rarity.capitalize()}\n📝 *Описание:* {description}"
    
    buttons = []
    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"view_collection:{index - 1}"))
    if index < total_photos - 1:
        nav_row.append(InlineKeyboardButton(text="След. ➡️", callback_data=f"view_collection:{index + 1}"))
    if nav_row:
        buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(text="👤 Вернуться в профиль", callback_data="back_to_profile")])
    
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

    caption = f"👤 *Ваш Профиль:*\n\n🏷 *Имя:* {callback.from_user.full_name}\n📦 *Ваша коллекция:* {user_photos_count} из {total_photos_count} фото.\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🖼 Смотреть все фото", callback_data="view_collection:0")]])
    await callback.message.edit_media(media=InputMediaPhoto(media=avatar_url, caption=caption, parse_mode="Markdown"), reply_markup=keyboard)

# 3) Команда /admin или триггер «кто админ»
@dp.message(Command("admin"))
@dp.message(F.text.func(lambda text: text and text.lower().strip() == "кто админ"))
async def handle_admin_info_cmd(message: Message):
    admin_info = execute_query("SELECT value FROM settings WHERE key = 'admin_info'", fetchone=True)
    await message.reply(admin_info[0] if admin_info else "Администрация не настроена.")

# ----------------------------------------------------------------------
# ЗАПУСК БОТА
# ----------------------------------------------------------------------
async def main():
    if not API_TOKEN:
        raise ValueError("BOT_TOKEN не установлен в переменных окружения!")
    init_db()
    
    await bot.set_my_commands([
        BotCommand(command="foto", description="Получить фем-фото (раз в 3 часа)"),
        BotCommand(command="profil", description="Посмотреть свой профиль и коллекцию"),
        BotCommand(command="admin", description="Информация об админах"),
        BotCommand(command="i_am_admin", description="Запросить права админа по коду"),
        BotCommand(command="stat", description="Топ-10 игроков и статистика"),
        BotCommand(command="promo", description="Активировать промокод")
    ])
    
    logging.info("Бот успешно запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
