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

# ==================== DATABASE ====================
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
    """Extract all clickable URLs from email body"""
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

# ==================== SMAILPRO ENGINE ====================
SMAILPRO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://smailpro.com/temporary-email",
    "Origin": "https://smailpro.com",
    "Sec-Ch-Ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site"
}

async def smailpro_create_fresh():
    """Creates a NEW cookie session every single time to bypass limits"""
    jar = aiohttp.CookieJar(unsafe=True)
    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(
        cookie_jar=jar, headers=SMAILPRO_HEADERS, timeout=timeout
    ) as session:
        try:
            async with session.get("https://smailpro.com/temporary-email") as page:
                await page.read()
        except Exception as e:
            logger.warning(f"Page visit failed: {e}")

        endpoints = [
            "https://api.smailpro.com/v2/client/create?type=google",
            "https://api.smailpro.com/v2/email/create?type=google",
            "https://api.smailpro.com/v2/client/create?type=default",
            "https://api.smailpro.com/v2/email/create?type=default",
        ]

        for url in endpoints:
            try:
                async with session.get(url) as res:
                    if res.status == 200:
                        data = await res.json()
                        email = data.get("address") or data.get("email")
                        if email and "@" in email:
                            cookies = {c.key: c.value for c in jar}
                            logger.info(f"Smailpro email created: {email}")
                            return {
                                "email": email,
                                "provider": "smailpro",
                                "cookies": cookies
                            }
            except Exception as err:
                logger.debug(f"Endpoint {url} failed: {err}")
                continue

    # Fallback to Mail.tm
    try:
        logger.info("Smailpro unavailable, using backup engine...")
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://api.mail.tm/domains") as res:
                if res.status == 200:
                    data = await res.json()
                    domain = data["hydra:member"][0]["domain"]
                    rand_u = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
                    rand_p = "".join(random.choices(string.ascii_letters + string.digits, k=12))
                    email = f"{rand_u}@{domain}"
                    payload = {"address": email, "password": rand_p}

                    async with session.post("https://api.mail.tm/accounts", json=payload) as reg:
                        if reg.status == 201:
                            async with session.post("https://api.mail.tm/token", json=payload) as tok:
                                tok_data = await tok.json()
                                return {
                                    "email": email,
                                    "token": tok_data.get("token"),
                                    "provider": "mailtm",
                                    "cookies": {}
                                }
    except Exception as e:
        logger.error(f"Fallback error: {e}")
    return None


async def smailpro_check_inbox(session_data: dict):
    """Checks inbox using stored cookies"""
    if not session_data:
        return []

    provider = session_data.get("provider", "smailpro")
    email = session_data.get("email", "")
    timeout = aiohttp.ClientTimeout(total=10)

    if provider == "smailpro":
        stored_cookies = session_data.get("cookies", {})
        jar = aiohttp.CookieJar(unsafe=True)

        async with aiohttp.ClientSession(
            cookie_jar=jar, headers=SMAILPRO_HEADERS, timeout=timeout
        ) as session:
            for key, val in stored_cookies.items():
                jar.update_cookies({key: val})

            inbox_urls = [
                f"https://api.smailpro.com/v2/client/inbox?email={email}",
                f"https://api.smailpro.com/v2/email/inbox?email={email}",
            ]

            for url in inbox_urls:
                try:
                    async with session.get(url) as res:
                        if res.status == 200:
                            data = await res.json()
                            msgs = data.get("messages", [])
                            if isinstance(msgs, list) and len(msgs) > 0:
                                return msgs
                except Exception:
                    continue
        return []

    elif provider == "mailtm":
        try:
            token = session_data.get("token")
            headers = {"Authorization": f"Bearer {token}"}
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.get("https://api.mail.tm/messages") as res:
                    if res.status == 200:
                        data = await res.json()
                        raw = data.get("hydra:member", [])
                        messages = []
                        for m in raw:
                            mid = m.get("id")
                            async with session.get(f"https://api.mail.tm/messages/{mid}") as det_res:
                                if det_res.status == 200:
                                    det = await det_res.json()
                                    messages.append({
                                        "id": mid,
                                        "from": det.get("from", {}).get("address", "Unknown"),
                                        "subject": det.get("subject", "No Subject"),
                                        "body": det.get("text") or det.get("intro", "No content")
                                    })
                        return messages
        except Exception as e:
            logger.error(f"Mailtm inbox error: {e}")
    return []
    # ==================== 30-SEC AUTO REFRESH ====================
async def auto_refresh_inbox(user_id: int, chat_id: int):
    seen_ids = set()
    logger.info(f"Auto-refresh started for user {user_id}")

    while user_id in auto_refresh_tasks:
        session = user_mail_sessions.get(user_id)
        if not session:
            break

        try:
            messages = await smailpro_check_inbox(session)

            for msg in messages:
                msg_id = str(msg.get("id") or (msg.get("subject", "") + msg.get("from", "")))
                if msg_id not in seen_ids:
                    seen_ids.add(msg_id)

                    sender = msg.get("from", "Unknown")
                    subject = msg.get("subject", "No Subject")
                    body = msg.get("body") or msg.get("text") or "No content available."

                    # Extract clickable links from body
                    urls = extract_urls(body)

                    # Build message WITHOUT mono/code on links
                    msg_text = (
                        f"📩 <b>New Email Received!</b>\n\n"
                        f"👤 <b>From:</b> {html.escape(str(sender))}\n"
                        f"📌 <b>Subject:</b> {html.escape(str(subject))}\n\n"
                        f"📝 <b>Content:</b>\n{html.escape(str(body))}"
                    )

                    # Send links separately as plain clickable URLs (no mono)
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


# ==================== USER COMMANDS ====================

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
            f"Thank you for starting the <b>Smailpro Temp Mail Bot</b>! 🎉\n\n"
            f"⚡ <b>Available Actions:</b>\n"
            f"🟢 <b>Create New Mail</b> — Generate a fresh temporary Gmail\n"
            f"🔵 <b>Check Inbox</b> — Read incoming emails and OTPs\n"
            f"🟡 <b>Current Mail</b> — View your active email address\n"
            f"🔴 <b>Delete Session</b> — Discard email and stop refresh\n\n"
            f"⏱️ <i>Once created, inbox auto-refreshes every 30 seconds!</i>\n\n"
            f"Tap a button below to get started 👇"
        )
    else:
        text = (
            f"👋 <b>Welcome back, {mention}!</b>\n\n"
            f"Use the buttons below to manage your temporary mail 👇"
        )

    await message.answer(text, reply_markup=get_main_keyboard())


@dp.message(F.text == "🟢 Create New Mail")
async def create_mail_handler(message: types.Message):
    user = message.from_user
    if is_banned(user.id):
        await message.answer("🚫 You are banned.")
        return

    register_user(user)
    status_msg = await message.answer("⏳ <i>Generating new Smailpro email...</i>")

    mail_data = await smailpro_create_fresh()

    if mail_data:
        stop_auto_refresh(user.id)

        user_mail_sessions[user.id] = {
            "email": mail_data["email"],
            "token": mail_data.get("token"),
            "provider": mail_data.get("provider"),
            "cookies": mail_data.get("cookies", {}),
            "created_at": datetime.now().isoformat()
        }

        email = mail_data["email"]

        uid = str(user.id)
        if uid in db["users"]:
            db["users"][uid]["emails_created"] = db["users"][uid].get("emails_created", 0) + 1
            db["stats"]["total_emails_created"] += 1
            save_db(db)

        await status_msg.edit_text(
            f"✅ <b>Your Smailpro Email is Ready:</b>\n\n"
            f"<code>{email}</code>\n\n"
            f"📋 <i>Tap the email above to copy.</i>\n"
            f"🔄 <b>Auto-Refresh Active:</b> Checking every 30 seconds.\n"
            f"♾️ <b>Unlimited:</b> Create as many as you want!\n\n"
            f"Tap 🔵 <b>Check Inbox</b> anytime."
        )

        start_auto_refresh(user.id, message.chat.id)
    else:
        await status_msg.edit_text("❌ <b>Failed to generate email.</b> Try again in a few seconds.")


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

    messages = await smailpro_check_inbox(session)

    if not messages:
        await status_msg.edit_text(
            f"📭 <b>Inbox is empty for:</b>\n<code>{email}</code>\n\n"
            f"🔄 <i>Auto-refresh running every 30s.</i>"
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
        w.writerow([uid, u.get("first_name", ""), u.get("username", ""), u.get("joined", ""), u.get("emails_created", 0), u.get("inbox_checks", 0), u.get("last_active", ""), "Yes" if int(uid) in db["banned"] else "No", db["warned"].get(uid, 0)])
    output.seek(0)
    buf = io.BytesIO(output.getvalue().encode("utf-8"))
    buf.name = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    await message.answer_document(BufferedInputFile(buf.read(), filename=buf.name), caption="📁 <b>User Export (CSV)</b>")


# ==================== WEB SERVER ====================
async def handle_home(request):
    return web.json_response({
        "status": "online",
        "service": "Smailpro Bot",
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
