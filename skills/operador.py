"""Fase 6: herramientas de ingeniería de SOLO LECTURA sobre proyectos fijos.

El modelo nunca pasa rutas absolutas: elige un alias de PROYECTOS y una ruta
relativa dentro de él. Devuelven (ok, mensaje) salvo listar_proyectos.
"""
import fnmatch
import os
import re
import subprocess
import time

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

DIFF_TIMEOUT      = 15
BUSQUEDA_TIMEOUT  = 15
MAX_LOG           = 30
MAX_COINCIDENCIAS = 50
MAX_ANCHO_LINEA   = 200
MAX_PATRON        = 200
MAX_ARCHIVO_BUSQ  = 1024 * 1024
# Solo en la búsqueda sin git (en git, el .gitignore ya los deja fuera)
IGNORAR_DIRS      = {"node_modules", "venv", ".venv", "__pycache__"}

REF_VALIDA  = re.compile(r"[\w./~^-]{1,64}", re.ASCII)  # ~ y ^: HEAD~1, HEAD^
GLOB_VALIDO = re.compile(r"\*\.[A-Za-z0-9]{1,10}")
# Pathspecs que dejan los secretos fuera de diff y grep dentro del propio git:
# su contenido nunca sale del proceso.
EXCLUIR_SECRETOS = [f":(exclude,icase,glob)**/{patron}{sufijo}"
                    for patron in SECRETOS for sufijo in ("", "/**")]

# Comando fijo por proyecto (el modelo solo elige alias y filtro).
# jarvis: el venv no tiene pytest -> unittest (también admite -k).
# authority: la suite vive en jarvis-runtime y usa su propio venv (en WSL no hay 'python').
# java-labs, dam y vibe-guard no tienen suite ejecutable en la raíz: sin entrada.
COMANDOS_TEST = {
    "jarvis":    ["venv\\Scripts\\python.exe", "-m", "unittest", "discover", "-s", "tests"],
    "authority": ["wsl.exe", "-d", "Ubuntu", "--cd",
                  "/home/usuariojoaquin/AuthorityEngine/jarvis-runtime",
                  "--", "venv/bin/python", "-m", "pytest", "-q", "--tb=short"],
}
TESTS_TIMEOUT = 300
MAX_COLA_TEST = 4 * 1024
FILTRO_VALIDO = re.compile(r"[\w/._-]{1,80}", re.ASCII)
# Líneas de resumen de pytest, unittest y maven
RESUMEN_TEST  = re.compile(r"\b\d+ (passed|failed|errors?|skipped)\b|no tests ran|^Ran \d+ tests?"
                           r"|^(OK|FAILED)\b|Tests run:|BUILD (SUCCESS|FAILURE)")


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


def _truncar(salida):
    if len(salida.encode("utf-8")) > MAX_BYTES:
        return salida.encode("utf-8")[:MAX_BYTES].decode("utf-8", "ignore") + "\n(truncado)"
    return salida


def _git(proyecto, base, accion, args, timeout=GIT_TIMEOUT, config=()):
    """(CompletedProcess, None) o (None, mensaje de error). Nunca usa shell."""
    # safe.directory: git de Windows rechaza los repos de \\wsl.localhost por
    # "dubious ownership"; se exime solo esta ruta fija, nunca '*'.
    # --no-optional-locks: no reescribe el índice (lectura pura).
    cmd = ["git", "--no-pager", "--no-optional-locks",
           "-c", "safe.directory=" + base.replace("\\", "/"),
           "-c", "core.fsmonitor=", "-c", "core.hooksPath=/dev/null"]
    for valor in config:
        cmd += ["-c", valor]
    try:
        r = subprocess.run(cmd + list(args), cwd=base, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"git {accion} de {proyecto} superó {timeout} s"
    except OSError as e:  # git no instalado, carpeta inexistente...
        return None, f"No se pudo ejecutar git en {proyecto}: {e}"
    return r, None


def _fallo(r, proyecto, accion):
    detalle = (r.stderr.strip().splitlines() or ["sin detalle"])[0]
    return f"git {accion} falló en {proyecto}: {detalle}"


def _pathspec_ruta(base, ruta):
    """([pathspec], None) o (None, error). Sin ruta: [] (todo el proyecto)."""
    if ruta is None:
        return [], None
    _, error = _validar_relativa(base, ruta)
    if error:
        return None, f"BLOQUEADO: {error}"
    # literal: '*' o '[' en la ruta no se interpretan como comodines
    return [":(literal)" + ruta.replace("\\", "/")], None


def git_estado(proyecto):
    base, error = _base(proyecto)
    if error:
        return False, error
    r, error = _git(proyecto, base, "status", ["status", "--short"])
    if error:
        return False, error
    if r.returncode != 0:
        return False, _fallo(r, proyecto, "status")
    salida = r.stdout.strip()
    if not salida:
        return True, f"{proyecto}: sin cambios pendientes"
    return True, f"{proyecto}, cambios pendientes:\n{_truncar(salida)}"


def git_diff(proyecto, staged=False, ref=None, ruta=None):
    base, error = _base(proyecto)
    if error:
        return False, error
    if ref is not None:
        if not isinstance(ref, str) or ref.startswith("-") or not REF_VALIDA.fullmatch(ref):
            return False, f"BLOQUEADO: ref no válida: {ref!r}"
    pathspec, error = _pathspec_ruta(base, ruta)
    if error:
        return False, error
    args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color"]
    if staged is True:
        args.append("--staged")
    if ref is not None:
        args.append(ref)
    args += ["--", *(pathspec or ["."]), *EXCLUIR_SECRETOS]
    r, error = _git(proyecto, base, "diff", args, DIFF_TIMEOUT, config=["diff.external="])
    if error:
        return False, error
    if r.returncode != 0:
        return False, _fallo(r, proyecto, "diff")
    salida = r.stdout.strip()
    if not salida:
        return True, f"{proyecto}: sin diferencias"
    return True, f"{proyecto}, diff:\n{_truncar(salida)}"


def git_log(proyecto, n=10, ruta=None):
    base, error = _base(proyecto)
    if error:
        return False, error
    if n is None:
        n = 10
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        return False, "'n' debe ser un entero >= 1"
    n = min(n, MAX_LOG)
    pathspec, error = _pathspec_ruta(base, ruta)
    if error:
        return False, error
    args = ["log", "--oneline", "--no-decorate", "--no-color", "-n", str(n)]
    if pathspec:
        args += ["--", *pathspec]
    r, error = _git(proyecto, base, "log", args, config=["log.showSignature=false"])
    if error:
        return False, error
    if r.returncode != 0:
        return False, _fallo(r, proyecto, "log")
    salida = r.stdout.strip()
    if not salida:
        return True, f"{proyecto}: sin commits" + (f" que toquen {ruta}" if ruta else "")
    return True, f"{proyecto}, últimos {len(salida.splitlines())} commits:\n{_truncar(salida)}"


def _es_secreto(nombre):
    return any(fnmatch.fnmatch(nombre.lower(), patron) for patron in SECRETOS)


def _buscar_sin_git(base, patron, glob):
    """Búsqueda literal en Python (en Windows no hay grep en el PATH).
    Devuelve (lineas, hay_mas, error)."""
    limite = time.monotonic() + BUSQUEDA_TIMEOUT
    lineas = []
    for raiz, dirs, archivos in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in IGNORAR_DIRS and not _es_secreto(d))
        for nombre in sorted(archivos):
            if time.monotonic() > limite:
                return None, False, f"la búsqueda superó {BUSQUEDA_TIMEOUT} s"
            if _es_secreto(nombre) or (glob and not fnmatch.fnmatch(nombre.lower(), glob.lower())):
                continue
            completa = os.path.join(raiz, nombre)
            try:
                if os.path.islink(completa) or os.path.getsize(completa) > MAX_ARCHIVO_BUSQ:
                    continue
                with open(completa, "rb") as f:
                    datos = f.read()
            except OSError:
                continue
            if b"\x00" in datos[:1024]:
                continue
            rel = os.path.relpath(completa, base).replace("\\", "/")
            for n, linea in enumerate(datos.decode("utf-8", "replace").splitlines(), start=1):
                if patron in linea:
                    if len(lineas) == MAX_COINCIDENCIAS:
                        return lineas, True, None
                    lineas.append(f"{rel}:{n}:{linea}")
    return lineas, False, None


def buscar_en_codigo(proyecto, patron, glob=None):
    base, error = _base(proyecto)
    if error:
        return False, error
    if not isinstance(patron, str) or not patron.strip():
        return False, "BLOQUEADO: patrón vacío o no válido"
    if len(patron) > MAX_PATRON or any(c in patron for c in "\x00\r\n"):
        return False, f"BLOQUEADO: el patrón debe ser una sola línea de hasta {MAX_PATRON} caracteres"
    if glob is not None and (not isinstance(glob, str) or not GLOB_VALIDO.fullmatch(glob)):
        return False, f"BLOQUEADO: glob no válido: {glob!r} (usa la forma *.py)"

    r, error = _git(proyecto, base, "rev-parse", ["rev-parse", "--is-inside-work-tree"])
    if error and "superó" in error:
        return False, error
    if r is not None and r.returncode == 0 and r.stdout.strip() == "true":
        # -e: el patrón nunca se interpreta como opción (p. ej. -O ejecuta un programa)
        positivos = [f":(glob)**/{glob}"] if glob else ["."]
        args = ["grep", "-n", "-F", "-I", "--no-color", "-e", patron,
                "--", *positivos, *EXCLUIR_SECRETOS]
        r, error = _git(proyecto, base, "grep", args, BUSQUEDA_TIMEOUT)
        if error:
            return False, error
        if r.returncode not in (0, 1):  # 1 = sin coincidencias
            return False, _fallo(r, proyecto, "grep")
        todas = r.stdout.splitlines()
        lineas, hay_mas = todas[:MAX_COINCIDENCIAS], len(todas) > MAX_COINCIDENCIAS
    else:
        lineas, hay_mas, error = _buscar_sin_git(base, patron, glob)
        if error:
            return False, f"{proyecto}: {error}"

    if not lineas:
        return True, f"{proyecto}: sin coincidencias para '{patron}'"
    recortadas = [l if len(l) <= MAX_ANCHO_LINEA else l[:MAX_ANCHO_LINEA] + "…" for l in lineas]
    aviso = f" (mostrando las primeras {MAX_COINCIDENCIAS})" if hay_mas else ""
    return True, f"{proyecto}, coincidencias de '{patron}'{aviso}:\n" + "\n".join(recortadas)


def ejecutar_tests(proyecto, filtro=None):
    """(True, resumen) si la suite pasa; (False, motivo o resumen) si falla o no se pudo ejecutar."""
    base, error = _base(proyecto)
    if error:
        return False, error
    if proyecto not in COMANDOS_TEST:
        return False, f"{proyecto} no tiene suite de tests configurada. Con tests: {', '.join(COMANDOS_TEST)}."
    if filtro == "":  # vacío = sin filtro
        filtro = None
    if filtro is not None:
        if not isinstance(filtro, str) or filtro.startswith("-") or not FILTRO_VALIDO.fullmatch(filtro):
            return False, f"BLOQUEADO: filtro no válido: {filtro!r}"

    cmd = list(COMANDOS_TEST[proyecto])
    en_wsl = cmd[0] == "wsl.exe"
    if not en_wsl:  # ejecutable relativo al proyecto (en Windows no se resuelve contra cwd)
        cmd[0] = os.path.join(base, cmd[0])
    if filtro:
        cmd += [f"-Dtest={filtro}"] if "mvn" in cmd else ["-k", filtro]
    try:
        # cwd de WSL lo fija --cd; una UNC como cwd de un proceso Windows falla
        r = subprocess.run(cmd, cwd=None if en_wsl else base, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                           errors="replace", timeout=TESTS_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"Los tests de {proyecto} superaron {TESTS_TIMEOUT} s y se cancelaron"
    except OSError as e:
        return False, f"No se pudieron ejecutar los tests de {proyecto}: {e}"

    salida = (r.stdout or "").strip()
    resumen = " | ".join([l.strip() for l in salida.splitlines() if RESUMEN_TEST.search(l.strip())][-3:])
    cola = salida.encode("utf-8")[-MAX_COLA_TEST:].decode("utf-8", "ignore")
    if len(salida.encode("utf-8")) > MAX_COLA_TEST:
        cola = "(…)\n" + cola
    estado = "PASAN" if r.returncode == 0 else f"FALLAN (código {r.returncode})"
    filtro_txt = f" [filtro {filtro}]" if filtro else ""
    return r.returncode == 0, (f"Tests de {proyecto}{filtro_txt}: {estado}. "
                               f"{resumen or 'sin línea de resumen'}\n{cola}")
