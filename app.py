import os
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from fear_greed_index import (
    generate_interactive_fear_greed_chart,
    get_category_indices,
    get_daily_sentiment_index,
    get_weighted_fear_greed_index,
)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = BASE_DIR / "fear_greed_chart.html"
DEFAULT_DAYS = 30
DEFAULT_THEME = "finance"
VALID_THEMES = {"finance", "esg", "ecology"}
CHART_CACHE_SECONDS = int(os.environ.get("CHART_CACHE_SECONDS", "300"))

app = Flask(__name__, template_folder="templates")

_chart_cache: dict[tuple[int, str], tuple[float, Path]] = {}


def normalize_theme(theme: str | None) -> str:
    if not theme:
        return DEFAULT_THEME
    normalized = str(theme).strip().lower()
    return normalized if normalized in VALID_THEMES else DEFAULT_THEME


def build_dashboard(days: int = DEFAULT_DAYS, theme: str = DEFAULT_THEME) -> Path:
    theme = normalize_theme(theme)
    cache_key = (days, theme)
    cached = _chart_cache.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] < CHART_CACHE_SECONDS:
        return cached[1]

    output_path = BASE_DIR / f"fear_greed_chart_{theme}_{days}.html"
    generated_path = Path(generate_interactive_fear_greed_chart(str(output_path), days, theme=theme))
    _chart_cache[cache_key] = (now, generated_path)
    return generated_path


def dashboard_summary(days: int = DEFAULT_DAYS) -> dict:
    global_rows = get_daily_sentiment_index(days)
    categories = get_category_indices(days)
    latest_day = global_rows[-1] if global_rows else {"fear_greed_index": 50, "article_count": 0, "date": "-"}
    weighted = get_weighted_fear_greed_index(days)
    return {
        "days": days,
        "latest_index": latest_day.get("fear_greed_index"),
        "latest_status": "Neutral" if latest_day.get("fear_greed_index") is None else (
            "Extreme fear" if latest_day["fear_greed_index"] < 20 else
            "Fear" if latest_day["fear_greed_index"] < 40 else
            "Neutral" if latest_day["fear_greed_index"] < 60 else
            "Greed" if latest_day["fear_greed_index"] < 80 else
            "Extreme greed"
        ),
        "weighted_index": weighted.get("fear_greed_index", 50),
        "article_count": weighted.get("article_count", 0),
        "global": global_rows,
        "categories": categories,
    }


@app.get("/")
def index():
    days = request.args.get("days", DEFAULT_DAYS, type=int)
    theme = normalize_theme(request.args.get("theme"))
    if days is None or days <= 0:
        days = DEFAULT_DAYS
    chart_path = build_dashboard(days, theme=theme)
    response = send_file(chart_path, mimetype="text/html")
    response.headers["Cache-Control"] = f"public, max-age={CHART_CACHE_SECONDS}"
    return response


@app.get("/dashboard")
def dashboard():
    days = request.args.get("days", DEFAULT_DAYS, type=int)
    theme = normalize_theme(request.args.get("theme"))
    if days is None or days <= 0:
        days = DEFAULT_DAYS
    chart_path = build_dashboard(days, theme=theme)
    response = send_file(chart_path, mimetype="text/html")
    response.headers["Cache-Control"] = f"public, max-age={CHART_CACHE_SECONDS}"
    return response


@app.get("/api")
def api_dashboard():
    days = request.args.get("days", DEFAULT_DAYS, type=int)
    theme = normalize_theme(request.args.get("theme"))
    if days is None or days <= 0:
        days = DEFAULT_DAYS

    return jsonify({"theme": theme, "summary": dashboard_summary(days)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=False)
