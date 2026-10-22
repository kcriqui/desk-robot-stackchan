"""Pack esp-sr speech models into the srmodels.bin the robot loads its wake
word from, and send it to the robot over WiFi.

    python tools/srmodels.py pack                 # sr_models/ -> .pio/srmodels.bin
    python tools/srmodels.py upload               # pack, then OTA it to the robot
    python tools/srmodels.py unpack <bin> <dir>   # the reverse, for checking

The format is esp-sr's (model/pack_model.py in espressif/esp-sr): a u32
model count, then per model a 32-byte name, a u32 file count and per file a
32-byte name, u32 offset and u32 length, then all the file data in order.
Written here so the build doesn't run third-party scripts; check.py-style
proof: unpacking the srmodels.bin that ships with the Arduino core and
packing it again gives the same bytes.

The robot reads it from the flash partition labelled "spiffs" (the default
16 MB layout's spare 3.4 MB data partition; nothing else uses it), so no
partition change and no USB cable are needed: ArduinoOTA writes a "-s"
(filesystem) upload straight into that partition.
"""

from __future__ import annotations

import configparser
import os
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # firmware-stackchan/
MODELS = HERE / "sr_models"
OUT = HERE / ".pio" / "srmodels.bin"


def pack(models: list[tuple[str, list[tuple[str, bytes]]]]) -> bytes:
    """[(model name, [(file name, data), ...]), ...] -> srmodels.bin bytes."""
    name32 = lambda s: s.encode().ljust(32, b"\0")
    n_files = sum(len(files) for _, files in models)
    header_len = 4 + len(models) * (32 + 4) + n_files * (32 + 4 + 4)
    head, data = [struct.pack("<I", len(models))], []
    at = header_len
    for name, files in models:
        assert len(name) < 32, name
        head += [name32(name), struct.pack("<I", len(files))]
        for fname, blob in files:
            assert len(fname) < 32, fname
            head += [name32(fname), struct.pack("<II", at, len(blob))]
            data.append(blob)
            at += len(blob)
    return b"".join(head + data)


def unpack(blob: bytes) -> list[tuple[str, list[tuple[str, bytes]]]]:
    n = struct.unpack_from("<I", blob, 0)[0]
    off, out = 4, []
    for _ in range(n):
        name = blob[off:off + 32].split(b"\0")[0].decode()
        count = struct.unpack_from("<I", blob, off + 32)[0]
        off += 36
        files = []
        for _ in range(count):
            fname = blob[off:off + 32].split(b"\0")[0].decode()
            start, length = struct.unpack_from("<II", blob, off + 32)
            files.append((fname, blob[start:start + length]))
            off += 40
        out.append((name, files))
    return out


def from_dir(root: Path) -> list[tuple[str, list[tuple[str, bytes]]]]:
    """Every model folder under root, in name order; files in name order."""
    return [(d.name, [(f.name, f.read_bytes()) for f in sorted(d.iterdir()) if f.is_file()])
            for d in sorted(root.iterdir()) if d.is_dir()]


def build() -> Path:
    models = from_dir(MODELS)
    if not models:
        sys.exit(f"no models in {MODELS}")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_bytes(pack(models))
    print(f"packed {', '.join(m for m, _ in models)} -> {OUT} ({OUT.stat().st_size} bytes)")
    return OUT


def upload() -> None:
    """OTA the models into the robot's "spiffs" partition. The host and
    password come from ota.ini (git-ignored), like a firmware upload."""
    path = build()
    ini = configparser.ConfigParser()
    ini.read(HERE / "ota.ini")
    sect = next((s for s in ini.sections() if ini.has_option(s, "upload_port")), None)
    if sect is None:
        sys.exit("ota.ini has no upload_port")
    host = ini.get(sect, "upload_port")
    flags = ini.get(sect, "upload_flags", fallback="")
    password = next((l.split("=", 1)[1].strip() for l in flags.split() if l.startswith("--auth=")), "")
    espota = Path.home() / ".platformio/packages/framework-arduinoespressif32/tools/espota.py"
    print(f"uploading to {host}'s model partition...")
    rc = subprocess.call([sys.executable, str(espota), "-i", host, "-p", "3232",
                          f"--auth={password}", "-s", "-f", str(path)])
    sys.exit(rc)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pack"
    if cmd == "pack":
        build()
    elif cmd == "upload":
        upload()
    elif cmd == "unpack" and len(sys.argv) == 4:
        dest = Path(sys.argv[3])
        for name, files in unpack(Path(sys.argv[2]).read_bytes()):
            (dest / name).mkdir(parents=True, exist_ok=True)
            for fname, blob in files:
                (dest / name / fname).write_bytes(blob)
            print(name, [f for f, _ in files])
    else:
        sys.exit(__doc__)
