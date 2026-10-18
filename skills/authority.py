"""AuthorityEngine (proyecto del autor, en WSL): rachas, inventario, documentos y scores.

Las carpetas salen del perfil de rutas.json ("authority": "motor", "roadmap" y, opcional,
"historico"; relativas al home de WSL). El histórico lo escribe AuthorityEngine en WSL y se lee
desde Windows por \\wsl.localhost. Sin perfil, ninguna función ejecuta nada (ni WSL, ni git, ni
push, ni búsquedas): todas devuelven NO_CONFIGURADO, que el sistema dice tal cual.
"""
import functools
import re
import subprocess
import json
from pathlib import Path
from datetime import datetime

import rutas

NO_CONFIGURADO = ('AuthorityEngine no está configurado. Para usarlo, añade "authority" con "motor" y '
                  '"roadmap" (carpetas del home de WSL) al perfil de rutas.json.')


def config():
    """{"motor", "roadmap"} del perfil, o None."""
    return rutas.authority()


def repo_wsl():
    return rutas.wsl_ruta(config()["roadmap"])


def roadmap_wsl():
    return repo_wsl() + "/ROADMAP_TEMAS.md"


def ruta_historico():
    """score_historico.json en el home de WSL (lo escribe AuthorityEngine), visto desde Windows."""
    return Path(rutas.wsl_unc(config()["historico"]))


def repo_publico():
    """Nombre del repo de documentos (el anuncio de la racha dice dónde publicará)."""
    return config()["roadmap"].rsplit("/", 1)[-1]


def _configurado(funcion):
    @functools.wraps(funcion)
    def envoltura(*args, **kwargs):
        if config() is None:
            return NO_CONFIGURADO
        return funcion(*args, **kwargs)
    return envoltura

# ── WSL HELPER ROBUSTO ────────────────────────────────────────────
def wsl(cmd):
    try:
        result = subprocess.run(
            ["wsl", "bash", "-c", cmd],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()
    except Exception:
        return ""

# ── RACHA ACTIVA ──────────────────────────────────────────────────
def racha_activa():
    if config() is None:
        return False
    result = wsl("ps aux | grep racha.py | grep -v grep")
    return bool(result)

# ── SIGUIENTE RACHA ───────────────────────────────────────────────
@_configurado
def siguiente_racha():
    if racha_activa():
        return "Ya hay una racha en ejecución, espera a que termine"

    tema = wsl(f"grep -m1 -- '- \\[ \\]' {roadmap_wsl()} | sed 's/- \\[ \\] //'")
    if not tema:
        return "No hay temas pendientes en el roadmap"

    tema_safe = tema.replace("'", "").replace('"', '')

    subprocess.Popen([
        "wsl", "bash", "-c",
        f"cd ~/{config()['motor']} && source venv/bin/activate && python3 racha.py '{tema_safe}'"
    ])

    # Marcar solo primera coincidencia con awk
    wsl(f"awk 'BEGIN{{done=0}} /- \\[ \\] {tema_safe}/{{if(!done){{sub(/- \\[ \\]/,\"- [x]\"); done=1}}}} {{print}}' "
        f"{roadmap_wsl()} > /tmp/roadmap_tmp.md && mv /tmp/roadmap_tmp.md {roadmap_wsl()}")

    return f"Lanzando racha sobre: {tema}"

@_configurado
def lanzar_racha(tema):
    if racha_activa():
        return "Ya hay una racha en ejecución"

    tema_safe = tema.replace("'", "").replace('"', '')
    subprocess.Popen([
        "wsl", "bash", "-c",
        f"cd ~/{config()['motor']} && source venv/bin/activate && python3 racha.py '{tema_safe}'"
    ])
    return f"Lanzando racha sobre {tema}"

# ── INVENTARIO ────────────────────────────────────────────────────
@_configurado
def actualizar_inventario():
    subprocess.Popen([
        "wsl", "bash", "-c",
        f"cd ~/{config()['motor']} && source venv/bin/activate && python3 generar_inventario.py"
    ])
    return "Actualizando inventario y subiendo a GitHub y S3"

# ── DOCUMENTOS ───────────────────────────────────────────────────
@_configurado
def contar_documentos():
    n = wsl(f"find ~/{config()['roadmap']} -name '*_STAFF.md' | wc -l")
    return f"Tienes {n or 0} documentos Staff publicados"

@_configurado
def documentos_en_review():
    n = wsl(f"ls {repo_wsl()}/_Review/ 2>/dev/null | wc -l")
    if not n or n == "0":
        return "No hay documentos en review pendientes"
    return f"Hay {n} documentos en review esperando refinamiento"

# ── S3 ────────────────────────────────────────────────────────────
@_configurado
def documentos_s3():
    bucket = rutas.perfil("bucket_authority")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", bucket):
        return "No hay bucket de AuthorityEngine configurado (JARVIS_AUTHORITY_BUCKET)"
    n = wsl(f"aws s3 ls s3://{bucket}/ 2>/dev/null | wc -l")
    if not n:
        return "No puedo conectar con AWS S3 ahora mismo"
    return f"Hay {n} documentos en AWS S3"

# ── SCORES ───────────────────────────────────────────────────────
@_configurado
def resumen_scores():
    try:
        if not ruta_historico().exists():
            return "No hay historial de scores todavía"
        with open(ruta_historico(), 'r', encoding='utf-8') as f:
            historico = json.load(f)
        if not historico:
            return "El historial está vacío"
        scores   = [e.get("score", 0) for e in historico]
        promedio = round(sum(scores) / len(scores), 1)
        ultimo   = historico[-1]
        return (f"Tienes {len(historico)} documentos generados. "
                f"Score medio {promedio}. "
                f"Último: {ultimo.get('tema','?')} con {ultimo.get('score',0)} puntos.")
    except Exception as e:
        return f"Error leyendo historial: {type(e).__name__}"   # sin la ruta (lleva el usuario)

@_configurado
def progreso_semanal():
    try:
        if not ruta_historico().exists():
            return "No hay datos de progreso todavía"
        with open(ruta_historico(), 'r', encoding='utf-8') as f:
            historico = json.load(f)
        hoy    = datetime.now()
        semana = [e for e in historico
                  if (hoy - datetime.strptime(e["fecha"], "%Y-%m-%d %H:%M")).days <= 7]
        if not semana:
            return "No has generado documentos esta semana"
        scores   = [e.get("score", 0) for e in semana]
        promedio = round(sum(scores) / len(scores), 1)
        return (f"Esta semana has generado {len(semana)} documentos "
                f"con score medio de {promedio}.")
    except Exception as e:
        return f"Error calculando progreso: {type(e).__name__}"

# ── GIT ───────────────────────────────────────────────────────────
@_configurado
def git_status():
    result = wsl(f"cd {repo_wsl()} && git status --short")
    if not result:
        return "El repositorio está limpio"
    lineas = [l for l in result.split('\n') if l.strip()]
    return f"Hay {len(lineas)} archivos con cambios pendientes"

# ── TEMAS PENDIENTES ─────────────────────────────────────────────
@_configurado
def temas_pendientes():
    n = wsl(f"grep -c '- \\[ \\]' {roadmap_wsl()}")
    try:
        return f"Tienes {int(n)} temas pendientes en el roadmap"
    except Exception:
        return "No puedo leer el roadmap ahora mismo"

# ── AUTOPILOTO ───────────────────────────────────────────────────
@_configurado
def autopiloto():
    if racha_activa():
        return "Ya hay una racha en ejecución"
    review = documentos_en_review()
    if "Hay" in review:
        return f"Tienes documentos pendientes de revisar antes de lanzar nuevas rachas. {review}"
    return siguiente_racha()

# ── ESTADO GLOBAL ────────────────────────────────────────────────
@_configurado
def estado_global():
    partes = [
        resumen_scores(),
        progreso_semanal(),
        documentos_en_review(),
        git_status()
    ]
    return " | ".join(partes)