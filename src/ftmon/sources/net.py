"""Socket sampler (SA-04, SPEC 7.7.7): listener presence + connection counts.

Two kinds of entities from one psutil.net_connections pass:

- `totals` — one always-present entity with per-state connection counts,
  the thing the baseline-relative conn-spike rule watches. Aggregate only:
  per-process attribution is deliberately out of v1 (NG-06 — it needs root
  and a per-connection process walk that violates the CPU budget).
- one synthetic watchlist entity per expected listener (`tcp:22`), with
  present 0/1 — same inversion as the unit sampler: absence alerts.

Unprivileged callers may not see other users' sockets on some systems
(psutil AccessDenied): counts are then a lower bound over what is visible.
Listening sockets of *this* user — the watchlist case on a desktop — are
always visible, so present/absent stays trustworthy; documented rather than
worked around (needing root would violate the deployment model, SE-01).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

import psutil

from ftmon.clock import Clock
from ftmon.model import EntitySample, Snapshot, SourceDecl
from ftmon.sources.base import SOURCE_DECLS


class NetSampler:
    decl: ClassVar[SourceDecl] = SOURCE_DECLS["net"]

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def sample(self, now: float, deadline_mono: float, options: Mapping) -> Snapshot:
        try:
            conns = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, OSError):
            conns = []  # totals become 0-lower-bounds; watchlist goes absent->0

        established = time_wait = listen_count = 0
        tcp_listeners: set[int] = set()
        udp_ports: set[int] = set()
        for c in conns:
            if c.status == psutil.CONN_ESTABLISHED:
                established += 1
            elif c.status == psutil.CONN_TIME_WAIT:
                time_wait += 1
            elif c.status == psutil.CONN_LISTEN:
                listen_count += 1
                if c.laddr:
                    tcp_listeners.add(c.laddr.port)
            if c.type == 2 and c.laddr:  # SOCK_DGRAM: "bound" is UDP's listen
                udp_ports.add(c.laddr.port)

        entities = [EntitySample(
            entity_id="totals",
            attrs={"proto": "all", "port": ""},
            metrics={
                "conn_total": float(len(conns)),
                "conn_established": float(established),
                "conn_time_wait": float(time_wait),
                "conn_listen": float(listen_count),
            },
        )]

        for item in options.get("watchlist", ()):
            if not isinstance(item, Mapping) or "listen" not in item:
                continue
            proto, _, port_s = str(item["listen"]).partition(":")
            try:
                port = int(port_s)
            except ValueError:
                continue  # a broken entry must not kill the pass (PL-03)
            up = port in (udp_ports if proto == "udp" else tcp_listeners)
            entities.append(EntitySample(
                entity_id=f"{proto}:{port}",
                attrs={"proto": proto, "port": str(port)},
                metrics={"present": 1.0 if up else 0.0},
            ))

        return Snapshot(source=self.decl.name, ts=now, entities=tuple(entities))
