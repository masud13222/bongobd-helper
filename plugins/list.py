from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import os
from dotenv import load_dotenv
import pickle
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Load environment variables
load_dotenv()

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

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command"""
    try:
        # Check if search query provided
        if not context.args:
            await update.message.reply_text(
                "❌ Please provide a search query!\n\n"
                "Use: /list <search_query>"
            )
            return
            
        # Get search query
        query = ' '.join(context.args)
        
        # Get Drive service
        service = get_drive_service()
        if not service:
            await update.message.reply_text("❌ Error: Drive service not available")
            return
            
        try:
            # Search files
            page_token = context.user_data.get('page_token')
            
            # Build search query
            search_query = f"name contains '{query}' and trashed = false"
            
            # Get files
            results = service.files().list(
                q=search_query,
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token,
                pageSize=7,
                orderBy='name',
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora='allDrives'
            ).execute()
            
            items = results.get('files', [])
            next_page_token = results.get('nextPageToken')
            
            if not items:
                await update.message.reply_text("❌ No files found!")
                return
                
            # Build message text
            text = f"🔍 Search results for: <code>{query}</code>\n\n"
            
            for i, item in enumerate(items, 1):
                name = item.get('name', 'Unknown')
                file_id = item.get('id')
                mime_type = item.get('mimeType', 'Unknown')
                is_folder = mime_type == 'application/vnd.google-apps.folder'
                
                if is_folder:
                    link = f"https://drive.google.com/drive/folders/{file_id}"
                    text += f"{i}. 📁 <a href='{link}'>{name}</a>\n"
                else:
                    link = f"https://drive.google.com/file/d/{file_id}/view"
                    text += f"{i}. 📄 <a href='{link}'>{name}</a>\n"
                    
            # Build keyboard
            buttons = []
            if next_page_token:  # Has next page
                buttons.append(InlineKeyboardButton("Next ➡️", callback_data="next_page"))
                
            if page_token:  # Not first page
                buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data="prev_page"))
                
            buttons.append(InlineKeyboardButton("❌ Close", callback_data="close_search"))
            
            keyboard = InlineKeyboardMarkup([buttons])
            
            # Save tokens in user_data
            context.user_data['prev_token'] = page_token
            context.user_data['next_token'] = next_page_token
            context.user_data['search_query'] = query
            
            await update.message.reply_text(
                text,
                reply_markup=keyboard,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
            
        except Exception as e:
            await update.message.reply_text(
                f"❌ Error searching files:\n{str(e)}"
            )
            
    except Exception as e:
        print(f"Error in list_command: {e}")
        await update.message.reply_text("An error occurred. Please try again.")

async def handle_search_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle search pagination callbacks"""
    query = update.callback_query
    
    try:
        if query.data == "close_search":
            await query.message.delete()
            await query.answer()
            return
            
        # Get saved data
        search_query = context.user_data.get('search_query')
        
        if not search_query:
            await query.answer("❌ Search expired! Please search again.")
            await query.message.delete()
            return
            
        # Update page token based on button
        if query.data == "next_page":
            context.user_data['page_token'] = context.user_data.get('next_token')
        elif query.data == "prev_page":
            context.user_data['page_token'] = context.user_data.get('prev_token')
            
        # Get Drive service
        service = get_drive_service()
        if not service:
            await query.answer("❌ Error: Drive service not available")
            return
            
        try:
            # Build search query
            search_query_text = f"name contains '{search_query}' and trashed = false"
            
            # Get files
            results = service.files().list(
                q=search_query_text,
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=context.user_data.get('page_token'),
                pageSize=7,
                orderBy='name',
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora='allDrives'
            ).execute()
            
            items = results.get('files', [])
            next_page_token = results.get('nextPageToken')
            
            if not items:
                await query.answer("❌ No files found!")
                await query.message.delete()
                return
                
            # Build message text
            text = f"🔍 Search results for: <code>{search_query}</code>\n\n"
            
            for i, item in enumerate(items, 1):
                name = item.get('name', 'Unknown')
                file_id = item.get('id')
                mime_type = item.get('mimeType', 'Unknown')
                is_folder = mime_type == 'application/vnd.google-apps.folder'
                
                if is_folder:
                    link = f"https://drive.google.com/drive/folders/{file_id}"
                    text += f"{i}. 📁 <a href='{link}'>{name}</a>\n"
                else:
                    link = f"https://drive.google.com/file/d/{file_id}/view"
                    text += f"{i}. 📄 <a href='{link}'>{name}</a>\n"
                    
            # Build keyboard
            buttons = []
            if next_page_token:  # Has next page
                buttons.append(InlineKeyboardButton("Next ➡️", callback_data="next_page"))
                
            if context.user_data.get('page_token'):  # Not first page
                buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data="prev_page"))
                
            buttons.append(InlineKeyboardButton("❌ Close", callback_data="close_search"))
            
            keyboard = InlineKeyboardMarkup([buttons])
            
            # Save tokens
            context.user_data['prev_token'] = context.user_data.get('page_token')
            context.user_data['next_token'] = next_page_token
            
            # Edit message
            await query.message.edit_text(
                text,
                reply_markup=keyboard,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
            await query.answer()
            
        except Exception as e:
            print(f"Error in pagination: {e}")
            await query.answer("❌ Error occurred!")
            
    except Exception as e:
        print(f"Error in handle_search_callback: {e}")
        await query.answer("❌ Error occurred!") 