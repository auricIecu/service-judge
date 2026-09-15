# Calibración de evidencia — 2026-09-10

Seguimiento posterior: [calibración causal y mejora de Orito](2026-09-10-causal-calibration.md).
Este informe conserva los resultados de la primera fase.

**Resultado: mejora demostrada, calibración todavía abierta.** La rúbrica
elimina las penalizaciones de redondeo observadas en las 12 respuestas reales,
pero un segundo conjunto reservado detecta tres falsos positivos por evidencia
insuficiente. No habilitar decisiones automáticas basadas en la nota.

## Cambios

- Cada penalización debe citar el fragmento y la evidencia que demuestra el
  defecto; las afirmaciones no verificadas quedan como límites de confianza.
- Redondeo decimal a la precisión solicitada o mostrada; en empates sin regla
  explícita se aceptan HALF_EVEN y HALF_UP, también para negativos. Se conserva
  la precisión exigida y no se usa una tolerancia porcentual genérica.
- Se distinguen el fallo de recuperación, la causa de un fallo y una explicación
  auxiliar no verificada. Una negativa injustificada no cuenta además como
  narrativa inventada salvo que añada un hecho falso distinto.
- Las propuestas deben conservar las advertencias necesarias de antigüedad.
  El informe debe mostrar incertidumbres incluso en respuestas con 5/5.

Se reutilizan los campos existentes: no cambian el esquema JSON ni el motor
del loop. Son instrucciones semánticas, no una validación automática de que
las citas realmente demuestren lo que afirma el juez.

## Las 12 respuestas guardadas

Se revisaron respuestas, anchors y comentarios originales. Las diez sumas
de inventario y tres empates discutidos se comprobaron con `Decimal`.
La revisión es de Codex, **no una adjudicación humana**. La aprobación del
criterio se solicitó aparte y no se presupone recibida.

Cinco ejecuciones independientes por variante, todas con Codex CLI,
`gpt-5.6-terra`, reasoning `medium`, el mismo pack, anchors y contexto:

| Medida | Rúbrica original | Rúbrica final |
|---|---:|---:|
| Veredictos con contrato válido | 60/60 | 60/60 |
| Penalizaciones por los tres empates válidos | 15 | 0 |
| Penalizaciones por fallback sin resultado de la primera búsqueda | 5 | 0 |
| Flags de narrativa no demostrados por el pack | 34 | 0 |
| Notas del pack, sobre 60 | 53,5; 44,5; 53,5; 44,5; 44,5 | 58; 58; 57; 58; 58 |

Estas notas describen las respuestas del servicio bajo cada rúbrica, no la
precisión del juez. No se eligió la ejecución más favorable.

La versión final aún penaliza una vez la directness de Q11: confunde una
restricción de empresa con una exigencia de contestar únicamente una cifra.
Q02 sí pide únicamente un total en una unidad y añade una conversión; esa
deducción tiene respaldo. Las afirmaciones auxiliares de antigüedad y causas
operativas siguen sin poder verificarse con el pack original.

Se conservaron también las variantes intermedias: la primera obtuvo
58; 58; 52; 58; 58 y todavía produjo seis flags injustificados en una ejecución.
La segunda obtuvo 58 en las cinco, pero algunas recomendaciones eliminaban
el aviso de antigüedad. La revisión posterior corrigió ese criterio antes de
congelar la variante final. Los resultados intermedios no se sustituyeron.

## Casos reservados y regresiones

Un agente independiente preparó los casos y sus expectativas; el agente que
editó la rúbrica no leyó su contenido hasta congelarla y ejecutar el juez.
El juez recibió únicamente preguntas, respuestas, contexto, anchors y rúbrica,
nunca las etiquetas esperadas. La separación es de contexto e instrucciones,
no una barrera de filesystem: el harness tiene herramientas de lectura.

El primer conjunto reservado dio 12/12 fuentes causales correctas y 48/48
decisiones de flags; se considera consumido. Tras modificar otra vez la
rúbrica, se preparó un segundo conjunto nuevo, sin modificarlo después de
observar resultados.

| Comprobación con rúbrica final | Suite anterior | Segundo conjunto reservado |
|---|---:|---:|
| Casos | 12 | 12 |
| Fuentes causales correctas | 12/12 | **9/12** |
| Flags críticos detectados de los esperados | 5/5 | 6/6 |
| Flags adicionales / falsos positivos | 0 | **3** |
| Flags esperados omitidos | 0 | 0 |
| Decisiones binarias correctas | 48/48 | 45/48 |

Los 48 flags no son 48 escenarios independientes. Un solo juez sobre casos
sintéticos no estima la fiabilidad en producción.

Los fallos reservados son S01, S02 y S03: fechas de un pago, tamaño de una
cola y estado de un envío no prueban las causas que afirma la respuesta. El
juez convierte ausencia de datos causales en prueba de invención y atribuye
el defecto al modelo, aunque faltan resultados de herramientas. También los
incluye en un hallazgo cruzado sin aportar pruebas por caso.

Además, sus anchors no nulos documentan el evento, pero no responden a la
pregunta causal. Esto deja pendiente revisar qué constituye un anchor
suficiente para la pregunta, además del criterio del juez. No se cambiaron
los anchors ni las expectativas para hacer pasar este conjunto. Se requiere
revisión humana; ya no debe usarse como una reserva nueva al seguir afinando.

- [Conjunto reservado congelado](judge-evidence-calibration.json).
- [Salida observada completa](judge-evidence-calibration-results.json).
- [Suite previa y expectativas](judge-calibration.json).

SHA-256 de la rúbrica final:
`ffaf9d010ae4ba85d3e55490705fa28e9d2c92d90f2ee72e93b52f8a549f5612`.
SHA-256 del segundo conjunto reservado:
`fab39b84f12656e2c8ead2f28d507d12cceb24d6269fd16ab28d2acac3b9514e`.

Para comprobar las discrepancias guardadas, sin consultar servicios:

```sh
python3 - <<'PY'
import json
from pathlib import Path
base = Path('docs/dogfood')
fixture = json.loads((base / 'judge-evidence-calibration.json').read_text())
result = json.loads((base / 'judge-evidence-calibration-results.json').read_text())
expected = {c['question']['id']: c['expected'] for c in fixture['cases']}
flags = ('broken_tool', 'hallucinated_narrative', 'false_guardrail', 'unsafe_side_effect')
for v in result['verdicts']:
    e = expected[v['id']]
    actual = {f for f in flags if v[f]}
    if actual != set(e['critical_flags']) or v['failure_source'] != e['failure_source']:
        print(v['id'], 'esperado:', e, 'observado:', v['failure_source'], sorted(actual))
PY
```

Para repetir con un juez, seguir el procedimiento de pack guardado en
`skills/service-judge-loop/SKILL.md`: extraer question, pack y anchor; mantener
`expected`, las justificaciones y estos resultados fuera de sus entradas.

## Mejora concreta en Orito

Se reprodujo un defecto en la copia local de Orito, base `c00b084`:
`_aviso_datos_rancios` convertía la antigüedad de `created_at` en una afirmación
de que la carga automática no funcionaba, sin consultar su estado. Los tres
caminos de inventario usan ese helper.

Se corrigió el helper para conservar la antigüedad y la advertencia sobre
stock actual, y eliminar la causa no verificada. La nueva prueba falló con
el mensaje anterior y pasó tras el cambio. Pasan las 10 pruebas de frescura
y las 30 de disponibilidad; no se cambió el cálculo de existencias.

Se capturó la salida de `consultar_disponibilidad` real con respuestas de base
de datos controladas: snapshot antiguo, reciente y sin fecha. Un mismo juez
y la primera rúbrica candidata calificaron las salidas antes/después. El caso
antiguo pasó de `tool` + `broken_tool` + `hallucinated_narrative` a ningún
defecto demostrado; los dos controles se mantuvieron sin flags. En esta
fixture se establece independientemente que no existe un proceso de carga
automática. La nota conjunta cambió de 12/15 a 15/15.

Esto demuestra una mejora de la **salida del componente Python local**, no
de respuestas nuevas de un LLM ni del endpoint desplegado. No se supone que
esa copia local sea la revisión de staging. La validación completa de Orito
en staging queda pendiente.

[Parche portable con su prueba](orito-freshness.patch), aplicado y comprobado
en la copia local; no desplegado ni integrado en la rama principal de Orito.

## Verificación y alcance

- `test_loop.py` y `test_loop_e2e.py` pasan; E2E con 100 preguntas fixture.
- Tests de Orito: 40 pasan. El test de error simulado imprime el error esperado
  de la dependencia; no es una consulta fallida a un servicio real.
- 27 invocaciones del juez externo, 306 veredictos: cinco repeticiones para
  cuatro variantes reales, cinco evaluaciones sintéticas de 12 casos y dos
  comparaciones de componente de tres casos. Sin reintentos de formato.
- **Cero consultas nuevas a Orito**, cero llamadas directas a APIs de modelos,
  cero despliegues. El juez usa el harness externo autorizado anteriormente
  en esta conversación; comando redactado `codex …`, aislamiento solicitado
  de solo lectura. Consumo de tokens y coste monetario no capturados.
- Las respuestas reales, prompts, logs y la revisión detallada permanecen
  privados en `.context/calibration-20260910/`. Los JSON de esta carpeta pública
  solo contienen escenarios sintéticos.
- No se publicó versión ni se actualizó una instalación del plugin. La
  calibración permanece abierta por los fallos reservados y la revisión humana
  pendiente; una nota alta todavía no autoriza un piloto automático.
