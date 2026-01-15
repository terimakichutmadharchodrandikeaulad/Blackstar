import os
import sys
import asyncio
import time
import random
import re
from typing import Dict, List, Optional
from datetime import datetime
from collections import defaultdict

from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import (
    UserAlreadyParticipant, ChatAdminRequired, UserNotParticipant,
    FloodWait, ChannelPrivate, PeerIdInvalid
)
from pyrogram.enums import ChatMemberStatus

from pytgcalls import PyTgCalls

from pytgcalls.exceptions import GroupCallNotFound, NotInGroupCallError

import yt_dlp
import psutil


API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ASSISTANT_SESSION = os.getenv("ASSISTANT_SESSION", "")
LOG_CHANNEL = os.getenv("LOG_CHANNEL", "")

START_TIME = datetime.now()
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

bot = Client(
    "MusicBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

assistant = Client(
    "Assistant",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=ASSISTANT_SESSION
)

calls = PyTgCalls(assistant)

queues: Dict[int, List[Dict]] = defaultdict(list)
current_playing: Dict[int, Dict] = {}
loop_status: Dict[int, bool] = defaultdict(bool)
command_cooldown: Dict[int, float] = {}


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


async def search_youtube(query: str) -> Optional[Dict]:
    try:
        ydl_opts = {
            'format': 'bestaudio',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'skip_download': True,
        }

        if query.startswith('http'):
            search_query = query
        else:
            search_query = f"ytsearch1:{query}"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_query, download=False)

            if 'entries' in info:
                info = info['entries'][0]

            return {
                'title': info.get('title', 'Unknown'),
                'url': info.get('webpage_url', info.get('url', '')),
                'duration': info.get('duration', 0),
                'thumbnail': info.get('thumbnail', ''),
                'id': info.get('id', ''),
            }
    except Exception as e:
        print(f"YouTube search error: {e}")
        return None


async def download_audio(url: str) -> Optional[str]:
    try:
        ydl_opts = get_yt_config()

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_id = info.get('id', '')

            possible_files = [
                f"{DOWNLOAD_DIR}/{video_id}.mp3",
                f"{DOWNLOAD_DIR}/{video_id}.m4a",
                f"{DOWNLOAD_DIR}/{video_id}.webm",
                f"{DOWNLOAD_DIR}/{video_id}.opus",
            ]

            for file_path in possible_files:
                if os.path.exists(file_path):
                    return file_path

            return None
    except Exception as e:
        print(f"Download error: {e}")
        return None


async def cleanup_files():
    try:
        for filename in os.listdir(DOWNLOAD_DIR):
            file_path = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.isfile(file_path):
                file_age = time.time() - os.path.getmtime(file_path)
                if file_age > 3600:
                    os.remove(file_path)
    except Exception as e:
        print(f"Cleanup error: {e}")


def format_duration(seconds: int) -> str:
    if seconds < 3600:
        return time.strftime("%M:%S", time.gmtime(seconds))
    return time.strftime("%H:%M:%S", time.gmtime(seconds))


def get_queue_text(chat_id: int) -> str:
    if not queues[chat_id]:
        return "📭 **Queue is empty**"

    text = "📋 **Current Queue:**\n\n"
    for idx, song in enumerate(queues[chat_id][:10], 1):
        text += f"`{idx}.` **{song['title']}** - `{format_duration(song['duration'])}`\n"

    if len(queues[chat_id]) > 10:
        text += f"\n*...and {len(queues[chat_id]) - 10} more*"

    return text


def get_player_buttons(chat_id: int) -> InlineKeyboardMarkup:
    playing = current_playing.get(chat_id)
    is_playing = playing is not None

    buttons = [
        [
            InlineKeyboardButton("⏸ Pause", callback_data=f"pause_{chat_id}") if is_playing else InlineKeyboardButton("▶️ Resume", callback_data=f"resume_{chat_id}"),
            InlineKeyboardButton("⏭ Skip", callback_data=f"skip_{chat_id}"),
            InlineKeyboardButton("⏹ Stop", callback_data=f"stop_{chat_id}"),
        ],
        [
            InlineKeyboardButton("🔁 Loop", callback_data=f"loop_{chat_id}"),
            InlineKeyboardButton("🔀 Shuffle", callback_data=f"shuffle_{chat_id}"),
            InlineKeyboardButton("📋 Queue", callback_data=f"queue_{chat_id}"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{chat_id}"),
            InlineKeyboardButton("❌ Close", callback_data=f"close_{chat_id}"),
        ]
    ]

    return InlineKeyboardMarkup(buttons)


async def is_admin(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]
    except:
        return False


async def join_voice_chat(chat_id: int):
    try:
        await assistant.get_chat(chat_id)
    except:
        pass

    try:
        await calls.get_call(chat_id)
    except (GroupCallNotFound, NotInGroupCallError):
        try:
            await calls.play(
                chat_id,
                AudioPiped("https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3")
            )
            await asyncio.sleep(1)
        except Exception as e:
            print(f"Join VC error: {e}")
            raise


async def leave_voice_chat(chat_id: int):
    try:
        await calls.leave_call(chat_id)
    except:
        pass


async def play_next(chat_id: int):
    if loop_status.get(chat_id) and current_playing.get(chat_id):
        song = current_playing[chat_id]
    elif queues[chat_id]:
        song = queues[chat_id].pop(0)
    else:
        current_playing[chat_id] = None
        await leave_voice_chat(chat_id)
        try:
            await bot.send_message(chat_id, "✅ **Queue finished! Leaving voice chat.**")
        except:
            pass
        return

    try:
        file_path = await download_audio(song['url'])

        if not file_path or not os.path.exists(file_path):
            await bot.send_message(chat_id, f"❌ **Failed to download:** {song['title']}")
            await play_next(chat_id)
            return

        await calls.play(
            chat_id,
            AudioPiped(file_path)
        )

        current_playing[chat_id] = song

        text = f"🎵 **Now Playing:**\n\n**{song['title']}**\n⏱ Duration: `{format_duration(song['duration'])}`"

        if loop_status.get(chat_id):
            text += "\n🔁 **Loop:** Enabled"

        await bot.send_message(
            chat_id,
            text,
            reply_markup=get_player_buttons(chat_id)
        )

    except Exception as e:
        print(f"Play error: {e}")
        await bot.send_message(chat_id, f"❌ **Error playing audio:** {str(e)}")
        await play_next(chat_id)


@calls.on_stream_end()
async def on_stream_end(client: PyTgCalls, update: Update):
    chat_id = update.chat_id
    await play_next(chat_id)


@bot.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    text = """
👋 **Welcome to Advanced Music Bot!**

I can play music in your group voice chats with high quality audio.

**Commands:**
/play - Play a song
/pause - Pause playback
/resume - Resume playback
/skip - Skip current song
/stop - Stop and clear queue
/queue - Show queue
/nowplaying - Current song
/volume - Adjust volume
/loop - Toggle loop
/shuffle - Shuffle queue
/help - Show help

**Admin Commands:**
/ping - Check bot status
/speedtest - Speed test
/restart - Restart bot

Add me to your group and enjoy! 🎵
"""

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{(await client.get_me()).username}?startgroup=true")],
        [InlineKeyboardButton("💬 Support", url="https://t.me/telegram")],
    ])

    await message.reply_text(text, reply_markup=buttons)


@bot.on_message(filters.command("help"))
async def help_command(client: Client, message: Message):
    text = """
📚 **Music Bot Commands**

**Music Controls:**
• `/play <song/url>` - Play a song
• `/pause` - Pause current song
• `/resume` - Resume playback
• `/skip` - Skip to next song
• `/stop` - Stop and leave VC
• `/volume <1-200>` - Set volume

**Queue Management:**
• `/queue` - View queue
• `/nowplaying` - Current song info
• `/loop` - Toggle loop mode
• `/shuffle` - Shuffle queue

**Bot Management:**
• `/ping` - Check status
• `/alive` - Bot uptime
• `/speedtest` - Network speed

**Tips:**
✓ Add bot to group
✓ Start voice chat
✓ Use /play command
✓ Enjoy music! 🎵
"""
    await message.reply_text(text)


@bot.on_message(filters.command("play") & filters.group)
async def play_command(client: Client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if chat_id in command_cooldown:
        if time.time() - command_cooldown[chat_id] < 3:
            return

    command_cooldown[chat_id] = time.time()

    if len(message.command) < 2:
        await message.reply_text("❌ **Usage:** `/play <song name or URL>`")
        return

    query = message.text.split(None, 1)[1]

    msg = await message.reply_text("🔍 **Searching...**")

    try:
        song_info = await search_youtube(query)

        if not song_info:
            await msg.edit("❌ **No results found!**")
            return

        song_data = {
            'title': song_info['title'],
            'url': song_info['url'],
            'duration': song_info['duration'],
            'requested_by': message.from_user.mention,
        }

        try:
            await join_voice_chat(chat_id)
        except Exception as e:
            await msg.edit(f"❌ **Failed to join voice chat:** {str(e)}")
            return

        if current_playing.get(chat_id):
            queues[chat_id].append(song_data)
            position = len(queues[chat_id])
            await msg.edit(
                f"✅ **Added to queue at position #{position}**\n\n"
                f"**{song_info['title']}**\n"
                f"⏱ Duration: `{format_duration(song_info['duration'])}`\n"
                f"👤 Requested by: {message.from_user.mention}"
            )
        else:
            queues[chat_id].append(song_data)
            await msg.edit("⏳ **Loading...**")
            await play_next(chat_id)
            await msg.delete()

    except Exception as e:
        await msg.edit(f"❌ **Error:** {str(e)}")


@bot.on_message(filters.command("pause") & filters.group)
async def pause_command(client: Client, message: Message):
    chat_id = message.chat.id

    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply_text("❌ **Admin only command!**")
        return

    try:
        await calls.pause_stream(chat_id)
        await message.reply_text("⏸ **Paused!**")
    except:
        await message.reply_text("❌ **Nothing is playing!**")


@bot.on_message(filters.command("resume") & filters.group)
async def resume_command(client: Client, message: Message):
    chat_id = message.chat.id

    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply_text("❌ **Admin only command!**")
        return

    try:
        await calls.resume_stream(chat_id)
        await message.reply_text("▶️ **Resumed!**")
    except:
        await message.reply_text("❌ **Nothing is paused!**")


@bot.on_message(filters.command("skip") & filters.group)
async def skip_command(client: Client, message: Message):
    chat_id = message.chat.id

    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply_text("❌ **Admin only command!**")
        return

    if not current_playing.get(chat_id):
        await message.reply_text("❌ **Nothing is playing!**")
        return

    await message.reply_text("⏭ **Skipped!**")
    await play_next(chat_id)


@bot.on_message(filters.command("stop") & filters.group)
async def stop_command(client: Client, message: Message):
    chat_id = message.chat.id

    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply_text("❌ **Admin only command!**")
        return

    queues[chat_id].clear()
    current_playing[chat_id] = None
    loop_status[chat_id] = False

    await leave_voice_chat(chat_id)
    await message.reply_text("⏹ **Stopped and cleared queue!**")


@bot.on_message(filters.command("queue") & filters.group)
async def queue_command(client: Client, message: Message):
    chat_id = message.chat.id
    text = get_queue_text(chat_id)

    if current_playing.get(chat_id):
        text = f"🎵 **Now Playing:**\n**{current_playing[chat_id]['title']}**\n\n{text}"

    await message.reply_text(text)


@bot.on_message(filters.command("nowplaying") & filters.group)
async def nowplaying_command(client: Client, message: Message):
    chat_id = message.chat.id

    if not current_playing.get(chat_id):
        await message.reply_text("❌ **Nothing is playing!**")
        return

    song = current_playing[chat_id]
    text = f"🎵 **Now Playing:**\n\n**{song['title']}**\n⏱ Duration: `{format_duration(song['duration'])}`\n👤 Requested by: {song['requested_by']}"

    if loop_status.get(chat_id):
        text += "\n🔁 **Loop:** Enabled"

    await message.reply_text(text, reply_markup=get_player_buttons(chat_id))


@bot.on_message(filters.command("volume") & filters.group)
async def volume_command(client: Client, message: Message):
    chat_id = message.chat.id

    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply_text("❌ **Admin only command!**")
        return

    if len(message.command) < 2:
        await message.reply_text("❌ **Usage:** `/volume <1-200>`")
        return

    try:
        volume = int(message.command[1])
        if volume < 1 or volume > 200:
            raise ValueError

        await calls.change_volume_call(chat_id, volume)
        await message.reply_text(f"🔊 **Volume set to {volume}%**")
    except ValueError:
        await message.reply_text("❌ **Volume must be between 1-200!**")
    except:
        await message.reply_text("❌ **Nothing is playing!**")


@bot.on_message(filters.command("loop") & filters.group)
async def loop_command(client: Client, message: Message):
    chat_id = message.chat.id

    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply_text("❌ **Admin only command!**")
        return

    loop_status[chat_id] = not loop_status.get(chat_id, False)
    status = "enabled" if loop_status[chat_id] else "disabled"
    emoji = "🔁" if loop_status[chat_id] else "➡️"

    await message.reply_text(f"{emoji} **Loop {status}!**")


@bot.on_message(filters.command("shuffle") & filters.group)
async def shuffle_command(client: Client, message: Message):
    chat_id = message.chat.id

    if not await is_admin(client, chat_id, message.from_user.id):
        await message.reply_text("❌ **Admin only command!**")
        return

    if not queues[chat_id]:
        await message.reply_text("❌ **Queue is empty!**")
        return

    random.shuffle(queues[chat_id])
    await message.reply_text("🔀 **Queue shuffled!**")


@bot.on_message(filters.command("ping"))
async def ping_command(client: Client, message: Message):
    start = time.time()
    msg = await message.reply_text("🏓 **Pinging...**")
    end = time.time()

    latency = round((end - start) * 1000, 2)

    uptime = datetime.now() - START_TIME
    uptime_str = str(uptime).split('.')[0]

    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent

    text = f"""
🏓 **Pong!**

⚡ **Latency:** `{latency}ms`
⏰ **Uptime:** `{uptime_str}`
💻 **CPU:** `{cpu}%`
🎛 **RAM:** `{ram}%`
💾 **Disk:** `{disk}%`
"""

    await msg.edit(text)


@bot.on_message(filters.command("alive"))
async def alive_command(client: Client, message: Message):
    uptime = datetime.now() - START_TIME
    uptime_str = str(uptime).split('.')[0]

    text = f"""
✨ **Bot is Alive!**

⏰ **Uptime:** `{uptime_str}`
🎵 **Active VCs:** `{len(current_playing)}`
📋 **Total Queued:** `{sum(len(q) for q in queues.values())}`
🤖 **Pyrogram:** `v2.0+`
🎙 **PyTgCalls:** `v3.0+`
"""

    await message.reply_text(text)


@bot.on_message(filters.command("speedtest"))
async def speedtest_command(client: Client, message: Message):
    if not await is_admin(client, message.chat.id, message.from_user.id):
        await message.reply_text("❌ **Admin only command!**")
        return

    msg = await message.reply_text("🌐 **Running speedtest...**")

    try:
        import speedtest
        st = speedtest.Speedtest()
        st.get_best_server()

        download = st.download() / 1_000_000
        upload = st.upload() / 1_000_000
        ping = st.results.ping

        text = f"""
🌐 **Speedtest Results**

📥 **Download:** `{download:.2f} Mbps`
📤 **Upload:** `{upload:.2f} Mbps`
🏓 **Ping:** `{ping:.2f} ms`
"""
        await msg.edit(text)
    except ImportError:
        await msg.edit("❌ **Speedtest module not installed!**")
    except Exception as e:
        await msg.edit(f"❌ **Error:** {str(e)}")


@bot.on_message(filters.command("restart") & filters.user([int(x) for x in os.getenv("SUDO_USERS", "").split(",") if x.isdigit()]))
async def restart_command(client: Client, message: Message):
    await message.reply_text("🔄 **Restarting...**")
    os.execl(sys.executable, sys.executable, *sys.argv)


@bot.on_callback_query()
async def callback_handler(client: Client, callback: CallbackQuery):
    data = callback.data

    if data.startswith("close_"):
        await callback.message.delete()
        return

    chat_id = int(data.split("_")[1])
    action = data.split("_")[0]

    if not await is_admin(client, chat_id, callback.from_user.id):
        await callback.answer("❌ Admin only!", show_alert=True)
        return

    try:
        if action == "pause":
            await calls.pause_stream(chat_id)
            await callback.answer("⏸ Paused!")

        elif action == "resume":
            await calls.resume_stream(chat_id)
            await callback.answer("▶️ Resumed!")

        elif action == "skip":
            await callback.answer("⏭ Skipped!")
            await play_next(chat_id)

        elif action == "stop":
            queues[chat_id].clear()
            current_playing[chat_id] = None
            loop_status[chat_id] = False
            await leave_voice_chat(chat_id)
            await callback.answer("⏹ Stopped!")
            await callback.message.delete()
            return

        elif action == "loop":
            loop_status[chat_id] = not loop_status.get(chat_id, False)
            status = "enabled" if loop_status[chat_id] else "disabled"
            await callback.answer(f"🔁 Loop {status}!")

        elif action == "shuffle":
            if queues[chat_id]:
                random.shuffle(queues[chat_id])
                await callback.answer("🔀 Queue shuffled!")
            else:
                await callback.answer("❌ Queue is empty!", show_alert=True)
                return

        elif action == "queue":
            text = get_queue_text(chat_id)
            if current_playing.get(chat_id):
                text = f"🎵 **Now Playing:**\n**{current_playing[chat_id]['title']}**\n\n{text}"
            await callback.answer()
            await callback.message.reply_text(text)
            return

        elif action == "refresh":
            pass

        if current_playing.get(chat_id):
            song = current_playing[chat_id]
            text = f"🎵 **Now Playing:**\n\n**{song['title']}**\n⏱ Duration: `{format_duration(song['duration'])}`"
            if loop_status.get(chat_id):
                text += "\n🔁 **Loop:** Enabled"

            await callback.message.edit_text(
                text,
                reply_markup=get_player_buttons(chat_id)
            )

    except Exception as e:
        await callback.answer(f"❌ Error: {str(e)}", show_alert=True)


async def auto_cleanup():
    while True:
        await asyncio.sleep(1800)
        await cleanup_files()


async def main():
    await bot.start()
    await assistant.start()
    await calls.start()

    print("✅ Bot started successfully!")
    print(f"Bot: @{(await bot.get_me()).username}")
    print(f"Assistant: @{(await assistant.get_me()).username}")

    asyncio.create_task(auto_cleanup())

    await idle()

    await bot.stop()
    await assistant.stop()


if __name__ == "__main__":
    asyncio.run(main())
