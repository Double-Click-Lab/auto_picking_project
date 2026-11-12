import asyncio
import fcntl
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
from collections import deque
from datetime import datetime, timezone
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from typing import Annotated


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Item(StrictModel):
    item_id: str = Field(min_length=1, max_length=100)
    quantity: int = Field(strict=True, ge=1)


class Order(StrictModel):
    request_id: str = Field(min_length=1, max_length=100)
    customer_id: str = Field(min_length=1, max_length=100)
    items: list[Item] = Field(min_length=1)


class Batch(StrictModel):
    orders: list[Order] = Field(min_length=1)


class AppInventorBatch(StrictModel):
    request_id: str = Field(min_length=1, max_length=100)
    orders: list[tuple[
        Annotated[str, Field(min_length=1, max_length=100)],
        dict[str, Annotated[StrictInt, Field(ge=0)]],
        dict[str, Annotated[StrictInt, Field(ge=0)]],
        dict[str, Annotated[StrictInt, Field(ge=0)]],
        dict[str, Annotated[StrictInt, Field(ge=0)]],
    ]] = Field(min_length=1)

    @model_validator(mode="after")
    def check_products(self):
        for row in self.orders:
            for product, entry in zip("ABCD", row[1:]):
                if set(entry) != {product}:
                    raise ValueError(f"상품은 A, B, C, D 순서의 단일 키 딕셔너리여야 합니다: {product}")
            if not any(entry[product] for product, entry in zip("ABCD", row[1:])):
                raise ValueError("주문자마다 하나 이상의 상품 수량이 필요합니다")
        return self


class Bin(StrictModel):
    name: str = Field(min_length=1)
    bin: str = Field(min_length=1)
    position_mm: float = Field(ge=0, allow_inf_nan=False)


class Configuration(StrictModel):
    motor_length_mm: float = Field(gt=0, allow_inf_nan=False)
    items: dict[str, Bin] = Field(min_length=1)

    def check_positions(self):
        if any(not key or item.position_mm > self.motor_length_mm
               for key, item in self.items.items()):
            raise ValueError("품목 ID와 모터 길이 내의 보관함 위치를 확인하세요")
        return self


class Result(StrictModel):
    success: bool
    detail: str = Field(default="", max_length=500)



STATUS_TEXT = {"waiting": "작업 시작 대기", "queued": "접수 대기", "running": "출고 처리 중", "waiting_box": "상자를 치우고 새 상자를 놓아 주세요", "completed": "출고 완료", "failed": "처리 실패"}


def progress_events(order, simulation):
    events = []
    names = order["configuration"]["items"]
    for cmd in order["commands"]:
        payload = json.loads(cmd["payload"])
        result = json.loads(cmd["result"]) if cmd["result"] else None
        mock = (result is not None and result.get("detail") == "simulation") or (result is None and simulation)
        prefix = "[모의 실행] " if mock else ""
        item_id = payload["item_id"]
        name = names.get(item_id, {}).get("name", item_id)
        label = f"{name} ({item_id})"
        device = {"motor": "리니어 모터", "camera": "카메라", "robot": "로봇팔"}[cmd["device"]]
        events.append({"id": cmd["id"] + ":prepared", "command_id": cmd["id"],
                       "device": cmd["device"], "kind": "prepared", "simulation": mock,
                       "message": prefix + f"{label}: {device} 명령을 준비했습니다. 장치 수신 대기 중입니다."})
        if cmd.get("dispatched_at") is not None or (mock and result):
            events.append({"id": cmd["id"] + ":sent", "command_id": cmd["id"],
                           "device": cmd["device"], "kind": "sent", "simulation": mock,
                           "message": prefix + f"{label}: {device}에 신호를 주었습니다."})
        if result and result["success"]:
            message = {"motor": "리니어 모터 이동 완료 신호를 받았습니다.",
                       "camera": "카메라로 보관함을 확인했습니다.",
                       "robot": "로봇팔로 물건 1개를 출고상자에 옮겼습니다."}[cmd["device"]]
            events.append({"id": cmd["id"] + ":completed", "command_id": cmd["id"],
                           "device": cmd["device"], "kind": "completed", "simulation": mock,
                           "message": prefix + label + ": " + message})
        elif cmd["status"] == "failed":
            events.append({"id": cmd["id"] + ":failed", "command_id": cmd["id"],
                           "device": cmd["device"], "kind": "failed", "simulation": mock,
                           "message": prefix + f"{label}: {device} 작업이 실패 또는 중단되었습니다."})
    if order["status"] == "completed":
        mock = bool(events) and all(e["simulation"] for e in events)
        events.append({"id": order["id"] + ":completed", "kind": "order_completed", "simulation": mock,
                       "message": ("[모의 실행] " if mock else "") + f'{order["customer_id"]} 주문자의 물품을 출고시켰습니다.'})
    elif order["status"] == "waiting_box":
        events.append({'id': order['id'] + ':waiting_box', 'kind': 'box_replacement_required',
                       'message': '물품 이송이 끝났습니다. 상자를 치우고 새 상자를 놓은 뒤 준비 완료를 눌러 주세요.'})
    elif order["status"] == "failed":
        events.append({"id": order["id"] + ":failed", "kind": "order_failed",
                       "message": "주문 처리를 중단했습니다: " + (order["error"] or "원인 확인 필요")})
    return {"status_text": STATUS_TEXT[order["status"]], "events": events}

def create_app(db_path=None, simulation=None):
    db_path = str(db_path or os.getenv("DB_PATH", "warehouse.sqlite3"))
    simulation = (os.getenv("SIMULATION", "true").lower() == "true"
                  if simulation is None else simulation)
    api_key = os.getenv("API_KEY", "")
    device_key = os.getenv("DEVICE_KEY", "")
    clear_orders_on_start = os.getenv("CLEAR_ORDERS_ON_START", "false").lower() == "true"
    require_box_confirmation = os.getenv('REQUIRE_BOX_CONFIRMATION', 'true').lower() == 'true'
    monitor_events = deque(maxlen=2000)
    monitor_instance = str(uuid4())
    monitor_sequence = 0

    def emit(kind, message, **data):
        nonlocal monitor_sequence
        monitor_sequence += 1
        monitor_events.append({'id': monitor_sequence, 'time': datetime.now(timezone.utc).isoformat(),
                               'kind': kind, 'message': message, **data})
    timeout = float(os.getenv("COMMAND_TIMEOUT", "60"))
    if timeout <= 0:
        raise ValueError("COMMAND_TIMEOUT must be positive")
    initial_config = Configuration(
        motor_length_mm=float(os.getenv("MOTOR_LENGTH_MM", "1000")),
        items=json.loads(Path(os.getenv("CATALOG_PATH", "catalog.json")).read_text()),
    ).check_positions()

    def configuration(db):
        return Configuration.model_validate_json(
            db.execute("SELECT body FROM configuration").fetchone()[0])
    if not simulation and (not api_key or not device_key):
        raise ValueError("Real mode requires API_KEY and DEVICE_KEY")

    def connect():
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        return db

    def auth(expected, actual):
        if expected and not secrets.compare_digest(expected, actual or ""):
            raise HTTPException(401, "Invalid API key")

    async def user_auth(x_api_key: str = Header(default="")):
        auth(api_key, x_api_key)

    async def device_auth(x_device_key: str = Header(default="")):
        auth(device_key, x_device_key)

    def fault(db, detail):
        db.execute("UPDATE system SET fault=?", (detail,))
        db.execute("UPDATE orders SET status='failed', error=? WHERE status='running'", (detail,))
        db.execute("UPDATE commands SET status='failed' WHERE status='pending'")

    def tick():
        with connect() as db:
            if db.execute("SELECT fault FROM system").fetchone()[0]:
                return
            if db.execute("SELECT 1 FROM orders WHERE status='waiting_box'").fetchone():
                return
            command = db.execute("SELECT * FROM commands WHERE status='pending'").fetchone()
            if command:
                if simulation:
                    db.execute("UPDATE commands SET status='completed' WHERE id=?", (command["id"],))
                elif time.time() - command["created"] > timeout:
                    fault(db, "장치 응답 시간 초과: " + command["id"])
                return
            order = db.execute("SELECT * FROM orders WHERE status='running'").fetchone()
            if order is None:
                order = db.execute("SELECT * FROM orders WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
                if order is None:
                    return
                db.execute("UPDATE orders SET status='running' WHERE id=?", (order["id"],))
                emit('order_started', '주문 처리 시작', order_id=order['id'], customer_id=order['customer_id'])
            items = json.loads(order["items"])
            index, picked, stage = order["item_index"], order["picked"], order["stage"]
            if index >= len(items):
                db.execute("UPDATE orders SET status=? WHERE id=?",
                           ('waiting_box' if require_box_confirmation else 'completed', order['id']))
                emit('waiting_box' if require_box_confirmation else 'order_completed',
                     '상품을 모두 담았습니다. 상자를 치우고 새 상자를 놓아 주세요. 준비 완료 신호 대기 중입니다.'
                     if require_box_confirmation else '주문 출고 완료',
                     order_id=order['id'], customer_id=order['customer_id'])
                return
            item = items[index]
            device, action = [("motor", "move_to_bin"), ("camera", "align_bin"), ("robot", "pick_and_place")][stage]
            config = Configuration.model_validate_json(order["configuration"])
            location = config.items[item["item_id"]]
            payload = {"item_id": item["item_id"], "bin": location.bin,
                       "position_mm": location.position_mm,
                       "motor_length_mm": config.motor_length_mm,
                       "customer_id": order["customer_id"], "order_id": order["id"],
                       "quantity": 1, "destination": "shipping_box",
                       "destination_id": order["id"]}
            command_id = str(uuid4())
            db.execute("INSERT INTO commands (id, order_id, device, action, payload, status, created, result) "
                       "VALUES (?, ?, ?, ?, ?, 'pending', ?, NULL)",
                       (command_id, order["id"], device, action, json.dumps(payload), time.time()))
            emit('command_prepared', '장치 명령 준비 (장치 수신 전)', command_id=command_id,
                 order_id=order['id'], device=device, action=action, payload=payload, simulation=simulation)

    async def worker():
        while True:
            # All DB work runs without await, so API handlers cannot interleave a transaction.
            try:
                tick()
                if simulation:
                    with connect() as db:
                        done = db.execute("SELECT id FROM commands WHERE status='completed' AND result IS NULL").fetchall()
                        for row in done:
                            advance(db, row["id"], Result(success=True, detail="simulation"))
            except Exception:
                logging.exception("Warehouse worker stopped")
                with connect() as db:
                    fault(db, "서버 작업자 오류: 운영자 확인 필요")
                return
            await asyncio.sleep(0.1)

    def advance(db, command_id, result):
        cmd = db.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
        encoded = result.model_dump_json()
        if cmd["result"] is not None:
            if cmd["result"] != encoded:
                raise HTTPException(409, "Conflicting completion result")
            return
        if cmd["status"] == "failed":
            raise HTTPException(409, "Command no longer active")
        db.execute("UPDATE commands SET status=?, result=?, completed_at=? WHERE id=?",
                   ("completed" if result.success else "failed", encoded, time.time(), command_id))
        emit('command_completed', '장치 완료 신호 수신' if result.success else '장치 실패 신호 수신',
             command_id=command_id, order_id=cmd['order_id'], device=cmd['device'],
             action=cmd['action'], success=result.success, detail=result.detail, simulation=simulation)
        if not result.success:
            fault(db, result.detail or "장치 작업 실패")
            return
        order = db.execute("SELECT * FROM orders WHERE id=?", (cmd["order_id"],)).fetchone()
        stage, index, picked = order["stage"], order["item_index"], order["picked"]
        if stage < 2:
            stage += 1
        else:
            picked += 1
            if picked == json.loads(order["items"])[index]["quantity"]:
                index, picked, stage = index + 1, 0, 0
        db.execute("UPDATE orders SET stage=?, item_index=?, picked=? WHERE id=?",
                   (stage, index, picked, order["id"]))

    @asynccontextmanager
    async def lifespan(app):
        lock = open(db_path + ".lock", "a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with connect() as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS orders (
                        id TEXT PRIMARY KEY, request_id TEXT UNIQUE, items TEXT,
                        status TEXT, item_index INTEGER DEFAULT 0, picked INTEGER DEFAULT 0,
                        stage INTEGER DEFAULT 0, error TEXT);
                    CREATE TABLE IF NOT EXISTS commands (
                        id TEXT PRIMARY KEY, order_id TEXT, device TEXT, action TEXT,
                        payload TEXT, status TEXT, created REAL, result TEXT);
                    CREATE TABLE IF NOT EXISTS system (fault TEXT);
                    CREATE TABLE IF NOT EXISTS app_inventor_requests (id TEXT PRIMARY KEY, digest TEXT NOT NULL);
                    INSERT INTO system SELECT NULL WHERE NOT EXISTS (SELECT 1 FROM system);
                """)
                command_columns = {row["name"] for row in db.execute("PRAGMA table_info(commands)")}
                for column in ("dispatched_at", "completed_at"):
                    if column not in command_columns:
                        db.execute(f"ALTER TABLE commands ADD COLUMN {column} REAL")
                columns = {row["name"] for row in db.execute("PRAGMA table_info(orders)")}
                if "customer_id" not in columns:
                    db.execute("ALTER TABLE orders ADD COLUMN customer_id TEXT DEFAULT 'legacy'")
                if "configuration" not in columns:
                    db.execute("ALTER TABLE orders ADD COLUMN configuration TEXT")
                if 'box_ready_at' not in columns:
                    db.execute('ALTER TABLE orders ADD COLUMN box_ready_at REAL')
                db.execute("CREATE TABLE IF NOT EXISTS configuration (body TEXT NOT NULL)")
                if not db.execute("SELECT 1 FROM configuration").fetchone():
                    db.execute("INSERT INTO configuration VALUES (?)", (initial_config.model_dump_json(),))
                config = configuration(db).check_positions()
                # Upgrade the old three-product simulation catalog once. Keep
                # calibrated positions and deliberate later catalog edits intact.
                db.execute("CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY)")
                migration = "simulation_four_products_v1"
                if simulation and not db.execute("SELECT 1 FROM migrations WHERE name=?", (migration,)).fetchone():
                    if set(config.items) == {"ITEM-001", "ITEM-002", "ITEM-003"} and "ITEM-004" in initial_config.items:
                        product_d = initial_config.items["ITEM-004"]
                        if product_d.position_mm <= config.motor_length_mm:
                            config.items["ITEM-004"] = product_d
                            db.execute("UPDATE configuration SET body=?", (config.model_dump_json(),))
                            logging.info("모의 실행 품목 설정에 D(ITEM-004)를 추가했습니다")
                        else:
                            logging.warning("D 품목 좌표가 저장된 모터 길이를 벗어납니다. /configuration에서 등록하세요")
                    db.execute("INSERT INTO migrations VALUES (?)", (migration,))
                db.execute("UPDATE orders SET configuration=? WHERE configuration IS NULL",
                           (config.model_dump_json(),))
                if db.execute("SELECT 1 FROM orders WHERE status='running'").fetchone():
                    fault(db, "서버 재시작: 장치 위치 및 실제 출고 수량을 확인하세요")
                if clear_orders_on_start:
                    db.execute("DELETE FROM commands")
                    db.execute("DELETE FROM orders")
                    db.execute("DELETE FROM app_inventor_requests")
                    if simulation:
                        db.execute("UPDATE system SET fault=NULL")
                    logging.warning("서버 시작: 이전 주문·명령·요청 중복 확인 기록을 삭제했습니다")
            task = asyncio.create_task(worker())
            app.state.worker = task
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        finally:
            lock.close()

    app = FastAPI(title="라즈베리파이 출고 서버", lifespan=lifespan)
    recent_requests = deque(maxlen=200)
    known_peers = set()

    class AccessHistoryMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope['type'] != 'http' or scope['path'] in ('/connections', '/monitor/events'):
                return await self.app(scope, receive, send)
            started = time.monotonic()
            status = 500

            async def capture(message):
                nonlocal status
                if message['type'] == 'http.response.start':
                    status = message['status']
                await send(message)

            try:
                await self.app(scope, receive, capture)
            finally:
                peer = scope.get('client')
                ip = peer[0] if peer else 'unknown'
                if ip not in known_peers:
                    known_peers.add(ip)
                    emit('client_seen', '접속 기기의 첫 HTTP 요청 수신 (현재 연결 유지 여부를 뜻하지 않음)', ip=ip)
                if scope['method'] != 'GET' or status >= 400:
                    emit('http_request', 'HTTP 요청 처리', ip=ip, method=scope['method'],
                         path=scope['path'], status=status)
                recent_requests.append({
                    'time': datetime.now(timezone.utc).isoformat(),
                    'ip': peer[0] if peer else 'unknown',
                    'method': scope['method'], 'path': scope['path'],
                    'status': status,
                    'duration_ms': round((time.monotonic() - started) * 1000),
                })

    app.add_middleware(AccessHistoryMiddleware)

    @app.get('/connections', dependencies=[Depends(user_auth)])
    async def connections():
        return {'requests': list(reversed(recent_requests)), 'limit': 200}

    @app.get('/monitor/events', dependencies=[Depends(user_auth)])
    async def monitor(after: int = Query(default=0, ge=0)):
        with connect() as db:
            return {'instance': monitor_instance, 'cursor': monitor_sequence,
                    'oldest_id': monitor_events[0]['id'] if monitor_events else monitor_sequence + 1,
                    'events': [event for event in monitor_events if event['id'] > after],
                    'mode': 'simulation' if simulation else 'real',
                    'fault': db.execute('SELECT fault FROM system').fetchone()[0],
                    'worker_alive': not app.state.worker.done(),
                    'orders': [dict(row) for row in db.execute(
                        "SELECT id AS order_id, customer_id, status FROM orders "
                        "WHERE status IN ('running', 'waiting_box') ORDER BY rowid")]}

    @app.get("/health")
    async def health():
        with connect() as db:
            return {"mode": "simulation" if simulation else "real",
                    "fault": db.execute("SELECT fault FROM system").fetchone()[0],
                    "worker_alive": not app.state.worker.done()}

    @app.get("/items", dependencies=[Depends(user_auth)])
    async def items():
        with connect() as db:
            return {key: value.model_dump() for key, value in configuration(db).items.items()}

    @app.get("/configuration", dependencies=[Depends(user_auth)])
    async def get_configuration():
        with connect() as db:
            return configuration(db)

    @app.put("/configuration", dependencies=[Depends(user_auth)])
    async def set_configuration(config: Configuration):
        try:
            config.check_positions()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with connect() as db:
            if db.execute("SELECT 1 FROM orders WHERE status IN ('waiting', 'queued', 'running', 'waiting_box')").fetchone():
                raise HTTPException(409, "대기 및 진행 주문을 모두 처리한 뒤 설정을 변경하세요")
            if db.execute("SELECT fault FROM system").fetchone()[0]:
                raise HTTPException(409, "장치 오류를 복구한 뒤 설정을 변경하세요")
            db.execute("UPDATE configuration SET body=?", (config.model_dump_json(),))
        return config

    def enqueue(orders, batch_identity=None, status="queued"):
        results = []
        accepted = []
        with connect() as db:
            if batch_identity:
                request_id, digest = batch_identity
                previous = db.execute("SELECT digest FROM app_inventor_requests WHERE id=?", (request_id,)).fetchone()
                if previous and previous[0] != digest:
                    raise HTTPException(409, "request_id already used with a different batch")
                db.execute("INSERT OR IGNORE INTO app_inventor_requests VALUES (?, ?)", batch_identity)
            config = configuration(db)
            for order in orders:
                payload = json.dumps([i.model_dump() for i in order.items], sort_keys=True)
                previous = db.execute("SELECT * FROM orders WHERE request_id=?", (order.request_id,)).fetchone()
                if previous:
                    if previous["items"] != payload or previous["customer_id"] != order.customer_id:
                        raise HTTPException(409, "request_id already used with different customer or items")
                    results.append({"order_id": previous["id"], "customer_id": previous["customer_id"],
                                    "status": previous["status"]})
                    continue
                ids = [item.item_id for item in order.items]
                if len(set(ids)) != len(ids) or any(i not in config.items for i in ids):
                    raise HTTPException(422, "Unknown or duplicate item_id")
                if db.execute("SELECT fault FROM system").fetchone()[0] or app.state.worker.done():
                    raise HTTPException(503, "장치 및 서버 상태 확인이 필요합니다")
                order_id = str(uuid4())
                db.execute("INSERT INTO orders (id, request_id, customer_id, items, status, configuration) "
                           "VALUES (?, ?, ?, ?, ?, ?)",
                           (order_id, order.request_id, order.customer_id, payload, status, config.model_dump_json()))
                results.append({"order_id": order_id, "customer_id": order.customer_id, "status": status})
                accepted.append((order_id, order))
        for order_id, order in accepted:
            emit('order_received', '주문 접수', order_id=order_id,
                 customer_id=order.customer_id, items=[item.model_dump() for item in order.items])
        return results

    @app.post("/orders", status_code=202, dependencies=[Depends(user_auth)])
    async def submit(order: Order):
        return enqueue([order])[0]

    @app.post("/orders/batch", status_code=202, dependencies=[Depends(user_auth)])
    async def submit_batch(batch: Batch):
        # One transaction: invalid entries roll back the entire batch.
        return {"orders": enqueue(batch.orders)}

    @app.post("/orders/app-inventor", status_code=202, dependencies=[Depends(user_auth)])
    async def submit_app_inventor(batch: AppInventorBatch):
        # Include the complete batch in every derived ID: editing any row on retry
        # must conflict, even when a previously submitted batch is shortened.
        digest = hashlib.sha256(batch.model_dump_json().encode()).hexdigest()
        key = hashlib.sha256(batch.request_id.encode()).hexdigest()
        orders = [Order(
            request_id=f"ai:{key}:{index}", customer_id=row[0],
            items=[Item(item_id=f"ITEM-00{number}", quantity=entry[product])
                   for number, (product, entry) in enumerate(zip("ABCD", row[1:]), 1)
                   if entry[product] > 0],
        ) for index, row in enumerate(batch.orders)]
        return {"orders": enqueue(orders, (batch.request_id, digest), status="waiting")}

    @app.get("/orders", dependencies=[Depends(user_auth)])
    async def list_orders(customer_id: str | None = None,
                          limit: int = Query(default=100, ge=1, le=100),
                          offset: int = Query(default=0, ge=0)):
        with connect() as db:
            where = " WHERE customer_id=?" if customer_id is not None else ""
            params = (customer_id,) if customer_id is not None else ()
            rows = db.execute(
                "SELECT id AS order_id, customer_id, status, items FROM orders" + where +
                " ORDER BY rowid DESC LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
            total = db.execute("SELECT COUNT(*) FROM orders" + where, params).fetchone()[0]
            orders = [dict(row) for row in rows]
            for order in orders:
                order["items"] = json.loads(order["items"])
            return {"orders": orders, "total": total, "limit": limit, "offset": offset}

    @app.get("/orders/{order_id}", dependencies=[Depends(user_auth)])
    async def status(order_id: str):
        with connect() as db:
            row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Order not found")
            data = dict(row)
            data["items"] = json.loads(data["items"])
            data["configuration"] = json.loads(data["configuration"])
            data["commands"] = [dict(c) for c in db.execute("SELECT * FROM commands WHERE order_id=? ORDER BY rowid", (order_id,))]
            data["progress"] = progress_events(data, simulation)
            data['box_replacement_required'] = row['status'] == 'waiting_box'
            return data

    @app.post('/orders/{order_id}/box-ready', dependencies=[Depends(user_auth)])
    async def box_ready(order_id: str):
        with connect() as db:
            row = db.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
            if row is None:
                raise HTTPException(404, 'Order not found')
            if row['status'] == 'completed' and row['box_ready_at'] is not None:
                return {'order_id': order_id, 'status': 'completed', 'accepted': True}
            if row['status'] != 'waiting_box':
                raise HTTPException(409, '상자 교체 대기 중인 주문이 아닙니다')
            if db.execute('SELECT fault FROM system').fetchone()[0]:
                raise HTTPException(409, '장치 오류를 먼저 복구하세요')
            db.execute("UPDATE orders SET status='completed', box_ready_at=? WHERE id=?",
                       (time.time(), order_id))
        emit('box_ready', '새 상자 준비 완료 신호 수신 → 다음 주문 진행 허용',
             order_id=order_id, customer_id=row['customer_id'])
        return {'order_id': order_id, 'status': 'completed', 'accepted': True}

    @app.post('/orders/{order_id}/start', status_code=200, dependencies=[Depends(user_auth)])
    async def start_order(order_id: str):
        with connect() as db:
            # Serialize the availability check and state change, including
            # requests made at the same time for different waiting orders.
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM orders WHERE id=?', (order_id,)).fetchone()
            if row is None:
                raise HTTPException(404, 'Order not found')
            if row['status'] in ('queued', 'running'):
                return {'order_id': order_id, 'status': row['status'], 'accepted': True}
            if row['status'] != 'waiting':
                raise HTTPException(409, '작업 시작 대기 중인 주문이 아닙니다')
            if db.execute('SELECT fault FROM system').fetchone()[0] or app.state.worker.done():
                raise HTTPException(503, '장치 및 서버 상태 확인이 필요합니다')
            if db.execute("SELECT 1 FROM orders WHERE status IN ('queued', 'running', 'waiting_box')").fetchone():
                raise HTTPException(409, '진행 중이거나 상자 교체 대기 중인 주문이 있습니다')
            db.execute("UPDATE orders SET status='queued' WHERE id=?", (order_id,))
        return {'order_id': order_id, 'status': 'queued', 'accepted': True}

    @app.get("/devices/{device}/commands/next", dependencies=[Depends(device_auth)])
    async def next_command(device: Literal["motor", "camera", "robot"]):
        if simulation:
            raise HTTPException(409, "Simulation mode has no real device commands")
        with connect() as db:
            row = db.execute("SELECT * FROM commands WHERE device=? AND status='pending' LIMIT 1", (device,)).fetchone()
            if row is None:
                return {"command": None}
            if row["dispatched_at"] is None:
                db.execute("UPDATE commands SET dispatched_at=? WHERE id=?", (time.time(), row["id"]))
                row = db.execute("SELECT * FROM commands WHERE id=?", (row["id"],)).fetchone()
                emit('command_delivered', '장치가 명령을 가져감', command_id=row['id'],
                     order_id=row['order_id'], device=device, action=row['action'])
            data = dict(row)
            data["payload"] = json.loads(data["payload"])
            data['expires_at'] = data['created'] + timeout
            data['expires_in'] = max(0.0, data['expires_at'] - time.time())
            return {"command": data}

    @app.post("/devices/{device}/commands/{command_id}/complete", dependencies=[Depends(device_auth)])
    async def complete(device: Literal["motor", "camera", "robot"], command_id: str, result: Result):
        if simulation:
            raise HTTPException(409, "Simulation mode")
        with connect() as db:
            cmd = db.execute("SELECT * FROM commands WHERE id=? AND device=?", (command_id, device)).fetchone()
            if cmd is None:
                raise HTTPException(404, "Command not found")
            advance(db, command_id, result)
        return {"accepted": True}

    @app.post("/system/reset", dependencies=[Depends(user_auth)])
    async def reset():
        # Operator must first reconcile physical state; failed orders are never retried.
        with connect() as db:
            if db.execute("SELECT 1 FROM orders WHERE status='running'").fetchone():
                raise HTTPException(409, "An order is running")
            db.execute("UPDATE system SET fault=NULL")
        return {"ready": True}

    return app


app = create_app()
