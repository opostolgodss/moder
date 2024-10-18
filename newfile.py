import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils.exceptions import BotBlocked

import aiosqlite
from tabulate import tabulate

# Токен бота
API_TOKEN = '7859351612:AAE9iP24j7cNnN8Ujy31T8R56WbwM8V-USM' 

# Имя файла базы данных
DATABASE_FILE = 'bot_database.db'

# Имя файла логов
LOG_FILE = 'chat_logs.html'

# Максимальное количество логов для выгрузки
MAX_LOGS_LIMIT = 1000

# ID администратора
ADMIN_ID = 7166220534

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)


# Создание базы данных и таблиц при запуске
async def create_database():
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                is_admin BOOLEAN DEFAULT FALSE,
                message_count INTEGER DEFAULT 0,
                chat_id INTEGER
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS chat_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                timestamp DATETIME,
                action TEXT,
                details TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                chat_title TEXT
            )
        ''')
        await db.commit()


# Функция для проверки, есть ли чат в базе данных
async def check_chat_exists(chat_id):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        cursor = await db.execute("SELECT 1 FROM chats WHERE chat_id = ?", (chat_id,))
        result = await cursor.fetchone()
        return result is not None


# Функция для добавления чата в базу данных
async def add_chat_to_database(chat_id, chat_title):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute("INSERT INTO chats (chat_id, chat_title) VALUES (?, ?)", (chat_id, chat_title))
        await db.commit()


# Функция для получения списка администраторов чата
async def get_admins(chat_id):
    admins = []
    chat_admins = await bot.get_chat_administrators(chat_id)
    for admin in chat_admins:
        admins.append(admin.user.id)
    return admins


# Обработчик команды /start
@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.reply("Добавьте меня в чат.")


# Обработчик добавления бота в чат
@dp.message_handler(content_types=types.ContentType.NEW_CHAT_MEMBERS)
async def new_chat_member(message: types.Message):
    if message.new_chat_members[0].id == bot.id:
        chat_id = message.chat.id
        chat_title = message.chat.title

        if not await check_chat_exists(chat_id):
            await add_chat_to_database(chat_id, chat_title)
            await message.reply("Привет! Я бот-модератор. Для работы мне нужны права администратора.")
        else:
            await message.reply("Я уже в этом чате.")


# Обработчик команды /mute (только по ID)
@dp.message_handler(commands=['mute'], chat_type=[types.ChatType.GROUP, types.ChatType.SUPERGROUP])
async def mute_user(message: types.Message):
    if message.from_user.id in await get_admins(message.chat.id):
        try:
            args = message.get_args().split()
            user_id = int(args[0])  # Получаем ID пользователя
            duration = int(args[1])

            until_date = datetime.now() + timedelta(minutes=duration)

            await bot.restrict_chat_member(
                message.chat.id,
                user_id,
                until_date=until_date,
                permissions=types.ChatPermissions(
                    can_send_messages=False
                )
            )

            # Оформление сообщения о муте
            muted_user = await bot.get_chat_member(message.chat.id, user_id)
            muted_username = muted_user.user.mention
            mute_duration_formatted = f"{duration} мин." if duration < 60 else f"{duration // 60} ч. {duration % 60} мин."

            await message.reply(
                f"🔇 {muted_username} был заглушен на {mute_duration_formatted}."
            )

            # Логирование действия
            await log_action(message.chat.id, message.from_user.id, 'mute',
                             f'User {user_id} muted for {duration} minutes.')

        except (IndexError, ValueError, BotBlocked):
            await message.reply(
                "⚠️ Неверный формат команды или пользователь не найден.\n"
                "Используйте: /mute [ID пользователя] [время в минутах]"
            )
    else:
        await message.reply("🚫 У вас нет прав на использование этой команды.")


# Обработчик команды /ban
@dp.message_handler(commands=['ban'], chat_type=[types.ChatType.GROUP, types.ChatType.SUPERGROUP])
async def ban_user(message: types.Message):
    if message.from_user.id in await get_admins(message.chat.id):
        try:
            args = message.get_args().split()
            user_arg = args[0]
            if len(args) > 1:
                duration = int(args[1])
                until_date = datetime.now() + timedelta(minutes=duration)
            else:
                until_date = None

            if user_arg.startswith('@'):
                try:
                    user = await bot.get_chat_member(message.chat.id, user_arg)
                    user_id = user.user.id
                    if user.status == 'left' or user.status == 'kicked':
                        raise BotBlocked("Пользователь не является участником чата.")
                except BotBlocked as e:
                    await message.reply(str(e))
                    return
            else:
                user_id = int(user_arg)

            await bot.kick_chat_member(
                message.chat.id,
                user_id,
                until_date=until_date
            )

            if until_date:
                await message.reply(f"⛔ Пользователь {user_id} был забанен на {duration} минут.")
                await log_action(message.chat.id, message.from_user.id, 'ban',
                                 f'User {user_id} banned for {duration} minutes.')
            else:
                await message.reply(f"⛔ Пользователь {user_id} был забанен.")
                await log_action(message.chat.id, message.from_user.id, 'ban',
                                 f'User {user_id} banned permanently.')


        except (IndexError, ValueError, BotBlocked):
            await message.reply(
                "⚠️ Неверный формат команды или пользователь не найден.\n"
                "Используйте: /ban @username [время в минутах]"
            )
    else:
        await message.reply("🚫 У вас нет прав на использование этой команды.")


# Обработчик команды /unban
@dp.message_handler(commands=['unban'], chat_type=[types.ChatType.GROUP, types.ChatType.SUPERGROUP])
async def unban_user(message: types.Message):
    if message.from_user.id in await get_admins(message.chat.id):
        try:
            args = message.get_args().split()
            user_arg = args[0]

            if user_arg.startswith('@'):
                try:
                    user = await bot.get_chat_member(message.chat.id, user_arg)
                    user_id = user.user.id
                    if user.status == 'left' or user.status == 'kicked':
                        raise BotBlocked("Пользователь не является участником чата.")
                except BotBlocked as e:
                    await message.reply(str(e))
                    return
            else:
                user_id = int(user_arg)

            await bot.unban_chat_member(
                message.chat.id,
                user_id
            )

            await message.reply(f"✅ Пользователь {user_id} был разбанен.")
            await log_action(message.chat.id, message.from_user.id, 'unban', f'User {user_id} unbanned.')


        except (IndexError, ValueError, BotBlocked):
            await message.reply(
                "⚠️ Неверный формат команды или пользователь не найден.\n"
                "Используйте: /unban @username"
            )
    else:
        await message.reply("🚫 У вас нет прав на использование этой команды.")


# Обработчик команды /unmute
@dp.message_handler(commands=['unmute'], chat_type=[types.ChatType.GROUP, types.ChatType.SUPERGROUP])
async def unmute_user(message: types.Message):
    if message.from_user.id in await get_admins(message.chat.id):
        try:
            args = message.get_args().split()
            user_arg = args[0]

            if user_arg.startswith('@'):
                try:
                    user = await bot.get_chat_member(message.chat.id, user_arg)
                    user_id = user.user.id
                    if user.status == 'left' or user.status == 'kicked':
                        raise BotBlocked("Пользователь не является участником чата.")
                except BotBlocked as e:
                    await message.reply(str(e))
                    return
            else:
                user_id = int(user_arg)

            await bot.restrict_chat_member(
                message.chat.id,
                user_id,
                permissions=types.ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True
                )
            )

            await message.reply(f"🔊 Пользователь {user_id} был размьючен.")

            # Логирование действия
            await log_action(message.chat.id, message.from_user.id, 'unmute', f'User {user_id} unmuted.')

        except (IndexError, ValueError, BotBlocked):
            await message.reply(
                "⚠️ Неверный формат команды или пользователь не найден.\n"
                "Используйте: /unmute @username"
            )
    else:
        await message.reply("🚫 У вас нет прав на использование этой команды.")


# Обработчик команды /logs
@dp.message_handler(commands=['logs'], chat_type=[types.ChatType.GROUP, types.ChatType.SUPERGROUP])
async def get_logs(message: types.Message):
    if message.from_user.id in await get_admins(message.chat.id):
        await generate_log_file(message.chat.id)
        with open(LOG_FILE, 'rb') as f:
            await message.reply_document(f)
    else:
        await message.reply("🚫 У вас нет прав на использование этой команды.")


# Обработчик команды /admins
@dp.message_handler(commands=['admins'], chat_type=[types.ChatType.GROUP, types.ChatType.SUPERGROUP])
async def get_admins_list(message: types.Message):
    admins = await get_admins(message.chat.id)
    admin_list = []
    for admin_id in admins:
        try:
            admin_user = await bot.get_chat_member(message.chat.id, admin_id)
            admin_list.append(admin_user.user.mention)
        except:
            pass
    if admin_list:
        await message.reply(f"👑 Список администраторов:\n{', '.join(admin_list)}")
    else:
        await message.reply("⚠️ Администраторы не найдены.")


# Обработчик команды /top
@dp.message_handler(commands=['top'], chat_type=[types.ChatType.GROUP, types.ChatType.SUPERGROUP])
async def get_top_users(message: types.Message):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        cursor = await db.execute('''
            SELECT username, message_count
            FROM users
            WHERE chat_id = ?
            ORDER BY message_count DESC
            LIMIT 10
        ''', (message.chat.id,))
        rows = await cursor.fetchall()

    if rows:
        table = tabulate(rows, headers=["Пользователь", "Сообщений"], tablefmt="pretty")
        await message.reply(f"🏆 Топ 10 самых общительных пользователей:\n```\n{table}\n```")
    else:
        await message.reply("⚠️ Нет данных об активности пользователей.")


# Обработчик всех сообщений в чате (для подсчета сообщений пользователей)
@dp.message_handler(chat_type=[types.ChatType.GROUP, types.ChatType.SUPERGROUP])
async def count_messages(message: types.Message):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('''
            INSERT OR IGNORE INTO users (user_id, username, chat_id)
            VALUES (?, ?, ?)
        ''', (message.from_user.id, message.from_user.username, message.chat.id))
        await db.execute('''
            UPDATE users
            SET message_count = message_count + 1
            WHERE user_id = ? AND chat_id = ?
        ''', (message.from_user.id, message.chat.id))
        await db.commit()


# Функция для логирования действий
async def log_action(chat_id, user_id, action, details):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('''
            INSERT INTO chat_logs (chat_id, user_id, timestamp, action, details)
            VALUES (?, ?, ?, ?, ?)
        ''', (chat_id, user_id, datetime.now(), action, details))
        await db.commit()


# Функция для генерации HTML-файла с логами (с лимитом и watermark)
async def generate_log_file(chat_id):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        cursor = await db.execute(f'''
            SELECT timestamp, user_id, action, details
            FROM chat_logs
            WHERE chat_id = ?
            ORDER BY timestamp DESC
            LIMIT {MAX_LOGS_LIMIT} 
        ''', (chat_id,))
        rows = await cursor.fetchall()

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Логи чата</title>
        <style>
            body {
                font-family: sans-serif;
            }
            table {
                width: 100%;
                border-collapse: collapse;
            }
            th, td {
                padding: 8px;
                text-align: left;
                border-bottom: 1px solid #ddd;
            }
            tr:hover {
                background-color: #f5f5f5;
            }
            #search-results {
                margin-top: 20px;
            }
            #watermark {
                position: fixed;
                bottom: 10px;
                right: 10px;
                opacity: 0.5;
                font-size: 12px;
            }
        </style>
    </head>
    <body>
        <h1>Логи чата</h1>
        <input type="text" id="search-input" placeholder="Поиск по ID пользователя...">
        <button id="search-button" onclick="searchLogs()">Найти</button> <div id="reset-button" style="display:none;"><button onclick="resetSearch()">Сбросить</button></div>

        <table id="log-table">
            <thead>
                <tr>
                    <th>Время</th>
                    <th>ID пользователя</th>
                    <th>Действие</th>
                    <th>Детали</th>
                </tr>
            </thead>
            <tbody>
    """

    for row in rows:
        html += f"""
                <tr>
                    <td>{row[0]}</td>
                    <td>{row[1]}</td>
                    <td>{row[2]}</td>
                    <td>{row[3]}</td>
                </tr>
        """

    html += """
            </tbody>
        </table>

        <div id="search-results"></div>

        <script>
            function searchLogs() {
                const searchTerm = document.getElementById('search-input').value.trim();
                if (searchTerm === '') {
                    return;
                }

                const rows = document.getElementById('log-table').querySelectorAll('tbody tr');
                let found = false;
                document.getElementById('search-results').innerHTML = '';

                rows.forEach(row => {
                    const userId = row.cells[1].textContent;
                    if (userId.includes(searchTerm)) {
                        document.getElementById('search-results').appendChild(row.cloneNode(true));
                        found = true;
                    }
                    row.style.display = userId.includes(searchTerm) ? '' : 'none'; 
                });

                if (!found) {
                    document.getElementById('search-results').innerHTML = '<p>Ничего не найдено.</p>';
                }
                
                document.getElementById('reset-button').style.display = 'block'; 
            }

            function resetSearch() {
                const rows = document.getElementById('log-table').querySelectorAll('tbody tr');
                rows.forEach(row => {
                    row.style.display = ''; 
                });
                document.getElementById('search-input').value = ''; 
                document.getElementById('search-results').innerHTML = '';
                document.getElementById('reset-button').style.display = 'none';
            }
        </script>

        <div id="watermark">@IntenisenModerator_Bot</div> 

    </body>
    </html>
    """

    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        f.write(html)


# Обработчик команды /admin (только в личке и только для администратора)
@dp.message_handler(commands=['admin'], chat_type=types.ChatType.PRIVATE)
async def admin_command(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        async with aiosqlite.connect(DATABASE_FILE) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM users")
            total_users = (await cursor.fetchone())[0]
            cursor = await db.execute("SELECT COUNT(*) FROM chats")
            total_chats = (await cursor.fetchone())[0]

        await message.reply(
            f"Всего пользователей в БД: {total_users}\n"
            f"Всего чатов: {total_chats}",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("Рассылка", callback_data="broadcast")
            )
        )
    else:
        await message.reply("У вас нет доступа к этой команде.")


# Обработчик нажатия кнопки "Рассылка"
@dp.callback_query_handler(lambda c: c.data == "broadcast")
async def broadcast_callback(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    await bot.send_message(callback_query.from_user.id, "Введите текст для рассылки:")
    await BroadcastState.waiting_for_message.set()


# Состояния для рассылки
class BroadcastState(StatesGroup):
    waiting_for_message = State()
    waiting_for_photo = State()
    waiting_for_button_text = State()
    waiting_for_button_url = State()


# Обработчик текста для рассылки
@dp.message_handler(state=BroadcastState.waiting_for_message, chat_type=types.ChatType.PRIVATE)
async def broadcast_message(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        async with state.proxy() as data:
            data['message_text'] = message.text

        await bot.send_message(message.from_user.id, "Отправьте фото для рассылки (необязательно):")
        await BroadcastState.waiting_for_photo.set()
    else:
        await message.reply("У вас нет доступа к этой команде.")


# Обработчик фото для рассылки
@dp.message_handler(content_types=types.ContentType.PHOTO, state=BroadcastState.waiting_for_photo,
                    chat_type=types.ChatType.PRIVATE)
async def broadcast_photo(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        async with state.proxy() as data:
            data['photo'] = message.photo[-1].file_id

        await bot.send_message(message.from_user.id, "Введите текст для кнопки (необязательно):")
        await BroadcastState.waiting_for_button_text.set()
    else:
        await message.reply("У вас нет доступа к этой команде.")


# Обработчик текста кнопки для рассылки (если фото не было отправлено)
@dp.message_handler(state=BroadcastState.waiting_for_photo, chat_type=types.ChatType.PRIVATE)
async def broadcast_no_photo(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        await bot.send_message(message.from_user.id, "Введите текст для кнопки (необязательно):")
        await BroadcastState.waiting_for_button_text.set()
    else:
        await message.reply("У вас нет доступа к этой команде.")


# Обработчик текста кнопки для рассылки
@dp.message_handler(state=BroadcastState.waiting_for_button_text, chat_type=types.ChatType.PRIVATE)
async def broadcast_button_text(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        async with state.proxy() as data:
            data['button_text'] = message.text

        await bot.send_message(message.from_user.id, "Введите ссылку для кнопки (необязательно):")
        await BroadcastState.waiting_for_button_url.set()
    else:
        await message.reply("У вас нет доступа к этой команде.")


# Обработчик ссылки кнопки для рассылки
@dp.message_handler(state=BroadcastState.waiting_for_button_url, chat_type=types.ChatType.PRIVATE)
async def broadcast_button_url(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        async with state.proxy() as data:
            data['button_url'] = message.text

        async with aiosqlite.connect(DATABASE_FILE) as db:
            cursor = await db.execute("SELECT user_id, chat_id FROM users")
            users = await cursor.fetchall()

        for user_id, chat_id in users:
            try:
                keyboard = None
                if data.get('button_text') and data.get('button_url'):
                    keyboard = types.InlineKeyboardMarkup().add(
                        types.InlineKeyboardButton(data['button_text'], url=data['button_url'])
                    )

                if data.get('photo'):
                    await bot.send_photo(chat_id, data['photo'], caption=data['message_text'],
                                         reply_markup=keyboard)
                else:
                    await bot.send_message(chat_id, data['message_text'], reply_markup=keyboard)
            except Exception as e:
                logging.error(f"Ошибка при отправке сообщения пользователю {user_id} в чат {chat_id}: {e}")

        await message.reply("Рассылка завершена.")
        await state.finish()
    else:
        await message.reply("У вас нет доступа к этой команде.")


async def on_startup(dp):
    await create_database()


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)