import subprocess
import threading

VLC_PATH       = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
musica_proceso = None
lock           = threading.Lock()
ultima_busqueda = None

# ── PRINT SEGURO (consolas cp1252) ────────────────────────────────
def _print_seguro(texto):
    try:
        print(texto)
    except UnicodeEncodeError:
        try:
            print(texto.encode('ascii', errors='replace').decode('ascii'))
        except Exception:
            pass
    except Exception:
        pass

# ── EXTRAER BÚSQUEDA ──────────────────────────────────────────────
def extraer_busqueda(texto):
    texto = texto.lower()
    triggers = ["pon música de", "pon musica de", "ponme música de",
                "ponme musica de", "reproduce", "quiero escuchar",
                "quiero oir", "quiero oír", "pon música", "pon musica",
                "ponme", "pon"]
    for t in triggers:
        if t in texto:
            texto = texto.split(t, 1)[-1]
            break
    busqueda = texto.strip().strip(".,!¡¿?")
    return busqueda if busqueda else "lofi hip hop"

# ── DETECTAR PETICIÓN MUSICAL ─────────────────────────────────────
def es_peticion_musical(texto_lower):
    palabras = [
        "pon musica", "pon música", "ponme musica", "ponme música",
        "reproduce", "quiero escuchar", "quiero oir", "quiero oír",
        "pon una cancion", "pon la cancion", "pon una canción",
        "musica de", "música de", "cancion de", "canción de",
        "pon algo de", "me pones", "me puedes poner"
    ]
    return any(p in texto_lower for p in palabras)

# ── REPRODUCIR ────────────────────────────────────────────────────
def reproducir(busqueda):
    global musica_proceso, ultima_busqueda

    def _play():
        global musica_proceso, ultima_busqueda
        try:
            _print_seguro(f"🎵 Buscando: {busqueda}")

            with lock:
                if musica_proceso:
                    musica_proceso.kill()
                    musica_proceso = None

            result = subprocess.run(
                ["yt-dlp", f"ytsearch1:{busqueda}",
                 "--get-url", "--format", "bestaudio"],
                capture_output=True, text=True, timeout=30
            )

            if result.returncode != 0 or not result.stdout.strip():
                _print_seguro("❌ No se encontró audio")
                return

            url = result.stdout.strip().split('\n')[0]
            if not url.startswith("http"):
                _print_seguro("❌ URL inválida")
                return

            with lock:
                musica_proceso = subprocess.Popen(
                    [VLC_PATH,
                     "--no-one-instance",
                     "--no-video",
                     "--play-and-exit", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                ultima_busqueda = busqueda

            _print_seguro(f"🎵 Reproduciendo: {busqueda}")

        except Exception as e:
            _print_seguro(f"❌ Error música: {e}")

    threading.Thread(target=_play, daemon=True).start()

# ── PARAR ─────────────────────────────────────────────────────────
def parar():
    global musica_proceso
    with lock:
        if musica_proceso:
            try:
                musica_proceso.kill()
            except Exception:
                pass
            musica_proceso = None
    subprocess.run(
        ["taskkill", "/f", "/im", "vlc.exe"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

# ── REPETIR ÚLTIMA ────────────────────────────────────────────────
def repetir_ultima():
    if ultima_busqueda:
        reproducir(ultima_busqueda)
        return f"Reproduciendo de nuevo: {ultima_busqueda}"
    return "No hay ninguna canción anterior"

# ── CLEANUP ───────────────────────────────────────────────────────
def cleanup():
    parar()