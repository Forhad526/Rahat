import sqlite3
import logging
import requests
import asyncio
import aiohttp
from telegram import ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, CallbackQueryHandler, ConversationHandler
)

# --- CONFIG ---
BOT_TOKEN = "8679520507:AAGopkKUG1wN0GlxD8OYC4VqQ7wdmJlQkck"
# --- CONFIG ---
ADMIN_IDS = [8589946469, 8499714648]
CHANNEL_ID = "@TR_TECH_ZONE"
LOG_GROUP_ID = -1003878606545
ZINIPAY_API_TOKEN = "7e69e2a2412325671ac4e492afc994633d1b47c05b424f83" # Apnar Private Group ID
# logging setup (Error check korar jonno)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Conversation Stages
ADD_PROV, UPLOAD_PROXY, DEP_AMOUNT, DEP_SCREENSHOT, BROADCAST_STATE = range(5)

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('awm_proxy.db')
    cursor = conn.cursor()
    
    # ১. ইনভেন্টরি টেবিল (প্রক্সি স্টকের জন্য)
    cursor.execute('''CREATE TABLE IF NOT EXISTS inventory 
                (id INTEGER PRIMARY KEY, provider TEXT, gb TEXT, price REAL, data TEXT, status TEXT)''')
    
    # ২. প্রোভাইডার টেবিল
    cursor.execute('''CREATE TABLE IF NOT EXISTS providers (name TEXT PRIMARY KEY)''')
    
    # ৩. ইউজার ব্যালেন্স টেবিল
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0.0)''')
    
    # ৪. রিচার্জ হিস্টোরি টেবিল (সংশোধিত - invoice_id যোগ করা হয়েছে)
    # UNIQUE invoice_id ডুপ্লিকেট পেমেন্ট রুখতে ১০০% কার্যকর
    cursor.execute('''CREATE TABLE IF NOT EXISTS recharge_history 
                (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                 user_id INTEGER, 
                 amount_tk REAL, 
                 amount_usd REAL, 
                 status TEXT DEFAULT 'pending', 
                 invoice_id TEXT UNIQUE, 
                 timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
                
    conn.commit()
    conn.close()
    print("✅ Database Tables Initialized Successfully with Invoice ID support.")

async def check_payment_status(invoice_id, user_id, amount_usd, context):
    print(f"🌀 [VERIFY] Checking started for Invoice: {invoice_id}")
    
    verify_url = "https://api.zinipay.com/v1/payment/verify"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {
        "invoiceId": str(invoice_id),
        "apiKey": ZINIPAY_API_TOKEN
    }

    # ৫ মিনিট পর্যন্ত প্রতি ২ সেকেন্ড পর পর চেক করবে
    async with aiohttp.ClientSession() as session:
        for _ in range(150):
            await asyncio.sleep(2)
            try:
                async with session.post(verify_url, json=payload, headers=headers, timeout=10) as response:
                    if response.status == 200:
                        res_data = await response.json()
                        print(f"🔍 [DEBUG] API Response for {invoice_id}: {res_data}")

                        # আপনার পরামর্শ অনুযায়ী status এবং payment_status চেক
                        if res_data.get("status") is True:
                            data = res_data.get("data", {})
                            payment_status = data.get("payment_status") or data.get("status")

                            if payment_status == "COMPLETED":
                                # ১. ডুপ্লিকেট চেক (একই ইনভয়েস যেন দুইবার অ্যাড না হয়)
                                already_done = db_query("SELECT id FROM recharge_history WHERE invoice_id = ?", (invoice_id,), fetch=True)
                                if already_done:
                                    print(f"⚠️ [VERIFY] Invoice {invoice_id} was already processed.")
                                    return

                                # ২. ব্যালেন্স আপডেট (ইউজার না থাকলেও auto insert হবে)
                                db_query("""
                                INSERT INTO users (user_id, balance) 
                                VALUES (?, ?) 
                                ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?
                                """, (user_id, amount_usd, amount_usd))

                                # ৩. রিচার্জ হিস্টোরি সেভ
                                db_query("""
                                INSERT INTO recharge_history (user_id, amount_tk, amount_usd, status, invoice_id) 
                                VALUES (?, ?, ?, 'approved', ?)
                                """, (user_id, amount_usd * 127, amount_usd, invoice_id))

                                # ইউজারকে মেসেজ পাঠানো
                                await context.bot.send_message(
                                    chat_id=user_id,
                                    text=f"✅ **পেমেন্ট সফল!**\n\n💰 আপনার অ্যাকাউন্টে **${amount_usd:.2f}** যোগ করা হয়েছে।\nএখন আপনি প্রক্সি কিনতে পারবেন।"
                                )
                                print(f"💰 [SUCCESS] Added ${amount_usd} to User {user_id}")
                                return
                            
                            elif payment_status in ['FAILED', 'CANCELLED']:
                                await context.bot.send_message(chat_id=user_id, text="❌ আপনার পেমেন্টটি ব্যর্থ হয়েছে।")
                                return
            except Exception as e:
                print(f"⚠️ [VERIFY ERROR]: {str(e)}")
                continue

    print(f"⏰ [TIMEOUT] Verification ended for Invoice: {invoice_id}")

def db_query(query, params=(), fetch=False):
    conn = sqlite3.connect('awm_proxy.db')
    cursor = conn.cursor()
    cursor.execute(query, params)
    data = cursor.fetchall() if fetch else None
    conn.commit()
    conn.close()
    return data

# --- KEYBOARDS ---
def cancel_btn():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]])

async def show_main_menu(update: Update):
    user_id = update.effective_user.id
    buttons = [['🌐 Buy Proxy', '💳 My Balance'], ['⚡ Recharge', '📜 Purchase History'], ['👨‍💻 Contact Support']]
    if user_id in ADMIN_IDS: buttons.append(['⚙️ Admin Panel'])
    keyboard = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    text = "✨ **Welcome to AWM PROXY STORE!** ✨"
    if update.message: 
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode='Markdown')
    else: 
        await update.callback_query.message.reply_text(text, reply_markup=keyboard, parse_mode='Markdown')

# --- HANDLERS ---
async def is_user_joined(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in ADMIN_IDS: return True # অ্যাডমিনদের চেক করার দরকার নেই
    
    try:
        # CHANNEL_ID = "@TR_TECH_ZONE" আপনার কোডে আগে থেকেই আছে
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']:
            return True
    except Exception as e:
        print(f"Force Join Error: {e}")
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    init_db()
    
    # ইউজার আইডি সেভ করা (আগে না থাকলে)
    db_query("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0.0)", (user_id,))
    
    # --- পেমেন্ট সাকসেস চেক (Redirect Handler) ---
    # যদি ইউজার পেমেন্ট শেষে ফিরে আসে তবে ইউআরএল-এ ?start=success থাকে
    if context.args and "success" in context.args:
        # ব্যালেন্স অ্যাড হওয়া ব্যাকগ্রাউন্ডে check_payment_status দিয়ে হচ্ছে
        success_msg = (
            "✅ **পেমেন্ট ভেরিফিকেশন চলছে!**\n\n"
            "আপনার পেমেন্টটি সফলভাবে সম্পন্ন হয়েছে। সিস্টেম অটোমেটিক চেক করে "
            "১-১০ সেকেন্ডের মধ্যে আপনার ব্যালেন্স অ্যাড করে দিবে। অনুগ্রহ করে অপেক্ষা করুন।"
        )
        await update.message.reply_text(success_msg, parse_mode='Markdown')
        # এখানে রিটার্ন দিচ্ছি না কারণ এরপর মেইন কিবোর্ড দেখানো দরকার
    
    # --- Force Join Check ---
    joined = await is_user_joined(update, context)
    if not joined:
        keyboard = [
            [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}")],
            [InlineKeyboardButton("✅ Joined", callback_data="check_join_btn")]
        ]
        await update.message.reply_text(
            "❌ **অ্যাক্সেস ডিনাইড!**\n\nবটটি ব্যবহার করতে আপনাকে অবশ্যই আমাদের চ্যানেলে জয়েন থাকতে হবে। জয়েন করে নিচের 'Joined' বাটনে ক্লিক করুন।",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return

    # --- Keyboard Selection ---
    user_keyboard = [
        ['🌐 Buy Proxy', '⚡ Recharge'],
        ['💳 My Balance', '📜 Purchase History'],
        ['👨‍💻 Contact Support']
    ]
    
    if user_id in ADMIN_IDS:
        user_keyboard.append(['⚙️ Admin Panel'])
    
    reply_markup = ReplyKeyboardMarkup(user_keyboard, resize_keyboard=True)
    
    welcome_text = (
        f"👋 আসসালামু আলাইকুম, {update.effective_user.first_name}!\n\n"
        "🚀 **AWM Proxy Bot**-এ আপনাকে স্বাগতম।\n\n"
        "নিচের বাটনগুলো ব্যবহার করে আপনার প্রয়োজনীয় সার্ভিস বেছে নিন।"
    )
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if await is_user_joined(update, context):
        await query.message.delete() # জয়েন মেসেজ মুছে দিবে
        await start(update, context) # পুনরায় স্টার্ট মেনু দিবে
    else:
        await query.answer("⚠️ আপনি এখনো জয়েন করেননি! আগে জয়েন করুন।", show_alert=True)

async def admin_panel_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    keyboard = [
        [InlineKeyboardButton("➕ Add Provider", callback_data="admin_add_prov"), 
         InlineKeyboardButton("📦 View Stock", callback_data="admin_view_stock")],
        [InlineKeyboardButton("👥 All Users", callback_data="admin_all_users"), 
         InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("💾 Backup Database", callback_data="admin_backup_db")]
    ]
    await update.message.reply_text("🛠 **Admin Control Panel:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def save_prov(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.startswith('(') and text.endswith(')'):
        name = text.replace('(', '').replace(')', '').upper()
        db_query("INSERT OR IGNORE INTO providers (name) VALUES (?)", (name,))
        await update.message.reply_text(f"✅ Provider **{name}** added successfully!", parse_mode='Markdown')
        await show_main_menu(update)
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ **Format Error!**\nYou must use brackets. Example: `(ABC)`\nNiche Cancel e click korun.", reply_markup=cancel_btn(), parse_mode='Markdown')
        return ADD_PROV

# --- 2. AVAILABLE PROXY LIST (ADMIN) ---
async def view_admin_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    provs = db_query("SELECT name FROM providers", fetch=True)
    if not provs:
        await update.callback_query.edit_message_text("No providers found!", reply_markup=cancel_btn())
        return
    
    keyboard = []
    for p in provs:
        name = p[0]
        stocks = db_query("SELECT gb, price, COUNT(*) FROM inventory WHERE provider=? AND status='available' GROUP BY gb, price", (name,), fetch=True)
        if not stocks:
            keyboard.append([InlineKeyboardButton(f"{name} PROXY (0)", callback_data=f"manage_{name}")])
        else:
            for s in stocks:
                btn_text = f"{name} PROXY {s[0]} ${s[1]} ({s[2]})"
                keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"manage_{name}")])

    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="cancel_action")])
    await update.callback_query.edit_message_text("🎯 **Select Package to Manage:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def manage_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    prov = update.callback_query.data.split("_")[1]
    context.user_data['current_prov'] = prov
    keyboard = [
        [InlineKeyboardButton("📥 Upload Proxy (Text/File)", callback_data=f"up_btn_{prov}")],
        [InlineKeyboardButton("🔥 Delete Provider", callback_data=f"del_prov_{prov}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_view_stock")]
    ]
    await update.callback_query.edit_message_text(f"💎 **Provider:** {prov}\nSelect an action:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# --- 3. PROXY UPLOAD (TEXT/FILE) ---
async def start_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    prov = update.callback_query.data.split("_")[2]
    await update.callback_query.edit_message_text(f"📤 **Upload for {prov}**\n\nFirst, send format: `GB | Price`\nExample: `1GB | 1.0`", reply_markup=cancel_btn(), parse_mode='Markdown')
    return UPLOAD_PROXY

async def save_proxy_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_text = update.message.text if update.message.text else ""
    
    if '|' in msg_text:
        try:
            parts = msg_text.split('|')
            gb = parts[0].strip()
            price = parts[1].strip().replace('$', '')
            context.user_data['up_gb'] = gb
            context.user_data['up_price'] = price
            await update.message.reply_text(f"✅ **Format Set!**\nPackage: {gb}\nPrice: ${price}\n\nNow send the **Proxy List** or upload a `.txt` file.", reply_markup=cancel_btn(), parse_mode='Markdown')
            return UPLOAD_PROXY
        except:
            await update.message.reply_text("❌ Invalid Format! Use: `GB | Price`", reply_markup=cancel_btn())
            return UPLOAD_PROXY

    gb = context.user_data.get('up_gb')
    price = context.user_data.get('up_price')
    prov = context.user_data.get('current_prov')

    if not gb or not price:
        await update.message.reply_text("❌ Send format first! Example: `1GB | 1.0`", reply_markup=cancel_btn())
        return UPLOAD_PROXY

    proxy_list = []
    if update.message.document:
        file = await context.bot.get_file(update.message.document.file_id)
        content = await file.download_as_bytearray()
        proxy_list = content.decode('utf-8').splitlines()
    else:
        proxy_list = msg_text.splitlines()

    count = 0
    for p in proxy_list:
        p = p.strip()
        if p:
            try:
                db_query("INSERT INTO inventory (provider, gb, price, data, status) VALUES (?,?,?,?,'available')",
                         (prov, gb, float(price), p))
                count += 1
            except: continue
            
    await update.message.reply_text(f"✅ **Added Successful {count} pic proxy** to {prov} {gb} package!")
    await show_main_menu(update)
    return ConversationHandler.END

# --- 4. DELETE PROVIDER ---
async def delete_provider_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    prov = update.callback_query.data.split("_")[2]
    db_query("DELETE FROM providers WHERE name=?", (prov,))
    db_query("DELETE FROM inventory WHERE provider=?", (prov,))
    await update.callback_query.answer(f"🔥 {prov} Deleted!", show_alert=True)
    await view_admin_stock(update, context)

# --- 5. USER BUY PROXY LIST ---
# --- USER BUY PROXY LIST ---
async def buy_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = """
        SELECT provider, gb, price, COUNT(*) 
        FROM inventory 
        WHERE status='available' 
        GROUP BY provider, gb, price
    """
    stocks = db_query(query, fetch=True)
    
    if not stocks:
        await update.message.reply_text("❌ বর্তমানে কোনো প্রক্সি স্টকে নেই।")
        return

    keyboard = []
    for s in stocks:
        provider, gb, price, count = s
        btn_text = f"{provider} PROXY {gb} ${price} LIFT ({count})"
        # এখানে prebuy_ যোগ করা হয়েছে কনফার্মেশন টেবিলের জন্য
        callback_data = f"prebuy_{provider}_{gb}_{price}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=callback_data)])
    
    await update.message.reply_text("🎯 **একটি প্যাকেজ নির্বাচন করুন:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
# --- PRE-BUY CONFIRMATION TABLE ---
async def pre_buy_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, prov, gb, price = query.data.split('_')
    
    table_text = (
        f"💰 **AWM PROXY STORE (BOT)**\n"
        f"Unlimited Validity\n"
        f"মেয়াদ আনলিমিটেড\n\n"
        f"**Prices:**\n"
        f"• {prov} {gb}: ${float(price):.2f}\n\n"
        f"❤️ আপনি কি প্রক্সি টা কিনবেন? যদি শিউর হন তাহলে নিচে **Confirm Buy** বাটন এ ক্লিক করুন "
        f"আর যদি কিনতে না চান তাহলে **Cancel** এ ক্লিক করুন 😍"
    )
    keyboard = [
        [InlineKeyboardButton("✅ Confirm Buy", callback_data=f"confirm_{prov}_{gb}_{price}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_buy")]
    ]
    await query.edit_message_text(table_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# --- EXECUTE PURCHASE (AUTO DELIVERY) ---
# --- EXECUTE PURCHASE (WITH BALANCE CHECK) ---
async def execute_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    _, prov, gb, price = query.data.split('_')
    price = float(price)

    # User balance check database theke
    user_data = db_query("SELECT balance FROM users WHERE user_id=?", (user_id,), fetch=True)
    current_balance = user_data[0][0] if user_data else 0.0

    if current_balance < price:
        await query.edit_message_text(
            f"❌ **Insufficient Balance!**\n\n"
            f"Apnar account-e porjapto balance nei.\n"
            f"Proyojon: ${price:.2f}\n"
            f"Current Balance: ${current_balance:.2f}\n\n"
            "Anugroho kore recharge kore abar chesta korun।",
            parse_mode='Markdown'
        )
        return

    # Stock theke proxy khunja
    proxy_data = db_query("SELECT id, data FROM inventory WHERE provider=? AND gb=? AND status='available' LIMIT 1", (prov, gb), fetch=True)
    
    if not proxy_data:
        await query.edit_message_text("❌ Dukhhito! Ei proxy-ti stock out hoye geche।")
        return

    p_id, raw_data = proxy_data[0]
    
    try:
        parts = raw_data.split(':')
        server, port, user, pwd = parts[0], parts[1], parts[2], parts[3]
        
        # Balance kete neya
        new_balance = current_balance - price
        db_query("UPDATE users SET balance=? WHERE user_id=?", (new_balance, user_id))
        db_query("UPDATE inventory SET status='sold' WHERE id=?", (p_id,))
        
        delivery_text = (
            f"✅ **Your-account created!**\n\n"
            f"**Proxy Name:** {prov} Proxy\n"
            f"**Protocol:** HTTP\n"
            f"**Server:** `{server}`\n"
            f"**Port:** `{port}`\n"
            f"**User:** `{user}`\n"
            f"**Password:** `{pwd}`\n\n"
            f"💰 **Baki Balance:** ${new_balance:.2f}"
        )
        await query.edit_message_text(delivery_text, parse_mode='Markdown')
    except:
        await query.edit_message_text("❌ Proxy data format bhul (IP:Port:User:Pass hote hobe)।")

async def cancel_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("❌ আপনার প্রক্সি কেনার প্রসেসটি বাতিল করা হয়েছে।")
               
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Action Cancelled.")
    return ConversationHandler.END

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # ডাটাবেস থেকে ব্যালেন্স আনা
    user_data = db_query("SELECT balance FROM users WHERE user_id=?", (user_id,), fetch=True)
    
    # যদি ডাটাবেসে ইউজার না থাকে তবে ০.০ দেখাবে
    bal = user_data[0][0] if user_data and len(user_data) > 0 else 0.0
    
    await update.message.reply_text(
        f"💰 **আপনার বর্তমান ব্যালেন্স:** ${bal:.2f}\n\n"
        f"আপনার ইউজার আইডি: `{user_id}`", 
        parse_mode='Markdown'
    )

async def contact_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    support_text = (
        "🫂 <b>আমাদের বটে আপনার যে কোনো সমস্যা হলে সরাসরি যোগাযোগ করুন নিচে দেওয়া আইডি তে ধন্যবাদ ☺️</b>\n\n"
        "👑 <b>Owner id</b> :- @Awm_Owner\n\n"
        "🧑‍💻 <b>Admin id</b> :- @Awm_Admin_1\n\n"
        "🧑‍💻 <b>Admin id</b> :- @azmainex3"
    )
    # এখানে parse_mode='HTML' ব্যবহার করা হয়েছে যাতে আন্ডারস্কোর থাকলেও সমস্যা না হয়
    await update.message.reply_text(support_text, parse_mode='HTML')

async def purchase_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # রিচার্জ বা ডিপোজিট ইতিহাস আনা
    deposit_data = db_query("SELECT amount_tk, amount_usd, timestamp FROM recharge_history WHERE user_id=? AND status='approved'", (user_id,), fetch=True)
    
    # প্রক্সি কেনার ইতিহাস আনা (status কলামে ইউজারের আইডি সেভ থাকলে এটি কাজ করবে)
    # আপনার ইনভেন্টরি টেবিলে timestamp নেই, তাই আমরা সেটি বাদ দিয়েছি
    user_buys = db_query("SELECT provider, gb FROM inventory WHERE status=?", (f"sold_to_{user_id}",), fetch=True)
    
    history_text = "📜 **আপনার লেনদেনের ইতিহাস**\n"
    history_text += "⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    
    # ডিপোজিট সেকশন
    history_text += "💰 **ডিপোজিট ইতিহাস:**\n"
    if not deposit_data:
        history_text += "∟ কোনো ডিপোজিট পাওয়া যায়নি।\n"
    else:
        total_tk = 0
        total_usd = 0
        for row in deposit_data:
            tk, usd, dt = row
            total_tk += tk
            total_usd += usd
            history_text += f"✅ {tk}৳ (${usd:.2f}) - {dt[:10]}\n"
        history_text += f"\n📊 **মোট ডিপোজিট:** {total_tk}৳ (${total_usd:.2f})\n"
    
    history_text += "\n" + "⎯" * 15 + "\n"
    
    # প্রক্সি কেনা সেকশন
    history_text += "🛒 **প্রক্সি কেনার ইতিহাস:**\n"
    if not user_buys:
        history_text += "∟ আপনি এখনো কোনো প্রক্সি কিনেননি।\n"
    else:
        for i, buy in enumerate(user_buys, 1):
            prov, gb = buy
            history_text += f"{i}. {prov} - {gb} GB\n"
            
    await update.message.reply_text(history_text, parse_mode='Markdown')


# --- RECHARGE FLOW ---
async def start_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    # পেমেন্ট মেথড বাটনগুলো
    keyboard = [
        [InlineKeyboardButton("1️⃣ Bkash", callback_data="pay_bkash"), 
         InlineKeyboardButton("2️⃣ Nagad", callback_data="pay_nagad")],
        [InlineKeyboardButton("3️⃣ Rocket", callback_data="pay_rocket"), 
         InlineKeyboardButton("4️⃣ Binance", callback_data="pay_binance")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_recharge")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "💳 **অনুগ্রহ করে পেমেন্ট মেথড নির্বাচন করুন:**"

    if query:
        await query.answer()
        method = query.data.split("_")[1]
        context.user_data['pay_method'] = method
        await query.edit_message_text(
            f"✅ আপনি **{method.capitalize()}** সিলেক্ট করেছেন।\n"
            f"এখন কত টাকা রিচার্জ করতে চান তা সংখ্যায় লিখুন (যেমন: 500):"
        )
        return DEP_AMOUNT
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        return DEP_AMOUNT

async def get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount_tk = update.message.text.strip()
        if not amount_tk.replace('.', '', 1).isdigit():
            await update.message.reply_text("❌ শুধু সংখ্যা লিখুন (যেমন: 100)")
            return DEP_AMOUNT
            
        amount_tk = float(amount_tk)
        user_id = update.effective_user.id
        amount_usd = round(amount_tk / 127, 2)

        # Zinipay V1 API
        create_url = "https://api.zinipay.com/v1/payment/create"
        
        headers = {
            "zini-api-key": ZINIPAY_API_TOKEN,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        # আপনার বটের সঠিক ইউজারনেম এখানে দিন (যেমন: AWM_Proxy_Store_2_bot)
        bot_username = (await context.bot.get_me()).username
        
        payload = {
            "amount": str(amount_tk),
            # পেমেন্ট শেষে ইউজারকে সরাসরি আপনার এই বটেই ফিরিয়ে আনবে
            "redirect_url": f"https://t.me/{bot_username}?start=success",
            "cancel_url": f"https://t.me/{bot_username}",
            "webhook_url": f"https://t.me/{bot_username}", # আপনার যদি Webhook URL থাকে তবে সেটি দিন
            "cus_name": update.effective_user.first_name,
            "cus_email": f"user_{user_id}@t.me", 
            "metadata": {
                "user_id": str(user_id),
                "amount_usd": str(amount_usd)
            }
        }
        
        response = requests.post(create_url, json=payload, headers=headers, timeout=15)
        
        if response.status_code in [200, 201]:
            res = response.json()
            if res.get('status') == True or res.get('status') == "success":
                payment_url = res.get('payment_url')
                # ডকুমেন্টেশন অনুযায়ী invoiceId কী-টি চেক করা
                invoice_id = res.get('invoiceId') or res.get('invoice_id')
                
                if payment_url:
                    keyboard = [[InlineKeyboardButton("💳 Pay Now (Click Here)", url=payment_url)]]
                    await update.message.reply_text(
                        f"🚀 **পেমেন্ট লিঙ্ক তৈরি হয়েছে!**\n\n"
                        f"পরিমাণ: {amount_tk} TK (${amount_usd})\n\n"
                        "⚠️ **নির্দেশনা:** পেমেন্ট সফল হওয়ার পর অটোমেটিক এই বোটে ফিরে আসবেন এবং ব্যালেন্স অ্যাড হবে। "
                        "যদি পেমেন্ট শেষে অন্য বোটে নিয়ে যায়, তবে ঘাবড়াবেন না, আপনার ব্যালেন্স ব্যাকগ্রাউন্ডে চেক হচ্ছে।",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode='Markdown'
                    )
                    
                    # ব্যাকগ্রাউন্ডে ১ সেকেন্ড পর পর চেক করার টাস্ক শুরু করা
                    if invoice_id:
                        asyncio.create_task(check_payment_status(invoice_id, user_id, amount_usd, context))
                    return ConversationHandler.END
            
            await update.message.reply_text(f"❌ API Error: {res.get('message', 'Unknown Error')}")
        else:
            await update.message.reply_text(f"❌ Server Error: {response.status_code}. API Key চেক করুন।")

    except Exception as e:
        print(f"DEBUG ERROR: {str(e)}")
        await update.message.reply_text("❌ একটি কারিগরি সমস্যা হয়েছে।")
    
    return ConversationHandler.END
    
async def admin_recharge_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # লগ প্রিন্ট (টার্মিনালে ডিবাগ করার জন্য)
    print(f"DEBUG: Callback Data Received: {query.data}")
    
    try:
        data = query.data.split('_')
        action = data[0]     # approve অথবা reject
        req_id = data[1]     # ডাটাবেসের রিচার্জ আইডি
        user_id = int(data[2]) # ইউজারের টেলিগ্রাম আইডি
        
        # ১. ডাটাবেস চেক - রিকোয়েস্টটি এখনো পেন্ডিং আছে কি না
        status_check = db_query("SELECT status FROM recharge_history WHERE id=?", (req_id,), fetch=True)
        
        if not status_check:
            await query.answer("❌ রিকোয়েস্টটি ডাটাবেসে পাওয়া যায়নি!", show_alert=True)
            return
            
        if status_check[0][0] != 'pending':
            await query.answer("⚠️ এটি ইতিমধ্যে প্রসেস করা হয়েছে!", show_alert=True)
            await query.edit_message_reply_markup(reply_markup=None)
            return

        # ২. APPROVE লজিক
        if action == "approve":
            amount_usd = float(data[3])
            
            # ইউজারের মেইন ব্যালেন্স আপডেট
            db_query("INSERT INTO users (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?", 
                     (user_id, amount_usd, amount_usd))
            
            # রিচার্জ হিস্টোরি আপডেট
            db_query("UPDATE recharge_history SET status='approved' WHERE id=?", (req_id,))
            
            # অ্যাডমিন গ্রুপের মেসেজ আপডেট
            new_caption = query.message.caption_html.replace("🟡 Pending", "<b>✅ Approved (By Admin)</b>")
            await query.edit_message_caption(caption=new_caption, reply_markup=None, parse_mode='HTML')
            
            # ইউজারকে জানানো
            try:
                msg = (f"✅ <b>Deposit Success!</b>\n\n"
                       f"আপনার অ্যাকাউন্টে <b>${amount_usd:.3f}</b> যুক্ত করা হয়েছে।\n"
                       f"এখন আপনি প্রক্সি কিনতে পারবেন। ধন্যবাদ!")
                await context.bot.send_message(chat_id=user_id, text=msg, parse_mode='HTML')
            except Exception as e:
                print(f"DEBUG: Could not notify user: {e}")

        # ৩. REJECT লজিক
        elif action == "reject":
            db_query("UPDATE recharge_history SET status='rejected' WHERE id=?", (req_id,))
            
            # অ্যাডমিন গ্রুপের মেসেজ আপডেট
            new_caption = query.message.caption_html.replace("🟡 Pending", "<b>❌ Rejected (By Admin)</b>")
            await query.edit_message_caption(caption=new_caption, reply_markup=None, parse_mode='HTML')
            
            # ইউজারকে জানানো
            try:
                reject_text = (
                    "❌ <b>Deposit Rejected!</b>\n\n"
                    "আপনার পেমেন্ট রিকোয়েস্টটি বাতিল করা হয়েছে।\n"
                    "সঠিক স্ক্রিনশট দিয়ে আবার চেষ্টা করুন অথবা সাপোর্ট আইডিতে যোগাযোগ করুন।"
                )
                await context.bot.send_message(chat_id=user_id, text=reject_text, parse_mode='HTML')
            except Exception as e:
                print(f"DEBUG: Could not notify user: {e}")

    except Exception as e:
        print(f"ERROR in admin_recharge_action: {e}")
        await query.answer("❌ এরর: ডাটা প্রসেস করা সম্ভব হচ্ছে না।", show_alert=True)

async def cancel_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("❌ Recharge process cancelled.")
    return ConversationHandler.END

async def add_balance_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        target_id = int(context.args[0])
        amount = float(context.args[1])
        # User thakle update korbe, na thakle insert korbe
        db_query("INSERT INTO users (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?", (target_id, amount, amount))
        await update.message.reply_text(f"✅ User `{target_id}` ke ${amount} deya hoyeche।")
    except:
        await update.message.reply_text("Usage: `/addbal user_id poriman` (Example: /addbal 12345 10)")

# ১. সকল ইউজারের সংখ্যা দেখা
# ১. সকল ইউজারের সংখ্যা দেখা
async def all_users_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    users = db_query("SELECT COUNT(*) FROM users", fetch=True)
    count = users[0][0] if users else 0
    await update.callback_query.message.reply_text(f"👥 **Total Registered Users:** {count}")

# ২. ব্রডকাস্ট শুরু করা
async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("📢 **ব্রডকাস্ট মেসেজটি লিখুন:**\n(এটি সকল ইউজারের কাছে চলে যাবে)", reply_markup=cancel_btn())
    return "BROADCAST_STATE"

# ৩. মেসেজ পাঠানো
async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    broadcast_msg = update.message.text
    all_users = db_query("SELECT user_id FROM users", fetch=True)
    sent, failed = 0, 0
    for user in all_users:
        try:
            await context.bot.send_message(chat_id=user[0], text=f"📢 **Notification:**\n\n{broadcast_msg}")
            sent += 1
        except: failed += 1
    await update.message.reply_text(f"✅ **Broadcast Finished!**\n🚀 Sent: {sent}\n❌ Failed: {failed}")
    return ConversationHandler.END

# ৪. ডাটাবেস ব্যাকআপ
async def backup_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Sending Backup...")
    try:
        with open('awm_proxy.db', 'rb') as f:
            await context.bot.send_document(chat_id=update.effective_user.id, document=f, caption="📂 Database Backup")
    except Exception as e:
        await update.callback_query.message.reply_text(f"❌ Error: {e}")

# ৩. মেসেজ পাঠানো
async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    broadcast_msg = update.message.text
    all_users = db_query("SELECT user_id FROM users", fetch=True)
    
    sent = 0
    failed = 0
    for user in all_users:
        try:
            await context.bot.send_message(chat_id=user[0], text=f"📢 **Notification:**\n\n{broadcast_msg}")
            sent += 1
        except:
            failed += 1
            
    await update.message.reply_text(f"✅ **Broadcast Finished!**\n\n🚀 Sent: {sent}\n❌ Failed: {failed}")
    return ConversationHandler.END

# --- MAIN FUNCTION ---
# --- MISSING ADMIN FUNCTIONS (main এর উপরে থাকতে হবে) ---

async def start_add_prov(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("⌨️ Enter Provider Name inside brackets\nExample: `(ABC)`", reply_markup=cancel_btn(), parse_mode='Markdown')
    return ADD_PROV

async def start_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    prov = update.callback_query.data.split("_")[2]
    await update.callback_query.edit_message_text(f"📤 **Upload for {prov}**\n\nFirst, send format: `GB | Price`\nExample: `1GB | 1.0`", reply_markup=cancel_btn(), parse_mode='Markdown')
    return UPLOAD_PROXY

# --- MAIN FUNCTION (নিচের এটি সম্পূর্ণ রিপ্লেস করুন) ---

def main():
    app = Application.builder().token(BOT_TOKEN).connect_timeout(30).read_timeout(30).write_timeout(30).build()
    init_db()

    # ১. প্রক্সি অ্যাড করার কনভারসেশন হ্যান্ডলার (Admin)
    proxy_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_add_prov, pattern="admin_add_prov"),
            CallbackQueryHandler(start_upload, pattern="^up_btn_")
        ],
        states={
            ADD_PROV: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_prov)],
            UPLOAD_PROXY: [MessageHandler((filters.TEXT | filters.Document.ALL) & ~filters.COMMAND, save_proxy_data)],
        },
        fallbacks=[CallbackQueryHandler(cancel, pattern="cancel_action")]
    )

    # ২. রিচার্জ করার কনভারসেশন হ্যান্ডলার (User - Auto Payment System)
    # এখানে বাটন ক্লিক হ্যান্ডল করার জন্য এন্ট্রি পয়েন্টে CallbackQueryHandler যোগ করা হয়েছে
    recharge_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex('^⚡ Recharge$'), start_recharge),
            CallbackQueryHandler(start_recharge, pattern="^pay_")
        ],
        states={
            DEP_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount),
                CallbackQueryHandler(start_recharge, pattern="^pay_") # যদি ইউজার আবার অন্য বাটন চাপে
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_recharge, pattern="cancel_recharge"),
        ],
        per_message=False, # বাটন ক্লিক মিস না হওয়ার জন্য
        allow_reentry=True
    )

    # ৩. ব্রডকাস্ট কনভারসেশন হ্যান্ডলার (Admin)
    admin_extra_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_broadcast, pattern="admin_broadcast")],
        states={"BROADCAST_STATE": [MessageHandler(filters.TEXT & ~filters.COMMAND, send_broadcast)]},
        fallbacks=[CallbackQueryHandler(cancel, pattern="cancel_action")]
    )
    
    # --- Registration of Handlers (Order matters!) ---
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addbal", add_balance_admin)) 
    
    # Conversation Handlers আগে রাখতে হয়
    app.add_handler(proxy_conv)
    app.add_handler(recharge_conv)
    app.add_handler(admin_extra_conv)
    
    # Static Message Handlers
    app.add_handler(MessageHandler(filters.Regex('^🌐 Buy Proxy$'), buy_proxy))
    app.add_handler(MessageHandler(filters.Regex('^💳 My Balance$'), check_balance))
    app.add_handler(MessageHandler(filters.Regex('^📜 Purchase History$'), purchase_history))
    app.add_handler(MessageHandler(filters.Regex('^👨‍💻 Contact Support$'), contact_support))
    app.add_handler(MessageHandler(filters.Regex('^⚙️ Admin Panel$'), admin_panel_click))
    
    # Callback Handlers (সব ধরণের বাটন অ্যাকশন)
    app.add_handler(CallbackQueryHandler(pre_buy_confirmation, pattern="^prebuy_"))
    app.add_handler(CallbackQueryHandler(execute_purchase, pattern="^confirm_"))
    app.add_handler(CallbackQueryHandler(cancel_buy, pattern="cancel_buy"))
    app.add_handler(CallbackQueryHandler(view_admin_stock, pattern="admin_view_stock"))
    app.add_handler(CallbackQueryHandler(manage_options, pattern="^manage_"))
    app.add_handler(CallbackQueryHandler(delete_provider_confirm, pattern="^del_prov_"))
    app.add_handler(CallbackQueryHandler(all_users_count, pattern="admin_all_users"))
    app.add_handler(CallbackQueryHandler(backup_db, pattern="admin_backup_db"))
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="check_join_btn"))
    app.add_handler(CallbackQueryHandler(cancel, pattern="cancel_action"))
    app.add_handler(CallbackQueryHandler(cancel_recharge, pattern="cancel_recharge"))
    
    # নোট: আপনার get_amount ফাংশনে অবশ্যই 'requests.post' এর রেসপন্স চেক করবেন 
    # যেন API অফলাইন থাকলে বট ক্রাশ না করে (try-except ব্লক ব্যবহার করবেন)।

    print("🚀 AWM Proxy Bot is Running Successfully...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
