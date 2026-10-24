/*
 * DDrive Call & Screenshare Recorder for Vencord
 * Live-streams call audio and screenshare video straight to DDrive in memory
 * Zero disk usage, no background pauses when minimized
 */

import { definePluginSettings } from "@api/Settings";
import ErrorBoundary from "@components/ErrorBoundary";
import definePlugin, { OptionType, PluginNative } from "@utils/types";
import {
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
        description: "DDrive Server URL: the address of its dashboard (e.g. http://127.0.0.1:8765)",
        default: "http://127.0.0.1:8765"
    },
    recorderKey: {
        type: OptionType.STRING,
        description: "DDrive Recorder Key (DDrive dashboard: Settings > Live upload > Make a key, then Copy)",
        default: ""
    },
    folderName: {
        type: OptionType.STRING,
        description: "Folder inside DDrive's recordings folder (/Recordings unless changed in DDrive)",
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
let currentUpload: Upload | null = null;
let currentRecordedVideo: HTMLVideoElement | null = null;
let currentAudioContext: AudioContext | null = null;
let captureTracks: MediaStreamTrack[] = [];
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
        captureTracks.push(...micStream.getTracks());
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
            captureTracks.push(...audioTracks);
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

// ------------------------------------------------------------- Upload to DDrive
// Parts go to DDrive one at a time, in order. A part stays here until DDrive has it: when DDrive is slow
// (it asks to wait), can't be reached for a while, or says it has less than was sent, it is sent again
// from where DDrive is. Only an answer that can't get better (wrong key, recording gone) stops it.
interface Upload {
    serverUrl: string;
    recorderKey: string;
    id: string;
    queue: { offset: number; data: Uint8Array; }[];
    total: number; // bytes recorded
    acked: number; // bytes DDrive has
    reading: Promise<void>; // parts of the recorder being read, in order
    pumping: Promise<void> | null;
    fatal: string | null;
    onFatal: (why: string) => void;
}

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
const FATAL = new Set([400, 401, 403, 404, 409, 410, 413]);

function addPart(up: Upload, blob: Blob) {
    up.reading = up.reading.then(async () => {
        const data = new Uint8Array(await blob.arrayBuffer());
        if (!data.byteLength) return;
        up.queue.push({ offset: up.total, data });
        up.total += data.byteLength;
        kick(up);
    }).catch(e => console.error("[DDriveRecorder] Could not read a recorded part:", e));
}

function kick(up: Upload) {
    if (!up.pumping) up.pumping = pump(up).finally(() => { up.pumping = null; });
}

async function pump(up: Upload) {
    let tries = 0;
    while (up.queue.length && !up.fatal) {
        const part = up.queue[0];
        if (part.offset + part.data.byteLength <= up.acked) { up.queue.shift(); continue; }
        const res = await Native!.sendLiveChunk({
            serverUrl: up.serverUrl, recorderKey: up.recorderKey, id: up.id, offset: part.offset, chunk: part.data
        });
        if (res.status === 200 && typeof res.offset === "number") {
            up.acked = Math.max(up.acked, res.offset);
            tries = 0;
            if (!res.ok && res.wait) await sleep(1000); // DDrive is still sending earlier pieces to Discord
            continue; // (not ok, no wait: DDrive has up to res.offset; go on from there)
        }
        if (FATAL.has(res.status)) {
            up.fatal = res.error || `HTTP ${res.status}`;
            up.onFatal(up.fatal);
            break;
        }
        tries++; // can't reach DDrive (or it is busy): try again
        if (tries === 3) console.warn("[DDriveRecorder] DDrive isn't answering, still trying:", res.error);
        await sleep(Math.min(10000, 1000 * tries));
    }
}

/** Every part recorded is in DDrive (or it can't be): wait for that, at most `ms`. */
async function drain(up: Upload, ms: number) {
    const end = Date.now() + ms;
    await up.reading;
    while ((up.queue.length || up.pumping) && !up.fatal && Date.now() < end) {
        kick(up);
        await Promise.race([up.pumping, sleep(1000)]);
    }
}

// ------------------------------------------------------------- Recording Lifecycle
export async function startRecording() {
    if (recState.isRecording) return;

    const { serverUrl, recorderKey, folderName, recordScreenshare, keepAliveWhenMinimized } = settings.store;

    if (!recorderKey || !serverUrl) {
        showToast({ text: "Set your DDrive Server URL and Recorder Key in Settings > Vencord > Plugins > DDriveRecorder.", variant: "critical" });
        return;
    }
    if (!Native) {
        showToast({ text: "DDriveRecorder's desktop part is missing: Vencord has to be rebuilt with the plugin (Install-Recorder.cmd).", variant: "critical" });
        return;
    }

    if (!RTCConnectionStore?.isConnected?.()) {
        showToast({ text: "Connect to a voice call or channel before recording.", variant: "critical" });
        return;
    }

    const channelId = RTCConnectionStore.getChannelId();
    const channel = channelId ? ChannelStore?.getChannel?.(channelId) : null;
    const channelTitle = channel?.name || "Call";

    try {
        showToast({ text: "Starting live call recording...", variant: "default" });

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

        const recPath = openRes.path || fileName;
        const up: Upload = {
            serverUrl, recorderKey, id: openRes.id, queue: [], total: 0, acked: 0,
            reading: Promise.resolve(), pumping: null, fatal: null,
            onFatal: why => {
                showToast({ text: `DDrive stopped taking the recording: ${why}`, variant: "critical" });
                stopRecording();
            }
        };
        currentUpload = up;

        // 7. Setup MediaRecorder
        const recorder = new MediaRecorder(combinedStream, {
            mimeType,
            videoBitsPerSecond: isVideo ? 2500000 : undefined,
            audioBitsPerSecond: 128000
        });

        currentMediaRecorder = recorder;

        recorder.ondataavailable = (e: BlobEvent) => {
            if (e.data && e.data.size > 0 && currentUpload === up) {
                addPart(up, e.data);
                updateState({ uploadedBytes: up.total + e.data.size });
            }
        };

        recorder.onstop = async () => {
            try {
                // the last part arrives just before this: let it (and anything still on its way) get to DDrive
                await drain(up, 5 * 60 * 1000);
                if (up.queue.length && !up.fatal) {
                    showToast({ text: "DDrive couldn't be reached: the end of the recording wasn't saved.", variant: "critical" });
                }
                const closeRes = await Native.closeLiveRecording({
                    serverUrl,
                    recorderKey,
                    id: up.id,
                    offset: up.acked,
                    wait: 30
                });

                if (closeRes && closeRes.ok) {
                    showToast({ text: `Call recorded to DDrive: ${closeRes.path || recPath}`, variant: "success" });
                } else {
                    showToast({ text: `Recording finished: ${closeRes?.error || "Uploaded"}`, variant: "default" });
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

        showToast({ text: `Recording live to DDrive: ${isVideo ? "Screenshare + Voice" : "Voice call"} (0 disk used)`, variant: "success" });
    } catch (e: any) {
        cleanupRecording();
        showToast({ text: `Could not start recording: ${e?.message || e}`, variant: "critical" });
    }
}

export async function stopRecording() {
    if (!recState.isRecording || !currentMediaRecorder) return;
    showToast({ text: "Finalizing live upload to DDrive...", variant: "default" });
    if (currentMediaRecorder.state !== "inactive") {
        currentMediaRecorder.stop(); // (a second press while it stops does nothing)
    }
}

function cleanupRecording() {
    if (durationInterval) {
        clearInterval(durationInterval);
        durationInterval = null;
    }
    stopWatchdog();

    currentMediaRecorder = null;
    currentUpload = null;
    currentRecordedVideo = null;

    for (const t of captureTracks) t.stop(); // the microphone and desktop audio this recording opened
    captureTracks = [];
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

    // also in the Vencord toolbox (the icon at the top right), should the voice panel button not show
    toolboxActions: {
        "Start / stop DDrive recording"() {
            if (recState.isRecording) stopRecording();
            else startRecording();
        }
    },

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
                // (CallTimer patches the same spot: after it, children is already an array)
                match: /("RTCConnectionMenu".{0,300}?lineClamp:1,children:)(?:(\i)(?=,|}\))|\[)/,
                replace: (_, pre, child) => child ? `${pre}[${child},$self.renderRecordButton()]` : `${pre}[$self.renderRecordButton(),`
            }
        }
    ],

    renderRecordButton: ErrorBoundary.wrap(() => {
        return <RecordButton />;
    }, { noop: true })
});
