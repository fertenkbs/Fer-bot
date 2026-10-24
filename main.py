# -*- coding: utf-8 -*-
"""Gimbek AI: Telegram-бот на aiogram 3.25+ (тарифы, оплата, тикеты, поиск, бизнес-режим)."""
import asyncio, base64, hashlib, hmac, html as H, io, json, logging, math, os, random, re, shutil, tempfile, time, zipfile
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlparse

import aiohttp, aiosqlite
from aiohttp import web
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BotCommand, BotCommandScopeChat, BotCommandScopeDefault, BufferedInputFile,
                           BusinessConnection, CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto,
                           LabeledPrice, Message, PreCheckoutQuery, WebAppInfo, InputMediaVideo, InputMediaAnimation, InputSticker)
from aiogram.types import InlineKeyboardButton as _IKB
from openai import AsyncOpenAI


# ====================== ПЕРЕМЕННЫЕ (лучше задать в Variables хостинга) ======================
def _env(name, default=""):
    return os.getenv(name, default).strip().strip('"').strip("'")


BOT_TOKEN = _env("BOT_TOKEN", "ВСТАВЬ_БОТ_ТОКЕН")
OWNER_ID = int(_env("OWNER_ID", "0") or 0)          # твой Telegram ID: только ты видишь /admin
API_KEY = _env("API_KEY", "ВСТАВЬ_API_КЛЮЧ")         # ключ ИИ-провайдера (можно сменить и в админке)
BASE_URL = _env("BASE_URL", "https://api.basisweb.ru/v1")
CRYPTO_TOKEN = _env("CRYPTO_TOKEN")                   # токен @CryptoBot (Crypto Pay)
PROVIDER_TOKEN = _env("PROVIDER_TOKEN")               # токен платёжного провайдера для карт (@BotFather → Payments)
ACTIVATION_KEY = _env("ACTIVATION_KEY", "РусиканглApi#1")  # секретный ключ (работает, если включён в настройках)
DB = _env("DB_PATH", "/data/bot.db")                      # путь к базе: на хостинге укажи папку постоянного тома, например /data/bot.db
BACKUP_API_KEY = _env("BACKUP_API_KEY")               # запасной ключ: включается, когда на основном кончились токены
BACKUP_BASE_URL = _env("BACKUP_BASE_URL", "https://darkapi.shop/v1")
# ==========================================================================================

logging.basicConfig(level=logging.INFO)
router = Router()
ai = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=120, max_retries=1)

# ---------------------- модели ----------------------
# (группа, id, название, коэффициент расхода токенов)
CHAT_CATALOG = [
    ("OpenAI", "gpt-6-astra", "GPT 6 Astra", "10"), ("OpenAI", "gpt-5.6-sol", "GPT 5.6 Sol", "4"),
    ("OpenAI", "gpt-5.5", "GPT 5.5", "4"), ("OpenAI", "gpt-6.1-sol", "GPT 6.1 Sol", "3"),
    ("OpenAI", "gpt-6-sol", "GPT 6 Sol", "3"), ("OpenAI", "gpt-5.6-terra", "GPT 5.6 Terra", "1.3"),
    ("OpenAI", "gpt-5.6-luna", "GPT 5.6 Luna", "0.5"), ("OpenAI", "gpt-6-luna", "GPT 6 Luna", "0.38"),
    ("Claude", "claude-fable-5-1", "Fable 5.1", "8"), ("Claude", "claude-fable-5", "Fable 5", "8"),
    ("Claude", "claude-opus-5-5", "Opus 5.5", "4"), ("Claude", "claude-opus-5", "Opus 5", "4"),
    ("Claude", "claude-opus-4-8", "Opus 4.8", "4"), ("Claude", "claude-opus-4-7", "Opus 4.7", "4"),
    ("Claude", "claude-opus-4-6", "Opus 4.6", "4"), ("Claude", "claude-sonnet-5", "Sonnet 5", "2"),
    ("Claude", "claude-sonnet-4-6", "Sonnet 4.6", "2"), ("Claude", "claude-haiku-4-5", "Haiku 4.5", "0.9"),
    ("Claude", "claude-haiku-5-5", "Haiku 5.5", "0.7"), ("Claude", "claude-sonnet-5-5", "Sonnet 5.5", "1.2"),
    ("Google", "gemini-3.1-pro", "Gemini 3.1 Pro", "2"), ("Google", "gemini-3.6-flash", "Gemini 3.6 Flash", "2"),
    ("Google", "gemini-4-argon", "Gemini 4 Argon", "2.6"), ("Google", "gemini-3.7-flash", "Gemini 3.7 Flash", "2"), ("Google", "gemini-3.8-flash", "Gemini 3.8 Flash", "2"),
    ("xAI", "grok-4.7", "Grok 4.7", "0.5"), ("xAI", "grok-4.6", "Grok 4.6", "0.5"),
    ("xAI", "grok-4.5", "Grok 4.5", "0.5"), ("xAI", "composer-2.5-fast", "Composer 2.5 Fast", "0.3"),
    ("DeepSeek", "deepseek-v4-pro", "V4 Pro", "0.5"), ("DeepSeek", "deepseek-v4.1-flash", "V4.1 Flash", "0.1"),
    ("DeepSeek", "deepseek-v4-flash", "V4 Flash", "0.1"),
    ("Zhipu", "glm-5.3", "GLM 5.3", "1.5"), ("Zhipu", "glm-5.2", "GLM 5.2", "1.5"),
    ("Zhipu", "glm-5-turbo", "GLM 5 Turbo", "1"), ("Zhipu", "glm-5.3-flash", "GLM 5.3 Flash", "0.3"),
    ("Moonshot", "kimi-k3", "Kimi K3", "2.5"), ("Moonshot", "kimi-k2.7-code", "Kimi K2.7 Code", "0.9"),
    ("Alibaba", "qwen3.8-max", "Qwen3.8 Max", "2.5"), ("Alibaba", "qwen3.8-flash", "Qwen3.8 Flash", "0.7"),
    ("Другие", "hy4-preview", "Tencent Hy4 Preview", "1"), ("Другие", "minimax-m3", "MiniMax M3", "0.3"),
    ("Другие", "muse-spark-1.3", "Meta Muse Spark 1.3", "0.07"), ("Другие", "mimo-v2.5", "Xiaomi MiMo V2.5", "0.05"),
]
IMG_CATALOG = [("gpt-image-2", "GPT Image 2", 50000), ("gpt-image-2.5-flare", "GPT Image 2.5 Flare", 50000),
               ("gpt-image-2.5-sunburst", "GPT Image 2.5 Sunburst", 50000)]
MODELS = [m[1] for m in CHAT_CATALOG]
MULT = {m[1]: float(m[3]) for m in CHAT_CATALOG}
TITLE = {m[1]: m[2] for m in CHAT_CATALOG}
IMG_MODELS = [m[0] for m in IMG_CATALOG]
IMG_COST = {m[0]: m[2] for m in IMG_CATALOG}
DEFAULT_CHAT = "gpt-6-luna"
DEFAULT_CODE = "deepseek-v4-pro"
CHEAP = ["gpt-6-luna", "deepseek-v4-flash", "deepseek-v4.1-flash"]

# ---------------------- тексты, кнопки, настройки ----------------------
DEF_SYS = ("Ты Gimbek AI, дружелюбный и точный ассистент. Отвечай на языке пользователя, кратко и по делу. "
           "Форматирование: простой текст, **жирный** для важного, списки через «-». "
           "НЕ используй LaTeX и формулы в долларах, таблицы, горизонтальные линии (---) и лишние символы; "
           "дроби и степени пиши обычным текстом (1/2, x², √3). "
           "Если нужны файлы, выводи каждый так: <<<FILE: имя.расш>>>\nкод\n<<<END>>>. Код полный, без заглушек. "
           "Android: Kivy+buildozer. 3D: один html с three.js (cdnjs).")

TEXTS = {
    "welcome2": "{avatar} {greeting} ❤️\n\n<b>{name}</b>, добро пожаловать в <b>Gimbek AI</b>!\n"
                "Пиши и присылай фото: я отвечу. Умею картинки, код, статьи в Telegraph и поиск в интернете.",
    "avatar_ask": "🙂 Сделать эмодзи из твоей круглой аватарки? Оно появится в приветствии и профиле.\n"
                  "Telegram хранит такие наборы публично: тот, у кого есть ссылка на набор, сможет увидеть аватарку. Согласен?",
    "need_key": "🔑 Для доступа введи секретный ключ одним сообщением.",
    "key_ok": "✅ Ключ принят! Теперь всё доступно.", "key_bad": "❌ Неверный ключ.",
    "banned": "🚫 Доступ ограничен. Напиши в поддержку: /support",
    "plans_title": "💎 <b>Тарифы Gimbek AI</b>",
    "plans_note": "Неиспользованные токены не переносятся на следующий месяц.",
    "agree_text": "📄 Перед оплатой нужно принять пользовательское соглашение и политику конфиденциальности.",
    "paid_ok": "✅ Оплата подтверждена!\nТариф <b>{plan}</b> активен до <b>{date}</b>.",
    "requisites": "", "img_ask": "🎨 Опиши картинку (можно «3 кот в шляпе» — 3 штуки).",
    "tg_ask": "📰 Тема статьи для Telegraph?",
    "support_ask": "🆘 Опиши проблему одним сообщением. Мы ответим здесь же.",
    "code_help": "📎 <code>/code задача</code> — длинный код и файлы без лимита\n<code>/apk задача</code> — проект Android (Kivy+buildozer)\n<code>/3d задача</code> — 3D-сцена (html)",
    "expire_soon": "⏰ Подписка <b>{plan}</b> заканчивается <b>{date}</b> (осталось {left}).\nПродли её, чтобы не потерять доступ.",
    "expire_now": "😔 Подписка <b>{plan}</b> закончилась. Ты переведён на бесплатный тариф.",
    "ai_info": "🤖 <b>Команда .ai в твоих чатах</b>\nПодключи бота в Telegram: Настройки → Telegram Business → Чат-боты (нужен Telegram Premium).\n"
               "Потом в любом личном чате напиши, например:\n<code>.ai что такое блокчейн</code>\n<code>.ai сделай статью в телеграф про кофе</code>\n"
               "<code>.ai какая погода в Казани</code>\nТвоя команда сразу исчезнет, собеседник увидит «печатает…» и получит ответ.\n\n"
               "💬 <b>Автоответчик .lc</b>: напиши <code>.lc</code> в чате, и бот будет отвечать за тебя. Стиль задаётся в «Мои настройки → Автоответчик». Выключить: <code>.lc</code> ещё раз.",
    "help2": "ℹ️ <b>Что я умею</b>\n• Общаться и отвечать на вопросы (пиши как обычно)\n• Понимать фото и отвечать по ним\n"
             "• Рисовать картинки: /img\n• Писать код: /code и /studio (студия кода)\n• Статьи в Telegraph: /tg\n• Искать в интернете: /search\n"
             "• Мини-игры: /games\n• Профиль и токены: /profile\n• Тарифы: /plans\n• Поддержка: /support\n"
             "• <b>Команды .ai и .lc в твоих чатах</b>: /ai\n• Документы: /terms",
    "help": "ℹ️ <b>Что я умею</b>\n• Общаться и отвечать на вопросы (пиши как обычно)\n• Понимать фото и отвечать по ним\n"
            "• Рисовать картинки: /img\n• Писать код и файлы: /code\n• Статьи в Telegraph: /tg\n• Искать в интернете: /search\n"
            "• Мини-игры: /games\n• Профиль и токены: /profile\n• Тарифы: /plans\n• Поддержка: /support",
}
TEXT_LABELS = {
    "welcome2": "Приветствие ({greeting} и {name} подставятся сами)", "need_key": "Просьба ввести ключ",
    "key_ok": "Ключ принят", "key_bad": "Неверный ключ", "banned": "Сообщение блокировки",
    "plans_title": "Заголовок тарифов", "plans_note": "Примечание под тарифами", "agree_text": "Текст перед оплатой",
    "paid_ok": "Оплата прошла ({plan}, {date})", "requisites": "Реквизиты для перевода",
    "img_ask": "Запрос описания картинки", "tg_ask": "Запрос темы статьи", "support_ask": "Запрос в поддержку",
    "code_help": "Подсказка по коду", "expire_soon": "Напоминание о конце подписки ({plan}, {date}, {left})",
    "expire_now": "Подписка закончилась ({plan})", "help2": "Текст помощи", "ai_info": "Описание команд .ai и .lc",
}
# ключ: (текст, premium-эмодзи, цвет)
BTNS = {"models": ("Модели", "", "primary"), "img": ("Картинки", "", "success"), "code": ("Файлы и код", "", "primary"),
        "tg": ("Telegraph", "", "success"), "search": ("Поиск в сети", "", "primary"), "games": ("Мини-игры", "", "success"),
        "profile": ("Профиль", "", "primary"), "plans": ("Тарифы", "", "success"), "support": ("Поддержка", "", "primary"),
        "mysettings": ("Мои настройки", "", "primary"), "clear": ("Очистить память", "", "danger"),
        "docs": ("Соглашение и политика", "", "primary"), "aiinfo": ("Команды .ai и .lc", "", "success")}

SET_DEF = {
    "sys3": DEF_SYS, "max_out": "1500", "hist_len": "6", "img_quality": "low", "premium_only": "1",
    "banner_answers": "1", "img_model": "gpt-image-2", "avatar_emoji": "1",
    "code_rounds": "3", "code_tokens": "4000", "show_tokens": "1", "require_key": "0", "tz": "3",
    "stars_per_rub": "0.85", "rub_per_usdt": "95", "default_city": "Москва",
    "biz_model_free": "deepseek-v4-flash", "biz_model_basic": "gemini-3.6-flash", "biz_model_pro": "gemini-3.6-flash", "biz_delete_cmd": "1",
    "webapp_chess": "https://fertenkbs.github.io/mysite/",
    "terms_url": "https://telegra.ph/Polzovatelskoe-soglashenie-10-20-28",
    "privacy_url": "https://telegra.ph/Politika-konfidencialnosti-10-20-69",
    "api_base": "", "api_key": "", "backup_url": "", "backup_key": "", "backup_models": "", "disabled_models": "",
    "article_model": "deepseek-v4-flash", "article_images": "1", "fast_mode": "1", "autobackup": "1", "backup_hours": "6",
    "webapp_code": "", "last_backup_hash": "", "platega_id": "", "platega_secret": "", "platega_url": "https://app.platega.io",
    "platega_methods": "2:СБП (QR)", "public_url": "", "code_max_mult": "1.5", "webapp_ttt": "https://fertenkbs.github.io/mysite/tictactoe.html", "banner_id": "", "banner_type": "", "tg_token": "", "styles_v3": "0",
}
SET_LABELS = {
    "sys3": "Системная инструкция ИИ", "max_out": "Макс. длина ответа (токены)", "hist_len": "Память: сколько сообщений помнить",
    "img_quality": "Качество картинок (low/medium/high/auto)", "premium_only": "Только премиум-эмодзи (1 или 0)",
    "banner_answers": "Баннер на коротких ответах (1 или 0)", "img_model": "Модель картинок по умолчанию",
    "avatar_emoji": "Эмодзи из аватарки пользователя (1 или 0)",
    "code_rounds": "Код: максимум частей ответа (меньше = экономнее)", "code_tokens": "Код: токенов на одну часть (4000 ≈ 300 строк)", "show_tokens": "Показывать расход токенов (1 или 0)",
    "require_key": "Требовать секретный ключ для входа (1 или 0)", "tz": "Часовой пояс (UTC+ часов)",
    "stars_per_rub": "Звёзд Telegram за 1 ₽",
    "rub_per_usdt": "Курс: ₽ за 1 USDT", "default_city": "Город по умолчанию (погода)",
    "biz_model_free": "Бизнес-режим: модель для Free", "biz_model_basic": "Бизнес-режим: модель для Basic",
    "biz_model_pro": "Бизнес-режим: модель для Pro",
    "biz_delete_cmd": "Бизнес-режим: удалять команду .ai после ответа (1 или 0)", "webapp_chess": "Ссылка на мини-апп «Шахматы»", "webapp_code": "Ссылка на мини-апп «Студия кода» (если лежит на GitHub)",
    "article_model": "Статьи Telegraph: модель текста (дешёвая)", "article_images": "Статьи Telegraph: макс. картинок (0 = без)",
    "fast_mode": "Быстрый режим моделей (1 или 0)", "backup_hours": "Автобэкап: раз в сколько часов", "webapp_ttt": "Ссылка на мини-апп «Крестики-нолики»",
    "public_url": "Публичный адрес бота https://… (для студии кода и Platega)", "code_max_mult": "Код: макс. коэффициент модели (1.5)",
    "terms_url": "Ссылка: пользовательское соглашение", "privacy_url": "Ссылка: политика конфиденциальности",
}
# код, название, токенов/мес, макс. коэффициент, картинки, список моделей, цены 30д/6м/1г (₽), порядок
TARIFF_SEED = [
    ("free", "Free", 100000, 999, 1, "", 0, 0, 0, 0),
    ("basic", "Basic", 1000000, 999, 1, "", 50, 250, 550, 1),
    ("pro", "Pro", 5000000, 999, 1, "", 100, 550, 1150, 2),
]
USER_COLS = ["voice INT DEFAULT 0", "plan TEXT DEFAULT 'free'", "plan_until INT DEFAULT 0", "period_start INT DEFAULT 0",
             "tok_used INT DEFAULT 0", "bonus INT DEFAULT 0", "tok_total INT DEFAULT 0", "tok_raw INT DEFAULT 0",
             "req_count INT DEFAULT 0", "img_model TEXT DEFAULT ''", "code_model TEXT DEFAULT ''",
             "length TEXT DEFAULT 'mid'", "memory INT DEFAULT 1", "custom_prompt TEXT DEFAULT ''",
             "banned INT DEFAULT 0", "agreed INT DEFAULT 0", "activated INT DEFAULT 0", "search INT DEFAULT 0",
             "mode TEXT DEFAULT ''", "notified INT DEFAULT 0", "quiz INT DEFAULT 0",
             "lc_style TEXT DEFAULT 'short'", "lc_custom TEXT DEFAULT ''", "lc_avatar INT DEFAULT 0",
             "avatar_opt INT DEFAULT 0", "avatar_emoji_id TEXT DEFAULT ''", "avatar_uid TEXT DEFAULT ''"]


# ---------------------- БАЗА ----------------------
async def q(sql, args=(), one=False, commit=False):
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, args)
        if commit:
            await db.commit()
            return cur.lastrowid
        rows = await cur.fetchall()
        return (rows[0] if rows else None) if one else rows


async def init_db():
    for s in [
        "CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, name TEXT, joined INT, model TEXT)",
        "CREATE TABLE IF NOT EXISTS texts(key TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE IF NOT EXISTS buttons(key TEXT PRIMARY KEY, text TEXT, emoji TEXT, style TEXT)",
        "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE IF NOT EXISTS emoji_pool(id INTEGER PRIMARY KEY AUTOINCREMENT, cid TEXT UNIQUE, plain TEXT)",
        "CREATE TABLE IF NOT EXISTS tariffs(code TEXT PRIMARY KEY, name TEXT, tokens INT, max_mult REAL, img_ok INT, models TEXT, p30 INT, p180 INT, p365 INT, sort INT)",
        "CREATE TABLE IF NOT EXISTS grants(uid INT, model TEXT, tokens INT, PRIMARY KEY(uid, model))",
        "CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INT, plan TEXT, days INT, rub INT, method TEXT, status TEXT, ext TEXT DEFAULT '', created INT, paid_at INT DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INT, status TEXT, created INT)",
        "CREATE TABLE IF NOT EXISTS ticket_msgs(id INTEGER PRIMARY KEY AUTOINCREMENT, tid INT, from_admin INT, body TEXT, ts INT)",
        "CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INT, role TEXT, content TEXT)",
        "CREATE TABLE IF NOT EXISTS model_banners(model TEXT PRIMARY KEY, typ TEXT, fid TEXT)",
        "CREATE TABLE IF NOT EXISTS biz(conn TEXT PRIMARY KEY, uid INT)",
        "CREATE TABLE IF NOT EXISTS lc_chats(conn TEXT, chat INT, PRIMARY KEY(conn, chat))"]:
        await q(s, commit=True)
    for col in USER_COLS:
        try:
            await q("ALTER TABLE users ADD COLUMN " + col, commit=True)
        except Exception:
            pass
    for k, v in TEXTS.items():
        await q("INSERT OR IGNORE INTO texts VALUES(?,?)", (k, v), commit=True)
    for k, (t, e, st) in BTNS.items():
        await q("INSERT OR IGNORE INTO buttons VALUES(?,?,?,?)", (k, t, e, st), commit=True)
    for k, v in SET_DEF.items():
        await q("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v), commit=True)
    for t in TARIFF_SEED:
        await q("INSERT OR IGNORE INTO tariffs VALUES(?,?,?,?,?,?,?,?,?,?)", t, commit=True)
    if await setting("styles_v3") != "1":  # один раз: красим кнопки по умолчанию
        for k, (t, e, st) in BTNS.items():
            await q("UPDATE buttons SET style=? WHERE key=? AND style=''", (st, k), commit=True)
        await set_setting("styles_v3", "1")
    if await setting("img_model") not in IMG_MODELS:  # убранные модели картинок заменяем рабочей
        await set_setting("img_model", "gpt-image-2")
    await q("UPDATE users SET img_model='' WHERE img_model!='' AND img_model NOT IN (%s)" % ",".join("?" * len(IMG_MODELS)), tuple(IMG_MODELS), commit=True)
    if await setting("migr_v6") != "1":  # один раз: все функции и модели на любых тарифах, тексты без голоса
        await q("UPDATE tariffs SET max_mult=999, img_ok=1, models='' WHERE code IN ('free','basic','pro')", commit=True)
        for key, old, new in (("welcome2", "говори голосом, ", ""), ("help2", "Понимать голосовые, кружки, видео и фото", "Понимать фото и отвечать по ним"),
                              ("help", "Понимать голосовые, кружки, видео и фото", "Понимать фото и отвечать по ним")):
            r = await q("SELECT value FROM texts WHERE key=?", (key,), one=True)
            if r and old in r["value"]:
                await q("UPDATE texts SET value=? WHERE key=?", (r["value"].replace(old, new), key), commit=True)
        r = await q("SELECT value FROM texts WHERE key='welcome2'", one=True)
        if r and "{avatar}" not in r["value"] and r["value"].startswith("{greeting}"):
            await q("UPDATE texts SET value=? WHERE key='welcome2'", ("{avatar} " + r["value"],), commit=True)
        await set_setting("migr_v6", "1")


# ---------------------- кэши, настройки, тарифы ----------------------
SC: dict = {}
TC: dict = {}
BC: dict = {}        # кнопки
BANNERS: dict = {}   # баннеры моделей
TARIFFS: dict = {}
MONTH = 30 * 86400
ADMIN_MODE: set = set()   # владелец в админке: ИИ молчит до /start
TEST_MODE = False         # /asuser: владелец видит бота как обычный пользователь


async def setting(k):
    if k not in SC:
        r = await q("SELECT value FROM settings WHERE key=?", (k,), one=True)
        SC[k] = r["value"] if r else SET_DEF.get(k, "")
    return SC[k]


async def set_setting(k, v):
    await q("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, v), commit=True)
    SC[k] = v


async def seti(k, d):
    try:
        return int(await setting(k))
    except Exception:
        return d


async def setf(k, d):
    try:
        return float((await setting(k)).replace(",", "."))
    except Exception:
        return d


async def text(k):
    if k not in TC:
        r = await q("SELECT value FROM texts WHERE key=?", (k,), one=True)
        TC[k] = r["value"] if r else TEXTS.get(k, k)
    return TC[k]


async def load_buttons():
    BC.clear()
    for r in await q("SELECT * FROM buttons"):
        BC[r["key"]] = dict(r)


async def load_banners():
    BANNERS.clear()
    for r in await q("SELECT * FROM model_banners"):
        BANNERS[r["model"]] = (r["typ"], r["fid"])


async def load_tariffs():
    TARIFFS.clear()
    for r in await q("SELECT * FROM tariffs ORDER BY sort, rowid"):
        TARIFFS[r["code"]] = dict(r)


def tariff(code):
    return TARIFFS.get(code) or TARIFFS.get("free") or {"code": "free", "name": "Free", "tokens": 0, "max_mult": 0,
                                                       "img_ok": 0, "models": "", "p30": 0, "p180": 0, "p365": 0}


ai_backup = None
API_STATE = {"down_until": 0.0, "notified": 0.0}
BMAP: dict = {}    # модели запасного API (подгружаются при первом обращении)
OFF: set = set()   # отключённые (нерабочие) модели


def load_disabled():
    OFF.clear()
    OFF.update(x for x in str(SC.get("disabled_models") or "").split(",") if x)


async def reload_client():
    global ai, ai_backup
    key = (await setting("api_key")) or API_KEY
    base = (await setting("api_base")) or BASE_URL
    ai = AsyncOpenAI(api_key=key, base_url=base, timeout=120, max_retries=1)
    bkey = (await setting("backup_key")) or BACKUP_API_KEY
    bbase = (await setting("backup_url")) or BACKUP_BASE_URL
    ai_backup = AsyncOpenAI(api_key=bkey, base_url=bbase, timeout=120, max_retries=1) if bkey else None
    BMAP.clear()


def api_order():
    """Порядок обращения к API: основной, затем запасной. Если основной недавно подвёл, сначала запасной."""
    if ai_backup is None:
        return [("primary", ai)]
    if time.time() < API_STATE["down_until"]:
        return [("backup", ai_backup), ("primary", ai)]
    return [("primary", ai), ("backup", ai_backup)]


def is_quota_error(e):
    s = str(e).lower()
    return getattr(e, "status_code", None) in (401, 402, 403, 429) or any(
        w in s for w in ("quota", "balance", "insufficient", "credit", "billing", "limit", "invalid api", "неверный api",
                         "баланс", "токен", "лимит", "exceeded", "payment", "connection", "timed out", "timeout"))


async def note_failure(name, e):
    if name != "primary" or ai_backup is None:
        return
    if is_quota_error(e):
        API_STATE["down_until"] = time.time() + 300  # 5 минут не мучаем основной ключ
    if time.time() - API_STATE["notified"] > 1800 and BOT and OWNER_ID:
        API_STATE["notified"] = time.time()
        try:
            await BOT.send_message(OWNER_ID, f"⚠️ Основной API не ответил ({str(e)[:160]}). Работаю через запасной ключ.")
        except Exception:
            pass


def is_model_error(e):
    """Ошибка именно в модели (нет такой / не поддерживается), а не в ключе, лимитах или сети."""
    if getattr(e, "status_code", None) in (401, 402, 403, 429):
        return False
    low = str(e).lower()
    return getattr(e, "status_code", None) == 404 or any(
        w in low for w in ("model not found", "model_not_found", "does not exist", "unknown model", "no such model",
                           "invalid model", "unsupported model", "model is not supported", "не найдена", "не существует"))


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


async def backup_model(model):
    """Название модели на запасном API: ручное соответствие, точное совпадение или похожее по написанию."""
    manual = dict(x.split("=", 1) for x in (await setting("backup_models")).split(",") if "=" in x)
    if model in manual:
        return manual[model].strip()
    if "_loaded" not in BMAP:
        BMAP["_loaded"] = {}
        try:
            r = await ai_backup.models.list()
            BMAP["_loaded"] = {_norm(m.id): m.id for m in r.data}
        except Exception as e:
            logging.warning("backup models: %s", e)
    return BMAP["_loaded"].get(_norm(model), model)


BG: set = set()
GROUP_OF = {m[1]: m[0] for m in CHAT_CATALOG}
GROUPS = list(dict.fromkeys(m[0] for m in CHAT_CATALOG))


def spawn(coro):
    """create_task без риска, что задачу соберёт сборщик мусора."""
    t = asyncio.create_task(coro)
    BG.add(t)
    t.add_done_callback(BG.discard)
    return t


# ---------------------- пользователи ----------------------
def exempt(uid):
    return uid == OWNER_ID and not TEST_MODE


async def get_user(uid):
    r = await q("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not r:
        return None
    u = dict(r)
    now = int(time.time())
    ch = {}
    if not u["period_start"]:
        u["period_start"] = ch["period_start"] = now
    elif now - u["period_start"] >= MONTH:
        k = (now - u["period_start"]) // MONTH
        u["period_start"] += k * MONTH
        u["tok_used"] = 0
        ch["period_start"], ch["tok_used"] = u["period_start"], 0
    if ch:
        await q("UPDATE users SET " + ",".join(f"{k}=?" for k in ch) + " WHERE id=?", (*ch.values(), uid), commit=True)
    return u


async def ensure_user(tu):
    await q("INSERT INTO users(id,username,name,joined,model,period_start) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET username=excluded.username, name=excluded.name",
            (tu.id, tu.username or "", tu.full_name, int(time.time()), DEFAULT_CHAT, int(time.time())), commit=True)


def eff_plan(u):
    p = u.get("plan") or "free"
    if p != "free" and (u.get("plan_until") or 0) <= time.time():
        p = "free"
    return p if p in TARIFFS else "free"


def plan_allows(t, model):
    lst = [x.strip() for x in (t.get("models") or "").split(",") if x.strip()]
    if lst:
        return model in lst
    return MULT.get(model, 1.0) <= float(t.get("max_mult") or 0)


def left_tokens(u, t):
    return max(0, int(t["tokens"]) - u["tok_used"]) + max(0, u["bonus"])


def fmt(n):
    return f"{int(n):,}".replace(",", " ")


def fdate(ts):
    return time.strftime("%d.%m.%Y", time.localtime(ts))


def next_reset(u):
    return fdate(u["period_start"] + MONTH)


def models_desc(t):
    lst = [x.strip() for x in (t.get("models") or "").split(",") if x.strip()]
    if lst:
        return "только " + ", ".join(TITLE.get(x, x) for x in lst)
    mm = float(t.get("max_mult") or 0)
    if mm >= 100:
        return "все модели"
    if str(mm).endswith(".99"):
        return f"модели с коэффициентом ниже ×{math.ceil(mm)}"
    return f"модели с коэффициентом до ×{mm:g}"


async def grant_left(uid, model):
    r = await q("SELECT tokens FROM grants WHERE uid=? AND model=?", (uid, model), one=True)
    return r["tokens"] if r else 0


async def can_use(uid, u, model, kind="chat"):
    """Можно ли пользоваться: (да/нет, сообщение)."""
    if exempt(uid):
        return True, ""
    if u["banned"]:
        return False, await text("banned")
    t = tariff(eff_plan(u))
    left = left_tokens(u, t)
    if kind == "image":
        if not t["img_ok"]:
            return False, "🖼 Генерация картинок доступна на тарифе Pro. Открой /plans"
        if left <= 0:
            return False, f"🪙 Токены закончились. Обновятся {next_reset(u)} или возьми тариф: /plans"
        return True, ""
    g = await grant_left(uid, model)
    if not plan_allows(t, model):
        if g > 0:
            return True, ""
        return False, (f"🔒 Модель <b>{TITLE.get(model, model)}</b> недоступна на тарифе <b>{t['name']}</b>.\n"
                       "Выбери другую: /models, или смени тариф: /plans")
    if left <= 0 and g <= 0:
        return False, f"🪙 Токены закончились. Обновятся <b>{next_reset(u)}</b> или возьми тариф: /plans"
    return True, ""


async def usable_model(uid, u, model, col):
    """Если модель недоступна на тарифе, подбираем доступную и запоминаем. -> (модель, заменили?)"""
    if exempt(uid):
        return model, False
    t = tariff(eff_plan(u))
    if plan_allows(t, model) or await grant_left(uid, model) > 0:
        return model, False
    pick = next((c for c in CHEAP if c not in OFF and plan_allows(t, c)), None) or next((m for m in MODELS if m not in OFF and plan_allows(t, m)), None)
    if not pick:
        return model, False
    await q(f"UPDATE users SET {col}=? WHERE id=?", (pick, uid), commit=True)
    return pick, True


async def spend(uid, u, model, raw, fixed=None, kind="chat"):
    """Списываем токены по факту (usage от API × коэффициент). -> (списано, осталось или None)"""
    mult = MULT.get(model, 1.0)
    charged = int(fixed) if fixed is not None else max(1, math.ceil(raw * mult))
    t = tariff(eff_plan(u))
    if not exempt(uid):
        g = await grant_left(uid, model) if kind != "image" else 0
        if g > 0 and not plan_allows(t, model):
            await q("UPDATE grants SET tokens=MAX(0,tokens-?) WHERE uid=? AND model=?", (charged, uid, model), commit=True)
        else:
            take = min(max(0, int(t["tokens"]) - u["tok_used"]), charged)
            await q("UPDATE users SET tok_used=tok_used+?, bonus=MAX(0,bonus-?) WHERE id=?",
                    (take, charged - take, uid), commit=True)
    await q("UPDATE users SET tok_total=tok_total+?, tok_raw=tok_raw+?, req_count=req_count+1 WHERE id=?",
            (charged, int(raw), uid), commit=True)
    if exempt(uid):
        return charged, None
    u2 = await get_user(uid)
    return charged, left_tokens(u2, t)


def footer_text(charged, left, mult, approx):
    s = f"🪙 −{fmt(charged)}"
    if mult != 1:
        s += f" (×{mult:g})"
    if left is not None:
        s += f" · осталось {fmt(left)}"
    return ("≈ " if approx else "") + s


def first_ok(pref, pool=None):
    """Первая рабочая модель из предпочтительных, иначе любая включённая."""
    pool = [m for m in (pool if pool is not None else MODELS) if m not in OFF]
    for c in pref:
        if c in pool:
            return c
    return pool[0] if pool else pref[0]


def cur_model(u):
    """Выбранная пользователем модель; если её отключили в админке, берём запасную."""
    return u["model"] if u["model"] in MODELS and u["model"] not in OFF else first_ok([DEFAULT_CHAT] + CHEAP)


def code_max():
    try:
        return float(str(SC.get("code_max_mult") or "1.5").replace(",", "."))
    except Exception:
        return 1.5


def code_models():
    """Для кода доступны только модели с коэффициентом не выше лимита (по умолчанию ×1.5)."""
    return [m for m in MODELS if MULT[m] <= code_max() and m not in OFF]


def cur_code_model(u):
    cm = code_models()
    return u["code_model"] if u["code_model"] in cm else first_ok([DEFAULT_CODE, DEFAULT_CHAT], cm)


async def cur_img_model(u):
    return u["img_model"] if u["img_model"] in IMG_MODELS else await setting("img_model")


async def grant_plan(uid, code, days):
    """Выдать тариф на N дней. -> дата окончания (timestamp)."""
    u = await get_user(uid)
    now = int(time.time())
    same = eff_plan(u) == code and code != "free"
    base = u["plan_until"] if same else now
    until = base + days * 86400
    if same:
        await q("UPDATE users SET plan=?, plan_until=?, notified=0 WHERE id=?", (code, until, uid), commit=True)
    else:
        await q("UPDATE users SET plan=?, plan_until=?, notified=0, tok_used=0, period_start=? WHERE id=?",
                (code, until, now, uid), commit=True)
    return until


# ---------------------- память диалога (хранится в базе) ----------------------
HIST: dict = {}
REPLIES: dict = {}
LOCKS: dict = {}
QUIZ: dict = {}


async def get_hist(uid):
    if uid not in HIST:
        rows = await q("SELECT role, content FROM history WHERE uid=? ORDER BY id DESC LIMIT 30", (uid,))
        HIST[uid] = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    return HIST[uid]


async def save_hist(uid, items):
    for it in items:
        await q("INSERT INTO history(uid,role,content) VALUES(?,?,?)", (uid, it["role"], it["content"][:4000]), commit=True)
    await q("DELETE FROM history WHERE uid=? AND id NOT IN (SELECT id FROM history WHERE uid=? ORDER BY id DESC LIMIT 30)",
            (uid, uid), commit=True)


async def clear_hist(uid):
    HIST.pop(uid, None)
    await q("DELETE FROM history WHERE uid=?", (uid,), commit=True)


# ---------------------- оформление ответов ----------------------
SUP = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
SUB = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
GREEK = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "theta": "θ", "lambda": "λ",
         "mu": "μ", "pi": "π", "sigma": "σ", "phi": "φ", "omega": "ω", "Delta": "Δ", "Sigma": "Σ", "Omega": "Ω", "Pi": "Π"}
SYM = {"cdot": "·", "times": "×", "div": "÷", "pm": "±", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠",
       "ne": "≠", "approx": "≈", "infty": "∞", "rightarrow": "→", "to": "→", "Rightarrow": "⇒", "leftarrow": "←",
       "sum": "Σ", "int": "∫", "degree": "°", "ldots": "…", "dots": "…", "cdots": "…", "equiv": "≡", "in": "∈",
       "cap": "∩", "cup": "∪", "partial": "∂", "nabla": "∇"}


def latex_to_text(x):
    for _ in range(6):
        x2 = re.sub(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}",
                    lambda m: "/".join(g if re.fullmatch(r"[\w.]+", g.strip()) else f"({g.strip()})" for g in m.groups()), x)
        x2 = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"√(\1)", x2)
        x2 = re.sub(r"\\(?:text|mathrm|mathbf|mathit|operatorname|textbf|boldsymbol)\s*\{([^{}]*)\}", r"\1", x2)
        if x2 == x:
            break
        x = x2
    x = re.sub(r"\\(?:left|right|bigg?|Bigg?)\b\s*", "", x)
    x = re.sub(r"\\[,;:! ]|\\q?quad", " ", x)
    x = re.sub(r"\^\{([^{}]*)\}", lambda m: m.group(1).translate(SUP)
               if all(c in "0123456789+-=()ni" for c in m.group(1)) else "^(" + m.group(1) + ")", x)
    x = re.sub(r"\^([0-9n])", lambda m: m.group(1).translate(SUP), x)
    x = re.sub(r"_\{([^{}]*)\}", lambda m: m.group(1).translate(SUB)
               if all(c in "0123456789+-=()" for c in m.group(1)) else "_" + m.group(1), x)
    x = re.sub(r"_([0-9])", lambda m: m.group(1).translate(SUB), x)
    x = re.sub(r"\\([A-Za-z]+)", lambda m: SYM.get(m.group(1)) or GREEK.get(m.group(1)) or m.group(1), x)
    x = x.replace("\\\\", "\n").replace("{", "").replace("}", "")
    return re.sub(r"[ \t]+", " ", x).strip()


def clean_math(s):
    s = re.sub(r"\$\$(.+?)\$\$", lambda m: "\n" + latex_to_text(m.group(1)) + "\n", s, flags=re.S)
    s = re.sub(r"\\\[(.+?)\\\]", lambda m: "\n" + latex_to_text(m.group(1)) + "\n", s, flags=re.S)
    s = re.sub(r"\\\((.+?)\\\)", lambda m: latex_to_text(m.group(1)), s, flags=re.S)
    s = re.sub(r"(?<![\\$\w])\$([^$\n]{1,200}?)\$(?!\w)",
               lambda m: latex_to_text(m.group(1)) if re.search(r"[\\^_{}]", m.group(1)) else m.group(0), s)
    if re.search(r"\\(?:[dt]?frac|sqrt|cdot|times|leq|geq)\b", s):
        s = re.sub(r"[^\n]*\\(?:[dt]?frac|sqrt|cdot|times|leq|geq)\b[^\n]*", lambda m: latex_to_text(m.group(0)), s)
    return s


PH = "\ue000"


def md_html(s):
    """Markdown/LaTeX из ответа модели -> чистый HTML для Telegram (без ***, ---, \\frac и т.п.)."""
    s = clean_math(s)
    stash = []

    def keep(h):
        stash.append(h)
        return f"{PH}{len(stash) - 1}{PH}"
    lines, out, i = s.split("\n"), [], 0
    while i < len(lines):
        if lines[i].strip().startswith("|") and lines[i].count("|") >= 2:
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            rows = [[c.strip() for c in ln.strip().strip("|").split("|")]
                    for ln in lines[i:j] if not re.fullmatch(r"\s*\|?[\s:\-|]+\|?\s*", ln)]
            if rows:
                n = max(len(r) for r in rows)
                w = [max(len(r[c]) if c < len(r) else 0 for r in rows) for c in range(n)]
                tbl = "\n".join("  ".join((r[c] if c < len(r) else "").ljust(w[c]) for c in range(n)).rstrip() for r in rows)
                out.append(keep(f"<pre>{H.escape(tbl)}</pre>"))
            i = j
            continue
        out.append(lines[i])
        i += 1
    s = "\n".join(out)
    s = re.sub(r"`([^`\n]+)`", lambda m: keep(f"<code>{H.escape(m.group(1))}</code>"), s)
    s = re.sub(r"^\s*(?:[-–—_*=]\s*){3,}$", "", s, flags=re.M)
    s = H.escape(s, quote=False)
    s = re.sub(r"^\s{0,3}#{1,6}\s*(.+?)\s*#*$", r"<b>\1</b>", s, flags=re.M)
    s = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s, flags=re.S)
    s = re.sub(r"__(.+?)__", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"(?<![\w_])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w_])", r"<i>\1</i>", s)
    s = re.sub(r"~~(.+?)~~", r"<s>\1</s>", s)
    s = re.sub(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)",
               lambda m: f'<a href="{m.group(2).replace(chr(34), "%22")}">{m.group(1)}</a>', s)
    s = re.sub(r"^\s*[-*+]\s+", "• ", s, flags=re.M)
    s = re.sub(r"^&gt;\s?(.*)$", r"<blockquote>\1</blockquote>", s, flags=re.M)
    s = s.replace("</blockquote>\n<blockquote>", "\n")
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return re.sub(PH + r"(\d+)" + PH, lambda m: stash[int(m.group(1))], s)


def chunk_text(s, limit=3000):
    out = []
    while len(s) > limit:
        cut = s.rfind("\n\n", 0, limit)
        if cut < limit // 3:
            cut = s.rfind("\n", 0, limit)
        if cut < limit // 3:
            cut = limit
        out.append(s[:cut])
        s = s[cut:].lstrip("\n")
    out.append(s)
    return [x for x in out if x.strip()]


EMO_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2B06\u2B1B\u2B1C]\uFE0F?")
POOL = None
POOL_I = 0


async def get_pool():
    global POOL
    if POOL is None:
        POOL = [(r["cid"], r["plain"]) for r in await q("SELECT cid, plain FROM emoji_pool ORDER BY id")]
    return POOL


async def emoji_filter(s):
    pool = await get_pool()
    only = await setting("premium_only") == "1"
    if not pool and not only:
        return s
    by_plain = {}
    for cid, pl in pool:
        by_plain.setdefault(pl, []).append(cid)

    def rep(m):
        global POOL_I
        e = m.group(0).replace("\uFE0F", "")
        if e in by_plain:
            return f'<tg-emoji emoji-id="{random.choice(by_plain[e])}">{e}</tg-emoji>'
        if only:
            if not pool:
                return ""
            cid, pl = pool[POOL_I % len(pool)]
            POOL_I += 1
            return f'<tg-emoji emoji-id="{cid}">{pl}</tg-emoji>'
        return m.group(0)
    parts = re.split(r"(<code>.*?</code>|<pre>.*?</pre>)", s, flags=re.S)
    return "".join(p if p.startswith(("<code>", "<pre>")) else EMO_RE.sub(rep, p) for p in parts)


# ---------------------- баннеры, ответы, правка экранов ----------------------
SHOWN: dict = {}   # (чат, id сообщения) -> file_id баннера, который сейчас на сообщении


async def get_banner(model=None):
    """Баннер для модели: сама модель -> её компания -> «все модели» -> общий."""
    if model:
        for key in (model, GROUP_OF.get(model), "@models"):
            if key and key in BANNERS:
                return BANNERS[key]
    fid = await setting("banner_id")
    return (await setting("banner_type"), fid) if fid else (None, None)


async def screen_banner(key):
    """None: общий баннер; '@pick': экран выбора компании ИИ; иначе id модели."""
    if key == "@pick" and "@pick" in BANNERS:
        return BANNERS["@pick"]
    return await get_banner(None if key == "@pick" else key)


def media_obj(typ, fid, caption):
    cls = {"photo": InputMediaPhoto, "video": InputMediaVideo}.get(typ, InputMediaAnimation)
    return cls(media=fid, caption=caption, parse_mode="HTML")


async def send_banner(m, typ, fid, txt, reply_markup):
    send = {"photo": m.answer_photo, "video": m.answer_video}.get(typ, m.answer_animation)
    r = await send(fid, caption=txt, reply_markup=reply_markup)
    SHOWN[(r.chat.id, r.message_id)] = fid
    return r


async def reply(m, txt, reply_markup=None, model=None):
    """Сообщение с баннером (если он задан и подпись влезает в 1024 знака)."""
    typ, fid = await get_banner(model)  # model=None: экран бота, только общий баннер; баннеры компаний и «всех моделей» только для моделей
    if fid and len(re.sub(r"<[^>]+>", "", txt)) <= 1000:
        try:
            return await send_banner(m, typ, fid, txt, reply_markup)
        except Exception as e:
            logging.warning("banner: %s", e)
    return await m.answer(txt, reply_markup=reply_markup)


async def edit_msg(msg, txt, reply_markup=None, banner=None, plain=False):
    """Обновить экран. Баннер подставляется сам: общий, для выбора компании ('@pick') или модели.
    plain=True: без баннера (экраны админки)."""
    has_media = bool(msg.photo or msg.video or msg.animation)
    typ, fid = (None, None) if plain else await screen_banner(banner)
    if fid and len(re.sub(r"<[^>]+>", "", txt)) > 1000:
        fid = None  # подпись к медиа не длиннее 1024 знаков
    key = (msg.chat.id, msg.message_id)
    try:
        if fid and has_media:
            try:
                if SHOWN.get(key) == fid:
                    return await msg.edit_caption(caption=txt, reply_markup=reply_markup)
                r = await msg.edit_media(media=media_obj(typ, fid, txt), reply_markup=reply_markup)
                SHOWN[key] = fid
                return r
            except Exception as e:
                if "not modified" in str(e).lower():
                    return None
                logging.info("edit_media: %s", e)
        if fid:  # было текстовое сообщение (или медиа не заменилось): шлём новое с баннером, старое убираем
            r = await send_banner(msg, typ, fid, txt, reply_markup)
            await safe_delete(msg)
            return r
        if has_media:  # баннер больше не нужен
            r = await msg.answer(txt, reply_markup=reply_markup)
            await safe_delete(msg)
            return r
        return await msg.edit_text(txt, reply_markup=reply_markup)
    except Exception as e:
        if "not modified" in str(e).lower():
            return None
        logging.warning("edit: %s", e)
        try:
            return await msg.answer(txt, reply_markup=reply_markup)
        except Exception:
            return None


async def safe_delete(msg):
    try:
        await msg.delete()
    except Exception:
        pass


FILE_RE = re.compile(r"<<<FILE:\s*(.+?)>>>\n?(.*?)<<<END>>>", re.S)


async def send_html(m, html_):
    try:
        return await m.answer(html_)
    except Exception:
        return await m.answer(H.escape(re.sub(r"<[^>]+>", "", html_))[:4000], parse_mode=None)


async def send_answer(m, answer, model=None, footer="", sm=None):
    files = [(n.strip(), c.rstrip("\n")) for n, c in FILE_RE.findall(answer)]
    shown = FILE_RE.sub(lambda x: f"\n📎 {x.group(1).strip()}\n", answer)
    parts = re.split(r"```(\w*)\n?(.*?)```", shown, flags=re.S)
    segs = []
    for i in range(0, len(parts), 3):
        if parts[i].strip():
            segs.append(("t", "", parts[i].strip()))
        if i + 2 < len(parts):
            segs.append(("c", parts[i + 1], parts[i + 2].rstrip("\n")))
    foot = f"\n\n<i>{footer}</i>" if footer else ""
    if not files and len(segs) == 1 and segs[0][0] == "t":
        full = await emoji_filter(md_html(segs[0][2])) + foot
        plain = len(re.sub(r"<[^>]+>", "", full))
        if plain <= 1000 and await setting("banner_answers") == "1" and (await get_banner(model))[1]:
            try:
                if sm:
                    await safe_delete(sm)
                    sm = None
                r = await reply(m, full, model=model)
                return [r.message_id]
            except Exception as e:
                logging.warning("banner answer: %s", e)
        if sm and len(full) <= 4000:
            try:
                await sm.edit_text(full)
                return [sm.message_id]
            except Exception as e:
                if "not modified" in str(e).lower():
                    return [sm.message_id]
                try:
                    await sm.edit_text(H.escape(re.sub(r"<[^>]+>", "", full))[:4000], parse_mode=None)
                    return [sm.message_id]
                except Exception:
                    pass
    if sm:
        await safe_delete(sm)
    ids = []
    for kind, lang, c in segs:
        if kind == "c":
            if len(c) > 3500:
                ext = {"python": "py", "py": "py", "js": "js", "html": "html"}.get(lang, lang or "txt")
                d = await m.answer_document(BufferedInputFile(c.encode(), f"code.{ext}"))
                ids.append(d.message_id)
                continue
            body = (f'<pre><code class="language-{lang}">{H.escape(c)}</code></pre>' if lang else f"<pre>{H.escape(c)}</pre>")
            ids.append((await send_html(m, body)).message_id)
        else:
            for ch in chunk_text(c):
                ids.append((await send_html(m, await emoji_filter(md_html(ch)))).message_id)
    for name, content in files:
        d = await m.answer_document(BufferedInputFile(content.encode(), name.split("/")[-1]), caption=f"📎 {H.escape(name)}")
        ids.append(d.message_id)
    if len(files) > 1:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, content in files:
                z.writestr(name, content)
        d = await m.answer_document(BufferedInputFile(buf.getvalue(), "project.zip"), caption="📦 Всё архивом")
        ids.append(d.message_id)
    if footer:
        ids.append((await send_html(m, f"<i>{footer}</i>")).message_id)
    return ids


# ---------------------- ядро ИИ ----------------------
VARIANT: dict = {}


def est_tokens(msgs, out):
    n = len(out)
    for x in msgs:
        c = x["content"]
        n += len(c) if isinstance(c, str) else 3000
    return math.ceil(n / 2.6)  # запас: для русского токенов больше, чем символов/4


VAR_WORDS = ("max_tokens", "max_completion", "stream_options", "reasoning", "unsupported", "not supported", "unrecognized", "unknown parameter", "invalid param", "extra inputs")


async def _stream_with(client, cname, model, msgs, max_tokens, cb):
    fast = await setting("fast_mode") == "1"
    so = {"include_usage": True}
    variants = [{"max_tokens": max_tokens, "stream_options": so, "reasoning_effort": "low"},
                {"max_tokens": max_tokens, "stream_options": so}, {"max_tokens": max_tokens}, {"max_completion_tokens": max_tokens}]
    start = VARIANT.get((cname, model), 0)
    if not fast:
        start = max(start, 1)
    stream = None
    for i in range(start, len(variants)):
        try:
            stream = await client.chat.completions.create(model=model, messages=msgs, stream=True, **variants[i])
            VARIANT[(cname, model)] = i
            break
        except Exception as e:
            if getattr(e, "status_code", 0) in (401, 402, 403, 429) or i == len(variants) - 1 or not any(w in str(e).lower() for w in VAR_WORDS):
                raise
    out, finish, used, last = "", None, 0, 0.0
    async for ch in stream:
        if getattr(ch, "usage", None) and ch.usage.total_tokens:
            used = ch.usage.total_tokens          # реальный расход по данным провайдера
        if ch.choices:
            c = ch.choices[0]
            if c.delta and c.delta.content:
                out += c.delta.content
                if cb and time.time() - last > 0.6:
                    last = time.time()
                    await cb(out)
            if c.finish_reason:
                finish = c.finish_reason
    approx = not used
    if approx:
        used = est_tokens(msgs, out)
    return out, finish, used, approx


async def stream_once(model, msgs, max_tokens, cb):
    """Основной API, а если он не ответил или токены кончились, то запасной."""
    first, errs = None, []
    for cname, cl in api_order():
        m = model if cname == "primary" else await backup_model(model)
        try:
            res = await _stream_with(cl, cname, m, msgs, max_tokens, cb)
            MODEL_FAILS.pop(model, None)
            return res
        except Exception as e:
            first = first or e
            errs.append(e)
            logging.warning("API %s (%s): %s", cname, m, str(e)[:200])
            await note_failure(cname, e)
    first.all_errs = errs
    await note_model_failure(model, errs)  # модель стабильно «не найдена» → сама отключится
    raise first


async def chat_long(messages, model, rounds=1, max_tokens=1500, on_delta=None):
    """-> (текст, реально потрачено токенов, оценка?)"""
    out, used, approx = "", 0, False
    msgs = list(messages)
    for _ in range(rounds):
        cb = (lambda t, b=out: on_delta(b + t)) if on_delta else None
        part, finish, u, ap = await stream_once(model, msgs, max_tokens, cb)
        out += part
        used += u
        approx = approx or ap
        if finish != "length":
            break
        msgs = list(messages) + [{"role": "assistant", "content": part[-8000:]},
                                 {"role": "user", "content": "Продолжи ровно с места обрыва, без повторов и вступлений."}]
    if finish == "length" and rounds > 1:  # дошли до лимита: закрываем файл и предупреждаем
        if out.count("<<<FILE") > out.count("<<<END>>>"):
            out += "\n<<<END>>>"
        out += "\n\n✂️ Достигнут лимит длины кода (так сделано для экономии токенов). Напиши, что дописать, и я продолжу."
    return out, used, approx


# ---------------------- картинки ----------------------
IMG_URL_RE = re.compile(r"https?://[^\s\"')\\]+?\.(?:png|jpe?g|webp)[^\s\"')\\]*", re.I)
DATA_RE = re.compile(r"data:image/[a-z+]+;base64,[A-Za-z0-9+/=]+")
NO_QUALITY: set = set()


async def _one_image(cl, model, prompt):
    out = []
    try:
        kw = {}
        ql = await setting("img_quality")
        if model.startswith("gpt-image") and ql and ql != "auto" and model not in NO_QUALITY:
            kw["quality"] = ql
        try:
            r = await cl.images.generate(model=model, prompt=prompt, n=1, **kw)
        except Exception:
            if not kw:
                raise
            NO_QUALITY.add(model)
            r = await cl.images.generate(model=model, prompt=prompt, n=1)
        for d in r.data:
            if getattr(d, "b64_json", None):
                out.append(BufferedInputFile(base64.b64decode(d.b64_json), "img.png"))
            elif getattr(d, "url", None):
                out.append(d.url)
    except Exception as e:
        logging.warning("images.generate %s: %s", model, e)
        try:  # запасной путь: часть моделей отдаёт картинку через чат
            r = await cl.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}])
            raw = json.dumps(r.model_dump(), ensure_ascii=False)
            for x in DATA_RE.findall(raw)[:1]:
                out.append(BufferedInputFile(base64.b64decode(x.split(",", 1)[1]), "img.png"))
            if not out:
                out += IMG_URL_RE.findall(raw.replace("\\/", "/"))[:1]
        except Exception:
            pass
        if not out:
            raise e
    return out


async def one_image(prompt, model):
    first = None
    for cname, cl in api_order():
        m = model if cname == "primary" else await backup_model(model)
        try:
            return await _one_image(cl, m, prompt)
        except Exception as e:
            first = first or e
            await note_failure(cname, e)
    raise first


async def gen_images(prompt, n=1, model=None):
    model = model or await setting("img_model")
    res = await asyncio.gather(*[one_image(prompt, model) for _ in range(n)], return_exceptions=True)
    out = [i for r in res if isinstance(r, list) for i in r]
    if not out:
        for r in res:
            if isinstance(r, Exception):
                raise r
    return out


# ---------------------- поиск в интернете ----------------------
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
_http = None


async def http():
    global _http
    if _http is None or _http.closed:
        _http = aiohttp.ClientSession(headers=UA, timeout=aiohttp.ClientTimeout(total=10))
    return _http


def strip_tags(x):
    return H.unescape(re.sub(r"<[^>]+>", "", x)).strip()


async def ddg(query, n=5):
    s = await http()
    async with s.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "ru-ru"}) as r:
        page = await r.text()
    res = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', page, re.S):
        href, title, snip = m.groups()
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href if href.startswith("http") else "https:" + href).query).get("uddg", [href])[0])
        res.append((strip_tags(title), href, strip_tags(snip)))
        if len(res) >= n:
            break
    return res


async def wiki(query, n=4):
    s = await http()
    async with s.get("https://ru.wikipedia.org/w/api.php", params={"action": "query", "list": "search", "srsearch": query,
                                                                   "format": "json", "srlimit": n}) as r:
        j = await r.json(content_type=None)
    return [(x["title"], "https://ru.wikipedia.org/wiki/" + quote(x["title"].replace(" ", "_")), strip_tags(x["snippet"]))
            for x in j.get("query", {}).get("search", [])]


async def search_block(query):
    try:
        res = await ddg(query)
    except Exception as e:
        logging.warning("ddg: %s", e)
        res = []
    if not res:
        res = await wiki(query)
    return "Результаты поиска:\n" + "\n".join(f"- {t}: {sn[:200]} ({u})" for t, u, sn in res) if res else ""


async def news_block(query):
    url = "https://news.google.com/rss/search?q=" + quote(query) + "&hl=ru&gl=RU&ceid=RU:ru" if query.strip() \
        else "https://news.google.com/rss?hl=ru&gl=RU&ceid=RU:ru"
    s = await http()
    async with s.get(url) as r:
        root = ET.fromstring(await r.text())
    items = root.findall(".//item")[:6]
    return "Свежие новости:\n" + "\n".join(f"- {strip_tags(i.findtext('title') or '')} ({i.findtext('pubDate') or ''}) {i.findtext('link') or ''}"
                                          for i in items) if items else ""


async def weather_block(city):
    s = await http()
    async with s.get(f"https://wttr.in/{quote(city)}", params={"format": "j1", "lang": "ru"}) as r:
        j = await r.json(content_type=None)
    cc = j["current_condition"][0]
    area = j["nearest_area"][0]["areaName"][0]["value"]
    desc = (cc.get("lang_ru") or cc.get("weatherDesc") or [{"value": ""}])[0]["value"]
    days = "; ".join(f"{d['date']}: {d['mintempC']}…{d['maxtempC']}°C" for d in j.get("weather", [])[:3])
    return (f"Погода, {area}: {cc['temp_C']}°C (ощущается {cc['FeelsLikeC']}°C), {desc}, влажность {cc['humidity']}%, "
            f"ветер {cc['windspeedKmph']} км/ч. Местное время: {cc.get('localObsDateTime', '?')}. Прогноз: {days}")


def extract_city(t):
    w = r"[А-ЯЁA-Z][а-яёa-z]+(?:-[А-ЯЁA-Za-zа-яё]+)*"
    m = re.search(r"(?:\bв|\bво|\bдля|\bпо)\s+(" + w + r"(?:\s" + w + r")?)", t)
    return m.group(1) if m else None


async def now_line():
    tz = await seti("tz", 3)
    t = time.gmtime(time.time() + tz * 3600)
    return f"Сейчас {time.strftime('%d.%m.%Y %H:%M', t)} (UTC{tz:+d})."


def web_intent(t):
    t = t.lower()
    if re.search(r"погод|температур|прогноз", t):
        return "weather"
    if re.search(r"сколько времени|который час|текущее время|время в |сколько сейчас", t):
        return "time"
    if "новост" in t:
        return "news"
    if re.search(r"найди|поищи|загугли|в интернете|в инете|в сети|курс (доллара|евро|валют)|актуальн|кто сейчас", t):
        return "search"
    return None


async def web_context(t, force=False):
    kind = web_intent(t) or ("search" if force else None)
    if not kind:
        return None
    parts = [await now_line()]
    try:
        if kind == "weather":
            parts.append(await weather_block(extract_city(t) or await setting("default_city")))
        elif kind == "time":
            city = extract_city(t)
            if city:
                parts.append(await weather_block(city))
        elif kind == "news":
            q_ = re.sub(r"(?i)\b(последние|свежие|покажи|расскажи|какие|новости|новость|про|об|о|по|за сегодня|сегодня)\b", " ", t).strip()
            parts.append(await news_block(q_))
        else:
            parts.append(await search_block(t))
    except Exception as e:
        logging.warning("web_context: %s", e)
    ctx = "\n".join(p for p in parts if p)
    return ctx[:2500] if len(parts) > 1 else (ctx if kind == "time" else None)


# ---------------------- статья в Telegraph ----------------------
async def make_article(uid, u, topic, model, max_images):
    """-> (url, потрачено токенов-тарифа). Бросает исключение при ошибке."""
    prompt = ('Напиши статью по теме и верни ТОЛЬКО JSON: {"title":"...","blocks":[{"type":"h","text":"..."},'
              '{"type":"p","text":"..."},{"type":"img","prompt":"описание картинки"}]}. '
              + (f"ровно {max_images} картинка, " if max_images else "без картинок, ") + f"6-10 абзацев. Тема: {topic}")
    raw, used, _ = await chat_long([{"role": "user", "content": prompt}], model, 2, 3500)
    data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    nodes, charged, imgs_done = [], max(1, math.ceil(used * MULT.get(model, 1.0))), 0
    ses = await http()
    tok = await setting("tg_token")
    if not tok:
        async with ses.post("https://api.telegra.ph/createAccount", data={"short_name": "GimbekAI", "author_name": "Gimbek AI"}) as r:
            tok = (await r.json())["result"]["access_token"]
        await set_setting("tg_token", tok)
    img_model = await cur_img_model(u)
    for b in data["blocks"]:
        if b["type"] == "p":
            nodes.append({"tag": "p", "children": [b["text"]]})
        elif b["type"] == "h":
            nodes.append({"tag": "h3", "children": [b["text"]]})
        elif b["type"] == "img" and imgs_done < max_images:
            imgs_done += 1
            try:
                im = (await gen_images(b["prompt"], 1, img_model))[0]
                charged += IMG_COST.get(img_model, 50000)
                if isinstance(im, BufferedInputFile):
                    fd = aiohttp.FormData()
                    fd.add_field("file", im.data, filename="i.png", content_type="image/png")
                    async with ses.post("https://telegra.ph/upload", data=fd) as r:
                        src = "https://telegra.ph" + (await r.json(content_type=None))[0]["src"]
                else:
                    src = im
                nodes.append({"tag": "img", "attrs": {"src": src}})
            except Exception as e:
                logging.warning("tg img: %s", e)
    async with ses.post("https://api.telegra.ph/createPage", data={
            "access_token": tok, "title": data["title"][:256], "content": json.dumps(nodes), "return_content": "false"}) as r:
        res = await r.json()
    return res["result"]["url"], charged


# ---------------------- кнопки и клавиатуры ----------------------
def auto_style(t):
    if any(x in t for x in ("🗑", "Убрать", "Очистить", "Сбросить", "Отклонить", "Бан", "Удалить", "❌", "Отменить")):
        return "danger"
    if any(x in t for x in ("➕", "✅", "Оплатить", "Скопировать", "Одобрить", "Выдать", "Проверить", "Принять", "согласен", "Продлить")):
        return "success"
    return "primary"


def IB(text, callback_data=None, url=None, style=None, **kw):
    kw["style"] = style or auto_style(text)
    if url:
        kw["url"] = url
    if callback_data is not None:
        kw["callback_data"] = callback_data
    return _IKB(text=text, **kw)


def kb(rows, back="adm:main"):
    out = []
    for r in rows:
        line = []
        for item in r:
            t, d = item[0], item[1]
            st = item[2] if len(item) > 2 else None
            if d.startswith("http"):
                line.append(IB(t, url=d, style=st))
            elif d.startswith("wa:"):
                line.append(IB(t, web_app=WebAppInfo(url=d[3:]), style=st))
            else:
                line.append(IB(t, d, style=st))
        out.append(line)
    if back:
        out.append([IB("⬅️ Назад", back, style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=out)


def btn(key, cb):
    r = BC.get(key) or {"text": BTNS[key][0], "emoji": "", "style": BTNS[key][2]}
    kw = {"icon_custom_emoji_id": r["emoji"]} if r["emoji"] else {}
    return IB(r["text"], cb, style=r["style"] or None, **kw)


async def main_kb(uid):
    rows = [[btn("models", "m:models"), btn("img", "m:img")], [btn("code", "m:code"), btn("tg", "m:tg")],
            [btn("search", "m:search"), btn("games", "m:games")], [btn("profile", "m:profile"), btn("plans", "m:plans")],
            [btn("support", "m:support"), btn("mysettings", "m:set")], [btn("docs", "m:docs"), btn("aiinfo", "m:ai")],
            [btn("clear", "m:clear")]]
    if uid == OWNER_ID:
        rows.append([IB("🛠 Админ-панель", "adm:main", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class Track(BaseMiddleware):
    async def __call__(self, handler, event, data):
        u = getattr(event, "from_user", None)
        if u and not u.is_bot:
            await ensure_user(u)
        return await handler(event, data)


class G(StatesGroup):
    img = State()
    prompt = State()
    ticket = State()
    tk_reply = State()
    receipt = State()
    lc = State()


class A(StatesGroup):
    input = State()
    emo_pack = State()
    banner = State()
    mbanner = State()
    restore = State()


# ---------------------- экраны ----------------------
PERIODS = [(30, "30 дней"), (180, "6 месяцев"), (365, "1 год")]


def greeting(tz):
    h = time.gmtime(time.time() + tz * 3600).tm_hour
    return "Доброе утро" if 5 <= h < 11 else "Добрый день" if 11 <= h < 17 else "Добрый вечер" if 17 <= h < 23 else "Доброй ночи"


async def welcome_text(tu):
    t = await text("welcome2")
    tag = avatar_tag(await get_user(tu.id))
    t = t.replace("{avatar} ", tag + " " if tag else "").replace("{avatar}", tag)
    return t.replace("{greeting}", greeting(await seti("tz", 3))).replace("{name}", H.escape(tu.first_name or tu.full_name or "друг"))


async def gate(m, uid=None):
    """True, если пользователя нужно остановить (бан / нужен ключ)."""
    uid = uid or m.from_user.id
    if exempt(uid):
        return False
    u = await get_user(uid)
    if u and u["banned"]:
        await reply(m, await text("banned"))
        return True
    if await setting("require_key") == "1" and not (u and u["activated"]):
        await reply(m, await text("need_key"))
        return True
    return False


async def profile_text(uid, tu):
    u = await get_user(uid)
    cp = eff_plan(u)
    t = tariff(cp)
    L = [f"{avatar_tag(u) or '👤'} <b>Профиль</b>", f"Имя: {H.escape(tu.full_name)}", f"ID: <code>{uid}</code>", ""]
    if exempt(uid):
        L.append("💎 Тариф: <b>безлимит (владелец)</b>")
    else:
        until = f" (до {fdate(u['plan_until'])})" if cp != "free" else ""
        L.append(f"💎 Тариф: <b>{t['name']}</b>{until}")
        L.append(f"🪙 Токены: израсходовано {fmt(u['tok_used'])} из {fmt(t['tokens'])}, осталось <b>{fmt(left_tokens(u, t))}</b>")
        L.append(f"🔄 Обновление лимита: {next_reset(u)}")
        if u["bonus"] > 0:
            L.append(f"➕ Бонусные токены: {fmt(u['bonus'])}")
        for g in await q("SELECT model, tokens FROM grants WHERE uid=? AND tokens>0", (uid,)):
            L.append(f"🎁 На модель {TITLE.get(g['model'], g['model'])}: {fmt(g['tokens'])}")
    L += ["", f"📊 Всего списано: {fmt(u['tok_total'])} токенов тарифа; по данным API: {fmt(u['tok_raw'])}; запросов: {u['req_count']}",
          f"🤖 Чат: {TITLE.get(cur_model(u))} (×{MULT.get(cur_model(u), 1):g})",
          f"💻 Код: {TITLE.get(cur_code_model(u))} (×{MULT.get(cur_code_model(u), 1):g})",
          f"🧠 Память чата: {'вкл' if u['memory'] else 'выкл'}"]
    if u["custom_prompt"]:
        L.append("✏️ Свой стиль: " + H.escape(u["custom_prompt"][:120]))
    return "\n".join(L)


async def plans_screen(uid):
    u = await get_user(uid)
    cur = eff_plan(u)
    L = [await text("plans_title"), ""]
    for t in TARIFFS.values():
        mark = " ← твой" if t["code"] == cur else ""
        l = f"<b>{t['name']}</b>{mark}: {fmt(t['tokens'])} токенов в месяц, {models_desc(t)}" + (", генерация изображений" if t["img_ok"] else "")
        prices = [f"{lab} — {p} ₽" for (d, lab), p in zip(PERIODS, (t["p30"], t["p180"], t["p365"])) if p]
        if prices:
            l += "\n   " + " · ".join(prices)
        L.append(l)
    L += ["", await text("plans_note")]
    rows = [[(f"💎 {t['name']}", f"tar:{t['code']}")] for t in TARIFFS.values() if t["p30"] or t["p180"] or t["p365"]]
    rows.append([("📄 Соглашение", await setting("terms_url")), ("🔒 Политика", await setting("privacy_url"))])  # документы всегда под рукой
    return "\n".join(L), kb(rows, back="nav:home")


LEN_RU = {"short": "короткая", "mid": "средняя", "long": "подробная"}


async def settings_screen(u):
    onoff = lambda v: "вкл" if v else "выкл"
    txt = (f"⚙️ <b>Мои настройки</b>\n\n📏 Длина ответа: <b>{LEN_RU[u['length']]}</b>\n🧠 Память чата: <b>{onoff(u['memory'])}</b>\n"
           f"🙂 Эмодзи из аватарки: <b>{onoff(u['avatar_opt'] == 1)}</b>\n🌐 Поиск в сети для каждого вопроса: <b>{onoff(u['search'])}</b>\n"
           f"✏️ Свой стиль: {H.escape(u['custom_prompt'][:200]) if u['custom_prompt'] else 'не задан'}")
    rows = [[(f"📏 Длина: {LEN_RU[u['length']]}", "us:len"), (f"🧠 Память: {onoff(u['memory'])}", "us:mem")],
            [(f"🙂 Эмодзи-аватар: {onoff(u['avatar_opt'] == 1)}", "us:avatar"), (f"🌐 Поиск: {onoff(u['search'])}", "us:search")],
            [("✏️ Задать свой стиль", "us:prompt"), ("🗑 Сбросить стиль", "us:reset")],
            [("💬 Автоответчик .lc", "lc:menu")]]
    return txt, kb(rows, back="nav:home")


def models_kb():
    return kb([[("💬 Чат", "mgrp:_"), ("💻 Код", "cgrp:_")], [("🎨 Картинки", "mimg:_")]], back="nav:home")


# ---------------------- ответ ИИ ----------------------
QUEST_SYS = ("Ты ведущий текстовой RPG. Придумай захватывающий мир, веди историю по шагам, в конце каждого хода "
             "предлагай 3 варианта действий (1, 2, 3). Пиши живо, до 120 слов за ход, без LaTeX и таблиц.")


async def typing_loop(bot, chat_id, stop):
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), 4.5)
        except asyncio.TimeoutError:
            pass


async def respond(m: Message, user_text: str, rounds=1, prefix="", code=False, image=None, uid=None, force_search=False):
    uid = uid or m.from_user.id
    if uid in ADMIN_MODE:
        return
    lock = LOCKS.setdefault(uid, asyncio.Lock())
    if lock.locked():
        return await m.answer("⏳ Подожди, я ещё отвечаю на прошлое сообщение.")
    async with lock:
        u = await get_user(uid)
        if not u:
            return
        col = "code_model" if code else "model"
        model, swapped = await usable_model(uid, u, cur_code_model(u) if code else cur_model(u), col)
        ok, msg = await can_use(uid, u, model)
        if not ok:
            return await reply(m, msg, reply_markup=kb([[("💎 Тарифы", "nav:plans")]], back="nav:home"))
        mem = bool(u["memory"])
        hist = await get_hist(uid) if mem else []
        user_msg = {"role": "user", "content": ("[фото] " if image else "") + prefix + user_text}
        hist.append(user_msg)
        del hist[:-30]
        ctx = [dict(x) for x in hist[-await seti("hist_len", 6):]]
        for x in ctx[:-1]:
            if len(x["content"]) > 1200:
                x["content"] = x["content"][:1200] + "…"
        last = prefix + user_text
        web = await web_context(user_text, force_search or bool(u["search"]))
        if web:
            last = f"[Данные из интернета]\n{web}\n\n[Вопрос]\n{last}\nОтветь по этим данным и укажи источники ссылками."
        ctx[-1] = ({"role": "user", "content": [{"type": "text", "text": last}, {"type": "image_url", "image_url": {"url": image}}]}
                   if image else {"role": "user", "content": last})
        sys_ = QUEST_SYS if u["mode"] == "quest" else await setting("sys3")
        sys_ += {"short": " Отвечай очень кратко.", "long": " Отвечай подробно."}.get(u["length"], "")
        if u["custom_prompt"]:
            sys_ += " Стиль и инструкции пользователя: " + u["custom_prompt"][:500]
        max_out = await seti("code_tokens", 4000) if code else ({"short": 500, "long": 3500}.get(u["length"]) or await seti("max_out", 1500))

        stop = asyncio.Event()
        spawn(typing_loop(m.bot, m.chat.id, stop))
        sm, last_edit = [None], [0.0]

        async def cb(t):
            if len(t) < 60 or time.time() - last_edit[0] < 1.2:
                return
            last_edit[0] = time.time()
            shown = f"✍️ Пишу код... {len(t)} симв." if (code or "<<<FILE" in t) else H.escape(t[-3800:])
            try:
                if sm[0] is None:
                    sm[0] = await m.answer(shown)
                else:
                    await sm[0].edit_text(shown)
            except Exception:
                pass
        try:
            ans, raw, approx = await chat_long([{"role": "system", "content": sys_}] + ctx, model, rounds, max_out, cb)
        except Exception as e:
            stop.set()
            if mem:
                hist.pop()
            if sm[0]:
                await safe_delete(sm[0])
            hint = "\n\nВозможно, эта модель не читает фото. Выбери другую в /models." if image else ""
            return await m.answer(f"⚠️ Ошибка API: <code>{H.escape(str(e))[:400]}</code>{hint}")
        finally:
            stop.set()
        charged, left = await spend(uid, u, model, raw)
        foot = footer_text(charged, left, MULT.get(model, 1.0), approx) if await setting("show_tokens") == "1" else ""
        if swapped:
            ans = f"ℹ️ Эта модель недоступна на твоём тарифе, отвечаю моделью {TITLE.get(model, model)}.\n\n" + ans
        ids = []
        ids += await send_answer(m, ans, model, foot, sm[0])
        if mem:
            asst = {"role": "assistant", "content": FILE_RE.sub(lambda x: f"[файл {x.group(1).strip()} создан]", ans)[:1500]}
            hist.append(asst)
            spawn(save_hist(uid, [user_msg, asst]))
        REPLIES[(m.chat.id, m.message_id)] = ids
        if len(REPLIES) > 500:
            REPLIES.pop(next(iter(REPLIES)))


async def do_image(m: Message, raw: str, uid=None):
    uid = uid or m.from_user.id
    u = await get_user(uid)
    model = await cur_img_model(u)
    ok, msg = await can_use(uid, u, model, "image")
    if not ok:
        return await reply(m, msg, reply_markup=kb([[("💎 Тарифы", "nav:plans")]], back="nav:home"))
    n, mt = 1, re.match(r"^([1-4])\s+(.+)", raw, re.S)
    if mt:
        n, raw = int(mt.group(1)), mt.group(2)
    stop = asyncio.Event()
    spawn(typing_loop(m.bot, m.chat.id, stop))
    try:
        imgs = await gen_images(raw, n, model)
        if not imgs:
            return await m.answer("⚠️ Модель не вернула картинку. Выбери другую: /models → Картинки.")
        cost = IMG_COST.get(model, 50000) * len(imgs)
        charged, left = await spend(uid, u, model, 0, fixed=cost, kind="image")
        if len(imgs) == 1:
            await m.answer_photo(imgs[0])
        else:
            await m.answer_media_group([InputMediaPhoto(media=i) for i in imgs])
        if await setting("show_tokens") == "1":
            await m.answer(f"<i>{footer_text(charged, left, 1, False)}</i>")
    except Exception as e:
        await m.answer(f"⚠️ Ошибка: <code>{H.escape(str(e))[:400]}</code>")
    finally:
        stop.set()


async def article_model_for(uid, u):
    """Для статей берём дешёвую текстовую модель (DeepSeek), а не выбранную в чате."""
    t = tariff(eff_plan(u))
    for cand in [await setting("article_model")] + CHEAP:
        if cand in MODELS and cand not in OFF and (exempt(uid) or plan_allows(t, cand)):
            return cand
    return DEFAULT_CHAT


async def article_images(uid, u):
    """Картинки в статье только на тарифах с картинками и не больше N штук (дорого)."""
    if exempt(uid) or tariff(eff_plan(u))["img_ok"]:
        return max(0, min(3, await seti("article_images", 1)))
    return 0


async def do_telegraph(m: Message, topic: str, uid=None):
    uid = uid or m.from_user.id
    u = await get_user(uid)
    model = await article_model_for(uid, u)
    ok, msg = await can_use(uid, u, model)
    if not ok:
        return await reply(m, msg, reply_markup=kb([[("💎 Тарифы", "nav:plans")]], back="nav:home"))
    stop = asyncio.Event()
    spawn(typing_loop(m.bot, m.chat.id, stop))
    try:
        url, charged = await make_article(uid, u, topic, model, await article_images(uid, u))
        c, left = await spend(uid, u, model, 0, fixed=charged)
        foot = f"\n<i>{footer_text(c, left, 1, False)}</i>" if await setting("show_tokens") == "1" else ""
        await m.answer(f"✅ {url}{foot}")
    except Exception as e:
        await m.answer(f"⚠️ Не получилось: <code>{H.escape(str(e))[:400]}</code>")
    finally:
        stop.set()


# ---------------------- команды пользователя ----------------------
ATTEMPTS: dict = {}


async def try_key(m: Message, key=None):
    uid = m.from_user.id
    key = (key if key is not None else (m.text or "")).strip()
    cnt, until = ATTEMPTS.get(uid, [0, 0])
    if until > time.time():
        return await m.answer(f"⏳ Слишком много попыток. Подожди {int(until - time.time()) // 60 + 1} мин.")
    if hmac.compare_digest(key.encode(), ACTIVATION_KEY.encode()):
        ATTEMPTS.pop(uid, None)
        await q("UPDATE users SET activated=1 WHERE id=?", (uid,), commit=True)
        await safe_delete(m)
        return await reply(m, await text("key_ok"), reply_markup=await main_kb(uid))
    cnt += 1
    ATTEMPTS[uid] = [0 if cnt >= 5 else cnt, time.time() + 600 if cnt >= 5 else 0]
    await m.answer(await text("key_bad") + "\n" + await text("need_key"))


@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    ADMIN_MODE.discard(m.from_user.id)
    if await gate(m):
        return
    uid = m.from_user.id
    u = await get_user(uid)
    if u and u["avatar_opt"] == 1:
        await ensure_avatar(m.bot, uid)  # если аватарка сменилась, эмодзи обновится
    await reply(m, await welcome_text(m.from_user), reply_markup=await main_kb(uid))
    if u and u["avatar_opt"] == 0 and await setting("avatar_emoji") == "1" and await has_avatar(m.bot, uid):
        await m.answer(await text("avatar_ask"), reply_markup=kb([[("✅ Да, сделать", "av:yes"), ("Нет, спасибо", "av:no")]], back=None))


@router.callback_query(F.data.startswith("av:"))
async def avatar_answer(c: CallbackQuery):
    uid, yes = c.from_user.id, c.data == "av:yes"
    await q("UPDATE users SET avatar_opt=? WHERE id=?", (1 if yes else 2, uid), commit=True)
    if not yes:
        await edit_msg(c.message, "Хорошо, без эмодзи. Передумаешь: «Мои настройки».", None, plain=True)
        return await c.answer()
    await c.answer("Делаю…")
    ok_ = await ensure_avatar(c.bot, uid)
    await edit_msg(c.message, "✅ Готово! Эмодзи из твоей аватарки появится в приветствии и профиле." if ok_ else
                   "Не получилось сделать эмодзи (возможно, аватарка скрыта настройками приватности). Попробую позже.", None, plain=True)
    if ok_:
        await reply(c.message, await welcome_text(c.from_user), reply_markup=await main_kb(uid))


@router.message(Command("key"))
async def cmd_key(m: Message):
    await try_key(m, m.text.partition(" ")[2])


@router.message(Command("help"))
async def cmd_help(m: Message):
    await reply(m, await text("help2"), reply_markup=kb([], back="nav:home"))


@router.message(Command("terms", "docs"))
async def cmd_terms(m: Message):
    txt, k = await docs_screen()
    await reply(m, txt, reply_markup=k)


@router.message(Command("ai", "lc"))
async def cmd_ai(m: Message):
    await reply(m, await text("ai_info"), reply_markup=kb([[("💬 Настроить автоответчик", "lc:menu")]], back="nav:home"))


@router.message(Command("profile", "balance"))
async def cmd_profile(m: Message):
    if await gate(m):
        return
    await reply(m, await profile_text(m.from_user.id, m.from_user),
                reply_markup=kb([[("💎 Тарифы", "nav:plans"), ("⚙️ Настройки", "nav:set")]], back="nav:home"))


@router.message(Command("models"))
async def cmd_models(m: Message):
    if await gate(m):
        return
    await reply(m, "🤖 Что выбираем?", reply_markup=models_kb())


@router.message(Command("plans", "tariffs"))
async def cmd_plans(m: Message):
    txt, k = await plans_screen(m.from_user.id)
    await reply(m, txt, reply_markup=k)


@router.message(Command("settings"))
async def cmd_settings(m: Message):
    if await gate(m):
        return
    txt, k = await settings_screen(await get_user(m.from_user.id))
    await reply(m, txt, reply_markup=k)


@router.message(Command("clear"))
async def cmd_clear(m: Message):
    await clear_hist(m.from_user.id)
    await m.answer("🧹 Память очищена")


@router.message(Command("stop"))
async def cmd_stop(m: Message):
    await q("UPDATE users SET mode='' WHERE id=?", (m.from_user.id,), commit=True)
    await clear_hist(m.from_user.id)
    await m.answer("✅ Игра закончена, обычный режим включён.")


@router.message(Command("search"))
async def cmd_search(m: Message):
    if await gate(m):
        return
    arg = m.text.partition(" ")[2].strip()
    if arg:
        await respond(m, arg, force_search=True)
    else:
        await m.answer("🌐 Напиши: <code>/search запрос</code>. Новости, погоду и время я нахожу и без команды.\n"
                       "Искать для каждого вопроса можно включить в /settings.")


@router.message(Command("games"))
async def cmd_games(m: Message):
    txt, k = await games_screen()
    await reply(m, txt, reply_markup=k)


@router.message(Command("support"))
async def cmd_support(m: Message, state: FSMContext):
    await state.set_state(G.ticket)
    await reply(m, await text("support_ask"), reply_markup=kb([], back="nav:home"))


@router.message(Command("receipts"))
async def cmd_receipts(m: Message):
    rows = await q("SELECT * FROM payments WHERE uid=? ORDER BY id DESC LIMIT 10", (m.from_user.id,))
    if not rows:
        return await m.answer("🧾 Платежей пока нет.")
    await m.answer("🧾 <b>Твои чеки</b>\n\n" + "\n\n".join(receipt_text(dict(r)) for r in rows))


@router.message(Command("img"))
async def cmd_img(m: Message):
    if await gate(m):
        return
    arg = m.text.partition(" ")[2]
    await do_image(m, arg) if arg else await m.answer(await text("img_ask"))


@router.message(Command("tg"))
async def cmd_tg(m: Message):
    if await gate(m):
        return
    arg = m.text.partition(" ")[2]
    await do_telegraph(m, arg) if arg else await m.answer(await text("tg_ask"))


async def long_cmd(m: Message, prefix):
    if await gate(m):
        return
    arg = m.text.partition(" ")[2]
    if not arg:
        return await m.answer(await text("code_help"))
    await respond(m, arg, rounds=await seti("code_rounds", 3), prefix=prefix, code=True)


@router.message(Command("code"))
async def cmd_code(m: Message):
    await long_cmd(m, "Сделай рабочий код Пиши компактно: без лишних комментариев, дублирования и воды, только нужное. Каждый файл в формате <<<FILE: имя>>>...<<<END>>>. Задача: ")


@router.message(Command("apk"))
async def cmd_apk(m: Message):
    await long_cmd(m, "Сделай Android-проект Kivy+buildozer (main.py, buildozer.spec и только нужные файлы). Пиши компактно: без лишних комментариев, дублирования и воды, только нужное. Задача: ")


@router.message(Command("3d"))
async def cmd_3d(m: Message):
    await long_cmd(m, "Сделай 3D-сцену одним файлом scene.html на three.js (cdnjs), аккуратно и компактно. Пиши компактно: без лишних комментариев, дублирования и воды, только нужное. Задача: ")


@router.message(Command("studio"))
async def cmd_studio(m: Message):
    if await gate(m):
        return
    url = await studio_url()
    if not url:
        return await m.answer("💻 Студия кода пока не настроена: админу нужно задать публичный адрес бота (переменная PUBLIC_URL).")
    await reply(m, "💻 <b>Студия кода</b>\nВыбери модель, опиши задачу и получи готовый файл (.html, .py или любой другой формат). "
                   "Запустить можно прямо внутри. Расходует токены твоей подписки.",
                reply_markup=kb([[("💻 Открыть студию", "wa:" + url)]], back="nav:home"))


@router.message(Command("asuser"))
async def cmd_asuser(m: Message):
    global TEST_MODE
    if m.from_user.id != OWNER_ID:
        return
    TEST_MODE = not TEST_MODE
    await m.answer("🧪 Режим проверки ВКЛ: ты как обычный пользователь (тариф Free). Ещё раз /asuser: выход." if TEST_MODE
                   else "✅ Режим проверки выкл: ты снова владелец.")


# ---------------------- навигация и меню ----------------------
async def docs_screen():
    return "📄 <b>Документы</b>\nУсловия использования и политика всегда доступны здесь.", kb(
        [[("📄 Пользовательское соглашение", await setting("terms_url"))], [("🔒 Политика конфиденциальности", await setting("privacy_url"))]], back="nav:home")


async def games_screen():
    url, ttt = await setting("webapp_chess"), await setting("webapp_ttt")
    rows = ([[("♟ Шахматы: бот и друзья", "wa:" + url)]] if url else []) + ([[("❌⭕ Крестики-нолики: бот и друзья", "wa:" + ttt)]] if ttt else []) \
        + [[("🧠 Викторина", "g:quiz"), ("🎭 Текстовый квест", "g:quest")]]
    return "🎮 <b>Мини-игры с ИИ</b>\nВыбирай:", kb(rows, back="nav:home")


@router.callback_query(F.data.startswith("nav:"))
async def nav(c: CallbackQuery, state: FSMContext):
    await state.clear()
    a, uid = c.data[4:], c.from_user.id
    if a == "home":
        await edit_msg(c.message, await welcome_text(c.from_user), await main_kb(uid))
    elif a == "models":
        await edit_msg(c.message, "🤖 Что выбираем?", models_kb())
    elif a == "plans":
        txt, k = await plans_screen(uid)
        await edit_msg(c.message, txt, k)
    elif a == "set":
        txt, k = await settings_screen(await get_user(uid))
        await edit_msg(c.message, txt, k)
    elif a == "games":
        txt, k = await games_screen()
        await edit_msg(c.message, txt, k)
    await c.answer()


@router.callback_query(F.data.startswith("m:"))
async def menu(c: CallbackQuery, state: FSMContext):
    a, uid, toast = c.data[2:], c.from_user.id, None
    if a not in ("clear",) and await gate(c.message, uid):
        return await c.answer()
    if a == "models":
        await edit_msg(c.message, "🤖 Что выбираем?", models_kb())
    elif a == "img":
        await state.set_state(G.img)
        await edit_msg(c.message, await text("img_ask"), kb([], back="nav:home"))
    elif a == "tg":
        await state.set_state(None)
        await edit_msg(c.message, await text("tg_ask") + "\n\nНапиши: <code>/tg тема</code>", kb([], back="nav:home"))
    elif a == "code":
        url = await studio_url()
        rows = ([[("💻 Студия кода (мини-апп)", "wa:" + url)]] if url else []) + [[("🤖 Модель для кода", "cgrp:_")]]
        await edit_msg(c.message, await text("code_help") + f"\n\nДля кода доступны модели с коэффициентом до ×{code_max():g}.", kb(rows, back="nav:home"))
    elif a == "search":
        u = await get_user(uid)
        v = 0 if u["search"] else 1
        await q("UPDATE users SET search=? WHERE id=?", (v, uid), commit=True)
        toast = "🌐 Поиск для каждого вопроса: " + ("вкл" if v else "выкл")
    elif a == "games":
        txt, k = await games_screen()
        await edit_msg(c.message, txt, k)
    elif a == "docs":
        txt, k = await docs_screen()
        await edit_msg(c.message, txt, k)
    elif a == "ai":
        await edit_msg(c.message, await text("ai_info"), kb([[("💬 Настроить автоответчик", "lc:menu")]], back="nav:home"))
    elif a == "profile":
        await edit_msg(c.message, await profile_text(uid, c.from_user),
                       kb([[("💎 Тарифы", "nav:plans"), ("⚙️ Настройки", "nav:set")]], back="nav:home"))
    elif a == "plans":
        txt, k = await plans_screen(uid)
        await edit_msg(c.message, txt, k)
    elif a == "support":
        await state.set_state(G.ticket)
        await edit_msg(c.message, await text("support_ask"), kb([], back="nav:home"))
    elif a == "set":
        txt, k = await settings_screen(await get_user(uid))
        await edit_msg(c.message, txt, k)
    elif a == "clear":
        await clear_hist(uid)
        toast = "🧹 Память очищена"
    await c.answer(toast)


@router.callback_query(F.data.startswith("us:"))
async def user_settings(c: CallbackQuery, state: FSMContext):
    a, uid = c.data[3:], c.from_user.id
    u = await get_user(uid)
    if a == "len":
        nxt = {"short": "mid", "mid": "long", "long": "short"}[u["length"]]
        await q("UPDATE users SET length=? WHERE id=?", (nxt, uid), commit=True)
    elif a in ("mem", "search"):
        col = {"mem": "memory", "search": "search"}[a]
        await q(f"UPDATE users SET {col}=? WHERE id=?", (0 if u[col] else 1, uid), commit=True)
    elif a == "avatar":
        on = u["avatar_opt"] == 1
        await q("UPDATE users SET avatar_opt=? WHERE id=?", (2 if on else 1, uid), commit=True)
        if not on:
            if not await ensure_avatar(c.bot, uid):
                await c.answer("Не вышло: нет доступной аватарки", show_alert=True)
    elif a == "reset":
        await q("UPDATE users SET custom_prompt='' WHERE id=?", (uid,), commit=True)
    elif a == "prompt":
        await state.set_state(G.prompt)
        await edit_msg(c.message, "✏️ Напиши, как мне отвечать (стиль, роль, тон), например: «отвечай как опытный программист, коротко и с примерами». До 500 знаков.",
                       kb([], back="nav:set"))
        return await c.answer()
    txt, k = await settings_screen(await get_user(uid))
    await edit_msg(c.message, txt, k)
    await c.answer()


@router.message(G.prompt, F.text)
async def st_prompt(m: Message, state: FSMContext):
    await q("UPDATE users SET custom_prompt=? WHERE id=?", (m.text.strip()[:500], m.from_user.id), commit=True)
    await state.clear()
    txt, k = await settings_screen(await get_user(m.from_user.id))
    await reply(m, "✅ Стиль сохранён.\n\n" + txt, reply_markup=k)


# ---------------------- выбор моделей ----------------------
@router.callback_query(F.data.regexp(r"^[mc]grp:"))
async def pick_group(c: CallbackQuery):
    kind, g = c.data[0], c.data[5:]
    uid = c.from_user.id
    u = await get_user(uid)
    t = tariff(eff_plan(u))
    cur = cur_model(u) if kind == "m" else cur_code_model(u)
    pref, title = ("mod", "Чат") if kind == "m" else ("cmod", "Код")
    pool = [x for x in CHAT_CATALOG if x[1] not in OFF and (kind == "m" or x[1] in code_models())]
    if g == "_":  # выбор компании ИИ: свой баннер
        btns = [(x, f"{kind}grp:{x}") for x in dict.fromkeys(p[0] for p in pool)]
        rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
        note = "" if kind == "m" else f"\nДля кода доступны модели с коэффициентом до ×{code_max():g}"
        await edit_msg(c.message, f"{title}: сейчас <b>{TITLE.get(cur, cur)}</b>{note}\nВыбери компанию:", kb(rows, back="nav:models"), banner="@pick")
        return await c.answer()
    rows, first = [], None
    for grp, mid, ttl, mult in pool:
        if grp != g:
            continue
        first = first or mid
        lock = "🔒 " if (not exempt(uid) and not plan_allows(t, mid)) else ""
        rows.append([(("✅ " if mid == cur else lock) + f"{ttl} ×{mult}", f"{pref}:{mid}")])
    # список моделей: баннер компании, а если его нет, общий баннер «всех моделей»
    await edit_msg(c.message, f"{title} · <b>{g}</b>\n🔒 — недоступно на твоём тарифе, ×N — расход токенов",
                   kb(rows, back=f"{kind}grp:_"), banner=first)
    await c.answer()


async def choose_model(c: CallbackQuery, col, label):
    uid = c.from_user.id
    mid = c.data.split(":", 1)[1]
    if mid not in MODELS or mid in OFF:
        return await c.answer("Эта модель сейчас недоступна", show_alert=True)
    if col == "code_model" and mid not in code_models():
        return await c.answer(f"Для кода доступны модели с коэффициентом до ×{code_max():g}", show_alert=True)
    u = await get_user(uid)
    if not exempt(uid) and not plan_allows(tariff(eff_plan(u)), mid) and await grant_left(uid, mid) <= 0:
        return await c.answer("🔒 Эта модель недоступна на твоём тарифе. Смотри /plans", show_alert=True)
    await q(f"UPDATE users SET {col}=? WHERE id=?", (mid, uid), commit=True)
    await edit_msg(c.message, f"✅ {label}: <b>{TITLE.get(mid, mid)}</b> (×{MULT.get(mid, 1):g})\nПамять диалога сохранена.",
                   kb([], back="nav:models"))
    await c.answer()


@router.callback_query(F.data.startswith("mod:"))
async def set_model(c: CallbackQuery):
    await choose_model(c, "model", "Модель чата")


@router.callback_query(F.data.startswith("cmod:"))
async def set_code_model(c: CallbackQuery):
    await choose_model(c, "code_model", "Модель для кода (/code, /apk, /3d)")


@router.callback_query(F.data.startswith("mimg:"))
async def pick_img(c: CallbackQuery):
    u = await get_user(c.from_user.id)
    cur = await cur_img_model(u)
    t = tariff(eff_plan(u))
    lock = "" if (exempt(c.from_user.id) or t["img_ok"]) else "🔒 "
    rows = [[(("✅ " if mid == cur else lock) + f"{ttl} · {fmt(cost)}/шт", f"imod:{mid}")] for mid, ttl, cost in IMG_CATALOG]
    await edit_msg(c.message, "🎨 Модель для картинок (цена за одну картинку в токенах)\n🔒 — нужен тариф Pro", kb(rows, back="nav:models"))
    await c.answer()


@router.callback_query(F.data.startswith("imod:"))
async def set_img_model(c: CallbackQuery):
    mid = c.data[5:]
    if mid not in IMG_MODELS:
        return await c.answer("Нет такой модели", show_alert=True)
    await q("UPDATE users SET img_model=? WHERE id=?", (mid, c.from_user.id), commit=True)
    await edit_msg(c.message, f"✅ Картинки делает: <b>{mid}</b>", kb([], back="nav:models"))
    await c.answer()


# ---------------------- тарифы и оплата ----------------------
def price_for(t, days):
    return {30: t["p30"], 180: t["p180"], 365: t["p365"]}.get(days, 0)


def period_label(days):
    return dict(PERIODS).get(days, f"{days} дн.")


def receipt_text(p):
    st = {"paid": "✅ оплачено", "new": "⏳ ждёт оплаты", "pending": "🔎 на проверке", "rejected": "❌ отклонён / отменён"}.get(p["status"], p["status"])
    meth = {"card": "карта", "star": "Telegram Stars", "usdt": "CryptoBot", "man": "перевод", "plat": "Platega", "admin": "выдано админом"}.get(p["method"], p["method"])
    return (f"🧾 <b>Чек №{p['id']}</b>\nТариф: {tariff(p['plan'])['name']} · {period_label(p['days'])}\n"
            f"Сумма: {p['rub']} ₽ ({meth})\nДата: {fdate(p['created'])}\nСтатус: {st}")


async def pay_methods(code, days):
    t = tariff(code)
    rows = []
    if PROVIDER_TOKEN:
        rows.append([("💳 Картой (₽)", f"pm:card:{code}:{days}")])
    rows.append([("⭐ Telegram Stars", f"pm:star:{code}:{days}")])
    if CRYPTO_TOKEN:
        rows.append([("₮ CryptoBot (USDT)", f"pm:usdt:{code}:{days}")])
    if await plat_ready():
        for pid_, name in await plat_methods():
            rows.append([(f"🏦 {name}", f"pm:plat{pid_}:{code}:{days}")])
    if (await text("requisites")).strip():
        rows.append([("🧾 Перевод по реквизитам", f"pm:man:{code}:{days}")])
    txt = f"💳 <b>{t['name']}</b> · {period_label(days)} · <b>{price_for(t, days)} ₽</b>\nВыбери способ оплаты:"
    return txt, kb(rows, back=f"tar:{code}")


@router.callback_query(F.data.startswith("tar:"))
async def tar(c: CallbackQuery):
    code = c.data[4:]
    t = tariff(code)
    rows = [[(f"{lab} — {p} ₽", f"tp:{code}:{d}")] for (d, lab), p in zip(PERIODS, (t["p30"], t["p180"], t["p365"])) if p]
    txt = (f"💎 <b>{t['name']}</b>\n{fmt(t['tokens'])} токенов в месяц, {models_desc(t)}"
           + ("\nГенерация изображений включена" if t["img_ok"] else "") + "\n\nВыбери срок:")
    await edit_msg(c.message, txt, kb(rows, back="nav:plans"))
    await c.answer()


@router.callback_query(F.data.startswith("tp:"))
async def tp(c: CallbackQuery):
    """Соглашение показываем при КАЖДОЙ покупке, а не только в первый раз."""
    _, code, days = c.data.split(":")
    rows = [[("📄 Пользовательское соглашение", await setting("terms_url"))],
            [("🔒 Политика конфиденциальности", await setting("privacy_url"))],
            [("✅ Я согласен", f"agree:{code}:{days}")]]
    await edit_msg(c.message, await text("agree_text"), kb(rows, back=f"tar:{code}"))
    await c.answer()


@router.callback_query(F.data.startswith("agree:"))
async def agree(c: CallbackQuery):
    _, code, days = c.data.split(":")
    await q("UPDATE users SET agreed=1 WHERE id=?", (c.from_user.id,), commit=True)
    txt, k = await pay_methods(code, int(days))
    await edit_msg(c.message, txt, k)
    await c.answer("Спасибо!")


async def crypto(method, **params):
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"https://pay.crypt.bot/api/{method}", json=params, headers={"Crypto-Pay-API-Token": CRYPTO_TOKEN})
        return (await r.json()).get("result")


@router.callback_query(F.data.startswith("pm:"))
async def pm(c: CallbackQuery, state: FSMContext):
    _, method, code, days = c.data.split(":")
    days, uid = int(days), c.from_user.id
    t = tariff(code)
    rub = price_for(t, days)
    if not rub:
        return await c.answer("Этот срок недоступен", show_alert=True)
    pid = await q("INSERT INTO payments(uid,plan,days,rub,method,status,created) VALUES(?,?,?,?,?,'new',?)",
                  (uid, code, days, rub, method, int(time.time())), commit=True)
    title = f"Gimbek AI {t['name']}"
    desc = f"{period_label(days)} · {fmt(t['tokens'])} токенов в месяц"
    try:
        if method == "card":
            await c.bot.send_invoice(chat_id=uid, title=title, description=desc, payload=f"pay:{pid}", provider_token=PROVIDER_TOKEN,
                                     currency="RUB", prices=[LabeledPrice(label=title, amount=rub * 100)])
        elif method == "star":
            stars = max(1, math.ceil(rub * await setf("stars_per_rub", 0.85)))
            await c.bot.send_invoice(chat_id=uid, title=title, description=desc, payload=f"pay:{pid}", provider_token="",
                                     currency="XTR", prices=[LabeledPrice(label=title, amount=stars)])
        elif method == "usdt":
            usdt = max(0.01, round(rub / await setf("rub_per_usdt", 95), 2))
            inv = await crypto("createInvoice", asset="USDT", amount=str(usdt), description=title, payload=str(pid))
            await q("UPDATE payments SET ext=? WHERE id=?", (str(inv["invoice_id"]), pid), commit=True)
            await c.message.answer(f"🧾 Чек №{pid}: {usdt} USDT. Оплати счёт, тариф выдастся автоматически.",
                                   reply_markup=kb([[("💸 Оплатить", inv["bot_invoice_url"])], [("🔄 Проверить оплату", f"pc:{pid}")]], back=None))
        elif method.startswith("plat"):
            tid, url = await platega_create(rub, f"{title} · {desc}", f"pay:{pid}", int(method[4:]))
            await q("UPDATE payments SET ext=?, method='plat' WHERE id=?", (tid, pid), commit=True)
            await c.message.answer(f"🧾 Чек №{pid}: {rub} ₽. Оплати по кнопке, тариф включится автоматически.",
                                   reply_markup=kb([[("💸 Оплатить", url)], [("🔄 Проверить оплату", f"pc:{pid}")]], back=None))
        else:
            await c.message.answer(f"🧾 <b>Чек №{pid}</b>: {title}, {period_label(days)}, {rub} ₽\n\n{await text('requisites')}\n\n"
                                   "Переведи точную сумму и нажми кнопку, затем пришли скриншот или файл чека.",
                                   reply_markup=kb([[("📎 Отправить чек", f"pr:{pid}")]], back=None))
    except Exception as e:
        await c.message.answer(f"⚠️ Не удалось создать счёт: <code>{H.escape(str(e))[:300]}</code>")
    await c.answer()


@router.pre_checkout_query()
async def pre_checkout(p: PreCheckoutQuery):
    await p.answer(ok=True)


def notify_owner_text(p, who):
    return f"💰 Платёж №{p['id']}: {who}\n{tariff(p['plan'])['name']} · {period_label(p['days'])} · {p['rub']} ₽ ({p['method']})"


async def finish_payment(bot, pid):
    p = await q("SELECT * FROM payments WHERE id=?", (pid,), one=True)
    if not p or p["status"] == "paid":
        return None
    p = dict(p)
    await q("UPDATE payments SET status='paid', paid_at=? WHERE id=?", (int(time.time()), pid), commit=True)
    until = await grant_plan(p["uid"], p["plan"], p["days"])
    p["status"] = "paid"
    msg = (await text("paid_ok")).replace("{plan}", tariff(p["plan"])["name"]).replace("{date}", fdate(until))
    try:
        await bot.send_message(p["uid"], msg + "\n\n" + receipt_text(p))
    except Exception as e:
        logging.warning("notify user: %s", e)
    if OWNER_ID:
        try:
            u = await get_user(p["uid"])
            await bot.send_message(OWNER_ID, notify_owner_text(p, f"{u['name']} (@{u['username'] or '-'}, id {p['uid']})") + " ✅ выдан автоматически")
        except Exception:
            pass
    return until


@router.message(F.successful_payment)
async def paid_ok(m: Message):
    try:
        await finish_payment(m.bot, int(m.successful_payment.invoice_payload.split(":")[1]))
    except Exception:
        logging.exception("successful_payment")


@router.callback_query(F.data.startswith("pc:"))
async def check_crypto(c: CallbackQuery):
    pid = int(c.data[3:])
    p = await q("SELECT * FROM payments WHERE id=? AND uid=?", (pid, c.from_user.id), one=True)
    if not p:
        return await c.answer()
    try:
        if p["method"] == "plat":
            paid = await platega_status(p["ext"]) == "CONFIRMED"
        else:
            items = ((await crypto("getInvoices", invoice_ids=p["ext"])) or {}).get("items", [])
            paid = bool(items) and items[0]["status"] == "paid"
    except Exception as e:
        return await c.answer("Не удалось проверить: " + str(e)[:150], show_alert=True)
    if paid:
        await finish_payment(c.bot, pid)
        return await c.answer("✅ Оплата найдена")
    await c.answer("Оплата ещё не пришла", show_alert=True)


@router.callback_query(F.data.startswith("pr:"))
async def receipt_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(G.receipt)
    await state.update_data(pid=int(c.data[3:]))
    await c.message.answer("📎 Пришли скриншот или файл чека одним сообщением.")
    await c.answer()


@router.message(G.receipt)
async def receipt_in(m: Message, state: FSMContext):
    pid = (await state.get_data()).get("pid")
    p = await q("SELECT * FROM payments WHERE id=?", (pid,), one=True)
    await state.clear()
    if not p or p["uid"] != m.from_user.id:
        return
    fid = m.photo[-1].file_id if m.photo else (m.document.file_id if m.document else "")
    await q("UPDATE payments SET status='pending', ext=? WHERE id=?", (fid or (m.text or "")[:200], pid), commit=True)
    await m.answer(f"✅ Чек №{pid} отправлен на проверку. После подтверждения тариф включится автоматически.")
    if OWNER_ID:
        cap = notify_owner_text(dict(p), f"{m.from_user.full_name} (@{m.from_user.username or '-'}, id {m.from_user.id})") + "\nНужно проверить чек."
        k = kb([[("✅ Одобрить", f"pay_ok:{pid}"), ("❌ Отклонить", f"pay_no:{pid}")]], back=None)
        try:
            if m.photo:
                await m.bot.send_photo(OWNER_ID, fid, caption=cap, reply_markup=k)
            elif m.document:
                await m.bot.send_document(OWNER_ID, fid, caption=cap, reply_markup=k)
            else:
                await m.bot.send_message(OWNER_ID, cap + f"\nТекст: {H.escape(m.text or '')[:500]}", reply_markup=k)
        except Exception as e:
            logging.warning("receipt notify: %s", e)


# ---------------------- тикеты ----------------------
async def notify_ticket(bot, tid, uid, body):
    if not OWNER_ID:
        return
    u = await get_user(uid)
    try:
        await bot.send_message(OWNER_ID, f"🆘 <b>Тикет №{tid}</b> от {H.escape(u['name'])} (@{u['username'] or '-'}, id {uid})\n\n{H.escape(body)[:3500]}",
                               reply_markup=kb([[("✉️ Ответить", f"tk_r:{tid}"), ("✅ Закрыть", f"tk_c:{tid}")]], back=None))
    except Exception as e:
        logging.warning("ticket notify: %s", e)


@router.message(G.ticket, F.text)
async def ticket_new(m: Message, state: FSMContext):
    await state.clear()
    tid = await q("INSERT INTO tickets(uid,status,created) VALUES(?,?,?)", (m.from_user.id, "open", int(time.time())), commit=True)
    await q("INSERT INTO ticket_msgs(tid,from_admin,body,ts) VALUES(?,0,?,?)", (tid, m.text, int(time.time())), commit=True)
    await m.answer(f"✅ Тикет №{tid} создан. Ответ придёт сюда.")
    await notify_ticket(m.bot, tid, m.from_user.id, m.text)


@router.callback_query(F.data.startswith("tk_u:"))
async def ticket_user_reply(c: CallbackQuery, state: FSMContext):
    await state.set_state(G.tk_reply)
    await state.update_data(tid=int(c.data[5:]))
    await c.message.answer("✍️ Напиши ответ в поддержку:")
    await c.answer()


@router.message(G.tk_reply, F.text)
async def ticket_user_msg(m: Message, state: FSMContext):
    tid = (await state.get_data()).get("tid")
    await state.clear()
    t = await q("SELECT * FROM tickets WHERE id=? AND uid=?", (tid, m.from_user.id), one=True)
    if not t:
        return
    await q("UPDATE tickets SET status='open' WHERE id=?", (tid,), commit=True)
    await q("INSERT INTO ticket_msgs(tid,from_admin,body,ts) VALUES(?,0,?,?)", (tid, m.text, int(time.time())), commit=True)
    await m.answer("✅ Отправлено.")
    await notify_ticket(m.bot, tid, m.from_user.id, m.text)


# ---------------------- игры ----------------------
@router.callback_query(F.data == "g:quiz")
async def quiz_new(c: CallbackQuery):
    uid = c.from_user.id
    if await gate(c.message, uid):
        return await c.answer()
    u = await get_user(uid)
    t = tariff(eff_plan(u))
    model = next((x for x in CHEAP if exempt(uid) or plan_allows(t, x)), DEFAULT_CHAT)
    ok, msg = await can_use(uid, u, model)
    if not ok:
        await c.message.answer(msg)
        return await c.answer()
    await c.answer("Придумываю вопрос…")
    try:
        raw, used, _ = await chat_long([{"role": "user", "content": 'Придумай один интересный вопрос викторины на русском (случайная тема). '
                                         'Верни ТОЛЬКО JSON: {"q":"...","options":["...","...","...","..."],"answer":0,"fact":"короткий факт"}. '
                                         'Индекс правильного ответа сделай случайным от 0 до 3.'}], model, 1, 400)
        j = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        assert len(j["options"]) == 4
    except Exception as e:
        return await c.message.answer(f"⚠️ Не получилось придумать вопрос: <code>{H.escape(str(e))[:200]}</code>")
    await spend(uid, u, model, used)
    QUIZ[uid] = j
    rows = [[(f"{'ABCD'[i]}. {o[:50]}", f"qa:{i}")] for i, o in enumerate(j["options"])]
    await c.message.answer(f"🧠 <b>{H.escape(j['q'])}</b>\n\nСчёт: {u['quiz']}", reply_markup=kb(rows, back="nav:games"))


@router.callback_query(F.data.startswith("qa:"))
async def quiz_answer(c: CallbackQuery):
    uid = c.from_user.id
    j = QUIZ.pop(uid, None)
    if not j:
        return await c.answer("Вопрос уже закрыт", show_alert=True)
    i = int(c.data[3:])
    right = int(j["answer"])
    if i == right:
        await q("UPDATE users SET quiz=quiz+1 WHERE id=?", (uid,), commit=True)
    res = "✅ Верно!" if i == right else f"❌ Неверно. Правильный ответ: {'ABCD'[right]}. {H.escape(j['options'][right])}"
    await edit_msg(c.message, f"{res}\n\n💡 {H.escape(j.get('fact', ''))}", kb([[("➡️ Следующий", "g:quiz")]], back="nav:games"))
    await c.answer()


@router.callback_query(F.data == "g:quest")
async def quest_start(c: CallbackQuery):
    uid = c.from_user.id
    if await gate(c.message, uid):
        return await c.answer()
    await q("UPDATE users SET mode='quest' WHERE id=?", (uid,), commit=True)
    await clear_hist(uid)
    await c.message.answer("🎭 Квест начался! Пиши свои действия. Выйти: /stop")
    await c.answer()
    await respond(c.message, "Начинай игру: опиши мир и первую сцену.", uid=uid)


# ---------------------- сообщения: текст, фото, голос ----------------------
@router.message(G.img, F.text)
async def st_img(m: Message, state: FSMContext):
    await state.clear()
    await do_image(m, m.text)


@router.message(StateFilter(None), F.photo)
async def on_photo(m: Message):
    uid = m.from_user.id
    if uid in ADMIN_MODE or await gate(m):
        return
    try:
        f = await m.bot.download(m.photo[-1])
        data = base64.b64encode(f.read()).decode()
    except Exception as e:
        return await m.answer(f"⚠️ Не удалось скачать фото: <code>{H.escape(str(e))[:300]}</code>")
    await respond(m, m.caption or "Опиши, что на фото, и ответь на вопрос, если он там есть.", image="data:image/jpeg;base64," + data)


@router.message(StateFilter(None), F.voice | F.video_note | F.video | F.audio)
async def on_media(m: Message):
    if m.from_user.id in ADMIN_MODE:
        return
    await m.answer("🎙 Голосовые, кружки и видео я не обрабатываю. Напиши текстом или пришли фото.")


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    uid = m.from_user.id
    if uid in ADMIN_MODE:
        return
    if not exempt(uid) and await setting("require_key") == "1":
        u = await get_user(uid)
        if not (u and u["activated"]):
            return await try_key(m)
    if await gate(m):
        return
    await respond(m, m.text)


@router.edited_message(StateFilter(None), F.text)
async def on_edit(m: Message):
    uid = m.from_user.id
    for i in REPLIES.pop((m.chat.id, m.message_id), []):
        try:
            await m.bot.delete_message(m.chat.id, i)
        except Exception:
            pass
    h = await get_hist(uid)
    if len(h) >= 2:
        del h[-2:]
        await q("DELETE FROM history WHERE id IN (SELECT id FROM history WHERE uid=? ORDER BY id DESC LIMIT 2)", (uid,), commit=True)
    await respond(m, m.text)


# ---------------------- бизнес-режим: «.ai вопрос» в чужих чатах ----------------------
@router.business_connection()
async def on_biz_conn(bc: BusinessConnection):
    if bc.is_enabled:
        await ensure_user(bc.user)
        await q("INSERT OR REPLACE INTO biz VALUES(?,?)", (bc.id, bc.user.id), commit=True)
    else:
        await q("DELETE FROM biz WHERE conn=?", (bc.id,), commit=True)


async def biz_owner(m):
    r = await q("SELECT uid FROM biz WHERE conn=?", (m.business_connection_id,), one=True)
    if r:
        return r["uid"]
    try:
        bc = await m.bot.get_business_connection(m.business_connection_id)
        await ensure_user(bc.user)
        await q("INSERT OR REPLACE INTO biz VALUES(?,?)", (bc.id, bc.user.id), commit=True)
        return bc.user.id
    except Exception:
        return None


def biz_allowed(owner, plan, model):
    return exempt(owner) or plan_allows(tariff(plan), model)  # все модели на любом тарифе, ограничивают только токены


async def biz_cleanup(m):
    """Убираем команду «.ai ...» из чата, чтобы собеседник видел только ответ (нужно право удалять сообщения)."""
    if await setting("biz_delete_cmd") != "1":
        return
    try:
        await m.bot.delete_business_messages(business_connection_id=m.business_connection_id, message_ids=[m.message_id])
    except Exception as e:
        logging.info("biz delete: %s", e)


async def biz_typing(bot, chat_id, conn, stop):
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id, "typing", business_connection_id=conn)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), 4.5)
        except asyncio.TimeoutError:
            pass


async def biz_typing_now(m):
    try:
        await m.bot.send_chat_action(m.chat.id, "typing", business_connection_id=m.business_connection_id)
    except Exception:
        pass


async def biz_tell_owner(m, owner, txt):
    """Ошибки и подсказки отправляем владельцу в личку с ботом, чтобы собеседник их не видел."""
    try:
        await m.bot.send_message(owner, txt)
    except Exception as e:
        logging.info("biz owner dm: %s", e)


async def biz_model(owner, u):
    plan = eff_plan(u)
    model = await setting(f"biz_model_{plan}") or "deepseek-v4-flash"
    return model if (model in MODELS and model not in OFF and biz_allowed(owner, plan, model)) else "deepseek-v4-flash"


# ---------------------- автоответчик .lc ----------------------
LC_STYLES = {
    "short": ("Коротко", "Отвечай очень коротко, 1-2 фразы, как в обычной переписке в мессенджере."),
    "friendly": ("Дружелюбно", "Отвечай тепло и дружелюбно, живым разговорным языком, коротко."),
    "phil": ("Философски", "Отвечай философски: с размышлением и неожиданным взглядом, но коротко (2-3 фразы)."),
    "rude": ("Дерзко, с матом", "Отвечай дерзко и с сарказмом, можно использовать мат как разговорную речь. Без угроз и без оскорблений по национальности, полу или здоровью."),
    "formal": ("Официально", "Отвечай вежливо и официально, по делу."),
    "custom": ("Свой стиль", ""),
}
LC_BASE = ("Ты отвечаешь в личной переписке вместо владельца аккаунта. Пиши как живой человек от первого лица, без пометок, что ты ИИ, "
           "без markdown, списков и кавычек вокруг ответа. Отвечай на последнее сообщение собеседника по смыслу и в контексте диалога.")
LC_HIST: dict = {}   # (подключение, чат) -> последние реплики
PEER: dict = {}      # чат -> (время, что известно о собеседнике)


async def lc_active(m):
    return bool(await q("SELECT 1 FROM lc_chats WHERE conn=? AND chat=?", (m.business_connection_id, m.chat.id), one=True))


async def peer_info(m, want_avatar=False, model="deepseek-v4-flash"):
    """Что бот знает о собеседнике: имя, username, «о себе», аватарка (кэш на час)."""
    cid = m.chat.id
    hit = PEER.get(cid)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    parts = []
    name = " ".join(x for x in (getattr(m.chat, "first_name", None), getattr(m.chat, "last_name", None)) if x)
    if name:
        parts.append(f"имя {name}")
    if getattr(m.chat, "username", None):
        parts.append(f"username @{m.chat.username}")
    try:
        full = await m.bot.get_chat(cid)
        if getattr(full, "bio", None):
            parts.append(f"о себе: {full.bio}")
    except Exception as e:
        logging.info("peer get_chat: %s", e)
    try:
        ph = await m.bot.get_user_profile_photos(cid, limit=1)
        if ph.total_count:
            parts.append("есть аватарка")
            if want_avatar:
                f = await m.bot.download(ph.photos[0][-1])
                img = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
                d, _, _ = await chat_long([{"role": "user", "content": [{"type": "text", "text": "Опиши аватарку человека одной короткой фразой."},
                                                                        {"type": "image_url", "image_url": {"url": img}}]}], model, 1, 60)
                parts.append("на аватарке: " + d.strip()[:150])
    except Exception as e:
        logging.info("peer photo: %s", e)
    info = ", ".join(parts) or "неизвестен"
    PEER[cid] = (time.time(), info)
    return info


async def lc_command(m, owner, arg):
    await biz_cleanup(m)
    key = (m.business_connection_id, m.chat.id)
    on = await lc_active(m)
    low = arg.lower()
    want = False if low in ("off", "выкл", "0") else True if low in ("on", "вкл", "1") else (not on)
    if want:
        await q("INSERT OR IGNORE INTO lc_chats VALUES(?,?)", key, commit=True)
    else:
        await q("DELETE FROM lc_chats WHERE conn=? AND chat=?", key, commit=True)
    LC_HIST.pop(key, None)
    u = await get_user(owner)
    info = await peer_info(m, bool(u["lc_avatar"]), await biz_model(owner, u))
    style = LC_STYLES.get(u["lc_style"], LC_STYLES["short"])[0]
    await biz_tell_owner(m, owner, f"💬 Автоответчик {'ВКЛЮЧЁН' if want else 'выключен'} в чате: {info}.\nСтиль: {style}. "
                                   "Изменить: /settings → Автоответчик. Выключить: .lc в этом чате.")


def lc_note_owner(m, body):
    key = (m.business_connection_id, m.chat.id)
    h = LC_HIST.get(key)
    if h is not None and body and not (h and h[-1]["role"] == "assistant" and h[-1]["content"] == body):
        h.append({"role": "assistant", "content": body})
        del h[:-12]


async def lc_reply(m, owner):
    incoming = (m.text or m.caption or "").strip()
    if not incoming:
        return
    u = await get_user(owner)
    model = await biz_model(owner, u)
    ok, msg = await can_use(owner, u, model)
    if not ok:
        return await biz_tell_owner(m, owner, "💬 Автоответчик не ответил: " + re.sub(r"<[^>]+>", "", msg))
    key = (m.business_connection_id, m.chat.id)
    hist = LC_HIST.setdefault(key, [])
    hist.append({"role": "user", "content": incoming})
    del hist[:-12]
    st = u["lc_style"]
    style = u["lc_custom"] if st == "custom" else LC_STYLES.get(st, LC_STYLES["short"])[1]
    extra = f" Дополнительно: {u['lc_custom']}." if st != "custom" and u["lc_custom"] else ""
    info = await peer_info(m, bool(u["lc_avatar"]), model)
    sys_ = f"{LC_BASE} {style}{extra} Что известно о собеседнике: {info}."
    stop = asyncio.Event()
    await biz_typing_now(m)  # «печатает…» появляется сразу
    spawn(biz_typing(m.bot, m.chat.id, m.business_connection_id, stop))
    try:
        ans, raw, _ = await chat_long([{"role": "system", "content": sys_}] + hist, model, 1, 300)
        await spend(owner, u, model, raw)
        ans = re.sub(r"[*_`#]", "", ans).strip()
        if ans:
            await asyncio.sleep(min(3.0, 0.4 + len(ans) / 45))  # живая пауза «печатает»
            await m.answer(ans, parse_mode=None)
            hist.append({"role": "assistant", "content": ans})
    except Exception as e:
        await biz_tell_owner(m, owner, f"⚠️ Ошибка автоответчика: {str(e)[:250]}")
    finally:
        stop.set()


def lc_screen(u):
    cur = u["lc_style"]
    btns = [(("✅ " if k == cur else "") + v[0], f"lc:s:{k}") for k, v in LC_STYLES.items() if k != "custom"]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([(("✅ " if cur == "custom" else "") + "✏️ Свой стиль", "lc:custom")])
    rows.append([(f"🖼 Видеть аватарку собеседника: {'вкл' if u['lc_avatar'] else 'выкл'}", "lc:ava")])
    custom = f"\nСвой текст: {H.escape(u['lc_custom'][:200])}" if u["lc_custom"] else ""
    return ("💬 <b>Автоответчик .lc</b>\nНапиши <code>.lc</code> в любом своём чате (через Telegram Business), и бот начнёт отвечать за тебя. "
            f"Ещё раз <code>.lc</code> выключит.\n\nСтиль ответов: <b>{LC_STYLES.get(cur, LC_STYLES['short'])[0]}</b>{custom}\n"
            "Бот видит имя, username и «о себе» собеседника. Аватарку тоже, если включишь (это чуть дороже по токенам)."), kb(rows, back="nav:set")


@router.callback_query(F.data.startswith("lc:"))
async def lc_menu(c: CallbackQuery, state: FSMContext):
    a, uid = c.data[3:], c.from_user.id
    u = await get_user(uid)
    if a.startswith("s:") and a[2:] in LC_STYLES:
        await q("UPDATE users SET lc_style=? WHERE id=?", (a[2:], uid), commit=True)
    elif a == "ava":
        await q("UPDATE users SET lc_avatar=? WHERE id=?", (0 if u["lc_avatar"] else 1, uid), commit=True)
    elif a == "custom":
        await state.set_state(G.lc)
        await edit_msg(c.message, "✏️ Напиши, как отвечать за тебя (до 400 знаков), например: «отвечай коротко, с иронией, на «ты»».", kb([], back="lc:menu"))
        return await c.answer()
    txt, k = lc_screen(await get_user(uid))
    await edit_msg(c.message, txt, k)
    await c.answer()


@router.message(G.lc, F.text)
async def st_lc(m: Message, state: FSMContext):
    await q("UPDATE users SET lc_custom=?, lc_style='custom' WHERE id=?", (m.text.strip()[:400], m.from_user.id), commit=True)
    await state.clear()
    txt, k = lc_screen(await get_user(m.from_user.id))
    await reply(m, "✅ Сохранено.\n\n" + txt, reply_markup=k)


# ---------------------- команда .ai ----------------------
async def ai_command(m, owner, prompt):
    await biz_cleanup(m)  # команда сразу исчезает из чата
    if not prompt:
        return await biz_tell_owner(m, owner, "Напиши вопрос после .ai, например: .ai что такое блокчейн")
    u = await get_user(owner)
    plan = eff_plan(u)
    model = await biz_model(owner, u)
    mm = re.match(r"^-m\s+(\S+)\s+(.*)$", prompt, re.S)
    if mm and mm.group(1) in MODELS and mm.group(1) not in OFF and biz_allowed(owner, plan, mm.group(1)):
        model, prompt = mm.group(1), mm.group(2)
    ok, msg = await can_use(owner, u, model)
    if not ok:
        return await biz_tell_owner(m, owner, re.sub(r"<[^>]+>", "", msg))
    stop = asyncio.Event()
    await biz_typing_now(m)  # «печатает…» появляется сразу
    spawn(biz_typing(m.bot, m.chat.id, m.business_connection_id, stop))  # собеседник видит «печатает…»
    try:
        if re.search(r"телеграф|telegra", prompt, re.I) and re.search(r"стат", prompt, re.I):
            topic = re.sub(r"(?i)(сделай|напиши|создай|телеграф|telegraph|статью|статья|на тему|про)\s*", " ", prompt).strip() or prompt
            am = await article_model_for(owner, u)
            url, charged = await make_article(owner, u, topic, am, await article_images(owner, u))
            await spend(owner, u, am, 0, fixed=charged)
            return await m.answer(f"📰 {url}")
        web = await web_context(prompt)
        last = f"[Данные из интернета]\n{web}\n\n[Вопрос]\n{prompt}\nОтветь по этим данным, дай ссылки." if web else prompt
        ans, raw, _ = await chat_long([{"role": "system", "content": "Ты отвечаешь собеседнику в чате от имени владельца аккаунта. Коротко, по делу, без LaTeX и таблиц."},
                                       {"role": "user", "content": last}], model, 1, 1200)
        await spend(owner, u, model, raw)
        for ch in chunk_text(ans, 3000):
            await send_html(m, await emoji_filter(md_html(ch)))
    except Exception as e:
        await biz_tell_owner(m, owner, f"⚠️ Ошибка .ai: {str(e)[:300]}")
    finally:
        stop.set()


@router.business_message()
async def on_biz(m: Message):
    owner = await biz_owner(m)
    if not owner:
        return
    body = (m.text or m.caption or "").strip()
    if m.from_user and m.from_user.id == owner:  # пишет владелец аккаунта
        await ensure_user(m.from_user)
        low = body.lower()
        if low.startswith(".lc"):
            return await lc_command(m, owner, body[3:].strip())
        if low.startswith(".ai"):
            return await ai_command(m, owner, body[3:].strip())
        return lc_note_owner(m, body)  # обычные реплики владельца идут в контекст автоответчика
    if await lc_active(m):  # пишет собеседник, а автоответчик включён
        await lc_reply(m, owner)


# ====================== ВЕБ-СЕРВЕР: студия кода и Platega ======================
BOT = None
BOT_USERNAME = ""
STUDIO_HTML = r'''<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<title>Студия кода</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Onest:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root {
  --bg: var(--tg-theme-bg-color, #ffffff);
  --bg2: var(--tg-theme-secondary-bg-color, #f0f2f6);
  --text: var(--tg-theme-text-color, #151a22);
  --hint: var(--tg-theme-hint-color, #6a7280);
  --accent: var(--tg-theme-button-color, #2f7cf6);
  --on-accent: var(--tg-theme-button-text-color, #ffffff);
  --line: rgba(128, 134, 148, .30);
  --ok: #22a06b; --bad: #e5484d;
  --ui: "Onest", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html, body { margin: 0; background: var(--bg); color: var(--text); font: 16px/1.45 var(--ui); -webkit-tap-highlight-color: transparent; }
body { padding: env(safe-area-inset-top) 0 calc(24px + env(safe-area-inset-bottom)); }
button, input, textarea { font: inherit; color: inherit; }
button { cursor: pointer; }
button:focus-visible, input:focus-visible, textarea:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.wrap { max-width: 640px; margin: 0 auto; padding: 14px 14px 0; display: flex; flex-direction: column; gap: 14px; }

header { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
h1 { font: 700 24px/1.1 var(--ui); margin: 0; letter-spacing: -.3px; }
h1 small { display: block; font: 400 13px var(--ui); color: var(--hint); margin-top: 3px; letter-spacing: 0; }
.bal { background: var(--bg2); border-radius: 999px; padding: 7px 12px; font: 500 14px var(--mono); white-space: nowrap; }
.bal b { font-weight: 500; }

.lbl { font-size: 13px; color: var(--hint); margin: 0 0 6px; }
.models { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; scrollbar-width: none; }
.models::-webkit-scrollbar { display: none; }
.chip { flex: none; background: var(--bg2); border: 2px solid transparent; border-radius: 12px; padding: 9px 12px; text-align: left; min-height: 48px; }
.chip b { display: block; font-weight: 600; font-size: 15px; white-space: nowrap; }
.chip span { font: 400 12px var(--mono); color: var(--hint); }
.chip[aria-pressed="true"] { border-color: var(--accent); }
.chip.lock { opacity: .5; }

textarea, input.line { width: 100%; background: var(--bg2); border: 2px solid transparent; border-radius: 12px; padding: 12px; resize: vertical; }
textarea#prompt { min-height: 96px; }
.row { display: flex; gap: 8px; align-items: center; }
.row > input { flex: 1; min-width: 0; }
.exts { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.ext { background: transparent; border: 1px solid var(--line); border-radius: 999px; padding: 4px 10px; font: 400 13px var(--mono); }
.btn { background: var(--accent); color: var(--on-accent); border: 0; border-radius: 12px; padding: 13px 16px; font-weight: 600; min-height: 48px; }
.btn.sec { background: var(--bg2); color: var(--text); }
.btn.sm { padding: 8px 12px; min-height: 40px; font-size: 14px; border-radius: 10px; }
.btn:disabled { opacity: .55; cursor: default; }
.btn:active { transform: scale(.98); }
.bar { height: 4px; border-radius: 4px; background: var(--bg2); overflow: hidden; }
.bar i { display: block; height: 100%; width: 30%; background: var(--accent); animation: slide 1.1s ease-in-out infinite; }
@keyframes slide { 0% { margin-left: -30%; } 100% { margin-left: 100%; } }
.msg { font-size: 14px; color: var(--hint); min-height: 20px; }
.msg.err { color: var(--bad); }

.tabs { display: flex; gap: 4px; overflow-x: auto; border-bottom: 1px solid var(--line); scrollbar-width: none; }
.tabs::-webkit-scrollbar { display: none; }
.tab { flex: none; background: none; border: 0; border-bottom: 3px solid transparent; padding: 9px 12px; font: 500 14px var(--mono); display: flex; align-items: center; gap: 7px; }
.tab i { width: 9px; height: 9px; border-radius: 50%; background: var(--hint); }
.tab[aria-selected="true"] { border-bottom-color: var(--accent); }
#editor { font: 400 13px/1.55 var(--mono); min-height: 240px; max-height: 55vh; white-space: pre; overflow: auto; tab-size: 2; border-radius: 0 0 12px 12px; }
.acts { display: flex; gap: 8px; flex-wrap: wrap; }
.out { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
.out .tabs { padding: 0 6px; }
#frame { width: 100%; height: 320px; border: 0; background: #fff; display: block; }
#term { font: 400 13px/1.5 var(--mono); padding: 10px 12px; min-height: 160px; max-height: 40vh; overflow: auto; white-space: pre-wrap; word-break: break-word; margin: 0; background: var(--bg2); user-select: text; -webkit-user-select: text; }
#term .err { color: var(--bad); } #term .sys { color: var(--hint); } #term .in { color: var(--accent); } #term .warn { color: #c98a00; }
.repl { display: flex; gap: 6px; align-items: center; padding: 6px 10px; border-top: 1px solid var(--line); font-family: var(--mono); }
.repl input { flex: 1; min-width: 0; background: transparent; border: 0; padding: 8px 4px; font: 400 14px var(--mono); }
.toast { position: fixed; left: 50%; bottom: calc(18px + env(safe-area-inset-bottom)); transform: translateX(-50%); background: var(--text); color: var(--bg); padding: 10px 14px; border-radius: 12px; font-size: 14px; max-width: 90vw; z-index: 10; }
@media (prefers-reduced-motion: reduce) { .bar i { animation: none; width: 100%; } .btn:active { transform: none; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Студия кода<small>Опиши задачу, получи файл, запусти здесь же</small></h1>
    <div class="bal" id="bal" title="Осталось токенов">…</div>
  </header>

  <div id="denied" hidden class="msg err">Открой студию через бота в Telegram: кнопка «Студия кода».</div>

  <section id="composer">
    <p class="lbl">Модель (для кода доступны с коэффициентом до ×<span id="cap">1.5</span>)</p>
    <div class="models" id="models" role="group" aria-label="Выбор модели"></div>

    <p class="lbl" style="margin-top:14px">Что написать?</p>
    <textarea id="prompt" placeholder="Например: калькулятор на HTML и JS с тёмной темой" maxlength="6000"></textarea>

    <p class="lbl" style="margin-top:12px">Формат или имя файла (необязательно, можно любое расширение)</p>
    <div class="row"><input class="line" id="fmt" list="extlist" placeholder="index.html, bot.py, query.sql, main.rs …" autocomplete="off" autocapitalize="off">
      <datalist id="extlist"></datalist></div>
    <div class="exts" id="exts"></div>

    <div style="margin-top:14px"><button class="btn" id="go" style="width:100%" onclick="generate(false)">Создать</button></div>
    <div class="bar" id="bar" hidden style="margin-top:10px"><i></i></div>
    <div class="msg" id="msg" role="status"></div>
  </section>

  <section id="result" hidden>
    <div class="tabs" id="ftabs" role="tablist"></div>
    <textarea id="editor" spellcheck="false" wrap="off" aria-label="Код файла"></textarea>
    <div class="acts" style="margin-top:10px">
      <button class="btn sm" onclick="download(cur())">Скачать</button>
      <button class="btn sm" onclick="sendFile(cur())">В чат</button>
      <button class="btn sm sec" onclick="copyCode()">Копировать</button>
      <button class="btn sm" id="runBtn" onclick="runCurrent()">▶ Запустить</button>
      <button class="btn sm sec" onclick="sendAll()">Все файлы в чат</button>
    </div>

    <p class="lbl" style="margin-top:14px">Доработать</p>
    <div class="row"><input class="line" id="editPrompt" placeholder="Что изменить: добавь кнопку сброса, исправь ошибку…" maxlength="2000">
      <button class="btn sm" onclick="generate(true)">Отправить</button></div>

    <div class="out" style="margin-top:14px">
      <div class="tabs" role="tablist">
        <button class="tab" id="tPrev" role="tab" aria-selected="true" onclick="showOut('prev')">Просмотр</button>
        <button class="tab" id="tTerm" role="tab" aria-selected="false" onclick="showOut('term')">Терминал</button>
        <button class="tab" style="margin-left:auto" onclick="clearTerm()">Очистить</button>
      </div>
      <iframe id="frame" sandbox="allow-scripts allow-forms allow-modals allow-popups" title="Просмотр"></iframe>
      <div id="termBox" hidden>
        <pre id="term"></pre>
        <div class="repl"><button class="btn sm sec" id="lang" onclick="toggleLang()">py</button><span>❯</span>
          <input id="cmd" placeholder="команда" autocomplete="off" autocapitalize="off" spellcheck="false"></div>
      </div>
    </div>
  </section>
</div>
<div class="toast" id="toast" hidden></div>

<script>
const $ = id => document.getElementById(id);
const TG = window.Telegram && Telegram.WebApp;
try { if (TG) { TG.ready(); TG.expand(); } } catch (e) {}
const initData = (TG && TG.initData) || '';
const EXTS = ['html', 'css', 'js', 'ts', 'py', 'json', 'sql', 'sh', 'java', 'kt', 'cs', 'cpp', 'c', 'go', 'rs', 'php', 'rb', 'swift', 'md', 'yaml', 'xml', 'txt'];
const DOT = { html: '#e44d26', css: '#2965f1', js: '#e8c600', ts: '#3178c6', py: '#3776ab', json: '#8a8f98', sql: '#e38c00', sh: '#4eaa25', java: '#e76f00',
  kt: '#a97bff', cs: '#68217a', cpp: '#00599c', c: '#00599c', go: '#00add8', rs: '#dea584', php: '#777bb4', rb: '#cc342d', swift: '#fa7343', md: '#8a8f98' };
let me = null, model = '', files = [], active = 0, busy = false, replLang = 'py', outTab = 'prev';

/* ---------- утилиты ---------- */
const fmtNum = n => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
const extOf = n => ((n || '').split('.').pop() || '').toLowerCase();
function kindOf(name) {
  const e = extOf(name);
  if (['html', 'htm', 'svg'].includes(e)) return 'html';
  if (['js', 'mjs'].includes(e)) return 'js';
  if (e === 'py') return 'py';
  return null;
}
function toast(t) { const el = $('toast'); el.textContent = t; el.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => { el.hidden = true; }, 3200); }
function setMsg(t, err) { const m = $('msg'); m.textContent = t || ''; m.className = 'msg' + (err ? ' err' : ''); }
function hap(k) { try { if (TG && TG.HapticFeedback) { if (k === 'ok') TG.HapticFeedback.notificationOccurred('success'); else if (k === 'bad') TG.HapticFeedback.notificationOccurred('error'); else TG.HapticFeedback.impactOccurred('light'); } } catch (e) {} }
const cur = () => files[active] || { name: 'file.txt', content: '' };

const API = (new URLSearchParams(location.search).get('api') || '').replace(/\/$/, '');  // адрес бота, если страница лежит отдельно (GitHub)
async function api(path, body) {
  return fetch(API + path, { method: body ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json', 'Authorization': 'tma ' + initData }, body: body ? JSON.stringify(body) : undefined });
}

/* ---------- профиль и модели ---------- */
function renderBal() { $('bal').innerHTML = me ? '🪙 <b>' + (me.left === null ? '∞' : fmtNum(me.left)) + '</b>' : '…'; }
function renderModels() {
  const box = $('models'); box.innerHTML = '';
  for (const m of me.models) {
    const b = document.createElement('button');
    b.className = 'chip' + (m.ok ? '' : ' lock'); b.setAttribute('aria-pressed', m.id === model);
    const t = document.createElement('b'); t.textContent = (m.ok ? '' : '🔒 ') + m.title;
    const s = document.createElement('span'); s.textContent = '×' + m.mult;
    b.appendChild(t); b.appendChild(s);
    b.onclick = () => { if (!m.ok) { toast('Эта модель недоступна на твоём тарифе'); return; } model = m.id; saveState(); renderModels(); };
    box.appendChild(b);
  }
}
async function init() {
  const ex = $('exts'), dl = $('extlist');
  for (const e of EXTS) {
    const b = document.createElement('button'); b.className = 'ext'; b.textContent = '.' + e; b.onclick = () => { $('fmt').value = e === 'html' ? 'index.html' : 'main.' + e; };
    ex.appendChild(b);
    const o = document.createElement('option'); o.value = 'main.' + e; dl.appendChild(o);
  }
  if (!initData) { $('denied').hidden = false; $('composer').hidden = true; return; }
  try {
    const r = await api('/api/me');
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Нет доступа');
    me = await r.json();
  } catch (e) { $('denied').textContent = String(e.message || e); $('denied').hidden = false; $('composer').hidden = true; return; }
  $('cap').textContent = me.cap;
  const saved = loadState();
  model = (saved.model && me.models.some(m => m.id === saved.model && m.ok)) ? saved.model : me.default;
  if (saved.files && saved.files.length) { files = saved.files; active = 0; showResult(); }
  renderBal(); renderModels();
}
function saveState() { try { localStorage.setItem('studio', JSON.stringify({ model, files: files.slice(0, 8) })); } catch (e) {} }
function loadState() { try { return JSON.parse(localStorage.getItem('studio') || '{}'); } catch (e) { return {}; } }

/* ---------- генерация ---------- */
function setBusy(on, text) { busy = on; $('go').disabled = on; $('bar').hidden = !on; if (text !== undefined) setMsg(text); }
async function generate(isEdit) {
  if (busy) return;
  const box = isEdit ? $('editPrompt') : $('prompt');
  const prompt = box.value.trim();
  if (!prompt) { toast('Опиши задачу'); return; }
  const fmt = $('fmt').value.trim();
  const body = { model, prompt: (!isEdit && fmt) ? prompt + '\n\nИмя или формат файла: ' + fmt : prompt, files: isEdit ? files : [] };
  setBusy(true, 'Пишу код…');
  try {
    const res = await api('/api/code', body);
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || 'Ошибка ' + res.status);
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = '', got = false;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
        if (!line) continue;
        const m = JSON.parse(line);
        if (m.error) throw new Error(m.error);
        if (m.p != null) setMsg('Пишу код… получено ' + fmtNum(m.p) + ' симв.');
        if (m.done) { got = true; finish(m.done, isEdit); box.value = isEdit ? '' : box.value; }
      }
    }
    if (!got) throw new Error('Соединение оборвалось, токены могли быть списаны. Проверь баланс.');
  } catch (e) { setBusy(false, String(e.message || e)); $('msg').className = 'msg err'; hap('bad'); return; }
  setBusy(false);
}
function finish(d, isEdit) {
  if (!d.files.length) { setMsg(d.note || 'Модель не вернула файлов. Попробуй уточнить задачу.', true); return; }
  if (isEdit) { for (const nf of d.files) { const i = files.findIndex(f => f.name === nf.name); if (i >= 0) files[i] = nf; else files.push(nf); } }
  else files = d.files;
  active = Math.max(0, files.findIndex(f => f.name === d.files[0].name));
  if (me) { me.left = d.left; renderBal(); }
  setMsg((d.note ? d.note + ' · ' : '') + 'Списано ' + fmtNum(d.charged) + (d.approx ? ' (≈)' : '') + ' токенов');
  saveState(); showResult(); hap('ok');
}

/* ---------- файлы ---------- */
function showResult() { $('result').hidden = false; renderTabs(); loadEditor(); }
function renderTabs() {
  const box = $('ftabs'); box.innerHTML = '';
  files.forEach((f, i) => {
    const b = document.createElement('button'); b.className = 'tab'; b.setAttribute('role', 'tab'); b.setAttribute('aria-selected', i === active);
    const d = document.createElement('i'); d.style.background = DOT[extOf(f.name)] || 'var(--hint)';
    b.appendChild(d); b.appendChild(document.createTextNode(f.name));
    b.onclick = () => { syncEditor(); active = i; renderTabs(); loadEditor(); };
    box.appendChild(b);
  });
}
function loadEditor() { $('editor').value = cur().content; const k = kindOf(cur().name); $('runBtn').textContent = k ? '▶ Запустить' : '▶ Запуск недоступен'; $('runBtn').disabled = !k; }
function syncEditor() { if (files[active]) { files[active].content = $('editor').value; saveState(); } }
$('editor').addEventListener('input', syncEditor);
function download(f) {
  syncEditor();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([f.content], { type: 'text/plain;charset=utf-8' }));
  a.download = f.name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  toast('Скачивание запущено. Если файл не появился, нажми «В чат»');
}
async function sendFile(f) {
  syncEditor();
  try {
    const r = await api('/api/send', { name: f.name, content: f.content });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Ошибка ' + r.status);
    toast('Файл ' + f.name + ' отправлен в чат с ботом'); hap('ok');
  } catch (e) { toast(String(e.message || e)); }
}
async function sendAll() { syncEditor(); for (const f of files.slice(0, 10)) await sendFile(f); }
function copyCode() {
  syncEditor(); const t = cur().content;
  if (navigator.clipboard) navigator.clipboard.writeText(t).then(() => toast('Скопировано')).catch(() => toast('Не удалось скопировать'));
  else toast('Скопируй из окна кода вручную');
}

/* ---------- запуск: просмотр и терминал ---------- */
const PRELUDE = '<script>(function(){function s(t,m){parent.postMessage({__studio:1,t:t,m:m},"*")}["log","info","warn","error"].forEach(function(k){var o=console[k];console[k]=function(){s(k,[].map.call(arguments,function(a){try{return typeof a==="object"?JSON.stringify(a):String(a)}catch(e){return String(a)}}).join(" "));o.apply(console,arguments)}});addEventListener("error",function(e){s("error",e.message+" (строка "+(e.lineno||0)+")")});addEventListener("unhandledrejection",function(e){s("error","Promise: "+(e.reason&&e.reason.message||e.reason))})})()<\/script>';
function withPrelude(html) {
  if (/<head[^>]*>/i.test(html)) return html.replace(/<head[^>]*>/i, m => m + PRELUDE);
  if (/<html[^>]*>/i.test(html)) return html.replace(/<html[^>]*>/i, m => m + PRELUDE);
  return PRELUDE + html;
}
function showOut(tab) {
  outTab = tab;
  $('frame').hidden = tab !== 'prev'; $('termBox').hidden = tab !== 'term';
  $('tPrev').setAttribute('aria-selected', tab === 'prev'); $('tTerm').setAttribute('aria-selected', tab === 'term');
}
function log(text, cls) {
  const t = $('term'), s = document.createElement('span');
  s.className = cls || ''; s.textContent = text + '\n'; t.appendChild(s); t.scrollTop = t.scrollHeight;
}
function clearTerm() { $('term').innerHTML = ''; }
window.addEventListener('message', e => {
  const d = e.data;
  if (!d || !d.__studio || e.source !== $('frame').contentWindow) return;
  log((d.t === 'error' ? '✖ ' : '') + d.m, d.t === 'error' ? 'err' : d.t === 'warn' ? 'warn' : '');
});
async function runCurrent() {
  syncEditor();
  const f = cur(), k = kindOf(f.name);
  if (!k) { toast('Этот формат запустить нельзя: скачай файл'); return; }
  if (k === 'html') { showOut('prev'); $('frame').srcdoc = withPrelude(f.content); log('▶ ' + f.name, 'sys'); }
  else if (k === 'js') { showOut('term'); log('▶ ' + f.name, 'sys'); $('frame').srcdoc = '<!doctype html><html><body>' + PRELUDE + '<script>' + f.content.replace(/<\/script/gi, '<\\/script') + '<\/script></body></html>'; }
  else { showOut('term'); log('▶ ' + f.name, 'sys'); await runPy(f.content); }
  $('result').scrollIntoView && $('result').scrollIntoView({ block: 'end', behavior: 'smooth' });
}
let pyReady = null;
function loadPy() {
  if (!pyReady) pyReady = new Promise((res, rej) => {
    const s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js';
    s.onload = async () => {
      try {
        const py = await loadPyodide({ indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/' });
        py.setStdout({ batched: t => log(t) }); py.setStderr({ batched: t => log(t, 'err') });
        py.setStdin({ stdin: () => prompt('input()') });
        res(py);
      } catch (e) { pyReady = null; rej(e); }
    };
    s.onerror = () => { pyReady = null; rej(new Error('Python не загрузился (нужен интернет)')); };
    document.head.appendChild(s);
  });
  return pyReady;
}
async function runPy(code) {
  try {
    log('Готовлю Python (первый раз около 10 МБ)…', 'sys');
    const py = await loadPy();
    await py.loadPackagesFromImports(code);
    await py.runPythonAsync(code);
    log('— готово —', 'sys');
  } catch (e) { log(String((e && e.message) || e), 'err'); }
}
function toggleLang() { replLang = replLang === 'py' ? 'js' : 'py'; $('lang').textContent = replLang; }
$('cmd').addEventListener('keydown', async e => {
  if (e.key !== 'Enter') return;
  const c = $('cmd').value; if (!c.trim()) return;
  $('cmd').value = ''; log('❯ ' + c, 'in');
  if (replLang === 'py') {
    try { const py = await loadPy(); const r = await py.runPythonAsync(c); if (r !== undefined && r !== null) log(String(r)); }
    catch (er) { log(String((er && er.message) || er), 'err'); }
  } else { $('frame').srcdoc = '<!doctype html><html><body>' + PRELUDE + '<script>try{var r=eval(' + JSON.stringify(c) + ');if(r!==undefined)console.log(r)}catch(e){console.error(e.message)}<\/script></body></html>'; }
});
init();
</script>
</body>
</html>
'''

STUDIO_SYS = ("Ты программист внутри мини-приложения. Отвечай ТОЛЬКО файлами в формате:\n<<<FILE: имя.расширение>>>\nкод\n<<<END>>>\n"
              "Можно создать файл любого формата (код, разметка, конфиг, данные, скрипт). Если формат не указан, выбери подходящий. "
              "Код рабочий и компактный, без заглушек и лишних комментариев. После файлов добавь одну короткую строку-пояснение обычным текстом. "
              "Если даны текущие файлы, верни полностью только те файлы, которые изменились.")
LANG_EXT = {"python": "py", "py": "py", "javascript": "js", "js": "js", "typescript": "ts", "ts": "ts", "html": "html", "css": "css",
            "json": "json", "bash": "sh", "sh": "sh", "sql": "sql", "java": "java", "cpp": "cpp", "c": "c", "go": "go", "rust": "rs",
            "php": "php", "kotlin": "kt", "swift": "swift", "ruby": "rb", "yaml": "yaml", "xml": "xml", "markdown": "md", "md": "md"}


class _TU:
    def __init__(self, d):
        self.id = int(d["id"])
        self.username = d.get("username") or ""
        self.full_name = (str(d.get("first_name", "")) + " " + str(d.get("last_name", ""))).strip() or "user"


def sanitize_name(n):
    n = os.path.basename(str(n).replace("\\", "/")).strip()
    n = re.sub(r"[^\w.\- ()]", "_", n, flags=re.U)[:80].strip(". ")
    return n or "file.txt"


def verify_init_data(init_data):
    """Проверка подписи Telegram Mini App (initData). -> dict пользователя или None."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        h = pairs.pop("hash", "")
        dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, h):
            return None
        if time.time() - int(pairs.get("auth_date", 0)) > 86400:
            return None
        return json.loads(pairs["user"])
    except Exception:
        return None


async def public_url():
    u = (await setting("public_url")) or _env("PUBLIC_URL")
    if not u and _env("RAILWAY_PUBLIC_DOMAIN"):
        u = "https://" + _env("RAILWAY_PUBLIC_DOMAIN")
    return u.rstrip("/")


def json_err(msg, status=400):
    return web.json_response({"error": re.sub(r"<[^>]+>", "", msg)}, status=status)


async def web_auth(request):
    h = request.headers.get("Authorization", "")
    user = verify_init_data(h[4:]) if h.startswith("tma ") else None
    if not user or not user.get("id"):
        return None, None, json_err("Открой студию через бота в Telegram", 401)
    await ensure_user(_TU(user))
    uid = int(user["id"])
    u = await get_user(uid)
    if u["banned"] and not exempt(uid):
        return None, None, json_err(await text("banned"), 403)
    if await setting("require_key") == "1" and not exempt(uid) and not u["activated"]:
        return None, None, json_err("Сначала введи секретный ключ в боте", 403)
    return uid, u, None


async def studio_url():
    """Адрес студии: отдельный файл на GitHub (webapp_code) + адрес бота для API, либо встроенная страница бота."""
    base, hosted = await public_url(), (await setting("webapp_code")).strip()
    if hosted and base:
        return hosted + ("&" if "?" in hosted else "?") + "api=" + quote(base, safe="")
    return base + "/studio" if base else ""


@web.middleware
async def cors_mw(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            resp = e
    if not getattr(resp, "prepared", False):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


async def studio_page(request):
    return web.Response(text=STUDIO_HTML, content_type="text/html", headers={"Cache-Control": "no-store"})


async def api_me(request):
    uid, u, err = await web_auth(request)
    if err:
        return err
    t = tariff(eff_plan(u))
    models = []
    for m in code_models():
        ok = exempt(uid) or plan_allows(t, m) or await grant_left(uid, m) > 0
        models.append({"id": m, "title": TITLE[m], "mult": f"{MULT[m]:g}", "ok": bool(ok)})
    default = cur_code_model(u)
    if not any(x["id"] == default and x["ok"] for x in models):
        default = next((x["id"] for x in models if x["ok"]), default)
    return web.json_response({"left": None if exempt(uid) else left_tokens(u, t), "plan": t["name"], "models": models,
                              "default": default, "cap": f"{code_max():g}"})


async def api_code(request):
    uid, u, err = await web_auth(request)
    if err:
        return err
    try:
        data = await request.json()
    except Exception:
        return json_err("Неверный запрос")
    model, prompt = str(data.get("model", "")), str(data.get("prompt", "")).strip()[:6000]
    files_in = [(sanitize_name(f.get("name", "")), str(f.get("content", ""))[:40000])
                for f in (data.get("files") or [])[:10] if isinstance(f, dict)]
    if not prompt:
        return json_err("Опиши задачу")
    if model not in code_models():
        return json_err(f"Для кода доступны модели с коэффициентом до ×{code_max():g}")
    ok, msg = await can_use(uid, u, model)
    if not ok:
        return json_err(msg, 403)
    lock = LOCKS.setdefault(("studio", uid), asyncio.Lock())
    if lock.locked():
        return json_err("Подожди: предыдущий запрос ещё выполняется", 429)
    ctx = prompt
    if files_in:
        ctx = "Текущие файлы:\n" + "\n".join(f"<<<FILE: {n}>>>\n{c}\n<<<END>>>" for n, c in files_in) + "\n\nЗадача: " + prompt
    msgs = [{"role": "system", "content": STUDIO_SYS}, {"role": "user", "content": ctx}]
    resp = web.StreamResponse(headers={"Content-Type": "application/x-ndjson; charset=utf-8", "Cache-Control": "no-store", "X-Accel-Buffering": "no",
                                       "Access-Control-Allow-Origin": "*"})
    alive = [True]

    async def write(obj):
        if alive[0]:
            try:
                await resp.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
            except Exception:
                alive[0] = False
    async with lock:
        await resp.prepare(request)
        size = [0]

        async def prog(t):
            size[0] = len(t)
        task = spawn(chat_long(msgs, model, await seti("code_rounds", 3), await seti("code_tokens", 4000), prog))
        while True:
            done, _ = await asyncio.wait({task}, timeout=2)
            if done:
                break
            await write({"p": size[0]})
        try:
            ans, raw, approx = task.result()
        except Exception as e:
            await write({"error": "Ошибка API: " + str(e)[:300]})
            return resp
        found = [(sanitize_name(n), c.rstrip("\n")) for n, c in FILE_RE.findall(ans)]
        if not found:  # модель ответила обычными блоками кода
            for i, (lang, code) in enumerate(re.findall(r"```(\w*)\n(.*?)```", ans, re.S)[:5], 1):
                found.append((f"file{i}.{LANG_EXT.get(lang.lower(), lang.lower() or 'txt')}", code.rstrip("\n")))
        note = re.sub(r"```.*?```", "", FILE_RE.sub("", ans), flags=re.S).strip()[:300]
        charged, left = await spend(uid, u, model, raw)
        await write({"done": {"files": [{"name": n, "content": c} for n, c in found], "note": note, "charged": charged,
                              "left": left, "approx": approx}})
    return resp


async def api_send(request):
    uid, u, err = await web_auth(request)
    if err:
        return err
    try:
        d = await request.json()
        name, content = sanitize_name(d.get("name", "")), str(d.get("content", ""))
    except Exception:
        return json_err("Неверный запрос")
    if len(content) > 300000:
        return json_err("Файл слишком большой")
    try:
        await BOT.send_document(uid, BufferedInputFile(content.encode("utf-8"), name), caption=f"📎 {H.escape(name)}")
    except Exception as e:
        return json_err("Не удалось отправить: " + str(e)[:150], 500)
    return web.json_response({"ok": True})


# ---------------------- Platega ----------------------
async def plat_cfg():
    base = (await setting("platega_url")) or _env("PLATEGA_URL", "https://app.platega.io")
    mid = (await setting("platega_id")) or _env("PLATEGA_MERCHANT_ID")
    sec = (await setting("platega_secret")) or _env("PLATEGA_SECRET")
    return base.rstrip("/"), mid, sec


async def plat_ready():
    _, mid, sec = await plat_cfg()
    return bool(mid and sec)


async def plat_methods():
    out = []
    for part in (await setting("platega_methods")).split(","):
        i, _, name = part.partition(":")
        if i.strip().isdigit():
            out.append((int(i), name.strip() or f"Метод {i.strip()}"))
    return out


async def platega_call(method, path, **kw):
    base, mid, sec = await plat_cfg()
    async with aiohttp.ClientSession(headers={"X-MerchantId": mid, "X-Secret": sec}, timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.request(method, base + path, **kw) as r:
            body = await r.text()
            try:
                j = json.loads(body)
            except Exception:
                j = {"raw": body[:300]}
            if r.status >= 400:
                raise RuntimeError(f"Platega {r.status}: {body[:200]}")
            return j


async def platega_create(rub, desc, payload, method):
    ret = f"https://t.me/{BOT_USERNAME}" if BOT_USERNAME else "https://t.me"
    body = {"paymentMethod": int(method), "paymentDetails": {"amount": float(rub), "currency": "RUB"}, "description": desc,
            "return": ret, "failedUrl": ret, "payload": payload}
    j = await platega_call("POST", "/transaction/process", json=body)
    tid, url = j.get("transactionId") or j.get("id"), j.get("redirect") or j.get("url")
    if not tid or not url:
        raise RuntimeError("Platega не вернула ссылку на оплату: " + str(j)[:200])
    return str(tid), url


async def platega_status(tid):
    j = await platega_call("GET", f"/transaction/{tid}")
    return str(j.get("status", "")).upper()


CANCEL_ST = ("CANCELED", "CANCELLED", "CHARGEBACKED", "EXPIRED", "FAILED")


async def platega_callback(request):
    _, mid, sec = await plat_cfg()
    ok = (mid and sec and hmac.compare_digest(request.headers.get("X-MerchantId", "").encode(), mid.encode())
          and hmac.compare_digest(request.headers.get("X-Secret", "").encode(), sec.encode()))
    if not ok:
        return web.Response(status=401, text="unauthorized")
    try:
        d = await request.json()
    except Exception:
        return web.Response(status=400, text="bad json")
    status, tid = str(d.get("status", "")).upper(), str(d.get("id") or d.get("transactionId") or "")
    p = await q("SELECT * FROM payments WHERE method='plat' AND ext=?", (tid,), one=True)
    if not p and str(d.get("payload", "")).startswith("pay:"):
        p = await q("SELECT * FROM payments WHERE id=?", (int(str(d["payload"])[4:] or 0),), one=True)
    if p and status == "CONFIRMED":
        await finish_payment(BOT, p["id"])
    elif p and status in CANCEL_ST and p["status"] == "new":
        await q("UPDATE payments SET status='rejected' WHERE id=?", (p["id"],), commit=True)
    return web.Response(text="ok")


async def platega_loop(bot):
    while True:
        await asyncio.sleep(20)
        try:
            if not await plat_ready():
                continue
            for p in await q("SELECT * FROM payments WHERE method='plat' AND status='new' AND ext!='' AND created>?", (int(time.time()) - 6 * 3600,)):
                st = await platega_status(p["ext"])
                if st == "CONFIRMED":
                    await finish_payment(bot, p["id"])
                elif st in CANCEL_ST:
                    await q("UPDATE payments SET status='rejected' WHERE id=?", (p["id"],), commit=True)
        except Exception:
            logging.exception("platega_loop")


async def start_web(bot):
    global BOT
    BOT = bot
    app = web.Application(client_max_size=2 * 1024 * 1024, middlewares=[cors_mw])
    app.router.add_get("/", lambda r: web.Response(text="Gimbek AI: ok"))
    app.router.add_get("/studio", studio_page)
    app.router.add_get("/api/me", api_me)
    app.router.add_post("/api/code", api_code)
    app.router.add_post("/api/send", api_send)
    app.router.add_post("/platega/callback", platega_callback)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(_env("PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logging.info("Веб-сервер запущен на порту %s", port)


# ====================== РЕЗЕРВНЫЕ КОПИИ, ПРОМТ ДЛЯ CLAUDE, ПРОВЕРКА МОДЕЛЕЙ ======================
EXPORT_TABLES = ["users", "tariffs", "grants", "payments", "tickets", "ticket_msgs", "settings", "texts", "buttons",
                 "emoji_pool", "model_banners", "biz", "lc_chats"]
NO_EXPORT_KEYS = {"api_key", "backup_key", "platega_secret", "platega_id", "tg_token", "last_backup_hash"}
SEED_DATA = None  # сюда встраивается резервная копия (промт для Claude); применяется, когда база пустая
SEED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed.json")


async def dump_db(history=False):
    tables = {}
    for t in EXPORT_TABLES + (["history"] if history else []):
        rows = [dict(r) for r in await q(f"SELECT * FROM {t}")]
        if t == "settings":
            rows = [r for r in rows if r["key"] not in NO_EXPORT_KEYS]  # ключи и секреты в копию не попадают
        tables[t] = rows
    return {"app": "gimbek-ai", "version": 1, "created": int(time.time()), "tables": tables}


async def reload_caches():
    global POOL
    SC.clear()
    TC.clear()
    for r in await q("SELECT * FROM settings"):
        SC[r["key"]] = r["value"]
    await load_buttons()
    await load_banners()
    await load_tariffs()
    load_disabled()
    POOL = None
    await reload_client()


async def restore_db(data):
    """Полное восстановление таблиц из копии. Секреты (ключи API) остаются как есть."""
    if not isinstance(data, dict) or data.get("app") != "gimbek-ai":
        raise ValueError("Это не резервная копия Gimbek AI")
    tables = data.get("tables", {})
    async with aiosqlite.connect(DB) as db:
        for t, rows in tables.items():
            if t not in EXPORT_TABLES + ["history"]:
                continue
            cur = await db.execute(f"PRAGMA table_info({t})")
            cols = [r[1] for r in await cur.fetchall()]
            if t == "settings":
                ph = ",".join("?" * len(NO_EXPORT_KEYS))
                await db.execute(f"DELETE FROM settings WHERE key NOT IN ({ph})", tuple(NO_EXPORT_KEYS))
            else:
                await db.execute(f"DELETE FROM {t}")
            for r in rows:
                keys = [k for k in r if k in cols]
                if not keys:
                    continue
                if t == "settings" and r.get("key") in NO_EXPORT_KEYS:
                    continue
                await db.execute(f"INSERT OR REPLACE INTO {t}({','.join(keys)}) VALUES({','.join('?' * len(keys))})", [r[k] for k in keys])
        await db.commit()
    await reload_caches()
    return len(tables.get("users", []))


async def send_backup(bot, reason="авто", force=False):
    """Отправляет JSON-копию владельцу и закрепляет её: после потери базы бот восстановится сам."""
    data = await dump_db()
    digest = hashlib.sha256(json.dumps(data["tables"], sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    if not force and digest == await setting("last_backup_hash"):
        return None
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    msg = await bot.send_document(OWNER_ID, BufferedInputFile(raw, time.strftime("gimbek_backup_%Y%m%d_%H%M.json")),
                                  caption=f"💾 Резервная копия ({reason}): пользователей {len(data['tables']['users'])}, {len(raw) // 1024} КБ",
                                  disable_notification=True)
    try:
        await bot.pin_chat_message(OWNER_ID, msg.message_id, disable_notification=True)
    except Exception as e:
        logging.info("pin backup: %s", e)
    await set_setting("last_backup_hash", digest)
    return msg


async def auto_restore(bot):
    """Если база пустая (после переустановки), поднимаем данные из закреплённой копии, SEED_DATA или seed.json."""
    if (await q("SELECT COUNT(*) c FROM users", one=True))["c"] > 0:
        return False
    data, src = None, ""
    if OWNER_ID:
        try:
            pm = getattr(await bot.get_chat(OWNER_ID), "pinned_message", None)
            if pm and pm.document and (pm.document.file_name or "").startswith("gimbek_backup"):
                data, src = json.loads((await bot.download(pm.document)).read().decode("utf-8")), "закреплённой копии в чате"
        except Exception as e:
            logging.warning("auto_restore pinned: %s", e)
    if data is None and SEED_DATA:
        data, src = SEED_DATA, "данных, встроенных в код"
    if data is None and os.path.exists(SEED_FILE):
        try:
            with open(SEED_FILE, encoding="utf-8") as f:
                data, src = json.load(f), "файла seed.json"
        except Exception as e:
            logging.warning("seed.json: %s", e)
    if data is None:
        return False
    n = await restore_db(data)
    logging.info("Данные восстановлены из %s: %s пользователей", src, n)
    if OWNER_ID:
        try:
            await bot.send_message(OWNER_ID, f"♻️ База была пустой. Данные восстановлены из {src}: пользователей {n}.")
        except Exception:
            pass
    return True


async def build_prompt():
    """Текст для Claude: что настроено сейчас + все данные, чтобы встроить их в код как стартовые."""
    data = await dump_db()
    T, now = data["tables"], int(time.time())
    L = ["Привет! Это резервная копия данных моего Telegram-бота Gimbek AI. Встрой её в main.py как стартовые данные: присвой словарь из блока "
         "JSON внизу переменной SEED_DATA (она уже есть в коде и сейчас равна None). Больше ничего в коде не меняй и верни обновлённый main.py целиком.", "",
         "=== КРАТКО: ЧТО В КОПИИ ==="]
    users = T["users"]
    paid = [u for u in users if u.get("plan") != "free" and (u.get("plan_until") or 0) > now]
    L.append(f"Пользователей: {len(users)}, платных сейчас: {len(paid)}, в бане: {sum(1 for u in users if u.get('banned'))}")
    for u in paid:
        L.append(f"  • id {u['id']} @{u.get('username') or '-'}: {u['plan']} до {fdate(u['plan_until'])}, израсходовано {u.get('tok_used', 0)} токенов, бонус {u.get('bonus', 0)}")
    L.append("Тарифы: " + "; ".join(f"{t['name']} ({t['tokens']} ток., ×{t['max_mult']}, картинки {'да' if t['img_ok'] else 'нет'}, цены {t['p30']}/{t['p180']}/{t['p365']} ₽)" for t in T["tariffs"]))
    changed = [f"{r['key']}={str(r['value'])[:60]!r}" for r in T["settings"] if r["key"] in SET_DEF and str(SET_DEF[r["key"]]) != str(r["value"])]
    L.append("Изменённые настройки: " + (", ".join(changed) or "нет"))
    ct = [r["key"] for r in T["texts"] if TEXTS.get(r["key"]) != r["value"]]
    L.append("Изменённые тексты: " + (", ".join(ct) or "нет"))
    cb = [f"{r['key']}: текст «{r['text']}», эмодзи {r['emoji'] or '-'}, цвет {r['style'] or '-'}" for r in T["buttons"]
          if r["key"] in BTNS and (r["text"], r["emoji"] or "", r["style"] or "") != BTNS[r["key"]]]
    L.append("Изменённые кнопки: " + ("; ".join(cb) or "нет"))
    L.append(f"Премиум-эмодзи ИИ: {len(T['emoji_pool'])} шт.: " + ", ".join(f"{r['plain']}={r['cid']}" for r in T["emoji_pool"]))
    L.append("Баннеры: " + (", ".join(f"{r['model']} ({r['typ']})" for r in T["model_banners"]) or "нет") + (", общий задан" if any(r["key"] == "banner_id" and r["value"] for r in T["settings"]) else ""))
    L.append(f"Токены по моделям: {sum(1 for _ in T['grants'])} записей; платежей: {len(T['payments'])}; тикетов: {len(T['tickets'])}")
    L += ["", "=== JSON ===", json.dumps(data, ensure_ascii=False, separators=(",", ":"))]
    return "\n".join(L)


async def backup_loop(bot):
    await asyncio.sleep(180)
    while True:
        try:
            if OWNER_ID and await setting("autobackup") == "1":
                await send_backup(bot, "авто")
        except Exception:
            logging.exception("backup_loop")
        await asyncio.sleep(max(1, await seti("backup_hours", 6)) * 3600)


# ---------------------- проверка моделей ----------------------
LAST_BAD: dict = {}


async def run_model_check(msg):
    """Пробный запрос к каждой модели; нерабочие можно отключить одной кнопкой."""
    LAST_BAD.clear()
    sem, done = asyncio.Semaphore(5), [0]
    total = len(MODELS)

    async def one(m):
        async with sem:
            try:
                await asyncio.wait_for(stream_once(m, [{"role": "user", "content": "Ответь одним словом: ок"}], 8, None), 45)
            except Exception as e:
                LAST_BAD[m] = str(e).replace("\n", " ")[:90]
            done[0] += 1
            if done[0] % 8 == 0:
                await edit_msg(msg, f"🧪 Проверяю модели: {done[0]} из {total}…", None, plain=True)
    await asyncio.gather(*[one(m) for m in MODELS])
    lines = "\n".join(f"• {m}: {er}" for m, er in LAST_BAD.items())
    txt = (f"🧪 <b>Проверка закончена</b>: работают {total - len(LAST_BAD)} из {total}.\n" + (f"\nНе отвечают:\n{H.escape(lines)[:3000]}" if LAST_BAD else "\nВсе модели отвечают ✅"))
    rows = ([[(f"🗑 Отключить нерабочие ({len(LAST_BAD)})", "adm:moff")]] if LAST_BAD else []) + [[("♻️ Вернуть все модели", "adm:mon")]]
    await edit_msg(msg, txt, kb(rows, back="adm:api"), plain=True)


# ---------------------- авто-отключение нерабочих моделей ----------------------
MODEL_FAILS: dict = {}


async def disable_models(models, why):
    await set_setting("disabled_models", ",".join(sorted(set(OFF) | set(models))))
    load_disabled()
    if BOT and OWNER_ID:
        try:
            await BOT.send_message(OWNER_ID, f"🧹 Отключил нерабочие модели ({why}): {', '.join(models)}.\nВернуть: Админка → API → «Вернуть все модели».")
        except Exception:
            pass


async def note_model_failure(model, errs):
    """Если модель дважды подряд отвечает «такой модели нет», убираем её из меню."""
    if not errs or not all(is_model_error(e) for e in errs):
        return
    MODEL_FAILS[model] = MODEL_FAILS.get(model, 0) + 1
    if MODEL_FAILS[model] >= 2 and model not in OFF:
        await disable_models([model], "ответила ошибкой «модель не найдена»")


async def quiet_model_check():
    sem, bad = asyncio.Semaphore(5), {}

    async def one(m):
        async with sem:
            try:
                await asyncio.wait_for(stream_once(m, [{"role": "user", "content": "Ответь одним словом: ок"}], 8, None), 45)
            except Exception as e:
                errs = getattr(e, "all_errs", [e])
                if errs and all(is_model_error(x) for x in errs):  # только точно «нет такой модели», не сбои сети и лимиты
                    bad[m] = str(errs[0])[:90]
    await asyncio.gather(*[one(m) for m in MODELS if m not in OFF])
    if bad:
        await disable_models(list(bad), "не отвечают")
    return bad


async def model_watch_loop(bot):
    await asyncio.sleep(90)
    while True:
        try:
            await quiet_model_check()
        except Exception:
            logging.exception("model_watch_loop")
        await asyncio.sleep(12 * 3600)


# ---------------------- эмодзи из аватарки пользователя ----------------------
def circle_png(raw):
    """Аватарка -> круглая картинка 100×100 с прозрачным фоном (формат кастомного эмодзи)."""
    from PIL import Image, ImageDraw
    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    w, h = im.size
    s = min(w, h)
    im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s)).resize((100, 100), getattr(Image, "Resampling", Image).LANCZOS)
    mask = Image.new("L", (100, 100), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 99, 99), fill=255)
    out = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


def avatar_tag(u):
    if u and u.get("avatar_opt") == 1 and u.get("avatar_emoji_id"):
        return f'<tg-emoji emoji-id="{u["avatar_emoji_id"]}">🙂</tg-emoji>'
    return ""


async def has_avatar(bot, uid):
    try:
        return (await bot.get_user_profile_photos(uid, limit=1)).total_count > 0
    except Exception:
        return False


async def ensure_avatar(bot, uid):
    """Создаёт (или обновляет при смене аватарки) личный набор кастомных эмодзи из круглой аватарки пользователя."""
    if await setting("avatar_emoji") != "1" or not BOT_USERNAME:
        return False
    u = await get_user(uid)
    if not u or u["avatar_opt"] != 1:
        return False
    try:
        ph = await bot.get_user_profile_photos(uid, limit=1)
        if not ph.total_count:
            return False
        size = ph.photos[0][-1]
        if u["avatar_emoji_id"] and u["avatar_uid"] == size.file_unique_id:
            return True
        png = await asyncio.get_running_loop().run_in_executor(None, circle_png, (await bot.download(size)).read())
        name = f"u{uid}_by_{BOT_USERNAME}"
        if u["avatar_emoji_id"]:  # аватарка сменилась: пересоздаём набор
            try:
                await bot.delete_sticker_set(name)
            except Exception:
                pass
        sticker = InputSticker(sticker=BufferedInputFile(png, "avatar.png"), format="static", emoji_list=["🙂"])
        try:
            await bot.create_new_sticker_set(user_id=uid, name=name, title=f"Аватар {uid}", stickers=[sticker], sticker_type="custom_emoji")
        except Exception as e:
            if "occupied" not in str(e).lower() and "already" not in str(e).lower():
                raise
        cid = (await bot.get_sticker_set(name)).stickers[0].custom_emoji_id
        await q("UPDATE users SET avatar_emoji_id=?, avatar_uid=? WHERE id=?", (cid, size.file_unique_id, uid), commit=True)
        return True
    except Exception as e:
        logging.warning("avatar emoji %s: %s", uid, e)
        return False


# ====================== АДМИН-ПАНЕЛЬ ======================
OWN = F.from_user.id == OWNER_ID
ADM_MAIN = kb([[("📝 Тексты", "adm:texts"), ("🔘 Кнопки", "adm:btns")],
               [("😀 Эмодзи ИИ", "adm:emo"), ("🖼 Баннеры", "adm:banner")],
               [("💎 Тарифы", "adm:tar"), ("🧾 Платежи", "adm:pay")],
               [("💳 Platega", "adm:pl"), ("🆘 Тикеты", "adm:tk")],
               [("👥 Пользователи", "adm:ul:0"), ("🎁 Токены по модели", "adm:gr")],
               [("⚙️ Настройки", "adm:set"), ("🔌 API", "adm:api")],
               [("💾 Данные", "adm:db"), ("📊 Статистика", "adm:stat")],
               [("🤖 Модели", "adm:mdl:_")]], back=None)
PLAT_LABELS = {"platega_id": "MerchantId (из кабинета Platega → Настройки)", "platega_secret": "Секретный ключ API (X-Secret)",
               "platega_url": "Базовый URL API (по умолчанию https://app.platega.io)",
               "platega_methods": "Способы оплаты: ID:название через запятую, например 2:СБП (QR),11:Карта. ID берутся из документации Platega"}
SECRET_KEYS = ("platega_id", "platega_secret", "api_key", "backup_key")
ADM_HEAD = "🛠 <b>Админ-панель</b>\nИИ молчит, пока ты не напишешь /start."
TF_NAMES = {"name": "название", "tokens": "токены в месяц (число)", "max_mult": "макс. коэффициент модели (число, 999 = все)",
            "models": "список моделей через запятую (или - чтобы очистить и работать по коэффициенту)",
            "p30": "цену за 30 дней, ₽ (0 = нет)", "p180": "цену за 6 месяцев, ₽", "p365": "цену за 1 год, ₽"}


def models_admin_screen(g):
    """Экран «Модели» в админке: ✅ работает, 🚫 убрана из меню."""
    if g == "_":
        btns = []
        for x in GROUPS:
            off = sum(1 for y in CHAT_CATALOG if y[0] == x and y[1] in OFF)
            btns.append((f"{x}" + (f" ({off} откл.)" if off else ""), f"adm:mdl:{x}"))
        return ("🤖 <b>Модели</b>\nВыбери компанию, затем включай и выключай модели. Отключённая модель пропадает из меню у пользователей, "
                f"из студии кода и из бизнес-режима. Сейчас отключено: {len(OFF)}.", kb([btns[i:i + 2] for i in range(0, len(btns), 2)], back="adm:main"))
    rows = [[(("🚫 " if mid in OFF else "✅ ") + f"{ttl} ×{mult}", f"adm:mt:{mid}")] for grp, mid, ttl, mult in CHAT_CATALOG if grp == g]
    rows.append([("🚫 Отключить всю компанию", f"adm:mg:{g}:0"), ("✅ Включить всю", f"adm:mg:{g}:1")])
    return f"🤖 <b>{g}</b>\n✅ работает · 🚫 убрана из меню. Нажми на модель, чтобы переключить.", kb(rows, back="adm:mdl:_")


@router.error()
async def on_error(event):
    logging.exception("Ошибка в обработчике: %s", event.exception)
    return True


def parse_amount(s):
    s = s.strip().lower().replace(" ", "").replace("_", "")
    mul = 1
    if s.endswith("k") or s.endswith("к"):
        mul, s = 1000, s[:-1]
    elif s.endswith("m") or s.endswith("м"):
        mul, s = 1000000, s[:-1]
    return int(float(s.replace(",", ".")) * mul)


async def find_user(s):
    s = s.strip().lstrip("@")
    if s.isdigit():
        return await get_user(int(s))
    r = await q("SELECT id FROM users WHERE lower(username)=?", (s.lower(),), one=True)
    return await get_user(r["id"]) if r else None


async def give_tokens(bot, raw):
    parts = raw.split()
    if len(parts) != 3:
        return "Формат: @username 100000 gpt-6-luna"
    u = await find_user(parts[0])
    if not u:
        return "Пользователь не найден (он должен был запустить бота)."
    try:
        n = parse_amount(parts[1])
    except Exception:
        return "Сумма непонятна. Пример: 100000 или 100k или 1m"
    if parts[2] not in MODELS:
        return "Нет такой модели. Пример: gpt-6-luna"
    await q("INSERT INTO grants(uid,model,tokens) VALUES(?,?,?) ON CONFLICT(uid,model) DO UPDATE SET tokens=tokens+excluded.tokens",
            (u["id"], parts[2], n), commit=True)
    try:
        await bot.send_message(u["id"], f"🎁 Тебе выдано <b>{fmt(n)}</b> токенов на модель <b>{TITLE.get(parts[2])}</b>. Выбери её: /models")
    except Exception:
        pass
    return f"✅ {u['name']} (@{u['username'] or '-'}): +{fmt(n)} токенов на {parts[2]}"


def user_card(u):
    cp, t = eff_plan(u), tariff(eff_plan(u))
    until = f" до {fdate(u['plan_until'])}" if cp != "free" else ""
    return (f"👤 <b>{H.escape(u['name'])}</b> @{u['username'] or '-'}\nID: <code>{u['id']}</code>\n"
            f"💎 Тариф: <b>{t['name']}</b>{until}\n🪙 Расход: {fmt(u['tok_used'])} из {fmt(t['tokens'])} · бонус {fmt(u['bonus'])}\n"
            f"📊 Всего: {fmt(u['tok_total'])} (API: {fmt(u['tok_raw'])}) · запросов {u['req_count']}\n"
            f"Регистрация: {fdate(u['joined'])}" + ("\n🚫 <b>ЗАБАНЕН</b>" if u["banned"] else ""))


def user_kb(uid):
    return kb([[("💎 Выдать тариф", f"adm:ug:{uid}"), ("➕ Токены", f"adm:ut:{uid}")],
               [("♻️ Сбросить расход", f"adm:ur:{uid}"), ("⬇️ Перевести на Free", f"adm:ud:{uid}")],
               [("🚫 Бан / разбан", f"adm:ub:{uid}"), ("✉️ Написать", f"adm:um:{uid}")],
               [("🎁 Токены на модель", f"adm:ugm:{uid}")]], back="adm:ul:0")


def tariff_card(t):
    return (f"💎 <b>{t['name']}</b> (<code>{t['code']}</code>)\n🪙 Токенов в месяц: {fmt(t['tokens'])}\n⚖️ Макс. коэффициент: ×{t['max_mult']:g}\n"
            f"🤖 Список моделей: {t['models'] or 'нет (по коэффициенту)'}\n🖼 Картинки: {'да' if t['img_ok'] else 'нет'}\n"
            f"💰 Цены: 30 дн — {t['p30']} ₽ · 6 мес — {t['p180']} ₽ · 1 год — {t['p365']} ₽\n"
            f"Доступно: {models_desc(t)}")


@router.message(Command("admin"), OWN)
async def cmd_admin(m: Message, state: FSMContext):
    await state.clear()
    ADMIN_MODE.add(m.from_user.id)
    await m.answer(ADM_HEAD, reply_markup=ADM_MAIN)


@router.message(Command("give"), OWN)
async def cmd_give(m: Message):
    await m.answer(await give_tokens(m.bot, m.text.partition(" ")[2]))


@router.callback_query(F.data.startswith("adm:"), OWN)
async def adm(c: CallbackQuery, state: FSMContext):
    await state.clear()
    ADMIN_MODE.add(c.from_user.id)
    p = c.data.split(":")
    a, m = p[1], c.message

    async def show(txt, k):
        await edit_msg(m, txt, k, plain=True)

    async def ask(act, prompt, back, **data):
        await state.set_state(A.input)
        await state.update_data(act=act, **data)
        await show(prompt, kb([], back=back))
    if a == "main":
        await show(ADM_HEAD, ADM_MAIN)
    # ---- тексты ----
    elif a == "texts":
        await show("📝 Какой текст менять?", kb([[(lab.split(" (")[0], f"adm:t:{k}")] for k, lab in TEXT_LABELS.items()]))
    elif a == "t":
        k = p[2]
        hint = "\nПодстановки: {greeting} (Добрый вечер), {name}." if k == "welcome2" else ""
        await ask("text", f"📝 <b>{TEXT_LABELS[k]}</b>\nСейчас:\n<code>{H.escape(await text(k))[:3000]}</code>\n\nПришли новый текст (HTML и премиум-эмодзи можно).{hint}", "adm:texts", key=k)
    # ---- кнопки ----
    elif a == "btns":
        await show("🔘 Какую кнопку менять?", kb([[(f"{BC[k]['text']} · {BC[k]['style'] or 'авто'}", f"adm:b:{k}")] for k in BTNS if k in BC]))
    elif a == "b":
        k = p[2]
        await show(f"🔘 Кнопка <b>{BC[k]['text']}</b>", kb([
            [("✏️ Текст", f"adm:bt:{k}"), ("😀 Премиум-эмодзи", f"adm:be:{k}")],
            [("🔵 Синяя", f"adm:bs:{k}:primary", "primary"), ("🟢 Зелёная", f"adm:bs:{k}:success", "success"), ("🔴 Красная", f"adm:bs:{k}:danger", "danger")]],
            back="adm:btns"))
    elif a == "bt":
        await ask("btn_text", "Пришли новый текст кнопки (без эмодзи, эмодзи ставится отдельно):", f"adm:b:{p[2]}", key=p[2])
    elif a == "be":
        await ask("btn_emoji", "Пришли сообщение с премиум-эмодзи (или «-», чтобы убрать):", f"adm:b:{p[2]}", key=p[2])
    elif a == "bs":
        await q("UPDATE buttons SET style=? WHERE key=?", (p[3], p[2]), commit=True)
        await load_buttons()
        await c.answer("✅ Цвет изменён")
    # ---- эмодзи ИИ ----
    elif a == "emo":
        pool = await get_pool()
        only = await setting("premium_only") == "1"
        lst = " ".join(f'<tg-emoji emoji-id="{cid}">{pl}</tg-emoji>' for cid, pl in pool) or "пусто"
        await show(f"😀 Премиум-эмодзи ИИ ({len(pool)}):\n{lst}\n\nРежим «только премиум»: <b>{'вкл' if only else 'выкл'}</b>\n"
                   "Вкл: каждый обычный эмодзи в ответе заменяется премиум-эмодзи из списка.",
                   kb([[("➕ Добавить пачку", "adm:eadd"), ("🗑 Очистить", "adm:eclr")], [("🔁 Режим «только премиум»", "adm:etog")]]))
    elif a == "eadd":
        await state.set_state(A.emo_pack)
        await show("Пришли ОДНИМ сообщением несколько премиум-эмодзи подряд.", kb([], back="adm:emo"))
    elif a == "eclr":
        global POOL
        await q("DELETE FROM emoji_pool", commit=True)
        POOL = None
        await c.answer("🗑 Очищено")
    elif a == "etog":
        v = "0" if await setting("premium_only") == "1" else "1"
        await set_setting("premium_only", v)
        await c.answer("Режим «только премиум»: " + ("вкл" if v == "1" else "выкл"))
    # ---- баннеры ----
    elif a == "banner":
        await show("🖼 <b>Баннеры</b>\n• Общий: на всех экранах бота\n• Выбор компании ИИ: экран, где выбираешь OpenAI, Claude, DeepSeek…\n"
                   "• Все модели: один баннер на списки моделей и ответы всех моделей\n• Отдельной компании (по желанию): заменяет «все модели» для её моделей",
                   kb([[("🌐 Общий баннер", "adm:bk:global")], [("🏢 Выбор компании ИИ", "adm:bk:@pick")],
                       [("🤖 Все модели", "adm:bk:@models")], [("🏷 Отдельной компании", "adm:bmg:_")]]))
    elif a == "bmg":
        btns = [(("🖼 " if g in BANNERS else "") + g, f"adm:bk:{g}") for g in GROUPS]
        await show("🏷 Баннер какой компании? (🖼 — уже задан)", kb([btns[i:i + 2] for i in range(0, len(btns), 2)], back="adm:banner"))
    elif a == "bk":
        key = p[2]
        name = {"global": "Общий баннер", "@pick": "Выбор компании ИИ", "@models": "Все модели"}.get(key, f"Компания {key}")
        typ, fid = (await get_banner(None)) if key == "global" else BANNERS.get(key, (None, None))
        info = {"photo": "фото", "video": "видео", "animation": "гифка"}.get(typ, "не задан")
        rows = [[("➕ Загрузить/сменить", f"adm:bks:{key}"), ("🗑 Убрать", f"adm:bkd:{key}")]]
        if key == "global":
            rows.append([("🔁 На коротких ответах ИИ вкл/выкл", "adm:btog")])
        await show(f"🖼 <b>{name}</b>: {info}", kb(rows, back="adm:banner" if key in ("global", "@pick", "@models") else "adm:bmg:_"))
    elif a == "bks":
        await state.set_state(A.mbanner)
        await state.update_data(model=p[2])
        await show("Пришли фото, видео или гифку для этого баннера.", kb([], back=f"adm:bk:{p[2]}"))
    elif a == "bkd":
        if p[2] == "global":
            await set_setting("banner_id", "")
            await set_setting("banner_type", "")
        else:
            await q("DELETE FROM model_banners WHERE model=?", (p[2],), commit=True)
            await load_banners()
        await c.answer("🗑 Убрано")
    elif a == "btog":
        v = "0" if await setting("banner_answers") == "1" else "1"
        await set_setting("banner_answers", v)
        await c.answer("Баннер на ответах ИИ: " + ("вкл" if v == "1" else "выкл"))
    # ---- тарифы ----
    elif a == "tar":
        rows = [[(f"{t['name']} · {fmt(t['tokens'])}", f"adm:tv:{t['code']}")] for t in TARIFFS.values()] + [[("➕ Новый тариф", "adm:tnew")]]
        await show("💎 <b>Тарифы</b>: выбери для настройки", kb(rows))
    elif a == "tv":
        t = tariff(p[2])
        k = p[2]
        rows = [[("✏️ Название", f"adm:tf:{k}:name"), ("🪙 Токены", f"adm:tf:{k}:tokens")],
                [("⚖️ Макс. коэффициент", f"adm:tf:{k}:max_mult"), ("🤖 Список моделей", f"adm:tf:{k}:models")],
                [("💰 30 дн", f"adm:tf:{k}:p30"), ("💰 6 мес", f"adm:tf:{k}:p180"), ("💰 1 год", f"adm:tf:{k}:p365")],
                [(f"🖼 Картинки: {'вкл' if t['img_ok'] else 'выкл'}", f"adm:ti:{k}")]]
        if k != "free":
            rows.append([("🗑 Удалить тариф", f"adm:td:{k}")])
        await show(tariff_card(t), kb(rows, back="adm:tar"))
    elif a == "tf":
        await ask("tfield", f"✏️ Пришли новое значение: {TF_NAMES[p[3]]}.", f"adm:tv:{p[2]}", code=p[2], field=p[3])
    elif a == "ti":
        t = tariff(p[2])
        await q("UPDATE tariffs SET img_ok=? WHERE code=?", (0 if t["img_ok"] else 1, p[2]), commit=True)
        await load_tariffs()
        await c.answer("Готово")
    elif a == "td":
        await q("UPDATE users SET plan='free', plan_until=0 WHERE plan=?", (p[2],), commit=True)
        await q("DELETE FROM tariffs WHERE code=?", (p[2],), commit=True)
        await load_tariffs()
        await c.answer("🗑 Тариф удалён, пользователи переведены на Free", show_alert=True)
    elif a == "tnew":
        await ask("tnew", "➕ Пришли: <code>код название токены</code>\nНапример: <code>plus Plus 2000000</code>\n(код — латиницей, без пробелов)", "adm:tar")
    # ---- платежи ----
    elif a == "pay":
        rows_db = await q("SELECT * FROM payments ORDER BY (status='pending') DESC, id DESC LIMIT 15")
        ic = {"paid": "✅", "new": "⏳", "pending": "🔎", "rejected": "❌"}
        rows = [[(f"{ic.get(r['status'], '')} №{r['id']} · {tariff(r['plan'])['name']} · {r['rub']} ₽", f"adm:pv:{r['id']}")] for r in rows_db]
        await show("🧾 <b>Платежи</b> (🔎 — чек ждёт проверки)", kb(rows))
    elif a == "pv":
        r = await q("SELECT * FROM payments WHERE id=?", (int(p[2]),), one=True)
        u = await get_user(r["uid"])
        rows = [[("✅ Одобрить", f"pay_ok:{r['id']}"), ("❌ Отклонить", f"pay_no:{r['id']}")]] if r["status"] in ("pending", "new") else []
        await show(receipt_text(dict(r)) + f"\n\nПользователь: {H.escape(u['name'])} @{u['username'] or '-'} (id {u['id']})", kb(rows, back="adm:pay"))
    # ---- пользователи ----
    elif a == "ul":
        page = int(p[2])
        rows_db = await q("SELECT * FROM users ORDER BY joined DESC LIMIT 8 OFFSET ?", (page * 8,))
        total = (await q("SELECT COUNT(*) c FROM users", one=True))["c"]
        rows = [[(f"{r['name'][:16]} @{r['username'] or '-'} · {r['plan']}", f"adm:uv:{r['id']}")] for r in rows_db]
        nav_ = []
        if page > 0:
            nav_.append(("◀️", f"adm:ul:{page - 1}"))
        if (page + 1) * 8 < total:
            nav_.append(("▶️", f"adm:ul:{page + 1}"))
        if nav_:
            rows.append(nav_)
        rows.append([("🔎 Найти по ID или @username", "adm:uf")])
        await show(f"👥 <b>Пользователи</b>: всего {total}", kb(rows))
    elif a == "uf":
        await ask("u_find", "🔎 Пришли ID или @username:", "adm:ul:0")
    elif a == "uv":
        u = await get_user(int(p[2]))
        await show(user_card(u), user_kb(u["id"]))
    elif a == "ug":
        rows = [[(f"💎 {t['name']}", f"adm:ugp:{p[2]}:{t['code']}")] for t in TARIFFS.values() if t["code"] != "free"]
        await show("Какой тариф выдать?", kb(rows, back=f"adm:uv:{p[2]}"))
    elif a == "ugp":
        rows = [[(f"{d} дн.", f"adm:ugd:{p[2]}:{p[3]}:{d}") for d in (7, 30, 180, 365)]]
        await show("На сколько?", kb(rows, back=f"adm:ug:{p[2]}"))
    elif a == "ugd":
        uid, code, days = int(p[2]), p[3], int(p[4])
        until = await grant_plan(uid, code, days)
        await q("INSERT INTO payments(uid,plan,days,rub,method,status,created,paid_at) VALUES(?,?,?,?,?,?,?,?)",
                (uid, code, days, 0, "admin", "paid", int(time.time()), int(time.time())), commit=True)
        try:
            await c.bot.send_message(uid, (await text("paid_ok")).replace("{plan}", tariff(code)["name"]).replace("{date}", fdate(until)))
        except Exception:
            pass
        await show("✅ Тариф выдан.\n\n" + user_card(await get_user(uid)), user_kb(uid))
    elif a == "ut":
        await ask("u_tokens", "➕ Сколько бонусных токенов добавить? (можно отрицательное, например -50000 или 100k)", f"adm:uv:{p[2]}", uid=int(p[2]))
    elif a == "ur":
        await q("UPDATE users SET tok_used=0 WHERE id=?", (int(p[2]),), commit=True)
        await show("♻️ Расход обнулён.\n\n" + user_card(await get_user(int(p[2]))), user_kb(int(p[2])))
    elif a == "ud":
        await q("UPDATE users SET plan='free', plan_until=0 WHERE id=?", (int(p[2]),), commit=True)
        await show("⬇️ Переведён на Free.\n\n" + user_card(await get_user(int(p[2]))), user_kb(int(p[2])))
    elif a == "ub":
        u = await get_user(int(p[2]))
        await q("UPDATE users SET banned=? WHERE id=?", (0 if u["banned"] else 1, u["id"]), commit=True)
        try:
            await c.bot.send_message(u["id"], "✅ Доступ восстановлен." if u["banned"] else await text("banned"))
        except Exception:
            pass
        await show(user_card(await get_user(u["id"])), user_kb(u["id"]))
    elif a == "um":
        await ask("u_msg", "✉️ Пришли текст сообщения для пользователя:", f"adm:uv:{p[2]}", uid=int(p[2]))
    elif a == "ugm":
        await ask("u_grant", "🎁 Пришли: <code>токены модель</code>\nНапример: <code>200k gpt-6-luna</code>", f"adm:uv:{p[2]}", uid=int(p[2]))
    # ---- тикеты ----
    elif a == "tk":
        rows_db = await q("SELECT * FROM tickets WHERE status='open' ORDER BY id DESC LIMIT 15")
        rows = [[(f"№{r['id']} · id {r['uid']}", f"adm:tkv:{r['id']}")] for r in rows_db]
        await show(f"🆘 <b>Открытые тикеты</b>: {len(rows_db)}", kb(rows))
    elif a == "tkv":
        tid = int(p[2])
        msgs = await q("SELECT * FROM ticket_msgs WHERE tid=? ORDER BY id DESC LIMIT 8", (tid,))
        body = "\n".join(f"{'🛠' if r['from_admin'] else '👤'} {H.escape(r['body'])[:500]}" for r in reversed(msgs))
        await show(f"🆘 <b>Тикет №{tid}</b>\n\n{body}", kb([[("✉️ Ответить", f"tk_r:{tid}"), ("✅ Закрыть", f"tk_c:{tid}")]], back="adm:tk"))
    # ---- токены по модели ----
    elif a == "gr":
        rows_db = await q("SELECT g.uid, g.model, g.tokens, u.username FROM grants g LEFT JOIN users u ON u.id=g.uid WHERE g.tokens>0 ORDER BY g.tokens DESC LIMIT 15")
        lst = "\n".join(f"@{r['username'] or r['uid']}: {fmt(r['tokens'])} на {r['model']}" for r in rows_db) or "пока нет"
        await show("🎁 <b>Токены на конкретную модель</b>\nЧеловек сможет пользоваться этой моделью, даже если её нет в его тарифе. "
                   f"Когда токены кончатся, модель закроется.\n\nБалансы:\n{lst}", kb([[("➕ Выдать", "adm:gadd")]]))
    elif a == "gadd":
        await ask("grant_user", "🎁 Пришли: <code>@username токены модель</code>\nНапример: <code>@ivan 100k gpt-6-luna</code>", "adm:gr")
    # ---- настройки ----
    elif a == "set":
        await show("⚙️ <b>Настройки</b>: что менять?", kb([[(lab.split(" (")[0], f"adm:s:{k}")] for k, lab in SET_LABELS.items()]))
    elif a == "s":
        k = p[2]
        await ask("setting", f"⚙️ <b>{SET_LABELS[k]}</b>\nСейчас: <code>{H.escape((await setting(k))[:900])}</code>\n\nПришли новое значение:", "adm:set", key=k)
    # ---- API ----
    elif a == "api":
        base, key = (await setting("api_base")) or BASE_URL, (await setting("api_key")) or API_KEY
        mask = key[:4] + "…" + key[-3:] if len(key) > 8 else "не задан"
        bk, bb = (await setting("backup_key")) or BACKUP_API_KEY, (await setting("backup_url")) or BACKUP_BASE_URL
        now_bk = " (сейчас работает он)" if time.time() < API_STATE["down_until"] else ""
        await show(f"🔌 <b>API</b>\nОсновной: <code>{H.escape(base)}</code>, ключ <code>{H.escape(mask)}</code> "
                   f"({'из админки' if await setting('api_key') else 'из переменных хостинга'})\n"
                   f"Запасной: {('✅ <code>' + H.escape(bb) + '</code>' + now_bk) if bk else 'не задан'}\n"
                   f"Отключено моделей: {len(OFF)}\n\nЕсли на основном ключе кончатся токены, бот сам переключится на запасной.",
                   kb([[("✏️ Адрес", "adm:apib"), ("🔑 Ключ", "adm:apik")], [("🛟 Запасной: адрес", "adm:bpb"), ("🛟 Запасной: ключ", "adm:bpk")],
                       [("🔄 Проверить связь", "adm:apit"), ("🧪 Проверить модели", "adm:mchk")],
                       [("♻️ Вернуть все модели", "adm:mon"), ("🗑 Сбросить на переменные", "adm:apir")]]))
    elif a == "apib":
        await ask("api_base", "Пришли адрес API, например https://api.basisweb.ru/v1", "adm:api")
    elif a == "apik":
        await ask("api_key", "Пришли новый ключ API. Сообщение с ключом я удалю из чата.", "adm:api")
    elif a == "bpb":
        await ask("setting", "🛟 Адрес запасного API, например https://darkapi.shop/v1", "adm:api", key="backup_url")
    elif a == "bpk":
        await ask("setting", "🛟 Ключ запасного API. Сообщение с ним я удалю из чата.", "adm:api", key="backup_key")
    elif a == "apit":
        res = []
        for name, cl in (("основной", ai), ("запасной", ai_backup)):
            if cl is None:
                res.append(f"{name}: не задан")
                continue
            try:
                res.append(f"{name}: ✅ моделей {len((await cl.models.list()).data)}")
            except Exception as e:
                res.append(f"{name}: ⚠️ {str(e)[:70]}")
        await c.answer("\n".join(res), show_alert=True)
    elif a == "apir":
        for k_ in ("api_base", "api_key"):
            await set_setting(k_, "")
        await reload_client()
        await c.answer("Сброшено на переменные хостинга", show_alert=True)
    elif a == "mchk":
        await show("🧪 Проверяю модели, это займёт около минуты…", kb([], back="adm:api"))
        spawn(run_model_check(m))
    elif a == "moff":
        await set_setting("disabled_models", ",".join(sorted(set(OFF) | set(LAST_BAD))))
        load_disabled()
        await c.answer(f"Отключено моделей: {len(OFF)}", show_alert=True)
    elif a == "mon":
        await set_setting("disabled_models", "")
        load_disabled()
        await c.answer("Все модели возвращены", show_alert=True)
    # ---- данные: резервные копии ----
    elif a == "db":
        n = (await q("SELECT COUNT(*) c FROM users", one=True))["c"]
        auto = await setting("autobackup") == "1"
        await show(f"💾 <b>Данные</b>\nПользователей в базе: {n}\nАвтобэкап: <b>{'вкл' if auto else 'выкл'}</b> (раз в {await seti('backup_hours', 6)} ч). "
                   "Копия приходит сюда и закрепляется. Если база пропадёт после замены файлов, бот сам поднимет данные из закреплённой копии.\n\n"
                   "«Промт для Claude»: файл, который можно отправить мне, и я встрою данные в код.",
                   kb([[("📤 Выгрузить копию", "adm:dbx"), ("📝 Промт для Claude", "adm:dbp")],
                       [("📥 Восстановить из файла", "adm:dbr"), (f"💾 Автобэкап: {'вкл' if auto else 'выкл'}", "adm:dbt")]]))
    elif a == "dbx":
        await send_backup(c.bot, "вручную", force=True)
        await c.answer("Копия отправлена в этот чат")
    elif a == "dbp":
        await c.message.answer_document(BufferedInputFile((await build_prompt()).encode("utf-8"), "prompt_for_claude.txt"),
                                        caption="📝 Отправь этот файл мне (Claude): я встрою все данные в код.")
    elif a == "dbr":
        await state.set_state(A.restore)
        await show("📥 Пришли файл резервной копии (.json). Данные в базе будут заменены данными из файла (ключи API останутся).", kb([], back="adm:db"))
    elif a == "dbt":
        v = "0" if await setting("autobackup") == "1" else "1"
        await set_setting("autobackup", v)
        await c.answer("Автобэкап: " + ("вкл" if v == "1" else "выкл"))
    # ---- модели: включить / отключить вручную ----
    elif a == "mdl":
        txt, k = models_admin_screen(p[2])
        await show(txt, k)
    elif a == "mt":
        cur = set(OFF)
        cur.symmetric_difference_update({p[2]})
        await set_setting("disabled_models", ",".join(sorted(cur)))
        load_disabled()
        txt, k = models_admin_screen(GROUP_OF.get(p[2], "_"))
        await show(txt, k)
    elif a == "mg":
        ids = {mid for grp, mid, _, _ in CHAT_CATALOG if grp == p[2]}
        cur = (set(OFF) | ids) if p[3] == "0" else (set(OFF) - ids)
        await set_setting("disabled_models", ",".join(sorted(cur)))
        load_disabled()
        txt, k = models_admin_screen(p[2])
        await show(txt, k)
    # ---- Platega ----
    elif a == "pl":
        base, mid, sec = await plat_cfg()
        pub = await public_url()
        meth = ", ".join(f"{i}: {n}" for i, n in await plat_methods()) or "не заданы"
        await show("💳 <b>Platega</b>\n"
                   f"MerchantId: <code>{H.escape(mid[:4] + '…' + mid[-3:]) if len(mid) > 8 else 'не задан'}</code>\n"
                   f"Секрет: <code>{'••••' + H.escape(sec[-3:]) if len(sec) > 6 else 'не задан'}</code>\n"
                   f"Адрес API: <code>{H.escape(base)}</code>\nСпособы оплаты: {H.escape(meth)}\n\n"
                   f"Callback URL для кабинета Platega:\n<code>{H.escape(pub + '/platega/callback') if pub else 'сначала задай публичный адрес бота (Настройки → Публичный адрес)'}</code>\n"
                   f"Статус: {'✅ подключено' if mid and sec else '⚠️ не настроено'}",
                   kb([[("✏️ MerchantId", "adm:pls:platega_id"), ("🔑 Секрет", "adm:pls:platega_secret")],
                       [("🌐 Адрес API", "adm:pls:platega_url"), ("🔢 Способы оплаты", "adm:pls:platega_methods")]]))
    elif a == "pls":
        k = p[2]
        cur = "" if k in SECRET_KEYS else f"\nСейчас: <code>{H.escape(await setting(k))}</code>"
        await ask("setting", f"💳 <b>{PLAT_LABELS[k]}</b>{cur}\n\nПришли новое значение" + (". Сообщение с ним я удалю из чата." if k in SECRET_KEYS else ":"), "adm:pl", key=k)
    # ---- статистика ----
    elif a == "stat":
        now = int(time.time())
        one = lambda sql, *a_: q(sql, a_, one=True)
        tot = (await one("SELECT COUNT(*) c FROM users"))["c"]
        paid = (await one("SELECT COUNT(*) c FROM users WHERE plan!='free' AND plan_until>?", now))["c"]
        banned = (await one("SELECT COUNT(*) c FROM users WHERE banned=1"))["c"]
        s = await one("SELECT COALESCE(SUM(tok_total),0) a, COALESCE(SUM(tok_raw),0) b, COALESCE(SUM(req_count),0) r FROM users")
        rev = (await one("SELECT COALESCE(SUM(rub),0) s FROM payments WHERE status='paid'"))["s"]
        tk = (await one("SELECT COUNT(*) c FROM tickets WHERE status='open'"))["c"]
        pend = (await one("SELECT COUNT(*) c FROM payments WHERE status='pending'"))["c"]
        await show(f"📊 <b>Статистика</b>\n👥 Пользователей: {tot} (платных сейчас: {paid}, в бане: {banned})\n"
                   f"🪙 Списано токенов тарифа: {fmt(s['a'])}\n🔌 Токенов по данным API: {fmt(s['b'])}\n💬 Запросов: {fmt(s['r'])}\n"
                   f"💰 Выручка: {fmt(rev)} ₽\n🆘 Открытых тикетов: {tk}\n🔎 Чеков на проверке: {pend}", kb([]))
    try:
        await c.answer()
    except Exception:
        pass  # на callback уже ответили выше


@router.callback_query(F.data.startswith("pay_"), OWN)
async def pay_decide(c: CallbackQuery):
    act, pid = c.data.split(":")
    pid = int(pid)
    p = await q("SELECT * FROM payments WHERE id=?", (pid,), one=True)
    if not p:
        return await c.answer("Платёж не найден")
    if act == "pay_ok":
        await finish_payment(c.bot, pid)
        note = f"✅ Платёж №{pid} одобрен, тариф выдан."
    else:
        await q("UPDATE payments SET status='rejected' WHERE id=?", (pid,), commit=True)
        try:
            await c.bot.send_message(p["uid"], f"❌ Чек №{pid} не подтверждён. Напиши в поддержку: /support")
        except Exception:
            pass
        note = f"❌ Платёж №{pid} отклонён."
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await c.message.answer(note)
    await c.answer()


@router.callback_query(F.data.startswith("tk_r:"), OWN)
async def tk_reply_start(c: CallbackQuery, state: FSMContext):
    ADMIN_MODE.add(c.from_user.id)
    await state.set_state(A.input)
    await state.update_data(act="tk_reply", tid=int(c.data[5:]))
    await c.message.answer(f"✍️ Напиши ответ на тикет №{c.data[5:]}:")
    await c.answer()


@router.callback_query(F.data.startswith("tk_c:"), OWN)
async def tk_close(c: CallbackQuery):
    tid = int(c.data[5:])
    t = await q("SELECT * FROM tickets WHERE id=?", (tid,), one=True)
    if t:
        await q("UPDATE tickets SET status='closed' WHERE id=?", (tid,), commit=True)
        try:
            await c.bot.send_message(t["uid"], f"✅ Тикет №{tid} закрыт. Если вопрос остался, создай новый: /support")
        except Exception:
            pass
    await c.message.answer(f"✅ Тикет №{tid} закрыт.")
    await c.answer()


# ---- ввод значений в админке ----
@router.message(A.input, OWN)
async def adm_input(m: Message, state: FSMContext):
    d = await state.get_data()
    act, txt = d.get("act"), (m.text or "").strip()
    done = kb([], back="adm:main")
    try:
        if act == "text":
            await q("UPDATE texts SET value=? WHERE key=?", (m.html_text, d["key"]), commit=True)
            TC.clear()
        elif act == "btn_text":
            await q("UPDATE buttons SET text=? WHERE key=?", (txt, d["key"]), commit=True)
            await load_buttons()
        elif act == "btn_emoji":
            cid = ""
            if txt != "-":
                ents = [e for e in (m.entities or []) if e.type == "custom_emoji"]
                cid = ents[0].custom_emoji_id if ents else (txt if txt.isdigit() else "")
                if not cid:
                    return await m.answer("Не вижу премиум-эмодзи, пришли ещё раз.")
            await q("UPDATE buttons SET emoji=? WHERE key=?", (cid, d["key"]), commit=True)
            await load_buttons()
        elif act == "tfield":
            f = d["field"]
            val = txt
            if f in ("tokens", "p30", "p180", "p365"):
                val = parse_amount(txt)
            elif f == "max_mult":
                val = float(txt.replace(",", "."))
            elif f == "models":
                val = "" if txt == "-" else ",".join(x.strip() for x in txt.split(",") if x.strip())
            await q(f"UPDATE tariffs SET {f}=? WHERE code=?", (val, d["code"]), commit=True)
            await load_tariffs()
        elif act == "tnew":
            code, name, tokens = txt.split()[:3]
            code = re.sub(r"[^a-z0-9_]", "", code.lower())[:12]
            if not code or code in TARIFFS:
                return await m.answer("Такой код уже есть или он пустой. Пришли другой.")
            await q("INSERT INTO tariffs VALUES(?,?,?,?,?,?,?,?,?,?)", (code, name, parse_amount(tokens), 2.99, 0, "", 0, 0, 0, len(TARIFFS)), commit=True)
            await load_tariffs()
        elif act == "u_find":
            u = await find_user(txt)
            if not u:
                return await m.answer("Не нашёл. Пришли ID или @username ещё раз.")
            await state.clear()
            return await m.answer(user_card(u), reply_markup=user_kb(u["id"]))
        elif act == "u_tokens":
            await q("UPDATE users SET bonus=MAX(0,bonus+?) WHERE id=?", (parse_amount(txt), d["uid"]), commit=True)
            await state.clear()
            return await m.answer("✅ Готово.\n\n" + user_card(await get_user(d["uid"])), reply_markup=user_kb(d["uid"]))
        elif act == "u_msg":
            await m.bot.send_message(d["uid"], "📩 <b>Сообщение от поддержки Gimbek AI</b>\n\n" + m.html_text)
        elif act == "u_grant":
            res = await give_tokens(m.bot, f"{d['uid']} {txt}")
            await state.clear()
            return await m.answer(res, reply_markup=user_kb(d["uid"]))
        elif act == "grant_user":
            res = await give_tokens(m.bot, txt)
            await state.clear()
            return await m.answer(res, reply_markup=kb([[("➕ Ещё", "adm:gadd")]], back="adm:gr"))
        elif act == "tk_reply":
            tid = d["tid"]
            t = await q("SELECT * FROM tickets WHERE id=?", (tid,), one=True)
            await q("INSERT INTO ticket_msgs(tid,from_admin,body,ts) VALUES(?,1,?,?)", (tid, txt, int(time.time())), commit=True)
            await m.bot.send_message(t["uid"], f"🆘 <b>Ответ поддержки (тикет №{tid})</b>\n\n{m.html_text}",
                                     reply_markup=kb([[("↩️ Ответить", f"tk_u:{tid}")]], back=None))
        elif act == "setting":
            val = txt.rstrip("/") if d["key"] in ("public_url", "platega_url") else txt
            await set_setting(d["key"], val)
            if d["key"] in SECRET_KEYS:
                await safe_delete(m)
            if d["key"] in ("backup_url", "backup_key"):
                await reload_client()
            if d["key"] in ("code_max_mult", "disabled_models"):
                load_disabled()
        elif act == "api_base":
            await set_setting("api_base", txt.rstrip("/"))
            await reload_client()
        elif act == "api_key":
            await set_setting("api_key", txt)
            await reload_client()
            await safe_delete(m)
    except Exception as e:
        return await m.answer(f"⚠️ Не получилось: <code>{H.escape(str(e))[:200]}</code>\nПопробуй ещё раз или нажми «Назад».")
    await state.clear()
    await m.answer("✅ Сохранено.", reply_markup=done)


@router.message(A.emo_pack, OWN)
async def st_pack(m: Message, state: FSMContext):
    global POOL
    ents = [e for e in (m.entities or []) if e.type == "custom_emoji"]
    if not ents:
        return await m.answer("Не вижу премиум-эмодзи. Пришли их одним сообщением.")
    for e in ents:
        await q("INSERT OR IGNORE INTO emoji_pool(cid, plain) VALUES(?,?)", (e.custom_emoji_id, e.extract_from(m.text).replace("\uFE0F", "")), commit=True)
    POOL = None
    await state.clear()
    await m.answer(f"✅ Добавлено: {len(ents)}", reply_markup=kb([], back="adm:emo"))


@router.message(A.restore, OWN, F.document)
async def st_restore(m: Message, state: FSMContext):
    try:
        data = json.loads((await m.bot.download(m.document)).read().decode("utf-8"))
        n = await restore_db(data)
    except Exception as e:
        return await m.answer(f"⚠️ Не получилось: <code>{H.escape(str(e))[:200]}</code>")
    await state.clear()
    await m.answer(f"✅ Данные восстановлены. Пользователей: {n}.", reply_markup=kb([], back="adm:db"))


@router.message(A.restore, OWN)
async def st_restore_bad(m: Message):
    await m.answer("Пришли именно файл .json с резервной копией.")


@router.message(Command("backup"), OWN)
async def cmd_backup(m: Message):
    await send_backup(m.bot, "вручную", force=True)


def media_of(m):
    if m.animation:
        return "animation", m.animation.file_id
    if m.video:
        return "video", m.video.file_id
    if m.photo:
        return "photo", m.photo[-1].file_id
    return None, None


@router.message(A.mbanner, OWN, F.photo | F.video | F.animation)
async def st_mbanner(m: Message, state: FSMContext):
    key = (await state.get_data())["model"]
    t, f = media_of(m)
    if key == "global":
        await set_setting("banner_type", t)
        await set_setting("banner_id", f)
    else:
        await q("INSERT OR REPLACE INTO model_banners VALUES(?,?,?)", (key, t, f), commit=True)
        await load_banners()
    await state.clear()
    await m.answer("✅ Баннер сохранён.", reply_markup=kb([], back="adm:banner"))


@router.message(A.banner, OWN)
@router.message(A.mbanner, OWN)
async def st_banner_bad(m: Message):
    await m.answer("Нужно именно фото, видео или гифка. Пришли ещё раз.")


# ====================== ФОНОВЫЕ ЗАДАЧИ И ЗАПУСК ======================
async def check_subscriptions(bot):
    now = int(time.time())
    for r in await q("SELECT id, plan, plan_until, notified FROM users WHERE plan!='free' AND plan_until>0"):
        left, name = r["plan_until"] - now, tariff(r["plan"])["name"]
        buy = kb([[("💎 Продлить", "nav:plans")]], back=None)
        try:
            if left <= 0:
                await q("UPDATE users SET plan='free', plan_until=0, tok_used=0, period_start=?, notified=0 WHERE id=?", (now, r["id"]), commit=True)
                await bot.send_message(r["id"], (await text("expire_now")).replace("{plan}", name), reply_markup=buy)
            elif left <= 86400 and r["notified"] < 2:
                await q("UPDATE users SET notified=2 WHERE id=?", (r["id"],), commit=True)
                await bot.send_message(r["id"], (await text("expire_soon")).replace("{plan}", name).replace("{date}", fdate(r["plan_until"])).replace("{left}", "меньше суток"), reply_markup=buy)
            elif left <= 3 * 86400 and r["notified"] < 1:
                await q("UPDATE users SET notified=1 WHERE id=?", (r["id"],), commit=True)
                await bot.send_message(r["id"], (await text("expire_soon")).replace("{plan}", name).replace("{date}", fdate(r["plan_until"])).replace("{left}", f"{left // 86400 + 1} дн."), reply_markup=buy)
        except Exception as e:
            logging.warning("reminder %s: %s", r["id"], e)


async def reminders_loop(bot):
    while True:
        try:
            await check_subscriptions(bot)
        except Exception:
            logging.exception("reminders")
        await asyncio.sleep(1800)


async def crypto_loop(bot):
    while True:
        await asyncio.sleep(30)
        if not CRYPTO_TOKEN:
            continue
        try:
            for p in await q("SELECT * FROM payments WHERE method='usdt' AND status='new' AND ext!='' AND created>?", (int(time.time()) - 6 * 3600,)):
                r = await crypto("getInvoices", invoice_ids=p["ext"])
                items = (r or {}).get("items", [])
                if items and items[0]["status"] == "paid":
                    await finish_payment(bot, p["id"])
        except Exception:
            logging.exception("crypto_loop")


async def set_commands(bot):
    cmds = [("start", "Главное меню"), ("profile", "Профиль и токены"), ("plans", "Тарифы и оплата"), ("models", "Выбор модели"),
            ("search", "Поиск в интернете"), ("games", "Мини-игры с ИИ"), ("img", "Нарисовать картинку"), ("code", "Написать код и файлы"),
            ("tg", "Статья в Telegraph"), ("studio", "Студия кода"), ("ai", "Команды .ai и .lc"), ("terms", "Соглашение и политика"), ("settings", "Мои настройки"), ("support", "Тех. поддержка"), ("receipts", "Мои чеки"),
            ("clear", "Очистить память"), ("help", "Помощь")]
    base = [BotCommand(command=c, description=d) for c, d in cmds]
    try:
        await bot.set_my_commands(base, scope=BotCommandScopeDefault())
        if OWNER_ID:
            await bot.set_my_commands(base + [BotCommand(command="admin", description="Админ-панель")], scope=BotCommandScopeChat(chat_id=OWNER_ID))
    except Exception as e:
        logging.warning("set_commands: %s", e)


async def main():
    global BOT_USERNAME, BOT
    if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", BOT_TOKEN):
        raise SystemExit("BOT_TOKEN неверный: нужен токен от @BotFather вида 1234567890:AAH... "
                         f"(сейчас начинается с {BOT_TOKEN[:6]!r}, длина {len(BOT_TOKEN)}). Проверь переменную BOT_TOKEN.")
    if not OWNER_ID:
        logging.warning("OWNER_ID не задан: в админку никто не попадёт")
    await init_db()
    await load_tariffs()
    await load_buttons()
    await load_banners()
    await reload_client()
    for r in await q("SELECT * FROM settings"):  # настройки в кэш, чтобы не ходить в базу на каждый ответ
        SC[r["key"]] = r["value"]
    load_disabled()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    BOT = bot
    try:
        await auto_restore(bot)  # база пустая? поднимаем данные из закреплённой копии
    except Exception:
        logging.exception("auto_restore")
    try:
        BOT_USERNAME = (await bot.get_me()).username or ""
    except Exception as e:
        logging.warning("get_me: %s", e)
    dp = Dispatcher(storage=MemoryStorage())
    for obs in (dp.message, dp.callback_query, dp.pre_checkout_query):
        obs.outer_middleware(Track())
    dp.include_router(router)
    await set_commands(bot)
    spawn(reminders_loop(bot))
    spawn(crypto_loop(bot))
    spawn(platega_loop(bot))
    spawn(backup_loop(bot))
    spawn(model_watch_loop(bot))
    try:
        await start_web(bot)
    except Exception:
        logging.exception("Веб-сервер не запустился (студия кода и Platega-callback недоступны)")
    logging.info("Gimbek AI запущен. Тарифы: %s", ", ".join(TARIFFS))
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
