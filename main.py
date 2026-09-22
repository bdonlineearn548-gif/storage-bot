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

# Supabase Seoul Connection URI
DB_URI = "postgresql://postgres.pofuxngbmbkbsvliqyka:czpH1jl4dGQLD84B@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres"
RENDER_APP_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
logging.basicConfig(level=logging.INFO)

user_states = {}
media_groups = {}

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
        file_unique_id TEXT,
        file_name TEXT,
        playlist_name TEXT,
        date TEXT,
        message_id BIGINT
    );
    CREATE TABLE IF NOT EXISTS users_list (
        user_id BIGINT PRIMARY KEY,
        full_name TEXT,
        username TEXT,
        join_date TEXT
    );
    ALTER TABLE files ADD COLUMN IF NOT EXISTS file_unique_id TEXT;
    ALTER TABLE files ADD COLUMN IF NOT EXISTS message_id BIGINT;
    """)

auto_setup_db()

# ==========================================
# ৪. হেল্পার: ১০টি করে মিডিয়া গ্রুপ পাঠানো
# ==========================================
def send_photos_as_grid(chat_id, photo_list, uid):
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
                run_query("UPDATE files SET message_id = ? WHERE id = ?", (s_msg.message_id, item['id']))
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
# ৬. কমান্ড হ্যান্ডলারস
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
        f"🚀 <b>আপনার স্মার্ট ক্লাউড ড্রাইভে স্বাগতম!</b>\n\n"
        f"📌 <b>কমান্ড নির্দেশিকা:</b>\n"
        f"• নতুন প্লেলিস্ট তৈরি: <code>/add প্লেলিস্টের নাম</code>\n"
        f"• প্লেলিস্ট ডিলিট করতে: <code>/rem</code>\n"
        f"• ফাইলের নাম বা ক্যাপশন বদলাতে ওই ফটো/ভিডিওতে <b>Reply</b> দিয়ে নতুন নামটি লিখে পাঠান।\n\n"
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

@bot.message_handler(commands=['add', 'new'])
def add_playlist_cmd(message):
    uid = message.from_user.id
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(message, "⚠️ অনুগ্রহ করে প্লেলিস্টের নাম দিন।\nউদাহরণ: <code>/add My Tour</code>")
    
    pl_name = args[1].strip()
    exist = run_query("SELECT id FROM playlists WHERE user_id=? AND playlist_name=?", (uid, pl_name), fetch=True)
    if exist:
        return bot.reply_to(message, "⚠️ এই নামের প্লেলিস্ট ইতিমধ্যে রয়েছে!")
    
    run_query("INSERT INTO playlists (user_id, playlist_name) VALUES (?, ?)", (uid, pl_name))
    bot.reply_to(message, f"✅ <b>{pl_name}</b> প্লেলিস্টটি সফলভাবে তৈরি হয়েছে!")

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
# ৭. মেইন মেনু হ্যান্ডলার
# ==========================================
@bot.message_handler(func=lambda m: m.text in ["📁 প্লেলিস্টসমূহ", "📅 আপলোডের তারিখসমূহ", "🔍 সার্চ ফাইল", "📊 ড্রাইভ ড্যাশবোর্ড", "ℹ️ Help", "⚙️ Admin Panel"])
def menu_controller(message):
    uid = message.from_user.id
    text = message.text

    if text == "📁 প্লেলিস্টসমূহ":
        pls = run_query("SELECT playlist_name FROM playlists WHERE user_id = ?", (uid,), fetch=True)
        if not pls:
            return bot.send_message(message.chat.id, "❌ কোনো প্লেলিস্ট নেই। নতুন তৈরি করতে <code>/add নাম</code> লিখুন।")
        markup = types.InlineKeyboardMarkup(row_width=2)
        for p in pls:
            markup.add(types.InlineKeyboardButton(f"📂 {p[0]}", callback_data=f"choose_type|{p[0]}|{uid}"))
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
        help_msg = (
            "❓ <b>ব্যবহার নির্দেশিকা:</b>\n\n"
            "• নতুন প্লেলিস্ট তৈরি: <code>/add প্লেলিস্টের নাম</code>\n"
            "• প্লেলিস্ট মুছতে: <code>/rem</code>\n"
            "• যেকোনো ফটো/ভিডিওর নাম পরিবর্তন করতে ওই ছবিটিতে <b>Reply</b> দিয়ে নতুন নামটি লিখে দিন।\n"
            "• ছবিগুলো একসাথে ১০টি করে অ্যালবামে আসবে।"
        )
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("📩 Admin Inbox", url="https://t.me/rm_rasel_hossain"))
        bot.send_message(message.chat.id, help_msg, reply_markup=markup)

    elif text == "⚙️ Admin Panel" and uid == ADMIN_ID:
        show_admin_panel(message.chat.id)

# ==========================================
# ৮. অ্যাডমিন প্যানেল
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
# ৯. ব্যাচ / অ্যালবাম আপলোড সিস্টেম
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
    if not pls:
        return bot.send_message(chat_id, "⚠️ আপনার কোনো প্লেলিস্ট নেই! প্রথমে <code>/add প্লেলিস্টের_নাম</code> লিখে তৈরি করুন।")

    markup = types.InlineKeyboardMarkup(row_width=2)
    for p in pls:
        markup.add(types.InlineKeyboardButton(f"📁 {p[0]}", callback_data=f"save_batch_to|{p[0]}"))

    count = len(files_to_save)
    bot.send_message(chat_id, f"📥 <b>{count} টি ফাইল ডিটেক্ট করা হয়েছে!</b>\nকোন প্লেলিস্টে সেভ করতে চান?", reply_markup=markup)

@bot.message_handler(content_types=['photo', 'video', 'document', 'audio', 'voice'])
def file_auto_upload(message):
    uid = message.from_user.id
    f_type = message.content_type
    f_unique_id = None

    if f_type == 'photo':
        f_id = message.photo[-1].file_id
        f_unique_id = message.photo[-1].file_unique_id
        f_name = message.caption or f"Photo_{datetime.datetime.now().strftime('%H%M%S')}"
    elif f_type == 'video':
        f_id = message.video.file_id
        f_unique_id = message.video.file_unique_id
        f_name = message.caption or message.video.file_name or "Video"
    elif f_type == 'document':
        f_id = message.document.file_id
        f_unique_id = message.document.file_unique_id
        f_name = message.caption or message.document.file_name or "Document"
    else:
        f_id = message.audio.file_id if f_type == 'audio' else message.voice.file_id
        f_unique_id = getattr(message.audio or message.voice, 'file_unique_id', None)
        f_name = message.caption or (message.audio.file_name if f_type == 'audio' and message.audio.file_name else "Audio")

    if LOG_CHANNEL_ID:
        try:
            bot.copy_message(LOG_CHANNEL_ID, message.chat.id, message.message_id)
        except:
            pass

    file_item = {'type': f_type, 'id': f_id, 'unique_id': f_unique_id, 'name': f_name}

    if uid not in media_groups:
        media_groups[uid] = {'files': [], 'timer': None}

    media_groups[uid]['files'].append(file_item)

    if media_groups[uid]['timer']:
        media_groups[uid]['timer'].cancel()

    media_groups[uid]['timer'] = Timer(1.2, process_media_batch, args=[uid, message.chat.id])
    media_groups[uid]['timer'].start()

# ==========================================
# ১০. কলব্যাক ম্যানেজার (ডিলিট, অটো-টাইপ ও ভিউ)
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def callback_manager(call):
    uid = call.from_user.id
    data = call.data.split('|')
    action = data[0]

    # প্লেলিস্ট ডিলিট কনফার্মেশন
    if action == "ask_del_pl":
        pl_name = data[1]
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ হ্যাঁ, ডিলিট করুন", callback_data=f"confirm_del_pl|{pl_name}"),
            types.InlineKeyboardButton("❌ বাতিল", callback_data="cancel_del")
        )
        bot.edit_message_text(f"⚠️ আপনি কি নিশ্চিত যে <b>{pl_name}</b> প্লেলিস্ট ও এর সব ফাইল মুছে ফেলবেন?", 
                              call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif action == "confirm_del_pl":
        pl_name = data[1]
        run_query("DELETE FROM files WHERE user_id=? AND playlist_name=?", (uid, pl_name))
        run_query("DELETE FROM playlists WHERE user_id=? AND playlist_name=?", (uid, pl_name))
        bot.edit_message_text(f"🗑 <b>{pl_name}</b> প্লেলিস্টটি মুছে ফেলা হয়েছে।", call.message.chat.id, call.message.message_id)

    elif action == "cancel_del":
        bot.edit_message_text("❌ বাতিল করা হয়েছে।", call.message.chat.id, call.message.message_id)

    # ব্যাচ ফাইল সেভ
    elif action == "save_batch_to":
        pl_name = data[1]
        f_state = user_states.get(uid)
        if not f_state or 'file_batch' not in f_state:
            return bot.answer_callback_query(call.id, "⚠️ সেশন পাওয়া যায়নি।")

        batch = f_state['file_batch']
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        
        for item in batch:
            run_query(
                "INSERT INTO files (user_id, file_type, file_id, file_unique_id, file_name, playlist_name, date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uid, item['type'], item['id'], item.get('unique_id'), item['name'], pl_name, today)
            )
        
        del user_states[uid]
        bot.edit_message_text(f"✅ সফলভাবে <b>{len(batch)}</b> টি ফাইল <b>{pl_name}</b> প্লেলিস্টে সেভ হয়েছে!", call.message.chat.id, call.message.message_id)

    # স্মার্ট ক্যাটাগরি ও অটো-ডিটেকশন
    elif action == "choose_type":
        pl_name = data[1]
        target_uid = int(data[2]) if len(data) > 2 else uid
        
        types_found = run_query("SELECT DISTINCT file_type FROM files WHERE user_id=? AND playlist_name=?", (target_uid, pl_name), fetch=True)
        if not types_found:
            return bot.answer_callback_query(call.id, "❌ এই প্লেলিস্টটি সম্পূর্ণ খালি!", show_alert=True)
        
        avail = set()
        for t in types_found:
            ft = t[0]
            avail.add('audio' if ft in ['audio', 'voice'] else ft)

        if len(avail) == 1:
            only_type = list(avail)[0]
            call.data = f"show_filtered|{pl_name}|{only_type}|{target_uid}"
            return callback_manager(call)

        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_map = {
            'photo': ("🖼️ ফটো", f"show_filtered|{pl_name}|photo|{target_uid}"),
            'video': ("🎬 ভিডিও", f"show_filtered|{pl_name}|video|{target_uid}"),
            'document': ("📄 ডকুমেন্টস", f"show_filtered|{pl_name}|document|{target_uid}"),
            'audio': ("🎵 অডিও", f"show_filtered|{pl_name}|audio|{target_uid}")
        }
        buttons = [types.InlineKeyboardButton(btn_map[k][0], callback_data=btn_map[k][1]) for k in avail if k in btn_map]
        markup.add(*buttons)
        markup.row(types.InlineKeyboardButton("🌐 সব একসাথে দেখুন", callback_data=f"show_filtered|{pl_name}|all|{target_uid}"))

        bot.edit_message_text(f"📂 <b>প্লেলিস্ট: {pl_name}</b>\nকোন ধরনের ফাইল দেখতে চান?", call.message.chat.id, call.message.message_id, reply_markup=markup)

    # ফাইল গ্রিড ও অ্যালবাম ভিউ
    elif action == "show_filtered":
        pl_name = data[1]
        filter_type = data[2]
        target_uid = int(data[3])

        if filter_type == 'all':
            files = run_query("SELECT id, file_id, file_name, file_type FROM files WHERE user_id=? AND playlist_name=? ORDER BY id ASC", (target_uid, pl_name), fetch=True)
        elif filter_type == 'audio':
            files = run_query("SELECT id, file_id, file_name, file_type FROM files WHERE user_id=? AND playlist_name=? AND file_type IN ('audio', 'voice') ORDER BY id ASC", (target_uid, pl_name), fetch=True)
        else:
            files = run_query("SELECT id, file_id, file_name, file_type FROM files WHERE user_id=? AND playlist_name=? AND file_type=? ORDER BY id ASC", (target_uid, pl_name, filter_type), fetch=True)

        if not files:
            return bot.answer_callback_query(call.id, "❌ কোনো ফাইল নেই!", show_alert=True)

        bot.answer_callback_query(call.id, "ফাইলগুলো লোড হচ্ছে...")
        bot.send_message(call.message.chat.id, f"📂 <b>{pl_name}</b> ({len(files)} টি আইটেম):\n<i>(যেকোনো ফাইলের নাম বা ক্যাপশন বদলাতে সেটিতে Reply দিন)</i>")

        photos = [{'id': f[0], 'file_id': f[1], 'caption': f"📝 {f[2]}"} for f in files if f[3] == 'photo']
        if photos:
            send_photos_as_grid(call.message.chat.id, photos, target_uid)

        others = [f for f in files if f[3] != 'photo']
        for f_db_id, fid, fname, ftype in others:
            cap = f"📝 {fname}"
            try:
                if ftype == 'video':
                    s_msg = bot.send_video(call.message.chat.id, fid, caption=cap)
                elif ftype == 'document':
                    s_msg = bot.send_document(call.message.chat.id, fid, caption=cap)
                elif ftype in ['audio', 'voice']:
                    s_msg = bot.send_audio(call.message.chat.id, fid, caption=cap)
                run_query("UPDATE files SET message_id = ? WHERE id = ?", (s_msg.message_id, f_db_id))
            except Exception as e:
                logging.error(f"Send error: {e}")

    # তারিখ অনুযায়ী ফাইল দেখা
    elif action == "view_date":
        sel_date = data[1]
        files = run_query("SELECT id, file_id, file_name, file_type FROM files WHERE user_id=? AND date=? ORDER BY id ASC", 
                          (uid, sel_date), fetch=True)
        if not files:
            return bot.answer_callback_query(call.id, "❌ কোনো ফাইল নেই!")

        bot.answer_callback_query(call.id, f"{sel_date} এর ফাইল ওপেন হচ্ছে...")
        bot.send_message(call.message.chat.id, f"📅 <b>{sel_date}</b> তারিখে আপলোডকৃত ফাইলসমূহ:")

        photos = [{'id': f[0], 'file_id': f[1], 'caption': f"📝 {f[2]}"} for f in files if f[3] == 'photo']
        if photos:
            send_photos_as_grid(call.message.chat.id, photos, uid)

        others = [f for f in files if f[3] != 'photo']
        for f_db_id, fid, fname, ftype in others:
            cap = f"📝 {fname}"
            if ftype == 'video':
                s = bot.send_video(call.message.chat.id, fid, caption=cap)
            elif ftype == 'document':
                s = bot.send_document(call.message.chat.id, fid, caption=cap)
            elif ftype in ['audio', 'voice']:
                s = bot.send_audio(call.message.chat.id, fid, caption=cap)
            run_query("UPDATE files SET message_id = ? WHERE id = ?", (s.message_id, f_db_id))

    # অ্যাডমিন প্যানেল হ্যান্ডলার
    elif action == "adm_list_users" and uid == ADMIN_ID:
        users = run_query("SELECT user_id, full_name, username FROM users_list ORDER BY join_date DESC", fetch=True)
        markup = types.InlineKeyboardMarkup(row_width=1)
        for u_id, u_name, u_uname in users:
            markup.add(types.InlineKeyboardButton(f"👤 {u_name} (@{u_uname})", callback_data=f"adm_user_detail|{u_id}"))
        markup.add(types.InlineKeyboardButton("🔙 Back to Dashboard", callback_data="adm_back_dash"))
        bot.edit_message_text("👥 <b>রেজিস্টার্ড ইউজার তালিকা:</b>", call.message.chat.id, call.message.message_id, reply_markup=markup)

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
            f"📊 <b>পরিসংখ্যান:</b>\n"
            f"• প্লেলিস্ট: <b>{pl_count}</b> টি | ছবি: <b>{p_count}</b> টি\n"
            f"• ভিডিও: <b>{v_count}</b> টি | ডকুমেন্ট: <b>{d_count}</b> টি\n"
            f"═══════════════════════"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(f"📂 {name}-এর ফাইলসমূহ দেখুন", callback_data=f"adm_view_user_pls|{target_uid}"))
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
# ১১. রিপ্লাইয়ের মাধ্যমে ক্যাপশন এডিট ও সার্চ
# ==========================================
@bot.message_handler(func=lambda m: True, content_types=['text'])
def global_text_input(message):
    uid = message.from_user.id
    text = message.text.strip()

    # ১. ইউজার কোনো ফটো/ফাইলে Reply দিয়ে নতুন নাম পাঠালে
    if message.reply_to_message:
        replied = message.reply_to_message
        target_file_id = None
        target_unique_id = None

        if replied.photo:
            target_file_id = replied.photo[-1].file_id
            target_unique_id = replied.photo[-1].file_unique_id
        elif replied.video:
            target_file_id = replied.video.file_id
            target_unique_id = replied.video.file_unique_id
        elif replied.document:
            target_file_id = replied.document.file_id
            target_unique_id = replied.document.file_unique_id
        elif replied.audio:
            target_file_id = replied.audio.file_id
            target_unique_id = replied.audio.file_unique_id
        elif replied.voice:
            target_file_id = replied.voice.file_id
            target_unique_id = replied.voice.file_unique_id

        # ফাইল আইডি অথবা মেসেজ আইডি দিয়ে খোঁজা
        matched = None
        if target_unique_id:
            matched = run_query("SELECT id FROM files WHERE user_id=? AND file_unique_id=?", (uid, target_unique_id), fetch=True)
        if not matched and target_file_id:
            matched = run_query("SELECT id FROM files WHERE user_id=? AND file_id=?", (uid, target_file_id), fetch=True)
        if not matched:
            matched = run_query("SELECT id FROM files WHERE user_id=? AND message_id=?", (uid, replied.message_id), fetch=True)

        if matched:
            f_db_id = matched[0][0]
            run_query("UPDATE files SET file_name = ? WHERE id = ?", (text, f_db_id))
            return bot.reply_to(message, f"✅ এই ফাইলের নতুন ক্যাপশন সফলভাবে সেভ হয়েছে:\n📝 <b>{text}</b>")
        else:
            return bot.reply_to(message, "⚠️ এই ফাইলটি আপনার ড্রাইভে রেকর্ড হিসেবে খুঁজে পাওয়া যায়নি। অনুগ্রহ করে প্লেলিস্ট থেকে ওপেন করা ফাইলের মূল মেসেজে Reply দিন।")

    state_info = user_states.get(uid, {})
    current_action = state_info.get('action')

    # ২. সার্চ হ্যান্ডলার
    if current_action == 'searching':
        del user_states[uid]
        files = run_query("SELECT id, file_id, file_name, file_type FROM files WHERE user_id=? AND file_name ILIKE ? ORDER BY id DESC", 
                          (uid, f"%{text}%"), fetch=True)
        if not files:
            return bot.send_message(message.chat.id, f"❌ '<b>{text}</b>' নামে কোনো ফাইল পাওয়া যায়নি।", reply_markup=main_keyboard(uid))

        bot.send_message(message.chat.id, f"🔍 '<b>{text}</b>' এর সার্চ রেজাল্ট ({len(files)} টি ফাইল):")
        photos = [{'id': f[0], 'file_id': f[1], 'caption': f"📝 {f[2]}"} for f in files if f[3] == 'photo']
        if photos:
            send_photos_as_grid(message.chat.id, photos, uid)

        others = [f for f in files if f[3] != 'photo']
        for f_db_id, fid, fname, ftype in others:
            cap = f"📝 {fname}"
            if ftype == 'video':
                s = bot.send_video(message.chat.id, fid, caption=cap)
            elif ftype == 'document':
                s = bot.send_document(message.chat.id, fid, caption=cap)
            elif ftype in ['audio', 'voice']:
                s = bot.send_audio(message.chat.id, fid, caption=cap)
            run_query("UPDATE files SET message_id = ? WHERE id = ?", (s.message_id, f_db_id))
        return

if __name__ == "__main__":
    bot.infinity_polling()
