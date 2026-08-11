"""Cliente HTTP de la API de GridLink.

`RemoteGame` expone la misma interfaz que `simulator.GameSimulator`
(`start`/`get_status`/`move`/`reset`), para que el runner de partidas pueda
correr indistintamente contra el simulador local o contra el servidor real.

La API key nunca se escribe en el código: se lee de la variable de entorno
GRIDLINK_API_KEY o del archivo `.gridlink_key` (ignorado por git).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests

BASE_URL = "https://gridlink-app-imzah.ondigitalocean.app"
KEY_FILE = Path(__file__).resolve().parent.parent / ".gridlink_key"


class GridLinkError(Exception):
    """Error al comunicarse con la API."""


def load_api_key() -> str:
    key = os.environ.get("GRIDLINK_API_KEY")
    if key:
        return key.strip()
    if KEY_FILE.exists():
        return KEY_FILE.read_text().strip()
    raise GridLinkError(
        "No se encontró la API key: define GRIDLINK_API_KEY o crea el archivo "
        f"{KEY_FILE.name} en la raíz del proyecto."
    )


class GridLinkClient:
    """Wrapper delgado sobre los endpoints de la API.

    Reintenta ante fallas de red y errores 5xx porque la evaluación se corre
    sobre la red de la universidad, donde una caída puntual no debería costar
    una partida completa.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = BASE_URL,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff: float = 0.5,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key or load_api_key()})

    def _request(self, method: str, path: str, **kwargs) -> dict | list:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                response = self.session.request(
                    method, url, timeout=self.timeout, **kwargs
                )
            except requests.RequestException as error:
                last_error = error
            else:
                # Un 4xx es determinista: reintentar no lo va a arreglar y solo
                # gasta tiempo del presupuesto de la partida.
                if response.status_code < 500:
                    return self._parse(response)
                last_error = GridLinkError(
                    f"{method} {path} -> HTTP {response.status_code}: {response.text[:200]}"
                )

            if attempt < self.max_retries - 1:
                time.sleep(self.backoff * (2**attempt))

        raise GridLinkError(f"{method} {path} falló tras {self.max_retries} intentos: {last_error}")

    def _parse(self, response: requests.Response) -> dict | list:
        try:
            payload = response.json()
        except ValueError:
            raise GridLinkError(
                f"Respuesta no-JSON (HTTP {response.status_code}): {response.text[:200]}"
            ) from None
        if response.status_code >= 400:
            # Una jugada inválida llega como HTTP 400 con el detalle en el cuerpo.
            # No es un error de transporte: es información de juego que el runner
            # necesita ver, así que se devuelve en vez de lanzar.
            if isinstance(payload, dict) and payload.get("valid") is False:
                return payload
            raise GridLinkError(f"HTTP {response.status_code}: {payload}")
        return payload

    # ------------------------------------------------------------ endpoints

    def health(self) -> dict:
        return self._request("GET", "/api/health/")

    def list_games(self, tournament_id: int | None = None) -> list:
        params = {"tournament_id": tournament_id} if tournament_id is not None else None
        return self._request("GET", "/api/games/", params=params)

    def list_tournaments(self) -> list:
        return self._request("GET", "/api/tournaments/")

    def start_game(self, game_id: int) -> dict:
        return self._request("POST", f"/api/games/{game_id}/start/")

    def game_status(self, game_id: int) -> dict:
        return self._request("GET", f"/api/games/{game_id}/status/")

    def send_move(self, game_id: int, board: str) -> dict:
        return self._request("POST", f"/api/games/{game_id}/moves/", json={"board": board})

    def reset_game(self, game_id: int) -> dict:
        """Borra la sesión del jugador. Resetear sin sesión activa es un no-op."""
        try:
            return self._request("POST", f"/api/games/{game_id}/reset/")
        except GridLinkError as error:
            if "No existe una sesión" in str(error):
                return {"reset": False, "game_id": game_id, "detail": "sin sesión"}
            raise


class RemoteGame:
    """Una partida del servidor real, con la interfaz de `GameSimulator`."""

    def __init__(self, client: GridLinkClient, game_id: int):
        self.client = client
        self.game_id = game_id

    def start(self) -> dict:
        return self.client.start_game(self.game_id)

    def get_status(self) -> dict:
        return self.client.game_status(self.game_id)

    def move(self, board_str: str) -> dict:
        return self.client.send_move(self.game_id, board_str)

    def reset(self) -> dict:
        return self.client.reset_game(self.game_id)
