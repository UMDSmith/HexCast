/* Hexcast Avatars - Spout output, the fast engine (an Electron app Hexcast starts; never shown).
 *
 * It draws one overlay page off screen, on the graphics card, and instead of copying each frame out of the browser it
 * hands Hexcast the frame's texture itself: a line per frame, over a socket on this PC that Hexcast listens on -
 *     {"t": "frame", "id": 7, "handle": "1234", "w": 1920, "h": 1080, "format": "bgra"}
 * (handle: the texture's Windows handle in THIS process; Hexcast copies it into its own process, copies the picture into
 * the texture Spout shares - on the graphics card - and answers "7\n", which lets go of the frame here).
 * Frames that come while Hexcast still has one are dropped. Other lines: {"t": "ready"}, {"t": "error", "message"}.
 *
 *     HEXCAST_SPOUT_URL=http://127.0.0.1:4747/avatar/overlay?via=spout  HEXCAST_SPOUT_PORT=<the socket>
 *     electron.exe <this folder> --fps=60 [--mute]
 * (the address comes in the environment: Electron stops when an argument looks like a URL)
 */
'use strict';
const { app, BrowserWindow } = require('electron');
const net = require('net');
const readline = require('readline');

const arg = (name, d) => { const a = process.argv.find((x) => x.startsWith('--' + name + '=')); return a ? a.slice(name.length + 3) : d; };
const url = process.env.HEXCAST_SPOUT_URL || '', fps = Math.max(1, Math.min(240, parseInt(arg('fps', '60'), 10) || 60));
const mute = process.argv.includes('--mute');
// the line to Hexcast: a socket on this PC (Hexcast listens; an Electron app on Windows has no usable stdin)
const link = net.connect({ host: '127.0.0.1', port: parseInt(process.env.HEXCAST_SPOUT_PORT || '0', 10) });
link.on('error', () => app.exit(2));
link.on('close', () => app.exit(0));                   // Hexcast is gone: so is this
const say = (o) => { try { link.write(JSON.stringify(o) + '\n'); } catch (e) {} };

app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
app.commandLine.appendSwitch('disable-background-timer-throttling');
app.commandLine.appendSwitch('disable-renderer-backgrounding');
app.commandLine.appendSwitch('ignore-gpu-blocklist');
app.commandLine.appendSwitch('force-device-scale-factor', '1');

let held = new Map(), nextId = 1;                      // frames Hexcast has not answered yet: id -> texture
const MAX_HELD = 2;

readline.createInterface({ input: link }).on('line', (line) => {
  const id = parseInt(line, 10), tex = held.get(id);
  if (tex) { held.delete(id); try { tex.release(); } catch (e) {} }
});

app.whenReady().then(() => {
  if (!url) { say({ t: 'error', message: 'no HEXCAST_SPOUT_URL' }); app.quit(); return; }
  const win = new BrowserWindow({
    width: 1920, height: 1080, show: false, transparent: true, frame: false, useContentSize: true,
    webPreferences: { offscreen: { useSharedTexture: true }, backgroundThrottling: false, contextIsolation: true, sandbox: true },
  });
  const wc = win.webContents;
  if (mute) wc.setAudioMuted(true);
  wc.setFrameRate(fps);
  wc.on('paint', (event) => {
    const tex = event.texture;
    if (!tex) return;                                  // (a software frame: the graphics card is not drawing this)
    const info = tex.textureInfo, h = info.handle && info.handle.ntHandle;
    if (!h || held.size >= MAX_HELD || (info.widgetType && info.widgetType !== 'frame')) { try { tex.release(); } catch (e) {} return; }
    const id = nextId++;
    held.set(id, tex);
    const handle = h.length >= 8 ? h.readBigUInt64LE(0).toString() : String(h.readUInt32LE(0));
    say({ t: 'frame', id, handle, w: info.codedSize.width, h: info.codedSize.height, format: info.pixelFormat });
  });
  wc.on('render-process-gone', (e, d) => { say({ t: 'error', message: 'the page stopped: ' + d.reason }); app.exit(3); });
  wc.on('did-fail-load', (e, code, desc) => { say({ t: 'error', message: 'could not load the overlay: ' + desc }); setTimeout(() => wc.loadURL(url), 3000); });
  wc.on('did-finish-load', () => say({ t: 'ready' }));
  wc.loadURL(url);
});
app.on('window-all-closed', () => app.quit());
