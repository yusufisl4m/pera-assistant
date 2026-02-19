import asyncio
import logging
import sys
import os
import re
import sqlite3
import aiohttp
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

# --- KONFİGÜRASYON ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("KRİTİK HATA: BOT_TOKEN çevresel değişkeni bulunamadı.")

# --- LOGLAMA VE HATA TOLERANSI ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
scheduler = AsyncIOScheduler()
DB_NAME = "pera.db"

USER_STATES = {}

# --- DİL VE ARAYÜZ SÖZLÜĞÜ ---
TEXTS = {
    "TR": {
        "welcome_title": "Pera Assistant Active 😊",
        "select_lang": "Lütfen dil seçiniz / Please select language:",
        "menu_msg": "Hoş geldin patron! Sistem tam kapasite çalışıyor.\n\n👇 Aşağıdaki kontrol panelinden işlemlerini yönetebilirsin.",
        "btn_tasks": "📋 Planlarım",
        "btn_notes": "📝 Notlarım",
        "btn_weather": "🌤️ Hava Durumu",
        "btn_settings": "⚙️ Ayarlar",
        "settings_title": "⚙️ **SİSTEM AYARLARI**\nLütfen yapılandırmak istediğiniz modülü seçin:",
        "set_tasks": "📋 Plan Yönetimi",
        "set_notes": "📝 Not Yönetimi",
        "set_weather": "🌍 Konum Ayarları (Hava Durumu)",
        "set_lang": "🌐 Dil Ayarları",
        "set_info": "ℹ️ Sistem Bilgisi",
        "back": "🔙 Geri",
        "add_task": "➕ Plan Ekle",
        "add_note": "➕ Not Ekle",
        "enter_task": "✍️ Lütfen planınızı zaman belirterek yazın:\n*(Örn: 20:00 Kod incelemesi)*",
        "enter_note": "✍️ Lütfen veritabanına eklenecek notu yazın:",
        "enter_weather_loc": "✍️ Lütfen en doğru meteorolojik veri için Ülke, İl, İlçe bilgisi girin:\n*(Örn: Türkiye, İstanbul, Kadıköy)*",
        "no_tasks": "📭 Aktif bir plan bulunmuyor.",
        "no_notes": "📭 Veritabanında kayıtlı not yok.",
        "no_weather_loc": "⚠️ Konum bilgisi ayarlanmamış. Lütfen 'Ayarlar' üzerinden konumunuzu yapılandırın.",
        "tasks_title": "📂 <b>Aktif Planların:</b>\n──────────────",
        "notes_title": "📝 <b>Kayıtlı Notların:</b>\n──────────────",
        "info_msg": (
            "ℹ️ **PERA ASİSTAN - TEKNİK DOKÜMANTASYON**\n\n"
            "Pera, günlük operasyonlarınızı asenkron bir mimariyle optimize eden kişisel yönetim asistanıdır.\n\n"
            "⚙️ **Çekirdek Modüller:**\n"
            "• **Planlarım:** Doğal Dil İşleme (NLP) ile metin içindeki tarih ve saatleri ayrıştırarak görevlerinizi veritabanına kaydeder ve zamanı geldiğinde uyarır.\n"
            "• **Notlarım:** Anlık fikirlerinizi ve verilerinizi kalıcı bellekte depolar.\n"
            "• **Hava Durumu:** Belirlediğiniz spesifik lokasyonun (Ülke, İl, İlçe) anlık termodinamik durumunu, nem, basınç, rüzgar ve UV indeksi gibi meteorolojik parametrelerle analiz eder.\n"
            "• **Ayarlar:** Tüm sistem modüllerini yönetebileceğiniz ana kontrol merkezidir.\n\n"
            "Sistem, bulut sunucularda 7/24 kesintisiz çalışacak şekilde optimize edilmiştir."
        )
    }
}

# --- VERİTABANI İŞLEMLERİ ---
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task_name TEXT, task_time TEXT, end_date TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS settings (user_id INTEGER PRIMARY KEY, language TEXT DEFAULT 'TR')")
        cursor.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, note_text TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cursor.execute("CREATE TABLE IF NOT EXISTS weather_loc (user_id INTEGER PRIMARY KEY, location TEXT)")
        conn.commit()

def set_pref(table, column, user_id, value):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(f"INSERT OR REPLACE INTO {table} (user_id, {column}) VALUES (?, ?)", (user_id, value))

def get_pref(table, column, user_id, default=None):
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.execute(f"SELECT {column} FROM {table} WHERE user_id = ?", (user_id,)).fetchone()
    return res[0] if res else default

def db_action(query, params=(), fetch=False):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        if fetch: return cursor.fetchall()
        conn.commit()
        return cursor.lastrowid

# --- NLP VE ZAMAN PARSER ---
KNOWN_COMMANDS = ["günaydın", "kalkış", "kahvaltı", "öğle yemeği", "akşam yemeği", "toplantı", "spor", "uyku", "hatırlatma", "kodlama", "analiz"]

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
        phrase = words[-2] + " " + words[-1]
        clean_phrase = phrase.replace("gününe", "").replace("aksamina", "").replace("sabahına", "")
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

# --- METEOROLOJİK ANALİZ (HAVA DURUMU) ---
async def fetch_weather_data(location):
    url = f"https://wttr.in/{location.replace(' ', '+')}?format=j1&lang=tr"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    cc = data['current_condition'][0]
                    return {
                        "temp": cc.get('temp_C', '--'),
                        "feels_like": cc.get('FeelsLikeC', '--'),
                        "humidity": cc.get('humidity', '--'),
                        "wind": cc.get('windspeedKmph', '--'),
                        "pressure": cc.get('pressure', '--'),
                        "uv": cc.get('uvIndex', '--'),
                        "desc": cc.get('lang_tr', [{'value': cc.get('weatherDesc', [{'value': ''}])[0]['value']}])[0]['value']
                    }
    except Exception as e:
        logging.error(f"Hava Durumu API Hatası: {e}")
    return None

# --- ZAMANLANMIŞ GÖREVLER ---
async def send_reminder(chat_id: int, text: str):
    try:
        await bot.send_message(chat_id, f"⏰ <b>VAKİT GELDİ:</b>\n👉 {text}")
    except Exception as e:
        logging.error(f"Hatırlatma gönderilemedi ({chat_id}): {e}")

# --- UI BİLEŞENLERİ ---
def get_t(user_id, key):
    lang = get_pref("settings", "language", user_id, "TR") # EN seçeneği veritabanında olsa da TR kalacak şekilde izole edildi
    if lang not in TEXTS: lang = "TR"
    return TEXTS[lang].get(key, key)

def get_pera_menu(user_id):
    t = lambda k: get_t(user_id, k)
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=t("btn_tasks")), KeyboardButton(text=t("btn_notes"))],
        [KeyboardButton(text=t("btn_weather")), KeyboardButton(text=t("btn_settings"))]
    ], resize_keyboard=True, persistent=True)

def settings_kb(user_id):
    t = lambda k: get_t(user_id, k)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("set_tasks"), callback_data="conf_tasks"), InlineKeyboardButton(text=t("set_notes"), callback_data="conf_notes")],
        [InlineKeyboardButton(text=t("set_weather"), callback_data="conf_weather")],
        [InlineKeyboardButton(text=t("set_info"), callback_data="conf_info")]
    ])

# --- YÖNLENDİRİCİLER (HANDLERS) ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    set_pref("settings", "language", message.from_user.id, "TR")
    await message.answer(TEXTS["TR"]["welcome_title"])
    await message.answer(TEXTS["TR"]["menu_msg"], reply_markup=get_pera_menu(message.from_user.id))

@dp.message(F.text)
async def main_menu_handler(message: Message, state: FSMContext):
    uid = message.from_user.id
    txt = message.text.strip()
    t = lambda k: get_t(uid, k)
    user_state = USER_STATES.get(uid)

    # 1. STATE YÖNETİMİ
    if user_state:
        if txt.startswith("/"): 
            USER_STATES[uid] = None
            return
            
        if user_state == "wait_task_add":
            temp_jobs = []
            for line in txt.split("\n"):
                match = re.search(r"(\d{1,2}[:.]\d{2})\s+(.*)", line)
                if match:
                    t_name, e_date = parse_duration(match.group(2).strip())
                    temp_jobs.append({"time": match.group(1).replace(".", ":"), "task": fix_typo_and_format(t_name), "end_date": e_date.isoformat() if e_date else None})
            
            if not temp_jobs:
                await message.answer("⚠️ Saat belirteci saptanamadı. (Örn: 20:00 Kodlama)")
                return
                
            preview_text = "📋 <b>Plan Analizi:</b>\n──────────────\n"
            for job in temp_jobs:
                d_note = f" (Bitiş: {datetime.fromisoformat(job['end_date']).strftime('%d.%m.%Y')})" if job['end_date'] else ""
                preview_text += f"🔹 <b>{job['time']}</b> - {job['task']}{d_note}\n"
                
            await state.update_data(jobs=temp_jobs)
            await state.set_state(PlanForm.waiting_for_confirmation)
            await message.answer(preview_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Onayla", callback_data="confirm_plan"), InlineKeyboardButton(text="❌ İptal", callback_data="cancel_plan")]]))
            USER_STATES[uid] = None
            
        elif user_state == "wait_note_add":
            db_action("INSERT INTO notes (user_id, note_text) VALUES (?, ?)", (uid, txt))
            await message.answer("✅ Veri başarıyla kaydedildi.")
            USER_STATES[uid] = None

        elif user_state == "wait_weather_loc":
            set_pref("weather_loc", "location", uid, txt)
            await message.answer(f"✅ Konum yapılandırması tamamlandı: {txt}")
            USER_STATES[uid] = None
        return

    # 2. ANA MENÜ KONTROL BLOKLARI
    if txt == t("btn_settings"):
        await message.answer(t("settings_title"), reply_markup=settings_kb(uid))

    elif txt == t("btn_tasks"):
        tasks = db_action("SELECT id, task_name, task_time, end_date FROM tasks WHERE user_id = ?", (uid,), True)
        if not tasks:
            await message.answer(t("no_tasks"))
            return
        msg_text = t("tasks_title") + "\n"
        for t_data in tasks:
            note = f" (Son: {datetime.fromisoformat(t_data[3]).strftime('%d.%m.%Y')})" if t_data[3] else ""
            msg_text += f"⏰ <b>{t_data[2]}</b> - {t_data[1]}{note}\n"
        await message.answer(msg_text)

    elif txt == t("btn_notes"):
        notes = db_action("SELECT note_text FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT 10", (uid,), True)
        if not notes:
            await message.answer(t("no_notes"))
            return
        msg_text = t("notes_title") + "\n" + "\n\n".join([f"📌 {n[0]}" for n in notes])
        await message.answer(msg_text)

    elif txt == t("btn_weather"):
        location = get_pref("weather_loc", "location", uid)
        if not location:
            await message.answer(t("no_weather_loc"))
            return
            
        wait_msg = await message.answer("⏳ Atmosferik veriler analiz ediliyor...")
        weather = await fetch_weather_data(location)
        
        if not weather:
            await wait_msg.edit_text("❌ Veri sağlayıcı ile bağlantı kurulamadı veya konum geçersiz.")
            return

        report = (
            f"🌍 <b>Meteorolojik Rapor:</b> {location.title()}\n"
            f"──────────────\n"
            f"🌤️ <b>Durum:</b> {weather['desc']}\n"
            f"🌡️ <b>Sıcaklık:</b> {weather['temp']}°C (Hissedilen: {weather['feels_like']}°C)\n"
            f"💧 <b>Nem Oranı:</b> %{weather['humidity']}\n"
            f"🌬️ <b>Rüzgar:</b> {weather['wind']} km/s\n"
            f"🧭 <b>Basınç:</b> {weather['pressure']} hPa\n"
            f"☀️ <b>UV İndeksi:</b> {weather['uv']}\n"
            f"──────────────"
        )
        await wait_msg.edit_text(report)

# --- AYARLAR VE INLINE CALLBACK ---
@dp.callback_query(F.data.startswith("conf_"))
async def conf_handler(call: CallbackQuery):
    uid, mode = call.from_user.id, call.data.split("_")[1]
    t = lambda k: get_t(uid, k)

    if mode == "info":
        await call.message.edit_text(t("info_msg"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("back"), callback_data="back_settings")]]))
    
    elif mode == "weather":
        USER_STATES[uid] = "wait_weather_loc"
        await call.message.answer(t("enter_weather_loc"))
        await call.answer()

    elif mode in ["tasks", "notes"]:
        item_type = "task" if mode == "tasks" else "note"
        kb_buttons = [[InlineKeyboardButton(text=t(f"add_{item_type}"), callback_data=f"action_add_{item_type}")]]
        
        items = db_action(f"SELECT id, {'task_time, task_name' if mode=='tasks' else 'note_text'} FROM {mode} WHERE user_id = ?", (uid,), True)
        for item in items:
            disp = f"{item[1]} {item[2]}" if mode == "tasks" else (item[1][:25] + "...")
            kb_buttons.append([InlineKeyboardButton(text=f"🗑️ {disp}", callback_data=f"del_{mode}_{item[0]}")])
            
        kb_buttons.append([InlineKeyboardButton(text=t("back"), callback_data="back_settings")])
        await call.message.edit_text(f"⚙️ **{mode.capitalize()} Modülü**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons))

@dp.callback_query(F.data == "back_settings")
async def back_to_settings(call: CallbackQuery):
    USER_STATES[call.from_user.id] = None
    await call.message.edit_text(get_t(call.from_user.id, "settings_title"), reply_markup=settings_kb(call.from_user.id))

@dp.callback_query(F.data.startswith("action_add_"))
async def trigger_add(call: CallbackQuery):
    item_type = call.data.split("action_add_")[1]
    USER_STATES[call.from_user.id] = f"wait_{item_type}_add"
    await call.message.answer(get_t(call.from_user.id, f"enter_{item_type}"))
    await call.answer()

@dp.callback_query(F.data.startswith("del_"))
async def delete_item_handler(call: CallbackQuery):
    _, table, item_id = call.data.split("_")
    db_action(f"DELETE FROM {table} WHERE id = ?", (int(item_id),))
    if table == "tasks":
        try: scheduler.remove_job(item_id)
        except: pass
    await call.answer("✅ Veri silindi.")
    await call.message.delete()

@dp.callback_query(F.data == "confirm_plan", PlanForm.waiting_for_confirmation)
async def process_confirm(call: CallbackQuery, state: FSMContext):
    jobs = (await state.get_data()).get("jobs", [])
    for job in jobs:
        task_id = db_action("INSERT INTO tasks (user_id, task_name, task_time, end_date) VALUES (?, ?, ?, ?)", (call.message.chat.id, job['task'], job['time'], job['end_date']))
        h, m = map(int, job['time'].split(":"))
        e_dt = datetime.fromisoformat(job['end_date']) if job['end_date'] else None
        scheduler.add_job(send_reminder, "cron", hour=h, minute=m, end_date=e_dt, args=[call.message.chat.id, job['task']], id=str(task_id))
    await call.message.edit_text(f"✅ {len(jobs)} plan başarıyla senkronize edildi.")
    await state.clear()

@dp.callback_query(F.data == "cancel_plan", PlanForm.waiting_for_confirmation)
async def process_cancel(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("❌ İşlem iptal edildi.")
    await state.clear()

# --- BAŞLATMA VE OPTİMİZASYON (RENDER STABILITY) ---
async def load_tasks_on_startup():
    for t_id, u_id, t_name, t_time, t_end in db_action("SELECT id, user_id, task_name, task_time, end_date FROM tasks", fetch=True):
        h, m = map(int, t_time.split(":"))
        e_dt = datetime.fromisoformat(t_end) if t_end else None
        try: scheduler.add_job(send_reminder, "cron", hour=h, minute=m, end_date=e_dt, args=[u_id, t_name], id=str(t_id), replace_existing=True)
        except Exception as e: logging.error(f"Plan yükleme hatası ({t_id}): {e}")

async def health_check(request):
    return web.Response(text="Pera Assistant is running with optimized stability! 😊")

async def main():
    init_db()
    await load_tasks_on_startup()
    scheduler.start()
    
    logging.info("🚀 PERA (V12 - Weather & High Stability) Başlatıldı.")
    
    # Asenkron Web Server Başlatma (Timeout önlemi)
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", 8080)))
    await site.start()
    
    # Long Polling Başlatma
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except Exception as e:
        logging.error(f"Polling Hatası: {e}")

if __name__ == "__main__":
    try: 
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit): 
        logging.info("Sistem kapatıldı.")
