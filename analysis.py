"""Анализ архивных данных курса USD/RUB за 2016-2026 годы (ЦБ РФ)."""

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Путь к исходному CSV-архиву и выходным графикам
BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "usd_rub_archive.csv"
DYNAMICS_IMG = BASE_DIR / "usd_rub_dynamics.png"
DISTRIBUTION_IMG = BASE_DIR / "usd_rub_distribution.png"


def load_and_clean_data(csv_path: Path = DATA_PATH) -> pd.DataFrame:
    """Загружает CSV-датасет, очищает данные и восстанавливает пропуски."""
    raw_df = pd.read_csv(csv_path, encoding="utf-8")
    print("=== 1. Первичный осмотр исходного датасета ===")
    print(f"Строк в исходном файле: {len(raw_df)}")
    print(f"Типы столбцов до очистки:\n{raw_df.dtypes}\n")

    df = raw_df.copy()

    # Преобразуем дату из строкового формата DD.MM.YYYY в datetime64
    df["Date"] = pd.to_datetime(
        df["Date"],
        format="%d.%m.%Y",
        errors="coerce",
    )

    # Заменяем русскую запятую на точку и приводим курс к типу float
    df["Rate"] = pd.to_numeric(
        df["Rate"].astype(str).str.replace(",", ".", regex=False),
        errors="coerce",
    )
    df["Nominal"] = pd.to_numeric(df["Nominal"], errors="coerce").fillna(1)
    df["Rate"] = df["Rate"] / df["Nominal"]

    # Удаляем некорректные строки и дубликаты по дате
    before_drop = len(df)
    df = df.dropna(subset=["Date", "Rate"])
    df = df.drop_duplicates(subset=["Date"]).sort_values("Date")
    print(f"Удалено некорректных/дублирующих строк: {before_drop - len(df)}")

    # Восстанавливаем непрерывный календарный ряд (выходные и праздники)
    full_dates = pd.date_range(
        start=df["Date"].min(),
        end=df["Date"].max(),
        freq="D",
    )
    df = df.set_index("Date").reindex(full_dates)
    df.index.name = "Date"

    # Отмечаем реальные торговые дни до заполнения выходных пропусков
    df["IsTradingDay"] = df["Rate"].notna()

    missing_days = int(df["Rate"].isna().sum())
    print(f"Обнаружено пропусков (выходные/праздничные дни): {missing_days}")

    # Заполняем пропуски последним известным курсом ЦБ РФ (forward fill)
    df["Rate"] = df["Rate"].ffill()
    df["Currency"] = df["Currency"].ffill().fillna("USD")
    df["Nominal"] = 1

    # Добавляем вспомогательные признаки для дальнейшего анализа
    df = df.reset_index()
    df["DayIndex"] = np.arange(len(df), dtype=int)
    df["MA_30"] = df["Rate"].rolling(window=30).mean()
    df["MA_180"] = df["Rate"].rolling(window=180).mean()
    trading_rates = df.loc[df["IsTradingDay"], "Rate"]
    df["DailyChangePct"] = trading_rates.pct_change() * 100.0

    print(
        f"Пропусков после очистки: {int(df['Rate'].isna().sum())}, "
        f"итого дней в выборке: {len(df)}\n"
    )
    return df


def print_summary_statistics(df: pd.DataFrame) -> dict[str, float]:
    """Вычисляет и выводит основные описательные статистики ряда USD/RUB."""
    rates = df["Rate"]
    min_row = df.loc[rates.idxmin()]
    max_row = df.loc[rates.idxmax()]

    stats = {
        "count": float(len(rates)),
        "mean": float(rates.mean()),
        "median": float(rates.median()),
        "std": float(rates.std()),
        "min": float(rates.min()),
        "max": float(rates.max()),
        "q25": float(rates.quantile(0.25)),
        "q75": float(rates.quantile(0.75)),
    }

    print("=== 2. Описательная статистика курса USD/RUB ===")
    print(f"Период анализа: {df['Date'].min():%d.%m.%Y} — "
          f"{df['Date'].max():%d.%m.%Y}")
    print(f"Средний курс: {stats['mean']:.2f} руб.")
    print(f"Медианный курс: {stats['median']:.2f} руб.")
    print(f"Стандартное отклонение: {stats['std']:.2f} руб.")
    print(f"Минимум: {stats['min']:.4f} руб. ({min_row['Date']:%d.%m.%Y})")
    print(f"Максимум: {stats['max']:.4f} руб. ({max_row['Date']:%d.%m.%Y})")
    print(f"Межквартильный размах (25% — 75%): "
          f"{stats['q25']:.2f} — {stats['q75']:.2f} руб.\n")
    return stats


def build_charts(df: pd.DataFrame) -> None:
    """Строит и сохраняет графики динамики и распределения курса USD/RUB."""
    plt.rcParams["axes.edgecolor"] = "#CBD5E1"
    plt.rcParams["axes.linewidth"] = 0.8

    # График 1: Линейный график динамики курса, скользящие средние и тренд
    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=150)
    ax.plot(
        df["Date"],
        df["Rate"],
        color="#2563EB",
        linewidth=1.1,
        alpha=0.85,
        label="Курс USD/RUB (ЦБ РФ)",
    )
    ax.plot(
        df["Date"],
        df["MA_180"],
        color="#F59E0B",
        linewidth=2.0,
        label="Скользящее среднее (180 дней)",
    )

    # Линия линейного тренда через NumPy polyfit
    coeffs = np.polyfit(df["DayIndex"], df["Rate"], deg=1)
    trend_values = np.polyval(coeffs, df["DayIndex"])
    ax.plot(
        df["Date"],
        trend_values,
        color="#DC2626",
        linestyle="--",
        linewidth=1.8,
        label=f"Линейный тренд (+{coeffs[0] * 365:.2f} руб./год)",
    )

    # Отметки глобального минимума и максимума
    idx_min = df["Rate"].idxmin()
    idx_max = df["Rate"].idxmax()
    ax.scatter(
        [df.loc[idx_min, "Date"], df.loc[idx_max, "Date"]],
        [df.loc[idx_min, "Rate"], df.loc[idx_max, "Rate"]],
        color=["#16A34A", "#991B1B"],
        zorder=5,
        s=45,
    )
    ax.annotate(
        f"Мин: {df.loc[idx_min, 'Rate']:.2f} ₽",
        xy=(df.loc[idx_min, "Date"], df.loc[idx_min, "Rate"]),
        xytext=(12, -16),
        textcoords="offset points",
        fontsize=9,
        color="#166534",
        fontweight="bold",
    )
    ax.annotate(
        f"Макс: {df.loc[idx_max, 'Rate']:.2f} ₽",
        xy=(df.loc[idx_max, "Date"], df.loc[idx_max, "Rate"]),
        xytext=(-65, 8),
        textcoords="offset points",
        fontsize=9,
        color="#991B1B",
        fontweight="bold",
    )

    ax.set_ylim(45, 128)
    ax.set_title(
        "Динамика официального курса USD/RUB (2016–2026 гг.)",
        fontsize=13,
        fontweight="bold",
        pad=12,
    )
    ax.set_xlabel("Дата", fontsize=11)
    ax.set_ylabel("Курс, руб. за 1 USD", fontsize=11)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend(loc="upper left", frameon=True)
    fig.tight_layout()
    fig.savefig(DYNAMICS_IMG)
    plt.close(fig)

    # График 2: Гистограмма распределения значений курса и дневных изменений
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.0), dpi=150)

    rates = df["Rate"].to_numpy()
    mean_val = float(np.mean(rates))
    median_val = float(np.median(rates))
    std_val = float(np.std(rates, ddof=1))

    axes[0].hist(
        rates,
        bins=30,
        density=True,
        color="#3B82F6",
        edgecolor="#FFFFFF",
        alpha=0.75,
        label="Эмпирическое распределение",
    )
    x_grid = np.linspace(rates.min(), rates.max(), 300)
    normal_pdf = (
        1.0
        / (std_val * np.sqrt(2.0 * np.pi))
        * np.exp(-0.5 * ((x_grid - mean_val) / std_val) ** 2)
    )
    axes[0].plot(
        x_grid,
        normal_pdf,
        color="#DC2626",
        linewidth=2.0,
        label="Теоретическая кривая Гаусса",
    )
    axes[0].axvline(
        mean_val,
        color="#111827",
        linestyle="--",
        linewidth=1.4,
        label=f"Среднее: {mean_val:.2f}",
    )
    axes[0].axvline(
        median_val,
        color="#16A34A",
        linestyle=":",
        linewidth=1.8,
        label=f"Медиана: {median_val:.2f}",
    )
    axes[0].set_title("Распределение уровня курса USD/RUB", fontsize=11)
    axes[0].set_xlabel("Курс, руб.", fontsize=10)
    axes[0].set_ylabel("Плотность вероятности", fontsize=10)
    axes[0].grid(True, linestyle="--", alpha=0.4)
    axes[0].legend(fontsize=8.5)

    daily_pct = df["DailyChangePct"].dropna().to_numpy()
    clipped_pct = daily_pct[(daily_pct >= -5.0) & (daily_pct <= 5.0)]
    axes[1].hist(
        clipped_pct,
        bins=40,
        color="#10B981",
        edgecolor="#FFFFFF",
        alpha=0.8,
    )
    axes[1].axvline(0.0, color="#DC2626", linestyle="--", linewidth=1.3)
    axes[1].set_title(
        "Дневные изменения в торговые дни (±5%)",
        fontsize=11,
    )
    axes[1].set_xlabel("Изменение за день, %", fontsize=10)
    axes[1].set_ylabel("Количество дней", fontsize=10)
    axes[1].grid(True, linestyle="--", alpha=0.4)

    fig.suptitle(
        "Статистическое распределение значений курса USD/RUB",
        fontsize=13,
        fontweight="bold",
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(DISTRIBUTION_IMG, bbox_inches="tight")
    plt.close(fig)
    print(f"Графики сохранены: {DYNAMICS_IMG.name}, {DISTRIBUTION_IMG.name}\n")


def main() -> None:
    """Основной сценарий подготовки данных и построения графиков."""
    df = load_and_clean_data()
    print_summary_statistics(df)
    build_charts(df)


if __name__ == "__main__":
    main()
