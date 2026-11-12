import asyncio
import json
import logging
import os
from datetime import datetime

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    MessageEntity,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

# Строгий путь к базе данных для сохранения на томе хостинга
DB_NAME = "/data/bot.db"

logging.basicConfig(level=logging.INFO)
router = Router()

BUTTON_COLORS = {
    "none": ("Без цвета", ""),
    "green": ("🟢 Зеленый", "🟢 "),
    "red": ("🔴 Красный", "🔴 "),
    "blue": ("🔵 Синий", "🔵 "),
    "yellow": ("🟡 Желтый", "🟡 "),
    "purple": ("🟣 Фиолетовый", "🟣 "),
    "orange": ("🟠 Оранжевый", "🟠 "),
    "fire": ("🔥 Огонь", "🔥 "),
    "star": ("⭐️ Звезда", "⭐️ "),
    "check": ("✅ Галочка", "✅ "),
}

DEFAULT_SETTINGS = {
    "start_text": "🤖 Подтвердите, что вы человек, нажав на кнопку ниже\nПосле этого ваша заявка будет автоматически принята.",
    "start_entities": "[]",
    "btn1_text": "Я человек",
    "btn1_color": "none",
    "step2_text": "✅ Ваша заявка успешно принята!\n\nВ нашем канале вы сможете зарабатывать на написании простых отзывов без вложений и опыта.",
    "step2_entities": "[]",
    "btn2_text": "Перейти в канал",
    "btn2_color": "none",
    "channel_url": "https://t.me/+UedIEFA7C0liOTky",
}


class AdminStates(StatesGroup):
    waiting_for_start_text = State()
    waiting_for_step2_text = State()
    waiting_for_btn1_text = State()
    waiting_for_btn2_text = State()
    waiting_for_url = State()
    waiting_for_broadcast = State()


# ==================== БАЗА ДАННЫХ (/data/bot.db) ====================
async def init_db():
    # Создаем директорию /data, если она еще не существует
    db_dir = os.path.dirname(DB_NAME)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    async with aiosqlite.connect(DB_NAME) as db:
        # Проверяем структуру старой таблицы users в /data/bot.db
        async with db.execute("PRAGMA table_info(users)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]

        required_cols = {"user_id", "username", "full_name", "joined_at", "verified", "clicked_link"}
        if columns and not required_cols.issubset(set(columns)):
            # Если в /data/bot.db осталась несовместимая таблица от старого бота — пересоздаем её
            await db.execute("DROP TABLE users")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                joined_at TEXT,
                verified INTEGER DEFAULT 0,
                clicked_link INTEGER DEFAULT 0
            )
            """
        )

        # Проверяем структуру таблицы settings
        async with db.execute("PRAGMA table_info(settings)") as cursor:
            s_columns = [row[1] for row in await cursor.fetchall()]
        if s_columns and not {"key", "value"}.issubset(set(s_columns)):
            await db.execute("DROP TABLE settings")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        for k, v in DEFAULT_SETTINGS.items():
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
            )
        await db.commit()


async def get_setting(key: str) -> str:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else DEFAULT_SETTINGS.get(key, "")


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        await db.commit()


async def add_or_update_user(user_id: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await db.execute(
            """
            INSERT INTO users (user_id, username, full_name, joined_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name
            """,
            (user_id, username or "Нет юзернейма", full_name, now),
        )
        await db.commit()


async def mark_verified(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET verified = 1, clicked_link = 1 WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def serialize_entities(entities: list[MessageEntity] | None) -> str:
    if not entities:
        return "[]"
    return json.dumps([e.model_dump() for e in entities])


def deserialize_entities(entities_json: str) -> list[MessageEntity] | None:
    try:
        data = json.loads(entities_json)
        if not data:
            return None
        return [MessageEntity(**item) for item in data]
    except Exception:
        return None


async def build_step1_keyboard() -> InlineKeyboardMarkup:
    btn_text = await get_setting("btn1_text")
    color_key = await get_setting("btn1_color")
    prefix = BUTTON_COLORS.get(color_key, ("", ""))[1]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{prefix}{btn_text}", callback_data="verify_human"
                )
            ]
        ]
    )


async def build_step2_keyboard() -> InlineKeyboardMarkup:
    btn_text = await get_setting("btn2_text")
    color_key = await get_setting("btn2_color")
    url = await get_setting("channel_url")
    prefix = BUTTON_COLORS.get(color_key, ("", ""))[1]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{prefix}{btn_text}", url=url)]
        ]
    )


def get_admin_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⚙️ Админ-панель")]],
        resize_keyboard=True,
    )


def get_admin_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats"),
                InlineKeyboardButton(text="👥 Список юзеров", callback_data="adm_users"),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Текст /start (Премиум эмодзи)",
                    callback_data="adm_edit_start",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Текст 2 шага (Премиум эмодзи)",
                    callback_data="adm_edit_step2",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔘 Название кнопки 1", callback_data="adm_edit_btn1"
                ),
                InlineKeyboardButton(
                    text="🎨 Цвет кнопки 1", callback_data="adm_color_btn1"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔘 Название кнопки 2", callback_data="adm_edit_btn2"
                ),
                InlineKeyboardButton(
                    text="🎨 Цвет кнопки 2", callback_data="adm_color_btn2"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔗 Изменить ссылку канала", callback_data="adm_edit_url"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Сделать рассылку", callback_data="adm_broadcast"
                )
            ],
        ]
    )


def get_color_picker_kb(btn_num: int) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for key, (label, _) in BUTTON_COLORS.items():
        row.append(
            InlineKeyboardButton(
                text=label, callback_data=f"setcolor_{btn_num}_{key}"
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ==================== ПОЛЬЗОВАТЕЛЬСКИЕ ХЕНДЛЕРЫ ====================
@router.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    await add_or_update_user(user.id, user.username, user.full_name)

    if user.id in ADMIN_IDS:
        await message.answer(
            "👋 Вы вошли как администратор. Кнопка управления добавлена внизу экрана.",
            reply_markup=get_admin_reply_kb(),
        )

    text = await get_setting("start_text")
    entities_raw = await get_setting("start_entities")
    entities = deserialize_entities(entities_raw)
    kb = await build_step1_keyboard()

    await message.answer(text=text, entities=entities, reply_markup=kb)


@router.callback_query(F.data == "verify_human")
async def process_verify_human(callback: CallbackQuery):
    await mark_verified(callback.from_user.id)

    text = await get_setting("step2_text")
    entities_raw = await get_setting("step2_entities")
    entities = deserialize_entities(entities_raw)
    kb = await build_step2_keyboard()

    await callback.message.edit_text(text=text, entities=entities, reply_markup=kb)
    await callback.answer()


# ==================== АДМИН-ПАНЕЛЬ ====================
@router.message(Command("admin"))
@router.message(F.text == "⚙️ Админ-панель")
async def open_admin_panel(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await message.answer(
        "🛠 <b>Панель администратора</b>\n\n"
        "💡 <i>Подсказка: Чтобы добавить <b>Премиум эмодзи</b> или форматирование в тексты бота и рассылку, "
        "просто отправьте сообщение с нужными премиум-эмодзи при редактировании — бот автоматически сохранит их ID и разметку!</i>",
        parse_mode="HTML",
        reply_markup=get_admin_inline_kb(),
    )


@router.callback_query(F.data == "adm_back")
async def adm_back_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await callback.message.edit_text(
        "🛠 <b>Панель администратора</b>\nВыберите нужное действие:",
        parse_mode="HTML",
        reply_markup=get_admin_inline_kb(),
    )


@router.callback_query(F.data == "adm_stats")
async def adm_stats_callback(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            total_users = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE verified = 1") as c:
            verified_users = (await c.fetchone())[0]

    url = await get_setting("channel_url")
    text = (
        f"📊 <b>Статистика бота:</b>\n\n"
        f"👤 Всего зашло в бота (/start): <b>{total_users}</b>\n"
        f"✅ Нажали «Я человек» и получили ссылку: <b>{verified_users}</b>\n"
        f"🔗 Текущая ссылка: {url}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")]]
    )
    await callback.message.edit_text(
        text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True
    )


@router.callback_query(F.data == "adm_users")
async def adm_users_callback(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id, username, full_name, verified, joined_at FROM users ORDER BY joined_at DESC LIMIT 50"
        ) as c:
            rows = await c.fetchall()

    if not rows:
        await callback.answer("Пользователей пока нет.", show_alert=True)
        return

    lines = ["👥 <b>Последние 50 пользователей:</b>\n"]
    for uid, uname, fname, ver, j_at in rows:
        status = "✅" if ver else "⏳"
        uname_str = f"@{uname}" if uname != "Нет юзернейма" else "Без @username"
        lines.append(f"{status} <code>{uid}</code> | {uname_str} ({fname})")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n..."

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="adm_back")]]
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "adm_edit_start")
async def adm_edit_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_start_text)
    await callback.message.edit_text(
        "✏️ Отправьте новый текст для приветствия (/start).\n"
        "Вы можете использовать <b>Премиум эмодзи</b>, жирный шрифт, ссылки — всё сохранится точь-в-точь:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="adm_back")]]
        ),
    )


@router.message(AdminStates.waiting_for_start_text)
async def save_start_text(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await set_setting("start_text", message.text or "")
    await set_setting("start_entities", serialize_entities(message.entities))
    await state.clear()
    await message.answer(
        "✅ Приветственный текст (включая премиум-эмодзи) успешно обновлен!",
        reply_markup=get_admin_inline_kb(),
    )


@router.callback_query(F.data == "adm_edit_step2")
async def adm_edit_step2(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_step2_text)
    await callback.message.edit_text(
        "✏️ Отправьте новый текст после нажатия на кнопку «Я человек».\n"
        "Поддерживаются <b>Премиум эмодзи</b> и любое форматирование:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="adm_back")]]
        ),
    )


@router.message(AdminStates.waiting_for_step2_text)
async def save_step2_text(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await set_setting("step2_text", message.text or "")
    await set_setting("step2_entities", serialize_entities(message.entities))
    await state.clear()
    await message.answer(
        "✅ Текст второго шага успешно обновлен!",
        reply_markup=get_admin_inline_kb(),
    )


@router.callback_query(F.data == "adm_edit_btn1")
async def adm_edit_btn1(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_btn1_text)
    await callback.message.edit_text(
        "🔘 Введите новое название для первой кнопки (сейчас: «Я человек»).\n"
        "Можно добавлять любые эмодзи:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="adm_back")]]
        ),
    )


@router.message(AdminStates.waiting_for_btn1_text)
async def save_btn1_text(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await set_setting("btn1_text", message.text.strip())
    await state.clear()
    await message.answer(
        "✅ Название первой кнопки обновлено!", reply_markup=get_admin_inline_kb()
    )


@router.callback_query(F.data == "adm_edit_btn2")
async def adm_edit_btn2(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_btn2_text)
    await callback.message.edit_text(
        "🔘 Введите новое название для второй кнопки (сейчас: «Перейти в канал»):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="adm_back")]]
        ),
    )


@router.message(AdminStates.waiting_for_btn2_text)
async def save_btn2_text(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await set_setting("btn2_text", message.text.strip())
    await state.clear()
    await message.answer(
        "✅ Название второй кнопки обновлено!", reply_markup=get_admin_inline_kb()
    )


@router.callback_query(F.data == "adm_color_btn1")
async def adm_color_btn1(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "🎨 Выберите цветной индикатор для <b>Кнопки 1</b>:",
        parse_mode="HTML",
        reply_markup=get_color_picker_kb(1),
    )


@router.callback_query(F.data == "adm_color_btn2")
async def adm_color_btn2(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "🎨 Выберите цветной индикатор для <b>Кнопки 2</b>:",
        parse_mode="HTML",
        reply_markup=get_color_picker_kb(2),
    )


@router.callback_query(F.data.startswith("setcolor_"))
async def set_button_color(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    _, btn_num, color_key = callback.data.split("_")
    await set_setting(f"btn{btn_num}_color", color_key)
    await callback.answer("✅ Цвет кнопки установлен!")
    await callback.message.edit_text(
        "🛠 <b>Панель администратора</b>\nЦвет кнопки успешно изменен!",
        parse_mode="HTML",
        reply_markup=get_admin_inline_kb(),
    )


@router.callback_query(F.data == "adm_edit_url")
async def adm_edit_url(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_url)
    await callback.message.edit_text(
        "🔗 Отправьте новую ссылку на канал (должна начинаться с <code>https://t.me/</code> или <code>https://</code>):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="adm_back")]]
        ),
    )


@router.message(AdminStates.waiting_for_url)
async def save_channel_url(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    url = message.text.strip()
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://")):
        await message.answer("❌ Ошибка! Ссылка должна начинаться с https://")
        return
    await set_setting("channel_url", url)
    await state.clear()
    await message.answer(
        f"✅ Ссылка успешно изменена на: {url}",
        disable_web_page_preview=True,
        reply_markup=get_admin_inline_kb(),
    )


@router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.message.edit_text(
        "📢 <b>Рассылка по всем пользователям</b>\n\n"
        "Отправьте любое сообщение (текст, фото, видео, кружок, голосовое, с <b>Премиум эмодзи</b>) — "
        "бот скопирует его один-в-один и разошлет всем пользователям в базе:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="adm_back")]]
        ),
    )


@router.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as c:
            users = [row[0] for row in await c.fetchall()]

    sent_count = 0
    blocked_count = 0
    status_msg = await message.answer(f"⏳ Начинаю рассылку на {len(users)} пользователей...")

    for uid in users:
        try:
            await message.copy_to(chat_id=uid)
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            blocked_count += 1

    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📬 Успешно доставлено: <b>{sent_count}</b>\n"
        f"🚫 Заблокировали бота / Ошибка: <b>{blocked_count}</b>",
        parse_mode="HTML",
        reply_markup=get_admin_inline_kb(),
    )


async def main():
    if not BOT_TOKEN:
        raise ValueError("Не указан BOT_TOKEN в переменных окружения (.env)")
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    print("Бот успешно запущен с базой /data/bot.db!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
