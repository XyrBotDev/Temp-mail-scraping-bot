import asyncio
import csv
import io
import json
import logging
import os
from datetime import datetime

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties  # <-- Fix: Correct Import
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import ReplyKeyboardBuilder

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "123456789").split(",")
    if x.strip()
]
PORT = int(os.getenv("PORT", 8080))

# Bot instance with DefaultBotProperties
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ==================== LOCAL JSON DATABASE ====================
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
        "stats": {"total_emails_created": 0, "total_inbox_checks": 0},
    }


def save_db(data):
    try:
        with open(DB_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Database Save Error: {e}")


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
            "last_active": now,
        }
        save_db(db)
        return True
    else:
        db["users"][uid]["last_active"] = now
        db["users"][uid]["first_name"] = user.first_name
        db["users"][uid]["username"] = user.username or "N/A"
        save_db(db)
        return False


def is_banned(user_id: int) -> bool:
    return user_id in db["banned"]


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ==================== KEYBOARDS ====================
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


# ==================== SMAILPRO API ====================
async def smailpro_create_email():
    url = "https://api.smailpro.com/v2/email/create?type=google"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as res:
                if res.status == 200:
                    data = await res.json()
                    return data.get("address") or data.get("email")
    except Exception as e:
        logger.error(f"Smailpro Create Error: {e}")
    return None


async def smailpro_get_inbox(email: str):
    url = f"https://api.smailpro.com/v2/email/inbox?email={email}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as res:
                if res.status == 200:
                    return await res.json()
    except Exception as e:
        logger.error(f"Smailpro Inbox Error: {e}")
    return None


# ==================== 30-SECOND AUTO REFRESH TASK ====================
async def auto_refresh_inbox(user_id: int, chat_id: int):
    seen_ids = set()
    logger.info(f"Started 30s auto-refresh for user {user_id}")

    while user_id in auto_refresh_tasks:
        session = user_mail_sessions.get(user_id)
        if not session:
            break

        email = session.get("email")
        if not email:
            break

        try:
            inbox_data = await smailpro_get_inbox(email)
            messages = inbox_data.get("messages", []) if inbox_data else []

            for msg in messages:
                msg_id = msg.get("id") or (
                    msg.get("subject", "") + msg.get("from", "")
                )
                if msg_id not in seen_ids:
                    seen_ids.add(msg_id)

                    sender = msg.get("from", "Unknown")
                    subject = msg.get("subject", "No Subject")
                    body = (
                        msg.get("body")
                        or msg.get("text")
                        or "No content available."
                    )

                    msg_text = (
                        f"📩 <b>New Email Received!</b>\n\n"
                        f"👤 <b>From:</b> <code>{sender}</code>\n"
                        f"📌 <b>Subject:</b> {subject}\n\n"
                        f"📝 <b>Content / OTP:</b>\n<code>{body}</code>\n\n"
                        f"🔄 <i>Auto-refreshing every 30 seconds...</i>"
                    )
                    await bot.send_message(chat_id, msg_text)

                    uid = str(user_id)
                    if uid in db["users"]:
                        db["users"][uid]["inbox_checks"] = (
                            db["users"][uid].get("inbox_checks", 0) + 1
                        )
                        db["stats"]["total_inbox_checks"] += 1
                        save_db(db)

        except Exception as e:
            logger.error(f"Auto-refresh loop error for {user_id}: {e}")

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


# ==================== BOT HANDLERS ====================
@dp.message(CommandStart())
async def start_handler(message: types.Message):
    user = message.from_user

    if is_banned(user.id):
        await message.answer(
            "🚫 <b>Access Denied:</b> You are banned from using this bot."
        )
        return

    is_new = register_user(user)
    user_mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'

    if is_new:
        text = (
            f"👋 <b>Welcome, {user_mention}!</b>\n\n"
            f"Thank you for starting the <b>Smailpro Temp Mail Bot</b>! 🎉\n\n"
            f"⚡ <b>Available Actions:</b>\n"
            f"🟢 <b>Create New Mail</b> — Generate a fresh temporary Gmail\n"
            f"🔵 <b>Check Inbox</b> — View incoming messages and OTPs\n"
            f"🟡 <b>Current Mail</b> — Check your active address\n"
            f"🔴 <b>Delete Session</b> — Discard your email & stop refresh\n\n"
            f"⏱️ <i>Note: Once created, your inbox will automatically refresh every 30 seconds!</i>\n\n"
            f"Tap any button below to get started 👇"
        )
    else:
        text = (
            f"👋 <b>Welcome back, {user_mention}!</b>\n\n"
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
    status_msg = await message.answer(
        "⏳ <i>Generating new Smailpro email address...</i>"
    )

    email = await smailpro_create_email()

    if email:
        stop_auto_refresh(user.id)

        user_mail_sessions[user.id] = {
            "email": email,
            "created_at": datetime.now().isoformat(),
        }

        uid = str(user.id)
        if uid in db["users"]:
            db["users"][uid]["emails_created"] = (
                db["users"][uid].get("emails_created", 0) + 1
            )
            db["stats"]["total_emails_created"] += 1
            save_db(db)

        await status_msg.edit_text(
            f"✅ <b>Your Temporary Email is Ready:</b>\n\n"
            f"<code>{email}</code>\n\n"
            f"📋 <i>Tap on the address above to copy it.</i>\n"
            f"🔄 <b>Auto-Refresh Active:</b> Inbox is checked every 30 seconds automatically.\n\n"
            f"You can also tap 🔵 <b>Check Inbox</b> at any time."
        )

        start_auto_refresh(user.id, message.chat.id)
    else:
        await status_msg.edit_text(
            "❌ <b>Failed to generate email.</b> Please try again in a few seconds."
        )


@dp.message(F.text == "🔵 Check Inbox")
async def check_inbox_handler(message: types.Message):
    user = message.from_user
    if is_banned(user.id):
        return

    register_user(user)
    session = user_mail_sessions.get(user.id)

    if not session:
        await message.answer(
            "⚠️ No active email session found.\nTap 🟢 <b>Create New Mail</b> first!"
        )
        return

    email = session["email"]
    status_msg = await message.answer(
        f"🔍 <i>Checking inbox for:</i> <code>{email}</code>"
    )

    inbox_data = await smailpro_get_inbox(email)
    messages = inbox_data.get("messages", []) if inbox_data else []

    if not messages:
        await status_msg.edit_text(
            f"📭 <b>Inbox is empty for:</b>\n<code>{email}</code>\n\n"
            f"🔄 <i>Auto-refresh is running in background every 30s.</i>"
        )
        return

    await status_msg.delete()
    for msg in messages[:3]:
        sender = msg.get("from", "Unknown")
        subject = msg.get("subject", "No Subject")
        body = msg.get("body") or msg.get("text") or "No Content"

        await message.answer(
            f"📩 <b>Received Message:</b>\n\n"
            f"👤 <b>From:</b> <code>{sender}</code>\n"
            f"📌 <b>Subject:</b> {subject}\n\n"
            f"📝 <b>Body / OTP / Link:</b>\n<code>{body}</code>"
        )


@dp.message(F.text == "🟡 Current Mail")
async def current_mail_handler(message: types.Message):
    user = message.from_user
    if is_banned(user.id):
        return

    register_user(user)
    session = user_mail_sessions.get(user.id)

    if session:
        is_refreshing = user.id in auto_refresh_tasks
        refresh_status = (
            "🟢 Active (30s Interval)" if is_refreshing else "🔴 Stopped"
        )
        await message.answer(
            f"ℹ️ <b>Active Session Details:</b>\n\n"
            f"📧 <b>Email:</b> <code>{session['email']}</code>\n"
            f"📅 <b>Created:</b> {session['created_at'][:19]}\n"
            f"🔄 <b>Auto-Refresh:</b> {refresh_status}"
        )
    else:
        await message.answer(
            "⚠️ You do not have an active email.\nTap 🟢 <b>Create New Mail</b> to generate one."
        )


@dp.message(F.text == "🔴 Delete Session")
async def delete_session_handler(message: types.Message):
    user = message.from_user
    register_user(user)

    stop_auto_refresh(user.id)

    if user.id in user_mail_sessions:
        del user_mail_sessions[user.id]
        await message.answer(
            "🗑️ <b>Session deleted.</b> Auto-refresh has been stopped."
        )
    else:
        await message.answer("⚠️ No active session to delete.")


# ==================== ADMIN COMMANDS ====================
@dp.message(Command("broadcast"))
async def broadcast_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    text = message.text.replace("/broadcast", "", 1).strip()
    if not text:
        await message.answer(
            "⚠️ Usage: <code>/broadcast Your text message here</code>"
        )
        return

    sent = 0
    failed = 0
    status_msg = await message.answer(
        "📢 <i>Broadcasting message to all users...</i>"
    )

    for uid in list(db["users"].keys()):
        try:
            await bot.send_message(
                int(uid), f"📢 <b>Announcement:</b>\n\n{text}"
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"📢 <b>Broadcast Completed:</b>\n\n"
        f"✅ <b>Sent:</b> {sent}\n"
        f"❌ <b>Failed:</b> {failed}"
    )


@dp.message(Command("ban"))
async def ban_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("⚠️ Usage: <code>/ban USER_ID</code>")
        return

    try:
        target_id = int(args[1])
        if target_id not in db["banned"]:
            db["banned"].append(target_id)
            stop_auto_refresh(target_id)
            user_mail_sessions.pop(target_id, None)
            save_db(db)
            await message.answer(
                f"🚫 User <code>{target_id}</code> has been <b>banned</b>."
            )
        else:
            await message.answer("User is already banned.")
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
        target_id = int(args[1])
        if target_id in db["banned"]:
            db["banned"].remove(target_id)
            save_db(db)
            await message.answer(
                f"✅ User <code>{target_id}</code> has been <b>unbanned</b>."
            )
        else:
            await message.answer("User is not currently banned.")
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
        name = u.get("first_name", "Unknown")
        username = u.get("username", "N/A")
        text += f"• <code>{uid}</code> — {name} (@{username})\n"

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
        target_id_str = str(int(args[1]))
        target_id_int = int(args[1])

        db["warned"][target_id_str] = db["warned"].get(target_id_str, 0) + 1
        warn_count = db["warned"][target_id_str]
        save_db(db)

        await message.answer(
            f"⚠️ User <code>{target_id_str}</code> warned. Total: <b>{warn_count}/3</b>"
        )

        if warn_count >= 3:
            if target_id_int not in db["banned"]:
                db["banned"].append(target_id_int)
                stop_auto_refresh(target_id_int)
                user_mail_sessions.pop(target_id_int, None)
                save_db(db)
                await message.answer(
                    f"🚫 User <code>{target_id_str}</code> reached 3 warnings and was <b>auto-banned</b>."
                )

        try:
            await bot.send_message(
                target_id_int,
                f"⚠️ <b>Warning Received!</b>\n"
                f"You have received an administrative warning.\n"
                f"Status: <b>{warn_count}/3</b> warnings.\n<i>(3 warnings lead to an automatic ban)</i>",
            )
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

    target_id = args[1]
    if target_id in db["warned"]:
        del db["warned"][target_id]
        save_db(db)
        await message.answer(
            f"✅ Cleared warnings for user <code>{target_id}</code>."
        )
    else:
        await message.answer("User has no active warnings.")


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
        name = u.get("first_name", "Unknown")
        text += f"• <code>{uid}</code> — {name} — <b>{count}/3</b>\n"

    await message.answer(text)


@dp.message(Command("stats"))
async def stats_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        f"📊 <b>Bot Live Statistics:</b>\n\n"
        f"👥 <b>Total Users:</b> {len(db['users'])}\n"
        f"🚫 <b>Banned Users:</b> {len(db['banned'])}\n"
        f"⚠️ <b>Warned Users:</b> {len(db['warned'])}\n"
        f"📧 <b>Active Email Sessions:</b> {len(user_mail_sessions)}\n"
        f"🔄 <b>Active Auto-Refreshes:</b> {len(auto_refresh_tasks)}\n\n"
        f"📈 <b>Lifetime Metrics:</b>\n"
        f"✉️ Emails Created: {db['stats']['total_emails_created']}\n"
        f"📬 Inbox Checks: {db['stats']['total_inbox_checks']}"
    )


@dp.message(Command("topusers"))
async def topusers_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    users = sorted(
        db["users"].values(),
        key=lambda x: x.get("emails_created", 0),
        reverse=True,
    )
    text = "🏆 <b>Top 10 Active Users:</b>\n\n"

    for i, u in enumerate(users[:10], 1):
        name = u.get("first_name", "Unknown")
        created = u.get("emails_created", 0)
        checks = u.get("inbox_checks", 0)
        text += f"{i}. <b>{name}</b> — 📧 {created} created | 📬 {checks} checks\n"

    if not users:
        text = "No user activity recorded yet."

    await message.answer(text)


@dp.message(Command("userlist"))
async def userlist_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    if not db["users"]:
        await message.answer("📋 No users registered yet.")
        return

    text = "👥 <b>Registered Users List:</b>\n\n"
    for uid, u in db["users"].items():
        name = u.get("first_name", "Unknown")
        username = u.get("username", "N/A")
        joined = u.get("joined", "N/A")[:10]
        status = "🚫" if int(uid) in db["banned"] else "✅"

        text += f"{status} <code>{uid}</code> — {name} (@{username}) — Joined: {joined}\n"

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
        await message.answer("⚠️ No user data available to export.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "User ID",
            "First Name",
            "Username",
            "Joined Date",
            "Emails Created",
            "Inbox Checks",
            "Last Active",
            "Banned",
            "Warnings",
        ]
    )

    for uid, u in db["users"].items():
        writer.writerow(
            [
                uid,
                u.get("first_name", ""),
                u.get("username", ""),
                u.get("joined", ""),
                u.get("emails_created", 0),
                u.get("inbox_checks", 0),
                u.get("last_active", ""),
                "Yes" if int(uid) in db["banned"] else "No",
                db["warned"].get(uid, 0),
            ]
        )

    output.seek(0)
    csv_bytes = io.BytesIO(output.getvalue().encode("utf-8"))
    filename = (
        f"users_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )

    await message.answer_document(
        BufferedInputFile(csv_bytes.read(), filename=filename),
        caption="📁 <b>User Database Export (CSV)</b>",
    )


# ==================== AIOHTTP WEB SERVICE (Render Port Binding) ====================
async def handle_home(request):
    return web.json_response(
        {
            "status": "online",
            "service": "Smailpro Telegram Bot",
            "registered_users": len(db["users"]),
            "active_sessions": len(user_mail_sessions),
        }
    )


async def handle_health(request):
    return web.Response(text="OK", status=200)


def init_web_app():
    web_app = web.Application()
    web_app.router.add_get("/", handle_home)
    web_app.router.add_get("/health", handle_health)
    return web_app

# ==================== MAIN RUNNER ====================
async def main():
    logger.info(f"Starting Aiohttp Web Service on port {PORT}...")

    # 1. Start HTTP Server for Render port binding
    web_app = init_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()

    logger.info(f"Web server online on 0.0.0.0:{PORT}")
    logger.info("Starting Telegram Bot Long-Polling...")

    # 2. Start Bot Polling
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
