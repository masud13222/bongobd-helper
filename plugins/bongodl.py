from telegram import Update
from telegram.ext import ContextTypes
from plugins.clone import get_drive_service
from plugins.upload import upload_to_drive, format_size, format_speed
from pymongo import MongoClient
import os
import re
import asyncio
import time

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
        
        # Create command
        cmd = [
            'yt-dlp',
            '--no-check-certificate',
            '--no-warnings',
            '--newline',  # Important for progress parsing
            '--progress',
            '--referer', 'https://bongobd.com/',
            '--add-header', 'Origin: https://bongobd.com/',
            '--concurrent-fragments', '10',
            '--buffer-size', '16K',
            '-N', '10',
            '-o', file_path,
            url
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Variables for progress tracking
        downloaded = 0
        total_size = 0
        start_time = time.time()
        last_update_time = 0
        
        # Monitor download progress
        while True:
            line = await process.stdout.readline()
            if not line:
                break
                
            output = line.decode().strip()
            
            # Parse download progress
            if '[download]' in output:
                try:
                    # Extract progress info
                    if 'Downloading' in output:
                        # Starting download
                        await status_msg.edit_text(f"📥 Starting download: {file_name}")
                    elif '%' in output:
                        # Get progress percentage
                        percent = float(output.split('%')[0].split()[-1])
                        
                        # Get downloaded size
                        size_parts = output.split('of')[1].strip().split('at')
                        downloaded = size_parts[0].strip()
                        
                        # Get speed
                        speed = size_parts[1].split()[0]
                        
                        # Get ETA
                        eta = output.split('ETA')[1].strip()
                        
                        # Update status based on interval
                        current_time = time.time()
                        if current_time - last_update_time >= PROGRESS_UPDATE_INTERVAL:
                            status_text = (
                                f"📥 Downloading: {file_name}\n"
                                f"Progress: {percent:.1f}%\n"
                                f"Size: {downloaded}\n"
                                f"Speed: {speed}\n"
                                f"ETA: {eta}"
                            )
                            await status_msg.edit_text(status_text)
                            last_update_time = current_time
                            
                except Exception as e:
                    print(f"Error parsing progress: {e}")
                    continue
                    
        await process.wait()
        
        if os.path.exists(file_path):
            return True
        return False
        
    except Exception as e:
        print(f"Download error: {e}")
        return False

async def cleanup(file_path, user_dir):
    """Clean up downloaded files"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        if os.path.exists(user_dir):
            os.rmdir(user_dir)
    except Exception as e:
        print(f"Error in cleanup: {e}")

async def bdl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /bdl command"""
    try:
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
        print(f"Error in bdl_command: {e}")
        await update.message.reply_text("❌ An error occurred!")

async def process_bongo_download(update: Update, context: ContextTypes.DEFAULT_TYPE, status_msg):
    """Process the actual download"""
    try:
        # Check command format
        if len(context.args) < 4:
            await update.message.reply_text(
                "❌ Invalid format!\n\n"
                "Use: /bdl <link> -n <filename> -d<number>\n"
                "Example: /bdl https://example.m3u8 -n Movie.mp4 -d1"
            )
            return
            
        # Parse arguments
        url = context.args[0]
        
        try:
            name_index = context.args.index('-n')
            drive_flag = context.args[-1]
            filename = ' '.join(context.args[name_index + 1:-1])
        except ValueError:
            await update.message.reply_text("❌ Invalid format! Missing -n or filename")
            return
            
        # Get user's drive settings
        user_data = users_collection.find_one({"user_id": update.effective_user.id}) or {}
        
        # Check which drive to use
        target_folder = None
        drive_name = None
        if drive_flag.startswith('-d'):
            drive_num = drive_flag[2:]
            try:
                drive_num = int(drive_num)
                drive_key = f'drive_{drive_num:02d}'
                if user_data.get(drive_key):
                    target_folder = user_data[drive_key]
                    drive_name = user_data.get(f'{drive_key}_name', f'Drive {drive_num:02d}')
            except:
                pass
                
        if not target_folder:
            await update.message.reply_text("❌ Invalid or unset drive!")
            return
            
        # Create user directory
        user_dir = os.path.join('downloads', str(update.effective_user.id))
        os.makedirs(user_dir, exist_ok=True)
        file_path = None
        
        try:
            file_path = os.path.join(user_dir, filename)
            
            # Download video
            if not await download_bongo(url, file_path, status_msg):
                await status_msg.edit_text("❌ Download failed!")
                await cleanup(file_path, user_dir)
                return
            
            await status_msg.edit_text("⏳ Uploading to Google Drive...")
            
            # Get Drive service
            service = get_drive_service("token.pickle")
            if not service:
                await status_msg.edit_text("❌ Drive service not available!")
                await cleanup(file_path, user_dir)
                return
            
            # Upload to Drive
            file = await upload_to_drive(service, file_path, target_folder, status_msg)
            if not file:
                await status_msg.edit_text("❌ Failed to upload to Drive!")
                await cleanup(file_path, user_dir)
                return
            
            # Clean up after successful upload
            await cleanup(file_path, user_dir)
            
            # Generate drive link
            file_id = file.get('id')
            drive_link = f"https://drive.google.com/file/d/{file_id}/view"
            
            # Create view button
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 View File", url=drive_link)]
            ])
            
            # Send success message
            await status_msg.edit_text(
                "✅ File transferred successfully!\n\n"
                f"Name: <code>{file.get('name')}</code>\n"
                f"Drive: {drive_name}",
                parse_mode='HTML',
                reply_markup=keyboard
            )
            
        except Exception as e:
            print(f"Error in bdl_command: {e}")
            await status_msg.edit_text("❌ An error occurred!")
            if file_path:
                await cleanup(file_path, user_dir)
            
    except Exception as e:
        print(f"Error in bdl_command: {e}")
        await update.message.reply_text("❌ An error occurred!") 