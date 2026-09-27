import asyncio
import csv
import html
import io
import json
import logging
import os
import random
import re
import string
from urllib.parse import unquote
from datetime import datetime

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import ReplyKeyboardBuilder

# ==================== CONFIG ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "123456789").split(",")
    if x.strip()
]
PORT = int(os.getenv("PORT", 8080))

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ==================== LOCAL DATABASE ====================
DB_FILE = "database.json"

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "users": {},
        "banned": [],
        "warned": {},
        "stats": {"total_emails_created": 0, "total_inbox_checks": 0}
    }

def save_db(data):
    try:
        with open(DB_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"DB Save Error: {e}")

db = load_db()
user_mail_sessions = {}
auto_refresh_tasks = {}

# ==================== HELPERS ====================
def register_user(user: types.User) -> bool:
    uid = str(user.id)
    now = datetime.now().isoformat()
    if uid not in db["users"]:
        db["users"][uid] = {
            "user_id": user.id,
            "first_name": user.first_name,
            "username": user.username or "N/A",
            "joined": now,
            "emails_created": 0,
            "inbox_checks": 0,
            "last_active": now
        }
        save_db(db)
        return True
    else:
        db["users"][uid]["last_active"] = now
        db["users"][uid]["first_name"] = user.first_name
        db["users"][uid]["username"] = user.username or "N/A"
        save_db(db)
        return False

def is_banned(uid: int) -> bool:
    return uid in db["banned"]

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def extract_urls(text: str) -> list:
    """Extract clean clickable URLs from email body"""
    if not text:
        return []
    pattern = r'https?://[^\s<>"\')\]\},]+'
    urls = re.findall(pattern, text)
    return list(dict.fromkeys(urls))

# ==================== KEYBOARD ====================
def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(
        types.KeyboardButton(text="🟢 Create New Mail"),
        types.KeyboardButton(text="🔵 Check Inbox"),
    )
    builder.row(
        types.KeyboardButton(text="🟡 Current Mail"),
        types.KeyboardButton(text="🔴 Delete Session"),
    )
    return builder.as_markup(
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="Choose an option below...",
    )

# ==================== ULTRA RESILIENT GMAIL ENGINE ====================
COMMON_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
]

async def master_create_gmail():
    """Generates 100% genuine @gmail.com address with intelligent session handling"""
    ua = random.choice(COMMON_USER_AGENTS)
    timeout = aiohttp.ClientTimeout(total=18)

    # --- METHOD 1: Emailnator Session Engine ---
    try:
        logger.info("Attempting Gmail Generation via Method 1 (Emailnator)...")
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(cookie_jar=jar, timeout=timeout) as session:
            init_headers = {
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1"
            }
            # Step 1: Establish Session
            async with session.get("https://www.emailnator.com/", headers=init_headers) as r:
                await r.read()

            cookies = {c.key: c.value for c in jar}
            xsrf = unquote(cookies.get("XSRF-TOKEN", ""))

            if xsrf:
                post_headers = {
                    "User-Agent": ua,
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Content-Type": "application/json",
                    "Origin": "https://www.emailnator.com",
                    "Referer": "https://www.emailnator.com/",
                    "X-XSRF-TOKEN": xsrf,
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-origin"
                }

                payload = {"email": ["domain", "plusGmail", "dotGmail", "googleMail"]}
                async with session.post("https://www.emailnator.com/generate-email", json=payload, headers=post_headers) as gen_res:
                    if gen_res.status == 200:
                        data = await gen_res.json()
                        email_list = data.get("email", [])
                        for em in email_list:
                            if "@gmail.com" in em or "@googlemail.com" in em:
                                logger.info(f"Method 1 Success: {em}")
                                return {
                                    "email": em,
                                    "provider": "emailnator",
                                    "cookies": cookies,
                                    "xsrf": xsrf,
                                    "ua": ua
                                }
    except Exception as e:
        logger.warning(f"Method 1 Error: {e}")

    # --- METHOD 2: Smailpro Multi-Endpoint Engine ---
    try:
        logger.info("Attempting Gmail Generation via Method 2 (Smailpro)...")
        jar = aiohttp.CookieJar(unsafe=True)
        smail_headers = {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://smailpro.com",
            "Referer": "https://smailpro.com/temporary-email"
        }
        async with aiohttp.ClientSession(cookie_jar=jar, headers=smail_headers, timeout=timeout) as session:
            try:
                async with session.get("https://smailpro.com/temporary-email") as p:
                    await p.read()
            except Exception:
                pass

            endpoints = [
                "https://api.smailpro.com/v2/client/create?type=google&server=google",
                "https://api.smailpro.com/v2/email/create?type=google",
                "https://api.smailpro.com/v2/client/create?type=gmail"
            ]

            for ep in endpoints:
                try:
                    async with session.get(ep) as res:
                        if res.status == 200:
                            data = await res.json()
                            email = data.get("address") or data.get("email")
                            if email and ("@gmail.com" in email or "@googlemail.com" in email):
                                cookies = {c.key: c.value for c in jar}
                                logger.info(f"Method 2 Success: {email}")
                                return {
                                    "email": email,
                                    "provider": "smailpro",
                                    "cookies": cookies,
                                    "ua": ua
                                }
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"Method 2 Error: {e}")

    return None


async def master_check_inbox(session_data: dict):
    """Fetches Gmail messages from active provider"""
    if not session_data:
        return []

    provider = session_data.get("provider", "emailnator")
    email = session_data.get("email", "")
    ua = session_data.get("ua", COMMON_USER_AGENTS[0])
    timeout = aiohttp.ClientTimeout(total=12)

    # 1. Emailnator Inbox Reader
    if provider == "emailnator":
        try:
            stored_cookies = session_data.get("cookies", {})
            xsrf = session_data.get("xsrf", "")
            jar = aiohttp.CookieJar(unsafe=True)

            headers = {
                "User-Agent": ua,
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Origin": "https://www.emailnator.com",
                "Referer": "https://www.emailnator.com/",
                "X-XSRF-TOKEN": xsrf
            }

            async with aiohttp.ClientSession(cookie_jar=jar, headers=headers, timeout=timeout) as session:
                for k, v in stored_cookies.items():
                    jar.update_cookies({k: v})

                async with session.post("https://www.emailnator.com/message-list", json={"email": email}) as res:
                    if res.status == 200:
                        data = await res.json()
                        raw_msgs = data.get("messageData", [])
                        messages = []

                        for m in raw_msgs:
                            msg_id = m.get("messageID")
                            if msg_id and msg_id != "AD_CONTAINER":
                                # Fetch message content
                                async with session.post("https://www.emailnator.com/message-list", json={"email": email, "messageID": msg_id}) as b_res:
                                    if b_res.status == 200:
                                        body_html = await b_res.text()
                                        clean_text = re.sub(r'<[^>]+>', ' ', body_html).strip()
                                        messages.append({
                                            "id": msg_id,
                                            "from": m.get("from", "Unknown"),
                                            "subject": m.get("subject", "No Subject"),
                                            "body": clean_text or body_html
                                        })
                        return messages
        except Exception as e:
            logger.error(f"Emailnator Inbox Check Error: {e}")

    # 2. Smailpro Inbox Reader
    elif provider == "smailpro":
        try:
            stored_cookies = session_data.get("cookies", {})
            jar = aiohttp.CookieJar(unsafe=True)
            s_headers = {
                "User-Agent": ua,
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://smailpro.com",
                "Referer": "https://smailpro.com/temporary-email"
            }
            async with aiohttp.ClientSession(cookie_jar=jar, headers=s_headers, timeout=timeout) as session:
                for k, v in stored_cookies.items():
                    jar.update_cookies({k: v})

                urls = [
                    f"https://api.smailpro.com/v2/client/inbox?email={email}",
                    f"https://api.smailpro.com/v2/email/inbox?email={email}"
                ]
                for u in urls:
                    try:
                        async with session.get(u) as res:
                            if res.status == 200:
                                data = await res.json()
                                msgs = data.get("messages", [])
                                if isinstance(msgs, list):
                                    return msgs
                    except Exception:
                        continue
        except Exception as e:
            logger.error(f"Smailpro Inbox Check Error: {e}")

    return []
  # ==================== 30-SEC AUTO REFRESH LOOP ====================
async def auto_refresh_inbox(user_id: int, chat_id: int):
    seen_ids = set()
    logger.info(f"Auto-refresh started for user {user_id}")

    while user_id in auto_refresh_tasks:
        session = user_mail_sessions.get(user_id)
        if not session:
            break

        try:
            messages = await master_check_inbox(session)

            for msg in messages:
                msg_id = str(msg.get("id") or (msg.get("subject", "") + msg.get("from", "")))
                if msg_id not in seen_ids:
                    seen_ids.add(msg_id)

                    sender = msg.get("from", "Unknown")
                    subject = msg.get("subject", "No Subject")
                    body = msg.get("body") or msg.get("text") or "No content available."

                    # Clickable links extract karna
                    urls = extract_urls(body)

                    msg_text = (
                        f"📩 <b>New Gmail Received!</b>\n\n"
                        f"👤 <b>From:</b> {html.escape(str(sender))}\n"
                        f"📌 <b>Subject:</b> {html.escape(str(subject))}\n\n"
                        f"📝 <b>Content:</b>\n{html.escape(str(body))}"
                    )

                    # Links plain clickable format mein (no mono)
                    if urls:
                        links_text = "\n\n🔗 <b>Links Found:</b>\n"
                        for i, url in enumerate(urls, 1):
                            links_text += f"\n{i}. {url}"
                        msg_text += links_text

                    msg_text += "\n\n🔄 <i>Auto-refreshing every 30 seconds...</i>"

                    await bot.send_message(
                        chat_id, msg_text,
                        disable_web_page_preview=True
                    )

                    uid = str(user_id)
                    if uid in db["users"]:
                        db["users"][uid]["inbox_checks"] = db["users"][uid].get("inbox_checks", 0) + 1
                        db["stats"]["total_inbox_checks"] += 1
                        save_db(db)

        except Exception as e:
            logger.error(f"Auto-refresh error for {user_id}: {e}")

        await asyncio.sleep(30)


def start_auto_refresh(user_id: int, chat_id: int):
    if user_id in auto_refresh_tasks:
        auto_refresh_tasks[user_id].cancel()
    task = asyncio.create_task(auto_refresh_inbox(user_id, chat_id))
    auto_refresh_tasks[user_id] = task


def stop_auto_refresh(user_id: int):
    if user_id in auto_refresh_tasks:
        auto_refresh_tasks[user_id].cancel()
        del auto_refresh_tasks[user_id]
        logger.info(f"Stopped auto-refresh for user {user_id}")


# ==================== BOT USER COMMANDS ====================

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    user = message.from_user
    if is_banned(user.id):
        await message.answer("🚫 <b>Access Denied:</b> You are banned from using this bot.")
        return

    is_new = register_user(user)
    mention = f'<a href="tg://user?id={user.id}">{html.escape(user.first_name)}</a>'

    if is_new:
        text = (
            f"👋 <b>Welcome, {mention}!</b>\n\n"
            f"Thank you for starting the <b>Gmail Temp Mail Bot</b>! 🎉\n\n"
            f"⚡ <b>Available Actions:</b>\n"
            f"🟢 <b>Create New Mail</b> — Generate a fresh @gmail.com address\n"
            f"🔵 <b>Check Inbox</b> — Read incoming emails & OTPs\n"
            f"🟡 <b>Current Mail</b> — View your active Gmail address\n"
            f"🔴 <b>Delete Session</b> — Discard email & stop background refresh\n\n"
            f"⏱️ <i>Note: Once created, inbox auto-refreshes every 30 seconds!</i>\n\n"
            f"Tap a button below to get started 👇"
        )
    else:
        text = (
            f"👋 <b>Welcome back, {mention}!</b>\n\n"
            f"Use the buttons below to manage your temporary Gmail 👇"
        )

    await message.answer(text, reply_markup=get_main_keyboard())


@dp.message(F.text == "🟢 Create New Mail")
async def create_mail_handler(message: types.Message):
    user = message.from_user
    if is_banned(user.id):
        await message.answer("🚫 You are banned from using this bot.")
        return

    register_user(user)
    status_msg = await message.answer("⏳ <i>Generating fresh @gmail.com address...</i>")

    mail_data = await master_create_gmail()

    if mail_data:
        stop_auto_refresh(user.id)

        user_mail_sessions[user.id] = {
            "email": mail_data["email"],
            "provider": mail_data["provider"],
            "cookies": mail_data.get("cookies", {}),
            "xsrf": mail_data.get("xsrf", ""),
            "ua": mail_data.get("ua", ""),
            "created_at": datetime.now().isoformat()
        }

        email = mail_data["email"]

        uid = str(user.id)
        if uid in db["users"]:
            db["users"][uid]["emails_created"] = db["users"][uid].get("emails_created", 0) + 1
            db["stats"]["total_emails_created"] += 1
            save_db(db)

        await status_msg.edit_text(
            f"✅ <b>Your Gmail is Ready:</b>\n\n"
            f"<code>{email}</code>\n\n"
            f"📋 <i>Tap the email above to copy it.</i>\n"
            f"🔄 <b>Auto-Refresh Active:</b> Checking every 30 seconds.\n"
            f"♾️ <b>Unlimited:</b> Fresh session used!\n\n"
            f"You can also tap 🔵 <b>Check Inbox</b> anytime."
        )

        start_auto_refresh(user.id, message.chat.id)
    else:
        await status_msg.edit_text("❌ <b>Failed to generate Gmail.</b> Server busy, please try again in a few seconds.")


@dp.message(F.text == "🔵 Check Inbox")
async def check_inbox_handler(message: types.Message):
    user = message.from_user
    if is_banned(user.id):
        return

    register_user(user)
    session = user_mail_sessions.get(user.id)

    if not session:
        await message.answer("⚠️ No active email.\nTap 🟢 <b>Create New Mail</b> first!")
        return

    email = session["email"]
    status_msg = await message.answer(f"🔍 <i>Checking inbox for:</i> <code>{email}</code>")

    messages = await master_check_inbox(session)

    if not messages:
        await status_msg.edit_text(
            f"📭 <b>Inbox is empty for:</b>\n<code>{email}</code>\n\n"
            f"🔄 <i>Auto-refresh is running every 30 seconds.</i>"
        )
        return

    await status_msg.delete()
    for msg in messages[:3]:
        sender = msg.get("from", "Unknown")
        subject = msg.get("subject", "No Subject")
        body = msg.get("body") or msg.get("text") or "No Content"

        urls = extract_urls(body)

        msg_text = (
            f"📩 <b>Received Message:</b>\n\n"
            f"👤 <b>From:</b> {html.escape(str(sender))}\n"
            f"📌 <b>Subject:</b> {html.escape(str(subject))}\n\n"
            f"📝 <b>Body:</b>\n{html.escape(str(body))}"
        )

        if urls:
            links_text = "\n\n🔗 <b>Links:</b>\n"
            for i, url in enumerate(urls, 1):
                links_text += f"\n{i}. {url}"
            msg_text += links_text

        await message.answer(msg_text, disable_web_page_preview=True)


@dp.message(F.text == "🟡 Current Mail")
async def current_mail_handler(message: types.Message):
    user = message.from_user
    if is_banned(user.id):
        return

    register_user(user)
    session = user_mail_sessions.get(user.id)

    if session:
        is_refreshing = user.id in auto_refresh_tasks
        status = "🟢 Active (30s)" if is_refreshing else "🔴 Stopped"
        await message.answer(
            f"ℹ️ <b>Active Session:</b>\n\n"
            f"📧 <b>Email:</b> <code>{session['email']}</code>\n"
            f"📅 <b>Created:</b> {session['created_at'][:19]}\n"
            f"🔄 <b>Auto-Refresh:</b> {status}"
        )
    else:
        await message.answer("⚠️ No active email.\nTap 🟢 <b>Create New Mail</b>.")


@dp.message(F.text == "🔴 Delete Session")
async def delete_session_handler(message: types.Message):
    user = message.from_user
    register_user(user)

    stop_auto_refresh(user.id)

    if user.id in user_mail_sessions:
        del user_mail_sessions[user.id]
        await message.answer("🗑️ <b>Session and cookies deleted.</b> Auto-refresh stopped.")
    else:
        await message.answer("⚠️ No active session to delete.")


# ==================== ADMIN COMMANDS ====================

@dp.message(Command("broadcast"))
async def broadcast_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    text = message.text.replace("/broadcast", "", 1).strip()
    if not text:
        await message.answer("⚠️ Usage: <code>/broadcast Your message</code>")
        return

    sent = failed = 0
    status_msg = await message.answer("📢 <i>Broadcasting...</i>")

    for uid in list(db["users"].keys()):
        try:
            await bot.send_message(int(uid), f"📢 <b>Announcement:</b>\n\n{text}")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await status_msg.edit_text(f"📢 <b>Done:</b> ✅ {sent} | ❌ {failed}")


@dp.message(Command("ban"))
async def ban_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("⚠️ Usage: <code>/ban USER_ID</code>")
        return
    try:
        tid = int(args[1])
        if tid not in db["banned"]:
            db["banned"].append(tid)
            stop_auto_refresh(tid)
            user_mail_sessions.pop(tid, None)
            save_db(db)
            await message.answer(f"🚫 User <code>{tid}</code> <b>banned</b>.")
        else:
            await message.answer("Already banned.")
    except ValueError:
        await message.answer("❌ Invalid User ID.")


@dp.message(Command("unban"))
async def unban_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("⚠️ Usage: <code>/unban USER_ID</code>")
        return
    try:
        tid = int(args[1])
        if tid in db["banned"]:
            db["banned"].remove(tid)
            save_db(db)
            await message.answer(f"✅ User <code>{tid}</code> <b>unbanned</b>.")
        else:
            await message.answer("Not banned.")
    except ValueError:
        await message.answer("❌ Invalid User ID.")


@dp.message(Command("banlist"))
async def banlist_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    if not db["banned"]:
        await message.answer("📋 <b>Ban List:</b> Empty")
        return
    text = "🚫 <b>Banned Users:</b>\n\n"
    for uid in db["banned"]:
        u = db["users"].get(str(uid), {})
        text += f"• <code>{uid}</code> — {u.get('first_name', '?')} (@{u.get('username', 'N/A')})\n"
    await message.answer(text)


@dp.message(Command("warn"))
async def warn_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("⚠️ Usage: <code>/warn USER_ID</code>")
        return
    try:
        tid_str = str(int(args[1]))
        tid_int = int(args[1])
        db["warned"][tid_str] = db["warned"].get(tid_str, 0) + 1
        wc = db["warned"][tid_str]
        save_db(db)
        await message.answer(f"⚠️ User <code>{tid_str}</code> warned. <b>{wc}/3</b>")
        if wc >= 3 and tid_int not in db["banned"]:
            db["banned"].append(tid_int)
            stop_auto_refresh(tid_int)
            user_mail_sessions.pop(tid_int, None)
            save_db(db)
            await message.answer(f"🚫 <code>{tid_str}</code> <b>auto-banned</b> (3 warnings).")
        try:
            await bot.send_message(tid_int, f"⚠️ <b>Warning!</b> Status: {wc}/3\n<i>3 warnings = auto ban</i>")
        except Exception:
            pass
    except ValueError:
        await message.answer("❌ Invalid User ID.")


@dp.message(Command("unwarn"))
async def unwarn_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("⚠️ Usage: <code>/unwarn USER_ID</code>")
        return
    tid = args[1]
    if tid in db["warned"]:
        del db["warned"][tid]
        save_db(db)
        await message.answer(f"✅ Warnings cleared for <code>{tid}</code>.")
    else:
        await message.answer("No warnings found.")


@dp.message(Command("warmlist"), Command("warnlist"))
async def warnlist_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    if not db["warned"]:
        await message.answer("📋 <b>Warning List:</b> Empty")
        return
    text = "⚠️ <b>Warned Users:</b>\n\n"
    for uid, count in db["warned"].items():
        u = db["users"].get(uid, {})
        text += f"• <code>{uid}</code> — {u.get('first_name', '?')} — <b>{count}/3</b>\n"
    await message.answer(text)


@dp.message(Command("stats"))
async def stats_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        f"📊 <b>Bot Statistics:</b>\n\n"
        f"👥 Total Users: {len(db['users'])}\n"
        f"🚫 Banned: {len(db['banned'])}\n"
        f"⚠️ Warned: {len(db['warned'])}\n"
        f"📧 Active Sessions: {len(user_mail_sessions)}\n"
        f"🔄 Auto-Refreshes: {len(auto_refresh_tasks)}\n\n"
        f"📈 <b>Lifetime:</b>\n"
        f"✉️ Emails Created: {db['stats']['total_emails_created']}\n"
        f"📬 Inbox Checks: {db['stats']['total_inbox_checks']}"
    )


@dp.message(Command("topusers"))
async def topusers_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    users = sorted(db["users"].values(), key=lambda x: x.get("emails_created", 0), reverse=True)
    text = "🏆 <b>Top 10 Users:</b>\n\n"
    for i, u in enumerate(users[:10], 1):
        text += f"{i}. <b>{u.get('first_name', '?')}</b> — 📧 {u.get('emails_created', 0)} | 📬 {u.get('inbox_checks', 0)}\n"
    if not users:
        text = "No users yet."
    await message.answer(text)


@dp.message(Command("userlist"))
async def userlist_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    if not db["users"]:
        await message.answer("📋 No users yet.")
        return
    text = "👥 <b>All Users:</b>\n\n"
    for uid, u in db["users"].items():
        st = "🚫" if int(uid) in db["banned"] else "✅"
        text += f"{st} <code>{uid}</code> — {u.get('first_name', '?')} (@{u.get('username', 'N/A')}) — {u.get('joined', '')[:10]}\n"
        if len(text) > 3800:
            await message.answer(text)
            text = ""
    if text:
        await message.answer(text)


@dp.message(Command("export"))
async def export_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    if not db["users"]:
        await message.answer("No data to export.")
        return
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["User ID", "Name", "Username", "Joined", "Emails", "Checks", "Last Active", "Banned", "Warnings"])
    for uid, u in db["users"].items():
        w.writerow([
            uid, u.get("first_name", ""), u.get("username", ""),
            u.get("joined", ""), u.get("emails_created", 0),
            u.get("inbox_checks", 0), u.get("last_active", ""),
            "Yes" if int(uid) in db["banned"] else "No",
            db["warned"].get(uid, 0)
        ])
    output.seek(0)
    buf = io.BytesIO(output.getvalue().encode("utf-8"))
    buf.name = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    await message.answer_document(
        BufferedInputFile(buf.read(), filename=buf.name),
        caption="📁 <b>User Export (CSV)</b>"
    )


# ==================== WEB SERVER (Render Port Binding) ====================
async def handle_home(request):
    return web.json_response({
        "status": "online",
        "service": "Gmail Temp Bot",
        "users": len(db["users"]),
        "sessions": len(user_mail_sessions)
    })

async def handle_health(request):
    return web.Response(text="OK", status=200)

def init_web_app():
    app = web.Application()
    app.router.add_get("/", handle_home)
    app.router.add_get("/health", handle_health)
    return app


# ==================== MAIN ====================
async def main():
    logger.info(f"Starting web server on port {PORT}...")
    web_app = init_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logger.info(f"Web server online on 0.0.0.0:{PORT}")
    logger.info("Starting Telegram Bot...")
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())  
