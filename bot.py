import os
import aiofiles
from pyrogram import Client, filters
from pyrogram.types import Message
import subprocess
import tempfile
import shutil
import asyncio
import time
import math
import random
import logging
from pyrogram.errors import FloodWait

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
API_ID = os.getenv("API_ID", "29728224")
API_HASH = os.getenv("API_HASH", "b3a147834fd9d39e52e48221988c3702")
BOT_TOKEN = os.getenv("BOT_TOKEN", "7514240817:AAGItz8eiGbzKYVHA7N5gVy6OdeKrk9nLtU")
DOWNLOAD_LOCATION = "./downloads/"
DECRYPTED_LOCATION = "./decrypted/"
DEFAULT_PASSWORD = os.getenv("DEFAULT_PASSWORD", "ee")

# Create directories
os.makedirs(DOWNLOAD_LOCATION, exist_ok=True)
os.makedirs(DECRYPTED_LOCATION, exist_ok=True)

# Function to verify FFmpeg installation
def verify_ffmpeg():
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        logger.info(f"FFmpeg Version: {result.stdout.splitlines()[0]}")
        return result.returncode == 0
    except FileNotFoundError:
        logger.error("FFmpeg is not installed!")
        return False
    except subprocess.TimeoutExpired:
        logger.error("FFmpeg version check timed out")
        return False
    except Exception as e:
        logger.error(f"Error checking FFmpeg: {e}")
        return False

# Verify FFmpeg at startup
FFMPEG_AVAILABLE = verify_ffmpeg()

# Initialize Pyrogram client
app = Client("rar_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Queue system
file_queue = asyncio.Queue()

async def flood_wait_delay():
    """Randomized delay to prevent flood waits"""
    await asyncio.sleep(random.uniform(15, 25))

def human_readable_size(size):
    """Convert bytes to human-readable format"""
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    for unit in units:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"

def format_time(seconds):
    """Format seconds into HH:MM:SS"""
    seconds = int(seconds)
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02}:{mins:02}:{secs:02}"

def generate_video_thumbnail(video_path, thumbnail_path, logger):
    """Generate a video thumbnail using FFmpeg"""
    if not FFMPEG_AVAILABLE:
        logger.warning("FFmpeg not available. Cannot generate thumbnail.")
        return False
    
    try:
        command = [
            'ffmpeg',
            '-i', video_path,
            '-ss', '00:00:01.000',
            '-vframes', '1',
            '-q:v', '2',
            thumbnail_path
        ]
        
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0 and os.path.exists(thumbnail_path) and os.path.getsize(thumbnail_path) > 0:
            logger.info(f"Thumbnail generated successfully: {thumbnail_path}")
            return True
        else:
            logger.error(f"Thumbnail generation failed. FFmpeg output: {result.stderr}")
            return False
    
    except Exception as e:
        logger.error(f"Thumbnail generation error: {str(e)}")
        return False

async def download_file(client, message, document, dest_path):
    """Download file with progress tracking"""
    try:
        start_time = time.time()
        status_message = await message.reply_text(f"📥 Downloading {document.file_name}...")
        
        async with aiofiles.open(dest_path, 'wb') as f:
            await client.download_media(
                message=document.file_id,
                file_name=f.name,
                progress=lambda current, total: logger.info(
                    f"Downloading {document.file_name}: {human_readable_size(current)}/{human_readable_size(total)}"
                )
            )
            
        download_time = time.time() - start_time
        logger.info(f"Downloaded {document.file_name} in {download_time:.2f}s")
        await status_message.edit_text(f"✅ Downloaded {document.file_name}")
        return True, status_message
    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        if 'status_message' in locals():
            await status_message.edit_text(f"❌ Download failed: {str(e)}")
        return False, None

async def decrypt_file(file_path, output_dir, password):
    """Decrypt RAR/7z/ZIP files with password support"""
    try:
        logger.info(f"Starting decryption: {file_path}")
        
        if file_path.lower().endswith('.rar'):
            cmd = [
                'unrar', 'x', '-idq',
                '-p' + password,
                file_path,
                output_dir
            ]
        else:
            cmd = [
                '7z', 'x',
                f'-o{output_dir}',
                f'-p{password}',
                '-y',
                file_path
            ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600
        )

        if result.returncode == 0:
            logger.info(f"Successfully decrypted {file_path}")
            return True
            
        error_msg = result.stderr or result.stdout
        logger.error(f"Decryption failed: {error_msg}")
        return False

    except Exception as e:
        logger.error(f"Decryption error: {str(e)}")
        return False

async def process_file(client, message, document):
    """Main processing pipeline"""
    file_name = document.file_name
    download_path = os.path.join(DOWNLOAD_LOCATION, file_name)
    temp_dir = tempfile.mkdtemp(dir=DECRYPTED_LOCATION)

    try:
        status_msg = await message.reply_text(f"🔄 Processing {file_name}...")

        # Download file
        download_success, download_status = await download_file(client, message, document, download_path)
        if not download_success:
            return

        # Decrypt file
        await status_msg.edit_text(f"🔓 Decrypting {file_name}...")
        decryption_result = await decrypt_file(download_path, temp_dir, DEFAULT_PASSWORD)
        
        if not decryption_result:
            await status_msg.edit_text("❌ Decryption failed! Possible reasons:\n"
                                     "• Wrong password\n"
                                     "• Corrupted archive\n"
                                     "• Unsupported format")
            return

        # Upload extracted files
        await status_msg.edit_text(f"📤 Uploading contents of {file_name}...")
        uploaded_files = 0
        
        for root, _, files in os.walk(temp_dir):
            for file in files:
                file_path = os.path.join(root, file)
                caption = f"📁 File: {file}\n📦 From archive: {file_name}\n🔐 Password: {DEFAULT_PASSWORD}"
                
                try:
                    if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                        await client.send_photo(
                            chat_id=message.chat.id,
                            photo=file_path,
                            caption=caption
                        )
                    elif file.lower().endswith(('.mp4', '.mkv', '.avi', '.mov')):
                        thumbnail_path = os.path.join(temp_dir, f"{file}_thumb.jpg")
                        thumbnail_generated = generate_video_thumbnail(
                            file_path,
                            thumbnail_path,
                            logger
                        )

                        if thumbnail_generated:
                            await client.send_video(
                                chat_id=message.chat.id,
                                video=file_path,
                                caption=caption,
                                thumb=thumbnail_path
                            )
                            os.remove(thumbnail_path)
                        else:
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
                    
                    uploaded_files += 1
                    await flood_wait_delay()
                    
                except FloodWait as e:
                    wait_time = e.x + 5
                    logger.warning(f"Flood wait: Sleeping {wait_time}s")
                    await asyncio.sleep(wait_time)
                except Exception as e:
                    logger.error(f"Failed to upload {file}: {str(e)}")
                finally:
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        logger.error(f"Error removing file {file_path}: {str(e)}")

        await status_msg.edit_text(
            f"✅ Successfully processed {file_name}\n"
            f"📦 Extracted and uploaded files: {uploaded_files}"
        )

    except Exception as e:
        logger.error(f"Processing error: {str(e)}")
        await status_msg.edit_text(f"❌ Critical error processing {file_name}")
    finally:
        # Cleanup
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
            os.remove(download_path)
        except Exception as e:
            logger.error(f"Cleanup error: {str(e)}")

async def queue_worker():
    """Process files from the queue"""
    while True:
        client, message, document = await file_queue.get()
        try:
            await process_file(client, message, document)
        except Exception as e:
            logger.error(f"Queue worker error: {str(e)}")
        finally:
            file_queue.task_done()

@app.on_message(filters.document)
async def handle_documents(client, message: Message):
    """Handle incoming documents"""
    if not message.document.file_name.lower().endswith(('.rar', '.7z', '.zip')):
        await message.reply_text("❌ Unsupported format! Please send RAR, 7Z, or ZIP files only.")
        return

    await file_queue.put((client, message, message.document))
    await message.reply_text(
        f"📥 Added to queue\n"
        f"📁 File: {message.document.file_name}\n"
        f"🔄 Position in queue: {file_queue.qsize()}"
    )

@app.on_message(filters.command(["start", "help"]))
async def start_command(client, message: Message):
    """Start command handler"""
    help_text = (
        "🔐 Archive Decrypt Bot\n\n"
        "Send password-protected RAR/7Z/ZIP files\n"
        f"Default Password: `{DEFAULT_PASSWORD}`\n\n"
        "Features:\n"
        "• Auto-decrypt with password\n"
        "• Support for RAR/7Z/ZIP archives\n"
        "• Video thumbnails (FFmpeg: {'✅' if FFMPEG_AVAILABLE else '❌'})\n"
        "• Media previews for images/videos\n"
        "• Queue system with status updates"
    )
    await message.reply_text(help_text)

if __name__ == "__main__":
    logger.info("Starting Archive Decrypt Bot...")
    logger.info(f"FFmpeg Available: {FFMPEG_AVAILABLE}")
    loop = asyncio.get_event_loop()
    loop.create_task(queue_worker())
    app.run()
