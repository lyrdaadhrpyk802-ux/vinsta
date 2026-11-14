# ============================================
#  bot.py - Vibe Instagram Downloader Bot
# ============================================

import asyncio
import json
import os
import shutil
import sys
import traceback
import uuid
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadError

from botapi import VibeBot
from botapi.keyboards import ReplyKeyboard
from telethon import events


# ============================================
#  Configuration
# ============================================
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
STATE_FILE = BASE_DIR / "pending.json"
COOKIE_FILE = BASE_DIR / "cookies.txt"
MAX_FILE_SIZE_MB = 2000
RUN_TIMEOUT_SECONDS = 20700

INSTAGRAM_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
    "instagr.am",
    "www.instagr.am",
}

QUALITY_FORMATS = {
    "360p": "best[height<=360]/best",
    "720p": "best[height<=720]/best",
    "1080p": "best[height<=1080]/best",
    "best": "best",
    "mp3": "bestaudio/best",
}


# ============================================
#  Helpers
# ============================================
def load_token():
    return os.getenv("VIBE_BOT_TOKEN", "").strip()


def is_instagram_url(url):
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return (parsed.hostname or "").lower() in INSTAGRAM_HOSTS


def normalize_url(text):
    text = text.strip()
    if text.startswith("www."):
        text = "https://" + text
    if not text.startswith(("http://", "https://")):
        return ""
    return text


def format_size(num_bytes):
    if not num_bytes or num_bytes <= 0:
        return "unknown"
    units = ["B", "KB", "MB", "GB"]
    value = float(num_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return "unknown"


def build_quality_keyboard(qualities):
    kb = ReplyKeyboard(resize=True)
    for i in range(0, len(qualities), 2):
        for q in qualities[i:i + 2]:
            kb.add(q)
        kb.row()
    kb.add("cancel")
    kb.row()
    return kb.build()


def empty_keyboard():
    return ReplyKeyboard(resize=True).build()


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
def extract_sync(url):
    opts = ydl_options({"skip_download": True, "verbose": True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise DownloadError("No metadata returned")
    if "entries" in info:
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise DownloadError("Empty entry list")
        info = entries[0]
    return info


def download_sync(url, fmt, out_dir, token):
    outtmpl = str(out_dir / f"%(title).80B_{token}.%(ext)s")
    opts = ydl_options({
        "format": fmt,
        "outtmpl": outtmpl,
        "overwrites": True,
        "noprogress": True,
    })
    if fmt == "mp3":
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        opts["merge_output_format"] = "mp4"
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


def available_qualities(info):
    heights = set()
    has_video = False
    for fmt in info.get("formats") or []:
        if fmt.get("vcodec") in (None, "none"):
            continue
        has_video = True
        h = fmt.get("height")
        if h:
            heights.add(int(h))
    if info.get("height"):
        heights.add(int(info["height"]))
        has_video = True

    result = []
    for q in ("360p", "720p", "1080p"):
        max_h = int(q.rstrip("p"))
        if any(max_h - 100 <= h <= max_h for h in heights):
            result.append(q)
    if not result and has_video:
        result.append("best")
    result.append("mp3")
    return result


# ============================================
#  State
# ============================================
def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state):
    try:
        STATE_FILE.write_text(json.dumps(state))
    except Exception:
        pass


PENDING = load_state()
bot = None


# ============================================
#  Handlers
# ============================================
async def on_start(event):
    text = (
        "Instagram Downloader Bot\n\n"
        "Send an Instagram post, reel or TV link and choose the quality.\n\n"
        "Commands:\n"
        "/start - show this message\n"
        "/help - help\n"
        "/cancel - cancel pending operation"
    )
    await event.reply(text)


async def on_help(event):
    text = (
        "How to use:\n"
        "1. Copy an Instagram link (post, reel or tv).\n"
        "2. Send it to the bot.\n"
        "3. Choose a quality from the keyboard.\n"
        "4. Wait for the file.\n\n"
        "Supported hosts: instagram.com, instagr.am\n"
        "Private accounts cannot be downloaded."
    )
    await event.reply(text)


async def on_cancel(event):
    uid = event.sender_id
    PENDING.pop(uid, None)
    save_state(PENDING)
    await event.reply("Cancelled.", buttons=empty_keyboard())


async def process_url(event, url):
    uid = event.sender_id
    await event.reply("Fetching info...")
    try:
        info = await asyncio.to_thread(extract_sync, url)
    except DownloadError as exc:
        print("[bot] DownloadError:", file=sys.stderr)
        traceback.print_exc()
        await event.reply(f"Failed to fetch info: {exc}")
        return
    except Exception as exc:
        print("[bot] exception in extract:", file=sys.stderr)
        traceback.print_exc()
        msg = str(exc)
        low = msg.lower()
        if "i/o error" in low or "unreachable" in low or "reset" in low or "connection" in low:
            msg = f"Network error (Instagram likely blocks the runner IP): {msg}"
        await event.reply(f"Error: {msg}")
        return

    title = str(info.get("title") or "Instagram media")
    duration = info.get("duration")
    qualities = available_qualities(info)
    if not qualities:
        await event.reply("No downloadable format found.")
        return

    PENDING[uid] = {"url": url, "title": title, "qualities": qualities}
    save_state(PENDING)

    duration_text = ""
    if duration:
        minutes, seconds = divmod(int(duration), 60)
        duration_text = f"\nDuration: {minutes:02d}:{seconds:02d}"

    text = f"Title: {title[:200]}{duration_text}\n\nChoose quality:"
    await event.reply(text, buttons=build_quality_keyboard(qualities))


async def process_quality(event, quality):
    uid = event.sender_id
    session = PENDING.get(uid)
    if not session:
        await event.reply("No active download. Send an Instagram link first.")
        return
    if quality not in session["qualities"]:
        await event.reply("Invalid option. Choose from the keyboard.")
        return
    if quality == "mp3" and not shutil.which("ffmpeg"):
        await event.reply("MP3 conversion requires ffmpeg on the server.")
        return

    url = session["url"]
    title = session["title"]
    PENDING.pop(uid, None)
    save_state(PENDING)

    fmt = QUALITY_FORMATS.get(quality, "best")
    token = uuid.uuid4().hex[:10]
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    await event.reply(f"Downloading ({quality})...")

    file_path = None
    try:
        info = await asyncio.to_thread(download_sync, url, fmt, DOWNLOAD_DIR, token)
        file_path = find_output_file(info, token)
        if not file_path or not file_path.exists():
            await event.reply("Downloaded file not found on disk.")
            return

        size = file_path.stat().st_size
        if size > MAX_FILE_SIZE_MB * 1024 * 1024:
            await event.reply(f"File too large ({format_size(size)}).")
            return

        await event.reply(f"Uploading ({format_size(size)})...")
        caption = f"{title[:900]}\n{quality} | {format_size(size)}"
        await bot.send_file(event.chat_id, str(file_path), caption=caption)
        await event.reply("Done.", buttons=empty_keyboard())
    except DownloadError as exc:
        await event.reply(f"Download failed: {exc}")
    except Exception as exc:
        await event.reply(f"Error: {exc}")
    finally:
        if file_path and file_path.exists():
            try:
                file_path.unlink()
            except OSError:
                pass


async def on_message(event):
    text = (event.raw_text or "").strip()
    if not text or text.startswith("/"):
        return

    uid = event.sender_id
    session = PENDING.get(uid)

    if session:
        if text in session["qualities"]:
            await process_quality(event, text)
            return
        if text.lower() == "cancel":
            PENDING.pop(uid, None)
            save_state(PENDING)
            await event.reply("Cancelled.", buttons=empty_keyboard())
            return
        if not normalize_url(text):
            await event.reply("Choose a quality or send a new Instagram link.")
            return

    url = normalize_url(text)
    if not is_instagram_url(url):
        await event.reply("Only Instagram links are supported.")
        return
    await process_url(event, url)


# ============================================
#  Main
# ============================================
async def main():
    global bot
    token = load_token()
    if not token:
        print("[bot] error: VIBE_BOT_TOKEN is not set")
        sys.exit(1)

    if not shutil.which("ffmpeg"):
        print("[bot] warning: ffmpeg not found, MP3 will fail")

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
    print(f"[bot] connected as @{getattr(me, 'username', '-')}")

    bot.raw.add_event_handler(
        on_start,
        events.NewMessage(incoming=True, pattern=r"^/start(?:@\w+)?(?:\s|$)"),
    )
    bot.raw.add_event_handler(
        on_help,
        events.NewMessage(incoming=True, pattern=r"^/help(?:@\w+)?(?:\s|$)"),
    )
    bot.raw.add_event_handler(
        on_cancel,
        events.NewMessage(incoming=True, pattern=r"^/cancel(?:@\w+)?(?:\s|$)"),
    )
    bot.raw.add_event_handler(on_message, events.NewMessage(incoming=True))

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
