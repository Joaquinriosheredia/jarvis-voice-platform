"""Replay de turnos de JARVIS (logs/turnos.jsonl).

    python replay.py            últimos 10 turnos
    python replay.py 25         últimos 25
    python replay.py --resumen  totales de hoy y de los últimos 7 días
"""
import datetime
import sys

import metrics


def _hora(ts):
    try:
        return datetime.datetime.fromisoformat(ts).strftime("%d/%m %H:%M:%S")
    except (TypeError, ValueError):
        return "?"


def tabla(turnos, n=10):
    filas = [f"{'hora':<15} {'ruta':<6} {'tools':<24} {'total':>7} {'coste':>9}  texto"]
    for t in turnos[-n:]:
        texto = "(privado)" if t.get("privado") else (t.get("texto_usuario") or "")
        texto = texto if len(texto) <= 50 else texto[:49] + "…"
        tools = ",".join(t.get("tools") or []) or "-"
        tools = tools if len(tools) <= 24 else tools[:23] + "…"
        filas.append(f"{_hora(t.get('ts')):<15} {t.get('ruta') or '?':<6} {tools:<24} "
                     f"{t.get('total_ms', 0):>5}ms ${t.get('coste_usd', 0):>8.4f}  {texto}")
    return "\n".join(filas)


def _bloque(titulo, r):
    if not r["turnos"]:
        return f"{titulo}: sin turnos"
    rutas = ", ".join(f"{ruta} {pct}%" for ruta, pct in r["rutas_pct"].items())
    cache = f"{r['cache_ratio'] * 100:.1f}%" if r["cache_ratio"] is not None else "-"
    return (f"{titulo}: {r['turnos']} turnos · ${r['coste_usd']:.4f} · p50 {r['p50_ms']} ms · "
            f"p95 {r['p95_ms']} ms · cache {cache}\n  rutas: {rutas}")


def texto_resumen(turnos, hoy=None):
    return "\n".join([
        _bloque("Hoy", metrics.resumen(metrics.turnos_desde(turnos, 1, hoy))),
        _bloque("Últimos 7 días", metrics.resumen(metrics.turnos_desde(turnos, 7, hoy))),
    ])


def main(argv):
    turnos = metrics.cargar()
    if "--resumen" in argv:
        print(texto_resumen(turnos))
        return 0
    try:
        n = int(argv[0]) if argv else 10
    except ValueError:
        print(__doc__)
        return 2
    print(tabla(turnos, max(1, n)) if turnos else "No hay turnos registrados.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
