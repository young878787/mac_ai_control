// 聊天室視窗的預載腳本（sandbox 內只能用 CommonJS）：只交出聊天室需要的幾個動作。
// 刻意不提供 menu、拖曳、setIgnore、choose、command 以外的訊息；送出的文字由主程序驗證後才轉給後端。
const { contextBridge, ipcRenderer } = require('electron');

const on = (channel) => (cb) => { ipcRenderer.on(channel, (_e, payload) => cb(payload)); };
contextBridge.exposeInMainWorld('chat', {
  ready: () => ipcRenderer.send('chat:ready'),
  send: (text) => ipcRenderer.send('chat:send', String(text)),
  close: () => ipcRenderer.send('chat:close'),
  interact: () => ipcRenderer.send('chat:interact'),
  focus: () => ipcRenderer.send('chat:focus'),
  hidden: () => ipcRenderer.send('chat:hidden'),
  painted: (ms) => ipcRenderer.send('chat:painted', Number(ms)),
  onState: on('chat-state'),
  onLive: on('chat-live'),
  onShow: on('chat-show'),
  onHide: on('chat-hide'),
});
