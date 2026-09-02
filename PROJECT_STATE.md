# Estado del proyecto — MoneyPrinterTurbo 2.0 (Autonomous Media Engine)

Última actualización: 2026-09-02 · Fase actual: **Fase 1 · Hito H1 implementado, esperando aprobación para H2**

## Qué funciona hoy
- **Upstream intacto**: `app/`, `cli.py`, `webui/`, `main.py` sin cambios de código. `python main.py` arranca (13 rutas). Suite upstream: 865 tests pasan.
- **`mpt2/` (H1)**: settings desde entorno con validación, SQLite con migraciones Alembic (revisión `0001`, 15 tablas), máquina de estados validada de `VideoProject` con historial, cola de trabajos durable con idempotencia, reintentos con backoff y recuperación tras reinicio, contratos de los 8 módulos futuros, adaptador LLM sobre el registro upstream (Ollama por defecto), configuración de canal por YAML/API, API `/api/v2` (canales, proyectos, estado, transición, aprobaciones, errores, costes, health) y CLI `python -m mpt2`.
- **Tests**: 958 pasan (865 upstream + 93 mpt2) + 5 de CLI añadidos después del último recuento; cobertura de ramas total 81 % (umbral CI 70 %). `ruff check` y `compileall` limpios.
- **Canal piloto**: `channels/business-stories-en.yaml` importable con `python -m mpt2 import-channel`.

## Qué no existe todavía (por diseño, H2+)
- Research, Script, Fact-check, Storyboard, Assets, Voice, Editing, QC, Metadata: solo contratos.
- Panel HTMX, publicación en YouTube, analítica, aprendizaje, multi-canal.
- Límites mensuales de gasto por proveedor/canal con bloqueo (ADR-010): solo el registro de costes y `max_budget_eur` existen.

## Limitaciones conocidas
- El worker de trabajos se ejecuta bajo demanda (`JobQueue.run_pending()`); no hay proceso daemon todavía.
- `PipelineJob.claim_next` usa `SELECT ... FOR UPDATE`, que SQLite ignora; con un solo worker es seguro. Para varios workers habrá que pasar a Postgres o a un lock por fila explícito.
- En el sandbox de esta sesión Pexels, Pixabay, edge-tts y Google están bloqueados por red; no afecta al código.

## Documentos
- `docs/mpt2/00-FASE0-AUDITORIA.md` · `01-ARQUITECTURA-MVP.md` · `02-DECISIONES.md` (ADR-001…011) · `03-H1-GUIA.md` (ejecutar y probar H1).

## Siguientes pasos (bloqueados por aprobación)
1. **H2 · Investigación**: `LLMClient` con salida estructurada y registro de coste, Research Agent (Wikipedia, GDELT/RSS, EDGAR, trafilatura) → `ResearchSource`/`ResearchClaim`/dossier, primer handler real en la cola.
2. Guard de presupuesto (ADR-010): límites mensuales por canal/proveedor y bloqueo previo a la llamada.
3. H3 · Guion + Fact-check.

## Cómo probar
```bash
uv sync --frozen --python 3.11
uv run python -X utf8 -m pytest -q test            # upstream + mpt2
uv run python -m mpt2 migrate && uv run python -m mpt2 import-channel channels/business-stories-en.yaml
uv run python -m mpt2 serve                          # http://127.0.0.1:8090/docs
```
