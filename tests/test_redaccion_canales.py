"""Enmascarado de secretos en los canales de terceros: correo, buscar_en_web, calendario,
briefing y github_estado. Su salida pasa por skills/secretos.py antes de llegar al modelo,
igual que las tools de código. Los secretos se generan en cada ejecución (ninguno literal)."""
import contextlib
import io
import json
import os
import secrets as azar
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import brain  # noqa: E402


def _secretos():
    return {"sk": "sk-" + azar.token_hex(12), "github": "ghp_" + azar.token_hex(18),
            "password": "Pw" + azar.token_hex(8)}


def _texto(s):
    return (f"Hola, la clave es {s['sk']} y el token {s['github']}.\n"
            f"password = {s['password']}\nhttps://usuario:{s['password']}@intranet.example.com/x")


def _skills(texto):
    gmail = NS(leer_email=lambda indice=0: texto, leer_no_leidos=lambda: texto,
               buscar_emails=lambda q="": texto, leer_recruiters=lambda: texto)
    return {"gmail": gmail, "webs": NS(buscar_en_web=lambda q: texto)}


def _ej(nombre, params, skills):
    with contextlib.redirect_stdout(io.StringIO()):
        return brain._ejecutar(nombre, params, skills, "lee eso")


class TestCanalesEnmascarados(unittest.TestCase):
    def setUp(self):
        self.s = _secretos()
        self.texto = _texto(self.s)
        self.skills = _skills(self.texto)

    def _sin_secretos(self, res):
        for valor in self.s.values():
            self.assertNotIn(valor, res["message"])
        self.assertIn("[SECRETO-REDACTADO:", res["message"])
        self.assertIn("secreto(s) enmascarado(s) en esta salida", res["message"])

    def test_correo(self):
        for nombre, params in (("leer_email", {"indice": 0}), ("leer_emails", {}),
                               ("buscar_emails", {"query": "x"}), ("emails_recruiters", {})):
            with self.subTest(nombre):
                self._sin_secretos(_ej(nombre, params, self.skills))

    def test_buscar_en_web(self):
        self._sin_secretos(_ej("buscar_en_web", {"query": "x"}, self.skills))

    def test_calendario(self):
        with patch.object(brain.calendario, "ver_agenda", return_value=(True, self.texto)):
            self._sin_secretos(_ej("ver_agenda", {"dias": 1}, self.skills))

    def test_briefing_y_github(self):
        with patch.object(brain.briefing, "desde_skills", return_value=self.texto):
            self._sin_secretos(_ej("briefing", {}, self.skills))
        with patch.object(brain.github, "estado", return_value={}), \
             patch.object(brain.github, "detalle", return_value=self.texto):
            self._sin_secretos(_ej("github_estado", {}, self.skills))

    def test_un_error_del_canal_tambien_se_enmascara(self):
        with patch.object(brain.calendario, "ver_agenda", return_value=(False, self.texto)):
            res = _ej("ver_agenda", {"dias": 1}, self.skills)
        self.assertFalse(res["ok"])
        self._sin_secretos(res)

    def test_sin_secretos_el_texto_no_cambia(self):
        res = _ej("buscar_en_web", {"query": "x"}, _skills("Kafka 4.0 sale en marzo."))
        self.assertEqual(res["message"], "Kafka 4.0 sale en marzo.")

    def test_las_tools_de_codigo_no_se_enmascaran_dos_veces(self):
        # operador ya enmascara y pone su propia línea de recuento
        self.assertNotIn("leer_archivo", brain.REDACTAR_SALIDA)
        with patch.object(brain.operador, "leer_archivo", return_value=(True, "x [SECRETO-REDACTADO:sk]")):
            res = _ej("leer_archivo", {"proyecto": "jarvis", "ruta_relativa": "a.py"}, self.skills)
        self.assertEqual(res["message"], "x [SECRETO-REDACTADO:sk]")

    def test_el_modelo_recibe_el_correo_enmascarado(self):
        vistos = []

        def create(**kw):
            ultimo = kw["messages"][-1]["content"]
            if isinstance(ultimo, list):
                vistos.extend(json.loads(b["content"])["message"] for b in ultimo if b.get("type") == "tool_result")
                return NS(stop_reason="end_turn", usage=None, content=[NS(type="text", text="Leído.")])
            return NS(stop_reason="tool_use", usage=None,
                      content=[NS(type="tool_use", id="t1", name="leer_email", input={"indice": 0})])

        with patch.object(brain.cliente.messages, "create", side_effect=create), \
             patch.object(brain, "_enrutar", return_value=None), \
             contextlib.redirect_stdout(io.StringIO()):
            brain.pensar("lee mi último correo", skills=self.skills)
        self.assertEqual(len(vistos), 1)
        for valor in self.s.values():
            self.assertNotIn(valor, vistos[0])
        traza = brain.ultima_traza()
        self.assertNotIn(self.s["sk"], json.dumps(traza["tools_ejecutadas"], default=str))


if __name__ == "__main__":
    unittest.main()
