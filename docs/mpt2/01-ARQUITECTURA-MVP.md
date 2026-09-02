# Arquitectura propuesta para el MVP (MoneyPrinterTurbo 2.0 — Autonomous Media Engine)

Estado: propuesta para aprobación. No se ha escrito código del sistema nuevo.

---

## 1. Principio de diseño

**Kernel reutilizado + capa editorial nueva.** No se reescribe MoneyPrinterTurbo; se
mantiene `app/` como *kernel de render y proveedores* con parches mínimos, y se construye
un paquete nuevo `mpt2/` que orquesta el trabajo por escenas, persiste todo en SQLite y
expone un panel propio. Ninguna función editorial vive en un prompt gigante: cada agente
es un módulo Python con entrada y salida tipadas (Pydantic) y tests.

Reglas:
1. **Nada de texto libre entre módulos.** Cada etapa recibe y devuelve modelos validados.
2. **Todo se persiste.** Cada artefacto (dossier, guion, storyboard, assets, audio, render,
   QC, metadatos, aprobación) es una fila con `video_id`, versión y hash de entradas.
3. **Idempotencia por hash.** Una etapa con las mismas entradas no se recalcula (caché).
4. **Un solo proceso worker** para el MVP. Render y whisper son CPU-bound; paralelizar
   sin GPU no ayuda. Escalar a varios workers es cambiar un número, no la arquitectura.
5. **Sin Kubernetes, sin microservicios, sin base vectorial, sin frameworks multiagente.**
   Un scheduler de trabajos sobre SQLite es suficiente y depurable.
6. **Aprobación humana obligatoria** antes de publicar. La publicación automática existe
   como flag por canal, desactivado.

---

## 2. Diagrama del MVP

```
                          ┌────────────────────────────────────────────┐
                          │  Panel web (FastAPI + Jinja2 + HTMX)       │
                          │  dashboard · pipeline · vista de vídeo ·   │
                          │  aprobaciones · costes · analítica         │
                          └───────────────┬────────────────────────────┘
                                          │ HTTP (mismo proceso que la API)
┌───────────────┐   ┌─────────────────────▼────────────────────────┐   ┌───────────────┐
│ channels/*.yml│──►│  mpt2.api  (FastAPI /api/v2)                  │◄──│ .env (secretos│
│ config edit.  │   │  crea ContentIdea/Video, encola Jobs           │   │ y núcleo)     │
└───────────────┘   └─────────────────────┬────────────────────────┘   └───────────────┘
                                          │ SQLite (SQLAlchemy 2 + Alembic)
                          ┌───────────────▼────────────────────────────┐
                          │  mpt2.jobs  worker (1 proceso, cola en DB)  │
                          │  retries · backoff · resume · idempotencia  │
                          └───────────────┬────────────────────────────┘
                                          │ ejecuta etapas (stage graph)
   ┌─────────┬─────────┬─────────┬────────┼────────┬─────────┬─────────┬──────────┐
   ▼         ▼         ▼         ▼        ▼        ▼         ▼         ▼          ▼
research  script   factcheck storyboard assets   voice    editing     qc     metadata
  agent    agent     agent     agent    agent    agent     agent     agent    agent
   │         │         │         │        │        │         │         │          │
   └─────────┴─────────┴────┬────┴────────┴────────┴─────────┴─────────┴──────────┘
                            ▼
              mpt2.providers (adaptadores intercambiables)
   llm: ollama | groq-free | gemini-free | openai      tts: kokoro(local) | edge-tts | piper
   stock: pexels | pixabay | openverse | wikimedia     align: faster-whisper (local)
   graphics: pillow + matplotlib + ffmpeg filters      youtube: data api v3 | analytics api
                            │
                            ▼
              app/services (kernel MPT reutilizado)
   video.py (fit, efectos, concat, render) · voice.py (edge-tts) · subtitle.py (whisper)
   material.py (búsqueda/descarga/caché) · bgm.py · llm_provider.py (registro)
```

Flujo de estados de un vídeo (columna `status` de `Video`):
`idea → scored → approved_idea → researching → scripting → fact_checking → storyboarding →
sourcing_assets → voicing → editing → qc → awaiting_approval → scheduled → published →
analyzed → archived` (más `failed_<etapa>` y `needs_human`).

---

## 3. Estructura de carpetas

```
MoneyPrinterTurbo-2.0/
├── app/                      # kernel upstream (parches mínimos, tests verdes)
├── webui/, cli.py, main.py   # herramientas upstream, se conservan para depurar el kernel
├── mpt2/                     # sistema nuevo
│   ├── core/
│   │   ├── settings.py       # pydantic-settings: .env, rutas, límites, flags
│   │   ├── db.py             # engine SQLite, sesiones, base declarativa
│   │   ├── models/           # tablas SQLAlchemy (una por entidad de la sección 4)
│   │   ├── schemas/          # Pydantic I/O entre agentes (StoryboardSpec, ClaimList…)
│   │   ├── jobs.py           # cola en DB, worker, reintentos, resume, locks
│   │   ├── cache.py          # caché por hash de entradas (LLM, TTS, assets)
│   │   ├── costs.py          # CostEntry: unidades y coste por proveedor
│   │   ├── quotas.py         # contadores por proveedor/día (YouTube, Pexels…)
│   │   └── logging.py        # loguru con video_id/stage en cada línea
│   ├── channels/
│   │   ├── schema.py         # ChannelConfig (Pydantic) y validación de YAML
│   │   └── loader.py
│   ├── agents/               # un módulo por agente, misma interfaz run(ctx) -> Artifact
│   │   ├── research.py  script.py  factcheck.py  storyboard.py  assets.py
│   │   ├── voice.py  editing.py  qc.py  metadata.py  publishing.py
│   │   └── (fase 2+) discovery.py  scoring.py  analytics.py  learning.py
│   ├── providers/
│   │   ├── llm/       (base.py, ollama.py, openai_compat.py → reusa app/models/llm_provider)
│   │   ├── tts/       (base.py, kokoro.py, edge.py → reusa app/services/voice, piper.py)
│   │   ├── stock/     (base.py, pexels.py, pixabay.py, openverse.py, wikimedia.py)
│   │   ├── search/    (wikipedia.py, gdelt.py, rss.py, sec_edgar.py, youtube_data.py)
│   │   ├── align/     (whisper.py → reusa app/services/subtitle)
│   │   ├── graphics/  (cards.py, charts.py, kenburns.py)  # Pillow/matplotlib/ffmpeg
│   │   └── youtube/   (auth.py, upload.py, analytics.py)
│   ├── pipeline/
│   │   ├── stages.py         # grafo de etapas, dependencias, qué invalida qué
│   │   └── runner.py         # ejecuta una etapa con contexto, guarda artefacto
│   ├── render/
│   │   └── timeline.py       # compone escenas → ffmpeg (usa app/services/video helpers)
│   ├── api/                  # FastAPI /api/v2 (routers por dominio)
│   └── ui/                   # templates Jinja2 + HTMX, static/
├── channels/                 # YAML editorial por canal (sin secretos, versionado)
│   └── business-stories-en.yaml
├── migrations/               # Alembic
├── storage/                  # gitignored: db, assets, renders, cachés, backups
├── tests/mpt2/               # unit + integración (ffmpeg real con clips sintéticos)
├── docs/mpt2/                # auditoría, arquitectura, decisiones, estado
├── PROJECT_STATE.md          # estado y siguientes pasos (se actualiza cada sesión)
└── .env.example
```

---

## 4. Modelo de datos inicial

Identificador permanente: `Video.id` (UUID v7, ordenable por tiempo). Todas las tablas
hijas llevan `video_id`; los artefactos versionados llevan `version` e `input_hash`.

| Entidad | Campos clave | Relaciones |
|---|---|---|
| **Channel** | id, slug, name, language, country, niche, subniches[], audience, tone, persona, target_duration_s, voice_id, visual_style, palette, font, music_policy, allowed_sources[], banned_sources[], banned_words[], publish_cadence, monetization, cost_limit_per_video, quality_thresholds{}, auto_publish=false, youtube_channel_id | 1→N Video, ContentIdea |
| **ContentIdea** | id, channel_id, title, summary, origin(manual/discovery), source_signal{}, status, dedup_key, created_at | 1→1 OpportunityScore, 1→N Video |
| **OpportunityScore** | idea_id, total, dims{views, fit, originality, value, competition, sources, visuals, retention, commercial, copyright_risk, misinformation_risk, policy_risk, cost, lifetime, relation_to_winners}, rationale (texto por dimensión), model, version | |
| **Video** | id, channel_id, idea_id, working_title, format(long/short), parent_video_id (shorts derivados), status, current_stage, angle, hook_id, duration_target_s, created_at, updated_at | hub de todo |
| **ResearchSource** | id, video_id, url, title, author, publisher, published_at, retrieved_at, license, reliability(1–5), content_hash, excerpt_path | 1→N ResearchClaim |
| **ResearchClaim** | id, video_id, source_ids[], text, kind(fact/estimate/opinion/inference), entities[], numbers[], date, confidence, contradicts_claim_id, verified(bool), verification_note | ← ScriptSection.claim_ids |
| **ResearchDossier** | video_id, version, summary, timeline[], open_questions[], contradictions[] | |
| **Angle** / **Hook** | video_id, type, text, scores{curiosity, clarity, credibility, promise, specificity, retention, clickbait_risk}, selected | |
| **Script** | id, video_id, version, hook_variant_ids[], est_duration_s, word_count, language, status(draft/reviewed/approved), reviewer_notes | 1→N ScriptSection |
| **ScriptSection** | id, script_id, order, role(hook/promise/context/development/reveal/conclusion/cta), text, claim_ids[], needs_verification(bool), est_duration_s | |
| **FactCheckReport** | script_id, version, findings[{section_id, claim_id, status(pass/warn/block), reason}], blocked(bool) | |
| **Storyboard** | id, video_id, script_id, version, aspect, total_est_duration_s | 1→N Scene |
| **Scene** | id, storyboard_id, order, section_id, narration, est_duration_s, narrative_goal, visual_description, asset_type(enum 14 tipos), search_terms[], on_screen_text, animation, transition, framing, motion, source_hint, license_requirement, confidence, fallback_visual | 1→N Asset |
| **Asset** | id, video_id, scene_id, kind(video/image/graphic/audio), provider, provider_asset_id, source_url, author, license(enum: pexels, pixabay, cc0, cc-by, cc-by-sa, public-domain, generated, own), attribution_text, local_path, width, height, duration_s, fps, content_hash, relevance_score, status(candidate/selected/rejected/removed) | |
| **VoiceSegment** | id, video_id, scene_id, text, voice_id, provider, rate, pronunciation_overrides{}, audio_path, duration_s, word_timings[], input_hash, version | |
| **RenderJob** | id, video_id, kind(preview/final/short), timeline_json, output_path, codec, resolution, fps, duration_s, started_at, finished_at, status, error, attempts | |
| **Thumbnail** | id, video_id, concept, text, template, image_path, selected | |
| **Metadata** | video_id, titles[] (10), selected_title, description, chapters[], tags[], pinned_comment, cta, shorts_text, variants[] | |
| **QualityCheck** | id, video_id, render_job_id, checks[{name, status, score, detail}], total_score, passed, sent_back_to_stage | |
| **HumanApproval** | id, video_id, stage(idea/script/claims/storyboard/final/publish), decision(approve/reject/changes), reviewer, notes, diff_ref, created_at | |
| **Publication** | id, video_id, platform(youtube), youtube_video_id, privacy, scheduled_at, published_at, playlist_id, status, api_quota_used, error | |
| **AnalyticsSnapshot** | id, publication_id, captured_at, window(24h/72h/7d/28d), impressions, ctr, views, avg_view_duration_s, avg_view_pct, retention_30s, traffic_sources{}, subs_gained, likes, comments, shares, rpm, revenue | |
| **Experiment** | id, channel_id, hypothesis, variable, variants[], video_ids[], status, result, sample_size, confidence | |
| **CostEntry** | id, video_id, channel_id, provider, stage, units, unit_type(tokens/chars/requests/seconds), est_cost_eur, created_at | |
| **LearningInsight** | id, channel_id, statement, evidence_video_ids[], sample_size, effect_size, confidence, caveats, created_at, status(hypothesis/supported/refuted) | |
| **Job** | id, video_id, stage, status(queued/running/done/failed/needs_human), attempts, max_attempts, next_run_at, locked_by, error, input_hash, created_at | cola |
| **DecisionLog** | id, video_id, actor(agent/human), action, payload{}, created_at | historial completo |

Esquemas de intercambio (Pydantic, `mpt2/core/schemas`): `ResearchBundle`, `ScriptSpec`,
`FactCheckResult`, `StoryboardSpec`, `AssetPlan`, `VoicePlan`, `Timeline`, `QCReport`,
`MetadataPack`. Los LLM devuelven JSON validado contra estos esquemas; si no valida, se
reintenta con el error como feedback (máximo 3) y después escala a humano.

---

## 5. Proveedores recomendados

> **Actualización 2026-09-01 (ADR-010):** el presupuesto de validación pasa de 0 € a **150 €/mes** con límites configurables y bloqueo. Se admiten servicios de pago cuando mejoren de forma material calidad, fiabilidad o velocidad, manteniendo las alternativas gratuitas de la tabla. Previstos para fases posteriores: API de Anthropic, ElevenLabs, VPS Linux europeo y RunPod. No se integran en H1.


| Necesidad | Gratuito/local recomendado | Límites | Alternativa de pago | Diferencia | Cuándo pagar |
|---|---|---|---|---|---|
| LLM (guion, storyboard, metadatos) | **Ollama local** (Qwen2.5-14B o Llama-3.1-8B según hardware). Fallback: **Groq free tier**, **Gemini API free tier** (AI Studio), **OpenRouter :free** | Ollama: velocidad según GPU/CPU (8B en CPU ≈ 5–10 tok/s). Groq ≈ 14.400 req/día por modelo; Gemini free ≈ 15 req/min con uso de datos para entrenamiento | Claude/GPT-4-class ≈ 0,05–0,30 € por vídeo largo (≈ 50–100 k tokens) | Calidad de escritura, seguimiento de esquemas JSON, menos alucinación de datos | Cuando el fact-check rechace >30 % de guiones locales o cuando escribir un guion tarde >20 min en tu máquina |
| Investigación | **Wikipedia API**, **GDELT** (noticias, gratis), **Google News RSS**, **RSS sectoriales**, **SEC EDGAR full-text search** (10-K/S-1, ideal para historias empresariales), **YouTube Data API** (10.000 unidades/día) | EDGAR: 10 req/s con User-Agent. YouTube: 100 unidades por búsqueda | Tavily/Serper/Perplexity ≈ 5–20 €/mes | Búsqueda web general | Cuando >20 % de temas no encuentren ≥3 fuentes fiables gratis |
| Extracción de páginas | **trafilatura** (local) | — | Jina Reader/Firecrawl | Sitios con JS | Raro |
| TTS | **Kokoro-82M** local (Apache-2.0, CPU, calidad alta en inglés/español). Fallback: **edge-tts** (gratis, no oficial), **Piper** (MIT) | Kokoro: ≈ tiempo real ×0,3 en CPU; sin timings por palabra (alineamos con whisper) | ElevenLabs ≈ 5–22 €/mes; Azure Neural ≈ 15 € por millón de caracteres | Expresividad, pausas naturales, multi-idioma | Si en la prueba A/B la retención a 30 s difiere >5 pt entre voz local y de pago |
| Alineación/subtítulos | **faster-whisper** local (`small`/`medium` int8 CPU) | ≈ 1× tiempo real en 4 vCPU con `small` | Deepgram/AssemblyAI | Menor error en nombres | Raro; el texto ya lo conocemos, solo alineamos |
| Stock vídeo/foto | **Pexels API**, **Pixabay API**, **Openverse API** (CC en múltiples fuentes), **Wikimedia Commons API** (licencia por archivo), **Coverr** | Pexels 200 req/h · 20.000/mes; Pixabay ≈ 100 req/min; todos requieren no almacenar masivamente | Storyblocks ≈ 30 €/mes, Artgrid | Catálogo y coherencia estética | Cuando >25 % de escenas queden sin recurso relevante |
| Imagen generada | **Stable Diffusion local (ComfyUI)** vía la fuente `openai_image` ya existente. Solo si hay GPU ≥ 8 GB | Tiempo de generación | fal.ai/Replicate ≈ 0,01–0,05 €/imagen | Calidad y velocidad | Cuando los gráficos/fotos de archivo no cubran las escenas de "recreación" |
| Gráficos/motion | **Pillow** (tarjetas, texto animado), **matplotlib** (gráficos exportados a frames), **ffmpeg** (`zoompan`, `drawtext`, `xfade`) | — | After Effects/Remotion (Node) | Motion graphics complejos | Fase 6 |
| Música | **YouTube Audio Library** (descarga manual, licencia clara), **Pixabay Music**, **Free Music Archive**, **Kevin MacLeod CC-BY** | Atribución en descripción cuando aplique | Epidemic Sound ≈ 10–15 €/mes | Catálogo y sin atribución | Cuando el canal monetice |
| Miniaturas | **Pillow** con plantillas por canal + fotos de stock | — | Canva Pro, generación IA | Acabado | Cuando el CTR sea el cuello de botella |
| Publicación | **YouTube Data API v3** (OAuth propio) | 10.000 unidades/día; `videos.insert` = 1.600 → ≈ 6 subidas/día. Apps no verificadas: cuota reducida y vídeos privados hasta auditoría | — | — | Nunca; es la vía oficial |
| Analítica | **YouTube Analytics API** | Datos con 24–72 h de retraso; retención requiere `audienceWatchRatio` | — | — | — |
| Base de datos | **SQLite** + SQLAlchemy + Alembic | Un escritor a la vez (suficiente con 1 worker) | Postgres | Concurrencia | Fase 6 (varios workers) |
| Contenedores | **Docker Compose** (api+worker, volumen `storage/`) | — | — | — | — |

Registro de coste: cada llamada a proveedor crea un `CostEntry` con unidades reales
(tokens, caracteres, requests, segundos de render). Para proveedores gratuitos el coste es 0 €
pero las unidades se registran para saber qué pagaríamos si migráramos (`est_cost_eur` calculado
con una tabla de precios editable).

---

## 6. Riesgos técnicos

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| edge-tts deja de funcionar (endpoint no oficial) | Media | Alto | Kokoro local como voz principal; edge-tts solo fallback |
| LLM local de 8B no sigue esquemas JSON ni escribe guiones de 8 min con calidad | Alta | Alto | Salida por secciones cortas; validación Pydantic con reintento guiado; Groq/Gemini free como escalón; medir tasa de rechazo del fact-check |
| Alucinación de datos/citas | Alta | Crítico (políticas YouTube y credibilidad) | Toda cifra/fecha/nombre en el guion debe mapear a un `ResearchClaim` con fuente; el fact-check bloquea si no |
| Render lento en CPU (3–4× duración del vídeo hoy; un vídeo de 8 min podría tardar 30–40 min) | Alta | Medio | Eliminar la triple codificación; subtítulos por filtro ffmpeg; previews a 480p; render final en cola nocturna |
| Storyboard con imágenes irrelevantes (el problema central) | Alta | Alto | Términos de búsqueda por escena + puntuación de relevancia (LLM juzga candidato vs. narración) + tipos de recurso no-stock (tarjetas, gráficos, timelines) cuando no haya footage |
| Cuotas API (Pexels/YouTube) agotadas | Media | Medio | Contadores por día en `quotas.py`, caché de búsquedas, cola con `next_run_at` |
| Estado inconsistente tras reinicio | Media | Medio | Cola en DB con `locked_by` + heartbeat; etapas idempotentes por hash; artefactos escritos de forma atómica |
| SQLite bloqueado con UI + worker | Baja | Bajo | WAL mode, timeouts, un solo escritor |
| Dependencia de `moviepy`/Pillow en RAM con vídeos largos | Media | Medio | Componer con ffmpeg por escena y concatenar; moviepy solo para efectos puntuales |
| Deriva de upstream (queremos seguir cogiendo fixes de `app/`) | Media | Bajo | Parches mínimos en `app/`; lógica nueva fuera; `git merge upstream/main` periódico |

## 7. Riesgos de copyright y plataforma

| Riesgo | Mitigación en el diseño |
|---|---|
| Uso de clips/fotos sin licencia o con atribución obligatoria omitida | `Asset.license` obligatorio; QC bloquea assets sin licencia; generador automático de bloque de atribuciones en la descripción |
| Música incluida en el repo sin licencia | No se usa `resource/songs`; biblioteca propia con `license` y `source_url` por pista |
| Fuentes tipográficas propietarias en el repo | Solo fuentes OFL/Apache en el canal |
| Política de YouTube sobre contenido repetitivo/masivo y "inauthentic content" (actualizada julio 2025) | Cada vídeo parte de un dossier de investigación propio, guion original, storyboard específico; QC mide similitud con vídeos previos del canal; límite de publicaciones/día por canal |
| Desinformación / afirmaciones financieras | Fact-check bloqueante; disclaimers cuando se hable de inversión; sin consejos financieros personalizados |
| Difamación (historias de fracasos empresariales con personas nombradas) | Claims sobre personas requieren ≥2 fuentes fiables y lenguaje atribuido ("según el informe de…"); revisión humana obligatoria en esos casos |
| Miniaturas engañosas | Metadata Agent genera conceptos vinculados a claims del guion; QC rechaza texto no soportado |
| Voces clonadas | Solo voces sintéticas de catálogo; ningún endpoint de clonación se expone |
| Contenido sintético: etiqueta de divulgación de YouTube | Campo por vídeo `synthetic_disclosure` que la publicación envía cuando la narración/imágenes sean generadas de forma realista |
| Términos de APIs gratuitas (Pexels/Pixabay prohíben cachés masivas y redistribución) | Caché con TTL, sin librería offline; los assets se borran tras publicar salvo los usados |
| Scraping que viola ToS (Google Trends, YouTube sin API) | Solo APIs oficiales y RSS. Google Trends fuera del MVP |

---

## 8. Backlog priorizado

### Imprescindible (Fase 1: un vídeo de extremo a extremo)
1. Esqueleto `mpt2/`: settings, DB, modelos, migraciones Alembic, logging, tests base.
2. `ChannelConfig` YAML + canal piloto `business-stories-en`.
3. Cola de trabajos en DB con reintentos, backoff, resume y `needs_human`.
4. `LLMClient` con salida estructurada validada (Ollama + Groq/Gemini free) y registro de coste.
5. Research Agent (Wikipedia, GDELT/RSS, EDGAR, trafilatura) → `ResearchSource`/`ResearchClaim`/dossier.
6. Script Agent por secciones con `claim_ids` y duración estimada; 3 variantes de hook.
7. Fact-Checking Agent bloqueante.
8. Storyboard Agent → `Scene[]` tipadas con términos y tipo de recurso.
9. Asset Agent: Pexels + Pixabay + Openverse/Wikimedia, licencia obligatoria, relevancia por escena, generador de tarjetas/gráficos como alternativa.
10. Voice Agent por escena: Kokoro local + edge-tts fallback, caché por hash, alineación whisper.
11. Editing Agent: timeline por escena → ffmpeg (Ken Burns, xfade, subtítulos ASS, ducking por sidechain, loudnorm), 16:9.
12. QC Agent: informe con puntuación; reenvío a etapa.
13. Metadata Agent: 10 títulos, descripción, capítulos, atribuciones.
14. Panel mínimo: lista de vídeos, vista de vídeo con artefactos, botones de aprobación por etapa.
15. Exportación local del MP4 + paquete de publicación (título, descripción, miniatura, SRT).
16. Docker Compose (api + worker) y `.env.example`.

### Importante (Fases 2–4)
- Discovery Agent (YouTube Data API, RSS, Wikipedia current events, Reddit API oficial) e ideas con dedup.
- Opportunity Scoring explicado; calendario editorial.
- Thumbnail Agent con plantillas Pillow; 3 conceptos.
- Publishing Agent con YouTube Data API v3, programación, playlists, control de cuota, registro de ID.
- Analytics Agent (YouTube Analytics API) con snapshots 24h/72h/7d/28d.
- Dashboard con costes, errores, aprobaciones pendientes, rendimiento.
- Shorts derivados editados como piezas independientes (9:16, hook propio).
- Backups automáticos de `storage/` y export/import de un vídeo completo (zip con DB parcial + artefactos).

### Posterior (Fases 5–6)
- Learning Agent: relación decisiones→resultados, insights con tamaño de muestra y confianza.
- Experimentos A/B de títulos/miniaturas (YouTube "Test & compare" no tiene API; se hace por lotes).
- Varios canales/idiomas, presupuestos y límites diarios por canal, publicación automática opcional.
- Imagen/vídeo generados localmente (SD/ComfyUI) e image-to-video.
- Migración a Postgres y varios workers si hace falta.

---

## 9. Estimación de dificultad por módulo (1 = trivial, 5 = muy difícil)

| Módulo | Dificultad | Por qué |
|---|---|---|
| Núcleo (settings, DB, migraciones, jobs, cache, costs) | 3 | Conocido, pero la reanudación e idempotencia exigen cuidado |
| ChannelConfig | 1 | YAML + Pydantic |
| LLMClient estructurado | 3 | Los modelos locales pequeños fallan con JSON largo; hay que trocear y reintentar |
| Research Agent | 4 | Calidad de fuentes, extracción de claims sin copiar, contradicciones; muy dependiente del LLM |
| Script Agent | 3 | Prompting por secciones con restricciones; medición de duración |
| Fact-Checking Agent | 4 | Decidir qué bloquea sin falsos positivos masivos |
| Storyboard Agent | 4 | Pieza crítica; requiere iteración con datos reales |
| Asset Agent | 4 | Relevancia semántica con medios gratuitos; licencias; alternativas gráficas |
| Voice Agent | 3 | Kokoro + alineación whisper + caché; pronunciaciones |
| Editing Agent (timeline ffmpeg) | 4 | Sincronía, ducking, subtítulos ASS, xfade, rendimiento en CPU |
| QC Agent | 3 | Chequeos técnicos fáciles; chequeos editoriales dependen del LLM |
| Metadata/Thumbnail | 2 | Plantillas + LLM |
| Panel (FastAPI+HTMX) | 3 | Muchas vistas sencillas; sin SPA |
| Discovery/Scoring | 3 | APIs oficiales con cuotas; scoring explicable |
| Publishing (YouTube API) | 3 | OAuth, cuotas, verificación de app de Google |
| Analytics | 2 | API estable, datos con retraso |
| Learning | 4 | Estadística con muestras pequeñas sin engañarse |

---

## 10. Plan para producir el primer vídeo (Fase 1, tema manual)

Objetivo: un vídeo de 6–8 min en inglés, 16:9, sobre un tema introducido a mano
(propuesta: "Cómo WeWork pasó de 47.000 M$ a la quiebra", ya usado en la referencia),
con dossier, guion verificado, storyboard, recursos con licencia, voz local, subtítulos,
render, QC y paquete de publicación, todo aprobado a mano.

Hitos (cada uno con tests y demostración):
1. **H1 Núcleo** (esqueleto, DB, jobs, canal piloto, CLI `mpt2 video create --channel … --topic …`). Criterio: crear un vídeo, encolar etapa ficticia, matar el proceso, reanudar.
2. **H2 Investigación** (Research + LLMClient). Criterio: dossier con ≥5 fuentes, ≥20 claims tipados, contradicciones marcadas, revisable en el panel.
3. **H3 Guion + Fact-check**. Criterio: guion por secciones con claims enlazados; fact-check bloquea un claim inventado a propósito en un test.
4. **H4 Storyboard + Assets**. Criterio: ≥90 % de escenas con recurso relevante y licencia; el resto con tarjeta/gráfico generado.
5. **H5 Voz + Alineación**. Criterio: audio por escena, SRT alineado, regenerar una frase sin tocar el resto.
6. **H6 Edición + QC**. Criterio: MP4 final 1080p con ducking y subtítulos; informe QC > umbral; tiempo de render medido.
7. **H7 Metadatos + Aprobación + Exportación**. Criterio: paquete listo para subir a mano a YouTube Studio.

Después: pilotos 2 (automoción) y 3 (finanzas) reutilizando todo, midiendo tiempo humano y de máquina.

---

## 11. Preguntas que necesito resolver contigo

1. **Hardware y ubicación de ejecución**: ¿en qué máquina correrá (PC personal, servidor, NAS)? ¿CPU, RAM, GPU (modelo y VRAM)? Determina si Ollama con 14B y Kokoro son viables y cuánto tardará el render.
2. **Sistema operativo** de esa máquina (Linux/macOS/Windows). Afecta a Docker y a Kokoro/whisper.
3. **LLM gratuito preferido**: ¿aceptas usar niveles gratuitos en la nube (Groq, Gemini AI Studio, OpenRouter) sabiendo que pueden usar tus datos para entrenar y tienen límites, o quieres 100 % local aunque sea más lento y peor?
4. **Riesgo edge-tts**: ¿aceptas edge-tts (no oficial) como fallback, o prefieres solo voces locales (Kokoro/Piper)?
5. **Canal de YouTube**: ¿existe ya? ¿Tienes proyecto en Google Cloud con YouTube Data API habilitada y pantalla de consentimiento OAuth? Sin verificación de Google, las subidas por API quedan privadas.
6. **Claves gratuitas** que puedas crear ahora: Pexels, Pixabay, Groq/Gemini. No las pongas en el repo; irán en `.env`.
7. **Panel**: propongo FastAPI + Jinja2 + HTMX (servidor, sin build de JS, fácil de depurar). La alternativa es extender Streamlit (más rápido al principio, peor para flujos de revisión con formularios y estado). ¿Conforme con la propuesta?
8. **Idioma y voz del canal piloto**: inglés US con voz masculina o femenina; nombre del canal y tono (documental serio, divulgativo ágil…).
9. **Estilo visual**: ¿tienes referencias de canales cuyo look te gusta (sin copiar miniaturas)? Paleta y tipografía preferidas.
10. **Fuentes prohibidas/permitidas** iniciales para el nicho (por ejemplo, evitar tabloides; permitir EDGAR, prensa financiera, Wikipedia solo como índice).
11. **Umbral de aprobación**: ¿quieres aprobar en 3 puntos (guion, storyboard, final) o solo al final en el MVP?
12. **Duración objetivo** de los pilotos: propongo 6–8 min para reducir tiempo de render y validar antes.
13. **Repositorio**: ¿mantenemos `webui/Main.py` y `cli.py` del upstream (útiles para depurar) o los eliminamos para reducir superficie?
