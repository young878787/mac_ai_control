// 預載腳本（sandbox 內只能用 CommonJS）：只把畫面需要的幾個動作透過 contextBridge 交出去。
const { contextBridge, ipcRenderer } = require('electron');

const on = (channel) => (cb) => { ipcRenderer.on(channel, (_e, payload) => cb(payload)); };
contextBridge.exposeInMainWorld('desktop', {
  config: () => ipcRenderer.invoke('config'),
  ready: () => ipcRenderer.send('ready'),
  send: (msg) => ipcRenderer.send('send', msg),
  setIgnore: (v) => ipcRenderer.send('set-ignore', !!v),
  gaze: (v) => ipcRenderer.send('gaze', !!v),
  focus: (v) => ipcRenderer.send('focus', !!v),
  applied: (id, at) => ipcRenderer.send('applied', id, at),
  menu: () => ipcRenderer.send('menu'),
  dragStart: () => ipcRenderer.send('drag-start'),
  dragMove: (dx) => ipcRenderer.send('drag-move', dx),
  dragEnd: () => ipcRenderer.send('drag-end'),
  onBus: on('bus'),
  onCursor: on('cursor'),
  onToggleInput: on('toggle-input'),
  onToggleHistory: on('toggle-history'),
});
