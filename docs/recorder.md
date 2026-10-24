# DDrive Call & Screenshare Recorder (Vencord Plugin)

A Telegram-style call recording extension for Discord that live-streams call audio and active screenshares directly into **DDrive** in memory.

---

## Key Features

1. **Telegram-Style Call Recording**:
   - One-click record/stop button integrated directly into Discord's Voice Control panel (`RTCConnectionMenu`).
   - If anyone in the call is sharing their screen, it records the screenshare video along with mixed voice audio.
   - If no one is sharing screen, it records crystal-clear voice audio (`audio/webm;codecs=opus`).
   - Displays live recording status, duration timer (`🔴 REC 00:42`), and live uploaded bytes count.

2. **Zero Disk Usage on User Device**:
   - Chunks are held exclusively in memory buffer (`ArrayBuffer`).
   - As each piece is encoded by `MediaRecorder`, it is streamed via HTTP POST directly to `/api/rec/data` in DDrive.
   - Once received by DDrive, it is encrypted and staged to Discord backend channels in 9 MiB pieces without touching local disk.
   - Zero temporary files, zero cache files, zero local storage used.

3. **Window Minimization & Background Keep-Alive**:
   - Normally, Discord and Chromium suspend remote video sinks (`setHasActiveVideoOutputSink`) and throttle background renderers when the window is minimized or hidden.
   - The plugin neutralizes this at 3 levels:
     - **Electron Main Process (`native.ts`)**: Invokes `webContents.setBackgroundThrottling(false)` on all Discord windows and starts `powerSaveBlocker` to prevent system suspension.
     - **DOM Visibility Layer**: Overrides `document.hidden` (forced `false`), `document.visibilityState` (forced `'visible'`), and suppresses `visibilitychange` events so Discord never knows the window is minimized.
     - **Media Engine Hook**: Prevents Discord's `MediaEngineConnection` from deactivating active video sinks when hidden, and blocks pauses on screenshare video elements.

4. **One-Click CMD Injection**:
   - Double-click or run `Install-Recorder.cmd` from command prompt.
   - Automatically detects Vencord, copies the plugin, builds Vencord with `pnpm build`, injects it into Discord, and enables `DDriveRecorder` in settings.

---

## Quick Start / Installation via CMD

### 1. Injected via CMD
Open Command Prompt in the repository folder and run:
```cmd
Install-Recorder.cmd
```
*(Or with automatic approval: `Install-Recorder.cmd -Yes`)*

To uninstall or remove:
```cmd
Install-Recorder.cmd -Uninstall
```

### 2. Configure in Discord
1. Open Discord **User Settings** -> scroll to **Vencord** -> **Plugins**.
2. Find **DDriveRecorder** (or search "DDrive").
3. Set your **DDrive Server URL** (e.g. `http://127.0.0.1:8080`).
4. Set your **Recorder Key** (generated in DDrive Dashboard under **Settings** > **Live upload**).

### 3. Record a Call
1. Join any voice channel, DM, or Group Call.
2. In the bottom-left Voice connection area, click the **Record** button (or red circle).
3. The button turns red with an animated pulse: `🔴 [00:01] Screen | 320 KB`.
4. Minimize Discord or switch windows — recording continues uninterrupted.
5. Click the button again to stop. DDrive will finalize the recording under `/Recordings/Discord Calls/`.
