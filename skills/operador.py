"""Fase 6: herramientas de ingeniería de SOLO LECTURA sobre proyectos fijos.

El modelo nunca pasa rutas absolutas: elige un alias de PROYECTOS y una ruta
relativa dentro de él. Devuelven (ok, mensaje) salvo listar_proyectos.
"""
import fnmatch
import os
import re
import subprocess

PROYECTOS = {
    "jarvis":     "C:\\Jarvis",
    "authority":  "\\\\wsl.localhost\\Ubuntu\\home\\usuariojoaquin\\AuthorityEngine",
    "java-labs":  "\\\\wsl.localhost\\Ubuntu\\home\\usuariojoaquin\\Java-Production-Labs",
    "dam":        "\\\\wsl.localhost\\Ubuntu\\home\\usuariojoaquin\\DAM-Java-Mastery",
    "vibe-guard": "\\\\wsl.localhost\\Ubuntu\\home\\usuariojoaquin\\vibe-guard",
}

MAX_LINEAS  = 400
MAX_BYTES   = 8 * 1024
GIT_TIMEOUT = 10
# Se comprueban contra CADA componente de la ruta (carpeta o archivo).
# .git: su config puede llevar tokens en la URL del remoto.
SECRETOS    = (".env*", "*.pem", "*.key", "id_rsa*", "*credential*", "*token*", ".git")


def listar_proyectos():
    return "Proyectos: " + "; ".join(f"{alias} ({ruta})" for alias, ruta in PROYECTOS.items())


def _base(proyecto):
    if proyecto not in PROYECTOS:
        return None, f"Proyecto desconocido: '{proyecto}'. Disponibles: {', '.join(PROYECTOS)}."
    return PROYECTOS[proyecto], None


def _validar_relativa(base, ruta):
    """(ruta_real, None) si es segura; (None, motivo) si no."""
    if not isinstance(ruta, str) or not ruta.strip():
        return None, "ruta vacía o no válida"
    if "\x00" in ruta or ":" in ruta:
        return None, f"ruta con caracteres no válidos: {ruta}"
    if ruta.startswith(("\\", "/")) or os.path.splitdrive(ruta)[0]:
        return None, f"la ruta debe ser relativa al proyecto: {ruta}"
    partes = [p for p in re.split(r"[\\/]+", ruta) if p]
    if ".." in partes:
        return None, f"traversal (..) no permitido: {ruta}"
    for parte in partes:
        if any(fnmatch.fnmatch(parte.lower(), patron) for patron in SECRETOS):
            return None, f"archivo de secretos bloqueado: {ruta}"
    base_real = os.path.normcase(os.path.realpath(base))
    real      = os.path.normcase(os.path.realpath(os.path.join(base, *partes)))
    try:
        dentro = os.path.commonpath([real, base_real]) == base_real
    except ValueError:  # otra unidad
        dentro = False
    if not dentro:  # symlink/junction que sale del proyecto, o nombre de dispositivo (CON, NUL)
        return None, f"la ruta sale del proyecto: {ruta}"
    return real, None


def _entero_opcional(valor, nombre):
    if valor is None:
        return None, None
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 1:
        return None, f"'{nombre}' debe ser un entero >= 1"
    return valor, None


def leer_archivo(proyecto, ruta_relativa, desde=None, hasta=None):
    base, error = _base(proyecto)
    if error:
        return False, error
    real, error = _validar_relativa(base, ruta_relativa)
    if error:
        return False, f"BLOQUEADO: {error}"
    desde, error = _entero_opcional(desde, "desde")
    if error:
        return False, error
    hasta, error = _entero_opcional(hasta, "hasta")
    if error:
        return False, error
    desde = desde or 1
    if hasta is not None and hasta < desde:
        return False, f"rango inválido: hasta ({hasta}) < desde ({desde})"
    if not os.path.isfile(real):
        return False, f"No existe el archivo {ruta_relativa} en {proyecto}"

    try:
        with open(real, "rb") as f:
            if b"\x00" in f.read(1024):
                return False, f"{ruta_relativa} es un archivo binario; solo se leen archivos de texto"
        lineas, usados, ultima, truncado = [], 0, desde - 1, False
        with open(real, encoding="utf-8", errors="replace") as f:
            for n, linea in enumerate(f, start=1):
                if n < desde:
                    continue
                if hasta is not None and n > hasta:
                    break
                tam = len(linea.encode("utf-8"))
                if len(lineas) >= MAX_LINEAS or usados + tam > MAX_BYTES:
                    truncado = True
                    break
                lineas.append(linea)
                usados += tam
                ultima = n
    except OSError as e:
        return False, f"No se pudo leer {ruta_relativa}: {e}"

    if not lineas:
        if truncado:  # una sola línea ya supera MAX_BYTES
            return False, f"La línea {desde} de {ruta_relativa} supera {MAX_BYTES // 1024} KB"
        return False, f"{ruta_relativa} no tiene líneas a partir de la {desde}"
    aviso = f" (truncado: pide un rango con desde={ultima + 1})" if truncado else ""
    return True, f"{proyecto}/{ruta_relativa} líneas {desde}-{ultima}{aviso}:\n{''.join(lineas)}"


def git_estado(proyecto):
    base, error = _base(proyecto)
    if error:
        return False, error
    # safe.directory: git de Windows rechaza los repos de \\wsl.localhost por
    # "dubious ownership"; se exime solo esta ruta fija, nunca '*'.
    # --no-optional-locks: status no reescribe el índice (lectura pura).
    cmd = ["git", "--no-pager", "--no-optional-locks",
           "-c", "safe.directory=" + base.replace("\\", "/"),
           "-c", "core.fsmonitor=", "-c", "core.hooksPath=/dev/null",
           "status", "--short"]
    try:
        r = subprocess.run(cmd, cwd=base, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"git status de {proyecto} superó {GIT_TIMEOUT} s"
    except OSError as e:  # git no instalado, carpeta inexistente...
        return False, f"No se pudo ejecutar git en {proyecto}: {e}"
    if r.returncode != 0:
        detalle = (r.stderr.strip().splitlines() or ["sin detalle"])[0]
        return False, f"git status falló en {proyecto}: {detalle}"
    salida = r.stdout.strip()
    if not salida:
        return True, f"{proyecto}: sin cambios pendientes"
    if len(salida.encode("utf-8")) > MAX_BYTES:
        salida = salida.encode("utf-8")[:MAX_BYTES].decode("utf-8", "ignore") + "\n(truncado)"
    return True, f"{proyecto}, cambios pendientes:\n{salida}"
