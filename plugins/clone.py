from telegram import Update
from telegram.ext import ContextTypes
from pymongo import MongoClient
import os
from dotenv import load_dotenv
import re
import pickle
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from plugins.check import check_drive_access

# Load environment variables
load_dotenv()

# MongoDB setup
MONGO_URI = os.getenv('MONGO_URI')
DB_NAME = os.getenv('DB_NAME')
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
users_collection = db['users']

def extract_id(url):
    """Extract file/folder ID from Google Drive URL"""
    patterns = [
        r'/file/d/([a-zA-Z0-9_-]+)',  # File link
        r'/folders/([a-zA-Z0-9_-]+)',  # Folder link
        r'drive/folders/([a-zA-Z0-9_-]+)'  # Alternative folder link
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
            
    # If no pattern matches, try to extract from query parameter
    if 'folders' in url:
        folder_id = url.split('folders/')[-1].split('?')[0]
        return folder_id
        
    if 'file/d' in url:
        file_id = url.split('file/d/')[-1].split('?')[0]
        return file_id
        
    return url

def get_drive_service(token_file):
    """Get Google Drive service for specific account"""
    creds = None
    if os.path.exists(f'private/{token_file}'):
        with open(f'private/{token_file}', 'rb') as token:
            creds = pickle.load(token)
            
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(f'private/{token_file}', 'wb') as token:
                pickle.dump(creds, token)
        else:
            return None
            
    return build('drive', 'v3', credentials=creds)

def clone_folder(service, folder_id, parent_id, folder_name=None):
    """Recursively clone a folder and its contents"""
    try:
        # Get source folder metadata
        folder = service.files().get(
            fileId=folder_id,
            fields='name, mimeType',
            supportsAllDrives=True
        ).execute()
        
        # Create new folder
        folder_metadata = {
            'name': folder_name or folder.get('name'),
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }
        
        new_folder = service.files().create(
            body=folder_metadata,
            supportsAllDrives=True
        ).execute()
        
        # Get items in the folder
        results = service.files().list(
            q=f"'{folder_id}' in parents",
            fields="files(id, name, mimeType)",
            supportsAllDrives=True
        ).execute()
        
        items = results.get('files', [])
        total_items = len(items)
        print(f"Found {total_items} items in folder")
        
        # Clone each item
        for i, item in enumerate(items, 1):
            try:
                if item['mimeType'] == 'application/vnd.google-apps.folder':
                    # Recursively clone subfolder
                    clone_folder(service, item['id'], new_folder['id'], item['name'])
                else:
                    # Copy file
                    file_metadata = {
                        'name': item['name'],
                        'parents': [new_folder['id']]
                    }
                    service.files().copy(
                        fileId=item['id'],
                        body=file_metadata,
                        supportsAllDrives=True
                    ).execute()
                print(f"Cloned {i}/{total_items}: {item['name']}")
            except Exception as e:
                print(f"Error cloning item {item['name']}: {e}")
                continue
                
        return new_folder['id']
        
    except Exception as e:
        print(f"Error cloning folder: {e}")
        return None

async def clone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clone or /c command"""
    try:
        # Check if command has arguments
        if not context.args:
            # Get user's drive settings to show available drives
            user_data = users_collection.find_one({"user_id": update.effective_user.id}) or {}
            
            # Build help message with available drives
            help_msg = "❌ Invalid format!\n\nUse:\n"
            for i in range(1, 7):  # Check all 6 drives
                drive_key = f'drive_{i:02d}'  # drive_01, drive_02, etc.
                if user_data.get(drive_key):
                    drive_name = user_data.get(f'{drive_key}_name', f'Drive {i:02d}')
                    help_msg += f"• /c <file_link> -d{i} - Clone to {drive_name}\n"
                
            if len(help_msg) == 27:  # Only has header
                help_msg += "\nNo drives set! Use /uset command to set drive folders first."
                
            await update.message.reply_text(help_msg)
            return
            
        # Get file ID from URL
        file_id = extract_id(context.args[0])
        
        # Get user's drive settings
        user_data = users_collection.find_one({"user_id": update.effective_user.id}) or {}
        
        # Check which drive to use
        target_folder = None
        drive_name = None
        if len(context.args) > 1:
            drive_flag = context.args[1].lower()
            # Check for drive flags d1 to d6
            for i in range(1, 7):
                drive_key = f'drive_{i:02d}'
                if drive_flag == f"-d{i}" and user_data.get(drive_key):
                    target_folder = user_data[drive_key]
                    drive_name = user_data.get(f'{drive_key}_name', f'Drive {i:02d}')
                    break
                    
            if not target_folder:
                # Show available drives
                help_msg = "❌ Invalid or unset drive!\n\nAvailable drives:\n"
                for i in range(1, 7):
                    drive_key = f'drive_{i:02d}'
                    if user_data.get(drive_key):
                        drive_name = user_data.get(f'{drive_key}_name', f'Drive {i:02d}')
                        help_msg += f"• -d{i} ({drive_name})\n"
                await update.message.reply_text(help_msg)
                return
        else:
            # Use first available drive
            for i in range(1, 7):
                drive_key = f'drive_{i:02d}'
                if user_data.get(drive_key):
                    target_folder = user_data[drive_key]
                    drive_name = user_data.get(f'{drive_key}_name', f'Drive {i:02d}')
                    break
                
        # Check if any drive is set
        if not target_folder:
            await update.message.reply_text(
                "❌ No drives set!\n\n"
                "Use /uset command to set drive folders first."
            )
            return
                
        # Get Drive service
        service = get_drive_service("token.pickle")
        if not service:
            await update.message.reply_text("❌ Error: Drive service not available")
            return
            
        try:
            # Get file metadata
            file = service.files().get(
                fileId=file_id,
                fields='name',
                supportsAllDrives=True
            ).execute()
            
            # Clone file
            copied_file = service.files().copy(
                fileId=file_id,
                body={
                    'name': file.get('name'),
                    'parents': [target_folder]
                },
                supportsAllDrives=True
            ).execute()
            
            await update.message.reply_text(
                "✅ File cloned successfully!\n\n"
                f"Name: <code>{file.get('name')}</code>\n"
                f"Drive: {drive_name}",
                parse_mode='HTML'
            )
            
        except Exception as e:
            await update.message.reply_text(
                f"❌ Error: Could not clone file\n"
                f"Reason: {str(e)}"
            )
            
    except Exception as e:
        print(f"Error in clone_command: {e}")
        await update.message.reply_text("An error occurred. Please try again.") 