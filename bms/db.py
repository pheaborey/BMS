"""SQLite database for BoreyBMS."""

from __future__ import annotations

import hashlib
import hmac
import os
import random
import secrets
import sqlite3
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=7))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  full_name TEXT NOT NULL,
  email TEXT,
  role TEXT NOT NULL CHECK (role IN ('admin','manager','cashier','staff')),
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS role_permissions (
  role TEXT NOT NULL CHECK (role IN ('manager','cashier','staff')),
  perm TEXT NOT NULL,
  can_view INTEGER NOT NULL DEFAULT 0 CHECK (can_view IN (0,1)),
  can_write INTEGER NOT NULL DEFAULT 0 CHECK (can_write IN (0,1)),
  PRIMARY KEY (role, perm)
);
CREATE TABLE IF NOT EXISTS employees (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  full_name TEXT NOT NULL,
  phone TEXT,
  email TEXT,
  department TEXT,
  position TEXT,
  salary REAL NOT NULL DEFAULT 0,
  hire_date TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','on_leave','inactive')),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  phone TEXT,
  email TEXT,
  address TEXT,
  company TEXT,
  notes TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  phone TEXT,
  email TEXT,
  address TEXT,
  company TEXT,
  notes TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT
);
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY,
  sku TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  category_id INTEGER REFERENCES categories(id),
  cost REAL NOT NULL DEFAULT 0,
  price REAL NOT NULL DEFAULT 0,
  stock REAL NOT NULL DEFAULT 0,
  unit TEXT NOT NULL DEFAULT 'pcs',
  reorder_level REAL NOT NULL DEFAULT 5,
  description TEXT,
  image_data BLOB,
  image_mime TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stock_moves (
  id INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES products(id),
  kind TEXT NOT NULL,
  qty REAL NOT NULL,
  balance_after REAL NOT NULL,
  ref TEXT,
  note TEXT,
  user_id INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchase_orders (
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL UNIQUE,
  supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  status TEXT NOT NULL CHECK (status IN ('ordered','received','cancelled')),
  order_date TEXT NOT NULL,
  notes TEXT,
  total REAL NOT NULL DEFAULT 0,
  user_id INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchase_items (
  id INTEGER PRIMARY KEY,
  po_id INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products(id),
  qty REAL NOT NULL,
  cost REAL NOT NULL,
  line_total REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sales (
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL UNIQUE,
  customer_id INTEGER REFERENCES customers(id),
  status TEXT NOT NULL CHECK (status IN ('paid','void')),
  sale_date TEXT NOT NULL,
  subtotal REAL NOT NULL,
  discount REAL NOT NULL DEFAULT 0,
  tax_rate REAL NOT NULL DEFAULT 0,
  tax REAL NOT NULL DEFAULT 0,
  total REAL NOT NULL,
  paid REAL NOT NULL DEFAULT 0,
  change_due REAL NOT NULL DEFAULT 0,
  payment_method TEXT NOT NULL,
  notes TEXT,
  user_id INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sale_items (
  id INTEGER PRIMARY KEY,
  sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products(id),
  name TEXT NOT NULL,
  qty REAL NOT NULL,
  price REAL NOT NULL,
  cost REAL NOT NULL,
  line_total REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS expenses (
  id INTEGER PRIMARY KEY,
  category TEXT NOT NULL,
  amount REAL NOT NULL,
  expense_date TEXT NOT NULL,
  note TEXT,
  user_id INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attendance (
  id INTEGER PRIMARY KEY,
  employee_id INTEGER NOT NULL REFERENCES employees(id),
  work_date TEXT NOT NULL,
  check_in TEXT,
  check_out TEXT,
  status TEXT NOT NULL CHECK (status IN ('present','late','absent','leave')),
  note TEXT,
  UNIQUE (employee_id, work_date)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(sale_date);
CREATE INDEX IF NOT EXISTS idx_moves_product ON stock_moves(product_id);
CREATE INDEX IF NOT EXISTS idx_att_date ON attendance(work_date);
"""

DEFAULTS = {
    "auth_generation": "0",
    "language": "en",
    "theme": "light",
    "currency": "USD",
    "company_name": "BoreyBMS",
    "company_address": "18 Harbor Lane, District 1",
    "company_phone": "+84 28 3910 4410",
    "company_email": "desk@meridian.example",
    "tax_rate": "8",
    "work_start": "09:00",
    "work_end": "18:00",
    "break_start": "12:00",
    "break_end": "13:00",
}


def bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


def _default_db_path() -> Path:
    configured_path = os.environ.get("BMS_DB")
    is_vercel = os.environ.get("VERCEL") == "1"
    mode = os.environ.get("BMS_MODE", "online" if is_vercel else "offline").strip().lower()
    if mode not in {"offline", "online"}:
        raise RuntimeError("BMS_MODE must be either 'offline' or 'online'.")
    if mode == "online" and not configured_path:
        raise RuntimeError("BMS_DB must point to persistent storage in online mode.")
    if configured_path:
        path = Path(configured_path)
        if mode == "online" and not path.is_absolute():
            raise RuntimeError("BMS_DB must be an absolute path in online mode.")
        return path
    data_dir = (
        Path(sys.executable).resolve().parent / "data"
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent / "data"
    )
    path = data_dir / "borey_bms.db"
    legacy_path = data_dir / "meridian.db"
    if not path.exists() and legacy_path.exists():
        legacy_path.replace(path)
    return path


DB_PATH = _default_db_path()
_ACTIVE_DB_PATH: ContextVar[Path | None] = ContextVar("bms_active_db_path", default=None)


@contextmanager
def use_database(path: Path):
    token = _ACTIVE_DB_PATH.set(Path(path))
    try:
        yield
    finally:
        _ACTIVE_DB_PATH.reset(token)


def now() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def today():
    return datetime.now(TZ).date()


def today_s() -> str:
    return today().isoformat()


def clock() -> str:
    return datetime.now(TZ).strftime("%H:%M")


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 80_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 80_000).hex()
    return hmac.compare_digest(check, digest)


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path is not None else (_ACTIVE_DB_PATH.get() or DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=20, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def get_settings() -> dict:
    data = dict(DEFAULTS)
    try:
        with db() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
    except sqlite3.OperationalError:
        return data
    data.update({row["key"]: row["value"] for row in rows})
    return data


def set_setting(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def adjust_stock(conn, product_id, delta, kind, ref, note, user_id, created_at=None):
    row = conn.execute("SELECT stock FROM products WHERE id=?", (product_id,)).fetchone()
    if not row:
        raise ValueError("missing product")
    new_bal = round(float(row["stock"]) + float(delta), 3)
    if new_bal < -0.0001:
        raise ValueError("insufficient")
    conn.execute("UPDATE products SET stock=? WHERE id=?", (new_bal, product_id))
    conn.execute(
        "INSERT INTO stock_moves (product_id, kind, qty, balance_after, ref, note, user_id, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (product_id, kind, float(delta), new_bal, ref, note or "", user_id, created_at or now()),
    )
    return new_bal


def next_number(conn, table: str, column: str, prefix: str) -> str:
    row = conn.execute(
        f"SELECT {column} AS n FROM {table} WHERE {column} LIKE ? ORDER BY id DESC LIMIT 1",
        (prefix + "-%",),
    ).fetchone()
    n = 1
    if row and row["n"]:
        try:
            n = int(str(row["n"]).rsplit("-", 1)[-1]) + 1
        except ValueError:
            n = 1
    return f"{prefix}-{n:05d}"


def next_code(conn, table: str, prefix: str) -> str:
    row = conn.execute(f"SELECT COALESCE(MAX(id), 0) AS c FROM {table}").fetchone()
    return f"{prefix}-{int(row['c']) + 1:04d}"


def _stamp(days_ago: int, hour: int = 9, minute: int = 0) -> str:
    day = today() - timedelta(days=days_ago)
    return f"{day.isoformat()} {hour:02d}:{minute:02d}:00"


def seed(conn) -> None:
    created = _stamp(40, 8, 0)
    for key, value in DEFAULTS.items():
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))

    users = [
        ("admin", "admin123", "Amina Khalid", "amina@meridian.example", "admin"),
        ("manager", "manager123", "Leo Park", "leo@meridian.example", "manager"),
        ("cashier", "cashier123", "Sofia Nguyen", "sofia@meridian.example", "cashier"),
        ("staff", "staff123", "Jonah Ike", "jonah@meridian.example", "staff"),
    ]
    for username, password, full_name, email, role in users:
        conn.execute(
            "INSERT INTO users (username, password_hash, full_name, email, role, active, created_at) "
            "VALUES (?,?,?,?,?,1,?)",
            (username, hash_password(password), full_name, email, role, created),
        )

    employees = [
        ("Elena Voss", "0901 220 118", "elena@meridian.example", "Warehouse", "Floor lead", 920, "2023-04-12", "active"),
        ("Marcus Hale", "0903 441 902", "marcus@meridian.example", "Sales", "Cashier", 780, "2024-01-08", "active"),
        ("Priya Shah", "0908 773 441", "priya@meridian.example", "Finance", "Accountant", 1100, "2022-11-02", "active"),
        ("Jonah Ike", "0912 550 017", "jonah@meridian.example", "Warehouse", "Associate", 640, "2025-06-19", "active"),
    ]
    for index, row in enumerate(employees, start=1):
        conn.execute(
            "INSERT INTO employees (code, full_name, phone, email, department, position, salary, hire_date, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"EMP-{index:04d}", *row, created),
        )

    customers = [
        ("Harbor Cafe", "0902 100 221", "orders@harborcafe.example", "Harbor Cafe", "12 Quay Street", "Weekly dry goods"),
        ("Lan Stationery", "0907 333 190", "lan@lanstat.example", "Lan Stationery", "4 School Road", ""),
        ("Green Basket", "0911 808 452", "hello@greenbasket.example", "Green Basket", "88 Market Alley", "Pays by transfer"),
        ("Minh Tran", "0909 121 334", "", "", "District 3", "Counter account"),
    ]
    for index, (name, phone, email, company, address, notes) in enumerate(customers, start=1):
        conn.execute(
            "INSERT INTO customers (code, name, phone, email, company, address, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"CUS-{index:04d}", name, phone, email, company, address, notes, created),
        )

    suppliers = [
        ("Mekong Staples", "028 3911 2200", "sales@mekongstaples.example", "Mekong Staples Co.", "Binh Duong", "Rice, oil, dry goods"),
        ("Saigon Pack", "028 3822 0199", "hello@saigonpack.example", "Saigon Pack", "Thu Duc", "Household and care"),
        ("North Glass & Hardware", "028 3744 8801", "desk@northglass.example", "North Glass", "District 7", "Tools and bulbs"),
    ]
    for index, row in enumerate(suppliers, start=1):
        conn.execute(
            "INSERT INTO suppliers (code, name, phone, email, company, address, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"SUP-{index:04d}", *row, created),
        )

    categories = [
        ("Pantry", "Dry goods and staples"),
        ("Beverages", "Drinks and brews"),
        ("Household", "Home essentials"),
        ("Personal Care", "Care and hygiene"),
        ("Stationery", "Office and school"),
        ("Hardware", "Tools and fixings"),
    ]
    for name, description in categories:
        conn.execute("INSERT INTO categories (name, description) VALUES (?, ?)", (name, description))
    cat_ids = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM categories")}

    products = [
        ("PAN-RICE-5", "Jasmine rice 5kg", "Pantry", 6.4, 9.9, 40, "bag", 10, "Broken rice blend, 5kg sack"),
        ("PAN-NOOD-1", "Rice noodles 1kg", "Pantry", 1.1, 2.2, 55, "pack", 12, ""),
        ("PAN-SUGAR-1", "Cane sugar 1kg", "Pantry", 0.9, 1.7, 30, "bag", 8, ""),
        ("PAN-SALT-5", "Sea salt 500g", "Pantry", 0.4, 1.1, 24, "bag", 6, ""),
        ("BEV-COFFEE", "Ground coffee 250g", "Beverages", 3.2, 6.5, 28, "bag", 8, "Medium roast"),
        ("BEV-TEA-20", "Green tea 20 bags", "Beverages", 1.4, 3.0, 34, "box", 8, ""),
        ("BEV-WATER", "Spring water 1.5L", "Beverages", 0.25, 0.8, 80, "btl", 24, ""),
        ("BEV-SOY", "Soy milk 1L", "Beverages", 0.7, 1.6, 22, "box", 8, ""),
        ("HOM-SOAP", "Laundry soap 1kg", "Household", 1.2, 2.4, 26, "bar", 8, ""),
        ("HOM-DETERG", "Dish liquid 750ml", "Household", 1.0, 2.3, 18, "btl", 6, ""),
        ("HOM-BAGS", "Trash bags 30pk", "Household", 1.3, 2.8, 20, "box", 6, ""),
        ("PER-SOAP", "Hand soap 250ml", "Personal Care", 0.9, 2.1, 32, "btl", 8, ""),
        ("PER-PASTE", "Toothpaste 120g", "Personal Care", 0.8, 1.9, 27, "tube", 8, ""),
        ("STA-NOTE", "A5 notebook", "Stationery", 0.6, 1.8, 40, "pcs", 10, "Ruled, soft cover"),
        ("STA-PEN", "Ballpoint pen 10pk", "Stationery", 0.7, 1.9, 20, "box", 8, ""),
        ("HRD-TAPE", "Packing tape 6pk", "Hardware", 1.5, 3.4, 16, "pack", 4, ""),
        ("HRD-BULB", "LED bulb 9W", "Hardware", 1.1, 2.6, 22, "pcs", 8, "Warm white"),
        ("HRD-GLUE", "Wood glue 250ml", "Hardware", 1.4, 3.1, 16, "btl", 6, ""),
    ]
    for sku, name, category, cost, price, stock, unit, reorder, description in products:
        cur = conn.execute(
            "INSERT INTO products (sku, name, category_id, cost, price, stock, unit, reorder_level, description, active, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,1,?)",
            (sku, name, cat_ids[category], cost, price, stock, unit, reorder, description, created),
        )
        pid = cur.lastrowid
        conn.execute(
            "INSERT INTO stock_moves (product_id, kind, qty, balance_after, ref, note, user_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (pid, "opening", stock, stock, "OPEN", "Opening balance", 1, created),
        )

    mekong = conn.execute("SELECT id FROM suppliers WHERE name=?", ("Mekong Staples",)).fetchone()["id"]
    rice = conn.execute("SELECT id, cost FROM products WHERE sku='PAN-RICE-5'").fetchone()
    oil_like = conn.execute("SELECT id, cost FROM products WHERE sku='PAN-SUGAR-1'").fetchone()
    po_date = (today() - timedelta(days=26)).isoformat()
    po_no = next_number(conn, "purchase_orders", "number", "PO")
    lines = [(rice["id"], 20, float(rice["cost"])), (oil_like["id"], 12, float(oil_like["cost"]))]
    total = round(sum(qty * cost for _, qty, cost in lines), 2)
    po_id = conn.execute(
        "INSERT INTO purchase_orders (number, supplier_id, status, order_date, notes, total, user_id, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (po_no, mekong, "received", po_date, "Restock before the cafe contract", total, 2, _stamp(26, 10, 15)),
    ).lastrowid
    for pid, qty, cost in lines:
        conn.execute(
            "INSERT INTO purchase_items (po_id, product_id, qty, cost, line_total) VALUES (?,?,?,?,?)",
            (po_id, pid, qty, cost, round(qty * cost, 2)),
        )
        adjust_stock(conn, pid, qty, "purchase", po_no, "Goods received", 2, _stamp(26, 15, 0))

    prows = [dict(row) for row in conn.execute("SELECT id, price, cost, stock, name FROM products")]
    for prow in prows:
        prow["stock"] = float(prow["stock"])
    customer_ids = [row["id"] for row in conn.execute("SELECT id FROM customers")]
    rnd = random.Random(42)
    for days_ago in range(20, -1, -1):
        day = today() - timedelta(days=days_ago)
        tickets = rnd.randint(3, 6) if day.weekday() < 5 else rnd.randint(1, 3)
        for _ in range(tickets):
            available = [p for p in prows if p["stock"] >= 1]
            if not available:
                break
            chosen = rnd.sample(available, k=min(len(available), rnd.randint(1, 3)))
            lines = []
            for prow in chosen:
                qty = rnd.randint(1, min(3, int(prow["stock"])))
                if qty < 1:
                    continue
                prow["stock"] -= qty
                lines.append((prow, qty))
            if not lines:
                continue
            subtotal = round(sum(p["price"] * qty for p, qty in lines), 2)
            discount = 0.5 if rnd.random() < 0.12 and subtotal > 2 else 0
            tax_rate = 8.0
            tax = round((subtotal - discount) * tax_rate / 100, 2)
            total = round(subtotal - discount + tax, 2)
            method = rnd.choice(["cash", "cash", "card", "transfer"])
            if method == "cash":
                paid = round(total + rnd.choice([0, 0, 0, 1, 5]), 2)
                change = round(paid - total, 2)
            else:
                paid = total
                change = 0
            customer_id = rnd.choice(customer_ids + [None, None, None])
            number = next_number(conn, "sales", "number", "INV")
            hour = rnd.randint(8, 18)
            minute = rnd.randint(0, 59)
            created_at = f"{day.isoformat()} {hour:02d}:{minute:02d}:00"
            sale_id = conn.execute(
                "INSERT INTO sales (number, customer_id, status, sale_date, subtotal, discount, tax_rate, tax, total, paid, change_due, payment_method, notes, user_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (number, customer_id, "paid", day.isoformat(), subtotal, discount, tax_rate, tax, total, paid, change, method, "", 3, created_at),
            ).lastrowid
            for prow, qty in lines:
                line_total = round(prow["price"] * qty, 2)
                conn.execute(
                    "INSERT INTO sale_items (sale_id, product_id, name, qty, price, cost, line_total) VALUES (?,?,?,?,?,?,?)",
                    (sale_id, prow["id"], prow["name"], qty, prow["price"], prow["cost"], line_total),
                )
                adjust_stock(conn, prow["id"], -qty, "sale", number, "", 3, created_at)

    for sku, leave in (("HRD-BULB", 2), ("HRD-GLUE", 1), ("STA-PEN", 3)):
        row = conn.execute("SELECT id, stock FROM products WHERE sku=?", (sku,)).fetchone()
        extra = float(row["stock"]) - leave
        if extra >= 1:
            adjust_stock(conn, row["id"], -int(extra), "out", "COUNT", "Cycle count to shelf", 4, now())

    hardware = conn.execute("SELECT id FROM suppliers WHERE name LIKE 'North Glass%'").fetchone()["id"]
    bulb = conn.execute("SELECT id, cost FROM products WHERE sku='HRD-BULB'").fetchone()
    tape = conn.execute("SELECT id, cost FROM products WHERE sku='HRD-TAPE'").fetchone()
    open_lines = [(bulb["id"], 24, float(bulb["cost"])), (tape["id"], 10, float(tape["cost"]))]
    open_total = round(sum(q * c for _, q, c in open_lines), 2)
    open_no = next_number(conn, "purchase_orders", "number", "PO")
    open_id = conn.execute(
        "INSERT INTO purchase_orders (number, supplier_id, status, order_date, notes, total, user_id, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (open_no, hardware, "ordered", today_s(), "Waiting on the Thursday truck", open_total, 2, now()),
    ).lastrowid
    for pid, qty, cost in open_lines:
        conn.execute(
            "INSERT INTO purchase_items (po_id, product_id, qty, cost, line_total) VALUES (?,?,?,?,?)",
            (open_id, pid, qty, cost, round(qty * cost, 2)),
        )

    month_start = today().replace(day=1)
    expense_rows = [
        ("rent", 1800, 0, "Harbor Lane shop rent"),
        ("wages", 3440, 0, "Monthly wage accrual"),
        ("utilities", 240, 2, "Electric and water"),
        ("transport", 95, 4, "Supplier pickup"),
        ("supplies", 60, 6, "Till rolls and bags"),
        ("maintenance", 120, 9, "Shelf repair"),
        ("other", 35, 12, "Market permit stamp"),
    ]
    for category, amount, offset, note in expense_rows:
        day = month_start + timedelta(days=offset)
        if day > today():
            day = today()
        conn.execute(
            "INSERT INTO expenses (category, amount, expense_date, note, user_id, created_at) VALUES (?,?,?,?,?,?)",
            (category, amount, day.isoformat(), note, 2, f"{day.isoformat()} 09:30:00"),
        )

    emp_ids = [row["id"] for row in conn.execute("SELECT id FROM employees ORDER BY id")]
    yesterday = (today() - timedelta(days=1)).isoformat()
    for emp_id in emp_ids:
        conn.execute(
            "INSERT INTO attendance (employee_id, work_date, check_in, check_out, status, note) VALUES (?,?,?,?,?,?)",
            (emp_id, yesterday, "08:10", "17:05", "present", ""),
        )
    conn.execute(
        "INSERT INTO attendance (employee_id, work_date, check_in, check_out, status, note) VALUES (?,?,?,?,?,?)",
        (emp_ids[0], today_s(), "08:05", None, "present", ""),
    )
    conn.execute(
        "INSERT INTO attendance (employee_id, work_date, check_in, check_out, status, note) VALUES (?,?,?,?,?,?)",
        (emp_ids[1], today_s(), "09:22", None, "late", ""),
    )
    conn.execute(
        "INSERT INTO attendance (employee_id, work_date, check_in, check_out, status, note) VALUES (?,?,?,?,?,?)",
        (emp_ids[2], today_s(), None, None, "leave", "Clinic"),
    )


def init_db(
    path: Path | None = None,
    *,
    seed_data: bool = True,
    admin: dict | None = None,
    company_name: str | None = None,
) -> None:
    if not seed_data and not admin:
        raise ValueError("An administrator is required for a new store database.")
    db_path = Path(path) if path is not None else (_ACTIVE_DB_PATH.get() or DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        product_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(products)")
        }
        if "image_data" not in product_columns:
            conn.execute("ALTER TABLE products ADD COLUMN image_data BLOB")
        if "image_mime" not in product_columns:
            conn.execute("ALTER TABLE products ADD COLUMN image_mime TEXT")
        count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if count == 0:
            conn.execute("BEGIN")
            if seed_data:
                seed(conn)
            else:
                settings = dict(DEFAULTS)
                if company_name:
                    settings["company_name"] = company_name
                settings["company_address"] = ""
                settings["company_phone"] = ""
                settings["company_email"] = ""
                conn.executemany(
                    "INSERT INTO settings (key, value) VALUES (?, ?)",
                    settings.items(),
                )
                if admin:
                    conn.execute(
                        "INSERT INTO users (id, username, password_hash, full_name, email, role, active, created_at) "
                        "VALUES (?,?,?,?,?,'admin',1,?)",
                        (
                            admin["id"],
                            admin["username"],
                            admin["password_hash"],
                            admin["full_name"],
                            admin.get("email"),
                            now(),
                        ),
                    )
            conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()
