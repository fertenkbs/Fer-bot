import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import quote

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton as IKB,
    InlineKeyboardMarkup as IKM,
    Message,
    MessageEntity,
)
from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ═════════════ ENV / КОНФИГ ═════════════
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_ID", "").replace(" ", "").split(",") if x}
DB_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///devbank.db")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "deverskyi").lstrip("@")   # кому возвращают Айсы
GAME_BOT = os.getenv("GAME_BOT", "EyesSeeGamesBot").lstrip("@")         # игровой бот

G = 1_000_000_000                                          # 1ккк
CREDIT_MIN = int(os.getenv("CREDIT_MIN", 1))               # минимума нет
CREDIT_MAX = int(os.getenv("CREDIT_MAX", 2 * 1000 ** 5))   # макс. кредит 2ккккк
RECEIPT_FROM = int(os.getenv("RECEIPT_FROM", 1000 ** 5))   # от 1ккккк — объяснение и расписка
CREDIT_HOURS = 24                                          # срок возврата
HEART_EFFECT_ID = "5159385139981059251"                    # эффект сообщения ❤️ (вместо времени)

# ═════════════ БАЗА ДАННЫХ ═════════════
engine = create_async_engine(DB_URL)
Session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str] = mapped_column(String(256), default="")
    joined_at: Mapped[datetime] = mapped_column(DateTime)


class Credit(Base):
    __tablename__ = "credits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[int] = mapped_column(BigInteger)   # сколько взял
    repay: Mapped[int] = mapped_column(BigInteger)    # сколько вернуть
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/active/closed/rejected
    checking: Mapped[int] = mapped_column(Integer, default=0)  # 1 — сообщил о возврате, ждёт проверки
    created_at: Mapped[datetime] = mapped_column(DateTime)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_remind: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)   # как заёмщик собирается вернуть
    receipt: Mapped[int] = mapped_column(Integer, default=0)        # 1 — расписка подписана


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="{}")


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ═════════════ ТЕКСТЫ И КНОПКИ (по умолчанию) ═════════════
TEXT_DEFS = {
    # key: (название для админки, текст, доступные переменные)
    "greeting": ("Приветствие /start",
                 "Здравствуйте! Вас приветствует внутриигровой всемирный банк Дев-Банк. "
                 "Здесь вы можете взять кредит в Айсах.", ""),
    "bad_amount": ("Неверная сумма", "Неверная сумма. Введите число, например: 5ккк", ""),
    "credit": ("Экран «Взять кредит»",
               "💳 Кредиты Дев-Банка\n\nМаксимальная сумма кредита: {cmax}. "
               "Каждая заявка рассматривается владельцем.\n"
               "Для кредита от {rfrom} нужно объяснить, как вы его вернёте, и подписать расписку.\n"
               "Вернуть нужно в течение 24 часов, до возврата бот будет напоминать вам каждый час.\n\n"
               "Примеры возврата:\n5ккк → 7ккк\n10ккк → 15ккк\n100ккк → 150ккк\n200ккк → 250ккк\n\n"
               "Введите сумму кредита (например: 5ккк):", "{cmax} {rfrom}"),
    "credit_range": ("Кредит: сумма вне диапазона",
                     "Максимальная сумма кредита — {cmax}.", "{cmax}"),
    "credit_plan": ("Кредит: просьба объяснить возврат",
                    "Кредит {amount} Айсов (вернуть {repay}) — крупная сумма. "
                    "Опишите одним сообщением, как именно вы будете его возвращать:", "{amount} {repay}"),
    "credit_plan_short": ("Кредит: объяснение слишком короткое",
                          "Опишите подробнее (минимум 10 символов): как вы будете возвращать кредит?", ""),
    "credit_receipt": ("Кредит: расписка",
                       "📜 РАСПИСКА\n\nЯ, {name}, беру в кредит у Дев-Банка {amount} Айсов и обязуюсь "
                       "вернуть {repay} Айсов в течение 24 часов с момента получения.\n\n"
                       "Как я буду возвращать: {plan}\n\n"
                       "Я согласен со всеми условиями банка и понимаю, что обязан вернуть долг в срок.\n\n"
                       "Нажмите «Согласен», чтобы подписать расписку и отправить заявку.",
                       "{name} {amount} {repay} {plan}"),
    "debt_closed": ("Долг закрыт владельцем",
                    "✅ Владелец закрыл ваш долг. Кредит погашен, больше ничего возвращать не нужно.", ""),
    "credit_busy": ("Кредит: уже есть заявка/долг",
                    "У вас уже есть заявка или непогашенный кредит. "
                    "Нажмите «Вернуть кредит», чтобы посмотреть детали.", ""),
    "credit_nouser": ("Кредит: нет username",
                      "Чтобы взять кредит, установите username в настройках Telegram и нажмите /start.", ""),
    "credit_wait": ("Кредит: заявка отправлена",
                    "Заявка на кредит {amount} Айсов отправлена владельцу. "
                    "К возврату: {repay} Айсов. Ожидайте решение.", "{amount} {repay}"),
    "credit_ok": ("Кредит одобрен",
                  "✅ Кредит одобрен! {amount} Айсов отправлены вам.\n\n"
                  "Вернуть нужно {repay} Айсов в течение 24 часов. Бот будет напоминать каждый час.",
                  "{amount} {repay}"),
    "credit_no": ("Кредит отклонён", "❌ В кредите отказано.", ""),
    "credit_remind": ("Напоминание о долге",
                      "⏰ Напоминание! Верните кредит: {repay} Айсов (взяли {amount}).\n{left}",
                      "{repay} {amount} {left}"),
    "credit_overdue": ("Напоминание: срок истёк",
                       "⚠️ Срок возврата кредита истёк! Верните {repay} Айсов (взяли {amount}).\n{left}",
                       "{repay} {amount} {left}"),
    "credit_none": ("Нет кредита", "У вас нет непогашенных кредитов.", ""),
    "repay_info": ("Экран «Вернуть кредит»",
                   "💳 Ваш долг: {repay} Айсов (взяли {amount}).\n{left}\n\n"
                   "Нажмите «Вернуть всю сумму» — откроется @EyesSeeGamesBot с готовой командой. "
                   "После перевода нажмите «Я вернул» и перешлите сообщение о переводе.",
                   "{repay} {amount} {left}"),
    "repay_proof": ("Возврат: просьба переслать",
                    "Перешлите сюда сообщение об успешном переводе.", ""),
    "repay_wait": ("Возврат: ожидает проверки",
                   "Спасибо! Владелец проверит возврат в течение 24 часов.", ""),
    "repay_ok": ("Возврат подтверждён", "✅ Кредит погашен. Спасибо!", ""),
    "repay_no": ("Возврат не подтверждён",
                 "❌ Возврат не подтверждён. Проверьте перевод и попробуйте ещё раз.", ""),
}
TEXT_DEFAULTS = {k: {"text": v[1], "entities": []} for k, v in TEXT_DEFS.items()}

BTN_DEFS = {
    "credit": ("Взять кредит", "Взять кредит"),
    "repay": ("Вернуть кредит", "Вернуть кредит"),
    "repay_all": ("Вернуть всю сумму (ссылка в игровой бот)", "Вернуть всю сумму"),
    "repaid": ("Я вернул", "Я вернул"),
    "agree": ("Согласен с распиской", "✅ Согласен с распиской"),
    "cancel": ("Отмена", "Отмена"),
}
BTN_DEFAULTS = {k: {"text": v[1], "color": "", "emoji": ""} for k, v in BTN_DEFS.items()}
COLORS = {"green": ("🟢 Зелёная", "success"), "red": ("🔴 Красная", "danger"),
          "blue": ("🔵 Синяя", "primary"), "none": ("⚪ Без цвета", "")}


# ═════════════ УТИЛИТЫ ═════════════
def parse_amount(s: str) -> int | None:
    s = s.lower().replace(" ", "").replace("_", "").replace(",", ".")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(к*)", s)
    if not m:
        return None
    val = int(Decimal(m.group(1)) * (1000 ** len(m.group(2))))
    return val if val > 0 else None


def short(n: int) -> str:
    for k in (4, 3, 2, 1):
        if n >= 1000 ** k:
            v = f"{n / 1000 ** k:.2f}".rstrip("0").rstrip(".")
            return v + "к" * k
    return ""


def fmt(n: int) -> str:
    s = f"{n:,}".replace(",", " ")
    sh = short(n)
    return f"{s} ({sh})" if sh else s


def fmt_td(td: timedelta) -> str:
    secs = max(int(td.total_seconds()), 0)
    d, h, m = secs // 86400, secs % 86400 // 3600, secs % 3600 // 60
    return f"{d} д. {h} ч." if d else f"{h} ч. {m} мин."


def left_text(due: datetime) -> str:
    left = due - now()
    if left.total_seconds() > 0:
        return f"Осталось: {fmt_td(left)}"
    return f"Просрочено на: {fmt_td(-left)}"


def repay_amount(a: int) -> int:
    """Сумма возврата: 5ккк→7, 10→15, 100→150, 200→250; дальше +25%."""
    if a < 5 * G:
        interest = a * 40 // 100                           # 40%
    elif a < 10 * G:
        interest = a * (150 * G + 10 * a) // (500 * G)   # от 40% (5ккк) до 50% (10ккк)
    elif a <= 100 * G:
        interest = a // 2                                  # 50%
    elif a <= 200 * G:
        interest = 50 * G                                  # фикс. 50ккк
    else:
        interest = a // 4                                  # 25%
    return a + interest


def u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def render(block: dict, **kw):
    """Подставляет переменные, сохраняя позиции премиум-эмодзи."""
    text = block["text"]
    ents = [dict(e) for e in block.get("entities", [])]
    pos = 0
    pat = re.compile(r"\{(\w+)\}")
    while True:
        m = pat.search(text, pos)
        if not m:
            break
        key = m.group(1)
        if key not in kw:
            pos = m.end()
            continue
        val = str(kw[key])
        start, old, new = u16(text[:m.start()]), u16(m.group(0)), u16(val)
        delta = new - old
        for e in ents:
            if e["offset"] >= start + old:
                e["offset"] += delta
            elif e["offset"] <= start and e["offset"] + e["length"] >= start + old:
                e["length"] += delta
        text = text[:m.start()] + val + text[m.end():]
        pos = m.start() + len(val)
    return text, ([MessageEntity(**e) for e in ents] or None)


def pack(m: Message) -> dict:
    return {"text": m.text or "",
            "entities": [e.model_dump(exclude_none=True) for e in (m.entities or [])]}


def uname(u) -> str:
    return f"@{u.username}" if u.username else "без username"


def name_of(u: User) -> str:
    return f"@{u.username}" if u.username else (u.full_name or str(u.id))


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def game_link(cmd: str) -> str:
    """Ссылка на игровой бот с заранее вписанным текстом команды."""
    return f"https://t.me/{GAME_BOT}?text={quote(cmd)}"


def repay_cmd(repay: int) -> str:
    return f"п {repay} @{OWNER_USERNAME}"


# ═════════════ НАСТРОЙКИ В БД ═════════════
async def cfg(name: str, defaults: dict) -> dict:
    async with Session() as s:
        row = await s.get(Setting, name)
    stored = json.loads(row.value) if row else {}
    return {k: {**v, **stored.get(k, {})} for k, v in defaults.items()}


async def cfg_set(name: str, key: str, data: dict | None):
    async with Session() as s:
        row = await s.get(Setting, name)
        stored = json.loads(row.value) if row else {}
        if data is None:
            stored.pop(key, None)
        else:
            stored[key] = {**stored.get(key, {}), **data}
        if row:
            row.value = json.dumps(stored, ensure_ascii=False)
        else:
            s.add(Setting(key=name, value=json.dumps(stored, ensure_ascii=False)))
        await s.commit()


async def btn(key: str, cb: str | None = None, url: str | None = None) -> IKB:
    b = (await cfg("buttons", BTN_DEFAULTS))[key]
    extra = {}
    if b["color"]:
        extra["style"] = b["color"]
    if b["emoji"]:
        extra["icon_custom_emoji_id"] = b["emoji"]
    if url:
        return IKB(text=b["text"], url=url, **extra)
    return IKB(text=b["text"], callback_data=cb, **extra)


async def main_kb() -> IKM:
    return IKM(inline_keyboard=[[await btn("credit", "credit")], [await btn("repay", "repay")]])


async def cancel_kb() -> IKM:
    return IKM(inline_keyboard=[[await btn("cancel", "home")]])


async def repay_kb(repay: int) -> IKM:
    return IKM(inline_keyboard=[
        [await btn("repay_all", url=game_link(repay_cmd(repay)))],
        [await btn("repaid", "repaid")],
        [await btn("cancel", "home")],
    ])


# ═════════════ ОТПРАВКА (с сердечком вместо времени) ═════════════
async def send_msg(bot: Bot, chat_id: int, text: str, **kw):
    try:
        return await bot.send_message(chat_id, text, message_effect_id=HEART_EFFECT_ID, **kw)
    except TelegramBadRequest:
        return await bot.send_message(chat_id, text, **kw)


async def send_text(bot: Bot, chat_id: int, key: str, kb=None, **kw):
    texts = await cfg("texts", TEXT_DEFAULTS)
    text, ents = render(texts[key], **kw)
    return await send_msg(bot, chat_id, text, entities=ents, reply_markup=kb)


async def send_cmd(bot: Bot, chat_id: int, cmd: str, kb=None):
    """Команда для игрового бота: нажатие по ней копирует текст."""
    prefix = "📋 Команда (нажмите, чтобы скопировать):\n"
    ents = [MessageEntity(type="code", offset=u16(prefix), length=u16(cmd))]
    return await send_msg(bot, chat_id, prefix + cmd, entities=ents, reply_markup=kb)


async def show(call: CallbackQuery, key: str, kb=None, **kw):
    """Заменяет экран: удаляет старое сообщение и шлёт новое (с сердечком)."""
    texts = await cfg("texts", TEXT_DEFAULTS)
    text, ents = render(texts[key], **kw)
    try:
        await call.message.delete()
    except Exception:
        pass
    await send_msg(call.bot, call.message.chat.id, text, entities=ents, reply_markup=kb)


async def edit_plain(call: CallbackQuery, text: str, kb=None):
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=kb)


async def notify_admins(bot: Bot, text: str, kb=None, forward: Message | None = None):
    for a in ADMIN_IDS:
        try:
            await bot.send_message(a, text, reply_markup=kb)
            if forward:
                try:
                    await forward.forward(a)
                except Exception:
                    await forward.copy_to(a)
        except Exception as e:
            logging.warning("admin notify failed: %s", e)


async def upsert_user(u):
    async with Session() as s:
        row = await s.get(User, u.id)
        if row:
            row.username, row.full_name = u.username, u.full_name
        else:
            s.add(User(id=u.id, username=u.username, full_name=u.full_name, joined_at=now()))
        await s.commit()


async def open_credit(s, uid: int, statuses=("pending", "active")):
    res = await s.execute(select(Credit).where(Credit.user_id == uid, Credit.status.in_(statuses))
                          .order_by(Credit.id.desc()).limit(1))
    return res.scalars().first()


# ═════════════ FSM ═════════════
class Cr(StatesGroup):
    amount = State()
    plan = State()
    agree = State()


class Rep(StatesGroup):
    proof = State()


class Adm(StatesGroup):
    text = State()
    btn_text = State()
    btn_emoji = State()
    find = State()
    amount = State()
    msg = State()


router = Router()


# ═════════════ КОМАНДЫ ═════════════
@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await upsert_user(m.from_user)
    await send_text(bot, m.chat.id, "greeting", await main_kb())


@router.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отменено.")


@router.message(Command("admin"))
async def cmd_admin(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.clear()
    await m.answer(await admin_stats(), reply_markup=admin_kb())


@router.callback_query(F.data == "home")
async def cb_home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await show(call, "greeting", await main_kb())


# ═════════════ КРЕДИТЫ: ПОЛЬЗОВАТЕЛЬ ═════════════
@router.callback_query(F.data == "credit")
async def cb_credit(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    if not call.from_user.username:
        return await show(call, "credit_nouser", await cancel_kb())
    async with Session() as s:
        busy = await open_credit(s, call.from_user.id)
    if busy:
        return await show(call, "credit_busy", await cancel_kb())
    await upsert_user(call.from_user)
    await state.set_state(Cr.amount)
    await show(call, "credit", await cancel_kb(), cmax=fmt(CREDIT_MAX), rfrom=fmt(RECEIPT_FROM))


async def create_credit(bot: Bot, user, chat_id: int, amount: int, plan: str | None = None, receipt: int = 0):
    uid = user.id
    await upsert_user(user)
    async with Session() as s:
        if await open_credit(s, uid):
            return await send_text(bot, chat_id, "credit_busy", await main_kb())
        repay = repay_amount(amount)
        c = Credit(user_id=uid, amount=amount, repay=repay, status="pending", created_at=now(),
                   plan=plan, receipt=receipt)
        s.add(c)
        await s.commit()
        await s.refresh(c)
        cid = c.id
    kb = IKM(inline_keyboard=[[
        IKB(text="✅ Принять", callback_data=f"ca:{cid}", style="success"),
        IKB(text="❌ Отклонить", callback_data=f"cx:{cid}", style="danger"),
    ]])
    head = (f"💳 Заявка на кредит #{cid}\n👤 {uname(user)} | {user.full_name}\n"
            f"🆔 {uid}\nСумма: {fmt(amount)} Айсов\nВернуть: {fmt(repay)} Айсов (в течение 24 ч.)")
    if receipt:
        head += "\n\n📜 Расписка подписана: заёмщик согласен со всеми условиями."
    if plan:
        head += f"\n📝 Как вернёт: {plan[:1500]}"
    await notify_admins(bot, head, kb)
    await send_text(bot, chat_id, "credit_wait", await main_kb(), amount=fmt(amount), repay=fmt(repay))


@router.message(Cr.amount)
async def cr_amount(m: Message, state: FSMContext, bot: Bot):
    amount = parse_amount(m.text) if m.text else None
    if amount is None:
        return await send_text(bot, m.chat.id, "bad_amount", await cancel_kb())
    if amount < CREDIT_MIN or amount > CREDIT_MAX:
        return await send_text(bot, m.chat.id, "credit_range", await cancel_kb(), cmax=fmt(CREDIT_MAX))
    if amount >= RECEIPT_FROM:
        await state.update_data(amount=amount)
        await state.set_state(Cr.plan)
        return await send_text(bot, m.chat.id, "credit_plan", await cancel_kb(),
                               amount=fmt(amount), repay=fmt(repay_amount(amount)))
    await state.clear()
    await create_credit(bot, m.from_user, m.chat.id, amount)


@router.message(Cr.plan)
async def cr_plan(m: Message, state: FSMContext, bot: Bot):
    plan = (m.text or "").strip()
    if len(plan) < 10:
        return await send_text(bot, m.chat.id, "credit_plan_short", await cancel_kb())
    plan = plan[:1000]
    amount = (await state.get_data())["amount"]
    await state.update_data(plan=plan)
    await state.set_state(Cr.agree)
    kb = IKM(inline_keyboard=[[await btn("agree", "cr_agree")], [await btn("cancel", "home")]])
    await send_text(bot, m.chat.id, "credit_receipt", kb, name=uname(m.from_user), amount=fmt(amount),
                    repay=fmt(repay_amount(amount)), plan=plan)


@router.callback_query(Cr.agree, F.data == "cr_agree")
async def cr_agree(call: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    amount, plan = data.get("amount"), data.get("plan")
    await state.clear()
    await call.answer("Расписка подписана")
    try:
        await call.message.delete()
    except Exception:
        pass
    if amount:
        await create_credit(bot, call.from_user, call.message.chat.id, amount, plan, 1)


async def send_money_prompt(bot: Bot, admin_id: int, username: str | None, uid: int, amount: int):
    """Админу: кнопка, открывающая игровой бот с готовым переводом заёмщику."""
    if username:
        cmd = f"п {amount} @{username}"
        kb = IKM(inline_keyboard=[[IKB(text="🚀 Перейти в бота", url=game_link(cmd), style="success")]])
        await bot.send_message(admin_id, f"💳 Отправьте кредит {fmt(amount)} Айсов пользователю @{username}:",
                               reply_markup=kb)
        await send_cmd(bot, admin_id, cmd)
    else:
        await bot.send_message(admin_id, f"⚠️ У пользователя {uid} нет username — "
                                         f"переведите {fmt(amount)} Айсов вручную.")


@router.callback_query(F.data.startswith("ca:"))
async def credit_accept(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    cid = int(call.data.split(":")[1])
    async with Session() as s:
        c = await s.get(Credit, cid)
        if not c or c.status != "pending":
            return await call.answer("Уже обработано", show_alert=True)
        u = await s.get(User, c.user_id)
        c.status = "active"
        c.due_at = now() + timedelta(hours=CREDIT_HOURS)
        c.next_remind = now() + timedelta(hours=1)
        uid, amount, repay = c.user_id, c.amount, c.repay
        username = u.username if u else None
        await s.commit()
    await call.answer("Принято")
    await edit_plain(call, (call.message.text or "") + "\n\n✅ Принято")
    await send_text(bot, uid, "credit_ok", await main_kb(), amount=fmt(amount), repay=fmt(repay))
    await send_money_prompt(bot, call.from_user.id, username, uid, amount)


@router.callback_query(F.data.startswith("cx:"))
async def credit_reject(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    cid = int(call.data.split(":")[1])
    async with Session() as s:
        c = await s.get(Credit, cid)
        if not c or c.status != "pending":
            return await call.answer("Уже обработано", show_alert=True)
        c.status = "rejected"
        uid = c.user_id
        await s.commit()
    await call.answer("Отклонено")
    await edit_plain(call, (call.message.text or "") + "\n\n❌ Отклонено")
    await send_text(bot, uid, "credit_no", await main_kb())


@router.callback_query(F.data == "repay")
async def cb_repay(call: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    await call.answer()
    async with Session() as s:
        c = await open_credit(s, call.from_user.id, ("active",))
    if not c:
        return await show(call, "credit_none", await cancel_kb())
    await show(call, "repay_info", await repay_kb(c.repay),
               repay=fmt(c.repay), amount=fmt(c.amount), left=left_text(c.due_at))
    await send_cmd(bot, call.message.chat.id, repay_cmd(c.repay))


@router.callback_query(F.data == "repaid")
async def cb_repaid(call: CallbackQuery, state: FSMContext):
    await call.answer()
    async with Session() as s:
        c = await open_credit(s, call.from_user.id, ("active",))
    if not c:
        return await show(call, "credit_none", await cancel_kb())
    await state.set_state(Rep.proof)
    await show(call, "repay_proof", await cancel_kb())


@router.message(Rep.proof)
async def rep_proof(m: Message, state: FSMContext, bot: Bot):
    async with Session() as s:
        c = await open_credit(s, m.from_user.id, ("active",))
        if not c:
            await state.clear()
            return await send_text(bot, m.chat.id, "credit_none", await main_kb())
        c.checking = 1
        cid, repay = c.id, c.repay
        await s.commit()
    kb = IKM(inline_keyboard=[[
        IKB(text="✅ Подтвердить возврат", callback_data=f"ra:{cid}", style="success"),
        IKB(text="❌ Не подтверждать", callback_data=f"rx:{cid}", style="danger"),
    ]])
    head = (f"💳 Возврат кредита #{cid}\n👤 {uname(m.from_user)} | {m.from_user.full_name}\n"
            f"🆔 {m.from_user.id}\nДолжен вернуть: {fmt(repay)} Айсов")
    await notify_admins(bot, head, kb, forward=m)
    await state.clear()
    await send_text(bot, m.chat.id, "repay_wait", await main_kb())


@router.callback_query(F.data.startswith("ra:"))
async def repay_accept(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    cid = int(call.data.split(":")[1])
    async with Session() as s:
        c = await s.get(Credit, cid)
        if not c or c.status != "active":
            return await call.answer("Уже обработано", show_alert=True)
        c.status = "closed"
        c.checking = 0
        c.closed_at = now()
        uid = c.user_id
        await s.commit()
    await call.answer("Подтверждено")
    await edit_plain(call, (call.message.text or "") + "\n\n✅ Возврат подтверждён")
    await send_text(bot, uid, "repay_ok", await main_kb())


@router.callback_query(F.data.startswith("rx:"))
async def repay_reject(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    cid = int(call.data.split(":")[1])
    async with Session() as s:
        c = await s.get(Credit, cid)
        if not c or c.status != "active":
            return await call.answer("Уже обработано", show_alert=True)
        c.checking = 0
        uid = c.user_id
        await s.commit()
    await call.answer("Отклонено")
    await edit_plain(call, (call.message.text or "") + "\n\n❌ Возврат не подтверждён")
    await send_text(bot, uid, "repay_no", await main_kb())


# ═════════════ АДМИН-ПАНЕЛЬ ═════════════
def admin_kb() -> IKM:
    return IKM(inline_keyboard=[
        [IKB(text="💳 Должники", callback_data="ad:debts")],
        [IKB(text="👤 Найти / управлять пользователем", callback_data="ad:find")],
        [IKB(text="👥 Пользователи", callback_data="ad:users:0")],
        [IKB(text="📝 Тексты", callback_data="ad:texts")],
        [IKB(text="🔘 Кнопки", callback_data="ad:btns")],
        [IKB(text="🔄 Обновить", callback_data="ad:home")],
    ])


def back_kb(to: str = "ad:home") -> IKM:
    return IKM(inline_keyboard=[[IKB(text="⬅️ Назад", callback_data=to)]])


async def admin_stats() -> str:
    async with Session() as s:
        users = await s.scalar(select(func.count(User.id))) or 0
        debts = await s.scalar(select(func.count(Credit.id)).where(Credit.status == "active")) or 0
        debt_sum = await s.scalar(select(func.sum(Credit.repay)).where(Credit.status == "active")) or 0
        pend = await s.scalar(select(func.count(Credit.id)).where(Credit.status == "pending")) or 0
        issued = await s.scalar(select(func.sum(Credit.amount))
                                .where(Credit.status.in_(["active", "closed", "forgiven"]))) or 0
        returned = await s.scalar(select(func.sum(Credit.repay)).where(Credit.status == "closed")) or 0
        overdue = await s.scalar(select(func.count(Credit.id)).where(
            Credit.status == "active", Credit.due_at <= now())) or 0
    return (f"🛠 Админ-панель Дев-Банк\n\n👥 Пользователей: {users}\n"
            f"💳 Выдано кредитов на: {fmt(issued)}\n✅ Возвращено: {fmt(returned)}\n"
            f"🔴 Должников: {debts} (просрочили: {overdue})\n"
            f"💰 К возврату сейчас: {fmt(debt_sum)}\n⏳ Заявок на кредит: {pend}")


@router.callback_query(F.data == "ad:home")
async def ad_home(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.clear()
    await call.answer()
    await edit_plain(call, await admin_stats(), admin_kb())


PAGE = 15


@router.callback_query(F.data.startswith("ad:users:"))
async def ad_users(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    page = max(0, int(call.data.split(":")[2]))
    async with Session() as s:
        total = await s.scalar(select(func.count(User.id))) or 0
        users = (await s.execute(select(User).order_by(User.joined_at.desc())
                                 .offset(page * PAGE).limit(PAGE))).scalars().all()
        ids = [u.id for u in users]
        credits = (await s.execute(select(Credit).where(Credit.user_id.in_(ids),
                                   Credit.status.in_(["active", "closed", "forgiven"])))).scalars().all() if ids else []
    taken, owe, done = {}, {}, {}
    for c in credits:
        taken[c.user_id] = taken.get(c.user_id, 0) + c.amount
        if c.status == "active":
            owe[c.user_id] = owe.get(c.user_id, 0) + c.repay
        else:
            done[c.user_id] = done.get(c.user_id, 0) + 1
    lines = [f"👥 Пользователи ({total}) — стр. {page + 1}\n"]
    for i, u in enumerate(users, page * PAGE + 1):
        lines.append(f"{i}. {name_of(u)} | {u.id}\n    брал всего: {fmt(taken.get(u.id, 0))} | "
                     f"долг: {fmt(owe.get(u.id, 0))} | закрыто кредитов: {done.get(u.id, 0)}")
    nav = []
    if page > 0:
        nav.append(IKB(text="◀️", callback_data=f"ad:users:{page - 1}"))
    if (page + 1) * PAGE < total:
        nav.append(IKB(text="▶️", callback_data=f"ad:users:{page + 1}"))
    rows = ([nav] if nav else []) + [[IKB(text="⬅️ Назад", callback_data="ad:home")]]
    await call.answer()
    await edit_plain(call, "\n".join(lines), IKM(inline_keyboard=rows))


@router.callback_query(F.data == "ad:debts")
async def ad_debts(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    async with Session() as s:
        rows = (await s.execute(select(Credit, User).join(User, User.id == Credit.user_id)
                                .where(Credit.status == "active").order_by(Credit.due_at).limit(30))).all()
    await call.answer()
    if not rows:
        return await edit_plain(call, "💳 Должников нет.", back_kb())
    lines = ["💳 Должники\n"]
    kb_rows = []
    for i, (c, u) in enumerate(rows, 1):
        mark = "⚠️ " if c.due_at <= now() else ""
        lines.append(f"{i}. {mark}{name_of(u)} | {u.id}\n    взял {fmt(c.amount)} → вернуть {fmt(c.repay)}\n"
                     f"    {left_text(c.due_at)}{' (ждёт проверки возврата)' if c.checking else ''}")
        kb_rows.append([IKB(text=f"👤 {name_of(u)}", callback_data=f"uc:{u.id}")])
    kb_rows.append([IKB(text="⬅️ Назад", callback_data="ad:home")])
    await edit_plain(call, "\n".join(lines), IKM(inline_keyboard=kb_rows))


# ── поиск и управление пользователем ──
async def find_user(s, q: str):
    q = q.strip().lstrip("@")
    if q.isdigit():
        return await s.get(User, int(q))
    res = await s.execute(select(User).where(func.lower(User.username) == q.lower()))
    return res.scalars().first()


async def user_card(uid: int):
    async with Session() as s:
        u = await s.get(User, uid)
        credit = await open_credit(s, uid, ("active",))
        pending = await open_credit(s, uid, ("pending",))
        taken = await s.scalar(select(func.sum(Credit.amount)).where(
            Credit.user_id == uid, Credit.status.in_(["active", "closed", "forgiven"]))) or 0
        closed = await s.scalar(select(func.count(Credit.id)).where(
            Credit.user_id == uid, Credit.status == "closed")) or 0
    lines = [f"👤 {name_of(u) if u else uid} | {uid}", "",
             f"💳 Брал кредитов на: {fmt(taken)} | закрыто: {closed}"]
    if credit:
        lines.append(f"🔴 Долг: {fmt(credit.repay)} (взял {fmt(credit.amount)}) — {left_text(credit.due_at)}")
        if credit.receipt:
            lines.append("📜 Расписка подписана")
        if credit.plan:
            lines.append(f"📝 Как вернёт: {credit.plan[:300]}")
    else:
        lines.append("🟢 Долгов нет")
    if pending:
        lines.append(f"⏳ Есть заявка на кредит: {fmt(pending.amount)}")
    rows = []
    if not credit and not pending:
        rows.append([IKB(text="💳 Выдать кредит", callback_data=f"ua:ci:{uid}")])
    if credit:
        rows.append([IKB(text="✏️ Изменить сумму долга", callback_data=f"ua:ed:{uid}"),
                     IKB(text="✅ Закрыть долг", callback_data=f"ua:cl:{uid}")])
    rows.append([IKB(text="✉️ Написать через бота", callback_data=f"ua:msg:{uid}")])
    rows.append([IKB(text="💬 Открыть профиль", url=f"tg://user?id={uid}")])
    rows.append([IKB(text="⬅️ Назад", callback_data="ad:home")])
    return "\n".join(lines), IKM(inline_keyboard=rows)


@router.callback_query(F.data == "ad:find")
async def ad_find_ask(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.set_state(Adm.find)
    await call.answer()
    await call.message.answer("Отправьте @username или ID пользователя (/cancel — отмена):")


@router.message(Adm.find)
async def ad_find(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    async with Session() as s:
        u = await find_user(s, m.text or "")
    if not u:
        return await m.answer("Не найден. Пользователь должен хотя бы раз запустить бота. "
                              "Попробуйте ещё раз или /cancel")
    await state.clear()
    text, kb = await user_card(u.id)
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("uc:"))
async def ad_card(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.clear()
    await call.answer()
    text, kb = await user_card(int(call.data.split(":")[1]))
    await edit_plain(call, text, kb)


@router.callback_query(F.data.startswith("ua:"))
async def ad_user_action(call: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    _, op, uid = call.data.split(":")
    uid = int(uid)
    await call.answer()
    if op == "ci":
        await state.set_state(Adm.amount)
        await state.update_data(op=op, uid=uid)
        await call.message.answer("💳 Сколько ВЫДАТЬ в кредит? Введите сумму (например: 10ккк).\n"
                                  "Сумма к возврату посчитается автоматически. /cancel — отмена")
    elif op == "ed":
        await state.set_state(Adm.amount)
        await state.update_data(op=op, uid=uid)
        await call.message.answer("✏️ Введите НОВУЮ сумму долга (сколько ему нужно вернуть). /cancel — отмена")
    elif op == "msg":
        await state.set_state(Adm.msg)
        await state.update_data(uid=uid)
        await call.message.answer("Отправьте сообщение для пользователя — оно придёт от бота. /cancel — отмена")
    elif op == "cl":
        async with Session() as s:
            c = await open_credit(s, uid, ("active",))
            had = c is not None
            if c:
                c.status = "forgiven"
                c.checking = 0
                c.closed_at = now()
                await s.commit()
        if had:
            try:
                await send_text(bot, uid, "debt_closed", await main_kb())
            except Exception as e:
                await call.message.answer(f"⚠️ Не удалось уведомить пользователя: {e}")
        text, kb = await user_card(uid)
        await call.message.answer("✅ Долг закрыт, пользователь уведомлён.\n\n" + text, reply_markup=kb)


@router.message(Adm.amount)
async def ad_amount(m: Message, state: FSMContext, bot: Bot):
    if not is_admin(m.from_user.id):
        return
    amt = parse_amount(m.text) if m.text else None
    if amt is None:
        return await m.answer("Неверная сумма. Пример: 5ккк. /cancel — отмена")
    data = await state.get_data()
    await state.clear()
    uid, op = data["uid"], data["op"]
    if op == "ci":
        async with Session() as s:
            if await open_credit(s, uid):
                return await m.answer("У пользователя уже есть заявка или долг.")
            u = await s.get(User, uid)
            repay = repay_amount(amt)
            s.add(Credit(user_id=uid, amount=amt, repay=repay, status="active", created_at=now(),
                         due_at=now() + timedelta(hours=CREDIT_HOURS), next_remind=now() + timedelta(hours=1)))
            username = u.username if u else None
            await s.commit()
        try:
            await send_text(bot, uid, "credit_ok", await main_kb(), amount=fmt(amt), repay=fmt(repay))
        except Exception as e:
            await m.answer(f"⚠️ Не удалось написать пользователю: {e}")
        await m.answer(f"✅ Кредит выдан: {fmt(amt)}, вернуть {fmt(repay)}.")
        await send_money_prompt(bot, m.from_user.id, username, uid, amt)
    else:  # ed — изменить сумму долга
        async with Session() as s:
            c = await open_credit(s, uid, ("active",))
            if c:
                c.repay = amt
                await s.commit()
        text, kb = await user_card(uid)
        await m.answer(f"✅ Сумма долга изменена на {fmt(amt)}.\n\n" + text, reply_markup=kb)


@router.message(Adm.msg)
async def ad_msg(m: Message, state: FSMContext, bot: Bot):
    if not is_admin(m.from_user.id):
        return
    uid = (await state.get_data())["uid"]
    await state.clear()
    try:
        await bot.send_message(uid, "✉️ Сообщение от Дев-Банка:")
        await m.copy_to(uid)
        await m.answer("✅ Сообщение отправлено.")
    except Exception as e:
        await m.answer(f"❌ Не удалось отправить: {e}")


# ── тексты ──
@router.callback_query(F.data == "ad:texts")
async def ad_texts(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.clear()
    await call.answer()
    rows = [[IKB(text=v[0], callback_data=f"tx:{k}")] for k, v in TEXT_DEFS.items()]
    rows.append([IKB(text="⬅️ Назад", callback_data="ad:home")])
    await edit_plain(call, "📝 Выберите текст для редактирования:", IKM(inline_keyboard=rows))


@router.callback_query(F.data.startswith("tx:"))
async def ad_text_pick(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    key = call.data.split(":")[1]
    await state.set_state(Adm.text)
    await state.update_data(key=key)
    await call.answer()
    label, _, vars_ = TEXT_DEFS[key]
    hint = f"\nПеременные (оставьте как есть): {vars_}" if vars_ else ""
    kb = IKM(inline_keyboard=[
        [IKB(text="↩️ Сбросить по умолчанию", callback_data=f"txr:{key}")],
        [IKB(text="⬅️ Назад", callback_data="ad:texts")],
    ])
    await call.message.answer(f"✏️ {label}{hint}\n\nОтправьте новый текст (можно с премиум эмодзи, "
                              f"жирным и т.д.). Текущий текст ниже:", reply_markup=kb)
    texts = await cfg("texts", TEXT_DEFAULTS)
    text, ents = render(texts[key])
    await call.message.answer(text, entities=ents)


@router.callback_query(F.data.startswith("txr:"))
async def ad_text_reset(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await cfg_set("texts", call.data.split(":")[1], None)
    await state.clear()
    await call.answer("Сброшено", show_alert=True)


@router.message(Adm.text)
async def ad_text_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id) or not m.text:
        return
    key = (await state.get_data())["key"]
    await cfg_set("texts", key, pack(m))
    await state.clear()
    await m.answer("✅ Текст сохранён.", reply_markup=back_kb("ad:texts"))


# ── кнопки ──
@router.callback_query(F.data == "ad:btns")
async def ad_btns(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.clear()
    await call.answer()
    rows = [[IKB(text=v[0], callback_data=f"bt:{k}")] for k, v in BTN_DEFS.items()]
    rows.append([IKB(text="⬅️ Назад", callback_data="ad:home")])
    await edit_plain(call, "🔘 Выберите кнопку:", IKM(inline_keyboard=rows))


@router.callback_query(F.data.startswith("bt:"))
async def ad_btn_menu(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.clear()
    key = call.data.split(":")[1]
    b = (await cfg("buttons", BTN_DEFAULTS))[key]
    color = next((v[0] for v in COLORS.values() if v[1] == b["color"]), "⚪ Без цвета")
    kb = IKM(inline_keyboard=[
        [IKB(text="✏️ Текст", callback_data=f"bte:{key}")],
        [IKB(text="🎨 Цвет", callback_data=f"btc:{key}")],
        [IKB(text="✨ Премиум эмодзи", callback_data=f"btm:{key}")],
        [IKB(text="↩️ Сбросить кнопку", callback_data=f"btr:{key}")],
        [IKB(text="⬅️ Назад", callback_data="ad:btns")],
    ])
    await call.answer()
    await edit_plain(call, f"🔘 {BTN_DEFS[key][0]}\n\nТекст: {b['text']}\nЦвет: {color}\n"
                           f"Эмодзи: {'установлено' if b['emoji'] else 'нет'}", kb)


@router.callback_query(F.data.startswith("bte:"))
async def ad_btn_text_ask(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.set_state(Adm.btn_text)
    await state.update_data(key=call.data.split(":")[1])
    await call.answer()
    await call.message.answer("Отправьте новый текст кнопки (/cancel — отмена):")


@router.message(Adm.btn_text)
async def ad_btn_text_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id) or not m.text:
        return
    key = (await state.get_data())["key"]
    await cfg_set("buttons", key, {"text": m.text[:64]})
    await state.clear()
    await m.answer("✅ Текст кнопки сохранён.", reply_markup=back_kb("ad:btns"))


@router.callback_query(F.data.startswith("btc:"))
async def ad_btn_color(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    key = call.data.split(":")[1]
    rows = [[IKB(text=v[0], callback_data=f"bts:{key}:{c}")] for c, v in COLORS.items()]
    rows.append([IKB(text="⬅️ Назад", callback_data=f"bt:{key}")])
    await call.answer()
    await edit_plain(call, "🎨 Выберите цвет кнопки:", IKM(inline_keyboard=rows))


@router.callback_query(F.data.startswith("bts:"))
async def ad_btn_color_set(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    _, key, c = call.data.split(":")
    await cfg_set("buttons", key, {"color": COLORS[c][1]})
    await call.answer("Сохранено", show_alert=True)


@router.callback_query(F.data.startswith("btm:"))
async def ad_btn_emoji_ask(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await state.set_state(Adm.btn_emoji)
    await state.update_data(key=call.data.split(":")[1])
    await call.answer()
    await call.message.answer("Отправьте ОДНО премиум эмодзи для кнопки.\n"
                              "Отправьте «-», чтобы убрать эмодзи. (/cancel — отмена)")


@router.message(Adm.btn_emoji)
async def ad_btn_emoji_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id) or not m.text:
        return
    key = (await state.get_data())["key"]
    if m.text.strip() == "-":
        emoji = ""
    else:
        custom = [e for e in (m.entities or []) if e.type == "custom_emoji"]
        if not custom:
            return await m.answer("❌ Это не премиум эмодзи. Отправьте именно премиум эмодзи.")
        emoji = custom[0].custom_emoji_id
    await cfg_set("buttons", key, {"emoji": emoji})
    await state.clear()
    await m.answer("✅ Эмодзи кнопки сохранено.", reply_markup=back_kb("ad:btns"))


@router.callback_query(F.data.startswith("btr:"))
async def ad_btn_reset(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await cfg_set("buttons", call.data.split(":")[1], None)
    await call.answer("Сброшено", show_alert=True)


# ═════════════ ЕЖЕЧАСНЫЕ НАПОМИНАНИЯ ═════════════
async def run_reminders(bot: Bot):
    out = []
    async with Session() as s:
        res = await s.execute(select(Credit).where(Credit.status == "active", Credit.checking == 0,
                                                   Credit.next_remind <= now()))
        for c in res.scalars():
            while c.next_remind <= now():
                c.next_remind += timedelta(hours=1)
            out.append((c.user_id, c.repay, c.amount, c.due_at))
        await s.commit()
    for uid, repay, amount, due in out:
        key = "credit_remind" if due > now() else "credit_overdue"
        try:
            await send_text(bot, uid, key, await repay_kb(repay), repay=fmt(repay),
                            amount=fmt(amount), left=left_text(due))
        except Exception as e:
            logging.warning("reminder failed for %s: %s", uid, e)
        await asyncio.sleep(0.05)


async def background_loop(bot: Bot):
    while True:
        try:
            await run_reminders(bot)
        except Exception:
            logging.exception("reminders error")
        await asyncio.sleep(300)


# ═════════════ ЗАПУСК ═════════════
async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN:
        raise SystemExit("Укажите переменную окружения BOT_TOKEN")
    if not ADMIN_IDS:
        raise SystemExit("Укажите переменную окружения ADMIN_ID")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # добавляем новые колонки в уже существующую таблицу credits
        if engine.dialect.name == "sqlite":
            cols = {r[1] for r in (await conn.exec_driver_sql("PRAGMA table_info(credits)")).fetchall()}
            if "plan" not in cols:
                await conn.exec_driver_sql("ALTER TABLE credits ADD COLUMN plan TEXT")
            if "receipt" not in cols:
                await conn.exec_driver_sql("ALTER TABLE credits ADD COLUMN receipt INTEGER DEFAULT 0")
        else:
            await conn.exec_driver_sql("ALTER TABLE credits ADD COLUMN IF NOT EXISTS plan TEXT")
            await conn.exec_driver_sql("ALTER TABLE credits ADD COLUMN IF NOT EXISTS receipt INTEGER DEFAULT 0")
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    task = asyncio.create_task(background_loop(bot))
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
