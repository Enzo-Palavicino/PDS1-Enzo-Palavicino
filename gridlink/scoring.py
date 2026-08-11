"""Cálculo de puntaje según enunciado/Proyecto_1_Ajustes_calculo_puntaje.md.

Ese ajuste reemplaza las fórmulas de puntaje por partida del PDF original: ahora
el puntaje se normaliza por la cantidad total de solicitudes de la partida.
"""

from __future__ import annotations


def max_score(total_requests: int) -> int:
    """Puntaje máximo de una partida según su cantidad total de solicitudes."""
    if total_requests <= 10:
        return 100
    if total_requests <= 30:
        return 200
    return 300


def _round_half_up(value: float) -> int:
    # El `round` de Python redondea al par más cercano (round-half-even), que no
    # es lo que suele hacer un servidor. Usamos medio hacia arriba para que
    # nuestra estimación local calce con el puntaje informado por la API.
    return int(value + 0.5)


def survival_score(completed_requests: int, total_requests: int) -> int:
    """Supervivencia: máximo x (solicitudes completadas / solicitudes totales)."""
    if total_requests <= 0:
        return 0
    ratio = completed_requests / total_requests
    return _round_half_up(max_score(total_requests) * ratio)


def fewest_cells_score(
    reference_cost: float, player_cost: float, total_requests: int
) -> int:
    """Menos celdas: máximo x (costo de referencia / costo del jugador), tope máximo."""
    maximum = max_score(total_requests)
    if player_cost <= 0:
        return 0
    return min(maximum, _round_half_up(maximum * reference_cost / player_cost))


def player_cost(final_cells: int, removed_cells: int, penalty: float) -> float:
    """costo = celdas finales + N x celdas eliminadas."""
    return final_cells + penalty * removed_cells
