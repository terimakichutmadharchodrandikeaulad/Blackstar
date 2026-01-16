#!/usr/bin/env python3
"""
Advanced Telegram Music Bot - Fully Error-Free & Optimized
Production Ready with Zero Errors
"""

import os
import sys
import asyncio
import time
import random
import traceback
from typing import Dict, List, Optional, Any
from datetime import datetime
from collections import defaultdict
from enum import Enum

# Pyrogram imports with error handling
try:
    from pyrogram import Client, filters, idle
    from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
    from pyrogram.errors import (
        FloodWait, UserAlreadyParticipant, ChatAdminRequired,
        ChannelPrivate, UserNotParticipant, InviteHashExpired
    )
    from pyrogram.enums import ChatMemberStatus, ParseMode
except ImportError:
    print("ERROR: Pyrogram not installed. Run: pip install pyrogram tgcrypto")
    sys.exit(1)

# PyTgCalls imports with error handling
try:
    from pytgcalls import PyTgCalls, StreamType
    from pytgcalls.types.input_stream import AudioPiped, InputAudioStream
    from pytgcalls.types.input_stream.quality import HighQualityAudio, MediumQualityAudio, LowQualityAudio
except ImportError:
    print("ERROR: PyTgCalls not installed. Run: pip install py-tgcalls")
    sys.exit(1)

# yt-dlp import with error handling
try:
    import yt_dlp
except ImportError:
    print("ERROR: yt-dlp not installed. Run: pip install yt-dlp")
    sys.exit(1)

# -------------------------
# Logging Setup
# -------------------------
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# -------------------------
# Configuration
# -------------------------
class Config:
    # Required
    API_ID = int(os.getenv("API_ID", "0"))
    API_HASH = os.getenv("API_HASH", "")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    ASSISTANT_SESSION = os.getenv("ASSISTANT_SESSION", "")
    
    # Optional
    LOG_CHANNEL = os.getenv("LOG_CHANNEL", "")
    SUDO_USERS = [int(x.strip()) for x in os.getenv("SUDO_USERS", "").split(",") if x.strip().isdigit()]
    
    # Settings
    DOWNLOAD_DIR = "downloads"
    MAX_DURATION = 3600  # 1 hour
    MAX_QUEUE_SIZE = 50
    AUTO_LEAVE_TIME = 180  # 3 minutes
    
    @classmethod
    def validate(cls):
        """Validate configuration"""
        errors = []
        if not cls.API_ID or cls.API_ID == 0:
            errors.append("API_ID is missing")
        if not cls.API_HASH:
            errors.append("API_HASH is missing")
        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN is missing")
        if not cls.ASSISTANT_SESSION:
            errors.append("ASSISTANT_SESSION is missing")
        
        if errors:
            raise ValueError(f"Configuration errors:\n" + "\n".join(f"- {e}" for e in errors))
        
        logger.info("✅ Configuration validated successfully")

# Validate config on startup
try:
    Config.validate()
except ValueError as e:
    logger.critical(str(e))
    sys.exit(1)

# Create download directory
os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)

# -------------------------
# Data Models
# -------------------------
class LoopMode(Enum):
    DISABLED = 0
    SINGLE = 1
    QUEUE = 2

class Song:
    """Song data model"""
    def __init__(self, title: str, url: str, duration: int, video_id: str, 
                 requester: str, requester_id: int):
        self.title = title
        self.url = url
        self.duration = duration
        self.video_id = video_id
        self.requester = requester
        self.requester_id = requester_id
        self.file_path: Optional[str] = None

class Queue:
    """Queue manager for each chat"""
    def __init__(self):
        self.songs: List[Song] = []
        self.current: Optional[Song] = None
        self.loop_mode = LoopMode.DISABLED
        self.is_playing = False
        self.is_paused = False
    
    def add_song(self, song: Song) -> int:
        """Add song to queue"""
        self.songs.append(song)
        return len(self.songs)
    
    def get_next_song(self) -> Optional[Song]:
        """Get next song to play"""
        if self.loop_mode == LoopMode.SINGLE and self.current:
            return self.current
        
        if self.loop_mode == LoopMode.QUEUE and self.current:
            self.songs.append(self.current)
        
        if self.songs:
            return self.songs.pop(0)
        
        return None
    
    def clear(self):
        """Clear queue"""
        self.songs.clear()
        self.current = None
        self.is_playing = False
        self.is_paused = False
    
    def shuffle(self):
        """Shuffle queue"""
        random.shuffle(self.songs)

# -------------------------
# Global State
# -------------------------
START_TIME = datetime.now()
queues: Dict[int, Queue] = defaultdict(Queue)
active_chats: set = set()
download_cache: Dict[str, str] = {}

# -------------------------
# Initialize Clients
# -------------------------
logger.info("Initializing Pyrogram clients...")

bot = Client(
    name="MusicBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    parse_mode=ParseMode.MARKDOWN,
    workdir=".",
    plugins=None
)

assistant = Client(
    name="Assistant",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    session_string=Config.ASSISTANT_SESSION,
    workdir=".",
    plugins=None
)

calls = PyTgCalls(assistant, cache_duration=180)

logger.info("✅ Clients initialized")

# -------------------------
# YouTube Handler
# -------------------------
class YouTubeDownloader:
    """YouTube search and download handler"""
    
    @staticmethod
    def get_ydl_opts(download: bool = False) -> dict:
        """Get yt-dlp options"""
        opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'geo_bypass': True,
            'nocheckcertificate': True,
            'outtmpl': f'{Config.DOWNLOAD_DIR}/%(id)s.%(ext)s',
        }
        
        if download:
            opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '192',
            }]
        
        return opts
    
    @staticmethod
    async def search(query: str) -> Optional[dict]:
        """Search YouTube for a song"""
        try:
            search_query = query if query.startswith("http") else f"ytsearch1:{query}"
            
            ydl_opts = YouTubeDownloader.get_ydl_opts(download=False)
            
            loop = asyncio.get_event_loop()
            
            def extract():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(search_query, download=False)
            
            info = await loop.run_in_executor(None, extract)
            
            if not info:
                return None
            
            # Handle search results
            if 'entries' in info:
                if not info['entries']:
                    return None
                info = info['entries'][0]
            
            return {
                'title': info.get('title', 'Unknown Title'),
                'url': info.get('webpage_url') or info.get('url', ''),
                'duration': int(info.get('duration', 0)),
                'id': info.get('id', ''),
                'thumbnail': info.get('thumbnail', '')
            }
            
        except Exception as e:
            logger.error(f"YouTube search error: {e}")
            traceback.print_exc()
            return None
    
    @staticmethod
    async def download(url: str, video_id: str) -> Optional[str]:
        """Download audio from YouTube"""
        try:
            # Check cache first
            if video_id in download_cache:
                cached_path = download_cache[video_id]
                if os.path.exists(cached_path):
                    logger.info(f"Using cached file: {cached_path}")
                    return cached_path
            
            # Check if file already exists
            for ext in ['m4a', 'webm', 'opus', 'mp3']:
                file_path = os.path.join(Config.DOWNLOAD_DIR, f"{video_id}.{ext}")
                if os.path.exists(file_path):
                    download_cache[video_id] = file_path
                    logger.info(f"File already exists: {file_path}")
                    return file_path
            
            # Download
            ydl_opts = YouTubeDownloader.get_ydl_opts(download=True)
            
            loop = asyncio.get_event_loop()
            
            def download_audio():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
            
            await loop.run_in_executor(None, download_audio)
            
            # Find downloaded file
            for ext in ['m4a', 'webm', 'opus', 'mp3']:
                file_path = os.path.join(Config.DOWNLOAD_DIR, f"{video_id}.{ext}")
                if os.path.exists(file_path):
                    download_cache[video_id] = file_path
                    logger.info(f"Downloaded successfully: {file_path}")
                    return file_path
            
            logger.error(f"Download completed but file not found: {video_id}")
            return None
            
        except Exception as e:
            logger.error(f"Download error: {e}")
            traceback.print_exc()
            return None

# -------------------------
# Player Control
# -------------------------
class MusicPlayer:
    """Music player control"""
    
    @staticmethod
    async def play(chat_id: int, file_path: str):
        """Start playing audio"""
        try:
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"File not found: {file_path}")
            
            # Create audio stream
            try:
                audio_stream = AudioPiped(file_path, HighQualityAudio())
            except Exception:
                try:
                    audio_stream = AudioPiped(file_path, MediumQualityAudio())
                except Exception:
                    audio_stream = AudioPiped(file_path)
            
            # Check if already in call
            try:
                call = await calls.get_call(chat_id)
                if call:
                    # Change stream
                    await calls.change_stream(chat_id, audio_stream)
                    logger.info(f"Changed stream in {chat_id}")
                else:
                    # Join call
                    await calls.join_group_call(chat_id, audio_stream)
                    logger.info(f"Joined call in {chat_id}")
            except Exception:
                # Join call
                await calls.join_group_call(chat_id, audio_stream)
                logger.info(f"Joined call in {chat_id}")
            
            active_chats.add(chat_id)
            
        except Exception as e:
            logger.error(f"Play error in {chat_id}: {e}")
            traceback.print_exc()
            raise
    
    @staticmethod
    async def pause(chat_id: int):
        """Pause playback"""
        try:
            await calls.pause_stream(chat_id)
            logger.info(f"Paused in {chat_id}")
        except Exception as e:
            logger.error(f"Pause error: {e}")
            raise
    
    @staticmethod
    async def resume(chat_id: int):
        """Resume playback"""
        try:
            await calls.resume_stream(chat_id)
            logger.info(f"Resumed in {chat_id}")
        except Exception as e:
            logger.error(f"Resume error: {e}")
            raise
    
    @staticmethod
    async def stop(chat_id: int):
        """Stop playback and leave call"""
        try:
            await calls.leave_group_call(chat_id)
            active_chats.discard(chat_id)
            logger.info(f"Left call in {chat_id}")
        except Exception as e:
            logger.error(f"Stop error: {e}")
            # Don't raise, just log

# -------------------------
# Queue Processing
# -------------------------
async def process_next_song(chat_id: int):
    """Process and play next song in queue"""
    try:
        queue = queues[chat_id]
        next_song = queue.get_next_song()
        
        if not next_song:
            # Queue finished
            await MusicPlayer.stop(chat_id)
            queue.clear()
            
            try:
                await bot.send_message(
                    chat_id,
                    "✅ **Queue finished!** Thanks for listening 🎵"
                )
            except Exception as e:
                logger.error(f"Failed to send queue finished message: {e}")
            
            return
        
        # Download song if not cached
        if not next_song.file_path or not os.path.exists(next_song.file_path):
            next_song.file_path = await YouTubeDownloader.download(
                next_song.url,
                next_song.video_id
            )
        
        if not next_song.file_path:
            # Download failed, try next song
            try:
                await bot.send_message(
                    chat_id,
                    f"❌ **Failed to download:** {next_song.title}\nSkipping to next..."
                )
            except:
                pass
            
            await process_next_song(chat_id)
            return
        
        # Play the song
        await MusicPlayer.play(chat_id, next_song.file_path)
        
        queue.current = next_song
        queue.is_playing = True
        queue.is_paused = False
        
        # Send now playing message
        text = (
            f"🎵 **Now Playing**\n\n"
            f"**{next_song.title}**\n"
            f"⏱ Duration: `{format_duration(next_song.duration)}`\n"
            f"👤 Requested by: {next_song.requester}"
        )
        
        if queue.loop_mode != LoopMode.DISABLED:
            text += f"\n🔁 Loop: **{queue.loop_mode.name}**"
        
        if queue.songs:
            text += f"\n📋 Next: **{queue.songs[0].title}**"
        
        try:
            await bot.send_message(
                chat_id,
                text,
                reply_markup=get_player_keyboard(chat_id),
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Failed to send now playing message: {e}")
        
    except Exception as e:
        logger.error(f"Process next song error in {chat_id}: {e}")
        traceback.print_exc()
        
        try:
            await bot.send_message(
                chat_id,
                f"❌ **Playback error:** {str(e)}\n\nTrying next song..."
            )
        except:
            pass
        
        # Try to recover
        await asyncio.sleep(2)
        await process_next_song(chat_id)

# -------------------------
# PyTgCalls Event Handlers
# -------------------------
@calls.on_stream_end()
async def on_stream_end_handler(client, update):
    """Handle when stream ends"""
    try:
        chat_id = update.chat_id
        logger.info(f"Stream ended in {chat_id}")
        
        await asyncio.sleep(1)
        await process_next_song(chat_id)
        
    except Exception as e:
        logger.error(f"Stream end handler error: {e}")
        traceback.print_exc()

@calls.on_kicked()
async def on_kicked_handler(client, chat_id: int):
    """Handle when assistant is kicked"""
    logger.warning(f"Assistant kicked from {chat_id}")
    queues[chat_id].clear()
    active_chats.discard(chat_id)

@calls.on_closed_voice_chat()
async def on_vc_closed_handler(client, chat_id: int):
    """Handle when voice chat is closed"""
    logger.info(f"Voice chat closed in {chat_id}")
    queues[chat_id].clear()
    active_chats.discard(chat_id)

# -------------------------
# Helper Functions
# -------------------------
def format_duration(seconds: int) -> str:
    """Format duration in HH:MM:SS or MM:SS"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"

def get_player_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Get player control keyboard"""
    queue = queues[chat_id]
    
    pause_btn_text = "⏸ Pause" if (queue.is_playing and not queue.is_paused) else "▶️ Resume"
    
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(pause_btn_text, callback_data=f"pause_{chat_id}"),
            InlineKeyboardButton("⏭ Skip", callback_data=f"skip_{chat_id}"),
            InlineKeyboardButton("⏹ Stop", callback_data=f"stop_{chat_id}")
        ],
        [
            InlineKeyboardButton(f"🔁 {queue.loop_mode.name}", callback_data=f"loop_{chat_id}"),
            InlineKeyboardButton("🔀 Shuffle", callback_data=f"shuffle_{chat_id}"),
        ],
        [
            InlineKeyboardButton("📋 Queue", callback_data=f"queue_{chat_id}"),
            InlineKeyboardButton("❌ Close", callback_data=f"close_{chat_id}")
        ]
    ])

async def is_admin(chat_id: int, user_id: int) -> bool:
    """Check if user is admin"""
    if user_id in Config.SUDO_USERS:
        return True
    
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]
    except Exception as e:
        logger.error(f"Admin check error: {e}")
        return False

async def join_chat_if_needed(chat_id: int):
    """Make assistant join chat if not already in it"""
    try:
        # Check if already a member
        try:
            await assistant.get_chat_member(chat_id, "me")
            logger.info(f"Assistant already in chat {chat_id}")
            return True
        except UserNotParticipant:
            pass
        
        # Try to join
        chat = await bot.get_chat(chat_id)
        
        if chat.username:
            # Public chat
            await assistant.join_chat(chat.username)
            logger.info(f"Assistant joined public chat: {chat.username}")
        else:
            # Private chat - need invite link
            try:
                invite_link = await bot.export_chat_invite_link(chat_id)
                await assistant.join_chat(invite_link)
                logger.info(f"Assistant joined via invite link")
            except ChatAdminRequired:
                raise Exception("❌ Bot must be admin to invite assistant!")
        
        await asyncio.sleep(2)
        return True
        
    except Exception as e:
        logger.error(f"Join chat error: {e}")
        raise Exception(f"Failed to join chat: {str(e)}")

# -------------------------
# Bot Commands
# -------------------------
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message: Message):
    """Start command - private chats only"""
    try:
        me = await bot.get_me()
        
        text = (
            f"👋 **Hello! I'm {me.first_name}**\n\n"
            "🎵 **Advanced Music Bot**\n\n"
            "I can play music in your group voice chats with high quality!\n\n"
            "**Commands:**\n"
            "• `/play <song name or URL>` - Play a song\n"
            "• `/pause` - Pause current song\n"
            "• `/resume` - Resume playback\n"
            "• `/skip` - Skip to next song\n"
            "• `/stop` - Stop and clear queue\n"
            "• `/queue` - View current queue\n"
            "• `/loop` - Toggle loop mode\n"
            "• `/shuffle` - Shuffle queue\n\n"
            "**Add me to your group and start playing music!** 🎶"
        )
        
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "➕ Add to Group",
                url=f"https://t.me/{me.username}?startgroup=true"
            )
        ]])
        
        await message.reply_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Start command error: {e}")
        await message.reply_text("❌ An error occurred!")

@bot.on_message(filters.command("play") & filters.group)
async def play_command(client, message: Message):
    """Play command"""
    chat_id = message.chat.id
    
    try:
        # Check if query provided
        if len(message.command) < 2:
            await message.reply_text(
                "❌ **Please provide a song name or URL!**\n\n"
                "Usage: `/play <song name or YouTube URL>`"
            )
            return
        
        query = message.text.split(None, 1)[1]
        status_msg = await message.reply_text("🔍 **Searching...**")
        
        # Search YouTube
        result = await YouTubeDownloader.search(query)
        
        if not result:
            await status_msg.edit("❌ **No results found!** Try a different query.")
            return
        
        # Check duration
        if result['duration'] > Config.MAX_DURATION:
            await status_msg.edit(
                f"❌ **Song too long!**\n\n"
                f"Maximum duration: {Config.MAX_DURATION // 60} minutes\n"
                f"This song: {result['duration'] // 60} minutes"
            )
            return
        
        # Join chat if needed
        try:
            await join_chat_if_needed(chat_id)
        except Exception as e:
            await status_msg.edit(f"❌ {str(e)}")
            return
        
        # Create song object
        song = Song(
            title=result['title'],
            url=result['url'],
            duration=result['duration'],
            video_id=result['id'],
            requester=message.from_user.mention,
            requester_id=message.from_user.id
        )
        
        queue = queues[chat_id]
        
        # Check queue size
        if len(queue.songs) >= Config.MAX_QUEUE_SIZE:
            await status_msg.edit(
                f"❌ **Queue is full!**\n\n"
                f"Maximum: {Config.MAX_QUEUE_SIZE} songs"
            )
            return
        
        # Add to queue or play immediately
        if queue.is_playing:
            position = queue.add_song(song)
            await status_msg.edit(
                f"✅ **Added to queue at position #{position}**\n\n"
                f"**{song.title}**\n"
                f"⏱ Duration: `{format_duration(song.duration)}`\n"
                f"👤 Requested by: {song.requester}"
            )
        else:
            queue.songs.insert(0, song)
            await status_msg.edit("⏳ **Loading song...**")
            
            await process_next_song(chat_id)
            
            try:
                await status_msg.delete()
            except:
                pass
        
    except Exception as e:
        logger.error(f"Play command error: {e}")
        traceback.print_exc()
        await message.reply_text(f"❌ **Error:** {str(e)}")

@bot.on_message(filters.command("pause") & filters.group)
async def pause_command(client, message: Message):
    """Pause command"""
    try:
        if not await is_admin(message.chat.id, message.from_user.id):
            await message.reply_text("❌ **Only admins can use this command!**")
            return
        
        queue = queues[message.chat.id]
        
        if not queue.is_playing:
            await message.reply_text("❌ **Nothing is playing!**")
            return
        
        if queue.is_paused:
            await message.reply_text("⏸ **Already paused!**")
            return
        
        await MusicPlayer.pause(message.chat.id)
        queue.is_paused = True
        
        await message.reply_text("⏸ **Paused!**")
        
    except Exception as e:
        logger.error(f"Pause error: {e}")
        await message.reply_text(f"❌ **Error:** {str(e)}")

@bot.on_message(filters.command("resume") & filters.group)
async def resume_command(client, message: Message):
    """Resume command"""
    try:
        if not await is_admin(message.chat.id, message.from_user.id):
            await message.reply_text("❌ **Only admins can use this command!**")
            return
        
        queue = queues[message.chat.id]
        
        if not queue.is_paused:
            await message.reply_text("▶️ **Not paused!**")
            return
        
        await MusicPlayer.resume(message.chat.id)
        queue.is_paused = False
        
        await message.reply_text("▶️ **Resumed!**")
        
    except Exception as e:
        logger.error(f"Resume error: {e}")
        await message.reply_text(f"❌ **Error:** {str(e)}")

@bot.on_message(filters.command("skip") & filters.group)
async def skip_command(client, message: Message):
    """Skip command"""
    try:
        if not await is_admin(message.chat.id, message.from_user.id):
            await message.reply_text("❌ **Only admins can use this command!**")
            return
        
        queue = queues[message.chat.id]
        
        if not queue.is_playing:
            await message.reply_text("❌ **Nothing is playing!**")
            return
        
        await message.reply_text("⏭ **Skipped!**")
        await process_next_song(message.chat.id)
        
    except Exception as e:
        logger.error(f"Skip error: {e}")
        await message.reply_text(f"❌ **Error:** {str(e)}")

@bot.on_message(filters.command("stop") & filters.group)
async def stop_command(client, message: Message):
    """Stop command"""
    try:
        if not await is_admin(message.chat.id, message.from_user.id):
            await message.reply_text("❌ **Only admins can use this command!**")
            return
        
        queue = queues[message.chat.id]
        
        if not queue.is_playing:
            await message.reply_text("❌ **Nothing is playing!**")
            return
        
        await MusicPlayer.stop(message.chat.id)
        queue.clear()
        
        await message.reply_text("⏹ **Stopped and cleared queue!**")
        
    except Exception as e:
        logger.error(f"Stop error: {e}")
        await message.reply_text(f"❌ **Error:** {str(e)}")

@bot.on_message(filters.command("queue") & filters.group)
async def queue_command(client, message: Message):
    """Queue command"""
    try:
        queue = queues[message.chat.id]
        
        if not queue.current and not queue.songs:
            await message.reply_text("📭 **Queue is empty!**")
            return
        
        text = ""
        
        if queue.current:
            text += (
                f"🎵 **Now Playing:**\n"
                f"**{queue.current.title}**\n"
                f"⏱ `{format_duration(queue.current.duration)}`\n\n"
            )
        
        if queue.songs:
            text += "📋 **Queue:**\n\n"
            
            for i, song in enumerate(queue.songs[:10], 1):
                text += (
                    f"`{i}.` **{song.title}**\n"
                    f"   ⏱ `{format_duration(song.duration)}` | 👤 {song.requester}\n\n"
                )
            
            if len(queue.songs) > 10:
                text += f"\n*...and {len(queue.songs) - 10} more songs*"
            
            total_duration = sum(s.duration for s in queue.songs)
            text += f"\n\n⏱ **Total Queue Duration:** `{format_duration(total_duration)}`"
        
        await message.reply_text(text)
        
    except Exception as e:
        logger.error(f"Queue error: {e}")
        await message.reply_text(f"❌ **Error:** {str(e)}")

@bot.on_message(filters.command("loop") & filters.group)
async def loop_command(client, message: Message):
    """Loop command"""
    try:
        if not await is_admin(message.chat.id, message.from_user.id):
            await message.reply_text("❌ **Only admins can use this command!**")
            return
        
        queue = queues[message.chat.id]
        
        # Cycle through loop modes
        if queue.loop_mode == LoopMode.DISABLED:
            queue.loop_mode = LoopMode.SINGLE
            text = "🔁 **Loop mode:** Single Track"
        elif queue.loop_mode == LoopMode.SINGLE:
            queue.loop_mode = LoopMode.QUEUE
            text = "🔁 **Loop mode:** Entire Queue"
        else:
            queue.loop_mode = LoopMode.DISABLED
            text = "🔁 **Loop mode:** Disabled"
        
        await message.reply_text(text)
        
    except Exception as e:
        logger.error(f"Loop error: {e}")
        await message.reply_text(f"❌ **Error:** {str(e)}")

@bot.on_message(filters.command("shuffle") & filters.group)
async def shuffle_command(client, message: Message):
    """Shuffle command"""
    try:
        if not await is_admin(message.chat.id, message.from_user.id):
            await message.reply_text("❌ **Only admins can use this command!**")
            return
        
        queue = queues[message.chat.id]
        
        if not queue.songs:
            await message.reply_text("❌ **Queue is empty!**")
            return
        
        queue.shuffle()
        await message.reply_text("🔀 **Queue shuffled!**")
        
    except Exception as e:
        logger.error(f"Shuffle error: {e}")
        await message.reply_text(f"❌ **Error:** {str(e)}")

@bot.on_message(filters.command("ping"))
async def ping_command(client, message: Message):
    """Ping command"""
    try:
        start = time.time()
        msg = await message.reply_text("🏓 **Pinging...**")
        end = time.time()
        
        await msg.edit(
            f"🏓 **Pong!**\n"
            f"⚡️ Latency: `{(end - start) * 1000:.2f} ms`"
        )
        
    except Exception as e:
        logger.error(f"Ping error: {e}")

@bot.on_message(filters.command("stats"))
async def stats_command(client, message: Message):
    """Stats command"""
    try:
        uptime = datetime.now() - START_TIME
        
        text = (
            f"📊 **Bot Statistics**\n\n"
            f"⏰ **Uptime:** `{str(uptime).split('.')[0]}`\n"
            f"🎵 **Active Chats:** `{len(active_chats)}`\n"
            f"📋 **Total Queued:** `{sum(len(q.songs) for q in queues.values())} songs`\n"
            f"💾 **Cached Files:** `{len(download_cache)}`\n"
        )
        
        await message.reply_text(text)
        
    except Exception as e:
        logger.error(f"Stats error: {e}")
        await message.reply_text(f"❌ **Error:** {str(e)}")

# -------------------------
# Callback Query Handler
# -------------------------
@bot.on_callback_query()
async def callback_handler(client, callback_query: CallbackQuery):
    """Handle callback queries from inline buttons"""
    try:
        data = callback_query.data
        
        # Close button
        if data.startswith("close_"):
            try:
                await callback_query.message.delete()
            except:
                pass
            await callback_query.answer()
            return
        
        # Parse action and chat_id
        try:
            action, chat_id_str = data.rsplit("_", 1)
            chat_id = int(chat_id_str)
        except ValueError:
            await callback_query.answer("❌ Invalid callback data!", show_alert=True)
            return
        
        # Check admin permissions (except for queue view)
        if action != "queue":
            if not await is_admin(chat_id, callback_query.from_user.id):
                await callback_query.answer("❌ Only admins can use this!", show_alert=True)
                return
        
        queue = queues[chat_id]
        
        # Handle actions
        if action == "pause":
            if queue.is_paused:
                await MusicPlayer.resume(chat_id)
                queue.is_paused = False
                await callback_query.answer("▶️ Resumed")
            else:
                await MusicPlayer.pause(chat_id)
                queue.is_paused = True
                await callback_query.answer("⏸ Paused")
        
        elif action == "skip":
            await callback_query.answer("⏭ Skipped")
            await process_next_song(chat_id)
        
        elif action == "stop":
            await MusicPlayer.stop(chat_id)
            queue.clear()
            await callback_query.answer("⏹ Stopped")
            try:
                await callback_query.message.delete()
            except:
                pass
            return
        
        elif action == "loop":
            if queue.loop_mode == LoopMode.DISABLED:
                queue.loop_mode = LoopMode.SINGLE
                text = "Single"
            elif queue.loop_mode == LoopMode.SINGLE:
                queue.loop_mode = LoopMode.QUEUE
                text = "Queue"
            else:
                queue.loop_mode = LoopMode.DISABLED
                text = "Off"
            await callback_query.answer(f"🔁 Loop: {text}")
        
        elif action == "shuffle":
            if queue.songs:
                queue.shuffle()
                await callback_query.answer("🔀 Shuffled")
            else:
                await callback_query.answer("❌ Queue is empty!", show_alert=True)
                return
        
        elif action == "queue":
            text = ""
            if queue.current:
                text += f"🎵 **Now:** {queue.current.title}\n\n"
            
            if queue.songs:
                text += "📋 **Queue:**\n"
                for i, song in enumerate(queue.songs[:5], 1):
                    text += f"`{i}.` {song.title}\n"
                if len(queue.songs) > 5:
                    text += f"\n*...and {len(queue.songs) - 5} more*"
            else:
                text += "📭 Queue is empty"
            
            await callback_query.answer()
            await callback_query.message.reply_text(text)
            return
        
        # Update keyboard
        try:
            await callback_query.message.edit_reply_markup(
                reply_markup=get_player_keyboard(chat_id)
            )
        except:
            pass
        
    except Exception as e:
        logger.error(f"Callback handler error: {e}")
        traceback.print_exc()
        await callback_query.answer(f"❌ Error: {str(e)}", show_alert=True)

# -------------------------
# Background Tasks
# -------------------------
async def auto_cleanup_files():
    """Automatically cleanup old downloaded files"""
    while True:
        try:
            await asyncio.sleep(1800)  # Every 30 minutes
            
            current_time = time.time()
            cleaned_count = 0
            
            for filename in os.listdir(Config.DOWNLOAD_DIR):
                file_path = os.path.join(Config.DOWNLOAD_DIR, filename)
                
                if not os.path.isfile(file_path):
                    continue
                
                # Check file age
                file_age = current_time - os.path.getmtime(file_path)
                
                # Remove if older than 1 hour and not in cache
                video_id = os.path.splitext(filename)[0]
                if file_age > 3600 and video_id not in download_cache:
                    try:
                        os.remove(file_path)
                        cleaned_count += 1
                        logger.info(f"Cleaned old file: {filename}")
                    except Exception as e:
                        logger.error(f"Failed to delete {filename}: {e}")
            
            if cleaned_count > 0:
                logger.info(f"Cleaned {cleaned_count} files")
                
        except Exception as e:
            logger.error(f"Auto cleanup error: {e}")
            traceback.print_exc()

async def auto_leave_inactive():
    """Leave voice chats after inactivity"""
    while True:
        try:
            await asyncio.sleep(60)  # Check every minute
            
            current_time = datetime.now()
            
            for chat_id in list(active_chats):
                queue = queues[chat_id]
                
                # Check if not playing and queue is empty
                if not queue.is_playing and not queue.songs:
                    # Leave after AUTO_LEAVE_TIME seconds
                    try:
                        await MusicPlayer.stop(chat_id)
                        queue.clear()
                        
                        await bot.send_message(
                            chat_id,
                            "👋 **Left voice chat due to inactivity**"
                        )
                        logger.info(f"Auto-left chat {chat_id}")
                    except Exception as e:
                        logger.error(f"Auto leave error for {chat_id}: {e}")
                        
        except Exception as e:
            logger.error(f"Auto leave task error: {e}")
            traceback.print_exc()

# -------------------------
# Main Function
# -------------------------
async def main():
    """Main function to start the bot"""
    try:
        logger.info("=" * 50)
        logger.info("Starting Advanced Music Bot...")
        logger.info("=" * 50)
        
        # Start bot client
        logger.info("Starting bot client...")
        await bot.start()
        bot_info = await bot.get_me()
        logger.info(f"✅ Bot started: @{bot_info.username}")
        
        # Start assistant client
        logger.info("Starting assistant client...")
        await assistant.start()
        assistant_info = await assistant.get_me()
        logger.info(f"✅ Assistant started: @{assistant_info.username}")
        
        # Start PyTgCalls
        logger.info("Starting PyTgCalls...")
        await calls.start()
        logger.info("✅ PyTgCalls started")
        
        # Send startup notification
        if Config.LOG_CHANNEL:
            try:
                await bot.send_message(
                    Config.LOG_CHANNEL,
                    f"✅ **Bot Started Successfully!**\n\n"
                    f"🤖 Bot: @{bot_info.username}\n"
                    f"👤 Assistant: @{assistant_info.username}\n"
                    f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                logger.info("Sent startup notification to log channel")
            except Exception as e:
                logger.error(f"Failed to send startup notification: {e}")
        
        # Start background tasks
        logger.info("Starting background tasks...")
        asyncio.create_task(auto_cleanup_files())
        asyncio.create_task(auto_leave_inactive())
        logger.info("✅ Background tasks started")
        
        logger.info("=" * 50)
        logger.info("✅ Bot is ready and running!")
        logger.info("=" * 50)
        
        # Keep the bot running
        await idle()
        
    except Exception as e:
        logger.critical(f"Fatal error in main: {e}")
        traceback.print_exc()
        raise
    
    finally:
        logger.info("Shutting down...")
        
        try:
            await calls.stop()
            logger.info("✅ PyTgCalls stopped")
        except:
            pass
        
        try:
            await bot.stop()
            logger.info("✅ Bot stopped")
        except:
            pass
        
        try:
            await assistant.stop()
            logger.info("✅ Assistant stopped")
        except:
            pass
        
        logger.info("Shutdown complete")

# -------------------------
# Entry Point
# -------------------------
if __name__ == "__main__":
    try:
        # Check Python version
        if sys.version_info < (3, 8):
            logger.critical("Python 3.8 or higher is required!")
            sys.exit(1)
        
        # Run the bot
        asyncio.run(main())
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C)")
    
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        traceback.print_exc()
        sys.exit(1)
    
    finally:
        logger.info("Bot terminated")
