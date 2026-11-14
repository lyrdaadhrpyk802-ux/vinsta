# ============================================
#  bot.py - Vibe Instagram Downloader (Simple)
# ============================================

import asyncio
import os
import shutil
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadError

from botapi import VibeBot
from telethon import events


# ============================================
#  Configuration
# ============================================
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
COOKIE_FILE = BASE_DIR / "cookies.txt"
RUN_TIMEOUT_SECONDS = 20700

INSTAGRAM_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
    "instagr.am",
    "www.instagr.am",
}


def is_instagram_url(url):
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return (parsed.hostname or "").lower() in INSTAGRAM_HOSTS


def proxy_url():
    return os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or None


def ydl_options(extra=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
    }
    if COOKIE_FILE.exists():
        opts["cookiefile"] = str(COOKIE_FILE)
    p = proxy_url()
    if p:
        opts["proxy"] = p
    if extra:
        opts.update(extra)
    return opts


# ============================================
#  yt-dlp wrappers
# ============================================
def download_sync(url, out_dir, token):
    outtmpl = str(out_dir / f"%(title).80B_{token}.%(ext)s")
    opts = ydl_options({
        "format": "bv*+ba/b", # بهترین کیفیت ویدیو + صدا
        "outtmpl": outtmpl,
        "overwrites": True,
        "noprogress": True,
        "merge_output_format": "mp4",
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if info is None:
        raise DownloadError("Download returned no data")
    if "entries" in info:
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise DownloadError("Empty entry list")
        info = entries[0]
    return info


def find_output_file(info, token):
    for item in info.get("requested_downloads") or []:
        path = item.get("filepath") or item.get("filename")
        if path and Path(path).exists():
            return Path(path)
    try:
        candidates = sorted(
            DOWNLOAD_DIR.glob(f"*{token}*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None
    return candidates[0] if candidates else None


# ============================================
#  Handlers
# ============================================
async def on_message(event, bot_username):
    text = (event.raw_text or "").strip()
    if not text or text.startswith("/"):
        return

    if not is_instagram_url(text):
        return

    # ارسال پیام انتظار
    status_msg = await event.reply("⏳")
    
    token = uuid.uuid4().hex[:10]
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    
    file_path = None
    try:
        # دانلود در پس‌زمینه
        info = await asyncio.to_thread(download_sync, text, DOWNLOAD_DIR, token)
        file_path = find_output_file(info, token)
        
        if not file_path or not file_path.exists():
            await status_msg.delete()
            return
        
        # استخراج اطلاعات برای کپشن
        height = info.get("height", 0)
        if not height:
            for fmt in info.get("formats", []):
                if fmt.get("height"):
                    height = max(height, fmt.get("height"))
        quality = f"{height}p" if height else "Original"
        
        duration = info.get("duration", 0)
        minutes = int(duration // 60)
        seconds = int(duration % 60)
        time_str = f"{minutes:02d}:{seconds:02d}"
        
        caption = f"Quality: {quality}\ntime: {time_str}\n@{bot_username}"
        
        # پاک کردن پیام انتظار
        await status_msg.delete()
        
        # ارسال ویدیو
        await bot.send_file(event.chat_id, str(file_path), caption=caption)
        
    except Exception as exc:
        print(f"[bot] error: {exc}", file=sys.stderr)
        try:
            await status_msg.delete()
        except:
            pass
    finally:
        if file_path and file_path.exists():
            try:
                file_path.unlink()
            except OSError:
                pass


# ============================================
#  Main
# ============================================
async def main():
    token = os.getenv("VIBE_BOT_TOKEN", "").strip()
    if not token:
        print("[bot] error: VIBE_BOT_TOKEN is not set")
        sys.exit(1)

    if not shutil.which("ffmpeg"):
        print("[bot] warning: ffmpeg not found")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    bot = VibeBot(token=token)
    try:
        connected = await bot.connect()
    except Exception as exc:
        print(f"[bot] error: connect failed: {exc}")
        sys.exit(1)
    if not connected:
        print("[bot] error: connect returned False")
        sys.exit(1)

    me = await bot.get_me()
    bot_username = getattr(me, "username", "bot")
    print(f"[bot] connected as @{bot_username}")

    # ثبت هندلر با ارسال یوزرنیم به عنوان آرگومان
    async def message_handler(event):
        await on_message(event, bot_username)

    bot.raw.add_event_handler(message_handler, events.NewMessage(incoming=True))

    print("[bot] listening for messages")
    try:
        await asyncio.wait_for(
            bot.run_until_disconnected(),
            timeout=RUN_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        print("[bot] time limit reached, exiting cleanly")
    except KeyboardInterrupt:
        print("[bot] stopped by user")
    finally:
        try:
            await bot.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
