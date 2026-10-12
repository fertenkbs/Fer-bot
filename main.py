import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

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

BASE_RATE = 2                  # % в день
DONATE_STEP = 10_000_000_000   # за каждые 10ккк пожертвований...
DONATE_BONUS = 1               # ...+1% к ежедневному росту
LOCK_DAYS = 7                  # через сколько дней можно выводить
MAX_DEPOSIT = 30_000_000_000   # 30ккк — ОБЩИЙ лимит пополнений на аккаунт
INT_CAP = 9_000_000_000_000_000_000

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


class Deposit(Base):
    __tablename__ = "deposits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    balance: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/active/closed/rejected
    created_at: Mapped[datetime] = mapped_column(DateTime)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_accrual: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Withdrawal(Base):
    __tablename__ = "withdrawals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/done/rejected
    created_at: Mapped[datetime] = mapped_column(DateTime)
    allocations: Mapped[str] = mapped_column(Text, default="{}")


class Donation(Base):
    __tablename__ = "donations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/accepted/rejected
    created_at: Mapped[datetime] = mapped_column(DateTime)


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
                 "Тут вы можете заработать, просто положив Айсы в бота.", ""),
    "earn": ("Экран «Заработать Айсы»",
             "Вы переводите через бота @EyesSeeGamesBot любую сумму Айсов. "
             "Каждый день она растёт на 2%. Вы можете вывести её в любой день, "
             "когда копилке исполнится хотя бы 7 дней.", ""),
    "deposit": ("Экран «Перевести Айсы»",
                "Если вы хотите положить ваши Айсы в копилку и тем самым они будут расти, "
                "переводя Айсы вы соглашаетесь, что переводили именно вы.\n\n"
                "Чтобы перевести Айсы, зайдите в бота @EyesSeeGamesBot и напишите:\n"
                "п [сумма] @deverskyi\n\nОбщий лимит пополнений на аккаунт: {limit}\n"
                "Уже внесено: {used}, осталось: {left}", "{limit} {used} {left}"),
    "ask_amount": ("Запрос суммы пополнения",
                   "Введите сумму, которую вы перевели (например: 5кк или 1500000):", ""),
    "proof": ("Просьба переслать сообщение",
              "После того как вы перевели сумму, перешлите сообщение об успешном переводе сюда.", ""),
    "wait": ("«Заявку проверят»", "В течение 24 часов вашу заявку проверят.", ""),
    "approved": ("Заявка одобрена",
                 "✅ Ваша заявка одобрена! В копилке: {amount} Айсов. "
                 "Каждый день она будет расти на {rate}%.", "{amount} {rate}"),
    "rejected": ("Заявка отклонена", "❌ Ваша заявка отклонена.", ""),
    "daily": ("Ежедневное начисление", "+{rate}% ({amount})", "{amount} {rate}"),
    "bad_amount": ("Неверная сумма", "Неверная сумма. Введите число, например: 5кк", ""),
    "over_limit": ("Превышен лимит",
                   "Общий лимит пополнений — {limit}. Вы уже внесли {used}, осталось {left}.",
                   "{limit} {used} {left}"),
    "donate": ("Экран «Пожертвовать»",
               "Вы можете поддержать владельца Дев-Банка пожертвованием. За каждые 10ккк Айсов "
               "пожертвований ваша копилка получает +1% к ежедневному росту.\n\n"
               "Вы уже пожертвовали: {donated}\nВаш ежедневный рост: {rate}%\n\n"
               "Чтобы пожертвовать, зайдите в бота @EyesSeeGamesBot и напишите:\n"
               "п [сумма] @deverskyi\n\nПожертвование не возвращается и не выводится.",
               "{donated} {rate}"),
    "donate_ask": ("Пожертвование: запрос суммы",
                   "Введите сумму пожертвования (например: 10ккк):", ""),
    "donate_proof": ("Пожертвование: переслать сообщение",
                     "После перевода перешлите сообщение об успешном переводе сюда.", ""),
    "donate_wait": ("Пожертвование: заявка принята",
                    "В течение 24 часов ваше пожертвование проверят.", ""),
    "donate_ok": ("Пожертвование принято",
                  "💛 Спасибо за пожертвование {amount} Айсов! Всего пожертвовано: {donated}. "
                  "Ваш ежедневный рост теперь {rate}%.", "{amount} {donated} {rate}"),
    "donate_no": ("Пожертвование отклонено", "❌ Ваше пожертвование не подтверждено.", ""),
    "wd_empty": ("Вывод: копилки нет",
                 "У вас пока нет копилки. Нажмите «Заработать Айсы», чтобы начать.", ""),
    "wd_locked": ("Вывод: ещё рано",
                  "Копилке должно исполниться хотя бы 7 дней.\nВ копилке: {total}\n"
                  "Вывод откроется через {left}.", "{total} {left}"),
    "wd_ask": ("Вывод: запрос суммы",
               "В копилке: {total}\nДоступно к выводу: {available}\n\n"
               "Введите сумму вывода (или напишите «всё»):", "{total} {available}"),
    "wd_confirm": ("Вывод: подтверждение", "Вы хотите вывести {amount} Айсов?", "{amount}"),
    "wd_sent": ("Вывод: заявка отправлена",
                "Заявка на вывод {amount} Айсов отправлена. Ожидайте перевод.", "{amount}"),
    "wd_done": ("Вывод: выполнен", "✅ Вывод выполнен. Сообщение о переводе ниже:", ""),
    "wd_rejected": ("Вывод: отклонён",
                    "❌ Заявка на вывод отклонена, сумма возвращена в копилку.", ""),
}
TEXT_DEFAULTS = {k: {"text": v[1], "entities": []} for k, v in TEXT_DEFS.items()}

BTN_DEFS = {
    "earn": ("Заработать Айсы", "Заработать Айсы"),
    "wd": ("Вывести", "Вывести"),
    "donate": ("Пожертвовать", "Пожертвовать"),
    "dep": ("Перевести Айсы", "Перевести Айсы"),
    "cancel": ("Отмена", "Отмена"),
    "go": ("Перевести (после перевода)", "Перевести"),
    "ok": ("Подтвердить вывод", "Подтвердить"),
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
    for k in (3, 2, 1):
        if n >= 1000 ** k:
            v = f"{n / 1000 ** k:.2f}".rstrip("0").rstrip(".")
            return v + "к" * k
    return ""


def fmt(n: int) -> str:
    s = f"{n:,}".replace(",", " ")
    sh = short(n)
    return f"{s} ({sh})" if sh else s


def fmt_td(td: timedelta) -> str:
    return f"{td.days} д. {td.seconds // 3600} ч."


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


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


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


async def btn(key: str, cb: str) -> IKB:
    b = (await cfg("buttons", BTN_DEFAULTS))[key]
    extra = {}
    if b["color"]:
        extra["style"] = b["color"]
    if b["emoji"]:
        extra["icon_custom_emoji_id"] = b["emoji"]
    return IKB(text=b["text"], callback_data=cb, **extra)


async def main_kb() -> IKM:
    return IKM(inline_keyboard=[[await btn("earn", "earn")], [await btn("wd", "wd")],
                                [await btn("donate", "donate")]])


async def cancel_kb() -> IKM:
    return IKM(inline_keyboard=[[await btn("cancel", "home")]])


async def send_text(bot: Bot, chat_id: int, key: str, kb=None, **kw):
    texts = await cfg("texts", TEXT_DEFAULTS)
    text, ents = render(texts[key], **kw)
    return await bot.send_message(chat_id, text, entities=ents, reply_markup=kb)


async def show(call: CallbackQuery, key: str, kb=None, **kw):
    texts = await cfg("texts", TEXT_DEFAULTS)
    text, ents = render(texts[key], **kw)
    try:
        await call.message.edit_text(text, entities=ents, reply_markup=kb)
    except TelegramBadRequest:
        await call.message.answer(text, entities=ents, reply_markup=kb)


async def edit_plain(call: CallbackQuery, text: str, kb=None):
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=kb)


async def upsert_user(u):
    async with Session() as s:
        row = await s.get(User, u.id)
        if row:
            row.username, row.full_name = u.username, u.full_name
        else:
            s.add(User(id=u.id, username=u.username, full_name=u.full_name, joined_at=now()))
        await s.commit()


async def piggy(s, uid: int):
    """(все вклады, доступные к выводу, всего, доступно, ближайшая разблокировка)"""
    res = await s.execute(select(Deposit).where(Deposit.user_id == uid, Deposit.status == "active")
                          .order_by(Deposit.approved_at))
    deps = list(res.scalars())
    t = now()
    elig = [d for d in deps if d.approved_at + timedelta(days=LOCK_DAYS) <= t]
    total = sum(d.balance for d in deps)
    avail = sum(d.balance for d in elig)
    unlock = min((d.approved_at + timedelta(days=LOCK_DAYS) for d in deps), default=None)
    return deps, elig, total, avail, unlock


async def used_limit(s, uid: int) -> int:
    return await s.scalar(select(func.sum(Deposit.amount)).where(
        Deposit.user_id == uid, Deposit.status.in_(["pending", "active", "closed"]))) or 0


async def donated_sum(s, uid: int) -> int:
    return await s.scalar(select(func.sum(Donation.amount)).where(
        Donation.user_id == uid, Donation.status == "accepted")) or 0


def rate_for(donated: int) -> int:
    return BASE_RATE + (donated // DONATE_STEP) * DONATE_BONUS


# ═════════════ FSM ═════════════
class Dep(StatesGroup):
    amount = State()
    proof = State()


class Wd(StatesGroup):
    amount = State()


class Don(StatesGroup):
    amount = State()
    proof = State()


class Adm(StatesGroup):
    text = State()
    btn_text = State()
    btn_emoji = State()
    wproof = State()


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


# ═════════════ ПОЛЬЗОВАТЕЛЬ: ПОПОЛНЕНИЕ ═════════════
@router.callback_query(F.data == "home")
async def cb_home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await show(call, "greeting", await main_kb())


@router.callback_query(F.data == "earn")
async def cb_earn(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    kb = IKM(inline_keyboard=[[await btn("dep", "dep")], [await btn("cancel", "home")]])
    await show(call, "earn", kb)


@router.callback_query(F.data == "dep")
async def cb_dep(call: CallbackQuery):
    await call.answer()
    async with Session() as s:
        used = await used_limit(s, call.from_user.id)
    left = max(MAX_DEPOSIT - used, 0)
    info = dict(limit=fmt(MAX_DEPOSIT), used=fmt(used), left=fmt(left))
    if left == 0:
        return await show(call, "over_limit", await cancel_kb(), **info)
    kb = IKM(inline_keyboard=[[await btn("go", "dep_go")], [await btn("cancel", "home")]])
    await show(call, "deposit", kb, **info)


@router.callback_query(F.data == "dep_go")
async def cb_dep_go(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(Dep.amount)
    await show(call, "ask_amount", await cancel_kb())


@router.message(Dep.amount)
async def dep_amount(m: Message, state: FSMContext, bot: Bot):
    amount = parse_amount(m.text) if m.text else None
    if amount is None:
        return await send_text(bot, m.chat.id, "bad_amount", await cancel_kb())
    async with Session() as s:
        used = await used_limit(s, m.from_user.id)
    if used + amount > MAX_DEPOSIT:
        return await send_text(bot, m.chat.id, "over_limit", await cancel_kb(), limit=fmt(MAX_DEPOSIT),
                               used=fmt(used), left=fmt(max(MAX_DEPOSIT - used, 0)))
    await state.update_data(amount=amount)
    await state.set_state(Dep.proof)
    await send_text(bot, m.chat.id, "proof", await cancel_kb())


@router.message(Dep.proof)
async def dep_proof(m: Message, state: FSMContext, bot: Bot):
    amount = (await state.get_data())["amount"]
    await upsert_user(m.from_user)
    async with Session() as s:
        used = await used_limit(s, m.from_user.id)
        if used + amount > MAX_DEPOSIT:
            await state.clear()
            return await send_text(bot, m.chat.id, "over_limit", await main_kb(), limit=fmt(MAX_DEPOSIT),
                                   used=fmt(used), left=fmt(max(MAX_DEPOSIT - used, 0)))
        d = Deposit(user_id=m.from_user.id, amount=amount, balance=amount,
                    status="pending", created_at=now())
        s.add(d)
        await s.commit()
        await s.refresh(d)
        did = d.id
    kb = IKM(inline_keyboard=[[
        IKB(text="✅ Принять", callback_data=f"da:{did}", style="success"),
        IKB(text="❌ Отказать", callback_data=f"dr:{did}", style="danger"),
    ]])
    head = (f"💰 Заявка на пополнение #{did}\n"
            f"👤 {uname(m.from_user)} | {m.from_user.full_name}\n"
            f"🆔 {m.from_user.id}\nСумма: {fmt(amount)} Айсов")
    for a in ADMIN_IDS:
        try:
            await bot.send_message(a, head, reply_markup=kb)
            try:
                await m.forward(a)
            except Exception:
                await m.copy_to(a)
        except Exception as e:
            logging.warning("admin notify failed: %s", e)
    await state.clear()
    await send_text(bot, m.chat.id, "wait", await main_kb())


@router.callback_query(F.data.startswith("da:"))
async def dep_accept(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    did = int(call.data.split(":")[1])
    async with Session() as s:
        d = await s.get(Deposit, did)
        if not d or d.status != "pending":
            return await call.answer("Уже обработано", show_alert=True)
        d.status = "active"
        d.approved_at = now()
        d.next_accrual = now() + timedelta(days=1)
        uid, bal = d.user_id, d.balance
        rate = rate_for(await donated_sum(s, uid))
        await s.commit()
    await call.answer("Принято")
    await edit_plain(call, (call.message.text or "") + "\n\n✅ Принято")
    await send_text(bot, uid, "approved", await main_kb(), amount=fmt(bal), rate=rate)


@router.callback_query(F.data.startswith("dr:"))
async def dep_reject(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    did = int(call.data.split(":")[1])
    async with Session() as s:
        d = await s.get(Deposit, did)
        if not d or d.status != "pending":
            return await call.answer("Уже обработано", show_alert=True)
        d.status = "rejected"
        uid = d.user_id
        await s.commit()
    await call.answer("Отклонено")
    await edit_plain(call, (call.message.text or "") + "\n\n❌ Отказано")
    await send_text(bot, uid, "rejected", await main_kb())


# ═════════════ ПОЖЕРТВОВАНИЯ ═════════════
@router.callback_query(F.data == "donate")
async def cb_donate(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    async with Session() as s:
        donated = await donated_sum(s, call.from_user.id)
    kb = IKM(inline_keyboard=[[await btn("go", "don_go")], [await btn("cancel", "home")]])
    await show(call, "donate", kb, donated=fmt(donated), rate=rate_for(donated))


@router.callback_query(F.data == "don_go")
async def cb_don_go(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(Don.amount)
    await show(call, "donate_ask", await cancel_kb())


@router.message(Don.amount)
async def don_amount(m: Message, state: FSMContext, bot: Bot):
    amount = parse_amount(m.text) if m.text else None
    if amount is None:
        return await send_text(bot, m.chat.id, "bad_amount", await cancel_kb())
    await state.update_data(amount=amount)
    await state.set_state(Don.proof)
    await send_text(bot, m.chat.id, "donate_proof", await cancel_kb())


@router.message(Don.proof)
async def don_proof(m: Message, state: FSMContext, bot: Bot):
    amount = (await state.get_data())["amount"]
    await upsert_user(m.from_user)
    async with Session() as s:
        d = Donation(user_id=m.from_user.id, amount=amount, status="pending", created_at=now())
        s.add(d)
        await s.commit()
        await s.refresh(d)
        nid = d.id
    kb = IKM(inline_keyboard=[[
        IKB(text="✅ Принять", callback_data=f"na:{nid}", style="success"),
        IKB(text="❌ Отказать", callback_data=f"nr:{nid}", style="danger"),
    ]])
    head = (f"💛 Пожертвование #{nid}\n"
            f"👤 {uname(m.from_user)} | {m.from_user.full_name}\n"
            f"🆔 {m.from_user.id}\nСумма: {fmt(amount)} Айсов")
    for a in ADMIN_IDS:
        try:
            await bot.send_message(a, head, reply_markup=kb)
            try:
                await m.forward(a)
            except Exception:
                await m.copy_to(a)
        except Exception as e:
            logging.warning("admin notify failed: %s", e)
    await state.clear()
    await send_text(bot, m.chat.id, "donate_wait", await main_kb())


@router.callback_query(F.data.startswith("na:"))
async def don_accept(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    nid = int(call.data.split(":")[1])
    async with Session() as s:
        d = await s.get(Donation, nid)
        if not d or d.status != "pending":
            return await call.answer("Уже обработано", show_alert=True)
        d.status = "accepted"
        await s.commit()
        uid, amount = d.user_id, d.amount
        donated = await donated_sum(s, uid)
    await call.answer("Принято")
    await edit_plain(call, (call.message.text or "") + "\n\n✅ Принято")
    await send_text(bot, uid, "donate_ok", await main_kb(), amount=fmt(amount),
                    donated=fmt(donated), rate=rate_for(donated))


@router.callback_query(F.data.startswith("nr:"))
async def don_reject(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    nid = int(call.data.split(":")[1])
    async with Session() as s:
        d = await s.get(Donation, nid)
        if not d or d.status != "pending":
            return await call.answer("Уже обработано", show_alert=True)
        d.status = "rejected"
        uid = d.user_id
        await s.commit()
    await call.answer("Отклонено")
    await edit_plain(call, (call.message.text or "") + "\n\n❌ Отказано")
    await send_text(bot, uid, "donate_no", await main_kb())


# ═════════════ ПОЛЬЗОВАТЕЛЬ: ВЫВОД ═════════════
@router.callback_query(F.data == "wd")
async def cb_wd(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    async with Session() as s:
        _, _, total, avail, unlock = await piggy(s, call.from_user.id)
    back = await cancel_kb()
    if total == 0:
        return await show(call, "wd_empty", back)
    if avail == 0:
        return await show(call, "wd_locked", back, total=fmt(total), left=fmt_td(unlock - now()))
    await state.set_state(Wd.amount)
    await show(call, "wd_ask", back, total=fmt(total), available=fmt(avail))


@router.message(Wd.amount)
async def wd_amount(m: Message, state: FSMContext, bot: Bot):
    async with Session() as s:
        _, _, _, avail, _ = await piggy(s, m.from_user.id)
    t = (m.text or "").strip().lower()
    amount = avail if t in ("всё", "все", "all") else (parse_amount(t) if t else None)
    if amount is None or amount > avail:
        return await send_text(bot, m.chat.id, "bad_amount", await cancel_kb())
    await state.update_data(amount=amount)
    kb = IKM(inline_keyboard=[[await btn("ok", "wd_ok")], [await btn("cancel", "home")]])
    await send_text(bot, m.chat.id, "wd_confirm", kb, amount=fmt(amount))


@router.callback_query(F.data == "wd_ok")
async def wd_ok(call: CallbackQuery, state: FSMContext, bot: Bot):
    amount = (await state.get_data()).get("amount")
    if not amount:
        return await call.answer()
    uid = call.from_user.id
    async with Session() as s:
        _, elig, _, avail, _ = await piggy(s, uid)
        if amount > avail:
            await call.answer("Недостаточно средств", show_alert=True)
            return
        remaining, alloc = amount, {}
        for d in elig:
            if remaining <= 0:
                break
            take = min(d.balance, remaining)
            d.balance -= take
            if d.balance == 0:
                d.status = "closed"
            alloc[str(d.id)] = take
            remaining -= take
        w = Withdrawal(user_id=uid, amount=amount, status="pending",
                       created_at=now(), allocations=json.dumps(alloc))
        s.add(w)
        await s.commit()
        await s.refresh(w)
        wid = w.id
    await state.clear()
    await call.answer()
    await show(call, "wd_sent", await main_kb(), amount=fmt(amount))
    kb = IKM(inline_keyboard=[[
        IKB(text="✅ Я перевёл", callback_data=f"wp:{wid}", style="success"),
        IKB(text="❌ Отклонить", callback_data=f"wx:{wid}", style="danger"),
    ]])
    uname_ = uname(call.from_user)
    head = (f"💸 Заявка на вывод #{wid}\n👤 {uname_} | {call.from_user.full_name}\n"
            f"🆔 {uid}\nСумма: {fmt(amount)} Айсов\n\n"
            f"Переведите через @EyesSeeGamesBot:\nп {amount} {uname_}")
    for a in ADMIN_IDS:
        try:
            await bot.send_message(a, head, reply_markup=kb)
        except Exception as e:
            logging.warning("admin notify failed: %s", e)


@router.callback_query(F.data.startswith("wp:"))
async def wd_paid(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    wid = int(call.data.split(":")[1])
    async with Session() as s:
        w = await s.get(Withdrawal, wid)
    if not w or w.status != "pending":
        return await call.answer("Уже обработано", show_alert=True)
    await state.set_state(Adm.wproof)
    await state.update_data(wid=wid)
    await call.answer()
    await call.message.answer(f"Перешлите сюда сообщение об успешном переводе по заявке #{wid} "
                              f"— оно уйдёт пользователю. (/cancel — отмена)")


@router.message(Adm.wproof)
async def wd_proof(m: Message, state: FSMContext, bot: Bot):
    if not is_admin(m.from_user.id):
        return
    wid = (await state.get_data())["wid"]
    async with Session() as s:
        w = await s.get(Withdrawal, wid)
        if not w or w.status != "pending":
            await state.clear()
            return await m.answer("Заявка уже обработана.")
        w.status = "done"
        uid = w.user_id
        await s.commit()
    await send_text(bot, uid, "wd_done", await main_kb())
    try:
        await m.copy_to(uid)
    except Exception as e:
        await m.answer(f"⚠️ Не удалось отправить пользователю: {e}")
    await state.clear()
    await m.answer(f"✅ Заявка #{wid} закрыта, сообщение отправлено пользователю.")


@router.callback_query(F.data.startswith("wx:"))
async def wd_reject(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    wid = int(call.data.split(":")[1])
    async with Session() as s:
        w = await s.get(Withdrawal, wid)
        if not w or w.status != "pending":
            return await call.answer("Уже обработано", show_alert=True)
        for did, amt in json.loads(w.allocations).items():
            d = await s.get(Deposit, int(did))
            if d:
                d.balance += amt
                if d.status == "closed":
                    d.status = "active"
                    d.next_accrual = now() + timedelta(days=1)
        w.status = "rejected"
        uid = w.user_id
        await s.commit()
    await call.answer("Отклонено")
    await edit_plain(call, (call.message.text or "") + "\n\n❌ Отклонено, сумма возвращена")
    await send_text(bot, uid, "wd_rejected", await main_kb())


# ═════════════ АДМИН-ПАНЕЛЬ ═════════════
def admin_kb() -> IKM:
    return IKM(inline_keyboard=[
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
        pend_dep = await s.scalar(select(func.count(Deposit.id)).where(Deposit.status == "pending")) or 0
        pend_wd = await s.scalar(select(func.count(Withdrawal.id)).where(Withdrawal.status == "pending")) or 0
        total_in = await s.scalar(select(func.sum(Deposit.amount))
                                  .where(Deposit.status.in_(["active", "closed"]))) or 0
        owed = await s.scalar(select(func.sum(Deposit.balance)).where(Deposit.status == "active")) or 0
        dons = await s.scalar(select(func.sum(Donation.amount)).where(Donation.status == "accepted")) or 0
    return (f"🛠 Админ-панель Дев-Банк\n\n👥 Пользователей: {users}\n"
            f"💰 Всего внесено: {fmt(total_in)}\n🏦 Сейчас в копилках: {fmt(owed)}\n"
            f"💛 Пожертвовано: {fmt(dons)}\n"
            f"⏳ Заявок на пополнение: {pend_dep}\n💸 Заявок на вывод: {pend_wd}")


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
        deps = (await s.execute(select(Deposit).where(Deposit.user_id.in_(ids),
                                Deposit.status.in_(["active", "closed"])))).scalars().all() if ids else []
        nds = (await s.execute(select(Donation).where(Donation.user_id.in_(ids),
                               Donation.status == "accepted"))).scalars().all() if ids else []
    don = {}
    for x in nds:
        don[x.user_id] = don.get(x.user_id, 0) + x.amount
    put, cur = {}, {}
    for d in deps:
        put[d.user_id] = put.get(d.user_id, 0) + d.amount
        if d.status == "active":
            cur[d.user_id] = cur.get(d.user_id, 0) + d.balance
    lines = [f"👥 Пользователи ({total}) — стр. {page + 1}\n"]
    for i, u in enumerate(users, page * PAGE + 1):
        name = f"@{u.username}" if u.username else (u.full_name or "—")
        lines.append(f"{i}. {name} | {u.id}\n    внёс: {fmt(put.get(u.id, 0))} | в копилке: {fmt(cur.get(u.id, 0))}"
                     f"\n    пожертвовал: {fmt(don.get(u.id, 0))} | рост: {rate_for(don.get(u.id, 0))}%")
    nav = []
    if page > 0:
        nav.append(IKB(text="◀️", callback_data=f"ad:users:{page - 1}"))
    if (page + 1) * PAGE < total:
        nav.append(IKB(text="▶️", callback_data=f"ad:users:{page + 1}"))
    rows = ([nav] if nav else []) + [[IKB(text="⬅️ Назад", callback_data="ad:home")]]
    await call.answer()
    await edit_plain(call, "\n".join(lines), IKM(inline_keyboard=rows))


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


# ═════════════ ЕЖЕДНЕВНОЕ НАЧИСЛЕНИЕ ═════════════
async def run_accrual(bot: Bot):
    notify = []
    async with Session() as s:
        res = await s.execute(select(Deposit).where(Deposit.status == "active",
                                                    Deposit.next_accrual <= now()))
        rates = {}
        for d in res.scalars():
            if d.user_id not in rates:
                rates[d.user_id] = rate_for(await donated_sum(s, d.user_id))
            rate = rates[d.user_id]
            while d.next_accrual <= now():
                d.balance = min(d.balance + d.balance * rate // 100, INT_CAP)
                d.next_accrual += timedelta(days=1)
            notify.append((d.user_id, d.balance, rate))
        await s.commit()
    for uid, bal, rate in notify:
        try:
            await send_text(bot, uid, "daily", amount=fmt(bal), rate=rate)
        except Exception as e:
            logging.warning("daily notify failed for %s: %s", uid, e)
        await asyncio.sleep(0.05)


async def accrual_loop(bot: Bot):
    while True:
        try:
            await run_accrual(bot)
        except Exception:
            logging.exception("accrual error")
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
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    task = asyncio.create_task(accrual_loop(bot))
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
