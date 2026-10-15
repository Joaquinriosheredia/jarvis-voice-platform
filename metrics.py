"""Observabilidad por turno: latencias, tokens, coste estimado y log de replay.

Un turno empieza en audio.escuchar (antes de Whisper) y lo cierra jarvis.py
cuando JARVIS termina de hablar: se añade una línea a logs/turnos.jsonl y se
emite el evento "metrics" a la UI. Ninguna función lanza: un fallo de métricas
nunca debe tumbar el bucle de voz. Las llamadas fuera de un turno se ignoran.

total_ms = latencia de respuesta: desde que empieza Whisper hasta que empieza
a sonar el primer audio (no incluye la duración de la locución).

Texto en el log: JARVIS_LOG_TEXTO=0 guarda solo metadatos. Los turnos privados
nunca guardan texto, y los de TOOLS_SIN_RESPUESTA no guardan la respuesta.
"""
import datetime
import json
import math
import os
import threading
import time
from contextlib import contextmanager

import tts_elevenlabs
import ui_bridge

# ── PRECIOS (USD por millón de tokens; estimado, Claude Haiku 4.5) ──
PRECIO_INPUT       = 1.00
PRECIO_OUTPUT      = 5.00
PRECIO_CACHE_READ  = 0.10
PRECIO_CACHE_WRITE = 1.25
# ElevenLabs: USD por carácter enviado (estimado; depende del plan)
PRECIO_CHAR_ELEVENLABS = 0.0

DIR_LOGS      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
RUTA_TURNOS   = os.path.join(DIR_LOGS, "turnos.jsonl")
MAX_BYTES_LOG = 5 * 1024 * 1024
MAX_TEXTO     = 500
# Turnos con estas tools se guardan sin respuesta: no volcar código ni salidas de tests a disco
TOOLS_SIN_RESPUESTA = {"leer_archivo", "git_diff", "buscar_en_codigo", "ejecutar_tests"}
FASES         = ("whisper", "router", "llm", "tools", "tts")
TOKENS        = ("input", "output", "cache_read", "cache_creation")

_lock  = threading.Lock()
_turno = None


def _log(texto):
    try:
        print(texto)
    except Exception:
        pass


# ── CICLO DEL TURNO ───────────────────────────────────────────────
def iniciar_turno():
    global _turno
    try:
        with _lock:
            _turno = {
                "t0": time.monotonic(), "t_audio": None,
                "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                "ruta": None, "tools": [],
                **{f"{f}_ms": 0 for f in FASES},
                **{t: 0 for t in TOKENS},
                "tts": set(), "chars_tts": 0, "chars_elevenlabs": 0,
                "whisper_logprob": None, "ttft_ms": None, "t_primera_voz": None,
            }
    except Exception:
        pass


def descartar_turno():
    global _turno
    with _lock:
        _turno = None


def _en_turno(funcion):
    """Aplica funcion(turno) bajo el lock si hay turno activo; nunca lanza."""
    try:
        with _lock:
            if _turno is not None:
                funcion(_turno)
    except Exception:
        pass


def marcar(fase, ms):
    def f(t):
        t[f"{fase}_ms"] += int(ms)
    _en_turno(f)


@contextmanager
def cronometro(fase):
    t0 = time.monotonic()
    try:
        yield
    finally:
        marcar(fase, (time.monotonic() - t0) * 1000)


def registrar_ruta(ruta):
    def f(t):
        t["ruta"] = ruta
    _en_turno(f)


def registrar_uso_llm(usage):
    """Suma el usage de una respuesta de Claude (una iteración del bucle de tools)."""
    def f(t):
        for clave, campo in (("input", "input_tokens"), ("output", "output_tokens"),
                             ("cache_read", "cache_read_input_tokens"),
                             ("cache_creation", "cache_creation_input_tokens")):
            valor = getattr(usage, campo, None)
            if isinstance(valor, int):
                t[clave] += valor
    if usage is not None:
        _en_turno(f)


def registrar_whisper_logprob(logprob):
    """Confianza media de Whisper en el turno (media de avg_logprob de los segmentos)."""
    def f(t):
        t["whisper_logprob"] = round(logprob, 3)
    if isinstance(logprob, (int, float)):
        _en_turno(f)


def registrar_ttft(ms):
    """Tiempo hasta el primer token de texto de Claude (solo el primero del turno)."""
    def f(t):
        if t["ttft_ms"] is None:
            t["ttft_ms"] = int(ms)
    _en_turno(f)


def registrar_primera_voz():
    """Momento en que empieza a sonar la primera frase del turno."""
    def f(t):
        if t["t_primera_voz"] is None:
            t["t_primera_voz"] = time.monotonic()
    _en_turno(f)


def registrar_tool(nombre):
    def f(t):
        t["tools"].append(nombre)
    _en_turno(f)


def registrar_tts(provider, chars, ms):
    def f(t):
        t["tts"].add(provider)
        t["chars_tts"] += int(chars)
        if provider == "elevenlabs":
            t["chars_elevenlabs"] += int(chars)
        t["tts_ms"] += int(ms)
        if t["t_audio"] is None:  # empieza a sonar el primer audio
            t["t_audio"] = time.monotonic()
    _en_turno(f)


# ── CÁLCULOS ──────────────────────────────────────────────────────
def coste_usd(input=0, output=0, cache_read=0, cache_creation=0, chars_elevenlabs=0):
    return round((input * PRECIO_INPUT + output * PRECIO_OUTPUT + cache_read * PRECIO_CACHE_READ
                  + cache_creation * PRECIO_CACHE_WRITE) / 1_000_000
                 + chars_elevenlabs * PRECIO_CHAR_ELEVENLABS, 6)


def cache_ratio(turnos):
    """Fracción de los tokens de entrada servidos desde caché (None si no hubo LLM)."""
    leidos = sum(t.get("cache_read", 0) for t in turnos)
    total  = sum(t.get("input", 0) + t.get("cache_read", 0) + t.get("cache_creation", 0) for t in turnos)
    return leidos / total if total else None


def percentil(valores, p):
    """Percentil por rango más cercano (p50 de [1..10] = 5)."""
    datos = sorted(valores)
    if not datos:
        return None
    return datos[max(0, math.ceil(p / 100 * len(datos)) - 1)]


def _truncar(texto):
    if not isinstance(texto, str):
        return None
    return texto if len(texto) <= MAX_TEXTO else texto[:MAX_TEXTO] + "…"


def _registro(t, texto_usuario, respuesta, privado):
    fin = t["t_audio"] or time.monotonic()
    tts = sorted(t["tts"])
    reg = {
        "ts": t["ts"], "ruta": t["ruta"] or "claude", "tools": list(t["tools"]), "privado": privado,
        **{f"{f}_ms": t[f"{f}_ms"] for f in FASES},
        "total_ms": int((fin - t["t0"]) * 1000),
        **{k: t[k] for k in TOKENS},
        "coste_usd": coste_usd(*(t[k] for k in TOKENS), chars_elevenlabs=t["chars_elevenlabs"]),
        "tts_provider": tts[0] if len(tts) == 1 else ("mixto" if tts else None),
        "chars_tts": t["chars_tts"],
    }
    # Campos opcionales del JSONL: los turnos antiguos no los tienen
    if t.get("whisper_logprob") is not None:
        reg["whisper_logprob"] = t["whisper_logprob"]
    if t.get("ttft_ms") is not None:
        reg["ttft_ms"] = t["ttft_ms"]
    if t.get("t_primera_voz") is not None:
        reg["primera_voz_ms"] = int((t["t_primera_voz"] - t["t0"]) * 1000)
    # Turnos con email/agenda o JARVIS_LOG_TEXTO=0 (se lee en cada turno): solo metadatos
    if not privado and os.environ.get("JARVIS_LOG_TEXTO", "1").strip() != "0":
        reg["texto_usuario"] = _truncar(texto_usuario)
        if not TOOLS_SIN_RESPUESTA.intersection(t["tools"]):
            reg["respuesta"] = _truncar(respuesta)
    return reg


# ── PERSISTENCIA ──────────────────────────────────────────────────
def _escribir(reg, ruta=None):
    ruta = ruta or RUTA_TURNOS
    try:
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        if os.path.exists(ruta) and os.path.getsize(ruta) > MAX_BYTES_LOG:
            os.replace(ruta, ruta.replace(".jsonl", ".1.jsonl"))  # un solo backup
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(json.dumps(reg, ensure_ascii=False) + "\n")
    except Exception as e:
        _log(f"[METRICS] no se pudo escribir {os.path.basename(ruta)}: {type(e).__name__}")


def cargar(ruta=None, con_backup=True):
    """Turnos del JSONL (y de su backup, primero los más antiguos). Ignora líneas corruptas."""
    ruta   = ruta or RUTA_TURNOS
    rutas  = ([ruta.replace(".jsonl", ".1.jsonl")] if con_backup else []) + [ruta]
    turnos = []
    for r in rutas:
        try:
            with open(r, encoding="utf-8") as f:
                for linea in f:
                    try:
                        turnos.append(json.loads(linea))
                    except ValueError:
                        pass
        except OSError:
            pass
    return turnos


def cerrar_turno(texto_usuario=None, respuesta=None):
    """Cierra el turno activo: lo escribe en el JSONL, emite "metrics" y lo devuelve (o None)."""
    global _turno
    try:
        with _lock:
            t, _turno = _turno, None
        if t is None:
            return None
        reg = _registro(t, texto_usuario, respuesta, tts_elevenlabs.turno_privado())
        _escribir(reg)
        ui_bridge.emit("metrics", **evento_ui(reg))
        _log(f"[METRICS] ruta={reg['ruta']} total={reg['total_ms']}ms llm={reg['llm_ms']}ms "
             f"tts={reg['tts_ms']}ms coste=${reg['coste_usd']:.4f}")
        return reg
    except Exception as e:
        _log(f"[METRICS] error al cerrar el turno: {type(e).__name__}")
        return None


def evento_ui(reg):
    """Campos del turno para la UI: nunca el texto del usuario ni la respuesta."""
    return {k: v for k, v in reg.items() if k not in ("texto_usuario", "respuesta", "ts")}


# ── RESÚMENES ─────────────────────────────────────────────────────
def resumen(turnos):
    total = [t["total_ms"] for t in turnos if isinstance(t.get("total_ms"), int)]
    rutas = {}
    for t in turnos:
        rutas[t.get("ruta")] = rutas.get(t.get("ruta"), 0) + 1
    return {
        "turnos":    len(turnos),
        "coste_usd": round(sum(t.get("coste_usd", 0) for t in turnos), 6),
        "rutas_pct": {r: round(n * 100 / len(turnos), 1) for r, n in sorted(rutas.items(), key=str)},
        "p50_ms":    percentil(total, 50),
        "p95_ms":    percentil(total, 95),
        "medio_ms":  round(sum(total) / len(total)) if total else None,
        "cache_ratio": cache_ratio(turnos),
    }


def turnos_desde(turnos, dias, hoy=None):
    """Turnos de los últimos `dias` días naturales (1 = solo hoy)."""
    hoy    = hoy or datetime.date.today()
    limite = hoy - datetime.timedelta(days=dias - 1)
    salida = []
    for t in turnos:
        try:
            if datetime.datetime.fromisoformat(t["ts"]).date() >= limite:
                salida.append(t)
        except (KeyError, TypeError, ValueError):
            pass
    return salida


def gasto_hoy(ruta=None):
    """Tool de voz: (True, resumen de hoy)."""
    try:
        r = resumen(turnos_desde(cargar(ruta), 1))
    except Exception as e:
        return False, f"No se pudo leer el registro de turnos: {type(e).__name__}"
    if not r["turnos"]:
        return True, "Hoy no hay turnos registrados."
    return True, (f"Hoy llevas {r['turnos']} turnos, con un coste estimado de "
                  f"{r['coste_usd']:.4f} dólares y un tiempo medio de respuesta de "
                  f"{r['medio_ms'] / 1000:.1f} segundos.")
