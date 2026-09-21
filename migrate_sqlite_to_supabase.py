import os
import sqlite3

import psycopg2
from psycopg2.extras import execute_values

SQLITE_DB = "sentiments.db"
PG_DSN = os.environ["DATABASE_URL"]


sqlite_conn = sqlite3.connect(SQLITE_DB)
sqlite_conn.row_factory = sqlite3.Row

pg_conn = psycopg2.connect(PG_DSN, sslmode="require")
pg_cur = pg_conn.cursor()


def ensure_table(table_name: str):
    cols = sqlite_conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if not cols:
        return

    names = [col[1] for col in cols]
    quoted = ", ".join(f'"{name}"' for name in names)
    types = []
    for col in cols:
        name, ctype = col[1], col[2]
        if ctype and "INTEGER" in str(ctype).upper():
            pg_type = "INTEGER"
        elif ctype and "REAL" in str(ctype).upper():
            pg_type = "DOUBLE PRECISION"
        elif ctype and "TEXT" in str(ctype).upper():
            pg_type = "TEXT"
        elif ctype and "BLOB" in str(ctype).upper():
            pg_type = "BYTEA"
        else:
            pg_type = "TEXT"
        types.append(f'"{name}" {pg_type}')

    ddl = f"CREATE TABLE IF NOT EXISTS \"{table_name}\" ({', '.join(types)});"
    pg_cur.execute(ddl)
    pg_cur.execute(f'DROP INDEX IF EXISTS "{table_name}_url_unique";')
    pg_conn.commit()
    print(f"Ensured table exists: {table_name}")


for table in ("daily_articles", "articles"):
    ensure_table(table)
    rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        print(f"{table}: no rows")
        continue

    columns = [row[1] for row in sqlite_conn.execute(f"PRAGMA table_info({table})").fetchall()]

    pg_cur.execute(f'SELECT "url" FROM "{table}"')
    existing_urls = {row[0] for row in pg_cur.fetchall()}

    new_rows = [
        tuple(row[c] for c in columns)
        for row in rows
        if row["url"] not in existing_urls
    ]
    print(f"{table}: {len(new_rows)} new row(s) to insert, {len(rows) - len(new_rows)} already present")

    if new_rows:
        insert_sql = (
            f"INSERT INTO \"{table}\" ({', '.join(f'\"{c}\"' for c in columns)}) VALUES %s"
        )
        try:
            execute_values(pg_cur, insert_sql, new_rows, page_size=500)
        except Exception as exc:  # pragma: no cover
            print(f"Failed inserting into {table}: {exc}")
            raise

pg_conn.commit()
print("Migration finished successfully")
pg_cur.close()
pg_conn.close()
sqlite_conn.close()
