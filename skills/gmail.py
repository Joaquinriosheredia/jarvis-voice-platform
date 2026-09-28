import os
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES           = ['https://www.googleapis.com/auth/gmail.modify']
CREDENTIALS_FILE = r"C:\Jarvis-secrets\credentials.json"
TOKEN_FILE       = r"C:\Jarvis-secrets\token.json"

cache_emails     = []
pendiente_borrar = None  # mantenido por compatibilidad con jarvis.py

# ── AUTENTICACIÓN ─────────────────────────────────────────────────
def get_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow  = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=8080, open_browser=True)
        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)

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
        cache_emails = messages
        resumen = []
        for i, msg in enumerate(messages[:5]):
            remitente, asunto = _get_headers(service, msg['id'])
            resumen.append(f"{i+1}. {remitente}: {asunto}")
        return f"Tienes {len(messages)} emails sin leer. {'. '.join(resumen)}"
    except Exception as e:
        return f"Error leyendo emails: {e}"

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
        cache_emails = messages
        resumen = []
        for i, msg in enumerate(messages):
            remitente, asunto = _get_headers(service, msg['id'])
            resumen.append(f"{i+1}. {remitente}: {asunto}")
        return f"Tienes {len(messages)} emails de recruiters. " + ". ".join(resumen)
    except Exception as e:
        return f"Error: {e}"

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
        return f"Error leyendo email: {e}"

# ── BORRAR EMAIL — DIRECTO ────────────────────────────────────────
def borrar_email(indice=0):
    """Borra directamente. El agente ya pidió confirmación al usuario."""
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

        service.users().messages().trash(userId='me', id=msg_id).execute()
        if cache_emails and indice < len(cache_emails):
            cache_emails.pop(indice)
        return f"Email {indice+1} eliminado"
    except Exception as e:
        return f"Error borrando: {e}"

# ── BORRAR MÚLTIPLES ──────────────────────────────────────────────
def borrar_multiples(cantidad=5):
    """Borra los primeros N emails directamente."""
    global cache_emails
    try:
        service = get_service()
        if not cache_emails:
            leer_no_leidos(max_emails=cantidad)
        borrados = 0
        indices_borrar = min(cantidad, len(cache_emails))
        for _ in range(indices_borrar):
            if not cache_emails:
                break
            msg_id = cache_emails[0]['id']
            service.users().messages().trash(userId='me', id=msg_id).execute()
            cache_emails.pop(0)
            borrados += 1
        return f"Eliminados {borrados} emails"
    except Exception as e:
        return f"Error borrando: {e}"

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
        return f"Error archivando: {e}"

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
        cache_emails = messages
        resumen = []
        for i, msg in enumerate(messages[:3]):
            remitente, asunto = _get_headers(service, msg['id'])
            resumen.append(f"{i+1}. {remitente}: {asunto}")
        return f"Encontré {len(messages)} emails. " + ". ".join(resumen)
    except Exception as e:
        return f"Error buscando: {e}"

# ── ENVIAR EMAIL ──────────────────────────────────────────────────
def enviar_email(destinatario, asunto, cuerpo):
    try:
        service = get_service()
        mensaje = f"To: {destinatario}\nSubject: {asunto}\n\n{cuerpo}"
        encoded = base64.urlsafe_b64encode(mensaje.encode()).decode()
        service.users().messages().send(
            userId='me', body={'raw': encoded}
        ).execute()
        return f"Email enviado a {destinatario}"
    except Exception as e:
        return f"Error enviando email: {e}"

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