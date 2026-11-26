"""Telegram-бот @currency_2026bot для просмотра курсов и конвертации валют."""

import asyncio
import logging
import re
from typing import Any

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# Токен Telegram-бота (BotFather)
BOT_TOKEN = "9084571379:AAESAxskZgA0THN_3eDTzdYJw9KlOb4ybP0"

# Адрес открытого API ЦБ РФ с ежедневными курсами валют
CBR_API_URL = "https://www.cbr-xml-daily.ru/daily_json.js"

# Список популярных валют для отображения в сводке курсов
POPULAR_CURRENCIES = [
    "USD",
    "EUR",
    "CNY",
    "GBP",
    "TRY",
    "KZT",
    "BYN",
    "CHF",
    "JPY",
    "AED",
    "RUB",
]

# Настройка логирования работы бота
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Роутер для регистрации обработчиков команд и сообщений
router = Router()


class ConvertStates(StatesGroup):
    """Состояния конечного автомата (FSM) для пошаговой конвертации."""

    waiting_for_query = State()


class CurrencyService:
    """Сервис получения актуальных курсов валют и выполнения конвертации."""

    def __init__(self, api_url: str = CBR_API_URL, timeout: int = 10) -> None:
        """Инициализирует сервис URL-адресом API и таймаутом запроса."""
        self.api_url = api_url
        self.timeout = timeout

    async def fetch_rates(self) -> tuple[str, dict[str, dict[str, Any]]]:
        """Загружает курсы валют из API и пересчитывает к 1 единице."""
        client_timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(self.api_url) as response:
                data = await response.json(content_type=None)

        date_str = str(data.get("Date", ""))[:10]
        valutes = data.get("Valute", {})

        # Добавляем российский рубль как базовый ориентир (1 RUB = 1.0 RUB)
        rates: dict[str, dict[str, Any]] = {
            "RUB": {
                "code": "RUB",
                "name": "Российский рубль",
                "rate_rub": 1.0,
                "prev_rub": 1.0,
            }
        }

        for code, info in valutes.items():
            nominal = float(info.get("Nominal", 1))
            value = float(info["Value"])
            previous = float(info.get("Previous", value))
            rates[code.upper()] = {
                "code": code.upper(),
                "name": str(info.get("Name", code)),
                "rate_rub": value / nominal,
                "prev_rub": previous / nominal,
            }

        return date_str, rates

    @staticmethod
    def convert(
        amount: float,
        from_code: str,
        to_code: str,
        rates: dict[str, dict[str, Any]],
    ) -> float:
        """Конвертирует сумму между двумя валютами через кросс-курс."""
        src = from_code.upper()
        dst = to_code.upper()
        amount_in_rub = amount * rates[src]["rate_rub"]
        return amount_in_rub / rates[dst]["rate_rub"]

    @staticmethod
    def parse_query(
        text: str,
        default_target: str = "RUB",
    ) -> tuple[float, str, str] | None:
        """Разбирает строку вида '100 USD RUB' или '100 USD' в кортеж."""
        cleaned = text.strip().upper().replace(",", ".")
        cleaned = re.sub(r"\s+(В|IN|TO|->)\s+", " ", cleaned)
        parts = cleaned.split()

        if len(parts) not in (2, 3):
            return None

        amount = float(parts[0])
        from_code = parts[1]
        to_code = parts[2] if len(parts) == 3 else default_target.upper()
        return amount, from_code, to_code

    def format_rates_message(
        self,
        base_code: str,
        date_str: str,
        rates: dict[str, dict[str, Any]],
    ) -> str:
        """Формирует текстовую сводку текущих курсов к базовой валюте."""
        base = base_code.upper()
        base_rub = rates[base]["rate_rub"]
        base_prev = rates[base]["prev_rub"]

        lines = [
            f"📊 <b>Актуальные курсы валют на {date_str}</b>",
            f"Базовая валюта: <b>{base}</b>\n",
        ]

        for code in POPULAR_CURRENCIES:
            if code == base or code not in rates:
                continue
            item = rates[code]
            unit_qty = 100 if code in ("KZT", "JPY") else 1
            current_val = (item["rate_rub"] * unit_qty) / base_rub
            prev_val = (item["prev_rub"] * unit_qty) / base_prev
            delta = current_val - prev_val
            arrow = "🔺" if delta > 0 else ("🔻" if delta < 0 else "▪️")
            lines.append(
                f"• <b>{unit_qty} {code}</b> ({item['name']}): "
                f"<code>{current_val:.4f} {base}</code> "
                f"{arrow} {delta:+.4f}"
            )

        lines.append("\nИсточник: данные ЦБ РФ.")
        return "\n".join(lines)


currency_service = CurrencyService()


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


def build_rates_inline_keyboard() -> InlineKeyboardMarkup:
    """Создает inline-клавиатуру для обновления списка курсов."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Обновить курсы",
                    callback_data="refresh_rates",
                )
            ]
        ]
    )


def build_cancel_keyboard() -> InlineKeyboardMarkup:
    """Создает inline-кнопку отмены ввода в режиме конвертера."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="cancel_convert",
                )
            ]
        ]
    )


async def perform_conversion(
    message: Message,
    query_text: str,
    base_currency: str = "RUB",
) -> None:
    """Выполняет разбор запроса и отправляет результат конвертации."""
    parsed = currency_service.parse_query(query_text, base_currency)
    if parsed is None:
        await message.answer(
            "Укажите запрос в формате: <code>100 USD RUB</code> "
            "или <code>100 USD</code>.",
        )
        return

    amount, from_code, to_code = parsed
    date_str, rates = await currency_service.fetch_rates()
    if from_code not in rates or to_code not in rates:
        await message.answer("Указанный код валюты не найден в базе ЦБ РФ.")
        return

    result = currency_service.convert(amount, from_code, to_code, rates)
    rate_one = currency_service.convert(1.0, from_code, to_code, rates)
    reply = (
        f"💱 <b>Результат конвертации</b> ({date_str})\n\n"
        f"<code>{amount:.2f} {from_code}</code> = "
        f"<b>{result:.2f} {to_code}</b>\n"
        f"Курс: 1 {from_code} = {rate_one:.4f} {to_code}"
    )
    await message.answer(reply, reply_markup=build_main_keyboard())


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Обработчик команды /start: приветствие пользователя."""
    await state.clear()
    user_name = message.from_user.first_name if message.from_user else "друг"
    text = (
        f"Привет, <b>{user_name}</b>! 👋\n\n"
        "Я бот <b>@currency_2026bot</b> для отслеживания курсов валют.\n"
        "Выберите действие в меню ниже или отправьте запрос вида "
        "<code>100 USD RUB</code>."
    )
    await message.answer(text, reply_markup=build_main_keyboard())


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message, state: FSMContext) -> None:
    """Обработчик команды /help: вывод подробной инструкции."""
    await state.clear()
    text = (
        "ℹ️ <b>Справка по работе бота</b>\n\n"
        "Доступные команды:\n"
        "• <code>/rates</code> — актуальные курсы валют ЦБ РФ\n"
        "• <code>/convert 100 USD RUB</code> — конвертер валют\n"
        "• <code>/base</code> — выбор базовой валюты\n"
        "• <code>/help</code> — показать эту справку\n\n"
        "Также можно писать сумму и валюты напрямую, например:\n"
        "<code>250 EUR RUB</code> или <code>50 USD в CNY</code>."
    )
    await message.answer(text, reply_markup=build_main_keyboard())


@router.message(Command("rates"))
@router.message(F.text == "📊 Текущий курс")
async def cmd_rates(message: Message, state: FSMContext) -> None:
    """Обработчик команды /rates: вывод текущих курсов валют."""
    await state.clear()
    date_str, rates = await currency_service.fetch_rates()
    text = currency_service.format_rates_message("RUB", date_str, rates)
    await message.answer(text, reply_markup=build_rates_inline_keyboard())


@router.callback_query(F.data == "refresh_rates")
async def cb_refresh_rates(callback: CallbackQuery) -> None:
    """Обновляет сообщение с курсами валют по нажатию inline-кнопки."""
    date_str, rates = await currency_service.fetch_rates()
    text = currency_service.format_rates_message("RUB", date_str, rates)
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=build_rates_inline_keyboard(),
        )
    await callback.answer("Курсы обновлены!")


@router.message(Command("convert"))
@router.message(F.text == "💱 Конвертер")
async def cmd_convert(
    message: Message,
    state: FSMContext,
    command: CommandObject | None = None,
) -> None:
    """Обработчик команды /convert и кнопки запуска конвертера."""
    if command and command.args:
        await state.clear()
        await perform_conversion(message, command.args)
        return

    await state.set_state(ConvertStates.waiting_for_query)
    await message.answer(
        "Введите сумму и валюты для конвертации.\n"
        "Примеры: <code>100 USD RUB</code>, <code>50 EUR</code>, "
        "<code>1000 RUB в CNY</code>:",
        reply_markup=build_cancel_keyboard(),
    )


@router.callback_query(F.data == "cancel_convert")
async def cb_cancel_convert(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Отменяет пошаговый ввод суммы для конвертации."""
    await state.clear()
    if callback.message:
        await callback.message.edit_text("Конвертация отменена.")
    await callback.answer()


@router.message(ConvertStates.waiting_for_query)
async def process_convert_state(message: Message, state: FSMContext) -> None:
    """Обрабатывает ввод пользователя в состоянии ожидания конвертации."""
    if not message.text:
        return
    await perform_conversion(message, message.text)
    await state.clear()


@router.message(F.text)
async def handle_text_message(message: Message) -> None:
    """Обрабатывает произвольные текстовые запросы на конвертацию."""
    if not message.text:
        return
    await perform_conversion(message, message.text)


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
