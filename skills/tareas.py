import threading
import time
import re
from datetime import datetime, timedelta

# ── RECORDATORIOS ────────────────────────────────────────────────
recordatorios_activos = []

def _timer_recordatorio(memoria, hablar_fn, tarea, minutos=None, hora_str=None):
    """Hilo que espera y avisa cuando llega el momento."""
    if minutos:
        time.sleep(minutos * 60)
    elif hora_str:
        ahora = datetime.now()
        try:
            hora_obj = datetime.strptime(hora_str, "%H:%M").replace(
                year=ahora.year, month=ahora.month, day=ahora.day
            )
            if hora_obj < ahora:
                hora_obj += timedelta(days=1)
            espera = (hora_obj - ahora).total_seconds()
            if espera > 0:
                time.sleep(espera)
        except Exception:
            return
    hablar_fn(f"Joaquin, recordatorio: {tarea}")

def añadir_recordatorio(memoria, hablar_fn, tarea, hora_str=None, minutos=None):
    from memoria import añadir_tarea
    añadir_tarea(memoria, tarea, hora=hora_str)

    hilo = threading.Thread(
        target=_timer_recordatorio,
        args=(memoria, hablar_fn, tarea, minutos, hora_str),
        daemon=True
    )
    hilo.start()
    recordatorios_activos.append(hilo)

    if hora_str:
        return f"Recordatorio a las {hora_str}: {tarea}"
    return f"Recordatorio en {minutos} minutos: {tarea}"

# ── PARSER DE HORA Y TIEMPO ──────────────────────────────────────
def extraer_hora(texto):
    """Extrae hora tipo '18:30' o '18h30' o '6 de la tarde'."""
    # Formato HH:MM
    match = re.search(r'\b(\d{1,2}):(\d{2})\b', texto)
    if match:
        return f"{match.group(1).zfill(2)}:{match.group(2)}"

    # Formato HH h
    match = re.search(r'\b(\d{1,2})\s*h\b', texto)
    if match:
        return f"{match.group(1).zfill(2)}:00"

    # "a las X de la tarde/mañana"
    match = re.search(r'a las (\d{1,2})', texto)
    if match:
        hora = int(match.group(1))
        if "tarde" in texto and hora < 12:
            hora += 12
        return f"{hora:02d}:00"

    return None

def extraer_minutos(texto):
    """Extrae minutos de 'en X minutos'."""
    match = re.search(r'en (\d+) minutos?', texto)
    if match:
        return int(match.group(1))
    match = re.search(r'(\d+) minutos?', texto)
    if match:
        return int(match.group(1))
    return None

def extraer_tarea(texto):
    """Extrae la tarea eliminando palabras de tiempo."""
    texto = texto.lower()
    for p in ["recuérdame", "recuerdame", "jarvis", "a las", "en",
              "minutos", "mañana", "esta tarde", "esta noche",
              "recordatorio", "por favor"]:
        texto = texto.replace(p, "")
    # Eliminar horas
    texto = re.sub(r'\d{1,2}:\d{2}', '', texto)
    texto = re.sub(r'\d{1,2}\s*h\b', '', texto)
    texto = re.sub(r'\d+\s*minutos?', '', texto)
    return texto.strip().strip(".,!¿?que ")

# ── TEMPORIZADOR POMODORO ────────────────────────────────────────
def pomodoro(hablar_fn, minutos=25):
    def _pomodoro():
        hablar_fn(f"Pomodoro iniciado. {minutos} minutos de foco.")
        time.sleep(minutos * 60)
        hablar_fn("Pomodoro terminado. Tómate 5 minutos de descanso.")

    threading.Thread(target=_pomodoro, daemon=True).start()
    return f"Pomodoro de {minutos} minutos iniciado"

# ── LISTAR TAREAS ────────────────────────────────────────────────
def listar_tareas(memoria):
    from memoria import tareas_pendientes
    tareas = tareas_pendientes(memoria)
    if not tareas:
        return "No tienes tareas pendientes"
    lista = [f"{i+1}. {t['tarea']}" + (f" a las {t['hora']}" if t['hora'] else "")
             for i, t in enumerate(tareas[:5])]
    return "Tareas pendientes: " + ". ".join(lista)

# ── AÑADIR NOTA ──────────────────────────────────────────────────
def añadir_nota_voz(memoria, texto):
    from memoria import añadir_nota
    # Limpiar el texto de palabras trigger
    nota = texto.lower()
    for p in ["jarvis", "anota", "anótame", "apunta", "guarda", "nota", "que"]:
        nota = nota.replace(p, "")
    nota = nota.strip().strip(".,!¿?")
    if nota:
        añadir_nota(memoria, nota)
        return f"Nota guardada: {nota}"
    return "No entendí qué querías anotar"

# ── DETECTAR INTENCIONES ─────────────────────────────────────────
def es_recordatorio(texto_lower):
    return any(p in texto_lower for p in [
        "recuérdame", "recuerdame", "pon un recordatorio",
        "avísame", "avisame", "no me olvides"
    ])

def es_nota(texto_lower):
    return any(p in texto_lower for p in [
        "anota", "anótame", "apunta", "guarda una nota",
        "toma nota", "nota que"
    ])

def es_pomodoro(texto_lower):
    return "pomodoro" in texto_lower

def es_listar_tareas(texto_lower):
    return any(p in texto_lower for p in [
        "qué tareas tengo", "que tareas tengo",
        "mis tareas", "tareas pendientes",
        "qué tengo pendiente", "que tengo pendiente"
    ])