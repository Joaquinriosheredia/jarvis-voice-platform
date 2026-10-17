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
import memoria_sesiones
from skills import musica, sistema, webs, authority, tareas, gmail, google_auth

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
    _cerrar_memoria_sesiones()
    print("👋 Jarvis cerrado correctamente")

def _cerrar_memoria_sesiones():
    """Al cerrar (adiós, apágate, Ctrl+C o fin normal): resume la sesión y la guarda.
    Nunca impide cerrar."""
    try:
        if memoria_sesiones.cerrar_sesion(resumir=brain.resumir_sesion):
            print("[MEMORIA] sesión guardada")
    except Exception as e:
        print(f"[MEMORIA] no se pudo guardar la sesión: {type(e).__name__}")

def _purgar_memoria_caducada():
    try:
        n = memoria_sesiones.purgar_caducados()
        if n:
            print(f"[MEMORIA] {n} sesión(es) caducadas borradas")
    except Exception as e:
        print(f"[MEMORIA] no se pudo aplicar la retención: {type(e).__name__}")

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

# ── AUTORIZACIÓN DE GOOGLE AL ARRANCAR ───────────────────────────
def _comprobar_autorizacion():
    """Renueva de verdad los tokens de Gmail y Calendar (en memoria, sin leer correo
    ni eventos). Devuelve ({servicio: resultado}, aviso), con aviso = (voz, pantalla)
    si hay que volver a autorizar algo, o None. Si la comprobación misma falla,
    (None, None): se arranca como antes. Nunca interrumpe el arranque."""
    try:
        resultados = google_auth.comprobar_todos()
        for r in resultados.values():
            brain._print_seguro(f"[AUTH] {google_auth.mensaje(r)}")
        return resultados, google_auth.aviso_arranque(resultados)
    except Exception as e:
        print(f"[AUTH] no se pudo comprobar la autorización de Google: {type(e).__name__}")
        return None, None

def _gmail_disponible(autorizacion):
    """Sin comprobación (None) se intenta como siempre; si se comprobó, solo con token válido.
    Así un token caducado o ausente no abre el navegador ni falla en el resumen."""
    if autorizacion is None:
        return True
    return bool((autorizacion.get("gmail") or {}).get("ok"))

# ── RESUMEN DIARIO ────────────────────────────────────────────────
def resumen_diario(autorizacion=None, aviso=""):
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

    resumen_gmail = _resumen_emails_arranque() if _gmail_disponible(autorizacion) else ""
    if resumen_gmail:
        partes.append(resumen_gmail)
    if aviso:
        partes.append(aviso)

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

DESPEDIDA = "Hasta luego Joaquín. Que tengas un buen día."

def _despedirse():
    """Orden de parada.APAGADO: la voz ya está cortada; se despide y el bucle termina."""
    brain._print_seguro("[PARADA] orden de apagado: JARVIS se cierra")
    audio.hablar(DESPEDIDA)

def _callar_sin_voz():
    """Orden de parada.SILENCIO sin que JARVIS esté hablando: solo calla. Para la
    música que lanzó JARVIS (no cualquier VLC). Nunca llega al modelo ni al gate:
    no confirma ninguna acción pendiente."""
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
    _purgar_memoria_caducada()
    audio.hay_confirmacion_pendiente = brain.hay_pendiente  # Whisper estricto si hay que confirmar
    # Parada por voz (mientras genera o habla), botón o atajo de la UI
    audio.modelo_whisper = whisper_model
    audio.al_parar.append(brain.cancelar_generacion)
    ui_bridge.al_parar = audio.parar_voz
    brain.al_texto_parcial = lambda texto: ui_bridge.emit("assistant_delta", text=texto)
    # Una sola vez por arranque: por voz (dentro del resumen) y en la UI (persistente)
    autorizacion, aviso = _comprobar_autorizacion()
    if aviso:
        ui_bridge.avisar(aviso[1])
    if _gmail_disponible(autorizacion):  # getProfile: con el token caducado no se intenta
        brain.cargar_destinatarios_permitidos(SKILLS)
    tts_elevenlabs.marcar_privado()  # el resumen lleva remitentes y asuntos de Gmail → Piper
    audio.hablar(resumen_diario(autorizacion, aviso[0] if aviso else ""))
    if audio.apagado_pedido():   # "apágate" durante el resumen de arranque
        _despedirse()
        return

    while True:
        try:
            texto = audio.escuchar(whisper_model)
            if not texto:
                metrics.descartar_turno()
                continue

            # Silenciar o cerrar: frase corta del micro (parada.SILENCIO / parada.APAGADO)
            orden = parada.clasificar(texto)
            if orden:
                metrics.descartar_turno()
                if orden == "apagado":
                    _despedirse()
                    break
                _callar_sin_voz()
                continue

            respuesta, cerrar = None, False
            brain.reiniciar_cancelacion()
            try:
                with audio.escucha_de_parada():
                    respuesta, cerrar = _turno(texto)
            finally:
                metrics.cerrar_turno(texto, respuesta)
            if cerrar or audio.apagado_pedido():  # "apágate" mientras hablaba: voz ya cortada
                _despedirse()
                break

        except KeyboardInterrupt:
            audio.hablar("Cerrando Jarvis.")
            break
        except Exception as e:
            print(f"❌ Error: {brain.direccion.ocultar(str(e))}")

# ── HISTORIAL (memoria.json) ──────────────────────────────────────
# El historial va a disco y vuelve al modelo como contexto: nunca guarda direcciones
# (salen como <email>) ni el texto de un turno privado (email, agenda, pantalla o una
# dirección dictada). Lo que el modelo necesite de esos turnos lo resuelve el sistema:
# destinatario "yo" (getProfile), "dictada" (brain._dictada) y la acción pendiente.
TURNO_PRIVADO = "[Turno privado: el texto no se guarda]"

def _tema(tools):
    temas = {"ver_agenda": "agenda", "ver_pantalla": "pantalla"}
    vistos = []
    for t in tools:
        tema = temas.get(t, "correo")
        if tema not in vistos:
            vistos.append(tema)
    return " y ".join(vistos) or "datos privados"

def _respuesta_privada():
    """Resumen sin datos de un turno privado: qué tools usó y si quedó algo por confirmar."""
    traza  = brain.ultima_traza() or {}
    tools  = []
    for t in traza.get("tools_ejecutadas") or []:
        if t.get("nombre") in brain.TOOLS_PRIVADAS and t["nombre"] not in tools:
            tools.append(t["nombre"])
    texto = f"[Respuesta privada no guardada. Tema: {_tema(tools)}"
    if tools:
        texto += f". Tools: {', '.join(tools)}"
    info = brain.pendiente_info() if brain.hay_pendiente() else None
    if info:
        texto += f". Pendiente de confirmar por voz: {info['tool']}"
    return texto + "]"

# Rutas que no son conversación: órdenes de parada y de memoria ("olvida lo de X" no
# puede acabar guardando X en el resumen de la sesión)
RUTAS_FUERA_DE_MEMORIA = {"parada", "apagado", "memoria"}

def _registrar_en_memoria_sesiones(texto, dicho):
    traza = brain.ultima_traza() or {}
    if traza.get("router") in RUTAS_FUERA_DE_MEMORIA:
        return
    tools = [t.get("nombre") for t in traza.get("tools_ejecutadas") or [] if t.get("nombre")]
    memoria_sesiones.registrar_turno(texto, dicho, tools, tts_elevenlabs.turno_privado())

def _guardar_en_historial(texto, dicho):
    """Guarda el turno ya terminado (pregunta y, si se dijo, la respuesta)."""
    if tts_elevenlabs.turno_privado():
        mem.añadir_al_historial(memoria, TURNO_PRIVADO)
        if dicho:
            mem.añadir_al_historial(memoria, f"Jarvis: {_respuesta_privada()}")
        return
    mem.añadir_al_historial(memoria, brain.direccion.redactar(texto))
    if dicho:
        mem.añadir_al_historial(memoria, f"Jarvis: {brain.direccion.redactar(dicho)}")

def _turno(texto):
    """Piensa y responde a un texto del micro. Devuelve (respuesta, cerrar).
    Una parada durante la generación (brain.generacion_cancelada) descarta la
    respuesta: no se dice nada ni se ejecutan el D1-fix ni el anuncio. El turno
    se guarda en el historial al terminar, cuando ya se sabe si es privado."""
    dicho = None
    try:
        # La pregunta actual va aparte (brain.pensar la añade): el contexto son los turnos anteriores
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
            dicho = anuncio

        # Una orden de apagado que llegó a brain.pensar: jarvis.py se despide y cierra
        if brain.debe_cerrar():
            return respuesta, True

        # Si _d1_fix ejecutó, ya se habló el resultado real: la respuesta
        # del modelo de este turno se descarta (puede afirmar algo falso).
        if respuesta and respuesta != "cerrando" and not d1_ejecutado and not anuncio:
            audio.BARGE_IN_ACTIVO = not brain.hay_pendiente()
            try:
                _decir_respuesta(respuesta)
            finally:
                audio.BARGE_IN_ACTIVO = True
            dicho = respuesta
        return respuesta, False
    finally:
        _guardar_en_historial(texto, dicho)
        _registrar_en_memoria_sesiones(texto, dicho)

if __name__ == "__main__":
    main()
