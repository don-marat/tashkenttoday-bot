import os, requests, json, re, html, time, base64
from datetime import datetime, timezone, timedelta

TASHKENT_TZ = timezone(timedelta(hours=5))
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
    return 9 <= now.hour < 21

def format_text_threads(text, mid):
    text = text.strip()
    paras = [p.strip() for p in re.split(r'\n\s*\n+', text) if p.strip()]
    if not paras:
        return ""
    title = paras[0].strip()
    first = paras[1] if len(paras) > 1 else ""
    clean = SOURCE_CHANNEL.replace('@', '').strip()
    link = f"https://t.me/{clean}/{mid}"
    more = f"Подробнее: {link}"
    LIMIT = 500
    if first:
        cand = f"{title}\n\n{first}\n\n{more}"
        if len(cand) <= LIMIT:
            return cand
    cand_title = f"{title}\n\n{more}"
    if len(cand_title) <= LIMIT:
        return cand_title
    return title[:497] + "..." if len(title) > LIMIT else title

def upload_to_public_host(tg_url, filename="image.jpg", mime="image/jpeg"):
    data = None
    for attempt in range(3):
        try:
            r = requests.get(tg_url, timeout=90)
            if r.status_code == 200 and len(r.content) >= 500:
                data = r.content
                print(f"download {len(data)} bytes attempt {attempt+1}")
                break
        except Exception as e:
            print(f"tg download error {e} attempt {attempt+1}")
            time.sleep(2)
    if not data:
        print("tg download failed after 3 attempts")
        return None
    try:
        files = {"fileToUpload": (filename, data, mime)}
        cr = requests.post("https://catbox.moe/user/api.php", data={"reqtype": "fileupload"}, files=files, timeout=30)
        if cr.status_code == 200 and cr.text.startswith("http"):
            print(f"catbox OK {cr.text.strip()}")
            return cr.text.strip()
    except Exception as e:
        print(f"catbox error {e}")
    try:
        files = {"file": (filename, data, mime)}
        r2 = requests.post("https://0x0.st", files=files, timeout=30)
        if r2.status_code == 200 and r2.text.startswith("http"):
            print(f"0x0.st OK {r2.text.strip()}")
            return r2.text.strip()
    except Exception as e:
        print(f"0x0.st error {e}")
    return tg_url

def post_to_threads(text, pub_img=None):
    if not THREADS_TOKEN or not THREADS_USER_ID:
        return None
    try:
        create_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
        short_text = text[:500]
        if pub_img:
            payload = {"media_type": "IMAGE", "image_url": pub_img, "text": short_text, "access_token": THREADS_TOKEN}
            r = requests.post(create_url, data=payload, timeout=90)
            print(f"Threads IMAGE create {r.status_code} {r.text[:300]}")
            if r.status_code == 200 and r.json().get("id"):
                cid = r.json().get("id")
                time.sleep(5)
                pub_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
                r2 = requests.post(pub_url, data={"creation_id": cid, "access_token": THREADS_TOKEN}, timeout=30)
                print(f"Threads IMAGE publish {r2.status_code}")
                return r2.json()
        payload = {"media_type": "TEXT", "text": short_text, "access_token": THREADS_TOKEN}
        r = requests.post(create_url, data=payload, timeout=30)
        cid = r.json().get("id")
        if not cid:
            print(f"Threads TEXT fail {r.text[:500]}")
            return None
        time.sleep(2)
        pub_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
        r2 = requests.post(pub_url, data={"creation_id": cid, "access_token": THREADS_TOKEN}, timeout=30)
        print(f"Threads TEXT publish {r2.status_code}")
        return r2.json()
    except Exception as e:
        print(f"Threads error {e}")
        return None

def fetch_last_channel_posts(n=20):
    try:
        clean = SOURCE_CHANNEL.replace('@', '').strip()
        url = f"https://t.me/s/{clean}"
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            print(f"fetch fail {r.status_code}")
            return []
        wraps = re.split(r'<div class="tgme_widget_message_wrap[^"]*">', r.text)
        posts = []
        for wrap in wraps:
            m = re.search(rf'data-post="{re.escape(clean)}/(\d+)"', wrap)
            if not m:
                continue
            mid = int(m.group(1))
            text = ""
            tm = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', wrap, re.DOTALL)
            if tm:
                raw = tm.group(1)
                raw = re.sub(r'<br\s*/?>', '\n', raw)
                raw = re.sub(r'<[^>]+>', '', raw)
                text = html.unescape(raw).strip()
            if not text:
                cm = re.search(r'<div class="tgme_widget_message_caption[^"]*"[^>]*>(.*?)</div>', wrap, re.DOTALL)
                if cm:
                    raw = cm.group(1)
                    raw = re.sub(r'<br\s*/?>', '\n', raw)
                    raw = re.sub(r'<[^>]+>', '', raw)
                    text = html.unescape(raw).strip()
            photo = None
            pm = re.search(r"background-image:url\('(.*?)'\)", wrap)
            if pm:
                photo = pm.group(1)
                if photo.startswith("//"):
                    photo = "https:" + photo
                photo = photo.replace("\\", "")
            if text or photo:
                posts.append({"id": mid, "text": text, "photo": photo})
        return sorted(posts, key=lambda x: x["id"], reverse=True)[:n]
    except Exception as e:
        print(f"fetch error {e}")
        return []

def push_posted():
    if not GITHUB_TOKEN:
        return
    try:
        import base64
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{POSTED_FILE}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        r = requests.get(url, headers=headers, timeout=15)
        sha = r.json().get("sha") if r.status_code == 200 else None
        content = open(POSTED_FILE, "r", encoding="utf-8").read()
        b64 = base64.b64encode(content.encode()).decode()
        data = {"message": f"Update posted_ids {len(POSTED_IDS)} {datetime.now(TASHKENT_TZ).strftime('%Y-%m-%d %H:%M')}", "content": b64, "branch": "main"}
        if sha:
            data["sha"] = sha
        r2 = requests.put(url, headers=headers, json=data, timeout=20)
        print(f"GitHub push {r2.status_code}")
    except Exception as e:
        print(f"push error {e}")

if not is_working_hours():
    print("Вне графика 9-21, skip")
    exit(0)
if len(POSTED_IDS) == 0:
    print("Дедуп 0 — первый запуск, не постим 20 старых")
    exit(0)

posts = fetch_last_channel_posts(20)
added = 0
for p in posts:
    mid = p["id"]
    if is_already_posted(mid):
        continue
    raw = p["text"] or ""
    if not raw or len(raw) < 5:
        continue
    low = raw.lower()
    if "pinned a photo" in low or "pinned a video" in low:
        save_posted_id(mid)
        continue
    th_text = format_text_threads(raw, mid)
    pub_img = None
    if p["photo"]:
        pub_img = upload_to_public_host(p["photo"]) or p["photo"]
    res = post_to_threads(th_text, pub_img)
    if res:
        save_posted_id(mid)
        added += 1
        time.sleep(5)

if added > 0:
    push_posted()
print(f"Done added {added}")
