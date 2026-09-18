"""§10's peak day, driven through the API.

Ingest, pickups, processing events, the nightly cycle, next-day routes,
outcomes, the return run -- each through its §13.2 endpoint, with the solver
work going over the queue as §13.1 requires.

**It must produce Task 5's numbers.** The API is a thin layer over the same
modules, so if driving the day through HTTP gives a different answer than
calling `ddn.simulation` directly, a handler is deciding something it should
not be. That is the property this test exists to hold, and it is worth more
than any of the individual assertions.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from ddn import assumptions
from ddn.api import workers
from ddn.api.store import Store
from ddn.model import VehicleType
from tests.api_harness import drain, make_app, make_pool
from tests.fixtures import peak_day
from tests.matrices import fake_matrix

HOUR = 3600
SEED = 7


@pytest.fixture(scope="module")
def day():
    return peak_day.load()


@pytest.fixture(scope="module")
def payload(day):
    """§10's day as the API takes it: the same inputs Task 5 used."""
    midnight = datetime.combine(day.collection_day, datetime.min.time())

    def secs(moment):
        return int((moment - midnight).total_seconds())

    pools: dict[str, list] = {}
    for e in peak_day.morning_pool():
        pools.setdefault(e.facility_id, []).append(
            {"package_id": e.package_id, "customer_id": e.customer_id,
             "lat": e.lat, "lon": e.lon, "status": "Ready",
             "geocode_confidence": "high", "priority": float(e.priority),
             "sla_date": e.sla_date.isoformat(), "facility_id": e.facility_id,
             "attempt_number": e.attempt_number,
             "customer_lat": e.lat, "customer_lon": e.lon})

    return {
        "day": day.delivery_day.isoformat(),
        "seed": SEED,
        "allocation": dict(peak_day.BIKE_ALLOCATION),
        "pools": pools,
        "facilities": [
            {"id": f.facility_id, "lat": f.lat, "lon": f.lon,
             "route_release_time": f.route_release_time.hour * HOUR,
             "transit_from_hub_min": f.transit_from_hub_min,
             "shift_start": 7 * HOUR, "shift_end": 15 * HOUR}
            for f in day.facilities],
        "bikes": [v.vehicle_id for v in day.vehicles
                  if v.type is VehicleType.MOTORBIKE],
        "vans": [{"vehicle_id": v.vehicle_id, "type": "van",
                  "role": v.role.value,
                  "capacity_mailbags": v.capacity_mailbags,
                  "capacity_weight_g": v.capacity_weight_g,
                  "shift_start": secs(v.shift_start),
                  "shift_end": secs(v.shift_end),
                  "linehaul_release_at": (None if v.linehaul_release_at is None
                                          else secs(v.linehaul_release_at))}
                 for v in day.vehicles if v.type is VehicleType.VAN],
        "requests": [{"mailbag_id": b.mailbag_id, "customer_id": b.customer_id,
                      "lat": r.lat, "lon": r.lon,
                      "requested_at": secs(r.requested_at),
                      "expected_weight_g": b.expected_weight_g,
                      "envelope_count": b.envelope_count}
                     for r in day.requests for b in r.mailbags],
        "inflow": [{"package_id": e.package_id, "mailbag_id": e.mailbag_id,
                    "package_type": e.package_type,
                    "facility_id": e.facility_id, "lat": e.lat, "lon": e.lon,
                    "status": "Ready", "geocode_confidence": "high",
                    "priority": float(e.priority),
                    "sla_date": e.sla_date.isoformat(),
                    "customer_id": e.customer_id} for e in day.ready()],
    }


@pytest.fixture(scope="module")
def payload_with_travel(payload, day):
    """§3.3 and §5.1 are road distance, so the caller supplies a matrix.

    A fake one — `tests/matrices.py` — spanning exactly the hub and the bag
    sites, which is where §5.1's legs run.
    """
    hub = next(f for f in payload["facilities"] if f["id"] == "HUB")
    points = [hub, *payload["requests"]]
    matrix = fake_matrix(points)
    return dict(payload, matrix={"durations": [list(r) for r in matrix.durations],
                                 "distances": [list(r) for r in matrix.distances]})


@pytest.fixture
def pool():
    return make_pool()


@pytest.fixture
def store():
    return Store()


@pytest.fixture
async def client(pool, store):
    async with AsyncClient(transport=ASGITransport(app=make_app(pool, store)),
                           base_url="http://api") as c:
        yield c


async def test_the_peak_day_through_the_api_matches_task_5(
        client, pool, store, day, payload_with_travel):
    payload = payload_with_travel
    """The whole chain, and then the same numbers."""
    # ---- ingest (§5.1.1): the upload file precedes the bag.
    sample = [{"package_id": e["package_id"], "customer_id": e["customer_id"],
               "recipient_id": f"RCPT-{n}", "package_type": e["package_type"],
               "mailbag_id": e["mailbag_id"], "status": "Requested",
               "lat": e["lat"], "lon": e["lon"], "coord_source": "actual",
               "geocode_confidence": "high", "facility_id": e["facility_id"],
               "priority": e["priority"], "sla_date": e["sla_date"]}
              for n, e in enumerate(payload["inflow"][:50])]
    ingested = await client.post("/envelopes/batch", json={"envelopes": sample},
                                 headers={"Idempotency-Key": "peak-ingest"})
    assert ingested.status_code == 202
    assert ingested.json()["accepted"] == 50

    # ---- pickups (§5.1): a bag is ready, a van collects it.
    bag = payload["requests"][0]
    created = await client.post("/pickups", headers={"Idempotency-Key": "bag-1"},
                                json={"mailbag_id": bag["mailbag_id"],
                                      "customer_id": bag["customer_id"],
                                      "lat": bag["lat"], "lon": bag["lon"],
                                      "requested_at": "2026-09-16T08:00:00",
                                      "envelope_count": bag["envelope_count"],
                                      "expected_weight_g": bag["expected_weight_g"],
                                      "seal_id": "SEAL-0001"})
    assert created.status_code == 201
    collected = await client.post(
        f"/pickups/{bag['mailbag_id']}/events",
        json={"event": "collected", "actor": "driver-3"},
        headers={"Idempotency-Key": "bag-1-collected"})
    assert collected.status_code == 200
    assert collected.json()["collected"] is True

    # ---- processing events (§5.2): the hub reports, it does not compute.
    first = sample[0]["package_id"]
    for step, key in (("Collected", "e1"), ("Received at hub", "e2")):
        moved = await client.post(f"/envelopes/{first}/events",
                                  json={"event": step, "actor": "hub-1"},
                                  headers={"Idempotency-Key": key})
        assert moved.status_code == 200
    reconciled = await client.post(
        "/processing/events",
        json={"package_id": first, "event": "Reconciled", "actor": "hub-1"},
        headers={"Idempotency-Key": "e3"})
    assert reconciled.status_code == 200
    assert reconciled.json()["status"] == "Reconciled"

    # ---- the nightly cycle (§13.3), enqueuing rather than running inline.
    queued = await workers.nightly_cycle(store, pool, {
        "day": payload["day"],
        "pools": {f: len(p) for f, p in payload["pools"].items()},
        "bikes": payload["bikes"],
        "linehaul": {"day": payload["day"], "facilities": [],
                     "envelopes": [], "vans": payload["vans"],
                     "unload_seconds": assumptions.FACILITY_UNLOAD_MIN * 60},
        "routes": {}, "facilities": []})
    assert set(queued) == {"allocation", "linehaul"}

    # ---- the day itself (§5.6), as a job.
    accepted = await client.post("/simulation/days", json=payload)
    assert accepted.status_code == 202
    await drain(pool)

    state = (await client.get(accepted.json()["poll"])).json()
    assert state["status"] == "complete"
    report = state["result"]["days"][0]
    tally = report["tally"]

    # ---- Task 5's numbers, through HTTP.
    assert tally["ready_pool"] == peak_day.MORNING_POOL == 3100
    assert tally["dispatched"] == 2910
    assert tally["delivered"] == 2717
    assert tally["unassigned"] == 190
    assert report["positioned"] == dict(peak_day.READY_BY_FACILITY)
    assert sum(report["positioned"].values()) == peak_day.READY == 4550
    assert report["rolled"] == {}
    assert state["result"]["tomorrow_pool"] == 4850
    assert report["binding"] == "delivery"
    assert report["moves"] == 0

    # ---- outcomes (§6) reached the return run (§5.5).
    assert report["outcomes"]["Rejected"] + report["outcomes"]["Returned"] > 0
    assert report["return_stops"] > 0


async def test_the_return_run_goes_over_the_queue(client, pool, day):
    """§5.5 as a §13.2 resource, with its §7.1 list attached (§13.1)."""
    sites = {r.customer_id: r for r in day.requests}
    envelopes = [{"package_id": e.package_id, "customer_id": e.customer_id,
                  "previous_outcome": str(e.previous_outcome),
                  "facility_id": "HUB", "weight_g": e.weight_g,
                  "customer_lat": sites[e.customer_id].lat,
                  "customer_lon": sites[e.customer_id].lon,
                  "sla_date": e.sla_date.isoformat()}
                 for e in peak_day.returns_pool()]
    size = 31
    accepted = await client.post("/returns/runs", json={
        "envelopes": envelopes,
        "hub": {"id": "HUB", "lat": day.facilities[0].lat,
                "lon": day.facilities[0].lon,
                "shift_start": 17 * HOUR, "shift_end": 21 * HOUR},
        "vehicle_ids": ["VAN-01", "VAN-02"],
        "vehicle_fixed_cost": 500,
        "matrix": {"durations": [[0 if i == j else 120 for j in range(size)]
                                 for i in range(size)],
                   "distances": [[0 if i == j else 700 for j in range(size)]
                                 for i in range(size)]}})
    assert accepted.status_code == 202
    await drain(pool)

    state = (await client.get(accepted.json()["poll"])).json()
    assert state["status"] == "complete"
    assert len(state["result"]["stops"]) == peak_day.RETURN_SITES == 30
    assert state["result"]["violations"] == [], "§7.1 clean"
    assert state["violations"] == [], "and reported on the job (§13.1)"


async def test_the_pickup_worker_publishes_the_plan_the_endpoint_reads(
        client, store, payload):
    """§13.3's first process, feeding `GET /pickups/plan` (§13.2)."""
    hub = {"id": "HUB", "lat": 9.9333, "lon": -84.0833}
    requests = payload["requests"][:20]
    matrix = fake_matrix([hub, *requests])
    plan = workers.reoptimise_pickups(store, {
        "cycle": 1,
        "requests": requests,
        "vans": [v for v in payload["vans"] if v["role"] == "pickup"],
        "hub": hub,
        "matrix": {"durations": [list(r) for r in matrix.durations],
                   "distances": [list(r) for r in matrix.distances]}})
    assert plan["visits"] > 0

    published = (await client.get("/pickups/plan")).json()
    assert published["cycle"] == 1
    assert published["routes"] == plan["routes"]
    assert sum(len(r) for r in published["routes"].values()) == plan["visits"]
