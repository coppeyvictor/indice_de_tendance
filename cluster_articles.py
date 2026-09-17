#!/usr/bin/env python3
import argparse
import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from math import sqrt

from veille_presse import DATABASE_PATH, initialize_database

TOKEN_RE = re.compile(r"[\wÀ-ÿ]{3,}", re.UNICODE)
STOP_WORDS = {
    "avec", "dans", "pour", "sur", "les", "des", "une", "aux", "est",
    "sont", "this", "that", "from", "with", "the", "and", "for", "are",
    "will", "after", "into", "than", "their", "about", "have", "has",
}


def tokenize(text: str) -> list[str]:
    return [
        token.lower()
        for token in TOKEN_RE.findall(text or "")
        if token.lower() not in STOP_WORDS
    ]


def tfidf_vectors(documents: list[str]) -> list[dict[str, float]]:
    tokenized = [tokenize(document) for document in documents]
    document_frequency = Counter(
        token for tokens in tokenized for token in set(tokens)
    )
    total_documents = max(len(documents), 1)
    vectors = []
    for tokens in tokenized:
        counts = Counter(tokens)
        vector = {}
        for token, count in counts.items():
            inverse_frequency = 1.0 + math.log(
                total_documents / (1 + document_frequency[token])
            )
            vector[token] = (1.0 + math.log(count)) * inverse_frequency
        norm = sqrt(sum(value * value for value in vector.values())) or 1.0
        vectors.append({token: value / norm for token, value in vector.items()})
    return vectors


def cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(token, 0.0) for token, value in left.items())


def cluster_documents(documents: list[str], threshold: float = 0.28) -> list[int]:
    vectors = tfidf_vectors(documents)
    parents = list(range(len(documents)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for left in range(len(vectors)):
        for right in range(left + 1, len(vectors)):
            if cosine_similarity(vectors[left], vectors[right]) >= threshold:
                union(left, right)

    roots = {}
    cluster_ids = []
    for index in range(len(documents)):
        root = find(index)
        roots.setdefault(root, len(roots) + 1)
        cluster_ids.append(roots[root])
    return cluster_ids


def cluster_period(start_date: date, end_date: date, threshold: float) -> int:
    initialize_database()
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute(
            """
            SELECT url, article_day, title, summary
            FROM daily_articles
            WHERE article_day BETWEEN ? AND ?
            ORDER BY article_day, published, url
            """,
            (start_date.isoformat(), end_date.isoformat()),
        ).fetchall()

        by_day = defaultdict(list)
        for row in rows:
            by_day[row[1]].append(row)

        total_clusters = 0
        for article_day, articles in by_day.items():
            documents = [f"{row[2]} {row[3]}" for row in articles]
            labels = cluster_documents(documents, threshold)
            sizes = Counter(labels)
            for article, label in zip(articles, labels):
                cluster_id = f"{article_day}:{label}"
                connection.execute(
                    """
                    UPDATE daily_articles
                    SET cluster_id = ?, cluster_size = ?
                    WHERE url = ?
                    """,
                    (cluster_id, sizes[label], article[0]),
                )
            total_clusters += len(sizes)
            print(f"{article_day}: {len(articles)} articles, {len(sizes)} clusters")

    return total_clusters


def main() -> int:
    parser = argparse.ArgumentParser(description="Regroupe les articles par événement.")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--threshold", type=float, default=0.28)
    args = parser.parse_args()

    end_date = date.fromisoformat(args.end_date) if args.end_date else datetime.now(timezone.utc).date()
    start_date = date.fromisoformat(args.start_date) if args.start_date else end_date - timedelta(days=29)
    total_clusters = cluster_period(start_date, end_date, args.threshold)
    print(f"Clusters créés : {total_clusters}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
