# Telegram Music Bot Setup

## Prerequisites

1. Python 3.10+
2. FFmpeg installed on system
3. Telegram API credentials (API_ID, API_HASH)
4. Bot token from @BotFather
5. Assistant account session string

## Quick Setup

### 1. Get API Credentials

- Go to https://my.telegram.org
- Login and create an app
- Copy API_ID and API_HASH

### 2. Create Bot

- Message @BotFather on Telegram
- Create new bot with /newbot
- Copy the bot token

### 3. Generate Session String

```bash
python session_generator.py
```

Enter your API_ID and API_HASH, then login with phone number.
Copy the session string.

### 4. Configure Environment

Create `.env` file:

```env
API_ID=12345678
API_HASH=your_api_hash
BOT_TOKEN=123456:ABCdefGHIjklMNOpqrsTUVwxyz
ASSISTANT_SESSION=your_session_string
SUDO_USERS=your_user_id
```

### 5. Install Dependencies

```bash
pip install -r requirements.txt
```

### 6. Run Bot

```bash
python main.py
```

## Deployment

### Heroku

1. Create new app
2. Add Python buildpack
3. Set environment variables
4. Deploy from GitHub
5. Enable worker dyno

### Render

1. Create new Web Service
2. Select Python environment
3. Set build command: `pip install -r requirements.txt`
4. Set start command: `python main.py`
5. Add environment variables

### VPS

```bash
git clone <repo>
cd <repo>
pip install -r requirements.txt
python main.py
```

Use screen/tmux for background:

```bash
screen -S musicbot
python main.py
# Ctrl+A, D to detach
```

## Commands

- `/play <song>` - Play music
- `/pause` - Pause playback
- `/resume` - Resume playback
- `/skip` - Skip song
- `/stop` - Stop and clear
- `/queue` - View queue
- `/loop` - Toggle loop
- `/shuffle` - Shuffle queue
- `/volume <1-200>` - Set volume
- `/ping` - Check status
- `/help` - Show help

## Troubleshooting

**Bot not joining VC:**
- Ensure assistant account is in group
- Check voice chat is active
- Verify permissions

**Download errors:**
- Check internet connection
- Verify yt-dlp is updated
- Check disk space

**Session expired:**
- Regenerate session string
- Update ASSISTANT_SESSION in env

## Features

✅ YouTube search & direct links
✅ High quality audio streaming
✅ Queue management
✅ Loop & shuffle
✅ Volume control
✅ Inline buttons
✅ Admin-only controls
✅ Auto-cleanup
✅ Multi-group support
✅ Auto-reconnect
✅ Flood wait handling

## Support

For issues, check logs and ensure:
- All dependencies installed
- Environment variables set
- FFmpeg available
- Sufficient permissions
