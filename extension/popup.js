// =================================================================
//
//               popup.js (Manifest V3 - Final Version)
//
// =================================================================

document.addEventListener('DOMContentLoaded', () => {
    // --- 1. DOM Element References ---
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const statusDiv = document.getElementById('status');
    const micDeviceSelect = document.getElementById('micDevice');
    const systemDeviceSelect = document.getElementById('systemDevice');
    const transcriptArea = document.getElementById('transcriptArea');
    const clearTranscriptBtn = document.getElementById('clearTranscriptBtn');
    const copyTranscriptBtn = document.getElementById('copyTranscriptBtn');
    const summaryArea = document.getElementById('summaryArea'); // <-- THÊM DÒNG NÀY
    const copySummaryBtn = document.getElementById('copySummaryBtn'); // <-- THÊM DÒNG NÀY
    const languageSelect = document.getElementById('languageSelect'); // <-- THÊM DÒNG NÀY
    // --- 2. UI Update Functions ---
    const updateRecordingUI = (isRecording, message = null) => {
        if (isRecording) {
            statusDiv.className = 'status recording';
            statusDiv.textContent = message || 'Trạng thái: Đang ghi âm...';
            startBtn.disabled = true;
            stopBtn.disabled = false;
        } else {
            statusDiv.className = 'status ready';
            statusDiv.textContent = message || 'Trạng thái: Sẵn sàng';
            startBtn.disabled = false;
            stopBtn.disabled = true;
        }
    };

    const populateDeviceDropdowns = (devices, savedSettings) => {
        micDeviceSelect.innerHTML = '';
        systemDeviceSelect.innerHTML = '';
        if (!devices || devices.length === 0) {
            micDeviceSelect.innerHTML = '<option value="">Không tìm thấy thiết bị</option>';
            systemDeviceSelect.innerHTML = '<option value="">Không tìm thấy thiết bị</option>';
            return;
        }
        devices.forEach(device => {
            const option = document.createElement('option');
            option.value = device.id;
            option.textContent = `${device.name} -(${device.id}id-${device.channels}ch-${device.sampleRate}Hz)`;
            if (device.type === 'input') micDeviceSelect.appendChild(option);
            else if (device.type === 'loopback') systemDeviceSelect.appendChild(option.cloneNode(true));
        });

        if (savedSettings.micDevice && micDeviceSelect.querySelector(`option[value="${savedSettings.micDevice}"]`)) {
            micDeviceSelect.value = savedSettings.micDevice;
        }
        if (savedSettings.systemDevice && systemDeviceSelect.querySelector(`option[value="${savedSettings.systemDevice}"]`)) {
            systemDeviceSelect.value = savedSettings.systemDevice;
        }
    };
    
    const updateTranscriptUI = (transcriptText) => {
        transcriptArea.value = transcriptText || '';
        transcriptArea.scrollTop = transcriptArea.scrollHeight;
    };

    const updateSummaryUI = (summaryText) => {
        summaryArea.value = summaryText || '';
        summaryArea.scrollTop = summaryArea.scrollHeight;
    };

    const showNotification = (message, statusType = 'warning') => {
        statusDiv.className = `status ${statusType}`;
        statusDiv.textContent = message;
    };

    // --- 3. Communication Port Setup and Listeners ---
    const port = chrome.runtime.connect({ name: "popup-connection" });

    port.onMessage.addListener((message) => {
        console.log("Received message via port:",message.type, message);

        if (message.type === 'INITIAL_STATE') {
            updateRecordingUI(message.isRecording);
            updateTranscriptUI(message.transcript);
            updateSummaryUI(message.summary);
            console.log("Received message via port:", message);
        } else if (message.type === 'NATIVE_APP_MESSAGE') {
            const { action, status, devices, message: msgText, is_recording, type: transcriptType } = message.data;

            if (action === 'get_devices') {
                chrome.storage.sync.get(['micDevice', 'systemDevice'], (savedSettings) => {
                    populateDeviceDropdowns(devices, savedSettings);
                });
            } else if (action === 'get_status') {
                updateRecordingUI(is_recording, msgText);
            } else if (action === 'transcript') {
                if (transcriptType === 'clear') {
                    updateTranscriptUI('');
                    updateSummaryUI('');
                } else {
                    chrome.storage.local.get([STORAGE_KEYS.transcript], (result) => {
                         updateTranscriptUI(result[STORAGE_KEYS.transcript]);
                    });
                }
            } else if (action === 'summary_update') {
                chrome.storage.local.get([STORAGE_KEYS.summary], (result) => {
                    updateSummaryUI(result[STORAGE_KEYS.summary]);
                });
            }
            if (status === 'error' || status === 'warning') {
                showNotification(msgText, status);
            }
        }
    });
    
    port.onDisconnect.addListener(() => {
        showNotification("Mất kết nối với background. Vui lòng mở lại popup.", "error");
        startBtn.disabled = true;
        stopBtn.disabled = true;
    });

    // --- 4. Event Handlers ---
    const saveSettings = () => {
        chrome.storage.sync.set({
            micDevice: micDeviceSelect.value,
            systemDevice: systemDeviceSelect.value,
            language: languageSelect.value
        });
    };
    
    startBtn.addEventListener('click', () => {
        saveSettings();
        const config = {
            micDevice: micDeviceSelect.value,
            systemDevice: systemDeviceSelect.value,
            language: languageSelect.value
        };
        port.postMessage({ type: 'START_CAPTURE', config: config });
        // Hiển thị trạng thái "đang chờ" để người dùng biết
        showNotification('Đang khởi động...', 'recording');
        startBtn.disabled = true;
        stopBtn.disabled = true;
    });

    stopBtn.addEventListener('click', () => {
        port.postMessage({ type: 'STOP_CAPTURE' });
        // Hiển thị trạng thái "đang chờ"
        showNotification('Đang dừng...', 'ready');
        startBtn.disabled = true;
        stopBtn.disabled = true;
    });
    
    clearTranscriptBtn.addEventListener('click', () => {
        port.postMessage({ type: 'CLEAR_TRANSCRIPT' });
        updateTranscriptUI('');
        updateSummaryUI('');
    });

    copyTranscriptBtn.addEventListener('click', async () => {
        await navigator.clipboard.writeText(transcriptArea.value);
        copyTranscriptBtn.textContent = 'Đã chép!';
        setTimeout(() => { copyTranscriptBtn.textContent = 'Copy'; }, 1500);
    });

    copySummaryBtn.addEventListener('click', async () => {
        await navigator.clipboard.writeText(summaryArea.value);
        copySummaryBtn.textContent = 'Đã chép!';
        setTimeout(() => { copySummaryBtn.textContent = 'Copy'; }, 1500);
    });

    micDeviceSelect.addEventListener('change', saveSettings);
    systemDeviceSelect.addEventListener('change', saveSettings);
    languageSelect.addEventListener('change', saveSettings);
    
    // --- 5. Initialization ---
    const initializePopup = () => {
        // Load cài đặt đã lưu
        chrome.storage.sync.get(['micDevice', 'systemDevice', 'language'], (savedSettings) => {
            if (savedSettings.language) languageSelect.value = savedSettings.language;
            if (savedSettings.micDevice) micDeviceSelect.value = savedSettings.micDevice;
            if (savedSettings.systemDevice) systemDeviceSelect.value = savedSettings.systemDevice;
        });
        // Background sẽ gửi trạng thái và danh sách thiết bị khi port kết nối.
    };

    initializePopup();
});

// Định nghĩa STORAGE_KEYS ở đây để popup có thể truy cập
const STORAGE_KEYS = {
    transcript: 'local_transcript',
    summary: 'local_summary' // <-- THÊM DÒNG NÀY
};