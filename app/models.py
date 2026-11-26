"""Pydantic schemas and input normalization for the museum access API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The service models one opening day. Keeping this bound also makes the
# minute-state route search small and deterministic.
MAX_DAY_MINUTE = 24 * 60


def parse_minute(value: Any) -> int:
    """Accept an integer minute or a local ``HH:MM`` string."""
    if value is None:
        raise ValueError("minute is required")
    if isinstance(value, bool):
        raise ValueError("minute must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)

    text = str(value).strip().replace("：", ":")
    if not text:
        raise ValueError("minute is required")
    if ":" in text:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if not (0 <= hour <= 24 and 0 <= minute < 60):
            raise ValueError("clock time must be HH:MM")
        return hour * 60 + minute
    return int(float(text))


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on", "可", "是"}:
        return True
    if text in {"false", "0", "no", "n", "off", "否"}:
        return False
    return default


def remap(data: Any, aliases: dict[str, str]) -> Any:
    """Copy accepted external aliases onto canonical field names."""
    if not isinstance(data, dict):
        return data
    result = dict(data)
    for alias, canonical in aliases.items():
        if alias in result and canonical not in result:
            result[canonical] = result[alias]
    return result


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class PointCreate(ApiModel):
    id: str | None = None
    name: str | None = None
    can_wait: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        data = remap(
            data,
            {
                "code": "id",
                "point_id": "id",
                "point_code": "id",
                "key": "id",
                "label": "name",
                "title": "name",
                "waitable": "can_wait",
                "is_waitable": "can_wait",
                "waiting_allowed": "can_wait",
                "canWait": "can_wait",
                "is_wait_point": "can_wait",
                "wait_point": "can_wait",
                "waitPoint": "can_wait",
            },
        )
        if isinstance(data, dict) and "can_wait" in data:
            data = dict(data)
            data["can_wait"] = as_bool(data["can_wait"])
        return data


class Point(ApiModel):
    id: str
    name: str
    can_wait: bool = False


class PassageCreate(ApiModel):
    id: str | None = None
    name: str | None = None
    from_point_id: str
    to_point_id: str
    minutes: int = Field(gt=0, le=MAX_DAY_MINUTE)
    bidirectional: bool = False
    direction: str | None = "forward"
    direction_label: str | None = None
    wait_point_ids: list[str] = Field(default_factory=list)
    wait_points: list[Any] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        data = remap(
            data,
            {
                "code": "id",
                "passage_code": "id",
                "corridor_id": "id",
                "corridor_code": "id",
                "key": "id",
                "label": "name",
                "from": "from_point_id",
                "from_id": "from_point_id",
                "from_point": "from_point_id",
                "from_point_code": "from_point_id",
                "start_point_id": "from_point_id",
                "start_point_code": "from_point_id",
                "start_point": "from_point_id",
                "origin_id": "from_point_id",
                "origin": "from_point_id",
                "source_id": "from_point_id",
                "source": "from_point_id",
                "to": "to_point_id",
                "to_id": "to_point_id",
                "to_point": "to_point_id",
                "to_point_code": "to_point_id",
                "end_point_id": "to_point_id",
                "end_point_code": "to_point_id",
                "end_point": "to_point_id",
                "destination_id": "to_point_id",
                "destination": "to_point_id",
                "target_id": "to_point_id",
                "target": "to_point_id",
                "duration": "minutes",
                "duration_minutes": "minutes",
                "travel_minutes": "minutes",
                "pass_minutes": "minutes",
                "passage_minutes": "minutes",
                "walk_minutes": "minutes",
                "time": "minutes",
                "both_directions": "bidirectional",
                "two_way": "bidirectional",
                "twoWay": "bidirectional",
                "is_bidirectional": "bidirectional",
                "round_trip": "bidirectional",
                "wait_point_codes": "wait_point_ids",
                "waiting_point_ids": "wait_point_ids",
                "waiting_points": "wait_points",
                "waitable_points": "wait_points",
                "waitPointIds": "wait_point_ids",
                "waitPoints": "wait_points",
            },
        )
        if isinstance(data, dict):
            data = dict(data)
            if "minutes" in data and data["minutes"] is not None:
                data["minutes"] = parse_minute(data["minutes"])
            if "bidirectional" in data:
                data["bidirectional"] = as_bool(data["bidirectional"])
            # Some clients naturally put ID strings in wait_points.
            wait_points = data.get("wait_points") or []
            if wait_points and all(isinstance(x, (str, int)) for x in wait_points):
                existing = list(data.get("wait_point_ids") or [])
                data["wait_point_ids"] = existing + [str(x) for x in wait_points]
                data["wait_points"] = []
        return data

    @model_validator(mode="after")
    def _validate_endpoints(self) -> "PassageCreate":
        if self.from_point_id == self.to_point_id:
            raise ValueError("from_point_id and to_point_id must differ")
        return self


class Passage(ApiModel):
    id: str
    name: str | None = None
    from_point_id: str
    to_point_id: str
    minutes: int
    bidirectional: bool = False
    direction: Literal["forward", "reverse", "both"] = "forward"
    direction_label: str | None = None
    wait_point_ids: list[str] = Field(default_factory=list)


class ClosureCreate(ApiModel):
    passage_id: str
    start_minute: int = Field(ge=0, le=MAX_DAY_MINUTE)
    end_minute: int = Field(ge=0, le=MAX_DAY_MINUTE)
    reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        data = remap(
            data,
            {
                "passage_code": "passage_id",
                "corridor_id": "passage_id",
                "corridor_code": "passage_id",
                "corridor": "passage_id",
                "passage": "passage_id",
                "target_passage_id": "passage_id",
                "start": "start_minute",
                "from_minute": "start_minute",
                "closed_from": "start_minute",
                "start_time": "start_minute",
                "begin_minute": "start_minute",
                "starts_at_minute": "start_minute",
                "start_at": "start_minute",
                "end": "end_minute",
                "to_minute": "end_minute",
                "closed_to": "end_minute",
                "end_time": "end_minute",
                "finish_minute": "end_minute",
                "ends_at_minute": "end_minute",
                "end_at": "end_minute",
            },
        )
        if isinstance(data, dict):
            data = dict(data)
            for key in ("start_minute", "end_minute"):
                if data.get(key) is not None:
                    data[key] = parse_minute(data[key])
        return data

    @model_validator(mode="after")
    def _validate_interval(self) -> "ClosureCreate":
        if self.end_minute <= self.start_minute:
            raise ValueError("end_minute must be greater than start_minute")
        return self


class Closure(ClosureCreate):
    id: str


class RouteWait(ApiModel):
    point_id: str
    from_minute: int
    to_minute: int
    minutes: int


class RouteSegment(ApiModel):
    seq: int
    passage_id: str
    passage_code: str
    direction: Literal["forward", "reverse"] = "forward"
    from_point_id: str
    to_point_id: str
    departure_minute: int
    arrival_minute: int
    travel_minutes: int
    wait_before_minutes: int = 0
    wait_at_point_id: str | None = None


class Route(ApiModel):
    found: bool = True
    start_point_id: str
    start_minute: int
    destination_point_id: str
    deadline_minute: int | None = None
    arrival_minute: int | None = None
    on_time: bool | None = None
    total_travel_minutes: int = 0
    total_wait_minutes: int = 0
    segments: list[RouteSegment] = Field(default_factory=list)
    waits: list[RouteWait] = Field(default_factory=list)
    reason: str | None = None


class CheckInCreate(ApiModel):
    point_id: str
    minute: int = Field(ge=0, le=MAX_DAY_MINUTE)
    note: str | None = None
    reporter: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        data = remap(
            data,
            {
                "location_id": "point_id",
                "location_code": "point_id",
                "location": "point_id",
                "point": "point_id",
                "point_code": "point_id",
                "place_id": "point_id",
                "at_point_id": "point_id",
                "report_point_id": "point_id",
                "check_in_point_id": "point_id",
                "reported_minute": "minute",
                "report_minute": "minute",
                "check_in_minute": "minute",
                "checked_in_at_minute": "minute",
                "at_minute": "minute",
                "time": "minute",
                "timestamp_minute": "minute",
            },
        )
        if isinstance(data, dict) and data.get("minute") is not None:
            data = dict(data)
            data["minute"] = parse_minute(data["minute"])
        return data


class CheckIn(ApiModel):
    point_id: str
    minute: int
    note: str | None = None
    reporter: str | None = None


class CheckInRequest(CheckInCreate):
    order_id: str

    @model_validator(mode="before")
    @classmethod
    def _normalize_order(cls, data: Any) -> Any:
        return remap(
            data,
            {
                "guide_order_id": "order_id",
                "guide_sheet_id": "order_id",
                "order_no": "order_id",
                "order_number": "order_id",
                "ticket_id": "order_id",
                "id": "order_id",
            },
        )


class GuideOrderCreate(ApiModel):
    start_point_id: str
    departure_minute: int = Field(ge=0, le=MAX_DAY_MINUTE)
    destination_point_id: str
    deadline_minute: int = Field(ge=0, le=MAX_DAY_MINUTE)
    tourist_name: str | None = None
    note: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        data = remap(
            data,
            {
                "start_id": "start_point_id",
                "start_code": "start_point_id",
                "start_point_code": "start_point_id",
                "start_point": "start_point_id",
                "start": "start_point_id",
                "origin_point_id": "start_point_id",
                "origin_id": "start_point_id",
                "origin": "start_point_id",
                "from_point_id": "start_point_id",
                "from": "start_point_id",
                "depart_minute": "departure_minute",
                "start_minute": "departure_minute",
                "departure": "departure_minute",
                "departure_time": "departure_minute",
                "start_time": "departure_minute",
                "leave_minute": "departure_minute",
                "leave_at": "departure_minute",
                "end_point_id": "destination_point_id",
                "destination_id": "destination_point_id",
                "destination_code": "destination_point_id",
                "destination": "destination_point_id",
                "end_id": "destination_point_id",
                "end_code": "destination_point_id",
                "end_point": "destination_point_id",
                "end": "destination_point_id",
                "to_point_id": "destination_point_id",
                "arrival_point_id": "destination_point_id",
                "deadline": "deadline_minute",
                "arrival_deadline": "deadline_minute",
                "arrival_deadline_minute": "deadline_minute",
                "due_minute": "deadline_minute",
                "due": "deadline_minute",
                "latest_arrival_minute": "deadline_minute",
                "max_arrival_minute": "deadline_minute",
                "visitor_name": "tourist_name",
                "wheelchair_user": "tourist_name",
            },
        )
        if isinstance(data, dict):
            data = dict(data)
            for key in ("departure_minute", "deadline_minute"):
                if data.get(key) is not None:
                    data[key] = parse_minute(data[key])
        return data

    @model_validator(mode="after")
    def _validate_times(self) -> "GuideOrderCreate":
        if self.deadline_minute < self.departure_minute:
            raise ValueError("deadline_minute must be at or after departure_minute")
        return self


class RouteSnapshot(ApiModel):
    event: str
    minute: int
    closure_id: str | None = None
    route: Route


class GuideOrder(ApiModel):
    id: str
    order_no: str
    start_point_id: str
    departure_minute: int
    destination_point_id: str
    deadline_minute: int
    tourist_name: str | None = None
    note: str | None = None
    status: Literal["issued", "in_transit", "completed"] = "issued"
    feasible: bool = True
    on_time: bool | None = None
    latest_report: CheckIn | None = None
    check_ins: list[CheckIn] = Field(default_factory=list)
    issued_route: Route | None = None
    current_route: Route | None = None
    route_history: list[RouteSnapshot] = Field(default_factory=list)
    completion_minute: int | None = None
