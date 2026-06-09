const { app, BrowserWindow, ipcMain, shell } = require('electron');
const { execFile } = require('node:child_process');
const path = require('node:path');

const DEV_URL = process.env.RESUME_INTEL_UI_URL || 'http://127.0.0.1:5177';

function createWindow() {
  const win = new BrowserWindow({
    width: 1320,
    height: 900,
    minWidth: 980,
    minHeight: 680,
    title: 'Resume Intel',
    backgroundColor: '#eef2f7',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  win.loadURL(DEV_URL);
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

function runJxa(script) {
  return new Promise((resolve, reject) => {
    execFile('/usr/bin/osascript', ['-l', 'JavaScript', '-e', script], { maxBuffer: 10 * 1024 * 1024 }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(stderr || error.message));
        return;
      }
      resolve(stdout.trim());
    });
  });
}

async function readSelectedMailMessages() {
  const script = `
    function safeRead(fn, fallback) {
      try {
        var value = fn();
        if (value === undefined || value === null) return fallback;
        return String(value);
      } catch (error) {
        return fallback;
      }
    }

    var Mail = Application('Mail');
    var selection = Mail.selection();
    var result = [];

    for (var i = 0; i < selection.length; i++) {
      var message = selection[i];
      var subject = safeRead(function () { return message.subject(); }, '');
      var sender = safeRead(function () { return message.sender(); }, '');
      var sentAt = safeRead(function () { return message.dateReceived(); }, '');
      var content = safeRead(function () { return message.content(); }, '');
      var rawSource = safeRead(function () { return message.source(); }, '');
      var id = safeRead(function () { return message.id(); }, String(i + 1));

      result.push({
        subject: subject,
        sender: sender,
        sent_at: sentAt,
        body: rawSource || content,
        raw_filename: 'apple-mail-' + id + (rawSource ? '.eml' : '.txt')
      });
    }

    JSON.stringify(result);
  `;

  const stdout = await runJxa(script);
  if (!stdout) return [];
  return JSON.parse(stdout);
}

ipcMain.handle('mail:read-selected', async () => {
  const messages = await readSelectedMailMessages();
  return { messages };
});

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
