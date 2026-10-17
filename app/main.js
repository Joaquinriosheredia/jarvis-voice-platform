// Proceso principal de Electron: ventana + icono en la bandeja del sistema.
// El WebSocket lo abre el renderer. Cerrar la ventana solo la oculta en la
// bandeja (JARVIS sigue escuchando); "Cerrar JARVIS completamente" en el menú
// de la bandeja pide a jarvis.py que guarde y termine (WebSocket "cerrar"), lo fuerza
// si no ha terminado a tiempo y cierra la app. Nunca se llama a process.exit.
const { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain } = require('electron');
const { execFile } = require('child_process');
const path = require('path');

// Mismo ID que el acceso directo: icono anclado y ventana comparten botón
const APP_ID   = 'Jarvis.Desktop';
const ICONO    = path.join(__dirname, 'jarvis.ico');
const LANZADOR = path.join(__dirname, 'start.vbs');
// Callar a JARVIS desde cualquier ventana (el renderer envía "stop" por el WebSocket)
const ATAJO_PARAR = 'Control+Alt+P';
// Segundos que se espera a que jarvis.py guarde la sesión (con resumen) antes de forzar
const ESPERA_CIERRE_S = 20;

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
      preload: path.join(__dirname, 'preload.js'),
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

// Espera hasta `espera` segundos a que jarvis.py termine solo y, si no, lo termina a la
// fuerza (sin atexit: lo no guardado se pierde, salvo el último guardado periódico).
// -EncodedCommand evita problemas de comillas.
function cerrarJarvis(espera, callback) {
  const esperar = espera > 0 ? `Wait-Process -Id $id -Timeout ${espera} -ErrorAction Stop; ` : 'throw "forzar"; ';
  const ps = "Get-CimInstance Win32_Process -Filter \"Name='python.exe' AND CommandLine LIKE '%jarvis.py%'\" " +
             '| ForEach-Object { $id = $_.ProcessId; try { ' + esperar + '} ' +
             'catch { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue } }';
  execFile('powershell.exe',
    ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(ps, 'utf16le').toString('base64')],
    { windowsHide: true, timeout: (espera + 15) * 1000 },
    () => callback());
}

// El renderer envía "cerrar" por el WebSocket y responde si pudo enviarlo. Sin ventana,
// sin conexión o sin respuesta en 1 s: se fuerza como antes.
function cerrarTodo() {
  saliendo = true;
  let hecho = false;
  const seguir = (enviado) => {
    if (hecho) return;
    hecho = true;
    cerrarJarvis(enviado ? ESPERA_CIERRE_S : 0, () => app.quit());
  };
  ipcMain.once('cierre-pedido', (_e, enviado) => seguir(enviado === true));
  setTimeout(() => seguir(false), 1000);
  if (win) win.webContents.send('cerrar');
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
    if (!globalShortcut.register(ATAJO_PARAR, () => win && win.webContents.send('parar'))) {
      console.warn(`[JARVIS] no se pudo registrar el atajo ${ATAJO_PARAR}`);
    }
  });
  app.on('will-quit', () => globalShortcut.unregisterAll());
}

// Apagado/cierre de sesión de Windows: no bloquear el cierre de la ventana
app.on('before-quit', () => { saliendo = true; });

app.on('window-all-closed', () => app.quit());
