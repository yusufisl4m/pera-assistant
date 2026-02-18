# Pera Assistant (V8) 🤖

Pera is a smart, cloud-based personal assistant bot running 24/7 on Render. It helps organize daily tasks, manage GitHub contributions, and provides automated morning briefings.

## 🚀 Key Features
- Multi-Language Support: Offers both Turkish 🇹🇷 and English 🇬🇧 interface options.
- Turkish Language Correction: Advanced grammar and translation capabilities (Preserved Core Feature).
- 24/7 Availability: Hosted on Render with a custom Keep-Alive mechanism.
- Morning Briefing: Automated daily summaries at 07:00 AM (Europe/Istanbul).

## 🛠 Installation
1. Clone the repository.
2. Install dependencies: pip install -r requirements.txt
3. Configure environment variables (`BOT_TOKEN`, ADMIN_ID, `TZ`).
4. Run the bot: python main.py

## 📦 Tech Stack
- Language: Python 3.10+
- Core Libs: Aiogram, Aiohttp, SQLite
- Cloud: Render Web Service