import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from config import AppConfig

THAI_MONTHS = {
    "ม.ค.": 1, "มค": 1, "มกราคม": 1,
    "ก.พ.": 2, "กพ": 2, "กุมภาพันธ์": 2,
    "มี.ค.": 3, "มีค": 3, "มีนาคม": 3,
    "เม.ย.": 4, "เมย": 4, "เมษายน": 4,
    "พ.ค.": 5, "พค": 5, "พฤษภาคม": 5,
    "มิ.ย.": 6, "มิย": 6, "มิถุนายน": 6,
    "ก.ค.": 7, "กค": 7, "กรกฎาคม": 7,
    "ส.ค.": 8, "สค": 8, "สิงหาคม": 8,
    "ก.ย.": 9, "กย": 9, "กันยายน": 9,
    "ต.ค.": 10, "ตค": 10, "ตุลาคม": 10,
    "พ.ย.": 11, "พย": 11, "พฤศจิกายน": 11,
    "ธ.ค.": 12, "ธค": 12, "ธันวาคม": 12,
}

_GENERIC_HEADER_RE = re.compile(
    r"^(?:ใบเสร็จรับเงิน|ใบกำกับภาษี(?:อย่างย่อ)?|ใบเสร็จรับเงิน/ใบกำกับภาษี(?:อย่างย่อ)?|"
    r"tax\s*invoice(?:\s*\(abb\))?|receipt|โอนเงินสำเร็จ|สลิปโอนเงิน|รายการสำเร็จ|"
    r"เลขประจำตัวผู้เสียภาษี.*|tax\s*id.*)$",
    re.IGNORECASE,
)
_PURE_META_LINE_RE = re.compile(r"^[\d\s/\-.:#%()]+$")

VALID_DOC_CATEGORIES = {
    "general",
    "receipt",
    "tax",
    "official",
    "contract",
    "manual",
    "school",
    "teaching",
    "student",
}

VALID_EXPENSE_CATEGORIES = {
    "food",
    "transport",
    "utilities",
    "office",
    "classroom",
    "kids_snack",
    "other",
}

VALID_REIMBURSEMENT_STATUSES = {
    "pending_reimbursement",
    "reimbursed",
    "personal",
}

VALID_BUDGET_SOURCES = {
    "classroom",
    "kids_snack",
    "subsidy",
    "school_project",
    "personal",
    "other",
}

_EXPENSE_CATEGORY_KEYWORDS = {
    "classroom": (
        "สื่อการสอน",
        "สีเทียน",
        "สีน้ำ",
        "สีไม้",
        "ดินน้ำมัน",
        "กระดาษสี",
        "กระดาษโปสเตอร์",
        "สติกเกอร์",
        "กาว",
        "กรรไกร",
        "ลูกปัด",
        "ของเล่นเสริมพัฒนาการ",
        "ของเล่น",
        "งานประดิษฐ์",
        "นิทาน",
        "สมุดระบายสี",
        "บอร์ดห้องเรียน",
        "แฟลชการ์ด",
        "สื่ออนุบาล",
    ),
    "kids_snack": (
        "นมโรงเรียน",
        "นมกล่อง",
        "นม uht",
        "ขนมเด็ก",
        "อาหารว่างเด็ก",
        "อาหารว่าง",
        "น้ำผลไม้เด็ก",
        "เยลลี่",
        "อาหารกลางวัน",
    ),
    "food": ("กาแฟ", "cafe", "amazon", "starbucks", "ข้าว", "อาหาร", "ก๋วยเตี๋ยว", "7-eleven", "เซเว่น", "restaurant", "coffee"),
    "transport": ("grab", "bolt", "ptt", "บางจาก", "ปั๊ม", "น้ำมัน", "ทางด่วน", "bts", "mrt", "แท็กซี่", "สายการบิน"),
    "utilities": ("การไฟฟ้า", "การประปา", "ค่าไฟ", "ค่าน้ำ", "ais", "true", "dtac", "3bb", "อินเทอร์เน็ต"),
    "office": ("officemate", "b2s", "เครื่องเขียน", "กระดาษ", "หมึก", "cloud", "software", "hosting"),
}

_STRONG_STUDENT_KEYWORDS = (
    "สูติบัตร",
    "ใบสมัครเรียน",
    "ทะเบียนนักเรียน",
    "ประวัตินักเรียน",
    "ประวัติเด็ก",
    "ข้อมูลนักเรียน",
    "แบบบันทึกประวัตินักเรียน",
)

_STUDENT_PROFILE_SIGNALS = (
    "เด็กชาย",
    "เด็กหญิง",
    "ด.ช.",
    "ด.ญ.",
    "ชื่อเล่น",
    "เลขประจำตัวประชาชน",
    "เลขบัตรประชาชน",
    "แพ้อาหาร",
    "โรคประจำตัว",
    "เกิดวันที่",
    "วันเดือนปีเกิด",
    "ชื่อผู้ปกครอง",
    "เบอร์โทรผู้ปกครอง",
    "ผู้ปกครอง",
)

_ALLERGY_KEYWORDS = (
    "แพ้นมวัว",
    "แพ้นม",
    "แพ้ถั่วลิสง",
    "แพ้ถั่ว",
    "แพ้ไข่",
    "แพ้อาหารทะเล",
    "แพ้กุ้ง",
    "แพ้ปู",
    "แพ้ปลา",
    "แพ้แป้งสาลี",
    "แพ้กลูเตน",
    "แพ้ช็อกโกแลต",
)

_MEDICAL_KEYWORDS = (
    "G6PD",
    "g6pd",
    "หอบหืด",
    "ลมชัก",
    "ภูมิแพ้",
    "เบาหวาน",
    "โรคหัวใจ",
)


def is_llm_configured(config: AppConfig) -> bool:
    """Return True when a cloud LLM API key or local OpenAI-compatible URL is configured."""
    return bool(config.llm_api_key or (config.llm_provider != "gemini" and config.llm_base_url))


def _normalize_year(raw_year: int) -> int:
    if raw_year < 100:
        raw_year = (2500 + raw_year) if raw_year >= 40 else (2000 + raw_year)
    if raw_year >= 2400:
        return raw_year - 543
    return raw_year


def _valid_iso_date(year: int, month: int, day: int) -> str:
    if not (1940 <= year <= 2100):
        return ""
    try:
        datetime(year, month, day)
        return f"{year:04d}-{month:02d}-{day:02d}"
    except ValueError:
        return ""


def _extract_date(text: str) -> str:
    month_keys = "|".join(sorted((re.escape(k) for k in THAI_MONTHS), key=len, reverse=True))
    thai_date_re = re.compile(rf"(\d{{1,2}})\s*({month_keys})\s*(\d{{2,4}})")
    for m in thai_date_re.finditer(text):
        iso = _valid_iso_date(
            _normalize_year(int(m.group(3))),
            THAI_MONTHS.get(m.group(2), 0),
            int(m.group(1)),
        )
        if iso:
            return iso

    iso_re = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
    for m in iso_re.finditer(text):
        iso = _valid_iso_date(_normalize_year(int(m.group(1))), int(m.group(2)), int(m.group(3)))
        if iso:
            return iso

    dmy_re = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
    for m in dmy_re.finditer(text):
        iso = _valid_iso_date(_normalize_year(int(m.group(3))), int(m.group(2)), int(m.group(1)))
        if iso:
            return iso

    return ""


def _parse_amount(num_str: str) -> float:
    try:
        return round(float(num_str.replace(",", "").strip()), 2)
    except ValueError:
        return 0.0


def _extract_amount_for_patterns(lines: List[str], patterns: List[re.Pattern[str]]) -> float:
    for pat in patterns:
        for line in reversed(lines):
            m = pat.search(line)
            if m:
                val = _parse_amount(m.group(1))
                if val > 0:
                    return val
    return 0.0


def _infer_merchant(lines: List[str]) -> str:
    recipient_re = re.compile(r"(?:ไปยัง|ผู้รับ|ชื่อบัญชี|ร้านค้า)[:\s]+(.+)")
    for line in lines:
        m = recipient_re.search(line)
        if m:
            candidate = m.group(1).strip()
            if candidate and not _PURE_META_LINE_RE.match(candidate):
                return candidate[:120]

    for line in lines[:8]:
        cleaned = line.strip()
        if not cleaned or len(cleaned) < 2:
            continue
        if _GENERIC_HEADER_RE.match(cleaned) or _PURE_META_LINE_RE.match(cleaned):
            continue
        if re.search(r"วันที่|เวลา|ยอดสุทธิ|รวมทั้งสิ้น|จำนวนเงิน|vat|tax\s*id", cleaned, re.IGNORECASE):
            continue
        return cleaned[:120]
    return ""


def _infer_expense_category(text: str) -> str:
    lower = text.lower()
    for category, keywords in _EXPENSE_CATEGORY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return category
    return "other"


def infer_reimbursement_fields(
    text: str,
    exp_cat: str,
    doc_tags: Optional[List[str]] = None,
) -> tuple[str, str]:
    """Infer teacher reimbursement status and kindergarten budget source from OCR text & category."""
    lower = (text or "").lower()
    tags = set(doc_tags or [])

    if "ส่วนตัว" in lower or "ส่วนตัว" in tags:
        return "personal", "personal"

    if exp_cat == "classroom":
        return "pending_reimbursement", "classroom"
    if exp_cat == "kids_snack":
        return "pending_reimbursement", "kids_snack"
    if any(kw in lower for kw in ("ค่าอุปกรณ์การเรียน", "เงินอุดหนุน", "เรียนฟรี")):
        return "pending_reimbursement", "subsidy"

    school_signals = (
        "โรงเรียน",
        "อนุบาล",
        "เบิก",
        "สื่อการสอน",
        "อาหารกลางวัน",
        "นมโรงเรียน",
        "ขนมเด็ก",
        "ค่าอุปกรณ์",
        "เงินอุดหนุน",
        "นักเรียน",
        "ใบสำคัญรับเงิน",
        "ห้องเรียน",
    )
    if any(kw in lower for kw in school_signals) or "ครูอนุบาล" in tags or "เบิกโรงเรียน" in tags:
        return "pending_reimbursement", "school_project"

    if exp_cat in {"food", "transport", "utilities"}:
        return "personal", "personal"

    return "pending_reimbursement", "other"


def is_likely_student_document(text: str) -> bool:
    """Return True when OCR text represents a student profile, birth certificate, admission, or allergy form."""
    lower = (text or "").lower()
    if any(kw in lower for kw in _STRONG_STUDENT_KEYWORDS):
        return True
    hits = sum(1 for sig in _STUDENT_PROFILE_SIGNALS if sig.lower() in lower)
    if hits >= 2:
        return True
    if any(kw in lower for kw in _ALLERGY_KEYWORDS) and any(
        sig in lower for sig in ("เด็ก", "น้อง", "ด.ช.", "ด.ญ.", "อนุบาล")
    ):
        return True
    return False


def _clean_none_phrase(val: str) -> str:
    cleaned = (val or "").strip(" :：-\t\r\n")
    if not cleaned:
        return ""
    lower = cleaned.lower()
    if lower in {"ไม่มี", "ไม่แพ้", "ไม่เคยแพ้", "ปกติ", "-", "none", "n/a", "ไม่ระบุ"}:
        return ""
    if re.match(r"^(?:ไม่มี|ไม่แพ้|ปกติ)\b", lower):
        return ""
    return cleaned[:160]


def _format_thai_phone(raw_phone: str) -> str:
    digits = re.sub(r"\D", "", raw_phone or "")
    if len(digits) == 10 and digits.startswith("0"):
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    if len(digits) == 9 and digits.startswith("0"):
        return f"{digits[:2]}-{digits[2:5]}-{digits[5:]}"
    return raw_phone.strip()


def extract_student_profile_regex(text: str) -> Dict[str, Any]:
    """Extract structured kindergarten Student Profile fields offline from Thai OCR text."""
    raw = text or ""
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]

    # Citizen ID (13 digits, optionally hyphenated or spaced; tolerate common OCR O->0 / l->1 on ID lines)
    citizen_id = ""
    cid_pattern = r"(?<![\dA-Za-z])([0-9OoIl][\s-]?[0-9OoIl]{4}[\s-]?[0-9OoIl]{5}[\s-]?[0-9OoIl]{2}[\s-]?[0-9OoIl])(?![\dA-Za-z])"
    for ln in lines:
        if any(k in ln for k in ("ประจำตัว", "บัตร", "ประชาชน", "id")) or re.search(r"\d[\s-]?\d{4}", ln):
            cid_match = re.search(cid_pattern, ln)
            if cid_match:
                normalized_cid = (
                    cid_match.group(1)
                    .replace("O", "0")
                    .replace("o", "0")
                    .replace("I", "1")
                    .replace("l", "1")
                )
                digits = re.sub(r"\D", "", normalized_cid)
                if len(digits) == 13:
                    citizen_id = digits
                    break

    # Full name
    full_name = ""
    name_label_re = re.compile(
        r"(?:ชื่อ-นามสกุล|ชื่อ-สกุล|ชื่อเด็ก|ชื่อนักเรียน|ชื่อผู้เรียน|ชื่อ)\s*[:：]?\s*"
        r"((?:เด็กชาย|เด็กหญิง|ด\.ช\.|ด\.ญ\.)?\s*[ก-๙a-zA-Z]+(?:\s+[ก-๙a-zA-Z]+){0,3})"
    )
    prefix_name_re = re.compile(
        r"\b((?:เด็กชาย|เด็กหญิง|ด\.ช\.|ด\.ญ\.)\s*[ก-๙a-zA-Z]+(?:\s+[ก-๙a-zA-Z]+){0,2})"
    )
    for ln in lines:
        if any(skip in ln for skip in ("ชื่อผู้ปกครอง", "ชื่อบิดา", "ชื่อมารดา", "ชื่อเล่น", "ชื่อร้าน")):
            continue
        m = name_label_re.search(ln)
        if m:
            cand = re.split(r"\s+(?:ชื่อเล่น|เลขประจำตัว|เกิดวันที่|วันเกิด|ชั้น|ห้อง)\b", m.group(1))[0].strip()
            if len(cand) >= 3:
                full_name = cand[:120]
                break
        m2 = prefix_name_re.search(ln)
        if m2:
            cand = re.split(r"\s+(?:ชื่อเล่น|เลขประจำตัว|เกิดวันที่|วันเกิด|ชั้น|ห้อง)\b", m2.group(1))[0].strip()
            if len(cand) >= 4:
                full_name = cand[:120]
                break

    # Nickname
    nickname = ""
    nick_match = re.search(r"ชื่อเล่น\s*[:：]?\s*\(?([ก-๙a-zA-Z]{1,25})\)?", raw)
    if nick_match:
        nickname = nick_match.group(1).strip()
    else:
        colon_nick = re.search(r"[:：]\s*น้อง([ก-๙a-zA-Z]{1,25})", raw)
        if colon_nick:
            nickname = "น้อง" + colon_nick.group(1).strip()
        else:
            paren_nick = re.search(r"\((?:น้อง)?([ก-๙a-zA-Z]{2,20})\)", raw)
            if paren_nick and paren_nick.group(1) not in {"อ.1", "อ.2", "อ.3"}:
                nickname = paren_nick.group(1).strip()

    # Birth date (prioritize lines mentioning birth)
    birth_date = ""
    for ln in lines:
        if any(k in ln for k in ("เกิด", "birth", "dob")):
            birth_date = _extract_date(ln)
            if birth_date:
                break
    if not birth_date:
        birth_date = _extract_date(raw)

    # Classroom
    classroom = "อนุบาล"
    cls_match = re.search(
        r"(?:ระดับชั้น|ชั้นเรียน|ช้นเรียน|ชั้น|ห้องเรียน|ห้อง)\s*[:：]?\s*((?:อน[ุ]?บาล|อ\.?)\s*[1-3](?:/\d+)?)",
        raw,
    )
    if not cls_match:
        cls_match = re.search(r"(อน[ุ]?บาล\s*[1-3](?:/\d+)?|อ\.[1-3](?:/\d+)?)", raw)
    if cls_match:
        classroom = re.sub(r"\s+", " ", cls_match.group(1)).replace("อนบาล", "อนุบาล").strip()

    # Parent phone
    parent_phone = ""
    phone_match = re.search(r"(?<!\d)(0[689]\d[\s-]?\d{3}[\s-]?\d{4})(?!\d)", raw)
    if not phone_match:
        phone_match = re.search(r"(?:โทร|เบอร์|tel|mobile)[.:\s]*(0\d{1,2}[\s-]?\d{3,4}[\s-]?\d{4})", raw, re.IGNORECASE)
    if phone_match:
        parent_phone = _format_thai_phone(phone_match.group(1))

    # Parent name
    parent_name = ""
    parent_re = re.compile(
        r"(?:ชื่อผ\u0e39?\u0e49?ปกครอง|ผ\u0e39?\u0e49?ปกครอง|บิดา/มารดา|มารดา|บิดา)\s*[:：]?\s*([^\n]+)"
    )
    for ln in lines:
        pm = parent_re.search(ln)
        if pm:
            cand = pm.group(1)
            cand = re.split(r"\s*(?:เบอร์|โทร|tel|mobile|0[689]\d)", cand, maxsplit=1, flags=re.IGNORECASE)[0]
            cand = cand.strip(" :：()-")
            if cand and not _PURE_META_LINE_RE.match(cand):
                parent_name = cand[:120]
                break

    # Allergies
    allergies = ""
    allergy_re = re.compile(
        r"(?:ประวัติการแพ้อาหาร|ข้อมูลการแพ้อาหาร|แพ้อาหาร|อาหารที่แพ้|การแพ้)\s*[:：]?\s*([^\n]+)"
    )
    for ln in lines:
        am = allergy_re.search(ln)
        if am:
            cleaned = _clean_none_phrase(am.group(1))
            if cleaned:
                allergies = cleaned
                break
    if not allergies:
        found_allergies = [kw for kw in _ALLERGY_KEYWORDS if kw in raw.lower()]
        if found_allergies:
            allergies = ", ".join(dict.fromkeys(found_allergies))

    # Medical notes
    medical_notes = ""
    med_re = re.compile(
        r"(?:โรคประจำตัว|ข้อควรระวัง|ข้อมูลสุขภาพ|ประวัติสุขภาพ|หมายเหตุสุขภาพ)\s*[:：]?\s*([^\n]+)"
    )
    for ln in lines:
        mm = med_re.search(ln)
        if mm:
            cleaned = _clean_none_phrase(mm.group(1))
            if cleaned:
                medical_notes = cleaned
                break
    if not medical_notes:
        found_meds = [kw for kw in _MEDICAL_KEYWORDS if kw.lower() in raw.lower()]
        if found_meds:
            medical_notes = ", ".join(dict.fromkeys(found_meds))

    return {
        "full_name": full_name,
        "nickname": nickname,
        "citizen_id": citizen_id,
        "birth_date": birth_date,
        "classroom": classroom,
        "parent_name": parent_name,
        "parent_phone": parent_phone,
        "allergies": allergies,
        "medical_notes": medical_notes,
        "extraction_method": "regex",
    }


def _infer_doc_category(text: str, likely_exp: bool, likely_student: bool = False) -> str:
    if likely_exp:
        return "receipt"
    if likely_student:
        return "student"
    lower = (text or "").lower()
    if any(
        kw in lower
        for kw in (
            "แผนการจัดประสบการณ์",
            "แผนการสอน",
            "ใบงานอนุบาล",
            "กิจกรรมสร้างสรรค์",
            "เกมการศึกษา",
            "สื่อการสอน",
        )
    ):
        return "teaching"
    if any(
        kw in lower
        for kw in (
            "แจ้งผู้ปกครอง",
            "ผู้ปกครอง",
            "อนุบาล",
            "ทัศนศึกษา",
            "ประเมินพัฒนาการ",
            "สมุดพก",
            "โรงเรียน",
        )
    ):
        return "school"
    if any(kw in lower for kw in ("บันทึกข้อความ", "ส่วนราชการ", "ที่ ศธ")):
        return "official"
    return "general"


def _infer_auto_tags(
    text: str,
    *,
    likely_exp: bool = False,
    likely_student: bool = False,
    student_data: Optional[Dict[str, Any]] = None,
    exp_cat: str = "other",
    doc_cat: str = "general",
) -> List[str]:
    lower = (text or "").lower()
    tags: List[str] = []

    def add(tag: str) -> None:
        if tag not in tags:
            tags.append(tag)

    if (
        doc_cat in {"school", "teaching", "student"}
        or exp_cat in {"classroom", "kids_snack"}
        or likely_student
        or any(
            kw in lower
            for kw in (
                "อนุบาล",
                "สื่อการสอน",
                "แผนการจัดประสบการณ์",
                "ผู้ปกครอง",
                "พัฒนาการเด็ก",
                "นมโรงเรียน",
                "ขนมเด็ก",
            )
        )
    ):
        add("ครูอนุบาล")

    if likely_student or doc_cat == "student":
        add("ทะเบียนเด็ก")
        if student_data and (student_data.get("allergies") or student_data.get("medical_notes")):
            add("แพ้อาหาร")

    if exp_cat == "classroom" or any(
        kw in lower for kw in ("สื่อการสอน", "สีเทียน", "ดินน้ำมัน", "งานประดิษฐ์")
    ):
        add("สื่อการสอน")

    if exp_cat == "kids_snack" or any(kw in lower for kw in ("นมโรงเรียน", "ขนมเด็ก")):
        add("ขนมเด็ก")

    if any(kw in lower for kw in ("แจ้งผู้ปกครอง", "ผู้ปกครอง")) and not likely_student:
        add("แจ้งผู้ปกครอง")

    if likely_exp and any(
        kw in lower for kw in ("เบิกงบ", "เบิกค่า", "ใบสำคัญรับเงิน", "โรงเรียน")
    ):
        add("เบิกโรงเรียน")

    if "ใบเสร็จ" in lower:
        add("ใบเสร็จ")

    if "โอนเงิน" in lower or "สลิป" in lower:
        add("สลิปโอนเงิน")

    return tags[:5]


def is_likely_expense(text: str) -> bool:
    """Heuristic check for whether OCR text looks like a receipt, invoice, or transfer slip."""
    lower = (text or "").lower()
    signals = (
        "ใบเสร็จ",
        "ใบกำกับภาษี",
        "ยอดสุทธิ",
        "รวมทั้งสิ้น",
        "จำนวนเงิน",
        "โอนเงิน",
        "vat",
        "บาท",
        "thb",
        "เลขประจำตัวผู้เสียภาษี",
        "tax invoice",
    )
    hits = sum(1 for sig in signals if sig in lower)
    return hits >= 2


def extract_expense_regex(text: str) -> Dict[str, Any]:
    """Extract structured Expense Record fields purely offline using Thai/English regexes."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    amt_token = r"(?:thb|฿)?\s*([\d,]+\.\d{2}|[\d,]+(?!\s*%)(?!\s*(?:items?|ชิ้น|รายการ|pcs|ea)\b))\s*(?:บาท|thb|฿)?"

    total_patterns = [
        re.compile(rf"(?:ยอดสุทธิ|รวมทั้งสิ้น|ยอดรวมสุทธิ|รวมเงินทั้งสิ้น|grand\s*total|net\s*total)[:\s]*{amt_token}", re.IGNORECASE),
        re.compile(rf"(?:จำนวนเงิน|ยอดชำระ|ชำระเงิน|total\s*amount)[:\s]*{amt_token}", re.IGNORECASE),
        re.compile(rf"(?<!sub\s)(?<!sub-)\b(?:total)\b[:\s]*{amt_token}", re.IGNORECASE),
    ]
    vat_patterns = [
        re.compile(rf"(?<!before\s)(?<!pre-)(?:ภาษีมูลค่าเพิ่ม|\bvat\b)\s*(?:\d+(?:\.\d+)?\s*%)?[:\s]+{amt_token}", re.IGNORECASE),
    ]
    subtotal_patterns = [
        re.compile(rf"(?:รวมเป็นเงิน|ยอดก่อนภาษี|มูลค่าสินค้า|before\s*vat|sub\s*total|subtotal)[:\s]*{amt_token}", re.IGNORECASE),
    ]

    total_amount = _extract_amount_for_patterns(lines, total_patterns)
    vat_amount = _extract_amount_for_patterns(lines, vat_patterns)
    subtotal = _extract_amount_for_patterns(lines, subtotal_patterns)

    if total_amount == 0.0:
        fallback_baht = re.compile(r"([\d,]+\.\d{2})\s*(?:บาท|thb|฿)", re.IGNORECASE)
        candidates = [_parse_amount(m.group(1)) for ln in lines for m in fallback_baht.finditer(ln)]
        if candidates:
            total_amount = max(candidates)

    if subtotal == 0.0 and total_amount > 0.0:
        subtotal = round(max(0.0, total_amount - vat_amount), 2)
    elif total_amount == 0.0 and subtotal > 0.0:
        total_amount = round(subtotal + vat_amount, 2)

    exp_cat = _infer_expense_category(text or "")
    reimb_status, budget_source = infer_reimbursement_fields(text or "", exp_cat)

    return {
        "merchant": _infer_merchant(lines),
        "expense_date": _extract_date(text or ""),
        "subtotal": round(subtotal, 2),
        "vat_amount": round(vat_amount, 2),
        "total_amount": round(total_amount, 2),
        "currency": "THB",
        "expense_category": exp_cat,
        "reimbursement_status": reimb_status,
        "budget_source": budget_source,
        "extraction_method": "regex",
    }


def _strip_json_fences(raw_text: str) -> str:
    cleaned = (raw_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _to_float(val: Any, default: float = 0.0) -> float:
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return default


_LLM_SYSTEM_PROMPT = (
    "You extract structured metadata, teacher reimbursement info, and kindergarten student profiles strictly from Thai/English OCR text. "
    "Return ONLY valid JSON with keys: "
    "is_expense (boolean), is_student (boolean), doc_category (one of: general, receipt, tax, official, contract, manual, school, teaching, student), "
    "tags (array of 1-5 short Thai/English tags), merchant (string), expense_date (YYYY-MM-DD or ''), "
    "subtotal (number), vat_amount (number), total_amount (number), currency (string like THB), "
    "expense_category (one of: food, transport, utilities, office, classroom, kids_snack, other), "
    "reimbursement_status (one of: pending_reimbursement, reimbursed, personal), "
    "budget_source (one of: classroom, kids_snack, subsidy, school_project, personal, other), "
    "student (object or null with keys: full_name, nickname, citizen_id, birth_date YYYY-MM-DD, classroom, parent_name, parent_phone, allergies, medical_notes). "
    "When the document relates to kindergarten teaching, classroom supplies, children's snacks/milk, student enrollment/allergy forms, or school/parent notices, "
    "add relevant Thai tags such as 'ครูอนุบาล', 'สื่อการสอน', 'เบิกโรงเรียน', 'แจ้งผู้ปกครอง', 'ขนมเด็ก', 'ทะเบียนเด็ก', or 'แพ้อาหาร'. "
    "Convert Thai Buddhist Era years (>= 2400) to Gregorian YYYY-MM-DD."
)


def extract_with_llm(text: str, config: AppConfig) -> Dict[str, Any]:
    """Call optional text-only LLM API (Gemini or OpenAI-compatible/Ollama) using OCR text only."""
    if not is_llm_configured(config):
        raise ValueError("LLM endpoint is not configured")

    prompt_text = f"{_LLM_SYSTEM_PROMPT}\n\nOCR_TEXT:\n{text[:8000]}"
    provider = (config.llm_provider or "gemini").lower()

    if provider == "gemini":
        base = (config.llm_base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        model = config.llm_model or "gemini-2.5-flash"
        url = f"{base}/models/{model}:generateContent"
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json", "x-goog-api-key": config.llm_api_key},
            json={
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                },
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        raw_content = payload["candidates"][0]["content"]["parts"][0]["text"]
    else:
        base = (config.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if config.llm_api_key:
            headers["Authorization"] = f"Bearer {config.llm_api_key}"
        resp = requests.post(
            url,
            headers=headers,
            json={
                "model": config.llm_model or "gpt-4o-mini",
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                    {"role": "user", "content": f"OCR_TEXT:\n{text[:8000]}"},
                ],
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        raw_content = payload["choices"][0]["message"]["content"]

    parsed = json.loads(_strip_json_fences(raw_content))
    if not isinstance(parsed, dict):
        raise ValueError("LLM output was not a JSON object")

    doc_cat = str(parsed.get("doc_category") or "general").strip().lower()
    exp_cat = str(parsed.get("expense_category") or "other").strip().lower()
    resolved_exp_cat = exp_cat if exp_cat in VALID_EXPENSE_CATEGORIES else "other"
    raw_tags = parsed.get("tags") or []
    tags = [str(t).strip() for t in raw_tags if str(t).strip()][:5] if isinstance(raw_tags, list) else []

    raw_reimb = str(parsed.get("reimbursement_status") or "").strip().lower()
    raw_budget = str(parsed.get("budget_source") or "").strip().lower()
    inferred_reimb, inferred_budget = infer_reimbursement_fields(text, resolved_exp_cat, tags)
    reimb_status = raw_reimb if raw_reimb in VALID_REIMBURSEMENT_STATUSES else inferred_reimb
    budget_source = raw_budget if raw_budget in VALID_BUDGET_SOURCES else inferred_budget

    is_student = bool(parsed.get("is_student", False))
    student_payload = parsed.get("student")
    clean_student = None
    if is_student and isinstance(student_payload, dict):
        clean_student = {
            "full_name": str(student_payload.get("full_name") or "").strip()[:120],
            "nickname": str(student_payload.get("nickname") or "").strip()[:40],
            "citizen_id": re.sub(r"\D", "", str(student_payload.get("citizen_id") or ""))[:13],
            "birth_date": str(student_payload.get("birth_date") or "").strip()[:10],
            "classroom": str(student_payload.get("classroom") or "อนุบาล").strip()[:40] or "อนุบาล",
            "parent_name": str(student_payload.get("parent_name") or "").strip()[:120],
            "parent_phone": str(student_payload.get("parent_phone") or "").strip()[:30],
            "allergies": _clean_none_phrase(str(student_payload.get("allergies") or "")),
            "medical_notes": _clean_none_phrase(str(student_payload.get("medical_notes") or "")),
            "extraction_method": "llm",
        }
    elif is_student:
        clean_student = extract_student_profile_regex(text)

    return {
        "is_expense": bool(parsed.get("is_expense", False)),
        "is_student": bool(is_student and clean_student),
        "doc_category": doc_cat if doc_cat in VALID_DOC_CATEGORIES else "general",
        "tags": tags,
        "expense": {
            "merchant": str(parsed.get("merchant") or "").strip()[:120],
            "expense_date": str(parsed.get("expense_date") or "").strip()[:10],
            "subtotal": _to_float(parsed.get("subtotal"), 0.0),
            "vat_amount": _to_float(parsed.get("vat_amount"), 0.0),
            "total_amount": _to_float(parsed.get("total_amount"), 0.0),
            "currency": str(parsed.get("currency") or "THB").strip() or "THB",
            "expense_category": resolved_exp_cat,
            "reimbursement_status": reimb_status,
            "budget_source": budget_source,
            "extraction_method": "llm",
        },
        "student": clean_student,
    }


def extract_document_intelligence(
    text: str,
    config: AppConfig,
    *,
    extraction_method: str = "auto",
    require_expense_hint: bool = False,
) -> Dict[str, Any]:
    """Run hybrid extraction (text-only LLM when configured, offline Thai Regex fallback)."""
    clean_method = (extraction_method or "auto").strip().lower()
    if clean_method in ("auto", "llm") and is_llm_configured(config):
        try:
            return extract_with_llm(text, config)
        except Exception as exc:
            print(f"[Extractor] LLM extraction failed, falling back to offline regex: {exc}")

    likely_exp = is_likely_expense(text)
    likely_student = (not likely_exp) and is_likely_student_document(text)
    expense_data = extract_expense_regex(text) if (likely_exp or not require_expense_hint) else None
    student_data = extract_student_profile_regex(text) if likely_student else None
    exp_cat = expense_data["expense_category"] if expense_data else _infer_expense_category(text or "")
    doc_cat = _infer_doc_category(text or "", likely_exp, likely_student=likely_student)
    auto_tags = _infer_auto_tags(
        text or "",
        likely_exp=likely_exp,
        likely_student=likely_student,
        student_data=student_data,
        exp_cat=exp_cat,
        doc_cat=doc_cat,
    )
    return {
        "is_expense": likely_exp if require_expense_hint else True,
        "is_student": likely_student,
        "doc_category": doc_cat,
        "tags": auto_tags,
        "expense": expense_data,
        "student": student_data,
    }
