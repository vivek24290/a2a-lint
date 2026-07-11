"""a2a-lint — conformance tooling for A2A (Agent2Agent) agents.

Shared core used by the CLI, the GitHub Action, the playground server,
and the a2a-watch monitor probes.
"""
__version__ = "0.1.0"

from a2a_lint.client import A2AClient, A2AProbeError, build_rpc_request
from a2a_lint.validator import summarize, validate_card

__all__ = [
    "A2AClient",
    "A2AProbeError",
    "build_rpc_request",
    "summarize",
    "validate_card",
    "__version__",
]
