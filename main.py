import asyncio
import logging
import os
import re
from decimal import Decimal, InvalidOperation
from html import escape
from urllib.parse import quote

import aiosqlite
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)


# =====================================================
# НАСТРОЙКИ
# =====================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "/data/bot.db")

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip()
}

REWARD = 5
MIN_WITHDRAWAL = 30

if not BOT_TOKEN:
    raise RuntimeError("Не указан BOT_TOKEN в .env")

if not ADMIN_IDS:
    raise RuntimeError("Не указаны ADMIN_IDS в .env")

os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)


# =====================================================
# ЛОГИ И ЗАПУСК
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Админские обработчики подключаются ПЕРВЫМИ, поэтому /admin
# и кнопки админки работают в любом состоянии.
admin_router = Router()
user_router = Router()

admin_router.message.filter(F.from_user.id.in_(ADMIN_IDS))
admin_router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))


# =====================================================
# СОСТОЯНИЯ
# =====================================================

class WithdrawState(StatesGroup):
    amount = State()
    requisites = State()


class ChannelState(StatesGroup):
    waiting_channel = State()


class EmojiState(StatesGroup):
    waiting_regular = State()
    waiting_premium = State()


# =====================================================
# SQLITE
# =====================================================

def connect():
    return aiosqlite.connect(DB_PATH)


async def init_db():
    async with connect() as db:

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER NOT NULL DEFAULT 0,
                invited_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS invite_links (
                user_id INTEGER PRIMARY KEY,
                channel_id TEXT NOT NULL,
                invite_link TEXT NOT NULL UNIQUE
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_id INTEGER NOT NULL UNIQUE,
                reward INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                requisites TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor = await db.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in await cursor.fetchall()]

        migrations = {
            "username": "TEXT",
            "first_name": "TEXT",
            "balance": "INTEGER NOT NULL DEFAULT 0",
            "invited_count": "INTEGER NOT NULL DEFAULT 0",
            "created_at": "TIMESTAMP",
        }

        created_at_added = False

        for column, definition in migrations.items():
            if column not in columns:
                await db.execute(
                    f"ALTER TABLE users ADD COLUMN {column} {definition}"
                )
                if column == "created_at":
                    created_at_added = True

        if created_at_added:
            await db.execute("""
                UPDATE users
                SET created_at = CURRENT_TIMESTAMP
                WHERE created_at IS NULL
            """)

        await db.commit()

    logger.info("База данных готова: %s", DB_PATH)


async def get_setting(key):
    async with connect() as db:
        cursor = await db.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def set_setting(key, value):
    async with connect() as db:
        await db.execute("""
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, str(value)))

        await db.commit()


async def del_setting(key):
    async with connect() as db:
        await db.execute("DELETE FROM settings WHERE key = ?", (key,))
        await db.commit()


async def get_user(user_id):
    async with connect() as db:
        cursor = await db.execute("""
            SELECT user_id, username, first_name,
                   balance, invited_count
            FROM users
            WHERE user_id = ?
        """, (user_id,))

        return await cursor.fetchone()


async def register_user(user):
    async with connect() as db:
        await db.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
        """, (
            user.id,
            user.username,
            user.first_name
        ))

        await db.commit()


# =====================================================
# ЭМОДЗИ: ОБЫЧНЫЕ И ПРЕМИУМ (настраиваются из админки)
# =====================================================

# ключ: (название для админки, эмодзи по умолчанию)
EMOJI_SLOTS = {
    "cabinet": ("Заголовок кабинета", "👤"),
    "id": ("Строка «ID»", "🆔"),
    "balance": ("Строка «Баланс»", "💰"),
    "invited": ("Строка «Приглашено»", "👥"),
    "reward": ("Строка «Награда»", "🎁"),
    "minwd": ("Строка «Минимальный вывод»", "💸"),
    "btn_link": ("Ссылка (кнопка и текст)", "🔗"),
    "btn_withdraw": ("Кнопка «Вывести деньги»", "💸"),
    "btn_refresh": ("Кнопка «Обновить кабинет»", "🔄"),
    "btn_share": ("Кнопка «Поделиться»", "📤"),
    "btn_back": ("Кнопка «В личный кабинет»", "⬅️"),
    "btn_admin": ("Кнопка «Админ-панель»", "⚙️"),
    "party": ("Уведомление о новом реферале", "🎉"),
    "ok": ("Успех (выплата подтверждена)", "✅"),
    "no": ("Отказ (заявка отклонена)", "❌"),
}

# Кэш, чтобы не ходить в базу на каждую кнопку.
_cfg = {"premium": True, "custom": {}, "regular": {}}


async def load_emoji_cache():
    async with connect() as db:
        cursor = await db.execute("""
            SELECT key, value FROM settings
            WHERE key LIKE 'emoji%' OR key = 'premium_enabled'
        """)
        rows = await cursor.fetchall()

    premium = True
    custom, regular = {}, {}

    for key, value in rows:
        if key == "premium_enabled":
            premium = value != "0"
        elif key.startswith("emoji_custom:"):
            custom[key.split(":", 1)[1]] = value
        elif key.startswith("emoji_regular:"):
            regular[key.split(":", 1)[1]] = value

    _cfg["premium"] = premium
    _cfg["custom"] = custom
    _cfg["regular"] = regular


def regular_emoji(key):
    return _cfg["regular"].get(key) or EMOJI_SLOTS[key][1]


def emo(key, plain=False):
    """Эмодзи для текста (HTML)."""
    base = regular_emoji(key)
    cid = _cfg["custom"].get(key)

    if cid and _cfg["premium"] and not plain:
        return f'<tg-emoji emoji-id="{cid}">{base}</tg-emoji>'

    return base


def btn(key, text, plain=False, **kwargs):
    """Кнопка: с премиум-иконкой или с обычным эмодзи в тексте."""
    cid = _cfg["custom"].get(key)

    if cid and _cfg["premium"] and not plain:
        return InlineKeyboardButton(
            text=text,
            icon_custom_emoji_id=cid,
            **kwargs
        )

    return InlineKeyboardButton(
        text=f"{regular_emoji(key)} {text}",
        **kwargs
    )


def has_premium_emoji():
    return bool(_cfg["premium"] and _cfg["custom"])


async def with_fallback(make):
    """
    make(plain) -> awaitable.
    Сначала пробуем с премиум-эмодзи. Если Telegram их не принял —
    повторяем с обычными, чтобы бот не ломался.
    """
    try:
        return await make(False)

    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return None

        if has_premium_emoji():
            logger.warning(
                "Премиум-эмодзи не приняты Telegram (%s). "
                "Отправляю с обычными.", e
            )
            return await make(True)

        raise


# =====================================================
# КЛАВИАТУРЫ
# =====================================================

def cabinet_keyboard(user_id, plain=False):
    rows = [
        [btn("btn_link", "Создать реферальную ссылку", plain,
             callback_data="create_link")],
        [btn("btn_withdraw", "Вывести деньги", plain,
             callback_data="withdraw")],
        [btn("btn_refresh", "Обновить кабинет", plain,
             callback_data="cabinet")],
    ]

    if user_id in ADMIN_IDS:
        rows.append([
            btn("btn_admin", "Админ-панель", plain,
                callback_data="adm:home")
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_withdraw_keyboard(withdrawal_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Подтвердить выплату",
                callback_data=f"paid:{withdrawal_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Отклонить заявку",
                callback_data=f"reject:{withdrawal_id}"
            )
        ]
    ])


# =====================================================
# ЛИЧНЫЙ КАБИНЕТ
# =====================================================

async def show_cabinet(target, user_id):
    user = await get_user(user_id)

    async def make(plain):
        if not user:
            text = "Сначала отправь команду /start."
        else:
            text = (
                f"{emo('cabinet', plain)} <b>Личный кабинет</b>\n\n"
                f"{emo('id', plain)} ID: <code>{user_id}</code>\n"
                f"{emo('balance', plain)} Баланс: <b>{user[3]} ₽</b>\n"
                f"{emo('invited', plain)} Приглашено: <b>{user[4]}</b>\n\n"
                f"{emo('reward', plain)} За каждого нового участника — {REWARD} ₽.\n"
                f"{emo('minwd', plain)} Минимальный вывод — {MIN_WITHDRAWAL} ₽.\n\n"
                "Создай свою ссылку и отправляй её друзьям!"
            )

        kb = cabinet_keyboard(user_id, plain)

        if isinstance(target, CallbackQuery):
            return await target.message.edit_text(
                text, reply_markup=kb, parse_mode="HTML"
            )

        return await target.answer(
            text, reply_markup=kb, parse_mode="HTML"
        )

    await with_fallback(make)

    if isinstance(target, CallbackQuery):
        await target.answer()


# =====================================================
# ПОДКЛЮЧЕНИЕ КАНАЛА (общая логика)
# =====================================================

async def resolve_chat(raw):
    """Принимает ID (с -100 или без), @username или ссылку t.me/name."""
    raw = (raw or "").strip()

    if raw.startswith("https://t.me/") or raw.startswith("http://t.me/"):
        raw = "@" + raw.rstrip("/").split("/")[-1]

    if re.fullmatch(r"-?\d+", raw):
        cid = int(raw)
        candidates = [cid]

        # Админы часто присылают ID без -100.
        if cid > 0:
            candidates.append(int(f"-100{cid}"))
        elif not raw.startswith("-100"):
            candidates.append(int(f"-100{abs(cid)}"))

        last_error = None
        for candidate in candidates:
            try:
                return await bot.get_chat(candidate)
            except TelegramAPIError as e:
                last_error = e

        raise last_error

    if not raw.startswith("@"):
        raw = "@" + raw

    return await bot.get_chat(raw)


async def try_connect_channel(raw):
    """Возвращает (успех, html-текст)."""
    try:
        chat = await resolve_chat(raw)
        me = await bot.get_me()
        member = await bot.get_chat_member(chat.id, me.id)

    except TelegramAPIError as e:
        logger.warning("Не удалось получить канал: %s", e)
        return False, (
            "❌ <b>Бот не видит этот канал.</b>\n\n"
            f"Ответ Telegram: <code>{escape(str(e.message))}</code>\n\n"
            "Проверь:\n"
            "1. Бот добавлен в канал <b>администратором</b>.\n"
            "2. ID указан верно (для каналов он начинается с "
            "<code>-100</code>) или это публичный @username."
        )

    except Exception:
        logger.exception("Ошибка подключения канала")
        return False, "❌ Не удалось подключить канал. Проверь ID."

    if member.status not in ("administrator", "creator"):
        return False, (
            "❌ Бот есть в канале, но не администратор.\n"
            "Сделай его администратором."
        )

    if (
        member.status == "administrator"
        and not getattr(member, "can_invite_users", False)
    ):
        return False, (
            "❌ У бота нет права <b>«Приглашать пользователей»</b>.\n"
            "Включи его в правах администратора."
        )

    await set_setting("channel_id", chat.id)

    async with connect() as db:
        await db.execute("DELETE FROM invite_links")
        await db.commit()

    return True, (
        "✅ <b>Канал подключён!</b>\n\n"
        f"Название: {escape(chat.title or 'Канал')}\n"
        f"ID: <code>{chat.id}</code>\n\n"
        "Теперь пользователи могут создавать персональные ссылки."
    )


async def channel_info_text():
    channel_id = await get_setting("channel_id")

    if not channel_id:
        return (
            "📢 <b>Канал</b>\n\n"
            "Канал не подключён.\n\n"
            "Нажми «Указать ID канала» и отправь ID или @username."
        )

    try:
        chat = await bot.get_chat(int(channel_id))
        me = await bot.get_me()
        member = await bot.get_chat_member(chat.id, me.id)

        is_admin = member.status in ("administrator", "creator")
        can_invite = (
            member.status == "creator"
            or getattr(member, "can_invite_users", False)
        )

        username = f"@{chat.username}" if chat.username else "—"

        return (
            "📢 <b>Канал</b>\n\n"
            f"Название: {escape(chat.title or 'Канал')}\n"
            f"Username: {escape(username)}\n"
            f"ID: <code>{chat.id}</code>\n\n"
            f"Бот — админ: {'✅ да' if is_admin else '❌ нет'}\n"
            f"Право приглашать: {'✅ да' if can_invite else '❌ нет'}"
        )

    except Exception as e:
        return (
            "📢 <b>Канал</b>\n\n"
            f"ID в настройках: <code>{escape(str(channel_id))}</code>\n\n"
            "❌ <b>Бот сейчас не видит этот канал.</b>\n"
            f"<code>{escape(str(e))}</code>\n\n"
            "Добавь бота админом с правом приглашать "
            "пользователей или укажи другой ID."
        )


# =====================================================
# АДМИН-ПАНЕЛЬ
# =====================================================

async def admin_panel_content():
    channel_id = await get_setting("channel_id")

    async with connect() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        users_count = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM referrals")
        referrals_count = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COALESCE(SUM(balance), 0) FROM users"
        )
        total_balance = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'"
        )
        pending_count = (await cursor.fetchone())[0]

    premium_state = "включены" if _cfg["premium"] else "выключены"

    text = (
        "⚙️ <b>Админ-панель</b>\n\n"
        f"📢 Канал: <code>{escape(str(channel_id or 'не подключён'))}</code>\n"
        f"👥 Пользователей: {users_count}\n"
        f"🔗 Успешных приглашений: {referrals_count}\n"
        f"💰 Балансы пользователей: {total_balance} ₽\n"
        f"⏳ Заявок на вывод: {pending_count}\n"
        f"⭐ Премиум-эмодзи: {premium_state}"
    )

    premium_btn = (
        "⭐ Премиум-эмодзи: ВКЛ (выключить)"
        if _cfg["premium"]
        else "⭐ Премиум-эмодзи: ВЫКЛ (включить)"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📢 Канал / ID канала", callback_data="adm:channel")],
        [InlineKeyboardButton(
            text="😀 Эмодзи кнопок и текстов", callback_data="adm:emoji")],
        [InlineKeyboardButton(
            text=premium_btn, callback_data="adm:premium")],
        [InlineKeyboardButton(
            text="🔄 Обновить", callback_data="adm:home")],
        [InlineKeyboardButton(
            text="⬅️ В личный кабинет", callback_data="cabinet")],
    ])

    return text, kb


async def admin_show(target, text, kb):
    """Показать экран админки (редактирует или шлёт новое сообщение)."""
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(
                text, reply_markup=kb, parse_mode="HTML"
            )
        except TelegramBadRequest as e:
            if "not modified" not in str(e).lower():
                raise
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


@admin_router.message(Command("admin"))
async def admin_handler(message: Message, state: FSMContext):
    await state.clear()
    text, kb = await admin_panel_content()
    await admin_show(message, text, kb)


@admin_router.callback_query(F.data == "adm:home")
async def admin_home_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = await admin_panel_content()
    await admin_show(callback, text, kb)


@admin_router.callback_query(F.data == "adm:premium")
async def admin_premium_toggle(callback: CallbackQuery):
    new_value = "0" if _cfg["premium"] else "1"
    await set_setting("premium_enabled", new_value)
    await load_emoji_cache()

    text, kb = await admin_panel_content()
    await admin_show(callback, text, kb)


# ---------- Канал ----------

def channel_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✏️ Указать ID канала", callback_data="adm:setchannel")],
        [InlineKeyboardButton(
            text="🔍 Проверить снова", callback_data="adm:channel")],
        [InlineKeyboardButton(
            text="🗑 Отключить канал", callback_data="adm:delchannel")],
        [InlineKeyboardButton(
            text="⬅️ Назад", callback_data="adm:home")],
    ])


@admin_router.callback_query(F.data == "adm:channel")
async def admin_channel_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Проверяю…")
    text = await channel_info_text()
    await admin_show(callback, text, channel_menu_kb())


@admin_router.callback_query(F.data == "adm:setchannel")
async def admin_setchannel_cb(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ChannelState.waiting_channel)
    await callback.message.answer(
        "Отправь ID или @username канала.\n\n"
        "Например: <code>-1001234567890</code> или "
        "<code>@my_channel</code>\n\n"
        "Бот должен быть администратором канала "
        "с правом приглашать пользователей.\n\n"
        "Отмена — /admin",
        parse_mode="HTML"
    )
    await callback.answer()


@admin_router.callback_query(F.data == "adm:delchannel")
async def admin_delchannel_cb(callback: CallbackQuery):
    await del_setting("channel_id")

    async with connect() as db:
        await db.execute("DELETE FROM invite_links")
        await db.commit()

    await callback.answer("Канал отключён.")
    text = await channel_info_text()
    await admin_show(callback, text, channel_menu_kb())


@admin_router.message(Command("setchannel"))
async def setchannel_command(message: Message, state: FSMContext):
    await state.set_state(ChannelState.waiting_channel)
    await message.answer(
        "Отправь ID или @username канала.\n\n"
        "Например: <code>-1001234567890</code> или "
        "<code>@my_channel</code>",
        parse_mode="HTML"
    )


@admin_router.message(
    ChannelState.waiting_channel,
    F.text,
    ~F.text.startswith("/")
)
async def save_channel(message: Message, state: FSMContext):
    ok, text = await try_connect_channel(message.text)

    if ok:
        await state.clear()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⚙️ В админ-панель", callback_data="adm:home")]
    ])

    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@admin_router.callback_query(F.data.startswith("usechan:"))
async def use_channel_cb(callback: CallbackQuery):
    chat_id = callback.data.split(":", 1)[1]
    ok, text = await try_connect_channel(chat_id)

    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer("Готово." if ok else "Не вышло.")


# ---------- Эмодзи ----------

def emoji_list_kb():
    rows = []

    for key, (label, _default) in EMOJI_SLOTS.items():
        mark = ""
        if key in _cfg["custom"]:
            mark += " ⭐"
        if key in _cfg["regular"]:
            mark += " ✏️"

        rows.append([InlineKeyboardButton(
            text=f"{regular_emoji(key)} {label}{mark}",
            callback_data=f"adm:e:{key}"
        )])

    rows.append([InlineKeyboardButton(
        text="⬅️ Назад", callback_data="adm:home")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def emoji_slot_content(key):
    label, default = EMOJI_SLOTS[key]
    regular = _cfg["regular"].get(key)
    custom = _cfg["custom"].get(key)

    regular_line = (
        f"{regular} (изменён)" if regular else f"{default} (по умолчанию)"
    )
    custom_line = f"<code>{custom}</code> ✅" if custom else "не задан"

    text = (
        f"😀 <b>{escape(label)}</b>\n\n"
        f"Обычный эмодзи: {regular_line}\n"
        f"Премиум-эмодзи (ID): {custom_line}\n\n"
        "Обычный эмодзи показывается, когда премиум не задан, "
        "выключен или не принят Telegram."
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✏️ Заменить обычный эмодзи",
            callback_data=f"adm:er:{key}")],
        [InlineKeyboardButton(
            text="⭐ Поставить премиум-эмодзи",
            callback_data=f"adm:ep:{key}")],
        [InlineKeyboardButton(
            text="♻️ Сбросить на стандартный",
            callback_data=f"adm:ed:{key}")],
        [InlineKeyboardButton(
            text="⬅️ К списку", callback_data="adm:emoji")],
    ])

    return text, kb


@admin_router.callback_query(F.data == "adm:emoji")
async def admin_emoji_list(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    text = (
        "😀 <b>Эмодзи кнопок и текстов</b>\n\n"
        "Выбери, что менять.\n"
        "⭐ — стоит премиум-эмодзи, ✏️ — заменён обычный."
    )
    await admin_show(callback, text, emoji_list_kb())


@admin_router.callback_query(F.data.regexp(r"^adm:e:\w+$"))
async def admin_emoji_slot(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    key = callback.data.split(":")[2]

    if key not in EMOJI_SLOTS:
        await callback.answer("Неизвестный пункт.", show_alert=True)
        return

    text, kb = emoji_slot_content(key)
    await admin_show(callback, text, kb)


@admin_router.callback_query(F.data.regexp(r"^adm:er:\w+$"))
async def admin_emoji_ask_regular(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split(":")[2]

    if key not in EMOJI_SLOTS:
        await callback.answer("Неизвестный пункт.", show_alert=True)
        return

    await state.set_state(EmojiState.waiting_regular)
    await state.update_data(key=key)

    await callback.message.answer(
        "Отправь один обычный эмодзи, например 🔥\n\nОтмена — /admin"
    )
    await callback.answer()


@admin_router.callback_query(F.data.regexp(r"^adm:ep:\w+$"))
async def admin_emoji_ask_premium(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split(":")[2]

    if key not in EMOJI_SLOTS:
        await callback.answer("Неизвестный пункт.", show_alert=True)
        return

    await state.set_state(EmojiState.waiting_premium)
    await state.update_data(key=key)

    await callback.message.answer(
        "Отправь сообщение с <b>одним премиум-эмодзи</b> "
        "(кастомным, анимированным) или числовой ID эмодзи.\n\n"
        "Отмена — /admin",
        parse_mode="HTML"
    )
    await callback.answer()


@admin_router.callback_query(F.data.regexp(r"^adm:ed:\w+$"))
async def admin_emoji_reset(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split(":")[2]

    if key not in EMOJI_SLOTS:
        await callback.answer("Неизвестный пункт.", show_alert=True)
        return

    await del_setting(f"emoji_custom:{key}")
    await del_setting(f"emoji_regular:{key}")
    await load_emoji_cache()

    await callback.answer("Сброшено.")
    text, kb = emoji_slot_content(key)
    await admin_show(callback, text, kb)


def slot_back_kb(key):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⬅️ К этому пункту", callback_data=f"adm:e:{key}")],
        [InlineKeyboardButton(
            text="📋 К списку эмодзи", callback_data="adm:emoji")],
    ])


def extract_custom_emoji(message: Message):
    """Достаёт (custom_emoji_id, символ) из сообщения."""
    for entity in (message.entities or []):
        if entity.type == "custom_emoji":
            char = entity.extract_from(message.text or "")
            return entity.custom_emoji_id, char

    sticker = message.sticker
    if sticker and getattr(sticker, "custom_emoji_id", None):
        return sticker.custom_emoji_id, sticker.emoji or ""

    return None, ""


@admin_router.message(EmojiState.waiting_premium, ~F.text.startswith("/"))
async def save_premium_emoji(message: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("key")

    if key not in EMOJI_SLOTS:
        await state.clear()
        return

    emoji_id, _char = extract_custom_emoji(message)

    if not emoji_id:
        raw = (message.text or "").strip()
        if re.fullmatch(r"\d{8,25}", raw):
            emoji_id = raw

    if not emoji_id:
        await message.answer(
            "Не вижу премиум-эмодзи. Отправь именно кастомный эмодзи "
            "из премиум-набора или его числовой ID."
        )
        return

    await set_setting(f"emoji_custom:{key}", emoji_id)
    await load_emoji_cache()
    await state.clear()

    await message.answer(
        f"✅ Премиум-эмодзи сохранён.\nID: <code>{emoji_id}</code>",
        parse_mode="HTML",
        reply_markup=slot_back_kb(key)
    )


@admin_router.message(EmojiState.waiting_regular, ~F.text.startswith("/"))
async def save_regular_emoji(message: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("key")

    if key not in EMOJI_SLOTS:
        await state.clear()
        return

    _emoji_id, char = extract_custom_emoji(message)
    value = (char or message.text or "").strip()

    if (
        not value
        or len(value) > 8
        or (value.isascii() and value.isalnum())
    ):
        await message.answer("Отправь один обычный эмодзи, например 🔥")
        return

    await set_setting(f"emoji_regular:{key}", value)
    await load_emoji_cache()
    await state.clear()

    await message.answer(
        f"✅ Обычный эмодзи заменён на {escape(value)}",
        parse_mode="HTML",
        reply_markup=slot_back_kb(key)
    )


# =====================================================
# БОТ ДОБАВЛЕН В КАНАЛ — сообщаем админам
# =====================================================

@user_router.my_chat_member()
async def bot_membership_handler(update: ChatMemberUpdated):
    chat = update.chat

    if chat.type not in ("channel", "supergroup", "group"):
        return

    new = update.new_chat_member

    if new.status == "administrator":
        can_invite = getattr(new, "can_invite_users", False)

        text = (
            "📢 <b>Бот добавлен администратором</b>\n\n"
            f"Название: {escape(chat.title or 'Без названия')}\n"
            f"ID: <code>{chat.id}</code>\n"
            f"Право приглашать: {'✅ да' if can_invite else '❌ нет'}\n\n"
        )

        if can_invite:
            text += "Подключить этот канал для рефералов?"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="✅ Подключить этот канал",
                    callback_data=f"usechan:{chat.id}")]
            ])
        else:
            text += "Включи боту право «Приглашать пользователей»."
            kb = None

    elif new.status in ("left", "kicked"):
        text = (
            "⚠️ Бот удалён из чата "
            f"{escape(chat.title or '')} (<code>{chat.id}</code>)."
        )
        kb = None

    else:
        return

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id, text, parse_mode="HTML", reply_markup=kb
            )
        except Exception:
            logger.exception("Не удалось уведомить админа о канале")


# =====================================================
# СТАРТ И КАБИНЕТ
# =====================================================

@user_router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    await register_user(message.from_user)
    await show_cabinet(message, message.from_user.id)


@user_router.callback_query(F.data == "cabinet")
async def cabinet_handler(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_cabinet(callback, callback.from_user.id)


# =====================================================
# СОЗДАНИЕ ПЕРСОНАЛЬНОЙ ССЫЛКИ
# =====================================================

@user_router.callback_query(F.data == "create_link")
async def create_link_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    channel_id = await get_setting("channel_id")

    if not channel_id:
        await callback.answer(
            "Администратор ещё не подключил канал.",
            show_alert=True
        )
        return

    async with connect() as db:
        cursor = await db.execute("""
            SELECT invite_link, channel_id
            FROM invite_links
            WHERE user_id = ?
        """, (user_id,))

        row = await cursor.fetchone()

    if row and str(row[1]) == str(channel_id):
        invite_url = row[0]

    else:
        try:
            invite = await bot.create_chat_invite_link(
                chat_id=int(channel_id),
                name=f"ref_{user_id}",
                creates_join_request=False
            )

            invite_url = invite.invite_link

            async with connect() as db:
                await db.execute("""
                    INSERT INTO invite_links (
                        user_id, channel_id, invite_link
                    )
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        channel_id = excluded.channel_id,
                        invite_link = excluded.invite_link
                """, (user_id, channel_id, invite_url))

                await db.commit()

        except Exception:
            logger.exception("Ошибка создания ссылки")

            await callback.answer(
                "Не получилось создать ссылку. "
                "Проверь права бота в канале.",
                show_alert=True
            )
            return

    share_url = (
        "https://t.me/share/url?"
        f"url={quote(invite_url, safe='')}"
        f"&text={quote('Присоединяйся к каналу по моей ссылке!')}"
    )

    async def make(plain):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [btn("btn_share", "Поделиться ссылкой", plain, url=share_url)],
            [btn("btn_back", "В личный кабинет", plain,
                 callback_data="cabinet")],
        ])

        return await callback.message.edit_text(
            f"{emo('btn_link', plain)} <b>Твоя персональная ссылка "
            "на канал:</b>\n\n"
            f"<code>{escape(invite_url)}</code>\n\n"
            f"{emo('reward', plain)} За каждого нового участника, "
            f"который вступит по этой ссылке, тебе начислится "
            f"{REWARD} ₽.\n\n"
            "Нажми «Поделиться ссылкой», чтобы отправить её "
            "в любой свой чат Telegram.",
            reply_markup=kb,
            parse_mode="HTML"
        )

    await with_fallback(make)
    await callback.answer()


# =====================================================
# ОТСЛЕЖИВАНИЕ ВСТУПЛЕНИЙ
# =====================================================

def member_is_active(member):
    status = member.status

    return (
        status in ("creator", "administrator", "member")
        or (
            status == "restricted"
            and getattr(member, "is_member", False)
        )
    )


@user_router.chat_member()
async def member_update_handler(update: ChatMemberUpdated):
    channel_id = await get_setting("channel_id")

    if not channel_id:
        return

    if str(update.chat.id) != str(channel_id):
        return

    old_active = member_is_active(update.old_chat_member)
    new_active = member_is_active(update.new_chat_member)

    if old_active or not new_active:
        return

    invite = update.invite_link

    if not invite:
        return

    invite_url = invite.invite_link
    new_user = update.new_chat_member.user

    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")

        cursor = await db.execute("""
            SELECT user_id
            FROM invite_links
            WHERE invite_link = ? AND channel_id = ?
        """, (invite_url, channel_id))

        row = await cursor.fetchone()

        if not row:
            await db.commit()
            return

        referrer_id = row[0]
        referred_id = new_user.id

        if referrer_id == referred_id:
            await db.commit()
            return

        cursor = await db.execute("""
            SELECT id FROM referrals WHERE referred_id = ?
        """, (referred_id,))

        if await cursor.fetchone():
            await db.commit()
            return

        await db.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
        """, (referred_id, new_user.username, new_user.first_name))

        await db.execute("""
            INSERT INTO referrals (referrer_id, referred_id, reward)
            VALUES (?, ?, ?)
        """, (referrer_id, referred_id, REWARD))

        await db.execute("""
            UPDATE users
            SET balance = balance + ?,
                invited_count = invited_count + 1
            WHERE user_id = ?
        """, (REWARD, referrer_id))

        await db.commit()

    try:
        await with_fallback(lambda plain: bot.send_message(
            referrer_id,
            f"{emo('party', plain)} <b>Новый реферал!</b>\n\n"
            "Пользователь вступил в канал по твоей ссылке.\n"
            f"На баланс начислено <b>{REWARD} ₽</b>.",
            parse_mode="HTML"
        ))

    except Exception:
        logger.exception("Не удалось уведомить пригласившего")


# =====================================================
# ВЫВОД ДЕНЕГ
# =====================================================

@user_router.callback_query(F.data == "withdraw")
async def withdraw_handler(callback: CallbackQuery, state: FSMContext):
    user = await get_user(callback.from_user.id)

    if not user:
        await callback.answer("Сначала отправь /start.", show_alert=True)
        return

    balance = user[3]

    if balance < MIN_WITHDRAWAL:
        await callback.answer(
            f"Минимальный вывод — {MIN_WITHDRAWAL} ₽.\n"
            f"Твой баланс — {balance} ₽.",
            show_alert=True
        )
        return

    await state.set_state(WithdrawState.amount)

    await callback.message.answer(
        f"💸 Доступно: {balance} ₽.\n\n"
        f"Введи сумму вывода от {MIN_WITHDRAWAL} до {balance} ₽."
    )

    await callback.answer()


@user_router.message(WithdrawState.amount)
async def withdraw_amount_handler(message: Message, state: FSMContext):
    try:
        value = Decimal(message.text.strip().replace(",", "."))

        if value != value.to_integral_value():
            raise InvalidOperation

        amount = int(value)

    except (InvalidOperation, ValueError, AttributeError):
        await message.answer("Введи целую сумму числом. Например: 50")
        return

    user = await get_user(message.from_user.id)

    if (
        not user
        or amount < MIN_WITHDRAWAL
        or amount > user[3]
    ):
        await message.answer(
            "❌ Неверная сумма или недостаточно средств. "
            "Введи сумму ещё раз."
        )
        return

    await state.update_data(amount=amount)
    await state.set_state(WithdrawState.requisites)

    await message.answer(
        "📋 Отправь реквизиты для получения выплаты.\n\n"
        "Не отправляй пароли, коды подтверждения "
        "или данные для входа в банковские приложения."
    )


@user_router.message(WithdrawState.requisites)
async def withdraw_requisites_handler(message: Message, state: FSMContext):
    requisites = (message.text or "").strip()

    if len(requisites) < 3 or len(requisites) > 500:
        await message.answer("Проверь реквизиты и отправь их ещё раз.")
        return

    data = await state.get_data()

    amount = data["amount"]
    user_id = message.from_user.id

    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")

        cursor = await db.execute("""
            UPDATE users
            SET balance = balance - ?
            WHERE user_id = ? AND balance >= ?
        """, (amount, user_id, amount))

        if cursor.rowcount != 1:
            await db.rollback()
            await state.clear()

            await message.answer("Недостаточно средств. Открой личный кабинет.")
            return

        cursor = await db.execute("""
            INSERT INTO withdrawals (user_id, amount, requisites)
            VALUES (?, ?, ?)
        """, (user_id, amount, requisites))

        withdrawal_id = cursor.lastrowid

        await db.commit()

    await state.clear()

    await message.answer(
        "✅ Заявка отправлена администраторам!\n\n"
        f"Номер: #{withdrawal_id}\n"
        f"Сумма: {amount} ₽\n\n"
        "Ожидай решения администратора."
    )

    username = (
        "@" + message.from_user.username
        if message.from_user.username
        else "не указан"
    )

    admin_text = (
        "💸 <b>Новая заявка на вывод</b>\n\n"
        f"Заявка: #{withdrawal_id}\n"
        f"Пользователь: {escape(username)}\n"
        f"ID: <code>{user_id}</code>\n"
        f"Сумма: <b>{amount} ₽</b>\n"
        f"Реквизиты: <code>{escape(requisites)}</code>"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                admin_text,
                parse_mode="HTML",
                reply_markup=admin_withdraw_keyboard(withdrawal_id)
            )

        except Exception:
            logger.exception("Не удалось отправить заявку администратору")


# =====================================================
# ПОДТВЕРЖДЕНИЕ / ОТКЛОНЕНИЕ ВЫПЛАТЫ
# =====================================================

@user_router.callback_query(F.data.startswith("paid:"))
async def paid_handler(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    withdrawal_id = int(callback.data.split(":")[1])

    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")

        cursor = await db.execute("""
            SELECT user_id, amount
            FROM withdrawals
            WHERE id = ? AND status = 'pending'
        """, (withdrawal_id,))

        row = await cursor.fetchone()

        if not row:
            await db.rollback()
            await callback.answer(
                "Заявка уже обработана или не найдена.",
                show_alert=True
            )
            return

        user_id, amount = row

        await db.execute("""
            UPDATE withdrawals SET status = 'paid' WHERE id = ?
        """, (withdrawal_id,))

        await db.commit()

    await callback.message.edit_reply_markup(reply_markup=None)

    await callback.message.answer(
        f"✅ Заявка #{withdrawal_id} отмечена как оплаченная."
    )

    try:
        await with_fallback(lambda plain: bot.send_message(
            user_id,
            f"{emo('ok', plain)} Выплата по заявке #{withdrawal_id} "
            f"подтверждена.\nСумма: {amount} ₽.",
            parse_mode="HTML"
        ))

    except Exception:
        logger.exception("Не удалось уведомить пользователя")

    await callback.answer("Готово.")


@user_router.callback_query(F.data.startswith("reject:"))
async def reject_handler(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    withdrawal_id = int(callback.data.split(":")[1])

    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")

        cursor = await db.execute("""
            SELECT user_id, amount
            FROM withdrawals
            WHERE id = ? AND status = 'pending'
        """, (withdrawal_id,))

        row = await cursor.fetchone()

        if not row:
            await db.rollback()
            await callback.answer(
                "Заявка уже обработана или не найдена.",
                show_alert=True
            )
            return

        user_id, amount = row

        await db.execute("""
            UPDATE withdrawals SET status = 'rejected' WHERE id = ?
        """, (withdrawal_id,))

        await db.execute("""
            UPDATE users SET balance = balance + ? WHERE user_id = ?
        """, (amount, user_id))

        await db.commit()

    await callback.message.edit_reply_markup(reply_markup=None)

    await callback.message.answer(
        f"❌ Заявка #{withdrawal_id} отклонена. "
        "Средства возвращены пользователю."
    )

    try:
        await with_fallback(lambda plain: bot.send_message(
            user_id,
            f"{emo('no', plain)} Заявка #{withdrawal_id} отклонена.\n"
            f"{amount} ₽ возвращены на баланс.",
            parse_mode="HTML"
        ))

    except Exception:
        logger.exception("Не удалось уведомить пользователя")

    await callback.answer("Заявка отклонена.")


# =====================================================
# ЗАПУСК
# =====================================================

dp.include_router(admin_router)  # сначала админка
dp.include_router(user_router)


async def main():
    await init_db()
    await load_emoji_cache()

    logger.info("Бот запускается")

    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "callback_query",
            "chat_member",
            "my_chat_member",
        ]
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
