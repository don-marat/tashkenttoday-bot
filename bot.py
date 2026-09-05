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
FB_PAGE_TOKEN = os.getenv("FB_PAGE_TOKEN")
THREADS_USER_ID = os.getenv("THREADS_USER_ID", "27092394720363294")
THREADS_TOKEN = os.getenv("THREADS_TOKEN")
SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL", "@tashkenttodayuz")
SOURCE_USERNAME = "tashkenttodayuz"

def get_post_link(message_id):
    return f"https://t.me/{SOURCE_USERNAME}/{message_id}"

def format_text_threads(text, message_id):
    # Для Threads: только заголовок + ссылка (лимит 500)
    link = get_post_link(message_id)
    title = text.strip().split('\n')[0].strip()
    return f"{title}\n\nПодробнее: {link}"

def format_text_facebook(text, message_id):
    # Для Facebook: заголовок + первый абзац + ссылка
    link = get_post_link(message_id)
    clean = text.strip()
    if not clean:
        return f"Подробнее: {link}"
    
    # Заголовок = первая строка
    title = clean.split('\n')[0].strip()
    
    # Остальной текст после заголовка
    rest = clean[len(title):].strip()
    # Разбиваем на абзацы по двойному переводу строки
    paragraphs = [p.strip() for p in rest.split('\n\n') if p.strip()]
    
    first_para = ""
    if paragraphs:
        first_para = paragraphs[0]
        # Ограничим до 1000 символов для FB
        if len(first_para) > 1000:
            first_para = first_para[:997] + "..."
    
    if first_para:
        return f"{title}\n\n{first_para}\n\nПодробнее: {link}"
    else:
        return f"{title}\n\nПодробнее: {link}"

def upload_to_public_host(telegram_file_url):
    try:
        logger.info(f"Downloading TG file: {telegram_file_url[:80]}...")
        img_data = requests.get(telegram_file_url, timeout=20).content
        if len(img_data) < 1000:
            return None
        try:
            r = requests.post("https://catbox.moe/user/api.php", data={"reqtype": "fileupload"}, files={"fileToUpload": ("image.jpg", img_data, "image/jpeg")}, timeout=20)
            if r.status_code == 200 and r.text.startswith("https://"):
                public_url = r.text.strip()
                logger.info(f"Uploaded to catbox: {public_url}")
                return public_url
        except Exception as e:
            logger.error(f"Catbox error: {e}")
        try:
            r = requests.post("https://0x0.st", files={"file": ("image.jpg", img_data, "image/jpeg")}, timeout=20)
            if r.status_code == 200 and r.text.startswith("https://"):
                public_url = r.text.strip()
                logger.info(f"Uploaded to 0x0.st: {public_url}")
                return public_url
        except Exception as e:
            logger.error(f"0x0 error: {e}")
        return None
    except Exception as e:
        logger.error(f"upload error: {e}")
        return None

def post_to_facebook(text, telegram_image_url=None, public_image_url=None):
    try:
        img_url = public_image_url or telegram_image_url
        if img_url:
            url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/photos"
            data = {"caption": text, "url": img_url, "access_token": FB_PAGE_TOKEN}
        else:
            url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/feed"
            data = {"message": text, "access_token": FB_PAGE_TOKEN}
        r = requests.post(url, data=data, timeout=30)
        logger.info(f"FB OK: {r.status_code} {r.text[:500]}")
        return r.json()
    except Exception as e:
        logger.error(f"FB error: {e}")

def post_to_threads(text, telegram_image_url=None, public_image_url=None):
    try:
        create_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
        short_text = text[:480] if len(text) > 480 else text
        img_to_use = public_image_url
        if img_to_use:
            payload = {"media_type": "IMAGE", "text": short_text, "image_url": img_to_use, "access_token": THREADS_TOKEN}
            r = requests.post(create_url, data=payload, timeout=30)
            logger.info(f"Threads container (IMAGE): {r.text[:500]}")
            data = r.json()
            container_id = data.get("id")
            if container_id:
                time.sleep(4)
                publish_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
                r2 = requests.post(publish_url, data={"creation_id": container_id, "access_token": THREADS_TOKEN}, timeout=30)
                logger.info(f"Threads publish (IMAGE): {r2.text[:500]}")
                if r2.status_code == 200 and r2.json().get("id"):
                    return r2.json()
        payload = {"media_type": "TEXT", "text": short_text, "access_token": THREADS_TOKEN}
        r = requests.post(create_url, data=payload, timeout=30)
        logger.info(f"Threads container (TEXT): {r.text[:500]}")
        container_id = r.json().get("id")
        if not container_id:
            return
        time.sleep(2)
        publish_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
        r2 = requests.post(publish_url, data={"creation_id": container_id, "access_token": THREADS_TOKEN}, timeout=30)
        logger.info(f"Threads publish (TEXT): {r2.text[:500]}")
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
    message_id = post.message_id
    fb_text = format_text_facebook(raw_text, message_id)
    threads_text = format_text_threads(raw_text, message_id)

    telegram_image_url = None
    public_image_url = None
    if post.photo:
        file = await post.photo[-1].get_file()
        telegram_image_url = file.file_path
        public_image_url = upload_to_public_host(telegram_image_url)
    elif post.video:
        try:
            file = await post.video.get_file()
            telegram_image_url = file.file_path
        except:
            pass

    logger.info(f"Новый пост {message_id}: {raw_text[:80]}... tg_img={bool(telegram_image_url)} public={public_image_url}")
    logger.info(f"FB text: {fb_text[:200]}...")
    logger.info(f"Threads text: {threads_text[:200]}...")

    post_to_facebook(fb_text, telegram_image_url, public_image_url)
    post_to_threads(threads_text, telegram_image_url, public_image_url)

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Нет TELEGRAM_BOT_TOKEN!")
        return
    try:
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=10)
        logger.info("Webhook удален")
    except:
        pass
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))
    logger.info(f"Бот запущен {SOURCE_CHANNEL} -> FB:{FB_PAGE_ID} + Threads:{THREADS_USER_ID} | Формат: FB=Заголовок+Абзац+Картинка+Ссылка / Threads=Заголовок+Картинка+Ссылка")
    app.run_polling(allowed_updates=["channel_post"], poll_interval=10.0, timeout=30, drop_pending_updates=True, close_loop=False)

if __name__ == "__main__":
    main()
