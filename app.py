import csv
import io
import os
import sys
from contextlib import asynccontextmanager
from typing import Optional, List, Any, Callable
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "model")
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, MODEL_DIR)

import database
import extractor
import ocr_service
from config import AppConfig
from runtime.line_engine import LineOCR
from watcher import InboxWatcher


class DocumentUpdateRequest(BaseModel):
    text: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    doc_date: Optional[str] = None


class ExpenseUpsertRequest(BaseModel):
    merchant: str = ""
    expense_date: str = ""
    subtotal: float = 0.0
    vat_amount: float = 0.0
    total_amount: float = 0.0
    currency: str = "THB"
    expense_category: str = "other"


def _default_engine_factory() -> LineOCR:
    print("[Engine] Initializing OpenThai 2.0 OCR Engine from:", MODEL_DIR)
    engine = LineOCR(MODEL_DIR, threads=1)
    print("[Engine] OpenThai 2.0 OCR Engine ready!")
    return engine

def create_app(
    config: Optional[AppConfig] = None,
    engine_factory: Optional[Callable[[], Any]] = None
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

    application = FastAPI(title="OpenThai 2.0 OCR Studio", version="1.2.0", lifespan=lifespan)
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
            "engine": "OpenThai 2.0 OCR Text (Local ONNX)",
            "model_architecture": "PaddlePaddle PP-OCRv6-tiny Detector + Thai CTC Recognizer",
            "parameters": "1.02M (det: 428K, rec: 587K)",
            "model_size": "~4.2 MB",
            "hardware": "CPU (ONNX Runtime / OpenVINO)",
            "offline": True,
            "llm_configured": bool(cfg.llm_api_key),
            "llm_provider": cfg.llm_provider if cfg.llm_api_key else None,
            "license": "Apache-2.0",
            "database": {
                "type": "SQLite with Trigram Full-Text Search (FTS5)",
                "path": db_stats["db_path"],
                "total_documents": db_stats["total_documents"],
                "total_pages": db_stats["total_pages"],
                "total_characters": db_stats["total_characters"]
            },
            "python_version": sys.version.split()[0]
        }

    @application.get("/api/samples")
    def get_samples():
        samples_dir = os.path.join(BASE_DIR, "samples")
        samples = []
        if os.path.exists(samples_dir):
            files = {
                "sample_official_document.png": "บันทึกข้อความราชการ & ตารางผลทดสอบ",
                "sample_budget_table.png": "ตารางรายงานงบประมาณรายจ่ายประจำปี"
            }
            for fname, label in files.items():
                fpath = os.path.join(samples_dir, fname)
                if os.path.exists(fpath):
                    samples.append({
                        "id": fname,
                        "title": label,
                        "url": f"/api/sample/{fname}"
                    })
        return samples

    @application.get("/api/sample/{filename}")
    def get_sample_file(filename: str):
        fpath = os.path.join(BASE_DIR, "samples", filename)
        if not os.path.exists(fpath):
            raise HTTPException(status_code=404, detail="Sample not found")
        return FileResponse(fpath)

    # Database Endpoints
    @application.get("/api/documents")
    def list_documents(
        query: Optional[str] = Query(None),
        category: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
        limit: int = 50,
        offset: int = 0,
    ):
        docs = database.search_documents(
            query=query,
            category=category,
            tag=tag,
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
            db_path=cfg.db_path,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"success": True, "document": updated}

    @application.delete("/api/documents/{doc_id}")
    def delete_document(doc_id: str):
        success = database.delete_document(doc_id, db_path=cfg.db_path)
        return {"success": success, "deleted_id": doc_id}

    @application.get("/api/database/download")
    def download_database():
        if not os.path.exists(cfg.db_path):
            raise HTTPException(status_code=404, detail="Database file not found")
        return FileResponse(cfg.db_path, filename="opentha_documents.db", media_type="application/octet-stream")

    @application.get("/api/watcher/status")
    def watcher_status():
        return application.state.watcher.get_status()

    @application.post("/api/watcher/scan")
    def watcher_scan():
        result = application.state.watcher.scan_once(require_stability=False)
        return {"success": True, **result}

    # Expense Extraction & Reporting Endpoints
    @application.post("/api/documents/{doc_id}/extract")
    def extract_document_expense(doc_id: str, mode: str = Query("auto")):
        doc = database.get_document_by_id(doc_id, db_path=cfg.db_path)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        intel = extractor.extract_document_intelligence(
            doc.get("text") or "",
            cfg,
            mode=mode,
            require_expense_hint=False,
        )
        extracted = intel.get("expense") or extractor.extract_expense_regex(doc.get("text") or "")

        suggested_cat = intel.get("doc_category") or "general"
        suggested_tags = intel.get("tags") or []
        updated_doc = None
        if ((doc.get("category") or "general") == "general" and suggested_cat != "general") or (
            not doc.get("tags") and suggested_tags
        ):
            updated_doc = database.update_document_metadata(
                doc_id,
                category=suggested_cat if (doc.get("category") or "general") == "general" else doc.get("category"),
                tags=suggested_tags if not doc.get("tags") else doc.get("tags"),
                db_path=cfg.db_path,
            )

        expense = database.upsert_expense(
            document_id=doc_id,
            merchant=extracted["merchant"],
            expense_date=extracted["expense_date"],
            subtotal=extracted["subtotal"],
            vat_amount=extracted["vat_amount"],
            total_amount=extracted["total_amount"],
            currency=extracted["currency"],
            expense_category=extracted["expense_category"],
            extraction_method=extracted["extraction_method"],
            raw_json=intel,
            db_path=cfg.db_path,
        )
        return {"success": True, "expense": expense, "document": updated_doc}

    @application.get("/api/expenses")
    def list_expenses(
        month: Optional[str] = Query(None),
        expense_category: Optional[str] = Query(None),
    ):
        data = database.list_expenses(
            month=month,
            expense_category=expense_category,
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
    ):
        data = database.list_expenses(
            month=month,
            expense_category=expense_category,
            db_path=cfg.db_path,
        )
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "expense_date",
            "merchant",
            "expense_category",
            "subtotal",
            "vat_amount",
            "total_amount",
            "currency",
            "extraction_method",
            "filename",
            "document_id",
        ])
        for item in data["expenses"]:
            writer.writerow([
                item["expense_date"],
                item["merchant"],
                item["expense_category"],
                f"{item['subtotal']:.2f}",
                f"{item['vat_amount']:.2f}",
                f"{item['total_amount']:.2f}",
                item["currency"],
                item["extraction_method"],
                item.get("filename", ""),
                item["document_id"],
            ])
        csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
        suffix = f"_{month}" if month else ""
        return Response(
            content=csv_bytes,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="expenses{suffix}.csv"'},
        )

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
        return HTMLResponse("<h1>OpenThai 2.0 OCR Studio</h1><p>Web UI is initializing...</p>")

    return application


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8765, reload=False)
