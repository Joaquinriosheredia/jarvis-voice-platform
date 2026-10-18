"""Limpieza de un solo uso de memoria.json y memoria.json.bak.

    venv\\Scripts\\python.exe limpiar_memoria.py

Sustituye por <email> cada dirección de correo (escrita o dictada) del historial de
conversación, con el mismo enmascarado que los logs (direccion.redactar). Las notas,
las tareas y el resto de campos quedan intactos. Primero muestra cuántas direcciones
se redactarían y pide confirmación; al aplicar, copia los originales a
logs/backup-memoria-AAAAMMDD-HHMMSS/ (logs/ está en .gitignore).
Con JARVIS abierto no hace nada: al cerrarse volvería a escribir memoria.json.
Nunca imprime el contenido de la memoria.
"""
import json
import os
import shutil
import socket
import sys
from datetime import datetime

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)

from skills import direccion  # noqa: E402

ARCHIVOS  = ("memoria.json", "memoria.json.bak")
PUERTO_UI = 8765


def jarvis_abierto(puerto=PUERTO_UI):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", puerto)) == 0


def limpiar(datos):
    """(memoria con el historial redactado, entradas que cambian, direcciones redactadas).
    Solo toca historial[*].texto."""
    nuevo = dict(datos)
    entradas = direcciones = 0
    historial = []
    for h in datos.get("historial") or []:
        if isinstance(h, dict) and isinstance(h.get("texto"), str):
            texto = direccion.redactar(h["texto"])
            if texto != h["texto"]:
                entradas += 1
                direcciones += texto.count(direccion.OCULTA) - h["texto"].count(direccion.OCULTA)
                h = dict(h, texto=texto)
        historial.append(h)
    if "historial" in datos:
        nuevo["historial"] = historial
    return nuevo, entradas, direcciones


def analizar(raiz=RAIZ):
    """[(ruta, memoria_nueva o None, entradas, direcciones, error)] sin escribir nada."""
    plan = []
    for nombre in ARCHIVOS:
        ruta = os.path.join(raiz, nombre)
        if not os.path.isfile(ruta):
            continue
        try:
            with open(ruta, encoding="utf-8-sig") as f:
                datos = json.load(f)
        except (OSError, ValueError) as e:
            plan.append((ruta, None, 0, 0, type(e).__name__))
            continue
        if not isinstance(datos, dict):
            plan.append((ruta, None, 0, 0, "formato inesperado"))
            continue
        nuevo, entradas, direcciones = limpiar(datos)
        plan.append((ruta, nuevo if entradas else None, entradas, direcciones, None))
    return plan


def informe(plan):
    lineas = []
    for ruta, nuevo, entradas, direcciones, error in plan:
        nombre = os.path.basename(ruta)
        if error:
            lineas.append(f"  {nombre}: omitido ({error})")
        elif nuevo is None:
            lineas.append(f"  {nombre}: sin direcciones")
        else:
            lineas.append(f"  {nombre}: {direcciones} dirección(es) → {direccion.OCULTA} "
                          f"en {entradas} entrada(s) del historial; notas y tareas intactas")
    return "\n".join(lineas)


def aplicar(plan, raiz=RAIZ, ahora=None):
    cambian = [(r, n) for r, n, *_ in plan if n is not None]
    if not cambian:
        return None
    copia = os.path.join(raiz, "logs", f"backup-memoria-{(ahora or datetime.now()):%Y%m%d-%H%M%S}")
    os.makedirs(copia, exist_ok=False)
    for ruta, nuevo in cambian:
        shutil.copy2(ruta, os.path.join(copia, os.path.basename(ruta)))
        temporal = ruta + ".limpio"
        with open(temporal, "w", encoding="utf-8") as f:
            json.dump(nuevo, f, ensure_ascii=False, indent=2)  # mismo formato que memoria.guardar
        os.replace(temporal, ruta)
    return copia


def _imprimir(texto):
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(texto.encode(enc, "replace").decode(enc))


def main(argv=None, entrada=input, raiz=RAIZ, abierto=jarvis_abierto):
    if abierto():
        _imprimir("JARVIS parece estar abierto (puerto 8765 en uso). Ciérralo y vuelve a ejecutar el script: "
                  "al cerrarse reescribiría memoria.json.")
        return 2
    plan = analizar(raiz)
    if not plan:
        _imprimir("No hay memoria.json ni memoria.json.bak.")
        return 0
    _imprimir("Cambios propuestos:\n" + informe(plan))
    if not any(n is not None for _, n, *_ in plan):
        _imprimir("Nada que limpiar.")
        return 0
    respuesta = entrada("¿Aplicar? Se guarda antes una copia en logs/backup-memoria-<fecha>/ [s/N]: ")
    if respuesta.strip().lower() not in ("s", "si", "sí"):
        _imprimir("Cancelado: no se ha modificado nada.")
        return 1
    copia = aplicar(plan, raiz)
    _imprimir(f"Hecho. Originales en {os.path.relpath(copia, raiz)} "
              "(contienen las direcciones: bórralos cuando lo hayas comprobado).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
