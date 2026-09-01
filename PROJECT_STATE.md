# Estado del proyecto — MoneyPrinterTurbo 2.0 (Autonomous Media Engine)

Última actualización: 2026-09-01 · Fase actual: **0 (auditoría) completada, esperando aprobación para Fase 1**

## Qué funciona hoy
- Repo = upstream harry0703/MoneyPrinterTurbo `main` (`3ade9fb`, v1.3.5 + 124 commits), sin modificaciones de código.
- Entorno reproducible: `uv sync --frozen --python 3.11` + `apt install ffmpeg`. Tests: `uv run python -X utf8 -m pytest -q test` → 865 pasan, 11 saltados.
- Pipeline original ejecutado headless con `cli.py` (dos vídeos de referencia en `storage/tasks/00000000-0000-4000-8000-00000000000{1,2}/`, no versionados).
- API original arranca con `uv run python main.py` (puerto 8080).

## Qué no funciona / limitaciones conocidas
- En el sandbox de esta sesión el proxy bloquea Pexels, Pixabay, Wikimedia, edge-tts, HuggingFace y Google. En tu máquina no aplica.
- No hay ninguna pieza del sistema 2.0 implementada todavía (por diseño: Fase 0 = solo auditoría).

## Documentos
- `docs/mpt2/00-FASE0-AUDITORIA.md` — arquitectura actual, flujo, reutilizable/modificar, gap analysis, ejecución de referencia, dependencias, problemas.
- `docs/mpt2/01-ARQUITECTURA-MVP.md` — arquitectura objetivo, carpetas, modelo de datos, proveedores 0 €, riesgos, backlog, dificultad, plan del primer vídeo, preguntas.
- `docs/mpt2/02-DECISIONES.md` — registro de decisiones.

## Siguientes pasos (bloqueados por tus respuestas a las 13 preguntas del doc 01, sección 11)
1. Aprobar/ajustar arquitectura (ADR-002…005 pendientes).
2. Fase 1 · H1: esqueleto `mpt2/` (settings, SQLite, Alembic, jobs, canal piloto YAML, CLI).
3. Fase 1 · H2: Research Agent + LLMClient estructurado.

## Cómo probar lo que hay
```bash
uv sync --frozen --python 3.11
cp config.example.toml config.toml            # ya existe localmente, gitignored
uv run python -X utf8 -m pytest -q test       # suite upstream
uv run python cli.py --video-script "Texto…" --video-source local \
  --video-materials "a.mp4,b.mp4" --voice-name no-voice --video-aspect 16:9 \
  --task-id 00000000-0000-4000-8000-000000000001
```
