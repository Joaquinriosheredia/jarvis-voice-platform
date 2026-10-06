// Cliente WebSocket de JARVIS: solo muestra lo que envía ui_bridge.py.
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

function burbuja(clase, texto, ts) {
  if (typeof texto !== 'string' || !texto) return;
  vacio.hidden = true;
  const div = document.createElement('div');
  div.className   = `burbuja ${clase}`;
  div.textContent = texto;
  const h = document.createElement('span');
  h.className   = 'hora';
  h.textContent = hora(ts);
  div.appendChild(h);
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function manejar(msg) {
  switch (msg.type) {
    case 'hello':
    case 'state':
      setEstado(msg.state);
      break;
    case 'interrupted':
    case 'not_understood':
      setEstado(msg.type);
      break;
    case 'user_message':
      burbuja('usuario', msg.text, msg.ts);
      break;
    case 'assistant_message':
      burbuja('jarvis', msg.text, msg.ts);
      break;
    case 'error':
      burbuja('error', msg.message, msg.ts);
      break;
  }
}

function conectar() {
  const ws = new WebSocket(URL_WS);

  ws.onmessage = (ev) => {
    try {
      manejar(JSON.parse(ev.data));
    } catch (e) {
      console.warn('[JARVIS] mensaje no válido', e);
    }
  };

  ws.onclose = () => {
    setEstado('disconnected');
    setTimeout(conectar, RECONEXION);
  };

  ws.onerror = () => ws.close();
}

setEstado('disconnected');
conectar();
