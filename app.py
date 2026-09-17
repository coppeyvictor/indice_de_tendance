import os
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

app = Flask(__name__, template_folder="templates")


def normalize_theme(theme: str | None) -> str:
    if not theme:
        return DEFAULT_THEME
    normalized = str(theme).strip().lower()
    return normalized if normalized in VALID_THEMES else DEFAULT_THEME


def build_dashboard(days: int = DEFAULT_DAYS, theme: str = DEFAULT_THEME) -> Path:
    generated_path = generate_interactive_fear_greed_chart(str(OUTPUT_PATH), days, theme=normalize_theme(theme))
    return Path(generated_path)


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
    build_dashboard(days, theme=theme)
    response = send_file(OUTPUT_PATH, mimetype="text/html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/dashboard")
def dashboard():
    days = request.args.get("days", DEFAULT_DAYS, type=int)
    theme = normalize_theme(request.args.get("theme"))
    if days is None or days <= 0:
        days = DEFAULT_DAYS
    build_dashboard(days, theme=theme)
    response = send_file(OUTPUT_PATH, mimetype="text/html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
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
