#!/usr/bin/env python3
"""Minimal ATEM UDP handshake probe.

Goal: learn whether current ATEM Software Control will complete the transport
handshake with a software switcher before we implement device state.
No third-party Python packages are required.
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--client-id", type=int, default=1)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.bind, args.port))

    print("ATEM Mock - handshake probe")
    print(f"Listening on UDP {args.bind}:{args.port}")
    print("In ATEM Software Control, connect to 127.0.0.1")
    print("Ctrl-C to stop.\n")

    while True:
        data, peer = sock.recvfrom(65535)
        p = parse_packet(data)
        print(f"[{stamp()}] RX {peer[0]}:{peer[1]} {len(data)} bytes  {data.hex(' ')}")

        if p is None:
            print("  -> ignored: shorter than ATEM header")
            continue

        print(
            f"  flags=0x{p['flags']:02x} session=0x{p['session']:04x} "
            f"ack={p['ack']} id={p['packet_id']} payload={p['payload'].hex(' ')}"
        )

        # ATEM client opens with INIT + 8-byte payload beginning 01.
        if (p["flags"] & FLAG_INIT) and p["payload"][:1] in (b"\x01", b"\x04"):
            # Known switcher-side handshake response:
            # payload 02 00 + client id (16-bit) + four zero bytes.
            payload = struct.pack("!HH4x", 0x0200, args.client_id)
            response = make_packet(FLAG_INIT, p["session"], payload=payload)
            sock.sendto(response, peer)
            print(f"[{stamp()}] TX INIT response              {response.hex(' ')}")
            continue

        # Once Software Control ACKs the handshake, report it clearly.
        if p["flags"] & FLAG_ACK:
            print("*** ACK RECEIVED: transport handshake progressed. ***")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
