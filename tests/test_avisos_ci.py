"""Avisos proactivos v1 (avisos_ci.py): CI que pasa a rojo, una vez por evento, por voz y UI,
solo entre turnos, respetando el silencio y sin ejecutar nada. Con mocks: sin red ni GitHub."""
import atexit
import contextlib
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import avisos_ci  # noqa: E402
import doctor  # noqa: E402
from skills import github  # noqa: E402

_STUBS = {n: MagicMock() for n in (
    "dotenv", "whisper", "audio", "memoria", "skills", "skills.musica", "skills.sistema",
    "skills.webs", "skills.authority", "skills.tareas", "skills.gmail")}
with patch.dict(sys.modules, _STUBS), contextlib.redirect_stdout(io.StringIO()):
    _previo = sys.modules.pop("jarvis", None)
    import jarvis  # noqa: E402
    if _previo is not None:
        sys.modules["jarvis"] = _previo
atexit.unregister(jarvis.cleanup)

REPO = "Joaquinriosheredia/jarvis-voice-platform"


def ci(id_, estado, rama="main"):
    return {"id": id_, "nombre": "CI", "rama": rama, "estado": estado}


class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.respuestas = {}
        self.registros = []
        self.reloj = Reloj()
        self.v = avisos_ci.Vigilante([REPO], consultar=self._consultar, reloj=self.reloj,
                                     registrar=self.registros.append)
        self.voz, self.ui = [], []

    def _consultar(self, repo):
        r = self.respuestas[repo]
        if isinstance(r, Exception):
            raise r
        return r

    def pasada(self, valor, repo=REPO):
        self.respuestas[repo] = valor
        self.v.comprobar()

    def entregar(self):
        return self.v.entregar(self.voz.append, self.ui.append)


class TestEventos(Base):
    def test_lo_rojo_al_arrancar_no_se_avisa(self):
        self.pasada(ci(1, "rojo"))
        self.assertEqual(self.entregar(), 0)

    def test_pasa_a_rojo_se_avisa_una_vez_por_voz_y_ui(self):
        self.pasada(ci(1, "verde"))
        self.pasada(ci(2, "en curso"))
        self.pasada(ci(2, "rojo"))
        self.pasada(ci(2, "rojo"))          # misma ejecución: no se repite
        self.assertEqual(self.entregar(), 1)
        texto = "Aviso: el CI de jarvis-voice-platform ha pasado a rojo (CI en la rama main)."
        self.assertEqual((self.voz, self.ui), ([texto], [texto]))
        self.assertEqual(self.entregar(), 0)

    def test_otra_ejecucion_en_rojo_es_otro_evento(self):
        self.pasada(ci(1, "verde"))
        self.pasada(ci(2, "rojo"))
        self.pasada(ci(3, "rojo"))
        self.assertEqual(self.entregar(), 2)

    def test_verde_cancelado_y_en_curso_no_avisan(self):
        self.pasada(ci(1, "verde"))
        for i, estado in enumerate(("verde", "en curso", "cancelado"), start=2):
            self.pasada(ci(i, estado))
        self.assertEqual(self.entregar(), 0)

    def test_sin_ejecuciones(self):
        self.pasada(None)
        self.pasada(None)
        self.assertEqual(self.entregar(), 0)


class TestSilencioYTurnos(Base):
    def test_tras_una_orden_de_silencio_solo_ui(self):
        self.pasada(ci(1, "verde"))
        self.v.silenciar()
        self.pasada(ci(2, "rojo"))
        self.assertEqual(self.entregar(), 1)
        self.assertEqual(self.voz, [])
        self.assertEqual(len(self.ui), 1)

    def test_el_silencio_caduca(self):
        self.pasada(ci(1, "verde"))
        self.v.silenciar()
        self.reloj.t += avisos_ci.SILENCIO_MIN * 60 + 1
        self.pasada(ci(2, "rojo"))
        self.entregar()
        self.assertEqual(len(self.voz), 1)

    def test_jarvis_entrega_entre_turnos_y_la_voz_se_puede_parar(self):
        with patch.object(avisos_ci, "_vigilante", self.v), \
             patch.object(jarvis, "audio") as audio, patch.object(jarvis, "ui_bridge_emit") as emit:
            jarvis._entregar_avisos()                     # nada pendiente: ni micro ni voz
            audio.escucha_de_parada.assert_not_called()
            self.pasada(ci(1, "verde"))
            self.pasada(ci(2, "rojo"))
            jarvis._entregar_avisos()
        audio.escucha_de_parada.assert_called_once()
        audio.hablar.assert_called_once()
        emit.assert_called_once_with("warning", message=audio.hablar.call_args.args[0])

    def test_la_orden_de_silencio_del_bucle_silencia_los_avisos(self):
        with patch.object(avisos_ci, "_vigilante", self.v):
            avisos_ci.silenciar()
        self.assertTrue(self.v.silenciado())


class TestFallos(Base):
    def test_sin_token_no_avisa_lo_registra_una_vez_y_para(self):
        for _ in range(3):
            self.pasada(github.ErrorGitHub(github.SIN_TOKEN))
        self.assertEqual(len(self.registros), 1)
        self.assertIn("sin_token", self.registros[0])
        self.assertTrue(self.v.parar)
        self.assertEqual(self.entregar(), 0)

    def test_token_invalido_o_sin_red_se_registra_una_vez_y_sigue(self):
        self.pasada(github.ErrorGitHub(github.TOKEN_INVALIDO))
        self.pasada(github.ErrorGitHub(github.TOKEN_INVALIDO))
        self.assertEqual(len(self.registros), 1)
        self.assertFalse(self.v.parar)
        self.pasada(ci(1, "verde"))
        self.pasada(ci(2, "rojo"))
        self.assertEqual(self.entregar(), 1)

    def test_repo_sin_acceso_no_corta_los_demas(self):
        otro = "Joaquinriosheredia/otro"
        self.v.lista = [REPO, otro]
        self.respuestas[otro] = github.ErrorGitHub(github.NO_EXISTE)
        self.pasada(ci(1, "verde"))
        self.pasada(ci(2, "rojo"))
        self.assertEqual(self.entregar(), 1)
        self.assertEqual(len(self.registros), 1)

    def test_un_error_inesperado_no_lanza_ni_filtra_el_texto(self):
        self.pasada(RuntimeError("Bearer abc123"))
        self.assertEqual(self.registros, ["[AVISOS] avisos de CI: fallo inesperado (RuntimeError)"])


class TestConfiguracion(unittest.TestCase):
    def test_desactivado_por_defecto(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JARVIS_AVISOS_CI", None)
            self.assertFalse(avisos_ci.activado())
            self.assertIsNone(avisos_ci.iniciar(registrar=lambda t: None))

    def test_intervalo_acotado(self):
        for valor, esperado in (("", 5), ("1", 2), ("10", 10), ("999", 60), ("x", 5)):
            self.assertEqual(avisos_ci.minutos(valor), esperado, valor)

    def test_repos_propios_o_los_del_briefing(self):
        with patch.dict(os.environ, {"JARVIS_AVISOS_CI_REPOS": "uno, dueño/dos, mal nombre",
                                     "JARVIS_GITHUB_REPOS": "tres"}):
            self.assertEqual(avisos_ci.repos(), ["Joaquinriosheredia/uno"])
        with patch.dict(os.environ, {"JARVIS_AVISOS_CI_REPOS": "", "JARVIS_GITHUB_REPOS": "tres"}):
            self.assertEqual(avisos_ci.repos(), ["Joaquinriosheredia/tres"])

    def test_solo_lectura_y_sin_acciones(self):
        codigo = open(avisos_ci.__file__, encoding="utf-8").read()
        for prohibido in ("brain", "_ejecutar", "subprocess", "requests.post", "requests.put"):
            self.assertNotIn(prohibido, codigo)

    def test_doctor(self):
        def var(valores):
            return lambda n: valores.get(n, "")
        with patch.object(doctor, "variable", var({})):
            self.assertIn("desactivados", doctor.check_avisos_ci()["detalle"])
        with patch.object(doctor, "variable", var({"JARVIS_AVISOS_CI": "1", "JARVIS_AVISOS_CI_MIN": "10",
                                                    "GITHUB_TOKEN": "x" * 20})):
            r = doctor.check_avisos_ci()
        self.assertEqual(r["estado"], doctor.OK)
        self.assertIn("cada 10 min", r["detalle"])
        with patch.object(doctor, "variable", var({"JARVIS_AVISOS_CI": "1"})):
            self.assertEqual(doctor.check_avisos_ci()["estado"], doctor.AVISO)
        self.assertIn("avisos_ci", [c[0] for c in doctor.COMPROBACIONES])


if __name__ == "__main__":
    unittest.main()
