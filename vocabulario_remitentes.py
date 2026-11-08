"""Propone los remitentes frecuentes de tu bandeja para el vocabulario de Whisper.

Todo en local: lee las cabeceras From de los últimos emails de la bandeja de entrada con tu
token de Gmail y cuenta los nombres. Sin --guardar solo los muestra. Con --guardar los escribe
en la carpeta de secretos (fuera del repo, nunca se sube) como vocabulario_remitentes.txt, que
audio.py añade detrás de vocabulario_whisper.txt al arrancar JARVIS (con el mismo tope de 40
términos en total). Nada sale del equipo salvo la propia lectura de Gmail.

    python vocabulario_remitentes.py            # muestra la propuesta
    python vocabulario_remitentes.py --guardar  # la guarda (pide confirmación)
"""
import argparse
import collections
import os
import sys

import rutas
from skills import gmail

NOMBRE_ARCHIVO = "vocabulario_remitentes.txt"   # mismo nombre que audio.NOMBRE_VOCABULARIO_REMITENTES
MAX_PROPUESTOS = 10
MAX_TERMINO    = 40   # mismo límite que audio.MAX_TERMINO


def contar_remitentes(service, maximo=200):
    """Counter {nombre visible: veces} de los últimos `maximo` emails de la bandeja de entrada."""
    res = service.users().messages().list(userId='me', q='in:inbox', maxResults=maximo).execute()
    cuenta = collections.Counter()
    for msg in res.get('messages', []):
        remitente, _ = gmail._get_headers(service, msg['id'])
        nombre = gmail._nombre_visible(remitente)
        if nombre and "@" not in nombre and len(nombre) <= MAX_TERMINO and "confirm" not in nombre.lower():
            cuenta[nombre] += 1
    return cuenta


def propuesta(cuenta, existentes=(), maximo=MAX_PROPUESTOS):
    """Los más frecuentes (al menos 2 emails) que no estén ya en el vocabulario."""
    ya = {t.lower() for t in existentes}
    return [n for n, veces in cuenta.most_common() if veces >= 2 and n.lower() not in ya][:maximo]


def main(argv=None):
    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("--guardar", action="store_true", help="guarda la propuesta (pide confirmación)")
    opciones = args.parse_args(argv)
    ruta_vocab = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vocabulario_whisper.txt")
    try:
        with open(ruta_vocab, encoding="utf-8-sig") as f:
            existentes = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except OSError:
        existentes = []
    nombres = propuesta(contar_remitentes(gmail.get_service()), existentes)
    if not nombres:
        print("No hay remitentes frecuentes nuevos que proponer.")
        return 0
    print("Remitentes frecuentes propuestos para Whisper:")
    for n in nombres:
        print(f"  - {n}")
    if not opciones.guardar:
        print("\nPara añadirlos: python vocabulario_remitentes.py --guardar")
        return 0
    destino = rutas.en_secretos(NOMBRE_ARCHIVO)
    if input(f"\n¿Guardar en {destino}? Escribe 'si' para confirmar: ").strip().lower() not in ("si", "sí"):
        print("No se ha guardado nada.")
        return 1
    with open(destino, "w", encoding="utf-8") as f:
        f.write("# Remitentes frecuentes (vocabulario_remitentes.py). Local: no está en el repo.\n")
        f.write("\n".join(nombres) + "\n")
    print("Guardado. JARVIS los usará al arrancar de nuevo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
