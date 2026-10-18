import os, logging, requests, time, asyncio, threading, json, re, html
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters, CommandHandler
from datetime import datetime, timezone, timedelta

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
        except: msg = "OK"
        self.wfile.write(msg.encode('utf-8'))
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
    def log_message(self, format, *args): return

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
DISABLE_VIDEO = os.getenv("DISABLE_VIDEO", "true").lower() in ("1","true","yes")
THREADS_DISABLE_VIDEO = os.getenv("THREADS_DISABLE_VIDEO", "true").lower() in ("1","true","yes")
FB_ENABLED = os.getenv("FB_ENABLED", "false").lower() in ("1","true","yes")
FB_PAGE_ENABLED = os.getenv("FB_PAGE_ENABLED", "false").lower() in ("1","true","yes")
THREADS_ENABLED = os.getenv("THREADS_ENABLED", "true").lower() in ("1","true","yes")

def is_fb_enabled(): return FB_ENABLED and FB_PAGE_ENABLED
def is_working_hours(now=None):
    if now is None: now = datetime.now(TASHKENT_TZ)
    return WORK_START_HOUR <= now.hour < WORK_END_HOUR

POSTED_FILE = os.getenv("POSTED_FILE", "posted_ids.json")
POSTED_IDS = set()

def load_posted_ids():
    global POSTED_IDS
    try:
        if os.path.exists(POSTED_FILE):
            with open(POSTED_FILE, "r") as f:
                POSTED_IDS = set(json.load(f))
    except: POSTED_IDS = set()

def save_posted_id(mid):
    try:
        POSTED_IDS.add(mid)
        to_save = sorted(list(POSTED_IDS))[-1000:]
        d = os.path.dirname(POSTED_FILE)
        if d and not os.path.exists(d): os.makedirs(d, exist_ok=True)
        with open(POSTED_FILE, "w") as f: json.dump(to_save, f)
        logger.info(f"ID {mid} сохранен, всего {len(POSTED_IDS)}")
    except Exception as e:
        logger.error(f"save_posted_id: {e}")

def is_already_posted(mid): return mid in POSTED_IDS

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

def load_token(path, env_val):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                t = f.read().strip()
                if t and len(t) > 20: return t
    except: pass
    return env_val

def save_token(path, token):
    try:
        with open(path, "w") as f: f.write(token)
    except: pass

THREADS_TOKEN = load_token(THREADS_TOKEN_FILE, THREADS_TOKEN_ENV)
FB_PAGE_TOKEN = load_token(FB_PAGE_TOKEN_FILE, FB_PAGE_TOKEN_ENV)
FB_USER_TOKEN = load_token(FB_USER_TOKEN_FILE, FB_USER_TOKEN_ENV)

def refresh_threads_token():
    global THREADS_TOKEN
    if not THREADS_TOKEN: return None
    try:
        url = f"https://graph.threads.net/refresh_access_token?grant_type=th_refresh_token&access_token={THREADS_TOKEN}"
        r = requests.get(url, timeout=20)
        new_t = r.json().get("access_token")
        if new_t:
            THREADS_TOKEN = new_t
            save_token(THREADS_TOKEN_FILE, new_t)
            logger.info("Threads refreshed")
            return new_t
    except: pass
    return None

def format_text_threads(text, mid, with_link=True):
    text=text.strip()
    paras=[p.strip() for p in re.split(r'\n\s*\n+', text) if p.strip()]
    if not paras: return ""
    title=paras[0].strip(); first=paras[1] if len(paras)>1 else ""
    clean=SOURCE_CHANNEL.replace('@','').strip()
    link=f"https://t.me/{clean}/{mid}"; more=f"Подробнее: {link}"; LIMIT=500
    if first:
        cand=f"{title}\n\n{first}\n\n{more}"
        if len(cand)<=LIMIT: return cand
        allowed=LIMIT-len(title)-len(f"\n\n\n\n{more}")
        if allowed>50:
            cut=first[:allowed].rsplit(' ',1)[0]+"..."
            cand2=f"{title}\n\n{cut}\n\n{more}"
            if len(cand2)<=LIMIT: return cand2
    cand_title=f"{title}\n\n{more}"
    if len(cand_title)<=LIMIT: return cand_title
    allowed_title=LIMIT-len(f"\n\n{more}")-5
    if allowed_title>20:
        title_cut=title[:allowed_title].rsplit(' ',1)[0]+"..."
        return f"{title_cut}\n\n{more}"
    return title[:497]+"..." if len(title)>LIMIT else title

def format_text_facebook(text, mid, with_link=True):
    text=text.strip()
    paras=[p.strip() for p in re.split(r'\n\s*\n+', text) if p.strip()]
    if not paras: return ""
    title=paras[0]; first=paras[1] if len(paras)>1 else ""
    base=f"{title}\n\n{first}" if first else title
    if with_link:
        clean=SOURCE_CHANNEL.replace('@','').strip()
        link=f"https://t.me/{clean}/{mid}"
        result=f"{base}\n\nПодробнее: {link}"
        return result[:1000]
    return base[:997]+"..."

def upload_to_public_host(tg_url, filename, mime):
    """Фикс: 3 ретрая скачивания + 4 хоста"""
    data = None
    for attempt in range(3):
        try:
            r = requests.get(tg_url, timeout=90)
            if r.status_code==200 and len(r.content)>=500:
                data = r.content
                logger.info(f"upload: downloaded {len(data)} bytes {mime} attempt {attempt+1}")
                break
            else:
                logger.warning(f"upload: tg download fail {r.status_code} attempt {attempt+1}")
        except Exception as e:
            logger.warning(f"upload: tg download error {e} attempt {attempt+1}")
            time.sleep(2)
    if not data:
        logger.warning("upload: tg download failed after 3 attempts")
        return None
    try:
        files = {"fileToUpload": (filename, data, mime)}
        cr = requests.post("https://catbox.moe/user/api.php", data={"reqtype": "fileupload"}, files=files, timeout=30)
        if cr.status_code==200 and cr.text.startswith("http"):
            url = cr.text.strip()
            logger.info(f"upload catbox OK {url}")
            return url
    except Exception as e: logger.warning(f"catbox error {e}")
    try:
        files = {"file": (filename, data, mime)}
        r2 = requests.post("https://0x0.st", files=files, timeout=30)
        if r2.status_code==200 and r2.text.startswith("http"):
            url = r2.text.strip()
            logger.info(f"upload 0x0.st OK {url}")
            return url
    except Exception as e: logger.warning(f"0x0.st error {e}")
    try:
        files = {"file": (filename, data, mime)}
        r3 = requests.post("https://file.io", files=files, timeout=30)
        j = r3.json()
        if j.get("link"):
            logger.info(f"upload file.io OK {j['link']}")
            return j["link"]
    except Exception as e: logger.warning(f"file.io error {e}")
    try:
        files = {"file": (filename, data, mime)}
        r4 = requests.post("https://tmpfiles.org/api/v1/upload", files=files, timeout=30)
        j = r4.json()
        if j.get("data", {}).get("url"):
            url = j["data"]["url"]
            logger.info(f"upload tmpfiles OK {url}")
            return url
    except Exception as e: logger.warning(f"tmpfiles error {e}")
    logger.warning("all hosts failed, using direct tg url as last resort")
    return tg_url

def post_to_threads(text, tg_img=None, pub_img=None, video_url=None, pub_imgs=None):
    if not THREADS_ENABLED: return None
    if not THREADS_TOKEN or not THREADS_USER_ID: return None
    try:
        create_url=f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
        short_text=text[:500]
        if pub_imgs: pub_img=pub_imgs[0]
        if pub_img:
            payload={"media_type":"IMAGE","image_url":pub_img,"text":short_text,"access_token":THREADS_TOKEN}
            r=requests.post(create_url,data=payload,timeout=90)
            if r.status_code==200 and r.json().get("id"):
                cid=r.json().get("id"); time.sleep(5)
                pub_url=f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
                r2=requests.post(pub_url,data={"creation_id":cid,"access_token":THREADS_TOKEN},timeout=30)
                logger.info(f"Threads IMAGE publish {r2.status_code} {r2.text[:300]}")
                return r2.json()
            else:
                logger.warning(f"Threads IMAGE create fail {r.status_code} {r.text[:500]} -> fallback TEXT")
        payload={"media_type":"TEXT","text":short_text,"access_token":THREADS_TOKEN}
        r=requests.post(create_url,data=payload,timeout=30)
        cid=r.json().get("id")
        if not cid: 
            logger.warning(f"Threads TEXT create fail {r.text[:500]}")
            return None
        time.sleep(2)
        pub_url=f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
        r2=requests.post(pub_url,data={"creation_id":cid,"access_token":THREADS_TOKEN},timeout=30)
        logger.info(f"Threads TEXT publish {r2.status_code}")
        return r2.json()
    except Exception as e:
        logger.error(f"Threads error: {e}", exc_info=True)
        return None

def post_to_facebook(text, tg_img=None, pub_img=None, video_url=None, pub_imgs=None):
    if not is_fb_enabled(): return None
    if not FB_PAGE_TOKEN or not FB_PAGE_ID: return None
    try:
        img_list=pub_imgs or ([pub_img or tg_img] if (pub_img or tg_img) else [])
        img_url=img_list[0] if img_list else None
        if img_url:
            url=f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/photos"
            data={"caption":text,"url":img_url,"access_token":FB_PAGE_TOKEN}
        else:
            url=f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/feed"
            data={"message":text,"access_token":FB_PAGE_TOKEN}
        r=requests.post(url,data=data,timeout=60)
        return r.json()
    except Exception as e:
        logger.error(f"FB error: {e}", exc_info=True)
        return None

def fetch_last_channel_posts(n=20):
    try:
        clean=SOURCE_CHANNEL.replace('@','').strip()
        url=f"https://t.me/s/{clean}"
        r=requests.get(url,timeout=20,headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code!=200: return []
        html_text=r.text
        wraps=re.split(r'<div class="tgme_widget_message_wrap[^"]*">', html_text)
        posts=[]
        for wrap in wraps:
            m=re.search(rf'data-post="{re.escape(clean)}/(\d+)"', wrap)
            if not m: continue
            mid=int(m.group(1))
            text=""
            tm=re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', wrap, re.DOTALL)
            if tm:
                raw=tm.group(1)
                raw=re.sub(r'<br\s*/?>', '\n', raw)
                raw=re.sub(r'<[^>]+>', '', raw)
                raw=html.unescape(raw).strip()
                text=raw
            if not text:
                cm=re.search(r'<div class="tgme_widget_message_caption[^"]*"[^>]*>(.*?)</div>', wrap, re.DOTALL)
                if cm:
                    raw=cm.group(1)
                    raw=re.sub(r'<br\s*/?>', '\n', raw)
                    raw=re.sub(r'<[^>]+>', '', raw)
                    raw=html.unescape(raw).strip()
                    text=raw
            photo=None
            pm=re.search(r"background-image:url\('(.*?)'\)", wrap)
            if pm:
                photo=pm.group(1)
                if photo.startswith("//"): photo="https:"+photo
                photo=photo.replace("\\", "")
            if text or photo:
                posts.append({"id":mid,"text":text,"photo":photo})
        posts=sorted(posts, key=lambda x: x["id"], reverse=True)[:n]
        return posts
    except Exception as e:
        logger.error(f"fetch error: {e}", exc_info=True)
        return []

async def sync_missing_posts_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_working_hours():
            logger.info("Синхронизация пропущена - вне графика 9-21")
            return
        if len(POSTED_IDS)==0:
            logger.info("sync skipped: Дедуп 0 - первый запуск, не постим 20 старых")
            return
        logger.info("=== Старт сверки последних 20 постов ===")
        posts=fetch_last_channel_posts(20)
        added=0; skipped=0
        for p in posts:
            mid=p["id"]
            if is_already_posted(mid):
                skipped+=1
                continue
            raw_text=p["text"] or ""
            if not raw_text or len(raw_text)<5: continue
            low=raw_text.lower()
            if "pinned a photo" in low or "pinned a video" in low or ("pinned" in low and "tashkent today" in low):
                logger.info(f"Skip pinned service post {mid}")
                save_posted_id(mid); continue
            th_text=format_text_threads(raw_text, mid, True)
            pub_img=None
            if p["photo"]:
                try: pub_img=upload_to_public_host(p["photo"], "image.jpg", "image/jpeg") or p["photo"]
                except: pub_img=p["photo"]
            th_res=post_to_threads(th_text, pub_img=pub_img) if THREADS_ENABLED else None
            if th_res:
                save_posted_id(mid); added+=1; time.sleep(4)
        logger.info(f"=== Сверка 20: добавлено {added}, пропущено {skipped} ===")
    except Exception as e:
        logger.error(f"sync error: {e}", exc_info=True)

async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Сверка 20 постов...")
    await sync_missing_posts_job(context)
    await update.message.reply_text("✅ Готово 20")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    posts=fetch_last_channel_posts(20)
    lines=[f"Всего: {len(POSTED_IDS)}", f"График {WORK_START_HOUR}-{WORK_END_HOUR}",""]
    for p in posts:
        status="✅ есть" if is_already_posted(p["id"]) else "❌ нету"
        preview=(p["text"][:35].replace("\n"," ")+"...") if p["text"] else ""
        lines.append(f"{status} {p['id']} {preview}")
    await update.message.reply_text("\n".join(lines))

def is_service_pinned_post(post, raw_text):
    if getattr(post, 'pinned_message', None) is not None:
        return True
    if not raw_text: return False
    low=raw_text.lower()
    if "pinned a photo" in low or "pinned a video" in low or "pinned a message" in low:
        return True
    if "tashkent today - новости ташкента pinned" in low:
        return True
    return False

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post=update.channel_post
    if not post: return
    raw_text=post.text or post.caption or ""
    if is_service_pinned_post(post, raw_text):
        logger.info(f"Skip service pinned post {post.message_id}")
        save_posted_id(post.message_id)
        return
    mid=post.message_id
    if is_already_posted(mid):
        logger.info(f"Skip duplicate {mid} already posted")
        return
    save_posted_id(mid)
    if post.media_group_id:
        mg_id=post.media_group_id
        MEDIA_GROUP_CACHE[mg_id].append(post)
        txt=post.text or post.caption or ""
        if txt: MEDIA_GROUP_LAST_TEXT[mg_id]=(txt, post.message_id)
        if not context.job_queue.get_jobs_by_name(f"album_{mg_id}"):
            context.job_queue.run_once(process_album_job, when=7, name=f"album_{mg_id}", data=mg_id)
        return
    if not raw_text: return
    if not is_working_hours(): return
    is_video=bool(post.video or post.animation or post.video_note or (post.document and post.document.mime_type and post.document.mime_type.startswith("video/")))
    if is_video and (DISABLE_VIDEO or THREADS_DISABLE_VIDEO):
        return
    def get_full_tg_url(fp):
        if not fp: return None
        if fp.startswith("http"): return fp
        return f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{fp}"
    th_text=format_text_threads(raw_text, mid, True)
    tg_img=None; pub_img=None
    if post.photo:
        for attempt in range(3):
            try:
                file_id=post.photo[-1].file_id
                file=await context.bot.get_file(file_id)
                tg_img=get_full_tg_url(file.file_path)
                if tg_img:
                    pub_img=upload_to_public_host(tg_img, "image.jpg", "image/jpeg")
                    if pub_img:
                        logger.info(f"Фото -> public: {pub_img} attempt {attempt+1}")
                        break
            except Exception as e:
                logger.error(f"Photo error attempt {attempt+1}: {e}", exc_info=True)
                time.sleep(2)
    post_to_threads(th_text, tg_img, pub_img) if THREADS_ENABLED else None

async def process_album_job(context: ContextTypes.DEFAULT_TYPE):
    mg_id=context.job.data
    posts=MEDIA_GROUP_CACHE.pop(mg_id, []); text_info=MEDIA_GROUP_LAST_TEXT.pop(mg_id, None)
    if not posts: return
    if not text_info:
        for p in posts:
            if p.caption: text_info=(p.caption, p.message_id); break
    raw_text, mid=text_info if text_info else ("", posts[0].message_id)
    if not raw_text or is_already_posted(mid): return
    if is_service_pinned_post(posts[0], raw_text):
        logger.info(f"Skip service pinned album {mid}")
        save_posted_id(mid); return
    if not is_working_hours(): return
    save_posted_id(mid)
    def get_full_tg_url(fp):
        if not fp: return None
        if fp.startswith("http"): return fp
        return f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{fp}"
    pub_imgs=[]
    for idx,p in enumerate(posts):
        if p.photo:
            for attempt in range(3):
                try:
                    file_id=p.photo[-1].file_id
                    file=await context.bot.get_file(file_id)
                    tg_url=get_full_tg_url(file.file_path)
                    if tg_url:
                        pub=upload_to_public_host(tg_url, f"image_{idx}.jpg", "image/jpeg")
                        if pub: 
                            pub_imgs.append(pub)
                            break
                except Exception as e:
                    logger.error(f"Album error attempt {attempt+1}: {e}")
                    time.sleep(1)
    if not pub_imgs: 
        logger.warning("Album: no images after retries")
        return
    th_text=format_text_threads(raw_text, mid, True)
    post_to_threads(th_text, pub_imgs=pub_imgs) if THREADS_ENABLED else None

async def auto_refresh_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Авто-проверка токенов...")
    try: refresh_threads_token()
    except: pass

async def error_handler(update, context):
    err=str(context.error)
    if "409" in err or "Conflict" in err or "TimedOut" in err or "Timed out" in err:
        logger.warning(f"Telegram timeout/conflict: {err[:200]}")
        time.sleep(5)
    else:
        logger.error(f"Error: {context.error}")

def main():
    t=threading.Thread(target=start_keepalive, daemon=True); t.start(); time.sleep(1)
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Нет TELEGRAM_BOT_TOKEN!"); return
    load_posted_ids()
    for _ in range(2):
        try:
            requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=False", timeout=10)
            time.sleep(2); break
        except: time.sleep(2)
    global THREADS_TOKEN, FB_PAGE_TOKEN, FB_USER_TOKEN
    THREADS_TOKEN=load_token(THREADS_TOKEN_FILE, THREADS_TOKEN_ENV)
    FB_PAGE_TOKEN=load_token(FB_PAGE_TOKEN_FILE, FB_PAGE_TOKEN_ENV)
    FB_USER_TOKEN=load_token(FB_USER_TOKEN_FILE, FB_USER_TOKEN_ENV)
    try: asyncio.get_event_loop()
    except RuntimeError: asyncio.set_event_loop(asyncio.new_event_loop())
    app=Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_error_handler(error_handler)
    if app.job_queue:
        app.job_queue.run_repeating(auto_refresh_job, interval=24*60*60, first=60)
    now_t=datetime.now(TASHKENT_TZ).strftime("%H:%M")
    logger.info(f"Бот {SOURCE_CHANNEL} -> Threads {THREADS_USER_ID} | График {WORK_START_HOUR}-{WORK_END_HOUR} | Дедуп {len(POSTED_IDS)}")
    app.run_polling(allowed_updates=["channel_post","message"], poll_interval=15.0, timeout=60, drop_pending_updates=False)

if __name__=="__main__": main()
