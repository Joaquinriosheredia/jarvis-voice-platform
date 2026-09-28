"""Verifica que ejecutar_herramienta() llama a skills.tareas con los
argumentos en el orden correcto (Fase 1B). Todo mockeado: sin voz, sin red."""
import os
import sys
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-no-real")  # anthropic.Anthropic() se instancia al importar brain
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402


class TestBrainLlamaATareasEnOrden(unittest.TestCase):
    def setUp(self):
        self.tareas = MagicMock()
        self.audio = MagicMock()
        self.memoria = {"marca": "memoria-test"}
        self.skills = {"tareas": self.tareas, "audio": self.audio, "memoria": self.memoria}

    def test_recordatorio_orden_argumentos(self):
        brain.ejecutar_herramienta("recordatorio", {"tarea": "llamar a Ana", "hora": "18:30"}, self.skills)
        self.tareas.añadir_recordatorio.assert_called_once_with(
            self.memoria, self.audio.hablar, "llamar a Ana", hora_str="18:30"
        )

    def test_nota_orden_argumentos(self):
        brain.ejecutar_herramienta("nota", {"texto": "comprar pan"}, self.skills)
        self.tareas.añadir_nota_voz.assert_called_once_with(self.memoria, "comprar pan")

    def test_pomodoro_orden_argumentos(self):
        brain.ejecutar_herramienta("pomodoro", {"minutos": 40}, self.skills)
        self.tareas.pomodoro.assert_called_once_with(self.audio.hablar, 40)


if __name__ == "__main__":
    unittest.main()
