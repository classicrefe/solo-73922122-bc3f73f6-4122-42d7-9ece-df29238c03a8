# 展馆轮椅通道与清洁封路 API

一个单日内存态 FastAPI 服务：录入无台阶通道、等候点，签发限时引导单，处理途中报到，并在封路生效前检查所有在途单是否仍能按时到达。

## 已实现的业务规则

- **通道建模**：通道连接两个点位，包含通行分钟数；支持单向、反向或双向。可在创建点位时标记 `can_wait: true`，也可在创建通道时通过 `wait_point_ids` / `wait_points` 指定等候点。
- **发单校验**：引导单记录起点、出发分钟、终点、到达期限。只有在当前封闭安排下能找到不晚于期限的路线时才发单；否则返回 `409`、不可行路线和原因，且不创建单号。
- **途中报到**：报到记录点位和分钟；之后始终以“最新一条报到”的点位与分钟作为续算起点。报到终点即结单。
- **等候限制**：搜索按整数分钟推进；只有 `can_wait=true` 的点位允许等待。非等候点不能通过“原地等待”绕过封闭。
- **封闭避让**：通行占用半开区间 `[departure_minute, arrival_minute)`，封闭占用 `[start_minute, end_minute)`。到达时封闭刚开始、或出发时封闭刚结束，都不算重叠；区间相交则不可进入该通道。
- **封路原子检查**：提交拟封通道和起止分钟后，以所有未结单引导单及其最新报到（无报到则用起点/出发分钟）逐一试算。若有单无法按时到达，返回 `blocked_order_ids` 等受阻详情和 `409`，**不写入新封闭、不改写现有路线，维持原封闭安排**。
- **生效后改线**：全部可到达时封闭生效，服务会重算并保存各在途单的新路线。查询原引导单可得到分段通道、每段通行时刻、合法等候区间和路线历史。
- **预演接口**：`POST /api/closures/evaluate` 只试算、不写入，始终以 `effective` / `would_apply` 表示是否可生效。

## 用 Docker Compose 启动并演示

```bash
docker compose up --build
```

`api` 服务健康后，`demo` 服务会自动运行端到端演示。另开终端可访问：

- API 根地址：<http://127.0.0.1:8000/>
- 交互式文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

如果只启动 API：

```bash
docker compose up --build api
```

再运行演示：

```bash
docker compose run --rm demo
```

演示会覆盖：

1. 期限过紧时拒绝发单；
2. 正常发单；
3. 途中报到并从最新报到续走；
4. 拟封唯一通道导致 `GD-0001` 受阻，返回单号且封闭不生效；
5. 可行封路生效后，`GD-0002` 改走东连廊，得到新的两段路线和通行时刻；
6. `GD-0003` 只在轮椅等候区等待，封闭结束后通过；
7. 终点报到后结单。

## 本地运行与自测

需要 Python 3.11+。

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt

uvicorn app.main:app --reload --port 8000
python scripts/demo.py --base-url http://127.0.0.1:8000
pytest -q
```

本仓库交付前已在隔离虚拟环境中运行：

```text
12 passed
DEMO PASSED: 发单、报到、受阻回滚、封闭后改线、等候与结单均符合预期。
```

## 主要接口

规范前缀为 `/api`；常用资源也支持不带 `/api` 的根路径别名，例如下列两组等价：

- `POST /api/points` ≡ `POST /points`
- `POST /api/guide-orders` ≡ `POST /guide-sheets`
- `POST /api/closures` ≡ `POST /closures`

### 1. 录入点位

```bash
curl -X POST http://127.0.0.1:8000/api/points \
  -H 'Content-Type: application/json' \
  -d '{"id":"central_hall","name":"中央大厅","can_wait":true}'
```

### 2. 录入无台阶通道

```bash
curl -X POST http://127.0.0.1:8000/api/passages \
  -H 'Content-Type: application/json' \
  -d '{
    "id":"central_north",
    "name":"中央大厅至北展厅直廊",
    "from_point_id":"central_hall",
    "to_point_id":"north_hall",
    "minutes":10,
    "bidirectional":true,
    "wait_point_ids":["central_hall"]
  }'
```

也接受 `corridors`、`code`、`from`、`to`、`travel_minutes`、`duration`、`waitable` 等常见别名字段；分钟可传整数，也可传 `"HH:MM"`。

### 3. 发引导单

```bash
curl -X POST http://127.0.0.1:8000/api/guide-orders \
  -H 'Content-Type: application/json' \
  -d '{
    "start_point_id":"south_gate",
    "departure_minute":0,
    "destination_point_id":"north_hall",
    "deadline_minute":35
  }'
```

成功响应中的 `current_route` 类似：

```json
{
  "found": true,
  "arrival_minute": 18,
  "segments": [
    {
      "passage_id": "south_central",
      "from_point_id": "south_gate",
      "to_point_id": "central_hall",
      "departure_minute": 0,
      "arrival_minute": 8,
      "travel_minutes": 8,
      "wait_before_minutes": 0
    }
  ],
  "waits": []
}
```

### 4. 途中报到

```bash
curl -X POST http://127.0.0.1:8000/api/guide-orders/GD-0002/check-ins \
  -H 'Content-Type: application/json' \
  -d '{"point_id":"central_hall","minute":8,"reporter":"馆员"}'
```

### 5. 提交拟封通道

```bash
curl -X POST http://127.0.0.1:8000/api/closures \
  -H 'Content-Type: application/json' \
  -d '{
    "passage_id":"central_north",
    "start_minute":8,
    "end_minute":30,
    "reason":"清洁"
  }'
```

受阻时响应示例：

```json
{
  "detail": "closure was not applied ...",
  "effective": false,
  "applied": false,
  "blocked_order_ids": ["GD-0001"],
  "blocked_order_nos": ["GD-0001"],
  "original_closures_preserved": true
}
```

### 6. 查询原在途单的新路线

```bash
curl http://127.0.0.1:8000/api/guide-orders/GD-0002
curl http://127.0.0.1:8000/api/guide-orders/GD-0002/route
```

封闭生效后，`current_route.segments` 是新的分段路线，`departure_minute` / `arrival_minute` 是每段通行时刻；`waits` 列出合法等候。`route_history` 保存发单、报到和每次封闭改线的历史快照。

## 演示地图

`POST /api/demo/seed` 会重置并写入 10 个点位、8 条通道：

- 南入口 → 中央大厅 → 北展厅：直廊封闭后可改走东连廊；
- 副楼入口 → 西侧连廊中段 → 珍本展厅：中段不是等候点且无替代路线，用于演示封闭被拒；
- 静区入口 → 轮椅等候区 → 静音展厅：用于演示只能在等候点等待至封闭结束。

## 设计说明与限制

- 数据保存在进程内内存中，适合单日演示和接口自测；容器重启后数据清空。`POST /api/demo/seed` 或 `POST /api/admin/reset` 可重置。
- 服务不引入真实时钟；“当时”由请求中的报到分钟和单据状态表达，所有未结单都会参与封闭检查。
- 分钟按单日 `0..1440` 的整数分钟建模；不支持小数分钟。
- 如果等候点位于一条长通道中间，需要把该通道按位置拆成两条带各自通行分钟数的通道；仅有总通行分钟数无法推断到达中间点的时刻。
- 未实现鉴权、多馆员协作锁、持久化数据库和跨日排班。
- 本交付环境没有可用的 Docker CLI/守护进程，因此未能在该环境实际执行 `docker compose up --build`；已验证 Compose YAML 可解析、Dockerfile 使用标准 `python:3.11-slim` 启动方式，并用等价的本地 Uvicorn 进程跑通了同一演示脚本和 pytest。
