import re
from typing import Any, Dict, List, Optional, Tuple

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

_EXPENSE_CATEGORY_KEYWORDS = {
    "food": ("กาแฟ", "cafe", "amazon", "starbucks", "ข้าว", "อาหาร", "ก๋วยเตี๋ยว", "7-eleven", "เซเว่น", "restaurant", "coffee"),
    "transport": ("grab", "bolt", "ptt", "บางจาก", "ปั๊ม", "น้ำมัน", "ทางด่วน", "bts", "mrt", "แท็กซี่", "สายการบิน"),
    "utilities": ("การไฟฟ้า", "การประปา", "ค่าไฟ", "ค่าน้ำ", "ais", "true", "dtac", "3bb", "อินเทอร์เน็ต"),
    "office": ("officemate", "b2s", "เครื่องเขียน", "กระดาษ", "หมึก", "cloud", "software", "hosting"),
}


def _normalize_year(raw_year: int) -> int:
    if raw_year < 100:
        # Assume Thai 2-digit BE year when >= 40 (e.g. 69 -> 2569 -> 2026)
        raw_year = (2500 + raw_year) if raw_year >= 40 else (2000 + raw_year)
    if raw_year >= 2400:
        return raw_year - 543
    return raw_year


def _extract_date(text: str) -> str:
    # 1. Thai day + month name + year (e.g., 12 พ.ย. 2569 or 12 พ.ย. 69)
    month_keys = "|".join(sorted((re.escape(k) for k in THAI_MONTHS), key=len, reverse=True))
    thai_date_re = re.compile(rf"(\d{{1,2}})\s*({month_keys})\s*(\d{{2,4}})")
    m = thai_date_re.search(text)
    if m:
        day = int(m.group(1))
        month = THAI_MONTHS.get(m.group(2), 0)
        year = _normalize_year(int(m.group(3)))
        if 1 <= month <= 12 and 1 <= day <= 31 and 1990 <= year <= 2100:
            return f"{year:04d}-{month:02d}-{day:02d}"

    # 2. ISO YYYY-MM-DD (handles BE year like 2569-11-12 too)
    iso_re = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
    m = iso_re.search(text)
    if m:
        year = _normalize_year(int(m.group(1)))
        month, day = int(m.group(2)), int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31 and 1990 <= year <= 2100:
            return f"{year:04d}-{month:02d}-{day:02d}"

    # 3. DD/MM/YYYY or DD-MM-YYYY
    dmy_re = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
    m = dmy_re.search(text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = _normalize_year(int(m.group(3)))
        if 1 <= month <= 12 and 1 <= day <= 31 and 1990 <= year <= 2100:
            return f"{year:04d}-{month:02d}-{day:02d}"

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
    # PromptPay / Bank transfer recipient patterns
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
    amt_token = r"(?:thb|฿)?\s*([\d,]+\.\d{2}|[\d,]+)\s*(?:บาท|thb|฿)?"

    total_patterns = [
        re.compile(rf"(?:ยอดสุทธิ|รวมทั้งสิ้น|ยอดรวมสุทธิ|รวมเงินทั้งสิ้น|grand\s*total|net\s*total)[:\s]*{amt_token}", re.IGNORECASE),
        re.compile(rf"(?:จำนวนเงิน|ยอดชำระ|ชำระเงิน|total\s*amount)[:\s]*{amt_token}", re.IGNORECASE),
        re.compile(rf"\b(?:total)\b[:\s]*{amt_token}", re.IGNORECASE),
    ]
    vat_patterns = [
        re.compile(rf"(?:ภาษีมูลค่าเพิ่ม|vat)\s*(?:\d+(?:\.\d+)?\s*%)?[:\s]+{amt_token}", re.IGNORECASE),
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

    return {
        "merchant": _infer_merchant(lines),
        "expense_date": _extract_date(text or ""),
        "subtotal": round(subtotal, 2),
        "vat_amount": round(vat_amount, 2),
        "total_amount": round(total_amount, 2),
        "currency": "THB",
        "expense_category": _infer_expense_category(text or ""),
        "extraction_method": "regex",
    }
