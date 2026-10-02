from dotenv import load_dotenv
load_dotenv(r"C:\Jarvis-secrets\.env")

import whisper
import atexit
from datetime import datetime

import audio
import brain
import memoria as mem
from skills import musica, sistema, webs, authority, tareas, gmail

# ── CARGAR MODELOS ────────────────────────────────────────────────
print("🤖 Cargando Jarvis...")
whisper_model = whisper.load_model("small")
print("✅ Jarvis listo")

# ── MEMORIA ───────────────────────────────────────────────────────
memoria = mem.cargar()
memoria = mem.iniciar_sesion(memoria)

# ── SKILLS ────────────────────────────────────────────────────────
SKILLS = {
    'musica':    musica,
    'sistema':   sistema,
    'webs':      webs,
    'authority': authority,
    'tareas':    tareas,
    'gmail':     gmail,
    'memoria':   memoria,
    'audio':     audio,
}

# ── CLEANUP ───────────────────────────────────────────────────────
def cleanup():
    musica.cleanup()
    mem.guardar(memoria)
    print("👋 Jarvis cerrado correctamente")

atexit.register(cleanup)

# ── RESUMEN DE EMAILS AL ARRANCAR (sin LLM) ──────────────────────
def _enumerar(items):
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " y " + items[-1]

def _resumen_emails_arranque():
    """Resumen breve de los no leídos construido en Python.
    Cadena vacía si no hay emails o si falla Gmail: nunca interrumpe el arranque."""
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

# ── RESUMEN DIARIO ────────────────────────────────────────────────
def resumen_diario():
    hora = datetime.now().hour
    if hora < 12:
        saludo = "Buenos días"
    elif hora < 20:
        saludo = "Buenas tardes"
    else:
        saludo = "Buenas noches"

    partes = [f"{saludo}, señor Ríos."]
    partes.append(sistema.tiempo_sevilla() + ".")

    vencidas    = mem.tareas_vencidas(memoria)
    tareas_pend = mem.tareas_pendientes(memoria)
    if vencidas:
        partes.append(f"Tiene {len(vencidas)} tareas vencidas.")
    elif tareas_pend:
        partes.append(f"Tiene {len(tareas_pend)} tareas pendientes.")

    resumen_gmail = _resumen_emails_arranque()
    if resumen_gmail:
        partes.append(resumen_gmail)

    partes.append("¿Qué puedo hacer por usted hoy?")
    return " ".join(partes)

# ── D1-FIX: ejecución directa de la acción pendiente confirmada ──
def _d1_fix(texto):
    """Si pensar() detectó D1, ejecuta la acción pendiente pasando por el
    gate (_ejecutar) con el texto del usuario de este turno.
    Devuelve True si ejecutó (ya habló el resultado real), False si no."""
    traza = brain.ultima_traza()
    if traza and traza.get("d1_detectado"):
        info = brain.pendiente_info()
        if info and info["tool"] in ("borrar_archivo", "apagar_pc", "borrar_email", "borrar_multiples_emails"):
            res = brain._ejecutar(info["tool"], info["params"], SKILLS, texto)
            audio.hablar(res["message"])
            brain.consumir_pendiente()
            brain._print_seguro(f"[D1-FIX] ejecutado tool={info['tool']} ok={res['ok']}")
            return True
    return False

# ── MAIN LOOP ─────────────────────────────────────────────────────
def main():
    audio.hablar(resumen_diario())

    while True:
        try:
            texto = audio.escuchar(whisper_model)
            if not texto:
                continue

            mem.añadir_al_historial(memoria, texto)
            contexto  = mem.obtener_contexto(memoria)
            respuesta = brain.pensar(texto, contexto=contexto, skills=SKILLS)
            d1_ejecutado = _d1_fix(texto)

            # El agente activa el flag si quiere cerrar
            if brain.debe_cerrar():
                audio.hablar("Hasta luego Joaquín. Que tengas un buen día.")
                break

            # Si _d1_fix ejecutó, ya se habló el resultado real: la respuesta
            # del modelo de este turno se descarta (puede afirmar algo falso).
            if respuesta and respuesta != "cerrando" and not d1_ejecutado:
                audio.BARGE_IN_ACTIVO = not brain.hay_pendiente()
                try:
                    audio.hablar(respuesta)
                finally:
                    audio.BARGE_IN_ACTIVO = True
                mem.añadir_al_historial(memoria, f"Jarvis: {respuesta}")

        except KeyboardInterrupt:
            audio.hablar("Cerrando Jarvis.")
            break
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()