from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import pickle
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from pymongo import MongoClient
import re
from plugins.check import check_drive_access

# Load environment variables
load_dotenv()

# MongoDB setup
MONGO_URI = os.getenv('MONGO_URI')
DB_NAME = os.getenv('DB_NAME')

if not MONGO_URI or not DB_NAME:
    raise ValueError("MONGO_URI and DB_NAME must be set in .env file")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
users_collection = db['users']

# States for conversation
WAITING_FOLDER_ID = 1
WAITING_PREFIX = 2
WAITING_SUFFIX = 3
WAITING_REMNAME = 4

# Timeouts
TIMEOUT = 60

MENU_KEYBOARD = [
    [InlineKeyboardButton("🔑 Drive Settings", callback_data="drive_settings")],
    [InlineKeyboardButton("📝 Rename Settings", callback_data="rename_settings")]
]

def get_drive_status(drive_num, user_data):
    drive_key = f"drive_{drive_num}"
    if drive_key in user_data and user_data[drive_key]:
        return "✅"
    return "⭕"

def get_name_status(key, user_data):
    if key in user_data and user_data[key]:
        return f" ({user_data[key]})"
    return ""

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback queries"""
    query = update.callback_query
    await query.answer()
    
    try:
        if query.data.startswith("reset_drive_"):
            drive_num = query.data.split("_")[2]
            # Reset the drive
            users_collection.update_one(
                {"user_id": query.from_user.id},
                {
                    "$unset": {
                        f"drive_{drive_num}": "",
                        f"drive_{drive_num}_name": ""
                    }
                }
            )
            await query.answer(f"Drive {drive_num} has been reset!")
            await uset_command(update, context)
            
        elif query.data == "reset_prename":
            users_collection.update_one(
                {"user_id": query.from_user.id},
                {"$unset": {"prefix": ""}}
            )
            await query.answer("Prefix has been reset!")
            await uset_command(update, context)
            
        elif query.data == "reset_surfname":
            users_collection.update_one(
                {"user_id": query.from_user.id},
                {"$unset": {"suffix": ""}}
            )
            await query.answer("Suffix has been reset!")
            await uset_command(update, context)
            
        elif query.data.startswith("drive_"):
            drive_num = query.data.split("_")[1]
            keyboard = [
                [InlineKeyboardButton("Reset", callback_data=f"reset_drive_{drive_num}")],
                [
                    InlineKeyboardButton("Back", callback_data="back_to_main"),
                    InlineKeyboardButton("Close", callback_data="close")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            context.user_data['start_time'] = datetime.now()
            context.user_data['selected_drive'] = drive_num
            context.user_data['waiting_for'] = 'folder_id'
            context.user_data['message_id'] = query.message.message_id
            
            await query.edit_message_text(
                f"Please send the folder ID for Drive {drive_num}\n\n"
                f"⏰ This request will timeout in {TIMEOUT} seconds.",
                reply_markup=reply_markup
            )

        elif query.data == "prename":
            keyboard = [
                [InlineKeyboardButton("Reset", callback_data="reset_prename")],
                [
                    InlineKeyboardButton("Back", callback_data="back_to_main"),
                    InlineKeyboardButton("Close", callback_data="close")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            context.user_data['start_time'] = datetime.now()
            context.user_data['waiting_for'] = 'prefix'
            context.user_data['message_id'] = query.message.message_id
            
            await query.edit_message_text(
                "Please enter the prefix name you want to add\n\n"
                f"⏰ This request will timeout in {TIMEOUT} seconds.",
                reply_markup=reply_markup
            )

        elif query.data == "remname":
            keyboard = [
                [InlineKeyboardButton("Add New", callback_data="add_remname")],
                [InlineKeyboardButton("View All", callback_data="view_remnames")],
                [InlineKeyboardButton("Reset All", callback_data="reset_remnames")],
                [
                    InlineKeyboardButton("Back", callback_data="back_to_main"),
                    InlineKeyboardButton("Close", callback_data="close")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "Remname Options:\n"
                "• Click 'Add New' to add a new remname\n"
                "• Click 'View All' to see all remnames\n"
                "• Click 'Reset All' to remove all remnames",
                reply_markup=reply_markup
            )

        elif query.data == "reset_remnames":
            users_collection.update_one(
                {"user_id": query.from_user.id},
                {"$set": {"remnames": []}},
                upsert=True
            )
            await query.answer("All remnames have been reset!")
            await uset_command(update, context)
            
        elif query.data.startswith("delete_remname_"):
            if (datetime.now() - context.user_data.get('start_time', datetime.now())).seconds > TIMEOUT:
                await query.answer("⚠️ Timeout! Please try again.")
                await uset_command(update, context)
                return
                
            index = int(query.data.split("_")[2]) - 1
            user_data = users_collection.find_one({"user_id": query.from_user.id}) or {}
            remnames = user_data.get('remnames', [])
            
            if 0 <= index < len(remnames):
                deleted_name = remnames.pop(index)
                users_collection.update_one(
                    {"user_id": query.from_user.id},
                    {"$set": {"remnames": remnames}}
                )
                await query.answer(f"Removed: {deleted_name}")
                
            # Return to main menu
            await uset_command(update, context)

        elif query.data == "add_remname":
            keyboard = [
                [
                    InlineKeyboardButton("Back", callback_data="back_to_main"),
                    InlineKeyboardButton("Close", callback_data="close")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            context.user_data['start_time'] = datetime.now()
            context.user_data['waiting_for'] = 'remname'
            context.user_data['message_id'] = query.message.message_id
            
            await query.edit_message_text(
                "Please enter a new remname to add\n\n"
                f"⏰ This request will timeout in {TIMEOUT} seconds.",
                reply_markup=reply_markup
            )

        elif query.data == "back_to_main":
            context.user_data.clear()  # Clear any ongoing operation
            await uset_command(update, context)
        
        elif query.data == "close":
            context.user_data.clear()
            await query.message.delete()
            
        elif query.data == "surfname":
            keyboard = [
                [InlineKeyboardButton("Reset", callback_data="reset_surfname")],
                [
                    InlineKeyboardButton("Back", callback_data="back_to_main"),
                    InlineKeyboardButton("Close", callback_data="close")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            context.user_data['start_time'] = datetime.now()
            context.user_data['waiting_for'] = 'suffix'
            context.user_data['message_id'] = query.message.message_id
            
            await query.edit_message_text(
                "Please enter the suffix name you want to add\n\n"
                f"⏰ This request will timeout in {TIMEOUT} seconds.",
                reply_markup=reply_markup
            )
            
        elif query.data == "view_remnames":
            user_data = users_collection.find_one({"user_id": query.from_user.id}) or {}
            remnames = user_data.get('remnames', [])
            
            if not remnames:
                text = "No remnames added yet."
            else:
                text = "Current Remnames:\n\nClick on any remname to delete it:\n\n"
                for i, name in enumerate(remnames, 1):
                    text += f"{i}. <code>{name}</code>\n"
            
            keyboard = [
                [InlineKeyboardButton("Add New", callback_data="add_remname")],
                [InlineKeyboardButton("Reset All", callback_data="reset_remnames")],
                [
                    InlineKeyboardButton("Back", callback_data="back_to_main"),
                    InlineKeyboardButton("Close", callback_data="close")
                ]
            ]
            
            # Add delete buttons for each remname
            if remnames:
                delete_buttons = []
                for i in range(len(remnames)):
                    delete_buttons.append(
                        InlineKeyboardButton(
                            f"Delete {i+1}", 
                            callback_data=f"delete_remname_{i+1}"
                        )
                    )
                    # Add row after every 2 buttons
                    if len(delete_buttons) == 2:
                        keyboard.insert(-2, delete_buttons)
                        delete_buttons = []
                if delete_buttons:  # Add remaining buttons
                    keyboard.insert(-2, delete_buttons)
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            
            # Set timeout
            context.user_data['start_time'] = datetime.now()
            
    except Exception as e:
        print(f"Error in handle_callbacks: {e}")
        await query.answer("An error occurred. Please try again.")

# Add message handler to handle text inputs
async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text inputs for drive IDs and names"""
    try:
        if not context.user_data.get('waiting_for'):
            return
            
        if (datetime.now() - context.user_data['start_time']).seconds > TIMEOUT:
            await update.message.reply_text("⚠️ Timeout! Please try the operation again.")
            context.user_data.clear()
            return
            
        user_id = update.effective_user.id
        text = update.message.text.strip()
        status_text = ""
        
        # Delete user's input message
        await update.message.delete()
        
        if context.user_data['waiting_for'] == 'folder_id':
            drive_num = context.user_data['selected_drive']
            
            # Check folder access
            has_access, folder_name = check_drive_access(text)
            if not has_access:
                await update.message.reply_text(f"❌ Error: {folder_name}")
                return
                
            # Save to database with folder name
            users_collection.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        f"drive_{drive_num}": text,
                        f"drive_{drive_num}_name": folder_name
                    }
                },
                upsert=True
            )
            status_text = f"✅ Drive {drive_num} connected to '{folder_name}'\n\n"
            
        elif context.user_data['waiting_for'] == 'prefix':
            users_collection.update_one(
                {"user_id": user_id},
                {"$set": {"prefix": text}},
                upsert=True
            )
            status_text = "✅ Prefix name has been saved!\n\n"
            
        elif context.user_data['waiting_for'] == 'suffix':
            users_collection.update_one(
                {"user_id": user_id},
                {"$set": {"suffix": text}},
                upsert=True
            )
            status_text = "✅ Suffix name has been saved!\n\n"
            
        elif context.user_data['waiting_for'] == 'remname':
            users_collection.update_one(
                {"user_id": user_id},
                {"$push": {"remnames": text}},
                upsert=True
            )
            status_text = "✅ New remname has been added!\n\n"

        # Get updated user data
        user_data = users_collection.find_one({"user_id": user_id}) or {}
        
        # Update drive statuses to show folder names
        for i in range(1, 11):
            drive_num = f"{i:02d}"
            drive_key = f"drive_{drive_num}"
            name_key = f"drive_{drive_num}_name"
            if drive_key in user_data and user_data[drive_key]:
                folder_name = user_data.get(name_key, 'Unknown Folder')
                status_text += f"Drive {drive_num}: ✅ {folder_name}\n"
            else:
                status_text += f"Drive {drive_num}: ⭕ Not Set\n"
        
        # Add remnames if any
        remnames = user_data.get('remnames', [])
        if remnames:
            status_text += f"\nRemnames ({len(remnames)}):\n"
            for name in remnames[:3]:  # Show first 3
                status_text += f"• {name}\n"
            if len(remnames) > 3:
                status_text += "..."
        else:
            status_text += "\nRemnames: None\n"
        
        # Add other settings
        status_text += f"\nPrefix: {user_data.get('prefix', 'Not Set')}\n"
        status_text += f"Suffix: {user_data.get('suffix', 'Not Set')}"

        # Update the menu message with success message and status
        keyboard = [
            [
                InlineKeyboardButton(f"Drive 01 {get_drive_status('01', user_data)}", callback_data="drive_01"),
                InlineKeyboardButton(f"Drive 02 {get_drive_status('02', user_data)}", callback_data="drive_02"),
            ],
            [
                InlineKeyboardButton(f"Drive 03 {get_drive_status('03', user_data)}", callback_data="drive_03"),
                InlineKeyboardButton(f"Drive 04 {get_drive_status('04', user_data)}", callback_data="drive_04"),
            ],
            [
                InlineKeyboardButton(f"Drive 05 {get_drive_status('05', user_data)}", callback_data="drive_05"),
                InlineKeyboardButton(f"Drive 06 {get_drive_status('06', user_data)}", callback_data="drive_06"),
            ],
            [
                InlineKeyboardButton(f"Drive 07 {get_drive_status('07', user_data)}", callback_data="drive_07"),
                InlineKeyboardButton(f"Drive 08 {get_drive_status('08', user_data)}", callback_data="drive_08"),
            ],
            [
                InlineKeyboardButton(f"Drive 09 {get_drive_status('09', user_data)}", callback_data="drive_09"),
                InlineKeyboardButton(f"Drive 10 {get_drive_status('10', user_data)}", callback_data="drive_10"),
            ],
            [
                InlineKeyboardButton(f"Prename{get_name_status('prefix', user_data)}", callback_data="prename"),
                InlineKeyboardButton(f"Surfname{get_name_status('suffix', user_data)}", callback_data="surfname"),
            ],
            [
                InlineKeyboardButton("Remname", callback_data="remname"),
                InlineKeyboardButton("Close ❌", callback_data="close"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Get the original menu message and update it
        message = await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=context.user_data['message_id'],
            text=status_text,
            reply_markup=reply_markup
        )
        
    except Exception as e:
        print(f"Error in handle_text_input: {e}")
        await update.message.reply_text("An error occurred. Please try again.")
    finally:
        context.user_data.clear()

async def uset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /uset command"""
    try:
        user_id = update.effective_user.id
        user_data = users_collection.find_one({"user_id": user_id}) or {}
        
        keyboard = [
            [
                InlineKeyboardButton(f"Drive 01 {get_drive_status('01', user_data)}", callback_data="drive_01"),
                InlineKeyboardButton(f"Drive 02 {get_drive_status('02', user_data)}", callback_data="drive_02"),
            ],
            [
                InlineKeyboardButton(f"Drive 03 {get_drive_status('03', user_data)}", callback_data="drive_03"),
                InlineKeyboardButton(f"Drive 04 {get_drive_status('04', user_data)}", callback_data="drive_04"),
            ],
            [
                InlineKeyboardButton(f"Drive 05 {get_drive_status('05', user_data)}", callback_data="drive_05"),
                InlineKeyboardButton(f"Drive 06 {get_drive_status('06', user_data)}", callback_data="drive_06"),
            ],
            [
                InlineKeyboardButton(f"Drive 07 {get_drive_status('07', user_data)}", callback_data="drive_07"),
                InlineKeyboardButton(f"Drive 08 {get_drive_status('08', user_data)}", callback_data="drive_08"),
            ],
            [
                InlineKeyboardButton(f"Drive 09 {get_drive_status('09', user_data)}", callback_data="drive_09"),
                InlineKeyboardButton(f"Drive 10 {get_drive_status('10', user_data)}", callback_data="drive_10"),
            ],
            [
                InlineKeyboardButton(f"Prename{get_name_status('prefix', user_data)}", callback_data="prename"),
                InlineKeyboardButton(f"Surfname{get_name_status('suffix', user_data)}", callback_data="surfname"),
            ],
            [
                InlineKeyboardButton("Remname", callback_data="remname"),
                InlineKeyboardButton("Close ❌", callback_data="close"),
            ],
        ]

        status_text = "🔧 Current Settings:\n\n"
        
        # Add drive statuses
        for i in range(1, 11):
            drive_num = f"{i:02d}"
            drive_key = f"drive_{drive_num}"
            name_key = f"drive_{drive_num}_name"
            if drive_key in user_data and user_data[drive_key]:
                folder_name = user_data.get(name_key, 'Unknown Folder')
                status_text += f"Drive {drive_num}: ✅ {folder_name}\n"
            else:
                status_text += f"Drive {drive_num}: ⭕ Not Set\n"
        
        # Add remnames if any
        remnames = user_data.get('remnames', [])
        if remnames:
            status_text += f"\nRemnames ({len(remnames)}):\n"
            for name in remnames[:3]:  # Show first 3
                status_text += f"• {name}\n"
            if len(remnames) > 3:
                status_text += "..."
        else:
            status_text += "\nRemnames: None\n"
        
        # Add other settings
        status_text += f"\nPrefix: {user_data.get('prefix', 'Not Set')}\n"
        status_text += f"Suffix: {user_data.get('suffix', 'Not Set')}"

        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text=status_text,
                reply_markup=reply_markup
            )
        elif update.message:
            await update.message.reply_text(
                text=status_text,
                reply_markup=reply_markup
            )
            
    except Exception as e:
        print(f"Error in uset_command: {e}")
        if update.message:
            await update.message.reply_text("An error occurred. Please try again.")
        elif update.callback_query:
            await update.callback_query.answer("An error occurred. Please try again.")

async def handle_drive_selection(update: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    drive_num = query.data.split("_")[1]
    
    keyboard = [
        [
            InlineKeyboardButton("Reset", callback_data=f"reset_drive_{drive_num}"),
            InlineKeyboardButton("Back", callback_data="back_to_main"),
            InlineKeyboardButton("Close", callback_data="close")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    context.user_data['selected_drive'] = drive_num
    context.user_data['start_time'] = datetime.now()
    
    await query.edit_message_text(
        f"Please send the folder ID for Drive {drive_num} within {TIMEOUT} seconds:",
        reply_markup=reply_markup
    )
    return WAITING_FOLDER_ID

async def handle_folder_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if (datetime.now() - context.user_data['start_time']).seconds > TIMEOUT:
        await update.message.reply_text("Timeout! Please try again.")
        return ConversationHandler.END
    
    folder_id = update.message.text.strip()
    drive_num = context.user_data['selected_drive']
    
    # Here you would validate the folder ID using token.pickle
    # For now, we'll just save it
    users_collection.update_one(
        {"user_id": update.effective_user.id},
        {
            "$set": {
                f"drive_{drive_num}": folder_id
            }
        },
        upsert=True
    )
    
    # Return to main menu
    await uset_command(update, context)
    return ConversationHandler.END

async def handle_prename(update: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    keyboard = [
        [
            InlineKeyboardButton("Back", callback_data="back_to_main"),
            InlineKeyboardButton("Close", callback_data="close")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    context.user_data['start_time'] = datetime.now()
    await query.edit_message_text(
        "Please enter the prefix name you want to add:",
        reply_markup=reply_markup
    )
    return WAITING_PREFIX

async def handle_prefix_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if (datetime.now() - context.user_data['start_time']).seconds > TIMEOUT:
        await update.message.reply_text("Timeout! Please try again.")
        return ConversationHandler.END
    
    prefix = update.message.text.strip()
    users_collection.update_one(
        {"user_id": update.effective_user.id},
        {"$set": {"prefix": prefix}},
        upsert=True
    )
    
    await uset_command(update, context)
    return ConversationHandler.END

# Add similar handlers for surfname and remname
# Will add in next iteration

async def handle_close(update: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.message.delete()
    return ConversationHandler.END

async def handle_back(update: CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await uset_command(update, context)
    return ConversationHandler.END 