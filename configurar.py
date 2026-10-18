"""Asistente de configuración: la clave de Anthropic y cómo activar los servicios opcionales.

    venv\\Scripts\\python.exe configurar.py      (instalar.cmd lo ofrece al final)

- Pide la clave sin mostrarla (getpass): no sale en pantalla ni queda en el historial de la
  consola. Nunca se acepta como argumento, que sí quedaría en el historial.
- La valida con una llamada mínima y sin coste (la lista de modelos) antes de guardarla.
- La escribe en <carpeta de secretos>\\.env: si el archivo existe, cambia solo esa línea y
  conserva el resto; si no, parte de la plantilla env.example. Escritura atómica.
- Deja el .env solo para el usuario actual (icacls: sin herencia, control total al usuario).
- Idempotente: si ya hay una clave, pregunta antes de sustituirla (por defecto, no).

Nunca imprime ni registra la clave, ni un trozo: como mucho, su longitud.
"""
import getpass
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


def restringir(ruta):
    """Solo el usuario actual puede leer y escribir el archivo. True si se ha podido."""
    if os.name != "nt":
        os.chmod(ruta, 0o600)
        return True
    usuario = os.environ.get("USERNAME", "")
    dominio = os.environ.get("USERDOMAIN", "")
    if not usuario:
        return False
    cuenta = f"{dominio}\\{usuario}" if dominio else usuario
    r = subprocess.run(["icacls", ruta, "/inheritance:r", "/grant:r", f"{cuenta}:F"],
                       capture_output=True, text=True, timeout=30)
    return r.returncode == 0


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


def main(pedir=getpass.getpass, preguntar=input, validar_=validar):
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
