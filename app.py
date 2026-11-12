import os
import sys
import time
import io
import base64
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, Callable
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
import pypdfium2 as pdfium

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "model")
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, MODEL_DIR)

import database
from config import AppConfig
from runtime.line_engine import LineOCR

def _default_engine_factory() -> LineOCR:
    print("[Engine] Initializing OpenThai 2.0 OCR Engine from:", MODEL_DIR)
    engine = LineOCR(MODEL_DIR, threads=1)
    print("[Engine] OpenThai 2.0 OCR Engine ready!")
    return engine

def image_to_base64(img: Image.Image, max_dimension: int = 1800) -> str:
    w, h = img.size
    if max(w, h) > max_dimension:
        ratio = max_dimension / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.Resampling.LANCZOS)
    
    buffered = io.BytesIO()
    img.save(buffered, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffered.getvalue()).decode("utf-8")

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
        yield

    application = FastAPI(title="OpenThai 2.0 OCR Studio", version="1.2.0", lifespan=lifespan)
    application.state.config = cfg
    application.state.engine_factory = factory
    application.state.engine = None

    def get_engine() -> Any:
        if application.state.engine is None:
            application.state.engine = factory()
        return application.state.engine

    application.state.get_engine = get_engine

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
    def list_documents(query: Optional[str] = Query(None), limit: int = 50, offset: int = 0):
        docs = database.search_documents(query=query, limit=limit, offset=offset, db_path=cfg.db_path)
        stats = database.get_db_stats(db_path=cfg.db_path)
        return {
            "success": True,
            "query": query,
            "total": len(docs),
            "db_stats": stats,
            "documents": docs
        }

    @application.get("/api/documents/{doc_id}")
    def get_document(doc_id: str):
        doc = database.get_document_by_id(doc_id, db_path=cfg.db_path)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"success": True, "document": doc}

    @application.delete("/api/documents/{doc_id}")
    def delete_document(doc_id: str):
        success = database.delete_document(doc_id, db_path=cfg.db_path)
        return {"success": success, "deleted_id": doc_id}

    @application.get("/api/database/download")
    def download_database():
        if not os.path.exists(cfg.db_path):
            raise HTTPException(status_code=404, detail="Database file not found")
        return FileResponse(cfg.db_path, filename="opentha_documents.db", media_type="application/octet-stream")

    # OCR Processing Endpoint
    @application.post("/api/ocr")
    async def process_ocr(
        file: UploadFile = File(...),
        page: int = Form(1),
        process_all_pages: bool = Form(False),
        save_to_db: bool = Form(True)
    ):
        try:
            contents = await file.read()
            filename = (file.filename or "unknown").lower()
            file_size = len(contents)
            engine = get_engine()

            images: List[Image.Image] = []
            is_pdf = filename.endswith(".pdf") or file.content_type == "application/pdf"
            total_pages = 1

            if is_pdf:
                try:
                    pdf = pdfium.PdfDocument(contents)
                    total_pages = len(pdf)
                    if total_pages == 0:
                        raise HTTPException(status_code=400, detail="PDF contains 0 pages")
                    
                    if process_all_pages:
                        for i in range(total_pages):
                            p = pdf[i]
                            images.append(p.render(scale=2.0).to_pil())
                    else:
                        target_page = max(1, min(page, total_pages)) - 1
                        p = pdf[target_page]
                        images.append(p.render(scale=2.0).to_pil())
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"Failed to read PDF: {str(e)}")
            else:
                try:
                    img = Image.open(io.BytesIO(contents)).convert("RGB")
                    images.append(img)
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"Invalid image format: {str(e)}")

            start_time = time.time()
            combined_text = []
            all_lines: List[Dict[str, Any]] = []
            all_page_results = []
            first_img_b64 = ""
            orig_w, orig_h = 0, 0

            for idx, img in enumerate(images):
                w, h = img.size
                if idx == 0:
                    orig_w, orig_h = w, h
                    first_img_b64 = image_to_base64(img)

                t_page_start = time.time()
                ocr_res = engine.read(img)
                page_duration = (time.time() - t_page_start) * 1000

                page_num = (idx + 1) if process_all_pages else page
                page_text = ocr_res.get("text", "")
                lines = ocr_res.get("lines", [])

                clean_lines = []
                for l_idx, line in enumerate(lines):
                    line_data = {
                        "text": line.get("text", ""),
                        "confidence": round(float(line.get("confidence", 0.0)), 4),
                        "row": line.get("row", l_idx),
                        "box": line.get("box", []),
                        "page": page_num
                    }
                    clean_lines.append(line_data)
                    all_lines.append(line_data)

                combined_text.append(f"--- หน้า {page_num} ---\n" + page_text if total_pages > 1 and process_all_pages else page_text)

                all_page_results.append({
                    "page": page_num,
                    "text": page_text,
                    "lines_count": len(clean_lines),
                    "rotation": ocr_res.get("rotation", 0),
                    "text_angle": ocr_res.get("text_angle", 0.0),
                    "duration_ms": round(page_duration, 1)
                })

            total_duration_ms = round((time.time() - start_time) * 1000, 1)
            full_text = "\n\n".join(combined_text).strip()

            confidences = [l["confidence"] for l in all_lines if l.get("confidence") is not None]
            avg_conf = round(sum(confidences) / len(confidences), 4) if confidences else 1.0

            doc_id = None
            if save_to_db:
                try:
                    doc_id = database.save_document(
                        filename=file.filename or "uploaded_doc",
                        file_type="pdf" if is_pdf else "image",
                        text_content=full_text,
                        lines=all_lines,
                        avg_confidence=avg_conf,
                        duration_ms=total_duration_ms,
                        total_pages=total_pages if (is_pdf and process_all_pages) else 1,
                        image_preview=first_img_b64,
                        file_size=file_size,
                        db_path=cfg.db_path
                    )
                except Exception as dbe:
                    print("[DB Error] Failed to save document to database:", dbe)

            return JSONResponse({
                "success": True,
                "doc_id": doc_id,
                "filename": file.filename,
                "is_pdf": is_pdf,
                "total_pages": total_pages,
                "current_page": page if not process_all_pages else 1,
                "processed_pages_count": len(images),
                "text": full_text,
                "lines": all_lines,
                "image_preview": first_img_b64,
                "image_width": orig_w,
                "image_height": orig_h,
                "stats": {
                    "duration_ms": total_duration_ms,
                    "avg_ms_per_page": round(total_duration_ms / max(1, len(images)), 1),
                    "total_lines": len(all_lines),
                    "avg_confidence": avg_conf,
                    "rotation": all_page_results[0]["rotation"] if all_page_results else 0,
                    "text_angle": all_page_results[0]["text_angle"] if all_page_results else 0.0
                },
                "page_details": all_page_results
            })

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
