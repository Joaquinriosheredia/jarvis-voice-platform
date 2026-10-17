"""Rutas de esta instalación. El resto del código no escribe rutas de usuario: las pide aquí.

Cada valor sale, por este orden:
  1. de su variable de entorno (también en el .env de la carpeta de secretos, que carga jarvis.py);
  2. de rutas.json en la carpeta de secretos (lo escribe instalar.cmd con las carpetas reales
     del usuario de Windows; no es un secreto, pero es de esta máquina y queda fuera del repo);
  3. del valor de respaldo: el de la instalación original, para que no cambie.

La carpeta de secretos solo se configura por entorno (JARVIS_SECRETOS): rutas.json vive dentro.
La carpeta del código es siempre la de este archivo.

Ligero a propósito (sin dependencias): lo importan doctor.py y los tests.
"""
import json
import os

RAIZ    = os.path.dirname(os.path.abspath(__file__))
ARCHIVO = "rutas.json"

# clave: (variable de entorno, respaldo)
CLAVES = {
    "descargas":  ("JARVIS_DESCARGAS",  r"E:\descargas ryzen"),
    "home":       ("JARVIS_HOME",       r"C:\Users\Usuario"),
    "escritorio": ("JARVIS_ESCRITORIO", None),                    # None: <home>\Desktop
    "vlc":        ("JARVIS_VLC",        r"C:\Program Files\VideoLAN\VLC\vlc.exe"),
    "wsl_distro": ("JARVIS_WSL_DISTRO", "Ubuntu"),
    "wsl_home":   ("JARVIS_WSL_HOME",   "/home/usuariojoaquin"),
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
    if clave == "escritorio":
        return os.path.join(valor("home"), "Desktop")
    return respaldo


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
    """Alias del operador -> carpeta. "proyectos" en rutas.json sustituye a estos por completo."""
    propios = _archivo().get("proyectos")
    if isinstance(propios, dict) and propios and all(isinstance(v, str) for v in propios.values()):
        return dict(propios)
    return {
        "jarvis":     RAIZ,
        "authority":  wsl_unc("AuthorityEngine"),
        "java-labs":  wsl_unc("Java-Production-Labs"),
        "dam":        wsl_unc("DAM-Java-Mastery"),
        "vibe-guard": wsl_unc("vibe-guard"),
    }
