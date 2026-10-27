/*
 * DDrive Call & Screenshare Recorder for Vencord
 * Live-streams call audio and screenshare video straight to DDrive in memory
 * Zero disk usage, no background pauses when minimized
 */

import { NavContextMenuPatchCallback } from "@api/ContextMenu";
import { definePluginSettings } from "@api/Settings";
import ErrorBoundary from "@components/ErrorBoundary";
import definePlugin, { OptionType, PluginNative } from "@utils/types";
import type { User } from "@vencord/discord-types";
import {
    ChannelStore,
    FluxDispatcher,
    MediaEngineStore,
    Menu,
    React,
    RTCConnectionStore,
    showToast,
    useEffect,
    UserStore,
    useState,
    WindowStore
} from "@webpack/common";

import style from "./style.css?managed";

const Native = (VencordNative as any)?.pluginHelpers?.DDriveRecorder as PluginNative<typeof import("./native")> | undefined;

export const settings = definePluginSettings({
    serverUrl: {
        type: OptionType.STRING,
        description: "Where recordings go: DDrive Lite on this computer (http://127.0.0.1:8766, for an end-to-end encrypted account), or a DDrive dashboard address",
        default: "http://127.0.0.1:8766"
    },
    recorderKey: {
        type: OptionType.STRING,
        description: "Recorder Key: an admin makes one on their account page in the DDrive dashboard (Call recorder for Discord), or 'lite recorder on' on this computer (or, for the owner's drive, Settings > Live upload)",
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
    format: {
        type: OptionType.SELECT,
        description: "File format (MP4 when this Discord can record it, else WebM)",
        options: [
            { label: "MP4", value: "mp4", default: true },
            { label: "WebM", value: "webm" }
        ]
    },
    recordScreenshare: {
        type: OptionType.BOOLEAN,
        description: "Record screenshare video when anyone is sharing their screen in the call",
        default: true
    },
    excludedUsers: {
        type: OptionType.STRING,
        description: "People left out of recordings (user IDs, separated by commas; easier: right-click someone > Exclude from DDrive recordings). "
            + "You: your microphone isn't recorded. Someone else: the call's sound is left out while they talk (and so is anyone talking at the same time).",
        default: ""
    },
    callSound: {
        type: OptionType.SELECT,
        description: "What is recorded besides your microphone",
        options: [
            { label: "Discord only: the call, streams and soundboard (not other programs)", value: "discord", default: true },
            { label: "Whole computer: everything this computer plays", value: "computer" }
        ]
    },
    localCopy: {
        type: OptionType.SELECT,
        description: "Also write the recording to this computer as it is made: kept when DDrive couldn't take all of it (or always)",
        options: [
            { label: "When DDrive fails: kept only if DDrive didn't get all of it", value: "fallback", default: true },
            { label: "Always: keep a copy on this computer too", value: "always" },
            { label: "Off: DDrive only", value: "off" }
        ]
    },
    localFolder: {
        type: OptionType.STRING,
        description: "Folder for the copies on this computer (empty: Videos\\DDrive Recordings)",
        default: ""
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
    phase: "idle" | "starting" | "recording" | "saving";
    duration: number;
    isVideo: boolean;
    uploadedBytes: number; // recorded
    savedBytes: number; // of that, in DDrive
    channelName: string;
}

let recState: RecState = {
    isRecording: false,
    phase: "idle",
    duration: 0,
    isVideo: false,
    uploadedBytes: 0,
    savedBytes: 0,
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
    // the biggest one showing on screen first (the stream being watched, not a camera tile or a preview)
    const videos = Array.from(document.querySelectorAll<HTMLVideoElement>("video"))
        .filter(v => v.videoWidth > 0 || v.srcObject instanceof MediaStream)
        .sort((a, b) => b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight);
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

// ------------------------------------------------------------- People left out of recordings
// Discord mixes everyone's voice into one sound before anything else gets it, so one person can't be
// taken out of it. What can be done: you (your microphone isn't recorded), and for anyone else, the
// call's sound is muted in the recording while they talk (Discord says who talks: SPEAKING), and for
// HANG ms after, which leaves out whoever talks at the same time too.
const HANG = 400;
const talking = new Map<string, number>(); // user -> until when (Infinity: talking now)
let gateTimer: ReturnType<typeof setInterval> | null = null;
let gateNodes: { mic: GainNode; call: GainNode; } | null = null;

function excludedSet(): Set<string> {
    return new Set((settings.store.excludedUsers || "").split(/[\s,]+/).filter(Boolean));
}

function setExcluded(userId: string, on: boolean) {
    const ids = excludedSet();
    if (on) ids.add(userId);
    else ids.delete(userId);
    settings.store.excludedUsers = [...ids].join(", ");
}

function onSpeaking(e: { userId?: string; speakingFlags?: number; speaking?: boolean; }) {
    if (!e?.userId) return;
    const on = e.speakingFlags !== undefined ? !!e.speakingFlags : !!e.speaking;
    talking.set(e.userId, on ? Infinity : Date.now() + HANG);
}

/** The microphone and the call's sound each go through a gate of their own (made once per recording). */
function gates(ctx: AudioContext, dest: AudioNode) {
    if (gateNodes && gateNodes.mic.context === ctx) return gateNodes;
    const mic = ctx.createGain(), call = ctx.createGain();
    mic.connect(dest);
    call.connect(dest);
    gateNodes = { mic, call };
    const apply = () => {
        const ids = excludedSet();
        const me = UserStore?.getCurrentUser?.()?.id;
        const now = Date.now();
        const quiet = [...ids].some(id => id !== me && (talking.get(id) ?? 0) > now);
        mic.gain.setTargetAtTime(me && ids.has(me) ? 0 : 1, ctx.currentTime, 0.015);
        call.gain.setTargetAtTime(quiet ? 0 : 1, ctx.currentTime, 0.015);
    };
    apply();
    if (gateTimer) clearInterval(gateTimer);
    gateTimer = setInterval(apply, 50);
    return gateNodes;
}

function stopGates() {
    if (gateTimer) clearInterval(gateTimer);
    gateTimer = null;
    gateNodes = null;
}

const userContextMenu: NavContextMenuPatchCallback = (children, { user }: { user?: User; }) => {
    if (!user) return;
    const on = excludedSet().has(user.id);
    const me = UserStore?.getCurrentUser?.()?.id === user.id;
    children.push(
        <Menu.MenuCheckboxItem
            id="vc-ddrive-rec-exclude"
            label="Exclude from DDrive recordings"
            checked={on}
            action={() => {
                setExcluded(user.id, !on);
                showToast({
                    text: !on
                        ? (me ? "Your microphone is left out of DDrive recordings" : `${user.username} is left out of DDrive recordings (the call's sound is muted while they talk)`)
                        : `${me ? "You are" : user.username + " is"} recorded again`,
                    variant: "success"
                });
            }}
        />
    );
};

// ------------------------------------------------------------- Discord's own sound
// What Discord plays (the others in the call, streams, the soundboard), and nothing of other programs:
// Windows' process loopback, by a small helper of the desktop side (native.ts, tapStart), which hands it
// over as 48 kHz 16-bit stereo. It is played into the recording from a buffer of up to a second.
const TAP_RATE = 48000;
let tapTimer: ReturnType<typeof setInterval> | null = null;
let tapNode: ScriptProcessorNode | null = null;

async function discordSound(ctx: AudioContext): Promise<AudioNode | null> {
    const started = await Native!.tapStart();
    if (!started.ok) {
        console.warn(`[DDriveRecorder] Discord's own sound can't be taken: ${started.error}`);
        showToast({ text: `Discord's sound can't be recorded here (${started.error}): your microphone only`, variant: "critical" });
        return null;
    }
    const CAP = TAP_RATE * 2;
    const ring = new Float32Array(CAP * 2);
    let written = 0, read = 0, warned = false;
    const step = TAP_RATE / ctx.sampleRate;
    tapTimer = setInterval(async () => {
        const got = await Native!.tapRead();
        if (got.data) {
            const bin = atob(got.data);
            for (let i = 0; i + 3 < bin.length; i += 4) {
                const l = (bin.charCodeAt(i) | bin.charCodeAt(i + 1) << 8) << 16 >> 16;
                const r = (bin.charCodeAt(i + 2) | bin.charCodeAt(i + 3) << 8) << 16 >> 16;
                const at = (written % CAP) * 2;
                ring[at] = l / 32768;
                ring[at + 1] = r / 32768;
                written++;
            }
            if (written - read > TAP_RATE) read = written - TAP_RATE / 5; // (fell behind: keep it near)
        }
        if (!got.alive && !warned) {
            warned = true;
            console.warn(`[DDriveRecorder] Discord's sound stopped: ${got.error || "the helper ended"}`);
        }
    }, 40);
    const node = ctx.createScriptProcessor(2048, 1, 2);
    node.onaudioprocess = e => {
        const L = e.outputBuffer.getChannelData(0), R = e.outputBuffer.getChannelData(1);
        for (let i = 0; i < L.length; i++) {
            if (written - read < 2) { L[i] = R[i] = 0; continue; }
            const f = Math.floor(read), k = read - f;
            const a = (f % CAP) * 2, b = ((f + 1) % CAP) * 2;
            L[i] = ring[a] + (ring[b] - ring[a]) * k;
            R[i] = ring[a + 1] + (ring[b + 1] - ring[a + 1]) * k;
            read += step;
        }
    };
    tapNode = node;
    return node;
}

function stopDiscordSound() {
    if (tapTimer) clearInterval(tapTimer);
    tapTimer = null;
    tapNode?.disconnect();
    tapNode = null;
    Native?.tapStop?.();
}

// ------------------------------------------------------------- Video: the stream drawn onto a canvas
// MediaRecorder writes nothing at all (not even the sound) while a video track it records gives no
// frames, and the stream's own video gives none while Discord doesn't show it (minimized, another view)
// or after Discord swapped its video element: such recordings stopped at the file's first 1257 bytes.
// So the stream is drawn onto a canvas 30 times a second, black while there's none, and the canvas is
// recorded: its frames never stop, and it follows the stream to whatever element shows it now.
interface Compositor { track: MediaStreamTrack; video: () => HTMLVideoElement | null; stop: () => void; }

function makeCompositor(first: HTMLVideoElement): Compositor {
    const w0 = first.videoWidth || 1280, h0 = first.videoHeight || 720;
    const scale = Math.min(1, 1920 / w0, 1080 / h0);
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(2, Math.round(w0 * scale / 2) * 2);
    canvas.height = Math.max(2, Math.round(h0 * scale / 2) * 2);
    const g = canvas.getContext("2d", { alpha: false })!;
    let video: HTMLVideoElement | null = first;
    let looked = 0;
    const draw = () => {
        const now = Date.now();
        if (!video || !video.isConnected || !video.videoWidth || now - looked > 2000) {
            looked = now;
            video = findActiveScreenshare().videoEl ?? (video?.isConnected ? video : null);
        }
        g.fillStyle = "#000";
        g.fillRect(0, 0, canvas.width, canvas.height);
        if (video && video.videoWidth) {
            const k = Math.min(canvas.width / video.videoWidth, canvas.height / video.videoHeight);
            const w = video.videoWidth * k, h = video.videoHeight * k;
            try {
                g.drawImage(video, (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
            } catch (_) {}
        }
    };
    draw();
    const timer = setInterval(draw, 1000 / 30);
    const track = canvas.captureStream(30).getVideoTracks()[0];
    return { track, video: () => video, stop: () => { clearInterval(timer); track.stop(); } };
}

let currentCompositor: Compositor | null = null;

async function captureCallAudio(): Promise<{ stream: MediaStream; ctx: AudioContext }> {
    const audioCtx = new AudioContext();
    if (audioCtx.state === "suspended") {
        await audioCtx.resume();
    }
    const dest = audioCtx.createMediaStreamDestination();
    let hasConnected = false;
    const heard: { name: string; node: AudioNode; }[] = [];

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
            micSource.connect(gates(audioCtx, dest).mic);
            heard.push({ name: `microphone (${micStream.getAudioTracks()[0].label})`, node: micSource });
            hasConnected = true;
        }
    } catch (e) {
        console.warn(`[DDriveRecorder] Microphone capture note: ${(e as any)?.name}: ${(e as any)?.message}`);
    }

    // 2. The call's sound: Discord's own (settings: callSound), or everything the computer plays
    const discordOnly = (settings.store.callSound ?? "discord") === "discord";
    if (discordOnly) {
        const node = await discordSound(audioCtx);
        if (node) {
            node.connect(gates(audioCtx, dest).call);
            heard.push({ name: "Discord's sound", node });
            hasConnected = true;
        }
    }
    if (!discordOnly) try {
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
                sysSource.connect(gates(audioCtx, dest).call);
                heard.push({ name: `computer sound (${audioTracks[0].label})`, node: sysSource });
                hasConnected = true;
            } else {
                console.warn("[DDriveRecorder] Computer sound: the desktop capture came without sound");
            }
        } else {
            console.warn("[DDriveRecorder] Computer sound: no screen to take the sound of");
        }
    } catch (e) {
        console.warn(`[DDriveRecorder] Desktop loopback audio fallback: ${(e as any)?.name}: ${(e as any)?.message}`);
    }

    // 3. Fallback: connect DOM media elements (HTMLAudioElement / HTMLVideoElement)
    if (!hasConnected && !discordOnly) {
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

    console.info(`[DDriveRecorder] Recording sound from: ${heard.map(h => h.name).join(", ") || "nothing"}`);
    watchLevels(audioCtx, heard);
    return { stream: dest.stream, ctx: audioCtx };
}

/** For the first 20 seconds, how loud each sound is (Discord's log): silent computer sound means the
 * call plays somewhere the recorder can't hear (another output device than Windows' default). */
function watchLevels(ctx: AudioContext, heard: { name: string; node: AudioNode; }[]) {
    const meters = heard.map(h => {
        const a = ctx.createAnalyser();
        a.fftSize = 2048;
        h.node.connect(a);
        return { name: h.name, a, peak: 0 };
    });
    const buf = new Float32Array(2048);
    let n = 0;
    const timer = setInterval(() => {
        for (const m of meters) {
            m.a.getFloatTimeDomainData(buf);
            for (const v of buf) m.peak = Math.max(m.peak, Math.abs(v));
        }
        if (++n % 20 === 0) {
            console.info(`[DDriveRecorder] Sound levels (0 = silent, 1 = loudest): ${meters.map(m => `${m.name}: ${m.peak.toFixed(3)}`).join("; ")}`);
            meters.forEach(m => { m.peak = 0; });
        }
        if (n >= 80 || ctx.state === "closed") clearInterval(timer);
    }, 250);
}

/** Discord plays the call on the output device chosen in its settings; the computer sound the recorder
 * takes is that of Windows' default device. When they differ, the others in the call aren't recorded. */
function checkOutputDevice() {
    const out = (MediaEngineStore as any)?.getOutputDeviceId?.();
    if (out && out !== "default") {
        console.warn(`[DDriveRecorder] Discord plays the call on output device ${out}, not Windows' default`);
        showToast({
            text: "The others in the call may not be recorded: in Discord, set Voice & Video > Output Device to Default "
                + "(and pick your headset as Windows' default instead).",
            variant: "critical"
        });
    }
}

// ------------------------------------------------------------- Upload to DDrive
// Parts go to DDrive one at a time, in order. A part stays here until DDrive has it: when DDrive is slow
// (it asks to wait), can't be reached for a while, or says it has less than was sent, it is sent again
// from where DDrive is. Only an answer that can't get better (wrong key, recording gone) stops it.
interface Upload {
    serverUrl: string;
    recorderKey: string;
    id: string;
    queue: { offset: number; size: number; data: string; }[]; // data: base64
    total: number; // bytes recorded
    acked: number; // bytes DDrive has
    reading: Promise<void>; // parts of the recorder being read, in order
    pumping: Promise<void> | null;
    fatal: string | null;
    onFatal: (why: string) => void;
    local: string | null; // the copy on this computer (its id with the desktop side), or none
    localPath: string;
    localFailed: boolean;
}

const MEMORY_MAX = 256 * 1024 * 1024; // more than this not in DDrive yet, with a copy here: DDrive is given up on

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
const FATAL = new Set([400, 401, 403, 404, 409, 410, 413]);

// A part as base64 text. (Sent as bytes, it reached the desktop side of the plugin as 1 byte.)
function readBase64(blob: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result).slice(String(r.result).indexOf(",") + 1));
        r.onerror = () => reject(r.error);
        r.readAsDataURL(blob);
    });
}

function addPart(up: Upload, blob: Blob) {
    up.reading = up.reading.then(async () => {
        if (!blob.size) return;
        const data = await readBase64(blob);
        if (up.local && !up.localFailed) { // the copy on this computer first: it is what is left if DDrive fails
            const w = await Native!.localWrite({ id: up.local, chunk64: data });
            if (!w.ok) {
                up.localFailed = true;
                showToast({ text: `The copy on this computer couldn't be written: ${w.error}`, variant: "critical" });
            }
        }
        up.total += blob.size;
        if (up.fatal) return; // (DDrive was given up on: the copy here has it all)
        up.queue.push({ offset: up.total - blob.size, size: blob.size, data });
        const waiting = up.queue.reduce((n, p) => n + p.size, 0);
        if (up.local && !up.localFailed && waiting > MEMORY_MAX) {
            up.fatal = `DDrive is ${Math.round(waiting / 2 ** 20)} MB behind`;
            up.onFatal(up.fatal);
            return;
        }
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
        if (part.offset + part.size <= up.acked) { up.queue.shift(); continue; }
        const res = await Native!.sendLiveChunk({
            serverUrl: up.serverUrl, recorderKey: up.recorderKey, id: up.id, offset: part.offset, chunk64: part.data
        });
        if (res.status === 200 && res.sent !== undefined && res.sent !== part.size) {
            up.fatal = `a part of ${part.size} bytes arrived as ${res.sent}`;
            up.onFatal(up.fatal);
            break;
        }
        if (res.status === 200 && typeof res.offset === "number") {
            const before = up.acked;
            up.acked = Math.max(up.acked, res.offset);
            tries = 0;
            if (currentUpload === up) updateState({ savedBytes: up.acked });
            if (!res.ok && res.wait) await sleep(1000); // DDrive is still sending earlier pieces to Discord
            else if (!res.ok && up.acked === before) await sleep(500); // (DDrive has up to res.offset: go on from there)
            continue;
        }
        if (FATAL.has(res.status)) {
            up.fatal = res.error || `HTTP ${res.status}`;
            up.onFatal(up.fatal);
            break;
        }
        if (up.local && !up.localFailed && tries >= 30) { // DDrive hasn't answered for minutes: the copy here goes on alone
            up.fatal = `no answer from ${up.serverUrl}`;
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
let starting = false; // a start is under way (it waits for DDrive): another one isn't begun meanwhile
let autoChannel: string | null = null; // the call auto-record last started for (or tried to): once per call

// How a recording is made: 0 as the settings say (MP4), 1 WebM, 2 WebM with sound only. A recording
// that has made next to nothing after STALL_AFTER is given up (DDrive keeps no file of it) and started
// again one step safer.
const STALL_AFTER = 10000;
const STALL_BYTES = 16 * 1024;
let restartAt: number | null = null;

export async function startRecording(level = 0) {
    if (recState.isRecording || starting) return;
    starting = true;
    updateState({ phase: "starting" });
    try {
        await begin(level);
    } finally {
        starting = false;
        if (recState.phase === "starting") updateState({ phase: recState.isRecording ? "recording" : "idle" });
    }
}

async function begin(level: number) {

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
        if ((settings.store.callSound ?? "discord") === "computer") checkOutputDevice();
        const { stream: audioStream, ctx: audioCtx } = await captureCallAudio();
        currentAudioContext = audioCtx;

        // 3. Screenshare Video Detection (drawn onto a canvas: see makeCompositor)
        let videoTrack: MediaStreamTrack | null = null;
        if (recordScreenshare && level < 2) {
            const screen = findActiveScreenshare();
            if (screen.videoEl) {
                currentCompositor = makeCompositor(screen.videoEl);
                videoTrack = currentCompositor.track;
                currentRecordedVideo = screen.videoEl;
            }
        }

        const isVideo = !!videoTrack;
        const tracks: MediaStreamTrack[] = [...audioStream.getAudioTracks()];
        if (videoTrack) tracks.push(videoTrack);

        const combinedStream = new MediaStream(tracks);

        // 4. Select MIME type: MP4 when Discord's Chromium can make it (and it is wanted), else WebM
        const mp4 = settings.store.format !== "webm" && level === 0;
        const wanted = isVideo
            ? [...(mp4 ? ["video/mp4;codecs=avc1.640028,mp4a.40.2", "video/mp4;codecs=avc1,mp4a.40.2", "video/mp4;codecs=avc1,opus", "video/mp4"] : []),
                "video/webm;codecs=vp8,opus", "video/webm"]
            : [...(mp4 ? ["audio/mp4;codecs=mp4a.40.2", "audio/mp4;codecs=opus", "audio/mp4"] : []),
                "audio/webm;codecs=opus", "audio/webm"];
        const mimeType = wanted.find(t => MediaRecorder.isTypeSupported(t)) ?? wanted[wanted.length - 1];
        const ext = mimeType.includes("mp4") ? "mp4" : "webm";
        if (mp4 && ext !== "mp4") console.warn("[DDriveRecorder] This Discord can't record MP4: recording WebM");

        // 5. Generate clean file name
        const cleanName = channelTitle.replace(/[\x00-\x1f/\\:*?"<>|]+/g, " ").trim() || "Discord_Call";
        const now = new Date();
        const pad = (n: number) => String(n).padStart(2, "0");
        const dateTag = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}_${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}`;
        const fileName = `${cleanName}_${dateTag}.${ext}`;

        // 6. The copy on this computer (settings: localCopy), then the stream on DDrive (/api/rec/open)
        const localMode = settings.store.localCopy ?? "fallback";
        let local: { ok: boolean; id?: string; path?: string; error?: string; } | null = null;
        if (localMode !== "off") {
            local = await Native.localOpen({ folder: settings.store.localFolder || "", name: fileName });
            if (!local.ok) showToast({ text: `No copy on this computer: ${local.error}`, variant: "critical" });
        }
        const openRes = await Promise.race([
            Native.openLiveRecording({
                serverUrl,
                recorderKey,
                folder: folderName || "Discord Calls",
                name: fileName,
                mime: mimeType
            }),
            sleep(20000).then(() => ({ ok: false, id: undefined, path: undefined, error: `no answer from ${serverUrl} in 20 seconds` }))
        ]);

        const localOk = !!(local?.ok && local.id);
        if (!openRes || !openRes.ok || !openRes.id) {
            const why = openRes?.error || "DDrive didn't answer";
            if (!localOk) throw new Error(why);
            showToast({ text: `DDrive can't take it (${why}): recording to this computer only`, variant: "critical" });
        }

        const recPath = openRes?.path || fileName;
        const up: Upload = {
            serverUrl, recorderKey, id: openRes?.id || "", queue: [], total: 0, acked: 0,
            reading: Promise.resolve(), pumping: null, fatal: openRes?.ok && openRes.id ? null : "DDrive couldn't be reached",
            local: localOk ? local!.id! : null, localPath: local?.path || "", localFailed: false,
            onFatal: why => {
                if (up.local && !up.localFailed) {
                    up.queue = [];
                    showToast({ text: `DDrive stopped taking the recording (${why}): it goes on, on this computer`, variant: "critical" });
                } else {
                    showToast({ text: `DDrive stopped taking the recording: ${why}`, variant: "critical" });
                    stopRecording();
                }
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
                const hasLocal = !!up.local && !up.localFailed;
                await drain(up, hasLocal ? 60 * 1000 : 5 * 60 * 1000);
                if (up.queue.length && !up.fatal && !hasLocal) {
                    showToast({ text: "DDrive couldn't be reached: the end of the recording wasn't saved.", variant: "critical" });
                }
                const closeRes = up.id ? await Native.closeLiveRecording({
                    serverUrl,
                    recorderKey,
                    id: up.id,
                    offset: up.acked,
                    wait: 30
                }) : null;
                // (in DDrive only once it says done: Lite may still be handing it to the host, and could give up)
                const whole = !!closeRes?.ok && !!closeRes?.done && !up.fatal && up.acked === up.total;
                const keep = !!up.local && restartAt === null && (localMode === "always" || !whole);
                const kept = up.local ? await Native.localClose({ id: up.local, keep: keep && up.total > 0 }) : null;

                if (restartAt !== null) {
                    // (a stalled one: nothing worth keeping, DDrive drops it)
                } else if (whole) {
                    showToast({
                        text: `Call recorded to DDrive: ${closeRes!.path || recPath}` + (kept?.kept ? ` (and to ${kept.path})` : ""),
                        variant: "success"
                    });
                } else if (kept?.kept && closeRes?.ok && !closeRes.done && !up.fatal) {
                    showToast({ text: `DDrive is still storing it (the host is slow); a copy is kept on this computer, ${kept.path}`, variant: "default" });
                } else if (kept?.kept) {
                    showToast({ text: `DDrive didn't get all of it: the recording is on this computer, ${kept.path}`, variant: "critical" });
                } else {
                    showToast({ text: `Recording finished: ${closeRes?.error || up.fatal || "Uploaded"}`, variant: "default" });
                }
            } catch (err: any) {
                console.error("[DDriveRecorder] Error closing recording:", err);
            } finally {
                cleanupRecording();
                const again = restartAt;
                restartAt = null;
                if (again !== null && RTCConnectionStore?.isConnected?.()) startRecording(again);
            }
        };

        recorder.onerror = (e: any) => {
            console.error(`[DDriveRecorder] The recorder stopped: ${e?.error?.name}: ${e?.error?.message}`);
            showToast({ text: `The recording stopped: ${e?.error?.message || "the recorder failed"}`, variant: "critical" });
        };

        // Nothing (or next to nothing) made after a while: start again a safer way
        setTimeout(() => {
            if (currentUpload !== up || up.total >= STALL_BYTES || recState.phase !== "recording" || level >= 2) return;
            console.warn(`[DDriveRecorder] ${mimeType} made only ${up.total} bytes in ${STALL_AFTER / 1000} s: starting again `
                + (level === 0 ? "as WebM" : "with sound only"));
            showToast({ text: level === 0 ? "MP4 didn't work here: recording as WebM" : "Video didn't work here: recording the sound only", variant: "default" });
            restartAt = level + 1;
            if (recorder.state !== "inactive") recorder.stop();
        }, STALL_AFTER);

        // Start emitting chunks every 1.5s directly to memory and DDrive
        recorder.start(1500);

        updateState({
            isRecording: true,
            phase: "recording",
            duration: 0,
            isVideo,
            uploadedBytes: 0,
            savedBytes: 0,
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
    showToast({ text: "Saving the recording to DDrive...", variant: "default" });
    updateState({ phase: "saving" });
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
    currentCompositor?.stop();
    stopDiscordSound();
    stopGates();
    currentCompositor = null;

    for (const t of captureTracks) t.stop(); // the microphone and desktop audio this recording opened
    captureTracks = [];
    if (currentAudioContext && currentAudioContext.state !== "closed") {
        currentAudioContext.close().catch(() => {});
        currentAudioContext = null;
    }

    Native?.setRecordingActive?.(false);

    updateState({
        isRecording: false,
        phase: "idle",
        duration: 0,
        isVideo: false,
        uploadedBytes: 0,
        savedBytes: 0,
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

// ------------------------------------------------------------- The button in the call's control bar
// Discord's call view has a row of big buttons (microphone, camera, screen, ..., Disconnect). There is no
// way in for a plugin there, so a Record button of the same size is put next to Disconnect in the page
// itself, and put back whenever Discord draws the row again.
const BAR_ID = "ddrive-rec-callbar";
let barObserver: MutationObserver | null = null;
let barQueued = false;

function findDisconnect(): HTMLElement | null {
    let best: HTMLElement | null = null;
    let bestH = 0;
    for (const b of document.querySelectorAll<HTMLElement>("button[aria-label], [role='button'][aria-label]")) {
        if (b.closest(`#${BAR_ID}`)) continue;
        if (!/^(disconnect|leave call|hang up|end call)$/i.test((b.getAttribute("aria-label") || "").trim())) continue;
        const h = b.getBoundingClientRect().height;
        if (h >= 36 && h > bestH) { best = b; bestH = h; } // (the call view's, not the small one in the voice panel)
    }
    return best;
}

function barLabel(): { text: string; title: string; } {
    const s = recState;
    if (s.phase === "starting") return { text: "Starting…", title: "Starting the recording" };
    if (s.phase === "saving") {
        return { text: "Saving…", title: `Saving to DDrive: ${formatBytes(s.savedBytes)} of ${formatBytes(s.uploadedBytes)} there` };
    }
    if (s.isRecording) {
        return {
            text: formatDuration(s.duration),
            title: `Recording ${s.isVideo ? "screen + voice" : "voice"}: ${formatBytes(s.uploadedBytes)} recorded, `
                + `${formatBytes(s.savedBytes)} saved in DDrive. Click to stop and save.`
        };
    }
    return { text: "", title: "Record this call into DDrive" };
}

function renderBar() {
    const btn = document.querySelector<HTMLButtonElement>(`#${BAR_ID} button`);
    if (!btn) return;
    const { text, title } = barLabel();
    btn.className = `ddrive-callbar-btn ${recState.phase}`;
    btn.setAttribute("aria-label", title);
    btn.title = title;
    btn.disabled = recState.phase === "starting" || recState.phase === "saving";
    (btn.querySelector(".ddrive-callbar-text") as HTMLElement).textContent = text;
}

function placeBar() {
    barQueued = false;
    const disc = findDisconnect();
    const have = document.getElementById(BAR_ID);
    if (!disc) { have?.remove(); return; }
    // next to the outermost wrapper Disconnect has of its own
    let anchor: HTMLElement = disc;
    while (anchor.parentElement && anchor.parentElement.childElementCount === 1 && anchor.parentElement !== document.body) {
        anchor = anchor.parentElement;
    }
    const row = anchor.parentElement;
    if (!row) return;
    if (have && have.parentElement === row && have.nextElementSibling === anchor) return; // (already there)
    have?.remove();
    const r = disc.getBoundingClientRect();
    const wrap = document.createElement("div");
    wrap.id = BAR_ID;
    const gap = getComputedStyle(row).columnGap;
    if (!gap || gap === "normal" || gap === "0px") wrap.style.marginRight = "8px";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.style.height = `${Math.round(r.height)}px`;
    btn.style.minWidth = `${Math.round(r.width)}px`;
    btn.style.borderRadius = getComputedStyle(disc).borderRadius || "8px";
    btn.innerHTML = '<span class="ddrive-callbar-icon"><span class="ddrive-callbar-dot"></span></span><span class="ddrive-callbar-text"></span>';
    btn.addEventListener("click", e => {
        e.preventDefault();
        e.stopPropagation();
        if (recState.isRecording) stopRecording();
        else startRecording();
    });
    wrap.appendChild(btn);
    row.insertBefore(wrap, anchor);
    renderBar();
}

function queueBar() {
    if (barQueued) return;
    barQueued = true;
    requestAnimationFrame(placeBar);
}

function startBar() {
    listeners.add(renderBar);
    barObserver = new MutationObserver(queueBar);
    barObserver.observe(document.body, { childList: true, subtree: true });
    queueBar();
}

function stopBar() {
    listeners.delete(renderBar);
    barObserver?.disconnect();
    barObserver = null;
    document.getElementById(BAR_ID)?.remove();
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
    contextMenus: {
        "user-context": userContextMenu
    },

    toolboxActions: {
        "Start / stop DDrive recording"() {
            if (recState.isRecording) stopRecording();
            else startRecording();
        }
    },

    start() {
        setupVisibilityHooks();
        startBar();
        FluxDispatcher.subscribe("SPEAKING", onSpeaking);

        // Listen for call state changes: stop recording if call ends, or auto-record
        if (RTCConnectionStore) {
            RTCConnectionStore.addChangeListener(this.onRTCChange);
        }
    },

    stop() {
        stopBar();
        FluxDispatcher.unsubscribe("SPEAKING", onSpeaking);
        if (recState.isRecording) {
            stopRecording();
        }
        if (RTCConnectionStore) {
            RTCConnectionStore.removeChangeListener(this.onRTCChange);
        }
    },

    onRTCChange() {
        // (this is told of every change of the voice connection, many a second at times)
        const connected = RTCConnectionStore?.isConnected?.();
        const channelId = connected ? RTCConnectionStore.getChannelId?.() ?? null : null;
        if (recState.isRecording && !connected) {
            // Left call while recording: finalize and close
            stopRecording();
        } else if (connected && channelId !== autoChannel && settings.store.autoRecord && !recState.isRecording && !starting) {
            // Joined a call with autoRecord enabled: tried once for this call, also when it doesn't start
            autoChannel = channelId;
            startRecording();
        }
        if (!connected) autoChannel = null;
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
