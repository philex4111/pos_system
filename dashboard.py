"""
My Duka POS — Full Dashboard
Includes: Overview · POS Checkout · Inventory · AI Chat
Run:  python dashboard.py
Open: http://localhost:8080   password: duka2024
"""
import sys
from flask import Flask, request, session, redirect, jsonify, make_response
import mysql.connector, json
from datetime import datetime
from functools import wraps
import pos_engine
import os

# Avoid Windows console UnicodeEncodeError when printing emojis
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

app = Flask(__name__)
app.secret_key = "duka_dashboard_secret_2024"
DASHBOARD_PASSWORD = "duka2024"

@app.before_request
def _log_mpesa_requests():
    """Log M-Pesa-related requests to help debugging."""
    try:
        p = request.path or ""
        if p.startswith("/api/mpesa"):
            print(f"➡️  {request.method} {request.path}", flush=True)
    except Exception:
        pass

DB_CONFIG = {
    "host":     "localhost",
    "user":     "root",
    "password": "@Lakika2003",
    "database": "duka_pos"
}

def db():
    return mysql.connector.connect(**DB_CONFIG)

# ─────────────────────────────────────────
# DATA HELPERS
# ─────────────────────────────────────────
def get_summary():
    conn = db(); cur = conn.cursor(dictionary=True)
    cur.execute("""SELECT
        SUM(CASE WHEN DATE(sale_timestamp)=CURDATE() THEN total_price ELSE 0 END) AS today,
        SUM(CASE WHEN sale_timestamp>=DATE_SUB(NOW(),INTERVAL 7 DAY) THEN total_price ELSE 0 END) AS week,
        SUM(CASE WHEN sale_timestamp>=DATE_SUB(NOW(),INTERVAL 30 DAY) THEN total_price ELSE 0 END) AS month,
        SUM(total_price) AS all_time,
        COUNT(CASE WHEN DATE(sale_timestamp)=CURDATE() THEN 1 END) AS txn_today,
        SUM(CASE WHEN DATE(sale_timestamp)=CURDATE() THEN quantity_sold ELSE 0 END) AS items_today
        FROM Sales_Ledger""")
    r = cur.fetchone(); cur.close(); conn.close()
    return {k: float(v or 0) for k, v in r.items()}

def get_profit_summary():
    conn = db(); cur = conn.cursor(dictionary=True)
    cur.execute("""SELECT
        SUM(CASE WHEN DATE(s.sale_timestamp)=CURDATE()
            THEN s.total_price-s.quantity_sold*i.cost_price ELSE 0 END) AS today,
        SUM(CASE WHEN s.sale_timestamp>=DATE_SUB(NOW(),INTERVAL 7 DAY)
            THEN s.total_price-s.quantity_sold*i.cost_price ELSE 0 END) AS week,
        SUM(CASE WHEN s.sale_timestamp>=DATE_SUB(NOW(),INTERVAL 30 DAY)
            THEN s.total_price-s.quantity_sold*i.cost_price ELSE 0 END) AS month,
        SUM(s.total_price-s.quantity_sold*i.cost_price) AS all_time
        FROM Sales_Ledger s JOIN Inventory i ON s.item_id=i.item_id""")
    r = cur.fetchone(); cur.close(); conn.close()
    return {k: float(v or 0) for k, v in r.items()}

def get_best_sellers():
    conn = db(); cur = conn.cursor(dictionary=True)
    cur.execute("""SELECT i.item_name AS name,SUM(s.quantity_sold) AS qty,SUM(s.total_price) AS revenue
        FROM Sales_Ledger s JOIN Inventory i ON s.item_id=i.item_id
        WHERE s.sale_timestamp>=DATE_SUB(NOW(),INTERVAL 30 DAY)
        GROUP BY i.item_name ORDER BY qty DESC LIMIT 6""")
    rows = cur.fetchall(); cur.close(); conn.close()
    for r in rows: r["revenue"] = float(r["revenue"])
    return rows

def get_stock_levels():
    conn = db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT item_id,item_name,stock_quantity,selling_price,cost_price,barcode FROM Inventory ORDER BY item_name")
    rows = cur.fetchall(); cur.close(); conn.close()
    for r in rows:
        r["selling_price"] = float(r["selling_price"] or 0)
        r["cost_price"]    = float(r["cost_price"] or 0)
    return rows

def get_daily_chart():
    conn = db(); cur = conn.cursor(dictionary=True)
    cur.execute("""SELECT DATE(sale_timestamp) AS day,SUM(total_price) AS revenue,SUM(quantity_sold) AS items
        FROM Sales_Ledger WHERE sale_timestamp>=DATE_SUB(NOW(),INTERVAL 7 DAY)
        GROUP BY DATE(sale_timestamp) ORDER BY day ASC""")
    rows = cur.fetchall(); cur.close(); conn.close()
    return {"labels":[str(r["day"]) for r in rows],
            "revenue":[float(r["revenue"]) for r in rows],
            "items":[int(r["items"]) for r in rows]}

def get_items_today():
    conn = db(); cur = conn.cursor(dictionary=True)
    cur.execute("""SELECT i.item_name AS name,SUM(s.quantity_sold) AS qty
        FROM Sales_Ledger s JOIN Inventory i ON s.item_id=i.item_id
        WHERE DATE(s.sale_timestamp)=CURDATE() GROUP BY i.item_name ORDER BY qty DESC""")
    rows = cur.fetchall(); cur.close(); conn.close()
    return {"labels":[r["name"] for r in rows],"quantities":[int(r["qty"]) for r in rows]}

def get_recent_sales(limit=8):
    conn = db(); cur = conn.cursor(dictionary=True)
    cur.execute("""SELECT i.item_name,s.quantity_sold,s.total_price,s.payment_method,s.sale_timestamp
        FROM Sales_Ledger s JOIN Inventory i ON s.item_id=i.item_id
        ORDER BY s.sale_timestamp DESC LIMIT %s""", (limit,))
    rows = cur.fetchall(); cur.close(); conn.close()
    for r in rows:
        r["total_price"] = float(r["total_price"])
        r["sale_timestamp"] = r["sale_timestamp"].strftime("%b %d, %H:%M")
    return rows

def search_items_db(q=""):
    conn = db(); cur = conn.cursor(dictionary=True)
    if q:
        # If a barcode/QR is scanned, it is often numeric; support barcode exact match too.
        cur.execute(
            "SELECT item_id,item_name,selling_price,cost_price,stock_quantity,barcode "
            "FROM Inventory "
            "WHERE item_name LIKE %s OR barcode = %s "
            "ORDER BY item_name",
            (f"%{q}%", q)
        )
    else:
        cur.execute("SELECT item_id,item_name,selling_price,cost_price,stock_quantity,barcode FROM Inventory ORDER BY item_name")
    rows = cur.fetchall(); cur.close(); conn.close()
    for r in rows:
        r["selling_price"] = float(r["selling_price"] or 0)
        r["cost_price"]    = float(r["cost_price"] or 0)
    return rows

def process_cart_sale(cart, payment_method, phone=""):
    conn = db(); cur = conn.cursor(dictionary=True)
    receipt_items = []; total = 0
    try:
        for entry in cart:
            item_id = int(entry["item_id"]); qty = int(entry["qty"])
            cur.execute("SELECT item_name,selling_price,stock_quantity FROM Inventory WHERE item_id=%s",(item_id,))
            item = cur.fetchone()
            if not item: return False, f"Item ID {item_id} not found"
            if item["stock_quantity"] < qty:
                return False, f"Not enough stock for {item['item_name']} (only {item['stock_quantity']} left)"
            price = float(item["selling_price"]) * qty; total += price
            cur.execute("UPDATE Inventory SET stock_quantity=stock_quantity-%s WHERE item_id=%s",(qty,item_id))
            cur.execute("INSERT INTO Sales_Ledger (item_id,quantity_sold,total_price,phone,payment_method) VALUES (%s,%s,%s,%s,%s)",
                (item_id,qty,price,phone or "cash",payment_method))
            receipt_items.append({"name":item["item_name"],"qty":qty,"price":price,"unit":float(item["selling_price"])})
        conn.commit()
        return True,{"items":receipt_items,"total":total,"payment":payment_method,"phone":phone,"time":datetime.now().strftime("%b %d %Y, %H:%M")}
    except Exception as e:
        conn.rollback(); return False, str(e)
    finally:
        cur.close(); conn.close()

def add_inventory_item(name, selling_price, cost_price, stock, barcode=None):
    conn = db(); cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO Inventory (item_name,selling_price,cost_price,stock_quantity,barcode) "
            "VALUES (%s,%s,%s,%s,%s)",
            (name, selling_price, cost_price, stock, (barcode or None))
        )
        conn.commit(); return True,"Item added"
    except Exception as e: return False,str(e)
    finally: cur.close(); conn.close()

def edit_inventory_item(item_id, selling_price, cost_price, stock, barcode=None):
    conn = db(); cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE Inventory SET selling_price=%s,cost_price=%s,stock_quantity=%s,barcode=%s WHERE item_id=%s",
            (selling_price, cost_price, stock, (barcode or None), item_id)
        )
        conn.commit(); return True,"Updated"
    except Exception as e: return False,str(e)
    finally: cur.close(); conn.close()

def add_stock_by_barcode(barcode: str, qty: int) -> tuple[bool, str]:
    barcode = (barcode or "").strip()
    if not barcode:
        return False, "Missing barcode"
    conn = db(); cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE Inventory SET stock_quantity=stock_quantity+%s WHERE barcode=%s",
            (int(qty), barcode)
        )
        if cur.rowcount == 0:
            return False, "Barcode not found in inventory"
        conn.commit()
        return True, "Stock added"
    except Exception as e:
        return False, str(e)
    finally:
        cur.close(); conn.close()

def local_chat_response(text):
    t = text.lower().strip()
    try:
        import pos_engine
        if any(w in t for w in ["report","profit","revenue","sales","mauzo","mapato","faida","how much","earned"]):
            period = "today" if "today" in t or "leo" in t else \
                     "week"  if "week"  in t else \
                     "all"   if any(w in t for w in ["all","so far","ever","total","tangu","overall"]) else "month"
            return pos_engine.get_sales_report(period)
        if any(w in t for w in ["stock","inventory","ngapi","kuna","left","available","remaining"]):
            words = [w for w in t.split() if len(w) > 3]
            for w in words:
                r = pos_engine.check_stock(w)
                if "not found" not in r and "Error" not in r and "empty" not in r.lower():
                    return r
            return pos_engine.check_stock("")
        if any(w in t for w in ["restock","low","order","hitaji","running out"]):
            conn = db(); cur = conn.cursor(dictionary=True)
            cur.execute("SELECT item_name,stock_quantity FROM Inventory ORDER BY stock_quantity ASC LIMIT 10")
            rows = cur.fetchall(); cur.close(); conn.close()
            msg = "📦 *Items needing restock:*\n\n"
            for r in rows:
                icon = "🔴" if r["stock_quantity"] < 5 else ("🟡" if r["stock_quantity"] < 15 else "🟢")
                msg += f"{icon} {r['item_name']} — {r['stock_quantity']} units\n"
            return msg
        if any(w in t for w in ["trend","best sell","popular","top item"]):
            return pos_engine.get_trends()
        return "❓ Sijaelewa.\n\nTry asking:\n• Show today's report\n• How much profit this month\n• Check all stock\n• What needs restocking\n• Best selling items"
    except Exception as e:
        return f"Error: {e}"

# ─────────────────────────────────────────
# SHARED DESIGN SYSTEM
# ─────────────────────────────────────────

FONTS = '<link href="https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700;800&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">'

CSS = FONTS + """<style>
:root {
  --bg:      #080812;
  --sidebar: #0e0e1c;
  --card:    #12121f;
  --card2:   #181829;
  --border:  #1e1e35;
  --border2: #252540;
  --lime:    #c8f135;
  --lime2:   #a8d420;
  --purple:  #7c6af5;
  --purple2: #9d8ff7;
  --text:    #f0f0ff;
  --muted:   #6b6b8a;
  --muted2:  #9090b0;
  --green:   #22d3a0;
  --red:     #f0566a;
  --amber:   #fbbf24;
  --blue:    #60a5fa;
}
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;overflow:hidden}
body{font-family:'Sora',sans-serif;background:var(--bg);color:var(--text);display:flex}

/* ── SIDEBAR ── */
.sidebar{
  width:220px;min-width:220px;height:100vh;background:var(--sidebar);
  border-right:1px solid var(--border);display:flex;flex-direction:column;
  padding:28px 16px 20px;position:relative;z-index:10;flex-shrink:0;
}
.s-brand{display:flex;align-items:center;gap:10px;margin-bottom:36px;padding:0 6px}
.s-brand-icon{width:36px;height:36px;background:var(--lime);border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:1.1rem;flex-shrink:0}
.s-brand-name{font-size:.95rem;font-weight:800;letter-spacing:-.3px}
.s-brand-sub{font-size:.65rem;color:var(--muted);margin-top:1px}

.s-section{font-size:.62rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);padding:0 10px;margin-bottom:8px;margin-top:20px}
.s-link{display:flex;align-items:center;gap:11px;padding:10px 12px;border-radius:10px;color:var(--muted2);font-size:.84rem;font-weight:500;text-decoration:none;transition:.2s;margin-bottom:3px;cursor:pointer}
.s-link:hover{background:var(--card2);color:var(--text)}
.s-link.active{background:var(--lime);color:#000;font-weight:700}
.s-link.active .s-icon{color:#000}
.s-icon{font-size:1rem;width:20px;text-align:center}

.s-bottom{margin-top:auto;border-top:1px solid var(--border);padding-top:16px}
.s-time{font-family:'Space Mono',monospace;font-size:.78rem;color:var(--muted);padding:0 12px;margin-bottom:12px}
.s-logout{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:9px;color:var(--red);font-size:.82rem;cursor:pointer;background:none;border:none;width:100%;font-family:'Sora',sans-serif;transition:.2s}
.s-logout:hover{background:#1a0812}

/* ── MAIN CONTENT ── */
.main{flex:1;height:100vh;overflow-y:auto;overflow-x:hidden;background:var(--bg)}
.main::-webkit-scrollbar{width:5px}
.main::-webkit-scrollbar-track{background:transparent}
.main::-webkit-scrollbar-thumb{background:var(--border2);border-radius:99px}

.page-wrap{padding:28px 28px 32px}

/* ── PAGE HEADER ── */
.page-header{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:26px}
.page-title{font-size:1.55rem;font-weight:800;letter-spacing:-.5px}
.page-subtitle{font-size:.8rem;color:var(--muted);margin-top:3px}
.page-date{font-family:'Space Mono',monospace;font-size:.75rem;color:var(--muted);text-align:right}

/* ── METRIC CARDS ── */
.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:22px}
.metric-card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:22px 20px;position:relative;overflow:hidden;transition:.2s}
.metric-card::before{content:'';position:absolute;top:0;right:0;width:80px;height:80px;border-radius:0 16px 0 80px;opacity:.06}
.metric-card.lime::before{background:var(--lime)}
.metric-card.purple::before{background:var(--purple)}
.metric-card.green::before{background:var(--green)}
.metric-card.amber::before{background:var(--amber)}
.metric-card.blue::before{background:var(--blue)}
.metric-label{font-size:.7rem;text-transform:uppercase;letter-spacing:1.2px;color:var(--muted);margin-bottom:10px}
.metric-value{font-size:2rem;font-weight:800;letter-spacing:-1px;line-height:1}
.metric-value.lime{color:var(--lime)}
.metric-value.purple{color:var(--purple2)}
.metric-value.green{color:var(--green)}
.metric-value.amber{color:var(--amber)}
.metric-value.blue{color:var(--blue)}
.metric-sub{font-size:.73rem;color:var(--muted);margin-top:7px}
.metric-icon{position:absolute;top:18px;right:18px;font-size:1.4rem;opacity:.3}

/* ── GRID LAYOUTS ── */
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.grid-3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:16px}
.grid-1-2{display:grid;grid-template-columns:1fr 2fr;gap:16px;margin-bottom:16px}
@media(max-width:1100px){.grid-2,.grid-3,.grid-1-2{grid-template-columns:1fr}}

/* ── CARD BOX ── */
.card-box{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:22px}
.card-box-title{font-size:.8rem;font-weight:700;letter-spacing:.5px;color:var(--muted2);text-transform:uppercase;margin-bottom:18px;display:flex;align-items:center;gap:8px}
.card-box-title span{opacity:.5}

/* ── TABLES ── */
.data-table{width:100%;border-collapse:collapse;font-size:.82rem}
.data-table th{text-align:left;padding:0 10px 12px;color:var(--muted);font-weight:600;font-size:.7rem;text-transform:uppercase;letter-spacing:.8px;border-bottom:1px solid var(--border)}
.data-table td{padding:13px 10px;border-bottom:1px solid var(--border)40;vertical-align:middle}
.data-table tr:last-child td{border-bottom:none}
.data-table tr:hover td{background:#ffffff04}

/* ── BADGES ── */
.badge{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:99px;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.5px}
.badge-green{background:#022f1f;color:var(--green);border:1px solid #0a4a2a}
.badge-blue{background:#0a1a35;color:var(--blue);border:1px solid #1a3a5f}
.badge-red{background:#2d0812;color:var(--red);border:1px solid #4a1020}
.badge-amber{background:#2d1a00;color:var(--amber);border:1px solid #4a3000}
.badge-lime{background:#1a2500;color:var(--lime);border:1px solid #2a3f00}

/* ── STOCK BAR ── */
.stock-bar-wrap{display:flex;align-items:center;gap:8px}
.stock-bar-bg{flex:1;height:4px;background:var(--border2);border-radius:99px;max-width:80px}
.stock-bar-fill{height:4px;border-radius:99px}

/* ── FORMS ── */
.form-field{margin-bottom:14px}
.form-label{display:block;font-size:.72rem;font-weight:600;color:var(--muted2);text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px}
.form-input{width:100%;background:var(--card2);border:1px solid var(--border2);color:var(--text);padding:10px 13px;border-radius:9px;font-size:.86rem;font-family:'Sora',sans-serif;transition:.2s}
.form-input:focus{outline:none;border-color:var(--purple);background:var(--card)}
.form-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;align-items:end}

/* ── BUTTONS ── */
.btn{display:inline-flex;align-items:center;gap:7px;padding:9px 18px;border-radius:9px;border:none;font-size:.84rem;font-weight:700;cursor:pointer;font-family:'Sora',sans-serif;transition:.2s;letter-spacing:.1px}
.btn-lime{background:var(--lime);color:#000}.btn-lime:hover{background:var(--lime2)}
.btn-purple{background:var(--purple);color:#fff}.btn-purple:hover{background:#6a58e3}
.btn-ghost{background:transparent;border:1px solid var(--border2);color:var(--muted2)}.btn-ghost:hover{border-color:var(--purple);color:var(--text)}
.btn-danger{background:#2d0812;color:var(--red);border:1px solid #4a1020}.btn-danger:hover{background:#3d1020}
.btn-sm{padding:6px 13px;font-size:.76rem;border-radius:7px}
.btn-full{width:100%;justify-content:center}

/* ── POS LAYOUT ── */
.pos-layout{display:grid;grid-template-columns:1fr 360px;gap:16px;height:calc(100vh - 54px)}
@media(max-width:1200px){.pos-layout{grid-template-columns:1fr}}
.pos-left{display:flex;flex-direction:column;gap:12px;overflow:hidden}
.items-scroll{flex:1;overflow-y:auto;display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;padding:2px;align-content:start}
.items-scroll::-webkit-scrollbar{width:4px}
.items-scroll::-webkit-scrollbar-thumb{background:var(--border2);border-radius:99px}

.item-tile{background:var(--card);border:1px solid var(--border);border-radius:13px;padding:15px 13px;cursor:pointer;transition:.2s;text-align:center}
.item-tile:hover{border-color:var(--purple);background:var(--card2);transform:translateY(-2px)}
.item-tile.oos{opacity:.3;cursor:not-allowed}
.item-tile-name{font-weight:700;font-size:.82rem;margin-bottom:5px;line-height:1.3}
.item-tile-price{color:var(--lime);font-size:.88rem;font-weight:800;font-family:'Space Mono',monospace}
.item-tile-stock{font-size:.68rem;color:var(--muted);margin-top:4px}

/* ── CART ── */
.cart-panel{background:var(--card);border:1px solid var(--border);border-radius:16px;display:flex;flex-direction:column;height:100%;overflow:hidden}
.cart-head{padding:16px 18px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}
.cart-head-title{font-weight:800;font-size:.95rem}
.cart-head-count{background:var(--lime);color:#000;font-size:.68rem;font-weight:800;padding:2px 8px;border-radius:99px}
.cart-body{flex:1;overflow-y:auto;padding:10px 14px}
.cart-body::-webkit-scrollbar{width:3px}
.cart-body::-webkit-scrollbar-thumb{background:var(--border2);border-radius:99px}
.cart-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;color:var(--muted);gap:8px;font-size:.84rem}
.cart-empty-icon{font-size:2rem;opacity:.3}
.cart-item{display:flex;align-items:center;gap:9px;padding:9px 4px;border-bottom:1px solid var(--border)50}
.cart-item:last-child{border-bottom:none}
.ci-name{flex:1;font-size:.82rem;font-weight:600;line-height:1.3}
.ci-qty{display:flex;align-items:center;gap:5px}
.qty-btn{width:24px;height:24px;border-radius:6px;border:1px solid var(--border2);background:var(--card2);color:var(--text);cursor:pointer;font-size:.9rem;display:flex;align-items:center;justify-content:center;transition:.15s}
.qty-btn:hover{border-color:var(--purple);background:var(--card)}
.ci-price{font-size:.82rem;font-weight:700;color:var(--lime);font-family:'Space Mono',monospace;min-width:62px;text-align:right}
.rm-btn{background:none;border:none;color:var(--red);cursor:pointer;font-size:.95rem;opacity:.5;transition:.15s;padding:2px}
.rm-btn:hover{opacity:1}
.cart-foot{padding:16px 18px;border-top:1px solid var(--border)}
.cart-total-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
.cart-total-label{font-size:.78rem;color:var(--muted);text-transform:uppercase;letter-spacing:.8px}
.cart-total-val{font-size:1.5rem;font-weight:800;color:var(--lime);font-family:'Space Mono',monospace}
.pay-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px}
.btn-cash{background:var(--purple);color:#fff;padding:12px;border-radius:10px;border:none;font-weight:800;font-size:.88rem;cursor:pointer;font-family:'Sora',sans-serif;transition:.2s}
.btn-cash:hover{background:#6a58e3}
.btn-mpesa{background:var(--lime);color:#000;padding:12px;border-radius:10px;border:none;font-weight:800;font-size:.88rem;cursor:pointer;font-family:'Sora',sans-serif;transition:.2s}
.btn-mpesa:hover{background:var(--lime2)}
.btn-clear{width:100%;background:transparent;border:1px solid var(--border2);color:var(--muted);padding:8px;border-radius:9px;font-size:.76rem;cursor:pointer;font-family:'Sora',sans-serif;transition:.2s}
.btn-clear:hover{border-color:var(--red);color:var(--red)}

/* ── SEARCH BAR ── */
.search-wrap{position:relative}
.search-input{width:100%;background:var(--card);border:1px solid var(--border);color:var(--text);padding:11px 14px 11px 40px;border-radius:11px;font-size:.86rem;font-family:'Sora',sans-serif;transition:.2s}
.search-input:focus{outline:none;border-color:var(--purple);background:var(--card2)}
.search-icon{position:absolute;left:13px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:.9rem;pointer-events:none}

/* ── MODAL ── */
.modal-bg{display:none;position:fixed;inset:0;background:#00000099;z-index:500;align-items:center;justify-content:center;backdrop-filter:blur(4px)}
.modal-bg.show{display:flex}
.modal-box{background:var(--card2);border:1px solid var(--border2);border-radius:18px;padding:28px;width:460px;max-width:95vw;max-height:90vh;overflow-y:auto;animation:modalIn .2s ease}
@keyframes modalIn{from{opacity:0;transform:scale(.95) translateY(10px)}to{opacity:1;transform:scale(1) translateY(0)}}
@keyframes payPop{0%{opacity:0;transform:scale(.7) translateY(30px)}70%{transform:scale(1.04) translateY(-4px)}100%{opacity:1;transform:scale(1) translateY(0)}}
@keyframes shake{0%,100%{transform:translateX(0)}15%{transform:translateX(-12px)}30%{transform:translateX(10px)}45%{transform:translateX(-8px)}60%{transform:translateX(6px)}75%{transform:translateX(-4px)}90%{transform:translateX(2px)}}
.modal-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:22px}
.modal-title{font-size:1rem;font-weight:800}
.modal-close{background:none;border:1px solid var(--border2);color:var(--muted);width:30px;height:30px;border-radius:7px;cursor:pointer;font-size:1rem;display:flex;align-items:center;justify-content:center;transition:.15s}
.modal-close:hover{border-color:var(--red);color:var(--red)}

/* ── RECEIPT ── */
.receipt{background:#fff;color:#111;padding:22px;border-radius:10px;font-family:'Space Mono',monospace;font-size:.78rem}
.receipt-head{text-align:center;padding-bottom:12px;margin-bottom:12px;border-bottom:2px dashed #ddd}
.receipt-shop{font-size:1rem;font-weight:700;margin-bottom:3px}
.r-row{display:flex;justify-content:space-between;margin:4px 0}
.r-divider{border:none;border-top:2px dashed #ddd;margin:10px 0}
.r-total{font-weight:700;font-size:.9rem}
.receipt-foot{text-align:center;margin-top:12px;font-size:.7rem;color:#888;border-top:1px dashed #ddd;padding-top:10px}

/* ── CHAT ── */
.chat-layout{display:grid;grid-template-columns:240px 1fr;gap:16px;height:calc(100vh - 54px)}
@media(max-width:900px){.chat-layout{grid-template-columns:1fr}}
.chat-panel{background:var(--card);border:1px solid var(--border);border-radius:16px;display:flex;flex-direction:column;overflow:hidden}
.chat-messages{flex:1;overflow-y:auto;padding:18px;display:flex;flex-direction:column;gap:12px}
.chat-messages::-webkit-scrollbar{width:3px}
.chat-messages::-webkit-scrollbar-thumb{background:var(--border2);border-radius:99px}
.msg{max-width:84%;padding:11px 15px;border-radius:14px;font-size:.84rem;line-height:1.55;white-space:pre-wrap}
.msg.user{background:var(--purple);align-self:flex-end;border-bottom-right-radius:4px}
.msg.bot{background:var(--card2);border:1px solid var(--border);align-self:flex-start;border-bottom-left-radius:4px}
.msg-time{font-size:.66rem;opacity:.45;margin-top:4px;font-family:'Space Mono',monospace}
.chat-inp-area{padding:14px;border-top:1px solid var(--border);display:flex;gap:9px}
.chat-inp-area input{flex:1}

.chips-panel{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:18px;align-self:start}
.chips-title{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:12px}
.chip{display:block;width:100%;text-align:left;background:var(--card2);border:1px solid var(--border2);border-radius:9px;padding:9px 12px;font-size:.78rem;cursor:pointer;color:var(--muted2);font-family:'Sora',sans-serif;transition:.2s;margin-bottom:6px}
.chip:hover{border-color:var(--purple);color:var(--text);background:var(--card)}
.chip:last-child{margin-bottom:0}

/* ── TOAST ── */
.toast{position:fixed;bottom:24px;right:24px;background:var(--card2);border:1px solid var(--border2);border-radius:11px;padding:12px 18px;font-size:.84rem;z-index:999;opacity:0;transform:translateY(8px);transition:.3s;pointer-events:none;display:flex;align-items:center;gap:8px}
.toast.show{opacity:1;transform:translateY(0)}
.toast.success{border-color:var(--green);color:var(--green)}
.toast.error{border-color:var(--red);color:var(--red)}

/* ── CHART OVERRIDES ── */
canvas{display:block}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
</style>"""

# ── NAV SIDEBAR PARTIAL ──
def sidebar(active):
    links = [
        ("p","🛒","POS Checkout","Checkout"),
        ("a","✅","Automated Checkout","Automated Checkout"),
        ("i","📦","Inventory","Inventory"),
        ("o","📊","Overview","Overview"),
        ("c","💬","Ask AI","Ask AI"),
    ]
    items = ""
    for key, icon, href_map, label in links:
        page_url = {"o":"/overview","p":"/pos","a":"/auto_checkout","i":"/inventory","c":"/chat"}[key]
        cls = "s-link active" if active == key else "s-link"
        items += f'<a href="{page_url}" class="{cls}"><span class="s-icon">{icon}</span>{label}</a>\n'
    return f"""
<div class="sidebar">
  <div class="s-brand">
    <div class="s-brand-icon">🏪</div>
    <div><div class="s-brand-name">My Duka</div><div class="s-brand-sub">POS Dashboard</div></div>
  </div>
  <div class="s-section">Main Menu</div>
  {items}
  <div class="s-bottom">
    <div class="s-time" id="clk">--:--:--</div>
    <a href="/logout"><button class="s-logout">⎋ &nbsp;Logout</button></a>
  </div>
</div>
<script>(function t(){{const el=document.getElementById('clk');if(el)el.textContent=new Date().toLocaleTimeString('en-KE');setTimeout(t,1000);}})();</script>
"""

# ─────────────────────────────────────────
# PAGE TEMPLATES
# ─────────────────────────────────────────

def render_page(sidebar_html, content, extra_head=""):
    html = ("<!DOCTYPE html><html lang='en'><head>"
        "<meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>My Duka POS</title>"
        + CSS + extra_head +
        "</head><body>"
        + sidebar_html +
        "<div class='main'>" + content + "</div>"
        "<div class='toast' id='toast'></div>"
        "<script>"
        "function showToast(msg,type){"
        "const t=document.getElementById('toast');"
        "t.textContent=msg;t.className='toast show '+(type||'success');"
        "setTimeout(()=>t.className='toast',2800);}"
        "</script>"
        "</body></html>")
    resp = make_response(html)
    # Prevent stale cached JS/HTML causing old polling behavior
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp

def overview_content(s, p, bs, stock, ch, it, recent):
    now = datetime.now().strftime("%A, %d %B %Y")
    margin = f"{p['today']/s['today']*100:.0f}" if s['today'] else "0"

    # Metric cards
    metrics = f"""
<div class="metric-grid">
  <div class="metric-card lime">
    <div class="metric-icon">💰</div>
    <div class="metric-label">Revenue Today</div>
    <div class="metric-value lime">KES {s['today']:,.0f}</div>
    <div class="metric-sub">{s['txn_today']:.0f} transactions · {s['items_today']:.0f} items sold</div>
  </div>
  <div class="metric-card green">
    <div class="metric-icon">📈</div>
    <div class="metric-label">Profit Today</div>
    <div class="metric-value green">KES {p['today']:,.0f}</div>
    <div class="metric-sub">Margin: {margin}%</div>
  </div>
  <div class="metric-card purple">
    <div class="metric-icon">📅</div>
    <div class="metric-label">This Week</div>
    <div class="metric-value purple">KES {s['week']:,.0f}</div>
    <div class="metric-sub">Profit: KES {p['week']:,.0f}</div>
  </div>
  <div class="metric-card amber">
    <div class="metric-icon">🗓️</div>
    <div class="metric-label">This Month</div>
    <div class="metric-value amber">KES {s['month']:,.0f}</div>
    <div class="metric-sub">Profit: KES {p['month']:,.0f}</div>
  </div>
  <div class="metric-card blue">
    <div class="metric-icon">🏆</div>
    <div class="metric-label">All Time</div>
    <div class="metric-value blue">KES {s['all_time']:,.0f}</div>
    <div class="metric-sub">Total profit: KES {p['all_time']:,.0f}</div>
  </div>
</div>"""

    # Best sellers rows
    bs_rows = ""
    colors = ["var(--lime)","var(--purple2)","var(--green)","var(--amber)","var(--blue)","var(--red)"]
    for i, b in enumerate(bs):
        pct = int(b['qty'] / max(bs[0]['qty'], 1) * 100) if bs else 0
        color = colors[i % len(colors)]
        bs_rows += f"""<tr>
          <td style="color:var(--muted);font-family:'Space Mono',monospace;font-size:.72rem">{i+1:02d}</td>
          <td><span style="font-weight:700">{b['name']}</span></td>
          <td>
            <div style="display:flex;align-items:center;gap:8px">
              <div style="flex:1;height:4px;background:var(--border2);border-radius:99px;max-width:80px">
                <div style="width:{pct}%;height:4px;border-radius:99px;background:{color}"></div>
              </div>
              <span style="font-family:'Space Mono',monospace;font-size:.76rem;color:{color}">{b['qty']}</span>
            </div>
          </td>
          <td style="font-family:'Space Mono',monospace;font-size:.78rem;color:var(--muted2)">KES {b['revenue']:,.0f}</td>
        </tr>"""
    if not bs:
        bs_rows = '<tr><td colspan="4" style="text-align:center;padding:20px;color:var(--muted)">No sales yet</td></tr>'

    # Stock rows
    stock_rows = ""
    for it_item in stock[:10]:
        qty = it_item['stock_quantity']
        pct = min(qty * 3, 100)
        bar_color = "var(--red)" if qty < 5 else ("var(--amber)" if qty < 15 else "var(--green)")
        badge = f'<span class="badge badge-red">Urgent</span>' if qty < 5 else \
                (f'<span class="badge badge-amber">Low</span>' if qty < 15 else \
                 f'<span class="badge badge-green">OK</span>')
        stock_rows += f"""<tr>
          <td><span style="font-weight:600">{it_item['item_name']}</span></td>
          <td style="font-family:'Space Mono',monospace;font-size:.78rem">KES {it_item['selling_price']:,.0f}</td>
          <td>
            <div class="stock-bar-wrap">
              <span style="font-family:'Space Mono',monospace;font-size:.76rem;min-width:28px">{qty}</span>
              <div class="stock-bar-bg"><div class="stock-bar-fill" style="width:{pct}%;background:{bar_color}"></div></div>
            </div>
          </td>
          <td>{badge}</td>
        </tr>"""

    # Recent sales rows
    recent_rows = ""
    for r in recent:
        pay_badge = '<span class="badge badge-green">M-Pesa</span>' if r['payment_method']=='mpesa' else '<span class="badge badge-blue">Cash</span>'
        recent_rows += f"""<tr>
          <td><span style="font-weight:600">{r['item_name']}</span></td>
          <td style="color:var(--muted)">{r['quantity_sold']}</td>
          <td style="font-family:'Space Mono',monospace;font-size:.78rem;color:var(--lime)">KES {r['total_price']:,.0f}</td>
          <td>{pay_badge}</td>
          <td style="color:var(--muted);font-size:.76rem">{r['sale_timestamp']}</td>
        </tr>"""
    if not recent:
        recent_rows = '<tr><td colspan="5" style="text-align:center;padding:20px;color:var(--muted)">No sales yet</td></tr>'

    chart_labels = json.dumps(ch['labels'])
    chart_revenue = json.dumps(ch['revenue'])
    items_labels = json.dumps(it['labels'])
    items_qty = json.dumps(it['quantities'])

    return f"""
<div class="page-wrap">
  <div class="page-header">
    <div><div class="page-title">Good day, Philex 👋</div><div class="page-subtitle">Here's what's happening at your shop today</div></div>
    <div class="page-date">{now}</div>
  </div>
  {metrics}
  <div class="grid-2">
    <div class="card-box">
      <div class="card-box-title">📈 Revenue — Last 7 Days</div>
      <canvas id="rc" height="180"></canvas>
    </div>
    <div class="card-box">
      <div class="card-box-title">📦 Items Sold Today <span>by product</span></div>
      <canvas id="dc" height="180"></canvas>
    </div>
  </div>
  <div class="grid-2">
    <div class="card-box">
      <div class="card-box-title">🏆 Best Sellers <span>this month</span></div>
      <table class="data-table">
        <tr><th>#</th><th>Item</th><th>Units Sold</th><th>Revenue</th></tr>
        {bs_rows}
      </table>
    </div>
    <div class="card-box">
      <div class="card-box-title">📦 Stock Levels</div>
      <table class="data-table">
        <tr><th>Item</th><th>Price</th><th>Stock</th><th>Status</th></tr>
        {stock_rows}
      </table>
    </div>
  </div>
  <div class="card-box">
    <div class="card-box-title">🕐 Recent Sales</div>
    <table class="data-table">
      <tr><th>Item</th><th>Qty</th><th>Amount</th><th>Payment</th><th>Time</th></tr>
      {recent_rows}
    </table>
  </div>
  <p style="text-align:center;font-size:.68rem;color:var(--muted);padding:18px 0 0">Auto-refreshes every 30 seconds</p>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<script>
setTimeout(()=>location.reload(),30000);
Chart.defaults.color='#6b6b8a';
Chart.defaults.borderColor='#1e1e35';
new Chart(document.getElementById('rc'),{{type:'bar',data:{{labels:{chart_labels},datasets:[{{data:{chart_revenue},backgroundColor:'rgba(200,241,53,0.15)',borderColor:'#c8f135',borderWidth:2,borderRadius:8,hoverBackgroundColor:'rgba(200,241,53,0.3)'}}]}},options:{{responsive:true,plugins:{{legend:{{display:false}}}},scales:{{x:{{grid:{{color:'#1e1e3520'}}}},y:{{grid:{{color:'#1e1e3530'}}}}}}}}));
new Chart(document.getElementById('dc'),{{type:'doughnut',data:{{labels:{items_labels},datasets:[{{data:{items_qty},backgroundColor:['#c8f135','#7c6af5','#22d3a0','#fbbf24','#60a5fa','#f0566a'],borderWidth:0,hoverOffset:6}}]}},options:{{responsive:true,cutout:'68%',plugins:{{legend:{{position:'right',labels:{{boxWidth:10,padding:12,color:'#9090b0'}}}}}}}}}}));
</script>"""

# ─────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────
def auth(f):
    @wraps(f)
    def dec(*a,**kw):
        if not session.get("logged_in"): return redirect("/login")
        return f(*a,**kw)
    return dec

LOGIN_PAGE = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>My Duka POS</title>{CSS}</head>
<body style="display:flex;align-items:center;justify-content:center;min-height:100vh;overflow:auto">
<div style="width:360px;text-align:center">
  <div style="width:60px;height:60px;background:var(--lime);border-radius:16px;display:flex;align-items:center;justify-content:center;font-size:1.8rem;margin:0 auto 20px">🏪</div>
  <div style="font-size:1.6rem;font-weight:800;letter-spacing:-0.5px;margin-bottom:6px">My Duka POS</div>
  <div style="color:var(--muted);font-size:.82rem;margin-bottom:32px">Enter your dashboard password to continue</div>
  {{error_block}}
  <form method="POST">
    <div class="form-field">
      <input class="form-input" type="password" name="password" placeholder="Password" autofocus style="text-align:center;font-size:1rem;padding:13px">
    </div>
    <button type="submit" class="btn btn-lime btn-full" style="padding:13px;font-size:.95rem">Open Dashboard →</button>
  </form>
  <p style="color:var(--muted);font-size:.72rem;margin-top:20px">My Duka POS · Powered by Philex</p>
</div>
</body></html>"""

# ─────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        if request.form.get("password")==DASHBOARD_PASSWORD:
            session["logged_in"]=True; return redirect("/pos")
        err = '<div style="background:#2d0812;border:1px solid #4a1020;color:var(--red);padding:10px 14px;border-radius:9px;font-size:.82rem;margin-bottom:16px">Wrong password. Try again.</div>'
        return make_response(LOGIN_PAGE.replace("{error_block}", err))
    return make_response(LOGIN_PAGE.replace("{error_block}", ""))

@app.route("/logout")
def logout():
    session.clear(); return redirect("/login")

@app.route("/")
def index():
    """Root — redirect to pos if logged in, else login."""
    if session.get("logged_in"):
        return redirect("/pos")
    return redirect("/login")

@app.route("/overview")
@auth
def overview():
    try:
        content = overview_content(get_summary(),get_profit_summary(),get_best_sellers(),
            get_stock_levels(),get_daily_chart(),get_items_today(),get_recent_sales())
        return render_page(sidebar("o"), content)
    except Exception as e:
        return f"<pre style='color:red;padding:20px'>Error: {e}</pre>"

@app.route("/pos")
@auth
def pos():
    content = """
<div class="page-wrap" style="padding-bottom:0;height:calc(100vh - 0px)">
<div class="pos-layout" style="height:calc(100vh - 56px)">
  <div class="pos-left">
    <div style="display:flex;gap:10px;flex-shrink:0">
      <div class="search-wrap" style="flex:1">
        <span class="search-icon">🔍</span>
        <input class="search-input" id="srch" type="text" placeholder="Search items by name..." oninput="filterItems(this.value)" autofocus>
      </div>
      <input class="form-input" id="hwScanPos" type="text" inputmode="none"
             placeholder="⌨ Scanner (click then scan)"
             style="width:220px;display:none"
             onkeydown="return hwScanKeydown(event,'pos')">
      <button class="btn btn-ghost" onclick="toggleHwScan('pos')" style="flex-shrink:0">⌨ Scanner</button>
      <button class="btn btn-ghost" onclick="openScan()" style="flex-shrink:0">📷 Scan</button>
    </div>
    <div class="items-scroll" id="grid"></div>
  </div>

  <div class="cart-panel">
    <div class="cart-head">
      <span class="cart-head-title">🛒 Cart</span>
      <span class="cart-head-count" id="cnt" style="display:none">0</span>
    </div>
    <div class="cart-body" id="cartEl">
      <div class="cart-empty"><div class="cart-empty-icon">🛒</div>Cart is empty<small style="color:var(--muted)">Tap items to add them</small></div>
    </div>
    <div class="cart-foot">
      <div class="cart-total-row">
        <span class="cart-total-label">Total</span>
        <span class="cart-total-val" id="tot">KES 0</span>
      </div>
      <div class="pay-grid">
        <button class="btn-cash" onclick="pay('cash')">💵 Cash</button>
        <button class="btn-mpesa" onclick="openMpesa()">📱 M-Pesa</button>
      </div>
      <button class="btn-clear" onclick="clearCart()">✕ Clear cart</button>
    </div>
  </div>
</div>
</div>

<!-- Mpesa Modal -->
<div class="modal-bg" id="mpMdl">
  <div class="modal-box" style="width:420px">
    <div class="modal-header"><div class="modal-title">📱 M-Pesa Payment</div><button class="modal-close" onclick="closeMdl('mpMdl')">✕</button></div>
    <div class="form-field"><label class="form-label">Customer Phone Number</label><input class="form-input" type="text" id="mpPhone" placeholder="0724022458"></div>
    <div id="mpSum" style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:16px;font-size:.84rem"></div>
    <button class="btn btn-lime btn-full" onclick="sendMpesa()">Send STK Push →</button>
  </div>
</div>

<!-- Receipt Modal -->
<div class="modal-bg" id="rcMdl">
  <div class="modal-box" style="width:380px">
    <div class="modal-header"><div class="modal-title">🧾 Receipt</div><button class="modal-close" onclick="closeMdl('rcMdl')">✕</button></div>
    <div id="rcContent"></div>
    <button class="btn btn-ghost btn-full" style="margin-top:14px" onclick="window.print()">🖨️ Print Receipt</button>
  </div>
</div>

<!-- Scanner Modal -->
<div class="modal-bg" id="scanMdl">
  <div class="modal-box" style="width:460px">
    <div class="modal-header"><div class="modal-title">📷 Scan Barcode / QR Code</div><button class="modal-close" onclick="closeScan()">✕</button></div>
    <div id="reader" style="border-radius:10px;overflow:hidden;margin-bottom:10px"></div>
    <p style="font-size:.75rem;color:var(--muted);text-align:center">Point camera at barcode or QR code</p>
  </div>
</div>

<!-- Payment Result Modal -->
<div id="payResultMdl" style="display:none;position:fixed;inset:0;z-index:9999;align-items:center;justify-content:center;backdrop-filter:blur(8px)">
  <div id="payResultBg" style="position:absolute;inset:0"></div>
  <div id="payResultBox" style="position:relative;width:440px;max-width:96vw;border-radius:24px;padding:44px 36px;text-align:center;animation:payPop .35s cubic-bezier(.34,1.56,.64,1)">
    <div id="payResultRing" style="width:100px;height:100px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:3.2rem;margin:0 auto 22px;position:relative"></div>
    <div id="payResultTitle" style="font-size:1.8rem;font-weight:900;margin-bottom:10px;letter-spacing:-0.5px"></div>
    <div id="payResultSub" style="font-size:.95rem;line-height:1.7;margin-bottom:10px"></div>
    <div id="payResultCode" style="font-family:Space Mono,monospace;font-size:1rem;font-weight:700;margin-bottom:28px;padding:10px 16px;border-radius:10px;display:inline-block"></div>
    <button id="payResultBtn" onclick="closePayResult()" style="width:100%;padding:14px;border-radius:12px;border:none;font-size:1rem;font-weight:800;cursor:pointer;font-family:Sora,sans-serif;letter-spacing:.3px"></button>
  </div>
  <canvas id="confettiCanvas" style="position:fixed;inset:0;pointer-events:none;z-index:10000"></canvas>
</div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/html5-qrcode/2.3.8/html5-qrcode.min.js"></script>
<script>
let cart=[],allItems=[],scanner=null;
const HW_SCAN_CFG = {
  suffixKeys: ['Enter','Tab'],
  minLen: 3,
  maxInterKeyMs: 60
};
let _hwLastTs = 0;

function toggleHwScan(which){
  const id = which==='pos' ? 'hwScanPos' : 'hwScanInv';
  const el = document.getElementById(id);
  if(!el) return;
  const show = el.style.display === 'none';
  el.style.display = show ? '' : 'none';
  if(show){
    el.value='';
    setTimeout(()=>{ try{ el.focus(); }catch(e){} }, 50);
    showToast('Scanner ready. Scan now.');
  }
}

function hwScanKeydown(ev, mode){
  const k = ev.key;
  const now = Date.now();
  const input = ev.target;
  if(_hwLastTs && (now - _hwLastTs) > HW_SCAN_CFG.maxInterKeyMs){
    input.value = '';
  }
  _hwLastTs = now;

  if(HW_SCAN_CFG.suffixKeys.includes(k)){
    ev.preventDefault();
    const code = (input.value||'').trim();
    input.value = '';
    if(code.length < HW_SCAN_CFG.minLen){
      showToast('Scan too short','error');
      return false;
    }
    if(mode==='pos') handlePosBarcode(code);
    if(mode==='inv') handleInvBarcode(code);
    return false;
  }
  return true;
}

function handlePosBarcode(code){
  // Same behavior as camera scan
  fetch('/api/item/barcode/'+encodeURIComponent(code))
    .then(r=>r.json().then(j=>({ok:r.ok, j})))
    .then(({ok,j})=>{
      if(ok && j.ok && j.item){
        const it=j.item;
        add(it.item_id, it.item_name, it.selling_price, it.stock_quantity);
        showToast('Added: '+it.item_name);
      } else {
        document.getElementById('srch').value=code;
        filterItems(code);
        showToast('Scanned: '+code);
      }
    })
    .catch(()=>{
      document.getElementById('srch').value=code;
      filterItems(code);
      showToast('Scanned: '+code);
    });
}

async function loadItems(){
  try{
    const r=await fetch('/api/items');
    if(!r.ok){
      const t=await r.text().catch(()=> '');
      showToast('Failed to load items (server error)','error');
      console.error('api/items error',r.status,t);
      allItems=[];renderGrid(allItems);
      return;
    }
    allItems=await r.json();
    if(!Array.isArray(allItems)) allItems=[];
    renderGrid(allItems);
  }catch(e){
    showToast('Cannot reach server / database','error');
    console.error(e);
    allItems=[];renderGrid(allItems);
  }
}
function filterItems(q){renderGrid(q?allItems.filter(i=>i.item_name.toLowerCase().includes(q.toLowerCase())):allItems);}
function renderGrid(items){
  const g=document.getElementById('grid');
  if(!items.length){g.innerHTML='<div style="grid-column:1/-1;text-align:center;padding:40px;color:var(--muted)">No items found</div>';return;}
  g.innerHTML=items.map(it=>`
    <div class="item-tile${it.stock_quantity<=0?' oos':''}" onclick="${it.stock_quantity>0?`add(${it.item_id},'${it.item_name.replace(/'/g,"\\\\'")}',${it.selling_price},${it.stock_quantity})`:''}">
      <div class="item-tile-name">${it.item_name}</div>
      <div class="item-tile-price">KES ${it.selling_price.toFixed(0)}</div>
      <div class="item-tile-stock">${it.stock_quantity>0?it.stock_quantity+' units left':'⚠ Out of stock'}</div>
    </div>`).join('');
}
function add(id,name,price,stock){
  const e=cart.find(c=>c.id===id);
  if(e){if(e.q>=stock){showToast('Not enough stock!','error');return;}e.q++;}
  else cart.push({id,name,price,q:1,stock});
  renderCart();showToast(name+' added');
}
function renderCart(){
  const el=document.getElementById('cartEl'),cnt=document.getElementById('cnt');
  if(!cart.length){
    el.innerHTML='<div class="cart-empty"><div class="cart-empty-icon">🛒</div>Cart is empty<small style="color:var(--muted)">Tap items to add them</small></div>';
    document.getElementById('tot').textContent='KES 0';cnt.style.display='none';return;
  }
  el.innerHTML=cart.map((c,i)=>`
    <div class="cart-item">
      <span class="ci-name">${c.name}</span>
      <div class="ci-qty">
        <button class="qty-btn" onclick="chg(${i},-1)">−</button>
        <span style="min-width:18px;text-align:center;font-size:.84rem;font-weight:700">${c.q}</span>
        <button class="qty-btn" onclick="chg(${i},1)">+</button>
      </div>
      <span class="ci-price">KES ${(c.price*c.q).toFixed(0)}</span>
      <button class="rm-btn" onclick="rm(${i})">✕</button>
    </div>`).join('');
  const total=cart.reduce((s,c)=>s+c.price*c.q,0);
  const totalItems=cart.reduce((s,c)=>s+c.q,0);
  document.getElementById('tot').textContent='KES '+total.toFixed(0);
  cnt.textContent=totalItems;cnt.style.display='inline';
}
function chg(i,d){cart[i].q=Math.max(1,Math.min(cart[i].stock,cart[i].q+d));renderCart();}
function rm(i){cart.splice(i,1);renderCart();}
function clearCart(){cart=[];renderCart();}
async function pay(method){
  if(!cart.length){showToast('Cart is empty!','error');return;}
  const r=await fetch('/api/sale',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({cart:cart.map(c=>({item_id:c.id,qty:c.q})),payment_method:method})});
  const d=await r.json();
  if(d.ok){showReceipt(d.receipt);clearCart();loadItems();showToast('Sale recorded!');}
  else showToast(d.error,'error');
}
function openMpesa(){
  if(!cart.length){showToast('Cart is empty!','error');return;}
  const total=cart.reduce((s,c)=>s+c.price*c.q,0);
  document.getElementById('mpSum').innerHTML=
    '<div style="font-weight:700;margin-bottom:6px">'+cart.map(c=>c.q+'× '+c.name).join(', ')+'</div>'+
    '<div style="color:var(--lime);font-size:1.1rem;font-weight:800;font-family:Space Mono,monospace">Total: KES '+total.toFixed(0)+'</div>';
  document.getElementById('mpMdl').classList.add('show');
}
async function sendMpesa(){
  const phone=document.getElementById('mpPhone').value.trim();
  if(!phone){showToast('Enter phone number','error');return;}
  const total=cart.reduce((s,c)=>s+c.price*c.q,0);
  const cartSnapshot=[...cart]; // save cart before clearing

  // Show waiting state
  document.getElementById('mpSum').innerHTML=
    '<div style="text-align:center;padding:10px">' +
    '<div style="font-size:1.5rem;margin-bottom:8px">📱</div>' +
    '<div style="font-weight:700;margin-bottom:4px">Sending STK Push...</div>' +
    '<div style="color:var(--muted);font-size:.8rem">Please wait</div></div>';

  const r=await fetch('/api/mpesa',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({phone,amount:total,items:cart.map(c=>c.name).join(', '),cart:cart.map(c=>({item_id:c.id,qty:c.q}))})});
  const d=await r.json();

  if(!d.ok){
    closeMdl('mpMdl');
    showToast('M-Pesa error: '+(d.error||'Unknown error'),'error');
    return;
  }

  // Show waiting for PIN screen
  document.getElementById('mpSum').innerHTML=
    '<div style="text-align:center;padding:12px">' +
    '<div style="font-size:2rem;margin-bottom:10px;animation:pulse 1.5s infinite">📲</div>' +
    '<div style="font-weight:800;font-size:1rem;margin-bottom:6px;color:var(--lime)">STK Push Sent!</div>' +
    '<div style="color:var(--text);margin-bottom:4px">Waiting for <strong>'+phone+'</strong></div>' +
    '<div style="color:var(--muted);font-size:.8rem">Ask customer to enter M-Pesa PIN...</div>' +
    '<div style="margin-top:12px;font-size:.75rem;color:var(--muted)" id="pollStatus">Checking payment status...</div>' +
    '</div>';
  document.querySelector('#mpMdl .btn-lime').style.display='none'; // hide send button

  // Poll for payment confirmation
  const checkoutId = d.checkout_id;
  let attempts = 0;
  const maxAttempts = 40; // 2 minutes max

  const poll = setInterval(async ()=>{
    attempts++;
    if(attempts > maxAttempts){
      clearInterval(poll);
      closeMdl('mpMdl');
      document.querySelector('#mpMdl .btn-lime').style.display='';
      showPayResult('failed', null, total, phone, []);
      return;
    }

    document.getElementById('pollStatus').textContent =
      'Checking... ('+attempts+'/'+maxAttempts+')';

    if(!checkoutId){
      // No checkout_id means we can't poll status reliably.
      // Show a clear failure after a short wait (common root cause: backend didn't return checkout_id).
      if(attempts===10){
        clearInterval(poll);
        closeMdl('mpMdl');
        document.querySelector('#mpMdl .btn-lime').style.display='';
        showPayResult('failed', null, total, phone, []);
        clearCart(); loadItems();
      }
      return;
    }

    try{
      const sr=await fetch('/api/mpesa/status/'+checkoutId);
      const sd=await sr.json();

      if(sd.status==='Completed'){
        clearInterval(poll);
        closeMdl('mpMdl');
        document.querySelector('#mpMdl .btn-lime').style.display='';
        clearCart(); loadItems();
        // Show on-screen success result
        showPayResult('success', sd.mpesa_code, total, phone,
          cartSnapshot.map(c=>({name:c.name,qty:c.q,price:c.price*c.q,unit:c.price})));

      } else if(sd.status==='Failed'){
        clearInterval(poll);
        closeMdl('mpMdl');
        document.querySelector('#mpMdl .btn-lime').style.display='';
        // Show on-screen failed result
        showPayResult('failed', null, total, phone, [], sd.fail_reason);
      }
    }catch(e){ /* keep polling */ }
  }, 3000); // check every 3 seconds
}
function showReceipt(rc){
  document.getElementById('rcContent').innerHTML=`
    <div class="receipt">
      <div class="receipt-head"><div class="receipt-shop">🏪 My Duka</div><div>${rc.time}</div></div>
      ${rc.items.map(i=>'<div class="r-row"><span>'+i.name+' ×'+i.qty+'</span><span>KES '+i.price.toFixed(0)+'</span></div>').join('')}
      <hr class="r-divider">
      <div class="r-row r-total"><span>TOTAL</span><span>KES ${rc.total.toFixed(0)}</span></div>
      <div class="r-row" style="margin-top:4px"><span>Payment</span><span>${rc.payment.toUpperCase()}</span></div>
      ${rc.phone&&rc.phone!=='cash'?'<div class="r-row"><span>Phone</span><span>'+rc.phone+'</span></div>':''}
      ${rc.mpesa_code?'<div class="r-row"><span>M-Pesa Code</span><span style="font-weight:700">'+rc.mpesa_code+'</span></div>':''}
      <div class="receipt-foot">Thank you! · Asante kwa kununua!</div>
    </div>`;
  document.getElementById('rcMdl').classList.add('show');
}
function closeMdl(id){document.getElementById(id).classList.remove('show');}

// ── Payment Result Popup ──────────────────────────────────────────────────
function showPayResult(type, mpesaCode, total, phone, items, failReason){
  const isSuccess = type === 'success';
  const mdl = document.getElementById('payResultMdl');
  const box = document.getElementById('payResultBox');
  const bg  = document.getElementById('payResultBg');
  const ring= document.getElementById('payResultRing');
  const btn = document.getElementById('payResultBtn');

  if(isSuccess){
    bg.style.background   = 'rgba(0,0,0,0.75)';
    box.style.background  = 'linear-gradient(160deg,#0a1f0d 0%,#0d2010 100%)';
    box.style.border      = '2px solid #22d3a0';
    box.style.boxShadow   = '0 0 80px #22d3a055, 0 0 0 1px #22d3a033';
    ring.style.background = 'linear-gradient(135deg,#c8f135,#22d3a0)';
    ring.style.boxShadow  = '0 0 40px #22d3a066';
    ring.textContent      = '✓';
    document.getElementById('payResultTitle').style.color = '#c8f135';
    document.getElementById('payResultTitle').textContent = 'Payment Confirmed!';
    document.getElementById('payResultSub').style.color   = '#a0f0cc';
    document.getElementById('payResultSub').innerHTML     =
      'KES <span style="font-size:1.5rem;font-weight:900;color:#c8f135;font-family:Space Mono,monospace">'+total.toFixed(0)+
      '</span> received from <strong style="color:#fff">'+phone+'</strong>';
    const codeEl = document.getElementById('payResultCode');
    codeEl.textContent     = mpesaCode ? '🧾  Code: '+mpesaCode : '';
    codeEl.style.background= '#0e2e12';
    codeEl.style.color     = '#c8f135';
    codeEl.style.border    = '1px solid #22d3a044';
    codeEl.style.display   = mpesaCode ? 'inline-block' : 'none';
    btn.style.background   = 'linear-gradient(135deg,#c8f135,#22d3a0)';
    btn.style.color        = '#080812';
    btn.textContent        = '✓  Continue';
    launchConfetti();
  } else {
    bg.style.background   = 'rgba(0,0,0,0.82)';
    box.style.background  = 'linear-gradient(160deg,#1a0a0a 0%,#200d0d 100%)';
    box.style.border      = '2px solid #ef4444';
    box.style.boxShadow   = '0 0 60px #ef444433, 0 0 0 1px #ef444422';
    ring.style.background = 'linear-gradient(135deg,#ef4444,#b91c1c)';
    ring.style.boxShadow  = '0 0 30px #ef444466';
    ring.textContent      = '✕';
    document.getElementById('payResultTitle').style.color = '#ef4444';
    document.getElementById('payResultTitle').textContent = 'Payment Cancelled';
    document.getElementById('payResultSub').style.color   = '#fca5a5';
    const rsn = (failReason||'').toString().trim();
    document.getElementById('payResultSub').innerHTML     =
      (rsn
        ? ('Reason: <strong style=\"color:#fff\">'+rsn+'</strong><br>')
        : 'The customer <strong style="color:#fff">cancelled</strong> or did not enter their M-Pesa PIN.<br>') +
      '<span style="color:#888;font-size:.85rem">No money was deducted. You can retry.</span>';
    const codeEl = document.getElementById('payResultCode');
    codeEl.textContent  = '';
    codeEl.style.display= 'none';
    btn.style.background= 'linear-gradient(135deg,#ef4444,#b91c1c)';
    btn.style.color     = '#fff';
    btn.textContent     = '↩  Back to Cart';
    shakeBox();
  }
  mdl.style.display = 'flex';
  void mdl.offsetWidth; // force reflow for animation
  if(isSuccess && items.length){
    setTimeout(()=>showReceipt({items,total,payment:'M-Pesa',phone,
      time:new Date().toLocaleString('en-KE'),mpesa_code:mpesaCode}), 300);
  }
}
function closePayResult(){
  const mdl=document.getElementById('payResultMdl');
  mdl.style.opacity='0';
  mdl.style.transition='opacity .2s';
  setTimeout(()=>{mdl.style.display='none';mdl.style.opacity='';mdl.style.transition='';},200);
  stopConfetti();
}
function shakeBox(){
  const b=document.getElementById('payResultBox');
  b.style.animation='none';void b.offsetWidth;
  b.style.animation='shake .5s ease';
}
// ── Confetti ──────────────────────────────────────────────────────────────
let _confettiAnim=null,_confettiParts=[];
function launchConfetti(){
  const canvas=document.getElementById('confettiCanvas');
  canvas.width=window.innerWidth;canvas.height=window.innerHeight;
  const ctx=canvas.getContext('2d');
  const colors=['#c8f135','#22d3a0','#7c6af5','#ffffff','#fbbf24','#38bdf8'];
  _confettiParts=Array.from({length:120},()=>({
    x:Math.random()*canvas.width, y:Math.random()*canvas.height-canvas.height,
    r:Math.random()*7+3, c:colors[Math.floor(Math.random()*colors.length)],
    vx:(Math.random()-0.5)*4, vy:Math.random()*4+2,
    tilt:Math.random()*360, spin:Math.random()*6-3, alpha:1
  }));
  function draw(){
    ctx.clearRect(0,0,canvas.width,canvas.height);
    _confettiParts.forEach(p=>{
      p.y+=p.vy;p.x+=p.vx;p.tilt+=p.spin;p.alpha-=0.008;
      ctx.save();ctx.globalAlpha=Math.max(0,p.alpha);ctx.fillStyle=p.c;
      ctx.translate(p.x,p.y);ctx.rotate(p.tilt*Math.PI/180);
      ctx.fillRect(-p.r/2,-p.r/2,p.r,p.r*2.2);ctx.restore();
    });
    _confettiParts=_confettiParts.filter(p=>p.alpha>0);
    if(_confettiParts.length) _confettiAnim=requestAnimationFrame(draw);
    else ctx.clearRect(0,0,canvas.width,canvas.height);
  }
  draw();
}
function stopConfetti(){
  if(_confettiAnim){cancelAnimationFrame(_confettiAnim);_confettiAnim=null;}
  const canvas=document.getElementById('confettiCanvas');
  canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);
}
function openScan(){
  document.getElementById('scanMdl').classList.add('show');
  scanner=new Html5Qrcode("reader");
  scanner.start({facingMode:"environment"},{fps:10,qrbox:220},(code)=>{
    closeScan();
    const val = (code||'').toString().trim();
    if(!val){ showToast('Scan failed','error'); return; }
    // Try barcode lookup first; if found, add to cart for checkout.
    fetch('/api/item/barcode/'+encodeURIComponent(val))
      .then(r=>r.json().then(j=>({ok:r.ok, j})))
      .then(({ok,j})=>{
        if(ok && j.ok && j.item){
          const it=j.item;
          add(it.item_id, it.item_name, it.selling_price, it.stock_quantity);
          showToast('Added: '+it.item_name);
        } else {
          // Fallback: treat as search term (name search)
          document.getElementById('srch').value=val;
          filterItems(val);
          showToast('Scanned: '+val);
        }
      })
      .catch(()=>{
        document.getElementById('srch').value=val;
        filterItems(val);
        showToast('Scanned: '+val);
      });
  },()=>{});
}
function closeScan(){
  document.getElementById('scanMdl').classList.remove('show');
  if(scanner){scanner.stop().catch(()=>{});scanner=null;}
}
loadItems();
</script>"""
    return render_page(sidebar("p"), content)


@app.route("/auto_checkout")
@auth
def auto_checkout():
    content = """
<div class="page">
  <div class="topbar">
    <div>
      <div class="title">Automated Checkout</div>
      <div class="subtitle">Paid Telegram orders waiting for collection approval</div>
    </div>
  </div>

  <div class="card-box">
    <div class="card-box-title">Pending collection</div>
    <table class="data-table" id="ao_tbl">
      <tr><th>Order</th><th>Customer</th><th>Phone</th><th>M-Pesa</th><th>Items</th><th>Total</th><th>Paid</th><th></th></tr>
    </table>
    <div style="color:var(--muted);font-size:.75rem;margin-top:10px">Auto-refreshes every 5 seconds</div>
  </div>
</div>
<script>
async function loadAutoOrders(){
  const tbl=document.getElementById('ao_tbl');
  try{
    const r=await fetch('/api/auto_orders');
    const j=await r.json();
    const rows=j.orders||[];
    tbl.innerHTML='<tr><th>Order</th><th>Customer</th><th>Phone</th><th>M-Pesa</th><th>Items</th><th>Total</th><th>Paid</th><th></th></tr>';
    for(const o of rows){
      const items=(o.items||[]).map(x=>`${x.item_name} ×${x.qty}`).join('<br>');
      const tr=document.createElement('tr');
      tr.innerHTML = `
        <td>#${o.order_id}</td>
        <td>${o.customer||''}</td>
        <td>${o.phone||''}</td>
        <td>${o.mpesa_receipt||''}</td>
        <td>${items}</td>
        <td>KES ${Number(o.total_amount||0).toFixed(0)}</td>
        <td>${o.paid_at||''}</td>
        <td><button class="btn btn-lime" style="padding:8px 10px;font-size:.75rem" onclick="approveOrder(${o.order_id})">Approve collected</button></td>
      `;
      tbl.appendChild(tr);
    }
  }catch(e){
    showToast('Failed to load orders','error');
  }
}
async function approveOrder(orderId){
  if(!confirm('Approve this order as collected?')) return;
  const r=await fetch('/api/auto_orders/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({order_id:orderId})});
  const j=await r.json();
  if(j.ok){ showToast('Approved'); loadAutoOrders(); }
  else{ showToast(j.error||'Approve failed','error'); }
}
loadAutoOrders();
setInterval(loadAutoOrders, 5000);
</script>
"""
    return render_page(sidebar("a"), content)

@app.route("/inventory")
@auth
def inventory():
    stock = get_stock_levels()
    rows = ""
    for it in stock:
        qty = it['stock_quantity']
        badge = f'<span class="badge badge-red">Urgent</span>' if qty<5 else \
                (f'<span class="badge badge-amber">Low</span>' if qty<15 else \
                 f'<span class="badge badge-green">OK</span>')
        rows += f"""<tr>
          <td style="font-weight:700">{it['item_name']}</td>
          <td style="font-family:'Space Mono',monospace;font-size:.78rem;color:var(--lime)">KES {it['selling_price']:,.0f}</td>
          <td style="font-family:'Space Mono',monospace;font-size:.78rem;color:var(--muted2)">KES {it['cost_price']:,.0f}</td>
          <td style="font-family:'Space Mono',monospace;font-size:.78rem">{qty}</td>
          <td>{badge}</td>
          <td><button class="btn btn-ghost btn-sm" onclick="openEdit({it['item_id']},'{it['item_name'].replace(chr(39),chr(92)+chr(39))}',{it['selling_price']},{it['cost_price']},{qty},'{(it.get('barcode') or '').replace(chr(39),chr(92)+chr(39))}')">✏️ Edit</button></td>
        </tr>"""

    content = f"""
<div class="page-wrap">
  <div class="page-header">
    <div><div class="page-title">📦 Inventory</div><div class="page-subtitle">Manage your products and stock levels</div></div>
  </div>

  <div class="card-box" style="margin-bottom:16px">
    <div class="card-box-title">➕ Add New Item</div>
    <div class="form-row">
      <div><label class="form-label">Item Name</label><input class="form-input" type="text" id="nName" placeholder="e.g. Kabras Sugar 1kg"></div>
      <div><label class="form-label">Selling Price (KES)</label><input class="form-input" type="number" id="nSell" placeholder="150"></div>
      <div><label class="form-label">Cost Price (KES)</label><input class="form-input" type="number" id="nCost" placeholder="120"></div>
      <div><label class="form-label">Initial Stock</label><input class="form-input" type="number" id="nQty" placeholder="50"></div>
      <div>
        <label class="form-label">Barcode</label>
        <div style="display:flex;gap:8px;align-items:center">
          <input class="form-input" type="text" id="nBar" placeholder="Scan or type barcode">
          <button class="btn btn-ghost btn-sm" type="button" onclick="openAddScan()">📷</button>
        </div>
      </div>
      <div style="align-self:flex-end"><button class="btn btn-lime" onclick="addItem()">Add Item</button></div>
    </div>
  </div>

  <div style="margin-bottom:14px">
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <div class="search-wrap" style="max-width:400px;flex:1;min-width:260px">
        <span class="search-icon">🔍</span>
        <input class="search-input" type="text" id="invSrch" placeholder="Search inventory..." oninput="filterInv(this.value)">
      </div>
      <button class="btn btn-ghost" onclick="openInvScan()">📷 Scan Restock</button>
      <input class="form-input" id="hwScanInv" type="text" inputmode="none"
             placeholder="⌨ Scanner (click then scan)"
             style="width:220px;display:none"
             onkeydown="return hwScanKeydown(event,'inv')">
      <button class="btn btn-ghost" onclick="toggleHwScan('inv')">⌨ Scanner</button>
    </div>
  </div>

  <div class="card-box" style="margin-bottom:16px">
    <div class="card-box-title">🧾 Restock List <span>(scan items, then apply)</span></div>
    <div id="restockEmpty" style="color:var(--muted);font-size:.82rem;padding:6px 2px">No items scanned yet.</div>
    <div style="overflow:auto">
      <table class="data-table" id="restockTbl" style="display:none">
        <tr><th>Item</th><th>Barcode</th><th>Qty</th><th></th></tr>
        <tbody id="restockBody"></tbody>
      </table>
    </div>
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:12px">
      <button class="btn btn-ghost" onclick="clearRestock()">Clear</button>
      <button class="btn btn-lime" onclick="applyRestock()">Apply Restock</button>
    </div>
  </div>

  <div class="card-box">
    <div class="card-box-title">All Products <span>({len(stock)} items)</span></div>
    <table class="data-table" id="invTable">
      <tr><th>Item Name</th><th>Selling Price</th><th>Cost Price</th><th>Stock</th><th>Status</th><th>Action</th></tr>
      {rows}
    </table>
  </div>
</div>

<div class="modal-bg" id="editMdl">
  <div class="modal-box">
    <div class="modal-header"><div class="modal-title" id="editTitle">Edit Item</div><button class="modal-close" onclick="closeMdl('editMdl')">✕</button></div>
    <input type="hidden" id="eId">
    <div class="form-row">
      <div><label class="form-label">Selling Price (KES)</label><input class="form-input" type="number" id="eSell"></div>
      <div><label class="form-label">Cost Price (KES)</label><input class="form-input" type="number" id="eCost"></div>
      <div><label class="form-label">Stock Quantity</label><input class="form-input" type="number" id="eQty"></div>
      <div><label class="form-label">Barcode</label><input class="form-input" type="text" id="eBar" placeholder="Scan or type barcode"></div>
    </div>
    <button class="btn btn-lime btn-full" style="margin-top:6px" onclick="saveEdit()">Save Changes</button>
  </div>
</div>

<!-- Inventory Scan Restock Modal -->
<div class="modal-bg" id="invScanMdl">
  <div class="modal-box" style="width:460px">
    <div class="modal-header">
      <div class="modal-title">📷 Scan Barcode</div>
      <button class="modal-close" onclick="closeInvScan()">✕</button>
    </div>
    <div id="invReader" style="border-radius:10px;overflow:hidden;margin-bottom:10px"></div>
    <p style="font-size:.75rem;color:var(--muted);text-align:center">Scan an item to add it to the restock list</p>
  </div>
</div>

<!-- Quantity Prompt Modal -->
<div class="modal-bg" id="qtyMdl">
  <div class="modal-box" style="width:420px">
    <div class="modal-header">
      <div class="modal-title">➕ Restock Quantity</div>
      <button class="modal-close" onclick="closeQty()">✕</button>
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:14px;font-size:.84rem">
      <div style="font-weight:800" id="qtyItemName">Item</div>
      <div style="color:var(--muted);font-family:Space Mono,monospace" id="qtyBarcode">barcode</div>
    </div>
    <div class="form-field">
      <label class="form-label">Quantity to add</label>
      <input class="form-input" type="number" id="qtyVal" value="1" min="1">
      <input type="hidden" id="qtyBarHidden">
    </div>
    <button class="btn btn-lime btn-full" onclick="confirmQty()">Add to Restock List</button>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html5-qrcode/2.3.8/html5-qrcode.min.js"></script>
<script>
function filterInv(q){{document.querySelectorAll('#invTable tr.irow').forEach(r=>{{r.style.display=r.textContent.toLowerCase().includes(q.toLowerCase())?'':'none';}});}}
function openEdit(id,name,sell,cost,qty,bar){{
  document.getElementById('eId').value=id;document.getElementById('editTitle').textContent='Edit: '+name;
  document.getElementById('eSell').value=sell;document.getElementById('eCost').value=cost;document.getElementById('eQty').value=qty;
  document.getElementById('eBar').value=bar||'';
  document.getElementById('editMdl').classList.add('show');
}}
async function saveEdit(){{
  const r=await fetch('/api/item/edit',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{item_id:document.getElementById('eId').value,selling_price:document.getElementById('eSell').value,cost_price:document.getElementById('eCost').value,stock:document.getElementById('eQty').value,barcode:document.getElementById('eBar').value.trim()}})}});
  const d=await r.json();closeMdl('editMdl');
  if(d.ok){{showToast('Saved!');setTimeout(()=>location.reload(),700);}}else showToast(d.error,'error');
}}
async function addItem(){{
  const name=document.getElementById('nName').value.trim();
  if(!name||!document.getElementById('nSell').value||!document.getElementById('nQty').value){{showToast('Fill all fields','error');return;}}
  const r=await fetch('/api/item/add',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{name,selling_price:document.getElementById('nSell').value,cost_price:document.getElementById('nCost').value||0,stock:document.getElementById('nQty').value,barcode:document.getElementById('nBar').value.trim()}})}});
  const d=await r.json();
  if(d.ok){{showToast('Item added!');setTimeout(()=>location.reload(),700);}}else showToast(d.error,'error');
}}
function closeMdl(id){{document.getElementById(id).classList.remove('show');}}

// ── Scan barcode to add new item ─────────────────────────────────────────
let addScanner=null;
function openAddScan(){{
  document.getElementById('invScanMdl').classList.add('show');
  // reuse same modal reader; change helper text
  const hint = document.querySelector('#invScanMdl p');
  if(hint) hint.textContent = 'Scan barcode to fill the Add Item form';
  addScanner = new Html5Qrcode("invReader");
  addScanner.start({{facingMode:"environment"}},{{fps:10,qrbox:220}},(code)=>{{
    const val=(code||'').toString().trim();
    if(!val) return;
    closeAddScan();
    // If barcode exists, open edit. Otherwise fill Add New Item barcode input.
    fetch('/api/item/barcode/'+encodeURIComponent(val))
      .then(r=>r.json().then(j=>({{httpOk:r.ok, body:j}})))
      .then(data=>{{
        const httpOk=data&&data.httpOk, body=data&&data.body;
        if(httpOk && body && body.ok && body.item){{
          const it=body.item;
          openEdit(it.item_id, it.item_name, it.selling_price, it.cost_price, it.stock_quantity, it.barcode||val);
          showToast('Barcode already exists — editing item');
          return;
        }}
        document.getElementById('nBar').value = val;
        try{{ document.getElementById('nName').focus(); }}catch(e){{}}
        showToast('Barcode set. Fill item details and Add Item.');
      }})
      .catch(()=>{{
        document.getElementById('nBar').value = val;
        try{{ document.getElementById('nName').focus(); }}catch(e){{}}
        showToast('Barcode set. Fill item details and Add Item.');
      }});
  }},()=>{{}});
}}
function closeAddScan(){{
  document.getElementById('invScanMdl').classList.remove('show');
  if(addScanner){{addScanner.stop().catch(()=>{{}}); addScanner=null;}}
}}

// ── Scan-to-restock (Option A + C) ───────────────────────────────────────
let restockList=[]; // [{{barcode,name,qty}}]
let invScanner=null;
function openInvScan(){{
  document.getElementById('invScanMdl').classList.add('show');
  const hint = document.querySelector('#invScanMdl p');
  if(hint) hint.textContent = 'Scan an item to add it to the restock list';
  invScanner=new Html5Qrcode("invReader");
  invScanner.start({{facingMode:"environment"}},{{fps:10,qrbox:220}},(code)=>{{
    const val=(code||'').toString().trim();
    if(!val) return;
    closeInvScan();
    // Identify item then ask for quantity
    fetch('/api/item/barcode/'+encodeURIComponent(val))
      .then(r=>r.json().then(j=>({{httpOk:r.ok, body:j}})))
      .then(data=>{{
        const httpOk=data&&data.httpOk, body=data&&data.body;
        if(!httpOk || !body || !body.ok || !body.item){{ showToast('Barcode not found','error'); return; }}
        openQty(body.item.item_name, val);
      }})
      .catch(()=>showToast('Barcode lookup failed','error'));
  }},()=>{{}});
}}
function closeInvScan(){{
  document.getElementById('invScanMdl').classList.remove('show');
  if(invScanner){{invScanner.stop().catch(()=>{{}}); invScanner=null;}}
}}

function handleInvBarcode(code){{
  // Identify item then ask quantity (same as camera restock)
  fetch('/api/item/barcode/'+encodeURIComponent(code))
    .then(r=>r.json().then(j=>({{httpOk:r.ok, body:j}})))
    .then(data=>{{
      const httpOk=data&&data.httpOk, body=data&&data.body;
      if(!httpOk || !body || !body.ok || !body.item){{ showToast('Barcode not found','error'); return; }}
      openQty(body.item.item_name, code);
    }})
    .catch(()=>showToast('Barcode lookup failed','error'));
}}

function openQty(name, barcode){{
  document.getElementById('qtyItemName').textContent=name;
  document.getElementById('qtyBarcode').textContent=barcode;
  document.getElementById('qtyVal').value=1;
  document.getElementById('qtyBarHidden').value=barcode;
  document.getElementById('qtyMdl').classList.add('show');
  setTimeout(()=>{{ try{{document.getElementById('qtyVal').focus();}}catch(e){{}} }},50);
}}
function closeQty(){{ document.getElementById('qtyMdl').classList.remove('show'); }}
function confirmQty(){{
  const barcode=document.getElementById('qtyBarHidden').value.trim();
  const qty=Math.max(1, parseInt(document.getElementById('qtyVal').value||'1',10));
  const name=document.getElementById('qtyItemName').textContent.trim();
  if(!barcode){{ showToast('Missing barcode','error'); return; }}
  const ex=restockList.find(x=>x.barcode===barcode);
  if(ex) ex.qty += qty;
  else restockList.push({{barcode,name,qty}});
  closeQty();
  renderRestock();
  showToast('Added to restock: '+name);
}}

function renderRestock(){{
  const empty=document.getElementById('restockEmpty');
  const tbl=document.getElementById('restockTbl');
  const body=document.getElementById('restockBody');
  if(!restockList.length){{ empty.style.display='block'; tbl.style.display='none'; body.innerHTML=''; return; }}
  empty.style.display='none'; tbl.style.display='';
  body.innerHTML = restockList.map((r,i)=>`
    <tr>
      <td style="font-weight:700">${{r.name||'Item'}}</td>
      <td style="font-family:Space Mono,monospace;color:var(--muted)">${{r.barcode}}</td>
      <td>
        <div style="display:flex;gap:6px;align-items:center">
          <button class="qty-btn" onclick="chgRestock(${{i}},-1)">−</button>
          <span style="min-width:30px;text-align:center;font-weight:800">${{r.qty}}</span>
          <button class="qty-btn" onclick="chgRestock(${{i}},1)">+</button>
        </div>
      </td>
      <td style="text-align:right"><button class="btn btn-danger btn-sm" onclick="rmRestock(${{i}})">Remove</button></td>
    </tr>`).join('');
}}
function chgRestock(i,d){{ restockList[i].qty = Math.max(1, restockList[i].qty + d); renderRestock(); }}
function rmRestock(i){{ restockList.splice(i,1); renderRestock(); }}
function clearRestock(){{ restockList=[]; renderRestock(); }}
async function applyRestock(){{
  if(!restockList.length){{ showToast('No items in restock list','error'); return; }}
  try{{
    const r=await fetch('/api/item/restock_batch',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{items:restockList.map(x=>({{barcode:x.barcode,qty:x.qty}}))}})}});
    const d=await r.json();
    if(r.ok && d.ok){{ showToast('Restock applied'); restockList=[]; setTimeout(()=>location.reload(),700); }}
    else showToast(d.error||d.msg||'Restock failed','error');
  }}catch(e){{ showToast('Restock failed','error'); }}
}}
// Mark table rows for filtering
document.querySelectorAll('#invTable tr:not(:first-child)').forEach(r=>r.classList.add('irow'));
renderRestock();
</script>"""
    return render_page(sidebar("i"), content)

@app.route("/chat")
@auth
def chat():
    content = """
<div class="page-wrap" style="height:calc(100vh - 0px);padding-bottom:0">
  <div class="page-header" style="margin-bottom:16px">
    <div><div class="page-title">💬 Ask AI</div><div class="page-subtitle">Same as your Telegram bot — ask anything about your shop</div></div>
  </div>
  <div class="chat-layout" style="height:calc(100vh - 130px)">
    <div class="chips-panel">
      <div class="chips-title">Quick Questions</div>
      <button class="chip" onclick="ask(this)">📊 Today's report</button>
      <button class="chip" onclick="ask(this)">📅 This week's report</button>
      <button class="chip" onclick="ask(this)">📈 This month's profit</button>
      <button class="chip" onclick="ask(this)">💰 All time sales</button>
      <button class="chip" onclick="ask(this)">📦 Check all stock</button>
      <button class="chip" onclick="ask(this)">🔴 What needs restocking</button>
      <button class="chip" onclick="ask(this)">🏆 Best selling items</button>
      <button class="chip" onclick="ask(this)">How much profit have I made so far</button>
    </div>
    <div class="chat-panel">
      <div class="chat-messages" id="msgs">
        <div class="msg bot">👋 <strong>Habari Philex!</strong> I'm your shop assistant.<br><br>Ask me anything — sales, profit, stock, trends. Same as your Telegram bot!<div class="msg-time">now</div></div>
      </div>
      <div class="chat-inp-area">
        <input class="form-input" type="text" id="cin" placeholder="e.g. How much profit today?" onkeydown="if(event.key==='Enter')send()">
        <button class="btn btn-lime" onclick="send()">Send →</button>
      </div>
    </div>
  </div>
</div>
<script>
function ask(el){document.getElementById('cin').value=el.textContent.trim();send();}
async function send(){
  const inp=document.getElementById('cin'),text=inp.value.trim();if(!text)return;inp.value='';
  addMsg(text,'user');const typing=addMsg('typing...','bot');
  const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
  const d=await r.json();typing.remove();addMsg(d.reply||'❓ No response','bot');
}
function addMsg(text,who){
  const msgs=document.getElementById('msgs');
  const div=document.createElement('div');div.className='msg '+who;
  div.innerHTML=text.replace(/[*]([^*]+)[*]/g,'<strong>$1</strong>').replace(/[\\n]/g,'<br>')+
    '<div class="msg-time">'+new Date().toLocaleTimeString('en-KE',{hour:'2-digit',minute:'2-digit'})+'</div>';
  msgs.appendChild(div);msgs.scrollTop=msgs.scrollHeight;return div;
}
</script>"""
    return render_page(sidebar("c"), content)

# ─────────────────────────────────────────
# API ENDPOINTS
# ─────────────────────────────────────────
@app.route("/api/items")
@auth
def api_items():
    try:
        return jsonify(search_items_db(request.args.get("q", "")))
    except Exception as e:
        return jsonify({"error": str(e), "hint": "Check MySQL running + DB_CONFIG + database exists"}), 500


@app.route("/api/auto_orders", methods=["GET"])
@auth
def api_auto_orders():
    try:
        conn = db(); cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT order_id,telegram_full_name,telegram_username,phone,total_amount,mpesa_receipt,"
            "DATE_FORMAT(paid_at, '%Y-%m-%d %H:%i') AS paid_at "
            "FROM Customer_Orders WHERE status='PaidPendingCollection' ORDER BY paid_at DESC, order_id DESC"
        )
        orders = cur.fetchall() or []
        if orders:
            ids = [o["order_id"] for o in orders]
            placeholders = ",".join(["%s"] * len(ids))
            cur.execute(
                f"SELECT order_id,item_name,qty,line_total FROM Customer_Order_Items WHERE order_id IN ({placeholders}) ORDER BY id ASC",
                tuple(ids)
            )
            items = cur.fetchall() or []
        else:
            items = []
        cur.close(); conn.close()

        by_order: dict[int, list] = {}
        for it in items:
            by_order.setdefault(int(it["order_id"]), []).append({
                "item_name": it["item_name"],
                "qty": int(it["qty"] or 0),
                "line_total": float(it["line_total"] or 0),
            })

        out = []
        for o in orders:
            out.append({
                "order_id": int(o["order_id"]),
                "customer": (o.get("telegram_full_name") or "") + ((" @" + o.get("telegram_username")) if o.get("telegram_username") else ""),
                "phone": o.get("phone") or "",
                "total_amount": float(o.get("total_amount") or 0),
                "mpesa_receipt": o.get("mpesa_receipt") or "",
                "paid_at": o.get("paid_at") or "",
                "items": by_order.get(int(o["order_id"]), []),
            })
        return jsonify({"ok": True, "orders": out})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/auto_orders/approve", methods=["POST"])
@auth
def api_auto_orders_approve():
    try:
        d = request.json or {}
        order_id = int(d.get("order_id") or 0)
        if not order_id:
            return jsonify({"ok": False, "error": "Missing order_id"}), 400
        conn = db(); cur = conn.cursor()
        cur.execute(
            "UPDATE Customer_Orders SET status='CollectedApproved', approved_at=NOW(), approved_by=%s "
            "WHERE order_id=%s AND status='PaidPendingCollection'",
            ("dashboard", order_id)
        )
        conn.commit()
        ok = cur.rowcount > 0
        cur.close(); conn.close()
        if not ok:
            return jsonify({"ok": False, "error": "Order not found or already approved"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/sale", methods=["POST"])
@auth
def api_sale():
    d=request.json
    ok,result=process_cart_sale(d.get("cart",[]),d.get("payment_method","cash"),d.get("phone",""))
    return jsonify({"ok":ok,"receipt":result} if ok else {"ok":False,"error":result}), 200 if ok else 400

@app.route("/api/mpesa", methods=["POST"])
@auth
def api_mpesa():
    d = request.json
    print(f"📱 Dashboard M-Pesa request: phone={d.get('phone')} amount={d.get('amount')}")
    try:
        import mpesa_api, json as _json
        cart      = d.get("cart", [])
        cart_json = _json.dumps(cart) if cart else None
        info      = mpesa_api.trigger_stk_push_info(
            phone_number = d["phone"],
            amount       = int(float(d["amount"])),
            item         = d.get("items", "items"),
            quantity     = len(cart),
            cart_json    = cart_json
        )
        print(f"📱 STK info: ok={info.get('ok')} checkout_id={info.get('checkout_id')}")

        if info.get("ok"):
            # Always return the real checkout_id from the STK response (when available).
            # If Daraja timed out and checkout_id is missing, fall back to DB lookup.
            checkout_id = info.get("checkout_id")
            if not checkout_id:
                try:
                    # Match the latest pending transaction for this phone+amount
                    formatted_phone = mpesa_api.format_phone_number(d["phone"])
                    amt = int(float(d["amount"]))
                    conn = db(); cur = conn.cursor()
                    cur.execute(
                        "SELECT checkout_request_id FROM Mpesa_Transactions "
                        "WHERE phone_number=%s AND amount=%s "
                        "ORDER BY timestamp DESC LIMIT 1",
                        (formatted_phone, amt)
                    )
                    row = cur.fetchone()
                    cur.close(); conn.close()
                    if row and row[0]:
                        checkout_id = row[0]
                        print(f"📱 checkout_id recovered from DB: {checkout_id}")
                except Exception as e:
                    print(f"⚠️  checkout_id DB recovery failed: {e}")

            return jsonify({"ok": True, "checkout_id": checkout_id})

        return jsonify({"ok": False, "error": info.get("message") or info.get("error") or "M-Pesa error"}), 400
    except Exception as e:
        print(f"❌ M-Pesa dashboard error: {e}")
        import traceback; traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/mpesa/status/<checkout_id>")
@auth
def api_mpesa_status(checkout_id):
    """Return payment status for a CheckoutRequestID.

    Primary source is MySQL (Mpesa_Transactions). Optionally, if a separate
    callback server is running on port 5000, we can query it first.
    """
    # Log polls so we can confirm the dashboard is actually polling
    try:
        print(f"📡 Poll status: {checkout_id}")
    except Exception:
        pass
    try:
        # If user runs mpesa_callback.py separately, it exposes /mpesa/status on :5000
        import requests as _rq
        r = _rq.get(f"http://127.0.0.1:5000/mpesa/status/{checkout_id}", timeout=2)
        data = r.json()
        # Normalize status casing for frontend checks
        if isinstance(data, dict) and "status" in data and isinstance(data["status"], str):
            s = data["status"].strip()
            data["status"] = s[:1].upper() + s[1:].lower() if s else s
        try:
            print(f"   ↳ status={data.get('status') if isinstance(data, dict) else type(data)}")
        except Exception:
            pass
        return jsonify(data)
    except Exception as e:
        # Fallback: query DB directly
        try:
            conn   = db()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT status, mpesa_code, amount, phone_number, fail_reason FROM Mpesa_Transactions WHERE checkout_request_id=%s",
                (checkout_id,)
            )
            row = cursor.fetchone(); cursor.close(); conn.close()
            if not row: return jsonify({"status": "pending"})
            row["amount"] = float(row["amount"] or 0)
            # Normalize casing to match frontend checks
            if isinstance(row.get("status"), str):
                s = row["status"].strip()
                row["status"] = s[:1].upper() + s[1:].lower() if s else s
            try:
                print(f"   ↳ status(db)={row.get('status')}")
            except Exception:
                pass
            return jsonify(row)
        except Exception as e2:
            return jsonify({"status": "error", "error": str(e2)})

@app.route("/api/item/add", methods=["POST"])
@auth
def api_item_add():
    d=request.json
    ok,msg=add_inventory_item(d["name"],d["selling_price"],d.get("cost_price",0),d["stock"], d.get("barcode"))
    return jsonify({"ok":ok,"msg":msg})

@app.route("/api/item/edit", methods=["POST"])
@auth
def api_item_edit():
    d=request.json
    ok,msg=edit_inventory_item(d["item_id"],d["selling_price"],d.get("cost_price",0),d["stock"], d.get("barcode"))
    return jsonify({"ok":ok,"msg":msg})

@app.route("/api/item/barcode/<code>")
@auth
def api_item_by_barcode(code):
    try:
        conn = db(); cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT item_id,item_name,selling_price,cost_price,stock_quantity,barcode FROM Inventory WHERE barcode=%s LIMIT 1",
            (code,)
        )
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            return jsonify({"ok": False, "error": "Barcode not found"}), 404
        row["selling_price"] = float(row["selling_price"] or 0)
        row["cost_price"]    = float(row["cost_price"] or 0)
        return jsonify({"ok": True, "item": row})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/item/restock_barcode", methods=["POST"])
@auth
def api_restock_barcode():
    d = request.json or {}
    ok, msg = add_stock_by_barcode(d.get("barcode"), int(d.get("qty") or 0))
    return jsonify({"ok": ok, "msg": msg}), 200 if ok else 400

@app.route("/api/item/restock_batch", methods=["POST"])
@auth
def api_restock_batch():
    d = request.json or {}
    items = d.get("items") or []
    if not isinstance(items, list) or not items:
        return jsonify({"ok": False, "error": "No items provided"}), 400

    conn = db()
    cur = conn.cursor()
    try:
        # All-or-nothing: if any barcode missing, rollback.
        for it in items:
            barcode = str((it or {}).get("barcode") or "").strip()
            qty = int((it or {}).get("qty") or 0)
            if not barcode or qty <= 0:
                raise ValueError("Invalid barcode/qty in batch")
            cur.execute(
                "UPDATE Inventory SET stock_quantity=stock_quantity+%s WHERE barcode=%s",
                (qty, barcode)
            )
            if cur.rowcount == 0:
                raise ValueError(f"Barcode not found: {barcode}")
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        cur.close(); conn.close()

@app.route("/api/chat", methods=["POST"])
@auth
def api_chat():
    return jsonify({"reply":local_chat_response(request.json.get("text",""))})

@app.route("/api/version")
def api_version():
    """Debug endpoint: identify running dashboard code."""
    try:
        p = os.path.abspath(__file__)
        st = os.stat(p)
        return jsonify({
            "file": p,
            "mtime": st.st_mtime,
            "size": st.st_size,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─────────────────────────────────────────
# M-PESA CALLBACK — Safaricom calls this
# No auth required — it comes from Safaricom
# ─────────────────────────────────────────

@app.route('/mpesa/callback', methods=['POST'])
def mpesa_callback():
    """Safaricom calls this after every STK push attempt."""
    import json as _json, traceback as _tb

    # Log the raw payload first — always
    raw = request.get_data(as_text=True)
    print(f"\n{'='*60}")
    print(f"📩 MPESA CALLBACK RECEIVED at {datetime.now().strftime('%H:%M:%S')}")
    print(f"Raw payload: {raw[:500]}")
    print(f"{'='*60}")

    try:
        data         = _json.loads(raw) if raw else {}
        stk_callback = data.get('Body', {}).get('stkCallback', {})
        result_code  = stk_callback.get('ResultCode')
        checkout_id  = stk_callback.get('CheckoutRequestID', '')

        print(f"Result Code: {result_code} | Checkout ID: {checkout_id}")

        if result_code == 0:
            # ── PAYMENT SUCCESS ──
            metadata       = stk_callback.get('CallbackMetadata', {}).get('Item', [])
            receipt_number = "UNKNOWN"
            amount_paid    = 0
            phone_number   = "UNKNOWN"

            for meta in metadata:
                name  = meta.get('Name')
                value = meta.get('Value')
                if name == 'MpesaReceiptNumber': receipt_number = str(value)
                if name == 'Amount':             amount_paid    = float(value)
                if name == 'PhoneNumber':        phone_number   = str(value)

            print(f"✅ SUCCESS | Receipt: {receipt_number} | KES {amount_paid} | Phone: {phone_number}")

            # Step 1: Update status in Mpesa_Transactions
            conn   = db()
            cursor = conn.cursor()
            rows_updated = cursor.execute(
                "UPDATE Mpesa_Transactions SET status='Completed', mpesa_code=%s WHERE checkout_request_id=%s",
                (receipt_number, checkout_id)
            )
            conn.commit()
            print(f"DB update rows affected: {cursor.rowcount}")

            # Step 2: Fetch the cart/item stored when STK was sent
            # Use individual column queries to avoid cart_data column-not-found crash
            cursor.execute("SELECT item, quantity, amount, phone_number FROM Mpesa_Transactions WHERE checkout_request_id=%s", (checkout_id,))
            row = cursor.fetchone()
            
            # cart_data fetched separately so a missing column doesn't crash everything
            cart_data = None
            try:
                cursor.execute("SELECT cart_data FROM Mpesa_Transactions WHERE checkout_request_id=%s", (checkout_id,))
                cd_row = cursor.fetchone()
                if cd_row: cart_data = cd_row[0]
            except Exception as e:
                print(f"⚠️ cart_data column not found (will process as single item): {e}")

            cursor.close(); conn.close()
            print(f"Row from DB: {row} | cart_data present: {bool(cart_data)}")

            item_lines = []

            if cart_data:
                # ── Dashboard multi-item cart ──
                print(f"Processing cart JSON: {cart_data}")
                cart_items = _json.loads(cart_data)
                conn2   = db()
                cursor2 = conn2.cursor(dictionary=True)
                for entry in cart_items:
                    iid = int(entry["item_id"]); qty = int(entry["qty"])
                    cursor2.execute("SELECT item_name,selling_price,stock_quantity FROM Inventory WHERE item_id=%s",(iid,))
                    itm = cursor2.fetchone()
                    if not itm:
                        print(f"⚠️ Item {iid} not found in Inventory")
                        continue
                    if itm["stock_quantity"] < qty:
                        print(f"⚠️ Not enough stock for {itm['item_name']}: have {itm['stock_quantity']}, need {qty}")
                        qty = itm["stock_quantity"]  # sell what we have
                    price = float(itm["selling_price"]) * qty
                    cursor2.execute("UPDATE Inventory SET stock_quantity=stock_quantity-%s WHERE item_id=%s",(qty,iid))
                    cursor2.execute(
                        "INSERT INTO Sales_Ledger (item_id,quantity_sold,total_price,phone,payment_method) VALUES (%s,%s,%s,%s,'mpesa')",
                        (iid, qty, price, phone_number)
                    )
                    item_lines.append(f"{itm['item_name']} x{qty} = KES {price:.0f}")
                    print(f"✅ Recorded: {itm['item_name']} x{qty} = KES {price:.0f}")
                conn2.commit(); cursor2.close(); conn2.close()
                print(f"✅ Total items recorded in Sales_Ledger: {len(item_lines)}")

            elif row and row[0]:
                # ── Telegram bot single-item sale ──
                import pos_engine as _pe
                print(f"Processing single item: {row[0]} x{row[1]}")
                _pe.process_sale(item_name=row[0], quantity_sold=row[1], phone=phone_number, payment_method="mpesa")
                item_lines.append(f"{row[0]} x{row[1]}")
                print(f"✅ Single item sale recorded")

            else:
                # ── No cart data — just record amount received ──
                print(f"⚠️ No cart data and no item name. Recording payment only.")

            # Log success — dashboard shows result via polling, no Telegram needed here
            items_text = ", ".join(item_lines) if item_lines else "no items"
            print(f"✅ Payment fully processed | {items_text}")

        else:
            # ── PAYMENT FAILED / CANCELLED ──
            reason = stk_callback.get('ResultDesc', 'Unknown reason')
            print(f"❌ Payment FAILED: {reason}")
            conn   = db()
            cursor = conn.cursor()
            cursor.execute("UPDATE Mpesa_Transactions SET status='Failed' WHERE checkout_request_id=%s",(checkout_id,))
            conn.commit(); cursor.close(); conn.close()
            print(f"❌ Payment failed recorded in DB | Reason: {reason}")

    except Exception as e:
        print(f"\n🔥 CALLBACK HANDLER CRASHED: {e}")
        _tb.print_exc()
        # Still return 200 so Safaricom doesn't keep retrying
        return jsonify({"ResultCode": 0, "ResultDesc": "Received"})

    return jsonify({"ResultCode": 0, "ResultDesc": "Success"})


@app.route('/mpesa/test', methods=['GET'])
def mpesa_test():
    """Visit this URL in browser to verify ngrok is reaching the dashboard."""
    return jsonify({
        "status": "✅ Dashboard is reachable!",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "callback_url": "https://subconjunctively-brainy-norine.ngrok-free.dev/mpesa/callback"
    })


# /mpesa/status handled by /api/mpesa/status above

@app.route("/ping")
def ping():
    return make_response("DUKA POS IS RUNNING ✅ — open http://localhost:8080 in your browser")

if __name__=="__main__":
    # Ensure required tables exist before starting the dashboard
    try:
        pos_engine.init_tables()
    except Exception as e:
        print(f"⚠️ Could not init tables: {e}")

    print("🏪 My Duka POS Dashboard starting...")
    print("🌐 Open: http://localhost:8080")
    print("🔑 Password: duka2024")
    print("📡 M-Pesa callback: https://subconjunctively-brainy-norine.ngrok-free.dev/mpesa/callback")
    print("\n⚠️  NOTE: Run ONLY this file for the dashboard.")
    print("   Do NOT run mpesa_callback.py separately — it's built in here.")
    print("\n📋 Registered routes:")
    with app.app_context():
        for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
            print(f"   {rule.methods} {rule.rule}")
    print()
    app.run(host="0.0.0.0",port=8080,debug=False)