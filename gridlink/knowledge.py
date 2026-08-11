"""Memoria de las secuencias de solicitudes ya observadas.

La secuencia de solicitudes de cada partida es fija y determinista (verificado
contra el servidor: tras un reset se repite idéntica), y los resets están
permitidos. Eso convierte el problema de "decidir a ciegas" en uno de
información completa: cada corrida revela una solicitud más de las que ya
conocíamos, y la corrida siguiente puede rutear sabiendo qué viene después.

Con ventana de tamaño 1 lo observado es siempre un prefijo de la secuencia real,
así que basta con quedarse con el prefijo más largo visto hasta ahora.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_STORE = Path(__file__).resolve().parent.parent / "request_sequences.json"


class RequestKnowledge:
    def __init__(self, path: Path | str = DEFAULT_STORE):
        self.path = Path(path)
        self._sequences: dict[str, list[list[int]]] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            self._sequences = json.loads(self.path.read_text())

    def save(self) -> None:
        self.path.write_text(json.dumps(self._sequences, indent=1, sort_keys=True))

    def get(self, game_id: int) -> list[list[int]]:
        return self._sequences.get(str(game_id), [])

    def observe(self, game_id: int, sequence: list[list[int]]) -> bool:
        """Registra lo visto en una corrida. Devuelve True si aportó información."""
        known = self.get(game_id)
        if len(sequence) <= len(known):
            return False
        self._sequences[str(game_id)] = [list(request) for request in sequence]
        return True

    def coverage(self, game_id: int, total_requests: int) -> str:
        return f"{len(self.get(game_id))}/{total_requests}"
