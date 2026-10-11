import os
import logging
import requests
import time
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters
from datetime import datetime, timezone, timedelta
import json
import re

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        try:
            from __main__ import SOURCE_CHANNEL as SC, POSTED_IDS as PIDS, TASHKENT_TZ as TZ
            now = datetime.now(TZ).strftime('%Y-%m-%d %H:%M')
            msg = f"Bot running {SC} | {len(PIDS)} posts | {now} | OK"
        except:
            msg = "OK"
        self.wfile.write(msg.encode('utf-8'))
    def log_message(self, format, *args):
        return

def start_keepalive():
    port = int(os.getenv("PORT", "10000"))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        logger.info(f"Keepalive HTTP server started on 0.0.0.0:{port}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Keepalive failed: {e}")

MEDIA_GROUP_CACHE = defaultdict(list)
MEDIA_GROUP_LAST_TEXT = {}

WORK_START_HOUR = int(os.getenv("WORK_START_HOUR", "9"))
WORK_END_HOUR = int(os.getenv("WORK_END_HOUR", "21"))
TASHKENT_TZ = timezone(timedelta(hours=5))
DISABLE_VIDEO = os.getenv("DISABLE_VIDEO", "true").lower() in ("1", "true", "yes")
THREADS_DISABLE_VIDEO = os.getenv("THREADS_DISABLE_VIDEO", "true").lower() in ("1", "true", "yes")

FB_ENABLED = os.getenv("FB_ENABLED", "true").lower() in ("1", "true", "yes")
FB_PAGE_ENABLED = os.getenv("FB_PAGE_ENABLED", "true").lower() in ("1", "true", "yes")
THREADS_ENABLED = os.getenv("THREADS_ENABLED", "true").lower() in ("1", "true", "yes")

def is_fb_enabled():
    return FB_ENABLED and FB_PAGE_ENABLED

def is_working_hours(now=None):
    if now is None:
        now = datetime.now(TASHKENT_TZ)
    return WORK_START_HOUR <= now.hour < WORK_END_HOUR

POSTED_FILE = os.getenv("POSTED_FILE", "posted_ids.json")
POSTED_IDS = set()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "don-marat/tashkenttoday-bot")

def load_posted_ids():
    global POSTED_IDS
    try:
        if os.path.exists(POSTED_FILE):
            with open(POSTED_FILE, "r") as f:
                POSTED_IDS = set(json.load(f))
                logger.info(f"Загружено {len(POSTED_IDS)} ID из {POSTED_FILE}")
    except Exception as e:
        logger.error(f"load_posted_ids: {e}")
        POSTED_IDS = set()

def save_posted_id(mid):
    try:
        POSTED_IDS.add(mid)
        to_save = sorted(list(POSTED_IDS))[-1000:]
        _dir = os.path.dirname(POSTED_FILE)
        if _dir and not os.path.exists(_dir):
            os.makedirs(_dir, exist_ok=True)
        with open(POSTED_FILE, "w") as f:
            json.dump(to_save, f)
        logger.info(f"ID {mid} сохранен, всего {len(POSTED_IDS)}")
    except Exception as e:
        logger.error(f"save_posted_id: {e}")

def is_already_posted(mid):
    return mid in POSTED_IDS

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "568226286376483")
FB_PAGE_TOKEN_ENV = os.getenv("FB_PAGE_TOKEN")
FB_USER_TOKEN_ENV = os.getenv("FB_USER_LONG_TOKEN") or os.getenv("FB_USER_TOKEN")
FB_APP_ID = os.getenv("FB_APP_ID", "1301269625209688")
FB_APP_SECRET = os.getenv("FB_APP_SECRET")
THREADS_USER_ID = os.getenv("THREADS_USER_ID", "27092394720363294")
THREADS_TOKEN_ENV = os.getenv("THREADS_TOKEN")
SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL", "@tashkenttodayuz")

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
        return False
    try:
        q = "mutation variableUpsert($input: VariableUpsertInput!) { variableUpsert(input: $input) }"
        vars_ = {"input": {"projectId": RAILWAY_PROJECT_ID, "environmentId": RAILWAY_ENV_ID, "serviceId": RAILWAY_SERVICE_ID, "name": name, "value": value}}
        headers = {"Authorization": f"Bearer {RAILWAY_API_TOKEN}", "Content-Type": "application/json"}
        r = requests.post("https://backboard.railway.app/graphql/v2", json={"query": q, "variables": vars_}, headers=headers, timeout=20)
        logger.info(f"Railway {name}: {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Railway {name} error: {e}")
        return False

def check_fb_expiry():
    if not FB_USER_TOKEN:
        return
    try:
        url = f"https://graph.facebook.com/v20.0/debug_token?input_token={FB_USER_TOKEN}&access_token={FB_APP_ID}|{FB_APP_SECRET}"
        r = requests.get(url, timeout=15)
        data = r.json().get("data", {})
        exp = data.get("expires_at", 0)
        if exp:
            exp_dt = datetime.fromtimestamp(exp, tz=TASHKENT_TZ)
            days = (exp_dt - datetime.now(TASHKENT_TZ)).days
            logger.info(f"FB User token expires: {exp_dt} ({days} дней)")
    except Exception as e:
        logger.error(f"check_fb_expiry: {e}")

def refresh_fb_page_token():
    global FB_PAGE_TOKEN
    if not FB_USER_TOKEN or not FB_PAGE_ID:
        return None
    try:
        url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}?fields=access_token&access_token={FB_USER_TOKEN}"
        r = requests.get(url, timeout=20)
        new_t = r.json().get("access_token")
        if new_t:
            FB_PAGE_TOKEN = new_t
            save_token(FB_PAGE_TOKEN_FILE, new_t)
            railway_update("FB_PAGE_TOKEN", new_t)
            logger.info(f"FB Page token refreshed")
            return new_t
        return None
    except Exception as e:
        logger.error(f"refresh_fb_page error: {e}")
        return None

def check_threads_expiry():
    if THREADS_TOKEN:
        logger.info(f"Threads token present len={len(THREADS_TOKEN)}")

def refresh_threads_token():
    global THREADS_TOKEN
    if not THREADS_TOKEN:
        return None
    try:
        url = f"https://graph.threads.net/refresh_access_token?grant_type=th_refresh_token&access_token={THREADS_TOKEN}"
        r = requests.get(url, timeout=20)
        new_t = r.json().get("access_token")
        if new_t:
            THREADS_TOKEN = new_t
            save_token(THREADS_TOKEN_FILE, new_t)
            railway_update("THREADS_TOKEN", new_t)
            logger.info(f"Threads refreshed")
            return new_t
        return None
    except Exception as e:
        logger.error(f"refresh_threads error: {e}")
        return None

def format_text_facebook(text, mid, with_link=True):
    text = text.strip()
    paras = [p.strip() for p in re.split(r'\n\s*\n+', text) if p.strip()]
    if not paras:
        return ""
    title = paras[0]
    first_para = paras[1] if len(paras) > 1 else ""
    if first_para:
        base = f"{title}\n\n{first_para}"
    else:
        base = title
    if with_link:
        clean_channel = SOURCE_CHANNEL.replace('@','').strip()
        link = f"https://t.me/{clean_channel}/{mid}"
        base = re.sub(rf'@?{re.escape(clean_channel)}\s*$', '', base, flags=re.IGNORECASE).strip()
        base = re.sub(rf'https?://t\.me/{re.escape(clean_channel)}/\d+\s*$', '', base, flags=re.IGNORECASE).strip()
        result = f"{base}\n\nПодробнее: {link}"
        if len(result) > 1000:
            allowed_first = 1000 - len(title) - len(f"\n\n\n\nПодробнее: {link}") - 10
            if allowed_first > 50 and first_para:
                first_cut = first_para[:allowed_first].rsplit(' ', 1)[0] + "..."
                result = f"{title}\n\n{first_cut}\n\nПодробнее: {link}"
            else:
                result = f"{title}\n\nПодробнее: {link}"
        return result
    else:
        if len(base) > 1000:
            base = base[:997] + "..."
        return base

def format_text_threads(text, mid, with_link=True):
    text = text.strip()
    paras = [p.strip() for p in re.split(r'\n\s*\n+', text) if p.strip()]
    if not paras:
        return ""
    title = paras[0].strip()
    first_para = paras[1] if len(paras) > 1 else ""
    first_para = first_para.strip()
    clean_channel = SOURCE_CHANNEL.replace('@','').strip()
    link = f"https://t.me/{clean_channel}/{mid}"
    more = f"Подробнее: {link}"
    LIMIT = 500
    if first_para:
        candidate = f"{title}\n\n{first_para}\n\n{more}"
        if len(candidate) <= LIMIT:
            return candidate
        reserved = len(title) + len(f"\n\n\n\n{more}")
        allowed = LIMIT - reserved
        if allowed > 50:
            cut = first_para[:allowed].rsplit(' ', 1)[0] + "..."
            candidate2 = f"{title}\n\n{cut}\n\n{more}"
            if len(candidate2) <= LIMIT:
                return candidate2
    candidate_title_only = f"{title}\n\n{more}"
    if len(candidate_title_only) <= LIMIT:
        return candidate_title_only
    allowed_title = LIMIT - len(f"\n\n{more}") - 5
    if allowed_title > 20:
        title_cut = title[:allowed_title].rsplit(' ', 1)[0] + "..."
        return f"{title_cut}\n\n{more}"
    return title[:497] + "..." if len(title) > LIMIT else title

def upload_to_public_host(tg_url, filename, mime):
    try:
        r = requests.get(tg_url, timeout=60)
        if r.status_code!= 200 or len(r.content) < 100:
            return None
        data = r.content
        try:
            files = {"fileToUpload": (filename, data, mime)}
            cr = requests.post("https://catbox.moe/user/api.php", data={"reqtype": "fileupload"}, files=files, timeout=30)
            if cr.status_code == 200 and cr.text.startswith("http"):
                return cr.text.strip()
        except:
            pass
        try:
            files = {"file": (filename, data, mime)}
            r2 = requests.post("https://0x0.st", files=files, timeout=30)
            if r2.status_code == 200 and r2.text.startswith("http"):
                return r2.text.strip()
        except:
            pass
        return None
    except Exception as e:
        logger.error(f"upload error: {e}")
        return None

def post_to_facebook(text, tg_img=None, pub_img=None, video_url=None, pub_imgs=None):
    if not is_fb_enabled():
        logger.info(f"FB Page отключен FB_ENABLED={FB_ENABLED} FB_PAGE_ENABLED={FB_PAGE_ENABLED}")
        return None
    if not FB_PAGE_TOKEN or not FB_PAGE_ID:
        logger.warning("FB token/ID не заданы")
        return None
    try:
        if video_url:
            url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/videos"
            data = {"description": text, "file_url": video_url, "access_token": FB_PAGE_TOKEN}
            r = requests.post(url, data=data, timeout=120)
            logger.info(f"FB video: {r.status_code} {r.text[:500]}")
            return r.json() if r.status_code == 200 else None
        if pub_imgs:
            # альбом
            ids = []
            for img in pub_imgs:
                url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/photos"
                d = {"url": img, "published": False, "access_token": FB_PAGE_TOKEN}
                rr = requests.post(url, data=d, timeout=60)
                j = rr.json()
                if "id" in j:
                    ids.append(j["id"])
            if not ids:
                return None
            url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/feed"
            attached = ",".join([f'{{"media_fbid":"{i}"}}' for i in ids])
            data = {"message": text, "attached_media": f"[{attached}]", "access_token": FB_PAGE_TOKEN}
            r = requests.post(url, data=data, timeout=60)
            return r.json()
        if pub_img:
            url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/photos"
            data = {"url": pub_img, "caption": text, "access_token": FB_PAGE_TOKEN}
            r = requests.post(url, data=data, timeout=60)
            return r.json()
        url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/feed"
        data = {"message": text, "access_token": FB_PAGE_TOKEN}
        r = requests.post(url, data=data, timeout=60)
        return r.json()
    except Exception as e:
        logger.error(f"FB error: {e}", exc_info=True)
        return None

def post_to_threads(text, tg_img=None, pub_img=None, video_url=None, pub_imgs=None):
    if not THREADS_ENABLED:
        logger.info("Threads ВЫКЛЮЧЕН")
        return None
    if not THREADS_TOKEN or not THREADS_USER_ID:
        logger.warning("Threads token/ID не заданы")
        return None
    try:
        create_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
        short_text = text[:500]
        if video_url and not THREADS_DISABLE_VIDEO:
            # video post
            payload = {"media_type": "VIDEO", "video_url": video_url, "text": short_text, "access_token": THREADS_TOKEN}
            r = requests.post(create_url, data=payload, timeout=90)
            cid = r.json().get("id")
            if not cid:
                logger.warning(f"Threads video create fail {r.text[:500]}")
                return None
            time.sleep(5)
            pub_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
            r2 = requests.post(pub_url, data={"creation_id": cid, "access_token": THREADS_TOKEN}, timeout=30)
            logger.info(f"Threads video publish {r2.status_code} {r2.text[:500]}")
            return r2.json()
        if pub_imgs:
            # Threads не поддерживает альбом, постим первое фото
            pub_img = pub_imgs[0]
        if pub_img:
            payload = {"media_type": "IMAGE", "image_url": pub_img, "text": short_text, "access_token": THREADS_TOKEN}
            r = requests.post(create_url, data=payload, timeout=90)
            if r.status_code == 200 and r.json().get("id"):
                cid = r.json().get("id")
                time.sleep(5)
                pub_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
                r2 = requests.post(pub_url, data={"creation_id": cid, "access_token": THREADS_TOKEN}, timeout=30)
                logger.info(f"Threads image publish {r2.status_code}")
                return r2.json()
        payload = {"media_type": "TEXT", "text": short_text, "access_token": THREADS_TOKEN}
        r = requests.post(create_url, data=payload, timeout=30)
        cid = r.json().get("id")
        if not cid:
            return None
        time.sleep(2)
        pub_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
        r2 = requests.post(pub_url, data={"creation_id": cid, "access_token": THREADS_TOKEN}, timeout=30)
        return r2.json()
    except Exception as e:
        logger.error(f"Threads error: {e}", exc_info=True)
        return None

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post:
        return
    if post.media_group_id:
        mg_id = post.media_group_id
        MEDIA_GROUP_CACHE[mg_id].append(post)
        txt = post.text or post.caption or ""
        if txt:
            MEDIA_GROUP_LAST_TEXT[mg_id] = (txt, post.message_id)
        jobs = context.job_queue.get_jobs_by_name(f"album_{mg_id}")
        if not jobs:
            context.job_queue.run_once(process_album_job, when=7, name=f"album_{mg_id}", data=mg_id)
            logger.info(f"Альбом {mg_id}: первое фото, ждем 7 сек...")
        else:
            logger.info(f"Альбом {mg_id}: собрано {len(MEDIA_GROUP_CACHE[mg_id])} фото")
        return

    raw_text = post.text or post.caption or ""
    if not raw_text:
        return
    mid = post.message_id
    if is_already_posted(mid):
        logger.info(f"Пост {mid} уже был, пропускаем")
        return
    if not is_working_hours():
        logger.info(f"Вне графика 9-21, пост {mid} пропускаем")
        save_posted_id(mid)
        return
    is_video = bool(post.video or post.animation or post.video_note or (post.document and post.document.mime_type and post.document.mime_type.startswith("video/")))
    if is_video and (DISABLE_VIDEO or THREADS_DISABLE_VIDEO):
        logger.info(f"Видео отключено DISABLE_VIDEO={DISABLE_VIDEO} THREADS_DISABLE_VIDEO={THREADS_DISABLE_VIDEO}, пост {mid} пропускаем")
        save_posted_id(mid)
        return

    def get_full_tg_url(file_path):
        if not file_path:
            return None
        if file_path.startswith("http"):
            return file_path
        return f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"

    fb_text = format_text_facebook(raw_text, mid, with_link=not is_video)
    th_text = format_text_threads(raw_text, mid, with_link=not is_video)
    tg_img = None
    pub_img = None
    pub_video = None
    if post.photo:
        try:
            file_id = post.photo[-1].file_id
            file = await context.bot.get_file(file_id)
            tg_img = get_full_tg_url(file.file_path)
            if tg_img:
                pub_img = upload_to_public_host(tg_img, "image.jpg", "image/jpeg")
            logger.info(f"Фото -> public: {bool(pub_img)}")
        except Exception as e:
            logger.error(f"Photo error: {e}", exc_info=True)
    elif post.video and not DISABLE_VIDEO:
        try:
            file_id = post.video.file_id
            file = await context.bot.get_file(file_id)
            tg_file_url = get_full_tg_url(file.file_path)
            if tg_file_url:
                pub_video = upload_to_public_host(tg_file_url, "video.mp4", "video/mp4")
            tg_img = tg_file_url
        except Exception as e:
            logger.error(f"Video error: {e}", exc_info=True)

    fb_res = None
    th_res = None
    if is_fb_enabled():
        fb_res = post_to_facebook(fb_text, tg_img, pub_img, video_url=pub_video)
    else:
        logger.info(f"FB Page ВЫКЛЮЧЕН FB_ENABLED={FB_ENABLED} FB_PAGE_ENABLED={FB_PAGE_ENABLED}")

    if THREADS_ENABLED:
        th_res = post_to_threads(th_text, tg_img, pub_img, video
