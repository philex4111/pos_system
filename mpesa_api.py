import os
from dotenv import load_dotenv
import requests
import base64
from datetime import datetime
from requests.auth import HTTPBasicAuth
import pos_engine

load_dotenv()

# ─────────────────────────────────────────
# 1. DARAJA SANDBOX CREDENTIALS
# ─────────────────────────────────────────
CONSUMER_KEY    = os.getenv("MPESA_CONSUMER_KEY").strip()
CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET").strip()
SHORTCODE       = os.getenv("MPESA_SHORTCODE")
PASSKEY         = os.getenv("MPESA_PASSKEY")
# ─────────────────────────────────────────
# 2. NGROK CALLBACK URL
#    Update this every time Ngrok restarts!
# ─────────────────────────────────────────
NGROK_BASE_URL = os.getenv("NGROK_BASE_URL")
CALLBACK_URL   = f"{NGROK_BASE_URL}/mpesa/callback"
# ─────────────────────────────────────────
# 3. HELPERS
# ─────────────────────────────────────────

def get_access_token() -> str | None:
    """Fetch a fresh OAuth token from Safaricom."""
    url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
    try:
        resp = requests.get(url, auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET), timeout=15)
        if resp.status_code == 200:
            return resp.json().get('access_token')
        print(f"❌ Auth failed: {resp.status_code} — {resp.text}")
        return None
    except Exception as e:
        print(f"❌ Auth exception: {e}")
        return None

def format_phone_number(phone: str) -> str:
    """Normalize any Kenyan phone format to 2547XXXXXXXX."""
    phone = str(phone).strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):  phone = phone[1:]
    if phone.startswith("0"):  phone = "254" + phone[1:]
    if phone.startswith("7") or phone.startswith("1"):
        phone = "254" + phone
    return phone

# ─────────────────────────────────────────
# 4. STK PUSH — main function
# ─────────────────────────────────────────
def trigger_stk_push_info(phone_number: str, amount: float,
                          item: str = None, quantity: int = 1,
                          cart_json: str = None) -> dict:
    """
    Send an M-Pesa STK push and save a Pending record to MySQL.
    cart_json: JSON string of [{item_id, qty}, ...] for multi-item dashboard sales.
    """
    pos_engine.init_tables()

    token = get_access_token()
    if not token:
        return {
            "ok": False,
            "error": "Auth Error",
            "message": "❌ Failed to connect to Safaricom (Auth Error). Check your Consumer Key & Secret.",
            "checkout_id": None,
        }

    formatted_phone = format_phone_number(phone_number)
    timestamp       = datetime.now().strftime('%Y%m%d%H%M%S')
    password        = base64.b64encode(
        (SHORTCODE + PASSKEY + timestamp).encode('utf-8')
    ).decode('utf-8')

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json"
    }
    payload = {
        "BusinessShortCode": SHORTCODE,
        "Password":          password,
        "Timestamp":         timestamp,
        "TransactionType":   "CustomerPayBillOnline",
        "Amount":            int(amount),
        "PartyA":            formatted_phone,
        "PartyB":            SHORTCODE,
        "PhoneNumber":       formatted_phone,
        "CallBackURL":       CALLBACK_URL,
        "AccountReference":  "Duka POS",
        "TransactionDesc":   f"Payment of {int(amount)} Ksh"
    }

    try:
        print(f"🚀 Sending STK Push → {formatted_phone} | KES {amount}")
        resp   = requests.post(
            "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
            json=payload, headers=headers, timeout=60
        )
        result = resp.json() if resp.content else {}

        if "CheckoutRequestID" in result:
            checkout_id = result["CheckoutRequestID"]

            # Save Pending transaction to MySQL
            pos_engine.init_tables()
            conn   = pos_engine.get_connection()
            cursor = conn.cursor()
            # Ensure cart_data column exists
            try:
                cursor.execute("ALTER TABLE Mpesa_Transactions ADD COLUMN cart_data TEXT")
                conn.commit()
            except Exception:
                pass  # column already exists

            cursor.execute("""
                INSERT IGNORE INTO Mpesa_Transactions
                    (checkout_request_id, phone_number, amount, status, item, quantity, cart_data)
                VALUES (%s, %s, %s, 'Pending', %s, %s, %s)
            """, (checkout_id, formatted_phone, amount, item, quantity, cart_json))
            conn.commit()
            cursor.close()
            conn.close()

            print(f"✅ STK Push accepted. Checkout ID: {checkout_id}")
            return {
                "ok": True,
                "checkout_id": checkout_id,
                "phone": formatted_phone,
                "amount": int(amount),
                "message": (
                    f"✅ *STK Push Sent!*\n"
                    f"📱 Phone  : `{formatted_phone}`\n"
                    f"💰 Amount : *KES {int(amount)}*\n"
                    f"⏳ Waiting for customer to enter PIN..."
                ),
                "raw": result,
            }
        else:
            error_msg = result.get('errorMessage', result.get('ResultDesc', 'Service Unavailable'))
            print(f"❌ Safaricom rejected: {error_msg}")
            return {
                "ok": False,
                "error": "Rejected",
                "message": f"❌ M-Pesa Error: {error_msg}",
                "checkout_id": None,
                "raw": result,
            }

    except requests.exceptions.ReadTimeout:
        return {
            "ok": True,
            "checkout_id": None,
            "phone": formatted_phone,
            "amount": int(amount),
            "message": (
                f"⏳ *Daraja Sandbox is slow today.*\n"
                f"The request was sent — the prompt may still appear on "
                f"{formatted_phone} shortly. Ask the customer to check their phone!"
            ),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "System Error",
            "message": f"❌ System Error: {e}",
            "checkout_id": None,
        }


def trigger_stk_push(phone_number: str, amount: float,
                     item: str = None, quantity: int = 1,
                     cart_json: str = None) -> str:
    """Legacy wrapper kept for Telegram bot and older callers."""
    info = trigger_stk_push_info(phone_number, amount, item=item, quantity=quantity, cart_json=cart_json)
    return info.get("message") or (info.get("error") or "Error")


# ─────────────────────────────────────────
# 5. QUICK TEST
# ─────────────────────────────────────────
if __name__ == "__main__":
    print(trigger_stk_push("0724022458", 1))