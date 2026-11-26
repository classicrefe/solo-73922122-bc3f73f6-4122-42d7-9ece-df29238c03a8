"""Thread-safe in-memory store and transactional closure evaluation."""

from __future__ import annotations

import threading
from typing import Any

from .models import (
    CheckIn,
    CheckInCreate,
    Closure,
    ClosureCreate,
    GuideOrder,
    GuideOrderCreate,
    Passage,
    PassageCreate,
    Point,
    PointCreate,
    Route,
    RouteSnapshot,
)
from .routing import find_route


class StoreError(Exception):
    """Base class for store-level errors."""


class NotFoundError(StoreError):
    pass


class DuplicateError(StoreError):
    pass


class ConflictError(StoreError):
    pass


class OrderNotIssuedError(StoreError):
    def __init__(self, route: Route):
        self.route = route
        super().__init__(route.reason or "route is not feasible")


class ClosureBlockedError(StoreError):
    def __init__(self, candidate: Closure, blocked: list[tuple[GuideOrder, Route]]):
        self.candidate = candidate
        self.blocked = blocked
        ids = ", ".join(order.id for order, _ in blocked)
        super().__init__(f"proposed closure blocks orders: {ids}")


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.RLock()):
            self.points: dict[str, Point] = {}
            self.passages: dict[str, Passage] = {}
            self.closures: dict[str, Closure] = {}
            self.orders: dict[str, GuideOrder] = {}
            self._counters: dict[str, int] = {
                "point": 0,
                "passage": 0,
                "closure": 0,
                "order": 0,
            }

    def _next_id(self, kind: str, prefix: str) -> str:
        self._counters[kind] += 1
        return f"{prefix}-{self._counters[kind]:04d}"

    # ---------- points and passages ----------

    def list_points(self) -> list[Point]:
        with self._lock:
            return list(self.points.values())

    def get_point(self, identifier: str) -> Point:
        with self._lock:
            if identifier in self.points:
                return self.points[identifier]
            for point in self.points.values():
                if point.name == identifier:
                    return point
            raise NotFoundError(f"point not found: {identifier}")

    def create_point(self, payload: PointCreate) -> Point:
        with self._lock:
            point_id = payload.id or self._next_id("point", "PT")
            if point_id in self.points:
                raise DuplicateError(f"point already exists: {point_id}")
            point = Point(
                id=point_id,
                name=payload.name or point_id,
                can_wait=payload.can_wait,
            )
            self.points[point_id] = point
            return point

    def _ensure_wait_point(self, value: Any) -> str:
        if isinstance(value, PointCreate):
            payload = value
        elif isinstance(value, dict):
            payload = PointCreate.model_validate(value)
        else:
            point_id = str(value)
            existing = self.points.get(point_id)
            if existing is not None:
                existing.can_wait = True
                return point_id
            payload = PointCreate(id=point_id, name=point_id, can_wait=True)

        point_id = payload.id or self._next_id("point", "PT")
        payload.id = point_id
        payload.can_wait = True
        if point_id in self.points:
            self.points[point_id].can_wait = True
            if payload.name:
                self.points[point_id].name = payload.name
        else:
            self.create_point(payload)
        return point_id

    def list_passages(self) -> list[Passage]:
        with self._lock:
            return list(self.passages.values())

    def get_passage(self, identifier: str) -> Passage:
        with self._lock:
            if identifier in self.passages:
                return self.passages[identifier]
            for passage in self.passages.values():
                if passage.name == identifier:
                    return passage
            raise NotFoundError(f"passage not found: {identifier}")

    def create_passage(self, payload: PassageCreate) -> Passage:
        with self._lock:
            passage_id = payload.id or self._next_id("passage", "PS")
            if passage_id in self.passages:
                raise DuplicateError(f"passage already exists: {passage_id}")

            from_point = self.get_point(payload.from_point_id)
            to_point = self.get_point(payload.to_point_id)

            wait_ids: list[str] = []
            for value in list(payload.wait_point_ids) + list(payload.wait_points):
                wait_id = self._ensure_wait_point(value)
                if wait_id not in wait_ids:
                    wait_ids.append(wait_id)

            token = (payload.direction or "forward").strip().lower()
            both_tokens = {
                "both",
                "bidirectional",
                "two_way",
                "two-way",
                "双向",
                "往返",
            }
            reverse_tokens = {"reverse", "backward", "backwards", "反向", "逆向"}
            if payload.bidirectional or token in both_tokens:
                direction = "both"
                bidirectional = True
            elif token in reverse_tokens:
                direction = "reverse"
                bidirectional = False
            else:
                direction = "forward"
                bidirectional = False

            known_tokens = both_tokens | reverse_tokens | {
                "forward",
                "one_way",
                "one-way",
                "单向",
                "正向",
            }
            label = payload.direction_label
            if label is None and token not in known_tokens:
                label = payload.direction

            passage = Passage(
                id=passage_id,
                name=payload.name,
                from_point_id=from_point.id,
                to_point_id=to_point.id,
                minutes=payload.minutes,
                bidirectional=bidirectional,
                direction=direction,
                direction_label=label,
                wait_point_ids=wait_ids,
            )
            self.passages[passage_id] = passage
            return passage

    # ---------- closures ----------

    def list_closures(self) -> list[Closure]:
        with self._lock:
            return list(self.closures.values())

    def get_closure(self, closure_id: str) -> Closure:
        with self._lock:
            try:
                return self.closures[closure_id]
            except KeyError as exc:
                raise NotFoundError(f"closure not found: {closure_id}") from exc

    def _evaluate_closure_locked(
        self, payload: ClosureCreate
    ) -> tuple[
        Closure,
        list[tuple[GuideOrder, Route]],
        dict[str, Route],
        list[Closure],
    ]:
        passage = self.get_passage(payload.passage_id)
        candidate = Closure(
            id="CL-PENDING",
            passage_id=passage.id,
            start_minute=payload.start_minute,
            end_minute=payload.end_minute,
            reason=payload.reason,
        )

        blocked: list[tuple[GuideOrder, Route]] = []
        feasible_routes: dict[str, Route] = {}
        for order in self.orders.values():
            if order.status == "completed":
                continue

            report = order.latest_report
            if report is None:
                source_point = order.start_point_id
                source_minute = order.departure_minute
            else:
                source_point = report.point_id
                source_minute = report.minute

            route = find_route(
                points=list(self.points.values()),
                passages=list(self.passages.values()),
                closures=list(self.closures.values()),
                start_point_id=source_point,
                start_minute=source_minute,
                destination_point_id=order.destination_point_id,
                deadline_minute=order.deadline_minute,
                extra_closure=candidate,
            )
            if not route.found:
                blocked.append((order, route))
            else:
                feasible_routes[order.id] = route

        return candidate, blocked, feasible_routes, self.list_closures()

    def evaluate_closure(
        self, payload: ClosureCreate
    ) -> tuple[
        Closure,
        list[tuple[GuideOrder, Route]],
        dict[str, Route],
        list[Closure],
    ]:
        """Dry-run a proposed closure without changing state."""
        with self._lock:
            return self._evaluate_closure_locked(payload)

    def propose_closure(
        self, payload: ClosureCreate
    ) -> tuple[Closure, list[GuideOrder]]:
        """Atomically validate and apply a closure.

        The candidate is evaluated against every non-completed order using its
        latest report. If any order cannot arrive by its deadline, nothing is
        inserted and existing closures/routes are left untouched.
        """
        with self._lock:
            candidate, blocked, feasible_routes, _ = (
                self._evaluate_closure_locked(payload)
            )
            if blocked:
                raise ClosureBlockedError(candidate, blocked)

            closure_id = self._next_id("closure", "CL")
            closure = candidate.model_copy(update={"id": closure_id})
            self.closures[closure_id] = closure

            rerouted: list[GuideOrder] = []
            for order_id, route in feasible_routes.items():
                order = self.orders[order_id]
                order.current_route = route
                order.feasible = True
                order.on_time = route.on_time
                event_minute = (
                    order.latest_report.minute
                    if order.latest_report is not None
                    else order.departure_minute
                )
                order.route_history.append(
                    RouteSnapshot(
                        event="closure_reroute",
                        minute=event_minute,
                        closure_id=closure.id,
                        route=route,
                    )
                )
                rerouted.append(order)

            return closure, rerouted

    # ---------- guide orders ----------

    def list_orders(self, status: str | None = None) -> list[GuideOrder]:
        with self._lock:
            orders = list(self.orders.values())
            if status:
                orders = [order for order in orders if order.status == status]
            return orders

    def get_order(self, identifier: str) -> GuideOrder:
        with self._lock:
            if identifier in self.orders:
                return self.orders[identifier]
            for order in self.orders.values():
                if order.order_no == identifier:
                    return order
            raise NotFoundError(f"guide order not found: {identifier}")

    def create_order(self, payload: GuideOrderCreate) -> GuideOrder:
        with self._lock:
            start = self.get_point(payload.start_point_id)
            destination = self.get_point(payload.destination_point_id)

            route = find_route(
                points=list(self.points.values()),
                passages=list(self.passages.values()),
                closures=list(self.closures.values()),
                start_point_id=start.id,
                start_minute=payload.departure_minute,
                destination_point_id=destination.id,
                deadline_minute=payload.deadline_minute,
            )
            if not route.found:
                raise OrderNotIssuedError(route)

            order_id = self._next_id("order", "GD")
            order = GuideOrder(
                id=order_id,
                order_no=order_id,
                start_point_id=start.id,
                departure_minute=payload.departure_minute,
                destination_point_id=destination.id,
                deadline_minute=payload.deadline_minute,
                tourist_name=payload.tourist_name,
                note=payload.note,
                status="issued",
                feasible=True,
                on_time=True,
                issued_route=route,
                current_route=route,
                route_history=[
                    RouteSnapshot(
                        event="issued",
                        minute=payload.departure_minute,
                        route=route,
                    )
                ],
            )
            self.orders[order_id] = order
            return order

    def check_in(self, order_identifier: str, payload: CheckInCreate) -> GuideOrder:
        with self._lock:
            order = self.get_order(order_identifier)
            if order.status == "completed":
                raise ConflictError(f"order is already completed: {order.id}")

            point = self.get_point(payload.point_id)
            earliest_minute = (
                order.latest_report.minute
                if order.latest_report is not None
                else order.departure_minute
            )
            if payload.minute < earliest_minute:
                raise ConflictError(
                    f"check-in minute {payload.minute} is earlier than latest known minute "
                    f"{earliest_minute}"
                )

            report = CheckIn(
                point_id=point.id,
                minute=payload.minute,
                note=payload.note,
                reporter=payload.reporter,
            )
            order.check_ins.append(report)
            order.latest_report = report

            if point.id == order.destination_point_id:
                order.status = "completed"
                order.completion_minute = payload.minute
                order.feasible = True
                order.on_time = payload.minute <= order.deadline_minute
                terminal_route = Route(
                    found=True,
                    start_point_id=point.id,
                    start_minute=payload.minute,
                    destination_point_id=point.id,
                    deadline_minute=order.deadline_minute,
                    arrival_minute=payload.minute,
                    on_time=order.on_time,
                    reason="completed",
                )
                order.current_route = terminal_route
                order.route_history.append(
                    RouteSnapshot(
                        event="completed",
                        minute=payload.minute,
                        route=terminal_route,
                    )
                )
                return order

            route = find_route(
                points=list(self.points.values()),
                passages=list(self.passages.values()),
                closures=list(self.closures.values()),
                start_point_id=point.id,
                start_minute=payload.minute,
                destination_point_id=order.destination_point_id,
                deadline_minute=order.deadline_minute,
            )
            order.status = "in_transit"
            order.current_route = route
            order.feasible = route.found
            order.on_time = route.on_time if route.found else False
            order.route_history.append(
                RouteSnapshot(event="check_in", minute=payload.minute, route=route)
            )
            return order


store = Store()
