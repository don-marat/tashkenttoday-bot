import os
import logging
import requests
import time
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "568226286376483")
FB_PAGE_TOKEN_ENV = os.getenv("FB_PAGE_TOKEN")
FB_USER_TOKEN_ENV = os.getenv("FB_USER_LONG_TOKEN") or os.getenv("FB_USER_TOKEN")
FB_APP_ID = os.getenv("FB_APP_ID", "1301269625209688")
FB_APP_SECRET = os.getenv("FB_APP_SECRET")
THREADS_USER_ID = os.getenv("THREADS_USER_ID", "27092394720363294")
THREADS_TOKEN_ENV = os.getenv("THREADS_TOKEN")
SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL", "@tashkenttodayuz")
SOURCE_USERNAME = "tashkenttodayuz"

THREADS_TOKEN_FILE = os.getenv("THREADS_TOKEN_FILE", "threads_token.txt")
FB_PAGE_TOKEN_FILE = os.getenv("FB_TOKEN_FILE", "fb_page_token.txt")
FB_USER_TOKEN_FILE = os.getenv("FB_USER_TOKEN_FILE", "fb_user_token.txt")

RAILWAY_API_TOKEN = os.getenv("RAILWAY_API_TOKEN")
RAILWAY_PROJECT_ID = os.getenv("RAILWAY_PROJECT_ID")
RAILWAY_ENV_ID = os.getenv("RAILWAY_ENVIRONMENT_ID") or os.getenv("RAILWAY_ENV_ID")
RAILWAY_SERVICE_ID = os.getenv("RAILWAY_SERVICE_ID")

def load_token(path, env_val):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                t = f.read().strip()
                if t and len(t) > 20:
                    logger.info(f"Token loaded from {path}")
                    return t
    except Exception as e:
        logger.error(f"load {path}: {e}")
    return env_val

def save_token(path, token):
    try:
        with open(path, "w") as f:
            f.write(token)
        logger.info(f"Token saved to {path}")
    except Exception as e:
        logger.error(f"save {path}: {e}")

THREADS_TOKEN = load_token(THREADS_TOKEN_FILE, THREADS_TOKEN_ENV)
FB_PAGE_TOKEN = load_token(FB_PAGE_TOKEN_FILE, FB_PAGE_TOKEN_ENV)
FB_USER_TOKEN = load_token(FB_USER_TOKEN_FILE, FB_USER_TOKEN_ENV)

def railway_update(name, value):
    if not (RAILWAY_API_TOKEN and RAILWAY_PROJECT_ID and RAILWAY_ENV_ID and RAILWAY_SERVICE_ID):
        logger.info(f"Railway update skipped for {name}: set RAILWAY_API_TOKEN/PROJECT_ID/ENV_ID/SERVICE_ID")
        return False
    try:
        q = "mutation variableUpsert($input: VariableUpsertInput!) { variableUpsert(input: $input) }"
        vars_ = {"input": {"projectId": RAILWAY_PROJECT_ID, "environmentId": RAILWAY_ENV_ID, "serviceId": RAILWAY_SERVICE_ID, "name": name, "value": value}}
        headers = {"Authorization": f"Bearer {RAILWAY_API_TOKEN}", "Content-Type": "application/json"}
        r = requests.post("https://backboard.railway.app/graphql/v2", json={"query": q, "variables": vars_}, headers=headers, timeout=20)
        logger.info(f"Railway {name}: {r.status_code} {r.text[:400]}")
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Railway {name} error: {e}")
        return False

# === THREADS ===
def refresh_threads_token():
    global THREADS_TOKEN
    if not THREADS_TOKEN:
        return None
    try:
        url = f"https://graph.threads.net/refresh_access_token?grant_type=th_refresh_token&access_token={THREADS_TOKEN}"
        r = requests.get(url, timeout=20)
        logger.info(f"Threads refresh: {r.text[:500]}")
        new_t = r.json().get("access_token")
        if new_t:
            THREADS_TOKEN = new_t
            save_token(THREADS_TOKEN_FILE, new_t)
            railway_update("THREADS_TOKEN", new_t)
            logger.info(f"✅ Threads refreshed expires_in={r.json().get('expires_in')}")
            return new_t
        logger.error(f"Threads refresh failed: {r.text}")
        return None
    except Exception as e:
        logger.error(f"refresh_threads error: {e}")
        return None

def check_threads_expiry():
    if not THREADS_TOKEN:
        return
    try:
        url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}?fields=id,username&access_token={THREADS_TOKEN}"
        r = requests.get(url, timeout=15)
        if any(x in r.text for x in ["Session has expired", "Error validating access token", "Invalid OAuth"]):
            logger.warning("Threads token expired -> refresh")
            refresh_threads_token()
    except Exception as e:
        logger.error(f"check threads: {e}")

# === FACEBOOK ===
def refresh_fb_user_token():
    global FB_USER_TOKEN
    if not (FB_APP_ID and FB_APP_SECRET and FB_USER_TOKEN):
        logger.info("FB user refresh skipped: need FB_APP_ID, FB_APP_SECRET, FB_USER_LONG_TOKEN")
        return FB_USER_TOKEN
    try:
        url = f"https://graph.facebook.com/v20.0/oauth/access_token?grant_type=fb_exchange_token&client_id={FB_APP_ID}&client_secret={FB_APP_SECRET}&fb_exchange_token={FB_USER_TOKEN}"
        r = requests.get(url, timeout=20)
        logger.info(f"FB user refresh: {r.text[:500]}")
        new_t = r.json().get("access_token")
        if new_t:
            FB_USER_TOKEN = new_t
            save_token(FB_USER_TOKEN_FILE, new_t)
            railway_update("FB_USER_LONG_TOKEN", new_t)
            logger.info("✅ FB User token refreshed")
            return new_t
        logger.warning(f"FB user refresh no token: {r.text[:500]}")
        return FB_USER_TOKEN
    except Exception as e:
        logger.error(f"refresh_fb_user: {e}")
        return FB_USER_TOKEN

def refresh_fb_page_token():
    global FB_PAGE_TOKEN, FB_USER_TOKEN
    if not FB_USER_TOKEN:
        logger.info("FB page refresh skipped: no FB_USER_LONG_TOKEN")
        return None
    try:
        FB_USER_TOKEN = refresh_fb_user_token() or FB_USER_TOKEN
        url = f"https://graph.facebook.com/v20.0/me?fields=accounts{{id,name,access_token}}&access_token={FB_USER_TOKEN}"
        r = requests.get(url, timeout=20)
        logger.info(f"FB accounts: {r.text[:800]}")
        data = r.json()
        accounts = []
        if "accounts" in data:
            accounts = data["accounts"].get("data", [])
        elif "data" in data:
            accounts = data["data"]
        for acc in accounts:
            if acc.get("id") == FB_PAGE_ID:
                new_t = acc.get("access_token")
                if new_t:
                    FB_PAGE_TOKEN = new_t
                    save_token(FB_PAGE_TOKEN_FILE, new_t)
                    railway_update("FB_PAGE_TOKEN", new_t)
                    logger.info(f"✅ FB Page token refreshed len={len(new_t)}")
                    return new_t
        logger.error(f"FB Page {FB_PAGE_ID} not found in {accounts}")
        return None
    except Exception as e:
        logger.error(f"refresh_fb_page: {e}")
        return None

def check_fb_expiry():
    if not FB_PAGE_TOKEN:
        return
    try:
        url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}?fields=id,name&access_token={FB_PAGE_TOKEN}"
        r = requests.get(url, timeout=15)
        if any(x in r.text for x in ["Session has expired", "Error validating access token", "Invalid OAuth"]):
            logger.warning("FB Page token expired -> refresh")
            refresh_fb_page_token()
    except Exception as e:
        logger.error(f"check fb: {e}")

def get_post_link(mid):
    return f"https://t.me/{SOURCE_USERNAME}/{mid}"

def format_text_threads(text, mid):
    link = get_post_link(mid)
    title = text.strip().split("\n")[0].strip()
    return f"{title}\n\nПодробнее: {link}"

def format_text_facebook(text, mid):
    link = get_post_link(mid)
    clean = text.strip()
    if not clean:
        return f"Подробнее: {link}"
    title = clean.split("\n")[0].strip()
    rest = clean[len(title):].strip()
    paras = [p.strip() for p in rest.split("\n\n") if p.strip()]
    first = paras[0] if paras else ""
    if len(first) > 1000:
        first = first[:997] + "..."
    if first:
        return f"{title}\n\n{first}\n\nПодробнее: {link}"
    else:
        return f"{title}\n\nПодробнее: {link}"

def upload_to_public_host(tg_url):
    try:
        logger.info(f"Downloading TG file: {tg_url[:80]}...")
        img_data = requests.get(tg_url, timeout=20).content
        if len(img_data) < 1000:
            return None
        try:
            r = requests.post("https://catbox.moe/user/api.php", data={"reqtype": "fileupload"}, files={"fileToUpload": ("image.jpg", img_data, "image/jpeg")}, timeout=20)
            if r.status_code == 200 and r.text.startswith("https://"):
                logger.info(f"Uploaded to catbox: {r.text.strip()}")
                return r.text.strip()
        except Exception as e:
            logger.error(f"Catbox error: {e}")
        try:
            r = requests.post("https://0x0.st", files={"file": ("image.jpg", img_data, "image/jpeg")}, timeout=20)
            if r.status_code == 200 and r.text.startswith("https://"):
                logger.info(f"Uploaded to 0x0.st: {r.text.strip()}")
                return r.text.strip()
        except Exception as e:
            logger.error(f"0x0 error: {e}")
        return None
    except Exception as e:
        logger.error(f"upload error: {e}")
        return None

def post_to_facebook(text, tg_img=None, pub_img=None):
    try:
        img_url = pub_img or tg_img
        if img_url:
            url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/photos"
            data = {"caption": text, "url": img_url, "access_token": FB_PAGE_TOKEN}
        else:
            url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/feed"
            data = {"message": text, "access_token": FB_PAGE_TOKEN}
        r = requests.post(url, data=data, timeout=30)
        logger.info(f"FB OK: {r.status_code} {r.text[:500]}")
        if any(x in r.text for x in ["Session has expired", "Error validating access token"]):
            if refresh_fb_page_token():
                data["access_token"] = FB_PAGE_TOKEN
                r = requests.post(url, data=data, timeout=30)
                logger.info(f"FB RETRY: {r.status_code} {r.text[:500]}")
        return r.json()
    except Exception as e:
        logger.error(f"FB error: {e}")

def post_to_threads(text, tg_img=None, pub_img=None):
    try:
        create_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
        short_text = text[:480] if len(text) > 480 else text
        img_to_use = pub_img
        if img_to_use:
            payload = {"media_type": "IMAGE", "text": short_text, "image_url": img_to_use, "access_token": THREADS_TOKEN}
            r = requests.post(create_url, data=payload, timeout=30)
            logger.info(f"Threads IMAGE: {r.text[:500]}")
            cid = r.json().get("id")
            if cid:
                time.sleep(4)
                pub_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
                r2 = requests.post(pub_url, data={"creation_id": cid, "access_token": THREADS_TOKEN}, timeout=30)
                logger.info(f"Threads publish IMAGE: {r2.text[:500]}")
                if r2.status_code == 200 and r2.json().get("id"):
                    return r2.json()
        payload = {"media_type": "TEXT", "text": short_text, "access_token": THREADS_TOKEN}
        r = requests.post(create_url, data=payload, timeout=30)
        logger.info(f"Threads TEXT: {r.text[:500]}")
        cid = r.json().get("id")
        if not cid:
            if any(x in r.text for x in ["Session has expired", "Error validating access token"]):
                if refresh_threads_token():
                    payload["access_token"] = THREADS_TOKEN
                    r = requests.post(create_url, data=payload, timeout=30)
                    cid = r.json().get("id")
            if not cid:
                return
        time.sleep(2)
        pub_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
        r2 = requests.post(pub_url, data={"creation_id": cid, "access_token": THREADS_TOKEN}, timeout=30)
        logger.info(f"Threads publish TEXT: {r2.text[:500]}")
        return r2.json()
    except Exception as e:
        logger.error(f"Threads error: {e}")

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post:
        return
    raw_text = post.text or post.caption or ""
    if not raw_text:
        return
    mid = post.message_id
    fb_text = format_text_facebook(raw_text, mid)
    th_text = format_text_threads(raw_text, mid)
    tg_img = None
    pub_img = None
    if post.photo:
        file = await post.photo[-1].get_file()
        tg_img = file.file_path
        pub_img = upload_to_public_host(tg_img)
    elif post.video:
        try:
            file = await post.video.get_file()
            tg_img = file.file_path
        except:
            pass
    logger.info(f"Новый пост {mid}: {raw_text[:80]}... tg_img={bool(tg_img)} public={pub_img}")
    post_to_facebook(fb_text, tg_img, pub_img)
    post_to_threads(th_text, tg_img, pub_img)

async def auto_refresh_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("⏰ Авто-проверка FB + Threads...")
    check_fb_expiry()
    check_threads_expiry()
    fb_new = refresh_fb_page_token()
    th_new = refresh_threads_token()
    if fb_new:
        logger.info("✅ FB auto-refresh OK")
    if th_new:
        logger.info("✅ Threads auto-refresh OK")

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Нет TELEGRAM_BOT_TOKEN!")
        return
    try:
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=10)
        logger.info("Webhook удален")
    except:
        pass
    global THREADS_TOKEN, FB_PAGE_TOKEN, FB_USER_TOKEN
    THREADS_TOKEN = load_token(THREADS_TOKEN_FILE, THREADS_TOKEN_ENV)
    FB_PAGE_TOKEN = load_token(FB_PAGE_TOKEN_FILE, FB_PAGE_TOKEN_ENV)
    FB_USER_TOKEN = load_token(FB_USER_TOKEN_FILE, FB_USER_TOKEN_ENV)
    check_fb_expiry()
    check_threads_expiry()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))
    if app.job_queue:
        app.job_queue.run_repeating(auto_refresh_job, interval=24*60*60, first=60)
        logger.info("JobQueue: авто-рефреш FB+Threads каждые 24ч")
    logger.info(f"Бот запущен {SOURCE_CHANNEL} -> FB:{FB_PAGE_ID} + Threads:{THREADS_USER_ID} | Авто-обновление FB+Threads: ВКЛ (Вариант B)")
    app.run_polling(allowed_updates=["channel_post"], poll_interval=10.0, timeout=30, drop_pending_updates=True, close_loop=False)

if __name__ == "__main__":
    main()
