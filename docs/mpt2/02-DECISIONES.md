# Registro de decisiones (ADR ligero)

Formato: número, fecha, decisión, contexto, alternativas descartadas, consecuencias.
Las decisiones marcadas *(pendiente)* esperan aprobación del propietario.

## ADR-001 · 2026-09-01 · Reutilizar `app/` como kernel, construir `mpt2/` aparte
- Contexto: `app/services/video.py`, `voice.py`, `subtitle.py`, `material.py` y el registro LLM son sólidos y están cubiertos por 865 tests; el pipeline de `task.py` es lineal y no modela escenas.
- Alternativas: (a) reescribir desde cero; (b) modificar `task.py` in situ.
- Decisión: envolver, no reescribir. Parches mínimos en `app/`; toda la lógica nueva en `mpt2/`.
- Consecuencias: podemos seguir fusionando upstream; el WebUI/CLI antiguos siguen funcionando para depurar.

## ADR-002 · 2026-09-01 · SQLite + SQLAlchemy 2 + Alembic como persistencia del MVP *(aprobada 2026-09-01)*
- Alternativas: Redis (volátil, sin consultas), Postgres (más operación), JSON en disco (sin consultas ni migraciones).
- Consecuencias: un proceso worker; WAL; migración a Postgres solo en Fase 6.

## ADR-003 · 2026-09-01 · Cola de trabajos en la propia DB, un worker *(aprobada 2026-09-01)*
- Alternativas: Celery+Redis, RQ, Dramatiq, APScheduler.
- Motivo: el trabajo es CPU-bound y secuencial; una tabla `Job` con `locked_by`, `next_run_at` y reintentos es suficiente y totalmente depurable.

## ADR-004 · 2026-09-01 · Panel con FastAPI + Jinja2 + HTMX *(aprobada 2026-09-01; no se construye en H1)*
- Alternativas: extender Streamlit (rerun completo por interacción, estado frágil para revisiones), SPA React (build, más código).
- Motivo: flujos de revisión con formularios, diffs y aprobaciones se resuelven mejor con HTML servido.

## ADR-005 · 2026-09-01 · Voz: edge-tts como fallback gratuito provisional; voz comercial en fases posteriores *(actualizada, ver ADR-010)*
- Motivo: edge-tts usa un endpoint no oficial; Kokoro es Apache-2.0, corre en CPU y su calidad es suficiente para narración. Los timings se obtienen alineando con faster-whisper.

## ADR-006 · 2026-09-01 · Solo APIs oficiales y RSS para descubrimiento; Google Trends fuera del MVP
- Motivo: los ToS de Google prohíben el scraping de Trends; las librerías no oficiales rompen con frecuencia.

## ADR-007 · 2026-09-01 · Publicación con YouTube Data API v3 propia; se retira upload-post.com
- Motivo: control de credenciales, cuotas y metadatos; evitar un SaaS intermediario.

## ADR-008 · 2026-09-01 · No usar `resource/songs` ni las fuentes propietarias del repo en vídeos publicados
- Motivo: sin licencia trazable. Se sustituirán por una biblioteca con licencia registrada por pista.

## ADR-009 · 2026-09-01 · Secretos solo en `.env`; configuración editorial en `channels/*.yaml` versionada
- Motivo: `config.toml` del upstream mezcla secretos y preferencias y se escribe desde el WebUI.

## ADR-010 · 2026-09-01 · Presupuesto de validación de 150 €/mes con control de costes bloqueante
- Contexto: el propietario levanta la restricción de coste cero. Se admiten servicios de pago cuando mejoren de forma material calidad, fiabilidad o velocidad.
- Decisión: presupuesto inicial de validación de **150 €/mes**, con límites configurables por proveedor, tarea, vídeo y canal y **bloqueo antes de excederlos**. Los proveedores siguen siendo intercambiables y se conservan alternativas gratuitas.
- Proveedores previstos para fases posteriores (no integrados en H1): API de Anthropic (inteligencia), ElevenLabs (voz comercial), VPS Linux europeo (operación permanente), RunPod (GPU bajo demanda).
- Consecuencias: `CostEntry` ya registra proveedor, etapa, unidades y coste estimado por vídeo y canal; `Channel.max_budget_eur` existe. En H2+ se añadirán límites mensuales por canal y proveedor, contadores y un guard que rechace la llamada antes de superar el límite. Ninguna integración de pago se añade en H1.

## ADR-011 · 2026-09-01 · H1 no toca `app/`; los tests nuevos viven en `test/mpt2/`
- El CI upstream ejecuta `pytest test`, por lo que `test/mpt2/` se ejecuta automáticamente. Se amplían `compileall`/`ruff` del CI a `mpt2` y la cobertura incluye `mpt2`.
- `uv add` reescribió `uv.lock` al formato de revisión 3 (añade `upload-time`); ninguna versión existente cambió.
