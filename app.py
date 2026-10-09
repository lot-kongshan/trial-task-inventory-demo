"""库存预占原型: 初始化库存 / 原子预占 / 超时自动释放 / 确认支付 / 流水查询. 单进程内存实现."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

DEFAULT_TTL_S = 900


@dataclass
class Reservation:
    id: str
    sku: str
    qty: int
    expires_at: float
    status: str = "held"  # held / confirmed / released


@dataclass
class Store:
    available: dict[str, int] = field(default_factory=dict)
    reservations: dict[str, Reservation] = field(default_factory=dict)
    movements: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    clock: callable = time.time

    def _log(self, sku: str, kind: str, delta: int, ref: str = "") -> None:
        self.movements.append({"at": self.clock(), "sku": sku, "kind": kind, "delta": delta, "ref": ref})

    def _release_expired(self) -> None:
        now = self.clock()
        for r in self.reservations.values():
            if r.status == "held" and r.expires_at <= now:
                r.status = "released"
                self.available[r.sku] += r.qty
                self._log(r.sku, "release_timeout", r.qty, r.id)

    def init(self, sku: str, qty: int) -> int:
        with self.lock:
            self.available[sku] = qty
            self._log(sku, "init", qty)
            return qty

    def reserve(self, sku: str, qty: int, ttl_s: int) -> Reservation:
        with self.lock:
            self._release_expired()
            if sku not in self.available:
                raise KeyError(sku)
            if self.available[sku] < qty:
                raise ValueError("insufficient")
            self.available[sku] -= qty
            r = Reservation(uuid.uuid4().hex, sku, qty, self.clock() + ttl_s)
            self.reservations[r.id] = r
            self._log(sku, "reserve", -qty, r.id)
            return r

    def confirm(self, rid: str) -> Reservation:
        with self.lock:
            self._release_expired()
            r = self.reservations.get(rid)
            if r is None:
                raise KeyError(rid)
            if r.status != "held":
                raise ValueError(r.status)
            r.status = "confirmed"
            self._log(r.sku, "confirm", 0, r.id)
            return r

    def history(self, sku: str) -> list[dict]:
        with self.lock:
            self._release_expired()
            return sorted((m for m in self.movements if m["sku"] == sku), key=lambda m: m["at"], reverse=True)


store = Store()
app = FastAPI(title="inventory-reservation")


class InitIn(BaseModel):
    qty: int = Field(ge=0)


class ReserveIn(BaseModel):
    sku: str
    qty: int = Field(gt=0)
    ttl_s: int = Field(default=DEFAULT_TTL_S, gt=0, le=86400)


@app.post("/stock/{sku}")
def init_stock(sku: str, body: InitIn) -> dict:
    return {"sku": sku, "available": store.init(sku, body.qty)}


@app.get("/stock/{sku}")
def get_stock(sku: str) -> dict:
    with store.lock:
        store._release_expired()
        if sku not in store.available:
            raise HTTPException(404, "sku not found")
        return {"sku": sku, "available": store.available[sku]}


@app.post("/reservations", status_code=201)
def reserve(body: ReserveIn) -> dict:
    try:
        r = store.reserve(body.sku, body.qty, body.ttl_s)
    except KeyError:
        raise HTTPException(404, "sku not found") from None
    except ValueError:
        raise HTTPException(409, "insufficient stock") from None
    return {"id": r.id, "expires_at": r.expires_at}


@app.post("/reservations/{rid}/confirm")
def confirm(rid: str) -> dict:
    try:
        r = store.confirm(rid)
    except KeyError:
        raise HTTPException(404, "reservation not found") from None
    except ValueError as exc:
        raise HTTPException(409, f"reservation is {exc}") from None
    return {"id": r.id, "status": r.status}


@app.get("/stock/{sku}/movements")
def movements(sku: str) -> list[dict]:
    return store.history(sku)
