"""Read a live LE connection's RSSI with the raw HCI "Read RSSI" command (Linux only).

`btmgmt conn-info` asks the kernel for RSSI *and* transmit power; the Pi 3's Broadcom controller rejects the
transmit-power read on LE links, so the whole request fails with "Invalid Parameters". Sending only
HCI_Read_RSSI avoids that. Needs CAP_NET_RAW (granted by the systemd unit) or root.

Test on the Pi while the service is connected:
    sudo /opt/jkbms-pi/venv/bin/python -m jkbms_pi.hci_rssi c8:47:80:71:44:d4 [hci0]
"""

from __future__ import annotations

import fcntl
import select
import socket
import struct
import sys
import time

HCI_COMMAND_PKT = 0x01
HCI_EVENT_PKT = 0x04
EVT_CMD_COMPLETE = 0x0E
EVT_CMD_STATUS = 0x0F
OP_READ_RSSI = (0x05 << 10) | 0x0005  # OGF status params, OCF Read RSSI

HCIGETCONNLIST = 0x800448D4  # _IOR('H', 212, int)
CONN_INFO_SIZE = 16  # struct hci_conn_info: handle u16, bdaddr[6], type u8, out u8, state u16, link_mode u32
MAX_CONNS = 10


class HciError(Exception):
    pass


def _dev_id(adapter: str) -> int:
    return int(adapter[3:]) if adapter.startswith("hci") and adapter[3:].isdigit() else 0


def _open(dev_id: int) -> socket.socket:
    if not hasattr(socket, "BTPROTO_HCI"):
        raise HciError("raw HCI sockets are not supported on this platform")
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    s.bind((dev_id,))
    # Only deliver Command Complete / Command Status events to this socket.
    event_mask = (1 << EVT_CMD_COMPLETE) | (1 << EVT_CMD_STATUS)
    s.setsockopt(
        getattr(socket, "SOL_HCI", 0),
        getattr(socket, "HCI_FILTER", 2),
        struct.pack("<IIIH2x", 1 << HCI_EVENT_PKT, event_mask, 0, 0),
    )
    return s


def connection_handle(s: socket.socket, dev_id: int, mac: str) -> int | None:
    req = struct.pack("<HH", dev_id, MAX_CONNS) + bytes(CONN_INFO_SIZE * MAX_CONNS)
    buf = fcntl.ioctl(s.fileno(), HCIGETCONNLIST, req)
    count = struct.unpack_from("<H", buf, 2)[0]
    want = mac.lower()
    for i in range(min(count, MAX_CONNS)):
        handle, addr, _link_type = struct.unpack_from("<H6sB", buf, 4 + i * CONN_INFO_SIZE)
        if ":".join(f"{b:02x}" for b in reversed(addr)) == want:
            return handle
    return None


def read_rssi(mac: str, adapter: str = "hci0", timeout: float = 2.0) -> int:
    """RSSI in dBm of the existing connection to `mac`. Raises HciError if it can't be read."""
    dev_id = _dev_id(adapter)
    try:
        s = _open(dev_id)
    except PermissionError as e:
        raise HciError("permission denied opening HCI socket (needs CAP_NET_RAW or root)") from e
    except OSError as e:
        raise HciError(f"cannot open HCI socket: {e}") from e
    with s:
        handle = connection_handle(s, dev_id, mac)
        if handle is None:
            raise HciError(f"{mac} is not connected on {adapter}")
        s.send(struct.pack("<BHBH", HCI_COMMAND_PKT, OP_READ_RSSI, 2, handle))
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            if not select.select([s], [], [], left)[0]:
                break
            pkt = s.recv(260)
            if len(pkt) < 3 or pkt[0] != HCI_EVENT_PKT:
                continue
            evt = pkt[1]
            # Command Status: [04 0F len status ncmd op_lo op_hi]; non-zero status means rejected.
            if (
                evt == EVT_CMD_STATUS
                and len(pkt) >= 7
                and pkt[3]
                and struct.unpack_from("<H", pkt, 5)[0] == OP_READ_RSSI
            ):
                raise HciError(f"controller rejected Read RSSI (status 0x{pkt[3]:02x})")
            # Command Complete: [04 0E len ncmd op_lo op_hi status handle_lo handle_hi rssi]
            if evt == EVT_CMD_COMPLETE and len(pkt) >= 10 and struct.unpack_from("<H", pkt, 4)[0] == OP_READ_RSSI:
                status, _h, rssi = struct.unpack_from("<BHb", pkt, 6)
                if status:
                    raise HciError(f"Read RSSI failed (status 0x{status:02x})")
                return rssi
        raise HciError("timed out waiting for Read RSSI response")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python -m jkbms_pi.hci_rssi <mac> [hci0]")
    try:
        print(f"RSSI {read_rssi(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else 'hci0')} dBm")
    except HciError as e:
        sys.exit(f"error: {e}")
