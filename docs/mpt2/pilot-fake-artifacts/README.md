# Piloto ejecutado con el backend SIMULADO (no es investigación real)

Estos 13 archivos son la salida real del pipeline H2 ejecutado de extremo a extremo con
`MPT2_LLM_BACKEND=fake`, porque el entorno de desarrollo no tenía credenciales ni acceso a la
API de Anthropic. Todo el contenido está marcado `[FAKE]`, las fuentes usan dominios
`*.example.test` y **ninguna cifra es verdadera**. Sirven para verificar la forma de los
artefactos, la trazabilidad claim → fuente → sección → escena y el flujo de revisión.

Para producir los artefactos reales del piloto, ejecutar fuera del sandbox los comandos de
`docs/mpt2/04-H2-GUIA.md` §3 con `ANTHROPIC_API_KEY` en `.env`.
