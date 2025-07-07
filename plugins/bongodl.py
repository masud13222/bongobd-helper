from telegram import Update
from telegram.ext import ContextTypes
from plugins.clone import get_drive_service
from plugins.upload import upload_to_drive, format_size, format_speed
from pymongo import MongoClient
import os
import re
import asyncio
import time
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# MongoDB setup
MONGO_URI = os.getenv('MONGO_URI')
DB_NAME = os.getenv('DB_NAME')
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
users_collection = db['users']

# Progress update interval
PROGRESS_UPDATE_INTERVAL = 3

async def download_bongo(url, file_path, status_msg):
    """Download video from BongoBD"""
    try:
        # Get file name
        file_name = os.path.basename(file_path)
        logger.info(f"Starting download for: {file_name}")
        logger.info(f"URL: {url}")
        logger.info(f"Output path: {file_path}")
        
        # Create command matching the working Colab version
        cmd = [
            'yt-dlp',
            '--no-check-certificate',
            '--no-warnings',
            '--progress-template', 
            '[%(progress._percent_str)s] ⚡ %(progress._speed_str)s | 📥 %(progress._downloaded_bytes_str)s/%(progress._total_bytes_str)s | ⏱️ ETA: %(progress._eta_str)s',
            '--referer', 'https://bongobd.com/',
            '--add-header', 'Origin: https://bongobd.com/',
            '--concurrent-fragments', '10',
            '--buffer-size', '16K',
            '-N', '10',
            '-o', file_path,
            url
        ]
        
        logger.info(f"yt-dlp command: {' '.join(cmd)}")
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Variables for progress tracking
        last_update_time = 0
        
        # Monitor download progress
        stdout_lines = []
        stderr_lines = []
        
        # Read both stdout and stderr
        async def read_stdout():
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                output = line.decode().strip()
                stdout_lines.append(output)
                logger.info(f"STDOUT: {output}")
                
                # Update status based on progress
                current_time = time.time()
                if current_time - last_update_time >= PROGRESS_UPDATE_INTERVAL:
                    if '[' in output and '%' in output:
                        try:
                            # Extract progress info from the custom template
                            progress_text = f"📥 Downloading: {file_name}\n{output}"
                            await status_msg.edit_text(progress_text)
                            last_update_time = current_time
                        except Exception as e:
                            logger.warning(f"Error updating progress: {e}")
                
        async def read_stderr():
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                output = line.decode().strip()
                stderr_lines.append(output)
                logger.error(f"STDERR: {output}")
        
        # Start reading both streams
        await asyncio.gather(read_stdout(), read_stderr())
        
        # Wait for process to complete
        await process.wait()
        
        logger.info(f"Process exit code: {process.returncode}")
        
        # Log all output for debugging
        if stdout_lines:
            logger.info("=== FULL STDOUT ===")
            for line in stdout_lines:
                logger.info(line)
        
        if stderr_lines:
            logger.error("=== FULL STDERR ===")
            for line in stderr_lines:
                logger.error(line)
        
        # Check if download was successful
        if process.returncode == 0 and os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            logger.info(f"Download successful! File size: {file_size} bytes")
            return True
        else:
            logger.error(f"Download failed! Exit code: {process.returncode}")
            logger.error(f"File exists: {os.path.exists(file_path)}")
            if os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
                logger.error(f"File size: {file_size} bytes")
            
            # Create detailed error message
            error_msg = f"❌ Download failed!\n\n"
            error_msg += f"Exit code: {process.returncode}\n"
            if stderr_lines:
                error_msg += f"Error: {stderr_lines[-1][:200]}..."
            
            await status_msg.edit_text(error_msg)
            return False
        
    except Exception as e:
        logger.exception(f"Download error: {e}")
        await status_msg.edit_text(f"❌ Download error: {str(e)}")
        return False

async def cleanup(file_path, user_dir):
    """Clean up downloaded files"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up file: {file_path}")
        if os.path.exists(user_dir) and len(os.listdir(user_dir)) == 0:
            os.rmdir(user_dir)
            logger.info(f"Cleaned up directory: {user_dir}")
    except Exception as e:
        logger.error(f"Error in cleanup: {e}")

async def bdl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /bdl command"""
    try:
        logger.info(f"BDL command received from user {update.effective_user.id}")
        logger.info(f"Arguments: {context.args}")
        
        # Check command format
        if len(context.args) < 4:
            await update.message.reply_text(
                "❌ Invalid format!\n\n"
                "Use: /bdl <link> -n <filename> -d<number>\n"
                "Example: /bdl https://example.m3u8 -n Movie.mp4 -d1"
            )
            return

        # Create status message first
        status_msg = await update.message.reply_text("⏳ Starting download...")
        
        # Create task but don't block
        asyncio.create_task(
            process_bongo_download(update, context, status_msg)
        )
        
    except Exception as e:
        logger.exception(f"Error in bdl_command: {e}")
        await update.message.reply_text(f"❌ An error occurred: {str(e)}")

async def process_bongo_download(update: Update, context: ContextTypes.DEFAULT_TYPE, status_msg):
    """Process the actual download"""
    try:
        logger.info("Starting bdl process")
        
        # Check command format
        if len(context.args) < 4:
            error_msg = "❌ Invalid format!\n\nUse: /bdl <link> -n <filename> -d<number>\nExample: /bdl https://example.m3u8 -n Movie.mp4 -d1"
            await status_msg.edit_text(error_msg)
            logger.error("Invalid command format")
            return
            
        # Parse arguments
        url = context.args[0]
        logger.info(f"URL: {url}")
        
        try:
            name_index = context.args.index('-n')
            drive_flag = context.args[-1]
            filename = ' '.join(context.args[name_index + 1:-1])
            logger.info(f"Filename: {filename}")
            logger.info(f"Drive flag: {drive_flag}")
        except ValueError as e:
            await status_msg.edit_text("❌ Invalid format! Missing -n or filename")
            logger.error(f"Error parsing arguments: {e}")
            return
            
        # Get user's drive settings
        user_data = users_collection.find_one({"user_id": update.effective_user.id}) or {}
        logger.info(f"User data found: {len(user_data)} keys")
        
        # Check which drive to use
        target_folder = None
        drive_name = None
        if drive_flag.startswith('-d'):
            drive_num = drive_flag[2:]
            try:
                drive_num = int(drive_num)
                drive_key = f'drive_{drive_num:02d}'
                target_folder = user_data.get(drive_key)
                drive_name = user_data.get(f'{drive_key}_name', f'Drive {drive_num:02d}')
                logger.info(f"Drive {drive_num}: {drive_key} -> {target_folder}")
            except Exception as e:
                logger.error(f"Error parsing drive number: {e}")
                
        if not target_folder:
            await status_msg.edit_text(f"❌ Invalid or unset drive! Drive flag: {drive_flag}")
            logger.error(f"No target folder found for drive flag: {drive_flag}")
            return
            
        # Create user directory
        user_dir = os.path.join('downloads', str(update.effective_user.id))
        os.makedirs(user_dir, exist_ok=True)
        logger.info(f"Created user directory: {user_dir}")
        
        file_path = None
        
        try:
            file_path = os.path.join(user_dir, filename)
            logger.info(f"Full file path: {file_path}")
            
            # Check if yt-dlp is available
            import shutil
            if not shutil.which('yt-dlp'):
                await status_msg.edit_text("❌ yt-dlp not found! Please install it.")
                logger.error("yt-dlp not found in system PATH")
                return
            
            # Download video
            await status_msg.edit_text(f"📥 Starting download: {filename}")
            if not await download_bongo(url, file_path, status_msg):
                logger.error("Download failed")
                await cleanup(file_path, user_dir)
                return
            
            await status_msg.edit_text("⏳ Uploading to Google Drive...")
            logger.info("Starting upload to Google Drive")
            
            # Get Drive service
            service = get_drive_service("token.pickle")
            if not service:
                await status_msg.edit_text("❌ Drive service not available!")
                logger.error("Drive service not available")
                await cleanup(file_path, user_dir)
                return
            
            # Upload to Drive
            file = await upload_to_drive(service, file_path, target_folder, status_msg)
            if not file:
                await status_msg.edit_text("❌ Failed to upload to Drive!")
                logger.error("Failed to upload to Drive")
                await cleanup(file_path, user_dir)
                return
            
            # Clean up after successful upload
            await cleanup(file_path, user_dir)
            
            # Generate drive link
            file_id = file.get('id')
            drive_link = f"https://drive.google.com/file/d/{file_id}/view"
            logger.info(f"Upload successful! File ID: {file_id}")
            
            # Get file size
            file = service.files().get(
                fileId=file_id,
                fields='name, size',
                supportsAllDrives=True
            ).execute()

            file_size = await format_size(int(file.get('size', 0)))
            
            # Create view button
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 View File", url=drive_link)]
            ])
            
            # Send success message
            await status_msg.edit_text(
                "✅ File transferred successfully!\n\n"
                f"Name: <code>{file.get('name')}</code>\n"
                f"Size: {file_size}\n"
                f"Drive: {drive_name}\n"
                f"Link: <code>{drive_link}</code>",
                parse_mode='HTML',
                reply_markup=keyboard
            )
            logger.info("Process completed successfully")
            
        except Exception as e:
            logger.exception(f"Error in process_bongo_download: {e}")
            await status_msg.edit_text(f"❌ An error occurred: {str(e)}")
            if file_path:
                await cleanup(file_path, user_dir)
            
    except Exception as e:
        logger.exception(f"Error in process_bongo_download: {e}")
        await status_msg.edit_text(f"❌ An error occurred: {str(e)}") 