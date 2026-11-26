"""Deterministic demonstration network."""

from __future__ import annotations

from .models import PassageCreate, PointCreate
from .store import Store


def seed_demo(store: Store) -> dict[str, list[str]]:
    """Reset and create the demo hall.

    The redundant north route demonstrates rerouting; the annex route
    demonstrates a rejected closure; the quiet route demonstrates waiting at
    an explicitly waitable point.
    """
    store.reset()

    points = [
        PointCreate(id="south_gate", name="南入口", can_wait=False),
        PointCreate(id="central_hall", name="中央大厅等候点", can_wait=True),
        PointCreate(id="east_cloister", name="东连廊转角", can_wait=True),
        PointCreate(id="north_hall", name="北展厅", can_wait=True),
        PointCreate(id="annex_gate", name="副楼入口", can_wait=False),
        PointCreate(id="west_cloister", name="西侧连廊中段", can_wait=False),
        PointCreate(id="rare_book_hall", name="珍本展厅", can_wait=False),
        PointCreate(id="quiet_gate", name="静区入口", can_wait=False),
        PointCreate(id="lounge_hub", name="轮椅等候区", can_wait=True),
        PointCreate(id="quiet_gallery", name="静音展厅", can_wait=False),
    ]
    point_ids: list[str] = []
    for point in points:
        point_ids.append(store.create_point(point).id)

    passages = [
        PassageCreate(
            id="south_central",
            name="南入口至中央大厅无台阶通道",
            from_point_id="south_gate",
            to_point_id="central_hall",
            minutes=8,
            bidirectional=True,
        ),
        PassageCreate(
            id="central_north",
            name="中央大厅至北展厅直廊",
            from_point_id="central_hall",
            to_point_id="north_hall",
            minutes=10,
            bidirectional=True,
        ),
        PassageCreate(
            id="central_east",
            name="中央大厅至东连廊",
            from_point_id="central_hall",
            to_point_id="east_cloister",
            minutes=5,
            bidirectional=True,
        ),
        PassageCreate(
            id="east_north",
            name="东连廊至北展厅",
            from_point_id="east_cloister",
            to_point_id="north_hall",
            minutes=9,
            bidirectional=True,
        ),
        PassageCreate(
            id="annex_west",
            name="副楼入口至西连廊",
            from_point_id="annex_gate",
            to_point_id="west_cloister",
            minutes=5,
            bidirectional=False,
        ),
        PassageCreate(
            id="west_rare",
            name="西连廊至珍本展厅专用通道",
            from_point_id="west_cloister",
            to_point_id="rare_book_hall",
            minutes=8,
            bidirectional=False,
        ),
        PassageCreate(
            id="quiet_access",
            name="静区入口至等候区",
            from_point_id="quiet_gate",
            to_point_id="lounge_hub",
            minutes=4,
            bidirectional=True,
        ),
        PassageCreate(
            id="lounge_gallery",
            name="等候区至静音展厅直廊",
            from_point_id="lounge_hub",
            to_point_id="quiet_gallery",
            minutes=12,
            bidirectional=True,
        ),
    ]
    passage_ids: list[str] = []
    for passage in passages:
        passage_ids.append(store.create_passage(passage).id)

    return {"point_ids": point_ids, "passage_ids": passage_ids}
