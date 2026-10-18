# Changelog

Todos los cambios desde el commit inicial, agrupados por día y por tipo, del más reciente al más antiguo. El proyecto no publica versiones ni etiquetas: todo está en **Sin publicar**. Cada línea resume un commit de `main`, y `git log` tiene el detalle.

## Sin publicar

### 2026-10-18

**Añadido**

- Asistente de configuración (configurar.py), que instalar.cmd ofrece al final: clave de Anthropic sin mostrarla, validada y en el .env con permisos solo para el usuario

**Corregido**

- Primer arranque solo con la clave de Anthropic: lo no configurado (Gmail, Calendar, Contactos, GitHub) se omite del arranque en silencio, queda en el registro y, al pedirlo, se dice con el comando que lo arregla; sin errores en crudo, sin abrir el navegador de OAuth desde una tool y sin «nada pendiente» en GitHub sin token ni repos

**Documentación**

- Licencia Apache-2.0 (LICENSE) y sección Licencia en el README
- Dependencias con alertas abiertas en SECURITY.md (torch y setuptools, con el motivo)

**Mantenimiento**

- fsspec 2026.6.0
- cryptography 50.0.0
- oauthlib 4.0.0, httplib2 0.32.0 y pyasn1 0.6.4
- urllib3 2.8.0, idna 3.15 y anyio 4.14.2
- Fuera PyAutoGUI y sus dependencias, sin uso en el código

### 2026-10-17

**Añadido**

- Publicación: SECURITY.md con el modelo de amenazas, este CHANGELOG, plantillas de issue y perfil del usuario fuera del código (rutas.json), con rutas de respaldo genéricas
- `prueba_eco.py`, diagnóstico manual de eco y altavoz (solo métricas)
- Resumen sin ruido meta, con términos técnicos, y sin guardar sesiones que solo prueban la memoria
- Modo degradado — frase útil por voz, sin bucles ni reintentos sin límite
- Informe de latencia por etapa (replay.py --latencia) y registro del fallback de Whisper
- `siguiente_racha` pide confirmación por voz en el gate estricto por defecto (7 tools)
- Rutas configurables (rutas.py): entorno > rutas.json > valores actuales de respaldo
- Gate estricto activado por defecto para 6 tools (JARVIS_GATE_ESTRICTO=0 lo desactiva)
- Avisos proactivos v1 de CI en rojo (JARVIS_AVISOS_CI=1, desactivados por defecto)
- Instalador idempotente para Windows (instalar.cmd) e inicio rápido nuevo en el README
- Contactos de Google (buscar_contacto, solo lectura) y envío a un contacto dictado
- Vocabulario propio para Whisper y búsqueda aproximada en recordar
- Gate estricto opcional (JARVIS_GATE_ESTRICTO=1) para tools WRITE/EXTERNAL con efectos
- Anuncio de envío completo y confirmación ligada a un hash del email
- Briefing hablado ampliado (agenda de hoy, CI y PRs de GitHub)
- Memoria entre sesiones v1 (SQLite FTS5, recordar, olvidar y retención)
- Más órdenes de silencio por voz («calla», «espera», «déjame hablar»...)

**Corregido**

- La suite de inyección borra sus repos temporales en Windows
- Enmascarar secretos en la salida de correo, buscar_en_web y calendario
- Clave falsa del anuncio generada en ejecución (gitleaks)
- «¿te acuerdas de...?» llama siempre a recordar, guardado robusto de la sesión y causa del fallo del tiempo
- `ver_pantalla` determinista (el sistema pide y confirma la captura)

**Tests**

- Evals adversariales de inyección indirecta (arnés con adversario que obedece todo)

**CI**

- Gitleaks exime solo el valor exacto de la clave falsa de test_secretos.py

**Documentación**

- Efecto real de siguiente_racha (push a un repo público) en la tabla del gate estricto

### 2026-10-16

**Añadido**

- Estado real de la autorización de Google, reautorizar.py y limpieza de logs
- Órdenes de silencio y de apagado separadas, y variantes de la confirmación
- `doctor.py` para diagnosticar el entorno (dependencias, credenciales, audio, Ollama)
- `buscar_en_navegador` (google, amazon, youtube, wikipedia, maps, github)
- Enmascarar secretos en las tools de lectura de código

**Corregido**

- El historial de memoria.json no guarda direcciones ni turnos privados
- Interrumpir a JARVIS con «para», «cállate»... mientras habla o genera
- Enviar correo a «mi propia dirección» sin preguntarla y con la causa real si falla
- El texto OK del doctor dice credentials*.json, como la regla de .gitignore
- Enmascarar siempre la contraseña de URLs con credenciales
- `ver_pantalla` se llama directamente, review sin secretos y evals vivos

**Tests**

- Claves falsas de ElevenLabs sin forma de clave en test_doctor (gitleaks)

**CI**

- Actualizar actions para evitar la deprecación de Node 20
- GitHub Actions con tests con mocks y badge en el README

**Documentación**

- Demo en GIF de la confirmación por voz en el README

**Mantenimiento**

- .gitignore cubre token*.json y credentials*.json

### 2026-10-15

**Añadido**

- Code review por voz (ruta review, resumen hablado + detalle en pantalla)
- Visión por voz (ver_pantalla) con confirmación y turno privado
- Streaming de Claude (pantalla en vivo) + TTS por frases
- Fecha real en cada turno + umbral de confianza de Whisper
- Observabilidad de turnos (métricas, coste, replay, UI) + gasto_hoy
- ElevenLabs TTS opcional con fallback a Piper
- Google Calendar (ver_agenda, crear_evento) + router "qué tengo"
- Evals suite 27/27 + router fixes (para, silencio, deadlock, meta)
- `ejecutar_tests` (Fase 6 completa) + prompt caching
- Fase 6 - git_diff, git_log, buscar_en_codigo

**Corregido**

- JARVIS dice 'Te muestro el contenido en pantalla' para archivos largos
- Mostrar archivos y código sin resumir + renderizado código en Electron

**Documentación**

- README actualizado (calendar, visión, streaming, métricas, Whisper)
- README con arquitectura, decisiones de diseño y seguridad

**Mantenimiento**

- Ignorar normalización EOL en git blame
- Normalizar fin de línea a LF con .gitattributes

### 2026-10-14

**Añadido**

- Fase 6 - tools de ingeniería (leer_archivo, git_estado, listar_proyectos)

### 2026-10-06

**Añadido**

- Contexto conversacional entre turnos + seguimiento va a Claude
- Busqueda web Wikipedia -> DuckDuckGo -> Tavily
- Lanzador de escritorio con icono, bandeja del sistema y acceso directo
- App Electron con chat en tiempo real via WebSocket

**Corregido**

- Filtro de alucinaciones de Whisper (Suscribete y silencio)
- Descripción buscar_en_web restaura noticias y elimina límite ambiguo
- Descripción buscar_en_web refleja Wikipedia + Tavily
- Prompt buscar_en_web activa Wikipedia para preguntas académicas
- Microfono por nombre (Blue Yeti) e initial_prompt en Whisper

### 2026-10-02

**Añadido**

- Whisper en GPU RTX 4060 + qwen2.5:3b para router local
- `enviar_email` con adjunto, gate DESTRUCTIVE y protección anti-inyección
- Modo desarrollador con router dev + fix falsos positivos
- Router 3 capas local/cloud + atajos sin LLM
- Resumen diario de emails en el saludo de arranque
- `borrar_multiples_emails` seguro con firma por msg_ids y confirmación

**Corregido**

- Control de volumen con API nueva de pycaw + redondeo correcto

**Rendimiento**

- `localhost` → `127.0.0.1` elimina delay IPv6 en Ollama (-2s por llamada)
- Whisper en GPU (torch 2.11.0+cu128)

### 2026-09-29

**Añadido**

- `borrar_email` seguro con firma por msg_id, frase propia y caché de 5 min

**Corregido**

- VAD dinámico con música (umbral x3 si VLC tiene sesión de audio activa)

### 2026-09-28

**Añadido**

- JARVIS Voice Platform - agente con gate de permisos, contrato estructurado y 68 tests
