// Puente main ↔ renderer: el atajo global para callar a JARVIS y el cierre desde la
// bandeja (el renderer contesta si pudo pedirlo por el WebSocket).
// No expone ipcRenderer ni nada de Node a la página.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvis', {
  alAtajoParar: (callback) => {
    ipcRenderer.on('parar', () => callback());
  },
  alCerrar: (callback) => {
    ipcRenderer.on('cerrar', () => ipcRenderer.send('cierre-pedido', callback() === true));
  },
});
