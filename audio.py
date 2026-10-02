import time
import threading
import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav
import tempfile
import os
import wave
from piper.voice import PiperVoice
from pycaw.pycaw import AudioUtilities

SAMPLE_RATE  = 16000
VOICE_MODEL  = r"C:\Jarvis\voices\es_ES-davefx-medium.onnx"

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
                               dtype='int16', blocksize=FRAME_SIZE, device=1) as stream:
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

# ── HABLAR ────────────────────────────────────────────────────────
def hablar(texto):
    global JARVIS_HABLANDO, _INTERRUMPIR
    print(f"🔊 Jarvis: {texto}")
    ducking_on()
    JARVIS_HABLANDO = True
    _INTERRUMPIR    = False

    # Monitor de barge-in
    hilo_monitor = threading.Thread(target=_monitor_barge_in, daemon=True)
    hilo_monitor.start()

    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        with wave.open(tmp, 'wb') as wf:
            voice.synthesize_wav(texto, wf)
        with wave.open(tmp, 'rb') as wf:
            frames = wf.readframes(wf.getnframes())
            rate   = wf.getframerate()
            data   = np.frombuffer(frames, dtype=np.int16)

        # Tracker RMS de salida en hilo paralelo
        hilo_rms = threading.Thread(
            target=_track_rms_salida, args=(data,), daemon=True
        )
        hilo_rms.start()

        sd.play(data, rate)
        while sd.get_stream().active:
            if _INTERRUMPIR and BARGE_IN_ACTIVO:
                sd.stop()
                print("⏹️  Jarvis interrumpido")
                break
            time.sleep(0.05)

        os.unlink(tmp)
    finally:
        JARVIS_HABLANDO = False
        _INTERRUMPIR    = False
        with _lock_rms:
            _rms_salida = 0.0
        ducking_off()
        hilo_monitor.join(timeout=0.5)

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

# ── ESCUCHAR ──────────────────────────────────────────────────────
def escuchar(whisper_model):
    global JARVIS_HABLANDO
    if JARVIS_HABLANDO:
        return ""

    print("🎤 Escuchando...")
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
                           dtype='int16', blocksize=FRAME_SIZE, device=1) as stream:
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
                        fin_captura = "silencio"
                        break

    ducking_off()

    if not frames or len(frames) < FRAMES_INICIO:
        print("🤷 No entendido")
        return ""

    audio_data = np.concatenate(frames)
    max_val    = np.max(np.abs(audio_data))
    if max_val > 0:
        audio_data = (audio_data / max_val * 32767).astype(np.int16)

    print(f"⏱️  Audio capturado: {len(frames) * FRAME_MS / 1000:.1f}s")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp = f.name
    wav.write(tmp, SAMPLE_RATE, audio_data)

    t0        = time.time()
    resultado = whisper_model.transcribe(tmp, language='es')
    t_whisper = time.time() - t0
    print(f"⏱️  Whisper: {t_whisper:.2f}s")

    os.unlink(tmp)
    texto = resultado['text'].strip()
    for linea in _lineas_log_audio(frames, fin_captura, t_whisper, resultado, texto):
        _print_seguro(linea)
    if len(texto) < 2:
        print("🤷 No entendido")
        return ""
    print(f"👂 Tú: {texto}")
    return texto