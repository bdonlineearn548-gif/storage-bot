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
from threading import Thread

# ==========================================
# ১. কনফিগারেশন
# ==========================================
BOT_TOKEN = "8725779053:AAGjKKSa5GjPxnCFfK4HJvRfBM18o4ZtSwg"
ADMIN_ID = 6271611009
LOG_CHANNEL_ID = -1003481796766

# আপনার Supabase এর আসল Connection URI টি এখানে দিন
# Supabase Database URI
DB_URI = "postgresql://postgres:czpH1jl4dGQLD84B@db.pofuxngbmbkbsvliqyka.supabase.co:5432/postgres"

# আপনার Render Web Service এর লাইভ লিংক (যেমন: https://your-app.onrender.com)
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
logging.basicConfig(level=logging.INFO)
user_states = {}

# ==========================================
# ২. ২৪/৭ সচল রাখার ব্যাকগ্রাউন্ড সার্ভার
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Telegram Bot is Running 24/7!"

def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# নিজে নিজেকে ১০ মিনিট পর পর কল করবে (স্লিপ বন্ধ করার ট্রিক)
def auto_keep_alive():
    while True:
        time.sleep(600)  # প্রতি ১০ মিনিট পর
        if RENDER_APP_URL:
            try:
                requests.get(RENDER_APP_URL, timeout=10)
                logging.info("Auto-pinged self to keep Render awake.")
            except Exception as e:
                logging.error(f"Keep-alive ping error: {e}")

# সার্ভার ও পিং চালু করা
Thread(target=run_web, daemon=True).start()
Thread(target=auto_keep_alive, daemon=True).start()

# ==========================================
# ৩. ডাটাবেস ও অটোমেটিক টেবিল তৈরি
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

# বট চালু হলেই টেবিল নিজে নিজেই তৈরি করে নেবে
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
# ৪. ছবি গ্রিড/অ্যালবাম ফাংশন
# ==========================================
def send_photos_as_grid(chat_id, photo_list):
    chunk_size = 10
    for i in range(0, len(photo_list), chunk_size):
        chunk = photo_list[i:i + chunk_size]
        media_group = []
        for idx, item in enumerate(chunk):
            cap = item['caption'] if idx == 0 else ""
            media_group.append(types.InputMediaPhoto(media=item['file_id'], caption=cap))
        try:
            bot.send_media_group(chat_id, media=media_group)
        except Exception as e:
            logging.error(f"Media group error: {e}")

# ==========================================
# ৫. মেইন কীবোর্ড
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
# ৬. বট হ্যান্ডলার ও ফাংশনসমূহ
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
        f"🚀 <b>আপনার স্মার্ট ডিজিটাল ড্রাইভে স্বাগতম!</b>\n"
        f"যেকোনো ছবি, ভিডিও বা ফাইল সরাসরি সেন্ড করুন, বট প্লেলিস্টে গুছিয়ে রাখবে।\n\n"
        f"🆔 <b>User ID:</b> <code>{uid}</code>\n"
        f"📅 <b>Member Since:</b> {date_now}\n\n"
        f"👇 নিচের মেনু ব্যবহার করুন:"
    )

    try:
        photos = bot.get_user_profile_photos(uid)
        if photos.total_count > 0:
            bot.send_photo(uid, photos.photos[0][-1].file_id, caption=welcome_text, reply_markup=main_keyboard(uid))
        else:
            bot.send_message(uid, welcome_text, reply_markup=main_keyboard(uid))
    except:
        bot.send_message(uid, welcome_text, reply_markup=main_keyboard(uid))

@bot.message_handler(func=lambda m: m.text in ["📁 প্লেলিস্টসমূহ", "📅 আপলোডের তারিখসমূহ", "🔍 সার্চ ফাইল", "📊 ড্রাইভ ড্যাশবোর্ড", "ℹ️ Help", "⚙️ Admin Panel"])
def menu_controller(message):
    uid = message.from_user.id
    text = message.text

    if text == "📁 প্লেলিস্টসমূহ":
        pls = run_query("SELECT playlist_name FROM playlists WHERE user_id = ?", (uid,), fetch=True)
        markup = types.InlineKeyboardMarkup(row_width=2)
        if pls:
            for p in pls:
                markup.add(types.InlineKeyboardButton(f"📂 {p[0]}", callback_data=f"show_pl|{p[0]}"))
        markup.row(types.InlineKeyboardButton("➕ নতুন প্লেলিস্ট বানান", callback_data="btn_new_pl"))
        bot.send_message(message.chat.id, "📁 <b>আপনার প্লেলিস্টসমূহ:</b>", reply_markup=markup)

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
        help_msg = "❓ <b>ব্যবহার নির্দেশিকা:</b>\n\n• ফাইল সরাসরি সেন্ড করলেই প্লেলিস্টে সেভ হবে।\n• তারিখ ও প্লেলিস্ট অনুযায়ী ফাইল বের করতে পারেন।"
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("📩 Admin Inbox", url="https://t.me/rm_rasel_hossain"))
        bot.send_message(message.chat.id, help_msg, reply_markup=markup)

    elif text == "⚙️ Admin Panel" and uid == ADMIN_ID:
        users = run_query("SELECT COUNT(user_id) FROM users_list", fetch=True)[0][0]
        bot.send_message(message.chat.id, f"🛠 <b>অ্যাডমিন প্যানেল</b>\nমোট ইউজার: {users}")

@bot.message_handler(content_types=['photo', 'video', 'document', 'audio', 'voice'])
def file_auto_upload(message):
    uid = message.from_user.id
    f_type = message.content_type

    if f_type == 'photo':
        f_id = message.photo[-1].file_id
        f_name = message.caption or f"Photo_{datetime.datetime.now().strftime('%H%M%S')}"
    elif f_type == 'video':
        f_id = message.video.file_id
        f_name = message.video.file_name or message.caption or "Video"
    elif f_type == 'document':
        f_id = message.document.file_id
        f_name = message.document.file_name or "Document"
    else:
        f_id = message.audio.file_id if f_type == 'audio' else message.voice.file_id
        f_name = message.audio.file_name if f_type == 'audio' and message.audio.file_name else "Audio"

    if LOG_CHANNEL_ID:
        try:
            bot.copy_message(LOG_CHANNEL_ID, message.chat.id, message.message_id)
        except:
            pass

    user_states[uid] = {
        'action': 'save_file',
        'file_id': f_id,
        'file_type': f_type,
        'file_name': f_name
    }

    pls = run_query("SELECT playlist_name FROM playlists WHERE user_id = ?", (uid,), fetch=True)
    markup = types.InlineKeyboardMarkup(row_width=2)
    if pls:
        for p in pls:
            markup.add(types.InlineKeyboardButton(f"📁 {p[0]}", callback_data=f"save_to|{p[0]}"))
    markup.row(types.InlineKeyboardButton("➕ নতুন প্লেলিস্ট তৈরি করুন", callback_data="create_pl_for_file"))

    bot.reply_to(message, f"📥 <b>{f_type.upper()} ডিটেক্ট করা হয়েছে!</b>\nকোন প্লেলিস্টে এটি সেভ করবেন?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_manager(call):
    uid = call.from_user.id
    data = call.data.split('|')
    action = data[0]

    if action in ["btn_new_pl", "create_pl_for_file"]:
        user_states[uid] = user_states.get(uid, {})
        user_states[uid]['action'] = 'waiting_pl_name'
        bot.send_message(call.message.chat.id, "📝 <b>নতুন প্লেলিস্টের নাম লিখে পাঠান:</b>")

    elif action == "save_to":
        pl_name = data[1]
        f_data = user_states.get(uid)
        if not f_data or 'file_id' not in f_data:
            return bot.answer_callback_query(call.id, "⚠️ সেশন শেষ হয়ে গেছে, আবার ফাইল পাঠান।")

        today = datetime.datetime.now().strftime("%Y-%m-%d")
        run_query(
            "INSERT INTO files (user_id, file_type, file_id, file_name, playlist_name, date) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, f_data['file_type'], f_data['file_id'], f_data['file_name'], pl_name, today)
        )
        del user_states[uid]
        bot.edit_message_text(f"✅ ফাইলটি সফলভাবে <b>{pl_name}</b> প্লেলিস্টে সংরক্ষিত হয়েছে!", call.message.chat.id, call.message.message_id)

    elif action == "show_pl":
        pl_name = data[1]
        files = run_query("SELECT file_id, file_name, file_type FROM files WHERE user_id=? AND playlist_name=? ORDER BY id ASC", 
                          (uid, pl_name), fetch=True)
        if not files:
            return bot.answer_callback_query(call.id, "❌ এই প্লেলিস্টটি খালি!")

        bot.answer_callback_query(call.id, "ফাইলগুলো নিয়ে আসা হচ্ছে...")
        bot.send_message(call.message.chat.id, f"📂 <b>প্লেলিস্ট: {pl_name}</b>")

        photos = [{'file_id': f[0], 'caption': f[1]} for f in files if f[2] == 'photo']
        if photos:
            send_photos_as_grid(call.message.chat.id, photos)

        others = [f for f in files if f[2] != 'photo']
        for fid, fname, ftype in others:
            if ftype == 'video':
                bot.send_video(call.message.chat.id, fid, caption=fname)
            elif ftype == 'document':
                bot.send_document(call.message.chat.id, fid, caption=fname)
            elif ftype in ['audio', 'voice']:
                bot.send_audio(call.message.chat.id, fid, caption=fname)

    elif action == "view_date":
        sel_date = data[1]
        files = run_query("SELECT file_id, file_name, file_type FROM files WHERE user_id=? AND date=? ORDER BY id ASC", 
                          (uid, sel_date), fetch=True)
        if not files:
            return bot.answer_callback_query(call.id, "❌ কোনো ফাইল নেই!")

        bot.answer_callback_query(call.id, f"{sel_date} এর ফাইল ওপেন হচ্ছে...")
        bot.send_message(call.message.chat.id, f"📅 <b>{sel_date}</b> তারিখে আপলোডকৃত ফাইলসমূহ:")

        photos = [{'file_id': f[0], 'caption': f[1]} for f in files if f[2] == 'photo']
        if photos:
            send_photos_as_grid(call.message.chat.id, photos)

        others = [f for f in files if f[2] != 'photo']
        for fid, fname, ftype in others:
            if ftype == 'video':
                bot.send_video(call.message.chat.id, fid, caption=fname)
            elif ftype == 'document':
                bot.send_document(call.message.chat.id, fid, caption=fname)
            elif ftype in ['audio', 'voice']:
                bot.send_audio(call.message.chat.id, fid, caption=fname)

@bot.message_handler(func=lambda m: True, content_types=['text'])
def global_text_input(message):
    uid = message.from_user.id
    text = message.text.strip()
    state_info = user_states.get(uid, {})
    current_action = state_info.get('action')

    if current_action == 'waiting_pl_name':
        run_query("INSERT INTO playlists (user_id, playlist_name) VALUES (?, ?)", (uid, text))
        if 'file_id' in state_info:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            run_query(
                "INSERT INTO files (user_id, file_type, file_id, file_name, playlist_name, date) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, state_info['file_type'], state_info['file_id'], state_info['file_name'], text, today)
            )
            del user_states[uid]
            bot.send_message(message.chat.id, f"✅ নতুন প্লেলিস্ট <b>{text}</b> তৈরি ও ফাইল সংরক্ষিত হয়েছে!", reply_markup=main_keyboard(uid))
        else:
            del user_states[uid]
            bot.send_message(message.chat.id, f"✅ নতুন প্লেলিস্ট <b>{text}</b> তৈরি সম্পন্ন!", reply_markup=main_keyboard(uid))

    elif current_action == 'searching':
        del user_states[uid]
        files = run_query("SELECT file_id, file_name, file_type FROM files WHERE user_id=? AND file_name LIKE ? ORDER BY id DESC", 
                          (uid, f"%{text}%"), fetch=True)
        if not files:
            return bot.send_message(message.chat.id, f"❌ '<b>{text}</b>' নামে কোনো ফাইল পাওয়া যায়নি।", reply_markup=main_keyboard(uid))

        bot.send_message(message.chat.id, f"🔍 '<b>{text}</b>' এর সার্চ রেজাল্ট:")
        photos = [{'file_id': f[0], 'caption': f[1]} for f in files if f[2] == 'photo']
        if photos:
            send_photos_as_grid(chat_id=message.chat.id, photo_list=photos)

        others = [f for f in files if f[2] != 'photo']
        for fid, fname, ftype in others:
            if ftype == 'video':
                bot.send_video(message.chat.id, fid, caption=fname)
            elif ftype == 'document':
                bot.send_document(message.chat.id, fid, caption=fname)
            elif ftype in ['audio', 'voice']:
                bot.send_audio(message.chat.id, fid, caption=fname)

if __name__ == "__main__":
    bot.infinity_polling()