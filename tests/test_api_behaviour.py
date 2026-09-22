"""§13.1's three promises: idempotency, event validation, and jobs.

Each is a rule a caller relies on rather than a feature they see, which makes
them exactly the rules worth testing directly.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from ddn import returns
from ddn.api import jobs
from ddn.api.idempotency import PREFIX, Replays, once
from ddn.api.store import Store
from ddn.model import Status
from ddn.solver_adapter import postcheck
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
    assert done["violations"] is None, (
        "§4.2 produces no routes, so §13.1 asks it for no list — an empty "
        "one would be the clean bill allocation_plan stopped giving")


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


async def test_operations_can_force_an_envelope_out_and_it_is_audited(client, store):
    """§8.2's other half, over the same endpoint as the lock.

    Half of §8.2 had no representation at all: `Lock` required a
    `locked_vehicle_id`, so the body could only ever say "in". The envelope is
    marked rather than locked -- see §8.2 -- and the audit says `exclude`, not
    `lock`, because an operator reading the trail needs to know which way the
    override went.
    """
    await client.post("/envelopes/batch", json={"envelopes": [envelope()]},
                      headers={"Idempotency-Key": "ingest"})
    response = await client.post("/routes/runs/run-1/locks", json={
        "package_id": "PKG-1", "excluded_by_ops": True, "actor": "ops-anna"})

    assert response.status_code == 202, "§8.2 re-runs, so it is a job"
    assert store.envelopes["PKG-1"]["excluded_by_ops"] is True
    assert store.envelopes["PKG-1"]["locked_vehicle_id"] is None
    entry = next(store.audit_for("PKG-1"))
    assert (entry.actor, entry.action) == ("ops-anna", "exclude")
    assert entry.at is not None


@pytest.mark.parametrize("body, why", [
    ({"package_id": "PKG-1", "actor": "ops-anna"}, "neither direction"),
    ({"package_id": "PKG-1", "locked_vehicle_id": "MOTO-050",
      "excluded_by_ops": True, "actor": "ops-anna"}, "both at once"),
])
async def test_an_override_that_names_no_single_direction_is_refused(
        client, body, why):
    """§8.2 forces an envelope in *or* out. A body saying both is asking for an
    envelope pinned to a vehicle it is also withheld from, and a body saying
    neither is a re-run wearing an override's clothes. Refused at the schema so
    no handler has to invent a precedence."""
    await client.post("/envelopes/batch", json={"envelopes": [envelope()]},
                      headers={"Idempotency-Key": "ingest"})

    response = await client.post("/routes/runs/run-1/locks", json=body)

    assert response.status_code == 422, why


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


# ------------------------------------------------- §13.1's records are durable

async def test_a_replay_survives_the_process_that_answered_it(pool):
    """The records were a dict on the Store, so a restart dropped them.

    A driver app retrying across a deploy then acted twice. Here the second app
    shares only the Redis — a different `Store`, different state, as two API
    containers are — and still answers with the first one's response.
    """
    body = {"envelopes": [envelope("PKG-1")]}
    headers = {"Idempotency-Key": "survives"}

    first_store = Store()
    async with AsyncClient(transport=ASGITransport(app=make_app(pool, first_store)),
                           base_url="http://api") as first:
        original = await first.post("/envelopes/batch", json=body, headers=headers)
    assert original.status_code == 202
    assert "PKG-1" in first_store.envelopes

    second_store = Store()
    async with AsyncClient(transport=ASGITransport(app=make_app(pool, second_store)),
                           base_url="http://api") as second:
        again = await second.post("/envelopes/batch", json=body, headers=headers)

    assert again.status_code == original.status_code
    assert again.json() == original.json()
    assert again.headers.get("Idempotent-Replay") == "true"
    assert second_store.envelopes == {}, "the replay did not act on this one"


async def test_a_call_still_in_flight_is_refused_rather_than_repeated(client, pool):
    """A retry after a timeout is often concurrent with the original.

    The first call was slow, not dead. Checking "have I seen this key?" and
    then acting is a race that loses exactly when it is most needed, so the key
    is claimed before the work runs.
    """
    replays = Replays(pool)
    assert await replays.claim("in-flight-key") is None, "claimed by the first call"

    response = await client.post("/envelopes/batch",
                                 json={"envelopes": [envelope()]},
                                 headers={"Idempotency-Key": "in-flight-key"})
    assert response.status_code == 409
    body = response.json()
    assert body["title"] == "request in flight"
    assert "still being processed" in body["detail"]


async def test_a_failed_call_does_not_poison_its_key(pool):
    """A request that raised has not happened, so the key must stay usable."""
    replays = Replays(pool)

    def explode() -> dict:
        raise RuntimeError("the hub caught fire")

    with pytest.raises(RuntimeError):
        await once(replays, "released", 200, explode)

    assert await replays.claim("released") is None, "the claim was released"


async def test_records_expire(pool):
    """Keys are remembered long enough to cover a retry, not for ever."""
    replays = Replays(pool, ttl=60)
    await replays.claim("ttl-key")
    await replays.complete("ttl-key", status_code=200, body={"ok": True})
    remaining = await pool.ttl(f"{PREFIX}ttl-key")
    assert 0 < remaining <= 60


# ----------------------------------------------------- §13.1's worker settings

def test_the_worker_connects_where_the_api_enqueues(monkeypatch):
    """`arq ddn.api.jobs.WorkerSettings` must read `DDN_REDIS_DSN`.

    It did not. arq falls back to its own default -- localhost -- so a worker in
    a container crash-looped against nothing while the API enqueued happily to
    the real Redis: jobs accepted, `202` returned, and nobody ever running them.
    Nothing in this suite noticed, because the tests supply their own pool and
    their own worker; it took a `compose up` to see it.
    """
    import importlib

    monkeypatch.setenv("DDN_REDIS_DSN", "redis://redis:6379/2")
    module = importlib.reload(jobs)
    try:
        assert module.WorkerSettings.redis_settings.host == "redis"
        assert module.WorkerSettings.redis_settings.port == 6379
        assert module.WorkerSettings.redis_settings.database == 2
        assert module.WorkerSettings.functions == module.FUNCTIONS
    finally:
        monkeypatch.delenv("DDN_REDIS_DSN")
        importlib.reload(module)


def test_the_worker_falls_back_to_localhost_for_a_developer():
    """Unset is a developer running `make worker` beside `make redis`."""
    assert jobs.redis_settings().host == "localhost"


# ------------------------------------------------------- §13.2's transfers

async def test_a_transfer_is_raised_and_then_arrives(client, store):
    """§13.2's three events, and §5.2.6 validating each move."""
    await client.post("/envelopes/batch", json={"envelopes": [envelope()]},
                      headers={"Idempotency-Key": "ingest"})
    store.envelopes["PKG-1"]["status"] = str(Status.POSTPONED)
    store.envelopes["PKG-1"]["facility_id"] = "D1"

    raised = await client.post("/transfers", headers={"Idempotency-Key": "t1"},
                               json={"transfer_id": "T1", "package_id": "PKG-1",
                                     "from_facility_id": "D1",
                                     "to_facility_id": "D3",
                                     "reason": "misassignment",
                                     "deadline": "2026-09-17T07:00:00",
                                     "actor": "ops-anna"})
    assert raised.status_code == 201
    assert raised.json()["status"] == "Transfer requested"

    for event, key, expected in (("loaded", "e1", "In transfer"),
                                 ("arrived", "e2", "Ready")):
        moved = await client.post("/transfers/T1/events",
                                  json={"event": event, "actor": "driver-2"},
                                  headers={"Idempotency-Key": key})
        assert moved.status_code == 200
        assert moved.json()["status"] == expected

    assert store.envelopes["PKG-1"]["facility_id"] == "D3", (
        "§7.1: the envelope moves depot when it arrives, not when it is loaded")


async def test_a_transfer_event_out_of_order_is_refused(client, store):
    """§5.2.6 draws no Transfer requested -> Ready by way of `arrived`."""
    await client.post("/envelopes/batch", json={"envelopes": [envelope()]},
                      headers={"Idempotency-Key": "ingest"})
    store.envelopes["PKG-1"]["status"] = str(Status.POSTPONED)
    await client.post("/transfers", headers={"Idempotency-Key": "t1"},
                      json={"transfer_id": "T1", "package_id": "PKG-1",
                            "from_facility_id": "D1", "to_facility_id": "D3",
                            "reason": "address_correction",
                            "deadline": "2026-09-17T07:00:00",
                            "actor": "ops"})

    refused = await client.post("/transfers/T1/events",
                                json={"event": "arrived", "actor": "driver-2"},
                                headers={"Idempotency-Key": "bad"})
    assert refused.status_code == 409
    assert refused.json()["current_state"] == "Transfer requested"


async def test_transfers_can_be_listed_by_status(client, store):
    await client.post("/envelopes/batch", json={"envelopes": [envelope()]},
                      headers={"Idempotency-Key": "ingest"})
    store.envelopes["PKG-1"]["status"] = str(Status.POSTPONED)
    await client.post("/transfers", headers={"Idempotency-Key": "t1"},
                      json={"transfer_id": "T1", "package_id": "PKG-1",
                            "from_facility_id": "D1", "to_facility_id": "D3",
                            "reason": "rebalancing",
                            "deadline": "2026-09-17T07:00:00", "actor": "ops"})

    every = (await client.get("/transfers")).json()
    assert every["count"] == 1
    requested = (await client.get("/transfers",
                                  params={"status": "Transfer requested"})).json()
    assert requested["count"] == 1
    assert (await client.get("/transfers",
                             params={"status": "Ready"})).json()["count"] == 0


async def test_raising_a_transfer_records_who_and_why(client, store):
    """§13.1: overrides record the actor and the time."""
    await client.post("/envelopes/batch", json={"envelopes": [envelope()]},
                      headers={"Idempotency-Key": "ingest"})
    store.envelopes["PKG-1"]["status"] = str(Status.POSTPONED)
    await client.post("/transfers", headers={"Idempotency-Key": "t1"},
                      json={"transfer_id": "T1", "package_id": "PKG-1",
                            "from_facility_id": "D1", "to_facility_id": "D3",
                            "reason": "address_correction",
                            "deadline": "2026-09-17T07:00:00",
                            "actor": "ops-anna"})
    entry = next(e for e in store.audit_for("PKG-1")
                 if e.action == "transfer:raised")
    assert entry.actor == "ops-anna"
    assert entry.detail["reason"] == "address_correction"


# --------------------------------------------- §13.1's violation list (§7.1)


def test_the_linehaul_endpoint_reports_an_overloaded_leg():
    """§13.1: "Every routing result carries its §7.1 violation list".

    This endpoint returned `[]` as a literal — a clean §7.1 bill for a plan
    nothing had read. The combined-load bullet is a property of a leg, and a
    line-haul plan is made of legs, so it was the one check most obviously
    owed and least obviously missing.

    Three envelopes at 200 kg are 600 kg on one leg, against §7.1's 500.
    """
    from ddn.api import runner

    result = runner.linehaul_plan({
        "day": "2026-09-16",
        "facilities": [{"id": "D1", "route_release_time": 7 * 3600,
                        "transit_from_hub_min": 30}],
        "envelopes": [{"package_id": f"P{n}", "facility_id": "D1",
                       "expected_ready_at": 0, "weight_g": 200_000}
                      for n in range(3)],
        "vans": [{"vehicle_id": "VAN-01"}],
        "unload_seconds": 1800,
    })

    assert [v["bullet"] for v in result["violations"]] == [postcheck.COMBINED_LOAD]
    assert "600000" in result["violations"][0]["detail"]


def test_a_linehaul_plan_within_its_limits_reports_nothing():
    """Empty has to mean checked and clean, or the list says nothing at all."""
    from ddn.api import runner

    result = runner.linehaul_plan({
        "day": "2026-09-16",
        "facilities": [{"id": "D1", "route_release_time": 7 * 3600,
                        "transit_from_hub_min": 30}],
        "envelopes": [{"package_id": "P1", "facility_id": "D1",
                       "expected_ready_at": 0}],
        "vans": [{"vehicle_id": "VAN-01"}],
        "unload_seconds": 1800,
    })

    assert result["violations"] == []
    assert result["declined"] == [], "§9.2's fourth clause, and nothing to say"


def test_the_allocation_result_claims_no_seven_one_check():
    """§4.2 is not a routing result, so §13.1 does not ask it for a list.

    Every one of §7.1's thirteen bullets predicates over a route, a load, a
    leg, a stop, a departure or a package. A fleet allocation produces none of
    those — it "is a planning decision made before routing". An empty list here
    would be the same false reassurance this endpoint's siblings just stopped
    giving, so there is no list.
    """
    from ddn.api import runner

    result = runner.allocation_plan(
        {"day": "2026-09-16", "pools": {"HUB": 10, "D1": 10},
         "bikes": ["M1", "M2"], "previous": {"M1": "D1", "M2": "D1"}})

    assert "violations" not in result
    assert result["moves"] >= 1, (
        "§7.2's relocation cost is what this stage does report, and both "
        "bikes started at D1")


# ------------------------------------ §6 v0.15's cancellation, over HTTP

async def _ready(client, package_id="PKG-CANCEL", status="Ready"):
    """One envelope in the store, in the status the test needs it in."""
    await client.post("/envelopes/batch",
                headers={"Idempotency-Key": f"ingest-{package_id}-{status}"},
                json={"envelopes": [envelope(package_id=package_id,
                                             status=status)]})
    return package_id


@pytest.mark.parametrize("status", ["Ready", "Dispatched"])
async def test_an_envelope_may_be_cancelled_from_either_side_of_dispatch(client,
                                                                   status):
    """§6 v0.15's two cases, over the event endpoint §13.2 now names.

    §13.1: status is never set directly, so a cancellation is an event naming
    the state it moves to — and `Return run` is reachable from Ready and, since
    v0.15, from Dispatched.
    """
    package_id = await _ready(client, f"PKG-C-{status}", status)

    answered = await client.post(
        f"/envelopes/{package_id}/events",
        headers={"Idempotency-Key": f"cancel-{status}"},
        json={"event": "Return run", "actor": "call-centre",
              "outcome": "Cancelled", "reason": "customer withdrew it"})

    assert answered.status_code == 200
    assert answered.json()["status"] == "Return run"


async def test_a_cancelled_envelope_is_recognised_by_the_return_run(client):
    """§5.5 carries it, which needs §6's word on the record and not §5.2.6's.

    `returns.goes_back` reads `previous_outcome`. Recording the *status* there
    left a cancelled envelope carrying "Return run", which the predicate does
    not recognise — so it reached §5.5 and was filtered straight back out:
    destroyed rather than returned, the same defect the `sla_expired` flag
    exists to prevent.
    """
    package_id = await _ready(client, "PKG-C-RETURNS")
    await client.post(f"/envelopes/{package_id}/events",
                headers={"Idempotency-Key": "cancel-returns"},
                json={"event": "Return run", "actor": "call-centre",
                      "outcome": "Cancelled"})

    stored = (await client.get(f"/envelopes/{package_id}")).json()

    assert stored["previous_outcome"] == "Cancelled"
    assert returns.goes_back(stored)


async def test_cancelling_a_delivered_envelope_is_refused_with_where_it_is(client):
    """e2e-3 §2.2: "if already visited and delivered, cancellation is refused".

    No rule of its own: §5.2.6 draws nothing out of Delivered, so `advance`
    refuses and §13.1's 409-with-current-state answers. A second rule here
    would be a second place for it to disagree with the diagram.
    """
    package_id = await _ready(client, "PKG-C-DONE", "Delivered")

    refused = await client.post(
        f"/envelopes/{package_id}/events",
        headers={"Idempotency-Key": "cancel-delivered"},
        json={"event": "Return run", "actor": "call-centre",
              "outcome": "Cancelled"})

    assert refused.status_code == 409
    body = refused.json()
    assert body["current_state"] == "Delivered"
    assert body["package_id"] == package_id
    assert "§5.2.6" in body["detail"]
