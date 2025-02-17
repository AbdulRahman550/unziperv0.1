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
DEFAULT_PASSWORD = os.getenv("DEFAULT_PASSWORD", "Telegram MEQIQU")

# Create directories if they don't exist
os.makedirs(DOWNLOAD_LOCATION, exist_ok=True)
os.makedirs(DECRYPTED_LOCATION, exist_ok=True)

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
    except Exception as e:
        logger.error(f"FFmpeg check failed: {e}")
        return False

FFMPEG_AVAILABLE = verify_ffmpeg()

app = Client("file_decrypt_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
file_queue = asyncio.Queue()

async def flood_wait_delay():
    await asyncio.sleep(random.uniform(15, 20))

def human_readable_bytes(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024

def generate_video_thumbnail(video_path, thumbnail_path, logger):
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
        return result.returncode == 0 and os.path.exists(thumbnail_path)
    except Exception as e:
        logger.error(f"Thumbnail error: {e}")
        return False

async def download_file(client, message, document, dest):
    try:
        await client.download_media(document.file_id, file_name=dest)
        return True
    except Exception as e:
        await message.reply_text(f"Download failed: {e}")
        return False

async def decrypt_file(file_path, output_path, password=DEFAULT_PASSWORD):
    try:
        if file_path.lower().endswith('.rar'):
            cmd = ["unrar", "x", "-p" + password, file_path, output_path]
        else:
            cmd = ["7z", "x", f"-o{output_path}", file_path, f"-p{password}", "-y"]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode == 0:
            return True
        logger.error(f"Decryption failed: {result.stderr or result.stdout}")
        return False
    except subprocess.TimeoutExpired:
        logger.error("Decryption timed out")
        return False
    except Exception as e:
        logger.error(f"Decryption error: {e}")
        return False

async def process_file(client, message, document):
    file_name = document.file_name
    download_path = os.path.join(DOWNLOAD_LOCATION, file_name)
    temp_dir = tempfile.mkdtemp(dir=DECRYPTED_LOCATION)

    try:
        status_msg = await message.reply_text(f"Processing {file_name}...")
        
        if not await download_file(client, status_msg, document, download_path):
            return

        await status_msg.edit_text(f"Decrypting {file_name}...")
        if not await decrypt_file(download_path, temp_dir):
            await status_msg.edit_text("Decryption failed. Wrong password or corrupt file.")
            return

        await status_msg.edit_text(f"Uploading {file_name} contents...")
        for root, _, files in os.walk(temp_dir):
            for file in files:
                file_path = os.path.join(root, file)
                caption = f"{file} - From {file_name}"
                
                try:
                    if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                        await client.send_photo(message.chat.id, file_path, caption=caption)
                    elif file.lower().endswith(('.mp4', '.mkv', '.avi')):
                        thumb_path = os.path.join(temp_dir, f"{file}_thumb.jpg")
                        if generate_video_thumbnail(file_path, thumb_path, logger):
                            await client.send_video(message.chat.id, file_path, caption=caption, thumb=thumb_path)
                            os.remove(thumb_path)
                        else:
                            await client.send_video(message.chat.id, file_path, caption=caption)
                    else:
                        await client.send_document(message.chat.id, file_path, caption=caption)
                    await flood_wait_delay()
                except FloodWait as e:
                    await asyncio.sleep(e.x)
                except Exception as e:
                    logger.error(f"Upload error: {e}")
                finally:
                    try:
                        os.remove(file_path)
                    except:
                        pass

        await status_msg.edit_text(f"Completed: {file_name}")
    except Exception as e:
        logger.error(f"Processing error: {e}")
        await status_msg.edit_text(f"Error processing {file_name}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            os.remove(download_path)
        except:
            pass

async def queue_worker():
    while True:
        client, message, document = await file_queue.get()
        try:
            await process_file(client, message, document)
        except Exception as e:
            logger.error(f"Worker error: {e}")
        finally:
            file_queue.task_done()

@app.on_message(filters.document)
async def handle_documents(client, message: Message):
    await file_queue.put((client, message, message.document))
    await message.reply_text("File queued for processing")

@app.on_message(filters.command(["start", "help"]))
async def start_command(client, message: Message):
    start_text = (
        "🔐 File Decryption Bot\n\n"
        "Send encrypted archives (RAR/7Z) with password protection\n"
        f"Default Password: {DEFAULT_PASSWORD}\n"
        f"FFmpeg: {'✅' if FFMPEG_AVAILABLE else '❌'}"
    )
    await message.reply_text(start_text)

if __name__ == "__main__":
    logger.info("Starting bot...")
    loop = asyncio.get_event_loop()
    loop.create_task(queue_worker())
    app.run()
