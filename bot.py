import os
import aiofiles
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import subprocess
import tempfile
import shutil
import asyncio
import time
import math
import random
import logging
import hashlib
from datetime import datetime
from pyrogram.errors import FloodWait, MessageTooLong
from typing import Optional, Tuple, Dict

# Configure logging with rotation
from logging.handlers import RotatingFileHandler

# Enhanced logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            'bot.log',
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration with new settings
class Config:
    API_ID = os.getenv("API_ID", "29728224")
    API_HASH = os.getenv("API_HASH", "b3a147834fd9d39e52e48221988c3702")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "7514240817:AAGItz8eiGbzKYVHA7N5gVy6OdeKrk9nLtU")
    DOWNLOAD_LOCATION = "./downloads/"
    DECRYPTED_LOCATION = "./decrypted/"
    DEFAULT_PASSWORD = os.getenv("DEFAULT_PASSWORD", "ee")
    MAX_FILE_SIZE = 20000 * 1024 * 1024  # 2GB
    ALLOWED_MIME_TYPES = ['application/x-rar-compressed', 'application/x-7z-compressed', 'application/zip']
    ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "5858127198").split(","))) if os.getenv("ADMIN_IDS") else []
    MAX_CONCURRENT_DOWNLOADS = 10000
    CHUNK_SIZE = 8192 * 1024  # 8MB chunks for download
    STATS_UPDATE_INTERVAL = 60  # Update stats every 60 seconds

# Create necessary directories
os.makedirs(Config.DOWNLOAD_LOCATION, exist_ok=True)
os.makedirs(Config.DECRYPTED_LOCATION, exist_ok=True)

# Global stats tracking
class BotStats:
    def __init__(self):
        self.total_processed = 0
        self.total_failed = 0
        self.active_downloads = 0
        self.bytes_processed = 0
        self.start_time = datetime.now()
        self.processing_times = []
        self.current_tasks: Dict[int, str] = {}  # user_id: current_task
        self.download_semaphore = asyncio.Semaphore(Config.MAX_CONCURRENT_DOWNLOADS)

    def add_processing_time(self, time_taken: float):
        self.processing_times.append(time_taken)
        if len(self.processing_times) > 100:  # Keep only last 100 entries
            self.processing_times.pop(0)

    def get_average_processing_time(self) -> float:
        if not self.processing_times:
            return 0
        return sum(self.processing_times) / len(self.processing_times)

stats = BotStats()

# Enhanced FFmpeg verification
def verify_ffmpeg() -> Tuple[bool, str]:
    """Verify FFmpeg installation and return version info"""
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        version = result.stdout.splitlines()[0]
        return result.returncode == 0, version
    except Exception as e:
        logger.error(f"FFmpeg verification error: {str(e)}")
        return False, "Not available"

FFMPEG_AVAILABLE, FFMPEG_VERSION = verify_ffmpeg()

# Add QueueManager class before Bot class (after BotStats)
class QueueManager:
    def __init__(self, timeout=5):
        self.pending_files = {}  # chat_id: [FileInfo]
        self.timeout = timeout
        self.locks = {}  # chat_id: Lock
        
    async def add_file(self, chat_id, file_info):
        if chat_id not in self.locks:
            self.locks[chat_id] = asyncio.Lock()
            
        async with self.locks[chat_id]:
            if chat_id not in self.pending_files:
                self.pending_files[chat_id] = []
                # Schedule flush after timeout
                asyncio.create_task(self.flush_queue(chat_id))
            
            self.pending_files[chat_id].append(file_info)
            
    async def flush_queue(self, chat_id):
        await asyncio.sleep(self.timeout)
        async with self.locks[chat_id]:
            files = self.pending_files.pop(chat_id, [])
            if not files:
                return
                
            total_files = len(files)
            if total_files == 1:
                # Single file notification
                file_info = files[0]
                await file_info['message'].reply_text(
                    f"📥 File added to queue\n"
                    f"📁 File: {file_info['name']}\n"
                    f"💾 Size: {human_readable_size(file_info['size'])}\n"
                    f"🔄 Queue position: {file_info['position']}\n"
                    f"⏱ Estimated wait time: {format_time(file_info['eta'])}"
                )
            else:
                # Batch notification
                first_file = files[0]
                last_file = files[-1]
                total_size = sum(f['size'] for f in files)
                total_eta = last_file['eta']
                
                await first_file['message'].reply_text(
                    f"📥 Batch Queue Update\n"
                    f"📦 {total_files} files added to queue\n"
                    f"📁 First: {first_file['name']}\n"
                    f"📁 Last: {last_file['name']}\n"
                    f"💾 Total Size: {human_readable_size(total_size)}\n"
                    f"🔄 Queue positions: {first_file['position']} - {last_file['position']}\n"
                    f"⏱ Total estimated wait time: {format_time(total_eta)}"
                )

# Now the Bot class can use QueueManager
class Bot(Client):
    def __init__(self):
        super().__init__(
            "archive_decrypt_bot",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            bot_token=Config.BOT_TOKEN
        )
        self.queue = asyncio.Queue()
        self.active_tasks = {}
        self.queue_manager = QueueManager(timeout=5)  # 5 second window to batch files

    async def start(self):
        await super().start()
        logger.info("Bot started successfully!")
        asyncio.create_task(self._queue_worker())
        asyncio.create_task(self._stats_updater())

    async def stop(self, *args):
        await super().stop()
        logger.info("Bot stopped!")

    async def _queue_worker(self):
        """Enhanced queue worker with priority support"""
        while True:
            try:
                priority, client, message, document = await self.queue.get()
                await process_file(client, message, document)
            except Exception as e:
                logger.error(f"Queue worker error: {str(e)}")
            finally:
                self.queue.task_done()

    async def _stats_updater(self):
        """Periodic stats update task"""
        while True:
            try:
                await asyncio.sleep(Config.STATS_UPDATE_INTERVAL)
                logger.info(
                    f"Stats Update - Processed: {stats.total_processed}, "
                    f"Failed: {stats.total_failed}, "
                    f"Queue: {self.queue.qsize()}, "
                    f"Active: {stats.active_downloads}"
                )
            except Exception as e:
                logger.error(f"Stats updater error: {str(e)}")

# Add missing helper functions
async def flood_wait_delay():
    """Add random delay to prevent flood wait"""
    await asyncio.sleep(random.uniform(0.5, 1.5))

def human_readable_size(size_bytes):
    """Convert bytes to human readable format"""
    if not size_bytes:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def format_time(seconds):
    """Format seconds into human readable time"""
    if not seconds or seconds < 0:
        return "0s"
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}h {mins}m {secs}s"
    elif mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"

async def download_file(client, status_msg, document, download_path):
    """Download file with progress tracking"""
    try:
        start_time = time.time()
        await client.download_media(
            document,
            file_name=download_path,
            progress=progress_callback,
            progress_args=(status_msg, start_time, "Downloading")
        )
        return True
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        return False

# Add these new imports
import subprocess
import patoolib

# Update decrypt_file function with proper archive extraction
async def decrypt_file(file_path, output_dir, password):
    """Decrypt RAR/7z/ZIP files with proper password handling"""
    try:
        logger.info(f"Starting decryption: {file_path}")
        file_ext = file_path.lower()
        
        if file_ext.endswith('.rar'):
            cmd = [
                'unrar', 'x', '-idq',
                '-p' + password,
                file_path,
                output_dir
            ]
            # Try unrar first
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if result.returncode == 0:
                    return True
            except:
                # Fallback to patool for RAR
                patoolib.extract_archive(file_path, outdir=output_dir, password=password)
                return True

        elif file_ext.endswith(('.7z', '.zip')):
            cmd = [
                '7z', 'x',
                f'-o{output_dir}',
                f'-p{password}',
                '-y',
                file_path
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if result.returncode == 0:
                    return True
            except:
                # Fallback to patool for 7Z/ZIP
                patoolib.extract_archive(file_path, outdir=output_dir, password=password)
                return True

        return False

    except Exception as e:
        logger.error(f"Decryption error: {str(e)}")
        return False

def generate_video_thumbnail(video_path, thumbnail_path, logger):
    """Generate video thumbnail using FFmpeg"""
    try:
        subprocess.run([
            'ffmpeg', '-i', video_path,
            '-ss', '00:00:01',
            '-vframes', '1',
            '-vf', 'scale=320:-1',
            thumbnail_path
        ], check=True, capture_output=True)
        return True
    except Exception as e:
        logger.error(f"Thumbnail generation error: {str(e)}")
        return False

app = Bot()

# Enhanced helper functions
def calculate_file_hash(file_path: str) -> str:
    """Calculate SHA-256 hash of file"""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def get_progress_bar(current: int, total: int, length: int = 20) -> str:
    """Generate a text progress bar"""
    filled_length = int(length * current // total)
    bar = '█' * filled_length + '░' * (length - filled_length)
    percent = current / total * 100
    return f"|{bar}| {percent:.1f}%"

async def progress_callback(current: int, total: int, message: Message, start_time: float, action: str):
    """Enhanced progress callback with ETA and speed calculation"""
    try:
        if time.time() - progress_callback.last_update < 60:  # Update UI max once per second
            return
    except AttributeError:
        progress_callback.last_update = 0

    progress_callback.last_update = time.time()
    elapsed_time = progress_callback.last_update - start_time
    speed = current / elapsed_time if elapsed_time > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0

    progress_text = (
        f"{action}...\n"
        f"{get_progress_bar(current, total)}\n"
        f"💾 Size: {human_readable_size(current)}/{human_readable_size(total)}\n"
        f"⚡ Speed: {human_readable_size(speed)}/s\n"
        f"⏱ ETA: {format_time(eta)}"
    )

    try:
        await message.edit_text(progress_text)
    except MessageTooLong:
        # Shorter version if message is too long
        await message.edit_text(f"{action}... {get_progress_bar(current, total)}")
    except Exception as e:
        logger.error(f"Progress callback error: {str(e)}")

# Enhanced file processing
async def process_file(client: Client, message: Message, document: Message):
    """Main file processing pipeline with enhanced features"""
    user_id = message.from_user.id
    file_name = document.file_name
    start_time = time.time()
    temp_dir = None
    download_path = None

    try:
        # Check file size
        if document.file_size > Config.MAX_FILE_SIZE:
            await message.reply_text("❌ File too large! Maximum size is 2GB.")
            return

        # Check if user is already processing a file
        if user_id in stats.current_tasks:
            await message.reply_text("⚠️ You already have a file being processed. Please wait.")
            return

        stats.current_tasks[user_id] = file_name
        status_msg = await message.reply_text("🔄 Initializing process...")

        # Create unique directories for this process
        process_id = hashlib.md5(f"{user_id}{time.time()}".encode()).hexdigest()[:10]
        download_path = os.path.join(Config.DOWNLOAD_LOCATION, f"{process_id}_{file_name}")
        temp_dir = tempfile.mkdtemp(dir=Config.DECRYPTED_LOCATION)

        # Download with semaphore
        async with stats.download_semaphore:
            stats.active_downloads += 1
            download_success = await download_file(client, status_msg, document, download_path)
            stats.active_downloads -= 1

            if not download_success:
                raise Exception("Download failed")

        # Verify file integrity
        file_hash = calculate_file_hash(download_path)
        logger.info(f"File hash: {file_hash}")

        # Decrypt file
        await status_msg.edit_text("🔓 Decrypting archive...")
        if not await decrypt_file(download_path, temp_dir, Config.DEFAULT_PASSWORD):
            raise Exception("Decryption failed")

        # Process extracted files
        extracted_files = []
        total_size = 0
        for root, _, files in os.walk(temp_dir):
            for file in files:
                file_path = os.path.join(root, file)
                total_size += os.path.getsize(file_path)
                extracted_files.append(file)

        await status_msg.edit_text(
            f"📤 Uploading {len(extracted_files)} files...\n"
            f"📦 Total size: {human_readable_size(total_size)}"
        )

        # Upload files with enhanced information
        for idx, file in enumerate(extracted_files, 1):
            file_path = os.path.join(temp_dir, file)
            caption = (
                f"📁 File: {file}\n"
                f"📦 From: {file_name}\n"
                f"🔐 Password: {Config.DEFAULT_PASSWORD}\n"
                f"📊 Progress: {idx}/{len(extracted_files)}"
            )

            try:
                if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                    await client.send_photo(
                        chat_id=message.chat.id,
                        photo=file_path,
                        caption=caption
                    )
                elif file.lower().endswith(('.mp4', '.mkv', '.avi', '.mov')):
                    # Generate thumbnail if FFmpeg is available
                    thumbnail_path = None
                    if FFMPEG_AVAILABLE:
                        thumbnail_path = os.path.join(temp_dir, f"thumb_{idx}.jpg")
                        if generate_video_thumbnail(file_path, thumbnail_path, logger):
                            await client.send_video(
                                chat_id=message.chat.id,
                                video=file_path,
                                caption=caption,
                                thumb=thumbnail_path
                            )
                        else:
                            thumbnail_path = None

                    if not thumbnail_path:
                        await client.send_video(
                            chat_id=message.chat.id,
                            video=file_path,
                            caption=caption
                        )
                else:
                    await client.send_document(
                        chat_id=message.chat.id,
                        document=file_path,
                        caption=caption
                    )

                await flood_wait_delay()

            except FloodWait as e:
                logger.warning(f"FloodWait detected: {e.x} seconds")
                await asyncio.sleep(e.x)
            except Exception as e:
                logger.error(f"Error uploading {file}: {str(e)}")
                await message.reply_text(f"⚠️ Error uploading {file}")

        # Update stats
        stats.total_processed += 1
        stats.bytes_processed += total_size
        process_time = time.time() - start_time
        stats.add_processing_time(process_time)

        # Final status update
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 View Stats", callback_data="view_stats")]
        ])
        
        await status_msg.edit_text(
            f"✅ Successfully processed {file_name}\n"
            f"📦 Files extracted: {len(extracted_files)}\n"
            f"💾 Total size: {human_readable_size(total_size)}\n"
            f"⏱ Time taken: {format_time(process_time)}",
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Error processing {file_name}: {str(e)}")
        stats.total_failed += 1
        if 'status_msg' in locals():
            await status_msg.edit_text(f"❌ Error processing {file_name}: {str(e)}")
    finally:
        # Cleanup
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        if download_path and os.path.exists(download_path):
            os.remove(download_path)
        if user_id in stats.current_tasks:
            del stats.current_tasks[user_id]

# Stats callback handler
@app.on_callback_query(filters.regex("^view_stats$"))
async def handle_stats_callback(client, callback_query):
    uptime = datetime.now() - stats.start_time
    avg_time = stats.get_average_processing_time()
    
    stats_text = (
        "📊 Bot Statistics\n\n"
        f"✅ Successfully processed: {stats.total_processed}\n"
        f"❌ Failed: {stats.total_failed}\n"
        f"💾 Total data processed: {human_readable_size(stats.bytes_processed)}\n"
        f"⏱ Average processing time: {format_time(avg_time)}\n"
        f"📡 Active downloads: {stats.active_downloads}\n"
        f"⌛ Uptime: {str(uptime).split('.')[0]}\n"
        f"🛠 FFmpeg: {'✅ ' + FFMPEG_VERSION if FFMPEG_AVAILABLE else '❌ Not available'}"
    )
    
    await callback_query.answer()
    await callback_query.message.edit_text(stats_text)

# Admin command handler
@app.on_message(filters.command(["admin"]) & filters.user(Config.ADMIN_IDS))
async def admin_command(client, message):
    """Handle admin commands for bot management"""
    commands = message.text.split()[1:]
    if not commands:
        await message.reply_text(
            "📊 Admin Commands:\n\n"
            "/admin stats - View detailed statistics\n"
            "/admin clear_queue - Clear processing queue\n"
            "/admin maintenance - Toggle maintenance mode"
        )
        return

    command = commands[0].lower()
    if command == "stats":
        # Detailed stats for admins
        stats_text = (
            "📊 Detailed Statistics\n\n"
            f"Queue size: {app.queue.qsize()}\n"
            f"Active downloads: {stats.active_downloads}\n"
            f"Total processed: {stats.total_processed}\n"
            f"Total failed: {stats.total_failed}\n"
            f"Data processed: {human_readable_size(stats.bytes_processed)}\n"
            f"Average time: {format_time(stats.get_average_processing_time())}\n"
            f"Current tasks: {len(stats.current_tasks)}\n"
            f"System uptime: {str(datetime.now() - stats.start_time).split('.')[0]}"
        )
        await message.reply_text(stats_text)
    elif command == "clear_queue":
        # Clear the processing queue
        while not app.queue.empty():
            app.queue.get_nowait()
        await message.reply_text("🧹 Queue cleared successfully!")
    elif command == "maintenance":
        # Toggle maintenance mode (implement in future)
        await message.reply_text("🛠 Maintenance mode not implemented yet")

# Enhanced message handlers
@app.on_message(filters.document)
async def handle_documents(client: Client, message: Message):
    """Enhanced document handler with batched notifications"""
    try:
        document = message.document
        if not document:
            return

        # Check file format
        file_name = document.file_name.lower()
        if not any(file_name.endswith(ext) for ext in ('.rar', '.7z', '.zip')):
            await message.reply_text(
                "❌ Unsupported file format!\n"
                "📝 Supported formats: RAR, 7Z, ZIP"
            )
            return

        # Check file size
        if document.file_size > Config.MAX_FILE_SIZE:
            await message.reply_text(
                f"❌ File too large!\n"
                f"📦 Maximum size: {human_readable_size(Config.MAX_FILE_SIZE)}\n"
                f"📝 Your file: {human_readable_size(document.file_size)}"
            )
            return

        # Check concurrent downloads
        # user_id = message.from_user.id
        # if user_id in stats.current_tasks:
        #     current_task = stats.current_tasks[user_id]
        #     await message.reply_text(
        #         "⚠️ You already have an active task!\n"
        #         f"📝 Current task: {current_task}\n"
        #         "Please wait for it to complete."
        #     )
        #     return

        # Add to queue with priority for small files
        priority = 1 if document.file_size < 100 * 1024 * 1024 else 2  # Priority 1 for files < 100MB
        await app.queue.put((priority, client, message, document))
        
        position = app.queue.qsize()
        eta = position * stats.get_average_processing_time()
        
        # Add to queue manager for batched notification
        file_info = {
            'name': document.file_name,
            'size': document.file_size,
            'position': position,
            'eta': eta,
            'message': message
        }
        await app.queue_manager.add_file(message.chat.id, file_info)

    except Exception as e:
        logger.error(f"Error handling document: {str(e)}")
        await message.reply_text("❌ An error occurred while processing your file")

@app.on_message(filters.command(["start", "help"]))
async def start_command(client: Client, message: Message):
    """Enhanced start command with inline keyboard"""
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Stats", callback_data="view_stats"),
            InlineKeyboardButton("ℹ️ Help", callback_data="show_help")
        ]
    ])

    help_text = (
        "🔐 Advanced Archive Decrypt Bot\n\n"
        "Send password-protected RAR/7Z/ZIP files\n"
        f"Default Password: `{Config.DEFAULT_PASSWORD}`\n\n"
        "Features:\n"
        "• Auto-decrypt with password\n"
        "• Support for RAR/7Z/ZIP archives\n"
        "• Video thumbnails (FFmpeg)\n"
        "• Queue system with priorities\n"
        "• Progress tracking\n"
        "• File integrity checks\n"
        "• Concurrent processing\n\n"
        "Status:\n"
        f"• FFmpeg: {'✅' if FFMPEG_AVAILABLE else '❌'}\n"
        f"• Queue Size: {app.queue.qsize()}\n"
        f"• Active Downloads: {stats.active_downloads}"
    )

    await message.reply_text(help_text, reply_markup=keyboard)

@app.on_callback_query(filters.regex("^show_help$"))
async def help_callback(client, callback_query):
    """Detailed help callback"""
    help_text = (
        "📖 Detailed Help\n\n"
        "1. Supported Formats:\n"
        "   • RAR (including RAR5)\n"
        "   • 7Z\n"
        "   • ZIP\n\n"
        "2. File Limits:\n"
        f"   • Maximum size: {human_readable_size(Config.MAX_FILE_SIZE)}\n"
        "   • One active task per user\n\n"
        "3. Features:\n"
        "   • Auto-extraction\n"
        "   • Video thumbnails\n"
        "   • Progress tracking\n"
        "   • Queue system\n"
        "   • File integrity checks\n\n"
        "4. Tips:\n"
        "   • Smaller files (<100MB) get priority\n"
        "   • Wait for one file to complete before sending another\n"
        "   • Check queue position for estimated wait time"
    )
    
    await callback_query.answer()
    await callback_query.message.edit_text(help_text)

if __name__ == "__main__":
    logger.info("Starting Enhanced Archive Decrypt Bot...")
    logger.info(f"FFmpeg Status: {FFMPEG_AVAILABLE}")
    logger.info(f"FFmpeg Version: {FFMPEG_VERSION}")
    
    app.run()
