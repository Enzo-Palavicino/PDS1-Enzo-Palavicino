# GridLink API - Docs

---

## 1. URL API

**https://gridlink-app-imzah.ondigitalocean.app/Links to an external site.**

---

## 2. Autenticación

Todos los endpoints, excepto `/api/health/`, requieren enviar la API key del jugador en cada request usando el header:

```http
X-API-Key: TU_API_KEY
```

---

## 3. Formato del tablero

El tablero se representa como un string tabular:

- Las celdas se separan con coma: `,`
- Las filas se separan con barra vertical: `|`

### Tipos de celda

- `N`: celda normal
- `L`: bloqueo
- `P`: celda potencial
- `B`: puente libre
- `C1`, `C2`: conectores
- `K1`, `K2`: celda usada por una conexión
- `B:1`: puente usado por la conexión 1
- `B:1+2`: puente usado por las conexiones 1 y 2

---

# 4. Endpoints

## 4.1. Health check

### Request

```http
GET /api/health/
```

Endpoint público para verificar que la API está activa.

### Respuesta

```json
{
  "status": "ok",
  "service": "gridlink"
}
```

---

## 4.2. Listar partidas disponibles

### Request

```http
GET /api/games/
```

Retorna las partidas actualmente disponibles para jugar.

### Filtro opcional por torneo

```http
GET /api/games/?tournament_id=1
```

### Respuesta

```json
[
  {
    "id": 2,
    "mode": "survival",
    "rows": 8,
    "cols": 8,
    "request_count": 4
  }
]
```

---

## 4.3. Listar torneos activos

### Request

```http
GET /api/tournaments/
```

Permite consultar los torneos disponibles para el jugador autenticado.

### Respuesta

```json
[
  {
    "id": 1,
    "name": "Copa GridLink",
    "description": "Torneo activo",
    "available_from": "2026-08-01T00:00:00Z",
    "available_until": "2026-08-31T23:59:59Z"
  }
]
```

---

## 4.4. Iniciar partida

### Request

```http
POST /api/games/<game_id>/start/
```

Crea una sesión de juego para el jugador autenticado. Cada jugador puede tener una sola sesión activa por partida.

### Respuesta

```json
{
  "game_id": 2,
  "board": "C1,N,N|N,L,N|N,N,C2",
  "params": {
    "mode": "survival",
    "rows": 3,
    "cols": 3,
    "connector_count": 2,
    "request_count": 1,
    "blocked_count": 1,
    "bridge_count": 0,
    "potential_count": 0,
    "bridge_probability": 0,
    "request_window_size": 1,
    "fewest_cells_penalty": "1.0",
    "allow_player_resets": true,
    "has_verified_solution": true
  },
  "requests": [
    [1, 2]
  ]
}
```

---

## 4.5. Consultar estado de partida

### Request

```http
GET /api/games/<game_id>/status/
```

Permite recuperar el estado actual de una partida ya iniciada por el jugador. Es útil si el jugador automático pierde su estado local.

### Respuesta

```json
{
  "game_id": 2,
  "status": "in_progress",
  "board": "C1,K1,C2|N,N,N|N,N,N",
  "params": {
    "mode": "survival",
    "rows": 3,
    "cols": 3,
    "request_count": 2,
    "request_window_size": 1
  },
  "requests": [
    [3, 4]
  ],
  "current_turn": 1,
  "completed_requests": 1,
  "removed_cells_total": 0,
  "last_error": "",
  "started_at": "2026-08-06T03:00:00+00:00",
  "ended_at": null,
  "updated_at": "2026-08-06T03:01:00+00:00"
}
```

Si la partida está completada, la respuesta incluye `score`.

---

## 4.6. Enviar jugada

### Request

```http
POST /api/games/<game_id>/moves/
```

Envía el nuevo estado del tablero después de construir una conexión. La API valida que la jugada cumpla las reglas del juego.

### Body

```json
{
  "board": "C1,K1,C2|N,N,N|N,N,N"
}
```

Para los caminos se usa `"K<número_de_ronda>"`

### Respuesta exitosa

```json
{
  "valid": true,
  "completed": false,
  "blocked": false,
  "board": "C1,K1,C2|N,N,N|N,N,N",
  "requests": [
    [3, 4]
  ],
  "current_turn": 1,
  "completed_requests": 1,
  "removed_cells_count": 0,
  "removed_cells_total": 0,
  "status": "in_progress"
}
```

### Respuesta cuando termina la partida

```json
{
  "valid": true,
  "completed": true,
  "blocked": false,
  "board": "C1,K1,C2|N,N,N|N,N,N",
  "requests": [],
  "current_turn": 1,
  "completed_requests": 1,
  "removed_cells_count": 0,
  "removed_cells_total": 0,
  "status": "completed",
  "score": 1
}
```

### Respuesta con jugada inválida

```json
{
  "valid": false,
  "error": "Los conectores no pueden modificarse.",
  "session_status": "failed"
}
```

---

## 4.7. Resetear partida

### Request

```http
POST /api/games/<game_id>/reset/
```

Borra la sesión del jugador para esa partida. Solo funciona si la partida permite reset.

### Respuesta

```json
{
  "reset": true,
  "game_id": 2
}
```

---

# 5. Ejemplo en Python

Ejemplo mínimo para consultar partidas activas:

```python
import requests

BASE_URL = "https://gridlink-app-imzah.ondigitalocean.app/"
API_KEY = "TU_API_KEY"

headers = {
    "X-API-Key": API_KEY,
}

response = requests.get(
    f"{BASE_URL}/api/games/",
    headers=headers,
    timeout=10,
)

response.raise_for_status()

games = response.json()

for game in games:
    print(
        f"Partida {game['id']}: "
        f"{game['mode']} - "
        f"{game['rows']}x{game['cols']} - "
        f"{game['request_count']} solicitudes"
    )
```