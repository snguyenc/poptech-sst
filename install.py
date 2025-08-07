#!/usr/bin/env python3
"""
Installation script cho Audio Capture Extension + Native App
"""

import os
import sys
import json
import shutil
import subprocess
import platform
from pathlib import Path

def get_chrome_native_messaging_dir():
    """Lấy thư mục Native Messaging của Chrome theo OS"""
    system = platform.system()
    home = Path.home()
    
    if system == "Linux":
        return home / ".config/google-chrome/NativeMessagingHosts"
    elif system == "Darwin":  # macOS
        return home / "Library/Application Support/Google/Chrome/NativeMessagingHosts"
    elif system == "Windows":
        return Path(os.environ.get("APPDATA", "")) / "Google/Chrome/User Data/NativeMessagingHosts"
    else:
        raise Exception(f"Unsupported OS: {system}")

def install_native_app():
    """Cài đặt Native App"""
    print("🔧 Installing Native App...")
    
    # Get current directory
    current_dir = Path(__file__).parent.absolute()
    native_app_dir = current_dir / "native_app"
    
    # Install Python dependencies
    print("📦 Installing Python dependencies...")
    subprocess.run([
        sys.executable, "-m", "pip", "install", "-r", 
        str(native_app_dir / "requirements.txt")
    ], check=True)
    
    # Make Python script executable
    python_script = native_app_dir / "audio_capture_native.py"
    os.chmod(python_script, 0o755)
    
    # Update Native Messaging host configuration
    host_config_file = native_app_dir / "com.audiocapture.stt.json"
    with open(host_config_file, 'r') as f:
        config = json.load(f)
    
    # Update path to absolute path
    config["path"] = str(python_script)
    
    with open(host_config_file, 'w') as f:
        json.dump(config, f, indent=2)
    
    # Copy to Chrome Native Messaging directory
    chrome_dir = get_chrome_native_messaging_dir()
    chrome_dir.mkdir(parents=True, exist_ok=True)
    
    dest_config = chrome_dir / "com.audiocapture.stt.json"
    shutil.copy2(host_config_file, dest_config)
    
    print(f"✅ Native Messaging host installed to: {dest_config}")
    return str(python_script)

def generate_extension_instructions():
    """Tạo hướng dẫn cài đặt extension"""
    instructions = """
🚀 HƯỚNG DẪN CÀI ĐẶT AUDIO CAPTURE EXTENSION

1. MỞ CHROME EXTENSIONS:
   - Mở Chrome
   - Vào chrome://extensions/
   - Bật "Developer mode" (góc trên bên phải)

2. LOAD EXTENSION:
   - Click "Load unpacked"
   - Chọn thư mục: {extension_dir}
   - Extension sẽ xuất hiện trong danh sách

3. LẤY EXTENSION ID:
   - Copy Extension ID từ trang chrome://extensions/
   - Ví dụ: abcdefghijklmnopqrstuvwxyz123456

4. CẬP NHẬT NATIVE MESSAGING CONFIG:
   - Mở file: {config_file}
   - Thay "EXTENSION_ID_HERE" bằng Extension ID thực tế
   - Lưu file

5. RESTART CHROME:
   - Đóng hoàn toàn Chrome
   - Mở lại Chrome

6. TEST EXTENSION:
   - Click vào icon extension trên toolbar
   - Click "Bắt đầu" để test
   - Kiểm tra log: {log_file}

🎯 LƯU Ý:
- Đảm bảo WebSocket STT server đang chạy (ws://localhost:8765)
- Cần quyền microphone và system audio
- Trên Linux: cần cài pulseaudio-utils hoặc pipewire
"""
    
    current_dir = Path(__file__).parent.absolute()
    extension_dir = current_dir / "extension"
    config_file = current_dir / "native_app" / "com.audiocapture.stt.json"
    log_file = current_dir / "native_app" / "audio_capture_native.log"
    
    return instructions.format(
        extension_dir=extension_dir,
        config_file=config_file,
        log_file=log_file
    )

def check_system_requirements():
    """Kiểm tra system requirements"""
    print("🔍 Checking system requirements...")
    
    # Check Python version
    if sys.version_info < (3, 7):
        raise Exception("Python 3.7+ required")
    print(f"✅ Python {sys.version}")
    
    # Check audio system
    system = platform.system()
    if system == "Linux":
        # Check for PulseAudio or PipeWire
        try:
            subprocess.run(["pulseaudio", "--version"], 
                         capture_output=True, check=True)
            print("✅ PulseAudio detected")
        except:
            try:
                subprocess.run(["pipewire", "--version"], 
                             capture_output=True, check=True)
                print("✅ PipeWire detected")
            except:
                print("⚠️  Warning: No PulseAudio/PipeWire detected")
    
    print("✅ System requirements check completed")

def main():
    """Main installation function"""
    try:
        print("🎤 Audio Capture Extension Installer")
        print("=" * 50)
        
        # Check requirements
        check_system_requirements()
        
        # Install native app
        python_script = install_native_app()
        
        # Generate instructions
        instructions = generate_extension_instructions()
        
        # Save instructions to file
        with open("INSTALLATION_INSTRUCTIONS.txt", "w") as f:
            f.write(instructions)
        
        print("\n" + "=" * 50)
        print("🎉 INSTALLATION COMPLETED!")
        print("=" * 50)
        print(f"📝 Xem hướng dẫn chi tiết: INSTALLATION_INSTRUCTIONS.txt")
        print(f"🐍 Native app: {python_script}")
        print(f"📁 Extension: {Path(__file__).parent / 'extension'}")
        
        print("\n🚀 NEXT STEPS:")
        print("1. Load extension vào Chrome")
        print("2. Cập nhật Extension ID trong native messaging config")
        print("3. Restart Chrome")
        print("4. Test extension!")
        
    except Exception as e:
        print(f"❌ Installation failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
