"""Reading the data files of Fallout 3 and Fallout: New Vegas (and their kin), for tools/fnv_world.py.

    BSA     the game's archives (Fallout - Sound.bsa, Fallout - Voices1.bsa, ...): version 104.
            BSA(path).names() lists the files inside, read(name) gives one's bytes.
    Plugin  the master file (FalloutNV.esm): records in groups, some compressed. Plugin(path).records()
            walks every record, with the world it belongs to.

Only what the radio needs: no writing, no other versions. The formats are the community's
write-ups (UESP and the xEdit sources).
"""

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path


# --- BSA -----------------------------------------------------------------------------------------

class BSA:
    """A version 104 archive (Oblivion's is 103, Skyrim's 104 too, Skyrim SE's 105)."""

    def __init__(self, path):
        self.path = Path(path)
        with open(self.path, "rb") as f:
            head = f.read(36)
            magic, version, offset, flags, folders, files, folder_names, file_names, _ = \
                struct.unpack("<4sIIIIIIII", head)
            if magic != b"BSA\0" or version not in (103, 104):
                raise ValueError(f"{self.path.name} isn't a BSA this tool reads (version {version})")
            self._compressed = bool(flags & 0x4)
            self._embedded_names = version == 104 and bool(flags & 0x100)
            f.seek(offset)
            folder_recs = [struct.unpack("<QII", f.read(16)) for _ in range(folders)]
            entries = []                  # (folder, size, offset)
            for _, count, _ in folder_recs:
                n = f.read(1)[0]
                folder = f.read(n).rstrip(b"\0").decode("latin-1")
                for _ in range(count):
                    _, size, off = struct.unpack("<QII", f.read(16))
                    entries.append((folder, size, off))
            names = f.read(file_names).split(b"\0")
        self._files, self._names = {}, []
        for (folder, size, off), name in zip(entries, names):
            full = f"{folder}\\{name.decode('latin-1')}"
            self._files[full.lower()] = (size, off)
            self._names.append(full)

    def names(self):
        """Every file inside, as "folder\\name.ext" (read() takes them in any case)."""
        return list(self._names)

    def read(self, name):
        size, off = self._files[name.lower()]
        compressed = self._compressed != bool(size & 0x40000000)
        size &= 0x3FFFFFFF
        with open(self.path, "rb") as f:
            f.seek(off)
            if self._embedded_names:
                n = f.read(1)[0]
                f.read(n)
                size -= n + 1
            data = f.read(size)
        if compressed:
            return zlib.decompress(data[4:])
        return data


# --- plugins (.esm / .esp) ------------------------------------------------------------------------

@dataclass
class Record:
    type: str
    form_id: int
    flags: int
    data: bytes
    world: int = 0            # the WRLD this record sits under (0: none)
    topic: int = 0            # an INFO's DIAL (the topic it answers; 0: none)
    cell: int = 0             # a cell's children (LAND, REFR): that CELL's form id
    fields: dict = field(default=None, repr=False)

    def subrecords(self):
        """[(type, bytes)] in order (XXXX, which gives the next one's size, is handled)."""
        out, d, i, big = [], self.data, 0, None
        while i + 6 <= len(d):
            kind, size = struct.unpack_from("<4sH", d, i)
            i += 6
            if big is not None:
                size, big = big, None
            if kind == b"XXXX":
                big = struct.unpack_from("<I", d, i)[0]
                i += size
                continue
            out.append((kind.decode("latin-1"), d[i:i + size]))
            i += size
        return out

    def get(self, kind, default=None):
        """The first subrecord of this type."""
        if self.fields is None:
            self.fields = {}
            for k, v in self.subrecords():
                self.fields.setdefault(k, v)
        return self.fields.get(kind, default)

    def text(self, kind):
        v = self.get(kind)
        return v.split(b"\0", 1)[0].decode("cp1252", "replace") if v is not None else ""

    def uint(self, kind):
        v = self.get(kind)
        return struct.unpack_from("<I", v)[0] if v is not None and len(v) >= 4 else None


class Plugin:
    """FalloutNV.esm and the like (Fallout 3 / New Vegas: 24-byte record and group headers)."""

    HEADER = 24

    def __init__(self, path):
        self.path = Path(path)
        self.raw = self.path.read_bytes()

    def records(self, types=None):
        """Every record (of these types, if given), with .world set for those under a WRLD and
        .topic for the INFOs of a DIAL."""
        types = {t.encode() for t in types} if types else None
        yield from self._walk(0, len(self.raw), 0, 0, 0, types)

    def _walk(self, start, end, world, topic, cell, types):
        raw, i = self.raw, start
        while i + self.HEADER <= end:
            kind = raw[i:i + 4]
            if kind == b"GRUP":
                size, label, gtype = struct.unpack_from("<I4si", raw, i + 4)
                # A world's children (group type 1) carry its form id: everything in them is in that
                # world. A topic's children (type 7) carry the DIAL's: its INFOs. A cell's (6), the CELL's.
                parent = struct.unpack("<I", label)[0]
                yield from self._walk(i + self.HEADER, i + size, parent if gtype == 1 else world,
                                      parent if gtype == 7 else topic, parent if gtype == 6 else cell, types)
                i += size
                continue
            size, flags, form_id = struct.unpack_from("<III", raw, i + 4)
            body = i + self.HEADER
            if types is None or kind in types:
                data = raw[body:body + size]
                if flags & 0x00040000:          # compressed: 4 bytes of the full size, then zlib
                    data = _inflate(data[4:])
                yield Record(kind.decode("latin-1"), form_id, flags, data, world, topic, cell)
            i = body + size


def _inflate(data):
    """zlib data, ignoring its checksum: a few of the game's records have a wrong one."""
    try:
        return zlib.decompress(data)
    except zlib.error:
        return zlib.decompressobj(-15).decompress(data[2:])


def heights(land):
    """A LAND record's 33 x 33 terrain heights (rows south to north, columns west to east), or None.
    VHGT holds an offset and each point's step from the one before it (the first of a row: from
    the row below's first), in units of 8."""
    v = land.get("VHGT")
    if v is None or len(v) < 4 + 33 * 33:
        return None
    base = struct.unpack_from("<f", v)[0]
    steps = struct.unpack_from("<1089b", v, 4)
    out, row_start = [], base
    for r in range(33):
        row_start += steps[r * 33]
        h, row = row_start, [row_start * 8]
        for c in range(1, 33):
            h += steps[r * 33 + c]
            row.append(h * 8)
        out.append(row)
    return out


def position(record):
    """A placed reference's (x, y, z) from its DATA: position then rotation, six floats."""
    d = record.get("DATA")
    return struct.unpack_from("<3f", d) if d is not None and len(d) >= 12 else None
