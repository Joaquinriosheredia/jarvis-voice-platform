"""Google Calendar: ver la agenda y crear eventos. Devuelven (ok, mensaje).

Usa el mismo cliente OAuth que Gmail (credentials.json) pero un token propio,
token_calendar.json: añadir el scope al token de Gmail rompería su refresco.
La primera vez se abre el navegador para dar permiso; mejor hacerlo a mano
antes que en mitad de una conversación:
    venv\\Scripts\\python.exe -c "from skills import calendario; print(calendario.ver_agenda())"
"""
import datetime
import re

from googleapiclient.discovery import build

from skills import gmail

# calendar.events basta para leer y crear eventos (no da acceso a borrar calendarios ni compartir)
SCOPES_CAL   = ["https://www.googleapis.com/auth/calendar.events"]
TOKEN_CAL    = r"C:\Jarvis-secrets\token_calendar.json"
ZONA         = "Europe/Madrid"
MAX_DIAS     = 7
MAX_EVENTOS  = 20
DURACION_MIN = 5
DURACION_MAX = 480
MAX_TITULO   = 200
MAX_DESC     = 500
DIAS_SEMANA  = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")

RE_FECHA = re.compile(r"\d{4}-\d{2}-\d{2}")
RE_HORA  = re.compile(r"\d{2}:\d{2}")


def _servicio():
    return build("calendar", "v3", credentials=gmail.credenciales(SCOPES_CAL, TOKEN_CAL),
                 cache_discovery=False)


def _entero(valor, nombre, defecto):
    if valor is None:
        return defecto, None
    if isinstance(valor, bool) or not isinstance(valor, int):
        return None, f"'{nombre}' debe ser un número entero"
    return valor, None


def _cuando(inicio):
    """'Lun 19/10 09:00', o 'Lun 19/10 (todo el día)' para eventos de día completo."""
    try:
        if "dateTime" in inicio:
            dt = datetime.datetime.fromisoformat(inicio["dateTime"])
            return f"{DIAS_SEMANA[dt.weekday()]} {dt:%d/%m %H:%M}"
        d = datetime.date.fromisoformat(inicio.get("date", ""))
        return f"{DIAS_SEMANA[d.weekday()]} {d:%d/%m} (todo el día)"
    except ValueError:
        return inicio.get("dateTime") or inicio.get("date") or "?"


def ver_agenda(dias=1):
    """Eventos de los próximos N días (1-7) desde ahora."""
    dias, error = _entero(dias, "dias", 1)
    if error:
        return False, error
    dias  = max(1, min(dias, MAX_DIAS))
    ahora = datetime.datetime.now(datetime.timezone.utc)
    try:
        res = _servicio().events().list(
            calendarId="primary",
            timeMin=ahora.isoformat(),
            timeMax=(ahora + datetime.timedelta(days=dias)).isoformat(),
            maxResults=MAX_EVENTOS,
            singleEvents=True,
            orderBy="startTime",
            timeZone=ZONA,
        ).execute()
    except Exception as e:  # HttpError, red, OAuth caducado...
        return False, f"No se pudo leer el calendario: {e}"

    periodo = "el próximo día" if dias == 1 else f"los próximos {dias} días"
    eventos = res.get("items", [])
    if not eventos:
        return True, f"No tienes eventos en {periodo}."
    lineas = [f"- {_cuando(e.get('start', {}))}: {e.get('summary') or '(sin título)'}" for e in eventos]
    return True, f"Agenda para {periodo}:\n" + "\n".join(lineas)


def eventos_hoy(ahora=None):
    """Eventos que quedan hoy (de ahora a medianoche, hora de Madrid), solo lectura:
    (True, [{"hora": "HH:MM" o None si es de todo el día, "titulo"}]) o (False, mensaje)."""
    ahora = (ahora or datetime.datetime.now().astimezone())
    fin   = ahora.replace(hour=23, minute=59, second=59, microsecond=0)
    try:
        res = _servicio().events().list(
            calendarId="primary", timeMin=ahora.isoformat(), timeMax=fin.isoformat(),
            maxResults=MAX_EVENTOS, singleEvents=True, orderBy="startTime", timeZone=ZONA,
        ).execute()
    except Exception as e:
        return False, f"No se pudo leer el calendario: {type(e).__name__}"
    eventos = []
    for e in res.get("items", []):
        inicio = e.get("start", {})
        hora = None
        if "dateTime" in inicio:
            try:
                hora = f"{datetime.datetime.fromisoformat(inicio['dateTime']):%H:%M}"
            except ValueError:
                hora = None
        eventos.append({"hora": hora, "titulo": e.get("summary") or "(sin título)"})
    return True, eventos


def crear_evento(titulo, fecha, hora_inicio, duracion_min=60, descripcion=""):
    titulo = titulo.strip()[:MAX_TITULO] if isinstance(titulo, str) else ""
    if not titulo:
        return False, "El título no puede estar vacío."
    if not isinstance(fecha, str) or not RE_FECHA.fullmatch(fecha):
        return False, f"Fecha no válida: {fecha!r}. Usa YYYY-MM-DD."
    if not isinstance(hora_inicio, str) or not RE_HORA.fullmatch(hora_inicio):
        return False, f"Hora no válida: {hora_inicio!r}. Usa HH:MM."
    try:
        inicio = datetime.datetime.fromisoformat(f"{fecha}T{hora_inicio}:00")
    except ValueError:  # 2026-02-30, 25:00...
        return False, f"Esa fecha u hora no existe: {fecha} {hora_inicio}."
    duracion, error = _entero(duracion_min, "duracion_min", 60)
    if error:
        return False, error
    duracion    = max(DURACION_MIN, min(duracion, DURACION_MAX))
    descripcion = descripcion[:MAX_DESC] if isinstance(descripcion, str) else ""
    fin         = inicio + datetime.timedelta(minutes=duracion)

    evento = {
        "summary":     titulo,
        "description": descripcion,
        "start":       {"dateTime": inicio.isoformat(), "timeZone": ZONA},
        "end":         {"dateTime": fin.isoformat(),    "timeZone": ZONA},
    }
    try:
        _servicio().events().insert(calendarId="primary", body=evento).execute()
    except Exception as e:
        return False, f"No se pudo crear el evento: {e}"
    return True, (f"Evento creado: '{titulo}' el {DIAS_SEMANA[inicio.weekday()]} "
                  f"{inicio:%d/%m} a las {inicio:%H:%M} ({duracion} min).")
