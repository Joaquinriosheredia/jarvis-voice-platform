// Proceso principal de Electron: ventana + icono en la bandeja del sistema.
// El WebSocket lo abre el renderer. Cerrar la ventana solo la oculta en la
// bandeja (JARVIS sigue escuchando); "Cerrar JARVIS completamente" en el menú
// de la bandeja termina jarvis.py y la app. Nunca se llama a process.exit.
const { app, BrowserWindow, Tray, Menu } = require('electron');
const { execFile } = require('child_process');
const path = require('path');

// Mismo ID que el acceso directo: icono anclado y ventana comparten botón
const APP_ID   = 'Jarvis.Desktop';
const ICONO    = path.join(__dirname, 'jarvis.ico');
const LANZADOR = path.join(__dirname, 'start.vbs');

let win      = null;
let tray     = null;
let saliendo = false;

app.setAppUserModelId(APP_ID);

function crearVentana() {
  win = new BrowserWindow({
    width: 900,
    height: 650,
    title: 'JARVIS',
    icon: ICONO,
    backgroundColor: '#0b0f17',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  // Si se ancla la ventana abierta, el icono anclado relanza start.vbs (no solo Electron)
  win.setAppDetails({
    appId: APP_ID,
    appIconPath: ICONO,
    relaunchCommand: `wscript.exe "${LANZADOR}"`,
    relaunchDisplayName: 'JARVIS',
  });
  win.loadFile(path.join(__dirname, 'index.html'));

  win.on('close', (e) => {
    if (!saliendo) {
      e.preventDefault();
      win.hide();
    }
  });
}

function mostrar() {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

// Termina jarvis.py a la fuerza: su atexit no se ejecuta, pero memoria.json
// se guarda en cada cambio. -EncodedCommand evita problemas de comillas.
function cerrarJarvis(callback) {
  const ps = "Get-CimInstance Win32_Process -Filter \"Name='python.exe' AND CommandLine LIKE '%jarvis.py%'\" " +
             '| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }';
  execFile('powershell.exe',
    ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(ps, 'utf16le').toString('base64')],
    { windowsHide: true, timeout: 15000 },
    () => callback());
}

function cerrarTodo() {
  saliendo = true;
  cerrarJarvis(() => app.quit());
}

function crearTray() {
  tray = new Tray(ICONO);
  tray.setToolTip('JARVIS');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Mostrar JARVIS', click: mostrar },
    { type: 'separator' },
    { label: 'Cerrar JARVIS completamente', click: cerrarTodo },
  ]));
  tray.on('click', mostrar);
}

// Una sola ventana: un segundo clic en el icono muestra la existente
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', mostrar);
  app.whenReady().then(() => {
    crearVentana();
    crearTray();
  });
}

// Apagado/cierre de sesión de Windows: no bloquear el cierre de la ventana
app.on('before-quit', () => { saliendo = true; });

app.on('window-all-closed', () => app.quit());
