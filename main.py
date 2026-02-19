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
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from thefuzz import process
import dateparser

# --- AYARLAR ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

if not TOKEN:
    raise ValueError("KRİTİK HATA: BOT_TOKEN bulunamadı.")

# --- LOGLAMA ---
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
scheduler = AsyncIOScheduler()
DB_NAME = "pera.db"

# --- BELLEK YÖNETİMİ ---
USER_STATES = {}

# --- DİL SÖZLÜĞÜ (Cryptology Mantığı) ---
TEXTS = {
    "TR": {
        "welcome_title": "🤖 **PERA ASİSTAN AKTİF** 🤖",
        "select_lang": "Lütfen dil seçiniz / Please select language:",
        "menu_msg": "Hoş geldin patron! Günlük rutinini organize etmek ve projelerini takip etmek için hazırım.\n\n👇 Aşağıdaki sabit menüden işlemlerini yönetebilirsin.",
        "btn_tasks": "📋 Görevlerim",
        "btn_github": "🐙 GitHub Durumu",
        "btn_briefing": "☕ Sabah Brifingi",
        "btn_settings": "⚙️ Ayarlar",
        "settings_title": "⚙️ **AYARLAR MENÜSÜ**\nLütfen düzenlemek istediğiniz alanı seçin:",
        "set_tasks": "📋 Görev Yönetimi",
        "set_lang": "🌐 Dil / Language",
        "set_info": "ℹ️ Bilgi",
        "back": "🔙 Geri",
        "add_task": "➕ Görev Ekle",
        "del_task": "➖ Görev Sil",
        "enter_task": "✍️ Lütfen planınızı yazın:\n*(Örn: 08:00 Kahvaltı veya 15:30 Toplantı yarına kadar)*",
        "no_tasks": "📭 Planlanmış görevin yok. Keyfine bak! 😎",
        "tasks_title": "📂 <b>Kayıtlı Planların:</b>\n──────────────",
        "github_placeholder": "🔍 **GitHub Durumu:**\nAPI bağlantısı bekleniyor... (Yakında eklenecek)",
        "info_msg": (
            "ℹ️ **PERA ASİSTAN KULLANIM KILAVUZU**\n\n"
            "🤖 **Ben Kimim?**\nSizin için günlük işleri organize eden, sabahları brifing veren akıllı kişisel asistanınızım.\n\n"
            "🎛 **Özellikler:**\n"
            "• **Görevlerim:** Günlük planlarınızı listeler.\n"
            "• **Sabah Brifingi:** Her sabah 07:00'de günün özetini sunar.\n"
            "• **Ayarlar:** Yeni görev ekleyebilir veya silebilirsiniz.\n\n"
            "💡 *Görev eklerken doğal dille yazabilirsiniz (Örn: 20:00 Spor yap).* "
        )
    },
    "EN": {
        "welcome_title": "🤖 **PERA ASSISTANT ACTIVE** 🤖",
        "select_lang": "Please select language:",
        "menu_msg": "Welcome boss! I am ready to organize your daily routine and track your projects.\n\n👇 Use the pinned menu below to manage your tasks.",
        "btn_tasks": "📋 My Tasks",
        "btn_github": "🐙 GitHub Status",
        "btn_briefing": "☕ Morning Briefing",
        "btn_settings": "⚙️ Settings",
        "settings_title": "⚙️ **SETTINGS MENU**\nPlease select an area to manage:",
        "set_tasks": "📋 Task Management",
        "set_lang": "🌐 Language",
        "set_info": "ℹ️ Info",
        "back": "🔙 Back",
        "add_task": "➕ Add Task",
        "del_task": "➖ Delete Task",
        "enter_task": "✍️ Please enter your plan:\n*(e.g., 08:00 Breakfast or 15:30 Meeting until tomorrow)*",
        "no_tasks": "📭 You have no scheduled tasks. Enjoy your day! 😎",
        "tasks_title": "📂 <b>Your Scheduled Tasks:</b>\n──────────────",
        "github_placeholder": "🔍 **GitHub Status:**\nAwaiting API connection... (Coming soon)",
        "info_msg": (
            "ℹ️ **PERA ASSISTANT USER GUIDE**\n\n"
            "🤖 **Who am I?**\nI am your smart personal assistant that organizes your daily tasks and provides morning briefings.\n\n"
            "🎛 **Features:**\n"
            "• **My Tasks:** Lists your daily plans.\n"
            "• **Morning Briefing:** Summarizes your day every morning at 07:00.\n"
            "• **Settings:** You can add or remove tasks here.\n\n"
            "💡 *You can use natural language to add tasks (e.g., 20:00 Workout).* "
        )
    }
}

# --- VERİTABANI ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            task_name TEXT, task_time TEXT, end_date TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER PRIMARY KEY, language TEXT DEFAULT 'TR'
        )
    """)
    conn.commit()
    conn.close()

def set_language(user_id, lang):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("INSERT OR REPLACE INTO settings (user_id, language) VALUES (?, ?)", (user_id, lang))

def get_language(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.execute("SELECT language FROM settings WHERE user_id = ?", (user_id,)).fetchone()
    return res[0] if res else "TR"

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

# --- SÖZLÜK VE NLP YARDIMCILARI ---
KNOWN_COMMANDS = ["günaydın", "kalkış", "kahvaltı", "öğle yemeği", "akşam yemeği", "toplantı", "spor", "uyku", "hatırlatma", "su iç", "ilaç", "mesai bitiş"]

class PlanForm(StatesGroup):
    waiting_for_confirmation = State()

def fix_typo_and_format(text):
    best_match, score = process.extractOne(text, KNOWN_COMMANDS)
    return best_match.title() if score > 70 else text.title()

def parse_duration(full_text):
    if "kadar" not in full_text.lower(): return full_text, None
    part_before_kadar = full_text.lower().split("kadar")[0].strip()
    words = part_before_kadar.split()
    candidate_date = None
    task_name_end_index = len(words)
    
    if len(words) >= 2:
        clean_phrase = (words[-2] + " " + words[-1]).replace("gününe", "").replace("aksamina", "").replace("sabahına", "")
        dt = dateparser.parse(clean_phrase, languages=['tr'], settings={'PREFER_DATES_FROM': 'future'})
        if dt: candidate_date, task_name_end_index = dt, len(words) - 2

    if not candidate_date and len(words) >= 1:
        clean_word = words[-1].replace("gününe", "").replace("günü", "").replace("a", "").replace("e", "")
        dt = dateparser.parse(clean_word, languages=['tr'], settings={'PREFER_DATES_FROM': 'future'})
        if not dt: dt = dateparser.parse(words[-1], languages=['tr'], settings={'PREFER_DATES_FROM': 'future'})
        if dt: candidate_date, task_name_end_index = dt, len(words) - 1

    if candidate_date:
        return " ".join(words[:task_name_end_index]), candidate_date.replace(hour=23, minute=59, second=59)
    return full_text, None

# --- ZAMANLANMIŞ GÖREVLER (SCHEDULER) ---
async def send_reminder(chat_id: int, text: str):
    await bot.send_message(chat_id, f"⏰ <b>VAKİT GELDİ:</b>\n👉 {text}")

async def send_morning_briefing(chat_id: int):
    tasks = get_user_tasks(chat_id)
    if not tasks: return 
    
    today, todays_tasks = date.today(), []
    for t_id, t_name, t_time, t_end in tasks:
        if not t_end or today <= datetime.fromisoformat(t_end).date():
            todays_tasks.append((t_time, t_name))
            
    if not todays_tasks:
        await bot.send_message(chat_id, "Günaydın! ☕\nBugün için planlanmış bir görevin görünmüyor. Keyfine bak! 😎")
        return
        
    todays_tasks.sort(key=lambda x: x[0])
    msg = f"☀️ <b>GÜNAYDIN!</b>\nİşte bugünkü {len(todays_tasks)} görevin:\n──────────────\n"
    for t_time, t_name in todays_tasks:
        msg += f"🔹 <b>{t_time}</b> - {t_name}\n"
    msg += "──────────────\nHarika bir gün olsun! 🚀"
    
    await bot.send_message(chat_id, msg)

# --- ARAYÜZ (UI) KLAVYELERİ ---
def get_t(user_id, key):
    lang = get_language(user_id)
    return TEXTS[lang].get(key, key)

def get_pera_menu(user_id):
    t = lambda k: get_t(user_id, k)
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=t("btn_tasks")), KeyboardButton(text=t("btn_github"))],
        [KeyboardButton(text=t("btn_briefing")), KeyboardButton(text=t("btn_settings"))]
    ], resize_keyboard=True, persistent=True)

def settings_kb(user_id):
    t = lambda k: get_t(user_id, k)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("set_tasks"), callback_data="conf_tasks")],
        [InlineKeyboardButton(text=t("set_lang"), callback_data="conf_lang"),
         InlineKeyboardButton(text=t("set_info"), callback_data="conf_info")]
    ])

# --- BAŞLANGIÇ ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(TEXTS["TR"]["welcome_title"])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇹🇷 Türkçe", callback_data="lang_TR"),
         InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_EN")]
    ])
    await message.answer(TEXTS["TR"]["select_lang"], reply_markup=kb)

@dp.callback_query(F.data.startswith("lang_"))
async def process_language_selection(call: CallbackQuery):
    lang_code = call.data.split("_")[1]
    set_language(call.from_user.id, lang_code)
    await call.message.delete()
    await call.message.answer(get_t(call.from_user.id, "menu_msg"), reply_markup=get_pera_menu(call.from_user.id))

# --- ANA MENÜ (TEXT YAKALAYICI) ---
@dp.message(F.text)
async def main_menu_handler(message: Message, state: FSMContext):
    uid = message.from_user.id
    txt = message.text.strip()
    t = lambda k: get_t(uid, k)
    user_state = USER_STATES.get(uid)

    # 1. DURUM YÖNETİMİ (GÖREV EKLEME)
    if user_state == "wait_task_add":
        if txt.startswith("/"): 
            USER_STATES[uid] = None
            return
            
        temp_jobs = []
        for line in txt.split("\n"):
            match = re.search(r"(\d{1,2}[:.]\d{2})\s+(.*)", line)
            if match:
                time_part = match.group(1).replace(".", ":")
                raw_content = match.group(2).strip()
                task_name, end_date = parse_duration(raw_content)
                final_task = fix_typo_and_format(task_name)
                temp_jobs.append({"time": time_part, "task": final_task, "end_date": end_date.isoformat() if end_date else None})
        
        if not temp_jobs:
            await message.answer("⚠️ Saat formatı bulunamadı. Lütfen '08:00 Görev' şeklinde yazın.")
            return
            
        preview_text = "📋 <b>Plan Analizi:</b>\n──────────────\n"
        for job in temp_jobs:
            date_note = f" (Bitiş: {datetime.fromisoformat(job['end_date']).strftime('%d.%m.%Y')})" if job['end_date'] else ""
            preview_text += f"🔹 <b>{job['time']}</b> - {job['task']}{date_note}\n"
            
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Onayla", callback_data="confirm_plan"), 
             InlineKeyboardButton(text="❌ İptal", callback_data="cancel_plan")]
        ])
        
        await state.update_data(jobs=temp_jobs)
        await state.set_state(PlanForm.waiting_for_confirmation)
        await message.answer(preview_text, reply_markup=kb)
        USER_STATES[uid] = None
        return

    # 2. SABİT MENÜ BUTONLARI
    if txt in [TEXTS["TR"]["btn_settings"], TEXTS["EN"]["btn_settings"]]:
        await message.answer(t("settings_title"), reply_markup=settings_kb(uid))

    elif txt in [TEXTS["TR"]["btn_tasks"], TEXTS["EN"]["btn_tasks"]]:
        tasks = get_user_tasks(uid)
        if not tasks:
            await message.answer(t("no_tasks"))
            return
        
        msg_text = t("tasks_title") + "\n"
        for task in tasks:
            t_id, t_name, t_time, t_end = task
            note = f" (Son: {datetime.fromisoformat(t_end).strftime('%d.%m.%Y')})" if t_end else ""
            msg_text += f"⏰ <b>{t_time}</b> - {t_name}{note}\n"
        await message.answer(msg_text)

    elif txt in [TEXTS["TR"]["btn_briefing"], TEXTS["EN"]["btn_briefing"]]:
        await send_morning_briefing(uid)

    elif txt in [TEXTS["TR"]["btn_github"], TEXTS["EN"]["btn_github"]]:
        await message.answer(t("github_placeholder"))

# --- AYARLAR & INLINE İŞLEMLER ---
@dp.callback_query(F.data.startswith("conf_"))
async def conf_handler(call: CallbackQuery):
    uid = call.from_user.id
    mode = call.data.split("_")[1]
    t = lambda k: get_t(uid, k)

    if mode == "info":
        await call.message.edit_text(t("info_msg"), reply_markup=settings_kb(uid))
        
    elif mode == "lang":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🇹🇷 Türkçe", callback_data="lang_TR"), 
             InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_EN")],
            [InlineKeyboardButton(text=t("back"), callback_data="back_settings")]
        ])
        await call.message.edit_text(t("select_lang"), reply_markup=kb)
        
    elif mode == "tasks":
        # Görev Yönetimi Menüsü
        tasks = get_user_tasks(uid)
        kb_buttons = [
            [InlineKeyboardButton(text=t("add_task"), callback_data="action_add_task")]
        ]
        # Varsa silme butonlarını ekle
        for task in tasks:
            t_id, t_name, t_time, _ = task
            kb_buttons.append([InlineKeyboardButton(text=f"🗑️ Sil: {t_time} {t_name}", callback_data=f"del_{t_id}")])
            
        kb_buttons.append([InlineKeyboardButton(text=t("back"), callback_data="back_settings")])
        await call.message.edit_text("📋 **Görev Yönetimi**\nYeni görev ekleyebilir veya mevcutları silebilirsiniz:", 
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons))

@dp.callback_query(F.data == "back_settings")
async def back_to_settings(call: CallbackQuery):
    USER_STATES[call.from_user.id] = None
    await call.message.edit_text(get_t(call.from_user.id, "settings_title"), reply_markup=settings_kb(call.from_user.id))

@dp.callback_query(F.data == "action_add_task")
async def trigger_add_task(call: CallbackQuery):
    USER_STATES[call.from_user.id] = "wait_task_add"
    await call.message.answer(get_t(call.from_user.id, "enter_task"))
    await call.answer()

@dp.callback_query(F.data.startswith("del_"))
async def delete_task_handler(call: CallbackQuery):
    task_id = int(call.data.split("_")[1])
    delete_task_from_db(task_id)
    try: scheduler.remove_job(str(task_id))
    except: pass
    await call.answer("✅ Görev silindi!")
    # Listeyi güncelle
    await conf_handler(call) # conf_tasks mantığıyla aynı yeri tetikleriz ama call.data'yı değiştirmek riskli, direkt edit_text yapalım
    await call.message.delete()

# --- GÖREV ONAY İŞLEMLERİ (FSM) ---
@dp.callback_query(F.data == "confirm_plan", PlanForm.waiting_for_confirmation)
async def process_confirm(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    jobs = data.get("jobs", [])
    count = 0
    for job in jobs:
        hour, minute = map(int, job['time'].split(":"))
        end_date = job['end_date']
        task_id = add_task_to_db(call.message.chat.id, job['task'], job['time'], end_date)
        end_dt = datetime.fromisoformat(end_date) if end_date else None
        scheduler.add_job(send_reminder, "cron", hour=hour, minute=minute, end_date=end_dt, args=[call.message.chat.id, job['task']], id=str(task_id))
        count += 1
    await call.message.edit_text(f"✅ {count} Görev Hafızaya Alındı ve Zamanlandı!")
    await state.clear()

@dp.callback_query(F.data == "cancel_plan", PlanForm.waiting_for_confirmation)
async def process_cancel(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("❌ İşlem iptal edildi.")
    await state.clear()

# --- BAŞLANGIÇ YÜKLEMELERİ ---
async def load_tasks_on_startup():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, user_id, task_name, task_time, end_date FROM tasks")
        for t_id, u_id, t_name, t_time, t_end in cursor.fetchall():
            hour, minute = map(int, t_time.split(":"))
            end_dt = datetime.fromisoformat(t_end) if t_end else None
            try: scheduler.add_job(send_reminder, "cron", hour=hour, minute=minute, end_date=end_dt, args=[u_id, t_name], id=str(t_id), replace_existing=True)
            except: pass
    except: pass
    finally: conn.close()

# --- RENDER WEB SUNUCUSU ---
async def health_check(request):
    return web.Response(text="Pera Assistant is running smoothly! 🚀")

async def main():
    init_db()
    await load_tasks_on_startup()
    
    if ADMIN_ID:
        scheduler.add_job(send_morning_briefing, 'cron', hour=7, minute=0, args=[int(ADMIN_ID)], id='morning_briefing', replace_existing=True)
    
    scheduler.start()
    logging.info("🚀 PERA (V9 - Cryptology UI) Started")
    
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try: asyncio.run(main())
    except (KeyboardInterrupt, SystemExit): pass
