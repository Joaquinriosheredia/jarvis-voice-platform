"""Google Calendar: skills/calendario.py y su integración en brain. Sin red: _servicio mockeado."""
import datetime
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislar_memoria  # noqa: E402,F401  (nunca la base real de memoria)

import brain  # noqa: E402
from skills import calendario  # noqa: E402


def servicio(items=None):
    svc = MagicMock()
    svc.events.return_value.list.return_value.execute.return_value = {"items": items or []}
    svc.events.return_value.insert.return_value.execute.return_value = {"id": "ev1"}
    return svc


class TestVerAgenda(unittest.TestCase):
    def test_sin_eventos(self):
        with patch("skills.calendario._servicio", return_value=servicio()):
            self.assertEqual(calendario.ver_agenda(1), (True, "No tienes eventos en el próximo día."))

    def test_dos_eventos_con_formato(self):
        items = [
            {"summary": "Reunión", "start": {"dateTime": "2026-10-19T09:00:00+02:00"}},
            {"summary": "Dentista", "start": {"date": "2026-10-20"}},
        ]
        with patch("skills.calendario._servicio", return_value=servicio(items)):
            ok, msg = calendario.ver_agenda(1)
        self.assertTrue(ok)
        self.assertEqual(msg.splitlines()[1:], ["- Lun 19/10 09:00: Reunión",
                                                "- Mar 20/10 (todo el día): Dentista"])

    def test_dias_se_trunca_a_7(self):
        svc = servicio()
        with patch("skills.calendario._servicio", return_value=svc):
            ok, msg = calendario.ver_agenda(10)
        kw = svc.events.return_value.list.call_args.kwargs
        inicio = datetime.datetime.fromisoformat(kw["timeMin"])
        fin    = datetime.datetime.fromisoformat(kw["timeMax"])
        self.assertEqual(fin - inicio, datetime.timedelta(days=7))
        self.assertEqual(kw["calendarId"], "primary")
        self.assertTrue(kw["singleEvents"])
        self.assertIn("los próximos 7 días", msg)

    def test_dias_invalido_y_error_de_api(self):
        with patch("skills.calendario._servicio") as srv:
            self.assertFalse(calendario.ver_agenda("3")[0])
            srv.assert_not_called()
        with patch("skills.calendario._servicio", side_effect=RuntimeError("token caducado")):
            ok, msg = calendario.ver_agenda(1)
        self.assertFalse(ok)
        self.assertIn("token caducado", msg)


class TestCrearEvento(unittest.TestCase):
    def test_datos_validos_llama_a_insert(self):
        svc = servicio()
        with patch("skills.calendario._servicio", return_value=svc):
            ok, msg = calendario.crear_evento("Reunión con Ana", "2026-10-19", "09:30", 45, "Revisar PR")
        self.assertTrue(ok, msg)
        self.assertEqual(msg, "Evento creado: 'Reunión con Ana' el Lun 19/10 a las 09:30 (45 min).")
        kw = svc.events.return_value.insert.call_args.kwargs
        self.assertEqual(kw["calendarId"], "primary")
        self.assertEqual(kw["body"], {
            "summary":     "Reunión con Ana",
            "description": "Revisar PR",
            "start":       {"dateTime": "2026-10-19T09:30:00", "timeZone": "Europe/Madrid"},
            "end":         {"dateTime": "2026-10-19T10:15:00", "timeZone": "Europe/Madrid"},
        })

    def test_fecha_invalida_no_llama_a_la_api(self):
        with patch("skills.calendario._servicio") as srv:
            for fecha in ("19/10/2026", "2026-10-19x", "mañana", "", None, "2026-02-30"):
                ok, msg = calendario.crear_evento("X", fecha, "09:00")
                self.assertFalse(ok, repr(fecha))
        srv.assert_not_called()

    def test_hora_invalida_no_llama_a_la_api(self):
        with patch("skills.calendario._servicio") as srv:
            for hora in ("9:00", "09:00:00", "las 9", "", None, "25:00"):
                ok, msg = calendario.crear_evento("X", "2026-10-19", hora)
                self.assertFalse(ok, repr(hora))
        srv.assert_not_called()

    def test_duracion_999_se_trunca_a_480(self):
        svc = servicio()
        with patch("skills.calendario._servicio", return_value=svc):
            ok, msg = calendario.crear_evento("Largo", "2026-10-19", "08:00", 999)
        body = svc.events.return_value.insert.call_args.kwargs["body"]
        self.assertEqual(body["end"]["dateTime"], "2026-10-19T16:00:00")
        self.assertIn("(480 min)", msg)

    def test_titulo_vacio_no_llama_a_la_api(self):
        with patch("skills.calendario._servicio") as srv:
            for titulo in ("", "   ", None):
                self.assertFalse(calendario.crear_evento(titulo, "2026-10-19", "09:00")[0])
        srv.assert_not_called()


class TestIntegracionBrain(unittest.TestCase):
    def test_riesgos(self):
        self.assertEqual(brain.TOOL_RISK["ver_agenda"], "READ")
        self.assertEqual(brain.TOOL_RISK["crear_evento"], "EXTERNAL")

    def test_dispatcher_contrato_ok_err(self):
        with patch("skills.calendario._servicio", return_value=servicio()), patch("builtins.print"), \
             patch.dict(os.environ, {"JARVIS_GATE_ESTRICTO": "0"}):   # aquí se prueba la tool, no la confirmación del gate estricto
            res = brain._ejecutar("ver_agenda", {"dias": 2}, {})
            self.assertTrue(res["ok"], res)
            res = brain._ejecutar("crear_evento", {"titulo": "X", "fecha": "ayer", "hora_inicio": "09:00"}, {})
        self.assertEqual((res["ok"], res["error"]), (False, "calendario_fallo"))

    def test_router_manda_agenda_a_claude(self):
        for texto in ("qué tengo en la agenda esta semana", "tengo alguna reunión mañana",
                      "qué eventos tengo", "qué tengo hoy", "qué tengo mañana",
                      "qué tengo esta semana", "qué tengo pendiente", "jarvis qué tengo"):
            self.assertEqual(brain._clasificar_peticion(texto), "claude", texto)

    def test_atajos_de_tareas_siguen_y_que_tengo_ya_no_es_atajo(self):
        self.assertEqual(brain._match_atajo(brain._normalizar("mis tareas")), "listar_tareas")
        self.assertIsNone(brain._match_atajo(brain._normalizar("qué tengo")))


if __name__ == "__main__":
    unittest.main()
