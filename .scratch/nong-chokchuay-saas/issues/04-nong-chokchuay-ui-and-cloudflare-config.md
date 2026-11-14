# 04: Nong ChokChuay UI Redesign and Cloudflare Container Config

**What to build:** Redesign `static/index.html`, `static/styles.css`, and `static/app.js` with the Sunny Sky & Golden Hour Brand CI featuring `/static/mascot.jpg`, Review Queue controls, Student Roster & Allergy Alert modal, upgraded Reimbursement Ledger modal, and interactive LINE OA Chat Simulator modal; add `wrangler.toml` for Cloudflare Containers ($5/mo) and update `README.md`.

**Blocked by:** 03: LINE OA Webhook, Flex Replies, and Web Simulator

**Status:** done

- [x] UI renders mascot `/static/mascot.jpg`, Mali + Prompt fonts, zero-jargon teacher dashboard, and preserves existing DOM IDs
- [x] Student Roster modal, upgraded Reimbursement modal, and LINE OA Simulator modal function end-to-end
- [x] `wrangler.toml` configures Cloudflare Containers scale-to-zero (`sleep_after = "3m"`) and `README.md` documents setup
