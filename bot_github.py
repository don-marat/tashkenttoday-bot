import os, requests, json, re, html, time, base64
from datetime import datetime, timezone, timedelta

WORK_START_HOUR = int(os.getenv("WORK_START_HOUR", "9"))
WORK_END_HOUR = int(os.getenv("WORK_END_HOUR", "21"))
TASHKENT_TZ = timezone(timedelta(hours=5))
THREADS_ENABLED = os.getenv("THREADS_ENABLED", "true").lower() in ("1","true","yes")

SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL", "@tashkenttodayuz")
THREADS_USER_ID = os.getenv("THREADS_USER_ID", "27092394720363294")
THREADS_TOKEN = os.getenv("THREADS_TOKEN")
POSTED_FILE = os.getenv("POSTED_FILE", "posted_ids.json")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "don-marat/tashkenttoday-bot")

POSTED_IDS = set()
if os.path.exists(POSTED_FILE):
    try:
        POSTED_IDS = set(json.load(open(POSTED_FILE, "r", encoding="utf-8")))
        print(f"Загружено {len(POSTED_IDS)} ID")
    except:
        POSTED_IDS = set()

def save_posted_id(mid):
    POSTED_IDS.add(mid)
    to_save = sorted(list(POSTED_IDS))[-1000:]
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(to_save, f)
    print(f"ID {mid} сохранен, всего {len(POSTED_IDS)}")

def is_already_posted(mid):
    return mid in POSTED_IDS

def is_working_hours():
    now = datetime.now(TASHKENT_TZ)
    return WORK_START_HOUR <= now.hour < WORK_END_HOUR

# --- ФИЛЬТРЫ ---
SKIP_KEYWORDS = [
    "фото недели",
    "фото дня",
    "фото месяца",
    "фото от подписчика",
    "ретро фото",
    "архивное фото",
    "доброе утро",
]

def should_skip(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    for kw in SKIP_KEYWORDS:
        if kw in low:
            return True
    if "место:" in low and "автор:" in low:
        return True
    if low.strip().startswith("место:"):
        return True
    return False

def format_text_threads(text, mid, with_link=True):
    text = text.strip()
    paras = [p.strip() for p in re.split(r'\n\s*\n+', text) if p.strip()]
    if not paras:
        return ""
    title = paras[0].strip()
    first = paras[1] if len(paras) > 1 else ""
    first = first.strip()
    clean = SOURCE_CHANNEL.replace('@','').strip()
    link = f"t.me/{clean}/{mid}"
    more = f"Подробнее: {link}"
    LIMIT = 500
    if not with_link:
        base = f"{title}\n\n{first}" if first else title
        return base[:LIMIT-3]+"..." if len(base)>LIMIT else base
    cand = f"{title}\n\n{first}\n\n{more}" if first else f"{title}\n\n{more}"
    if len(cand) <= LIMIT:
        return cand
    reserved = len(title)+len(more)+4
    allowed = LIMIT-reserved
    if first and allowed>80:
        cut = first[:allowed]
        if len(first)>allowed:
            cut = cut.rsplit(' ',1)[0]+"..."
        cand2 = f"{title}\n\n{cut}\n\n{more}"
        if len(cand2) <= LIMIT:
            return cand2
    cand3 = f"{title}\n\n{more}"
    if len(cand3) <= LIMIT:
        return cand3
    allowed_title = LIMIT-len(more)-2-3
    if allowed_title>30:
        title_cut = title[:allowed_title].rsplit(' ',1)[0]+"..."
        return f"{title_cut}\n\n{more}"
    return more

def upload_to_public_host(tg_url, filename="image.jpg", mime="image/jpeg"):
    try:
        r = requests.get(tg_url, timeout=15)
        if r.status_code != 200 or len(r.content) < 1000:
            return tg_url
        data = r.content
    except:
        return tg_url
    try:
        files = {'file': (filename, data, mime)}
        r = requests.post('https://0x0.st', files=files, timeout=15)
        if r.status_code == 200 and r.text.startswith('https://'):
            return r.text.strip()
    except:
        pass
    try:
        files = {'fileToUpload': (filename, data, mime)}
        r = requests.post('https://catbox.moe/user/api.php', data={'reqtype':'fileupload'}, files=files, timeout=15)
        if r.status_code == 200 and r.text.startswith('https://'):
            return r.text.strip()
    except:
        pass
    return tg_url

def post_to_threads(text, tg_img=None, pub_img=None):
    if not THREADS_ENABLED or not THREADS_TOKEN or not THREADS_USER_ID:
        return None
    try:
        create_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
        short_text = text
        if len(short_text) > 500:
            if "Подробнее:" in short_text:
                idx = short_text.rfind("Подробнее:")
                more = short_text[idx:].strip()
                head = short_text[:idx].strip()
                allowed = 500 - len("\n\n" + more)
                if len(head) > allowed:
                    head = head[:allowed].rsplit(' ',1)[0] + "..."
                short_text = f"{head}\n\n{more}" if head else more
            else:
                short_text = short_text[:500]

        img_to_use = pub_img or tg_img
        if img_to_use:
            payload = {"media_type":"IMAGE","text":short_text,"image_url":img_to_use,"access_token":THREADS_TOKEN}
            r = requests.post(create_url, data=payload, timeout=20)
            cid = r.json().get("id")
            if cid:
                time.sleep(4)
                pub_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
                r2 = requests.post(pub_url, data={"creation_id":cid,"access_token":THREADS_TOKEN}, timeout=20)
                if r2.status_code==200 and r2.json().get("id"):
                    return r2.json()
        payload = {"media_type":"TEXT","text":short_text,"access_token":THREADS_TOKEN}
        r = requests.post(create_url, data=payload, timeout=20)
        cid = r.json().get("id")
        if not cid:
            return None
        time.sleep(2)
        pub_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
        r2 = requests.post(pub_url, data={"creation_id":cid,"access_token":THREADS_TOKEN}, timeout=20)
        return r2.json() if r2.status_code==200 else None
    except Exception as e:
        print(f"Threads error {e}")
        return None

def fetch_last_channel_posts(n=20):
    try:
        clean = SOURCE_CHANNEL.replace('@','').strip()
        url = f"https://t.me/s/{clean}"
        r = requests.get(url, timeout=15, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code != 200:
            return []
        wraps = re.split(r'<div class="tgme_widget_message_wrap[^"]*">', r.text)
        posts = []
        for wrap in wraps:
            m = re.search(rf'data-post="{re.escape(clean)}/(\d+)"', wrap)
            if not m:
                continue
            mid = int(m.group(1))
            is_video = False
            low = wrap.lower()
            if '<video' in low or 'tgme_widget_message_video' in low or 'video_player' in low or 'tl_video' in low or 'video_duration' in low:
                is_video = True

            text = ""
            tm = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', wrap, re.DOTALL)
            if tm:
                raw = tm.group(1); raw = re.sub(r'<br\s*/?>','\n',raw); raw = re.sub(r'<[^>]+>','',raw); text = html.unescape(raw).strip()
            if not text:
                cm = re.search(r'<div class="tgme_widget_message_caption[^"]*"[^>]*>(.*?)</div>', wrap, re.DOTALL)
                if cm:
                    raw = cm.group(1); raw = re.sub(r'<br\s*/?>','\n',raw); raw = re.sub(r'<[^>]+>','',raw); text = html.unescape(raw).strip()
            photo = None
            pm = re.search(r"background-image:url\('(.*?)'\)", wrap)
            if pm:
                photo = pm.group(1)
                if photo.startswith("//"):
                    photo = "https:"+photo
                photo = photo.replace("\\","")
            if text or photo or is_video:
                posts.append({"id":mid,"text":text,"photo":photo,"is_video":is_video})
        posts = sorted(posts, key=lambda x:x["id"], reverse=True)[:n]
        return posts
    except Exception as e:
        print(f"fetch error {e}")
        return []

def push_posted():
    if not GITHUB_TOKEN:
        return
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{POSTED_FILE}"
        headers = {"Authorization":f"token {GITHUB_TOKEN}","Accept":"application/vnd.github.v3+json"}
        r = requests.get(url, headers=headers, timeout=10)
        sha = r.json().get("sha") if r.status_code==200 else None
        content = open(POSTED_FILE,"r",encoding="utf-8").read()
        b64 = base64.b64encode(content.encode()).decode()
        data = {"message":f"Update posted_ids {len(POSTED_IDS)}","content":b64,"branch":"main"}
        if sha:
            data["sha"]=sha
        r2 = requests.put(url, headers=headers, json=data, timeout=15)
        print(f"GitHub push {r2.status_code}")
    except Exception as e:
        print(f"push error {e}")

# --- MAIN ---
if not is_working_hours():
    print(f"Вне графика {WORK_START_HOUR}-{WORK_END_HOUR}, skip")
    exit(0)

posts = fetch_last_channel_posts(20)
print(f"POSTED_IDS len {len(POSTED_IDS)} fetched {len(posts)}")

if len(POSTED_IDS)==0 and posts:
    for p in posts:
        save_posted_id(p["id"])
    push_posted()
    print("Bootstrap: сохранили 20 ID без постинга")
    exit(0)

added = 0
skipped_video = 0
skipped_photo = 0

for p in reversed(posts):
    mid = p["id"]
    if is_already_posted(mid):
        continue
    raw = p["text"] or ""

    # 1. СКИП ВИДЕО
    if p.get("is_video"):
        print(f"SKIP VIDEO {mid}")
        save_posted_id(mid)
        skipped_video+=1
        continue

    if not raw or len(raw)<5:
        save_posted_id(mid)
        continue
    if "pinned a photo" in raw.lower() or "pinned a video" in raw.lower():
        save_posted_id(mid)
        continue

    # 2. СКИП ФОТО НЕДЕЛИ
    if should_skip(raw):
        print(f"SKIP PHOTO_WEEK {mid}: {raw[:60]}")
        save_posted_id(mid)
        skipped_photo+=1
        continue

    th_text = format_text_threads(raw, mid, with_link=True)
    pub_img = upload_to_public_host(p["photo"]) if p["photo"] else None
    print(f"Posting {mid}: {th_text[:80]}...")
    res = post_to_threads(th_text, tg_img=p["photo"], pub_img=pub_img)
    if res and res.get("id"):
        save_posted_id(mid)
        added+=1
        time.sleep(2)

if added>0 or skipped_video>0 or skipped_photo>0:
    push_posted()

print(f"Done added {added} skipped_video {skipped_video} skipped_photo {skipped_photo}")
