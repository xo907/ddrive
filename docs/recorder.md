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
3. Get the **Server URL** and **Recorder Key**. Where the recordings should go decides where they come from:
   - **Your end-to-end encrypted account** (an account or admin account on a DDrive dashboard): through DDrive Lite on this computer,
     signed in to that account (`lite connect`). The host can't put anything into such an account, so Lite seals the recording here,
     a piece at a time, and sends it on as it is made.
     An **admin account** can make the key in the dashboard: the account menu > **Account settings** > **Discord** > **Call recorder for Discord** >
     **Make a key**. It shows the key once, with the Server URL (`http://127.0.0.1:8766`); the host keeps only a hash of it and hands
     that to every DDrive Lite signed in to the account (3.24.0 or newer), which takes it within about 15 seconds and starts taking
     recordings. **New key** replaces it, **Turn off** stops it. Or, in a terminal, in the DDrive folder:
     ```cmd
     run.bat lite recorder on
     ```
     It shows the Server URL (`http://127.0.0.1:8766`, this computer only) and the key, and starts it. It runs while Lite runs (with the
     drive letter, folder syncing, or by itself); `lite autostart on` has it start with the computer. `lite recorder new` makes a new key,
     `lite recorder off` turns it off. The host has to be DDrive 3.20.0 or newer.
   - **The owner's drive**: the Server URL is the drive's dashboard address (`http://127.0.0.1:8765` on the computer that runs it),
     and the key is in the dashboard under **Settings** > **Advanced** > **Live upload** (**Make a key**, then **Copy**), or in a terminal on that
     computer: `run.bat recorder-key` (`./run.sh recorder-key` on Linux or a Pi).

   Put both into the plugin's settings, or give them to the installer: `Install-Recorder.cmd -ServerUrl http://127.0.0.1:8766 -RecorderKey <key>`.
4. Recordings go to `/Recordings/<folder>` (the plugin's folder setting, `Discord Calls` unless changed).
5. If the **Record** button doesn't show in the voice panel, use the Vencord toolbox (top right of Discord): **DDriveRecorder** > **Start / stop DDrive recording**.

### 3. Record a Call
1. Join any voice channel, DM, or Group Call.
2. In the bottom-left Voice connection area, click the **Record** button (or red circle).
3. The button turns red with an animated pulse: `🔴 [00:01] Screen | 320 KB`.
4. Minimize Discord or switch windows — recording continues uninterrupted.
5. Click the button again to stop. DDrive will finalize the recording under `/Recordings/Discord Calls/`.
