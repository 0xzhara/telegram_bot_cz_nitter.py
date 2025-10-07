#!/usr/bin/env python3
# telegram_bot_cz_nitter.py
import os, asyncio, json, logging, re
import aiohttp, feedparser
from aiohttp import web

# CONFIG (via env vars)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TARGET_USERNAME = os.getenv("TARGET_USERNAME", "cz_binance")
NITTER_INSTANCE = os.getenv("NITTER_INSTANCE", "https://nitter.net")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))  # detik
STATE_FILE = "state_nitter.json"
PORT = int(os.getenv("PORT", os.getenv("RENDER_INTERNAL_PORT", "10000")))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cz-bot")

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def build_rss_url(instance_base, username):
    return instance_base.rstrip('/') + f"/rss/{username}"

async def fetch_rss(session, url):
    try:
        async with session.get(url, timeout=20) as resp:
            content = await resp.read()
            return feedparser.parse(content)
    except Exception as e:
        logger.warning("Gagal fetch RSS %s: %s", url, e)
        return None

def format_entry(entry):
    published = entry.get('published', '')
    link = entry.get('link', '')
    summary = entry.get('summary', '')
    summary_text = re.sub('<[^<]+?>', '', summary)
    text = f"*{TARGET_USERNAME}*\n{published}\n\n{summary_text}\n\n[Open on X]({link})"
    return text

async def send_to_telegram(session, text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_TOKEN atau TELEGRAM_CHAT_ID belum dikonfigurasi.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        async with session.post(url, json=payload, timeout=20) as resp:
            data = await resp.json()
            if not data.get("ok"):
                logger.warning("Telegram API respon bukan OK: %s", data)
                return False
            return True
    except Exception as e:
        logger.exception("Gagal kirim ke Telegram: %s", e)
        return False

async def poll_loop(session):
    state = load_state()
    last_guid = state.get("last_guid")
    sent_guids = state.get("sent_guids", [])
    rss_url = build_rss_url(NITTER_INSTANCE, TARGET_USERNAME)
    logger.info("Polling RSS %s setiap %s detik", rss_url, POLL_INTERVAL)

    while True:
        feed = await fetch_rss(session, rss_url)
        if feed and 'entries' in feed:
            entries = feed['entries']
            to_send = []
            for e in reversed(entries):
                guid = e.get('id') or e.get('link') or e.get('title')
                if not guid:
                    continue
                if last_guid is None or (guid != last_guid and guid not in sent_guids):
                    to_send.append((guid, e))
            if to_send:
                logger.info("Menemukan %d item baru", len(to_send))
                for guid, entry in to_send:
                    text = format_entry(entry)
                    ok = await send_to_telegram(session, text)
                    if ok:
                        last_guid = guid
                        sent_guids.append(guid)
                        sent_guids = sent_guids[-200:]
                        state['last_guid'] = last_guid
                        state['sent_guids'] = sent_guids
                        save_state(state)
        await asyncio.sleep(POLL_INTERVAL)

# Simple health server for Render + Uptime checks
async def start_health_server():
    async def health(request):
        return web.Response(text="OK")
    app = web.Application()
    app.router.add_get('/', health)
    app.router.add_get('/health', health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Health server running on port %s", PORT)
    await asyncio.Event().wait()  # run forever

async def main():
    async with aiohttp.ClientSession() as session:
        poll_task = asyncio.create_task(poll_loop(session))
        web_task = asyncio.create_task(start_health_server())
        await asyncio.gather(poll_task, web_task)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as e:
        logger.exception("Terminated with error: %s", e)

