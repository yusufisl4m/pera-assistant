import asyncio
import logging
import sys
import os
import re
import sqlite3
from datetime import datetime, date
from dotenv import load_dotenv
from aiohttp import web
from aiogram import Bot, Dispatcher, html, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from thefuzz import process
import dateparser

from aiogram.types import InlineKeyboardMarkup,InlineKeyboardButton, CallbackQuery

# --- AYARLAR ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

# --- LOGLAMA ---
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
# --- DİL AYARLARI ---
USER_LANGUAGES = {}  # Kullanıcı dili tutma geçici

MESSAGES = {
    "TR": {
        "welcome": "Merhaba! Ben Pera. Lütfen bir dil seçin:",
        "selected": "Harika! Türkçe devam ediyorum. 🇹🇷\n\nSana nasıl yardımcı olabilirim?",
        "menu": "Menü"
    },
    "EN": {
        "welcome": "Hello! I am Pera. Please select a language:",
        "selected": "Great! Switching to English. 🇬🇧\n\nHow can I help you?",
        "menu": "Menu"
    }
}
dp = Dispatcher()
scheduler = AsyncIOScheduler()
DB_NAME = "pera.db"


# --- VERİTABANI ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            task_name TEXT,
            task_time TEXT,
            end_date TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_task_to_db(user_id, task_name, task_time, end_date):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (user_id, task_name, task_time, end_date) VALUES (?, ?, ?, ?)",
                   (user_id, task_name, task_time, end_date))
    conn.commit()
    task_id = cursor.lastrowid
    conn.close()
    return task_id

def get_user_tasks(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, task_name, task_time, end_date FROM tasks WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def delete_task_from_db(task_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()

# --- SÖZLÜK ---
KNOWN_COMMANDS = [
    "günaydın", "kalkış", "kahvaltı", "öğle yemeği", "akşam yemeği", 
    "toplantı", "spor", "uyku", "hatırlatma", "su iç", "ilaç", "mesai bitiş"
]

class PlanForm(StatesGroup):
    waiting_for_confirmation = State()

# --- YARDIMCI FONKSİYONLAR ---

async def send_reminder(chat_id: int, text: str):
    await bot.send_message(chat_id, f"⏰ {html.bold('VAKİT GELDİ:')}\n👉 {text}")

# --- YENİ: SABAH BRİFİNGİ ---
async def send_morning_briefing(chat_id: int):
    """Her sabah 07:00'de çalışır ve günlük özeti sunar"""
    tasks = get_user_tasks(chat_id)
    if not tasks:
        return # Görev yoksa sessiz kal modu

    today = date.today()
    todays_tasks = []

    # Bugün geçerli olan görevleri filtrele
    for task in tasks:
        t_id, t_name, t_time, t_end = task
        
        is_active = True
        if t_end:
            # Bitiş tarihi varsa kontrol et: Bugün <= Bitiş Tarihi mi?
            end_dt = datetime.fromisoformat(t_end).date()
            if today > end_dt:
                is_active = False # Süresi geçmiş
        
        if is_active:
            todays_tasks.append((t_time, t_name))

    # Eğer bugün hiç aktif görev yoksa
    if not todays_tasks:
        await bot.send_message(chat_id, f"Günaydın! ☕\nBugün için planlanmış bir görevin görünmüyor. Keyfine bak! 😎")
        return

    # Listeyi saate göre sırala (Erkenden geçe doğru)
    todays_tasks.sort(key=lambda x: x[0])

    # Mesajı Oluştur
    msg = f"Günaydın! ☀️☕\nİşte bugünkü {len(todays_tasks)} görevin:\n\n"
    for t_time, t_name in todays_tasks:
        msg += f"🔹 <b>{t_time}</b> - {t_name}\n"
    
    msg += "\nHarika bir gün olsun! 🚀"
    
    await bot.send_message(chat_id, msg)

def fix_typo_and_format(text):
    best_match, score = process.extractOne(text, KNOWN_COMMANDS)
    final_text = text
    if score > 70: final_text = best_match
    return final_text.title()

def parse_duration(full_text):
    if "kadar" not in full_text.lower(): return full_text, None
    part_before_kadar = full_text.lower().split("kadar")[0].strip()
    words = part_before_kadar.split()
    candidate_date = None
    task_name_end_index = len(words)
    if len(words) >= 2:
        phrase = words[-2] + " " + words[-1]
        clean_phrase = phrase.replace("gününe", "").replace("aksamina", "").replace("sabahına", "")
        dt = dateparser.parse(clean_phrase, languages=['tr'], settings={'PREFER_DATES_FROM': 'future'})
        if dt:
            candidate_date = dt
            task_name_end_index = len(words) - 2
    if not candidate_date and len(words) >= 1:
        word = words[-1]
        clean_word = word.replace("gününe", "").replace("günü", "").replace("a", "").replace("e", "") 
        dt = dateparser.parse(clean_word, languages=['tr'], settings={'PREFER_DATES_FROM': 'future'})
        if not dt: dt = dateparser.parse(word, languages=['tr'], settings={'PREFER_DATES_FROM': 'future'})
        if dt:
            candidate_date = dt
            task_name_end_index = len(words) - 1
    if candidate_date:
        candidate_date = candidate_date.replace(hour=23, minute=59, second=59)
        task_name = " ".join(words[:task_name_end_index])
        return task_name, candidate_date
    return full_text, None

def get_confirmation_keyboard():
    buttons = [[InlineKeyboardButton(text="✅ Onayla ve Kur", callback_data="confirm_plan"), InlineKeyboardButton(text="❌ İptal Et", callback_data="cancel_plan")]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- İŞLEYİCİLER ---

@dp.message(Command("start"))
async def cmd_start(message: Message):
    # Dil seçimi için butonlar
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇹🇷 Türkçe", callback_data="lang_tr"),
            InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")
        ]
    ])
    await message.answer(
        "👋 Welcome / Merhaba!\n\nPlease select your language / Lütfen dil seçimi yapınız:", 
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data.startswith("lang_"))
async def process_language_selection(callback_query: CallbackQuery):
    lang_code = "TR" if callback_query.data == "lang_tr" else "EN"
    user_id = callback_query.from_user.id
    
    # Seçimi kaydet
    USER_LANGUAGES[user_id] = lang_code
    
    # Onay mesajı gönder
    response_text = MESSAGES[lang_code]["selected"]
    await callback_query.message.answer(response_text)
    await callback_query.answer()  # Yükleniyor ikonunu kaldır

@dp.message(Command("plans"))
async def list_plans(message: Message):
    if str(message.from_user.id) != str(ADMIN_ID): return
    tasks = get_user_tasks(message.from_user.id)
    if not tasks:
        await message.answer("📭 Planın yok.")
        return
    await message.answer(f"📂 <b>Kayıtlı Planlar ({len(tasks)}):</b>")
    for task in tasks:
        t_id, t_name, t_time, t_end = task
        note = ""
        if t_end:
            d_obj = datetime.fromisoformat(t_end)
            note = f" (Son: {d_obj.strftime('%d.%m.%Y')})"
        text = f"⏰ {t_time} - {t_name}{note}"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🗑️ Sil", callback_data=f"del_{t_id}")]])
        await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("del_"))
async def delete_task_handler(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    delete_task_from_db(task_id)
    try: scheduler.remove_job(str(task_id))
    except: pass
    await callback.message.delete()
    await callback.answer("Silindi.")

@dp.message(F.text)
async def analyze_plan(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_ID): return
    user_text = message.text
    temp_jobs = []
    for line in user_text.split("\n"):
        match = re.search(r"(\d{1,2}[:.]\d{2})\s+(.*)", line)
        if match:
            time_part = match.group(1).replace(".", ":")
            raw_content = match.group(2).strip()
            task_name, end_date = parse_duration(raw_content)
            final_task = fix_typo_and_format(task_name)
            temp_jobs.append({"time": time_part, "task": final_task, "end_date": end_date.isoformat() if end_date else None})
    if not temp_jobs:
        await message.answer("⚠️ Saat bulunamadı.")
        return
    preview_text = "📋 <b>Plan Analizi:</b>\n\n"
    for job in temp_jobs:
        if job['end_date']:
            d_obj = datetime.fromisoformat(job['end_date'])
            date_note = f"(Bitiş: {d_obj.strftime('%d %B %Y')})"
        else: date_note = "(Süresiz)"
        preview_text += f"🔹 <b>{job['time']}</b> - {job['task']} {html.italic(date_note)}\n"
    preview_text += "\nOnaylıyor musunuz?"
    await state.update_data(jobs=temp_jobs)
    await state.set_state(PlanForm.waiting_for_confirmation)
    await message.answer(preview_text, reply_markup=get_confirmation_keyboard())

@dp.callback_query(F.data == "confirm_plan", PlanForm.waiting_for_confirmation)
async def process_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    jobs = data.get("jobs", [])
    count = 0
    for job in jobs:
        hour, minute = map(int, job['time'].split(":"))
        end_date = job['end_date']
        task_id = add_task_to_db(callback.message.chat.id, job['task'], job['time'], end_date)
        end_dt = datetime.fromisoformat(end_date) if end_date else None
        scheduler.add_job(send_reminder, "cron", hour=hour, minute=minute, end_date=end_dt, args=[callback.message.chat.id, job['task']], id=str(task_id))
        count += 1
    await callback.message.edit_text(f"✅ {count} Görev Hafızaya Alındı!")
    await state.clear()

@dp.callback_query(F.data == "cancel_plan", PlanForm.waiting_for_confirmation)
async def process_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("❌ İptal edildi.")
    await state.clear()

async def load_tasks_on_startup():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, user_id, task_name, task_time, end_date FROM tasks")
        rows = cursor.fetchall()
        for row in rows:
            t_id, u_id, t_name, t_time, t_end = row
            hour, minute = map(int, t_time.split(":"))
            end_dt = datetime.fromisoformat(t_end) if t_end else None
            try: scheduler.add_job(send_reminder, "cron", hour=hour, minute=minute, end_date=end_dt, args=[u_id, t_name], id=str(t_id), replace_existing=True)
            except Exception as e: pass
        print(f"{len(rows)} eski görev yüklendi.")
    except: pass
    conn.close()

async def health_check(request):
    return web.Response(text="Pera is alive and kicking! 🚀")

async def start_server():
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

async def main():
    init_db()
    await load_tasks_on_startup()
    
    if ADMIN_ID:
        scheduler.add_job(send_morning_briefing, 'cron', hour=7, minute=0, args=[int(ADMIN_ID)], id='morning_briefing', replace_existing=True)
    
    scheduler.start()
    print("Pera (V8) - Sabah Brifingi ve Full Asistan Modu Aktif...")

    # Devamlı Aktif Mod (bot ve sunucuuyu aynı anda a)
    await asyncio.gather(
        dp.start_polling(bot),
        start_server()
    )

if __name__ == "__main__":
    try: asyncio.run(main())
    except (KeyboardInterrupt, SystemExit): pass
    
    # Gelişim ve adaptasyon devam...
    # yusufisl4m