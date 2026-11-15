import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "documents.db")
UPLOADS_DIR = os.path.join(DB_DIR, "uploads")

os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

SUPPORTED_SCHEMA_VERSION = 4
MAX_SNAPSHOT_BYTES = 100 * 1024 * 1024

_DOCUMENT_MIGRATION_COLUMNS = {
    "source_path": "TEXT DEFAULT ''",
    "source_storage_key": "TEXT DEFAULT ''",
    "source_sha256": "TEXT DEFAULT ''",
    "category": "TEXT DEFAULT 'general'",
    "tags_json": "TEXT DEFAULT '[]'",
    "ingestion_channel": "TEXT DEFAULT 'web_upload'",
    "doc_date": "TEXT DEFAULT ''",
    "image_width": "INTEGER DEFAULT 0",
    "image_height": "INTEGER DEFAULT 0",
    "review_status": "TEXT DEFAULT 'pending_review'",
    "line_user_id": "TEXT DEFAULT ''",
}

_EXPENSE_MIGRATION_COLUMNS = {
    "reimbursement_status": "TEXT DEFAULT 'pending_reimbursement'",
    "budget_source": "TEXT DEFAULT 'classroom'",
}

VALID_REVIEW_STATUSES = {"pending_review", "reviewed"}
VALID_REIMBURSEMENT_STATUSES = {"pending_reimbursement", "reimbursed", "personal"}


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
        source_storage_key TEXT DEFAULT '',
        source_sha256 TEXT DEFAULT '',
        category TEXT DEFAULT 'general',
        tags_json TEXT DEFAULT '[]',
        ingestion_channel TEXT DEFAULT 'web_upload',
        doc_date TEXT DEFAULT '',
        image_width INTEGER DEFAULT 0,
        image_height INTEGER DEFAULT 0,
        review_status TEXT DEFAULT 'pending_review',
        line_user_id TEXT DEFAULT '',
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
        reimbursement_status TEXT DEFAULT 'pending_reimbursement',
        budget_source TEXT DEFAULT 'classroom',
        raw_json TEXT DEFAULT '{}',
        created_at TEXT,
        updated_at TEXT,
        FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
    );
    """)

    existing_exp_cols = {
        row["name"] for row in cursor.execute("PRAGMA table_info(expenses)").fetchall()
    }
    for col_name, col_def in _EXPENSE_MIGRATION_COLUMNS.items():
        if col_name not in existing_exp_cols:
            cursor.execute(f"ALTER TABLE expenses ADD COLUMN {col_name} {col_def}")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id TEXT PRIMARY KEY,
        document_id TEXT UNIQUE,
        full_name TEXT NOT NULL DEFAULT '',
        nickname TEXT DEFAULT '',
        citizen_id TEXT DEFAULT '',
        birth_date TEXT DEFAULT '',
        classroom TEXT DEFAULT 'อนุบาล',
        parent_name TEXT DEFAULT '',
        parent_phone TEXT DEFAULT '',
        allergies TEXT DEFAULT '',
        medical_notes TEXT DEFAULT '',
        extraction_method TEXT DEFAULT 'regex',
        raw_json TEXT DEFAULT '{}',
        created_at TEXT,
        updated_at TEXT,
        FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE SET NULL
    );
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS auth_sessions (
        session_id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        name TEXT DEFAULT '',
        picture TEXT DEFAULT '',
        csrf_token TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked_at TEXT DEFAULT ''
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires_at ON auth_sessions(expires_at)")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS vault_operations (
        operation_id TEXT PRIMARY KEY,
        op_type TEXT NOT NULL,
        target_id TEXT DEFAULT '',
        result_json TEXT DEFAULT '{}',
        revision INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS pending_r2_deletions (
        storage_key TEXT PRIMARY KEY,
        doc_id TEXT DEFAULT '',
        deleted_in_revision INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS teacher_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        teacher_name TEXT NOT NULL,
        classroom TEXT NOT NULL,
        default_reimbursement_status TEXT NOT NULL
            CHECK (default_reimbursement_status IN ('pending_reimbursement', 'personal')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL DEFAULT ''
    );
    """)
    conn.execute(f"PRAGMA user_version = {SUPPORTED_SCHEMA_VERSION}")

    conn.commit()
    conn.close()
    print("[DB] Database initialized at:", target_path)


def get_teacher_settings(db_path: Optional[str] = None, *, teacher_name: str = "ครูพิ้งกี้") -> Dict[str, Any]:
    """Shared vault preferences, with environment/legacy defaults until first save."""
    with closing(get_db_connection(db_path)) as conn:
        row = conn.execute("SELECT * FROM teacher_settings WHERE id = 1").fetchone()
    if row:
        return {**dict(row), "configured": True}
    return {
        "teacher_name": teacher_name, "classroom": "อนุบาล",
        "default_reimbursement_status": "pending_reimbursement",
        "configured": False, "created_at": "", "updated_at": "", "updated_by": "",
    }


def save_teacher_settings(settings: Dict[str, str], *, updated_by: str = "", db_path: Optional[str] = None) -> Dict[str, Any]:
    now = _utc_iso()
    with closing(get_db_connection(db_path)) as conn, conn:
        conn.execute("""
            INSERT INTO teacher_settings
                (id, teacher_name, classroom, default_reimbursement_status, created_at, updated_at, updated_by)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET teacher_name=excluded.teacher_name,
                classroom=excluded.classroom,
                default_reimbursement_status=excluded.default_reimbursement_status,
                updated_at=excluded.updated_at, updated_by=excluded.updated_by
        """, (settings["teacher_name"], settings["classroom"], settings["default_reimbursement_status"], now, now, updated_by))
    return get_teacher_settings(db_path)


def has_any_vault_data(db_path: Optional[str] = None) -> bool:
    """Return True if an existing SQLite file has user data or is a non-empty unreadable file."""
    target_path = db_path or DB_PATH
    if not os.path.exists(target_path):
        return False
    try:
        if os.path.getsize(target_path) <= 0:
            return False
    except OSError:
        return True

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(target_path)
        conn.execute("PRAGMA query_only = ON")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            return True
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        ]
        for table in tables:
            lower = str(table).lower()
            if lower.startswith("sqlite_") or lower.startswith("documents_fts"):
                continue
            row = conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
            if row is not None:
                return True
        return False
    except sqlite3.Error:
        return True
    finally:
        if conn is not None:
            conn.close()


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
    source_storage_key: str = "",
    source_sha256: str = "",
    category: str = "general",
    tags: Optional[Union[str, List[str]]] = None,
    ingestion_channel: str = "web_upload",
    doc_date: str = "",
    image_width: int = 0,
    image_height: int = 0,
    review_status: str = "pending_review",
    line_user_id: str = "",
    db_path: Optional[str] = None,
) -> str:
    if not doc_id:
        doc_id = str(uuid.uuid4())

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines_json_str = json.dumps(lines, ensure_ascii=False)
    clean_tags = normalize_tags(tags)
    tags_json_str = json.dumps(clean_tags, ensure_ascii=False)
    clean_category = (category or "general").strip() or "general"
    clean_review = (
        review_status.strip()
        if isinstance(review_status, str) and review_status.strip() in VALID_REVIEW_STATUSES
        else "pending_review"
    )
    resolved_storage_key = (source_storage_key or "").strip() or (
        os.path.basename(source_path) if source_path else ""
    )
    resolved_sha256 = (source_sha256 or "").strip().lower()

    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO documents (
            id, filename, file_type, file_size, total_pages,
            text_content, lines_json, avg_confidence, duration_ms,
            image_preview, source_path, source_storage_key, source_sha256,
            category, tags_json, ingestion_channel, doc_date,
            image_width, image_height, review_status, line_user_id,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            source_storage_key = excluded.source_storage_key,
            source_sha256 = excluded.source_sha256,
            category = excluded.category,
            tags_json = excluded.tags_json,
            ingestion_channel = excluded.ingestion_channel,
            doc_date = excluded.doc_date,
            image_width = excluded.image_width,
            image_height = excluded.image_height,
            review_status = excluded.review_status,
            line_user_id = excluded.line_user_id,
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
            resolved_storage_key,
            resolved_sha256,
            clean_category,
            tags_json_str,
            ingestion_channel or "web_upload",
            doc_date or "",
            int(image_width or 0),
            int(image_height or 0),
            clean_review,
            (line_user_id or "").strip(),
            now,
            now,
        ),
    )

    _sync_fts_row(cursor, doc_id, filename, clean_category, clean_tags, text_content)
    conn.commit()
    conn.close()
    return doc_id


def _resolve_has_source_file(
    src_path: str,
    storage_key: str,
    db_path: Optional[str],
    r2_enabled: bool,
) -> bool:
    target_db = db_path or DB_PATH
    uploads_dir = os.path.join(os.path.dirname(os.path.abspath(target_db)), "uploads")
    local_key_path = os.path.join(uploads_dir, storage_key) if storage_key else ""
    local_exists = bool(
        (src_path and os.path.exists(src_path))
        or (local_key_path and os.path.exists(local_key_path))
    )
    if r2_enabled:
        return bool(storage_key or local_exists)
    return local_exists


def update_document_metadata(
    doc_id: str,
    text_content: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[Union[str, List[str]]] = None,
    doc_date: Optional[str] = None,
    review_status: Optional[str] = None,
    db_path: Optional[str] = None,
    r2_enabled: bool = False,
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
    existing_review = (row["review_status"] if "review_status" in row.keys() else None) or "pending_review"
    if review_status is not None and review_status.strip() in VALID_REVIEW_STATUSES:
        new_review_status = review_status.strip()
    else:
        new_review_status = existing_review

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        """
        UPDATE documents
        SET text_content = ?, category = ?, tags_json = ?, doc_date = ?, review_status = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            new_text,
            new_category,
            json.dumps(new_tags, ensure_ascii=False),
            new_doc_date,
            new_review_status,
            now,
            doc_id,
        ),
    )
    _sync_fts_row(cursor, doc_id, row["filename"], new_category, new_tags, new_text or "")
    conn.commit()
    conn.close()
    return get_document_by_id(doc_id, db_path=db_path, r2_enabled=r2_enabled)


def search_documents(
    query: Optional[str] = None,
    category: Optional[str] = None,
    tag: Optional[str] = None,
    review_status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db_path: Optional[str] = None,
    r2_enabled: bool = False,
) -> List[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    clean_query = (query or "").strip()
    clean_category = (category or "").strip()
    clean_tag = (tag or "").strip()
    clean_review = (review_status or "").strip()

    filter_clauses: List[str] = []
    filter_params: List[Any] = []
    if clean_category and clean_category.lower() != "all":
        filter_clauses.append("d.category = ?")
        filter_params.append(clean_category)
    if clean_tag:
        filter_clauses.append("EXISTS (SELECT 1 FROM json_each(d.tags_json) WHERE value = ?)")
        filter_params.append(clean_tag)
    if clean_review and clean_review.lower() != "all":
        filter_clauses.append("COALESCE(NULLIF(d.review_status, ''), 'pending_review') = ?")
        filter_params.append(clean_review)

    select_cols = """
        d.id, d.filename, d.file_type, d.total_pages, d.avg_confidence,
        d.duration_ms, d.created_at, d.updated_at, d.image_preview,
        d.source_path, d.source_storage_key, d.source_sha256,
        d.category, d.tags_json, d.ingestion_channel, d.doc_date,
        d.review_status, d.line_user_id,
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
        row_keys = set(r.keys())
        src_path = r["source_path"] or ""
        storage_key = (
            (r["source_storage_key"] if "source_storage_key" in row_keys else "") or ""
        ).strip() or (os.path.basename(src_path) if src_path else "")
        source_sha256 = ((r["source_sha256"] if "source_sha256" in row_keys else "") or "").strip()
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
                "source_storage_key": storage_key,
                "source_sha256": source_sha256,
                "has_source_file": _resolve_has_source_file(src_path, storage_key, db_path, r2_enabled),
                "category": r["category"] or "general",
                "tags": _parse_tags_json(r["tags_json"]),
                "ingestion_channel": r["ingestion_channel"] or "web_upload",
                "doc_date": r["doc_date"] or "",
                "review_status": r["review_status"] or "pending_review",
                "line_user_id": r["line_user_id"] or "",
            }
        )
    conn.close()
    return results


def _format_student_row(row: sqlite3.Row) -> Dict[str, Any]:
    raw_meta = {}
    if "raw_json" in row.keys() and row["raw_json"]:
        try:
            raw_meta = json.loads(row["raw_json"])
        except Exception:
            raw_meta = {}
    return {
        "id": row["id"],
        "document_id": row["document_id"] or "",
        "full_name": row["full_name"] or "",
        "nickname": row["nickname"] or "",
        "citizen_id": row["citizen_id"] or "",
        "birth_date": row["birth_date"] or "",
        "classroom": row["classroom"] or "อนุบาล",
        "parent_name": row["parent_name"] or "",
        "parent_phone": row["parent_phone"] or "",
        "allergies": row["allergies"] or "",
        "medical_notes": row["medical_notes"] or "",
        "extraction_method": row["extraction_method"] or "regex",
        "raw_json": raw_meta,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_document_by_id(
    doc_id: str,
    db_path: Optional[str] = None,
    r2_enabled: bool = False,
) -> Optional[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        row = cursor.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not row:
            return None

        lines = []
        if row["lines_json"]:
            try:
                lines = json.loads(row["lines_json"])
            except Exception:
                lines = []

        row_keys = set(row.keys())
        src_path = row["source_path"] or ""
        storage_key = (
            (row["source_storage_key"] if "source_storage_key" in row_keys else "") or ""
        ).strip() or (os.path.basename(src_path) if src_path else "")
        source_sha256 = ((row["source_sha256"] if "source_sha256" in row_keys else "") or "").strip()

        exp = cursor.execute("SELECT * FROM expenses WHERE document_id = ?", (doc_id,)).fetchone()
        expense_row = _format_expense_row(exp) if exp else None

        stu = cursor.execute("SELECT * FROM students WHERE document_id = ?", (doc_id,)).fetchone()
        student_row = _format_student_row(stu) if stu else None

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
            "source_storage_key": storage_key,
            "source_sha256": source_sha256,
            "has_source_file": _resolve_has_source_file(src_path, storage_key, db_path, r2_enabled),
            "category": row["category"] or "general",
            "tags": _parse_tags_json(row["tags_json"]),
            "ingestion_channel": row["ingestion_channel"] or "web_upload",
            "doc_date": row["doc_date"] or "",
            "image_width": row["image_width"] or 0,
            "image_height": row["image_height"] or 0,
            "review_status": (row["review_status"] if "review_status" in row_keys else None) or "pending_review",
            "line_user_id": (row["line_user_id"] if "line_user_id" in row_keys else None) or "",
            "expense": expense_row,
            "student": student_row,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def _format_expense_row(
    row: sqlite3.Row,
    filename: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    raw_meta = {}
    if row["raw_json"]:
        try:
            raw_meta = json.loads(row["raw_json"])
        except Exception:
            raw_meta = {}
    row_keys = set(row.keys())
    resolved_tags = (
        tags
        if tags is not None
        else (_parse_tags_json(row["tags_json"]) if "tags_json" in row_keys else [])
    )
    reimb_status = (
        (row["reimbursement_status"] if "reimbursement_status" in row_keys else None)
        or "pending_reimbursement"
    )
    budget_source = (
        (row["budget_source"] if "budget_source" in row_keys else None)
        or "classroom"
    )
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
        "reimbursement_status": reimb_status,
        "budget_source": budget_source,
        "tags": resolved_tags,
        "raw_json": raw_meta,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if filename is not None:
        out["filename"] = filename
    return out


def _default_reimbursement_status(
    expense_category: str, doc_tags: List[str], *, db_path: Optional[str] = None
) -> str:
    if "ส่วนตัว" in doc_tags and expense_category not in {"classroom", "kids_snack"}:
        return "personal"
    settings = get_teacher_settings(db_path)
    if settings["configured"]:
        return settings["default_reimbursement_status"]
    return "pending_reimbursement"


def _default_budget_source(expense_category: str, reimbursement_status: str) -> str:
    if reimbursement_status == "personal":
        return "personal"
    if expense_category in {"classroom", "kids_snack"}:
        return expense_category
    if expense_category == "office":
        return "subsidy"
    return "school_project"


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
    reimbursement_status: Optional[str] = None,
    budget_source: Optional[str] = None,
    raw_json: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Create or update the Expense Record attached to a Vault Document."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    doc_row = cursor.execute("SELECT id, tags_json FROM documents WHERE id = ?", (document_id,)).fetchone()
    if not doc_row:
        conn.close()
        return None

    doc_tags = _parse_tags_json(doc_row["tags_json"])
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing = cursor.execute(
        "SELECT id, created_at, reimbursement_status, budget_source FROM expenses WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    expense_id = existing["id"] if existing else str(uuid.uuid4())
    created_at = existing["created_at"] if existing else now
    clean_exp_cat = (expense_category or "other").strip() or "other"

    if reimbursement_status and reimbursement_status.strip() in VALID_REIMBURSEMENT_STATUSES:
        resolved_reimb = reimbursement_status.strip()
    elif existing and existing["reimbursement_status"]:
        resolved_reimb = existing["reimbursement_status"]
    else:
        resolved_reimb = _default_reimbursement_status(clean_exp_cat, doc_tags, db_path=db_path)

    if budget_source and budget_source.strip():
        resolved_budget = budget_source.strip()
    elif existing and existing["budget_source"]:
        resolved_budget = existing["budget_source"]
    else:
        resolved_budget = _default_budget_source(clean_exp_cat, resolved_reimb)

    raw_str = json.dumps(raw_json or {}, ensure_ascii=False)

    cursor.execute(
        """
        INSERT INTO expenses (
            id, document_id, merchant, expense_date, subtotal,
            vat_amount, total_amount, currency, expense_category,
            extraction_method, reimbursement_status, budget_source,
            raw_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(document_id) DO UPDATE SET
            merchant = excluded.merchant,
            expense_date = excluded.expense_date,
            subtotal = excluded.subtotal,
            vat_amount = excluded.vat_amount,
            total_amount = excluded.total_amount,
            currency = excluded.currency,
            expense_category = excluded.expense_category,
            extraction_method = excluded.extraction_method,
            reimbursement_status = excluded.reimbursement_status,
            budget_source = excluded.budget_source,
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
            clean_exp_cat,
            (extraction_method or "regex").strip() or "regex",
            resolved_reimb,
            resolved_budget,
            raw_str,
            created_at,
            now,
        ),
    )
    conn.commit()
    row = cursor.execute(
        """
        SELECT e.*, d.filename, d.tags_json
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
    tag: Optional[str] = None,
    reimbursement_status: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """List Expense Records and compute aggregate totals optionally filtered by YYYY-MM, category, tag, or reimbursement_status."""
    conn = get_db_connection(db_path)
    clauses: List[str] = []
    params: List[Any] = []
    if month and month.strip():
        clauses.append("e.expense_date LIKE ?")
        params.append(f"{month.strip()}%")
    if expense_category and expense_category.strip() and expense_category.strip().lower() != "all":
        clauses.append("e.expense_category = ?")
        params.append(expense_category.strip())
    if tag and tag.strip() and tag.strip().lower() != "all":
        clauses.append("EXISTS (SELECT 1 FROM json_each(d.tags_json) WHERE value = ?)")
        params.append(tag.strip())
    if reimbursement_status and reimbursement_status.strip() and reimbursement_status.strip().lower() != "all":
        clauses.append("COALESCE(NULLIF(e.reimbursement_status, ''), 'pending_reimbursement') = ?")
        params.append(reimbursement_status.strip())

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT e.*, d.filename, d.tags_json
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
    pending_items = [i for i in items if i["reimbursement_status"] == "pending_reimbursement"]
    reimbursed_items = [i for i in items if i["reimbursement_status"] == "reimbursed"]
    personal_items = [i for i in items if i["reimbursement_status"] == "personal"]

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
            "pending_reimbursement_count": len(pending_items),
            "pending_reimbursement_total": round(sum(i["total_amount"] for i in pending_items), 2),
            "reimbursed_total": round(sum(i["total_amount"] for i in reimbursed_items), 2),
            "personal_total": round(sum(i["total_amount"] for i in personal_items), 2),
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


def upsert_student(
    *,
    student_id: Optional[str] = None,
    document_id: Optional[str] = None,
    full_name: str = "",
    nickname: str = "",
    citizen_id: str = "",
    birth_date: str = "",
    classroom: str = "อนุบาล",
    parent_name: str = "",
    parent_phone: str = "",
    allergies: str = "",
    medical_notes: str = "",
    extraction_method: str = "regex",
    raw_json: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Create or update a kindergarten Student Profile, optionally linked to a Vault Document."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    clean_doc_id = (document_id or "").strip() or None
    if clean_doc_id:
        doc_exists = cursor.execute("SELECT id FROM documents WHERE id = ?", (clean_doc_id,)).fetchone()
        if not doc_exists:
            clean_doc_id = None

    existing = None
    if student_id:
        existing = cursor.execute("SELECT id, created_at FROM students WHERE id = ?", (student_id,)).fetchone()
    if not existing and clean_doc_id:
        existing = cursor.execute("SELECT id, created_at FROM students WHERE document_id = ?", (clean_doc_id,)).fetchone()

    resolved_id = existing["id"] if existing else (student_id or str(uuid.uuid4()))
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    created_at = existing["created_at"] if existing else now
    raw_str = json.dumps(raw_json or {}, ensure_ascii=False)

    cursor.execute(
        """
        INSERT INTO students (
            id, document_id, full_name, nickname, citizen_id,
            birth_date, classroom, parent_name, parent_phone,
            allergies, medical_notes, extraction_method, raw_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            document_id = COALESCE(excluded.document_id, students.document_id),
            full_name = excluded.full_name,
            nickname = excluded.nickname,
            citizen_id = excluded.citizen_id,
            birth_date = excluded.birth_date,
            classroom = excluded.classroom,
            parent_name = excluded.parent_name,
            parent_phone = excluded.parent_phone,
            allergies = excluded.allergies,
            medical_notes = excluded.medical_notes,
            extraction_method = excluded.extraction_method,
            raw_json = excluded.raw_json,
            updated_at = excluded.updated_at
        """,
        (
            resolved_id,
            clean_doc_id,
            full_name.strip(),
            nickname.strip(),
            citizen_id.strip(),
            birth_date.strip(),
            (classroom or "อนุบาล").strip() or "อนุบาล",
            parent_name.strip(),
            parent_phone.strip(),
            allergies.strip(),
            medical_notes.strip(),
            (extraction_method or "regex").strip() or "regex",
            raw_str,
            created_at,
            now,
        ),
    )
    conn.commit()
    row = cursor.execute("SELECT * FROM students WHERE id = ?", (resolved_id,)).fetchone()
    conn.close()
    return _format_student_row(row)


def list_students(
    query: Optional[str] = None,
    classroom: Optional[str] = None,
    has_allergy: Optional[bool] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """List kindergarten Student Profiles with optional search, classroom, and allergy filters."""
    conn = get_db_connection(db_path)
    clauses: List[str] = []
    params: List[Any] = []

    clean_query = (query or "").strip()
    if clean_query:
        like_term = f"%{clean_query}%"
        clauses.append(
            "(full_name LIKE ? OR nickname LIKE ? OR citizen_id LIKE ? OR parent_name LIKE ? OR parent_phone LIKE ? OR allergies LIKE ? OR medical_notes LIKE ?)"
        )
        params.extend([like_term] * 7)

    clean_class = (classroom or "").strip()
    if clean_class and clean_class.lower() != "all":
        clauses.append("classroom = ?")
        params.append(clean_class)

    if has_allergy is True:
        clauses.append("(TRIM(allergies) != '' OR TRIM(medical_notes) != '')")
    elif has_allergy is False:
        clauses.append("(TRIM(allergies) = '' AND TRIM(medical_notes) = '')")

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM students {where_sql} ORDER BY classroom ASC, full_name ASC, created_at DESC",
        params,
    ).fetchall()
    conn.close()

    items = [_format_student_row(r) for r in rows]
    allergy_count = sum(1 for item in items if item["allergies"] or item["medical_notes"])
    by_classroom: Dict[str, int] = {}
    for item in items:
        cls_name = item["classroom"] or "อนุบาล"
        by_classroom[cls_name] = by_classroom.get(cls_name, 0) + 1

    return {
        "students": items,
        "summary": {
            "total_students": len(items),
            "allergy_count": allergy_count,
            "by_classroom": by_classroom,
        },
    }


def get_student_by_id(student_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    row = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    conn.close()
    return _format_student_row(row) if row else None


def get_student_by_document_id(document_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    row = conn.execute("SELECT * FROM students WHERE document_id = ?", (document_id,)).fetchone()
    conn.close()
    return _format_student_row(row) if row else None


def delete_student(student_id: str, db_path: Optional[str] = None) -> bool:
    conn = get_db_connection(db_path)
    cur = conn.execute("DELETE FROM students WHERE id = ?", (student_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def delete_document(
    doc_id: str,
    db_path: Optional[str] = None,
    *,
    defer_file_removal: bool = False,
    deleted_in_revision: int = 0,
    removed_paths_out: Optional[List[str]] = None,
) -> bool:
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    row = cursor.execute(
        "SELECT source_path, source_storage_key FROM documents WHERE id = ?",
        (doc_id,),
    ).fetchone()
    if not row:
        conn.close()
        return False

    src_path = row["source_path"] or ""
    row_keys = set(row.keys())
    storage_key = (
        (row["source_storage_key"] if "source_storage_key" in row_keys else "") or ""
    ).strip() or (os.path.basename(src_path) if src_path else "")

    target_db = db_path or DB_PATH
    uploads_dir = os.path.join(os.path.dirname(os.path.abspath(target_db)), "uploads")
    candidate_paths: List[str] = []
    if src_path:
        candidate_paths.append(src_path)
    if storage_key:
        key_path = os.path.join(uploads_dir, storage_key)
        if key_path not in candidate_paths:
            candidate_paths.append(key_path)

    if defer_file_removal and storage_key:
        cursor.execute(
            """
            INSERT OR REPLACE INTO pending_r2_deletions (
                storage_key, doc_id, deleted_in_revision, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (storage_key, doc_id, int(deleted_in_revision), _utc_iso()),
        )

    cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    try:
        cursor.execute("DELETE FROM documents_fts WHERE id = ?", (doc_id,))
    except Exception:
        pass
    conn.commit()
    conn.close()

    if removed_paths_out is not None:
        removed_paths_out.extend(candidate_paths)

    if not defer_file_removal:
        for path in candidate_paths:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError as exc:
                    print("[DB] Failed to remove source file:", exc)
    return True


def get_db_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    target_path = db_path or DB_PATH
    conn = get_db_connection(target_path)
    cursor = conn.cursor()
    row = cursor.execute(
        """
        SELECT
            COUNT(*) as doc_count,
            COALESCE(SUM(total_pages), 0) as total_pages,
            COALESCE(SUM(length(text_content)), 0) as total_chars,
            COALESCE(SUM(CASE WHEN COALESCE(NULLIF(review_status, ''), 'pending_review') = 'pending_review' THEN 1 ELSE 0 END), 0) as pending_review_count
        FROM documents
        """
    ).fetchone()
    stu_row = cursor.execute(
        """
        SELECT
            COUNT(*) as total_students,
            COALESCE(SUM(CASE WHEN TRIM(allergies) != '' OR TRIM(medical_notes) != '' THEN 1 ELSE 0 END), 0) as allergy_student_count
        FROM students
        """
    ).fetchone()
    exp_row = cursor.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN COALESCE(NULLIF(reimbursement_status, ''), 'pending_reimbursement') = 'pending_reimbursement' THEN total_amount ELSE 0 END), 0.0) as pending_reimbursement_total
        FROM expenses
        """
    ).fetchone()
    conn.close()
    return {
        "total_documents": row["doc_count"],
        "total_pages": row["total_pages"],
        "total_characters": row["total_chars"],
        "pending_review_count": row["pending_review_count"],
        "total_students": stu_row["total_students"] if stu_row else 0,
        "allergy_student_count": stu_row["allergy_student_count"] if stu_row else 0,
        "pending_reimbursement_total": round(float(exp_row["pending_reimbursement_total"] or 0.0), 2) if exp_row else 0.0,
        "db_path": target_path,
    }


def _utc_iso(dt: Optional[datetime] = None) -> str:
    target = dt or datetime.now(timezone.utc)
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    else:
        target = target.astimezone(timezone.utc)
    return target.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc_dt(raw: str) -> Optional[datetime]:
    cleaned = (raw or "").strip()
    if not cleaned:
        return None
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def purge_expired_auth_sessions(now_iso: Optional[str] = None, db_path: Optional[str] = None) -> int:
    """Delete expired or revoked auth sessions older than now_iso."""
    now_dt = _parse_utc_dt(now_iso) if now_iso else datetime.now(timezone.utc)
    cutoff = _utc_iso(now_dt)
    conn = get_db_connection(db_path)
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM auth_sessions WHERE expires_at <= ?",
        (cutoff,),
    )
    removed = cur.rowcount or 0
    conn.commit()
    conn.close()
    return removed


def create_auth_session(
    session_id: str,
    email: str,
    name: str,
    picture: str,
    csrf_token: str,
    expires_at: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist a new revocable auth session and purge expired sessions."""
    now_str = _utc_iso()
    exp_dt = _parse_utc_dt(expires_at)
    exp_str = _utc_iso(exp_dt) if exp_dt is not None else expires_at
    clean_email = (email or "").strip().lower()

    conn = get_db_connection(db_path)
    conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now_str,))
    conn.execute(
        """
        INSERT INTO auth_sessions (
            session_id, email, name, picture, csrf_token, created_at, expires_at, revoked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, '')
        """,
        (session_id, clean_email, name or "", picture or "", csrf_token, now_str, exp_str),
    )
    conn.commit()
    conn.close()
    return {
        "session_id": session_id,
        "email": clean_email,
        "name": name or "",
        "picture": picture or "",
        "csrf_token": csrf_token,
        "created_at": now_str,
        "expires_at": exp_str,
        "revoked_at": "",
    }


def get_active_auth_session(
    session_id: str,
    now_iso: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch an unrevoked, unexpired auth session row or None."""
    if not session_id:
        return None
    now_dt = _parse_utc_dt(now_iso) if now_iso else datetime.now(timezone.utc)
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)

    conn = get_db_connection(db_path)
    row = conn.execute(
        """
        SELECT session_id, email, name, picture, csrf_token, created_at, expires_at, revoked_at
        FROM auth_sessions
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    if (row["revoked_at"] or "").strip():
        return None
    exp_dt = _parse_utc_dt(row["expires_at"])
    if exp_dt is None or exp_dt <= now_dt:
        return None
    return dict(row)


def revoke_auth_session(
    session_id: str,
    revoked_at: Optional[str] = None,
    db_path: Optional[str] = None,
) -> bool:
    """Mark an auth session as revoked immediately; returns True if an active row was updated."""
    if not session_id:
        return False
    rev_dt = _parse_utc_dt(revoked_at) if revoked_at else datetime.now(timezone.utc)
    rev_str = _utc_iso(rev_dt)
    conn = get_db_connection(db_path)
    cur = conn.execute(
        """
        UPDATE auth_sessions
        SET revoked_at = ?
        WHERE session_id = ? AND COALESCE(revoked_at, '') = ''
        """,
        (rev_str, session_id),
    )
    updated = (cur.rowcount or 0) > 0
    conn.commit()
    conn.close()
    return updated


def record_vault_operation(
    operation_id: str,
    op_type: str,
    target_id: str,
    result_obj: Optional[Dict[str, Any]],
    revision: int,
    db_path: Optional[str] = None,
    *,
    payload_hash: str = "",
) -> None:
    """Persist an idempotency record for a completed vault mutation."""
    clean_op_id = (operation_id or "").strip()
    if not clean_op_id:
        return
    stored_obj = dict(result_obj or {})
    clean_payload_hash = (payload_hash or "").strip().lower()
    if clean_payload_hash:
        stored_obj["_payload_hash"] = clean_payload_hash
    conn = get_db_connection(db_path)
    conn.execute(
        """
        INSERT OR REPLACE INTO vault_operations (
            operation_id, op_type, target_id, result_json, revision, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            clean_op_id,
            (op_type or "").strip(),
            (target_id or "").strip(),
            json.dumps(stored_obj, ensure_ascii=False),
            int(revision),
            _utc_iso(),
        ),
    )
    conn.commit()
    conn.close()


def get_vault_operation(
    operation_id: str,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve a previously committed idempotent operation result if recorded."""
    clean_op_id = (operation_id or "").strip()
    if not clean_op_id:
        return None
    conn = get_db_connection(db_path)
    row = conn.execute(
        """
        SELECT operation_id, op_type, target_id, result_json, revision, created_at
        FROM vault_operations
        WHERE operation_id = ?
        """,
        (clean_op_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    parsed_result: Dict[str, Any] = {}
    payload_hash = ""
    if row["result_json"]:
        try:
            loaded = json.loads(row["result_json"])
            if isinstance(loaded, dict):
                payload_hash = str(loaded.pop("_payload_hash", "") or "").strip().lower()
                parsed_result = loaded
        except Exception:
            parsed_result = {}
    return {
        "operation_id": row["operation_id"],
        "op_type": row["op_type"],
        "target_id": row["target_id"],
        "result": parsed_result,
        "payload_hash": payload_hash,
        "revision": int(row["revision"] or 0),
        "created_at": row["created_at"],
    }


def list_pending_r2_deletions(
    max_revision: int,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List pending R2 source object deletions safe to purge once older snapshots expire."""
    conn = get_db_connection(db_path)
    rows = conn.execute(
        """
        SELECT storage_key, doc_id, deleted_in_revision, created_at
        FROM pending_r2_deletions
        WHERE deleted_in_revision <= ?
        ORDER BY deleted_in_revision ASC, created_at ASC
        """,
        (int(max_revision),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def remove_pending_r2_deletion(
    storage_key: str,
    db_path: Optional[str] = None,
) -> None:
    clean_key = (storage_key or "").strip()
    if not clean_key:
        return
    conn = get_db_connection(db_path)
    conn.execute("DELETE FROM pending_r2_deletions WHERE storage_key = ?", (clean_key,))
    conn.commit()
    conn.close()


def remove_sqlite_sidecars(db_path: str) -> None:
    """Remove SQLite journal/WAL/SHM sidecar files before replacing the main DB file."""
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = f"{db_path}{suffix}"
        if os.path.exists(sidecar):
            os.remove(sidecar)


def _compute_file_sha256(path: str) -> tuple[int, str]:
    hasher = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            size += len(chunk)
            hasher.update(chunk)
    return size, hasher.hexdigest()


def export_db_snapshot(db_path: str, dest_path: str) -> Dict[str, Any]:
    """Create a consistent standalone SQLite snapshot using sqlite3.Connection.backup()."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Source database does not exist: {db_path}")
    parent = os.path.dirname(os.path.abspath(dest_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.exists(dest_path):
        os.remove(dest_path)
    remove_sqlite_sidecars(dest_path)

    src_conn: Optional[sqlite3.Connection] = None
    dst_conn: Optional[sqlite3.Connection] = None
    try:
        src_conn = sqlite3.connect(db_path)
        dst_conn = sqlite3.connect(dest_path)
        src_conn.backup(dst_conn)
        integrity = dst_conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError(f"Snapshot integrity check failed: {integrity}")
        fk_errors = dst_conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise ValueError(f"Snapshot foreign key check failed ({len(fk_errors)} violations)")
        version_row = dst_conn.execute("PRAGMA user_version").fetchone()
        schema_version = int(version_row[0]) if version_row else SUPPORTED_SCHEMA_VERSION
    finally:
        if dst_conn is not None:
            dst_conn.close()
        if src_conn is not None:
            src_conn.close()

    remove_sqlite_sidecars(dest_path)
    size, sha256_hex = _compute_file_sha256(dest_path)
    if size <= 0 or size > MAX_SNAPSHOT_BYTES:
        raise ValueError(f"Exported snapshot size out of bounds: {size}")
    return {
        "size": size,
        "sha256": sha256_hex,
        "schema_version": schema_version,
    }


def validate_sqlite_snapshot_file(
    snapshot_path: str,
    expected_size: Optional[int] = None,
    expected_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate snapshot byte size, SHA-256, SQLite integrity, foreign keys, and schema version."""
    if not os.path.isfile(snapshot_path):
        raise ValueError("Snapshot file does not exist")

    size, sha256_hex = _compute_file_sha256(snapshot_path)
    if size <= 0 or size > MAX_SNAPSHOT_BYTES:
        raise ValueError(f"Snapshot size invalid or exceeds limit: {size}")
    if expected_size is not None and size != int(expected_size):
        raise ValueError(f"Snapshot size mismatch: expected {expected_size}, got {size}")
    if expected_sha256 is not None and sha256_hex != expected_sha256.strip().lower():
        raise ValueError("Snapshot SHA-256 checksum mismatch")

    ro_uri = f"file:{os.path.abspath(snapshot_path)}?mode=ro"
    conn = sqlite3.connect(ro_uri, uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError(f"SQLite integrity check failed: {integrity}")
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise ValueError(f"SQLite foreign key check failed ({len(fk_errors)} violations)")
        version_row = conn.execute("PRAGMA user_version").fetchone()
        user_version = int(version_row[0]) if version_row else 0
        if user_version > SUPPORTED_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported future schema version {user_version} > {SUPPORTED_SCHEMA_VERSION}"
            )
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        required_tables = {"documents", "expenses", "students"}
        if user_version >= 4:
            required_tables.add("teacher_settings")
        missing = required_tables - tables
        if missing:
            raise ValueError(f"Snapshot missing required tables: {sorted(missing)}")
    finally:
        conn.close()

    return {
        "size": size,
        "sha256": sha256_hex,
        "user_version": user_version,
    }
