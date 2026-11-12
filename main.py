
import asyncio
import logging
import os
from html import escape
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import aiosqlite
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
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
    raise RuntimeError("Не указан BOT_TOKEN в файле .env")

if not ADMIN_IDS:
    raise RuntimeError("Не указаны ADMIN_IDS в файле .env")


# =====================================================
# ЗАПУСК
# =====================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# =====================================================
# СОСТОЯНИЯ
# =====================================================

class WithdrawState(StatesGroup):
    amount = State()
    requisites = State()


class ChannelState(StatesGroup):
    waiting_channel = State()


# =====================================================
# БАЗА ДАННЫХ
# =====================================================

async def connect():
    return await aiosqlite.connect(DB_PATH)


async def init_db():
    async with await connect() as db:

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

        await db.commit()


async def get_setting(key):
    async with await connect() as db:
        cursor = await db.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def set_setting(key, value):
    async with await connect() as db:
        await db.execute("""
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, str(value)))
        await db.commit()


async def get_user(user_id):
    async with await connect() as db:
        cursor = await db.execute("""
            SELECT user_id, username, first_name,
                   balance, invited_count
            FROM users
            WHERE user_id = ?
        """, (user_id,))
        return await cursor.fetchone()


async def register_user(user):
    async with await connect() as db:
        await db.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
        """, (user.id, user.username, user.first_name))
        await db.commit()


# =====================================================
# КЛАВИАТУРЫ
# =====================================================

def cabinet_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔗 Создать реферальную ссылку",
                callback_data="create_link"
            )
        ],
        [
            InlineKeyboardButton(
                text="💸 Вывести деньги",
                callback_data="withdraw"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔄 Обновить кабинет",
                callback_data="cabinet"
            )
        ]
    ])


def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⬅️ В личный кабинет",
                callback_data="cabinet"
            )
        ]
    ])


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

async def show_cabinet(message_or_callback, user_id):
    user = await get_user(user_id)

    if not user:
        text = "Сначала отправь команду /start."
    else:
        text = (
            "👤 <b>Личный кабинет</b>\n\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"💰 Баланс: <b>{user[3]} ₽</b>\n"
            f"👥 Приглашено: <b>{user[4]}</b>\n\n"
            f"🎁 За каждого приглашённого — {REWARD} ₽.\n"
            f"💸 Минимальный вывод — {MIN_WITHDRAWAL} ₽.\n\n"
            "Создай свою ссылку и отправляй её друзьям!"
        )

    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.edit_text(
            text,
            reply_markup=cabinet_keyboard(),
            parse_mode="HTML"
        )
        await message_or_callback.answer()
    else:
        await message_or_callback.answer(
            text,
            reply_markup=cabinet_keyboard(),
            parse_mode="HTML"
        )


@dp.message(CommandStart())
async def start_handler(message: Message):
    await register_user(message.from_user)
    await show_cabinet(message, message.from_user.id)


@dp.callback_query(F.data == "cabinet")
async def cabinet_handler(callback: CallbackQuery):
    await show_cabinet(callback, callback.from_user.id)


# =====================================================
# ПОДКЛЮЧЕНИЕ КАНАЛА
# =====================================================

@dp.message(Command("setchannel"))
async def setchannel_command(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(ChannelState.waiting_channel)

    await message.answer(
        "Отправь ID или @username канала.\n\n"
        "Например:\n"
        "<code>@my_channel</code>\n\n"
        "Бот должен быть администратором канала "
        "с правом приглашать пользователей.",
        parse_mode="HTML"
    )


@dp.message(ChannelState.waiting_channel)
async def save_channel(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    channel = (message.text or "").strip()

    if not channel:
        await message.answer("Отправь ID или @username канала.")
        return

    try:
        chat = await bot.get_chat(channel)
        bot_member = await bot.get_chat_member(
            chat_id=chat.id,
            user_id=(await bot.get_me()).id
        )

        if bot_member.status not in ("administrator", "creator"):
            await message.answer(
                "❌ Сначала добавь бота в канал администратором."
            )
            return

        if bot_member.status == "administrator":
            permissions = getattr(
                bot_member,
                "can_invite_users",
                False
            )
            if not permissions:
                await message.answer(
                    "❌ Дай боту право приглашать пользователей "
                    "и повтори команду."
                )
                return

        await set_setting("channel_id", chat.id)

        # Ссылки старого канала больше не используются.
        async with await connect() as db:
            await db.execute("DELETE FROM invite_links")
            await db.commit()

        await state.clear()

        await message.answer(
            "✅ Канал успешно подключён!\n\n"
            f"Название: {escape(chat.title or 'Канал')}\n"
            f"ID: <code>{chat.id}</code>\n\n"
            "Теперь пользователи могут создавать "
            "персональные ссылки.",
            parse_mode="HTML"
        )

    except Exception:
        logger.exception("Ошибка подключения канала")
        await message.answer(
            "❌ Не удалось подключить канал.\n"
            "Проверь ID/username и права бота."
        )


# =====================================================
# СОЗДАНИЕ ПЕРСОНАЛЬНОЙ ССЫЛКИ НА КАНАЛ
# =====================================================

@dp.callback_query(F.data == "create_link")
async def create_link_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    channel_id = await get_setting("channel_id")

    if not channel_id:
        await callback.answer(
            "Администратор ещё не подключил канал.",
            show_alert=True
        )
        return

    # Проверяем, не создана ли ссылка ранее.
    async with await connect() as db:
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

            async with await connect() as db:
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

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📤 Поделиться ссылкой",
                url=share_url
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ В личный кабинет",
                callback_data="cabinet"
            )
        ]
    ])

    await callback.message.edit_text(
        "🔗 <b>Твоя персональная ссылка на канал:</b>\n\n"
        f"<code>{escape(invite_url)}</code>\n\n"
        f"🎁 За каждого нового участника, который вступит "
        f"по этой ссылке, тебе начислится {REWARD} ₽.\n\n"
        "Нажми «Поделиться ссылкой», чтобы отправить её "
        "в любой свой чат Telegram.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

    await callback.answer()


# =====================================================
# ОТСЛЕЖИВАНИЕ ВСТУПЛЕНИЙ В КАНАЛ
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


@dp.chat_member()
async def member_update_handler(update: ChatMemberUpdated):
    channel_id = await get_setting("channel_id")

    if not channel_id or str(update.chat.id) != str(channel_id):
        return

    old_active = member_is_active(update.old_chat_member)
    new_active = member_is_active(update.new_chat_member)

    # Интересует только новое вступление.
    if old_active or not new_active:
        return

    invite = update.invite_link

    if not invite:
        return

    invite_url = invite.invite_link
    new_user = update.new_chat_member.user

    # Ищем владельца именно этой ссылки.
    async with await connect() as db:
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

        # Нельзя приглашать самого себя.
        if referrer_id == referred_id:
            await db.commit()
            return

        # Один приглашённый — только одна награда.
        cursor = await db.execute("""
            SELECT id
            FROM referrals
            WHERE referred_id = ?
        """, (referred_id,))

        if await cursor.fetchone():
            await db.commit()
            return

        # Запоминаем приглашённого даже если он не нажимал /start.
        await db.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
        """, (
            referred_id,
            new_user.username,
            new_user.first_name
        ))

        await db.execute("""
            INSERT INTO referrals (
                referrer_id, referred_id, reward
            )
            VALUES (?, ?, ?)
        """, (referrer_id, referred_id, REWARD))

        await db.execute("""
            UPDATE users
            SET balance = balance + ?,
                invited_count = invited_count + 1
            WHERE user_id = ?
        """, (REWARD, referrer_id))

        await db.commit()

    # Уведомляем пригласившего.
    try:
        await bot.send_message(
            referrer_id,
            "🎉 <b>Новый реферал!</b>\n\n"
            f"Пользователь вступил в канал по твоей ссылке.\n"
            f"На баланс начислено <b>{REWARD} ₽</b>.",
            parse_mode="HTML"
        )
    except Exception:
        logger.exception("Не удалось уведомить пригласившего")


# =====================================================
# ЗАПРОС НА ВЫВОД
# =====================================================

@dp.callback_query(F.data == "withdraw")
async def withdraw_handler(
    callback: CallbackQuery,
    state: FSMContext
):
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


@dp.message(WithdrawState.amount)
async def withdraw_amount_handler(
    message: Message,
    state: FSMContext
):
    try:
        value = Decimal(message.text.strip().replace(",", "."))
        if value != value.to_integral_value():
            raise InvalidOperation
        amount = int(value)
    except (InvalidOperation, ValueError, AttributeError):
        await message.answer(
            "Введи целую сумму числом. Например: 50"
        )
        return

    user = await get_user(message.from_user.id)

    if not user or amount < MIN_WITHDRAWAL or amount > user[3]:
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


@dp.message(WithdrawState.requisites)
async def withdraw_requisites_handler(
    message: Message,
    state: FSMContext
):
    requisites = (message.text or "").strip()

    if len(requisites) < 3 or len(requisites) > 500:
        await message.answer("Проверь реквизиты и отправь их ещё раз.")
        return

    data = await state.get_data()
    amount = data["amount"]
    user_id = message.from_user.id

    # Сначала резервируем сумму, затем создаём заявку.
    async with await connect() as db:
        await db.execute("BEGIN IMMEDIATE")

        cursor = await db.execute("""
            UPDATE users
            SET balance = balance - ?
            WHERE user_id = ? AND balance >= ?
        """, (amount, user_id, amount))

        if cursor.rowcount != 1:
            await db.rollback()
            await state.clear()
            await message.answer(
                "Недостаточно средств. Открой личный кабинет."
            )
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
# ОБРАБОТКА ЗАЯВОК АДМИНИСТРАТОРОМ
# =====================================================

@dp.callback_query(F.data.startswith("paid:"))
async def paid_handler(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    withdrawal_id = int(callback.data.split(":")[1])

    async with await connect() as db:
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
            UPDATE withdrawals
            SET status = 'paid'
            WHERE id = ?
        """, (withdrawal_id,))

        await db.commit()

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"✅ Заявка #{withdrawal_id} отмечена как оплаченная."
    )

    try:
        await bot.send_message(
            user_id,
            f"✅ Выплата по заявке #{withdrawal_id} подтверждена.\n"
            f"Сумма: {amount} ₽."
        )
    except Exception:
        logger.exception("Не удалось уведомить пользователя")

    await callback.answer("Готово.")


@dp.callback_query(F.data.startswith("reject:"))
async def reject_handler(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    withdrawal_id = int(callback.data.split(":")[1])

    async with await connect() as db:
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
            UPDATE withdrawals
            SET status = 'rejected'
            WHERE id = ?
        """, (withdrawal_id,))

        await db.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
        """, (amount, user_id))

        await db.commit()

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"❌ Заявка #{withdrawal_id} отклонена. "
        "Средства возвращены пользователю."
    )

    try:
        await bot.send_message(
            user_id,
            f"❌ Заявка #{withdrawal_id} отклонена.\n"
            f"{amount} ₽ возвращены на баланс."
        )
    except Exception:
        logger.exception("Не удалось уведомить пользователя")

    await callback.answer("Заявка отклонена.")


# =====================================================
# АДМИН-ПАНЕЛЬ И СТАТИСТИКА
# =====================================================

@dp.message(Command("admin"))
async def admin_handler(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    channel_id = await get_setting("channel_id")

    async with await connect() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        users_count = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM referrals")
        referrals_count = (await cursor.fetchone())[0]

        cursor = await db.execute("""
            SELECT COALESCE(SUM(balance), 0)
            FROM users
        """)
        total_balance = (await cursor.fetchone())[0]

        cursor = await db.execute("""
            SELECT COUNT(*)
            FROM withdrawals
            WHERE status = 'pending'
        """)
        pending_count = (await cursor.fetchone())[0]

    await message.answer(
        "⚙️ <b>Админ-панель</b>\n\n"
        f"📢 Канал: <code>{channel_id or 'не подключён'}</code>\n"
        f"👥 Пользователей: {users_count}\n"
        f"🔗 Успешных приглашений: {referrals_count}\n"
        f"💰 Балансы пользователей: {total_balance} ₽\n"
        f"⏳ Заявок на вывод: {pending_count}\n\n"
        "Подключить или изменить канал:\n"
        "<code>/setchannel @username</code>\n\n"
        "После команды отправь ID или @username канала.",
        parse_mode="HTML"
    )


# =====================================================
# ЗАПУСК
# =====================================================

async def main():
    await init_db()

    logger.info("Бот запущен")

    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "callback_query",
            "chat_member"
        ]
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
