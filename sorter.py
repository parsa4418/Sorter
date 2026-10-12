import os, asyncio, logging
from aiohttp import web
from telethon import TelegramClient, events, Button, functions, types, utils
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, FloodWaitError

logging.basicConfig(level=logging.INFO)

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
SESSION_FILE = "user.session.txt"

# اگه یه خواننده با چند املا ثبت شده، اینجا یکی‌شون کن (همه چیز lowercase)
ALIASES = {
    "hayedeh": "هایده",
    "googoosh": "گوگوش",
    "matin fattahi": "متین فتاحی",
}

bot = TelegramClient("bot", API_ID, API_HASH)
user = None
st = {"step": None, "phone": "", "code": "", "hash": None}


def load_session():
    if os.environ.get("SESSION_STRING"):
        return os.environ["SESSION_STRING"]
    if os.path.exists(SESSION_FILE):
        return open(SESSION_FILE).read().strip()
    return ""


def new_user_client(s=""):
    return TelegramClient(StringSession(s), API_ID, API_HASH)


def norm(name):
    n = " ".join((name or "").split()).casefold()
    return ALIASES.get(n, n)


def info(doc):
    for a in doc.attributes:
        if isinstance(a, types.DocumentAttributeAudio):
            return (a.performer or "").strip(), (a.title or "").strip()
    return "", ""


async def get_music():
    docs = []
    while True:
        r = await user(functions.users.GetSavedMusicRequest(
            id=types.InputUserSelf(), offset=len(docs), limit=100, hash=0))
        got = getattr(r, "documents", None)
        if not got:
            break
        docs += got
        if len(docs) >= r.count:
            break
    return docs


def make_plan(docs):
    groups = {}
    for d in docs:
        groups.setdefault(norm(info(d)[0]), []).append(d)
    idx = {k: i for i, k in enumerate(groups)}
    keys = sorted(groups, key=lambda k: (k == "", -len(groups[k]), idx[k]))
    new = [d for k in keys for d in groups[k]]
    return keys, groups, new


def label(k, groups):
    if k == "":
        return "بدون خواننده"
    for d in groups[k]:
        p = info(d)[0]
        if p:
            return p
    return k


ORD = ["اول", "دوم", "سوم", "چهارم", "پنجم", "ششم", "هفتم", "هشتم", "نهم", "دهم",
       "یازدهم", "دوازدهم", "سیزدهم", "چهاردهم", "پانزدهم", "شانزدهم", "هفدهم", "هجدهم", "نوزدهم", "بیستم"]


def order_text(keys, groups):
    lines = []
    for i, k in enumerate(keys):
        n = ORD[i] if i < len(ORD) else f"{i + 1}م"
        lines.append(f"{n}: {label(k, groups)}")
    return "\n".join(lines)


def menu():
    return [
        [Button.inline("🎵 پیش‌نمایش ترتیب جدید", b"preview")],
        [Button.inline("✅ اعمال ترتیب", b"apply")],
        [Button.inline("🔑 ورود / ورود مجدد", b"login")],
    ]


def keypad():
    rows = [[Button.inline(str(n), f"k:{n}".encode()) for n in r] for r in ((1, 2, 3), (4, 5, 6), (7, 8, 9))]
    rows.append([Button.inline("⌫", b"k:del"), Button.inline("0", b"k:0"), Button.inline("✔️ تایید", b"k:ok")])
    return rows


def owner_only(ev):
    return ev.sender_id == OWNER_ID


async def logged_in():
    return user is not None and await user.is_user_authorized()


@bot.on(events.NewMessage(pattern="/start"))
async def start(ev):
    if not owner_only(ev):
        return
    ok = await logged_in()
    await ev.respond("پنل مدیریت آهنگ‌های پروفایل\n" + ("✅ وارد شدی" if ok else "❌ اول وارد شو"), buttons=menu())


@bot.on(events.CallbackQuery)
async def cb(ev):
    global user
    if ev.sender_id != OWNER_ID:
        return await ev.answer()
    d = ev.data.decode()

    if d == "login":
        st.update(step="phone", phone="", code="", hash=None)
        if user:
            await user.disconnect()
        user = new_user_client()
        await user.connect()
        return await ev.respond("شماره‌ت رو با کد کشور بفرست (مثل +98912...)")

    if d.startswith("k:") and st["step"] == "code":
        v = d[2:]
        if v == "del":
            st["code"] = st["code"][:-1]
        elif v == "ok":
            try:
                await user.sign_in(st["phone"], st["code"], phone_code_hash=st["hash"])
            except SessionPasswordNeededError:
                st["step"] = "2fa"
                return await ev.edit("رمز تایید دومرحله‌ای رو بفرست:")
            except Exception as e:
                st["code"] = ""
                return await ev.edit(f"❌ {e}\nدوباره کد رو بزن:", buttons=keypad())
            return await finish_login(ev)
        else:
            st["code"] += v
        shown = st["code"] or "—"
        return await ev.edit(f"کد: {shown}", buttons=keypad())

    if d in ("preview", "apply"):
        if not await logged_in():
            return await ev.respond("اول وارد شو.", buttons=menu())
        if not hasattr(functions.users, "GetSavedMusicRequest"):
            return await ev.respond("نسخه‌ی Telethon قدیمیه؛ requirements رو آپدیت کن.")
        docs = await get_music()
        if not docs:
            return await ev.respond("آهنگی تو پروفایلت نیست.")
        keys, groups, new = make_plan(docs)
        if d == "preview":
            lines = [f"{i+1}. {label(k, groups)} — {len(groups[k])} آهنگ" for i, k in enumerate(keys)]
            text = "ترتیب جدید خواننده‌ها:\n" + "\n".join(lines)
            return await ev.respond(text[:4000], buttons=menu())
        await ev.respond(f"در حال اعمال ({len(docs)} آهنگ)...")
        asyncio.create_task(apply_order(ev, docs, new, order_text(keys, groups)))


async def finish_login(ev):
    open(SESSION_FILE, "w").write(user.session.save())
    st["step"] = None
    await ev.edit("✅ وارد شدی.", buttons=menu())


@bot.on(events.NewMessage)
async def msgs(ev):
    if not owner_only(ev) or not ev.raw_text or ev.raw_text.startswith("/"):
        return
    if st["step"] == "phone":
        phone = ev.raw_text.strip().replace(" ", "")
        try:
            sent = await user.send_code_request(phone)
        except Exception as e:
            return await ev.respond(f"❌ {e}")
        st.update(phone=phone, hash=sent.phone_code_hash, step="code", code="")
        await ev.respond("کد: —", buttons=keypad())
    elif st["step"] == "2fa":
        pw = ev.raw_text
        await ev.delete()
        try:
            await user.sign_in(password=pw)
        except Exception as e:
            return await ev.respond(f"❌ {e}\nدوباره رمز رو بفرست:")
        open(SESSION_FILE, "w").write(user.session.save())
        st["step"] = None
        await ev.respond("✅ وارد شدی.", buttons=menu())


async def apply_order(ev, docs, new, summary):
    # فقط از اولین جایی که ترتیب فرق داره شروع می‌کنیم
    cur = [d.id for d in docs]
    tgt = [d.id for d in new]
    start_i = next((i for i in range(len(tgt)) if cur[i] != tgt[i]), None)
    if start_i is None:
        return await ev.respond(("✅ از قبل مرتب بود.\n\n" + summary)[:4000], buttons=menu())
    prev = utils.get_input_document(new[start_i - 1]) if start_i > 0 else None
    done = 0
    for d in new[start_i:]:
        inp = utils.get_input_document(d)
        while True:
            try:
                kw = {"id": inp, "unsave": False}
                if prev:
                    kw["after_id"] = prev
                await user(functions.account.SaveMusicRequest(**kw))
                break
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
        prev = inp
        done += 1
        await asyncio.sleep(1.5)
        if done % 10 == 0:
            await ev.respond(f"{done} از {len(new) - start_i} ...")
    await ev.respond(("✅ ترتیب جدید اعمال شد:\n\n" + summary)[:4000], buttons=menu())


async def health(_):
    return web.Response(text="ok")


async def main():
    global user
    await bot.start(bot_token=BOT_TOKEN)
    s = load_session()
    if s:
        user = new_user_client(s)
        await user.connect()
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    await bot.run_until_disconnected()


asyncio.run(main())
