# ============================================
#  bot.py - Vibe Instagram Downloader (Final)
# ============================================

import asyncio
import os
import shutil
import sys
import uuid
import traceback
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadError

from botapi import VibeBot
from telethon import events


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


def download_sync(url, out_dir, token):
    outtmpl = str(out_dir / f"%(title).80B_{token}.%(ext)s")
    opts = ydl_options({
        "format": "best",
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


def get_duration_sync(file_path):
    """Extract exact video duration using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as exc:
        print(f"[bot] ffprobe error: {exc}")
    return 0.0


async def main():
    token = os.getenv("VIBE_BOT_TOKEN", "").strip()
    if not token:
        print("[bot] error: VIBE_BOT_TOKEN is not set")
        sys.exit(1)

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    bot = VibeBot(token=token)
    print("[bot] connecting...")
    
    try:
        connected = await bot.connect()
    except Exception as exc:
        print(f"[bot] error: connect failed: {exc}")
        traceback.print_exc()
        sys.exit(1)
        
    if not connected:
        print("[bot] error: connect returned False")
        sys.exit(1)

    me = await bot.get_me()
    bot_username = getattr(me, "username", "bot")
    print(f"[bot] connected as @{bot_username}")

    try:
        await bot.send_message("me", "Bot started and connected successfully.")
    except Exception:
        pass

    @bot.raw.on(events.NewMessage(incoming=True))
    async def handler(event):
        text = (event.raw_text or "").strip()
        print(f"[bot] received message: {text[:50]}")
        
        if text == "/ping":
            await event.reply("pong")
            return
            
        if not is_instagram_url(text):
            return

        status_msg = await event.reply("⏳")
        token = uuid.uuid4().hex[:10]
        
        file_path = None
        try:
            print(f"[bot] downloading: {text}")
            info = await asyncio.to_thread(download_sync, text, DOWNLOAD_DIR, token)
            file_path = find_output_file(info, token)
            
            if not file_path:
                await status_msg.edit("Error: File not found.")
                return
            
            # Read duration from metadata or fallback to ffprobe
            duration = info.get("duration", 0)
            if not duration or duration <= 0:
                duration = await asyncio.to_thread(get_duration_sync, file_path)

            minutes = int(duration // 60)
            seconds = int(duration % 60)
            time_str = f"{minutes:02d}:{seconds:02d}"

            height = info.get("height", 0) or 1080
            caption = f"Quality: {height}p\ntime: {time_str}\n@{bot_username}"
            
            await status_msg.delete()
            print(f"[bot] uploading: {file_path} | time: {time_str}")
            await bot.send_file(event.chat_id, str(file_path), caption=caption)
            print("[bot] upload complete")
            
        except Exception as exc:
            print(f"[bot] error in handler: {exc}")
            traceback.print_exc()
            try:
                await status_msg.edit(f"Error: {str(exc)[:100]}")
            except:
                pass
        finally:
            if file_path and file_path.exists():
                try:
                    file_path.unlink()
                except OSError:
                    pass

    print("[bot] listening for messages")
    try:
        await asyncio.wait_for(bot.run_until_disconnected(), timeout=RUN_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        print("[bot] time limit reached")
    except KeyboardInterrupt:
        print("[bot] stopped")
    finally:
        await bot.disconnect()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
