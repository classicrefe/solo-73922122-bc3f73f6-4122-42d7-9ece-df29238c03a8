#!/usr/bin/env python3
"""End-to-end API demonstration and smoke check.

The script only uses the Python standard library so the demo service can run
from the same slim application image without extra test dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def request_api(
    method: str, base_url: str, path: str, payload: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any] | list[Any]]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"detail": raw}


def wait_for_api(base_url: str) -> None:
    last_error: Exception | None = None
    for _ in range(60):
        try:
            status, body = request_api("GET", base_url, "/health")
            if status == 200:
                print(f"API ready: {json.dumps(body, ensure_ascii=False)}")
                return
        except Exception as exc:  # noqa: BLE001 - retry during container startup
            last_error = exc
        time.sleep(0.5)
    raise SystemExit(f"API did not become ready at {base_url}: {last_error}")


def show(title: str, status: int, body: Any, keys: list[str] | None = None) -> None:
    print(f"\n--- {title} [HTTP {status}] ---")
    if keys and isinstance(body, dict):
        printable = {key: body.get(key) for key in keys if key in body}
        print(json.dumps(printable, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(body, ensure_ascii=False, indent=2))


def must(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit("DEMO ASSERTION FAILED: " + message)
    print("  ✓ " + message)


def segment_summary(route: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "passage": segment["passage_id"],
            "from": segment["from_point_id"],
            "to": segment["to_point_id"],
            "depart": segment["departure_minute"],
            "arrive": segment["arrival_minute"],
            "wait_before": segment["wait_before_minutes"],
        }
        for segment in route.get("segments", [])
    ]


def run(base_url: str) -> None:
    wait_for_api(base_url)

    status, seed = request_api("POST", base_url, "/api/demo/seed")
    show("重置并写入演示展馆", status, {"status": seed.get("status"), "health": seed.get("health")})
    must(status == 200, "演示数据写入成功")

    print("\n========== 1. 发单：不能按时到达则不发单 ==========")
    status, too_late = request_api(
        "POST",
        base_url,
        "/api/guide-orders",
        {
            "start_point_id": "south_gate",
            "departure_minute": 0,
            "destination_point_id": "north_hall",
            "deadline_minute": 10,
        },
    )
    show("期限过紧，发单被拒绝", status, too_late, ["detail", "feasible", "route"])
    must(status == 409, "预计 18 分钟、期限 10 分钟时不发单")

    status, blocked_order = request_api(
        "POST",
        base_url,
        "/api/guide-orders",
        {
            "start_point_id": "annex_gate",
            "departure_minute": 0,
            "destination_point_id": "rare_book_hall",
            "deadline_minute": 20,
            "tourist_name": "示例轮椅游客 A",
        },
    )
    show("发出 GD-0001", status, blocked_order, ["id", "status", "current_route"])
    must(status == 200 and blocked_order["id"] == "GD-0001", "GD-0001 成功发单")
    must(blocked_order["current_route"]["arrival_minute"] == 13, "初版路线 13 分钟到达")

    print("\n========== 2. 途中报到 ==========")
    order_id = blocked_order["id"]
    status, checked_in = request_api(
        "POST",
        base_url,
        f"/api/guide-orders/{order_id}/check-ins",
        {"point_id": "west_cloister", "minute": 5, "reporter": "馆员"},
    )
    show("GD-0001 在西侧连廊中段 5 分报到", status, checked_in, ["id", "status", "latest_report", "current_route"])
    must(status == 200, "报到成功")
    must(checked_in["current_route"]["start_point_id"] == "west_cloister", "从最近报到点续走")

    print("\n========== 3. 封闭会挡住在途单：返回受阻单号并维持原安排 ==========")
    status, rejected_closure = request_api(
        "POST",
        base_url,
        "/api/closures",
        {
            "passage_id": "west_rare",
            "start_minute": 6,
            "end_minute": 30,
            "reason": "清洁封路演示-应被拒绝",
        },
    )
    show("拟封 west_rare 6-30", status, rejected_closure, ["detail", "blocked_order_ids", "original_closures_preserved"])
    must(status == 409, "受阻时返回 HTTP 409")
    must(rejected_closure["blocked_order_ids"] == ["GD-0001"], "受阻单号为 GD-0001")

    status, closures = request_api("GET", base_url, "/api/closures")
    must(status == 200 and closures == [], "被拒封闭未生效，原封闭安排（空）保持不变")

    status, completed_a = request_api(
        "POST",
        base_url,
        f"/api/guide-orders/GD-0001/check-ins",
        {"point_id": "rare_book_hall", "minute": 13},
    )
    must(status == 200 and completed_a["status"] == "completed", "原路线仍可在 13 分到达并结单")

    print("\n========== 4. 封闭生效后，原在途单获得新分段路线 ==========")
    status, reroute_order = request_api(
        "POST",
        base_url,
        "/api/guide-orders",
        {
            "start_point_id": "south_gate",
            "departure_minute": 0,
            "destination_point_id": "north_hall",
            "deadline_minute": 35,
            "tourist_name": "示例轮椅游客 B",
        },
    )
    show("发出 GD-0002", status, reroute_order, ["id", "status", "current_route"])
    must(status == 200 and reroute_order["id"] == "GD-0002", "GD-0002 成功发单")

    status, checked_b = request_api(
        "POST",
        base_url,
        "/api/guide-orders/GD-0002/check-ins",
        {"point_id": "central_hall", "minute": 8},
    )
    must(status == 200, "GD-0002 在中央大厅 8 分报到")
    print("报到后续走路线:", json.dumps(segment_summary(checked_b["current_route"]), ensure_ascii=False))

    status, accepted_closure = request_api(
        "POST",
        base_url,
        "/api/closures",
        {
            "passage_id": "central_north",
            "start_minute": 8,
            "end_minute": 30,
            "reason": "中央直廊清洁",
        },
    )
    show("拟封 central_north 8-30", status, accepted_closure, ["closure", "blocked_order_ids", "rerouted_order_ids"])
    must(status == 200, "有东侧替代路线，封闭生效")
    must(accepted_closure["blocked_order_ids"] == [], "没有在途单受阻")
    must("GD-0002" in accepted_closure["rerouted_order_ids"], "GD-0002 已重算路线")

    status, fetched_b = request_api("GET", base_url, "/api/guide-orders/GD-0002")
    route_b = fetched_b["current_route"]
    show("查询原在途单 GD-0002 的新路线", status, {
        "arrival_minute": route_b["arrival_minute"],
        "segments": segment_summary(route_b),
        "waits": route_b["waits"],
    })
    segment_ids = [segment["passage_id"] for segment in route_b["segments"]]
    must(segment_ids == ["central_east", "east_north"], "新分段路线绕经东连廊")
    must(route_b["arrival_minute"] == 22, "新路线 22 分到达，早于 35 分期限")
    must(
        all(
            not (
                segment["passage_id"] == "central_north"
                and segment["departure_minute"] < 30
                and segment["arrival_minute"] > 8
            )
            for segment in route_b["segments"]
        ),
        "所有通行区间均避开 8-30 封闭",
    )

    status, completed_b = request_api(
        "POST",
        base_url,
        "/api/guide-orders/GD-0002/check-ins",
        {"point_id": "north_hall", "minute": 22},
    )
    must(status == 200 and completed_b["status"] == "completed", "GD-0002 到终点报到后结单")

    print("\n========== 5. 只能在等候点停留 ==========")
    status, wait_order = request_api(
        "POST",
        base_url,
        "/api/guide-orders",
        {
            "start_point_id": "quiet_gate",
            "departure_minute": 0,
            "destination_point_id": "quiet_gallery",
            "deadline_minute": 35,
            "tourist_name": "示例轮椅游客 C",
        },
    )
    must(status == 200 and wait_order["id"] == "GD-0003", "GD-0003 成功发单")
    status, _ = request_api(
        "POST",
        base_url,
        "/api/guide-orders/GD-0003/check-ins",
        {"point_id": "lounge_hub", "minute": 4},
    )
    must(status == 200, "GD-0003 在轮椅等候区 4 分报到")

    status, wait_closure = request_api(
        "POST",
        base_url,
        "/api/closures",
        {"passage_id": "lounge_gallery", "start_minute": 4, "end_minute": 16},
    )
    must(status == 200, "等候到 16 分后仍可按时到达，封闭生效")

    status, fetched_c = request_api("GET", base_url, "/api/guide-orders/GD-0003")
    route_c = fetched_c["current_route"]
    show("GD-0003 新路线含合法等候", status, {
        "arrival_minute": route_c["arrival_minute"],
        "waits": route_c["waits"],
        "segments": segment_summary(route_c),
    })
    must(route_c["waits"][0]["point_id"] == "lounge_hub", "停留点是可等候点位")
    must((route_c["waits"][0]["from_minute"], route_c["waits"][0]["to_minute"]) == (4, 16), "在等候点停留至通道重开")
    must(route_c["segments"][0]["departure_minute"] == 16, "16 分封闭结束后才进入通道")
    must(route_c["arrival_minute"] == 28, "28 分到达，未超过 35 分期限")

    status, completed_c = request_api(
        "POST",
        base_url,
        "/api/guide-orders/GD-0003/check-ins",
        {"point_id": "quiet_gallery", "minute": 28},
    )
    must(status == 200 and completed_c["status"] == "completed", "GD-0003 结单")

    status, final_health = request_api("GET", base_url, "/health")
    print("\n========== 演示完成 ==========")
    print(json.dumps(final_health, ensure_ascii=False, indent=2))
    must(status == 200 and final_health["guide_orders"] == 3, "3 张引导单均已处理")
    must(final_health["closures"] == 2, "2 条可行封闭已生效，1 条受阻封闭未写入")
    print("\nDEMO PASSED: 发单、报到、受阻回滚、封闭后改线、等候与结单均符合预期。")
    print("交互式文档: " + base_url.rstrip("/") + "/docs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Demonstrate the museum passage API")
    parser.add_argument(
        "--base-url",
        default=os.getenv("API_BASE_URL", "http://127.0.0.1:8000"),
        help="API base URL (default: http://127.0.0.1:8000 or $API_BASE_URL)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        run(parse_args().base_url)
    except KeyboardInterrupt:
        sys.exit(130)
