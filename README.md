# Mohar — Pre-Publication Brand Governance Review Desk (Round 3)

**Show the rule. Explain the mismatch. Support the fix. Leave the decision with a person.**

Mohar is a human-controlled brand governance review desk built by **The Spider's Little Birds** for CodeNova[cite: 1, 8]. A draft goes in before publication, is checked against the organisation's approved guidelines, facts, terminology, and visual identity rules, returns cited findings with plain-language explanations, and requires a verified human decision before approval[cite: 1, 3].

---

## Round 3 Improvements Delivered

All three improvements committed in our Round 2 submission are implemented and verifiable[cite: 7]:

1. **Shared Backend & Server-Enforced Roles (`backend.py`)**[cite: 7]
   - FastAPI + SQLite/PostgreSQL persistence for workspaces, approved rules, submitted versions, decisions, and a hash-chained append-only audit trail[cite: 7].
   - JWT session authentication with server-side RBAC enforcement: `Creator` sessions can draft and submit versions, while `Reviewer` / `Manager` sessions are required to approve submissions or modify workspace rules (`HTTP 403` enforced on unauthorized requests)[cite: 7].
   - Cross-workspace isolation prevents tokens scoped to one workspace from reading another[cite: 7].

2. **AI-Assisted Semantic Review behind a Citation Validator (`backend.py`, `mohar-r3.js`)**[cite: 7]
   - Provider-neutral LLM adapter (supports `GEMINI_API_KEY` or `OPENAI_API_KEY`, with a deterministic offline semantic fallback so the app works with zero external setup)[cite: 7].
   - **Deterministic Citation Validator:** Every AI-suggested finding is checked against the exact draft text and workspace rule IDs[cite: 7]. Unquoted spans are discarded; unverified rule IDs are downgraded to `Needs review`[cite: 7].

3. **Visual Identity Checks: OCR, WCAG Contrast, Palette & Logo (`backend.py`, `mohar-r3.js`)**[cite: 7]
   - Supports `.png`, `.jpg`, `.jpeg`, `.webp`, and scanned `.pdf` uploads directly in the draft dropzone[cite: 7].
   - Extracts text via OCR, computes WCAG 2.2 SC 1.4.3 contrast ratios (`4.5:1` minimum), compares dominant colours against Northwind Outdoor Co.'s approved palette (`#1F3A2E`, `#D9822B`, `#F4EFE6`, `#1B1B1B`, `#FFFFFF`), and flags retired 2022 logo markers[cite: 7].

---

## Quickstart (Run Locally)

### 1. Clone and install
```bash
git clone [https://github.com/minermob25-tech/Mohar.git](https://github.com/minermob25-tech/Mohar.git)
cd Mohar/mohar
pip install -r requirements.txt