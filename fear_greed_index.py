import html
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


MIN_ARTICLES_FOR_CHART = int(os.getenv("MIN_ARTICLES_FOR_CHART", "20"))
CLUSTER_WEIGHT_CAP = float(os.getenv("CLUSTER_WEIGHT_CAP", "2.0"))
CRYPTO_MAX_SHARE = float(os.getenv("CRYPTO_MAX_SHARE", "0.10"))
INDEX_SMOOTHING_DAYS = int(os.getenv("INDEX_SMOOTHING_DAYS", "7"))
CRYPTO_SMOOTHING_DAYS = int(os.getenv("CRYPTO_SMOOTHING_DAYS", "2"))
CATEGORY_LABELS = {
    "economy": "General economy",
    "markets": "Markets",
    "crypto": "Crypto",
}
INDEX_FAMILY_LABELS = {
    "global": "Global",
    "markets": "Markets",
    "economy": "General economy",
    "crypto": "Crypto",
}
THEME_LABELS = {
    "finance": "Finance",
    "esg": "ESG",
    "ecology": "Ecology",
}
CURVE_WINDOWS = (1, 3, 7, 30)
CURVE_DESCRIPTIONS = {
    "global": "Overall sentiment across the monitored economy, markets and crypto news.",
    "economy": "General economy sentiment, smoothed over the last seven available values.",
    "markets": "Financial markets sentiment, smoothed over the last seven available values.",
    "crypto": "Crypto market sentiment, averaged with the previous available value.",
}
ASSET_TYPE_LABELS = {
    "macroeconomy": "Macroeconomy",
    "equities": "Equities",
    "fixed_income": "Fixed income",
    "crypto": "Crypto",
    "real_estate": "Real estate",
    "commodities": "Commodities",
    "forex": "Forex",
    "business": "Business",
}
COUNTRY_LABELS = {
    "US": "United States",
    "GB": "United Kingdom",
    "FR": "France",
    "DE": "Germany",
    "CN": "China",
    "JP": "Japan",
    "IN": "India",
    "CA": "Canada",
    "AU": "Australia",
    "EU": "European Union",
    "INT": "International",
}
FLAG_BY_COUNTRY = {
    "US": "🇺🇸", "GB": "🇬🇧", "FR": "🇫🇷", "DE": "🇩🇪", "CN": "🇨🇳",
    "JP": "🇯🇵", "IN": "🇮🇳", "CA": "🇨🇦", "AU": "🇦🇺", "EU": "🇪🇺", "INT": "🌐",
}
FEAR_GREED_ZONES = (
    (0, 20, "Extreme fear", "#e76f51", "rgba(231, 111, 81, 0.16)"),
    (20, 40, "Fear", "#f4a261", "rgba(244, 162, 97, 0.16)"),
    (40, 60, "Neutral", "#e9c46a", "rgba(233, 196, 106, 0.16)"),
    (60, 80, "Greed", "#90be6d", "rgba(144, 190, 109, 0.16)"),
    (80, 100, "Extreme greed", "#43aa8b", "rgba(67, 170, 139, 0.16)"),
)
MISSING_DATA_CUTOFF_DATE = "2025-09-07"

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover
    psycopg2 = None
    RealDictCursor = None


def get_database_url() -> str | None:
    return os.getenv("DATABASE_URL")


def get_database_connection():
    database_url = get_database_url()
    if database_url:
        if psycopg2 is None:
            raise RuntimeError("psycopg2-binary is required when DATABASE_URL is configured")
        return psycopg2.connect(database_url, sslmode="require")
    conn = sqlite3.connect("sentiments.db")
    conn.row_factory = sqlite3.Row
    return conn


def fetch_rows(query: str, params: tuple = (), *, fetch_one: bool = False):
    database_url = get_database_url()
    conn = get_database_connection()
    try:
        if database_url:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(query, params)
            return cursor.fetchone() if fetch_one else cursor.fetchall()
        cursor = conn.execute(query, params)
        return cursor.fetchone() if fetch_one else cursor.fetchall()
    finally:
        conn.close()


def compute_fear_greed_index(score: float) -> float:
    """Map a sentiment score in [-1, 1] to a 0-100 fear/greed index."""
    if not -1.0 <= score <= 1.0:
        raise ValueError("score must be between -1 and 1")
    return round((score + 1.0) * 50.0, 2)


def _cluster_rows(
    start_date: str,
    end_date: str,
    *,
    theme: str | None = None,
) -> dict[str, dict[str, list[dict[str, float | int | str]]]]:
    from veille_presse import initialize_database

    initialize_database()
    database_url = get_database_url()
    if database_url:
        query = """
            SELECT article_day, COALESCE(category, 'economy'),
                   COALESCE(cluster_id, url), AVG(score), COUNT(*),
                   LEAST(%s, 1.0 + 0.2 * (COUNT(*) - 1))
            FROM daily_articles
            WHERE score IS NOT NULL AND article_day BETWEEN %s AND %s
              AND (%s IS NULL OR COALESCE(theme, 'finance') = %s)
            GROUP BY article_day, category, cluster_id, url
            ORDER BY article_day, category, cluster_id, url
            """
        params = (CLUSTER_WEIGHT_CAP, start_date, end_date, theme, theme)
        with get_database_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
    else:
        query = """
            SELECT article_day, COALESCE(category, 'economy'),
                   COALESCE(cluster_id, url), AVG(score), COUNT(*),
                   MIN(?, 1.0 + 0.2 * (COUNT(*) - 1))
            FROM daily_articles
            WHERE score IS NOT NULL AND article_day BETWEEN ? AND ?
              AND (? IS NULL OR COALESCE(theme, 'finance') = ?)
            GROUP BY article_day, category, cluster_id, url
            ORDER BY article_day, category, cluster_id, url
            """
        params = (CLUSTER_WEIGHT_CAP, start_date, end_date, theme, theme)
        with get_database_connection() as connection:
            rows = connection.execute(query, params).fetchall()

    grouped = defaultdict(lambda: defaultdict(list))
    for article_day, category, cluster_key, score, article_count, cluster_weight in rows:
        grouped[article_day][category].append(
            {
                "cluster_key": cluster_key,
                "score": float(score),
                "article_count": int(article_count),
                "cluster_weight": float(cluster_weight),
            }
        )
    return grouped


def _select_global_clusters(categories: dict[str, list[dict[str, float | int | str]]]) -> list[dict[str, float | int | str]]:
    non_crypto = [
        cluster
        for category, clusters in categories.items()
        if category != "crypto"
        for cluster in clusters
    ]
    crypto = list(categories.get("crypto", []))
    if not non_crypto or not crypto:
        return non_crypto + crypto

    non_crypto_count = sum(int(cluster["article_count"]) for cluster in non_crypto)
    crypto_count = sum(int(cluster["article_count"]) for cluster in crypto)
    allowed_crypto = min(
        crypto_count,
        int(non_crypto_count * CRYPTO_MAX_SHARE / (1.0 - CRYPTO_MAX_SHARE)),
    )
    selected_crypto = []
    selected_count = 0
    for cluster in sorted(crypto, key=lambda item: str(item["cluster_key"])):
        cluster_count = int(cluster["article_count"])
        if selected_count + cluster_count <= allowed_crypto:
            selected_crypto.append(cluster)
            selected_count += cluster_count
    return non_crypto + selected_crypto


def _aggregate_clusters(clusters: list[dict[str, float | int | str]]) -> tuple[float | None, int]:
    if not clusters:
        return None, 0
    total_weight = sum(float(cluster["cluster_weight"]) for cluster in clusters)
    score = sum(
        float(cluster["score"]) * float(cluster["cluster_weight"])
        for cluster in clusters
    ) / total_weight
    return score, sum(int(cluster["article_count"]) for cluster in clusters)


def get_daily_sentiment_index(
    days: int = 7,
    category: str | None = None,
    carry_forward: bool = True,
    theme: str | None = None,
) -> list[dict[str, float | str | int | bool | None]]:
    """Return daily sentiment, optionally restricted to one article category or theme."""
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=max(days - 1, 0))
    grouped = _cluster_rows(start_date.isoformat(), end_date.isoformat(), theme=theme)

    data = []
    seen_days = set()
    for article_day, categories in grouped.items():
        clusters = (
            _select_global_clusters(categories)
            if category is None
            else categories.get(category, [])
        )
        mean_score, article_count = _aggregate_clusters(clusters)
        if mean_score is None:
            continue
        seen_days.add(article_day)
        data.append(
            {
                "date": article_day,
                "score": mean_score,
                "article_count": int(article_count),
                "fear_greed_index": compute_fear_greed_index(mean_score),
            }
        )

    for current_day in (
        start_date + timedelta(days=i)
        for i in range((end_date - start_date).days + 1)
    ):
        day_key = current_day.isoformat()
        if day_key not in seen_days:
            data.append(
                {
                    "date": day_key,
                    "score": None,
                    "article_count": 0,
                    "fear_greed_index": None,
                }
            )

    data.sort(key=lambda item: item["date"])

    previous_index = None
    for row in data:
        raw_index = row["fear_greed_index"]
        article_count = int(row["article_count"])
        if raw_index is None:
            if carry_forward and previous_index is not None:
                row["fear_greed_index"] = previous_index
                row["raw_fear_greed_index"] = previous_index
                row["index_carried_forward"] = True
            else:
                row["index_carried_forward"] = False
            continue

        row["raw_fear_greed_index"] = raw_index
        row["index_carried_forward"] = False

        previous_index = row["fear_greed_index"]

    return data


def get_weighted_fear_greed_index(days: int = 7) -> dict[str, float | int | str]:
    """Weight the sentiment by article volume so days with more news matter more."""
    daily_index = get_daily_sentiment_index(days)
    valid_days = [row for row in daily_index if row["score"] is not None]
    if not valid_days:
        return {"score": 0.0, "fear_greed_index": 50.0, "article_count": 0}

    total_weight = sum(int(row["article_count"]) for row in valid_days)
    weighted_score = sum(
        float(row["score"]) * int(row["article_count"]) for row in valid_days
    ) / total_weight
    return {
        "score": round(weighted_score, 6),
        "fear_greed_index": compute_fear_greed_index(weighted_score),
        "article_count": total_weight,
    }


def get_chart_series(days: int = 30, theme: str | None = None) -> list[tuple[str, float]]:
    """Return daily values, carrying the previous value through low-volume days."""
    rows = get_daily_sentiment_index(days, theme=theme)
    series = [
        (row["date"], float(row["fear_greed_index"]))
        for row in rows
        if row["fear_greed_index"] is not None
    ]
    return series


def _apply_rolling_smoothing(
    rows: list[dict[str, float | str | int | bool | None]],
    window: int = INDEX_SMOOTHING_DAYS,
) -> list[dict[str, float | str | int | bool | None]]:
    """Smooth an index, weighting each daily value by its article volume."""
    valid_rows: list[dict[str, float | str | int | bool | None]] = []
    for row in rows:
        raw_index = row.get("raw_fear_greed_index", row.get("fear_greed_index"))
        if raw_index is None:
            row["is_smoothed"] = False
            continue

        value = float(raw_index)
        row["raw_fear_greed_index"] = value
        valid_rows.append(row)
        recent_rows = valid_rows[-max(window, 1):]
        total_articles = sum(max(int(previous.get("article_count", 0)), 1) for previous in recent_rows)
        weighted_value = sum(
            float(previous.get("raw_fear_greed_index", previous.get("fear_greed_index")))
            * max(int(previous.get("article_count", 0)), 1)
            for previous in recent_rows
        ) / total_articles
        row["fear_greed_index"] = round(weighted_value, 2)
        row["smoothing_window"] = len(recent_rows)
        row["is_smoothed"] = len(recent_rows) > 1
    return rows


def get_category_indices(days: int = 30) -> dict[str, list[dict[str, float | str | int | bool | None]]]:
    """Return category indices with category-specific rolling smoothing."""
    history_days = days + max(INDEX_SMOOTHING_DAYS - 1, 0)
    end_date = datetime.now(timezone.utc).date()
    display_start = end_date - timedelta(days=max(days - 1, 0))
    return {
        category: [
            row
            for row in _apply_rolling_smoothing(
                get_daily_sentiment_index(
                    history_days,
                    category=category,
                    carry_forward=False,
                ),
                window=CRYPTO_SMOOTHING_DAYS if category == "crypto" else INDEX_SMOOTHING_DAYS,
            )
            if str(row["date"]) >= display_start.isoformat()
        ]
        for category in CATEGORY_LABELS
    }


def _build_windowed_curve(
    rows: list[dict[str, float | str | int | bool | None]],
    window: int,
) -> list[dict[str, float | str | int | bool | None]]:
    """Build a volume-weighted trailing average per calendar day."""
    curve = []
    for index, row in enumerate(rows):
        window_rows = [
            previous
            for previous in rows[max(0, index - window + 1): index + 1]
            if previous.get("fear_greed_index") is not None
        ]
        if not window_rows:
            continue
        total_articles = sum(
            max(int(previous.get("article_count", 0)), 1)
            for previous in window_rows
        )
        weighted_index = sum(
            float(previous.get("fear_greed_index"))
            * max(int(previous.get("article_count", 0)), 1)
            for previous in window_rows
        ) / total_articles
        curve.append(
            {
                "date": row["date"],
                "fear_greed_index": round(weighted_index, 2),
                "raw_fear_greed_index": row.get("raw_fear_greed_index", row.get("fear_greed_index")),
                "article_count": row.get("article_count", 0),
                "smoothing_window": len(window_rows),
                "index_carried_forward": bool(row.get("index_carried_forward", False)),
            }
        )
    return curve


def get_index_curves(
    days: int = 30,
    theme: str | None = None,
) -> dict[str, dict[int, list[dict[str, float | str | int | bool | None]]]]:
    """Return daily, 3-day, 7-day and 30-day curves for every index family."""
    history_days = days + max(CURVE_WINDOWS) - 1
    base_rows = {
        "global": get_daily_sentiment_index(history_days, carry_forward=True, theme=theme),
        **{
            category: get_daily_sentiment_index(
                history_days,
                category=category,
                carry_forward=False,
                theme=theme,
            )
            for category in CATEGORY_LABELS
        },
    }
    end_date = datetime.now(timezone.utc).date()
    display_start = end_date - timedelta(days=max(days - 1, 0))
    return {
        family: {
            window: [
                row
                for row in _build_windowed_curve(rows, window)
                if str(row["date"]) >= display_start.isoformat()
            ]
            for window in CURVE_WINDOWS
        }
        for family, rows in base_rows.items()
    }


def get_advanced_fear_greed_index(
    scores: list[float] | None = None,
    days: int = 7,
) -> dict[str, float | int | str]:
    """More market-style sentiment index with weighted score + historical smoothing + status label."""
    if scores is None:
        daily_rows = get_daily_sentiment_index(days)
        valid_rows = [row for row in daily_rows if row["score"] is not None]
    else:
        valid_rows = [{"score": float(score), "article_count": 1} for score in scores]

    if not valid_rows:
        return {"score": 0.0, "fear_greed_index": 50.0, "status": "Neutral"}

    total_articles = sum(max(int(row.get("article_count", 0)), 1) for row in valid_rows)
    weighted_score = sum(
        float(row["score"]) * max(int(row.get("article_count", 0)), 1)
        for row in valid_rows
    ) / total_articles
    index_value = compute_fear_greed_index(weighted_score)

    if index_value >= 80:
        status = "Extreme greed"
    elif index_value >= 60:
        status = "Greed"
    elif index_value >= 40:
        status = "Neutral"
    elif index_value >= 20:
        status = "Fear"
    else:
        status = "Extreme fear"

    return {
        "score": round(weighted_score, 6),
        "fear_greed_index": round(index_value, 2),
        "status": status,
    }


def generate_fear_greed_chart(output_path: str = "fear_greed_chart.png", days: int = 7) -> str:
    """Create a Fear & Greed style chart for the last N days and save it as PNG."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ModuleNotFoundError as exc:
        raise RuntimeError("matplotlib is required to generate the chart") from exc

    series = get_chart_series(days)
    labels = [date for date, _ in series]
    values = [value for _, value in series]

    fig, ax = plt.subplots(figsize=(16, 8), dpi=150)
    fig.patch.set_facecolor("#f3f3f3")
    ax.set_facecolor("#f3f3f3")

    for lower, upper, label, color, _ in FEAR_GREED_ZONES:
        ax.axhspan(lower, upper, color=color, alpha=0.16, zorder=0)
        ax.text(
            1.005,
            (lower + upper) / 2,
            label,
            transform=ax.get_yaxis_transform(),
            va="center",
            fontsize=10,
            color="#4a4a4a",
        )

    for y in [0, 20, 40, 60, 80, 100]:
        ax.axhline(y, color="#666666", linestyle=":", linewidth=1, alpha=0.6)

    missing_start_index = next(
        (index for index, label in enumerate(labels) if label >= MISSING_DATA_CUTOFF_DATE),
        len(labels),
    )
    if missing_start_index > 0 and labels:
        ax.axvspan(0, missing_start_index, color="#a3a3a3", alpha=0.22, zorder=1)
        ax.text(
            missing_start_index / 2,
            96,
            "Missing data",
            ha="center",
            va="top",
            fontsize=10,
            color="#5b5b5b",
            bbox=dict(boxstyle="round", facecolor="#f5f5f5", edgecolor="#cfcfcf", alpha=0.8),
        )

    ax.plot(
        labels,
        values,
        color="#1f6f8b",
        linewidth=3,
        marker="o",
        markersize=4,
        markerfacecolor="#ffffff",
        markeredgewidth=2,
        markeredgecolor="#1f6f8b",
        zorder=3,
    )
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.set_yticklabels(["0", "20", "40", "60", "80", "100"])
    ax.set_title("Fear & Greed Index", fontsize=28, weight="bold", pad=20)
    ax.text(
        0.01,
        1.02,
        "Extreme fear  0–20   |   Fear  20–40   |   Neutral  40–60   |   Greed  60–80   |   Extreme greed  80–100",
        transform=ax.transAxes,
        fontsize=10,
        color="#4a4a4a",
        va="bottom",
    )
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.grid(False)
    ax.tick_params(axis="x", rotation=45, labelsize=12)
    ax.tick_params(axis="y", labelsize=12)
    ax.margins(x=0.02)
    fig.subplots_adjust(right=0.86, top=0.88, bottom=0.22)
    for spine in ax.spines.values():
        spine.set_visible(False)

    weighted = get_weighted_fear_greed_index(days)
    ax.text(
        0.01,
        0.01,
        f"Weighted index: {weighted['fear_greed_index']:.2f} | Articles: {weighted['article_count']}",
        transform=ax.transAxes,
        fontsize=10,
        color="#333333",
        bbox=dict(boxstyle="round", facecolor="#ffffff", alpha=0.75, edgecolor="#cccccc"),
    )

    plt.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return output_path


def generate_interactive_fear_greed_chart(
    output_path: str = "fear_greed_chart.html",
    days: int = 30,
    theme: str = "finance",
) -> str:
    """Create a self-contained HTML chart with interactive date/index tooltips."""
    try:
        import plotly.graph_objects as go
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "plotly est requis pour le graphique interactif. "
            "Installe-le avec : python3 -m pip install plotly"
        ) from exc

    selected_theme = (theme or "finance").lower()
    theme_label = THEME_LABELS.get(selected_theme, "Finance")
    chart_titles = {
        "finance": "Fear & Greed Index",
        "esg": "ESG Sentiment Index",
        "ecology": "Ecology Sentiment Index",
    }
    chart_title = chart_titles.get(selected_theme, chart_titles["finance"])
    zone_labels = {
        "finance": ("Extreme fear", "Fear", "Neutral", "Greed", "Extreme greed"),
        "esg": ("Very concerning", "Concerning", "Mixed", "Positive", "Very positive"),
        "ecology": ("Very concerning", "Concerning", "Mixed", "Positive", "Very positive"),
    }.get(selected_theme, ("Extreme fear", "Fear", "Neutral", "Greed", "Extreme greed"))
    sentiment_zones = tuple(
        (lower, upper, zone_labels[index], color, fill)
        for index, (lower, upper, _, color, fill) in enumerate(FEAR_GREED_ZONES)
    )
    available_days = max(days, 90)
    curves = get_index_curves(available_days, theme=selected_theme)
    labels = [str(row["date"]) for row in curves["global"][1]]
    generated_at = datetime.now(timezone.utc)
    database_url = get_database_url()
    if database_url:
        with get_database_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT MAX(analyzed_at) FROM daily_articles WHERE analyzed_at IS NOT NULL"
            )
            last_data_update = cursor.fetchone()[0]
    else:
        with sqlite3.connect("sentiments.db") as connection:
            last_data_update = connection.execute(
                "SELECT MAX(analyzed_at) FROM daily_articles WHERE analyzed_at IS NOT NULL"
            ).fetchone()[0]
    if last_data_update:
        updated_at = datetime.fromisoformat(last_data_update).astimezone(timezone.utc)
    else:
        updated_at = generated_at
    updated_at_label = updated_at.strftime("%d %B %Y at %H:%M UTC")
    current_day = datetime.now(timezone.utc).date().isoformat()

    if not labels:
        axis_tick_labels = [current_day]
        axis_tick_text = ["No data available yet"]
        chart_end_day = current_day
        daily_article_counts = {}
        axis_article_counts = {}
    else:
        chart_end_day = (
            (datetime.fromisoformat(labels[-1]).date() + timedelta(days=1)).isoformat()
        )
        axis_tick_labels = labels[-min(days, len(labels)):]
        if days > 7:
            first_day = datetime.fromisoformat(axis_tick_labels[0]).date()
            last_day = datetime.fromisoformat(axis_tick_labels[-1]).date()
            first_monday = first_day + timedelta(days=(7 - first_day.weekday()) % 7)
            axis_tick_labels = [
                (first_monday + timedelta(days=7 * index)).isoformat()
                for index in range(((last_day - first_monday).days // 7) + 1)
            ]
            if current_day <= last_day.isoformat() and current_day not in axis_tick_labels:
                axis_tick_labels.append(current_day)
        daily_article_counts = {
            str(row["date"]): int(row.get("article_count", 0))
            for row in curves["global"][1]
        }
        axis_article_counts = daily_article_counts.copy()
        if days > 7:
            axis_article_counts = {
                monday: sum(
                    count
                    for day, count in axis_article_counts.items()
                    if monday <= day <= (
                        datetime.fromisoformat(monday).date() + timedelta(days=6)
                    ).isoformat()
                )
                for monday in axis_tick_labels
            }
        axis_tick_text = [
            (
                f"{datetime.fromisoformat(day).strftime('%b %-d')}"
                if day == current_day
                else f"{datetime.fromisoformat(day).strftime('%b %-d')}<br>{axis_article_counts.get(day, 0)} article(s)"
            )
            for day in axis_tick_labels
        ]

    figure = go.Figure()
    for lower, upper, label, _, color in sentiment_zones:
        figure.add_hrect(
            y0=lower,
            y1=upper,
            fillcolor=color,
            line_width=0,
        )

    if labels:
        cutoff_start = min(labels)
        cutoff_end = MISSING_DATA_CUTOFF_DATE
        if cutoff_start < cutoff_end:
            figure.add_vrect(
                x0=cutoff_start,
                x1=cutoff_end,
                fillcolor="rgba(120, 120, 120, 0.25)",
                line_width=0,
                layer="below",
                annotation_text="Missing data",
                annotation_position="top left",
                annotation_font={"size": 11, "color": "#5b5b5b"},
            )
    else:
        figure.add_annotation(
            text="No data available yet",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font={"size": 18, "color": "#5b5b5b"},
        )

    curve_colors = {"global": "#1f6f8b", "economy": "#264653", "markets": "#e76f51", "crypto": "#8a5a44"}
    chart_families = tuple(INDEX_FAMILY_LABELS) if selected_theme == "finance" else ("global",)
    default_windows = {"global": 7 if selected_theme in {"esg", "ecology"} else 3, "markets": 7, "economy": 7, "crypto": 3}
    curve_descriptions = []
    for family in chart_families:
        family_label = INDEX_FAMILY_LABELS[family]
        for window in CURVE_WINDOWS:
            curve_rows = curves[family][window]
            window_label = "Daily" if window == 1 else f"{window}-day average"
            visible = window == default_windows[family]
            figure.add_trace(
                go.Scatter(
                    x=[str(row["date"]) for row in curve_rows],
                    y=[float(row["fear_greed_index"]) for row in curve_rows],
                    mode="lines+markers",
                    name=f"{family_label} — {window_label}",
                    visible=visible,
                    showlegend=window == 1,
                    line={
                        "color": curve_colors[family],
                        "width": 3 if window == 1 else 2,
                        "shape": "spline",
                        "smoothing": 0.65,
                    },
                    meta={"family": family, "window": window},
                    marker={"size": 8 if window == 1 else 6},
                    customdata=[
                        [row.get("article_count", 0), row.get("smoothing_window", 1)]
                        for row in curve_rows
                    ],
                    hovertemplate=(
                        f"<b>{family_label} — {window_label}</b><br>Date: %{{x}}"
                        "<br>Index: %{y:.2f}<br>Articles: %{customdata[0]}"
                        "<br>Values averaged: %{customdata[1]}<extra></extra>"
                    ),
                )
            )
            if window == 1:
                curve_descriptions.append(CURVE_DESCRIPTIONS.get(family, ""))
    figure.update_layout(
        title=chart_title,
        template="plotly_white",
        height=700,
        hovermode="closest",
        margin={"l": 60, "r": 260, "t": 90, "b": 155},
        xaxis={
            "title": "Date",
            "tickangle": 0,
            "tickmode": "array",
            "tickvals": axis_tick_labels,
            "ticktext": axis_tick_text,
            "range": [labels[max(0, len(labels) - days)], chart_end_day] if labels else None,
        },
        yaxis={"title": "Index", "range": [0, 100], "dtick": 20},
        showlegend=False,
        annotations=[
            *[
                {
                    "x": 0.01,
                    "y": (lower + upper) / 2,
                    "xref": "paper",
                    "yref": "y",
                    "text": label,
                    "showarrow": False,
                    "font": {"size": 11, "color": color},
                    "xanchor": "left",
                    "yanchor": "middle",
                }
                for lower, upper, label, color, _ in sentiment_zones
            ],
        ],
    )
    database_url = get_database_url()
    if database_url:
        with get_database_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                     SELECT article_day, title, title_fr, title_en, source, score, url, cluster_size,
                         category, country, asset_type, theme
                FROM daily_articles
                WHERE article_day BETWEEN %s AND %s
                ORDER BY article_day DESC, published DESC, title ASC
                """,
                (labels[0] if labels else "", labels[-1] if labels else ""),
            )
            article_rows = cursor.fetchall()
    else:
        with sqlite3.connect("sentiments.db") as connection:
            article_rows = connection.execute(
                """
                     SELECT article_day, title, title_fr, title_en, source, score, url, cluster_size,
                         category, country, asset_type, theme
                FROM daily_articles
                WHERE article_day BETWEEN ? AND ?
                ORDER BY article_day DESC, published DESC, title ASC
                """,
                (labels[0] if labels else "", labels[-1] if labels else ""),
            ).fetchall()

    articles_by_day = {}
    for article_day, title, title_fr, title_en, source, score, url, cluster_size, category, country, asset_type, theme in article_rows:
        articles_by_day.setdefault(article_day, []).append(
            {
                "title": title,
                "title_fr": title_fr or title,
                "title_en": title_en or title,
                "source": source,
                "score": score,
                "url": url,
                "cluster_size": cluster_size or 1,
                "category": CATEGORY_LABELS.get(category, "General economy"),
                "category_code": category or "economy",
                "country": COUNTRY_LABELS.get(country, "International"),
                "country_code": country or "INT",
                "asset_type": ASSET_TYPE_LABELS.get(asset_type, "Macroeconomy"),
                "asset_type_code": asset_type or "macroeconomy",
                "theme": THEME_LABELS.get(theme, "Finance"),
                "theme_code": theme or "finance",
            }
        )

    plot_html = figure.to_html(
        include_plotlyjs=True,
        full_html=False,
        config={
            "displaylogo": False,
            "responsive": True,
            "modeBarButtonsToRemove": ["zoom2d", "autoscale", "resetScale2d"],
        },
    )
    article_data = json.dumps(articles_by_day, ensure_ascii=False).replace("<", "\\u003c")
    date_options = "".join(
        f'<option value="{html.escape(day)}">{html.escape(day)}</option>'
        for day in sorted(articles_by_day, reverse=True)
    )
    all_articles = [article for day_articles in articles_by_day.values() for article in day_articles]
    country_options = "".join(
        f'<option value="{html.escape(code)}">{html.escape(COUNTRY_LABELS.get(code, code))}</option>'
        for code in sorted({article["country_code"] for article in all_articles})
    )
    asset_options = "".join(
        f'<option value="{html.escape(code)}">{html.escape(ASSET_TYPE_LABELS.get(code, code))}</option>'
        for code in sorted({article["asset_type_code"] for article in all_articles})
    )
    category_options = "".join(
        f'<option value="{html.escape(code)}">{html.escape(CATEGORY_LABELS.get(code, code))}</option>'
        for code in sorted({article["category_code"] for article in all_articles})
    )
    theme_options = "".join(
        f'<option value="{html.escape(code)}">{html.escape(THEME_LABELS.get(code, code))}</option>'
        for code in sorted({article["theme_code"] for article in all_articles})
    )
    theme_buttons = "".join(
        f'<button class="theme-button" type="button" data-theme="{html.escape(code)}" aria-pressed="{"true" if code == selected_theme else "false"}">{html.escape(THEME_LABELS.get(code, code.title()))}</button>'
        for code in ("finance", "esg", "ecology")
    )
    curve_controls = "".join(
        f'''<div class="curve-control">
            <button class="curve-family-button" type="button" data-family="{html.escape(code)}" aria-expanded="false">
                <span class="curve-control-name"><span class="curve-color" style="background:{curve_colors[code]}"></span>{html.escape(label)}</span>
                <span class="curve-chevron" aria-hidden="true">+</span>
            </button>
            <div class="curve-options" data-family-options="{html.escape(code)}" hidden>
                {''.join(f'<button class="curve-option" type="button" data-family="{html.escape(code)}" data-window="{window}" aria-pressed="{"true" if window == default_windows[code] else "false"}">{window_label}</button>' for window, window_label in ((1, "Daily"), (3, "3 days"), (7, "7 days"), (30, "30 days")))}
            </div>
        </div>'''
        for code, label in ((code, INDEX_FAMILY_LABELS[code]) for code in chart_families)
    )
    page = f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="theme" content="{html.escape(selected_theme)}">
    <title>Fear &amp; Greed Index — {html.escape(theme_label)}</title>
    <style>
        :root {{ --ink:#17212b; --muted:#63707c; --line:#dce4e8; --paper:#f6f8f7; --accent:#1f6f8b; }}
        * {{ box-sizing:border-box; }}
        body {{ margin:0; background:var(--paper); color:var(--ink); font-family:Georgia, 'Times New Roman', serif; }}
        .page {{ max-width:1440px; margin:0 auto; padding:20px 24px 40px; }}
        header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:20px; margin-bottom:12px; }}
        h1 {{ margin:0 0 8px; font-size:clamp(2rem, 3.6vw, 3.2rem); letter-spacing:0; }}
        .kicker {{ margin:0; color:var(--accent); font:700 .72rem/1.2 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; letter-spacing:.12em; text-transform:uppercase; }}
        .lede {{ max-width:700px; margin:0; color:var(--muted); font:1rem/1.5 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
        button, select {{ border:1px solid var(--line); background:white; color:var(--ink); border-radius:6px; padding:10px 14px; font:600 .9rem -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; cursor:pointer; }}
        button:hover, select:hover {{ border-color:var(--accent); }}
        .toolbar {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; }}
        .theme-switcher {{ display:flex; gap:8px; flex-wrap:wrap; margin:12px 0 16px; padding-left:4px; }}
        .chart-notes {{ display:grid; gap:4px; margin:10px 4px 0; color:var(--muted); font:.76rem/1.4 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
        .theme-button {{ padding:8px 14px; border-radius:999px; font-size:.78rem; border:1px solid var(--line); background:#f8fafb; }}
        .theme-button[aria-pressed="true"] {{ background:var(--accent); border-color:var(--accent); color:white; box-shadow:0 8px 18px rgba(31,111,139,0.16); }}
        .language-button {{ padding:9px 12px; }}
        .chart-actions {{ position:absolute; top:10px; right:10px; z-index:4; display:flex; gap:8px; align-items:center; }}
        .period-actions {{ display:flex; gap:4px; }}
        .period-button {{ padding:9px 10px; font-size:.78rem; }}
        .period-button[aria-pressed="true"] {{ background:var(--accent); border-color:var(--accent); color:white; }}
        .curve-legend {{ position:absolute; top:92px; right:16px; z-index:2; display:grid; gap:6px; width:216px; padding:10px; border:1px solid var(--line); border-radius:6px; background:rgba(251,252,252,.96); font:600 .86rem -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
        .curve-actions {{ display:grid; grid-template-columns:1fr 1fr; gap:5px; padding-bottom:4px; border-bottom:1px solid var(--line); }}
        .curve-action {{ padding:5px 3px; font-size:.68rem; white-space:nowrap; }}
        .curve-control {{ display:grid; gap:5px; color:var(--muted); }}
        .curve-family-button {{ display:flex; align-items:center; justify-content:space-between; width:100%; padding:6px 4px; border:0; background:transparent; color:var(--ink); text-align:left; }}
        .curve-family-button:hover {{ background:#eef5f3; }}
        .curve-family-button[aria-expanded="true"] {{ color:var(--accent); }}
        .curve-chevron {{ font-size:1.1rem; font-weight:400; }}
        .curve-control-name {{ display:flex; align-items:center; gap:7px; min-width:0; color:var(--ink); white-space:nowrap; }}
        .curve-color {{ width:10px; height:10px; border-radius:50%; display:inline-block; }}
        .curve-options {{ display:grid; grid-template-columns:repeat(2, 1fr); gap:4px; padding:0 0 4px 17px; }}
        .curve-options[hidden] {{ display:none; }}
        .curve-option {{ padding:5px 4px; border:1px solid var(--line); font-size:.72rem; }}
        .curve-option[aria-pressed="true"] {{ background:var(--accent); border-color:var(--accent); color:white; }}
        .panel {{ background:white; border:1px solid var(--line); border-radius:8px; box-shadow:0 8px 24px rgba(23,33,43,.06); }}
        .chart-panel {{ position:relative; padding:8px 10px 0; }}
        .chart-panel:fullscreen {{ width:100vw; height:100vh; padding:24px; background:white; }}
        .chart-panel:fullscreen .plotly-graph-div {{ height:calc(100vh - 48px) !important; }}
        .explain {{ display:grid; grid-template-columns:1.2fr 1fr; gap:24px; margin:24px 0; padding:24px; }}
        .explain h2, .articles h2 {{ margin:0 0 10px; font-size:1.35rem; }}
        .explain p {{ margin:0; color:var(--muted); font:1rem/1.6 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
        .formula {{ padding:16px; background:#eef5f3; border-left:4px solid var(--accent); font:600 1rem/1.5 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
        .articles {{ margin-top:24px; padding:24px; }}
        .article-header {{ display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:16px; }}
        #article-count {{ color:var(--muted); font: .9rem -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
        .article-list {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:12px; }}
        .article-more {{ display:block; margin:18px auto 0; }}
        .disclaimer {{ margin-top:24px; padding:18px 20px; color:var(--muted); border-top:1px solid var(--line); font: .84rem/1.55 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
        .credits {{ padding:0 20px 28px; color:var(--muted); text-align:center; font: .78rem/1.5 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
        .article {{ padding:16px; border:1px solid var(--line); border-radius:6px; background:#fbfcfc; }}
        .article a {{ color:var(--ink); text-decoration:none; font-weight:700; line-height:1.35; }}
        .article a:hover {{ color:var(--accent); }}
        .meta {{ margin-top:9px; color:var(--muted); font: .8rem/1.4 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
        .score {{ display:inline-block; margin-top:12px; color:var(--accent); font:700 .9rem -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
        .article-tags {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; font:600 .76rem -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color:var(--muted); }}
        .tag {{ padding:4px 7px; border:1px solid var(--line); border-radius:999px; background:white; }}
        .article-filters {{ display:grid; grid-template-columns:repeat(4, minmax(140px, 1fr)) auto; gap:10px; align-items:end; margin-bottom:18px; padding:14px; border:1px solid var(--line); border-radius:6px; background:#fbfcfc; }}
        .filter-field {{ display:grid; gap:5px; color:var(--muted); font:600 .76rem -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
        .filter-field select {{ width:100%; padding:8px 10px; font-size:.82rem; }}
        #clear-filters {{ padding:8px 12px; white-space:nowrap; }}
        .zone-item {{ display:inline-flex; align-items:center; gap:6px; }}
        .curve-tooltip {{ position:absolute; z-index:10; display:none; max-width:320px; padding:9px 11px; border:1px solid var(--line); border-radius:5px; background:var(--ink); color:white; box-shadow:0 6px 18px rgba(23,33,43,.18); font: .78rem/1.35 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; pointer-events:none; }}
        .zone-dot {{ width:10px; height:10px; border-radius:50%; display:inline-block; }}
        @media (max-width:900px) {{ .article-filters {{ grid-template-columns:repeat(2, minmax(140px, 1fr)); }} }}
        @media (max-width:700px) {{ .page {{ padding:20px 14px 40px; }} header, .article-header {{ display:block; }} .toolbar {{ margin-top:16px; }} .explain {{ grid-template-columns:1fr; padding:18px; }} .articles {{ padding:18px; }} .article-filters {{ grid-template-columns:1fr; }} .curve-legend {{ position:static; width:auto; grid-template-columns:repeat(2, minmax(0, 1fr)); margin:4px 0 8px; }} }}
    </style>
</head>
<body>
    <main class="page">
        <header>
            <div>
                <p id="page-kicker" class="kicker"></p>
                <h1 id="page-title">{html.escape(chart_title)}</h1>
                <p id="page-lede" class="lede">A daily reading of economic, market, ESG and ecology sentiment, built from internationally sourced articles analyzed automatically.</p>
            </div>
            <div class="toolbar"><button id="language-button" class="language-button" type="button">Français</button></div>
        </header>
        <div class="theme-switcher" aria-label="Theme selector">
            {theme_buttons}
        </div>
        <section id="chart-panel" class="panel chart-panel">
            <div class="chart-actions">
                <div class="period-actions" aria-label="Chart period">
                    <button class="period-button" type="button" data-period="7">7 days</button>
                    <button class="period-button" type="button" data-period="30">30 days</button>
                    <button class="period-button" type="button" data-period="90">90 days</button>
                </div>
                <button id="reset-view-button" type="button">Reset view</button>
                <button id="fullscreen-button" type="button">Fullscreen</button>
            </div>
            <div id="curve-tooltip" class="curve-tooltip" role="tooltip"></div>
            {plot_html}
            <div class="curve-legend" aria-label="Index curves">
                {f'''<div class="curve-actions">
                    <button id="default-curves-button" class="curve-action" type="button">Default curves</button>
                    <button id="clear-curves-button" class="curve-action" type="button">Clear all</button>
                </div>''' if selected_theme == "finance" else ""}
                {curve_controls}
            </div>
        </section>
        <div class="chart-notes">
            <span>Data last updated: {updated_at_label}</span>
            <span>For 30- and 90-day views, the count under each Monday is the total number of articles from Monday to Sunday.</span>
            <span>Low-volume days keep their raw value and receive less weight in moving averages</span>
        </div>
        <section class="panel explain">
            <div>
                <h2 id="calculation-title">How the index is calculated</h2>
                <p id="calculation-text">Each article receives a sentiment score between -1 and +1. Articles covering the same event are grouped into clusters. The global index limits crypto to 10% when non-crypto articles are available. Each daily index is mapped from the average article sentiment to a scale from 0 to 100, then can be displayed as the daily value or as a trailing average of the last 3, 7 or 30 available values. The default view uses 3 days for Global and Crypto, and 7 days for Markets and General economy.</p>
            </div>
            <div id="formula-text" class="formula"><strong>Index = (average score + 1) × 50</strong><br><br>The index ranges from 0 to 100. Values from 0 to 20 indicate extreme fear, 20 to 40 fear, 40 to 60 a neutral mood, 60 to 80 greed, and 80 to 100 extreme greed.</div>
        </section>
        <section class="panel articles">
            <div class="article-header">
                <div><h2 id="articles-title">Articles of the day</h2><span id="article-selection"></span><span id="article-count"></span></div>
            </div>
            <div class="article-filters" aria-label="Article filters">
                <label class="filter-field" for="day-select">Date<select id="day-select">{date_options}</select></label>
                <label class="filter-field" for="country-filter">Country<select id="country-filter"><option value="">All countries</option>{country_options}</select></label>
                <label class="filter-field" for="asset-filter">Asset type<select id="asset-filter"><option value="">All asset types</option>{asset_options}</select></label>
                <label class="filter-field" for="category-filter">Index family<select id="category-filter"><option value="">All index families</option>{category_options}</select></label>
                <label class="filter-field" for="theme-filter">Theme<select id="theme-filter"><option value="">All themes</option>{theme_options}</select></label>
                <button id="clear-filters" type="button">Clear filters</button>
            </div>
            <div id="article-list" class="article-list"></div>
            <button id="article-more" class="article-more" type="button" hidden>Show more</button>
        </section>
        <footer id="disclaimer" class="disclaimer">This dashboard is an academic study project. Its indicators and article classifications are experimental and should not be treated as financial advice or as a sole basis for making decisions. Always verify information independently and remain aware of the limitations of automated analysis.</footer>
        <div id="credits" class="credits"><strong>Project author:</strong> Victor Coppey · <strong>Academic supervisor:</strong> David Ardia · <strong>Institution:</strong> HEC Montréal · 2026</div>
    </main>
    <script>
        const articlesByDay = {article_data};
        const select = document.getElementById('day-select');
        const countryFilter = document.getElementById('country-filter');
        const assetFilter = document.getElementById('asset-filter');
        const categoryFilter = document.getElementById('category-filter');
        const themeFilter = document.getElementById('theme-filter');
        const list = document.getElementById('article-list');
        const count = document.getElementById('article-count');
        const articleSelection = document.getElementById('article-selection');
        const moreArticlesButton = document.getElementById('article-more');
        const updatedAtLabel = {json.dumps(updated_at_label, ensure_ascii=False)};
        const flags = {json.dumps(FLAG_BY_COUNTRY, ensure_ascii=False)};
        const graph = document.querySelector('.plotly-graph-div');
        const chartPanel = document.getElementById('chart-panel');
        const curveTooltip = document.getElementById('curve-tooltip');
        const themeButtons = [...document.querySelectorAll('.theme-button')];
        const familyButtons = [...document.querySelectorAll('.curve-family-button')];
        const curveOptions = [...document.querySelectorAll('.curve-option')];
        const defaultCurvesButton = document.getElementById('default-curves-button');
        const clearCurvesButton = document.getElementById('clear-curves-button');
        const periodButtons = [...document.querySelectorAll('.period-button')];
        const initialPeriod = {min(days, 90)};
        let selectedPeriod = [7, 30, 90].includes(initialPeriod) ? initialPeriod : 30;
        const curveDescriptions = {json.dumps(curve_descriptions, ensure_ascii=False)};
        const familyOrder = {json.dumps(list(chart_families), ensure_ascii=False)};
        const windowOrder = {json.dumps(list(CURVE_WINDOWS))};
        const familyLabels = {json.dumps(INDEX_FAMILY_LABELS, ensure_ascii=False)};
        const selectedTheme = {json.dumps(selected_theme, ensure_ascii=False)};
        const defaultWindows = {json.dumps(default_windows)};
        const currentDay = {json.dumps(current_day)};
        const chartDates = {json.dumps(labels)};
        const dailyArticleCounts = {json.dumps(daily_article_counts)};
        const axisArticleCounts = {json.dumps(axis_article_counts)};
        const selectedAverages = Object.fromEntries(familyOrder.map(family => [family, new Set([defaultWindows[family]])]));
        let language = 'en';
        const themeKickerLabels = {{
            en: {{finance: 'Market sentiment', esg: 'ESG sentiment', ecology: 'Ecology sentiment'}},
            fr: {{finance: 'Sentiment des marchés', esg: 'Sentiment ESG', ecology: 'Sentiment écologique'}},
        }};
        const translations = {{
            en: {{button: 'Français', kicker: 'Market sentiment / {days} days', lede: 'A daily reading of economic, market, ESG and ecology sentiment, built from internationally sourced articles analyzed automatically.', calculationTitle: 'How the index is calculated', calculationText: 'Each article receives a sentiment score between -1 and +1. Articles covering the same event are grouped into clusters. The global index limits crypto to 10% when non-crypto articles are available. Each daily index is mapped from the average article sentiment to a scale from 0 to 100, then can be displayed as the daily value or as a trailing average of the last 3, 7 or 30 available values. The default view uses 3 days for Global and Crypto, and 7 days for Markets and General economy.', formula: 'The index ranges from 0 to 100. Values from 0 to 20 indicate extreme fear, 20 to 40 fear, 40 to 60 a neutral mood, 60 to 80 greed, and 80 to 100 extreme greed.', articles: 'Articles of the day', date: 'Date', country: 'Country', asset: 'Asset type', family: 'Index family', theme: 'Theme', allCountries: 'All countries', allAssets: 'All asset types', allFamilies: 'All index families', allThemes: 'All themes', clear: 'Clear filters', more: 'Show more', less: 'Show less', shown: 'article(s) shown', noArticles: 'No articles for this date.', cluster: 'cluster of', score: 'Score', disclaimer: 'This dashboard is an academic study project. Its indicators and article classifications are experimental and should not be treated as financial advice or as a sole basis for making decisions. Always verify information independently and remain aware of the limitations of automated analysis.'}},
            fr: {{button: 'English', kicker: 'Sentiment des marchés / {days} jours', lede: 'Une lecture quotidienne du sentiment économique, financier, ESG et écologique, construite à partir d’articles internationaux analysés automatiquement.', calculationTitle: 'Comment l’indice est calculé', calculationText: 'Chaque article reçoit un score de sentiment compris entre -1 et +1. Les articles couvrant le même événement sont regroupés en clusters. L’indice global limite la crypto à 10 % lorsque des articles non crypto sont disponibles. Chaque indice quotidien est converti sur une échelle de 0 à 100, puis peut être affiché au jour le jour ou sous forme de moyenne glissante des 3, 7 ou 30 dernières valeurs disponibles. Par défaut, la vue utilise 3 jours pour Global et Crypto, et 7 jours pour Markets et General economy.', formula: 'L’indice va de 0 à 100. De 0 à 20 : peur extrême, de 20 à 40 : peur, de 40 à 60 : sentiment neutre, de 60 à 80 : avidité, et de 80 à 100 : avidité extrême.', articles: 'Articles du jour', date: 'Date', country: 'Pays', asset: 'Type d’actif', family: 'Famille d’indice', theme: 'Thème', allCountries: 'Tous les pays', allAssets: 'Tous les types d’actifs', allFamilies: 'Toutes les familles', allThemes: 'Tous les thèmes', clear: 'Effacer les filtres', more: 'Voir plus', less: 'Voir moins', shown: 'article(s) affiché(s)', noArticles: 'Aucun article pour cette date.', cluster: 'cluster de', score: 'Score', disclaimer: 'Ce dashboard est un projet d’étude académique. Ses indicateurs et classifications d’articles sont expérimentaux et ne constituent pas un conseil financier. Vérifiez toujours les informations de manière indépendante et gardez à l’esprit les limites de l’analyse automatisée.'}}
        }};
        const t = key => translations[language][key];
        function updateKicker() {{
            document.getElementById('page-kicker').textContent = `${{themeKickerLabels[language][selectedTheme]}} / ${{selectedPeriod}} ${{language === 'fr' ? 'jours' : 'days'}}`;
        }}
        function updateDateAxis(dates) {{
            let visibleDates = chartDates.slice(-selectedPeriod);
            if (selectedPeriod > 7) {{
                const firstDate = new Date(`${{visibleDates[0]}}T00:00:00Z`);
                const lastDate = new Date(`${{visibleDates[visibleDates.length - 1]}}T00:00:00Z`);
                const daysUntilMonday = (8 - firstDate.getUTCDay()) % 7;
                firstDate.setUTCDate(firstDate.getUTCDate() + daysUntilMonday);
                visibleDates = [];
                const weeklyCounts = {{}};
                for (const cursor = new Date(firstDate); cursor <= lastDate; cursor.setUTCDate(cursor.getUTCDate() + 7)) {{
                    const monday = cursor.toISOString().slice(0, 10);
                    let weeklyCount = 0;
                    for (const day of dates) {{
                        const dayDate = new Date(`${{day}}T00:00:00Z`);
                        const weekEnd = new Date(cursor);
                        weekEnd.setUTCDate(weekEnd.getUTCDate() + 6);
                        if (dayDate >= cursor && dayDate <= weekEnd) weeklyCount += dailyArticleCounts[day] ?? 0;
                    }}
                    visibleDates.push(monday);
                    weeklyCounts[monday] = weeklyCount;
                }}
                if (currentDay >= visibleDates[0] && currentDay <= dates[dates.length - 1] && !visibleDates.includes(currentDay)) {{
                    visibleDates.push(currentDay);
                }}
                Object.assign(axisArticleCounts, weeklyCounts);
            }}
            const suffix = language === 'fr' ? ' article(s)' : ' article(s)';
            const tickText = visibleDates.map(day => {{
                const dateLabel = new Date(`${{day}}T12:00:00Z`).toLocaleDateString(
                    language === 'fr' ? 'fr-FR' : 'en-US',
                    {{month: 'short', day: 'numeric'}}
                );
                if (day === currentDay) return dateLabel;
                const count = selectedPeriod <= 7 ? dailyArticleCounts[day] ?? 0 : axisArticleCounts[day] ?? 0;
                return `${{dateLabel}}<br>${{count}}${{suffix}}`;
            }});
            Plotly.relayout(graph, {{
                'xaxis.tickmode': 'array',
                'xaxis.tickvals': visibleDates,
                'xaxis.ticktext': tickText,
                'xaxis.tickangle': 0,
            }});
        }}
        const articleLabels = {{
            en: {{category: {{economy: 'General economy', markets: 'Markets', crypto: 'Crypto'}}, asset: {{macroeconomy: 'Macroeconomy', equities: 'Equities', fixed_income: 'Fixed income', crypto: 'Crypto', real_estate: 'Real estate', commodities: 'Commodities', forex: 'Forex', business: 'Business'}}, country: {{}}}},
            fr: {{category: {{economy: 'Économie générale', markets: 'Marchés', crypto: 'Crypto'}}, asset: {{macroeconomy: 'Macroéconomie', equities: 'Actions', fixed_income: 'Obligations', crypto: 'Crypto', real_estate: 'Immobilier', commodities: 'Matières premières', forex: 'Devises', business: 'Entreprises'}}, country: {{US: 'États-Unis', GB: 'Royaume-Uni', FR: 'France', DE: 'Allemagne', CN: 'Chine', JP: 'Japon', IN: 'Inde', CA: 'Canada', AU: 'Australie', EU: 'Union européenne', INT: 'International'}}}}
        }};
        let showAllArticles = false;
        const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({{'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}}[char]));
        function renderArticles() {{
            const day = select.value;
            const filters = {{
                country_code: countryFilter.value,
                asset_type_code: assetFilter.value,
                category_code: categoryFilter.value,
                theme_code: themeFilter.value,
            }};
            const articles = (articlesByDay[day] || []).filter(article =>
                Object.entries(filters).every(([key, value]) => !value || article[key] === value)
            );
            const displayedArticles = showAllArticles ? articles : articles.slice(0, 8);
            count.textContent = `${{displayedArticles.length}} / ${{articles.length}} ${{t('shown')}}`;
            list.innerHTML = displayedArticles.length ? displayedArticles.map(article => `
                <article class="article">
                    <a class="article-title" href="${{escapeHtml(article.url)}}" target="_blank" rel="noopener">${{escapeHtml(language === 'fr' ? article.title_fr : article.title_en)}}</a>
                    <div class="article-tags">
                        <span class="tag" title="Country">${{flags[article.country_code] || flags.INT}} ${{escapeHtml(articleLabels[language].country[article.country_code] || article.country)}}</span>
                        <span class="tag" title="Asset type">${{escapeHtml(articleLabels[language].asset[article.asset_type_code] || article.asset_type)}}</span>
                    </div>
                    <div class="meta">${{escapeHtml(articleLabels[language].category[article.category_code] || article.category)}} · ${{escapeHtml(article.source)}} · ${{t('cluster')}} ${{article.cluster_size}} article(s)</div>
                    <span class="score">${{t('score')}}: ${{Number(article.score).toFixed(2)}}</span>
                </article>`).join('') : `<p>${{t('noArticles')}}</p>`;
                moreArticlesButton.hidden = articles.length <= 8;
            moreArticlesButton.textContent = showAllArticles ? t('less') : t('more');
        }}
        function applyLanguage() {{
            document.documentElement.lang = language;
            document.getElementById('language-button').textContent = t('button');
            updateKicker();
            document.getElementById('page-lede').textContent = t('lede');
            document.getElementById('calculation-title').textContent = t('calculationTitle');
            document.getElementById('calculation-text').textContent = t('calculationText');
            document.getElementById('formula-text').innerHTML = '<strong>Index = (average score + 1) × 50</strong><br><br>' + t('formula');
            document.getElementById('articles-title').textContent = t('articles');
            document.getElementById('reset-view-button').textContent = language === 'fr' ? 'Réinitialiser la vue' : 'Reset view';
            document.getElementById('fullscreen-button').textContent = document.fullscreenElement ? (language === 'fr' ? 'Quitter le plein écran' : 'Exit fullscreen') : (language === 'fr' ? 'Plein écran' : 'Fullscreen');
            document.getElementById('disclaimer').textContent = t('disclaimer');
            const labels = document.querySelectorAll('.article-filters .filter-field');
            [t('date'), t('country'), t('asset'), t('family'), t('theme')].forEach((label, index) => {{ labels[index].childNodes[0].nodeValue = label; }});
            countryFilter.options[0].textContent = t('allCountries');
            assetFilter.options[0].textContent = t('allAssets');
            categoryFilter.options[0].textContent = t('allFamilies');
            themeFilter.options[0].textContent = t('allThemes');
            document.getElementById('clear-filters').textContent = t('clear');
            renderArticles();
        }}
        document.getElementById('language-button').addEventListener('click', () => {{
            language = language === 'en' ? 'fr' : 'en';
            applyLanguage();
        }});
        themeButtons.forEach(button => button.addEventListener('click', () => {{
            const nextTheme = button.dataset.theme;
            const currentUrl = new URL(window.location.href);
            currentUrl.searchParams.set('theme', nextTheme);
            window.location.href = currentUrl.toString();
        }}));
        if (select.options.length) {{ select.value = select.options[0].value; renderArticles(); }}
        [select, countryFilter, assetFilter, categoryFilter, themeFilter].forEach(filter => filter.addEventListener('change', () => {{
            showAllArticles = false;
            renderArticles();
        }}));
        moreArticlesButton.addEventListener('click', () => {{
            showAllArticles = !showAllArticles;
            renderArticles();
        }});
        graph.on('plotly_click', event => {{
            const point = event.points && event.points[0];
            if (!point) return;
            const day = String(point.x);
            const family = point.data && point.data.meta ? point.data.meta.family : 'global';
            const matchingOption = [...select.options].find(option => option.value === day);
            if (!matchingOption) return;
            select.value = day;
            categoryFilter.value = family === 'global' ? '' : family;
            showAllArticles = false;
            articleSelection.textContent = `Selected from ${{family === 'global' ? 'Global' : familyLabels[family]}} — ${{day}} · `;
            renderArticles();
            document.querySelector('.articles').scrollIntoView({{behavior: 'smooth', block: 'start'}});
        }});
        function updateCurves() {{
            const visibility = Array(graph.data.length).fill(false);
            familyOrder.forEach((family, index) => {{
                selectedAverages[family].forEach(window => {{
                    const targetIndex = index * windowOrder.length + windowOrder.indexOf(window);
                    visibility[targetIndex] = true;
                }});
            }});
            Plotly.restyle(graph, 'visible', visibility);
            Plotly.relayout(graph, {{title: {json.dumps(chart_title)}}});
        }}
        function updatePeriod(period) {{
            selectedPeriod = period;
            periodButtons.forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.period) === period)));
            updateKicker();
            const dates = chartDates;
            if (!dates.length) return;
            const chartEnd = new Date(`${{dates[dates.length - 1]}}T00:00:00Z`);
            chartEnd.setUTCDate(chartEnd.getUTCDate() + 1);
            Plotly.relayout(graph, {{'xaxis.range': [dates[Math.max(0, dates.length - period)], chartEnd.toISOString().slice(0, 10)]}});
            updateDateAxis(dates);
        }}
        periodButtons.forEach(button => button.addEventListener('click', () => updatePeriod(Number(button.dataset.period))));
        function setCurveSelections(selections) {{
            familyOrder.forEach(family => {{
                selectedAverages[family] = new Set(selections[family] || []);
            }});
            curveOptions.forEach(option => {{
                const family = option.dataset.family;
                const window = Number(option.dataset.window);
                option.setAttribute('aria-pressed', String(selectedAverages[family].has(window)));
            }});
            updateCurves();
        }}
        defaultCurvesButton?.addEventListener('click', () => {{
            setCurveSelections(Object.fromEntries(
                familyOrder.map(family => [family, [defaultWindows[family]]])
            ));
        }});
        clearCurvesButton?.addEventListener('click', () => {{
            setCurveSelections(Object.fromEntries(familyOrder.map(family => [family, []])));
        }});
        familyButtons.forEach(button => button.addEventListener('click', () => {{
            const options = document.querySelector(`[data-family-options="${{button.dataset.family}}"]`);
            const expanded = button.getAttribute('aria-expanded') === 'true';
            button.setAttribute('aria-expanded', String(!expanded));
            options.hidden = expanded;
            button.querySelector('.curve-chevron').textContent = expanded ? '+' : '−';
        }}));
        curveOptions.forEach(option => option.addEventListener('click', () => {{
            const family = option.dataset.family;
            const window = Number(option.dataset.window);
            const selected = selectedAverages[family];
            if (familyOrder.length === 1) {{
                selectedAverages[family] = new Set([window]);
                curveOptions.forEach(candidate => {{
                    if (candidate.dataset.family === family) {{
                        candidate.setAttribute('aria-pressed', String(Number(candidate.dataset.window) === window));
                    }}
                }});
            }} else {{
                if (selected.has(window)) selected.delete(window);
                else selected.add(window);
                option.setAttribute('aria-pressed', String(selected.has(window)));
            }}
            updateCurves();
        }}));
        document.getElementById('clear-filters').addEventListener('click', () => {{
            countryFilter.value = '';
            assetFilter.value = '';
            categoryFilter.value = '';
            themeFilter.value = '';
            showAllArticles = false;
            renderArticles();
        }});
        function bindCurveLegendTooltips() {{
            const legendEntries = graph.querySelectorAll('.legend .traces');
            legendEntries.forEach((entry, index) => {{
                if (entry.dataset.descriptionBound) return;
                entry.dataset.descriptionBound = 'true';
                entry.addEventListener('mouseenter', event => {{
                    curveTooltip.textContent = curveDescriptions[index] || '';
                    curveTooltip.style.display = 'block';
                    moveCurveTooltip(event);
                }});
                entry.addEventListener('mousemove', moveCurveTooltip);
                entry.addEventListener('mouseleave', () => {{ curveTooltip.style.display = 'none'; }});
            }});
        }}
        function moveCurveTooltip(event) {{
            const panelRect = chartPanel.getBoundingClientRect();
            curveTooltip.style.left = `${{event.clientX - panelRect.left + 12}}px`;
            curveTooltip.style.top = `${{event.clientY - panelRect.top + 12}}px`;
        }}
        graph.on('plotly_afterplot', bindCurveLegendTooltips);
        bindCurveLegendTooltips();
        updateCurves();
        updatePeriod(selectedPeriod);
        document.getElementById('fullscreen-button').addEventListener('click', async () => {{
            const panel = document.getElementById('chart-panel');
            if (!document.fullscreenElement) await panel.requestFullscreen();
            else await document.exitFullscreen();
        }});
        document.getElementById('reset-view-button').addEventListener('click', () => {{
            const graph = document.querySelector('.plotly-graph-div');
            if (graph) {{
                Plotly.relayout(graph, {{'yaxis.autorange': false, 'yaxis.range': [0, 100]}});
                updatePeriod(selectedPeriod);
            }}
        }});
        document.addEventListener('fullscreenchange', () => {{
            document.getElementById('fullscreen-button').textContent = document.fullscreenElement ? 'Exit fullscreen' : 'Fullscreen';
            window.dispatchEvent(new Event('resize'));
        }});
    </script>
</body>
</html>"""
    Path(output_path).write_text(page, encoding="utf-8")
    return output_path


if __name__ == "__main__":
    print(get_daily_sentiment_index(7))
    print(get_weighted_fear_greed_index(7))
    print(generate_fear_greed_chart())
    print(generate_interactive_fear_greed_chart())
