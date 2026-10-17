"""Briefing hablado: el saludo de arranque y la orden "dame el briefing".

Corto (MAX_FRASES frases contando el saludo y la pregunta final; el aviso de
autorización de Google, si lo hay, va aparte) y con lo más
urgente primero, en este orden: agenda de hoy, GitHub (CI en rojo, PRs esperando
review), correo sin leer, tareas y tiempo. Lo que no cabe se queda fuera.
Solo lee: Calendar (events.list), Gmail (no leídos), GitHub (GET) y la memoria de
tareas. Nunca ejecuta una acción ni usa la memoria entre sesiones.

JARVIS_BRIEFING=0 desactiva el briefing del arranque (queda solo el saludo y los
avisos); "dame el briefing" sigue funcionando.
"""
import os
import re
from datetime import datetime

from skills import calendario, github

MAX_FRASES  = 6
MAX_TITULO  = 60
CIERRE      = "¿Qué puedo hacer por usted hoy?"
PRIORIDAD   = ("agenda", "github", "emails", "tareas", "tiempo")

# Resultado de google_auth.comprobar_todos() al arrancar (lo pone jarvis.py). None =
# sin comprobar: se intenta como siempre. Con un token roto no se toca ese servicio
# (ni se abre el navegador de OAuth en mitad de una conversación).
autorizacion = None

_github_avisado = False   # el fallo de GitHub se dice una vez por sesión


def activado():
    return os.environ.get("JARVIS_BRIEFING", "1").strip() != "0"


def disponible(servicio, resultados=None):
    resultados = autorizacion if resultados is None else resultados
    if resultados is None:
        return True
    return bool((resultados.get(servicio) or {}).get("ok"))


def saludo(hora=None):
    hora = datetime.now().hour if hora is None else hora
    momento = "Buenos días" if hora < 12 else ("Buenas tardes" if hora < 20 else "Buenas noches")
    return f"{momento}, señor Ríos."


def contar_frases(texto):
    texto = (texto or "").strip()
    if not texto:
        return 0
    return max(1, len(re.findall(r"[.!?]+(?=\s|$)", texto)))


def componer(saludo_, items, aviso="", cierre=CIERRE, max_frases=MAX_FRASES):
    """Saludo + los items que quepan, en orden de prioridad + aviso + cierre. El aviso de
    autorización de Google no cuenta para el tope: es una alerta puntual que hay que dar."""
    libres = max_frases - contar_frases(saludo_) - contar_frases(cierre)
    elegidos = []
    for item in items:
        n = contar_frases(item)
        if n and n <= libres:
            elegidos.append(item.strip())
            libres -= n
    return " ".join([saludo_, *elegidos, *([aviso.strip()] if aviso else []), cierre])


# ── FRASES ────────────────────────────────────────────────────────
def _titulo(titulo):
    # Sin puntos ni signos: un título no puede añadir frases ni cortar la voz
    limpio = " ".join(re.sub(r"[.!?¿¡;:\n]+", " ", titulo or "").split())
    return limpio[:MAX_TITULO] or "un evento sin título"


def frase_agenda(eventos):
    if not eventos:
        return "Hoy no tiene más eventos en la agenda."
    primero = eventos[0]
    cuando = f"a las {primero['hora']}" if primero.get("hora") else "todo el día"
    if len(eventos) == 1:
        return f"Hoy tiene {_titulo(primero['titulo'])} {cuando}."
    return f"Hoy tiene {len(eventos)} eventos; el primero, {_titulo(primero['titulo'])} {cuando}."


def frase_tareas(vencidas, pendientes):
    if vencidas:
        return f"Tiene {len(vencidas)} tareas vencidas."
    if pendientes:
        return f"Tiene {len(pendientes)} tareas pendientes."
    return ""


def frase_github():
    """Estado de GitHub; si falla, lo dice solo la primera vez en la sesión."""
    global _github_avisado
    datos = github.estado()
    if datos.get("causa"):
        if _github_avisado:
            return ""
        _github_avisado = True
    return github.frase(datos)


def _enumerar(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1]


def resumen_emails(gmail):
    """Resumen breve de los no leídos, sin LLM. "" si no hay o si falla Gmail."""
    try:
        datos = gmail.no_leidos_arranque(max_emails=10)
        if not datos or not datos[1]:
            return ""
        total, emails = datos
        if total <= 3:
            items = []
            for e in emails:
                asunto = " ".join(e["asunto"].split()[:8])
                if asunto and asunto != "Sin asunto":
                    items.append(f"uno de {e['remitente']} sobre {asunto}")
                else:
                    items.append(f"uno de {e['remitente']}")
            cuantos = "un email" if total == 1 else f"{total} emails"
            return f"Tienes {cuantos}: {_enumerar(items)}."
        # Más de 3: agrupa por remitente, en orden de llegada (más recientes primero)
        conteo = {}
        for e in emails:
            conteo[e["remitente"]] = conteo.get(e["remitente"], 0) + 1
        grupos = [r if n == 1 else f"{n} de {r}" for r, n in list(conteo.items())[:3]]
        return f"Tienes {total} emails. Los más recientes son de {_enumerar(grupos)}."
    except Exception:
        return ""


# ── FUENTES Y GENERACIÓN ──────────────────────────────────────────
def fuentes(sistema, emails, tareas_vencidas, tareas_pendientes, resultados=None):
    """{clave: callable() -> frase} de solo lectura. Un servicio con el token roto no se toca."""
    def agenda():
        ok, eventos = calendario.eventos_hoy()
        return frase_agenda(eventos) if ok else ""

    f = {
        "github": frase_github,
        "tareas": lambda: frase_tareas(tareas_vencidas(), tareas_pendientes()),
        "tiempo": lambda: sistema.tiempo_sevilla() + ".",
    }
    if disponible("calendar", resultados):
        f["agenda"] = agenda
    if disponible("gmail", resultados):
        f["emails"] = emails
    return f


def generar(fuentes_, aviso="", hora=None):
    items = []
    for clave in PRIORIDAD:
        fuente = fuentes_.get(clave)
        if not fuente:
            continue
        try:
            items.append(fuente() or "")
        except Exception:      # una fuente rota no tumba el briefing
            items.append("")
    return componer(saludo(hora), items, aviso)


def desde_skills(skills, hora=None):
    """Briefing completo para la orden "dame el briefing" (tool briefing)."""
    import memoria as mem
    datos = skills.get("memoria") or {}
    return generar(fuentes(skills.get("sistema"), lambda: resumen_emails(skills.get("gmail")),
                           lambda: mem.tareas_vencidas(datos), lambda: mem.tareas_pendientes(datos)),
                   hora=hora)
