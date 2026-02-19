import asyncio
import logging
import sys
import os
import re
import sqlite3
from datetime import datetime, date, timezone
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
import aiohttp

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

# --- DİL SÖZLÜĞÜ ---
TEXTS = {
    "TR": {
        "welcome_title": "🤖 **PERA ASİSTAN AKTİF** 🤖",
        "select_lang": "Lütfen dil seçiniz / Please select language:",
        "menu_msg": "Hoş geldin patron! Görevlerini, notlarını ve projelerini takip etmek için hazırım.\n\n👇 Aşağıdaki sabit menüden işlemlerini yönetebilirsin.",
        "btn_tasks": "📋 Görevlerim",
        "btn_github": "🐙 GitHub Durumu",
        "btn_notes": "📝 Hızlı Notlar",
        "btn_settings": "⚙️ Ayarlar",
        "settings_title": "⚙️ **AYARLAR MENÜSÜ**\nLütfen düzenlemek istediğiniz alanı seçin:",
        "set_tasks": "📋 Görev Yönetimi",
        "set_notes": "📝 Not Yönetimi",
        "set_github": "🐙 GitHub Kullanıcı Adı",
        "set_lang": "🌐 Dil / Language",
        "back": "🔙 Geri",
        "add_task": "➕ Görev Ekle",
        "add_note": "➕ Not Ekle",
        "enter_task": "✍️ Lütfen planınızı yazın:\n*(Örn: 08:00 Kahvaltı veya 15:30 Toplantı yarına kadar)*",
        "enter_note": "✍️ Lütfen kaydetmek istediğiniz notu yazın:",
        "enter_github": "✍️ Lütfen GitHub kullanıcı adınızı yazın (Örn: yusufisl4m):",
        "no_tasks": "📭 Planlanmış görevin yok.",
        "no_notes": "📭 Kayıtlı notun bulunmuyor.",
        "tasks_title": "📂 <b>Kayıtlı Planların:</b>\n──────────────",
        "notes_title": "📝 <b>Hızlı Notların:</b>\n──────────────",
        "github_not_set": "⚠️ GitHub kullanıcı adınız ayarlanmamış. Lütfen 'Ayarlar' menüsünden ekleyin.",
        "github_status": "🐙 <b>GitHub Günlük Raporu:</b>\n👤 Kullanıcı: {username}\n🟩 Bugünkü Commit: {count}\n\n{msg}"
    },
    "EN": {
        "welcome_title": "🤖 **PERA ASSISTANT ACTIVE** 🤖",
        "select_lang": "Please select language:",
        "menu_msg": "Welcome boss! I am ready to track your tasks, notes, and projects.\n\n👇 Use the pinned menu below.",
        "btn_tasks": "📋 My Tasks",
        "btn_github": "🐙 GitHub Status",
        "btn_notes": "📝 Quick Notes",
        "btn_settings": "⚙️ Settings",
        "settings_title": "⚙️ **SETTINGS MENU**\nPlease select an area:",
        "set_tasks": "📋 Task Management",
        "set_notes": "📝 Note Management",
        "set_github": "🐙 Set GitHub Username",
        "set_lang": "🌐 Language",
        "back": "🔙 Back",
        "add_task": "➕ Add Task",
        "add_note": "➕ Add Note",
        "enter_task": "✍️ Please enter your plan:\n*(e.g., 08:00 Breakfast)*",
        "enter_note": "✍️ Please enter your note:",
        "enter_github": "✍️ Please enter your GitHub username:",
        "no_tasks": "📭 No scheduled tasks.",
        "no_notes": "📭 No saved notes.",
        "tasks_title": "📂 <b>Your Tasks:</b>\n──────────────",
        "notes_title": "📝 <b>Your Notes:</b>\n──────────────",
        "github_not_set": "⚠️ GitHub username not set. Please add it from Settings.",
        "github_status": "🐙 <b>GitHub Daily Report:</b>\n👤 User: {username}\n🟩 Today's Commits: {count}\n\n{msg}"
    }
}

# --- VERİTABANI İŞLEMLERİ ---
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task_name TEXT, task_time TEXT, end_date TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS settings (user_id INTEGER PRIMARY KEY, language TEXT DEFAULT 'TR')")
        cursor.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, note_text TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cursor.execute("CREATE TABLE IF NOT EXISTS github_settings (user_id INTEGER PRIMARY KEY, username TEXT)")
        conn.commit()

# --- DİL VE GITHUB SETTINGS ---
def set_pref(table, column, user_id, value):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(f"INSERT OR REPLACE INTO {table} (user_id, {column}) VALUES (?, ?)", (user_id, value))

def get_pref(table, column, user_id, default=None):
    with sqlite3.connect(DB_NAME) as conn:
        res = conn.execute(f"SELECT {column} FROM {table} WHERE user_id = ?", (user_id,)).fetchone()
    return res[0] if res else default

# --- GÖREV VE NOT YÖNETİMİ ---
def db_action(query, params=(), fetch=False):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        if fetch: return cursor.fetchall()
        conn.commit()
        return cursor.lastrowid

# --- NLP VE PARSER ---
KNOWN_COMMANDS = ["günaydın", "kalkış", "kahvaltı", "öğle yemeği", "akşam yemeği", "toplantı", "spor", "uyku", "hatırlatma"]
class PlanForm(StatesGroup): waiting_for_confirmation = State()

def fix_typo_and_format(text):
    best_match, score = process.extractOne(text, KNOWN_COMMANDS)
    return best_match.title() if score > 70 else text.title()

def parse_duration(full_text):
    if "kadar" not in full_text.lower(): return full_text, None
    words = full_text.lower().split("kadar")[0].strip().split()
    if not words: return full_text, None
    candidate_date, idx = None, len(words)
    if len(words) >= 2:
        dt = dateparser.parse(f"{words[-2]} {words[-1]}", languages=['tr'], settings={'PREFER_DATES_FROM': 'future'})
        if dt: candidate_date, idx = dt, len(words) - 2
    if not candidate_date:
        dt = dateparser.parse(words[-1], languages=['tr'], settings={'PREFER_DATES_FROM': 'future'})
        if dt: candidate_date, idx = dt, len(words) - 1
    if candidate_date: return " ".join(words[:idx]), candidate_date.replace(hour=23, minute=59, second=59)
    return full_text, None

# --- GITHUB PUBLIC API ÇEKİRDEĞİ ---
async def fetch_github_commits(username):
    """Herkese açık (public) olayları okur. Güvenlidir, token gerektirmez."""
    url = f"https://api.github.com/users/{username}/events/public"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    events = await resp.json()
                    # GitHub saat dilimi (UTC) bazlı gün hesaplaması
                    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                    commits_today = sum(
                        len(e['payload'].get('commits', [])) 
                        for e in events 
                        if e['created_at'].startswith(today) and e['type'] == 'PushEvent'
                    )
                    return commits_today
        except Exception as e:
            logging.error(f"GitHub API Error: {e}")
    return -1

async def send_reminder(chat_id: int, text: str):
    await bot.send_message(chat_id, f"⏰ <b>VAKİT GELDİ:</b>\n👉 {text}")

async def check_daily_github(chat_id: int):
    """Her akşam 21:00'de yeşil kare uyarısı yapar."""
    username = get_pref("github_settings", "username", chat_id)
    if username:
        commits = await fetch_github_commits(username)
        if commits == 0:
            await bot.send_message(chat_id, "⚠️ Patron, bugün GitHub'a hiç kod göndermedin! Yeşil seriyi bozmamak için commit atmayı unutma. 🟩")

# --- UI BİLEŞENLERİ ---
def get_t(user_id, key):
    lang = get_pref("settings", "language", user_id, "TR")
    return TEXTS[lang].get(key, key)

def get_pera_menu(user_id):
    t = lambda k: get_t(user_id, k)
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=t("btn_tasks")), KeyboardButton(text=t("btn_github"))],
        [KeyboardButton(text=t("btn_notes")), KeyboardButton(text=t("btn_settings"))]
    ], resize_keyboard=True, persistent=True)

def settings_kb(user_id):
    t = lambda k: get_t(user_id, k)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("set_tasks"), callback_data="conf_tasks"), InlineKeyboardButton(text=t("set_notes"), callback_data="conf_notes")],
        [InlineKeyboardButton(text=t("set_github"), callback_data="conf_github"), InlineKeyboardButton(text=t("set_lang"), callback_data="conf_lang")]
    ])

# --- YÖNLENDİRİCİLER ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(TEXTS["TR"]["welcome_title"])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🇹🇷 Türkçe", callback_data="lang_TR"), InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_EN")]])
    await message.answer(TEXTS["TR"]["select_lang"], reply_markup=kb)

@dp.callback_query(F.data.startswith("lang_"))
async def process_language_selection(call: CallbackQuery):
    lang_code = call.data.split("_")[1]
    set_pref("settings", "language", call.from_user.id, lang_code)
    await call.message.delete()
    await call.message.answer(get_t(call.from_user.id, "menu_msg"), reply_markup=get_pera_menu(call.from_user.id))

@dp.message(F.text)
async def main_menu_handler(message: Message, state: FSMContext):
    uid = message.from_user.id
    txt = message.text.strip()
    t = lambda k: get_t(uid, k)
    user_state = USER_STATES.get(uid)

    # 1. DURUM YÖNETİMİ (KAYIT MODLARI)
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
                await message.answer("⚠️ Format hatası. Örn: '08:00 Kahvaltı'")
                return
            preview_text = "📋 <b>Plan Analizi:</b>\n──────────────\n" + "".join([f"🔹 <b>{j['time']}</b> - {j['task']}\n" for j in temp_jobs])
            await state.update_data(jobs=temp_jobs)
            await state.set_state(PlanForm.waiting_for_confirmation)
            await message.answer(preview_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Onayla", callback_data="confirm_plan"), InlineKeyboardButton(text="❌ İptal", callback_data="cancel_plan")]]))
            USER_STATES[uid] = None
            
        elif user_state == "wait_note_add":
            db_action("INSERT INTO notes (user_id, note_text) VALUES (?, ?)", (uid, txt))
            await message.answer("✅ Not başarıyla kaydedildi!")
            USER_STATES[uid] = None
            
        elif user_state == "wait_github_user":
            set_pref("github_settings", "username", uid, txt)
            await message.answer(f"✅ GitHub hesabı ayarlandı: {txt}")
            USER_STATES[uid] = None
        return

    # 2. SABİT MENÜ BUTONLARI
    if txt in [TEXTS["TR"]["btn_settings"], TEXTS["EN"]["btn_settings"]]:
        await message.answer(t("settings_title"), reply_markup=settings_kb(uid))

    elif txt in [TEXTS["TR"]["btn_tasks"], TEXTS["EN"]["btn_tasks"]]:
        tasks = db_action("SELECT id, task_name, task_time, end_date FROM tasks WHERE user_id = ?", (uid,), True)
        if not tasks:
            await message.answer(t("no_tasks"))
            return
        msg_text = t("tasks_title") + "\n" + "".join([f"⏰ <b>{t[2]}</b> - {t[1]}\n" for t in tasks])
        await message.answer(msg_text)

    elif txt in [TEXTS["TR"]["btn_notes"], TEXTS["EN"]["btn_notes"]]:
        notes = db_action("SELECT note_text, created_at FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT 10", (uid,), True)
        if not notes:
            await message.answer(t("no_notes"))
            return
        msg_text = t("notes_title") + "\n" + "\n\n".join([f"📌 {n[0]}" for n in notes])
        await message.answer(msg_text)

    elif txt in [TEXTS["TR"]["btn_github"], TEXTS["EN"]["btn_github"]]:
        username = get_pref("github_settings", "username", uid)
        if not username:
            await message.answer(t("github_not_set"))
            return
            
        wait_msg = await message.answer("⏳ GitHub verileri analiz ediliyor...")
        commits = await fetch_github_commits(username)
        
        if commits == -1:
            await wait_msg.edit_text("❌ Kullanıcı bulunamadı veya API sınırına ulaşıldı.")
        else:
            status_msg = "Harika gidiyorsun, kodlama serin devam ediyor! 🔥" if commits > 0 else "Bugün henüz kod göndermedin, yeşil kareyi yakmayı unutma! 🟩"
            await wait_msg.edit_text(t("github_status").format(username=username, count=commits, msg=status_msg))

# --- AYARLAR & INLINE İŞLEMLER ---
@dp.callback_query(F.data.startswith("conf_"))
async def conf_handler(call: CallbackQuery):
    uid, mode = call.from_user.id, call.data.split("_")[1]
    t = lambda k: get_t(uid, k)

    if mode == "lang":
        await call.message.edit_text(t("select_lang"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🇹🇷 TR", callback_data="lang_TR"), InlineKeyboardButton(text="🇬🇧 EN", callback_data="lang_EN")], [InlineKeyboardButton(text=t("back"), callback_data="back_settings")]]))
    elif mode == "github":
        USER_STATES[uid] = "wait_github_user"
        await call.message.answer(t("enter_github"))
        await call.answer()
    elif mode in ["tasks", "notes"]:
        kb_buttons = [[InlineKeyboardButton(text=t(f"add_{mode[:-1]}"), callback_data=f"action_add_{mode}")]]
        items = db_action(f"SELECT id, {'task_time, task_name' if mode=='tasks' else 'note_text'} FROM {mode} WHERE user_id = ?", (uid,), True)
        for item in items:
            disp = f"{item[1]} {item[2]}" if mode == "tasks" else (item[1][:20] + "...")
            kb_buttons.append([InlineKeyboardButton(text=f"🗑️ {disp}", callback_data=f"del_{mode}_{item[0]}")])
        kb_buttons.append([InlineKeyboardButton(text=t("back"), callback_data="back_settings")])
        await call.message.edit_text(f"⚙️ **{mode.capitalize()} Yönetimi**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons))

@dp.callback_query(F.data == "back_settings")
async def back_to_settings(call: CallbackQuery):
    USER_STATES[call.from_user.id] = None
    await call.message.edit_text(get_t(call.from_user.id, "settings_title"), reply_markup=settings_kb(call.from_user.id))

@dp.callback_query(F.data.startswith("action_add_"))
async def trigger_add(call: CallbackQuery):
    mode = call.data.split("_")[2] # task or note
    USER_STATES[call.from_user.id] = f"wait_{mode}_add"
    await call.message.answer(get_t(call.from_user.id, f"enter_{mode}"))
    await call.answer()

@dp.callback_query(F.data.startswith("del_"))
async def delete_item_handler(call: CallbackQuery):
    _, table, item_id = call.data.split("_")
    db_action(f"DELETE FROM {table} WHERE id = ?", (int(item_id),))
    if table == "tasks":
        try: scheduler.remove_job(item_id)
        except: pass
    await call.answer("✅ Silindi!")
    await call.message.delete()

@dp.callback_query(F.data == "confirm_plan", PlanForm.waiting_for_confirmation)
async def process_confirm(call: CallbackQuery, state: FSMContext):
    jobs = (await state.get_data()).get("jobs", [])
    for job in jobs:
        task_id = db_action("INSERT INTO tasks (user_id, task_name, task_time, end_date) VALUES (?, ?, ?, ?)", (call.message.chat.id, job['task'], job['time'], job['end_date']))
        h, m = map(int, job['time'].split(":"))
        e_dt = datetime.fromisoformat(job['end_date']) if job['end_date'] else None
        scheduler.add_job(send_reminder, "cron", hour=h, minute=m, end_date=e_dt, args=[call.message.chat.id, job['task']], id=str(task_id))
    await call.message.edit_text(f"✅ {len(jobs)} Görev Zamanlandı!")
    await state.clear()

@dp.callback_query(F.data == "cancel_plan", PlanForm.waiting_for_confirmation)
async def process_cancel(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("❌ İşlem iptal edildi.")
    await state.clear()

# --- BAŞLANGIÇ YÜKLEMELERİ VE RENDER SUNUCUSU ---
async def load_tasks_on_startup():
    for t_id, u_id, t_name, t_time, t_end in db_action("SELECT id, user_id, task_name, task_time, end_date FROM tasks", fetch=True):
        h, m = map(int, t_time.split(":"))
        e_dt = datetime.fromisoformat(t_end) if t_end else None
        try: scheduler.add_job(send_reminder, "cron", hour=h, minute=m, end_date=e_dt, args=[u_id, t_name], id=str(t_id), replace_existing=True)
        except: pass

async def health_check(request):
    return web.Response(text="Pera Assistant is running smoothly! 🚀")

async def main():
    init_db()
    await load_tasks_on_startup()
    
    if ADMIN_ID:
        # Her akşam saat 21:00'de GitHub kontrolü
        scheduler.add_job(check_daily_github, 'cron', hour=21, minute=0, args=[int(ADMIN_ID)], id='github_daily_check', replace_existing=True)
    
    scheduler.start()
    logging.info("🚀 PERA (V10 - GitHub & Notes) Started")
    
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", 8080)))
    await site.start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try: asyncio.run(main())
    except (KeyboardInterrupt, SystemExit): pass
