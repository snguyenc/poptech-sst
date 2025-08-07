// =================================================================
//
//             background.js (Manifest V3 - Final Version)
//
// =================================================================

// --- 1. Constants and State Management ---

const NATIVE_WS_URL = 'ws://localhost:8766';
const ALARM_HEALTH_CHECK = 'websocket-health-check';

const STORAGE_KEYS = {
    isRecording: 'session_isRecording',
    config: 'session_config',
    transcript: 'local_transcript',
    summary: 'local_summary'
};

const ICONS = {
    idle: 'mic-idle',
    recording: 'mic-recording',
    error: 'mic-error'
};

let nativeWebSocket = null;
let popupPort = null;
let connectionPromise = null;

// --- 2. UI and Badge Management ---

function updateIcon(state) {
    if (!ICONS[state]) return;
    chrome.action.setIcon({
        path: {
            16: `icons/${ICONS[state]}-16.png`,
            32: `icons/${ICONS[state]}-32.png`,
            48: `icons/${ICONS[state]}-48.png`,
            128: `icons/${ICONS[state]}-128.png`
        }
    });
}

function updateBadge(isRecording) {
    if (isRecording) {
        chrome.action.setBadgeText({ text: 'REC' });
        chrome.action.setBadgeBackgroundColor({ color: '#FF0000' });
        chrome.action.setTitle({ title: 'Audio Capture - Đang ghi âm' });
        updateIcon('recording');
    } else {
        chrome.action.setBadgeText({ text: '' });
        chrome.action.setTitle({ title: 'Audio Capture' });
        if (!nativeWebSocket || nativeWebSocket.readyState === WebSocket.OPEN) {
            updateIcon('idle');
        } else {
            updateIcon('error');
        }
    }
}


// --- 3. WebSocket Connection Management ---

async function getWebSocket() {
    // Nếu đã có kết nối và đang mở, trả về ngay
    if (nativeWebSocket && nativeWebSocket.readyState === WebSocket.OPEN) {
        return nativeWebSocket;
    }

    // Nếu đang có một nỗ lực kết nối khác, hãy chờ nó hoàn thành và trả về kết quả
    if (connectionPromise) {
        console.log('Waiting for an existing connection attempt...');
        return connectionPromise;
    }

    // Nếu không, bắt đầu một nỗ lực kết nối mới
    console.log('Attempting to connect to native app...');
    connectionPromise = new Promise((resolve, reject) => {
        const ws = new WebSocket(NATIVE_WS_URL);

        const cleanup = () => {
            // Dọn dẹp promise sau khi kết nối hoàn thành hoặc thất bại
            // để các lần gọi sau có thể tạo kết nối mới nếu cần.
            connectionPromise = null;
        };

        ws.onopen = () => {
            console.log('WebSocket connection established.');
            nativeWebSocket = ws;
            setupWebSocketListeners(ws);
            updateIcon('idle');
            cleanup();
            resolve(ws);
        };

        ws.onerror = (event) => {
            console.error('WebSocket connection error.', event);
            nativeWebSocket = null;
            updateIcon('error');
            cleanup();
            reject(new Error('WebSocket connection failed.'));
        };
    });

    return connectionPromise;
}

function setupWebSocketListeners(ws) {
    ws.onmessage = (event) => {
        try {
            const message = JSON.parse(event.data);
            console.log('Received from native app:', message);
            if (message.action === 'get_status') {
                const isNowRecording = message.is_recording === true;
                chrome.storage.session.set({ [STORAGE_KEYS.isRecording]: isNowRecording });
            } else if (message.status === 'error') {
                chrome.storage.session.set({ [STORAGE_KEYS.isRecording]: false });
            } else if (message.action === 'transcript' && message.type === 'final') {
                updateTranscriptStorage(message.text);
            } else if (message.action === 'summary_update') {
                const timestamp = new Date().toLocaleTimeString('vi-VN');
                const finalLine = `[${timestamp}] \n ${message.text}`;
                chrome.storage.local.set({ [STORAGE_KEYS.summary]: finalLine });
            }

            broadcastToUI({ type: 'NATIVE_APP_MESSAGE', data: message });
        } catch (e) {
            console.error('Error parsing native message:', e);
        }
    };

    ws.onclose = () => {
        console.log('WebSocket connection closed.');
        nativeWebSocket = null;
        updateIcon('error');
        chrome.storage.session.set({ [STORAGE_KEYS.isRecording]: false });
    };
}

async function sendMessageToNative(message) {
    try {
        const ws = await getWebSocket();
        ws.send(JSON.stringify(message));
    } catch (e) {
        console.error(`Failed to send message to native app: ${e.message}`, message);
        broadcastToUI({
            type: 'NATIVE_APP_MESSAGE',
            data: {
                status: 'error',
                message: 'Không thể kết nối với ứng dụng native.'
            }
        });
        chrome.storage.session.set({ [STORAGE_KEYS.isRecording]: false });
    }
}

async function updateTranscriptStorage(newText) {
    const { [STORAGE_KEYS.transcript]: currentTranscript } = await chrome.storage.local.get(STORAGE_KEYS.transcript);
    const timestamp = new Date().toLocaleTimeString('vi-VN');
    const finalLine = `[${timestamp}] ${newText}`;
    const updatedTranscript = (currentTranscript ? currentTranscript + '\n' : '') + finalLine;
    chrome.storage.local.set({ [STORAGE_KEYS.transcript]: updatedTranscript });
}


// --- 4. Port-based Communication with Popup ---

function broadcastToUI(message) {
    if (popupPort) {
        try {
            popupPort.postMessage(message);
        } catch (e) {
            console.warn("Could not send message via port, it might be closing.");
        }
    }
}

chrome.runtime.onConnect.addListener((port) => {
    if (port.name === 'popup-connection') {
        popupPort = port;
        console.log('Popup connected.');

        (async () => {
            const sessionState = await chrome.storage.session.get([STORAGE_KEYS.isRecording]);
            const localState = await chrome.storage.local.get([STORAGE_KEYS.transcript, STORAGE_KEYS.summary]);
            port.postMessage({
                type: 'INITIAL_STATE',
                isRecording: sessionState[STORAGE_KEYS.isRecording] || false,
                transcript: localState[STORAGE_KEYS.transcript] || '',
                summary: localState[STORAGE_KEYS.summary] || ''
            });
            sendMessageToNative({ action: 'get_status' });
            sendMessageToNative({ action: 'get_devices' });
        })();

        port.onDisconnect.addListener(() => {
            console.log('Popup disconnected.');
            popupPort = null;
        });

        port.onMessage.addListener(handleUICommand);
    }
});


// --- 5. Main Command Handler and Event Listeners ---

function handleUICommand(message) {
    switch (message.type) {
        case 'START_CAPTURE':
            // Chỉ gửi lệnh, không thay đổi state ở đây
            sendMessageToNative({ action: 'start_capture', data: message.config });
            break;

        case 'STOP_CAPTURE':
            // Chỉ gửi lệnh, không thay đổi state ở đây
            sendMessageToNative({ action: 'stop_capture' });
            break;

        case 'CLEAR_TRANSCRIPT':
            chrome.storage.local.set({ [STORAGE_KEYS.transcript]: '', [STORAGE_KEYS.summary]: '' });
            broadcastToUI({ type: 'NATIVE_APP_MESSAGE', data: { action: 'transcript', type: 'clear' }});
            break;

        case 'GET_DEVICES':
            sendMessageToNative({ action: 'get_devices' });
            break;
            
        case 'GET_STATUS':
            sendMessageToNative({ action: 'get_status' });
            break;
    }
}

chrome.storage.onChanged.addListener((changes, namespace) => {
    if (namespace === 'session' && changes[STORAGE_KEYS.isRecording]) {
        const isNowRecording = !!changes[STORAGE_KEYS.isRecording].newValue;
        updateBadge(isNowRecording);
        if (isNowRecording) {
            chrome.alarms.create(ALARM_HEALTH_CHECK, { delayInMinutes: 0.3, periodInMinutes: 0.3 });
        } else {
            chrome.alarms.clear(ALARM_HEALTH_CHECK);
        }
    }
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
    if (alarm.name === ALARM_HEALTH_CHECK) {
        console.log('Health check alarm: Pinging native app...');
        sendMessageToNative({ action: 'ping' });
    }
});


// --- 6. Extension Lifecycle Hooks ---

chrome.runtime.onStartup.addListener(() => {
    console.log('Browser started. Resetting state.');
    chrome.storage.session.set({ [STORAGE_KEYS.isRecording]: false, [STORAGE_KEYS.config]: {} });
    chrome.storage.local.set({ [STORAGE_KEYS.transcript]: '', [STORAGE_KEYS.summary]: '' });
    updateBadge(false);
});

chrome.runtime.onInstalled.addListener((details) => {
    console.log(`Extension ${details.reason}. Resetting state.`);
    chrome.storage.session.set({ [STORAGE_KEYS.isRecording]: false, [STORAGE_KEYS.config]: {} });
    chrome.storage.local.set({ [STORAGE_KEYS.transcript]: '', [STORAGE_KEYS.summary]: '' });
    updateBadge(false);
});

console.log('Audio Capture background script loaded.');