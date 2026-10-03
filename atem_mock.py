#!/usr/bin/env python3
"""ATEM Mock - compatibility bootstrap.

Experimental software implementation of the ATEM UDP control endpoint.
This build implements:
- UDP/9910 handshake
- session establishment
- ACK handling
- a minimal ATEM initialization state
- quiet, readable logs

It intentionally does not process video.
"""

import argparse
import socket
import struct
from datetime import datetime

PORT = 9910
HEADER_SIZE = 12

FLAG_COMMAND = 0x01
FLAG_INIT = 0x02
FLAG_RETRANSMIT = 0x04
FLAG_ACK = 0x10


def stamp():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def parse_packet(data: bytes):
    if len(data) < HEADER_SIZE:
        return None
    flags_size = struct.unpack_from("!H", data, 0)[0]
    return {
        "flags": (flags_size >> 11) & 0x1F,
        "length": flags_size & 0x07FF,
        "session": struct.unpack_from("!H", data, 2)[0],
        "ack": struct.unpack_from("!H", data, 4)[0],
        "packet_id": struct.unpack_from("!H", data, 10)[0],
        "payload": data[HEADER_SIZE:],
    }


def make_packet(flags, session, ack=0, packet_id=0, payload=b""):
    length = HEADER_SIZE + len(payload)
    first = ((flags & 0x1F) << 11) | (length & 0x07FF)
    return struct.pack("!HHH4xH", first, session, ack, packet_id) + payload


def command(name: str, payload=b"", reserved=0):
    raw_name = name.encode("ascii")
    if len(raw_name) != 4:
        raise ValueError("ATEM command names must be exactly 4 ASCII bytes")
    length = 8 + len(payload)
    return struct.pack("!HH4s", length, reserved, raw_name) + payload


def fixed_string(value: str, size: int):
    raw = value.encode("utf-8")[:size]
    return raw + (b"\x00" * (size - len(raw)))


def initial_state():
    """Return a deliberately small, known-good style ATEM state stream."""

    # Protocol version used by the public simulator reference.
    ver = command("_ver", struct.pack("!HH", 2, 30), reserved=0x0014)

    # Product identity. The topology below is intentionally conservative for
    # the first compatibility milestone. Once Software Control accepts it, the
    # project will grow proper Mini Extreme and Constellation profiles.
    pin = command(
        "_pin",
        fixed_string("ATEM Mock - Mini Extreme", 44),
        reserved=0x0004,
    )

    # Conservative topology payload derived from a known working ATEM
    # initialization sequence. This is a bootstrap profile, not yet a claim
    # of exact Mini Extreme capabilities.
    top_payload = bytes.fromhex(
        "01 18 02 01 04 02 01 01 "
        "04 01 00 00 01 01 04 00 "
        "00 00 01 01 01 01 00 00 "
        "00 20 00 00"
    )
    top = command("_top", top_payload, reserved=0x8000)

    # Initial Program / Preview state for M/E 1.
    prgi = command("PrgI", struct.pack("!BxH", 0, 1), reserved=0x0101)
    prvi = command("PrvI", struct.pack("!BxH4x", 0, 2), reserved=0x0000)

    # Initialization complete marker.
    incm = command("InCm", b"\x01\x00\x00\x00", reserved=0x0000)

    return [ver + pin + top, prgi + prvi, incm]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--client-id", type=int, default=1)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.bind, args.port))

    print("ATEM Mock - bootstrap profile")
    print(f"Listening on UDP {args.bind}:{args.port}")
    print("Connect ATEM Software Control to 127.0.0.1")
    print("Ctrl-C to stop.\n")

    peer = None
    session = None
    established = False
    state_sent = False
    next_packet_id = 1
    seen_rx = set()

    while True:
        data, addr = sock.recvfrom(65535)
        p = parse_packet(data)
        if p is None:
            continue

        key = (p["flags"], p["session"], p["ack"], p["packet_id"], p["payload"])
        first_time = key not in seen_rx
        seen_rx.add(key)

        # Initial client hello / disconnect-init.
        if (p["flags"] & FLAG_INIT) and p["payload"][:1] in (b"\x01", b"\x04"):
            peer = addr
            session = p["session"]
            payload = struct.pack("!HH4x", 0x0200, args.client_id)
            response = make_packet(FLAG_INIT, session, payload=payload)
            sock.sendto(response, peer)
            print(f"[{stamp()}] client {peer[0]}:{peer[1]} -> handshake")
            continue

        # Modern Software Control switches to 0x8000 + client id after init.
        expected_session = 0x8000 + args.client_id
        if p["session"] == expected_session and not established:
            established = True
            session = expected_session
            peer = addr
            print(f"[{stamp()}] session established: 0x{session:04x}")

        if not established:
            continue

        # ACK every client command/retransmission packet. This prevents the
        # retransmission flood seen in the first probe.
        if p["flags"] & (FLAG_COMMAND | FLAG_RETRANSMIT):
            ack = make_packet(FLAG_ACK, session, ack=p["packet_id"])
            sock.sendto(ack, peer)
            if first_time and p["packet_id"]:
                print(f"[{stamp()}] ACK client packet #{p['packet_id']}")

        # As soon as the transport session exists, push the minimal switcher
        # state. Each state packet is reliable and therefore gets a packet id.
        if not state_sent:
            for payload in initial_state():
                pkt = make_packet(
                    FLAG_COMMAND,
                    session,
                    packet_id=next_packet_id,
                    payload=payload,
                )
                sock.sendto(pkt, peer)
                print(
                    f"[{stamp()}] TX state packet #{next_packet_id} "
                    f"({len(payload)} bytes)"
                )
                next_packet_id += 1

            state_sent = True
            print(f"[{stamp()}] initialization state sent")
            print(">>> Check ATEM Software Control now. <<<")

        # Only display meaningful acknowledgements once.
        if (p["flags"] & FLAG_ACK) and first_time:
            print(
                f"[{stamp()}] client ACK "
                f"(ack={p['ack']}, id={p['packet_id']})"
            )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
