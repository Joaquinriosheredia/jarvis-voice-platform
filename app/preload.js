// Único puente main → renderer: avisa del atajo global para callar a JARVIS.
// No expone ipcRenderer ni nada de Node a la página.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvis', {
  alAtajoParar: (callback) => {
    ipcRenderer.on('parar', () => callback());
  },
});
