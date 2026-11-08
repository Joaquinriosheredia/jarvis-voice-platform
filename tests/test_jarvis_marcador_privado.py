"""El historial guarda los turnos privados como «[Respuesta privada no guardada. Tema: correo...]».
El 2026-11-08 el modelo imitó ese marcador como respuesta y JARVIS lo leyó en voz alta (con un
«los dos IDs de Dalis Press» inventado). jarvis._turno no lo dice nunca: dice MSG_MARCADOR_PRIVADO.
jarvis.py se importa con whisper/audio/skills/memoria sustituidos por mocks."""
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

import brain  # noqa: E402

_STUBS = {n: MagicMock() for n in (
    "dotenv", "whisper", "audio", "memoria", "skills", "skills.musica", "skills.sistema",
    "skills.webs", "skills.authority", "skills.tareas", "skills.gmail")}
with patch.dict(sys.modules, _STUBS), contextlib.redirect_stdout(io.StringIO()):
    sys.modules.pop("jarvis", None)
    import jarvis  # noqa: E402
atexit.unregister(jarvis.cleanup)

MARCADOR = ("[Respuesta privada no guardada. Tema: correo. Tools: borrar_multiples_emails. "
            "Pendiente de confirmar por voz: borrar_multiples_emails con los dos IDs de Dalis Press]")


class TestMarcadorPrivado(unittest.TestCase):
    def _turno(self, respuesta):
        dichos = []
        with patch("brain.pensar", return_value=respuesta), \
                patch("brain.generacion_cancelada", return_value=False), \
                patch("brain.anuncio_confirmacion", return_value=None), \
                patch.object(jarvis, "_d1_fix", return_value=False), \
                patch.object(jarvis, "_decir_respuesta", side_effect=dichos.append), \
                patch.object(jarvis, "_guardar_en_historial"), \
                patch.object(jarvis, "_registrar_en_memoria_sesiones"), \
                contextlib.redirect_stdout(io.StringIO()):
            jarvis._turno("No, solo quiero borrar los dos Dalis Press.")
        return dichos

    def test_el_marcador_imitado_no_se_dice(self):
        self.assertEqual(self._turno(MARCADOR), [brain.MSG_MARCADOR_PRIVADO])

    def test_una_respuesta_normal_se_dice_tal_cual(self):
        self.assertEqual(self._turno("Tienes 3 correos de AliExpress."), ["Tienes 3 correos de AliExpress."])


if __name__ == "__main__":
    unittest.main()
