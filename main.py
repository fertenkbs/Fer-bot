
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
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


# =========================================================
# CONFIGURATION
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS = {
    int(item.strip())
    for item in os.getenv("ADMIN_IDS", "").split(",")
    if item.strip().isdigit()
}

CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()

CHANNEL_DEFAULT = "https://t.me/+UedIEFA7C0liOTky"

DB_PATH = "/data/bot.db"

os.makedirs("/data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


# =========================================================
# DEFAULT SETTINGS
# =========================================================

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
    "channel_url": CHANNEL_DEFAULT,
    "first_button_style": "success",
    "second_button_style": "primary",
    "first_button_icon": "",
    "second_button_icon": "",
}


# =========================================================
# DATABASE
# =========================================================

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
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, json.dumps(value, ensure_ascii=False)),
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


# =========================================================
# FSM STATES
# =========================================================

class AdminStates(StatesGroup):
    first_text = State()
    second_text = State()

    first_button_text = State()
    second_button_text = State()

    channel_url = State()

    first_style = State()
    second_style = State()

    first_icon = State()
    second_icon = State()

    broadcast = State()


# =========================================================
# BOT INITIALIZATION
# =========================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=None
    ),
)

dp = Dispatcher(storage=MemoryStorage())


def is_admin(user_id):
    return user_id in ADMIN_IDS


# =========================================================
# MESSAGE ENTITIES
# =========================================================

def restore_entities(data):
    """
    Восстанавливает Telegram MessageEntity,
    включая custom_emoji.
    """

    if not data:
        return None

    return [
        MessageEntity.model_validate(item)
        for item in data
    ]


def serialize_entities(entities):
    """
    Сохраняет entities в JSON.
    custom_emoji_id сохраняется вместе с остальными
    параметрами MessageEntity.
    """

    if not entities:
        return []

    return [
        entity.model_dump(exclude_none=True)
        for entity in entities
    ]


# =========================================================
# USER KEYBOARDS
# =========================================================

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


# =========================================================
# SEND USER MESSAGES
# =========================================================

async def send_first_message(message: Message):
    text = await get_setting("first_text")
    entities = await get_setting("first_entities")

    await message.answer(
        text,
        entities=restore_entities(entities),
        reply_markup=await first_keyboard(message.from_user.id),
    )


# =========================================================
# START COMMAND
# =========================================================

@dp.message(CommandStart())
async def start_handler(message: Message):
    await register_user(message.from_user)
    await send_first_message(message)


# =========================================================
# CONFIRMATION
# =========================================================

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


# =========================================================
# CHANNEL MEMBERSHIP TRACKING
# =========================================================

@dp.chat_member()
async def channel_member_handler(event: ChatMemberUpdated):

    if not CHANNEL_ID:
        return

    if str(event.chat.id) != CHANNEL_ID:
        return

    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status

    active_statuses = {
        "member",
        "administrator",
        "creator",
    }

    was_member = old_status in active_statuses
    is_member = new_status in active_statuses

    if was_member != is_member:
        await mark_channel_member(
            event.new_chat_member.user.id,
            is_member,
        )


# =========================================================
# ADMIN KEYBOARD
# =========================================================

def admin_keyboard():

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="📝 Изменить первое сообщение",
            callback_data="admin:edit:first_text",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="📝 Изменить второе сообщение",
            callback_data="admin:edit:second_text",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🔘 Названия кнопок",
            callback_data="admin:edit:buttons",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🔗 Ссылка на канал",
            callback_data="admin:edit:link",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🎨 Цвет кнопок",
            callback_data="admin:edit:styles",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🌟 Premium Emoji кнопок",
            callback_data="admin:edit:icons",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="📊 Статистика",
            callback_data="admin:stats",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="👥 Список пользователей",
            callback_data="admin:users",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="📨 Рассылка",
            callback_data="admin:broadcast",
        )
    )

    return builder.as_markup()


# =========================================================
# ADMIN COMMAND
# =========================================================

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

    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await callback.answer()

    await callback.message.answer(
        "⚙️ Панель администратора",
        reply_markup=admin_keyboard(),
    )


# =========================================================
# ADMIN ACCESS CHECK
# =========================================================

async def check_admin(callback):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Доступ запрещён",
            show_alert=True,
        )
        return False

    return True


# =========================================================
# EDIT TEXTS
# =========================================================

@dp.callback_query(F.data == "admin:edit:first_text")
async def edit_first_text(callback: CallbackQuery, state: FSMContext):

    if not await check_admin(callback):
        return

    await callback.answer()

    await state.set_state(AdminStates.first_text)

    await callback.message.answer(
        "Отправьте новый текст первого сообщения.\n\n"
        "Можно использовать Premium Emoji, форматирование "
        "и другие Telegram entities."
    )


@dp.callback_query(F.data == "admin:edit:second_text")
async def edit_second_text(callback: CallbackQuery, state: FSMContext):

    if not await check_admin(callback):
        return

    await callback.answer()

    await state.set_state(AdminStates.second_text)

    await callback.message.answer(
        "Отправьте новый текст второго сообщения.\n\n"
        "Premium Emoji и форматирование сохраняются."
    )


@dp.message(AdminStates.first_text)
async def save_first_text(message: Message, state: FSMContext):

    if not is_admin(message.from_user.id):
        return

    value = message.text or message.caption

    if value is None:
        await message.answer("Отправьте текстовое сообщение.")
        return

    entities = message.entities or message.caption_entities

    await set_setting("first_text", value)
    await set_setting(
        "first_entities",
        serialize_entities(entities),
    )

    await state.clear()

    await message.answer(
        "✅ Первое сообщение успешно обновлено.",
        reply_markup=admin_keyboard(),
    )


@dp.message(AdminStates.second_text)
async def save_second_text(message: Message, state: FSMContext):

    if not is_admin(message.from_user.id):
        return

    value = message.text or message.caption

    if value is None:
        await message.answer("Отправьте текстовое сообщение.")
        return

    entities = message.entities or message.caption_entities

    await set_setting("second_text", value)
    await set_setting(
        "second_entities",
        serialize_entities(entities),
    )

    await state.clear()

    await message.answer(
        "✅ Второе сообщение успешно обновлено.",
        reply_markup=admin_keyboard(),
    )


# =========================================================
# EDIT BUTTON LABELS
# =========================================================

@dp.callback_query(F.data == "admin:edit:buttons")
async def edit_buttons(callback: CallbackQuery):

    if not await check_admin(callback):
        return

    await callback.answer()

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Изменить первую кнопку",
                    callback_data="admin:edit:first_button",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Изменить вторую кнопку",
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

    await callback.message.answer(
        "Отправьте новое название первой кнопки."
    )


@dp.callback_query(F.data == "admin:edit:second_button")
async def edit_second_button(callback: CallbackQuery, state: FSMContext):

    if not await check_admin(callback):
        return

    await callback.answer()
    await state.set_state(AdminStates.second_button_text)

    await callback.message.answer(
        "Отправьте новое название второй кнопки."
    )


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


# =========================================================
# EDIT CHANNEL LINK
# =========================================================

@dp.callback_query(F.data == "admin:edit:link")
async def edit_link(callback: CallbackQuery, state: FSMContext):

    if not await check_admin(callback):
        return

    await callback.answer()

    await state.set_state(AdminStates.channel_url)

    await callback.message.answer(
        "Отправьте новую ссылку на канал.\n"
        "Поддерживаются ссылки https://t.me/..."
    )


@dp.message(AdminStates.channel_url)
async def save_link(message: Message, state: FSMContext):

    if not is_admin(message.from_user.id):
        return

    url = (message.text or "").strip()

    if not (
        url.startswith("https://t.me/")
        or url.startswith("http://t.me/")
    ):
        await message.answer(
            "❌ Неверная ссылка. Используйте https://t.me/..."
        )
        return

    await set_setting("channel_url", url)

    await state.clear()

    await message.answer(
        "✅ Ссылка на канал обновлена.",
        reply_markup=admin_keyboard(),
    )


# =========================================================
# BUTTON COLORS
# =========================================================

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
        "Для какой кнопки изменить цвет?",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data.startswith("admin:style:"))
async def choose_style(callback: CallbackQuery, state: FSMContext):

    if not await check_admin(callback):
        return

    await callback.answer()

    target = callback.data.split(":")[-1]

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


# =========================================================
# PREMIUM EMOJI BUTTONS
# =========================================================

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
        "Выберите кнопку для настройки Premium Emoji.",
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
    await state.set_state(
        AdminStates.first_icon if target == "first"
        else AdminStates.second_icon
    )

    await callback.message.answer(
        "Отправьте ID Premium Emoji.\n\n"
        "Например, отправьте строку с ID custom emoji, "
        "который вы получили из Telegram.\n\n"
        "Чтобы убрать иконку, отправьте: 0"
    )


@dp.message(AdminStates.first_icon)
@dp.message(AdminStates.second_icon)
async def save_icon(message: Message, state: FSMContext):

    if not is_admin(message.from_user.id):
        return

    icon_id = (message.text or "").strip()

    if icon_id != "0" and not icon_id.isdigit():
        await message.answer(
            "❌ ID должен содержать только цифры. "
            "Для удаления отправьте 0."
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
        "✅ Premium Emoji кнопки обновлён.",
        reply_markup=admin_keyboard(),
    )


# =========================================================
# STATISTICS
# =========================================================

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
        "Участники канала считаются по обновлениям "
        "статуса участников Telegram."
    )

    await callback.message.answer(text)


# =========================================================
# USERS LIST
# =========================================================

@dp.callback_query(F.data == "admin:users")
async def users_handler(callback: CallbackQuery):

    if not await check_admin(callback):
        return

    await callback.answer()

    users = await get_users()

    if not users:
        await callback.message.answer(
            "Пользователей пока нет."
        )
        return

    await callback.message.answer(
        f"👥 Всего пользователей: {len(users)}"
    )

    lines = []

    for user in users:

        user_id = user[0]
        username = user[1]
        first_name = user[2] or "Без имени"

        username_text = (
            f"@{username}" if username else "без username"
        )

        lines.append(
            f"ID: {user_id}\n"
            f"Имя: {first_name}\n"
            f"Username: {username_text}\n"
        )

    chunk = ""

    for line in lines:

        if len(chunk) + len(line) > 3500:
            await callback.message.answer(chunk)
            chunk = ""

        chunk += line + "\n"

    if chunk:
        await callback.message.answer(chunk)


# =========================================================
# BROADCAST
# =========================================================

@dp.callback_query(F.data == "admin:broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext):

    if not await check_admin(callback):
        return

    await callback.answer()

    await state.set_state(AdminStates.broadcast)

    await callback.message.answer(
        "📨 ОТПРАВКА РАССЫЛКИ\n\n"
        "Отправьте сообщение, которое нужно разослать.\n\n"
        "Поддерживаются:\n"
        "• Обычный текст\n"
        "• Фото\n"
        "• Видео\n"
        "• Документы\n"
        "• Аудио\n"
        "• Подписи к медиа\n"
        "• Telegram Premium Emoji\n\n"
        "К сообщению автоматически добавится кнопка "
        "перехода в канал.\n\n"
        "Для отмены используйте /cancel."
    )


@dp.message(AdminStates.broadcast)
async def broadcast_send(message: Message, state: FSMContext):

    if not is_admin(message.from_user.id):
        return

    if message.text and message.text.startswith("/cancel"):
        await state.clear()
        await message.answer("Рассылка отменена.")
        return

    users = await get_user_ids()

    if not users:
        await state.clear()
        await message.answer("Нет пользователей для рассылки.")
        return

    await state.clear()

    channel_text = await get_setting("second_button_text")
    channel_url = await get_setting("channel_url")
    channel_style = await get_setting("second_button_style")
    channel_icon = await get_setting("second_button_icon")

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=channel_text,
                    url=channel_url,
                    style=channel_style,
                    icon_custom_emoji_id=channel_icon or None,
                )
            ]
        ]
    )

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
        f"👥 Всего получателей: {len(users)}\n"
        f"✅ Успешно: {successful}\n"
        f"❌ Ошибок: {failed}",
        reply_markup=admin_keyboard(),
    )


# =========================================================
# CANCEL FSM
# =========================================================

@dp.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):

    if not is_admin(message.from_user.id):
        return

    await state.clear()

    await message.answer(
        "Действие отменено.",
        reply_markup=admin_keyboard(),
    )


# =========================================================
# UNKNOWN CALLBACKS
# =========================================================

@dp.callback_query()
async def unknown_callback(callback: CallbackQuery):
    await callback.answer()


# =========================================================
# MAIN
# =========================================================

async def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не найден в переменных окружения!"
        )

    if not ADMIN_IDS:
        logger.warning(
            "ADMIN_IDS не задан. Админ-панель недоступна."
        )

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
