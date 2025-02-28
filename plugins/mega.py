from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import os
import asyncio
import threading
import time
import sys
from pymongo import MongoClient
from plugins.upload import upload_to_drive, cleanup, format_size, format_speed
from mega import (MegaApi, MegaListener, MegaRequest, MegaTransfer, MegaError)
from datetime import datetime
import shutil

# MongoDB setup
MONGO_URI = os.getenv('MONGO_URI')
DB_NAME = os.getenv('DB_NAME')
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
users_collection = db['users']

# Mega credentials
MEGA_EMAIL = os.getenv('MEGA_EMAIL_ID')
MEGA_PASSWORD = os.getenv('MEGA_PASSWORD')
MEGA_API_KEY = "aJ5NAQI5"  # Default API key for MEGA SDK

# Progress update interval in seconds
PROGRESS_UPDATE_INTERVAL = 3

def log_info(message: str):
    """Log info with flush"""
    print(f"[MEGA] {message}", flush=True)
    sys.stdout.flush()

class MegaAppListener(MegaListener):
    _NO_EVENT_ON = (MegaRequest.TYPE_LOGIN, MegaRequest.TYPE_FETCH_NODES)
    NO_ERROR = "no error"

    def __init__(self, continue_event: threading.Event, status_msg, app):
        self.continue_event = continue_event
        self.status_msg = status_msg
        self.node = None
        self.public_node = None
        self.transfer = None
        self.last_update = 0
        self.is_cancelled = False
        self.error = None
        self.__bytes_transferred = 0
        self.__speed = 0
        self.__name = ''
        self.app = app
        self.main_loop = None
        super().__init__()

    @property
    def speed(self):
        return self.__speed

    @property
    def downloaded_bytes(self):
        return self.__bytes_transferred

    def _format_bytes(self, size):
        """Format bytes to human readable format"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} TB"

    def _format_speed(self, speed):
        """Format speed to human readable format"""
        return self._format_bytes(speed) + "/s"

    async def start_status_updater(self, loop):
        """Store the main event loop"""
        self.main_loop = loop

    def onRequestFinish(self, api, request, error):
        if str(error).lower() != self.NO_ERROR:
            self.error = error.copy()
            log_info(f'Mega onRequestFinishError: {self.error}')
            self.continue_event.set()
            return

        request_type = request.getType()
        if request_type == MegaRequest.TYPE_LOGIN:
            log_info("Logged in successfully, fetching nodes...")
            api.fetchNodes()
        elif request_type == MegaRequest.TYPE_GET_PUBLIC_NODE:
            self.public_node = request.getPublicMegaNode()
            self.__name = self.public_node.getName()
            log_info(f"Got public node: {self.__name}")
        elif request_type == MegaRequest.TYPE_FETCH_NODES:
            log_info("Fetching Root Node...")
            self.node = api.getRootNode()
            self.__name = self.node.getName()
            log_info(f"Node Name: {self.__name}")

        if request_type not in self._NO_EVENT_ON or (self.node and "cloud drive" not in self.__name.lower()):
            self.continue_event.set()

    def onRequestTemporaryError(self, api, request, error):
        log_info(f'Mega Request temporary error: {error}')
        self.error = str(error)
        self.continue_event.set()

    def onTransferStart(self, api, transfer):
        log_info(f"Transfer started: {transfer.getFileName()}")
        self.transfer = transfer

    def onTransferUpdate(self, api, transfer):
        """Handle transfer updates synchronously"""
        if self.is_cancelled:
            api.cancelTransfer(transfer, None)
            return

        current_time = time.time()
        if current_time - self.last_update >= 2:  # Update every 2 seconds
            try:
                self.__speed = transfer.getSpeed()
                self.__bytes_transferred = transfer.getTransferredBytes()
                total_bytes = transfer.getTotalBytes()
                progress = (self.__bytes_transferred / total_bytes) * 100

                status_text = (
                    f"📥 Downloading from Mega\n"
                    f"File: {self.__name}\n"
                    f"Progress: {progress:.1f}%\n"
                    f"Size: {self._format_bytes(self.__bytes_transferred)} / {self._format_bytes(total_bytes)}\n"
                    f"Speed: {self._format_speed(self.__speed)}"
                )

                if self.main_loop:
                    # Schedule the coroutine to update the status message
                    self.main_loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(
                            self.status_msg.edit_text(status_text)
                        )
                    )

                self.last_update = current_time

            except Exception as e:
                log_info(f"Error in transfer update: {e}")

    def onTransferFinish(self, api, transfer, error):
        try:
            if self.is_cancelled:
                self.continue_event.set()
            elif transfer.isFinished() and (transfer.isFolderTransfer() or transfer.getFileName() == self.__name):
                log_info(f"Transfer finished successfully: {self.__name}")
                self.continue_event.set()
        except Exception as e:
            log_info(f"Error in transfer finish: {e}")
            self.error = str(e)
            self.continue_event.set()

    def onTransferTemporaryError(self, api, transfer, error):
        filen = transfer.getFileName()
        state = transfer.getState()
        log_info(f'Mega download temporary error in file {filen}: {error}')

        if state in [1, 4]:  # Queued or retrying
            return

        self.error = str(error)
        self.continue_event.set()

    def onUsersUpdate(self, api, users):
        pass

    def onNodesUpdate(self, api, nodes):
        pass

    def onAccountUpdate(self, api):
        pass

    def onContactRequestsUpdate(self, api, requests):
        pass

    def onReloadNeeded(self, api):
        pass

class AsyncExecutor:
    def __init__(self):
        self.continue_event = threading.Event()

    async def async_do(self, function, args):
        """Execute a function with args in a separate thread and wait for continue_event"""
        self.continue_event.clear()
        # Run the function in a thread so as not to block the event loop
        await asyncio.to_thread(function, *args)
        # Wait for the continue_event in a thread
        await asyncio.to_thread(self.continue_event.wait)

async def download_from_mega(url: str, dest_path: str, status_msg, app):
    """Download file from Mega using SDK"""
    try:
        # Store the current event loop
        app.loop = asyncio.get_running_loop()

        # Initialize with API key
        executor = AsyncExecutor()
        api = MegaApi(MEGA_API_KEY, None, None, 'telegram-mirror-bot')

        # Create listener with application instance
        listener = MegaAppListener(executor.continue_event, status_msg, app)
        api.addListener(listener)

        # Start status updater with main loop
        await listener.start_status_updater(app.loop)

        # Login if credentials available
        if MEGA_EMAIL and MEGA_PASSWORD:
            log_info("Logging into Mega account...")
            await executor.async_do(api.login, (MEGA_EMAIL, MEGA_PASSWORD))
            if listener.error:
                log_info(f"Login error: {listener.error}")
                return False

        # Get public node
        log_info("Getting public node...")

        # Extract file ID and key from URL
        try:
            if '#' in url:
                if url.startswith(('https://mega.nz/', 'mega.nz/')):
                    base = url.split('#')[0].split('/')[-1]
                    key = url.split('#')[1]

                    if '!' in base:
                        file_id = base.split('!')[1]
                    else:
                        file_id = base

                    if '!' in key:
                        key = key.split('!')[1]

                    mega_url = f"{file_id}!{key}"
                else:
                    mega_url = url.split('#')[1]

                log_info(f"Formatted URL: {mega_url}")
                await executor.async_do(api.getPublicNode, (mega_url,))
            else:
                log_info("Invalid Mega URL format - no # separator found")
                return False

        except Exception as e:
            log_info(f"Error formatting Mega URL: {e}")
            return False

        if listener.error:
            log_info(f"Error getting node: {listener.error}")
            return False

        node = listener.public_node
        if not node:
            log_info("Could not get public node")
            return False

        # Start download
        file_name = node.getName()
        file_size = node.getSize()
        log_info(f"Starting download of {file_name} ({file_size} bytes)")

        os.makedirs(dest_path, exist_ok=True)
        local_path = os.path.join(dest_path, file_name)

        await executor.async_do(api.startDownload, (node, local_path))
        if listener.error:
            log_info(f"Download error: {listener.error}")
            return False

        return os.path.exists(local_path)

    except Exception as e:
        log_info(f"Error downloading from Mega: {e}")
        return False
    finally:
        try:
            api.removeListener(listener)
        except:
            pass

async def mega_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mega command"""
    try:
        if not context.args:
            await update.message.reply_text(
                "❌ Please provide a Mega link!\n\n"
                "Usage: /mega [url] [-d1/-d2/..]"
            )
            return

        url = context.args[0]
        if not url.startswith(('https://mega.nz/', 'mega.nz/')):
            await update.message.reply_text("❌ Invalid Mega link!")
            return

        status_msg = await update.message.reply_text("⏳ Starting download...")
        app = context.application

        asyncio.create_task(
            process_mega_download(update, context, url, status_msg, app)
        )

    except Exception as e:
        log_info(f"Error in mega_command: {e}")
        await update.message.reply_text("❌ An error occurred!")

async def process_mega_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, status_msg, app):
    """Process Mega download and upload to Drive"""
    download_dir = None
    file_path = None
    
    try:
        user_id = update.effective_user.id
        timestamp = int(time.time())  # Add timestamp to avoid conflicts
        log_info(f"Processing mega download for user {user_id}")
        
        # Get user's drive settings
        user_data = users_collection.find_one({"user_id": user_id}) or {}
        log_info(f"Got user data: {list(user_data.keys())}")
        
        # Check which drive to use
        target_folder = None
        drive_name = None
        if len(context.args) > 1:
            drive_flag = context.args[1].lower()
            log_info(f"Checking drive flag: {drive_flag}")
            for i in range(1, 7):
                drive_key = f'drive_{i:02d}'
                if drive_flag == f"-d{i}" and user_data.get(drive_key):
                    target_folder = user_data[drive_key]
                    drive_name = user_data.get(f'{drive_key}_name', f'Drive {i:02d}')
                    log_info(f"Selected drive: {drive_name} with folder: {target_folder}")
                    break
                    
        if not target_folder:
            log_info("No valid drive folder found")
            await status_msg.edit_text("❌ Invalid or unset drive!")
            return
            
        # Create unique download directory for this task
        download_dir = os.path.join('downloads', f"{user_id}_{timestamp}")
        os.makedirs(download_dir, exist_ok=True)
        log_info(f"Created download directory: {download_dir}")
        
        # Download file
        log_info(f"Starting download from: {url}")
        success = await download_from_mega(url, download_dir, status_msg, app)
        if not success:
            log_info("Download failed")
            await status_msg.edit_text("❌ Download failed!")
            return
            
        # Get downloaded file (excluding .getxfer files)
        files = [f for f in os.listdir(download_dir) if not f.startswith('.getxfer')]
        log_info(f"Files in download directory: {files}")
        if not files:
            log_info("No files found after download")
            await status_msg.edit_text("❌ No file downloaded!")
            return
            
        file_path = os.path.join(download_dir, files[0])
        log_info(f"Using file path: {file_path}")
        
        # Upload to Drive
        from plugins.clone import get_drive_service
        service = get_drive_service("token.pickle")
        if not service:
            log_info("Drive service not available")
            await status_msg.edit_text("❌ Drive service not available!")
            return
            
        log_info("Starting upload to Google Drive")
        await status_msg.edit_text("⏳ Uploading to Google Drive...")
        response = await upload_to_drive(service, file_path, target_folder, status_msg)
        
        if response:
            log_info(f"Upload successful. File ID: {response.get('id')}")
            # Generate drive link
            file_id = response.get('id')
            drive_link = f"https://drive.google.com/file/d/{file_id}/view"
            
            # Create view button
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 View File", url=drive_link)]
            ])
            
            # Send success message with button
            await status_msg.edit_text(
                "✅ File transferred successfully!\n\n"
                f"Name: <code>{response.get('name')}</code>\n"
                f"Drive: {drive_name}",
                parse_mode='HTML',
                reply_markup=keyboard
            )
        else:
            log_info("Upload failed")
            await status_msg.edit_text("❌ Upload failed!")
            
    except Exception as e:
        log_info(f"Error in process_mega_download: {e}")
        await status_msg.edit_text("❌ An error occurred!")
        
    finally:
        # Cleanup
        try:
            if download_dir and os.path.exists(download_dir):
                # Force remove directory and all contents
                shutil.rmtree(download_dir, ignore_errors=True)
                log_info(f"Cleaned up download directory: {download_dir}")
        except Exception as e:
            log_info(f"Error during cleanup: {e}")
