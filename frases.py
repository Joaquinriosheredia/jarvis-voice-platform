"""Troceado de una respuesta en frases para hablarlas una a una.

Corta en . ? ! o salto de línea, con un mínimo de MIN_CHARS por trozo (los
trozos cortos se juntan con el siguiente) y sin cortar decimales (3.5) ni
abreviaturas (p.ej., etc., Sr.). Lo que quede al final es un último trozo,
aunque sea corto o no acabe en puntuación.
"""
import re

MIN_CHARS    = 20
FINALES      = ".?!"
ABREVIATURAS = {"p.ej", "ej", "etc", "sr", "sra", "srta", "dr", "dra", "ud", "uds", "pág", "pag",
                "aprox", "núm", "num", "vs", "admin", "dpto", "av", "c"}


# ── Saneado de markdown para la voz (la pantalla recibe el texto original) ──
_RE_ENLACE   = re.compile(r"\[([^\]]+)\]\((?:https?://)?[^)\s]+\)")
_RE_URL      = re.compile(r"https?://\S{20,}")
_RE_TITULO   = re.compile(r"^[ \t]*#+[ \t]*", re.M)
_RE_VINETA   = re.compile(r"^[ \t]*[-*][ \t]+", re.M)
_RE_ENFASIS  = re.compile(r"\*\*|__|`")
_RE_ESPACIOS = re.compile(r"[ \t]{2,}")


def limpiar_para_voz(texto):
    """Quita **, __, `, # de título, viñetas '- ' / '* ', enlaces [texto](url) → texto
    y URLs sueltas largas. Solo para lo que se sintetiza."""
    if not texto:
        return texto
    t = _RE_ENLACE.sub(r"\1", texto)
    t = _RE_URL.sub("", t)
    t = _RE_TITULO.sub("", t)
    t = _RE_VINETA.sub("", t)
    t = _RE_ENFASIS.sub("", t)
    t = _RE_ESPACIOS.sub(" ", t)
    return "\n".join(l.strip() for l in t.splitlines()).strip()


def _es_abreviatura(texto, i):
    """True si el '.' de texto[i] cierra una abreviatura conocida o una inicial (p. ej. 'J.')."""
    inicio = i
    while inicio > 0 and not texto[inicio - 1].isspace():
        inicio -= 1
    palabra = texto[inicio:i].strip("(¿¡\"'").lower()
    return palabra in ABREVIATURAS or (len(palabra) == 1 and palabra.isalpha())


def _es_corte(texto, i):
    c = texto[i]
    if c == "\n":
        return True
    if c not in FINALES:
        return False
    siguiente = texto[i + 1] if i + 1 < len(texto) else ""
    if siguiente and not siguiente.isspace():  # 3.5, p.ej, "...", "?!"
        return False
    return not (c == "." and _es_abreviatura(texto, i))


def trocear(texto, minimo=MIN_CHARS):
    """Lista de frases en orden; " ".join(...) conserva el contenido."""
    frases, inicio = [], 0
    for i in range(len(texto or "")):
        if _es_corte(texto, i):
            trozo = texto[inicio:i + 1].strip()
            if len(trozo) >= minimo:
                frases.append(trozo)
                inicio = i + 1
    resto = (texto or "")[inicio:].strip()
    if resto:
        frases.append(resto)
    return frases
