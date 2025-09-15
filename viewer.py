import sys
import os
import signal
import termios
import tty
import threading
import time
from pathlib import Path
from PIL import Image
import fitz  # PyMuPDF
from term_image.image import AutoImage
from term_image.exceptions import TermImageError
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class PDFFileHandler(FileSystemEventHandler):
    """
    File system event handler for PDF file changes with debouncing.
    """
    def __init__(self, pdf_path, callback, debounce_seconds=3.0):
        self.pdf_path = Path(pdf_path).resolve()
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.last_event_time = 0
        self.timer = None
        self.lock = threading.Lock()
        
    def _debounced_callback(self):
        """Execute the callback after debounce period"""
        with self.lock:
            current_time = time.time()
            # Only execute if no new events occurred during debounce period
            if current_time - self.last_event_time >= self.debounce_seconds:
                self.callback()
    
    def _handle_event(self, file_path):
        """Handle file system event with debouncing"""
        if Path(file_path).resolve() == self.pdf_path:
            with self.lock:
                self.last_event_time = time.time()
                
                # Cancel existing timer
                if self.timer is not None:
                    self.timer.cancel()
                
                # Start new timer
                self.timer = threading.Timer(self.debounce_seconds, self._debounced_callback)
                self.timer.start()
        
    def on_modified(self, event):
        if not event.is_directory:
            self._handle_event(event.src_path)
    
    def on_moved(self, event):
        """Handle file moves (common with editors that save to temp file then move)"""
        if not event.is_directory:
            self._handle_event(event.dest_path)
    
    def on_created(self, event):
        """Handle file creation (some editors delete and recreate files)"""
        if not event.is_directory:
            self._handle_event(event.src_path)
    
    def cleanup(self):
        """Clean up timer resources"""
        with self.lock:
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None

def handle_signal(sig, frame):
    """
    Handles SIGINT (Ctrl+C) to exit gracefully.
    """
    print("\nExiting...")
    sys.exit(0)

def convert_pdf_to_images(pdf_path, dpi, max_retries=3):
    """
    Converts a PDF into a list of PIL Image objects with a specified resolution.
    Includes retry logic for file access issues.
    """
    for attempt in range(max_retries):
        try:
            # Check if file exists and is readable
            if not os.path.exists(pdf_path):
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                    continue
                else:
                    print(f"Error: PDF file not found at {pdf_path}")
                    return None
            
            doc = fitz.open(str(pdf_path))
            images = []
            
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=dpi)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                images.append(img)
            
            doc.close()
            return images
            
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Attempt {attempt + 1} failed: {e}. Retrying...")
                time.sleep(1.0)  # Wait longer between retries
            else:
                print(f"Error converting PDF after {max_retries} attempts: {e}")
                return None
    
    return None

def get_cropped_image(image, zoom_level):
    """
    Crops an image to simulate zooming.
    """
    width, height = image.size
    # Calculate the new dimensions based on zoom level
    new_width = int(width / zoom_level)
    new_height = int(height / zoom_level)
    # Calculate crop coordinates to center the "zoom"
    left = (width - new_width) / 2
    top = (height - new_height) / 2
    right = (width + new_width) / 2
    bottom = (height + new_height) / 2
    # Crop the image
    cropped_image = image.crop((left, top, right, bottom))
    return cropped_image

def main():
    """
    Main function for the PDF viewer utility.
    """
    if len(sys.argv) < 2:
        print("Usage: python your_script.py <pdf_file_path>")
        sys.exit(1)
    
    pdf_path = Path(sys.argv[1])
    if not pdf_path.is_file():
        print(f"Error: PDF file not found at {pdf_path}")
        sys.exit(1)
    
    signal.signal(signal.SIGINT, handle_signal)
    old_settings = termios.tcgetattr(sys.stdin)
    tty.setcbreak(sys.stdin.fileno())
    
    # Shared state
    state = {
        'images': None,
        'page_count': 0,
        'current_page': 0,
        'zoom_level': 1.0,
        'base_dpi': 300,
        'reload_needed': False,
        'is_reloading': False,  # Prevent concurrent reloads
        'last_reload_time': 0,  # Track when last reload happened
        'lock': threading.Lock()
    }
    
    def reload_pdf():
        """
        Callback function to reload PDF when file changes.
        """
        with state['lock']:
            current_time = time.time()
            # Additional safety: don't reload if we just reloaded recently
            if current_time - state['last_reload_time'] < 2.0:
                return
            
            if not state['is_reloading']:
                state['reload_needed'] = True
    
    def load_pdf():
        """
        Load or reload the PDF images.
        """
        with state['lock']:
            if state['is_reloading']:
                return False  # Already reloading
            state['is_reloading'] = True
        
        try:
            images = convert_pdf_to_images(pdf_path, int(state['base_dpi'] * state['zoom_level']))
            if not images:
                return False
            
            with state['lock']:
                state['images'] = images
                state['page_count'] = len(images)
                state['current_page'] = min(state['current_page'], state['page_count'] - 1)
                state['reload_needed'] = False
                state['last_reload_time'] = time.time()
            return True
        finally:
            with state['lock']:
                state['is_reloading'] = False
    
    def display_page(page_num):
        """
        Displays a specific page using term_image.
        """
        os.system('cls' if os.name == 'nt' else 'clear')
        
        with state['lock']:
            if state['images'] is None or not (0 <= page_num < state['page_count']):
                print("Page does not exist or PDF not loaded.")
                return
            
            # Apply cropping based on zoom level
            cropped_img = get_cropped_image(state['images'][page_num], state['zoom_level'])
            
            # Show reload status if currently reloading
            status = ""
            if state['is_reloading']:
                status = " | RELOADING..."
            
            print(f"Page {page_num + 1}/{state['page_count']} | Zoom: {state['zoom_level']:.1f}x{status} | Press 'q' to quit, 'r' to reload")
            
        try:
            term_image = AutoImage(cropped_img)
            term_image.draw()
        except TermImageError as e:
            print(f"Error displaying image: {e}")
    
    # Initial PDF load
    if not load_pdf():
        sys.exit(1)
    
    # Set up file watcher with debouncing
    event_handler = PDFFileHandler(pdf_path, reload_pdf, debounce_seconds=3.0)
    observer = Observer()
    observer.schedule(event_handler, str(pdf_path.parent), recursive=False)
    observer.start()
    
    try:
        display_page(state['current_page'])
        
        while True:
            # Check if reload is needed
            if state['reload_needed'] and not state['is_reloading']:
                print("File changed. Reloading...")
                time.sleep(0.2)  # Brief pause to show the message
                
                # Try to reload, but don't exit if it fails
                if load_pdf():
                    display_page(state['current_page'])
                else:
                    print("Failed to reload PDF - file may be temporarily locked. Press 'r' to retry or continue using current version.")
                    # Reset the reload flag so we don't keep trying
                    with state['lock']:
                        state['reload_needed'] = False
                    time.sleep(2)  # Show error message for a bit
                    display_page(state['current_page'])  # Redisplay current page
            
            # Set a timeout for stdin reading to check for file changes
            # Use select to check if input is available
            import select
            ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            
            if ready:
                key = sys.stdin.read(1)
                
                if key in ('h', '\x1b[D'):  # 'h' or left arrow
                    state['current_page'] = max(0, state['current_page'] - 1)
                    display_page(state['current_page'])
                elif key in ('l', '\x1b[C'):  # 'l' or right arrow
                    state['current_page'] = min(state['page_count'] - 1, state['current_page'] + 1)
                    display_page(state['current_page'])
                elif key == '+':
                    old_zoom = state['zoom_level']
                    state['zoom_level'] = min(4.0, state['zoom_level'] + 0.1)
                    if old_zoom != state['zoom_level']:
                        # Try to reload images with new zoom level
                        if load_pdf():
                            display_page(state['current_page'])
                        else:
                            # Revert zoom level if reload fails
                            state['zoom_level'] = old_zoom
                            print("Failed to apply zoom change")
                            time.sleep(1)
                            display_page(state['current_page'])
                elif key == '-':
                    old_zoom = state['zoom_level']
                    state['zoom_level'] = max(1.0, state['zoom_level'] - 0.1)
                    if old_zoom != state['zoom_level']:
                        # Try to reload images with new zoom level
                        if load_pdf():
                            display_page(state['current_page'])
                        else:
                            # Revert zoom level if reload fails
                            state['zoom_level'] = old_zoom
                            print("Failed to apply zoom change")
                            time.sleep(1)
                            display_page(state['current_page'])
                elif key == 'q':  # Quit
                    break
                elif key == 'r':  # Manual reload
                    print("Manual reload...")
                    if load_pdf():
                        display_page(state['current_page'])
                    else:
                        print("Failed to reload PDF")
                        time.sleep(2)
                        display_page(state['current_page'])
    
    finally:
        event_handler.cleanup()  # Clean up timers
        observer.stop()
        observer.join()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        print("Terminal settings restored.")

if __name__ == "__main__":
    main()
