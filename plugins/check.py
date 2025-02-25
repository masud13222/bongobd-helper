from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle
import os

SCOPES = ['https://www.googleapis.com/auth/drive.metadata.readonly']

def check_drive_access(folder_id):
    """Check if we have access to the given folder ID and if it's a folder"""
    try:
        creds = None
        # Load token.pickle
        if os.path.exists('private/token.pickle'):
            with open('private/token.pickle', 'rb') as token:
                creds = pickle.load(token)
                
        # If credentials are expired or don't exist, refresh or create new
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                # Save refreshed credentials
                with open('private/token.pickle', 'wb') as token:
                    pickle.dump(creds, token)
            else:
                return False, "Token invalid or expired"
        
        # Build the Drive API service
        service = build('drive', 'v3', credentials=creds)
        
        # Get file metadata with shared drive support
        file = service.files().get(
            fileId=folder_id,
            fields="name, mimeType",
            supportsTeamDrives=True
        ).execute()
        
        if file['mimeType'] != 'application/vnd.google-apps.folder':
            return False, "The provided ID is not a folder"
            
        return True, file.get('name', 'Unknown Folder')
        
    except Exception as e:
        if "File not found" in str(e):
            return False, "Folder not found or no access"
        return False, str(e) 