import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from plugins.uset import uset_command, handle_callbacks, handle_text_input
from plugins.start import start_command, help_command
from plugins.rename import rename_command
from plugins.clone import clone_command
from plugins.list import list_command, handle_search_callback
from plugins.upload import upload_command
from plugins.direct_dl import direct_dl_command
from plugins.delete import del_command
from plugins.bongodl import bdl_command
from plugins.mega import mega_command
from plugins.move import move_command, register_move_handlers
import socket
from threading import Thread

# Load environment variables
load_dotenv()

# Get owner IDs from env
OWNER_IDS = list(map(int, os.getenv('OWNER_IDS', '').split(',')))

# TCP Health Check Server
def tcp_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', 8080))
    server.listen(1)
    
    while True:
        try:
            client, _ = server.accept()
            client.send(b'OK')
            client.close()
        except:
            pass

def is_owner(update: Update):
    """Check if user is owner"""
    return update.effective_user.id in OWNER_IDS

async def owner_check(update: Update, context):
    """Check if user is owner before executing command"""
    if not is_owner(update):
        await update.message.reply_text("❌ Only bot owners can use this command!")
        return False
    return True

def main():
    # Start TCP server in thread
    Thread(target=tcp_server, daemon=True).start()

    # Create application
    application = Application.builder().token(os.getenv('BOT_TOKEN')).build()

    # Add command handlers with owner check
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # Protected commands
    application.add_handler(CommandHandler("uset", uset_command, filters=filters.COMMAND & filters.User(OWNER_IDS)))
    application.add_handler(CommandHandler(["rename", "r"], rename_command, filters=filters.COMMAND & filters.User(OWNER_IDS)))
    application.add_handler(CommandHandler(['clone', 'c'], clone_command, filters=filters.COMMAND & filters.User(OWNER_IDS)))
    application.add_handler(CommandHandler("list", list_command, filters=filters.COMMAND & filters.User(OWNER_IDS)))
    application.add_handler(CommandHandler("upload", upload_command, filters=filters.COMMAND & filters.User(OWNER_IDS)))
    application.add_handler(CommandHandler("mirror", direct_dl_command, filters=filters.COMMAND & filters.User(OWNER_IDS)))
    application.add_handler(CommandHandler("m", move_command, filters=filters.COMMAND & filters.User(OWNER_IDS)))
    application.add_handler(CommandHandler("del", del_command, filters=filters.COMMAND & filters.User(OWNER_IDS)))
    application.add_handler(CommandHandler("bdl", bdl_command, filters=filters.COMMAND & filters.User(OWNER_IDS)))
    application.add_handler(CommandHandler("mega", mega_command, filters=filters.COMMAND & filters.User(OWNER_IDS)))
    
    # Add callback handlers
    application.add_handler(CallbackQueryHandler(handle_search_callback, pattern="^(next_page|prev_page|close_search)$"))
    application.add_handler(CallbackQueryHandler(handle_callbacks))
    
    # Register move handlers
    register_move_handlers(application)
    
    # Add message handler for text inputs
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(OWNER_IDS), handle_text_input))

    # Run bot
    print("Bot started...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main() 