from telegram import Update
from telegram.ext import ContextTypes
import os
from googleapiclient.http import MediaFileUpload
from plugins.clone import get_drive_service
from pymongo import MongoClient
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# MongoDB setup
MONGO_URI = os.getenv('MONGO_URI')
DB_NAME = os.getenv('DB_NAME')
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
users_collection = db['users']

# Progress update interval in seconds
PROGRESS_UPDATE_INTERVAL = 3  # Change this value to update progress faster or slower

async def format_size(size):
    """Format size in bytes to human readable"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

async def format_speed(speed):
    """Format speed in bytes/sec to human readable"""
    return await format_size(speed) + "/s"

async def upload_to_drive(service, file_path, folder_id, status_msg):
    """Upload file to Google Drive with progress"""
    try:
        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        
        file_metadata = {
            'name': file_name,
            'parents': [folder_id]
        }
        
        media = MediaFileUpload(
            file_path,
            resumable=True,
            chunksize=1024*1024
        )
        
        # Create drive file
        request = service.files().create(
            body=file_metadata,
            media_body=media,
            supportsAllDrives=True,
            fields='id, name'
        )
        
        # Upload with progress
        response = None
        uploaded = 0
        start_time = time.time()
        last_update_time = 0
        
        while response is None:
            status, response = request.next_chunk()
            if status:
                uploaded = status.resumable_progress
                current_time = time.time()
                elapsed = current_time - start_time
                speed = uploaded / elapsed if elapsed > 0 else 0
                progress = (uploaded / file_size) * 100
                
                # Update status based on interval
                if current_time - last_update_time >= PROGRESS_UPDATE_INTERVAL:
                    status_text = (
                        f"📤 Uploading: {file_name}\n"
                        f"Progress: {progress:.1f}%\n"
                        f"Size: {await format_size(uploaded)} / {await format_size(file_size)}\n"
                        f"Speed: {await format_speed(speed)}"
                    )
                    await status_msg.edit_text(status_text)
                    last_update_time = current_time
        
        return response
    except Exception as e:
        print(f"Error uploading to Drive: {e}")
        return None

async def cleanup(file_path, user_dir):
    """Clean up uploaded files"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        if os.path.exists(user_dir):
            os.rmdir(user_dir)
    except Exception as e:
        print(f"Error in cleanup: {e}")

async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /upload command"""
    try:
        # Check if file is attached
        if not update.message.document:
            await update.message.reply_text(
                "❌ Please send a file with the command!\n\n"
                "Use: /upload -d<number>"
            )
            return
            
        # Get user's drive settings
        user_data = users_collection.find_one({"user_id": update.effective_user.id}) or {}
        
        # Check which drive to use
        target_folder = None
        drive_name = None
        if context.args:
            drive_flag = context.args[0].lower()
            for i in range(1, 7):
                drive_key = f'drive_{i:02d}'
                if drive_flag == f"-d{i}" and user_data.get(drive_key):
                    target_folder = user_data[drive_key]
                    drive_name = user_data.get(f'{drive_key}_name', f'Drive {i:02d}')
                    break
                    
        if not target_folder:
            await update.message.reply_text("❌ Invalid or unset drive!")
            return
            
        # Create user directory
        user_dir = os.path.join('downloads', str(update.effective_user.id))
        os.makedirs(user_dir, exist_ok=True)
        file_path = None
        
        try:
            # Download file
            file = await context.bot.get_file(update.message.document.file_id)
            file_name = update.message.document.file_name
            file_path = os.path.join(user_dir, file_name)
            
            # Send status
            status_msg = await update.message.reply_text("⏳ Downloading file...")
            
            # Download
            await file.download_to_drive(file_path)
            
            await status_msg.edit_text("⏳ Uploading to Google Drive...")
            
            # Get Drive service
            service = get_drive_service("token.pickle")
            if not service:
                await status_msg.edit_text("❌ Drive service not available!")
                return
            
            # Upload file
            response = await upload_to_drive(service, file_path, target_folder, status_msg)
            
            if response:
                # Clean up
                await cleanup(file_path, user_dir)
                
                # Generate drive link
                file_id = response.get('id')
                drive_link = f"https://drive.google.com/file/d/{file_id}/view"
                
                # Create view button
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 View File", url=drive_link)]
                ])
                
                # Send success message with button
                await status_msg.edit_text(
                    "✅ File transferred successfully!\n\n"
                    f"Name: <code>{response.get('name')}</code>\n"
                    f"Drive: {drive_name}",
                    parse_mode='HTML',
                    reply_markup=keyboard
                )
            else:
                await status_msg.edit_text("❌ Upload failed!")
            
        except Exception as e:
            print(f"Error in upload_command: {e}")
            await status_msg.edit_text("❌ An error occurred!")
            if file_path:
                await cleanup(file_path, user_dir)
            
    except Exception as e:
        print(f"Error in upload_command: {e}")
        await update.message.reply_text("❌ An error occurred!") 