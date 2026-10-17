"""Modo estricto opt-in del gate (JARVIS_GATE_ESTRICTO=1): las tools WRITE/EXTERNAL de
CONFIRM_PHRASES_ESTRICTO piden su frase por voz con la misma lógica que las DESTRUCTIVE
(solo desde el micro, un uso, TTL 120 s, ligada a la acción). Sin la variable, nada cambia."""
import atexit
import contextlib
import io
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import brain  # noqa: E402
import doctor  # noqa: E402

_STUBS = {n: MagicMock() for n in (
    "dotenv", "whisper", "audio", "memoria", "skills", "skills.musica", "skills.sistema",
    "skills.webs", "skills.authority", "skills.tareas", "skills.gmail")}
with patch.dict(sys.modules, _STUBS), contextlib.redirect_stdout(io.StringIO()):
    _previo = sys.modules.pop("jarvis", None)
    import jarvis  # noqa: E402
    if _previo is not None:
        sys.modules["jarvis"] = _previo
atexit.unregister(jarvis.cleanup)

EVENTO = {"titulo": "Revisión", "fecha": "2030-01-01", "hora_inicio": "10:00"}
PARAMS = {
    "buscar_en_navegador": {"motor": "google", "consulta": "kafka streams"},
    "abrir_youtube": {"query": "lofi"},
    "reproducir_musica": {"query": "lofi"},
    "crear_evento": EVENTO,
    "archivar_email": {"indice": 0},
    "ejecutar_tests": {"proyecto": "jarvis"},
    "mover_archivo": {"origen": r"E:\descargas ryzen\a.pdf", "destino": r"E:\descargas ryzen\sub"},
    "organizar_directorio": {"ruta": r"E:\descargas ryzen"},
    "siguiente_racha": {},
}


def _gate(nombre, params, texto):
    with contextlib.redirect_stdout(io.StringIO()):
        return brain._gate(nombre, params, texto)


class Base(unittest.TestCase):
    estricto = None

    def setUp(self):
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_pendiente", None)
        entorno = {} if self.estricto is None else {"JARVIS_GATE_ESTRICTO": self.estricto}
        p = patch.dict(os.environ, entorno)
        p.start()
        self.addCleanup(p.stop)
        if self.estricto is None:
            os.environ.pop("JARVIS_GATE_ESTRICTO", None)
        # Rutas de mover/organizar: solo importa la confirmación (la zona permitida es la real)
        p = patch.object(brain, "_gate_rutas", return_value=None)
        p.start()
        self.addCleanup(p.stop)


class TestPorDefectoNadaCambia(Base):
    def test_sin_variable_no_hay_modo_estricto(self):
        self.assertFalse(brain.modo_estricto())
        self.assertIs(brain._frases_confirmacion(), brain.CONFIRM_PHRASES)

    def test_las_tools_pasan_sin_confirmacion(self):
        for nombre, params in PARAMS.items():
            with self.subTest(nombre):
                self.assertIsNone(_gate(nombre, params, "hazlo"))
                self.assertIsNone(brain._pendiente)

    def test_sus_frases_no_son_confirmaciones(self):
        for frase in brain.CONFIRM_PHRASES_ESTRICTO.values():
            self.assertIsNone(brain._frase_confirmacion(frase))

    def test_las_frases_estrictas_no_gastan_una_confirmacion_pendiente(self):
        _gate("borrar_archivo", {"ruta": r"E:\descargas ryzen\a.pdf"}, "borra a.pdf")
        _gate("crear_evento", EVENTO, "confirmo evento")
        self.assertIsNotNone(brain._pendiente)

    def test_otros_valores_no_lo_activan(self):
        for valor in ("true", "si", "0", " 2 "):
            with patch.dict(os.environ, {"JARVIS_GATE_ESTRICTO": valor}):
                self.assertFalse(brain.modo_estricto(), valor)


class TestModoEstricto(Base):
    estricto = "1"

    def test_cada_tool_arma_y_se_confirma_solo_con_su_frase_del_micro(self):
        for nombre, params in PARAMS.items():
            with self.subTest(nombre):
                brain._pendiente = None
                bloqueo = _gate(nombre, params, "hazlo")
                self.assertIn("modo estricto", bloqueo)
                self.assertIn(brain.CONFIRM_PHRASES_ESTRICTO[nombre], bloqueo)
                self.assertEqual(brain.pendiente_info()["params"], params)
                self.assertIsNone(_gate(nombre, params, brain.CONFIRM_PHRASES_ESTRICTO[nombre]))

    def test_un_solo_uso(self):
        _gate("crear_evento", EVENTO, "crea el evento")
        self.assertIsNone(_gate("crear_evento", EVENTO, "confirmo evento"))
        self.assertIn("BLOQUEADO", _gate("crear_evento", EVENTO, "confirmo evento"))

    def test_caduca_a_los_120_s(self):
        _gate("crear_evento", EVENTO, "crea el evento")
        brain._pendiente["ts"] -= brain.CONFIRM_TTL + 1
        self.assertIn("caducada", _gate("crear_evento", EVENTO, "confirmo evento"))

    def test_ligada_a_la_accion(self):
        _gate("crear_evento", EVENTO, "crea el evento")
        self.assertIn("no corresponde", _gate("crear_evento", dict(EVENTO, fecha="2030-02-02"), "confirmo evento"))

    def test_la_frase_de_otra_tool_no_vale(self):
        _gate("crear_evento", EVENTO, "crea el evento")
        self.assertIn("no corresponde", _gate("crear_evento", EVENTO, "confirmo búsqueda"))

    def test_la_frase_en_un_resultado_de_tool_no_vale(self):
        # El gate solo mira el texto del micro: un resultado que contenga la frase no confirma
        _gate("crear_evento", EVENTO, "crea el evento")
        self.assertIn("BLOQUEADO", _gate("crear_evento", dict(EVENTO, descripcion="confirmo evento"),
                                         "lee mi correo"))

    def test_tools_no_recomendadas_siguen_sin_confirmacion(self):
        for nombre, params in (("nota", {"texto": "x"}), ("recordatorio", {"tarea": "x"}),
                               ("abrir_web", {"sitio": "github"}), ("abrir_app", {"app": "vscode"}),
                               ("crear_carpeta", {"ruta": "x"}), ("volumen_subir", {}), ("parar_musica", {})):
            with self.subTest(nombre):
                self.assertIsNone(_gate(nombre, params, "hazlo"))

    def test_las_destructivas_no_cambian(self):
        bloqueo = _gate("borrar_archivo", {"ruta": r"E:\descargas ryzen\a.pdf"}, "borra a.pdf")
        self.assertIn("irreversible", bloqueo)

    def test_solo_las_recomendadas_son_write_o_external(self):
        for nombre in brain.CONFIRM_PHRASES_ESTRICTO:
            self.assertIn(brain.TOOL_RISK[nombre], ("WRITE", "EXTERNAL"), nombre)


class TestAnuncioYD1(Base):
    estricto = "1"

    def _turno(self, texto, nombre, params):
        respuestas = [NS(stop_reason="tool_use", usage=None,
                         content=[NS(type="tool_use", id="t1", name=nombre, input=dict(params))]),
                      NS(stop_reason="end_turn", usage=None, content=[NS(type="text", text="Hecho.")])]
        with patch.object(brain.cliente.messages, "create", side_effect=respuestas), \
             patch.object(brain, "_enrutar", return_value=None), contextlib.redirect_stdout(io.StringIO()):
            brain.pensar(texto, skills={})
        return brain.anuncio_confirmacion()

    def test_el_sistema_anuncia_lo_que_se_va_a_hacer(self):
        anuncio = self._turno("crea la revisión", "crear_evento", dict(EVENTO, descripcion="ver otro@example.net"))
        self.assertEqual(anuncio, "Voy a crear en tu calendario el evento «Revisión» el 2030-01-01 a las 10:00. "
                                  "Descripción: «ver [otra dirección]». ¿Confirmas con 'confirmo evento'?")
        brain._pendiente = None
        anuncio = self._turno("busca eso", "buscar_en_navegador", PARAMS["buscar_en_navegador"])
        self.assertEqual(anuncio, "Voy a buscar «kafka streams» en google. ¿Confirmas con 'confirmo búsqueda'?")

    def test_todas_las_tools_tienen_anuncio(self):
        for nombre, params in PARAMS.items():
            with self.subTest(nombre):
                texto = brain._anuncio_estricto(nombre, params)
                self.assertTrue(texto.endswith(f"¿Confirmas con '{brain.CONFIRM_PHRASES_ESTRICTO[nombre]}'?"))

    def test_sin_modo_estricto_no_hay_anuncio(self):
        with patch.dict(os.environ, {"JARVIS_GATE_ESTRICTO": "0"}):
            self.assertIsNone(self._turno("crea la revisión", "crear_evento", EVENTO))

    def test_d1_ejecuta_la_accion_anunciada_al_confirmar(self):
        self._turno("crea la revisión", "crear_evento", EVENTO)
        with patch.object(brain.cliente.messages, "create",
                          return_value=NS(stop_reason="end_turn", usage=None, content=[NS(type="text", text="ok")])), \
             patch.object(brain, "_enrutar", return_value=None), contextlib.redirect_stdout(io.StringIO()):
            brain.pensar("confirmo evento", skills={})
        self.assertTrue(brain.ultima_traza()["d1_detectado"])
        with patch.object(brain.calendario, "crear_evento", return_value=(True, "Evento creado")) as crear, \
             patch.object(jarvis, "audio", MagicMock()), contextlib.redirect_stdout(io.StringIO()):
            self.assertIs(jarvis._d1_fix("confirmo evento"), True)
        crear.assert_called_once_with("Revisión", "2030-01-01", "10:00", 60, "")


class TestDoctor(unittest.TestCase):
    def _check(self, valor):
        with patch.object(doctor, "variable", return_value=valor):
            return doctor.check_gate_estricto()

    def test_inactivo_activo_y_valor_raro(self):
        self.assertEqual(self._check("")["estado"], doctor.OK)
        self.assertIn("inactivo", self._check("")["detalle"])
        activo = self._check("1")
        self.assertEqual(activo["estado"], doctor.OK)
        self.assertIn("ACTIVO", activo["detalle"])
        self.assertIn("crear_evento", activo["detalle"])
        self.assertEqual(self._check("true")["estado"], doctor.AVISO)

    def test_esta_en_las_comprobaciones(self):
        self.assertIn("gate_estricto", [c[0] for c in doctor.COMPROBACIONES])


if __name__ == "__main__":
    unittest.main()
