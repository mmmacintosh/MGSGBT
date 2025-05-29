#!/usr/bin/env python3
"""
🤖 bot.py — Telegram‑бот «MGSGBT 1.0 A»
────────────────────────────────────────
Ключи и токены больше не хранятся в коде — они берутся из файла
<code>keys.env</code> (формат .env). В репозиторий файл не кладите.

<details>
<summary>Как подготовить <code>keys.env</code></summary>

```
TELEGRAM_TOKEN=123456:ABC…      # токен вашего бота
OPENAI_API_KEY=sk-…             # секретный ключ OpenAI
```

</details>

Запуск:
```bash
pip install aiogram>=3.7 openai>=1.23 python-dotenv
python bot.py        # при наличии keys.env в каталоге
```
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Dict

from dotenv import load_dotenv  # pip install python-dotenv
from aiogram import Bot, Dispatcher
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

# ---------------------------------------------------------------------------
# 🔑  Загрузка секретов из keys.env
# ---------------------------------------------------------------------------

ENV_FILE = "keys.env"
load_dotenv(ENV_FILE)

try:
    TELEGRAM_TOKEN: str = os.environ["TELEGRAM_TOKEN"]
    OPENAI_API_KEY: str = os.environ["OPENAI_API_KEY"]
except KeyError as err:
    missing = err.args[0]
    raise RuntimeError(
        f"Отсутствует переменная {missing} в {ENV_FILE}. Создайте файл и заполните токены."
    ) from None

# ---------------------------------------------------------------------------
# 🛠️  Постоянные настройки (не секретные)
# ---------------------------------------------------------------------------

CHANNEL_ID = -1002240015144  # канал, на который требуется подписка
INVITE_LINK = "https://t.me/+EmJ6Z1GamvNiNDQy"
REQUEST_COOLDOWN_SEC = 10    # задержка перед запросом к модели
OPENAI_CONCURRENCY = 1       # одновременных вызовов

# ---------------------------------------------------------------------------
# 🚧  Инициализация
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(
    TELEGRAM_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY, max_retries=0)
rate_semaphore = asyncio.Semaphore(OPENAI_CONCURRENCY)

# Хранилище id → имя
USERS: Dict[int, str] = {}

# ---------------------------------------------------------------------------
# 🔧  Утилиты
# ---------------------------------------------------------------------------

async def is_subscribed(user_id: int) -> bool:
    """Проверяем, подписан ли пользователь на канал."""
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status not in ("left", "kicked")
    except Exception as e:
        log.warning("Не смог проверить подписку: %s", e)
        return False


def kb_subscription() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Вступить", url=INVITE_LINK)],
            [InlineKeyboardButton(text="🔄 Проверить", callback_data="check_sub")],
        ]
    )


async def need_subscription(msg: Message):
    text = (
        "🔒 <b>Доступ закрыт</b>\n"
        "📣 Сначала подпишитесь на наш канал.\n"
        "⬇️ Нажмите «Вступить», затем «Проверить»."
    )
    await msg.answer(text, reply_markup=kb_subscription())


async def gpt_reply(prompt: str) -> str:
    """Запрашивает GPT‑4o mini c задержкой и повторными попытками."""
    await asyncio.sleep(REQUEST_COOLDOWN_SEC)
    async with rate_semaphore:
        for attempt in range(3):
            try:
                resp = await openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.choices[0].message.content.strip()
            except RateLimitError:
                wait = 2 ** attempt
                log.warning("429 RateLimit → пауза %s с", wait)
                await asyncio.sleep(wait)
            except AuthenticationError:
                return "❌ Неверный OpenAI‑ключ."
            except APIError as e:
                log.error("OpenAI API error: %s", e)
                return "⚠️ Ошибка OpenAI, попробуйте позже."
            except Exception as e:
                log.error("Unexpected: %s", e)
                return "⚠️ Непредвидённая ошибка."
    return "🚦 Модель перегружена, повторите позже."


def kb_main() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🏠 Главное меню")]], resize_keyboard=True)


async def show_main_menu(msg: Message):
    text = (
        "📌 <b>Главное меню</b>\n"
        "🤖 MGSGBT 1.0 A\n\n"
        "🧩 Доступные модели:\n • GPT‑4o mini — компактная версия GPT‑4o."
    )
    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="GPT-4o mini ✅", callback_data="noop")]]))


async def remember_user(message: Message):
    USERS[message.from_user.id] = message.from_user.full_name or f"user_{message.from_user.id}"

# ---------------------------------------------------------------------------
# 🚀  Хэндлеры
# ---------------------------------------------------------------------------

@dp.callback_query(lambda c: c.data == "check_sub")
async def cb_check_sub(call):
    if await is_subscribed(call.from_user.id):
        await call.answer("✅ Подписка подтверждена!", show_alert=True)
        await show_main_menu(call.message)
    else:
        await call.answer("❌ Подписка не обнаружена.", show_alert=True)


@dp.message(CommandStart())
async def cmd_start(message: Message):
    if not await is_subscribed(message.from_user.id):
        return await need_subscription(message)

    await remember_user(message)
    await message.answer(
        f"👋 Привет, <b>{USERS[message.from_user.id]}</b>! Меню внизу экрана.",
        reply_markup=kb_main(),
    )
    await show_main_menu(message)


@dp.message(Command("users"))
async def cmd_users(message: Message):
    if not await is_subscribed(message.from_user.id):
        return await need_subscription(message)

    if not USERS:
        return await message.answer("🤷 Пользователей пока нет.")
    roster = "\n".join(f" • {name}" for name in USERS.values())
    await message.answer(f"👥 <b>Пользователи:</b>\n{roster}\n\nВсего: <b>{len(USERS)}</b>")


@dp.message(lambda m: m.text == "🏠 Главное меню")
async def on_main_btn(message: Message):
    if not await is_subscribed(message.from_user.id):
        return await need_subscription(message)
    await show_main_menu(message)


@dp.message()
async def handle_chat(message: Message):
    if not await is_subscribed(message.from_user.id):
        return await need_subscription(message)

    await remember_user(message)
    thinking = await message.answer("🤖 <i>Думаю…</i>", reply_markup=kb_main())
    asyncio.create_task(_process(message, thinking))


async def _process(user_msg: Message, thinking_msg: Message):
    reply = await gpt_reply(user_msg.text)
    try:
        await thinking_msg.edit_text(reply, reply_markup=kb_main())
    except Exception:
        await user_msg.answer(reply, reply_markup=kb_main())

# ---------------------------------------------------------------------------
# ▶️  Запуск
# ---------------------------------------------------------------------------

async def main():
    log.info("🚀 Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
