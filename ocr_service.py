import base64
import hashlib
import io
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Union

import pypdfium2 as pdfium
from PIL import Image

import database
import extractor
from config import AppConfig
from storage_service import StorageUpload, VaultStorageCoordinator


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
    body = "".join(ch for ch in ext.lower().lstrip(".") if ("a" <= ch <= "z") or ("0" <= ch <= "9"))
    if 1 <= len(body) <= 10:
        return f".{body}"
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


def apply_intelligence_to_document(
    doc_id: str,
    current_category: str,
    current_tags: List[str],
    intel: Dict[str, Any],
    db_path: Optional[str] = None,
    *,
    force_expense: bool = False,
    force_student: bool = False,
    r2_enabled: bool = False,
) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Merge suggested metadata onto existing fields and persist Expense Record / Student Profile."""
    suggested_cat = intel.get("doc_category") or "general"
    suggested_tags = intel.get("tags") or []
    normalized_current = database.normalize_tags(current_tags or [])
    merged_tags = database.normalize_tags(normalized_current + list(suggested_tags))
    tags_changed = merged_tags != normalized_current
    cat_changed = current_category == "general" and suggested_cat != "general"

    updated_doc = None
    if cat_changed or tags_changed:
        updated_doc = database.update_document_metadata(
            doc_id,
            category=suggested_cat if cat_changed else current_category,
            tags=merged_tags if tags_changed else normalized_current,
            db_path=db_path,
            r2_enabled=r2_enabled,
        )

    expense_record = None
    settings = database.get_teacher_settings(db_path)
    if (force_expense or intel.get("is_expense")) and intel.get("expense"):
        exp = intel["expense"]
        reimb_status = exp.get("reimbursement_status")
        budget_source = exp.get("budget_source")
        # Preferences apply only on first ingestion, never on re-extraction of an existing record.
        doc = database.get_document_by_id(doc_id, db_path=db_path, r2_enabled=r2_enabled)
        existing_expense = (doc or {}).get("expense")
        if existing_expense:
            reimb_status = existing_expense["reimbursement_status"]
            budget_source = existing_expense["budget_source"]
        elif settings["configured"]:
            reimb_status = settings["default_reimbursement_status"]
            budget_source = None  # Resolve from the chosen status/category in database.py.
        if not existing_expense and "ส่วนตัว" in merged_tags and exp.get("expense_category") not in {"classroom", "kids_snack"}:
            reimb_status = "personal"
            budget_source = "personal"
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
            reimbursement_status=reimb_status,
            budget_source=budget_source,
            raw_json=intel,
            db_path=db_path,
        )

    student_record = None
    if (force_student or intel.get("is_student")) and intel.get("student"):
        stu = intel["student"]
        classroom = stu.get("classroom") or "อนุบาล"
        if classroom == "อนุบาล":
            existing_student = database.get_student_by_document_id(doc_id, db_path=db_path)
            classroom = existing_student["classroom"] if existing_student else settings["classroom"]
        if any(stu.get(k) for k in ("full_name", "nickname", "citizen_id", "parent_phone", "allergies")):
            student_record = database.upsert_student(
                document_id=doc_id,
                full_name=stu.get("full_name", ""),
                nickname=stu.get("nickname", ""),
                citizen_id=stu.get("citizen_id", ""),
                birth_date=stu.get("birth_date", ""),
                classroom=classroom,
                parent_name=stu.get("parent_name", ""),
                parent_phone=stu.get("parent_phone", ""),
                allergies=stu.get("allergies", ""),
                medical_notes=stu.get("medical_notes", ""),
                extraction_method=stu.get("extraction_method", "regex"),
                raw_json=intel,
                db_path=db_path,
            )

    return updated_doc, expense_record, student_record


def _compute_ingest_payload_hash(
    *,
    source_sha256: str,
    filename: str,
    page: int,
    process_all_pages: bool,
    category: str,
    tags: List[str],
    doc_date: str,
    auto_extract: bool,
    ingestion_channel: str,
    review_status: str,
    line_user_id: str,
) -> str:
    payload = {
        "source_sha256": source_sha256,
        "filename": filename or "unknown",
        "page": int(page),
        "process_all_pages": bool(process_all_pages),
        "category": category,
        "tags": tags,
        "doc_date": (doc_date or "").strip(),
        "auto_extract": bool(auto_extract),
        "ingestion_channel": ingestion_channel or "web_upload",
        "review_status": review_status or "pending_review",
        "line_user_id": (line_user_id or "").strip(),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    review_status: str = "pending_review",
    line_user_id: str = "",
    storage: Optional[VaultStorageCoordinator] = None,
    operation_id: str = "",
) -> Dict[str, Any]:
    """Run OCR on image/PDF bytes and optionally persist Source File, Vault Document, Expense Record, and Student Profile."""
    clean_tags = database.normalize_tags(tags)
    clean_category = (category or "general").strip() or "general"
    should_auto_extract = config.auto_extract_on_ingest if auto_extract is None else auto_extract
    source_sha256 = hashlib.sha256(contents).hexdigest()
    payload_hash = _compute_ingest_payload_hash(
        source_sha256=source_sha256,
        filename=filename or "unknown",
        page=page,
        process_all_pages=process_all_pages,
        category=clean_category,
        tags=clean_tags,
        doc_date=doc_date,
        auto_extract=bool(should_auto_extract),
        ingestion_channel=ingestion_channel,
        review_status=review_status,
        line_user_id=line_user_id,
    )

    if save_to_db and storage is not None:
        storage.require_ready()
        if operation_id:
            existing_op = storage.get_committed_operation(
                operation_id,
                expected_payload_hash=payload_hash,
            )
            if existing_op is not None:
                return existing_op

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

    doc_id: Optional[str] = None
    source_path = ""
    expense_record: Optional[Dict[str, Any]] = None
    student_record: Optional[Dict[str, Any]] = None

    def _build_result(
        *,
        result_doc_id: Optional[str],
        has_source_file: bool,
        result_category: str,
        result_tags: List[str],
        result_expense: Optional[Dict[str, Any]],
        result_student: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "success": True,
            "doc_id": result_doc_id,
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
            "has_source_file": has_source_file,
            "category": result_category,
            "tags": result_tags,
            "ingestion_channel": ingestion_channel,
            "doc_date": doc_date,
            "review_status": review_status,
            "line_user_id": line_user_id,
            "auto_extract": bool(should_auto_extract),
            "expense": result_expense,
            "student": result_student,
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

    if save_to_db:
        doc_id = str(uuid.uuid4())
        ext = _safe_extension(filename, is_pdf)
        storage_key = f"{doc_id}{ext}"
        source_path = os.path.join(config.uploads_dir, storage_key)

        intel: Optional[Dict[str, Any]] = None
        if should_auto_extract and full_text:
            try:
                intel = extractor.extract_document_intelligence(
                    full_text, config, extraction_method="auto", require_expense_hint=True
                )
            except Exception as exc:
                print(f"[OCR] Auto-extract skipped for {doc_id}: {exc}")

        if storage is not None:
            r2_enabled = config.is_r2_enabled()

            def _persist_with_storage(_rev: int) -> Dict[str, Any]:
                local_cat = clean_category
                local_tags = list(clean_tags)
                local_expense: Optional[Dict[str, Any]] = None
                local_student: Optional[Dict[str, Any]] = None
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
                    source_storage_key=storage_key,
                    source_sha256=source_sha256,
                    category=local_cat,
                    tags=local_tags,
                    ingestion_channel=ingestion_channel,
                    doc_date=doc_date,
                    image_width=orig_w,
                    image_height=orig_h,
                    review_status=review_status,
                    line_user_id=line_user_id,
                    db_path=config.db_path,
                )
                if intel is not None:
                    updated_doc, local_expense, local_student = apply_intelligence_to_document(
                        doc_id,
                        local_cat,
                        local_tags,
                        intel,
                        db_path=config.db_path,
                        force_expense=False,
                        force_student=False,
                        r2_enabled=r2_enabled,
                    )
                    if updated_doc:
                        local_cat = updated_doc["category"]
                        local_tags = updated_doc["tags"]
                return _build_result(
                    result_doc_id=doc_id,
                    has_source_file=True,
                    result_category=local_cat,
                    result_tags=local_tags,
                    result_expense=local_expense,
                    result_student=local_student,
                )

            upload_mime = content_type or ("application/pdf" if is_pdf else "image/png")
            return storage.execute_mutation(
                operation_id,
                "ingest_document",
                _persist_with_storage,
                new_upload=StorageUpload(
                    storage_key=storage_key,
                    contents=contents,
                    sha256=source_sha256,
                    content_type=upload_mime,
                ),
                payload_hash=payload_hash,
            )

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
                source_storage_key=storage_key,
                source_sha256=source_sha256,
                category=clean_category,
                tags=clean_tags,
                ingestion_channel=ingestion_channel,
                doc_date=doc_date,
                image_width=orig_w,
                image_height=orig_h,
                review_status=review_status,
                line_user_id=line_user_id,
                db_path=config.db_path,
            )
        except Exception:
            if source_path and os.path.exists(source_path):
                os.remove(source_path)
            raise

        if intel is not None:
            try:
                updated_doc, expense_record, student_record = apply_intelligence_to_document(
                    doc_id,
                    clean_category,
                    clean_tags,
                    intel,
                    db_path=config.db_path,
                    force_expense=False,
                    force_student=False,
                    r2_enabled=config.is_r2_enabled(),
                )
                if updated_doc:
                    clean_category = updated_doc["category"]
                    clean_tags = updated_doc["tags"]
            except Exception as exc:
                print(f"[OCR] Auto-extract skipped for {doc_id}: {exc}")

    return _build_result(
        result_doc_id=doc_id,
        has_source_file=bool(source_path and os.path.exists(source_path)),
        result_category=clean_category,
        result_tags=clean_tags,
        result_expense=expense_record,
        result_student=student_record,
    )
