from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "app.db"
ASSETS_DIR = ROOT / "assets"


def find_import_dir() -> Path:
    root_import = ROOT / "import"
    if root_import.exists() and root_import.is_dir():
        return root_import
    for p in ROOT.rglob("import"):
        if p.is_dir():
            return p
    raise FileNotFoundError("Не найдена папка import в проекте.")


def schema_sql() -> str:
    return """
    PRAGMA foreign_keys = ON;

    DROP TABLE IF EXISTS order_items;
    DROP TABLE IF EXISTS orders;
    DROP TABLE IF EXISTS products;
    DROP TABLE IF EXISTS suppliers;
    DROP TABLE IF EXISTS manufacturers;
    DROP TABLE IF EXISTS categories;
    DROP TABLE IF EXISTS users;
    DROP TABLE IF EXISTS roles;
    DROP TABLE IF EXISTS pickup_points;

    CREATE TABLE roles (
        id INTEGER PRIMARY KEY,
        role_name TEXT NOT NULL UNIQUE
    );

    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        role_id INTEGER NOT NULL,
        FOREIGN KEY (role_id) REFERENCES roles (id)
    );

    CREATE TABLE categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_name TEXT NOT NULL UNIQUE
    );

    CREATE TABLE manufacturers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        manufacturer_name TEXT NOT NULL UNIQUE
    );

    CREATE TABLE suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier_name TEXT NOT NULL UNIQUE
    );

    CREATE TABLE products (
        article TEXT PRIMARY KEY,
        product_name TEXT NOT NULL,
        category_id INTEGER NOT NULL,
        description TEXT NOT NULL,
        manufacturer_id INTEGER NOT NULL,
        supplier_id INTEGER NOT NULL,
        base_price REAL NOT NULL CHECK (base_price >= 0),
        stock_count INTEGER NOT NULL CHECK (stock_count >= 0),
        discount_percent INTEGER NOT NULL CHECK (discount_percent >= 0 AND discount_percent <= 100),
        image_path TEXT,
        FOREIGN KEY (category_id) REFERENCES categories (id),
        FOREIGN KEY (manufacturer_id) REFERENCES manufacturers (id),
        FOREIGN KEY (supplier_id) REFERENCES suppliers (id)
    );

    CREATE TABLE pickup_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        address_text TEXT NOT NULL UNIQUE
    );

    CREATE TABLE orders (
        id INTEGER PRIMARY KEY,
        order_date TEXT NOT NULL,
        delivery_date TEXT NOT NULL,
        pickup_point_id INTEGER NOT NULL,
        client_name TEXT NOT NULL,
        receive_code TEXT,
        order_status TEXT NOT NULL,
        FOREIGN KEY (pickup_point_id) REFERENCES pickup_points (id)
    );

    CREATE TABLE order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        product_article TEXT NOT NULL,
        quantity INTEGER NOT NULL CHECK (quantity > 0),
        FOREIGN KEY (order_id) REFERENCES orders (id),
        FOREIGN KEY (product_article) REFERENCES products (article)
    );
    """


def read_xlsx(path: Path) -> pd.DataFrame:
    return pd.read_excel(path, header=0)


def pick_import_file(import_dir: Path, include_keyword: str, exclude_keywords: tuple[str, ...] = ()) -> Path:
    include = include_keyword.casefold()
    excluded = tuple(word.casefold() for word in exclude_keywords)
    candidates = sorted(import_dir.glob("*.xlsx"))
    for file_path in candidates:
        name = file_path.name.casefold()
        if include not in name:
            continue
        if any(word in name for word in excluded):
            continue
        return file_path
    raise FileNotFoundError(f"Не найден файл импорта по ключу: {include_keyword}")


def get_col(row: pd.Series, idx: int, default: Any = "") -> Any:
    if idx >= len(row.index):
        return default
    value = row.iloc[idx]
    if pd.isna(value):
        return default
    return value


def build_db() -> None:
    import_dir = find_import_dir()
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    placeholder = import_dir / "picture.png"
    if placeholder.exists():
        (ASSETS_DIR / "picture.png").write_bytes(placeholder.read_bytes())
    for image_file in import_dir.glob("*"):
        if image_file.suffix.lower() in (".jpg", ".jpeg", ".png", ".ico"):
            (ASSETS_DIR / image_file.name).write_bytes(image_file.read_bytes())

    product_file = pick_import_file(import_dir, "tovar")
    user_file = pick_import_file(import_dir, "user_import")
    order_file = pick_import_file(
        import_dir,
        "заказ",
        exclude_keywords=("user", "tovar", "пункт", "выдач"),
    )
    pickup_file = next(import_dir.glob("*пункт*выдач*_import.xlsx"), None)

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(schema_sql())
    cur = conn.cursor()

    roles = [(1, "guest"), (2, "client"), (3, "manager"), (4, "admin")]
    cur.executemany("INSERT INTO roles(id, role_name) VALUES(?, ?)", roles)

    user_df = read_xlsx(user_file)
    for _, row in user_df.iterrows():
        role_name = str(get_col(row, 0)).strip().lower()
        full_name = str(get_col(row, 1)).strip()
        email = str(get_col(row, 2)).strip()
        password = str(get_col(row, 3)).strip()
        role_map = {"клиент": 2, "менеджер": 3, "администратор": 4}
        role_id = role_map.get(role_name, 2)
        if not email:
            continue
        cur.execute(
            "INSERT OR IGNORE INTO users(full_name, email, password, role_id) VALUES(?, ?, ?, ?)",
            (full_name, email, password, role_id),
        )

    cur.execute(
        "INSERT INTO users(full_name, email, password, role_id) VALUES(?, ?, ?, ?)",
        ("Гостевой доступ", "guest@local", "guest", 1),
    )

    product_df = read_xlsx(product_file)
    for _, row in product_df.iterrows():
        article = str(get_col(row, 0)).strip()
        product_name = str(get_col(row, 1)).strip()
        category = str(get_col(row, 6)).strip()
        price = float(get_col(row, 3, 0))
        description = str(get_col(row, 9)).strip()
        manufacturer = str(get_col(row, 4)).strip()
        supplier = str(get_col(row, 5)).strip()
        stock = int(float(get_col(row, 7, 0)))
        discount = int(float(get_col(row, 8, 0)))
        raw_image_name = str(get_col(row, 10)).strip()
        image_name = "" if raw_image_name.lower() == "nan" else raw_image_name

        cur.execute("INSERT OR IGNORE INTO categories(category_name) VALUES(?)", (category,))
        cur.execute(
            "INSERT OR IGNORE INTO manufacturers(manufacturer_name) VALUES(?)",
            (manufacturer,),
        )
        cur.execute("INSERT OR IGNORE INTO suppliers(supplier_name) VALUES(?)", (supplier,))
        cur.execute("SELECT id FROM categories WHERE category_name = ?", (category,))
        category_id = cur.fetchone()[0]
        cur.execute(
            "SELECT id FROM manufacturers WHERE manufacturer_name = ?",
            (manufacturer,),
        )
        manufacturer_id = cur.fetchone()[0]
        cur.execute("SELECT id FROM suppliers WHERE supplier_name = ?", (supplier,))
        supplier_id = cur.fetchone()[0]

        image_path = None
        if image_name:
            for candidate in import_dir.glob("*"):
                if candidate.name.lower() == image_name.lower():
                    target = ASSETS_DIR / candidate.name
                    target.write_bytes(candidate.read_bytes())
                    image_path = str(target.relative_to(ROOT))
                    break

        cur.execute(
            """
            INSERT INTO products(
                article, product_name, category_id, description, manufacturer_id,
                supplier_id, base_price, stock_count, discount_percent, image_path
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article,
                product_name,
                category_id,
                description,
                manufacturer_id,
                supplier_id,
                price,
                stock,
                discount,
                image_path,
            ),
        )

    if pickup_file and pickup_file.exists():
        pickup_df = read_xlsx(pickup_file)
        for _, row in pickup_df.iterrows():
            address = str(get_col(row, 0)).strip()
            if address:
                cur.execute(
                    "INSERT OR IGNORE INTO pickup_points(address_text) VALUES(?)",
                    (address,),
                )

    order_df = read_xlsx(order_file)
    cur.execute(
        "INSERT OR IGNORE INTO pickup_points(id, address_text) VALUES(1, ?)",
        ("Пункт выдачи не указан",),
    )
    for _, row in order_df.iterrows():
        order_id = int(float(get_col(row, 0, 0)))
        order_lines = str(get_col(row, 1))
        order_date = str(get_col(row, 2)).split(" ")[0]
        delivery_date = str(get_col(row, 3)).split(" ")[0]
        pickup_id = int(float(get_col(row, 4, 1)))
        client_name = str(get_col(row, 5))
        receive_code = str(get_col(row, 6))
        status = str(get_col(row, 7))
        pickup_exists = cur.execute(
            "SELECT 1 FROM pickup_points WHERE id = ?",
            (pickup_id,),
        ).fetchone()
        if pickup_exists is None:
            pickup_id = 1

        cur.execute(
            """
            INSERT INTO orders(
                id, order_date, delivery_date, pickup_point_id,
                client_name, receive_code, order_status
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                order_date,
                delivery_date,
                pickup_id,
                client_name,
                receive_code,
                status,
            ),
        )
        parts = [p.strip() for p in order_lines.split(",") if p.strip()]
        for i in range(0, len(parts), 2):
            if i + 1 >= len(parts):
                continue
            article = parts[i]
            quantity = int(float(parts[i + 1]))
            cur.execute(
                "INSERT INTO order_items(order_id, product_article, quantity) VALUES(?, ?, ?)",
                (order_id, article, quantity),
            )

    conn.commit()
    conn.close()
    print(f"База данных создана: {DB_PATH}")


if __name__ == "__main__":
    build_db()
