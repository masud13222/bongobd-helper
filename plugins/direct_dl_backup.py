from telegram import Update
from telegram.ext import ContextTypes
import os
import requests
from googleapiclient.http import MediaFileUpload
from plugins.clone import get_drive_service
from plugins.upload import upload_to_drive, format_size, format_speed
from pymongo import MongoClient
import time
import asyncio
import re
from urllib.parse import unquote

# MongoDB setup
MONGO_URI = os.getenv('MONGO_URI')
DB_NAME = os.getenv('DB_NAME')
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
users_collection = db['users']

# Progress update interval in seconds
PROGRESS_UPDATE_INTERVAL = 3

async def download_file_requests(url, file_path, status_msg):
    """Download file using requests (fallback)"""
    try:
        # Create downloads directory if not exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # Use requests with stream=True for large files
        response = requests.get(url, stream=True, timeout=120)
        response.raise_for_status()
        
        # Get file size and name
        file_size = int(response.headers.get('content-length', 0))
        file_name = os.path.basename(file_path)
        
        # Download with progress
        downloaded = 0
        start_time = time.time()
        last_update_time = 0
        
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192*1024):  # 8MB chunks
                if chunk:
                    f.write(chunk)
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
        # Remove file if exists
        if os.path.exists(file_path):
            os.remove(file_path)
            
        # Remove user directory if empty
        user_dir = os.path.dirname(file_path)
        if os.path.exists(user_dir) and not os.listdir(user_dir):
            os.rmdir(user_dir)
            
        # Remove downloads directory if empty
        downloads_dir = os.path.dirname(user_dir)
        if os.path.exists(downloads_dir) and not os.listdir(downloads_dir):
            os.rmdir(downloads_dir)
            
    except Exception as e:
        print(f"Error in cleanup: {e}")

async def direct_dl_command_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct download using requests library"""
    try:
        if not context.args:
            await update.message.reply_text(
                "❌ Please provide direct link and drive number!\n\n"
                "Use: /mirror <direct_link> -d<number>"
            )
            return

        # Create status message
        status_msg = await update.message.reply_text("⏳ Starting download...")
        
        # Get direct link
        direct_link = context.args[0]
        
        # Get user's drive settings
        user_data = users_collection.find_one({"user_id": update.effective_user.id}) or {}
        
        # Check which drive to use
        target_folder = None
        drive_name = None
        if len(context.args) > 1:
            drive_flag = context.args[1].lower()
            for i in range(1, 11):  # Support 10 drives
                drive_key = f'drive_{i:02d}'
                if drive_flag == f"-d{i}" and user_data.get(drive_key):
                    target_folder = user_data[drive_key]
                    drive_name = user_data.get(f'{drive_key}_name', f'Drive {i:02d}')
                    break
                    
        if not target_folder:
            await status_msg.edit_text("❌ Invalid or unset drive!")
            return
            
        # Get filename from URL
        file_name = unquote(os.path.basename(direct_link.split('?')[0]))
        if len(file_name) > 100 or file_name == direct_link:
            file_name = f"downloaded_file_{int(time.time())}"
            
        file_path = os.path.join('downloads', str(update.effective_user.id), file_name)
        
        # Download file
        if not await download_file_requests(direct_link, file_path, status_msg):
            await status_msg.edit_text("❌ Failed to download file!")
            await cleanup(file_path, 'downloads')
            return
                
        await status_msg.edit_text("⏳ Uploading to Google Drive...")
        
        # Get Drive service
        service = get_drive_service("token.pickle")
        if not service:
            await status_msg.edit_text("❌ Drive service not available!")
            await cleanup(file_path, 'downloads')
            return
            
        # Upload to Drive
        file = await upload_to_drive(service, file_path, target_folder, status_msg)
        if not file:
            await status_msg.edit_text("❌ Failed to upload to Drive!")
            await cleanup(file_path, 'downloads')
            return
            
        # Clean up after successful upload
        await cleanup(file_path, 'downloads')
        
        # Generate drive link
        file_id = file.get('id')
        drive_link = f"https://drive.google.com/file/d/{file_id}/view"
        
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
            
    except Exception as e:
        print(f"Error in direct_dl_command: {e}")
        await update.message.reply_text("❌ An error occurred!")