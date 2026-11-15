import csv
import hashlib
import io
import json
import os
import posixpath
import re
import sys
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "model")
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, MODEL_DIR)

import auth_service
import database
import extractor
import line_service
import ocr_service
import storage_service
from config import AppConfig
from runtime.line_engine import LineOCR
from storage_service import R2ClientProtocol, VaultStorageCoordinator
from watcher import InboxWatcher


class DocumentUpdateRequest(BaseModel):
    text: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    doc_date: Optional[str] = None
    review_status: Optional[str] = None


class DocumentReviewRequest(BaseModel):
    review_status: str = "reviewed"
    reimbursement_status: Optional[str] = None


class ExpenseUpsertRequest(BaseModel):
    merchant: str = ""
    expense_date: str = ""
    subtotal: float = 0.0
    vat_amount: float = 0.0
    total_amount: float = 0.0
    currency: str = "THB"
    expense_category: str = "other"
    reimbursement_status: Optional[str] = None
    budget_source: Optional[str] = None


class StudentUpsertRequest(BaseModel):
    document_id: Optional[str] = None
    full_name: str = ""
    nickname: str = ""
    citizen_id: str = ""
    birth_date: str = ""
    classroom: str = "อนุบาล"
    parent_name: str = ""
    parent_phone: str = ""
    allergies: str = ""
    medical_notes: str = ""


_KINDERGARTEN_PRESETS = {
    "classroom_receipt": "sample_kindergarten_receipt.png",
    "student_profile": "sample_student_profile.png",
    "school_circular": "sample_school_notice.png",
}


def _resolve_idempotency_key(request: Request, explicit_key: str = "", scope: str = "api") -> str:
    raw = (
        request.headers.get("Idempotency-Key")
        or request.headers.get("X-Idempotency-Key")
        or explicit_key
        or ""
    ).strip()
    if not raw:
        return ""
    if len(raw) > 200 or any(ord(ch) < 32 for ch in raw):
        raise HTTPException(status_code=400, detail="Invalid Idempotency-Key")
    return f"{scope}:{raw}"


def _compute_json_payload_hash(payload_obj: Any) -> str:
    encoded = json.dumps(payload_obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _default_engine_factory() -> LineOCR:
    print("[Engine] Initializing OpenThai 2.0 OCR Engine from:", MODEL_DIR)
    engine = LineOCR(MODEL_DIR, threads=1)
    print("[Engine] OpenThai 2.0 OCR Engine ready!")
    return engine


def create_app(
    config: Optional[AppConfig] = None,
    engine_factory: Optional[Callable[[], Any]] = None,
    static_dir: Optional[str] = None,
    storage_client: Optional[R2ClientProtocol] = None,
) -> FastAPI:
    cfg = config or AppConfig.from_env()
    factory = engine_factory or _default_engine_factory
    storage = VaultStorageCoordinator(cfg, client=storage_client)

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI):
        cfg.ensure_dirs()
        fastapi_app.state.storage.startup()
        if fastapi_app.state.engine is None:
            fastapi_app.state.engine = factory()
        if fastapi_app.state.storage.state == "READY":
            fastapi_app.state.watcher.start()
        try:
            yield
        finally:
            fastapi_app.state.watcher.stop()

    application = FastAPI(title="น้องโชคช่วย (Nong ChokChuay)", version="2.0.0", lifespan=lifespan)
    application.state.config = cfg
    application.state.engine_factory = factory
    application.state.engine = None
    application.state.storage = storage

    def get_engine() -> Any:
        if application.state.engine is None:
            application.state.engine = factory()
        return application.state.engine

    application.state.get_engine = get_engine
    application.state.watcher = InboxWatcher(config=cfg, engine_getter=get_engine, storage=storage)

    @application.exception_handler(storage_service.StorageError)
    async def storage_error_handler(request: Request, exc: storage_service.StorageError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "detail": str(exc),
                "error": exc.error_code,
                "storage_state": storage.state,
            },
        )

    _PUBLIC_PATHS = {
        "/",
        "/api/health",
        "/api/auth/me",
        "/api/auth/google/login",
        "/api/auth/google/callback",
        "/api/line/webhook",
    }
    _PUBLIC_STATIC_PATHS = {
        "/static/styles.css",
        "/static/app.js",
        "/static/mascot.jpg",
    }
    _STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    @application.middleware("http")
    async def auth_and_csrf_middleware(request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        raw_path = request.url.path or "/"
        if ".." in raw_path.split("/"):
            if cfg.auth_required:
                return JSONResponse(status_code=401, content={"detail": "Authentication required"})
            return JSONResponse(status_code=400, content={"detail": "Invalid path"})

        norm_path = posixpath.normpath(raw_path)
        is_storage_exempt = (
            norm_path == "/"
            or norm_path == "/api/health"
            or norm_path.startswith("/static/")
        )
        is_public_path = (
            norm_path in _PUBLIC_PATHS
            or norm_path in _PUBLIC_STATIC_PATHS
            or norm_path.startswith("/static/mascot_cards/")
        )
        session = None
        if not is_storage_exempt:
            try:
                if cfg.auth_required and not is_public_path:
                    session = await run_in_threadpool(
                        storage.execute_read,
                        lambda: auth_service.authenticate_request(request, cfg),
                    )
                else:
                    await run_in_threadpool(storage.require_ready)
            except storage_service.StorageError as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={
                        "success": False,
                        "detail": str(exc),
                        "error": exc.error_code,
                        "storage_state": storage.state,
                    },
                )

        if not cfg.auth_required or is_public_path:
            return await call_next(request)

        if session is None and is_storage_exempt:
            session = await run_in_threadpool(auth_service.authenticate_request, request, cfg)
        if not session:
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})

        request.state.auth_user = session
        if request.method in _STATE_CHANGING_METHODS:
            if not auth_service.validate_csrf_and_origin(request, cfg, session):
                return JSONResponse(status_code=403, content={"detail": "CSRF validation failed"})

        return await call_next(request)

    cors_origins = list(auth_service.get_cors_allowed_origins(cfg)) if cfg.auth_required else ["*"]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/api/health")
    def get_health():
        is_error = storage.state == "ERROR"
        payload = {
            "status": "error" if is_error else "ok",
            "brand": "น้องโชคช่วย (Nong ChokChuay)",
            "auth_required": cfg.auth_required,
            "oauth_configured": auth_service.is_oauth_configured(cfg),
            "line_oa_configured": bool(cfg.line_channel_secret and cfg.line_channel_access_token),
            "line_allowlist_configured": bool(cfg.allowed_line_user_ids),
            "storage_state": storage.state,
            "storage_revision": storage.current_revision,
            "busy": bool(storage.is_busy or application.state.watcher.is_scanning),
        }
        return JSONResponse(status_code=503 if is_error else 200, content=payload)

    @application.get("/api/auth/me")
    def get_auth_me(request: Request):
        session = storage.execute_read(
            lambda: auth_service.authenticate_request(request, cfg) if cfg.auth_required else None
        )
        return {
            "authenticated": bool(session) if cfg.auth_required else True,
            "auth_required": cfg.auth_required,
            "oauth_configured": auth_service.is_oauth_configured(cfg),
            "csrf_token": session.get("csrf_token") if session else None,
            "user": (
                {
                    "email": session["email"],
                    "name": session.get("name", ""),
                    "picture": session.get("picture", ""),
                }
                if session
                else None
            ),
        }

    @application.get("/api/auth/google/login")
    def auth_google_login(request: Request):
        if not auth_service.is_oauth_configured(cfg):
            return RedirectResponse(url="/?auth_error=not_configured", status_code=302)
        base_url = auth_service.resolve_external_base_url(request, cfg)
        redirect_uri = f"{base_url}/api/auth/google/callback"
        resp = RedirectResponse(url="/", status_code=302)
        state = auth_service.issue_oauth_state_cookie(resp, request, cfg)
        resp.headers["location"] = auth_service.build_google_authorize_url(redirect_uri, state, cfg)
        return resp

    @application.get("/api/auth/google/callback")
    def auth_google_callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
    ):
        def _error_redirect(err_code: str) -> RedirectResponse:
            err_resp = RedirectResponse(url=f"/?auth_error={err_code}", status_code=302)
            auth_service.clear_oauth_state_cookie(err_resp, request, cfg)
            return err_resp

        if error:
            return _error_redirect("oauth_failed")
        if not auth_service.is_oauth_configured(cfg):
            return _error_redirect("not_configured")
        if not code or not auth_service.verify_oauth_state(request, cfg, state):
            return _error_redirect("invalid_state")

        base_url = auth_service.resolve_external_base_url(request, cfg)
        redirect_uri = f"{base_url}/api/auth/google/callback"
        try:
            token_data = auth_service.exchange_google_code(code, redirect_uri, cfg)
            access_token = str(token_data.get("access_token") or "").strip()
            if not access_token:
                return _error_redirect("oauth_failed")
            userinfo = auth_service.fetch_google_userinfo(access_token)
        except Exception as exc:
            print(f"[Auth] Google OAuth callback failed: {exc}")
            return _error_redirect("oauth_failed")

        email_verified = userinfo.get("email_verified") is True
        email = str(userinfo.get("email") or "").strip().lower()
        if not email_verified or not email or email not in cfg.allowed_emails:
            return _error_redirect("unauthorized_email")

        resp = RedirectResponse(url="/", status_code=302)
        auth_service.clear_oauth_state_cookie(resp, request, cfg)
        auth_service.issue_login_session(
            resp,
            request,
            cfg,
            email=email,
            name=str(userinfo.get("name") or ""),
            picture=str(userinfo.get("picture") or ""),
            storage=storage,
        )
        return resp

    @application.post("/api/auth/logout")
    def auth_logout(request: Request):
        resp = JSONResponse({"success": True})
        auth_service.revoke_login_session(resp, request, cfg, storage=storage)
        return resp

    @application.get("/api/system")
    def get_system_info():
        storage.require_ready()
        db_stats = database.get_db_stats(db_path=cfg.db_path)
        return {
            "brand": "น้องโชคช่วย (Nong ChokChuay)",
            "teacher_name": cfg.teacher_name,
            "engine": "OpenThai 2.0 OCR Text (Local ONNX)",
            "model_architecture": "PaddlePaddle PP-OCRv6-tiny Detector + Thai CTC Recognizer",
            "parameters": "1.02M (det: 428K, rec: 587K)",
            "model_size": "~4.2 MB",
            "hardware": "CPU (ONNX Runtime / OpenVINO)",
            "offline": True,
            "auto_extract_on_ingest": cfg.auto_extract_on_ingest,
            "llm_configured": extractor.is_llm_configured(cfg),
            "llm_provider": cfg.llm_provider if extractor.is_llm_configured(cfg) else None,
            "llm_model": cfg.llm_model if extractor.is_llm_configured(cfg) else None,
            "line_oa_configured": bool(cfg.line_channel_secret and cfg.line_channel_access_token),
            "license": "Apache-2.0",
            "database": {
                "type": "SQLite with Trigram Full-Text Search (FTS5)",
                "path": db_stats["db_path"],
                "total_documents": db_stats["total_documents"],
                "total_pages": db_stats["total_pages"],
                "total_characters": db_stats["total_characters"],
                "pending_review_count": db_stats["pending_review_count"],
                "total_students": db_stats["total_students"],
                "allergy_student_count": db_stats["allergy_student_count"],
                "pending_reimbursement_total": db_stats["pending_reimbursement_total"],
            },
            "python_version": sys.version.split()[0],
        }

    @application.get("/api/samples")
    def get_samples():
        samples_dir = os.path.join(BASE_DIR, "samples")
        samples = []
        if os.path.exists(samples_dir):
            files = {
                "sample_kindergarten_receipt.png": "🧾 บิลค่าสีเทียน/สื่อการสอนอนุบาล",
                "sample_student_profile.png": "👶 ใบประวัติเด็กอนุบาล & แพ้อาหาร",
                "sample_school_notice.png": "🏫 หนังสือแจ้งผู้ปกครอง",
                "sample_official_document.png": "📝 บันทึกข้อความราชการ & ตารางผลทดสอบ",
                "sample_budget_table.png": "📊 ตารางรายงานงบประมาณรายจ่ายประจำปี",
            }
            for fname, label in files.items():
                fpath = os.path.join(samples_dir, fname)
                if os.path.exists(fpath):
                    samples.append({
                        "id": fname,
                        "title": label,
                        "url": f"/api/sample/{fname}",
                    })
        return samples

    @application.get("/api/sample/{filename}")
    def get_sample_file(filename: str):
        fpath = os.path.join(BASE_DIR, "samples", filename)
        if not os.path.exists(fpath):
            raise HTTPException(status_code=404, detail="Sample not found")
        return FileResponse(fpath)

    @application.post("/api/samples/kindergarten/{preset}")
    def run_kindergarten_preset(request: Request, preset: str):
        op_id = _resolve_idempotency_key(request, scope=f"web_sample:{preset}")
        fname = _KINDERGARTEN_PRESETS.get(preset)
        if not fname:
            raise HTTPException(status_code=404, detail="Preset not found")
        fpath = os.path.join(BASE_DIR, "samples", fname)
        if not os.path.exists(fpath):
            raise HTTPException(status_code=404, detail="Sample file missing")
        with open(fpath, "rb") as fh:
            contents = fh.read()
        engine = get_engine()
        result = ocr_service.ingest_document(
            contents=contents,
            filename=fname,
            engine=engine,
            config=cfg,
            content_type="image/png",
            save_to_db=True,
            ingestion_channel="web_upload",
            auto_extract=True,
            storage=storage,
            operation_id=op_id,
        )
        return JSONResponse(result)

    # Database Endpoints
    @application.get("/api/documents")
    def list_documents(
        query: Optional[str] = Query(None),
        category: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
        review_status: Optional[str] = Query(None),
        limit: int = 50,
        offset: int = 0,
    ):
        storage.require_ready()
        r2_enabled = cfg.is_r2_enabled()
        docs = database.search_documents(
            query=query,
            category=category,
            tag=tag,
            review_status=review_status,
            limit=limit,
            offset=offset,
            db_path=cfg.db_path,
            r2_enabled=r2_enabled,
        )
        stats = database.get_db_stats(db_path=cfg.db_path)
        return {
            "success": True,
            "query": query,
            "category": category,
            "tag": tag,
            "review_status": review_status,
            "total": len(docs),
            "db_stats": stats,
            "documents": docs,
        }

    @application.get("/api/documents/{doc_id}")
    def get_document(doc_id: str):
        storage.require_ready()
        doc = database.get_document_by_id(
            doc_id, db_path=cfg.db_path, r2_enabled=cfg.is_r2_enabled()
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"success": True, "document": doc}

    @application.get("/api/documents/{doc_id}/file")
    def download_document_source(doc_id: str):
        storage.require_ready()
        doc = database.get_document_by_id(
            doc_id, db_path=cfg.db_path, r2_enabled=cfg.is_r2_enabled()
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        try:
            src_path = storage.ensure_local_source_file(doc)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Source file not found")
        except ValueError:
            raise HTTPException(status_code=404, detail="Source file not found")
        return FileResponse(src_path, filename=doc.get("filename") or os.path.basename(src_path))

    @application.patch("/api/documents/{doc_id}")
    def update_document(request: Request, doc_id: str, payload: DocumentUpdateRequest):
        storage.require_ready()
        op_id = _resolve_idempotency_key(request, scope=f"web_doc_update:{doc_id}")
        p_hash = _compute_json_payload_hash({"doc_id": doc_id, **payload.model_dump()})
        if op_id:
            existing_op = storage.get_committed_operation(op_id, expected_payload_hash=p_hash)
            if existing_op is not None:
                return existing_op

        r2_enabled = cfg.is_r2_enabled()
        existing = database.get_document_by_id(doc_id, db_path=cfg.db_path, r2_enabled=r2_enabled)
        if not existing:
            raise HTTPException(status_code=404, detail="Document not found")

        def _do_update(_rev: int) -> Dict[str, Any]:
            updated = database.update_document_metadata(
                doc_id=doc_id,
                text_content=payload.text,
                category=payload.category,
                tags=payload.tags,
                doc_date=payload.doc_date,
                review_status=payload.review_status,
                db_path=cfg.db_path,
                r2_enabled=r2_enabled,
            )
            return {"success": True, "doc_id": doc_id, "document": updated}

        return storage.execute_mutation(op_id, "update_document", _do_update, payload_hash=p_hash)

    @application.post("/api/documents/{doc_id}/review")
    def review_document(
        request: Request,
        doc_id: str,
        payload: Optional[DocumentReviewRequest] = Body(default=None),
    ):
        storage.require_ready()
        op_id = _resolve_idempotency_key(request, scope=f"web_doc_review:{doc_id}")
        p_hash = _compute_json_payload_hash(
            {"doc_id": doc_id, "payload": payload.model_dump() if payload else None}
        )
        if op_id:
            existing_op = storage.get_committed_operation(op_id, expected_payload_hash=p_hash)
            if existing_op is not None:
                return existing_op

        r2_enabled = cfg.is_r2_enabled()
        existing = database.get_document_by_id(doc_id, db_path=cfg.db_path, r2_enabled=r2_enabled)
        if not existing:
            raise HTTPException(status_code=404, detail="Document not found")

        target_review = payload.review_status if payload else "reviewed"

        def _do_review(_rev: int) -> Dict[str, Any]:
            updated = database.update_document_metadata(
                doc_id=doc_id,
                review_status=target_review,
                db_path=cfg.db_path,
                r2_enabled=r2_enabled,
            )
            if payload and payload.reimbursement_status and updated and updated.get("expense"):
                exp = updated["expense"]
                updated_exp = database.upsert_expense(
                    document_id=doc_id,
                    merchant=exp["merchant"],
                    expense_date=exp["expense_date"],
                    subtotal=exp["subtotal"],
                    vat_amount=exp["vat_amount"],
                    total_amount=exp["total_amount"],
                    currency=exp["currency"],
                    expense_category=exp["expense_category"],
                    extraction_method=exp["extraction_method"],
                    reimbursement_status=payload.reimbursement_status,
                    budget_source=exp.get("budget_source"),
                    raw_json=exp.get("raw_json"),
                    db_path=cfg.db_path,
                )
                updated["expense"] = updated_exp
            return {"success": True, "doc_id": doc_id, "document": updated}

        return storage.execute_mutation(op_id, "review_document", _do_review, payload_hash=p_hash)

    @application.delete("/api/documents/{doc_id}")
    def delete_document(request: Request, doc_id: str):
        storage.require_ready()
        op_id = _resolve_idempotency_key(request, scope=f"web_doc_delete:{doc_id}")
        p_hash = _compute_json_payload_hash({"doc_id": doc_id})
        if op_id:
            existing_op = storage.get_committed_operation(op_id, expected_payload_hash=p_hash)
            if existing_op is not None:
                return {"success": bool(existing_op.get("success")), "deleted_id": doc_id}

        r2_enabled = cfg.is_r2_enabled()
        existing = database.get_document_by_id(doc_id, db_path=cfg.db_path, r2_enabled=r2_enabled)
        if not existing:
            return {"success": False, "deleted_id": doc_id}

        removed_paths: List[str] = []

        def _do_delete(rev: int) -> Dict[str, Any]:
            success = database.delete_document(
                doc_id,
                db_path=cfg.db_path,
                defer_file_removal=r2_enabled,
                deleted_in_revision=rev,
                removed_paths_out=removed_paths,
            )
            return {"success": success, "deleted_id": doc_id, "doc_id": doc_id}

        res = storage.execute_mutation(
            op_id,
            "delete_document",
            _do_delete,
            cleanup_local_paths_after_commit=removed_paths,
            payload_hash=p_hash,
        )
        return {"success": bool(res.get("success")), "deleted_id": doc_id}

    @application.get("/api/database/download")
    def download_database():
        storage.require_ready()
        if not os.path.exists(cfg.db_path):
            raise HTTPException(status_code=404, detail="Database file not found")
        download_path = (
            storage.committed_db_path
            if cfg.is_r2_enabled() and os.path.exists(storage.committed_db_path)
            else cfg.db_path
        )
        return FileResponse(download_path, filename="nong_chokchuay_vault.db", media_type="application/octet-stream")

    @application.get("/api/watcher/status")
    def watcher_status():
        return application.state.watcher.get_status()

    @application.post("/api/watcher/scan")
    def watcher_scan():
        storage.require_ready()
        result = application.state.watcher.scan_once(require_stability=False)
        return {"success": True, **result}

    # Expense Extraction & Reporting Endpoints
    @application.post("/api/documents/{doc_id}/extract")
    def extract_document_expense(
        request: Request,
        doc_id: str,
        extraction_method: str = Query("auto"),
    ):
        storage.require_ready()
        op_id = _resolve_idempotency_key(request, scope=f"web_doc_extract:{doc_id}")
        p_hash = _compute_json_payload_hash({"doc_id": doc_id, "extraction_method": extraction_method})
        if op_id:
            existing_op = storage.get_committed_operation(op_id, expected_payload_hash=p_hash)
            if existing_op is not None:
                return existing_op

        r2_enabled = cfg.is_r2_enabled()
        doc = database.get_document_by_id(doc_id, db_path=cfg.db_path, r2_enabled=r2_enabled)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        intel = extractor.extract_document_intelligence(
            doc.get("text") or "",
            cfg,
            extraction_method=extraction_method,
            require_expense_hint=False,
        )

        def _do_extract(_rev: int) -> Dict[str, Any]:
            updated_doc, expense, student = ocr_service.apply_intelligence_to_document(
                doc_id,
                doc.get("category") or "general",
                doc.get("tags") or [],
                intel,
                db_path=cfg.db_path,
                force_expense=True,
                r2_enabled=r2_enabled,
            )
            return {"success": True, "doc_id": doc_id, "expense": expense, "student": student, "document": updated_doc}

        return storage.execute_mutation(op_id, "extract_document", _do_extract, payload_hash=p_hash)

    @application.get("/api/expenses")
    def list_expenses(
        month: Optional[str] = Query(None),
        expense_category: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
        reimbursement_status: Optional[str] = Query(None),
    ):
        storage.require_ready()
        data = database.list_expenses(
            month=month,
            expense_category=expense_category,
            tag=tag,
            reimbursement_status=reimbursement_status,
            db_path=cfg.db_path,
        )
        return {"success": True, **data}

    @application.put("/api/expenses/{doc_id}")
    def update_expense(request: Request, doc_id: str, payload: ExpenseUpsertRequest):
        storage.require_ready()
        op_id = _resolve_idempotency_key(request, scope=f"web_expense_upsert:{doc_id}")
        p_hash = _compute_json_payload_hash({"doc_id": doc_id, **payload.model_dump()})
        if op_id:
            existing_op = storage.get_committed_operation(op_id, expected_payload_hash=p_hash)
            if existing_op is not None:
                return existing_op

        doc = database.get_document_by_id(
            doc_id, db_path=cfg.db_path, r2_enabled=cfg.is_r2_enabled()
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        def _do_upsert_expense(_rev: int) -> Dict[str, Any]:
            expense = database.upsert_expense(
                document_id=doc_id,
                merchant=payload.merchant,
                expense_date=payload.expense_date,
                subtotal=payload.subtotal,
                vat_amount=payload.vat_amount,
                total_amount=payload.total_amount,
                currency=payload.currency,
                expense_category=payload.expense_category,
                extraction_method="manual",
                reimbursement_status=payload.reimbursement_status,
                budget_source=payload.budget_source,
                raw_json=payload.model_dump(),
                db_path=cfg.db_path,
            )
            return {"success": True, "doc_id": doc_id, "expense": expense}

        return storage.execute_mutation(op_id, "upsert_expense", _do_upsert_expense, payload_hash=p_hash)

    @application.delete("/api/expenses/{doc_id}")
    def delete_expense(doc_id: str):
        storage.require_ready()
        doc = database.get_document_by_id(
            doc_id, db_path=cfg.db_path, r2_enabled=cfg.is_r2_enabled()
        )
        if not doc or not doc.get("expense"):
            return {"success": False, "document_id": doc_id}

        res = storage.execute_mutation(
            "",
            "delete_expense",
            lambda _rev: {
                "success": database.delete_expense(doc_id, db_path=cfg.db_path),
                "document_id": doc_id,
                "doc_id": doc_id,
            },
        )
        return {"success": bool(res.get("success")), "document_id": doc_id}

    @application.get("/api/expenses/export.csv")
    def export_expenses_csv(
        month: Optional[str] = Query(None),
        expense_category: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
        reimbursement_status: Optional[str] = Query(None),
    ):
        storage.require_ready()
        data = database.list_expenses(
            month=month,
            expense_category=expense_category,
            tag=tag,
            reimbursement_status=reimbursement_status,
            db_path=cfg.db_path,
        )
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "expense_date",
            "merchant",
            "expense_category",
            "reimbursement_status",
            "budget_source",
            "subtotal",
            "vat_amount",
            "total_amount",
            "currency",
            "extraction_method",
            "filename",
            "document_id",
            "tags",
        ])
        for item in data["expenses"]:
            writer.writerow([
                item["expense_date"],
                item["merchant"],
                item["expense_category"],
                item.get("reimbursement_status", "pending_reimbursement"),
                item.get("budget_source", "classroom"),
                f"{item['subtotal']:.2f}",
                f"{item['vat_amount']:.2f}",
                f"{item['total_amount']:.2f}",
                item["currency"],
                item["extraction_method"],
                item.get("filename", ""),
                item["document_id"],
                ", ".join(item.get("tags", [])),
            ])
        csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
        parts = ["expenses"]
        if month and month.strip():
            parts.append(re.sub(r"[^0-9A-Za-z_-]+", "", month.strip()))
        if reimbursement_status and reimbursement_status.strip():
            parts.append(re.sub(r"[^0-9A-Za-z_-]+", "", reimbursement_status.strip()))
        if tag and tag.strip():
            safe_tag = re.sub(r"[^0-9A-Za-z_-]+", "", tag.strip())
            parts.append(safe_tag or "filtered")
        ascii_name = "_".join(p for p in parts if p) + ".csv"
        utf8_suffix = "".join(f"_{p.strip()}" for p in (month, reimbursement_status, tag) if p and p.strip())
        utf8_name = f"expenses{utf8_suffix}.csv"
        disposition = f'attachment; filename="{ascii_name}"'
        if utf8_name != ascii_name:
            disposition += f"; filename*=UTF-8''{quote(utf8_name)}"
        return Response(
            content=csv_bytes,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": disposition},
        )

    # Kindergarten Student Profiles Endpoints
    @application.get("/api/students")
    def list_students(
        query: Optional[str] = Query(None),
        classroom: Optional[str] = Query(None),
        has_allergy: Optional[bool] = Query(None),
    ):
        storage.require_ready()
        data = database.list_students(
            query=query,
            classroom=classroom,
            has_allergy=has_allergy,
            db_path=cfg.db_path,
        )
        return {"success": True, **data}

    @application.post("/api/students")
    def create_student(request: Request, payload: StudentUpsertRequest):
        storage.require_ready()
        op_id = _resolve_idempotency_key(request, scope="web_student_create")
        p_hash = _compute_json_payload_hash(payload.model_dump())
        if op_id:
            existing_op = storage.get_committed_operation(op_id, expected_payload_hash=p_hash)
            if existing_op is not None:
                return existing_op

        if payload.document_id:
            doc = database.get_document_by_id(
                payload.document_id, db_path=cfg.db_path, r2_enabled=cfg.is_r2_enabled()
            )
            if not doc:
                raise HTTPException(status_code=404, detail="Document not found")

        def _do_create_student(_rev: int) -> Dict[str, Any]:
            student = database.upsert_student(
                document_id=payload.document_id,
                full_name=payload.full_name,
                nickname=payload.nickname,
                citizen_id=payload.citizen_id,
                birth_date=payload.birth_date,
                classroom=payload.classroom,
                parent_name=payload.parent_name,
                parent_phone=payload.parent_phone,
                allergies=payload.allergies,
                medical_notes=payload.medical_notes,
                extraction_method="manual",
                raw_json=payload.model_dump(),
                db_path=cfg.db_path,
            )
            return {"success": True, "id": student.get("id", ""), "student": student}

        return storage.execute_mutation(op_id, "create_student", _do_create_student, payload_hash=p_hash)

    @application.put("/api/students/{student_id}")
    def update_student(student_id: str, payload: StudentUpsertRequest):
        storage.require_ready()
        existing = database.get_student_by_id(student_id, db_path=cfg.db_path)
        if not existing:
            raise HTTPException(status_code=404, detail="Student not found")

        def _do_update_student(_rev: int) -> Dict[str, Any]:
            student = database.upsert_student(
                student_id=student_id,
                document_id=payload.document_id or existing.get("document_id") or None,
                full_name=payload.full_name,
                nickname=payload.nickname,
                citizen_id=payload.citizen_id,
                birth_date=payload.birth_date,
                classroom=payload.classroom,
                parent_name=payload.parent_name,
                parent_phone=payload.parent_phone,
                allergies=payload.allergies,
                medical_notes=payload.medical_notes,
                extraction_method="manual",
                raw_json=payload.model_dump(),
                db_path=cfg.db_path,
            )
            return {"success": True, "id": student_id, "student": student}

        return storage.execute_mutation("", "update_student", _do_update_student)

    @application.delete("/api/students/{student_id}")
    def delete_student(student_id: str):
        storage.require_ready()
        existing = database.get_student_by_id(student_id, db_path=cfg.db_path)
        if not existing:
            raise HTTPException(status_code=404, detail="Student not found")

        return storage.execute_mutation(
            "",
            "delete_student",
            lambda _rev: {
                "success": database.delete_student(student_id, db_path=cfg.db_path),
                "deleted_id": student_id,
                "id": student_id,
            },
        )

    @application.get("/api/students/export.csv")
    def export_students_csv(
        query: Optional[str] = Query(None),
        classroom: Optional[str] = Query(None),
        has_allergy: Optional[bool] = Query(None),
    ):
        storage.require_ready()
        data = database.list_students(
            query=query,
            classroom=classroom,
            has_allergy=has_allergy,
            db_path=cfg.db_path,
        )
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "full_name",
            "nickname",
            "citizen_id",
            "birth_date",
            "classroom",
            "parent_name",
            "parent_phone",
            "allergies",
            "medical_notes",
            "document_id",
        ])
        for stu in data["students"]:
            writer.writerow([
                stu["full_name"],
                stu["nickname"],
                stu["citizen_id"],
                stu["birth_date"],
                stu["classroom"],
                stu["parent_name"],
                stu["parent_phone"],
                stu["allergies"],
                stu["medical_notes"],
                stu.get("document_id", ""),
            ])
        csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
        return Response(
            content=csv_bytes,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="students_kindergarten.csv"'},
        )

    # LINE OA Webhook & Interactive Simulator
    @application.post("/api/line/webhook")
    async def line_webhook(request: Request):
        if cfg.auth_required and not (cfg.line_channel_secret or "").strip():
            raise HTTPException(status_code=503, detail="LINE channel secret is not configured")

        body = await request.body()
        signature = request.headers.get("X-Line-Signature", "")
        if not line_service.verify_line_signature(body, signature, cfg.line_channel_secret):
            raise HTTPException(status_code=401, detail="Invalid LINE signature")

        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}")

        events = payload.get("events") or []
        storage.require_ready()
        replies: List[Dict[str, Any]] = []
        rejected_events = 0
        storage_failed = False

        for event in events:
            etype = event.get("type")
            reply_token = event.get("replyToken", "")
            source = event.get("source") or {}
            user_id = (source.get("userId") or "").strip()

            allowed, denial_text = line_service.authorize_line_event_source(source, cfg)
            if not allowed:
                rejected_events += 1
                if reply_token and cfg.line_channel_access_token and denial_text:
                    try:
                        line_service.reply_line_messages(
                            reply_token,
                            [{"type": "text", "text": denial_text}],
                            cfg.line_channel_access_token,
                        )
                    except Exception as exc:
                        print(f"[LINE] Rejection reply skipped/failed: {exc}")
                continue

            reply_obj: Optional[Dict[str, Any]] = None
            event_uid = str(event.get("webhookEventId") or "").strip()

            message_type = (event.get("message") or {}).get("type")
            supported = (etype == "message" and message_type in ("text", "image", "file")) or etype == "postback"
            if supported and source.get("type") == "user" and user_id and reply_token and cfg.line_channel_access_token:
                await run_in_threadpool(line_service.start_line_loading, user_id, cfg.line_channel_access_token)

            if etype == "message":
                msg = event.get("message") or {}
                mtype = msg.get("type")
                if mtype == "text":
                    try:
                        reply_obj = line_service.process_line_text_command(
                            msg.get("text", ""),
                            db_path=cfg.db_path,
                            public_base_url=cfg.public_base_url,
                            config=cfg,
                            teacher_name=cfg.teacher_name,
                            storage=storage,
                        )
                    except storage_service.StorageError as exc:
                        storage_failed = True
                        reply_text = (
                            f"ขออภัยครับ{cfg.teacher_name} ระบบจัดเก็บข้อมูลขัดข้องชั่วคราว "
                            "จึงยังไม่สามารถดึงข้อมูลในตอนนี้ได้ครับ กรุณาลองใหม่อีกครั้งนะครับ"
                        )
                        reply_obj = {
                            "success": False,
                            "error": exc.error_code,
                            "reply_text": reply_text,
                            "line_messages": [{"type": "text", "text": reply_text}],
                        }
                elif mtype in ("image", "file"):
                    msg_id = msg.get("id", "")
                    op_id = f"line:msg:{event_uid or msg_id}" if (event_uid or msg_id) else ""
                    if cfg.line_channel_access_token and msg_id:
                        try:
                            file_bytes, content_type = line_service.download_line_content(
                                msg_id, cfg.line_channel_access_token
                            )
                            ext = ".pdf" if "pdf" in content_type.lower() else ".jpg"
                            fname = msg.get("fileName") or f"line_{msg_id}{ext}"
                            engine = get_engine()
                            ocr_res = ocr_service.ingest_document(
                                contents=file_bytes,
                                filename=fname,
                                engine=engine,
                                config=cfg,
                                content_type=content_type or ("application/pdf" if ext == ".pdf" else "image/jpeg"),
                                process_all_pages=(ext == ".pdf"),
                                save_to_db=True,
                                ingestion_channel="line_oa",
                                line_user_id=user_id,
                                auto_extract=True,
                                storage=storage,
                                operation_id=op_id,
                            )
                            reply_obj = line_service.build_nong_chok_reply_summary(
                                ocr_res,
                                expense=ocr_res.get("expense"),
                                student=ocr_res.get("student"),
                                public_base_url=cfg.public_base_url,
                                config=cfg,
                                teacher_name=cfg.teacher_name,
                            )
                        except storage_service.StorageError as exc:
                            storage_failed = True
                            reply_text = (
                                f"ขออภัยครับ{cfg.teacher_name} ระบบจัดเก็บข้อมูลขัดข้องชั่วคราว "
                                "จึงยังไม่ได้บันทึกเอกสารนี้ครับ กรุณาส่งใหม่อีกครั้งนะครับ"
                            )
                            reply_obj = {
                                "success": False,
                                "error": exc.error_code,
                                "reply_text": reply_text,
                                "line_messages": [{"type": "text", "text": reply_text}],
                            }
                        except Exception as exc:
                            reply_text = f"น้องโชคเปิดไฟล์นี้ไม่ได้ครับ{cfg.teacher_name} ({exc})"
                            reply_obj = {
                                "reply_text": reply_text,
                                "line_messages": [{"type": "text", "text": reply_text}],
                            }
                    else:
                        reply_text = (
                            f"น้องโชคได้รับข้อความไฟล์แล้วครับ{cfg.teacher_name} "
                            "แต่ยังไม่ได้ตั้งค่า LINE_CHANNEL_ACCESS_TOKEN ครับ"
                        )
                        reply_obj = {
                            "reply_text": reply_text,
                            "line_messages": [{"type": "text", "text": reply_text}],
                        }
            elif etype == "postback":
                pb_data = (event.get("postback") or {}).get("data", "")
                pb_ts = str(event.get("timestamp") or "").strip()
                if event_uid:
                    pb_op_id = f"line:pb:{event_uid}"
                elif pb_ts:
                    pb_op_id = f"line:pb:{pb_ts}:{hashlib.sha256(pb_data.encode('utf-8')).hexdigest()[:16]}"
                else:
                    pb_op_id = ""
                try:
                    reply_obj = line_service.handle_line_postback(
                        pb_data,
                        db_path=cfg.db_path,
                        config=cfg,
                        teacher_name=cfg.teacher_name,
                        public_base_url=cfg.public_base_url,
                        storage=storage,
                        operation_id=pb_op_id,
                    )
                except storage_service.StorageError as exc:
                    storage_failed = True
                    reply_text = (
                        f"ขออภัยครับ{cfg.teacher_name} ระบบจัดเก็บข้อมูลขัดข้องชั่วคราว "
                        "จึงยังไม่ได้บันทึกการเปลี่ยนแปลงครับ กรุณากดลองใหม่อีกครั้งนะครับ"
                    )
                    reply_obj = {
                        "success": False,
                        "error": exc.error_code,
                        "reply_text": reply_text,
                        "line_messages": [{"type": "text", "text": reply_text}],
                    }

            if reply_obj:
                if reply_token and cfg.line_channel_access_token:
                    try:
                        line_service.reply_line_messages(
                            reply_token,
                            reply_obj.get("line_messages") or [],
                            cfg.line_channel_access_token,
                        )
                    except Exception as exc:
                        print(f"[LINE] Reply API call skipped/failed: {exc}")
                replies.append(reply_obj)

        if storage_failed:
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "error": "storage_commit_failed",
                    "storage_state": storage.state,
                    "processed_events": len(replies),
                    "rejected_events": rejected_events,
                    "replies": replies,
                },
            )

        return {
            "success": True,
            "processed_events": len(replies),
            "rejected_events": rejected_events,
            "replies": replies,
        }

    @application.post("/api/line/simulate")
    async def line_simulate(
        request: Request,
        text: str = Form(""),
        postback_data: str = Form(""),
        file: Optional[UploadFile] = File(None),
        operation_id: str = Form(""),
    ):
        storage.require_ready()
        op_id = _resolve_idempotency_key(request, operation_id, scope="web_line_sim")
        if postback_data.strip():
            res = line_service.handle_line_postback(
                postback_data.strip(),
                db_path=cfg.db_path,
                config=cfg,
                teacher_name=cfg.teacher_name,
                public_base_url=cfg.public_base_url,
                storage=storage,
                operation_id=op_id,
            )
            return JSONResponse({"mode": "postback", **res})

        if file is not None:
            contents = await file.read()
            engine = get_engine()
            ocr_res = ocr_service.ingest_document(
                contents=contents,
                filename=file.filename or "line_photo.png",
                engine=engine,
                config=cfg,
                content_type=file.content_type,
                process_all_pages=(file.filename or "").lower().endswith(".pdf"),
                save_to_db=True,
                ingestion_channel="line_oa",
                line_user_id="sim-teacher",
                auto_extract=True,
                storage=storage,
                operation_id=op_id,
            )
            summary = line_service.build_nong_chok_reply_summary(
                ocr_res,
                expense=ocr_res.get("expense"),
                student=ocr_res.get("student"),
                public_base_url=cfg.public_base_url,
                config=cfg,
                teacher_name=cfg.teacher_name,
            )
            return JSONResponse({
                "success": True,
                "mode": "file",
                "ocr": ocr_res,
                **summary,
            })

        res = line_service.process_line_text_command(
            text,
            db_path=cfg.db_path,
            public_base_url=cfg.public_base_url,
            config=cfg,
            teacher_name=cfg.teacher_name,
            storage=storage,
        )
        return JSONResponse({"success": True, "mode": "text", **res})

    # OCR Processing Endpoint
    @application.post("/api/ocr")
    async def process_ocr(
        request: Request,
        file: UploadFile = File(...),
        page: int = Form(1),
        process_all_pages: bool = Form(False),
        save_to_db: bool = Form(True),
        category: str = Form("general"),
        tags: str = Form(""),
        doc_date: str = Form(""),
        auto_extract: Optional[bool] = Form(None),
        operation_id: str = Form(""),
    ):
        try:
            if save_to_db:
                storage.require_ready()
            op_id = _resolve_idempotency_key(request, operation_id, scope="web_ocr") if save_to_db else ""
            contents = await file.read()
            engine = get_engine()
            result = ocr_service.ingest_document(
                contents=contents,
                filename=file.filename or "unknown",
                engine=engine,
                config=cfg,
                content_type=file.content_type,
                page=page,
                process_all_pages=process_all_pages,
                save_to_db=save_to_db,
                category=category,
                tags=tags,
                ingestion_channel="web_upload",
                doc_date=doc_date,
                auto_extract=auto_extract,
                storage=storage if save_to_db else None,
                operation_id=op_id,
            )
            return JSONResponse(result)
        except storage_service.StorageError:
            raise
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))
        except HTTPException:
            raise
        except Exception as e:
            import traceback

            traceback.print_exc()
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    STATIC_DIR = os.path.abspath(static_dir) if static_dir else os.path.join(BASE_DIR, "static")
    os.makedirs(STATIC_DIR, exist_ok=True)
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.get("/")
    def serve_index():
        index_file = os.path.join(STATIC_DIR, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return HTMLResponse("<h1>น้องโชคช่วย</h1><p>Web UI is initializing...</p>")

    return application


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8765, reload=False)
