import os
from dotenv import load_dotenv
import mysql.connector

load_dotenv()

# ─────────────────────────────────────────
# 1. DATABASE CONNECTION
# ─────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "user":     os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASS"),
    "database": os.getenv("DB_NAME", "duka_pos")
}

def get_connection():
    return mysql.connector.connect(**DB_CONFIG)

def init_tables():
    """Create missing tables and patch existing ones with any new columns."""
    conn   = get_connection()
    cursor = conn.cursor()

    # Create Inventory if not exists (core POS table)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Inventory (
            item_id         INT AUTO_INCREMENT PRIMARY KEY,
            item_name       VARCHAR(200) NOT NULL UNIQUE,
            selling_price   DECIMAL(10,2) NOT NULL DEFAULT 0,
            cost_price      DECIMAL(10,2) NOT NULL DEFAULT 0,
            stock_quantity  INT NOT NULL DEFAULT 0,
            barcode         VARCHAR(100),
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Patch: add missing columns if Inventory existed before this update
    for col, definition in [
        ("cost_price",     "DECIMAL(10,2) NOT NULL DEFAULT 0"),
        ("stock_quantity", "INT NOT NULL DEFAULT 0"),
        ("barcode",        "VARCHAR(100)"),
        ("created_at",     "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE Inventory ADD COLUMN {col} {definition}")
            conn.commit()
            print(f"Patched Inventory: added {col}")
        except Exception:
            pass  # column already exists

    # Create Mpesa_Transactions if not exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Mpesa_Transactions (
            id                  INT AUTO_INCREMENT PRIMARY KEY,
            checkout_request_id VARCHAR(100) UNIQUE,
            phone_number        VARCHAR(20),
            amount              DECIMAL(10,2),
            status              VARCHAR(20) DEFAULT 'Pending',
            mpesa_code          VARCHAR(50),
            item                VARCHAR(100),
            quantity            INT DEFAULT 1,
            timestamp           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Patch: add missing columns if table existed before this update
    for col, definition in [
        ("item",      "VARCHAR(200)"),
        ("quantity",  "INT DEFAULT 1"),
        ("cart_data", "TEXT"),         # stores full cart JSON for dashboard sales
        ("fail_reason", "VARCHAR(255)"),  # store ResultDesc for failed/cancelled
    ]:
        try:
            cursor.execute(f"ALTER TABLE Mpesa_Transactions ADD COLUMN {col} {definition}")
            conn.commit()
            print(f"Patched Mpesa_Transactions: added {col}")
        except Exception:
            pass  # column already exists

    # Create Sales_Ledger if not exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Sales_Ledger (
            sale_id        INT AUTO_INCREMENT PRIMARY KEY,
            item_id        INT,
            quantity_sold  INT,
            total_price    DECIMAL(10,2),
            phone          VARCHAR(20),
            payment_method VARCHAR(20) DEFAULT 'cash',
            sale_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Patch: add phone / payment_method columns if missing
    for col, definition in [("phone", "VARCHAR(20)"), ("payment_method", "VARCHAR(20) DEFAULT 'cash'")]:
        try:
            cursor.execute(f"ALTER TABLE Sales_Ledger ADD COLUMN {col} {definition}")
            conn.commit()
            print(f"Patched Sales_Ledger: added {col}")
        except Exception:
            pass  # already exists

    # Create Users table for multi-user support
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Users (
            id           INT AUTO_INCREMENT PRIMARY KEY,
            telegram_id  BIGINT UNIQUE NOT NULL,
            name         VARCHAR(100),
            role         VARCHAR(20) DEFAULT 'attendant',
            is_active    BOOLEAN DEFAULT TRUE,
            joined_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Customer self-checkout orders (Telegram)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Customer_Orders (
            order_id            INT AUTO_INCREMENT PRIMARY KEY,
            telegram_user_id    BIGINT NOT NULL,
            telegram_username   VARCHAR(100),
            telegram_full_name  VARCHAR(150),
            phone               VARCHAR(30),
            status              VARCHAR(30) NOT NULL DEFAULT 'PendingPayment',
            total_amount        DECIMAL(10,2) NOT NULL DEFAULT 0,
            checkout_request_id VARCHAR(100),
            mpesa_receipt       VARCHAR(50),
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            paid_at             TIMESTAMP NULL,
            approved_at         TIMESTAMP NULL,
            approved_by         VARCHAR(100)
        )
    """)
    try:
        cursor.execute("CREATE INDEX idx_customer_orders_status ON Customer_Orders (status)")
        conn.commit()
    except Exception:
        pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Customer_Order_Items (
            id         INT AUTO_INCREMENT PRIMARY KEY,
            order_id   INT NOT NULL,
            item_id    INT,
            item_name  VARCHAR(200),
            qty        INT NOT NULL DEFAULT 1,
            unit_price DECIMAL(10,2) NOT NULL DEFAULT 0,
            line_total DECIMAL(10,2) NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    cursor.close()
    conn.close()

# ─────────────────────────────────────────
# USERS — multi-user management
# ─────────────────────────────────────────
def get_user(telegram_id: int) -> dict | None:
    """Return user dict if they exist, else None."""
    conn   = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM Users WHERE telegram_id = %s", (telegram_id,))
        return cursor.fetchone()
    except Exception:
        return None
    finally:
        cursor.close()
        conn.close()

def register_user(telegram_id: int, name: str, role: str = "attendant") -> bool:
    """Register a new user. Returns True on success."""
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT IGNORE INTO Users (telegram_id, name, role) VALUES (%s, %s, %s)",
            (telegram_id, name, role)
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print(f"Register error: {e}")
        return False
    finally:
        cursor.close()
        conn.close()

def list_users() -> list:
    """Return all registered users."""
    conn   = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM Users ORDER BY role, joined_at")
        return cursor.fetchall()
    except Exception:
        return []
    finally:
        cursor.close()
        conn.close()

def remove_user(telegram_id: int) -> bool:
    """Remove a user by telegram_id."""
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM Users WHERE telegram_id = %s AND role != 'admin'", (telegram_id,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        return False
    finally:
        cursor.close()
        conn.close()

def update_user_role(telegram_id: int, role: str) -> bool:
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE Users SET role = %s WHERE telegram_id = %s", (role, telegram_id))
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        return False
    finally:
        cursor.close()
        conn.close()

# ─────────────────────────────────────────
# 2. ITEM PRICE LOOKUP
# ─────────────────────────────────────────
def get_item_price(item_name: str):
    """Returns the selling price of an item. Used by mpesa_api before STK push."""
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT selling_price FROM Inventory WHERE item_name LIKE %s",
            ("%" + item_name + "%",)
        )
        result = cursor.fetchone()
        return float(result[0]) if result else "Error: Item not found in inventory."
    except Exception as e:
        return f"Error: {e}"
    finally:
        cursor.close()
        conn.close()

# ─────────────────────────────────────────
# 3. CHECK STOCK
# ─────────────────────────────────────────
def check_stock(item_name: str) -> str:
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        if item_name and item_name.strip():
            cursor.execute(
                "SELECT item_name, stock_quantity, selling_price FROM Inventory WHERE item_name LIKE %s",
                ("%" + item_name + "%",)
            )
            result = cursor.fetchone()
            if result:
                status = "🔴 LOW" if result[1] < 10 else ("🟡 OK" if result[1] <= 20 else "🟢 GOOD")
                return (f"📦 *{result[0]}*\n"
                        f"Stock : {result[1]} units  {status}\n"
                        f"Price : KES {result[2]}")
            return f"❌ '{item_name}' not found in inventory."
        else:
            # No item specified — return full inventory
            cursor.execute(
                "SELECT item_name, stock_quantity, selling_price FROM Inventory ORDER BY stock_quantity ASC"
            )
            rows = cursor.fetchall()
            if not rows:
                return "📦 Inventory is empty."
            msg = "📦 *Full Inventory:*\n\n"
            for r in rows:
                status = "🔴" if r[1] < 10 else ("🟡" if r[1] <= 20 else "🟢")
                msg += f"{status} {r[0]} — KES {r[2]} | {r[1]} units\n"
            return msg
    except Exception as e:
        return f"Error: {e}"
    finally:
        cursor.close()
        conn.close()

# ─────────────────────────────────────────
# 4. PROCESS A SALE (cash)
# ─────────────────────────────────────────
def process_sale(item_name: str, quantity_sold: int, phone: str = "cash",
                 payment_method: str = "cash") -> str:
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT item_id, selling_price, stock_quantity FROM Inventory WHERE item_name LIKE %s",
            ("%" + item_name + "%",)
        )
        item = cursor.fetchone()
        if not item:
            return f"❌ Item '{item_name}' not found."

        item_id, selling_price, current_stock = item
        quantity_sold = int(quantity_sold)

        if current_stock < quantity_sold:
            return f"⚠️ Not enough stock. Only {current_stock} units of {item_name} left."

        total_price = selling_price * quantity_sold

        # Deduct stock
        cursor.execute(
            "UPDATE Inventory SET stock_quantity = stock_quantity - %s WHERE item_id = %s",
            (quantity_sold, item_id)
        )
        # Record sale
        cursor.execute(
            "INSERT INTO Sales_Ledger (item_id, quantity_sold, total_price, phone, payment_method) "
            "VALUES (%s, %s, %s, %s, %s)",
            (item_id, quantity_sold, total_price, phone, payment_method)
        )
        conn.commit()
        return f"✅ Sold {quantity_sold}x {item_name} for KES {total_price}."
    except Exception as e:
        return f"Error: {e}"
    finally:
        cursor.close()
        conn.close()

# ─────────────────────────────────────────
# 5. ADD STOCK
# ─────────────────────────────────────────
def add_stock(item_name: str, quantity: int) -> str:
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE Inventory SET stock_quantity = stock_quantity + %s WHERE item_name LIKE %s",
            (quantity, "%" + item_name + "%")
        )
        if cursor.rowcount == 0:
            return f"❌ '{item_name}' not found in inventory. Add it via the database first."
        conn.commit()
        return f"✅ Added {quantity} units of {item_name} to stock."
    except Exception as e:
        return f"Error: {e}"
    finally:
        cursor.close()
        conn.close()

# ─────────────────────────────────────────
# 6. SALES REPORT
# ─────────────────────────────────────────
def get_sales_report(period: str = "today", date_from: str = None, date_to: str = None) -> str:
    """
    Flexible sales report.
    period    : today | yesterday | week | month | year | all
    date_from : "YYYY-MM-DD" — start of custom range
    date_to   : "YYYY-MM-DD" — end of custom range (defaults to today)
    """
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        # ── Build date filter ──
        if date_from:
            date_to = date_to or "CURDATE()"
            if date_to != "CURDATE()":
                date_to = f"'{date_to}'"
            date_filter = f"DATE(s.sale_timestamp) BETWEEN '{date_from}' AND {date_to}"
            label = f"{date_from} to {date_to.strip(chr(39))}"
        elif period == "today":
            date_filter = "DATE(s.sale_timestamp) = CURDATE()"
            label       = "Today"
        elif period == "yesterday":
            date_filter = "DATE(s.sale_timestamp) = DATE_SUB(CURDATE(), INTERVAL 1 DAY)"
            label       = "Yesterday"
        elif period == "week":
            date_filter = "s.sale_timestamp >= DATE_SUB(NOW(), INTERVAL 7 DAY)"
            label       = "This Week"
        elif period == "year":
            date_filter = "YEAR(s.sale_timestamp) = YEAR(NOW())"
            label       = "This Year"
        elif period == "all":
            date_filter = "1=1"
            label       = "All Time"
        else:  # month
            date_filter = "s.sale_timestamp >= DATE_SUB(NOW(), INTERVAL 30 DAY)"
            label       = "This Month"

        cursor.execute(f"""
            SELECT i.item_name,
                   SUM(s.quantity_sold)                                AS qty,
                   SUM(s.total_price)                                  AS revenue,
                   SUM(s.quantity_sold * i.cost_price)                 AS cost,
                   SUM(s.total_price - s.quantity_sold * i.cost_price) AS profit
            FROM Sales_Ledger s
            JOIN Inventory i ON s.item_id = i.item_id
            WHERE {date_filter}
            GROUP BY i.item_name
            ORDER BY SUM(s.total_price) DESC
        """)
        rows = cursor.fetchall()

        if not rows:
            return f"📊 No sales recorded for *{label}* yet."

        total_revenue = sum(float(r[2]) for r in rows)
        total_profit  = sum(float(r[4] or 0) for r in rows)
        total_qty     = sum(int(r[1])   for r in rows)

        msg = f"📊 *Sales Report — {label}*\n\n"
        for r in rows:
            name    = r[0]
            qty     = int(r[1])
            rev     = float(r[2])
            profit  = float(r[4] or 0)
            msg += f"• *{name}*: {qty} units → KES {rev:.0f} (profit: KES {profit:.0f})\n"

        msg += f"\n{'─'*30}\n"
        msg += f"🛒 *Items Sold: {total_qty}*\n"
        msg += f"💰 *Revenue: KES {total_revenue:.0f}*\n"
        msg += f"📈 *Profit: KES {total_profit:.0f}*"
        return msg
    except Exception as e:
        return f"Error generating report: {e}"
    finally:
        cursor.close()
        conn.close()

# ─────────────────────────────────────────
# 7. ADVANCED SALES HISTORY QUERY
# ─────────────────────────────────────────

def get_sales_history(period: str = "today", date_from: str = None, date_to: str = None) -> str:
    """
    Flexible sales query.
    period: today | yesterday | this week | this month | this year | all
    date_from / date_to: "YYYY-MM-DD" for custom ranges
    """
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        # Build date filter
        if date_from and date_to:
            date_filter = f"DATE(s.sale_timestamp) BETWEEN '{date_from}' AND '{date_to}'"
            label       = f"{date_from} to {date_to}"
        elif date_from:
            date_filter = f"DATE(s.sale_timestamp) >= '{date_from}'"
            label       = f"From {date_from}"
        elif period == "today":
            date_filter = "DATE(s.sale_timestamp) = CURDATE()"
            label       = "Today"
        elif period == "yesterday":
            date_filter = "DATE(s.sale_timestamp) = DATE_SUB(CURDATE(), INTERVAL 1 DAY)"
            label       = "Yesterday"
        elif period in ("week", "this week"):
            date_filter = "s.sale_timestamp >= DATE_SUB(NOW(), INTERVAL 7 DAY)"
            label       = "This Week"
        elif period in ("month", "this month"):
            date_filter = "s.sale_timestamp >= DATE_SUB(NOW(), INTERVAL 30 DAY)"
            label       = "This Month"
        elif period in ("year", "this year"):
            date_filter = "YEAR(s.sale_timestamp) = YEAR(NOW())"
            label       = "This Year"
        elif period == "all":
            date_filter = "1=1"
            label       = "All Time"
        else:
            # Try to parse as specific month e.g. "january", "march 2025"
            import calendar
            months = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
            p_lower = period.lower()
            matched_month = None
            matched_year  = None
            for m_name, m_num in months.items():
                if m_name in p_lower:
                    matched_month = m_num
                    break
            import re
            year_match = re.search(r'20\d{2}', period)
            if year_match:
                matched_year = int(year_match.group())
            if matched_month and matched_year:
                date_filter = f"MONTH(s.sale_timestamp)={matched_month} AND YEAR(s.sale_timestamp)={matched_year}"
                label       = f"{calendar.month_name[matched_month]} {matched_year}"
            elif matched_month:
                date_filter = f"MONTH(s.sale_timestamp) = {matched_month}"
                label       = calendar.month_name[matched_month]
            elif matched_year:
                date_filter = f"YEAR(s.sale_timestamp) = {matched_year}"
                label       = str(matched_year)
            else:
                date_filter = "DATE(s.sale_timestamp) = CURDATE()"
                label       = "Today"

        cursor.execute(f"""
            SELECT i.item_name,
                   SUM(s.quantity_sold)                                AS qty,
                   SUM(s.total_price)                                  AS revenue,
                   SUM(s.quantity_sold * i.cost_price)                 AS cost,
                   SUM(s.total_price - s.quantity_sold * i.cost_price) AS profit,
                   MIN(DATE(s.sale_timestamp))                         AS first_sale,
                   MAX(DATE(s.sale_timestamp))                         AS last_sale
            FROM Sales_Ledger s
            JOIN Inventory i ON s.item_id = i.item_id
            WHERE {date_filter}
            GROUP BY i.item_name
            ORDER BY SUM(s.total_price) DESC
        """)
        rows = cursor.fetchall()

        if not rows:
            return f"📊 No sales recorded for *{label}* yet."

        total_revenue = sum(float(r[2]) for r in rows)
        total_profit  = sum(float(r[4] or 0) for r in rows)
        total_qty     = sum(int(r[1]) for r in rows)

        msg = "📊 *Sales History — " + label + "*\n\n"
        for r in rows:
            name    = r[0]
            qty     = int(r[1])
            rev     = float(r[2])
            profit  = float(r[4] or 0)
            msg    += "• *" + name + "*: " + str(qty) + " units → KES " + str(int(rev)) + " (profit: KES " + str(int(profit)) + ")\n"

        msg += "\n" + "─"*28 + "\n"
        msg += "🛒 *Items Sold: " + str(total_qty) + " units*\n"
        msg += "💰 *Revenue: KES " + str(int(total_revenue)) + "*\n"
        msg += "📈 *Profit: KES " + str(int(total_profit)) + "*"
        
        return msg

    except Exception as e:
        return "Error generating history: " + str(e)
    finally:
        cursor.close()
        conn.close()