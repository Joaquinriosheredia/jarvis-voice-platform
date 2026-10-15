"""TTS opcional con ElevenLabs. Piper sigue siendo el default y el fallback.

Variables de entorno (también valen en C:\\Jarvis-secrets\\.env, que carga jarvis.py):
  JARVIS_TTS            "piper" (default) | "elevenlabs"
  ELEVENLABS_API_KEY    clave de la API (si no, se lee de ELEVENLABS_JSON)
  ELEVENLABS_VOICE_ID   voz a usar (si no, se lee de ELEVENLABS_JSON)
ELEVENLABS_JSON = C:\\Jarvis-secrets\\elevenlabs.json con {"api_key": "...", "voice_id": "..."}

sintetizar(texto) devuelve PCM 16-bit mono a PCM_RATE Hz (lo que reproduce
audio.hablar), o None para que esa frase la diga Piper. Nunca lanza ni
escribe la clave en ningún log.

Privacidad: si en el turno se usó una tool con datos privados (brain.TOOLS_PRIVADAS)
o se dice el resumen diario (emails), marcar_privado() fuerza Piper hasta nuevo_turno().
"""
import json
import os
import time

import requests

ELEVENLABS_JSON = r"C:\Jarvis-secrets\elevenlabs.json"
URL             = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
MODELO          = "eleven_flash_v2_5"
PCM_RATE        = 22050                 # output_format=pcm_22050: int16 mono sin cabecera
TIMEOUT         = 10                    # s, sin reintentos
MAX_CHARS       = 500                   # por frase; más largo → Piper (coste)
MAX_FALLOS      = 3                     # seguidos → pausa
PAUSA_S         = 5 * 60

_fallos_seguidos   = 0
_desactivado_hasta = 0.0
_total_chars       = 0
_aviso_config      = False
# Turno con datos privados (emails, agenda): lo marca brain y lo limpia al
# empezar el siguiente turno. Mientras esté activo, todo se dice con Piper.
_turno_privado     = False


def nuevo_turno():
    global _turno_privado
    _turno_privado = False


def marcar_privado():
    global _turno_privado
    _turno_privado = True


def turno_privado():
    return _turno_privado


def _log(texto):
    try:
        print(texto)
    except Exception:
        pass


def _config():
    """(api_key, voice_id); cualquiera puede ser None."""
    api_key  = os.environ.get("ELEVENLABS_API_KEY")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID")
    if not (api_key and voice_id):
        try:
            with open(ELEVENLABS_JSON, encoding="utf-8") as f:
                datos = json.load(f)
            api_key  = api_key or datos.get("api_key")
            voice_id = voice_id or datos.get("voice_id")
        except (OSError, ValueError, AttributeError):
            pass
    return api_key or None, voice_id or None


def _fallo(motivo):
    """Registra un fallo; tras MAX_FALLOS seguidos pausa ElevenLabs PAUSA_S."""
    global _fallos_seguidos, _desactivado_hasta
    _fallos_seguidos += 1
    _log(f"[TTS] fallback a piper: {motivo}")
    if _fallos_seguidos >= MAX_FALLOS:
        _desactivado_hasta = time.monotonic() + PAUSA_S
        _fallos_seguidos   = 0
        _log(f"[TTS] elevenlabs desactivado {PAUSA_S // 60} min tras {MAX_FALLOS} fallos seguidos")
    return None


def sintetizar(texto):
    """bytes PCM int16 mono a PCM_RATE Hz, o None → Piper para esta frase."""
    global _fallos_seguidos, _total_chars, _aviso_config
    if os.environ.get("JARVIS_TTS", "piper").strip().lower() != "elevenlabs":
        return None
    if not texto or not texto.strip():
        return None
    if _turno_privado:
        _log("[TTS] turno con datos privados → piper")
        return None
    if len(texto) > MAX_CHARS:
        _log(f"[TTS] {len(texto)} caracteres > {MAX_CHARS}: piper para esta frase")
        return None
    if time.monotonic() < _desactivado_hasta:
        return None
    api_key, voice_id = _config()
    if not (api_key and voice_id):
        if not _aviso_config:
            falta = " y ".join(n for n, v in (("ELEVENLABS_API_KEY", api_key),
                                              ("ELEVENLABS_VOICE_ID", voice_id)) if not v)
            _log(f"[TTS] JARVIS_TTS=elevenlabs pero falta {falta}: se usa piper")
            _aviso_config = True
        return None

    try:
        r = requests.post(URL.format(voice_id=voice_id),
                          params={"output_format": f"pcm_{PCM_RATE}"},
                          headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                          json={"text": texto, "model_id": MODELO},
                          timeout=TIMEOUT)
    except requests.Timeout:
        return _fallo(f"timeout ({TIMEOUT} s)")
    except requests.RequestException as e:  # sin red, DNS... (solo el tipo: sin URL ni cabeceras)
        return _fallo(f"error de red ({type(e).__name__})")
    if r.status_code != 200:
        return _fallo(f"HTTP {r.status_code}")
    pcm = r.content[: len(r.content) // 2 * 2]  # int16: longitud par
    if not pcm:
        return _fallo("respuesta vacía")

    _fallos_seguidos = 0
    _total_chars    += len(texto)
    _log(f"[TTS] elevenlabs chars={len(texto)} total={_total_chars}")
    return pcm
