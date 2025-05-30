#!/usr/bin/env python3
"""
MGSGBT 1.0 A — Telegram‑бот (v1.6, 30‑05‑2025)
────────────────────────────────────────────────────────
• aiogram ≥ 3.7
• openai ≥ 1.23
• python‑dotenv

Изменения v1.6
──────────────
1. Исправлена опечатка **aasync ➜ async**.
2. /users выводит всех пользователей и их число.
3. Пользователи логируются в `users.txt` формата `id/username`.
4. Нет задержки перед вызовом OpenAI; анти‑спам 10 с.
5. Сообщение «🤖 Думаю…» удаляется после ответа.
6. Ключи и параметры берутся из `keys.env` в той же папке.
"""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Dict, Set

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from openai import AsyncOpenAI, APIError, AuthenticationError, RateLimitError

# ────────────────────────────────────────────────────────────────────────────
# 🔧 Загрузка конфигурации из keys.env
# ────────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / "keys.env")

TELEGRAM_TOKEN: str | None = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
OPENAI_PROJECT_ID: str | None = os.getenv("OPENAI_PROJECT_ID")
CHANNEL_ID: int = int(os.getenv("CHANNEL_ID", "0"))  # 0 => проверка отключена
INVITE_LINK: str | None = os.getenv("INVITE_LINK")

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("TELEGRAM_TOKEN или OPENAI_API_KEY не найдены в keys.env")

# ────────────────────────────────────────────────────────────────────────────
# 🛠️  Инициализация
# ────────────────────────────────────────────────────────────────────────────
openai_client = AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    project=OPENAI_PROJECT_ID,
    max_retries=0,
)

bot = Bot(
    TELEGRAM_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Файл для хранения пользователей
USERS_FILE = BASE_DIR / "users.txt"
USERS_FILE.touch(exist_ok=True)

# id -> timestamp последнего сообщения
_last_message: Dict[int, float] = {}
SPAM_INTERVAL = 10  # сек

# ────────────────────────────────────────────────────────────────────────────
# 🔧 Вспомогательные функции
# ────────────────────────────────────────────────────────────────────────────

def read_users() -> Dict[int, str]:
    users: Dict[int, str] = {}
    with USERS_FILE.open("r", encoding="utf‑8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                uid_str, uname = line.split("/", 1)
                users[int(uid_str)] = uname.lstrip()
            except ValueError:
                continue
    return users


def add_user(uid: int, uname: str):
    users = read_users()
    if uid not in users:
        with USERS_FILE.open("a", encoding="utf‑8") as f:
            f.write(f"{uid}/{uname}\n")


async def is_subscribed(user_id: int) -> bool:
    """Если CHANNEL_ID задан — проверяем подписку."""
    if CHANNEL_ID == 0:
        return True
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status not in ("left", "kicked")
    except Exception as e:
        log.warning("Cannot check subscription: %s", e)
        return False


def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Вступить", url=INVITE_LINK)] if INVITE_LINK else [],
            [InlineKeyboardButton(text="🔄 Проверить", callback_data="check_sub")],
        ]
    )


async def send_subscription_required(msg: Message):
    text = (
        "🔒 <b>Доступ закрыт!</b>\n"
        "📣 Для использования бота подпишитесь на наш канал.\n"
        "👇 Нажмите «Вступить», затем вернитесь и нажмите «Проверить»."
    )
    await msg.answer(text, reply_markup=subscription_keyboard())


async def gpt4o_reply(prompt: str) -> str:
    """Прямой запрос к GPT‑4o mini (без искусственной задержки)."""
    try:
        resp = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content.strip()
    except (RateLimitError, AuthenticationError, APIError) as e:
        log.exception("OpenAI error: %s", e)
        return f"⚠️ OpenAI: {e}"
    except Exception as e:
        log.exception("Unexpected error: %s", e)
        return "⚠️ Непредвиденная ошибка GPT-4o."


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🏠 Главное меню")]],
        resize_keyboard=True,
    )


async def send_main_menu(message: Message):
    text = (
        "📌 <b>Главное меню</b>\n"
        "🤖 <b>MGSGBT 1.0 A</b> — набор чат‑ботов в одном интерфейсе.\n\n"
        "🧩 <b>Доступные модели:</b>\n"
        " • <b>GPT‑4o mini</b>\n\n"
        "⚙️ Сейчас доступна только одна модель. (уже выбрана по умолчанию)"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="GPT‑4o mini ✅", callback_data="noop")]])
    await message.answer(text, reply_markup=kb)


# ────────────────────────────────────────────────────────────────────────────
# 🚀  Хэндлеры
# ────────────────────────────────────────────────────────────────────────────


@dp.callback_query(lambda c: c.data == "check_sub")
async def cb_check_sub(call):
    if await is_subscribed(call.from_user.id):
        await call.answer("✅ Подписка подтверждена!", show_alert=True)
        await send_main_menu(call.message)
    else:
        await call.answer("❌ Вы всё ещё не подписаны.", show_alert=True)


@dp.message(CommandStart())
async def cmd_start(message: Message):
    if not await is_subscribed(message.from_user.id):
        return await send_subscription_required(message)

    add_user(message.from_user.id, message.from_user.username or "-")
    await message.answer(
        f"👋 Привет, <b>{message.from_user.full_name}</b>!\n"
        "✨ Рад видеть тебя в MGSGBT 1.0 A. Нажми кнопку ниже, чтобы открыть меню.",
        reply_markup=main_menu_kb(),
    )
    await send_main_menu(message)


@dp.message(Command("users"))
async def cmd_users(message: Message):
    if not await is_subscribed(message.from_user.id):
        return await send_subscription_required(message)

    users = read_users()
    if not users:
        return await message.answer("🤷 Пока нет зарегистрированных пользователей.")

    lst = "\n".join(f"{uid} / @{uname}" for uid, uname in users.items())
    await message.answer(f"👥 <b>Пользователи:</b>\n{lst}\n\nВсего: <b>{len(users)}</b>")


@dp.message(F.text == "🏠 Главное меню")
async def btn_main_menu(message: Message):
    if not await is_subscribed(message.from_user.id):
        return await send_subscription_required(message)
    await send_main_menu(message)


@dp.message()
async def handle_chat(message: Message):
    # анти‑спам
    now = time.time()
    last = _last_message.get(message.from_user.id, 0)
    if now - last < SPAM_INTERVAL:
        return await message.answer("⏱️ Не чаще 1 раза в 10 секунд!")
    _last_message[message.from_user.id] = now

    if not await is_subscribed(message.from_user.id):
        return await send_subscription_required(message)

    add_user(message.from_user.id, message.from_user.username or "-")

    thinking_msg = await message.answer("🤖 <i>Думаю…</i>", reply_markup=main_menu_kb())
    asyncio.create_task(_process_message(message, thinking_msg))


async def _process_message(user_msg: Message, thinking_msg: Message):
    reply = await gpt4o_reply(user_msg.text)
    try:
        await thinking_msg.delete()
    except Exception:
        pass
    await user_msg.answer(reply, reply_markup=main_menu_kb())


# ────────────────────────────────────────────────────────────────────────────
# ▶️  Запуск
# ────────────────────────────────────────────────────────────────────────────

async def main():
    log.info("🚀 Bot started")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
