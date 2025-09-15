import sys
import os
import signal
import termios
import tty
from pathlib import Path
from PIL import Image
import fitz  # PyMuPDF
from term_image.image import AutoImage
from term_image.exceptions import TermImageError

def handle_signal(sig, frame):
    """
    Handles SIGINT (Ctrl+C) to exit gracefully.
    """
    print("\nExiting...")
    sys.exit(0)

def convert_pdf_to_images(pdf_path, dpi):
    """
    Converts a PDF into a list of PIL Image objects with a specified resolution.
    """
    try:
        doc = fitz.open(pdf_path)
        images = []
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=dpi)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            images.append(img)
        return images
    except Exception as e:
        print(f"Error converting PDF: {e}")
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

    try:
        base_dpi = 300
        zoom_level = 1.0
        
        images = convert_pdf_to_images(pdf_path, int(base_dpi * zoom_level))
        if not images:
            sys.exit(1)
        
        page_count = len(images)
        current_page = 0
        last_modified = os.path.getmtime(pdf_path)

        def display_page(page_num):
            """
            Displays a specific page using term_image.
            """
            os.system('cls' if os.name == 'nt' else 'clear')
            if 0 <= page_num < page_count:
                # Apply cropping based on zoom level
                cropped_img = get_cropped_image(images[page_num], zoom_level)
                
                print(f"Page {page_num + 1}/{page_count} | Zoom: {zoom_level:.1f}x")
                try:
                    term_image = AutoImage(cropped_img)
                    term_image.draw()
                except TermImageError as e:
                    print(f"Error displaying image: {e}")
            else:
                print("Page does not exist.")

        display_page(current_page)

        while True:
            # Check for file changes
            new_modified = os.path.getmtime(pdf_path)
            if new_modified > last_modified:
                print("File changed. Rebuilding...")
                images = convert_pdf_to_images(pdf_path, int(base_dpi * zoom_level))
                if not images:
                    sys.exit(1)
                page_count = len(images)
                current_page = min(current_page, page_count - 1)
                last_modified = new_modified
                display_page(current_page)

            # Wait for key press
            key = sys.stdin.read(1)

            if key in ('h', '\x1b[D'):  # 'h' or left arrow
                current_page = max(0, current_page - 1)
                display_page(current_page)
            elif key in ('l', '\x1b[C'):  # 'l' or right arrow
                current_page = min(page_count - 1, current_page + 1)
                display_page(current_page)
            elif key == '+':
                zoom_level = min(4.0, zoom_level + 0.1) # Limit max zoom
                display_page(current_page)
            elif key == '-':
                zoom_level = max(1.0, zoom_level - 0.1) # Limit min zoom
                display_page(current_page)
            elif key == 'q':  # Quit
                break
            
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        print("Terminal settings restored.")

if __name__ == "__main__":
    main()
