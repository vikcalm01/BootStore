from __future__ import annotations

import sqlite3
from functools import wraps
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "app.db"
ASSETS_DIR = ROOT / "assets"
DEFAULT_IMAGE = "picture.png"

app = Flask(
    __name__,
    template_folder=str(ROOT / "templates"),
    static_folder=str(ROOT / "static"),
)
app.config["SECRET_KEY"] = "prac1-secret-key"


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_error: Exception | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def role_required(*roles: str):
    def decorator(func):
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any):
            role = session.get("role", "guest")
            if role not in roles:
                flash("Недостаточно прав для выполнения действия.", "error")
                return redirect(url_for("products"))
            return func(*args, **kwargs)

        return wrapper

    return decorator


@app.get("/")
def index():
    return redirect(url_for("login"))


@app.get("/assets/<path:filename>")
def assets(filename: str):
    return send_from_directory(ASSETS_DIR, filename)


@app.get("/manager")
@role_required("manager", "admin")
def manager_page():
    return redirect(url_for("products"))


@app.get("/admin")
@role_required("admin")
def admin_page():
    return redirect(url_for("products"))


def _normalize_image(image_path: str | None) -> str:
    return Path(str(image_path)).name if image_path else DEFAULT_IMAGE


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("guest") == "1":
            session.clear()
            session["user_id"] = 0
            session["full_name"] = "Гость"
            session["role"] = "guest"
            return redirect(url_for("products"))

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        row = get_db().execute(
            """
            SELECT u.id, u.full_name, r.role_name
            FROM users u JOIN roles r ON r.id = u.role_id
            WHERE u.email = ? AND u.password = ?
            """,
            (email, password),
        ).fetchone()
        if row is None:
            flash("Неверный логин или пароль.", "error")
            return render_template("login.html")

        session.clear()
        session["user_id"] = row["id"]
        session["full_name"] = row["full_name"]
        session["role"] = row["role_name"]
        return redirect(url_for("products"))

    return render_template("login.html")


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/products")
def products():
    role = session.get("role", "guest")
    q = request.args.get("q", "").strip()
    supplier = request.args.get("supplier", "Все поставщики")
    stock_sort = request.args.get("sort", "none")

    sql = """
    SELECT
        p.article, p.product_name, c.category_name, p.description,
        m.manufacturer_name, s.supplier_name, p.base_price,
        p.discount_percent, p.stock_count, p.image_path
    FROM products p
    JOIN categories c ON c.id = p.category_id
    JOIN manufacturers m ON m.id = p.manufacturer_id
    JOIN suppliers s ON s.id = p.supplier_id
    """
    where = []
    params: list[str] = []

    if role in ("manager", "admin"):
        if supplier and supplier != "Все поставщики":
            where.append("s.supplier_name = ?")
            params.append(supplier)
        if q:
            term = f"%{q}%"
            where.append(
                """(
                p.article LIKE ? OR p.product_name LIKE ? OR c.category_name LIKE ?
                OR p.description LIKE ? OR m.manufacturer_name LIKE ? OR s.supplier_name LIKE ?
                )"""
            )
            params.extend([term, term, term, term, term, term])

    if where:
        sql += " WHERE " + " AND ".join(where)

    if role in ("manager", "admin"):
        if stock_sort == "asc":
            sql += " ORDER BY p.stock_count ASC"
        elif stock_sort == "desc":
            sql += " ORDER BY p.stock_count DESC"

    rows = get_db().execute(sql, params).fetchall()
    normalized_rows = []
    for row in rows:
        item = dict(row)
        item["image_file"] = _normalize_image(item.get("image_path"))
        normalized_rows.append(item)
    suppliers = get_db().execute(
        "SELECT supplier_name FROM suppliers ORDER BY supplier_name"
    ).fetchall()
    return render_template(
        "products.html",
        role=role,
        full_name=session.get("full_name", "Гость"),
        products=normalized_rows,
        suppliers=suppliers,
        q=q,
        supplier=supplier,
        stock_sort=stock_sort,
    )


def _save_product(current_article: str | None):
    db = get_db()
    article = request.form.get("article", "").strip()
    product_name = request.form.get("product_name", "").strip()
    category = request.form.get("category", "").strip()
    description = request.form.get("description", "").strip()
    manufacturer = request.form.get("manufacturer", "").strip()
    supplier = request.form.get("supplier", "").strip()
    try:
        base_price = float(request.form.get("base_price", "0").strip())
        stock_count = int(request.form.get("stock_count", "0").strip())
        discount = int(request.form.get("discount_percent", "0").strip())
    except ValueError:
        flash("Неверный формат числовых полей.", "error")
        return redirect(request.url)

    if base_price < 0 or stock_count < 0 or discount < 0:
        flash("Цена, количество и скидка не могут быть отрицательными.", "error")
        return redirect(request.url)

    db.execute("INSERT OR IGNORE INTO categories(category_name) VALUES(?)", (category,))
    db.execute("INSERT OR IGNORE INTO manufacturers(manufacturer_name) VALUES(?)", (manufacturer,))
    db.execute("INSERT OR IGNORE INTO suppliers(supplier_name) VALUES(?)", (supplier,))

    category_id = db.execute(
        "SELECT id FROM categories WHERE category_name = ?",
        (category,),
    ).fetchone()["id"]
    manufacturer_id = db.execute(
        "SELECT id FROM manufacturers WHERE manufacturer_name = ?",
        (manufacturer,),
    ).fetchone()["id"]
    supplier_id = db.execute(
        "SELECT id FROM suppliers WHERE supplier_name = ?",
        (supplier,),
    ).fetchone()["id"]

    try:
        if current_article is None:
            db.execute(
                """
                INSERT INTO products(
                    article, product_name, category_id, description, manufacturer_id,
                    supplier_id, base_price, stock_count, discount_percent, image_path
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    article,
                    product_name,
                    category_id,
                    description,
                    manufacturer_id,
                    supplier_id,
                    base_price,
                    stock_count,
                    discount,
                ),
            )
            flash("Товар добавлен.", "info")
        else:
            db.execute(
                """
                UPDATE products
                SET article = ?, product_name = ?, category_id = ?, description = ?,
                    manufacturer_id = ?, supplier_id = ?, base_price = ?, stock_count = ?,
                    discount_percent = ?
                WHERE article = ?
                """,
                (
                    article,
                    product_name,
                    category_id,
                    description,
                    manufacturer_id,
                    supplier_id,
                    base_price,
                    stock_count,
                    discount,
                    current_article,
                ),
            )
            flash("Товар обновлен.", "info")
        db.commit()
    except sqlite3.IntegrityError:
        flash(
            "Ошибка сохранения товара: проверьте уникальность артикула и связанные данные.",
            "error",
        )
        db.rollback()
    return redirect(url_for("products"))


@app.route("/products/new", methods=["GET", "POST"])
@role_required("admin")
def product_create():
    if request.method == "POST":
        return _save_product(None)
    return render_template("product_form.html", title="Добавление товара", item=None)


@app.route("/products/<string:article>/edit", methods=["GET", "POST"])
@role_required("admin")
def product_edit(article: str):
    db = get_db()
    item = db.execute(
        """
        SELECT
            p.article, p.product_name, c.category_name, p.description,
            m.manufacturer_name, s.supplier_name, p.base_price,
            p.stock_count, p.discount_percent
        FROM products p
        JOIN categories c ON c.id = p.category_id
        JOIN manufacturers m ON m.id = p.manufacturer_id
        JOIN suppliers s ON s.id = p.supplier_id
        WHERE p.article = ?
        """,
        (article,),
    ).fetchone()
    if item is None:
        flash("Товар не найден.", "error")
        return redirect(url_for("products"))
    if request.method == "POST":
        return _save_product(article)
    return render_template("product_form.html", title="Редактирование товара", item=item)


@app.post("/products/<string:article>/delete")
@role_required("admin")
def product_delete(article: str):
    db = get_db()
    exists = db.execute(
        "SELECT 1 FROM order_items WHERE product_article = ? LIMIT 1",
        (article,),
    ).fetchone()
    if exists:
        flash("Нельзя удалить товар: он используется в заказе.", "error")
        return redirect(url_for("products"))
    db.execute("DELETE FROM products WHERE article = ?", (article,))
    db.commit()
    flash("Товар удален.", "info")
    return redirect(url_for("products"))


def _parse_order_id(raw: str, db: sqlite3.Connection) -> int:
    if raw.strip():
        return int(raw.strip())
    row = db.execute("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM orders").fetchone()
    return int(row["next_id"])


def _save_order(current_id: int | None):
    db = get_db()
    order_id_raw = request.form.get("id", "")
    order_date = request.form.get("order_date", "").strip()
    delivery_date = request.form.get("delivery_date", "").strip()
    status = request.form.get("order_status", "").strip()
    pickup_address = request.form.get("pickup_address", "").strip()
    client_name = request.form.get("client_name", "").strip()
    receive_code = request.form.get("receive_code", "").strip()
    article = request.form.get("article", "").strip()
    quantity_raw = request.form.get("quantity", "1").strip()

    if not (order_date and delivery_date and status and pickup_address and client_name and article):
        flash("Заполните обязательные поля заказа.", "error")
        return redirect(request.url)
    try:
        quantity = int(quantity_raw)
        if quantity <= 0:
            raise ValueError
        order_id = _parse_order_id(order_id_raw, db)
    except ValueError:
        flash("ID и количество должны быть положительными числами.", "error")
        return redirect(request.url)

    product_exists = db.execute(
        "SELECT 1 FROM products WHERE article = ?",
        (article,),
    ).fetchone()
    if not product_exists:
        flash("Товар с таким артикулом не найден.", "error")
        return redirect(request.url)

    db.execute(
        "INSERT OR IGNORE INTO pickup_points(address_text) VALUES(?)",
        (pickup_address,),
    )
    pickup_row = db.execute(
        "SELECT id FROM pickup_points WHERE address_text = ?",
        (pickup_address,),
    ).fetchone()
    pickup_id = int(pickup_row["id"])

    try:
        if current_id is None:
            db.execute(
                """
                INSERT INTO orders(
                    id, order_date, delivery_date, pickup_point_id,
                    client_name, receive_code, order_status
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (order_id, order_date, delivery_date, pickup_id, client_name, receive_code, status),
            )
            db.execute(
                "INSERT INTO order_items(order_id, product_article, quantity) VALUES(?, ?, ?)",
                (order_id, article, quantity),
            )
            flash("Заказ добавлен.", "info")
        else:
            # Удаляем позиции старого заказа перед обновлением, чтобы корректно менять ID.
            db.execute("DELETE FROM order_items WHERE order_id = ?", (current_id,))
            db.execute(
                """
                UPDATE orders
                SET id = ?, order_date = ?, delivery_date = ?, pickup_point_id = ?,
                    client_name = ?, receive_code = ?, order_status = ?
                WHERE id = ?
                """,
                (
                    order_id,
                    order_date,
                    delivery_date,
                    pickup_id,
                    client_name,
                    receive_code,
                    status,
                    current_id,
                ),
            )
            db.execute(
                "INSERT INTO order_items(order_id, product_article, quantity) VALUES(?, ?, ?)",
                (order_id, article, quantity),
            )
            flash("Заказ обновлен.", "info")
        db.commit()
    except sqlite3.IntegrityError:
        flash("Ошибка сохранения заказа: проверьте ID заказа и артикул товара.", "error")
        db.rollback()
    return redirect(url_for("orders"))


@app.get("/orders")
@role_required("manager", "admin")
def orders():
    rows = get_db().execute(
        """
        SELECT
            o.id, o.order_date, o.delivery_date, o.order_status, p.address_text,
            o.client_name, o.receive_code
        FROM orders o JOIN pickup_points p ON p.id = o.pickup_point_id
        ORDER BY o.id
        """
    ).fetchall()
    return render_template(
        "orders.html",
        rows=rows,
        role=session.get("role", "guest"),
    )


@app.route("/orders/new", methods=["GET", "POST"])
@role_required("admin")
def order_create():
    if request.method == "POST":
        return _save_order(None)
    return render_template("order_form.html", title="Добавление заказа", item=None)


@app.route("/orders/<int:order_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def order_edit(order_id: int):
    db = get_db()
    item = db.execute(
        """
        SELECT
            o.id, o.order_date, o.delivery_date, o.order_status, p.address_text,
            o.client_name, o.receive_code, oi.product_article, oi.quantity
        FROM orders o
        JOIN pickup_points p ON p.id = o.pickup_point_id
        LEFT JOIN order_items oi ON oi.order_id = o.id
        WHERE o.id = ?
        ORDER BY oi.id
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()
    if item is None:
        flash("Заказ не найден.", "error")
        return redirect(url_for("orders"))
    if request.method == "POST":
        return _save_order(order_id)
    return render_template("order_form.html", title="Редактирование заказа", item=item)


@app.post("/orders/<int:order_id>/delete")
@role_required("admin")
def order_delete(order_id: int):
    db = get_db()
    db.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
    db.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    db.commit()
    flash("Заказ удален.", "info")
    return redirect(url_for("orders"))


if __name__ == "__main__":
    app.run(debug=True)
