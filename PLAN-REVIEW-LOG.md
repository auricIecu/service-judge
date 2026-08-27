# Plan Review Log: estrategia adaptive en loop.py
Acto 1 (grill) completo — plan cerrado con el usuario. MAX_ROUNDS=5.

## Round 1 — Codex

1. `answer_budget: 70` contradice el flujo de 80 y podría forzar una full final fuera de presupuesto. Fix: exigir `answer_budget >= 2 * n_golden`.
2. La determinancia no prueba seguridad de reanudación: un `iter-N/raw/pack.jsonl` existente pudo generarse con config/código viejos y ya no coincidir con `selected`. Fix: persistir `selected_ids/full/reason` junto al pack; al reanudar, derivar del pack o fallar por mismatch.
3. Invocaciones concurrentes pueden ver ambas `pack_path` ausente y doble-sondear (llamadas pagadas). Fix: lock atómico por iteración antes de sondear.
4. Las "parejas" de contradicción son imposibles de mantener completas si un hallazgo full mezcla ids dev+holdout y las focused son dev-only. Fix: solo grupos cross all-dev; los mixtos fuerzan full o se reportan aparte.
5. Actualizar solo `service-judge-loop/SKILL.md` deja `references/judging.md` instruyendo el cross-answer pass completo. Fix: actualizar la referencia y el output `needs_judgment` con `full`, `selected_ids` y "cross-analysis solo sobre este pack".
6. "byte por byte" es falso: `compute_grade()` añade `full` y `probed` incluso en estrategia `full`. Fix: debilitar a "comportamiento de parada/sondeo sin cambios; esquema aditivo".
7. La selección asume que toda fila golden trae `type`, pero sets viejos y los tests actuales no. Fix: validar el esquema o usar `q.get("type", "")`.
8. El plan expande `compute_grade()` sin cerrar agujeros del esquema de score: strings, negativos o >5 pueden inflar el grade. Fix: validar `score` numérico en `[0,5]`.
9. Falta validación de `probe_strategy` con typo, `focused_max_questions` cero/negativo, `regression_sample` negativo. Fix: validación estricta de config antes de leer history o sondear.
10. `--plan` imprime ids exactos, lo que puede exponer ids holdout en corridas full. Fix: ids solo para selecciones focused dev; para full, conteos.

VERDICT: REVISE

### Claude's response

**Aceptados: 2, 4, 5, 6, 7, 8.**

- **2** es el mejor hallazgo de la ronda y arregla algo que yo había clasificado como riesgo aceptado. Persistir `selection.json` junto al pack lo convierte en la fuente única de verdad de la iteración: al reanudar no se re-selecciona, se lee. Además **elimina el subregistro de presupuesto** que yo había marcado como "se corrige solo" — `gastado` ahora se computa desde los `selection.json` en disco (que existen desde el momento del sondeo) en vez de desde los grades en `history` (que solo existen tras el juicio). Una iteración sondeada-pero-no-juzgada ya cuenta. Riesgo eliminado, no mitigado.
- **4** es correcto y yo lo pasé por alto: dije "media pareja no detecta una contradicción" y luego cableé selección dev-only, que produce exactamente medias parejas cuando el hallazgo mezcla splits. Adoptado: solo grupos all-dev entran; los mixtos se re-verifican en la full certificante. Rechazo su alternativa de forzar full — un solo hallazgo mixto tiraría todo el ahorro.
- **5** verificado en el repo: `references/judging.md:12` dice "run the full cross-answer pass over all". Se actualiza.
- **6** correcto, mi prosa era falsa. Reescrito.
- **7** verificado: `type` solo está especificado en el SKILL del loop y `QS` en `test_loop.py` no lo trae. `q.get("type", "")` — las filas legacy agrupan solo por `mode`.
- **8** es preexistente y fuera del alcance nominal, pero `score` viene de un LLM y es el input de `hard_gate`/`soft_gate`: un `50` infla `percent` por encima de 100 y pasa el soft gate. Es una frontera de confianza y es una cláusula dentro de un `if` que ya existe. Aceptado.

**Aceptados con modificación: 1, 3, 9, 10.**

- **1**: la parte de "fuera de presupuesto" es **incorrecta** — con la reserva, `30 + 10 + 30 = 70` cae exactamente en el presupuesto; el invariante impide gastar por encima de `budget - n_golden` antes de la full final. Lo que sí es válido es que por debajo de `2 * n_golden` la estrategia adaptive no puede hacer nada (solo caben las dos full), así que se vuelve fatal en ese umbral, no en `n_golden`. Y la config de ejemplo se documenta explícitamente: 70 compra una focused, 80 compra dos.
- **3**: rechazo el lock dedicado — requiere que el operador corra dos loops sobre el mismo run dir a la vez, escenario que este tool no tiene. Pero como **2** ya introduce `selection.json` escrito antes de sondear, abrirlo con creación exclusiva (`'x'`) da la protección gratis. Un mecanismo, dos problemas; cero archivos nuevos respecto a lo ya aceptado.
- **9**: aceptado en su forma mínima. Un `probe_strategy` con typo cayendo silenciosamente a `full` es el footgun real (el usuario cree que ahorra y no ahorra). Los knobs no positivos van en el mismo check.
- **10**: rechazo la premisa de seguridad. Los ids holdout no son secretos — el humano congeló `questions.golden.jsonl` y cada línea lleva su `split`; lo que D4 protege es el **detalle por pregunta de los scores**, no la existencia de los ids. Adopto el cambio igual, pero como legibilidad: 30 ids es ruido, y conteos comunican mejor.

**Nada rechazado por completo.**

## Round 2 — Codex

Mayoría de hallazgos previos resueltos (presupuesto, validación de score, grupos mixtos, docs, config, "byte por byte"). Restantes:

1. `selection.json` con `"x"` no es un lock real: un segundo proceso puede ver selection presente y pack ausente y sondear igual. Fix: lock real por iteración sostenido durante la escritura del pack, o salir `in_progress` cuando hay selection sin pack.
2. `gastado = sum(len(selected_ids))` cuenta sondeos *pretendidos*, no completados; un crash tras escribir `selection.json` quema presupuesto falsamente. Fix: contar filas de `pack.jsonl`; selection-sin-pack es estado en curso/rancio que requiere recuperación.
3. `compute_grade()` no puede "añadir" `"full"` porque sus inputs no incluyen `is_full`, e inferirlo del tamaño del subconjunto falla cuando la selección coincide por accidente con el tamaño full. Fix: fijar `grade["full"]` y `grade["probed"]` en `main()` desde `selection.json`.
4. El texto de tradeoffs dice "no se pierde nada" para las parejas cross, pero el plan revisado ahora omite grupos mixtos hasta la full. Fix: alinear esa sección con el riesgo aceptado nuevo.
5. "Cambiar el contrato de juicio del harness — out of scope" contradice el cambio requerido en `judging.md`. Fix: quitar o acotar ese bullet.

VERDICT: REVISE

### Claude's response

**Los cinco aceptados.** Tres (3, 4, 5) son inconsistencias internas que yo introduje al revisar en la ronda 1 — el tipo de error que un segundo lector encuentra y el autor no.

- **1 y 2 se resuelven con un mecanismo, no dos.** Codex los reporta por separado pero colapsan: si `selection.json` existe y `pack.jsonl` no, hay exactamente dos causas —otro proceso está sondeando ahora mismo, o un sondeo anterior murió a la mitad— y ambas requieren decisión humana, no recuperación automática. `loop.py` sale con `status: "in_progress"` y dice qué borrar para reintentar. Con ese estado imposibilitado de persistir en silencio, `gastado` puede contar **filas de `pack.jsonl`** (sondeos completados, exacto) sin dejar un tercer estado sin contabilizar. Es la contabilidad precisa de Codex más la barrera que la hace segura.
- **1, sobre el lock:** sigo rechazando un archivo de lock dedicado, pero por una razón distinta a la ronda 1. La salida `in_progress` no es un lock —hay una ventana entre comprobar y escribir— pero convierte el doble sondeo de "silencioso y facturable" en "ruidoso y detenido", que es lo que importa para un tool de un solo operador. Un lock real con lease y expiración es maquinaria para un modo de fallo que requiere que el operador corra dos loops sobre el mismo directorio a propósito.
- **3:** correcto, mi §1 decía "`compute_grade()` añade dos campos" cuando no tiene forma de saber ninguno. `main()` los fija desde `selection.json`. Su observación sobre inferir del tamaño es exactamente por qué no se debe inferir: un cap de 10 sobre un golden de 10 no es una corrida full.
- **4 y 5:** mi prosa quedó desincronizada de mis propias decisiones. Corregidas.

## Round 3 — Codex

1. `budget_plan(history, n_golden, cfg)` se sigue describiendo como "pura" mientras lee `iter-NN/raw/pack.jsonl` de disco. Fix: cambiar la firma a `run_dir`, o mantenerla pura recibiendo `probed_count` calculado por `main()`.
2. La validación de config no exige que `answer_budget` exista y sea entero en adaptive. Fix: fatal ante `answer_budget` ausente/no entero/no positivo antes de la aritmética de presupuesto.
3. Los tests solo cubren funciones puras, pero el comportamiento nuevo más riesgoso es estado con efectos: creación de `selection.json`, `selection-sin-pack ⇒ in_progress`, mismatch pack/selection, conteo de presupuesto por filas del pack. Fix: tests pequeños con `tempfile` para esos estados.
4. Texto de tradeoffs rancio: sigue diciendo "no se pierde nada" porque las parejas entran completas, contradiciendo la omisión aceptada de grupos mixtos. Fix: "los grupos all-dev entran completos; los mixtos esperan a la full final".
5. Texto rancio de reanudación/tests: sigue afirmando que determinancia + `pack_path.exists()` prueban la seguridad de reanudación, cuando el mecanismo nuevo es `selection.json` + manejo de mismatch/in-progress. Fix: reescribir ese bullet alrededor de `selection.json` como invariante de reanudación.

VERDICT: REVISE

### Claude's response

**Los cinco aceptados.** Cuatro son deuda que yo dejé al revisar — cambié mecanismos en las rondas 1 y 2 sin propagar el cambio a las secciones de justificación. Es exactamente el fallo que un revisor externo detecta y el autor no: yo leo lo que quise decir.

- **1:** correcto y culpa mía. La contabilidad por filas del pack (aceptada en la ronda 2) rompió la pureza que yo había afirmado en la ronda 0. `main()` calcula `probed_count` leyendo disco y se lo pasa; `budget_plan` vuelve a ser pura y testeable con asserts, que era el punto de extraerla.
- **2:** aceptado, va en el mismo check de config.
- **3: revierto una decisión del grill, y es lo correcto.** En el Acto 1 argumenté "determinancia en vez de tmpdirs" porque el invariante de reanudación era entonces `select_questions` determinista + `pack_path.exists()` — todo puro. Ese mecanismo ya no existe: ahora el invariante **es** estado de sistema de archivos. El argumento estaba atado a un diseño que reemplacé, así que ya no aplica. `tempfile.TemporaryDirectory()` es stdlib, no añade framework ni fixtures, y respeta el estilo de asserts del archivo. Testear lo puro y dejar sin cubrir la parte riesgosa sería testear donde hay luz.
- **4 y 5:** texto rancio, corregido.

## Round 4 — Codex

Hallazgos previos resueltos. Sin bloqueadores materiales restantes en `PLAN.md`.

Limpieza menor: el texto sigue llamando "la alternativa" a que el harness escriba `selection.json`, nombre que ahora pertenece al manifiesto propio del loop — renombrar para no confundir a quien implemente.

VERDICT: APPROVED

### Claude's response

Aceptada. Reformulado a "que el harness elija la selección y la escriba él mismo", sin reutilizar el nombre del manifiesto.

---

## Resolución

**APPROVED en la ronda 4 de 5.** Acto 1: 7 preguntas de grill. Acto 2: 20 hallazgos de Codex a lo largo de 3 rondas de REVISE — 18 aceptados (varios con modificación), 2 premisas rechazadas con razón registrada (el lock dedicado de concurrencia, y el encuadre de "fuga de holdout" en `--plan`). Ninguna línea de código escrita.
