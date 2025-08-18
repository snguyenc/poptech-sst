#!/usr/bin/env python3
"""
Audio Capture Native App
Capture mic + system audio và gửi lên WebSocket STT server
Giao tiếp với Chrome Extension qua WebSocket Local Server
"""

import asyncio
import websockets
import functools
import logging
import os
import logging.handlers
import sys
from typing import Dict, Any, Optional, Set
import threading
import signal
import numpy as np
from collections import deque
import json
import time
from websockets.legacy.server import WebSocketServerProtocol
import ssl
import librosa
from auto_summary import AutoSummary
# GIẢI PHÁP: Import đúng thư viện cho từng hệ điều hành
if sys.platform == "win32":
    # Trên Windows, chúng ta dùng pyaudiowpatch
    # và đổi tên nó thành "pyaudio" để phần còn lại của code không cần thay đổi.
    import pyaudiowpatch as pyaudio
else:
    # Trên macOS và Linux, chúng ta dùng pyaudio gốc.
    import pyaudio


ECHO_FACTOR = 0.3  # Adjust this value (0.1 - 0.5)
MIC_GAIN = 1.1
CHUNK_DURATION = 0.032
INT16_MAX_ABS_VALUE = 32768.0
DEFAULT_SAMPLE_RATE = 16000
PYAUDIO_LOCK = threading.Lock()
N8N_AUTO_SUMMARY_WEBHOOK_URL = "https://n8n.securityzone.vn/webhook/rt-auto-summary"
N8N_AUTO_SUMMARY_INTERVAL = 60

SUMMARY_MIN_WORDS = 30
DEFAULT_WSS_URL = "wss://rtsttws-demo.securityzone.vn"

def get_log_level_from_env_or_config(config: Optional[Dict] = None) -> str:
    """Get log level from environment variable or config file"""
    
    # Priority 1: Environment variable
    env_level = os.getenv('LOG_LEVEL', '').upper()
    if env_level in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
        print(f"Using log level from environment: {env_level}")
        return env_level
    
    # Priority 2: Config file
    if config and 'logging' in config:
        config_level = config['logging'].get('level', 'INFO').upper()
        print(f"Using log level from config: {config_level}")
        return config_level
    
    # Priority 3: Default
    print("Using default log level: INFO")
    return 'INFO'

def setup_flexible_logging(config_file='config.json'):
    """Setup logging with environment override support"""
    
    # Load config if exists
    config = None
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:
            print(f"Error loading config: {e}")
    
    # Get settings with environment override
    log_level = get_log_level_from_env_or_config(config)
    
    # Other settings from environment or config
    log_file = os.getenv('LOG_FILE')
    if not log_file and config:
        log_file = config.get('logging', {}).get('file')
    
    log_format = os.getenv('LOG_FORMAT', 
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    console_enabled = os.getenv('LOG_CONSOLE', 'true').lower() == 'true'
    
    # Convert to numeric level
    numeric_level = getattr(logging, log_level, logging.INFO)
    
    # Setup handlers
    handlers = []
    
    if console_enabled:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(numeric_level)
        console_formatter = logging.Formatter(log_format)
        console_handler.setFormatter(console_formatter)
        handlers.append(console_handler)
    
    if log_file:
        try:
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir)
                
            file_handler = logging.handlers.RotatingFileHandler(
                log_file, 
                maxBytes=int(os.getenv('LOG_MAX_BYTES', '10485760')), 
                backupCount=int(os.getenv('LOG_BACKUP_COUNT', '5')),
                encoding='utf-8'
            )
            file_handler.setLevel(numeric_level)
            file_formatter = logging.Formatter(log_format)
            file_handler.setFormatter(file_formatter)
            handlers.append(file_handler)
            print(f"File logging: {log_file}")
        except Exception as e:
            print(f"File logging error: {e}")
    
    # Configure logging
    logging.basicConfig(
        level=numeric_level,
        format=log_format,
        handlers=handlers,
        force=True
    )
    
    print(f"Logging setup complete: level={log_level}, handlers={len(handlers)}")
    
    return config
class AudioCaptureNative:
    def __init__(self, config: Optional[Dict] = None):
        self.is_recording = False
        self.stt_websocket = None  # WebSocket connection to STT server
        self.mic_thread = None
        self.system_thread = None
        self.audio_task = None
        self.receiver_task = None
        self.heartbeat_task = None
        self.logger = logging.getLogger(__name__)
        
        # WebSocket server for extension communication
        self.extension_clients: Set[WebSocketServerProtocol] = set()
        self.local_server = None
        self.local_port = 8766
        self.devices = None
        if config:
            self.show_debug = config.get('logging', {}).get('sound_debug', False)
        else:
            self.show_debug = True

        self.config = {
            'serverUrl': DEFAULT_WSS_URL,
            'sampleRate': DEFAULT_SAMPLE_RATE,
            'language': 'vi',
            'channels': 2,  # Luôn xử lý stereo (mic+system)
            'systemDeviceName': 'BlackHole'  # Tên thiết bị system audio
        }

        self.mic_buffer = deque(maxlen=100)  # Limit buffer size
        self.system_buffer = deque(maxlen=100)
        self.shutdown_event = threading.Event()
        
        # Add connection state tracking
        self.stt_connected = False
        self.stt_ready = False
        self.loop = asyncio.get_event_loop()

        # Auto Summary
        self.auto_summary = None
        if config and 'auto_summary' in config:
            summary_config = config['auto_summary']
            webhook_url = summary_config.get('n8n_webhook_url')
            interval = summary_config.get('interval_seconds', N8N_AUTO_SUMMARY_INTERVAL)
            min_summary_words = summary_config.get('min_summary_words', SUMMARY_MIN_WORDS)
            sst_ws_url = summary_config.get('sst_ws_url', DEFAULT_WSS_URL)
            self.config['serverUrl'] = sst_ws_url
            if webhook_url and webhook_url != 'YOUR_N8N_WEBHOOK_URL_HERE':
                self.auto_summary = AutoSummary(
                    n8n_webhook_url=webhook_url, 
                    interval=interval,
                    summary_callback=self.send_to_extension,
                    loop=self.loop,
                    min_summary_words=min_summary_words,
                )
                log.info("AutoSummary service initialized.")
            else:
                log.warning("AutoSummary is configured but n8n_webhook_url is missing or not set.")

        else: 
            self.auto_summary = AutoSummary(
                n8n_webhook_url=N8N_AUTO_SUMMARY_WEBHOOK_URL, 
                interval=N8N_AUTO_SUMMARY_INTERVAL,
                summary_callback=self.send_to_extension,
                loop=self.loop,
                min_summary_words=SUMMARY_MIN_WORDS
            )
            log.info("AutoSummary service initialized with default config.")

    async def send_to_extension(self, message: Dict[Any, Any]):
        """Send message to all connected extension clients"""
        if not self.extension_clients:
            log.debug("No extension clients connected")
            return
            
        message_str = json.dumps(message)
        disconnected_clients = set()
        
        for client in self.extension_clients:
            try:
                await client.send(message_str)
                log.info(f"Sent to extension: client {client} message {message}")
            except websockets.exceptions.ConnectionClosed:
                log.info("Extension client disconnected")
                disconnected_clients.add(client)
            except Exception as e:
                log.error(f"Error sending to extension: {e}")
                disconnected_clients.add(client)
        
        # Remove disconnected clients
        self.extension_clients -= disconnected_clients

    async def connect_stt_websocket(self, server_url: str):
        """Connect to STT WebSocket with better error handling"""
        try:
            log.info(f"Connecting to STT WebSocket: {server_url}")
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            self.stt_websocket = await websockets.connect(
                server_url, 
                ssl=ssl_context,
                ping_interval=20, 
                ping_timeout=30,
                close_timeout=10
            )
            self.stt_connected = True
            self.stt_ready = False  # Reset ready state
            log.info("STT WebSocket connected.")
            return True
        except Exception as e:
            log.error(f"STT WebSocket connection failed: {e}")
            self.stt_websocket = None
            self.stt_connected = False
            self.stt_ready = False
            return False

    async def disconnect_stt_websocket(self):
        """Disconnect from STT WebSocket"""
        if self.stt_websocket:
            try:
                # Send end stream before closing
                if self.stt_connected:
                    try:
                        end_message = {"type": "end_stream"}
                        await asyncio.wait_for(
                            self.stt_websocket.send(json.dumps(end_message)), 
                            timeout=2.0
                        )
                        log.info("Sent end_stream message")
                    except Exception as e:
                        log.warning(f"Could not send end_stream: {e}")
                
                await self.stt_websocket.close()
                log.info("STT WebSocket disconnected.")
            except Exception as e:
                log.error(f"Error disconnecting STT WebSocket: {e}")
            finally:
                self.stt_websocket = None
                self.stt_connected = False
                self.stt_ready = False

    def _find_device_by_name(self, name_keyword: str) -> Optional[int]:
        name_keyword = name_keyword.lower()
        log.info(f"Searching for device with keyword: '{name_keyword}'")
        for device in self.devices:
            if name_keyword in device['name'].lower() and device['channels'] > 0:
                log.info(f"Found device '{device['name']}' at index {device['index']}")
                return device['index']
        log.warning(f"Could not find any input device matching '{name_keyword}'")
        return None

    def _find_device_by_index(self, index: int) -> Optional[dict]:
        log.info(f"Searching for device with index: '{index}'")
        for device in self.devices:
            if device['index'] == index and device['channels'] > 0:
                log.info(f"Found device '{device['name']}' at index {device['index']}")
                return device

        log.warning(f"Could not find any input device matching '{index}'")
        return None

    def init_audio_devices(self):
        p = pyaudio.PyAudio()
        devices = []
        
        try:
            device_count = p.get_device_count()
            log.info(f"Get audio devices for system: {sys.platform}")
            '''
            # Lấy thông tin của thiết bị ĐẦU VÀO mặc định
            default_input_device_info = p.get_default_input_device_info()
            log.info("--- Thông tin Mic mặc định ---")
            log.info(f"Index: {default_input_device_info['index']}")
            log.info(f"Tên: {default_input_device_info['name']}")
            log.info(f"Max Input Channels: {default_input_device_info['maxInputChannels']}")
            inputIndex = default_input_device_info['index']
            
            if sys.platform == "win32":
                waapis = p.get_default_wasapi_device()
                log.info(f"--- Thông tin Mic waapis mặc định ---")
                log.info(f"Index: {waapis['index']}")
                log.info(f"Tên: {waapis['name']}")
                log.info(f"Max Input Channels: {waapis['maxInputChannels']}")
            '''
            for i in range(device_count):

                device_info = p.get_device_info_by_index(i)
                 # Format device info 
                device_data = {
                    'index': i,
                    'name': device_info['name'],
                    'host_api': p.get_host_api_info_by_index(device_info['hostApi'])['name'],
                    'max_input_channels': device_info['maxInputChannels'],
                    'max_output_channels': device_info['maxOutputChannels'],
                    'default_sample_rate': device_info['defaultSampleRate'],
                    'is_loopback': device_info.get('isLoopbackDevice', False)
                }
                log.info(f"Device {i} => {device_data}")
                if device_info['maxInputChannels'] > 0 and not device_info.get('isLoopbackDevice', False):
                    devices.append({
                        'id': i,
                        'index': i,
                        'name': device_info['name'],
                        'type': 'input',
                        'channels': device_info['maxInputChannels'],
                        'sampleRate': int(device_info['defaultSampleRate'])
                    })

                if device_info['maxInputChannels'] > 0 and (device_info.get('isLoopbackDevice', False) or device_info['name'].lower().startswith('blackhole')) :
                    devices.append({
                        'id': i,
                        'index': i,
                        'name': device_info['name'],
                        'type': 'loopback',
                        'channels': device_info['maxInputChannels'],
                        'sampleRate': int(device_info['defaultSampleRate'])
                    })

        except Exception as e:
            log.error(f"Error getting init audio devices: {e}")
        finally:
            p.terminate()

        return devices      
        
    def get_audio_devices(self):
        if self.devices is not None and len(self.devices) > 0:
            return {"action": "get_devices", "status": "success", "devices": self.devices}
        else:
            return {"action": "get_devices", "status": "success", "devices": []}

    def _recording_loop(self, device_id: int, sample_rate: int, channels: int, buffer: deque, stream_name: str):
        stream = None
        p = None

        try:
            # === KHỞI TẠO MỘT LẦN DUY NHẤT ===
            with PYAUDIO_LOCK:
                p = pyaudio.PyAudio()
                log.info(f"init pyaudio {stream_name} on device {device_id} {sample_rate}Hz {channels}ch")
            
            retry_count = 0
            max_retries = 5
        
            while self.is_recording and not self.shutdown_event.is_set() and retry_count < max_retries:
                try:
                    log.info(f"Opening stream for {stream_name} on device {device_id} {sample_rate}Hz {channels}ch (attempt {retry_count + 1})")
                    frames_per_buffer = int(CHUNK_DURATION * sample_rate)
                    stream = p.open(
                        format=pyaudio.paInt16,
                        channels=channels,  # Ghi mono
                        rate=sample_rate,
                        input=True,
                        input_device_index=device_id,
                        frames_per_buffer=frames_per_buffer
                    )
                    log.info(f"{stream_name} stream opened successfully.")
                    
                    # Reset retry count on successful connection
                    retry_count = 0
                    
                    # Main recording loop
                    consecutive_errors = 0
                    while self.is_recording and not self.shutdown_event.is_set():
                        try:
                            data = stream.read(frames_per_buffer, exception_on_overflow=False)
                            #self.logger.info(f"Data len: {stream_name} => {frames_per_buffer} - {len(data)}")
                            if channels == 2:
                                stereo_array = np.frombuffer(data, dtype=np.int16)
                                stereo_reshaped = stereo_array.reshape(-1, 2)
                                mono_array = np.mean(stereo_reshaped, axis=1).astype(np.int16)  # Giữ int16
                                data = mono_array.tobytes()  # Convert về bytes
                            
                            if len(buffer) < buffer.maxlen:  # Check buffer capacity
                                buffer.append(data)

                            consecutive_errors = 0  # Reset error count on success
                            
                        except Exception as read_error:
                            consecutive_errors += 1
                            log.warning(f"Read error in {stream_name}: {read_error} (consecutive: {consecutive_errors})")
                            
                            if consecutive_errors > 15:
                                log.error(f"Too many consecutive read errors in {stream_name}, restarting stream")
                                break  # Break inner loop to restart stream
                            
                            time.sleep(0.01)
                            
                except Exception as e:
                    retry_count += 1
                    log.error(f"Error in {stream_name} recording loop (attempt {retry_count}): {e}")
                    
                    if retry_count < max_retries:
                        backoff_delay = min(retry_count * 1.5, 5)
                        log.info(f"Retrying {stream_name} stream in {backoff_delay} seconds...")
                        time.sleep(backoff_delay)
                    else:
                        log.error(f"Max retries reached for {stream_name}, giving up")
                        
                finally:
                    
                    if stream:
                        try:
                            stream.stop_stream()
                            stream.close()
                            log.info(f"Close stream {stream_name}")
                        except Exception as close_error:
                            log.warning(f"Error closing {stream_name} stream: {close_error}")
                        stream = None
        except Exception as outer_e:
            self.logger.error(f"Lỗi nghiêm trọng không thể phục hồi trong thread {stream_name}: {outer_e}")
        finally:
            if p:
                p.terminate()
            log.info(f"{stream_name} recording loop and PyAudio instance terminated.")

        log.info(f"{stream_name} recording loop finished.")

    async def _heartbeat_task(self):
        """Send periodic heartbeats to check connection health"""
        while self.is_recording and self.stt_websocket:
            try:
                await asyncio.sleep(15)  # Check every 15 seconds
                if self.stt_websocket and self.stt_connected:
                    try:
                        pong_waiter = await self.stt_websocket.ping()
                        await asyncio.wait_for(pong_waiter, timeout=10)
                        log.debug("Heartbeat successful")
                    except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed) as e:
                        log.warning(f"Heartbeat failed: {e}, marking connection as dead")
                        self.stt_connected = False
                        self.stt_ready = False
                        break
            except Exception as e:
                log.error(f"Error in heartbeat task: {e}")
                await asyncio.sleep(5)
        
        log.info("Heartbeat task finished")

    async def setup_stt_connection(self):
        """Setup STT connection with proper configuration"""
        try:
            # Send config
            ws_config = {
                "type": "config",
                "client_name": "Agent 001",
                "audio_format": "pcm16",
                "language": self.config['language'],
                "sample_rate": self.config['sampleRate'],
                "channels": self.config['channels']
            }
            await self.stt_websocket.send(json.dumps(ws_config))
            log.info(f"Sent STT WebSocket config: {ws_config}")
            
            # Wait for config acknowledgment (optional, based on your server)
            await asyncio.sleep(0.5)
            
            # Send start stream
            start_message = {
                "type": "start_stream",
                #"client_id": "native_audio_capture"
            }
            await self.stt_websocket.send(json.dumps(start_message))
            log.info("Sent start_stream event to STT WebSocket.")
            
            # Mark as ready after successful setup
            self.stt_ready = True
            return True
            
        except Exception as e:
            log.error(f"Error setting up STT connection: {e}")
            self.stt_ready = False
            return False
    
    async def restart_receiver_task(self):
        """Restart the websocket receiver task after reconnection"""
        try:
            # Cancel old receiver task if it exists
            if self.receiver_task and not self.receiver_task.done():
                self.receiver_task.cancel()
                try:
                    await self.receiver_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    log.error(f"Error cancelling old receiver task: {e}")
            
            # Start new receiver task
            self.receiver_task = asyncio.create_task(self.websocket_receiver())
            log.info("WebSocket receiver task restarted after reconnection")
        except Exception as e:
            log.error(f"Error restarting receiver task: {e}")

    def _process_audio_chunk(self, mic_data: bytes, system_data: bytes) -> bytes:
        """
        Hàm đồng bộ (synchronous) này thực hiện tất cả các tác vụ nặng.
        Nó sẽ được chạy trong một thread riêng bởi run_in_executor.
        """
        try:
            # Chuyển đổi và chuẩn hóa
            mic_array = np.frombuffer(mic_data, dtype=np.int16).astype(np.float32) / INT16_MAX_ABS_VALUE
            system_array = np.frombuffer(system_data, dtype=np.int16).astype(np.float32) / INT16_MAX_ABS_VALUE

            # Resample mic
            if self.config['inputSampleRate'] != self.config['sampleRate']:
                mic_processed = librosa.resample(y=mic_array, orig_sr=self.config['inputSampleRate'], target_sr=self.config['sampleRate'])
            else:
                mic_processed = mic_array

            # Resample system
            if self.config['systemSampleRate'] != self.config['sampleRate']:
                system_processed = librosa.resample(y=system_array, orig_sr=self.config['systemSampleRate'], target_sr=self.config['sampleRate'])
            else:
                system_processed = system_array

            # Đồng bộ độ dài
            min_len = min(len(mic_processed), len(system_processed))
            if min_len == 0:
                return b''  # Trả về bytes rỗng nếu không có gì

            mic_processed = mic_processed[:min_len]
            system_processed = system_processed[:min_len]

            # Khử nhiễu, tăng âm và clip
            mic_cleaned = (mic_processed - (system_processed * ECHO_FACTOR)) * MIC_GAIN
            mic_cleaned_int16 = np.clip(mic_cleaned * INT16_MAX_ABS_VALUE, -INT16_MAX_ABS_VALUE, INT16_MAX_ABS_VALUE).astype(np.int16)
            
            system_gain = system_processed * MIC_GAIN
            system_int16 = np.clip(system_gain * INT16_MAX_ABS_VALUE, -INT16_MAX_ABS_VALUE, INT16_MAX_ABS_VALUE).astype(np.int16)

            if self.show_debug:
                mic_rms = np.sqrt(np.mean(mic_cleaned_int16.astype(np.float32)**2))
                system_rms = np.sqrt(np.mean(system_int16.astype(np.float32)**2))
                self.logger.info(f"Mic RMS: {mic_rms:.1f}, System RMS: {system_rms:.1f}")

            # Tạo dữ liệu stereo và trả về
            return np.column_stack((mic_cleaned_int16, system_int16)).tobytes()
            
        except Exception as e:
            self.logger.error(f"Lỗi trong thread xử lý audio: {e}")
            return b''
            
    async def audio_sender(self):
        """Enhanced audio sender with better reconnection handling"""
        log.info("Audio sender task started.")
        reconnect_attempts = 0
        max_reconnect_attempts = 5
        backoff_delay = 1
        loop = asyncio.get_running_loop() # Lấy event loop hiện tại
        
        while self.is_recording:
            try:
                # Check STT WebSocket connection
                if not self.stt_websocket or not self.stt_connected:
                    if reconnect_attempts < max_reconnect_attempts:
                        log.warning(f"STT WebSocket disconnected, attempting reconnect {reconnect_attempts + 1}/{max_reconnect_attempts}")
                        await self.send_to_extension({
                            "status": "warning", 
                            "message": f"Reconnecting to server ({reconnect_attempts + 1}/{max_reconnect_attempts})..."
                        })
                        
                        if await self.connect_stt_websocket(self.config['serverUrl']):
                            # Setup connection properly
                            if await self.setup_stt_connection():
                                # CRITICAL: Restart receiver task with new connection
                                await self.restart_receiver_task()
                                
                                reconnect_attempts = 0
                                backoff_delay = 1
                                log.info("STT WebSocket reconnected and configured successfully")
                                await self.send_to_extension({
                                    "status": "success", 
                                    "message": "Reconnected to server successfully"
                                })
                            else:
                                log.error("Failed to setup STT connection after reconnect")
                                self.stt_connected = False
                                reconnect_attempts += 1
                        else:
                            reconnect_attempts += 1
                            backoff_delay = min(backoff_delay * 2, 10)
                            log.warning(f"Reconnect failed, waiting {backoff_delay}s before retry")
                            await asyncio.sleep(backoff_delay)
                            continue
                    else:
                        log.error("Max reconnection attempts reached, stopping recording")
                        self.is_recording = False
                        await self.send_to_extension({
                            "status": "error", 
                            "message": "WebSocket connection lost after multiple reconnection attempts"
                        })
                        break
                
                # Only send audio if connection is ready
                if self.stt_ready and len(self.mic_buffer) > 0:
                    try:
                        mic_data = self.mic_buffer.popleft()
                        # Kiểm tra xem có dữ liệu hệ thống tương ứng không
                        if len(self.system_buffer) > 0:
                            system_data = self.system_buffer.popleft()
                        else:
                            # NẾU KHÔNG CÓ, TẠO RA DỮ LIỆU IM LẶNG
                            # Độ dài của nó phải bằng với độ dài của mic_data
                            # b'\x00' là một byte rỗng (im lặng)
                            system_data = b'\x00' * len(mic_data) # <-- ĐÂY LÀ BƯỚC FIX LỖI CHÍNH

                        stereo_data = await loop.run_in_executor(
                            None,  # Dùng ThreadPoolExecutor mặc định
                            self._process_audio_chunk,
                            mic_data,
                            system_data
                        )
                        
                        if stereo_data and self.stt_websocket and self.stt_connected:
                            try:
                                await self.stt_websocket.send(stereo_data)
                            except websockets.exceptions.ConnectionClosed as e:
                                self.logger.error(f"WebSocket connection closed during send: {e}") 
                                self.stt_connected = False
                                self.stt_ready = False
                                reconnect_attempts += 1
                                continue
                    except Exception as e:
                        log.error(f"Error sending audio data: {e}")
                        await asyncio.sleep(0.1)
                else:
                    await asyncio.sleep(0.01)
                    
            except asyncio.CancelledError:
                log.info("Audio sender task was cancelled.")
                break
            except Exception as e:
                log.error(f"Lỗi nghiêm trọng trong audio_sender: {e}")
                await asyncio.sleep(1)
                
        log.info("Audio sender task finished.")

    async def websocket_receiver(self):
        """Enhanced WebSocket receiver with better error handling"""
        log.info("WebSocket receiver task started.")
        
        while self.is_recording:
            try:
                if self.stt_websocket and self.stt_connected:
                    try:
                        response = await asyncio.wait_for(self.stt_websocket.recv(), timeout=1.0)
                        
                        # Handle both text and binary messages
                        if isinstance(response, bytes):
                            log.debug("Received binary message, skipping")
                            continue
                            
                        try:
                            data = json.loads(response)
                        except json.JSONDecodeError as e:
                            log.warning(f"Invalid JSON from WebSocket: {e}, raw: {response[:100]}")
                            continue
                        
                        msg_type = data.get('type', '')
                        text = data.get('text', '')
                        confidence = data.get('confidence', 0.0)
                        is_final = data.get('is_final', False)
                        
                        log.debug(f"Received WebSocket message: {msg_type} - {text[:50]}...")
                        
                        # Forward transcript to extension
                        if msg_type in ['final']:
                            transcript_message = {
                                "action": "transcript",
                                "type": msg_type,
                                "text": text,
                                "confidence": confidence,
                                "is_final": is_final
                            }
                            await self.send_to_extension(transcript_message)
                            log.info(f"Forwarded transcript: {msg_type} - {text}")

                            # Add transcript to auto-summary service only if it's a final transcript
                            if self.auto_summary and is_final:
                                self.auto_summary.add_transcript(text)
                        
                        elif msg_type == 'status':
                            status_text = data.get('message', text)
                            log.info(f"WebSocket status: {status_text}")
                            
                            # Handle specific status messages
                            if 'ready' in status_text.lower() or 'connected' in status_text.lower():
                                self.stt_ready = True
                                log.info("STT server is ready for audio")
                        
                        elif msg_type == 'error':
                            error_msg = data.get('error', data.get('message', 'Unknown error'))
                            log.error(f"WebSocket error: {error_msg}")
                            await self.send_to_extension({
                                "status": "error", 
                                "message": f"STT Error: {error_msg}"
                            })
                            
                        elif msg_type == 'config_ack':
                            log.info("Received config acknowledgment from server")
                            
                        else:
                            log.debug(f"Unknown message type: {msg_type}, data: {data}")
                            
                    except asyncio.TimeoutError:
                        continue
                    except websockets.exceptions.ConnectionClosed as e:
                        log.warning(f"WebSocket connection closed in receiver: {e}")
                        self.stt_connected = False
                        self.stt_ready = False
                        # Don't break here - let audio_sender handle reconnection
                        # This task will be restarted by audio_sender after reconnect
                        await asyncio.sleep(1.0)
                        continue
                else:
                    await asyncio.sleep(0.1)
                    
            except asyncio.CancelledError:
                log.info("WebSocket receiver task cancelled")
                break
            except Exception as e:
                log.error(f"Error in websocket_receiver: {e}")
                await asyncio.sleep(0.1)
                
        log.info("WebSocket receiver task finished.")

    async def start_capture(self, config: Dict[str, Any]):
        log.info(f"Starting capture with config: {config}")
        if self.is_recording:
            await self.send_to_extension({"status": "error", "message": "Recording is already in progress."})
            return

        self.config.update(config)
        self.auto_summary.update_language(config.get('language', 'vi'))

        if not await self.connect_stt_websocket(self.config['serverUrl']):
            await self.send_to_extension({"status": "error", "message": "Failed to connect to STT WebSocket."})
            return

        self.is_recording = True
        self.shutdown_event.clear()

        try:
            
            # Get device IDs from config or use defaults
            if config.get('micDevice') and config['micDevice'] != 'default':
                mic_device_id = int(config['micDevice'])
            else:
                log.error(f"Not found mic device from config")
                
                
            if config.get('systemDevice') and config['systemDevice'] != 'blackhole':
                system_device_id = int(config['systemDevice'])
            else:
                log.error(f"Not found system device from config")
                
            if system_device_id is None:
                raise RuntimeError(f"Could not find system audio device")
                
            # Get device names for logging
            mic_device = self._find_device_by_index(mic_device_id)
            if mic_device is None:
                raise RuntimeError(f"Could not find mic device")
            
            mic_name = mic_device['name']
            mic_channels = mic_device['channels']
            mic_sample_rate = int(mic_device['sampleRate'])

            system_device = self._find_device_by_index(system_device_id)
            if system_device is None:
                raise RuntimeError(f"Could not find system device")
            
            system_name = system_device['name']
            system_channels = system_device['channels']
            system_sample_rate = int(system_device['sampleRate'])

            self.config['inputSampleRate'] = mic_sample_rate
            self.config['systemSampleRate'] = system_sample_rate

            log.info(f"Using Mic: {mic_name} (ID: {mic_device_id}) {mic_channels}ch, {mic_sample_rate}Hz, System: {system_name} (ID: {system_device_id}) {system_channels}ch, {system_sample_rate}Hz")

            # Start recording threads
            self.mic_thread = threading.Thread(
                target=self._recording_loop, 
                args=(mic_device_id, mic_sample_rate, mic_channels, self.mic_buffer, 'Microphone')
            )
            self.system_thread = threading.Thread(
                target=self._recording_loop, 
                args=(system_device_id, system_sample_rate, system_channels, self.system_buffer, 'SystemAudio')
            )
            self.mic_thread.daemon = True
            self.system_thread.daemon = True

            self.mic_thread.start()
            time.sleep(0.5)
            self.system_thread.start()
            log.info("Recording threads started.")

            # Setup STT connection
            if not await self.setup_stt_connection():
                raise RuntimeError("Failed to setup STT connection")
            
            # Start async tasks
            self.audio_task = asyncio.create_task(self.audio_sender())
            self.receiver_task = asyncio.create_task(self.websocket_receiver())
            self.heartbeat_task = asyncio.create_task(self._heartbeat_task())

            if self.auto_summary:
                self.auto_summary.start()

            status = self.get_status()
            await self.send_to_extension(status)

        except Exception as e:
            log.error(f"Failed to start capture: {e}")
            self.is_recording = False
            self.shutdown_event.set()
            await self.disconnect_stt_websocket()
            
            await self.send_to_extension({"status": "error", "message": f"Failed to start capture: {str(e)}"})

    async def stop_capture(self):
        log.info("Stopping capture.")
        if not self.is_recording:
            await self.send_to_extension({"status": "error", "message": "Recording is not in progress."})
            return

        self.is_recording = False
        self.shutdown_event.set()

        # Cancel async tasks
        tasks = [self.audio_task, self.receiver_task, self.heartbeat_task]
        for task in tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    log.error(f"Error stopping task: {e}")

        # Wait for threads to finish
        if self.mic_thread and self.mic_thread.is_alive():
            self.mic_thread.join(timeout=2.0)
        if self.system_thread and self.system_thread.is_alive():
            self.system_thread.join(timeout=2.0)
        
        log.info("Recording threads stopped.")

        if self.auto_summary:
            self.auto_summary.stop()

        await self.disconnect_stt_websocket()
        self.mic_buffer.clear()
        self.system_buffer.clear()

        log.info("All threads stopped.")
        status = self.get_status()
        await self.send_to_extension(status)

    def get_status(self):
        """Get current status - synchronous method"""
        status = "recording" if self.is_recording else "ready"
        message = "Đang ghi âm..." if self.is_recording else "Sẵn sàng"
        rs = {
            "action": "get_status",
            "status": status,
            "message": message,
            "is_recording": self.is_recording, 
            "stt_connected": self.stt_connected,
            "stt_ready": self.stt_ready,
            "config": self.config
        }
        log.info(f"get_status: Status: {status}")
        return rs

    async def handle_extension_message(self, websocket, message_str):
        """Handle messages from extension"""
        try:
            try:
                message = json.loads(message_str)
            except json.JSONDecodeError:
                log.error("Invalid JSON received from extension")
                await websocket.send(json.dumps({"status": "error", "message": "Invalid JSON message"}))
                return
                
            action = message.get('action')
            data = message.get('data', {})
            
            log.info(f"Received from extension: {action}")
            
            if action == 'start_capture' or action == 'start':
                await self.start_capture(data)
            elif action == 'stop_capture' or action == 'stop':
                await self.stop_capture()
            elif action == 'get_status':
                status = self.get_status()
                await websocket.send(json.dumps(status))
            elif action == 'get_devices':
                devices = self.get_audio_devices()
                await websocket.send(json.dumps(devices))
            elif action == 'ping':
                await websocket.send(json.dumps({
                    "status": "success", 
                    "action": "pong", 
                    "timestamp": time.time()
                }))
            else:
                log.warning(f"Unknown action: {action}")
                await websocket.send(json.dumps({
                    "status": "error", 
                    "message": f"Unknown action: {action}"
                }))
                
        except Exception as e:
            log.error(f"Error handling extension message: {e}")
            try:
                await websocket.send(json.dumps({
                    "status": "error", 
                    "message": f"Server error: {str(e)}"
                }))
            except:
                log.error("Could not send error response to client")

    async def handle_extension_client(self, websocket, path: str = "/"):
        """Handle WebSocket connection from Chrome extension"""
        log.info(f"Extension client connected from {websocket.remote_address}")
        self.extension_clients.add(websocket)
        try:
            # Send initial status to client upon connection
            status = self.get_status()
            await websocket.send(json.dumps(status))
            
            # Handle incoming messages
            async for message in websocket:
                await self.handle_extension_message(websocket, message)
                
        except websockets.exceptions.ConnectionClosed:
            log.info("Extension client disconnected")
        except Exception as e:
            log.error(f"Error in extension client handler: {e}")
        finally:
            if websocket in self.extension_clients:
                self.extension_clients.remove(websocket)
            log.info("Extension client removed from active connections")

    async def start_websocket_server(self):
        """Start WebSocket server for extension communication"""
        try:
            self.local_server = await websockets.serve(
                self.handle_extension_client,
                "0.0.0.0",
                self.local_port,
                ping_interval=20,
                ping_timeout=10
            )
            log.info(f"WebSocket server started on ws://0.0.0.0:{self.local_port}")
            return True
        except Exception as e:
            log.error(f"Failed to start WebSocket server: {e}")
            return False

    async def shutdown(self):
        """Clean shutdown method"""
        log.info("Shutting down application...")
        
        if self.is_recording:
            await self.stop_capture()
            
        if self.local_server:
            self.local_server.close()
            await self.local_server.wait_closed()
            
        # Close all extension connections
        for client in list(self.extension_clients):
            try:
                await client.close()
            except:
                pass
        self.extension_clients.clear()
        
        log.info("Application shutdown complete")

    async def run(self):
        """Main run method - start WebSocket server and keep running"""
        log.info("Native app starting...")
        
        if not await self.start_websocket_server():
            log.error("Failed to start WebSocket server, exiting")
            return

       
        loop = asyncio.get_running_loop()
        self.devices = await loop.run_in_executor(None, self.init_audio_devices)

        log.info("Native app ready. Waiting for extension connections...")
        
        try:
            # Keep the server running
            await self.local_server.wait_closed()
        except KeyboardInterrupt:
            log.info("Received keyboard interrupt")
        except Exception as e:
            log.error(f"Error in main loop: {e}")
        finally:
            await self.shutdown()

# Global app instance for signal handling
app = None

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    log.info(f"Received signal {signum}, shutting down gracefully.")
    if app:
        # Create a new event loop if needed for cleanup
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        if not loop.is_running():
            loop.run_until_complete(app.shutdown())
        else:
            # If loop is running, schedule shutdown
            asyncio.create_task(app.shutdown())
    
    sys.exit(0)

async def main():
    global app, log
    # Setup logging first
    config = setup_flexible_logging()
    log = logging.getLogger(__name__)
    log.info("Application starting...")

    # PyInstaller fixes
    if getattr(sys, 'frozen', False):
        '''
        import multiprocessing

        multiprocessing.freeze_support()
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        '''

    app = AudioCaptureNative(config)
    
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    await app.run()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Application exiting.")
    except Exception as e:
        log.error(f"Unexpected error: {e}")
        sys.exit(1)