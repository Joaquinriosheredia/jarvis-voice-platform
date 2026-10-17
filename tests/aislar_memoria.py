"""Los tests nunca leen ni escriben la memoria entre sesiones real (C:\\Jarvis-secrets).

Cada test_*.py lo importa justo tras añadir la raíz al path: memoria_sesiones.RUTA_DB pasa
a una carpeta temporal que se borra al salir. Los tests que necesitan su propia base la
siguen parcheando encima. Sin esto, el guardado periódico de jarvis.py (un hilo cada pocos
turnos) escribiría en la base real con un resumen de los turnos simulados.
"""
import atexit
import os
import shutil
import tempfile

import memoria_sesiones

CARPETA = tempfile.mkdtemp(prefix="jarvis-tests-memoria-")
memoria_sesiones.RUTA_DB = os.path.join(CARPETA, "memoria_sesiones.db")
atexit.register(shutil.rmtree, CARPETA, True)

# Sin guardado periódico salvo en los tests que lo prueban (fijan sus propios umbrales):
# así ningún test lanza en segundo plano un resumen real con Ollama o Claude.
CADA_TURNOS, INACTIVIDAD_S = memoria_sesiones.CADA_TURNOS, memoria_sesiones.INACTIVIDAD_S
memoria_sesiones.CADA_TURNOS = memoria_sesiones.INACTIVIDAD_S = float("inf")
