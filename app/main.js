// Proceso principal de Electron: solo abre la ventana.
// El WebSocket lo abre el renderer; cerrar la ventana no afecta a JARVIS
// (es otro proceso), y aquí no se llama nunca a process.exit.
const { app, BrowserWindow } = require('electron');
const path = require('path');

function crearVentana() {
  const win = new BrowserWindow({
    width: 900,
    height: 650,
    title: 'JARVIS',
    backgroundColor: '#0b0f17',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.loadFile(path.join(__dirname, 'index.html'));
}

app.whenReady().then(crearVentana);

app.on('window-all-closed', () => app.quit());
