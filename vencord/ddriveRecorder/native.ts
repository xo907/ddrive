/*
 * DDrive Call & Screenshare Recorder for Vencord
 * Live-streams call audio and screenshare video straight to DDrive in memory
 * Zero disk usage, no background pauses when minimized
 */

import { ChildProcess, execFile, spawn } from "child_process";
import { app, BrowserWindow, desktopCapturer, IpcMainInvokeEvent, powerSaveBlocker } from "electron";
import fs from "fs";
import http from "http";
import https from "https";
import path from "path";
import { URL } from "url";

const httpAgent = new http.Agent({ keepAlive: true, maxSockets: 10 });
const httpsAgent = new https.Agent({ keepAlive: true, maxSockets: 10 });

let blockerId: number | null = null;

function request(urlStr: string, options: {
    method?: string;
    headers?: Record<string, string | number>;
    body?: Buffer | Uint8Array | string;
    timeout?: number;
}): Promise<{ status: number; data: string; json: any }> {
    return new Promise((resolve, reject) => {
        try {
            const parsed = new URL(urlStr);
            const isHttps = parsed.protocol === "https:";
            const client = isHttps ? https : http;
            const agent = isHttps ? httpsAgent : httpAgent;

            const reqOptions: http.RequestOptions = {
                hostname: parsed.hostname,
                port: parsed.port || (isHttps ? 443 : 80),
                path: parsed.pathname + parsed.search,
                method: options.method || "GET",
                headers: options.headers || {},
                agent,
                timeout: options.timeout || 30000
            };

            const req = client.request(reqOptions, res => {
                const chunks: Buffer[] = [];
                res.on("data", c => chunks.push(c));
                res.on("end", () => {
                    const text = Buffer.concat(chunks).toString("utf8");
                    let json = null;
                    try {
                        json = JSON.parse(text);
                    } catch (_) {}
                    resolve({ status: res.statusCode || 0, data: text, json });
                });
            });

            req.on("error", err => reject(err));
            req.on("timeout", () => {
                req.destroy();
                reject(new Error("Request timed out"));
            });

            if (options.body) {
                if (options.body instanceof Uint8Array) {
                    req.write(Buffer.from(options.body.buffer, options.body.byteOffset, options.body.byteLength));
                } else {
                    req.write(options.body);
                }
            }
            req.end();
        } catch (e) {
            reject(e);
        }
    });
}

/**
 * Configure Electron window background throttling and OS power suspension
 * When active, Discord windows will NEVER throttle timers, media, or video decoding when minimized
 */
export async function setRecordingActive(_: IpcMainInvokeEvent, active: boolean): Promise<boolean> {
    try {
        for (const win of BrowserWindow.getAllWindows()) {
            if (win && !win.isDestroyed()) {
                // Disable background throttling so Chromium doesn't throttle or freeze media when minimized
                win.webContents.setBackgroundThrottling(!active);
            }
        }
        if (active) {
            if (blockerId === null || !powerSaveBlocker.isStarted(blockerId)) {
                blockerId = powerSaveBlocker.start("prevent-app-suspension");
            }
        } else {
            if (blockerId !== null && powerSaveBlocker.isStarted(blockerId)) {
                powerSaveBlocker.stop(blockerId);
                blockerId = null;
            }
        }
        return true;
    } catch (e) {
        console.error("[DDriveRecorder Native] setRecordingActive error:", e);
        return false;
    }
}

/**
 * Get screen sources for desktop loopback audio capture
 */
export async function getDesktopSources(_: IpcMainInvokeEvent): Promise<Array<{ id: string; name: string }>> {
    try {
        const sources = await desktopCapturer.getSources({ types: ["screen"] });
        return sources.map(s => ({ id: s.id, name: s.name }));
    } catch (e) {
        console.error("[DDriveRecorder Native] getDesktopSources error:", e);
        return [];
    }
}

// ------------------------------------------------------------- Discord's own sound (Windows process loopback)
// A small helper, built here the first time with the C# compiler Windows has, takes what Discord's
// processes play (and nothing of other programs) and writes it out as 48 kHz 16-bit stereo; the page
// fetches it every 40 ms (tapRead) and plays it into the recording.
const TAP_SOURCE = String.raw`// ddrive-audiotap: the sound one program (and the programs it started) plays, nothing else, written to
// standard output as raw PCM: 48000 Hz, 2 channels, 16-bit little-endian. Windows 10 2004 or newer
// (process loopback). Usage: ddrive-audiotap <process id>. Ends when its output is closed.
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

static class AudioTap
{
    [ComImport, Guid("41D949AB-9862-444A-80F6-C261334DA5EB"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IActivateAudioInterfaceCompletionHandler
    {
        void ActivateCompleted(IActivateAudioInterfaceAsyncOperation op);
    }

    [ComImport, Guid("72A22D78-CDE4-431D-B8CC-843A71199B6D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IActivateAudioInterfaceAsyncOperation
    {
        void GetActivateResult(out int hr, [MarshalAs(UnmanagedType.IUnknown)] out object iface);
    }

    [ComImport, Guid("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAgileObject { }

    [ComImport, Guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioClient
    {
        void Initialize(int shareMode, int streamFlags, long bufferDuration, long periodicity, IntPtr format, IntPtr sessionGuid);
        void GetBufferSize(out uint frames);
        void GetStreamLatency(out long latency);
        void GetCurrentPadding(out uint frames);
        void IsFormatSupported(int shareMode, IntPtr format, out IntPtr closest);
        void GetMixFormat(out IntPtr format);
        void GetDevicePeriod(out long defaultPeriod, out long minimumPeriod);
        void Start();
        void Stop();
        void Reset();
        void SetEventHandle(IntPtr handle);
        void GetService(ref Guid iid, [MarshalAs(UnmanagedType.IUnknown)] out object service);
    }

    [ComImport, Guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioCaptureClient
    {
        void GetBuffer(out IntPtr data, out uint frames, out uint flags, out ulong devicePosition, out ulong qpcPosition);
        void ReleaseBuffer(uint frames);
        void GetNextPacketSize(out uint frames);
    }

    class Done : IActivateAudioInterfaceCompletionHandler, IAgileObject
    {
        public readonly ManualResetEvent Event = new ManualResetEvent(false);
        public IActivateAudioInterfaceAsyncOperation Op;
        public void ActivateCompleted(IActivateAudioInterfaceAsyncOperation op) { Op = op; Event.Set(); }
    }

    [DllImport("Mmdevapi.dll", ExactSpelling = true, PreserveSig = false)]
    static extern void ActivateAudioInterfaceAsync([MarshalAs(UnmanagedType.LPWStr)] string path, ref Guid iid,
        IntPtr activationParams, IActivateAudioInterfaceCompletionHandler handler, out IActivateAudioInterfaceAsyncOperation op);

    const int RATE = 48000, CHANNELS = 2, BYTES = 4;   // a frame: 2 channels of 16 bits

    [MTAThread]
    static int Main(string[] args)
    {
        int pid;
        if (args.Length < 1 || !int.TryParse(args[0], out pid))
        {
            Console.Error.WriteLine("usage: ddrive-audiotap <process id>");
            return 2;
        }
        try
        {
            // AUDIOCLIENT_ACTIVATION_PARAMS: process loopback of the process and its children
            IntPtr prm = Marshal.AllocHGlobal(12);
            Marshal.WriteInt32(prm, 0, 1);       // AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
            Marshal.WriteInt32(prm, 4, pid);
            Marshal.WriteInt32(prm, 8, 0);       // PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE
            // PROPVARIANT of VT_BLOB holding it
            IntPtr pv = Marshal.AllocHGlobal(24);
            for (int i = 0; i < 24; i++) Marshal.WriteByte(pv, i, 0);
            Marshal.WriteInt16(pv, 0, 65);       // VT_BLOB
            Marshal.WriteInt32(pv, 8, 12);
            Marshal.WriteIntPtr(pv, IntPtr.Size == 8 ? 16 : 12, prm);

            Guid iidClient = typeof(IAudioClient).GUID;
            Done done = new Done();
            IActivateAudioInterfaceAsyncOperation op;
            ActivateAudioInterfaceAsync("VAD\\Process_Loopback", ref iidClient, pv, done, out op);
            done.Event.WaitOne();
            int hr;
            object o;
            done.Op.GetActivateResult(out hr, out o);
            if (hr != 0) throw new COMException("activation failed", hr);
            IAudioClient client = (IAudioClient)o;

            IntPtr fmt = Marshal.AllocHGlobal(18);   // WAVEFORMATEX, PCM
            Marshal.WriteInt16(fmt, 0, 1);
            Marshal.WriteInt16(fmt, 2, CHANNELS);
            Marshal.WriteInt32(fmt, 4, RATE);
            Marshal.WriteInt32(fmt, 8, RATE * BYTES);
            Marshal.WriteInt16(fmt, 12, BYTES);
            Marshal.WriteInt16(fmt, 14, 16);
            Marshal.WriteInt16(fmt, 16, 0);
            const int LOOPBACK = 0x00020000, EVENTCALLBACK = 0x00040000, AUTOCONVERTPCM = unchecked((int)0x80000000), SRC_QUALITY = 0x08000000;
            client.Initialize(0, LOOPBACK | EVENTCALLBACK | AUTOCONVERTPCM | SRC_QUALITY, 2000000, 0, fmt, IntPtr.Zero);
            AutoResetEvent ready = new AutoResetEvent(false);
            client.SetEventHandle(ready.SafeWaitHandle.DangerousGetHandle());
            Guid iidCapture = typeof(IAudioCaptureClient).GUID;
            object svc;
            client.GetService(ref iidCapture, out svc);
            IAudioCaptureClient capture = (IAudioCaptureClient)svc;
            client.Start();

            Stream output = Console.OpenStandardOutput();
            byte[] buf = new byte[RATE * BYTES];
            while (true)
            {
                ready.WaitOne(100);
                uint next;
                capture.GetNextPacketSize(out next);
                while (next > 0)
                {
                    IntPtr data;
                    uint frames, flags;
                    ulong a, b;
                    capture.GetBuffer(out data, out frames, out flags, out a, out b);
                    int n = (int)frames * BYTES;
                    if (n > buf.Length) buf = new byte[n];
                    if ((flags & 2) != 0) Array.Clear(buf, 0, n);    // AUDCLNT_BUFFERFLAGS_SILENT
                    else Marshal.Copy(data, buf, 0, n);
                    capture.ReleaseBuffer(frames);
                    output.Write(buf, 0, n);                         // (a closed output ends it: IOException)
                    capture.GetNextPacketSize(out next);
                }
                output.Flush();
            }
        }
        catch (IOException)
        {
            return 0;
        }
        catch (Exception e)
        {
            Console.Error.WriteLine(e.GetType().Name + ": " + e.Message + (e is COMException ? " (0x" + ((COMException)e).ErrorCode.ToString("X8") + ")" : ""));
            return 1;
        }
    }
}
`;
const TAP_VERSION = 1;
let tap: ChildProcess | null = null;
let tapChunks: Buffer[] = [];
let tapError = "";

function buildTap(): Promise<string> {
    const dir = path.join(app.getPath("userData"), "DDriveRecorder");
    const exe = path.join(dir, `ddrive-audiotap-${TAP_VERSION}.exe`);
    if (fs.existsSync(exe)) return Promise.resolve(exe);
    fs.mkdirSync(dir, { recursive: true });
    const src = path.join(dir, "ddrive-audiotap.cs");
    fs.writeFileSync(src, TAP_SOURCE);
    const csc = path.join(process.env.WINDIR || "C:\\Windows", "Microsoft.NET", "Framework64", "v4.0.30319", "csc.exe");
    return new Promise((resolve, reject) => {
        execFile(csc, ["-nologo", "-optimize", "-platform:x64", "-target:exe", `-out:${exe}`, src], { windowsHide: true }, (err, stdout) => {
            if (err || !fs.existsSync(exe)) reject(new Error(`the sound helper couldn't be built: ${String(stdout || err?.message).trim().slice(0, 300)}`));
            else resolve(exe);
        });
    });
}

export async function tapStart(_: IpcMainInvokeEvent): Promise<{ ok: boolean; error?: string; }> {
    if (process.platform !== "win32") return { ok: false, error: "only on Windows" };
    try {
        const exe = await buildTap();
        tap?.kill();
        tapChunks = [];
        tapError = "";
        const p = spawn(exe, [String(process.pid)], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
        tap = p;
        p.stdout!.on("data", (b: Buffer) => { if (tap === p) tapChunks.push(b); });
        p.stderr!.on("data", (b: Buffer) => { tapError += b.toString(); });
        p.on("error", e => { tapError ||= e.message; });
        // it fails at once when Windows can't do it (older than Windows 10 2004): see that before saying yes
        await new Promise(r => setTimeout(r, 400));
        if (p.exitCode !== null) return { ok: false, error: tapError.trim() || `the sound helper ended (${p.exitCode})` };
        return { ok: true };
    } catch (e: any) {
        return { ok: false, error: e?.message || String(e) };
    }
}

export async function tapRead(_: IpcMainInvokeEvent): Promise<{ data: string; alive: boolean; error?: string; }> {
    const data = tapChunks.length ? Buffer.concat(tapChunks).toString("base64") : "";
    tapChunks = [];
    return { data, alive: !!tap && tap.exitCode === null, error: tapError.trim() || undefined };
}

export async function tapStop(_: IpcMainInvokeEvent): Promise<void> {
    tap?.kill();
    tap = null;
    tapChunks = [];
}

// ------------------------------------------------------------- The copy on this computer
// Written as the recording is made, so that it is all here when DDrive (Lite, or the host behind it)
// can't take it; removed at the end when DDrive got all of it (unless a copy is always wanted).
const localFiles = new Map<string, { fd: number; path: string; }>();

export async function localOpen(_: IpcMainInvokeEvent, opts: { folder: string; name: string; }): Promise<{ ok: boolean; id?: string; path?: string; error?: string; }> {
    try {
        const dir = opts.folder?.trim() || path.join(app.getPath("videos"), "DDrive Recordings");
        fs.mkdirSync(dir, { recursive: true });
        const ext = path.extname(opts.name), stem = opts.name.slice(0, opts.name.length - ext.length);
        let file = path.join(dir, opts.name);
        for (let n = 2; fs.existsSync(file); n++) file = path.join(dir, `${stem} (${n})${ext}`);
        const fd = fs.openSync(file, "wx");
        const id = Math.random().toString(36).slice(2) + Date.now().toString(36);
        localFiles.set(id, { fd, path: file });
        return { ok: true, id, path: file };
    } catch (e: any) {
        return { ok: false, error: e?.message || String(e) };
    }
}

export async function localWrite(_: IpcMainInvokeEvent, opts: { id: string; chunk64: string; }): Promise<{ ok: boolean; error?: string; }> {
    const f = localFiles.get(opts.id);
    if (!f) return { ok: false, error: "the file isn't open" };
    try {
        fs.writeSync(f.fd, Buffer.from(opts.chunk64, "base64"));
        return { ok: true };
    } catch (e: any) {
        return { ok: false, error: e?.message || String(e) };
    }
}

export async function localClose(_: IpcMainInvokeEvent, opts: { id: string; keep: boolean; }): Promise<{ kept: boolean; path?: string; }> {
    const f = localFiles.get(opts.id);
    if (!f) return { kept: false };
    localFiles.delete(opts.id);
    try { fs.closeSync(f.fd); } catch { }
    if (opts.keep) return { kept: true, path: f.path };
    try { fs.unlinkSync(f.path); } catch { }
    return { kept: false };
}

/**
 * Open a live recording stream on DDrive (/api/rec/open)
 */
export async function openLiveRecording(_: IpcMainInvokeEvent, opts: {
    serverUrl: string;
    recorderKey: string;
    folder: string;
    name: string;
    mime: string;
}): Promise<{ ok: boolean; id?: string; path?: string; error?: string }> {
    try {
        const base = opts.serverUrl.replace(/\/+$/, "");
        const url = `${base}/api/rec/open`;
        const bodyStr = JSON.stringify({
            folder: opts.folder || "Discord Calls",
            name: opts.name,
            mime: opts.mime || ""
        });
        const res = await request(url, {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${opts.recorderKey}`,
                "Content-Type": "application/json",
                "Content-Length": Buffer.byteLength(bodyStr)
            },
            body: bodyStr
        });
        if (res.status === 200 && res.json?.id) {
            return { ok: true, id: res.json.id, path: res.json.path };
        }
        const errMsg = res.json?.error || res.data || `HTTP ${res.status}`;
        return { ok: false, error: errMsg };
    } catch (e: any) {
        return {
            ok: false,
            error: `nothing answers at ${opts.serverUrl} (${e?.message || e}). For DDrive Lite, run: run.bat lite recorder on`
        };
    }
}

/**
 * Send one part to DDrive (/api/rec/data), once. The page decides what to do with the answer:
 * ok -> DDrive has everything up to `offset`; not ok with `wait` -> send it again in a moment;
 * not ok with an `offset` -> DDrive has up to there, go on from it; status 0 -> DDrive couldn't be reached.
 */
export async function sendLiveChunk(_: IpcMainInvokeEvent, opts: {
    serverUrl: string;
    recorderKey: string;
    id: string;
    offset: number;
    chunk64?: string; // the part as base64 (what the plugin sends)
    chunk?: Uint8Array | ArrayBuffer;
}): Promise<{ ok: boolean; status: number; offset?: number; wait?: string; error?: string; sent?: number }> {
    try {
        const base = opts.serverUrl.replace(/\/+$/, "");
        const url = `${base}/api/rec/data?id=${encodeURIComponent(opts.id)}&offset=${opts.offset}`;
        const c: any = opts.chunk;
        const buffer = typeof opts.chunk64 === "string" ? Buffer.from(opts.chunk64, "base64")
            : c instanceof ArrayBuffer ? Buffer.from(c)
                : ArrayBuffer.isView(c) ? Buffer.from(c.buffer, c.byteOffset, c.byteLength)
                    : Buffer.from(Object.values(c ?? {}) as number[]);
        const res = await request(url, {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${opts.recorderKey}`,
                "Content-Type": "application/octet-stream",
                "Content-Length": buffer.length
            },
            body: buffer,
            timeout: 60000
        });
        if (res.status === 200 && res.json) {
            return { ok: !!res.json.ok, status: 200, offset: res.json.offset, wait: res.json.wait, sent: buffer.length };
        }
        return { ok: false, status: res.status, error: res.json?.error || res.data || `HTTP ${res.status}` };
    } catch (e: any) {
        return { ok: false, status: 0, error: e?.message || String(e) };
    }
}

/**
 * Finalize live recording on DDrive (/api/rec/close)
 */
export async function closeLiveRecording(_: IpcMainInvokeEvent, opts: {
    serverUrl: string;
    recorderKey: string;
    id: string;
    offset: number;
    wait?: number;
}): Promise<{ ok: boolean; path?: string; size?: number; done?: boolean; error?: string }> {
    try {
        const waitTime = opts.wait ?? 30;
        const base = opts.serverUrl.replace(/\/+$/, "");
        const url = `${base}/api/rec/close?id=${encodeURIComponent(opts.id)}&offset=${opts.offset}&wait=${waitTime}`;
        const res = await request(url, {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${opts.recorderKey}`,
                "Content-Length": 0
            },
            timeout: (waitTime + 10) * 1000
        });
        if (res.status === 200 && res.json) {
            return {
                ok: !!res.json.ok,
                path: res.json.path,
                size: res.json.size,
                done: !!res.json.done,
                error: res.json.error
            };
        }
        return { ok: false, error: res.json?.error || res.data || `HTTP ${res.status}` };
    } catch (e: any) {
        return { ok: false, error: e?.message || String(e) };
    }
}
