import os
import logging
import requests
import time
import asyncio
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Кэш для альбомов (media_group_id -> list of posts)
MEDIA_GROUP_CACHE = defaultdict(list)
MEDIA_GROUP_LAST_TEXT = {}

# === Дедупликация: какие посты уже опубликованы ===
import json
POSTED_FILE = os.getenv("POSTED_FILE", "posted_ids.json")
POSTED_IDS = set()

def load_posted_ids():
    global POSTED_IDS
    try:
        if os.path.exists(POSTED_FILE):
            with open(POSTED_FILE, "r") as f:
                data = json.load(f)
                POSTED_IDS = set(data)
                logger.info(f"Загружено {len(POSTED_IDS)} уже опубликованных ID из {POSTED_FILE}")
    except Exception as e:
        logger.error(f"load_posted_ids: {e}")
        POSTED_IDS = set()

def save_posted_id(mid):
    try:
        POSTED_IDS.add(mid)
        # Храним только последние 1000 ID чтобы файл не рос бесконечно
        to_save = sorted(list(POSTED_IDS))[-1000:]
        with open(POSTED_FILE, "w") as f:
            json.dump(to_save, f)
        logger.info(f"ID {mid} сохранен как опубликованный, всего {len(POSTED_IDS)}")
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
        # Правильный endpoint: /me/accounts
        url = f"https://graph.facebook.com/v20.0/me/accounts?fields=id,name,access_token&access_token={FB_USER_TOKEN}"
        r = requests.get(url, timeout=20)
        logger.info(f"FB accounts: {r.text[:800]}")
        data = r.json()
        accounts = data.get("data", []) if "data" in data else []
        # Если accounts пусто, пробуем второй формат
        if not accounts and "accounts" in data:
            accounts = data["accounts"].get("data", [])
        for acc in accounts:
            if acc.get("id") == FB_PAGE_ID:
                new_t = acc.get("access_token")
                if new_t:
                    FB_PAGE_TOKEN = new_t
                    save_token(FB_PAGE_TOKEN_FILE, new_t)
                    railway_update("FB_PAGE_TOKEN", new_t)
                    logger.info(f"✅ FB Page token refreshed len={len(new_t)}")
                    return new_t
        logger.error(f"FB Page {FB_PAGE_ID} not found in {accounts} - full response: {r.text[:1000]}")
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

def format_text_threads(text, mid, with_link=True):
    link = get_post_link(mid)
    clean = text.strip()
    if not clean:
        return f"Подробнее: {link}" if with_link else ""
    title = clean.split("\n")[0].strip()
    rest = clean[len(title):].strip()
    paras = [p.strip() for p in rest.split("\n\n") if p.strip()]
    first = paras[0] if paras else ""
    if len(first) > 300:
        first = first[:297] + "..."
    if first:
        base = f"{title}\n\n{first}"
    else:
        base = f"{title}"
    if with_link:
        return f"{base}\n\nПодробнее: {link}"
    return base

def format_text_facebook(text, mid, with_link=True):
    link = get_post_link(mid)
    clean = text.strip()
    if not clean:
        return f"Подробнее: {link}" if with_link else ""
    title = clean.split("\n")[0].strip()
    rest = clean[len(title):].strip()
    paras = [p.strip() for p in rest.split("\n\n") if p.strip()]
    first = paras[0] if paras else ""
    if len(first) > 1000:
        first = first[:997] + "..."
    if first:
        base = f"{title}\n\n{first}"
    else:
        base = f"{title}"
    if with_link:
        return f"{base}\n\nПодробнее: {link}"
    return base

def upload_to_public_host(tg_url, filename="image.jpg", mime="image/jpeg"):
    try:
        logger.info(f"Downloading TG file: {tg_url[:80]}...")
        img_data = requests.get(tg_url, timeout=60).content
        if len(img_data) < 1000:
            return None
        # catbox поддерживает и видео до 200MB
        try:
            r = requests.post("https://catbox.moe/user/api.php", data={"reqtype": "fileupload"}, files={"fileToUpload": (filename, img_data, mime)}, timeout=60)
            if r.status_code == 200 and r.text.startswith("https://"):
                logger.info(f"Uploaded to catbox: {r.text.strip()}")
                return r.text.strip()
        except Exception as e:
            logger.error(f"Catbox error: {e}")
        try:
            r = requests.post("https://0x0.st", files={"file": (filename, img_data, mime)}, timeout=60)
            if r.status_code == 200 and r.text.startswith("https://"):
                logger.info(f"Uploaded to 0x0.st: {r.text.strip()}")
                return r.text.strip()
        except Exception as e:
            logger.error(f"0x0 error: {e}")
        return None
    except Exception as e:
        logger.error(f"upload error: {e}")
        return None

def post_to_facebook(text, tg_img=None, pub_img=None, video_url=None, pub_imgs=None):
    try:
        # Если есть видео - постим как видео
        if video_url:
            url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/videos"
            data = {"description": text, "file_url": video_url, "access_token": FB_PAGE_TOKEN}
            r = requests.post(url, data=data, timeout=120)
            logger.info(f"FB VIDEO: {r.status_code} {r.text[:800]}")
            if any(x in r.text for x in ["Session has expired", "Error validating access token"]):
                if refresh_fb_page_token():
                    data["access_token"] = FB_PAGE_TOKEN
                    r = requests.post(url, data=data, timeout=120)
                    logger.info(f"FB VIDEO RETRY: {r.status_code} {r.text[:800]}")
            return r.json()

        img_list = pub_imgs or ([pub_img or tg_img] if (pub_img or tg_img) else [])
        
        # === Несколько фото: пробуем multi-photo пост через unpublished ===
        if len(img_list) > 1:
            logger.info(f"FB ALBUM: {len(img_list)} фото, пробуем multi-photo пост")
            media_ids = []
            for img_url in img_list[:10]:  # FB лимит 10 для attached_media
                try:
                    up_url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/photos"
                    up_data = {"url": img_url, "published": False, "temporary": True, "access_token": FB_PAGE_TOKEN}
                    ur = requests.post(up_url, data=up_data, timeout=30)
                    logger.info(f"FB unpublished upload: {ur.text[:400]}")
                    fid = ur.json().get("id")
                    if fid:
                        media_ids.append(fid)
                except Exception as e:
                    logger.error(f"FB unpublished error: {e}")
                time.sleep(0.5)
            
            if len(media_ids) >= 2:
                try:
                    feed_url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/feed"
                    feed_data = {"message": text, "access_token": FB_PAGE_TOKEN}
                    for i, mid in enumerate(media_ids):
                        feed_data[f"attached_media[{i}]"] = f'{{"media_fbid":"{mid}"}}'
                    fr = requests.post(feed_url, data=feed_data, timeout=30)
                    logger.info(f"FB multi-photo post: {fr.status_code} {fr.text[:600]}")
                    if fr.status_code == 200 and fr.json().get("id"):
                        return fr.json()
                    # Если multi не сработал, fallback на 1 фото
                    logger.warning(f"FB multi-photo failed, fallback to single: {fr.text[:400]}")
                except Exception as e:
                    logger.error(f"FB multi-photo feed error: {e}")
            # Fallback если не удалось загрузить 2+ фото
            logger.info("FB multi fallback: постим только первое фото")

        img_url = img_list[0] if img_list else None
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

def post_to_threads_carousel(text, image_urls):
    """П постит карусель в Threads (до 20 фото)"""
    try:
        create_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
        short_text = text[:480] if len(text) > 480 else text
        # 1. Создаем контейнеры для каждого изображения
        item_ids = []
        for img_url in image_urls[:10]:  # Threads лимит 20, но возьмем 10 для надежности
            payload = {"media_type": "IMAGE", "image_url": img_url, "is_carousel_item": True, "access_token": THREADS_TOKEN}
            r = requests.post(create_url, data=payload, timeout=30)
            logger.info(f"Threads CAROUSEL ITEM: {r.text[:400]}")
            iid = r.json().get("id")
            if iid:
                item_ids.append(iid)
            time.sleep(1)
        if not item_ids:
            return None
        # 2. Создаем карусель-контейнер
        payload = {"media_type": "CAROUSEL", "text": short_text, "children": ",".join(item_ids), "access_token": THREADS_TOKEN}
        r = requests.post(create_url, data=payload, timeout=30)
        logger.info(f"Threads CAROUSEL CREATE: {r.text[:600]}")
        cid = r.json().get("id")
        if not cid:
            return None
        time.sleep(6)
        pub_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
        r2 = requests.post(pub_url, data={"creation_id": cid, "access_token": THREADS_TOKEN}, timeout=60)
        logger.info(f"Threads publish CAROUSEL: {r2.text[:600]}")
        if r2.status_code == 200 and r2.json().get("id"):
            return r2.json()
        time.sleep(10)
        r2 = requests.post(pub_url, data={"creation_id": cid, "access_token": THREADS_TOKEN}, timeout=60)
        logger.info(f"Threads publish CAROUSEL retry: {r2.text[:600]}")
        return r2.json()
    except Exception as e:
        logger.error(f"Threads carousel error: {e}")
        return None

def post_to_threads(text, tg_img=None, pub_img=None, video_url=None, pub_imgs=None):
    try:
        create_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
        short_text = text[:480] if len(text) > 480 else text
        # Видео приоритет
        if video_url:
            payload = {"media_type": "VIDEO", "text": short_text, "video_url": video_url, "access_token": THREADS_TOKEN}
            r = requests.post(create_url, data=payload, timeout=60)
            logger.info(f"Threads VIDEO: {r.text[:800]}")
            cid = r.json().get("id")
            if cid:
                # Видео обрабатывается дольше
                time.sleep(10)
                pub_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
                r2 = requests.post(pub_url, data={"creation_id": cid, "access_token": THREADS_TOKEN}, timeout=60)
                logger.info(f"Threads publish VIDEO: {r2.text[:800]}")
                if r2.status_code == 200 and r2.json().get("id"):
                    return r2.json()
                # Если еще не готово, ждем еще
                time.sleep(15)
                r2 = requests.post(pub_url, data={"creation_id": cid, "access_token": THREADS_TOKEN}, timeout=60)
                logger.info(f"Threads publish VIDEO retry: {r2.text[:800]}")
                return r2.json()
        # Если альбом (несколько фото)
        img_list = pub_imgs or ([pub_img or tg_img] if (pub_img or tg_img) else [])
        if len(img_list) > 1:
            logger.info(f"Threads ALBUM: {len(img_list)} фото -> пробуем карусель")
            res = post_to_threads_carousel(text, img_list)
            if res:
                return res
            # Fallback: постим только первое если карусель не удалась
            img_list = [img_list[0]]
        img_to_use = img_list[0] if img_list else None
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
    # === АЛЬБОМ: собираем все фото в группу ===
    if post.media_group_id:
        mg_id = post.media_group_id
        MEDIA_GROUP_CACHE[mg_id].append(post)
        # Сохраняем текст (он только в первом фото альбома)
        txt = post.text or post.caption or ""
        if txt:
            MEDIA_GROUP_LAST_TEXT[mg_id] = (txt, post.message_id)
        # Планируем обработку через 4 сек (чтобы собрать все фото альбома)
        # Если уже запланировано - не дублируем
        jobs = context.job_queue.get_jobs_by_name(f"album_{mg_id}")
        if not jobs:
            context.job_queue.run_once(process_album_job, when=4, name=f"album_{mg_id}", data=mg_id)
            logger.info(f"Альбом {mg_id}: собрано {len(MEDIA_GROUP_CACHE[mg_id])} фото, ждем остальные...")
        return

    raw_text = post.text or post.caption or ""
    if not raw_text:
        return
    mid = post.message_id

    # === Проверка: уже публиковали? ===
    if is_already_posted(mid):
        logger.info(f"Пост {mid} уже был опубликован, пропускаем")
        return

    is_video = bool(post.video)
    # Для видео - без ссылки, для фото/текста - с ссылкой
    fb_text = format_text_facebook(raw_text, mid, with_link=not is_video)
    th_text = format_text_threads(raw_text, mid, with_link=not is_video)
    tg_img = None
    pub_img = None
    pub_video = None
    if post.photo:
        file = await post.photo[-1].get_file()
        tg_img = file.file_path
        pub_img = upload_to_public_host(tg_img, "image.jpg", "image/jpeg")
    elif post.video:
        try:
            file = await post.video.get_file()
            tg_file_url = file.file_path
            if tg_file_url and not tg_file_url.startswith("http"):
                tg_file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{tg_file_url}"
            logger.info(f"Video TG url: {tg_file_url[:100]}")
            pub_video = upload_to_public_host(tg_file_url, "video.mp4", "video/mp4")
            tg_img = tg_file_url
        except Exception as e:
            logger.error(f"Video handling error: {e}")
    logger.info(f"Новый пост {mid}: {raw_text[:80]}... is_video={is_video} tg_img={bool(tg_img)} public_img={pub_img} public_video={bool(pub_video)}")
    fb_res = post_to_facebook(fb_text, tg_img, pub_img, video_url=pub_video)
    th_res = post_to_threads(th_text, tg_img, pub_img, video_url=pub_video)
    # Сохраняем ID только если хотя бы один пост успешно ушел
    if fb_res or th_res:
        save_posted_id(mid)

async def process_album_job(context: ContextTypes.DEFAULT_TYPE):
    mg_id = context.job.data
    posts = MEDIA_GROUP_CACHE.pop(mg_id, [])
    text_info = MEDIA_GROUP_LAST_TEXT.pop(mg_id, None)
    if not posts:
        return
    if not text_info:
        for p in posts:
            if p.caption:
                text_info = (p.caption, p.message_id)
                break
    raw_text, mid = text_info if text_info else ("", posts[0].message_id)
    if not raw_text:
        logger.info(f"Альбом {mg_id}: нет текста, пропускаем")
        return
    # Проверка дедупликации для альбома
    if is_already_posted(mid):
        logger.info(f"Альбом {mg_id} пост {mid} уже был опубликован, пропускаем")
        return
    logger.info(f"Альбом {mg_id}: обрабатываем {len(posts)} фото, текст: {raw_text[:60]}...")
    pub_imgs = []
    for p in posts:
        if p.photo:
            try:
                file = await p.photo[-1].get_file()
                tg_url = file.file_path
                pub = upload_to_public_host(tg_url, "image.jpg", "image/jpeg")
                if pub:
                    pub_imgs.append(pub)
            except Exception as e:
                logger.error(f"Album photo error: {e}")
    if not pub_imgs:
        logger.warning(f"Альбом {mg_id}: не удалось загрузить фото")
        return
    fb_text = format_text_facebook(raw_text, mid, with_link=True)
    th_text = format_text_threads(raw_text, mid, with_link=True)
    logger.info(f"Альбом {mg_id}: FB пост с 1 фото из {len(pub_imgs)}, Threads карусель {len(pub_imgs)} фото")
    fb_res = post_to_facebook(fb_text, pub_imgs=pub_imgs)
    th_res = post_to_threads(th_text, pub_imgs=pub_imgs)
    if fb_res or th_res:
        save_posted_id(mid)

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

async def error_handler(update, context):
    err = str(context.error)
    if "Conflict" in err or "409" in err or "terminated by other getUpdates" in err:
        logger.warning(f"⚠️ Conflict: другой инстанс бота запущен. Ждем 10 сек... {err[:200]}")
        time.sleep(10)
    else:
        logger.error(f"Error: {context.error}")

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Нет TELEGRAM_BOT_TOKEN!")
        return
    # Загружаем уже опубликованные ID
    load_posted_ids()
    # Удаляем webhook и ждем чтобы старый инстанс точно завершился (Railway rolling deploy)
    for attempt in range(2):
        try:
            # ВАЖНО: drop_pending_updates=False чтобы не терять посты когда бот был выключен
            # Telegram хранит до 100 неподтвержденных channel_post до 24 часов
            requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=False", timeout=10)
            logger.info(f"Webhook удален (попытка {attempt+1}), ждем 5 сек... drop_pending=False (сохраняем пропущенные посты)")
            time.sleep(5)
            break
        except:
            time.sleep(3)
    global THREADS_TOKEN, FB_PAGE_TOKEN, FB_USER_TOKEN
    THREADS_TOKEN = load_token(THREADS_TOKEN_FILE, THREADS_TOKEN_ENV)
    FB_PAGE_TOKEN = load_token(FB_PAGE_TOKEN_FILE, FB_PAGE_TOKEN_ENV)
    FB_USER_TOKEN = load_token(FB_USER_TOKEN_FILE, FB_USER_TOKEN_ENV)
    check_fb_expiry()
    check_threads_expiry()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))
    app.add_error_handler(error_handler)
    if app.job_queue:
        app.job_queue.run_repeating(auto_refresh_job, interval=24*60*60, first=60)
        logger.info("JobQueue: авто-рефреш FB+Threads каждые 24ч")
    logger.info(f"Бот запущен {SOURCE_CHANNEL} -> FB:{FB_PAGE_ID} + Threads:{THREADS_USER_ID} | Авто-обновление FB+Threads: ВКЛ | Дедупликация: {len(POSTED_IDS)} ID | drop_pending=False")
    # drop_pending_updates=False - чтобы проверять последние посты если бот был оффлайн
    app.run_polling(allowed_updates=["channel_post"], poll_interval=60.0, timeout=50, drop_pending_updates=False, close_loop=False)

if __name__ == "__main__":
    main()
