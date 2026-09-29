"""Fase 1C: gate de permisos, confirmación de un solo uso y validación de rutas.

Ninguna operación real: subprocess.run / shutil.rmtree / os.remove van
mockeados en todo test que pueda llegar a ejecutar una rama destructiva.
"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402

DESC   = r"E:\descargas ryzen"
DESK   = r"C:\Users\Usuario\Desktop"
FILE_A = DESC + r"\informe.pdf"
FILE_B = DESC + r"\otro.pdf"
CONF_B = "confirmo borrado"
CONF_P = "confirmo apagado del pc"


def bloqueado(res):
    return isinstance(res, str) and res.startswith("BLOQUEADO")


class Base(unittest.TestCase):
    def setUp(self):
        brain._pendiente = None
        self.addCleanup(setattr, brain, "_pendiente", None)


class TestConfirmacion(Base):
    def test_borrar_archivo_sin_confirmacion_bloqueado(self):
        res = brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")
        self.assertTrue(bloqueado(res))
        self.assertIn("confirmo borrado", res)

    def test_texto_usuario_omitido_bloquea(self):
        self.assertTrue(bloqueado(brain._gate("borrar_archivo", {"ruta": FILE_A})))

    def test_apagar_pc_sin_confirmacion_bloqueado(self):
        self.assertTrue(bloqueado(brain._gate("apagar_pc", {"accion": "apagar"}, "apaga el ordenador")))

    def test_confirmacion_generada_por_el_modelo_bloqueada(self):
        # (a) el modelo mete la frase en los parámetros
        p = {"ruta": FILE_A, "confirmacion": CONF_B, "texto": CONF_B}
        self.assertTrue(bloqueado(brain._gate("borrar_archivo", p, "borra el informe")))
        # (b) reintento en el MISMO turno (mismo texto de usuario): sigue bloqueado
        self.assertTrue(bloqueado(brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")))
        # (c) la frase dentro de una oración más larga (p. ej. eco de la voz de Jarvis)
        largo = f"Para borrar informe.pdf di {CONF_B}"
        self.assertTrue(bloqueado(brain._gate("borrar_archivo", {"ruta": FILE_A}, largo)))
        # (d) frase como enunciado completo pero SIN acción pendiente previa:
        #     no autoriza y no crea pendiente (el modelo no puede sembrarla en el mismo turno)
        brain._pendiente = None
        self.assertTrue(bloqueado(brain._gate("borrar_archivo", {"ruta": FILE_A}, CONF_B)))
        self.assertIsNone(brain._pendiente)
        self.assertTrue(bloqueado(brain._gate("borrar_archivo", {"ruta": FILE_A}, CONF_B)))

    def test_confirmacion_valida_permitida(self):
        self.assertTrue(bloqueado(brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")))
        self.assertIsNone(brain._gate("borrar_archivo", {"ruta": FILE_A}, "Jarvis, ¡Confirmo borrado!"))

    def test_confirmacion_tolera_espacios_mal_transcritos_por_whisper(self):
        for dicho in ("con firmo borrado", "Confirmo  bor rado.", "Jarvis, con firmo borrado"):
            brain._pendiente = None
            brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")
            self.assertIsNone(brain._gate("borrar_archivo", {"ruta": FILE_A}, dicho), dicho)
        # sigue exigiendo el enunciado completo
        brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")
        self.assertTrue(bloqueado(brain._gate("borrar_archivo", {"ruta": FILE_A}, "con firmo borrado ya")))

    def test_apagar_confirmacion_valida_con_tildes_y_mayusculas(self):
        brain._gate("apagar_pc", {"accion": "apagar"}, "apágate")
        self.assertIsNone(brain._gate("apagar_pc", {}, "Confirmo apagado del PC."))

    def test_confirmacion_de_un_solo_uso(self):
        brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")
        self.assertIsNone(brain._gate("borrar_archivo", {"ruta": FILE_A}, CONF_B))
        self.assertTrue(bloqueado(brain._gate("borrar_archivo", {"ruta": FILE_A}, CONF_B)))

    def test_confirmacion_caducada_bloqueada(self):
        with patch("brain.time.monotonic", return_value=1000.0):
            brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")
        with patch("brain.time.monotonic", return_value=1000.0 + brain.CONFIRM_TTL + 1):
            self.assertTrue(bloqueado(brain._gate("borrar_archivo", {"ruta": FILE_A}, CONF_B)))

    def test_confirmacion_vigente_en_el_limite_del_ttl_permitida(self):
        with patch("brain.time.monotonic", return_value=1000.0):
            brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")
        with patch("brain.time.monotonic", return_value=1000.0 + brain.CONFIRM_TTL):
            self.assertIsNone(brain._gate("borrar_archivo", {"ruta": FILE_A}, CONF_B))

    def test_hay_pendiente_refleja_estado_y_ttl(self):
        self.assertFalse(brain.hay_pendiente())
        with patch("brain.time.monotonic", return_value=1000.0):
            brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")
            self.assertTrue(brain.hay_pendiente())
        with patch("brain.time.monotonic", return_value=1000.0 + brain.CONFIRM_TTL - 1):
            self.assertTrue(brain.hay_pendiente())
        with patch("brain.time.monotonic", return_value=1000.0 + brain.CONFIRM_TTL):
            self.assertFalse(brain.hay_pendiente())
        brain._gate("borrar_archivo", {"ruta": FILE_A}, CONF_B)  # gasta el pendiente (o ya caducado)
        self.assertFalse(brain.hay_pendiente())

    def test_confirmacion_valida_pero_ruta_diferente_bloqueada(self):
        brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")
        self.assertTrue(bloqueado(brain._gate("borrar_archivo", {"ruta": FILE_B}, CONF_B)))
        # y el enunciado de confirmación quedó gastado: la original tampoco pasa
        self.assertTrue(bloqueado(brain._gate("borrar_archivo", {"ruta": FILE_A}, CONF_B)))

    def test_confirmacion_de_otra_tool_no_vale(self):
        brain._gate("apagar_pc", {"accion": "apagar"}, "apágate")
        self.assertTrue(bloqueado(brain._gate("apagar_pc", {"accion": "apagar"}, CONF_B)))

    def test_reiniciar_no_se_confirma_como_apagar(self):
        brain._gate("apagar_pc", {"accion": "apagar"}, "apágate")
        self.assertTrue(bloqueado(brain._gate("apagar_pc", {"accion": "reiniciar"}, CONF_P)))


class TestRutas(Base):
    def test_fuera_de_allowlist_bloqueada(self):
        for ruta in (r"C:\Windows\System32\x.dll", r"C:\Users\Usuario\Documents\a.txt",
                     r"C:\Users\Usuario", "C:\\", "E:\\", r"E:\otra\a.txt",
                     DESC + "2" + r"\a.txt",  # prefijo de texto, no de componente
                     r"\\servidor\share\a.txt", "relativa\\a.txt", ""):
            ok, _ = brain._validate_path(ruta)
            self.assertFalse(ok, ruta)
            self.assertTrue(bloqueado(brain._gate("borrar_archivo", {"ruta": ruta}, "x")), ruta)

    def test_zonas_de_jarvis_y_sistema_bloqueadas(self):
        for ruta in (r"C:\Jarvis-secrets\.env", r"C:\Jarvis\brain.py",
                     r"C:\Users\Usuario\OneDrive\a.txt", r"C:\Users\Usuario\AppData\x"):
            self.assertFalse(brain._validate_path(ruta)[0], ruta)

    def test_traversal_bloqueado(self):
        for ruta in (DESC + r"\..\Windows\x", DESC + r"\sub\..\..\x", DESK + "/../secreto.txt"):
            ok, motivo = brain._validate_path(ruta)
            self.assertFalse(ok, ruta)
            self.assertIn("traversal", motivo)
            self.assertTrue(bloqueado(brain._gate("mover_archivo", {"origen": ruta, "destino": DESC}, "x")))

    def test_ruta_dentro_de_allowlist_permitida(self):
        for ruta in (FILE_A, DESC + r"\sub\a.txt", DESK + r"\nota.txt", DESC.upper() + r"\A.TXT"):
            self.assertTrue(brain._validate_path(ruta)[0], ruta)
        self.assertIsNone(brain._gate("crear_carpeta", {"ruta": DESC + r"\nueva"}, "x"))
        self.assertIsNone(brain._gate("mover_archivo", {"origen": FILE_A, "destino": DESK}, "x"))
        # borrar dentro de allowlist: pasa la ruta, y queda a la espera de confirmación
        res = brain._gate("borrar_archivo", {"ruta": FILE_A}, "x")
        self.assertIn("confirmación", res)

    def test_raiz_de_allowlist_no_se_borra_ni_se_mueve(self):
        self.assertTrue(bloqueado(brain._gate("borrar_archivo", {"ruta": DESC}, "x")))
        self.assertTrue(bloqueado(brain._gate("mover_archivo", {"origen": DESK, "destino": DESC}, "x")))

    def test_organizar_solo_en_descargas(self):
        self.assertIsNone(brain._gate("organizar_directorio", {"ruta": DESC}, "x"))
        self.assertIsNone(brain._gate("organizar_directorio", {}, "x"))  # default DESCARGAS
        self.assertTrue(bloqueado(brain._gate("organizar_directorio", {"ruta": DESK}, "x")))
        self.assertTrue(bloqueado(brain._gate("organizar_directorio", {"ruta": r"C:\Users\Usuario"}, "x")))


class TestBorrarMultiples(Base):
    def _skills(self):
        return {"gmail": MagicMock()}

    def test_11_bloqueado(self):
        s = self._skills()
        res = brain.ejecutar_herramienta("borrar_multiples_emails", {"cantidad": 11}, s, "borra 11")
        self.assertTrue(bloqueado(res))
        s["gmail"].borrar_multiples.assert_not_called()

    def test_10_pasa_el_tope_pero_bloquea_por_falta_de_frase(self):
        # Fase 2B: borrar_multiples_emails es DESTRUCTIVE y aún no tiene frase (Fase 2C).
        # 10 supera el control de tope (no es el mensaje de "máximo"), pero no se ejecuta.
        s = self._skills()
        res = brain.ejecutar_herramienta("borrar_multiples_emails", {"cantidad": 10}, s, "borra 10")
        self.assertTrue(bloqueado(res))
        self.assertIn("frase de confirmación", res)
        self.assertNotIn("máximo", res)
        s["gmail"].borrar_multiples.assert_not_called()

    def test_cero_negativo_o_basura_bloqueados(self):
        s = self._skills()
        for c in (0, -3, "muchos", None):
            self.assertTrue(bloqueado(brain.ejecutar_herramienta(
                "borrar_multiples_emails", {"cantidad": c}, s, "x")), c)
        s["gmail"].borrar_multiples.assert_not_called()


class TestIntegracionDispatcher(Base):
    def test_apagar_pc_extremo_a_extremo_con_subprocess_mockeado(self):
        with patch("brain.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0)
            r1 = brain.ejecutar_herramienta("apagar_pc", {"accion": "apagar"}, {}, "apágate")
            self.assertTrue(bloqueado(r1))
            run.assert_not_called()
            r2 = brain.ejecutar_herramienta("apagar_pc", {"accion": "apagar"}, {}, CONF_P)
            self.assertEqual(r2, "Comando de apagado aceptado")
            run.assert_called_once_with(['shutdown', '/s', '/t', '5'], capture_output=True, timeout=10)
            r3 = brain.ejecutar_herramienta("apagar_pc", {"accion": "apagar"}, {}, CONF_P)
            self.assertTrue(bloqueado(r3))
            run.assert_called_once()

    def test_borrar_archivo_extremo_a_extremo_con_os_mockeado(self):
        with patch("brain.os.path.exists", return_value=True), \
             patch("brain.os.path.isdir", return_value=False), \
             patch("brain.os.remove") as rm, patch("brain.shutil.rmtree") as rmtree:
            r1 = brain.ejecutar_herramienta("borrar_archivo", {"ruta": FILE_A}, {}, "borra el informe")
            self.assertTrue(bloqueado(r1))
            rm.assert_not_called()
            r2 = brain.ejecutar_herramienta("borrar_archivo", {"ruta": FILE_A}, {}, CONF_B)
            self.assertEqual(r2, f"Eliminado: {FILE_A}")
            rm.assert_called_once_with(FILE_A)
            rmtree.assert_not_called()

    def test_pensar_pasa_el_texto_real_del_usuario_al_gate(self):
        tool_use = SimpleNamespace(type="tool_use", name="apagar_pc", id="t1", input={"accion": "apagar"})
        r_tool = SimpleNamespace(stop_reason="tool_use", content=[tool_use])
        r_fin  = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(text="ok")])
        with patch.object(brain.cliente.messages, "create", side_effect=[r_tool, r_fin]), \
             patch("builtins.print"), \
             patch("brain._ejecutar", return_value=brain._ok("x")) as ej:
            brain.pensar("apaga el pc", contexto=f"Jarvis: di {CONF_P}", skills={})
        self.assertEqual(ej.call_args.args[3], "apaga el pc")  # texto, NO el contexto

    def test_toda_tool_del_schema_tiene_riesgo(self):
        nombres = {t["name"] for t in brain.TOOLS}
        self.assertEqual(len(nombres), 33)
        self.assertEqual(nombres, set(brain.TOOL_RISK))
        self.assertTrue(set(brain.TOOL_RISK.values()) <= {"READ", "WRITE", "EXTERNAL", "DESTRUCTIVE"})
        por_nivel = {n: {t for t, r in brain.TOOL_RISK.items() if r == n}
                     for n in ("READ", "WRITE", "EXTERNAL", "DESTRUCTIVE")}
        self.assertEqual({n: len(v) for n, v in por_nivel.items()},
                         {"READ": 13, "WRITE": 3, "EXTERNAL": 13, "DESTRUCTIVE": 4})
        self.assertEqual(por_nivel["DESTRUCTIVE"],
                         {"borrar_archivo", "apagar_pc", "borrar_email", "borrar_multiples_emails"})
        self.assertEqual(por_nivel["WRITE"], {"recordatorio", "nota", "cerrar_jarvis"})

    def test_reclasificaciones_de_la_fase_2b(self):
        esperado = {
            **dict.fromkeys(["reproducir_musica", "parar_musica", "abrir_youtube", "abrir_web", "abrir_app",
                             "organizar_directorio", "crear_carpeta", "mover_archivo", "archivar_email",
                             "volumen_subir", "volumen_bajar", "pomodoro", "siguiente_racha"], "EXTERNAL"),
            "cerrar_jarvis": "WRITE",
            "borrar_email": "DESTRUCTIVE", "borrar_multiples_emails": "DESTRUCTIVE",
        }
        for tool, nivel in esperado.items():
            self.assertEqual(brain.TOOL_RISK[tool], nivel, tool)

    def test_toda_tool_destructive_sin_frase_falla_cerrado_sin_keyerror(self):
        # Fija el estado de la Fase 3 (borrar_email ya tiene frase; borrar_multiples_emails aún no).
        # Al añadir su frase la primera aserción dejará de cumplirse a propósito: habrá que actualizar este test.
        sin_frase = [t for t, r in brain.TOOL_RISK.items() if r == "DESTRUCTIVE" and t not in brain.CONFIRM_PHRASES]
        self.assertEqual(set(sin_frase), {"borrar_multiples_emails"})
        for tool in sin_frase:
            res = brain._gate(tool, {"indice": 0, "cantidad": 3}, "borra")
            self.assertTrue(bloqueado(res), tool)
            self.assertIn("frase de confirmación", res)
            self.assertIsNone(brain._pendiente)              # no arma nada

    def test_destructive_sin_frase_no_gasta_un_pendiente_ajeno(self):
        brain._gate("borrar_archivo", {"ruta": FILE_A}, "borra el informe")     # arma pendiente legítimo
        # en el turno de confirmación, el modelo intenta además borrar un email
        self.assertTrue(bloqueado(brain._gate("borrar_email", {"indice": 0}, CONF_B)))
        self.assertIsNotNone(brain._pendiente)               # el pendiente sigue intacto
        self.assertIsNone(brain._gate("borrar_archivo", {"ruta": FILE_A}, CONF_B))   # y se puede confirmar

    def test_borrar_email_extremo_a_extremo_no_llama_a_gmail(self):
        s = {"gmail": MagicMock()}
        res = brain.ejecutar_herramienta("borrar_email", {"indice": 0}, s, "borra el primer email")
        self.assertTrue(bloqueado(res))
        s["gmail"].borrar_email.assert_not_called()


if __name__ == "__main__":
    unittest.main()
