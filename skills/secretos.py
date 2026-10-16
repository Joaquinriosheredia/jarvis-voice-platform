"""Enmascarado determinista de secretos en texto (salida de las tools de lectura de código).

Puro y sin dependencias: redactar(texto) -> (texto, n) sustituye cada secreto por
[SECRETO-REDACTADO:<tipo>] conservando el resto de la línea, el nombre de la variable
y el contexto. Idempotente: un marcador nunca se vuelve a enmascarar.
"""
import re

MARCADOR = "[SECRETO-REDACTADO:{}]"
MIN_VALOR = 8

# Clave privada completa, o hasta el final del texto si llega cortada
_RE_PRIVADA = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----.*?"
                         r"(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|\Z)", re.S)
# esquema://usuario:clave@host → solo la clave
_RE_URL = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@\"'<>]*:)([^\s@/\"'<>]+)(?=@)")
# Authorization: Bearer <valor> / Basic <valor>
_RE_CABECERA = re.compile(r"\b(Bearer|Basic)(\s+)(?=[A-Za-z0-9._~+/-]*[0-9=])([A-Za-z0-9._~+/=-]{8,})", re.I)
# Claves con prefijo conocido: (tipo, patrón)
_PREFIJOS = [
    ("jwt",    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")),
    ("sk",     re.compile(r"\bsk-(?=[A-Za-z_-]*\d)[A-Za-z0-9_-]{16,}")),
    ("aws",    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github", re.compile(r"\b(?:gh[poshur]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("slack",  re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("gitlab", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
]
# nombre sospechoso = "literal" | nombre = valor_sin_comillas (solo el valor se enmascara)
_NOMBRE = (r"(?<![\w.\[-])(?P<nombre>[\w.-]*?(?P<clave>api[_-]?key|client[_-]?secret|private[_-]?key"
           r"|secret|token|passw(?:or)?d|pwd|authorization)[\w.-]*)")
_RE_ASIGNACION = re.compile(
    _NOMBRE + r"(?P<sep>[\"']?\s*(?::=|=>|=|:)\s*)"
    r"(?:(?P<q>[\"'])(?P<vq>[^\"'\n]*)(?P=q)|(?P<v>[A-Za-z0-9_\-+/=.~]+)(?![(\[]))", re.I)

_TIPO_NOMBRE = {"apikey": "api_key", "clientsecret": "client_secret", "privatekey": "private_key",
                "passwd": "password", "pwd": "password"}
_MARCADORES = {"changeme", "change_me", "changeit", "password", "contraseña", "secret", "example",
               "placeholder", "dummy", "redacted", "your_api_key", "your-api-key", "none", "null",
               "undefined", "todo", "fixme"}
_RE_ENV_NOMBRE  = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
_RE_SNAKE       = re.compile(r"[a-z]+(?:_[a-z]+)+")
_RE_ATRIBUTO    = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_RE_ARCHIVO     = re.compile(r"[\w.\\/:~-]*\.(?:json|txt|pem|key|ya?ml|env|py|cfg|ini|toml|log|csv)", re.I)


def _es_marcador(valor):
    """True si el valor es claramente un marcador o una referencia, no un secreto."""
    v = valor.strip()
    bajo = v.lower()
    if len(v) < MIN_VALOR or "SECRETO-REDACTADO" in v:
        return True
    if bajo in _MARCADORES or len(set(bajo)) == 1:            # xxxxxxxx, ********
        return True
    if v.startswith(("<", "$", "%", "{{")) or "${" in v or "{{" in v:
        return True
    if any(r in bajo for r in ("os.environ", "getenv", "process.env", "environ[")):
        return True
    return False


def _ya_redactado(valor):
    return "SECRETO-REDACTADO" in valor


def _sub(patron, tipo, texto, cuenta, grupo=0, omitir=_es_marcador):
    def reemplazo(m):
        if grupo and omitir(m.group(grupo)):
            return m.group(0)
        cuenta[0] += 1
        if not grupo:
            return MARCADOR.format(tipo)
        ini, fin = m.start(grupo) - m.start(), m.end(grupo) - m.start()
        return m.group(0)[:ini] + MARCADOR.format(tipo) + m.group(0)[fin:]
    return patron.sub(reemplazo, texto)


def _asignacion(m, cuenta):
    tipo = re.sub(r"[_-]", "", m.group("clave").lower())
    tipo = _TIPO_NOMBRE.get(tipo, m.group("clave").lower().replace("-", "_"))
    if m.group("q"):
        valor = m.group("vq")
        if (_es_marcador(valor) or any(c.isspace() for c in valor) or valor.startswith(("http://", "https://"))
                or _RE_ENV_NOMBRE.fullmatch(valor) or _RE_SNAKE.fullmatch(valor) or _RE_ARCHIVO.fullmatch(valor)):
            return m.group(0)
        cuenta[0] += 1
        return f"{m.group('nombre')}{m.group('sep')}{m.group('q')}{MARCADOR.format(tipo)}{m.group('q')}"
    valor = m.group("v")
    # Sin comillas: solo si parece un literal (letras y dígitos), nunca un nombre de variable
    if (_es_marcador(valor) or not re.search(r"\d", valor) or not re.search(r"[A-Za-z]", valor)
            or _RE_ATRIBUTO.fullmatch(valor) or _RE_ENV_NOMBRE.fullmatch(valor) or _RE_ARCHIVO.fullmatch(valor)):
        return m.group(0)
    cuenta[0] += 1
    return f"{m.group('nombre')}{m.group('sep')}{MARCADOR.format(tipo)}"


def redactar(texto):
    """(texto con los secretos enmascarados, número de secretos enmascarados)."""
    if not texto:
        return texto, 0
    cuenta = [0]

    def privada(m):
        cuenta[0] += 1
        return MARCADOR.format("clave_privada") + "\n" * m.group(0).count("\n")  # mismas líneas
    t = _RE_PRIVADA.sub(privada, texto)
    # Clave de una URL con credenciales: siempre, sin mínimo de longitud ni marcadores
    t = _sub(_RE_URL, "url", t, cuenta, grupo=2, omitir=_ya_redactado)
    t = _cabecera(t, cuenta)
    for tipo, patron in _PREFIJOS:
        t = _sub(patron, tipo, t, cuenta)
    t = _RE_ASIGNACION.sub(lambda m: _asignacion(m, cuenta), t)
    return t, cuenta[0]


def _cabecera(texto, cuenta):
    def reemplazo(m):
        if _es_marcador(m.group(3)):
            return m.group(0)
        cuenta[0] += 1
        return m.group(1) + m.group(2) + MARCADOR.format(m.group(1).lower())
    return _RE_CABECERA.sub(reemplazo, texto)


def redactar_secretos(texto: str) -> str:
    return redactar(texto)[0]


def aviso(n):
    """Línea final para la salida de una tool; vacía si no se enmascaró nada."""
    return f"\n[{n} secreto(s) enmascarado(s) en esta salida]" if n > 0 else ""
