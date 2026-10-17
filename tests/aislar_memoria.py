"""Los tests nunca leen ni escriben la memoria entre sesiones real (C:\\Jarvis-secrets).

Cada test_*.py lo importa justo tras añadir la raíz al path: memoria_sesiones.RUTA_DB pasa
a una carpeta temporal que se borra al salir. Los tests que necesitan su propia base la
siguen parcheando encima. Sin esto, el guardado periódico de jarvis.py (un hilo cada pocos
turnos) escribiría en la base real con un resumen de los turnos simulados.
"""
import atexit
import os
import shutil
import sys
import tempfile

# Ningún test llama a la API real de Anthropic, aunque el entorno tenga una clave de verdad:
# lo que no esté mockeado falla al momento contra un puerto local cerrado
os.environ["ANTHROPIC_API_KEY"] = "test-key-no-real"
os.environ["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:9"
# La pausa de Ollama tras un fallo (modo degradado) no pasa de un test al siguiente;
# los tests que la prueban fijan la suya
os.environ["JARVIS_OLLAMA_PAUSA_S"] = "0"
# Ni la voz de Windows de emergencia (sonaría de verdad); los tests que la prueban la mockean
os.environ["JARVIS_VOZ_EMERGENCIA"] = "0"
if "brain" in sys.modules:   # importado antes que este módulo: su cliente también va al puerto cerrado
    import anthropic
    sys.modules["brain"].cliente = anthropic.Anthropic(max_retries=0)

import memoria_sesiones

CARPETA = tempfile.mkdtemp(prefix="jarvis-tests-memoria-")
memoria_sesiones.RUTA_DB = os.path.join(CARPETA, "memoria_sesiones.db")
atexit.register(shutil.rmtree, CARPETA, True)

# Sin guardado periódico salvo en los tests que lo prueban (fijan sus propios umbrales):
# así ningún test lanza en segundo plano un resumen real con Ollama o Claude.
CADA_TURNOS, INACTIVIDAD_S = memoria_sesiones.CADA_TURNOS, memoria_sesiones.INACTIVIDAD_S
memoria_sesiones.CADA_TURNOS = memoria_sesiones.INACTIVIDAD_S = float("inf")
