"""FastAPI dashboard — canlı durum + bot kontrolü.

Çalıştırma:
    uvicorn tradebot.web.app:app --host 127.0.0.1 --port 8000
veya:
    python -m tradebot.web.app
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from loguru import logger

from tradebot.config import load_config
from tradebot.engine import Engine

STATIC = Path(__file__).parent / "static"


class ConnectionManager:
    def __init__(self) -> None:
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def broadcast(self, data: dict) -> None:
        dead = []
        msg = json.dumps(data, default=str)
        for ws in self.active:
            try:
                await ws.send_text(msg)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()
app = FastAPI(title="Tradebot Dashboard")
cfg = load_config()
engine = Engine(cfg, broadcast=manager.broadcast)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/state")
async def get_state() -> dict:
    return engine.state.to_dict()


@app.post("/api/start")
async def start() -> dict:
    engine.start()
    return {"ok": True, "running": engine.state.running}


@app.post("/api/stop")
async def stop() -> dict:
    await engine.stop()
    return {"ok": True, "running": engine.state.running}


@app.post("/api/emergency_close")
async def emergency_close() -> dict:
    await engine.emergency_close()
    return {"ok": True, "running": engine.state.running}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        await ws.send_text(json.dumps(engine.state.to_dict(), default=str))
        while True:
            await ws.receive_text()  # istemciden mesaj beklemiyoruz; bağlantıyı tutar
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:  # noqa: BLE001
        manager.disconnect(ws)


def main() -> None:
    import uvicorn

    mode = "TESTNET" if cfg.secrets.use_testnet else "CANLI"
    dry = "DRY-RUN" if cfg.engine.dry_run else "GERÇEK EMİR"
    logger.info(f"Dashboard: http://{cfg.web.host}:{cfg.web.port}  [{mode} / {dry}]")
    uvicorn.run(app, host=cfg.web.host, port=cfg.web.port, log_level="info")


if __name__ == "__main__":
    main()
