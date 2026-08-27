# Plan: estrategia `adaptive` en loop.py — reducir respuestas sin bajar el listón de certificación

_Locked via grill — by Claude + auricIecu_

## Goal

Hoy `loop.py` re-sondea el golden set completo en cada iteración: con 30 preguntas y
`max_iterations: 4` son hasta 120 respuestas pagadas al servicio. La mayoría son
desperdicio — remedir 26 preguntas que ya pasaron para enterarse de las 4 que siguen
rotas. Añadir una estrategia `adaptive` que en iteraciones de desarrollo sondea solo un
subconjunto dirigido (fallos + relacionadas + parejas de contradicción + muestra de
regresión) y reserva el examen completo para la corrida certificante. La calidad final
no baja porque **solo una corrida full puede satisfacer los quality gates**: la
propiedad se garantiza estructuralmente (gates filtrados a full, última iteración
forzada a full, presupuesto que reserva su costo), no con un flag de config. Costo
típico esperado: 30 + 10 + 10 + 30 = 80 en vez de 120.

## Approach

### 1. `history.json` distingue corridas full de parciales

- El grade gana dos campos: `"full": bool` y `"probed": int`. **Los fija `main()`**, no
  `compute_grade()` — la función no tiene forma de saber si su subconjunto es el golden
  completo, e inferirlo del tamaño es incorrecto: un `focused_max_questions: 10` sobre un
  golden de 10 preguntas no es una corrida certificante. Vienen de `selection.json` (§3b).
- `should_stop()` filtra `fulls = [h for h in history if h.get("full", True)]`:
  - gates (`hard_gate and soft_gate`) se evalúan sobre `fulls[-1]`, no `history[-1]`;
  - REGRESSION y STAGNATION comparan solo entre elementos de `fulls`;
  - `max_iterations` sigue contando `len(history)` completo (full + focused), para que
    el loop siempre termine.
- El `default=True` en `.get("full", True)` hace que runs preexistentes (grades sin el
  campo) se comporten exactamente como hoy.

### 2. `compute_grade()` recibe el subconjunto ya filtrado (y valida el rango de `score`)

- `main()` selecciona las preguntas y le pasa `selected` en vez de `questions`. La
  función no cambia su lógica interna: su universo sigue siendo "lo que me pasaron",
  así que `missing_verdict` se calcula contra el subconjunto y no genera 20 errores
  espurios que hoy matarían la corrida con exit 2.
- Se endurece el check de validez que ya existe: además de `"score" in v`, exigir que
  `score` sea numérico y esté en `[0, 5]`. Es preexistente y estrictamente fuera del
  alcance nominal, pero `score` lo escribe un LLM y es el input directo de `hard_gate` y
  `soft_gate`: un `50` infla `percent` por encima de 100 y pasa el soft gate. Frontera de
  confianza, y es una cláusula dentro de un `if` que ya está ahí.
- En el `print` final, `holdout` se reporta como `None` cuando `grade["holdout"]["max"]`
  es 0 (corrida dev-only), no como `0` — que se leería como catástrofe en vez de
  "no se midió". En `grade.json` el `max: 0` ya es autoexplicativo y no se toca.

### 3. `select_questions(questions, history, cfg, iteration) -> (selected, is_full, reason)`

Función pura. Construye `latest_score[qid]` recorriendo `history` hacia adelante — el
score más reciente de cada pregunta, venga de una corrida full o focused. Los fallos son
las preguntas **dev** con `latest_score < 4`.

Devuelve `is_full=True` (todo el golden set) ante cualquiera de estos cinco casos:

1. no hay ninguna corrida full en `history` (no hay baseline);
2. ningún dev con `latest_score < 4` — pasaron, toca certificar;
3. la focused no cabe en el presupuesto disponible (ver §4);
4. `len(fallos) > focused_max_questions` — con 12 de 30 rotas, una focused de 10 cuesta
   casi lo mismo, mide menos y no puede certificar;
5. `iteration == max_iterations` — la última iteración permitida es siempre full, o el
   run se detendría por límite sin haber podido certificar.

Si no, construye la selección **solo desde el split `dev`** (cableado, sin knob) como
unión de:

- **fallos**: dev con `latest_score < 4`;
- **grupos de contradicción**: hallazgos de `cross_analysis` del último grade full cuyos
  `ids` sean **todos dev**, incluidos en bloque — media pareja no detecta una
  contradicción. Los grupos que mezclan dev y holdout se **omiten**: la selección es
  dev-only, así que incluirlos produciría exactamente la media pareja inútil que esta
  regla evita. Se re-verifican en la full certificante, donde ambos splits se sondean;
- **relacionadas**: dev que comparten `(mode, type)` con algún fallo, leyendo
  `q.get("type", "")` — `type` está en el esquema del golden set pero sets congelados
  antes de este cambio pueden no traerlo, y esas filas simplemente agrupan por `mode`.
  Es la única señal de parentesco disponible sin llamar a un modelo, y `loop.py` es
  harness-only por diseño;
- **muestra de regresión**: `regression_sample` preguntas dev con `latest_score >= 4`,
  rotadas de forma determinista por número de iteración (p.ej. índice `i % len`), nunca
  al azar — determinista significa que reanudar una iteración interrumpida selecciona
  exactamente los mismos ids.

Recorte a `focused_max_questions` por prioridad: fallos → parejas → relacionadas →
regresión. Los fallos nunca se descartan; las parejas entran completas o no entran.

### 3b. `selection.json` es la fuente de verdad de cada iteración

La determinancia de `select_questions()` no basta para reanudar con seguridad: un
`iter-NN/raw/pack.jsonl` en disco pudo generarse con otra config o con código anterior,
y re-seleccionar en la llamada de finalización daría un `selected` que no coincide con lo
que realmente se sondeó — verdicts y preguntas desalineados, `missing_verdict` espurios.

- **Antes de sondear**, `main()` escribe `iter-NN/selection.json` con
  `{selected_ids, full, reason, strategy}`, abierto en modo **creación exclusiva**
  (`open(..., "x")`).
- **Al reanudar**, si `selection.json` existe, `main()` **lo lee en vez de re-seleccionar**.
  Esa es la lista que se pasa a `compute_grade()`, y de ahí salen `full` y `probed`. La
  selección se decide una vez por iteración, no una vez por invocación.
- Si `selection.json` y `pack.jsonl` no concuerdan en ids, es fatal con mensaje explícito
  — no se adivina cuál manda.
- **`selection.json` presente y `pack.jsonl` ausente ⇒ salida `status: "in_progress"`,
  sin sondear.** Ese estado tiene exactamente dos causas: otro proceso está sondeando en
  este momento, o un sondeo anterior murió a la mitad. Ambas requieren decisión humana
  —no recuperación automática, porque solo el operador sabe cuántas respuestas se
  facturaron realmente— así que el mensaje dice qué borrar para reintentar.
- No se añade un archivo de lock dedicado. La salida `in_progress` no es un lock (hay
  ventana entre comprobar y escribir), pero convierte el doble sondeo concurrente de
  "silencioso y facturable" en "ruidoso y detenido", que es lo que importa en un tool de
  un solo operador. Un lock con lease y expiración es maquinaria para un modo de fallo
  que exige correr dos loops sobre el mismo directorio a propósito.

### 4. `budget_plan(probed_count, n_golden, cfg) -> (gastado, disponible, reservado)`

Función pura: **`main()` lee el disco y le pasa `probed_count`**. La contabilidad por
filas del pack es un efecto de sistema de archivos, y meterlo dentro de `budget_plan`
destruiría la única razón por la que se extrajo — poder testear la aritmética de la
reserva con asserts y sin montar directorios.

- `probed_count` = suma de **filas de los `iter-NN/raw/pack.jsonl` en disco** — no desde
  `history`, y no desde `selected_ids`. Son las tres opciones y cada una cuenta algo
  distinto: `history` cuenta lo **juzgado** (subcuenta la iteración que se sondeó y quedó
  sin juzgar, que ya facturó); `selected_ids` cuenta lo **pretendido** (sobrecuenta si el
  proceso murió antes de sondear); las filas del pack cuentan lo **efectivamente
  sondeado**, que es lo que el servicio cobró. El estado intermedio que haría ambigua
  esta cuenta —selection sin pack— no puede persistir en silencio: §3b lo detiene con
  `in_progress`. Sin archivo contador aparte, y la reanudación no re-sondea packs en
  disco, así que no hay doble cobro.
- `reservado = n_golden` — apartado para la full certificante, intocable.
- `disponible = answer_budget - gastado - reservado`.
- Si una focused no cabe en `disponible`, **no se corre**: se salta a la full final. El
  run nunca se queda sin veredicto habiendo gastado todo el presupuesto.
- Validación de arranque, fatal como el mismatch de sha256: con `probe_strategy:
  "adaptive"`, si `answer_budget < 2 * n_golden` la estrategia no puede hacer nada — solo
  caben el baseline y la full certificante, cero iteraciones focused. Mejor decirlo en el
  segundo cero que descubrirlo en la iteración 3.
- **El invariante de la reserva garantiza que nunca se excede el presupuesto**: ninguna
  focused corre si dejaría `gastado > answer_budget - n_golden`, así que la full final
  siempre cabe. Con `budget: 70` y 30 preguntas: `30 + 10 + 30 = 70`, exacto.

### 5. `--plan`

Imprime y sale **sin tocar el servicio**. Muestra estrategia, el conteo,
gastado/disponible/reservado, y si esta corrida es la certificante. Para una selección
focused imprime los **ids exactos**; para una full imprime **conteos por split**, porque
30 ids son ruido y el conteo comunica mejor. (No es una medida de fuga: los ids holdout
no son secretos — el humano congeló `questions.golden.jsonl` y cada línea lleva su
`split`; D4 protege el detalle por pregunta de los *scores*, no la existencia de los ids.)
Es literalmente el output de `select_questions()` + `budget_plan()` — el mismo cálculo
que usa la corrida real, no una estimación paralela que pueda divergir.

### 6. Reportes nuevos en el output de `needs_fix`

- `"full": bool` y `"probed": int` — para que nadie lea el `percent` de una focused como
  la nota del producto.
- `"regressed_ids"`: preguntas que tenían `latest_score >= 4` y ahora sacaron `< 4`.
  **Reporta, no detiene.** Una muestra de 3 es evidencia débil para quemar la full
  reservada, y el skill ya establece que el loop mide y el humano decide. La parada
  REGRESSION agregada sigue existiendo, full-only, sin cambios.

### 7. Config

```json
{
  "probe_strategy": "adaptive",
  "focused_max_questions": 10,
  "regression_sample": 3,
  "answer_budget": 70,
  "max_iterations": 4
}
```

`probe_strategy` ausente o `"full"` = **comportamiento de sondeo y de parada idéntico al
actual**. Los runs existentes no cambian de conducta; el esquema de `grade.json` sí crece
de forma aditiva (`full`, `probed`), que es compatible hacia adelante pero no es "byte por
byte" — los lectores que hagan comparación exacta de grades verán los campos nuevos.

Con 30 preguntas, `answer_budget: 70` compra **una** iteración focused
(`30 + 10 + 30 = 70`); para el flujo de dos focused del enunciado hay que poner **80**.

Validación estricta de config antes de leer `history` o sondear: `probe_strategy`
desconocido es fatal — caer en silencio a `full` ante un typo es el peor resultado, el
usuario cree que está ahorrando y paga el precio completo. En el mismo check,
`focused_max_questions` y `regression_sample` deben ser enteros no negativos con
`focused_max_questions >= 1`, y con `probe_strategy: "adaptive"`, `answer_budget` debe
existir y ser un entero positivo. Ausente o no numérico es fatal antes de tocar la
aritmética de presupuesto: sin él no hay reserva, y sin reserva se pierde la garantía de
que la full certificante siempre cabe.

### 8. Tests (`test_loop.py`, mismo estilo: import de funciones puras + asserts)

- `select_questions`: los cinco disparadores de full; unión dev-only (assert de que
  ningún id holdout aparece jamás); grupos cross all-dev incluidos completos y grupos
  mixtos dev+holdout omitidos; filas golden sin `type` no revientan; encogimiento del
  conjunto vía `latest_score` conforme aterrizan fixes; prioridad de recorte;
  **determinancia** — dos llamadas con los mismos inputs devuelven los mismos ids.
- `budget_plan`: reserva respetada; focused que no cabe → full; el flujo
  `30 + 10 + 30` con `budget: 70` no excede; fatal si `answer_budget < 2 * n_golden` en
  adaptive.
- `compute_grade`: `score` no numérico, negativo o `> 5` cae en `errors` y no infla
  `percent`.
- `should_stop`: gates ignoran corridas parciales; REGRESSION/STAGNATION solo entre
  fulls; `max_iterations` cuenta todas.
- `compute_grade`: sobre un subconjunto no emite `missing_verdict` por las no sondeadas.
- Retrocompat: grades sin `"full"` se tratan como full; config sin `probe_strategy` se
  comporta como `"full"`.
- Config inválida: `probe_strategy` con typo es fatal; `answer_budget` ausente en
  adaptive es fatal.
- **Estado de sistema de archivos, con `tempfile.TemporaryDirectory()`** (stdlib, sin
  framework ni fixtures, mismo estilo de asserts): `selection.json` se escribe antes de
  sondear; `selection.json` presente y `pack.jsonl` ausente devuelve `in_progress` y **no
  sondea**; ids desalineados entre pack y selection son fatales; `probed_count` cuenta
  filas de pack y no `selected_ids`; reanudar con `selection.json` presente **no**
  re-selecciona aunque la config haya cambiado en medio.

### 9. Documentación

- `skills/service-judge-loop/SKILL.md`: sección de estrategia adaptive; **contrato
  explícito de que en una iteración focused el cross-analysis se limita a las preguntas
  del pack**; corrección de la sección "Cost".
- `skills/service-judge/references/judging.md`: la línea 12 instruye hoy "run the full
  cross-answer pass over all" — hay que acotarla al pack de la iteración. Sin esto, el
  contrato del harness contradice la validación de `compute_grade()` y el harness
  haciendo lo correcto rompería la corrida.
- El output `needs_judgment` gana `full` y `selected_ids`, para que el harness sepa
  sobre qué universo está juzgando sin tener que inferirlo del pack.
- `README.md` + `CHANGELOG.md`; version bump `1.4.0` → `1.5.0` en ambos `SKILL.md`.
- No se toca `.codex/plugins/cache`: se cambia el repo fuente, se valida, se reinstala.

## Key decisions & tradeoffs

- **`require_full_final` se elimina de la config propuesta.** Con los gates filtrados a
  full, la última iteración forzada a full y el presupuesto reservando su costo, la
  propiedad está garantizada por tres lados. El knob no la añade — solo permite
  *apagarla*, y "certifica con un examen parcial" no es una opción que valga la pena
  ofrecer. Un flag cuyo único valor útil es `true` es una línea de código, no config.
- **Sin reuso de baseline entre `run-<id>`.** El baseline de un run viejo midió una
  versión distinta del servicio, y `loop.py` no puede saber cuál: `golden_sha256`
  congela el examen, no el sujeto. El ahorro sería de 30 respuestas una sola vez; el
  modo de fallo es sondear las preguntas que fallaban en el código de hace tres semanas
  y quedar ciego a lo que se rompió desde entonces. **El costo típico honesto es 80, no
  50.** Los 50 solo aplican al retomar un run existente que ya tiene su full — algo que
  ya funciona hoy sin código nuevo.
- **La selección es dev-only, cableado, sin knob.** Tocar holdout en corridas focused lo
  rompe dos veces: expone detalle por pregunta al humano (viola D4) y lo re-sondea
  persiguiendo fallos, que es exactamente cómo un conjunto reservado deja de serlo. Un
  knob ahí solo serviría para desactivar la protección.
- **"Relacionada" = misma `(mode, type)`.** Similitud semántica requeriría un modelo, y
  `loop.py` nunca llama a uno. La alternativa (que el harness elija la selección y la escriba él mismo)
  añade un round-trip por iteración para ganar poco.
- **Selección por último-valor-conocido, no por último grade full.** Si siempre mirara
  la última full, las iteraciones 2 y 3 sondearían las mismas 10 preguntas incluso las
  ya arregladas. Con `latest_score` el conjunto se encoge solo, y "ningún dev por debajo
  de 4" es la señal natural de certificar.
- **Cross-analysis parcial se resuelve en el contrato, no en el código.** La validación
  de `ids ∈ split_of` se deja como está; `SKILL.md` y `judging.md` acotan el
  cross-analysis al pack. Los grupos **all-dev** de la full anterior entran completos en
  la selección, así que se re-verifican de inmediato; los **mixtos dev+holdout** esperan a
  la full certificante (ver Risks). Relajar la validación sería aceptar hallazgos sin
  evidencia en esta iteración.
- **STAGNATION deja de disparar en la práctica.** Filtrada a fulls, necesita 3 corridas
  full y un run adaptive típico tiene 2. Se acepta y se documenta: `answer_budget` es un
  freno de costo directo y duro, que es lo que STAGNATION aproximaba indirectamente. Dos
  frenos para lo mismo es uno de más.
- **`regressed_ids` reporta sin detener** (ver §6).
- **El invariante de reanudación es `selection.json`, no la determinancia.** En el grill
  argumenté testear determinancia en vez de montar tmpdirs, porque entonces reanudar
  dependía de `select_questions` determinista más el `if not pack_path.exists()` que ya
  existía — todo puro. Ese mecanismo se reemplazó: ahora la selección se decide **una vez
  por iteración** y se persiste, así que reanudar es correcto aunque la config cambie en
  medio, que es un caso que la determinancia nunca cubrió. El invariante pasó a ser
  estado de sistema de archivos, y por eso los tests lo siguen hasta ahí con `tempfile`.
  La determinancia se sigue testeando, pero ya no carga sola con la garantía.

## Risks / open questions

- **`(mode, type)` como proxy de parentesco es grueso.** Si el golden set tiene pocos
  `mode` distintos, "relacionadas" puede arrastrar casi todo el dev y saturar el cap,
  degradando la focused a algo cercano a una full. El recorte por prioridad lo contiene
  (los fallos nunca se pierden), pero el ahorro real depende de la granularidad del set.
  Mitigación: `--plan` lo hace visible antes de gastar una sola respuesta.
- **Una focused puede dar `hard_gate: true` en su JSON de salida** aunque no certifique.
  `should_stop` la ignora correctamente, pero un humano leyendo el output podría creer
  que terminó. Por eso `"full": false` va en el output; queda por validar que el fraseo
  del skill sea inequívoco.
- **Los grupos cross mixtos dev+holdout no se re-verifican hasta la full certificante.**
  Es la consecuencia deliberada de la selección dev-only; el riesgo es que una
  contradicción entre splits sobreviva varias iteraciones sin visibilidad. Aceptado: la
  full final la detecta, y no puede certificar con ella presente.

## Out of scope

- Reuso de baseline entre `run-<id>` distintos.
- Selección semántica de preguntas relacionadas (requeriría llamar a un modelo).
- Cambiar el rubric, la semántica de puntuación, o el esquema del golden set. (El
  contrato de juicio **sí** cambia en un punto acotado y necesario: `judging.md` debe
  limitar el cross-answer pass al pack de la iteración — ver §9. Sin eso, el harness
  siguiendo el contrato actual rompería la corrida.)
- Editar la copia instalada en `.codex/plugins/cache`.
- Nuevas condiciones de parada más allá de las cuatro existentes.
- Cambiar el comportamiento de runs con `probe_strategy` ausente o `"full"`.
