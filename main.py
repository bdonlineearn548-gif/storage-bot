import os
import time
import logging
import datetime
import html
import random
import queue
import requests
import telebot
from telebot import types
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from flask import Flask
from threading import Thread, Timer, Lock

# ==========================================
# 1. Configuration (আপনার আগের ক্রেডেনশিয়াল)
# ==========================================
BOT_TOKEN = "8725779053:AAEE-eDLIAuGiECIPoMUIIw0tsN9PRA1mKM"
ADMIN_ID = 6271611009
LOG_CHANNEL_ID = -1003481796766
DB_URI = "postgresql://postgres.pofuxngbmbkbsvliqyka:czpH1jl4dGQLD84B@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres"
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True, num_threads=8)
logging.basicConfig(level=logging.INFO)

# In-Memory Cache ও সিঙ্ক্রোনাইজেশন লক
workspace_cache = {}
user_states = {}
media_groups = {}
media_lock = Lock()

# ==========================================
# 2. Keep-alive Flask Server (24/7 on Render)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "⚡ High-Speed Telegram Cloud Drive Bot Running!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def auto_keep_alive():
    while True:
        time.sleep(300)
        if RENDER_APP_URL:
            try:
                requests.get(RENDER_APP_URL, timeout=10)
            except Exception:
                pass

Thread(target=run_web, daemon=True).start()
Thread(target=auto_keep_alive, daemon=True).start()

# ==========================================
# 3. Thread-Safe Database Pool
# ==========================================
db_pool = ThreadedConnectionPool(1, 20, DB_URI)

def run_query(query, params=(), fetch=False):
    conn = None
    data = None
    try:
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute(query, params)
            if fetch:
                data = cur.fetchall()
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Database Error: {e} | Query: {query}")
    finally:
        if conn:
            db_pool.putconn(conn)
    return data

def auto_setup_db():
    run_query("""
    CREATE TABLE IF NOT EXISTS playlists (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        playlist_name TEXT
    );
    CREATE TABLE IF NOT EXISTS files (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        file_type TEXT, 
        file_id TEXT,
        file_unique_id TEXT,
        file_name TEXT,
        playlist_name TEXT,
        date TEXT,
        message_id BIGINT,
        media_group_id TEXT
    );
    CREATE TABLE IF NOT EXISTS notes (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        title TEXT,
        content TEXT,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS users_list (
        user_id BIGINT PRIMARY KEY,
        full_name TEXT,
        username TEXT,
        join_date TEXT
    );
    CREATE TABLE IF NOT EXISTS user_settings (
        user_id BIGINT PRIMARY KEY,
        active_workspace BIGINT
    );
    CREATE TABLE IF NOT EXISTS invites (
        code TEXT PRIMARY KEY,
        owner_id BIGINT
    );
    ALTER TABLE files ADD COLUMN IF NOT EXISTS file_unique_id TEXT;
    ALTER TABLE files ADD COLUMN IF NOT EXISTS message_id BIGINT;
    ALTER TABLE files ADD COLUMN IF NOT EXISTS media_group_id TEXT;
    """)

auto_setup_db()

def get_workspace(uid):
    if uid in workspace_cache:
        return workspace_cache[uid]
    res = run_query("SELECT active_workspace FROM user_settings WHERE user_id = %s", (uid,), fetch=True)
    if res:
        wid = res[0][0]
    else:
        run_query("INSERT INTO user_settings (user_id, active_workspace) VALUES (%s, %s) ON CONFLICT DO NOTHING", (uid, uid))
        wid = uid
    workspace_cache[uid] = wid
    return wid

# --- Background Channel Logging Queue (মেমোরি ও র‍্যাম সেইভ করার জন্য) ---
log_queue = queue.Queue()

def log_worker():
    while True:
        chat_id, msg_id = log_queue.get()
        if LOG_CHANNEL_ID:
            try:
                bot.copy_message(LOG_CHANNEL_ID, chat_id, msg_id)
                time.sleep(0.05)
            except Exception as e:
                logging.error(f"Channel Log Error: {e}")
        log_queue.task_done()

Thread(target=log_worker, daemon=True).start()

# ==========================================
# 4. Helper: Send Photos in 10-Item Grid
# ==========================================
def send_photos_as_grid(chat_id, photo_list, wid):
    chunk_size = 10
    for i in range(0, len(photo_list), chunk_size):
        chunk = photo_list[i:i + chunk_size]
        media_group = []
        for idx, item in enumerate(chunk):
            cap = item['caption'] if idx == 0 else ""
            media_group.append(types.InputMediaPhoto(media=item['file_id'], caption=cap))
        try:
            sent_msgs = bot.send_media_group(chat_id, media=media_group)
            for s_msg, item in zip(sent_msgs, chunk):
                run_query("UPDATE files SET message_id = %s WHERE id = %s", (s_msg.message_id, item['id']))
        except Exception as e:
            logging.error(f"Media group send error: {e}")

def get_replied_media(replied):
    if replied.photo:
        return replied.photo[-1]
    return replied.video or replied.document or replied.audio or replied.voice

# ==========================================
# 5. Main Keyboard
# ==========================================
def main_keyboard(uid):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.row(types.KeyboardButton("📁 প্লেলিস্টসমূহ"), types.KeyboardButton("📝 নোটস"))
    markup.row(types.KeyboardButton("🔍 সার্চ ফাইল"), types.KeyboardButton("📅 আপলোডের তারিখসমূহ"))
    markup.row(types.KeyboardButton("📊 ড্রাইভ ড্যাশবোর্ড"))
    if uid == ADMIN_ID:
        markup.row(types.KeyboardButton("⚙️ Admin Panel"))
    markup.row(types.KeyboardButton("ℹ️ Help"))
    return markup

# ==========================================
# 6. Basic & Pairing Commands
# ==========================================
@bot.message_handler(commands=['start'])
def start_cmd(message):
    uid = message.from_user.id
    fname = message.from_user.first_name
    full_name = f"{fname} {message.from_user.last_name or ''}".strip()
    uname = message.from_user.username or "N/A"
    date_now = datetime.datetime.now().strftime("%Y-%m-%d")

    run_query("INSERT INTO users_list (user_id, full_name, username, join_date) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING", 
              (uid, full_name, uname, date_now))

    wid = get_workspace(uid)
    existing_pl = run_query("SELECT id FROM playlists WHERE user_id = %s", (wid,), fetch=True)
    if not existing_pl:
        run_query("INSERT INTO playlists (user_id, playlist_name) VALUES (%s, '🎬 Default Series')", (wid,))
        run_query("INSERT INTO playlists (user_id, playlist_name) VALUES (%s, '🎨 My Assets')", (wid,))

    mention = f"<a href='tg://user?id={uid}'>{html.escape(full_name)}</a>"
    welcome_text = (
        f"⚡ <b>আসসালামু আলাইকুম, {mention}!</b> ⚡\n\n"
        f"🚀 <b>আপনার হাই-স্পিড ক্লাউড ড্রাইভ ও নোটস রেডি!</b>\n\n"
        f"📌 <b>কমান্ডস:</b>\n"
        f"• নতুন প্লেলিস্ট: <code>/add নাম</code> | ডিলিট: <code>/rem</code>\n"
        f"• <b>অ্যালবাম রিনেম:</b> অ্যালবামের একটি ছবিতে Reply দিয়ে নতুন নাম দিন।\n"
        f"• <b>শেয়ারিং:</b> <code>/share</code> কোড তৈরি করতে, যুক্ত হতে <code>/join কোড</code>\n\n"
        f"👇 নিচের মেনু ব্যবহার করুন:"
    )

    try:
        photos = bot.get_user_profile_photos(uid)
        if photos.total_count > 0:
            bot.send_photo(uid, photos.photos[0][-1].file_id, caption=welcome_text, reply_markup=main_keyboard(uid))
        else:
            bot.send_message(uid, welcome_text, reply_markup=main_keyboard(uid))
    except Exception:
        bot.send_message(uid, welcome_text, reply_markup=main_keyboard(uid))

@bot.message_handler(commands=['share'])
def share_cmd(message):
    uid = message.from_user.id
    wid = get_workspace(uid)
    if wid != uid:
        return bot.reply_to(message, "⚠️ আপনি বর্তমানে অন্যের শেয়ার্ড ড্রাইভে আছেন। নিজের ড্রাইভ শেয়ার করতে <code>/leave</code> কমান্ড দিন।")
    
    code = f"DRIVE-{random.randint(100000, 999999)}"
    run_query("INSERT INTO invites (code, owner_id) VALUES (%s, %s)", (code, uid))
    bot.reply_to(message, f"🔗 <b>আপনার ড্রাইভ শেয়ারিং কোড:</b>\n\n<code>/join {code}</code>\n\nযাকে অ্যাক্সেস দিতে চান তাকে এই কোডটি বটে পাঠাতে বলুন।")

@bot.message_handler(commands=['join'])
def join_cmd(message):
    uid = message.from_user.id
    args = message.text.split()
    if len(args) < 2:
        return bot.reply_to(message, "⚠️ কোড উল্লেখ করুন। উদাহরণ: <code>/join DRIVE-123456</code>")
    
    code = args[1]
    invite = run_query("SELECT owner_id FROM invites WHERE code = %s", (code,), fetch=True)
    if not invite:
        return bot.reply_to(message, "❌ কোডটি সঠিক নয় বা মেয়াদ শেষ!")
    
    owner_id = invite[0][0]
    if owner_id == uid:
        return bot.reply_to(message, "⚠️ এটি আপনার নিজেরই ড্রাইভ!")
    
    workspace_cache[uid] = owner_id
    run_query("INSERT INTO user_settings (user_id, active_workspace) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET active_workspace = %s", (uid, owner_id, owner_id))
    bot.reply_to(message, "✅ <b>শেয়ার্ড ড্রাইভে যুক্ত হয়েছেন!</b>\nএখন থেকে দুজনেই একই ড্রাইভ ও ফাইল একসাথে ব্যবহার করতে পারবেন।")

@bot.message_handler(commands=['leave'])
def leave_cmd(message):
    uid = message.from_user.id
    wid = get_workspace(uid)
    if wid == uid:
        return bot.reply_to(message, "⚠️ আপনি ইতিমধ্যে নিজের পার্সোনাল ড্রাইভেই আছেন।")
    
    workspace_cache[uid] = uid
    run_query("UPDATE user_settings SET active_workspace = %s WHERE user_id = %s", (uid, uid))
    bot.reply_to(message, "✅ শেয়ার্ড ড্রাইভ থেকে বিচ্ছিন্ন হয়ে নিজের ব্যক্তিগত ড্রাইভে ফিরে এসেছেন।")

@bot.message_handler(commands=['add', 'new'])
def add_playlist_cmd(message):
    uid = message.from_user.id
    wid = get_workspace(uid)
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(message, "⚠️ নাম উল্লেখ করুন। উদাহরণ: <code>/add My Tour</code>")
    
    pl_name = args[1].strip()
    exist = run_query("SELECT id FROM playlists WHERE user_id=%s AND playlist_name=%s", (wid, pl_name), fetch=True)
    if exist:
        return bot.reply_to(message, "⚠️ এই নামের প্লেলিস্ট ইতিমধ্যে রয়েছে!")
    
    run_query("INSERT INTO playlists (user_id, playlist_name) VALUES (%s, %s)", (wid, pl_name))
    bot.reply_to(message, f"✅ <b>{pl_name}</b> প্লেলিস্ট তৈরি হয়েছে!")

@bot.message_handler(commands=['rem', 'remove'])
def remove_playlist_cmd(message):
    uid = message.from_user.id
    wid = get_workspace(uid)
    pls = run_query("SELECT id, playlist_name FROM playlists WHERE user_id = %s", (wid,), fetch=True)
    if not pls:
        return bot.send_message(message.chat.id, "❌ আপনার কোনো প্লেলিস্ট নেই।")
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    for p_id, p_name in pls:
        markup.add(types.InlineKeyboardButton(f"🗑 {p_name}", callback_data=f"ask_del_pl|{p_id}"))
    bot.send_message(message.chat.id, "🗑 <b>কোন প্লেলিস্টটি ডিলিট করতে চান?</b>", reply_markup=markup)

# ==========================================
# 7. Menu Controller
# ==========================================
@bot.message_handler(func=lambda m: m.text in ["📁 প্লেলিস্টসমূহ", "📝 নোটস", "📅 আপলোডের তারিখসমূহ", "🔍 সার্চ ফাইল", "📊 ড্রাইভ ড্যাশবোর্ড", "ℹ️ Help", "⚙️ Admin Panel"])
def menu_controller(message):
    uid = message.from_user.id
    wid = get_workspace(uid)
    text = message.text

    if text == "📁 প্লেলিস্টসমূহ":
        pls = run_query("SELECT id, playlist_name FROM playlists WHERE user_id = %s", (wid,), fetch=True)
        if not pls:
            return bot.send_message(message.chat.id, "❌ কোনো প্লেলিস্ট নেই। <code>/add নাম</code> দিয়ে তৈরি করুন।")
        markup = types.InlineKeyboardMarkup(row_width=2)
        for p_id, p_name in pls:
            markup.add(types.InlineKeyboardButton(f"📂 {p_name}", callback_data=f"choose_type|{p_id}|{wid}"))
        bot.send_message(message.chat.id, "📁 <b>আপনার প্লেলিস্টসমূহ:</b>", reply_markup=markup)

    elif text == "📝 নোটস":
        show_notes_menu(message.chat.id, wid, message_id=None, uid=uid)

    elif text == "📅 আপলোডের তারিখসমূহ":
        dates = run_query("SELECT date, COUNT(id) FROM files WHERE user_id = %s GROUP BY date ORDER BY date DESC", (wid,), fetch=True)
        if not dates:
            return bot.send_message(message.chat.id, "❌ কোনো ফাইল আপলোড করা হয়নি।")
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        res_text = "🗓 <b>আপলোড হিস্ট্রি:</b>\n\n"
        for d, count in dates:
            res_text += f"• <b>{d}</b>: <b>{count}</b> টি ফাইল\n"
            markup.add(types.InlineKeyboardButton(f"📅 {d} ({count} টি ফাইল দেখুন)", callback_data=f"view_date|{d}"))
        bot.send_message(message.chat.id, res_text, reply_markup=markup)

    elif text == "🔍 সার্চ ফাইল":
        user_states[uid] = {'action': 'searching'}
        bot.send_message(message.chat.id, "🔎 ফাইলের নাম বা ক্যাপশন লিখে পাঠান:")

    elif text == "📊 ড্রাইভ ড্যাশবোর্ড":
        counts = run_query("SELECT file_type, COUNT(id) FROM files WHERE user_id=%s GROUP BY file_type", (wid,), fetch=True) or []
        count_dict = {row[0]: row[1] for row in counts}
        
        p_count = count_dict.get('photo', 0)
        v_count = count_dict.get('video', 0)
        d_count = count_dict.get('document', 0)
        a_count = count_dict.get('audio', 0) + count_dict.get('voice', 0)
        notes_res = run_query("SELECT COUNT(id) FROM notes WHERE user_id=%s", (wid,), fetch=True)
        n_count = notes_res[0][0] if notes_res else 0

        stat_msg = (
            f"📊 <b>আপনার ক্লাউড স্টোরেজ স্ট্যাটাস</b>\n"
            f"═══════════════════════\n"
            f"🖼️ মোট ছবি: <b>{p_count}</b> টি\n"
            f"🎬 মোট ভিডিও: <b>{v_count}</b> টি\n"
            f"📄 মোট ডকুমেন্ট: <b>{d_count}</b> টি\n"
            f"🎵 মোট অডিও: <b>{a_count}</b> টি\n"
            f"📝 মোট সংরক্ষিত নোটস: <b>{n_count}</b> টি\n"
            f"═══════════════════════\n"
            f"📁 মোট মিডিয়া আইটেম: <b>{p_count + v_count + d_count + a_count}</b> টি"
        )
        bot.send_message(message.chat.id, stat_msg)

    elif text == "ℹ️ Help":
        help_msg = (
            "❓ <b>ব্যবহার নির্দেশিকা:</b>\n\n"
            "• প্লেলিস্ট: <code>/add নাম</code> | মুছতে: <code>/rem</code>\n"
            "• <b>শেয়ারিং:</b> <code>/share</code> কোড দিয়ে অন্যকে <code>/join</code> করতে দিন।\n"
            "• <b>অ্যালবাম রিনেম:</b> ছবির মেসেজে Reply দিয়ে নাম পাঠালে সম্পূর্ণ অ্যালবাম রিনেম হবে।\n"
            "• <b>মিডিয়া রিপ্লেস:</b> ফাইলে Reply দিয়ে নতুন ফাইল পাঠান।"
        )
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("📩 Admin Inbox", url="https://t.me/rm_rasel_hossain"))
        bot.send_message(message.chat.id, help_msg, reply_markup=markup)

    elif text == "⚙️ Admin Panel" and uid == ADMIN_ID:
        show_admin_panel(message.chat.id)

# ==========================================
# 8. Notes System Functionality
# ==========================================
def show_notes_menu(chat_id, wid, message_id=None, uid=None):
    notes = run_query("SELECT id, title FROM notes WHERE user_id=%s ORDER BY id DESC", (wid,), fetch=True)
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    if notes:
        for n_id, n_title in notes:
            markup.add(types.InlineKeyboardButton(f"📄 {n_title}", callback_data=f"read_note|{n_id}"))
    
    markup.row(types.InlineKeyboardButton("➕ নতুন নোট যোগ করুন", callback_data="btn_add_note"),
               types.InlineKeyboardButton("🔍 সার্চ নোট", callback_data="btn_search_note"))
    
    text = "📝 <b>আপনার সংরক্ষিত নোটস সমূহ:</b>\n\nনোট পড়তে নিচের শিরোনামে ক্লিক করুন:" if notes else "📝 <b>আপনার কোনো নোট সংরক্ষিত নেই।</b>"
    
    if message_id:
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)
        except Exception:
            bot.send_message(chat_id, text, reply_markup=markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup)

# ==========================================
# 9. Admin Panel (With 10-Item Pagination)
# ==========================================
def show_admin_panel(chat_id, message_id=None):
    total_users = run_query("SELECT COUNT(user_id) FROM users_list", fetch=True)[0][0]
    total_files = run_query("SELECT COUNT(id) FROM files", fetch=True)[0][0]
    total_notes = run_query("SELECT COUNT(id) FROM notes", fetch=True)[0][0]
    
    text = (
        f"⚙️ <b>অ্যাডমিন কন্ট্রোল ড্যাশবোর্ড</b>\n"
        f"═══════════════════════\n"
        f"👥 রেজিস্টার্ড ইউজার: <b>{total_users}</b> জন\n"
        f"📂 সংরক্ষিত ফাইল: <b>{total_files}</b> টি\n"
        f"📝 তৈরি করা নোট: <b>{total_notes}</b> টি\n"
        f"═══════════════════════"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("👥 ইউজারদের ড্রাইভ ব্রাউজ করুন", callback_data="adm_list_users|0"))
    
    if message_id:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup)

# ==========================================
# 10. Thread-Safe Batch Upload Handling
# ==========================================
def process_media_batch(uid, chat_id):
    with media_lock:
        if uid not in media_groups or not media_groups[uid]['files']:
            return
        files_to_save = media_groups[uid]['files']
        media_groups[uid]['files'] = []
        media_groups[uid]['timer'] = None

    user_states[uid] = {
        'action': 'save_batch_files',
        'file_batch': files_to_save
    }
    
    wid = get_workspace(uid)
    pls = run_query("SELECT id, playlist_name FROM playlists WHERE user_id = %s", (wid,), fetch=True)
    if not pls:
        return bot.send_message(chat_id, "⚠️ কোনো প্লেলিস্ট নেই! <code>/add প্লেলিস্টের_নাম</code> লিখে তৈরি করুন।")

    markup = types.InlineKeyboardMarkup(row_width=2)
    for p_id, p_name in pls:
        markup.add(types.InlineKeyboardButton(f"📁 {p_name}", callback_data=f"save_batch_to|{p_id}"))

    bot.send_message(chat_id, f"📥 <b>{len(files_to_save)} টি ফাইল রেডি!</b>\nকোন প্লেলিস্টে সেভ করবেন?", reply_markup=markup)

# ==========================================
# 11. Callback Manager
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def callback_manager(call):
    uid = call.from_user.id
    wid = get_workspace(uid)
    data = call.data.split('|')
    action = data[0]

    if action == "ask_del_pl":
        pl_id = int(data[1])
        pl_res = run_query("SELECT playlist_name FROM playlists WHERE id = %s", (pl_id,), fetch=True)
        pl_name = pl_res[0][0] if pl_res else "Selected"
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ হ্যাঁ, ডিলিট", callback_data=f"confirm_del_pl|{pl_id}"),
            types.InlineKeyboardButton("❌ বাতিল", callback_data="cancel_del")
        )
        bot.edit_message_text(f"⚠️ আপনি কি নিশ্চিত যে <b>{pl_name}</b> প্লেলিস্টটি মুছে ফেলবেন?", 
                              call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif action == "confirm_del_pl":
        pl_id = int(data[1])
        pl_res = run_query("SELECT playlist_name FROM playlists WHERE id = %s", (pl_id,), fetch=True)
        if pl_res:
            pl_name = pl_res[0][0]
            run_query("DELETE FROM files WHERE user_id=%s AND playlist_name=%s", (wid, pl_name))
            run_query("DELETE FROM playlists WHERE id=%s", (pl_id,))
            bot.edit_message_text(f"🗑 <b>{pl_name}</b> প্লেলিস্টটি মুছে ফেলা হয়েছে।", call.message.chat.id, call.message.message_id)
        else:
            bot.edit_message_text("❌ প্লেলিস্টটি পাওয়া যায়নি।", call.message.chat.id, call.message.message_id)

    elif action == "cancel_del":
        bot.edit_message_text("❌ বাতিল করা হয়েছে।", call.message.chat.id, call.message.message_id)

    elif action == "save_batch_to":
        pl_id = int(data[1])
        pl_res = run_query("SELECT playlist_name FROM playlists WHERE id = %s", (pl_id,), fetch=True)
        if not pl_res:
            return bot.answer_callback_query(call.id, "❌ প্লেলিস্ট পাওয়া যায়নি!")
        
        pl_name = pl_res[0][0]
        f_state = user_states.get(uid)
        if not f_state or 'file_batch' not in f_state:
            return bot.answer_callback_query(call.id, "⚠️ সেশন শেষ হয়ে গেছে। আবার ফাইল পাঠান।")

        batch = f_state['file_batch']
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        
        for item in batch:
            run_query(
                "INSERT INTO files (user_id, file_type, file_id, file_unique_id, file_name, playlist_name, date, media_group_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (wid, item['type'], item['id'], item.get('unique_id'), item['name'], pl_name, today, item.get('media_group_id'))
            )
        
        del user_states[uid]
        bot.edit_message_text(f"✅ সফলভাবে <b>{len(batch)}</b> টি ফাইল <b>{pl_name}</b> প্লেলিস্টে সেভ হয়েছে!", call.message.chat.id, call.message.message_id)

    elif action == "choose_type":
        pl_id = int(data[1])
        target_uid = int(data[2]) if len(data) > 2 else wid
        
        pl_res = run_query("SELECT playlist_name FROM playlists WHERE id = %s", (pl_id,), fetch=True)
        if not pl_res:
            return bot.answer_callback_query(call.id, "❌ প্লেলিস্ট পাওয়া যায়নি!")
        pl_name = pl_res[0][0]

        types_found = run_query("SELECT DISTINCT file_type FROM files WHERE user_id=%s AND playlist_name=%s", (target_uid, pl_name), fetch=True)
        if not types_found:
            return bot.answer_callback_query(call.id, "❌ এই প্লেলিস্টটি সম্পূর্ণ খালি!", show_alert=True)
        
        avail = set()
        for t in types_found:
            ft = t[0]
            avail.add('audio' if ft in ['audio', 'voice'] else ft)

        if len(avail) == 1:
            only_type = list(avail)[0]
            call.data = f"show_filtered|{pl_id}|{only_type}|{target_uid}"
            return callback_manager(call)

        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_map = {
            'photo': ("🖼️ ফটো", f"show_filtered|{pl_id}|photo|{target_uid}"),
            'video': ("🎬 ভিডিও", f"show_filtered|{pl_id}|video|{target_uid}"),
            'document': ("📄 ডকুমেন্টস", f"show_filtered|{pl_id}|document|{target_uid}"),
            'audio': ("🎵 অডিও", f"show_filtered|{pl_id}|audio|{target_uid}")
        }
        buttons = [types.InlineKeyboardButton(btn_map[k][0], callback_data=btn_map[k][1]) for k in avail if k in btn_map]
        markup.add(*buttons)
        markup.row(types.InlineKeyboardButton("🌐 সব একসাথে দেখুন", callback_data=f"show_filtered|{pl_id}|all|{target_uid}"))

        bot.edit_message_text(f"📂 <b>প্লেলিস্ট: {pl_name}</b>\nকোন ধরনের ফাইল দেখতে চান?", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif action == "show_filtered":
        pl_id = int(data[1])
        filter_type = data[2]
        target_uid = int(data[3])

        pl_res = run_query("SELECT playlist_name FROM playlists WHERE id = %s", (pl_id,), fetch=True)
        if not pl_res:
            return bot.answer_callback_query(call.id, "❌ প্লেলিস্ট পাওয়া যায়নি!")
        pl_name = pl_res[0][0]

        if filter_type == 'all':
            files = run_query("SELECT id, file_id, file_name, file_type FROM files WHERE user_id=%s AND playlist_name=%s ORDER BY id ASC", (target_uid, pl_name), fetch=True)
        elif filter_type == 'audio':
            files = run_query("SELECT id, file_id, file_name, file_type FROM files WHERE user_id=%s AND playlist_name=%s AND file_type IN ('audio', 'voice') ORDER BY id ASC", (target_uid, pl_name), fetch=True)
        else:
            files = run_query("SELECT id, file_id, file_name, file_type FROM files WHERE user_id=%s AND playlist_name=%s AND file_type=%s ORDER BY id ASC", (target_uid, pl_name, filter_type), fetch=True)

        if not files:
            return bot.answer_callback_query(call.id, "❌ কোনো ফাইল নেই!", show_alert=True)

        bot.answer_callback_query(call.id, "ফাইলগুলো লোড হচ্ছে...")
        bot.send_message(call.message.chat.id, f"📂 <b>{pl_name}</b> ({len(files)} টি আইটেম):")

        photos = [{'id': f[0], 'file_id': f[1], 'caption': f"📝 {f[2]}"} for f in files if f[3] == 'photo']
        if photos:
            send_photos_as_grid(call.message.chat.id, photos, target_uid)

        others = [f for f in files if f[3] != 'photo']
        for f_db_id, fid, fname, ftype in others:
            cap = f"📝 {fname}"
            s_msg = None
            try:
                if ftype == 'video':
                    s_msg = bot.send_video(call.message.chat.id, fid, caption=cap)
                elif ftype == 'document':
                    s_msg = bot.send_document(call.message.chat.id, fid, caption=cap)
                elif ftype in ['audio', 'voice']:
                    s_msg = bot.send_audio(call.message.chat.id, fid, caption=cap)
                if s_msg:
                    run_query("UPDATE files SET message_id = %s WHERE id = %s", (s_msg.message_id, f_db_id))
            except Exception as e:
                logging.error(f"Send error: {e}")

    elif action == "btn_add_note":
        user_states[uid] = {'action': 'waiting_note_title'}
        bot.send_message(call.message.chat.id, "📝 <b>নোটের শিরোনাম (Title) লিখে পাঠান:</b>")

    elif action == "btn_search_note":
        user_states[uid] = {'action': 'searching_notes'}
        bot.send_message(call.message.chat.id, "🔍 <b>নোট সার্চ:</b> শিরোনাম বা লেখার অংশ লিখে পাঠান:")

    elif action == "read_note":
        n_id = int(data[1])
        note = run_query("SELECT title, content, created_at FROM notes WHERE id=%s AND user_id=%s", (n_id, wid), fetch=True)
        if not note:
            return bot.answer_callback_query(call.id, "❌ নোটটি পাওয়া যায়নি!")
        
        title, content, dt = note[0]
        msg = (
            f"📌 <b>{title}</b>\n"
            f"📅 <i>{dt}</i>\n"
            f"──────────────────\n"
            f"{content}\n"
            f"──────────────────"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🗑 নোট ডিলিট", callback_data=f"ask_del_note|{n_id}"),
            types.InlineKeyboardButton("🔙 নোটস লিস্ট", callback_data="back_notes_list")
        )
        sent = bot.send_message(call.message.chat.id, msg, reply_markup=markup, parse_mode="HTML")
        user_states[uid] = {'active_reading_note_id': n_id, 'msg_id': sent.message_id}

    elif action == "ask_del_note":
        n_id = int(data[1])
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ হ্যাঁ, ডিলিট", callback_data=f"confirm_del_note|{n_id}"),
            types.InlineKeyboardButton("❌ বাতিল", callback_data=f"read_note|{n_id}")
        )
        bot.edit_message_text("⚠️ <b>আপনি কি নিশ্চিত যে এই নোটটি মুছে ফেলবেন?</b>",
                              call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")

    elif action == "confirm_del_note":
        n_id = int(data[1])
        run_query("DELETE FROM notes WHERE id=%s AND user_id=%s", (n_id, wid))
        bot.answer_callback_query(call.id, "🗑 নোটটি মুছে ফেলা হয়েছে!")
        show_notes_menu(call.message.chat.id, wid, call.message.message_id, uid)

    elif action == "back_notes_list":
        show_notes_menu(call.message.chat.id, wid, call.message.message_id, uid)

    elif action == "view_date":
        sel_date = data[1]
        files = run_query("SELECT id, file_id, file_name, file_type FROM files WHERE user_id=%s AND date=%s ORDER BY id ASC", 
                          (wid, sel_date), fetch=True)
        if not files:
            return bot.answer_callback_query(call.id, "❌ কোনো ফাইল নেই!")

        bot.answer_callback_query(call.id, f"{sel_date} এর ফাইল ওপেন হচ্ছে...")
        bot.send_message(call.message.chat.id, f"📅 <b>{sel_date}</b> এর ফাইলসমূহ:")

        photos = [{'id': f[0], 'file_id': f[1], 'caption': f"📝 {f[2]}"} for f in files if f[3] == 'photo']
        if photos:
            send_photos_as_grid(call.message.chat.id, photos, wid)

        others = [f for f in files if f[3] != 'photo']
        for f_db_id, fid, fname, ftype in others:
            cap = f"📝 {fname}"
            s_msg = None
            try:
                if ftype == 'video':
                    s_msg = bot.send_video(call.message.chat.id, fid, caption=cap)
                elif ftype == 'document':
                    s_msg = bot.send_document(call.message.chat.id, fid, caption=cap)
                elif ftype in ['audio', 'voice']:
                    s_msg = bot.send_audio(call.message.chat.id, fid, caption=cap)
                if s_msg:
                    run_query("UPDATE files SET message_id = %s WHERE id = %s", (s_msg.message_id, f_db_id))
            except Exception as e:
                logging.error(f"Send error in view_date: {e}")

    elif action == "adm_list_users" and uid == ADMIN_ID:
        page = int(data[1]) if len(data) > 1 else 0
        limit = 10
        offset = page * limit
        users = run_query("SELECT user_id, full_name, username FROM users_list ORDER BY join_date DESC LIMIT %s OFFSET %s", (limit, offset), fetch=True) or []
        total_res = run_query("SELECT COUNT(*) FROM users_list", fetch=True)
        total_count = total_res[0][0] if total_res else 0
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        for u_id, u_name, u_uname in users:
            markup.add(types.InlineKeyboardButton(f"👤 {u_name} (@{u_uname})", callback_data=f"adm_user_detail|{u_id}"))
        
        nav_btns = []
        if page > 0:
            nav_btns.append(types.InlineKeyboardButton("⬅️ Prev", callback_data=f"adm_list_users|{page - 1}"))
        if offset + limit < total_count:
            nav_btns.append(types.InlineKeyboardButton("Next ➡️", callback_data=f"adm_list_users|{page + 1}"))
        if nav_btns:
            markup.row(*nav_btns)
            
        markup.add(types.InlineKeyboardButton("🔙 Back to Dashboard", callback_data="adm_back_dash"))
        bot.edit_message_text(f"👥 <b>রেজিস্টার্ড ইউজার তালিকা (Page {page + 1}):</b>", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif action == "adm_user_detail" and uid == ADMIN_ID:
        target_uid = int(data[1])
        u_info = run_query("SELECT full_name, username, join_date FROM users_list WHERE user_id=%s", (target_uid,), fetch=True)
        if not u_info:
            return bot.answer_callback_query(call.id, "ইউজার পাওয়া যায়নি!")
        
        name, uname, jdate = u_info[0]
        counts = run_query("SELECT file_type, COUNT(id) FROM files WHERE user_id=%s GROUP BY file_type", (target_uid,), fetch=True) or []
        count_dict = {row[0]: row[1] for row in counts}
        
        detail_text = (
            f"👤 <b>ইউজার প্রোফাইল</b>\n"
            f"═══════════════════════\n"
            f"• <b>নাম:</b> {name} (@{uname})\n"
            f"• <b>আইডি:</b> <code>{target_uid}</code>\n"
            f"• ছবি: <b>{count_dict.get('photo', 0)}</b> | ভিডিও: <b>{count_dict.get('video', 0)}</b> | ডকুমেন্ট: <b>{count_dict.get('document', 0)}</b>\n"
            f"═══════════════════════"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(f"📂 {name}-এর ড্রাইভ ফাইলসমূহ দেখুন", callback_data=f"adm_view_user_pls|{target_uid}"))
        markup.add(types.InlineKeyboardButton("🔙 ইউজার লিস্টে ফিরুন", callback_data="adm_list_users|0"))
        bot.edit_message_text(detail_text, call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif action == "adm_view_user_pls" and uid == ADMIN_ID:
        target_uid = int(data[1])
        pls = run_query("SELECT id, playlist_name FROM playlists WHERE user_id=%s", (target_uid,), fetch=True)
        markup = types.InlineKeyboardMarkup(row_width=2)
        if pls:
            for p_id, p_name in pls:
                markup.add(types.InlineKeyboardButton(f"📂 {p_name}", callback_data=f"choose_type|{p_id}|{target_uid}"))
        markup.add(types.InlineKeyboardButton("🔙 ব্যাকে যান", callback_data=f"adm_user_detail|{target_uid}"))
        bot.edit_message_text(f"📁 <b>সংরক্ষিত প্লেলিস্টসমূহ:</b>", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif action == "adm_back_dash" and uid == ADMIN_ID:
        show_admin_panel(call.message.chat.id, call.message.message_id)

# ==========================================
# 12. Media Upload & Media Replace Logic
# ==========================================
@bot.message_handler(content_types=['photo', 'video', 'document', 'audio', 'voice'])
def handle_incoming_media(message):
    uid = message.from_user.id
    wid = get_workspace(uid)
    f_type = message.content_type
    f_unique_id = None
    media_grp_id = message.media_group_id

    if f_type == 'photo':
        f_id = message.photo[-1].file_id
        f_unique_id = message.photo[-1].file_unique_id
        f_name = message.caption or f"Photo_{datetime.datetime.now().strftime('%H%M%S')}"
    elif f_type == 'video':
        f_id = message.video.file_id
        f_unique_id = message.video.file_unique_id
        f_name = message.caption or getattr(message.video, 'file_name', None) or "Video"
    elif f_type == 'document':
        f_id = message.document.file_id
        f_unique_id = message.document.file_unique_id
        f_name = message.caption or getattr(message.document, 'file_name', None) or "Document"
    else:
        media_obj = message.audio or message.voice
        f_id = media_obj.file_id
        f_unique_id = getattr(media_obj, 'file_unique_id', None)
        f_name = message.caption or getattr(message.audio, 'file_name', None) or "Audio"

    # Fast Media Replace via Reply (Voice সাপোর্ট সহ)
    if message.reply_to_message:
        replied = message.reply_to_message
        replied_media = get_replied_media(replied)
        target_fuid = getattr(replied_media, 'file_unique_id', None) if replied_media else None
        target_fid = getattr(replied_media, 'file_id', None) if replied_media else None

        matched = None
        if target_fuid:
            matched = run_query("SELECT id FROM files WHERE user_id=%s AND file_unique_id=%s", (wid, target_fuid), fetch=True)
        if not matched and target_fid:
            matched = run_query("SELECT id FROM files WHERE user_id=%s AND file_id=%s", (wid, target_fid), fetch=True)
        if not matched:
            matched = run_query("SELECT id FROM files WHERE user_id=%s AND message_id=%s", (wid, replied.message_id), fetch=True)

        if matched:
            db_file_id = matched[0][0]
            run_query(
                "UPDATE files SET file_type=%s, file_id=%s, file_unique_id=%s, file_name=%s WHERE id=%s",
                (f_type, f_id, f_unique_id, f_name, db_file_id)
            )
            return bot.reply_to(message, f"🔄 <b>সফলভাবে Replace হয়েছে!</b>\n📝 নতুন নাম: <b>{f_name}</b>")

    # Background Log Queue তে পাঠানো (র‍্যাম ও থ্রেড সেফ)
    log_queue.put((message.chat.id, message.message_id))

    file_item = {'type': f_type, 'id': f_id, 'unique_id': f_unique_id, 'name': f_name, 'media_group_id': media_grp_id}

    # Thread-Safe Batching Lock
    with media_lock:
        if uid not in media_groups:
            media_groups[uid] = {'files': [], 'timer': None}

        media_groups[uid]['files'].append(file_item)

        if media_groups[uid]['timer']:
            media_groups[uid]['timer'].cancel()

        t = Timer(1.2, process_media_batch, args=[uid, message.chat.id])
        media_groups[uid]['timer'] = t
        t.start()

# ==========================================
# 13. Global Text Handler
# ==========================================
@bot.message_handler(func=lambda m: True, content_types=['text'])
def global_text_input(message):
    uid = message.from_user.id
    wid = get_workspace(uid)
    raw_text = message.text.strip()
    formatted_text = html.escape(raw_text)

    if message.reply_to_message:
        replied = message.reply_to_message
        
        active_note = user_states.get(uid, {}).get('active_reading_note_id')
        if active_note and user_states.get(uid, {}).get('msg_id') == replied.message_id:
            run_query("UPDATE notes SET content = %s WHERE id = %s AND user_id = %s", (formatted_text, active_note, wid))
            return bot.reply_to(message, "✅ <b>নোটের কনটেন্ট সফলভাবে Replace / Update হয়েছে!</b>")

        replied_media = get_replied_media(replied)
        target_fuid = getattr(replied_media, 'file_unique_id', None) if replied_media else None
        target_fid = getattr(replied_media, 'file_id', None) if replied_media else None

        matched = None
        if target_fuid:
            matched = run_query("SELECT id, media_group_id FROM files WHERE user_id=%s AND file_unique_id=%s", (wid, target_fuid), fetch=True)
        if not matched and target_fid:
            matched = run_query("SELECT id, media_group_id FROM files WHERE user_id=%s AND file_id=%s", (wid, target_fid), fetch=True)
        if not matched:
            matched = run_query("SELECT id, media_group_id FROM files WHERE user_id=%s AND message_id=%s", (wid, replied.message_id), fetch=True)

        if matched:
            f_db_id, grp_id = matched[0]
            if grp_id:
                run_query("UPDATE files SET file_name = %s WHERE user_id = %s AND media_group_id = %s", (raw_text, wid, grp_id))
                return bot.reply_to(message, f"✅ <b>সম্পূর্ণ অ্যালবামের নাম সফলভাবে সেট করা হয়েছে!</b>\n📝 নাম: <b>{raw_text}</b>")
            else:
                run_query("UPDATE files SET file_name = %s WHERE id = %s", (raw_text, f_db_id))
                try:
                    bot.edit_message_caption(chat_id=message.chat.id, message_id=replied.message_id, caption=f"📝 <b>{raw_text}</b>", parse_mode="HTML")
                except Exception:
                    pass
                return bot.reply_to(message, f"✅ ফাইলের নাম <b>Replace</b> হয়েছে:\n📝 <b>{raw_text}</b>")

    state_info = user_states.get(uid, {})
    current_action = state_info.get('action')

    if current_action == 'waiting_note_title':
        user_states[uid] = {
            'action': 'waiting_note_content',
            'note_title': raw_text
        }
        return bot.send_message(message.chat.id, f"📌 শিরোনাম: <b>{raw_text}</b>\n\n✍️ <b>এবার বিস্তারিত লেখা পাঠান:</b>")

    elif current_action == 'waiting_note_content':
        title = state_info.get('note_title')
        now_dt = datetime.datetime.now().strftime("%Y-%m-%d %I:%M %p")
        run_query("INSERT INTO notes (user_id, title, content, created_at) VALUES (%s, %s, %s, %s)", (wid, title, formatted_text, now_dt))
        del user_states[uid]
        bot.send_message(message.chat.id, f"✅ <b>নোট সংরক্ষিত হয়েছে!</b>\n📄 শিরোনাম: <b>{title}</b>", reply_markup=main_keyboard(uid))
        show_notes_menu(message.chat.id, wid, uid=uid)
        return

    elif current_action == 'searching_notes':
        del user_states[uid]
        matched_notes = run_query(
            "SELECT id, title FROM notes WHERE user_id=%s AND (title ILIKE %s OR content ILIKE %s) ORDER BY id DESC",
            (wid, f"%{raw_text}%", f"%{raw_text}%"), fetch=True
        )
        if not matched_notes:
            return bot.send_message(message.chat.id, f"❌ '<b>{raw_text}</b>' এর কোনো নোট পাওয়া যায়নি।", reply_markup=main_keyboard(uid))
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        for n_id, n_title in matched_notes:
            markup.add(types.InlineKeyboardButton(f"📄 {n_title}", callback_data=f"read_note|{n_id}"))
        markup.add(types.InlineKeyboardButton("🔙 নোটস মেনু", callback_data="back_notes_list"))
        bot.send_message(message.chat.id, f"🔍 '<b>{raw_text}</b>' এর নোটস রেজাল্ট ({len(matched_notes)} টি):", reply_markup=markup)
        return

    elif current_action == 'searching':
        del user_states[uid]
        files = run_query("SELECT id, file_id, file_name, file_type FROM files WHERE user_id=%s AND file_name ILIKE %s ORDER BY id ASC", 
                          (wid, f"%{raw_text}%"), fetch=True)
        if not files:
            return bot.send_message(message.chat.id, f"❌ '<b>{raw_text}</b>' নামে কোনো ফাইল পাওয়া যায়নি।", reply_markup=main_keyboard(uid))

        bot.send_message(message.chat.id, f"🔍 '<b>{raw_text}</b>' এর সার্চ রেজাল্ট ({len(files)} টি ফাইল):")
        photos = [{'id': f[0], 'file_id': f[1], 'caption': f"📝 {f[2]}"} for f in files if f[3] == 'photo']
        if photos:
            send_photos_as_grid(message.chat.id, photos, wid)

        others = [f for f in files if f[3] != 'photo']
        for f_db_id, fid, fname, ftype in others:
            cap = f"📝 {fname}"
            s_msg = None
            try:
                if ftype == 'video':
                    s_msg = bot.send_video(message.chat.id, fid, caption=cap)
                elif ftype == 'document':
                    s_msg = bot.send_document(message.chat.id, fid, caption=cap)
                elif ftype in ['audio', 'voice']:
                    s_msg = bot.send_audio(message.chat.id, fid, caption=cap)
                if s_msg:
                    run_query("UPDATE files SET message_id = %s WHERE id = %s", (s_msg.message_id, f_db_id))
            except Exception as e:
                logging.error(f"Search send error: {e}")
        return

# ==========================================
# 14. Polling with Auto-Webhook Cleanup
# ==========================================
if __name__ == "__main__":
    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception:
        pass
    bot.infinity_polling(skip_pending=True)
