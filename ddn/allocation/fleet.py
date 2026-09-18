"""§4.2's first half: dividing a fixed fleet between facilities.

`docs/solver-capabilities.md` settled the shape. A vehicle's depot is fixed
before the solve -- `Vehicle.start_location_id` is a required string, and the
one lock that mentions a depot pins an *order* to one -- so §4.2's "integrated"
option cannot be expressed and the two-stage approach the document already
recommends is the only one available. This is that first stage: it runs before
any routing and decides what each facility has to route with.
"""

from __future__ import annotations

from ddn import assumptions

#: §4.2's planning figure: "~25 envelopes per motorbike per day". §7.4 is where
#: it comes from -- 35 envelopes times ten minutes is 350 against an 8-hour
#: shift, so the box is never what binds.
EFFECTIVE_PER_BIKE = assumptions.ENVELOPES_PER_BIKE


def allocate(pools: dict[str, int], bikes: int) -> dict[str, int]:
    """§4.2's two-stage first half: divide a fixed fleet between facilities.

    Largest-remainder, so the fleet is neither over- nor under-committed: the
    obvious `round()` per facility can allocate more bikes than exist, which is
    a plan nobody can run.

    A facility with any demand at all gets at least one bike. Zero would leave
    its envelopes unserved with bikes idle elsewhere, and §8's first objective
    is delivered work rather than tidy arithmetic.
    """
    demand = sum(pools.values())
    if not demand:
        return {facility: 0 for facility in pools}

    exact = {f: n / demand * bikes for f, n in pools.items()}
    floors = {f: max(1, int(share)) if pools[f] else 0
              for f, share in exact.items()}
    spare = bikes - sum(floors.values())
    for facility in sorted(exact, key=lambda f: exact[f] - int(exact[f]),
                           reverse=True):
        if spare <= 0:
            break
        if pools[facility]:
            floors[facility] += 1
            spare -= 1
    return floors
