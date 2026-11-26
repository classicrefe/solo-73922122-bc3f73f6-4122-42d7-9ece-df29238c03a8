from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import store


@pytest.fixture
def client():
    store.reset()
    with TestClient(app) as test_client:
        yield test_client


def create_point(client: TestClient, point_id: str, can_wait: bool = False) -> None:
    response = client.post(
        "/api/points",
        json={"id": point_id, "name": point_id, "can_wait": can_wait},
    )
    assert response.status_code == 200, response.text


def create_passage(
    client: TestClient,
    passage_id: str,
    from_id: str,
    to_id: str,
    minutes: int,
    bidirectional: bool = False,
) -> None:
    response = client.post(
        "/api/passages",
        json={
            "id": passage_id,
            "from_point_id": from_id,
            "to_point_id": to_id,
            "minutes": minutes,
            "bidirectional": bidirectional,
        },
    )
    assert response.status_code == 200, response.text


def issue_order(
    client: TestClient,
    start: str = "a",
    destination: str = "c",
    departure: int = 0,
    deadline: int = 20,
):
    return client.post(
        "/api/guide-orders",
        json={
            "start_point_id": start,
            "departure_minute": departure,
            "destination_point_id": destination,
            "deadline_minute": deadline,
        },
    )


def test_health_and_demo_seed(client: TestClient) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    seeded = client.post("/api/demo/seed")
    assert seeded.status_code == 200
    body = seeded.json()
    assert len(body["point_ids"]) == 10
    assert len(body["passage_ids"]) == 8
    assert client.get("/api/passages").json()[0]["from_point_id"]


def test_issue_only_on_time_guide_order(client: TestClient) -> None:
    create_point(client, "a")
    create_point(client, "b")
    create_passage(client, "ab", "a", "b", 5)

    ok = issue_order(client, destination="b", deadline=5)
    assert ok.status_code == 200, ok.text
    order = ok.json()
    assert order["id"] == "GD-0001"
    assert order["order_no"] == "GD-0001"
    assert order["current_route"]["arrival_minute"] == 5
    assert order["current_route"]["segments"][0]["passage_id"] == "ab"

    impossible = issue_order(client, destination="b", deadline=4)
    assert impossible.status_code == 409
    assert impossible.json()["feasible"] is False
    assert impossible.json()["route"]["found"] is False
    assert len(client.get("/api/guide-orders").json()) == 1


def test_check_in_replans_from_latest_report_and_completes(
    client: TestClient,
) -> None:
    create_point(client, "a")
    create_point(client, "b")
    create_point(client, "c")
    create_passage(client, "ab", "a", "b", 3)
    create_passage(client, "bc", "b", "c", 4)

    order_id = issue_order(client, deadline=20).json()["id"]

    check_in = client.post(
        f"/api/guide-orders/{order_id}/check-ins",
        json={"point_id": "b", "minute": 3},
    )
    assert check_in.status_code == 200, check_in.text
    body = check_in.json()
    assert body["status"] == "in_transit"
    assert body["latest_report"] == {
        "point_id": "b",
        "minute": 3,
        "note": None,
        "reporter": None,
    }
    route = body["current_route"]
    assert route["start_point_id"] == "b"
    assert route["start_minute"] == 3
    assert route["arrival_minute"] == 7
    assert [s["passage_id"] for s in route["segments"]] == ["bc"]

    completion = client.post(
        f"/api/guide-orders/{order_id}/check-ins",
        json={"point_id": "c", "minute": 7},
    )
    assert completion.status_code == 200
    assert completion.json()["status"] == "completed"
    assert completion.json()["completion_minute"] == 7

    # Completed orders are not active closure constraints.
    closure = client.post(
        "/api/closures",
        json={"passage_id": "bc", "start_minute": 3, "end_minute": 20},
    )
    assert closure.status_code == 200, closure.text


def test_earlier_check_in_is_rejected(client: TestClient) -> None:
    create_point(client, "a")
    create_point(client, "b", can_wait=True)
    create_point(client, "c")
    create_passage(client, "ab", "a", "b", 3)
    create_passage(client, "bc", "b", "c", 4)
    order_id = issue_order(client).json()["id"]

    assert (
        client.post(
            f"/api/guide-orders/{order_id}/check-ins",
            json={"point_id": "b", "minute": 8},
        ).status_code
        == 200
    )
    stale = client.post(
        f"/api/guide-orders/{order_id}/check-ins",
        json={"point_id": "b", "minute": 7},
    )
    assert stale.status_code == 409


def test_cannot_wait_at_non_waitable_point(client: TestClient) -> None:
    create_point(client, "a")
    create_point(client, "hub", can_wait=False)
    create_point(client, "d")
    create_passage(client, "a_hub", "a", "hub", 2)
    create_passage(client, "hub_d", "hub", "d", 5)

    order_id = issue_order(client, destination="d", deadline=20).json()["id"]
    check_in = client.post(
        f"/api/guide-orders/{order_id}/check-ins",
        json={"point_id": "hub", "minute": 2},
    )
    assert check_in.status_code == 200

    response = client.post(
        "/api/closures",
        json={"passage_id": "hub_d", "start_minute": 2, "end_minute": 10},
    )
    assert response.status_code == 409
    assert response.json()["blocked_order_ids"] == [order_id]
    assert response.json()["original_closures_preserved"] is True
    assert client.get("/api/closures").json() == []


def test_can_wait_only_at_waitable_point_and_route_uses_wait(
    client: TestClient,
) -> None:
    create_point(client, "a")
    create_point(client, "hub", can_wait=True)
    create_point(client, "d")
    create_passage(client, "a_hub", "a", "hub", 2)
    create_passage(client, "hub_d", "hub", "d", 5)

    order_id = issue_order(client, destination="d", deadline=20).json()["id"]
    client.post(
        f"/api/guide-orders/{order_id}/check-ins",
        json={"point_id": "hub", "minute": 2},
    )

    response = client.post(
        "/api/closures",
        json={"passage_id": "hub_d", "start_minute": 2, "end_minute": 10},
    )
    assert response.status_code == 200, response.text

    order = client.get(f"/api/guide-orders/{order_id}").json()
    route = order["current_route"]
    assert route["arrival_minute"] == 15
    assert route["waits"] == [
        {
            "point_id": "hub",
            "from_minute": 2,
            "to_minute": 10,
            "minutes": 8,
        }
    ]
    segment = route["segments"][0]
    assert (segment["departure_minute"], segment["arrival_minute"]) == (10, 15)
    assert segment["wait_before_minutes"] == 8
    assert segment["wait_at_point_id"] == "hub"


def test_rejected_closure_preserves_original_arrangement(
    client: TestClient,
) -> None:
    create_point(client, "a", can_wait=True)
    create_point(client, "b")
    create_passage(client, "ab", "a", "b", 5)
    order_id = issue_order(client, destination="b", deadline=10).json()["id"]

    first = client.post(
        "/api/closures",
        json={"passage_id": "ab", "start_minute": 100, "end_minute": 110},
    )
    assert first.status_code == 200
    first_id = first.json()["closure"]["id"]

    rejected = client.post(
        "/api/closures",
        json={"passage_id": "ab", "start_minute": 0, "end_minute": 10},
    )
    assert rejected.status_code == 409
    assert rejected.json()["blocked_order_ids"] == [order_id]

    closures = client.get("/api/closures").json()
    assert [closure["id"] for closure in closures] == [first_id]
    order = client.get(f"/api/guide-orders/{order_id}").json()
    assert order["current_route"]["arrival_minute"] == 5


def test_successful_closure_reroutes_to_segmented_alternative(
    client: TestClient,
) -> None:
    create_point(client, "south")
    create_point(client, "hub", can_wait=True)
    create_point(client, "east")
    create_point(client, "north")
    create_passage(client, "south_hub", "south", "hub", 3)
    create_passage(client, "hub_north", "hub", "north", 5)
    create_passage(client, "hub_east", "hub", "east", 2)
    create_passage(client, "east_north", "east", "north", 2)

    order_id = issue_order(
        client, start="south", destination="north", deadline=20
    ).json()["id"]
    client.post(
        f"/api/guide-orders/{order_id}/check-ins",
        json={"point_id": "hub", "minute": 3},
    )

    proposed = client.post(
        "/api/closures",
        json={"passage_id": "hub_north", "start_minute": 3, "end_minute": 12},
    )
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["blocked_order_ids"] == []
    assert proposed.json()["rerouted_order_ids"] == [order_id]

    order = client.get(f"/api/guide-orders/{order_id}").json()
    route = order["current_route"]
    assert route["arrival_minute"] == 7
    assert [s["passage_id"] for s in route["segments"]] == [
        "hub_east",
        "east_north",
    ]
    assert [
        (s["departure_minute"], s["arrival_minute"]) for s in route["segments"]
    ] == [(3, 5), (5, 7)]
    assert any(
        snapshot["event"] == "closure_reroute"
        and snapshot["closure_id"] == proposed.json()["closure"]["id"]
        for snapshot in order["route_history"]
    )


def test_closure_boundary_is_half_open(client: TestClient) -> None:
    create_point(client, "a")
    create_point(client, "b")
    create_passage(client, "ab", "a", "b", 5)

    # Traversal [0, 5) does not overlap closure [5, 10).
    response = client.post(
        "/api/closures",
        json={"passage_id": "ab", "start_minute": 5, "end_minute": 10},
    )
    assert response.status_code == 200, response.text

    order = issue_order(client, destination="b", departure=0, deadline=5)
    assert order.status_code == 200


def test_closure_dry_run_reports_block_without_mutating(
    client: TestClient,
) -> None:
    create_point(client, "a")
    create_point(client, "b")
    create_passage(client, "ab", "a", "b", 5)
    order_id = issue_order(client, destination="b", deadline=10).json()["id"]

    dry_run = client.post(
        "/api/closures/evaluate",
        json={"passage_id": "ab", "start_minute": 0, "end_minute": 20},
    )
    assert dry_run.status_code == 200
    body = dry_run.json()
    assert body["effective"] is False
    assert body["would_apply"] is False
    assert body["blocked_order_ids"] == [order_id]
    assert client.get("/api/closures").json() == []


def test_passage_wait_points_are_marked_waitable(client: TestClient) -> None:
    create_point(client, "a")
    create_point(client, "b")
    response = client.post(
        "/api/passages",
        json={
            "id": "p",
            "from_point_id": "a",
            "to_point_id": "b",
            "minutes": 3,
            "wait_points": ["mid", {"id": "cafe", "name": "Cafe"}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["wait_point_ids"] == ["mid", "cafe"]
    assert client.get("/api/points/mid").json()["can_wait"] is True
    assert client.get("/api/locations/cafe").json()["name"] == "Cafe"


def test_input_aliases_and_hhmm_minutes(client: TestClient) -> None:
    response = client.post(
        "/api/locations",
        json={"code": "entrance", "label": "入口", "waitable": False},
    )
    assert response.status_code == 200
    assert response.json()["id"] == "entrance"

    client.post("/api/points", json={"id": "hall", "can_wait": True})
    corridor = client.post(
        "/api/corridors",
        json={
            "code": "c1",
            "from": "entrance",
            "to": "hall",
            "duration": "00:08",
            "both_directions": True,
        },
    )
    assert corridor.status_code == 200, corridor.text
    assert corridor.json()["minutes"] == 8
    assert corridor.json()["bidirectional"] is True

    order = client.post(
        "/api/guide-sheets",
        json={
            "start_point": "entrance",
            "start_time": "09:00",
            "destination": "hall",
            "arrival_deadline": "09:30",
        },
    )
    assert order.status_code == 200, order.text
    assert order.json()["departure_minute"] == 540
    assert order.json()["deadline_minute"] == 570
