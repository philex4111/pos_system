# Telegram Customer Self-Checkout

## What customers can do

Customers can:
- open the bot and press **Start**
- browse the **menu** (reads `Inventory`)
- add items to cart
- checkout using **M-Pesa STK only**
- receive a **PDF receipt**

Cash is not supported in self-checkout. Cash customers must purchase in the shop manually.

## What the cashier sees (Dashboard)

Open Dashboard → **Automated Checkout**

It shows:
- paid customer orders waiting for collection
- customer name/username + phone + M-Pesa code
- list of goods purchased

Cashier taps **Approve collected** after the customer picks up items.
Approved orders disappear from the pending list but remain stored in MySQL.

