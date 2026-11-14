import csv
import io
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "model")
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, MODEL_DIR)

import database
import extractor
import line_service
import ocr_service
from config import AppConfig
from runtime.line_engine import LineOCR
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


def _default_engine_factory() -> LineOCR:
    print("[Engine] Initializing OpenThai 2.0 OCR Engine from:", MODEL_DIR)
    engine = LineOCR(MODEL_DIR, threads=1)
    print("[Engine] OpenThai 2.0 OCR Engine ready!")
    return engine


def create_app(
    config: Optional[AppConfig] = None,
    engine_factory: Optional[Callable[[], Any]] = None,
) -> FastAPI:
    cfg = config or AppConfig.from_env()
    factory = engine_factory or _default_engine_factory

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI):
        cfg.ensure_dirs()
        database.init_db(cfg.db_path)
        if fastapi_app.state.engine is None:
            fastapi_app.state.engine = factory()
        fastapi_app.state.watcher.start()
        try:
            yield
        finally:
            fastapi_app.state.watcher.stop()

    application = FastAPI(title="น้องโชคช่วย (Nong ChokChuay)", version="2.0.0", lifespan=lifespan)
    application.state.config = cfg
    application.state.engine_factory = factory
    application.state.engine = None

    def get_engine() -> Any:
        if application.state.engine is None:
            application.state.engine = factory()
        return application.state.engine

    application.state.get_engine = get_engine
    application.state.watcher = InboxWatcher(config=cfg, engine_getter=get_engine)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/api/system")
    def get_system_info():
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
    def run_kindergarten_preset(preset: str):
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
        docs = database.search_documents(
            query=query,
            category=category,
            tag=tag,
            review_status=review_status,
            limit=limit,
            offset=offset,
            db_path=cfg.db_path,
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
        doc = database.get_document_by_id(doc_id, db_path=cfg.db_path)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"success": True, "document": doc}

    @application.get("/api/documents/{doc_id}/file")
    def download_document_source(doc_id: str):
        doc = database.get_document_by_id(doc_id, db_path=cfg.db_path)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        src_path = doc.get("source_path") or ""
        if not src_path or not os.path.exists(src_path):
            raise HTTPException(status_code=404, detail="Source file not found")
        return FileResponse(src_path, filename=doc.get("filename") or os.path.basename(src_path))

    @application.patch("/api/documents/{doc_id}")
    def update_document(doc_id: str, payload: DocumentUpdateRequest):
        updated = database.update_document_metadata(
            doc_id=doc_id,
            text_content=payload.text,
            category=payload.category,
            tags=payload.tags,
            doc_date=payload.doc_date,
            review_status=payload.review_status,
            db_path=cfg.db_path,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"success": True, "document": updated}

    @application.post("/api/documents/{doc_id}/review")
    def review_document(doc_id: str, payload: Optional[DocumentReviewRequest] = Body(default=None)):
        target_review = payload.review_status if payload else "reviewed"
        updated = database.update_document_metadata(
            doc_id=doc_id,
            review_status=target_review,
            db_path=cfg.db_path,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Document not found")
        if payload and payload.reimbursement_status and updated.get("expense"):
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
        return {"success": True, "document": updated}

    @application.delete("/api/documents/{doc_id}")
    def delete_document(doc_id: str):
        success = database.delete_document(doc_id, db_path=cfg.db_path)
        return {"success": success, "deleted_id": doc_id}

    @application.get("/api/database/download")
    def download_database():
        if not os.path.exists(cfg.db_path):
            raise HTTPException(status_code=404, detail="Database file not found")
        return FileResponse(cfg.db_path, filename="nong_chokchuay_vault.db", media_type="application/octet-stream")

    @application.get("/api/watcher/status")
    def watcher_status():
        return application.state.watcher.get_status()

    @application.post("/api/watcher/scan")
    def watcher_scan():
        result = application.state.watcher.scan_once(require_stability=False)
        return {"success": True, **result}

    # Expense Extraction & Reporting Endpoints
    @application.post("/api/documents/{doc_id}/extract")
    def extract_document_expense(doc_id: str, extraction_method: str = Query("auto")):
        doc = database.get_document_by_id(doc_id, db_path=cfg.db_path)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        intel = extractor.extract_document_intelligence(
            doc.get("text") or "",
            cfg,
            extraction_method=extraction_method,
            require_expense_hint=False,
        )
        updated_doc, expense, student = ocr_service.apply_intelligence_to_document(
            doc_id,
            doc.get("category") or "general",
            doc.get("tags") or [],
            intel,
            db_path=cfg.db_path,
            force_expense=True,
        )
        return {"success": True, "expense": expense, "student": student, "document": updated_doc}

    @application.get("/api/expenses")
    def list_expenses(
        month: Optional[str] = Query(None),
        expense_category: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
        reimbursement_status: Optional[str] = Query(None),
    ):
        data = database.list_expenses(
            month=month,
            expense_category=expense_category,
            tag=tag,
            reimbursement_status=reimbursement_status,
            db_path=cfg.db_path,
        )
        return {"success": True, **data}

    @application.put("/api/expenses/{doc_id}")
    def update_expense(doc_id: str, payload: ExpenseUpsertRequest):
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
        if not expense:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"success": True, "expense": expense}

    @application.delete("/api/expenses/{doc_id}")
    def delete_expense(doc_id: str):
        deleted = database.delete_expense(doc_id, db_path=cfg.db_path)
        return {"success": deleted, "document_id": doc_id}

    @application.get("/api/expenses/export.csv")
    def export_expenses_csv(
        month: Optional[str] = Query(None),
        expense_category: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
        reimbursement_status: Optional[str] = Query(None),
    ):
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
        data = database.list_students(
            query=query,
            classroom=classroom,
            has_allergy=has_allergy,
            db_path=cfg.db_path,
        )
        return {"success": True, **data}

    @application.post("/api/students")
    def create_student(payload: StudentUpsertRequest):
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
        return {"success": True, "student": student}

    @application.put("/api/students/{student_id}")
    def update_student(student_id: str, payload: StudentUpsertRequest):
        existing = database.get_student_by_id(student_id, db_path=cfg.db_path)
        if not existing:
            raise HTTPException(status_code=404, detail="Student not found")
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
        return {"success": True, "student": student}

    @application.delete("/api/students/{student_id}")
    def delete_student(student_id: str):
        deleted = database.delete_student(student_id, db_path=cfg.db_path)
        if not deleted:
            raise HTTPException(status_code=404, detail="Student not found")
        return {"success": True, "deleted_id": student_id}

    @application.get("/api/students/export.csv")
    def export_students_csv(
        query: Optional[str] = Query(None),
        classroom: Optional[str] = Query(None),
        has_allergy: Optional[bool] = Query(None),
    ):
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
        body = await request.body()
        signature = request.headers.get("X-Line-Signature", "")
        if not line_service.verify_line_signature(body, signature, cfg.line_channel_secret):
            raise HTTPException(status_code=401, detail="Invalid LINE signature")

        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}")

        events = payload.get("events") or []
        replies: List[Dict[str, Any]] = []

        for event in events:
            etype = event.get("type")
            reply_token = event.get("replyToken", "")
            user_id = (event.get("source") or {}).get("userId", "")
            reply_obj: Optional[Dict[str, Any]] = None

            if etype == "message":
                msg = event.get("message") or {}
                mtype = msg.get("type")
                if mtype == "text":
                    reply_obj = line_service.process_line_text_command(
                        msg.get("text", ""),
                        db_path=cfg.db_path,
                        public_base_url=cfg.public_base_url,
                        config=cfg,
                        teacher_name=cfg.teacher_name,
                    )
                elif mtype in ("image", "file"):
                    msg_id = msg.get("id", "")
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
                            )
                            reply_obj = line_service.build_nong_chok_reply_summary(
                                ocr_res,
                                expense=ocr_res.get("expense"),
                                student=ocr_res.get("student"),
                                public_base_url=cfg.public_base_url,
                                config=cfg,
                                teacher_name=cfg.teacher_name,
                            )
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
                reply_obj = line_service.handle_line_postback(
                    pb_data,
                    db_path=cfg.db_path,
                    config=cfg,
                    teacher_name=cfg.teacher_name,
                )

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

        return {"success": True, "processed_events": len(replies), "replies": replies}

    @application.post("/api/line/simulate")
    async def line_simulate(
        text: str = Form(""),
        postback_data: str = Form(""),
        file: Optional[UploadFile] = File(None),
    ):
        if postback_data.strip():
            res = line_service.handle_line_postback(
                postback_data.strip(),
                db_path=cfg.db_path,
                config=cfg,
                teacher_name=cfg.teacher_name,
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
        )
        return JSONResponse({"success": True, "mode": "text", **res})

    # OCR Processing Endpoint
    @application.post("/api/ocr")
    async def process_ocr(
        file: UploadFile = File(...),
        page: int = Form(1),
        process_all_pages: bool = Form(False),
        save_to_db: bool = Form(True),
        category: str = Form("general"),
        tags: str = Form(""),
        doc_date: str = Form(""),
        auto_extract: Optional[bool] = Form(None),
    ):
        try:
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
            )
            return JSONResponse(result)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))
        except HTTPException:
            raise
        except Exception as e:
            import traceback

            traceback.print_exc()
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    STATIC_DIR = os.path.join(BASE_DIR, "static")
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
