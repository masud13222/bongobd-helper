from telegram import Update
from telegram.ext import ContextTypes
import os
import aiohttp
import aiofiles
from googleapiclient.http import MediaFileUpload
from plugins.clone import get_drive_service
from plugins.upload import upload_to_drive, format_size, format_speed
from pymongo import MongoClient
import time
import asyncio

# MongoDB setup
MONGO_URI = os.getenv('MONGO_URI')
DB_NAME = os.getenv('DB_NAME')
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
users_collection = db['users']

# Progress update interval in seconds
PROGRESS_UPDATE_INTERVAL = 3  # Change this value to update progress faster or slower

async def download_file(url, file_path, status_msg):
    """Download file from direct link with progress"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return False
                    
                # Get file size and name
                file_size = int(response.headers.get('content-length', 0))
                file_name = os.path.basename(file_path)
                
                # Download with progress
                downloaded = 0
                start_time = time.time()
                last_update_time = 0
                
                async with aiofiles.open(file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(1024*1024):
                        await f.write(chunk)
                        downloaded += len(chunk)
                        
                        # Calculate speed and progress
                        current_time = time.time()
                        elapsed = current_time - start_time
                        speed = downloaded / elapsed if elapsed > 0 else 0
                        progress = (downloaded / file_size) * 100 if file_size > 0 else 0
                        
                        # Update status based on interval
                        if current_time - last_update_time >= PROGRESS_UPDATE_INTERVAL:
                            status_text = (
                                f"📥 Downloading: {file_name}\n"
                                f"Progress: {progress:.1f}%\n"
                                f"Size: {await format_size(downloaded)} / {await format_size(file_size)}\n"
                                f"Speed: {await format_speed(speed)}"
                            )
                            await status_msg.edit_text(status_text)
                            last_update_time = current_time
                            
        return True
    except Exception as e:
        print(f"Error downloading file: {e}")
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

async def direct_dl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /m command"""
    try:
        # Create task for download process
        task = asyncio.create_task(process_direct_download(update, context))
        await task
    except Exception as e:
        print(f"Error in direct_dl_command: {e}")
        await update.message.reply_text("❌ An error occurred!")

async def process_direct_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the actual download"""
    try:
        # Check if command has arguments
        if not context.args:
            await update.message.reply_text(
                "❌ Please provide direct link and drive number!\n\n"
                "Use: /m <direct_link> -d<number>"
            )
            return
            
        # Get direct link
        direct_link = context.args[0]
        
        # Get user's drive settings
        user_data = users_collection.find_one({"user_id": update.effective_user.id}) or {}
        
        # Check which drive to use
        target_folder = None
        drive_name = None
        if len(context.args) > 1:
            drive_flag = context.args[1].lower()
            for i in range(1, 7):
                drive_key = f'drive_{i:02d}'
                if drive_flag == f"-d{i}" and user_data.get(drive_key):
                    target_folder = user_data[drive_key]
                    drive_name = user_data.get(f'{drive_key}_name', f'Drive {i:02d}')
                    break
                    
        if not target_folder:
            await update.message.reply_text("❌ Invalid or unset drive!")
            return
            
        # Send initial message
        status_msg = await update.message.reply_text(
            "⏳ Starting download..."
        )
        
        # Create user directory
        user_dir = os.path.join('downloads', str(update.effective_user.id))
        os.makedirs(user_dir, exist_ok=True)
        file_path = None
        
        # Get filename from URL
        file_name = os.path.basename(direct_link.split('?')[0])
        
        # If filename is too long or same as URL, use a default name
        if len(file_name) > 100 or file_name == direct_link:
            file_name = f"downloaded_file_{int(time.time())}"
            
        # Try to get filename from Content-Disposition header
        async with aiohttp.ClientSession() as session:
            async with session.get(direct_link, allow_redirects=True) as response:
                content_disposition = response.headers.get('Content-Disposition')
                if content_disposition and 'filename=' in content_disposition:
                    try:
                        new_name = content_disposition.split('filename=')[1].strip('"')
                        if len(new_name) < 100:  # Only use if name is not too long
                            file_name = new_name
                    except:
                        pass
        
        file_path = os.path.join(user_dir, file_name)
        
        # Download file
        if not await download_file(direct_link, file_path, status_msg):
            await status_msg.edit_text("❌ Failed to download file!")
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
        
        # Send success message with button
        await status_msg.edit_text(
            "✅ File transferred successfully!\n\n"
            f"Name: <code>{file.get('name')}</code>\n"
            f"Drive: {drive_name}",
            parse_mode='HTML',
            reply_markup=keyboard
        )
        
    except Exception as e:
        print(f"Error in process_direct_download: {e}")
        await status_msg.edit_text("❌ An error occurred!")
        if file_path:
            await cleanup(file_path, user_dir) 