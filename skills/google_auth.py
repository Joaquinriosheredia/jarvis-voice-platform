"""Estado real de la autorización OAuth de Gmail, Calendar y Contactos (token.json,
token_calendar.json, token_contacts.json; Contactos es opcional).

comprobar() intenta renovar el access token con el refresh_token contra el endpoint
de tokens de Google: no lee ni envía correo ni eventos, y no escribe el archivo (el
token renovado solo vive en memoria). La usan el arranque de jarvis.py, doctor.py
--live y reautorizar.py.

Nunca devuelve ni imprime tokens, direcciones ni el texto de la excepción: solo el
estado y, si Google lo da, el código de error OAuth de una lista cerrada. Todo texto
de salida pasa por redactar_secretos y direccion.ocultar.

Google responde invalid_grant tanto si el refresh_token ha caducado (app OAuth en
modo Testing: 7 días) como si se ha revocado, así que los dos casos son un solo
estado: REVOCADO.
"""
import json
import os
import threading

import rutas

from skills import direccion
from skills.secretos import redactar_secretos

CREDENTIALS_FILE = rutas.en_secretos("credentials.json")
TIMEOUT          = 10    # s por petición al endpoint de tokens
COMANDO          = r"venv\Scripts\python.exe reautorizar.py {servicio}"

VALIDO           = "valido"            # el access token seguía vigente y el refresh_token funciona
RENOVADO         = "renovado"          # el access token había caducado y se ha renovado
CADUCADO         = "caducado"          # sin refresh_token: no se puede renovar
REVOCADO         = "revocado"          # invalid_grant: refresh_token caducado o revocado
SIN_RED          = "sin_red"           # no se pudo hablar con Google: no verificado
SIN_TOKEN        = "sin_token"         # nunca se ha autorizado (falta el archivo del token)
SIN_CREDENCIALES = "sin_credenciales"  # falta credentials.json (el cliente OAuth)
INVALIDO         = "invalido"          # archivo dañado o incompleto, o invalid_scope
ERROR            = "error"             # Google rechazó la renovación por otra causa

VALIDOS     = {VALIDO, RENOVADO}
REAUTORIZAR = {CADUCADO, REVOCADO, SIN_TOKEN, INVALIDO, ERROR}  # se arreglan con reautorizar.py
AVISAR      = REAUTORIZAR | {SIN_CREDENCIALES}                  # se avisan al arrancar

# Únicos códigos OAuth que se muestran (RFC 6749 §5.2 y los de Google)
CODIGOS_OAUTH = ("invalid_grant", "invalid_scope", "invalid_client", "unauthorized_client",
                 "invalid_request", "deleted_client", "disabled_client", "org_internal")


# Servicios opcionales: si nunca se autorizaron (SIN_TOKEN) no se avisa al arrancar
OPCIONALES = {"contacts"}


def servicios():
    """{servicio: (nombre, token_file, scopes)} desde las constantes de gmail, calendario y contactos."""
    from skills import calendario, contactos, gmail
    return {"gmail":    ("Gmail", gmail.TOKEN_FILE, list(gmail.SCOPES)),
            "calendar": ("Calendar", calendario.TOKEN_CAL, list(calendario.SCOPES_CAL)),
            "contacts": ("Contactos", contactos.TOKEN_CONTACTOS, list(contactos.SCOPES_CONTACTOS))}


def comando(servicio):
    return COMANDO.format(servicio=servicio)


def _resultado(servicio, estado, codigo=None):
    return {"servicio": servicio, "estado": estado, "codigo": codigo,
            "ok": estado in VALIDOS, "reautorizar": estado in REAUTORIZAR}


def _codigo_oauth(e):
    """Código OAuth de un RefreshError (de la lista cerrada) o None."""
    for arg in getattr(e, "args", ()):
        if isinstance(arg, dict) and arg.get("error") in CODIGOS_OAUTH:
            return arg["error"]
    primero = str(e.args[0]) if getattr(e, "args", None) else ""
    codigo = primero.split(":", 1)[0].strip()
    return codigo if codigo in CODIGOS_OAUTH else None


def _transporte(timeout=TIMEOUT):
    """Request de google-auth con un timeout corto (el de la librería es de 120 s)."""
    from google.auth.transport.requests import Request
    request = Request()

    def llamar(*args, **kwargs):
        kwargs["timeout"] = min(kwargs.get("timeout") or timeout, timeout)
        return request(*args, **kwargs)
    return llamar


def _es_de_red(e):
    from google.auth import exceptions as gexc
    return (isinstance(e, (gexc.TransportError, ConnectionError, TimeoutError, OSError))
            or type(e).__module__.split(".")[0] in ("requests", "urllib3", "httplib2"))


def comprobar(servicio, token_file, scopes, credentials_file=CREDENTIALS_FILE, transporte=None):
    """Estado de un token renovándolo de verdad (en memoria). Nunca lanza."""
    try:
        if not os.path.isfile(credentials_file):
            return _resultado(servicio, SIN_CREDENCIALES)
        if not os.path.isfile(token_file):
            return _resultado(servicio, SIN_TOKEN)
        try:
            with open(token_file, encoding="utf-8") as f:
                datos = json.load(f)
        except (OSError, ValueError):
            return _resultado(servicio, INVALIDO)
        if not isinstance(datos, dict):
            return _resultado(servicio, INVALIDO)
        if not datos.get("refresh_token"):
            return _resultado(servicio, CADUCADO)
        from google.auth import exceptions as gexc
        from google.oauth2.credentials import Credentials
        try:
            creds = Credentials.from_authorized_user_info(datos, scopes)
        except (ValueError, KeyError, TypeError):
            return _resultado(servicio, INVALIDO)
        vigente = creds.valid
        try:
            creds.refresh(transporte or _transporte())
        except gexc.RefreshError as e:
            codigo = _codigo_oauth(e)
            if codigo == "invalid_grant":
                return _resultado(servicio, REVOCADO, codigo)
            if codigo == "invalid_scope":
                return _resultado(servicio, INVALIDO, codigo)
            return _resultado(servicio, ERROR, codigo)
        except Exception as e:
            if _es_de_red(e):
                return _resultado(servicio, SIN_RED)
            return _resultado(servicio, ERROR)
        return _resultado(servicio, VALIDO if vigente else RENOVADO)
    except Exception:
        return _resultado(servicio, ERROR)


def comprobar_servicio(servicio, transporte=None):
    nombre, token_file, scopes = servicios()[servicio]
    return comprobar(servicio, token_file, scopes, transporte=transporte)


def comprobar_todos(espera=TIMEOUT + 5):
    """{servicio: resultado} de Gmail, Calendar y Contactos en paralelo. Lo que no termina en
    `espera` segundos cuenta como SIN_RED (los hilos son daemon: no bloquean la salida)."""
    try:
        config = servicios()
    except Exception:
        return {s: _resultado(s, ERROR) for s in ("gmail", "calendar", "contacts")}
    resultados = {}

    def uno(servicio, token_file, scopes):
        resultados[servicio] = comprobar(servicio, token_file, scopes)
    hilos = [threading.Thread(target=uno, args=(s, t, sc), daemon=True, name=f"auth-{s}")
             for s, (_, t, sc) in config.items()]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(espera)
    return {s: resultados.get(s) or _resultado(s, SIN_RED) for s in config}


# ── TEXTOS ────────────────────────────────────────────────────────
_NOMBRES  = {"gmail": "Gmail", "calendar": "Calendar", "contacts": "Contactos"}
_ARCHIVOS = {"gmail": "token.json", "calendar": "token_calendar.json", "contacts": "token_contacts.json"}
_DETALLE = {
    VALIDO:           "autorización válida (renovación verificada con Google)",
    RENOVADO:         "autorización válida (el access token había caducado y se ha renovado)",
    CADUCADO:         "{archivo} no tiene refresh_token: no se puede renovar",
    REVOCADO:         "la autorización ha caducado o se ha revocado (invalid_grant)",
    SIN_RED:          "no verificado: no hay conexión con Google",
    SIN_TOKEN:        "no autorizado: falta {archivo}",
    SIN_CREDENCIALES: f"falta {CREDENTIALS_FILE} (el cliente OAuth de Google)",
    INVALIDO:         "{archivo} está dañado, incompleto o con otros permisos",
    ERROR:            "Google ha rechazado la renovación",
}
_VOZ = {
    CADUCADO:         "el token de {nombre} ha caducado y no se puede renovar",
    REVOCADO:         "la autorización de {nombre} ha caducado o se ha revocado",
    SIN_TOKEN:        "{nombre} no está autorizado",
    INVALIDO:         "el token de {nombre} está dañado",
    ERROR:            "Google ha rechazado la autorización de {nombre}",
    SIN_CREDENCIALES: "falta el archivo credentials de Google, así que {nombre} no funcionará",
}
_USO = {"gmail": "leer ni enviar correo", "calendar": "ver ni crear eventos",
        "contacts": "buscar tus contactos"}


def limpio(texto):
    """Red de seguridad para todo texto que sale de este módulo."""
    return direccion.ocultar(redactar_secretos(texto))


def detalle(r):
    texto = _DETALLE[r["estado"]].format(archivo=_ARCHIVOS.get(r["servicio"], "el token"))
    if r["estado"] == ERROR and r.get("codigo"):
        texto += f" ({r['codigo']})"
    return limpio(texto)


def mensaje(r):
    """'Gmail: <detalle>' para consola, doctor y reautorizar.py."""
    return limpio(f"{_NOMBRES.get(r['servicio'], r['servicio'])}: {detalle(r)}")


def accion(r):
    """Qué hacer para arreglarlo, o '' si no hace falta nada."""
    if r["estado"] in REAUTORIZAR:
        return comando(r["servicio"])
    if r["estado"] == SIN_CREDENCIALES:
        return f"Descarga el cliente OAuth de escritorio de Google Cloud a {CREDENTIALS_FILE}"
    if r["estado"] == SIN_RED:
        return "Comprueba la conexión a internet"
    return ""


def aviso_arranque(resultados):
    """(texto para la voz, texto para la pantalla) si algún servicio necesita que el
    usuario haga algo, o None. Sin red no se avisa: no hay nada que arreglar."""
    malos = [r for r in resultados.values() if r and r["estado"] in AVISAR
             and not (r["servicio"] in OPCIONALES and r["estado"] in (SIN_TOKEN, SIN_CREDENCIALES))]
    if not malos:
        return None
    voz, pantalla = [], []
    for r in malos:
        nombre = _NOMBRES.get(r["servicio"], r["servicio"])
        frase = _VOZ[r["estado"]].format(nombre=nombre)
        frase = frase[0].upper() + frase[1:]
        if r["estado"] in REAUTORIZAR:
            frase += (f": hasta que lo arregles no podré {_USO.get(r['servicio'], 'usarlo')}. "
                      f"Ejecuta reautorizar {r['servicio']}")
        voz.append(frase + ".")
        pantalla.append(f"⚠ {mensaje(r)}\n   → {accion(r)}")
    voz.append("Tienes el comando exacto en pantalla.")
    return limpio("Aviso. " + " ".join(voz)), limpio("\n".join(pantalla))
