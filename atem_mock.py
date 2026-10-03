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
import time
from datetime import datetime
from pathlib import Path

from profiles import format_profiles, get_profile

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


def parse_commands(payload: bytes):
    """Parse one ATEM command packet payload into (name, body) tuples."""
    commands = []
    offset = 0
    total = len(payload)

    while offset + 8 <= total:
        length = struct.unpack_from("!H", payload, offset)[0]
        if length < 8 or offset + length > total:
            break

        name = payload[offset + 4:offset + 8].decode("ascii", errors="replace")
        body = payload[offset + 8:offset + length]
        commands.append((name, body))
        offset += length

    return commands


def state_program(program_source: int, me: int = 0):
    return command("PrgI", struct.pack("!BxH", me, program_source))


def state_preview(preview_source: int, me: int = 0):
    return command("PrvI", struct.pack("!BxH4x", me, preview_source))


def state_usk_on_air(keyer_id: int, on_air: bool, me: int = 0):
    return command(
        "KeOn",
        struct.pack("!BBBB", me, keyer_id, 1 if on_air else 0, 0),
    )


def state_dsk(dsk_id: int, on_air: bool):
    # Protocol >= 8.0.1 DskS layout:
    # id, onAir, inTransition, isAuto, isTowardsOnAir, remainingFrames.
    return command(
        "DskS",
        struct.pack(
            "!BBBBBB2x",
            dsk_id,
            1 if on_air else 0,
            0,
            0,
            0,
            0,
        ),
    )


def state_aux(aux_bus: int, source: int):
    return command("AuxS", struct.pack("!BBH", aux_bus, 0, source))


def state_transition(style: int, selection: int = 1, me: int = 0):
    # TrSS: M/E, current style, current selection, next style,
    # next selection (+ padding).
    return command(
        "TrSS",
        struct.pack("!BBBBB3x", me, style, selection, style, selection),
    )


def state_transition_position(handle_position: int, in_transition=False, me: int = 0, remaining_frames: int = 0):
    # TrPs: M/E, inTransition, remainingFrames, pad, handlePosition.
    return command(
        "TrPs",
        struct.pack(
            "!BBBBH2x",
            me,
            1 if in_transition else 0,
            remaining_frames,
            0,
            handle_position,
        ),
    )


def state_mix_rate(rate: int, me: int = 0):
    return command("TMxP", struct.pack("!BB2x", me, rate))


def state_supersource_box(box_id: int, box):
    # SSBP v8+: ssrcId, boxId, enabled, pad, source, x, y, size,
    # cropped, pad, cropTop, cropBottom, cropLeft, cropRight.
    return command(
        "SSBP",
        struct.pack(
            "!BBBBHhhHBBHHHH2x",
            0,
            box_id,
            1 if box["enabled"] else 0,
            0,
            box["source"],
            box["x"],
            box["y"],
            box["size"],
            1 if box["cropped"] else 0,
            0,
            box["crop_top"],
            box["crop_bottom"],
            box["crop_left"],
            box["crop_right"],
        ),
    )


def state_supersource_properties(props):
    return command(
        "SSrc",
        struct.pack(
            "!BBHHBBHHB3x",
            0,
            0,
            props["art_fill_source"],
            props["art_cut_source"],
            props["art_option"],
            1 if props["art_pre_multiplied"] else 0,
            props["art_clip"],
            props["art_gain"],
            1 if props["art_invert_key"] else 0,
        ),
    )


def initial_state(profile=None):
    """Return a known working ATEM initialization stream."""
    if profile and profile.get("bootstrap", "").endswith(".data"):
        data_path = Path(__file__).with_name(profile["bootstrap"].split("/")[0]) / Path(profile["bootstrap"]).name
        lines = data_path.read_text(encoding="utf-8").splitlines()
        return [bytes.fromhex(line.strip()) for line in lines if line.strip()]

    raw_blocks = [
        # pyAtemSim setup block 1
        "00 0c 00 14 5f 76 65 72 00 02 00 1e 00 34 00 04 5f 70 69 6e 41 54 45 4d 20 54 65 6c 65 76 69 73 69 6f 6e 20 53 74 75 64 69 6f 20 48 44 00 00 04 00 00 00 00 00 50 72 70 00 00 00 00 08 00 00 00 00 24 80 00 5f 74 6f 70 01 18 02 01 04 02 01 01 04 01 00 00 01 01 04 00 00 00 01 01 01 01 00 00 00 20 00 00 00 0c 64 50 5f 4d 65 43 00 01 00 01 00 0c 00 00 5f 6d 70 6c 14 00 00 20 00 14 00 00 5f 4d 76 43 0a 01 01 00 00 01 01 01 00 00 00 02 00 0c 00 00 5f 41 4d 43 0a 00 01 00 00 c2 00 00 5f 56 4d 43 00 0e 64 50 00 0b 00 80 00 00 00 80 00 00 00 00 00 01 72 70 00 00 00 00 40 00 00 00 00 00 02 00 53 50 00 00 00 80 00 00 00 00 00 03 53 50 5a 00 00 00 40 00 00 00 00 00 04 5a 43 53 00 00 00 10 00 00 00 00 00 05 43 6d 01 00 00 00 20 00 00 00 00 00 06 70 00 00 00 00 00 40 00 00 00 00 00 07 43 43 64 00 00 00 80 00 00 00 00 00 08 00 00 00 00 00 01 00 00 00 00 00 00 09 08 00 00 00 00 02 00 00 00 00 00 00 0a 01 00 01 00 00 04 40 00 00 00 00 00 0b 00 00 00 00 00 08 80 00 00 00 00 00 0c 43 64 50 00 00 14 40 00 00 00 00 00 0d 00 00 00 00 00 28 80 00 00 00 00 00 00 0c 00 20 5f 4d 41 43 64 50 06 08 00 20 00 00 5f 44 56 45 00 00 00 11 10 11 12 13 14 15 16 17 18 19 1a 1b 1c 1d 1e 1f 22 50 06 08 00 0c 00 00 50 6f 77 72 01 00 00 50 00 0c 08 00 56 69 64 4d 0d 00 00 20 00 0c 43 43 56 33 73 6c 00 80 00 00 00 0c 00 00 54 63 4c 6b 00 70 00 00 00 2c 00 00 49 6e 50 72 00 00 42 6c 61 63 6b 00 04 80 00 00 00 02 00 00 00 00 00 50 72 70 42 4c 4b 00 01 00 01 00 01 00 01 00 13 01 00 2c 06 08 49 6e 50 72 00 01 50 50 20 4d 41 49 4e 00 08 00 00 00 00 00 00 00 00 20 0b c2 50 50 31 00 00 08 00 02 00 02 00 02 13 01 00 2c 00 50 49 6e 50 72 00 02 50 50 20 54 48 49 52 44 53 00 64 50 06 0b 00 80 00 00 00 02 50 50 32 00 00 50 00 02 00 02 00 00 13 01 00 2c 00 20 49 6e 50 72 00 03 50 50 20 53 54 41 47 45 00 00 00 00 00 50 72 70 00 00 00 00 50 50 33 00 00 20 00 02 00 02 00 50 13 01 00 2c 00 00 49 6e 50 72 00 04 43 41 4d 45 52 41 20 34 00 00 00 00 00 20 00 00 43 43 64 50 43 41 4d 34 00 01 00 02 00 02 00 00 13 01 00 2c 00 5a 49 6e 50 72 00 05 43 41 4d 45 52 41 20 31 00 01 01 01 00 01 00 00 00 00 00 00 43 41 4d 31 00 5c 00 01 00 01 00 00 13 01 00 2c 43 43 49 6e 50 72 00 06 43 41 4d 45 52 41 20 32 00 50 72 70 15 e0 00 00 00 00 00 00 43 41 4d 32 00 43 00 01 00 01 00 03 13 01 00 2c 00 01 49 6e 50 72 00 07 43 41 4d 45 52 41 20 33 00 20 00 00 43 43 64 50 07 01 08 01 43 41 4d 33 00 00 00 01 00 01 00 70 13 01 00 2c 08 00 49 6e 50 72 00 08 43 41 4d 45 52 41 20 35 00 01 00 00 00 00 00 00 00 43 73 74 43 41 4d 35 00 00 00 01 00 01 00 50 13 01 00 2c 07 08 49 6e 50 72 03 e8 43 6f 6c 6f 72 20 42 61 72 73 00 00 00 00 00 00 00 20 64 50 42 41 52 53 01 08 01 00 01 00 02 04 13 01 00 2c 00 00 49 6e 50 72 07 d1 43 6f 6c 6f 72 20 31 00 43 43 64 50 07 08 02 80 00 00 00 04 43 4f 4c 31 01 00 01 00 01 00 03 00 03 01 00 2c 00 20 49 6e 50 72 07 d2 43 6f 6c 6f 72 20 32 00 00 00 00 00 00 00 00 00 00 00 00 00 43 4f 4c 32 01 20 01 00 01 00 03 50 03 01 00 2c 00 00 49 6e 50 72 0b c2 4d 65 64 69 61 20 50 6c 61 79 65 72 20 31 00 50 43 43 64 50 4d 50 31 00 01 00 01 00 01 00 04 00 13 01 00 2c 08 00 49 6e 50 72 0b c3 4d 65 64 69 61 20 50 6c 61 79 65 72 20 31 20 4b 65 79 00 00 4d 50 31 4b 01 00 01 00 01 00 05 70 13 01 00 2c 43 43 49 6e 50 72 0b cc 4d 65 64 69 61 20 50 6c 61 79 65 72 20 32 00 00 04 00 00 00 4d 50 32 00 01 12 01 00 01 00 04 04 13 01 00 2c 01 00 49 6e 50 72 0b cd 4d 65 64 69 61 20 50 6c 61 79 65 72 20 32 20 4b 65 79 00 04 4d 50 32 4b 01 00 01 00 01 00 05 04 13 01 00 2c 01 00 49 6e 50 72 0f aa 4b 65 79 20 31 20 4d 61 73 6b 00 04 c0 12 ed 04 81 d3 00 01 4d 31 4b 31 01 00 01 00 01 00 82 00 03 00 00 2c 20 00 49 6e 50 72 13 92 44 53 4b 20 31 20 4d 61 73 6b 00 02 00 00 00 00 cc c6 0e 01 44 4b 31 4d 01 00 01 00 01 00 82 00 03 00 00 2c 00 00 49 6e 50 72 13 9c 44 53 4b 20 32 20 4d 61 73 6b 00 00 a6 7d 03 00 00 00 00 00 44 4b 32 4d 01 d3 01 00 01 00 82 00 03 00 00 2c a6 7d 49 6e 50 72 27 1a 50 72 6f 67 72 61 6d 00 bc e5 0d 01 78 0f 01 02 14 00 00 00 50 47 4d 00 01 00 01 00 01 00 80 02 03 00 00 2c 20 46 49 6e 50 72 27 1b 50 72 65 76 69 65 77 00 78 64 69 75 73 63 66 58 44 49 55 53 50 56 57 00 01 78 01 00 01 00 80 01 03 00 00 2c 08 08 49 6e 50 72 1b 59 43 6c 65 61 6e 20 46 65 65 64 20 31 00 00 00 00 44 f9 00 02 43 46 44 31 01 7e 01 00 01 00 80 02 03 00",
        # pyAtemSim setup block 2
        "00 2c 00 14 49 6e 50 72 1b 5a 43 6c 65 61 6e 20 46 65 65 64 20 32 00 4d 20 54 65 6c 65 76 43 46 44 32 01 20 01 00 01 00 80 6f 03 00 00 2c 00 04 49 6e 50 72 1f 41 41 75 78 69 6c 69 61 72 79 20 4f 75 74 00 5f 74 6f 70 01 18 41 55 58 00 00 01 01 00 01 00 81 01 02 00 00 0c 01 01 4d 76 56 4d 00 07 00 00 00 0c 64 50 4d 76 56 4d 01 06 00 01 00 0c 00 00 4d 76 56 4d 02 07 00 20 00 0c 00 00 4d 76 56 4d 03 06 01 00 00 0c 01 01 4d 76 56 4d 04 04 00 00 00 0c 4d 43 4d 76 56 4d 05 05 00 00 00 0c 4d 43 4d 76 56 4d 06 06 00 80 00 0c 00 80 4d 76 56 4d 07 07 72 70 00 0c 00 00 4d 76 56 4d 08 08 02 00 00 0c 00 00 4d 76 56 4d 09 09 00 03 00 0c 5a 00 4d 76 56 4d 0a 0a 00 00 00 0c 43 53 4d 76 56 4d 0b 0b 00 00 00 0c 43 6d 4d 76 56 4d 0c 0c 00 00 00 0c 06 70 4d 76 56 4d 0d 0d 00 00 00 0c 00 07 4d 76 50 72 00 0c 00 00 00 10 00 00 4d 76 49 6e 00 00 27 1b 00 01 00 00 00 0c 08 00 56 75 4d 43 00 00 00 00 00 0c 0a 01 53 61 4d 77 00 00 01 00 00 10 00 0b 4d 76 49 6e 00 01 27 1a 01 00 00 00 00 0c 64 50 56 75 4d 43 00 01 01 00 00 0c 00 00 53 61 4d 77 00 01 00 00 00 10 00 0c 4d 76 49 6e 00 02 00 05 01 00 00 20 00 0c 5f 44 56 75 4d 43 00 02 00 11 00 0c 14 15 53 61 4d 77 00 02 00 1d 00 10 22 50 4d 76 49 6e 00 03 00 06 01 00 01 00 00 0c 00 0c 56 75 4d 43 00 03 00 00 00 0c 00 0c 53 61 4d 77 00 03 00 80 00 10 00 0c 4d 76 49 6e 00 04 00 07 01 00 00 2c 00 0c 49 6e 56 75 4d 43 00 04 00 63 00 0c 04 80 53 61 4d 77 00 04 00 00 00 10 72 70 4d 76 49 6e 00 05 00 04 01 00 01 00 00 0c 00 2c 56 75 4d 43 00 05 00 01 00 0c 20 4d 53 61 4d 77 00 05 00 00 00 10 00 00 4d 76 49 6e 00 06 00 03 01 00 00 02 00 0c 00 02 56 75 4d 43 00 06 00 6e 00 0c 00 02 53 61 4d 77 00 06 00 44 00 10 64 50 4d 76 49 6e 00 07 00 01 01 00 32 00 00 0c 00 02 56 75 4d 43 00 07 00 2c 00 0c 49 6e 53 61 4d 77 00 07 00 53 00 10 47 45 4d 76 49 6e 00 08 00 02 01 00 00 00 00 0c 33 00 56 75 4d 43 00 08 00 50 00 0c 00 2c 53 61 4d 77 00 08 00 04 00 10 4d 45 4d 76 49 6e 00 09 1f 41 01 00 00 00 00 0c 64 50 56 75 4d 43 00 09 00 02 00 0c 00 00 53 61 4d 77 00 09 00 6e 00 0c 00 05 56 75 4d 6f 00 64 20 31 00 0c 01 01 50 72 67 49 00 00 00 05 00 10 4d 31 50 72 76 49 00 01 00 01 00 01 00 2c 00 10 49 6e 54 72 53 53 00 00 01 00 01 41 20 32 00 0c 72 70 54 72 50 72 00 00 00 00 00 10 4d 32 54 72 50 73 00 00 14 03 00 00 00 2c 00 0c 49 6e 54 4d 78 50 00 14 4d 45 00 0c 20 33 54 44 70 50 00 14 00 00 00 1c 08 01 54 57 70 50 00 1e 00 01 00 00 07 d1 13 88 00 00 13 88 13 88 00 00 00 08 00 1c 4d 45 54 44 76 50 00 1e 1e 1c 0b c2 0b c3 01 01 01 f4 02 bc 00 00 00 00 00 01 00 1c 00 50 54 53 74 50 00 01 01 6e 01 f4 02 bc 00 6f 00 02 00 49 00 22 00 05 00 00 00 0c 00 00 4b 65 4f 6e 00 00 00 53 00 1c 01 00 4b 65 42 50 00 00 00 01 01 00 00 00 00 00 00 d1 23 28 dc d8 c1 80 3e 80 00 14 64 50 4b 65 4c 6d 00 00 01 04 01 f4 02 bc 00 00 01 00 00 14 03 00 4b 65 43 6b 00 00 04 b0 03 e8 03 84 00 00 00 6f 00 18 32 00 4b 65 50 74 00 00 06 00 13 88 13 88 00 00 13 88 13 88 00 00 00 44 03 50 4b 65 44 56 00 00 49 6e 00 00 01 f4 00 00 01 f4 00 00 00 00 00 00 00 00 00 00 00 00 01 00 00 50 00 32 00 32 00 00 00 32 64 00 00 00 00 00 03 e8 01 68 19 00 00 00 00 00 00 00 00 00 1e 20 50 6c 00 10 65 72 4b 65 46 53 00 00 00 00 00 50 00 4b 00 3c 01 00 4b 4b 46 50 00 00 01 2c 00 00 03 e8 00 00 03 e8 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 50 00 00 00 00 03 e8 01 68 19 04 00 00 00 00 00 00 00 00 00 3c 0b cd 4b 4b 46 50 00 00 02 6c 00 00 03 e8 00 00 03 e8 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 03 e8 01 68 19 20 00 00 00 00 00 00 00 00 00 10 ed 04 44 73 6b 42 00 31 0b c2 0b c2 01 00 00 1c 82 00 44 73 6b 50 00 00 14 00 01 36 01 7c 00 00 23 28 dc d8 c1 80 3e 80 00 02 00 10 00 00 44 73 6b 53 00 00 00 00 01 14 01 00 00 10 82 00 44 73 6b 42 01 00 00 02 00 02 13 9c 00 1c 4b 20 44 73 6b 50 01 00 14 00 00 82 01 d6 00 00 23 28 dc d8 c1 80 3e 80 01 00 00 10 82 00 44 73 6b 53 01 00 00 00 01 14 27 1a 00 0c 6f 67 46 74 62 50 00 3c 0d 01 00 0c 01 02 46 74 62 53 00 00 00 3c 00 10 01 00 43 6f 6c 56 00 00 01 f4 03 e8 01 f4 00 10 27 1b 43 6f 6c 56 01 65 01 0e 03 e8 01 f4 00 0c 66 58 41 75 78 53 00 56 00 00",
        # pyAtemSim setup block 3
        "00 50 00 14 4d 50 66 65 00 5a 00 00 01 cc 05 2a 20 14 fd 25 fe 85 d7 08 a6 4c ec 55 16 76 00 2e 43 45 4e 54 52 41 4c 5f 59 4f 55 54 48 5f 79 6f 75 74 68 2d 6c 69 76 65 2d 62 61 63 6b 67 72 6f 75 6e 64 73 2d 43 48 52 49 53 54 4d 41 53 00 01 00 20 01 00 4d 50 66 65 00 0c 00 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 06 00 00 00 20 00 00 4d 50 66 65 00 07 00 02 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 76 00 00 00 20 00 00 4d 50 66 65 00 76 00 03 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 0c 00 00 00 20 56 4d 4d 50 66 65 00 0c 00 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 09 00 00 00 40 5a 00 4d 50 66 65 00 0a 00 05 01 40 88 37 b5 8f d8 5c ac 06 23 7b d5 95 66 5a 63 76 00 1f 43 45 4e 54 52 41 4c 5f 62 61 63 6b 67 72 6f 75 6e 64 2d 6c 6f 67 6f 5f 62 6c 61 63 6b 20 32 00 00 20 49 6e 4d 50 66 65 00 01 00 06 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 61 00 00 00 20 01 00 4d 50 66 65 00 76 00 07 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00 00 00 20 00 00 4d 50 66 65 00 01 00 08 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 0c 00 00 00 20 4d 43 4d 50 66 65 00 0c 00 09 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 03 00 00 00 20 01 00 4d 50 66 65 00 75 00 0a 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 10 00 00 00 20 49 6e 4d 50 66 65 00 00 00 0b 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 61 00 00 00 20 00 00 4d 50 66 65 00 76 00 0c 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 05 00 00 00 20 20 4d 4d 50 66 65 00 05 00 0d 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 0c 00 00 00 20 4d 43 4d 50 66 65 00 0c 00 0e 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 07 00 00 00 58 32 00 4d 50 66 65 00 75 00 0f 01 fd 80 27 1c b6 9b 39 fb 2b 28 8e 95 5b c8 c4 23 10 00 37 43 45 4e 54 52 41 4c 5f 59 4f 55 54 48 5f 79 6f 75 74 68 2d 6c 69 76 65 2d 62 61 63 6b 67 72 6f 75 6e 64 5f 62 6c 61 63 6b 2d 6c 6f 67 6f 2d 63 65 6e 74 65 72 20 32 50 00 58 4d 43 4d 50 66 65 00 0c 00 10 01 e4 72 c1 a0 ca da 97 75 bb 1c ca 91 54 83 f2 8d 64 00 37 43 45 4e 54 52 41 4c 5f 59 4f 55 54 48 5f 79 6f 75 74 68 2d 6c 69 76 65 2d 62 61 63 6b 67 72 6f 75 6e 64 5f 6c 6f 67 6f 2d 77 68 69 74 65 2d 63 65 6e 74 65 72 20 32 00 00 54 4d 32 4d 50 66 65 00 00 00 11 01 dd 58 57 dd 91 1d c2 a5 23 3a 21 69 d7 53 83 45 0c 00 31 43 45 4e 54 52 41 4c 5f 59 4f 55 54 48 5f 79 6f 75 74 68 2d 6c 69 76 65 2d 62 61 63 6b 67 72 6f 75 6e 64 5f 6c 6f 77 65 72 74 68 69 72 64 73 20 32 c2 0b c3 00 20 01 f4 4d 50 66 65 00 00 00 12 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 6f 00 00 00 20 00 22 4d 50 66 65 00 0c 00 13 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 0c 00 00 4d 50 43 45 00 01 05 00 00 0c 3e 80 4d 50 43 45 01 01 11 00 00 1c 01 04 52 58 4d 53 00 00 01 00 00 00 00 00 00 00 00 6b 00 00 00 b0 00 02 00 84 00 1c 00 6f 52 58 43 50 00 00 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 28 03 50 52 58 53 53 00 00 49 6e 00 00 00 00 ff ff ff ff 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 32 00 0c 00 32 52 58 43 43 00 00 00 e8 00 1c 19 00 52 58 4d 53 00 01 00 00 00 00 00 00 00 00 00 72 00 00 00 53 00 02 00 00 00 1c 00 4b 52 58 43 50 00 01 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 28 00 00 52 58 53 53 00 01 00 00 00 00 00 00 ff ff ff ff 00 00 00 00 00 68 19 04 00 00 00 00 00 00 00 00 00 00 0b cd 00 0c 46 50 52 58 43 43 00 01 00 e8 00 1c 03 e8 52 58 4d 53 00 02 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 02 00 00 00 1c 03 e8 52 58 43 50 00 02 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 0b c2 00 28 01 00 52 58 53 53 00 02 6b 50 00 00 00 00 ff ff ff ff 00 00 00 00 00 d8 c1 80 00 00 00 00 00 00 00 00 00 00 6b 53 00 0c 00 00 52 58 43 43 00 02 00 00 00 1c 6b 42 52 58 4d 53 00 03 13 9c 00 00 00 00 00 00 00 50 00 00 00 00 00 02 00 d6 00 1c 23 28 52 58 43 50 00 03 01 00 00 10 00 00 00 00 00 00 00 00 00 00 00 00 27 1a 00 28 6f 67 52 58 53 53 00 03 0d 01 00 00 00 00 ff ff ff ff 00 00 00 00 00 10 01 00 00 00 00 00 00 00 00 00 00 00 01 f4 00 0c 27 1b 52 58 43 43 00 03 00 0e 00 0c 01 f4 41 4d 50 50 01 75 78 53 00 18 00 00 41 4d 49 50 00 01 00 01 00 01 00 02 00 08 80 00 00 00 00 00 00 18 65 61 41 4d 49 50 00 02 00 31 00 02 00 02 00 f9 80 00 00 00 00 00",
        # pyAtemSim setup block 4
        "00 18 00 14 41 4d 49 50 00 03 00 00 00 03 00 02 00 14 80 00 00 00 00 00 00 18 ec 55 41 4d 49 50 00 04 00 54 00 04 00 02 00 4f 80 00 00 00 00 00 00 18 68 2d 41 4d 49 50 00 05 00 63 00 05 00 01 00 6e 80 00 00 00 00 00 00 18 54 4d 41 4d 49 50 00 06 00 00 00 06 00 01 00 0c 80 00 00 00 00 00 00 18 00 00 41 4d 49 50 00 07 00 00 00 07 00 01 00 20 80 00 00 00 00 00 00 18 00 02 41 4d 49 50 00 08 00 00 00 08 00 01 00 00 80 00 00 00 00 00 00 18 00 00 41 4d 49 50 05 15 02 03 00 00 02 00 00 00 00 00 00 00 00 00 00 18 00 00 41 4d 49 50 03 e9 02 4d 00 01 00 20 01 0c 80 00 00 00 01 00 00 10 00 00 41 4d 4d 4f b5 d9 00 00 00 09 00 00 00 28 5a 00 41 4d 54 6c 00 0a 00 01 00 00 02 00 00 03 00 00 04 00 00 05 00 00 06 00 00 07 00 00 08 00 05 15 00 03 e9 01 00 10 63 6b 41 4d 48 50 19 3f 40 27 28 7a 28 7a 00 0c 61 63 41 54 4d 50 00 00 01 6e 00 10 66 65 54 4d 49 50 00 00 00 05 01 01 00 00 00 10 00 00 54 4d 49 50 00 61 00 06 01 01 00 00 00 10 66 65 54 4d 49 50 00 00 00 07 01 01 00 00 00 10 00 00 54 4d 49 50 00 01 00 08 01 01 00 00 00 14 66 65 4d 4d 4f 50 00 01 03 00 01 00 00 05 01 00 00 00 00 14 00 00 4d 4d 4f 50 01 01 03 00 01 50 00 06 01 00 00 09 00 14 00 00 4d 4d 4f 50 02 01 03 00 01 00 00 07 01 00 00 00 00 14 01 00 4d 4d 4f 50 03 01 03 00 01 00 00 08 01 00 00 00 00 0c 00 00 4c 4b 53 54 00 00 00 00 00 10 49 6e 5f 54 6c 43 00 01 00 00 08 00 00 00 00 14 00 00 54 6c 49 6e 00 08 02 00 00 00 01 00 00 00 00 00 00 54 66 65 54 6c 53 72 00 18 00 00 00 00 01 02 00 02 00 00 03 00 00 04 00 00 05 01 00 06 00 00 07 00 00 08 00 03 e8 00 07 d1 00 07 d2 00 0b c2 00 0b c3 00 0b cc 00 0b cd 00 0f aa 00 13 92 00 13 9c 00 27 1a 00 27 1b 00 1b 59 00 1b 5a 00 1f 41 00 00 00 00 24 00 00 54 6c 46 63 00 08 00 01 00 00 02 00 00 03 00 00 04 00 00 05 00 00 06 00 00 07 00 00 08 00 c8 c4 00 0c 00 37 4d 52 50 72 00 00 ff ff 00 0c 55 54 4d 52 63 53 00 74 00 00 00 14 76 65 4d 50 72 70 00 00 01 00 00 04 00 00 44 53 4b 31 00 10 6c 6f 4d 50 72 70 00 01 00 00 00 00 00 00 00 10 4d 43 4d 50 72 70 00 02 00 00 00 00 00 00 00 10 da 97 4d 50 72 70 00 03 00 00 00 00 00 00 00 10 4e 54 4d 50 72 70 00 04 00 00 00 00 00 00 00 10 68 2d 4d 50 72 70 00 05 00 00 00 00 00 00 00 10 64 5f 4d 50 72 70 00 06 00 00 00 00 00 00 00 10 74 65 4d 50 72 70 00 07 00 00 00 00 00 00 00 10 00 11 4d 50 72 70 00 08 00 00 00 00 00 00 00 10 53 83 4d 50 72 70 00 09 00 00 00 00 00 00 00 10 55 54 4d 50 72 70 00 0a 00 00 00 00 00 00 00 10 61 63 4d 50 72 70 00 0b 00 00 00 00 00 00 00 10 68 69 4d 50 72 70 00 0c 00 00 00 00 00 00 00 10 66 65 4d 50 72 70 00 0d 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 0e 00 00 00 00 00 00 00 10 66 65 4d 50 72 70 00 0f 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 10 00 00 00 00 00 00 00 10 43 45 4d 50 72 70 00 11 00 00 00 00 00 00 00 10 11 00 4d 50 72 70 00 12 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 13 00 00 00 00 00 00 00 10 00 6f 4d 50 72 70 00 14 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 15 00 00 00 00 00 00 00 10 53 53 4d 50 72 70 00 16 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 17 00 00 00 00 00 00 00 10 00 32 4d 50 72 70 00 18 00 00 00 00 00 00 00 10 19 00 4d 50 72 70 00 19 00 00 00 00 00 00 00 10 00 72 4d 50 72 70 00 1a 00 00 00 00 00 00 00 10 43 50 4d 50 72 70 00 1b 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 1c 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 1d 00 00 00 00 00 00 00 10 19 04 4d 50 72 70 00 1e 00 00 00 00 00 00 00 10 46 50 4d 50 72 70 00 1f 00 00 00 00 00 00 00 10 4d 53 4d 50 72 70 00 20 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 21 00 00 00 00 00 00 00 10 01 00 4d 50 72 70 00 22 00 00 00 00 00 00 00 10 0b c2 4d 50 72 70 00 23 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 24 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 25 00 00 00 00 00 00 00 10 43 43 4d 50 72 70 00 26 00 00 00 00 00 00 00 10 13 9c 4d 50 72 70 00 27 00 00 00 00 00 00 00 10 00 d6 4d 50 72 70 00 28 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 29 00 00 00 00 00 00 00 10 6f 67 4d 50 72 70 00 2a 00 00 00 00 00 00 00 10 ff ff 4d 50 72 70 00 2b 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 2c 00 00 00 00 00 00 00 10 00 0e 4d 50 72 70 00 2d 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 2e 00 00 00 00 00 00 00 10 80 00 4d 50 72 70 00 2f 00 00 00 00 00 00 00 10 00 31 4d 50 72 70 00 30 00 00 00 00 00 00",
        # pyAtemSim setup block 5
        "00 10 00 14 4d 50 72 70 00 31 00 00 00 00 00 00 00 10 80 00 4d 50 72 70 00 32 00 00 00 00 00 00 00 10 00 54 4d 50 72 70 00 33 00 00 00 00 00 00 00 10 68 2d 4d 50 72 70 00 34 00 00 00 00 00 00 00 10 80 00 4d 50 72 70 00 35 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 36 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 37 00 00 00 00 00 00 00 10 80 00 4d 50 72 70 00 38 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 39 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 3a 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 3b 00 00 00 00 00 00 00 10 02 4d 4d 50 72 70 00 3c 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 3d 00 00 00 00 00 00 00 10 5a 00 4d 50 72 70 00 3e 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 3f 00 00 00 00 00 00 00 10 05 15 4d 50 72 70 00 40 00 00 00 00 00 00 00 10 40 27 4d 50 72 70 00 41 00 00 00 00 00 00 00 10 01 6e 4d 50 72 70 00 42 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 43 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 44 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 45 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 46 00 00 00 00 00 00 00 10 00 05 4d 50 72 70 00 47 00 00 00 00 00 00 00 10 03 00 4d 50 72 70 00 48 00 00 00 00 00 00 00 10 4f 50 4d 50 72 70 00 49 00 00 00 00 00 00 00 10 01 00 4d 50 72 70 00 4a 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 4b 00 00 00 00 00 00 00 10 49 6e 4d 50 72 70 00 4c 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 4d 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 4e 00 00 00 00 00 00 00 10 01 02 4d 50 72 70 00 4f 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 50 00 00 00 00 00 00 00 10 0b c2 4d 50 72 70 00 51 00 00 00 00 00 00 00 10 92 00 4d 50 72 70 00 52 00 00 00 00 00 00 00 10 00 1f 4d 50 72 70 00 53 00 00 00 00 00 00 00 10 00 01 4d 50 72 70 00 54 00 00 00 00 00 00 00 10 06 00 4d 50 72 70 00 55 00 00 00 00 00 00 00 10 50 72 4d 50 72 70 00 56 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 57 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 58 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 59 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 5a 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 5b 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 5c 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 5d 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 5e 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 5f 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 60 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 61 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 62 00 00 00 00 00 00 00 10 00 00 4d 50 72 70 00 63 00 00 00 00 00 00 00 0c 00 00 43 43 73 74 00 00 13 88 00 20 72 70 43 43 64 50 01 00 00 80 00 00 00 01 00 00 00 00 00 0e 00 00 00 00 00 00 00 10 66 65 00 20 72 70 43 43 64 50 01 00 02 80 00 00 00 01 00 00 00 00 00 10 00 00 2a 00 00 00 00 10 43 45 00 20 72 70 43 43 64 50 01 01 0d 01 00 01 00 00 00 00 00 00 00 12 00 00 00 00 00 00 00 10 00 00 00 20 72 70 43 43 64 50 01 01 01 01 00 01 00 00 00 00 00 00 00 14 00 00 02 00 00 00 00 10 00 00 00 20 72 70 43 43 64 50 01 01 02 02 00 00 00 01 00 00 00 00 00 16 00 00 15 e0 00 00 00 10 00 00 00 20 72 70 43 43 64 50 01 01 05 03 00 00 00 00 00 01 00 00 00 18 00 00 00 00 4e 20 00 10 19 00 00 20 72 70 43 43 64 50 01 01 08 01 00 01 00 00 00 00 00 00 00 1a 00 00 01 00 00 00 00 10 43 50 00 20 72 70 43 43 64 50 01 04 04 01 00 01 00 00 00 00 00 00 00 1c 00 00 00 00 00 00 00 10 00 00 00 20 72 70 43 43 64 50 01 08 00 80 00 00 00 04 00 00 00 00 00 1e 00 00 00 00 00 00 00 00 00 00 00 20 72 70 43 43 64 50 01 08 01 80 00 00 00 04 00 00 00 00 00 20 00 00 00 00 00 00 00 00 00 00 00 20 72 70 43 43 64 50 01 08 02 80 00 00 00 04 00 00 00 00 00 22 00 00 08 00 08 00 08 00 08 00 00 20 72 70 43 43 64 50 01 08 03 80 00 00 00 04 00 00 00 00 00 24 00 00 00 00 00 00 00 00 00 00 00 20 72 70 43 43 64 50 01 08 04 80 00 00 00 02 00 00 00 00 00 26 00 00 04 00 08 00 00 10 13 9c 00 20 72 70 43 43 64 50 01 08 05 80 00 00 00 01 00 00 00 00 00 28 00 00 08 00 00 00 00 10 00 00 00 20 72 70 43 43 64 50 01 08 06 80 00 00 00 02 00 00 00 00 00 2a 00 00 00 00 08 00 00 10 ff ff 00 20 72 70 43 43 64 50 01 0b 00 80 00 00 00 02 00 00 00 00 00 2c 00 00 00 00 00 00 00 10 00 0e 00 20 72 70 43 43 64 50 02 00 00 80 00 00 00 01 00 00 00 00 00 2e 00 00 00 00 00 00 00 10 80 00 00 20 72 70 43 43 64 50 02 00 02 80 00 00 00 01 00 00 00 00 00 30 00 00 2a 00 00 00 01 7e 01 00",
        # pyAtemSim setup block 6
        "00 20 00 14 43 43 64 50 02 01 0d 01 00 01 00 00 00 00 00 00 00 50 72 70 00 32 00 00 00 00 00 00 00 20 00 54 43 43 64 50 02 01 01 01 00 01 00 00 00 00 00 00 00 50 72 70 02 34 00 00 00 00 00 00 00 20 80 00 43 43 64 50 02 01 02 02 00 00 00 01 00 00 00 00 00 50 72 70 15 e0 00 00 00 00 00 00 00 20 00 00 43 43 64 50 02 01 05 03 00 00 00 00 00 01 00 00 00 50 72 70 00 00 4e 20 00 00 00 00 00 20 00 00 43 43 64 50 02 01 08 01 00 01 00 00 00 00 00 00 00 50 72 70 01 3a 00 00 00 00 00 00 00 20 00 00 43 43 64 50 02 04 04 01 00 01 00 00 00 00 00 00 00 50 72 70 00 3c 00 00 00 00 00 00 00 20 00 00 43 43 64 50 02 08 00 80 00 00 00 04 00 00 00 00 00 50 72 70 00 00 00 00 00 00 00 00 00 20 00 00 43 43 64 50 02 08 01 80 00 00 00 04 00 00 00 00 00 50 72 70 00 00 00 00 00 00 00 00 00 20 40 27 43 43 64 50 02 08 02 80 00 00 00 04 00 00 00 00 00 50 72 70 08 00 08 00 08 00 08 00 00 20 00 00 43 43 64 50 02 08 03 80 00 00 00 04 00 00 00 00 00 50 72 70 00 00 00 00 00 00 00 00 00 20 00 00 43 43 64 50 02 08 04 80 00 00 00 02 00 00 00 00 00 50 72 70 04 00 08 00 00 00 00 00 00 20 00 05 43 43 64 50 02 08 05 80 00 00 00 01 00 00 00 00 00 50 72 70 08 00 00 00 00 00 00 00 00 20 4f 50 43 43 64 50 02 08 06 80 00 00 00 02 00 00 00 00 00 50 72 70 00 00 08 00 00 00 00 00 00 20 00 00 43 43 64 50 02 0b 00 80 00 00 00 02 00 00 00 00 00 50 72 70 00 00 00 00 00 00 00 00 00 20 00 00 43 43 64 50 04 00 00 80 00 00 00 01 00 00 00 00 00 50 72 70 00 00 00 00 00 00 00 00 00 20 01 02 43 43 64 50 04 00 02 80 00 00 00 01 00 00 00 00 00 50 72 70 2a 00 00 00 00 00 00 00 00 20 0b c2 43 43 64 50 04 01 0d 01 00 01 00 00 00 00 00 00 00 50 72 70 00 52 00 00 00 00 00 00 00 20 00 1f 43 43 64 50 04 01 01 01 00 01 00 00 00 00 00 00 00 50 72 70 02 54 00 00 00 00 00 00 00 20 06 00 43 43 64 50 04 01 02 02 00 00 00 01 00 00 00 00 00 50 72 70 15 e0 00 00 00 00 00 00 00 20 00 00 43 43 64 50 04 01 05 03 00 00 00 00 00 01 00 00 00 50 72 70 00 00 4e 20 00 00 00 00 00 20 00 00 43 43 64 50 04 01 08 01 00 01 00 00 00 00 00 00 00 50 72 70 01 5a 00 00 00 00 00 00 00 20 00 00 43 43 64 50 04 04 04 01 00 01 00 00 00 00 00 00 00 50 72 70 00 5c 00 00 00 00 00 00 00 20 00 00 43 43 64 50 04 08 00 80 00 00 00 04 00 00 00 00 00 50 72 70 00 00 00 00 00 00 00 00 00 20 00 00 43 43 64 50 04 08 01 80 00 00 00 04 00 00 00 00 00 50 72 70 00 00 00 00 00 00 00 00 00 20 00 00 43 43 64 50 04 08 02 80 00 00 00 04 00 00 00 00 00 50 72 70 08 00 08 00 08 00 08 00 00 20 00 00 43 43 64 50 04 08 03 80 00 00 00 04 00 00 00 00 00 43 73 74 00 00 00 00 00 00 00 00 00 20 64 50 43 43 64 50 04 08 04 80 00 00 00 02 00 00 00 00 00 00 00 00 04 00 08 00 00 20 72 70 00 20 64 50 43 43 64 50 04 08 05 80 00 00 00 01 00 00 00 00 00 00 00 00 08 00 43 45 00 20 72 70 00 20 64 50 43 43 64 50 04 08 06 80 00 00 00 02 00 00 00 00 00 00 00 00 00 00 08 00 00 20 72 70 00 20 64 50 43 43 64 50 04 0b 00 80 00 00 00 02 00 00 00 00 00 00 00 00 00 00 00 00 00 20 72 70 00 20 64 50 43 43 64 50 05 00 00 80 00 00 00 01 00 00 00 00 00 e0 00 00 00 00 00 00 00 20 72 70 00 20 64 50 43 43 64 50 05 00 02 80 00 00 00 01 00 00 00 00 00 00 4e 20 2a 00 19 00 00 20 72 70 00 20 64 50 43 43 64 50 05 01 0d 01 00 01 00 00 00 00 00 00 00 00 00 00 00 10 43 50 00 20 72 70 00 20 64 50 43 43 64 50 05 01 01 01 00 01 00 00 00 00 00 00 00 00 00 00 02 10 00 00 00 20 72 70 00 20 64 50 43 43 64 50 05 01 02 02 00 00 00 01 00 00 00 00 00 00 00 00 15 e0 00 00 00 20 72 70 00 20 64 50 43 43 64 50 05 01 05 03 00 00 00 00 00 01 00 00 00 00 00 00 00 00 4e 20 00 20 72 70 00 20 64 50 43 43 64 50 05 01 08 01 00 01 00 00 00 00 00 00 00 00 08 00 01 00 08 00 00 20 72 70 00 20 64 50 43 43 64 50 05 04 04 01 00 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 20 72 70 00 20 64 50 43 43 64 50 05 08 00 80 00 00 00 04 00 00 00 00 00 00 08 00 00 00 00 00 00 00 00 00 00 20 64 50 43 43 64 50 05 08 01 80 00 00 00 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 20 64 50 43 43 64 50 05 08 02 80 00 00 00 04 00 00 00 00 00 00 08 00 08 00 08 00 08 00 08 00 00 20 64 50 43 43 64 50 05 08 03 80 00 00 00 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 20 64 50 43 43 64 50 05 08 04 80 00 00 00 02 00 00 00 00 00 00 00 00 04 00 08 00 00 20 72 70 00 20 64 50 43 43 64 50 05 08 05 80 00 00 00 01 00 00 00 00 00 00 00 00 08 00 01 00 01 00 80 02"
    ]

    state = [bytes.fromhex(block) for block in raw_blocks]

    # Initialization complete marker, matching the reference simulator.
    state.append(command("InCm", b"\x01\x00\x00\x00", reserved=0x0000))
    return state

def bootstrap_mix_effects(profile):
    """Derive M/E topology and initial bus state from the startup stream."""
    result = {}
    for payload in initial_state(profile):
        for name, body in parse_commands(payload):
            if name == "_MeC" and len(body) >= 2:
                me = body[0]
                result.setdefault(me, {})["key_count"] = body[1]
            elif name == "PrgI" and len(body) >= 4:
                me, source = struct.unpack("!BxH", body[:4])
                result.setdefault(me, {})["program"] = source
            elif name == "PrvI" and len(body) >= 4:
                me, source = struct.unpack("!BxH", body[:4])
                result.setdefault(me, {})["preview"] = source
            elif name == "TMxP" and len(body) >= 2:
                me = body[0]
                result.setdefault(me, {})["mix_rate"] = body[1]
            elif name == "TrSS" and len(body) >= 5:
                me = body[0]
                result.setdefault(me, {})["transition_style"] = body[3]
                result.setdefault(me, {})["transition_selection"] = body[4]

    if not result:
        result[0] = {}

    for state in result.values():
        state.setdefault("key_count", 1)
        state.setdefault("program", 1)
        state.setdefault("preview", 2)
        state.setdefault("mix_rate", 25)
        state.setdefault("transition_style", 0)
        state.setdefault("transition_selection", 1)
        state["transition_position"] = 0
        state["upstream_keyers"] = [False] * state["key_count"]

    return dict(sorted(result.items()))


class ClientSession:
    def __init__(self, addr, client_id):
        self.addr = addr
        self.client_id = client_id
        self.session = 0x8000 + client_id
        self.established = False
        self.state_sent = False
        self.next_packet_id = 1
        self.seen_rx = set()

    def next_id(self):
        packet_id = self.next_packet_id
        self.next_packet_id += 1
        if self.next_packet_id >= 0x8000:
            self.next_packet_id = 0
        return packet_id


def send_state_packet(sock, client, payload):
    packet_id = client.next_id()
    pkt = make_packet(
        FLAG_COMMAND,
        client.session,
        packet_id=packet_id,
        payload=payload,
    )
    sock.sendto(pkt, client.addr)
    return packet_id


def broadcast_state(sock, clients, payload):
    for client in clients.values():
        if client.established:
            send_state_packet(sock, client, payload)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--model", default="tvstudio-hd")
    ap.add_argument(
        "--fps",
        type=float,
        default=25.0,
        help="transition timing base in frames per second (default: 25)",
    )
    ap.add_argument(
        "--list-models",
        action="store_true",
        help="list known ATEM profiles and exit",
    )
    args = ap.parse_args()

    if args.list_models:
        print(format_profiles())
        return

    profile = get_profile(args.model)
    if profile is None:
        raise SystemExit(
            f"Unknown model '{args.model}'. Use --list-models."
        )
    if not profile.get("implemented"):
        raise SystemExit(
            f"{profile['name']} profile is planned but not implemented yet. "
            "Use --list-models to see profile status."
        )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.bind, args.port))
    sock.settimeout(0.01)

    print("ATEM Mock")
    print(f"Model: {profile['name']} ({args.model})")
    print(f"Listening on UDP {args.bind}:{args.port}")
    print("Software Control / Companion can connect to this host.")
    print("Ctrl-C to stop.\n")

    clients = {}
    next_client_id = 1

    # Shared switcher state, derived from the selected model's startup capture.
    mix_effects = bootstrap_mix_effects(profile)
    downstream_keyers = [False, False]
    aux_sources = [1, 1]
    auto_transitions = {}

    print(
        "Detected M/E topology: "
        + ", ".join(
            f"M/E {me + 1} ({state['key_count']} keyers)"
            for me, state in mix_effects.items()
        )
    )

    supersource_boxes = [
        {
            "enabled": False,
            "source": 1,
            "x": 0,
            "y": 0,
            "size": 1000,
            "cropped": False,
            "crop_top": 0,
            "crop_bottom": 0,
            "crop_left": 0,
            "crop_right": 0,
        }
        for _ in range(4)
    ]
    supersource_properties = {
        "art_fill_source": 0,
        "art_cut_source": 0,
        "art_option": 0,
        "art_pre_multiplied": False,
        "art_clip": 0,
        "art_gain": 0,
        "art_invert_key": False,
    }

    while True:
        # Drive independent AUTO transitions for every M/E.
        now = time.monotonic()
        for me, transition in list(auto_transitions.items()):
            state = mix_effects[me]
            elapsed = now - transition["started"]
            duration = transition["duration"]
            progress = 1.0 if duration <= 0 else min(1.0, elapsed / duration)
            position = int(round(progress * 10000))

            if (
                position != transition["last_position"]
                and (
                    now - transition["last_sent"] >= 1.0 / args.fps
                    or position >= 10000
                )
            ):
                remaining = max(
                    0,
                    int(round((1.0 - progress) * transition["frames"])),
                )
                broadcast_state(
                    sock,
                    clients,
                    state_transition_position(
                        position,
                        in_transition=(position < 10000),
                        me=me,
                        remaining_frames=remaining,
                    ),
                )
                transition["last_position"] = position
                transition["last_sent"] = now

            if progress >= 1.0:
                state["program"], state["preview"] = (
                    state["preview"],
                    state["program"],
                )
                state["transition_position"] = 0
                broadcast_state(
                    sock,
                    clients,
                    state_program(state["program"], me)
                    + state_preview(state["preview"], me)
                    + state_transition_position(0, False, me),
                )
                print(
                    f"[{stamp()}] M/E {me + 1} AUTO COMPLETE -> "
                    f"PGM {state['program']} / PVW {state['preview']}"
                )
                del auto_transitions[me]

        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue

        p = parse_packet(data)
        if p is None:
            continue

        # Initial hello. A new UDP endpoint represents another ATEM control
        # client (Software Control, Companion, etc.).
        if (p["flags"] & FLAG_INIT) and p["payload"][:1] in (b"\x01", b"\x04"):
            client = clients.get(addr)
            if client is None or p["payload"][:1] == b"\x04":
                client = ClientSession(addr, next_client_id)
                clients[addr] = client
                next_client_id += 1
                if next_client_id >= 0x7FFF:
                    next_client_id = 1

            response_payload = struct.pack(
                "!HH4x", 0x0200, client.client_id
            )
            response = make_packet(
                FLAG_INIT,
                p["session"],
                payload=response_payload,
            )
            sock.sendto(response, addr)
            print(
                f"[{stamp()}] client {addr[0]}:{addr[1]} "
                f"-> handshake (id={client.client_id})"
            )
            continue

        client = clients.get(addr)
        if client is None:
            continue

        key = (
            p["flags"],
            p["session"],
            p["ack"],
            p["packet_id"],
            p["payload"],
        )
        first_time = key not in client.seen_rx
        client.seen_rx.add(key)

        # Handshake completes when the client ACKs our INIT response while
        # using its assigned session id.
        if (
            not client.established
            and p["session"] == client.session
            and (p["flags"] & FLAG_ACK)
            and p["ack"] == 0
        ):
            client.established = True
            print(
                f"[{stamp()}] session established: "
                f"{addr[0]}:{addr[1]} / 0x{client.session:04x}"
            )

        if not client.established:
            continue

        # ACK every control packet/retransmission from this client.
        if p["flags"] & (FLAG_COMMAND | FLAG_RETRANSMIT):
            ack = make_packet(
                FLAG_ACK,
                client.session,
                ack=p["packet_id"],
            )
            sock.sendto(ack, client.addr)

        # Each newly connected controller receives a full ATEM initialization
        # stream followed by the current live state of every M/E.
        if not client.state_sent:
            for payload in initial_state(profile):
                send_state_packet(sock, client, payload)

            for me, state in mix_effects.items():
                send_state_packet(sock, client, state_program(state["program"], me))
                send_state_packet(sock, client, state_preview(state["preview"], me))

            client.state_sent = True
            print(
                f"[{stamp()}] initialization sent to "
                f"{addr[0]}:{addr[1]}"
            )

        # Process control commands. State changes are broadcast to every
        # connected ATEM client so Software Control and Companion stay synced.
        if p["payload"]:
            for cmd_name, body in parse_commands(p["payload"]):
                response_payload = None

                if cmd_name == "CPgI" and len(body) >= 4:
                    me, source = struct.unpack("!BxH", body[:4])
                    if me in mix_effects:
                        state = mix_effects[me]
                        state["program"] = source
                        response_payload = state_program(source, me)
                        print(
                            f"[{stamp()}] M/E {me + 1} PROGRAM -> input {source} "
                            f"from {addr[0]}:{addr[1]}"
                        )

                elif cmd_name == "CPvI" and len(body) >= 4:
                    me, source = struct.unpack("!BxH", body[:4])
                    if me in mix_effects:
                        state = mix_effects[me]
                        state["preview"] = source
                        response_payload = state_preview(source, me)
                        print(
                            f"[{stamp()}] M/E {me + 1} PREVIEW -> input {source} "
                            f"from {addr[0]}:{addr[1]}"
                        )

                elif cmd_name == "DCut" and len(body) >= 1:
                    me = body[0]
                    if me in mix_effects:
                        state = mix_effects[me]
                        auto_transitions.pop(me, None)
                        state["transition_position"] = 0
                        state["program"], state["preview"] = (
                            state["preview"],
                            state["program"],
                        )
                        response_payload = (
                            state_program(state["program"], me)
                            + state_preview(state["preview"], me)
                        )
                        print(
                            f"[{stamp()}] M/E {me + 1} CUT -> "
                            f"PGM {state['program']} / PVW {state['preview']}"
                        )

                elif cmd_name == "DAut" and len(body) >= 1:
                    me = body[0]
                    if me in mix_effects and me not in auto_transitions:
                        state = mix_effects[me]
                        frames = max(1, state["mix_rate"])
                        duration = frames / max(1.0, args.fps)
                        auto_transitions[me] = {
                            "started": time.monotonic(),
                            "duration": duration,
                            "frames": frames,
                            "last_position": -1,
                            "last_sent": 0.0,
                        }
                        state["transition_position"] = 0
                        response_payload = state_transition_position(
                            0,
                            in_transition=True,
                            me=me,
                            remaining_frames=frames,
                        )
                        print(
                            f"[{stamp()}] M/E {me + 1} AUTO START -> "
                            f"{frames} frames / {duration:.3f}s"
                        )

                elif cmd_name == "CKOn" and len(body) >= 3:
                    me = body[0]
                    keyer_id = body[1]
                    on_air = body[2] == 1
                    if (
                        me in mix_effects
                        and keyer_id < len(mix_effects[me]["upstream_keyers"])
                    ):
                        mix_effects[me]["upstream_keyers"][keyer_id] = on_air
                        response_payload = state_usk_on_air(keyer_id, on_air, me)
                        print(
                            f"[{stamp()}] M/E {me + 1} USK {keyer_id + 1} -> "
                            f"{'ON AIR' if on_air else 'OFF'}"
                        )

                elif cmd_name == "CDsL" and len(body) >= 2:
                    dsk_id = body[0]
                    on_air = body[1] == 1
                    if dsk_id < len(downstream_keyers):
                        downstream_keyers[dsk_id] = on_air
                        response_payload = state_dsk(dsk_id, on_air)
                        print(
                            f"[{stamp()}] DSK {dsk_id + 1} -> "
                            f"{'ON AIR' if on_air else 'OFF'}"
                        )

                elif cmd_name == "DDsA" and len(body) >= 2:
                    # Protocol v8+: byte 1 is the DSK id.
                    dsk_id = body[1]
                    if dsk_id < len(downstream_keyers):
                        downstream_keyers[dsk_id] = not downstream_keyers[dsk_id]
                        response_payload = state_dsk(
                            dsk_id,
                            downstream_keyers[dsk_id],
                        )
                        print(
                            f"[{stamp()}] DSK {dsk_id + 1} AUTO -> "
                            f"{'ON AIR' if downstream_keyers[dsk_id] else 'OFF'}"
                        )

                elif cmd_name == "CAuS" and len(body) >= 4:
                    aux_bus = body[1]
                    source = struct.unpack("!H", body[2:4])[0]
                    if aux_bus < len(aux_sources):
                        aux_sources[aux_bus] = source
                        response_payload = state_aux(aux_bus, source)
                        print(
                            f"[{stamp()}] AUX {aux_bus + 1} -> input {source}"
                        )

                elif cmd_name == "CTTp" and len(body) >= 4:
                    flags = body[0]
                    me = body[1]
                    if me in mix_effects:
                        state = mix_effects[me]
                        if flags & 0x01:
                            state["transition_style"] = body[2]
                        if flags & 0x02:
                            state["transition_selection"] = body[3]
                        response_payload = state_transition(
                            state["transition_style"],
                            state["transition_selection"],
                            me,
                        )
                        print(
                            f"[{stamp()}] M/E {me + 1} TRANSITION -> style "
                            f"{state['transition_style']}, selection "
                            f"0x{state['transition_selection']:02x}"
                        )

                elif cmd_name == "CTPs" and len(body) >= 4:
                    me = body[0]
                    if me in mix_effects:
                        state = mix_effects[me]
                        auto_transitions.pop(me, None)
                        state["transition_position"] = struct.unpack(
                            "!H", body[2:4]
                        )[0]
                        response_payload = state_transition_position(
                            state["transition_position"],
                            in_transition=(
                                state["transition_position"] not in (0, 10000)
                            ),
                            me=me,
                        )
                        print(
                            f"[{stamp()}] M/E {me + 1} T-BAR -> "
                            f"{state['transition_position']}"
                        )

                        if state["transition_position"] >= 10000:
                            state["program"], state["preview"] = (
                                state["preview"],
                                state["program"],
                            )
                            state["transition_position"] = 0
                            response_payload += (
                                state_program(state["program"], me)
                                + state_preview(state["preview"], me)
                                + state_transition_position(0, False, me)
                            )

                elif cmd_name == "CTMx" and len(body) >= 2:
                    me = body[0]
                    if me in mix_effects:
                        state = mix_effects[me]
                        state["mix_rate"] = body[1]
                        response_payload = state_mix_rate(state["mix_rate"], me)
                        print(
                            f"[{stamp()}] M/E {me + 1} MIX RATE -> "
                            f"{state['mix_rate']} frames"
                        )

                elif cmd_name == "CSBP" and len(body) >= 24:
                    flags = struct.unpack("!H", body[0:2])[0]
                    ssrc_id = body[2]
                    box_id = body[3]
                    if ssrc_id == 0 and box_id < len(supersource_boxes):
                        box = supersource_boxes[box_id]

                        if flags & (1 << 0):
                            box["enabled"] = body[4] == 1
                        if flags & (1 << 1):
                            box["source"] = struct.unpack("!H", body[6:8])[0]
                        if flags & (1 << 2):
                            box["x"] = struct.unpack("!h", body[8:10])[0]
                        if flags & (1 << 3):
                            box["y"] = struct.unpack("!h", body[10:12])[0]
                        if flags & (1 << 4):
                            box["size"] = struct.unpack("!H", body[12:14])[0]
                        if flags & (1 << 5):
                            box["cropped"] = body[14] == 1
                        if flags & (1 << 6):
                            box["crop_top"] = struct.unpack("!H", body[16:18])[0]
                        if flags & (1 << 7):
                            box["crop_bottom"] = struct.unpack("!H", body[18:20])[0]
                        if flags & (1 << 8):
                            box["crop_left"] = struct.unpack("!H", body[20:22])[0]
                        if flags & (1 << 9):
                            box["crop_right"] = struct.unpack("!H", body[22:24])[0]

                        response_payload = state_supersource_box(box_id, box)
                        print(
                            f"[{stamp()}] SUPERSOURCE BOX {box_id + 1} -> "
                            f"{'ON' if box['enabled'] else 'OFF'}, "
                            f"src {box['source']}, x {box['x']}, "
                            f"y {box['y']}, size {box['size']}"
                        )

                elif cmd_name == "CSSc" and len(body) >= 13:
                    flags = body[0]
                    ssrc_id = body[1]
                    if ssrc_id == 0:
                        if flags & (1 << 0):
                            supersource_properties["art_fill_source"] = struct.unpack(
                                "!H", body[2:4]
                            )[0]
                        if flags & (1 << 1):
                            supersource_properties["art_cut_source"] = struct.unpack(
                                "!H", body[4:6]
                            )[0]
                        if flags & (1 << 2):
                            supersource_properties["art_option"] = body[6]
                        if flags & (1 << 3):
                            supersource_properties["art_pre_multiplied"] = body[7] == 1
                        if flags & (1 << 4):
                            supersource_properties["art_clip"] = struct.unpack(
                                "!H", body[8:10]
                            )[0]
                        if flags & (1 << 5):
                            supersource_properties["art_gain"] = struct.unpack(
                                "!H", body[10:12]
                            )[0]
                        if flags & (1 << 6):
                            supersource_properties["art_invert_key"] = body[12] == 1

                        response_payload = state_supersource_properties(
                            supersource_properties
                        )
                        print(f"[{stamp()}] SUPERSOURCE properties updated")

                if response_payload:
                    broadcast_state(sock, clients, response_payload)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
