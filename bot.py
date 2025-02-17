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

# Function to verify FFmpeg installation
def verify_ffmpeg():
    try:
        # Run FFmpeg version command
        result = subprocess.run(
            ['ffmpeg', '-version'], 
            capture_output=True, 
            text=True, 
            timeout=5
        )
        
        # Use .splitlines() instead of .split('\n')
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

# Initialize the bot
app = Client("file_decrypt_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Create an asyncio queue
file_queue = asyncio.Queue()

# Flood wait delay function with randomized wait
async def flood_wait_delay():
    await asyncio.sleep(random.uniform(15, 20))

# Helper function to convert bytes to human-readable format
def human_readable_bytes(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024

# Helper function to format time
def format_time(seconds):
    seconds = int(seconds)
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02}:{mins:02}:{secs:02}"

# Function to generate video thumbnail with improved error handling
def generate_video_thumbnail(video_path, thumbnail_path, logger):
    """
    Generate a video thumbnail using FFmpeg with comprehensive error handling.
    
    Args:
    video_path (str): Path to the input video file
    thumbnail_path (str): Path where thumbnail will be saved
    logger (logging.Logger): Logger for error tracking
    
    Returns:
    bool: True if thumbnail generation was successful, False otherwise
    """
    if not FFMPEG_AVAILABLE:
        logger.warning("FFmpeg not available. Cannot generate thumbnail.")
        return False
    
    try:
        # Detailed FFmpeg command for thumbnail generation
        command = [
            'ffmpeg', 
            '-i', video_path,  # Input video
            '-ss', '00:00:01.000',  # Seek to 1 second
            '-vframes', '1',  # Extract only 1 frame
            '-q:v', '2',  # High-quality thumbnail
            thumbnail_path
        ]
        
        # Run FFmpeg with timeout and capture output
        result = subprocess.run(
            command, 
            capture_output=True, 
            text=True, 
            timeout=10  # 10-second timeout
        )
        
        # Check if thumbnail was generated
        if result.returncode == 0 and os.path.exists(thumbnail_path) and os.path.getsize(thumbnail_path) > 0:
            logger.info(f"Thumbnail generated successfully: {thumbnail_path}")
            return True
        else:
            logger.error(f"Thumbnail generation failed. FFmpeg output: {result.stderr}")
            return False
    
    except subprocess.TimeoutExpired:
        logger.error(f"Thumbnail generation timed out for {video_path}")
        return False
    except FileNotFoundError:
        logger.error("FFmpeg executable not found")
        return False
    except Exception as e:
        logger.error(f"Unexpected error generating thumbnail: {e}")
        return False

# Function to download a file (without progress tracking)
async def download_file(client, message, document, dest):
    try:
        await client.download_media(document.file_id, file_name=dest)
        logger.info(f"Downloaded {dest}")
        return True
    except Exception as e:
        logger.error(f"Download failed: {e}")
        await message.reply_text(f"Failed to download file: {e}")
        return False

# Function to decrypt the file
async def decrypt_file(file_path, output_path, password=DEFAULT_PASSWORD):
    command = ["7z", "x", f"-o{output_path}", file_path, f"-p{password}", "-y"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == 0:
        logger.info(f"Decrypted: {file_path}")
        return True
    else:
        logger.error(f"Decryption error: {result.stderr}")
        return False

# Main function to process files
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
            await status_msg.edit_text(f"Failed to decrypt {file_name}.")
            return

        await status_msg.edit_text(f"Uploading decrypted files for {file_name}...")
        for root, _, files in os.walk(temp_dir):
            for file in files:
                decrypted_file_path = os.path.join(root, file)
                caption = f"{file} - Extracted from {file_name}"
                try:
                    # Determine the file type and upload as media with a caption
                    if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                        await client.send_photo(message.chat.id, decrypted_file_path, caption=caption)
                    elif file.lower().endswith(('.mp4', '.mkv', '.avi')):
                        # Advanced thumbnail generation
                        thumbnail_path = os.path.join(temp_dir, f"{file}_thumb.jpg")
                        thumbnail_generated = generate_video_thumbnail(
                            decrypted_file_path, 
                            thumbnail_path, 
                            logger
                        )

                        # Upload video with or without thumbnail
                        if thumbnail_generated:
                            await client.send_video(
                                message.chat.id,
                                decrypted_file_path,
                                caption=caption,
                                thumb=thumbnail_path
                            )
                            os.remove(thumbnail_path)  # Clean up thumbnail
                        else:
                            await client.send_video(
                                message.chat.id,
                                decrypted_file_path,
                                caption=caption
                            )
                    else:
                        await client.send_document(message.chat.id, decrypted_file_path, caption=caption)
                    await flood_wait_delay()  # Apply randomized flood wait delay between uploads
                except FloodWait as e:
                    logger.warning(f"FloodWait of {e.x} seconds. Sleeping...")
                    await asyncio.sleep(e.x)
                    await client.send_document(message.chat.id, decrypted_file_path, caption=caption)
                except Exception as e:
                    logger.error(f"Error uploading file {file}: {e}")
                finally:
                    # Always try to remove the file
                    try:
                        os.remove(decrypted_file_path)
                    except Exception as cleanup_error:
                        logger.error(f"Error during file cleanup: {cleanup_error}")

        await status_msg.edit_text(f"Completed processing {file_name}.")
    except Exception as e:
        logger.error(f"Unexpected error processing {file_name}: {e}")
        await status_msg.edit_text(f"An unexpected error occurred while processing {file_name}")
    finally:
        # Clean up temporary directories and files
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
            os.remove(download_path)
        except Exception as cleanup_error:
            logger.error(f"Error during final cleanup: {cleanup_error}")

# Worker function
async def queue_worker():
    while True:
        client, message, document = await file_queue.get()
        try:
            await process_file(client, message, document)
        except Exception as e:
            logger.error(f"Queue worker error: {e}")
        finally:
            file_queue.task_done()

# Handle document messages
@app.on_message(filters.document)
async def handle_documents(client, message: Message):
    await file_queue.put((client, message, message.document))
    await message.reply_text("Your file has been added to the processing queue.")

# Handle start command
@app.on_message(filters.command(["start", "help"]))
async def start_command(client, message: Message):
    startup_message = (
        "🤖 File Decryption Bot\n\n"
        "Send me an encrypted 7z file, and I'll decrypt and upload its contents.\n\n"
        f"FFmpeg Available: {'✅ Yes' if FFMPEG_AVAILABLE else '❌ No'}\n"
        "Supported file types: Images, Videos, Documents"
    )
    await message.reply_text(startup_message)

# Run the bot and worker
if __name__ == "__main__":
    # Log FFmpeg status during startup
    logger.info(f"FFmpeg Availability: {FFMPEG_AVAILABLE}")
    
    loop = asyncio.get_event_loop()
    loop.create_task(queue_worker())
    app.run()
