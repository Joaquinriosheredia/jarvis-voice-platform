import os
import re
import time
import base64
import json
import difflib
import unicodedata

import rutas
from email.message import EmailMessage
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES           = ['https://www.googleapis.com/auth/gmail.modify']
CREDENTIALS_FILE = rutas.en_secretos("credentials.json")
TOKEN_FILE       = rutas.en_secretos("token.json")

# Entradas {"id", "remitente", "asunto", "ts"} de la última lista leída al usuario
cache_emails     = []
CACHE_TTL        = 300  # s; pasado este tiempo no se borra nada de la lista
pendiente_borrar = None  # mantenido por compatibilidad con jarvis.py
MAX_ADJUNTO      = 20 * 1024 * 1024  # bytes
_mi_email        = None  # caché de getProfile
MAX_LOTE         = 25    # emails por borrado (mismo límite que brain.MAX_BORRAR_EMAILS)
MAX_LISTA        = 10    # emails que muestra una búsqueda
# Último lote movido a la papelera en esta sesión: {"ids", "remitentes", "ts"} (solo en memoria)
_ultimo_lote     = None
# Callback de la UI (lo pone jarvis.py): recibe [{"n", "remitente", "asunto"}] de la lista vigente
al_listar        = None

# ── AUTENTICACIÓN ─────────────────────────────────────────────────
def credenciales(scopes, token_file, servicio="gmail"):
    """OAuth con el credentials.json de JARVIS. Cada token_file guarda solo
    sus scopes: añadir uno a un token existente haría fallar su refresco
    (invalid_scope), por eso Calendar usa su propio token.
    Si hace falta autorizar en el navegador (nunca configurado, o sin refresh_token),
    lanza google_auth.NoConfigurado: eso se hace con reautorizar.py, no en mitad de
    una conversación."""
    from skills import google_auth
    if not os.path.isfile(CREDENTIALS_FILE):
        raise google_auth.NoConfigurado(servicio, google_auth.SIN_CREDENCIALES)
    if not os.path.exists(token_file):
        raise google_auth.NoConfigurado(servicio, google_auth.SIN_TOKEN)
    try:
        with open(token_file, encoding="utf-8-sig") as f:   # con o sin BOM
            creds = Credentials.from_authorized_user_info(json.load(f), scopes)
    except (ValueError, KeyError, TypeError, AttributeError):   # dañado o sin refresh_token
        raise google_auth.NoConfigurado(servicio, google_auth.INVALIDO) from None
    if not creds.valid:
        if not (creds.expired and creds.refresh_token):
            raise google_auth.NoConfigurado(servicio, google_auth.CADUCADO)
        creds.refresh(Request())
        with open(token_file, 'w', encoding='utf-8') as f:
            f.write(creds.to_json())
    return creds

def get_service():
    return build('gmail', 'v1', credentials=credenciales(SCOPES, TOKEN_FILE))

# ── OBTENER HEADERS ───────────────────────────────────────────────
def _get_headers(service, msg_id):
    data      = service.users().messages().get(
        userId='me', id=msg_id, format='metadata',
        metadataHeaders=['From', 'Subject']
    ).execute()
    headers   = {h['name']: h['value'] for h in data['payload']['headers']}
    remitente = headers.get('From', 'Desconocido')
    asunto    = headers.get('Subject', 'Sin asunto')
    if '<' in remitente:
        remitente = remitente.split('<')[0].strip().strip('"')
    return remitente, asunto

# ── CACHÉ DE LA ÚLTIMA LISTA ──────────────────────────────────────
# Remitentes vistos en las listas de esta sesión (solo en memoria): para sugerir el más
# parecido cuando Whisper escribe mal un nombre
_remitentes_vistos = {}

def _entradas_cache(service, messages):
    ahora    = time.time()
    entradas = []
    for msg in messages:
        remitente, asunto = _get_headers(service, msg['id'])
        entradas.append({"id": msg['id'], "remitente": remitente,
                         "asunto": asunto, "ts": ahora})
        _remitentes_vistos[remitente] = _remitentes_vistos.get(remitente, 0) + 1
    return entradas

def remitentes_vistos():
    """Remitentes de la lista vigente y de las anteriores de esta sesión, los más vistos primero."""
    actuales = [e.get("remitente", "") for e in cache_emails if isinstance(e, dict)]
    previos  = sorted(_remitentes_vistos, key=_remitentes_vistos.get, reverse=True)
    return list(dict.fromkeys(actuales + previos))

def _linea(i, e):
    return f"{i+1}. {e['remitente']}: {e['asunto']} [id={e['id']}]"

def _publicar_lista():
    """Envía a la UI la lista vigente numerada (posición → remitente y asunto, sin IDs)."""
    if not callable(al_listar):
        return
    try:
        al_listar([{"n": i + 1, "remitente": e.get("remitente", ""), "asunto": e.get("asunto", "")}
                   for i, e in enumerate(cache_emails) if isinstance(e, dict)])
    except Exception as e:
        print(f"[GMAIL] no se pudo mostrar la lista: {type(e).__name__}")

def lista_vigente():
    """Entradas de la última lista si no ha caducado (CACHE_TTL); [] si no hay o caducó."""
    ahora = time.time()
    entradas = [e for e in cache_emails if isinstance(e, dict)]
    if not entradas or any(ahora - e.get('ts', 0) > CACHE_TTL for e in entradas):
        return []
    return entradas

def por_posiciones(posiciones):
    """(entradas, None) para posiciones 1..N de la lista vigente, o (None, motivo).
    Nunca busca: solo resuelve contra lo que ya se ha listado al usuario."""
    entradas = lista_vigente()
    if not entradas:
        return None, "No hay ninguna lista de emails vigente. Lee primero los emails."
    if (not isinstance(posiciones, list) or not posiciones
            or not all(isinstance(p, int) and not isinstance(p, bool) for p in posiciones)):
        return None, "Las posiciones tienen que ser números de la última lista (1, 2, 3...)."
    fuera = [p for p in posiciones if not 1 <= p <= len(entradas)]
    if fuera:
        return None, (f"La última lista tiene {len(entradas)} emails; no existe la posición "
                      f"{', '.join(map(str, fuera))}.")
    return [entradas[p - 1] for p in dict.fromkeys(posiciones)], None

# ── NOMBRES PARECIDOS (Whisper escribe «Alis Pres» por «AliExpress») ─
def _compacto(texto):
    t = unicodedata.normalize("NFD", (texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", t)

def _nombre_visible(remitente):
    from email.utils import parseaddr
    nombre, direccion = parseaddr(remitente or "")
    return (nombre or direccion or remitente or "").strip()

def _trozos(texto, maximo=3):
    """Grupos de 1 a `maximo` palabras seguidas, compactados («alis pres» → «alispres»)."""
    palabras = re.findall(r"\w+", unicodedata.normalize("NFC", texto or "").lower())
    return {_compacto("".join(palabras[i:i + n]))
            for n in range(1, maximo + 1) for i in range(len(palabras) - n + 1)} - {""}

def parecido(a, b):
    """Similitud 0..1 entre dos nombres, sin mayúsculas, tildes, espacios ni signos."""
    a, b = _compacto(a), _compacto(b)
    if not a or not b:
        return 0.0
    if min(len(a), len(b)) >= 4 and (a in b or b in a):
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()

UMBRAL_PARECIDO = 0.72

def mencionado_en(nombre, texto):
    """True si `nombre` (o algo parecido) aparece en `texto` (lo que transcribió Whisper)."""
    objetivo = _compacto(nombre)
    if len(objetivo) < 3:
        return False
    return any(parecido(objetivo, t) >= UMBRAL_PARECIDO for t in _trozos(texto))

def remitente_mas_parecido(nombre, candidatos=None):
    """(remitente visible, similitud) del candidato más parecido a `nombre`, o (None, 0).
    Por defecto, los remitentes de la lista vigente."""
    if candidatos is None:
        candidatos = [e.get("remitente", "") for e in cache_emails if isinstance(e, dict)]
    mejor, puntos = None, 0.0
    for c in dict.fromkeys(_nombre_visible(c) for c in candidatos):
        p = max([parecido(nombre, c)] + [parecido(nombre, t) for t in _trozos(c)])
        if p > puntos:
            mejor, puntos = c, p
    return mejor, puntos

def de_remitente(nombre):
    """Entradas de la lista vigente cuyo remitente se parece a `nombre`."""
    return [e for e in lista_vigente()
            if max([parecido(nombre, e.get("remitente", ""))]
                   + [parecido(nombre, t) for t in _trozos(_nombre_visible(e.get("remitente", "")))])
            >= UMBRAL_PARECIDO]

def info_email(msg_id):
    """Entrada de caché del email (dict) o None si no se ha listado."""
    if not msg_id:
        return None
    for e in cache_emails:
        if isinstance(e, dict) and e.get('id') == msg_id:
            return e
    return None

def esta_en_cache(msg_id):
    return info_email(msg_id) is not None

# ── LEER NO LEÍDOS ────────────────────────────────────────────────
def leer_no_leidos(max_emails=5):
    global cache_emails
    try:
        service  = get_service()
        results  = service.users().messages().list(
            userId='me',
            labelIds=['INBOX', 'UNREAD'],
            q='category:primary',
            maxResults=max_emails
        ).execute()
        messages = results.get('messages', [])
        if not messages:
            cache_emails = []
            return "No tienes emails sin leer en la bandeja principal"
        cache_emails = _entradas_cache(service, messages)
        _publicar_lista()
        resumen = [_linea(i, e) for i, e in enumerate(cache_emails[:5])]
        return f"Tienes {len(messages)} emails sin leer. {'. '.join(resumen)}"
    except Exception as e:
        return fallo("Error leyendo emails", e)

# ── NO LEÍDOS PARA EL RESUMEN DE ARRANQUE ─────────────────────────
def no_leidos_arranque(max_emails=10):
    """(total, [{"remitente", "asunto"}, ...]) de los no leídos de la
    bandeja principal, o None si falla Gmail. No toca cache_emails:
    el arranque no debe dejar borrados habilitados."""
    try:
        service  = get_service()
        results  = service.users().messages().list(
            userId='me',
            labelIds=['INBOX', 'UNREAD'],
            q='category:primary',
            maxResults=max_emails
        ).execute()
        messages = results.get('messages', [])
        emails   = []
        for msg in messages:
            remitente, asunto = _get_headers(service, msg['id'])
            emails.append({"remitente": remitente, "asunto": asunto})
        total = max(results.get('resultSizeEstimate', 0), len(emails))
        return total, emails
    except Exception:
        return None

# ── LEER RECRUITERS ───────────────────────────────────────────────
def leer_recruiters():
    global cache_emails
    try:
        service  = get_service()
        keywords = ['recruiter', 'talent', 'selección', 'oferta', 'empleo',
                    'trabajo', 'backend', 'java', 'developer', 'engineer',
                    'posición', 'oportunidad']
        query    = '(' + ' OR '.join(keywords) + ') is:unread category:primary'
        results  = service.users().messages().list(
            userId='me', q=query, maxResults=5
        ).execute()
        messages = results.get('messages', [])
        if not messages:
            return "No tienes emails de recruiters sin leer"
        cache_emails = _entradas_cache(service, messages)
        _publicar_lista()
        resumen = [_linea(i, e) for i, e in enumerate(cache_emails)]
        return f"Tienes {len(messages)} emails de recruiters. " + ". ".join(resumen)
    except Exception as e:
        return fallo("Error leyendo emails", e)

# ── LEER EMAIL ────────────────────────────────────────────────────
def leer_email(indice=0):
    global cache_emails
    try:
        service = get_service()
        if cache_emails and indice < len(cache_emails):
            msg_id = cache_emails[indice]['id']
        else:
            results  = service.users().messages().list(
                userId='me',
                labelIds=['INBOX', 'UNREAD'],
                q='category:primary',
                maxResults=indice + 1
            ).execute()
            messages = results.get('messages', [])
            if not messages or indice >= len(messages):
                return "No hay email en esa posición"
            cache_emails = messages
            msg_id = messages[indice]['id']

        data    = service.users().messages().get(
            userId='me', id=msg_id, format='full'
        ).execute()
        headers   = {h['name']: h['value'] for h in data['payload']['headers']}
        remitente = headers.get('From', 'Desconocido')
        asunto    = headers.get('Subject', 'Sin asunto')
        if '<' in remitente:
            remitente = remitente.split('<')[0].strip().strip('"')

        cuerpo = ""
        if 'parts' in data['payload']:
            for part in data['payload']['parts']:
                if part['mimeType'] == 'text/plain' and 'data' in part.get('body', {}):
                    cuerpo = base64.urlsafe_b64decode(
                        part['body']['data'] + '=='
                    ).decode('utf-8', errors='ignore')
                    break
        elif 'body' in data['payload'] and 'data' in data['payload']['body']:
            cuerpo = base64.urlsafe_b64decode(
                data['payload']['body']['data'] + '=='
            ).decode('utf-8', errors='ignore')

        cuerpo_corto = cuerpo[:600].replace('\n', ' ').strip()
        return f"De {remitente}. Asunto: {asunto}. Contenido: {cuerpo_corto[:300]}"
    except Exception as e:
        return fallo("Error leyendo email", e)

# ── BORRAR EMAIL ──────────────────────────────────────────────────
def borrar_email(msg_id):
    """Mueve a la papelera un email de la última lista leída.
    Devuelve (ok, mensaje). Falla cerrado si el email no está en la caché
    o la lista tiene más de CACHE_TTL s: nunca busca por su cuenta."""
    entrada = info_email(msg_id)
    if entrada is None:
        return False, "Lee primero los emails"
    if time.time() - entrada.get('ts', 0) > CACHE_TTL:
        return False, "La lista está desactualizada, lee los emails de nuevo"
    try:
        service = get_service()
        service.users().messages().trash(userId='me', id=msg_id).execute()
    except Exception as e:
        return False, fallo("Error borrando", e)
    if entrada in cache_emails:
        cache_emails.remove(entrada)
    _recordar_lote([entrada])
    _publicar_lista()
    return True, f"Email de {entrada['remitente']} eliminado"

# ── BORRAR MÚLTIPLES ──────────────────────────────────────────────
def borrar_multiples(msg_ids):
    """Mueve a la papelera varios emails de la última lista leída.
    Devuelve (ok, mensaje). Valida todos antes de tocar ninguno: si alguno
    no está en la caché o la lista tiene más de CACHE_TTL s, no borra nada.
    Nunca busca por su cuenta ni usa batchDelete (borrado permanente)."""
    if not isinstance(msg_ids, list) or not msg_ids:
        return False, "No hay emails que borrar"
    entradas = [info_email(m) for m in dict.fromkeys(msg_ids)]
    if any(e is None for e in entradas):
        return False, "Lee primero los emails"
    ahora = time.time()
    if any(ahora - e.get('ts', 0) > CACHE_TTL for e in entradas):
        return False, "La lista está desactualizada, lee los emails de nuevo"
    try:
        service = get_service()
    except Exception as e:
        return False, fallo("Error borrando", e)
    borrados, fallidos = [], []
    for e in entradas:
        try:
            service.users().messages().trash(userId='me', id=e['id']).execute()
        except Exception:
            fallidos.append(e)
            continue
        borrados.append(e)
        if e in cache_emails:
            cache_emails.remove(e)
    if borrados:
        _recordar_lote(borrados)
        _publicar_lista()
    if not fallidos:
        return True, f"{len(borrados)} emails eliminados"
    fallo = "; ".join(f"{e['remitente']}: {e['asunto']}" for e in fallidos)
    if not borrados:
        return False, f"No se pudo borrar ningún email. Fallaron: {fallo}"
    return False, f"Eliminados {len(borrados)} de {len(entradas)}. Fallaron: {fallo}"

def _recordar_lote(entradas):
    global _ultimo_lote
    _ultimo_lote = {"ids": [e["id"] for e in entradas],
                    "remitentes": [e.get("remitente", "") for e in entradas], "ts": time.time()}

# ── DESHACER EL ÚLTIMO BORRADO ────────────────────────────────────
def deshacer_ultimo_borrado():
    """Saca de la papelera (untrash) los emails del último lote borrado en esta sesión.
    Devuelve (ok, mensaje). Solo toca esos IDs; nunca borra ni busca nada."""
    global _ultimo_lote
    lote = _ultimo_lote
    if not lote or not lote.get("ids"):
        return False, "No hay ningún borrado de correo que deshacer en esta sesión"
    try:
        service = get_service()
    except Exception as e:
        return False, fallo("Error recuperando", e)
    recuperados, fallidos = [], []
    for msg_id in lote["ids"]:
        try:
            service.users().messages().untrash(userId='me', id=msg_id).execute()
        except Exception:
            fallidos.append(msg_id)
            continue
        recuperados.append(msg_id)
    if not fallidos:
        _ultimo_lote = None
        n = len(recuperados)
        return True, f"{n} email{'s' if n != 1 else ''} recuperado{'s' if n != 1 else ''} de la papelera"
    _ultimo_lote = dict(lote, ids=fallidos)   # se puede reintentar con los que faltan
    if not recuperados:
        return False, "No se pudo recuperar ningún email de la papelera"
    return False, f"Recuperados {len(recuperados)} de {len(lote['ids'])}; {len(fallidos)} siguen en la papelera"

# ── ARCHIVAR EMAIL ────────────────────────────────────────────────
def archivar_email(indice=0):
    global cache_emails
    try:
        service = get_service()
        if not cache_emails or indice >= len(cache_emails):
            return "No hay email en esa posición"
        msg_id = cache_emails[indice]['id']
        service.users().messages().modify(
            userId='me', id=msg_id, body={'removeLabelIds': ['INBOX']}
        ).execute()
        cache_emails.pop(indice)
        return "Email archivado"
    except Exception as e:
        return fallo("Error archivando", e)

# ── BUSCAR EMAILS ─────────────────────────────────────────────────
def buscar_emails(query, max_emails=MAX_LISTA, acumular=False):
    """Busca y deja los resultados numerados como lista vigente. Con acumular=True
    (segunda búsqueda del mismo turno: «los de AliExpress y las alertas») se añaden
    detrás de la lista vigente en vez de sustituirla, para poder borrar todo junto."""
    global cache_emails
    try:
        service  = get_service()
        results  = service.users().messages().list(
            userId='me',
            q=query + ' category:primary',
            maxResults=max_emails
        ).execute()
        messages = results.get('messages', [])
        if not messages:
            return f"No encontré emails sobre: {query}"
        nuevas = _entradas_cache(service, messages)
        base   = lista_vigente() if acumular else []
        vistos = {e["id"] for e in base}
        cache_emails = base + [e for e in nuevas if e["id"] not in vistos]
        _publicar_lista()
        inicio  = len(base)
        resumen = [_linea(i, e) for i, e in enumerate(cache_emails) if i >= inicio]
        return f"Encontré {len(messages)} emails sobre {query}. " + ". ".join(resumen)
    except Exception as e:
        return fallo("Error buscando", e)

def _limpiar_remitente(nombre):
    """Solo letras, números, espacios y . - _ @, y sin comillas: va entre comillas en from:"...",
    así que OR, -, paréntesis o in: no pueden actuar como operadores de búsqueda."""
    return " ".join(re.sub(r"[^\w.@\- ]", " ", nombre or "").split())[:60]

def buscar_por_remitente(nombre, maximo=MAX_LOTE):
    """Emails de la bandeja de entrada de un remitente (todas las categorías). Deja el
    resultado como lista vigente numerada. Devuelve (entradas, total_estimado) o lanza."""
    global cache_emails
    limpio = _limpiar_remitente(nombre)
    if not limpio:
        return [], 0
    service  = get_service()
    results  = service.users().messages().list(
        userId='me', q=f'in:inbox from:"{limpio}"', maxResults=maximo).execute()
    messages = results.get('messages', [])
    if not messages:   # sin resultados la lista vigente se queda como estaba
        return [], 0
    cache_emails = _entradas_cache(service, messages)
    _publicar_lista()
    total = max(results.get('resultSizeEstimate', 0), len(cache_emails))
    return list(cache_emails), total

# ── ENVIAR EMAIL ──────────────────────────────────────────────────
def enviar_email(destinatario, asunto, cuerpo, adjunto=None):
    """(True, "Email enviado a X") o (False, "Error: motivo")."""
    destinatario = (destinatario or "").strip()
    asunto       = asunto or ""
    if not destinatario or "@" not in destinatario:
        return False, "Error: destinatario no válido"
    if any(c in destinatario for c in "\r\n"):
        return False, "Error: destinatario con saltos de línea"
    if any(c in asunto for c in "\r\n"):
        return False, "Error: asunto con saltos de línea"
    try:
        msg = EmailMessage()
        msg["To"]      = destinatario
        msg["Subject"] = asunto
        msg.set_content(cuerpo or "")
        if adjunto:
            if not os.path.isfile(adjunto):
                return False, f"Error: no existe el adjunto {adjunto}"
            if os.path.getsize(adjunto) > MAX_ADJUNTO:
                return False, "Error: el adjunto supera 20 MB"
            with open(adjunto, "rb") as f:
                msg.add_attachment(f.read(), maintype="application", subtype="octet-stream",
                                   filename=os.path.basename(adjunto))
        encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        get_service().users().messages().send(userId='me', body={'raw': encoded}).execute()
        return True, f"Email enviado a {destinatario}"
    except Exception as e:
        return False, fallo("Error enviando", e)

# ── MI DIRECCIÓN ──────────────────────────────────────────────────
# Causa real de un fallo de Gmail → frase para el usuario. Nunca pide que dicte su dirección.
MENSAJES_CAUSA = {
    "autenticacion": "No puedo obtener tu dirección: la autorización de Gmail ha caducado o se ha "
                     "revocado. Vuelve a autorizar a JARVIS con reautorizar.py gmail.",
    "red":           "No puedo obtener tu dirección: no hay conexión con Gmail. Revisa la red e "
                     "inténtalo de nuevo.",
    "permisos":      "No puedo obtener tu dirección: Gmail ha denegado el permiso para leer el "
                     "perfil de tu cuenta.",
    "servicio":      "No puedo obtener tu dirección: Gmail ha devuelto un error del servicio. "
                     "Inténtalo de nuevo en unos minutos.",
    "desconocida":   "No puedo obtener tu dirección por un fallo inesperado de Gmail.",
    "no_configurado": "No puedo obtener tu dirección: Gmail no está configurado; ejecuta "
                      "reautorizar.py gmail.",
}

def causa_error(e):
    """no_configurado | autenticacion | red | permisos | servicio | desconocida, solo por el
    tipo de la excepción."""
    from google.auth import exceptions as gexc
    from googleapiclient.errors import HttpError
    from skills import google_auth
    if isinstance(e, google_auth.NoConfigurado):
        return "no_configurado"
    if isinstance(e, gexc.RefreshError):  # invalid_grant: refresh_token caducado o revocado
        return "autenticacion"
    if isinstance(e, HttpError):
        estado = getattr(getattr(e, "resp", None), "status", None)
        return {401: "autenticacion", 403: "permisos"}.get(estado, "servicio")
    if isinstance(e, FileNotFoundError):  # falta credentials.json para autorizar
        return "autenticacion"
    if (isinstance(e, (gexc.TransportError, ConnectionError, TimeoutError, OSError))
            or type(e).__module__.split(".")[0] in ("httplib2", "requests", "urllib3")):
        return "red"
    return "desconocida"

# Fallo de una tool de Gmail → frase útil para decir por voz, nunca el texto de la excepción
# (puede llevar la petición o una dirección)
FALLOS = {
    "autenticacion": "la autorización de Gmail ha caducado o se ha revocado. Vuelve a autorizar a "
                     "JARVIS con reautorizar.py gmail.",
    "red":           "no hay conexión con Gmail. Revisa la red e inténtalo de nuevo.",
    "permisos":      "Gmail ha denegado el permiso para esta operación.",
    "servicio":      "Gmail ha devuelto un error del servicio. Inténtalo de nuevo en unos minutos.",
}

def fallo(accion, e):
    causa = causa_error(e)
    print(f"[GMAIL] {accion.lower()}: {type(e).__name__} ({causa})")
    if causa == "no_configurado":
        return e.frase
    return f"{accion}: " + FALLOS.get(causa, f"fallo inesperado de Gmail ({type(e).__name__}).")

def resolver_mi_email():
    """(dirección, None) de la cuenta autenticada (getProfile, cacheada) o
    (None, causa) si falla. Solo se registra el tipo de la excepción."""
    global _mi_email
    if _mi_email:
        return _mi_email, None
    try:
        direccion = get_service().users().getProfile(userId='me').execute()['emailAddress']
    except Exception as e:
        causa = causa_error(e)
        print(f"[GMAIL] getProfile falló: {type(e).__name__} ({causa})")
        return None, causa
    if not isinstance(direccion, str) or "@" not in direccion:
        return None, "servicio"
    _mi_email = direccion.strip()
    return _mi_email, None

def get_mi_email():
    """Dirección de la cuenta autenticada (cacheada), o None si falla."""
    return resolver_mi_email()[0]

# ── RESUMEN DIARIO ────────────────────────────────────────────────
def resumen_diario_gmail():
    try:
        service   = get_service()
        no_leidos = service.users().messages().list(
            userId='me',
            labelIds=['INBOX', 'UNREAD'],
            q='category:primary',
            maxResults=1
        ).execute().get('resultSizeEstimate', 0)
        if no_leidos:
            return f"Tienes {no_leidos} emails sin leer en la bandeja principal."
        return ""
    except Exception:
        return ""

# ── COMPATIBILIDAD ────────────────────────────────────────────────
def solicitar_borrar(indice=0):
    return borrar_email(indice)

def confirmar_borrar():
    return "Borrado completado"

def es_peticion_gmail(texto_lower):
    return any(p in texto_lower for p in [
        "emails", "correos", "email", "correo", "gmail",
        "tengo mensajes", "bandeja de entrada"
    ])