# Estado del proyecto — MoneyPrinterTurbo 2.0 (Autonomous Media Engine)

Última actualización: 2026-09-02 · Fase actual: **Fase 1 · H2 implementado (pipeline editorial), esperando aprobación para H3**

## Qué funciona hoy
- **Upstream intacto**: `app/`, `cli.py`, `webui/`, `main.py` sin cambios. `python main.py` arranca. Suite upstream: 865 tests pasan.
- **H1**: settings por entorno, SQLite + Alembic, máquina de estados, cola durable, contratos, API `/api/v2`, CLI.
- **H2 (nuevo)**: pipeline editorial completo de 10 etapas sobre la cola de H1:
  `research_plan → research_search → research_dossier → claims_extract → claims_verify → angles_hooks → script_write → script_fact_check → storyboard → editorial_qc → editorial_review`.
  - Proveedor LLM: **API oficial de Anthropic** (SDK 1.3.0) con modelos configurables por tier/tarea (Haiku 4.5 para tareas simples, Opus 5 para investigación, razonamiento y guion), salidas estructuradas validadas, caché, idempotencia, timeouts, reintentos acotados, telemetría de tokens/modelo/latencia/coste; **búsqueda web nativa** de la API con citas, dominios permitidos/prohibidos, canonización y deduplicación de URLs. `ResearchProvider` intercambiable.
  - **Control de coste**: límites por llamada (2 €), proyecto (30 €), mes (1.000 € duro, 100 € aviso), editables por admin, nunca eliminables; bloqueo previo a la llamada; todo gasto en `cost_entries`/`llm_calls`.
  - Claims trazables (tipo, importancia, confianza, fuentes de apoyo/contradicción, evidencia, ámbito, periodo, estado), fact-check en dos fases, ángulos/hooks/estructuras puntuados, guion 6–8 min con `claim_ids` por sección, storyboard por escenas con datos de gráfico, QC editorial (automático + LLM), revisión humana (editar claims, elegir hook, editar secciones y escenas, aprobar/rechazar, rerun por etapa), worker reanudable, 13 artefactos exportables.
- **Tests**: 157 en `test/mpt2` (155 pasan, 2 saltados: test real opcional y ruta OpenAPI alternativa); suite completa 1.020 pasan, 13 saltados; cobertura de ramas 83 % (umbral CI 70 %). `ruff` y `compileall` limpios.

## Integración real vs simulada (importante)
- La integración con Anthropic está **implementada según la documentación oficial y probada con objetos del SDK simulados** (`test/mpt2/test_anthropic_backend.py`). **No se ha ejecutado contra la API real** porque el sandbox no tenía `ANTHROPIC_API_KEY` ni red. El piloto completo se ejecutó con `MPT2_LLM_BACKEND=fake`; sus artefactos (marcados `[FAKE]`) están en `docs/mpt2/pilot-fake-artifacts/`.
- Comando para la prueba real: ver `docs/mpt2/04-H2-GUIA.md` §6. Test live opcional: `MPT2_RUN_REAL_LLM_TESTS=1`.

## Qué no existe todavía (H3+)
- Búsqueda/descarga de recursos visuales, voz, render, publicación, analítica, panel HTMX, multi-canal.
- Proveedores de búsqueda alternativos (Tavily/Brave): solo el contrato.
- Fallbacks de refusal del lado servidor de Anthropic: documentados, no activados.

## Limitaciones conocidas
- Un solo worker (SQLite, un escritor). `SELECT FOR UPDATE` no aplica en SQLite.
- La estimación de coste previa es conservadora (asume `max_tokens` de salida); con límites bajos puede bloquear llamadas baratas. Ajustable por admin.
- Con el proveedor real, la calidad del plan/dossier/guion depende del modelo; los umbrales de QC (`quality_threshold` del canal) deben calibrarse con el primer piloto real.
- Las fuentes se limitan a lo que la herramienta de búsqueda cita (fragmentos ≤150 caracteres); no se descarga el texto completo de las páginas (respeta ToS; `web_fetch` queda como opción para H3+).

## Documentos
- `docs/mpt2/00-FASE0-AUDITORIA.md` · `01-ARQUITECTURA-MVP.md` · `02-DECISIONES.md` (ADR-001…015) · `03-H1-GUIA.md` · `04-H2-GUIA.md` · `pilot-fake-artifacts/`.

## Siguientes pasos (bloqueados por aprobación)
1. Ejecutar el piloto real con `ANTHROPIC_API_KEY` y revisar el paquete editorial (coste estimado 1–4 €).
2. H3: recursos visuales (Pexels/Pixabay/Wikimedia/Openverse con licencia), tarjetas y gráficos generados; voz.
3. Guard de presupuesto: informe mensual y alertas por canal.

## Cómo probar
```bash
uv sync --frozen --python 3.11
uv run python -X utf8 -m pytest -q test                   # upstream + mpt2
MPT2_LLM_BACKEND=fake uv run python -m mpt2 migrate
MPT2_LLM_BACKEND=fake uv run python -m mpt2 import-channel channels/business-stories-en.yaml
MPT2_LLM_BACKEND=fake uv run python -m mpt2 project create --channel business-stories-en --title "Pilot"
MPT2_LLM_BACKEND=fake uv run python -m mpt2 project run <id> && uv run python -m mpt2 project export <id>
```
