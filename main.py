#!/usr/bin/env python3
"""
Advanced Telegram Music Bot - PyTgCalls Stable Compatible
Works with py-tgcalls stable version
"""

import os
import sys
import asyncio
import time
import random
import traceback
import logging
from typing import Dict, List, Optional, Union
from datetime import datetime, timedelta
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

# Pyrogram
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import RPCError, FloodWait, UserAlreadyParticipant, ChatAdminRequired
from pyrogram.enums import ChatMemberStatus, ParseMode

# PyTgCalls - Compatible imports
try:
    from pytgcalls import PyTgCalls
    from pytgcalls.types import Update
    from pytgcalls.types.input_stream import AudioPiped, AudioVideoPiped
    from pytgcalls.types.input_stream.quality import HighQualityAudio, MediumQualityAudio
except ImportError as e:
    print("ERROR: PyTgCalls import failed!")
    print("Install with: pip install py-tgcalls")
    sys.exit(1)

# Utils
try:
    import yt_dlp
    import psutil
    from youtubesearchpython import VideosSearch
except ImportError as e:
    print(f"ERROR: Missing dependency - {e}")
    print("Install: pip install yt-dlp psutil youtube-search-python")
    sys.exit(1)

# -------------------------
# Configure Logging
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# -------------------------
# Config with Validation
# -------------------------
class Config:
    API_ID = int(os.getenv("API_ID", "0"))
    API_HASH = os.getenv("API_HASH", "")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    ASSISTANT_SESSION = os.getenv("ASSISTANT_SESSION", "")
    LOG_CHANNEL = os.getenv("LOG_CHANNEL", "")
    SUDO_USERS = [int(x) for x in os.getenv("SUDO_USERS", "").split(",") if x.strip().isdigit()]
    
    MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "100"))
    MAX_DURATION = int(os.getenv("MAX_DURATION", "3600"))  # 1 hour
    DOWNLOAD_DIR = "downloads"
    COOLDOWN_TIME = 2
    AUTO_LEAVE_DURATION = 300  # 5 minutes
    
    @classmethod
    def validate(cls):
        if not all([cls.API_ID, cls.API_HASH, cls.BOT_TOKEN, cls.ASSISTANT_SESSION]):
            raise ValueError("❌ Missing required environment variables: API_ID, API_HASH, BOT_TOKEN, ASSISTANT_SESSION")
        logger.info("✅ Configuration validated")

try:
    Config.validate()
except Exception as e:
    logger.critical(f"Configuration error: {e}")
    sys.exit(1)

os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)

# -------------------------
# Enums
# -------------------------
class PlaybackState(Enum):
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"

class LoopMode(Enum):
    OFF = 0
    SINGLE = 1
    QUEUE = 2

# -------------------------
# Data Classes
# -------------------------
@dataclass
class Song:
    title: str
    url: str
    duration: int
    thumbnail: str
    video_id: str
    requested_by: str
    requested_by_id: int
    file_path: Optional[str] = None
    added_at: datetime = field(default_factory=datetime.now)

@dataclass
class QueueManager:
    queue: List[Song] = field(default_factory=list)
    loop_mode: LoopMode = LoopMode.OFF
    current: Optional[Song] = None
    state: PlaybackState = PlaybackState.STOPPED
    
    def add(self, song: Song) -> int:
        self.queue.append(song)
        return len(self.queue)
    
    def remove(self, index: int) -> Optional[Song]:
        if 0 <= index < len(self.queue):
            return self.queue.pop(index)
        return None
    
    def get_next(self) -> Optional[Song]:
        if self.loop_mode == LoopMode.SINGLE and self.current:
            return self.current
        elif self.loop_mode == LoopMode.QUEUE and self.current:
            self.queue.append(self.current)
        
        if self.queue:
            return self.queue.pop(0)
        return None
    
    def clear(self):
        self.queue.clear()
        self.current = None
        self.state = PlaybackState.STOPPED
    
    def shuffle(self):
        random.shuffle(self.queue)

# -------------------------
# Global State
# -------------------------
START_TIME = datetime.now()
queues: Dict[int, QueueManager] = defaultdict(QueueManager)
command_cooldown: Dict[int, float] = {}
active_calls: Dict[int, datetime] = {}
download_cache: Dict[str, str] = {}

# -------------------------
# Clients
# -------------------------
bot = Client(
    "MusicBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    in_memory=True,
    parse_mode=ParseMode.MARKDOWN
)

assistant = Client(
    "Assistant",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    session_string=Config.ASSISTANT_SESSION,
    in_memory=True
)

calls = PyTgCalls(assistant)

# -------------------------
# YouTube Handler
# -------------------------
class YouTubeHandler:
    @staticmethod
    def get_yt_config():
        return {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': f'{Config.DOWNLOAD_DIR}/%(id)s.%(ext)s',
            'geo_bypass': True,
            'nocheckcertificate': True,
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '192',
            }],
        }
    
    @staticmethod
    async def search(query: str, limit: int = 1) -> Optional[List[Dict]]:
        try:
            if query.startswith(("http://", "https://")):
                return await YouTubeHandler._extract_info(query)
            
            # Use youtubesearchpython
            loop = asyncio.get_event_loop()
            search = await loop.run_in_executor(None, lambda: VideosSearch(query, limit=limit).result())
            
            if not search or not search.get('result'):
                return None
            
            return [{
                'title': v['title'],
                'url': v['link'],
                'duration': YouTubeHandler._parse_duration(v.get('duration', '0:00')),
                'thumbnail': v['thumbnails'][0]['url'] if v.get('thumbnails') else '',
                'id': v['id']
            } for v in search['result']]
        except Exception as e:
            logger.error(f"Search error: {e}")
            traceback.print_exc()
            return None
    
    @staticmethod
    async def _extract_info(url: str) -> Optional[List[Dict]]:
        try:
            ydl_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}
            loop = asyncio.get_event_loop()
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
                
                if not info:
                    return None
                
                if 'entries' in info:
                    return [{
                        'title': e.get('title', 'Unknown'),
                        'url': e.get('webpage_url', e.get('url', '')),
                        'duration': int(e.get('duration', 0)),
                        'thumbnail': e.get('thumbnail', ''),
                        'id': e.get('id', '')
                    } for e in info['entries'][:50] if e]
                else:
                    return [{
                        'title': info.get('title', 'Unknown'),
                        'url': info.get('webpage_url', info.get('url', '')),
                        'duration': int(info.get('duration', 0)),
                        'thumbnail': info.get('thumbnail', ''),
                        'id': info.get('id', '')
                    }]
        except Exception as e:
            logger.error(f"Extract info error: {e}")
            traceback.print_exc()
            return None
    
    @staticmethod
    def _parse_duration(duration_str: str) -> int:
        try:
            parts = duration_str.split(':')
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except:
            pass
        return 0
    
    @staticmethod
    async def download(url: str, video_id: str) -> Optional[str]:
        # Check cache
        if video_id in download_cache and os.path.exists(download_cache[video_id]):
            logger.info(f"Using cached file: {download_cache[video_id]}")
            return download_cache[video_id]
        
        try:
            ydl_opts = YouTubeHandler.get_yt_config()
            loop = asyncio.get_event_loop()
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
                vid_id = info.get('id', video_id)
                
                for ext in ['m4a', 'mp3', 'webm', 'opus']:
                    path = f"{Config.DOWNLOAD_DIR}/{vid_id}.{ext}"
                    if os.path.exists(path):
                        download_cache[video_id] = path
                        logger.info(f"Downloaded: {path}")
                        return path
            return None
        except Exception as e:
            logger.error(f"Download error: {e}")
            traceback.print_exc()
            return None

# -------------------------
# Playback Manager
# -------------------------
class PlaybackManager:
    @staticmethod
    async def start_playback(chat_id: int, file_path: str):
        """Start playback using AudioPiped"""
        try:
            # Use high quality audio
            try:
                audio_quality = HighQualityAudio()
            except:
                audio_quality = MediumQualityAudio()
            
            stream = AudioPiped(file_path, audio_quality)
            
            # Check if already in call
            try:
                await calls.get_call(chat_id)
                # Already in call, change stream
                await calls.change_stream(chat_id, stream)
                logger.info(f"Changed stream in {chat_id}")
            except:
                # Not in call, join
                await calls.join_group_call(chat_id, stream)
                logger.info(f"Joined and started playback in {chat_id}")
            
            active_calls[chat_id] = datetime.now()
            
        except Exception as e:
            logger.error(f"Playback error in {chat_id}: {e}")
            traceback.print_exc()
            raise
    
    @staticmethod
    async def pause(chat_id: int):
        try:
            await calls.pause_stream(chat_id)
            queues[chat_id].state = PlaybackState.PAUSED
            logger.info(f"Paused in {chat_id}")
        except Exception as e:
            logger.error(f"Pause error: {e}")
            raise
    
    @staticmethod
    async def resume(chat_id: int):
        try:
            await calls.resume_stream(chat_id)
            queues[chat_id].state = PlaybackState.PLAYING
            logger.info(f"Resumed in {chat_id}")
        except Exception as e:
            logger.error(f"Resume error: {e}")
            raise
    
    @staticmethod
    async def stop(chat_id: int):
        try:
            await calls.leave_group_call(chat_id)
            active_calls.pop(chat_id, None)
            queues[chat_id].clear()
            logger.info(f"Stopped and left {chat_id}")
        except Exception as e:
            logger.error(f"Stop error: {e}")

# -------------------------
# Queue Processor
# -------------------------
async def process_queue(chat_id: int):
    """Process next song in queue"""
    try:
        qm = queues[chat_id]
        song = qm.get_next()
        
        if not song:
            qm.state = PlaybackState.STOPPED
            await PlaybackManager.stop(chat_id)
            try:
                await bot.send_message(chat_id, "✅ **Queue finished! Leaving voice chat.**")
            except:
                pass
            return
        
        # Download if needed
        if not song.file_path or not os.path.exists(song.file_path):
            song.file_path = await YouTubeHandler.download(song.url, song.video_id)
        
        if not song.file_path:
            try:
                await bot.send_message(chat_id, f"❌ **Failed to download:** {song.title}")
            except:
                pass
            await process_queue(chat_id)
            return
        
        # Start playback
        await PlaybackManager.start_playback(chat_id, song.file_path)
        qm.current = song
        qm.state = PlaybackState.PLAYING
        
        # Send now playing
        text = (
            f"🎵 **Now Playing**\n\n"
            f"**{song.title}**\n"
            f"⏱ Duration: `{format_duration(song.duration)}`\n"
            f"👤 Requested by: {song.requested_by}\n"
        )
        
        if qm.loop_mode == LoopMode.SINGLE:
            text += "🔁 **Loop:** Single Track\n"
        elif qm.loop_mode == LoopMode.QUEUE:
            text += "🔁 **Loop:** Queue\n"
        
        if qm.queue:
            text += f"📋 **Next:** {qm.queue[0].title}"
        
        try:
            await bot.send_message(chat_id, text, reply_markup=get_player_buttons(chat_id))
        except Exception as e:
            logger.error(f"Failed to send now playing: {e}")
            
    except Exception as e:
        logger.error(f"Queue process error in {chat_id}: {e}")
        traceback.print_exc()
        try:
            await bot.send_message(chat_id, f"❌ **Playback error:** {str(e)}")
        except:
            pass

# -------------------------
# Event Handlers
# -------------------------
@calls.on_stream_end()
async def on_stream_end(client, update: Update):
    """Handle stream end"""
    try:
        chat_id = update.chat_id
        logger.info(f"Stream ended in {chat_id}")
        await asyncio.sleep(0.5)
        await process_queue(chat_id)
    except Exception as e:
        logger.error(f"Stream end handler error: {e}")
        traceback.print_exc()

@calls.on_kicked()
async def on_kicked(client, chat_id: int):
    """Handle when kicked"""
    logger.warning(f"Kicked from {chat_id}")
    queues[chat_id].clear()
    active_calls.pop(chat_id, None)

@calls.on_closed_voice_chat()
async def on_closed_vc(client, chat_id: int):
    """Handle VC closed"""
    logger.info(f"Voice chat closed in {chat_id}")
    queues[chat_id].clear()
    active_calls.pop(chat_id, None)

# -------------------------
# Helper Functions
# -------------------------
def format_duration(seconds: int) -> str:
    if seconds < 3600:
        return time.strftime("%M:%S", time.gmtime(seconds))
    return time.strftime("%H:%M:%S", time.gmtime(seconds))

def get_queue_text(chat_id: int) -> str:
    qm = queues[chat_id]
    if not qm.queue:
        return "📭 **Queue is empty**"
    
    text = "📋 **Current Queue:**\n\n"
    total_duration = sum(s.duration for s in qm.queue)
    
    for i, song in enumerate(qm.queue[:10], 1):
        text += f"`{i}.` **{song.title}**\n"
        text += f"   ⏱ `{format_duration(song.duration)}` | 👤 {song.requested_by}\n\n"
    
    if len(qm.queue) > 10:
        text += f"\n*...and {len(qm.queue) - 10} more*\n"
    
    text += f"\n⏱ **Total:** `{format_duration(total_duration)}`"
    return text

def get_player_buttons(chat_id: int):
    qm = queues[chat_id]
    is_playing = qm.state == PlaybackState.PLAYING
    
    buttons = [
        [
            InlineKeyboardButton(
                "⏸ Pause" if is_playing else "▶️ Resume",
                callback_data=f"{'pause' if is_playing else 'resume'}_{chat_id}"
            ),
            InlineKeyboardButton("⏭ Skip", callback_data=f"skip_{chat_id}"),
            InlineKeyboardButton("⏹ Stop", callback_data=f"stop_{chat_id}")
        ],
        [
            InlineKeyboardButton(
                f"🔁 Loop: {qm.loop_mode.name}",
                callback_data=f"loop_{chat_id}"
            ),
            InlineKeyboardButton("🔀 Shuffle", callback_data=f"shuffle_{chat_id}"),
        ],
        [
            InlineKeyboardButton("📋 Queue", callback_data=f"queue_{chat_id}"),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{chat_id}"),
            InlineKeyboardButton("❌ Close", callback_data=f"close_{chat_id}")
        ]
    ]
    return InlineKeyboardMarkup(buttons)

async def is_admin(client: Client, chat_id: int, user_id: int) -> bool:
    if user_id in Config.SUDO_USERS:
        return True
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    except:
        return False

def check_cooldown(chat_id: int) -> bool:
    if chat_id in command_cooldown:
        if (time.time() - command_cooldown[chat_id]) < Config.COOLDOWN_TIME:
            return False
    command_cooldown[chat_id] = time.time()
    return True

async def join_voice_chat(chat_id: int):
    """Ensure assistant is in chat"""
    try:
        # Check if assistant is in chat
        try:
            await assistant.get_chat_member(chat_id, "me")
        except:
            # Join chat
            try:
                chat = await bot.get_chat(chat_id)
                if chat.username:
                    await assistant.join_chat(chat.username)
                else:
                    invite_link = await bot.export_chat_invite_link(chat_id)
                    await assistant.join_chat(invite_link)
                await asyncio.sleep(1)
            except Exception as e:
                raise Exception(f"Cannot join chat: {str(e)}")
        
        return True
    except Exception as e:
        logger.error(f"Join chat error: {e}")
        raise

# -------------------------
# Bot Commands
# -------------------------
@bot.on_message(filters.command("start") & filters.private)
async def start_command(_, message: Message):
    me = await bot.get_me()
    text = (
        f"👋 **Welcome to {me.first_name}!**\n\n"
        "🎵 Advanced Music Bot\n\n"
        "**Commands:**\n"
        "• `/play <song/URL>` - Play music\n"
        "• `/pause` - Pause playback\n"
        "• `/resume` - Resume\n"
        "• `/skip` - Skip song\n"
        "• `/stop` - Stop & clear\n"
        "• `/queue` - Show queue\n"
        "• `/nowplaying` - Current song\n"
        "• `/loop` - Toggle loop\n"
        "• `/shuffle` - Shuffle queue\n"
        "• `/stats` - Bot stats\n\n"
        "Add me to your group! 🎶"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{me.username}?startgroup=true")],
        [InlineKeyboardButton("📚 Help", callback_data="help")]
    ])
    await message.reply_text(text, reply_markup=buttons)

@bot.on_message(filters.command("play") & filters.group)
async def play_command(_, message: Message):
    chat_id = message.chat.id
    
    if not check_cooldown(chat_id):
        return
    
    if len(message.command) < 2:
        await message.reply_text("❌ **Usage:** `/play <song name or URL>`")
        return
    
    query = message.text.split(None, 1)[1]
    msg = await message.reply_text("🔍 **Searching...**")
    
    try:
        results = await YouTubeHandler.search(query)
        if not results:
            await msg.edit("❌ **No results found.**")
            return
        
        try:
            await join_voice_chat(chat_id)
        except Exception as e:
            await msg.edit(f"❌ **Cannot join voice chat:** {str(e)}")
            return
        
        qm = queues[chat_id]
        
        if len(qm.queue) >= Config.MAX_QUEUE_SIZE:
            await msg.edit(f"❌ **Queue is full!** (Max: {Config.MAX_QUEUE_SIZE})")
            return
        
        added_songs = []
        for info in results[:20]:
            if info['duration'] > Config.MAX_DURATION:
                continue
            
            song = Song(
                title=info['title'],
                url=info['url'],
                duration=info['duration'],
                thumbnail=info['thumbnail'],
                video_id=info['id'],
                requested_by=message.from_user.mention,
                requested_by_id=message.from_user.id
            )
            
            position = qm.add(song)
            added_songs.append((song, position))
        
        if not added_songs:
            await msg.edit("❌ **No valid songs found.**")
            return
        
        if qm.state == PlaybackState.STOPPED:
            await msg.edit("⏳ **Loading...**")
            await process_queue(chat_id)
            try:
                await msg.delete()
            except:
                pass
        else:
            if len(added_songs) == 1:
                song, pos = added_songs[0]
                text = f"✅ **Added at #{pos}**\n\n**{song.title}**\n⏱ `{format_duration(song.duration)}`"
            else:
                text = f"✅ **Added {len(added_songs)} songs**"
            await msg.edit(text)
    
    except Exception as e:
        logger.error(f"Play command error: {e}")
        traceback.print_exc()
        await msg.edit(f"❌ **Error:** {str(e)}")

@bot.on_message(filters.command("pause") & filters.group)
async def pause_command(_, message: Message):
    if not await is_admin(bot, message.chat.id, message.from_user.id):
        await message.reply_text("❌ **Admin only!**")
        return
    try:
        await PlaybackManager.pause(message.chat.id)
        await message.reply_text("⏸ **Paused**")
    except Exception as e:
        await message.reply_text(f"❌ {str(e)}")

@bot.on_message(filters.command("resume") & filters.group)
async def resume_command(_, message: Message):
    if not await is_admin(bot, message.chat.id, message.from_user.id):
        await message.reply_text("❌ **Admin only!**")
        return
    try:
        await PlaybackManager.resume(message.chat.id)
        await message.reply_text("▶️ **Resumed**")
    except Exception as e:
        await message.reply_text(f"❌ {str(e)}")

@bot.on_message(filters.command("skip") & filters.group)
async def skip_command(_, message: Message):
    if not await is_admin(bot, message.chat.id, message.from_user.id):
        await message.reply_text("❌ **Admin only!**")
        return
    qm = queues[message.chat.id]
    if not qm.current:
        await message.reply_text("❌ **Nothing playing!**")
        return
    await message.reply_text("⏭ **Skipped**")
    await process_queue(message.chat.id)

@bot.on_message(filters.command("stop") & filters.group)
async def stop_command(_, message: Message):
    if not await is_admin(bot, message.chat.id, message.from_user.id):
        await message.reply_text("❌ **Admin only!**")
        return
    await PlaybackManager.stop(message.chat.id)
    await message.reply_text("⏹ **Stopped**")

@bot.on_message(filters.command("queue") & filters.group)
async def queue_command(_, message: Message):
    qm = queues[message.chat.id]
    text = get_queue_text(message.chat.id)
    if qm.current:
        text = f"🎵 **Now:** {qm.current.title}\n\n{text}"
    await message.reply_text(text)

@bot.on_message(filters.command("nowplaying") & filters.group)
async def nowplaying_command(_, message: Message):
    qm = queues[message.chat.id]
    if not qm.current:
        await message.reply_text("❌ **Nothing playing!**")
        return
    song = qm.current
    text = (
        f"🎵 **Now Playing:**\n\n**{song.title}**\n"
        f"⏱ `{format_duration(song.duration)}`\n"
        f"👤 {song.requested_by}\n"
        f"🔁 Loop: {qm.loop_mode.name}"
    )
    await message.reply_text(text, reply_markup=get_player_buttons(message.chat.id))

@bot.on_message(filters.command("loop") & filters.group)
async def loop_command(_, message: Message):
    if not await is_admin(bot, message.chat.id, message.from_user.id):
        await message.reply_text("❌ **Admin only!**")
        return
    qm = queues[message.chat.id]
    if qm.loop_mode == LoopMode.OFF:
        qm.loop_mode = LoopMode.SINGLE
        text = "🔁 **Loop:** Single"
    elif qm.loop_mode == LoopMode.SINGLE:
        qm.loop_mode = LoopMode.QUEUE
        text = "🔁 **Loop:** Queue"
    else:
        qm.loop_mode = LoopMode.OFF
        text = "🔁 **Loop:** Off"
    await message.reply_text(text)

@bot.on_message(filters.command("shuffle") & filters.group)
async def shuffle_command(_, message: Message):
    if not await is_admin(bot, message.chat.id, message.from_user.id):
        await message.reply_text("❌ **Admin only!**")
        return
    qm = queues[message.chat.id]
    if not qm.queue:
        await message.reply_text("❌ **Queue empty!**")
        return
    qm.shuffle()
    await message.reply_text("🔀 **Shuffled**")

@bot.on_message(filters.command("stats"))
async def stats_command(_, message: Message):
    uptime = datetime.now() - START_TIME
    cpu = psutil.cpu_percent()
    memory = psutil.virtual_memory().percent
    active = len([c for c in active_calls.keys()])
    total = sum(len(q.queue) for q in queues.values())
    text = (
        f"📊 **Stats**\n\n"
        f"⏰ Uptime: `{str(uptime).split('.')[0]}`\n"
        f"🎵 Active: `{active}`\n"
        f"📋 Queued: `{total}`\n"
        f"🖥 CPU: `{cpu}%`\n"
        f"💾 RAM: `{memory}%`"
    )
    await message.reply_text(text)
    
@bot.on_message(filters.command("ping"))
async def ping_command(_, message: Message):
    start = time.time()

    # Temporary message to calculate ping
    msg = await message.reply_text("⏳ Checking bot status...")

    end = time.time()
    ping = round((end - start) * 1000, 2)

    # Example stats (apne variables yaha plug karo)
    uptime = time.time() - START_TIME
    active_chats = len(active_chats_db)
    total_songs = sum(len(v) for v in queues.values())
    cpu = psutil.cpu_percent()
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent

    text = (
        f"📊 **Bot Statistics**\n\n"
        f"🏓 **Ping:** `{ping} ms`\n"
        f"⏰ **Uptime:** `{str(uptime).split('.')[0]}`\n"
        f"🎵 **Active Chats:** `{active_chats}`\n"
        f"📋 **Total Queued:** `{total_songs} songs`\n"
        f"💾 **Cache Size:** `{len(download_cache)}/{Config.CACHE_SIZE}`\n\n"
        f"**System:**\n"
        f"🖥 CPU: `{cpu}%`\n"
        f"💾 RAM: `{memory}%`\n"
        f"💿 Disk: `{disk}%`"
    )

    await msg.edit_text(text)



@bot.on_message(filters.command("ping"))
async def ping_command(_, message: Message):
    start = time.time()
    msg = await message.reply_text("🏓 **Pinging...**")
    latency = (time.time() - start) * 1000
    await msg.edit(f"🏓 **Pong!**\n⚡️ `{latency:.2f} ms`")

@bot.on_message(filters.command("clean") & filters.group)
async def clean_command(_, message: Message):
    if not await is_admin(bot, message.chat.id, message.from_user.id):
        await message.reply_text("❌ **Admin only!**")
        return
    qm = queues[message.chat.id]
    if not qm.queue:
        await message.reply_text("❌ **Queue already empty!**")
        return
    count = len(qm.queue)
    qm.queue.clear()
    await message.reply_text(f"🧹 **Cleared {count} songs**")

@bot.on_message(filters.command("remove") & filters.group)
async def remove_command(_, message: Message):
    if not await is_admin(bot, message.chat.id, message.from_user.id):
        await message.reply_text("❌ **Admin only!**")
        return
    if len(message.command) < 2:
        await message.reply_text("❌ **Usage:** `/remove <position>`")
        return
    try:
        position = int(message.command[1]) - 1
    except ValueError:
        await message.reply_text("❌ **Invalid position!**")
        return
    qm = queues[message.chat.id]
    song = qm.remove(position)
    if song:
        await message.reply_text(f"✅ **Removed:** {song.title}")
    else:
        await message.reply_text("❌ **Invalid position!**")

@bot.on_message(filters.command("search"))
async def search_command(_, message: Message):
    if len(message.command) < 2:
        await message.reply_text("❌ **Usage:** `/search <query>`")
        return
    query = message.text.split(None, 1)[1]
    msg = await message.reply_text("🔍 **Searching...**")
    results = await YouTubeHandler.search(query, limit=5)
    if not results:
        await msg.edit("❌ **No results!**")
        return
    text = "🔎 **Search Results:**\n\n"
    buttons = []
    for i, info in enumerate(results, 1):
        text += f"{i}. **{info['title']}**\n   ⏱ `{format_duration(info['duration'])}`\n\n"
        buttons.append([InlineKeyboardButton(f"{i}. {info['title'][:30]}...", callback_data=f"play_{message.chat.id}_{info['id']}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="close_0")])
    await msg.edit(text, reply_markup=InlineKeyboardMarkup(buttons))

# -------------------------
# Callback Handler
# -------------------------
@bot.on_callback_query()
async def callback_handler(_, callback: CallbackQuery):
    data = callback.data
    if not data:
        return
    
    if data.startswith("close_"):
        try:
            await callback.message.delete()
        except:
            pass
        return
    
    if data == "help":
        text = (
            "📚 **Help**\n\n"
            "**Playback:**\n"
            "• `/play <query>` - Play\n"
            "• `/pause` - Pause\n"
            "• `/resume` - Resume\n"
            "• `/skip` - Skip\n"
            "• `/stop` - Stop\n\n"
            "**Queue:**\n"
            "• `/queue` - Show\n"
            "• `/clean` - Clear\n"
            "• `/remove <pos>` - Remove\n"
            "• `/shuffle` - Shuffle\n\n"
            "**Info:**\n"
            "• `/nowplaying` - Current\n"
            "• `/search <query>` - Search\n"
            "• `/stats` - Stats\n"
            "• `/ping` - Latency\n\n"
            "**Settings:**\n"
            "• `/loop` - Toggle loop"
        )
        await callback.message.edit_text(text)
        return
    
    try:
        action, rest = data.split("_", 1)
        chat_id = int(rest.split("_")[0])
    except:
        await callback.answer("❌ Invalid", show_alert=True)
        return
    
    if action not in ["queue", "refresh"]:
        if not await is_admin(bot, chat_id, callback.from_user.id):
            await callback.answer("❌ Admin only!", show_alert=True)
            return
    
    try:
        if action == "pause":
            await PlaybackManager.pause(chat_id)
            await callback.answer("⏸ Paused")
        elif action == "resume":
            await PlaybackManager.resume(chat_id)
            await callback.answer("▶️ Resumed")
        elif action == "skip":
            await callback.answer("⏭ Skipped")
            await process_queue(chat_id)
        elif action == "stop":
            await PlaybackManager.stop(chat_id)
            await callback.answer("⏹ Stopped")
            try:
                await callback.message.delete()
            except:
                pass
            return
        elif action == "loop":
            qm = queues[chat_id]
            if qm.loop_mode == LoopMode.OFF:
                qm.loop_mode = LoopMode.SINGLE
                text = "Single"
            elif qm.loop_mode == LoopMode.SINGLE:
                qm.loop_mode = LoopMode.QUEUE
                text = "Queue"
            else:
                qm.loop_mode = LoopMode.OFF
                text = "Off"
            await callback.answer(f"🔁 {text}")
        elif action == "shuffle":
            qm = queues[chat_id]
            if qm.queue:
                qm.shuffle()
                await callback.answer("🔀 Shuffled")
            else:
                await callback.answer("❌ Empty", show_alert=True)
                return
        elif action == "queue":
            text = get_queue_text(chat_id)
            qm = queues[chat_id]
            if qm.current:
                text = f"🎵 **Now:** {qm.current.title}\n\n{text}"
            await callback.answer()
            await callback.message.reply_text(text)
            return
        elif action == "play":
            video_id = rest.split("_")[1]
            info = await YouTubeHandler._extract_info(f"https://youtube.com/watch?v={video_id}")
            if info and info[0]:
                song_info = info[0]
                song = Song(
                    title=song_info['title'],
                    url=song_info['url'],
                    duration=song_info['duration'],
                    thumbnail=song_info['thumbnail'],
                    video_id=song_info['id'],
                    requested_by=callback.from_user.mention,
                    requested_by_id=callback.from_user.id
                )
                qm = queues[chat_id]
                position = qm.add(song)
                if qm.state == PlaybackState.STOPPED:
                    await join_voice_chat(chat_id)
                    await process_queue(chat_id)
                    await callback.answer("▶️ Playing")
                else:
                    await callback.answer(f"✅ Added at #{position}")
                try:
                    await callback.message.delete()
                except:
                    pass
            return
        
        # Update UI
        qm = queues[chat_id]
        if qm.current:
            text = f"🎵 **Now:**\n**{qm.current.title}**\n⏱ `{format_duration(qm.current.duration)}`\n🔁 {qm.loop_mode.name}"
            try:
                await callback.message.edit_text(text, reply_markup=get_player_buttons(chat_id))
            except:
                pass
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await callback.answer(f"❌ {str(e)}", show_alert=True)

# -------------------------
# Background Tasks
# -------------------------
async def auto_cleanup():
    """Clean old files"""
    while True:
        try:
            await asyncio.sleep(1800)
            current_time = time.time()
            for filename in os.listdir(Config.DOWNLOAD_DIR):
                filepath = os.path.join(Config.DOWNLOAD_DIR, filename)
                if os.path.isfile(filepath):
                    file_age = current_time - os.path.getmtime(filepath)
                    video_id = os.path.splitext(filename)[0]
                    if file_age > 3600 and video_id not in download_cache:
                        try:
                            os.remove(filepath)
                            logger.info(f"Cleaned: {filename}")
                        except Exception as e:
                            logger.error(f"Cleanup error: {e}")
        except Exception as e:
            logger.error(f"Auto cleanup error: {e}")

async def auto_leave_inactive():
    """Leave inactive chats"""
    while True:
        try:
            await asyncio.sleep(60)
            current_time = datetime.now()
            inactive = []
            for chat_id, join_time in active_calls.items():
                qm = queues[chat_id]
                if qm.state == PlaybackState.STOPPED:
                    if (current_time - join_time).seconds > Config.AUTO_LEAVE_DURATION:
                        inactive.append(chat_id)
            for chat_id in inactive:
                try:
                    await PlaybackManager.stop(chat_id)
                    await bot.send_message(chat_id, "👋 **Left due to inactivity**")
                    logger.info(f"Auto-left {chat_id}")
                except Exception as e:
                    logger.error(f"Auto leave error: {e}")
        except Exception as e:
            logger.error(f"Auto leave task error: {e}")

async def log_activity():
    """Log to channel"""
    if not Config.LOG_CHANNEL:
        return
    while True:
        try:
            await asyncio.sleep(3600)
            active = len([c for c in active_calls.keys()])
            total = sum(len(q.queue) for q in queues.values())
            uptime = datetime.now() - START_TIME
            text = f"📊 **Hourly Report**\n\n⏰ `{str(uptime).split('.')[0]}`\n🎵 Active: `{active}`\n📋 Queued: `{total}`"
            await bot.send_message(Config.LOG_CHANNEL, text)
        except Exception as e:
            logger.error(f"Log activity error: {e}")

# -------------------------
# Sudo Commands
# -------------------------
@bot.on_message(filters.command("restart") & filters.user(Config.SUDO_USERS))
async def restart_command(_, message: Message):
    await message.reply_text("🔄 **Restarting...**")
    os.execl(sys.executable, sys.executable, *sys.argv)

@bot.on_message(filters.command("broadcast") & filters.user(Config.SUDO_USERS))
async def broadcast_command(_, message: Message):
    if len(message.command) < 2:
        await message.reply_text("❌ **Usage:** `/broadcast <message>`")
        return
    text = message.text.split(None, 1)[1]
    sent = 0
    failed = 0
    msg = await message.reply_text("📢 **Broadcasting...**")
    for chat_id in list(active_calls.keys()):
        try:
            await bot.send_message(chat_id, f"📢 **Announcement:**\n\n{text}")
            sent += 1
            await asyncio.sleep(0.5)
        except:
            failed += 1
    await msg.edit(f"✅ **Done**\n\nSent: `{sent}`\nFailed: `{failed}`")

@bot.on_message(filters.command("logs") & filters.user(Config.SUDO_USERS))
async def logs_command(_, message: Message):
    try:
        if os.path.exists('bot.log'):
            await message.reply_document('bot.log', caption="📄 **Bot Logs**")
        else:
            await message.reply_text("❌ **No logs found**")
    except Exception as e:
        await message.reply_text(f"❌ **Error:** {str(e)}")

@bot.on_message(filters.command("clearall") & filters.user(Config.SUDO_USERS))
async def clearall_command(_, message: Message):
    """Clear all queues (emergency)"""
    count = len(active_calls)
    for chat_id in list(active_calls.keys()):
        try:
            await PlaybackManager.stop(chat_id)
        except:
            pass
    await message.reply_text(f"✅ **Cleared {count} chats**")

# -------------------------
# Main
# -------------------------
async def main():
    """Startup"""
    try:
        logger.info("Starting bot...")
        
        await bot.start()
        logger.info("✅ Bot started")
        
        await assistant.start()
        logger.info("✅ Assistant started")
        
        await calls.start()
        logger.info("✅ PyTgCalls started")
        
        bot_info = await bot.get_me()
        assistant_info = await assistant.get_me()
        
        logger.info(f"Bot: @{bot_info.username}")
        logger.info(f"Assistant: @{assistant_info.username}")
        
        if Config.LOG_CHANNEL:
            try:
                await bot.send_message(
                    Config.LOG_CHANNEL,
                    f"✅ **Bot Started**\n\n"
                    f"🤖 @{bot_info.username}\n"
                    f"👤 @{assistant_info.username}\n"
                    f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
            except Exception as e:
                logger.error(f"Failed to send startup message: {e}")
        
        # Background tasks
        asyncio.create_task(auto_cleanup())
        asyncio.create_task(auto_leave_inactive())
        asyncio.create_task(log_activity())
        
        logger.info("✅ All systems ready!")
        logger.info("Press Ctrl+C to stop")
        
        await idle()
        
    except Exception as e:
        logger.error(f"Startup error: {e}")
        traceback.print_exc()
    finally:
        try:
            await calls.stop()
            await bot.stop()
            await assistant.stop()
            logger.info("Bot stopped")
        except:
            pass

# -------------------------
# Entry Point
# -------------------------
if __name__ == "__main__":
    try:
        if sys.version_info < (3, 8):
            print("❌ Python 3.8+ required!")
            sys.exit(1)
        
        logger.info("="*50)
        logger.info("Advanced Telegram Music Bot")
        logger.info("="*50)
        
        asyncio.run(main())
    
    except KeyboardInterrupt:
    logger.info("Stopped by user")

except Exception as e:
    logger.critical(f"Fatal error: {e}")
    traceback.print_exc()

finally:
    logger.info("Bot terminated")

@bot.on_message(filters.command("clean") & filters.group)
async def clean_command(_, message: Message):
    if not await is_admin(bot, message.chat.id, message.from_user.id):
        await message.reply_text("❌ **Admin only!**")
        return
    
    qm = queues[message.chat.id]
    if not qm.queue:
        await message.reply_text("❌ **Queue is already empty!**")
        return
    
    count = len(qm.queue)
    qm.queue.clear()
    await message.reply_text(f"🧹 **Cleared {count} songs from queue**")

@bot.on_message(filters.command("remove") & filters.group)
async def remove_command(_, message: Message):
    if not await is_admin(bot, message.chat.id, message.from_user.id):
        await message.reply_text("❌ **Admin only!**")
        return
    
    if len(message.command) < 2:
        await message.reply_text("❌ **Usage:** `/remove <position>`")
        return
    
    try:
        position = int(message.command[1]) - 1
    except ValueError:
        await message.reply_text("❌ **Invalid position number!**")
        return
    
    qm = queues[message.chat.id]
    song = qm.remove(position)
    
    if song:
        await message.reply_text(f"✅ **Removed:** {song.title}")
    else:
        await message.reply_text("❌ **Invalid position!**")

@bot.on_message(filters.command("search"))
async def search_command(_, message: Message):
    if len(message.command) < 2:
        await message.reply_text("❌ **Usage:** `/search <query>`")
        return
    
    query = message.text.split(None, 1)[1]
    msg = await message.reply_text("🔍 **Searching...**")
    
    results = await YouTubeHandler.search(query, limit=5)
    
    if not results:
        await msg.edit("❌ **No results found!**")
        return
    
    text = "🔎 **Search Results:**\n\n"
    buttons = []
    
    for i, info in enumerate(results, 1):
        text += (
            f"{i}. **{info['title']}**\n"
            f"   ⏱ `{format_duration(info['duration'])}`\n\n"
        )
        buttons.append([
            InlineKeyboardButton(
                f"{i}. {info['title'][:30]}...",
                callback_data=f"play_{message.chat.id}_{info['id']}"
            )
        ])
    
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="close_0")])
    
    await msg.edit(text, reply_markup=InlineKeyboardMarkup(buttons))

# -------------------------
# Callback Query Handler
# -------------------------
@bot.on_callback_query()
async def callback_handler(_, callback: CallbackQuery):
    data = callback.data
    
    if not data:
        return
    
    if data.startswith("close_"):
        try:
            await callback.message.delete()
        except:
            pass
        return
    
    if data == "help":
        text = (
            "📚 **Help & Commands**\n\n"
            "**Playback:**\n"
            "• `/play <query>` - Play a song\n"
            "• `/pause` - Pause current song\n"
            "• `/resume` - Resume playback\n"
            "• `/skip` - Skip to next song\n"
            "• `/stop` - Stop and leave\n\n"
            "**Queue:**\n"
            "• `/queue` - Show current queue\n"
            "• `/clean` - Clear queue\n"
            "• `/remove <pos>` - Remove song\n"
            "• `/shuffle` - Shuffle queue\n\n"
            "**Info:**\n"
            "• `/nowplaying` - Current song\n"
            "• `/search <query>` - Search songs\n"
            "• `/stats` - Bot statistics\n"
            "• `/ping` - Check latency\n\n"
            "**Settings:**\n"
            "• `/loop` - Cycle loop modes\n"
        )
        await callback.message.edit_text(text)
        return
    
    if data == "about":
        text = (
            "ℹ️ **About Music Bot**\n\n"
            "🎵 Advanced Telegram Music Bot\n"
            "🔧 Built with PyTgCalls v3+\n"
            "🎶 High-quality audio streaming\n"
            "⚡️ Fast and reliable\n\n"
            "Powered by MediaStream API"
        )
        await callback.message.edit_text(text)
        return
    
    try:
        action, rest = data.split("_", 1)
        chat_id = int(rest.split("_")[0])
    except:
        await callback.answer("❌ **Invalid callback data**", show_alert=True)
        return
    
    # Check admin permissions
    if action not in ["queue", "refresh"]:
        if not await is_admin(bot, chat_id, callback.from_user.id):
            await callback.answer("❌ **Admin only!**", show_alert=True)
            return
    
    try:
        if action == "pause":
            await PlaybackManager.pause(chat_id)
            await callback.answer("⏸ **Paused**")
            
        elif action == "resume":
            await PlaybackManager.resume(chat_id)
            await callback.answer("▶️ **Resumed**")
            
        elif action == "skip":
            await callback.answer("⏭ **Skipped**")
            await process_queue(chat_id)
            
        elif action == "stop":
            await PlaybackManager.stop(chat_id)
            await callback.answer("⏹ **Stopped**")
            try:
                await callback.message.delete()
            except:
                pass
            return
            
        elif action == "loop":
            qm = queues[chat_id]
            if qm.loop_mode == LoopMode.OFF:
                qm.loop_mode = LoopMode.SINGLE
                text = "Single Track"
            elif qm.loop_mode == LoopMode.SINGLE:
                qm.loop_mode = LoopMode.QUEUE
                text = "Entire Queue"
            else:
                qm.loop_mode = LoopMode.OFF
                text = "Off"
            await callback.answer(f"🔁 Loop: {text}")
            
        elif action == "shuffle":
            qm = queues[chat_id]
            if qm.queue:
                qm.shuffle()
                await callback.answer("🔀 **Shuffled**")
            else:
                await callback.answer("❌ **Queue is empty**", show_alert=True)
                return
            
        elif action == "queue":
            text = get_queue_text(chat_id)
            qm = queues[chat_id]
            if qm.current:
                text = f"🎵 **Now Playing:**\n{qm.current.title}\n\n{text}"
            await callback.answer()
            await callback.message.reply_text(text)
            return
        
        elif action == "play":
            # Handle search result play
            video_id = rest.split("_")[1]
            # Search by video ID to get full info
            info = await YouTubeHandler._extract_info(f"https://youtube.com/watch?v={video_id}")
            if info and info[0]:
                song_info = info[0]
                song = Song(
                    title=song_info['title'],
                    url=song_info['url'],
                    duration=song_info['duration'],
                    thumbnail=song_info['thumbnail'],
                    video_id=song_info['id'],
                    requested_by=callback.from_user.mention,
                    requested_by_id=callback.from_user.id
                )
                
                qm = queues[chat_id]
                position = qm.add(song)
                
                if qm.state == PlaybackState.STOPPED:
                    await join_voice_chat(chat_id, callback.from_user.id)
                    await process_queue(chat_id)
                    await callback.answer("▶️ **Playing**")
                else:
                    await callback.answer(f"✅ Added at position #{position}")
                
                try:
                    await callback.message.delete()
                except:
                    pass
            return
        
        # Update UI
        qm = queues[chat_id]
        if qm.current:
            text = (
                f"🎵 **Now Playing:**\n\n"
                f"**{qm.current.title}**\n"
                f"⏱ `{format_duration(qm.current.duration)}`\n"
                f"🔁 Loop: {qm.loop_mode.name}\n"
                f"📊 State: {qm.state.value.title()}"
            )
            try:
                await callback.message.edit_text(
                    text,
                    reply_markup=get_player_buttons(chat_id)
                )
            except:
                pass
    
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await callback.answer(f"❌ **Error:** {str(e)}", show_alert=True)

# -------------------------
# Background Tasks
# -------------------------
async def auto_cleanup():
    """Clean old downloaded files"""
    while True:
        try:
            await asyncio.sleep(1800)  # Every 30 minutes
            
            current_time = time.time()
            for filename in os.listdir(Config.DOWNLOAD_DIR):
                filepath = os.path.join(Config.DOWNLOAD_DIR, filename)
                
                if os.path.isfile(filepath):
                    # Check if file is old and not in cache
                    file_age = current_time - os.path.getmtime(filepath)
                    video_id = os.path.splitext(filename)[0]
                    
                    if file_age > 3600 and video_id not in download_cache:
                        try:
                            os.remove(filepath)
                            logger.info(f"Cleaned up: {filename}")
                        except Exception as e:
                            logger.error(f"Cleanup error: {e}")
        except Exception as e:
            logger.error(f"Auto cleanup error: {e}")

async def auto_leave_inactive():
    """Leave voice chats that have been inactive"""
    while True:
        try:
            await asyncio.sleep(60)  # Check every minute
            
            current_time = datetime.now()
            inactive_chats = []
            
            for chat_id, join_time in active_calls.items():
                qm = queues[chat_id]
                
                # If nothing playing and inactive for X minutes
                if qm.state == PlaybackState.STOPPED:
                    if (current_time - join_time).seconds > Config.AUTO_LEAVE_DURATION:
                        inactive_chats.append(chat_id)
            
            for chat_id in inactive_chats:
                try:
                    await PlaybackManager.stop(chat_id)
                    await bot.send_message(
                        chat_id,
                        "👋 **Left voice chat due to inactivity**"
                    )
                    logger.info(f"Auto-left chat {chat_id}")
                except Exception as e:
                    logger.error(f"Auto leave error: {e}")
        
        except Exception as e:
            logger.error(f"Auto leave task error: {e}")

async def log_activity():
    """Log bot activity to log channel"""
    if not Config.LOG_CHANNEL:
        return
    
    while True:
        try:
            await asyncio.sleep(3600)  # Every hour
            
            active_chats = len([c for c in active_calls.keys()])
            total_songs = sum(len(q.queue) for q in queues.values())
            uptime = datetime.now() - START_TIME
            
            text = (
                f"📊 **Hourly Report**\n\n"
                f"⏰ Uptime: `{str(uptime).split('.')[0]}`\n"
                f"🎵 Active Chats: `{active_chats}`\n"
                f"📋 Total Queued: `{total_songs}`\n"
                f"💾 Cache: `{len(download_cache)}/{Config.CACHE_SIZE}`"
            )
            
            await bot.send_message(Config.LOG_CHANNEL, text)
        except Exception as e:
            logger.error(f"Log activity error: {e}")

# -------------------------
# Error Handler
# -------------------------
@bot.on_message(filters.command("error") & filters.user(Config.SUDO_USERS))
async def error_command(_, message: Message):
    """Get recent errors (sudo only)"""
    try:
        with open('bot.log', 'r') as f:
            lines = f.readlines()
            errors = [l for l in lines[-100:] if 'ERROR' in l]
            
            if errors:
                text = "🔴 **Recent Errors:**\n\n```\n" + "".join(errors[-10:]) + "```"
            else:
                text = "✅ **No recent errors**"
            
            await message.reply_text(text)
    except Exception as e:
        await message.reply_text(f"❌ **Error reading logs:** {str(e)}")

@bot.on_message(filters.command("restart") & filters.user(Config.SUDO_USERS))
async def restart_command(_, message: Message):
    """Restart bot (sudo only)"""
    await message.reply_text("🔄 **Restarting bot...**")
    os.execl(sys.executable, sys.executable, *sys.argv)

@bot.on_message(filters.command("broadcast") & filters.user(Config.SUDO_USERS))
async def broadcast_command(_, message: Message):
    """Broadcast message to all active chats (sudo only)"""
    if len(message.command) < 2:
        await message.reply_text("❌ **Usage:** `/broadcast <message>`")
        return
    
    text = message.text.split(None, 1)[1]
    sent = 0
    failed = 0
    
    msg = await message.reply_text("📢 **Broadcasting...**")
    
    for chat_id in list(active_calls.keys()):
        try:
            await bot.send_message(chat_id, f"📢 **Announcement:**\n\n{text}")
            sent += 1
            await asyncio.sleep(0.5)
        except:
            failed += 1
    
    await msg.edit(f"✅ **Broadcast complete**\n\nSent: `{sent}`\nFailed: `{failed}`")

# -------------------------
# Main Function
# -------------------------
async def main():
    """Main startup function"""
    try:
        # Start clients
        await bot.start()
        logger.info("Bot client started")
        
        await assistant.start()
        logger.info("Assistant client started")
        
        await calls.start()
        logger.info("PyTgCalls started")
        
        # Get info
        bot_info = await bot.get_me()
        assistant_info = await assistant.get_me()
        
        logger.info(f"Bot: @{bot_info.username}")
        logger.info(f"Assistant: @{assistant_info.username}")
        
        # Send startup message to log channel
        if Config.LOG_CHANNEL:
            try:
                await bot.send_message(
                    Config.LOG_CHANNEL,
                    f"✅ **Bot Started**\n\n"
                    f"🤖 Bot: @{bot_info.username}\n"
                    f"👤 Assistant: @{assistant_info.username}\n"
                    f"⏰ Time: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
                )
            except Exception as e:
                logger.error(f"Failed to send startup message: {e}")
        
        # Start background tasks
        asyncio.create_task(auto_cleanup())
        asyncio.create_task(auto_leave_inactive())
        asyncio.create_task(log_activity())
        
        logger.info("Background tasks started")
        logger.info("Bot is ready! Press Ctrl+C to stop.")
        
        # Keep running
        await idle()
        
    except Exception as e:
        logger.error(f"Startup error: {e}")
        traceback.print_exc()
    finally:
        # Cleanup
        try:
            await calls.stop()
            await bot.stop()
            await assistant.stop()
            logger.info("Bot stopped gracefully")
        except:
            pass

# -------------------------
# Entry Point
# -------------------------
if __name__ == "__main__":
    try:
        # Check Python version
        if sys.version_info < (3, 8):
            print("Python 3.8 or higher is required!")
            sys.exit(1)
        
        # Run bot
        asyncio.run(main())
    
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        traceback.print_exc()
    finally:
        logger.info("Bot terminated")
