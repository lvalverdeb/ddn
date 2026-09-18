"""§13.1's three promises: idempotency, event validation, and jobs.

Each is a rule a caller relies on rather than a feature they see, which makes
them exactly the rules worth testing directly.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from ddn.api.store import Store
from ddn.model import Status
from tests.api_harness import drain, make_app, make_pool


def envelope(package_id: str = "PKG-1", **over) -> dict:
    return {"package_id": package_id, "customer_id": "CUST-001",
            "recipient_id": "RCPT-1", "package_type": "finished",
            "mailbag_id": "BAG-0001", "status": "Requested",
            "lat": 9.94, "lon": -84.08, "coord_source": "actual",
            "geocode_confidence": "high", "facility_id": "HUB",
            "priority": 1500.0, "sla_date": "2026-09-20", **over}


@pytest.fixture
def pool():
    return make_pool()


@pytest.fixture
def store():
    return Store()


@pytest.fixture
async def client(pool, store):
    app = make_app(pool, store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://api") as c:
        yield c


# ------------------------------------------------------------- idempotency

async def test_ingest_requires_an_idempotency_key(client):
    """§13.1 requires it on ingest and event endpoints."""
    response = await client.post("/envelopes/batch",
                                 json={"envelopes": [envelope()]})
    assert response.status_code == 400
    assert "Idempotency-Key" in response.json()["title"]


async def test_a_replayed_ingest_returns_the_original_response(client, store):
    """A driver app retrying must not create the envelope twice."""
    body = {"envelopes": [envelope("PKG-1"), envelope("PKG-2")]}
    headers = {"Idempotency-Key": "abc-123"}

    first = await client.post("/envelopes/batch", json=body, headers=headers)
    assert first.status_code == 202
    assert first.json()["accepted"] == 2

    again = await client.post("/envelopes/batch",
                              json={"envelopes": [envelope("PKG-9")]},
                              headers=headers)
    assert again.status_code == first.status_code
    assert again.json() == first.json(), "the original response, not a new one"
    assert again.headers.get("Idempotent-Replay") == "true"
    assert "PKG-9" not in store.envelopes, "the replay did not act"


async def test_a_replayed_outcome_is_not_recorded_twice(client, store):
    """§11's rates are wrong for the day if one delivery counts twice."""
    await client.post("/envelopes/batch", json={"envelopes": [envelope()]},
                      headers={"Idempotency-Key": "ingest"})
    store.envelopes["PKG-1"]["status"] = str(Status.DISPATCHED)

    headers = {"Idempotency-Key": "outcome-1"}
    body = {"event": "Delivered", "actor": "driver-7"}
    first = await client.post("/envelopes/PKG-1/events", json=body,
                              headers=headers)
    again = await client.post("/envelopes/PKG-1/events", json=body,
                              headers=headers)

    assert first.status_code == 200
    assert again.json() == first.json()
    assert len([a for a in store.audit if a.action == "event:Delivered"]) == 1


async def test_different_keys_are_different_calls(client, store):
    for key in ("k1", "k2"):
        await client.post("/envelopes/batch",
                          json={"envelopes": [envelope(f"PKG-{key}")]},
                          headers={"Idempotency-Key": key})
    assert {"PKG-k1", "PKG-k2"} <= set(store.envelopes)


# --------------------------------------------------------- §5.2.6 at the edge

async def test_an_illegal_transition_is_409_with_the_current_state(client, store):
    """§13.1: the state machine validates; §5.2.6 draws no Requested->Delivered."""
    await client.post("/envelopes/batch", json={"envelopes": [envelope()]},
                      headers={"Idempotency-Key": "ingest"})

    response = await client.post("/envelopes/PKG-1/events",
                                 json={"event": "Delivered", "actor": "ops"},
                                 headers={"Idempotency-Key": "bad-move"})
    assert response.status_code == 409
    body = response.json()
    assert body["current_state"] == "Requested"
    assert body["package_id"] == "PKG-1"
    assert "§5.2.6" in body["detail"]
    assert "Collected" in body["detail"], "it names the way out"
    assert store.envelopes["PKG-1"]["status"] == "Requested", "nothing moved"


async def test_a_legal_transition_is_accepted(client, store):
    await client.post("/envelopes/batch", json={"envelopes": [envelope()]},
                      headers={"Idempotency-Key": "ingest"})
    response = await client.post("/envelopes/PKG-1/events",
                                 json={"event": "Collected", "actor": "driver-7"},
                                 headers={"Idempotency-Key": "ok-move"})
    assert response.status_code == 200
    assert store.envelopes["PKG-1"]["status"] == "Collected"


async def test_a_refused_transition_leaves_no_audit_trail(client, store):
    await client.post("/envelopes/batch", json={"envelopes": [envelope()]},
                      headers={"Idempotency-Key": "ingest"})
    await client.post("/envelopes/PKG-1/events",
                      json={"event": "Delivered", "actor": "ops"},
                      headers={"Idempotency-Key": "bad"})
    assert list(store.audit_for("PKG-1")) == []


# -------------------------------------------------------------- §13.1's jobs

async def test_a_solver_endpoint_returns_202_and_a_job_id(client):
    response = await client.post("/allocation/runs", json={
        "day": "2026-09-17", "pools": {"HUB": 100, "D1": 50},
        "bikes": [f"MOTO-{n:03d}" for n in range(1, 11)]})
    assert response.status_code == 202
    body = response.json()
    assert body["job_id"]
    assert body["poll"] == f"/allocation/runs/{body['job_id']}"


async def test_a_job_runs_on_the_queue_and_reports_its_result(client, pool):
    """Real Arq: enqueued, executed by a worker, read back from arq's records."""
    accepted = (await client.post("/allocation/runs", json={
        "day": "2026-09-17", "pools": {"HUB": 100, "D1": 50},
        "bikes": [f"MOTO-{n:03d}" for n in range(1, 11)]})).json()

    pending = (await client.get(accepted["poll"])).json()
    assert pending["status"] in {"queued", "deferred", "JobStatus.queued"}

    await drain(pool)

    done = (await client.get(accepted["poll"])).json()
    assert done["status"] == "complete"
    assert sum(done["result"]["targets"].values()) == 10
    assert done["violations"] == []


async def test_the_audit_records_actor_and_time_on_a_lock(client, store, pool):
    """§13.1: "Overrides (§8.2) and outcome events record the actor and time"."""
    await client.post("/envelopes/batch", json={"envelopes": [envelope()]},
                      headers={"Idempotency-Key": "ingest"})
    response = await client.post("/routes/runs/run-1/locks", json={
        "package_id": "PKG-1", "locked_vehicle_id": "MOTO-050",
        "actor": "ops-anna"})

    assert response.status_code == 202, "§8.2 re-runs, so it is a job"
    assert store.envelopes["PKG-1"]["locked_vehicle_id"] == "MOTO-050"
    entry = next(store.audit_for("PKG-1"))
    assert entry.actor == "ops-anna"
    assert entry.action == "lock"
    assert entry.at is not None


async def test_the_pickup_plan_is_empty_until_the_worker_has_run(client):
    """§13.3 publishes it on a cadence; before the first cycle there is none."""
    plan = (await client.get("/pickups/plan")).json()
    assert plan["cycle"] is None
    assert plan["routes"] == {}


async def test_capabilities_report_what_was_measured(client):
    body = (await client.get("/solver/capabilities")).json()
    assert body["flexible_vehicle_to_depot_assignment"] is False
    assert body["dynamic_stop_insertion"] is True
    assert body["source"] == "docs/solver-capabilities.md"
