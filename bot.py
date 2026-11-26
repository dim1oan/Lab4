"""Telegram-бот @currency_2026bot для просмотра курсов и конвертации валют."""

import asyncio
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
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
from dotenv import load_dotenv

# Загружаем переменные окружения из локального файла .env (рядом с bot.py)
ENV_FILE = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_FILE)

# Токен Telegram-бота считывается из переменной окружения (не хранится в коде)
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Адрес открытого API ЦБ РФ с ежедневными курсами валют
CBR_API_URL = "https://www.cbr-xml-daily.ru/daily_json.js"

# Файл для хранения пользовательских настроек (базовая валюта)
SETTINGS_FILE = Path(__file__).resolve().parent / "user_settings.json"

# Валюта по умолчанию для новых пользователей
DEFAULT_BASE_CURRENCY = "RUB"

# Валюты, доступные для быстрого выбора в настройках
BASE_CURRENCIES = [
    ("🇷🇺 RUB", "RUB"),
    ("🇺🇸 USD", "USD"),
    ("🇪🇺 EUR", "EUR"),
    ("🇨🇳 CNY", "CNY"),
    ("🇬🇧 GBP", "GBP"),
    ("🇰🇿 KZT", "KZT"),
    ("🇹🇷 TRY", "TRY"),
    ("🇧🇾 BYN", "BYN"),
]

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


class BotBaseError(Exception):
    """Базовое исключение для всех прикладных ошибок чат-бота."""


class CurrencyAPIError(BotBaseError):
    """Ошибка сетевого взаимодействия с внешним API курсов валют."""


class CurrencyParseError(BotBaseError):
    """Ошибка разбора или валидации структуры ответа API."""


class ValidationError(BotBaseError):
    """Ошибка пользовательского ввода (сумма или код валюты)."""


class SettingsStorageError(BotBaseError):
    """Ошибка чтения или записи JSON-файла настроек пользователей."""


class ConvertStates(StatesGroup):
    """Состояния конечного автомата (FSM) для пошаговой конвертации."""

    waiting_for_query = State()


class SettingsStorage:
    """Хранилище пользовательских настроек в формате JSON."""

    def __init__(self, filepath: Path = SETTINGS_FILE) -> None:
        """Инициализирует хранилище путем к файлу настроек."""
        self.filepath = filepath
        self._data: dict[str, dict[str, str]] = self._load()

    def _load(self) -> dict[str, dict[str, str]]:
        """Загружает словарь настроек из JSON-файла с защитой от сбоев."""
        if not self.filepath.exists():
            return {}
        try:
            raw_text = self.filepath.read_text(encoding="utf-8")
            if not raw_text.strip():
                return {}
            parsed = json.loads(raw_text)
            if not isinstance(parsed, dict):
                logger.warning("Некорректный формат файла настроек, сброс.")
                return {}
            return parsed
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Не удалось прочитать настройки: %s", exc)
            return {}

    def _save(self) -> None:
        """Сохраняет текущие настройки пользователей на диск в JSON."""
        try:
            content = json.dumps(self._data, ensure_ascii=False, indent=2)
            self.filepath.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise SettingsStorageError(
                "Не удалось сохранить настройки на диск. Попробуйте позже."
            ) from exc

    def get_base_currency(self, user_id: int) -> str:
        """Возвращает сохраненную базовую валюту пользователя или RUB."""
        user_key = str(user_id)
        record = self._data.get(user_key, {})
        currency = record.get("base_currency", DEFAULT_BASE_CURRENCY)
        return str(currency).upper()

    def set_base_currency(self, user_id: int, currency: str) -> None:
        """Устанавливает и сохраняет базовую валюту для пользователя."""
        code = currency.strip().upper()
        if not code.isalpha() or len(code) != 3:
            raise ValidationError(
                "Код валюты должен состоять из 3 латинских букв (напр. USD)."
            )
        user_key = str(user_id)
        self._data[user_key] = {"base_currency": code}
        self._save()


class CurrencyService:
    """Сервис получения актуальных курсов валют и выполнения конвертации."""

    def __init__(self, api_url: str = CBR_API_URL, timeout: int = 10) -> None:
        """Инициализирует сервис URL-адресом API и таймаутом запроса."""
        self.api_url = api_url
        self.timeout = timeout

    async def fetch_rates(self) -> tuple[str, dict[str, dict[str, Any]]]:
        """Загружает курсы валют из API и пересчитывает к 1 единице."""
        client_timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as s:
                async with s.get(self.api_url) as response:
                    if response.status != 200:
                        raise CurrencyAPIError(
                            f"Сервер курсов валют вернул код "
                            f"{response.status}. Попробуйте позже."
                        )
                    raw_text = await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            logger.error("Сетевая ошибка при запросе API: %s", exc)
            raise CurrencyAPIError(
                "Не удалось подключиться к серверу курсов валют. "
                "Проверьте соединение или повторите запрос позже."
            ) from exc

        try:
            data = json.loads(raw_text)
            if not isinstance(data, dict) or "Valute" not in data:
                raise KeyError("Отсутствует ключ Valute в ответе API")
            valutes = data["Valute"]
            if not isinstance(valutes, dict) or not valutes:
                raise ValueError("Словарь валют пуст")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            logger.error("Ошибка парсинга ответа API: %s", exc)
            raise CurrencyParseError(
                "Получен некорректный ответ от сервиса курсов валют."
            ) from exc

        date_str = str(data.get("Date", "сегодня"))[:10]

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
            try:
                nominal = float(info["Nominal"])
                value = float(info["Value"])
                previous = float(info.get("Previous", value))
                if nominal <= 0 or value <= 0:
                    continue
                rates[str(code).upper()] = {
                    "code": str(code).upper(),
                    "name": str(info.get("Name", code)),
                    "rate_rub": value / nominal,
                    "prev_rub": previous / nominal,
                }
            except (KeyError, ValueError, TypeError, ZeroDivisionError):
                logger.warning(
                    "Пропущена некорректная запись валюты: %s", code
                )

        if len(rates) <= 1:
            raise CurrencyParseError(
                "Не удалось извлечь ни одного корректного курса валют."
            )

        return date_str, rates

    @staticmethod
    def convert(
        amount: float,
        from_code: str,
        to_code: str,
        rates: dict[str, dict[str, Any]],
    ) -> float:
        """Конвертирует сумму между двумя валютами через кросс-курс."""
        if not math.isfinite(amount) or amount <= 0:
            raise ValidationError("Сумма должна быть положительным числом.")

        src = from_code.upper()
        dst = to_code.upper()
        if src not in rates:
            raise ValidationError(
                f"Валюта «{src}» не найдена. Проверьте 3-буквенный код "
                "(например: USD, EUR, CNY, RUB)."
            )
        if dst not in rates:
            raise ValidationError(
                f"Валюта «{dst}» не найдена. Проверьте 3-буквенный код "
                "(например: USD, EUR, CNY, RUB)."
            )

        amount_in_rub = amount * rates[src]["rate_rub"]
        return amount_in_rub / rates[dst]["rate_rub"]

    @staticmethod
    def parse_query(
        text: str,
        default_target: str = DEFAULT_BASE_CURRENCY,
    ) -> tuple[float, str, str]:
        """Разбирает запрос вида '100 USD RUB' или '100 USD' в кортеж."""
        cleaned = text.strip().upper().replace(",", ".")
        if not cleaned:
            raise ValidationError("Запрос не может быть пустым.")

        cleaned = re.sub(r"\s+(В|IN|TO|->)\s+", " ", cleaned)
        parts = cleaned.split()

        if len(parts) not in (2, 3):
            raise ValidationError(
                "Неверный формат запроса.\n"
                "Используйте вид: <code>100 USD RUB</code> "
                "или <code>100 USD</code>."
            )

        try:
            amount = float(parts[0])
        except ValueError as exc:
            raise ValidationError(
                f"Не удалось распознать число «{parts[0]}». "
                "Введите корректную сумму, например: <code>150.5 USD</code>."
            ) from exc

        if not math.isfinite(amount) or amount <= 0:
            raise ValidationError(
                "Сумма для конвертации должна быть больше нуля."
            )

        from_code = parts[1]
        to_code = parts[2] if len(parts) == 3 else default_target.upper()

        if not from_code.isalpha() or len(from_code) != 3:
            raise ValidationError(
                f"Некорректный код исходной валюты: «{from_code}»."
            )
        if not to_code.isalpha() or len(to_code) != 3:
            raise ValidationError(
                f"Некорректный код целевой валюты: «{to_code}»."
            )

        return amount, from_code, to_code

    def format_rates_message(
        self,
        base_code: str,
        date_str: str,
        rates: dict[str, dict[str, Any]],
    ) -> str:
        """Формирует текстовую сводку текущих курсов к базовой валюте."""
        base = base_code.upper()
        if base not in rates:
            base = DEFAULT_BASE_CURRENCY

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
            unit_qty = 100 if code in ("KZT", "JPY", "RUB") else 1
            current_val = (item["rate_rub"] * unit_qty) / base_rub
            prev_val = (item["prev_rub"] * unit_qty) / base_prev
            delta = current_val - prev_val
            if abs(delta) < 0.0001:
                delta = 0.0
            arrow = "🔺" if delta > 0 else ("🔻" if delta < 0 else "▪️")
            lines.append(
                f"• <b>{unit_qty} {code}</b> ({item['name']}): "
                f"<code>{current_val:.4f} {base}</code> "
                f"{arrow} {delta:+.4f}"
            )

        lines.append("\nИсточник: данные ЦБ РФ.")
        return "\n".join(lines)


settings_storage = SettingsStorage()
currency_service = CurrencyService()


def get_user_id(obj: Message | CallbackQuery) -> int:
    """Безопасно извлекает числовой ID пользователя из события Telegram."""
    if obj.from_user is None:
        return 0
    return obj.from_user.id


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


def build_base_currency_keyboard(current_base: str) -> InlineKeyboardMarkup:
    """Создает inline-клавиатуру выбора базовой валюты пользователя."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []

    for label, code in BASE_CURRENCIES:
        mark = " ✅" if code == current_base else ""
        btn = InlineKeyboardButton(
            text=f"{label}{mark}",
            callback_data=f"set_base:{code}",
        )
        row.append(btn)
        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    user_id: int,
) -> None:
    """Выполняет разбор запроса, конвертацию и отправку результата."""
    base_currency = settings_storage.get_base_currency(user_id)
    amount, from_code, to_code = currency_service.parse_query(
        query_text,
        base_currency,
    )
    date_str, rates = await currency_service.fetch_rates()
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
    """Обработчик команды /start: приветствие и текущая базовая валюта."""
    await state.clear()
    user_name = message.from_user.first_name if message.from_user else "друг"
    base_code = settings_storage.get_base_currency(get_user_id(message))
    text = (
        f"Привет, <b>{user_name}</b>! 👋\n\n"
        "Я бот <b>@currency_2026bot</b> для отслеживания курсов валют.\n"
        f"Ваша текущая базовая валюта: <b>{base_code}</b>.\n\n"
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
        "• <code>/base</code> — выбор базовой валюты (сохраняется)\n"
        "• <code>/help</code> — показать эту справку\n\n"
        "Быстрый формат (целевая валюта берется из настроек, "
        "если не указана):\n"
        "<code>250 EUR</code>, <code>100 USD RUB</code>, "
        "<code>500 CNY в TRY</code>."
    )
    await message.answer(text, reply_markup=build_main_keyboard())


@router.message(Command("rates"))
@router.message(F.text == "📊 Текущий курс")
async def cmd_rates(message: Message, state: FSMContext) -> None:
    """Обработчик команды /rates: вывод текущих курсов валют."""
    await state.clear()
    user_id = get_user_id(message)
    base_code = settings_storage.get_base_currency(user_id)
    try:
        date_str, rates = await currency_service.fetch_rates()
        text = currency_service.format_rates_message(
            base_code,
            date_str,
            rates,
        )
        await message.answer(
            text,
            reply_markup=build_rates_inline_keyboard(),
        )
    except BotBaseError as exc:
        await message.answer(f"⚠️ {exc}")
    except TelegramAPIError as exc:
        logger.error("Ошибка Telegram API в /rates: %s", exc)
    except Exception as exc:
        logger.exception("Непредвиденная ошибка в /rates: %s", exc)
        await message.answer(
            "⚠️ Произошла непредвиденная ошибка при получении курсов."
        )


@router.callback_query(F.data == "refresh_rates")
async def cb_refresh_rates(callback: CallbackQuery) -> None:
    """Обновляет сообщение с курсами валют по нажатию inline-кнопки."""
    user_id = get_user_id(callback)
    base_code = settings_storage.get_base_currency(user_id)
    try:
        date_str, rates = await currency_service.fetch_rates()
        text = currency_service.format_rates_message(
            base_code,
            date_str,
            rates,
        )
        if callback.message:
            await callback.message.edit_text(
                text,
                reply_markup=build_rates_inline_keyboard(),
            )
        await callback.answer("Курсы обновлены!")
    except TelegramBadRequest:
        await callback.answer("Данные уже актуальны!")
    except BotBaseError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception as exc:
        logger.exception("Ошибка обновления курсов: %s", exc)
        await callback.answer(
            "Не удалось обновить курсы. Попробуйте позже.",
            show_alert=True,
        )


@router.message(Command("base"))
@router.message(Command("settings"))
@router.message(F.text == "⚙️ Базовая валюта")
async def cmd_base(
    message: Message,
    state: FSMContext,
    command: CommandObject | None = None,
) -> None:
    """Показывает меню выбора базовой валюты или сохраняет код из команды."""
    await state.clear()
    user_id = get_user_id(message)

    if command and command.args:
        candidate = command.args.strip().upper()
        try:
            _, rates = await currency_service.fetch_rates()
            if candidate not in rates:
                raise ValidationError(
                    f"Валюта «{candidate}» отсутствует в списке ЦБ РФ."
                )
            settings_storage.set_base_currency(user_id, candidate)
            await message.answer(
                f"✅ Базовая валюта изменена на <b>{candidate}</b>.",
                reply_markup=build_main_keyboard(),
            )
        except BotBaseError as exc:
            await message.answer(f"⚠️ {exc}")
        return

    current_base = settings_storage.get_base_currency(user_id)
    await message.answer(
        f"⚙️ <b>Настройка базовой валюты</b>\n\n"
        f"Сейчас выбрана: <b>{current_base}</b>.\n"
        "Выберите новую базовую валюту кнопкой ниже или командой "
        "<code>/base КОД</code>:",
        reply_markup=build_base_currency_keyboard(current_base),
    )


@router.callback_query(F.data.startswith("set_base:"))
async def cb_set_base(callback: CallbackQuery) -> None:
    """Сохраняет выбранную пользователем базовую валюту в JSON-файл."""
    if not callback.data:
        return
    new_base = callback.data.split(":", maxsplit=1)[1]
    user_id = get_user_id(callback)

    try:
        settings_storage.set_base_currency(user_id, new_base)
        if callback.message:
            await callback.message.edit_text(
                f"⚙️ Базовая валюта сохранена: <b>{new_base}</b> ✅",
                reply_markup=build_base_currency_keyboard(new_base),
            )
        await callback.answer(f"Базовая валюта: {new_base}")
    except TelegramBadRequest:
        await callback.answer(f"Валюта {new_base} уже выбрана.")
    except BotBaseError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception as exc:
        logger.exception("Ошибка сохранения настроек: %s", exc)
        await callback.answer(
            "Ошибка при сохранении настройки.",
            show_alert=True,
        )


@router.message(Command("convert"))
@router.message(F.text == "💱 Конвертер")
async def cmd_convert(
    message: Message,
    state: FSMContext,
    command: CommandObject | None = None,
) -> None:
    """Обработчик команды /convert и кнопки запуска конвертера."""
    user_id = get_user_id(message)
    if command and command.args:
        await state.clear()
        try:
            await perform_conversion(message, command.args, user_id)
        except BotBaseError as exc:
            await message.answer(f"⚠️ {exc}")
        except Exception as exc:
            logger.exception("Ошибка конвертации: %s", exc)
            await message.answer("⚠️ Не удалось выполнить конвертацию.")
        return

    base_code = settings_storage.get_base_currency(user_id)
    await state.set_state(ConvertStates.waiting_for_query)
    await message.answer(
        "Введите сумму и валюты для конвертации.\n"
        "Примеры: <code>100 USD RUB</code>, "
        f"<code>50 EUR</code> (переведется в {base_code}), "
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
        await message.answer(
            "⚠️ Пожалуйста, отправьте текстовый запрос, например: "
            "<code>100 USD RUB</code>."
        )
        return

    user_id = get_user_id(message)
    try:
        await perform_conversion(message, message.text, user_id)
        await state.clear()
    except ValidationError as exc:
        await message.answer(
            f"⚠️ {exc}\nПопробуйте еще раз или нажмите «❌ Отмена».",
            reply_markup=build_cancel_keyboard(),
        )
    except BotBaseError as exc:
        await state.clear()
        await message.answer(f"⚠️ {exc}")
    except Exception as exc:
        await state.clear()
        logger.exception("Ошибка в FSM-конвертере: %s", exc)
        await message.answer("⚠️ Произошла ошибка при обработке запроса.")


@router.message(F.text)
async def handle_text_message(message: Message) -> None:
    """Обрабатывает произвольные текстовые запросы на конвертацию."""
    if not message.text:
        return

    user_id = get_user_id(message)
    try:
        await perform_conversion(message, message.text, user_id)
    except BotBaseError as exc:
        await message.answer(
            f"⚠️ {exc}\n\n"
            "Воспользуйтесь кнопками меню или введите запрос вида "
            "<code>100 USD RUB</code>."
        )
    except Exception as exc:
        logger.exception("Ошибка обработки текстового сообщения: %s", exc)
        await message.answer("⚠️ Внутренняя ошибка при обработке запроса.")


@router.message()
async def handle_unsupported_message(message: Message) -> None:
    """Обработчик неподдерживаемых типов сообщений (стикеры, фото и др.)."""
    await message.answer(
        "⚠️ Я понимаю только текстовые сообщения и команды.\n"
        "Например: <code>100 USD RUB</code> или нажмите <code>/help</code>.",
        reply_markup=build_main_keyboard(),
    )


async def main() -> None:
    """Точка входа: проверка токена, инициализация бота и запуск polling."""
    if not BOT_TOKEN or not BOT_TOKEN.strip():
        logger.error(
            "Переменная BOT_TOKEN не задана! Укажите токен в файле .env."
        )
        return

    bot = Bot(
        token=BOT_TOKEN.strip(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)
    logger.info("Бот @currency_2026bot запущен...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
