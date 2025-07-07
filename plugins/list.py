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
            # Clear previous search data to start fresh
            context.user_data.pop('page_token', None)
            context.user_data.pop('prev_tokens', None)
            context.user_data.pop('current_page', None)
            
            # Build search query
            search_query = f"name contains '{query}' and trashed = false"
            
            # Get files - start fresh without pageToken
            results = service.files().list(
                q=search_query,
                fields="nextPageToken, files(id, name, mimeType)",
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
                
            buttons.append(InlineKeyboardButton("❌ Close", callback_data="close_search"))
            
            keyboard = InlineKeyboardMarkup([buttons])
            
            # Save tokens and search info in user_data with better structure
            context.user_data['search_query'] = query
            context.user_data['current_page'] = 0
            context.user_data['prev_tokens'] = []  # Stack of previous page tokens
            context.user_data['next_token'] = next_page_token
            
            await update.message.reply_text(
                text,
                reply_markup=keyboard,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
            
        except Exception as e:
            error_msg = str(e)
            if "Invalid Value" in error_msg and "pageToken" in error_msg:
                await update.message.reply_text(
                    "❌ Search session expired. Please try your search again."
                )
            else:
                await update.message.reply_text(
                    f"❌ Error searching files:\n{error_msg}"
                )
            
    except Exception as e:
        print(f"Error in list_command: {e}")
        await update.message.reply_text("An error occurred. Please try again.")

async def handle_search_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle search pagination callbacks"""
    query = update.callback_query
    
    try:
        if query.data == "close_search":
            # Clear search data
            context.user_data.pop('search_query', None)
            context.user_data.pop('current_page', None)
            context.user_data.pop('prev_tokens', None)
            context.user_data.pop('next_token', None)
            await query.message.delete()
            await query.answer()
            return
            
        # Get saved data
        search_query = context.user_data.get('search_query')
        current_page = context.user_data.get('current_page', 0)
        prev_tokens = context.user_data.get('prev_tokens', [])
        next_token = context.user_data.get('next_token')
        
        if not search_query:
            await query.answer("❌ Search expired! Please search again.")
            await query.message.delete()
            return
            
        # Get Drive service
        service = get_drive_service()
        if not service:
            await query.answer("❌ Error: Drive service not available")
            return
            
        try:
            # Determine which page token to use
            page_token = None
            new_page = current_page
            
            if query.data == "next_page":
                if next_token:
                    # Save current position before moving forward
                    if current_page == 0:
                        prev_tokens.append(None)  # First page has no token
                    else:
                        # This shouldn't happen in normal flow, but just in case
                        pass
                    page_token = next_token
                    new_page = current_page + 1
                else:
                    await query.answer("No more pages available")
                    return
                    
            elif query.data == "prev_page":
                if current_page > 0 and len(prev_tokens) >= current_page:
                    new_page = current_page - 1
                    if new_page == 0:
                        page_token = None  # First page
                    else:
                        # For going back multiple pages, we'd need more complex logic
                        # For now, let's restart the search
                        page_token = None
                        new_page = 0
                else:
                    await query.answer("Already at first page")
                    return
                    
            # Build search query
            search_query_text = f"name contains '{search_query}' and trashed = false"
            
            # Get files
            results = service.files().list(
                q=search_query_text,
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
                await query.answer("❌ No files found!")
                await query.message.delete()
                return
                
            # Build message text
            text = f"🔍 Search results for: <code>{search_query}</code>\n"
            if new_page > 0:
                text += f"(Page {new_page + 1})\n"
            text += "\n"
            
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
                
            if new_page > 0:  # Not first page
                buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data="prev_page"))
                
            buttons.append(InlineKeyboardButton("❌ Close", callback_data="close_search"))
            
            keyboard = InlineKeyboardMarkup([buttons])
            
            # Update context data
            context.user_data['current_page'] = new_page
            context.user_data['next_token'] = next_page_token
            if query.data == "next_page":
                context.user_data['prev_tokens'] = prev_tokens
            
            # Edit message
            await query.message.edit_text(
                text,
                reply_markup=keyboard,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
            await query.answer()
            
        except Exception as e:
            error_msg = str(e)
            print(f"Error in pagination: {e}")
            if "Invalid Value" in error_msg and "pageToken" in error_msg:
                await query.answer("❌ Search session expired. Please start a new search.")
                await query.message.delete()
                # Clear search data
                context.user_data.pop('search_query', None)
                context.user_data.pop('current_page', None)
                context.user_data.pop('prev_tokens', None)
                context.user_data.pop('next_token', None)
            else:
                await query.answer("❌ Error occurred!")
            
    except Exception as e:
        print(f"Error in handle_search_callback: {e}")
        await query.answer("❌ Error occurred!") 