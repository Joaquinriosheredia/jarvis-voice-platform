import webbrowser
import urllib.parse
import subprocess
import os

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