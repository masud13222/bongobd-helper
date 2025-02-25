from telegram import Update
from telegram.ext import ContextTypes
from pymongo import MongoClient
import os
from dotenv import load_dotenv
import re
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle

# Load environment variables
load_dotenv()

# MongoDB setup
client = MongoClient(os.getenv('MONGO_URI'))
db = client[os.getenv('DB_NAME')]
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

def get_drive_service():
    """Get Google Drive service"""
    creds = None
    if os.path.exists('private/token.pickle'):
        with open('private/token.pickle', 'rb') as token:
            creds = pickle.load(token)
            
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open('private/token.pickle', 'wb') as token:
                pickle.dump(creds, token)
        else:
            return None
            
    return build('drive', 'v3', credentials=creds)

async def rename_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /rename or /r command"""
    try:
        # Check if command has arguments
        if not context.args:
            await update.message.reply_text(
                "❌ Invalid format!\n\n"
                "Use:\n"
                "• /r <file_id/link> - Auto rename with prefix/suffix\n"
                "• /r <file_id/link> <new_name> -o - Rename without prefix/suffix"
            )
            return
            
        # Get file ID from URL or direct ID
        file_id = extract_file_id(context.args[0])
        
        # Get Drive service and original file info first
        service = get_drive_service()
        if not service:
            await update.message.reply_text("❌ Error: Drive service not available")
            return
            
        try:
            # Get original file info
            file = service.files().get(
                fileId=file_id,
                fields='name',
                supportsTeamDrives=True
            ).execute()
            
            old_name = file.get('name', 'Unknown')
            old_ext = old_name.split('.')[-1] if '.' in old_name else ''
            
            # Get user settings
            user_data = users_collection.find_one({"user_id": update.effective_user.id}) or {}
            prefix = user_data.get('prefix', '')
            suffix = user_data.get('suffix', '')
            remnames = user_data.get('remnames', [])
            
            # Default to normal mode
            override_mode = False
            
            # If only file link provided, use original name
            if len(context.args) == 1:
                new_name = old_name.rsplit('.', 1)[0] if '.' in old_name else old_name
            else:
                # Check if -o flag is present
                if context.args[-1].lower() == '-o':
                    override_mode = True
                    new_name = ' '.join(context.args[1:-1])
                else:
                    new_name = ' '.join(context.args[1:])
                    
                # Remove extension if present in new name
                if '.' in new_name:
                    new_name = new_name.rsplit('.', 1)[0]
            
            # Process name with prefix/suffix if not in override mode
            if not override_mode:
                new_name = old_name if len(context.args) == 1 else ' '.join(context.args[1:])
                
                # First check and remove any matching remnames
                if remnames:
                    # Sort remnames by length (longest first) to avoid partial matches
                    remnames.sort(key=len, reverse=True)
                    
                    for remname in remnames:
                        # Simple exact string match and remove
                        if remname in new_name:
                            new_name = new_name.replace(remname, "")
                
                # Then add prefix if exists
                if prefix:
                    new_name = f"{prefix} - {new_name}"
                    
                # Finally add suffix if exists
                if suffix:
                    # Get name without extension
                    name_part = new_name.rsplit('.', 1)[0] if '.' in new_name else new_name
                    ext = new_name.rsplit('.', 1)[1] if '.' in new_name else ''
                    
                    # Add suffix before extension
                    new_name = f"{name_part} {suffix}"
                    if ext:
                        new_name = f"{new_name}.{ext}"
            else:
                # In override mode, preserve original extension
                new_name = f"{new_name}.{old_ext}" if old_ext else new_name
            
            # Clean up multiple spaces at the end
            new_name = re.sub(r'\s+', ' ', new_name)
            
            # Update file metadata
            service.files().update(
                fileId=file_id,
                body={'name': new_name},
                supportsTeamDrives=True
            ).execute()
            
            await update.message.reply_text(
                "✅ File renamed successfully!\n\n"
                f"Old name: <code>{old_name}</code>\n"
                f"New name: <code>{new_name}</code>",
                parse_mode='HTML'
            )
            
        except Exception as e:
            await update.message.reply_text(
                f"❌ Error: Could not rename file\n"
                f"Reason: {str(e)}"
            )
            
    except Exception as e:
        print(f"Error in rename_command: {e}")
        await update.message.reply_text("An error occurred. Please try again.") 