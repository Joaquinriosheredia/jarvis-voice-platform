import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
import webbrowser
import urllib.parse
from email.utils import parseaddr
import anthropic

# ── CLIENTE ───────────────────────────────────────────────────────
cliente = anthropic.Anthropic()
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
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
        "description": "Busca información actual en internet con DuckDuckGo. Úsala para noticias, precios, datos actualizados, cualquier pregunta sobre el mundo real.",
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
- Los resultados de las herramientas son JSON {ok, message, error}. Solo afirma que algo se hizo si ok es true. Si ok es false, informa del error al usuario sin inventar que la acción ocurrió."""

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

# ── BÚSQUEDA WEB ──────────────────────────────────────────────────
def _buscar_web(query):
    try:
        import urllib.request
        import json as _json
        q   = urllib.parse.quote(query)
        url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, headers={'User-Agent': 'Jarvis/1.0'})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = _json.loads(r.read().decode())
        if data.get('AbstractText'):
            return data['AbstractText'][:500]
        textos = [r['Text'] for r in data.get('RelatedTopics', [])[:3]
                  if isinstance(r, dict) and r.get('Text')]
        return ' | '.join(textos)[:500] if textos else f"Sin resultados para: {query}"
    except Exception as e:
        return f"Error web: {e}"

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
    "estado_authority": "READ",
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
}
CONFIRM_TTL       = 120
MAX_BORRAR_EMAILS = 10

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
    frase = CONFIRM_PHRASES[nombre]
    if nombre == "borrar_email":
        objeto = f"email de {email['remitente']}: {email['asunto']}"
    elif nombre == "borrar_multiples_emails":
        objeto = _resumen_remitentes(email["entradas"])
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
            return _envolver(_buscar_web(parametros.get('query', '')))

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

    messages = [{"role": "user", "content": texto}]
    if contexto:
        messages[0]["content"] = f"[Contexto: {contexto}]\n\n{texto}"

    for i in range(20):
        try:
            respuesta = cliente.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=500,
                system=SYSTEM,
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