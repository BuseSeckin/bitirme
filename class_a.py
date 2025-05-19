# -*- coding: utf-8 -*-
"""
Created on Mon May 12 22:41:41 2025

@author: buses
"""

import time
import threading
import queue
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pathlib import Path
from sliding_tiles_v1_v3 import main  # main(image_path)

WATCH_DIR = "captured_frames1_after_stop"
VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}

# Create a thread-safe queue for image paths
image_queue = queue.Queue()

# Event handler: just adds new file paths to the queue
class ImageHandler(FileSystemEventHandler):
    def on_created(self, event):
        filepath = Path(event.src_path)
        if filepath.suffix.lower() in VALID_EXTENSIONS:
            print(f"New image detected: {filepath.name}")
            time.sleep(1)  # Wait briefly to allow full file write
            image_queue.put(str(filepath))

# Background worker thread to process images one by one
def process_images():
    while True:
        image_path = image_queue.get()
        try:
            print(f"Processing image: {image_path}")
            main(image_path)  # Call your detection code
        except Exception as e:
            print(f"Error processing {image_path}: {e}")
        image_queue.task_done()

if __name__ == "__main__":
    print(f"Watching directory: {WATCH_DIR}")
    
    # Start the background thread
    threading.Thread(target=process_images, daemon=True).start()

    event_handler = ImageHandler()
    observer = Observer()
    observer.schedule(event_handler, WATCH_DIR, recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
