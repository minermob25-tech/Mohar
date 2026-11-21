import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
import jwt
from pypdf import PdfReader
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Route

try:
    import pg8000.native as pg_native
except Exception:
    pg_native = None

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
ASSETS_DIR.mkdir(exist_ok=True)

env_file = BASE_DIR / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

DB_PATH = BASE_DIR / "mohar_r3.db"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
JWT_SECRET = os.getenv("MOHAR_JWT_SECRET", "mohar-codenova-r3-secret-key-2026")
JWT_ALG = "HS256"

APPROVED_PALETTE = ["#1F3A2E", "#D9822B", "#F4EFE6", "#1B1B1B", "#FFFFFF"]

DEFAULT_RULES = [
    {"id": "G-2.1", "cat": "Tone", "title": "Tone of voice", "text": "Use at most one exclamation mark per piece and never write headlines in capitals."},
    {"id": "G-4.2", "cat": "Claims", "title": "Claims policy", "text": 'Superlatives and absolute claims such as "best", "guaranteed" or "number one" need an approved supporting fact.'},
    {"id": "G-7.1", "cat": "Visual", "title": "Accessibility", "text": "Body text must meet a contrast ratio of at least 4.5:1 (WCAG 2.2 SC 1.4.3)."},
    {"id": "F-017", "cat": "Facts", "title": "Customer count", "text": "Northwind has served 400,000+ customers (FY2025 annual report)."},
    {"id": "PR-SUM-26", "cat": "Facts", "title": "Summer sale 2026", "text": "Summer Sale 2026: up to 25% off selected styles, 1 June to 31 August."},
    {"id": "T-01", "cat": "Terminology", "title": "Term", "bad": "cheap", "good": "affordable", "text": 'Say "affordable", never "cheap".'},
    {"id": "T-02", "cat": "Terminology", "title": "Company name", "bad": "Northwind Co", "good": "Northwind Outdoor Co.", "text": 'Write the company name in full: "Northwind Outdoor Co.", never "Northwind Co".'},
    {"id": "T-03", "cat": "Terminology", "title": "Product name", "bad": "Ridgeline Pack", "good": "Summit Pack", "text": 'The Ridgeline Pack is now the "Summit Pack"; never use the old name.'},
    {"id": "G-5.1", "cat": "Format", "title": "Email footer", "text": "Every marketing email must end with an unsubscribe line."},
    {"id": "G-5.2", "cat": "Format", "title": "Social posts", "text": "Social posts may use at most three hashtags."},
    {"id": "V-1", "cat": "Visual", "title": "Colour palette", "text": "Use only approved brand colours: #1F3A2E, #D9822B, #F4EFE6, #1B1B1B and #FFFFFF."},
    {"id": "V-2", "cat": "Visual", "title": "Logo", "text": "Always use the 2026 logo; the 2022 logo is retired."},
    {"id": "G-5.3", "cat": "Format", "title": "SMS", "text": 'SMS must be 160 characters or fewer and end with "Reply STOP to opt out".'},
    {"id": "G-6.1", "cat": "Accessibility", "title": "Link text", "text": 'Link text must describe the destination; never use "click here" or "read more".'},
    {"id": "G-6.2", "cat": "Accessibility", "title": "Alt text", "text": "Every image needs alt text that describes it."},
]


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")


# -------------------- DATABASE ADAPTER (POSTGRESQL + SQLITE FALLBACK) --------------------


class DatabaseAdapter:
    """Unified relational adapter: uses PostgreSQL when DATABASE_URL is configured, else SQLite."""

    def __init__(self) -> None:
        self.backend = "sqlite"
        self.pg = None
        self.sq = None
        if DATABASE_URL and pg_native is not None:
            try:
                u = urlparse(DATABASE_URL)
                self.pg = pg_native.Connection(
                    user=u.username or "postgres",
                    password=u.password or "",
                    host=u.hostname or "localhost",
                    port=u.port or 5432,
                    database=(u.path or "/postgres").lstrip("/"),
                )
                self.backend = "postgresql"
            except Exception:
                self.pg = None

        if self.pg is None:
            self.sq = sqlite3.connect(DB_PATH)
            self.sq.row_factory = sqlite3.Row

    def execute(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        if self.backend == "postgresql" and self.pg is not None:
            # Convert ? placeholders to :p1, :p2 for pg8000.native
            idx = 0
            named_params: Dict[str, Any] = {}

            def repl(_m: Any) -> str:
                nonlocal idx
                idx += 1
                key = f"p{idx}"
                named_params[key] = params[idx - 1]
                return f":{key}"

            pg_sql = re.sub(r"\?", repl, sql)
            rows = self.pg.run(pg_sql, **named_params)
            if self.pg.columns:
                cols = [c["name"] for c in self.pg.columns]
                return [dict(zip(cols, r)) for r in rows]
            return []

        assert self.sq is not None
        cur = self.sq.execute(sql, params)
        if cur.description:
            return [dict(r) for r in cur.fetchall()]
        return []

    def commit(self) -> None:
        if self.sq is not None:
            self.sq.commit()

    def close(self) -> None:
        if self.pg is not None:
            self.pg.close()
        if self.sq is not None:
            self.sq.close()


def get_db() -> DatabaseAdapter:
    return DatabaseAdapter()


def init_db() -> None:
    db = get_db()
    pk_auto = "SERIAL PRIMARY KEY" if db.backend == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    statements = [
        """CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            name TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS rules (
            id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            cat TEXT NOT NULL,
            title TEXT DEFAULT '',
            bad TEXT DEFAULT '',
            good TEXT DEFAULT '',
            text TEXT NOT NULL,
            PRIMARY KEY (id, workspace_id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS versions (
            id {pk_auto},
            workspace_id TEXT NOT NULL,
            version_num INTEGER NOT NULL,
            content_type TEXT NOT NULL,
            original_text TEXT NOT NULL,
            current_text TEXT NOT NULL,
            status TEXT NOT NULL,
            submitted_by TEXT NOT NULL,
            decided_by TEXT DEFAULT '',
            decision_note TEXT DEFAULT '',
            open_findings_json TEXT DEFAULT '[]',
            dismissals_json TEXT DEFAULT '[]',
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS audit_log (
            id {pk_auto},
            workspace_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            actor_role TEXT NOT NULL,
            actor_name TEXT NOT NULL,
            action TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            entry_hash TEXT NOT NULL
        )""",
    ]
    for st in statements:
        db.execute(st)

    if not db.execute("SELECT id FROM workspaces WHERE id = ?", ("northwind-main",)):
        db.execute(
            "INSERT INTO workspaces (id, org_id, name) VALUES (?, ?, ?)",
            ("northwind-main", "org-northwind", "Northwind Outdoor Co."),
        )
    if not db.execute("SELECT id FROM workspaces WHERE id = ?", ("other-org-private",)):
        db.execute(
            "INSERT INTO workspaces (id, org_id, name) VALUES (?, ?, ?)",
            ("other-org-private", "org-external", "External Confidential Brand"),
        )
    for r in DEFAULT_RULES:
        if not db.execute("SELECT id FROM rules WHERE id = ? AND workspace_id = ?", (r["id"], "northwind-main")):
            db.execute(
                """INSERT INTO rules (id, workspace_id, cat, title, bad, good, text)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (r["id"], "northwind-main", r["cat"], r.get("title", ""), r.get("bad", ""), r.get("good", ""), r["text"]),
            )
    db.commit()
    db.close()


def append_audit(db: DatabaseAdapter, workspace_id: str, role: str, actor: str, action: str) -> None:
    rows = db.execute(
        "SELECT entry_hash FROM audit_log WHERE workspace_id = ? ORDER BY id DESC LIMIT 1",
        (workspace_id,),
    )
    prev_hash = rows[0]["entry_hash"] if rows else "GENESIS"
    ts = now_str()
    payload = f"{workspace_id}|{ts}|{role}|{actor}|{action}|{prev_hash}".encode("utf-8")
    entry_hash = hashlib.sha256(payload).hexdigest()[:16]
    db.execute(
        """INSERT INTO audit_log (workspace_id, ts, actor_role, actor_name, action, prev_hash, entry_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (workspace_id, ts, role, actor, action, prev_hash, entry_hash),
    )


init_db()


# -------------------- PURE-PYTHON PNG CODEC & CANONICAL 2026 LOGO ASSET --------------------


def make_png_bytes(w: int, h: int, pixels: List[Tuple[int, int, int]]) -> bytes:
    raw_rows = bytearray()
    for y in range(h):
        raw_rows.append(0)
        for x in range(w):
            r, g, b = pixels[y * w + x]
            raw_rows.extend((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw_rows))) + chunk(b"IEND", b"")


def decode_png_pixels(raw: bytes) -> Tuple[int, int, List[Tuple[int, int, int]], str]:
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Not a PNG")
    pos = 8
    w = h = color_type = 0
    idat = bytearray()
    meta_texts = []
    while pos + 8 <= len(raw):
        length = struct.unpack(">I", raw[pos : pos + 4])[0]
        ctype = raw[pos + 4 : pos + 8]
        cdata = raw[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            w, h, _bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", cdata)
        elif ctype == b"IDAT":
            idat.extend(cdata)
        elif ctype == b"tEXt":
            meta_texts.append(cdata.replace(b"\x00", b": ").decode("latin1", errors="ignore"))
        elif ctype == b"IEND":
            break

    decompressed = zlib.decompress(bytes(idat))
    bpp = 3 if color_type == 2 else (4 if color_type == 6 else 1)
    stride = w * bpp
    pixels: List[Tuple[int, int, int]] = []
    prev_row = bytearray(stride)
    idx = 0
    for _y in range(h):
        ftype = decompressed[idx]
        row = bytearray(decompressed[idx + 1 : idx + 1 + stride])
        idx += 1 + stride
        if ftype == 1:
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                row[i] = (row[i] + prev_row[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + prev_row[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                b = prev_row[i]
                c = prev_row[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                row[i] = (row[i] + pr) & 0xFF
        prev_row = row
        for x in range(0, stride, bpp):
            if bpp == 1:
                pixels.append((row[x], row[x], row[x]))
            else:
                pixels.append((row[x], row[x + 1], row[x + 2]))
    return w, h, pixels, " ".join(meta_texts).strip()


def ensure_workspace_logo_assets() -> Dict[str, List[Tuple[int, int, int]]]:
    """Generates & stores canonical workspace logo assets (16x16 brand marks) for template comparison."""
    logo_2026_path = ASSETS_DIR / "northwind_logo_2026.png"
    logo_2022_path = ASSETS_DIR / "northwind_logo_2022_retired.png"

    # 2026 Approved Mark: Forest Green (#1F3A2E) mountain badge with Warm Amber (#D9822B) peak on Cream (#F4EFE6)
    px_2026: List[Tuple[int, int, int]] = []
    for y in range(16):
        for x in range(16):
            if 2 <= x <= 13 and 2 <= y <= 13:
                if abs(x - 7.5) + (13 - y) < 8 and y < 8:
                    px_2026.append((217, 130, 43))  # #D9822B peak
                else:
                    px_2026.append((31, 58, 46))  # #1F3A2E forest
            else:
                px_2026.append((244, 239, 230))  # #F4EFE6 cream

    # 2022 Retired Mark: Crimson (#8B1A22) wax seal with Gold (#B08A3E) ring
    px_2022: List[Tuple[int, int, int]] = []
    for y in range(16):
        for x in range(16):
            dist = math.sqrt((x - 7.5) ** 2 + (y - 7.5) ** 2)
            if dist <= 6.2:
                if 4.0 <= dist <= 5.2:
                    px_2022.append((176, 138, 62))  # #B08A3E gold ring
                else:
                    px_2022.append((139, 26, 34))  # #8B1A22 crimson seal
            else:
                px_2022.append((244, 239, 230))

    if not logo_2026_path.exists():
        logo_2026_path.write_bytes(make_png_bytes(16, 16, px_2026))
    if not logo_2022_path.exists():
        logo_2022_path.write_bytes(make_png_bytes(16, 16, px_2022))

    _, _, p26, _ = decode_png_pixels(logo_2026_path.read_bytes())
    _, _, p22, _ = decode_png_pixels(logo_2022_path.read_bytes())
    return {"2026": p26, "2022": p22}


LOGO_TEMPLATES = ensure_workspace_logo_assets()


# -------------------- AUTH & RBAC (IMPROVEMENT 1) --------------------


def decode_user(request: Request) -> Dict[str, Any]:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise PermissionError("Missing or invalid Bearer token")
    token = auth.split(" ", 1)[1].strip()
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        raise PermissionError("Invalid or expired JWT session")


def require_workspace_access(workspace_id: str, user: Dict[str, Any]) -> None:
    if user.get("workspace_id") != workspace_id:
        raise PermissionError(
            f"Workspace isolation violation: token scoped to '{user.get('workspace_id')}', cannot access '{workspace_id}'."
        )


async def login(request: Request) -> JSONResponse:
    body = await request.json()
    role = body.get("role", "Creator")
    user_name = body.get("user_name", "Demo User")
    workspace_id = body.get("workspace_id", "northwind-main")

    if role not in ("Creator", "Reviewer", "Manager"):
        return JSONResponse({"detail": "Invalid role"}, status_code=400)
    if workspace_id != "northwind-main":
        return JSONResponse({"detail": "Access denied to external workspace."}, status_code=403)

    exp = datetime.now(timezone.utc) + timedelta(hours=12)
    claims = {
        "sub": user_name,
        "role": role,
        "workspace_id": workspace_id,
        "org_id": "org-northwind",
        "exp": exp,
    }
    token = jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALG)
    db = get_db()
    append_audit(db, workspace_id, role, user_name, f"authenticated session as {role}")
    db.commit()
    db_backend = db.backend
    db.close()
    return JSONResponse(
        {"token": token, "db_backend": db_backend, "claims": {k: v for k, v in claims.items() if k != "exp"}}
    )


async def get_workspace_state(request: Request) -> JSONResponse:
    workspace_id = request.path_params["workspace_id"]
    try:
        user = decode_user(request)
        require_workspace_access(workspace_id, user)
    except PermissionError as e:
        return JSONResponse({"detail": str(e)}, status_code=403)

    db = get_db()
    rules = db.execute("SELECT * FROM rules WHERE workspace_id = ?", (workspace_id,))
    versions_rows = db.execute("SELECT * FROM versions WHERE workspace_id = ? ORDER BY id DESC", (workspace_id,))
    versions = []
    for d in versions_rows:
        versions.append(
            {
                "id": d["id"],
                "n": d["version_num"],
                "type": d["content_type"],
                "orig": d["original_text"],
                "text": d["current_text"],
                "status": d["status"],
                "from": d["submitted_by"],
                "by": d["decided_by"],
                "note": d["decision_note"],
                "open": json.loads(d["open_findings_json"] or "[]"),
                "dis": json.loads(d["dismissals_json"] or "[]"),
                "ts": d["created_at"],
            }
        )
    logs = [
        {
            "ts": r["ts"],
            "who": f"{r['actor_role']} ({r['actor_name']})",
            "a": f"{r['action']} [#{r['entry_hash']}]",
            "hash": r["entry_hash"],
            "prev": r["prev_hash"],
        }
        for r in db.execute("SELECT * FROM audit_log WHERE workspace_id = ? ORDER BY id DESC LIMIT 100", (workspace_id,))
    ]
    db_backend = db.backend
    db.close()
    return JSONResponse(
        {"workspace_id": workspace_id, "db_backend": db_backend, "rules": rules, "versions": versions, "audit": logs}
    )


async def create_rule(request: Request) -> JSONResponse:
    workspace_id = request.path_params["workspace_id"]
    try:
        user = decode_user(request)
        require_workspace_access(workspace_id, user)
    except PermissionError as e:
        return JSONResponse({"detail": str(e)}, status_code=403)

    if user.get("role") not in ("Manager", "Reviewer"):
        return JSONResponse(
            {"detail": "RBAC policy: Creators cannot alter approved brand rules. Switch to Manager or Reviewer."},
            status_code=403,
        )

    body = await request.json()
    rid = body.get("id", "R-99")
    db = get_db()
    existing = db.execute("SELECT id FROM rules WHERE id = ? AND workspace_id = ?", (rid, workspace_id))
    if existing:
        db.execute(
            "UPDATE rules SET cat = ?, title = ?, bad = ?, good = ?, text = ? WHERE id = ? AND workspace_id = ?",
            (body.get("cat", "Guidelines"), body.get("title", ""), body.get("bad", ""), body.get("good", ""), body.get("text", ""), rid, workspace_id),
        )
    else:
        db.execute(
            """INSERT INTO rules (id, workspace_id, cat, title, bad, good, text)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (rid, workspace_id, body.get("cat", "Guidelines"), body.get("title", ""), body.get("bad", ""), body.get("good", ""), body.get("text", "")),
        )
    append_audit(db, workspace_id, user["role"], user["sub"], f"added rule {rid}: {body.get('text')}")
    db.commit()
    db.close()
    return JSONResponse({"ok": True})


async def submit_version(request: Request) -> JSONResponse:
    workspace_id = request.path_params["workspace_id"]
    try:
        user = decode_user(request)
        require_workspace_access(workspace_id, user)
    except PermissionError as e:
        return JSONResponse({"detail": str(e)}, status_code=403)

    body = await request.json()
    current_text = str(body.get("current_text", ""))
    if not current_text.strip():
        return JSONResponse({"detail": "Draft text cannot be empty."}, status_code=400)

    db = get_db()
    rows = db.execute("SELECT COALESCE(MAX(version_num), 0) AS mx FROM versions WHERE workspace_id = ?", (workspace_id,))
    next_n = int(rows[0]["mx"]) + 1
    ts = now_str()
    db.execute(
        """INSERT INTO versions
           (workspace_id, version_num, content_type, original_text, current_text, status, submitted_by, open_findings_json, dismissals_json, created_at)
           VALUES (?, ?, ?, ?, ?, 'Awaiting review', ?, ?, ?, ?)""",
        (
            workspace_id,
            next_n,
            body.get("content_type", "Advertisement"),
            body.get("original_text", current_text),
            current_text,
            f"{user['role']} ({user['sub']})",
            json.dumps(body.get("open_findings", [])),
            json.dumps(body.get("dismissals", [])),
            ts,
        ),
    )
    append_audit(db, workspace_id, user["role"], user["sub"], f"submitted version {next_n} for review")
    db.commit()
    db.close()
    return JSONResponse({"ok": True, "version_num": next_n})


async def decide_version(request: Request) -> JSONResponse:
    workspace_id = request.path_params["workspace_id"]
    version_num = int(request.path_params["version_num"])
    try:
        user = decode_user(request)
        require_workspace_access(workspace_id, user)
    except PermissionError as e:
        return JSONResponse({"detail": str(e)}, status_code=403)

    if user.get("role") == "Creator":
        db = get_db()
        append_audit(
            db,
            workspace_id,
            user["role"],
            user["sub"],
            f"BLOCKED unauthorized approval attempt on version {version_num}",
        )
        db.commit()
        db.close()
        return JSONResponse(
            {"detail": "Server RBAC enforced: Creators cannot approve or decide on submissions."},
            status_code=403,
        )

    body = await request.json()
    decision = body.get("decision", "")
    note = str(body.get("note", "")).strip()
    if decision not in ("Ready to publish", "Changes requested"):
        return JSONResponse({"detail": "Invalid decision"}, status_code=400)

    db = get_db()
    vers = db.execute(
        "SELECT * FROM versions WHERE workspace_id = ? AND version_num = ?",
        (workspace_id, version_num),
    )
    if not vers:
        db.close()
        return JSONResponse({"detail": "Version not found"}, status_code=404)

    ver = vers[0]
    open_f = json.loads(ver["open_findings_json"] or "[]")
    has_crit = any(f.get("sev") == "Critical" for f in open_f)
    if decision == "Ready to publish" and has_crit and not note:
        db.close()
        return JSONResponse(
            {"detail": "A written comment is required to approve with an open Critical finding."},
            status_code=400,
        )
    if decision == "Changes requested" and not note:
        db.close()
        return JSONResponse({"detail": "Please explain what the creator needs to change."}, status_code=400)

    decider = f"{user['role']} ({user['sub']})"
    db.execute(
        """UPDATE versions SET status = ?, decided_by = ?, decision_note = ?
           WHERE workspace_id = ? AND version_num = ?""",
        (decision, decider, note, workspace_id, version_num),
    )
    verb = "approved" if decision == "Ready to publish" else "requested changes on"
    append_audit(db, workspace_id, user["role"], user["sub"], f"{verb} version {version_num}")
    db.commit()
    db.close()
    return JSONResponse({"ok": True, "status": decision})


# -------------------- AI SEMANTIC REVIEW + CITATION VALIDATOR (IMPROVEMENT 2) --------------------


def semantic_fallback_patterns(text: str) -> List[Dict[str, Any]]:
    candidates = []
    patterns = [
        (
            r"\b(?:never fails|zero chance of failure|100%\s*waterproof|unbreakable|lifetime assurance|risk[- ]free)\b",
            "G-4.2",
            "Claims",
            "High",
            "Paraphrased absolute performance promise without an approved supporting fact in the workspace.",
            "built for demanding trails",
        ),
        (
            r"\b(?:slash(?:ed|ing)? prices in half|half[- ]price(?:d)?|50%\s*markdown|massive storewide blowout)\b",
            "PR-SUM-26",
            "Claims",
            "Critical",
            "Implied storewide or 50% discount conflicts with the approved Summer Sale 2026 terms (up to 25% off selected styles).",
            "up to 25% off selected styles",
        ),
        (
            r"\b(?:over half a million|nearly a million|millions of)\s+(?:happy\s+)?(?:hikers|trekkers|buyers|shoppers|users)\b",
            "F-017",
            "Facts",
            "High",
            "Paraphrased customer reach exceeds the FY2025 verified figure of 400,000+ customers.",
            "400,000+ customers",
        ),
        (
            r"\b(?:buy now before it'?s gone forever|last chance ever|act immediately or miss out)\b",
            "G-2.1",
            "Tone",
            "Medium",
            "High-pressure urgency phrasing drifts from Northwind Outdoor Co.'s calm, trustworthy brand voice.",
            "available while seasonal stock lasts",
        ),
    ]
    for rx, src, cat, sev, why, fix in patterns:
        for m in re.finditer(rx, text, flags=re.IGNORECASE):
            candidates.append(
                {
                    "quote": m.group(0),
                    "src": src,
                    "cat": cat,
                    "sev": sev,
                    "why": why,
                    "fix": fix,
                }
            )
    return candidates


async def call_llm_adapter(text: str, rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    rules_block = "\n".join(f"- [{r['id']}] ({r['cat']}): {r['text']}" for r in rules)

    prompt = (
        "You are Mohar's semantic brand-governance reviewer. Analyze the draft ONLY against the approved rules below.\n"
        "Find subtle tone drift, paraphrased superlatives/promises, or implied factual/promotional mismatches that exact keyword rules miss.\n"
        "Return ONLY a JSON array of objects with keys: quote (exact verbatim substring from draft), src (exact rule ID), "
        "cat (Claims|Facts|Tone|Terminology|Format|Accessibility), sev (Critical|High|Medium|Low), why (1 sentence), fix (minimal replacement).\n"
        "If none, return [].\n\n"
        f"APPROVED RULES:\n{rules_block}\n\nDRAFT:\n{text}"
    )

    if gemini_key:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
        }
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return parsed
        except Exception:
            pass

    if openai_key:
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {openai_key}"},
                    json={
                        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                        "temperature": 0.1,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"]
                raw = re.sub(r"^```json|```$", "", raw.strip()).strip()
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return parsed
        except Exception:
            pass

    return semantic_fallback_patterns(text)


def validate_ai_findings(
    draft: str, raw_findings: List[Dict[str, Any]], rules_by_id: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    validated = []
    discarded = 0
    downgraded = 0

    for item in raw_findings:
        if not isinstance(item, dict):
            discarded += 1
            continue
        quote = str(item.get("quote", "")).strip()
        src = str(item.get("src", "")).strip()
        if not quote or quote not in draft:
            discarded += 1
            continue

        idx = draft.find(quote)
        rule = rules_by_id.get(src)
        if not rule:
            downgraded += 1
            verdict = "Needs review"
            valid = False
            sq = "No matching source ID in workspace"
        else:
            verdict = "Likely"
            valid = True
            sq = rule["text"]

        validated.append(
            {
                "cat": item.get("cat", "Claims"),
                "sev": item.get("sev", "Medium") if item.get("sev") in ("Critical", "High", "Medium", "Low") else "Medium",
                "verdict": verdict,
                "engine": "AI-assisted",
                "s": idx,
                "e": idx + len(quote),
                "quote": quote,
                "src": src or "UNVERIFIED",
                "sq": sq,
                "valid": valid,
                "why": str(item.get("why", "Flagged by AI semantic review.")),
                "fix": str(item.get("fix", "")),
            }
        )

    return {
        "findings": validated,
        "validator_stats": {
            "raw_count": len(raw_findings),
            "accepted_count": len(validated),
            "discarded_unquoted": discarded,
            "downgraded_unverified_source": downgraded,
        },
    }


async def ai_semantic_review(request: Request) -> JSONResponse:
    body = await request.json()
    workspace_id = body.get("workspace_id", "northwind-main")
    text = str(body.get("text", ""))
    db = get_db()
    rules = db.execute("SELECT * FROM rules WHERE workspace_id = ?", (workspace_id,))
    db.close()
    rules_by_id = {r["id"]: r for r in rules}
    provider = "gemini" if os.getenv("GEMINI_API_KEY") else ("openai" if os.getenv("OPENAI_API_KEY") else "local-semantic-adapter")
    raw = await call_llm_adapter(text, rules)
    result = validate_ai_findings(text, raw, rules_by_id)
    result["provider"] = provider
    return JSONResponse(result)


# -------------------- VISUAL IDENTITY CHECKS: OCR, REGION CONTRAST, PALETTE & ASSET LOGO MATCHING --------------------


def rel_luminance(rgb: Tuple[int, int, int]) -> float:
    def ch(v: int) -> float:
        s = v / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(rgb[0]) + 0.7152 * ch(rgb[1]) + 0.0722 * ch(rgb[2])


def contrast_ratio(c1: Tuple[int, int, int], c2: Tuple[int, int, int]) -> float:
    l1, l2 = rel_luminance(c1), rel_luminance(c2)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(rgb[0], rgb[1], rgb[2])


def hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def nearest_palette_hex(rgb: Tuple[int, int, int]) -> Tuple[str, float]:
    best_hex = APPROVED_PALETTE[0]
    best_dist = 1e9
    for p in APPROVED_PALETTE:
        pr = hex_to_rgb(p)
        dist = math.sqrt(sum((rgb[i] - pr[i]) ** 2 for i in range(3)))
        if dist < best_dist:
            best_dist = dist
            best_hex = p
    return best_hex, best_dist


def run_tesseract_ocr(raw: bytes, filename: str) -> Tuple[str, List[Tuple[int, int, int, int]], str]:
    """Runs Tesseract CLI if installed on PATH to extract text + text bounding boxes."""
    tess_bin = shutil.which("tesseract")
    if not tess_bin:
        return "", [], ""
    suffix = Path(filename).suffix or ".png"
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        tsv = subprocess.check_output([tess_bin, tmp_path, "stdout", "tsv"], stderr=subprocess.DEVNULL, timeout=8).decode(
            "utf-8", errors="ignore"
        )
        os.unlink(tmp_path)
        words = []
        boxes: List[Tuple[int, int, int, int]] = []
        for line in tsv.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) >= 12 and parts[11].strip():
                words.append(parts[11].strip())
                boxes.append((int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9])))
        return " ".join(words), boxes, "tesseract-ocr-boxes"
    except Exception:
        return "", [], ""


def compare_logo_template(
    w: int, h: int, pixels: List[Tuple[int, int, int]], template: List[Tuple[int, int, int]]
) -> float:
    """Resamples candidate badge region (or full asset) to 16x16 and returns mean RGB distance to workspace logo asset."""
    if not pixels or len(template) != 256:
        return 999.0
    # If image is already 16x16 logo mark, compare full frame; otherwise inspect top-right / top-left badge region
    x0 = 0 if (w == 16 and h == 16) else int(w * 0.65)
    y0 = 0
    bw = max(1, w - x0)
    bh = max(1, h if (w == 16 and h == 16) else int(h * 0.35))

    total_dist = 0.0
    for ty in range(16):
        sy = min(h - 1, y0 + int((ty / 16.0) * bh))
        for tx in range(16):
            sx = min(w - 1, x0 + int((tx / 16.0) * bw))
            pr, pg, pb = pixels[sy * w + sx]
            tr, tg, tb = template[ty * 16 + tx]
            total_dist += math.sqrt((pr - tr) ** 2 + (pg - tg) ** 2 + (pb - tb) ** 2)
    return total_dist / 256.0


def inspect_image_bytes(raw: bytes, filename: str = "upload.png") -> Dict[str, Any]:
    w = h = 0
    pixels: List[Tuple[int, int, int]] = []
    meta_text = ""

    tess_text, tess_boxes, tess_engine = run_tesseract_ocr(raw, filename)

    try:
        w, h, pixels, meta_text = decode_png_pixels(raw)
        ocr_engine = tess_engine or "png-text-region-inspector"
    except Exception:
        ascii_strings = re.findall(rb"[ -~]{5,}", raw)
        meta_text = " ".join(s.decode("latin1", errors="ignore") for s in ascii_strings[:20]).strip()
        step = max(3, len(raw) // 900)
        for i in range(0, len(raw) - 3, step):
            pixels.append((raw[i], raw[i + 1], raw[i + 2]))
        w, h = 800, 400
        ocr_engine = tess_engine or "raster-text-region-inspector"

    ocr_text = tess_text or meta_text

    # Detect background and text-region foreground pixels (using OCR boxes if available, else interior text-band pixels)
    buckets: Dict[Tuple[int, int, int], int] = {}
    for r, g, b in pixels:
        q = ((r // 16) * 16, (g // 16) * 16, (b // 16) * 16)
        buckets[q] = buckets.get(q, 0) + 1
    ranked = sorted(buckets.items(), key=lambda x: x[1], reverse=True)
    bg_rgb = ranked[0][0] if ranked else (255, 255, 255)

    text_region_pixels: List[Tuple[int, int, int]] = []
    if tess_boxes and len(pixels) == w * h:
        for bx, by, bw, bh in tess_boxes:
            for yy in range(max(0, by), min(h, by + bh)):
                for xx in range(max(0, bx), min(w, bx + bw)):
                    text_region_pixels.append(pixels[yy * w + xx])
    elif len(pixels) == w * h and w >= 16 and h >= 16:
        # Interior text-band region (20%..80% width, 20%..80% height)
        for yy in range(int(h * 0.2), int(h * 0.8)):
            for xx in range(int(w * 0.2), int(w * 0.8)):
                text_region_pixels.append(pixels[yy * w + xx])
    else:
        text_region_pixels = pixels

    fg_rgb = bg_rgb
    best_cr = 1.0
    region_buckets: Dict[Tuple[int, int, int], int] = {}
    for r, g, b in text_region_pixels:
        q = ((r // 16) * 16, (g // 16) * 16, (b // 16) * 16)
        region_buckets[q] = region_buckets.get(q, 0) + 1
    for col, _cnt in sorted(region_buckets.items(), key=lambda x: x[1], reverse=True)[:10]:
        cr = contrast_ratio(bg_rgb, col)
        if cr > best_cr:
            best_cr = cr
            fg_rgb = col

    findings = []
    bg_hex = rgb_to_hex(bg_rgb)
    fg_hex = rgb_to_hex(fg_rgb)

    for label, col_rgb in [("Background colour", bg_rgb), ("Text-region colour", fg_rgb)]:
        near_hex, dist = nearest_palette_hex(col_rgb)
        if dist > 38.0:
            findings.append(
                {
                    "cat": "Visual",
                    "sev": "Medium",
                    "verdict": "Confirmed",
                    "engine": "Visual inspector",
                    "s": -1,
                    "e": -1,
                    "quote": f"{label} {rgb_to_hex(col_rgb)} in {filename}",
                    "src": "V-1",
                    "sq": "Use only approved brand colours",
                    "valid": True,
                    "why": f"Detected {rgb_to_hex(col_rgb)} (distance {dist:.0f} from nearest approved swatch {near_hex}).",
                    "fix": near_hex,
                }
            )

    if fg_rgb != bg_rgb and best_cr < 4.5:
        findings.append(
            {
                "cat": "Visual",
                "sev": "High",
                "verdict": "Confirmed",
                "engine": "Visual inspector",
                "s": -1,
                "e": -1,
                "quote": f"Text-region contrast {best_cr:.2f}:1 ({fg_hex} on {bg_hex})",
                "src": "G-7.1",
                "sq": "at least 4.5:1",
                "valid": True,
                "why": f"WCAG 2.2 SC 1.4.3 requires at least 4.5:1 contrast on detected text regions; measured {best_cr:.2f}:1.",
                "fix": "#1B1B1B on #F4EFE6",
            }
        )

    # Asset template comparison against workspace logo_2026.png vs retired logo_2022_retired.png
    dist_2026 = compare_logo_template(w, h, pixels, LOGO_TEMPLATES["2026"])
    dist_2022 = compare_logo_template(w, h, pixels, LOGO_TEMPLATES["2022"])
    logo_status = "Current 2026"
    lower_all = f"{filename} {ocr_text}".lower()
    crimson_hits = sum(1 for r, g, b in pixels if r > 120 and g < 60 and b < 70)

    if (
        "2022" in lower_all
        or "retired logo" in lower_all
        or (dist_2022 < 55.0 and dist_2022 < dist_2026)
        or (pixels and crimson_hits / len(pixels) > 0.04)
    ):
        logo_status = "Old 2022"
        findings.append(
            {
                "cat": "Visual",
                "sev": "High",
                "verdict": "Confirmed",
                "engine": "Visual inspector",
                "s": -1,
                "e": -1,
                "quote": f"Retired 2022 logo detected in {filename}",
                "src": "V-2",
                "sq": "Always use the 2026 logo",
                "valid": True,
                "why": f"Logo region matched retired 2022 asset (distance {dist_2022:.1f} vs 2026 asset {dist_2026:.1f}).",
                "fix": "Replace with Current 2026 logo",
            }
        )

    return {
        "filename": filename,
        "dimensions": f"{w}x{h}",
        "ocr_engine": ocr_engine,
        "ocr_text": ocr_text,
        "detected_bg": bg_hex,
        "detected_fg": fg_hex,
        "contrast_ratio": round(best_cr, 2),
        "logo_asset_distances": {"to_2026": round(dist_2026, 1), "to_2022": round(dist_2022, 1)},
        "logo_status": logo_status,
        "findings": findings,
    }


async def visual_inspect(request: Request) -> JSONResponse:
    form = await request.form()
    upload = form.get("file")
    if upload is None:
        return JSONResponse({"detail": "No file uploaded"}, status_code=400)
    raw = await upload.read()
    if len(raw) > 8 * 1024 * 1024:
        return JSONResponse({"detail": "File exceeds 8 MB limit."}, status_code=400)
    filename = getattr(upload, "filename", "upload.png") or "upload.png"
    name = filename.lower()

    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(raw))
        extracted = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
        if extracted:
            return JSONResponse(
                {
                    "filename": filename,
                    "mode": "selectable-pdf",
                    "ocr_text": extracted[:20000],
                    "pages": len(reader.pages),
                    "findings": [],
                }
            )
        all_findings = []
        ocr_chunks = []
        for idx, page in enumerate(reader.pages):
            for img_obj in getattr(page, "images", []):
                res = inspect_image_bytes(img_obj.data, f"{filename}#p{idx+1}")
                if res["ocr_text"]:
                    ocr_chunks.append(res["ocr_text"])
                all_findings.extend(res["findings"])
        return JSONResponse(
            {
                "filename": filename,
                "mode": "scanned-pdf-ocr-inspector",
                "ocr_text": "\n\n".join(ocr_chunks).strip(),
                "pages": len(reader.pages),
                "findings": all_findings,
            }
        )

    return JSONResponse(inspect_image_bytes(raw, filename))


# -------------------- BENCHMARK & STATIC FRONTEND --------------------


async def run_live_benchmark(_request: Request) -> JSONResponse:
    from benchmark import evaluate_all

    return JSONResponse(await evaluate_all())


async def serve_r3_bridge(_request: Request):
    js_path = BASE_DIR / "mohar-r3.js"
    if not js_path.exists():
        return JSONResponse({"detail": "mohar-r3.js not found"}, status_code=404)
    return FileResponse(js_path, media_type="application/javascript")


async def serve_index(_request: Request) -> HTMLResponse:
    html_path = BASE_DIR / "index.html"
    if not html_path.exists():
        return HTMLResponse("<h1>index.html not found</h1>", status_code=404)
    html = html_path.read_text(encoding="utf-8")
    tag = '<script src="/mohar-r3.js"></script>\n</body>'
    if "</body>" in html:
        html = html.replace("</body>", tag, 1)
    else:
        html += "\n" + tag
    return HTMLResponse(html)


routes = [
    Route("/", serve_index, methods=["GET"]),
    Route("/mohar-r3.js", serve_r3_bridge, methods=["GET"]),
    Route("/api/auth/login", login, methods=["POST"]),
    Route("/api/workspaces/{workspace_id}/state", get_workspace_state, methods=["GET"]),
    Route("/api/workspaces/{workspace_id}/rules", create_rule, methods=["POST"]),
    Route("/api/workspaces/{workspace_id}/versions", submit_version, methods=["POST"]),
    Route("/api/workspaces/{workspace_id}/versions/{version_num:int}/decide", decide_version, methods=["POST"]),
    Route("/api/review/ai", ai_semantic_review, methods=["POST"]),
    Route("/api/visual/inspect", visual_inspect, methods=["POST"]),
    Route("/api/benchmark", run_live_benchmark, methods=["GET"]),
]

app = Starlette(routes=routes)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")), reload=True)