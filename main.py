import logging
import threading
import tkinter as tk
from tkinter import simpledialog, messagebox
from PIL import Image, ImageDraw
import pystray
from config import Config
from sender import Sender
from watcher import JournalWatcher

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class SkyLinkApp:
    def __init__(self):
        self.config = Config()
        self.sender = None
        self.watcher = None
        self.status = "Stopped"
        self.status_message = "Application is not running."
        self.icon = None

    def create_icon(self, color='gray'):
        """Creates a system tray icon with the given color."""
        width = 64
        height = 64
        image = Image.new('RGB', (width, height), 'black')
        dc = ImageDraw.Draw(image)
        dc.rectangle([(0, 0), (width, height)], fill=color)
        return image

    def setup_tray_icon(self):
        """Sets up the system tray icon and its menu."""
        menu = pystray.Menu(
            pystray.MenuItem(lambda text: f"Status: {self.status}", None, enabled=False),
            pystray.MenuItem('Set API Key', self.prompt_for_api_key),
            pystray.MenuItem('Exit', self.exit_app)
        )
        self.icon = pystray.Icon("skylink", self.create_icon(), "SkyLink", menu)
        
        # Run the icon in a separate thread to avoid blocking
        icon_thread = threading.Thread(target=self.icon.run, daemon=True)
        icon_thread.start()

    def update_status(self, status, message=""):
        """Updates the application status and icon color."""
        self.status = status
        self.status_message = message
        if self.status == "Running":
            self.icon.icon = self.create_icon('green')
        elif self.status == "Error":
            self.icon.icon = self.create_icon('red')
        else:
            self.icon.icon = self.create_icon('gray')
        # This is a bit of a hack to force the menu to update
        self.icon.update_menu()

    def prompt_for_api_key(self):
        """Opens a dialog to ask the user for their API key."""
        root = tk.Tk()
        root.withdraw()
        api_key = simpledialog.askstring("API Key", "Please enter your SkyLink API Key:", show='*')
        if api_key:
            self.config.set('api_key', api_key)
            messagebox.showinfo("Success", "API Key saved. Please restart the application if it was not running.")
            # If the app is already running, we might want to re-initialize the sender
            if self.sender:
                self.sender.api_key = api_key
        root.destroy()

    def run(self):
        """Main application loop."""
        logging.info("Starting SkyLink application...")
        self.setup_tray_icon()
        self.update_status("Starting", "Initializing...")

        api_key = self.config.get('api_key')
        journal_path = self.config.get('journal_path')

        if not api_key:
            logging.warning("API Key is not set.")
            self.update_status("Error", "API Key not set.")
            self.prompt_for_api_key()
            api_key = self.config.get('api_key') # Re-fetch after prompt
            if not api_key:
                self.exit_app()
                return

        if not journal_path:
            logging.error("Elite Dangerous journal path not found.")
            self.update_status("Error", "Journal path not found.")
            messagebox.showerror("Error", "Could not find the Elite Dangerous journal directory.")
            self.exit_app()
            return
            
        cache_file = self.config.app_data_dir / 'deduplication_cache.json'
        self.sender = Sender(api_key=api_key, cache_path=cache_file, config=self.config)
        self.sender.set_status_callback(self.update_status)
        self.sender.start()
        
        self.watcher = JournalWatcher(journal_dir=journal_path, sender_instance=self.sender, config=self.config)
        self.watcher.start()

        self.update_status("Running", "Monitoring journal files...")

        # Keep the main thread alive
        try:
            while True:
                pass
        except KeyboardInterrupt:
            self.exit_app()

    def exit_app(self):
        """Shuts down the application gracefully."""
        logging.info("Shutting down SkyLink...")
        if self.watcher:
            self.watcher.stop()
        if self.sender:
            self.sender.stop()
            self.sender.join()
        if self.icon:
            self.icon.stop()
        
        # A small delay to ensure threads are closed
        import time
        time.sleep(1)
        
        # Force exit if threads are still hanging
        os._exit(0)


if __name__ == '__main__':
    # Add os._exit(0) to handle exit from main thread
    import os
    app = SkyLinkApp()
    app.run()
