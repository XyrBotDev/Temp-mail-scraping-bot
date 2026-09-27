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

# ==================== CONFIGURATION ====================
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
    if not text:
        return []
    pattern = r'https?://[^\s<>"\')\]\},]+'
    urls = re.findall(pattern, text)
    return list(dict.fromkeys(urls))

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

# ==================== 50-WEBSITE/ENDPOINT PROVIDER POOL ====================

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
]

# 50 Distinct Service Providers & Dedicated Endpoints
PROVIDERS_50 = [
    # 1-10: Primary APIs & Gmail Gateways
    {"id": 1, "name": "Smailpro Google", "type": "smailpro", "domain": "gmail.com"},
    {"id": 2, "name": "Emailnator Gmail", "type": "emailnator", "domain": "gmail.com"},
    {"id": 3, "name": "Mail.tm Node 1", "type": "mailtm", "domain": "mailtm.me"},
    {"id": 4, "name": "Mailgw Node 1", "type": "mailgw", "domain": "mailgw.net"},
    {"id": 5, "name": "1SecMail Primary", "type": "1secmail", "domain": "1secmail.com"},
    {"id": 6, "name": "1SecMail Net", "type": "1secmail", "domain": "1secmail.net"},
    {"id": 7, "name": "1SecMail Org", "type": "1secmail", "domain": "1secmail.org"},
    {"id": 8, "name": "Esiix Mirror", "type": "1secmail", "domain": "esiix.com"},
    {"id": 9, "name": "Wwjmp Mirror", "type": "1secmail", "domain": "wwjmp.com"},
    {"id": 10, "name": "Vzadp Mirror", "type": "1secmail", "domain": "vzadp.com"},

    # 11-20: Guerrilla & TempMail.Plus Network
    {"id": 11, "name": "SharkLasers API", "type": "guerrilla", "domain": "sharklasers.com"},
    {"id": 12, "name": "GuerrillaMail Com", "type": "guerrilla", "domain": "guerrillamail.com"},
    {"id": 13, "name": "GuerrillaMail Net", "type": "guerrilla", "domain": "guerrillamail.net"},
    {"id": 14, "name": "GuerrillaMail Org", "type": "guerrilla", "domain": "guerrillamail.org"},
    {"id": 15, "name": "GuerrillaMail Biz", "type": "guerrilla", "domain": "guerrillamail.biz"},
    {"id": 16, "name": "Grr.la Gateway", "type": "guerrilla", "domain": "grr.la"},
    {"id": 17, "name": "Mailto.Plus Node", "type": "tempmailplus", "domain": "mailto.plus"},
    {"id": 18, "name": "FexPost Gateway", "type": "tempmailplus", "domain": "fexpost.com"},
    {"id": 19, "name": "FexBox Cluster", "type": "tempmailplus", "domain": "fexbox.ru"},
    {"id": 20, "name": "Mailbox UA Node", "type": "tempmailplus", "domain": "mailbox.in.ua"},

    # 21-30: Inboxes Cloud & Specialized REST Nodes
    {"id": 21, "name": "Inboxes Primary", "type": "inboxes", "domain": "inboxes.com"},
    {"id": 22, "name": "TempMail LOL v2", "type": "tempmaillol", "domain": "tempmail.lol"},
    {"id": 23, "name": "Chitthi IN Node", "type": "tempmailplus", "domain": "chitthi.in"},
    {"id": 24, "name": "RopMail Node", "type": "tempmailplus", "domain": "ropmail.com"},
    {"id": 25, "name": "Rover Info Gateway", "type": "tempmailplus", "domain": "rover.info"},
    {"id": 26, "name": "BoxiMail Cloud", "type": "tempmailplus", "domain": "boximail.com"},
    {"id": 27, "name": "Shut Net Node", "type": "tempmailplus", "domain": "shut.net"},
    {"id": 28, "name": "Any Pink Gateway", "type": "tempmailplus", "domain": "any.pink"},
    {"id": 29, "name": "Kzccv 1Sec Mirror", "type": "1secmail", "domain": "kzccv.com"},
    {"id": 30, "name": "Icznn 1Sec Mirror", "type": "1secmail", "domain": "icznn.com"},

    # 31-40: Extended Disposable Endpoints
    {"id": 31, "name": "Txcct 1Sec Mirror", "type": "1secmail", "domain": "txcct.com"},
    {"id": 32, "name": "Hubinar 1Sec Node", "type": "1secmail", "domain": "hubinar.com"},
    {"id": 33, "name": "KarenKey 1Sec Node", "type": "1secmail", "domain": "karenkey.com"},
    {"id": 34, "name": "Dcctb 1Sec Node", "type": "1secmail", "domain": "dcctb.com"},
    {"id": 35, "name": "Bbitf 1Sec Node", "type": "1secmail", "domain": "bbitf.com"},
    {"id": 36, "name": "Dpptd 1Sec Node", "type": "1secmail", "domain": "dpptd.com"},
    {"id": 37, "name": "YoMail Info Mirror", "type": "1secmail", "domain": "yomail.info"},
    {"id": 38, "name": "ArmySpy Inboxes", "type": "inboxes", "domain": "armyspy.com"},
    {"id": 39, "name": "Cuvox DE Inboxes", "type": "inboxes", "domain": "cuvox.de"},
    {"id": 40, "name": "DayRep Inboxes", "type": "inboxes", "domain": "dayrep.com"},

    # 41-50: Fallback Edge Nodes & Cloud Clusters
    {"id": 41, "name": "Einrot Inboxes", "type": "inboxes", "domain": "einrot.com"},
    {"id": 42, "name": "Fleckens Inboxes", "type": "inboxes", "domain": "fleckens.hu"},
    {"id": 43, "name": "Gustr Inboxes", "type": "inboxes", "domain": "gustr.com"},
    {"id": 44, "name": "JourRapide Inboxes", "type": "inboxes", "domain": "jourrapide.com"},
    {"id": 45, "name": "Rhyta Inboxes", "type": "inboxes", "domain": "rhyta.com"},
    {"id": 46, "name": "SuperRito Inboxes", "type": "inboxes", "domain": "superrito.com"},
    {"id": 47, "name": "TeleWorm Inboxes", "type": "inboxes", "domain": "teleworm.us"},
    {"id": 48, "name": "FirstMail LTD", "type": "tempmailplus", "domain": "firstmail.ltd"},
    {"id": 49, "name": "DropMail Engine", "type": "tempmailplus", "domain": "dropmail.me"},
    {"id": 50, "name": "Pokemail Guerrilla", "type": "guerrilla", "domain": "pokemail.net"}
]

# --- 50-ENDPOINT DISPATCHER ---
async def call_provider(prov: dict):
    p_type = prov["type"]
    dom = prov["domain"]
    t = aiohttp.ClientTimeout(total=4)
    ua = random.choice(UA_POOL)

    # 1. Smailpro Gmail
    if p_type == "smailpro":
        try:
            jar = aiohttp.CookieJar(unsafe=True)
            async with aiohttp.ClientSession(cookie_jar=jar, headers={"User-Agent": ua}, timeout=t) as s:
                await s.get("https://smailpro.com/temporary-email")
                async with s.get("https://api.smailpro.com/v2/client/create?type=google&server=google") as r:
                    if r.status == 200:
                        em = (await r.json()).get("address") or (await r.json()).get("email")
                        if em and ("@gmail.com" in em or "@googlemail.com" in em):
                            return {"email": em, "provider": prov["name"], "type": p_type, "cookies": {c.key: c.value for c in jar}, "is_gmail": True}
        except Exception:
            pass

    # 2. Emailnator Gmail
    elif p_type == "emailnator":
        try:
            jar = aiohttp.CookieJar(unsafe=True)
            async with aiohttp.ClientSession(cookie_jar=jar, timeout=t) as s:
                await s.get("https://www.emailnator.com/", headers={"User-Agent": ua})
                cookies = {c.key: c.value for c in jar}
                xsrf = unquote(cookies.get("XSRF-TOKEN", ""))
                if xsrf:
                    h = {"User-Agent": ua, "X-XSRF-TOKEN": xsrf, "Content-Type": "application/json", "Referer": "https://www.emailnator.com/"}
                    async with s.post("https://www.emailnator.com/generate-email", json={"email": ["plusGmail", "dotGmail", "gmail"]}, headers=h) as r:
                        if r.status == 200:
                            for em in (await r.json()).get("email", []):
                                if "@gmail.com" in em or "@googlemail.com" in em:
                                    return {"email": em, "provider": prov["name"], "type": p_type, "cookies": cookies, "xsrf": xsrf, "is_gmail": True}
        except Exception:
            pass

    # 3. Mail.tm
    elif p_type == "mailtm":
        try:
            async with aiohttp.ClientSession(timeout=t) as s:
                async with s.get("https://api.mail.tm/domains") as r:
                    if r.status == 200:
                        d = (await r.json())["hydra:member"][0]["domain"]
                        u = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
                        p = "".join(random.choices(string.ascii_letters + string.digits, k=12))
                        em = f"{u}@{d}"
                        async with s.post("https://api.mail.tm/accounts", json={"address": em, "password": p}) as reg:
                            if reg.status == 201:
                                async with s.post("https://api.mail.tm/token", json={"address": em, "password": p}) as tok:
                                    return {"email": em, "provider": prov["name"], "type": p_type, "token": (await tok.json()).get("token"), "is_gmail": False}
        except Exception:
            pass

    # 4. Mailgw
    elif p_type == "mailgw":
        try:
            async with aiohttp.ClientSession(timeout=t) as s:
                async with s.get("https://api.mailgw.net/domains") as r:
                    if r.status == 200:
                        d = (await r.json())["hydra:member"][0]["domain"]
                        u = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
                        p = "Pass@" + "".join(random.choices(string.digits, k=6))
                        em = f"{u}@{d}"
                        async with s.post("https://api.mailgw.net/accounts", json={"address": em, "password": p}) as reg:
                            if reg.status == 201:
                                async with s.post("https://api.mailgw.net/token", json={"address": em, "password": p}) as tok:
                                    return {"email": em, "provider": prov["name"], "type": p_type, "token": (await tok.json()).get("token"), "is_gmail": False}
        except Exception:
            pass

    # 5. 1SecMail Cluster
    elif p_type == "1secmail":
        try:
            u = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
            return {"email": f"{u}@{dom}", "provider": prov["name"], "type": p_type, "is_gmail": False}
        except Exception:
            pass

    # 6. Guerrilla Mail Cluster
    elif p_type == "guerrilla":
        try:
            async with aiohttp.ClientSession(timeout=t) as s:
                async with s.get("https://api.guerrillamail.com/ajax.php?f=get_email_address") as r:
                    if r.status == 200:
                        d = await r.json()
                        sid = d["sid_token"]
                        async with s.get(f"https://api.guerrillamail.com/ajax.php?f=set_email_user&email_user={d['email_user']}&lang=en&site={dom}&sid_token={sid}") as set_r:
                            set_d = await set_r.json()
                            return {"email": set_d.get("email_addr", d["email_addr"]), "provider": prov["name"], "type": p_type, "sid_token": sid, "is_gmail": False}
        except Exception:
            pass

    # 7. TempMail.Plus Cluster
    elif p_type == "tempmailplus":
        u = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        return {"email": f"{u}@{dom}", "provider": prov["name"], "type": p_type, "user": u, "domain": dom, "is_gmail": False}

    # 8. Inboxes Cluster
    elif p_type == "inboxes":
        u = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        return {"email": f"{u}@{dom}", "provider": prov["name"], "type": p_type, "is_gmail": False}

    # 9. TempMail LOL
    elif p_type == "tempmaillol":
        try:
            async with aiohttp.ClientSession(timeout=t) as s:
                async with s.get("https://api.tempmail.lol/v2/inbox/create") as r:
                    if r.status == 200:
                        d = await r.json()
                        return {"email": d["address"], "provider": prov["name"], "type": p_type, "token": d["token"], "is_gmail": False}
        except Exception:
            pass

    return None

# ==================== MASTER DISPATCHER (50 SITES SEQUENTIAL) ====================
async def generate_from_50_websites():
    # Priority: First 2 Gmail Providers
    for prov in PROVIDERS_50[:2]:
        res = await call_provider(prov)
        if res and res.get("email"):
            return res

    # Next: Rotate Remaining 48 Multi-Domain Providers
    remaining = PROVIDERS_50[2:]
    random.shuffle(remaining)

    for prov in remaining:
        res = await call_provider(prov)
        if res and res.get("email"):
            return res

    # Guaranteed Fallback
    return await call_provider(PROVIDERS_50[16])
    # ==================== UNIVERSAL 50-PROVIDER INBOX CHECKER ====================
async def check_50_inbox(session_data: dict):
    if not session_data:
        return []

    p_type = session_data.get("type")
    em = session_data.get("email", "")
    t = aiohttp.ClientTimeout(total=8)
    ua = random.choice(UA_POOL)

    try:
        # 1. Smailpro Inbox
        if p_type == "smailpro":
            jar = aiohttp.CookieJar(unsafe=True)
            async with aiohttp.ClientSession(cookie_jar=jar, headers={"User-Agent": ua}, timeout=t) as s:
                for k, v in session_data.get("cookies", {}).items():
                    jar.update_cookies({k: v})
                async with s.get(f"https://api.smailpro.com/v2/client/inbox?email={em}") as r:
                    if r.status == 200:
                        return (await r.json()).get("messages", [])

        # 2. Emailnator Inbox
        elif p_type == "emailnator":
            jar = aiohttp.CookieJar(unsafe=True)
            h = {
                "User-Agent": ua,
                "X-XSRF-TOKEN": session_data.get("xsrf", ""),
                "Content-Type": "application/json",
                "Referer": "https://www.emailnator.com/"
            }
            async with aiohttp.ClientSession(cookie_jar=jar, headers=h, timeout=t) as s:
                for k, v in session_data.get("cookies", {}).items():
                    jar.update_cookies({k: v})
                async with s.post("https://www.emailnator.com/message-list", json={"email": em}) as r:
                    if r.status == 200:
                        raw = (await r.json()).get("messageData", [])
                        msgs = []
                        for m in raw:
                            mid = m.get("messageID")
                            if mid and mid != "AD_CONTAINER":
                                async with s.post("https://www.emailnator.com/message-list", json={"email": em, "messageID": mid}) as b:
                                    body = re.sub(r'<[^>]+>', ' ', await b.text()).strip()
                                    msgs.append({
                                        "id": mid,
                                        "from": m.get("from", "Unknown"),
                                        "subject": m.get("subject", "No Subject"),
                                        "body": body
                                    })
                        return msgs

        # 3. Mail.tm & Mailgw
        elif p_type in ["mailtm", "mailgw"]:
            base = "https://api.mail.tm" if p_type == "mailtm" else "https://api.mailgw.net"
            h = {"Authorization": f"Bearer {session_data.get('token')}"}
            async with aiohttp.ClientSession(headers=h, timeout=t) as s:
                async with s.get(f"{base}/messages") as r:
                    if r.status == 200:
                        raw = (await r.json()).get("hydra:member", [])
                        msgs = []
                        for m in raw:
                            async with s.get(f"{base}/messages/{m['id']}") as d:
                                det = await d.json()
                                msgs.append({
                                    "id": m["id"],
                                    "from": det.get("from", {}).get("address", "Unknown"),
                                    "subject": det.get("subject", ""),
                                    "body": det.get("text", "") or det.get("intro", "")
                                })
                        return msgs

        # 4. 1SecMail Cluster
        elif p_type == "1secmail":
            login, domain = em.split("@")
            async with aiohttp.ClientSession(timeout=t) as s:
                async with s.get(f"https://www.1secmail.com/api/v1/?action=getMessages&login={login}&domain={domain}") as r:
                    if r.status == 200:
                        raw = await r.json()
                        msgs = []
                        for m in raw:
                            async with s.get(f"https://www.1secmail.com/api/v1/?action=readMessage&login={login}&domain={domain}&id={m['id']}") as d:
                                det = await d.json()
                                msgs.append({
                                    "id": str(m["id"]),
                                    "from": det.get("from", "Unknown"),
                                    "subject": det.get("subject", ""),
                                    "body": det.get("textBody") or det.get("body", "")
                                })
                        return msgs

        # 5. Guerrilla Mail Cluster
        elif p_type == "guerrilla":
            sid = session_data.get("sid_token")
            async with aiohttp.ClientSession(timeout=t) as s:
                async with s.get(f"https://api.guerrillamail.com/ajax.php?f=check_email&sid_token={sid}&seq=0") as r:
                    if r.status == 200:
                        raw = (await r.json()).get("list", [])
                        return [{
                            "id": m["mail_id"],
                            "from": m["mail_from"],
                            "subject": m["mail_subject"],
                            "body": m.get("mail_excerpt", "")
                        } for m in raw]

        # 6. TempMail.Plus Cluster
        elif p_type == "tempmailplus":
            u = session_data.get("user")
            dom = session_data.get("domain", "mailto.plus")
            async with aiohttp.ClientSession(timeout=t) as s:
                async with s.get(f"https://tempmail.plus/api/mails?email={u}%40{dom}&limit=5") as r:
                    if r.status == 200:
                        raw = (await r.json()).get("mail_list", [])
                        msgs = []
                        for m in raw:
                            async with s.get(f"https://tempmail.plus/api/mails/{m['mail_id']}?email={u}%40{dom}") as d:
                                det = await d.json()
                                msgs.append({
                                    "id": str(m["mail_id"]),
                                    "from": det.get("from_mail", "Unknown"),
                                    "subject": det.get("subject", ""),
                                    "body": det.get("text", "")
                                })
                        return msgs

        # 7. Inboxes Cluster
        elif p_type == "inboxes":
            login, domain = em.split("@")
            async with aiohttp.ClientSession(timeout=t) as s:
                async with s.get(f"https://inboxes.com/api/v2/inbox/{login}@{domain}") as r:
                    if r.status == 200:
                        raw = (await r.json()).get("msgs", [])
                        return [{
                            "id": m["id"],
                            "from": m.get("f", "Unknown"),
                            "subject": m.get("s", ""),
                            "body": m.get("b", "")
                        } for m in raw]

        # 8. TempMail LOL
        elif p_type == "tempmaillol":
            async with aiohttp.ClientSession(timeout=t) as s:
                async with s.get(f"https://api.tempmail.lol/v2/inbox?token={session_data.get('token')}") as r:
                    if r.status == 200:
                        raw = (await r.json()).get("emails", [])
                        return [{
                            "id": m.get("id", str(random.random())),
                            "from": m.get("from", "Unknown"),
                            "subject": m.get("subject", ""),
                            "body": m.get("body", "")
                        } for m in raw]

    except Exception as e:
        logger.error(f"Inbox read error for provider {p_type}: {e}")

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
            messages = await check_50_inbox(session)

            for msg in messages:
                msg_id = str(msg.get("id") or (msg.get("subject", "") + msg.get("from", "")))
                if msg_id not in seen_ids:
                    seen_ids.add(msg_id)

                    sender = msg.get("from", "Unknown")
                    subject = msg.get("subject", "No Subject")
                    body = msg.get("body") or msg.get("text") or "No content available."

                    urls = extract_urls(body)

                    msg_text = (
                        f"📩 <b>New Email Received!</b>\n\n"
                        f"👤 <b>From:</b> <code>{html.escape(str(sender))}</code>\n"
                        f"📌 <b>Subject:</b> {html.escape(str(subject))}\n\n"
                        f"📝 <b>Content:</b>\n{html.escape(str(body))}"
                    )

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
            f"Thank you for starting the <b>50-Engine Temp Mail Bot</b>! 🎉\n\n"
            f"⚡ <b>Available Actions:</b>\n"
            f"🟢 <b>Create New Mail</b> — Generate instant Gmail / Temp Mail\n"
            f"🔵 <b>Check Inbox</b> — Read incoming emails & OTPs\n"
            f"🟡 <b>Current Mail</b> — View your active email address\n"
            f"🔴 <b>Delete Session</b> — Discard email & stop background refresh\n\n"
            f"⏱️ <i>Note: Once created, inbox auto-refreshes every 30 seconds!</i>\n\n"
            f"Tap a button below to get started 👇"
        )
    else:
        text = (
            f"👋 <b>Welcome back, {mention}!</b>\n\n"
            f"Use the buttons below to manage your temporary email 👇"
        )

    await message.answer(text, reply_markup=get_main_keyboard())


@dp.message(F.text == "🟢 Create New Mail")
async def create_mail_handler(message: types.Message):
    user = message.from_user
    if is_banned(user.id):
        await message.answer("🚫 You are banned from using this bot.")
        return

    register_user(user)
    status_msg = await message.answer("⏳ <i>Searching across 50 Temp-Mail servers...</i>")

    mail_data = await generate_from_50_websites()

    if mail_data:
        stop_auto_refresh(user.id)

        user_mail_sessions[user.id] = {
            **mail_data,
            "created_at": datetime.now().isoformat()
        }

        email = mail_data["email"]
        provider_name = mail_data.get("provider", "Server")
        is_gmail = mail_data.get("is_gmail", False)
        status_tag = "🎯 <b>Verified @gmail.com</b>" if is_gmail else f"🌐 <b>Server: {provider_name}</b>"

        uid = str(user.id)
        if uid in db["users"]:
            db["users"][uid]["emails_created"] = db["users"][uid].get("emails_created", 0) + 1
            db["stats"]["total_emails_created"] += 1
            save_db(db)

        await status_msg.edit_text(
            f"✅ <b>Your Temporary Email is Ready!</b>\n"
            f"{status_tag}\n\n"
            f"<code>{email}</code>\n\n"
            f"📋 <i>Tap the email above to copy it.</i>\n"
            f"🔄 <b>Auto-Refresh Active:</b> Checking every 30 seconds.\n"
            f"♾️ <b>Unlimited:</b> Fresh session used!\n\n"
            f"You can also tap 🔵 <b>Check Inbox</b> anytime."
        )

        start_auto_refresh(user.id, message.chat.id)
    else:
        await status_msg.edit_text("❌ <b>All 50 servers busy.</b> Please try again in a moment.")


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

    messages = await check_50_inbox(session)

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
            f"👤 <b>From:</b> <code>{html.escape(str(sender))}</code>\n"
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
        provider_name = session.get("provider", "Server")
        await message.answer(
            f"ℹ️ <b>Active Session:</b>\n\n"
            f"📧 <b>Email:</b> <code>{session['email']}</code>\n"
            f"🌐 <b>Server:</b> {provider_name}\n"
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
        "service": "50-Engine Temp Mail Bot",
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
