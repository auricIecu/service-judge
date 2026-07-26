# service-judge — Capacidad de loop

**Estado:** diseño aprobado, sin implementar.
**Destino en el repo:** `docs/specs/LOOP-DESIGN.md`

Este documento es la fuente de verdad del rediseño. Las decisiones marcadas
como DECIDIDO no se re-litigan; si un agente cree que una es incorrecta, lo
plantea al humano antes de desviarse. Las marcadas OPEN se resuelven
empíricamente, no por argumentación.

---

## 1. Problema

`service-judge` v1.2 funciona y tiene usuarios. Encontró bugs reales en
producción: tools rotas, datos placeholder narrados como un colapso de
ventas, y guardrails disparando en preguntas legítimas — ninguno detectado
por tests basados en asserts.

Lo que **no** puede hacer hoy: decirte si un fix funcionó.

El skill es one-shot por cinco razones estructurales:

1. No persiste nada. Pack, anchors y verdicts viven en contexto; cuando la
   sesión muere, la corrida muere.
2. Regenera las preguntas en cada corrida, así que las notas entre corridas
   no son comparables.
3. Phase 5 termina y prohíbe tocar el repo.
4. El juez comparte contexto con el resto de la sesión.
5. La selección de modelo es una cascada de fallback, no un ruteo fijo. Si
   una corrida juzga con un modelo y la siguiente con otro, la diferencia de
   nota puede ser el cambio de juez.

**Objetivo:** poder correr el eval repetidamente contra un set de preguntas
congelado, y saber con confianza si la nota subió, y si algo que antes
pasaba ahora falla.

---

## 2. Fuera de alcance

- **Auto-fix.** El humano arregla; el loop mide. El valor está en la
  comparabilidad, no en la remediación automática. Se evaluará encender el
  auto-fix cuando la infraestructura lleve tiempo funcionando.
- **Cualquier diseño que exija tener Claude Code y Codex a la vez.** El
  cross-provider es bonus de calidad, no requisito.
- Reescribir el modo humano actual del skill.

---

## 3. Arquitectura: roles, no productos

Tres roles. Cada uno debe poder cumplirse con Claude Code solo, con Codex
solo, o con API key sola.

| Rol | Qué hace | Requisitos |
|---|---|---|
| **ORCHESTRATOR** | Hospeda el loop, escribe estado, gestiona iteraciones | Escritura en disco |
| **SCORER** | Nota por respuesta | Contexto limpio, sin escritura, alto volumen |
| **CROSS-ANALYST** | Contradicciones entre respuestas, narrativas alucinadas | Ve el pack completo, bajo volumen |

### Matriz de degradación

Hereda la filosofía del skill: *"Never fail hard because a capability is
missing."* Degradar y registrar la degradación en las confidence notes.

| Disponible | Orchestrator | Scorer | Cross-analyst | Caveat |
|---|---|---|---|---|
| Ambos | Claude Code *o* Codex | el otro | Claude fuerte | — (ideal) |
| Solo Claude Code | Claude Code | subagente, contexto limpio | Claude fuerte | `judge == generator provider` |
| Solo Codex | Codex | `codex exec` sesión fresca | Codex, sesión fresca | idem |
| Solo API key | `scripts/loop.py` | Batches API | 1 llamada | sin repo ni servicio local |

**El aislamiento del scorer se satisface dentro de un solo producto.** Los
subagentes de Claude Code dan contexto limpio; un `codex exec` sin `resume`
arranca de cero. Ninguno necesita al otro para cumplir el contrato.

Lo que se pierde en modo single-agent: si el servicio evaluado corre sobre
el mismo proveedor que juzga, comparten puntos ciegos. El contexto limpio
resuelve el anclaje, no la correlación. Se registra como caveat y se sigue.

---

## 4. DECIDIDO — decisiones cerradas

**D1. El loop vive en `scripts/loop.py`, no en prosa del SKILL.md.**
Si fuera prosa habría que escribirlo dos veces (skill de Claude Code y skill
de Codex) y las dos versiones divergirían en semanas. Un script, dos
wrappers finos, cero drift. Además un bucle en prosa lo ejecuta el modelo
por voluntad propia: sin garantía de terminación ni de cumplimiento del cap.

**D2. Contrato de aislamiento del scorer.**
Recibe únicamente `(rúbrica, pregunta, respuesta, anchor)`. Nunca: número de
iteración, notas previas, umbral objetivo, ni el diff aplicado.
Claude Code → subagente. Codex → `codex exec` **sin** `resume`.

> Nota: esto es deliberadamente lo contrario de lo que hace `grill-me-codex`,
> que reanuda la sesión para que el revisor recuerde sus críticas. Correcto
> para revisar un plan ("¿atendiste mi punto 3?"), veneno para puntuar.

**D3. El set de preguntas se congela.**
`questions.golden.jsonl` con flag `dev|holdout` por pregunta, split ~70/30.
Si se regeneran las preguntas cada iteración, las notas miden la varianza del
generador, no la mejora del servicio.

**D4. El holdout no se muestra a quien arregla.**
Si el que arregla ve qué preguntas fallaron, parchea esos casos concretos y
llegas al umbral sin haber mejorado nada. El número que importa no es la
nota: es el **gap dev↔holdout**. Dev 98 / holdout 79 = sobreajuste.

**D5. El modelo del juez se fija al inicio de la corrida.**
Se registra el modelo efectivo en `config.json`. Si cambia entre
iteraciones, la corrida se marca **INVÁLIDA**. No degradar en silencio: la
mejora observada podría ser el cambio de juez.

**D6. El umbral no es 98/100.**
Con scoring 0–5 sobre 30 preguntas, 98% permite perder 3 puntos de 150 — es
decir, casi todo en 5/5. El ruido típico de un juez LLM es de ±1 punto por
pregunta, lo que agrega ±8–10 puntos. El umbral quedaría dentro de la banda
de ruido y el loop terminaría cuando el juez tuviera un día generoso.

Sustituir por:
- **Hard gate (binario, 100% obligatorio):** cero verdicts ≤1, cero tools
  rotas, cero narrativas alucinadas, cero guardrails en preguntas legítimas.
  Son exactamente los hallazgos que justifican el skill.
- **Soft gate:** ≥95% de preguntas en 4–5.
- **Score:** se reporta, no se gatea.

**D7. Condiciones de parada, todas en código.**
`max_iterations` (default 5); estancamiento (<2pp de mejora en 2 iteraciones
consecutivas); regresión (si la nota baja tras un fix, el loop marca la
iteración como REGRESIÓN, **se detiene y notifica** — revertir el fix es
decisión del humano, coherente con §2: el loop mide, no remedia); presupuesto
de coste.

**D8. La rúbrica viaja con la llamada.**
En el loop, no depender de que la skill esté instalada en el proveedor del
scorer. Pasar la rúbrica inline en el prompt. Si no, aparece version skew
entre la rúbrica que el orquestador cree usar y la instalada en
`~/.codex/skills/`, y se pierden horas persiguiendo notas que no cuadran.

**D9. El modo humano actual no cambia de comportamiento.**
Todo lo nuevo entra por una bifurcación de modo API.

---

## 5. Modo API

El mismo skill, dos comportamientos. El loop necesita invocarlo N veces sin
que pregunte nada y que devuelva un objeto.

| Fase | Modo humano (hoy) | Modo API (nuevo) |
|---|---|---|
| 1 Discovery | Pide confirmación del Context Brief | Lee `config.json`, no pregunta |
| 2 Sizing | Menú 30/50/100 | Carga el golden set congelado |
| 3 Probing | Genera preguntas nuevas | Reusa el golden set; solo re-probea |
| 4 Judging | In-session o subagente | Escribe `verdicts.json` estructurado |
| 5 Report | Reporte narrativo y termina | Devuelve `grade.json` y sale |

---

## 6. Ruteo de modelos

Principio: **coste = (coste por ítem) × (N preguntas) × (M iteraciones)**.
Solo un paso multiplica en los tres ejes. Abaratar de forma pareja es el
error clásico: ahorras céntimos y pagas en calidad.

| # | Paso | Frecuencia | Modelo | Razón |
|---|---|---|---|---|
| 1 | Discovery | 1× corrida | Sonnet | Comprender repo ajeno |
| 2a | Preguntas normales (~80%) | 1× corrida | Barato / Codex | Volumen mecánico |
| 2b | Trap cases (~20%) | 1× corrida | Sonnet | Son las que encuentran bugs. No abaratar |
| 3 | Extracción de anchors | 1× corrida | Sonnet + verificación | Ver ⚠️ |
| 4 | Probing | M× | **ninguno** | Es `curl` en bucle. Script puro |
| 5 | **Scoring por respuesta** | **N × M** | **Barato, sesión fresca** | Único paso que multiplica en 3 ejes |
| 6 | Escalado de borderline (2–4) | subconjunto × M | Sonnet | Solo la banda dudosa |
| 7 | **Cross-answer pass** | 1× iteración | **El más fuerte** | Necesita verlo todo junto. Aquí se paga solo |
| 8 | Decidir el fix | 1× iteración | Humano (por ahora) | Fuera de alcance |
| 9 | Reporte y ROI | 1× corrida | El más fuerte | Una vez |

⚠️ **El paso 3 es el fallo más caro del sistema.** Un anchor incorrecto hace
que una respuesta correcta puntúe 1/5; el humano "arregla" algo que no está
roto y rompe código sano. Mitigación: dos consultas SQL independientes para
el mismo número. Si discrepan, ese anchor se marca "sin ground truth" en vez
de contaminar el grade.

**Escalado del paso 6:** el scorer barato puntúa las N preguntas. Los 0–1 y
los 5 quedan resueltos. Solo la banda 2–4 escala al modelo bueno — suele ser
20–30% del set.

**Telemetría gratis:** `claude -p --output-format json` devuelve
`total_cost_usd` por invocación. Loguearlo por paso en `history.json` desde
el día uno, para validar este ruteo con números reales en vez de supuestos.

---

## 7. Estado en disco

El contexto se muere; el estado no puede.

```
.service-judge/
├── questions.golden.jsonl   # congelado, flag dev|holdout — FUERA de run-<id>:
│                            # es el examen compartido; cada run lo referencia
│                            # por sha256 en su config.json
└── run-<id>/
    ├── config.json          # modelos fijados, umbral, max_iter, auth, sha256 del golden set
    ├── raw/                 # gitignored — datos reales de clientes
    │   └── anchors.snapshot.json   # con timestamp
    ├── iter-01/
    │   ├── raw/pack.jsonl   # gitignored — respuestas del servicio
    │   ├── verdicts.json    # notas por pregunta
    │   ├── grade.json       # agregado
    │   └── fix.patch        # qué cambió el humano
    ├── iter-02/ ...
    └── history.json         # nota por pregunta por iteración
```

`history.json` es el que permite decir *"arregladas 4, rotas 2, sin cambio
24"*. El agregado solo diría "94 → 96" y ocultaría que rompiste dos cosas.

### Qué se commitea

| Archivo | Destino | Razón |
|---|---|---|
| `questions.golden.jsonl` | **commit, en `main`** | Es el activo; los worktrees paralelos deben compartir el mismo examen |
| `history.json`, `grade.json` | commit | Pequeños, son el histórico |
| `raw/` (pack.jsonl, anchors.snapshot.json) | **`.gitignore`** | Datos reales de clientes. Nunca en git |

Regla de ignore: **un solo patrón** — `.service-judge/**/raw/`. Todo lo
sensible vive bajo `raw/`; nada de negaciones (`!`) mezclando commit e ignore
en el mismo nivel — un patrón mal escrito y datos de cliente acaban en git.
El `.gitignore` actual usa `eval-runs/`; se añade el patrón nuevo en la
Tarea 5.

---

## 8. Reglas duras preservadas

Del SKILL.md actual, intactas:

1. DB solo `SELECT`. Nunca DDL/DML.
2. Nunca imprimir credenciales ni connection strings.
3. Todo probe tagueado con ID `eval-`.
4. Confirmar entorno (staging/producción) antes de probear.
5. Output al usuario en su idioma; trabajo interno en inglés.

Regla reescrita:

- **Antes:** "el juez debe ser el Claude más fuerte disponible."
- **Ahora:** "el juez debe ser de tier igual o superior al modelo que generó
  las respuestas, **de cualquier proveedor**." Si no, el diseño
  cross-provider viola la regla en la letra sin violarla en el espíritu.

**Alcance de la regla de tier** (si no, contradice el paso 5 del ruteo): la
regla aplica a quien emite el juicio decisivo — el escalado de borderline
(paso 6) y el cross-analyst (paso 7). El scorer barato del paso 5 es triage;
sus extremos (0–1 y 5) se aceptan como finales **condicionado a OPEN-2**: si
el acuerdo entre jueces medido sale bajo, los extremos también escalan y el
ruteo se recalcula.

---

## 9. Estructura objetivo del repo

```
service-judge/
├── .claude-plugin/          # plugin.json + marketplace.json (Claude Code)
├── <manifiesto Codex>       # OPEN-3: verificar ruta y schema
├── skills/
│   ├── service-judge/SKILL.md
│   └── service-judge-loop/SKILL.md
├── shared/references/       # rubric.md, judging.md, questions.md, discovery.md
├── scripts/
│   ├── loop.py              # orquestador agnóstico
│   ├── detect.py            # qué hay instalado y autenticado
│   ├── batch_eval.py        # existente
│   └── providers/
│       ├── base.py          # interfaz: score(items) -> verdicts
│       ├── codex.py
│       ├── claude_cli.py
│       └── anthropic_api.py
├── assets/report-template.md
├── AGENTS.md
└── docs/specs/LOOP-DESIGN.md
```

**Ambos productos tienen sistema de plugins.** No es "plugin de Claude Code
o portabilidad a Codex": es doble manifiesto en el mismo repo. Los formatos
son distintos y viven en rutas distintas, así que conviven.

---

## 10. Comandos de referencia

**Scorer vía Codex** (fresco, read-only, cada iteración):
```bash
codex exec -s read-only "$(cat iter-$N/score-prompt.txt)" > iter-$N/verdicts.raw
```

**Critic vía Codex** (reanudado, solo si se añade el rol):
```bash
codex exec resume "$SESSION" -c sandbox_mode="read-only" "..."
```
El `-c` en vez de `-s` porque `resume` no acepta `-s`, y sin forzarlo hereda
el default de `config.toml`, que puede ser `danger-full-access`.

**Cross-analyst vía Claude Code:**
```bash
claude -p "$(cat cross-prompt.txt)" \
  --model opus --output-format json \
  --max-turns 12 --allowedTools "Read,Grep" > cross.json
```
`--max-turns` sale con error al alcanzar el límite: tratarlo como señal, no
taparlo. `--model` acepta alias o nombre completo y sobreescribe
`ANTHROPIC_MODEL`.

---

## 11. Roadmap

| # | Tarea | Depende de | Breaking |
|---|---|---|---|
| 0 | Reconciliar versión y taguear el estado actual | — | no |
| 1 | Extraer `rubric.md` con anclas; `batch_eval.py` la carga del archivo; añadir `grade.json` a Phase 5 | 0 | no |
| 2 | `detect.py` + `providers/base.py` + `anthropic_api.py` (refactor de `batch_eval.py`) | 1 | no |
| 3 | Congelar golden set + split dev/holdout en Phase 2 | 1 | no |
| 4 | Modo API en `service-judge` | 1, 3 | no |
| 5 | `scripts/loop.py` con las 4 condiciones de parada + patrón `raw/` en `.gitignore` | 2, 4 | no |
| 6 | Mover `SKILL.md` a `skills/` + doble manifiesto + `codex.py`/`claude_cli.py` | 5 | **sí** |

Notas de la revisión contra el repo (2026-07-25):

- **Tarea 0:** el doc decía "tag v1.2", pero el repo tiene tag `v1.1` y el
  commit se describe "v1.0". Reconciliar cuál es la verdad antes de taguear.
- **Tarea 1:** el drift que D8 teme **ya existe**: hay una rúbrica hardcodeada
  en la constante `RUBRIC` de `batch_eval.py` y otra en prosa en
  `references/judging.md`. Extraer `rubric.md` incluye que `batch_eval.py` la
  lea del archivo — si no, se crea la tercera copia.
- **Tarea 2 (recortada):** `batch_eval.py` ya cumple el contrato D2 — cada
  request del batch es independiente y recibe solo rúbrica+anchors+
  pregunta+respuesta. `anthropic_api.py` es un refactor, no código nuevo.
  `codex.py` y `claude_cli.py` se difieren a la Tarea 6: cross-provider es
  bonus (§2) y las Tareas 3–5 no los necesitan. El loop llega antes.

Las tareas 1–5 son aditivas: no cambian el comportamiento observable del
modo humano. La 6 es el único cambio breaking — rompe los comandos de
instalación del README, y va en su propia rama con el README actualizado en
el mismo commit.

**Verificación de regresión:** el propio skill es el test. Correr el eval
sobre un servicio antes del refactor y guardar el reporte; repetir después.
Si es equivalente, el refactor está limpio.

---

## 12. OPEN — resolver empíricamente, no por argumentación

**OPEN-1. ¿Cuál es el ruido real del juez?**
Correr el scorer 3 veces sobre el mismo pack y medir la dispersión. Ese
número define el umbral máximo que tiene sentido pedir y valida (o tumba)
D6. Coste: una tarde. Bloquea el diseño del gate.

**OPEN-2. ¿Cuánto coinciden Codex y Claude como jueces?**
Ambos sobre el mismo pack; medir % de acuerdo por verdict. Si es alto, el
escalado del paso 6 es seguro y se puede subir el corte. Se obtiene de la
misma corrida que OPEN-1.

**OPEN-3. Manifiesto de plugin de Codex.**
Verificar ruta y schema exactos en `developers.openai.com/codex/plugins`. No
asumir simetría con el formato de Anthropic.

**OPEN-4. ¿Funciona el anidamiento Codex → Claude?**
```bash
codex exec -s workspace-write 'Ejecuta: claude -p "di solo OK" --output-format json'
```
Si sale el JSON, "Codex orquesta" va como soportado en la matriz de §3. Si
falla por red o permisos, va como experimental y se documenta la
configuración de sandbox necesaria. Un minuto.

**OPEN-5. Auth de Codex: cuenta ChatGPT vs API key.**
Con auth de cuenta ChatGPT no se pueden pinnear variantes de modelo, hay que
usar el default de config. Eso choca con D5. Si no se puede pinnear:
registrar el modelo efectivo por iteración y abortar si cambia. Decisión de
setup, tomar antes de construir.

---

## Procedencia

Diseñado en conversación, julio 2026. El razonamiento está preservado a
propósito: sin él, un agente futuro re-litiga D2 (aislamiento), D6 (umbral) y
D8 (rúbrica inline), que son las tres decisiones contraintuitivas.

Enmendado 2026-07-25 tras revisión contra el repo: golden set fuera de
`run-<id>/` (§7), sin auto-revert en D7, alcance de la regla de tier (§8),
layout `raw/` para el ignore (§7), reuso de `batch_eval.py` y deferral de
adapters extra (§11).