# Plan: Service Judge 2.0 — objetivos por dimensión, autopilot y juez externo opcional

_Locked via grill — by Claude + auricIecu_

## Goal

`service-judge` mide bien pero certifica mal y solo puede medir. Tres huecos concretos:
(1) el `soft_gate` actual exige nota ≥4 sobre **todas** las preguntas, incluidas las
`unanchored`, cuyo techo por rúbrica es 4/5 — con la cobertura de anchors del 50% que el
propio perfil recomendado propone, aprobar exige que las 15 preguntas sin anchor salgan
literalmente perfectas; (2) el usuario no puede decir qué significa "aprobado" — no hay
objetivos configurables ni desglose por dimensión, solo un total 0–5; (3) cuando el loop
devuelve `needs_fix` se detiene y espera a un humano, sin camino autorizado para que un
agente aplique la corrección y siga.

Este plan corrige el gate, expone los objetivos, añade un modo autopilot con frontera de
autorización y rastro reversible, y permite —opcionalmente— que el juez sea un proceso de
otro harness en vez de la sesión activa. Se **diseña completo aquí** y se **aterriza en
tres tramos** (`1.6.0`, `1.7.0`, `2.0.0`), cada uno con su proof verde antes del siguiente.

## Approach

### Tramo 1 — `1.6.0`: dimensiones, objetivos y gate correcto

1. **Anchors pasan a ser un artefacto legible por máquina.** Hoy `cfg["anchors"]` apunta a
   un markdown libre (`Q01: total_customers=24305` / `Q02: anchor: none`) del que `loop.py`
   solo comprueba la existencia. Pasa a ser `raw/anchors.snapshot.json`, el nombre que el
   propio docstring de `loop.py` ya usa:

   ```json
   {"Q01": {"anchor": "total_customers=24305", "query": "SELECT count(*) FROM customers"},
    "Q02": {"anchor": null, "note": "date beyond available data — trap"}}
   ```

   `anchored(qid)` = la clave existe **y** `anchor` no es `null`. Se extrae en la Fase 3,
   antes de juzgar, así que es ciego al resultado. `references/discovery.md` y
   `references/questions.md` especifican el formato.

   **Dos consumidores se mueven en el mismo tramo, o la Fase 4 apunta al artefacto viejo
   mientras los gates leen el nuevo:** `references/judging.md:36` le entrega hoy `anchors.md`
   al juez de forma explícita, y el payload `needs_judgment` de `loop.py` publica la ruta en
   su clave `anchors`. Ambos pasan a `anchors.snapshot.json`.

2. **Contrato de verdict con dimensiones.** Cada objeto de `verdicts.json` añade:

   ```json
   {"id":"Q01","dimensions":{"tool_choice":1,"accuracy":2,"hallucination_free":1,"directness":1},
    "score":5,"unanchored":false,"improvement_comment":"",
    "broken_tool":false,"hallucinated_narrative":false,"false_guardrail":false}
   ```

   `compute_grade` valida, además de lo que ya valida: `tool_choice ∈ {0, 0.5, 1}`,
   `accuracy ∈ {0, 1, 2}`, `hallucination_free ∈ {0, 1}`, `directness ∈ {0, 1}`,
   `sum(dimensions) == score` **comparado en centésimas enteras** (`tool_choice` en pasos de
   0.5 obliga a no comparar flotantes), `unanchored` booleano e `improvement_comment` string.
   Hoy el código lee `v["verdict"]` sin validarlo (`loop.py:64-67`): un verdict sin esa clave
   lanza `KeyError` y tumba el run.

   **`verdict` sale del contrato del juez y pasa a derivarse** de `score` con las bandas de
   `rubric.md` (`pass ≥4`, `warn 2.5–3.5`, `fail ≤2`). Es dato redundante que hoy puede
   contradecir al score; derivarlo elimina la clase de error entera en vez de validarla.

   **`unanchored` también se deriva**, de `anchors.snapshot.json`. El juez lo sigue
   emitiendo, pero como *aserción verificable*: si contradice al artefacto, el verdict va a
   `errors`. Un campo escrito por el juez no puede decidir qué preguntas salen del
   denominador de los gates — sería el camino para sacar del examen justo lo difícil.
   Cuando `unanchored` es verdadero, `accuracy ≤ 1` (regla ya vigente en `rubric.md`).

   `references/rubric.md` pasa a exigir el objeto `dimensions` y a no pedir `verdict`; sigue
   siendo la única fuente de verdad y sigue viajando inline en el prompt del juez.

3. **Los porcentajes separan exactitud verificable de comportamiento juzgable.** Se corrige el
   defecto del `soft_gate` actual. `grade` gana:

   **Solo `accuracy` necesita un anchor.** `tool_choice`, `hallucination_free` y
   `directness` son juzgables sin ground truth, así que excluir las unanchored de las cuatro
   dimensiones dejaría media prueba sin gate: un servicio con 50% de cobertura podría
   certificar siendo perfecto en la mitad verificable y mediocre en la otra, frenado solo
   por `score ≤ 1` y los flags críticos. El reparto es:

   - `accuracy_pct` — **solo anchored**. `100 * puntos / (2 * nº anchored)`.
   - `tool_choice_pct`, `hallucination_free_pct`, `directness_pct` — **todas las preguntas**.
   - `pass_rate_pct` (`score ≥ 4`) — **solo anchored**: el techo de una unanchored es 4/5,
     así que "aprobado" sobre ellas significaría "perfecto" y el umbral no diría nada.
   - `dev` / `holdout` / `gap_pp` — **solo anchored**, por la misma razón.
   - `anchor_coverage_pct`: `100 * nº anchored / nº total`.
   - `unanchored_block`: `{count, percent, dimensions_pct}` — reportado aparte como
     confianza reducida. Ya no es lo único que cubre esas preguntas: las tres dimensiones
     independientes del anchor y los hard gates las gatean.

   Los **hard gates siguen aplicando a todas las preguntas**, con o sin anchor: nota ≤1,
   `broken_tool`, `hallucinated_narrative`, `false_guardrail`, hallazgos cross-analysis,
   errores de validación. Sin anchors la corrida no puede certificar exactitud, y lo dice;
   lo que no hace es fingir un porcentaje sobre preguntas que nadie puede verificar.
   `dev`/`holdout`/`gap_pp` se mantienen y pasan a calcularse también sobre anchored, con
   `null` cuando el denominador es cero.

   **Un denominador vacío nunca aprueba por accidente.** Un objetivo cuyo `actual` sea
   `null` cuenta como **no cumplido**. Certificar exige además al menos una pregunta
   anchored en dev y una en holdout; sin eso `goals.met` es falso con motivo explícito.

   **Sin ground truth el loop mide pero no certifica.** No hay perfil behavior-only ni modo
   alternativo: `min_anchor_coverage_pct` es un objetivo numérico más, nunca un selector de
   modo. Con cero anchors el loop sigue reportando las tres dimensiones independientes del
   anchor, los hard gates y la trayectoria, y `goals.met` es falso con motivo explícito
   ("sin anchors no se puede certificar exactitud"). Certificar un servicio cuyas respuestas
   nadie puede verificar es exactamente la aprobación falsa que este plugin existe para
   evitar. Para una lectura puntual sin ground truth está el one-off de `service-judge`.

4. **Objetivos configurables.** Bloque `goals` en `config.json` con el perfil recomendado
   como valor por defecto:

   ```json
   "goals": {"profile":"recommended-production-v1","min_tool_choice_pct":95,
     "min_accuracy_pct":95,"min_hallucination_free_pct":100,"min_directness_pct":95,
     "min_pass_rate_pct":95,"min_holdout_score_pct":95,"max_dev_holdout_gap_pp":5,
     "min_anchor_coverage_pct":50}
   ```

   `validate_config` valida rangos (`0–100`, enteros; `max_dev_holdout_gap_pp` entero ≥0).
   `grade["goals"] = {"met": bool, "detail": [{"metric","target","actual","met"}]}`.
   Los hard gates **no son configurables** — no existen claves para relajarlos.

   **Los objetivos se evalúan dentro de `compute_grade`**, cuya firma pasa a
   `compute_grade(verdicts, questions, judge, degradations, cross_analysis, goals, anchors)`
   —`anchors` es el mapa cargado de `anchors.snapshot.json`, o `None`—;
   `should_stop(history, max_iterations)` **no cambia de firma**: lee `last["goals"]["met"]`
   y trata un grade sin esa clave como corrida inválida bajo schema v2. Así el veredicto
   queda congelado en `history.json` junto a los números que lo produjeron, en vez de
   recalcularse contra unos objetivos que pudieron editarse a mitad de run.
   Sigue evaluándose **solo en corridas full**. `soft_gate` se elimina del grade.

5. **`schema_version: 2` y rechazo limpio de configs v1.** `loop.py` exige
   `schema_version == 2`; si falta o es otro, imprime
   `{"status":"unsupported_schema", ...}` con instrucción explícita de empezar un run
   nuevo y devuelve `2`. **No hay camino dual**: una sola definición de cada gate.
   `questions.golden.jsonl` y su `golden_sha256` **no cambian de formato** y se reutilizan
   tal cual, así que el activo caro (preguntas + anchors) sobrevive; lo que se pierde es el
   `history.json`, que de todos modos era incomparable tras corregir el gate.

6. **Handoff del baseline sin re-sondear.** Aceptar el loop tras un one-off escribe
   `iter-01/{selection.json, raw/pack.jsonl, verdicts.json, cross-analysis.json}` y ejecuta
   `loop.py` una vez, que salta el probe y el `needs_judgment` y escribe `grade.json` +
   `history.json`. **Esto no requiere código nuevo en `loop.py`** — el flujo actual ya lo
   permite, pero **sí exige que el handoff escriba un `config.json` v2 completo**
   (`schema_version`, `goals`, `probe_cmd`, `golden_sha256`, `anchors`,
   `baseline_holdout_exposed`): un config heredado del one-off sería rechazado por el punto
   5 antes de llegar a finalizar la iteración. `selection.json.selected_ids` debe ir en el mismo orden que `pack.jsonl`
   (`read_pack_ids` compara listas por igualdad).
   `config.json` registra `"baseline_holdout_exposed": true` y el reporte lo dice donde
   muestra el gap: *el holdout del baseline se mostró completo antes del primer fix, así
   que el `gap_pp` de la primera certificación es indicativo*. A partir de la segunda
   certificación full el gap recupera su valor.
   Si el usuario **declinó congelar** el golden set en el one-off, no hay handoff: congelar
   ahora asignaría splits después de ver resultados. En ese caso el loop congela el set y
   **sondea un baseline nuevo**.

7. **Tamaños y wizard.** `references/questions.md` publica el menú
   canary 10–12 / diagnóstico 30 *(recomendado)* / release 50 / release ampliado 100, con
   focused 10/15/20 y muestra de regresión 3/4/5. `service-judge-loop/SKILL.md` gana el
   paso guiado de objetivos: explica qué se califica, **muestra siempre el perfil
   recomendado** con sus valores, y ofrece "usar recomendado" o "personalizar"; al
   personalizar la columna recomendada permanece visible. Los objetivos quedan congelados
   para esa corrida.
   `references/reporting.md` y `assets/report-template.md` ganan la tabla por dimensión, la
   columna objetivo-vs-resultado, el bloque unanchored separado y la cobertura de anchors.
   (Verificado: ningún otro `.md` del repo lee `soft_gate` ni la forma de `grade.json`, así
   que la superficie de consumidores es esa.)

8. **Tests** en `test_loop.py`: dimensiones válidas / inválidas / suma incorrecta (incluida
   una suma con `tool_choice: 0.5` que solo cuadra en centésimas enteras); `accuracy > 1`
   con `unanchored` rechazado; un verdict con `unanchored: false` para una pregunta sin
   anchor en el snapshot va a `errors`; un verdict sin `verdict` **no** lanza `KeyError` y
   el campo se deriva del score; `accuracy`, `pass_rate`, `dev`, `holdout` y `gap_pp`
   calculados solo sobre anchored, y `tool_choice`, `hallucination_free` y `directness`
   sobre todas; hard gate disparando por una pregunta unanchored; `goals.met` verdadero y
   falso; un objetivo con `actual: null` cuenta como no cumplido; holdout sin ninguna
   anchored no certifica; cero anchors no certifica y `goals.met` es falso con motivo;
   objetivos fuera de rango rechazados; config v1 rechazada con
   `unsupported_schema`; grade sin `goals` bajo v2 rechazado; handoff de `iter-01`
   prefabricado sin sondear. Y el caso que motiva el reparto de métricas: **50% anchored
   perfecto + 50% unanchored mediocre no certifica**, porque `tool_choice_pct`,
   `hallucination_free_pct` y `directness_pct` se calculan sobre todas. `anchors.snapshot.json`
   ausente o mal formado degrada con motivo, no revienta.

### Tramo 2 — `1.7.0`: autopilot

9. **`fix-brief.json` dev-only, escrito por `loop.py`.** En `needs_fix`, `loop.py` escribe
   `iter-NN/fix-brief.json` **a partir de los verdicts ya validados**, no de
   `grade["per_question"]` — `compute_grade` descarta `improvement_comment` y el brief lo
   necesita. Contiene **solo**: dev con nota <4 y sus `improvement_comment`,
   `regressed_ids`, y hallazgos cross-analysis cuyos `ids` sean **todos** dev. Del holdout,
   únicamente `{percent, gap_pp}` y el resultado de los gates. La redacción ocurre en el
   origen, no en la prosa de la SKILL.

10. **El fixer consume `fix-brief.json` y nada más.** No `grade.json`, no `verdicts.json`,
   no `raw/`, no `history.json`. En Claude Code el fixer corre como **subagente** que
   recibe el brief inline y ninguna ruta a `.service-judge/`. Es una frontera de contrato,
   no una jaula: el fixer tiene shell. Por eso **en autopilot el `gap_pp` se reporta
   marcado como indicativo**, y el reporte dice que la medida limpia de generalización es
   un run manual.

11. **Rastro reversible.** Precondiciones duras, verificadas en un **preflight de git**
    antes de tocar nada: hay repo, el árbol está limpio, el sistema de archivos es
    escribible y `git switch -c` puede crear la rama (worktrees gestionados, `HEAD` detached
    o un checkout de solo lectura fallan aquí). Si el preflight falla, **el autopilot no
    arranca y se ofrece modo manual**, en vez de romper a mitad de la primera iteración. Rama dedicada `service-judge/run-<id>` creada desde
    `HEAD`; nunca escribe en `main`. **Un commit por iteración**, mensaje
    `service-judge autopilot iter-NN: <resumen>`. Así `REGRESSION` se vuelve accionable
    (`git revert <sha>`) y el loop mantiene su promesa de solo medir. `fix.json` guarda el
    sha, los archivos tocados, los tests ejecutados y la revisión evaluada; nunca
    razonamiento privado ni secretos. Vive en `.service-judge/run-<id>/iter-NN/fix.json` y
    **no se commitea**: el registro commiteado es el mensaje del commit más su diff.

    **El autopilot commitea solo código de producto, nunca nada bajo `.service-judge/`.** Y se corrige la afirmación de `service-judge-loop/SKILL.md` de que
    `grade.json`/`history.json` son seguros de commitear: `cross_analysis[].comment` puede
    citar respuestas del servicio con datos reales de clientes. Pasa a: revísalos antes de
    commitearlos; `raw/` sigue gitignored.

12. **Puerta de autorización.** Bloque `autonomy` en `config.json`
    (`{"mode":"manual"|"autopilot","edit_product_code","run_tests","restart_local",
    "deploy_staging","commit"}`). Antes de empezar se muestra: coder y modelo actuales,
    juez, repo permitido, entorno, objetivos exactos, estrategia, máximo de iteraciones,
    presupuesto de respuestas, acciones autorizadas —**incluido commitear**— y condiciones
    de parada. **Permite**: editar código del servicio en el repo autorizado, ejecutar
    tests, reiniciar el servicio local, commitear en la rama del run, actualizar staging
    solo si se marcó, y repetir el ciclo. **No permite**: tocar preguntas, anchors,
    rúbrica, objetivos o resultados; ver detalle holdout; cambiar de coder o de juez;
    desplegar a producción; DDL/DML o borrado de datos; secretos; migraciones destructivas;
    revertir cambios ajenos; saltarse diálogos de seguridad del harness o del SO.
    El valor en `config.json` es **auditoría, no autoridad**: la autorización vive en la
    conversación. Si cambia el coder, se abre otra sesión o se amplía el alcance, se vuelve
    a pedir. El run guarda `authorization.json` con timestamp, alcance, repo, entorno,
    acciones permitidas y el texto exacto que se aprobó. Es **necesario pero nunca
    suficiente**: sin él el autopilot no arranca, y con él tampoco arranca si la
    autorización no ocurrió en esta conversación.

13. **Ciclo del piloto**, por iteración: leer `fix-brief.json` → agrupar por causa raíz →
    escoger la corrección de mejor impacto/esfuerzo → aplicar → tests → reiniciar
    local/staging según lo autorizado → commit → ejecutar la focused o full que toque →
    juzgar → comparar contra los objetivos → continuar, certificar o parar. Una regresión
    en focused se vuelve prioridad del siguiente fix; una regresión confirmada en full
    detiene el piloto.

    **Invariante explícito de las focused**: una focused no sondea holdout, así que **no
    calcula `gap_pp`, no evalúa `goals` y no puede certificar**. Solo produce brief de dev y
    marca prioridades. Es lo que ya hace el filtrado por `fulls` en `should_stop`, ahora
    escrito como invariante en vez de como efecto secundario.

14. **Tests**: `fix-brief.json` no contiene ningún id holdout ni ningún comentario de
    holdout; grupos cross-analysis mixtos dev+holdout se excluyen del brief; árbol sucio
    bloquea el arranque; sin `autonomy.mode == "autopilot"` no se escribe brief de fix
    automático.

### Tramo 3 — `2.0.0`: juez externo opcional

15. **Consentimiento de egreso antes de activar un juez externo.** El pack contiene
    respuestas reales del servicio —la propia SKILL dice que `raw/` puede llevar datos de
    clientes— y los anchors salen de su base de datos. Un juez externo los **envía a otro
    proveedor**. Antes de habilitar `judge_cmd` se muestra exactamente qué archivos salen
    (`{prompt}`, `{pack}`, `{rubric}`, `{anchors}`), a qué harness, y se pide consentimiento
    explícito; queda registrado en `authorization.json` y en el reporte.
    **`authorization.json` tiene dos ubicaciones**, porque el juez externo también se elige
    en el one-off, que no tiene run dir: en el loop,
    `.service-judge/run-<id>/authorization.json`; en el one-off,
    `<artifacts-dir>/authorization.json`, donde `<artifacts-dir>` es el directorio que el
    usuario aprobó en la Fase 1 (`eval-runs/` por defecto). Misma forma, mismo contenido. El modo por defecto
    (juez en sesión) no tiene egreso nuevo y no pregunta.

16. **`judge_cmd` es una plantilla de shell en `config.json`**, con el mismo contrato de
    confianza que el `probe_cmd` que ya existe ("same trust as a Makefile"). Placeholders
    **solo de rutas**: `{prompt}`, `{pack}`, `{rubric}`, `{anchors}`, `{out}`. El contenido
    del pack **nunca** entra en la línea de comando — estrictamente más seguro que el
    `probe_cmd` actual, que sí interpola texto de pregunta con `shlex.quote`. Las rutas se
    expanden **absolutas y con `shlex.quote`**: un directorio con espacios o comillas
    rompería o inyectaría el comando.

17. **`judge_cmd` ausente = comportamiento actual.** El harness activo juzga en sesión,
    `status: needs_judgment`, coste cero. Presente: `loop.py` lanza el comando con
    `subprocess.run(shell=True, timeout=cfg.get("judge_timeout", 900))`, lee `{out}`,
    valida y continúa. Es un `if` junto a `probe()`, **no hay `judge_runner.py`**: la
    validación del JSON pertenece a `compute_grade`, que ya valida verdicts, y un segundo
    validador en otro archivo duplicaría ese código.

18. **Un reintento de formato, luego pausa.** JSON inválido o `{out}` ausente → se reinvoca
    una vez con una instrucción de formato; un segundo fallo devuelve
    `{"status":"judge_failed", ...}` con el stderr recortado y **no** consume iteración ni
    escribe `history.json`. Salida no cero del comando se trata igual.
    El juez escribe en un temporal y `loop.py` hace `rename` atómico a `verdicts.json` /
    `cross-analysis.json` **solo después de parsear y validar**, para que un fallo no deje
    basura que la siguiente invocación confunda con trabajo hecho.

19. **Congelado y drift.** `grade["judge"]` pasa de string a
    `{"label": "codex/gpt-5.5", "cmd_sha256": "<sha>"}`. El reporte muestra **`label` y
    `cmd_sha256`, no la cadena literal**: la hard rule 2 de `service-judge/SKILL.md` prohíbe
    imprimir credenciales, y un `judge_cmd` puede llevar headers, tokens o rutas sensibles
    inline. Por lo mismo, la SKILL del loop pasa a instruir que
    `.service-judge/run-*/config.json` se gitignoree — ya hoy contiene el `probe_cmd`, que
    típicamente lleva la API key del servicio evaluado, y eso nunca se dijo. Antes de juzgar, `loop.py` compara el sha del `judge_cmd`
    actual contra el del **primer grade del historial**; si difiere, para con `judge_drift`.
    Comparar contra el config no sirve: editar el config borra el valor anterior, que es
    justo el caso que hay que detectar.

20. **Selección del juez al inicio de `service-judge`.** El coder es, sin selector, el
    harness y modelo de la sesión actual. Antes de evaluar se pregunta, sin opción
    preseleccionada, qué juez usar: la sesión actual (gratis, por defecto si el usuario no
    elige otra cosa), Codex, Claude Code o DeepSeek Harness. **No hay resolución automática
    del "modelo más capaz"**: ninguna de las tres CLIs lo expone (`codex` no tiene
    `models list`; `claude --model` acepta alias pero no enumera; `dsh` bootea perfiles, no
    modelos). Se usa el default configurado del harness elegido, se **muestra antes de
    gastar respuestas**, y es sobreescribible dentro del propio `judge_cmd`.
    `references/judging.md` publica los tres comandos exactos copy-paste, cada uno con su
    aislamiento (`codex exec -s read-only … < /dev/null`; `claude -p` con
    `--disallowed-tools` y sin persistencia; `dsh --profile` con perfil de juez).
    `references/judging.md` exige hoy un juez "at least as strong" y sin herramientas; con
    un comando de shell el plugin **no puede verificar ninguna de las dos cosas**. Esa
    garantía se degrada a **advertencia auditable**: el reporte imprime `label`,
    `cmd_sha256` y una versión redactada del comando, y dice que el aislamiento y la
    potencia del juez externo son responsabilidad de quien lo configuró. Sigue siendo garantía real solo en el modo por defecto (juez en sesión).

21. **Corrección del discurso de costo.** `SKILL.md` dice hoy *"Judging is free"*: cierto
    solo cuando el juez es la sesión activa. Pasa a: **gratis por defecto; si eliges un
    juez externo, consume la suscripción de ese otro harness**. README, `SKILL.md` y
    `references/judging.md` se corrigen; se elimina también la afirmación obsoleta de que
    Codex no tiene subagentes.

22. **Tests** con un ejecutable falso: el comando recibe rutas y nunca contenido del pack;
    un `--run` con espacios y comillas en la ruta se ejecuta correctamente; JSON inválido
    reintenta exactamente una vez y luego `judge_failed`; salida no cero pausa sin escribir
    historia; un fallo tras escritura parcial no deja `verdicts.json` corrupto (rename
    atómico); `judge_cmd` distinto a mitad de run produce `judge_drift` comparando contra
    el primer grade; `judge_cmd` ausente conserva el flujo `needs_judgment` actual; el
    reporte nunca imprime la cadena literal de `judge_cmd`.

23. **Manifiestos.** La versión vive en exactamente cuatro sitios:
    `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json` y el `metadata.version` de
    ambas `SKILL.md`. **Los dos `marketplace.json` no tienen campo `version`** —verificado—
    así que no se bumpean. Un tramo, una versión (`1.6.0`, `1.7.0`, `2.0.0`), una entrada
    de `CHANGELOG.md`.

## Key decisions & tradeoffs

- **Tres tramos, no un 2.0 monolítico.** v1.5.0 fue una sola pieza (+610 líneas) y aun así
  se le escaparon tres bugs a los unit tests, detectados solo montando E2E a mano. Seis
  piezas es ~4× ese diff y el paso que atrapa los bugs no escala. Además, cambiar rúbrica,
  juez y tamaño a la vez deja `golden_sha256` intacto pero **ningún `history.json`
  comparable**: el fallo caro no rompe, miente. Se paga: tres ciclos de release en vez de uno.
- **Los porcentajes separan exactitud verificable de comportamiento juzgable; los hard
  gates van sobre todo.** Es la regla que el diseño ya aplicaba a `accuracy`, extendida a
  las demás métricas que dependen del anchor. Se descartó subir el techo de las unanchored a 5
  (le daría a lo no verificable el mismo valor que a lo verificado), una banda de pass
  distinta para unanchored (umbral arbitrario) y normalizar `score/max` (rompe la banda 0–5
  de la rúbrica, el reporte y todo el historial). Se paga: con cobertura baja el loop mide
  poco — y lo dice, en vez de fingir.
- **Sin compatibilidad de runs v1.** 1.5.0 llegó a `main` el 2026-09-01; la superficie real
  de runs en vuelo es una máquina. Mantener el camino dual obligaría a conservar vivo un
  `soft_gate` que sabemos incorrecto y a que `should_stop` compare `dev.percent` calculados
  con fórmulas distintas — el fallo silencioso de la Q1 metido dentro del release. Se paga:
  una corrida a medias se reempieza (30 respuestas, mismo golden set).
- **Redacción en el origen, y honestidad sobre su fuerza.** El holdout está hoy en claro en
  `grade.json`, `history.json`, `raw/pack.jsonl` y `verdicts.json`, y la única barrera es
  una línea de prosa que regula lo que el agente *enseña*, no lo que *sabe*. En autopilot el
  que arregla es el mismo contexto que leyó los cuatro. `fix-brief.json` evita la fuga
  accidental; no es una jaula, así que el `gap_pp` en autopilot se marca indicativo. Se
  descartó vender el gap como medida plena apoyándose en el contrato.
- **Commit por iteración.** Sin él, `REGRESSION: dev dropped 87 -> 79 after the last fix`
  es una parada que no puedes accionar: no existe "el último fix" como objeto separable.
  La alternativa (`fix.patch` por iteración, sin commitear) conserva el historial limpio
  pero convierte revertir en aplicar parches invertidos a mano. Se paga: el repo recibe
  commits automáticos en una rama dedicada, y commitear se lista explícitamente en la
  autorización.
- **El anchor solo condiciona lo que sin él no es juzgable.** `accuracy`, `pass_rate`, `dev`,
  `holdout` y `gap_pp` van sobre anchored; `tool_choice`, `hallucination_free` y `directness`
  van sobre todas. Excluir las unanchored de las cuatro dimensiones —mi primera versión—
  abría el hueco de certificar siendo perfecto en la mitad verificable y mediocre en la otra.
- **Sin ground truth no hay certificación, y no hay perfil que la conceda.** Se descartó
  `behavior-only-v1`: convertía un valor numérico (`min_anchor_coverage_pct: 0`) en selector
  de modo, obligaba a un umbral inventado y le daba a un servicio no verificable un sello
  que este plugin existe para negar.
- **`unanchored` se deriva, no se cree.** Codex señaló que dejar que el juez declare qué
  preguntas salen del denominador es el camino directo a sacar del examen justo lo difícil.
  Cuesta convertir el snapshot de anchors en JSON; a cambio, el campo que decide los gates
  deja de ser dato autorreportado por la parte evaluadora.
- **`verdict` deja de pedirse al juez y se deriva del score.** Un campo redundante que puede
  contradecir a su propia fuente es una clase de error, no una validación pendiente.
- **Plantilla `judge_cmd` en vez de tres adaptadores.** La diferencia real entre los
  adaptadores es una cadena de comando; el repo ya resolvió ese patrón con `probe_cmd`.
  Elimina `judge_runner.py`, `test_judge_runner.py`, `assets/dsh-judge.patch.yml` y el
  compromiso de mantener un asset atado a `dsh 0.1.1-rc.2` (developer preview). Soporta
  harnesses que aún no existen sin tocar código. **Se paga: el aislamiento deja de estar
  garantizado por nosotros** — un usuario puede pegar `claude -p` sin `--disallowed-tools`.
  Mitigación: los tres comandos se publican exactos, y el reporte lleva `label`,
  `cmd_sha256` y el comando redactado, así que un juez mal aislado es visible en la
  evidencia (un adaptador con un flag mal puesto, no).
- **Sin resolución automática del "modelo más capaz".** No existe API para ello en ninguna
  de las tres CLIs; una lista de prioridad hardcodeada se pudre en semanas y un probe por
  candidato cuesta llamadas y confunde "modelo no permitido" con "sin cuota". Se paga: la
  promesa de "escoge solo el más inteligente" se sustituye por "usa el que configuraste, y
  te lo enseña antes de gastar".

## Risks / open questions

- `fix-brief.json` es una frontera de contrato con un agente que tiene shell. Mitigado y
  declarado, no resuelto. Si en la práctica se observa al fixer leyendo `.service-judge/`,
  la siguiente iteración de diseño tendría que mover el brief fuera del repo del servicio.
- El commit por iteración asume que el repo del servicio y el repo donde vive
  `.service-judge/` son el mismo. Si el usuario tiene el golden set en otro sitio, la
  precondición de árbol limpio hay que evaluarla contra el repo del **servicio**.
- Un `judge_cmd` que exceda `judge_timeout` con un pack grande deja el run sin iteración
  consumida pero habiendo pagado las respuestas del servicio. Aceptado: las respuestas ya
  están en `pack.jsonl` y no se re-sondean.
- El perfil "recomendado" (`min_anchor_coverage_pct: 50`) sigue siendo una apuesta sin
  datos: no hay evidencia de cuántos servicios reales alcanzan 50% de cobertura de anchors.
- Un servicio sin ground truth **nunca podrá certificar** con el loop. Es deliberado, pero
  es una limitación dura: esos usuarios solo obtienen trayectoria, dimensiones de
  comportamiento y hard gates. Si resulta ser el caso mayoritario, habrá que revisarlo.
- Cambiar `anchors` de markdown a `anchors.snapshot.json` invalida los snapshots existentes.
  Es el mismo corte que el rechazo de configs v1 y va en el mismo tramo, pero hay que
  decirlo en el `CHANGELOG` de `1.6.0`.
- `authorization.json` es un registro de auditoría, no un mecanismo de aplicación: nada
  impide que un agente lo escriba él mismo. Su valor es la trazabilidad posterior.

## Out of scope

- Instalar o autenticar Codex, Claude Code o DeepSeek automáticamente.
- Guardar API keys o llamar directamente a APIs de modelos.
- Jueces en ensemble, votación múltiple o cambio de juez entre iteraciones.
- Despliegue automático a producción, migraciones destructivas, DDL/DML.
- CI, servicio persistente o interfaz gráfica fuera del chat.
- Migración automática de `history.json` de v1 a v2.
- Ampliar un golden set congelado a mitad de un run (cambia el sha → run nuevo).
