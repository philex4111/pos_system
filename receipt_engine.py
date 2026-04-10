import os
from datetime import datetime
from reportlab.lib.pagesizes import A6
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas

# ─────────────────────────────────────────
# SHOP CONFIG — edit these to match yours
# ─────────────────────────────────────────
SHOP_NAME    = "My Duka"
SHOP_PHONE   = "+254 724 022 458"
SHOP_LOCATION = "Nairobi, Kenya"
RECEIPTS_DIR = "receipts"   # folder where PDFs are saved

# ─────────────────────────────────────────
# RECEIPT GENERATOR
# ─────────────────────────────────────────
def generate_receipt(
    item: str,
    quantity: int,
    unit_price: float,
    total: float,
    payment_method: str,
    mpesa_code: str = None,
    customer_phone: str = None,
    receipt_number: str = None
) -> str:
    """
    Generate a professional PDF receipt.

    Returns:
        Path to the saved PDF file.
    """
    # Make sure receipts folder exists
    os.makedirs(RECEIPTS_DIR, exist_ok=True)

    # Build a unique filename
    timestamp      = datetime.now().strftime("%Y%m%d_%H%M%S")
    receipt_number = receipt_number or f"RCP-{timestamp}"
    filename       = os.path.join(RECEIPTS_DIR, f"receipt_{receipt_number}.pdf")

    # ── Page setup (A6 = receipt size 105mm x 148mm) ──
    page_w, page_h = A6
    c = canvas.Canvas(filename, pagesize=A6)

    # ── Colour palette ──
    dark_green  = colors.HexColor("#1B5E20")
    light_green = colors.HexColor("#E8F5E9")
    mid_green   = colors.HexColor("#2E7D32")
    grey        = colors.HexColor("#757575")
    black       = colors.HexColor("#212121")

    y = page_h - 10 * mm   # start near top

    # ── Header background ──
    c.setFillColor(dark_green)
    c.rect(0, page_h - 30 * mm, page_w, 30 * mm, fill=1, stroke=0)

    # ── Shop name ──
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(page_w / 2, page_h - 14 * mm, SHOP_NAME)

    # ── Shop sub-info ──
    c.setFont("Helvetica", 8)
    c.drawCentredString(page_w / 2, page_h - 20 * mm, SHOP_PHONE)
    c.drawCentredString(page_w / 2, page_h - 25 * mm, SHOP_LOCATION)

    y = page_h - 35 * mm

    # ── Receipt title & number ──
    c.setFillColor(mid_green)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(page_w / 2, y, "OFFICIAL RECEIPT")
    y -= 6 * mm

    c.setFillColor(grey)
    c.setFont("Helvetica", 8)
    c.drawCentredString(page_w / 2, y, f"No: {receipt_number}")
    y -= 4 * mm

    c.setFillColor(grey)
    c.drawCentredString(
        page_w / 2, y,
        datetime.now().strftime("%d %B %Y   %H:%M")
    )
    y -= 6 * mm

    # ── Divider ──
    c.setStrokeColor(mid_green)
    c.setLineWidth(1)
    c.line(10 * mm, y, page_w - 10 * mm, y)
    y -= 6 * mm

    # ── Items table header ──
    c.setFillColor(light_green)
    c.rect(8 * mm, y - 5 * mm, page_w - 16 * mm, 7 * mm, fill=1, stroke=0)

    c.setFillColor(dark_green)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(10 * mm,        y - 2 * mm, "ITEM")
    c.drawString(55 * mm,        y - 2 * mm, "QTY")
    c.drawString(72 * mm,        y - 2 * mm, "UNIT")
    c.drawString(88 * mm,        y - 2 * mm, "TOTAL")
    y -= 9 * mm

    # ── Item row ──
    c.setFillColor(black)
    c.setFont("Helvetica", 9)
    c.drawString(10 * mm, y, item.title())
    c.drawString(57 * mm, y, str(quantity))
    c.drawString(70 * mm, y, f"{unit_price:.0f}")
    c.drawString(87 * mm, y, f"KES {total:.0f}")
    y -= 5 * mm

    # ── Thin line under items ──
    c.setStrokeColor(colors.HexColor("#BDBDBD"))
    c.setLineWidth(0.5)
    c.line(10 * mm, y, page_w - 10 * mm, y)
    y -= 6 * mm

    # ── Total box ──
    c.setFillColor(dark_green)
    c.rect(8 * mm, y - 6 * mm, page_w - 16 * mm, 9 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(10 * mm,  y - 1 * mm, "TOTAL PAID")
    c.drawRightString(page_w - 10 * mm, y - 1 * mm, f"KES {total:.0f}")
    y -= 12 * mm

    # ── Payment details ──
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(10 * mm, y, "Payment Method:")
    c.setFont("Helvetica", 8)
    c.drawString(48 * mm, y, payment_method.upper())
    y -= 5 * mm

    if mpesa_code:
        c.setFont("Helvetica-Bold", 8)
        c.drawString(10 * mm, y, "M-Pesa Code:")
        c.setFont("Helvetica", 8)
        c.setFillColor(mid_green)
        c.drawString(40 * mm, y, mpesa_code)
        c.setFillColor(black)
        y -= 5 * mm

    if customer_phone:
        c.setFont("Helvetica-Bold", 8)
        c.drawString(10 * mm, y, "Customer Phone:")
        c.setFont("Helvetica", 8)
        c.drawString(48 * mm, y, customer_phone)
        y -= 5 * mm

    y -= 4 * mm

    # ── Divider ──
    c.setStrokeColor(mid_green)
    c.setLineWidth(0.8)
    c.line(10 * mm, y, page_w - 10 * mm, y)
    y -= 7 * mm

    # ── Thank you message ──
    c.setFillColor(mid_green)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(page_w / 2, y, "Thank you for shopping at")
    y -= 5 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(page_w / 2, y, SHOP_NAME + "!")
    y -= 5 * mm
    c.setFillColor(grey)
    c.setFont("Helvetica", 7)
    c.drawCentredString(page_w / 2, y, "Please keep this receipt for your records.")

    # ── Bottom border ──
    c.setFillColor(dark_green)
    c.rect(0, 0, page_w, 4 * mm, fill=1, stroke=0)

    c.save()
    print(f"Receipt saved: {filename}")
    return filename


def generate_receipt_cart(
    items: list[dict],
    total: float,
    payment_method: str,
    mpesa_code: str = None,
    customer_phone: str = None,
    receipt_number: str = None
) -> str:
    """
    Generate a PDF receipt for a multi-item cart.

    items: list of {name, qty, unit_price, line_total}
    """
    os.makedirs(RECEIPTS_DIR, exist_ok=True)
    timestamp      = datetime.now().strftime("%Y%m%d_%H%M%S")
    receipt_number = receipt_number or f"RCP-{timestamp}"
    filename       = os.path.join(RECEIPTS_DIR, f"receipt_{receipt_number}.pdf")

    page_w, page_h = A6
    c = canvas.Canvas(filename, pagesize=A6)

    dark_green  = colors.HexColor("#1B5E20")
    light_green = colors.HexColor("#E8F5E9")
    mid_green   = colors.HexColor("#2E7D32")
    grey        = colors.HexColor("#757575")
    black       = colors.HexColor("#212121")

    y = page_h - 10 * mm

    c.setFillColor(dark_green)
    c.rect(0, page_h - 30 * mm, page_w, 30 * mm, fill=1, stroke=0)

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(page_w / 2, page_h - 14 * mm, SHOP_NAME)

    c.setFont("Helvetica", 8)
    c.drawCentredString(page_w / 2, page_h - 20 * mm, SHOP_PHONE)
    c.drawCentredString(page_w / 2, page_h - 25 * mm, SHOP_LOCATION)

    y = page_h - 35 * mm

    c.setFillColor(mid_green)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(page_w / 2, y, "OFFICIAL RECEIPT")
    y -= 6 * mm

    c.setFillColor(grey)
    c.setFont("Helvetica", 8)
    c.drawCentredString(page_w / 2, y, f"No: {receipt_number}")
    y -= 4 * mm
    c.drawCentredString(page_w / 2, y, datetime.now().strftime("%d %B %Y   %H:%M"))
    y -= 6 * mm

    c.setStrokeColor(mid_green)
    c.setLineWidth(1)
    c.line(10 * mm, y, page_w - 10 * mm, y)
    y -= 6 * mm

    c.setFillColor(light_green)
    c.rect(8 * mm, y - 5 * mm, page_w - 16 * mm, 7 * mm, fill=1, stroke=0)

    c.setFillColor(dark_green)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(10 * mm, y - 2 * mm, "ITEM")
    c.drawString(60 * mm, y - 2 * mm, "QTY")
    c.drawRightString(page_w - 10 * mm, y - 2 * mm, "TOTAL")
    y -= 9 * mm

    c.setFillColor(black)
    c.setFont("Helvetica", 8)
    max_rows = 7
    for i, it in enumerate(items[:max_rows]):
        name = str(it.get("name") or it.get("item") or "Item")
        qty  = int(it.get("qty") or 1)
        lt   = float(it.get("line_total") or it.get("total") or 0)
        if len(name) > 18:
            name = name[:18] + "…"
        c.drawString(10 * mm, y, name)
        c.drawString(62 * mm, y, str(qty))
        c.drawRightString(page_w - 10 * mm, y, f"KES {lt:.0f}")
        y -= 4.5 * mm

    if len(items) > max_rows:
        c.setFillColor(grey)
        c.drawString(10 * mm, y, f"... and {len(items) - max_rows} more")
        c.setFillColor(black)
        y -= 4.5 * mm

    c.setStrokeColor(colors.HexColor("#BDBDBD"))
    c.setLineWidth(0.5)
    c.line(10 * mm, y, page_w - 10 * mm, y)
    y -= 6 * mm

    c.setFillColor(dark_green)
    c.rect(8 * mm, y - 6 * mm, page_w - 16 * mm, 9 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(10 * mm,  y - 1 * mm, "TOTAL PAID")
    c.drawRightString(page_w - 10 * mm, y - 1 * mm, f"KES {total:.0f}")
    y -= 12 * mm

    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(10 * mm, y, "Payment Method:")
    c.setFont("Helvetica", 8)
    c.drawString(48 * mm, y, payment_method.upper())
    y -= 5 * mm

    if mpesa_code:
        c.setFont("Helvetica-Bold", 8)
        c.drawString(10 * mm, y, "M-Pesa Code:")
        c.setFont("Helvetica", 8)
        c.setFillColor(mid_green)
        c.drawString(40 * mm, y, str(mpesa_code))
        c.setFillColor(black)
        y -= 5 * mm

    if customer_phone:
        c.setFont("Helvetica-Bold", 8)
        c.drawString(10 * mm, y, "Customer Phone:")
        c.setFont("Helvetica", 8)
        c.drawString(48 * mm, y, str(customer_phone))
        y -= 5 * mm

    y -= 4 * mm
    c.setStrokeColor(mid_green)
    c.setLineWidth(0.8)
    c.line(10 * mm, y, page_w - 10 * mm, y)
    y -= 7 * mm

    c.setFillColor(mid_green)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(page_w / 2, y, "Thank you for shopping at")
    y -= 5 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(page_w / 2, y, SHOP_NAME + "!")
    y -= 5 * mm
    c.setFillColor(grey)
    c.setFont("Helvetica", 7)
    c.drawCentredString(page_w / 2, y, "Please keep this receipt for your records.")

    c.setFillColor(dark_green)
    c.rect(0, 0, page_w, 4 * mm, fill=1, stroke=0)

    c.save()
    print(f"Receipt saved: {filename}")
    return filename


# ─────────────────────────────────────────
# QUICK TEST — run this file directly to
# generate a sample receipt
# ─────────────────────────────────────────
if __name__ == "__main__":
    path = generate_receipt(
        item           = "Kensalt 500g",
        quantity       = 2,
        unit_price     = 35,
        total          = 70,
        payment_method = "M-Pesa",
        mpesa_code     = "QJK4X9PLMN",
        customer_phone = "+254724022458",
        receipt_number = "RCP-20260312-001"
    )
    print(f"Test receipt generated at: {path}")