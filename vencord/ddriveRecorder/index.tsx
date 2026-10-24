/*
 * DDrive Call & Screenshare Recorder for Vencord
 * Live-streams call audio and screenshare video straight to DDrive in memory
 * Zero disk usage, no background pauses when minimized
 */

import { definePluginSettings } from "@api/Settings";
import ErrorBoundary from "@components/ErrorBoundary";
import definePlugin, { OptionType, PluginNative } from "@utils/types";
import {
    ApplicationStreamingStore,
    ChannelStore,
    MediaEngineStore,
    React,
    RTCConnectionStore,
    showToast,
    useEffect,
    useState,
    WindowStore
} from "@webpack/common";

import style from "./style.css?managed";

const Native = (VencordNative as any)?.pluginHelpers?.DDriveRecorder as PluginNative<typeof import("./native")> | undefined;

export const settings = definePluginSettings({
    serverUrl: {
        type: OptionType.STRING,
        description: "DDrive Server URL (e.g. http://127.0.0.1:8080)",
        default: "http://127.0.0.1:8080"
    },
    recorderKey: {
        type: OptionType.STRING,
        description: "DDrive Recorder Key (from DDrive Settings > Live upload)",
        default: ""
    },
    folderName: {
        type: OptionType.STRING,
        description: "Folder name under /Recordings",
        default: "Discord Calls"
    },
    autoRecord: {
        type: OptionType.BOOLEAN,
        description: "Automatically start recording when joining a voice call",
        default: false
    },
    recordScreenshare: {
        type: OptionType.BOOLEAN,
        description: "Record screenshare video when anyone is sharing their screen in the call",
        default: true
    },
    keepAliveWhenMinimized: {
        type: OptionType.BOOLEAN,
        description: "Prevent Discord from pausing screenshare or audio when window is minimized",
        default: true
    }
});

// ------------------------------------------------------------- Live Recording State
interface RecState {
    isRecording: boolean;
    duration: number;
    isVideo: boolean;
    uploadedBytes: number;
    channelName: string;
}

let recState: RecState = {
    isRecording: false,
    duration: 0,
    isVideo: false,
    uploadedBytes: 0,
    channelName: ""
};

const listeners = new Set<(state: RecState) => void>();

function updateState(partial: Partial<RecState>) {
    recState = { ...recState, ...partial };
    for (const listener of listeners) {
        try {
            listener(recState);
        } catch (_) {}
    }
}

let currentMediaRecorder: MediaRecorder | null = null;
let currentRecId: string | null = null;
let currentOffset = 0;
let currentRecordedVideo: HTMLVideoElement | null = null;
let currentAudioContext: AudioContext | null = null;
let durationInterval: NodeJS.Timeout | null = null;
let watchdogInterval: NodeJS.Timeout | null = null;

// ------------------------------------------------------------- Anti-Pause & Anti-Throttle Engine
const originalHiddenProp = Object.getOwnPropertyDescriptor(Document.prototype, "hidden");
const originalVisProp = Object.getOwnPropertyDescriptor(Document.prototype, "visibilityState");
let hooksInstalled = false;

function setupVisibilityHooks() {
    if (hooksInstalled) return;
    hooksInstalled = true;

    try {
        Object.defineProperty(document, "hidden", {
            get() {
                if (recState.isRecording && settings.store.keepAliveWhenMinimized) {
                    return false;
                }
                return originalHiddenProp ? originalHiddenProp.get?.call(document) : false;
            },
            configurable: true
        });

        Object.defineProperty(document, "visibilityState", {
            get() {
                if (recState.isRecording && settings.store.keepAliveWhenMinimized) {
                    return "visible";
                }
                return originalVisProp ? originalVisProp.get?.call(document) : "visible";
            },
            configurable: true
        });

        // Intercept visibilitychange event listeners so Discord doesn't receive "hidden"
        const originalAddEventListener = EventTarget.prototype.addEventListener;
        EventTarget.prototype.addEventListener = function(type, listener, options) {
            if (type === "visibilitychange" && typeof listener === "function") {
                const wrapped = function(this: any, event: any) {
                    if (recState.isRecording && settings.store.keepAliveWhenMinimized) {
                        return; // drop visibilitychange when recording is active
                    }
                    return (listener as Function).call(this, event);
                };
                return originalAddEventListener.call(this, type, wrapped, options);
            }
            return originalAddEventListener.call(this, type, listener, options);
        };
    } catch (e) {
        console.warn("[DDriveRecorder] Visibility hooks setup warning:", e);
    }

    // Intercept video pause calls while recording
    const originalMediaPause = HTMLMediaElement.prototype.pause;
    HTMLMediaElement.prototype.pause = function(this: HTMLMediaElement) {
        if (recState.isRecording && settings.store.keepAliveWhenMinimized && this === currentRecordedVideo) {
            // Keep screenshare video playing even if Discord tries to pause it when minimized
            return;
        }
        return originalMediaPause.call(this);
    };

    // Hook WindowStore.isFocused
    if (WindowStore && typeof WindowStore.isFocused === "function") {
        const origIsFocused = WindowStore.isFocused.bind(WindowStore);
        WindowStore.isFocused = () => {
            if (recState.isRecording && settings.store.keepAliveWhenMinimized) {
                return true;
            }
            return origIsFocused();
        };
    }
}

function patchMediaEngineConnections() {
    try {
        const mediaEngine = MediaEngineStore?.getMediaEngine?.();
        if (mediaEngine?.connections) {
            for (const conn of mediaEngine.connections) {
                if (!conn || (conn as any).__ddrivePatched) continue;
                (conn as any).__ddrivePatched = true;

                if (typeof conn.setHasActiveVideoOutputSink === "function") {
                    const origSetSink = conn.setHasActiveVideoOutputSink.bind(conn);
                    conn.setHasActiveVideoOutputSink = function(userId: string, active: boolean, reason: string) {
                        if (recState.isRecording && settings.store.keepAliveWhenMinimized && reason === "hidden") {
                            // Don't deactivate video sink when minimized
                            return origSetSink(userId, true, reason);
                        }
                        return origSetSink(userId, active, reason);
                    };
                }
            }
        }
    } catch (_) {}
}

function startWatchdog() {
    stopWatchdog();
    watchdogInterval = setInterval(() => {
        if (!recState.isRecording) return;

        // Ensure recorded video element has not paused
        if (currentRecordedVideo && currentRecordedVideo.paused) {
            currentRecordedVideo.play().catch(() => {});
        }

        // Ensure audio context remains running
        if (currentAudioContext && currentAudioContext.state === "suspended") {
            currentAudioContext.resume().catch(() => {});
        }

        patchMediaEngineConnections();
    }, 500);
}

function stopWatchdog() {
    if (watchdogInterval) {
        clearInterval(watchdogInterval);
        watchdogInterval = null;
    }
}

// ------------------------------------------------------------- Audio & Video Capture
function findActiveScreenshare(): { videoEl: HTMLVideoElement | null; videoTrack: MediaStreamTrack | null } {
    const videos = Array.from(document.querySelectorAll<HTMLVideoElement>("video"));
    for (const v of videos) {
        if (v.srcObject instanceof MediaStream) {
            const tracks = v.srcObject.getVideoTracks();
            if (tracks.length > 0 && tracks[0].readyState === "live") {
                return { videoEl: v, videoTrack: tracks[0] };
            }
        }
        if (v.videoWidth > 0 && v.videoHeight > 0) {
            try {
                const stream = (v as any).captureStream?.() || (v as any).mozCaptureStream?.();
                if (stream && stream.getVideoTracks().length > 0) {
                    return { videoEl: v, videoTrack: stream.getVideoTracks()[0] };
                }
            } catch (_) {}
        }
    }
    return { videoEl: null, videoTrack: null };
}

async function captureCallAudio(): Promise<{ stream: MediaStream; ctx: AudioContext }> {
    const audioCtx = new AudioContext();
    if (audioCtx.state === "suspended") {
        await audioCtx.resume();
    }
    const dest = audioCtx.createMediaStreamDestination();
    let hasConnected = false;

    // 1. Microphone capture (user's voice)
    try {
        const inputDeviceId = MediaEngineStore?.getInputDeviceId?.() || "default";
        const micStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                deviceId: inputDeviceId,
                echoCancellation: false,
                noiseSuppression: false,
                autoGainControl: false
            }
        });
        if (micStream.getAudioTracks().length > 0) {
            const micSource = audioCtx.createMediaStreamSource(micStream);
            micSource.connect(dest);
            hasConnected = true;
        }
    } catch (e) {
        console.warn("[DDriveRecorder] Microphone capture note:", e);
    }

    // 2. Desktop audio loopback (captures other call participants & shared audio)
    try {
        const sources = await Native?.getDesktopSources?.();
        if (sources && sources.length > 0) {
            const desktopStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    mandatory: {
                        chromeMediaSource: "desktop"
                    }
                } as any,
                video: {
                    mandatory: {
                        chromeMediaSource: "desktop",
                        chromeMediaSourceId: sources[0].id
                    }
                } as any
            });
            // Stop video track immediately: we only need the loopback audio track here
            desktopStream.getVideoTracks().forEach(t => t.stop());
            const audioTracks = desktopStream.getAudioTracks();
            if (audioTracks.length > 0) {
                const sysSource = audioCtx.createMediaStreamSource(new MediaStream(audioTracks));
                sysSource.connect(dest);
                hasConnected = true;
            }
        }
    } catch (e) {
        console.warn("[DDriveRecorder] Desktop loopback audio fallback:", e);
    }

    // 3. Fallback: connect DOM media elements (HTMLAudioElement / HTMLVideoElement)
    if (!hasConnected) {
        const mediaElements = Array.from(document.querySelectorAll<HTMLMediaElement>("audio, video"));
        for (const el of mediaElements) {
            try {
                if (el.srcObject instanceof MediaStream && el.srcObject.getAudioTracks().length > 0) {
                    const src = audioCtx.createMediaStreamSource(el.srcObject);
                    src.connect(dest);
                    hasConnected = true;
                }
            } catch (_) {}
        }
    }

    return { stream: dest.stream, ctx: audioCtx };
}

// ------------------------------------------------------------- Recording Lifecycle
export async function startRecording() {
    if (recState.isRecording) return;

    const { serverUrl, recorderKey, folderName, recordScreenshare, keepAliveWhenMinimized } = settings.store;

    if (!recorderKey) {
        showToast("DDrive Recorder Key is required. Set it in Settings > Plugins > DDrive Call Recorder.", {
            variant: "danger"
        });
        return;
    }

    if (!RTCConnectionStore?.isConnected?.()) {
        showToast("Connect to a voice call or channel before recording.", {
            variant: "danger"
        });
        return;
    }

    const channelId = RTCConnectionStore.getChannelId();
    const channel = ChannelStore?.getChannel?.(channelId);
    const channelTitle = channel?.name || "Call";

    try {
        showToast("Starting live call recording...", { variant: "info" });

        // 1. Enable Anti-Throttling & Watchdog
        if (keepAliveWhenMinimized) {
            await Native?.setRecordingActive?.(true);
            patchMediaEngineConnections();
            startWatchdog();
        }

        // 2. Audio Capture
        const { stream: audioStream, ctx: audioCtx } = await captureCallAudio();
        currentAudioContext = audioCtx;

        // 3. Screenshare Video Detection
        let videoTrack: MediaStreamTrack | null = null;
        if (recordScreenshare) {
            const screen = findActiveScreenshare();
            if (screen.videoTrack) {
                videoTrack = screen.videoTrack;
                currentRecordedVideo = screen.videoEl;
            }
        }

        const isVideo = !!videoTrack;
        const tracks: MediaStreamTrack[] = [...audioStream.getAudioTracks()];
        if (videoTrack) tracks.push(videoTrack);

        const combinedStream = new MediaStream(tracks);

        // 4. Select MIME type
        const mimeType = isVideo
            ? (MediaRecorder.isTypeSupported("video/webm;codecs=vp8,opus")
                ? "video/webm;codecs=vp8,opus"
                : "video/webm")
            : (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
                ? "audio/webm;codecs=opus"
                : "audio/webm");

        // 5. Generate clean file name
        const cleanName = channelTitle.replace(/[\x00-\x1f/\\:*?"<>|]+/g, " ").trim() || "Discord_Call";
        const now = new Date();
        const pad = (n: number) => String(n).padStart(2, "0");
        const dateTag = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}_${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}`;
        const fileName = `${cleanName}_${dateTag}.${isVideo ? "webm" : "webm"}`;

        // 6. Open stream on DDrive (/api/rec/open)
        const openRes = await Native?.openLiveRecording?.({
            serverUrl,
            recorderKey,
            folder: folderName || "Discord Calls",
            name: fileName,
            mime: mimeType
        });

        if (!openRes || !openRes.ok || !openRes.id) {
            throw new Error(openRes?.error || "Failed to initiate live recording stream on DDrive");
        }

        const recId = openRes.id;
        const recPath = openRes.path || fileName;
        currentRecId = recId;
        currentOffset = 0;

        // 7. Setup MediaRecorder
        const recorder = new MediaRecorder(combinedStream, {
            mimeType,
            videoBitsPerSecond: isVideo ? 2500000 : undefined,
            audioBitsPerSecond: 128000
        });

        currentMediaRecorder = recorder;

        recorder.ondataavailable = async (e: BlobEvent) => {
            if (e.data && e.data.size > 0 && currentRecId === recId) {
                const chunkOffset = currentOffset;
                const buffer = new Uint8Array(await e.data.arrayBuffer());
                currentOffset += buffer.byteLength;

                updateState({ uploadedBytes: currentOffset });

                const res = await Native?.sendLiveChunk?.({
                    serverUrl,
                    recorderKey,
                    id: recId,
                    offset: chunkOffset,
                    chunk: buffer
                });

                if (res && !res.ok) {
                    console.warn("[DDriveRecorder] Live upload status:", res.error);
                }
            }
        };

        recorder.onstop = async () => {
            try {
                const closeRes = await Native?.closeLiveRecording?.({
                    serverUrl,
                    recorderKey,
                    id: recId,
                    offset: currentOffset,
                    wait: 30
                });

                if (closeRes && closeRes.ok) {
                    showToast(`Call recorded to DDrive: ${closeRes.path || recPath}`, {
                        variant: "success"
                    });
                } else {
                    showToast(`Recording finished: ${closeRes?.error || "Uploaded"}`, {
                        variant: "info"
                    });
                }
            } catch (err: any) {
                console.error("[DDriveRecorder] Error closing recording:", err);
            } finally {
                cleanupRecording();
            }
        };

        // Start emitting chunks every 1.5s directly to memory and DDrive
        recorder.start(1500);

        updateState({
            isRecording: true,
            duration: 0,
            isVideo,
            uploadedBytes: 0,
            channelName: channelTitle
        });

        // Duration timer
        if (durationInterval) clearInterval(durationInterval);
        durationInterval = setInterval(() => {
            updateState({ duration: recState.duration + 1 });
        }, 1000);

        showToast(`Recording live to DDrive: ${isVideo ? "Screenshare + Voice" : "Voice call"} (0 disk used)`, {
            variant: "success"
        });
    } catch (e: any) {
        cleanupRecording();
        showToast(`Could not start recording: ${e?.message || e}`, { variant: "danger" });
    }
}

export async function stopRecording() {
    if (!recState.isRecording || !currentMediaRecorder) return;
    showToast("Finalizing live upload to DDrive...", { variant: "info" });
    if (currentMediaRecorder.state !== "inactive") {
        currentMediaRecorder.stop();
    }
}

function cleanupRecording() {
    if (durationInterval) {
        clearInterval(durationInterval);
        durationInterval = null;
    }
    stopWatchdog();

    currentMediaRecorder = null;
    currentRecId = null;
    currentOffset = 0;
    currentRecordedVideo = null;

    if (currentAudioContext && currentAudioContext.state !== "closed") {
        currentAudioContext.close().catch(() => {});
        currentAudioContext = null;
    }

    Native?.setRecordingActive?.(false);

    updateState({
        isRecording: false,
        duration: 0,
        isVideo: false,
        uploadedBytes: 0,
        channelName: ""
    });
}

function formatDuration(sec: number): string {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function formatBytes(bytes: number): string {
    if (bytes < 1024 * 1024) {
        return `${(bytes / 1024).toFixed(0)} KB`;
    }
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ------------------------------------------------------------- React UI Component
export function RecordButton() {
    const [state, setState] = useState<RecState>(recState);

    useEffect(() => {
        listeners.add(setState);
        return () => {
            listeners.delete(setState);
        };
    }, []);

    const toggle = (e: React.MouseEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (state.isRecording) {
            stopRecording();
        } else {
            startRecording();
        }
    };

    if (state.isRecording) {
        return (
            <div className="ddrive-rec-container">
                <button
                    className="ddrive-rec-btn recording"
                    onClick={toggle}
                    title="Click to stop and save recording to DDrive"
                >
                    <span className="ddrive-rec-dot" />
                    <span className="ddrive-rec-timer">{formatDuration(state.duration)}</span>
                    <span className="ddrive-rec-mode-badge">{state.isVideo ? "Screen" : "Voice"}</span>
                    <span className="ddrive-rec-stats">{formatBytes(state.uploadedBytes)}</span>
                </button>
            </div>
        );
    }

    return (
        <div className="ddrive-rec-container">
            <button
                className="ddrive-rec-btn idle"
                onClick={toggle}
                title="Record call & screenshare live to DDrive (Zero disk used)"
            >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="#ed4245">
                    <circle cx="12" cy="12" r="10" />
                </svg>
                <span>Record</span>
            </button>
        </div>
    );
}

// ------------------------------------------------------------- Plugin Definition
export default definePlugin({
    name: "DDriveRecorder",
    description: "Telegram-style call & screenshare recording live-streamed to DDrive without touching local disk.",
    authors: [{ name: "XO.ST", id: 0n }],
    managedStyle: style,
    settings,

    start() {
        setupVisibilityHooks();

        // Listen for call state changes: stop recording if call ends, or auto-record
        if (RTCConnectionStore) {
            RTCConnectionStore.addChangeListener(this.onRTCChange);
        }
    },

    stop() {
        if (recState.isRecording) {
            stopRecording();
        }
        if (RTCConnectionStore) {
            RTCConnectionStore.removeChangeListener(this.onRTCChange);
        }
    },

    onRTCChange() {
        const connected = RTCConnectionStore?.isConnected?.();
        if (recState.isRecording && !connected) {
            // Left call while recording: finalize and close
            stopRecording();
        } else if (!recState.isRecording && connected && settings.store.autoRecord) {
            // Joined call with autoRecord enabled
            startRecording();
        }
    },

    patches: [
        {
            find: '"RTCConnectionMenu"',
            replacement: {
                match: /("RTCConnectionMenu".{0,300}?lineClamp:1,children:)(\i)(?=,|}\))/,
                replace: "$1[$2,$self.renderRecordButton()]"
            }
        }
    ],

    renderRecordButton: ErrorBoundary.wrap(() => {
        return <RecordButton />;
    }, { noop: true })
});
