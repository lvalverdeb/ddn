"""§13's service interface.

Thin handlers over the modules of §5. Nothing in this package decides anything
about the operation: it accepts inputs, starts jobs, reports status, returns
results, and refuses what §5.2.6 does not allow.
"""

from ddn.api.app import create_app

__all__ = ["create_app"]
