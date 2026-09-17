"""The Document Delivery Network, planned on the `vrp` platform.

The operation is specified in `docs/vrp_problem_definition.md`: a consolidation
hub and six secondary depots, a shared fleet of motorbikes and vans, 2,500-5,000
document envelopes a day at ten minutes of service each, and a daily decision
about which packages cannot be served.

**This package is downstream of the platform and of the gateway.** It depends on
`vrp-platform` and it calls the OSRM gateway for travel matrices; neither knows
this repository exists, and nothing here may require them to. What lives here is
what the platform deliberately does not model -- the outcome lifecycle that
carries a package across a day boundary (§6), the mapping from the operation's
own data contract to a `Problem` (§9.1), the cut-off partition (§5.2) and the
daily fleet distribution (§4.2).

Delivery models live here too, not upstream: they describe *this* operation, and
are read by path from `models/` -- this repository ships the files and knows
where they are. `VRP_MODEL_PATH` is how the platform's own tooling finds models
it did not ship, which is a different problem from this one.
"""
