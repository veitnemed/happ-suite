"""Read-only evidence that Windows routes traffic through the HAPP tunnel."""

import ctypes
import socket
import struct
import sys
from dataclasses import dataclass


class _Ipv4Route(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint32)
        for name in (
            "destination", "mask", "policy", "next_hop", "interface_index",
            "route_type", "protocol", "age", "next_hop_as", "metric1",
            "metric2", "metric3", "metric4", "metric5",
        )
    ]


@dataclass(frozen=True)
class TunnelRoute:
    interface_index: int | None
    interface_alias: str | None
    through_happ: bool


def best_route_to(destination: str = "1.1.1.1") -> TunnelRoute:
    """Return Windows' selected IPv4 interface for a public destination.

    GetBestRoute and the interface conversion APIs only read the routing table.
    A listening HAPP proxy alone is not evidence of a system VPN route.
    """
    if sys.platform != "win32":
        return TunnelRoute(None, None, False)

    api = ctypes.windll.iphlpapi
    get_route = api.GetBestRoute
    get_route.argtypes = (ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(_Ipv4Route))
    get_route.restype = ctypes.c_uint32
    index_to_luid = api.ConvertInterfaceIndexToLuid
    index_to_luid.argtypes = (ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint64))
    index_to_luid.restype = ctypes.c_uint32
    luid_to_alias = api.ConvertInterfaceLuidToAlias
    luid_to_alias.argtypes = (ctypes.POINTER(ctypes.c_uint64), ctypes.c_wchar_p, ctypes.c_size_t)
    luid_to_alias.restype = ctypes.c_uint32

    route = _Ipv4Route()
    address = struct.unpack("<I", socket.inet_aton(destination))[0]
    if get_route(address, 0, ctypes.byref(route)):
        return TunnelRoute(None, None, False)

    interface_index = int(route.interface_index)
    luid = ctypes.c_uint64()
    if index_to_luid(interface_index, ctypes.byref(luid)):
        return TunnelRoute(interface_index, None, False)
    alias = ctypes.create_unicode_buffer(257)
    if luid_to_alias(ctypes.byref(luid), alias, len(alias)):
        return TunnelRoute(interface_index, None, False)

    name = alias.value
    return TunnelRoute(interface_index, name, name.casefold().startswith("happ-"))
