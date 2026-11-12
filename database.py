import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "documents.db")
UPLOADS_DIR = os.path.join(DB_DIR, "uploads")

os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

_DOCUMENT_MIGRATION_COLUMNS = {
    "source_path": "TEXT DEFAULT ''",
    "category": "TEXT DEFAULT 'general'",
    "tags_json": "TEXT DEFAULT '[]'",
    "ingestion_channel": "TEXT DEFAULT 'web_upload'",
    "doc_date": "TEXT DEFAULT ''",
    "image_width": "INTEGER DEFAULT 0",
    "image_height": "INTEGER DEFAULT 0",
}


def normalize_tags(raw_tags: Optional[Union[str, List[str]]]) -> List[str]:
    """Normalize tags from a list or comma-separated string into unique ordered strings."""
    if raw_tags is None:
        return []
    if isinstance(raw_tags, str):
        stripped = raw_tags.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    raw_items = [str(item) for item in parsed]
                else:
                    raw_items = [stripped]
            except Exception:
                raw_items = stripped.split(",")
        else:
            raw_items = stripped.split(",")
    else:
        raw_items = [str(item) for item in raw_tags]

    seen = set()
    cleaned: List[str] = []
    for item in raw_items:
        tag = item.strip()
        if tag and tag not in seen:
            seen.add(tag)
            cleaned.append(tag)
    return cleaned


def _parse_tags_json(tags_json: Optional[str]) -> List[str]:
    if not tags_json:
        return []
    try:
        parsed = json.loads(tags_json)
        return normalize_tags(parsed) if isinstance(parsed, list) else []
    except Exception:
        return []


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    target_path = db_path or DB_PATH
    parent = os.path.dirname(os.path.abspath(target_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _create_fts_table(cursor: sqlite3.Cursor) -> None:
    try:
        cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            id UNINDEXED,
            filename,
            category,
            tags_text,
            text_content,
            tokenize="trigram"
        );
        """)
    except Exception as exc:
        print("[DB Warning] Trigram FTS5 initialization error, falling back to standard FTS:", exc)
        cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            id UNINDEXED,
            filename,
            category,
            tags_text,
            text_content
        );
        """)


def _sync_fts_row(
    cursor: sqlite3.Cursor,
    doc_id: str,
    filename: str,
    category: str,
    tags: List[str],
    text_content: str,
) -> None:
    tags_text = " ".join(tags)
    try:
        cursor.execute("DELETE FROM documents_fts WHERE id = ?", (doc_id,))
        cursor.execute(
            """
            INSERT INTO documents_fts (id, filename, category, tags_text, text_content)
            VALUES (?, ?, ?, ?, ?)
            """,
            (doc_id, filename, category or "general", tags_text, text_content or ""),
        )
    except Exception as exc:
        print("[DB] FTS sync error:", exc)


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize SQLite schema and apply idempotent migrations without dropping existing rows."""
    target_path = db_path or DB_PATH
    conn = get_db_connection(target_path)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY,
        filename TEXT NOT NULL,
        file_type TEXT NOT NULL,
        file_size INTEGER DEFAULT 0,
        total_pages INTEGER DEFAULT 1,
        text_content TEXT,
        lines_json TEXT,
        avg_confidence REAL DEFAULT 0.0,
        duration_ms REAL DEFAULT 0.0,
        image_preview TEXT,
        source_path TEXT DEFAULT '',
        category TEXT DEFAULT 'general',
        tags_json TEXT DEFAULT '[]',
        ingestion_channel TEXT DEFAULT 'web_upload',
        doc_date TEXT DEFAULT '',
        image_width INTEGER DEFAULT 0,
        image_height INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    );
    """)

    existing_cols = {
        row["name"] for row in cursor.execute("PRAGMA table_info(documents)").fetchall()
    }
    for col_name, col_def in _DOCUMENT_MIGRATION_COLUMNS.items():
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE documents ADD COLUMN {col_name} {col_def}")

    fts_cols = {
        row["name"] for row in cursor.execute("PRAGMA table_info(documents_fts)").fetchall()
    }
    if not fts_cols:
        _create_fts_table(cursor)
    elif "tags_text" not in fts_cols or "category" not in fts_cols:
        cursor.execute("DROP TABLE IF EXISTS documents_fts")
        _create_fts_table(cursor)
        existing_docs = cursor.execute(
            "SELECT id, filename, category, tags_json, text_content FROM documents"
        ).fetchall()
        for row in existing_docs:
            _sync_fts_row(
                cursor,
                row["id"],
                row["filename"],
                row["category"] or "general",
                _parse_tags_json(row["tags_json"]),
                row["text_content"] or "",
            )

    conn.execute("""
    CREATE TABLE IF NOT EXISTS expenses (
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL UNIQUE,
        merchant TEXT DEFAULT '',
        expense_date TEXT DEFAULT '',
        subtotal REAL DEFAULT 0.0,
        vat_amount REAL DEFAULT 0.0,
        total_amount REAL DEFAULT 0.0,
        currency TEXT DEFAULT 'THB',
        expense_category TEXT DEFAULT 'other',
        extraction_method TEXT DEFAULT 'regex',
        raw_json TEXT DEFAULT '{}',
        created_at TEXT,
        updated_at TEXT,
        FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
    );
    """)

    conn.commit()
    conn.close()
    print("[DB] Database initialized at:", target_path)


def save_document(
    filename: str,
    file_type: str,
    text_content: str,
    lines: List[Dict[str, Any]],
    avg_confidence: float = 1.0,
    duration_ms: float = 0.0,
    total_pages: int = 1,
    image_preview: str = "",
    file_size: int = 0,
    doc_id: Optional[str] = None,
    source_path: str = "",
    category: str = "general",
    tags: Optional[Union[str, List[str]]] = None,
    ingestion_channel: str = "web_upload",
    doc_date: str = "",
    image_width: int = 0,
    image_height: int = 0,
    db_path: Optional[str] = None,
) -> str:
    if not doc_id:
        doc_id = str(uuid.uuid4())

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines_json_str = json.dumps(lines, ensure_ascii=False)
    clean_tags = normalize_tags(tags)
    tags_json_str = json.dumps(clean_tags, ensure_ascii=False)
    clean_category = (category or "general").strip() or "general"

    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO documents (
            id, filename, file_type, file_size, total_pages,
            text_content, lines_json, avg_confidence, duration_ms,
            image_preview, source_path, category, tags_json,
            ingestion_channel, doc_date, image_width, image_height,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            filename = excluded.filename,
            file_type = excluded.file_type,
            file_size = excluded.file_size,
            total_pages = excluded.total_pages,
            text_content = excluded.text_content,
            lines_json = excluded.lines_json,
            avg_confidence = excluded.avg_confidence,
            duration_ms = excluded.duration_ms,
            image_preview = excluded.image_preview,
            source_path = excluded.source_path,
            category = excluded.category,
            tags_json = excluded.tags_json,
            ingestion_channel = excluded.ingestion_channel,
            doc_date = excluded.doc_date,
            image_width = excluded.image_width,
            image_height = excluded.image_height,
            updated_at = excluded.updated_at
        """,
        (
            doc_id,
            filename,
            file_type,
            file_size,
            total_pages,
            text_content,
            lines_json_str,
            avg_confidence,
            duration_ms,
            image_preview,
            source_path,
            clean_category,
            tags_json_str,
            ingestion_channel or "web_upload",
            doc_date or "",
            int(image_width or 0),
            int(image_height or 0),
            now,
            now,
        ),
    )

    _sync_fts_row(cursor, doc_id, filename, clean_category, clean_tags, text_content)
    conn.commit()
    conn.close()
    return doc_id


def update_document_metadata(
    doc_id: str,
    text_content: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[Union[str, List[str]]] = None,
    doc_date: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Update editable document fields and synchronize FTS5."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    row = cursor.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not row:
        conn.close()
        return None

    new_text = row["text_content"] if text_content is None else text_content
    new_category = (
        row["category"] or "general"
        if category is None
        else ((category or "general").strip() or "general")
    )
    new_tags = (
        _parse_tags_json(row["tags_json"]) if tags is None else normalize_tags(tags)
    )
    new_doc_date = (row["doc_date"] or "") if doc_date is None else doc_date.strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        """
        UPDATE documents
        SET text_content = ?, category = ?, tags_json = ?, doc_date = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            new_text,
            new_category,
            json.dumps(new_tags, ensure_ascii=False),
            new_doc_date,
            now,
            doc_id,
        ),
    )
    _sync_fts_row(cursor, doc_id, row["filename"], new_category, new_tags, new_text or "")
    conn.commit()
    conn.close()
    return get_document_by_id(doc_id, db_path=db_path)


def search_documents(
    query: Optional[str] = None,
    category: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    clean_query = (query or "").strip()
    clean_category = (category or "").strip()
    clean_tag = (tag or "").strip()

    filter_clauses: List[str] = []
    filter_params: List[Any] = []
    if clean_category and clean_category.lower() != "all":
        filter_clauses.append("d.category = ?")
        filter_params.append(clean_category)
    if clean_tag:
        filter_clauses.append("EXISTS (SELECT 1 FROM json_each(d.tags_json) WHERE value = ?)")
        filter_params.append(clean_tag)

    select_cols = """
        d.id, d.filename, d.file_type, d.total_pages, d.avg_confidence,
        d.duration_ms, d.created_at, d.updated_at, d.image_preview,
        d.source_path, d.category, d.tags_json, d.ingestion_channel, d.doc_date,
        substr(d.text_content, 1, 300) as snippet,
        length(d.text_content) as char_count
    """

    rows = []
    if clean_query:
        used_fts = False
        if len(clean_query) >= 3:
            try:
                fts_term = clean_query.replace('"', '""')
                where_parts = ["documents_fts MATCH ?"] + filter_clauses
                sql = f"""
                SELECT {select_cols}
                FROM documents_fts f
                JOIN documents d ON d.id = f.id
                WHERE {' AND '.join(where_parts)}
                ORDER BY d.created_at DESC
                LIMIT ? OFFSET ?
                """
                rows = cursor.execute(
                    sql, [f'"{fts_term}"', *filter_params, limit, offset]
                ).fetchall()
                used_fts = True
            except Exception:
                used_fts = False

        if not used_fts:
            like_term = f"%{clean_query}%"
            where_parts = [
                "(d.text_content LIKE ? OR d.filename LIKE ? OR d.tags_json LIKE ? OR d.category LIKE ?)"
            ] + filter_clauses
            sql = f"""
            SELECT {select_cols}
            FROM documents d
            WHERE {' AND '.join(where_parts)}
            ORDER BY d.created_at DESC
            LIMIT ? OFFSET ?
            """
            rows = cursor.execute(
                sql, [like_term, like_term, like_term, like_term, *filter_params, limit, offset]
            ).fetchall()
    else:
        where_sql = f"WHERE {' AND '.join(filter_clauses)}" if filter_clauses else ""
        sql = f"""
        SELECT {select_cols}
        FROM documents d
        {where_sql}
        ORDER BY d.created_at DESC
        LIMIT ? OFFSET ?
        """
        rows = cursor.execute(sql, [*filter_params, limit, offset]).fetchall()

    results = []
    for r in rows:
        src_path = r["source_path"] or ""
        results.append(
            {
                "id": r["id"],
                "filename": r["filename"],
                "file_type": r["file_type"],
                "total_pages": r["total_pages"],
                "avg_confidence": round(float(r["avg_confidence"]), 4) if r["avg_confidence"] else 1.0,
                "duration_ms": round(float(r["duration_ms"]), 1) if r["duration_ms"] else 0.0,
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "snippet": r["snippet"] or "",
                "char_count": r["char_count"] or 0,
                "has_image": bool(r["image_preview"]),
                "has_source_file": bool(src_path and os.path.exists(src_path)),
                "category": r["category"] or "general",
                "tags": _parse_tags_json(r["tags_json"]),
                "ingestion_channel": r["ingestion_channel"] or "web_upload",
                "doc_date": r["doc_date"] or "",
            }
        )
    conn.close()
    return results


def get_document_by_id(doc_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    row = cursor.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    conn.close()
    if not row:
        return None

    lines = []
    if row["lines_json"]:
        try:
            lines = json.loads(row["lines_json"])
        except Exception:
            lines = []

    src_path = row["source_path"] or ""
    expense_row = None
    try:
        conn2 = get_db_connection(db_path)
        exp = conn2.execute("SELECT * FROM expenses WHERE document_id = ?", (doc_id,)).fetchone()
        conn2.close()
        if exp:
            expense_row = _format_expense_row(exp)
    except Exception:
        expense_row = None

    return {
        "id": row["id"],
        "filename": row["filename"],
        "file_type": row["file_type"],
        "file_size": row["file_size"],
        "total_pages": row["total_pages"],
        "text": row["text_content"],
        "lines": lines,
        "avg_confidence": row["avg_confidence"],
        "duration_ms": row["duration_ms"],
        "image_preview": row["image_preview"],
        "source_path": src_path,
        "has_source_file": bool(src_path and os.path.exists(src_path)),
        "category": row["category"] or "general",
        "tags": _parse_tags_json(row["tags_json"]),
        "ingestion_channel": row["ingestion_channel"] or "web_upload",
        "doc_date": row["doc_date"] or "",
        "image_width": row["image_width"] or 0,
        "image_height": row["image_height"] or 0,
        "expense": expense_row,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _format_expense_row(row: sqlite3.Row, filename: Optional[str] = None) -> Dict[str, Any]:
    raw_meta = {}
    if row["raw_json"]:
        try:
            raw_meta = json.loads(row["raw_json"])
        except Exception:
            raw_meta = {}
    out = {
        "id": row["id"],
        "document_id": row["document_id"],
        "merchant": row["merchant"] or "",
        "expense_date": row["expense_date"] or "",
        "subtotal": round(float(row["subtotal"] or 0.0), 2),
        "vat_amount": round(float(row["vat_amount"] or 0.0), 2),
        "total_amount": round(float(row["total_amount"] or 0.0), 2),
        "currency": row["currency"] or "THB",
        "expense_category": row["expense_category"] or "other",
        "extraction_method": row["extraction_method"] or "regex",
        "raw_json": raw_meta,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if filename is not None:
        out["filename"] = filename
    return out


def upsert_expense(
    document_id: str,
    *,
    merchant: str = "",
    expense_date: str = "",
    subtotal: float = 0.0,
    vat_amount: float = 0.0,
    total_amount: float = 0.0,
    currency: str = "THB",
    expense_category: str = "other",
    extraction_method: str = "regex",
    raw_json: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Create or update the Expense Record attached to a Vault Document."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    doc_exists = cursor.execute("SELECT id FROM documents WHERE id = ?", (document_id,)).fetchone()
    if not doc_exists:
        conn.close()
        return None

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing = cursor.execute(
        "SELECT id, created_at FROM expenses WHERE document_id = ?", (document_id,)
    ).fetchone()
    expense_id = existing["id"] if existing else str(uuid.uuid4())
    created_at = existing["created_at"] if existing else now
    raw_str = json.dumps(raw_json or {}, ensure_ascii=False)

    cursor.execute(
        """
        INSERT INTO expenses (
            id, document_id, merchant, expense_date, subtotal,
            vat_amount, total_amount, currency, expense_category,
            extraction_method, raw_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(document_id) DO UPDATE SET
            merchant = excluded.merchant,
            expense_date = excluded.expense_date,
            subtotal = excluded.subtotal,
            vat_amount = excluded.vat_amount,
            total_amount = excluded.total_amount,
            currency = excluded.currency,
            expense_category = excluded.expense_category,
            extraction_method = excluded.extraction_method,
            raw_json = excluded.raw_json,
            updated_at = excluded.updated_at
        """,
        (
            expense_id,
            document_id,
            merchant.strip(),
            expense_date.strip(),
            round(float(subtotal or 0.0), 2),
            round(float(vat_amount or 0.0), 2),
            round(float(total_amount or 0.0), 2),
            (currency or "THB").strip() or "THB",
            (expense_category or "other").strip() or "other",
            (extraction_method or "regex").strip() or "regex",
            raw_str,
            created_at,
            now,
        ),
    )
    conn.commit()
    row = cursor.execute(
        """
        SELECT e.*, d.filename
        FROM expenses e
        JOIN documents d ON d.id = e.document_id
        WHERE e.document_id = ?
        """,
        (document_id,),
    ).fetchone()
    conn.close()
    return _format_expense_row(row, filename=row["filename"]) if row else None


def list_expenses(
    month: Optional[str] = None,
    expense_category: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """List Expense Records and compute aggregate totals optionally filtered by YYYY-MM or category."""
    conn = get_db_connection(db_path)
    clauses: List[str] = []
    params: List[Any] = []
    if month and month.strip():
        clauses.append("e.expense_date LIKE ?")
        params.append(f"{month.strip()}%")
    if expense_category and expense_category.strip() and expense_category.strip().lower() != "all":
        clauses.append("e.expense_category = ?")
        params.append(expense_category.strip())

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT e.*, d.filename
        FROM expenses e
        JOIN documents d ON d.id = e.document_id
        {where_sql}
        ORDER BY COALESCE(NULLIF(e.expense_date, ''), e.created_at) DESC, e.created_at DESC
        """,
        params,
    ).fetchall()
    conn.close()

    items = [_format_expense_row(r, filename=r["filename"]) for r in rows]
    total_amount = round(sum(item["total_amount"] for item in items), 2)
    vat_amount = round(sum(item["vat_amount"] for item in items), 2)
    subtotal = round(sum(item["subtotal"] for item in items), 2)
    by_category: Dict[str, float] = {}
    for item in items:
        cat = item["expense_category"] or "other"
        by_category[cat] = round(by_category.get(cat, 0.0) + item["total_amount"], 2)

    return {
        "expenses": items,
        "summary": {
            "count": len(items),
            "subtotal": subtotal,
            "vat_amount": vat_amount,
            "total_amount": total_amount,
            "by_category": by_category,
        },
    }


def delete_expense(document_id: str, db_path: Optional[str] = None) -> bool:
    conn = get_db_connection(db_path)
    cur = conn.execute("DELETE FROM expenses WHERE document_id = ?", (document_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def delete_document(doc_id: str, db_path: Optional[str] = None) -> bool:
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    row = cursor.execute("SELECT source_path FROM documents WHERE id = ?", (doc_id,)).fetchone()
    src_path = row["source_path"] if row else ""
    cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    try:
        cursor.execute("DELETE FROM documents_fts WHERE id = ?", (doc_id,))
    except Exception:
        pass
    conn.commit()
    conn.close()

    if src_path and os.path.exists(src_path):
        try:
            os.remove(src_path)
        except OSError as exc:
            print("[DB] Failed to remove source file:", exc)
    return True


def get_db_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    target_path = db_path or DB_PATH
    conn = get_db_connection(target_path)
    cursor = conn.cursor()
    row = cursor.execute(
        "SELECT COUNT(*) as doc_count, COALESCE(SUM(total_pages), 0) as total_pages, COALESCE(SUM(length(text_content)), 0) as total_chars FROM documents"
    ).fetchone()
    conn.close()
    return {
        "total_documents": row["doc_count"],
        "total_pages": row["total_pages"],
        "total_characters": row["total_chars"],
        "db_path": target_path,
    }
