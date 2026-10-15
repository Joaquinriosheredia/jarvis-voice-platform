"""Evals de JARVIS: ruta del router, tool elegida y decisión del gate por caso.

Ejecutar:  python -m unittest tests.test_evals -v
Modo real: JARVIS_EVALS_LIVE=1 python -m unittest tests.test_evals -v
           (llama de verdad a Claude; las tools siguen simuladas y el gate es el real)

Qué mide cada métrica:
- Ruta: lo que decide el router real (_enrutar → traza["router"]). Siempre es real.
- Gate: la decisión real de brain._gate ante la tool elegida:
    confirm = arma una confirmación pendiente; block = la bloquea sin armar nada;
    none = la deja pasar o no hubo tool.
- Tool: la primera tool que llega al dispatcher. Sin JARVIS_EVALS_LIVE, en la ruta
  claude el mock devuelve la tool esperada, así que solo valida la tubería
  (atajos → tool, dev/local → sin tool). Con JARVIS_EVALS_LIVE=1 mide al modelo.

Ninguna tool se ejecuta: _ejecutar se sustituye por el gate real + un resultado simulado.
"""
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

LIVE = os.environ.get("JARVIS_EVALS_LIVE") == "1"
if not LIVE:
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402

UMBRAL = 0.80

CASOS = [
    # (texto_usuario, ruta_esperada, tool_esperada, gate_esperado)
    # ATAJOS (sin LLM)
    ("para",                  "atajo",  "parar_musica",    "none"),
    ("silencio",              "atajo",  "parar_musica",    "none"),
    # corregido: "cierra jarvis" es atajo en ATAJOS y cerrar_jarvis es WRITE (sin confirmación)
    ("cierra jarvis",         "atajo",  "cerrar_jarvis",   "none"),

    # LOCAL → Ollama
    ("qué es una variable",   "local",  None,              "none"),
    ("cuánto es 7 por 8",     "local",  None,              "none"),
    ("dime un chiste",        "claude", None,              "none"),  # chiste con la persona de JARVIS
    ("qué hora es en tokio",  "local",  None,              "none"),

    # DEV → Claude con SYSTEM_DEV
    ("por qué falla kafka",         "dev", None, "none"),
    ("cómo funciona el gc de java", "dev", None, "none"),
    ("explícame spring boot",       "dev", None, "none"),
    ("qué es un deadlock",          "dev", None, "none"),

    # TOOLS sin gate
    # corregido: "qué hora es", "qué tiempo hace" y "para la música" son atajos en ATAJOS
    ("qué hora es",          "atajo",  "hora",             "none"),
    ("qué tiempo hace",      "atajo",  "tiempo",           "none"),
    ("pon música de fondo",  "claude", "reproducir_musica", "none"),
    ("para la música",       "atajo",  "parar_musica",     "none"),

    # CLAUDE + TOOLS con gate
    ("apaga el ordenador",   "claude", "apagar_pc",        "confirm"),
    ("borra el email de prueba", "claude", "borrar_email", "confirm"),
    # corregido: solo se puede enviar a la propia dirección; otra se bloquea sin confirmar
    ("envía un correo a test@test.com", "claude", "enviar_email", "block"),

    # CLAUDE + herramientas READ / EXTERNAL (sin gate)
    ("léeme el archivo brain.py del proyecto jarvis líneas 1 a 20",
                             "claude", "leer_archivo",     "none"),
    ("estado de git en jarvis",
                             "claude", "git_estado",       "none"),
    ("muéstrame los últimos commits de jarvis",
                             "claude", "git_log",          "none"),
    ("ejecuta los tests de jarvis",
                             "claude", "ejecutar_tests",   "none"),

    # CLAUDE conversacional
    ("quién eres",           "claude", None,               "none"),
    ("qué puedes hacer",     "claude", None,               "none"),

    # EDGE CASES
    ("busca en google colchones baratos", "claude", "buscar_en_web", "none"),
    ("pon una alarma en 10 minutos",      "claude", "recordatorio",  "none"),
    ("lista mis emails",                  "claude", "leer_emails",   "none"),

    # CALENDARIO
    ("qué tengo en la agenda esta semana", "claude", "ver_agenda",   "none"),
    ("crea una reunión mañana a las 10",   "claude", "crear_evento", "none"),
    ("qué tengo hoy",                      "claude", "ver_agenda",   "none"),
    ("qué tengo mañana",                   "claude", "ver_agenda",   "none"),
    ("crea un evento mañana a las 10 reunión con Beatriz", "claude", "crear_evento", "none"),
    ("qué tareas tengo",                   "claude", "listar_tareas", "none"),

    # MÉTRICAS
    ("cuánto he gastado hoy",              "claude", "gasto_hoy",    "none"),
    ("cuánto llevo gastado",               "claude", "gasto_hoy",    "none"),
]

# Parámetros que el mock pone en el tool_use (los que pondría el modelo)
INPUTS = {
    "apagar_pc":         {"accion": "apagar"},
    "borrar_email":      {"msg_id": "eval-1"},
    "enviar_email":      {"destinatario": "test@test.com", "asunto": "Prueba", "cuerpo": "Hola"},
    "leer_archivo":      {"proyecto": "jarvis", "ruta_relativa": "brain.py", "desde": 1, "hasta": 20},
    "git_estado":        {"proyecto": "jarvis"},
    "git_log":           {"proyecto": "jarvis"},
    "ejecutar_tests":    {"proyecto": "jarvis"},
    "reproducir_musica": {"query": "música de fondo"},
    "buscar_en_web":     {"query": "colchones baratos"},
    "recordatorio":      {"tarea": "alarma", "hora": "10 minutos"},
    "ver_agenda":        {"dias": 7},
    "crear_evento":      {"titulo": "Reunión", "fecha": "2026-10-16", "hora_inicio": "10:00"},
}


def _respuesta(stop, content):
    return NS(stop_reason=stop, content=content, usage=None)


def _mock_claude(tool):
    """1.ª llamada: tool_use de la tool (o texto si None); después, end_turn."""
    llamadas = []

    def create(**_kwargs):
        llamadas.append(1)
        if tool and len(llamadas) == 1:
            bloque = NS(type="tool_use", name=tool, id="eval-t1", input=dict(INPUTS.get(tool, {})))
            return _respuesta("tool_use", [bloque])
        return _respuesta("end_turn", [NS(type="text", text="Hecho.")])
    return create


def _email_simulado(nombre, parametros):
    """borrar_email exige que el msg_id venga de un listado: se simula que sí."""
    if nombre == "borrar_email" and parametros.get("msg_id"):
        return {"remitente": "eval@ejemplo.com", "asunto": "Prueba"}
    return None


def _evaluar(texto, tool_mock):
    """(ruta, tool, gate) reales de un caso, sin efectos externos."""
    brain._pendiente = None
    brain._cerrar = False
    reg = {"tool": None, "gate": None}

    def ejecutar_simulado(nombre, parametros, skills, texto_usuario=""):
        bloqueo = brain._gate(nombre, parametros, texto_usuario, _email_simulado(nombre, parametros))
        if reg["tool"] is None:
            reg["tool"] = nombre
            reg["gate"] = "none" if not bloqueo else ("confirm" if brain.hay_pendiente() else "block")
        if bloqueo:
            return brain._envolver(bloqueo)
        return brain._ok(f"[eval] {nombre} simulado")

    parches = [
        patch("brain._ejecutar", side_effect=ejecutar_simulado),
        patch("brain._llamar_ollama", return_value="Respuesta local."),
        patch("brain._log_traza"),
        patch("brain._print_seguro"),
        patch("builtins.print"),
        patch.object(brain, "DESTINATARIOS_PERMITIDOS", []),
    ]
    if not LIVE:
        parches.append(patch.object(brain.cliente.messages, "create", side_effect=_mock_claude(tool_mock)))
    for p in parches:
        p.start()
    try:
        brain.pensar(texto, skills={"eval": True})
    finally:
        for p in reversed(parches):
            p.stop()
        brain._pendiente = None
        brain._cerrar = False
    return (brain.ultima_traza() or {}).get("router"), reg["tool"], reg["gate"] or "none"


def _escribir(texto):
    """print que no rompe con ✔ si la consola no es UTF-8."""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(texto.encode(enc, "replace").decode(enc))


class TestEvals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resultados = []
        for texto, ruta, tool, gate in CASOS:
            obtenido = _evaluar(texto, tool)
            cls.resultados.append((texto, (ruta, tool, gate), obtenido))
        cls.aciertos = {}
        fallos = []
        for n, metrica in enumerate(("RUTA", "TOOL", "GATE")):
            ok = sum(1 for _, esp, obt in cls.resultados if esp[n] == obt[n])
            cls.aciertos[metrica] = ok / len(cls.resultados)
            fallos += [f'  [{metrica}]  "{texto}" → esperado {esp[n]}, obtenido {obt[n]}'
                       for texto, esp, obt in cls.resultados if esp[n] != obt[n]]
        total = len(cls.resultados)
        lineas = ["", "=" * 30, f"EVALS JARVIS — {total} casos" + (" (LIVE)" if LIVE else "")]
        for metrica, etiqueta in (("RUTA", "Ruta: "), ("TOOL", "Tool: "), ("GATE", "Gate: ")):
            ok = round(cls.aciertos[metrica] * total)
            lineas.append(f"{etiqueta} {ok}/{total} ✔  ({cls.aciertos[metrica] * 100:.1f}%)")
        lineas.append("=" * 30)
        lineas += ["FALLOS:"] + fallos if fallos else ["Sin fallos."]
        if not LIVE:
            lineas.append("(Tool en ruta claude viene del mock; JARVIS_EVALS_LIVE=1 para medir al modelo)")
        cls.informe = "\n".join(lineas)
        _escribir(cls.informe)

    def test_ruta_supera_80(self):
        self.assertGreaterEqual(self.aciertos["RUTA"], UMBRAL, self.informe)

    def test_tool_supera_80(self):
        self.assertGreaterEqual(self.aciertos["TOOL"], UMBRAL, self.informe)

    def test_gate_supera_80(self):
        self.assertGreaterEqual(self.aciertos["GATE"], UMBRAL, self.informe)


if __name__ == "__main__":
    unittest.main()
