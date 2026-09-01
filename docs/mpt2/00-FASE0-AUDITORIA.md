# Fase 0 — Auditoría técnica de MoneyPrinterTurbo (upstream v1.3.5+)

Fecha: 2026-09-01. Commit auditado: `3ade9fb` (upstream `main`, idéntico al `main` de este fork).
Metodología: lectura del código (no de la documentación), ejecución de la suite de tests,
ejecución headless del pipeline y arranque de la API. Nada de lo que sigue se ha asumido
sin comprobarlo en el código; las referencias `archivo:línea` apuntan al commit anterior.

---

## 1. Ficha del repositorio

| Dato | Valor |
|---|---|
| Upstream | https://github.com/harry0703/MoneyPrinterTurbo |
| Licencia | MIT (LICENSE, © 2024 Harry) |
| Último tag | `v1.3.5` (2026-08-22). `main` lleva 124 commits más (hasta 2026-09-01) |
| Ramas upstream | solo `main` |
| Estado del fork | `xaviblanco98/MoneyPrinterTurbo-2.0` = upstream `main` sin cambios. El clon es *shallow* (146 commits visibles de ~809) |
| Tamaño | ~24.600 líneas Python. `webui/Main.py` 6.559, `voice.py` 2.253, `material.py` 2.073, `task.py` 1.563, `cli.py` 1.550, `video.py` 1.478 |
| Python | `>=3.11` (`.python-version` = 3.11). CI prueba 3.11 y 3.13 en Linux y smoke en Windows |
| Gestión deps | `pyproject.toml` + `uv.lock` (fuente de verdad), `requirements.txt` legado |
| Issues abiertas upstream (2026-09-01) | 7 visibles, todas "enhancement": storyboard (#1256), text-to-image/video local (#1274, #1290), dificultad de instalación (#1280), fuentes árabes (#1205), multi-guion (#1157), docs (#1183) |
| Tests | 865 pasan, 11 saltados (integración con proveedores), 106 s en 4 vCPU. Cobertura de ramas exigida por CI: 70 % |

### Licencia e implicaciones

MIT permite usar, modificar y mantener privado el código sin obligación de publicar cambios.
Única obligación: conservar el aviso de copyright y la licencia en las copias del software.
Para un sistema privado no hay ninguna restricción práctica. Si en el futuro se distribuye
(SaaS no cuenta como distribución bajo MIT), basta con mantener el `LICENSE` original y añadir
el nuestro para el código nuevo.

Atención a las **licencias de recursos**, que no son del código: fuentes en `resource/fonts/`
(Microsoft YaHei y STHeiti son fuentes propietarias de Microsoft/Apple redistribuidas sin
licencia explícita; BeVietnamPro y Charm son OFL) y las 29 pistas de `resource/songs/` no
tienen ninguna atribución ni licencia registrada. **No usar esas pistas ni esas fuentes
propietarias en vídeos publicados** hasta sustituirlas por material con licencia trazable.

---

## 2. Arquitectura actual

MoneyPrinterTurbo es una aplicación monolítica Python con tres puntos de entrada que
comparten un único pipeline síncrono:

```
                 ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
                 │ webui/Main.py│   │   cli.py     │   │  app/asgi.py │
                 │ (Streamlit,  │   │ (argparse,   │   │ (FastAPI,    │
                 │  in-process) │   │  in-process) │   │  threads)    │
                 └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
                        │                  │                  │
                        └──────────────────┼──────────────────┘
                                           ▼
                             app/services/task.py::start()
                                           │
      ┌──────────┬──────────┬──────────────┼──────────────┬──────────────┐
      ▼          ▼          ▼              ▼              ▼              ▼
   llm.py    voice.py   subtitle.py   material.py     video.py      upload_post.py
 (guion +   (TTS 9      (whisper /    (pexels,       (moviepy +    (SaaS de
  términos)  proveed.)   edge cues)    pixabay,       ffmpeg)        terceros)
                                        coverr, IA)
                                           │
                                  state.py (memoria o Redis)
                                  task_artifacts.py (script.json)
                                  storage/tasks/<uuid>/
```

### Flujo actual de una tarea (`task.py:_run_pipeline`, líneas 1243–1519)

```
VideoParams
   │
   ├─ preflight (claves de proveedores de pago, ffmpeg)
   ├─ 1. generate_script   → LLM con prompt fijo, o guion suministrado          (progreso 10)
   ├─ 2. generate_terms    → LLM devuelve 5 (u 8) términos de búsqueda en inglés (progreso 20)
   │       └─ save_script_data → storage/tasks/<id>/script.json
   ├─ 3. generate_audio    → voice.tts() UN solo mp3 para todo el guion          (progreso 30)
   ├─ 4. generate_subtitle → SRT desde word-boundaries de edge-tts o whisper      (progreso 40)
   ├─ 5. get_video_materials → descarga clips hasta cubrir audio_duration         (progreso 50)
   ├─ 6. generate_final_videos
   │       ├─ video.combine_videos → recorta clips de ≤N s, temp-clip-i.mp4, concat ffmpeg
   │       └─ video.generate_video → TextClip por cue + mezcla voz/BGM → final-N.mp4 (progreso 100)
   └─ 7. cross-post opcional (upload-post.com) en un ThreadPoolExecutor aparte
```

Estado: `state.py` guarda el diccionario de la tarea en memoria (se pierde al reiniciar)
o en Redis (hash por `task_id`, valores `str()` y `ast.literal_eval` al leer). No hay base
de datos, no hay reanudación de tareas interrumpidas, no hay historial más allá del
directorio `storage/tasks/`.

Concurrencia: un `threading.Thread` por tarea (`base_manager.py:54`), límite `max_concurrent_tasks`
(5) y cola en memoria o lista Redis. El WebUI fija 1 tarea concurrente porque el pipeline
toma un lock global de configuración.

Configuración: `config.toml` en la raíz (copiado de `config.example.toml`), global mutable.
**No lee variables de entorno** salvo `REDIS_HOST`, `CORS_ALLOWED_ORIGINS` y algunas claves de
TTS. Las claves se escriben en claro en `config.toml` desde el WebUI.

---

## 3. Qué hace bien (reutilizable)

| Componente | Archivo | Veredicto | Notas |
|---|---|---|---|
| Composición de vídeo | `app/services/video.py` | **Reutilizar como núcleo de render** | Estrategia sensata (sub-clips a disco + concat ffmpeg), fit cover/contain, subtítulos con Pillow (no necesita ImageMagick), fallback de códecs HW→libx264, cierre cuidadoso de recursos. Hay que extenderlo, no reescribirlo. |
| Efectos | `app/services/utils/video_effects.py` | Reutilizar | Zoom sub-píxel sin jitter, slide, fade. Duración fija a 1 s (parametrizar). |
| TTS edge-tts | `voice.py:833` (`azure_tts_v1`) | Reutilizar (con reservas) | Gratis, sin clave, devuelve word boundaries reales. Es un endpoint no oficial de Microsoft: puede romperse o bloquearse por región. Hay que tener un TTS local de respaldo. |
| Registro de proveedores LLM | `app/models/llm_provider.py`, `llm.py:_generate_response` | Reutilizar | 20+ proveedores vía SDK OpenAI, Gemini, dashscope, litellm. Ollama sin clave funciona (`llm.py:180`). Detección de contenedor para `host.docker.internal` (`config.py:361`). |
| Subtítulos whisper | `subtitle.py` | Reutilizar | faster-whisper con timestamps por palabra + VAD. Necesario para alinear TTS locales que no dan timings. |
| Búsqueda de stock | `material.py` pexels/pixabay/coverr | Reutilizar | Filtro por aspecto/duración, caché de búsquedas 24 h con claves excluidas, redacción de secretos en logs, rotación de claves. |
| Caché de clips | `cache_manager.py`, `material_cache.py` | Reutilizar | Escritura atómica, locks por shard, sin TTL en clips (añadir). |
| BGM | `bgm.py` | Reutilizar la validación de uploads | Loop, fade-out 3 s. Sin ducking. |
| API | `app/controllers/v1/*`, `asgi.py` | Reutilizar como base | FastAPI limpio, auth por `x-api-key`, streaming con Range, CORS restringido. |
| CLI | `cli.py` | Reutilizar para depurar | Permite ejecutar cualquier tramo del pipeline (`--stop-at`) sin UI. |
| Seguridad de rutas | `app/utils/file_security.py` | Reutilizar | Evita path traversal en materiales/BGM. |
| Tests | `test/` (865) | Mantener verdes | Buena base para no romper el núcleo al extenderlo. |
| Docker | `Dockerfile`, `docker-compose.yml` | Reutilizar con cambios | Imagen `python:3.11-slim-bullseye` + ffmpeg. Los mirrors chinos por defecto hay que desactivarlos (`DOCKER_BUILD_MIRROR=default`). |

## 4. Qué hay que modificar

| Componente | Problema comprobado | Cambio necesario |
|---|---|---|
| `task.py` pipeline | Lineal, un guion → un mp3 → una bolsa de clips. Sin escenas, sin reanudación, sin idempotencia. | No modificar: **envolverlo**. El nuevo orquestador llamará a las funciones de `video.py`/`voice.py` por escena. |
| `video.combine_videos` | No conoce el guion: rellena `audio_duration` con clips de ≤5 s en orden aleatorio o secuencial; si falta material **repite clips** (`video.py:787`). Términos→clips se pierden (`material.py:1708`). | Nueva función de composición que recibe una *timeline* `[(scene_id, asset, start, end, efecto)]`. Reutiliza `_fit_clip_to_canvas`, efectos y concat. |
| Transiciones | `fade-in`/`slide` se aplican **por clip contra negro** con `t=1` fijo: cada corte muestra negro (verificado en el vídeo de referencia, fotograma 30 s). | Transiciones reales entre clips (xfade de ffmpeg) o ninguna. |
| Triple re-encode | temp-clip → concat re-encode → final. 38 s de vídeo tardaron 3 m 53 s en 4 vCPU. | Concat con `-c copy` cuando los temp-clips comparten parámetros; render de subtítulos vía filtro `subtitles`/ASS de ffmpeg en vez de un `TextClip` por cue en memoria. |
| `voice.tts` | Todo el guion en una llamada; sin SSML salvo Azure; sin diccionario de pronunciación; reintentos sin backoff; `siliconflow` sin timeout. | Capa `VoiceAgent` que sintetiza **por escena/frase**, cachea por hash(texto, voz, rate), aplica léxico de pronunciación y concatena. |
| `voice.create_subtitle` | Si el nº de líneas del guion ≠ nº de cues, **no escribe el SRT** (`voice.py:2130`). Solo edge/Azure dan timings reales; el resto reparte la duración por nº de caracteres (`voice.py:604`). | Alinear siempre con faster-whisper cuando el TTS no devuelva timings; fallback por escena, nunca "sin subtítulos". |
| `subtitle.correct` | Lógica muerta (`similarity > 0.8` no cambia nada, `subtitle.py:237`), cues `00:00:00,000` para líneas sobrantes. | Reescribir la alineación (por escena, no por línea global). |
| `material.py` | Sin campo de licencia. `source_info` guarda autor/URL pero no se usa. Pexels exige resolución exacta (`material.py:343`), descarta mucho material válido. Sin reintentos en búsquedas/descargas. Descarga en RAM sin comprobar status HTTP. | Modelo `Asset` con licencia, atribución y proveedor; descargas en streaming con reintentos; filtro por aspecto tolerante (recortable). |
| `llm.py` | Prompts fijos en chino/inglés para "short video"; sin JSON mode; parseo por regex; 5 reintentos inmediatos; devuelve `"Error: ..."` como string. | Nueva capa `LLMClient` con salida estructurada validada por Pydantic, reintentos con backoff, presupuesto de tokens y registro de coste. Reutilizar solo el registro de proveedores y `_generate_response`. |
| `state.py` | Dict en memoria / Redis con `ast.literal_eval`. | Sustituir por SQLite + SQLAlchemy con migraciones. |
| `config.py` | TOML global mutable, secretos en claro, sin env vars. | Config de núcleo por entorno (`.env` + pydantic-settings); configuración editorial por canal en YAML versionado sin secretos. |
| `upload_post.py` | SaaS de terceros que custodia las credenciales de YouTube; fuerza `containsSyntheticMedia=true`. | Sustituir por YouTube Data API v3 oficial con OAuth propio. |
| `webui/Main.py` | 6.559 líneas en un archivo, in-process, orientado a "un vídeo corto ahora". | No extender. Mantener como herramienta de depuración del kernel; construir el panel nuevo aparte. |
| `resource/songs`, `resource/fonts` | Sin licencia trazable. | Sustituir por biblioteca de audio con licencia registrada (YouTube Audio Library, Pixabay Music, CC-BY) y fuentes OFL. |

## 5. Qué falta (gap analysis frente al objetivo)

| Capacidad objetivo | Estado en MPT | Gap |
|---|---|---|
| Configuración por canal (idioma, tono, voz, estilo, reglas) | Inexistente. Hay preferencias globales de UI. | Total |
| Descubrimiento de temas (YouTube, Trends, Reddit, RSS, Wikipedia) | Inexistente | Total |
| Puntuación de oportunidades explicada | Inexistente | Total |
| Investigación con fuentes, dossier, claims trazables | Inexistente. El LLM escribe "de memoria" sin fuentes. | Total |
| Ángulos y hooks puntuados | Inexistente | Total |
| Guion estructurado por secciones, con duración estimada y marcas de verificación | Texto plano (`paragraph_number` solo orienta) | Total |
| Fact-checking que bloquee | Inexistente | Total |
| Storyboard escena a escena con tipo de recurso, término, texto en pantalla, licencia | Inexistente. Solo 5–8 términos globales. Issue upstream #1256 lo pide. | Total (pieza crítica) |
| Recursos con licencia, atribución, dedup semántico, alternativas | Parcial: descarga + `source_info` sin licencia | Alto |
| Gráficos, mapas, timelines, texto animado, imágenes de dominio público | Solo `openai_image` (pago o SD local) e imágenes locales con zoom | Alto |
| Voz por escena, pronunciación, regeneración de frases, consistencia | Un mp3 por vídeo | Alto |
| Edición: timeline por escena, ducking, normalización, shorts editados | Bolsa de clips, volumen fijo, sin loudness | Alto |
| QC automático con informe y puntuación | Inexistente (solo validaciones técnicas dispersas) | Total |
| Títulos, descripción, capítulos, miniaturas | `generate_social_metadata` (título+caption+hashtags para TikTok/IG) | Alto |
| Aprobación humana con historial | Inexistente | Total |
| Publicación oficial YouTube, programación, playlists, cuotas | Vía SaaS upload-post.com | Total (rehacer) |
| Analítica YouTube + histórico + aprendizaje | Inexistente | Total |
| Registro de costes por vídeo/canal/proveedor | Inexistente | Total |
| Persistencia, migraciones, reanudación, idempotencia | Dict en memoria / Redis; `script.json` por tarea | Total |
| Trazabilidad tema→ángulo→hook→guion→escenas→publicación→resultados | Inexistente | Total |

Conclusión: MoneyPrinterTurbo aporta un **kernel de render y de proveedores** sólido
(≈30 % del sistema objetivo, principalmente `video.py`, `voice.py`, `subtitle.py`, `material.py`,
registro LLM, API). Todo lo editorial, el modelo de datos, la orquestación y el cierre del bucle
con YouTube hay que construirlo.

---

## 6. Ejecución del proyecto original

### Entorno de la auditoría
Ubuntu (contenedor), Python 3.11.15, 4 vCPU, 15 GB RAM, sin GPU. `uv sync --frozen` OK.
ffmpeg 6.1.1 instalado con apt. El sandbox bloquea por política de red
`api.pexels.com`, `pixabay.com`, `commons.wikimedia.org`, `speech.platform.bing.com` (edge-tts),
`huggingface.co`, Google/YouTube/Reddit. Por eso el vídeo de referencia usa **material local y modo
sin voz**; en tu máquina esas rutas funcionarán.

### Comando ejecutado (repo sin modificar)
```bash
uv run python cli.py \
  --video-script "$(cat ref_script.txt)" \
  --video-source local --video-materials "clip1.mp4,...,clip5.mp4" \
  --voice-name no-voice --video-aspect 16:9 --video-concat-mode sequential \
  --video-clip-duration 5 --video-transition-mode fade-in \
  --bgm-type random --bgm-volume 0.2 --font-name BeVietnamPro-Bold.ttf --font-size 48 \
  --task-id 00000000-0000-4000-8000-000000000001
```

### Resultado
| Métrica | Valor |
|---|---|
| Salida | `storage/tasks/00000000-0000-4000-8000-000000000001/final-1.mp4` |
| Duración / formato | 38,4 s, 1920×1080, H.264 30 fps, AAC estéreo, 9,3 Mbps, 44,8 MB |
| Subtítulos | 11 cues, SRT generado a partir de timings **estimados** (modo sin voz) |
| Tiempo total | 3 m 53 s de reloj, 7 m 21 s de CPU |
| Desglose | preprocess 2 s · render de 5 sub-clips 41 s · concat ffmpeg 54 s · render final con subtítulos y BGM 2 m 14 s |
| Observaciones | Material insuficiente → repitió 3 clips (aviso en log). `fade-in` por clip produce ~1 s de negro en cada corte. `--video-terms` se ignora con `--video-source local`. El manifiesto `script.json` no guarda licencia de los recursos. |

Segunda pasada (`…0002`, 9:16) con narración generada offline con espeak-ng y pasada como
`--custom-audio-file`: confirma la ruta de audio externo. Con audio externo el proveedor de
subtítulos `edge` no puede generar SRT (necesita whisper, cuya descarga de modelo está bloqueada aquí).

API: `python main.py` arranca en `:8080`; `/ping` → `"pong"`; `openapi.json` expone 13 rutas
(`/api/v1/videos`, `/tasks`, `/scripts`, `/terms`, `/social-metadata`, `/audio`, `/subtitle`,
`/musics`, `/video_materials`, `/stream`, `/download`).

---

## 7. Problemas técnicos y dependencias

### Dependencias fijadas vs. última versión (PyPI, 2026-09-01)
| Paquete | Fijado | Última | Comentario |
|---|---|---|---|
| moviepy | 2.2.1 | 2.2.1 | Al día |
| streamlit | 1.59.1 | 1.63.0 | Menor |
| fastapi | 0.136.3 | 0.141.1 | Menor |
| uvicorn | 0.32.1 | 0.52.4 | Muy atrasado, sin impacto funcional |
| openai | 2.24.0 | 3.6.0 | Major atrasado. Migrar cuando toquemos `llm.py` |
| litellm | 1.86.2 | 1.99.0 | Paquete pesado y volátil; opcional para nosotros |
| faster-whisper | 1.1.0 | 1.2.1 | Actualizar (mejoras de VAD/timestamps) |
| redis | 5.2.0 | 8.1.0 | No lo usaremos en el MVP |
| edge-tts | 7.2.7 | 7.2.8 | Al día |
| google-genai | 2.11.0 | 2.21.0 | Menor |
| pydantic (transitiva) | 2.12.5 | 2.13.5 | Al día |

No hay `torch` en el lock (faster-whisper usa ctranslate2 4.7.1): instalación ligera, whisper
en CPU int8 es viable.

### Problemas detectados en código (los más relevantes para nosotros)
1. **Sin correspondencia escena↔imagen**: el pipeline no sabe qué se narra mientras se muestra un clip (`video.py:601`). Es la causa del "AI slop" visual.
2. **Repetición de clips** cuando falta material (`video.py:787-802`).
3. **Fade/slide por clip contra negro** (`video.py:728-753`): flashes negros en cada corte.
4. **Subtítulos todo-o-nada** (`voice.py:2130`) y corrección con lógica muerta (`subtitle.py:237`).
5. **Timings falsos** para todo TTS que no sea edge/Azure (`voice.py:604-673`).
6. **Reintentos sin backoff** en LLM (`llm.py:14,554`) y TTS; `siliconflow` sin timeout (`voice.py:944`).
7. **Sin licencia/atribución** en recursos; Coverr casi nunca devuelve autor.
8. **Estado volátil**: tareas en memoria; nada se reanuda tras un reinicio.
9. **Secretos en claro** en `config.toml`, escritos desde el WebUI.
10. **Triple codificación** (tiempo ×3, pérdida de calidad).
11. **Hilos, no procesos**, para trabajo CPU-bound; el GIL no afecta a ffmpeg pero sí a Pillow/moviepy.
12. `video` como variable local sombrea el módulo `video` en `material.py:340,460` (bomba latente).
13. Descargas `requests.get(...).content` sin comprobar status ni tamaño (`material.py:1018`).
14. Recursos incluidos (`resource/songs`, fuentes MS/Apple) sin licencia trazable.

### Riesgos de proveedores gratuitos
- **edge-tts**: endpoint no oficial; Microsoft puede cortarlo. Se necesita TTS local de respaldo (Kokoro/Piper).
- **Pexels/Pixabay**: gratis pero con límites (Pexels 200 req/h, 20.000/mes) y obligación de no redistribuir ni "competir"; permiten uso en vídeos.
- **Google Trends**: sin API oficial; las librerías no oficiales rompen los ToS. Lo dejaremos fuera del MVP.
