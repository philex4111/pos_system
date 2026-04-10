"""
M-Pesa Callback Server — runs on port 5000
Ngrok tunnels to this port.
Dashboard runs separately on port 8080.
"""
import os
from dotenv import load_dotenv
import requests
import json, traceback
from flask import Flask, request, jsonify
import mysql.connector
import requests as req
from datetime import datetime
import pos_engine
import receipt_engine

load_dotenv()

app = Flask(__name__)

# ─── CONFIG ───────────────────────────────────
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID   = int(os.getenv("MY_ADMIN_ID"))

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "user":     os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASS"),
    "database": os.getenv("DB_NAME", "duka_pos")
}

def db():
    return mysql.connector.connect(**DB_CONFIG)

def telegram(msg: str):
    """Send a Telegram message to the admin."""
    try:
        req.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
        print(f"📲 Telegram sent: {msg[:60]}...")
    except Exception as e:
        print(f"⚠️  Telegram failed: {e}")

def ensure_cart_data_column():
    """Make sure cart_data column exists — run once on startup."""
    try:
        conn   = db()
        cursor = conn.cursor()
        cursor.execute("ALTER TABLE Mpesa_Transactions ADD COLUMN cart_data TEXT")
        conn.commit()
        print("✅ Added cart_data column to Mpesa_Transactions")
    except Exception:
        pass  # already exists
    finally:
        try: cursor.close(); conn.close()
        except: pass

# ─────────────────────────────────────────
# TEST ENDPOINT — visit in browser to confirm ngrok works
# ─────────────────────────────────────────
@app.route('/mpesa/test', methods=['GET'])
def test():
    return jsonify({
        "status":   "✅ Callback server is REACHABLE",
        "time":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "port":     5000
    })

# ─────────────────────────────────────────
# STATUS ENDPOINT — dashboard polls this every 3s
# ─────────────────────────────────────────
@app.route('/mpesa/status/<checkout_id>', methods=['GET'])
def status(checkout_id):
    try:
        conn   = db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT status, mpesa_code, amount, phone_number FROM Mpesa_Transactions WHERE checkout_request_id=%s",
            (checkout_id,)
        )
        row = cursor.fetchone()
        cursor.close(); conn.close()
        if not row:
            return jsonify({"status": "pending"})
        row["amount"] = float(row["amount"] or 0)
        return jsonify(row)
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})

# ─────────────────────────────────────────
# MAIN CALLBACK — Safaricom posts here
# ─────────────────────────────────────────
@app.route('/mpesa/callback', methods=['POST'])
def mpesa_callback():
    raw = request.get_data(as_text=True)

    print("\n" + "="*60)
    print(f"📩 CALLBACK RECEIVED — {datetime.now().strftime('%H:%M:%S')}")
    print(f"Payload: {raw[:400]}")
    print("="*60)

    try:
        data         = json.loads(raw) if raw else {}
        stk          = data.get('Body', {}).get('stkCallback', {})
        result_code  = stk.get('ResultCode')
        checkout_id  = stk.get('CheckoutRequestID', '')

        # ── PAYMENT SUCCESSFUL ────────────────────
        if result_code == 0:
            receipt_number = "UNKNOWN"
            amount_paid    = 0.0
            phone_number   = "UNKNOWN"

            for item in stk.get('CallbackMetadata', {}).get('Item', []):
                name  = item.get('Name')
                value = item.get('Value')
                if name == 'MpesaReceiptNumber': receipt_number = str(value)
                if name == 'Amount':             amount_paid    = float(value)
                if name == 'PhoneNumber':        phone_number   = str(value)

            print(f"✅ PAID | Code: {receipt_number} | KES {amount_paid} | {phone_number}")

            # 1. Mark transaction as Completed
            conn   = db()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE Mpesa_Transactions SET status='Completed', mpesa_code=%s WHERE checkout_request_id=%s",
                (receipt_number, checkout_id)
            )
            conn.commit()
            print(f"   DB rows updated: {cursor.rowcount}")

            # Link to customer self-checkout order (Telegram) if present
            try:
                cursor.execute(
                    "UPDATE Customer_Orders "
                    "SET status='PaidPendingCollection', mpesa_receipt=%s, phone=%s, paid_at=NOW() "
                    "WHERE checkout_request_id=%s",
                    (receipt_number, phone_number, checkout_id)
                )
                conn.commit()
            except Exception as e:
                print(f"⚠️  Customer_Orders update skipped: {e}")

            # 2. Get the cart/item data stored when STK was triggered
            cursor.execute(
                "SELECT item, quantity, amount, phone_number FROM Mpesa_Transactions WHERE checkout_request_id=%s",
                (checkout_id,)
            )
            row = cursor.fetchone()

            cart_data = None
            try:
                cursor.execute(
                    "SELECT cart_data FROM Mpesa_Transactions WHERE checkout_request_id=%s",
                    (checkout_id,)
                )
                cd = cursor.fetchone()
                if cd: cart_data = cd[0]
            except Exception as e:
                print(f"⚠️  cart_data read error: {e}")

            cursor.close(); conn.close()
            print(f"   item row: {row} | cart_data: {bool(cart_data)}")

            item_lines = []

            # 3a. Dashboard multi-item cart
            if cart_data:
                print(f"   Processing cart: {cart_data}")
                cart_list = json.loads(cart_data)
                conn2   = db()
                cursor2 = conn2.cursor(dictionary=True)
                for entry in cart_list:
                    iid = int(entry["item_id"])
                    qty = int(entry["qty"])
                    cursor2.execute(
                        "SELECT item_name, selling_price, stock_quantity FROM Inventory WHERE item_id=%s",
                        (iid,)
                    )
                    itm = cursor2.fetchone()
                    if not itm:
                        print(f"   ⚠️  item_id {iid} not found"); continue
                    sell_qty = min(qty, itm["stock_quantity"])
                    price    = float(itm["selling_price"]) * sell_qty
                    cursor2.execute(
                        "UPDATE Inventory SET stock_quantity=stock_quantity-%s WHERE item_id=%s",
                        (sell_qty, iid)
                    )
                    cursor2.execute(
                        "INSERT INTO Sales_Ledger (item_id,quantity_sold,total_price,phone,payment_method) VALUES (%s,%s,%s,%s,'mpesa')",
                        (iid, sell_qty, price, phone_number)
                    )
                    item_lines.append(f"{itm['item_name']} ×{sell_qty} = KES {price:.0f}")
                    print(f"   ✅ Recorded: {itm['item_name']} ×{sell_qty}")
                conn2.commit(); cursor2.close(); conn2.close()

            # 3b. Telegram bot single-item
            elif row and row[0]:
                import pos_engine
                pos_engine.process_sale(
                    item_name     = row[0],
                    quantity_sold = row[1],
                    phone         = phone_number,
                    payment_method= "mpesa"
                )
                item_lines.append(f"{row[0]} ×{row[1]}")
                print(f"   ✅ Single item recorded: {row[0]} ×{row[1]}")

            # 4. Telegram success alert
            items_str = "\n".join(f"  • {l}" for l in item_lines) or "  (items already recorded)"
            telegram(
                f"🎉 *Payment Received!*\n"
                f"📱 Phone: `{phone_number}`\n"
                f"💰 Amount: *KES {amount_paid:.0f}*\n"
                f"🧾 M-Pesa: `{receipt_number}`\n"
                f"🛒 Items:\n{items_str}"
            )

        # ── PAYMENT FAILED / CANCELLED ────────────
        else:
            reason = stk.get('ResultDesc', 'Cancelled or insufficient funds')
            print(f"❌ FAILED: {reason}")
            conn   = db()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE Mpesa_Transactions SET status='Failed', fail_reason=%s WHERE checkout_request_id=%s",
                (reason[:255], checkout_id)
            )
            try:
                cursor.execute(
                    "UPDATE Customer_Orders SET status='Failed' WHERE checkout_request_id=%s",
                    (checkout_id,)
                )
            except Exception:
                pass
            conn.commit(); cursor.close(); conn.close()

            telegram(
                f"❌ *Payment Failed / Cancelled*\n"
                f"🎫 ID: `{checkout_id}`\n"
                f"📝 Reason: {reason}"
            )

    except Exception:
        print(f"🔥 CALLBACK CRASHED:\n{traceback.format_exc()}")

    # Always return 200 so Safaricom doesn't retry endlessly
    return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"})


# ─────────────────────────────────────────
# START
# ─────────────────────────────────────────
if __name__ == "__main__":
    print("="*50)
    print("📡 M-Pesa Callback Server")
    print("🌐 Port: 5000")
    print("🔗 Ngrok must tunnel to port 5000")
    print("✅ Test: https://subconjunctively-brainy-norine.ngrok-free.dev/mpesa/test")
    print("="*50)
    ensure_cart_data_column()
    app.run(host="0.0.0.0", port=5000, debug=False)