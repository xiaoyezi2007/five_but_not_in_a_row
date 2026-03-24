from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


@dataclass
class PlayerConn:
    player_id: str
    name: str
    color: str
    ws: WebSocket


@dataclass
class Room:
    room_id: str
    players: Dict[str, PlayerConn] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # Lobby ownership / start settings
    host_id: Optional[str] = None  # first player who joined; can change if host disconnects
    host_claimed_explicit: bool = False
    turn_mode: str = "random"  # "host" | "client" | "random"

    # Game state
    phase: str = "lobby"  # lobby -> playing -> finished
    ready: Set[str] = field(default_factory=set)
    shapes: Dict[str, List[Tuple[int, int]]] = field(default_factory=dict)  # player_id -> 5 coords in 5x5
    colors: Dict[str, str] = field(default_factory=dict)  # player_id -> hex color
    turn: Optional[str] = None
    winner: Optional[str] = None
    win_cells: Optional[List[Tuple[int, int]]] = None
    board_size: int = 15
    board: List[List[Optional[str]]] = field(default_factory=list)  # [y][x] -> player_id

    def ensure_board(self) -> None:
        if not self.board:
            self.board = [[None for _ in range(self.board_size)] for _ in range(self.board_size)]


rooms: Dict[str, Room] = {}
rooms_lock = asyncio.Lock()

PROTOCOL_VERSION = 2

app = FastAPI(title="wuziqi-backend")

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
INDEX_FILE = STATIC_DIR / "index.html"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/version")
async def version() -> Dict[str, Any]:
    return {"protocolVersion": PROTOCOL_VERSION}


@app.get("/")
async def index() -> FileResponse:
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE)
    return FileResponse(Path(__file__).resolve().parents[1] / "static_missing.html")


async def get_or_create_room(room_id: str) -> Room:
    async with rooms_lock:
        if room_id not in rooms:
            rooms[room_id] = Room(room_id=room_id)
        return rooms[room_id]


async def safe_send_json(ws: WebSocket, message: Dict[str, Any]) -> None:
    await ws.send_text(json.dumps(message, ensure_ascii=False))


async def broadcast(room: Room, message: Dict[str, Any], *, exclude_player_id: Optional[str] = None) -> None:
    dead: list[str] = []
    for pid, player in room.players.items():
        if exclude_player_id is not None and pid == exclude_player_id:
            continue
        try:
            await safe_send_json(player.ws, message)
        except Exception:
            dead.append(pid)

    for pid in dead:
        room.players.pop(pid, None)


def _normalize_shape(points: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    min_x = min(x for x, _ in points)
    min_y = min(y for _, y in points)
    norm = sorted([(x - min_x, y - min_y) for x, y in points])
    return norm


def _rotate_point(x: int, y: int, n: int) -> Tuple[int, int]:
    # Rotate within 5x5 around origin (0..4). n in {0,1,2,3} => 0/90/180/270
    if n == 0:
        return (x, y)
    if n == 1:
        return (4 - y, x)
    if n == 2:
        return (4 - x, 4 - y)
    return (y, 4 - x)


def shape_variants(points: List[Tuple[int, int]]) -> List[List[Tuple[int, int]]]:
    variants: List[List[Tuple[int, int]]] = []
    seen: Set[Tuple[Tuple[int, int], ...]] = set()
    for r in range(4):
        rotated = [_rotate_point(x, y, r) for x, y in points]
        norm = _normalize_shape(rotated)
        key = tuple(norm)
        if key not in seen:
            seen.add(key)
            variants.append(norm)
    return variants


def check_win(room: Room, player_id: str) -> bool:
    room.ensure_board()
    points = room.shapes.get(player_id)
    if not points or len(points) != 5:
        return False
    variants = shape_variants(points)
    size = room.board_size

    for variant in variants:
        max_dx = max(dx for dx, _ in variant)
        max_dy = max(dy for _, dy in variant)
        for y0 in range(0, size - max_dy):
            for x0 in range(0, size - max_dx):
                ok = True
                for dx, dy in variant:
                    if room.board[y0 + dy][x0 + dx] != player_id:
                        ok = False
                        break
                if ok:
                    room.win_cells = [(x0 + dx, y0 + dy) for dx, dy in variant]
                    return True

    room.win_cells = None
    return False


def _is_hex_color(value: str) -> bool:
    if not isinstance(value, str):
        return False
    if len(value) != 7 or not value.startswith("#"):
        return False
    try:
        int(value[1:], 16)
        return True
    except Exception:
        return False


@app.websocket("/ws/{room_id}")
async def ws_room(websocket: WebSocket, room_id: str) -> None:
    await websocket.accept()

    room = await get_or_create_room(room_id)
    player_id: Optional[str] = None

    try:
        raw = await websocket.receive_text()
        hello = json.loads(raw)
        if hello.get("type") != "hello":
            await safe_send_json(websocket, {"type": "error", "message": "first message must be hello"})
            await websocket.close(code=1008)
            return

        player_id = str(hello.get("playerId") or "")
        name = str(hello.get("name") or "")
        color = str(hello.get("color") or "")
        is_host = bool(hello.get("isHost") or False)
        if not player_id or not name:
            await safe_send_json(websocket, {"type": "error", "message": "playerId and name are required"})
            await websocket.close(code=1008)
            return

        if color and not _is_hex_color(color):
            await safe_send_json(websocket, {"type": "error", "message": "color must be hex like #RRGGBB"})
            await websocket.close(code=1008)
            return

        async with room.lock:
            if player_id in room.players:
                await safe_send_json(websocket, {"type": "error", "message": "playerId already connected"})
                await websocket.close(code=1008)
                return
            if len(room.players) >= 2:
                await safe_send_json(websocket, {"type": "error", "message": "room is full (2 players)"})
                await websocket.close(code=1008)
                return

            # Color selection: must be unique among connected players.
            if not color:
                color = "#111111"
            if any(c == color for pid, c in room.colors.items() if pid != player_id):
                await safe_send_json(websocket, {"type": "error", "message": "color already taken by opponent"})
                await websocket.close(code=1008)
                return

            room.players[player_id] = PlayerConn(player_id=player_id, name=name, color=color, ws=websocket)
            room.colors[player_id] = color
            room.ensure_board()

            # Host selection:
            # - If someone explicitly claims host (isHost=true), allow it to override an implicit host.
            # - If host was already explicitly claimed by someone else, reject.
            if is_host:
                if room.host_id is not None and room.host_claimed_explicit and room.host_id != player_id:
                    await safe_send_json(websocket, {"type": "error", "message": "host already claimed"})
                    await websocket.close(code=1008)
                    return
                room.host_id = player_id
                room.host_claimed_explicit = True
            else:
                if room.host_id is None:
                    room.host_id = player_id
                    room.host_claimed_explicit = False

            if room.turn is None and len(room.players) == 1:
                room.turn = player_id
            elif room.turn is None and len(room.players) == 2:
                # deterministic: first connected keeps the turn
                room.turn = next(iter(room.players.keys()))

        await safe_send_json(
            websocket,
            {
                "type": "welcome",
                "roomId": room_id,
                "playerId": player_id,
                "protocolVersion": PROTOCOL_VERSION,
                "phase": room.phase,
                "boardSize": room.board_size,
                "turn": room.turn,
                "hostId": room.host_id,
                "turnMode": room.turn_mode,
                "winner": room.winner,
                "ready": list(room.ready),
                "colors": dict(room.colors),
                "players": [
                    {"playerId": p.player_id, "name": p.name}
                    for p in room.players.values()
                ],
            },
        )

        await broadcast(
            room,
            {
                "type": "player_joined",
                "roomId": room_id,
                "player": {"playerId": player_id, "name": name},
                "colors": dict(room.colors),
                "hostId": room.host_id,
                "turnMode": room.turn_mode,
            },
            exclude_player_id=player_id,
        )

        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "ping":
                await safe_send_json(websocket, {"type": "pong"})
                continue

            if msg_type == "set_turn_mode":
                mode = msg.get("mode")
                if not isinstance(mode, str) or mode not in ("host", "client", "random"):
                    await safe_send_json(websocket, {"type": "error", "message": "mode must be host|client|random"})
                    continue

                async with room.lock:
                    if room.phase != "lobby":
                        await safe_send_json(websocket, {"type": "error", "message": "cannot set turn mode after game started"})
                        continue
                    if room.host_id != player_id:
                        await safe_send_json(websocket, {"type": "error", "message": "only host can set turn mode"})
                        continue

                    room.turn_mode = mode

                    payload = {
                        "type": "turn_mode_update",
                        "roomId": room_id,
                        "protocolVersion": PROTOCOL_VERSION,
                        "hostId": room.host_id,
                        "turnMode": room.turn_mode,
                    }

                await broadcast(room, payload, exclude_player_id=None)
                continue

            if msg_type == "reset_game":
                async with room.lock:
                    if room.phase != "finished":
                        await safe_send_json(websocket, {"type": "error", "message": "can only reset after game finished"})
                        continue

                    room.phase = "lobby"
                    room.ready.clear()
                    room.shapes.clear()
                    room.winner = None
                    room.win_cells = None
                    room.board = []
                    if room.players:
                        room.turn = room.host_id or next(iter(room.players.keys()))
                    else:
                        room.turn = None

                    payload = {
                        "type": "game_reset",
                        "roomId": room_id,
                        "protocolVersion": PROTOCOL_VERSION,
                        "phase": room.phase,
                        "turn": room.turn,
                        "hostId": room.host_id,
                        "turnMode": room.turn_mode,
                        "ready": list(room.ready),
                        "colors": dict(room.colors),
                    }

                await broadcast(room, payload, exclude_player_id=None)
                continue

            if msg_type == "move":
                x = msg.get("x")
                y = msg.get("y")
                if not isinstance(x, int) or not isinstance(y, int):
                    await safe_send_json(websocket, {"type": "error", "message": "move x/y must be int"})
                    continue

                async with room.lock:
                    if room.phase != "playing":
                        await safe_send_json(websocket, {"type": "error", "message": "game not started"})
                        continue
                    if room.winner is not None:
                        await safe_send_json(websocket, {"type": "error", "message": "game already finished"})
                        continue
                    if room.turn != player_id:
                        await safe_send_json(websocket, {"type": "error", "message": "not your turn"})
                        continue
                    if x < 0 or y < 0 or x >= room.board_size or y >= room.board_size:
                        await safe_send_json(websocket, {"type": "error", "message": "out of bounds"})
                        continue
                    room.ensure_board()
                    if room.board[y][x] is not None:
                        await safe_send_json(websocket, {"type": "error", "message": "cell occupied"})
                        continue

                    room.board[y][x] = player_id

                    won = check_win(room, player_id)
                    if won:
                        room.phase = "finished"
                        room.winner = player_id
                    else:
                        other_ids = [pid for pid in room.players.keys() if pid != player_id]
                        room.turn = other_ids[0] if other_ids else player_id

                    payload: Dict[str, Any] = {
                        "type": "move_applied",
                        "roomId": room_id,
                        "protocolVersion": PROTOCOL_VERSION,
                        "playerId": player_id,
                        "x": x,
                        "y": y,
                        "phase": room.phase,
                        "turn": room.turn,
                        "winner": room.winner,
                        "winCells": [
                            {"x": cx, "y": cy} for (cx, cy) in (room.win_cells or [])
                        ],
                    }

                await broadcast(room, payload, exclude_player_id=None)
                continue

            if msg_type == "set_shape":
                pts_raw = msg.get("points")
                if not isinstance(pts_raw, list):
                    await safe_send_json(websocket, {"type": "error", "message": "points must be list"})
                    continue

                points: List[Tuple[int, int]] = []
                for p in pts_raw:
                    if not isinstance(p, dict):
                        continue
                    x = p.get("x")
                    y = p.get("y")
                    if isinstance(x, int) and isinstance(y, int):
                        points.append((x, y))

                points = list(dict.fromkeys(points))
                if len(points) != 5:
                    await safe_send_json(websocket, {"type": "error", "message": "shape must have exactly 5 unique points"})
                    continue
                if any(x < 0 or y < 0 or x > 4 or y > 4 for x, y in points):
                    await safe_send_json(websocket, {"type": "error", "message": "shape points must be within 5x5 (0..4)"})
                    continue

                payload_start: Optional[Dict[str, Any]] = None
                async with room.lock:
                    if room.phase != "lobby":
                        await safe_send_json(websocket, {"type": "error", "message": "cannot set shape after game started"})
                        continue

                    room.shapes[player_id] = points
                    room.ready.add(player_id)

                    payload_ready = {
                        "type": "player_ready",
                        "roomId": room_id,
                        "protocolVersion": PROTOCOL_VERSION,
                        "playerId": player_id,
                        "ready": list(room.ready),
                    }

                    both_ready = len(room.players) == 2 and all(pid in room.ready for pid in room.players.keys())
                    if both_ready:
                        # Decide who goes first based on host selection.
                        host_id = room.host_id if room.host_id in room.players else next(iter(room.players.keys()))
                        other_ids = [pid for pid in room.players.keys() if pid != host_id]
                        client_id = other_ids[0] if other_ids else host_id
                        if room.turn_mode == "host":
                            room.turn = host_id
                        elif room.turn_mode == "client":
                            room.turn = client_id
                        else:
                            room.turn = secrets.choice([host_id, client_id])

                        room.phase = "playing"
                        room.winner = None
                        room.win_cells = None
                        # Start turn stays as room.turn.
                        payload_start = {
                            "type": "game_start",
                            "roomId": room_id,
                            "protocolVersion": PROTOCOL_VERSION,
                            "phase": room.phase,
                            "turn": room.turn,
                            "hostId": room.host_id,
                            "turnMode": room.turn_mode,
                            "boardSize": room.board_size,
                            "colors": dict(room.colors),
                            "shapes": {
                                pid: [{"x": x, "y": y} for x, y in room.shapes.get(pid, [])]
                                for pid in room.players.keys()
                            },
                        }

                await broadcast(room, payload_ready, exclude_player_id=None)
                if payload_start is not None:
                    await broadcast(room, payload_start, exclude_player_id=None)
                continue

            if msg_type == "set_color":
                color = msg.get("color")
                if not isinstance(color, str) or not _is_hex_color(color):
                    await safe_send_json(websocket, {"type": "error", "message": "color must be hex like #RRGGBB"})
                    continue

                async with room.lock:
                    if room.phase != "lobby":
                        await safe_send_json(websocket, {"type": "error", "message": "cannot set color after game started"})
                        continue
                    if any(c == color for pid, c in room.colors.items() if pid != player_id):
                        await safe_send_json(websocket, {"type": "error", "message": "color already taken by opponent"})
                        continue
                    room.colors[player_id] = color
                    if player_id in room.players:
                        room.players[player_id].color = color

                    payload = {
                        "type": "colors_update",
                        "roomId": room_id,
                        "protocolVersion": PROTOCOL_VERSION,
                        "colors": dict(room.colors),
                    }

                await broadcast(room, payload, exclude_player_id=None)
                continue

            if msg_type == "chat":
                payload = {
                    "type": "chat",
                    "roomId": room_id,
                    "from": player_id,
                    "text": str(msg.get("text") or ""),
                }
                await broadcast(room, payload, exclude_player_id=None)
                continue

            await safe_send_json(
                websocket,
                {
                    "type": "error",
                    "message": f"unknown message type: {msg_type} (server protocolVersion={PROTOCOL_VERSION})",
                },
            )

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await safe_send_json(websocket, {"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if player_id is not None:
            async with room.lock:
                player = room.players.pop(player_id, None)

                if player_id in room.ready:
                    room.ready.discard(player_id)
                room.shapes.pop(player_id, None)
                room.colors.pop(player_id, None)

                # If someone leaves, reset game state to lobby.
                room.phase = "lobby"
                room.winner = None
                room.win_cells = None
                room.board = []
                if room.host_id == player_id:
                    room.host_id = next(iter(room.players.keys()), None)
                    room.host_claimed_explicit = False
                if room.players:
                    room.turn = room.host_id or next(iter(room.players.keys()))
                else:
                    room.turn = None
                    room.host_id = None
                    room.host_claimed_explicit = False

            if player is not None:
                await broadcast(
                    room,
                    {
                        "type": "player_left",
                        "roomId": room_id,
                        "playerId": player_id,
                        "protocolVersion": PROTOCOL_VERSION,
                        "hostId": room.host_id,
                        "turnMode": room.turn_mode,
                        "turn": room.turn,
                    },
                    exclude_player_id=player_id,
                )

            async with rooms_lock:
                if room_id in rooms and not rooms[room_id].players:
                    rooms.pop(room_id, None)


# Mount static frontend LAST so it doesn't shadow /ws routes.
if INDEX_FILE.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
