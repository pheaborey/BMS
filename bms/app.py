"""BoreyBMS — business management system."""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import sqlite3
import tempfile
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.datastructures import UploadFile

from backup import BackupError, create_backup, database_tables, restore_backup
from db import (
    TZ,
    adjust_stock,
    bundle_root,
    clock,
    connect,
    db,
    get_settings,
    hash_password,
    init_db,
    next_code,
    next_number,
    now,
    set_setting,
    today,
    today_s,
    use_database,
    verify_password,
)
from i18n import LANGUAGES, translator
from stores import (
    STORE_DIR,
    create_store_record,
    delete_store_record,
    get_store_path,
    init_store_registry,
    list_stores,
)

ROOT = bundle_root()
STATIC = ROOT / "static"
templates = Jinja2Templates(directory=str(ROOT / "templates"))

app = FastAPI(title="BoreyBMS")
APP_MODE = os.environ.get("BMS_MODE", "offline").strip().lower()
SESSION_SECRET = os.environ.get("BMS_SESSION_SECRET")
if APP_MODE == "online" and (not SESSION_SECRET or len(SESSION_SECRET) < 32):
    raise RuntimeError("BMS_SESSION_SECRET must be at least 32 characters in online mode.")
if APP_MODE not in {"offline", "online"}:
    raise RuntimeError("BMS_MODE must be either 'offline' or 'online'.")


class StoreDatabaseMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        store_id = request.session.get("store_id", "default")
        store_id = store_id if isinstance(store_id, str) else "default"
        database_path = get_store_path(store_id)
        if database_path is None:
            request.session.clear()
            database_path = get_store_path("default")
        with use_database(database_path):
            return await call_next(request)


app.add_middleware(StoreDatabaseMiddleware)
app.add_middleware(
    __import__("starlette.middleware.sessions", fromlist=["SessionMiddleware"]).SessionMiddleware,
    secret_key=SESSION_SECRET or "meridian-bms-demo-session-key",
    session_cookie="meridian_session",
    max_age=60 * 60 * 14,
    same_site="lax",
    https_only=APP_MODE == "online",
)

CURRENCIES = {
    "USD": {"symbol": "$", "position": "prefix", "decimals": 2},
    "EUR": {"symbol": "€", "position": "prefix", "decimals": 2},
    "VND": {"symbol": "₫", "position": "suffix", "decimals": 0},
    "KHR": {"symbol": "KHR", "position": "suffix", "decimals": 0},
}
EXPENSE_CATS = ["rent", "utilities", "transport", "supplies", "wages", "maintenance", "other"]
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")

PERMS = {
    "dashboard": {"view": {"admin", "manager", "cashier", "staff"}, "write": {"admin", "manager", "cashier", "staff"}},
    "users": {"view": {"admin"}, "write": {"admin"}},
    "employees": {"view": {"admin", "manager"}, "write": {"admin", "manager"}},
    "customers": {"view": {"admin", "manager", "cashier"}, "write": {"admin", "manager", "cashier"}},
    "suppliers": {"view": {"admin", "manager"}, "write": {"admin", "manager"}},
    "products": {"view": {"admin", "manager", "staff"}, "write": {"admin", "manager"}},
    "categories": {"view": {"admin", "manager"}, "write": {"admin", "manager"}},
    "stock": {"view": {"admin", "manager", "staff"}, "write": {"admin", "manager"}},
    "purchases": {"view": {"admin", "manager"}, "write": {"admin", "manager"}},
    "sales": {"view": {"admin", "manager", "cashier"}, "write": {"admin", "manager"}},
    "pos": {"view": {"admin", "manager", "cashier"}, "write": {"admin", "manager", "cashier"}},
    "expenses": {"view": {"admin", "manager"}, "write": {"admin", "manager"}},
    "reports": {"view": {"admin", "manager"}, "write": {"admin", "manager"}},
    "invoices": {"view": {"admin", "manager", "cashier"}, "write": {"admin", "manager", "cashier"}},
    "attendance": {"view": {"admin", "manager", "staff"}, "write": {"admin", "manager", "staff"}},
    "attendance_adjust": {"view": {"admin"}, "write": {"admin"}},
    "settings": {"view": {"admin"}, "write": {"admin"}},
    "permissions": {"view": {"admin"}, "write": {"admin"}},
}

NAV = [
    ("overview", [("dashboard", "/", "dashboard")]),
    ("people", [
        ("users", "/users", "users"),
        ("employees", "/employees", "employees"),
        ("customers", "/customers", "customers"),
        ("suppliers", "/suppliers", "suppliers"),
    ]),
    ("catalog", [
        ("products", "/products", "products"),
        ("categories", "/categories", "categories"),
        ("stock", "/stock", "stock"),
    ]),
    ("trade", [
        ("purchases", "/purchases", "purchases"),
        ("sales", "/sales", "sales"),
        ("pos", "/pos", "pos"),
        ("invoices", "/invoices", "invoices"),
    ]),
    ("finance", [
        ("expenses", "/expenses", "expenses"),
        ("reports", "/reports", "reports"),
    ]),
    ("floor", [("attendance", "/attendance", "attendance")]),
    ("system", [
        ("settings", "/settings", "settings"),
        ("permissions", "/permissions", "permissions"),
    ]),
]

DEMOS = [
    ("admin", "admin123", "admin", "role_hint_admin"),
    ("manager", "manager123", "manager", "role_hint_manager"),
    ("cashier", "cashier123", "cashier", "role_hint_cashier"),
    ("staff", "staff123", "staff", "role_hint_staff"),
]


def _text(label, **extra):
    field = {"name": label if "name" not in extra else extra["name"], "label": extra.get("label", label), "type": "text"}
    field.update(extra)
    return field


ENTITIES = {
    "users": {
        "table": "users",
        "perm": "users",
        "singular": "user",
        "search": ["username", "full_name", "email", "role"],
        "order": "id DESC",
        "columns": [
            {"key": "username", "label": "username"},
            {"key": "full_name", "label": "full_name"},
            {"key": "role", "label": "role", "kind": "badge"},
            {"key": "email", "label": "email"},
            {"key": "active", "label": "status", "kind": "bool"},
        ],
        "fields": [
            {"name": "full_name", "label": "full_name", "type": "text", "required": True},
            {"name": "username", "label": "username", "type": "text", "required": True},
            {"name": "email", "label": "email", "type": "email"},
            {"name": "role", "label": "role", "type": "select", "options": "roles", "required": True, "default": "cashier"},
            {"name": "password", "label": "password", "type": "password"},
            {"name": "active", "label": "active", "type": "checkbox"},
        ],
    },
    "employees": {
        "table": "employees",
        "perm": "employees",
        "singular": "employee",
        "search": ["code", "full_name", "phone", "email", "department", "position"],
        "order": "id DESC",
        "columns": [
            {"key": "code", "label": "code"},
            {"key": "full_name", "label": "full_name"},
            {"key": "department", "label": "department"},
            {"key": "position", "label": "position"},
            {"key": "phone", "label": "phone"},
            {"key": "status", "label": "status", "kind": "badge"},
        ],
        "fields": [
            {"name": "full_name", "label": "full_name", "type": "text", "required": True},
            {"name": "phone", "label": "phone", "type": "text"},
            {"name": "email", "label": "email", "type": "email"},
            {"name": "department", "label": "department", "type": "text"},
            {"name": "position", "label": "position", "type": "text"},
            {"name": "salary", "label": "salary", "type": "money"},
            {"name": "hire_date", "label": "hire_date", "type": "date"},
            {"name": "status", "label": "status", "type": "select", "options": "employee_status", "required": True, "default": "active"},
        ],
    },
    "customers": {
        "table": "customers",
        "perm": "customers",
        "singular": "customer",
        "search": ["code", "name", "phone", "email", "company"],
        "order": "id DESC",
        "columns": [
            {"key": "code", "label": "code"},
            {"key": "name", "label": "name"},
            {"key": "company", "label": "company"},
            {"key": "phone", "label": "phone"},
            {"key": "email", "label": "email"},
        ],
        "fields": [
            {"name": "name", "label": "name", "type": "text", "required": True},
            {"name": "phone", "label": "phone", "type": "text"},
            {"name": "email", "label": "email", "type": "email"},
            {"name": "company", "label": "company", "type": "text"},
            {"name": "address", "label": "address", "type": "textarea", "span": 2},
            {"name": "notes", "label": "notes", "type": "textarea", "span": 2},
        ],
    },
    "suppliers": {
        "table": "suppliers",
        "perm": "suppliers",
        "singular": "supplier",
        "search": ["code", "name", "phone", "email", "company"],
        "order": "id DESC",
        "columns": [
            {"key": "code", "label": "code"},
            {"key": "name", "label": "name"},
            {"key": "company", "label": "company"},
            {"key": "phone", "label": "phone"},
            {"key": "email", "label": "email"},
        ],
        "fields": [
            {"name": "name", "label": "name", "type": "text", "required": True},
            {"name": "phone", "label": "phone", "type": "text"},
            {"name": "email", "label": "email", "type": "email"},
            {"name": "company", "label": "company", "type": "text"},
            {"name": "address", "label": "address", "type": "textarea", "span": 2},
            {"name": "notes", "label": "notes", "type": "textarea", "span": 2},
        ],
    },
    "categories": {
        "table": "categories",
        "perm": "categories",
        "singular": "category",
        "search": ["name", "description"],
        "order": "name COLLATE NOCASE",
        "columns": [
            {"key": "name", "label": "name"},
            {"key": "description", "label": "description"},
        ],
        "fields": [
            {"name": "name", "label": "name", "type": "text", "required": True},
            {"name": "description", "label": "description", "type": "textarea", "span": 2},
        ],
    },
    "products": {
        "table": "products",
        "perm": "products",
        "singular": "product",
        "search": ["sku", "name", "unit", "category_name"],
        "order": "name COLLATE NOCASE",
        "columns": [
            {"key": "name", "label": "product", "kind": "product"},
            {"key": "category_name", "label": "category"},
            {"key": "price", "label": "price", "kind": "money"},
            {"key": "stock", "label": "current_stock", "kind": "stock"},
            {"key": "active", "label": "status", "kind": "bool"},
        ],
        "fields": [
            {"name": "name", "label": "name", "type": "text", "required": True},
            {"name": "sku", "label": "sku", "type": "text"},
            {"name": "category_id", "label": "category", "type": "select", "options": "categories", "coerce": "int", "required": True},
            {"name": "cost", "label": "cost", "type": "money", "required": True},
            {"name": "price", "label": "price", "type": "money", "required": True},
            {"name": "unit", "label": "unit", "type": "text", "default": "pcs"},
            {"name": "reorder_level", "label": "reorder_level", "type": "number", "default": 5},
            {"name": "opening_stock", "label": "opening_stock", "type": "number", "create_only": True, "default": 0},
            {"name": "description", "label": "description", "type": "textarea", "span": 2},
            {"name": "active", "label": "active", "type": "checkbox"},
        ],
    },
    "expenses": {
        "table": "expenses",
        "perm": "expenses",
        "singular": "expense",
        "search": ["category", "note", "expense_date"],
        "order": "expense_date DESC, id DESC",
        "columns": [
            {"key": "expense_date", "label": "date"},
            {"key": "category", "label": "category", "kind": "badge"},
            {"key": "amount", "label": "amount", "kind": "money"},
            {"key": "note", "label": "note"},
        ],
        "fields": [
            {"name": "category", "label": "category", "type": "select", "options": "expense_categories", "required": True, "default": "supplies"},
            {"name": "amount", "label": "amount", "type": "money", "required": True},
            {"name": "expense_date", "label": "expense_date", "type": "date", "required": True},
            {"name": "note", "label": "note", "type": "textarea", "span": 2},
        ],
    },
}


class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    pass


def format_money(value, settings) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    spec = CURRENCIES.get(settings.get("currency") or "USD", CURRENCIES["USD"])
    if spec["decimals"] == 0:
        text = f"{round(number):,}"
    else:
        text = f"{number:,.2f}"
    if spec["position"] == "prefix":
        return spec["symbol"] + text
    return f"{text} {spec['symbol']}"


def fmt_qty(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    if abs(number - round(number)) < 0.001:
        return str(int(round(number)))
    return f"{number:.2f}"


def fmt_input_number(value) -> str:
    if value is None or value == "":
        return ""
    number = float(value)
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.2f}".rstrip("0").rstrip(".")


MAX_PRODUCT_IMAGE_BYTES = 5 * 1024 * 1024


def product_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def allowed(user, perm, write=False) -> bool:
    if not user or perm not in PERMS:
        return False
    bucket = "write" if write else "view"
    if user["role"] == "admin":
        return user["role"] in PERMS[perm][bucket]
    saved = user.get("_permissions", {}).get(perm)
    if saved is not None:
        return saved["view"] and saved[bucket] if write else saved["view"]
    return user["role"] in PERMS[perm][bucket]


def get_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return None
    with db() as conn:
        generation = conn.execute(
            "SELECT value FROM settings WHERE key='auth_generation'"
        ).fetchone()
        current_generation = generation["value"] if generation else "0"
        if request.session.get("auth_generation") != current_generation:
            store_id = request.session.get("store_id", "default")
            request.session.clear()
            if store_id != "default":
                request.session["store_id"] = store_id
            return None
        row = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
        if not row:
            return None
        user = dict(row)
        if user["role"] != "admin":
            user["_permissions"] = {
                item["perm"]: {
                    "view": bool(item["can_view"]),
                    "write": bool(item["can_write"]),
                }
                for item in conn.execute(
                    "SELECT perm, can_view, can_write FROM role_permissions WHERE role=?",
                    (user["role"],),
                ).fetchall()
                if item["perm"] in PERMS
            }
        return user


def require(request: Request, perm: str, write: bool = False):
    user = get_user(request)
    if not user:
        raise LoginRequired()
    if not allowed(user, perm, write):
        raise Forbidden()
    return user


def require_admin(request: Request):
    user = get_user(request)
    if not user:
        raise LoginRequired()
    if user["role"] != "admin":
        raise Forbidden()
    return user


def ensure_csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        import secrets
        token = secrets.token_urlsafe(24)
        request.session["csrf"] = token
    return token


def csrf_ok(request: Request, token) -> bool:
    expected = request.session.get("csrf")
    return bool(token and expected and token == expected)


def initials(user) -> str:
    if not user:
        return ""
    parts = str(user["full_name"]).split()
    return "".join(part[0] for part in parts[:2]).upper()


def build_nav(user, path: str):
    groups = []
    if not user:
        return groups
    for group, items in NAV:
        visible = []
        for key, href, icon in items:
            if not allowed(user, key):
                continue
            active = path == href if href == "/" else path == href or path.startswith(href + "/")
            visible.append({"key": key, "href": href, "icon": icon, "active": active})
        if visible:
            groups.append({"group": group, "items": visible})
    return groups


def t_for(settings=None):
    settings = settings or get_settings()
    lang = settings.get("language") or "en"
    if lang not in {code for code, _ in LANGUAGES}:
        lang = "en"
    return translator(lang), lang, settings


def render(request: Request, name: str, status_code: int = 200, **ctx):
    user = get_user(request)
    t, lang, settings = t_for()
    theme = settings.get("theme") if settings.get("theme") in ("light", "dark") else "light"
    payload = {
        "user": user,
        "settings": settings,
        "t": t,
        "lang": lang,
        "theme": theme,
        "csrf": ensure_csrf(request),
        "flash": request.session.pop("flash", None),
        "nav": build_nav(user, request.url.path),
        "path": request.url.path,
        "money": lambda value: format_money(value, settings),
        "qty": fmt_qty,
        "can": lambda perm, write=False: allowed(user, perm, write),
        "initials": initials(user),
        "languages": LANGUAGES,
        "page_title": "",
        "page_sub": "",
    }
    payload.update(ctx)
    return templates.TemplateResponse(request, name, payload, status_code=status_code)


def see(url: str, request: Request, kind: str | None = None, text: str | None = None):
    if text:
        request.session["flash"] = {"kind": kind or "ok", "text": text}
    return RedirectResponse(url, status_code=303)


def safe_next(request: Request, fallback: str = "/") -> str:
    parsed = urlparse(request.headers.get("referer") or "")
    if parsed.netloc and parsed.netloc != request.url.netloc:
        return fallback
    if not parsed.path.startswith("/"):
        return fallback
    dest = parsed.path
    if parsed.query:
        dest += "?" + parsed.query
    return dest


def greet_key() -> str:
    hour = datetime.now(TZ).hour
    if hour < 12:
        return "good_morning"
    if hour < 18:
        return "good_afternoon"
    return "good_evening"


def options_for(kind: str, t, conn):
    if kind == "roles":
        return [{"value": role, "label": t(role)} for role in ("admin", "manager", "cashier", "staff")]
    if kind == "employee_status":
        return [{"value": role, "label": t(role)} for role in ("active", "on_leave", "inactive")]
    if kind == "expense_categories":
        return [{"value": cat, "label": t(cat)} for cat in EXPENSE_CATS]
    if kind == "categories":
        rows = conn.execute("SELECT id, name FROM categories ORDER BY name COLLATE NOCASE").fetchall()
        return [{"value": str(row["id"]), "label": row["name"]} for row in rows]
    return []


def load_options(spec, t):
    needed = [field for field in spec["fields"] if field["type"] == "select"]
    if not needed:
        return {}
    with db() as conn:
        return {field["name"]: options_for(field["options"], t, conn) for field in needed}


def fields_for(spec, creating: bool):
    rows = []
    for field in spec["fields"]:
        if field.get("create_only") and not creating:
            continue
        rows.append(field)
    return rows


def blank_values(fields):
    values = {}
    for field in fields:
        if "default" in field:
            values[field["name"]] = field["default"]
        elif field["type"] == "checkbox":
            values[field["name"]] = True
        elif field["type"] == "date":
            values[field["name"]] = today_s()
        else:
            values[field["name"]] = ""
    return values


def pack_values(row, fields):
    if not row:
        return blank_values(fields)
    data = dict(row)
    values = {}
    for field in fields:
        name = field["name"]
        if name not in data:
            values[name] = False if field["type"] == "checkbox" else ""
            continue
        val = data[name]
        if field["type"] == "checkbox":
            values[name] = bool(val)
        elif field["type"] == "select":
            values[name] = "" if val is None else str(val)
        elif field["type"] in ("number", "money"):
            values[name] = fmt_input_number(val)
        else:
            values[name] = "" if val is None else val
    return values


def read_fields(fields, form, t):
    values = {}
    errors = {}
    cleaned = {}
    for field in fields:
        name = field["name"]
        if field["type"] == "checkbox":
            flag = form.get(name) in ("1", "on", "true")
            values[name] = flag
            cleaned[name] = 1 if flag else 0
            continue
        raw = (form.get(name) or "").strip()
        values[name] = raw
        if field["type"] == "password":
            if raw and len(raw) < 4:
                errors[name] = t("password_short")
            cleaned[name] = raw
            continue
        if field["type"] in ("number", "money"):
            if raw == "":
                if field.get("required"):
                    errors[name] = t("required")
                else:
                    cleaned[name] = 0
                    values[name] = "0"
                continue
            try:
                number = float(raw)
            except ValueError:
                errors[name] = t("invalid_number")
                continue
            if number < 0:
                errors[name] = t("negative_money")
                continue
            if name == "amount" and number <= 0:
                errors[name] = t("amount_positive")
                continue
            cleaned[name] = round(number, 2) if field["type"] == "money" else number
            continue
        if field.get("required") and raw == "":
            errors[name] = t("required")
            continue
        if name == "username":
            raw = raw.lower()
            values[name] = raw
            if not USERNAME_RE.match(raw):
                errors[name] = t("invalid_username")
                continue
        if field["type"] == "email" and raw and ("@" not in raw or "." not in raw.split("@")[-1]):
            errors[name] = t("invalid_email")
            continue
        if field["type"] == "select" and field.get("coerce") == "int":
            if raw == "":
                cleaned[name] = None
                if field.get("required"):
                    errors[name] = t("required")
            else:
                try:
                    cleaned[name] = int(raw)
                except ValueError:
                    errors[name] = t("required")
            continue
        cleaned[name] = raw
    return values, errors, cleaned


def valid_time(value) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        return False
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError:
        return False
    return True


def make_sku(conn, name: str) -> str:
    base = "".join(ch for ch in name.upper() if ch.isalnum())[:8] or "ITEM"
    sku = base
    number = 1
    while conn.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
        number += 1
        sku = f"{base}-{number}"
    return sku


def persist_entity(conn, entity, spec, cleaned, item_id, user):
    table = spec["table"]
    data = {}
    for field in spec["fields"]:
        name = field["name"]
        if name in ("password", "opening_stock"):
            continue
        if name in cleaned:
            data[name] = cleaned[name]
    opening = float(cleaned.get("opening_stock") or 0) if entity == "products" and not item_id else 0

    if entity == "users":
        password = cleaned.get("password") or ""
        if not item_id and not password:
            return "password", None
        if password and len(password) < 4:
            return "password_short", None
        if item_id:
            current = conn.execute("SELECT * FROM users WHERE id=?", (item_id,)).fetchone()
            if not current:
                return "missing", None
            drops_admin = current["role"] == "admin" and current["active"] == 1 and (
                data.get("role") != "admin" or not data.get("active")
            )
            if drops_admin:
                others = conn.execute(
                    "SELECT COUNT(*) AS c FROM users WHERE role='admin' AND active=1 AND id!=?",
                    (item_id,),
                ).fetchone()["c"]
                if others == 0:
                    return "last_admin", None
            if password:
                data["password_hash"] = hash_password(password)
        else:
            data["password_hash"] = hash_password(password)
            data["created_at"] = now()
        clash = conn.execute(
            "SELECT id FROM users WHERE username=? AND id!=?",
            (data["username"], item_id or 0),
        ).fetchone()
        if clash:
            return "username", None
    elif entity == "products":
        for name in ("image_data", "image_mime"):
            if name in cleaned:
                data[name] = cleaned[name]
        if not (data.get("sku") or "").strip():
            data["sku"] = make_sku(conn, data.get("name") or "ITEM")
        clash = conn.execute(
            "SELECT id FROM products WHERE sku=? AND id!=?",
            (data["sku"], item_id or 0),
        ).fetchone()
        if clash:
            return "sku", None
        if data.get("category_id"):
            exists = conn.execute("SELECT 1 FROM categories WHERE id=?", (data["category_id"],)).fetchone()
            if not exists:
                return "category", None
        if not data.get("unit"):
            data["unit"] = "pcs"
        if not item_id:
            data["stock"] = 0
            data["created_at"] = now()
    elif entity == "employees" and not item_id:
        data["code"] = next_code(conn, "employees", "EMP")
        data["created_at"] = now()
        if not data.get("status"):
            data["status"] = "active"
    elif entity == "customers" and not item_id:
        data["code"] = next_code(conn, "customers", "CUS")
        data["created_at"] = now()
    elif entity == "suppliers" and not item_id:
        data["code"] = next_code(conn, "suppliers", "SUP")
        data["created_at"] = now()
    elif entity == "expenses" and not item_id:
        data["user_id"] = user["id"]
        data["created_at"] = now()
        if not data.get("expense_date"):
            data["expense_date"] = today_s()

    if item_id:
        exists = conn.execute(f"SELECT id FROM {table} WHERE id=?", (item_id,)).fetchone()
        if not exists:
            return "missing", None
        cols = list(data)
        conn.execute(
            f"UPDATE {table} SET {', '.join(col + '=?' for col in cols)} WHERE id=?",
            [data[col] for col in cols] + [item_id],
        )
        new_id = item_id
    else:
        cols = list(data)
        cur = conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [data[col] for col in cols],
        )
        new_id = cur.lastrowid
        if entity == "products" and opening:
            adjust_stock(conn, new_id, opening, "opening", "OPEN", "Opening balance", user["id"])
    return "ok", new_id


def decorate_products(conn, rows):
    cats = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM categories")}
    for row in rows:
        row["category_name"] = cats.get(row.get("category_id")) or ""
    return rows


def parse_range(start: str, end: str):
    fallback_end = today()
    fallback_start = fallback_end.replace(day=1)
    try:
        start_d = datetime.strptime(start, "%Y-%m-%d").date() if start else fallback_start
    except ValueError:
        start_d = fallback_start
    try:
        end_d = datetime.strptime(end, "%Y-%m-%d").date() if end else fallback_end
    except ValueError:
        end_d = fallback_end
    if start_d > end_d:
        start_d, end_d = end_d, start_d
    return start_d.isoformat(), end_d.isoformat()


@app.on_event("startup")
def _startup():
    init_db()
    init_store_registry()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


@app.get("/login")
def login_page(request: Request):
    if get_user(request):
        return RedirectResponse("/", status_code=303)
    demos = DEMOS if request.session.get("store_id", "default") == "default" else []
    return render(request, "login.html", page_title="", demos=demos, error="")


@app.post("/login")
async def login_post(request: Request):
    form = await request.form()
    t, _, _ = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return render(
            request,
            "message.html",
            status_code=400,
            title=t("expired"),
            message=t("expired_body"),
            href="/login",
            action=t("sign_in"),
            page_title=t("expired"),
        )
    username = (form.get("username") or "").strip().lower()
    password = form.get("password") or ""
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        row = dict(row) if row else None
        generation = conn.execute(
            "SELECT value FROM settings WHERE key='auth_generation'"
        ).fetchone()
    error = ""
    if not row or not verify_password(password, row["password_hash"]):
        error = t("invalid_login")
    elif not row["active"]:
        error = t("inactive_user")
    else:
        request.session["uid"] = row["id"]
        request.session["auth_generation"] = generation["value"] if generation else "0"
        return RedirectResponse("/", status_code=303)
    demos = DEMOS if request.session.get("store_id", "default") == "default" else []
    return render(request, "login.html", page_title="", demos=demos, error=error)


@app.post("/logout")
async def logout(request: Request):
    store_id = request.session.get("store_id", "default")
    request.session.clear()
    request.session["store_id"] = store_id
    return RedirectResponse("/login", status_code=303)


@app.get("/stores")
def stores_page(request: Request):
    require_admin(request)
    t, _, _ = t_for()
    return render(
        request,
        "stores.html",
        page_title=t("stores"),
        page_sub=t("stores_sub"),
        stores=list_stores(),
        active_store=request.session.get("store_id", "default"),
    )


@app.post("/stores")
async def store_create(request: Request):
    user = require_admin(request)
    form = await request.form()
    t, _, _ = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return see("/stores", request, "bad", t("expired_body"))
    name = (form.get("name") or "").strip()
    if not name or len(name) > 80:
        return see("/stores", request, "bad", t("store_name_required"))
    store = create_store_record(name)
    if not store:
        return see("/stores", request, "bad", t("store_name_exists"))
    try:
        init_db(
            STORE_DIR / f"{store['id']}.db",
            seed_data=False,
            admin=user,
            company_name=name,
        )
    except Exception:
        delete_store_record(store["id"])
        raise
    request.session["store_id"] = store["id"]
    request.session["flash"] = {"kind": "ok", "text": t("store_created")}
    return RedirectResponse("/", status_code=303)


@app.post("/stores/{store_id}/open")
async def store_open(request: Request, store_id: str):
    require_admin(request)
    form = await request.form()
    t, _, _ = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return see("/stores", request, "bad", t("expired_body"))
    if not get_store_path(store_id):
        raise HTTPException(404)
    if request.session.get("store_id", "default") == store_id:
        return RedirectResponse("/stores", status_code=303)
    request.session.clear()
    request.session["store_id"] = store_id
    return RedirectResponse("/login", status_code=303)


def backup_error_message(t, error: BackupError) -> str:
    return t(f"backup_error_{error.code}")


def backup_filename(mode: str, tables: list[str] | None, store_name: str) -> str:
    def slug(value: str, fallback: str) -> str:
        value = re.sub(r"[^\w]+", "-", value, flags=re.UNICODE).strip("-").lower()
        return value or fallback

    store = slug(store_name, "store")[:48].rstrip("-")
    if mode == "full":
        backup_type = "whole-database"
    else:
        table_names = "-".join(slug(table, "table") for table in (tables or []))
        backup_type = f"tables-{table_names[:120].rstrip('-')}"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return f"boreybms-{store}-{backup_type}-{timestamp}.zip"


@app.get("/backups")
def backups_page(request: Request):
    require_admin(request)
    t, _, _ = t_for()
    store_id = request.session.get("store_id", "default")
    conn = connect()
    try:
        tables = database_tables(conn)
    finally:
        conn.close()
    backup_dir = STORE_DIR.parent / "backups" / store_id
    saved_backups = []
    if backup_dir.is_dir():
        saved_backups = [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "modified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
            for path in sorted(
                backup_dir.glob("*.zip"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            if path.is_file() and not path.is_symlink()
        ]
    return render(
        request,
        "backups.html",
        page_title=t("backups"),
        page_sub=t("backups_sub"),
        backup_tables=[{"name": name, "label": t(name)} for name in tables],
        saved_backups=saved_backups,
    )


@app.post("/backups/create")
async def backup_create(request: Request):
    require_admin(request)
    form = await request.form()
    t, _, settings = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return see("/backups", request, "bad", t("expired_body"))
    mode = form.get("mode")
    if mode not in ("full", "tables"):
        return see("/backups", request, "bad", t("backup_error_invalid"))
    selected = form.getlist("tables") if mode == "tables" else None
    action = form.get("action", "download")
    if action not in ("download", "save"):
        return see("/backups", request, "bad", t("backup_error_invalid"))
    temp_dir = None
    store_id = request.session.get("store_id", "default")
    filename = backup_filename(mode, selected, settings.get("company_name", ""))
    if action == "save":
        backup_dir = STORE_DIR.parent / "backups" / store_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        archive_path = backup_dir / filename
    else:
        temp_dir = Path(tempfile.mkdtemp(prefix="meridian-backup-"))
        archive_path = temp_dir / filename
    conn = connect()
    try:
        create_backup(
            conn,
            archive_path,
            selected_tables=selected,
            store_id=store_id,
            store_name=settings.get("company_name", ""),
        )
    except BackupError as error:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            archive_path.unlink(missing_ok=True)
        return see("/backups", request, "bad", backup_error_message(t, error))
    except Exception:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            archive_path.unlink(missing_ok=True)
        raise
    finally:
        conn.close()
    if action == "save":
        request.session["flash"] = {"kind": "ok", "text": t("backup_saved")}
        return RedirectResponse("/backups", status_code=303)
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(shutil.rmtree, temp_dir, True),
    )


@app.post("/backups/restore")
async def backup_restore(request: Request):
    require_admin(request)
    form = await request.form()
    t, _, _ = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return see("/backups", request, "bad", t("expired_body"))
    source_type = form.get("backup_source")
    upload = form.get("backup_file")
    source_file = None
    saved_file = None
    try:
        if source_type == "saved":
            filename = form.get("saved_backup")
            if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".zip"):
                return see("/backups", request, "bad", t("backup_error_invalid"))
            store_id = request.session.get("store_id", "default")
            backup_path = STORE_DIR.parent / "backups" / store_id / filename
            if not backup_path.is_file() or backup_path.is_symlink():
                return see("/backups", request, "bad", t("backup_error_invalid"))
            if backup_path.stat().st_size > 250 * 1024 * 1024:
                return see("/backups", request, "bad", t("backup_error_too_large"))
            saved_file = backup_path.open("rb")
            source_file = saved_file
        elif source_type == "upload" and isinstance(upload, UploadFile):
            upload.file.seek(0, 2)
            if upload.file.tell() > 250 * 1024 * 1024:
                return see("/backups", request, "bad", t("backup_error_too_large"))
            upload.file.seek(0)
            source_file = upload.file
        else:
            return see("/backups", request, "bad", t("backup_error_invalid"))
        conn = connect()
        try:
            mode, _ = restore_backup(
                conn,
                source_file,
            )
        finally:
            conn.close()
    except BackupError as error:
        return see("/backups", request, "bad", backup_error_message(t, error))
    finally:
        if saved_file:
            saved_file.close()
        if isinstance(upload, UploadFile):
            await upload.close()
    store_id = request.session.get("store_id", "default")
    request.session.clear()
    request.session["store_id"] = store_id
    message = t("backup_restored_full" if mode == "full" else "backup_restored_tables")
    request.session["flash"] = {"kind": "ok", "text": message}
    return RedirectResponse("/login", status_code=303)


@app.post("/locale")
async def locale(request: Request):
    form = await request.form()
    if not csrf_ok(request, form.get("csrf")):
        return RedirectResponse("/login", status_code=303)
    language = form.get("language")
    theme = form.get("theme")
    if language in {code for code, _ in LANGUAGES}:
        set_setting("language", language)
    if theme in ("light", "dark"):
        set_setting("theme", theme)
    return RedirectResponse(safe_next(request, "/login"), status_code=303)


@app.get("/")
def dashboard(request: Request):
    user = require(request, "dashboard")
    t, _, settings = t_for()
    day = today_s()
    month = today().replace(day=1).isoformat()
    with db() as conn:
        today_row = conn.execute(
            "SELECT COALESCE(SUM(total),0) AS total, COUNT(*) AS tickets FROM sales WHERE status='paid' AND sale_date=?",
            (day,),
        ).fetchone()
        month_sales = conn.execute(
            "SELECT COALESCE(SUM(total),0) AS total FROM sales WHERE status='paid' AND sale_date>=?",
            (month,),
        ).fetchone()["total"]
        month_exp = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE expense_date>=?",
            (month,),
        ).fetchone()["total"]
        low_count = conn.execute(
            "SELECT COUNT(*) AS c FROM products WHERE active=1 AND stock <= reorder_level"
        ).fetchone()["c"]
        inventory = conn.execute(
            "SELECT COALESCE(SUM(stock * cost),0) AS total FROM products WHERE active=1"
        ).fetchone()["total"]
        open_orders = conn.execute(
            "SELECT COUNT(*) AS c FROM purchase_orders WHERE status='ordered'"
        ).fetchone()["c"]
        present = conn.execute(
            "SELECT COUNT(*) AS c FROM attendance WHERE work_date=? AND status IN ('present','late')",
            (day,),
        ).fetchone()["c"]
        customers = conn.execute("SELECT COUNT(*) AS c FROM customers").fetchone()["c"]
        product_count = conn.execute("SELECT COUNT(*) AS c FROM products WHERE active=1").fetchone()["c"]
        recent = [
            dict(row)
            for row in conn.execute(
                "SELECT s.id, s.number, s.sale_date, s.total, s.payment_method, s.status, "
                "COALESCE(c.name, '') AS customer_name FROM sales s "
                "LEFT JOIN customers c ON c.id=s.customer_id ORDER BY s.id DESC LIMIT 8"
            )
        ]
        low = [
            dict(row)
            for row in conn.execute(
                "SELECT id, name, sku, stock, reorder_level, unit FROM products "
                "WHERE active=1 AND stock <= reorder_level ORDER BY stock ASC, name LIMIT 6"
            )
        ]
        start = (today() - timedelta(days=13)).isoformat()
        grouped = {
            row["sale_date"]: row["total"]
            for row in conn.execute(
                "SELECT sale_date, SUM(total) AS total FROM sales WHERE status='paid' AND sale_date>=? GROUP BY sale_date",
                (start,),
            )
        }
    trend = []
    for offset in range(13, -1, -1):
        stamp = today() - timedelta(days=offset)
        key = stamp.isoformat()
        trend.append({"label": f"{stamp.day}/{stamp.month}", "total": float(grouped.get(key) or 0)})
    peak = max((point["total"] for point in trend), default=0) or 1
    scope = "full" if user["role"] in ("admin", "manager") else user["role"]
    return render(
        request,
        "dashboard.html",
        page_title=f"{t(greet_key())}, {user['full_name'].split()[0]}",
        page_sub=t("dashboard_sub"),
        scope=scope,
        today_total=today_row["total"],
        tickets=today_row["tickets"],
        month_sales=month_sales,
        month_exp=month_exp,
        low_count=low_count,
        inventory=inventory,
        open_orders=open_orders,
        present=present,
        customers=customers,
        product_count=product_count,
        recent=recent,
        low=low,
        trend=trend,
        peak=peak,
    )


@app.get("/stock")
def stock_page(request: Request):
    user = require(request, "stock")
    t, _, _ = t_for()
    with db() as conn:
        products = [dict(row) for row in conn.execute("SELECT id, sku, name, stock FROM products WHERE active=1 ORDER BY name")]
        low = [
            dict(row)
            for row in conn.execute(
                "SELECT name, sku, stock, reorder_level, unit FROM products WHERE active=1 AND stock <= reorder_level ORDER BY stock ASC"
            )
        ]
        moves = [
            dict(row)
            for row in conn.execute(
                "SELECT m.*, p.name AS product_name, p.sku FROM stock_moves m "
                "JOIN products p ON p.id=m.product_id ORDER BY m.id DESC LIMIT 40"
            )
        ]
    return render(
        request,
        "stock.html",
        page_title=t("stock"),
        page_sub=t("stock_sub"),
        products=products,
        low=low,
        moves=moves,
        can_write=allowed(user, "stock", write=True),
    )


@app.post("/stock/adjust")
async def stock_adjust(request: Request):
    user = require(request, "stock", write=True)
    form = await request.form()
    t, _, _ = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return see("/stock", request, "bad", t("expired_body"))
    try:
        product_id = int(form.get("product_id") or 0)
        qty = float(form.get("qty") or 0)
    except ValueError:
        return see("/stock", request, "bad", t("invalid_number"))
    direction = form.get("direction") if form.get("direction") in ("in", "out") else "in"
    note = (form.get("note") or "").strip()
    if qty <= 0:
        return see("/stock", request, "bad", t("qty_positive"))
    if not note:
        return see("/stock", request, "bad", t("reason_required"))
    delta = qty if direction == "in" else -qty
    try:
        with db() as conn:
            exists = conn.execute("SELECT id FROM products WHERE id=?", (product_id,)).fetchone()
            if not exists:
                return see("/stock", request, "bad", t("no_results"))
            adjust_stock(conn, product_id, delta, direction, "ADJ", note, user["id"])
    except ValueError:
        return see("/stock", request, "bad", t("insufficient_stock"))
    return see("/stock", request, "ok", t("saved_adjustment"))


@app.get("/purchases")
def purchase_list(request: Request, q: str = ""):
    require(request, "purchases")
    t, _, _ = t_for()
    needle = q.strip().lower()
    with db() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT po.*, s.name AS supplier_name FROM purchase_orders po "
                "JOIN suppliers s ON s.id=po.supplier_id ORDER BY po.id DESC"
            )
        ]
    if needle:
        rows = [
            row for row in rows
            if needle in row["number"].lower() or needle in (row["supplier_name"] or "").lower() or needle in row["status"]
        ]
    return render(
        request,
        "purchases.html",
        page_title=t("purchases"),
        page_sub=t("purchases_sub"),
        rows=rows,
        q=q,
    )


@app.get("/purchases/new")
def purchase_new(request: Request):
    require(request, "purchases", write=True)
    t, _, _ = t_for()
    with db() as conn:
        suppliers = [dict(row) for row in conn.execute("SELECT id, name FROM suppliers ORDER BY name")]
        products = [dict(row) for row in conn.execute("SELECT id, name, sku, cost FROM products WHERE active=1 ORDER BY name")]
    return render(
        request,
        "purchase_form.html",
        page_title=t("new_purchase"),
        page_sub=t("purchases_sub"),
        suppliers=suppliers,
        products=products,
        order_date=today_s(),
        error="",
    )


@app.post("/purchases/save")
async def purchase_save(request: Request):
    user = require(request, "purchases", write=True)
    form = await request.form()
    t, _, _ = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return see("/purchases", request, "bad", t("expired_body"))
    try:
        supplier_id = int(form.get("supplier_id") or 0)
    except ValueError:
        supplier_id = 0
    order_date = (form.get("order_date") or today_s()).strip()
    notes = (form.get("notes") or "").strip()
    product_ids = form.getlist("product_id")
    qtys = form.getlist("qty")
    costs = form.getlist("cost")
    merged = {}
    for pid, qty_raw, cost_raw in zip(product_ids, qtys, costs):
        try:
            pid_i = int(pid)
            qty = float(qty_raw)
            cost = float(cost_raw)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or cost < 0:
            continue
        bucket = merged.setdefault(pid_i, {"qty": 0.0, "cost": cost})
        bucket["qty"] += qty
        bucket["cost"] = cost
    if not supplier_id:
        return see("/purchases/new", request, "bad", t("supplier_required"))
    if not merged:
        return see("/purchases/new", request, "bad", t("at_least_one_line"))
    try:
        datetime.strptime(order_date, "%Y-%m-%d")
    except ValueError:
        order_date = today_s()
    with db() as conn:
        supplier = conn.execute("SELECT id FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
        if not supplier:
            return see("/purchases/new", request, "bad", t("supplier_required"))
        lines = []
        for pid, bucket in merged.items():
            product = conn.execute("SELECT id FROM products WHERE id=? AND active=1", (pid,)).fetchone()
            if not product:
                continue
            line_total = round(bucket["qty"] * bucket["cost"], 2)
            lines.append((pid, bucket["qty"], round(bucket["cost"], 2), line_total))
        if not lines:
            return see("/purchases/new", request, "bad", t("at_least_one_line"))
        total = round(sum(line[3] for line in lines), 2)
        number = next_number(conn, "purchase_orders", "number", "PO")
        po_id = conn.execute(
            "INSERT INTO purchase_orders (number, supplier_id, status, order_date, notes, total, user_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (number, supplier_id, "ordered", order_date, notes, total, user["id"], now()),
        ).lastrowid
        for pid, qty, cost, line_total in lines:
            conn.execute(
                "INSERT INTO purchase_items (po_id, product_id, qty, cost, line_total) VALUES (?,?,?,?,?)",
                (po_id, pid, qty, cost, line_total),
            )
    return see(f"/purchases/{po_id}", request, "ok", t("saved"))


@app.get("/purchases/{item_id}")
def purchase_detail(request: Request, item_id: int):
    require(request, "purchases")
    t, _, _ = t_for()
    with db() as conn:
        row = conn.execute(
            "SELECT po.*, s.name AS supplier_name, u.full_name AS author "
            "FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id "
            "LEFT JOIN users u ON u.id=po.user_id WHERE po.id=?",
            (item_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404)
        items = [
            dict(item)
            for item in conn.execute(
                "SELECT i.*, p.name, p.sku FROM purchase_items i JOIN products p ON p.id=i.product_id WHERE i.po_id=?",
                (item_id,),
            )
        ]
    return render(
        request,
        "purchase_detail.html",
        page_title=row["number"],
        page_sub=t("purchases"),
        po=dict(row),
        items=items,
    )


@app.post("/purchases/{item_id}/receive")
async def purchase_receive(request: Request, item_id: int):
    user = require(request, "purchases", write=True)
    form = await request.form()
    t, _, _ = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return see(f"/purchases/{item_id}", request, "bad", t("expired_body"))
    with db() as conn:
        row = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        if row["status"] != "ordered":
            return see(f"/purchases/{item_id}", request, "bad", t("cannot_receive"))
        items = conn.execute("SELECT * FROM purchase_items WHERE po_id=?", (item_id,)).fetchall()
        for item in items:
            adjust_stock(conn, item["product_id"], item["qty"], "purchase", row["number"], "Goods received", user["id"])
        conn.execute("UPDATE purchase_orders SET status='received' WHERE id=?", (item_id,))
    return see(f"/purchases/{item_id}", request, "ok", t("po_received"))


@app.post("/purchases/{item_id}/cancel")
async def purchase_cancel(request: Request, item_id: int):
    require(request, "purchases", write=True)
    form = await request.form()
    t, _, _ = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return see(f"/purchases/{item_id}", request, "bad", t("expired_body"))
    with db() as conn:
        row = conn.execute("SELECT status FROM purchase_orders WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        if row["status"] != "ordered":
            return see(f"/purchases/{item_id}", request, "bad", t("cannot_receive"))
        conn.execute("UPDATE purchase_orders SET status='cancelled' WHERE id=?", (item_id,))
    return see(f"/purchases/{item_id}", request, "ok", t("po_cancelled"))


@app.get("/sales")
def sales_list(request: Request, q: str = ""):
    require(request, "sales")
    t, _, _ = t_for()
    like = f"%{q.strip()}%"
    with db() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT s.*, COALESCE(c.name, '') AS customer_name, u.full_name AS seller "
                "FROM sales s LEFT JOIN customers c ON c.id=s.customer_id "
                "LEFT JOIN users u ON u.id=s.user_id "
                "WHERE ? = '%%' OR s.number LIKE ? OR COALESCE(c.name,'') LIKE ? OR s.payment_method LIKE ? "
                "ORDER BY s.sale_date DESC, s.id DESC LIMIT 300",
                (like, like, like, like),
            )
        ]
    return render(request, "sales.html", page_title=t("sales"), page_sub=t("sales_sub"), rows=rows, q=q)


@app.get("/sales/{item_id}")
def sale_detail(request: Request, item_id: int):
    user = require(request, "sales")
    t, _, _ = t_for()
    with db() as conn:
        row = conn.execute(
            "SELECT s.*, COALESCE(c.name, '') AS customer_name, u.full_name AS seller "
            "FROM sales s LEFT JOIN customers c ON c.id=s.customer_id "
            "LEFT JOIN users u ON u.id=s.user_id WHERE s.id=?",
            (item_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404)
        items = [dict(item) for item in conn.execute("SELECT * FROM sale_items WHERE sale_id=?", (item_id,))]
    return render(
        request,
        "sale_detail.html",
        page_title=row["number"],
        page_sub=t("sales"),
        sale=dict(row),
        items=items,
        can_void=allowed(user, "sales", write=True) and row["status"] == "paid",
    )


@app.post("/sales/{item_id}/void")
async def sale_void(request: Request, item_id: int):
    user = require(request, "sales", write=True)
    form = await request.form()
    t, _, _ = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return see(f"/sales/{item_id}", request, "bad", t("expired_body"))
    with db() as conn:
        row = conn.execute("SELECT * FROM sales WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        if row["status"] != "paid":
            return see(f"/sales/{item_id}", request, "bad", t("already_void"))
        items = conn.execute("SELECT * FROM sale_items WHERE sale_id=?", (item_id,)).fetchall()
        for item in items:
            adjust_stock(conn, item["product_id"], item["qty"], "void", row["number"], "Voided sale", user["id"])
        conn.execute("UPDATE sales SET status='void' WHERE id=?", (item_id,))
    return see(f"/sales/{item_id}", request, "ok", t("voided"))


@app.get("/pos")
def pos_page(request: Request):
    require(request, "pos")
    t, _, settings = t_for()
    with db() as conn:
        catalog = []
        rows = conn.execute(
            "SELECT p.id, p.name, p.sku, p.price, p.stock, p.active, c.name AS category "
            "FROM products p LEFT JOIN categories c ON c.id=p.category_id "
            "WHERE p.active=1 ORDER BY p.name"
        ).fetchall()
        for row in rows:
            catalog.append({
                "id": row["id"],
                "name": row["name"],
                "sku": row["sku"],
                "price": row["price"],
                "stock": row["stock"],
                "category": row["category"] or "",
            })
        customers = [dict(row) for row in conn.execute("SELECT id, name FROM customers ORDER BY name")]
    return render(
        request,
        "pos.html",
        page_title=t("pos"),
        page_sub=t("pos_sub"),
        catalog=catalog,
        customers=customers,
        tax_rate=float(settings.get("tax_rate") or 0),
        currency=settings.get("currency") or "USD",
    )


@app.post("/pos/checkout")
async def pos_checkout(request: Request):
    user = require(request, "pos", write=True)
    t, _, settings = t_for()
    if not csrf_ok(request, request.headers.get("x-csrf-token")):
        return JSONResponse({"ok": False, "error": t("expired_body")}, status_code=400)
    try:
        data = await request.json()
    except Exception:
        data = {}
    merged: dict[int, float] = {}
    for line in data.get("lines") or []:
        try:
            pid = int(line.get("product_id"))
            qty = float(line.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        merged[pid] = merged.get(pid, 0) + qty
    if not merged:
        return JSONResponse({"ok": False, "error": t("empty_cart")}, status_code=400)
    try:
        discount = max(0.0, float(data.get("discount") or 0))
        paid = float(data.get("paid") or 0)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": t("invalid_number")}, status_code=400)
    method = data.get("payment_method") if data.get("payment_method") in ("cash", "card", "transfer") else "cash"
    customer_id = data.get("customer_id") or None
    try:
        customer_id = int(customer_id) if customer_id else None
    except (TypeError, ValueError):
        customer_id = None
    tax_rate = float(settings.get("tax_rate") or 0)
    try:
        with db() as conn:
            if customer_id and not conn.execute("SELECT 1 FROM customers WHERE id=?", (customer_id,)).fetchone():
                customer_id = None
            resolved = []
            subtotal = 0.0
            for pid, qty in merged.items():
                product = conn.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
                if not product:
                    raise ValueError("insufficient")
                if float(product["stock"]) + 1e-9 < qty:
                    raise ValueError("insufficient")
                line_total = round(float(product["price"]) * qty, 2)
                subtotal += line_total
                resolved.append((product, qty, line_total))
            subtotal = round(subtotal, 2)
            if discount - subtotal > 1e-9:
                raise ValueError("discount")
            tax = round((subtotal - discount) * tax_rate / 100, 2)
            total = round(subtotal - discount + tax, 2)
            if method == "cash":
                if paid + 0.001 < total:
                    raise ValueError("short")
                change = round(paid - total, 2)
            else:
                paid = total
                change = 0.0
            number = next_number(conn, "sales", "number", "INV")
            sale_id = conn.execute(
                "INSERT INTO sales (number, customer_id, status, sale_date, subtotal, discount, tax_rate, tax, total, paid, change_due, payment_method, notes, user_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (number, customer_id, "paid", today_s(), subtotal, round(discount, 2), tax_rate, tax, total, round(paid, 2), change, method, "", user["id"], now()),
            ).lastrowid
            for product, qty, line_total in resolved:
                conn.execute(
                    "INSERT INTO sale_items (sale_id, product_id, name, qty, price, cost, line_total) VALUES (?,?,?,?,?,?,?)",
                    (sale_id, product["id"], product["name"], qty, product["price"], product["cost"], line_total),
                )
                adjust_stock(conn, product["id"], -qty, "sale", number, "", user["id"])
    except ValueError as exc:
        key = {"discount": "discount_high", "short": "paid_short"}.get(str(exc), "insufficient_stock")
        return JSONResponse({"ok": False, "error": t(key)}, status_code=400)
    return {"ok": True, "url": f"/invoices/{sale_id}"}


@app.get("/invoices")
def invoice_list(request: Request, q: str = ""):
    require(request, "invoices")
    t, _, _ = t_for()
    like = f"%{q.strip()}%"
    with db() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT s.id, s.number, s.sale_date, s.total, s.status, s.payment_method, COALESCE(c.name,'') AS customer_name "
                "FROM sales s LEFT JOIN customers c ON c.id=s.customer_id "
                "WHERE ? = '%%' OR s.number LIKE ? OR COALESCE(c.name,'') LIKE ? "
                "ORDER BY s.id DESC LIMIT 300",
                (like, like, like),
            )
        ]
    return render(request, "invoices.html", page_title=t("invoices"), page_sub=t("invoices_sub"), rows=rows, q=q)


@app.get("/invoices/{item_id}")
def invoice_detail(request: Request, item_id: int):
    require(request, "invoices")
    t, _, settings = t_for()
    with db() as conn:
        row = conn.execute(
            "SELECT s.*, COALESCE(c.name, '') AS customer_name, c.address AS customer_address, "
            "c.phone AS customer_phone, c.company AS customer_company, u.full_name AS seller "
            "FROM sales s LEFT JOIN customers c ON c.id=s.customer_id "
            "LEFT JOIN users u ON u.id=s.user_id WHERE s.id=?",
            (item_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404)
        items = [dict(item) for item in conn.execute("SELECT * FROM sale_items WHERE sale_id=?", (item_id,))]
    return render(
        request,
        "invoice.html",
        page_title=row["number"],
        page_sub=t("invoice"),
        sale=dict(row),
        items=items,
        company=settings,
    )


@app.get("/reports")
def reports_page(request: Request, start: str = "", end: str = ""):
    require(request, "reports")
    t, _, _ = t_for()
    start_s, end_s = parse_range(start, end)
    with db() as conn:
        revenue = conn.execute(
            "SELECT COALESCE(SUM(total),0) AS total, COUNT(*) AS c FROM sales WHERE status='paid' AND sale_date BETWEEN ? AND ?",
            (start_s, end_s),
        ).fetchone()
        cogs = conn.execute(
            "SELECT COALESCE(SUM(si.qty * si.cost),0) AS total FROM sale_items si "
            "JOIN sales s ON s.id=si.sale_id WHERE s.status='paid' AND s.sale_date BETWEEN ? AND ?",
            (start_s, end_s),
        ).fetchone()["total"]
        expenses = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE expense_date BETWEEN ? AND ?",
            (start_s, end_s),
        ).fetchone()["total"]
        purchases = conn.execute(
            "SELECT COALESCE(SUM(total),0) AS total FROM purchase_orders WHERE status='received' AND order_date BETWEEN ? AND ?",
            (start_s, end_s),
        ).fetchone()["total"]
        top = [
            dict(row)
            for row in conn.execute(
                "SELECT si.name, SUM(si.qty) AS qty, SUM(si.line_total) AS revenue FROM sale_items si "
                "JOIN sales s ON s.id=si.sale_id WHERE s.status='paid' AND s.sale_date BETWEEN ? AND ? "
                "GROUP BY si.product_id ORDER BY revenue DESC LIMIT 8",
                (start_s, end_s),
            )
        ]
        payments = [
            dict(row)
            for row in conn.execute(
                "SELECT payment_method, COUNT(*) AS tickets, SUM(total) AS total FROM sales "
                "WHERE status='paid' AND sale_date BETWEEN ? AND ? GROUP BY payment_method ORDER BY total DESC",
                (start_s, end_s),
            )
        ]
        by_supplier = [
            dict(row)
            for row in conn.execute(
                "SELECT sup.name, COUNT(po.id) AS orders, SUM(po.total) AS total FROM purchase_orders po "
                "JOIN suppliers sup ON sup.id=po.supplier_id WHERE po.status='received' AND po.order_date BETWEEN ? AND ? "
                "GROUP BY sup.id ORDER BY total DESC",
                (start_s, end_s),
            )
        ]
        by_expense = [
            dict(row)
            for row in conn.execute(
                "SELECT category, SUM(amount) AS total FROM expenses WHERE expense_date BETWEEN ? AND ? "
                "GROUP BY category ORDER BY total DESC",
                (start_s, end_s),
            )
        ]
        stock_report = [
            dict(row)
            for row in conn.execute(
                "SELECT sku, name, stock, unit, cost, price, reorder_level, "
                "stock * cost AS stock_value FROM products WHERE active=1 ORDER BY name"
            )
        ]
        expense_details = [
            dict(row)
            for row in conn.execute(
                "SELECT expense_date, category, amount, note FROM expenses "
                "WHERE expense_date BETWEEN ? AND ? ORDER BY expense_date DESC, id DESC",
                (start_s, end_s),
            )
        ]
        attendance_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT a.work_date, e.full_name, e.department, e.position, a.status, "
                "a.check_in, a.check_out, a.note FROM attendance a "
                "JOIN employees e ON e.id=a.employee_id "
                "WHERE a.work_date BETWEEN ? AND ? ORDER BY a.work_date DESC, e.full_name",
                (start_s, end_s),
            )
        ]
        purchase_details = [
            dict(row)
            for row in conn.execute(
                "SELECT po.number, po.order_date, sup.name AS supplier, po.status, po.total "
                "FROM purchase_orders po JOIN suppliers sup ON sup.id=po.supplier_id "
                "WHERE po.order_date BETWEEN ? AND ? ORDER BY po.order_date DESC, po.id DESC",
                (start_s, end_s),
            )
        ]
        grouped = {
            row["sale_date"]: row["total"]
            for row in conn.execute(
                "SELECT sale_date, SUM(total) AS total FROM sales WHERE status='paid' AND sale_date BETWEEN ? AND ? GROUP BY sale_date",
                (start_s, end_s),
            )
        }
    gross = float(revenue["total"]) - float(cogs)
    net = gross - float(expenses)
    start_d = datetime.strptime(start_s, "%Y-%m-%d").date()
    end_d = datetime.strptime(end_s, "%Y-%m-%d").date()
    trend = []
    if (end_d - start_d).days <= 31:
        cursor = start_d
        while cursor <= end_d:
            key = cursor.isoformat()
            trend.append({"label": f"{cursor.day}/{cursor.month}", "total": float(grouped.get(key) or 0)})
            cursor += timedelta(days=1)
    peak = max((point["total"] for point in trend), default=0) or 1
    return render(
        request,
        "reports.html",
        page_title=t("reports"),
        page_sub=t("reports_sub"),
        start=start_s,
        end=end_s,
        revenue=revenue["total"],
        tickets=revenue["c"],
        cogs=cogs,
        expenses=expenses,
        purchases=purchases,
        gross=gross,
        net=net,
        top=top,
        payments=payments,
        by_supplier=by_supplier,
        by_expense=by_expense,
        stock_report=stock_report,
        expense_details=expense_details,
        attendance_rows=attendance_rows,
        purchase_details=purchase_details,
        trend=trend,
        peak=peak,
    )


@app.get("/reports/sales.csv")
def reports_csv(request: Request, start: str = "", end: str = ""):
    require(request, "reports")
    start_s, end_s = parse_range(start, end)
    with db() as conn:
        rows = conn.execute(
            "SELECT s.number, s.sale_date, COALESCE(c.name,''), s.payment_method, s.status, s.subtotal, s.discount, s.tax, s.total "
            "FROM sales s LEFT JOIN customers c ON c.id=s.customer_id "
            "WHERE s.sale_date BETWEEN ? AND ? ORDER BY s.sale_date, s.id",
            (start_s, end_s),
        ).fetchall()
    return csv_download(
        ["number", "date", "customer", "payment", "status", "subtotal", "discount", "tax", "total"],
        rows,
        f"sales-{start_s}-to-{end_s}.csv",
    )


def csv_download(headers, rows, filename):
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return Response(
        buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/reports/{report_type}.csv")
def reports_data_csv(request: Request, report_type: str, start: str = "", end: str = ""):
    require(request, "reports")
    start_s, end_s = parse_range(start, end)
    queries = {
        "summary": (
            ["from", "to", "revenue", "sales_count", "cost_of_goods", "expenses", "purchases_received", "gross_profit", "net_profit"],
            "SELECT ?, ?, sales.total, sales.count, costs.total, expenses.total, purchases.total, "
            "sales.total - costs.total, sales.total - costs.total - expenses.total "
            "FROM (SELECT COALESCE(SUM(total),0) AS total, COUNT(*) AS count FROM sales "
            "WHERE status='paid' AND sale_date BETWEEN ? AND ?) sales "
            "CROSS JOIN (SELECT COALESCE(SUM(si.qty * si.cost),0) AS total FROM sale_items si "
            "JOIN sales s ON s.id=si.sale_id WHERE s.status='paid' AND s.sale_date BETWEEN ? AND ?) costs "
            "CROSS JOIN (SELECT COALESCE(SUM(amount),0) AS total FROM expenses "
            "WHERE expense_date BETWEEN ? AND ?) expenses "
            "CROSS JOIN (SELECT COALESCE(SUM(total),0) AS total FROM purchase_orders "
            "WHERE status='received' AND order_date BETWEEN ? AND ?) purchases",
            (start_s, end_s, start_s, end_s, start_s, end_s, start_s, end_s, start_s, end_s),
        ),
        "purchases": (
            ["number", "date", "supplier", "status", "total"],
            "SELECT po.number, po.order_date, sup.name, po.status, po.total "
            "FROM purchase_orders po JOIN suppliers sup ON sup.id=po.supplier_id "
            "WHERE po.order_date BETWEEN ? AND ? ORDER BY po.order_date DESC, po.id DESC",
            (start_s, end_s),
        ),
        "stock": (
            ["sku", "product", "on_hand", "unit", "unit_cost", "unit_price", "reorder_at", "stock_value"],
            "SELECT sku, name, stock, unit, cost, price, reorder_level, stock * cost "
            "FROM products WHERE active=1 ORDER BY name",
            (),
        ),
        "expenses": (
            ["date", "category", "amount", "note"],
            "SELECT expense_date, category, amount, note FROM expenses "
            "WHERE expense_date BETWEEN ? AND ? ORDER BY expense_date DESC, id DESC",
            (start_s, end_s),
        ),
        "attendance": (
            ["work_date", "employee", "department", "position", "status", "check_in", "check_out", "note"],
            "SELECT a.work_date, e.full_name, e.department, e.position, a.status, "
            "a.check_in, a.check_out, a.note FROM attendance a "
            "JOIN employees e ON e.id=a.employee_id "
            "WHERE a.work_date BETWEEN ? AND ? ORDER BY a.work_date DESC, e.full_name",
            (start_s, end_s),
        ),
        "top-products": (
            ["product", "quantity_sold", "revenue"],
            "SELECT si.name, SUM(si.qty), SUM(si.line_total) FROM sale_items si "
            "JOIN sales s ON s.id=si.sale_id WHERE s.status='paid' AND s.sale_date BETWEEN ? AND ? "
            "GROUP BY si.product_id ORDER BY SUM(si.line_total) DESC LIMIT 8",
            (start_s, end_s),
        ),
        "payments": (
            ["payment", "orders", "total"],
            "SELECT payment_method, COUNT(*), SUM(total) FROM sales "
            "WHERE status='paid' AND sale_date BETWEEN ? AND ? "
            "GROUP BY payment_method ORDER BY SUM(total) DESC",
            (start_s, end_s),
        ),
        "suppliers": (
            ["supplier", "orders", "total"],
            "SELECT sup.name, COUNT(po.id), SUM(po.total) FROM purchase_orders po "
            "JOIN suppliers sup ON sup.id=po.supplier_id "
            "WHERE po.status='received' AND po.order_date BETWEEN ? AND ? "
            "GROUP BY sup.id ORDER BY SUM(po.total) DESC",
            (start_s, end_s),
        ),
        "expense-summary": (
            ["category", "total"],
            "SELECT category, SUM(amount) FROM expenses WHERE expense_date BETWEEN ? AND ? "
            "GROUP BY category ORDER BY SUM(amount) DESC",
            (start_s, end_s),
        ),
    }
    query = queries.get(report_type)
    if query is None:
        raise HTTPException(404)
    headers, sql, params = query
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    period = "current" if report_type == "stock" else f"{start_s}-to-{end_s}"
    return csv_download(headers, rows, f"{report_type}-{period}.csv")


@app.get("/attendance")
def attendance_page(request: Request, work_date: str = ""):
    user = require(request, "attendance")
    t, _, _ = t_for()
    attendance_settings = get_settings()
    if work_date:
        try:
            datetime.strptime(work_date, "%Y-%m-%d")
        except ValueError:
            work_date = today_s()
    else:
        work_date = today_s()
    with db() as conn:
        roster = [
            dict(row)
            for row in conn.execute(
                "SELECT e.id, e.full_name, e.department, e.position, a.check_in, a.check_out, a.status, a.note "
                "FROM employees e LEFT JOIN attendance a ON a.employee_id=e.id AND a.work_date=? "
                "WHERE e.status!='inactive' ORDER BY e.full_name",
                (work_date,),
            )
        ]
    return render(
        request,
        "attendance.html",
        page_title=t("attendance"),
        page_sub=t("attendance_sub"),
        roster=roster,
        work_date=work_date,
        work_start=attendance_settings.get("work_start", "09:00"),
        work_end=attendance_settings.get("work_end", "18:00"),
        break_start=attendance_settings.get("break_start", "12:00"),
        break_end=attendance_settings.get("break_end", "13:00"),
        can_adjust=user["role"] == "admin",
    )


@app.post("/attendance/mark")
async def attendance_mark(request: Request):
    require(request, "attendance", write=True)
    form = await request.form()
    t, _, _ = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return see("/attendance", request, "bad", t("expired_body"))
    try:
        employee_id = int(form.get("employee_id") or 0)
    except ValueError:
        return see("/attendance", request, "bad", t("no_results"))
    work_date = (form.get("work_date") or today_s()).strip()
    try:
        datetime.strptime(work_date, "%Y-%m-%d")
    except ValueError:
        work_date = today_s()
    action = form.get("action") or ""
    back = f"/attendance?work_date={work_date}"
    work_start = get_settings().get("work_start", "09:00")
    with db() as conn:
        employee = conn.execute("SELECT id FROM employees WHERE id=?", (employee_id,)).fetchone()
        if not employee:
            return see(back, request, "bad", t("no_results"))
        existing = conn.execute(
            "SELECT * FROM attendance WHERE employee_id=? AND work_date=?",
            (employee_id, work_date),
        ).fetchone()
        if action == "in":
            if existing and existing["check_in"] and not existing["check_out"] and existing["status"] in ("present", "late"):
                return see(back, request, "ok", t("attendance_saved"))
            status = "late" if clock() > work_start else "present"
            conn.execute(
                "INSERT INTO attendance (employee_id, work_date, check_in, check_out, status, note) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(employee_id, work_date) DO UPDATE SET check_in=excluded.check_in, check_out=NULL, status=excluded.status",
                (employee_id, work_date, clock(), None, status, ""),
            )
        elif action == "out":
            if not existing or not existing["check_in"]:
                return see(back, request, "bad", t("need_check_in"))
            if existing["check_out"]:
                return see(back, request, "bad", t("already_out"))
            conn.execute(
                "UPDATE attendance SET check_out=? WHERE employee_id=? AND work_date=?",
                (clock(), employee_id, work_date),
            )
        elif action in ("absent", "leave"):
            conn.execute(
                "INSERT INTO attendance (employee_id, work_date, check_in, check_out, status, note) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(employee_id, work_date) DO UPDATE SET check_in=NULL, check_out=NULL, status=excluded.status",
                (employee_id, work_date, None, None, action, ""),
            )
        else:
            return see(back, request, "bad", t("no_results"))
    return see(back, request, "ok", t("attendance_saved"))


@app.post("/attendance/adjust")
async def attendance_adjust(request: Request):
    require(request, "attendance_adjust", write=True)
    form = await request.form()
    t, _, _ = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return see("/attendance", request, "bad", t("expired_body"))
    try:
        employee_id = int(form.get("employee_id") or 0)
        work_date = (form.get("work_date") or "").strip()
        datetime.strptime(work_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return see("/attendance", request, "bad", t("invalid_time"))
    check_in = (form.get("check_in") or "").strip()
    check_out = (form.get("check_out") or "").strip()
    if (check_in and not valid_time(check_in)) or (check_out and not valid_time(check_out)):
        return see(f"/attendance?work_date={work_date}", request, "bad", t("invalid_time"))
    if check_out and not check_in:
        return see(f"/attendance?work_date={work_date}", request, "bad", t("need_check_in"))
    if check_in and check_out and check_out < check_in:
        return see(f"/attendance?work_date={work_date}", request, "bad", t("invalid_time_order"))
    work_start = get_settings().get("work_start", "09:00")
    with db() as conn:
        employee = conn.execute(
            "SELECT id FROM employees WHERE id=?",
            (employee_id,),
        ).fetchone()
        if not employee:
            return see(f"/attendance?work_date={work_date}", request, "bad", t("no_results"))
        row = conn.execute(
            "SELECT status FROM attendance WHERE employee_id=? AND work_date=?",
            (employee_id, work_date),
        ).fetchone()
        if not row and not check_in:
            return see(f"/attendance?work_date={work_date}", request, "bad", t("no_results"))
        status = row["status"] if row else "absent"
        if check_in:
            status = "late" if check_in > work_start else "present"
        conn.execute(
            "INSERT INTO attendance (employee_id, work_date, check_in, check_out, status, note) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(employee_id, work_date) DO UPDATE SET "
            "check_in=excluded.check_in, check_out=excluded.check_out, status=excluded.status",
            (employee_id, work_date, check_in or None, check_out or None, status, ""),
        )
    return see(f"/attendance?work_date={work_date}", request, "ok", t("attendance_saved"))


@app.get("/attendance/report.csv")
def attendance_report_csv(request: Request, start: str = "", end: str = ""):
    require(request, "attendance")
    start_s, end_s = parse_range(start, end)
    with db() as conn:
        rows = conn.execute(
            "SELECT a.work_date, e.full_name, e.department, e.position, a.status, "
            "a.check_in, a.check_out, a.note "
            "FROM attendance a JOIN employees e ON e.id=a.employee_id "
            "WHERE a.work_date BETWEEN ? AND ? ORDER BY a.work_date, e.full_name",
            (start_s, end_s),
        ).fetchall()
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)
    writer.writerow(["work_date", "employee", "department", "position", "status", "check_in", "check_out", "note"])
    writer.writerows(rows)
    return Response(
        buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="attendance-{start_s}-to-{end_s}.csv"'},
    )


@app.get("/settings")
def settings_page(request: Request):
    require(request, "settings")
    t, _, settings = t_for()
    samples = {"USD": "$1,250.50", "EUR": "€1,250.50", "VND": "1,251 ₫", "KHR": "1,251 KHR"}
    return render(
        request,
        "settings.html",
        page_title=t("settings"),
        page_sub=t("settings_sub"),
        samples=samples,
        current=settings,
        languages=LANGUAGES,
    )


@app.post("/settings")
async def settings_save(request: Request):
    require(request, "settings", write=True)
    form = await request.form()
    t, _, _ = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return see("/settings", request, "bad", t("expired_body"))
    company = (form.get("company_name") or "").strip()
    if not company:
        return see("/settings", request, "bad", t("required"))
    try:
        tax = float(form.get("tax_rate") or 0)
    except ValueError:
        return see("/settings", request, "bad", t("invalid_number"))
    if tax < 0 or tax > 100:
        return see("/settings", request, "bad", t("invalid_number"))
    language = form.get("language") if form.get("language") in {code for code, _ in LANGUAGES} else "en"
    theme = form.get("theme") if form.get("theme") in ("light", "dark") else "light"
    currency = form.get("currency") if form.get("currency") in CURRENCIES else "USD"
    work_start = (form.get("work_start") or "").strip()
    work_end = (form.get("work_end") or "").strip()
    break_start = (form.get("break_start") or "").strip()
    break_end = (form.get("break_end") or "").strip()
    if not all(valid_time(value) for value in (work_start, work_end, break_start, break_end)):
        return see("/settings", request, "bad", t("invalid_time"))
    if not (work_start < work_end and break_start < break_end and
            work_start <= break_start and break_end <= work_end):
        return see("/settings", request, "bad", t("invalid_schedule"))
    values = {
        "company_name": company,
        "company_address": (form.get("company_address") or "").strip(),
        "company_phone": (form.get("company_phone") or "").strip(),
        "company_email": (form.get("company_email") or "").strip(),
        "tax_rate": fmt_input_number(tax),
        "language": language,
        "theme": theme,
        "currency": currency,
        "work_start": work_start,
        "work_end": work_end,
        "break_start": break_start,
        "break_end": break_end,
    }
    for key, value in values.items():
        set_setting(key, value)
    t, _, _ = t_for()
    return see("/settings", request, "ok", t("settings_saved"))


@app.get("/permissions")
def permissions_page(request: Request):
    require(request, "permissions")
    t, _, _ = t_for()
    roles = ("admin", "manager", "cashier", "staff")
    with db() as conn:
        saved = {
            (row["role"], row["perm"]): {
                "view": bool(row["can_view"]),
                "write": bool(row["can_write"]),
            }
            for row in conn.execute(
                "SELECT role, perm, can_view, can_write FROM role_permissions"
            ).fetchall()
        }
    groups = []
    for group, items in NAV:
        visible_items = []
        for key, _, icon in items:
            if key == "permissions":
                continue
            role_access = {}
            for role in roles:
                configured = saved.get((role, key))
                role_access[role] = {
                    "view": configured["view"] if configured else role in PERMS[key]["view"],
                    "write": configured["write"] if configured else role in PERMS[key]["write"],
                }
            visible_items.append({"key": key, "icon": icon, "access": role_access})
        if visible_items:
            groups.append({"group": group, "items": visible_items})
    return render(
        request,
        "permissions.html",
        page_title=t("permissions"),
        page_sub=t("permissions_sub"),
        permission_groups=groups,
        roles=roles,
    )


@app.post("/permissions")
async def permissions_save(request: Request):
    require(request, "permissions", write=True)
    form = await request.form()
    t, _, _ = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return see("/permissions", request, "bad", t("expired_body"))

    roles = ("manager", "cashier", "staff")
    permissions = {
        key
        for _, items in NAV
        for key, _, _ in items
        if key != "permissions"
    }
    with db() as conn:
        for role in roles:
            for perm in permissions:
                view = form.get(f"perm__{role}__{perm}__view") == "1"
                write = view and form.get(f"perm__{role}__{perm}__write") == "1"
                conn.execute(
                    "INSERT INTO role_permissions (role, perm, can_view, can_write) VALUES (?,?,?,?) "
                    "ON CONFLICT(role, perm) DO UPDATE SET "
                    "can_view=excluded.can_view, can_write=excluded.can_write",
                    (role, perm, int(view), int(write)),
                )
    return see("/permissions", request, "ok", t("permissions_saved"))


@app.get("/{entity}/new")
def entity_new(request: Request, entity: str):
    spec = ENTITIES.get(entity)
    if not spec:
        raise HTTPException(404)
    require(request, spec["perm"], write=True)
    t, _, _ = t_for()
    fields = fields_for(spec, True)
    return render(
        request,
        "form.html",
        page_title=t("new_record", name=t(spec["singular"])),
        entity=entity,
        fields=fields,
        values=blank_values(fields),
        errors={},
        options=load_options(spec, t),
        row_id=None,
        product_image=False,
        product_image_max_bytes=MAX_PRODUCT_IMAGE_BYTES,
    )


@app.get("/{entity}/{item_id}/edit")
def entity_edit(request: Request, entity: str, item_id: int):
    spec = ENTITIES.get(entity)
    if not spec:
        raise HTTPException(404)
    require(request, spec["perm"], write=True)
    t, _, _ = t_for()
    fields = fields_for(spec, False)
    with db() as conn:
        if entity == "products":
            query = (
                "SELECT id, name, sku, category_id, cost, price, unit, reorder_level, "
                "description, active, image_mime FROM products WHERE id=?"
            )
        else:
            query = f"SELECT * FROM {spec['table']} WHERE id=?"
        row = conn.execute(query, (item_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    return render(
        request,
        "form.html",
        page_title=t("edit_record", name=t(spec["singular"])),
        entity=entity,
        fields=fields,
        values=pack_values(row, fields),
        errors={},
        options=load_options(spec, t),
        row_id=item_id,
        product_image=bool(row["image_mime"]) if entity == "products" else False,
        product_image_max_bytes=MAX_PRODUCT_IMAGE_BYTES,
    )


@app.post("/{entity}/save")
async def entity_save(request: Request, entity: str):
    spec = ENTITIES.get(entity)
    if not spec:
        raise HTTPException(404)
    user = require(request, spec["perm"], write=True)
    form = await request.form()
    t, _, _ = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return see(f"/{entity}", request, "bad", t("expired_body"))
    raw_id = (form.get("id") or "").strip()
    item_id = int(raw_id) if raw_id.isdigit() else None
    fields = fields_for(spec, item_id is None)
    if entity == "users" and item_id is None and not (form.get("password") or "").strip():
        values, errors, cleaned = read_fields(fields, form, t)
        errors["password"] = t("required")
    else:
        values, errors, cleaned = read_fields(fields, form, t)
        if entity == "users" and item_id is None and not cleaned.get("password"):
            errors["password"] = t("required")
    old_product_image = False
    if entity == "products":
        if item_id:
            with db() as conn:
                row = conn.execute(
                    "SELECT image_mime FROM products WHERE id=?", (item_id,)
                ).fetchone()
            old_product_image = bool(row and row["image_mime"])
        upload = form.get("product_image")
        if isinstance(upload, UploadFile) and upload.filename:
            image_data = await upload.read(MAX_PRODUCT_IMAGE_BYTES + 1)
            await upload.close()
            if len(image_data) > MAX_PRODUCT_IMAGE_BYTES:
                errors["product_image"] = t("product_image_too_large")
            else:
                image_mime = product_image_mime(image_data)
                if image_mime is None:
                    errors["product_image"] = t("invalid_product_image")
                else:
                    cleaned["image_data"] = image_data
                    cleaned["image_mime"] = image_mime
        elif form.get("remove_product_image") == "1":
            cleaned["image_data"] = None
            cleaned["image_mime"] = None
    if errors:
        return render(
            request,
            "form.html",
            page_title=t("new_record" if item_id is None else "edit_record", name=t(spec["singular"])),
            entity=entity,
            fields=fields,
            values=values,
            errors=errors,
            options=load_options(spec, t),
            row_id=item_id,
            product_image=old_product_image,
            product_image_max_bytes=MAX_PRODUCT_IMAGE_BYTES,
        )
    try:
        with db() as conn:
            code, _new_id = persist_entity(conn, entity, spec, cleaned, item_id, user)
    except sqlite3.IntegrityError:
        code = "duplicate"
    messages = {
        "password": ("password", "required"),
        "password_short": ("password", "password_short"),
        "last_admin": ("role", "last_admin"),
        "username": ("username", "duplicate"),
        "sku": ("sku", "duplicate"),
        "category": ("category_id", "required"),
        "duplicate": ("name", "duplicate"),
    }
    if code == "missing":
        raise HTTPException(404)
    if code != "ok":
        field_name, msg_key = messages.get(code, ("name", "duplicate"))
        errors[field_name] = t(msg_key)
        return render(
            request,
            "form.html",
            page_title=t("new_record" if item_id is None else "edit_record", name=t(spec["singular"])),
            entity=entity,
            fields=fields,
            values=values,
            errors=errors,
            options=load_options(spec, t),
            row_id=item_id,
            product_image=old_product_image,
            product_image_max_bytes=MAX_PRODUCT_IMAGE_BYTES,
        )
    return see(f"/{entity}", request, "ok", t("saved"))


@app.post("/{entity}/{item_id}/delete")
async def entity_delete(request: Request, entity: str, item_id: int):
    spec = ENTITIES.get(entity)
    if not spec:
        raise HTTPException(404)
    user = require(request, spec["perm"], write=True)
    form = await request.form()
    t, _, _ = t_for()
    if not csrf_ok(request, form.get("csrf")):
        return see(f"/{entity}", request, "bad", t("expired_body"))
    table = spec["table"]
    with db() as conn:
        if entity == "users":
            if item_id == user["id"]:
                return see("/users", request, "bad", t("cannot_delete_self"))
            row = conn.execute("SELECT role, active FROM users WHERE id=?", (item_id,)).fetchone()
            if row and row["role"] == "admin" and row["active"]:
                others = conn.execute(
                    "SELECT COUNT(*) AS c FROM users WHERE role='admin' AND active=1 AND id!=?",
                    (item_id,),
                ).fetchone()["c"]
                if others == 0:
                    return see("/users", request, "bad", t("last_admin"))
            conn.execute("DELETE FROM users WHERE id=?", (item_id,))
        elif entity == "products":
            used = conn.execute("SELECT 1 FROM sale_items WHERE product_id=? LIMIT 1", (item_id,)).fetchone()
            used = used or conn.execute("SELECT 1 FROM purchase_items WHERE product_id=? LIMIT 1", (item_id,)).fetchone()
            if used:
                conn.execute("UPDATE products SET active=0 WHERE id=?", (item_id,))
                return see("/products", request, "ok", t("deactivated_instead"))
            conn.execute("DELETE FROM stock_moves WHERE product_id=?", (item_id,))
            conn.execute("DELETE FROM products WHERE id=?", (item_id,))
        elif entity == "categories":
            if conn.execute("SELECT 1 FROM products WHERE category_id=? LIMIT 1", (item_id,)).fetchone():
                return see("/categories", request, "bad", t("cannot_delete"))
            conn.execute("DELETE FROM categories WHERE id=?", (item_id,))
        elif entity == "customers":
            if conn.execute("SELECT 1 FROM sales WHERE customer_id=? LIMIT 1", (item_id,)).fetchone():
                return see("/customers", request, "bad", t("cannot_delete"))
            conn.execute("DELETE FROM customers WHERE id=?", (item_id,))
        elif entity == "suppliers":
            if conn.execute("SELECT 1 FROM purchase_orders WHERE supplier_id=? LIMIT 1", (item_id,)).fetchone():
                return see("/suppliers", request, "bad", t("cannot_delete"))
            conn.execute("DELETE FROM suppliers WHERE id=?", (item_id,))
        elif entity == "employees":
            if conn.execute("SELECT 1 FROM attendance WHERE employee_id=? LIMIT 1", (item_id,)).fetchone():
                return see("/employees", request, "bad", t("cannot_delete"))
            conn.execute("DELETE FROM employees WHERE id=?", (item_id,))
        else:
            conn.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))
    return see(f"/{entity}", request, "ok", t("deleted"))


@app.get("/products/{product_id}/image")
def product_image(request: Request, product_id: int):
    require(request, "products")
    with db() as conn:
        row = conn.execute(
            "SELECT image_data, image_mime FROM products WHERE id=?",
            (product_id,),
        ).fetchone()
    if not row or not row["image_data"] or row["image_mime"] not in {
        "image/jpeg",
        "image/png",
        "image/webp",
    }:
        raise HTTPException(404)
    return Response(
        content=row["image_data"],
        media_type=row["image_mime"],
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/catalogue")
def product_catalogue(request: Request):
    require(request, "products")
    t, _, settings = t_for()
    with db() as conn:
        products = [
            dict(row)
            for row in conn.execute(
                "SELECT p.id, p.name, p.sku, p.price, p.image_mime, c.name AS category_name "
                "FROM products p LEFT JOIN categories c ON c.id=p.category_id "
                "WHERE p.active=1 ORDER BY c.name COLLATE NOCASE, p.name COLLATE NOCASE"
            )
        ]
    return render(
        request,
        "catalogue.html",
        page_title=t("catalogue"),
        products=products,
        settings=settings,
    )


@app.get("/{entity}")
def entity_list(request: Request, entity: str, q: str = ""):
    spec = ENTITIES.get(entity)
    if not spec:
        raise HTTPException(404)
    user = require(request, spec["perm"])
    t, _, _ = t_for()
    with db() as conn:
        if entity == "products":
            query = (
                "SELECT id, sku, name, category_id, cost, price, stock, unit, "
                "reorder_level, description, image_mime, active, created_at FROM products "
                f"ORDER BY {spec['order']}"
            )
        else:
            query = f"SELECT * FROM {spec['table']} ORDER BY {spec['order']}"
        rows = [dict(row) for row in conn.execute(query)]
        if entity == "products":
            decorate_products(conn, rows)
    needle = q.strip().lower()
    if needle:
        rows = [
            row for row in rows
            if any(needle in str(row.get(field) or "").lower() for field in spec["search"])
        ]
    return render(
        request,
        "list.html",
        page_title=t(entity),
        entity=entity,
        columns=spec["columns"],
        rows=rows,
        q=q,
        can_write=allowed(user, spec["perm"], write=True),
    )


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, _exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(Forbidden)
async def _forbidden(request: Request, _exc: Forbidden):
    t, _, _ = t_for()
    return render(
        request,
        "message.html",
        status_code=403,
        title=t("forbidden_title"),
        message=t("forbidden_body"),
        href="/",
        action=t("go_dashboard"),
        page_title=t("forbidden_title"),
    )


@app.exception_handler(HTTPException)
async def _http(request: Request, exc: HTTPException):
    if exc.status_code == 404:
        t, _, _ = t_for()
        return render(
            request,
            "message.html",
            status_code=404,
            title=t("not_found_title"),
            message=t("not_found_body"),
            href="/" if get_user(request) else "/login",
            action=t("go_dashboard") if get_user(request) else t("sign_in"),
            page_title=t("not_found_title"),
        )
    return JSONResponse({"detail": str(exc.detail)}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def _boom(request: Request, exc: Exception):
    traceback.print_exc()
    t, _, _ = t_for()
    return render(
        request,
        "message.html",
        status_code=500,
        title=t("app_name"),
        message=t("not_found_body"),
        href="/",
        action=t("go_dashboard"),
        page_title=t("app_name"),
    )


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

init_db()
init_store_registry()
