# Tres dias sinteticos — corpus congelado de AC-1

**Como se generaron**: corriendo la cadena real `senales -> clasificar -> score ->
seleccionar` de `content_factory/cli.py`, con `--desde-raw`. **No se escribieron a
mano.** Esa es su unica razon de existir: si los produjera un guion paralelo,
dejarian de ser verdad de referencia del camino de produccion.

**Que tiene cada dia**: los crudos por fuente, `_manifiesto.json`,
`_derivados.json` y `corrida_esperada.json`, que es la fila real de `corridas`
escrita esa noche. `corrida_esperada.json` es el lado (ii) de AC-1: el replay se
compara contra ella, no contra otro replay.

**Son SINTETICOS y hay que decirlo**: los crudos salen de los fixtures del bloque
3, copiados a los tres dias. Los embeddings los calcula un modelo de juguete
determinista, no MiniLM, para que el corpus sea reproducible en cualquier maquina
y sin red. Lo que NO es sintetico es el camino: las etapas son las de produccion.

**Los tres dias no son iguales, y es deliberado**:

| Dia | TOP | Que ejercita |
|---|---|---|
| 2026-09-14 | 5 piezas | El caso con contenido: evidencia real y `pen_repeticion` > 0 en las cinco |
| 2026-09-15 | 1 pieza | RQ-27: TOP incompleto antes que relajar la compuerta |
| 2026-09-16 | 0 piezas | El acervo se agota. Es la evidencia de **OP-7** |

El dia 16 con TOP vacio **no es un defecto**: con el mismo acervo tres dias
seguidos, los temas ya publicados vuelven a competir, las penalizaciones los
hunden y la compuerta dura acaba cerrandoles el paso. Si un tema ya publicado
debe quedar excluido es politica editorial y la decide el operador (OP-7).

**Regla de uso**: la prueba de AC-1 exige que el dia 14 tenga estado derivado NO
VACIO en los dos lados. Sin esa comprobacion, una comparacion entre dos mapas
vacios pasaria byte a byte sin demostrar nada.
