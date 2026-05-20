from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .config import DB_PATH, ensure_dirs
from .models import ResearchItem


SCHEMA = """
CREATE TABLE IF NOT EXISTS research_items (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    author_or_team TEXT DEFAULT '',
    category TEXT DEFAULT '',
    published_at TEXT DEFAULT '',
    url TEXT NOT NULL,
    pdf_path TEXT DEFAULT '',
    raw_path TEXT DEFAULT '',
    text TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    ai_summary TEXT DEFAULT '',
    ai_summary_model TEXT DEFAULT '',
    ai_summary_at TEXT DEFAULT '',
    status TEXT NOT NULL,
    completeness TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_research_items_url ON research_items(url);
CREATE INDEX IF NOT EXISTS idx_research_items_status ON research_items(status);
CREATE INDEX IF NOT EXISTS idx_research_items_source ON research_items(source);
CREATE INDEX IF NOT EXISTS idx_research_items_published_at ON research_items(published_at);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Path = DB_PATH) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        _ensure_columns(conn)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(research_items)")}
    migrations = {
        "ai_summary": "ALTER TABLE research_items ADD COLUMN ai_summary TEXT DEFAULT ''",
        "ai_summary_model": "ALTER TABLE research_items ADD COLUMN ai_summary_model TEXT DEFAULT ''",
        "ai_summary_at": "ALTER TABLE research_items ADD COLUMN ai_summary_at TEXT DEFAULT ''",
    }
    for column, sql in migrations.items():
        if column not in columns:
            conn.execute(sql)


def upsert_item(item: ResearchItem) -> bool:
    item.normalized()
    fields = [
        "id",
        "title",
        "source",
        "source_type",
        "author_or_team",
        "category",
        "published_at",
        "url",
        "pdf_path",
        "raw_path",
        "text",
        "summary",
        "status",
        "completeness",
        "error",
        "created_at",
        "updated_at",
    ]
    values = [getattr(item, field) for field in fields]
    placeholders = ", ".join("?" for _ in fields)
    sql = f"""
    INSERT INTO research_items ({", ".join(fields)})
    VALUES ({placeholders})
    ON CONFLICT(url) DO UPDATE SET
        title = CASE WHEN excluded.title != '' THEN excluded.title ELSE research_items.title END,
        source = excluded.source,
        source_type = excluded.source_type,
        author_or_team = CASE WHEN excluded.author_or_team != '' THEN excluded.author_or_team ELSE research_items.author_or_team END,
        category = CASE WHEN excluded.category != '' THEN excluded.category ELSE research_items.category END,
        published_at = CASE WHEN excluded.published_at != '' THEN excluded.published_at ELSE research_items.published_at END,
        pdf_path = CASE WHEN excluded.pdf_path != '' THEN excluded.pdf_path ELSE research_items.pdf_path END,
        raw_path = CASE WHEN excluded.raw_path != '' THEN excluded.raw_path ELSE research_items.raw_path END,
        text = CASE WHEN excluded.text != '' THEN excluded.text ELSE research_items.text END,
        summary = CASE WHEN excluded.summary != '' THEN excluded.summary ELSE research_items.summary END,
        status = CASE
            WHEN excluded.status = 'need_manual'
             AND research_items.status IN ('collected', 'summary_pending', 'summarized')
            THEN research_items.status
            WHEN excluded.status = 'summary_pending'
             AND research_items.status = 'summarized'
             AND TRIM(COALESCE(research_items.summary, '')) != ''
            THEN research_items.status
            ELSE excluded.status
        END,
        completeness = CASE
            WHEN excluded.status = 'need_manual' AND research_items.completeness != ''
            THEN research_items.completeness
            ELSE excluded.completeness
        END,
        error = excluded.error,
        updated_at = excluded.updated_at
    """
    with connect() as conn:
        before = conn.total_changes
        conn.execute(sql, values)
        return conn.total_changes > before


def list_items(status: str | None = None, limit: int = 200) -> list[sqlite3.Row]:
    init_db()
    with connect() as conn:
        if status:
            return list(
                conn.execute(
                    "SELECT * FROM research_items WHERE status = ? ORDER BY published_at DESC LIMIT ?",
                    (status, limit),
                )
            )
        return list(conn.execute("SELECT * FROM research_items ORDER BY published_at DESC LIMIT ?", (limit,)))


def list_urls() -> set[str]:
    init_db()
    with connect() as conn:
        return {row["url"] for row in conn.execute("SELECT url FROM research_items WHERE url != ''")}


def list_items_by_ids(item_ids: list[str]) -> list[sqlite3.Row]:
    init_db()
    if not item_ids:
        return []
    placeholders = ", ".join("?" for _ in item_ids)
    with connect() as conn:
        return list(
            conn.execute(
                f"SELECT * FROM research_items WHERE id IN ({placeholders}) ORDER BY published_at DESC",
                item_ids,
            )
        )


def list_items_created_on(date_prefix: str, limit: int = 5000) -> list[sqlite3.Row]:
    init_db()
    with connect() as conn:
        return list(
            conn.execute(
                """
                SELECT * FROM research_items
                WHERE created_at LIKE ?
                ORDER BY published_at DESC
                LIMIT ?
                """,
                (f"{date_prefix}%", limit),
            )
        )


def iter_pending_summaries() -> Iterable[sqlite3.Row]:
    init_db()
    with connect() as conn:
        rows = list(conn.execute(
            """
            SELECT * FROM research_items
            WHERE status IN ('collected', 'summary_pending')
              AND TRIM(COALESCE(summary, '')) = ''
            ORDER BY published_at DESC
            """
        ))
    yield from rows


def iter_pending_ai_summaries(*, limit: int = 5, resummarize: bool = False) -> Iterable[sqlite3.Row]:
    init_db()
    where = "TRIM(COALESCE(text, '')) != ''"
    if not resummarize:
        where += " AND TRIM(COALESCE(ai_summary, '')) = ''"
    sql = f"""
        SELECT * FROM research_items
        WHERE {where}
        ORDER BY published_at DESC
    """
    params: tuple[int, ...] = ()
    if limit > 0:
        sql += " LIMIT ?"
        params = (limit,)
    with connect() as conn:
        rows = list(conn.execute(sql, params))
    yield from rows


def update_summary(item_id: str, summary: str, status: str = "summarized") -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE research_items SET summary = ?, status = ?, updated_at = datetime('now') WHERE id = ?",
            (summary, status, item_id),
        )


def update_ai_summary(item_id: str, summary: str, model: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE research_items
            SET ai_summary = ?,
                ai_summary_model = ?,
                ai_summary_at = datetime('now'),
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (summary, model, item_id),
        )


def count_ai_summaries() -> int:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM research_items WHERE TRIM(COALESCE(ai_summary, '')) != ''"
        ).fetchone()
    return int(row["count"] if row else 0)
