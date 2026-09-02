# Fase 1 · Hito H2 — Pipeline editorial: investigación → guion → storyboard → revisión

Estado: implementado y probado con proveedor simulado. La integración real con la API de
Anthropic está implementada según la documentación oficial del SDK 1.x y se ejecuta con
`ANTHROPIC_API_KEY`; en el sandbox de desarrollo no había credenciales ni red, así que la
prueba real queda como comando reproducible (§6). H2 termina en `editorial_review`: no busca
recursos visuales, no genera voz ni renderiza.

## 1. Flujo implementado

```
idea ─(run)─► researching ──────────────────────────────► script_draft ─────────► fact_check ──► storyboard ────────────► editorial_review
              research_plan → research_search →           angles_hooks →           script_fact_check  storyboard → editorial_qc      ▲ humano: aprobar → assets
              research_dossier → claims_extract →         script_write                                                              │ rechazar → rejected
              claims_verify                                                                                                           │ editar/rerun → vuelve a la etapa
```

| Etapa | Modelo (tier) | Qué hace | Salida validada |
|---|---|---|---|
| research_plan | smart | Pregunta central, subpreguntas, información necesaria, fuentes ideales, riesgos, hipótesis (incluida la que refuta el título), estrategia de búsqueda | `ResearchPlanOut` |
| research_search | fast + web_search | Una búsqueda por consulta del plan; canoniza URLs, deduplica, aplica dominios permitidos/prohibidos, rechaza fuentes sin extracto; registra cada consulta | `SearchQuery`, `ResearchSource` |
| research_dossier | smart | Resumen ejecutivo, hechos con fuentes, cronología, cifras, contradicciones, lagunas, riesgos, hipótesis descartadas, evaluación del título | `DossierOut` |
| claims_extract | fast | Claims atómicos con tipo, importancia, confianza, fuentes de apoyo/contradicción, evidencia literal, ámbito y periodo | `ClaimsOut` |
| claims_verify | smart | Veredicto por claim + reglas automáticas (numérico sin evidencia → unsupported; importante con una sola fuente secundaria → weak; contradicciones → disputed) | `FactCheckOut` |
| angles_hooks | smart | 5 ángulos, 10 hooks, 3 estructuras puntuados en 10 dimensiones; recomendación explicada | `AnglesHooksOut` |
| script_write | smart | Guion por secciones (hook…cta) con `claim_ids`; palabras y duración estimada | `ScriptOut` |
| script_fact_check | smart (solo si hay problemas) | Comprueba que cada número del guion aparece en los claims referenciados; una reparación acotada; marca secciones `needs_verification` | `QualityCheck(kind=fact_check)` |
| storyboard | fast | Escenas de 3–8 s por sección con visual concreto, tipo de recurso, términos, texto en pantalla, datos de gráfico, claims y fuentes | `StoryboardSectionOut` |
| editorial_qc | smart | Pruebas automáticas (fuentes, trazabilidad, duración, cobertura del storyboard…) + evaluación subjetiva del LLM, separadas por `kind` | `QualityCheck(kind=editorial)` |

Reglas duras: ningún número entra en el guion sin claim; los claims críticos usados sin respaldo
bloquean la aprobación; nunca se inventan fuentes (si la búsqueda no da ≥3 fuentes válidas el
proyecto falla a `needs_human`); el contenido web se marca como `<untrusted_data>` y nunca se
ejecuta como instrucción.

## 2. Proveedor LLM y control de coste

- `mpt2/llm/backend.py`: `AnthropicBackend` (SDK oficial `anthropic` 1.3.0): `messages.create` con
  `output_config.format=json_schema` para salidas estructuradas, herramienta `web_search` (versión
  configurable, por defecto `web_search_20250305`, `max_uses`, `allowed_domains`/`blocked_domains`,
  `user_location`), continuación de `pause_turn`, clasificación de errores del SDK. `FakeBackend`
  para tests.
- `mpt2/llm/client.py`: modelo por tarea (`MPT2_MODEL_FAST`/`MPT2_MODEL_SMART`, `MPT2_LLM_TASK_TIERS`,
  `MPT2_LLM_TASK_MODELS`, `MPT2_LLM_TASK_EFFORT`), timeout, reintentos con backoff acotados,
  validación Pydantic con una reparación, caché exacta (`llm_cache`), clave de idempotencia,
  telemetría completa en `llm_calls` (tokens, modelo, latencia, coste, cache_hit, error) y
  asiento en `cost_entries`. Los prompts no se persisten (solo hash y longitud) y un prompt que
  contenga una clave configurada se rechaza.
- `mpt2/llm/pricing.py`: tabla de precios (USD/M tokens, 10 $/1.000 búsquedas) ampliable con
  `MPT2_LLM_PRICING_JSON`; un modelo sin precio bloquea la llamada.
- `mpt2/costs/guard.py`: límites en EUR, comprobados **antes** de cada llamada con estimación
  de coste máximo: por llamada (2 €), por proyecto (30 €), duro mensual (1.000 €) y aviso (100 €).
  El administrador los cambia con `PUT /api/v2/budget` o `python -m mpt2 budget set`; nunca se
  pueden poner a cero ni eliminar.

Claude ≠ créditos de API: la suscripción de Claude.ai no da acceso a la API. Hace falta una
clave de la Console de Anthropic con créditos prepagados; el gasto se ve allí y aquí en
`cost-report.json`.

## 3. Ejecutar

```bash
uv sync --frozen --python 3.11
cp .env.example .env          # poner ANTHROPIC_API_KEY=... en .env (nunca en git)
uv run python -m mpt2 migrate
uv run python -m mpt2 import-channel channels/business-stories-en.yaml
uv run python -m mpt2 project create --channel business-stories-en \
  --title "The Hidden Business Model of Car Dealerships: Where the Money Is Really Made" \
  --topic "How car dealerships actually make money" \
  --required-topic "Margin on new and used vehicle sales" \
  --required-topic "Financing and dealer reserve" --required-topic "F&I products" \
  --required-topic "Insurance and warranties" --required-topic "Service, maintenance and parts" \
  --required-topic "Manufacturer incentives" --required-topic "Inventory turnover" \
  --required-topic "Differences between the United States and Europe" \
  --required-topic "Situations where 'they make more on financing' is not true"
uv run python -m mpt2 project run <project_id>        # ejecuta las 10 etapas hasta quedar en editorial_review
uv run python -m mpt2 project status <project_id>     # estado, trabajos, bloqueos, coste
uv run python -m mpt2 project export <project_id>     # storage/mpt2/projects/<id>/*.json|md
uv run python -m mpt2 project review <project_id> --stage package --decision approve --reviewer xavi
uv run python -m mpt2 worker                          # worker permanente (Ctrl-C para parar; reanuda al reiniciar)
```

Modo offline sin coste: `MPT2_LLM_BACKEND=fake` usa el backend simulado (contenido marcado `[FAKE]`).

## 4. Revisión humana (API)

| Acción | Endpoint |
|---|---|
| Ver plan / búsquedas / fuentes / dossier / claims / opciones / guion / storyboard / QC / coste | `GET /api/v2/projects/{id}/{research-plan,search-log,sources,dossier,claims,options,script,storyboard,quality,cost-report,llm-calls}` |
| Editar o descartar claim | `PATCH /api/v2/claims/{id}` |
| Elegir hook, ángulo o estructura | `POST /api/v2/options/{id}/select` |
| Editar sección del guion (conserva claims) | `PATCH /api/v2/sections/{id}` |
| Editar escena | `PATCH /api/v2/scenes/{id}` |
| Bloqueos y auditoría de ediciones | `GET /api/v2/projects/{id}/review` |
| Aprobar/rechazar (research, claims, hook, script, storyboard, package) | `POST /api/v2/projects/{id}/review` |
| Rerun desde una etapa (sube `pipeline_run`) | `POST /api/v2/projects/{id}/rerun {"stage": "script_write"}` |
| Reanudar tras fallo | `POST /api/v2/projects/{id}/run` |
| Presupuesto | `GET/PUT /api/v2/budget` |

`package approve` pasa a `assets` solo sin bloqueos; `reject` pasa a `rejected`. Toda edición
queda en `review_events` con actor, fecha, motivo y diff.

## 5. Artefactos (`storage/mpt2/projects/<id>/`)

`research-plan.json`, `search-log.json`, `sources.json`, `research.json`, `research.md`,
`claims.json`, `angles.json`, `hooks.json`, `script.json`, `script.md`, `storyboard.json`,
`editorial-qc.json`, `cost-report.json`. La base de datos es la fuente de verdad.

## 6. Prueba real fuera del sandbox

```bash
export ANTHROPIC_API_KEY=sk-ant-...            # o en .env
export MPT2_RUN_REAL_LLM_TESTS=1
uv run python -X utf8 -m pytest -q test/mpt2/test_real_anthropic.py   # 2 llamadas, < 0,05 €
```
Y el piloto completo: los comandos de §3 con `MPT2_LLM_BACKEND=anthropic` (valor por defecto).
Coste estimado del piloto con Haiku 4.5 (fast) + Opus 5 (smart) y ~10–20 búsquedas: 1–4 €.

## 7. Tests

```bash
uv run python -X utf8 -m pytest -q test/mpt2      # H1 + H2
uv run python -X utf8 -m pytest -q test           # + upstream
```
Cubren: cliente LLM (routing, caché, reparación, reintentos acotados, secretos), presupuesto
(límites, cambio por admin, bloqueo por llamada/proyecto/mes), proveedor de búsqueda (dedup,
canonización, dominios, fallos sin fabricar), backend Anthropic con objetos del SDK simulados,
pipeline completo, trazabilidad claim→fuente→sección→escena, idempotencia, reanudación tras
caída, fallo y resume, bloqueo de aprobación por claim crítico, selección humana de hook,
rerun por etapa, CLI, y un test real opcional.
