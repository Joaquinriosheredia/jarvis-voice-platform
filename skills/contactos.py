"""Google Contactos (People API), solo lectura: buscar un contacto por nombre.

Token propio, token_contacts.json, con el scope contacts.readonly: añadir el scope a otro
token rompería su refresco (como Calendar). Se autoriza con:
    venv\\Scripts\\python.exe reautorizar.py contacts

Nada de aquí se guarda: ni en la memoria entre sesiones, ni en el historial, ni en logs.
buscar() devuelve los datos a quien llama y no imprime nada.
"""
import threading

from googleapiclient.discovery import build

import rutas

from skills import direccion, gmail, google_auth

SCOPES_CONTACTOS = ["https://www.googleapis.com/auth/contacts.readonly"]
TOKEN_CONTACTOS  = rutas.en_secretos("token_contacts.json")
MAX_RESULTADOS   = 10
MAX_NOMBRE       = 80

_calentado = False
_lock = threading.Lock()


def _servicio():
    return build("people", "v1", credentials=gmail.credenciales(SCOPES_CONTACTOS, TOKEN_CONTACTOS, "contacts"),
                 cache_discovery=False)


def _calentar(servicio):
    """searchContacts necesita una petición vacía antes de la primera búsqueda (caché de Google)."""
    global _calentado
    with _lock:
        if not _calentado:
            servicio.people().searchContacts(query="", readMask="names").execute()
            _calentado = True


def buscar(nombre):
    """(True, [{"nombre", "email", "tipo"}]) con una entrada por dirección válida, o (False, motivo).
    El motivo nunca lleva la excepción original (puede incluir la petición)."""
    nombre = " ".join(str(nombre or "").split())[:MAX_NOMBRE]
    if not nombre:
        return False, "Falta el nombre del contacto."
    try:
        servicio = _servicio()
        _calentar(servicio)
        res = servicio.people().searchContacts(query=nombre, readMask="names,emailAddresses",
                                               pageSize=MAX_RESULTADOS).execute()
    except google_auth.NoConfigurado as e:
        return False, e.frase
    except Exception as e:
        return False, f"No se pudo consultar Google Contactos ({type(e).__name__})."
    encontrados = []
    for r in res.get("results") or []:
        persona = r.get("person") or {}
        nombres = persona.get("names") or [{}]
        visible = (nombres[0].get("displayName") or "").strip() or "(sin nombre)"
        for e in persona.get("emailAddresses") or []:
            valor = direccion.normalizar(e.get("value"))
            if direccion.es_valida(valor) and all(x["email"] != valor for x in encontrados):
                encontrados.append({"nombre": visible, "email": valor, "tipo": (e.get("type") or "").lower()})
    return True, encontrados


def etiqueta(contacto):
    """Nombre para distinguir contactos sin decir la dirección: "Ana García (trabajo)"."""
    tipos = {"home": "personal", "work": "trabajo", "other": "otra"}
    tipo = tipos.get(contacto.get("tipo"), contacto.get("tipo"))
    return f"{contacto['nombre']} ({tipo})" if tipo else contacto["nombre"]
