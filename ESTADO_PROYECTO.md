# Estado del proyecto GridLink

Última actualización: **2026-08-10** (tercera sesión). Documento de traspaso: contiene todo lo
necesario para retomar sin volver a deducir nada.

---

## 1. Plazos

| Instancia | Umbral | Techo | Vence |
|---|---|---|---|
| Torneo 2 (`tournament_id=2`) | ≥650 | 1600 | **2026-08-11 16:00** |
| Torneo 3 (`tournament_id=3`) | ≥900 | 1400 | 2026-08-12 12:00 |
| Entrega parcial (competencia en clase) | — | — | 2026-08-12 13:30 |
| Entrega final | — | — | 2026-08-26 |
| Post-mortem | — | — | 2026-09-02 |

**Restricciones permanentes:** nunca hacer `git commit` (Enzo maneja los commits); Python;
dirigirse a Enzo por su nombre.

---

## 2. Puntaje actual: **ambos umbrales superados**

| Torneo | Puntaje | Umbral | Estado |
|---|---|---|---|
| 2 (ids 12-19) | **793** / 1600 | 650 | ✅ superado con holgura |
| 3 (ids 20-27) | **945** / 1400 | 900 | ✅ superado |

**Verificado contra el servidor el 2026-08-10 (pasada de consolidación).** La API no expone ningún
endpoint de ranking — `GET /api/tournaments/` sólo devuelve nombre, descripción y fecha de cierre,
y `game_status` muestra la **sesión vigente**, no el máximo histórico. Es decir, **no hay forma de
confirmar desde afuera si el torneo cuenta la mejor sesión o la última.**

La solución fue volver el punto irrelevante: se rejugó cada partida con la estrategia que la gana,
dejando como sesión vigente el mejor resultado conocido de cada una. Las 16 alcanzaron su objetivo
al primer intento (lo que confirma de paso que las estrategias son deterministas), y un barrido de
`game_status` verificó los 16 puntajes uno por uno. **Cuente lo que cuente el servidor, el
resultado es el mismo.**

Si se vuelve a correr el benchmark completo antes de un cierre de torneo, hay que **repetir la
consolidación después**, porque el benchmark deja como sesión vigente la de la última estrategia
probada, que rara vez es la mejor. El procedimiento:

```bash
./.venv/bin/python benchmark.py --report     # ver el mejor por partida
# results/consolidation_plan.json guarda qué estrategia gana cada partida
```

Detalle Torneo 2: 12→100, 13→56, 14→**15**, 15→162, 16→75, 17→225, 18→**75**, 19→**85**.
Detalle Torneo 3: 20→100, 21→100, 22→200, 23→**42**, 24→200, 25→**117**, 26→86, 27→100.

Las partidas en negrita son las que siguen dejando margen — **salvo id 14, que está en su techo
absoluto de 15 (ver §4bis)**. Descontando id 14, quedan ~1100 puntos alcanzables sin capturar,
casi todos en las partidas de muchas solicitudes (18, 19, 23, 25).

### Cómo reproducir el puntaje

```bash
./.venv/bin/python benchmark.py    # corre el banco activo sobre las 16 partidas
```

Corre las 5 estrategias de `STRATEGIES` más el jugador `retry` (§2.c) sobre cada partida y deja que
el servidor se quede con la mejor. La diversidad **por estrategia** funciona; la diversidad por
semilla aleatoria no, y la diversidad estructural con rodeos es peor que no diversificar (§2.b.8).

---

## 2.b Banco de estrategias (`benchmark.py`)

`gridlink/strategies.py` define varios jugadores con la misma firma, y
`benchmark.py` los mide en igualdad de condiciones acumulando el histórico en
`results/benchmark.json`.

```bash
./.venv/bin/python benchmark.py                       # todas, las 16 partidas
./.venv/bin/python benchmark.py --games 14 18 25      # sólo algunas
./.venv/bin/python benchmark.py --report              # re-imprimir lo guardado
```

Resultado sobre las 16 partidas (techo 3000):

| Estrategia | Total | Partidas donde es la mejor |
|---|---|---|
| **`retry`** (reintentos con resets) | **1657** | **21 (100/100)**, 12, 19, 25, 26 |
| `shortest` (BFS puro) | 1582 | **25**, 19 |
| `shortest_turns` (menos quiebres) | 1562 | **15 (162, récord)**, 26 |
| `cortisimo` | 1540 | ninguna en exclusiva |
| `bidirectional` (doble mapeo) | 1512 | **18**, 26 |
| `hibrido`, `corto` | 1492 | ninguna |
| `shortest_safe` | 1444 | **17 (225, récord)** |
| `fragmentacion` | 1413 | ninguna |
| `shortest_open` | 1376 | ninguna |
| `evasivo` | 1373 | **12** |
| `shortest_hug` | 1371 | ninguna |
| `waypoints` | 1144 (peor) | ninguna |
| **Mejor de todas combinadas** | **1738** | — |

### 2.c El jugador de reintentos (`gridlink/retry_player.py`)

**Es el mejor jugador individual del proyecto: 1657, contra 1582 del BFS pelado.** No es una
estrategia de camino sino un ciclo completo de partida, así que tiene su propia entrada en
`benchmark.py` (`--strategies retry`) y se corre por defecto.

Cómo funciona, y por qué así:

- **Busca offline y verifica online.** Con `bridge_probability = 0` y `potential_count = 0` el
  tablero evoluciona de forma determinista, así que `Board.mark_path` reproduce exactamente lo que
  hace el servidor. La búsqueda (backtracking cronológico sobre qué camino usar en cada ronda) es
  local y gratis; el HTTP se gasta sólo en verificar. Si esos parámetros no son cero, el jugador
  **se abstiene** con un error explícito en vez de jugar sobre un modelo infiel.
- **La limitación que le da forma:** al morir, el servidor devuelve `requests: []`, así que nunca
  vemos la solicitud que nos mató. No se puede buscar dirigido contra ella; sólo proponer líneas
  distintas para lo conocido y dejar que el servidor juzgue.
- **Ventana de reintento que se ensancha hacia atrás.** Morimos en la solicitud siguiente a la
  última conocida, así que variar la ronda 1 cuando morimos en la 20 es irrelevante *y* rompe el
  prefijo compartido con el servidor. La ventana arranca en la última ronda y sólo se ensancha
  cuando esa cola se agota. Además `_Session.submit` reenvía únicamente las jugadas que cambian,
  lo que baja el costo de O(M²) a O(M) llamadas HTTP y es lo que lo hace viable en partidas largas.
- **Los rodeos son el último recurso, y ahí sí sirven.** El orden de candidatos replica el ranking
  medido (BFS, menos quiebres, desempates por daño) y deja los rodeos al final. Un buscador con
  backtracking sólo llega a ellos cuando ya agotó los caminos mínimos, o sea cuando la alternativa
  era perder la ronda. **Medido:** sin rodeos saca 3/5 en id 12; con rodeos al final, 5/5.

Resultado: iguala o supera el mejor histórico en 13 de 16 partidas y **resuelve id 21 completa
(10/10, 80 → 100)**. Tarda ~40 s por partida con el presupuesto por defecto, dentro del límite de
10 min por lote de 10.

### 2.d El runner de competencia (`gridlink/tournament_runner.py` + `tournament.py`)

```bash
./.venv/bin/python tournament.py --tournament 3              # lote completo
./.venv/bin/python tournament.py --tournament 3 --no-plan    # simula partidas nuevas
./.venv/bin/python tournament.py --games 20 21 --budget 120
```

**No es un benchmark.** `benchmark.py` mide y tarda lo que haga falta; esto juega en competencia,
donde la restricción dura es 10 partidas en 10 minutos y quedarse sin reloj cuesta 100-300 puntos
por partida sin jugar. Qué hace distinto:

- **Presupuesto global repartido sobre la marcha** (`restante / pendientes`), así cada partida que
  termina antes le dona su sobrante a las siguientes.
- **Techo por partida (`max_game_budget = 45 s`).** `retry` se estanca cerca de los 40 s. Medido en
  vivo: sin techo, id 23 recibió 119 s y sacó los mismos 42 puntos que saca en 1.4 s. Ponerlo bajó
  el lote de 390.6 s a **282.7 s con puntaje idéntico**.
- **Degradación elegante:** si la cuota baja de 18 s, se juega con `shortest` en vez de `retry`.
  1582 contra 1657 es una diferencia chica al lado de dejar una partida en 0.
- **Orden ascendente por solicitudes:** las chicas primero, que aseguran puntos temprano y donan
  tiempo a las caras.
- **Aislamiento de fallos:** cada partida en su propio `try`; un error de red no voltea el lote.
- **Red de seguridad contra la abstención.** `retry` se abstiene si la partida no permite resets,
  trae puentes potenciales o ventana > 1. Sin rescate esa partida quedaría en **0**, y como los
  lotes evaluados vienen "con diferentes parámetros" (enunciado §4.1), un lote entero podría irse
  a cero. El runner la rejuega con `shortest`, que no depende de ningún parámetro.
- **Fase de mejora** con el sobrante, con la disciplina de consolidación de §2: si el reintento
  saca menos, se restaura el jugador bueno para que la sesión vigente nunca quede peor.
- **Log en `results/logs/`** para el post-mortem, como pide el enunciado.

**Sobre `total_budget` (510 s contra el límite de 600 s).** Una partida puede excederse de su cuota
(medido: 7.8 s en id 25), pero **ese exceso no se acumula**: descuenta de `remaining` y achica la
cuota de las siguientes, así que el total está acotado por el presupuesto más el exceso de *una*
partida (~525 s). Latencia medida: 253 ms por llamada; el lote de 8 extrapola a 353 s en 10
partidas. Si la red de la universidad está más lenta, el runner **no se pasa del límite**: gasta el
presupuesto, salta la fase de mejora y degrada al jugador simple. Falla de forma segura.

Validado en vivo contra el Torneo 3: 945/1400, las 8 partidas jugadas, sin errores.

### Trampa que ya costó puntos: una sesión a medio jugar vale 0

El servidor **sólo asigna `score` a partidas terminadas**. Si `retry` agota su presupuesto de
tiempo con la partida todavía viva, sale del bucle y deja la sesión en `in_progress`: `game_status`
devuelve `score: null` y todo lo conseguido se pierde. Pasó de verdad el 2026-08-10 — id 26 quedó
en **0** después de haber sacado 86, y el Torneo 3 cayó a 859, bajo el umbral de 900.

Arreglado en `retry_player._finish_greedily`: antes de devolver, si la partida sigue viva se cierra
jugando el camino más corto en cada ronda. Es barato (un BFS y una jugada por ronda) y se hace
**aunque el reloj ya se haya pasado**, porque cualquier final es infinitamente mejor que ninguno.
Hay un test de regresión (`test_never_leaves_the_session_unfinished`).

**Al verificar el estado de un torneo, revisar `status == "completed"`, no sólo el puntaje.**

### Banco activo (`STRATEGIES`) contra descartadas (`DISCARDED`)

Sólo cinco estrategias de camino ganan alguna partida en exclusiva, y **esas cinco
más `retry` producen los 1738 puntos completos**: `shortest`, `shortest_turns`,
`shortest_safe`, `bidirectional` y `evasivo`. El resto está en `DISCARDED` en el
mismo módulo — se conservan porque sus resultados negativos son la evidencia de
la ley de diseño de §5, y porque su maquinaria (`waypoint_candidates`, `_damage`)
sirve para diagnosticar tableros. `benchmark.py` corre el banco activo por
defecto pero acepta cualquiera de las dos por nombre.

### Conclusiones medidas

1. **`shortest`, sin ninguna heurística, es la mejor estrategia individual.** Toda la maquinaria
   de evaluación de candidatos del `pathfinder` rinde *menos* que un BFS puro. En estos tableros
   nuestras heurísticas hacen daño neto, coherente con el hallazgo de §5 (los rodeos cuestan más
   que el riesgo que evitan).
2. **La peor estrategia global aporta igual.** `evasivo` es la última del ranking pero es la única
   que saca 100 en id 12. Sirve de argumento para mantener un portafolio en vez de elegir una sola
   ganadora: el servidor guarda el mejor puntaje por partida.
3. **El doble mapeo (A* Manhattan hacia adelante + BFS hacia atrás) aporta de verdad**, aunque no
   como se esperaba: no gana en general, pero es la única que despega en id 18 (75 contra 40 del
   resto) y en id 26. Su valor es la *forma distinta* del camino, no la velocidad.
4. **`corto`, `cortisimo` e `hibrido` no aportan nada en exclusiva.** Descartados del registro.
5. **Variantes del camino mínimo** (`shortest_safe`, `shortest_open`): de entre todos los caminos
   de largo mínimo eligen el de menor daño, resolviendo por programación dinámica sobre el DAG de
   caminos mínimos (exacto, sin muestreo). La idea era conservar la ventaja de `shortest` sin
   pagar rodeos. **Globalmente fallaron** (1444 y 1376 contra 1582 de `shortest`): desempatar por
   presión de conectores perjudica más veces de las que ayuda. **Pero `shortest_safe` logró el
   mejor resultado individual del proyecto: 225 en id 17**, contra 195 del anterior. Portafolio:
   1672 → **1702**.

6. **La forma del camino importa, y la dirección correcta es "recto", no "pegado al muro".**
   `shortest_turns` (minimiza quiebres) y `shortest_hug` (prefiere celdas contra un muro) son
   hipótesis opuestas con el mismo largo exacto, así que la comparación aísla el efecto de la
   forma: 1562 contra 1371, y `shortest_turns` puso el récord de id 15. Es el eje productivo.
7. **Medir el daño real rinde PEOR que aproximarlo.** `fragmentacion` construye cada tablero
   resultante y lo evalúa por orden lexicográfico (pares satisfacibles, conectores ahogados,
   largo) en vez de usar un costo por celda. Quedó en 1413, por debajo del BFS pelado.
8. **La diversidad estructural con rodeos es activamente dañina.** `waypoints` fuerza el camino a
   pasar por puntos intermedios repartidos por el tablero y elige por daño medido, aceptando
   rodeos de hasta 1.5x el mínimo. Sacó **1144, la peor de todas**, y es la única estrategia que
   *pierde* una partida que todas las demás ganan (id 22: 200 → 138). Con `slack=1.0` se reduce a
   `fragmentacion` (1413), así que **toda la caída de 1413 a 1144 viene de permitir rodeos**.

### Patrón consistente que vale la pena tener presente

Ninguna estrategia domina: cada una que agregamos rinde peor que `shortest` en total pero algunas
ganan alguna partida en exclusiva, y el portafolio sigue creciendo (1585 → 1672 → 1702 → 1718).
Como el servidor guarda el mejor puntaje por partida, **agregar estrategias diversas rinde más que
perfeccionar una sola** — pero el rendimiento decreciente ya se nota: las cuatro últimas
estrategias agregadas sumaron 16 puntos entre todas, y tres de ellas no aportaron nada.

### La ley de diseño, confirmada por cuatro vías independientes

**Nuestras métricas de daño al tablero no tienen poder predictivo.** Confirmado por:
(a) bajar `connector_penalty` de 6.0 a 1.0 subió el completado de 20% a 27% (§5);
(b) `satisfiable_pair_weight=400` fue un retroceso de 1410 a 1370;
(c) `waypoints`, con diversidad estructural real y un selector que mide el tablero de verdad,
cayó de 1413 a 1144 sólo por permitir rodeos;
(d) **el caso decisivo:** en `retry_player`, elegir entre líneas por `line_health` (fracción de
pares de conectores que siguen conectables) **no mejoró ninguna partida y empeoró id 26**
(12/28 → 10/28). Y ahí la métrica era *gratis*: todas las líneas comparadas completaban
exactamente las mismas solicitudes, así que no había ningún paso de más que pagar. Queda
implementada pero apagada tras `RetryConfig.use_line_health`.

El punto de (d) es que ya no se puede echar la culpa al costo en largo. La métrica simplemente no
predice qué solicitudes van a sobrevivir.

Corolarios prácticos:
1. **Toda estrategia de camino nueva debería restringirse a largo mínimo y diferenciarse sólo en
   cuál de ellos elige.** Ahí están los dos récords del proyecto (`shortest_turns` en id 15,
   `shortest_safe` en id 17).
2. **La excepción es el backtracking.** Un rodeo evaluado *a priori* es dañino, pero un rodeo al
   que sólo se llega tras agotar los caminos mínimos se toma cuando la alternativa era perder la
   ronda, y ahí sí paga (id 12: 3/5 → 5/5).
3. **No invertir más en métricas de salud del tablero.** Cuatro intentos, cuatro fracasos.

---

## 3. Qué está construido

Todo en `gridlink/`, con tests en `tests/` (126 tests, todos pasando).

- **`board.py`** — `Cell`/`Board`: parseo/serialización del tablero, ocupación y capacidades.
- **`rules.py`** — **única fuente de verdad sobre legalidad**, compartida entre simulador y
  jugador. `order_path` replica la verificación por grados del servidor.
- **`scoring.py`** — fórmulas de puntaje. **Verificadas exactas** contra el servidor.
- **`simulator.py`** — servidor local. **Advertencia:** garantiza solución, cosa que el servidor
  real no hace (`has_verified_solution: false`); los puntajes locales salen optimistas.
- **`api_client.py`** — `GridLinkClient` + `RemoteGame` (intercambiable con `GameSimulator`).
- **`pathfinder.py`** — candidatos vía Dijkstra con costo por celda + evaluación del tablero
  resultante. **Su premisa de diseño resultó falsa (§4bis); mide peor que un BFS pelado.**
- **`strategies.py`** — banco de jugadores (`STRATEGIES` activas, `DISCARDED` medidas y
  descartadas), más la maquinaria de diagnóstico de tableros.
- **`game_runner.py`** — `play_game()`, ciclo completo con logging por ronda.
- **`retry_player.py`** — `play_with_retries()`, el mejor jugador individual (1657). Busca líneas
  completas offline y usa el servidor como oráculo, aprovechando que los resets están permitidos
  y que la secuencia de solicitudes es determinista. Ver §2.c.
- **`knowledge.py`** — persistencia de secuencias de solicitudes observadas (ver §5, su utilidad
  resultó ser limitada).

- **`tournament_runner.py`** — corrida de competencia con presupuesto global, degradación
  elegante y logs en disco. Ver §2.d. CLI: `tournament.py`.

```bash
python3 -m venv .venv && ./.venv/bin/python -m pip install pytest requests
./.venv/bin/python -m pytest -q
```

API key en `.gridlink_key` (ignorado por git) o en `GRIDLINK_API_KEY`. **Nunca en el código.**

---

## 4. Comportamiento verificado de la API

Detalle completo en `CLAUDE.md`. Lo esencial:

1. **`current_turn` es un contador atrasado** (= `completed_requests`, vale 0 antes de jugar). El
   ID de conexión a enviar es `completed_requests + 1`. Usar `current_turn` mandaría `K0`.
2. **Los caminos no pueden tocarse a sí mismos** (dos celdas no consecutivas no pueden ser vecinas
   ortogonales). Mensaje de error del servidor engañoso.
3. Jugadas inválidas llegan con **HTTP 400**.
4. La secuencia de solicitudes es **fija y determinista** por partida.
5. `request_count == connector_count / 2` → todo conector termina siendo solicitado.
6. Resets permitidos; el servidor **guarda el mejor puntaje por partida**.

Sin verificar: la **regla de lados de puentes** (interpretada conservadoramente). Todas las
partidas de la entrega parcial traen `bridge_count: 0`. Revisar antes de la entrega final.

---

## 4bis. Dos "hechos verificados" que resultaron FALSOS (2026-08-10)

Ambos estaban escritos como verificados en `CLAUDE.md` y guiaron decisiones de diseño. Ya
corregidos ahí, pero conviene tenerlos presentes porque explican resultados pasados.

1. **`request_count == connector_count / 2` es falso en 12 de 16 partidas.** Sólo se cumple con
   igualdad en ids 12, 17, 20 y 24. En id 18 sobran **40 conectores de 160** que nunca se piden;
   en id 26, 24 de 80. Como no hay forma de saber cuáles sobran, **proteger a todos los conectores
   por igual es demasiado conservador** — y esa premisa es exactamente la que está escrita al
   inicio de `pathfinder.py` justificando todas sus heurísticas. Es la explicación de por qué el
   BFS pelado le gana a todo el módulo.
2. **No todas las partidas de la entrega parcial tienen `bridge_count: 0`.** 12 de 16 tienen
   puentes, entre 6% y 20% de las celdas (id 18 tiene 200). Sólo ids 12, 19, 22 y 24 no tienen.
   La regla de lados de puentes **sigue sin verificarse**: ninguna jugada ha sido rechazada por
   ella, pero eso es evidencia débil. Revisar antes de la entrega final.

### id 14 tiene techo 15/200 y no es culpa nuestra

Las 11 estrategias sacan exactamente 1/13 en id 14, lo que hizo sospechar. El tablero inicial
tiene **una isla de 27 celdas desconectada de la región principal de 120**: los conectores 6, 8,
17 y 28 viven sólo en la isla y 16 conectores sólo en la región grande. Cualquier solicitud que
empareje los dos grupos es imposible desde el turno cero. Se enviaron al servidor **16 caminos
estructuralmente distintos** para la ronda 1 (de 7 a 21 pasos, generados con waypoints) y **los
16 terminan la partida de inmediato**. `has_verified_solution: false` no era decorativo.

**No gastar más tiempo en id 14.** De los ~600 puntos que parecían faltar, ~185 no existen.

### El predictor real de nuestro rendimiento es el número de solicitudes

Cruzando puentes, bloqueos, densidad y tamaño contra el puntaje, lo único que correlaciona es
`request_count`: **las 5 partidas de ≤16 solicitudes las ganamos al 100%** (ids 12, 20, 22, 24,
27) **y todas las de ≥24 se caen** (23 → 21%, 18 → 25%, 19 → 28%, 25 → 39%, 26 → 43%). No es el
tablero: es que en supervivencia cada ronda degrada el tablero y hay que aguantar muchas más
rondas. Ahí está el margen real que queda.

---

## 5. Experimentos hechos (no repetir)

1. **Aprender la secuencia de solicitudes para planificar con información completa: NO FUNCIONA.**
   Cuando la partida termina bloqueada el servidor devuelve `requests: []`, así que sólo se
   aprende la solicitud k+1 si se completa la k *y la partida sigue viva*. En id 14 morimos en la
   ronda 2 y nunca vemos la solicitud 2: el bucle se atasca sin ganar información.
   `knowledge.py` y el parámetro `known_sequence` de `play_game` quedan implementados y
   funcionando, listos por si sirven con otra estrategia (p. ej. tras mejorar la supervivencia).
2. **Métrica "pares satisfacibles" con peso 400 reemplazando el aislamiento:** retroceso neto
   (1410 → 1370). Acepta caminos larguísimos con tal de no partir componentes.
3. **Mejor de N con semillas distintas: inútil.** Varias partidas dan resultados idénticos entre
   semillas; los candidatos deterministas ganan casi siempre. La aleatorización actual no genera
   diversidad real. La diversidad **por configuración** sí funciona (§2).
4. **`risky_connector_weight` no discrimina nada** por sí solo: con 25 o con 200 el resultado es
   idéntico partida por partida.
5. **La diversificación por ruido está muerta, y ahora está cuantificado.** En id 14 ronda 1, 122
   intentos con `noise` alto produjeron **2 caminos distintos**. Los waypoints forzados producen
   16 sobre el mismo tablero. El reemplazo está implementado (`waypoint_candidates`), pero medido
   resultó dañino (§2.b.8), así que el problema nunca fue la falta de diversidad.
6. **Insistir en id 14: NO. Es imposible, no la jugamos mal.** Ver §4bis.

### El hallazgo que desbloqueó los umbrales

Los tableros problemáticos **no están condenados estructuralmente**. Medido sobre el tablero
inicial: id 23 es prácticamente una sola componente libre (319 celdas) con cota de 30 solicitudes
satisfacibles sobre 24 pedidas; id 18 es una única componente de 1040 celdas con cota 80 sobre 60.
Las perdíamos por jugar mal.

El mecanismo concreto (diagnosticado en id 23, ronda 2): la solicitud pedía unir `(3,39)` con
`(2,11)`, a 29 pasos de distancia mínima, y el camino elegido usaba **43 pasos**. La penalización
por acercarse a conectores (`connector_penalty=6.0` contra un costo base de 1.0) hacía serpentear
el camino, y en un tablero denso ese camino largo actúa como **muro que parte el tablero**.

Bajar `connector_penalty` de 6.0 a 1.0 y `risky_connector_weight` de 25 a 2 subió la media de
solicitudes completadas de 20% a 27% (id 17: 11/40 → 24/40, id 23: 2/24 → 5/24). Ya está aplicado
como default, con el porqué comentado en el código.

**Regla general aprendida: en tableros densos, el espacio gastado por un camino largo cuesta más
que el riesgo que ese rodeo evita.**

---

## 6. Próximos pasos, por prioridad

1. **Más variantes del camino mínimo.** Es el único eje que sigue dando: los dos récords del
   proyecto salen de ahí y `shortest_turns` quedó segunda en total. Ideas no probadas, todas
   resolubles por programación dinámica sobre el DAG de caminos mínimos (patrón ya implementado en
   `cheapest_shortest_path` y `min_turns_shortest_path`): maximizar quiebres en vez de
   minimizarlos; pegarse a un borde específico del tablero; minimizar quiebres con desempate por
   espacio abierto; preferir el camino más cercano/lejano a la diagonal. Todas cuestan una pasada
   y respetan la ley de §2.b.
2. **Afinar el jugador de reintentos (§2.c), que ya es el mejor individual.** Lo que más limita
   hoy es que la búsqueda offline agota su diversidad rápido: en id 13 y 16 se queda sin líneas
   nuevas antes de gastar el presupuesto de tiempo. Falta ampliar el conjunto de candidatos por
   ronda **sin** caer en rodeos evaluados a priori (§ la ley de diseño), por ejemplo con las
   variantes del camino mínimo del punto 1, que se enchufan directo en `candidate_paths`.
3. Antes de la entrega final: verificar la regla de puentes (§4bis) y agregar el modo
   **Menos celdas** (`extract_added_cells` hoy prohíbe tocar conexiones previas, que es sólo
   correcto en Supervivencia).

**Descartados como próximos pasos** (ver §4bis y §5): insistir en id 14 (imposible), arreglar la
diversidad de candidatos (ya arreglada, y resultó dañina), y afinar los pesos de `pathfinder.py`
(su premisa de diseño es falsa).
