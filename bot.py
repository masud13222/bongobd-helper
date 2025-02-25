import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from aiohttp import web
from plugins.uset import uset_command, handle_callbacks, handle_text_input
from plugins.start import start_command, help_command
from plugins.rename import rename_command
from plugins.clone import clone_command
from plugins.list import list_command, handle_search_callback
from plugins.upload import upload_command
from plugins.direct_dl import direct_dl_command
from plugins.delete import del_command
from plugins.bongodl import bdl_command

# Load environment variables
load_dotenv()

# Get owner IDs from env
OWNER_IDS = list(map(int, os.getenv('OWNER_IDS', '').split(',')))

# Setup web app
async def web_server():
    web_app = web.Application()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    return runner, site

async def run_bot():
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
    application.add_handler(CommandHandler(['m', 'mirror'], direct_dl_command, filters=filters.COMMAND & filters.User(OWNER_IDS)))
    application.add_handler(CommandHandler("del", del_command, filters=filters.COMMAND & filters.User(OWNER_IDS)))
    application.add_handler(CommandHandler("bdl", bdl_command, filters=filters.COMMAND & filters.User(OWNER_IDS)))
    
    # Add callback handlers
    application.add_handler(CallbackQueryHandler(handle_search_callback, pattern="^(next_page|prev_page|close_search)$"))
    application.add_handler(CallbackQueryHandler(handle_callbacks))
    
    # Add message handler for text inputs
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(OWNER_IDS), handle_text_input))

    # Start bot
    await application.initialize()
    await application.start()
    print("Bot Started...")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

def is_owner(update: Update):
    """Check if user is owner"""
    return update.effective_user.id in OWNER_IDS

async def owner_check(update: Update, context):
    """Check if user is owner before executing command"""
    if not is_owner(update):
        await update.message.reply_text("❌ Only bot owners can use this command!")
        return False
    return True

async def main():
    # Start both web server and bot
    runner, site = await web_server()
    try:
        await run_bot()
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass 