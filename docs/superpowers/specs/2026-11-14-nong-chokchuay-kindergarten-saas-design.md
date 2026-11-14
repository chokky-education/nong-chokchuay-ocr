# Design Spec: "น้องโชคช่วย" (Nong ChokChuay) — Kindergarten Teacher Assistant SaaS

**Date:** 2026-11-14
**Status:** Approved

## 1. Intent & Why

Kindergarten teachers in Thailand carry heavy non-teaching paperwork outside classroom hours (20:00–23:00): collecting out-of-pocket classroom receipts for school reimbursement, re-typing student birth certificates / parent contacts / allergy notes into spreadsheets, and hunting for expired PDFs/images inside LINE group chats.

**"น้องโชคช่วย" (Nong ChokChuay)** transforms OpenThai 2.0 OCR Studio into a calm, friendly, zero-jargon assistant modeled after proven Thai teacher workflows (LINE OA front-door capture + Web/LIFF workspace for fast verification and Excel/CSV export), deployable within a **$5/month Cloudflare Workers Paid + Containers** budget.

## 2. Brand CI ("น้องโชคช่วย")

- **Name:** น้องโชคช่วย (*Nong ChokChuay — ผู้ช่วยคุณครูอนุบาล*)
- **Slogan:** *"งานเอกสารกองโตแค่ไหน... ให้น้องโชคช่วยนะครับคุณครู"*
- **Mascot:** Illustrated warm-hearted young helper wearing round glasses and a blue-check shirt with laptop (`/static/mascot.jpg`).
- **Color Palette (Sunny Sky & Golden Hour):**
  - Background: Warm Cloud Ivory `#FCFBF7`
  - Surface / Cards: `#FFFFFF` with soft warm border `#E2E8F0` / `#FDE68A`
  - Primary Brand Sky Blue: `#2563EB` (hover `#1D4ED8`, soft tint `#EFF6FF`)
  - Golden Sunlight Accent: `#F59E0B` (soft tint `#FEF3C7`)
  - Pending Reimbursement Alert: `#EA580C` (soft tint `#FFEDD5`)
  - Rosy Cheek Coral (Food Allergy / Medical Alert): `#E11D48` (soft tint `#FFE4E6`)
  - Verified / Reimbursed Green: `#10B981` (soft tint `#D1FAE5`)
- **Typography:** `Mali` (headings, pills, friendly speech bubbles) + `Prompt` (body text, Thai numbers, 13-digit IDs, currency).
- **Voice & Tone:** Warm, respectful, encouraging (`"น้องโชค...ครับคุณครู"`), free of developer jargon (`SQLite FTS5`, `Regex`, `Bounding Boxes` hidden under an optional "มุมมองเทคนิค" toggle).

## 3. Goals, Non-Goals & Anti-Goals

- **Goals:**
  1. LINE OA Webhook (`/api/line/webhook`) + interactive LINE OA Chat Simulator (`/api/line/simulate`) accepting receipt photos, student forms, PDFs, text commands (`ยอดรอเบิก`, `แพ้อาหาร`, search), and postback actions (`confirm_review`, `mark_reimbursed`).
  2. One-click Teacher Review Queue (`pending_review` -> `reviewed`) with side-by-side image and extracted fields.
  3. Teacher Expense & Reimbursement Ledger distinguishing `pending_reimbursement`, `reimbursed`, and `personal` across kindergarten budget sources (`classroom`, `kids_snack`, `subsidy`, `school_project`, `personal`, `other`) with UTF-8 BOM CSV export.
  4. Kindergarten Student Profile extraction (`extract_student_profile_regex` + updated LLM prompt) and CRUD roster + UTF-8 BOM CSV export highlighting food allergies and parent emergency contacts.
  5. Ready-to-deploy `wrangler.toml` for Cloudflare Containers (`sleep_after = "3m"`) within the $5/mo plan.
- **Non-Goals:** Kindergarten student login portal (children aged 3–5 do not submit homework online).
- **Anti-Goals:** Cluttering the primary teacher screen with engineering terminology or dropping compatibility with existing API/test contracts.

## 4. Approaches Considered & Selected

- **Approach 1 (Selected): Unified FastAPI + OpenThai 2.0 ONNX + SQLite FTS5 + LINE Webhook + Teacher LIFF/Web UI on Cloudflare Containers.**
  - Keeps the entire Thai OCR + FTS5 + extractor pipeline in one maintainable Python service, protects student document privacy (raw images never sent to external LLMs), and scales to zero on Cloudflare Containers.
- **Approach 2 (Rejected): Split TypeScript Cloudflare Worker + Python OCR microservice.** Unnecessary two-language maintenance overhead.
- **Approach 3 (Rejected): Standard Cloudflare Workers + 100% Cloud Vision AI.** Violates the 128 MB isolate cap for local ONNX and discards the offline Thai OCR engine.

## 5. Architecture & Data Model Summary

- **`documents`:** Adds `review_status` (`pending_review` / `reviewed`) and `line_user_id`.
- **`expenses`:** Adds `reimbursement_status` (`pending_reimbursement` / `reimbursed` / `personal`) and `budget_source`.
- **`students`:** Stores `id`, `document_id`, `full_name`, `nickname`, `citizen_id`, `birth_date`, `classroom`, `parent_name`, `parent_phone`, `allergies`, `medical_notes`, `extraction_method`, `raw_json`, timestamps.
- **Endpoints:** `/api/documents/{doc_id}/review`, `/api/students`, `/api/students/{student_id}`, `/api/students/export.csv`, `/api/line/webhook`, `/api/line/simulate`, `/api/samples/kindergarten/{preset}`.
