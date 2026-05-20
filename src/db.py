from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .config import DB_PATH, ensure_dirs
from .extractors.images import ArticleImage
from .models import ResearchItem
from .models import stable_id


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

CREATE TABLE IF NOT EXISTS article_images (
    id TEXT PRIMARY KEY,
    article_id TEXT NOT NULL,
    image_index INTEGER NOT NULL,
    url TEXT NOT NULL,
    alt TEXT DEFAULT '',
    ratio REAL DEFAULT 0,
    width INTEGER DEFAULT 0,
    height INTEGER DEFAULT 0,
    is_content_image INTEGER NOT NULL DEFAULT 1,
    vision_summary TEXT DEFAULT '',
    vision_model TEXT DEFAULT '',
    vision_summary_at TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(article_id) REFERENCES research_items(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_article_images_article_url ON article_images(article_id, url);
CREATE INDEX IF NOT EXISTS idx_article_images_article_id ON article_images(article_id);
CREATE INDEX IF NOT EXISTS idx_article_images_content ON article_images(is_content_image);
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

    image_columns = {row["name"] for row in conn.execute("PRAGMA table_info(article_images)")}
    if image_columns:
        image_migrations = {
            "alt": "ALTER TABLE article_images ADD COLUMN alt TEXT DEFAULT ''",
            "ratio": "ALTER TABLE article_images ADD COLUMN ratio REAL DEFAULT 0",
            "width": "ALTER TABLE article_images ADD COLUMN width INTEGER DEFAULT 0",
            "height": "ALTER TABLE article_images ADD COLUMN height INTEGER DEFAULT 0",
            "is_content_image": "ALTER TABLE article_images ADD COLUMN is_content_image INTEGER NOT NULL DEFAULT 1",
            "vision_summary": "ALTER TABLE article_images ADD COLUMN vision_summary TEXT DEFAULT ''",
            "vision_model": "ALTER TABLE article_images ADD COLUMN vision_model TEXT DEFAULT ''",
            "vision_summary_at": "ALTER TABLE article_images ADD COLUMN vision_summary_at TEXT DEFAULT ''",
            "created_at": "ALTER TABLE article_images ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
            "updated_at": "ALTER TABLE article_images ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
        }
        for column, sql in image_migrations.items():
            if column not in image_columns:
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


def list_items_with_raw_html(limit: int = 5000) -> list[sqlite3.Row]:
    init_db()
    sql = """
        SELECT * FROM research_items
        WHERE TRIM(COALESCE(raw_path, '')) != ''
        ORDER BY published_at DESC
    """
    params: tuple[int, ...] = ()
    if limit > 0:
        sql += " LIMIT ?"
        params = (limit,)
    with connect() as conn:
        return list(conn.execute(sql, params))


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


def upsert_article_images(article_id: str, images: list[ArticleImage]) -> int:
    if not images:
        return 0
    now_sql = "datetime('now')"
    fields = [
        "id",
        "article_id",
        "image_index",
        "url",
        "alt",
        "ratio",
        "width",
        "height",
        "is_content_image",
    ]
    sql = f"""
        INSERT INTO article_images ({", ".join(fields)}, created_at, updated_at)
        VALUES ({", ".join("?" for _ in fields)}, {now_sql}, {now_sql})
        ON CONFLICT(article_id, url) DO UPDATE SET
            image_index = excluded.image_index,
            alt = excluded.alt,
            ratio = excluded.ratio,
            width = excluded.width,
            height = excluded.height,
            is_content_image = excluded.is_content_image,
            updated_at = {now_sql}
    """
    values = []
    for index, image in enumerate(images, start=1):
        values.append(
            (
                stable_id(article_id, image.url),
                article_id,
                index,
                image.url,
                image.alt,
                image.ratio,
                image.width,
                image.height,
                1 if image.likely_content else 0,
            )
        )
    with connect() as conn:
        before = conn.total_changes
        conn.executemany(sql, values)
        return conn.total_changes - before


def iter_pending_image_summaries(
    *,
    limit: int = 10,
    resummarize: bool = False,
    content_only: bool = True,
) -> Iterable[sqlite3.Row]:
    init_db()
    where = ["TRIM(COALESCE(article_images.url, '')) != ''"]
    if content_only:
        where.append("article_images.is_content_image = 1")
    if not resummarize:
        where.append("TRIM(COALESCE(article_images.vision_summary, '')) = ''")
    sql = f"""
        SELECT
            article_images.*,
            research_items.title AS article_title,
            research_items.source AS article_source,
            research_items.published_at AS article_published_at
        FROM article_images
        JOIN research_items ON research_items.id = article_images.article_id
        WHERE {" AND ".join(where)}
        ORDER BY research_items.published_at DESC, article_images.image_index ASC
    """
    params: tuple[int, ...] = ()
    if limit > 0:
        sql += " LIMIT ?"
        params = (limit,)
    with connect() as conn:
        rows = list(conn.execute(sql, params))
    yield from rows


def update_image_summary(image_id: str, summary: str, model: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE article_images
            SET vision_summary = ?,
                vision_model = ?,
                vision_summary_at = datetime('now'),
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (summary, model, image_id),
        )


def list_article_image_summaries(article_id: str) -> list[sqlite3.Row]:
    init_db()
    with connect() as conn:
        return list(
            conn.execute(
                """
                SELECT image_index, alt, ratio, width, height, vision_summary, vision_model
                FROM article_images
                WHERE article_id = ?
                  AND is_content_image = 1
                  AND TRIM(COALESCE(vision_summary, '')) != ''
                ORDER BY image_index ASC
                """,
                (article_id,),
            )
        )


def count_ai_summaries() -> int:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM research_items WHERE TRIM(COALESCE(ai_summary, '')) != ''"
        ).fetchone()
    return int(row["count"] if row else 0)


def count_article_images() -> tuple[int, int, int]:
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN is_content_image = 1 THEN 1 ELSE 0 END) AS content_count,
                SUM(CASE WHEN TRIM(COALESCE(vision_summary, '')) != '' THEN 1 ELSE 0 END) AS summarized_count
            FROM article_images
            """
        ).fetchone()
    if not row:
        return (0, 0, 0)
    return (int(row["total"] or 0), int(row["content_count"] or 0), int(row["summarized_count"] or 0))
