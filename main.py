#!/usr/bin/env python3
"""
Advanced Telegram Music Bot - Fully Fixed & Optimized
Fast, reliable, and production-ready
"""

import os
import sys
import asyncio
import time
import random
import re
from typing import Dict, List, Optional
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

# Pyrogram
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait, UserAlreadyParticipant, ChatAdminRequired
from pyrogram.enums import ChatMemberStatus, ParseMode

# PyTgCalls
from pytgcalls import PyTgCalls
from pytgcalls.types import Update
from pytgcalls.types.input_stream import AudioPiped
from pytgcalls.types.input_stream.quality import HighQualityAudio, MediumQualityAudio

# Utils
import yt_dlp

# -------------------------
# Logging
# -------------------------
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# -------------------------
# Config
# -------------------------
class Config:
    API_ID = int(os.getenv("API_ID", "0"))
    API_HASH = os.getenv("API_HASH", "")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    ASSISTANT_SESSION = os.getenv("ASSISTANT_SESSION", "")
    LOG_CHANNEL = os.getenv("LOG_CHANNEL", "")
    SUDO_USERS = [int(x) for x in os.getenv("SUDO_USERS", "").split(",") if x.strip().isdigit()]
    
    DOWNLOAD_DIR = "downloads"
    MAX_DURATION = 3600
    
    @classmethod
    def validate(cls):
        if not all([cls.API_ID, cls.API_HASH, cls.BOT_TOKEN, cls.ASSISTANT_SESSION]):
            raise ValueError("Missing env vars")

Config.validate()
os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)

# -------------------------
# Enums
# -------------------------
class LoopMode(Enum):
    OFF = 0
    SINGLE = 1
    ALL = 2

# -------------------------
# Song Model
# -------------------------
@dataclass
class Song:
    title: str
    url: str
    duration: int
    video_id: str
    requester: str
    requester_id: int
    file_path: Optional[str] = None

# -------------------------
# Queue Manager
# -------------------------
class QueueManager:
    def __init__(self):
        self.queue: List[Song] = []
        self.current: Optional[Song] = None
        self.loop: LoopMode = LoopMode.OFF
        self.is_playing = False
        self.is_paused = False
    
    def add(self, song: Song) -> int:
        self.queue.append(song)
        return len(self.queue)
    
    def get_next(self) -> Optional[Song]:
        if self.loop == LoopMode.SINGLE and self.current:
            return self.current
        if self.loop == LoopMode.ALL and self.current:
            self.queue.append(self.current)
        return self.queue.pop(0) if self.queue else None
    
    def clear(self):
        self.queue.clear()
        self.current = None
        self.is_playing = False
        self.is_paused = False

# -------------------------
# Global State
# -------------------------
START_TIME = datetime.now()
queues: Dict[int, QueueManager] = defaultdict(QueueManager)
active_chats = set()

# -------------------------
# Clients
# -------------------------
bot = Client("MusicBot", api_id=Config.API_ID, api_hash=Config.API_HASH, 
             bot_token=Config.BOT_TOKEN, parse_mode=ParseMode.MARKDOWN)
assistant = Client("Assistant", api_id=Config.API_ID, api_hash=Config.API_HASH,
                  session_string=Config.ASSISTANT_SESSION)
calls = PyTgCalls(assistant)

# -------------------------
# YouTube Functions
# -------------------------
class YouTube:
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': f'{Config.DOWNLOAD_DIR}/%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'nocheckcertificate': True,
    }
    
    @staticmethod
    async def search(query: str) -> Optional[Dict]:
        """Search YouTube"""
        try:
            if not query.startswith("http"):
                query = f"ytsearch1:{query}"
            
            with yt_dlp.YoutubeDL(YouTube.ydl_opts) as ydl:
                info = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.extract_info(query, download=False)
                )
                
                if not info:
                    return None
                
                if 'entries' in info:
                    info = info['entries'][0]
                
                return {
                    'title': info.get('title', 'Unknown'),
                    'url': info.get('webpage_url', info.get('url', '')),
                    'duration': int(info.get('duration', 0)),
                    'id': info.get('id', ''),
                    'thumbnail': info.get('thumbnail', '')
                }
        except Exception as e:
            logger.error(f"Search error: {e}")
            return None
    
    @staticmethod
    async def download(url: str, video_id: str) -> Optional[str]:
        """Download audio"""
        try:
            # Check if already exists
            for ext in ['m4a', 'webm', 'opus', 'mp3']:
                file_path = f"{Config.DOWNLOAD_DIR}/{video_id}.{ext}"
                if os.path.exists(file_path):
                    logger.info(f"Using cached: {file_path}")
                    return file_path
            
            # Download
            opts = YouTube.ydl_opts.copy()
            opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '192',
            }]
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.download([url])
                )
            
            # Find file
            for ext in ['m4a', 'webm', 'opus', 'mp3']:
                file_path = f"{Config.DOWNLOAD_DIR}/{video_id}.{ext}"
                if os.path.exists(file_path):
                    logger.info(f"Downloaded: {file_path}")
                    return file_path
            
            return None
        except Exception as e:
            logger.error(f"Download error: {e}")
            return None

# -------------------------
# Player Functions
# -------------------------
class Player:
    @staticmethod
    async def play(chat_id: int, file_path: str):
        """Start playback"""
        try:
            # Create audio stream
            try:
                audio = AudioPiped(file_path, HighQualityAudio())
            except:
                audio = AudioPiped(file_path)
            
            # Join or change stream
            try:
                active = await calls.get_active_call(chat_id)
                if active:
                    await calls.change_stream(chat_id, audio)
                else:
                    await calls.join_group_call(chat_id, audio)
            except:
                await calls.join_group_call(chat_id, audio)
            
            active_chats.add(chat_id)
            logger.info(f"Playing in {chat_id}")
            
        except Exception as e:
            logger.error(f"Play error: {e}")
            raise
    
    @staticmethod
    async def pause(chat_id: int):
        await calls.pause_stream(chat_id)
    
    @staticmethod
    async def resume(chat_id: int):
        await calls.resume_stream(chat_id)
    
    @staticmethod
    async def stop(chat_id: int):
        try:
            await calls.leave_group_call(chat_id)
            active_chats.discard(chat_id)
        except:
            pass

# -------------------------
# Queue Processing
# -------------------------
async def play_next(chat_id: int):
    """Play next song"""
    try:
        qm = queues[chat_id]
        song = qm.get_next()
        
        if not song:
            await Player.stop(chat_id)
            qm.clear()
            try:
                await bot.send_message(chat_id, "✅ **Queue finished!**")
            except:
                pass
            return
        
        # Download
        if not song.file_path:
            song.file_path = await YouTube.download(song.url, song.video_id)
        
        if not song.file_path:
            await bot.send_message(chat_id, f"❌ **Download failed:** {song.title}")
            await play_next(chat_id)
            return
        
        # Play
        await Player.play(chat_id, song.file_path)
        qm.current = song
        qm.is_playing = True
        qm.is_paused = False
        
        # Send message
        text = (
            f"🎵 **Now Playing**\n\n"
            f"**{song.title}**\n"
            f"⏱ `{format_time(song.duration)}`\n"
            f"👤 {song.requester}"
        )
        
        if qm.loop != LoopMode.OFF:
            text += f"\n🔁 Loop: {qm.loop.name}"
        
        await bot.send_message(chat_id, text, reply_markup=get_buttons(chat_id))
        
    except Exception as e:
        logger.error(f"Play next error: {e}")
        await bot.send_message(chat_id, f"❌ Error: {str(e)}")

# -------------------------
# Events
# -------------------------
@calls.on_stream_end()
async def on_stream_end(client, update: Update):
    try:
        await asyncio.sleep(1)
        await play_next(update.chat_id)
    except Exception as e:
        logger.error(f"Stream end error: {e}")

@calls.on_kicked()
async def on_kicked(client, chat_id: int):
    queues[chat_id].clear()
    active_chats.discard(chat_id)

# -------------------------
# Helper Functions
# -------------------------
def format_time(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def get_buttons(chat_id: int):
    qm = queues[chat_id]
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏸ Pause" if qm.is_playing and not qm.is_paused else "▶️ Resume", 
                               callback_data=f"pause_{chat_id}"),
            InlineKeyboardButton("⏭ Skip", callback_data=f"skip_{chat_id}"),
            InlineKeyboardButton("⏹ Stop", callback_data=f"stop_{chat_id}")
        ],
        [
            InlineKeyboardButton(f"🔁 {qm.loop.name}", callback_data=f"loop_{chat_id}"),
            InlineKeyboardButton("🔀 Shuffle", callback_data=f"shuffle_{chat_id}"),
        ],
        [
            InlineKeyboardButton("📋 Queue", callback_data=f"queue_{chat_id}"),
            InlineKeyboardButton("❌ Close", callback_data=f"close_{chat_id}")
        ]
    ])

async def is_admin(chat_id: int, user_id: int) -> bool:
    if user_id in Config.SUDO_USERS:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]
    except:
        return False

async def join_assistant(chat_id: int):
    """Join assistant to chat"""
    try:
        # Check if already member
        try:
            await assistant.get_chat_member(chat_id, "me")
            return True
        except:
            pass
        
        # Get invite link
        chat = await bot.get_chat(chat_id)
        if chat.username:
            await assistant.join_chat(chat.username)
        else:
            try:
                link = await bot.export_chat_invite_link(chat_id)
                await assistant.join_chat(link)
            except:
                raise Exception("Cannot get invite link. Make bot admin!")
        
        await asyncio.sleep(2)
        return True
        
    except Exception as e:
        raise Exception(f"Join failed: {str(e)}")

# -------------------------
# Commands
# -------------------------
@bot.on_message(filters.command("start") & filters.private)
async def start_cmd(_, m: Message):
    me = await bot.get_me()
    await m.reply_text(
        f"👋 **Hi! I'm {me.first_name}**\n\n"
        "🎵 I can play music in voice chats!\n\n"
        "**Commands:**\n"
        "• /play <song> - Play music\n"
        "• /pause - Pause\n"
        "• /resume - Resume\n"
        "• /skip - Skip\n"
        "• /stop - Stop\n"
        "• /queue - View queue\n\n"
        "Add me to a group and enjoy!",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("➕ Add Me", url=f"https://t.me/{me.username}?startgroup=true")
        ]])
    )

@bot.on_message(filters.command("play") & filters.group)
async def play_cmd(_, m: Message):
    chat_id = m.chat.id
    
    if len(m.command) < 2:
        await m.reply_text("❌ Usage: `/play <song name or URL>`")
        return
    
    query = m.text.split(None, 1)[1]
    msg = await m.reply_text("🔍 **Searching...**")
    
    try:
        # Search
        result = await YouTube.search(query)
        if not result:
            await msg.edit("❌ **No results found!**")
            return
        
        # Check duration
        if result['duration'] > Config.MAX_DURATION:
            await msg.edit(f"❌ **Too long!** Max: {Config.MAX_DURATION//60} minutes")
            return
        
        # Join assistant
        try:
            await join_assistant(chat_id)
        except Exception as e:
            await msg.edit(f"❌ {str(e)}")
            return
        
        # Create song
        song = Song(
            title=result['title'],
            url=result['url'],
            duration=result['duration'],
            video_id=result['id'],
            requester=m.from_user.mention,
            requester_id=m.from_user.id
        )
        
        qm = queues[chat_id]
        
        # Add to queue
        if qm.is_playing:
            pos = qm.add(song)
            await msg.edit(f"✅ **Added to queue #{pos}**\n\n**{song.title}**\n⏱ `{format_time(song.duration)}`")
        else:
            qm.queue.insert(0, song)
            await msg.edit("⏳ **Loading...**")
            await play_next(chat_id)
            try:
                await msg.delete()
            except:
                pass
    
    except Exception as e:
        logger.error(f"Play error: {e}")
        await msg.edit(f"❌ **Error:** {str(e)}")

@bot.on_message(filters.command("pause") & filters.group)
async def pause_cmd(_, m: Message):
    if not await is_admin(m.chat.id, m.from_user.id):
        await m.reply_text("❌ **Admins only!**")
        return
    
    qm = queues[m.chat.id]
    if not qm.is_playing:
        await m.reply_text("❌ **Nothing playing!**")
        return
    
    try:
        await Player.pause(m.chat.id)
        qm.is_paused = True
        await m.reply_text("⏸ **Paused**")
    except Exception as e:
        await m.reply_text(f"❌ {str(e)}")

@bot.on_message(filters.command("resume") & filters.group)
async def resume_cmd(_, m: Message):
    if not await is_admin(m.chat.id, m.from_user.id):
        await m.reply_text("❌ **Admins only!**")
        return
    
    qm = queues[m.chat.id]
    if not qm.is_paused:
        await m.reply_text("❌ **Not paused!**")
        return
    
    try:
        await Player.resume(m.chat.id)
        qm.is_paused = False
        await m.reply_text("▶️ **Resumed**")
    except Exception as e:
        await m.reply_text(f"❌ {str(e)}")

@bot.on_message(filters.command("skip") & filters.group)
async def skip_cmd(_, m: Message):
    if not await is_admin(m.chat.id, m.from_user.id):
        await m.reply_text("❌ **Admins only!**")
        return
    
    qm = queues[m.chat.id]
    if not qm.is_playing:
        await m.reply_text("❌ **Nothing playing!**")
        return
    
    await m.reply_text("⏭ **Skipped**")
    await play_next(m.chat.id)

@bot.on_message(filters.command("stop") & filters.group)
async def stop_cmd(_, m: Message):
    if not await is_admin(m.chat.id, m.from_user.id):
        await m.reply_text("❌ **Admins only!**")
        return
    
    await Player.stop(m.chat.id)
    queues[m.chat.id].clear()
    await m.reply_text("⏹ **Stopped & cleared**")

@bot.on_message(filters.command("queue") & filters.group)
async def queue_cmd(_, m: Message):
    qm = queues[m.chat.id]
    
    if not qm.queue and not qm.current:
        await m.reply_text("📭 **Queue is empty**")
        return
    
    text = ""
    if qm.current:
        text += f"🎵 **Now Playing:**\n**{qm.current.title}**\n\n"
    
    if qm.queue:
        text += "📋 **Queue:**\n\n"
        for i, song in enumerate(qm.queue[:10], 1):
            text += f"`{i}.` **{song.title}**\n   ⏱ `{format_time(song.duration)}`\n\n"
        if len(qm.queue) > 10:
            text += f"\n*...and {len(qm.queue)-10} more*"
    
    await m.reply_text(text)

@bot.on_message(filters.command("ping"))
async def ping_cmd(_, m: Message):
    start = time.time()
    msg = await m.reply_text("🏓 **Pinging...**")
    end = time.time()
    await msg.edit(f"🏓 **Pong!**\n⚡️ `{(end-start)*1000:.2f} ms`")

# -------------------------
# Callbacks
# -------------------------
@bot.on_callback_query()
async def callbacks(_, cb: CallbackQuery):
    data = cb.data
    
    if data.startswith("close_"):
        try:
            await cb.message.delete()
        except:
            pass
        return
    
    try:
        action, chat_id = data.rsplit("_", 1)
        chat_id = int(chat_id)
    except:
        await cb.answer("Invalid!", show_alert=True)
        return
    
    if action not in ["queue"] and not await is_admin(chat_id, cb.from_user.id):
        await cb.answer("❌ Admins only!", show_alert=True)
        return
    
    qm = queues[chat_id]
    
    try:
        if action == "pause":
            if qm.is_paused:
                await Player.resume(chat_id)
                qm.is_paused = False
                await cb.answer("▶️ Resumed")
            else:
                await Player.pause(chat_id)
                qm.is_paused = True
                await cb.answer("⏸ Paused")
        
        elif action == "skip":
            await play_next(chat_id)
            await cb.answer("⏭ Skipped")
        
        elif action == "stop":
            await Player.stop(chat_id)
            qm.clear()
            await cb.answer("⏹ Stopped")
            try:
                await cb.message.delete()
            except:
                pass
            return
        
        elif action == "loop":
            if qm.loop == LoopMode.OFF:
                qm.loop = LoopMode.SINGLE
            elif qm.loop == LoopMode.SINGLE:
                qm.loop = LoopMode.ALL
            else:
                qm.loop = LoopMode.OFF
            await cb.answer(f"🔁 {qm.loop.name}")
        
        elif action == "shuffle":
            if qm.queue:
                random.shuffle(qm.queue)
                await cb.answer("🔀 Shuffled")
            else:
                await cb.answer("Queue empty!", show_alert=True)
                return
        
        elif action == "queue":
            text = ""
            if qm.current:
                text += f"🎵 **Now:** {qm.current.title}\n\n"
            if qm.queue:
                text += "📋 **Queue:**\n"
                for i, s in enumerate(qm.queue[:5], 1):
                    text += f"`{i}.` {s.title}\n"
            else:
                text += "📭 Empty"
            await cb.answer()
            await cb.message.reply_text(text)
            return
        
        # Update buttons
        if qm.current:
            try:
                await cb.message.edit_reply_markup(reply_markup=get_buttons(chat_id))
            except:
                pass
    
    except Exception as e:
        await cb.answer(f"❌ {str(e)}", show_alert=True)

# -------------------------
# Background Tasks
# -------------------------
async def cleanup():
    """Cleanup old files"""
    while True:
        try:
            await asyncio.sleep(1800)
            now = time.time()
            for f in os.listdir(Config.DOWNLOAD_DIR):
                path = os.path.join(Config.DOWNLOAD_DIR, f)
                if os.path.isfile(path) and now - os.path.getmtime(path) > 3600:
                    try:
                        os.remove(path)
                        logger.info(f"Cleaned: {f}")
                    except:
                        pass
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

# -------------------------
# Main
# -------------------------
async def main():
    try:
        logger.info("Starting...")
        
        await bot.start()
        await assistant.start()
        await calls.start()
        
        bot_me = await bot.get_me()
        ass_me = await assistant.get_me()
        
        logger.info(f"✅ Bot: @{bot_me.username}")
        logger.info(f"✅ Assistant: @{ass_me.username}")
        
        asyncio.create_task(cleanup())
        
        logger.info("✅ Ready!")
        await idle()
        
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await calls.stop()
        await bot.stop()
        await assistant.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped")
    except Exception as e:
        logger.error(f"Fatal: {e}")
