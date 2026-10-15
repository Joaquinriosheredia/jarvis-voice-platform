"""Captura de la pantalla principal para ver_pantalla (visión).

La imagen vive solo en memoria: nunca se escribe en disco, nunca se imprime y
no va a la memoria de conversación ni a turnos.jsonl; solo viaja en el
tool_result de ese turno. Pillow: ImageGrab.grab() sin all_screens captura
únicamente el monitor principal en Windows.
"""
import base64
import io

MAX_LADO           = 1568            # px en el lado largo (lo que Claude procesa sin reescalar)
CALIDAD            = 80
MAX_LADO_REINTENTO = 1024
CALIDAD_REINTENTO  = 60
MAX_BYTES          = 3 * 1024 * 1024  # del base64 resultante


def _codificar(imagen, lado, calidad):
    copia = imagen.copy()
    copia.thumbnail((lado, lado))  # mantiene la proporción; nunca agranda
    buf = io.BytesIO()
    copia.save(buf, format="JPEG", quality=calidad)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def capturar_pantalla():
    """(True, JPEG en base64) o (False, motivo). Un reintento más pequeño si pasa de 3 MB."""
    try:
        from PIL import ImageGrab
        imagen = ImageGrab.grab().convert("RGB")
    except Exception as e:
        return False, f"No se pudo capturar la pantalla: {type(e).__name__}"
    try:
        for lado, calidad in ((MAX_LADO, CALIDAD), (MAX_LADO_REINTENTO, CALIDAD_REINTENTO)):
            datos = _codificar(imagen, lado, calidad)
            if len(datos) <= MAX_BYTES:
                return True, datos
    except Exception as e:
        return False, f"No se pudo procesar la captura: {type(e).__name__}"
    return False, "La captura es demasiado grande para enviarla incluso reducida."
