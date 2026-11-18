"""The message bus between desks (SPEC "Desks as services on a message bus").

`BusClient` is what a desk uses; `MemoryBus` (tests, one process) and
`NatsBus` (NATS JetStream) are the transports. `NatsBus` is imported from
`firm.bus.nats` so code that never talks to a server doesn't load nats-py.
"""

from firm.bus.client import BusClient, BusError, Transport, decode, encode
from firm.bus.memory import MemoryBus, subject_matches

__all__ = [
    "BusClient",
    "BusError",
    "MemoryBus",
    "Transport",
    "decode",
    "encode",
    "subject_matches",
]
