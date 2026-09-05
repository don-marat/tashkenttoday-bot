import os
import logging
import requests
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

def format_text(text, message_id):
    link = get_post_link(message_id)
    # Только первая строка = заголовок
    title = text.strip().split('\n')[0].strip()
    # Убираем эмодзи в конце заголовка если есть, оставляем чистый заголовок
    return f"{title}\n\n🔗 {link}"

def post_to_facebook(text, image_url=None):
    try:
        if image_url:
            url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/photos"
            data = {"caption": text, "url": image_url, "access_token": FB_PAGE_TOKEN}
        else:
            url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/feed"
            data = {"message": text, "access_token": FB_PAGE_TOKEN}
        r = requests.post(url, data=data, timeout=25)
        logger.info(f"FB OK: {r.status_code} {r.text[:300]}")
        return r.json()
    except Exception as e:
        logger.error(f"FB error: {e}")

def post_to_threads(text, image_url=None):
    try:
        create_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
        short_text = text
        if len(short_text) > 480:
            short_text = short_text[:470] + "..."
        payload = {"media_type": "TEXT" if not image_url else "IMAGE", "text": short_text, "access_token": THREADS_TOKEN}
        if image_url:
            payload["image_url"] = image_url
        r = requests.post(create_url, data=payload, timeout=25)
        data = r.json()
        logger.info(f"Threads container: {r.text[:500]}")
        container_id = data.get("id")
        if not container_id:
            return
        publish_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
        r2 = requests.post(publish_url, data={"creation_id": container_id, "access_token": THREADS_TOKEN}, timeout=25)
        logger.info(f"Threads publish: {r2.text[:500]}")
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
    final_text = format_text(raw_text, message_id)
    image_url = None
    if post.photo:
        file = await post.photo[-1].get_file()
        image_url = file.file_path
    elif post.video:
        try:
            file = await post.video.get_file()
            image_url = file.file_path
        except:
            pass
    logger.info(f"Новый пост {message_id}: {raw_text[:80]}... img={bool(image_url)}")
    post_to_facebook(final_text, image_url)
    post_to_threads(final_text, image_url)

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Нет TELEGRAM_BOT_TOKEN!")
        return
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))
    logger.info(f"Бот запущен {SOURCE_CHANNEL} -> FB:{FB_PAGE_ID} + Threads:{THREADS_USER_ID} | Формат: Заголовок+Картинка+Ссылка | Интервал 10 мин")
    # Проверка каждые 10 минут = 600 секунд
    app.run_polling(allowed_updates=["channel_post"], poll_interval=600.0, timeout=30)

if __name__ == "__main__":
    main()
