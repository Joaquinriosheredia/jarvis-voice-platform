"""Órdenes de silencio y de apagado por voz. Única fuente de las dos listas.

Puro (sin audio ni Whisper): qué frase es una orden, cuándo es eco del propio
TTS, la respuesta a "¿cómo te callo / te cierro?" y un detector de voz corta y
dominante sobre el eco. Ninguna orden confirma ni ejecuta una acción del gate.

- SILENCIO: corta el audio, vacía la cola de frases y cancela la generación;
  JARVIS sigue abierto y escuchando.
- APAGADO: lo mismo y, después, cierra JARVIS.
Una orden solo vale si es la frase entera (más relleno como "Jarvis" o "por
favor") y tiene como mucho MAX_PALABRAS palabras sin contar "Jarvis".
"""
import re
import unicodedata
from collections import deque

import numpy as np

SILENCIO = ("cállate", "no hables", "escucha", "silencio", "para", "basta")
APAGADO  = ("apágate", "apaga JARVIS", "apaga JARVIS del todo", "adiós", "hasta luego",
            "desconéctate", "cierra JARVIS")
# Lo único que puede acompañar a la orden ("para ya", "cállate, por favor", "vale, adiós")
RELLENO  = {"ya", "por", "favor", "porfa", "vale", "oye", "eh"}
NOMBRE   = "jarvis"
MAX_PALABRAS = 4


def normalizar(texto):
    t = unicodedata.normalize("NFD", (texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())


_FRASES = {tipo: [normalizar(f).split() for f in frases]
           for tipo, frases in (("silencio", SILENCIO), ("apagado", APAGADO))}
_coinciden = [(a, b) for a in SILENCIO for b in APAGADO if normalizar(a) == normalizar(b)]
assert not _coinciden, f"una frase no puede silenciar y apagar a la vez: {_coinciden}"


def clasificar(texto):
    """"silencio" | "apagado" | None. La frase transcrita entera tiene que ser la
    orden (con relleno y "Jarvis" delante o detrás), sin nada más."""
    transcritas = normalizar(texto).split()
    if sum(p != NOMBRE for p in transcritas) > MAX_PALABRAS:  # el relleno también cuenta
        return None
    palabras = [p for p in transcritas if p not in RELLENO]
    if not palabras:
        return None
    variantes = [palabras]
    if palabras[0] == NOMBRE:
        variantes.append(palabras[1:])
    if palabras[-1] == NOMBRE:
        variantes += [v[:-1] for v in list(variantes) if v and v[-1] == NOMBRE]
    for tipo, frases in _FRASES.items():
        for frase in frases:
            if any(v in (frase, frase + frase) for v in variantes):
                return tipo
    return None


def es_orden_silencio(texto):
    return clasificar(texto) == "silencio"


def es_orden_apagado(texto):
    return clasificar(texto) == "apagado"


def es_orden_parada(texto):
    """Cualquier orden de las dos listas: las dos cortan la voz y la generación."""
    return clasificar(texto) is not None


def eco_probable(texto, texto_hablado):
    """True si lo transcrito puede ser JARVIS oyéndose a sí mismo: lo que está diciendo
    contiene un trozo que, solo, sería una orden (p. ej. lee la lista de órdenes)."""
    if not texto_hablado:
        return False
    return any(clasificar(trozo) for trozo in re.split(r"[.!?¿¡\n,;:]+|\bo\b|\by\b", texto_hablado))


# ── AYUDA: "¿cómo te callo?", "¿cómo te cierro?" ──────────────────
_RE_PREGUNTA = re.compile(r"\b(como|que digo|que (?:tengo|hay|debo|puedo)(?: que)? decir|"
                          r"que (?:palabras|ordenes|frases|comandos)|con que)\b")
_RE_A_JARVIS = re.compile(r"\bte\b|\bjarvis\b|\w+(?:arte|erte|irte)\b")
_RE_SILENCIAR = re.compile(r"\b(silenci\w*|call\w*|interrump\w*|pares|parar\w*|dejar? de hablar)\b")
_RE_APAGAR    = re.compile(r"\b(apag\w*|cierr\w*|cerrar\w*|desconect\w*)\b")
_RE_OTRA_COSA = re.compile(r"\b(pc|ordenador|equipo|musica|luz|luces|ventana|navegador|programa|"
                           r"app|aplicacion|pantalla|correo|email)\b")


def pregunta_de_ayuda(texto):
    """"silencio" | "apagado" | "ambas" | None si pregunta cómo callar o cerrar a JARVIS."""
    t = normalizar(texto)
    if not _RE_PREGUNTA.search(t) or not _RE_A_JARVIS.search(t) or _RE_OTRA_COSA.search(t):
        return None
    silencio, apagado = bool(_RE_SILENCIAR.search(t)), bool(_RE_APAGAR.search(t))
    if silencio and apagado:
        return "ambas"
    return "silencio" if silencio else ("apagado" if apagado else None)


def _lista(frases):
    citadas = [f"«{f}»" for f in frases]
    return ", ".join(citadas[:-1]) + " o " + citadas[-1]


def texto_ayuda(tipo="ambas"):
    """Respuesta fija, construida desde SILENCIO y APAGADO (nunca la improvisa el modelo)."""
    partes = []
    if tipo in ("silencio", "ambas"):
        partes.append(f"Para que me calle sin cerrarme, di {_lista(SILENCIO)}. "
                      "También puedes usar el botón Callar, Esc o Control Alt P.")
    if tipo in ("apagado", "ambas"):
        partes.append(f"Para cerrarme del todo, di {_lista(APAGADO)}.")
    partes.append(f"Tiene que ser una frase corta, de {MAX_PALABRAS} palabras como mucho.")
    return " ".join(partes)


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
