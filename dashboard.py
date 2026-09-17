#!/usr/bin/env python3
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fear_greed_index import get_daily_sentiment_index, get_advanced_fear_greed_index


def fetch_daily_rows(days: int = 30):
    rows = get_daily_sentiment_index(days)
    return [
        {
            "date": row["date"],
            "score": row["score"],
            "index": row["fear_greed_index"],
            "article_count": row["article_count"],
        }
        for row in rows
        if row["score"] is not None
    ]


def dashboard_summary(days: int = 30):
    rows = fetch_daily_rows(days)
    if not rows:
        return {
            "period_days": 0,
            "mean_score": 0.0,
            "fear_greed_index": 50.0,
            "status": "Neutral",
            "rows": [],
        }

    scores = [float(row["score"]) for row in rows]
    index_value = get_advanced_fear_greed_index(scores=scores)
    return {
        "period_days": len(rows),
        "mean_score": round(sum(scores) / len(scores), 4),
        "fear_greed_index": index_value["fear_greed_index"],
        "status": index_value["status"],
        "rows": rows,
    }


def print_dashboard(days: int = 30):
    summary = dashboard_summary(days)
    print("\n=== Fear & Greed Dashboard ===")
    print(f"Période: {summary['period_days']} jours")
    print(f"Score moyen: {summary['mean_score']:+.4f}")
    print(f"Indice: {summary['fear_greed_index']:.2f}")
    print(f"Statut: {summary['status']}")
    print("\nDétails par jour:")
    for row in summary["rows"]:
        print(
            f"- {row['date']}: score={row['score']:+.4f}, "
            f"index={row['index']:.2f}, articles={row['article_count']}"
        )


if __name__ == "__main__":
    print_dashboard(30)
