import json
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import webbrowser
import urllib.parse
import urllib.request
from email.utils import parseaddr
import anthropic
import requests

# ── CLIENTE ───────────────────────────────────────────────────────
cliente = anthropic.Anthropic()
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
OLLAMA_URL     = "http://127.0.0.1:11434"
OLLAMA_MODEL   = "qwen2.5:3b"
OLLAMA_TIMEOUT = 8
OLLAMA_KEEP    = "10m"
DESCARGAS    = r"E:\descargas ryzen"

# ── HERRAMIENTAS ──────────────────────────────────────────────────
TOOLS = [
    {
        "name": "reproducir_musica",
        "description": "Reproduce música buscando en YouTube con VLC. Úsala para cualquier petición de música, canción o artista.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Artista, canción o género"}
        }, "required": ["query"]}
    },
    {
        "name": "parar_musica",
        "description": "Para la música que está sonando",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "abrir_youtube",
        "description": "Abre YouTube en el navegador con una búsqueda específica",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Búsqueda para YouTube"}
        }, "required": ["query"]}
    },
    {
        "name": "abrir_web",
        "description": "Abre una web conocida: github, linkedin, gmail, youtube, aws, google, portfolio, malt, claude",
        "input_schema": {"type": "object", "properties": {
            "sitio": {"type": "string", "description": "Nombre del sitio"}
        }, "required": ["sitio"]}
    },
    {
        "name": "abrir_app",
        "description": "Abre una aplicación: chrome, vscode, notepad, explorador, terminal, powershell, android, intellij",
        "input_schema": {"type": "object", "properties": {
            "app": {"type": "string", "description": "Nombre de la app"}
        }, "required": ["app"]}
    },
    {
        "name": "buscar_en_web",
        "description": "Busca información actual en internet con DuckDuckGo. Úsala para noticias, precios, datos actualizados, cualquier pregunta sobre el mundo real. Para preguntas sobre definiciones, biografías o historia, incluye en la query las palabras 'qué es', 'quién es' o 'historia de' para activar Wikipedia automáticamente.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Búsqueda"}
        }, "required": ["query"]}
    },
    {
        "name": "leer_emails",
        "description": "Lee los emails no leídos de Gmail",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "leer_email",
        "description": "Lee el contenido de un email por índice (0=primero)",
        "input_schema": {"type": "object", "properties": {
            "indice": {"type": "integer", "description": "Índice del email"}
        }, "required": ["indice"]}
    },
    {
        "name": "borrar_email",
        "description": "Mueve a la papelera un email ya listado. El gate pide confirmación al usuario.",
        "input_schema": {"type": "object", "properties": {
            "msg_id": {"type": "string", "description": "ID del email: el valor [id=...] del último listado"}
        }, "required": ["msg_id"]}
    },
    {
        "name": "borrar_multiples_emails",
        "description": "Mueve a la papelera varios emails ya listados. El gate pide confirmación al usuario.",
        "input_schema": {"type": "object", "properties": {
            "msg_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10,
                        "description": "IDs de los emails: los valores [id=...] del último listado"}
        }, "required": ["msg_ids"]}
    },
    {
        "name": "archivar_email",
        "description": "Archiva un email por índice",
        "input_schema": {"type": "object", "properties": {
            "indice": {"type": "integer", "description": "Índice del email"}
        }, "required": ["indice"]}
    },
    {
        "name": "enviar_email",
        "description": "Envía un email. SOLO a direcciones confirmadas. Para enviar a ti mismo usa get_mi_email primero. El gate pedirá confirmación con destinatario y adjunto.",
        "input_schema": {"type": "object", "properties": {
            "destinatario": {"type": "string"},
            "asunto":       {"type": "string"},
            "cuerpo":       {"type": "string"},
            "adjunto":      {"type": "string", "description": "Ruta completa del archivo a adjuntar (opcional)"}
        }, "required": ["destinatario", "asunto", "cuerpo"]}
    },
    {
        "name": "get_mi_email",
        "description": "Devuelve la dirección de email del usuario (la cuenta de Gmail de JARVIS).",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "buscar_emails",
        "description": "Busca emails por remitente o tema",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Término de búsqueda"}
        }, "required": ["query"]}
    },
    {
        "name": "emails_recruiters",
        "description": "Muestra emails de recruiters o trabajo",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "tiempo",
        "description": "Tiempo meteorológico actual en Los Corrales, Sevilla",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "hora",
        "description": "Hora actual",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "fecha",
        "description": "Fecha actual",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "estado_sistema",
        "description": "Estado del PC: disco, GPU, temperatura, RAM",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "listar_directorio",
        "description": "Lista el contenido de una carpeta. En carpetas grandes solo muestra una parte del contenido, por lo que NO sirve para localizar archivos concretos. Para localizar un archivo específico usa buscar_archivo.",
        "input_schema": {"type": "object", "properties": {
            "ruta": {"type": "string", "description": "Ruta. Descargas = E:\\descargas ryzen"}
        }, "required": ["ruta"]}
    },
    {
        "name": "organizar_directorio",
        "description": "Organiza todos los archivos de una carpeta en subcarpetas por tipo",
        "input_schema": {"type": "object", "properties": {
            "ruta": {"type": "string", "description": "Ruta a organizar"}
        }, "required": ["ruta"]}
    },
    {
        "name": "crear_carpeta",
        "description": "Crea una carpeta",
        "input_schema": {"type": "object", "properties": {
            "ruta": {"type": "string", "description": "Ruta completa"}
        }, "required": ["ruta"]}
    },
    {
        "name": "mover_archivo",
        "description": "Mueve un archivo",
        "input_schema": {"type": "object", "properties": {
            "origen":  {"type": "string"},
            "destino": {"type": "string"}
        }, "required": ["origen", "destino"]}
    },
    {
        "name": "borrar_archivo",
        "description": "Borra un archivo o carpeta",
        "input_schema": {"type": "object", "properties": {
            "ruta": {"type": "string"}
        }, "required": ["ruta"]}
    },
    {
        "name": "buscar_archivo",
        "description": "Localiza archivos por parte del nombre: busca por subcadena (no necesita el nombre exacto), sin distinguir mayúsculas, de forma recursiva desde el directorio indicado. Por defecto busca en Descargas. Devuelve rutas completas. Úsala cuando el usuario describe el archivo en lugar de dar su nombre exacto.",
        "input_schema": {"type": "object", "properties": {
            "nombre":     {"type": "string"},
            "directorio": {"type": "string", "description": "Directorio donde buscar (opcional)"}
        }, "required": ["nombre"]}
    },
    {
        "name": "volumen_subir",
        "description": "Sube el volumen",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "volumen_bajar",
        "description": "Baja el volumen",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "recordatorio",
        "description": "Crea un recordatorio a una hora",
        "input_schema": {"type": "object", "properties": {
            "tarea": {"type": "string"},
            "hora":  {"type": "string", "description": "HH:MM"}
        }, "required": ["tarea", "hora"]}
    },
    {
        "name": "nota",
        "description": "Guarda una nota",
        "input_schema": {"type": "object", "properties": {
            "texto": {"type": "string"}
        }, "required": ["texto"]}
    },
    {
        "name": "listar_tareas",
        "description": "Muestra tareas y recordatorios pendientes",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "pomodoro",
        "description": "Inicia un pomodoro",
        "input_schema": {"type": "object", "properties": {
            "minutos": {"type": "integer"}
        }}
    },
    {
        "name": "siguiente_racha",
        "description": "Ejecuta la siguiente racha del Authority Engine",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "estado_authority",
        "description": "Estado del Authority Engine",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "cerrar_jarvis",
        "description": "Cierra y apaga Jarvis cuando el usuario se despide o pide que se cierre/apague/desconecte",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "apagar_pc",
        "description": "Apaga o reinicia el ordenador. SOLO si el usuario dice explícitamente 'confirmo apagado del pc'",
        "input_schema": {"type": "object", "properties": {
            "accion": {"type": "string", "description": "apagar o reiniciar"}
        }}
    }
]

# ── SYSTEM PROMPT ─────────────────────────────────────────────────
SYSTEM = """Eres Jarvis, asistente personal de Joaquín Ríos Heredia.
Joaquín es ingeniero backend: Java 21, Spring Boot, Python, IA.

RUTAS:
- Descargas: E:\\descargas ryzen
- Home: C:\\Users\\Usuario
- Desktop: C:\\Users\\Usuario\\Desktop

COMPORTAMIENTO:
- Responde SIEMPRE en español castellano
- Respuestas cortas, máximo 2 frases
- Usa herramientas para CUALQUIER acción — nunca finjas haberla ejecutado
- Para música: usa reproducir_musica (reproduce con VLC, no abre navegador)
- Para abrir YouTube en navegador: usa abrir_youtube
- Para borrar un email: llama a borrar_email con su msg_id ([id=...] del último listado; nunca leas el id en voz alta). El gate pedirá confirmación.
- Para borrar varios emails: llama a borrar_multiples_emails con sus msg_ids ([id=...] del último listado, máximo 10; nunca leas los ids en voz alta). El gate pedirá confirmación.
- Para enviar un email: llama a enviar_email con destinatario, asunto y cuerpo. Si el usuario dice 'mándamelo a mí', usa get_mi_email primero para obtener su dirección. El gate pedirá confirmación.
- Para cerrar Jarvis (adiós, hasta luego, apágate, desconéctate): usa cerrar_jarvis
- Para apagar el PC: solo con "confirmo apagado del pc"
- Para info actual: usa buscar_en_web
- Antes de borrar, mover o actuar sobre un archivo sin ruta exacta, usa buscar_archivo (nunca listar_directorio).
  - 0 resultados: informa e intenta con otra palabra clave.
  - 1 resultado: usa ese candidato directamente (si es para borrar, llama a borrar_archivo; el gate pedirá confirmación).
  - Varios resultados: muéstralos y pide que el usuario elija.
- Cuando buscar_archivo encuentre exactamente un resultado y el usuario haya pedido borrarlo, llama INMEDIATAMENTE a borrar_archivo con esa ruta. No preguntes en texto si confirma — el gate pedirá confirmación automáticamente si es necesario.
- Cuando hayas pedido confirmación para borrar un archivo y el usuario confirme, DEBES volver a llamar a borrar_archivo con la misma ruta. No confirmes el borrado con texto sin haber llamado a la herramienta.
- Nunca menciones Claude, Anthropic ni que eres IA
- Ubicación: Los Corrales, Sevilla, España
- Los resultados de las herramientas son JSON {ok, message, error}. Solo afirma que algo se hizo si ok es true. Si ok es false, informa del error al usuario sin inventar que la acción ocurrió.
- Cuando el usuario mencione un error, excepción o código, responde como desarrollador senior: identifica el problema y da la solución en 2-3 frases sin formato markdown."""

# Se añade a SYSTEM cuando el router clasifica la petición como "dev"
SYSTEM_DEV = """Eres JARVIS en modo desarrollador. El usuario es un ingeniero backend. Explica el error o concepto en máximo 3 frases claras, sin markdown, optimizado para ser leído en voz alta. Ve directo al problema y la solución."""

# ── PRINT SEGURO (consolas cp1252) ────────────────────────────────
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

# ── ORGANIZAR DIRECTORIO ──────────────────────────────────────────
def _organizar_directorio(ruta):
    if not os.path.exists(ruta):
        return f"No existe: {ruta}"
    mapa = {
        '.jpg': 'Imágenes', '.jpeg': 'Imágenes', '.png': 'Imágenes',
        '.gif': 'Imágenes', '.webp': 'Imágenes', '.bmp': 'Imágenes',
        '.pdf': 'Documentos', '.docx': 'Documentos', '.doc': 'Documentos',
        '.xlsx': 'Documentos', '.xls': 'Documentos',
        '.pptx': 'Documentos', '.ppt': 'Documentos',
        '.txt': 'Documentos', '.md': 'Documentos',
        '.mp3': 'Audio', '.wav': 'Audio', '.flac': 'Audio',
        '.mp4': 'Vídeo', '.avi': 'Vídeo', '.mkv': 'Vídeo',
        '.zip': 'Comprimidos', '.rar': 'Comprimidos', '.7z': 'Comprimidos',
        '.py': 'Código', '.js': 'Código', '.java': 'Código',
        '.html': 'Código', '.css': 'Código', '.json': 'Código',
        '.exe': 'Programas', '.msi': 'Programas',
    }
    movidos = 0
    nuevas  = set()
    for f in os.listdir(ruta):
        src = os.path.join(ruta, f)
        if not os.path.isfile(src):
            continue
        _, ext  = os.path.splitext(f)
        carpeta = mapa.get(ext.lower(), 'Otros')
        dst_dir = os.path.join(ruta, carpeta)
        os.makedirs(dst_dir, exist_ok=True)
        nuevas.add(carpeta)
        try:
            shutil.move(src, os.path.join(dst_dir, f))
            movidos += 1
        except Exception:
            pass
    return f"Organizados {movidos} archivos. Carpetas: {', '.join(nuevas)}."

# ── FLAG DE CIERRE ────────────────────────────────────────────────
_cerrar = False

def debe_cerrar():
    return _cerrar

# ── PERMISOS ──────────────────────────────────────────────────────
TOOL_RISK = {
    # READ: no producen ningún efecto externo
    "buscar_en_web": "READ", "leer_emails": "READ", "leer_email": "READ",
    "buscar_emails": "READ", "emails_recruiters": "READ",
    "tiempo": "READ", "hora": "READ", "fecha": "READ",
    "estado_sistema": "READ", "listar_directorio": "READ",
    "buscar_archivo": "READ", "listar_tareas": "READ",
    "estado_authority": "READ", "get_mi_email": "READ",
    # WRITE: modifican estado interno de JARVIS (memoria, tareas, notas, ciclo de vida)
    "recordatorio": "WRITE", "nota": "WRITE", "cerrar_jarvis": "WRITE",
    # EXTERNAL: producen efecto fuera de JARVIS (archivos, email, sistema, audio)
    "reproducir_musica": "EXTERNAL", "parar_musica": "EXTERNAL",
    "abrir_youtube": "EXTERNAL", "abrir_web": "EXTERNAL", "abrir_app": "EXTERNAL",
    "organizar_directorio": "EXTERNAL", "crear_carpeta": "EXTERNAL",
    "mover_archivo": "EXTERNAL", "archivar_email": "EXTERNAL",
    "volumen_subir": "EXTERNAL", "volumen_bajar": "EXTERNAL",
    "pomodoro": "EXTERNAL", "siguiente_racha": "EXTERNAL",
    # DESTRUCTIVE: irreversible o requieren confirmación (ver _gate_core)
    "borrar_archivo": "DESTRUCTIVE", "apagar_pc": "DESTRUCTIVE",
    "borrar_email": "DESTRUCTIVE", "borrar_multiples_emails": "DESTRUCTIVE",
    "enviar_email": "DESTRUCTIVE",
}

ALLOWED_ROOTS   = [DESCARGAS, r"C:\Users\Usuario\Desktop"]
ORGANIZAR_ROOTS = [DESCARGAS]
BLOCKED_TREES   = [
    r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)",
    r"C:\ProgramData", r"C:\Users\Usuario\AppData", r"C:\Users\Usuario\OneDrive",
    r"C:\Jarvis", r"C:\Jarvis-secrets",
]
BLOCKED_EXACT   = [r"C:\Users\Usuario"]

CONFIRM_PHRASES   = {
    "apagar_pc":      "confirmo apagado del pc",
    "borrar_archivo": "confirmo borrado",
    "borrar_email":   "confirmo borrado de correo",
    "borrar_multiples_emails": "confirmo borrado de correos",
    "enviar_email":   "confirmo envío",
}
CONFIRM_TTL       = 120
MAX_BORRAR_EMAILS = 10
MAX_ADJUNTO       = 20 * 1024 * 1024  # bytes; mismo límite que gmail.MAX_ADJUNTO
# Fase 5D: solo la dirección propia (gmail.get_mi_email). Vacía = ningún envío.
DESTINATARIOS_PERMITIDOS = []

# Última acción destructiva bloqueada: {"firma": (...), "ts": monotonic}
_pendiente = None


def _real(ruta):
    return os.path.normcase(os.path.realpath(ruta))


def _dentro(ruta, base):
    try:
        return os.path.commonpath([ruta, base]) == base
    except ValueError:  # otra unidad, UNC, etc.
        return False


def _validate_path(ruta, roots=None, permitir_raiz=False):
    """(True, ruta_real) si la ruta es segura; (False, motivo) si no."""
    roots = ALLOWED_ROOTS if roots is None else roots
    if not isinstance(ruta, str) or not ruta.strip():
        return False, "ruta vacía o no válida"
    if "\x00" in ruta:
        return False, "ruta con caracteres no válidos"
    if ".." in re.split(r"[\\/]+", ruta):
        return False, f"traversal (..) no permitido: {ruta}"
    if ruta.startswith(("\\\\", "//")):
        return False, f"rutas de red/UNC no permitidas: {ruta}"

    real = _real(ruta)
    drive, resto = os.path.splitdrive(real)
    if not resto.strip("\\/"):
        return False, f"raíz de unidad no permitida: {ruta}"
    if real in [_real(b) for b in BLOCKED_EXACT]:
        return False, f"ruta protegida: {ruta}"
    for b in BLOCKED_TREES:
        if _dentro(real, _real(b)):
            return False, f"zona del sistema o de Jarvis protegida: {ruta}"
    for root in roots:
        rr = _real(root)
        if real == rr:
            if permitir_raiz:
                return True, real
            return False, f"no se puede tocar la carpeta raíz {root} en sí, solo su contenido"
        if _dentro(real, rr):
            return True, real
    return False, f"fuera de las zonas permitidas ({', '.join(roots)}): {ruta}"


def _gate_rutas(nombre, parametros):
    if nombre == "borrar_archivo":
        chequeos = [(parametros.get('ruta', ''), ALLOWED_ROOTS, False)]
    elif nombre == "mover_archivo":
        chequeos = [(parametros.get('origen', ''), ALLOWED_ROOTS, False),
                    (parametros.get('destino', ''), ALLOWED_ROOTS, True)]
    elif nombre == "organizar_directorio":
        chequeos = [(parametros.get('ruta', DESCARGAS), ORGANIZAR_ROOTS, True)]
    elif nombre == "crear_carpeta":
        chequeos = [(parametros.get('ruta', ''), ALLOWED_ROOTS, True)]
    elif nombre == "enviar_email" and parametros.get('adjunto'):
        chequeos = [(parametros.get('adjunto'), ALLOWED_ROOTS, False)]
    else:
        return None
    for ruta, roots, permitir_raiz in chequeos:
        ok, info = _validate_path(ruta, roots, permitir_raiz)
        if not ok:
            return f"BLOQUEADO por seguridad ({nombre}): {info}."
    return None


def _normalizar(texto):
    t = unicodedata.normalize("NFD", (texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


def _frase_confirmacion(texto_usuario):
    """Nombre de la tool cuya frase ES el enunciado completo (o None)."""
    texto_norm = _normalizar(texto_usuario).replace(" ", "")
    for tool, frase in CONFIRM_PHRASES.items():
        frase_norm = _normalizar(frase).replace(" ", "")
        if texto_norm in (frase_norm, "jarvis" + frase_norm):
            return tool
    return None


def _firma(nombre, parametros):
    if nombre == "apagar_pc":  # mismo criterio que el dispatcher
        return (nombre, 'reiniciar' if parametros.get('accion', 'apagar') == 'reiniciar' else 'apagar')
    if nombre == "borrar_archivo":
        return (nombre, _real(parametros.get('ruta', '')))
    if nombre == "borrar_email":  # el ID real de Gmail, nunca la posición en la lista
        return (nombre, parametros.get("msg_id", ""))
    if nombre == "borrar_multiples_emails":  # mismo conjunto de IDs = misma firma, sin importar el orden
        ids = parametros.get("msg_ids")
        return (nombre, tuple(sorted(set(ids if isinstance(ids, list) else []))))
    if nombre == "enviar_email":  # lo que el usuario confirma en voz: a quién y qué archivo
        adjunto = parametros.get("adjunto")
        return (nombre, (parametros.get("destinatario") or "").strip().lower(),
                _real(adjunto) if adjunto else "")
    return (nombre, repr(sorted(parametros.items())))


def _resumen_remitentes(entradas):
    """'3 emails de David Pérez' o '3 emails: 2 de David Pérez y 1 de Ana'
    (nombre visible del From, sin la dirección, para que se pueda leer en voz alta)."""
    cuenta = {}
    for e in entradas:
        nombre, direccion = parseaddr(e.get("remitente", ""))
        quien = nombre or direccion or e.get("remitente") or "desconocido"
        cuenta[quien] = cuenta.get(quien, 0) + 1
    total = len(entradas)
    emails = "email" if total == 1 else "emails"
    if len(cuenta) == 1:
        return f"{total} {emails} de {next(iter(cuenta))}"
    partes = [f"{n} de {quien}" for quien, n in cuenta.items()]
    return f"{total} {emails}: {', '.join(partes[:-1])} y {partes[-1]}"


def _gate_core(nombre, parametros, texto_usuario="", email=None):
    """None = permitido; str = mensaje de bloqueo (se devuelve al modelo).

    La confirmación se lee SOLO de texto_usuario (lo que transcribió Whisper
    del micro en este turno). El modelo no puede producirla: ni sus
    parámetros, ni sus respuestas, ni el contexto se miran.
    """
    global _pendiente
    riesgo = TOOL_RISK.get(nombre)
    if riesgo is None:
        return f"BLOQUEADO: la herramienta '{nombre}' no tiene nivel de riesgo asignado."

    bloqueo = _gate_rutas(nombre, parametros)
    if bloqueo:
        return bloqueo

    if nombre == "borrar_multiples_emails":
        # email = {"entradas", "faltan", "caducados"} de la caché de gmail (lo resuelve _ejecutar).
        # Cualquier fallo bloquea sin armar ni gastar ninguna confirmación.
        ids = parametros.get("msg_ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
            return "BLOQUEADO: borrar_multiples_emails necesita los msg_ids de emails listados. Lee primero los emails."
        if len(ids) > MAX_BORRAR_EMAILS:
            return f"BLOQUEADO: máximo {MAX_BORRAR_EMAILS} emails por operación (pedidos: {len(ids)})."
        if len(set(ids)) != len(ids):
            return "BLOQUEADO: hay msg_ids repetidos. Pasa cada email una sola vez."
        if not email or email["faltan"]:
            return "BLOQUEADO: algún email no está en la última lista leída. Lee primero los emails."
        if email["caducados"]:
            return "BLOQUEADO: la lista de emails está desactualizada. Lee los emails de nuevo."

    if nombre == "borrar_email":
        # email = entrada de la caché de gmail para ese msg_id (la resuelve _ejecutar).
        # Sin ella no se arma ni se gasta ninguna confirmación.
        if not parametros.get("msg_id"):
            return "BLOQUEADO: borrar_email necesita el msg_id de un email listado. Lee primero los emails."
        if not email:
            return "BLOQUEADO: ese email no está en la última lista leída. Lee primero los emails."

    if nombre == "enviar_email":
        # Antes de armar nada: un email leído (inyección) no puede dirigir un envío fuera
        destinatario = (parametros.get("destinatario") or "").strip().lower()
        if destinatario not in DESTINATARIOS_PERMITIDOS:
            return "BLOQUEADO: solo puedes enviar emails a tu propia dirección por ahora."
        # La ruta del adjunto ya pasó _gate_rutas (allowlist); aquí existencia y tamaño
        adjunto = parametros.get("adjunto")
        if adjunto:
            try:
                tam = os.path.getsize(adjunto)
            except OSError:
                return f"BLOQUEADO: no existe el adjunto {adjunto}. Búscalo con buscar_archivo."
            if tam > MAX_ADJUNTO:
                return f"BLOQUEADO: el adjunto supera 20 MB ({tam // (1024 * 1024)} MB)."

    if riesgo != "DESTRUCTIVE":
        return None

    if nombre not in CONFIRM_PHRASES:
        # Antes de tocar _pendiente: no arma ni gasta ninguna confirmación.
        return (f"BLOQUEADO: '{nombre}' está clasificada como DESTRUCTIVE pero no tiene "
                f"frase de confirmación definida (CONFIRM_PHRASES); no se ejecuta.")

    firma    = _firma(nombre, parametros)
    ahora    = time.monotonic()
    pend     = _pendiente
    dicha    = _frase_confirmacion(texto_usuario)

    if dicha is not None:
        # Este enunciado de confirmación se gasta, coincida o no. Nunca
        # se crea una acción pendiente en un turno de confirmación.
        _pendiente = None
        if dicha == nombre and pend and pend["firma"] == firma and ahora - pend["ts"] <= CONFIRM_TTL:
            return None
        return ("BLOQUEADO: la confirmación no corresponde a una acción pendiente vigente "
                "(distinta acción, ya usada o caducada). Pide de nuevo la acción al usuario.")

    _pendiente = {"firma": firma, "ts": ahora}
    if nombre == "enviar_email":
        # La firma no lleva asunto/cuerpo; sin esto, el D1-fix (que ejecuta
        # pendiente_info()["params"] sin modelo) enviaría el email vacío
        _pendiente["params"] = dict(parametros)
    frase = CONFIRM_PHRASES[nombre]
    if nombre == "borrar_email":
        objeto = f"email de {email['remitente']}: {email['asunto']}"
    elif nombre == "borrar_multiples_emails":
        objeto = _resumen_remitentes(email["entradas"])
    elif nombre == "enviar_email":
        objeto = f"Enviar email a {firma[1]} con asunto {parametros.get('asunto', '')}"
        if firma[2]:
            objeto += f" y adjunto {os.path.basename(parametros['adjunto'])}"
    else:
        objeto = firma[1]
    return (f"BLOQUEADO: '{nombre}' ({objeto}) es irreversible y requiere confirmación del usuario. "
            f"Dile exactamente qué se va a hacer y que responda solo: \"{frase}\". "
            f"Caduca en {CONFIRM_TTL} s. No repitas la llamada hasta entonces.")


def hay_pendiente():
    return _pendiente is not None and time.monotonic() - _pendiente["ts"] < CONFIRM_TTL


def _params_desde_firma(firma):
    """_pendiente solo guarda la firma; los params se reconstruyen de ella de
    forma que _firma(nombre, params) == firma y el gate la reconozca."""
    if firma[0] == "borrar_archivo":
        return {"ruta": firma[1]}
    if firma[0] == "apagar_pc":
        return {"accion": firma[1]}
    if firma[0] == "borrar_email":
        return {"msg_id": firma[1]}
    if firma[0] == "borrar_multiples_emails":
        return {"msg_ids": list(firma[1])}
    if firma[0] == "enviar_email":
        return {"destinatario": firma[1], "adjunto": firma[2] or None}
    return {}


def pendiente_info():
    """Devuelve {"tool": str, "params": dict, "firma": tuple} o None."""
    if not _pendiente:
        return None
    return {
        "tool":   _pendiente["firma"][0],
        "params": _pendiente.get("params") or _params_desde_firma(_pendiente["firma"]),
        "firma":  _pendiente["firma"]
    }


def cargar_destinatarios_permitidos(skills):
    """Rellena DESTINATARIOS_PERMITIDOS con la dirección propia (gmail cachea
    getProfile). Si falla, la lista sigue vacía y todo envío queda bloqueado."""
    if DESTINATARIOS_PERMITIDOS:
        return
    try:
        mi_email = skills.get('gmail').get_mi_email()
    except Exception:
        return
    if isinstance(mi_email, str) and "@" in mi_email:
        DESTINATARIOS_PERMITIDOS.append(mi_email.strip().lower())


def anuncio_confirmacion():
    """Texto fijo (no del modelo) para leer en voz alta si este turno dejó un
    enviar_email pendiente de confirmar; None si no. Sale de _pendiente, que
    es exactamente lo que se ejecutará al confirmar."""
    traza = _ultima_traza or {}
    if not hay_pendiente() or _pendiente["firma"][0] != "enviar_email":
        return None
    if not any(t.get("nombre") == "enviar_email" for t in traza.get("tools_ejecutadas") or []):
        return None
    destinatario = _pendiente["firma"][1]
    adjunto      = (_pendiente.get("params") or {}).get("adjunto")
    con_adjunto  = f" con el archivo {os.path.basename(adjunto)}" if adjunto else ""
    frase        = CONFIRM_PHRASES["enviar_email"]
    return f"Vas a enviar un email a {destinatario}{con_adjunto}. ¿Confirmas con '{frase}'?"


def consumir_pendiente():
    """Limpia _pendiente. Equivalente a consumirlo tras ejecución directa."""
    global _pendiente
    _pendiente = None


def _gate(nombre, parametros, texto_usuario="", email=None):
    edad = f" pendiente_edad={int(time.monotonic() - _pendiente['ts'])}s" if _pendiente else ""
    _print_seguro(f"[GATE] tool={nombre} nivel={TOOL_RISK.get(nombre, 'DESCONOCIDO')}{edad}")
    resultado = _gate_core(nombre, parametros, texto_usuario, email)
    if resultado:
        _print_seguro(f"[GATE BLOQUEADO] {resultado}")
    else:
        _print_seguro("[GATE OK]")
    return resultado


# ── CONTRATO ESTRUCTURADO DE RESULTADO ───────────────────────────
def _ok(message):
    return {"ok": True, "message": message, "error": None}


def _err(message, code):
    return {"ok": False, "message": message, "error": code}


def _serializar(res):
    return json.dumps({"ok": res.get("ok"), "message": res.get("message"), "error": res.get("error")},
                       ensure_ascii=False)


def _envolver(texto):
    """dict para ramas no migradas al contrato _ok/_err: ok=False solo si el
    texto empieza por 'BLOQUEADO' o 'Error en'; ok=True en el resto."""
    if isinstance(texto, str) and (texto.startswith("BLOQUEADO") or texto.startswith("Error en")):
        return _err(texto, None)
    return _ok(texto)


# ── EJECUTOR ──────────────────────────────────────────────────────
def _email_en_cache(nombre, parametros, skills):
    """Entrada de la caché de gmail para borrar_email (o None). Da remitente
    y asunto al mensaje del gate y prueba que el msg_id salió de un listado.
    Para borrar_multiples_emails: {"entradas", "faltan", "caducados"} (o None)."""
    if nombre == "borrar_multiples_emails":
        return _emails_en_cache(parametros, skills)
    if nombre != "borrar_email":
        return None
    try:
        email = skills.get('gmail').info_email(parametros.get("msg_id", ""))
    except Exception:
        return None
    return email if isinstance(email, dict) else None


def _emails_en_cache(parametros, skills):
    """Clasifica los msg_ids contra la caché de gmail. La caducidad usa
    gmail.CACHE_TTL (única fuente); si no es un número, falla cerrado (None)."""
    ids = parametros.get("msg_ids")
    if not isinstance(ids, list):
        return None
    try:
        gmail = skills.get('gmail')
        ttl   = gmail.CACHE_TTL
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
            return None
        ahora = time.time()
        res   = {"entradas": [], "faltan": [], "caducados": []}
        for msg_id in ids:
            e = gmail.info_email(msg_id) if isinstance(msg_id, str) else None
            if not isinstance(e, dict):
                res["faltan"].append(msg_id)
                continue
            if ahora - e.get('ts', 0) > ttl:
                res["caducados"].append(msg_id)
            res["entradas"].append(e)
        return res
    except Exception:
        return None


def _ejecutar(nombre, parametros, skills, texto_usuario=""):
    global _cerrar
    try:
        if nombre == "enviar_email":
            cargar_destinatarios_permitidos(skills)  # por si falló al arrancar
        bloqueo = _gate(nombre, parametros, texto_usuario, _email_en_cache(nombre, parametros, skills))
        if bloqueo:
            return _envolver(bloqueo)

        musica    = skills.get('musica')
        sistema   = skills.get('sistema')
        webs      = skills.get('webs')
        authority = skills.get('authority')
        tareas    = skills.get('tareas')
        gmail     = skills.get('gmail')
        memoria   = skills.get('memoria')
        audio     = skills.get('audio')

        # ── MÚSICA ────────────────────────────────────────────────
        if nombre == "reproducir_musica":
            query = parametros.get('query', '')
            try:
                musica.reproducir(query)
            except Exception as e:
                return _err(f"No se pudo iniciar la búsqueda de audio: {e}", "fallo_subproceso")
            return _ok(f"Búsqueda de audio iniciada: {query}")

        if nombre == "parar_musica":
            musica.parar()
            return _envolver("Música parada")

        # ── WEB / APPS ────────────────────────────────────────────
        if nombre == "abrir_youtube":
            query = parametros.get('query', '')
            url   = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
            webbrowser.open(url)
            return _envolver(f"YouTube abierto: {query}")

        if nombre == "abrir_web":
            return _envolver(webs.abrir_url(parametros.get('sitio', '')) or "No conozco ese sitio")

        if nombre == "abrir_app":
            return _envolver(webs.abrir_app(parametros.get('app', '')) or "No conozco esa app")

        if nombre == "buscar_en_web":
            _print_seguro(f"🌐 Buscando: {parametros.get('query', '')}")
            return _envolver(webs.buscar_en_web(parametros.get('query', '')))

        # ── GMAIL ─────────────────────────────────────────────────
        if nombre == "leer_emails":
            return _envolver(gmail.leer_no_leidos())

        if nombre == "leer_email":
            return _envolver(gmail.leer_email(parametros.get('indice', 0)))

        if nombre == "borrar_email":
            ok, msg = gmail.borrar_email(parametros.get("msg_id", ""))
            return _ok(msg) if ok else _err(msg, "borrado_rechazado")

        if nombre == "borrar_multiples_emails":
            ok, msg = gmail.borrar_multiples(parametros.get("msg_ids", []))
            return _ok(msg) if ok else _err(msg, "borrado_rechazado")

        if nombre == "enviar_email":
            ok, msg = gmail.enviar_email(parametros.get("destinatario", ""), parametros.get("asunto", ""),
                                         parametros.get("cuerpo", ""), parametros.get("adjunto") or None)
            return _ok(msg) if ok else _err(msg, "envio_rechazado")

        if nombre == "get_mi_email":
            mi_email = gmail.get_mi_email()
            return _ok(mi_email) if mi_email else _err("No pude obtener tu dirección de email", "no_disponible")

        if nombre == "archivar_email":
            return _envolver(gmail.archivar_email(parametros.get('indice', 0)))

        if nombre == "buscar_emails":
            return _envolver(gmail.buscar_emails(parametros.get('query', '')))

        if nombre == "emails_recruiters":
            return _envolver(gmail.leer_recruiters())

        # ── SISTEMA ───────────────────────────────────────────────
        if nombre == "tiempo":
            return _envolver(sistema.tiempo_sevilla())

        if nombre == "hora":
            return _envolver(sistema.hora_actual())

        if nombre == "fecha":
            return _envolver(sistema.fecha_actual())

        if nombre == "estado_sistema":
            return _envolver(sistema.estado_completo())

        if nombre in ("volumen_subir", "volumen_bajar"):
            subir = nombre == "volumen_subir"
            try:
                antes = audio.get_volumen()
                audio.set_volumen(min(100, antes + 10) if subir else max(0, antes - 10))
                despues = audio.get_volumen()
            except Exception:
                return _err("Control de volumen no disponible", "no_disponible")
            # audio.get_volumen() devuelve el sentinel fijo 50 cuando no hay
            # control de audio disponible: si no cambia y coincide con él, es
            # la señal de que set_volumen() no hizo nada.
            if antes == despues == 50:
                return _err("Control de volumen no disponible", "no_disponible")
            return _ok("Volumen subido" if subir else "Volumen bajado")

        # ── ARCHIVOS ──────────────────────────────────────────────
        if nombre == "listar_directorio":
            ruta = parametros.get('ruta', DESCARGAS)
            if not os.path.exists(ruta):
                return _envolver(f"No existe: {ruta}")
            items    = os.listdir(ruta)
            archivos = [f for f in items if os.path.isfile(os.path.join(ruta, f))]
            carpetas = [f for f in items if os.path.isdir(os.path.join(ruta, f))]
            return _envolver(f"Carpetas: {', '.join(carpetas[:10]) or 'ninguna'}. Archivos: {', '.join(archivos[:20]) or 'ninguno'}.")

        if nombre == "organizar_directorio":
            return _envolver(_organizar_directorio(parametros.get('ruta', DESCARGAS)))

        if nombre == "crear_carpeta":
            ruta = parametros.get('ruta', '')
            os.makedirs(ruta, exist_ok=True)
            return _envolver(f"Carpeta creada: {ruta}")

        if nombre == "mover_archivo":
            origen  = parametros.get('origen', '')
            destino = parametros.get('destino', '')
            if not os.path.exists(origen):
                return _envolver(f"No existe: {origen}")
            shutil.move(origen, destino)
            return _envolver(f"Movido: {os.path.basename(origen)}")

        if nombre == "borrar_archivo":
            ruta = parametros.get('ruta', '')
            if not os.path.exists(ruta):
                return _envolver(f"No existe: {ruta}")
            shutil.rmtree(ruta) if os.path.isdir(ruta) else os.remove(ruta)
            return _envolver(f"Eliminado: {ruta}")

        if nombre == "buscar_archivo":
            nombre_f   = parametros.get('nombre', '')
            directorio = parametros.get('directorio', DESCARGAS)
            encontrados = []
            for root, dirs, files in os.walk(directorio):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for f in files:
                    if nombre_f.lower() in f.lower():
                        encontrados.append(os.path.join(root, f))
                if len(encontrados) >= 5:
                    break
            return _envolver("Encontrados: " + ", ".join(encontrados) if encontrados else f"No encontré '{nombre_f}'")

        # ── TAREAS ────────────────────────────────────────────────
        if nombre == "recordatorio":
            return _envolver(tareas.añadir_recordatorio(memoria, audio.hablar, parametros.get('tarea', ''), hora_str=parametros.get('hora', '')))

        if nombre == "nota":
            return _envolver(tareas.añadir_nota_voz(memoria, parametros.get('texto', '')))

        if nombre == "listar_tareas":
            return _envolver(tareas.listar_tareas(memoria))

        if nombre == "pomodoro":
            return _envolver(tareas.pomodoro(audio.hablar, parametros.get('minutos', 25)))

        # ── AUTHORITY ─────────────────────────────────────────────
        if nombre == "siguiente_racha":
            resultado = authority.siguiente_racha()
            if isinstance(resultado, str) and resultado.startswith("Lanzando racha sobre"):
                return _ok(resultado)
            return _err(resultado, "fallo_subproceso")

        if nombre == "estado_authority":
            return _envolver(authority.estado_global())

        # ── CONTROL ───────────────────────────────────────────────
        if nombre == "cerrar_jarvis":
            _cerrar = True
            return _envolver("cerrando")

        if nombre == "apagar_pc":
            accion = parametros.get('accion', 'apagar')
            cmd = ['shutdown', '/r', '/t', '5'] if accion == 'reiniciar' else ['shutdown', '/s', '/t', '5']
            try:
                proceso = subprocess.run(cmd, capture_output=True, timeout=10)
            except Exception:
                return _err("No se pudo iniciar el apagado", "fallo_subproceso")
            if proceso.returncode != 0:
                return _err("No se pudo iniciar el apagado", "fallo_subproceso")
            return _ok("Comando de reinicio aceptado" if accion == 'reiniciar' else "Comando de apagado aceptado")

        return _envolver(f"Herramienta '{nombre}' no implementada")

    except Exception as e:
        return _envolver(f"Error en {nombre}: {str(e)}")


def ejecutar_herramienta(nombre, parametros, skills, texto_usuario=""):
    return _ejecutar(nombre, parametros, skills, texto_usuario)["message"]


# ── TRAZA DE EJECUCIÓN (solo observabilidad) ──────────────────────
_ultima_traza = None

def ultima_traza():
    return _ultima_traza


def _recortar(valor, n=200):
    try:
        s = valor if isinstance(valor, str) else repr(valor)
        return s if len(s) <= n else s[:n] + "..."
    except Exception:
        return "?"


def _reg_iteracion(traza, i, respuesta):
    try:
        uso = getattr(respuesta, "usage", None)
        traza["iteraciones"].append({
            "i":          i,
            "stop_reason": respuesta.stop_reason,
            "n_tool_use": sum(1 for b in respuesta.content if getattr(b, "type", None) == "tool_use"),
            "tokens_in":  getattr(uso, "input_tokens", None),
            "tokens_out": getattr(uso, "output_tokens", None),
        })
    except Exception:
        pass


def _reg_tool(traza, i, bloque, stop_reason, resultado, ms):
    try:
        try:
            entrada = dict(bloque.input)
        except Exception:
            entrada = bloque.input
        traza["tools_ejecutadas"].append({
            "i": i, "tool_use_id": bloque.id, "stop_reason": stop_reason,
            "nombre": bloque.name, "input": entrada, "resultado": resultado, "ms": ms,
        })
    except Exception:
        pass


def _log_traza(traza):
    try:
        nombres = ",".join(t["nombre"] for t in traza["tools_ejecutadas"])
        _print_seguro(f"[TRAZA] iters={len(traza['iteraciones'])} stop={traza['stop_final']} "
                      f"tools=[{nombres}] resp_len={len(traza['respuesta'] or '')} t={traza['t_s']:.1f}s")
        for t in traza["tools_ejecutadas"]:
            _print_seguro(f"[TRAZA+] i={t['i']} tool={t['nombre']} ms={t['ms']} "
                          f"input={_recortar(t['input'])} resultado={_recortar(t['resultado'])}")
    except Exception:
        pass


def _detectar_d1(traza, pendiente_firma, texto_usuario):
    """True si había una acción pendiente al inicio del turno, el texto del
    usuario es una frase de confirmación válida, y la tool pendiente no
    aparece entre las ejecutadas en este turno. Solo detección (Fase 2D):
    no ejecuta nada ni cambia el comportamiento del gate."""
    if not pendiente_firma:
        return False
    if _frase_confirmacion(texto_usuario) is None:
        return False
    ejecutadas = {t.get("nombre") for t in (traza.get("tools_ejecutadas") or [])}
    return pendiente_firma[0] not in ejecutadas


# ── ROUTER EN 3 CAPAS: atajo (sin modelo) / local (Ollama) / claude ──
# Ante la duda, siempre "claude": es la única capa con tools y gate completo.

# Frases (normalizadas, sin tildes) que disparan una tool directa
ATAJOS = [
    (("que hora", "la hora", "hora"),                               "hora"),
    (("a que dia estamos", "que dia", "que fecha", "fecha"),        "fecha"),
    (("que tiempo hace", "tiempo", "clima", "temperatura"),         "tiempo"),
    (("sube el volumen", "sube volumen", "mas volumen"),            "volumen_subir"),
    (("baja el volumen", "baja volumen", "menos volumen"),          "volumen_bajar"),
    (("para la musica", "para musica", "stop musica"),              "parar_musica"),
    (("lista de tareas", "lista tareas", "mis tareas", "que tengo"), "listar_tareas"),
    (("cierra jarvis", "cierra", "apagate"),                        "cerrar_jarvis"),
]
# Lo único que puede acompañar a la frase del atajo; cualquier otra palabra
# ("a qué hora sale el tren", "cierra el navegador") → claude
RELLENO_ATAJO = {
    "es", "son", "hoy", "ahora", "ya", "jarvis", "oye", "por", "favor", "porfa",
    "un", "poco", "mas", "el", "la", "los", "las", "de", "del", "en", "me", "dime",
    "que", "hace", "estamos", "sevilla", "actual", "pendientes", "vale",
}
# Raíces de verbos de acción (cualquier conjugación) → claude
_RE_ACCION = re.compile(
    r"\b(borr|elimin|quit|muev|mov|busc|encuentr|lee|leer|lei|mand|envi|respond|contest|"
    r"abr|cre[ao]|crear|organiz|apag|encend|reproduc|pon|escuch|archiv|anot|apunt|"
    r"recuerd|record|guard|instal|ejecut|cierr|cerr|sub[ei]|baj[ao]|descarg|copi|"
    r"renombr|haz|hazlo|confirm|cancel)\w*")
# Temas que requieren tools, datos actuales o la persona de JARVIS → claude
_RE_DOMINIO = re.compile(
    r"\b(emails?|correos?|gmail|archivos?|ficheros?|carpetas?|descargas|escritorio|"
    r"musica|cancion\w*|youtube|web|pc|ordenador|volumen|tareas?|recordatorios?|notas?|"
    r"pomodoro|racha|authority|noticias|hoy|ahora|actual\w*|precio\w*|ultim\w*|"
    r"jarvis|eres|llamas|nombre|ia|inteligencia|claude|anthropic|qwen|modelo)\b")
# Modo desarrollador: errores, código o preguntas técnicas → claude con SYSTEM_DEV
# Fuertes: bastan solas. Débiles: ambiguas ("clase de vino", "500 euros"),
# necesitan otra débil o un inicio técnico.
_RE_DEV_FUERTE = re.compile(
    r"\b(exception\w*|excepcion\w*|traceback|stack ?trace|null ?pointer\w*|import ?error|"
    r"syntax ?error|bugs?|falla mi|error en mi|por que falla)\b")
_RE_DEV_DEBIL = re.compile(
    r"\b(clases?|lineas?|errore?s?|funcion(?:es)?|metodos?|codigos?|404|500)\b")
_RE_DEV_INICIO = re.compile(r"^(oye )?(por que|como funciona|que hace|explica\w*)\b")
_RE_TECNICO = re.compile(
    r"\b(api|rest|http|endpoint|java|jvm|spring|python|kafka|docker|kubernetes|k8s|sql|query|"
    r"json|git|maven|gradle|thread|hilo|lambda|stream|null|compila\w*|servidor|backend|"
    r"microservicio\w*|base de datos|cache|transaccion\w*|async\w*|concurrencia)\b")
# La capa local solo acepta preguntas/explicaciones que empiezan así
_RE_PREGUNTA = re.compile(
    r"^(oye )?(que|quien|quienes|como|por que|porque|cual|cuales|cuanto|cuanta|cuantos|"
    r"cuantas|cuando|donde|explica\w*|define|definicion|significa|diferencia|hola|buenas|gracias)\b")


def _match_atajo(t):
    """Nombre de la tool si el texto normalizado es solo un atajo + relleno."""
    for frases, tool in ATAJOS:
        for frase in frases:
            if re.search(rf"\b{frase}\b", t):
                resto = re.sub(rf"\b{frase}\b", " ", t, count=1).split()
                if all(p in RELLENO_ATAJO for p in resto):
                    return tool
    return None


def _es_pregunta_dev(texto):
    """True si habla de un error/código o pide explicar algo técnico:
    1 keyword fuerte, o 2 débiles distintas, o 1 débil + inicio técnico,
    o inicio técnico + término técnico ("explícame cómo funciona Kafka")."""
    t = _normalizar(texto)
    if _RE_DEV_FUERTE.search(t):
        return True
    debiles = {m[:4] for m in _RE_DEV_DEBIL.findall(t)}  # "error"/"errores" = 1
    if len(debiles) >= 2:
        return True
    inicio = _RE_DEV_INICIO.match(t)
    return bool(inicio and (debiles or _RE_TECNICO.search(t)))


def _clasificar_peticion(texto):
    """"atajo" | "dev" | "local" | "claude". Reglas simples, sin modelo."""
    if hay_pendiente():
        return "claude"
    t = _normalizar(texto)
    if not t:
        return "claude"
    if _match_atajo(t):
        return "atajo"
    # Una acción ("borra el log de errores") es claude normal: SYSTEM_DEV
    # empuja a explicar en vez de usar la tool
    if _RE_ACCION.search(t):
        return "claude"
    if _es_pregunta_dev(texto):
        return "dev"
    if _RE_DOMINIO.search(t):
        return "claude"
    if _RE_PREGUNTA.match(t):
        return "local"
    return "claude"


def _ejecutar_atajo(nombre_tool, skills, texto_usuario=""):
    """Mensaje de la tool, o None si no se pudo (→ claude)."""
    try:
        res = _ejecutar(nombre_tool, {}, skills, texto_usuario)
        return res.get("message") or None
    except Exception as e:
        _print_seguro(f"[ROUTER] atajo {nombre_tool} falló: {e}")
        return None


def _limpiar_local(texto):
    """Respuesta apta para voz, o None (→ claude): sin caracteres no latinos
    (qwen a veces cambia al chino) y cortada en la última frase completa."""
    texto = (texto or "").strip()
    if not texto or any(ord(c) > 1000 for c in texto):
        return None
    fin = max(texto.rfind(p) for p in ".!?")
    return texto[:fin + 1].strip() if fin > 0 else None


def _llamar_ollama(texto):
    """Respuesta de qwen sin tools, o None si Ollama falla, tarda o la
    respuesta no es usable (→ claude)."""
    try:
        r = requests.post(f"{OLLAMA_URL}/api/generate", json={
            "model":      OLLAMA_MODEL,
            "prompt":     ("Eres JARVIS. Responde SOLO en español. Máximo 2 frases completas. "
                           "No uses caracteres que no sean letras españolas. "
                           f"Pregunta: {texto}"),
            "stream":     False,
            "keep_alive": OLLAMA_KEEP,
            "options":    {"num_predict": 80},
        }, timeout=OLLAMA_TIMEOUT)
        r.raise_for_status()
        respuesta = _limpiar_local(r.json().get("response"))
        if respuesta is None:
            _print_seguro("[ROUTER] respuesta local descartada (idioma o frase incompleta)")
        return respuesta
    except Exception as e:
        _print_seguro(f"[ROUTER] ollama no disponible: {e}")
        return None


def _precargar_ollama():
    """Carga el modelo en VRAM para que la primera pregunta local no pague
    la carga. urllib (no requests) para no interferir con mocks de tests."""
    try:
        cuerpo = json.dumps({"model": OLLAMA_MODEL, "prompt": "", "stream": False,
                             "keep_alive": OLLAMA_KEEP, "options": {"num_predict": 1}}).encode()
        req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=cuerpo,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=60).close()
    except Exception:
        pass


threading.Thread(target=_precargar_ollama, daemon=True, name="precarga-ollama").start()


def _enrutar(texto, skills, traza):
    """Respuesta de las capas atajo/local, o None para seguir con Claude."""
    if not skills:  # sin skills (tests, compatibilidad) → comportamiento previo
        return None
    clase = _clasificar_peticion(texto)
    traza["router"] = clase
    if clase == "dev":
        _print_seguro("[ROUTER] dev")
        return None
    if clase == "atajo":
        tool_atajo = _match_atajo(_normalizar(texto))
        _print_seguro(f"[ROUTER] atajo={tool_atajo}")
        t_tool    = time.monotonic()
        resultado = _ejecutar_atajo(tool_atajo, skills, texto)
        if resultado:
            traza["tools_ejecutadas"].append({
                "i": 0, "tool_use_id": None, "stop_reason": "atajo", "nombre": tool_atajo,
                "input": {}, "resultado": resultado, "ms": int((time.monotonic() - t_tool) * 1000)})
            traza["stop_final"] = "atajo"
            return resultado
    elif clase == "local":
        _print_seguro("[ROUTER] local")
        resultado = _llamar_ollama(texto)
        if resultado:
            traza["stop_final"] = "local"
            return resultado
    _print_seguro("[ROUTER] claude")
    return None


# ── AGENTE PRINCIPAL ──────────────────────────────────────────────
def pensar(texto, contexto="", skills=None):
    global _ultima_traza
    t0              = time.monotonic()
    pendiente_ahora = hay_pendiente()
    pendiente_firma = _pendiente["firma"] if pendiente_ahora else None
    traza = {"texto": texto, "pendiente_al_inicio": pendiente_ahora, "iteraciones": [],
             "tools_ejecutadas": [], "stop_final": None, "respuesta": None}
    try:
        traza["respuesta"] = _pensar_impl(texto, contexto, skills, traza)
        return traza["respuesta"]
    finally:
        traza["stop_final"]   = traza["stop_final"] or "excepcion"
        traza["t_s"]          = time.monotonic() - t0
        traza["d1_detectado"] = _detectar_d1(traza, pendiente_firma, texto)
        if traza["d1_detectado"]:
            _print_seguro(f"[D1] pendiente no ejecutado: tool={pendiente_firma[0]} ruta={pendiente_firma[1]}")
        _ultima_traza = traza
        _log_traza(traza)


def _pensar_impl(texto, contexto, skills, traza):
    global _cerrar
    _cerrar = False

    if skills is None:
        skills = {}

    respuesta_router = _enrutar(texto, skills, traza)
    if respuesta_router:
        return respuesta_router
    system = SYSTEM + "\n\n" + SYSTEM_DEV if traza.get("router") == "dev" else SYSTEM

    messages = [{"role": "user", "content": texto}]
    if contexto:
        messages[0]["content"] = f"[Contexto: {contexto}]\n\n{texto}"

    for i in range(20):
        try:
            respuesta = cliente.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=500,
                system=system,
                tools=TOOLS,
                messages=messages
            )
        except Exception as e:
            _print_seguro(f"⚠️ Claude API error: {e}")
            traza["stop_final"] = "api_error"
            return "No puedo responder ahora mismo."

        _reg_iteracion(traza, i, respuesta)

        if respuesta.stop_reason == "end_turn":
            traza["stop_final"] = "end_turn"
            for bloque in respuesta.content:
                if hasattr(bloque, 'text'):
                    return bloque.text.strip()
            return "Entendido."

        if respuesta.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": respuesta.content})
            resultados = []
            for bloque in respuesta.content:
                if bloque.type == "tool_use":
                    _print_seguro(f"🔧 {bloque.name} {bloque.input}")
                    t_tool    = time.monotonic()
                    res       = _ejecutar(bloque.name, bloque.input, skills, texto)
                    resultado = res.get("message")
                    _reg_tool(traza, i, bloque, respuesta.stop_reason, resultado,
                              int((time.monotonic() - t_tool) * 1000))
                    _print_seguro(f"   → {resultado}")
                    bloque_resultado = {
                        "type":        "tool_result",
                        "tool_use_id": bloque.id,
                        "content":     _serializar(res)
                    }
                    if not res.get("ok", True):
                        bloque_resultado["is_error"] = True
                    resultados.append(bloque_resultado)
            messages.append({"role": "user", "content": resultados})
        else:
            traza["stop_final"] = respuesta.stop_reason
            break

    traza["stop_final"] = traza["stop_final"] or "max_iter"
    return "No he podido completar la tarea."


# ── COMPATIBILIDAD ────────────────────────────────────────────────
def interpretar_intencion(texto, contexto=""):
    return {"action": "ninguno"}