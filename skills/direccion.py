"""Direcciones de email dictadas por voz: validar el formato, extraerlas del texto
de Whisper ("pepe arroba gmail punto com") y deletrearlas para confirmarlas.

Puro y sin dependencias. Nunca imprime ni guarda las direcciones. redactar() es el
enmascarado de todo lo que se guarda en disco (logs, historial de memoria.json).
"""
import re

MAX_LONGITUD = 254

_RE_EMAIL = re.compile(
    r"[a-z0-9](?:[a-z0-9._%+-]{0,62}[a-z0-9])?"            # usuario
    r"@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}")  # dominio.tld
_RE_CANDIDATA = re.compile(r"(?<![\w.%+-])[\w.%+-]+@[\w.-]+")
_RE_PARECE    = re.compile(r"@|arroba", re.I)

_LETRAS = {
    "a": "a", "b": "be", "c": "ce", "d": "de", "e": "e", "f": "efe", "g": "ge", "h": "hache",
    "i": "i", "j": "jota", "k": "ka", "l": "ele", "m": "eme", "n": "ene", "o": "o", "p": "pe",
    "q": "cu", "r": "erre", "s": "ese", "t": "te", "u": "u", "v": "uve", "w": "uve doble",
    "x": "equis", "y": "i griega", "z": "zeta",
    "@": "arroba", ".": "punto", "-": "guion", "_": "guion bajo", "+": "más", "%": "por ciento",
}


def normalizar(direccion):
    return direccion.strip().lower() if isinstance(direccion, str) else ""


def es_valida(direccion):
    """True si tiene formato usuario@dominio.tld (sin espacios, puntos dobles ni saltos)."""
    d = normalizar(direccion)
    return bool(d) and len(d) <= MAX_LONGITUD and ".." not in d and _RE_EMAIL.fullmatch(d) is not None


def parece_dictada(texto):
    """True si el texto contiene una dirección (válida o no): '@' o 'arroba'."""
    return bool(texto) and _RE_PARECE.search(texto) is not None


def dictada(texto):
    """Primera dirección con formato válido que aparece en el texto transcrito, o None.
    Admite 'arroba', 'punto', 'guion' y 'guion bajo' dichos en voz, también pegados
    ('pepegarciaarrobagmail.com')."""
    if not parece_dictada(texto):
        return None
    t = texto.lower()
    t = re.sub(r"[.,;:!?¿¡]+(?=\s|$)", " ", t)  # puntuación de frase, no de la dirección
    t = re.sub(r"\s*(?:arroba|@)\s*", "@", t)
    t = re.sub(r"\s*(?:\b(?:gui[oó]n|barra) bajo\b|\bbarra baja\b)\s*", "_", t)
    t = re.sub(r"\s*\bgui[oó]n\b\s*", "-", t)
    t = re.sub(r"\s*\bpunto\b\s*", ".", t)
    for candidata in _RE_CANDIDATA.findall(t):
        candidata = candidata.strip("._-")
        if es_valida(candidata):
            return candidata
    return None


_RE_OCULTAR_LITERAL = re.compile(r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)*")
_SEP_DICHO = r"(?:punto|gui[oó]n(?: bajo)?)"
_RE_OCULTAR_DICHA   = re.compile(  # [palabra (punto palabra)*] arroba palabra (punto palabra)*
    rf"(?:\S+\s+(?:{_SEP_DICHO}\s+\S+\s+)*)?\S*arroba\S*(?:\s+\S+)?(?:\s+{_SEP_DICHO}\s+\S+)*", re.I)
OCULTA = "<email>"


CONTACTO_OCULTO = "<contacto>"
# Nombres de contactos resueltos en esta ejecución (solo en memoria): ocultar() los cambia por
# <contacto>, así que tampoco llegan a la consola, a jarvis.log ni a lo que se guarda en disco
_nombres_ocultos = set()


def ocultar_nombre(nombre):
    """Registra un nombre de contacto (de 3 o más letras) para que nunca salga en logs."""
    n = " ".join(str(nombre or "").split())
    if len(n) >= 3:
        _nombres_ocultos.add(n.lower())


def _ocultar_nombres(texto):
    for n in sorted(_nombres_ocultos, key=len, reverse=True):
        texto = re.sub(rf"(?<!\w){re.escape(n)}(?!\w)", CONTACTO_OCULTO, texto, flags=re.I)
    return texto


def ocultar(texto):
    """Texto para consola y logs con cada dirección (escrita o dictada) sustituida por <email>
    y cada nombre de contacto resuelto por <contacto>."""
    if not isinstance(texto, str):
        return texto
    if _nombres_ocultos:
        texto = _ocultar_nombres(texto)
    if not parece_dictada(texto):
        return texto
    return _RE_OCULTAR_DICHA.sub(OCULTA, _RE_OCULTAR_LITERAL.sub(OCULTA, texto))


def redactar(texto):
    """Lo que se guarda en disco: secretos enmascarados (skills.secretos) y cada
    dirección, escrita o dictada, como <email>."""
    if not isinstance(texto, str):
        return texto
    from skills.secretos import redactar_secretos
    return ocultar(redactar_secretos(texto))


def deletrear(direccion):
    """'pe, e, pe, e, arroba, ge, eme, a, i, ele, punto, ce, o, eme'."""
    return ", ".join(_LETRAS.get(c, c) for c in normalizar(direccion))
