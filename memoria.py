import json
import os
import uuid
from datetime import datetime, timedelta

MEMORIA_FILE = r"C:\Jarvis\memoria.json"
MAX_NOTAS    = 100
MAX_TAREAS   = 50
MAX_HISTORIAL = 50

# ── ESTRUCTURA BASE ───────────────────────────────────────────────
def _memoria_vacia():
    return {
        "ultima_sesion": None,
        "documento_activo": None,
        "tareas_pendientes": [],
        "notas": [],
        "historial": [],
        "rachas_completadas": []
    }

# ── CARGAR / GUARDAR CON BACKUP ───────────────────────────────────
def cargar():
    try:
        if os.path.exists(MEMORIA_FILE):
            with open(MEMORIA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    try:
        with open(MEMORIA_FILE + ".bak", 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        pass
    return _memoria_vacia()

def guardar(mem):
    try:
        if os.path.exists(MEMORIA_FILE):
            os.replace(MEMORIA_FILE, MEMORIA_FILE + ".bak")
        with open(MEMORIA_FILE, 'w', encoding='utf-8') as f:
            json.dump(mem, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ Error guardando memoria: {e}")

# ── SESIÓN ────────────────────────────────────────────────────────
def iniciar_sesion(mem):
    mem["ultima_sesion"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    guardar(mem)
    return mem

# ── NOTAS ────────────────────────────────────────────────────────
def añadir_nota(mem, nota):
    mem["notas"].append({
        "texto": nota,
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M")
    })
    mem["notas"] = mem["notas"][-MAX_NOTAS:]
    guardar(mem)

def buscar_en_notas(mem, query):
    return [n for n in mem["notas"] if query.lower() in n["texto"].lower()]

# ── TAREAS CON ID ────────────────────────────────────────────────
def añadir_tarea(mem, tarea, hora=None):
    mem["tareas_pendientes"].append({
        "id":         str(uuid.uuid4()),
        "tarea":      tarea,
        "hora":       hora,
        "completada": False,
        "fecha":      datetime.now().strftime("%Y-%m-%d %H:%M")
    })
    mem["tareas_pendientes"] = mem["tareas_pendientes"][-MAX_TAREAS:]
    guardar(mem)

def completar_tarea(mem, tarea_id):
    for t in mem["tareas_pendientes"]:
        if t["id"] == tarea_id:
            t["completada"] = True
            guardar(mem)
            return True
    return False

def tareas_pendientes(mem):
    return [t for t in mem["tareas_pendientes"] if not t["completada"]]

def tareas_vencidas(mem):
    ahora    = datetime.now()
    vencidas = []
    for t in mem["tareas_pendientes"]:
        if t["hora"] and not t["completada"]:
            try:
                hora_tarea = datetime.strptime(t["hora"], "%H:%M")
                if hora_tarea.time() < ahora.time():
                    vencidas.append(t)
            except Exception:
                pass
    return vencidas

# ── HISTORIAL ────────────────────────────────────────────────────
def añadir_al_historial(mem, texto):
    mem["historial"].append({
        "texto": texto,
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M")
    })
    mem["historial"] = mem["historial"][-MAX_HISTORIAL:]
    guardar(mem)

def obtener_contexto(mem, limite=5):
    return " | ".join([h["texto"] for h in mem["historial"][-limite:]])

PREFIJO_JARVIS = "Jarvis: "

def _es_jarvis(entrada):
    return entrada.get("texto", "").startswith(PREFIJO_JARVIS)

def obtener_mensajes(mem, pares=3):
    """Últimos pares (usuario → JARVIS) del historial como mensajes de la API
    de Claude, alternando user/assistant. Las entradas sin pareja se ignoran,
    incluida la pregunta actual (ya añadida al historial, aún sin respuesta)."""
    hist = mem["historial"]
    encontrados = [(u, j) for u, j in zip(hist, hist[1:]) if not _es_jarvis(u) and _es_jarvis(j)]
    mensajes = []
    for u, j in encontrados[-pares:]:
        mensajes.append({"role": "user", "content": u["texto"]})
        mensajes.append({"role": "assistant", "content": j["texto"][len(PREFIJO_JARVIS):]})
    return mensajes

def conversacion_reciente(mem, minutos=2, ahora=None):
    """True si JARVIS respondió hace como mucho `minutos` (precisión de
    minuto: la fecha del historial no guarda segundos)."""
    ahora = ahora or datetime.now()
    for h in reversed(mem["historial"]):
        if _es_jarvis(h):
            try:
                fecha = datetime.strptime(h["fecha"], "%Y-%m-%d %H:%M")
            except Exception:
                return False
            return ahora - fecha <= timedelta(minutes=minutos)
    return False

# ── DOCUMENTO ACTIVO ─────────────────────────────────────────────
def set_documento_activo(mem, documento):
    mem["documento_activo"] = documento
    guardar(mem)

# ── ESTADO ACTUAL ────────────────────────────────────────────────
def estado_actual(mem):
    return {
        "tareas":    len(tareas_pendientes(mem)),
        "notas":     len(mem["notas"]),
        "documento": mem["documento_activo"]
    }

# ── RESUMEN SESIÓN ANTERIOR ───────────────────────────────────────
def resumen_sesion_anterior(mem):
    if not mem["ultima_sesion"]:
        return "Es la primera vez que arranco, Joaquin."

    tareas = tareas_pendientes(mem)
    notas  = mem["notas"][-3:] if mem["notas"] else []
    doc    = mem["documento_activo"]

    if not tareas and not notas and not doc:
        return f"Última sesión el {mem['ultima_sesion']}. No tienes nada pendiente."

    partes = [f"Última sesión: {mem['ultima_sesion']}."]
    if doc:
        partes.append(f"Estabas trabajando en: {doc}.")
    if tareas:
        partes.append(f"Tienes {len(tareas)} tareas pendientes.")
    if notas:
        partes.append(f"Última nota: {notas[-1]['texto']}.")

    return " ".join(partes)