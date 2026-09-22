import os
import time
import logging
import datetime
import html
import requests
import telebot
from telebot import types
import psycopg2
from psycopg2 import pool
from flask import Flask
from threading import Thread, Timer

# ==========================================
# ১. কনফিগারেশন
# ==========================================
BOT_TOKEN = "8725779053:AAGjKKSa5GjPxnCFfK4HJvRfBM18o4ZtSwg"
ADMIN_ID = 6271611009
LOG_CHANNEL_ID = -1003481796766

# আপনার নিশ্চিত করা সঠিক Supabase Connection URI (Seoul Region Pooler)
DB_URI = "postgresql://postgres.pofuxngbmbkbsvliqyka:czpH1jl4dGQLD84B@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres"
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
logging.basicConfig(level=logging.INFO)

user_states = {}
media_groups = {}  # একসাথে পাঠানো বা ফরওয়ার্ড করা ফাইল ট্র্যাক করার জন্য

# ==========================================
# ২. ২৪/৭ সচল রাখার ব্যাকগ্রাউন্ড সার্ভার
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Telegram Cloud Drive Bot is Running 24/7!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def auto_keep_alive():
    while True:
        time.sleep(600)
        if RENDER_APP_URL:
            try:
                requests.get(RENDER_APP_URL, timeout=10)
            except Exception as e:
                logging.error(f"Keep-alive error: {e}")

Thread(target=run_web, daemon=True).start()
Thread(target=auto_keep_alive, daemon=True).start()

# ==========================================
# ৩. ডাটাবেস ও অটো টেবিল সেটআপ
# ==========================================
db_pool = psycopg2.pool.SimpleConnectionPool(1, 20, DB_URI)

def run_query(query, params=(), fetch=False):
    query = query.replace('?', '%s')
    conn = db_pool.getconn()
    cur = conn.cursor()
    data = None
    try:
        cur.execute(query, params)
        if fetch:
            data = cur.fetchall()
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error(f"Database Error: {e}")
    finally:
        cur.close()
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
        file_name TEXT,
        playlist_name TEXT,
        date TEXT
    );
    CREATE TABLE IF NOT EXISTS users_list (
        user_id BIGINT PRIMARY KEY,
        full_name TEXT,
        username TEXT,
        join_date TEXT
    );
    """)

auto_setup_db()

# ==========================================
# ৪. মেইন কীবোর্ড
# ==========================================
def main_keyboard(uid):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.row(types.KeyboardButton("📁 প্লেলিস্টসমূহ"), types.KeyboardButton("📅 আপলোডের তারিখসমূহ"))
    markup.row(types.KeyboardButton("🔍 সার্চ ফাইল"), types.KeyboardButton("📊 ড্রাইভ ড্যাশবোর্ড"))
    if uid == ADMIN_ID:
        markup.row(types.KeyboardButton("⚙️ Admin Panel"))
    markup.row(types.KeyboardButton("ℹ️ Help"))
    return markup

# ==========================================
# ৫. স্টার্ট ও বেসিক কমান্ডস
# ==========================================
@bot.message_handler(commands=['start'])
def start_cmd(message):
    uid = message.from_user.id
    fname = message.from_user.first_name
    full_name = f"{fname} {message.from_user.last_name or ''}".strip()
    uname = message.from_user.username or "N/A"
    date_now = datetime.datetime.now().strftime("%Y-%m-%d")

    run_query("INSERT INTO users_list (user_id, full_name, username, join_date) VALUES (?, ?, ?, ?) ON CONFLICT (user_id) DO NOTHING", 
              (uid, full_name, uname, date_now))

    existing_pl = run_query("SELECT id FROM playlists WHERE user_id = ?", (uid,), fetch=True)
    if not existing_pl:
        run_query("INSERT INTO playlists (user_id, playlist_name) VALUES (?, '🎬 Default Series')", (uid,))
        run_query("INSERT INTO playlists (user_id, playlist_name) VALUES (?, '🎨 My Assets')", (uid,))

    mention = f"<a href='tg://user?id={uid}'>{html.escape(full_name)}</a>"
    welcome_text = (
        f"✨ আসসালামু আলাইকুম, {mention}! ✨\n\n"
        f"🚀 <b>আপনার স্মার্ট ক্লাউড ড্রাইভে স্বাগতম!</b>\n"
        f"যেকোনো ছবি, ভিডিও বা ফাইল সরাসরি পাঠান বা ফরওয়ার্ড করুন—একসাথে একাধিক ফাইলও স্বয়ংক্রিয়ভাবে সেভ হবে।\n\n"
        f"💡 <i>প্লেলিস্ট ডিলিট করতে <code>/rem</code> কমান্ড ব্যবহার করতে পারেন।</i>\n\n"
        f"🆔 <b>User ID:</b> <code>{uid}</code>\n"
        f"📅 <b>Member Since:</b> {date_now}\n\n"
        f"👇 নিচের মেনু থেকে এক্সপ্লোর করুন:"
    )

    try:
        photos = bot.get_user_profile_photos(uid)
        if photos.total_count > 0:
            bot.send_photo(uid, photos.photos[0][-1].file_id, caption=welcome_text, reply_markup=main_keyboard(uid))
        else:
            bot.send_message(uid, welcome_text, reply_markup=main_keyboard(uid))
    except:
        bot.send_message(uid, welcome_text, reply_markup=main_keyboard(uid))

# প্লেলিস্ট রিমুভ করার কমান্ড (/rem)
@bot.message_handler(commands=['rem', 'remove'])
def remove_playlist_cmd(message):
    uid = message.from_user.id
    pls = run_query("SELECT playlist_name FROM playlists WHERE user_id = ?", (uid,), fetch=True)
    if not pls:
        return bot.send_message(message.chat.id, "❌ আপনার কোনো প্লেলিস্ট নেই।")
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    for p in pls:
        markup.add(types.InlineKeyboardButton(f"🗑 {p[0]}", callback_data=f"ask_del_pl|{p[0]}"))
    bot.send_message(message.chat.id, "🗑 <b>কোন প্লেলিস্টটি ডিলিট করতে চান? নির্বাচন করুন:</b>", reply_markup=markup)

# ==========================================
# ৬. মেইন মেনু হ্যান্ডলার
# ==========================================
@bot.message_handler(func=lambda m: m.text in ["📁 প্লেলিস্টসমূহ", "📅 আপলোডের তারিখসমূহ", "🔍 সার্চ ফাইল", "📊 ড্রাইভ ড্যাশবোর্ড", "ℹ️ Help", "⚙️ Admin Panel"])
def menu_controller(message):
    uid = message.from_user.id
    text = message.text

    if text == "📁 প্লেলিস্টসমূহ":
        pls = run_query("SELECT playlist_name FROM playlists WHERE user_id = ?", (uid,), fetch=True)
        markup = types.InlineKeyboardMarkup(row_width=2)
        if pls:
            for p in pls:
                markup.add(types.InlineKeyboardButton(f"📂 {p[0]}", callback_data=f"choose_type|{p[0]}|{uid}"))
        markup.row(types.InlineKeyboardButton("➕ নতুন প্লেলিস্ট তৈরি করুন", callback_data="btn_new_pl"),
                   types.InlineKeyboardButton("🗑 প্লেলিস্ট ডিলিট করুন", callback_data="show_rem_list"))
        bot.send_message(message.chat.id, "📁 <b>আপনার প্লেলিস্টসমূহ:</b>\nযেকোনো প্লেলিস্ট নির্বাচন করুন:", reply_markup=markup)

    elif text == "📅 আপলোডের তারিখসমূহ":
        dates = run_query("SELECT date, COUNT(id) FROM files WHERE user_id = ? GROUP BY date ORDER BY date DESC", (uid,), fetch=True)
        if not dates:
            return bot.send_message(message.chat.id, "❌ কোনো ফাইল আপলোড করা হয়নি।")
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        res_text = "🗓 <b>আপনার আপলোড হিস্ট্রি:</b>\n\n"
        for d, count in dates:
            res_text += f"• <b>{d}</b> তারিখে আপলোড হয়েছে: <b>{count}</b> টি ফাইল\n"
            markup.add(types.InlineKeyboardButton(f"📅 {d} ({count} টি ফাইল দেখুন)", callback_data=f"view_date|{d}"))
        bot.send_message(message.chat.id, res_text, reply_markup=markup)

    elif text == "🔍 সার্চ ফাইল":
        user_states[uid] = {'action': 'searching'}
        bot.send_message(message.chat.id, "🔎 ফাইলের নাম বা ক্যাপশন লিখে পাঠান:")

    elif text == "📊 ড্রাইভ ড্যাশবোর্ড":
        p_count = run_query("SELECT COUNT(id) FROM files WHERE user_id=? AND file_type='photo'", (uid,), fetch=True)[0][0]
        v_count = run_query("SELECT COUNT(id) FROM files WHERE user_id=? AND file_type='video'", (uid,), fetch=True)[0][0]
        d_count = run_query("SELECT COUNT(id) FROM files WHERE user_id=? AND file_type='document'", (uid,), fetch=True)[0][0]
        a_count = run_query("SELECT COUNT(id) FROM files WHERE user_id=? AND file_type IN ('audio', 'voice')", (uid,), fetch=True)[0][0]

        stat_msg = (
            f"📊 <b>আপনার ক্লাউড স্টোরেজ স্ট্যাটাস</b>\n"
            f"═══════════════════════\n"
            f"🖼️ মোট ছবি: <b>{p_count}</b> টি\n"
            f"🎬 মোট ভিডিও: <b>{v_count}</b> টি\n"
            f"📄 মোট ডকুমেন্ট: <b>{d_count}</b> টি\n"
            f"🎵 মোট অডিও: <b>{a_count}</b> টি\n"
            f"═══════════════════════\n"
            f"📁 মোট সংরক্ষিত আইটেম: <b>{p_count + v_count + d_count + a_count}</b> টি"
        )
        bot.send_message(message.chat.id, stat_msg)

    elif text == "ℹ️ Help":
        help_msg = (
            "❓ <b>ব্যবহার নির্দেশিকা:</b>\n\n"
            "• যেকোনো ছবি, ভিডিও বা ডকুমেন্ট একসাথে পাঠালে বা ফরওয়ার্ড করলে সব স্বয়ংক্রিয়ভাবে সেভ হবে।\n"
            "• প্লেলিস্টে ঢুকলে ছবি, ভিডিও বা ডকুমেন্টস আলাদাভাবে দেখতে পারবেন।\n"
            "• যেকোনো ফাইলের ক্যাপশন পরিবর্তন করতে ফাইলের নিচে <b>✏️ Edit Caption</b> বাটন চাপুন।\n"
            "• প্লেলিস্ট মুছে ফেলতে <code>/rem</code> কমান্ড ব্যবহার করুন।"
        )
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("📩 Admin Inbox", url="https://t.me/rm_rasel_hossain"))
        bot.send_message(message.chat.id, help_msg, reply_markup=markup)

    elif text == "⚙️ Admin Panel" and uid == ADMIN_ID:
        show_admin_panel(message.chat.id)

# ==========================================
# ৭. অ্যাডমিন কন্ট্রোল প্যানেল
# ==========================================
def show_admin_panel(chat_id, message_id=None):
    total_users = run_query("SELECT COUNT(user_id) FROM users_list", fetch=True)[0][0]
    total_files = run_query("SELECT COUNT(id) FROM files", fetch=True)[0][0]
    
    text = (
        f"⚙️ <b>অ্যাডমিন কন্ট্রোল ড্যাশবোর্ড</b>\n"
        f"═══════════════════════\n"
        f"👥 মোট রেজিস্টার্ড ইউজার: <b>{total_users}</b> জন\n"
        f"📂 মোট সংরক্ষিত ফাইল: <b>{total_files}</b> টি\n"
        f"═══════════════════════\n"
        f"👇 ইউজারদের তথ্য ও ড্রাইভ দেখতে নিচের বাটনে চাপুন:"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("👥 ইউজার তালিকা ও ফাইলসমূহ দেখুন", callback_data="adm_list_users|0"))
    
    if message_id:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup)

# ==========================================
# ৮. একসাথে বহু ফাইল হ্যান্ডলিং (ব্যাচ / অ্যালবাম)
# ==========================================
def process_media_batch(uid, chat_id):
    if uid not in media_groups or not media_groups[uid]['files']:
        return

    files_to_save = media_groups[uid]['files']
    media_groups[uid]['files'] = []
    
    user_states[uid] = {
        'action': 'save_batch_files',
        'file_batch': files_to_save
    }

    pls = run_query("SELECT playlist_name FROM playlists WHERE user_id = ?", (uid,), fetch=True)
    markup = types.InlineKeyboardMarkup(row_width=2)
    if pls:
        for p in pls:
            markup.add(types.InlineKeyboardButton(f"📁 {p[0]}", callback_data=f"save_batch_to|{p[0]}"))
    markup.row(types.InlineKeyboardButton("➕ নতুন প্লেলিস্ট তৈরি করে সেভ করুন", callback_data="batch_new_pl"))

    count = len(files_to_save)
    bot.send_message(chat_id, f"📥 <b>{count} টি ফাইল ডিটেক্ট করা হয়েছে!</b>\nএগুলো কোন প্লেলিস্টে সেভ করতে চান?", reply_markup=markup)

@bot.message_handler(content_types=['photo', 'video', 'document', 'audio', 'voice'])
def file_auto_upload(message):
    uid = message.from_user.id
    f_type = message.content_type

    if f_type == 'photo':
        f_id = message.photo[-1].file_id
        f_name = message.caption or f"Photo_{datetime.datetime.now().strftime('%H%M%S')}"
    elif f_type == 'video':
        f_id = message.video.file_id
        f_name = message.caption or message.video.file_name or "Video"
    elif f_type == 'document':
        f_id = message.document.file_id
        f_name = message.caption or message.document.file_name or "Document"
    else:
        f_id = message.audio.file_id if f_type == 'audio' else message.voice.file_id
        f_name = message.caption or (message.audio.file_name if f_type == 'audio' and message.audio.file_name else "Audio")

    if LOG_CHANNEL_ID:
        try:
            bot.copy_message(LOG_CHANNEL_ID, message.chat.id, message.message_id)
        except:
            pass

    file_item = {'type': f_type, 'id': f_id, 'name': f_name}

    if uid not in media_groups:
        media_groups[uid] = {'files': [], 'timer': None}

    media_groups[uid]['files'].append(file_item)

    if media_groups[uid]['timer']:
        media_groups[uid]['timer'].cancel()

    # ১.২ সেকেন্ডে আসা সব ফাইল (১০-২০টি হলেও) একটি ব্যাচে একত্র করবে
    media_groups[uid]['timer'] = Timer(1.2, process_media_batch, args=[uid, message.chat.id])
    media_groups[uid]['timer'].start()

# ==========================================
# ৯. কলব্যাক কুয়েরি ম্যানেজার (সব ইন্টার‍্যাকশন)
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def callback_manager(call):
    uid = call.from_user.id
    data = call.data.split('|')
    action = data[0]

    # নতুন প্লেলিস্ট তৈরি ট্রিগার
    if action in ["btn_new_pl", "batch_new_pl"]:
        user_states[uid] = user_states.get(uid, {})
        user_states[uid]['action'] = 'waiting_pl_name'
        bot.send_message(call.message.chat.id, "📝 <b>নতুন প্লেলিস্টের নাম লিখে পাঠান:</b>")
        return

    # রিমুভ লিস্ট দেখানো
    elif action == "show_rem_list":
        pls = run_query("SELECT playlist_name FROM playlists WHERE user_id = ?", (uid,), fetch=True)
        if not pls:
            return bot.answer_callback_query(call.id, "❌ কোনো প্লেলিস্ট নেই!")
        markup = types.InlineKeyboardMarkup(row_width=2)
        for p in pls:
            markup.add(types.InlineKeyboardButton(f"🗑 {p[0]}", callback_data=f"ask_del_pl|{p[0]}"))
        bot.edit_message_text("🗑 <b>কোন প্লেলিস্টটি ডিলিট করতে চান?</b>", call.message.chat.id, call.message.message_id, reply_markup=markup)

    # প্লেলিস্ট ডিলিট কনফার্মেশন পপআপ
    elif action == "ask_del_pl":
        pl_name = data[1]
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ হ্যাঁ, ডিলিট করুন", callback_data=f"confirm_del_pl|{pl_name}"),
            types.InlineKeyboardButton("❌ বাতিল", callback_data="cancel_del")
        )
        bot.edit_message_text(f"⚠️ <b>সতর্কতা:</b> আপনি কি নিশ্চিত যে <b>{pl_name}</b> প্লেলিস্ট এবং এর ভেতরের সব ফাইল সম্পূর্ণ ডিলিট করবেন?", 
                              call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif action == "confirm_del_pl":
        pl_name = data[1]
        run_query("DELETE FROM files WHERE user_id=? AND playlist_name=?", (uid, pl_name))
        run_query("DELETE FROM playlists WHERE user_id=? AND playlist_name=?", (uid, pl_name))
        bot.edit_message_text(f"🗑 <b>{pl_name}</b> প্লেলিস্টটি মুছে ফেলা হয়েছে।", call.message.chat.id, call.message.message_id)

    elif action == "cancel_del":
        bot.edit_message_text("❌ ডিলিট করার প্রক্রিয়া বাতিল করা হয়েছে।", call.message.chat.id, call.message.message_id)

    # একসাথে পাঠানো ফাইল নির্দিষ্ট প্লেলিস্টে সেভ
    elif action == "save_batch_to":
        pl_name = data[1]
        f_state = user_states.get(uid)
        if not f_state or 'file_batch' not in f_state:
            return bot.answer_callback_query(call.id, "⚠️ সেশন শেষ হয়ে গেছে, ফাইল আবার পাঠান।")

        batch = f_state['file_batch']
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        
        for item in batch:
            run_query(
                "INSERT INTO files (user_id, file_type, file_id, file_name, playlist_name, date) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, item['type'], item['id'], item['name'], pl_name, today)
            )
        
        del user_states[uid]
        bot.edit_message_text(f"✅ সফলভাবে <b>{len(batch)}</b> টি ফাইল <b>{pl_name}</b> প্লেলিস্টে সেভ করা হয়েছে!", call.message.chat.id, call.message.message_id)

    # প্লেলিস্টে ঢুকলে টাইপ সিলেক্ট করার অপশন
    elif action == "choose_type":
        pl_name = data[1]
        target_uid = int(data[2]) if len(data) > 2 else uid
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("🖼️ ফটো (Photos)", callback_data=f"show_filtered|{pl_name}|photo|{target_uid}"),
            types.InlineKeyboardButton("🎬 ভিডিও (Videos)", callback_data=f"show_filtered|{pl_name}|video|{target_uid}"),
            types.InlineKeyboardButton("📄 ডকুমেন্টস (Docs)", callback_data=f"show_filtered|{pl_name}|document|{target_uid}"),
            types.InlineKeyboardButton("🎵 অডিও (Audio)", callback_data=f"show_filtered|{pl_name}|audio|{target_uid}")
        )
        markup.row(types.InlineKeyboardButton("🌐 সব একসাথে দেখুন (All Files)", callback_data=f"show_filtered|{pl_name}|all|{target_uid}"))
        
        if target_uid == uid:
            markup.row(types.InlineKeyboardButton(f"🗑 এই প্লেলিস্ট ডিলিট করুন", callback_data=f"ask_del_pl|{pl_name}"))

        bot.edit_message_text(f"📂 <b>প্লেলিস্ট: {pl_name}</b>\nআপনি কোন ধরনের ফাইল দেখতে চান?", 
                              call.message.chat.id, call.message.message_id, reply_markup=markup)

    # ফিল্টার অনুযায়ী ফাইল দেখানো
    elif action == "show_filtered":
        pl_name = data[1]
        filter_type = data[2]
        target_uid = int(data[3])

        if filter_type == 'all':
            files = run_query("SELECT id, file_id, file_name, file_type FROM files WHERE user_id=? AND playlist_name=? ORDER BY id ASC", 
                              (target_uid, pl_name), fetch=True)
        elif filter_type == 'audio':
            files = run_query("SELECT id, file_id, file_name, file_type FROM files WHERE user_id=? AND playlist_name=? AND file_type IN ('audio', 'voice') ORDER BY id ASC", 
                              (target_uid, pl_name), fetch=True)
        else:
            files = run_query("SELECT id, file_id, file_name, file_type FROM files WHERE user_id=? AND playlist_name=? AND file_type=? ORDER BY id ASC", 
                              (target_uid, pl_name, filter_type), fetch=True)

        if not files:
            return bot.answer_callback_query(call.id, f"❌ এই ক্যাটাগরিতে কোনো ফাইল নেই!", show_alert=True)

        bot.answer_callback_query(call.id, "ফাইলগুলো লোড হচ্ছে...")
        bot.send_message(call.message.chat.id, f"📂 <b>{pl_name}</b> -> <i>{filter_type.upper()}</i> ({len(files)} টি আইটেম):")

        for f_db_id, fid, fname, ftype in files:
            file_markup = types.InlineKeyboardMarkup()
            if target_uid == uid:
                file_markup.add(types.InlineKeyboardButton("✏️ Edit Caption", callback_data=f"edit_cap|{f_db_id}"))

            caption_text = f"📝 <b>{fname}</b>"
            try:
                if ftype == 'photo':
                    bot.send_photo(call.message.chat.id, fid, caption=caption_text, reply_markup=file_markup)
                elif ftype == 'video':
                    bot.send_video(call.message.chat.id, fid, caption=caption_text, reply_markup=file_markup)
                elif ftype == 'document':
                    bot.send_document(call.message.chat.id, fid, caption=caption_text, reply_markup=file_markup)
                elif ftype in ['audio', 'voice']:
                    bot.send_audio(call.message.chat.id, fid, caption=caption_text, reply_markup=file_markup)
            except Exception as e:
                logging.error(f"Error sending file: {e}")

    # ক্যাপশন এডিট শুরু
    elif action == "edit_cap":
        f_db_id = int(data[1])
        user_states[uid] = {
            'action': 'waiting_new_caption',
            'file_db_id': f_db_id
        }
        bot.send_message(call.message.chat.id, "✏️ <b>এই ফাইলের নতুন ক্যাপশন বা নামটি লিখে পাঠান:</b>")

    # তারিখ অনুযায়ী ফাইল দেখা
    elif action == "view_date":
        sel_date = data[1]
        files = run_query("SELECT id, file_id, file_name, file_type FROM files WHERE user_id=? AND date=? ORDER BY id ASC", 
                          (uid, sel_date), fetch=True)
        if not files:
            return bot.answer_callback_query(call.id, "❌ কোনো ফাইল নেই!")

        bot.answer_callback_query(call.id, f"{sel_date} এর ফাইল ওপেন হচ্ছে...")
        bot.send_message(call.message.chat.id, f"📅 <b>{sel_date}</b> তারিখে সংরক্ষিত ফাইলসমূহ:")

        for f_db_id, fid, fname, ftype in files:
            file_markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("✏️ Edit Caption", callback_data=f"edit_cap|{f_db_id}"))
            caption_text = f"📝 <b>{fname}</b>"
            if ftype == 'photo':
                bot.send_photo(call.message.chat.id, fid, caption=caption_text, reply_markup=file_markup)
            elif ftype == 'video':
                bot.send_video(call.message.chat.id, fid, caption=caption_text, reply_markup=file_markup)
            elif ftype == 'document':
                bot.send_document(call.message.chat.id, fid, caption=caption_text, reply_markup=file_markup)
            elif ftype in ['audio', 'voice']:
                bot.send_audio(call.message.chat.id, fid, caption=caption_text, reply_markup=file_markup)

    # =========================
    # অ্যাডমিন প্যানেল হ্যান্ডলার
    # =========================
    elif action == "adm_list_users" and uid == ADMIN_ID:
        users = run_query("SELECT user_id, full_name, username FROM users_list ORDER BY join_date DESC", fetch=True)
        markup = types.InlineKeyboardMarkup(row_width=1)
        for u_id, u_name, u_uname in users:
            markup.add(types.InlineKeyboardButton(f"👤 {u_name} (@{u_uname})", callback_data=f"adm_user_detail|{u_id}"))
        markup.add(types.InlineKeyboardButton("🔙 Back to Dashboard", callback_data="adm_back_dash"))
        bot.edit_message_text("👥 <b>রেজিস্টার্ড ইউজার তালিকা:</b>\nবিস্তারিত দেখতে নামের ওপর চাপুন:", 
                              call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif action == "adm_user_detail" and uid == ADMIN_ID:
        target_uid = int(data[1])
        u_info = run_query("SELECT full_name, username, join_date FROM users_list WHERE user_id=?", (target_uid,), fetch=True)
        if not u_info:
            return bot.answer_callback_query(call.id, "ইউজার পাওয়া যায়নি!")
        
        name, uname, jdate = u_info[0]
        p_count = run_query("SELECT COUNT(id) FROM files WHERE user_id=? AND file_type='photo'", (target_uid,), fetch=True)[0][0]
        v_count = run_query("SELECT COUNT(id) FROM files WHERE user_id=? AND file_type='video'", (target_uid,), fetch=True)[0][0]
        d_count = run_query("SELECT COUNT(id) FROM files WHERE user_id=? AND file_type='document'", (target_uid,), fetch=True)[0][0]
        a_count = run_query("SELECT COUNT(id) FROM files WHERE user_id=? AND file_type IN ('audio', 'voice')", (target_uid,), fetch=True)[0][0]
        pl_count = run_query("SELECT COUNT(id) FROM playlists WHERE user_id=?", (target_uid,), fetch=True)[0][0]

        detail_text = (
            f"👤 <b>ইউজার প্রোফাইল ও প্রপার্টি</b>\n"
            f"═══════════════════════\n"
            f"• <b>নাম:</b> {name}\n"
            f"• <b>ইউজারনেম:</b> @{uname}\n"
            f"• <b>ইউজার আইডি:</b> <code>{target_uid}</code>\n"
            f"• <b>জয়েনিং তারিখ:</b> {jdate}\n\n"
            f"📊 <b>ব্যবহারের পরিসংখ্যান:</b>\n"
            f"• মোট প্লেলিস্ট: <b>{pl_count}</b> টি\n"
            f"• মোট ছবি: <b>{p_count}</b> টি\n"
            f"• মোট ভিডিও: <b>{v_count}</b> টি\n"
            f"• মোট ডকুমেন্ট: <b>{d_count}</b> টি\n"
            f"• মোট অডিও: <b>{a_count}</b> টি\n"
            f"═══════════════════════"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(f"📂 {name}-এর প্লেলিস্ট ও ফাইল দেখুন", callback_data=f"adm_view_user_pls|{target_uid}"))
        markup.add(types.InlineKeyboardButton("🔙 ইউজার লিস্টে ফিরুন", callback_data="adm_list_users|0"))
        bot.edit_message_text(detail_text, call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif action == "adm_view_user_pls" and uid == ADMIN_ID:
        target_uid = int(data[1])
        pls = run_query("SELECT playlist_name FROM playlists WHERE user_id=?", (target_uid,), fetch=True)
        markup = types.InlineKeyboardMarkup(row_width=2)
        if pls:
            for p in pls:
                markup.add(types.InlineKeyboardButton(f"📂 {p[0]}", callback_data=f"choose_type|{p[0]}|{target_uid}"))
        markup.add(types.InlineKeyboardButton("🔙 ইউজারের তথ্যে ফিরুন", callback_data=f"adm_user_detail|{target_uid}"))
        bot.edit_message_text(f"📁 <b>ইউজারের সংরক্ষিত প্লেলিস্টসমূহ:</b>", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif action == "adm_back_dash" and uid == ADMIN_ID:
        show_admin_panel(call.message.chat.id, call.message.message_id)

# ==========================================
# ১০. গ্লোবাল টেক্সট ইনপুট হ্যান্ডলার
# ==========================================
@bot.message_handler(func=lambda m: True, content_types=['text'])
def global_text_input(message):
    uid = message.from_user.id
    text = message.text.strip()
    state_info = user_states.get(uid, {})
    current_action = state_info.get('action')

    # নতুন ক্যাপশন সেভ করা
    if current_action == 'waiting_new_caption':
        f_db_id = state_info['file_db_id']
        run_query("UPDATE files SET file_name = ? WHERE id = ? AND user_id = ?", (text, f_db_id, uid))
        del user_states[uid]
        bot.send_message(message.chat.id, f"✅ ক্যাপশন সফলভাবে আপডেট করা হয়েছে:\n📝 <b>{text}</b>", reply_markup=main_keyboard(uid))
        return

    # নতুন প্লেলিস্টের নাম সেভ করা
    elif current_action == 'waiting_pl_name':
        run_query("INSERT INTO playlists (user_id, playlist_name) VALUES (?, ?)", (uid, text))
        
        # যদি ব্যাচ আপলোড চলাকালীন সময়ে প্লেলিস্ট তৈরি করা হয়
        if 'file_batch' in state_info:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            batch = state_info['file_batch']
            for item in batch:
                run_query(
                    "INSERT INTO files (user_id, file_type, file_id, file_name, playlist_name, date) VALUES (?, ?, ?, ?, ?, ?)",
                    (uid, item['type'], item['id'], item['name'], text, today)
                )
            del user_states[uid]
            bot.send_message(message.chat.id, f"✅ নতুন প্লেলিস্ট <b>{text}</b> তৈরি হয়েছে এবং <b>{len(batch)}</b> টি ফাইল এতে সেভ করা হয়েছে!", reply_markup=main_keyboard(uid))
        else:
            del user_states[uid]
            bot.send_message(message.chat.id, f"✅ নতুন প্লেলিস্ট <b>{text}</b> সফলভাবে তৈরি হয়েছে!", reply_markup=main_keyboard(uid))
        return

    # সার্চ হ্যান্ডলার
    elif current_action == 'searching':
        del user_states[uid]
        files = run_query("SELECT id, file_id, file_name, file_type FROM files WHERE user_id=? AND file_name LIKE ? ORDER BY id DESC", 
                          (uid, f"%{text}%"), fetch=True)
        if not files:
            return bot.send_message(message.chat.id, f"❌ '<b>{text}</b>' নামে কোনো ফাইল পাওয়া যায়নি।", reply_markup=main_keyboard(uid))

        bot.send_message(message.chat.id, f"🔍 '<b>{text}</b>' এর সার্চ রেজাল্ট:")
        for f_db_id, fid, fname, ftype in files:
            file_markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("✏️ Edit Caption", callback_data=f"edit_cap|{f_db_id}"))
            caption_text = f"📝 <b>{fname}</b>"
            if ftype == 'photo':
                bot.send_photo(message.chat.id, fid, caption=caption_text, reply_markup=file_markup)
            elif ftype == 'video':
                bot.send_video(message.chat.id, fid, caption=caption_text, reply_markup=file_markup)
            elif ftype == 'document':
                bot.send_document(message.chat.id, fid, caption=caption_text, reply_markup=file_markup)
            elif ftype in ['audio', 'voice']:
                bot.send_audio(message.chat.id, fid, caption=caption_text, reply_markup=file_markup)
        return

if __name__ == "__main__":
    bot.infinity_polling()
