
import os
import logging
import requests
import time
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters
from datetime import datetime, timezone, timedelta
import json
import re

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

MEDIA_GROUP_CACHE = defaultdict(list)
MEDIA_GROUP_LAST_TEXT = {}

WORK_START_HOUR = int(os.getenv("WORK_START_HOUR", "9"))
WORK_END_HOUR = int(os.getenv("WORK_END_HOUR", "20"))
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

POSTED_FILE = os.getenv("POSTED_FILE", "/app/data/posted_ids.json")
POSTED_IDS = set()

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
        d = os.path.dirname(POSTED_FILE)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
        with open(POSTED_FILE, "w") as f:
            json.dump(to_save, f)
        logger.info(f"ID {mid} сохранен")
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
                    return t
    except:
        pass
    return env_val

def save_token(path, token):
    try:
        with open(path, "w") as f:
            f.write(token)
    except:
        pass

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
        return r.status_code == 200
    except:
        return False

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
            logger.info("FB Page token refreshed")
            return new_t
    except Exception as e:
        logger.error(f"refresh_fb_page: {e}")
    return None

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
            logger.info("Threads refreshed")
            return new_t
    except Exception as e:
        logger.error(f"refresh_threads: {e}")
    return None

def format_text_facebook(text, mid, with_link=True):
    text = text.strip()
    if with_link:
        link = f"https://t.me/{SOURCE_CHANNEL.replace('@','')}/{mid}"
        if len(text) > 900:
            text = text[:900] + "..."
        return f"{text}\n\n{link}"
    else:
        if len(text) > 1000:
            text = text[:1000] + "..."
        return text

def format_text_threads(text, mid, with_link=True):
    text = text.strip()
    if not text:
        link = f"t.me/{SOURCE_CHANNEL.replace('@','')}/{mid}"
        return f"Подробнее: {link}" if with_link else ""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paras:
        title = text.split("\n")[0].strip()[:120]
        first_para = ""
    else:
        title = paras[0].strip()
        if len(title) > 150:
            title = title.split("\n")[0].strip()[:120]
            first_para = " ".join(paras[0].split("\n")[1:]).strip() or (paras[1] if len(paras) > 1 else "")
        else:
            first_para = paras[1] if len(paras) > 1 else ""
    if len(title) > 110:
        title = title[:107] + "..."
    first_para = first_para.replace("\n", " ").strip()
    if first_para:
        sentences = re.split(r'(?<=[.!?])\s+', first_para)
        if sentences:
            if len(sentences[0]) < 120 and len(sentences) > 1:
                first_para = sentences[0] + " " + sentences[1]
            else:
                first_para = sentences[0]
        if len(first_para) > 200:
            first_para = first_para[:197] + "..."
    short_link = f"t.me/{SOURCE_CHANNEL.replace('@','')}/{mid}"
    link_block = f"\n\nПодробнее: {short_link}" if with_link else ""
    if first_para:
        result = f"{title}\n\n{first_para}{link_block}"
    else:
        result = f"{title}{link_block}"
    if len(result) > 450:
        overflow = len(result) - 450
        if first_para:
            first_para = first_para[: max(0, len(first_para) - overflow - 3)] + "..."
            result = f"{title}\n\n{first_para}{link_block}"
        else:
            title = title[: max(20, len(title) - overflow - 3)] + "..."
            result = f"{title}{link_block}"
    if len(result) > 495:
        result = result[:492] + "..."
    return result

def upload_to_public_host(tg_url, filename, mime):
    try:
        r = requests.get(tg_url, timeout=60)
        if r.status_code != 200 or len(r.content) < 100:
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
        logger.info("FB отключен")
        return None
    if not FB_PAGE_TOKEN or not FB_PAGE_ID:
        return None
    try:
        if video_url:
            url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/videos"
            data = {"description": text, "file_url": video_url, "access_token": FB_PAGE_TOKEN}
            r = requests.post(url, data=data, timeout=120)
            logger.info(f"FB VIDEO: {r.status_code} {r.text[:500]}")
            return r.json()
        img_list = pub_imgs or ([pub_img or tg_img] if (pub_img or tg_img) else [])
        if len(img_list) > 1:
            media_ids = []
            for img_url in img_list[:10]:
                try:
                    up_url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/photos"
                    up_data = {"url": img_url, "published": False, "temporary": True, "access_token": FB_PAGE_TOKEN}
                    ur = requests.post(up_url, data=up_data, timeout=30)
                    fid = ur.json().get("id")
                    if fid:
                        media_ids.append(fid)
                except:
                    pass
                time.sleep(0.5)
            if len(media_ids) >= 2:
                try:
                    feed_url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/feed"
                    feed_data = {"message": text, "access_token": FB_PAGE_TOKEN}
                    for i, mid in enumerate(media_ids):
                        feed_data[f"attached_media[{i}]"] = f'{{"media_fbid":"{mid}"}}'
                    fr = requests.post(feed_url, data=feed_data, timeout=30)
                    if fr.status_code == 200 and fr.json().get("id"):
                        return fr.json()
                except:
                    pass
        img_url = img_list[0] if img_list else None
        if img_url:
            url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/photos"
            data = {"caption": text, "url": img_url, "access_token": FB_PAGE_TOKEN}
        else:
            url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/feed"
            data = {"message": text, "access_token": FB_PAGE_TOKEN}
        r = requests.post(url, data=data, timeout=30)
        logger.info(f"FB OK: {r.status_code} {r.text[:500]}")
        if "Session has expired" in r.text or "Error validating access token" in r.text:
            if refresh_fb_page_token():
                data["access_token"] = FB_PAGE_TOKEN
                r = requests.post(url, data=data, timeout=30)
        return r.json()
    except Exception as e:
        logger.error(f"FB error: {e}", exc_info=True)
        return None

def post_to_threads(text, tg_img=None, pub_img=None, video_url=None, pub_imgs=None):
    if THREADS_DISABLE_VIDEO and video_url:
        logger.info("Threads видео отключено - пропуск")
        return None
    if not THREADS_ENABLED:
        return None
    if not THREADS_TOKEN or not THREADS_USER_ID:
        return None
    try:
        create_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
        short_text = text[:500] if len(text) > 500 else text
        img_list = pub_imgs or ([pub_img or tg_img] if (pub_img or tg_img) else [])
        if len(img_list) > 1:
            # карусель
            item_ids = []
            for img_url in img_list[:10]:
                payload = {"media_type": "IMAGE", "image_url": img_url, "is_carousel_item": True, "access_token": THREADS_TOKEN}
                r = requests.post(create_url, data=payload, timeout=30)
                logger.info(f"Threads CAROUSEL ITEM: {r.text[:400]}")
                iid = r.json().get("id")
                if iid:
                    item_ids.append(iid)
                time.sleep(1.2)
            if len(item_ids) >= 2:
                payload = {"media_type": "CAROUSEL", "text": short_text, "children": ",".join(item_ids), "access_token": THREADS_TOKEN}
                r = requests.post(create_url, data=payload, timeout=30)
                logger.info(f"Threads CAROUSEL CREATE: {r.text[:600]}")
                cid = r.json().get("id")
                if cid:
                    time.sleep(7)
                    pub_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
                    r2 = requests.post(pub_url, data={"creation_id": cid, "access_token": THREADS_TOKEN}, timeout=60)
                    logger.info(f"Threads publish CAROUSEL: {r2.status_code} {r2.text[:600]}")
                    if r2.status_code == 200 and r2.json().get("id"):
                        return r2.json()
                    time.sleep(12)
                    r2 = requests.post(pub_url, data={"creation_id": cid, "access_token": THREADS_TOKEN}, timeout=60)
                    return r2.json()
        img_to_use = img_list[0] if img_list else None
        if img_to_use:
            payload = {"media_type": "IMAGE", "text": short_text, "image_url": img_to_use, "access_token": THREADS_TOKEN}
            r = requests.post(create_url, data=payload, timeout=40)
            logger.info(f"Threads IMAGE CREATE: {r.status_code} {r.text[:500]}")
            cid = r.json().get("id")
            if cid:
                time.sleep(5)
                pub_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
                r2 = requests.post(pub_url, data={"creation_id": cid, "access_token": THREADS_TOKEN}, timeout=40)
                logger.info(f"Threads publish IMAGE: {r2.status_code} {r2.text[:500]}")
                if r2.status_code == 200 and r2.json().get("id"):
                    return r2.json()
        payload = {"media_type": "TEXT", "text": short_text, "access_token": THREADS_TOKEN}
        r = requests.post(create_url, data=payload, timeout=30)
        logger.info(f"Threads TEXT CREATE: {r.status_code} {r.text[:500]}")
        cid = r.json().get("id")
        if not cid:
            if "Session has expired" in r.text or "Error validating access token" in r.text:
                if refresh_threads_token():
                    payload["access_token"] = THREADS_TOKEN
                    r = requests.post(create_url, data=payload, timeout=30)
                    cid = r.json().get("id")
            if not cid:
                return None
        time.sleep(2.5)
        pub_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
        r2 = requests.post(pub_url, data={"creation_id": cid, "access_token": THREADS_TOKEN}, timeout=30)
        logger.info(f"Threads publish TEXT: {r2.status_code} {r2.text[:500]}")
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
        return
    raw_text = post.text or post.caption or ""
    if not raw_text:
        return
    mid = post.message_id
    logger.info(f"Новый пост {mid}: {raw_text[:80]}...")
    if is_already_posted(mid):
        logger.info(f"Пост {mid} уже был")
        return
    if not is_working_hours():
        logger.info(f"Вне графика 9-20, пост {mid} пропуск")
        save_posted_id(mid)
        return
    is_video = bool(post.video)
    if is_video and DISABLE_VIDEO:
        logger.info(f"Видео отключено, пост {mid} пропуск")
        save_posted_id(mid)
        return
    def get_full_tg_url(file_path):
        if not file_path:
            return None
        if file_path.startswith("http"):
            return file_path
        return f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    fb_text = format_text_facebook(raw_text, mid, with_link=True)
    th_text = format_text_threads(raw_text, mid, with_link=True)
    logger.info(f"FB text len={len(fb_text)} Threads text len={len(th_text)} text={th_text[:150]}...")
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
            logger.info(f"Фото public: {bool(pub_img)}")
        except Exception as e:
            logger.error(f"Photo error: {e}")
    elif post.video and not DISABLE_VIDEO:
        try:
            file_id = post.video.file_id
            file = await context.bot.get_file(file_id)
            tg_file_url = get_full_tg_url(file.file_path)
            if tg_file_url:
                pub_video = upload_to_public_host(tg_file_url, "video.mp4", "video/mp4")
            tg_img = tg_file_url
        except Exception as e:
            logger.error(f"Video error: {e}")
    fb_res = None
    th_res = None
    if is_fb_enabled():
        fb_res = post_to_facebook(fb_text, tg_img, pub_img, video_url=pub_video)
    if THREADS_ENABLED:
        if is_video and THREADS_DISABLE_VIDEO:
            logger.info(f"Threads видео пропуск {mid}")
        else:
            th_res = post_to_threads(th_text, tg_img, pub_img, video_url=pub_video)
    if fb_res or th_res or not is_fb_enabled() or not THREADS_ENABLED:
        save_posted_id(mid)

async def process_album_job(context: ContextTypes.DEFAULT_TYPE):
    mg_id = context.job.data
    posts = MEDIA_GROUP_CACHE.pop(mg_id, [])
    text_info = MEDIA_GROUP_LAST_TEXT.pop(mg_id, None)
    logger.info(f"Альбом {mg_id}: {len(posts)} фото")
    if not posts:
        return
    if not text_info:
        for p in posts:
            if p.caption:
                text_info = (p.caption, p.message_id)
                break
    raw_text, mid = text_info if text_info else ("", posts[0].message_id)
    if not raw_text:
        return
    if is_already_posted(mid):
        return
    if not is_working_hours():
        save_posted_id(mid)
        return
    def get_full_tg_url(file_path):
        if not file_path:
            return None
        if file_path.startswith("http"):
            return file_path
        return f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    pub_imgs = []
    for idx, p in enumerate(posts):
        if p.photo:
            try:
                file_id = p.photo[-1].file_id
                file = await context.bot.get_file(file_id)
                tg_url = get_full_tg_url(file.file_path)
                if not tg_url:
                    continue
                pub = upload_to_public_host(tg_url, f"image_{idx}.jpg", "image/jpeg")
                if pub:
                    pub_imgs.append(pub)
            except Exception as e:
                logger.error(f"Album error: {e}")
    if not pub_imgs:
        return
    fb_text = format_text_facebook(raw_text, mid, with_link=True)
    th_text = format_text_threads(raw_text, mid, with_link=True)
    fb_res = None
    th_res = None
    if is_fb_enabled():
        fb_res = post_to_facebook(fb_text, pub_imgs=pub_imgs)
    if THREADS_ENABLED:
        th_res = post_to_threads(th_text, pub_imgs=pub_imgs)
    if fb_res or th_res or not is_fb_enabled() or not THREADS_ENABLED:
        save_posted_id(mid)

async def auto_refresh_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Авто-проверка токенов...")
    refresh_fb_page_token()
    refresh_threads_token()

async def error_handler(update, context):
    err = str(context.error)
    if "409" in err or "Conflict" in err:
        time.sleep(10)
    else:
        logger.error(f"Error: {context.error}")

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Нет TELEGRAM_BOT_TOKEN!")
        return
    load_posted_ids()
    for attempt in range(2):
        try:
            requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=False", timeout=10)
            time.sleep(5)
            break
        except:
            time.sleep(3)
    global THREADS_TOKEN, FB_PAGE_TOKEN, FB_USER_TOKEN
    THREADS_TOKEN = load_token(THREADS_TOKEN_FILE, THREADS_TOKEN_ENV)
    FB_PAGE_TOKEN = load_token(FB_PAGE_TOKEN_FILE, FB_PAGE_TOKEN_ENV)
    FB_USER_TOKEN = load_token(FB_USER_TOKEN_FILE, FB_USER_TOKEN_ENV)
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))
    app.add_error_handler(error_handler)
    if app.job_queue:
        app.job_queue.run_repeating(auto_refresh_job, interval=24*60*60, first=60)
    vid_fb = "ВЫКЛ" if DISABLE_VIDEO else "ВКЛ"
    vid_th = "ВЫКЛ" if THREADS_DISABLE_VIDEO else "ВКЛ"
    now_t = datetime.now(TASHKENT_TZ).strftime("%H:%M")
    logger.info(f"Бот запущен {SOURCE_CHANNEL} -> FB {FB_PAGE_ID} enabled={is_fb_enabled()} | Threads {THREADS_USER_ID} enabled={THREADS_ENABLED} | Время {now_t} График 9-20: {is_working_hours()} | Видео FB {vid_fb} Threads {vid_th} | Дедуп: {len(POSTED_IDS)}")
    app.run_polling(allowed_updates=["channel_post"], poll_interval=20.0, timeout=30, drop_pending_updates=False, close_loop=False)

if __name__ == "__main__":
    main()
