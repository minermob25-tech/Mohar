# Mohar — Intelligent Brand-Governance Review Desk

> **Show the rule. Explain the mismatch. Support the fix. Leave the decision with a person.**

Mohar is a human-controlled pre-publication review desk built for **CodeNova (Round 3)** by **Team The Spider's Little Birds**. Before any content goes public, Mohar checks text, documents, and visual assets against an organisation's approved guidelines, verified facts, terminology, and visual identity—returning cited findings, plain-language explanations, and intent-preserving edits while keeping final approval strictly in human hands.

- **Live Deployment:** https://mohar-694m.onrender.com/ *(Free-tier Render instances may take ~30 seconds to wake on a cold first request)*
- **Source Code:** https://github.com/minermob25-tech/Mohar
- **Demo Workspace:** `Northwind Outdoor Co.` (pre-loaded with rules, FY2025/2026 facts, palette swatches, and logo assets—no sign-up required)

---

## Three Core Rules

1. **No source, no verdict:** Every finding cites the exact rule or approved fact ID behind it (`G-4.2`, `F-017`, `PR-SUM-26`, `V-1`, `V-2`, etc.). Unsupported claims or unverified sources are downgraded to `Needs review` rather than asserted as fact.
2. **Suggest, never overwrite:** Fixes are editable suggestions. Creators accept, edit, or dismiss each finding with a mandatory written reason, preserving every historical version.
3. **Learn from decisions:** Approvals, dismissals, and repeat rule violations feed the compliance score and **Guideline Health** dashboard so managers can identify unclear or frequently broken rules.

---

## Round 3 Capabilities

### 1. Shared Multi-User Backend & Server-Enforced JWT RBAC
- **Unified Relational Persistence (`backend.py`):** Supports PostgreSQL automatically via `DATABASE_URL` (`pg8000`) with zero-configuration SQLite (`mohar_r3.db`) fallback for local evaluation.
- **Role-Based Access Control (JWT):** Switching roles (`Creator`, `Reviewer`, `Manager`) issues scoped JWT sessions (`/api/auth/login`) enforcing workspace isolation (`403` blocked across organisations) and blocking Creators from approving drafts or altering rules on the server.
- **Tamper-Resistant Audit Trail:** Every session, rule update, submission, blocked action, and approval decision is recorded in a SHA-256 hash-chained append-only audit log.

### 2. AI-Assisted Semantic Layer Behind a Deterministic Citation Validator
- **Provider-Neutral Adapter:** Connects to Google Gemini (`GEMINI_API_KEY`) or OpenAI (`OPENAI_API_KEY`) with a deterministic local semantic fallback so offline evaluation always works.
- **Strict Citation Validator & Tie-Breaking:** Every AI finding must quote an exact verbatim substring from the draft and cite a valid workspace rule ID; unquoted findings are discarded, unverified rule IDs are downgraded to `Needs review`, and **deterministic rules always win ties** on overlapping spans.
- **Visible Separation:** AI findings are badged **`AI-assisted`** and kept distinct from deterministic `Rule engine` cards and human approval decisions.

### 3. Visual Identity Checks (OCR, WCAG 2.2 Contrast, Palette & Logo Asset Matching)
- **Image & Scanned-PDF Intake:** Accepts `.png`, `.jpg`, `.jpeg`, `.webp`, and scanned `.pdf` files alongside `.docx`, `.txt`, and `.md`.
- **Automated Visual Checks:** Extracts text via OCR (`pytesseract` + pure-Python raster inspector), calculates WCAG 2.2 SC 1.4.3 contrast (`4.5:1` minimum threshold) on detected text regions, checks colours against the approved Northwind palette (`#1F3A2E`, `#D9822B`, `#F4EFE6`, `#1B1B1B`, `#FFFFFF`), and compares badge regions against canonical workspace logo assets (`assets/northwind_logo_2026.png` vs. retired `assets/northwind_logo_2022_retired.png`).

---

## Benchmark Results (`benchmark.py`)

Evaluated via `/api/benchmark` across **25 labelled synthetic drafts** (`18` seeded violations across Claims, Facts, and Tone + `7` clean controls) and **5 visual identity test images** (`3` defect images + `2` clean controls):

| Suite | Metric | Result |
| :--- | :--- | :--- |
| **Text Suite (`n = 25` drafts)** | Rule-Only Recall → **Rule + Validated AI Recall** | **50.0% → 100.0%** |
| **Text Suite (`n = 25` drafts)** | Precision / Valid-Source Rate / Actionable Edit Rate | **100.0% / 100.0% / 100.0%** |
| **Per Issue Type Recall** | Claims (`n=12`) · Facts (`n=4`) · Tone (`n=2`) | **58.3%→100%** · **50.0%→100%** · **0.0%→100%** |
| **Visual Suite (`n = 5` images)** | Defect Detection (`3` defects) / False Positives (`2` clean) | **100.0% detection / 0 false positives** |

---

## Repository Structure

```text
├── index.html          # Round 2 review desk interface
├── mohar-r3.js         # Client bridge connecting UI to JWT RBAC, AI validator & visual inspector
├── backend.py          # Starlette/Uvicorn API server, PostgreSQL/SQLite adapter, OCR & asset engine
├── benchmark.py        # 25-draft text evaluation & 5-image visual identity benchmark suite
├── assets/             # Canonical 2026 workspace logo & retired 2022 logo reference assets
├── requirements.txt    # Python dependencies
└── README.md           # Project documentation
```

---

## Run Locally

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. (Optional) Configure live LLM or PostgreSQL in .env
# GEMINI_API_KEY=your_google_ai_studio_key
# DATABASE_URL=postgresql://user:pass@host:5432/dbname

# 3. Start the server
uvicorn backend:app --host 0.0.0.0 --port 8000
```

Open **`http://127.0.0.1:8000/`** in your browser. Click **Test RBAC isolation** to verify `403` cross-workspace protection or **Run 25-draft & visual benchmark** to reproduce the metrics live.

---

## Team — The Spider's Little Birds (CodeNova)

| Member | Role |
| :--- | :--- |
| **Jatin Sharma** | Team Leader |
| **Divya Varshney** | Frontend and UX |
| **Palak Singh** | Backend |
| **Khushboo Varshney** | AI / ML |
| **Aditya Tomar** | Data, QA and Documentation |
