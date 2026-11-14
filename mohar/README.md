# Mohar: brand review desk (CodeNova Round 2, single-page demo)

Plain HTML, CSS and JavaScript. No build step, no backend. Open `index.html` with VS Code Live Server.
Needs internet for Google Fonts, pdf.js (PDF reading) and mammoth.js (Word reading).

## What it does
- Pick a content type: Advertisement, Social post, Email, Newsletter, SMS, Website copy, Blog article, Document.
- Paste text, or drop a PDF, Word (.docx) or text file. Text is read in the browser and never uploaded.
- Every finding names the rule or approved fact behind it, a verdict (Confirmed / Likely / Needs review) and a suggested edit you can accept, edit or dismiss with a reason.
- Compliance score shows where the draft started and where it is now.
- Send to a reviewer; the Reviewer or Manager approves or requests changes. Version history, audit trail and Guideline Health insights update as you go.

## Limits
Scanned PDFs have no selectable text and need OCR, which is not included. No AI model is used; all findings come from the rule engine. Data is stored in the browser only.
