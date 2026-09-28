import subprocess
import json
from pathlib import Path
from datetime import datetime

ROADMAP_WSL = "/home/usuariojoaquin/.openclaw/workspace/DAM-Java-Mastery/ROADMAP_TEMAS.md"
REPO_WSL    = "/home/usuariojoaquin/.openclaw/workspace/DAM-Java-Mastery"
HISTORICO   = Path.home() / "AuthorityEngine/score_historico.json"

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
    result = wsl("ps aux | grep racha.py | grep -v grep")
    return bool(result)

# ── SIGUIENTE RACHA ───────────────────────────────────────────────
def siguiente_racha():
    if racha_activa():
        return "Ya hay una racha en ejecución, espera a que termine"

    tema = wsl(f"grep -m1 -- '- \\[ \\]' {ROADMAP_WSL} | sed 's/- \\[ \\] //'")
    if not tema:
        return "No hay temas pendientes en el roadmap"

    tema_safe = tema.replace("'", "").replace('"', '')

    subprocess.Popen([
        "wsl", "bash", "-c",
        f"cd ~/AuthorityEngine && source venv/bin/activate && python3 racha.py '{tema_safe}'"
    ])

    # Marcar solo primera coincidencia con awk
    wsl(f"awk 'BEGIN{{done=0}} /- \\[ \\] {tema_safe}/{{if(!done){{sub(/- \\[ \\]/,\"- [x]\"); done=1}}}} {{print}}' "
        f"{ROADMAP_WSL} > /tmp/roadmap_tmp.md && mv /tmp/roadmap_tmp.md {ROADMAP_WSL}")

    return f"Lanzando racha sobre: {tema}"

def lanzar_racha(tema):
    if racha_activa():
        return "Ya hay una racha en ejecución"

    tema_safe = tema.replace("'", "").replace('"', '')
    subprocess.Popen([
        "wsl", "bash", "-c",
        f"cd ~/AuthorityEngine && source venv/bin/activate && python3 racha.py '{tema_safe}'"
    ])
    return f"Lanzando racha sobre {tema}"

# ── INVENTARIO ────────────────────────────────────────────────────
def actualizar_inventario():
    subprocess.Popen([
        "wsl", "bash", "-c",
        "cd ~/AuthorityEngine && source venv/bin/activate && python3 generar_inventario.py"
    ])
    return "Actualizando inventario y subiendo a GitHub y S3"

# ── DOCUMENTOS ───────────────────────────────────────────────────
def contar_documentos():
    n = wsl("find ~/.openclaw/workspace/DAM-Java-Mastery -name '*_STAFF.md' | wc -l")
    return f"Tienes {n or 0} documentos Staff publicados"

def documentos_en_review():
    n = wsl(f"ls {REPO_WSL}/_Review/ 2>/dev/null | wc -l")
    if not n or n == "0":
        return "No hay documentos en review pendientes"
    return f"Hay {n} documentos en review esperando refinamiento"

# ── S3 ────────────────────────────────────────────────────────────
def documentos_s3():
    n = wsl("aws s3 ls s3://authority-engine-docs-joaquin/ 2>/dev/null | wc -l")
    if not n:
        return "No puedo conectar con AWS S3 ahora mismo"
    return f"Hay {n} documentos en AWS S3"

# ── SCORES ───────────────────────────────────────────────────────
def resumen_scores():
    try:
        if not HISTORICO.exists():
            return "No hay historial de scores todavía"
        with open(HISTORICO, 'r', encoding='utf-8') as f:
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
        return f"Error leyendo historial: {e}"

def progreso_semanal():
    try:
        if not HISTORICO.exists():
            return "No hay datos de progreso todavía"
        with open(HISTORICO, 'r', encoding='utf-8') as f:
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
        return f"Error calculando progreso: {e}"

# ── GIT ───────────────────────────────────────────────────────────
def git_status():
    result = wsl(f"cd {REPO_WSL} && git status --short")
    if not result:
        return "El repositorio está limpio"
    lineas = [l for l in result.split('\n') if l.strip()]
    return f"Hay {len(lineas)} archivos con cambios pendientes"

# ── TEMAS PENDIENTES ─────────────────────────────────────────────
def temas_pendientes():
    n = wsl(f"grep -c '- \\[ \\]' {ROADMAP_WSL}")
    try:
        return f"Tienes {int(n)} temas pendientes en el roadmap"
    except Exception:
        return "No puedo leer el roadmap ahora mismo"

# ── AUTOPILOTO ───────────────────────────────────────────────────
def autopiloto():
    if racha_activa():
        return "Ya hay una racha en ejecución"
    review = documentos_en_review()
    if "Hay" in review:
        return f"Tienes documentos pendientes de revisar antes de lanzar nuevas rachas. {review}"
    return siguiente_racha()

# ── ESTADO GLOBAL ────────────────────────────────────────────────
def estado_global():
    partes = [
        resumen_scores(),
        progreso_semanal(),
        documentos_en_review(),
        git_status()
    ]
    return " | ".join(partes)