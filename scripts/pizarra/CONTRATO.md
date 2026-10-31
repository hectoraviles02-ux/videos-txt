# Contrato del motor pizarra

**Entrada:** `guiones\pizarra\AAAA-MM-DD_<slug>\guion.json`
**Comando:** `python scripts\producir_pieza.py guiones\pizarra\AAAA-MM-DD_<slug>` (o directo `scripts\pizarra\producir_pizarra.py`).

## guion.json
| Campo | Obligatorio | Qué es |
|---|---|---|
| `motor` | sí | `"pizarra"` |
| `id` | sí | nombre base del video |
| `titulo` | sí | título en pantalla (debe caber: el motor se detiene si no) |
| `leyenda` | sí | «EJEMPLO / CÁLCULO / MODELO ILUSTRATIVO · …» (siempre visible, nunca tapada) |
| `remate` | no | `{"texto", "en": ancla}` recuadro ámbar |
| `voz` | sí | `{"motor":"chatterbox","referencia":<wav>,"pausa_s","semilla"}` |
| `tramos` | sí | `[{"id":"t1","texto":…}]` — **sin cifras** (todo en palabras) |
| `verificaciones` | sí | `[{"desc","expr","esperado","tol"?}]` cada número narrado, recalculado |
| `musica` | no | `{"archivo","nivel_db"}`; **sin música por defecto** hasta verificar licencia por pista y destino |
| `pistas` | no | `{"nombre": [[ancla, valor], …]}` valores que cambian con la narración |
| `variables` | no | `{"nombre": "expresión"}` con pistas, `d_<pista>`, `tiempo` |
| `contadores` | no | `{"desde", "filas":[{"texto":"t = {tau:4.1f}", "color", "desde"?, "hasta"?}]}` |
| `medidor` | no | `{"expr","min","max","unidad","desde","color"}` |
| `escena` | sí | `{"tipo": "precio" \| "taza" \| "tanque", …}` |
| `centro` | sí | `{"tipo": "grafica" \| "barras", …}` (+ `notas`) |
| `formulas` | no | `[{"tokens":[[tex,color],…],"desde","hasta"?,"marco"?}]` |
| `pulsos` | no | `[{"objetivo":"barra:1"\|"insignia:0"\|"objeto"\|"formula"\|"nota"\|"contador:2"\|"punto","en":ancla}]` |

**Anclas:** `"t3"`, `"t3:fin"`, `"t3/palabra"`, `"t3/palabra#2"`, con desfase `"+0.4"`/`"-0.3"`. Palabra inexistente → se detiene.
**Colores:** ambar, blanco, tenue, rosa, cian, lima, violeta, agua, vapor. Texto mínimo 27 px.

## Salida
- `<pieza>\entrega\<AAAAMMDD_HHMMSS>_<huella8>\<id>_9x16.mp4` (solo lectura; nunca se sobrescribe)
- `<pieza>\entrega\…\MANIFIESTO_<id>_9x16.json`: huellas (video, guion, tiempos, mezcla, cada archivo del motor, candado),
  revisiones, verificaciones, anclas medidas.
- `<pieza>\produccion.log`; si falla: `MANIFIESTO_NO_APROBADO_<fecha_hora>.json` y no se congela.

## Revisiones (todas deben pasar)
render repetido idéntico · parpadeo · movimiento_pizarra (< 3 s quieto) · audio −14 ± 1 LUFS, pico ≤ −1 dBFS ·
duración 61–90 s · cobertura de voz ≥ 80 % por tramo (recalculada siempre) · verificaciones aritméticas.

## Recursos
Toma el candado de la fábrica (`telegram_bot\candado.py`) durante toda la corrida; exige ≥ 6 GB libres de GPU para la voz.
Medido (62 s de video): voz 157 s / 5 GB RAM / 5.7 GB VRAM · alineación 34 s · render 129 s / 2.2 GB RAM (×2 renders).

## Códigos de salida
0 entregado · 1 compuerta, revisión o motor desactivado · 2 fábrica ocupada o GPU sin memoria.
