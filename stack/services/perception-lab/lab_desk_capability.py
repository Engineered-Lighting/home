"""Server-installed desk evidence interface; an implementation is not qualification.

This interface never creates a camera/model owner. Runtime configuration contains
only commissioned pins; operator/browser JSON cannot supply these methods.
"""
from abc import ABC, abstractmethod


class TrustedDeskCapability(ABC):
    """Bounded, side-effect-free lookup of immutable independently qualified inputs.

    ``select`` receives a frozen descriptor of an actual admitted Lab capture.
    It returns either ``{status: selected, anchor_ref: OriginalReceiptRef}`` or
    ``{status: unavailable, reason: bounded_code}``. It cannot qualify a frame,
    create identity or derive stereo geometry under the RGB-only desk method.
    ``resolve_inputs`` is pure in-memory resolution of the publisher's exact
    INPUT_KEYS. ``quiesce`` prevents new selection and reports in-flight work.
    """

    @abstractmethod
    def binding(self):
        """Return exact scope, desk_policy_digest and lab_binding_digest."""

    @abstractmethod
    def select(self, descriptor):
        """Select an applicable qualified original, or explicitly unavailable."""

    @abstractmethod
    def resolve_inputs(self, anchor_ref):
        """Resolve immutable full originals; no network, disk or side effects."""

    @abstractmethod
    def quiesce(self):
        """Return exact status (quiescent/unconfirmed) and in_flight boolean."""
