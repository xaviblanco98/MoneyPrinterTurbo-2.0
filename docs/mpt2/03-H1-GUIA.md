# Fase 1 · Hito H1 — Cimientos persistentes de MPT2

Estado: implementado. Este documento explica qué contiene H1, cómo ejecutarlo y cómo probarlo.
No incluye investigación, guion, storyboard, recursos, voz ni render: eso llega en H2+.

## 1. Qué es `mpt2/`

Un paquete Python **aditivo** junto al código upstream (`app/`). No modifica el pipeline
original: MoneyPrinterTurbo sigue arrancando con `python main.py`, `webui.sh` y `cli.py`.

```
mpt2/
├── settings.py        # configuración desde variables de entorno (MPT2_*), validada al arrancar
├── errors.py          # ErrorInfo {code, message, module, occurred_at} y excepciones
├── db.py              # engine SQLite (WAL, FK), sesiones, helpers de migración Alembic
├── models/            # 15 tablas SQLAlchemy 2.0 (ver §3)
├── state_machine.py   # transiciones válidas de VideoProject, historial
├── jobs.py            # cola de trabajos durable: idempotencia, reintentos, recuperación
├── channels/          # ChannelConfig (Pydantic) y carga de YAML
├── contracts.py       # Protocol de los 8 módulos futuros + modelos de intercambio
├── providers/llm.py   # adaptador LLMProvider sobre el registro LLM upstream (Ollama, etc.)
├── services.py        # operaciones de negocio (canal, proyecto, aprobación, coste)
├── api/               # FastAPI /api/v2
├── migrations/        # Alembic (alembic.ini, env.py, versions/0001_*.py)
└── __main__.py        # python -m mpt2 {check-config, migrate, import-channel, serve}
```

## 2. Ejecutar en local

```bash
uv sync --frozen --python 3.11          # instala también sqlalchemy y alembic
cp .env.example .env                    # opcional; sin .env se usan los valores por defecto
uv run python -m mpt2 check-config      # valida entorno, muestra settings con secretos ocultos
uv run python -m mpt2 migrate           # crea storage/mpt2/mpt2.sqlite3 desde cero (revisión 0001)
uv run python -m mpt2 import-channel channels/business-stories-en.yaml
uv run python -m mpt2 serve             # API en http://127.0.0.1:8090/docs
```

Ejemplo de uso de la API:

```bash
curl -s -X POST localhost:8090/api/v2/projects -H 'content-type: application/json' \
  -d '{"channel_id":"business-stories-en","title":"WeWork","topic":"How WeWork lost billions"}'
curl -s localhost:8090/api/v2/projects/<id>/state
curl -s -X POST localhost:8090/api/v2/projects/<id>/transition -d '{"to_state":"researching"}' -H 'content-type: application/json'
curl -s -X POST localhost:8090/api/v2/projects/<id>/approvals \
  -d '{"stage":"final","decision":"approve","reviewer":"xavi"}' -H 'content-type: application/json'
```

Si `MPT2_API_KEY` está definido, todas las rutas salvo `/api/v2/health` exigen la cabecera `x-api-key`.

## 3. Modelo de datos (revisión Alembic `0001`)

| Tabla | Modelo | Notas |
|---|---|---|
| channels | Channel | slug único; listas en JSON; sin secretos |
| video_projects | VideoProject | id permanente (uuid4 hex); `state`, `failed_from_state` |
| project_state_transitions | ProjectStateTransition | historial append-only: from, to, actor, reason, instante |
| research_sources / research_claims / research_claim_sources | ResearchSource, ResearchClaim | claims ↔ fuentes N:M; `kind` fact/estimate/opinion/inference |
| scripts / script_sections | Script, ScriptSection | versión única por proyecto; secciones con rol y `claim_ids` |
| storyboards / scenes | Storyboard, Scene | escenas con tipo de recurso (14 tipos), términos, texto en pantalla |
| assets | Asset | licencia obligatoria (por defecto `unknown`), autor, atribución, hash |
| pipeline_jobs | PipelineJob | clave de idempotencia única, intentos, `next_run_at`, lock, error estructurado |
| quality_checks | QualityCheck | lista de comprobaciones, puntuación, estado al que se devuelve |
| human_approvals | HumanApproval | etapa, decisión, revisor, estado resultante |
| cost_entries | CostEntry | proveedor, etapa, unidades, tipo, coste estimado en EUR |

## 4. Máquina de estados

```
idea ─► researching ─► script_draft ─► fact_check ─► storyboard ─► assets ─► voice ─► rendering ─► quality_control ─► awaiting_approval ─► approved ─► completed
  │                        ▲              │                                                              │                       │
  └► rejected ◄────────────┼──────────────┘ (fact_check → script_draft)                                  │                       └► rejected ─► idea | script_draft
                           └─────────────── quality_control → script_draft | storyboard | assets | voice | rendering
Cualquier estado de trabajo ─► failed ─► (solo) el estado desde el que falló
```

Reglas: no hay saltos; `failed` guarda `failed_from_state` y solo puede reanudarse a ese
estado; `completed` es terminal; cada transición queda en `project_state_transitions`.
Una aprobación humana con `stage=final` sobre un proyecto en `awaiting_approval` aplica
`approved` o `rejected`; el resto de aprobaciones solo se registran.

## 5. Cola de trabajos

- `enqueue()` es idempotente por `idempotency_key` (por defecto `proyecto:etapa:sha256(payload)`).
- `claim_next()` bloquea el trabajo con `locked_by`/`locked_at`; `run_one()` lo ejecuta en su propia transacción.
- Fallo: guarda `error_code`, `error_message`, `error_module`, `error_at`; reintenta con backoff exponencial
  (`retry_base * 2^(intentos-1)`) hasta `max_attempts`; `StageError(retryable=False)` falla de inmediato.
- `recover_stale()` devuelve a la cola los trabajos `running` cuyo lock supera `MPT2_JOB_STALE_LOCK_SECONDS` (reinicio del proceso).
- `escalate()` marca `needs_human`.

## 6. Contratos

`mpt2/contracts.py` define `Protocol` comprobables en tiempo de ejecución para
ResearchProvider, LLMProvider, FactChecker, StoryboardGenerator, AssetProvider,
VoiceProvider, Renderer y QualityChecker, con modelos Pydantic de entrada/salida.
`ProviderRegistry` permite intercambiar implementaciones por nombre.
`UpstreamLLMProvider` demuestra la reutilización: construye desde el entorno el snapshot que
espera `app/services/llm.py` y convierte los `"Error: ..."` upstream en excepciones.

## 7. Pruebas

```bash
uv run python -X utf8 -m pytest -q test/mpt2      # solo mpt2
uv run python -X utf8 -m pytest -q test           # todo (upstream + mpt2)
uv run ruff check app mpt2 cli.py main.py webui test
```

Cobertura: `test/mpt2/` prueba settings, secretos, configuración de canal, migraciones
(desde cero, idempotencia, downgrade, coincidencia con los modelos), modelos, transiciones
válidas e inválidas, cola de trabajos, persistencia tras reinicio, API y contratos.
