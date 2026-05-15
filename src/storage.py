"""SQLite storage for ReviewLens.

Two tables:
  - skus: the curated 10-SKU catalog (brand, category, asin, display name)
  - reviews: raw scraped reviews, one row per review

Design notes:
  - asin is the join key, mirroring how Amazon identifies products
  - reviews.review_id is the natural PK from the Apify output, guaranteeing idempotent inserts
  - Keep raw data raw. Extraction signals live in a separate table (signals.db) so a bad
    extraction run never corrupts the source corpus.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path(__file__).parent.parent / "data" / "reviews.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS skus (
    asin            TEXT PRIMARY KEY,
    brand           TEXT NOT NULL,           -- 'Shark' or 'Ninja'
    category        TEXT NOT NULL,           -- e.g. 'upright_vacuum', 'air_fryer'
    display_name    TEXT NOT NULL,           -- human-readable name for dashboards
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
    review_id            TEXT PRIMARY KEY,    -- Apify-provided unique id
    asin                 TEXT NOT NULL,
    rating               INTEGER,             -- 1..5
    review_date          TEXT,                -- ISO date string from scraper
    review_title         TEXT,
    review_text          TEXT,
    verified_purchase    INTEGER,             -- 0/1
    helpful_vote_count   INTEGER,
    variant_asin         TEXT,
    country              TEXT,
    language             TEXT,
    scraped_at           TEXT,
    FOREIGN KEY (asin) REFERENCES skus(asin)
);

CREATE INDEX IF NOT EXISTS idx_reviews_asin ON reviews(asin);
CREATE INDEX IF NOT EXISTS idx_reviews_rating ON reviews(rating);
CREATE INDEX IF NOT EXISTS idx_reviews_date ON reviews(review_date);
"""


@contextmanager
def get_conn(db_path: Path = DB_PATH):
    """Context-managed sqlite3 connection with sensible defaults."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    """Create tables and indexes if they don't exist."""
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)


def upsert_skus(skus: list[dict[str, Any]], db_path: Path = DB_PATH) -> int:
    """Insert or replace SKU catalog rows. Returns count inserted."""
    with get_conn(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO skus (asin, brand, category, display_name, notes)
            VALUES (:asin, :brand, :category, :display_name, :notes)
            ON CONFLICT(asin) DO UPDATE SET
                brand=excluded.brand,
                category=excluded.category,
                display_name=excluded.display_name,
                notes=excluded.notes
            """,
            skus,
        )
    return len(skus)


def insert_reviews(reviews: Iterable[dict[str, Any]], db_path: Path = DB_PATH) -> int:
    """Insert reviews, skipping duplicates by review_id. Returns count of new rows."""
    rows = list(reviews)
    if not rows:
        return 0
    with get_conn(db_path) as conn:
        cur = conn.executemany(
            """
            INSERT OR IGNORE INTO reviews (
                review_id, asin, rating, review_date, review_title, review_text,
                verified_purchase, helpful_vote_count, variant_asin,
                country, language, scraped_at
            ) VALUES (
                :review_id, :asin, :rating, :review_date, :review_title, :review_text,
                :verified_purchase, :helpful_vote_count, :variant_asin,
                :country, :language, :scraped_at
            )
            """,
            rows,
        )
        return cur.rowcount


def review_count_by_sku(db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    """Sanity-check query: review count per SKU, joined with the catalog."""
    with get_conn(db_path) as conn:
        return conn.execute(
            """
            SELECT s.asin, s.brand, s.display_name, s.category,
                   COUNT(r.review_id) AS n_reviews,
                   AVG(r.rating)      AS avg_rating
            FROM skus s
            LEFT JOIN reviews r ON r.asin = s.asin
            GROUP BY s.asin
            ORDER BY s.brand, s.display_name
            """
        ).fetchall()


if __name__ == "__main__":
    init_db()
    print(f"Initialized {DB_PATH}")
