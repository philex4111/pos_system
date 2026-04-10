# 🛒 My Duka POS - Intelligent Telegram POS System

An intelligent, hybrid-AI Point of Sale (POS) system built for Kenyan businesses. It operates entirely through Telegram, processing sales, handling M-Pesa payments, and generating PDF receipts, all while powered by a local AI model capable of understanding natural language in both English and Swahili.

## ✨ Key Features

* **🤖 Hybrid AI Brain:** Powered locally by Ollama (Llama 3.2). Understands conversational commands like *"Niuzie sukari moja kwa cash"* or *"What is my best selling item this week?"*
* **⚡ Instant Fallback Engine:** Custom regex parser ensures the POS remains lightning-fast and operational even if the AI model is offline or loading.
* **💸 M-Pesa Integration:** Full Safaricom Daraja API integration. Generates STK Pushes directly to customer phones and auto-updates the database via Ngrok webhooks.
* **🧾 Automated PDF Receipts:** Generates professional A6-sized PDF receipts upon successful payment (Cash or M-Pesa) and sends them directly to the Telegram chat.
* **📊 Live Web Dashboard:** A Flask-powered web interface displaying real-time sales charts, revenue metrics, and low-stock alerts.
* **🔍 Web Search Context:** Integrates DuckDuckGo to look up unfamiliar local terms or general knowledge questions before answering.

## 🛠️ Tech Stack

* **Backend:** Python 3.x
* **Bot Framework:** `python-telegram-bot`
* **AI/LLM:** Ollama (Llama 3.2), `requests`
* **Database:** MySQL
* **Web/Webhooks:** Flask, Ngrok
* **PDF Generation:** ReportLab

## 🚀 Installation & Setup

### 1. Prerequisites
* Python 3.8+
* [MySQL Server](https://dev.mysql.com/downloads/installer/) installed and running locally.
* [Ollama](https://ollama.com/) installed with the `llama3.2` model downloaded (`ollama pull llama3.2`).
* [Ngrok](https://ngrok.com/) for exposing the M-Pesa webhook locally.

### 2. Clone the Repository
```bash
git clone [https://github.com/philex4111/pos_system.git](https://github.com/philex4111/pos_system.git)
cd pos_system