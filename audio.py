import queue
import re
import time
import threading
from contextlib import contextmanager
import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav
import tempfile
import os
import wave
from piper.voice import PiperVoice
from pycaw.pycaw import AudioUtilities
import ui_bridge
import tts_elevenlabs
import metrics

SAMPLE_RATE  = 16000
VOICE_MODEL  = r"C:\Jarvis\voices\es_ES-davefx-medium.onnx"

# Vocabulario habitual: orienta a Whisper hacia las órdenes de JARVIS
WHISPER_PROMPT = ("Jarvis, hola, buenos días, buenas tardes, bien gracias, cómo estás, qué tal, "
                  "confirmo borrado, confirmo envío, confirmo captura, apágate, hasta luego, adiós, descargas, "
                  "archivos, emails, música, volumen, subir, bajar, buscar, borrar, mover")

# Frases que Whisper inventa ante silencio o ruido (subtítulos de YouTube)
FRASES_ALUCINACION = [
    "suscríbete", "suscribete", "gracias por ver",
    "no olvides", "dale like", "próximo vídeo",
    "próximo video", "hasta la próxima",
]
NO_SPEECH_MAX = 0.7     # por encima en TODOS los segmentos → no había voz

# ── CONFIANZA DE WHISPER ──────────────────────────────────────────
def _umbral(nombre, defecto):
    """Umbral sobrescribible por la variable de entorno del mismo nombre."""
    try:
        return float(os.environ.get(nombre, defecto))
    except (TypeError, ValueError):
        return defecto

WHISPER_MIN_LOGPROB     = _umbral("WHISPER_MIN_LOGPROB", -1.0)    # media de avg_logprob
WHISPER_MAX_COMPRESSION = _umbral("WHISPER_MAX_COMPRESSION", 2.4)  # máx. compression_ratio
# Con una confirmación pendiente en el gate: un texto dudoso nunca debe confirmar
WHISPER_MIN_LOGPROB_CONFIRMACION = -0.7
# jarvis.py lo conecta a brain.hay_pendiente (audio no importa brain)
hay_confirmacion_pendiente = lambda: False

# ── MICRÓFONO ─────────────────────────────────────────────────────
def _buscar_microfono():
    """Índice del Blue Yeti por nombre (el índice cambia si se conectan otros
    dispositivos). "blue" como palabra completa para no coger "Bluetooth".
    None = dispositivo predeterminado de Windows."""
    try:
        for i, d in enumerate(sd.query_devices()):
            if d['max_input_channels'] > 0 and re.search(r"\bblue\b|yeti", d['name'], re.IGNORECASE):
                try:
                    print(f"[AUDIO] Micrófono: {i} {d['name']}")
                except Exception:
                    pass
                return i
    except Exception:
        pass
    print("[AUDIO] Blue Yeti no encontrado, usando dispositivo predeterminado")
    return None

MICROFONO = _buscar_microfono()

# ── DETECCIÓN POR ENERGÍA RMS ─────────────────────────────────────
FRAME_MS        = 30
FRAME_SIZE      = int(SAMPLE_RATE * FRAME_MS / 1000)
UMBRAL_RMS      = 500           # umbral base en escucha normal
SILENCIO_SEG    = 1.5
SILENCIO_FRAMES = int(SILENCIO_SEG * 1000 / FRAME_MS)
MAX_SEG         = 10
MAX_FRAMES      = int(MAX_SEG * 1000 / FRAME_MS)
FRAMES_INICIO   = 3

voice           = PiperVoice.load(VOICE_MODEL)
JARVIS_HABLANDO = False
BARGE_IN_ACTIVO = True
_INTERRUMPIR    = False
_VOL_CONTROL    = None
_vol_antes      = {}

# RMS del audio que está reproduciendo Jarvis — se actualiza en tiempo real
_rms_salida     = 0.0
_lock_rms       = threading.Lock()

# ── VOLUMEN ───────────────────────────────────────────────────────
def _get_vol_control():
    global _VOL_CONTROL
    if _VOL_CONTROL:
        return _VOL_CONTROL
    try:
        _VOL_CONTROL = AudioUtilities.GetSpeakers().EndpointVolume
        return _VOL_CONTROL
    except Exception as e:
        _print_seguro(f"[VOL] Error inicializando control de volumen: {e}")
        return None

def get_volumen():
    vol = _get_vol_control()
    return round(vol.GetMasterVolumeLevelScalar() * 100) if vol else 50

def set_volumen(pct):
    vol = _get_vol_control()
    if vol:
        vol.SetMasterVolumeLevelScalar(max(0, min(100, pct)) / 100.0, None)

# ── DUCKING VLC ───────────────────────────────────────────────────
def ducking_on():
    global _vol_antes
    try:
        for s in AudioUtilities.GetAllSessions():
            if s.Process and "vlc" in s.Process.name().lower():
                pid = s.Process.pid
                _vol_antes[pid] = s.SimpleAudioVolume.GetMasterVolume()
                s.SimpleAudioVolume.SetMasterVolume(0.30, None)
    except Exception:
        pass

def ducking_off():
    try:
        for s in AudioUtilities.GetAllSessions():
            if s.Process and "vlc" in s.Process.name().lower():
                pid = s.Process.pid
                if pid in _vol_antes:
                    s.SimpleAudioVolume.SetMasterVolume(_vol_antes[pid], None)
    except Exception:
        pass

# ── DETECCIÓN VLC ─────────────────────────────────────────────────
def _vlc_activo():
    """True si hay un proceso vlc con sesión de audio activa.
    Falla cerrado: ante cualquier error devuelve False (umbral normal)."""
    try:
        for s in AudioUtilities.GetAllSessions():
            if s.Process and "vlc" in s.Process.name().lower() and s.State == 1:
                return True
    except Exception:
        pass
    return False

# ── RMS ───────────────────────────────────────────────────────────
def _rms(frame_bytes):
    audio = np.frombuffer(frame_bytes, dtype=np.int16).astype(np.float32)
    return np.sqrt(np.mean(audio ** 2)) if len(audio) > 0 else 0.0

# ── TRACKER DE RMS DE SALIDA ──────────────────────────────────────
def _track_rms_salida(data, chunk_size=480):
    """Calcula RMS del audio que Jarvis está reproduciendo chunk a chunk
    y lo almacena en _rms_salida para que el monitor lo use como referencia."""
    global _rms_salida
    for i in range(0, len(data), chunk_size):
        chunk = data[i:i + chunk_size]
        with _lock_rms:
            _rms_salida = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
        time.sleep(chunk_size / SAMPLE_RATE)
    with _lock_rms:
        _rms_salida = 0.0

# ── MONITOR DE BARGE-IN CON UMBRAL DINÁMICO ───────────────────────
def _monitor_barge_in():
    global _INTERRUMPIR
    time.sleep(0.3)  # espera a que el tracker RMS arranque
    FACTOR        = 3.0
    MARGEN        = 400
    FRAMES_UMBRAL = 5

    voz_frames = 0
    try:
        with sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1,
                               dtype='int16', blocksize=FRAME_SIZE, device=MICROFONO) as stream:
            while JARVIS_HABLANDO:
                frame, _ = stream.read(FRAME_SIZE)
                rms_mic  = _rms(bytes(frame))
                with _lock_rms:
                    rms_ref = _rms_salida
                umbral_dinamico = max(rms_ref * FACTOR + MARGEN, UMBRAL_RMS * 2)
                if rms_mic > umbral_dinamico:
                    voz_frames += 1
                    if voz_frames >= FRAMES_UMBRAL:
                        print(f"🤚 Barge-in — mic:{rms_mic:.0f} > umbral:{umbral_dinamico:.0f}")
                        _INTERRUMPIR = True
                        break
                else:
                    voz_frames = 0
    except Exception:
        pass

# ── SÍNTESIS ──────────────────────────────────────────────────────
def _sintetizar_piper(texto):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp = f.name
    try:
        with wave.open(tmp, 'wb') as wf:
            voice.synthesize_wav(texto, wf)
        with wave.open(tmp, 'rb') as wf:
            frames = wf.readframes(wf.getnframes())
            rate   = wf.getframerate()
    finally:
        os.unlink(tmp)
    return np.frombuffer(frames, dtype=np.int16), rate

def _sintetizar(texto):
    """(muestras int16, frecuencia): ElevenLabs si está activo y responde, si no Piper."""
    t0  = time.monotonic()
    pcm = tts_elevenlabs.sintetizar(texto)
    if pcm is not None:
        resultado, proveedor = (np.frombuffer(pcm, dtype=np.int16), tts_elevenlabs.PCM_RATE), "elevenlabs"
    else:
        resultado, proveedor = _sintetizar_piper(texto), "piper"
    metrics.registrar_tts(proveedor, len(texto), (time.monotonic() - t0) * 1000)
    return resultado

# ── HABLAR ────────────────────────────────────────────────────────
@contextmanager
def _sesion_habla(texto, texto_pantalla):
    """Estado común de hablar() y hablar_frases(): UI, ducking, flags y monitor de barge-in."""
    global JARVIS_HABLANDO, _INTERRUMPIR, _rms_salida
    print(f"🔊 Jarvis: {texto}")
    ui_bridge.emit("state", state="responding")
    ui_bridge.emit("assistant_message", text=texto_pantalla or texto)
    ducking_on()
    JARVIS_HABLANDO = True
    _INTERRUMPIR    = False

    # Monitor de barge-in
    hilo_monitor = threading.Thread(target=_monitor_barge_in, daemon=True)
    hilo_monitor.start()
    try:
        yield
    finally:
        JARVIS_HABLANDO = False
        _INTERRUMPIR    = False
        with _lock_rms:
            _rms_salida = 0.0
        ducking_off()
        hilo_monitor.join(timeout=0.5)
        ui_bridge.emit("state", state="idle")

def _interrumpido():
    return _INTERRUMPIR and BARGE_IN_ACTIVO

def _reproducir(data, rate):
    """Reproduce un audio; True si el usuario interrumpió (antes o durante)."""
    if _interrumpido():
        return True
    # Tracker RMS de salida en hilo paralelo
    hilo_rms = threading.Thread(
        target=_track_rms_salida, args=(data,), daemon=True
    )
    hilo_rms.start()

    metrics.registrar_primera_voz()
    sd.play(data, rate)
    while sd.get_stream().active:
        if _interrumpido():
            sd.stop()
            print("⏹️  Jarvis interrumpido")
            ui_bridge.emit("state", state="interrupted")
            return True
        time.sleep(0.05)
    return False

def hablar(texto, texto_pantalla=None):
    """Dice texto por voz; la UI muestra texto_pantalla si se pasa (p. ej. un archivo completo)."""
    with _sesion_habla(texto, texto_pantalla):
        data, rate = _sintetizar(texto)
        _reproducir(data, rate)

def hablar_frases(frases, texto_pantalla=None):
    """Dice las frases en orden: un hilo sintetiza la N+1 mientras suena la N.
    Barge-in: corta el audio, vacía la cola y no dice nada más. Si falla la
    síntesis de una frase, se conserva lo ya dicho y se avisa en la UI."""
    frases = [f.strip() for f in frases if f and f.strip()]
    if not frases:
        return
    with _sesion_habla(" ".join(frases), texto_pantalla):
        cola     = queue.Queue(maxsize=2)
        cancelar = threading.Event()

        def poner(item):
            while not cancelar.is_set():
                try:
                    cola.put(item, timeout=0.1)
                    return True
                except queue.Full:
                    pass
            return False

        def productor():
            try:
                for frase in frases:
                    if cancelar.is_set() or not poner(("audio", _sintetizar(frase))):
                        return
            except Exception as e:
                poner(("error", e))
                return
            poner(("fin", None))

        hilo = threading.Thread(target=productor, daemon=True, name="voz-frases")
        hilo.start()
        try:
            while True:
                tipo, valor = cola.get()
                if tipo == "fin":
                    break
                if tipo == "error":
                    _print_seguro(f"[VOZ] fallo sintetizando una frase: {type(valor).__name__}")
                    ui_bridge.emit("error", message="No he podido decir el resto de la respuesta.")
                    break
                if _reproducir(*valor):
                    break
        finally:
            cancelar.set()
            hilo.join(timeout=1)  # el productor sale en cuanto ve cancelar
            while True:           # la cola queda vacía pase lo que pase
                try:
                    cola.get_nowait()
                except queue.Empty:
                    break

# ── LOG DE DIAGNÓSTICO DE AUDIO ───────────────────────────────────
def _print_seguro(texto):
    try:
        print(texto)
    except UnicodeEncodeError:
        try:
            print(texto.encode('ascii', errors='replace').decode('ascii'))
        except Exception:
            pass
    except Exception:
        pass

def _lineas_log_audio(frames, fin_captura, t_whisper, resultado, texto):
    """Líneas [AUDIO] / [AUDIO+] con métricas de captura y de Whisper."""
    try:
        dur      = len(frames) * FRAME_SIZE / SAMPLE_RATE
        rms_pico = max(_rms(f.tobytes()) for f in frames)
        segs     = resultado.get('segments') or []
        s0       = segs[0] if segs else {}

        def campo(clave, fmt):
            v = s0.get(clave)
            return format(v, fmt) if v is not None else '?'

        lineas = [
            f"[AUDIO] dur={dur:.1f}s fin={fin_captura} rms_pico={rms_pico:.0f} "
            f"segs={len(segs)} whisper={t_whisper:.2f}s "
            f"temp={campo('temperature', '.1f')} no_speech={campo('no_speech_prob', '.2f')} "
            f"logprob={campo('avg_logprob', '.2f')} compression={campo('compression_ratio', '.2f')} "
            f"texto_len={len(texto)}"
        ]
        if len(segs) > 1:
            lineas.append(
                "[AUDIO+] segs_temp=" + ",".join(format(s.get('temperature', float('nan')), '.1f') for s in segs)
                + " segs_logprob=" + ",".join(format(s.get('avg_logprob', float('nan')), '.2f') for s in segs)
            )
        return lineas
    except Exception as e:
        return [f"[AUDIO] error generando log: {e}"]

# ── FILTRO DE ALUCINACIONES ───────────────────────────────────────
def _filtrar_transcripcion(resultado):
    """Texto de Whisper, o "" si no había voz o es una alucinación conocida."""
    texto = resultado['text'].strip()
    segs  = resultado.get('segments') or []
    if segs and all((s.get('no_speech_prob') or 0) > NO_SPEECH_MAX for s in segs):
        _print_seguro(f"[AUDIO] sin voz (no_speech_prob > {NO_SPEECH_MAX}): {texto}")
        return ""
    if any(f in texto.lower() for f in FRASES_ALUCINACION):
        _print_seguro(f"[AUDIO] alucinación detectada: {texto}")
        return ""
    return texto

def _confianza(resultado):
    """(media de avg_logprob, máximo compression_ratio) de los segmentos; None si faltan."""
    segs = resultado.get('segments') or []
    lps  = [s['avg_logprob'] for s in segs if isinstance(s.get('avg_logprob'), (int, float))]
    crs  = [s['compression_ratio'] for s in segs if isinstance(s.get('compression_ratio'), (int, float))]
    return (sum(lps) / len(lps) if lps else None), (max(crs) if crs else None)

def _baja_confianza(logprob, compresion):
    """True si el texto debe tratarse como no entendido. Nunca imprime el texto."""
    try:
        pendiente = bool(hay_confirmacion_pendiente())
    except Exception:
        pendiente = True  # ante la duda, el criterio estricto
    minimo = max(WHISPER_MIN_LOGPROB, WHISPER_MIN_LOGPROB_CONFIRMACION) if pendiente else WHISPER_MIN_LOGPROB
    dudoso = ((logprob is None and pendiente)            # sin métrica no se puede confirmar nada
              or (logprob is not None and logprob < minimo)
              or (compresion is not None and compresion > WHISPER_MAX_COMPRESSION))
    if dudoso:
        lp = f"{logprob:.2f}" if logprob is not None else "?"
        cr = f"{compresion:.2f}" if compresion is not None else "?"
        _print_seguro(f"[WHISPER] descartado por baja confianza logprob={lp} cr={cr}"
                      + (" (confirmación pendiente)" if pendiente else ""))
    return dudoso

# ── ESCUCHAR ──────────────────────────────────────────────────────
def escuchar(whisper_model):
    global JARVIS_HABLANDO
    if JARVIS_HABLANDO:
        return ""

    print("🎤 Escuchando...")
    ui_bridge.emit("state", state="listening")
    ducking_on()

    frames       = []
    silencio     = 0
    hablando     = False
    total        = 0
    voz_contador = 0
    fin_captura  = "tope"
    # GetAllSessions() tarda ~29 ms: se consulta una vez por escucha, no por trama
    umbral       = UMBRAL_RMS * 3 if _vlc_activo() else UMBRAL_RMS

    with sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1,
                           dtype='int16', blocksize=FRAME_SIZE, device=MICROFONO) as stream:
        while total < MAX_FRAMES:
            frame, _ = stream.read(FRAME_SIZE)
            fb       = bytes(frame)
            total   += 1
            energia  = _rms(fb)

            if energia > umbral:
                voz_contador += 1
                if voz_contador >= FRAMES_INICIO:
                    hablando = True
                silencio = 0
                frames.append(np.frombuffer(fb, dtype=np.int16))
            else:
                voz_contador = 0
                if hablando:
                    silencio += 1
                    frames.append(np.frombuffer(fb, dtype=np.int16))
                    if silencio >= SILENCIO_FRAMES:
                        print("⏸️  Procesando...")
                        ui_bridge.emit("state", state="processing")
                        fin_captura = "silencio"
                        break

    ducking_off()

    if not frames or len(frames) < FRAMES_INICIO:
        print("🤷 No entendido")
        ui_bridge.emit("state", state="not_understood")
        return ""

    audio_data = np.concatenate(frames)
    max_val    = np.max(np.abs(audio_data))
    if max_val > 0:
        audio_data = (audio_data / max_val * 32767).astype(np.int16)

    print(f"⏱️  Audio capturado: {len(frames) * FRAME_MS / 1000:.1f}s")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp = f.name
    wav.write(tmp, SAMPLE_RATE, audio_data)

    metrics.iniciar_turno()  # el turno (y su total_ms) empieza con Whisper
    t0        = time.time()
    resultado = whisper_model.transcribe(tmp, language='es', initial_prompt=WHISPER_PROMPT)
    t_whisper = time.time() - t0
    metrics.marcar("whisper", t_whisper * 1000)
    print(f"⏱️  Whisper: {t_whisper:.2f}s")

    os.unlink(tmp)
    texto = resultado['text'].strip()
    for linea in _lineas_log_audio(frames, fin_captura, t_whisper, resultado, texto):
        _print_seguro(linea)
    texto = _filtrar_transcripcion(resultado)
    logprob, compresion = _confianza(resultado)
    metrics.registrar_whisper_logprob(logprob)
    if len(texto) >= 2 and _baja_confianza(logprob, compresion):
        texto = ""
    if len(texto) < 2:
        print("🤷 No entendido")
        ui_bridge.emit("state", state="not_understood")
        return ""
    print(f"👂 Tú: {texto}")
    ui_bridge.emit("user_message", text=texto)
    return texto