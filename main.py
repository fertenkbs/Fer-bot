"""
NFT Bot — личный бот для отправки NFT-текста через Telegram Business.
Команды только для владельца (OWNER_ID):
  .nft <ссылка>  — отправить шаблон с {nft} → ссылка
  /settext       — задать шаблон текста
  /setpremoji    — задать премиум-эмодзи для шаблона (пришли сообщение с ним)
  /preview       — предпросмотр шаблона
  /start         — статус и инструкция
"""
import asyncio, logging, os, json, html, re
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, BotCommand
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID  = int((os.environ.get("OWNER_ID") or "0").strip().lstrip(".").split(".")[0] or "0")
DB_FILE   = os.environ.get("DB_PATH", "/data/bot.bd")

router = Router()

# ---- хранилище ----
def load_db():
    try:
        return json.loads(open(DB_FILE, encoding="utf-8").read())
    except Exception:
        return {}

def save_db(d):
    try:
        open(DB_FILE, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, indent=2))
    except Exception as e:
        logging.warning("save_db: %s", e)

def get_template():
    return load_db().get("template", "🔥 Смотри это NFT: {nft}\n\nУникальная работа!")

def get_premoji():
    return load_db().get("premoji_id", "")

def set_template(text):
    d = load_db(); d["template"] = text; save_db(d)

def set_premoji(cid):
    d = load_db(); d["premoji_id"] = cid; save_db(d)

# ---- состояния ----
class S(StatesGroup):
    text = State()
    emoji = State()

# ---- утилиты ----
def build_message(nft_content: str) -> str:
    tpl = get_template()
    # Вставляем весь текст после .nft как обычный текст, не ломая HTML-шаблон.
    text = tpl.replace("{nft}", html.escape(nft_content))
    cid = get_premoji()
    if cid:
        text = f'<tg-emoji emoji-id="{cid}">🔥</tg-emoji> ' + text
    return text

def is_owner(m: Message) -> bool:
    return m.from_user and m.from_user.id == OWNER_ID

# ---- /start ----
@router.message(CommandStart())
async def cmd_start(m: Message):
    if not is_owner(m):
        return
    await m.answer(
        "👋 <b>NFT Bot</b> — только для тебя.\n\n"
        "Через Telegram Business в любом чате:\n"
        "<code>.nft https://ссылка_на_нфт</code>\n\n"
        "Команды:\n"
        "/settext — изменить шаблон текста (<code>{nft}</code> = ссылка)\n"
        "/setpremoji — задать премиум-эмодзи\n"
        "/preview — предпросмотр\n\n"
        f"Текущий шаблон:\n<code>{get_template()}</code>"
    )

# ---- /settext ----
@router.message(Command("settext"))
async def cmd_settext(m: Message, state: FSMContext):
    if not is_owner(m):
        return
    await state.set_state(S.text)
    await m.answer(
        "✏️ Пришли новый шаблон.\n"
        "Используй <code>{nft}</code> — туда подставится ссылка.\n"
        "HTML разрешён. Премиум-эмодзи можно прямо вставить."
    )

@router.message(S.text)
async def st_text(m: Message, state: FSMContext):
    if not is_owner(m):
        return
    await state.clear()
    set_template(m.html_text or m.text or "")
    await m.answer(f"✅ Шаблон сохранён:\n\n{m.html_text}")

# ---- /setpremoji ----
@router.message(Command("setpremoji"))
async def cmd_setpremoji(m: Message, state: FSMContext):
    if not is_owner(m):
        return
    await state.set_state(S.emoji)
    await m.answer("😀 Пришли сообщение с премиум-эмодзи (или «-» чтобы убрать).")

@router.message(S.emoji)
async def st_emoji(m: Message, state: FSMContext):
    if not is_owner(m):
        return
    await state.clear()
    if (m.text or "").strip() == "-":
        set_premoji("")
        return await m.answer("↩️ Премиум-эмодзи убран.")
    ents = [e for e in (m.entities or []) if e.type == "custom_emoji"]
    if not ents:
        return await m.answer("Не вижу премиум-эмодзи. Пришли ещё раз или «-» чтобы убрать.")
    cid = ents[0].custom_emoji_id
    set_premoji(cid)
    await m.answer(f"✅ Премиум-эмодзи задан: id <code>{cid}</code>")

# ---- /preview ----
@router.message(Command("preview"))
async def cmd_preview(m: Message):
    if not is_owner(m):
        return
    preview = build_message("https://example.com/nft/123")
    await m.answer(f"👁 <b>Предпросмотр:</b>\n\n{preview}")

# ---- /template ----
@router.message(Command("template"))
async def cmd_template(m: Message):
    if not is_owner(m):
        return
    cid = get_premoji()
    await m.answer(
        f"📋 <b>Текущие настройки</b>\n\n"
        f"Шаблон:\n<code>{get_template()}</code>\n\n"
        f"Премиум-эмодзи: {'задан (id ' + cid + ')' if cid else 'не задан'}"
    )

# ---- Telegram Business: .nft <ссылка> ----
@router.business_message()
async def on_biz(m: Message):
    # Только сообщения от владельца аккаунта
    if not m.from_user or m.from_user.id != OWNER_ID:
        return
    body = (m.text or m.caption or "").strip()
    # Команда должна быть отдельным токеном; всё после неё — значение {nft}.
    match = re.match(r"^\.nft(?:\s+|$)(.*)$", body, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return

    # Удаляем команду из чата
    try:
        await m.bot.delete_business_messages(
            business_connection_id=m.business_connection_id,
            message_ids=[m.message_id]
        )
    except Exception as e:
        logging.info("delete .nft: %s", e)

    nft_content = match.group(1).strip()
    if not nft_content:
        try:
            await m.bot.send_message(
                OWNER_ID,
                "⚠️ Укажи ссылку после .nft:\n<code>.nft https://...</code>"
            )
        except Exception:
            pass
        return

    text = build_message(nft_content)
    try:
        await m.answer(text)
    except Exception as e:
        logging.error("send nft: %s", e)
        try:
            await m.bot.send_message(OWNER_ID, f"⚠️ Не удалось отправить: {e}")
        except Exception:
            pass

# ---- business_connection: регистрация ----
@router.business_connection()
async def on_biz_conn(bc):
    if bc.user and bc.user.id == OWNER_ID:
        logging.info("Business connection: %s enabled=%s", bc.id, bc.is_enabled)

# ---- запуск ----
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")
    if not OWNER_ID:
        raise RuntimeError("OWNER_ID не задан")

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Инструкция"),
        BotCommand(command="settext", description="Изменить шаблон"),
        BotCommand(command="setpremoji", description="Задать премиум-эмодзи"),
        BotCommand(command="preview", description="Предпросмотр"),
        BotCommand(command="template", description="Текущие настройки"),
    ])

    logging.info("NFT Bot запущен. OWNER_ID=%s", OWNER_ID)
    await dp.start_polling(bot, allowed_updates=["message", "business_message", "business_connection"])

if __name__ == "__main__":
    asyncio.run(main())
