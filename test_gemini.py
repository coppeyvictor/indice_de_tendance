import sqlite3

import pytest

from cluster_articles import cluster_documents
from fear_greed_index import (
    _build_windowed_curve,
    _apply_rolling_smoothing,
    compute_fear_greed_index,
    get_daily_sentiment_index,
    get_advanced_fear_greed_index,
    get_chart_series,
)
from sentiment import (
    analyze_sentiment_local,
    parse_score,
    parse_sentiment_analysis,
    prepare_sentiment_chunks,
)
from veille_presse import (
    classify_asset_type,
    classify_article,
    classify_country,
    classify_theme,
    force_reanalyze_theme,
    normalize_article_url,
)


def test_compute_fear_greed_index():
    assert compute_fear_greed_index(1.0) == 100.0
    assert compute_fear_greed_index(0.0) == 50.0
    assert compute_fear_greed_index(-1.0) == 0.0
    assert compute_fear_greed_index(0.5) == 75.0
    assert compute_fear_greed_index(-0.5) == 25.0


def test_classify_article_separates_crypto_markets_and_economy():
    assert classify_article("Bitcoin rises", "Ethereum market update") == "crypto"
    assert classify_article("Stocks rally", "Bond investors return") == "markets"
    assert classify_article("Inflation slows", "Central bank watches prices") == "economy"


def test_classify_article_metadata_returns_country_and_asset_type():
    assert classify_country("US stocks rally", "Wall Street gains") == "US"
    assert classify_country("European Central Bank signals caution") == "EU"
    assert classify_asset_type("Oil prices rise", "Commodities gain") == "commodities"
    assert classify_asset_type("Housing market cools", "Mortgage rates remain high") == "real_estate"


def test_classify_theme_separates_finance_esg_and_ecology():
    assert classify_theme("Stocks rally after rate cut expectations") == "finance"
    assert classify_theme("Green bond issuance rises as net zero plan expands") == "esg"
    assert classify_theme("Wind turbines and carbon capture investments increase in Europe") == "ecology"


def test_force_reanalyze_theme_resets_scores_for_selected_theme(tmp_path, monkeypatch):
    db_path = tmp_path / "sentiments.db"
    monkeypatch.setattr("veille_presse.DATABASE_PATH", str(db_path))
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            """
            CREATE TABLE daily_articles (
                url TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                source TEXT NOT NULL,
                article_day TEXT NOT NULL,
                score REAL,
                analyzed_at TEXT,
                theme TEXT NOT NULL DEFAULT 'finance'
            )
            """
        )
        connection.execute(
            "INSERT INTO daily_articles (url, title, summary, source, article_day, score, analyzed_at, theme) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("u1", "Finance headline", "Summary", "Source", "2026-09-11", 0.8, "2026-09-11T00:00:00+00:00", "esg"),
        )
        connection.execute(
            "INSERT INTO daily_articles (url, title, summary, source, article_day, score, analyzed_at, theme) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("u2", "Other headline", "Summary", "Source", "2026-09-11", 0.2, "2026-09-11T00:00:00+00:00", "finance"),
        )
        connection.commit()
    force_reanalyze_theme("2026-09-11", "esg")
    with sqlite3.connect(str(db_path)) as connection:
        assert connection.execute("SELECT score FROM daily_articles WHERE url = ?", ("u1",)).fetchone()[0] is None
        assert connection.execute("SELECT score FROM daily_articles WHERE url = ?", ("u2",)).fetchone()[0] == 0.2


def test_category_index_rolling_smoothing_preserves_raw_value():
    rows = [
        {"date": "2026-09-01", "fear_greed_index": 20.0},
        {"date": "2026-09-02", "fear_greed_index": 80.0},
    ]

    smoothed = _apply_rolling_smoothing(rows, window=2)

    assert smoothed[-1]["raw_fear_greed_index"] == 80.0
    assert smoothed[-1]["fear_greed_index"] == 50.0
    assert smoothed[-1]["smoothing_window"] == 2


def test_build_windowed_curve_averages_previous_daily_indices():
    rows = [
        {"date": "2026-09-01", "fear_greed_index": 20.0, "article_count": 4},
        {"date": "2026-09-02", "fear_greed_index": 80.0, "article_count": 5},
        {"date": "2026-09-03", "fear_greed_index": 50.0, "article_count": 6},
    ]

    curve = _build_windowed_curve(rows, window=3)

    assert [row["fear_greed_index"] for row in curve] == [20.0, 53.33, 52.0]
    assert curve[-1]["smoothing_window"] == 3


def test_build_windowed_curve_weights_days_by_article_count():
    rows = [
        {"date": "2026-09-01", "fear_greed_index": 10.0, "article_count": 2},
        {"date": "2026-09-02", "fear_greed_index": 90.0, "article_count": 100},
    ]

    curve = _build_windowed_curve(rows, window=2)

    assert curve[-1]["fear_greed_index"] == 88.43


def test_cluster_documents_groups_repeated_news():
    labels = cluster_documents(
        [
            "Bitcoin falls after central bank raises interest rates",
            "Central bank raises rates and Bitcoin falls sharply",
            "New electric car factory opens in France",
        ],
        threshold=0.2,
    )
    assert labels[0] == labels[1]
    assert labels[0] != labels[2]


def test_get_advanced_fear_greed_index_uses_weighted_balance_and_status():
    data = get_advanced_fear_greed_index(scores=[-1.0, 1.0, 0.0])
    assert data["score"] == 0.0
    assert data["fear_greed_index"] == 50.0
    assert data["status"] == "Neutral"


def test_get_chart_series_skips_missing_days():
    series = get_chart_series(days=5)
    assert all(value is not None for _, value in series)
    assert series


def test_low_volume_day_keeps_raw_index_for_volume_weighting():
    rows = get_daily_sentiment_index(days=30)
    rows_by_date = {row["date"]: row for row in rows}

    low_volume_day = rows_by_date["2026-09-03"]
    assert low_volume_day["article_count"] < 20
    assert low_volume_day["index_carried_forward"] is False
    assert low_volume_day["fear_greed_index"] == low_volume_day["raw_fear_greed_index"]


def test_analyze_sentiment_local_handles_long_text(monkeypatch):
    calls = []

    def fake_pipeline(text, top_k=None, **kwargs):
        calls.append(text)
        if "very long" not in text:
            return [
                {"label": "LABEL_0", "score": 0.85},
                {"label": "LABEL_1", "score": 0.10},
                {"label": "LABEL_2", "score": 0.05},
            ]
        return [
            {"label": "LABEL_0", "score": 0.10},
            {"label": "LABEL_1", "score": 0.10},
            {"label": "LABEL_2", "score": 0.80},
        ]

    monkeypatch.setattr("sentiment._local_classifier", fake_pipeline)
    long_text = "very long " * 2000
    score = analyze_sentiment_local(long_text)
    assert -1.0 <= score <= 1.0
    assert len(calls) >= 2


def test_prepare_sentiment_chunks_preserves_long_article_without_oversized_chunks():
    long_text = "Une phrase importante. " * 500
    chunks = prepare_sentiment_chunks(long_text)

    assert len(chunks) > 1
    assert all(len(chunk) <= 800 for chunk in chunks)
    assert "Une phrase importante." in " ".join(chunks)


def test_normalize_article_url_rejects_google_news_redirects():
    assert normalize_article_url("https://example.com/article") == "https://example.com/article"
    assert normalize_article_url("https://news.google.com/rss/articles/abc?oc=5") is None
    assert normalize_article_url("https://news.google.com/rss/search?q=bitcoin") is None


def test_parse_score_positif():
    assert parse_score("0.65") == 0.65


def test_parse_score_negatif():
    assert parse_score("-0,30") == -0.30


def test_parse_sentiment_analysis_combines_impact_and_confidence():
    result = parse_sentiment_analysis(
        '{"sentiment": -0.40, "impact_financier": -0.80, "confiance": 0.90}'
    )

    assert result["score"] == -0.57
    assert result["confiance"] == 0.90


def test_parse_sentiment_analysis_accepts_json_code_fence():
    result = parse_sentiment_analysis(
        '```json\n{"sentiment": 0.20, "impact_financier": 0.60, "confiance": 0.80}\n```'
    )

    assert result["score"] == 0.36


@pytest.mark.parametrize("value", ["1.01", "0.6", "Le score est 0.65", ""])
def test_parse_score_refuse_les_reponses_invalides(value):
    with pytest.raises(ValueError):
        parse_score(value)
