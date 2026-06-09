const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('resumeIntelNative', {
  isElectron: true,
  readSelectedMailMessages: () => ipcRenderer.invoke('mail:read-selected'),
});
