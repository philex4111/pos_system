import json
import asyncio
import re
import random
import string
import secrets
import sys
import requests
import mysql.connector
import tempfile
import os
from datetime import datetime

# Avoid Windows console UnicodeEncodeError when printing emojis
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Whisper (local speech-to-text) — loaded lazily on first voice message
_whisper_model = None
FFMPEG_PATH = r"C:\ffmpeg\ffmpeg-8.0.1-essentials_build\bin"

def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        try:
            import whisper
            # Make sure ffmpeg is in PATH for this process
            if FFMPEG_PATH not in os.environ.get("PATH", ""):
                os.environ["PATH"] = FFMPEG_PATH + os.pathsep + os.environ.get("PATH", "")
            import warnings
            warnings.filterwarnings("ignore")  # suppress FP16 warning
            print("🎙️ Loading Whisper speech model...")
            _whisper_model = whisper.load_model("small")
            print("✅ Whisper ready — voice commands enabled!")
        except Exception as e:
            print(f"⚠️ Whisper not available: {e}")
    return _whisper_model
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import pos_engine
import mpesa_api
import receipt_engine

# ─────────────────────────────────────────
# 1. CONFIGURATION
# ─────────────────────────────────────────
TELEGRAM_TOKEN = "8696352975:AAERx1USzLuQXEu7VB08rQwlM5ZOHL3Uz0s"
MY_ADMIN_ID    = 8716135502
OLLAMA_URL     = "http://localhost:11434/api/generate"
OLLAMA_MODEL   = "llama3.2"

# ─────────────────────────────────────────
# USER MANAGEMENT  (roles: admin | attendant)
# ─────────────────────────────────────────

# Attendant permissions — admin always has full access
ATTENDANT_PERMISSIONS = {
    "sell", "pay_mpesa", "check_stock", "add_stock", "report", "chat", "restock", "trend"
}

# In-memory invite codes {code: {"created_by": admin_id, "used": False}}
_invite_codes: dict[str, dict] = {}

def _get_db():
    return mysql.connector.connect(**pos_engine.DB_CONFIG)

def init_users_table():
    conn   = _get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Bot_Users (
            id         INT AUTO_INCREMENT PRIMARY KEY,
            user_id    BIGINT UNIQUE,
            username   VARCHAR(100),
            full_name  VARCHAR(100),
            role       VARCHAR(20) DEFAULT 'attendant',
            joined_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active  BOOLEAN DEFAULT TRUE
        )
    """)
    # Make sure admin is always registered
    cursor.execute("""
        INSERT IGNORE INTO Bot_Users (user_id, username, full_name, role)
        VALUES (%s, 'admin', 'Shop Owner', 'admin')
    """, (MY_ADMIN_ID,))
    conn.commit()
    cursor.close()
    conn.close()

def get_user(user_id: int) -> dict | None:
    """Return user record or None if not registered."""
    try:
        conn   = _get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM Bot_Users WHERE user_id = %s AND is_active = TRUE",
            (user_id,)
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row
    except Exception:
        return None

def register_user(user_id: int, username: str, full_name: str, role: str = "attendant"):
    conn   = _get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO Bot_Users (user_id, username, full_name, role)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            username  = VALUES(username),
            full_name = VALUES(full_name),
            is_active = TRUE
    """, (user_id, username or "unknown", full_name or "Unknown", role))
    conn.commit()
    cursor.close()
    conn.close()

def generate_invite_code() -> str:
    """Generate a random 6-digit invite code."""
    code = ''.join(random.choices(string.digits, k=6))
    _invite_codes[code] = {"used": False}
    return code

def use_invite_code(code: str) -> bool:
    """Validate and consume an invite code. Returns True if valid."""
    entry = _invite_codes.get(code)
    if entry and not entry["used"]:
        _invite_codes[code]["used"] = True
        return True
    return False

def get_all_users() -> list:
    try:
        conn   = _get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT user_id, username, full_name, role, joined_at, is_active "
            "FROM Bot_Users ORDER BY role, joined_at"
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return rows
    except Exception:
        return []

def remove_user(user_id: int):
    conn   = _get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE Bot_Users SET is_active = FALSE WHERE user_id = %s",
        (user_id,)
    )
    conn.commit()
    cursor.close()
    conn.close()

# can_do defined below at the user management section

# ── Multi-user config ──
# Change this code and share it with your attendants to let them register
INVITE_CODE = "MYDUKA2026"

# What attendants are allowed to do (admin can do everything)
ATTENDANT_ACTIONS = ATTENDANT_PERMISSIONS  # keep in sync

# Actions blocked for attendants
ADMIN_ONLY_ACTIONS = {"audit", "trend"}

# ─────────────────────────────────────────
# 2. DATABASE — handled entirely by pos_engine (MySQL)
# ─────────────────────────────────────────

# ─────────────────────────────────────────
# 3. LOCAL BRAIN (INSTANT FALLBACK)
# ─────────────────────────────────────────
def parse_user_intent_local(text):
    """
    Smart intent parser — POS-first approach.
    Always tries to match a POS action before falling back to chat.
    Understands natural language, past tense, and Swahili.
    """
    text_lower = text.lower().strip()
    result = {"action": "chat", "quantity": 1}

    # ── Extract phone number ──
    # First clean spoken/whisper formats like "07-24-022-458" or "07 24 022 458"
    text_clean_phone = re.sub(r'(\d)[\s\-](\d)', r'\1\2', text_lower)
    text_clean_phone = re.sub(r'(\d)[\s\-](\d)', r'\1\2', text_clean_phone)  # twice for "0-7-2-4"
    phone_match = re.search(r'\b((?:07|01|2547|2541)\d{8})\b', text_clean_phone)
    if phone_match:
        result["phone"] = phone_match.group(1)

    # ── Extract quantity ──
    text_no_phone = text_lower.replace(result.get("phone", ""), "")
    qty_match = re.search(r'\b(\d+)\b', text_no_phone)
    if qty_match:
        result["quantity"] = int(qty_match.group(1))

    # ── Words that can NEVER be product names ──
    NON_ITEM_WORDS = {
        "stock", "shop", "inventory", "items", "remaining", "available",
        "left", "total", "all", "my", "our", "the", "is", "are", "what",
        "how", "much", "many", "tell", "me", "show", "check", "give",
        "please", "today", "now", "restock", "in", "of", "for", "and",
        "have", "has", "had", "just", "sold", "sale", "bought", "customer",
        "duka", "ngapi", "kuna", "nini", "gani", "yako", "yangu", "yetu",
        "imebaki", "nimebakisha", "baki", "iliyobaki", "leo", "sasa",
        "nauliza", "niambie", "nionyeshe", "angalia", "tazama", "je",
        "tuna", "nina", "una", "wana", "kuhusu", "kwenye", "katika",
        "nimemuuzia", "nimeuza", "niliuza", "nimemaliza"
    }

    def extract_item(pattern, text):
        """Extract product name, rejecting stop-words."""
        m = re.search(pattern, text)
        if m:
            candidate = m.group(1).strip()
            if candidate not in NON_ITEM_WORDS and len(candidate) > 2:
                return candidate
        return None

    def find_item_anywhere(text):
        """
        Last-resort item finder — scans all words for a known DB item name.
        This catches natural phrases like 'i sold sugar' or 'sugar imeuzwa'.
        """
        try:
            conn   = pos_engine.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT item_name FROM Inventory")
            db_items = [row[0].lower() for row in cursor.fetchall()]
            cursor.close()
            conn.close()
            for word in text.lower().split():
                word = word.strip(".,!?")
                for db_item in db_items:
                    if word in db_item or db_item in word:
                        return db_item
        except Exception:
            pass
        return None

    # ════════════════════════════════════════
    # POS INTENT DETECTION  — strict priority
    # Always try POS first, chat is last resort
    # ════════════════════════════════════════

    # ── AUDIT ──
    if any(w in text_lower for w in ["audit", "suspicious", "check transactions", "unusual"]):
        result["action"] = "audit"

    # ── TREND ──
    elif any(w in text_lower for w in ["trend", "best sell", "popular", "top item",
                                        "inayouzwa", "inauzwa zaidi"]):
        result["action"] = "trend"
        result["period"] = "week"

    # ── REPORT / SALES HISTORY ──
    elif any(w in text_lower for w in [
        "report", "sales today", "daily", "revenue", "profit", "mapato",
        "mauzo", "jumla ya", "how much have", "how much did", "how much profit",
        "what item was sold", "what items sold", "what was sold",
        "sold today", "sold yesterday", "sold this", "sold in", "sold last",
        "nini kiliuzwa", "nini tuliuza", "mauzo ya",
        # new phrases
        "show me sales", "show sales", "show me total", "show total",
        "show me all", "show all", "all time sales", "all time",
        "total sales", "all sales", "how much money", "total revenue",
        "earnings", "faida", "nionyeshe mauzo", "mauzo yote", "ripoti",
        "how much i made", "how much have i made", "since we started",
        "ever made", "total profit", "overall sales", "overall profit"
    ]):
        result["action"] = "report"
        # Detect specific date ranges
        import re as _re
        date_from_match = _re.search(r'from\s+(\d{4}-\d{2}-\d{2})', text_lower)
        date_to_match   = _re.search(r'to\s+(\d{4}-\d{2}-\d{2})', text_lower)
        year_match      = _re.search(r'(20\d{2})', text_lower)

        if date_from_match:
            result["date_from"] = date_from_match.group(1)
        if date_to_match:
            result["date_to"]   = date_to_match.group(1)

        # Period detection — order matters (most specific first)
        if "yesterday" in text_lower:
            result["period"] = "yesterday"
        elif "today" in text_lower or "leo" in text_lower:
            result["period"] = "today"
        elif "this week" in text_lower or "wiki hii" in text_lower:
            result["period"] = "this week"
        elif "this month" in text_lower or "mwezi huu" in text_lower:
            result["period"] = "this month"
        elif "this year" in text_lower or "mwaka huu" in text_lower:
            result["period"] = "this year"
        elif "all time" in text_lower or "ever" in text_lower or "all" in text_lower \
             or "so far" in text_lower or "since" in text_lower or "overall" in text_lower \
             or "total" in text_lower or "yote" in text_lower or "zote" in text_lower:
            result["period"] = "all"
        elif "january" in text_lower:  result["period"] = "january"
        elif "february" in text_lower: result["period"] = "february"
        elif "march" in text_lower:    result["period"] = "march"
        elif "april" in text_lower:    result["period"] = "april"
        elif "may" in text_lower:      result["period"] = "may"
        elif "june" in text_lower:     result["period"] = "june"
        elif "july" in text_lower:     result["period"] = "july"
        elif "august" in text_lower:   result["period"] = "august"
        elif "september" in text_lower:result["period"] = "september"
        elif "october" in text_lower:  result["period"] = "october"
        elif "november" in text_lower: result["period"] = "november"
        elif "december" in text_lower: result["period"] = "december"
        elif year_match:
            result["period"] = year_match.group(1)
        elif "week" in text_lower or "wiki" in text_lower:
            result["period"] = "week"
        elif "month" in text_lower or "mwezi" in text_lower:
            result["period"] = "month"
        elif "year" in text_lower or "mwaka" in text_lower:
            result["period"] = "year"
        else:
            result["period"] = "today"

    # ── RESTOCK CHECK ──
    elif any(w in text_lower for w in ["running low", "low stock", "restock first",
                                        "need to restock", "inayokwisha", "what do we need",
                                        "inakwisha"]):
        result["action"] = "restock"

    # ── ADD STOCK ──
    elif any(w in text_lower for w in ["add stock", "bandika", "ongeza", "weka stock",
                                        "nimepokea", "nimeingiza", "received stock"]):
        result["action"] = "add_stock"
        result["item"]   = (
            extract_item(r'(?:add|bandika|ongeza|received|weka)\s+(?:\d+\s+)?([a-z]+)', text_lower)
            or find_item_anywhere(text_lower)
        )

    # ── CHECK STOCK ──
    elif any(w in text_lower for w in ["stock", "ngapi", "how much", "available",
                                        "remaining", "nimebakisha", "baki", "iliyobaki",
                                        "kiasi gani", "je tuna", "kuna ngapi", "is left",
                                        "imeisha", "inventory", "what is left", "left",
                                        "is remaining", "do we have", "do we still"]):
        result["action"] = "check_stock"
        item_match = (
            extract_item(r'(?:of|ya|la|za|for|kuhusu|about)\s+([a-z]+)', text_lower)
            or extract_item(r'([a-z]+)\s+stock', text_lower)
            or extract_item(r'(?:check|angalia|ona|tazama)\s+([a-z]+)', text_lower)
            or find_item_anywhere(text_lower)
        )
        result["item"] = item_match

    # ── MPESA PAYMENT ──
    elif any(w in text_lower for w in ["mpesa", "charge", "lipa", "lipia",
                                        "tuma pesa", "send payment"]):
        result["action"] = "pay_mpesa"
        result["item"]   = (
            extract_item(r'(?:for|ya|kwa|lipa|lipia|charge)\s+(?:\d+\s+)?([a-z]+)', text_lower)
            or find_item_anywhere(text_lower)
        )

    # ── SALES HISTORY QUERY — "what was sold", "what items sold today" ──
    elif any(w in text_lower for w in [
        "what was sold", "what items sold", "what item sold", "what did we sell",
        "what have we sold", "items sold today", "sold today", "sold this",
        "nini kiliuzwa", "nini tuliuza", "niliuza nini", "tuliuza nini"
    ]):
        result["action"] = "report"
        result["period"] = "all"   if any(w in text_lower for w in ["all time", "all-time", "ever", "total", "jumla", "zote"]) else (
                           "today" if "today"  in text_lower else (
                           "week"  if "week"   in text_lower else "month"))

    # ── SELL — catches natural language like "i have sold", "nimemuuzia", "just sold" ──
    elif any(w in text_lower for w in [
        # English — present, past, natural
        "sell", "sold", "i sold", "i have sold", "just sold", "customer bought",
        "sale of", "selling", "make a sale", "record sale", "cash sale",
        # Swahili
        "niuzie", "uza", "nimemuuzia", "nimeuza", "niliuza", "ameununua",
        "ameuziwa", "amelipia", "umeuzwa", "bidhaa imeuzwa"
    ]):
        result["action"] = "sell"
        result["item"]   = (
            # After quantity: "sold 2 sugar"
            extract_item(r'(?:sold?|sell|niuzie|uza|nimeuza|niliuza)\s+(?:\d+\s+)?([a-z]+)', text_lower)
            # Before quantity: "sugar 2 sold"
            or extract_item(r'([a-z]+)\s+\d+\s+(?:sold|umeuzwa)', text_lower)
            # Generic item finder from DB
            or find_item_anywhere(text_lower)
        )
        # Default payment = cash for natural sell phrases
        if not result.get("payment"):
            result["payment"] = "cash"

    # ── CHAT — only if absolutely no POS intent detected ──
    else:
        result["action"] = "chat"

    return result

# ─────────────────────────────────────────
# 4. OLLAMA AI BRAIN (TWO-MODE)
# ─────────────────────────────────────────

# Conversation history per user (for natural multi-turn chat)
conversation_history: dict[int, list] = {}

# ── PROMPT 1: Fast router — decides if message is a POS command or general chat ──
ROUTER_PROMPT = """You are a POS system router for a Kenyan shop. Your job is to detect shop-related commands.

IMPORTANT RULES:
1. ALWAYS prefer a POS action over chat when the message is even slightly shop-related
2. Any mention of items (sugar, salt, unga, rice, oil, bread, flour etc.) = POS action
3. Any mention of selling, buying, stock, payment = POS action
4. Natural language and past tense STILL count: "i sold", "nimeuza", "i just sold", "sold some"
5. Only use "chat" for messages with ZERO connection to shop operations

Return ONLY a JSON object — no explanation, no markdown.

JSON format:
{
  "action": "<sell | pay_mpesa | check_stock | add_stock | report | trend | audit | restock | chat>",
  "item": "<product name in lowercase, or null>",
  "quantity": <integer, default 1>,
  "phone": "<07XXXXXXXX or null>",
  "payment": "<mpesa | cash | null>",
  "period": "<today | week | month | null>"
}

Examples — study these carefully:
"i have sold 1 sugar" -> {"action":"sell","item":"sugar","quantity":1,"phone":null,"payment":"cash","period":null}
"nimeuza sukari mbili" -> {"action":"sell","item":"sukari","quantity":2,"phone":null,"payment":"cash","period":null}
"just sold some salt" -> {"action":"sell","item":"salt","quantity":1,"phone":null,"payment":"cash","period":null}
"sold unga to customer" -> {"action":"sell","item":"unga","quantity":1,"phone":null,"payment":"cash","period":null}
"customer bought 3 bread" -> {"action":"sell","item":"bread","quantity":3,"phone":null,"payment":"cash","period":null}
"Sell 2 salt to 0724022458 via mpesa" -> {"action":"pay_mpesa","item":"salt","quantity":2,"phone":"0724022458","payment":"mpesa","period":null}
"kuna sukari ngapi?" -> {"action":"check_stock","item":"sukari","quantity":1,"phone":null,"payment":null,"period":null}
"what stock do we have?" -> {"action":"check_stock","item":null,"quantity":1,"phone":null,"payment":null,"period":null}
"add 50 unga to stock" -> {"action":"add_stock","item":"unga","quantity":50,"phone":null,"payment":null,"period":null}
"show today report" -> {"action":"report","item":null,"quantity":1,"phone":null,"payment":null,"period":"today"}
"what is trending?" -> {"action":"trend","item":null,"quantity":1,"phone":null,"payment":null,"period":"week"}
"habari yako" -> {"action":"chat","item":null,"quantity":1,"phone":null,"payment":null,"period":null}
"what is the weather?" -> {"action":"chat","item":null,"quantity":1,"phone":null,"payment":null,"period":null}

Return ONLY the JSON. No extra text. No markdown."""

# ── PROMPT 2: POS-only clarification persona ──
CHAT_PERSONA = """You are My Duka POS assistant. You ONLY help with shop operations.

You can ONLY help with:
- Recording sales (cash or M-Pesa)
- Checking stock levels
- Adding stock
- Sales reports
- Trends and best sellers
- Restock alerts
- M-Pesa payments

If the message is unclear, ask ONE short clarifying question to understand what shop action they want.
If the message has nothing to do with the shop, reply ONLY with:
"❓ I only handle shop operations. Try: sell, stock check, report, or restock."

NEVER answer general knowledge questions.
NEVER chat about topics unrelated to the shop.
NEVER search the web.
Keep all replies under 2 sentences.
Always reply in the same language the user used (English or Swahili)."""


def _call_ollama_sync(prompt: str, system: str = "", history: list = None) -> str:
    """Synchronous Ollama call — run in executor to avoid blocking async."""
    messages = []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model":    OLLAMA_MODEL,
        "messages": messages,
        "stream":   False,
        "system":   system
    }
    # Use /api/chat endpoint for multi-turn conversation
    resp = requests.post(
        OLLAMA_URL.replace("/api/generate", "/api/chat"),
        json=payload, timeout=60
    )
    data = resp.json()
    return data.get("message", {}).get("content", "").strip()


async def ask_ollama(user_message: str) -> dict | None:
    """
    STEP 1: Route the message — is it a POS action or general chat?
    Returns a parsed JSON dict with an 'action' field.
    """
    try:
        loop = asyncio.get_event_loop()
        raw  = await loop.run_in_executor(
            None,
            lambda: _call_ollama_sync(
                prompt = f"Message: {user_message}",
                system = ROUTER_PROMPT,
                history= []
            )
        )
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return None
    except Exception as e:
        print(f"\u26a0\ufe0f Ollama router error: {e}")
        return None


# ─────────────────────────────────────────
# WEB SEARCH  (DuckDuckGo — free, no API key)
# ─────────────────────────────────────────

def web_search(query: str, max_results: int = 3) -> str:
    """
    Search DuckDuckGo for a query and return a short text summary.
    Falls back gracefully if the internet is unavailable.
    """
    try:
        # DuckDuckGo Instant Answer API — completely free, no key needed
        url    = "https://api.duckduckgo.com/"
        params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
        resp   = requests.get(url, params=params, timeout=8)
        data   = resp.json()

        results = []

        # AbstractText = best single-sentence answer
        if data.get("AbstractText"):
            results.append(data["AbstractText"])

        # RelatedTopics = extra context snippets
        for topic in data.get("RelatedTopics", [])[:max_results]:
            text = topic.get("Text", "")
            if text and text not in results:
                results.append(text)

        # Answer = direct factual answer (e.g. "Paris" for capital of France)
        if data.get("Answer"):
            results.insert(0, data["Answer"])

        if results:
            return "\n".join(f"• {r}" for r in results[:max_results])

        # Fallback: try DuckDuckGo HTML search scrape for broader results
        html_resp = requests.get(
            f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8
        )
        # Extract snippets from result divs using simple regex
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html_resp.text)
        snippets = [re.sub(r'<[^>]+>', '', s).strip() for s in snippets[:max_results]]
        if snippets:
            return "\n".join(f"• {s}" for s in snippets)

        return ""
    except Exception as e:
        print(f"Search error: {e}")
        return ""


def needs_lookup(message: str) -> list[str]:
    """
    Ask Ollama if there are any unfamiliar words in the message that need
    to be looked up before answering. Returns a list of search queries.
    """
    try:
        check_prompt = f"""Read this message: "{message}"

Are there any words, names, local terms, slang, Swahili words, brand names,
or concepts that you are uncertain about and would benefit from a web search
before giving an accurate answer?

Reply ONLY with a JSON array of search queries (strings), or an empty array [].
Examples:
- "what is duka" -> ["duka meaning Kenya", "duka shop Kenya"]
- "tell me about safaricom" -> ["Safaricom Kenya telecom"]
- "how are you" -> []
- "what is M-Pesa" -> ["M-Pesa mobile money Kenya"]
- "sell 2 salt" -> []

Return ONLY the JSON array. No explanation."""

        raw = _call_ollama_sync(prompt=check_prompt, system="", history=[])
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if match:
            queries = json.loads(match.group())
            return [q for q in queries if isinstance(q, str) and len(q) > 2]
        return []
    except Exception:
        return []


async def ask_ollama_chat(user_id: int, user_message: str) -> str:
    """
    Fallback for unrecognised messages — asks user to clarify what POS action they want.
    No general chat, no web search — shop only.
    """
    try:
        loop  = asyncio.get_event_loop()
        reply = await loop.run_in_executor(
            None,
            lambda: _call_ollama_sync(
                prompt  = user_message,
                system  = CHAT_PERSONA,
                history = []  # no memory needed, just clarification
            )
        )
        return reply or "❓ Sijaelewa. Jaribu: *uza, stock, report, au restock*."

    except Exception as e:
        err = str(e)
        print(f"⚠️ Ollama chat error: {err}")
        return (
            "❓ *Sijaelewa amri hiyo.*\n\n"
            "Unaweza kusema:\n"
            "• `Uza 1 sukari kwa cash`\n"
            "• `Angalia stock ya unga`\n"
            "• `Onyesha ripoti ya leo`\n"
            "• `Niambie nini kinahitaji kuongezwa`"
        )

# ─────────────────────────────────────────
# 5. POS ACTION HANDLERS  (all DB work via pos_engine → MySQL)
# ─────────────────────────────────────────

async def action_sell(data: dict, update: Update, prefix: str):
    item     = (data.get("item") or "").lower()
    quantity = int(data.get("quantity") or 1)
    phone    = data.get("phone")
    payment  = (data.get("payment") or "cash").lower()

    if not item:
        return await update.message.reply_text(f"{prefix}❓ Which item do you want to sell?")

    price = pos_engine.get_item_price(item)
    if isinstance(price, str):  # returns error string if not found
        return await update.message.reply_text(
            f"{prefix}❌ Item *{item}* not found in inventory.", parse_mode="Markdown")

    total = price * quantity

    if payment == "mpesa" and phone:
        await update.message.reply_text(
            f"{prefix}🚀 Sending STK Push of *KES {total}* to {phone}...",
            parse_mode="Markdown")
        result = mpesa_api.trigger_stk_push(phone, total, item=item, quantity=quantity)
        await update.message.reply_text(f"{prefix}{result}", parse_mode="Markdown")
    else:
        res = pos_engine.process_sale(item, quantity, phone=phone or "cash", payment_method="cash")

        # Generate PDF receipt for cash sale
        from datetime import datetime
        receipt_number = f"CASH-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        receipt_path   = receipt_engine.generate_receipt(
            item           = item,
            quantity       = quantity,
            unit_price     = price,
            total          = total,
            payment_method = "Cash",
            customer_phone = phone or "Walk-in",
            receipt_number = receipt_number
        )

        await update.message.reply_text(
            f"{prefix}✅ *Cash Sale Recorded*\n"
            f"📦 {quantity}x {item.title()} @ KES {price}\n"
            f"💰 Total: KES {total}\n"
            f"📄 Receipt: `{receipt_number}`\n_{res}_",
            parse_mode="Markdown")

        # Send the PDF receipt file directly in Telegram
        with open(receipt_path, "rb") as pdf:
            await update.message.reply_document(
                document = pdf,
                filename = f"Receipt_{receipt_number}.pdf",
                caption  = "Here is your receipt!"
            )


async def action_pay_mpesa(data: dict, update: Update, prefix: str):
    phone    = data.get("phone")
    item     = (data.get("item") or "").lower()
    quantity = int(data.get("quantity") or 1)

    if not phone:
        return await update.message.reply_text(
            f"{prefix}❌ Please include a phone number, e.g. *0724022458*",
            parse_mode="Markdown")

    if item:
        price = pos_engine.get_item_price(item)
        if isinstance(price, str) and "Error" in price:
            return await update.message.reply_text(f"{prefix}❌ {price}")
        total = price * quantity
        await update.message.reply_text(
            f"{prefix}💰 *{quantity}x {item.title()} = KES {total}*\n"
            f"🚀 Sending STK Push to {phone}...",
            parse_mode="Markdown")
        result = mpesa_api.trigger_stk_push(phone, total, item=item, quantity=quantity)
    else:
        total  = int(data.get("quantity") or 1)
        await update.message.reply_text(
            f"{prefix}🚀 Sending STK Push of *KES {total}* to {phone}...",
            parse_mode="Markdown")
        result = mpesa_api.trigger_stk_push(phone, total)

    await update.message.reply_text(f"{prefix}{result}", parse_mode="Markdown")


async def action_check_stock(data: dict, update: Update, prefix: str):
    item = (data.get("item") or "").lower()
    res  = pos_engine.check_stock(item)
    await update.message.reply_text(f"{prefix}{res}", parse_mode="Markdown")


async def action_add_stock(data: dict, update: Update, prefix: str):
    item     = (data.get("item") or "").lower()
    quantity = int(data.get("quantity") or 0)

    if not item or not quantity:
        return await update.message.reply_text(
            f"{prefix}❓ Please specify item and quantity.\nExample: *Add 50 unga*",
            parse_mode="Markdown")

    res = pos_engine.add_stock(item, quantity)
    await update.message.reply_text(f"{prefix}{res}", parse_mode="Markdown")


async def action_report(data: dict, update: Update, prefix: str):
    period    = data.get("period") or "today"
    date_from = data.get("date_from")
    date_to   = data.get("date_to")
    res = pos_engine.get_sales_history(
        period    = period,
        date_from = date_from,
        date_to   = date_to
    )
    await update.message.reply_text(f"{prefix}{res}", parse_mode="Markdown")


async def action_trend(data: dict, update: Update, prefix: str):
    res = pos_engine.get_trends()
    await update.message.reply_text(f"{prefix}{res}", parse_mode="Markdown")


async def action_restock(data: dict, update: Update, prefix: str):
    """Show items that urgently need restocking, sorted by priority."""
    conn   = pos_engine.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT item_name, stock_quantity, selling_price
        FROM Inventory
        ORDER BY stock_quantity ASC
        LIMIT 10
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        return await update.message.reply_text(f"{prefix}📦 No inventory data found.")

    msg = f"{prefix}\U0001f6d2 *Restock Priority List*\n\n"
    for r in rows:
        qty  = r[1]
        if qty < 5:
            icon = "🔴 URGENT"
        elif qty < 15:
            icon = "🟡 LOW"
        else:
            icon = "🟢 OK"
        msg += f"{icon} \u2014 *{r[0]}* ({qty} units left)\n"

    msg += "\n_Items sorted by urgency. Restock red items first!_"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def action_audit(data: dict, update: Update, prefix: str):
    res = pos_engine.system_audit()
    await update.message.reply_text(f"{prefix}{res}", parse_mode="Markdown")

# ─────────────────────────────────────────
# 6. ROLE-BASED AUTHORIZATION
# ─────────────────────────────────────────

# Track users mid-registration (waiting for invite code)
pending_registration: dict[int, str] = {}   # {telegram_id: name}

def get_role(user_id: int) -> str:
    """Return 'admin', 'attendant', or 'stranger'."""
    if user_id == MY_ADMIN_ID:
        return "admin"
    user = get_user(user_id)
    if user and user.get("is_active"):
        return user.get("role", "attendant")
    return "stranger"

def can_do(user_or_id, action: str) -> bool:
    """Check permission — accepts either a user dict OR a user_id int."""
    # Extract id if dict passed
    if isinstance(user_or_id, dict):
        user_id = user_or_id.get("user_id", 0)
        role    = user_or_id.get("role", "attendant")
    else:
        user_id = user_or_id
        role    = get_role(user_id)
    if user_id == MY_ADMIN_ID or role == "admin":
        return True
    if role == "attendant":
        return action in ATTENDANT_PERMISSIONS
    return False

def notify_admin(message: str):
    """Send a silent alert to admin via Telegram Bot API."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id":    MY_ADMIN_ID,
            "text":       message,
            "parse_mode": "Markdown"
        }, timeout=5)
    except Exception:
        pass


# ─────────────────────────────────────────
# CUSTOMER SELF-CHECKOUT (PUBLIC BOT)
# ─────────────────────────────────────────

MENU_PAGE_SIZE = 8


def _is_customer(user_id: int) -> bool:
    """Customers are non-admin, non-attendant users."""
    return get_role(user_id) == "stranger"


def _db():
    return pos_engine.get_connection()


def _get_menu_page(page: int = 0) -> tuple[list[dict], int]:
    """Return (items, total_count) for inventory items in stock."""
    conn = _db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT COUNT(*) AS c FROM Inventory WHERE stock_quantity > 0")
    total = int((cur.fetchone() or {}).get("c") or 0)
    offset = max(0, int(page)) * MENU_PAGE_SIZE
    cur.execute(
        "SELECT item_id,item_name,selling_price,stock_quantity FROM Inventory "
        "WHERE stock_quantity > 0 ORDER BY item_name LIMIT %s OFFSET %s",
        (MENU_PAGE_SIZE, offset),
    )
    rows = cur.fetchall() or []
    cur.close()
    conn.close()
    for r in rows:
        r["selling_price"] = float(r.get("selling_price") or 0)
        r["stock_quantity"] = int(r.get("stock_quantity") or 0)
    return rows, total


def _get_items_by_ids(item_ids: list[int]) -> dict[int, dict]:
    if not item_ids:
        return {}
    conn = _db()
    cur = conn.cursor(dictionary=True)
    placeholders = ",".join(["%s"] * len(item_ids))
    cur.execute(
        f"SELECT item_id,item_name,selling_price,stock_quantity FROM Inventory WHERE item_id IN ({placeholders})",
        tuple(item_ids),
    )
    rows = cur.fetchall() or []
    cur.close()
    conn.close()
    out = {}
    for r in rows:
        r["selling_price"] = float(r.get("selling_price") or 0)
        r["stock_quantity"] = int(r.get("stock_quantity") or 0)
        out[int(r["item_id"])] = r
    return out


def _cart_get(context: ContextTypes.DEFAULT_TYPE) -> dict[int, int]:
    cart = context.user_data.get("cust_cart")
    if not isinstance(cart, dict):
        cart = {}
    out: dict[int, int] = {}
    for k, v in cart.items():
        try:
            out[int(k)] = int(v)
        except Exception:
            pass
    context.user_data["cust_cart"] = out
    return out


def _cart_clear(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in ("cust_cart", "cust_step", "cust_pending_item_id"):
        context.user_data.pop(k, None)


def _cart_totals(cart: dict[int, int]) -> tuple[list[dict], float]:
    items = _get_items_by_ids(list(cart.keys()))
    lines: list[dict] = []
    total = 0.0
    for iid, qty in cart.items():
        itm = items.get(iid)
        if not itm:
            continue
        qty = max(1, int(qty))
        unit = float(itm["selling_price"])
        line_total = unit * qty
        total += line_total
        lines.append({
            "item_id": iid,
            "name": itm["item_name"],
            "qty": qty,
            "unit_price": unit,
            "line_total": line_total,
        })
    return lines, float(total)


def _inventory_name_index() -> list[dict]:
    """Return list of {item_id, name} for in-stock items, lowercased for matching."""
    conn = _db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT item_id,item_name FROM Inventory WHERE stock_quantity > 0 ORDER BY LENGTH(item_name) DESC")
    rows = cur.fetchall() or []
    cur.close()
    conn.close()
    out = []
    for r in rows:
        out.append({"item_id": int(r["item_id"]), "name": str(r["item_name"] or ""), "name_l": str(r["item_name"] or "").lower()})
    return out


def _parse_customer_multi_add(text: str) -> list[tuple[int, int]]:
    """
    Parse messages like:
      - '2 mayai, 1 kensalt, bread 1'
      - 'mayai 2 and kensalt 1'
    Returns list of (item_id, qty) matched from Inventory names.
    """
    t = (text or "").lower().strip()
    if not t:
        return []
    # normalize separators
    t = t.replace(" and ", ",").replace(" pamoja ", ",").replace(" na ", ",")
    parts = [p.strip() for p in t.split(",") if p.strip()]
    if not parts:
        parts = [t]

    idx = _inventory_name_index()
    out: list[tuple[int, int]] = []
    for p in parts:
        # find quantity (first integer token); default 1
        m = re.search(r"\b(\d+)\b", p)
        qty = int(m.group(1)) if m else 1
        if qty <= 0:
            continue
        # remove the qty token to get candidate name
        name_part = re.sub(r"\b\d+\b", " ", p)
        name_part = re.sub(r"\s+", " ", name_part).strip()
        if not name_part:
            continue
        best = None
        best_len = 0
        for it in idx:
            nl = it["name_l"]
            if not nl:
                continue
            if nl in name_part or name_part in nl:
                if len(nl) > best_len:
                    best = it
                    best_len = len(nl)
        if best:
            out.append((int(best["item_id"]), qty))
    return out


async def customer_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Order now", callback_data="cust:menu:0")],
        [InlineKeyboardButton("View cart", callback_data="cust:cart")],
    ])
    if update.message:
        await update.message.reply_text(
            "Welcome to My Duka.\n\nTap *Order now* to browse the menu.",
            parse_mode="Markdown",
            reply_markup=kb,
        )
    else:
        await update.callback_query.message.reply_text(
            "Welcome to My Duka.\n\nTap *Order now* to browse the menu.",
            parse_mode="Markdown",
            reply_markup=kb,
        )


async def customer_show_menu(query, context: ContextTypes.DEFAULT_TYPE, page: int):
    rows, total = _get_menu_page(page)
    if total <= 0:
        return await query.message.edit_text("Menu is empty right now. Please try again later.")

    buttons = []
    for r in rows:
        label = f"{r['item_name']} — KES {r['selling_price']:.0f}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"cust:item:{r['item_id']}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("Prev", callback_data=f"cust:menu:{page-1}"))
    if (page + 1) * MENU_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("Next", callback_data=f"cust:menu:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("View cart", callback_data="cust:cart")])
    buttons.append([InlineKeyboardButton("Cancel", callback_data="cust:cancel")])

    await query.message.edit_text(
        f"*Menu* (page {page+1})\n"
        f"Select an item to add it to your cart.\n\n"
        f"Tip: you can also type multiple items like:\n"
        f"`2 mayai, 1 kensalt, 1 bread`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def customer_show_cart(query, context: ContextTypes.DEFAULT_TYPE):
    cart = _cart_get(context)
    if not cart:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Order now", callback_data="cust:menu:0")],
            [InlineKeyboardButton("Cancel", callback_data="cust:cancel")],
        ])
        return await query.message.edit_text("Your cart is empty.", reply_markup=kb)

    lines, total = _cart_totals(cart)
    msg = "*Your cart*\n\n"
    for l in lines:
        msg += f"- {l['name']} ×{l['qty']} = KES {l['line_total']:.0f}\n"
    msg += f"\n*Total*: KES {total:.0f}"

    flat = [InlineKeyboardButton(f"Remove {l['name']}", callback_data=f"cust:rm:{l['item_id']}") for l in lines[:6]]
    rem_rows = [flat[i:i+2] for i in range(0, len(flat), 2)]
    rem_rows.append([InlineKeyboardButton("Checkout (M-Pesa)", callback_data="cust:checkout")])
    rem_rows.append([InlineKeyboardButton("Back to menu", callback_data="cust:menu:0")])
    rem_rows.append([InlineKeyboardButton("Cancel", callback_data="cust:cancel")])

    await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rem_rows))


def _create_customer_order(user, phone: str, lines: list[dict], total: float, checkout_id: str | None) -> int:
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO Customer_Orders (telegram_user_id, telegram_username, telegram_full_name, phone, status, total_amount, checkout_request_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (
            int(user.id),
            (user.username or ""),
            (user.full_name or ""),
            phone,
            "PendingPayment",
            float(total),
            checkout_id,
        ),
    )
    order_id = int(cur.lastrowid)
    for l in lines:
        cur.execute(
            "INSERT INTO Customer_Order_Items (order_id,item_id,item_name,qty,unit_price,line_total) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (order_id, int(l["item_id"]), l["name"], int(l["qty"]), float(l["unit_price"]), float(l["line_total"])),
        )
    conn.commit()
    cur.close()
    conn.close()
    return order_id


def _update_customer_order_payment(order_id: int, status: str, mpesa_receipt: str | None = None, phone: str | None = None):
    conn = _db()
    cur = conn.cursor()
    if status == "PaidPendingCollection":
        cur.execute(
            "UPDATE Customer_Orders SET status=%s, mpesa_receipt=%s, phone=COALESCE(%s, phone), paid_at=NOW() WHERE order_id=%s",
            (status, mpesa_receipt, phone, order_id),
        )
    else:
        cur.execute("UPDATE Customer_Orders SET status=%s WHERE order_id=%s", (status, order_id))
    conn.commit()
    cur.close()
    conn.close()


async def _poll_and_finalize(context: ContextTypes.DEFAULT_TYPE, chat_id: int, order_id: int, checkout_id: str, phone: str, lines: list[dict], total: float):
    status_url = f"http://127.0.0.1:5000/mpesa/status/{checkout_id}"
    mpesa_code = None
    final_status = None
    for _ in range(60):  # ~3 minutes
        try:
            r = requests.get(status_url, timeout=5).json()
            st = (r.get("status") or "pending").lower()
            if st == "completed":
                final_status = "Completed"
                mpesa_code = r.get("mpesa_code")
                break
            if st == "failed":
                final_status = "Failed"
                break
        except Exception:
            pass
        await asyncio.sleep(3)

    if final_status != "Completed":
        _update_customer_order_payment(order_id, "Failed")
        await context.bot.send_message(chat_id=chat_id, text="Payment not completed. If you cancelled, please try again.")
        return

    _update_customer_order_payment(order_id, "PaidPendingCollection", mpesa_receipt=mpesa_code, phone=phone)

    receipt_number = f"MPESA-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    receipt_path = receipt_engine.generate_receipt_cart(
        items=lines,
        total=total,
        payment_method="M-Pesa",
        mpesa_code=mpesa_code,
        customer_phone=phone,
        receipt_number=receipt_number,
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text="Payment received. Please collect your order at the counter. A cashier will approve collection.",
    )
    with open(receipt_path, "rb") as pdf:
        await context.bot.send_document(
            chat_id=chat_id,
            document=pdf,
            filename=f"Receipt_{receipt_number}.pdf",
            caption="Here is your receipt (PDF).",
        )

# ─────────────────────────────────────────
# 7. MAIN MESSAGE HANDLER
# ─────────────────────────────────────────
async def handle_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id   = update.message.chat_id
    user_name = update.message.from_user.first_name or "User"
    user_text = (update.message.text or "").strip()

    if not user_text:
        return

    role = get_role(user_id)

    # ── Stranger: check if they are mid-registration ──
    if role == "stranger":
        # Customer self-checkout steps
        step = context.user_data.get("cust_step")
        if step == "qty":
            try:
                m = re.search(r"\b(\d+)\b", user_text)
                qty = int(m.group(1)) if m else 0
            except Exception:
                qty = 0
            if qty <= 0:
                await update.message.reply_text("Please enter a valid quantity (example: 2).")
                return
            iid = int(context.user_data.get("cust_pending_item_id") or 0)
            if iid <= 0:
                context.user_data.pop("cust_step", None)
                await customer_welcome(update, context)
                return
            cart = _cart_get(context)
            cart[iid] = cart.get(iid, 0) + qty
            context.user_data["cust_cart"] = cart
            context.user_data.pop("cust_step", None)
            context.user_data.pop("cust_pending_item_id", None)
            await update.message.reply_text("Added to cart. Tap /start to continue.")
            return
        if step == "phone":
            phone = user_text.strip()
            cleaned = phone.replace(" ", "").replace("-", "")
            if not re.search(r"^(07|01|2547|2541)\d{8}$", cleaned):
                await update.message.reply_text("Please enter a valid Kenyan phone number (example: 0724000000).")
                return
            cart = _cart_get(context)
            lines, total = _cart_totals(cart)
            if not lines:
                context.user_data.pop("cust_step", None)
                await update.message.reply_text("Your cart is empty. Tap /start to order.")
                return

            info = mpesa_api.trigger_stk_push_info(
                phone_number=cleaned,
                amount=total,
                item="Telegram Order",
                quantity=1,
                cart_json=json.dumps([{"item_id": l["item_id"], "qty": l["qty"]} for l in lines]),
            )
            checkout_id = info.get("checkout_id")
            if not info.get("ok"):
                context.user_data.pop("cust_step", None)
                await update.message.reply_text(info.get("message") or "M-Pesa failed. Try again later.", parse_mode="Markdown")
                return

            order_id = _create_customer_order(update.message.from_user, cleaned, lines, total, checkout_id)
            _cart_clear(context)
            await update.message.reply_text(info.get("message") or "STK sent. Please complete payment.", parse_mode="Markdown")
            if checkout_id:
                asyncio.create_task(_poll_and_finalize(context, user_id, order_id, checkout_id, cleaned, lines, total))
            else:
                await update.message.reply_text("STK sent but checkout id was missing. Please wait and try again.")
            return

        # Smart add: allow customers to type multiple items at once (adds to cart)
        adds = _parse_customer_multi_add(user_text)
        if adds:
            cart = _cart_get(context)
            for iid, qty in adds:
                cart[iid] = cart.get(iid, 0) + int(qty)
            context.user_data["cust_cart"] = cart
            # Show cart directly
            dummy = type("Q", (), {})()
            dummy.message = update.message
            return await customer_show_cart(dummy, context)

        if user_id in pending_registration:
            # They just sent the invite code
            if user_text.strip().upper() == INVITE_CODE:
                name = pending_registration.pop(user_id)
                success = pos_engine.register_user(user_id, name, role="attendant")
                if success:
                    await update.message.reply_text(
                        f"✅ *Welcome to My Duka POS, {name}!*\n\n"
                        f"You are now registered as a *Shop Attendant*.\n\n"
                        f"You can:\n"
                        f"• Sell items (cash or M-Pesa)\n"
                        f"• Check and add stock\n"
                        f"• View sales reports\n\n"
                        f"_Type anything to get started!_",
                        parse_mode="Markdown"
                    )
                    notify_admin(
                        f"👤 *New Attendant Registered!*\n"
                        f"Name: *{name}*\n"
                        f"Telegram ID: `{user_id}`"
                    )
                else:
                    await update.message.reply_text("❌ Registration failed. Please try again.")
            else:
                await update.message.reply_text(
                    "❌ *Wrong invite code.*\n"
                    "Please ask the shop owner for the correct code.\n"
                    "Type /register to try again.",
                    parse_mode="Markdown"
                )
            return

        # Default: treat as customer (self-service)
        await customer_welcome(update, context)
        return

    status_msg   = await update.message.reply_text("🧠 Thinking...")
    data         = None
    engine_used  = "🧠 AI"

    # ── Step A: Local parser first (instant, no RAM usage) ──
    data        = parse_user_intent_local(user_text)
    engine_used = "⚡ Local"

    # ── DB item lookup if item missing ──
    if data.get("action") in ("sell", "pay_mpesa", "add_stock", "check_stock"):
        if not data.get("item"):
            try:
                conn   = pos_engine.get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT item_name FROM Inventory")
                db_items = [row[0].lower() for row in cursor.fetchall()]
                cursor.close()
                conn.close()
                for word in user_text.lower().split():
                    word = word.strip(".,!?")
                    for db_item in db_items:
                        if word in db_item or db_item in word:
                            data["item"] = db_item
                            break
                    if data.get("item"):
                        break
            except Exception:
                pass

    # ── Step B: Only use Ollama if local parser couldn't identify action ──
    if data.get("action") == "chat":
        try:
            ai_data = await asyncio.wait_for(ask_ollama(user_text), timeout=8.0)
            if ai_data and ai_data.get("action") != "chat":
                data        = ai_data
                engine_used = "🧠 AI"
        except Exception as e:
            print(f"⚠️ Ollama skipped: {e}")

    action = data.get("action", "chat")
    prefix = f"[{engine_used}] "

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Engine:{engine_used} | Action:{action} | Role:{role} | Data:{data}")

    # ── Step C: Check permission before running action ──
    if action != "chat" and not can_do(user_id, action):
        await status_msg.edit_text(
            f"🚫 *Permission Denied*\n\n"
            f"You do not have access to: *{action}*\n"
            f"Please contact the shop owner.",
            parse_mode="Markdown"
        )
        notify_admin(
            f"🚫 *Blocked Action*\n"
            f"User: {user_name} (`{user_id}`)\n"
            f"Tried: `{action}`"
        )
        return

    await status_msg.delete()

    try:
        if action in ("sell",):
            await action_sell(data, update, prefix)

        elif action == "pay_mpesa":
            await action_pay_mpesa(data, update, prefix)

        elif action == "check_stock":
            await action_check_stock(data, update, prefix)

        elif action == "add_stock":
            await action_add_stock(data, update, prefix)

        elif action == "report":
            await action_report(data, update, prefix)

        elif action == "trend":
            await action_trend(data, update, prefix)

        elif action == "audit":
            await action_audit(data, update, prefix)

        elif action == "restock":
            await action_restock(data, update, prefix)

        else:
            # ── Unrecognised message — ask for clarification ──
            reply = await ask_ollama_chat(update.message.chat_id, user_text)
            await update.message.reply_text(reply, parse_mode="Markdown")

    except Exception as e:
        print(f"❌ Error executing action '{action}': {e}")
        await update.message.reply_text(
            f"❌ System error while processing your request.\nError: `{e}`",
            parse_mode="Markdown")

# ─────────────────────────────────────────
# 8. COMMANDS
# ─────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    if _is_customer(user_id):
        await customer_welcome(update, context)
        return
    await update.message.reply_text(
        "🛒 *Duka POS Bot is Online!*\n\n"
        "Powered by local AI — no internet needed.\n\n"
        "Try:\n"
        "• `Sell 1 salt and charge 0724022458 via mpesa`\n"
        "• `Je, tuna unga ngapi?`\n"
        "• `Show today's sales report`\n"
        "• `What's trending this week?`",
        parse_mode="Markdown")


async def customer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not _is_customer(user_id):
        return

    data = query.data or ""
    if data.startswith("cust:menu:"):
        page = int(data.split(":")[-1] or 0)
        return await customer_show_menu(query, context, page)
    if data.startswith("cust:item:"):
        iid = int(data.split(":")[-1] or 0)
        context.user_data["cust_step"] = "qty"
        context.user_data["cust_pending_item_id"] = iid
        return await query.message.reply_text("Enter quantity for the selected item (example: 2).")
    if data == "cust:cart":
        return await customer_show_cart(query, context)
    if data.startswith("cust:rm:"):
        iid = int(data.split(":")[-1] or 0)
        cart = _cart_get(context)
        cart.pop(iid, None)
        context.user_data["cust_cart"] = cart
        return await customer_show_cart(query, context)
    if data == "cust:checkout":
        cart = _cart_get(context)
        lines, total = _cart_totals(cart)
        if not lines:
            return await query.message.reply_text("Your cart is empty.")
        context.user_data["cust_step"] = "phone"
        return await query.message.reply_text(f"Total is KES {total:.0f}. Enter your phone number for M-Pesa STK (example: 0724000000).")
    if data == "cust:cancel":
        _cart_clear(context)
        return await query.message.edit_text("Cancelled. Tap /start to begin again.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Commands & Examples:*\n\n"
        "💰 *Sales*\n"
        "`Sell 2 salt to 0724022458 via mpesa`\n"
        "`Niuzie sukari moja kwa cash`\n\n"
        "📦 *Stock*\n"
        "`How much unga do we have?`\n"
        "`Add 100 rice to stock`\n\n"
        "📊 *Reports*\n"
        "`Show today's report`\n"
        "`Weekly sales summary`\n\n"
        "📈 *Trends*\n"
        "`What is selling best this week?`\n\n"
        "🔍 *Audit*\n"
        "`Check for suspicious transactions`",
        parse_mode="Markdown")

# ─────────────────────────────────────────
# 9. STARTUP
# ─────────────────────────────────────────
async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear conversation memory for this user."""
    user_id = update.message.chat_id
    conversation_history.pop(user_id, None)
    await update.message.reply_text(
        "🧹 *Conversation memory cleared!*\nFresh start!",
        parse_mode="Markdown")

# ── /adduser — admin generates an invite code ──
async def adduser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != MY_ADMIN_ID:
        return await update.message.reply_text("🚫 Only the admin can create invite codes.")
    code = generate_invite_code()
    await update.message.reply_text(
        f"✅ *New Invite Code Generated*\n\n"
        f"Share this code with your attendant:\n"
        f"┌─────────────────┐\n"
        f"│   `{code}`   │\n"
        f"└─────────────────┘\n\n"
        f"They must type: `/join {code}`\n"
        f"⚠️ Code expires after one use.",
        parse_mode="Markdown"
    )

# ── /join CODE — attendant registers ──
async def join_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.message.chat_id
    args     = context.args
    username  = update.message.from_user.username or "unknown"
    full_name = update.message.from_user.full_name or "Unknown"

    # Already registered?
    existing = get_user(user_id)
    if existing and existing["is_active"]:
        return await update.message.reply_text(
            f"✅ You are already registered as *{existing['role']}*.",
            parse_mode="Markdown"
        )

    if not args:
        return await update.message.reply_text(
            "❓ Please provide the invite code.\nExample: `/join 482910`",
            parse_mode="Markdown"
        )

    code = args[0].strip()
    if use_invite_code(code):
        register_user(user_id, username, full_name, role="attendant")
        await update.message.reply_text(
            f"🎉 *Welcome to My Duka POS, {full_name}!*\n\n"
            f"You are now registered as an *Attendant*.\n\n"
            f"You can:\n"
            f"• Record cash and M-Pesa sales\n"
            f"• Check stock levels\n"
            f"• Add stock\n"
            f"• View sales reports\n\n"
            f"Type anything to get started!",
            parse_mode="Markdown"
        )
        # Notify admin
        await context.bot.send_message(
            chat_id    = MY_ADMIN_ID,
            text       = f"👤 *New Attendant Registered!*\n"
                         f"Name : {full_name}\n"
                         f"Username : @{username}\n"
                         f"ID : `{user_id}`",
            parse_mode = "Markdown"
        )
    else:
        await update.message.reply_text(
            "❌ *Invalid or already used invite code.*\n"
            "Ask the shop owner for a new one.",
            parse_mode="Markdown"
        )

# ── /users — admin views all registered users ──
async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != MY_ADMIN_ID:
        return await update.message.reply_text("🚫 Admin only.")
    users = get_all_users()
    if not users:
        return await update.message.reply_text("No users registered yet.")
    msg = "👥 *Registered Users*\n\n"
    for u in users:
        icon   = "👑" if u["role"] == "admin" else "👤"
        status = "✅" if u["is_active"] else "❌"
        msg   += (f"{icon} *{u['full_name']}* (@{u['username']})\n"
                  f"   Role: {u['role']} | {status} | ID: `{u['user_id']}`\n\n")
    await update.message.reply_text(msg, parse_mode="Markdown")

# ── /removeuser ID — admin deactivates a user ──
async def removeuser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != MY_ADMIN_ID:
        return await update.message.reply_text("🚫 Admin only.")
    if not context.args:
        return await update.message.reply_text(
            "Usage: `/removeuser USER_ID`", parse_mode="Markdown")
    try:
        target_id = int(context.args[0])
        remove_user(target_id)
        await update.message.reply_text(f"✅ User `{target_id}` has been deactivated.", parse_mode="Markdown")
        # Notify the removed user
        try:
            await context.bot.send_message(
                chat_id = target_id,
                text    = "⚠️ Your access to My Duka POS has been revoked by the admin."
            )
        except Exception:
            pass
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the user their Telegram ID and current role."""
    user_id = update.message.chat_id
    role    = get_role(user_id)
    role_emoji = {"admin": "👑", "attendant": "👤", "stranger": "🔒"}.get(role, "❓")
    await update.message.reply_text(
        f"📋 *Your Info*\n\n"
        f"🆔 Telegram ID: `{user_id}`\n"
        f"{role_emoji} Role: *{role.title()}*",
        parse_mode="Markdown")

async def register_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the registration flow for new attendants."""
    user_id   = update.message.chat_id
    user_name = update.message.from_user.first_name or "User"
    role      = get_role(user_id)

    if role in ("admin", "attendant"):
        await update.message.reply_text(
            f"✅ You are already registered as *{role.title()}*. No need to register again!",
            parse_mode="Markdown")
        return

    # Save name and wait for invite code
    pending_registration[user_id] = user_name
    await update.message.reply_text(
        f"👋 Hello *{user_name}!*\n\n"
        f"To access *My Duka POS*, please enter the *invite code* "
        f"given to you by the shop owner:",
        parse_mode="Markdown")

async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin only: list all registered users."""
    user_id = update.message.chat_id
    if get_role(user_id) != "admin":
        await update.message.reply_text("🚫 Admin only command.")
        return

    users = pos_engine.list_users()
    if not users:
        await update.message.reply_text("👥 No users registered yet.")
        return

    role_emoji = {"admin": "👑", "attendant": "👤"}
    msg = "👥 *Registered Users:*\n\n"
    for u in users:
        emoji  = role_emoji.get(u["role"], "👤")
        status = "✅" if u["is_active"] else "❌"
        msg   += (
            f"{emoji} *{u['name']}*\n"
            f"   ID: `{u['telegram_id']}`\n"
            f"   Role: {u['role']} | Active: {status}\n\n"
        )
    msg += f"_Total: {len(users)} user(s)_\n"
    msg += f"\nTo remove: `/removeuser <telegram_id>`"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def removeuser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin only: remove an attendant by Telegram ID."""
    user_id = update.message.chat_id
    if get_role(user_id) != "admin":
        await update.message.reply_text("🚫 Admin only command.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: `/removeuser <telegram_id>`\n"
            "Find IDs with /users",
            parse_mode="Markdown")
        return

    try:
        target_id = int(args[0])
        if target_id == MY_ADMIN_ID:
            await update.message.reply_text("❌ Cannot remove the admin account.")
            return
        success = pos_engine.remove_user(target_id)
        if success:
            await update.message.reply_text(f"✅ User `{target_id}` has been removed.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ User `{target_id}` not found.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Invalid ID. Usage: `/removeuser 123456789`", parse_mode="Markdown")

async def invite_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin only: show the current invite code."""
    user_id = update.message.chat_id
    if get_role(user_id) != "admin":
        await update.message.reply_text("🚫 Admin only command.")
        return
    await update.message.reply_text(
        f"🔑 *Current Invite Code:*\n\n"
        f"`{INVITE_CODE}`\n\n"
        f"Share this with your shop attendants.\n"
        f"They must type /register first, then enter this code.",
        parse_mode="Markdown")

# ─────────────────────────────────────────
# VOICE MESSAGE HANDLER
# ─────────────────────────────────────────

async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Converts voice messages to text using Whisper, then processes
    the transcribed text through the normal AI/POS pipeline.
    """
    user_id = update.message.chat_id

    # Check registration
    user = get_user(user_id)
    if not user:
        return await update.message.reply_text(
            "🔒 You are not registered. Ask admin for an invite code.\n"
            "Then type: /join YOUR_CODE"
        )

    # Show processing indicator
    status_msg = await update.message.reply_text("🎙️ *Listening to your voice message...*", parse_mode="Markdown")

    try:
        model = get_whisper()
        if not model:
            await status_msg.delete()
            return await update.message.reply_text(
                "⚠️ Voice commands not available.\n"
                "Run this to enable them:\n"
                "`pip install openai-whisper`\n"
                "Then restart the bot.",
                parse_mode="Markdown"
            )

        # Download voice file from Telegram
        voice_file = await update.message.voice.get_file()
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name

        await voice_file.download_to_drive(tmp_path)

        # Transcribe with Whisper
        await status_msg.edit_text("🎙️ *Transcribing...*", parse_mode="Markdown")
        # Try English first (faster), fall back to Swahili if confidence low
        result = model.transcribe(tmp_path, language="en", fp16=False)
        # If result looks like garbage (short or has non-latin chars), try Swahili
        text_check = result["text"].strip()
        has_garbage = len(text_check) < 3 or any(ord(c) > 1000 for c in text_check)
        if has_garbage:
            result = model.transcribe(tmp_path, language="sw", fp16=False)
            text_check2 = result["text"].strip()
            # If still garbage, default to English result
            if any(ord(c) > 1000 for c in text_check2):
                result = model.transcribe(tmp_path, language="en", fp16=False)
        transcript = result["text"].strip()
        lang       = result.get("language", "unknown")
        os.unlink(tmp_path)  # clean up temp file

        # Reject garbage transcripts
        has_garbage = any(ord(c) > 1000 for c in transcript)
        if not transcript or len(transcript) < 2 or has_garbage:
            await status_msg.delete()
            return await update.message.reply_text(
                "❓ *Could not understand the voice message.*\n"
                "Please speak clearly in English or Swahili and try again.",
                parse_mode="Markdown"
            )

        print(f"🎙️ Voice [{lang}]: {transcript}")

        # Show what was heard
        await status_msg.edit_text(
            f"🎙️ *I heard:* _{transcript}_",
            parse_mode="Markdown"
        )

        # ── Now process the transcribed text exactly like a typed message ──
        # Simulate a text message update with the transcript
        data        = None
        engine_used = "🧠 AI"

        # ── Clean transcript: fix spoken numbers and M-Pesa variants ──
        clean_transcript = re.sub(r'(\d)[\s\-](\d)', r'\1\2', transcript)
        clean_transcript = re.sub(r'(\d)[\s\-](\d)', r'\1\2', clean_transcript)
        clean_transcript = re.sub(r'm[\-\s]?pas[ae]r?', 'mpesa', clean_transcript, flags=re.IGNORECASE)
        clean_transcript = re.sub(r'em[\s\-]?pec?[ae]', 'mpesa', clean_transcript, flags=re.IGNORECASE)
        clean_transcript = re.sub(r'empe[cs][ae]', 'mpesa', clean_transcript, flags=re.IGNORECASE)
        print(f"🎙️ Cleaned: {clean_transcript}")

        # ── FAST PATH: local parser first (instant, no network) ──
        data        = parse_user_intent_local(clean_transcript)
        engine_used = "🎙️ Voice"

        # ── DB item lookup if item not found ──
        if data.get("action") in ("sell", "pay_mpesa", "add_stock", "check_stock") and not data.get("item"):
            try:
                conn   = pos_engine.get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT item_name FROM Inventory")
                db_items = [row[0].lower() for row in cursor.fetchall()]
                cursor.close(); conn.close()
                for word in clean_transcript.lower().split():
                    word = word.strip(".,!?")
                    for db_item in db_items:
                        if word in db_item or db_item in word:
                            data["item"] = db_item
                            break
                    if data.get("item"):
                        break
            except Exception:
                pass

        # ── Only use Ollama if local parser couldn't determine action ──
        if data.get("action") == "chat":
            try:
                ai_data = await asyncio.wait_for(ask_ollama(clean_transcript), timeout=8.0)
                if ai_data and ai_data.get("action") != "chat":
                    data        = ai_data
                    engine_used = "🧠 AI"
            except Exception:
                pass  # stay with local parser result

        action = data.get("action", "chat")
        prefix = f"[{engine_used}] "

        print(f"🎙️ Voice action: {action} | data: {data}")

        if action != "chat" and not can_do(user, action):
            return await update.message.reply_text(f"🚫 You don't have permission to: `{action}`", parse_mode="Markdown")

        # Route to the right handler
        if action == "sell":
            await action_sell(data, update, prefix)
        elif action == "pay_mpesa":
            await action_pay_mpesa(data, update, prefix)
        elif action == "check_stock":
            await action_check_stock(data, update, prefix)
        elif action == "add_stock":
            await action_add_stock(data, update, prefix)
        elif action == "report":
            await action_report(data, update, prefix)
        elif action == "trend":
            await action_trend(data, update, prefix)
        elif action == "audit":
            await action_audit(data, update, prefix)
        elif action == "restock":
            await action_restock(data, update, prefix)
        else:
            await update.message.reply_text(
                f"❓ *Sijui:* _{clean_transcript}_\n\n"
                f"Sema wazi zaidi. Mifano:\n"
                f"• _Sell 1 sugar cash_\n"
                f"• _Check my inventory_\n"
                f"• _Show today report_\n"
                f"• _Sell 2 flour via M-Pesa to 0724022458_",
                parse_mode="Markdown"
            )

    except Exception as e:
        print(f"❌ Voice handler error: {e}")
        await status_msg.delete()
        await update.message.reply_text(f"❌ Voice processing failed: {e}\nTry typing your message instead.")


def main():
    pos_engine.init_tables()   # ensure MySQL tables exist
    init_users_table()          # ensure users table exists

    # Auto-register the admin account if not already registered
    if not get_user(MY_ADMIN_ID):
        register_user(MY_ADMIN_ID, "admin", "Shop Owner", role="admin")
        print("✅ Admin account registered in database")

    # Check if Ollama is running and pre-warm the model
    try:
        requests.get("http://localhost:11434", timeout=3)
        print("✅ Ollama is running — AI Brain is ACTIVE")
        print("⏳ Pre-loading AI model into memory (first load takes ~30s)...")
        try:
            warm_resp = requests.post(
                OLLAMA_URL.replace("/api/generate", "/api/chat"),
                json={
                    "model":    OLLAMA_MODEL,
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream":   False,
                    "system":   "Reply with one word: ready"
                },
                timeout=120   # give it up to 2 minutes to load
            )
            print("🧠 AI model loaded and ready!")
        except Exception as warm_err:
            print(f"⚠️  Model pre-load failed: {warm_err}")
            print("   First message may be slow. That is normal.")
    except Exception:
        print("⚠️  Ollama is NOT running. Bot will use Regex fallback.")
        print("   Start it with: ollama serve")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("help",       help_cmd))
    app.add_handler(CommandHandler("clear",      clear_cmd))
    app.add_handler(CommandHandler("myid",       myid_cmd))
    app.add_handler(CommandHandler("register",   register_cmd))
    app.add_handler(CommandHandler("users",      users_cmd))
    app.add_handler(CommandHandler("removeuser", removeuser_cmd))
    app.add_handler(CommandHandler("invite",     invite_cmd))
    app.add_handler(CallbackQueryHandler(customer_callback, pattern=r"^cust:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice_message))

    print("🤖 Duka POS Bot is running (Ollama AI + Regex Fallback)...")
    app.run_polling()

if __name__ == "__main__":
    main()