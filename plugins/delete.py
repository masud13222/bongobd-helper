from telegram import Update
from telegram.ext import ContextTypes
from plugins.clone import get_drive_service
from pymongo import MongoClient
import os
import re
from googleapiclient.errors import HttpError

# MongoDB setup
MONGO_URI = os.getenv('MONGO_URI')
DB_NAME = os.getenv('DB_NAME')
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
users_collection = db['users']

def extract_file_id(url):
    """Extract file ID from Google Drive URL"""
    patterns = [
        r'/file/d/([a-zA-Z0-9_-]+)',  # File link
        r'/folders/([a-zA-Z0-9_-]+)',  # Folder link
        r'id=([a-zA-Z0-9_-]+)',  # Open link
        r'drive/folders/([a-zA-Z0-9_-]+)',  # Alternative folder link
        r'^([a-zA-Z0-9_-]+)$'  # Direct ID
    ]
    
    # Clean the URL
    url = url.strip()
    
    # Try each pattern
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    # If no pattern matches, try to extract from various URL formats
    if 'folders' in url:
        folder_id = url.split('folders/')[-1].split('?')[0].split('/')[0]
        return folder_id
        
    if 'file/d' in url:
        file_id = url.split('file/d/')[-1].split('?')[0].split('/')[0]
        return file_id
        
    if 'open?id=' in url:
        file_id = url.split('open?id=')[-1].split('&')[0]
        return file_id
    
    # If nothing works, return cleaned URL
    return url.split('?')[0].split('&')[0]

async def del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /del command"""
    try:
        if not context.args:
            await update.message.reply_text(
                "❌ Please provide a Drive link!\n\n"
                "Use: /del <drive_link>"
            )
            return
            
        # Get drive link
        drive_link = context.args[0]
        
        # Extract file ID
        file_id = extract_file_id(drive_link)
        if not file_id:
            await update.message.reply_text("❌ Invalid Drive link!")
            return
            
        # Get Drive service
        service = get_drive_service("token.pickle")
        if not service:
            await update.message.reply_text("❌ Drive service not available!")
            return
            
        # Send initial message
        status_msg = await update.message.reply_text("⏳ Deleting file...")
        
        try:
            # First try to get file info to check permissions
            try:
                file = service.files().get(
                    fileId=file_id,
                    fields='name, parents',
                    supportsAllDrives=True
                ).execute()
                print(f"Found file: {file.get('name')}")
            except Exception as e:
                print(f"Error getting file info: {e}")
                await status_msg.edit_text(
                    "❌ Failed to access file!\n"
                    "Make sure:\n"
                    "1. The file exists\n"
                    "2. You have permission to access it"
                )
                return
                
            # Try to delete the file
            service.files().delete(
                fileId=file_id,
                supportsAllDrives=True
            ).execute()
            
            await status_msg.edit_text(
                "✅ File deleted successfully!\n\n"
                f"Name: <code>{file.get('name', 'Unknown')}</code>",
                parse_mode='HTML'
            )
            
        except Exception as e:
            print(f"Error deleting file: {e}")
            error_msg = str(e).lower()
            
            if "file not found" in error_msg:
                msg = "File not found or already deleted"
            elif "permission" in error_msg:
                msg = "You don't have permission to delete this file"
            else:
                msg = str(e)
                
            await status_msg.edit_text(
                f"❌ Failed to delete file!\n"
                f"Error: {msg}"
            )
            
    except Exception as e:
        print(f"Error in del_command: {e}")
        await update.message.reply_text("❌ An error occurred!") 