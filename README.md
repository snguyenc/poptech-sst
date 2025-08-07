# Audio Capture Extension + Native App

Extension Chrome để capture cả **mic audio** và **system audio** và gửi lên WebSocket STT server để xử lý speech-to-text real-time.

## Kiến trúc

```
┌─────────────────┐    Native Messaging    ┌──────────────────┐
│  Web Extension  │ ────────────────────────► │  Python Native   │
│   (Trigger UI)  │    start/stop commands   │      App         │
└─────────────────┘                         └──────────────────┘
                                                     │
                                                     │ Capture ALL:
                                                     │ • Mic Audio
                                                     │ • System Audio
                                                     │ • Merge to PCM16
                                                     ▼
                                            ┌──────────────────┐
                                            │  WebSocket STT   │
                                            │     Server       │
                                            └──────────────────┘
```

## Thành phần

### 1. Web Extension (`extension/`)
- **manifest.json**: Manifest V3 với quyền Native Messaging
- **popup.html/js**: UI đơn giản để start/stop capture
- **background.js**: Xử lý giao tiếp với native app

### 2. Native Python App (`native_app/`)
- **audio_capture_native.py**: Main app capture audio và gửi lên WebSocket
- **requirements.txt**: Python dependencies
- **com.audiocapture.stt.json**: Native Messaging host configuration

## Cài đặt
conda create -n captureaudio python=3.10
conda activate captureaudio
### Bước 1: Cài đặt Native App
```bash
cd audio_capture_extension
python install.py
```

### Bước 2: Load Extension vào Chrome
1. Mở Chrome → `chrome://extensions/`
2. Bật "Developer mode"
3. Click "Load unpacked" → chọn thư mục `extension/`
4. Copy Extension ID

### Bước 3: Cập nhật Native Messaging Config
1. Mở `native_app/com.audiocapture.stt.json`
2. Thay `EXTENSION_ID_HERE` bằng Extension ID thực tế
3. Restart Chrome

## Sử dụng

1. **Khởi động STT Server**: Đảm bảo WebSocket STT server đang chạy tại `ws://localhost:8765`
2. **Mở Extension**: Click icon extension trên Chrome toolbar
3. **Cấu hình**: Kiểm tra URL server, sample rate, channels
4. **Bắt đầu**: Click "Bắt đầu" để capture audio
5. **Dừng**: Click "Dừng" để dừng capture

## Tính năng

### ✅ Đã implement:
- Web Extension UI với start/stop controls
- Native Messaging communication
- Python app capture audio (mic + system)
- WebSocket integration với STT server
- Cross-platform support (Linux/macOS/Windows)
- Configurable audio settings

### 🔄 Có thể mở rộng:
- Real-time audio level indicator
- Multiple audio device selection
- Audio quality settings
- Recording session management
- Transcript display trong extension

## System Requirements

- **Python**: 3.7+
- **Chrome**: Version 88+ (Manifest V3 support)
- **Audio System**: 
  - Linux: PulseAudio hoặc PipeWire
  - macOS: Core Audio
  - Windows: WASAPI

## Dependencies

```
sounddevice>=0.4.6    # Audio capture
numpy>=1.21.0         # Audio processing
websockets>=11.0.0    # WebSocket client
```

## Troubleshooting

### Extension không kết nối được với Native App:
1. Kiểm tra Extension ID trong `com.audiocapture.stt.json`
2. Restart Chrome sau khi cập nhật config
3. Kiểm tra log: `native_app/audio_capture_native.log`

### Không capture được system audio:
1. **Linux**: Cần enable loopback device trong PulseAudio
2. **Windows**: Enable "Stereo Mix" trong Sound settings
3. **macOS**: Cần app như BlackHole để tạo virtual audio device

### WebSocket connection failed:
1. Đảm bảo STT server đang chạy
2. Kiểm tra URL trong extension settings
3. Kiểm tra firewall/network settings

## Development

### Test Native App standalone:
```bash
cd native_app
echo '{"action":"get_status"}' | python audio_capture_native.py
```

### Debug Extension:
1. Mở `chrome://extensions/`
2. Click "Inspect views: background page"
3. Xem Console logs

## License

MIT License - Tự do sử dụng và modify cho dự án của bạn.
