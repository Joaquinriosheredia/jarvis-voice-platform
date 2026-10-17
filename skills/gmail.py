import os
import time
import base64

import rutas
from email.message import EmailMessage
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
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

# ── AUTENTICACIÓN ─────────────────────────────────────────────────
def credenciales(scopes, token_file):
    """OAuth con el credentials.json de JARVIS. Cada token_file guarda solo
    sus scopes: añadir uno a un token existente haría fallar su refresco
    (invalid_scope), por eso Calendar usa su propio token."""
    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow  = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, scopes)
            creds = flow.run_local_server(port=8080, open_browser=True)
        with open(token_file, 'w') as f:
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
def _entradas_cache(service, messages):
    ahora    = time.time()
    entradas = []
    for msg in messages:
        remitente, asunto = _get_headers(service, msg['id'])
        entradas.append({"id": msg['id'], "remitente": remitente,
                         "asunto": asunto, "ts": ahora})
    return entradas

def _linea(i, e):
    return f"{i+1}. {e['remitente']}: {e['asunto']} [id={e['id']}]"

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
    if not fallidos:
        return True, f"{len(borrados)} emails eliminados"
    fallo = "; ".join(f"{e['remitente']}: {e['asunto']}" for e in fallidos)
    if not borrados:
        return False, f"No se pudo borrar ningún email. Fallaron: {fallo}"
    return False, f"Eliminados {len(borrados)} de {len(entradas)}. Fallaron: {fallo}"

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
def buscar_emails(query, max_emails=5):
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
        cache_emails = _entradas_cache(service, messages)
        resumen = [_linea(i, e) for i, e in enumerate(cache_emails[:3])]
        return f"Encontré {len(messages)} emails. " + ". ".join(resumen)
    except Exception as e:
        return fallo("Error buscando", e)

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
}

def causa_error(e):
    """autenticacion | red | permisos | servicio | desconocida, solo por el tipo de la excepción."""
    from google.auth import exceptions as gexc
    from googleapiclient.errors import HttpError
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