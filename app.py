import os
import sqlite3
from pathlib import Path
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, g, flash, jsonify
)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "apna_cart.db"

app = Flask(__name__)
app.config.update(
    SECRET_KEY="change_this_secret_key",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# ---------------- DB helpers ----------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def column_exists(db, table, column):
    row = db.execute(
        "PRAGMA table_info(%s)" % table
    ).fetchall()
    return any(col["name"] == column for col in row)

def init_db():
    db = get_db()
    cur = db.cursor()

    # Users
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE,
            password TEXT NOT NULL
        );
    """)

    # Products (create if not present)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price INTEGER NOT NULL
            -- 'image' may be added by migration below
        );
    """)

    # Migration: ensure 'image' column exists
    if not column_exists(db, "products", "image"):
        cur.execute("ALTER TABLE products ADD COLUMN image TEXT;")

    # Cart
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cart (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(product_id) REFERENCES products(id),
            UNIQUE(user_id, product_id)
        );
    """)

    # Orders
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            total_amount INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            price_each INTEGER NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
    """)

    # Seed products only if table is empty
    count = cur.execute("SELECT COUNT(*) AS c FROM products;").fetchone()["c"]
    if count == 0:
        products = [
            ("Tomato-1 Kg", 200, "img/tomato.jpeg"),
            ("Beans-1 Kg", 100, "img/beans.jpeg"),
            ("Brinjal-1 Kg", 60, "img/brinjal.jpeg"),
            ("Potato-1 Kg", 50, "img/potato.jpeg"),
            ("Cabbage-1 Kg", 40, "img/Cabbage.jpg"),
            ("Onion-1 Kg", 70, "img/onion.jpg"),
            ("Cauliflower-1 Pc", 55, "img/cauliflower.jpg"),
            ("Lemon-6 Pc", 30, "img/lemon.jpg"),
        ]
        cur.executemany(
            "INSERT INTO products (name, price, image) VALUES (?, ?, ?);",
            products
        )

    db.commit()

# ---------------- Auth helpers ----------------
def current_user_id():
    return session.get("user_id")

# ---------------- Routes ----------------
@app.route("/")
def home():
    db = get_db()
    products = db.execute(
        "SELECT id, name, price, image FROM products ORDER BY id;"
    ).fetchall()
    return render_template("index.html", products=products)

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template("signup.html")

        try:
            db = get_db()
            db.execute(
                "INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
                (username, email, password)  # Note: hash in real apps
            )
            db.commit()
            flash("Signup successful. Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username or email already exists.", "error")
            return render_template("signup.html")

    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        db = get_db()
        user = db.execute(
            "SELECT id, username FROM users WHERE username=? AND password=?",
            (username, password)
        ).fetchone()

        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            flash("Logged in.", "success")
            return redirect(url_for("home"))
        else:
            flash("Invalid credentials.", "error")
            return render_template("login.html")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("home"))

@app.route("/cart")
def cart():
    if not current_user_id():
        return redirect(url_for("login"))
    db = get_db()
    items = db.execute("""
        SELECT c.product_id, p.name, p.price, p.image, c.quantity,
               (p.price * c.quantity) AS subtotal
        FROM cart c
        JOIN products p ON p.id = c.product_id
        WHERE c.user_id = ?
        ORDER BY p.name;
    """, (current_user_id(),)).fetchall()

    total = sum(row["subtotal"] for row in items) if items else 0
    return render_template("cart.html", items=items, total=total)

@app.post("/cart/add/<int:product_id>")
def add_to_cart(product_id: int):
    if not current_user_id():
        return jsonify({"ok": False, "redirect": url_for("login")}), 401

    db = get_db()
    row = db.execute(
        "SELECT quantity FROM cart WHERE user_id=? AND product_id=?;",
        (current_user_id(), product_id)
    ).fetchone()
    if row:
        db.execute("""
            UPDATE cart SET quantity = quantity + 1
            WHERE user_id=? AND product_id=?;
        """, (current_user_id(), product_id))
    else:
        db.execute("""
            INSERT INTO cart (user_id, product_id, quantity)
            VALUES (?, ?, 1);
        """, (current_user_id(), product_id))
    db.commit()
    return jsonify({"ok": True})

@app.post("/cart/update/<int:product_id>")
def update_cart(product_id: int):
    if not current_user_id():
        return jsonify({"ok": False, "redirect": url_for("login")}), 401

    qty = int(request.form.get("quantity", 1))
    qty = max(0, qty)
    db = get_db()

    if qty == 0:
        db.execute(
            "DELETE FROM cart WHERE user_id=? AND product_id=?;",
            (current_user_id(), product_id)
        )
    else:
        db.execute("""
            UPDATE cart SET quantity=? WHERE user_id=? AND product_id=?;
        """, (qty, current_user_id(), product_id))
    db.commit()
    return jsonify({"ok": True})

@app.post("/cart/clear")
def clear_cart():
    if not current_user_id():
        return jsonify({"ok": False}), 401
    db = get_db()
    db.execute("DELETE FROM cart WHERE user_id=?;", (current_user_id(),))
    db.commit()
    return jsonify({"ok": True})

@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    if not current_user_id():
        return redirect(url_for("login"))

    db = get_db()
    if request.method == "POST":
        items = db.execute("""
            SELECT c.product_id, p.price, c.quantity
            FROM cart c JOIN products p ON p.id = c.product_id
            WHERE c.user_id=?;
        """, (current_user_id(),)).fetchall()

        if not items:
            flash("Cart is empty.", "error")
            return redirect(url_for("cart"))

        total = sum(i["price"] * i["quantity"] for i in items)

        cur = db.cursor()
        cur.execute(
            "INSERT INTO orders (user_id, total_amount) VALUES (?, ?);",
            (current_user_id(), total)
        )
        order_id = cur.lastrowid

        cur.executemany("""
            INSERT INTO order_items (order_id, product_id, quantity, price_each)
            VALUES (?, ?, ?, ?);
        """, [(order_id, i["product_id"], i["quantity"], i["price"]) for i in items])

        cur.execute("DELETE FROM cart WHERE user_id=?;", (current_user_id(),))
        db.commit()

        return redirect(url_for("thank_you", order_id=order_id))

    items = db.execute("""
        SELECT p.name, p.price, c.quantity, (p.price*c.quantity) AS subtotal
        FROM cart c JOIN products p ON p.id = c.product_id
        WHERE c.user_id=?;
    """, (current_user_id(),)).fetchall()
    total = sum(row["subtotal"] for row in items) if items else 0
    return render_template("checkout.html", items=items, total=total)

@app.route("/thank-you/<int:order_id>")
def thank_you(order_id: int):
    return render_template("ThankYou.html", order_id=order_id)

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        # Save or email message here if you like
        flash("Message sent. We'll get back soon.", "success")
        return redirect(url_for("contact"))
    return render_template("contact.html")

@app.route("/pay")
def pay():
    return render_template("paym.html")

@app.get("/api/products")
def api_products():
    db = get_db()
    rows = db.execute(
        "SELECT id, name, price, image FROM products ORDER BY id;"
    ).fetchall()
    return jsonify([dict(r) for r in rows])

# ---------------- Entry ----------------
if __name__ == "__main__":
    with app.app_context():
        # Create DB file if needed and run schema setup + migration
        if not DB_PATH.exists():
            os.makedirs(BASE_DIR, exist_ok=True)
        init_db()

    app.run(debug=True)
