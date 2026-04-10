# Launching the full POS project (Windows)

## One-click start

- Double-click `launch_all.bat`
- For silent background start (no CMD windows): double-click `launch_all.vbs`

This starts (in separate windows):
- `dashboard.py` on `http://127.0.0.1:8080`
- `mpesa_callback.py` on `http://127.0.0.1:5000`
- `ngrok.exe http 5000` (web UI at `http://127.0.0.1:4040`)
- `telegram_bot.py`

## One-click stop

- Double-click `stop_all.bat`
- For silent stop (no CMD window): double-click `stop_all.vbs`

## Notes

- The launcher expects:
  - Python venv at `pos_system\venv\Scripts\python.exe`
  - ngrok at `pos_system\ngrok.exe`

## Telegram bot: customer vs admin

- Customers (public users) can only:
  - browse menu (Inventory)
  - add items to cart
  - pay using M-Pesa STK (no cash)
  - receive a PDF receipt
- Admin/attendants keep the previous POS bot features (reports, stock, etc.)

## Make the bot publicly discoverable

In Telegram, open **BotFather** and:
- set a username for the bot (so people can search it)
- set profile photo, description (optional)

Users must press **Start** to begin chat with the bot.

