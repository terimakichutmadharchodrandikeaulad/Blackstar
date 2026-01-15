#!/usr/bin/env python3
# main.py — Pyrogram v2.x + PyTgCalls compatible single-file music bot
# Compatibility wrappers included: supports both join_group_call(stream=...) and play(...)

import os
import sys
import asyncio
import time
import random
import traceback
from typing import Dict, List, Optional
from datetime import datetime
from collections import defaultdict

# Pyrogram
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import RPCError
from pyrogram.enums import ChatMemberStatus

# PyTgCalls (import AudioPiped from stable location)
# Note: some docs/examples use `from pytgcalls.types import AudioPiped` and join_group_call(..., stream=AudioPiped(...))
# See upstream examples/docs. 2
try:
    from pytgcalls import PyTgCalls
    from pytgcalls.types import AudioPiped  # stable import path in examples
except Exception as e:
    # fail early with helpful message
    raise RuntimeError("pytgcalls import failed. Ensure py-tgcalls is installed (pip install py-tgcalls).") from e

# Optional high-quality wrapper (if available)
try:
    from pytgcalls.types import HighQualityAudio
except Exception:
    HighQualityAudio = None  # fallback if not present

# Other utils
import yt_dlp
import psutil

# -------------------------
# Config
# -------------------------
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ASSISTANT_SESSION = os.getenv("ASSISTANT_SESSION", "")
LOG_CHANNEL = os.getenv("LOG_CHANNEL", "")
SUDO_USERS = [int(x) for x in os.getenv("SUDO_USERS", "").split(",") if x.strip().isdigit()]

START_TIME = datetime.now()
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# -------------------------
# Clients
# -------------------------
bot = Client("MusicBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
assistant = Client("Assistant", api_id=API_ID, api_hash=API_HASH, session_string=ASSISTANT_SESSION, in_memory=True)

# attach PyTgCalls to assistant user client
calls = PyTgCalls(assistant)

# -------------------------
# State
# -------------------------
queues: Dict[int, List[Dict]] = defaultdict(list)
current_playing: Dict[int, Optional[Dict]] = {}
loop_status: Dict[int, bool] = defaultdict(bool)
command_cooldown: Dict[int, float] = {}

# -------------------------
# yt-dlp config
# -------------------------
def get_yt_config():
    return {
        'format': 'bestaudio/best',
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s.%(ext)s',
        'geo_bypass': True,
        'nocheckcertificate': True,
        'quiet': True,
        'no_warnings': True,
        'prefer_ffmpeg': True,
        'extract_flat': False,
        'keepvideo': False,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }

# -------------------------
# Helpers: search + download
# -------------------------
async def search_youtube(query: str) -> Optional[Dict]:
    try:
        ydl_opts = {'format': 'bestaudio', 'quiet': True, 'no_warnings': True, 'skip_download': True}
        if query.startswith("http"):
            search_query = query
        else:
            search_query = f"ytsearch1:{query}"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_query, download=False)
            if not info:
                return None
            if 'entries' in info:
                info = info['entries'][0]
            return {
                'title': info.get('title', 'Unknown'),
                'url': info.get('webpage_url', info.get('url', '')),
                'duration': int(info.get('duration') or 0),
                'thumbnail': info.get('thumbnail', ''),
                'id': info.get('id', ''),
            }
    except Exception as e:
        print("YouTube search error:", e)
        traceback.print_exc()
        return None

async def download_audio(url: str) -> Optional[str]:
    try:
        ydl_opts = get_yt_config()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_id = info.get('id', '')
            candidates = [
                f"{DOWNLOAD_DIR}/{video_id}.mp3",
                f"{DOWNLOAD_DIR}/{video_id}.m4a",
                f"{DOWNLOAD_DIR}/{video_id}.webm",
                f"{DOWNLOAD_DIR}/{video_id}.opus",
            ]
            for p in candidates:
                if os.path.exists(p):
                    return p
            return None
    except Exception as e:
        print("Download error:", e)
        traceback.print_exc()
        return None

async def cleanup_files():
    try:
        for fn in os.listdir(DOWNLOAD_DIR):
            p = os.path.join(DOWNLOAD_DIR, fn)
            if os.path.isfile(p) and (time.time() - os.path.getmtime(p)) > 3600:
                try:
                    os.remove(p)
                except:
                    pass
    except Exception:
        traceback.print_exc()

# -------------------------
# Formatting / UI
# -------------------------
def format_duration(seconds: int) -> str:
    if seconds < 3600:
        return time.strftime("%M:%S", time.gmtime(seconds))
    return time.strftime("%H:%M:%S", time.gmtime(seconds))

def get_queue_text(chat_id: int) -> str:
    q = queues.get(chat_id, [])
    if not q:
        return "📭 **Queue is empty**"
    txt = "📋 **Current Queue:**\n\n"
    for i, s in enumerate(q[:10], 1):
        txt += f"`{i}.` **{s['title']}** - `{format_duration(s['duration'])}`\n"
    if len(q) > 10:
        txt += f"\n*...and {len(q)-10} more*"
    return txt

def get_player_buttons(chat_id: int):
    playing = current_playing.get(chat_id)
    is_playing = bool(playing)
    buttons = [
        [
            InlineKeyboardButton("⏸ Pause" if is_playing else "▶️ Resume",
                                 callback_data=f"{'pause' if is_playing else 'resume'}_{chat_id}"),
            InlineKeyboardButton("⏭ Skip", callback_data=f"skip_{chat_id}"),
            InlineKeyboardButton("⏹ Stop", callback_data=f"stop_{chat_id}")
        ],
        [
            InlineKeyboardButton("🔁 Loop", callback_data=f"loop_{chat_id}"),
            InlineKeyboardButton("🔀 Shuffle", callback_data=f"shuffle_{chat_id}"),
            InlineKeyboardButton("📋 Queue", callback_data=f"queue_{chat_id}")
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{chat_id}"),
            InlineKeyboardButton("❌ Close", callback_data=f"close_{chat_id}")
        ]
    ]
    return InlineKeyboardMarkup(buttons)

# -------------------------
# Permissions
# -------------------------
async def is_admin(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        m = await client.get_chat_member(chat_id, user_id)
        return m.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    except Exception:
        return False

# -------------------------
# Compatibility wrappers for play/join/pause/resume
# -------------------------
async def _start_stream(chat_id: int, file_path: str):
    """
    Start playback using the best available API:
    - prefer join_group_call(chat_id, stream=AudioPiped(...))
    - fallback to play(chat_id, file_path)
    """
    # build audio object if available
    try:
        audio_obj = AudioPiped(file_path)
        if HighQualityAudio is not None:
            # if library provides HQ wrapper, use it
            try:
                audio_obj = AudioPiped(file_path, HighQualityAudio())
            except Exception:
                audio_obj = AudioPiped(file_path)
    except Exception:
        audio_obj = None

    # prefer join_group_call(stream=...)
    if hasattr(calls, "join_group_call"):
        if audio_obj is not None:
            await calls.join_group_call(chat_id, stream=audio_obj)
            return
        else:
            # fallback: try raw join/play
            await calls.join_group_call(chat_id, stream=AudioPiped(file_path))
            return
    # older/newer API: play(chat_id, path_or_url)
    if hasattr(calls, "play"):
        # some implementations accept a URL/path string
        await calls.play(chat_id, file_path)
        return

    # fallback: raise
    raise RuntimeError("No compatible playback method found in PyTgCalls (checked join_group_call/play).")

async def _pause_stream(chat_id: int):
    if hasattr(calls, "pause_stream"):
        await calls.pause_stream(chat_id)
    elif hasattr(calls, "pause"):
        await calls.pause(chat_id)
    else:
        raise RuntimeError("No pause method found on PyTgCalls.")

async def _resume_stream(chat_id: int):
    if hasattr(calls, "resume_stream"):
        await calls.resume_stream(chat_id)
    elif hasattr(calls, "resume"):
        await calls.resume(chat_id)
    else:
        raise RuntimeError("No resume method found on PyTgCalls.")

async def _leave_call(chat_id: int):
    if hasattr(calls, "leave_call"):
        await calls.leave_call(chat_id)
    elif hasattr(calls, "stop"):
        try:
            await calls.stop(chat_id)
        except TypeError:
            # some versions accept no args
            await calls.stop()
    elif hasattr(calls, "leave"):
        await calls.leave(chat_id)
    else:
        # best-effort: ignore
        pass

# -------------------------
# Voice helpers
# -------------------------
async def join_voice_chat(chat_id: int):
    """
    Ensure assistant is present in the voice chat.
    Implementation uses a lightweight join by playing a small remote stream (will create/join group call).
    """
    try:
        # ensure assistant can access chat (no-op if already accessible)
        try:
            await assistant.get_chat(chat_id)
        except Exception:
            pass

        # use hosted example to join quickly (will be replaced by real stream later)
        url = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"
        if hasattr(calls, "join_group_call"):
            await calls.join_group_call(chat_id, stream=AudioPiped(url))
            await asyncio.sleep(0.6)
            return
        if hasattr(calls, "play"):
            await calls.play(chat_id, url)
            await asyncio.sleep(0.6)
            return
    except Exception as e:
        print("Join VC error:", e)
        raise

# -------------------------
# Playback flow
# -------------------------
async def play_next(chat_id: int):
    try:
        if loop_status.get(chat_id) and current_playing.get(chat_id):
            song = current_playing[chat_id]
        elif queues.get(chat_id):
            song = queues[chat_id].pop(0)
        else:
            current_playing.pop(chat_id, None)
            try:
                await _leave_call(chat_id)
            except:
                pass
            try:
                await bot.send_message(chat_id, "✅ **Queue finished! Leaving voice chat.**")
            except:
                pass
            return

        file_path = await download_audio(song['url'])
        if not file_path:
            try:
                await bot.send_message(chat_id, f"❌ **Failed to download:** {song.get('title')}")
            except:
                pass
            await play_next(chat_id)
            return

        # start stream using compatibility wrapper
        try:
            await _start_stream(chat_id, file_path)
        except Exception as e:
            print("Playback start error:", e)
            traceback.print_exc()
            try:
                await bot.send_message(chat_id, f"❌ **Playback error:** {str(e)}")
            except:
                pass
            await play_next(chat_id)
            return

        current_playing[chat_id] = song
        text = f"🎵 **Now Playing:**\n\n**{song['title']}**\n⏱ Duration: `{format_duration(song['duration'])}`"
        if loop_status.get(chat_id):
            text += "\n🔁 **Loop:** Enabled"
        try:
            await bot.send_message(chat_id, text, reply_markup=get_player_buttons(chat_id))
        except:
            pass

    except Exception:
        traceback.print_exc()

# -------------------------
# Stream-end handler (uses whatever event decorator pytgcalls provides)
# -------------------------
# Many pytgcalls examples use @calls.on_stream_end() and update.chat_id — keep that.
@calls.on_stream_end()
async def _on_stream_end(_, update):
    try:
        chat_id = getattr(update, "chat_id", None) or getattr(update, "group_call_id", None)
        if chat_id is None:
            return
        await asyncio.sleep(0.2)
        await play_next(chat_id)
    except Exception:
        traceback.print_exc()

# -------------------------
# Bot handlers (commands + callbacks)
# -------------------------
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    me = await client.get_me()
    text = (
        "👋 **Welcome to Advanced Music Bot!**\n\n"
        "I can play music in your group voice chats.\n\n"
        "Commands:\n"
        "/play <song/url>\n/pause\n/resume\n/skip\n/stop\n/queue\n/nowplaying\n/volume\n/loop\n/shuffle\n/help\n"
    )
    buttons = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{me.username}?startgroup=true")]])
    await message.reply_text(text, reply_markup=buttons)

@bot.on_message(filters.command("help"))
async def help_command(client: Client, message: Message):
    text = "📚 Help: /play <song/url> | /pause | /resume | /skip | /stop | /queue | /nowplaying"
    await message.reply_text(text)

@bot.on_message(filters.command("play") & filters.group)
async def play_command(client: Client, message: Message):
    chat_id = message.chat.id
    if chat_id in command_cooldown and (time.time() - command_cooldown[chat_id]) < 2:
        return
    command_cooldown[chat_id] = time.time()

    if len(message.command) < 2:
        await message.reply_text("❌ Usage: /play <song name or URL>")
        return

    query = message.text.split(None, 1)[1]
    msg = await message.reply_text("🔍 Searching...")

    try:
        info = await search_youtube(query)
        if not info:
            await msg.edit("❌ No results found.")
            return

        song = {'title': info['title'], 'url': info['url'], 'duration': info['duration'], 'requested_by': message.from_user.mention}

        try:
            await join_voice_chat(chat_id)
        except Exception as e:
            await msg.edit(f"❌ Failed to join VC: {str(e)}")
            return

        if current_playing.get(chat_id):
            queues[chat_id].append(song)
            await msg.edit(f"✅ Added to queue at position #{len(queues[chat_id])}\n**{song['title']}**")
        else:
            queues[chat_id].append(song)
            await msg.edit("⏳ Loading...")
            await play_next(chat_id)
            try:
                await msg.delete()
            except:
                pass

    except Exception as e:
        await msg.edit(f"❌ Error: {str(e)}")
        traceback.print_exc()

@bot.on_message(filters.command("pause") & filters.group)
async def pause_command(client: Client, message: Message):
    chat_id = message.chat.id
    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply_text("❌ Admin only.")
        return
    try:
        await _pause_stream(chat_id)
        await message.reply_text("⏸ Paused.")
    except Exception as e:
        await message.reply_text(f"❌ Pause failed: {str(e)}")

@bot.on_message(filters.command("resume") & filters.group)
async def resume_command(client: Client, message: Message):
    chat_id = message.chat.id
    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply_text("❌ Admin only.")
        return
    try:
        await _resume_stream(chat_id)
        await message.reply_text("▶️ Resumed.")
    except Exception as e:
        await message.reply_text(f"❌ Resume failed: {str(e)}")

@bot.on_message(filters.command("skip") & filters.group)
async def skip_command(client: Client, message: Message):
    chat_id = message.chat.id
    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply_text("❌ Admin only.")
        return
    if not current_playing.get(chat_id):
        await message.reply_text("❌ Nothing is playing.")
        return
    await message.reply_text("⏭ Skipped.")
    await play_next(chat_id)

@bot.on_message(filters.command("stop") & filters.group)
async def stop_command(client: Client, message: Message):
    chat_id = message.chat.id
    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply_text("❌ Admin only.")
        return
    queues[chat_id].clear()
    current_playing.pop(chat_id, None)
    loop_status[chat_id] = False
    await _leave_call(chat_id)
    await message.reply_text("⏹ Stopped and cleared queue.")

@bot.on_message(filters.command("queue") & filters.group)
async def queue_command(client: Client, message: Message):
    chat_id = message.chat.id
    txt = get_queue_text(chat_id)
    if current_playing.get(chat_id):
        txt = f"🎵 Now Playing: **{current_playing[chat_id]['title']}**\n\n{txt}"
    await message.reply_text(txt)

@bot.on_message(filters.command("nowplaying") & filters.group)
async def nowplaying_command(client: Client, message: Message):
    chat_id = message.chat.id
    if not current_playing.get(chat_id):
        await message.reply_text("❌ Nothing is playing.")
        return
    s = current_playing[chat_id]
    txt = f"🎵 Now Playing:\n**{s['title']}**\n⏱ `{format_duration(s['duration'])}`\n👤 {s['requested_by']}"
    if loop_status.get(chat_id):
        txt += "\n🔁 Loop enabled"
    await message.reply_text(txt, reply_markup=get_player_buttons(chat_id))

@bot.on_message(filters.command("loop") & filters.group)
async def loop_command(client: Client, message: Message):
    chat_id = message.chat.id
    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply_text("❌ Admin only.")
        return
    loop_status[chat_id] = not loop_status.get(chat_id, False)
    await message.reply_text(f"🔁 Loop {'enabled' if loop_status[chat_id] else 'disabled'}")

@bot.on_message(filters.command("shuffle") & filters.group)
async def shuffle_command(client: Client, message: Message):
    chat_id = message.chat.id
    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply_text("❌ Admin only.")
        return
    if not queues.get(chat_id):
        await message.reply_text("❌ Queue empty.")
        return
    random.shuffle(queues[chat_id])
    await message.reply_text("🔀 Queue shuffled.")

@bot.on_message(filters.command("ping"))
async def ping_command(client: Client, message: Message):
    start = time.time()
    m = await message.reply_text("🏓 Pinging...")
    elapsed = (time.time() - start) * 1000
    uptime = datetime.now() - START_TIME
    txt = f"🏓 Pong!\nLatency: `{elapsed:.2f} ms`\nUptime: `{str(uptime).split('.')[0]}`"
    await m.edit(txt)

@bot.on_callback_query()
async def callback_handler(client: Client, callback: CallbackQuery):
    data = callback.data or ""
    if not data:
        return
    if data.startswith("close_"):
        try:
            await callback.message.delete()
        except:
            pass
        return
    try:
        action, rest = data.split("_", 1)
        chat_id = int(rest)
    except:
        await callback.answer("Invalid", show_alert=True)
        return
    if not await is_admin(client, chat_id, callback.from_user.id):
        await callback.answer("❌ Admin only", show_alert=True)
        return
    try:
        if action == "pause":
            await _pause_stream(chat_id)
            await callback.answer("⏸ Paused")
        elif action == "resume":
            await _resume_stream(chat_id)
            await callback.answer("▶️ Resumed")
        elif action == "skip":
            await callback.answer("⏭ Skipped")
            await play_next(chat_id)
        elif action == "stop":
            queues[chat_id].clear()
            current_playing.pop(chat_id, None)
            loop_status[chat_id] = False
            await _leave_call(chat_id)
            await callback.answer("⏹ Stopped")
            try:
                await callback.message.delete()
            except:
                pass
        elif action == "loop":
            loop_status[chat_id] = not loop_status.get(chat_id, False)
            await callback.answer("🔁 Toggled")
        elif action == "shuffle":
            if queues.get(chat_id):
                random.shuffle(queues[chat_id])
                await callback.answer("🔀 Shuffled")
            else:
                await callback.answer("Queue empty", show_alert=True)
        elif action == "queue":
            txt = get_queue_text(chat_id)
            await callback.message.reply_text(txt)
        # update UI
        if current_playing.get(chat_id):
            s = current_playing[chat_id]
            try:
                await callback.message.edit_text(f"🎵 Now Playing:\n**{s['title']}**\n⏱ `{format_duration(s['duration'])}`", reply_markup=get_player_buttons(chat_id))
            except:
                pass
    except Exception as e:
        await callback.answer(f"Error: {e}", show_alert=True)

# -------------------------
# Background tasks
# -------------------------
async def auto_cleanup():
    while True:
        await asyncio.sleep(1800)
        await cleanup_files()

# -------------------------
# Startup / main
# -------------------------
async def main():
    await bot.start()
    await assistant.start()
    # start pytgcalls client
    await calls.start()
    print("✅ Bot started")
    try:
        print("Bot:", (await bot.get_me()).username)
        print("Assistant:", (await assistant.get_me()).username)
    except:
        pass
    # background
    asyncio.create_task(auto_cleanup())
    await idle()
    await bot.stop()
    await assistant.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Exiting...")
