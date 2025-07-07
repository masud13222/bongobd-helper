from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
from plugins.clone import get_drive_service, extract_id
from pymongo import MongoClient
import os
import re
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

def format_size(size):
    """Format size in bytes to human readable"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

async def move_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /m command for moving files"""
    try:
        logger.info(f"Move command received from user {update.effective_user.id}")
        
        # Check if command has arguments
        if not context.args:
            # Get user's drive settings to show available drives
            user_data = users_collection.find_one({"user_id": update.effective_user.id}) or {}
            
            # Build help message with available drives
            help_msg = "❌ Invalid format!\n\nUse:\n"
            for i in range(1, 11):  # Check all 10 drives
                drive_key = f'drive_{i:02d}'
                if user_data.get(drive_key):
                    drive_name = user_data.get(f'{drive_key}_name', f'Drive {i:02d}')
                    help_msg += f"• /m <file_link> -d{i} - Move to {drive_name}\n"
                    help_msg += f"• /m <file_link> -d{i} -r - Move and auto rename\n"
            
            if len(help_msg) == 27:  # Only has header
                help_msg += "\nNo drives set! Use /uset command to set drive folders first."
                
            await update.message.reply_text(help_msg)
            return
            
        # Check for rename flag
        should_rename = False
        if '-r' in context.args:
            should_rename = True
            # Remove -r from args
            context.args = [arg for arg in context.args if arg != '-r']
            
        # Get file ID from URL
        file_id = extract_id(context.args[0])
        
        # Get user's drive settings
        user_data = users_collection.find_one({"user_id": update.effective_user.id}) or {}
        
        # Check which drive to use
        target_folder = None
        drive_name = None
        if len(context.args) > 1:
            drive_flag = context.args[1].lower()
            # Check for drive flags d1 to d10
            for i in range(1, 11):
                drive_key = f'drive_{i:02d}'
                if drive_flag == f"-d{i}" and user_data.get(drive_key):
                    target_folder = user_data[drive_key]
                    drive_name = user_data.get(f'{drive_key}_name', f'Drive {i:02d}')
                    break
                    
            if not target_folder:
                # Show available drives
                help_msg = "❌ Invalid or unset drive!\n\nAvailable drives:\n"
                for i in range(1, 11):
                    drive_key = f'drive_{i:02d}'
                    if user_data.get(drive_key):
                        drive_name = user_data.get(f'{drive_key}_name', f'Drive {i:02d}')
                        help_msg += f"• -d{i} ({drive_name})\n"
                await update.message.reply_text(help_msg)
                return
        else:
            # Use first available drive
            for i in range(1, 11):
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
            # Get file metadata including current parent
            file = service.files().get(
                fileId=file_id,
                fields='name, parents, mimeType',
                supportsAllDrives=True
            ).execute()
            
            file_name = file.get('name', 'Unknown File')
            current_parents = file.get('parents', [])
            file_mime_type = file.get('mimeType', '')
            
            # Check if it's a file (not folder)
            if file_mime_type == 'application/vnd.google-apps.folder':
                await update.message.reply_text("❌ This is a folder! Use this command for files only.")
                return
            
            # Create status message
            status_msg = await update.message.reply_text(f"🔄 Moving file: {file_name}...")
            
            # Move file to destination folder
            if current_parents:
                # Remove from current parent and add to destination
                previous_parents = ','.join(current_parents)
                moved_file = service.files().update(
                    fileId=file_id,
                    addParents=target_folder,
                    removeParents=previous_parents,
                    supportsAllDrives=True
                ).execute()
            else:
                # If no current parents, just add to destination
                moved_file = service.files().update(
                    fileId=file_id,
                    addParents=target_folder,
                    supportsAllDrives=True
                ).execute()
            
            logger.info(f"File moved successfully: {file_name} (ID: {file_id})")
            
            # Get updated file info
            file = service.files().get(
                fileId=file_id,
                fields='name, size',
                supportsAllDrives=True
            ).execute()
            
            file_size = format_size(int(file.get('size', 0)))
            
            # Generate drive link
            drive_link = f"https://drive.google.com/file/d/{file_id}/view"
            
            # Create view button
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 View File", url=drive_link)]
            ])
            
            # After successful move, handle rename if requested
            if should_rename:
                try:
                    # Get user settings
                    user_data = users_collection.find_one({"user_id": update.effective_user.id}) or {}
                    prefix = user_data.get('prefix', '')
                    suffix = user_data.get('suffix', '')
                    remnames = user_data.get('remnames', [])
                    
                    old_name = file.get('name', '')
                    
                    # Get name without extension for processing
                    if '.' in old_name:
                        name_part = old_name.rsplit('.', 1)[0]
                        ext = '.' + old_name.rsplit('.', 1)[1]
                    else:
                        name_part = old_name
                        ext = ''
                        
                    new_name = name_part
                    
                    # Process remnames first
                    if remnames:
                        remnames.sort(key=len, reverse=True)
                        for remname in remnames:
                            if remname in new_name:
                                new_name = new_name.replace(remname, '')
                    
                    # Add prefix
                    if prefix:
                        new_name = f"{prefix} - {new_name}"
                        
                    # Add suffix
                    if suffix:
                        new_name = f"{new_name} {suffix}"
                    
                    # Clean up multiple spaces
                    new_name = re.sub(r'\s+', ' ', new_name).strip()
                    
                    # Add back extension
                    final_name = new_name + ext
                    
                    # Update file only if name changed
                    if final_name != old_name:
                        service.files().update(
                            fileId=file_id,
                            body={'name': final_name},
                            supportsAllDrives=True
                        ).execute()
                        
                        await status_msg.edit_text(
                            "✅ File moved and renamed successfully!\n\n"
                            f"Old name: <code>{old_name}</code>\n"
                            f"New name: <code>{final_name}</code>\n"
                            f"Size: {file_size}\n"
                            f"Drive: {drive_name}\n"
                            f"Link: <code>{drive_link}</code>",
                            parse_mode='HTML',
                            reply_markup=keyboard
                        )
                        return
                        
                except Exception as e:
                    logger.error(f"Error in rename process: {e}")
            
            # Send success message
            await status_msg.edit_text(
                "✅ File moved successfully!\n\n"
                f"Name: <code>{file.get('name')}</code>\n"
                f"Size: {file_size}\n"
                f"Drive: {drive_name}\n"
                f"Link: <code>{drive_link}</code>",
                parse_mode='HTML',
                reply_markup=keyboard
            )
            
        except Exception as e:
            logger.error(f"Error moving file: {e}")
            await update.message.reply_text(
                f"❌ Error: Could not move file\n"
                f"Reason: {str(e)}"
            )
            
    except Exception as e:
        logger.exception(f"Error in move_command: {e}")
        await update.message.reply_text("❌ An error occurred. Please try again.")

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
                    status = "✅" if user_data.get(drive_key) else "⭕"
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
            "`/m <file_link> -d<drive_number>`\n\n"
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
                "**Command:** `/m <file_link> -d<drive_number>`\n\n"
                "**Examples:**\n"
                "• `/m https://drive.google.com/file/d/1ABC...XYZ -d1`\n"
                "• `/m https://drive.google.com/open?id=1ABC...XYZ -d2`\n"
                "• `/m 1ABC2DEF3GHI4JKL5MNO6PQR -d3`\n\n"
                "**Features:**\n"
                "• Move individual files between drives\n"
                "• Auto-rename with prefix/suffix (use -r flag)\n"
                "• Real-time status updates\n\n"
                "**Requirements:**\n"
                "• You need edit access to the file\n"
                "• Destination drive must be set in `/uset`\n"
                "• Works with individual files only (not folders)"
            )
            await query.edit_message_text(help_text, parse_mode='Markdown')
            
        elif query.data.startswith("move_select_"):
            drive_num = query.data.split("_")[2]
            await query.edit_message_text(
                f"✅ **Drive {drive_num} selected!**\n\n"
                f"Now send:\n`/m <file_link> -d{drive_num}`\n\n"
                f"Example:\n`/m https://drive.google.com/open?id=FILE_ID -d{drive_num}`",
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.exception(f"Error in move_callback_handler: {e}")

# Register handlers
def register_move_handlers(application):
    """Register move command handlers"""
    application.add_handler(CallbackQueryHandler(move_callback_handler, pattern="^move_"))