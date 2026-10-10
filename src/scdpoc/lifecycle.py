"""Issuance lifecycle state model (profile 0.3.0, LIF-09 and the lifecycle state-transition table).

This table is the executable form of the normative table in the profile. The demonstrator refuses any
transition that is not listed. "Retained historical" is not a state: every state except ``issued`` is a
historical state, retained and retrievable by identifier for the retention period, but never returned as
the current patient summary (LIF-10).
"""
from __future__ import annotations

from dataclasses import dataclass

ISSUED = "issued"
REPLACED_UPDATE = "replaced-for-update"
REPLACED_CORRECTION = "replaced-for-correction"
WITHDRAWN = "withdrawn"

STATES = {
    ISSUED: {"registry": "Approved", "current": True, "retrievable": True},
    REPLACED_UPDATE: {"registry": "Deprecated", "current": False, "retrievable": True},
    REPLACED_CORRECTION: {"registry": "Deprecated", "current": False, "retrievable": True},
    WITHDRAWN: {"registry": "Deprecated", "current": False, "retrievable": True},
}


@dataclass(frozen=True)
class Transition:
    action: str
    source: str | None          # None: the issuance does not exist yet
    target: str
    initiator: str
    metadata: str
    notify: bool


TRANSITIONS = [
    Transition("issue", None, ISSUED, "Issuer (gate LIF-05), Publisher",
               "ITI-41: projection and envelope DocumentEntries Approved; XFRM envelope to projection", False),
    Transition("replace-for-update", ISSUED, REPLACED_UPDATE, "Issuer, Publisher",
               "ITI-41 of the new issuance with RPLC to the projection; the registry deprecates the projection "
               "and its XFRM envelope", False),
    Transition("replace-for-correction", ISSUED, REPLACED_CORRECTION, "Issuer, Publisher",
               "As replace-for-update; new Composition.status amended; reason correction recorded", True),
    Transition("withdraw", ISSUED, WITHDRAWN, "Issuer, as Document Administrator",
               "ITI-57 UpdateAvailabilityStatus Approved to Deprecated for both DocumentEntries; no replacement",
               True),
]


class LifecycleError(Exception):
    pass


def transition(current: str | None, action: str) -> Transition:
    for t in TRANSITIONS:
        if t.action == action and t.source == current:
            return t
    allowed = [t.action for t in TRANSITIONS if t.source == current]
    raise LifecycleError(f"transition {action!r} is not allowed from state {current or 'none'!r}; "
                         f"allowed: {allowed or 'none (terminal state)'}")


def table() -> list[dict]:
    return [{"action": t.action, "from": t.source or "-", "to": t.target, "initiator": t.initiator,
             "metadata": t.metadata, "notifyKnownRecipients": t.notify,
             "originalRetrievable": STATES[t.target]["retrievable"] if t.source else None,
             "originalCurrent": STATES[t.target]["current"] if t.source else None} for t in TRANSITIONS]
