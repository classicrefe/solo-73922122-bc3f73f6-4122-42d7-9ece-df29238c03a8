"""Time-dependent accessible routing.

The router uses minute states rather than only an earliest-arrival label.
A non-waitable point can be reached by different routes at different minutes,
and an earlier arrival may be unable to wait for a closed passage to reopen.
The single-day horizon (``MAX_DAY_MINUTE``) keeps the state search small.
"""

from __future__ import annotations

from typing import Any

from .models import Closure, Passage, Point, Route, RouteSegment, RouteWait


def find_conflicting_closure(
    *,
    passage_id: str,
    departure_minute: int,
    travel_minutes: int,
    closures: list[Closure],
) -> Closure | None:
    """Return a closure overlapping the traversal interval.

    Travel occupies the half-open interval
    ``[departure_minute, departure_minute + travel_minutes)`` and a closure
    occupies ``[start_minute, end_minute)``. Thus arriving exactly when a
    closure starts or departing exactly when it ends is allowed: the visitor
    is out of the passage at that boundary minute.
    """
    arrival_minute = departure_minute + travel_minutes
    for closure in closures:
        if closure.passage_id != passage_id:
            continue
        if departure_minute < closure.end_minute and closure.start_minute < arrival_minute:
            return closure
    return None


def _failed_route(
    *,
    start_point_id: str,
    start_minute: int,
    destination_point_id: str,
    deadline_minute: int | None,
    reason: str,
) -> Route:
    on_time = False if deadline_minute is not None else None
    return Route(
        found=False,
        start_point_id=start_point_id,
        start_minute=start_minute,
        destination_point_id=destination_point_id,
        deadline_minute=deadline_minute,
        arrival_minute=None,
        on_time=on_time,
        reason=reason,
    )


def find_route(
    *,
    points: list[Point] | dict[str, Point],
    passages: list[Passage] | dict[str, Passage],
    closures: list[Closure] | dict[str, Closure],
    start_point_id: str,
    start_minute: int,
    destination_point_id: str,
    deadline_minute: int,
    extra_closure: Closure | None = None,
) -> Route:
    """Find an on-time route, including legal waits at waitable points."""
    point_map = dict(points) if isinstance(points, dict) else {p.id: p for p in points}
    passage_list = list(passages.values()) if isinstance(passages, dict) else list(passages)
    closure_list = list(closures.values()) if isinstance(closures, dict) else list(closures)
    if extra_closure is not None:
        closure_list = closure_list + [extra_closure]

    if start_point_id not in point_map or destination_point_id not in point_map:
        return _failed_route(
            start_point_id=start_point_id,
            start_minute=start_minute,
            destination_point_id=destination_point_id,
            deadline_minute=deadline_minute,
            reason="unknown_point",
        )

    if start_minute > deadline_minute:
        return _failed_route(
            start_point_id=start_point_id,
            start_minute=start_minute,
            destination_point_id=destination_point_id,
            deadline_minute=deadline_minute,
            reason="start_after_deadline",
        )

    if start_point_id == destination_point_id:
        return Route(
            found=True,
            start_point_id=start_point_id,
            start_minute=start_minute,
            destination_point_id=destination_point_id,
            deadline_minute=deadline_minute,
            arrival_minute=start_minute,
            on_time=start_minute <= deadline_minute,
            reason=None,
        )

    outgoing: dict[str, list[dict[str, Any]]] = {}
    for passage in passage_list:
        runs_forward = passage.direction == "forward" or passage.direction == "both"
        runs_reverse = (
            passage.direction == "reverse"
            or passage.direction == "both"
            or passage.bidirectional
        )
        if runs_forward:
            outgoing.setdefault(passage.from_point_id, []).append(
                {
                    "passage": passage,
                    "to_point_id": passage.to_point_id,
                    "direction": "forward",
                }
            )
        if runs_reverse:
            outgoing.setdefault(passage.to_point_id, []).append(
                {
                    "passage": passage,
                    "to_point_id": passage.from_point_id,
                    "direction": "reverse",
                }
            )

    # states_by_time[t] is the set of points reachable at exact minute t.
    states_by_time: dict[int, set[str]] = {start_minute: {start_point_id}}
    # predecessor[(point, minute)] = (previous_key, action)
    predecessor: dict[tuple[str, int], tuple[tuple[str, int], dict[str, Any]]] = {}
    # For equal point/time states prefer the least physical travel and then
    # the fewest segments, avoiding pointless cycles that happen to arrive
    # at the same minute as a legal wait.
    cost: dict[tuple[str, int], tuple[int, int]] = {
        (start_point_id, start_minute): (0, 0)
    }
    arrival_minute: int | None = None

    for minute in range(start_minute, deadline_minute + 1):
        points_now = list(states_by_time.get(minute, ()))
        if destination_point_id in points_now:
            arrival_minute = minute
            break

        for point_id in points_now:
            point = point_map[point_id]

            # Waiting is a one-minute transition and may be repeated only at
            # points explicitly marked as waitable.
            current_cost = cost[(point_id, minute)]
            if point.can_wait and minute < deadline_minute:
                next_key = (point_id, minute + 1)
                if next_key not in predecessor or current_cost < cost[next_key]:
                    predecessor[next_key] = (
                        (point_id, minute),
                        {
                            "kind": "wait",
                            "point_id": point_id,
                            "from_minute": minute,
                            "to_minute": minute + 1,
                        },
                    )
                    cost[next_key] = current_cost
                    states_by_time.setdefault(minute + 1, set()).add(point_id)

            for edge in outgoing.get(point_id, []):
                passage: Passage = edge["passage"]
                conflict = find_conflicting_closure(
                    passage_id=passage.id,
                    departure_minute=minute,
                    travel_minutes=passage.minutes,
                    closures=closure_list,
                )
                if conflict is not None:
                    continue

                arrived_at = minute + passage.minutes
                if arrived_at > deadline_minute:
                    continue

                next_key = (edge["to_point_id"], arrived_at)
                candidate_cost = (
                    current_cost[0] + passage.minutes,
                    current_cost[1] + 1,
                )
                if next_key in predecessor and candidate_cost >= cost[next_key]:
                    continue

                predecessor[next_key] = (
                    (point_id, minute),
                    {
                        "kind": "edge",
                        "passage_id": passage.id,
                        "passage_code": passage.id,
                        "direction": edge["direction"],
                        "from_point_id": point_id,
                        "to_point_id": edge["to_point_id"],
                        "departure_minute": minute,
                        "arrival_minute": arrived_at,
                        "travel_minutes": passage.minutes,
                    },
                )
                cost[next_key] = candidate_cost
                states_by_time.setdefault(arrived_at, set()).add(edge["to_point_id"])

    if arrival_minute is None:
        return _failed_route(
            start_point_id=start_point_id,
            start_minute=start_minute,
            destination_point_id=destination_point_id,
            deadline_minute=deadline_minute,
            reason="no_feasible_route_by_deadline",
        )

    actions: list[dict[str, Any]] = []
    key = (destination_point_id, arrival_minute)
    start_key = (start_point_id, start_minute)
    while key != start_key:
        previous_key, action = predecessor[key]
        actions.append(action)
        key = previous_key
    actions.reverse()

    segments: list[RouteSegment] = []
    waits: list[RouteWait] = []
    total_travel = 0
    wait_start: int | None = None
    wait_end: int | None = None
    wait_point_id: str | None = None

    def flush_wait() -> int:
        nonlocal wait_start, wait_end, wait_point_id
        if wait_start is None or wait_end is None or wait_point_id is None:
            return 0
        minutes = wait_end - wait_start
        if minutes > 0:
            waits.append(
                RouteWait(
                    point_id=wait_point_id,
                    from_minute=wait_start,
                    to_minute=wait_end,
                    minutes=minutes,
                )
            )
        result = minutes
        wait_start = None
        wait_end = None
        wait_point_id = None
        return result

    for action in actions:
        if action["kind"] == "wait":
            if wait_start is None:
                wait_start = action["from_minute"]
                wait_point_id = action["point_id"]
            wait_end = action["to_minute"]
            continue

        pending_wait_point = wait_point_id
        wait_minutes = flush_wait()
        segment = RouteSegment(
            seq=len(segments) + 1,
            passage_id=action["passage_id"],
            passage_code=action["passage_code"],
            direction=action["direction"],
            from_point_id=action["from_point_id"],
            to_point_id=action["to_point_id"],
            departure_minute=action["departure_minute"],
            arrival_minute=action["arrival_minute"],
            travel_minutes=action["travel_minutes"],
            wait_before_minutes=wait_minutes,
            wait_at_point_id=pending_wait_point if wait_minutes else None,
        )
        segments.append(segment)
        total_travel += segment.travel_minutes

    flush_wait()
    total_wait = sum(w.minutes for w in waits)

    return Route(
        found=True,
        start_point_id=start_point_id,
        start_minute=start_minute,
        destination_point_id=destination_point_id,
        deadline_minute=deadline_minute,
        arrival_minute=arrival_minute,
        on_time=arrival_minute <= deadline_minute,
        total_travel_minutes=total_travel,
        total_wait_minutes=total_wait,
        segments=segments,
        waits=waits,
    )
