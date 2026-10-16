"""Órdenes de parada por voz mientras JARVIS habla o genera la respuesta.

Puro (sin audio ni Whisper): qué texto es una orden de parada, cuándo es eco del
propio TTS y un detector de voz corta y dominante sobre el eco. Una orden de
parada solo calla a JARVIS: nunca confirma ni ejecuta ninguna acción.
"""
import re
import unicodedata
from collections import deque

import numpy as np

ORDENES  = {"para", "parar", "parate", "callate", "calla", "silencio", "basta", "apagate", "stop"}
# Lo único que puede acompañar a la orden ("jarvis, para ya", "cállate, por favor")
RELLENO  = {"jarvis", "ya", "por", "favor", "porfa", "vale", "oye", "eh"}
MAX_PALABRAS = 4


def normalizar(texto):
    t = unicodedata.normalize("NFD", (texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


def es_orden_parada(texto):
    """True si el texto es solo una orden de parada (más relleno): corta y sin nada más.
    "para la música" o "¿para qué sirve?" no lo son."""
    palabras = normalizar(texto).split()
    if not palabras or len(palabras) > MAX_PALABRAS:
        return False
    nucleo = [p for p in palabras if p not in RELLENO]
    return bool(nucleo) and len(nucleo) <= 2 and all(p in ORDENES for p in nucleo)


def eco_probable(texto, texto_hablado):
    """True si lo transcrito puede ser JARVIS oyéndose a sí mismo: lo que está diciendo
    contiene una frase que, sola, sería esa orden (p. ej. responde "Silencio.")."""
    if not texto_hablado:
        return False
    for frase in re.split(r"[.!?¿¡\n,;:]+", texto_hablado):
        if es_orden_parada(frase):
            return True
    return False


# ── DETECTOR DE VOZ DOMINANTE ─────────────────────────────────────
class DetectorVoz:
    """Segmenta por energía la voz del usuario sobre el eco del TTS.

    El eco esperado es ganancia * rms_ref (lo que JARVIS reproduce ahora). La
    ganancia altavoz→micro se estima como la mediana de rms_mic / rms_ref de los
    últimos 2 s: con auriculares tiende a 0 y con altavoces sube. Una trama es voz
    si supera FACTOR veces el eco esperado más un margen (y el umbral base).
    Un segmento solo se entrega si es corto (orden de una o dos palabras): el habla
    continua (eco de frases largas, conversación) se descarta sin transcribir.
    """
    FACTOR         = 2.5    # ~ +8 dB sobre el eco esperado
    MARGEN         = 200
    REF_MIN        = 150    # por debajo, JARVIS está en silencio: no hay eco que estimar
    MIN_MUESTRAS   = 10     # tramas con referencia antes de fiarse de la ganancia (0,3 s)
    VENTANA        = 66     # 2 s de tramas de 30 ms
    FRAMES_INICIO  = 3
    FRAMES_FIN     = 8      # 240 ms de silencio cierran el segmento
    MIN_FRAMES     = 6      # 180 ms
    MAX_FRAMES     = 67     # 2 s: más largo no es una orden de parada
    PREVIO         = 5      # tramas de antes del inicio (para no cortar la primera sílaba)

    def __init__(self, umbral_base):
        self.umbral_base = umbral_base
        self._ratios     = deque(maxlen=self.VENTANA)
        self._previo     = deque(maxlen=self.PREVIO)
        self._reiniciar()

    def _reiniciar(self):
        self._segmento = None
        self._inicio   = 0
        self._silencio = 0
        self._largo    = False

    def umbral(self, rms_ref):
        if rms_ref < self.REF_MIN:
            return self.umbral_base
        if len(self._ratios) < self.MIN_MUESTRAS:
            return float("inf")   # sin calibrar no se interpreta nada mientras suena el TTS
        ganancia = float(np.median(self._ratios))
        return max(self.umbral_base, ganancia * rms_ref * self.FACTOR + self.MARGEN)

    def trama(self, rms_mic, rms_ref, frame):
        """Procesa una trama; devuelve el audio (np.int16) de un segmento corto recién
        terminado, o None."""
        umbral = self.umbral(rms_ref)
        if rms_ref >= self.REF_MIN:
            self._ratios.append(rms_mic / rms_ref)
        voz = rms_mic > umbral

        if self._segmento is None:
            self._previo.append(frame)
            self._inicio = self._inicio + 1 if voz else 0
            if self._inicio >= self.FRAMES_INICIO:
                self._segmento = list(self._previo)
                self._previo.clear()
                self._silencio = 0
            return None

        if not self._largo:
            self._segmento.append(frame)
            if len(self._segmento) > self.MAX_FRAMES + self.PREVIO:
                self._largo = True        # habla continua: se descarta al terminar
        self._silencio = 0 if voz else self._silencio + 1
        if self._silencio < self.FRAMES_FIN:
            return None
        segmento, largo = self._segmento, self._largo
        self._reiniciar()
        if largo or len(segmento) - self.FRAMES_FIN < self.MIN_FRAMES:
            return None
        return np.concatenate(segmento)
