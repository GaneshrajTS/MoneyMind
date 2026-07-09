from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

import sqlite3
import numpy as np
import pandas as pd
from io import BytesIO

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

# -------------------------
# Flask Config
# -------------------------

app = Flask(__name__)
app.secret_key = "moneymind_supersecretkey_2025"

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

DATABASE = "finance.db"


# -------------------------
# User Model
# -------------------------

class User(UserMixin):
    def __init__(self, id_, username, password_hash):
        self.id = id_
        self.username = username
        self.password_hash = password_hash


# -------------------------
# Database Helpers
# -------------------------

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            description TEXT DEFAULT '',
            amount REAL NOT NULL,
            category TEXT DEFAULT 'Other',
            type TEXT NOT NULL DEFAULT 'expense',
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    conn.commit()
    conn.close()


@app.before_request
def ensure_db():
    init_db()


@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if user:
        return User(user["id"], user["username"], user["password_hash"])
    return None


# -------------------------
# Authentication
# -------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Username and password are required.", "danger")
            return render_template("register.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("register.html")

        password_hash = generate_password_hash(password)
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, password_hash)
            )
            conn.commit()
            flash("Registration successful! Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username already exists. Please choose another.", "danger")
        finally:
            conn.close()

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        user_row = conn.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()
        conn.close()

        if user_row and check_password_hash(user_row["password_hash"], password):
            user = User(user_row["id"], user_row["username"], user_row["password_hash"])
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard"))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# -------------------------
# Pages
# -------------------------

@app.route("/")
@login_required
def home():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", user=current_user.username)


@app.route("/transactions")
@login_required
def transactions_page():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM transactions WHERE user_id=? ORDER BY date DESC",
        (current_user.id,)
    ).fetchall()
    conn.close()
    return render_template("transactions.html", transactions=rows, user=current_user.username)


# -------------------------
# Transaction API
# -------------------------

@app.route("/api/add_transaction", methods=["POST"])
@login_required
def add_transaction():
    data = request.get_json()

    if not data:
        return jsonify({"error": "No data provided"}), 400

    date = data.get("date")
    amount = data.get("amount")
    tx_type = data.get("type", "expense")

    if not date or amount is None:
        return jsonify({"error": "Date and amount are required"}), 400

    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid amount"}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO transactions (user_id, date, description, amount, category, type) VALUES (?,?,?,?,?,?)",
        (
            current_user.id,
            date,
            data.get("description", ""),
            amount,
            data.get("category", "Other"),
            tx_type
        )
    )
    conn.commit()
    conn.close()

    return jsonify({"status": "success"})


@app.route("/api/transactions")
@login_required
def get_transactions():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, date, amount, category, type, description FROM transactions WHERE user_id=? ORDER BY date DESC",
        (current_user.id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route("/api/delete_transaction", methods=["DELETE"])
@login_required
def delete_transaction():
    data = request.get_json()
    tx_id = data.get("id")

    if not tx_id:
        return jsonify({"error": "Transaction ID required"}), 400

    conn = get_db()
    result = conn.execute(
        "DELETE FROM transactions WHERE id=? AND user_id=?",
        (tx_id, current_user.id)
    )
    conn.commit()
    conn.close()

    if result.rowcount == 0:
        return jsonify({"error": "Transaction not found"}), 404

    return jsonify({"status": "deleted"})


# -------------------------
# Edit Transaction Page
# -------------------------

@app.route("/transaction/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit_transaction(id):
    conn = get_db()

    if request.method == "POST":
        date = request.form.get("date")
        description = request.form.get("description", "")
        amount = request.form.get("amount")
        category = request.form.get("category", "Other")
        tx_type = request.form.get("type", "expense")

        try:
            amount = float(amount)
        except (ValueError, TypeError):
            flash("Invalid amount.", "danger")
            return redirect(url_for("edit_transaction", id=id))

        conn.execute(
            """UPDATE transactions SET date=?, description=?, amount=?, category=?, type=?
               WHERE id=? AND user_id=?""",
            (date, description, amount, category, tx_type, id, current_user.id)
        )
        conn.commit()
        conn.close()
        flash("Transaction updated successfully.", "success")
        return redirect(url_for("transactions_page"))

    tx = conn.execute(
        "SELECT * FROM transactions WHERE id=? AND user_id=?",
        (id, current_user.id)
    ).fetchone()
    conn.close()

    if not tx:
        flash("Transaction not found.", "danger")
        return redirect(url_for("transactions_page"))

    return render_template("edit_transaction.html", tx=tx)


# -------------------------
# Forecast API (SARIMA)
# -------------------------

@app.route("/api/forecast")
@login_required
def forecast():
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX
    except ImportError:
        return jsonify({"error": "statsmodels not installed"})

    conn = get_db()

    rows = conn.execute(
        """
        SELECT date, amount
        FROM transactions
        WHERE user_id=? AND type='expense'
        ORDER BY date
        """,
        (current_user.id,)
    ).fetchall()

    conn.close()

    if len(rows) < 4:
        return jsonify({
            "error": "Not enough data. Add at least 4 expense transactions."
        })

    # Convert to dataframe
    df = pd.DataFrame([dict(r) for r in rows])

    df["date"] = pd.to_datetime(df["date"])

    # Set date index
    df.set_index("date", inplace=True)

    # Monthly expense totals
    monthly = df.resample("ME").sum()

    # ---------------------------------------------------
    # FIX: Remove current incomplete month
    # ---------------------------------------------------
    current_month = pd.Timestamp.now().to_period("M")

    monthly = monthly[
        monthly.index.to_period("M") < current_month
    ]

    series = monthly["amount"]

    if len(series) < 3:
        return jsonify({
            "error": "Not enough complete monthly data for forecasting."
        })

    try:
        # Better SARIMAX model
        model = SARIMAX(
            series,
            order=(1, 1, 1),
            seasonal_order=(1, 1, 1, 12),
            enforce_stationarity=False,
            enforce_invertibility=False
        )

        result = model.fit(disp=False, maxiter=200)

        # Forecast next 3 months
        pred = result.forecast(3)

        # Prevent negative predictions
        pred = pred.clip(lower=0)

        labels = [
            d.strftime("%b %Y")
            for d in pred.index
        ]

        values = [
            round(float(v), 2)
            for v in pred
        ]

        return jsonify({
            "labels": labels,
            "values": values
        })

    except Exception as e:
        print("Forecast error:", e)

        # Fallback = average monthly expense
        mean_val = max(float(series.mean()), 0)

        return jsonify({
            "labels": ["Month+1", "Month+2", "Month+3"],
            "values": [
                round(mean_val, 2),
                round(mean_val, 2),
                round(mean_val, 2)
            ]
        })

# -------------------------
# Anomaly Detection
# -------------------------

@app.route("/api/anomalies")
@login_required
def anomalies():
    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        return jsonify({"error": "scikit-learn not installed"})

    conn = get_db()

    # ONLY expense transactions
    rows = conn.execute(
        """
        SELECT date, amount, category, description
        FROM transactions
        WHERE user_id=? AND type='expense'
        ORDER BY date
        """,
        (current_user.id,)
    ).fetchall()

    conn.close()

    if len(rows) < 10:
        return jsonify({
            "error": "Need at least 10 expense transactions for anomaly detection."
        })

    # Convert to dataframe
    df = pd.DataFrame([dict(r) for r in rows])

    # Ensure numeric amounts
    df["amount"] = pd.to_numeric(df["amount"])

    # ----------------------------------------
    # Isolation Forest
    # Only amount used for anomaly detection
    # ----------------------------------------
    iso = IsolationForest(
        contamination=0.1,
        random_state=42
    )

    df["anomaly"] = iso.fit_predict(df[["amount"]])

    # Get anomalies
    out = df[df["anomaly"] == -1][
        ["date", "amount", "category", "description"]
    ]

    # Sort by highest unusual amount
    out = out.sort_values(by="amount", ascending=False)

    return jsonify(
        out.to_dict(orient="records")
    )

# -------------------------
# Budget Optimization
# -------------------------

@app.route("/api/optimize", methods=["POST"])
@login_required
def optimize():
    try:
        from scipy.optimize import linprog
    except ImportError:
        return jsonify({"error": "scipy not installed"})

    data = request.get_json()
    categories = data.get("categories", [])
    incomes = data.get("income", [])
    priorities = data.get("priorities", [])

    if not categories:
        return jsonify({"error": "No categories provided"})

    c = [-p for p in priorities]
    A = [[1] * len(categories)]
    b = [sum(incomes)]
    bounds = [(0, i) for i in incomes]

    res = linprog(c, A_ub=A, b_ub=b, bounds=bounds, method="highs")

    if res.success:
        return jsonify(dict(zip(categories, [round(x, 2) for x in res.x.tolist()])))

    return jsonify({"error": "Optimization failed"})


# -------------------------
# Summary Stats API
# -------------------------

@app.route("/api/summary")
@login_required
def summary():
    conn = get_db()
    rows = conn.execute(
        "SELECT amount, type, category FROM transactions WHERE user_id=?",
        (current_user.id,)
    ).fetchall()
    conn.close()

    income = sum(r["amount"] for r in rows if r["type"] == "income")
    expense = sum(r["amount"] for r in rows if r["type"] == "expense")

    category_totals = {}
    for r in rows:
        if r["type"] == "expense":
            cat = r["category"] or "Other"
            category_totals[cat] = category_totals.get(cat, 0) + r["amount"]

    return jsonify({
        "income": round(income, 2),
        "expense": round(expense, 2),
        "balance": round(income - expense, 2),
        "category_totals": category_totals
    })


# -------------------------
# Download PDF Report
# -------------------------

@app.route("/api/download_report")
@login_required
def download_report():
    conn = get_db()
    rows = conn.execute(
        "SELECT date, description, amount, category, type FROM transactions WHERE user_id=? ORDER BY date DESC",
        (current_user.id,)
    ).fetchall()
    conn.close()

    df = pd.DataFrame([dict(r) for r in rows])

    income = df[df["type"] == "income"]["amount"].sum() if not df.empty else 0
    expense = df[df["type"] == "expense"]["amount"].sum() if not df.empty else 0
    balance = income - expense

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            leftMargin=40, rightMargin=40,
                            topMargin=60, bottomMargin=40)

    styles = getSampleStyleSheet()
    story = []

    # Title
    title = Paragraph("<b>MoneyMind - Financial Report</b>", styles["Title"])
    story.append(title)
    story.append(Spacer(1, 12))

    # User info
    story.append(Paragraph(f"<b>Account:</b> {current_user.username}", styles["Normal"]))
    story.append(Spacer(1, 8))

    # Summary
    summary_data = [
        ["Metric", "Amount"],
        ["Total Income", f"₹{income:,.2f}"],
        ["Total Expenses", f"₹{expense:,.2f}"],
        ["Net Balance", f"₹{balance:,.2f}"],
    ]
    summary_table = Table(summary_data, colWidths=[200, 200])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4361ee")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("PADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 20))

    # Transactions table
    if not df.empty:
        story.append(Paragraph("<b>Transaction History</b>", styles["Heading2"]))
        story.append(Spacer(1, 8))

        tx_data = [["Date", "Description", "Category", "Type", "Amount"]]
        for _, row in df.iterrows():
            tx_data.append([
                str(row["date"]),
                str(row.get("description", ""))[:30],
                str(row.get("category", "")),
                str(row["type"]).capitalize(),
                f"₹{row['amount']:,.2f}"
            ])

        tx_table = Table(tx_data, colWidths=[80, 150, 90, 70, 80])
        tx_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3f37c9")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (4, 0), (4, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("PADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(tx_table)
    else:
        story.append(Paragraph("No transactions found.", styles["Normal"]))

    doc.build(story)
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"moneymind_report_{current_user.username}.pdf",
        mimetype="application/pdf"
    )


# -------------------------

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
