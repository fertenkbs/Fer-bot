import asyncio, base64, html as H, io, json, logging, re, time, zipfile
import aiohttp, aiosqlite
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, InputMediaPhoto, LabeledPrice, Message,
                           PreCheckoutQuery)
from openai import AsyncOpenAI

# ====================== ПЕРЕМЕННЫЕ ======================
BOT_TOKEN = "ВСТАВЬ_БОТ_ТОКЕН"
OWNER_ID = 0  # <- твой Telegram ID (только ты попадаешь в админку)
API_KEY = "ВСТАВЬ_API_КЛЮЧ"
BASE_URL = "https://api.imbek.fun/v1"
CRYPTO_TOKEN = ""  # токен @CryptoBot (Crypto Pay). Можно оставить пустым и задать позже здесь
DB = "bot.db"
MODELS = ["im-flash", "mimo-v2.5", "deepseek-v4-flash", "deepseek-v4-flash-0731",
          "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "claude-opus-4-6"]
# ========================================================

logging.basicConfig(level=logging.INFO)
ai = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
router = Router()
HIST: dict[int, list] = {}
REPLIES: dict[tuple, list] = {}

DEF_SYS = ("Ты умный ассистент. Отвечай на языке пользователя. Если нужно создать файлы "
           "(код, html, json, buildozer.spec и т.д.), выводи КАЖДЫЙ файл строго так:\n"
           "<<<FILE: путь/имя.расширение>>>\nсодержимое\n<<<END>>>\n"
           "Код всегда полный, без сокращений и заглушек. Для Android-приложений используй "
           "Kivy + buildozer (main.py, buildozer.spec). Для 3D используй один html с three.js (cdnjs).")
SET_DEF = {"system": DEF_SYS, "img_model": "gpt-5.6-luna", "stt_model": "whisper-1",
           "tts_model": "tts-1", "tts_voice": "alloy", "max_rounds": "30",
           "show_tokens": "1", "only_premium": "0", "tokens_total": "0", "tg_token": ""}
TEXTS = {"welcome": "👋 <b>Привет!</b> Я твой ИИ-помощник. Пиши, говори голосом, "
                    "проси картинки, код, статьи в Telegraph.",
         "no_access": "🔒 Доступ по подписке. Нажми «Подписка».",
         "plans": "💳 <b>Выбери тариф:</b>", "paid": "✅ Подписка активна до {date}",
         "img_ask": "🎨 Опиши картинку (можно «3 кот в шляпе» — 3 штуки).",
         "tg_ask": "📰 Тема статьи для Telegraph?", "code_help":
         "📎 <code>/code задача</code> — длинный код/файлы без лимита\n"
         "<code>/apk задача</code> — проект Android (Kivy+buildozer)\n"
         "<code>/3d задача</code> — 3D-сцена (html)"}
BTNS = {"models": ("Модели", "", ""), "img": ("Картинки", "", ""), "code": ("Файлы/Код", "", ""),
        "tg": ("Telegraph", "", ""), "voice": ("Голос ответа", "", ""),
        "buy": ("Подписка", "", ""), "stats": ("Статистика", "", ""),
        "clear": ("Очистить чат", "", "")}


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
    for s in ["CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, name TEXT, joined INT, sub_until INT DEFAULT 0, model TEXT, voice INT DEFAULT 0)",
              "CREATE TABLE IF NOT EXISTS texts(key TEXT PRIMARY KEY, value TEXT)",
              "CREATE TABLE IF NOT EXISTS buttons(key TEXT PRIMARY KEY, text TEXT, emoji TEXT, style TEXT)",
              "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)",
              "CREATE TABLE IF NOT EXISTS emojis(plain TEXT PRIMARY KEY, cid TEXT)",
              "CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY AUTOINCREMENT, days INT, stars INT, usdt REAL)",
              "CREATE TABLE IF NOT EXISTS paid(invoice TEXT PRIMARY KEY)"]:
        await q(s, commit=True)
    for k, v in TEXTS.items():
        await q("INSERT OR IGNORE INTO texts VALUES(?,?)", (k, v), commit=True)
    for k, (t, e, st) in BTNS.items():
        await q("INSERT OR IGNORE INTO buttons VALUES(?,?,?,?)", (k, t, e, st), commit=True)
    for k, v in SET_DEF.items():
        await q("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v), commit=True)


async def setting(k):
    r = await q("SELECT value FROM settings WHERE key=?", (k,), one=True)
    return r["value"] if r else SET_DEF.get(k, "")


async def set_setting(k, v):
    await q("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, v), commit=True)


async def text(k):
    r = await q("SELECT value FROM texts WHERE key=?", (k,), one=True)
    return r["value"] if r else TEXTS.get(k, k)


async def btn(key, cb):
    r = await q("SELECT * FROM buttons WHERE key=?", (key,), one=True)
    kw = {}
    if r["emoji"]:
        kw["icon_custom_emoji_id"] = r["emoji"]
    if r["style"]:
        kw["style"] = r["style"]
    return InlineKeyboardButton(text=r["text"], callback_data=cb, **kw)


async def get_user(uid):
    return await q("SELECT * FROM users WHERE id=?", (uid,), one=True)


async def allowed(uid):
    if uid == OWNER_ID:
        return True
    u = await get_user(uid)
    return bool(u and u["sub_until"] > time.time())


async def add_days(uid, days):
    u = await get_user(uid)
    base = max(u["sub_until"], int(time.time()))
    until = base + days * 86400
    await q("UPDATE users SET sub_until=? WHERE id=?", (until, uid), commit=True)
    return time.strftime("%d.%m.%Y", time.localtime(until))


class Track(BaseMiddleware):
    async def __call__(self, handler, event, data):
        u = event.from_user
        if u:
            await q("INSERT OR IGNORE INTO users(id,username,name,joined,model) VALUES(?,?,?,?,?)",
                    (u.id, u.username or "", u.full_name, int(time.time()), MODELS[0]), commit=True)
            await q("UPDATE users SET username=?, name=? WHERE id=?",
                    (u.username or "", u.full_name, u.id), commit=True)
        return await handler(event, data)


# ---------------------- ФОРМАТИРОВАНИЕ ----------------------
EMO_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2B06\u2B1B\u2B1C]\uFE0F?")


async def emoji_filter(s):
    rows = await q("SELECT * FROM emojis")
    mp = {r["plain"]: r["cid"] for r in rows}
    only = await setting("only_premium") == "1"

    def rep(m):
        e = m.group(0).replace("\uFE0F", "")
        if e in mp:
            return f'<tg-emoji emoji-id="{mp[e]}">{e}</tg-emoji>'
        return "" if only else m.group(0)
    return EMO_RE.sub(rep, s)


def prose_html(s):
    s = H.escape(s)
    s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", s, flags=re.M)
    return s


FILE_RE = re.compile(r"<<<FILE:\s*(.+?)>>>\n?(.*?)<<<END>>>", re.S)


async def send_answer(m: Message, answer: str):
    files = [(n.strip(), c.rstrip("\n")) for n, c in FILE_RE.findall(answer)]
    shown = FILE_RE.sub(lambda x: f"\n📎 {x.group(1).strip()}\n", answer)
    parts = re.split(r"```(\w*)\n?(.*?)```", shown, flags=re.S)
    segs = []  # (kind, lang, content)
    for i in range(0, len(parts), 3):
        if parts[i].strip():
            segs.append(("t", "", parts[i].strip()))
        if i + 2 < len(parts):
            segs.append(("c", parts[i + 1], parts[i + 2].rstrip("\n")))
    ids = []
    for kind, lang, c in segs:
        if kind == "c":
            if len(c) > 3500:
                ext = {"python": "py", "py": "py", "js": "js", "html": "html"}.get(lang, lang or "txt")
                d = await m.answer_document(BufferedInputFile(c.encode(), f"code.{ext}"))
                ids.append(d.message_id)
                continue
            body = f'<pre><code class="language-{lang}">{H.escape(c)}</code></pre>' if lang else f"<pre>{H.escape(c)}</pre>"
            chunks = [body]
        else:
            body = await emoji_filter(prose_html(c))
            chunks = [body[i:i + 3900] for i in range(0, len(body), 3900)]
        for ch in chunks:
            try:
                r = await m.answer(ch)
            except Exception:
                r = await m.answer(H.escape(re.sub(r"<[^>]+>", "", ch))[:4000], parse_mode=None)
            ids.append(r.message_id)
    for name, content in files:
        d = await m.answer_document(BufferedInputFile(content.encode(), name.split("/")[-1]),
                                    caption=f"📎 {H.escape(name)}")
        ids.append(d.message_id)
    if len(files) > 1:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, content in files:
                z.writestr(name, content)
        d = await m.answer_document(BufferedInputFile(buf.getvalue(), "project.zip"), caption="📦 Всё архивом")
        ids.append(d.message_id)
    return ids


# ---------------------- ИИ ----------------------
async def chat_long(messages, model, rounds=1, max_tokens=4000):
    out, used = "", 0
    msgs = list(messages)
    for _ in range(rounds):
        r = await ai.chat.completions.create(model=model, messages=msgs, max_tokens=max_tokens)
        part = r.choices[0].message.content or ""
        used += getattr(r.usage, "total_tokens", 0) or 0
        out += part
        if r.choices[0].finish_reason != "length":
            break
        msgs = msgs + [{"role": "assistant", "content": part},
                       {"role": "user", "content": "Продолжи ровно с места обрыва, без повторов и вступлений."}]
    total = int(await setting("tokens_total")) + used
    await set_setting("tokens_total", str(total))
    return out, used, total


async def respond(m: Message, user_text: str, rounds=1, prefix="", voice_in=False):
    uid = m.from_user.id
    if not await allowed(uid):
        return await m.answer(await text("no_access"), reply_markup=await buy_kb())
    u = await get_user(uid)
    hist = HIST.setdefault(uid, [])
    hist.append({"role": "user", "content": prefix + user_text})
    await m.bot.send_chat_action(m.chat.id, "typing")
    try:
        msgs = [{"role": "system", "content": await setting("system")}] + hist[-20:]
        ans, used, total = await chat_long(msgs, u["model"], rounds, 8000 if rounds > 1 else 4000)
    except Exception as e:
        hist.pop()
        return await m.answer(f"⚠️ Ошибка API: <code>{H.escape(str(e))[:500]}</code>")
    hist.append({"role": "assistant", "content": ans})
    ids = []
    if u["voice"] and ans.strip():
        try:
            sp = await ai.audio.speech.create(model=await setting("tts_model"), voice=await setting("tts_voice"),
                                              input=re.sub(r"```.*?```|<<<FILE.*?<<<END>>>", "", ans, flags=re.S)[:4000],
                                              response_format="opus")
            v = await m.answer_voice(BufferedInputFile(sp.content, "a.ogg"))
            ids.append(v.message_id)
        except Exception as e:
            logging.warning("tts: %s", e)
    ids += await send_answer(m, ans)
    if await setting("show_tokens") == "1":
        r = await m.answer(f"<i>🪙 {used} токенов · всего {total}</i>")
        ids.append(r.message_id)
    REPLIES[(m.chat.id, m.message_id)] = ids


async def gen_images(prompt, n=1):
    r = await ai.images.generate(model=await setting("img_model"), prompt=prompt, n=n)
    out = []
    for d in r.data:
        if getattr(d, "b64_json", None):
            out.append(BufferedInputFile(base64.b64decode(d.b64_json), "img.png"))
        elif getattr(d, "url", None):
            out.append(d.url)
    return out


async def do_image(m: Message, raw: str):
    if not await allowed(m.from_user.id):
        return await m.answer(await text("no_access"))
    n = 1
    mt = re.match(r"^([1-4])\s+(.+)", raw, re.S)
    if mt:
        n, raw = int(mt.group(1)), mt.group(2)
    await m.bot.send_chat_action(m.chat.id, "upload_photo")
    try:
        imgs = await gen_images(raw, n)
        if not imgs:
            return await m.answer("⚠️ Модель не вернула картинку. Смени img_model в админке → Настройки.")
        if len(imgs) == 1:
            await m.answer_photo(imgs[0])
        else:
            await m.answer_media_group([InputMediaPhoto(media=i) for i in imgs])
    except Exception as e:
        await m.answer(f"⚠️ Ошибка: <code>{H.escape(str(e))[:500]}</code>")


async def do_telegraph(m: Message, topic: str):
    if not await allowed(m.from_user.id):
        return await m.answer(await text("no_access"))
    wait = await m.answer("⏳ Пишу статью...")
    u = await get_user(m.from_user.id)
    prompt = ('Напиши статью по теме и верни ТОЛЬКО JSON: {"title":"...","blocks":[{"type":"h","text":"..."},'
              '{"type":"p","text":"..."},{"type":"img","prompt":"описание картинки"}]}. '
              f"2-3 картинки, 6-12 абзацев. Тема: {topic}")
    try:
        raw, _, _ = await chat_long([{"role": "user", "content": prompt}], u["model"], 3, 4000)
        data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        nodes = []
        async with aiohttp.ClientSession() as s:
            tok = await setting("tg_token")
            if not tok:
                r = await (await s.post("https://api.telegra.ph/createAccount",
                                        data={"short_name": "AIBot", "author_name": "AI"})).json()
                tok = r["result"]["access_token"]
                await set_setting("tg_token", tok)
            for b in data["blocks"]:
                if b["type"] == "p":
                    nodes.append({"tag": "p", "children": [b["text"]]})
                elif b["type"] == "h":
                    nodes.append({"tag": "h3", "children": [b["text"]]})
                elif b["type"] == "img":
                    try:
                        im = (await gen_images(b["prompt"], 1))[0]
                        if isinstance(im, BufferedInputFile):
                            fd = aiohttp.FormData()
                            fd.add_field("file", im.data, filename="i.png", content_type="image/png")
                            up = await (await s.post("https://telegra.ph/upload", data=fd)).json()
                            src = "https://telegra.ph" + up[0]["src"]
                        else:
                            src = im
                        nodes.append({"tag": "img", "attrs": {"src": src}})
                    except Exception as e:
                        logging.warning("tg img: %s", e)
            r = await (await s.post("https://api.telegra.ph/createPage", data={
                "access_token": tok, "title": data["title"][:256],
                "content": json.dumps(nodes), "return_content": "false"})).json()
        await wait.edit_text(f"✅ {r['result']['url']}")
    except Exception as e:
        await wait.edit_text(f"⚠️ Ошибка: <code>{H.escape(str(e))[:500]}</code>")


# ---------------------- ОПЛАТА ----------------------
async def buy_kb():
    rows = []
    for p in await q("SELECT * FROM plans"):
        row = [InlineKeyboardButton(text=f"{p['days']} дн · {p['stars']}⭐", callback_data=f"bs:{p['id']}")]
        if CRYPTO_TOKEN and p["usdt"]:
            row.append(InlineKeyboardButton(text=f"{p['usdt']} USDT", callback_data=f"bc:{p['id']}"))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows or [[InlineKeyboardButton(text="Тарифов нет", callback_data="x")]])


async def crypto(method, **params):
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"https://pay.crypt.bot/api/{method}", json=params,
                         headers={"Crypto-Pay-API-Token": CRYPTO_TOKEN})
        return (await r.json()).get("result")


@router.callback_query(F.data.startswith("bs:"))
async def pay_stars(c: CallbackQuery):
    p = await q("SELECT * FROM plans WHERE id=?", (int(c.data[3:]),), one=True)
    await c.message.answer_invoice(title=f"Подписка {p['days']} дн", description="Доступ к ИИ-боту",
                                   payload=f"sub:{p['id']}", currency="XTR", provider_token="",
                                   prices=[LabeledPrice(label="Подписка", amount=p["stars"])])
    await c.answer()


@router.pre_checkout_query()
async def pre(p: PreCheckoutQuery):
    await p.answer(ok=True)


@router.message(F.successful_payment)
async def paid_ok(m: Message):
    pid = int(m.successful_payment.invoice_payload.split(":")[1])
    p = await q("SELECT * FROM plans WHERE id=?", (pid,), one=True)
    date = await add_days(m.from_user.id, p["days"])
    await m.answer((await text("paid")).format(date=date))


@router.callback_query(F.data.startswith("bc:"))
async def pay_crypto(c: CallbackQuery):
    pid = int(c.data[3:])
    p = await q("SELECT * FROM plans WHERE id=?", (pid,), one=True)
    inv = await crypto("createInvoice", asset="USDT", amount=str(p["usdt"]),
                       description=f"Подписка {p['days']} дн")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Оплатить", url=inv["bot_invoice_url"])],
        [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"ck:{inv['invoice_id']}:{pid}")]])
    await c.message.answer("Оплати счёт и нажми «Проверить»:", reply_markup=kb)
    await c.answer()


@router.callback_query(F.data.startswith("ck:"))
async def check_crypto(c: CallbackQuery):
    _, inv, pid = c.data.split(":")
    r = await crypto("getInvoices", invoice_ids=inv)
    items = (r or {}).get("items", [])
    if not items or items[0]["status"] != "paid":
        return await c.answer("Оплата ещё не пришла", show_alert=True)
    if await q("SELECT 1 FROM paid WHERE invoice=?", (inv,), one=True):
        return await c.answer("Уже начислено", show_alert=True)
    await q("INSERT INTO paid VALUES(?)", (inv,), commit=True)
    p = await q("SELECT * FROM plans WHERE id=?", (int(pid),), one=True)
    date = await add_days(c.from_user.id, p["days"])
    await c.message.answer((await text("paid")).format(date=date))
    await c.answer()


# ---------------------- ПОЛЬЗОВАТЕЛЬ ----------------------
class G(StatesGroup):
    img = State()
    tg = State()


async def main_kb(uid):
    rows = [[await btn("models", "m:models"), await btn("img", "m:img")],
            [await btn("code", "m:code"), await btn("tg", "m:tg")],
            [await btn("voice", "m:voice"), await btn("buy", "m:buy")],
            [await btn("stats", "m:stats"), await btn("clear", "m:clear")]]
    if uid == OWNER_ID:
        rows.append([InlineKeyboardButton(text="🛠 Админ-панель", callback_data="adm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(await text("welcome"), reply_markup=await main_kb(m.from_user.id))


@router.callback_query(F.data.startswith("m:"))
async def menu(c: CallbackQuery, state: FSMContext):
    a = c.data[2:]
    uid = c.from_user.id
    if a == "models":
        u = await get_user(uid)
        kb = [[InlineKeyboardButton(text=("✅ " if x == u["model"] else "") + x, callback_data=f"mod:{x}")] for x in MODELS]
        await c.message.answer("🤖 Выбери модель:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    elif a == "img":
        await state.set_state(G.img)
        await c.message.answer(await text("img_ask"))
    elif a == "tg":
        await state.set_state(G.tg)
        await c.message.answer(await text("tg_ask"))
    elif a == "code":
        await c.message.answer(await text("code_help"))
    elif a == "voice":
        u = await get_user(uid)
        await q("UPDATE users SET voice=? WHERE id=?", (0 if u["voice"] else 1, uid), commit=True)
        await c.message.answer("🔊 Голосовые ответы: " + ("выкл" if u["voice"] else "вкл"))
    elif a == "buy":
        await c.message.answer(await text("plans"), reply_markup=await buy_kb())
    elif a == "stats":
        u = await get_user(uid)
        left = "∞" if uid == OWNER_ID else time.strftime("%d.%m.%Y", time.localtime(u["sub_until"]))
        await c.message.answer(f"📊 Модель: <b>{u['model']}</b>\n🪙 Токенов всего: <b>{await setting('tokens_total')}</b>\n⏳ Подписка до: {left}")
    elif a == "clear":
        HIST.pop(uid, None)
        await c.message.answer("🧹 Контекст очищен")
    await c.answer()


@router.callback_query(F.data.startswith("mod:"))
async def set_model(c: CallbackQuery):
    await q("UPDATE users SET model=? WHERE id=?", (c.data[4:], c.from_user.id), commit=True)
    await c.message.edit_text(f"✅ Модель: <b>{c.data[4:]}</b>")


@router.message(Command("img"))
async def cmd_img(m: Message):
    arg = m.text.partition(" ")[2]
    await do_image(m, arg) if arg else await m.answer(await text("img_ask"))


@router.message(Command("tg"))
async def cmd_tg(m: Message):
    arg = m.text.partition(" ")[2]
    await do_telegraph(m, arg) if arg else await m.answer(await text("tg_ask"))


async def long_cmd(m: Message, prefix):
    arg = m.text.partition(" ")[2]
    if not arg:
        return await m.answer(await text("code_help"))
    await respond(m, arg, rounds=int(await setting("max_rounds")), prefix=prefix)


@router.message(Command("code"))
async def cmd_code(m: Message):
    await long_cmd(m, "Сделай полностью, без сокращений; каждый файл в формате <<<FILE: имя>>>...<<<END>>>. Задача: ")


@router.message(Command("apk"))
async def cmd_apk(m: Message):
    await long_cmd(m, "Сделай Android-проект Kivy+buildozer (main.py, buildozer.spec и нужные файлы), полностью. Задача: ")


@router.message(Command("3d"))
async def cmd_3d(m: Message):
    await long_cmd(m, "Сделай 3D-сцену одним файлом scene.html на three.js (cdnjs), красиво и полностью. Задача: ")


@router.message(Command("grant"))
async def grant(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    try:
        _, uid, d = m.text.split()
        await m.answer("✅ до " + await add_days(int(uid), int(d)))
    except Exception:
        await m.answer("/grant user_id дней")


@router.message(G.img, F.text)
async def st_img(m: Message, state: FSMContext):
    await state.clear()
    await do_image(m, m.text)


@router.message(G.tg, F.text)
async def st_tg(m: Message, state: FSMContext):
    await state.clear()
    await do_telegraph(m, m.text)


@router.message(F.voice)
async def on_voice(m: Message):
    if not await allowed(m.from_user.id):
        return await m.answer(await text("no_access"))
    f = await m.bot.download(m.voice)
    try:
        tr = await ai.audio.transcriptions.create(model=await setting("stt_model"), file=("v.ogg", f.read()))
    except Exception as e:
        return await m.answer(f"⚠️ Распознавание не удалось: <code>{H.escape(str(e))[:300]}</code>")
    await m.answer(f"🎙 <i>{H.escape(tr.text)}</i>")
    await respond(m, tr.text)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    await respond(m, m.text)


@router.edited_message(F.text)
async def on_edit(m: Message):
    for i in REPLIES.pop((m.chat.id, m.message_id), []):
        try:
            await m.bot.delete_message(m.chat.id, i)
        except Exception:
            pass
    h = HIST.get(m.from_user.id, [])
    if len(h) >= 2:
        del h[-2:]
    await respond(m, m.text)


# ---------------------- АДМИНКА ----------------------
class A(StatesGroup):
    text = State()
    btn_text = State()
    btn_emoji = State()
    emo_plain = State()
    emo_cid = State()
    plan = State()
    sett = State()


def own(c):
    return c.from_user.id == OWNER_ID


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in r] for r in rows])


def custom_emoji(m: Message):
    for e in m.entities or []:
        if e.type == "custom_emoji":
            return e.custom_emoji_id, e.extract_from(m.text)
    return None, None


@router.message(Command("admin"), F.from_user.id == OWNER_ID)
async def adm_cmd(m: Message):
    await m.answer("🛠 <b>Админ-панель</b>", reply_markup=ADM_MAIN)


ADM_MAIN = kb([[("📝 Тексты", "adm:texts"), ("🔘 Кнопки", "adm:btns")],
               [("😀 Эмодзи ИИ", "adm:emo"), ("💳 Тарифы", "adm:plans")],
               [("👥 Пользователи", "adm:users"), ("⚙️ Настройки", "adm:set")]])


@router.callback_query(F.data.startswith("adm:"), F.from_user.id == OWNER_ID)
async def adm(c: CallbackQuery, state: FSMContext):
    await state.clear()
    p = c.data.split(":")
    a = p[1]
    m = c.message
    if a == "main":
        await m.answer("🛠 <b>Админ-панель</b>", reply_markup=ADM_MAIN)
    elif a == "texts":
        await m.answer("Какой текст менять?", reply_markup=kb([[(r["key"], f"adm:t:{r['key']}")] for r in await q("SELECT key FROM texts")]))
    elif a == "t":
        await state.set_state(A.text)
        await state.update_data(key=p[2])
        await m.answer(f"Текущий:\n<code>{H.escape(await text(p[2]))}</code>\n\nПришли новый (HTML и премиум-эмодзи можно):")
    elif a == "btns":
        await m.answer("Какую кнопку менять?", reply_markup=kb([[(f"{r['text']} [{r['style'] or 'обычная'}]", f"adm:b:{r['key']}")] for r in await q("SELECT * FROM buttons")]))
    elif a == "b":
        k = p[2]
        await m.answer(f"Кнопка <b>{k}</b>", reply_markup=kb([
            [("✏️ Текст", f"adm:bt:{k}"), ("😀 Премиум-эмодзи", f"adm:be:{k}")],
            [("🔵 primary", f"adm:bs:{k}:primary"), ("🟢 success", f"adm:bs:{k}:success")],
            [("🔴 danger", f"adm:bs:{k}:danger"), ("⚪ обычная", f"adm:bs:{k}:none")]]))
    elif a == "bt":
        await state.set_state(A.btn_text)
        await state.update_data(key=p[2])
        await m.answer("Пришли новый текст кнопки (без эмодзи, эмодзи — отдельно):")
    elif a == "be":
        await state.set_state(A.btn_emoji)
        await state.update_data(key=p[2])
        await m.answer("Пришли сообщение с премиум-эмодзи (или «-» убрать):")
    elif a == "bs":
        await q("UPDATE buttons SET style=? WHERE key=?", ("" if p[3] == "none" else p[3], p[2]), commit=True)
        await m.answer("✅ Цвет изменён. /start чтобы увидеть.")
    elif a == "emo":
        rows = await q("SELECT * FROM emojis")
        only = await setting("only_premium") == "1"
        lst = "\n".join(f"{r['plain']} → <tg-emoji emoji-id=\"{r['cid']}\">{r['plain']}</tg-emoji>" for r in rows) or "пусто"
        await m.answer(f"Разрешённые премиум-эмодзи ИИ:\n{lst}\n\nТолько премиум: <b>{'да' if only else 'нет'}</b>",
                       reply_markup=kb([[("➕ Добавить", "adm:eadd"), ("🗑 Очистить", "adm:eclr")],
                                        [("🔁 Только премиум вкл/выкл", "adm:etog")]]))
    elif a == "eadd":
        await state.set_state(A.emo_plain)
        await m.answer("Пришли ОБЫЧНЫЙ эмодзи, который ИИ пишет (например 🔥), затем попрошу премиум-версию:")
    elif a == "eclr":
        await q("DELETE FROM emojis", commit=True)
        await m.answer("🗑 Очищено")
    elif a == "etog":
        v = "0" if await setting("only_premium") == "1" else "1"
        await set_setting("only_premium", v)
        await m.answer("Только премиум: " + ("да" if v == "1" else "нет"))
    elif a == "plans":
        rows = await q("SELECT * FROM plans")
        await m.answer("💳 Тарифы:", reply_markup=kb([[(f"🗑 {r['days']}д · {r['stars']}⭐ · {r['usdt']}$", f"adm:pdel:{r['id']}")] for r in rows] + [[("➕ Добавить", "adm:padd")]]))
    elif a == "padd":
        await state.set_state(A.plan)
        await m.answer("Пришли: <code>дни звёзды usdt</code>\nНапример: <code>30 150 3</code> (usdt 0 = без крипты)")
    elif a == "pdel":
        await q("DELETE FROM plans WHERE id=?", (int(p[2]),), commit=True)
        await m.answer("🗑 Удалено")
    elif a == "users":
        rows = await q("SELECT * FROM users ORDER BY joined DESC LIMIT 40")
        n = (await q("SELECT COUNT(*) c FROM users", one=True))["c"]
        lst = "\n".join(f"{r['id']} @{r['username'] or '-'} {H.escape(r['name'])}" + (" ✅" if r["sub_until"] > time.time() else "") for r in rows)
        await m.answer(f"👥 Всего: {n}\n\n{lst}\n\nВыдать доступ: /grant id дней")
    elif a == "set":
        keys = [k for k in SET_DEF if k not in ("tokens_total", "tg_token")]
        await m.answer("⚙️ Настройка:", reply_markup=kb([[(k, f"adm:s:{k}")] for k in keys]))
    elif a == "s":
        await state.set_state(A.sett)
        await state.update_data(key=p[2])
        await m.answer(f"<b>{p[2]}</b> = <code>{H.escape((await setting(p[2]))[:800])}</code>\nПришли новое значение:")
    await c.answer()


@router.message(A.text, F.from_user.id == OWNER_ID)
async def st_text(m: Message, state: FSMContext):
    k = (await state.get_data())["key"]
    await q("UPDATE texts SET value=? WHERE key=?", (m.html_text, k), commit=True)
    await state.clear()
    await m.answer("✅ Сохранено", reply_markup=ADM_MAIN)


@router.message(A.btn_text, F.from_user.id == OWNER_ID)
async def st_btext(m: Message, state: FSMContext):
    k = (await state.get_data())["key"]
    await q("UPDATE buttons SET text=? WHERE key=?", (m.text, k), commit=True)
    await state.clear()
    await m.answer("✅ Сохранено", reply_markup=ADM_MAIN)


@router.message(A.btn_emoji, F.from_user.id == OWNER_ID)
async def st_bemoji(m: Message, state: FSMContext):
    k = (await state.get_data())["key"]
    cid, _ = custom_emoji(m)
    if m.text.strip() == "-":
        cid = ""
    elif not cid and m.text.strip().isdigit():
        cid = m.text.strip()
    elif not cid:
        return await m.answer("Не вижу премиум-эмодзи, пришли ещё раз")
    await q("UPDATE buttons SET emoji=? WHERE key=?", (cid, k), commit=True)
    await state.clear()
    await m.answer("✅ Готово", reply_markup=ADM_MAIN)


@router.message(A.emo_plain, F.from_user.id == OWNER_ID)
async def st_ep(m: Message, state: FSMContext):
    await state.update_data(plain=m.text.strip().replace("\uFE0F", "")[:4])
    await state.set_state(A.emo_cid)
    await m.answer("Теперь пришли премиум-эмодзи, которым заменять:")


@router.message(A.emo_cid, F.from_user.id == OWNER_ID)
async def st_ec(m: Message, state: FSMContext):
    cid, _ = custom_emoji(m)
    if not cid:
        return await m.answer("Не вижу премиум-эмодзи, пришли ещё раз")
    plain = (await state.get_data())["plain"]
    await q("INSERT OR REPLACE INTO emojis VALUES(?,?)", (plain, cid), commit=True)
    await state.clear()
    await m.answer("✅ Добавлено", reply_markup=ADM_MAIN)


@router.message(A.plan, F.from_user.id == OWNER_ID)
async def st_plan(m: Message, state: FSMContext):
    try:
        d, s, u = m.text.split()
        await q("INSERT INTO plans(days,stars,usdt) VALUES(?,?,?)", (int(d), int(s), float(u)), commit=True)
        await state.clear()
        await m.answer("✅ Тариф добавлен", reply_markup=ADM_MAIN)
    except Exception:
        await m.answer("Формат: 30 150 3")


@router.message(A.sett, F.from_user.id == OWNER_ID)
async def st_set(m: Message, state: FSMContext):
    await set_setting((await state.get_data())["key"], m.text)
    await state.clear()
    await m.answer("✅ Сохранено", reply_markup=ADM_MAIN)


# ---------------------- ЗАПУСК ----------------------
async def main():
    await init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(Track())
    dp.callback_query.outer_middleware(Track())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
