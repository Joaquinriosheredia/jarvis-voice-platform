// Cliente WebSocket de JARVIS: muestra lo que envía ui_bridge.py y solo envía "stop" y "cerrar".
// Todo el texto se inserta con textContent (nunca innerHTML).
const URL_WS      = 'ws://127.0.0.1:8765';
const RECONEXION  = 2000;

const ETIQUETAS = {
  listening:      'Escuchando',
  processing:     'Procesando',
  responding:     'Respondiendo',
  idle:           'En espera',
  interrupted:    'Interrumpido',
  not_understood: 'No entendido',
  disconnected:   'Desconectado',
};

const chat        = document.getElementById('chat');
const botonParar  = document.getElementById('parar');
const vacio       = document.getElementById('vacio');
const estado      = document.getElementById('estado');
const estadoTexto = document.getElementById('estado-texto');

function setEstado(state) {
  const valido = Object.hasOwn(ETIQUETAS, state) ? state : 'idle';
  estado.dataset.state    = valido;
  estadoTexto.textContent = ETIQUETAS[valido];
}

function hora(ts) {
  const d = ts ? new Date(ts) : new Date();
  return isNaN(d) ? '' : d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
}

// Heurística conservadora: solo multilínea con varias líneas claramente de código.
// Ante la duda devuelve false y se muestra como burbuja normal.
const LINEA_CODIGO = /^(?:[ \t]+\S|(?:import|from|def|class|public|private|protected|const|var|let|function|return|package)\b|[{}])/;

function esCodigo(texto) {
  const lineas = texto.split('\n').filter((l) => l.trim());
  if (lineas.length < 3) return false;
  const deCodigo = lineas.filter((l) => LINEA_CODIGO.test(l)).length;
  return deCodigo >= 2 && deCodigo / lineas.length >= 0.3;
}

function burbuja(clase, texto, ts) {
  if (typeof texto !== 'string' || !texto) return;
  vacio.hidden = true;
  const div = document.createElement('div');
  div.className = `burbuja ${clase}`;
  if (clase === 'jarvis' && esCodigo(texto)) {
    div.classList.add('con-codigo');
    const pre  = document.createElement('pre');
    const code = document.createElement('code');
    code.textContent = texto;
    pre.appendChild(code);
    div.appendChild(pre);
  } else {
    div.textContent = texto;
  }
  const h = document.createElement('span');
  h.className   = 'hora';
  h.textContent = hora(ts);
  div.appendChild(h);
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

// ── Métricas del turno: línea discreta bajo la última burbuja de JARVIS ──
const FASES = ['whisper', 'router', 'llm', 'tools', 'tts'];

function num(v) {
  return typeof v === 'number' && isFinite(v) ? v : 0;
}

function resumenMetricas(m) {
  const partes = [String(m.ruta || '?'), `${(num(m.total_ms) / 1000).toFixed(1)} s`,
                  `$${num(m.coste_usd).toFixed(4)}`];
  const entrada = num(m.input) + num(m.cache_read) + num(m.cache_creation);
  if (entrada > 0) partes.push(`cache ${Math.round(num(m.cache_read) * 100 / entrada)}%`);
  return partes.join(' · ');
}

function desgloseMetricas(m) {
  const partes = FASES.map((f) => `${f} ${num(m[`${f}_ms`])} ms`);
  if (typeof m.ttft_ms === 'number') partes.push(`1.er token ${m.ttft_ms} ms`);
  if (typeof m.primera_voz_ms === 'number') partes.push(`1.ª voz ${m.primera_voz_ms} ms`);
  return partes.join(' · ');
}

function metricas(m) {
  const burbujas = chat.querySelectorAll('.burbuja.jarvis:not(.parcial)');
  const ultima   = burbujas[burbujas.length - 1];
  if (!ultima) return;
  const siguiente = ultima.nextElementSibling;
  if (siguiente && siguiente.classList.contains('metricas')) siguiente.remove();

  const div = document.createElement('div');
  div.className = 'metricas';
  const linea = document.createElement('span');
  linea.className   = 'metricas-linea';
  linea.textContent = resumenMetricas(m);
  const detalle = document.createElement('div');
  detalle.className   = 'metricas-detalle';
  detalle.hidden      = true;
  detalle.textContent = desgloseMetricas(m);
  div.append(linea, detalle);
  div.addEventListener('click', () => { detalle.hidden = !detalle.hidden; });
  ultima.after(div);
  chat.scrollTop = chat.scrollHeight;
}

// ── Texto de JARVIS mientras Claude lo genera (assistant_delta) ──
// Una sola burbuja provisional: el assistant_message final la sustituye y ""
// la descarta (la iteración usó tools). Se quita también al volver a escuchar.
let parcial = null;

function quitarParcial() {
  if (parcial) {
    parcial.remove();
    parcial = null;
  }
}

function textoParcial(texto) {
  if (typeof texto !== 'string' || !texto) {
    quitarParcial();
    return;
  }
  if (!parcial) {
    vacio.hidden = true;
    parcial = document.createElement('div');
    parcial.className = 'burbuja jarvis parcial';
    chat.appendChild(parcial);
  }
  parcial.textContent = texto;
  chat.scrollTop = chat.scrollHeight;
}

// Avisos persistentes (autorización de Google...): Python los reenvía al reconectar
const avisosVistos = new Set();

function aviso(msg) {
  const clave = `${msg.ts}|${msg.message}`;
  if (avisosVistos.has(clave)) return;
  avisosVistos.add(clave);
  burbuja('aviso', msg.message, msg.ts);
}

function manejar(msg) {
  switch (msg.type) {
    case 'hello':
    case 'state':
      if (msg.state === 'listening') quitarParcial();
      setEstado(msg.state);
      break;
    case 'assistant_delta':
      textoParcial(msg.text);
      break;
    case 'interrupted':
    case 'not_understood':
      setEstado(msg.type);
      break;
    case 'user_message':
      burbuja('usuario', msg.text, msg.ts);
      break;
    case 'assistant_message':
      quitarParcial();
      burbuja('jarvis', msg.text, msg.ts);
      break;
    case 'error':
      burbuja('error', msg.message, msg.ts);
      break;
    case 'warning':
      aviso(msg);
      break;
    case 'metrics':
      metricas(msg);
      break;
  }
}

// ── Callar a JARVIS: botón, Esc en la ventana o Ctrl+Alt+P global (main.js) ──
// Solo corta la voz, nunca confirma nada.
let socket = null;

function parar(origen) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'stop', origen }));
  }
}

botonParar.addEventListener('click', () => parar('ui'));
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') parar('teclado');
});
if (window.jarvis && typeof window.jarvis.alAtajoParar === 'function') {
  window.jarvis.alAtajoParar(() => parar('teclado'));
}

// ── "Cerrar JARVIS completamente" (bandeja): Python guarda la sesión y termina ──
function pedirCierre() {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'cerrar' }));
    return true;
  }
  return false;
}

if (window.jarvis && typeof window.jarvis.alCerrar === 'function') {
  window.jarvis.alCerrar(pedirCierre);
}

function conectar() {
  const ws = new WebSocket(URL_WS);
  socket = ws;
  ws.onopen = () => { botonParar.disabled = false; };

  ws.onmessage = (ev) => {
    try {
      manejar(JSON.parse(ev.data));
    } catch (e) {
      console.warn('[JARVIS] mensaje no válido', e);
    }
  };

  ws.onclose = () => {
    botonParar.disabled = true;
    setEstado('disconnected');
    setTimeout(conectar, RECONEXION);
  };

  ws.onerror = () => ws.close();
}

setEstado('disconnected');
conectar();
