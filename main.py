# -*- coding: utf-8 -*-
"""Gimbek AI: Telegram-бот на aiogram 3.25+ (тарифы, оплата, тикеты, поиск, бизнес-режим)."""
import asyncio, base64, hmac, html as H, io, json, logging, math, os, random, re, shutil, tempfile, time, zipfile
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, quote, unquote, urlparse

import aiohttp, aiosqlite
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BotCommand, BotCommandScopeChat, BotCommandScopeDefault, BufferedInputFile,
                           BusinessConnection, CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto,
                           LabeledPrice, Message, PreCheckoutQuery, WebAppInfo)
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
DB = _env("DB_PATH", "bot.db")
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
    ("Google", "gemini-3.1-pro", "Gemini 3.1 Pro", "2"), ("Google", "gemini-3.6-flash", "Gemini 3.6 Flash", "2"),
    ("Google", "gemini-3.7-flash", "Gemini 3.7 Flash", "2"), ("Google", "gemini-3.8-flash", "Gemini 3.8 Flash", "2"),
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
               ("gpt-image-2.5-sunburst", "GPT Image 2.5 Sunburst", 50000), ("nano-banana-2", "Nano Banana 2", 100000),
               ("grok-imagine-image", "Grok Imagine", 100000)]
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
    "welcome2": "{greeting} ❤️\n\n<b>{name}</b>, добро пожаловать в <b>Gimbek AI</b>!\n"
                "Пиши, говори голосом, присылай фото: я отвечу. Умею картинки, код, статьи в Telegraph и поиск в интернете.",
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
    "help": "ℹ️ <b>Что я умею</b>\n• Общаться и отвечать на вопросы (пиши как обычно)\n• Понимать голосовые, кружки, видео и фото\n"
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
    "expire_now": "Подписка закончилась ({plan})", "help": "Текст помощи",
}
# ключ: (текст, premium-эмодзи, цвет)
BTNS = {"models": ("Модели", "", "primary"), "img": ("Картинки", "", "success"), "code": ("Файлы и код", "", "primary"),
        "tg": ("Telegraph", "", "success"), "search": ("Поиск в сети", "", "primary"), "games": ("Мини-игры", "", "success"),
        "profile": ("Профиль", "", "primary"), "plans": ("Тарифы", "", "success"), "support": ("Поддержка", "", "primary"),
        "mysettings": ("Мои настройки", "", "primary"), "clear": ("Очистить память", "", "danger")}

SET_DEF = {
    "sys3": DEF_SYS, "max_out": "1500", "hist_len": "6", "img_quality": "low", "premium_only": "1",
    "banner_answers": "1", "img_model": "gpt-image-2", "stt_model": "gpt-4o-transcribe", "tts_model": "tts-1",
    "tts_voice": "alloy", "code_rounds": "3", "code_tokens": "4000", "show_tokens": "1", "require_key": "0", "tz": "3",
    "stt_per_sec": "100", "stars_per_rub": "0.85", "rub_per_usdt": "95", "default_city": "Москва",
    "biz_model_free": "deepseek-v4-flash", "biz_model_basic": "gemini-3.6-flash", "biz_model_pro": "gemini-3.6-flash", "biz_delete_cmd": "1",
    "webapp_chess": "https://fertenkbs.github.io/mysite/",
    "terms_url": "https://telegra.ph/Polzovatelskoe-soglashenie-10-20-28",
    "privacy_url": "https://telegra.ph/Politika-konfidencialnosti-10-20-69",
    "api_base": "", "api_key": "", "banner_id": "", "banner_type": "", "tg_token": "", "styles_v3": "0",
}
SET_LABELS = {
    "sys3": "Системная инструкция ИИ", "max_out": "Макс. длина ответа (токены)", "hist_len": "Память: сколько сообщений помнить",
    "img_quality": "Качество картинок (low/medium/high/auto)", "premium_only": "Только премиум-эмодзи (1 или 0)",
    "banner_answers": "Баннер на коротких ответах (1 или 0)", "img_model": "Модель картинок по умолчанию",
    "stt_model": "Модель распознавания голоса", "tts_model": "Модель озвучки", "tts_voice": "Голос озвучки",
    "code_rounds": "Код: максимум частей ответа (меньше = экономнее)", "code_tokens": "Код: токенов на одну часть (4000 ≈ 300 строк)", "show_tokens": "Показывать расход токенов (1 или 0)",
    "require_key": "Требовать секретный ключ для входа (1 или 0)", "tz": "Часовой пояс (UTC+ часов)",
    "stt_per_sec": "Токенов за секунду голоса", "stars_per_rub": "Звёзд Telegram за 1 ₽",
    "rub_per_usdt": "Курс: ₽ за 1 USDT", "default_city": "Город по умолчанию (погода)",
    "biz_model_free": "Бизнес-режим: модель для Free", "biz_model_basic": "Бизнес-режим: модель для Basic",
    "biz_model_pro": "Бизнес-режим: модель для Pro",
    "biz_delete_cmd": "Бизнес-режим: удалять команду .ai после ответа (1 или 0)", "webapp_chess": "Ссылка на мини-апп «Шахматы»",
    "terms_url": "Ссылка: пользовательское соглашение", "privacy_url": "Ссылка: политика конфиденциальности",
}
# код, название, токенов/мес, макс. коэффициент, картинки, список моделей, цены 30д/6м/1г (₽), порядок
TARIFF_SEED = [
    ("free", "Free", 100000, 0.5, 0, "gpt-6-luna,deepseek-v4-flash,deepseek-v4.1-flash", 0, 0, 0, 0),
    ("basic", "Basic", 1000000, 2.99, 0, "", 50, 250, 550, 1),
    ("pro", "Pro", 5000000, 999, 1, "", 100, 550, 1150, 2),
]
USER_COLS = ["voice INT DEFAULT 0", "plan TEXT DEFAULT 'free'", "plan_until INT DEFAULT 0", "period_start INT DEFAULT 0",
             "tok_used INT DEFAULT 0", "bonus INT DEFAULT 0", "tok_total INT DEFAULT 0", "tok_raw INT DEFAULT 0",
             "req_count INT DEFAULT 0", "img_model TEXT DEFAULT ''", "code_model TEXT DEFAULT ''",
             "length TEXT DEFAULT 'mid'", "memory INT DEFAULT 1", "custom_prompt TEXT DEFAULT ''",
             "banned INT DEFAULT 0", "agreed INT DEFAULT 0", "activated INT DEFAULT 0", "search INT DEFAULT 0",
             "mode TEXT DEFAULT ''", "notified INT DEFAULT 0", "quiz INT DEFAULT 0"]


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
        "CREATE TABLE IF NOT EXISTS biz(conn TEXT PRIMARY KEY, uid INT)"]:
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


async def reload_client():
    global ai
    key = (await setting("api_key")) or API_KEY
    base = (await setting("api_base")) or BASE_URL
    ai = AsyncOpenAI(api_key=key, base_url=base, timeout=120, max_retries=1)


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
    pick = next((c for c in CHEAP if plan_allows(t, c)), None) or next((m for m in MODELS if plan_allows(t, m)), None)
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


def cur_model(u):
    return u["model"] if u["model"] in MODELS else DEFAULT_CHAT


def cur_code_model(u):
    return u["code_model"] if u["code_model"] in MODELS else DEFAULT_CODE


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
async def get_banner(model=None):
    if model and model in BANNERS:
        return BANNERS[model]
    fid = await setting("banner_id")
    return (await setting("banner_type"), fid) if fid else (None, None)


async def reply(m, txt, reply_markup=None, model=None):
    """Сообщение с баннером (если задан и подпись влезает в 1024)."""
    if model is None:
        u = await get_user(m.chat.id)
        model = cur_model(u) if u else None
    typ, fid = await get_banner(model)
    if fid and len(re.sub(r"<[^>]+>", "", txt)) <= 1000:
        try:
            if typ == "photo":
                return await m.answer_photo(fid, caption=txt, reply_markup=reply_markup)
            if typ == "video":
                return await m.answer_video(fid, caption=txt, reply_markup=reply_markup)
            return await m.answer_animation(fid, caption=txt, reply_markup=reply_markup)
        except Exception as e:
            logging.warning("banner: %s", e)
    return await m.answer(txt, reply_markup=reply_markup)


async def edit_msg(msg, txt, reply_markup=None):
    try:
        if msg.photo or msg.video or msg.animation:
            return await msg.edit_caption(caption=txt, reply_markup=reply_markup)
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


async def stream_once(model, msgs, max_tokens, cb):
    variants = [{"max_tokens": max_tokens, "stream_options": {"include_usage": True}},
                {"max_tokens": max_tokens}, {"max_completion_tokens": max_tokens}]
    stream = None
    for i in range(VARIANT.get(model, 0), len(variants)):
        try:
            stream = await ai.chat.completions.create(model=model, messages=msgs, stream=True, **variants[i])
            VARIANT[model] = i
            break
        except Exception as e:
            low = str(e).lower()
            if i == len(variants) - 1 or not any(w in low for w in
                    ("max_tokens", "max_completion", "stream_options", "unsupported", "unrecognized", "unknown", "invalid")):
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


async def one_image(prompt, model):
    out = []
    try:
        kw = {}
        ql = await setting("img_quality")
        if model.startswith("gpt-image") and ql and ql != "auto" and model not in NO_QUALITY:
            kw["quality"] = ql
        try:
            r = await ai.images.generate(model=model, prompt=prompt, n=1, **kw)
        except Exception:
            if not kw:
                raise
            NO_QUALITY.add(model)
            r = await ai.images.generate(model=model, prompt=prompt, n=1)
        for d in r.data:
            if getattr(d, "b64_json", None):
                out.append(BufferedInputFile(base64.b64decode(d.b64_json), "img.png"))
            elif getattr(d, "url", None):
                out.append(d.url)
    except Exception as e:
        logging.warning("images.generate %s: %s", model, e)
        try:  # запасной путь: часть моделей отдаёт картинку через чат
            r = await ai.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}])
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


async def gen_images(prompt, n=1, model=None):
    model = model or await setting("img_model")
    res = await asyncio.gather(*[one_image(prompt, model) for _ in range(n)], return_exceptions=True)
    out = [i for r in res if isinstance(r, list) for i in r]
    if not out:
        for r in res:
            if isinstance(r, Exception):
                raise r
    return out


# ---------------------- голос и видео -> текст ----------------------
def _av_to_wav(data):
    """Перекодирование через PyAV (в пакете уже есть ffmpeg, ставить ничего в систему не нужно)."""
    import av
    inp = av.open(io.BytesIO(data))
    buf = io.BytesIO()
    out = av.open(buf, "w", format="wav")
    stream = out.add_stream("pcm_s16le", rate=16000)
    stream.layout = "mono"
    rs = av.AudioResampler(format="s16", layout="mono", rate=16000)

    def push(frames):
        for fr in (frames if isinstance(frames, list) else [frames]):
            if fr is not None:
                for pkt in stream.encode(fr):
                    out.mux(pkt)
    for frame in inp.decode(audio=0):
        push(rs.resample(frame))
    push(rs.resample(None))
    for pkt in stream.encode(None):
        out.mux(pkt)
    out.close()
    inp.close()
    return buf.getvalue()


async def to_wav(data, name):
    """API принимает WAV до 25 МБ, а Telegram присылает OGG/MP4, поэтому перекодируем в WAV 16 кГц моно."""
    if shutil.which("ffmpeg"):
        with tempfile.TemporaryDirectory() as td:
            src, dst = os.path.join(td, "in" + (os.path.splitext(name)[1] or ".bin")), os.path.join(td, "out.wav")
            with open(src, "wb") as f:
                f.write(data)
            p = await asyncio.create_subprocess_exec("ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000", dst,
                                                     stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await p.communicate()
            if p.returncode == 0 and os.path.exists(dst):
                with open(dst, "rb") as f:
                    return f.read()  # файл (а не канал), поэтому длина в заголовке WAV верная
            logging.warning("ffmpeg: %s", err.decode(errors="ignore")[:300])
    try:
        return await asyncio.get_running_loop().run_in_executor(None, _av_to_wav, data)
    except ImportError:
        raise RuntimeError("для голоса нужен пакет av (pip install av) или ffmpeg на сервере")


async def transcribe(bot, file_obj, name, seconds):
    f = await bot.download(file_obj)
    data = f.read()
    if len(data) > 20 * 1024 * 1024:
        raise RuntimeError("Файл слишком большой")
    wav = await to_wav(data, name)
    if len(wav) > 25 * 1024 * 1024:
        raise RuntimeError("Запись длиннее ~13 минут, API принимает WAV до 25 МБ")
    tr = await ai.audio.transcriptions.create(model=await setting("stt_model"), file=("audio.wav", wav))
    cost = max(500, int(seconds * await seti("stt_per_sec", 100)))
    return (tr.text or "").strip(), cost


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
async def make_article(uid, u, topic, model, with_images):
    """-> (url, потрачено токенов-тарифа). Бросает исключение при ошибке."""
    prompt = ('Напиши статью по теме и верни ТОЛЬКО JSON: {"title":"...","blocks":[{"type":"h","text":"..."},'
              '{"type":"p","text":"..."},{"type":"img","prompt":"описание картинки"}]}. '
              + ("2-3 картинки, " if with_images else "без картинок, ") + f"6-12 абзацев. Тема: {topic}")
    raw, used, _ = await chat_long([{"role": "user", "content": prompt}], model, 2, 3500)
    data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    nodes, charged = [], max(1, math.ceil(used * MULT.get(model, 1.0)))
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
        elif b["type"] == "img" and with_images:
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
            [btn("support", "m:support"), btn("mysettings", "m:set")], [btn("clear", "m:clear")]]
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


class A(StatesGroup):
    input = State()
    emo_pack = State()
    banner = State()
    mbanner = State()


# ---------------------- экраны ----------------------
PERIODS = [(30, "30 дней"), (180, "6 месяцев"), (365, "1 год")]


def greeting(tz):
    h = time.gmtime(time.time() + tz * 3600).tm_hour
    return "Доброе утро" if 5 <= h < 11 else "Добрый день" if 11 <= h < 17 else "Добрый вечер" if 17 <= h < 23 else "Доброй ночи"


async def welcome_text(tu):
    t = await text("welcome2")
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
    L = ["👤 <b>Профиль</b>", f"Имя: {H.escape(tu.full_name)}", f"ID: <code>{uid}</code>", ""]
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
    return "\n".join(L), kb(rows, back="nav:home")


LEN_RU = {"short": "короткая", "mid": "средняя", "long": "подробная"}


async def settings_screen(u):
    onoff = lambda v: "вкл" if v else "выкл"
    txt = (f"⚙️ <b>Мои настройки</b>\n\n📏 Длина ответа: <b>{LEN_RU[u['length']]}</b>\n🧠 Память чата: <b>{onoff(u['memory'])}</b>\n"
           f"🔊 Озвучка ответов: <b>{onoff(u['voice'])}</b>\n🌐 Поиск в сети для каждого вопроса: <b>{onoff(u['search'])}</b>\n"
           f"✏️ Свой стиль: {H.escape(u['custom_prompt'][:200]) if u['custom_prompt'] else 'не задан'}")
    rows = [[(f"📏 Длина: {LEN_RU[u['length']]}", "us:len"), (f"🧠 Память: {onoff(u['memory'])}", "us:mem")],
            [(f"🔊 Озвучка: {onoff(u['voice'])}", "us:voice"), (f"🌐 Поиск: {onoff(u['search'])}", "us:search")],
            [("✏️ Задать свой стиль", "us:prompt"), ("🗑 Сбросить стиль", "us:reset")]]
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
        ttask = asyncio.create_task(typing_loop(m.bot, m.chat.id, stop))
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
        if u["voice"] and ans.strip():
            try:
                sp = await ai.audio.speech.create(model=await setting("tts_model"), voice=await setting("tts_voice"),
                                                  input=re.sub(r"```.*?```|<<<FILE.*?<<<END>>>", "", ans, flags=re.S)[:3000],
                                                  response_format="opus")
                ids.append((await m.answer_voice(BufferedInputFile(sp.content, "a.ogg"))).message_id)
            except Exception as e:
                logging.warning("tts: %s", e)
        ids += await send_answer(m, ans, model, foot, sm[0])
        if mem:
            asst = {"role": "assistant", "content": FILE_RE.sub(lambda x: f"[файл {x.group(1).strip()} создан]", ans)[:1500]}
            hist.append(asst)
            asyncio.create_task(save_hist(uid, [user_msg, asst]))
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
    asyncio.create_task(typing_loop(m.bot, m.chat.id, stop))
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


async def do_telegraph(m: Message, topic: str, uid=None):
    uid = uid or m.from_user.id
    u = await get_user(uid)
    model, _ = await usable_model(uid, u, cur_model(u), "model")
    ok, msg = await can_use(uid, u, model)
    if not ok:
        return await reply(m, msg, reply_markup=kb([[("💎 Тарифы", "nav:plans")]], back="nav:home"))
    stop = asyncio.Event()
    asyncio.create_task(typing_loop(m.bot, m.chat.id, stop))
    try:
        with_img = exempt(uid) or bool(tariff(eff_plan(u))["img_ok"])
        url, charged = await make_article(uid, u, topic, model, with_img)
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
    await reply(m, await welcome_text(m.from_user), reply_markup=await main_kb(m.from_user.id))


@router.message(Command("key"))
async def cmd_key(m: Message):
    await try_key(m, m.text.partition(" ")[2])


@router.message(Command("help"))
async def cmd_help(m: Message):
    await reply(m, await text("help"), reply_markup=kb([], back="nav:home"))


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


@router.message(Command("asuser"))
async def cmd_asuser(m: Message):
    global TEST_MODE
    if m.from_user.id != OWNER_ID:
        return
    TEST_MODE = not TEST_MODE
    await m.answer("🧪 Режим проверки ВКЛ: ты как обычный пользователь (тариф Free). Ещё раз /asuser: выход." if TEST_MODE
                   else "✅ Режим проверки выкл: ты снова владелец.")


# ---------------------- навигация и меню ----------------------
async def games_screen():
    url = await setting("webapp_chess")
    rows = ([[("♟ Шахматы с ИИ (мини-апп)", "wa:" + url)]] if url else []) + [[("🧠 Викторина", "g:quiz"), ("🎭 Текстовый квест", "g:quest")]]
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
        await edit_msg(c.message, await text("code_help"), kb([[("🤖 Выбрать модель для кода", "cgrp:_")]], back="nav:home"))
    elif a == "search":
        u = await get_user(uid)
        v = 0 if u["search"] else 1
        await q("UPDATE users SET search=? WHERE id=?", (v, uid), commit=True)
        toast = "🌐 Поиск для каждого вопроса: " + ("вкл" if v else "выкл")
    elif a == "games":
        txt, k = await games_screen()
        await edit_msg(c.message, txt, k)
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
    elif a in ("mem", "voice", "search"):
        col = {"mem": "memory", "voice": "voice", "search": "search"}[a]
        await q(f"UPDATE users SET {col}=? WHERE id=?", (0 if u[col] else 1, uid), commit=True)
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
    if g == "_":
        groups = list(dict.fromkeys(x[0] for x in CHAT_CATALOG))
        btns = [(x, f"{kind}grp:{x}") for x in groups]
        rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
        return await edit_msg(c.message, f"{title}: сейчас <b>{TITLE.get(cur, cur)}</b>\nВыбери провайдера:", kb(rows, back="nav:models"))
    rows = []
    for grp, mid, ttl, mult in CHAT_CATALOG:
        if grp != g:
            continue
        lock = "🔒 " if (not exempt(uid) and not plan_allows(t, mid)) else ""
        rows.append([(("✅ " if mid == cur else lock) + f"{ttl} ×{mult}", f"{pref}:{mid}")])
    await edit_msg(c.message, f"{title} · <b>{g}</b>\n🔒 — недоступно на твоём тарифе, ×N — расход токенов", kb(rows, back=f"{kind}grp:_"))
    await c.answer()


async def choose_model(c: CallbackQuery, col, label):
    uid = c.from_user.id
    mid = c.data.split(":", 1)[1]
    if mid not in MODELS:
        return await c.answer("Нет такой модели", show_alert=True)
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
    st = {"paid": "✅ оплачено", "new": "⏳ ждёт оплаты", "pending": "🔎 на проверке", "rejected": "❌ отклонён"}.get(p["status"], p["status"])
    meth = {"card": "карта", "star": "Telegram Stars", "usdt": "CryptoBot", "man": "перевод", "admin": "выдано админом"}.get(p["method"], p["method"])
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
    _, code, days = c.data.split(":")
    u = await get_user(c.from_user.id)
    if not u["agreed"]:
        rows = [[("📄 Пользовательское соглашение", await setting("terms_url"))],
                [("🔒 Политика конфиденциальности", await setting("privacy_url"))],
                [("✅ Я согласен", f"agree:{code}:{days}")]]
        await edit_msg(c.message, await text("agree_text"), kb(rows, back=f"tar:{code}"))
    else:
        txt, k = await pay_methods(code, int(days))
        await edit_msg(c.message, txt, k)
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
    r = await crypto("getInvoices", invoice_ids=p["ext"])
    items = (r or {}).get("items", [])
    if items and items[0]["status"] == "paid":
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
    uid = m.from_user.id
    if uid in ADMIN_MODE or await gate(m):
        return
    if m.voice:
        obj, name, sec = m.voice, "voice.ogg", m.voice.duration
    elif m.video_note:
        obj, name, sec = m.video_note, "note.mp4", m.video_note.duration
    elif m.video:
        obj, name, sec = m.video, "video.mp4", m.video.duration
    else:
        obj, name, sec = m.audio, (m.audio.file_name or "audio.mp3"), m.audio.duration
    u = await get_user(uid)
    ok, msg = await can_use(uid, u, cur_model(u))
    if not ok:
        return await reply(m, msg, reply_markup=kb([[("💎 Тарифы", "nav:plans")]], back="nav:home"))
    await m.bot.send_chat_action(m.chat.id, "typing")
    try:
        txt, cost = await transcribe(m.bot, obj, name, sec or 10)
    except Exception as e:
        return await m.answer(f"⚠️ Не удалось распознать: <code>{H.escape(str(e))[:300]}</code>")
    if not txt:
        return await m.answer("🎙 Ничего не расслышал, попробуй ещё раз.")
    await spend(uid, u, "stt", 0, fixed=cost, kind="stt")
    await m.answer(f"🎙 <i>{H.escape(txt)}</i>")
    await respond(m, txt)


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
    if exempt(owner):
        return True
    if plan == "free":
        return model.startswith("deepseek")
    if plan == "basic":
        return not model.startswith(("claude", "gpt")) and plan_allows(tariff("basic"), model)
    return plan_allows(tariff(plan), model)


async def biz_cleanup(m):
    """Убираем команду «.ai ...» из чата, чтобы собеседник видел только ответ (нужно право удалять сообщения)."""
    if await setting("biz_delete_cmd") != "1":
        return
    try:
        await m.bot.delete_business_messages(business_connection_id=m.business_connection_id, message_ids=[m.message_id])
    except Exception as e:
        logging.info("biz delete: %s", e)


@router.business_message()
async def on_biz(m: Message):
    body = (m.text or m.caption or "").strip()
    if not body.lower().startswith(".ai"):
        return
    owner = await biz_owner(m)
    if not owner or not m.from_user or m.from_user.id != owner:
        return
    await ensure_user(m.from_user)
    prompt = body[3:].strip()
    if not prompt:
        return await m.answer("Напиши вопрос после .ai, например: .ai что такое блокчейн")
    u = await get_user(owner)
    plan = eff_plan(u)
    model = await setting(f"biz_model_{plan}") or "deepseek-v4-flash"
    mm = re.match(r"^-m\s+(\S+)\s+(.*)$", prompt, re.S)
    if mm and mm.group(1) in MODELS and biz_allowed(owner, plan, mm.group(1)):
        model, prompt = mm.group(1), mm.group(2)
    if not biz_allowed(owner, plan, model):
        model = "deepseek-v4-flash"
    ok, msg = await can_use(owner, u, model)
    if not ok:
        return await m.answer(re.sub(r"<[^>]+>", "", msg))
    try:
        await m.bot.send_chat_action(m.chat.id, "typing", business_connection_id=m.business_connection_id)
    except Exception:
        pass
    try:
        if re.search(r"телеграф|telegra", prompt, re.I) and re.search(r"стат", prompt, re.I):
            topic = re.sub(r"(?i)(сделай|напиши|создай|телеграф|telegraph|статью|статья|на тему|про)\s*", " ", prompt).strip() or prompt
            url, charged = await make_article(owner, u, topic, model, bool(tariff(plan)["img_ok"]) or exempt(owner))
            await spend(owner, u, model, 0, fixed=charged)
            await m.answer(f"📰 {url}")
            return await biz_cleanup(m)
        web = await web_context(prompt)
        last = f"[Данные из интернета]\n{web}\n\n[Вопрос]\n{prompt}\nОтветь по этим данным, дай ссылки." if web else prompt
        ans, raw, _ = await chat_long([{"role": "system", "content": "Ты отвечаешь собеседнику в чате от имени владельца аккаунта. Коротко, по делу, без LaTeX и таблиц."},
                                       {"role": "user", "content": last}], model, 1, 1200)
        await spend(owner, u, model, raw)
        for ch in chunk_text(ans, 3000):
            await send_html(m, await emoji_filter(md_html(ch)))
        await biz_cleanup(m)
    except Exception as e:
        await m.answer(f"⚠️ Ошибка: {str(e)[:200]}")


# ====================== АДМИН-ПАНЕЛЬ ======================
OWN = F.from_user.id == OWNER_ID
ADM_MAIN = kb([[("📝 Тексты", "adm:texts"), ("🔘 Кнопки", "adm:btns")],
               [("😀 Эмодзи ИИ", "adm:emo"), ("🖼 Баннеры", "adm:banner")],
               [("💎 Тарифы", "adm:tar"), ("🧾 Платежи", "adm:pay")],
               [("👥 Пользователи", "adm:ul:0"), ("🆘 Тикеты", "adm:tk")],
               [("🎁 Токены по модели", "adm:gr"), ("⚙️ Настройки", "adm:set")],
               [("🔌 API", "adm:api"), ("📊 Статистика", "adm:stat")]], back=None)
ADM_HEAD = "🛠 <b>Админ-панель</b>\nИИ молчит, пока ты не напишешь /start."
TF_NAMES = {"name": "название", "tokens": "токены в месяц (число)", "max_mult": "макс. коэффициент модели (число, 999 = все)",
            "models": "список моделей через запятую (или - чтобы очистить и работать по коэффициенту)",
            "p30": "цену за 30 дней, ₽ (0 = нет)", "p180": "цену за 6 месяцев, ₽", "p365": "цену за 1 год, ₽"}


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
        await edit_msg(m, txt, k)

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
        await show("🖼 <b>Баннеры</b>\nОбщий показывается везде, баннер модели заменяет общий, когда пользователь работает с этой моделью.",
                   kb([[("🌐 Общий баннер", "adm:bglob")], [("🤖 Баннеры моделей", "adm:bmg:_")]]))
    elif a == "bglob":
        typ, fid = await get_banner(None)
        info = {"photo": "фото", "video": "видео", "animation": "гифка"}.get(typ, "не задан")
        on = await setting("banner_answers") == "1"
        await show(f"🌐 Общий баннер: <b>{info}</b>\nНа коротких ответах ИИ: <b>{'вкл' if on else 'выкл'}</b>",
                   kb([[("➕ Загрузить/сменить", "adm:bset"), ("🗑 Убрать", "adm:bdel")], [("🔁 На ответах ИИ вкл/выкл", "adm:btog")]], back="adm:banner"))
    elif a == "bset":
        await state.set_state(A.banner)
        await show("Пришли фото, видео или гифку для общего баннера.", kb([], back="adm:bglob"))
    elif a == "bdel":
        await set_setting("banner_id", "")
        await set_setting("banner_type", "")
        await c.answer("🗑 Баннер убран")
    elif a == "btog":
        v = "0" if await setting("banner_answers") == "1" else "1"
        await set_setting("banner_answers", v)
        await c.answer("Баннер на ответах ИИ: " + ("вкл" if v == "1" else "выкл"))
    elif a == "bmg":
        g = p[2]
        if g == "_":
            groups = list(dict.fromkeys(x[0] for x in CHAT_CATALOG))
            btns = [(x, f"adm:bmg:{x}") for x in groups]
            await show("🤖 Баннер какой модели? Провайдер:", kb([btns[i:i + 2] for i in range(0, len(btns), 2)], back="adm:banner"))
        else:
            rows = [[(("🖼 " if mid in BANNERS else "") + ttl, f"adm:bm:{mid}")] for grp, mid, ttl, mult in CHAT_CATALOG if grp == g]
            await show(f"{g}: выбери модель (🖼 — баннер задан)", kb(rows, back="adm:bmg:_"))
    elif a == "bm":
        mid = p[2]
        await show(f"🤖 <b>{TITLE.get(mid, mid)}</b>: баннер {'задан' if mid in BANNERS else 'не задан'}",
                   kb([[("➕ Загрузить/сменить", f"adm:bms:{mid}"), ("🗑 Убрать", f"adm:bmd:{mid}")]], back="adm:bmg:_"))
    elif a == "bms":
        await state.set_state(A.mbanner)
        await state.update_data(model=p[2])
        await show(f"Пришли фото, видео или гифку для модели {TITLE.get(p[2], p[2])}.", kb([], back=f"adm:bm:{p[2]}"))
    elif a == "bmd":
        await q("DELETE FROM model_banners WHERE model=?", (p[2],), commit=True)
        await load_banners()
        await c.answer("🗑 Убрано")
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
        base = (await setting("api_base")) or BASE_URL
        key = (await setting("api_key")) or API_KEY
        mask = key[:4] + "…" + key[-3:] if len(key) > 8 else "не задан"
        await show(f"🔌 <b>API</b>\nАдрес: <code>{H.escape(base)}</code>\nКлюч: <code>{H.escape(mask)}</code>\n"
                   f"Источник: {'админка' if await setting('api_key') else 'переменные хостинга'}",
                   kb([[("✏️ Адрес API", "adm:apib"), ("🔑 Ключ API", "adm:apik")], [("🔄 Проверить", "adm:apit"), ("🗑 Сбросить на переменные", "adm:apir")]]))
    elif a == "apib":
        await ask("api_base", "Пришли адрес API, например https://api.basisweb.ru/v1", "adm:api")
    elif a == "apik":
        await ask("api_key", "Пришли новый ключ API. Сообщение с ключом я удалю из чата.", "adm:api")
    elif a == "apit":
        try:
            r = await ai.models.list()
            await c.answer(f"✅ API отвечает, моделей: {len(r.data)}", show_alert=True)
        except Exception as e:
            await c.answer("⚠️ " + str(e)[:180], show_alert=True)
    elif a == "apir":
        await set_setting("api_base", "")
        await set_setting("api_key", "")
        await reload_client()
        await c.answer("Сброшено на переменные хостинга", show_alert=True)
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
    await c.answer()


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
            await set_setting(d["key"], txt)
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


def media_of(m):
    if m.animation:
        return "animation", m.animation.file_id
    if m.video:
        return "video", m.video.file_id
    if m.photo:
        return "photo", m.photo[-1].file_id
    return None, None


@router.message(A.banner, OWN, F.photo | F.video | F.animation)
async def st_banner(m: Message, state: FSMContext):
    t, f = media_of(m)
    await set_setting("banner_type", t)
    await set_setting("banner_id", f)
    await state.clear()
    await reply(m, "✅ Общий баннер сохранён.", reply_markup=kb([], back="adm:banner"))


@router.message(A.mbanner, OWN, F.photo | F.video | F.animation)
async def st_mbanner(m: Message, state: FSMContext):
    model = (await state.get_data())["model"]
    t, f = media_of(m)
    await q("INSERT OR REPLACE INTO model_banners VALUES(?,?,?)", (model, t, f), commit=True)
    await load_banners()
    await state.clear()
    await reply(m, f"✅ Баннер для {TITLE.get(model, model)} сохранён.", reply_markup=kb([], back="adm:bmg:_"), model=model)


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
            ("tg", "Статья в Telegraph"), ("settings", "Мои настройки"), ("support", "Тех. поддержка"), ("receipts", "Мои чеки"),
            ("clear", "Очистить память"), ("help", "Помощь")]
    base = [BotCommand(command=c, description=d) for c, d in cmds]
    try:
        await bot.set_my_commands(base, scope=BotCommandScopeDefault())
        if OWNER_ID:
            await bot.set_my_commands(base + [BotCommand(command="admin", description="Админ-панель")], scope=BotCommandScopeChat(chat_id=OWNER_ID))
    except Exception as e:
        logging.warning("set_commands: %s", e)


async def main():
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
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    for obs in (dp.message, dp.callback_query, dp.pre_checkout_query):
        obs.outer_middleware(Track())
    dp.include_router(router)
    await set_commands(bot)
    asyncio.create_task(reminders_loop(bot))
    asyncio.create_task(crypto_loop(bot))
    logging.info("Gimbek AI запущен. Тарифы: %s", ", ".join(TARIFFS))
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
