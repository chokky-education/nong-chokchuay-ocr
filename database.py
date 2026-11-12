import os
import sqlite3
import json
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "documents.db")
UPLOADS_DIR = os.path.join(DB_DIR, "uploads")

os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    target_path = db_path or DB_PATH
    parent = os.path.dirname(os.path.abspath(target_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db(db_path: Optional[str] = None):
    target_path = db_path or DB_PATH
    conn = get_db_connection(target_path)
    cursor = conn.cursor()
    
    # Documents table
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
        created_at TEXT,
        updated_at TEXT
    );
    """)

    # Trigram Full-Text Search Virtual Table for Thai + English
    try:
        cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            id UNINDEXED,
            filename,
            text_content,
            tokenize="trigram"
        );
        """)
    except Exception as e:
        print("[DB Warning] Trigram FTS5 initialization error, falling back to standard FTS:", e)
        cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            id UNINDEXED,
            filename,
            text_content
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
    db_path: Optional[str] = None
) -> str:
    if not doc_id:
        doc_id = str(uuid.uuid4())
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines_json_str = json.dumps(lines, ensure_ascii=False)
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # Insert or replace in main table
    cursor.execute("""
    INSERT OR REPLACE INTO documents (
        id, filename, file_type, file_size, total_pages,
        text_content, lines_json, avg_confidence, duration_ms,
        image_preview, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        doc_id, filename, file_type, file_size, total_pages,
        text_content, lines_json_str, avg_confidence, duration_ms,
        image_preview, now, now
    ))

    # Update FTS index
    try:
        cursor.execute("DELETE FROM documents_fts WHERE id = ?", (doc_id,))
        cursor.execute("""
        INSERT INTO documents_fts (id, filename, text_content)
        VALUES (?, ?, ?)
        """, (doc_id, filename, text_content))
    except Exception as e:
        print("[DB] FTS sync error:", e)

    conn.commit()
    conn.close()
    return doc_id

def search_documents(
    query: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    query = (query or "").strip()

    if query:
        # Use FTS trigram search with fallback to LIKE
        try:
            # Escape quotes for FTS
            fts_term = query.replace('"', '""')
            cursor.execute("""
            SELECT d.id, d.filename, d.file_type, d.total_pages, d.avg_confidence,
                   d.duration_ms, d.created_at, d.image_preview,
                   substr(d.text_content, 1, 300) as snippet,
                   length(d.text_content) as char_count
            FROM documents_fts f
            JOIN documents d ON d.id = f.id
            WHERE documents_fts MATCH ?
            ORDER BY d.created_at DESC
            LIMIT ? OFFSET ?
            """, (f'"{fts_term}"', limit, offset))
            rows = cursor.fetchall()
        except Exception:
            # Fallback to standard LIKE
            like_term = f"%{query}%"
            cursor.execute("""
            SELECT id, filename, file_type, total_pages, avg_confidence,
                   duration_ms, created_at, image_preview,
                   substr(text_content, 1, 300) as snippet,
                   length(text_content) as char_count
            FROM documents
            WHERE text_content LIKE ? OR filename LIKE ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """, (like_term, like_term, limit, offset))
            rows = cursor.fetchall()
    else:
        cursor.execute("""
        SELECT id, filename, file_type, total_pages, avg_confidence,
               duration_ms, created_at, image_preview,
               substr(text_content, 1, 300) as snippet,
               length(text_content) as char_count
        FROM documents
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """, (limit, offset))
        rows = cursor.fetchall()

    results = []
    for r in rows:
        results.append({
            "id": r["id"],
            "filename": r["filename"],
            "file_type": r["file_type"],
            "total_pages": r["total_pages"],
            "avg_confidence": round(float(r["avg_confidence"]), 4) if r["avg_confidence"] else 1.0,
            "duration_ms": round(float(r["duration_ms"]), 1) if r["duration_ms"] else 0.0,
            "created_at": r["created_at"],
            "snippet": r["snippet"] or "",
            "char_count": r["char_count"] or 0,
            "has_image": bool(r["image_preview"])
        })
    conn.close()
    return results

def get_document_by_id(doc_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None

    lines = []
    if row["lines_json"]:
        try:
            lines = json.loads(row["lines_json"])
        except Exception:
            lines = []

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
        "created_at": row["created_at"],
        "updated_at": row["updated_at"]
    }

def delete_document(doc_id: str, db_path: Optional[str] = None) -> bool:
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    try:
        cursor.execute("DELETE FROM documents_fts WHERE id = ?", (doc_id,))
    except Exception:
        pass
    conn.commit()
    conn.close()
    return True

def get_db_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    target_path = db_path or DB_PATH
    conn = get_db_connection(target_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as doc_count, COALESCE(SUM(total_pages), 0) as total_pages, COALESCE(SUM(length(text_content)), 0) as total_chars FROM documents")
    row = cursor.fetchone()
    conn.close()
    return {
        "total_documents": row["doc_count"],
        "total_pages": row["total_pages"],
        "total_characters": row["total_chars"],
        "db_path": target_path
    }
