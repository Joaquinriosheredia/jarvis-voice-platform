"""Rutas de esta instalación. El resto del código no escribe rutas de usuario: las pide aquí.

Cada valor sale, por este orden:
  1. de su variable de entorno (también en el .env de la carpeta de secretos, que carga jarvis.py);
  2. de rutas.json en la carpeta de secretos (lo escribe instalar.cmd con las carpetas reales
     del usuario de Windows; no es un secreto, pero es de esta máquina y queda fuera del repo);
  3. del valor de respaldo, genérico: la carpeta personal de Windows (~), su Descargas y
     /home/<usuario de Windows> en WSL. Ningún usuario concreto escrito en el código.

Lo mismo para el perfil (perfil()): nombre, saludo, ciudad y coordenadas del tiempo, descripción,
usuario de GitHub, webs propias y proyectos del operador. Entorno > "perfil" de rutas.json >
vacío (saludo sin nombre, sin ciudad ni tiempo, y como proyecto solo este repo). Son datos del
usuario, no del código.

La carpeta de secretos solo se configura por entorno (JARVIS_SECRETOS): rutas.json vive dentro.
La carpeta del código es siempre la de este archivo.

Ligero a propósito (sin dependencias): lo importan doctor.py y los tests.
"""
import json
import os

RAIZ    = os.path.dirname(os.path.abspath(__file__))
ARCHIVO = "rutas.json"

# clave: (variable de entorno, respaldo). None: se calcula (ver _respaldo)
CLAVES = {
    "descargas":  ("JARVIS_DESCARGAS",  None),                    # <home>\Downloads
    "home":       ("JARVIS_HOME",       None),                    # la carpeta personal (~)
    "escritorio": ("JARVIS_ESCRITORIO", None),                    # <home>\Desktop
    "vlc":        ("JARVIS_VLC",        r"C:\Program Files\VideoLAN\VLC\vlc.exe"),
    "wsl_distro": ("JARVIS_WSL_DISTRO", "Ubuntu"),
    "wsl_home":   ("JARVIS_WSL_HOME",   None),                    # /home/<usuario de Windows>
}

# Perfil: clave -> variable de entorno
PERFIL = {
    "nombre":           "JARVIS_NOMBRE",            # cómo te llama JARVIS («Hasta luego, …»)
    "saludo":           "JARVIS_SALUDO",            # cómo te llama en el saludo («Buenos días, …»)
    "ciudad":           "JARVIS_CIUDAD",            # el tiempo y el prompt («En … hay 20 grados»)
    "provincia":        "JARVIS_PROVINCIA",
    "pais":             "JARVIS_PAIS",
    "latitud":          "JARVIS_LATITUD",           # coordenadas del tiempo (Open-Meteo)
    "longitud":         "JARVIS_LONGITUD",
    "nombre_completo":  "JARVIS_NOMBRE_COMPLETO",   # para el system prompt
    "descripcion":      "JARVIS_DESCRIPCION",       # una línea sobre ti para el system prompt
    "github_usuario":   "JARVIS_GITHUB_DUENIO",     # dueño de los repos escritos sin «dueño/»
    "web_github":       "JARVIS_WEB_GITHUB",        # «abre github»
    "web_portfolio":    "JARVIS_WEB_PORTFOLIO",     # «abre el portfolio»
    "bucket_authority": "JARVIS_AUTHORITY_BUCKET",  # bucket S3 de AuthorityEngine
}
SECRETOS_RESPALDO = r"C:\Jarvis-secrets"


def secretos():
    return os.environ.get("JARVIS_SECRETOS", "").strip() or SECRETOS_RESPALDO


def en_secretos(*partes):
    """Ruta de un archivo de la carpeta de secretos (.env, tokens, credenciales, memoria)."""
    return os.path.join(secretos(), *partes)


def en_raiz(*partes):
    """Ruta de un archivo de la carpeta del código (voz de Piper, memoria.json, logs)."""
    return os.path.join(RAIZ, *partes)


def _archivo():
    """Contenido de rutas.json (solo las claves de texto conocidas y "proyectos"), o {}."""
    try:
        with open(en_secretos(ARCHIVO), encoding="utf-8-sig") as f:
            datos = json.load(f)
    except (OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def origen(clave):
    """De dónde sale el valor: "entorno", "rutas.json" o "respaldo"."""
    variable, _ = CLAVES[clave]
    if os.environ.get(variable, "").strip():
        return "entorno"
    valor = _archivo().get(clave)
    return "rutas.json" if isinstance(valor, str) and valor.strip() else "respaldo"


def valor(clave):
    variable, respaldo = CLAVES[clave]
    del_entorno = os.environ.get(variable, "").strip()
    if del_entorno:
        return del_entorno
    del_archivo = _archivo().get(clave)
    if isinstance(del_archivo, str) and del_archivo.strip():
        return del_archivo.strip()
    return respaldo if respaldo is not None else _respaldo(clave)


def _respaldo(clave):
    if clave == "home":
        return os.path.expanduser("~")
    if clave == "descargas":
        return os.path.join(valor("home"), "Downloads")
    if clave == "escritorio":
        return os.path.join(valor("home"), "Desktop")
    if clave == "wsl_home":   # en WSL el usuario suele ser el de Windows en minúsculas
        return "/home/" + ((os.environ.get("USERNAME") or os.environ.get("USER") or "usuario").strip().lower()
                           or "usuario")
    return None


def perfil(clave):
    """Dato del perfil del usuario ("" si no está configurado)."""
    del_entorno = os.environ.get(PERFIL[clave], "").strip()
    if del_entorno:
        return del_entorno
    datos = _archivo().get("perfil")
    v = datos.get(clave) if isinstance(datos, dict) else None
    if clave in ("latitud", "longitud") and isinstance(v, (int, float)) and not isinstance(v, bool):
        return repr(v)
    return v.strip() if isinstance(v, str) else ""


def ubicacion(con_pais=True):
    """«Ciudad, provincia, país» del perfil (sin las partes vacías), o "" sin ciudad."""
    if not perfil("ciudad"):
        return ""
    partes = ("ciudad", "provincia", "pais") if con_pais else ("ciudad", "provincia")
    return ", ".join(p for p in (perfil(c) for c in partes) if p)


def coordenadas():
    """(latitud, longitud) del perfil, o None si faltan o no son válidas: sin ellas no hay tiempo."""
    try:
        lat, lon = float(perfil("latitud")), float(perfil("longitud"))
    except ValueError:
        return None
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def descargas():
    return valor("descargas")


def home():
    return valor("home")


def escritorio():
    return valor("escritorio")


def vlc():
    return valor("vlc")


def wsl_home():
    return valor("wsl_home").rstrip("/")


def wsl_unc(*partes):
    """\\\\wsl.localhost\\<distro>\\home\\<usuario>\\<partes> para abrir desde Windows un archivo de WSL."""
    trozos = [p for p in wsl_home().split("/") if p] + [p for parte in partes for p in parte.split("/") if p]
    return "\\\\wsl.localhost\\" + valor("wsl_distro") + "\\" + "\\".join(trozos)


def wsl_ruta(*partes):
    """/home/<usuario>/<partes>: la misma ruta vista desde dentro de WSL."""
    return "/".join([wsl_home()] + [p.strip("/") for p in partes if p.strip("/")])


def proyectos():
    """Alias del operador -> carpeta: "jarvis" (este repo) y los de "proyectos" del perfil de
    rutas.json, donde "wsl:<carpeta>" es una carpeta del home de WSL. Sin perfil, solo "jarvis".
    Un "proyectos" fuera del perfil (formato anterior) sigue valiendo y sustituye a la lista entera."""
    def valido(d):
        return isinstance(d, dict) and d and all(isinstance(k, str) and isinstance(v, str) and v.strip()
                                                 for k, v in d.items())
    antiguos = _archivo().get("proyectos")
    if valido(antiguos):
        return dict(antiguos)
    lista = {"jarvis": RAIZ}
    datos = _archivo().get("perfil")
    propios = datos.get("proyectos") if isinstance(datos, dict) else None
    if valido(propios):
        for alias, ruta in propios.items():
            ruta = ruta.strip()
            lista[alias] = wsl_unc(ruta[4:]) if ruta.lower().startswith("wsl:") else ruta
    return lista
