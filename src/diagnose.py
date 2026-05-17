"""Quick diagnostic: show the rating distribution per SKU.

If the scrape is properly stratified across stars, we expect roughly even
counts across the 1-5 buckets. If we see 90%+ in the 5-star bucket, the
stratification isn't working and we need to fix the scrape config.
"""

import sqlite3
from pathlib import Path

DB = Path(__file__).parent.parent / "data" / "reviews.db"

with sqlite3.connect(DB) as conn:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT s.display_name,
               SUM(CASE WHEN r.rating=1 THEN 1 ELSE 0 END) AS s1,
               SUM(CASE WHEN r.rating=2 THEN 1 ELSE 0 END) AS s2,
               SUM(CASE WHEN r.rating=3 THEN 1 ELSE 0 END) AS s3,
               SUM(CASE WHEN r.rating=4 THEN 1 ELSE 0 END) AS s4,
               SUM(CASE WHEN r.rating=5 THEN 1 ELSE 0 END) AS s5,
               COUNT(r.review_id) AS total
        FROM skus s LEFT JOIN reviews r ON r.asin = s.asin
        GROUP BY s.asin
        ORDER BY s.display_name
    """).fetchall()

print(f'{"SKU":<32} {"1★":>4} {"2★":>4} {"3★":>4} {"4★":>4} {"5★":>4}  {"tot":>4}')
print("-" * 64)
for r in rows:
    print(f'{r["display_name"]:<32} {r["s1"]:>4} {r["s2"]:>4} {r["s3"]:>4} {r["s4"]:>4} {r["s5"]:>4}  {r["total"]:>4}')