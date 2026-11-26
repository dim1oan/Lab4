"""Telegram-бот @currency_2026bot для просмотра курсов и конвертации валют."""

import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

# Токен Telegram-бота (BotFather)
BOT_TOKEN = "9084571379:AAESAxskZgA0THN_3eDTzdYJw9KlOb4ybP0"

# Настройка логирования работы бота
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Роутер для регистрации обработчиков команд и сообщений
router = Router()


def build_main_keyboard() -> ReplyKeyboardMarkup:
    """Создает основную клавиатуру меню бота."""
    keyboard = [
        [
            KeyboardButton(text="📊 Текущий курс"),
            KeyboardButton(text="💱 Конвертер"),
        ],
        [
            KeyboardButton(text="⚙️ Базовая валюта"),
            KeyboardButton(text="ℹ️ Помощь"),
        ],
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Обработчик команды /start: приветствие пользователя."""
    user_name = message.from_user.first_name if message.from_user else "друг"
    text = (
        f"Привет, <b>{user_name}</b>! 👋\n\n"
        "Я бот <b>@currency_2026bot</b> для отслеживания курсов валют.\n"
        "Выберите действие в меню ниже или используйте команды:\n"
        "• /rates — текущие курсы валют\n"
        "• /convert — конвертер валют\n"
        "• /base — выбор базовой валюты\n"
        "• /help — справка по работе бота"
    )
    await message.answer(text, reply_markup=build_main_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Обработчик команды /help: вывод инструкции."""
    text = (
        "ℹ️ <b>Справка по работе бота</b>\n\n"
        "Доступные команды:\n"
        "• <code>/rates</code> — показать актуальные курсы валют ЦБ РФ\n"
        "• <code>/convert 100 USD RUB</code> — перевести сумму\n"
        "• <code>/base</code> — сменить базовую валюту\n"
        "• <code>/help</code> — показать эту справку"
    )
    await message.answer(text, reply_markup=build_main_keyboard())


async def main() -> None:
    """Точка входа: инициализация бота и запуск polling."""
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)
    logger.info("Бот @currency_2026bot запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
