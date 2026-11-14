# น้องโชคช่วย (Nong ChokChuay): Kindergarten Teacher SaaS & LINE OA

## Problem Statement

Kindergarten teachers in Thailand need a warm, zero-jargon assistant that captures bills, student admission/allergy documents, and school circulars via LINE OA (or web dropzone), extracts Thai fields locally with OpenThai 2.0 OCR, separates teacher out-of-pocket expenses awaiting school reimbursement, tracks student profiles and food allergies safely, and exports clean Excel-ready CSVs — deployable on Cloudflare Workers Paid ($5/month) via Cloudflare Containers.

## Solution

1. **Database & Domain Extensions (`database.py`)**: Add `review_status` and `line_user_id` to `documents`, `reimbursement_status` and `budget_source` to `expenses`, and a new `students` table with CRUD + UTF-8 BOM CSV export.
2. **Student Profile & Reimbursement Intelligence (`extractor.py`, `ocr_service.py`)**: Extract Thai student names, nicknames, 13-digit citizen IDs, B.E. birth dates, classrooms, parent phone numbers, and food allergies offline and via hybrid text-only LLM, plus infer teacher reimbursement status and budget source.
3. **LINE OA Webhook & Simulator (`line_service.py`, `config.py`, `app.py`)**: Handle LINE Messaging API webhook events (`X-Line-Signature`, Reply Flex Cards, Postback actions, chat search/summary commands) and provide an interactive Web Simulator + 1-click kindergarten sample presets.
4. **Nong ChokChuay UI & Cloudflare Config (`static/`, `wrangler.toml`)**: Redesign the frontend with the Sunny Sky & Golden Hour CI featuring `/static/mascot.jpg`, Review Queue, Reimbursement Tracker, Student Roster modal, and `wrangler.toml` for scale-to-zero Cloudflare Containers.
