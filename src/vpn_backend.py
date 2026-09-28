"""Backend-neutral VPN contracts and typed errors."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, FrozenSet, Protocol, runtime_checkable


class Capability(str, Enum):
    START = "start"
    STOP = "stop"
    VERIFY = "verify"
    TUN = "tun"
    SELECT_NODE = "select_node"
    PROVIDERS = "providers"
    LATENCY_TEST = "latency_test"
    LIVE_RELOAD = "live_reload"
    PRIVILEGED_HELPER = "privileged_helper"


class VpnBackendError(RuntimeError):
    """Base class for safe, user-presentable VPN backend failures."""


class BinaryIntegrityError(VpnBackendError):
    pass


class ConfigValidationError(VpnBackendError):
    pass


class ApiUnavailableError(VpnBackendError):
    pass


class ApiAuthError(VpnBackendError):
    pass


class ProviderFormatError(VpnBackendError):
    pass


class TunStartTimeout(VpnBackendError):
    pass


class OwnershipConflictError(VpnBackendError):
    pass


class NetworkUnavailableError(VpnBackendError):
    pass


class SubscriptionError(VpnBackendError):
    pass


@dataclass(frozen=True)
class BackendObservation:
    state: str
    ownership: str
    pid: int | None = None
    version: str | None = None
    detail: dict[str, Any] | None = None


@runtime_checkable
class VPNBackend(Protocol):
    capabilities: FrozenSet[Capability]

    def detect(self) -> BackendObservation: ...
    def backend_observation(self) -> BackendObservation: ...
    def start(self) -> bool: ...
    def stop(self) -> bool: ...
    def verify(self) -> bool: ...
    def recover(self) -> bool: ...
