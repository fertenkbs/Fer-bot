# -*- coding: utf-8 -*-
"""Makers Money — Telegram-бот публикации заданий (aiogram 3 + aiosqlite, один файл).

Переменные окружения: BOT_TOKEN, ADMIN_IDS (через запятую), CHANNEL_ID (можно задать и в админке),
необязательно DB_PATH (по умолчанию makersmoney.db).
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
import re
import sys
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, ErrorEvent, InlineKeyboardButton, InlineKeyboardMarkup,
                           InputMediaPhoto, Message)

log = logging.getLogger("makersmoney")
MSK = ZoneInfo("Europe/Moscow")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in re.split(r"[,\s;]+", os.getenv("ADMIN_IDS", "")) if re.fullmatch(r"-?\d+", x or "")}
ENV_CHANNEL = os.getenv("CHANNEL_ID", "").strip()
DB_PATH = os.getenv("DB_PATH", "/data/bot.db")
LEAD = 60  # минимальный запас до слота, сек
BOT: Bot
db: "DB"
cfg: "Cfg"

# ───────────────────────── Значения по умолчанию ─────────────────────────
BANNER_SLOTS = {"main": "Главное меню", "expired": "Истечение подписки", "profile": "Профиль",
                "schedule": "Расписание", "admin": "Админ-панель"}
DEFAULT_SETTINGS = {
    "channel_id": ENV_CHANNEL, "interval_minutes": "20", "autoclose_enabled": "1", "autoclose_minutes": "10",
    "slot_step": "15", "slot_window_hours": "3", "slot_columns": "4", "slot_busy_style": "danger",
    "slot_free_style": "none", "slot_busy_emoji": "", "slot_free_emoji": "", "support_url": "https://t.me/Help_Makersmon",
    "admin_contact_url": "https://t.me/elenesing", "show_username": "1",
    "expired_layout": "below_banner", "expired_order": "title_first", "expired_quote_expandable": "0",
    "missed_grace_minutes": "30",
}
for _b in BANNER_SLOTS:
    DEFAULT_SETTINGS["banner_" + _b] = ""

TV = "{platform} {cena} {opisanie} {username} {user_id} {date} {time} {task_id}"
UV = "{username} {user_id} {expiry_date}"
_TEXTS = [
    ("main_menu", "Главное меню / приветствие", "<b>Makers Money</b>\n\n{username}, добро пожаловать! Выберите действие:", UV),
    ("no_access", "Нет доступа", "{username}, у вас нет доступа к боту.\n\nДоступ можно получить у @elenesing.", UV),
    ("banned", "Аккаунт заблокирован", "{username}, ваш аккаунт заблокирован.", UV),
    ("profile", "Профиль", "<b>Профиль</b>\n\nUsername: {username}\nTelegram ID: <code>{user_id}</code>\nСтатус доступа: {status}\nДоступ выдан: {granted_date}\nДоступ до: {expiry_date}\n\nОпубликовано заданий: {published}\nЗакрыто заданий: {closed}\nЗапланировано заданий: {scheduled}", "{username} {user_id} {status} {granted_date} {expiry_date} {published} {closed} {scheduled}"),
    ("pick_platform", "Выбор платформы", "<b>Выберите платформу:</b>", ""),
    ("ask_platform_name", "Запрос названия платформы", "Введите название платформы:", ""),
    ("ask_price", "Запрос оплаты", "Введите оплату в рублях (число):", ""),
    ("ask_desc", "Запрос описания", "Введите описание и условия задания:", ""),
    ("bad_name", "Ошибка: название", "Название должно содержать от 2 до 40 символов.", ""),
    ("bad_price", "Ошибка: оплата", "Введите положительное число, например: 500", ""),
    ("bad_desc", "Ошибка: описание", "Описание должно содержать от 3 до 1500 символов.", ""),
    ("preview_head", "Заголовок предпросмотра", "<b>Предпросмотр задания</b>", ""),
    ("published_auto", "Опубликовано (автозакрытие)", "Задание опубликовано в канале.\n\nЧерез {minutes} минут оно закроется автоматически", "{minutes}"),
    ("published_plain", "Опубликовано (без автозакрытия)", "Задание опубликовано в канале.", ""),
    ("booked", "Время забронировано", "Время забронировано: <b>{slot} МСК</b>.\n\nЗадание будет опубликовано автоматически.", "{slot}"),
    ("too_early", "Слишком рано (интервал)", "Слишком рано. Между публикациями должно пройти {interval} мин.\n\nБлижайшее свободное время: <b>{next_time} МСК</b>.", "{interval} {next_time}"),
    ("slot_title", "Расписание: заголовок", "<b>Выберите время публикации</b>\n{date}\n<i>Время указано по МСК</i>", "{date}"),
    ("slot_busy", "Расписание: время занято", "Это время уже занято. Ближайшее свободное: {next_time} МСК", "{next_time}"),
    ("slot_none", "Расписание: нет слотов", "Свободных слотов в ближайшее время нет.", ""),
    ("cancelled", "Отмена", "Действие отменено.", ""),
    ("close_list", "Закрытие: список", "<b>Ваши активные задания:</b>", ""),
    ("close_none", "Закрытие: пусто", "У вас нет активных заданий.", ""),
    ("close_confirm", "Закрытие: подтверждение", "Закрыть задание №{task_id}?\n\nПлатформа: {platform}\nЦена: {cena}", TV),
    ("close_done", "Задание закрыто", "Задание №{task_id} закрыто.", "{task_id}"),
    ("cancel_done", "Запланированное отменено", "Запланированное задание №{task_id} отменено.", "{task_id}"),
    ("task_open", "Канал: шаблон задания", "Новое задание!\n\nMakers Money\n\nПлатформа: {platform}\n\nЦена: {cena}\n\nОписание:\n<blockquote>{opisanie}</blockquote>", TV),
    ("task_closed", "Канал: шаблон закрытого задания", "Это задание уже закрыто.\n\nСледующее предложение появится по расписанию.\n\nВключите уведомления канала, чтобы ничего не пропустить", TV),
    ("expired_title", "Подписка истекла: заголовок", "{username}, срок подписки закончился.", UV),
    ("expired_quote", "Подписка истекла: цитата", "Чтобы снова создавать и публиковать задания, продлите доступ у @elenesing.", UV),
    ("n_published", "Уведомление: опубликовано по расписанию", "Запланированное задание №{task_id} опубликовано в канале.\n\nЧерез {minutes} минут оно закроется автоматически.", "{task_id} {minutes}"),
    ("n_closed_auto", "Уведомление: автозакрытие", "Задание №{task_id} закрыто автоматически.", "{task_id}"),
    ("n_closed_admin", "Уведомление: закрыто админом", "Задание №{task_id} закрыто администратором.", "{task_id}"),
    ("n_failed", "Уведомление: ошибка публикации", "Не удалось опубликовать задание №{task_id}. Обратитесь в поддержку.", "{task_id}"),
    ("n_missed", "Уведомление: пропущено", "Задание №{task_id} не опубликовано в срок (бот был недоступен). Забронируйте время заново.", "{task_id}"),
    ("n_granted", "Уведомление: доступ выдан", "Вам выдан доступ.\nДоступ до: {expiry_date}\n\nНажмите /start.", UV),
    ("channel_error", "Канал не настроен", "Канал публикаций не настроен. Обратитесь к администратору.", ""),
    ("admin_menu", "Админ-панель", "<b>Админ-панель Makers Money</b>\n\nВыберите раздел:", ""),
]
TEXT_DEF = {k: (d, l, h) for k, l, d, h in _TEXTS}

GROUP_LABELS = {"main": "Главное меню", "noaccess": "Нет доступа", "expired": "Подписка истекла",
                "platform": "Выбор платформы", "steps": "Этапы заполнения", "preview": "Предпросмотр",
                "profile": "Профиль / навигация", "close": "Закрытие заданий", "slots": "Расписание",
                "channel_open": "Канал: задание", "channel_closed": "Канал: закрытое задание", "admin": "Админ-панель"}
# key, group, text, kind(cb|url), value, row, pos, style, emoji_id
_BTN = [
    ("m_post", "main", "Выложить задание", "cb", "m:slot", 0, 0, "", ""),
    ("m_profile", "main", "Профиль", "cb", "m:profile", 1, 0, "", ""),
    ("m_close", "main", "Закрыть задание", "cb", "m:close", 1, 1, "", ""),
    ("m_admin", "main", "Админ-панель", "cb", "adm:menu", 2, 0, "", ""),
    ("na_contact", "noaccess", "Связаться с администратором", "url", "{admin_contact_url}", 0, 0, "", ""),
    ("ex_buy", "expired", "Оформить подписку", "url", "{admin_contact_url}", 0, 0, "success", ""),
    ("pl_0", "platform", "2ГИС", "cb", "pl:0", 0, 0, "", ""),
    ("pl_1", "platform", "Яндекс Карты", "cb", "pl:1", 0, 1, "", ""),
    ("pl_2", "platform", "Google Карты", "cb", "pl:2", 1, 0, "", ""),
    ("pl_3", "platform", "Яндекс Браузер", "cb", "pl:3", 1, 1, "", ""),
    ("pl_4", "platform", "Другое...", "cb", "pl:4", 2, 0, "", ""),
    ("pl_back", "platform", "Назад", "cb", "nav:back", 3, 0, "", ""),
    ("pl_cancel", "platform", "Отменить", "cb", "nav:cancel", 3, 1, "danger", ""),
    ("st_back", "steps", "Назад", "cb", "nav:back", 0, 0, "", ""),
    ("st_cancel", "steps", "Отменить", "cb", "nav:cancel", 0, 1, "danger", ""),
    ("pv_publish", "preview", "Выложить задание", "cb", "pv:ok", 0, 0, "success", ""),
    ("pv_book", "preview", "Выложить задание", "cb", "pv:ok", 0, 1, "success", ""),
    ("pv_back", "preview", "Назад", "cb", "nav:back", 1, 0, "", ""),
    ("pv_cancel", "preview", "Отменить", "cb", "nav:cancel", 1, 1, "danger", ""),
    ("pr_back", "profile", "В меню", "cb", "m:menu", 0, 0, "", ""),
    ("cl_back", "close", "В меню", "cb", "m:menu", 0, 0, "", ""),
    ("cl_yes", "close", "Да, закрыть", "cb", "cly:{task_id}", 0, 0, "danger", ""),
    ("cl_no", "close", "Назад", "cb", "m:close", 0, 1, "", ""),
    ("s_cancel", "slots", "Отменить", "cb", "nav:cancel", 0, 0, "", ""),
    ("ch_support", "channel_open", "Поддержка", "url", "{support_url}", 0, 0, "", ""),
    ("ch_take", "channel_open", "Взять задание", "url", "{poster_url}", 0, 1, "", ""),
    ("ch_learn", "channel_open", "Обучение", "url", "https://t.me/Lych_obychenie", 1, 0, "", ""),
    ("chc_support", "channel_closed", "Поддержка", "url", "{support_url}", 0, 0, "", ""),
    ("ad_users", "admin", "Пользователи", "cb", "adm:us:0", 0, 0, "", ""),
    ("ad_grant", "admin", "Выдать подписку", "cb", "adm:grant", 0, 1, "", ""),
    ("ad_extend", "admin", "Продлить подписку", "cb", "adm:extend", 1, 0, "", ""),
    ("ad_ban", "admin", "Блокировка", "cb", "adm:ban", 1, 1, "", ""),
    ("ad_channel", "admin", "Канал публикаций", "cb", "adm:chan", 2, 0, "", ""),
    ("ad_templates", "admin", "Шаблоны и тексты", "cb", "adm:txl:0", 2, 1, "", ""),
    ("ad_buttons", "admin", "Настройки кнопок", "cb", "adm:bg", 3, 0, "", ""),
    ("ad_emoji", "admin", "Premium Emoji", "cb", "adm:em", 3, 1, "", ""),
    ("ad_banners", "admin", "Баннеры", "cb", "adm:bn", 4, 0, "", ""),
    ("ad_schedule", "admin", "Расписание", "cb", "adm:sec:sch", 4, 1, "", ""),
    ("ad_interval", "admin", "Интервалы", "cb", "adm:sec:int", 5, 0, "", ""),
    ("ad_autoclose", "admin", "Автозакрытие", "cb", "adm:sec:ac", 5, 1, "", ""),
    ("ad_stats", "admin", "Статистика", "cb", "adm:stat", 6, 0, "", ""),
    ("ad_access", "admin", "Настройки доступа", "cb", "adm:sec:acc", 6, 1, "", ""),
    ("ad_exit", "admin", "В меню бота", "cb", "m:menu", 7, 0, "", ""),
    ("ad_back", "admin", "Админ-панель", "cb", "adm:menu", 8, 0, "", ""),
]
BTN_DEF = {k: dict(key=k, grp=g, text=t, kind=kd, value=v, row=r, pos=p, style=s, emoji_id=e, enabled=1)
           for k, g, t, kd, v, r, p, s, e in _BTN}
STYLES = ("primary", "success", "danger")

SETTING_META = {  # key: (подпись, тип, параметры)
    "interval_minutes": ("Интервал между публикациями, мин", "int", (0, 1440)),
    "autoclose_enabled": ("Автозакрытие", "bool", None),
    "autoclose_minutes": ("Закрывать через, мин", "int", (1, 10080)),
    "slot_step": ("Шаг слотов, мин", "enum", ["5", "10", "15", "20", "30", "60"]),
    "slot_window_hours": ("Окно расписания, ч", "int", (1, 12)),
    "slot_columns": ("Кнопок в строке", "int", (1, 6)),
    "slot_busy_style": ("Стиль занятых слотов", "enum", ["danger", "primary", "success", "none"]),
    "slot_free_style": ("Стиль свободных слотов", "enum", ["none", "primary", "success", "danger"]),
    "slot_busy_emoji": ("Premium Emoji занятых слотов (ID)", "emoji", None),
    "slot_free_emoji": ("Premium Emoji свободных слотов (ID)", "emoji", None),
    "missed_grace_minutes": ("Допуск опоздания публикации, мин", "int", (1, 1440)),
    "show_username": ("Показывать username в текстах", "bool", None),
    "admin_contact_url": ("Ссылка «связаться с админом»", "url", None),
    "support_url": ("Ссылка поддержки", "url", None),
    "expired_layout": ("Экран окончания: текст", "enum", ["below_banner", "above_banner"]),
    "expired_order": ("Экран окончания: порядок", "enum", ["title_first", "quote_first"]),
    "expired_quote_expandable": ("Сворачиваемая цитата", "bool", None),
}
SECTIONS = {
    "int": ("Интервалы публикаций", ["interval_minutes", "missed_grace_minutes"],
            "Глобальный интервал между публикациями всех пользователей."),
    "ac": ("Автозакрытие", ["autoclose_enabled", "autoclose_minutes"],
           "Новое время применяется к заданиям, опубликованным после изменения."),
    "sch": ("Расписание", ["slot_step", "slot_window_hours", "slot_columns", "slot_busy_style", "slot_free_style", "slot_busy_emoji", "slot_free_emoji"],
            "Настройки сетки слотов (время по МСК)."),
    "acc": ("Настройки доступа", ["show_username", "admin_contact_url", "support_url", "expired_layout",
                                     "expired_order", "expired_quote_expandable"],
            "Тексты и кнопки — в разделах «Шаблоны» и «Настройки кнопок»."),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
 has_access INTEGER NOT NULL DEFAULT 0, granted_at INTEGER, expires_at INTEGER, banned INTEGER NOT NULL DEFAULT 0,
 expiry_notified INTEGER NOT NULL DEFAULT 0, created_at INTEGER);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, poster_name TEXT,
 platform TEXT, price TEXT, description TEXT, status TEXT, publish_at INTEGER, published_at INTEGER, close_at INTEGER,
 closed_at INTEGER, closed_by INTEGER, chat_id TEXT, message_id INTEGER, channel_closed INTEGER NOT NULL DEFAULT 0,
 close_tries INTEGER NOT NULL DEFAULT 0, created_at INTEGER);
CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status, publish_at);
CREATE INDEX IF NOT EXISTS ix_tasks_user ON tasks(user_id, status);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS texts(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS buttons(key TEXT PRIMARY KEY, text TEXT, kind TEXT, value TEXT, brow INTEGER, bpos INTEGER,
 style TEXT, emoji_id TEXT, enabled INTEGER);
CREATE TABLE IF NOT EXISTS emojis(name TEXT PRIMARY KEY, emoji_id TEXT, fallback TEXT);
"""


# ───────────────────────── Утилиты ─────────────────────────
def now_ts() -> int:
    return int(time.time())


def to_msk(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, MSK)


def fmt_dt(ts) -> str:
    return to_msk(ts).strftime("%d.%m.%Y %H:%M") if ts else "—"


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def strip_custom(s: str) -> str:
    return re.sub(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', r"\1", s, flags=re.S)


EMO_ID = re.compile(r"\{emoji:(\d{5,32})(?::([^{}<>]{1,8}))?\}")
EMO_NAME = re.compile(r"\{e:([A-Za-z0-9_]{1,32})\}")
VAR = re.compile(r"\{([a-z_]+)\}")


def tg_emoji(eid, fb="⭐") -> str:
    return '<tg-emoji emoji-id="%s">%s</tg-emoji>' % (eid, fb)


def render(tpl: str, v: dict | None = None) -> str:
    """Подставляет {emoji:ID}, {e:имя} и переменные (значения экранируются)."""
    v = v or {}
    s = EMO_ID.sub(lambda m: tg_emoji(m[1], m[2] or "⭐"), tpl)
    s = EMO_NAME.sub(lambda m: tg_emoji(cfg.emojis[m[1]]["emoji_id"], cfg.emojis[m[1]]["fallback"])
                     if m[1] in cfg.emojis else m[0], s)
    return VAR.sub(lambda m: esc(v[m[1]]) if m[1] in v else m[0], s)


def T(key: str) -> str:
    return cfg.text(key)


def R(key: str, v: dict | None = None) -> str:
    return render(T(key), v)


def fill(s: str, ctx: dict) -> str:
    for k, v in ctx.items():
        s = s.replace("{%s}" % k, str(v))
    return s


def valid_url(u: str) -> bool:
    return bool(re.fullmatch(r"(https?://|tg://)\S+", u or ""))


def disp_name(u: dict) -> str:
    return "@" + u["username"] if u.get("username") else (u.get("first_name") or str(u["user_id"]))


def fmt_price(s: str) -> str:
    v = float(s)
    return ("%d" % v) if v == int(v) else ("%.2f" % v)


# ───────────────────────── БД и конфиг ─────────────────────────
class DB:
    def __init__(self, path: str):
        self.path, self.conn, self.lock = path, None, asyncio.Lock()

    async def init(self):
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript("PRAGMA journal_mode=WAL;PRAGMA foreign_keys=ON;" + SCHEMA)
        await self.conn.commit()

    async def close(self):
        if self.conn:
            await self.conn.close()

    async def one(self, sql, args=()):
        async with self.conn.execute(sql, args) as cur:
            r = await cur.fetchone()
        return dict(r) if r else None

    async def all(self, sql, args=()):
        async with self.conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def run(self, sql, args=()) -> int:
        async with self.lock:
            cur = await self.conn.execute(sql, args)
            await self.conn.commit()
            return cur.rowcount

    @asynccontextmanager
    async def tx(self):
        async with self.lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                await self.conn.rollback()
                raise
            else:
                await self.conn.commit()


class Cfg:
    def __init__(self):
        self.settings, self.texts, self.buttons, self.emojis = {}, {}, {}, {}

    async def load(self):
        self.settings = {r["key"]: r["value"] for r in await db.all("SELECT * FROM settings")}
        self.texts = {r["key"]: r["value"] for r in await db.all("SELECT * FROM texts")}
        self.buttons = {r["key"]: r for r in await db.all("SELECT * FROM buttons")}
        self.emojis = {r["name"]: r for r in await db.all("SELECT * FROM emojis")}

    def s(self, k):
        return self.settings.get(k, DEFAULT_SETTINGS.get(k, ""))

    def i(self, k):
        try:
            return int(self.s(k))
        except ValueError:
            return int(DEFAULT_SETTINGS[k])

    def text(self, k):
        return self.texts.get(k, TEXT_DEF[k][0])

    def btn(self, k):
        d = dict(BTN_DEF[k])
        o = self.buttons.get(k)
        if o:
            d.update(text=o["text"], kind=o["kind"], value=o["value"], row=o["brow"], pos=o["bpos"],
                     style=o["style"] or "", emoji_id=o["emoji_id"] or "", enabled=o["enabled"])
        return d

    async def set_s(self, k, v):
        await db.run("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        self.settings[k] = v

    async def set_text(self, k, v):
        await db.run("INSERT INTO texts(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        self.texts[k] = v

    async def reset_text(self, k):
        await db.run("DELETE FROM texts WHERE key=?", (k,))
        self.texts.pop(k, None)

    async def save_btn(self, k, b):
        await db.run("INSERT INTO buttons(key,text,kind,value,brow,bpos,style,emoji_id,enabled) VALUES(?,?,?,?,?,?,?,?,?) "
                     "ON CONFLICT(key) DO UPDATE SET text=excluded.text,kind=excluded.kind,value=excluded.value,"
                     "brow=excluded.brow,bpos=excluded.bpos,style=excluded.style,emoji_id=excluded.emoji_id,enabled=excluded.enabled",
                     (k, b["text"], b["kind"], b["value"], b["row"], b["pos"], b["style"], b["emoji_id"], int(b["enabled"])))
        self.buttons[k] = dict(key=k, text=b["text"], kind=b["kind"], value=b["value"], brow=b["row"], bpos=b["pos"],
                               style=b["style"], emoji_id=b["emoji_id"], enabled=int(b["enabled"]))

    async def reset_btn(self, k):
        await db.run("DELETE FROM buttons WHERE key=?", (k,))
        self.buttons.pop(k, None)


# ───────────────────────── Клавиатуры и отправка ─────────────────────────
def base_ctx() -> dict:
    return {"support_url": cfg.s("support_url"), "admin_contact_url": cfg.s("admin_contact_url")}


def btn_spec(key, ctx=None, b=None, preview=False):
    b = b or cfg.btn(key)
    if not b["enabled"] and b["grp"] != "admin" and not preview:
        return None
    c = {**base_ctx(), **(ctx or {})}
    d = {"text": b["text"][:64] or "•"}
    if b["kind"] == "url":
        u = fill(b["value"], c)
        if not valid_url(u):
            return None
        d["url"] = u
    else:
        d["callback_data"] = "adm:noop" if preview else fill(b["value"], c)[:64]
    if b["style"] in STYLES:
        d["style"] = b["style"]
    if b["emoji_id"]:
        d["icon_custom_emoji_id"] = b["emoji_id"]
    return d


def build_kb(group, ctx=None, only=None, skip=(), top=None, bottom=None):
    ctx = ctx or {}
    rows: dict = {}
    for k, dflt in BTN_DEF.items():
        if dflt["grp"] != group or (only and k not in only) or k in skip:
            continue
        if k == "m_admin" and not ctx.get("is_admin"):
            continue
        b = cfg.btn(k)
        s = btn_spec(k, ctx, b)
        if s:
            rows.setdefault(b["row"], []).append((b["pos"], s))
    out = [[s for _, s in sorted(items, key=lambda x: x[0])] for _, items in sorted(rows.items())]
    return (top or []) + out + (bottom or [])


def strip_spec(spec):
    return [[{k: v for k, v in d.items() if k != "icon_custom_emoji_id"} for d in row] for row in (spec or [])]


def markup(spec):
    rows = [[InlineKeyboardButton(**{k: v for k, v in d.items() if v}) for d in row] for row in (spec or []) if row]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def uses_custom(text, spec) -> bool:
    return "tg-emoji" in (text or "") or any("icon_custom_emoji_id" in d for r in (spec or []) for d in r)


_warned: dict = {}


async def warn_admins(text: str):
    h = hashlib.md5(text.encode()).hexdigest()
    if time.time() - _warned.get(h, 0) < 600:
        return
    _warned[h] = time.time()
    log.warning(text)
    for a in ADMIN_IDS:
        with suppress(Exception):
            await BOT.send_message(a, "" + esc(text)[:3500])


async def attempt(fn, text, spec, strict=False):
    """Вызов Telegram с обработкой flood-wait и отклонённых Premium Emoji (с предупреждением админам)."""
    for _ in range(3):
        try:
            try:
                return await fn(text, markup(spec))
            except TelegramBadRequest as e:
                s = str(e).lower()
                if strict or not uses_custom(text, spec) or not any(w in s for w in ("emoji", "entit", "icon", "button")):
                    raise
                await warn_admins("Telegram отклонил Premium Emoji / иконку кнопки: %s. Отправлено без них." % e.message)
                return await fn(strip_custom(text), markup(strip_spec(spec)))
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
    raise RuntimeError("Слишком много попыток Telegram API")


async def send_new(chat_id, text, spec=None, banner="", above=False, strict=False):
    if banner and len(strip_tags(text)) <= 1000:
        try:
            return await attempt(lambda t, m: BOT.send_photo(chat_id, banner, caption=t, reply_markup=m,
                                                             show_caption_above_media=True if above else None),
                                 text, spec, strict)
        except TelegramBadRequest as e:
            if strict and uses_custom(text, spec):
                raise
            log.warning("Баннер не отправлен: %s", e)
    return await attempt(lambda t, m: BOT.send_message(chat_id, t, reply_markup=m), text, spec, strict)


async def screen(ev, text, spec=None, banner_key=None, above=False, force_new=False, strict=False):
    """Показывает экран: редактирует текущее сообщение, а при смене типа (фото/текст) пересоздаёт его."""
    banner = cfg.s(banner_key) if banner_key else ""
    if banner and len(strip_tags(text)) > 1000:
        banner = ""
    msg = ev.message if isinstance(ev, CallbackQuery) else None
    if isinstance(msg, Message) and not force_new:
        try:
            if banner and msg.photo and not above:
                await attempt(lambda t, m: msg.edit_media(InputMediaPhoto(media=banner, caption=t, parse_mode="HTML"),
                                                          reply_markup=m), text, spec, strict)
                return msg
            if not banner and not msg.photo:
                await attempt(lambda t, m: msg.edit_text(t, reply_markup=m), text, spec, strict)
                return msg
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return msg
            if strict:
                raise
            log.info("Редактирование не удалось (%s), отправляю новое сообщение", e)
        with suppress(TelegramAPIError):
            await msg.delete()
    return await send_new(ev.from_user.id, text, spec, banner, above, strict)


async def clear_prev(chat_id, mid):
    if mid:
        with suppress(TelegramAPIError):
            await BOT.edit_message_reply_markup(chat_id=chat_id, message_id=mid, reply_markup=None)


# ───────────────────────── Пользователи и доступ ─────────────────────────
async def get_user(uid):
    return await db.one("SELECT * FROM users WHERE user_id=?", (uid,))


async def upsert_user(tg) -> dict:
    await db.run("INSERT INTO users(user_id,username,first_name,created_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE "
                 "SET username=excluded.username, first_name=excluded.first_name", (tg.id, tg.username, tg.first_name, now_ts()))
    return await get_user(tg.id)


def access_state(u: dict) -> str:
    if u["user_id"] in ADMIN_IDS:
        return "ok"
    if u["banned"]:
        return "banned"
    n = now_ts()
    if u["has_access"] and (u["expires_at"] is None or u["expires_at"] > n):
        return "ok"
    if u["expires_at"] is not None and u["expires_at"] <= n and u["granted_at"]:
        return "expired"
    return "none"


def uvars(u: dict) -> dict:
    exp = u.get("expires_at")
    st = access_state(u)
    if u["user_id"] in ADMIN_IDS or (exp is None and u.get("has_access")):
        exp_s = "бессрочно"
    else:
        exp_s = fmt_dt(exp) + " МСК" if exp else "—"
    status = {"ok": "Активен", "expired": "Истёк", "banned": "Заблокирован", "none": "Нет доступа"}[st]
    if u["user_id"] in ADMIN_IDS:
        status = "Администратор"
    return {"username": disp_name(u) if cfg.s("show_username") == "1" else "пользователь", "user_id": u["user_id"],
            "expiry_date": exp_s, "status": status, "granted_date": fmt_dt(u.get("granted_at"))}


async def counts(uid):
    r = await db.one("SELECT COALESCE(SUM(published_at IS NOT NULL),0) p, COALESCE(SUM(status='closed'),0) c, "
                     "COALESCE(SUM(status IN ('scheduled','publishing')),0) s FROM tasks WHERE user_id=?", (uid,))
    return r["p"], r["c"], r["s"]


async def grant(uid: int, days: int, extend=False):
    n = now_ts()
    async with db.tx() as c:
        await c.execute("INSERT OR IGNORE INTO users(user_id,created_at) VALUES(?,?)", (uid, n))
        async with c.execute("SELECT * FROM users WHERE user_id=?", (uid,)) as cur:
            u = dict(await cur.fetchone())
        active = u["has_access"] and (u["expires_at"] is None or u["expires_at"] > n)
        if days == 0 or (extend and active and u["expires_at"] is None):
            exp = None
        else:
            base = u["expires_at"] if (extend and active and u["expires_at"]) else n
            exp = base + days * 86400
        granted = u["granted_at"] if (active and u["granted_at"]) else n
        await c.execute("UPDATE users SET has_access=1, granted_at=?, expires_at=?, expiry_notified=0 WHERE user_id=?",
                        (granted, exp, uid))
    return exp


async def mark_expired(u) -> bool:
    return await db.run("UPDATE users SET has_access=0, expiry_notified=1 WHERE user_id=? AND has_access=1 AND expires_at<=?",
                        (u["user_id"], now_ts())) == 1


async def show_expired(u, ev=None):
    v = uvars(u)
    quote = R("expired_quote", v)
    q = "<blockquote%s>%s</blockquote>" % (" expandable" if cfg.s("expired_quote_expandable") == "1" else "", quote)
    parts = [R("expired_title", v), q] if cfg.s("expired_order") == "title_first" else [q, R("expired_title", v)]
    text = "\n\n".join(parts)
    spec = build_kb("expired", {"user_id": u["user_id"], "username": u.get("username") or ""})
    above = cfg.s("expired_layout") == "above_banner"
    if ev is None:
        await send_new(u["user_id"], text, spec, cfg.s("banner_expired"), above)
    else:
        await screen(ev, text, spec, "banner_expired", above=above)


async def block_screen(ev, u, st):
    if st == "expired":
        await mark_expired(u)
        await show_expired(u, ev)
    else:
        await screen(ev, R("banned" if st == "banned" else "no_access", uvars(u)), build_kb("noaccess"), "banner_main")


# ───────────────────────── Задания: логика ─────────────────────────
HOLDS: dict = {}  # slot_ts -> (user_id, expire_monotonic)


def hold(uid, ts):
    for k in [k for k, v in HOLDS.items() if v[0] == uid]:
        del HOLDS[k]
    HOLDS[ts] = (uid, time.monotonic() + 600)


def release(uid):
    for k in [k for k, v in HOLDS.items() if v[0] == uid]:
        del HOLDS[k]


async def busy_times(now, interval, exclude_uid=None, conn=None):
    sql = ("SELECT COALESCE(published_at, publish_at) AS t FROM tasks WHERE status NOT IN ('cancelled','failed','missed') "
           "AND COALESCE(published_at, publish_at) > ?")
    lim = now - max(interval, 60) - 5
    if conn:
        async with conn.execute(sql, (lim,)) as cur:
            rows = [r[0] for r in await cur.fetchall()]
    else:
        rows = [r["t"] for r in await db.all(sql, (lim,))]
    for t, (uid, exp) in list(HOLDS.items()):
        if exp < time.monotonic():
            del HOLDS[t]
        elif uid != exclude_uid:
            rows.append(t)
    return rows


def is_busy(ts, times, interval) -> bool:
    gap = max(interval, 60)
    return any(abs(ts - t) < gap for t in times)


def slot_grid(now):
    step = cfg.i("slot_step") * 60
    first = -(-(now + LEAD) // step) * step
    return [first + i * step for i in range(max(1, cfg.i("slot_window_hours") * 3600 // step))]


def next_free(now, times, interval):
    step = cfg.i("slot_step") * 60
    first = -(-(now + LEAD) // step) * step
    for i in range(48 * 3600 // step):
        s = first + i * step
        if not is_busy(s, times, interval):
            return s
    return None


def task_vars(t: dict, ts=None) -> dict:
    ts = ts or t.get("published_at") or t.get("publish_at") or now_ts()
    return {"platform": t["platform"], "cena": "%s ₽" % fmt_price(t["price"]), "opisanie": t["description"],
            "username": t.get("poster_name") or t.get("username") or t["user_id"], "user_id": t["user_id"],
            "date": to_msk(ts).strftime("%d.%m.%Y"), "time": to_msk(ts).strftime("%H:%M"),
            "task_id": t.get("id", "—"), "minutes": cfg.i("autoclose_minutes"), "interval": cfg.i("interval_minutes")}


def poster_url(t) -> str:
    return "https://t.me/%s" % t["username"] if t.get("username") else "tg://user?id=%s" % t["user_id"]


async def reserve(u, d):
    """Атомарно проверяет интервал/слот и создаёт задание. Возвращает (status, value)."""
    uid, interval, now = u["user_id"], cfg.i("interval_minutes") * 60, now_ts()
    mode = d["mode"]
    async with db.tx() as c:
        times = await busy_times(now, interval, uid, c)
        if mode == "now":
            ts, status = now, "publishing"
        else:
            ts, status = d["slot"], "scheduled"
            step = cfg.i("slot_step") * 60
            if ts % step or ts < now + LEAD - 5 or ts > now + cfg.i("slot_window_hours") * 3600 + step:
                return "busy", next_free(now, times, interval)
        if is_busy(ts, times, interval):
            return "busy", next_free(now, times, interval)
        cur = await c.execute(
            "INSERT INTO tasks(user_id,username,poster_name,platform,price,description,status,publish_at,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (uid, u.get("username"), disp_name(u), d["platform"], d["price"], d["desc"], status, ts, now))
        tid = cur.lastrowid
    release(uid)
    return "ok", tid


async def publish_task(tid) -> bool:
    t = await db.one("SELECT * FROM tasks WHERE id=?", (tid,))
    if not t or t["status"] != "publishing":
        return False
    chan, now = cfg.s("channel_id"), now_ts()
    text = render(T("task_open"), task_vars(t, now))
    ctx = {"poster_url": poster_url(t)}
    try:
        try:
            msg = await attempt(lambda tx, m: BOT.send_message(chan, tx, reply_markup=m), text, build_kb("channel_open", ctx))
        except TelegramBadRequest as e:
            if "privacy" not in str(e).lower():
                raise
            await warn_admins("Кнопка «Взять задание» отклонена (приватность пользователя %s). Опубликовано без неё." % t["user_id"])
            msg = await attempt(lambda tx, m: BOT.send_message(chan, tx, reply_markup=m), text,
                                build_kb("channel_open", {"poster_url": ""}))
    except Exception as e:
        log.exception("Публикация задания %s не удалась", tid)
        await db.run("UPDATE tasks SET status='failed' WHERE id=? AND status='publishing'", (tid,))
        await warn_admins("Не удалось опубликовать задание №%s: %s" % (tid, e))
        return False
    close_at = now + cfg.i("autoclose_minutes") * 60 if cfg.s("autoclose_enabled") == "1" else None
    await db.run("UPDATE tasks SET status='published', published_at=?, close_at=?, chat_id=?, message_id=? WHERE id=?",
                 (now, close_at, str(msg.chat.id), msg.message_id, tid))
    return True


LAST_ERR: dict = {}


async def finish_channel_close(tid):
    t = await db.one("SELECT * FROM tasks WHERE id=?", (tid,))
    if not t or t["channel_closed"] or not t["message_id"]:
        return
    text, spec = render(T("task_closed"), task_vars(t)), build_kb("channel_closed")
    done = False
    LAST_ERR.pop(tid, None)
    try:
        await attempt(lambda tx, m: BOT.edit_message_text(tx, chat_id=t["chat_id"], message_id=t["message_id"],
                                                          reply_markup=m), text, spec)
        done = True
    except TelegramBadRequest as e:
        done = any(w in str(e).lower() for w in ("not modified", "not found"))
        if not done:
            LAST_ERR[tid] = e.message
            log.error("Закрытие поста задания %s: %s", tid, e)
            if t["close_tries"] == 0:
                await warn_admins("Не удалось изменить пост задания №%s в канале %s: %s" % (tid, t["chat_id"], e.message))
    except Exception as e:
        LAST_ERR[tid] = str(e)
        log.exception("Закрытие поста задания %s", tid)
    if done:
        await db.run("UPDATE tasks SET channel_closed=1 WHERE id=?", (tid,))
    else:
        await db.run("UPDATE tasks SET close_tries=close_tries+1 WHERE id=?", (tid,))
        if t["close_tries"] + 1 == 5:
            await warn_admins("Не удалось закрыть пост задания №%s в канале (5 попыток). Проверьте права бота." % tid)


async def close_task(tid, by=None) -> bool:
    n = await db.run("UPDATE tasks SET status='closed', closed_at=?, closed_by=? WHERE id=? AND status='published'",
                     (now_ts(), by, tid))
    if n != 1:
        return False
    await finish_channel_close(tid)
    return True


async def cancel_task(tid) -> bool:
    return await db.run("UPDATE tasks SET status='cancelled' WHERE id=? AND status='scheduled'", (tid,)) == 1


async def notify_user(uid, key, t, extra=None):
    with suppress(Exception):
        await send_new(uid, R(key, {**task_vars(t), **(extra or {})}), build_kb("profile"))


async def scheduler():
    log.info("Планировщик запущен")
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка планировщика")
        await asyncio.sleep(3)


async def tick():
    now = now_ts()
    grace = cfg.i("missed_grace_minutes") * 60
    for r in await db.all("SELECT id FROM tasks WHERE status='scheduled' AND publish_at<=? ORDER BY publish_at", (now,)):
        if await db.run("UPDATE tasks SET status='publishing' WHERE id=? AND status='scheduled'", (r["id"],)) != 1:
            continue
        t = await db.one("SELECT * FROM tasks WHERE id=?", (r["id"],))
        if now - t["publish_at"] > grace:
            await db.run("UPDATE tasks SET status='missed' WHERE id=?", (t["id"],))
            await notify_user(t["user_id"], "n_missed", t)
        elif await publish_task(t["id"]):
            await notify_user(t["user_id"], "n_published", t)
        else:
            await notify_user(t["user_id"], "n_failed", t)
    if cfg.s("autoclose_enabled") == "1":
        for r in await db.all("SELECT id FROM tasks WHERE status='published' AND close_at IS NOT NULL AND close_at<=?", (now,)):
            if await close_task(r["id"], None):
                t = await db.one("SELECT * FROM tasks WHERE id=?", (r["id"],))
                await notify_user(t["user_id"], "n_closed_auto", t)
    for r in await db.all("SELECT id FROM tasks WHERE status='closed' AND channel_closed=0 AND message_id IS NOT NULL "
                          "AND close_tries<5 AND closed_at <= ?", (now - 10,)):
        await finish_channel_close(r["id"])
    for u in await db.all("SELECT * FROM users WHERE has_access=1 AND banned=0 AND expires_at IS NOT NULL AND expires_at<=?", (now,)):
        if u["user_id"] in ADMIN_IDS:
            continue
        if await mark_expired(u):
            with suppress(Exception):
                await show_expired(u)


# ───────────────────────── Middleware ─────────────────────────
class BaseMW(BaseMiddleware):
    def __init__(self):
        self.locks: dict = {}

    async def __call__(self, handler, ev, data):
        tg = ev.from_user
        chat = ev.chat if isinstance(ev, Message) else (ev.message.chat if getattr(ev, "message", None) else None)
        if tg is None or tg.is_bot or (chat is not None and chat.type != "private"):
            return None
        data["u"] = await upsert_user(tg)
        if isinstance(ev, CallbackQuery):
            if len(self.locks) > 5000:
                self.locks.clear()
            lock = self.locks.setdefault(tg.id, asyncio.Lock())
            if lock.locked():  # защита от повторных нажатий
                with suppress(TelegramAPIError):
                    await ev.answer("Подождите…")
                return None
            async with lock:
                try:
                    return await handler(ev, data)
                finally:
                    with suppress(TelegramAPIError):
                        await ev.answer()
        return await handler(ev, data)


class AccessMW(BaseMiddleware):
    async def __call__(self, handler, ev, data):
        u = await get_user(data["u"]["user_id"])
        data["u"] = u
        st = access_state(u)
        if st == "ok":
            return await handler(ev, data)
        if data.get("state"):
            await data["state"].clear()
        release(u["user_id"])
        await block_screen(ev, u, st)
        return None


# ───────────────────────── Пользовательские хендлеры ─────────────────────────
class Create(StatesGroup):
    platform = State()
    pname = State()
    price = State()
    desc = State()
    preview = State()


CREATE_STATES = [Create.platform, Create.pname, Create.price, Create.desc, Create.preview]
ur = Router(name="user")


async def show_main(ev, u):
    spec = build_kb("main", {"is_admin": u["user_id"] in ADMIN_IDS})
    await screen(ev, R("main_menu", uvars(u)), spec, "banner_main")


async def ask(ev, state, step, u):
    d = await state.get_data()
    if isinstance(ev, Message):
        await clear_prev(ev.chat.id, d.get("pm"))
    await state.set_state(getattr(Create, step))
    if step == "platform":
        text, spec = R("pick_platform"), build_kb("platform")
    elif step == "pname":
        text, spec = R("ask_platform_name"), build_kb("steps")
    elif step == "price":
        text, spec = R("ask_price"), build_kb("steps")
    elif step == "desc":
        text, spec = R("ask_desc"), build_kb("steps")
    else:
        slot = d.get("slot")
        t = {"id": "—", "platform": d["platform"], "price": d["price"], "description": d["desc"], "username": u.get("username"),
             "poster_name": disp_name(u), "user_id": u["user_id"], "publish_at": slot if d["mode"] == "slot" else now_ts()}
        text = R("preview_head") + "\n\n" + render(T("task_open"), task_vars(t))
        if d["mode"] == "slot":
            text += "\n\nПубликация: <b>%s МСК</b>" % fmt_dt(slot)
        spec = build_kb("preview", skip=["pv_publish" if d["mode"] == "slot" else "pv_book"])
    msg = await screen(ev, text, spec)
    await state.update_data(pm=msg.message_id)


async def too_early(ev, u, nxt):
    v = {"interval": cfg.i("interval_minutes"), "next_time": fmt_dt(nxt) if nxt else "—"}
    await screen(ev, R("too_early", v), build_kb("main", only=["m_post"]) + build_kb("profile"))


async def interval_ok_now(uid):
    interval, now = cfg.i("interval_minutes") * 60, now_ts()
    times = await busy_times(now, interval, uid)
    if not is_busy(now, times, interval):
        return True, None
    return False, next_free(now, times, interval)


@ur.message(CommandStart())
async def h_start(m: Message, state: FSMContext, u):
    await state.clear()
    release(u["user_id"])
    await show_main(m, u)


@ur.callback_query(F.data == "m:menu")
async def h_menu(cb: CallbackQuery, state: FSMContext, u):
    await state.clear()
    release(u["user_id"])
    await show_main(cb, u)


@ur.callback_query(F.data == "nav:cancel")
async def h_cancel(cb: CallbackQuery, state: FSMContext, u):
    await state.clear()
    release(u["user_id"])
    with suppress(TelegramAPIError):
        await cb.answer(strip_tags(R("cancelled")))
    await show_main(cb, u)


@ur.callback_query(F.data == "m:profile")
async def h_profile(cb: CallbackQuery, state: FSMContext, u):
    await state.clear()
    v = uvars(u)
    v["published"], v["closed"], v["scheduled"] = await counts(u["user_id"])
    await screen(cb, R("profile", v), build_kb("profile"), "banner_profile")


async def show_slots(ev, uid):
    now, interval = now_ts(), cfg.i("interval_minutes") * 60
    slots = slot_grid(now)
    times = await busy_times(now, interval, uid)
    busy_style, free_style = cfg.s("slot_busy_style"), cfg.s("slot_free_style")
    btns = []
    for s in slots:
        b = {"text": to_msk(s).strftime("%H:%M"), "callback_data": "sl:%d" % s}
        busy = is_busy(s, times, interval)
        st = busy_style if busy else free_style
        if st in STYLES:
            b["style"] = st
        eid = cfg.s("slot_busy_emoji" if busy else "slot_free_emoji")
        if eid:
            b["icon_custom_emoji_id"] = eid
        btns.append(b)
    n = max(1, cfg.i("slot_columns"))
    d1, d2 = to_msk(slots[0]).strftime("%d.%m.%Y"), to_msk(slots[-1]).strftime("%d.%m.%Y")
    text = R("slot_title", {"date": d1 if d1 == d2 else d1 + " – " + d2})
    if all(is_busy(s, times, interval) for s in slots):
        text += "\n\n" + R("slot_none")
    await screen(ev, text, [btns[i:i + n] for i in range(0, len(btns), n)] + build_kb("slots"), "banner_schedule")


@ur.callback_query(F.data.in_({"m:slot", "m:post"}))
async def h_slots(cb: CallbackQuery, state: FSMContext, u):
    await state.clear()
    release(u["user_id"])
    if not cfg.s("channel_id"):
        return await screen(cb, R("channel_error"), build_kb("profile"))
    await show_slots(cb, u["user_id"])


@ur.callback_query(F.data.regexp(r"^sl:\d+$"))
async def h_slot_pick(cb: CallbackQuery, state: FSMContext, u):
    ts, now, interval = int(cb.data[3:]), now_ts(), cfg.i("interval_minutes") * 60
    step = cfg.i("slot_step") * 60
    times = await busy_times(now, interval, u["user_id"])
    valid = ts % step == 0 and now + LEAD - 5 <= ts <= now + cfg.i("slot_window_hours") * 3600 + step
    if not valid or is_busy(ts, times, interval):
        nxt = next_free(now, times, interval)
        with suppress(TelegramAPIError):
            await cb.answer(strip_tags(R("slot_busy", {"next_time": fmt_dt(nxt) if nxt else "—"}))[:190], show_alert=True)
        return await show_slots(cb, u["user_id"])
    hold(u["user_id"], ts)
    await state.set_data({"mode": "slot", "slot": ts})
    await ask(cb, state, "platform", u)


@ur.callback_query(F.data.regexp(r"^pl:[0-4]$"), StateFilter(Create.platform))
async def h_platform(cb: CallbackQuery, state: FSMContext, u):
    idx = int(cb.data[3:])
    if idx == 4:
        return await ask(cb, state, "pname", u)
    await state.update_data(platform=cfg.btn("pl_%d" % idx)["text"][:50])
    await ask(cb, state, "price", u)


@ur.message(Create.pname, F.text)
async def h_pname(m: Message, state: FSMContext, u):
    name = m.text.strip()
    if not 2 <= len(name) <= 40:
        return await m.answer(R("bad_name"))
    await state.update_data(platform=name)
    await ask(m, state, "price", u)


@ur.message(Create.price, F.text)
async def h_price(m: Message, state: FSMContext, u):
    s = re.sub(r"(₽|руб\.?|р\.?)$", "", m.text.strip().replace(",", ".").replace(" ", ""), flags=re.I)
    if not re.fullmatch(r"\d{1,7}(\.\d{1,2})?", s) or float(s) <= 0:
        return await m.answer(R("bad_price"))
    await state.update_data(price=fmt_price(s))
    await ask(m, state, "desc", u)


@ur.message(Create.desc, F.text)
async def h_desc(m: Message, state: FSMContext, u):
    desc = m.text.strip()
    if not 3 <= len(desc) <= 1500:
        return await m.answer(R("bad_desc"))
    await state.update_data(desc=desc)
    await ask(m, state, "preview", u)


@ur.message(StateFilter(Create.pname, Create.price, Create.desc))
async def h_bad_input(m: Message):
    await m.answer("Отправьте текстовое сообщение.")


@ur.callback_query(F.data == "nav:back", StateFilter(*CREATE_STATES))
async def h_back(cb: CallbackQuery, state: FSMContext, u):
    cur = await state.get_state()
    d = await state.get_data()
    step = cur.split(":")[1]
    if step == "platform":
        release(u["user_id"])
        await state.clear()
        return await (show_slots(cb, u["user_id"]) if d.get("mode") == "slot" else show_main(cb, u))
    await ask(cb, state, {"pname": "platform", "price": "platform", "desc": "price", "preview": "desc"}[step], u)


@ur.callback_query(F.data == "pv:ok", StateFilter(Create.preview))
async def h_confirm(cb: CallbackQuery, state: FSMContext, u):
    d = await state.get_data()
    await state.clear()  # повторное нажатие уже не пройдёт
    if not cfg.s("channel_id"):
        return await screen(cb, R("channel_error"), build_kb("profile"))
    res, val = await reserve(u, d)
    if res == "busy":
        release(u["user_id"])
        if d["mode"] == "slot":
            with suppress(TelegramAPIError):
                await cb.answer(strip_tags(R("slot_busy", {"next_time": fmt_dt(val) if val else "—"}))[:190], show_alert=True)
            return await show_slots(cb, u["user_id"])
        return await too_early(cb, u, val)
    if d["mode"] == "slot":
        return await screen(cb, R("booked", {"slot": fmt_dt(d["slot"])}), build_kb("profile"))
    if await publish_task(val):
        text = render(T("published_auto") if cfg.s("autoclose_enabled") == "1" else T("published_plain"),
                      {"minutes": cfg.i("autoclose_minutes")})
    else:
        text = R("n_failed", {"task_id": val})
    await screen(cb, text, build_kb("profile"))


@ur.callback_query(F.data == "m:close")
async def h_close_list(cb: CallbackQuery, state: FSMContext, u):
    await state.clear()
    rows = await db.all("SELECT * FROM tasks WHERE user_id=? AND status IN ('published','scheduled','publishing') "
                        "ORDER BY id DESC LIMIT 20", (u["user_id"],))
    if not rows:
        return await screen(cb, R("close_none"), build_kb("close", only=["cl_back"]))
    btns = [[{"text": "№%s · %s · %s ₽%s" % (t["id"], t["platform"][:16], fmt_price(t["price"]),
                                             " · " + to_msk(t["publish_at"]).strftime("%H:%M") if t["status"] != "published" else ""),
              "callback_data": "cl:%s" % t["id"]}] for t in rows]
    await screen(cb, R("close_list"), btns + build_kb("close", only=["cl_back"]))


async def own_task(tid, u):
    t = await db.one("SELECT * FROM tasks WHERE id=?", (tid,))
    if not t or (t["user_id"] != u["user_id"] and u["user_id"] not in ADMIN_IDS):
        return None
    return t


@ur.callback_query(F.data.regexp(r"^cl:\d+$"))
async def h_close_ask(cb: CallbackQuery, u):
    t = await own_task(int(cb.data[3:]), u)
    if not t or t["status"] not in ("published", "scheduled"):
        with suppress(TelegramAPIError):
            await cb.answer("Задание недоступно или уже закрыто", show_alert=True)
        return await show_main(cb, u)
    await screen(cb, R("close_confirm", task_vars(t)), build_kb("close", {"task_id": t["id"]}, skip=["cl_back"]))


@ur.callback_query(F.data.regexp(r"^cly:\d+$"))
async def h_close_do(cb: CallbackQuery, u):
    t = await own_task(int(cb.data[4:]), u)
    if not t:
        return await cb.answer("Нет доступа к заданию", show_alert=True)
    if t["status"] == "published" and await close_task(t["id"], u["user_id"]):
        text = R("close_done", task_vars(t))
    elif t["status"] == "scheduled" and await cancel_task(t["id"]):
        text = R("cancel_done", task_vars(t))
    else:
        text = "Задание уже закрыто."
    err = LAST_ERR.pop(t["id"], None)
    if err:
        text += "\n\nПост в канале не изменился. Ответ Telegram: <code>%s</code>" % esc(err)
    await screen(cb, text, build_kb("close", only=["cl_back"]))


@ur.callback_query(F.data == "adm:noop")
async def h_noop(cb: CallbackQuery):
    return None


@ur.callback_query(F.data.startswith("adm:"))
async def h_not_admin(cb: CallbackQuery):
    await cb.answer("Недостаточно прав", show_alert=True)


@ur.callback_query()
async def h_stale(cb: CallbackQuery):
    await cb.answer("Кнопка устарела. Откройте меню: /start", show_alert=True)


@ur.message(StateFilter(None))
async def h_any(m: Message, u):
    await show_main(m, u)


# ───────────────────────── Админ-панель ─────────────────────────
class AdminSt(StatesGroup):
    input = State()
    banner = State()


adm = Router(name="admin")
adm.message.filter(F.from_user.id.in_(ADMIN_IDS))
adm.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))
PAGE = 8
SAMPLE = {"platform": "2ГИС", "cena": "500 ₽", "opisanie": "Оставить отзыв на карточке компании", "username": "@username",
          "user_id": 123456789, "date": "01.01.2026", "time": "12:00", "task_id": 42, "expiry_date": "01.02.2026 12:00 МСК",
          "minutes": 10, "interval": 20, "next_time": "01.01.2026 12:20", "slot": "01.01.2026 12:20", "status": "Активен",
          "granted_date": "01.01.2026 12:00", "published": 5, "closed": 4, "scheduled": 1}
PREVIEW_GROUP = {"task_open": "channel_open", "task_closed": "channel_closed", "main_menu": "main", "no_access": "noaccess",
                 "expired_title": "expired", "expired_quote": "expired", "profile": "profile", "pick_platform": "platform",
                 "admin_menu": "admin"}
CANCEL = [[{"text": "Отмена", "callback_data": "adm:menu"}]]


def A(text, data, style=None):
    d = {"text": text, "callback_data": data}
    if style:
        d["style"] = style
    return d


def back_admin():
    b = btn_spec("ad_back")
    return [[b or A("Админ-панель", "adm:menu")]]


def status_mark(u):
    return {"ok": "[+]", "expired": "[истёк]", "banned": "[бан]", "none": "[-]"}[access_state(u)]


async def ask_input(ev, state, text, spec=None, **data):
    d = await state.get_data()
    if isinstance(ev, Message):
        await clear_prev(ev.chat.id, d.get("pm"))
    await state.set_state(AdminSt.input)
    await state.update_data(**data)
    msg = await screen(ev, text, spec or CANCEL)
    await state.update_data(pm=msg.message_id)


async def resolve_user(s: str):
    s = s.strip()
    if re.fullmatch(r"\d{3,15}", s):
        await db.run("INSERT OR IGNORE INTO users(user_id,created_at) VALUES(?,?)", (int(s), now_ts()))
        return await get_user(int(s))
    if re.fullmatch(r"@?[A-Za-z0-9_]{4,32}", s):
        return await db.one("SELECT * FROM users WHERE lower(username)=lower(?)", (s.lstrip("@"),))
    return None


@adm.message(Command("admin"))
async def a_cmd(m: Message, state: FSMContext):
    await state.clear()
    await screen(m, R("admin_menu"), build_kb("admin", skip=["ad_back"]), "banner_admin")


@adm.callback_query(F.data == "adm:menu")
async def a_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await screen(cb, R("admin_menu"), build_kb("admin", skip=["ad_back"]), "banner_admin")


# ── Пользователи ──
async def user_card(ev, uid):
    u = await get_user(uid)
    if not u:
        return await screen(ev, "Пользователь не найден.", back_admin())
    p, c, s = await counts(uid)
    v = uvars(u)
    text = ("<b>Пользователь</b>\n\nUsername: %s\nID: <code>%s</code>\nСтатус: %s\nДоступ выдан: %s\nДоступ до: %s\n"
            "Заданий: опубликовано %s · закрыто %s · запланировано %s" %
            (esc(disp_name(u)), uid, v["status"], v["granted_date"], v["expiry_date"], p, c, s))
    spec = [[A("+7 дн", "adm:ua:%s:d7" % uid), A("+30 дн", "adm:ua:%s:d30" % uid)],
            [A("Бессрочно", "adm:ua:%s:perm" % uid), A("Отозвать доступ", "adm:ua:%s:revoke" % uid)],
            [A("Разблокировать", "adm:ua:%s:unban" % uid, "success") if u["banned"] else A("Заблокировать", "adm:ua:%s:ban" % uid, "danger")],
            [A("К списку", "adm:us:0")]]
    await screen(ev, text, spec)


@adm.callback_query(F.data.startswith("adm:us:"))
async def a_users(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    page = int(cb.data.split(":")[2])
    total = (await db.one("SELECT COUNT(*) n FROM users"))["n"]
    rows = await db.all("SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?", (PAGE, page * PAGE))
    btns = [[A("%s %s · %s" % (status_mark(u), disp_name(u), u["user_id"]), "adm:u:%s" % u["user_id"])] for u in rows]
    nav = ([A("Назад", "adm:us:%d" % (page - 1))] if page else []) + ([A("Вперёд", "adm:us:%d" % (page + 1))] if (page + 1) * PAGE < total else [])
    await screen(cb, "<b>Пользователи</b> (%s)\nСтраница %d" % (total, page + 1),
                 btns + ([nav] if nav else []) + [[A("Поиск", "adm:usearch")]] + back_admin())


@adm.callback_query(F.data == "adm:usearch")
async def a_usearch(cb: CallbackQuery, state: FSMContext):
    await ask_input(cb, state, "Отправьте ID или @username пользователя:", kind="uid", op="card")


@adm.callback_query(F.data.startswith("adm:u:"))
async def a_user(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await user_card(cb, int(cb.data.split(":")[2]))


@adm.callback_query(F.data.startswith("adm:ua:"))
async def a_user_act(cb: CallbackQuery):
    _, _, uid, act = cb.data.split(":")
    uid = int(uid)
    if act in ("d7", "d30", "perm"):
        await grant(uid, {"d7": 7, "d30": 30, "perm": 0}[act], extend=act != "perm")
        await notify_granted(uid)
    elif act == "revoke":
        await db.run("UPDATE users SET has_access=0, expires_at=NULL, expiry_notified=0 WHERE user_id=?", (uid,))
    elif act == "ban":
        await db.run("UPDATE users SET banned=1 WHERE user_id=?", (uid,))
        await db.run("UPDATE tasks SET status='cancelled' WHERE user_id=? AND status='scheduled'", (uid,))
    elif act == "unban":
        await db.run("UPDATE users SET banned=0 WHERE user_id=?", (uid,))
    await user_card(cb, uid)


async def notify_granted(uid):
    u = await get_user(uid)
    with suppress(Exception):
        await send_new(uid, R("n_granted", uvars(u)))


@adm.callback_query(F.data.in_({"adm:grant", "adm:extend", "adm:ban"}))
async def a_flow(cb: CallbackQuery, state: FSMContext):
    op = cb.data[4:]
    title = {"grant": "Выдать подписку", "extend": "Продлить подписку", "ban": "Заблокировать пользователя"}[op]
    await ask_input(cb, state, "<b>%s</b>\n\nОтправьте ID или @username пользователя (он должен был запускать бота):" % title,
                    kind="uid", op=op)


# ── Единый обработчик ввода ──
@adm.message(AdminSt.input, F.text)
async def a_input(m: Message, state: FSMContext):
    d = await state.get_data()
    kind, txt = d.get("kind"), m.text.strip()
    if kind == "uid":
        u2 = await resolve_user(txt)
        if not u2:
            return await send_new(m.chat.id, "Пользователь не найден. Укажите числовой ID или @username тех, кто запускал бота.", CANCEL)
        if d["op"] in ("grant", "extend"):
            return await ask_input(m, state, "Количество дней (0 — бессрочно):", kind="days", uid=u2["user_id"])
        await state.clear()
        return await user_card(m, u2["user_id"])
    if kind == "days":
        if not re.fullmatch(r"\d{1,4}", txt) or int(txt) > 3650:
            return await send_new(m.chat.id, "Введите целое число дней от 0 до 3650.", CANCEL)
        exp = await grant(d["uid"], int(txt), extend=d["op"] == "extend")
        await notify_granted(d["uid"])
        await state.clear()
        return await user_card(m, d["uid"])
    if kind == "channel":
        return await set_channel(m, state, txt)
    if kind == "text":
        return await text_preview(m, state, d)
    if kind == "btnf":
        return await btn_input(m, state, d)
    if kind == "setting":
        return await setting_input(m, state, d, txt)
    if kind == "emoji_add":
        return await emoji_add(m, state)
    if kind == "emoji_check":
        return await emoji_check(m, state, txt)
    await state.clear()


# ── Канал ──
async def channel_status(chan):
    ch = await BOT.get_chat(chan)
    me = await BOT.get_me()
    mem = await BOT.get_chat_member(ch.id, me.id)
    ok = mem.status in ("administrator", "creator") and getattr(mem, "can_post_messages", True) is not False \
        and getattr(mem, "can_edit_messages", True) is not False
    return ch, ok, mem.status


@adm.callback_query(F.data == "adm:chan")
async def a_chan(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    cur = cfg.s("channel_id") or "не задан"
    await screen(cb, "<b>Канал публикаций</b>\n\nТекущий: <code>%s</code>\n\nБот должен быть администратором канала с правами "
                     "публикации и редактирования сообщений." % esc(cur),
                 [[A("Изменить", "adm:chan_set"), A("Проверить", "adm:chan_check")]] + back_admin())


@adm.callback_query(F.data == "adm:chan_set")
async def a_chan_set(cb: CallbackQuery, state: FSMContext):
    await ask_input(cb, state, "Отправьте @username или числовой ID канала (например, -1001234567890):", kind="channel")


@adm.callback_query(F.data == "adm:chan_check")
async def a_chan_check(cb: CallbackQuery):
    chan = cfg.s("channel_id")
    if not chan:
        return await cb.answer("Канал не задан", show_alert=True)
    try:
        ch, ok, st = await channel_status(chan)
        await cb.answer(("OK: %s" % ch.title if ok else "Нет прав (статус: %s)" % st)[:190], show_alert=True)
    except TelegramAPIError as e:
        await cb.answer("%s" % e.message[:180], show_alert=True)


async def set_channel(m, state, txt):
    try:
        ch, ok, st = await channel_status(txt)
    except TelegramAPIError as e:
        return await send_new(m.chat.id, "Не удалось получить канал: %s" % esc(e.message), CANCEL)
    if not ok:
        return await send_new(m.chat.id, "Бот не администратор канала «%s» или нет прав публикации/редактирования (статус: %s)." %
                              (esc(ch.title), st), CANCEL)
    await cfg.set_s("channel_id", str(ch.id))
    await state.clear()
    await send_new(m.chat.id, "Канал сохранён: <b>%s</b> (<code>%s</code>)" % (esc(ch.title), ch.id), back_admin())


# ── Шаблоны / тексты ──
@adm.callback_query(F.data.startswith("adm:txl:"))
async def a_text_list(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    page = int(cb.data.split(":")[2])
    items = _TEXTS[page * PAGE:(page + 1) * PAGE]
    btns = [[A(("" if k in cfg.texts else "") + label, "adm:tx:%s" % k)] for k, label, _, _ in items]
    nav = ([A("Назад", "adm:txl:%d" % (page - 1))] if page else []) + ([A("Вперёд", "adm:txl:%d" % (page + 1))] if (page + 1) * PAGE < len(_TEXTS) else [])
    await screen(cb, "<b>Шаблоны и тексты</b>\n— изменён администратором. Страница %d/%d" %
                 (page + 1, -(-len(_TEXTS) // PAGE)), btns + ([nav] if nav else []) + back_admin())


@adm.callback_query(F.data.startswith("adm:tx:"))
async def a_text_card(cb: CallbackQuery, state: FSMContext):
    key = cb.data[7:]
    if key not in TEXT_DEF:
        return
    await state.clear()
    await text_card(cb, key)


async def text_card(ev, key):
    d, label, hint = TEXT_DEF[key]
    page = [k for k, *_ in _TEXTS].index(key) // PAGE
    text = ("<b>%s</b> <code>%s</code>\n\nПеременные: <code>%s</code>\nPremium Emoji: вставьте его в текст, либо <code>{emoji:ID}</code> / "
            "<code>{e:имя}</code>.\n\nТекущий шаблон:\n<pre>%s</pre>" % (esc(label), key, esc(hint or "—"), esc(T(key)[:2500])))
    spec = [[A("Изменить", "adm:txe:%s" % key), A("Предпросмотр", "adm:txp:%s" % key)],
            [A("По умолчанию", "adm:txr:%s" % key)], [A("К списку", "adm:txl:%d" % page)]]
    await screen(ev, text, spec)


def preview_spec(key):
    g = PREVIEW_GROUP.get(key)
    return build_kb(g, {"poster_url": "https://t.me/example", "task_id": 42, "is_admin": True}) if g else []


async def send_preview(chat_id, key, tpl, extra):
    return await send_new(chat_id, "<b>Предпросмотр</b>\n\n" + render(tpl, SAMPLE), preview_spec(key) + extra, strict=True)


@adm.callback_query(F.data.startswith("adm:txp:"))
async def a_text_prev(cb: CallbackQuery):
    key = cb.data[8:]
    try:
        await send_preview(cb.from_user.id, key, T(key), [])
    except TelegramBadRequest as e:
        await cb.answer("Telegram отклонил: %s" % e.message[:170], show_alert=True)


@adm.callback_query(F.data.startswith("adm:txr:"))
async def a_text_reset(cb: CallbackQuery):
    key = cb.data[8:]
    await cfg.reset_text(key)
    await cb.answer("Сброшено")
    await text_card(cb, key)


@adm.callback_query(F.data.startswith("adm:txe:"))
async def a_text_edit(cb: CallbackQuery, state: FSMContext):
    key = cb.data[8:]
    await ask_input(cb, state, "Отправьте новый текст для «%s». Можно использовать форматирование и Premium Emoji прямо в сообщении." %
                    esc(TEXT_DEF[key][1]), [[A("Отмена", "adm:tx:%s" % key)]], kind="text", tk=key)


async def text_preview(m: Message, state, d):
    key, new = d["tk"], m.html_text
    if len(new) > 3500:
        return await send_new(m.chat.id, "Слишком длинный текст (максимум 3500 символов).", CANCEL)
    try:
        await send_preview(m.chat.id, key, new, [[A("Сохранить", "adm:txs"), A("Отмена", "adm:tx:%s" % key)]])
    except TelegramBadRequest as e:
        return await send_new(m.chat.id, "Telegram не принял текст/разметку/Premium Emoji: <code>%s</code>\nИсправьте и отправьте снова." %
                              esc(e.message), [[A("Отмена", "adm:tx:%s" % key)]])
    await state.update_data(tpend=new)
    await state.set_state(None)


@adm.callback_query(F.data == "adm:txs")
async def a_text_save(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if not d.get("tpend") or not d.get("tk"):
        return await cb.answer("Сессия устарела", show_alert=True)
    await cfg.set_text(d["tk"], d["tpend"])
    await state.clear()
    await cb.answer("Сохранено")
    await text_card(cb, d["tk"])


# ── Кнопки ──
def eff_btn(key, pend):
    b = cfg.btn(key)
    b.update(pend or {})
    return b


@adm.callback_query(F.data == "adm:bg")
async def a_btn_groups(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await screen(cb, "<b>Настройки кнопок</b>\nВыберите раздел:",
                 [[A(l, "adm:bgl:%s" % g)] for g, l in GROUP_LABELS.items()] + back_admin())


@adm.callback_query(F.data.startswith("adm:bgl:"))
async def a_btn_list(cb: CallbackQuery, state: FSMContext):
    g = cb.data[8:]
    await state.clear()
    btns = [[A("%s  [%s]" % (cfg.btn(k)["text"], k), "adm:bt:%s" % k)] for k, v in BTN_DEF.items() if v["grp"] == g]
    await screen(cb, "<b>%s</b>\nВыберите кнопку:" % GROUP_LABELS.get(g, g), btns + [[A("К разделам", "adm:bg")]])


@adm.callback_query(F.data.startswith("adm:bt:"))
async def a_btn_open(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.update_data(bk=cb.data[7:], pend={})
    await btn_card(cb, state)


async def btn_card(ev, state, note=""):
    d = await state.get_data()
    key, pend = d.get("bk"), d.get("pend") or {}
    if not key:
        return await screen(ev, "Сессия устарела.", back_admin())
    b = eff_btn(key, pend)
    act = "%s" % esc(b["value"]) if b["kind"] == "url" else "callback <code>%s</code>" % esc(b["value"])
    text = ("<b>Кнопка</b> <code>%s</code> · %s\n\nНазвание: %s\nДействие: %s\nСтрока: %s · Позиция: %s\nСтиль: %s\n"
            "Premium Emoji ID: %s\nВключена: %s" % (key, GROUP_LABELS[b["grp"]], esc(b["text"]), act, b["row"], b["pos"],
                                                     b["style"] or "по умолчанию", b["emoji_id"] or "—", "да" if b["enabled"] else "нет"))
    if pend:
        text += "\n\n<i>Есть несохранённые изменения. Кнопка ниже — предпросмотр.</i>"
    text += note
    prev = btn_spec(key, {"poster_url": "https://t.me/example", "task_id": 1}, b, preview=True)
    ctrl = [[A("Название", "adm:bf:text"), A("Действие/ссылка", "adm:bf:url")],
            [A("Строка", "adm:bf:row"), A("Позиция", "adm:bf:pos")],
            [A("Стиль", "adm:bf:style"), A("Emoji ID", "adm:bf:emoji")]]
    if b["grp"] != "admin":
        ctrl.append([A("Вкл/выкл", "adm:bf:en")])
    ctrl += [[A("Сохранить", "adm:bsave"), A("По умолчанию", "adm:bres")],
             [A("К списку", "adm:bgl:%s" % b["grp"])]]
    spec = ([[prev]] if prev else []) + ctrl
    try:
        msg = await screen(ev, text, spec, strict=True)
    except TelegramBadRequest as e:
        text += "\n\nTelegram отклонил кнопку: <code>%s</code>" % esc(e.message)
        msg = await screen(ev, text, strip_spec(spec))
    await state.update_data(pm=msg.message_id)


@adm.callback_query(F.data.startswith("adm:bf:"))
async def a_btn_field(cb: CallbackQuery, state: FSMContext):
    f, d = cb.data[7:], await state.get_data()
    if not d.get("bk"):
        return await cb.answer("Сессия устарела", show_alert=True)
    pend = dict(d.get("pend") or {})
    b = eff_btn(d["bk"], pend)
    if f == "style":
        order = ["", "primary", "success", "danger"]
        pend["style"] = order[(order.index(b["style"]) + 1) % 4 if b["style"] in order else 0]
    elif f == "en":
        pend["enabled"] = 0 if b["enabled"] else 1
    else:
        prompts = {"text": "Введите новое название кнопки (до 64 символов):",
                   "url": "Отправьте ссылку (https://…, tg://…), чтобы кнопка открывала её. Допустимы {support_url}, {admin_contact_url}, "
                          "{poster_url}. «-» — вернуть действие по умолчанию.",
                   "row": "Номер строки (0–20):", "pos": "Позиция в строке (0–8):",
                   "emoji": "Отправьте ID Premium Emoji или сообщение с Premium Emoji. «-» — убрать."}
        return await ask_input(cb, state, prompts[f], [[A("Отмена", "adm:bt:%s" % d["bk"])]], kind="btnf", field=f)
    await state.update_data(pend=pend)
    await btn_card(cb, state)


async def btn_input(m: Message, state, d):
    f, txt, pend = d["field"], m.text.strip(), dict(d.get("pend") or {})
    dflt = BTN_DEF[d["bk"]]
    err = None
    if f == "text":
        if 1 <= len(txt) <= 64:
            pend["text"] = txt
        else:
            err = "Название: 1–64 символа."
    elif f == "url":
        if txt == "-":
            pend["kind"], pend["value"] = dflt["kind"], dflt["value"]
        elif valid_url(fill(txt, {**base_ctx(), "poster_url": "https://t.me/x"})):
            pend["kind"], pend["value"] = "url", txt
        else:
            err = "Нужна ссылка вида https://… или tg://…"
    elif f in ("row", "pos"):
        if re.fullmatch(r"\d{1,2}", txt) and int(txt) <= (20 if f == "row" else 8):
            pend[f] = int(txt)
        else:
            err = "Введите число в допустимом диапазоне."
    elif f == "emoji":
        ent = next((e for e in (m.entities or []) if e.type == "custom_emoji"), None)
        if ent:
            pend["emoji_id"] = ent.custom_emoji_id
        elif txt == "-":
            pend["emoji_id"] = ""
        elif re.fullmatch(r"\d{5,32}", txt):
            pend["emoji_id"] = txt
        else:
            err = "Нужен числовой ID или сообщение с Premium Emoji."
    if err:
        return await send_new(m.chat.id, "" + err, [[A("Отмена", "adm:bt:%s" % d["bk"])]])
    await clear_prev(m.chat.id, d.get("pm"))
    await state.update_data(pend=pend)
    await state.set_state(None)
    await btn_card(m, state)


@adm.callback_query(F.data == "adm:bsave")
async def a_btn_save(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if not d.get("bk"):
        return await cb.answer("Сессия устарела", show_alert=True)
    b = eff_btn(d["bk"], d.get("pend"))
    if b["kind"] == "url" and not valid_url(fill(b["value"], {**base_ctx(), "poster_url": "https://t.me/x"})):
        return await cb.answer("Некорректная ссылка", show_alert=True)
    await cfg.save_btn(d["bk"], b)
    await state.update_data(pend={})
    await cb.answer("Сохранено")
    await btn_card(cb, state)


@adm.callback_query(F.data == "adm:bres")
async def a_btn_reset(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if d.get("bk"):
        await cfg.reset_btn(d["bk"])
        await state.update_data(pend={})
        await cb.answer("Сброшено")
        await btn_card(cb, state)


# ── Premium Emoji ──
@adm.callback_query(F.data == "adm:em")
async def a_emoji(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    lib = "\n".join("• <code>%s</code> → %s <code>%s</code>" % (n, tg_emoji(e["emoji_id"], e["fallback"]), e["emoji_id"])
                    for n, e in cfg.emojis.items()) or "пока пусто"
    text = ("<b>Premium Emoji</b>\n\nВ любой текст можно вставить Premium Emoji прямо в сообщении, либо макросом "
            "<code>{emoji:ID}</code> или <code>{e:имя}</code> (из библиотеки). Для иконки кнопки укажите ID в «Настройках кнопок».\n"
            "Telegram может ограничивать Custom Emoji для ботов — при отказе вы получите предупреждение, замена на обычный эмодзи не скрывается.\n\n"
            "<b>Библиотека:</b>\n" + lib)
    spec = [[A("Добавить", "adm:ema"), A("Проверить ID", "adm:emc")]] + \
           [[A("" + n, "adm:emd:" + n)] for n in list(cfg.emojis)[:20]] + back_admin()
    try:
        await screen(cb, text, spec, strict=True)
    except TelegramBadRequest as e:
        await screen(cb, strip_custom(text) + "\n\nTelegram отклонил Premium Emoji: <code>%s</code>" % esc(e.message), spec)


@adm.callback_query(F.data == "adm:ema")
async def a_emoji_add(cb: CallbackQuery, state: FSMContext):
    await ask_input(cb, state, "Отправьте сообщение: <code>имя ID [запасной_эмодзи]</code>, либо Premium Emoji и имя (например: <code>fire</code>).",
                    [[A("Отмена", "adm:em")]], kind="emoji_add")


@adm.callback_query(F.data == "adm:emc")
async def a_emoji_chk(cb: CallbackQuery, state: FSMContext):
    await ask_input(cb, state, "Отправьте ID Premium Emoji для проверки:", [[A("Отмена", "adm:em")]], kind="emoji_check")


@adm.callback_query(F.data.startswith("adm:emd:"))
async def a_emoji_del(cb: CallbackQuery, state: FSMContext):
    n = cb.data[8:]
    await db.run("DELETE FROM emojis WHERE name=?", (n,))
    cfg.emojis.pop(n, None)
    await a_emoji(cb, state)


async def emoji_add(m: Message, state):
    ent = next((e for e in (m.entities or []) if e.type == "custom_emoji"), None)
    if ent:
        eid, fb = ent.custom_emoji_id, ent.extract_from(m.text)
        words = re.findall(r"[A-Za-z0-9_]{1,32}", m.text.replace(fb, " "))
        name = words[0] if words else "emoji%d" % (len(cfg.emojis) + 1)
    else:
        p = m.text.split()
        if len(p) < 2 or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", p[0]) or not re.fullmatch(r"\d{5,32}", p[1]):
            return await send_new(m.chat.id, "Формат: <code>имя ID [эмодзи]</code>", [[A("Отмена", "adm:em")]])
        name, eid, fb = p[0], p[1], (p[2] if len(p) > 2 else "⭐")
    fb = fb[:8]
    await db.run("INSERT INTO emojis(name,emoji_id,fallback) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET emoji_id=excluded.emoji_id,"
                 "fallback=excluded.fallback", (name, eid, fb))
    cfg.emojis[name] = {"name": name, "emoji_id": eid, "fallback": fb}
    await state.clear()
    await send_new(m.chat.id, "Сохранено: <code>{e:%s}</code> → <code>%s</code>" % (name, eid), [[A("К Premium Emoji", "adm:em")]])


async def emoji_check(m: Message, state, txt):
    if not re.fullmatch(r"\d{5,32}", txt):
        return await send_new(m.chat.id, "ID — только цифры.", [[A("Отмена", "adm:em")]])
    await state.clear()
    try:
        found = await BOT.get_custom_emoji_stickers([txt])
    except TelegramAPIError as e:
        return await send_new(m.chat.id, "Ошибка проверки: %s" % esc(e.message), [[A("К Premium Emoji", "adm:em")]])
    if not found:
        return await send_new(m.chat.id, "Emoji с таким ID не найден.", [[A("К Premium Emoji", "adm:em")]])
    try:
        await send_new(m.chat.id, "ID существует. Тест отображения: %s" % tg_emoji(txt, found[0].emoji or "⭐"),
                       [[A("К Premium Emoji", "adm:em")]], strict=True)
    except TelegramBadRequest as e:
        await send_new(m.chat.id, "ID существует, но Telegram запретил его использование ботом: <code>%s</code>" % esc(e.message),
                       [[A("К Premium Emoji", "adm:em")]])


# ── Баннеры ──
@adm.callback_query(F.data == "adm:bn")
async def a_banners(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await screen(cb, "<b>Баннеры</b>\n— баннер загружен.",
                 [[A(("[+] " if cfg.s("banner_" + k) else "[ ] ") + l, "adm:bns:" + k)] for k, l in BANNER_SLOTS.items()] + back_admin())


@adm.callback_query(F.data.startswith("adm:bns:"))
async def a_banner(cb: CallbackQuery, state: FSMContext):
    k = cb.data[8:]
    await state.clear()
    fid = cfg.s("banner_" + k)
    spec = [[A("Загрузить/заменить", "adm:bnu:" + k)]] + ([[A("Удалить", "adm:bnd:" + k)]] if fid else []) + [[A("К баннерам", "adm:bn")]]
    await screen(cb, "<b>Баннер: %s</b>\n%s" % (BANNER_SLOTS[k], "Текущий показан выше." if fid else "Не загружен."), spec, "banner_" + k)


@adm.callback_query(F.data.startswith("adm:bnu:"))
async def a_banner_up(cb: CallbackQuery, state: FSMContext):
    k = cb.data[8:]
    await state.set_state(AdminSt.banner)
    await state.update_data(slot=k)
    msg = await screen(cb, "Отправьте изображение (как фото) для баннера «%s»:" % BANNER_SLOTS[k], [[A("Отмена", "adm:bn")]])
    await state.update_data(pm=msg.message_id)


@adm.message(AdminSt.banner, F.photo)
async def a_banner_save(m: Message, state: FSMContext):
    d = await state.get_data()
    await cfg.set_s("banner_" + d["slot"], m.photo[-1].file_id)
    await state.clear()
    await send_new(m.chat.id, "Баннер «%s» сохранён." % BANNER_SLOTS[d["slot"]], [[A("К баннерам", "adm:bn")]],
                   cfg.s("banner_" + d["slot"]))


@adm.message(AdminSt.banner)
async def a_banner_bad(m: Message):
    await m.answer("Отправьте изображение как фото.")


@adm.callback_query(F.data.startswith("adm:bnd:"))
async def a_banner_del(cb: CallbackQuery, state: FSMContext):
    await cfg.set_s("banner_" + cb.data[8:], "")
    await cb.answer("Удалено")
    await a_banners(cb, state)


# ── Настройки (интервалы, автозакрытие, расписание, доступ) ──
def fmt_setting(k):
    v = cfg.s(k)
    t = SETTING_META[k][1]
    if t == "bool":
        return "вкл" if v == "1" else "выкл"
    return v or "—"


async def section_screen(ev, name):
    title, keys, hint = SECTIONS[name]
    text = "<b>%s</b>\n%s\n\n" % (title, hint) + "\n".join("• %s: <b>%s</b>" % (SETTING_META[k][0], esc(fmt_setting(k))) for k in keys)
    btns = [[A("%s: %s" % (SETTING_META[k][0], fmt_setting(k))[:60],
               ("adm:sv:" if SETTING_META[k][1] in ("int", "url", "emoji") else "adm:tg:") + k + ":" + name)] for k in keys]
    if name == "sch":
        rows = await db.all("SELECT * FROM tasks WHERE status IN ('published','scheduled','publishing') ORDER BY COALESCE(published_at,publish_at) LIMIT 10")
        text += "\n\n<b>Активные и запланированные задания:</b>" + ("" if rows else " нет")
        btns += [[A("№%s · %s · %s · %s" % (t["id"], t["platform"][:12], "опубл." if t["status"] == "published" else "план",
                                           to_msk(t["published_at"] or t["publish_at"]).strftime("%H:%M")), "adm:tk:%s" % t["id"])] for t in rows]
    await screen(ev, text, btns + back_admin())


@adm.callback_query(F.data.startswith("adm:sec:"))
async def a_section(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await section_screen(cb, cb.data[8:])


@adm.callback_query(F.data.startswith("adm:tg:"))
async def a_toggle(cb: CallbackQuery):
    _, _, key, sec = cb.data.split(":")
    t, p = SETTING_META[key][1], SETTING_META[key][2]
    cur = cfg.s(key)
    if t == "bool":
        new = "0" if cur == "1" else "1"
    else:
        new = p[(p.index(cur) + 1) % len(p)] if cur in p else p[0]
    await cfg.set_s(key, new)
    await section_screen(cb, sec)


@adm.callback_query(F.data.startswith("adm:sv:"))
async def a_set_value(cb: CallbackQuery, state: FSMContext):
    _, _, key, sec = cb.data.split(":")
    label, t, p = SETTING_META[key]
    hint = ("число от %s до %s" % p if t == "int" else
            "ID или сообщение с Premium Emoji, «-» — убрать" if t == "emoji" else "ссылка https://… или tg://…")
    await ask_input(cb, state, "<b>%s</b>\nТекущее: <code>%s</code>\nВведите значение (%s):" % (label, esc(cfg.s(key)), hint),
                    [[A("Отмена", "adm:sec:" + sec)]], kind="setting", key=key, sec=sec)


async def setting_input(m: Message, state, d, txt):
    key = d["key"]
    t, p = SETTING_META[key][1], SETTING_META[key][2]
    if t == "int" and re.fullmatch(r"\d{1,6}", txt) and p[0] <= int(txt) <= p[1]:
        await cfg.set_s(key, str(int(txt)))
    elif t == "url" and valid_url(txt):
        await cfg.set_s(key, txt)
    elif t == "emoji" and (txt == "-" or re.fullmatch(r"\d{5,32}", txt) or any(e.type == "custom_emoji" for e in (m.entities or []))):
        ent = next((e for e in (m.entities or []) if e.type == "custom_emoji"), None)
        await cfg.set_s(key, "" if txt == "-" else (ent.custom_emoji_id if ent else txt))
    else:
        return await send_new(m.chat.id, "Некорректное значение.", [[A("Отмена", "adm:sec:" + d["sec"])]])
    await clear_prev(m.chat.id, d.get("pm"))
    await state.clear()
    await section_screen(m, d["sec"])


@adm.callback_query(F.data.startswith("adm:tk:"))
async def a_task_ask(cb: CallbackQuery):
    t = await db.one("SELECT * FROM tasks WHERE id=?", (int(cb.data[7:]),))
    if not t:
        return await cb.answer("Не найдено", show_alert=True)
    await screen(cb, "Задание №%s · %s · %s ₽\nАвтор: %s\nСтатус: %s\n\nЗакрыть/отменить?" %
                 (t["id"], esc(t["platform"]), fmt_price(t["price"]), esc(t["poster_name"]), t["status"]),
                 [[A("Да", "adm:tkc:%s" % t["id"], "danger"), A("Назад", "adm:sec:sch")]])


@adm.callback_query(F.data.startswith("adm:tkc:"))
async def a_task_close(cb: CallbackQuery):
    t = await db.one("SELECT * FROM tasks WHERE id=?", (int(cb.data[8:]),))
    if t and ((t["status"] == "published" and await close_task(t["id"], cb.from_user.id)) or
              (t["status"] == "scheduled" and await cancel_task(t["id"]))):
        await notify_user(t["user_id"], "n_closed_admin", t)
        await cb.answer("Готово")
    else:
        await cb.answer("Уже закрыто", show_alert=True)
    await section_screen(cb, "sch")


@adm.callback_query(F.data == "adm:stat")
async def a_stats(cb: CallbackQuery):
    day = int(datetime.now(MSK).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    t = await db.one("SELECT COUNT(*) total, COALESCE(SUM(status='published'),0) act, COALESCE(SUM(status='closed'),0) cl, "
                     "COALESCE(SUM(status IN ('scheduled','publishing')),0) sch, COALESCE(SUM(status='failed'),0) fl, "
                     "COALESCE(SUM(status='missed'),0) ms, COALESCE(SUM(published_at>=?),0) today FROM tasks", (day,))
    users = await db.all("SELECT * FROM users")
    sts = [access_state(u) for u in users]
    await screen(cb, "<b>Статистика</b>\n\nПользователей: %s\nС доступом: %s · истёк: %s · заблокировано: %s\n\n"
                     "Заданий всего: %s\nАктивных: %s · закрыто: %s\nЗапланировано: %s\nОшибок: %s · пропущено: %s\nОпубликовано сегодня (МСК): %s" %
                 (len(users), sts.count("ok"), sts.count("expired"), sts.count("banned"), t["total"], t["act"], t["cl"],
                  t["sch"], t["fl"], t["ms"], t["today"]), back_admin())


# ───────────────────────── Запуск ─────────────────────────
async def on_error(event: ErrorEvent):
    log.exception("Необработанная ошибка: %s", event.exception)
    cb = event.update.callback_query
    if cb:
        with suppress(TelegramAPIError):
            await cb.answer("Произошла ошибка. Попробуйте ещё раз.", show_alert=True)
    return True


async def main():
    global BOT, db, cfg
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not BOT_TOKEN or not ADMIN_IDS:
        sys.exit("Задайте переменные окружения BOT_TOKEN и ADMIN_IDS (CHANNEL_ID — по желанию, можно в админке).")
    db = DB(DB_PATH)
    await db.init()
    cfg = Cfg()
    await cfg.load()
    if ENV_CHANNEL and "channel_id" not in cfg.settings:
        await cfg.set_s("channel_id", ENV_CHANNEL)
    await db.run("UPDATE tasks SET status='scheduled' WHERE status='publishing' AND message_id IS NULL")  # восстановление
    BOT = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    base = BaseMW()
    dp.message.outer_middleware(base)
    dp.callback_query.outer_middleware(base)
    ur.message.middleware(AccessMW())
    ur.callback_query.middleware(AccessMW())
    dp.include_router(adm)
    dp.include_router(ur)
    dp.errors.register(on_error)
    from aiogram.types import BotCommand
    with suppress(TelegramAPIError):
        await BOT.set_my_commands([BotCommand(command="start", description="Главное меню"),
                                   BotCommand(command="admin", description="Админ-панель")])
    task = asyncio.create_task(scheduler())
    try:
        await dp.start_polling(BOT, allowed_updates=dp.resolve_used_update_types())
    finally:
        task.cancel()
        with suppress(BaseException):
            await task
        await BOT.session.close()
        await db.close()


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        asyncio.run(main())
