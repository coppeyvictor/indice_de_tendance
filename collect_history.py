#!/usr/bin/env python3
import argparse
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone

from veille_presse import (
    RSS_URLS,
    collect_daily_articles,
    format_duration,
    historical_rss_urls,
    initialize_database,
    process_new_articles,
)

MIN_ARTICLES_PER_DAY = 20
HISTORICAL_WORKERS = 12


def iter_days(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def collect_history(
    start_date: str,
    end_date: str | None = None,
    min_articles: int = MIN_ARTICLES_PER_DAY,
) -> None:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date) if end_date else datetime.now(timezone.utc).date()

    initialize_database()

    total_days = (end - start).days + 1
    started_at = time.perf_counter()
    print(f"Collecte optimisée de {start.isoformat()} à {end.isoformat()}...")
    for current_day in iter_days(start, end):
        day_str = current_day.isoformat()
        collect_daily_articles(day_str, RSS_URLS)
        with sqlite3.connect("sentiments.db") as connection:
            current_count = connection.execute(
                "SELECT COUNT(*) FROM daily_articles WHERE article_day = ?",
                (day_str,),
            ).fetchone()[0]

        if current_count < min_articles:
            collect_daily_articles(
                day_str,
                historical_rss_urls(day_str),
                forced_article_day=day_str,
                max_workers=HISTORICAL_WORKERS,
            )

        elapsed = time.perf_counter() - started_at
        completed = (current_day - start).days + 1
        average = elapsed / completed
        remaining = average * (total_days - completed)
        print(
            f"Progression : {completed}/{total_days} jours | "
            f"temps restant estimé : {format_duration(remaining)}"
        )

    print("Analyse des articles collectés...")
    for current_day in iter_days(start, end):
        day_str = current_day.isoformat()
        try:
            processed_count, scores = process_new_articles(day_str)
            if scores:
                mean_score = sum(float(item["score"]) for item in scores) / len(scores)
                print(f"{day_str}: {processed_count} article(s), score moyen = {mean_score:+.4f}")
            else:
                print(f"{day_str}: 0 article analysé")
        except Exception as exc:  # pragma: no cover - CLI safeguard
            print(f"{day_str}: ERREUR {type(exc).__name__}: {exc}", file=sys.stderr)

    print("Terminé.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collecte et analyse l’historique RSS sur une période donnée.",
    )
    parser.add_argument(
        "start_date",
        help="Date de début au format YYYY-MM-DD",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Date de fin au format YYYY-MM-DD (défaut: aujourd’hui)",
    )
    parser.add_argument(
        "--min-articles",
        type=int,
        default=MIN_ARTICLES_PER_DAY,
        help="Déclenche le rattrapage historique sous ce volume (défaut : 20).",
    )
    args = parser.parse_args()

    collect_history(args.start_date, args.end_date, args.min_articles)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
