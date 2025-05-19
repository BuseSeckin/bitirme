# -*- coding: utf-8 -*-
"""
Created on Tue May 13 20:18:31 2025

@author: buses
"""

# -*- coding: utf-8 -*-
"""
Created on Mon May 12 20:19:16 2025

@author: buses
"""

# -*- coding: utf-8 -*-
"""
Created on Sat May 10 16:00:37 2025

@author: buses
"""

import cv2
import numpy as np
import torch
from ultralytics import YOLO
from typing import List, Tuple, Dict, Union
import os
from PIL import Image
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import time
from pathlib import Path
import subprocess
from typing import Optional

def get_latest_created_image(directory: str) -> Optional[Path]:
    """
    Returns the most recently created image file in the given directory (Windows only).
    """
    image_extensions = {'.jpg', '.jpeg', '.png', '.bmp'}
    image_files = [
        f for f in Path(directory).iterdir()
        if f.suffix.lower() in image_extensions and f.is_file()
    ]
    if not image_files:
        return None
    # Use creation time (Windows only)
    latest_file = max(image_files, key=lambda f: f.stat().st_ctime)
    return latest_file
image_dir = "data_exchange/captured_frames1_after_stop"
latest_image = get_latest_created_image(image_dir)
print("Latest image by creation time:", latest_image)





class SlidingWindowDetector:
    def __init__(
        self, 
        model_path: str,
        window_size: int = 640,
        overlap: float = 0.2,
        conf_threshold: float = 0.25,
        iou_threshold: float = 0.45
    ):
        
        self.model =YOLO("best.pt").cuda()
        #self.model =YOLO("C:/Users/silag/OneDrive/Belgeler/4.Sinif/Final_Project_PCB/belgeler/class_a/best.pt").cuda()
        
        print(f"Model is running on: {self.model.device}")
        self.window_size = window_size
        self.overlap = overlap
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        
        # Calculate stride based on overlap
        self.stride = int(window_size * (1 - overlap))
        print(f"Model loaded from {model_path}")
        print(f"Window size: {window_size}px, Overlap: {overlap*100}%, Stride: {self.stride}px")
    
    def process_image(self, image_path: str, visualize: bool = False, save_annotated: bool = True) -> List[Dict]:
        
        # Load image
        original_image = cv2.imread(image_path)
        if original_image is None:
            raise ValueError(f"Could not load image from {image_path}")
            
        # Convert to RGB for visualization purposes
        original_image_rgb = cv2.cvtColor(original_image, cv2.COLOR_BGR2RGB)
        height, width = original_image.shape[:2]
        
        print(f"Processing image of size {width}x{height}")
        
        # Calculate number of windows in each dimension
        num_windows_w = max(1, (width - self.window_size + self.stride) // self.stride)
        num_windows_h = max(1, (height - self.window_size + self.stride) // self.stride)
        
        # Add extra window if needed to cover the entire image
        if width > num_windows_w * self.stride:
            num_windows_w += 1
        if height > num_windows_h * self.stride:
            num_windows_h += 1
            
        print(f"Using {num_windows_w}x{num_windows_h} windows")
        
        # List to store all detections
        all_detections = []
        
        # Process each window
        for y in range(0, num_windows_h):
            for x in range(0, num_windows_w):
                # Calculate window coordinates
                x_start = min(x * self.stride, width - self.window_size)
                y_start = min(y * self.stride, height - self.window_size)
                x_end = min(x_start + self.window_size, width)
                y_end = min(y_start + self.window_size, height)
                
                # Extract window
                window = original_image_rgb[y_start:y_end, x_start:x_end]
                
                # Skip if window is too small
                if window.shape[0] < 10 or window.shape[1] < 10:
                    continue
                
                # Pad if window is smaller than expected
                if window.shape[0] < self.window_size or window.shape[1] < self.window_size:
                    padded_window = np.zeros((self.window_size, self.window_size, 3), dtype=np.uint8)
                    padded_window[:window.shape[0], :window.shape[1], :] = window
                    window = padded_window
                
                # Run detection on window
                results = self.model.predict(window, conf=self.conf_threshold, verbose=False)
                
                # Process detections
                for result in results:
                    boxes = result.boxes
                    for i, box in enumerate(boxes):
                        # Get detection details
                        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                        confidence = float(box.conf[0].cpu().numpy())
                        class_id = int(box.cls[0].cpu().numpy())
                        
                        # Adjust coordinates to original image
                        x1_adj = x1 + x_start
                        y1_adj = y1 + y_start
                        x2_adj = x2 + x_start
                        y2_adj = y2 + y_start
                        
                        # Save detection
                        detection = {
                            "bbox": [float(x1_adj), float(y1_adj), float(x2_adj), float(y2_adj)],
                            "confidence": confidence,
                            "class_id": class_id,
                            "class_name": result.names[class_id]
                        }
                        all_detections.append(detection)
        
        # Apply non-maximum suppression to remove duplicates
        filtered_detections = self._non_max_suppression(all_detections)
        
        if visualize:
            self._visualize_results(original_image_rgb, filtered_detections)
        
        if save_annotated:
            self._save_annotated_image(original_image, filtered_detections, image_path)
        
        return filtered_detections
    
    def _non_max_suppression(self, detections: List[Dict]) -> List[Dict]:
    
        if not detections:
            return []
            
        # Sort by confidence
        detections = sorted(detections, key=lambda x: x["confidence"], reverse=True)
        
        # Convert to numpy arrays for easier processing
        boxes = np.array([d["bbox"] for d in detections])
        scores = np.array([d["confidence"] for d in detections])
        classes = np.array([d["class_id"] for d in detections])
        
        # Lists to keep track of which detections to keep
        keep = []
        
        # Process each class separately
        for class_id in np.unique(classes):
            # Get indices of detections for this class
            class_indices = np.where(classes == class_id)[0]
            
            # Get boxes, scores for this class
            class_boxes = boxes[class_indices]
            class_scores = scores[class_indices]
            
            # Apply NMS
            while len(class_boxes) > 0:
                # Pick the detection with highest confidence
                max_idx = np.argmax(class_scores)
                max_box = class_boxes[max_idx]
                max_score = class_scores[max_idx]
                
                # Add to keep list
                keep.append(class_indices[max_idx])
                
                # Remove this detection
                class_boxes = np.delete(class_boxes, max_idx, axis=0)
                class_scores = np.delete(class_scores, max_idx)
                class_indices = np.delete(class_indices, max_idx)
                
                if len(class_boxes) == 0:
                    break
                
                # Calculate IoU with remaining boxes
                ious = self._calculate_iou(max_box, class_boxes)
                
                # Remove detections with IoU above threshold
                keep_indices = np.where(ious <= self.iou_threshold)[0]
                class_boxes = class_boxes[keep_indices]
                class_scores = class_scores[keep_indices]
                class_indices = class_indices[keep_indices]
        
        # Return filtered detections
        return [detections[i] for i in keep]
    
    def _calculate_iou(self, box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
       
        # Box coordinates
        x1, y1, x2, y2 = box
        
        # Calculate area of the main box
        box_area = (x2 - x1) * (y2 - y1)
        
        # Calculate coordinates of intersection
        xx1 = np.maximum(x1, boxes[:, 0])
        yy1 = np.maximum(y1, boxes[:, 1])
        xx2 = np.minimum(x2, boxes[:, 2])
        yy2 = np.minimum(y2, boxes[:, 3])
        
        # Calculate area of intersection
        w = np.maximum(0, xx2 - xx1)
        h = np.maximum(0, yy2 - yy1)
        intersection = w * h
        
        # Calculate area of boxes
        boxes_area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        
        # Calculate IoU
        union = box_area + boxes_area - intersection
        iou = intersection / union
        
        return iou
    
    def _visualize_results(self, image: np.ndarray, detections: List[Dict]) -> None:
     
        # Create figure and axes
        fig, ax = plt.subplots(1, figsize=(12, 12))
        
        # Display the image
        ax.imshow(image)
        
        # Create a color map for different classes
        cmap = plt.cm.get_cmap('tab10', 20)
        
        # Draw each detection
        for detection in detections:
            x1, y1, x2, y2 = detection["bbox"]
            class_id = detection["class_id"]
            class_name = detection["class_name"]
            confidence = detection["confidence"]
            
            # Create rectangle
            rect = patches.Rectangle(
                (x1, y1), x2-x1, y2-y1, 
                linewidth=1, 
                edgecolor=cmap(class_id),
                facecolor='none'
            )
            
            # Add rectangle to plot
            ax.add_patch(rect)
            
            # Add label
            plt.text(
                x1, y1-5, 
                f"{class_name}: {confidence:.2f}",
                color='white', 
                fontsize=10,
                bbox=dict(facecolor=cmap(class_id), alpha=0.8, linewidth=1)
            )
        
        # Set title
        plt.title(f"Detections: {len(detections)}")
        plt.axis('off')
        plt.tight_layout()
        plt.show()
    
    def _save_annotated_image(self, image: np.ndarray, detections: List[Dict], original_image_path: str) -> None:
      
        # Create a copy of the image to draw on
        annotated_image = image.copy()
        
        # Color mapping for different classes (BGR for OpenCV)
        colors = [
            (0, 0, 255),      # Red
            (0, 255, 0),      # Green
            (255, 0, 0),      # Blue
            (255, 0, 255),    # Magenta
            (0, 255, 255),    # Yellow
            (255, 165, 0),    # Orange
            (128, 0, 128),    # Purple
            (0, 128, 128),    # Teal
            (128, 128, 0),    # Olive
            (75, 0, 130)      # Indigo
        ]
        
        # Font settings
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale =1
        font_thickness = 1
        
        # Draw each detection
        for detection in detections:
            x1, y1, x2, y2 = map(int, detection["bbox"])
            class_id = detection["class_id"] % len(colors)  # Ensure color index is in range
            class_name = detection["class_name"]
            confidence = detection["confidence"]
            
            # Calculate center and radius for the circle
            center_x = int((x1 + x2) / 2)
            center_y = int((y1 + y2) / 2)
            radius = int(max(x2 - x1, y2 - y1) / 2)
            
            # Draw circle around the defect
            cv2.circle(annotated_image, (center_x, center_y), radius, colors[class_id], 3)
            
            # Prepare label text
            label = f"{class_name}: {confidence:.2f}"
            
            # Get text size
            (text_width, text_height), _ = cv2.getTextSize(label, font, font_scale, font_thickness)
            
            # Create text background
            # cv2.rectangle(
            #     annotated_image, 
            #     (x1, y1 - text_height - 10), 
            #     (x1 + text_width, y1), 
            #     colors[class_id], 
            #     -1
            # )
            
            # # Add label text
            # cv2.putText(
            #     annotated_image, 
            #     label, 
            #     (x1, y1 - 5), 
            #     font, 
            #     font_scale, 
            #     (255, 255, 255), 
            #     font_thickness
            # )
            

############ PREDICTION NAME #############
        pred_name=os.path.basename(original_image_path)
        pred_path=f"class_a_result/{pred_name}"
        os.makedirs(pred_path, exist_ok=True)

        # Save annotated image
        image_filename = os.path.join(pred_path, f"{pred_name}.png")
        cv2.imwrite(image_filename, annotated_image)
        print(f"Annotated image saved to: {image_filename}")
        
        
        detection_filename = os.path.join(pred_path, f"{pred_name}_detections.txt")
        with open(detection_filename, "w") as file:
            for i, detection in enumerate(detections, 1):
                file.write(f"Detection {i}: {detection['class_name']} - Confidence: {detection['confidence']:.2f}\n")

        print(f"Detections saved to: {detection_filename}")
        
        
        # with open(detection_filename, 'r') as f:
        #     annotations = f.readlines()
        #     for idx, line in enumerate(annotations):
        #         cv2.putText(annotated_image, line.strip(), (10, 30 + idx * 20),
        #             cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

        
        def resize_to_fit_screen(annotated_image, max_width=1280, max_height=720):
            h, w = annotated_image.shape[:2]
            scale_w = max_width / w
            scale_h = max_height / h
            scale = min(scale_w, scale_h, 1.0)  # Don't upscale
            new_w = int(w * scale)
            new_h = int(h * scale)
            return cv2.resize(annotated_image, (new_w, new_h), interpolation=cv2.INTER_AREA)
        resized_image = resize_to_fit_screen(annotated_image)
        cv2.imshow("Annotated Image", resized_image)
        cv2.waitKey(5000)  # Wait 5000 ms = 5 seconds
        cv2.destroyAllWindows()

 

# Example usage
# def main():
    
def main(image_path):
    print(f"Processing image: {image_path}")
    # Replace this with your actual processing logic
    # For example, load the image and apply YOLO detection

    ############ TRAIN NAME #############
    # input_image_path=latest_image
    # resized_image_path=f"C:/Users/buses/Desktop/PCB test images/cemre11/cemre1/cemre/resized_{train_name}.jpg"
    
    # # Resize image using ffmpeg
    # resize_command = [
    #     "ffmpeg",
    #     "-y",  # overwrite output file without asking
    #     "-i", input_image_path,
    #     "-vf", "scale='min(1000,iw)':'min(1000,ih)':force_original_aspect_ratio=decrease",
    #     resized_image_path
    # ]
    # subprocess.run(resize_command, check=True)


    image = cv2.imread(image_path)
    def order_points(pts):
        """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        diff = np.diff(pts, axis=1)

        rect[0] = pts[np.argmin(s)]      # top-left
        rect[2] = pts[np.argmax(s)]      # bottom-right
        rect[1] = pts[np.argmin(diff)]   # top-right
        rect[3] = pts[np.argmax(diff)]   # bottom-left

        return rect

    def crop_and_align_green_pcb(image_path, save_path=None, debug=False):
        image = cv2.imread(image_path)
        if image is None:
            raise ValueError(f"Could not read image: {image_path}")
        original = image.copy()

        # Convert to HSV and create mask for green
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

        # Green range (adjust as needed)
        lower_green = np.array([30, 30, 20])
        upper_green = np.array([85, 255, 255])

        mask = cv2.inRange(hsv, lower_green, upper_green)

        # Optional: Clean mask
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        # Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            raise ValueError("No contours found.")

        # Find the largest contour
        contours = sorted(contours, key=cv2.contourArea, reverse=True)
        for contour in contours:
            peri = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.02 * peri, True)

            if len(approx) == 4:
                # Found a rectangle
                pts = approx.reshape(4, 2)
                rect = order_points(pts)

                (tl, tr, br, bl) = rect
                widthA = np.linalg.norm(br - bl)
                widthB = np.linalg.norm(tr - tl)
                heightA = np.linalg.norm(tr - br)
                heightB = np.linalg.norm(tl - bl)
                maxWidth = int(max(widthA, widthB))
                maxHeight = int(max(heightA, heightB))

                # Destination points for warp
                dst = np.array([
                    [0, 0],
                    [maxWidth - 1, 0],
                    [maxWidth - 1, maxHeight - 1],
                    [0, maxHeight - 1]
                ], dtype="float32")

                # Perspective transform
                M = cv2.getPerspectiveTransform(rect, dst)
                warped = cv2.warpPerspective(original, M, (maxWidth, maxHeight))

                if save_path:
                    cv2.imwrite(image_path, warped)
                # if debug:
                #     cv2.imshow("Original", original)
                #     cv2.imshow("Mask", mask)
                #     cv2.imshow("Aligned PCB", warped)
                #     cv2.waitKey(0)
                #     cv2.destroyAllWindows()

                return warped

        raise ValueError("Could not find a rectangular PCB contour.")

    image_path = image_path
    output_path = image_path
    




    height, width = crop_and_align_green_pcb(image_path, save_path=output_path, debug=True).shape[:2]

    shorter_edge = min(width, height)
    adaptive_window_size = int(shorter_edge / 7*4)
    # scale_factor = shorter_edge / 3*2  # arbitrary scaling reference
    # adaptive_window_size = int(640 * scale_factor)
    # adaptive_window_size = min(max(adaptive_window_size, 640), 1024)  # clamp between 640 and 1024
    start_time = time.time()
    # Initialize detector
    detector = SlidingWindowDetector(
        model_path=YOLO("best.pt"),  # Replace with your model path
        window_size=adaptive_window_size,
        overlap=0.30,
        conf_threshold=0.25
    )
    

    # Process image
    image_path = image_path
    
    
    
    
    detections = detector.process_image(
        image_path, 
        # visualize=True,
        save_annotated=True
    )
    end_time = time.time()
    elapsed = end_time - start_time
    print(f"Processed {image_path} in {elapsed:.3f} seconds")
    if os.path.exists(image_path):
        os.remove(image_path)
        print(f"Deleted: {image_path}")
    else:
        print("File does not exist.")
    # Print results
    print(f"Found {len(detections)} objects")
    for i, detection in enumerate(detections):
        print(f"Detection {i+1}: {detection['class_name']} - Confidence: {detection['confidence']:.2f}")

# if __name__ == "__main__":
#      main(latest_image)
    




 