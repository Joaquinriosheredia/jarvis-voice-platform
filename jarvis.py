from dotenv import load_dotenv
load_dotenv(r"C:\Jarvis-secrets\.env")

import whisper
import atexit
from datetime import datetime

import audio
import tts_elevenlabs
import metrics
import frases
import parada
import brain
import memoria as mem
from skills import musica, sistema, webs, authority, tareas, gmail

# ── CARGAR MODELOS ────────────────────────────────────────────────
print("🤖 Cargando Jarvis...")
whisper_model = whisper.load_model("small")  # usa cuda si torch lo detecta
print(f"🎙️ Whisper en {whisper_model.device}")
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
        if info and info["tool"] in ("borrar_archivo", "apagar_pc", "borrar_email", "borrar_multiples_emails", "enviar_email"):
            with metrics.cronometro("tools"):
                res = brain._ejecutar(info["tool"], info["params"], SKILLS, texto)
            metrics.registrar_tool(info["tool"])
            audio.hablar(res["message"])
            brain.consumir_pendiente()
            brain._print_seguro(f"[D1-FIX] ejecutado tool={info['tool']} ok={res['ok']}")
            return True
    return False

# ── Voz: no leer en alto archivos ni código largos ──
AVISO_PANTALLA = "Te muestro el contenido en pantalla."

AVISO_DETALLE  = "El detalle está en pantalla."

def _turno_review():
    return (brain.ultima_traza() or {}).get("router") == "review"

def _resumen_review(respuesta):
    """Las 2 primeras frases (el resumen que SYSTEM_REVIEW pone al principio) + aviso."""
    todas = frases.trocear(respuesta, minimo=1)
    if len(todas) <= 2:
        return respuesta
    return " ".join(todas[:2]) + " " + AVISO_DETALLE

def _texto_voz(respuesta):
    """El texto completo va a la UI; por voz solo un aviso si es largo y multilínea.
    En una review (marca del turno, no heurística) solo se dice el resumen."""
    if _turno_review():
        return _resumen_review(respuesta)
    if len(respuesta) > 300 and respuesta.count("\n") >= 3:
        return AVISO_PANTALLA
    return respuesta

def _parece_codigo(texto):
    return "```" in texto or any(l.startswith(("    ", "\t")) for l in texto.splitlines())

def _decir_respuesta(respuesta):
    """Respuesta final del modelo: por frases (la 1.ª suena mientras se sintetiza
    la siguiente) si es texto normal; código o textos largos, como siempre."""
    voz = _texto_voz(respuesta)
    if brain.streaming_activo() and voz == respuesta and not _parece_codigo(respuesta):
        audio.hablar_frases(frases.trocear(respuesta), texto_pantalla=respuesta)
    else:
        audio.hablar(voz, texto_pantalla=respuesta)

def _callar_sin_voz():
    """Orden de parada sin que JARVIS esté hablando: solo calla. Para la música que
    lanzó JARVIS (no cualquier VLC). Nunca llega al modelo ni al gate: no confirma
    ninguna acción pendiente ni cierra JARVIS."""
    musica = SKILLS.get("musica")
    try:
        if getattr(musica, "musica_proceso", None):
            musica.parar()
    except Exception:
        pass
    brain._print_seguro("[PARADA] orden de parada sin voz activa: JARVIS calla")

# ── MAIN LOOP ─────────────────────────────────────────────────────
def main():
    import ui_bridge
    ui_bridge.start()
    audio.hay_confirmacion_pendiente = brain.hay_pendiente  # Whisper estricto si hay que confirmar
    # Parada por voz (mientras genera o habla), botón o atajo de la UI
    audio.modelo_whisper = whisper_model
    audio.al_parar.append(brain.cancelar_generacion)
    ui_bridge.al_parar = audio.parar_voz
    brain.al_texto_parcial = lambda texto: ui_bridge.emit("assistant_delta", text=texto)
    brain.cargar_destinatarios_permitidos(SKILLS)
    tts_elevenlabs.marcar_privado()  # el resumen lleva remitentes y asuntos de Gmail → Piper
    audio.hablar(resumen_diario())

    while True:
        try:
            texto = audio.escuchar(whisper_model)
            if not texto:
                metrics.descartar_turno()
                continue

            if parada.es_orden_parada(texto):
                _callar_sin_voz()
                metrics.descartar_turno()
                continue

            respuesta, cerrar = None, False
            brain.reiniciar_cancelacion()
            try:
                with audio.escucha_de_parada():
                    respuesta, cerrar = _turno(texto)
            finally:
                metrics.cerrar_turno(texto, respuesta)
            if cerrar:
                break

        except KeyboardInterrupt:
            audio.hablar("Cerrando Jarvis.")
            break
        except Exception as e:
            print(f"❌ Error: {brain.direccion.ocultar(str(e))}")

def _turno(texto):
    """Piensa y responde a un texto del micro. Devuelve (respuesta, cerrar).
    Una parada durante la generación (brain.generacion_cancelada) descarta la
    respuesta: no se dice nada ni se ejecutan el D1-fix ni el anuncio."""
    mem.añadir_al_historial(memoria, texto)
    contexto  = mem.obtener_mensajes(memoria)
    respuesta = brain.pensar(texto, contexto=contexto, skills=SKILLS,
                             conversacion_reciente=mem.conversacion_reciente(memoria))
    if brain.generacion_cancelada():
        brain._print_seguro("[PARADA] respuesta descartada")
        return None, False
    d1_ejecutado = _d1_fix(texto)

    # Envío pendiente: JARVIS lee el destinatario real (de _pendiente),
    # no la paráfrasis del modelo, que podría estar manipulada
    anuncio = brain.anuncio_confirmacion()
    if anuncio:
        audio.BARGE_IN_ACTIVO = False
        try:
            audio.hablar(anuncio)
        finally:
            audio.BARGE_IN_ACTIVO = True
        mem.añadir_al_historial(memoria, f"Jarvis: {brain.direccion.ocultar(anuncio)}")

    # El agente activa el flag si quiere cerrar
    if brain.debe_cerrar():
        audio.hablar("Hasta luego Joaquín. Que tengas un buen día.")
        return respuesta, True

    # Si _d1_fix ejecutó, ya se habló el resultado real: la respuesta
    # del modelo de este turno se descarta (puede afirmar algo falso).
    if respuesta and respuesta != "cerrando" and not d1_ejecutado and not anuncio:
        audio.BARGE_IN_ACTIVO = not brain.hay_pendiente()
        try:
            _decir_respuesta(respuesta)
        finally:
            audio.BARGE_IN_ACTIVO = True
        mem.añadir_al_historial(memoria, f"Jarvis: {respuesta}")
    return respuesta, False

if __name__ == "__main__":
    main()
