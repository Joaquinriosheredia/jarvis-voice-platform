"""Puente WebSocket local entre JARVIS y la app Electron (app/).

Python es el servidor (127.0.0.1:8765) y emite eventos. Del cliente solo se
aceptan {"type": "stop", "origen": "ui"|"teclado"} (botón o atajo para callar a
JARVIS) y {"type": "cerrar"} ("Cerrar JARVIS completamente" en la bandeja: guarda la
sesión y termina, lo mismo que ya hacía el menú matando el proceso); cualquier otro
mensaje se ignora. emit() nunca lanza y no hace nada si el servidor no está
arrancado o no hay ningún cliente conectado: JARVIS funciona igual sin la app.

Protocolo: {"v": 1, "type": ..., "ts": "ISO8601", ...datos}
Solo se envía texto que ya se muestra o se habla al usuario. Los avisos de
avisar() ("warning") se guardan y se reenvían a cada cliente que se conecte
después (p. ej. la UI abre más tarde que el aviso de arranque)."""
import asyncio
import json
import threading
from datetime import datetime

# ── ORÍGENES PERMITIDOS ───────────────────────────────────────────
# Solo el origen exacto que envía Electron al cargar index.html.
# Se rechazan "null" (iframes sandbox de cualquier web), http://localhost
# (otros servidores locales) y la ausencia de cabecera Origin.
ORIGENES = ["file://"]

_hilo     = None
_loop     = None
_clientes = set()       # solo se modifica desde el hilo del event loop
_estado   = "idle"      # último estado emitido, se envía en el hello
al_parar  = None        # jarvis.py: audio.parar_voz(origen). Solo calla; nunca confirma nada
al_cerrar = None        # jarvis.py: guarda (historial y memoria entre sesiones) y termina
_avisos   = []          # mensajes "warning" ya serializados (con su ts original)
MAX_MENSAJE = 256
_lock     = threading.Lock()

# ── LOG ───────────────────────────────────────────────────────────
def _print_seguro(texto):
    try:
        print(texto)
    except Exception:
        pass

# ── MENSAJES ──────────────────────────────────────────────────────
def _mensaje(tipo, **data):
    return json.dumps({
        **data,
        "v":    1,
        "type": tipo,
        "ts":   datetime.now().astimezone().isoformat(timespec="milliseconds"),
    }, ensure_ascii=False)

# ── MENSAJES DEL CLIENTE ──────────────────────────────────────────
def recibir(mensaje):
    """Atiende un mensaje del cliente: solo "stop" y "cerrar". Nunca lanza."""
    try:
        if not isinstance(mensaje, str) or len(mensaje) > MAX_MENSAJE:
            return
        datos = json.loads(mensaje)
        if not isinstance(datos, dict):
            return
        if datos.get("type") == "stop" and callable(al_parar):
            al_parar("teclado" if datos.get("origen") == "teclado" else "ui")
        elif datos.get("type") == "cerrar" and callable(al_cerrar):
            al_cerrar()
    except Exception:
        pass

# ── SERVIDOR ──────────────────────────────────────────────────────
async def _handler(ws):
    _clientes.add(ws)
    try:
        await ws.send(_mensaje("hello", state=_estado))
        for aviso in list(_avisos):
            await ws.send(aviso)
        async for mensaje in ws:
            recibir(mensaje)
    except Exception:
        pass
    finally:
        _clientes.discard(ws)

def _servir(host, port):
    global _loop
    try:
        from websockets.asyncio.server import serve

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def principal():
            async with serve(_handler, host, port, origins=ORIGENES):
                _print_seguro(f"[UI] WebSocket escuchando en ws://{host}:{port}")
                await asyncio.Future()  # hasta que muera el proceso

        _loop = loop
        loop.run_until_complete(principal())
    except Exception as e:
        _loop = None
        _print_seguro(f"[UI] servidor WebSocket no disponible: {e}")

def start(host="127.0.0.1", port=8765):
    """Arranca el servidor en un hilo daemon con su propio event loop.
    No bloquea. Llamadas repetidas no hacen nada."""
    global _hilo
    with _lock:
        if _hilo is not None:
            return
        _hilo = threading.Thread(target=_servir, args=(host, port),
                                 daemon=True, name="ui-bridge")
        _hilo.start()

# ── EMISIÓN ───────────────────────────────────────────────────────
def _difundir(mensaje):
    from websockets.asyncio.server import broadcast
    broadcast(set(_clientes), mensaje)

def avisar(texto):
    """Aviso persistente (p. ej. autorización de Google caducada): se muestra ahora
    y a cada cliente que se conecte más tarde. Nunca lanza."""
    try:
        if not isinstance(texto, str) or not texto or any(json.loads(a)["message"] == texto for a in _avisos):
            return
        mensaje = _mensaje("warning", message=texto)
        _avisos.append(mensaje)
        loop = _loop
        if loop is not None and _clientes:
            loop.call_soon_threadsafe(_difundir, mensaje)
    except Exception:
        pass

def emit(type, **data):
    """Envía un evento a todos los clientes. Thread-safe, nunca lanza."""
    global _estado
    try:
        if type == "state" and "state" in data:
            _estado = data["state"]
        loop = _loop
        if loop is None or not _clientes:
            return
        loop.call_soon_threadsafe(_difundir, _mensaje(type, **data))
    except Exception:
        pass
