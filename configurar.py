"""Asistente de configuración: la clave de Anthropic y cómo activar los servicios opcionales.

    venv\\Scripts\\python.exe configurar.py      (instalar.cmd lo ofrece al final)

- Pide la clave sin mostrarla (getpass): no sale en pantalla ni queda en el historial de la
  consola. Nunca se acepta como argumento, que sí quedaría en el historial.
- La valida con una llamada mínima y sin coste (la lista de modelos) antes de guardarla.
- La escribe en <carpeta de secretos>\\.env: si el archivo existe, cambia solo esa línea y
  conserva el resto; si no, parte de la plantilla env.example. Escritura atómica.
- Deja el .env solo para el usuario actual (DACL protegida con una sola entrada: su SID).
- Idempotente: si ya hay una clave, pregunta antes de sustituirla (por defecto, no).
- Opcional: cómo quieres que te llame y tu ciudad (sus coordenadas, con el geocodificador
  gratuito de Open-Meteo, sin clave), en el "perfil" de rutas.json. Nunca sustituye lo que ya
  haya; si no encuentra la ciudad, la guarda sin coordenadas y el tiempo queda desactivado.

Nunca imprime ni registra la clave, ni un trozo: como mucho, su longitud.
"""
import getpass
import json
import os
import re
import subprocess
import sys
import tempfile

import rutas

CLAVE      = "ANTHROPIC_API_KEY"
INTENTOS   = 3
TIMEOUT    = 15      # s de la llamada de validación
RE_LINEA   = re.compile(rf"^\s*{CLAVE}\s*=(.*)$")

OK, RECHAZADA, SIN_RED, ERROR = "ok", "rechazada", "sin_red", "error"

SERVICIOS = """
Servicios opcionales (JARVIS funciona sin ellos; puedes activarlos cuando quieras):
  - Gmail, Calendar y Contactos: descarga el cliente OAuth de Google (credentials.json) a la
    carpeta de secretos, como explica «Autorización de Google» en el README, y ejecuta
    venv\\Scripts\\python.exe reautorizar.py gmail   (o calendar, o contacts).
  - GitHub (CI y PRs en el briefing): un token fine-grained de solo lectura en GITHUB_TOKEN y
    tus repos en JARVIS_GITHUB_REPOS (dueño/repo), en el .env.
  - Voz de ElevenLabs: JARVIS_TTS=elevenlabs, ELEVENLABS_API_KEY y ELEVENLABS_VOICE_ID en el
    .env. Sin ellas habla Piper, local y gratis.
  - Búsqueda web mejor: TAVILY_API_KEY en el .env. Sin ella, Wikipedia y DuckDuckGo.
  - Respuestas locales rápidas: winget install Ollama.Ollama y ollama pull qwen2.5:3b.
    Sin Ollama todo va a Claude.
Comprueba cómo queda todo con: venv\\Scripts\\python.exe doctor.py
"""


def ruta_env():
    return rutas.en_secretos(".env")


def leer_lineas(ruta):
    """Líneas del .env (sin el salto final), o las de la plantilla env.example si no existe."""
    origen = ruta if os.path.isfile(ruta) else rutas.en_raiz("env.example")
    try:
        with open(origen, encoding="utf-8-sig") as f:
            return f.read().splitlines()
    except OSError:
        return []


def clave_actual(lineas):
    """Valor de ANTHROPIC_API_KEY en las líneas ("" si no está o está vacía)."""
    for linea in lineas:
        m = RE_LINEA.match(linea)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


def con_clave(lineas, valor):
    """Las mismas líneas con ANTHROPIC_API_KEY=valor (la primera aparición, o al final)."""
    nuevas, puesta = [], False
    for linea in lineas:
        if not puesta and RE_LINEA.match(linea):
            nuevas.append(f"{CLAVE}={valor}")
            puesta = True
        else:
            nuevas.append(linea)
    if not puesta:
        nuevas.append(f"{CLAVE}={valor}")
    return nuevas


def _sid_usuario():
    """SID del usuario actual (S-1-5-...), sin depender del idioma de Windows."""
    r = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=30)
    m = re.search(r'"(S-1-[0-9-]+)"', r.stdout)
    return m.group(1) if m else None


def restringir(ruta):
    """Solo el usuario actual puede leer y escribir el archivo: DACL protegida (sin herencia) con
    una sola entrada. Sustituye también las entradas explícitas que traiga el archivo (p. ej. las
    de SYSTEM y Administradores con una consola elevada). True si se ha podido."""
    if os.name != "nt":
        os.chmod(ruta, 0o600)
        return True
    import ctypes
    from ctypes import wintypes
    sid = _sid_usuario()
    if not sid:
        return False
    advapi, kernel = ctypes.windll.advapi32, ctypes.windll.kernel32
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL)]
    advapi.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p]
    advapi.SetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    sd = ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(f"D:P(A;;FA;;;{sid})", 1,
                                                                        ctypes.byref(sd), None):
        return False
    try:
        presente, por_defecto, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not advapi.GetSecurityDescriptorDacl(sd, ctypes.byref(presente), ctypes.byref(dacl),
                                                ctypes.byref(por_defecto)):
            return False
        SE_FILE_OBJECT, DACL, PROTEGIDA = 1, 0x4, 0x80000000
        return advapi.SetNamedSecurityInfoW(ruta, SE_FILE_OBJECT, DACL | PROTEGIDA,
                                            None, None, dacl, None) == 0
    finally:
        kernel.LocalFree(sd)


def escribir(ruta, lineas):
    """Escritura atómica: archivo temporal restringido en la misma carpeta y os.replace."""
    carpeta = os.path.dirname(ruta)
    os.makedirs(carpeta, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".env-", suffix=".tmp", dir=carpeta)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lineas) + "\n")
        restringir(tmp)
        os.replace(tmp, ruta)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return restringir(ruta)


def validar(valor, cliente=None):
    """OK, RECHAZADA, SIN_RED o ERROR, con una llamada que no gasta tokens (lista de modelos)."""
    import anthropic
    try:
        c = cliente or anthropic.Anthropic(api_key=valor, max_retries=0, timeout=TIMEOUT)
        c.models.list(limit=1)
        return OK
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        return RECHAZADA
    except (anthropic.APIConnectionError, anthropic.APITimeoutError):
        return SIN_RED
    except Exception:
        return ERROR


def _si(pregunta, defecto, preguntar):
    sufijo = " [S/n] " if defecto else " [s/N] "
    respuesta = (preguntar(pregunta + sufijo) or "").strip().lower()
    return defecto if not respuesta else respuesta in ("s", "si", "sí", "y", "yes")


def _pedir_clave(pedir, preguntar, validar_):
    """La clave nueva ya validada (o aceptada sin validar), o None si se cancela."""
    for _ in range(INTENTOS):
        valor = (pedir("Clave de Anthropic (no se muestra al escribir; Intro para cancelar): ") or "").strip()
        if not valor:
            return None
        if re.search(r"\s", valor) or len(valor) < 20:
            print("Eso no parece una clave de Anthropic (empieza por sk-ant- y no lleva espacios).")
            continue
        estado = validar_(valor)
        if estado == OK:
            print("Clave válida.")
            return valor
        if estado == RECHAZADA:
            print("Anthropic ha rechazado la clave. Cópiala de nuevo desde console.anthropic.com.")
            continue
        motivo = "no hay conexión con Anthropic" if estado == SIN_RED else "Anthropic ha devuelto un error"
        if _si(f"No se ha podido validar ({motivo}). ¿Guardarla sin validar?", False, preguntar):
            return valor
        return None
    print("Demasiados intentos.")
    return None


URL_GEOCODIFICAR = "https://geocoding-api.open-meteo.com/v1/search"
MAX_CIUDADES     = 5


def geocodificar(ciudad):
    """[{"ciudad", "provincia", "pais", "latitud", "longitud"}] de Open-Meteo (sin clave), o None
    si no se puede consultar (sin red, error del servicio)."""
    import requests
    try:
        r = requests.get(URL_GEOCODIFICAR, params={"name": ciudad, "count": MAX_CIUDADES, "language": "es",
                                                   "format": "json"}, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        resultados = r.json().get("results") or []
    except Exception:
        return None
    lugares = []
    for x in resultados[:MAX_CIUDADES]:
        try:
            provincia = re.sub(r"^(Provincia de|Province of)\s+", "", str(x.get("admin2") or x.get("admin1") or ""))
            lugares.append({"ciudad": str(x["name"]), "provincia": provincia,
                            "pais": str(x.get("country") or ""), "latitud": round(float(x["latitude"]), 4),
                            "longitud": round(float(x["longitude"]), 4)})
        except (KeyError, TypeError, ValueError):
            continue
    return lugares


def leer_rutas(ruta):
    try:
        with open(ruta, encoding="utf-8-sig") as f:
            datos = json.load(f)
        return datos if isinstance(datos, dict) else None
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None   # dañado: no se toca


def escribir_rutas(ruta, datos):
    carpeta = os.path.dirname(ruta)
    os.makedirs(carpeta, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".rutas-", suffix=".tmp", dir=carpeta)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(datos, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, ruta)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _nombre_lugar(lugar):
    return ", ".join(p for p in (lugar["ciudad"], lugar["provincia"], lugar["pais"]) if p)


def configurar_perfil(preguntar=input, geocodificar_=geocodificar, ruta=None):
    """Pregunta (opcional) cómo llamarte y tu ciudad y lo añade al perfil de rutas.json sin
    sustituir nada. Devuelve las claves añadidas."""
    ruta = ruta or rutas.en_secretos(rutas.ARCHIVO)
    datos = leer_rutas(ruta)
    if datos is None:
        print(f"Aviso: {ruta} no es un JSON válido; no se toca. Corrígelo y vuelve a ejecutar configurar.py.")
        return []
    perfil = datos.get("perfil") if isinstance(datos.get("perfil"), dict) else {}
    nuevo = {}
    print("\nPerfil (opcional; Intro para omitir cada pregunta):")
    if perfil.get("saludo"):
        print("  El saludo ya está configurado: no se cambia.")
    else:
        nombre = " ".join((preguntar("  ¿Cómo quieres que te llame? (p. ej. Ana): ") or "").split())[:60]
        if nombre:
            nuevo["saludo"] = nombre
            if not perfil.get("nombre"):
                nuevo["nombre"] = nombre
    if perfil.get("ciudad"):
        print("  La ciudad ya está configurada: no se cambia.")
    else:
        ciudad = " ".join((preguntar("  ¿En qué ciudad estás? Sirve para el tiempo: ") or "").split())[:80]
        if ciudad:
            lugares = geocodificar_(ciudad)
            elegido = None
            if lugares:
                for i, lugar in enumerate(lugares, 1):
                    print(f"    {i}. {_nombre_lugar(lugar)}")
                eleccion = (preguntar("  ¿Cuál es? (número, Intro = 1, 0 = ninguna): ") or "1").strip()
                if eleccion.isdigit() and 1 <= int(eleccion) <= len(lugares):
                    elegido = lugares[int(eleccion) - 1]
            if elegido:
                nuevo.update(elegido)
                print(f"  Tiempo de {_nombre_lugar(elegido)}.")
            else:
                nuevo["ciudad"] = ciudad
                motivo = ("no hay conexión con el servicio de coordenadas" if lugares is None
                          else "no he encontrado sus coordenadas")
                print(f"  Guardo la ciudad, pero {motivo}: el tiempo queda desactivado. Puedes añadir "
                      "latitud y longitud al perfil de rutas.json o volver a ejecutar configurar.py.")
    if not nuevo:
        return []
    datos["perfil"] = {**perfil, **{k: v for k, v in nuevo.items() if k not in perfil}}
    escribir_rutas(ruta, datos)
    print(f"  Perfil guardado en {ruta}.")
    return sorted(k for k in nuevo if k not in perfil)


def main(pedir=getpass.getpass, preguntar=input, validar_=validar, geocodificar_=geocodificar):
    ruta = ruta_env()
    print(f"Configuración de JARVIS. Archivo de claves: {ruta}")
    lineas = leer_lineas(ruta)
    actual = clave_actual(lineas)
    nueva = None
    if actual:
        print(f"Ya hay una clave de Anthropic guardada ({len(actual)} caracteres).")
        if _si("¿Comprobar que sigue siendo válida?", True, preguntar):
            estado = validar_(actual)
            print({OK: "La clave guardada es válida.",
                   RECHAZADA: "Anthropic rechaza la clave guardada: conviene sustituirla.",
                   SIN_RED: "No se ha podido comprobar: no hay conexión con Anthropic."}.get(
                       estado, "No se ha podido comprobar: Anthropic ha devuelto un error."))
        if _si("¿Sustituirla por otra?", False, preguntar):
            nueva = _pedir_clave(pedir, preguntar, validar_)
    else:
        nueva = _pedir_clave(pedir, preguntar, validar_)
        if nueva is None:
            print("No se ha guardado ninguna clave. JARVIS no arranca sin ella; vuelve a ejecutar configurar.py.")

    if nueva is not None:
        restringido = escribir(ruta, con_clave(lineas, nueva))
        print(f"Clave guardada en {ruta}.")
        if not restringido:
            print("Aviso: no se han podido restringir los permisos del archivo; revisa quién puede leerlo.")
    elif os.path.isfile(ruta) and not restringir(ruta):
        print("Aviso: no se han podido restringir los permisos del archivo; revisa quién puede leerlo.")

    guardada = nueva or actual
    del_entorno = os.environ.get(CLAVE, "").strip()
    if guardada and del_entorno and del_entorno != guardada:
        print(f"Aviso: la variable de entorno {CLAVE} de Windows tiene otro valor y manda sobre el .env. "
              "Bórrala o actualízala en las variables de entorno de tu usuario.")
    configurar_perfil(preguntar, geocodificar_)
    print(SERVICIOS)
    return 0 if guardada else 1


if __name__ == "__main__":
    if not sys.stdin.isatty():
        print("Ejecuta configurar.py en una consola: pide la clave sin mostrarla.")
        sys.exit(2)
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nCancelado. No se ha cambiado nada.")
        sys.exit(1)
