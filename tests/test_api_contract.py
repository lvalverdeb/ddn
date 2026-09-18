"""§13.2's table, and §9 as the published contract.

Two things are checked here that nothing else can check. That every endpoint
§13.2 lists exists, with the status codes §13.1 requires; and that the OpenAPI
document is §9 rather than a second schema that resembles it.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from ddn.api.schemas import Envelope as EnvelopeSchema
from ddn.api.schemas import Mailbag as MailbagSchema
from ddn.model import CoordSource, GeocodeConfidence, Outcome, Status
from ddn.model import Envelope as EnvelopeRecord
from ddn.model import Mailbag as MailbagRecord
from ddn.model import PickupRequest as PickupRecord
from tests.api_harness import make_app, make_pool

SPEC = (Path(__file__).resolve().parent.parent
        / "docs" / "vrp-problem-definition.md").read_text(encoding="utf-8")
# Split on the whole heading rather than on "## 14.", which also matches the
# first characters of a "### 14.x" subsection heading. §13's subsections were
# numbered that way until the spec was corrected, and the next section added
# below §13 could reintroduce it.
SECTION_13 = (SPEC.split("## 13. Service interface")[1]
              .split("\n## 14. Revision history")[0])

#: Every row of §13.2, as (method, path, expected status).
#: §13.1: solver-invoking endpoints are 202 with a job id.
ENDPOINTS = [
    ("post", "/envelopes/batch", 202),
    ("get", "/envelopes/{package_id}", 200),
    ("post", "/envelopes/{package_id}/events", 200),
    ("post", "/pickups", 201),
    ("post", "/pickups/{mailbag_id}/events", 200),
    ("get", "/pickups/plan", 200),
    ("post", "/processing/events", 200),
    ("get", "/processing/ready", 200),
    ("post", "/allocation/runs", 202),
    ("get", "/allocation/runs/{job_id}", 200),
    ("post", "/linehaul/plans", 202),
    ("get", "/linehaul/plans/{job_id}", 200),
    ("post", "/linehaul/plans/{job_id}/events", 200),
    ("post", "/routes/runs", 202),
    ("get", "/routes/runs/{job_id}", 200),
    ("post", "/routes/runs/{job_id}/locks", 202),
    ("post", "/returns/runs", 202),
    ("get", "/returns/runs/{job_id}", 200),
    ("post", "/simulation/days", 202),
    ("get", "/simulation/days/{job_id}", 200),
    ("get", "/metrics", 200),
    ("get", "/health", 200),
    ("get", "/solver/capabilities", 200),
]


@pytest.fixture(scope="module")
def spec():
    return make_app(make_pool()).openapi()


@pytest.mark.parametrize(("method", "path", "code"), ENDPOINTS,
                         ids=[f"{m.upper()} {p}" for m, p, _ in ENDPOINTS])
def test_every_section_13_2_endpoint_exists(spec, method, path, code):
    assert path in spec["paths"], f"§13.2 lists {path}"
    assert method in spec["paths"][path], f"§13.2 lists {method.upper()} {path}"
    responses = spec["paths"][path][method]["responses"]
    assert str(code) in responses, (
        f"{method.upper()} {path} should answer {code}; it documents "
        f"{sorted(responses)}")


def test_the_table_has_no_row_this_service_is_missing():
    """Read from §13.2 rather than from the list above, which could drift."""
    for path in ("/envelopes/batch", "/pickups", "/processing/events",
                 "/allocation/runs", "/linehaul/plans", "/routes/runs",
                 "/returns/runs", "/simulation/days", "/metrics", "/health",
                 "/solver/capabilities", "/pickups/plan", "/processing/ready"):
        assert path in SECTION_13, f"§13.2 no longer lists {path}"


def test_no_endpoint_is_offered_that_section_13_2_does_not_list(spec):
    served = {path for path in spec["paths"]}
    listed = {path for _, path, _ in ENDPOINTS}
    assert served == listed, f"undocumented: {sorted(served - listed)}"


def test_solver_endpoints_are_jobs_not_synchronous_calls(spec):
    """§13.1: "any endpoint that invokes the solver returns 202 Accepted"."""
    for path in ("/allocation/runs", "/linehaul/plans", "/routes/runs",
                 "/returns/runs", "/simulation/days"):
        assert "202" in spec["paths"][path]["post"]["responses"]
        assert "200" not in spec["paths"][path]["post"]["responses"]


# ------------------------------------------------------------------- §9

def test_the_envelope_schema_is_section_9_1s_fields_exactly():
    """§13.1: schemas are "shared with the internal model"."""
    assert set(EnvelopeSchema.model_fields) == {
        f.name for f in fields(EnvelopeRecord)}


def test_the_mailbag_schema_is_section_9_1s_fields_exactly():
    internal = ({f.name for f in fields(MailbagRecord)}
                | {f.name for f in fields(PickupRecord)}) - {"mailbags"}
    assert set(MailbagSchema.model_fields) == internal


def test_the_openapi_document_publishes_every_section_9_1_field(spec):
    published = set(spec["components"]["schemas"]["Envelope"]["properties"])
    assert published == {f.name for f in fields(EnvelopeRecord)}


@pytest.mark.parametrize("enum_type", [Status, CoordSource, GeocodeConfidence,
                                       Outcome],
                         ids=lambda e: e.__name__)
def test_the_openapi_document_publishes_the_model_enums(spec, enum_type):
    """The published values are the model's, not a parallel set."""
    schema = spec["components"]["schemas"][enum_type.__name__]
    assert set(schema["enum"]) == {member.value for member in enum_type}


def test_coord_source_keeps_the_schema_spelling_not_the_prose_one():
    """§9.1 underscores them; §3.2's hyphenated prose describes the same values."""
    assert {c.value for c in CoordSource} == {
        "actual", "geocoded_address", "zip_centroid"}


def test_section_13s_subsections_are_numbered_13_not_14():
    """They were 14.1-14.4, colliding with §14 Revision history.

    A copy-paste when §13 was inserted ahead of the old §13. It is the kind of
    defect that survives because nothing executes a heading — every reader
    understood which section they were in, and only a test that splits the file
    on section boundaries ever tripped over it.
    """
    for n, title in enumerate(("Principles", "Resources",
                               "Scheduled orchestration",
                               "Out of scope for the API"), start=1):
        assert f"### 13.{n} {title}" in SECTION_13
        assert f"### 14.{n} " not in SPEC, "§14 has no subsections"
