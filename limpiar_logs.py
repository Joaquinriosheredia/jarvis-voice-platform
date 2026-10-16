"""Limpieza de un solo uso de los logs anteriores a la privacidad actual.

    venv\\Scripts\\python.exe limpiar_logs.py

Recorre todos los archivos de logs/ (turnos.jsonl, su backup y cualquier otro) y los
*.log de la raíz (jarvis.log):
- Turnos que hoy son privados (una tool de brain.TOOLS_PRIVADAS o una dirección
  dictada en el texto del usuario): se eliminan texto_usuario y respuesta y quedan
  con "privado": true, como los escribe metrics.py ahora.
- Direcciones de correo, escritas o dictadas ("pepe arroba gmail punto com"): <email>.
- Secretos (claves, tokens): redactar_secretos.
Primero muestra el recuento por archivo y pide confirmación. Al aplicar, copia los
originales a logs/backup-AAAAMMDD-HHMMSS/ (logs/ está en .gitignore) y reescribe.
Nunca imprime el contenido de los logs. No toca memoria.json.
"""
import ast
import json
import os
import shutil
import sys
from datetime import datetime

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)

from skills import direccion  # noqa: E402
from skills.secretos import redactar_secretos  # noqa: E402

CAMPOS_TEXTO = ("texto_usuario", "respuesta")


def tools_privadas(raiz=RAIZ):
    """brain.TOOLS_PRIVADAS leído del código (sin importar brain ni crear el cliente)."""
    with open(os.path.join(raiz, "brain.py"), encoding="utf-8") as f:
        for nodo in ast.parse(f.read()).body:
            if isinstance(nodo, ast.Assign) and any(getattr(t, "id", None) == "TOOLS_PRIVADAS" for t in nodo.targets):
                return frozenset(ast.literal_eval(nodo.value))
    raise KeyError("TOOLS_PRIVADAS no está en brain.py")


def redactar(texto):
    """(texto sin direcciones ni secretos, direcciones sustituidas)."""
    if not isinstance(texto, str):
        return texto, 0
    nuevo = direccion.ocultar(redactar_secretos(texto))
    return nuevo, nuevo.count(direccion.OCULTA) - texto.count(direccion.OCULTA)


def _es_privado(reg, privadas):
    return (bool(privadas.intersection(reg.get("tools") or []))
            or direccion.parece_dictada(reg.get("texto_usuario") or ""))


def _redactar_valor(valor, cuenta):
    if isinstance(valor, str):
        nuevo, n = redactar(valor)
        cuenta["emails"] += n
        return nuevo
    if isinstance(valor, list):
        return [_redactar_valor(v, cuenta) for v in valor]
    if isinstance(valor, dict):
        return {k: _redactar_valor(v, cuenta) for k, v in valor.items()}
    return valor


def limpiar_turno(reg, privadas, cuenta):
    """Turno del JSONL limpio. Un turno privado pierde su texto antes de redactar nada."""
    if _es_privado(reg, privadas) and any(c in reg for c in CAMPOS_TEXTO):
        reg = {k: v for k, v in reg.items() if k not in CAMPOS_TEXTO}
        reg["privado"] = True
        cuenta["turnos_privados"] += 1
    return _redactar_valor(reg, cuenta)


def limpiar_jsonl(texto, privadas):
    cuenta = {"lineas": 0, "turnos_privados": 0, "emails": 0, "ilegibles": 0}
    salida = []
    for linea in texto.splitlines(keepends=True):
        fin = "\n" if linea.endswith("\n") else ""
        try:
            reg = json.loads(linea)
        except ValueError:
            reg = None
        if isinstance(reg, dict):
            limpio = limpiar_turno(reg, privadas, cuenta)
            nueva = json.dumps(limpio, ensure_ascii=False) + fin if limpio != reg else linea
        else:  # línea corrupta: se redacta como texto
            if linea.strip():
                cuenta["ilegibles"] += 1
            nueva, n = redactar(linea)
            cuenta["emails"] += n
        cuenta["lineas"] += nueva != linea
        salida.append(nueva)
    return "".join(salida), cuenta


def limpiar_texto(texto):
    cuenta = {"lineas": 0, "turnos_privados": 0, "emails": 0, "ilegibles": 0}
    salida = []
    for linea in texto.splitlines(keepends=True):
        nueva, n = redactar(linea)
        cuenta["emails"] += n
        cuenta["lineas"] += nueva != linea
        salida.append(nueva)
    return "".join(salida), cuenta


def archivos(raiz=RAIZ):
    """Todos los archivos de logs/ (sin las copias de seguridad) y los *.log de la raíz."""
    carpeta = os.path.join(raiz, "logs")
    rutas = []
    if os.path.isdir(carpeta):
        rutas += [os.path.join(carpeta, n) for n in sorted(os.listdir(carpeta))
                  if os.path.isfile(os.path.join(carpeta, n))]
    rutas += [os.path.join(raiz, n) for n in sorted(os.listdir(raiz))
              if n.lower().endswith(".log") and os.path.isfile(os.path.join(raiz, n))]
    return rutas


def analizar(raiz=RAIZ):
    """[(ruta, texto_nuevo o None, cuenta)] sin escribir nada."""
    privadas = tools_privadas(raiz)
    plan = []
    for ruta in archivos(raiz):
        try:
            with open(ruta, encoding="utf-8", newline="") as f:
                texto = f.read()
        except UnicodeDecodeError:
            plan.append((ruta, None, {"omitido": "no es texto UTF-8"}))
            continue
        except OSError as e:
            plan.append((ruta, None, {"omitido": type(e).__name__}))
            continue
        if ruta.lower().endswith(".jsonl"):
            nuevo, cuenta = limpiar_jsonl(texto, privadas)
        else:
            nuevo, cuenta = limpiar_texto(texto)
        plan.append((ruta, nuevo if nuevo != texto else None, cuenta))
    return plan


def informe(plan, raiz=RAIZ):
    lineas = []
    for ruta, nuevo, cuenta in plan:
        nombre = os.path.relpath(ruta, raiz)
        if "omitido" in cuenta:
            lineas.append(f"  {nombre}: omitido ({cuenta['omitido']})")
        elif nuevo is None:
            lineas.append(f"  {nombre}: sin cambios")
        else:
            lineas.append(f"  {nombre}: {cuenta['lineas']} línea(s) cambian; "
                          f"{cuenta['turnos_privados']} turno(s) privado(s) pierden el texto; "
                          f"{cuenta['emails']} dirección(es) → {direccion.OCULTA}"
                          + (f"; {cuenta['ilegibles']} línea(s) no JSON" if cuenta["ilegibles"] else ""))
    return "\n".join(lineas)


def aplicar(plan, raiz=RAIZ, ahora=None):
    """Copia de seguridad de los archivos que cambian y los reescribe. Devuelve la carpeta."""
    cambian = [(r, n) for r, n, _ in plan if n is not None]
    if not cambian:
        return None
    copia = os.path.join(raiz, "logs", f"backup-{(ahora or datetime.now()):%Y%m%d-%H%M%S}")
    os.makedirs(copia, exist_ok=False)
    for ruta, nuevo in cambian:
        shutil.copy2(ruta, os.path.join(copia, os.path.basename(ruta)))
        temporal = ruta + ".limpio"
        with open(temporal, "w", encoding="utf-8", newline="") as f:
            f.write(nuevo)
        os.replace(temporal, ruta)
    return copia


def _imprimir(texto):
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(texto.encode(enc, "replace").decode(enc))


def main(argv=None, entrada=input, raiz=RAIZ):
    plan = analizar(raiz)
    if not plan:
        _imprimir("No hay logs que revisar.")
        return 0
    _imprimir("Cambios propuestos:\n" + informe(plan, raiz))
    if not any(n is not None for _, n, _ in plan):
        _imprimir("Nada que limpiar.")
        return 0
    respuesta = entrada("¿Aplicar? Se guarda antes una copia en logs/backup-<fecha>/ [s/N]: ")
    if respuesta.strip().lower() not in ("s", "si", "sí"):
        _imprimir("Cancelado: no se ha modificado nada.")
        return 1
    try:
        copia = aplicar(plan, raiz)
    except PermissionError:
        _imprimir("ERROR: un log está en uso. Cierra JARVIS y vuelve a ejecutar el script.")
        return 2
    _imprimir(f"Hecho. Originales en {os.path.relpath(copia, raiz)} "
              "(contienen los datos sin redactar: bórralos cuando lo hayas comprobado).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
