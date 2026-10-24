/*
 * DDrive Call & Screenshare Recorder for Vencord
 * Live-streams call audio and screenshare video straight to DDrive in memory
 * Zero disk usage, no background pauses when minimized
 */

import { BrowserWindow, desktopCapturer, IpcMainInvokeEvent, powerSaveBlocker } from "electron";
import http from "http";
import https from "https";
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
        return { ok: false, error: e?.message || String(e) };
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
    chunk: Uint8Array;
}): Promise<{ ok: boolean; status: number; offset?: number; wait?: string; error?: string }> {
    try {
        const base = opts.serverUrl.replace(/\/+$/, "");
        const url = `${base}/api/rec/data?id=${encodeURIComponent(opts.id)}&offset=${opts.offset}`;
        const buffer = Buffer.from(opts.chunk.buffer, opts.chunk.byteOffset, opts.chunk.byteLength);
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
            return { ok: !!res.json.ok, status: 200, offset: res.json.offset, wait: res.json.wait };
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
