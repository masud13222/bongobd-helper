from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from pymongo import MongoClient
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# MongoDB setup
client = MongoClient(os.getenv('MONGO_URI'))
db = client[os.getenv('DB_NAME')]
users_collection = db['users']

HELP_TEXT = """
🤖 <b>Available Commands:</b>

🔧 <b>Basic Commands:</b>
• /start - Start the bot
• /help - Show this help message
• /uset - Configure drive settings

📁 <b>Drive Commands:</b>
• /clone or /c - Clone files between drives
  Format: /clone drive_link -d1
  Example: /c https://drive.google.com/file/xxx -d1

• /list - List files in drive folder
  Format: /list folder_link

� <b>Move Command:</b>
• /m - Move files between folders
  Format: /m source_folder destination_drive
  Example: /m https://drive.google.com/open?id=xxx 1
  Example: /m https://drive.google.com/drive/folders/xxx 2

� <b>Download Commands:</b>
• /mirror - Mirror direct download links
  Format: /mirror direct_link -d1
  Example: /mirror https://example.com/file.zip -d1

• /bdl - Download from BongoBD
  Format: /bdl bongo_link -n filename -d1
  Example: /bdl https://bongobd.com/xxx -n movie.mp4 -d1

🗑 <b>Delete Command:</b>
• /del - Delete files from drive
  Format: /del drive_link

✏️ <b>Rename Commands:</b>
• /rename or /r - Rename files
  Format: /r new_name
  Note: Reply to a message with file

<b>Drive Numbers (-d1 to -d10):</b>
Configure drive folders in /uset first
Then use -d1 to -d10 to select target drive
For /m command, use drive numbers 1-10 (without -d prefix)

<b>Note:</b> Only bot owners can use these commands
"""

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    
    # Save user to database if not exists
    users_collection.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "first_name": first_name,
                "username": update.effective_user.username
            }
        },
        upsert=True
    )
    
    keyboard = [
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
            InlineKeyboardButton("ℹ️ Help", callback_data="help")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = (
        f"👋 Hi {first_name}!\n\n"
        "I'm a Clone Bot that can help you manage and clone files between Google Drives.\n\n"
        "🔑 <b>Main Features:</b>\n"
        "• Clone files between drives\n"
        "• Upload files to drive\n" 
        "• Mirror direct links\n"
        "• Download from BongoBD\n"
        "• Delete files\n"
        "• Rename files\n\n"
        "🔧 Use /uset to configure your drive settings\n"
        "📚 Use /help to see all available commands"
    )
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    await update.message.reply_text(
        HELP_TEXT,
        parse_mode='HTML'
    ) 