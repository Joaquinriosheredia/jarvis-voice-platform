import webbrowser
import urllib.parse
import urllib.request
import json
import subprocess
import os
import requests

try:
    from tavily import TavilyClient
except ImportError:  # sin el paquete, la búsqueda sigue funcionando solo con DuckDuckGo
    TavilyClient = None

# ── URLS CONOCIDAS ────────────────────────────────────────────────
URLS = {
    "github":    "https://github.com/Joaquinriosheredia/DAM-Java-Mastery",
    "linkedin":  "https://www.linkedin.com",
    "portfolio": "https://joaquinriosheredia.github.io/DAM-Java-Mastery/",
    "malt":      "https://www.malt.es",
    "workana":   "https://www.workana.com",
    "gmail":     "https://mail.google.com",
    "aws":       "https://console.aws.amazon.com",
    "youtube":   "https://www.youtube.com",
    "claude":    "https://claude.ai",
    "google":    "https://www.google.com",
}

# ── APPS PORTABLES ────────────────────────────────────────────────
APPS = {
    "chrome":      os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
    "vscode":      os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
    "notepad":     "notepad.exe",
    "explorador":  "explorer.exe",
    "terminal":    "wt.exe",
    "powershell":  "powershell.exe",
    "android":     os.path.expandvars(r"%PROGRAMFILES%\Android\Android Studio\bin\studio64.exe"),
    "intellij":    os.path.expandvars(r"%LOCALAPPDATA%\JetBrains\Toolbox\apps\IDEA-U\ch-0\bin\idea64.exe"),
}

# ── MATCHING PRECISO ──────────────────────────────────────────────
def _match(nombre, opciones):
    nombre = nombre.lower().strip()
    # Coincidencia exacta primero
    for key in opciones:
        if nombre == key:
            return key
    # Luego por palabra completa
    palabras = nombre.split()
    for key in opciones:
        if key in palabras:
            return key
    # Finalmente por contenido
    for key in opciones:
        if key in nombre:
            return key
    return None

# ── ABRIR URL ─────────────────────────────────────────────────────
def abrir_url(nombre):
    key = _match(nombre, URLS)
    if key:
        webbrowser.open(URLS[key])
        return f"Abriendo {key}"
    return None

# ── ABRIR APP ─────────────────────────────────────────────────────
def abrir_app(nombre):
    key = _match(nombre, APPS)
    if key:
        try:
            subprocess.Popen([APPS[key]])
            return f"Abriendo {key}"
        except Exception as e:
            return f"No pude abrir {key}: {e}"
    return None

# ── YOUTUBE ───────────────────────────────────────────────────────
def es_peticion_youtube(texto):
    return any(p in texto for p in [
        "youtube", "video", "vídeo", "ver un video", "pon un video"
    ])

def extraer_busqueda_youtube(texto):
    texto = texto.lower()
    for t in ["youtube", "busca", "buscar", "video", "vídeo", "ver", "pon"]:
        if t in texto:
            texto = texto.split(t, 1)[-1]
    return texto.strip().strip(".,!¡¿?")

def youtube_buscar(busqueda):
    if not busqueda:
        busqueda = "lofi hip hop"
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(busqueda)}"
    webbrowser.open(url)
    return f"Buscando en YouTube: {busqueda}"

# ── GOOGLE FALLBACK ───────────────────────────────────────────────
def buscar_google(query):
    url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
    webbrowser.open(url)
    return f"Buscando en Google: {query}"

# ── BÚSQUEDA WEB (tool buscar_en_web) ─────────────────────────────
PREGUNTAS_ACADEMICAS = (
    "qué es", "quién es", "qué fue", "cuándo nació", "cuándo murió",
    "define", "definición", "significado", "historia de", "biografía",
    "inventor", "descubridor",
)
_RELLENO = {"de", "del", "el", "la", "los", "las", "un", "una"}

def _es_pregunta_academica(query):
    q = query.lower()
    return any(p in q for p in PREGUNTAS_ACADEMICAS)

def _tema_academico(query):
    """Título probable del artículo: "cuándo nació Cervantes" → "Cervantes".
    La API de Wikipedia busca por título exacto; la pregunta entera da 404."""
    q = query.lower()
    for p in PREGUNTAS_ACADEMICAS:
        i = q.find(p)
        if i != -1:
            query = query[i + len(p):]
            break
    palabras = query.strip(" ¿?¡!.,:;\"'").split()
    while palabras and palabras[0].lower() in _RELLENO:
        palabras.pop(0)
    return " ".join(palabras)

def _buscar_wikipedia(query):
    """Resumen del artículo de es.wikipedia (máx. 400 caracteres), o None
    si no existe, es desambiguación o falla. Usa requests (certifi): urllib
    con el almacén de Windows no valida el certificado de Wikipedia."""
    try:
        if not query:
            return None
        titulo = urllib.parse.quote(query.replace(" ", "_"))
        r = requests.get(f"https://es.wikipedia.org/api/rest_v1/page/summary/{titulo}",
                         headers={'User-Agent': 'Jarvis/1.0'}, timeout=5)
        if r.status_code != 200:
            return None
        datos = r.json()
        if datos.get('type') == 'disambiguation':
            return None
        return (datos.get('extract') or '').strip()[:400] or None
    except Exception:
        return None

def _buscar_ddg(query):
    """Instant Answer de DuckDuckGo. Texto, o None si vacío o falla."""
    try:
        q   = urllib.parse.quote(query)
        url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, headers={'User-Agent': 'Jarvis/1.0'})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
        if data.get('AbstractText'):
            return data['AbstractText'][:500]
        textos = [r['Text'] for r in data.get('RelatedTopics', [])[:3]
                  if isinstance(r, dict) and r.get('Text')]
        return ' | '.join(textos)[:500] if textos else None
    except Exception:
        return None

def _buscar_tavily(query):
    """Máximo 3 resultados de Tavily: solo título + snippet + url.
    None si no hay clave, no hay resultados o falla."""
    try:
        api_key = os.environ.get("TAVILY_API_KEY")
        if TavilyClient is None or not api_key:
            return None
        datos = TavilyClient(api_key=api_key).search(query, max_results=3, timeout=10)
        lineas = []
        for r in (datos.get('results') or [])[:3]:
            titulo  = (r.get('title') or '').strip()
            snippet = (r.get('content') or '').strip()[:300]
            url     = (r.get('url') or '').strip()
            if titulo or snippet:
                lineas.append(f"{titulo}: {snippet} ({url})")
        return '\n'.join(lineas) or None
    except Exception:
        return None

def buscar_en_web(query):
    """Pregunta académica → Wikipedia; después DuckDuckGo; si vacío o falla,
    Tavily; si no, sin resultados."""
    if _es_pregunta_academica(query):
        resultado = _buscar_wikipedia(_tema_academico(query))
        if resultado:
            print("[WEB] wikipedia OK")
            return resultado
    resultado = _buscar_ddg(query)
    if resultado:
        print("[WEB] ddg")
        return resultado
    resultado = _buscar_tavily(query)
    if resultado:
        print("[WEB] tavily")
        return resultado
    print("[WEB] sin resultados")
    return f"Sin resultados para: {query}"

# ── MANEJADOR UNIFICADO ───────────────────────────────────────────
def manejar_web(texto):
    texto_lower = texto.lower()

    # Intentar URL conocida
    resultado = abrir_url(texto_lower)
    if resultado:
        return resultado

    # Intentar app
    resultado = abrir_app(texto_lower)
    if resultado:
        return resultado

    # YouTube
    if es_peticion_youtube(texto_lower):
        busqueda = extraer_busqueda_youtube(texto_lower)
        return youtube_buscar(busqueda)

    # Fallback Google
    return buscar_google(texto)

# ── DETECTAR INTENCIÓN ────────────────────────────────────────────
def es_peticion_web(texto_lower):
    return any(p in texto_lower for p in [
        "abre", "entra en", "ve a", "abre la web",
        "abre el navegador", "abre la página", "abre la pagina"
    ])