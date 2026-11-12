import base64
import io
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Union

import pypdfium2 as pdfium
from PIL import Image

import database
import extractor
from config import AppConfig


def image_to_base64(img: Image.Image, max_dimension: int = 1800) -> str:
    """Resize large images for preview storage and encode as JPEG base64 data URI."""
    w, h = img.size
    if max(w, h) > max_dimension:
        ratio = max_dimension / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.Resampling.LANCZOS)

    buffered = io.BytesIO()
    img.save(buffered, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffered.getvalue()).decode("utf-8")


def _safe_extension(filename: str, is_pdf: bool) -> str:
    _, ext = os.path.splitext(filename or "")
    cleaned = "".join(ch for ch in ext.lower() if ch.isalnum() or ch == ".")
    if cleaned and len(cleaned) <= 10:
        return cleaned
    return ".pdf" if is_pdf else ".png"


def _persist_source_file(
    contents: bytes,
    doc_id: str,
    filename: str,
    is_pdf: bool,
    uploads_dir: str,
) -> str:
    os.makedirs(uploads_dir, exist_ok=True)
    ext = _safe_extension(filename, is_pdf)
    dest_path = os.path.join(uploads_dir, f"{doc_id}{ext}")
    with open(dest_path, "wb") as fh:
        fh.write(contents)
    return dest_path


def _render_document_images(
    contents: bytes,
    is_pdf: bool,
    page: int,
    process_all_pages: bool,
) -> tuple[List[Image.Image], int]:
    images: List[Image.Image] = []
    if is_pdf:
        pdf = pdfium.PdfDocument(contents)
        total_pages = len(pdf)
        if total_pages == 0:
            raise ValueError("PDF contains 0 pages")
        if process_all_pages:
            for i in range(total_pages):
                images.append(pdf[i].render(scale=2.0).to_pil())
        else:
            target_page = max(1, min(page, total_pages)) - 1
            images.append(pdf[target_page].render(scale=2.0).to_pil())
        return images, total_pages

    img = Image.open(io.BytesIO(contents)).convert("RGB")
    return [img], 1


def ingest_document(
    contents: bytes,
    filename: str,
    engine: Any,
    config: AppConfig,
    *,
    content_type: Optional[str] = None,
    page: int = 1,
    process_all_pages: bool = False,
    save_to_db: bool = True,
    category: str = "general",
    tags: Optional[Union[str, List[str]]] = None,
    ingestion_channel: str = "web_upload",
    doc_date: str = "",
    auto_extract: Optional[bool] = None,
) -> Dict[str, Any]:
    """Run OCR on image/PDF bytes and optionally persist Source File and Vault Document."""
    lower_name = (filename or "unknown").lower()
    is_pdf = lower_name.endswith(".pdf") or content_type == "application/pdf"
    file_size = len(contents)

    images, total_pages = _render_document_images(contents, is_pdf, page, process_all_pages)

    start_time = time.time()
    combined_text: List[str] = []
    all_lines: List[Dict[str, Any]] = []
    all_page_results: List[Dict[str, Any]] = []
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
                "page": page_num,
            }
            clean_lines.append(line_data)
            all_lines.append(line_data)

        combined_text.append(
            f"--- หน้า {page_num} ---\n{page_text}"
            if total_pages > 1 and process_all_pages
            else page_text
        )
        all_page_results.append(
            {
                "page": page_num,
                "text": page_text,
                "lines_count": len(clean_lines),
                "rotation": ocr_res.get("rotation", 0),
                "text_angle": ocr_res.get("text_angle", 0.0),
                "duration_ms": round(page_duration, 1),
            }
        )

    total_duration_ms = round((time.time() - start_time) * 1000, 1)
    full_text = "\n\n".join(combined_text).strip()
    confidences = [l["confidence"] for l in all_lines if l.get("confidence") is not None]
    avg_conf = round(sum(confidences) / len(confidences), 4) if confidences else 1.0

    clean_tags = database.normalize_tags(tags)
    clean_category = (category or "general").strip() or "general"

    doc_id: Optional[str] = None
    source_path = ""
    expense_record: Optional[Dict[str, Any]] = None
    should_auto_extract = config.auto_extract_on_ingest if auto_extract is None else auto_extract

    if save_to_db:
        doc_id = str(uuid.uuid4())
        source_path = _persist_source_file(contents, doc_id, filename, is_pdf, config.uploads_dir)
        try:
            database.save_document(
                doc_id=doc_id,
                filename=filename or "uploaded_doc",
                file_type="pdf" if is_pdf else "image",
                text_content=full_text,
                lines=all_lines,
                avg_confidence=avg_conf,
                duration_ms=total_duration_ms,
                total_pages=total_pages if (is_pdf and process_all_pages) else 1,
                image_preview=first_img_b64,
                file_size=file_size,
                source_path=source_path,
                category=clean_category,
                tags=clean_tags,
                ingestion_channel=ingestion_channel,
                doc_date=doc_date,
                image_width=orig_w,
                image_height=orig_h,
                db_path=config.db_path,
            )
            if should_auto_extract and full_text:
                intel = extractor.extract_document_intelligence(
                    full_text, config, mode="auto", require_expense_hint=True
                )
                suggested_cat = intel.get("doc_category") or "general"
                suggested_tags = intel.get("tags") or []
                if (clean_category == "general" and suggested_cat != "general") or (
                    not clean_tags and suggested_tags
                ):
                    new_cat = suggested_cat if clean_category == "general" else clean_category
                    new_tags = suggested_tags if not clean_tags else clean_tags
                    updated = database.update_document_metadata(
                        doc_id,
                        category=new_cat,
                        tags=new_tags,
                        db_path=config.db_path,
                    )
                    if updated:
                        clean_category = updated["category"]
                        clean_tags = updated["tags"]

                if intel.get("is_expense") and intel.get("expense"):
                    exp = intel["expense"]
                    expense_record = database.upsert_expense(
                        document_id=doc_id,
                        merchant=exp["merchant"],
                        expense_date=exp["expense_date"],
                        subtotal=exp["subtotal"],
                        vat_amount=exp["vat_amount"],
                        total_amount=exp["total_amount"],
                        currency=exp["currency"],
                        expense_category=exp["expense_category"],
                        extraction_method=exp["extraction_method"],
                        raw_json=intel,
                        db_path=config.db_path,
                    )
        except Exception:
            if source_path and os.path.exists(source_path):
                os.remove(source_path)
            raise

    return {
        "success": True,
        "doc_id": doc_id,
        "filename": filename,
        "is_pdf": is_pdf,
        "total_pages": total_pages,
        "current_page": page if not process_all_pages else 1,
        "processed_pages_count": len(images),
        "text": full_text,
        "lines": all_lines,
        "image_preview": first_img_b64,
        "image_width": orig_w,
        "image_height": orig_h,
        "has_source_file": bool(source_path and os.path.exists(source_path)),
        "category": clean_category,
        "tags": clean_tags,
        "ingestion_channel": ingestion_channel,
        "doc_date": doc_date,
        "expense": expense_record,
        "stats": {
            "duration_ms": total_duration_ms,
            "avg_ms_per_page": round(total_duration_ms / max(1, len(images)), 1),
            "total_lines": len(all_lines),
            "avg_confidence": avg_conf,
            "rotation": all_page_results[0]["rotation"] if all_page_results else 0,
            "text_angle": all_page_results[0]["text_angle"] if all_page_results else 0.0,
        },
        "page_details": all_page_results,
    }
