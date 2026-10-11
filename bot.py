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
DISABLE_VIDEO = os.getenv("DISABLE_VIDEO", "true").lower() in ("1","true","yes")
THREADS_DISABLE_VIDEO = os.getenv("THREADS_DISABLE_VIDEO", "true").lower() in ("1","true","yes")
FB_ENABLED = os.getenv("FB_ENABLED", "true").lower() in ("1","true","yes")
FB_PAGE_ENABLED = os.getenv("FB_PAGE_ENABLED", "true").lower() in ("1","true","yes")
THREADS_ENABLED = os.getenv("THREADS_ENABLED", "true").lower() in ("1","true","yes")

def is_fb_enabled(): return FB_ENABLED and FB_PAGE_ENABLED
def is_working_hours(now=None):
    if now is None: now = datetime.now(TASHKENT_TZ)
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
    except Exception as e:
        logger.error(f"load_posted_ids: {e}")
        POSTED_IDS = set()

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

def format_text_facebook(text, mid, with_link=True):
    text=text.strip()
    paras=[p.strip() for p in re.split(r'\n\s*\n+', text) if p.strip()]
    if not paras: return ""
    title=paras[0]; first=paras[1] if len(paras)>1 else ""
    base=f"{title}\n\n{first}" if first else title
    if with_link:
        clean=SOURCE_CHANNEL.replace('@','').strip()
        link=f"https://t.me/{clean}/{mid}"
        base=re.sub(rf'@?{re.escape(clean)}\s*$','',base,flags=re.I).strip()
        base=re.sub(rf'https?://t\.me/{re.escape(clean)}/\d+\s*$','',base,flags=re.I).strip()
        result=f"{base}\n\nПодробнее: {link}"
        if len(result)>1000 and first:
            allowed=1000-len(title)-len(f"\n\n\n\nПодробнее: {link}")-10
            if allowed>50:
                cut=first[:allowed].rsplit(' ',1)[0]+"..."
                result=f"{title}\n\n{cut}\n\nПодробнее: {link}"
        return result
    return base[:997]+"..." if len(base)>1000 else base

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

def upload_to_public_host(tg_url, filename, mime):
    try:
        r=requests.get(tg_url,timeout=60)
        if r.status_code!=200 or len(r.content)<100: return None
        data=r.content
        try:
            files={"fileToUpload":(filename,data,mime)}
            cr=requests.post("https://catbox.moe/user/api.php",data={"reqtype":"fileupload"},files=files,timeout=30)
            if cr.status_code==200 and cr.text.startswith("http"): return cr.text.strip()
        except: pass
        try:
            files={"file":(filename,data,mime)}
            r2=requests.post("https://0x0.st",files=files,timeout=30)
            if r2.status_code==200 and r2.text.startswith("http"): return r2.text.strip()
        except: pass
    except Exception as e:
        logger.error(f"upload error: {e}")
    return None

def post_to_facebook(text, tg_img=None, pub_img=None, video_url=None, pub_imgs=None):
    if not is_fb_enabled(): return None
    if not FB_PAGE_TOKEN or not FB_PAGE_ID: return None
    try:
        if video_url:
            url=f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/videos"
            data={"description":text,"file_url":video_url,"access_token":FB_PAGE_TOKEN}
            r=requests.post(url,data=data,timeout=120)
            return r.json() if r.status_code==200 else None
        img_list=pub_imgs or ([pub_img or tg_img] if (pub_img or tg_img) else [])
        if len(img_list)>1:
            media_ids=[]
            for img_url in img_list[:10]:
                try:
                    up_url=f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/photos"
                    up_data={"url":img_url,"published":False,"temporary":True,"access_token":FB_PAGE_TOKEN}
                    ur=requests.post(up_url,data=up_data,timeout=30)
                    fid=ur.json().get("id")
                    if fid: media_ids.append(fid)
                except: pass
                time.sleep(0.5)
            if len(media_ids)>=2:
                feed_url=f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/feed"
                feed_data={"message":text,"access_token":FB_PAGE_TOKEN}
                for i,mid in enumerate(media_ids):
                    feed_data[f"attached_media[{i}]"]=f'{{"media_fbid":"{mid}"}}'
                fr=requests.post(feed_url,data=feed_data,timeout=30)
                if fr.status_code==200 and fr.json().get("id"): return fr.json()
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
                return r2.json()
        payload={"media_type":"TEXT","text":short_text,"access_token":THREADS_TOKEN}
        r=requests.post(create_url,data=payload,timeout=30)
        cid=r.json().get("id")
        if not cid: return None
        time.sleep(2)
        pub_url=f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
        r2=requests.post(pub_url,data={"creation_id":cid,"access_token":THREADS_TOKEN},timeout=30)
        return r2.json()
    except Exception as e:
        logger.error(f"Threads error: {e}", exc_info=True)
        return None

# === СВЕРКА ПОСЛЕДНИХ 10 - ФИКС ПАРСЕРА ===
def fetch_last_channel_posts(n=10):
    try:
        clean=SOURCE_CHANNEL.replace('@','').strip()
        url=f"https://t.me/s/{clean}"
        r=requests.get(url,timeout=20,headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        if r.status_code!=200:
            logger.warning(f"t.me/s status {r.status_code}")
            return []
        html_text=r.text
        # режем по врапперам
        wraps=re.split(r'<div class="tgme_widget_message_wrap[^"]*">', html_text)
        posts=[]
        for wrap in wraps:
            m=re.search(rf'data-post="{re.escape(clean)}/(\d+)"', wrap)
            if not m: continue
            mid=int(m.group(1))
            # текст - может быть message_text или message_caption
            text=""
            tm=re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', wrap, re.DOTALL)
            if tm:
                raw=tm.group(1)
                raw=re.sub(r'<br\s*/?>', '\n', raw)
                raw=re.sub(r'<[^>]+>', '', raw)
                raw=html.unescape(raw).strip()
                text=raw
            if not text:
                # попробуем caption (для фото)
                cm=re.search(r'<div class="tgme_widget_message_caption[^"]*"[^>]*>(.*?)</div>', wrap, re.DOTALL)
                if cm:
                    raw=cm.group(1)
                    raw=re.sub(r'<br\s*/?>', '\n', raw)
                    raw=re.sub(r'<[^>]+>', '', raw)
                    raw=html.unescape(raw).strip()
                    text=raw
            # фото
            photo=None
            pm=re.search(r"background-image:url\('(.*?)'\)", wrap)
            if pm:
                photo=pm.group(1)
                if photo.startswith("//"): photo="https:"+photo
                # убираем превью параметры
                photo=photo.replace("\\", "")
            # только если есть хоть что-то
            if text or photo:
                posts.append({"id":mid,"text":text,"photo":photo})
        # сортируем по id desc и берем n
        posts=sorted(posts, key=lambda x: x["id"], reverse=True)[:n]
        logger.info(f"fetch_last_channel_posts: нашлось {len(posts)} постов IDs {[p['id'] for p in posts]}")
        return posts
    except Exception as e:
        logger.error(f"fetch_last_channel_posts error: {e}", exc_info=True)
        return []

async def sync_missing_posts_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_working_hours():
            logger.info("Синхронизация пропущена - вне графика 9-21")
            return
        logger.info("=== Старт сверки последних 10 постов ===")
        posts=fetch_last_channel_posts(10)
        added=0; skipped=0; empty=0
        for p in posts:
            mid=p["id"]
            if is_already_posted(mid):
                logger.info(f"Пост {mid} уже есть - Пропуск")
                skipped+=1
                continue
            raw_text=p["text"] or ""
            if not raw_text:
                logger.info(f"Пост {mid} без текста - пропускаем (не сохраняем)")
                empty+=1
                continue
            if len(raw_text)<5:
                logger.info(f"Пост {mid} слишком короткий - пропускаем")
                continue
            logger.info(f"Пост {mid} отсутствует - добавляем в Threads")
            th_text=format_text_threads(raw_text, mid, with_link=True)
            pub_img=None
            if p["photo"]:
                try:
                    pub_img=upload_to_public_host(p["photo"], "image.jpg", "image/jpeg") or p["photo"]
                except:
                    pub_img=p["photo"]
            th_res=None
            if THREADS_ENABLED:
                th_res=post_to_threads(th_text, pub_img=pub_img)
            if th_res:
                save_posted_id(mid)
                added+=1
                time.sleep(4)
            else:
                logger.warning(f"Пост {mid} не удалось добавить")
        logger.info(f"=== Сверка завершена: добавлено {added}, пропущено {skipped}, пустых {empty} ===")
    except Exception as e:
        logger.error(f"sync_missing_posts_job error: {e}", exc_info=True)

async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Начинаю сверку последних 10 постов...")
    await sync_missing_posts_job(context)
    await update.message.reply_text("✅ Сверка завершена. Проверь логи.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    posts=fetch_last_channel_posts(10)
    lines=[f"Всего в дедупе: {len(POSTED_IDS)}", f"График: {WORK_START_HOUR}-{WORK_END_HOUR} Ташкент",""]
    for p in posts:
        status="✅ есть" if is_already_posted(p["id"]) else "❌ нету"
        preview=(p["text"][:40].replace("\n"," ")+"...") if p["text"] else "(без текста)"
        lines.append(f"{status} {p['id']} - {preview}")
    await update.message.reply_text("\n".join(lines))

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post=update.channel_post
    if not post: return
    if post.media_group_id:
        mg_id=post.media_group_id
        MEDIA_GROUP_CACHE[mg_id].append(post)
        txt=post.text or post.caption or ""
        if txt: MEDIA_GROUP_LAST_TEXT[mg_id]=(txt, post.message_id)
        jobs=context.job_queue.get_jobs_by_name(f"album_{mg_id}")
        if not jobs:
            context.job_queue.run_once(process_album_job, when=7, name=f"album_{mg_id}", data=mg_id)
        return
    raw_text=post.text or post.caption or ""
    if not raw_text: return
    mid=post.message_id
    if is_already_posted(mid): return
    if not is_working_hours():
        save_posted_id(mid); return
    is_video=bool(post.video or post.animation or post.video_note or (post.document and post.document.mime_type and post.document.mime_type.startswith("video/")))
    if is_video and (DISABLE_VIDEO or THREADS_DISABLE_VIDEO):
        save_posted_id(mid); return
    def get_full_tg_url(file_path):
        if not file_path: return None
        if file_path.startswith("http"): return file_path
        return f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    th_text=format_text_threads(raw_text, mid, with_link=not is_video)
    fb_text=format_text_facebook(raw_text, mid, with_link=not is_video)
    tg_img=None; pub_img=None; pub_video=None
    if post.photo:
        try:
            file_id=post.photo[-1].file_id
            file=await context.bot.get_file(file_id)
            tg_img=get_full_tg_url(file.file_path)
            if tg_img: pub_img=upload_to_public_host(tg_img, "image.jpg", "image/jpeg")
        except Exception as e:
            logger.error(f"Photo error: {e}", exc_info=True)
    fb_res=None; th_res=None
    if is_fb_enabled(): fb_res=post_to_facebook(fb_text, tg_img, pub_img, video_url=pub_video)
    if THREADS_ENABLED: th_res=post_to_threads(th_text, tg_img, pub_img, video_url=pub_video)
    if fb_res or th_res or (not is_fb_enabled() and not THREADS_ENABLED): save_posted_id(mid)

async def process_album_job(context: ContextTypes.DEFAULT_TYPE):
    mg_id=context.job.data
    posts=MEDIA_GROUP_CACHE.pop(mg_id, [])
    text_info=MEDIA_GROUP_LAST_TEXT.pop(mg_id, None)
    if not posts: return
    if not text_info:
        for p in posts:
            if p.caption:
                text_info=(p.caption, p.message_id); break
    raw_text, mid=text_info if text_info else ("", posts[0].message_id)
    if not raw_text: return
    if is_already_posted(mid): return
    if not is_working_hours(): save_posted_id(mid); return
    def get_full_tg_url(file_path):
        if not file_path: return None
        if file_path.startswith("http"): return file_path
        return f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    pub_imgs=[]
    for idx,p in enumerate(posts):
        if p.photo:
            try:
                file_id=p.photo[-1].file_id
                file=await context.bot.get_file(file_id)
                tg_url=get_full_tg_url(file.file_path)
                if not tg_url: continue
                pub=upload_to_public_host(tg_url, f"image_{idx}.jpg", "image/jpeg")
                if pub: pub_imgs.append(pub)
            except: pass
    if not pub_imgs: return
    fb_text=format_text_facebook(raw_text, mid, with_link=True)
    th_text=format_text_threads(raw_text, mid, with_link=True)
    fb_res=None; th_res=None
    if is_fb_enabled(): fb_res=post_to_facebook(fb_text, pub_imgs=pub_imgs)
    if THREADS_ENABLED: th_res=post_to_threads(th_text, pub_imgs=pub_imgs)
    if fb_res or th_res: save_posted_id(mid)

async def auto_refresh_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Авто-проверка токенов... FB отключен, пропуск")
    try:
        check_threads_expiry()
        refresh_threads_token()
    except:
        pass

   async def error_handler(update, context):
       err = str(context.error)
       if "409" in err or "Conflict" in err or "TimedOut" in err or "Timed out" in err:
           logger.warning(f"Telegram timeout/conflict, retry: {err[:200]}")
           time.sleep(5)
       else:
           logger.error(f"Error: {context.error}")

def main():
    t=threading.Thread(target=start_keepalive, daemon=True); t.start(); time.sleep(1)
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Нет TELEGRAM_BOT_TOKEN!"); return
    load_posted_ids()
    for attempt in range(2):
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
        app.job_queue.run_once(sync_missing_posts_job, when=60, name="initial_sync")
    now_t=datetime.now(TASHKENT_TZ).strftime("%H:%M")
    vid_fb="ВЫКЛ" if DISABLE_VIDEO else "ВКЛ"
    vid_th="ВЫКЛ" if THREADS_DISABLE_VIDEO else "ВКЛ"
    logger.info(f"Бот запущен {SOURCE_CHANNEL} -> FB Page {FB_PAGE_ID} enabled={is_fb_enabled()} (FB_ENABLED={FB_ENABLED} FB_PAGE_ENABLED={FB_PAGE_ENABLED}) | Threads {THREADS_USER_ID} enabled={THREADS_ENABLED} | Время {now_t} График {WORK_START_HOUR}-{WORK_END_HOUR}: {is_working_hours()} | Видео FB {vid_fb} Threads {vid_th} | Дедуп: {len(POSTED_IDS)}")
    app.run_polling(allowed_updates=["channel_post","message"], poll_interval=15.0, timeout=60, drop_pending_updates=False)

if __name__=="__main__": main()
