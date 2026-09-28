import shutil
import subprocess
import os
from datetime import datetime
import requests as req

# ── DISCO ─────────────────────────────────────────────────────────
def espacio_disco():
    total, usado, libre = shutil.disk_usage("C:\\")
    libre_gb = libre // (2**30)
    total_gb = total // (2**30)
    return f"Disco C con {libre_gb} gigas libres de {total_gb} en total"

# ── GPU ───────────────────────────────────────────────────────────
def estado_gpu():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            partes = result.stdout.strip().split(", ")
            temp   = partes[0].strip()
            usado  = partes[1].strip()
            total  = partes[2].strip()
            return f"GPU a {temp} grados con {usado} de {total} megas de VRAM"
    except Exception:
        pass
    return "GPU no disponible"

# ── OLLAMA ────────────────────────────────────────────────────────
def estado_ollama():
    try:
        r = req.get("http://localhost:11434/api/tags", timeout=3)
        if r.status_code == 200:
            modelos = [m["name"] for m in r.json().get("models", [])]
            return f"Ollama activo con {len(modelos)} modelos cargados"
    except Exception:
        pass
    return "Ollama no responde"

# ── HORA Y FECHA ──────────────────────────────────────────────────
def hora_actual():
    return f"Son las {datetime.now().strftime('%H:%M')}"

def fecha_actual():
    dias   = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
    meses  = ["enero","febrero","marzo","abril","mayo","junio",
               "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    ahora  = datetime.now()
    dia    = dias[ahora.weekday()]
    mes    = meses[ahora.month - 1]
    return f"Hoy es {dia} {ahora.day} de {mes} de {ahora.year}"

# ── DIRECCIÓN DEL VIENTO ──────────────────────────────────────────
def _direccion_viento(grados):
    """Convierte grados a punto cardinal en español."""
    puntos = [
        (0,   "norte"),
        (22,  "nor-noreste"),
        (45,  "noreste"),
        (67,  "este-noreste"),
        (90,  "levante"),       # Este
        (112, "este-sureste"),
        (135, "sureste"),
        (157, "sur-sureste"),
        (180, "sur"),
        (202, "sur-suroeste"),
        (225, "suroeste"),
        (247, "oeste-suroeste"),
        (270, "poniente"),      # Oeste
        (292, "oeste-noroeste"),
        (315, "noroeste"),
        (337, "nor-noroeste"),
        (360, "norte"),
    ]
    grados = grados % 360
    for i in range(len(puntos) - 1):
        mitad = (puntos[i][0] + puntos[i+1][0]) / 2
        if grados < mitad:
            return puntos[i][1]
    return "norte"

# ── TIEMPO METEOROLÓGICO — LOS CORRALES ──────────────────────────
def tiempo_sevilla():
    """Tiempo real de Los Corrales (Sevilla) — coordenadas 37.1099, -5.0147"""
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=37.1099&longitude=-5.0147"
            "&current_weather=true"
            "&current_weather_units=kmh"
            "&forecast_days=1"
        )
        r = req.get(url, timeout=5)
        if r.status_code == 200:
            datos      = r.json()["current_weather"]
            temp       = datos["temperature"]
            viento     = datos["windspeed"]
            direccion  = datos.get("winddirection", None)
            code       = datos.get("weathercode", 0)

            # WMO Weather Code → descripción natural
            if code == 0:
                desc = "cielo despejado"
            elif code in (1, 2):
                desc = "parcialmente nublado"
            elif code == 3:
                desc = "cielo cubierto"
            elif code in (45, 48):
                desc = "hay niebla"
            elif code in (51, 53, 55):
                desc = "llovizna ligera"
            elif code in (61, 63):
                desc = "está lloviendo"
            elif code == 65:
                desc = "lluvia intensa"
            elif code in (71, 73, 75):
                desc = "está nevando"
            elif code in (80, 81, 82):
                desc = "chubascos"
            elif code in (95, 96, 99):
                desc = "tormenta eléctrica"
            else:
                desc = "condiciones variables"

            if direccion is not None:
                dir_texto = _direccion_viento(direccion)
                viento_str = f"viento de {dir_texto} a {viento} kilómetros por hora"
            else:
                viento_str = f"viento de {viento} kilómetros por hora"

            return (
                f"En Los Corrales hay {temp} grados, {desc} "
                f"y {viento_str}"
            )
    except Exception:
        pass
    return "No puedo obtener el tiempo ahora mismo"

# ── ESTADO COMPLETO ───────────────────────────────────────────────
def estado_completo():
    return f"{hora_actual()}. {espacio_disco()}. {estado_gpu()}. {estado_ollama()}."

# ── APAGAR / REINICIAR ────────────────────────────────────────────
def apagar(minutos=1):
    os.system(f"shutdown /s /t {minutos * 60}")
    return f"El ordenador se apagará en {minutos} minutos"

def cancelar_apagado():
    os.system("shutdown /a")
    return "Apagado cancelado"

# ── CARPETAS ──────────────────────────────────────────────────────
def crear_carpeta(nombre, ubicacion="Desktop"):
    ruta = os.path.join(os.path.expanduser("~"), ubicacion, nombre)
    os.makedirs(ruta, exist_ok=True)
    return f"Carpeta {nombre} creada en {ubicacion}"

# ── BUSCAR ARCHIVOS ───────────────────────────────────────────────
def buscar_archivo(nombre, raiz="C:\\Users"):
    encontrados = []
    try:
        for root, dirs, files in os.walk(raiz):
            dirs[:] = [d for d in dirs if d not in
                       ["Windows", "Program Files", "$Recycle.Bin", "AppData"]]
            for f in files:
                if nombre.lower() in f.lower():
                    encontrados.append(os.path.join(root, f))
                    if len(encontrados) >= 5:
                        return encontrados
    except Exception:
        pass
    return encontrados