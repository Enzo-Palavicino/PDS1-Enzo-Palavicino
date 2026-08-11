# Proyecto 1 - Ajustes cálculo puntaje

Estimadas y estimados,

Decidí hacer un ajuste al cálculo de puntaje de las partidas, para que no hubiera tanta diferencia entre unas y otras que provoque que por solo una partida podrían verse perjudicados de gran manera en su nota. Este cambio no afecta la estrategia misma de cada partida, sino que apunta a que el aporte de cada partida a la suma de un torneo sea más claro.

Ahora, el puntaje de cada partida **se normaliza según la cantidad total de solicitudes**, de modo que partidas de distinta escala sean más comparables.

El puntaje máximo se define así:

- **100 puntos** para partidas con 10 o menos solicitudes.
- **200 puntos** para partidas con 11 a 30 solicitudes.
- **300 puntos** para partidas con más de 30 solicitudes.

## Supervivencia

En modo Supervivencia, el puntaje depende de la fracción de solicitudes completadas:

```text
puntaje = máximo × solicitudes completadas / solicitudes totales
```

El resultado se redondea a entero.

## Menos celdas

En modo Menos celdas, el puntaje usa como referencia la solución verificada de la partida. Primero se calcula el costo del jugador:

```text
costo = celdas finales + N × celdas eliminadas
```

Luego se compara contra el costo de la solución almacenada:

```text
puntaje = máximo × costo referencia / costo jugador
```

El puntaje se redondea a entero y nunca puede superar el máximo de la partida. Así, igualar o mejorar la solución de referencia entrega el puntaje máximo, mientras que usar más celdas o modificar caminos lo reduce.

Nos vemos,  
Matías
