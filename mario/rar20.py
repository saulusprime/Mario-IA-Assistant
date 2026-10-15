"""Estrazione di archivi RAR 1.5-2.x (formato del 2001) in Python puro, per i moduli tracker `.mdr`
dell'archivio dei suoni.

I `.mdr` di The Mushroom Kingdom sono RAR con compressione 2.0 "Best", spesso in modalità audio (il RAR
2.0 ha un predittore dedicato ai campioni sonori). Il 7zip di Debian non ha il decompressore RAR, libarchive
non conosce il 2.0 e `unar` 1.10 fallisce su 11 dei 107 archivi: qui c'è l'algoritmo Unpack20 (lo stesso
dei sorgenti pubblici di unrar), controllato con il CRC32 scritto nell'archivio. Solo lettura, niente archivi
cifrati, multivolume o con compressione 1.5/2.9.
"""
from __future__ import annotations

import struct
import zlib
from typing import Iterator, NamedTuple, Optional

MARKER = b"Rar!\x1a\x07\x00"
FILE_HEAD = 0x74
LONG_BLOCK = 0x8000
FLAG_SPLIT = 0x01 | 0x02
FLAG_PASSWORD = 0x04
FLAG_SOLID = 0x10
FLAG_DIRECTORY = 0xE0
FLAG_LARGE = 0x100
METHOD_STORE = 0x30

NC20, DC20, RC20, BC20, MC20 = 298, 48, 28, 19, 257
LDECODE = (0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 20, 24, 28, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224)
LBITS = (0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5)
DDECODE = (0, 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024, 1536, 2048, 3072,
           4096, 6144, 8192, 12288, 16384, 24576, 32768, 49152, 65536, 98304, 131072, 196608, 262144, 327680, 393216,
           458752, 524288, 589824, 655360, 720896, 786432, 851968, 917504, 983040)
DBITS = (0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13, 14, 14, 15, 15,
         16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16)
SDDECODE = (0, 4, 8, 16, 32, 64, 128, 192)
SDBITS = (2, 2, 3, 4, 5, 6, 6, 6)


class RarError(ValueError):
    pass


class Entry(NamedTuple):
    name: str
    size: int
    crc: int
    version: int
    method: int
    flags: int
    data: bytes  # i byte compressi


def entries(archive: bytes) -> Iterator[Entry]:
    """I file di un archivio RAR 1.5-2.x (formato dei blocchi con l'intestazione 'Rar!\\x1a\\x07\\x00')."""
    if not archive.startswith(MARKER):
        raise RarError("non è un archivio RAR 1.5-4.x")
    pos = len(MARKER)
    while pos + 7 <= len(archive):
        _crc, kind, flags, size = struct.unpack_from("<HBHH", archive, pos)
        if size < 7:
            raise RarError("intestazione rovinata")
        add = struct.unpack_from("<I", archive, pos + 7)[0] if flags & LONG_BLOCK and pos + 11 <= len(archive) else 0
        if kind == FILE_HEAD:
            packed, unpacked, _os, crc, _time, version, method, name_size, _attr = struct.unpack_from(
                "<IIBIIBBHI", archive, pos + 7)
            off = pos + 32
            if flags & FLAG_LARGE:
                high_packed, high_unpacked = struct.unpack_from("<II", archive, off)
                packed += high_packed << 32
                unpacked += high_unpacked << 32
                off += 8
            name = archive[off:off + name_size].split(b"\x00")[0].decode("latin-1")
            start = pos + size
            yield Entry(name.replace("\\", "/"), unpacked, crc, version, method, flags, archive[start:start + packed])
            pos = start + packed
        else:
            pos += size + add


class _Bits:
    """Lettura a bit dal più significativo, come il BitInput di unrar."""

    def __init__(self, data: bytes):
        self.data = data + bytes(32)  # margine: un passo del ciclo legge al massimo 51 bit oltre il controllo
        self.pos = 0  # in bit
        self.limit = len(data) * 8

    def peek16(self) -> int:
        byte, bit = divmod(self.pos, 8)
        d = self.data
        return (((d[byte] << 16) | (d[byte + 1] << 8) | d[byte + 2]) >> (8 - bit)) & 0xFFFF

    def get(self, count: int) -> int:
        if count == 0:
            return 0
        value = self.peek16() >> (16 - count)
        self.pos += count
        return value

    def overrun(self) -> bool:
        return self.pos > self.limit + 64


class _Huffman:
    """Codici canonici di RAR (lunghezze da 1 a 15 bit, assegnati per lunghezza e poi per simbolo), con una
    tabella rapida per i codici fino a QUICK bit. Come in unrar, un codice fuori tabella vale il simbolo 0."""
    QUICK = 10

    def __init__(self, lengths):
        count = [0] * 16
        for n in lengths:
            count[n] += 1
        count[0] = 0
        self.limits = [0] * 16  # primo codice oltre la lunghezza n, allineato a 16 bit
        self.base = [0] * 16  # primo codice di lunghezza n
        self.first = [0] * 16  # posizione in `symbols` del primo codice di lunghezza n
        code = pos = 0
        for n in range(1, 16):
            self.base[n], self.first[n] = code, pos
            code += count[n]
            pos += count[n]
            self.limits[n] = code << (16 - n)
            code <<= 1
        self.symbols = [i for n in range(1, 16) for i, length in enumerate(lengths) if length == n]
        self.quick = [None] * (1 << self.QUICK)
        for index, symbol in enumerate(self.symbols):
            n = lengths[symbol]
            if n > self.QUICK:
                break
            code = self.base[n] + index - self.first[n]
            shift = self.QUICK - n
            for low in range(1 << shift):
                self.quick[(code << shift) | low] = (symbol, n)

    def decode(self, bits: _Bits) -> int:
        value = bits.peek16()
        hit = self.quick[value >> (16 - self.QUICK)]
        if hit is not None:
            bits.pos += hit[1]
            return hit[0]
        for n in range(self.QUICK + 1, 16):
            if value < self.limits[n]:
                break
        bits.pos += n
        index = self.first[n] + (value >> (16 - n)) - self.base[n]
        return self.symbols[index] if 0 <= index < len(self.symbols) else 0


class _Audio:
    __slots__ = ("k", "d1", "d2", "d3", "d4", "last_delta", "dif", "count", "last_char")

    def __init__(self):
        self.k = [0, 0, 0, 0, 0]
        self.d1 = self.d2 = self.d3 = self.d4 = 0
        self.last_delta = 0
        self.dif = [0] * 11
        self.count = 0
        self.last_char = 0


class _Unpack20:
    def __init__(self, data: bytes, size: int):
        self.bits = _Bits(data)
        self.size = size
        self.out = bytearray()
        self.old_table = [0] * (MC20 * 4)
        self.old_dist = [0, 0, 0, 0]
        self.old_dist_ptr = 0
        self.last_dist = self.last_length = 0
        self.audio_block = False
        self.channels = 1
        self.channel = 0
        self.channel_delta = 0
        self.audio = [_Audio() for _ in range(4)]
        self.tables: list[_Huffman] = []

    def read_tables(self) -> None:
        bits = self.bits
        flags = bits.peek16()
        self.audio_block = bool(flags & 0x8000)
        if not flags & 0x4000:
            self.old_table = [0] * (MC20 * 4)
        bits.pos += 2
        if self.audio_block:
            self.channels = ((flags >> 12) & 3) + 1
            if self.channel >= self.channels:
                self.channel = 0
            bits.pos += 2
            size = MC20 * self.channels
        else:
            size = NC20 + DC20 + RC20
        lengths = _Huffman([bits.get(4) for _ in range(BC20)])
        table = [0] * size
        i = 0
        while i < size:
            number = lengths.decode(bits)
            if number < 16:
                table[i] = (number + self.old_table[i]) & 15
                i += 1
            elif number == 16:
                if i == 0:
                    raise RarError("tabella rovinata")
                n = bits.get(2) + 3
                while n > 0 and i < size:
                    table[i] = table[i - 1]
                    i += 1
                    n -= 1
            else:
                n = bits.get(3) + 3 if number == 17 else bits.get(7) + 11
                while n > 0 and i < size:
                    table[i] = 0
                    i += 1
                    n -= 1
            if bits.overrun():
                raise RarError("dati finiti in mezzo alle tabelle")
        if self.audio_block:
            self.tables = [_Huffman(table[c * MC20:(c + 1) * MC20]) for c in range(self.channels)]
        else:
            self.tables = [_Huffman(table[:NC20]), _Huffman(table[NC20:NC20 + DC20]),
                           _Huffman(table[NC20 + DC20:])]
        self.old_table[:size] = table

    def copy(self, length: int, distance: int) -> None:
        self.old_dist[self.old_dist_ptr & 3] = distance
        self.old_dist_ptr += 1
        self.last_dist, self.last_length = distance, length
        out = self.out
        if distance > len(out) or distance <= 0:
            raise RarError("distanza oltre l'inizio del file")
        start = len(out) - distance
        if distance >= length:
            out += out[start:start + length]
        else:
            for k in range(length):
                out.append(out[start + k])

    def decode_audio(self, delta: int) -> int:
        v = self.audio[self.channel]
        k = v.k
        v.count += 1
        v.d4, v.d3 = v.d3, v.d2
        v.d2 = v.last_delta - v.d1
        v.d1 = v.last_delta
        pred = 8 * v.last_char + k[0] * v.d1 + k[1] * v.d2 + k[2] * v.d3 + k[3] * v.d4 + k[4] * self.channel_delta
        pred = (pred >> 3) & 0xFF
        ch = pred - delta
        d = (delta - 256 if delta >= 128 else delta) << 3
        dif = v.dif
        cd = self.channel_delta
        dif[0] += abs(d)
        dif[1] += abs(d - v.d1)
        dif[2] += abs(d + v.d1)
        dif[3] += abs(d - v.d2)
        dif[4] += abs(d + v.d2)
        dif[5] += abs(d - v.d3)
        dif[6] += abs(d + v.d3)
        dif[7] += abs(d - v.d4)
        dif[8] += abs(d + v.d4)
        dif[9] += abs(d - cd)
        dif[10] += abs(d + cd)
        delta_char = (ch - v.last_char) & 0xFF
        self.channel_delta = v.last_delta = delta_char - 256 if delta_char >= 128 else delta_char
        v.last_char = ch
        if v.count & 0x1F == 0:
            best = min(range(11), key=lambda i: (dif[i], i))
            v.dif = [0] * 11
            if best:
                which, up = divmod(best - 1, 2)
                if up and k[which] < 16:
                    k[which] += 1
                elif not up and k[which] >= -16:
                    k[which] -= 1
        return ch & 0xFF

    def run(self) -> bytes:
        bits, out, size = self.bits, self.out, self.size
        self.read_tables()
        while len(out) < size:
            if bits.overrun():
                raise RarError("dati compressi finiti prima del file")
            if self.audio_block:
                number = self.tables[self.channel].decode(bits)
                if number == 256:
                    self.read_tables()
                    continue
                out.append(self.decode_audio(number))
                self.channel = (self.channel + 1) % self.channels
                continue
            main, dist, rep = self.tables
            number = main.decode(bits)
            if number < 256:
                out.append(number)
            elif number > 269:
                number -= 270
                length = LDECODE[number] + 3 + bits.get(LBITS[number])
                dn = dist.decode(bits)
                distance = DDECODE[dn] + 1 + bits.get(DBITS[dn])
                if distance >= 0x2000:
                    length += 1
                    if distance >= 0x40000:
                        length += 1
                self.copy(length, distance)
            elif number == 269:
                self.read_tables()
            elif number == 256:
                self.copy(self.last_length, self.last_dist)
            elif number < 261:
                distance = self.old_dist[(self.old_dist_ptr - (number - 256)) & 3]
                ln = rep.decode(bits)
                length = LDECODE[ln] + 2 + bits.get(LBITS[ln])
                if distance >= 0x101:
                    length += 1
                    if distance >= 0x2000:
                        length += 1
                        if distance >= 0x40000:
                            length += 1
                self.copy(length, distance)
            else:
                number -= 261
                self.copy(2, SDDECODE[number] + 1 + bits.get(SDBITS[number]))
        return bytes(out[:size])


def unpack(entry: Entry) -> bytes:
    """Il contenuto di un file dell'archivio, controllato con il CRC32."""
    if entry.flags & FLAG_PASSWORD:
        raise RarError("archivio cifrato")
    if entry.flags & FLAG_SPLIT:
        raise RarError("archivio multivolume")
    if entry.flags & FLAG_SOLID:
        raise RarError("archivio solido non supportato")
    if entry.method == METHOD_STORE:
        data = entry.data[:entry.size]
    elif entry.version in (20, 26):
        data = _Unpack20(entry.data, entry.size).run()
    else:
        raise RarError(f"compressione RAR {entry.version / 10:.1f} non supportata (solo 2.0)")
    if zlib.crc32(data) != entry.crc:
        raise RarError("CRC sbagliato")
    return data


def extract_first(archive: bytes, suffixes: tuple[str, ...] = ()) -> Optional[tuple[str, bytes]]:
    """Il primo file (con una delle estensioni indicate, se ce ne sono) come (nome, contenuto)."""
    for entry in entries(archive):
        if entry.flags & FLAG_DIRECTORY == FLAG_DIRECTORY:
            continue
        if suffixes and not entry.name.lower().endswith(suffixes):
            continue
        return entry.name.rsplit("/", 1)[-1], unpack(entry)
    return None
