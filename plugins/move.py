from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
from plugins.clone import get_drive_service
from pymongo import MongoClient
import os
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

def get_drive_status(drive_num, user_data):
    """Get status emoji for drive"""
    drive_key = f"drive_{drive_num}"
    if user_data.get(drive_key):
        return "✅"
    return "⭕"

def get_name_status(prefix_key, user_data):
    """Get status emoji for prefix/suffix"""
    if user_data.get(prefix_key):
        return " ✅"
    return " ⭕"

async def move_files_from_folder(service, source_folder_id, dest_folder_id, status_msg, moved_count=[0]):
    """Move files from source folder to destination folder"""
    try:
        # Get all files in source folder
        query = f"'{source_folder_id}' in parents and trashed=false"
        results = service.files().list(
            q=query,
            fields='nextPageToken, files(id, name, mimeType, parents)',
            pageSize=1000,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        
        items = results.get('files', [])
        
        for item in items:
            try:
                file_id = item['id']
                file_name = item['name']
                current_parents = item.get('parents', [])
                
                # Move file to destination folder
                if current_parents:
                    # Remove from current parent and add to destination
                    previous_parents = ','.join(current_parents)
                    service.files().update(
                        fileId=file_id,
                        addParents=dest_folder_id,
                        removeParents=previous_parents,
                        supportsAllDrives=True
                    ).execute()
                    
                    moved_count[0] += 1
                    logger.info(f"Moved file: {file_name} (ID: {file_id})")
                    
                    # Update status every 10 files
                    if moved_count[0] % 10 == 0:
                        try:
                            await status_msg.edit_text(f"🔄 Moving files...\n\n📁 Files moved: {moved_count[0]}")
                        except Exception as e:
                            logger.warning(f"Error updating status: {e}")
                            
                # If it's a folder, recursively move its contents
                if item.get('mimeType') == 'application/vnd.google-apps.folder':
                    await move_files_from_folder(service, file_id, dest_folder_id, status_msg, moved_count)
                    
            except Exception as e:
                logger.error(f"Error moving file {item.get('name', 'Unknown')}: {e}")
                continue
                
    except Exception as e:
        logger.error(f"Error listing files from folder {source_folder_id}: {e}")
        
    return moved_count[0]

async def move_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /m command for moving files"""
    try:
        logger.info(f"Move command received from user {update.effective_user.id}")
        
        # Get user data
        user_data = users_collection.find_one({"user_id": update.effective_user.id}) or {}
        
        if len(context.args) < 2:
            # Show help message
            help_text = (
                "🔄 **Move Files Command**\n\n"
                "**Usage:** `/m <source_folder_link> <destination_drive_number>`\n\n"
                "**Examples:**\n"
                "• `/m https://drive.google.com/drive/folders/SOURCE_ID 1`\n"
                "• `/m SOURCE_FOLDER_ID 2`\n\n"
                "This will move all files from the source folder to your selected drive.\n\n"
                "**Note:** You must have access to the source folder and destination drive must be set in `/uset`."
            )
            await update.message.reply_text(help_text, parse_mode='Markdown')
            return
            
        source_input = context.args[0]
        dest_drive_num = context.args[1]
        
        # Extract folder ID from URL or use as-is if it's already an ID
        if 'drive.google.com' in source_input:
            # Extract folder ID from URL
            if '/folders/' in source_input:
                source_folder_id = source_input.split('/folders/')[1].split('?')[0].split('/')[0]
            else:
                await update.message.reply_text("❌ Invalid Google Drive folder URL!")
                return
        else:
            source_folder_id = source_input
            
        # Validate destination drive
        try:
            drive_num = int(dest_drive_num)
            if drive_num < 1 or drive_num > 10:
                await update.message.reply_text("❌ Drive number must be between 1 and 10!")
                return
        except ValueError:
            await update.message.reply_text("❌ Invalid drive number!")
            return
            
        # Get destination folder
        drive_key = f'drive_{drive_num:02d}'
        dest_folder_id = user_data.get(drive_key)
        drive_name = user_data.get(f'{drive_key}_name', f'Drive {drive_num:02d}')
        
        if not dest_folder_id:
            await update.message.reply_text(f"❌ Drive {drive_num} is not set! Use `/uset` to configure drives first.")
            return
            
        # Create status message
        status_msg = await update.message.reply_text("🔄 Starting file move operation...\n\n⏳ Initializing...")
        
        # Get Drive service
        service = get_drive_service("token.pickle")
        if not service:
            await status_msg.edit_text("❌ Google Drive service not available!")
            return
            
        try:
            # Verify source folder exists and is accessible
            source_folder = service.files().get(
                fileId=source_folder_id,
                fields='name, mimeType',
                supportsAllDrives=True
            ).execute()
            
            source_name = source_folder.get('name', 'Unknown Folder')
            
            if source_folder.get('mimeType') != 'application/vnd.google-apps.folder':
                await status_msg.edit_text("❌ Source must be a folder!")
                return
                
        except Exception as e:
            logger.error(f"Error accessing source folder: {e}")
            await status_msg.edit_text("❌ Cannot access source folder! Check permissions and folder ID.")
            return
            
        try:
            # Verify destination folder exists
            dest_folder = service.files().get(
                fileId=dest_folder_id,
                fields='name',
                supportsAllDrives=True
            ).execute()
            
        except Exception as e:
            logger.error(f"Error accessing destination folder: {e}")
            await status_msg.edit_text("❌ Cannot access destination folder!")
            return
            
        # Start moving files
        await status_msg.edit_text(f"🔄 Moving files...\n\n📂 Source: {source_name}\n📁 Destination: {drive_name}\n\n⏳ Please wait...")
        
        # Move all files from source to destination
        total_moved = await move_files_from_folder(service, source_folder_id, dest_folder_id, status_msg)
        
        # Final success message
        await status_msg.edit_text(
            f"✅ **Move operation completed!**\n\n"
            f"📂 **Source:** {source_name}\n"
            f"📁 **Destination:** {drive_name}\n"
            f"📊 **Files moved:** {total_moved}\n\n"
            f"🔗 **Destination Link:** https://drive.google.com/drive/folders/{dest_folder_id}"
        )
        
        logger.info(f"Move operation completed. Moved {total_moved} files from {source_folder_id} to {dest_folder_id}")
        
    except Exception as e:
        logger.exception(f"Error in move_command: {e}")
        try:
            await update.message.reply_text(f"❌ An error occurred: {str(e)}")
        except:
            pass

async def show_move_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show interactive move menu"""
    try:
        user_data = users_collection.find_one({"user_id": update.effective_user.id}) or {}
        
        # Create keyboard with drive selection
        keyboard = []
        
        # Add drives in rows of 2
        for i in range(1, 11, 2):
            row = []
            for j in range(2):
                drive_num = i + j
                if drive_num <= 10:
                    drive_key = f'drive_{drive_num:02d}'
                    status = get_drive_status(f'{drive_num:02d}', user_data)
                    drive_name = user_data.get(f'{drive_key}_name', f'Drive {drive_num:02d}')
                    row.append(InlineKeyboardButton(
                        f"{status} {drive_name}", 
                        callback_data=f"move_select_{drive_num}"
                    ))
            if row:
                keyboard.append(row)
        
        # Add help button
        keyboard.append([InlineKeyboardButton("❓ Help", callback_data="move_help")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message_text = (
            "🔄 **File Move Tool**\n\n"
            "Select a destination drive below, then send:\n"
            "`/m <folder_link_or_id> <drive_number>`\n\n"
            "**Available Drives:**"
        )
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                message_text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                message_text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            
    except Exception as e:
        logger.exception(f"Error in show_move_menu: {e}")

async def move_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle move menu callbacks"""
    try:
        query = update.callback_query
        await query.answer()
        
        if query.data == "move_help":
            help_text = (
                "🔄 **Move Files Help**\n\n"
                "**Command:** `/m <source> <destination_drive>`\n\n"
                "**Examples:**\n"
                "• `/m https://drive.google.com/drive/folders/1ABC...XYZ 1`\n"
                "• `/m 1ABC2DEF3GHI4JKL5MNO6PQR 2`\n\n"
                "**Features:**\n"
                "• Move all files from any folder to your drives\n"
                "• Preserves folder structure\n"
                "• Batch processing for efficiency\n"
                "• Real-time progress updates\n\n"
                "**Requirements:**\n"
                "• Source folder must be accessible\n"
                "• Destination drive must be set in `/uset`\n"
                "• You need edit access to both folders"
            )
            await query.edit_message_text(help_text, parse_mode='Markdown')
            
        elif query.data.startswith("move_select_"):
            drive_num = query.data.split("_")[2]
            await query.edit_message_text(
                f"✅ **Drive {drive_num} selected!**\n\n"
                f"Now send:\n`/m <folder_link> {drive_num}`\n\n"
                f"Example:\n`/m https://drive.google.com/drive/folders/SOURCE_ID {drive_num}`",
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.exception(f"Error in move_callback_handler: {e}")

# Register handlers
def register_move_handlers(application):
    """Register move command handlers"""
    application.add_handler(CallbackQueryHandler(move_callback_handler, pattern="^move_"))