# 04: Offline Thai Regex Expense Extractor and Dashboard

**What to build:** Extract structured Expense Records (`merchant`, `expense_date`, `subtotal`, `vat_amount`, `total_amount`, `currency`, `expense_category`) completely offline from Thai receipts and slips using regex heuristics, expose CRUD and monthly summary APIs, render an editable Expense Dashboard in the UI, and export UTF-8 BOM CSV reports.

**Blocked by:** 02: Vault Source Files, Categories, Tags, Edit Re-index, and Box Alignment

**Status:** done

- [x] Offline regex parser converts Buddhist Era dates (`พ.ศ.`) to Gregorian ISO dates and extracts total, VAT, subtotal, and merchant
- [x] Users can extract, view, edit, and delete Expense Records linked to Vault Documents
- [x] Monthly Expense Dashboard displays totals and exports Excel-compatible UTF-8 BOM CSV
