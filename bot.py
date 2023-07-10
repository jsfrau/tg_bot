import asyncio
import functools
import os
import logging
import sqlite3
from typing import Callable
#import ffmpeg_downloader as ffdl
import subprocess as sp
from io import BytesIO
from telegram import File, InlineKeyboardButton, InlineKeyboardMarkup, User, Update, Bot, Message
from telegram.ext import CommandHandler, ApplicationBuilder, MessageHandler, CallbackContext, CallbackQueryHandler, \
    filters
import openai
from dotenv import load_dotenv

is_debug = False
max_page_size = 15



def log_send_function(func: Callable[[Update, CallbackContext], Message] = None, print_log_to_chat: bool = True):
    def decorator(func: Callable[[Update, CallbackContext], Message]):
        # Создаем файловый обработчик логов
        file_handler = logging.FileHandler(filename='latest.log', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)

        # Создаем форматтер для логов
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)

        # Создаем логгер и добавляем в него обработчик
        logger = logging.getLogger(func.__name__)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

        @functools.wraps(func)
        async def handler(update: Update, context: CallbackContext):
            signature = ", ".join((str(update), str(context)))
            logger.debug(f"Function {func.__name__} called with args {signature}")

            def log_messages(count: int):
                messages = get_current_context_messages(update.message.from_user, count)
                for message in messages:
                    if message['role'] == 'user': logger.info(
                        f"{update.message.from_user.username}: {message['content']}")
                    if message['role'] == 'assistant': logger.info(
                        f"{context.bot.username} to {update.message.from_user.username}: {message['content']}")

            try:
                result: Message = await func(update, context)
                logger.debug(f'Function {func.__name__} returned {result}')
                log_messages(2 if result else 1)
                return result
            except Exception as e:
                logger.exception(f"Exception raised in {func.__name__}. exception: {str(e)}")
                log_messages(1)
                error = f"Необработанная ошибка: {e}"
                if 'Please reduce the length of the messages.' in error:
                    error = 'Контекст перегружен, необходимо спросить его с помощью /reset'
                if is_debug: print(error)
                if print_log_to_chat:
                    return await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        reply_to_message_id=update.message.message_id,
                        text=error
                    )

        return handler

    return decorator(func) if func else decorator


def resrict_access(func: Callable[[Update, CallbackContext], Message] = None, check_exists: bool = True,
                   check_access: bool = True):
    def decorator(func: Callable[[Update, CallbackContext], Message]):
        @functools.wraps(func)
        async def handler(update: Update, context: CallbackContext):
            user = update.message.from_user
            if check_exists and not is_user_exists(user):
                return await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="Вы не запустили бота, необходимо прописать /start"
                )
            if check_access and not is_user_has_access(user):
                return await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="У вас нет доступа"
                )
            return await func(update, context)

        return handler

    return decorator(func) if func else decorator


def create_user_if_not_exists(user: User) -> None:
    global conn
    with conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT * FROM users
            WHERE user_id = ?
        """, (user.id,))
        users = cursor.fetchall()
        if not users:
            cursor.execute(f"""
                INSERT INTO users (user_id, username)
                VALUES (?, ?)
            """, (user.id, user.username))
            conn.commit()


def is_user_exists(user: User) -> bool:
    global conn
    with conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT * FROM users
            WHERE user_id = ? Or username = ?;
        """, (user.id, user.username))
        result = cursor.fetchone()
        return bool(result)


def is_user_has_access(user: User) -> bool:
    global conn
    with conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT * FROM users
            WHERE user_id = ? OR username = ?
        """, (user.id, user.username))
        result = cursor.fetchone()
        return bool(result['has_access']) if result else False


def change_user_access(user: User) -> None:
    global conn
    current_access = is_user_has_access(user)
    with conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            UPDATE users
            SET has_access = ?
            WHERE user_id = ? OR username = ?
        """, (0 if current_access else 1, user.id, user.username))
        conn.commit()


def remove_user(user: User) -> None:
    global conn
    with conn:
        for context in get_user_contexts(user):
            remove_context(user, context['id'])
        cursor = conn.cursor()
        cursor.execute(f"""
            DELETE FROM users
            WHERE user_id = ?
        """, (user.id,))
        conn.commit()


def get_last_context_id(user: User) -> int | None:
    global conn
    with conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT * FROM contexts
            WHERE user_id = ?
            ORDER BY id DESC
        """, (user.id,))
        result = cursor.fetchone()
        return result['id'] if result else None


def get_current_context_id(user: User) -> int | None:
    global conn
    with conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT * FROM users
            WHERE user_id = ?
        """, (user.id,))
        result = cursor.fetchone()
        return result['current_context_id']


def set_current_context_id(user: User, context_id: int) -> dict[str, str]:
    global conn
    with conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            UPDATE users
            SET current_context_id = ?
            WHERE user_id = ?
        """, (context_id, user.id))
        conn.commit()
        cursor.execute(f"""
            SELECT id, context_name FROM contexts
            WHERE id = ?
        """, (context_id,))
        return cursor.fetchone()


def get_user_contexts(user: User) -> list[dict[str, str]]:
    global conn
    with conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT id, context_name FROM contexts
            WHERE user_id = ?
        """, (user.id,))
        return cursor.fetchall()


def create_context(user: User, context_name: str) -> None:
    global conn
    with conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO contexts (user_id, context_name)
            VALUES (?, ?)
        """, (user.id, context_name))
        conn.commit()
        cursor.execute("""
            SELECT * FROM contexts
            WHERE user_id = ?
            ORDER BY id DESC
        """, (user.id,))
        current_context_id = cursor.fetchone()['id']
        set_current_context_id(user, current_context_id)
        append_current_context(user, 'You are useful assistant.')


def append_current_context(user: User, message: str, role: str = 'system') -> None:
    global conn
    current_context_id = get_current_context_id(user)
    if not current_context_id:
        create_context(user, 'Новый чат')
    with conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO messages (context_id, role, content)
            VALUES (?, ?, ?)
        """, (current_context_id, role, message))
        conn.commit()


def remove_context(user: User, context_id: int = None) -> None:
    global conn
    current_context_id = context_id or get_current_context_id(user)
    if not current_context_id: return
    with conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            DELETE FROM messages
            WHERE context_id = ?
        """, (current_context_id,))
        conn.commit()
        cursor.execute(f"""
            DELETE FROM contexts
            WHERE id = ?
        """, (current_context_id,))
        conn.commit()


def reset_current_context(user: User) -> None:
    global conn
    current_context_id = get_current_context_id(user)
    with conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            DELETE FROM messages
            WHERE context_id = ? AND role != 'system'
        """, (current_context_id,))
        conn.commit()


def rename_current_context(user: User, context_name: str) -> None:
    global conn
    current_context_id = get_current_context_id(user)
    with conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            UPDATE contexts
            SET context_name = ?
            WHERE id = ?
        """, (context_name, current_context_id))
        conn.commit()


def get_current_context_messages(user: User, count: int = 0) -> list[dict[str, str]]:
    global conn
    current_context_id = get_current_context_id(user)
    with conn:
        cursor = conn.cursor()
        if count:
            cursor.execute(f"""
                SELECT role, content FROM (
                    SELECT id, role, content FROM messages
                    WHERE context_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
            """, (current_context_id, count))
        else:
            cursor.execute(f"""
                SELECT role, content FROM messages
                WHERE context_id = ?
                ORDER BY id ASC
            """, (current_context_id,))
        return cursor.fetchall()



def get_contexts_markup(user: User, current_page: int = 0) -> InlineKeyboardMarkup:
    buttons = []
    user_contexts = get_user_contexts(user)
    start_index = current_page * max_page_size
    end_index = (current_page + 1) * max_page_size
    for i in range(start_index, end_index):
        if i > len(user_contexts) - 1:
            break
        button = InlineKeyboardButton(f"{i + 1}. {user_contexts[i]['context_name']}",
                                      callback_data=f"{user.id}.change_context.{user_contexts[i]['id']}")
        buttons.append([button])
    page_buttons = []
    if current_page > 0:
        page_buttons.append(InlineKeyboardButton('<', callback_data=f"{user.id}.page.{current_page - 1}"))
    page_buttons.append(InlineKeyboardButton(f"+", callback_data=f"{user.id}.create_context."))
    if end_index < len(user_contexts):
        page_buttons.append(InlineKeyboardButton('>', callback_data=f"{user.id}.page.{current_page + 1}"))
    if page_buttons:
        buttons.append(page_buttons)
    return InlineKeyboardMarkup(buttons)


async def button_change_context_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    data = query.data.split('.')
    user = User(data[0], '', False)
    # Обрабатываем нажатие на кнопку смены страницы
    if data[1] == 'page':
        await query.edit_message_reply_markup(reply_markup=get_contexts_markup(user, int(data[2])))
    # Обрабатываем нажатие на кнопку смены контекста
    elif data[1] == 'change_context':
        context = set_current_context_id(user, int(data[2]))
        # await query.answer()
        await query.edit_message_text(text="Выбранный контекст: {}".format(context['context_name']))
    # Обрабатываем остальные нажатия на кнопки
    elif data[1] == 'create_context':
        create_context(user, 'Новый чат')
        await query.edit_message_text(text=f"Создан контекст")
    else:
        # await query.answer()
        await query.edit_message_text(text=f"Неизвестное действие {data}")


# Обработчик команды /change_access
# Только для @jsfrau
@log_send_function(print_log_to_chat=True)
async def change_access(update: Update, context: CallbackContext) -> Message:
    user = update.message.from_user
    if user.id == 513525121:  # @jsfrau
        prompt = update.message.text.split(' ', 1)
        username = prompt[1].lstrip('@').split('/')[-1] if len(prompt) > 1 else ''
        user_to_change = User(-1, '', False, '', username)
        if username and is_user_exists(user_to_change):
            change_user_access(user_to_change)
            return await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"У пользователя @{username} поменялся доступ на {is_user_has_access(user_to_change)}"
            )
        else:
            return await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Такого пользователя нет в системе"
            )


# Обработчик команды /start
@log_send_function
@resrict_access(check_exists=False, check_access=False)
async def start(update: Update, context: CallbackContext) -> Message:
    user = update.message.from_user
    create_user_if_not_exists(user)
    if not get_current_context_id(user):
        create_context(user, 'Новый чат')
        return await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Привет, Я бот, который может генерировать текст на основе запросов с помощью OpenAI"
        )
    else:
        return await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Вы уже запустили бота"
        )


# Обработчик команды /stop
@log_send_function
@resrict_access(check_access=False)
async def stop(update: Update, context: CallbackContext) -> Message:
    user = update.message.from_user
    remove_user(user)
    return await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Останавливаю"
    )


# Обработчик команды /remove
@log_send_function
@resrict_access
async def remove(update: Update, context: CallbackContext) -> Message:
    user = update.message.from_user
    remove_context(user)
    last_context_id = get_last_context_id(user)
    if last_context_id:
        set_current_context_id(user, last_context_id)
    else:
        create_context(user, 'Новый чат')
    return await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Удалил текущий контекст"
    )


# Обработчик команды /reset
@log_send_function
@resrict_access
async def reset(update: Update, context: CallbackContext) -> Message:
    user = update.message.from_user
    reset_current_context(user)
    return await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Сбросил текущий контекст"
    )


# Обработчик команды /change
@log_send_function
@resrict_access
async def change(update: Update, context: CallbackContext) -> Message:
    user = update.message.from_user
    return await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Выберите контекст:",
        reply_markup=get_contexts_markup(user)
    )


# Обработчик команды /rename
@log_send_function
@resrict_access
async def rename(update: Update, context: CallbackContext) -> Message:
    user = update.message.from_user
    prompt = update.message.text.split(' ', 1)[1]
    if not prompt:
        return await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Команда /rename используется вместе с новым названием контекста"
        )
    rename_current_context(user, prompt)
    return await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Переименовал текущий контекст в `{prompt}`"
    )


# Обработчик сообщений
@log_send_function
@resrict_access
async def message(update: Update, context: CallbackContext) -> Message:
    user = update.message.from_user
    chat_id = update.effective_chat.id
    await bot.send_chat_action(chat_id=chat_id, action="typing")

    prompt = ''
    if update.message.text:
        prompt = update.message.text
    if update.message.voice:
        file_info: File = await context.bot.getFile(update.message.voice.file_id)
        #result = sp.run([ffdl.ffmpeg_path, '-i', file_info.file_path, '-f', 'mp3', 'pipe:1'], capture_output=True)
        #with BytesIO(result.stdout) as mp3_file:
            #mp3_file.name = 'file.mp3'
            #transcript = await openai.Audio.atranscribe("whisper-1", mp3_file)
           # prompt = transcript['text']
    append_current_context(user, prompt, 'user')
    messages = messages = get_current_context_messages(user, count=15)


    completion = await openai.ChatCompletion.acreate(
        model="gpt-3.5-turbo",
        messages=messages
    )
    bot_answer = completion.choices[0].message

    append_current_context(user, bot_answer.content, 'assistant')
    if len(bot_answer.content) < 4000:
        return await context.bot.send_message(
            chat_id=chat_id,
            reply_to_message_id=update.message.message_id,
            text=bot_answer.content
            #     .replace('.', '\\.')
            #     .replace("-", "\\-")
            #     .replace("_", "\\_")
            #     .replace("!", "\\!")
            #     .replace("*", "\\*")
            #     .replace("+", "\\+")
            #     .replace("=", "\\=")
            #     .replace("~", "\\~")
            #     .replace("#", "\\#")
            #     .replace("[", "\\[")
            #     .replace("]", "\\]")
            #     .replace("(", "\\(")
            #     .replace(")", "\\)"),
            # parse_mode='MarkdownV2'
        )
    else:
        parts = [bot_answer.content[i:i + 4000] for i in range(0, len(bot_answer.content), 4000)]
        for i in range(len(parts)):
            await context.bot.send_message(
                chat_id=chat_id,
                reply_to_message_id=update.message.message_id if i == 0 else None,
                text=parts[i]
            )


if __name__ == "__main__":
    load_dotenv()
    openai.api_key = "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"
    debug = False

    global conn
    conn: sqlite3.Connection = sqlite3.Connection('db.sqlite')
    # conn.row_factory = sqlite3.Row
    conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
    with conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            current_context_id INTEGER NULL,
            has_access INTEGER DEFAULT 0 NOT NULL
        )
        """)
        conn.commit()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS contexts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            context_name TEXT NOT NULL
        )
        """)
        conn.commit()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            context_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL
        )
        """)
        conn.commit()

    queue = asyncio.Queue()
    application = ApplicationBuilder().token("6285314131:AAEQFGX1nxar21ar0RBFVy5XH4bkjRSrZqs").build()
    bot = Bot("6285314131:AAEQFGX1nxar21ar0RBFVy5XH4bkjRSrZqs")

    change_access_handler = CommandHandler('change_access', change_access)
    application.add_handler(change_access_handler)

    start_handler = CommandHandler('start', start)
    application.add_handler(start_handler)

    remove_handler = CommandHandler('remove', remove)
    application.add_handler(remove_handler)

    reset_handler = CommandHandler('reset', reset)
    application.add_handler(reset_handler)

    change_handler = CommandHandler('change', change)
    application.add_handler(change_handler)

    rename_handler = CommandHandler('rename', rename)
    application.add_handler(rename_handler)

    stop_handler = CommandHandler('stop', stop)
    application.add_handler(stop_handler)

    button_change_context_handler = CallbackQueryHandler(button_change_context_callback)
    application.add_handler(button_change_context_handler)

    message_handler = MessageHandler(filters.TEXT | filters.VOICE, message)
    application.add_handler(message_handler)

    application.run_polling()