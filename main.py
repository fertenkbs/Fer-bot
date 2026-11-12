
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MessageEntity,
    ChatMemberUpdated,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv


# =====================================================
# CONFIG
# =====================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()

DB_PATH = "/data/bot.db"

os.makedirs("/data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


# =====================================================
# DEFAULT SETTINGS
# =====================================================

DEFAULTS = {
    "first_text": (
        "🤖 Подтвердите, что вы человек, нажав на кнопку ниже\n"
        "После этого ваша заявка будет автоматически принята."
    ),
    "second_text": (
        "✅ Ваша заявка успешно принята!\n\n"
        "В нашем канале вы сможете зарабатывать на написании "
        "простых отзывов без вложений и опыта."
    ),
    "first_entities": [],
    "second_entities": [],
    "first_button_text": "Я человек",
    "second_button_text": "Перейти в канал",
    "channel_url": "https://t.me/+UedIEFA7C0liOTky",
    "first_button_style": "success",
    "second_button_style": "primary",
    "first_button_icon": "",
    "second_button_icon": "",
}


# =====================================================
# FSM
# =====================================================

class AdminStates(StatesGroup):
    first_text = State()
    second_text = State()

    first_button_text = State()
    second_button_text = State()

    channel_url = State()

    style_target = State()
    icon_target = State()

    broadcast = State()


# =====================================================
# BOT
# =====================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=None
    ),
)

dp = Dispatcher(storage=MemoryStorage())


def is_admin(user_id):
    return user_id in ADMIN_IDS


# =====================================================
# DATABASE INIT + MIGRATION
# =====================================================

async def init_db():

    os.makedirs("/data", exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_at TEXT,
                confirmed_at TEXT,
                channel_joined_at TEXT
            )
        """)

        # Проверяем старую структуру таблицы
        async with db.execute("PRAGMA table_info(users)") as cursor:
            columns = {
                row[1]
                for row in await cursor.fetchall()
            }

        # Автоматическая миграция
        migrations = {
            "username": "ALTER TABLE users ADD COLUMN username TEXT",
            "first_name": "ALTER TABLE users ADD COLUMN first_name TEXT",
            "joined_at": "ALTER TABLE users ADD COLUMN joined_at TEXT",
            "confirmed_at": "ALTER TABLE users ADD COLUMN confirmed_at TEXT",
            "channel_joined_at": "ALTER TABLE users ADD COLUMN channel_joined_at TEXT",
        }

        for column, sql in migrations.items():
            if column not in columns:
                await db.execute(sql)
                logger.info("Added column: %s", column)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sent_at TEXT,
                total INTEGER,
                successful INTEGER,
                failed INTEGER
            )
        """)

        for key, value in DEFAULTS.items():
            await db.execute(
                """
                INSERT OR IGNORE INTO settings (key, value)
                VALUES (?, ?)
                """,
                (key, json.dumps(value, ensure_ascii=False)),
            )

        await db.commit()

    logger.info("Database initialized: %s", DB_PATH)


# =====================================================
# DATABASE FUNCTIONS
# =====================================================

async def get_setting(key):

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ) as cursor:
            row = await cursor.fetchone()

    if row is None:
        return DEFAULTS.get(key)

    return json.loads(row[0])


async def set_setting(key, value):

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value
            """,
            (
                key,
                json.dumps(value, ensure_ascii=False),
            ),
        )

        await db.commit()


async def register_user(user):

    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            INSERT INTO users (
                user_id,
                username,
                first_name,
                joined_at
            )
            VALUES (?, ?, ?, ?)

            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (
                user.id,
                user.username,
                user.first_name,
                now,
            ),
        )

        await db.commit()


async def confirm_user(user_id):

    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            UPDATE users
            SET confirmed_at = COALESCE(confirmed_at, ?)
            WHERE user_id = ?
            """,
            (now, user_id),
        )

        await db.commit()


async def mark_channel_member(user_id, is_member):

    async with aiosqlite.connect(DB_PATH) as db:

        if is_member:

            now = datetime.now(timezone.utc).isoformat()

            await db.execute(
                """
                UPDATE users
                SET channel_joined_at = ?
                WHERE user_id = ?
                """,
                (now, user_id),
            )

        else:

            await db.execute(
                """
                UPDATE users
                SET channel_joined_at = NULL
                WHERE user_id = ?
                """,
                (user_id,),
            )

        await db.commit()


async def get_statistics():

    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute("""
            SELECT
                COUNT(*),
                COUNT(confirmed_at),
                COUNT(channel_joined_at)
            FROM users
        """) as cursor:

            row = await cursor.fetchone()

    return {
        "total": row[0],
        "confirmed": row[1],
        "channel_members": row[2],
    }


async def get_users():

    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute("""
            SELECT
                user_id,
                username,
                first_name,
                joined_at,
                confirmed_at,
                channel_joined_at
            FROM users
            ORDER BY joined_at DESC
        """) as cursor:

            return await cursor.fetchall()


async def get_user_ids():

    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute(
            "SELECT user_id FROM users"
        ) as cursor:

            rows = await cursor.fetchall()

    return [row[0] for row in rows]


# =====================================================
# ENTITIES / PREMIUM EMOJI
# =====================================================

def restore_entities(data):

    if not data:
        return None

    return [
        MessageEntity.model_validate(item)
        for item in data
    ]


def serialize_entities(entities):

    if not entities:
        return []

    return [
        entity.model_dump(exclude_none=True)
        for entity in entities
    ]


# =====================================================
# KEYBOARDS
# =====================================================

async def first_keyboard(user_id):

    text = await get_setting("first_button_text")
    style = await get_setting("first_button_style")
    icon = await get_setting("first_button_icon")

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=text,
            callback_data="confirm_human",
            style=style,
            icon_custom_emoji_id=icon or None,
        )
    )

    if is_admin(user_id):

        builder.row(
            InlineKeyboardButton(
                text="⚙️ Админ-панель",
                callback_data="admin:open",
            )
        )

    return builder.as_markup()


async def second_keyboard():

    text = await get_setting("second_button_text")
    style = await get_setting("second_button_style")
    icon = await get_setting("second_button_icon")
    url = await get_setting("channel_url")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text,
                    url=url,
                    style=style,
                    icon_custom_emoji_id=icon or None,
                )
            ]
        ]
    )


# =====================================================
# USER START
# =====================================================

@dp.message(CommandStart())
async def start_handler(message: Message):

    await register_user(message.from_user)

    text = await get_setting("first_text")
    entities = await get_setting("first_entities")

    await message.answer(
        text,
        entities=restore_entities(entities),
        reply_markup=await first_keyboard(
            message.from_user.id
        ),
    )


# =====================================================
# CONFIRMATION
# =====================================================

@dp.callback_query(F.data == "confirm_human")
async def confirm_handler(callback: CallbackQuery):

    user = callback.from_user

    await register_user(user)
    await confirm_user(user.id)

    text = await get_setting("second_text")
    entities = await get_setting("second_entities")

    await callback.answer("Заявка принята!")

    try:

        await callback.message.edit_text(
            text,
            entities=restore_entities(entities),
            reply_markup=await second_keyboard(),
        )

    except TelegramAPIError:

        await callback.message.answer(
            text,
            entities=restore_entities(entities),
            reply_markup=await second_keyboard(),
        )


# =====================================================
# CHANNEL MEMBERSHIP
# =====================================================

@dp.chat_member()
async def channel_member_handler(event: ChatMemberUpdated):

    if not CHANNEL_ID:
        return

    if str(event.chat.id) != CHANNEL_ID:
        return

    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status

    active = {
        "member",
        "administrator",
        "creator",
    }

    was_member = old_status in active
    is_member = new_status in active

    if was_member != is_member:

        await mark_channel_member(
            event.new_chat_member.user.id,
            is_member,
        )


# =====================================================
# ADMIN KEYBOARD
# =====================================================

def admin_keyboard():

    builder = InlineKeyboardBuilder()

    buttons = [
        ("📝 Первое сообщение", "admin:edit:first_text"),
        ("📝 Второе сообщение", "admin:edit:second_text"),
        ("🔘 Названия кнопок", "admin:edit:buttons"),
        ("🔗 Ссылка на канал", "admin:edit:link"),
        ("🎨 Цвет кнопок", "admin:edit:styles"),
        ("🌟 Premium Emoji кнопок", "admin:edit:icons"),
        ("📊 Статистика", "admin:stats"),
        ("👥 Пользователи", "admin:users"),
        ("📨 Рассылка", "admin:broadcast"),
    ]

    for text, data in buttons:
        builder.row(
            InlineKeyboardButton(
                text=text,
                callback_data=data,
            )
        )

    return builder.as_markup()


# =====================================================
# ADMIN ACCESS
# =====================================================

async def check_admin(callback):

    if not is_admin(callback.from_user.id):

        await callback.answer(
            "Доступ запрещён",
            show_alert=True,
        )

        return False

    return True


@dp.message(Command("admin"))
async def admin_command(message: Message):

    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "⚙️ Панель администратора",
        reply_markup=admin_keyboard(),
    )


@dp.callback_query(F.data == "admin:open")
async def admin_open(callback: CallbackQuery):

    if not await check_admin(callback):
        return

    await callback.answer()

    await callback.message.answer(
        "⚙️ Панель администратора",
        reply_markup=admin_keyboard(),
    )


# =====================================================
# EDIT FIRST / SECOND MESSAGE
# =====================================================

@dp.callback_query(F.data == "admin:edit:first_text")
async def edit_first_text(callback: CallbackQuery, state: FSMContext):

    if not await check_admin(callback):
        return

    await callback.answer()
    await state.set_state(AdminStates.first_text)

    await callback.message.answer(
        "Отправьте новый текст первого сообщения.\n"
        "Premium Emoji и форматирование сохраняются."
    )


@dp.callback_query(F.data == "admin:edit:second_text")
async def edit_second_text(callback: CallbackQuery, state: FSMContext):

    if not await check_admin(callback):
        return

    await callback.answer()
    await state.set_state(AdminStates.second_text)

    await callback.message.answer(
        "Отправьте новый текст второго сообщения.\n"
        "Premium Emoji и форматирование сохраняются."
    )


@dp.message(AdminStates.first_text)
async def save_first_text(message: Message, state: FSMContext):

    if not is_admin(message.from_user.id):
        return

    value = message.text or message.caption

    if value is None:
        await message.answer("Отправьте текст или подпись к медиа.")
        return

    entities = message.entities or message.caption_entities

    await set_setting("first_text", value)
    await set_setting(
        "first_entities",
        serialize_entities(entities),
    )

    await state.clear()

    await message.answer(
        "✅ Первое сообщение обновлено.",
        reply_markup=admin_keyboard(),
    )


@dp.message(AdminStates.second_text)
async def save_second_text(message: Message, state: FSMContext):

    if not is_admin(message.from_user.id):
        return

    value = message.text or message.caption

    if value is None:
        await message.answer("Отправьте текст или подпись к медиа.")
        return

    entities = message.entities or message.caption_entities

    await set_setting("second_text", value)
    await set_setting(
        "second_entities",
        serialize_entities(entities),
    )

    await state.clear()

    await message.answer(
        "✅ Второе сообщение обновлено.",
        reply_markup=admin_keyboard(),
    )


# =====================================================
# BUTTON LABELS
# =====================================================

@dp.callback_query(F.data == "admin:edit:buttons")
async def edit_buttons(callback: CallbackQuery):

    if not await check_admin(callback):
        return

    await callback.answer()

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Первая кнопка",
                    callback_data="admin:edit:first_button",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Вторая кнопка",
                    callback_data="admin:edit:second_button",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data="admin:open",
                )
            ],
        ]
    )

    await callback.message.answer(
        "Выберите кнопку:",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data == "admin:edit:first_button")
async def edit_first_button(callback: CallbackQuery, state: FSMContext):

    if not await check_admin(callback):
        return

    await callback.answer()
    await state.set_state(AdminStates.first_button_text)

    await callback.message.answer("Введите новое название первой кнопки.")


@dp.callback_query(F.data == "admin:edit:second_button")
async def edit_second_button(callback: CallbackQuery, state: FSMContext):

    if not await check_admin(callback):
        return

    await callback.answer()
    await state.set_state(AdminStates.second_button_text)

    await callback.message.answer("Введите новое название второй кнопки.")


@dp.message(AdminStates.first_button_text)
async def save_first_button(message: Message, state: FSMContext):

    if not is_admin(message.from_user.id):
        return

    if not message.text:
        await message.answer("Отправьте текст.")
        return

    await set_setting("first_button_text", message.text)

    await state.clear()

    await message.answer(
        "✅ Название первой кнопки изменено.",
        reply_markup=admin_keyboard(),
    )


@dp.message(AdminStates.second_button_text)
async def save_second_button(message: Message, state: FSMContext):

    if not is_admin(message.from_user.id):
        return

    if not message.text:
        await message.answer("Отправьте текст.")
        return

    await set_setting("second_button_text", message.text)

    await state.clear()

    await message.answer(
        "✅ Название второй кнопки изменено.",
        reply_markup=admin_keyboard(),
    )


# =====================================================
# CHANNEL URL
# =====================================================

@dp.callback_query(F.data == "admin:edit:link")
async def edit_link(callback: CallbackQuery, state: FSMContext):

    if not await check_admin(callback):
        return

    await callback.answer()
    await state.set_state(AdminStates.channel_url)

    await callback.message.answer(
        "Отправьте новую ссылку https://t.me/..."
    )


@dp.message(AdminStates.channel_url)
async def save_link(message: Message, state: FSMContext):

    if not is_admin(message.from_user.id):
        return

    url = (message.text or "").strip()

    if not url.startswith("https://t.me/"):
        await message.answer("❌ Нужна ссылка https://t.me/...")
        return

    await set_setting("channel_url", url)

    await state.clear()

    await message.answer(
        "✅ Ссылка изменена.",
        reply_markup=admin_keyboard(),
    )


# =====================================================
# BUTTON COLORS
# =====================================================

@dp.callback_query(F.data == "admin:edit:styles")
async def edit_styles(callback: CallbackQuery):

    if not await check_admin(callback):
        return

    await callback.answer()

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Первая кнопка",
                    callback_data="admin:style:first",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Вторая кнопка",
                    callback_data="admin:style:second",
                )
            ],
        ]
    )

    await callback.message.answer(
        "Выберите кнопку:",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data.startswith("admin:style:"))
async def choose_style(callback: CallbackQuery, state: FSMContext):

    if not await check_admin(callback):
        return

    await callback.answer()

    target = callback.data.split(":")[-1]

    if target not in {"first", "second"}:
        return

    await state.set_state(AdminStates.style_target)
    await state.update_data(style_target=target)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔵 Синий",
                    callback_data="admin:color:primary",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🟢 Зелёный",
                    callback_data="admin:color:success",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔴 Красный",
                    callback_data="admin:color:danger",
                    style="danger",
                )
            ],
        ]
    )

    await callback.message.answer(
        "Выберите цвет:",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data.startswith("admin:color:"))
async def save_style(callback: CallbackQuery, state: FSMContext):

    if not await check_admin(callback):
        return

    style = callback.data.split(":")[-1]

    if style not in {"primary", "success", "danger"}:
        await callback.answer("Недопустимый цвет", show_alert=True)
        return

    data = await state.get_data()
    target = data.get("style_target")

    if target not in {"first", "second"}:
        await callback.answer("Сначала выберите кнопку", show_alert=True)
        return

    await set_setting(f"{target}_button_style", style)

    await state.clear()
    await callback.answer("Цвет изменён!")

    await callback.message.answer(
        f"✅ Цвет кнопки изменён: {style}",
        reply_markup=admin_keyboard(),
    )


# =====================================================
# PREMIUM EMOJI BUTTONS
# =====================================================

@dp.callback_query(F.data == "admin:edit:icons")
async def edit_icons(callback: CallbackQuery):

    if not await check_admin(callback):
        return

    await callback.answer()

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Первая кнопка",
                    callback_data="admin:icon:first",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Вторая кнопка",
                    callback_data="admin:icon:second",
                )
            ],
        ]
    )

    await callback.message.answer(
        "Выберите кнопку:",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data.startswith("admin:icon:"))
async def choose_icon(callback: CallbackQuery, state: FSMContext):

    if not await check_admin(callback):
        return

    await callback.answer()

    target = callback.data.split(":")[-1]

    if target not in {"first", "second"}:
        return

    await state.update_data(icon_target=target)
    await state.set_state(AdminStates.icon_target)

    await callback.message.answer(
        "Отправьте ID Premium Emoji.\n"
        "Чтобы убрать иконку, отправьте 0."
    )


@dp.message(AdminStates.icon_target)
async def save_icon(message: Message, state: FSMContext):

    if not is_admin(message.from_user.id):
        return

    icon_id = (message.text or "").strip()

    if icon_id != "0" and not icon_id.isdigit():
        await message.answer(
            "❌ ID должен содержать только цифры. Для удаления отправьте 0."
        )
        return

    data = await state.get_data()
    target = data.get("icon_target")

    if target not in {"first", "second"}:
        await state.clear()
        return

    await set_setting(
        f"{target}_button_icon",
        "" if icon_id == "0" else icon_id,
    )

    await state.clear()

    await message.answer(
        "✅ Premium Emoji обновлён.",
        reply_markup=admin_keyboard(),
    )


# =====================================================
# STATISTICS
# =====================================================

@dp.callback_query(F.data == "admin:stats")
async def statistics_handler(callback: CallbackQuery):

    if not await check_admin(callback):
        return

    await callback.answer()

    stats = await get_statistics()

    text = (
        "📊 СТАТИСТИКА БОТА\n\n"
        f"👥 Всего пользователей: {stats['total']}\n"
        f"✅ Подтвердили заявку: {stats['confirmed']}\n"
        f"📢 Сейчас состоят в канале: {stats['channel_members']}\n\n"
        "Членство отслеживается через обновления Telegram."
    )

    await callback.message.answer(text)


# =====================================================
# USERS LIST
# =====================================================

@dp.callback_query(F.data == "admin:users")
async def users_handler(callback: CallbackQuery):

    if not await check_admin(callback):
        return

    await callback.answer()

    users = await get_users()

    if not users:
        await callback.message.answer("Пользователей пока нет.")
        return

    await callback.message.answer(
        f"👥 Всего пользователей: {len(users)}"
    )

    chunk = ""

    for user in users:

        user_id = user[0]
        username = user[1]
        first_name = user[2] or "Без имени"

        username_text = (
            f"@{username}" if username else "без username"
        )

        line = (
            f"ID: {user_id}\n"
            f"Имя: {first_name}\n"
            f"Username: {username_text}\n\n"
        )

        if len(chunk) + len(line) > 3500:
            await callback.message.answer(chunk)
            chunk = ""

        chunk += line

    if chunk:
        await callback.message.answer(chunk)


# =====================================================
# BROADCAST
# =====================================================

@dp.callback_query(F.data == "admin:broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext):

    if not await check_admin(callback):
        return

    await callback.answer()

    await state.set_state(AdminStates.broadcast)

    await callback.message.answer(
        "📨 ОТПРАВЬТЕ СООБЩЕНИЕ ДЛЯ РАССЫЛКИ.\n\n"
        "Поддерживаются текст, фото, видео, документы, "
        "подписи и Premium Emoji.\n\n"
        "К сообщению будет добавлена кнопка перехода в канал.\n\n"
        "Для отмены отправьте /cancel."
    )


@dp.message(AdminStates.broadcast)
async def broadcast_send(message: Message, state: FSMContext):

    if not is_admin(message.from_user.id):
        return

    if message.text == "/cancel":
        await state.clear()
        await message.answer("Рассылка отменена.")
        return

    users = await get_user_ids()

    if not users:
        await state.clear()
        await message.answer("Нет пользователей.")
        return

    await state.clear()

    markup = await second_keyboard()

    await message.answer(
        f"🚀 Начинаю рассылку.\nПолучателей: {len(users)}"
    )

    successful = 0
    failed = 0

    for user_id in users:

        try:

            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=markup,
            )

            successful += 1

        except TelegramForbiddenError:
            failed += 1

        except TelegramAPIError as error:
            failed += 1

            logger.warning(
                "Broadcast error for %s: %s",
                user_id,
                error,
            )

        await asyncio.sleep(0.05)

    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            INSERT INTO broadcasts (
                sent_at,
                total,
                successful,
                failed
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                now,
                len(users),
                successful,
                failed,
            ),
        )

        await db.commit()

    await message.answer(
        "✅ РАССЫЛКА ЗАВЕРШЕНА\n\n"
        f"👥 Всего: {len(users)}\n"
        f"✅ Успешно: {successful}\n"
        f"❌ Ошибок: {failed}",
        reply_markup=admin_keyboard(),
    )


# =====================================================
# CANCEL
# =====================================================

@dp.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):

    if not is_admin(message.from_user.id):
        return

    await state.clear()

    await message.answer(
        "Действие отменено.",
        reply_markup=admin_keyboard(),
    )


# =====================================================
# UNKNOWN CALLBACK
# =====================================================

@dp.callback_query()
async def unknown_callback(callback: CallbackQuery):
    await callback.answer()


# =====================================================
# MAIN
# =====================================================

async def main():

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не найден в .env")

    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS не задан")

    await init_db()

    logger.info("Bot started")

    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "callback_query",
            "chat_member",
        ],
    )


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info("Bot stopped")
