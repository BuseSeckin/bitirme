# <invoke name="artifacts">
# <parameter name="command">create</parameter>
# <parameter name="id">fixed_code</parameter>
# <parameter name="type">application/vnd.ant.code</parameter>
# <parameter name="language">python</parameter>
# <parameter name="title">Fixed RemoteStreamLauncher Class</parameter>
# <parameter name="content">
import paramiko
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import cv2
import numpy as np
import os
import threading
import traceback
import serial
from datetime import datetime


class RemoteStreamLauncher:
    def __init__(self, host, username, password, camera_configs, arduino_port, baud_rate=115200, base_save_dir=None):
        """
        Initialize with connection details and camera configurations
        
        Args:
        - host: IP address of the Raspberry Pi
        - username: SSH username
        - password: SSH password
        - camera_configs: List of dictionaries containing camera configurations
          Each config should have: script_path, port, save_dir, save_dir_after_motor_stop
        - arduino_port: COM port for Arduino serial connection
        - baud_rate: Serial baud rate (default 115200 to match Arduino)
        - base_save_dir: Base directory where all frame folders will be created
        """
        self.host = host
        self.username = username
        self.password = password
        self.camera_configs = camera_configs
        self.ssh = None
        self.start_time = None
        self.main_timings = {}  # Only store main timing components
        self.arduino_port = arduino_port
        self.baud_rate = baud_rate
        self.arduino = None
        self.motor_stopped = False
        self.motor_stop_event = threading.Event()
        self.base_save_dir = base_save_dir
        
        # Added debug logging
        self.debug_log = []
        self.log_debug("RemoteStreamLauncher initialized")
        
        # Create directories for saving frames
        for config in camera_configs:
            # Create initial directories
            save_dir = config.get('save_dir', 'captured_frames')
            if self.base_save_dir:
                save_dir = os.path.join(self.base_save_dir, save_dir)
            config['save_dir'] = save_dir  # Update config with full path
            os.makedirs(save_dir, exist_ok=True)
            
            # Create directories for after motor stops
            save_dir_after_stop = config.get('save_dir_after_motor_stop', f'{os.path.basename(save_dir)}_after_stop')
            if self.base_save_dir:
                save_dir_after_stop = os.path.join(self.base_save_dir, save_dir_after_stop)
            config['save_dir_after_motor_stop'] = save_dir_after_stop  # Update config with full path
            os.makedirs(save_dir_after_stop, exist_ok=True)

    def log_debug(self, message):
        """Add a timestamped debug message to the log"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        log_entry = f"[{timestamp}] {message}"
        self.debug_log.append(log_entry)
        print(f"DEBUG: {log_entry}")

    def log_main_timing(self, phase, duration):
        """Log only main timing phases"""
        self.main_timings[phase] = duration
        print(f"MAIN PHASE: {phase} completed in {duration:.2f} seconds")

    def connect(self):
        """Establish SSH connection"""
        start_time = time.time()
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            print(f"Connecting to {self.host}...")
            self.ssh.connect(self.host, username=self.username, password=self.password)
            print("SSH Connection established")
            success = True
        except Exception as e:
            print(f"SSH Connection Error: {e}")
            success = False
        
        duration = time.time() - start_time
        self.log_main_timing("SSH Connection", duration)
        return success

    def connect_arduino(self):
        """Establish Arduino serial connection"""
        start_time = time.time()
        try:
            self.arduino = serial.Serial(self.arduino_port, self.baud_rate, timeout=1)
            print(f"Arduino connected on {self.arduino_port} at {self.baud_rate} baud")
            # Clear any initial buffer data
            self.arduino.reset_input_buffer()
            self.arduino.reset_output_buffer()  # <-- Added this line
            success = True
            self.log_debug("Arduino connection successful")
        except Exception as e:
            print(f"Arduino Connection Error: {e}")
            self.log_debug(f"Arduino connection failed: {e}")
            success = False
    
        duration = time.time() - start_time
        self.log_main_timing("Arduino Connection", duration)
        return success


    def monitor_arduino(self):
        """Monitor Arduino for motor stop/start signals in a separate thread"""
        if not self.arduino:
            if not self.connect_arduino():
                print("Failed to connect to Arduino, motor stop monitoring disabled")
                return
    
        print("Starting Arduino monitoring thread...")
        self.log_debug("Arduino monitoring thread starting")
        
        def arduino_reader():
            self.log_debug("Arduino reader thread started")
            
            while not self.motor_stop_event.is_set():
                try:
                    if self.arduino.in_waiting > 0:
                        # Read single byte
                        data = self.arduino.read(1)
                       
                        if data:
                            #print(data)
                            signal = data[0]  # Get first byte value
                            if signal == 1:  # Motor stopped signal
                                self.log_debug(f"MOTOR STOP SIGNAL DETECTED (byte: {signal})")
                                print("\nMOTOR STOP SIGNAL DETECTED (byte: 1)")
                                self.motor_stopped = True
                                # Immediately notify all camera threads
                                self.log_debug(f"Setting motor_stopped to True")
                            else:
                                araba=0
                    else:
                        # Use a shorter sleep time to be more responsive
                        time.sleep(0.001)  # Check more frequently (1ms)
                except Exception as e:
                    self.log_debug(f"Error reading from Arduino: {e}")
                    print(f"Error reading from Arduino: {e}")
                    break
            
            self.log_debug("Arduino monitoring thread stopped")
            print("Arduino monitoring thread stopped")
        
        # Start the monitoring thread
        arduino_thread = threading.Thread(target=arduino_reader, daemon=True)
        arduino_thread.start()
        
        return arduino_thread

    def start_streams(self):
        """Start all camera stream scripts remotely"""
        if not self.ssh:
            if not self.connect():
                return False
        
        start_time = time.time()
        
        for config in self.camera_configs:
            script_path = config.get('script_path')
            if not script_path:
                continue
                
            try:
                # Kill any existing stream processes for this script
                kill_cmd = f'pkill -f "python3 {script_path}"'
                stdin, stdout, stderr = self.ssh.exec_command(kill_cmd)
                stderr_output = stderr.read().decode('utf-8').strip()
                if stderr_output:
                    print(f"Warning during kill command: {stderr_output}")
                time.sleep(1)  # Wait a bit longer to ensure processes are killed
                
                # Check if port is already in use
                port = config.get('port', 5000)
                check_port_cmd = f'netstat -tuln | grep :{port}'
                stdin, stdout, stderr = self.ssh.exec_command(check_port_cmd)
                port_in_use = stdout.read().decode('utf-8').strip()
                if port_in_use:
                    print(f"Warning: Port {port} appears to be in use already:")
                    print(port_in_use)
                    # Force kill any process on that port
                    self.ssh.exec_command(f'fuser -k {port}/tcp')
                    time.sleep(1)
                
                # Start the stream script in the background with output logging
                print(f"Starting stream: {script_path}")
                log_file = f"/tmp/stream_{port}.log"
                command = f'nohup python3 {script_path} > {log_file} 2>&1 &'
                stdin, stdout, stderr = self.ssh.exec_command(command)
                stderr_output = stderr.read().decode('utf-8').strip()
                if stderr_output:
                    print(f"Warning starting script: {stderr_output}")
                
                # Check if script started successfully by checking for errors in the log
                time.sleep(2)  # Give time for script to start and write to log
                stdin, stdout, stderr = self.ssh.exec_command(f'cat {log_file}')
                log_content = stdout.read().decode('utf-8').strip()
                if "Error" in log_content or "error" in log_content:
                    print(f"Warning: Possible error in script startup: {log_content}")
                
            except Exception as e:
                print(f"Error starting stream {script_path}: {e}")
                return False
        
        duration = time.time() - start_time
        self.log_main_timing("Remote Stream Launch", duration)
        return True

    def process_stream(self, config, stop_event, camera_timings):
        """
        Process a single remote stream with frame capture
        
        Args:
        - config: Dictionary with stream configuration
        - stop_event: Threading event to signal when to stop
        - camera_timings: Dictionary to store camera-specific timings
        """
        port = config.get('port', 5000)
        initial_save_dir = config.get('save_dir')  # Already contains full path
        save_dir_after_stop = config.get('save_dir_after_motor_stop')  # Already contains full path
        save_interval = config.get('save_interval', 0.0025)  # More frequent saves
        max_frames = config.get('max_frames', 50000)
        camera_name = config.get('name', f'camera_{port}')
        
        # Start with initial directory
        current_save_dir = initial_save_dir
        
        stream_url = f'http://{self.host}:{port}/video_feed'
        print(f"[{camera_name}] Connecting to {stream_url}")
        print(f"[{camera_name}] Initial save directory: {initial_save_dir}")
        print(f"[{camera_name}] After-stop save directory: {save_dir_after_stop}")
        
        # Track camera-specific timing
        connection_start = time.time()
        
        # Create a session with retry capability
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.3,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        
        try:
            # First try a quick HEAD request to check connectivity
            try:
                head_response = session.head(stream_url, timeout=2)
                print(f"[{camera_name}] HEAD request status: {head_response.status_code}")
            except Exception as e:
                print(f"[{camera_name}] HEAD request failed (this is often normal): {e}")
            
            # Connect to stream with increased timeout
            stream = session.get(stream_url, stream=True, timeout=5)
            
            connect_duration = time.time() - connection_start
            camera_timings["connection"] = connect_duration
            
            if stream.status_code != 200:
                print(f"[{camera_name}] Error: Status code {stream.status_code}")
                return
                
            print(f"[{camera_name}] Connected to stream")
            
            # Start processing timer
            processing_start = time.time()
            
            bytes_data = bytes()
            frame_count = 0
            green_count = 0
            initial_frames = 0
            after_stop_frames = 0
            last_save_time = time.time()
            frNUMBER = 14
            window_name = f'Stream - {camera_name}'
            #cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

            # Use a chunk iterator with a timeout
            chunk_iter = stream.iter_content(chunk_size=4096)
            
            # For tracking motor status changes
            last_motor_state = self.motor_stopped
            while not stop_event.is_set():
                current_time = time.time()
                
                # Check if motor status has changed
                if last_motor_state != self.motor_stopped:
                    if self.motor_stopped:
                        print(f"[{camera_name}] SWITCHING TO AFTER-STOP DIRECTORY: {save_dir_after_stop}")
                        self.log_debug(f"[{camera_name}] Switching to after-stop directory")
                    else:
                        print(f"[{camera_name}] SWITCHING TO INITIAL DIRECTORY: {initial_save_dir}")
                        self.log_debug(f"[{camera_name}] Switching to initial directory")
                    last_motor_state = self.motor_stopped
                
                # Determine which directory to use based on motor_stopped flag
                # This will dynamically switch between directories as the flag changes

                
                try:
                    # Set a timeout for getting the next chunk
                    chunk = next(chunk_iter)
                except StopIteration:
                    print(f"[{camera_name}] End of stream")
                    break
                except requests.exceptions.RequestException as e:
                    print(f"[{camera_name}] Stream connection error: {e}")
                    break
                except Exception as e:
                    print(f"[{camera_name}] Error reading stream: {e}")
                    break
                    
                if not chunk:
                    continue
                    
                bytes_data += chunk
                a = bytes_data.find(b'\xff\xd8')
                b = bytes_data.find(b'\xff\xd9')
                
                #print(self.motor_stopped, green_count)
                if green_count == 0:
                    color = (255, 0, 0)
                    current_save_dir = initial_save_dir
                elif (green_count == frNUMBER):
                    #print("sa")
                    color = (0, 255, 0)
                    current_save_dir = save_dir_after_stop

                else:
                    current_save_dir = initial_save_dir    
                    color = (0, 0, 255)

                
                if a != -1 and b != -1:
                    jpg_data = bytes_data[a:b+2]
                    bytes_data = bytes_data[b+2:]

                    # Decode frame
                    try:
                        
                        
                    
                        # Display frame
                        #cv2.imshow(window_name, frame)
                        
                        # Frame capture logic 
                        if current_time - last_save_time >= save_interval:
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                            filename = os.path.join(current_save_dir, f'frame_{timestamp}.jpg')
                            
                            # Add visual indicator of motor status
                            status_text = "MOTOR STOPPED" if self.motor_stopped else "MOTOR RUNNING"
                            status_color = (0, 0, 255) if self.motor_stopped else (0, 255, 0)  # Red or Green
                            #cv2.putText(frame, status_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                            #          1, status_color, 2, cv2.LINE_AA)
                            
                            #if green_count >= 1:
                                
                            last_save_time = current_time


                            if green_count >= 1:
                                if(green_count == frNUMBER):
                                    frame = cv2.imdecode(np.frombuffer(jpg_data, dtype=np.uint8), cv2.IMREAD_COLOR)    #AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
                                    frame[0:500, 0:500] = color
                                    cv2.imwrite(filename, frame)
                                    green_count = 0
                                else :
                                    green_count += 1
                                    
                            elif green_count == 0 and self.motor_stopped:
                                self.motor_stopped = False
                                green_count = 1
                            
                            frame_count += 1
                            
                            # Track frames per directory
                            if current_save_dir == initial_save_dir:
                                initial_frames += 1
                            else:
                                after_stop_frames += 1
                            
                            # Process key events faster
                            key = cv2.waitKey(1) & 0xFF
                            if key == ord('q'):
                                stop_event.set()
                                break
                    except Exception as e:
                        print(f"[{camera_name}] Error processing frame: {e}")
                        
            
            # Record processing time
            processing_duration = time.time() - processing_start
            camera_timings["processing"] = processing_duration
            camera_timings["frames_captured"] = frame_count
            camera_timings["initial_frames"] = initial_frames
            camera_timings["after_stop_frames"] = after_stop_frames
            
        except requests.exceptions.ConnectionError as e:
            print(f"[{camera_name}] Stream connection error: {e}")
        except Exception as e:
            print(f"[{camera_name}] Stream error: {e}")
            traceback.print_exc()  # Print the full traceback for debugging
        finally:
            try:
                cv2.destroyWindow(window_name)
            except:
                pass
            print(f"[{camera_name}] Stream stopped")

    def process_all_streams(self):
        """Process all streams concurrently using threads"""
        threads = []
        stop_event = threading.Event()
        camera_timings = {}
        
        # Set the start time
        self.start_time = time.time()
        stream_connection_start = time.time()
        
        try:
            # First start Arduino monitoring thread
            arduino_thread = self.monitor_arduino()
            
            # Start threads for each stream
            for config in self.camera_configs:
                camera_name = config.get('name', f'camera_{config.get("port", 5000)}')
                camera_timings[camera_name] = {}
                
                thread = threading.Thread(
                    target=self.process_stream,
                    args=(config, stop_event, camera_timings[camera_name]),
                    name=f"Thread-{camera_name}"
                )
                thread.daemon = True
                threads.append(thread)
                thread.start()
                
                # Add a bit more delay between thread starts to avoid overwhelming connections
                time.sleep(0.5)
            
            # Run until manually stopped
            max_runtime = 600  # Maximum runtime in seconds (10 minutes)
            main_end_time = time.time() + max_runtime
            
            print("\nMonitoring motor signals from Arduino...")
            print("Frames will save continuously, switching directories based on motor state")
            print("Press 'q' in any video window to stop")
            
            # Manual testing - simulate button press
            print("\nALSO: You can press 't' in the terminal to test the motor stop functionality")
            
            while any(thread.is_alive() for thread in threads):
                # Check if we've reached max runtime
                if time.time() >= main_end_time:
                    print("Maximum runtime reached, stopping")
                    stop_event.set()
                    break
                
                # Check for keyboard interrupt
                if stop_event.is_set():
                    break
                    
                # Manual test functionality
                if os.name == 'nt':  # Windows
                    import msvcrt
                    if msvcrt.kbhit():
                        key = msvcrt.getch()
                        if key == b't':
                            # Toggle motor state for testing
                            self.motor_stopped = not self.motor_stopped
                            state = "STOPPED" if self.motor_stopped else "RUNNING"
                            print(f"\nTEST: Manually toggled motor state to {state}")
                            #self.log_debug(f"TEST: Manually set motor_stopped to {self.motor_stopped}")
                else:  # Unix/Linux/Mac
                    # This is a non-blocking check - won't work in all terminals
                    # A full implementation would use select or curses
                    pass
                    
                time.sleep(0.1)
                
        except KeyboardInterrupt:
            print("\nProgram interrupted")
        except Exception as e:
            print(f"Error: {e}")
            traceback.print_exc()  # Print the full traceback for debugging
        finally:
            # Signal all threads to stop
            stop_event.set()
            self.motor_stop_event.set()
            
            # Give threads a short time to finish
            for thread in threads:
                if thread.is_alive():
                    thread.join(timeout=1.0)
            
            # Close Arduino connection
            if self.arduino:
                try:
                    self.arduino.close()
                    print("Arduino connection closed")
                except:
                    pass
                    
            cv2.destroyAllWindows()
            
            # Log video streaming phase timing
            stream_duration = time.time() - stream_connection_start
            self.log_main_timing("Video Streaming and Processing", stream_duration)
            
            # Log camera-specific timing
            for camera_name, timing in camera_timings.items():
                conn_time = timing.get("connection", 0)
                proc_time = timing.get("processing", 0)
                frames = timing.get("frames_captured", 0)
                initial_frames = timing.get("initial_frames", 0)
                after_stop_frames = timing.get("after_stop_frames", 0)
                print(f"{camera_name}: Connection: {conn_time:.2f}s, Processing: {proc_time:.2f}s, " +
                      f"Total Frames: {frames}, Initial: {initial_frames}, After Stop: {after_stop_frames}")
            
            print("All streams stopped.")
            self.print_timing_summary()
            
            # Print motor stop status
            if self.motor_stopped:
                print("Motor is currently in stopped state.")
            else:
                print("Motor is currently in running state.")
            
            # Print debug log summary
            self.print_debug_log()
    
    def print_timing_summary(self):
        """Print a summary of all main timing components"""
        print("\n===== MAIN TIMING COMPONENTS =====")
        total = 0
        for phase, duration in self.main_timings.items():
            print(f"{phase}: {duration:.2f} seconds")
            total += duration
        
        program_duration = time.time() - self.start_time + self.main_timings.get("SSH Connection", 0) + self.main_timings.get("Remote Stream Launch", 0)
        print(f"Total Program Duration: {program_duration:.2f} seconds")
        print("=================================\n")
    
    def print_debug_log(self):
        """Print the debug log"""
        print("\n===== DEBUG LOG =====")
        for entry in self.debug_log:
            print(entry)
        print("=====================\n")
    
    def check_stream_status(self):
        """Check status of the stream servers on the RPi"""
        if not self.ssh:
            if not self.connect():
                return False
                
        print("\nChecking stream server status...")
        
        for config in self.camera_configs:
            port = config.get('port', 5000)
            camera_name = config.get('name', f'camera_{port}')
            
            # Check if process is running
            script_path = config.get('script_path')
            stdin, stdout, stderr = self.ssh.exec_command(f'pgrep -f "python3 {script_path}"')
            pid = stdout.read().decode('utf-8').strip()
            
            if pid:
                print(f"[{camera_name}] Process is running with PID: {pid}")
            else:
                print(f"[{camera_name}] Process is NOT running")
            
            # Check if port is open
            stdin, stdout, stderr = self.ssh.exec_command(f'netstat -tuln | grep :{port}')
            port_info = stdout.read().decode('utf-8').strip()
            
            if port_info:
                print(f"[{camera_name}] Port {port} is OPEN: {port_info}")
            else:
                print(f"[{camera_name}] Port {port} is NOT listening")
                
        return True


def main():


    # Replace with your specific details
    RPI_IP = '169.254.91.42'
    USERNAME = 'talha'
    PASSWORD = 'talha'
    
    # Arduino serial port configuration
    ARDUINO_PORT = 'COM9'  # Change to your Arduino port
    ARDUINO_BAUD = 115200  # Matching Arduino baud rate
    
    # Base directory for all captured frames
    BASE_SAVE_DIR = r'C:\Users\silag\OneDrive\Belgeler\4.Sinif\Final_Project_PCB\belgeler\data_exchange'
    
    # Define camera configurations with relative directory names
    camera_configs = [
        # {
            # 'name': 'camera',
            # 'script_path': '/home/talha/Desktop/stream.py',
            # 'port': 5000,
            # 'save_dir': 'captured_frames',
            # 'save_dir_after_motor_stop': 'captured_frames_after_stop',
            # 'save_interval': 0.005,  # Faster saving
            # 'max_frames': 50000
        # },
        {
            'name': 'camera1',
            'script_path': '/home/talha/Desktop/stream1.py',
            'port': 5001,
            'save_dir': 'captured_frames1',
            'save_dir_after_motor_stop': 'captured_frames1_after_stop',
            'save_interval': 0.0025,  # Faster saving
            'max_frames': 50000
        }
    ]
    
    # Create stream launcher with Arduino support and base directory
    launcher = RemoteStreamLauncher(RPI_IP, USERNAME, PASSWORD, camera_configs, ARDUINO_PORT, ARDUINO_BAUD, BASE_SAVE_DIR)
    
    try:
        # Start all stream scripts remotely
        if launcher.start_streams():
            # Add a delay to ensure streams are fully initialized
            print("Waiting for streams to initialize...")
            time.sleep(3)
            
            # Check stream status to verify they're running
            launcher.check_stream_status()
            
            print("Starting stream processing...")
            print(f"- Saving to directories under: {BASE_SAVE_DIR}")
            print("- Will switch to after-stop directories when Arduino signals motor stop")
            print("- Will switch back to initial directories when Arduino signals motor start")
            print("- Press 'q' in any video window to stop")
            print("- Press 't' in terminal to manually test motor stop functionality")
            launcher.process_all_streams()
        else:
            print("Failed to start stream scripts")
    
    except KeyboardInterrupt:
        print("Program terminated by user")
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()

if __name__ == '__main__':
    main()
#     </parameter>
# </invoke>