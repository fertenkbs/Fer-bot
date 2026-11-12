"""
Telegram-бот на aiogram 3.x + aiosqlite
"""

import asyncio
import logging
import os
import json
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
)
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()
]

DB_PATH = "/data/bot.db"
os.makedirs("/data", exist_ok=True)

# ───────────────────────── FSM ──────────────────────────

class AdminStates(StatesGroup):
    waiting_start_text        = State()
    waiting_confirm_text      = State()
    waiting_btn1_label        = State()
    waiting_btn2_label        = State()
    waiting_channel_url       = State()
    waiting_broadcast         = State()

# ───────────────────────── DB ───────────────────────────

async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        # Проверяем существующую схему таблицы users
        async with db.execute("PRAGMA table_info(users)") as cur:
            columns = {row[1] async for row in cur}

        needs_migration = bool(columns) and "user_id" not in columns

        if needs_migration:
            logger.warning("Обнаружена старая схема таблицы users — выполняю миграцию.")
            await db.execute("DROP TABLE IF EXISTS users_backup")
            await db.execute("ALTER TABLE users RENAME TO users_backup")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                confirmed  INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Настройки
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        defaults = {
            "start_text": (
                "🤖 Подтвердите, что вы человек, нажав на кнопку ниже\n"
                "После этого ваша заявка будет автоматически принята."
            ),
            "confirm_text": (
                "✅ Ваша заявка успешно принята!\n\n"
                "В нашем канале вы сможете зарабатывать на написании "
                "простых отзывов без вложений и опыта."
            ),
            "btn1_label":       "Я человек",
            "btn2_label":       "Перейти в канал",
            "channel_url":      "https://t.me/+UedIEFA7C0liOTky",
            "btn1_emoji_id":    "",
            "btn2_emoji_id":    "",
            "start_entities":   "[]",
            "confirm_entities": "[]",
        }
        for k, v in defaults.items():
            await db.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v)
            )

        await db.commit()


async def get_setting(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else ""


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, value)
        )
        await db.commit()


async def upsert_user(user_id: int, username: Optional[str]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users(user_id, username) VALUES (?, ?)",
            (user_id, username),
        )
        # Обновляем username если изменился
        await db.execute(
            "UPDATE users SET username=? WHERE user_id=?", (username, user_id)
        )
        await db.commit()


async def mark_confirmed(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET confirmed=1 WHERE user_id=?", (user_id,)
        )
        await db.commit()


async def get_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE confirmed=1") as cur:
            confirmed = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT user_id, username FROM users ORDER BY created_at DESC LIMIT 50"
        ) as cur:
            users = await cur.fetchall()
    return {"total": total, "confirmed": confirmed, "users": users}


async def get_all_user_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            return [row[0] async for row in cur]


# ───────────────────────── Helpers ──────────────────────

def entities_to_list(entities: Optional[list[MessageEntity]]) -> list[dict]:
    if not entities:
        return []
    result = []
    for e in entities:
        d: dict = {"type": e.type.value, "offset": e.offset, "length": e.length}
        if e.url:
            d["url"] = e.url
        if e.custom_emoji_id:
            d["custom_emoji_id"] = e.custom_emoji_id
        result.append(d)
    return result


def list_to_entities(data: list[dict]) -> list[MessageEntity]:
    result = []
    for d in data:
        result.append(
            MessageEntity(
                type=d["type"],
                offset=d["offset"],
                length=d["length"],
                url=d.get("url"),
                custom_emoji_id=d.get("custom_emoji_id"),
            )
        )
    return result


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ───────────────────────── Keyboards ────────────────────

async def kb_start() -> InlineKeyboardMarkup:
    label   = await get_setting("btn1_label")
    emoji_id = await get_setting("btn1_emoji_id")
    btn = InlineKeyboardButton(
        text=label,
        callback_data="confirm",
        **({"icon_custom_emoji_id": emoji_id} if emoji_id else {}),
    )
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


async def kb_channel() -> InlineKeyboardMarkup:
    label   = await get_setting("btn2_label")
    url     = await get_setting("channel_url")
    emoji_id = await get_setting("btn2_emoji_id")
    btn = InlineKeyboardButton(
        text=label,
        url=url,
        **({"icon_custom_emoji_id": emoji_id} if emoji_id else {}),
    )
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


def kb_admin_main() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✏️ Тексты сообщений",   callback_data="adm_texts")],
        [InlineKeyboardButton(text="🔘 Кнопки и ссылка",    callback_data="adm_buttons")],
        [InlineKeyboardButton(text="📊 Статистика",          callback_data="adm_stats")],
        [InlineKeyboardButton(text="📢 Рассылка",            callback_data="adm_broadcast")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_admin_texts() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📝 Первое сообщение (/start)",  callback_data="adm_edit_start")],
        [InlineKeyboardButton(text="📝 Второе сообщение (после кнопки)", callback_data="adm_edit_confirm")],
        [InlineKeyboardButton(text="◀️ Назад",                       callback_data="adm_back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_admin_buttons() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔤 Текст кнопки «Я человек»",     callback_data="adm_btn1_label")],
        [InlineKeyboardButton(text="🎨 Emoji кнопки «Я человек»",     callback_data="adm_btn1_emoji")],
        [InlineKeyboardButton(text="🔤 Текст кнопки «Перейти»",       callback_data="adm_btn2_label")],
        [InlineKeyboardButton(text="🎨 Emoji кнопки «Перейти»",       callback_data="adm_btn2_emoji")],
        [InlineKeyboardButton(text="🔗 Ссылка на канал",              callback_data="adm_channel_url")],
        [InlineKeyboardButton(text="◀️ Назад",                         callback_data="adm_back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ───────────────────────── Router ───────────────────────

router = Router()

# /start
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await upsert_user(message.from_user.id, message.from_user.username)

    text     = await get_setting("start_text")
    ents_raw = json.loads(await get_setting("start_entities"))
    entities = list_to_entities(ents_raw) if ents_raw else None

    await message.answer(
        text,
        entities=entities,
        reply_markup=await kb_start(),
    )

    # Кнопка для входа в админку
    if is_admin(message.from_user.id):
        await message.answer(
            "👑 Вы администратор. Перейти в панель управления:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="adm_panel")
                ]]
            ),
        )


# Кнопка «Я человек»
@router.callback_query(F.data == "confirm")
async def cb_confirm(call: CallbackQuery) -> None:
    await mark_confirmed(call.from_user.id)

    text     = await get_setting("confirm_text")
    ents_raw = json.loads(await get_setting("confirm_entities"))
    entities = list_to_entities(ents_raw) if ents_raw else None

    await call.message.edit_text(
        text,
        entities=entities,
        reply_markup=await kb_channel(),
    )
    await call.answer()


# ─────────────── Админ-панель: навигация ────────────────

@router.callback_query(F.data == "adm_panel")
async def cb_adm_panel(call: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Нет доступа", show_alert=True)
        return
    await state.clear()
    await call.message.edit_text("⚙️ Админ-панель", reply_markup=kb_admin_main())
    await call.answer()


@router.callback_query(F.data == "adm_back")
async def cb_adm_back(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("⚙️ Админ-панель", reply_markup=kb_admin_main())
    await call.answer()


@router.callback_query(F.data == "adm_texts")
async def cb_adm_texts(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        return
    await call.message.edit_text("✏️ Выберите сообщение для редактирования:", reply_markup=kb_admin_texts())
    await call.answer()


@router.callback_query(F.data == "adm_buttons")
async def cb_adm_buttons(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        return
    await call.message.edit_text("🔘 Выберите параметр кнопки:", reply_markup=kb_admin_buttons())
    await call.answer()


# ─────────────── Редактирование текстов ─────────────────

@router.callback_query(F.data == "adm_edit_start")
async def cb_edit_start(call: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_start_text)
    cur = await get_setting("start_text")
    await call.message.edit_text(
        f"Текущий текст /start:\n\n{cur}\n\n"
        "Отправьте новый текст (поддерживаются Premium-эмодзи и форматирование).\n"
        "Или /cancel для отмены."
    )
    await call.answer()


@router.message(AdminStates.waiting_start_text)
async def process_start_text(message: Message, state: FSMContext) -> None:
    await set_setting("start_text", message.text or message.caption or "")
    entities = entities_to_list(message.entities or message.caption_entities)
    await set_setting("start_entities", json.dumps(entities, ensure_ascii=False))
    await state.clear()
    await message.answer("✅ Текст первого сообщения обновлён.", reply_markup=kb_admin_main())


@router.callback_query(F.data == "adm_edit_confirm")
async def cb_edit_confirm(call: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_confirm_text)
    cur = await get_setting("confirm_text")
    await call.message.edit_text(
        f"Текущий текст после кнопки:\n\n{cur}\n\n"
        "Отправьте новый текст.\nИли /cancel для отмены."
    )
    await call.answer()


@router.message(AdminStates.waiting_confirm_text)
async def process_confirm_text(message: Message, state: FSMContext) -> None:
    await set_setting("confirm_text", message.text or message.caption or "")
    entities = entities_to_list(message.entities or message.caption_entities)
    await set_setting("confirm_entities", json.dumps(entities, ensure_ascii=False))
    await state.clear()
    await message.answer("✅ Текст второго сообщения обновлён.", reply_markup=kb_admin_main())


# ─────────────── Редактирование кнопок ──────────────────

@router.callback_query(F.data == "adm_btn1_label")
async def cb_btn1_label(call: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_btn1_label)
    await call.message.edit_text("Введите новый текст кнопки «Я человек»:\nИли /cancel для отмены.")
    await call.answer()


@router.message(AdminStates.waiting_btn1_label)
async def process_btn1_label(message: Message, state: FSMContext) -> None:
    await set_setting("btn1_label", message.text or "")
    await state.clear()
    await message.answer("✅ Текст кнопки 1 обновлён.", reply_markup=kb_admin_main())


@router.callback_query(F.data == "adm_btn1_emoji")
async def cb_btn1_emoji(call: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_btn1_label)
    await call.message.answer(
        "Отправьте сообщение, содержащее Premium-эмодзи для кнопки «Я человек».\n"
        "Бот сохранит custom_emoji_id первого кастомного эмодзи.\n"
        "Или /cancel для отмены."
    )
    await call.answer()
    # Перезаписываем состояние специальным
    await state.set_state(AdminStates.waiting_btn1_label)
    await state.update_data(mode="emoji1")


@router.callback_query(F.data == "adm_btn2_label")
async def cb_btn2_label(call: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_btn2_label)
    await call.message.edit_text("Введите новый текст кнопки «Перейти в канал»:\nИли /cancel для отмены.")
    await call.answer()


@router.message(AdminStates.waiting_btn2_label)
async def process_btn2_label(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("mode") == "emoji2":
        emoji_id = _extract_first_custom_emoji(message)
        await set_setting("btn2_emoji_id", emoji_id or "")
        await state.clear()
        await message.answer(
            f"✅ Emoji кнопки 2 {'обновлён: ' + emoji_id if emoji_id else 'сброшен (не найден custom_emoji_id).'}",
            reply_markup=kb_admin_main(),
        )
        return
    await set_setting("btn2_label", message.text or "")
    await state.clear()
    await message.answer("✅ Текст кнопки 2 обновлён.", reply_markup=kb_admin_main())


@router.callback_query(F.data == "adm_btn2_emoji")
async def cb_btn2_emoji(call: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_btn2_label)
    await state.update_data(mode="emoji2")
    await call.message.edit_text(
        "Отправьте сообщение с Premium-эмодзи для кнопки «Перейти в канал».\n"
        "Или /cancel для отмены."
    )
    await call.answer()


@router.callback_query(F.data == "adm_channel_url")
async def cb_channel_url(call: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_channel_url)
    cur = await get_setting("channel_url")
    await call.message.edit_text(
        f"Текущая ссылка:\n{cur}\n\nВведите новую ссылку:\nИли /cancel для отмены."
    )
    await call.answer()


@router.message(AdminStates.waiting_channel_url)
async def process_channel_url(message: Message, state: FSMContext) -> None:
    await set_setting("channel_url", (message.text or "").strip())
    await state.clear()
    await message.answer("✅ Ссылка обновлена.", reply_markup=kb_admin_main())


def _extract_first_custom_emoji(message: Message) -> Optional[str]:
    entities = message.entities or []
    for e in entities:
        if e.type.value == "custom_emoji" and e.custom_emoji_id:
            return e.custom_emoji_id
    return None


# ─────────────── Статистика ─────────────────────────────

@router.callback_query(F.data == "adm_stats")
async def cb_stats(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        return
    stats = await get_stats()
    lines = [
        f"📊 <b>Статистика</b>",
        f"👥 Всего пользователей: <b>{stats['total']}</b>",
        f"✅ Подтвердили заявку: <b>{stats['confirmed']}</b>",
        "",
        "<b>Последние 50 пользователей:</b>",
    ]
    for uid, uname in stats["users"]:
        mention = f"@{uname}" if uname else "—"
        lines.append(f"• <code>{uid}</code> {mention}")

    text = "\n".join(lines)
    await call.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="adm_back")]]
        ),
    )
    await call.answer()


# ─────────────── Рассылка ───────────────────────────────

@router.callback_query(F.data == "adm_broadcast")
async def cb_broadcast(call: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.waiting_broadcast)
    await call.message.edit_text(
        "📢 Отправьте сообщение для рассылки.\n"
        "Поддерживается: текст, фото, видео, документы, кнопки, Premium-эмодзи.\n"
        "Или /cancel для отмены."
    )
    await call.answer()


@router.message(AdminStates.waiting_broadcast)
async def process_broadcast(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    user_ids = await get_all_user_ids()

    sent = 0
    failed = 0
    status_msg = await message.answer(f"⏳ Начинаю рассылку для {len(user_ids)} пользователей...")

    for uid in user_ids:
        try:
            await message.copy_to(uid)
            sent += 1
        except Exception as e:
            logger.warning(f"Не удалось отправить {uid}: {e}")
            failed += 1
        await asyncio.sleep(0.05)  # ~20 msg/s — в рамках лимитов

    await status_msg.edit_text(
        f"✅ Рассылка завершена.\n"
        f"Отправлено: {sent}\nОшибок: {failed}",
        reply_markup=kb_admin_main(),
    )


# ─────────────── /cancel ────────────────────────────────

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    if is_admin(message.from_user.id):
        await message.answer("❌ Действие отменено.", reply_markup=kb_admin_main())
    else:
        await message.answer("❌ Отменено.")


# ───────────────────────── Entry ────────────────────────

async def main() -> None:
    await init_db()

    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Бот запущен.")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
