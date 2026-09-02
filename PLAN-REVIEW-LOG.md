# Plan Review Log: Service Judge 2.0

Acto 1 (grill) completo — plan cerrado con el usuario. MAX_ROUNDS=5.

Ocho decisiones resueltas en el grill: (1) tres tramos en vez de 2.0 monolítico;
(2) sin resolución automática del modelo más capaz; (3) porcentajes sobre anchored
y hard gates sobre todas; (4) sin compatibilidad de runs v1; (5) `fix-brief.json`
dev-only y `gap_pp` indicativo en autopilot; (6) rama dedicada con un commit por
iteración; (7) plantilla `judge_cmd` en vez de tres adaptadores; (8) handoff del
baseline sin re-sondear con el holdout marcado como expuesto.

## Round 1 — Codex

No modifiqué archivos. `git status` ya venía sucio en `PLAN.md` y `PLAN-REVIEW-LOG.md`. El self-check actual no pudo completarse porque el sandbox no tiene directorio temporal usable.

**Hallazgos**

- `PLAN.md:41-56` usa `v["unanchored"]` como fuente para excluir preguntas del gate, pero ese campo lo escribe el juez; es un dato no confiable y puede sacar preguntas difíciles del denominador. Fix: derivar `anchored` desde el archivo de anchors/golden set y solo validar que el verdict no lo contradiga.

- `PLAN.md:55-65` no define qué pasa si no hay anchored en dev u holdout: métricas `null`, `min_holdout_score_pct` y `max_dev_holdout_gap_pp` pueden aprobar o romper de forma accidental. Fix: exigir al menos un anchored total y un anchored holdout para certificar, y hacer fallar objetivos con `actual: null`.

- `PLAN.md:68-72` cambia `should_stop` a `goals.met`, pero `should_stop(history, max_iterations)` no recibe config ni goals (`loop.py:114-135`); el plan no especifica dónde se recalculan ni cómo se maneja history viejo. Fix: calcular `goals` dentro de `grade` y hacer que `should_stop` consuma solo `last["goals"]["met"]`, rechazando grades sin esa clave bajo schema v2.

- `PLAN.md:33-37` dice “además de lo que ya valida”, pero el código actual no valida `verdict` ni `unanchored`; incluso `v["verdict"]` puede lanzar `KeyError` y tumbar el run (`loop.py:64-67`). Fix: validar presencia/tipo/valores permitidos de `verdict`, `unanchored` e `improvement_comment` antes de construir `per_question`.

- `PLAN.md:83-87` afirma que el handoff no requiere código nuevo, pero con `schema_version == 2` cualquier config heredada del one-off será rechazada antes de finalizar (`PLAN.md:75-78` vs `loop.py:332-344`). Fix: el handoff debe crear explícitamente un `config.json` v2 completo con `goals`, `schema_version` y `baseline_holdout_exposed`.

- `PLAN.md:113-117` quiere `fix-brief.json` con `improvement_comment`, pero `compute_grade` descarta ese campo y `per_question` solo conserva score/verdict/flags (`loop.py:64-67`, `loop.py:483-494`). Fix: preservar `improvement_comment` saneado en `per_question` o generar el brief directamente desde verdicts validados.

- `PLAN.md:126-132` promete commits por iteración, pero las reglas actuales del skill dicen que el loop mide y espera al humano, y que `grade.json`/`history.json` son seguros de commitear (`service-judge-loop/SKILL.md:104-118`), aunque `grade.json` contiene comentarios y hallazgos que pueden exponer datos. Fix: cambiar la política: no commitear artefactos de evaluación por defecto; commitear solo código/fix metadata redactada y mantener `.service-judge/` gitignored.

- `PLAN.md:134-147` dice que `autonomy` es auditoría, no autoridad, pero no define un mecanismo persistente para enlazar la autorización conversacional al run. Fix: guardar un `authorization.json` mínimo con timestamp, alcance, repo, acciones permitidas y resumen textual aprobado; abortar si falta o no coincide.

- `PLAN.md:149-154` permite que una regresión focused sea prioridad del siguiente fix, pero `should_stop` hoy ignora regresión/stagnation focused (`loop.py:116-135`) y el plan no define cómo detectar regresión focused sin filtrar holdout. Fix: añadir una regla explícita: focused solo puede bloquear/crear brief de dev, nunca certificar ni calcular gap.

- `PLAN.md:163-179` propone `judge_cmd` con `shell=True` y rutas formateadas, pero no dice que esas rutas se citen con `shlex.quote`; una ruta con espacios o caracteres shell rompe o inyecta. Fix: expandir placeholders con rutas absolutas `shlex.quote(str(path))`, y testear espacios/comillas en paths.

- `PLAN.md:176-179` dice que un fallo del juez no escribe history, pero no dice qué hacer con `{out}`, `verdicts.json` o `cross-analysis.json` parcialmente escritos; el siguiente run puede reutilizar basura. Fix: escribir salidas externas en archivos temporales y hacer `rename` atómico solo tras parsear y validar.

- `PLAN.md:181-184` detecta drift comparando `judge_cmd` entre iteraciones, pero si `judge_cmd` se edita en `config.json` ya no hay valor anterior salvo en history; además `grade["judge"]` hoy es solo string (`loop.py:94-96`, `loop.py:463-465`). Fix: guardar `judge_cmd_sha256` y `judge_label` en cada grade, y comparar contra el primer full grade antes de juzgar.

- `PLAN.md:186-196` mete selección de juez externo en `service-judge`, pero `judging.md` aún exige juez “at least as strong” y subagentes sin herramientas (`judging.md:26-41`); un comando shell externo no puede garantizar eso. Fix: degradar esa garantía a advertencia auditable o mantener adaptadores cerrados para comandos oficiales.

- `PLAN.md:208-211` lista manifiestos, pero los marketplace JSON no tienen versión (`.agents/plugins/marketplace.json:1-20`, `.claude-plugin/marketplace.json:1-15`), así que “bumpearlos” no está definido. Fix: especificar exactamente qué campos cambian en cada manifiesto o eliminar esos dos del requisito de version bump.

- El plan no cubre las referencias de reporte/assets, pero cambia `grade` eliminando `soft_gate` y añadiendo dimensiones/goals; cualquier plantilla o doc que lea `percent`, `dev`, `holdout`, `soft_gate` puede quedar incoherente. Fix: buscar y actualizar todos los consumidores de `grade.json`/reporting en el mismo tramo que cambia el schema.

VERDICT: REVISE
### Claude's response

**Aceptados (14 de 15), con dos reformulados más agresivamente que la propuesta de Codex:**

- `unanchored` autorreportado por el juez → **derivado** de un `anchors.snapshot.json`
  legible por máquina (hoy `anchors.sample.md` es markdown libre, así que no había forma
  mecánica de saberlo). El juez lo sigue emitiendo, pero como aserción verificable.
- `v["verdict"]` puede lanzar `KeyError` → en vez de validarlo, **`verdict` sale del
  contrato del juez y se deriva del score**. Elimina la clase de error en lugar de cubrirla.
- Denominadores vacíos → objetivo con `actual: null` es no-cumplido; certificar exige ≥1
  anchored en dev y ≥1 en holdout; se añade el perfil opt-in `behavior-only-v1`.
- `goals` se evalúa dentro de `compute_grade`; `should_stop` no cambia de firma y lee
  `last["goals"]["met"]`; grade sin esa clave bajo v2 es inválido.
- El handoff escribe un `config.json` v2 completo (si no, el punto 5 lo rechazaría).
- `fix-brief.json` se construye desde los verdicts validados, no desde `per_question`.
- El autopilot no commitea nada bajo `.service-judge/`; se corrige la afirmación de que
  `grade.json`/`history.json` son seguros de commitear (`cross_analysis[].comment` puede
  citar datos de clientes).
- `authorization.json` como registro de auditoría: necesario, nunca suficiente.
- Invariante explícito: una focused no calcula `gap_pp`, no evalúa `goals`, no certifica.
- Rutas del `judge_cmd` absolutas y con `shlex.quote`.
- Escritura del juez a temporal + `rename` atómico tras validar.
- Drift por `judge_cmd_sha256` comparado contra el **primer** grade, no contra el config.
- Manifiestos: verificado que los dos `marketplace.json` **no tienen campo `version`**; la
  versión vive en cuatro sitios, no seis.
- Consumidores del schema: verificado que ningún `.md` lee `soft_gate`; se actualizan
  `reporting.md` y `report-template.md` con la tabla por dimensión.

**Rechazado (1):**

- *"Mantener adaptadores cerrados para comandos oficiales"* (hallazgo 13). Es re-litigar la
  decisión Q7 del grill, cuyo tradeoff se aceptó a conciencia. Sí se acepta la mitad
  correcta del hallazgo: la garantía de `judging.md` ("at least as strong", sin
  herramientas) es inverificable con un comando de shell, así que se **degrada a
  advertencia auditable** —el comando literal se imprime en el reporte— y se mantiene como
  garantía real solo en el modo por defecto de juez en sesión.

## Round 2 — Codex

La revisión sí cubre la mayoría de mis hallazgos anteriores: `unanchored` ya no se cree al juez, `verdict` se deriva, `goals` queda congelado en `grade`, paths de `judge_cmd` van quoteados, hay rename atómico, drift compara contra history, y los marketplace sin `version` ya no se “bumpean”.

Quedan problemas materiales:

- [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:67): excluir todas las unanchored de `dimensions_pct` permite certificar con 50% anchored perfecto y 50% unanchored mediocre, porque solo las bloquea `score <= 1` o flags críticos. Fix: excluir unanchored solo de `accuracy`, pero gatear `tool_choice`, `hallucination_free`, `directness` y pass-rate también sobre todas las preguntas o añadir un objetivo explícito de comportamiento unanchored.

- [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:88): `min_anchor_coverage_pct: 0` “selecciona” `behavior-only-v1`; eso convierte un valor numérico en selector de modo y puede desactivar accuracy por accidente al personalizar objetivos. Fix: exigir `goals.profile == "behavior-only-v1"` explícito y rechazar `min_anchor_coverage_pct: 0` en perfiles de producción.

- [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:279): imprimir `judge_cmd` verbatim en el reporte choca con la regla de no exponer credenciales; un usuario puede pegar tokens inline, headers, rutas sensibles o nombres de secretos. Fix: rechazar secretos inline obvios y reportar comando redactado + `cmd_sha256`, no la cadena completa.

- [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:262): el juez externo puede enviar `pack` y `anchors` a otro harness/proveedor; el plan corrige coste, pero no trata egreso de datos ni consentimiento. Fix: antes de activar `judge_cmd`, mostrar qué archivos salen al harness externo, registrar consentimiento, y marcarlo en `authorization.json`/reporte.

- [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:109): `compute_grade` ahora necesita `goals` y también el mapa de anchors, pero el plan solo explicita el primer parámetro nuevo. Fix: definir la firma completa, por ejemplo `compute_grade(..., goals, anchors, cross_analysis)` y testear anchors ausentes/mal formados.

- [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:181): autopilot asume que puede crear rama y commitear; en worktrees gestionados, detached HEAD o sandboxes read-only eso falla antes de empezar. Fix: añadir preflight git: branch actual, permisos de escritura, `git switch -c` dry-run seguro; si falla, autopilot no arranca y queda modo manual.

- [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:185): `fix.json` se quiere commitear, pero no se define ruta y a la vez se prohíbe commitear `.service-judge/`; si vive dentro del run contradice el plan, si vive fuera ensucia el repo. Fix: o eliminar `fix.json` commiteado y usar commit message/diff, o fijar una ruta no sensible y documentarla.

VERDICT: REVISE
### Claude's response

**Los siete aceptados.** Los dos primeros destaparon un hueco que yo mismo introduje en la
ronda 1 y su corrección elimina una pieza entera del plan:

- Excluir las unanchored de las **cuatro** dimensiones dejaba media prueba sin gate.
  Corregido: `accuracy`, `pass_rate`, `dev`, `holdout` y `gap_pp` van sobre anchored;
  `tool_choice`, `hallucination_free` y `directness` van sobre **todas** las preguntas,
  porque son juzgables sin ground truth.
- `min_anchor_coverage_pct: 0` como selector de modo → **se elimina `behavior-only-v1`
  entero**. Sin anchors el loop mide y no certifica, con motivo explícito. Desaparecen el
  umbral inventado del 80%, el selector implícito y una rama de código.
- Reporte con `judge_cmd` verbatim vs. la hard rule 2 → el reporte muestra `label` +
  `cmd_sha256`. Y un hallazgo derivado que el plan no tenía: `config.json` ya contiene hoy
  el `probe_cmd` con la API key del servicio, y nunca se dijo que debía gitignorearse.
- Egreso de datos a otro proveedor → consentimiento explícito antes de habilitar
  `judge_cmd`, con la lista exacta de archivos que salen, registrado en `authorization.json`.
- Firma completa: `compute_grade(verdicts, questions, judge, degradations, cross_analysis,
  goals, anchors)`.
- Preflight de git (worktree gestionado, `HEAD` detached, FS de solo lectura) → si falla,
  el autopilot no arranca y se ofrece manual. Pertinente: este repo se trabaja en worktrees.
- `fix.json` vive en el run y **no se commitea**; el registro commiteado es el mensaje del
  commit más su diff.

## Round 3 — Codex

La nueva versión arregla los siete puntos que marqué, salvo dos contradicciones internas que todavía son lo bastante materiales para pedir revisión.

- [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:281) corrige bien el reporte para mostrar `label` + `cmd_sha256`, pero [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:305) y [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:379) siguen diciendo que el comando literal se imprime en el reporte; eso reintroduce la fuga de secretos y contradice la evidencia auditable. Fix: reemplazar esas frases por “se imprime `label`, `cmd_sha256` y, si hace falta, una versión redactada”.

- [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:97) elimina `behavior-only-v1`, pero [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:172) todavía lo lista como test obligatorio. Fix: quitar ese test y reemplazarlo por “cero anchors no certifica y `goals.met` es falso”.

- [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:67) dice “todos los porcentajes” sobre anchored, pero [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:70) define correctamente dimensiones mixtas; el título empuja a implementar mal. Fix: renombrarlo a “Los porcentajes separan exactitud verificable de comportamiento juzgable”.

- [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:169) aún dice “porcentajes calculados solo sobre anchored”, contradiciendo [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:76). Fix: especificar “`accuracy`, `pass_rate`, `dev`, `holdout`, `gap_pp` sobre anchored; `tool_choice`, `hallucination_free`, `directness` sobre todas”.

VERDICT: REVISE
### Claude's response

Los cuatro aceptados sin discusión: son incoherencias internas que dejaron mis propias
ediciones de la ronda 2, no desacuerdos de diseño.

- Título del punto 3 → "Los porcentajes separan exactitud verificable de comportamiento
  juzgable"; el anterior empujaba a implementar justo el hueco que la ronda 2 cerró.
- Dos frases residuales que aún decían "el comando literal se imprime en el reporte"
  (puntos 20 y Key decisions) → `label` + `cmd_sha256` + versión redactada.
- El test de `behavior-only-v1` sobrevivía a la eliminación del perfil → sustituido por
  "cero anchors no certifica y `goals.met` es falso con motivo".
- La línea de tests que decía "porcentajes solo sobre anchored" → reparto explícito.

## Round 4 — Codex

Los cuatro hallazgos anteriores están corregidos en el cuerpo del plan: métricas mixtas bien definidas, sin `behavior-only-v1`, `judge_cmd` no se imprime literal y los tests ya no piden porcentajes "solo anchored" de forma global.

Quedan dos problemas nuevos/materiales:

- `PLAN.md:25` cambia anchors de `anchors.md` a `raw/anchors.snapshot.json`, pero el plan no dice que `references/judging.md` debe dejar de pedir `anchors.md`; hoy el juez recibe explícitamente `anchors.md`, así que Phase 4 quedaría apuntando al artefacto viejo. Fix: añadir a Tramo 1 que `references/judging.md` y el prompt `needs_judgment` pasan a usar `anchors.snapshot.json`.

- `PLAN.md:252` registra consentimiento de juez externo en `authorization.json`, pero ese archivo solo está definido claramente para autopilot/run loop; `service-judge` one-off también puede elegir juez externo y no tiene ruta de `authorization.json` especificada. Fix: definir una ruta única para one-off, por ejemplo `<artifacts-dir>/authorization.json`, y para loop `.service-judge/run-<id>/authorization.json`.

VERDICT: REVISE

### Claude's response

Aceptados los dos. Mi edición para aplicarlos **falló silenciosamente** (discrepancia de texto en el reemplazo) y la ronda siguiente corrió sobre el plan sin cambios, por eso la Round 5 los repite. Se aplicaron de verdad tras la Round 5.

## Round 5 — Codex

Las contradicciones de métricas y `judge_cmd` literal ya están corregidas. Pero los dos hallazgos previos más operativos siguen sin cierre claro:

- [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:25) cambia el artefacto de anchors a `raw/anchors.snapshot.json`, pero el plan aún no ordena actualizar `references/judging.md` ni el output `needs_judgment` de `loop.py`; hoy el juez recibe una ruta `anchors` pero la doc de juicio sigue hablando de `anchors.md`. Fix: añadir explícitamente en Tramo 1 que `judging.md` y `needs_judgment["anchors"]` usan `anchors.snapshot.json`.

- [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:252) mantiene consentimiento externo en `authorization.json`, pero sigue sin ruta para one-off `service-judge`; la ruta solo está definida por contexto para loop/autopilot. Fix: definir `<artifacts-dir>/authorization.json` para one-off y `.service-judge/run-<id>/authorization.json` para loop.

Nuevo menor pero implementable: [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:338) conserva el tradeoff “Porcentajes sobre anchored”, que contradice el diseño corregido de [PLAN.md](/Users/auric/conductor/workspaces/service-judge/lagos/PLAN.md:67). Fix: renombrar ese bullet igual que el título operativo.

VERDICT: REVISE
_Nota de proceso: la ronda 4 se gastó en vano. Mi edición de `PLAN.md` falló por una
discrepancia de texto y no llegó a escribirse, pero la ronda de Codex sí corrió, así que
revisó el plan sin cambios y repitió sus dos hallazgos. No hubo desacuerdo — hubo un error
mío de herramienta._

### Claude's response

Los tres aceptados, ya aplicados de verdad esta vez:

- `references/judging.md:36` le entrega hoy `anchors.md` al juez de forma explícita, y el
  payload `needs_judgment` de `loop.py` publica la ruta en su clave `anchors`. Verificado en
  el repo. Los dos se mueven a `anchors.snapshot.json` dentro del Tramo 1.
- El juez externo también se elige en el one-off, que no tiene run dir. Dos ubicaciones
  definidas: `.service-judge/run-<id>/authorization.json` en el loop,
  `<artifacts-dir>/authorization.json` en el one-off (`eval-runs/` por defecto).
- El bullet de Key decisions seguía titulado "Porcentajes sobre anchored", contradiciendo el
  reparto corregido en la ronda 2 → renombrado igual que el título operativo.

## Round 6 — Codex

No modifiqué archivos.

Los tres hallazgos anteriores ya están aplicados: `judging.md`/`needs_judgment` quedan ligados a `anchors.snapshot.json`, `authorization.json` tiene ruta para loop y one-off, y el tradeoff de métricas ya no dice “porcentajes sobre anchored” de forma engañosa.

No veo un blocker nuevo en `PLAN.md`. Quedan riesgos declarados, especialmente que `authorization.json` no aplica seguridad por sí mismo y que el juez externo depende del comando configurado, pero el plan ya los trata como límites explícitos y auditables, no como garantías falsas.

VERDICT: APPROVED
### Resolution

Converged. APPROVED en la ronda 6 (una de ellas, la 4, se perdió por un error de herramienta mío, no por desacuerdo). Pendiente: sign-off humano antes de escribir código.

---

## Act 3 — Build (Codex builds, Claude reviews)

**Scope:** Tramo 1 (`1.6.0`) only — PLAN.md items 1–8, plus item 23 limited to the
1.6.0 bump, plus one line pulled early from item 19 (`.gitignore` for
`.service-judge/**/config.json`: `config.json` already carries `probe_cmd` with the
evaluated service's API key and was never ignored — a live leak, not worth deferring
two releases). Tramos 2 and 3 declared out of scope in the build contract.

**Setup:** `codex-cli 0.151.0`, `gpt-5.5`, `model_reasoning_effort = high`, `--yolo`,
thread `01a05f39-c9a9-7403-8061-39fa21151dba`. Clean-tree gate satisfied by committing
the plan docs as `5a97f89`. Baseline proof green before launch.

### Round 1 — Codex build

15 files changed, +624/−102. `loop.py` +242, `test_loop.py` +266. Anchors fixture
moved from `.md` to `.json`. All 18 tests enumerated in PLAN.md item 8 present and
substantive. Proof green.

### Claude's verdict — Round 1: REVISE (3 defects)

1. **`FOCUSED PASSED` branch is dead code — correctness regression.** `main()` pops
   `goals` when `not grade["full"]`, and the branch that sets the FOCUSED PASSED
   reason requires *both* `not grade["full"]` and `grade["goals"]["met"]`. It can
   never fire. A focused iteration whose targeted questions all pass now prints
   `{"status": "needs_fix", "reason": ""}` — the exact empty-reason-with-hard-gate-true
   symptom found by hand in 1.5.0, reintroduced. Fix: predicate on `dev_fails`, not on
   goals; goals are full-run-only by items 4 and 13.
2. **`improvement_comment` copied into `per_question` — spec break and data-leak
   surface.** PLAN.md item 9 states as an existing fact that `compute_grade` discards
   it, which is *why* the Tramo 2 fix-brief must be built from the validated verdicts
   instead. Worse: `per_question` is serialized into `grade.json` and `history.json`,
   and `service-judge-loop/SKILL.md:167` still says verbatim that both are safe to
   commit — that claim is only corrected in Tramo 2 (item 11), out of scope here. Judge
   prose about real service answers must not enter those files in 1.6.0. Validation of
   the field stays; the key leaves the row.
3. **`assert ADDED_TESTS == 22`** — a self-counting assertion that proves nothing and
   will fail spuriously the moment Tramo 2 adds a test to the file.

Verified as correct and left alone: the hundredths comparison for `tool_choice: 0.5`;
`unanchored` derived from the snapshot with the judge's claim validated against it;
the anchored/all-questions metric split; `should_stop` reading `fulls[-1]` so focused
grades never hit the `INVALID_GRADE: missing goals` guard; `None`-safe REGRESSION and
STAGNATION comparisons; short-circuit ordering in the dimension validation chain (no
`TypeError` on a non-numeric dimension); v1 config rejection with return code 2.

### Round 2 — Codex fixes (same session, resumed)

All three defects fixed, no deviations. `dev_fails` moved above the branch and the
focused-passed predicate now reads it; `improvement_comment` still validated but no
longer copied into `per_question`; the self-counting assertion removed and `check()`
reduced to a named `assert`. Two new assertions: a passing focused iteration reports
`FOCUSED PASSED`, and `per_question` rows carry no `improvement_comment`.

### Claude's verdict — Round 2: APPROVED

Proof green, run by Claude, not taken from Codex's report. Beyond re-reading the diff,
five mutations were applied to `loop.py` to check the suite actually has teeth on the
spec's load-bearing decisions — every one was caught:

| Mutation | Result |
|---|---|
| Revert the focused-passed predicate to the dead `goals` check | killed |
| Re-add `improvement_comment` to `per_question` | killed |
| Compute `accuracy_pct` over all questions instead of anchored | killed |
| Compute `tool_choice_pct` over anchored instead of all questions | killed |
| Trust the judge's `unanchored` instead of deriving it from the snapshot | killed |

That last one matters most: it is the defect Codex found in Act 2 round 1 — letting the
evaluated party decide which questions leave the denominator — and the suite now fails
if anyone reintroduces it.

**Rounds used:** 2 of `MAX_FIX_ROUNDS: 2` + 1 (build). Claude never had to take over.

## Act 3 — Build, Tramo 2 (`1.7.0`, autopilot)

Contract: items 9–14 + the 1.7.0 bump of item 23. Tramo 1 and Tramo 3 declared
out of scope. Codex `gpt-5.6-sol` / `reasoning_effort=high`, `--yolo`, from the
repo root. Base: `7963ea0` (`v1.6.0` + E2E), clean tree.

### Round 0 — Codex asked instead of building

`codex exec --yolo` returned exit 0 after ~1 minute having written nothing: it
described its design and ended on *"Approve this implementation design?"*.
`--yolo` bypasses the sandbox, not the model's habit of seeking sign-off.
Resumed the same session (`01a061ff-c797-7292-93ac-c7197d35c3de`) approving the
design and forbidding further approval requests. Cost: one wasted launch, no
lost context.

### Round 1 — Codex build

~29 min. 9 files, +447/−30. Both proof suites green. Report claimed
"Deviations: none".

Verified by Claude, not taken on trust:

- Both suites re-run locally: `test_loop: all assertions passed`,
  `test_loop_e2e: all assertions passed`.
- Full diff read.
- Seven mutations of the new logic, all killed: mixed dev/holdout cross-analysis
  into the brief; brief written in manual mode; preflight ignoring a dirty tree;
  autopilot starting without `authorization.json`; holdout ids surviving in
  `regressed_ids`; holdout `improvement_comment` reaching the brief.

Good calls beyond the letter of the spec:

- The preflight runs against `authorization["repo"]`, not the cwd — this closes
  the plan's own open question about the service repo and the `.service-judge/`
  repo not being the same one.
- `git status` excludes `.service-judge` only once HEAD is already on the run
  branch, so iteration 2+ is not blocked by the loop's own uncommitted
  artifacts, while iteration 1 still requires a genuinely clean tree.

### Claude's verdict — Round 1: three defects, fixed directly (not delegated)

Under ~10 lines total, so Claude took over rather than spending a Codex round on
trivia. Fix rounds used: 0 of 2.

1. **`validate_config` forced `autonomy.run_tests: true` for autopilot** —
   an unreported deviation that contradicts item 12 (actions are *authorized*,
   not mandatory) and the SKILL prose Codex itself wrote ("run authorized tests
   … only as approved"). A service with no test suite would have had to lie in
   its audit record. `edit_product_code` and `commit` stay required: without
   them autopilot is a no-op and item 11's commit-per-iteration cannot hold.
   That narrowing is a declared deviation, not a silent one.
2. **The `score < 4` filter in `build_fix_brief` was untested** — the fixture
   has exactly one dev question and it fails, so a passing dev question's
   `improvement_comment` could have entered the brief undetected. Code was
   correct; the test now covers it.
3. `10.` list continuation indented 3 spaces instead of 4 in the loop SKILL.

Both added assertions verified by mutation: re-requiring `run_tests` and
widening the score filter to `< 6` are each killed.

### Observations logged, not fixed (pre-existing, outside items 9–14)

- `loop.py`'s stdout `regressed_ids` is unfiltered and may name holdout ids.
  Pre-existing since 1.5.0; the *brief* filters correctly, and the fixer never
  reads stdout. The pilot does.
- The autopilot preflight gates every invocation, including finalize. A tree
  dirtied after probing therefore blocks grading an already-paid pack until it
  is cleaned. Stricter than item 11's "before starting", and arguably right —
  the answers stay in `pack.jsonl` and are not re-probed.

### Claude's addition — real-git coverage for `collect_git_preflight`

Both preflight tests mocked the collector away (`loop.collect_git_preflight =
lambda ...`), and the pure decision test covers only the decision. The single
piece of new code that touches the real world — linked-worktree detection,
`check-ref-format`, `show-ref`, the `.service-judge` status exclusion — had never
run against a git repo. Added seven assertions over real `tempfile` repos:
non-repo, clean attached checkout, uncommitted product change, pre-existing run
branch, loop artifacts on the run branch, detached `HEAD`, linked worktree.

They passed first try — the collector was correct. Five mutations confirm they
bite: dropping the linked-worktree clause, dropping the existing-branch check,
letting a detached `HEAD` through, removing the `.service-judge` exclusion, and
assuming any directory is a repo.
