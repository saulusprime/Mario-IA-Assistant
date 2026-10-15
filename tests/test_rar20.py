"""Estrazione dei RAR 2.0 (moduli tracker .mdr) in Python: intestazioni, tabelle di Huffman, letterali, copie
corte, ripetizione dell'ultima copia e CRC32, su un archivio costruito bit per bit (gli archivi veri sono musica
© Nintendo e restano fuori dal repository; la modalità audio è verificata sui 107 archivi scaricati)."""
import importlib.util
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from mario import rar20

PROJECT = Path(__file__).resolve().parent.parent


class BitWriter:
    def __init__(self):
        self.bits = []

    def put(self, value, count):
        self.bits += [(value >> (count - 1 - i)) & 1 for i in range(count)]

    def data(self):
        bits = self.bits + [0] * (-len(self.bits) % 8)
        return bytes(int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))


def compressed_stream():
    """'ababab!!!' compresso con l'algoritmo 2.0: due letterali, copia corta a distanza 2, ripetizione
    dell'ultima copia, un letterale e una copia sovrapposta a distanza 1."""
    w = BitWriter()
    w.put(0, 2)  # niente modalità audio, tabelle nuove
    bd = [0] * rar20.BC20
    bd[9], bd[17], bd[18] = 1, 2, 2  # codici delle lunghezze: 9 -> 0, 17 -> 10, 18 -> 11
    for n in bd:
        w.put(n, 4)
    for _ in range(257):
        w.put(0, 1)  # lunghezza 9 per i 256 letterali e per il simbolo 256 (ripeti l'ultima copia)
    w.put(0b10, 2)
    w.put(1, 3)  # 17: 4 zeri (simboli 257-260)
    w.put(0, 1)  # simbolo 261 (copia corta) lungo 9
    w.put(0b11, 2)
    w.put(101, 7)  # 18: 112 zeri fino alla fine delle tre tabelle (374 lunghezze)
    # codici canonici a 9 bit: letterale = valore, 256 = 256, 261 = 257
    w.put(ord("a"), 9)
    w.put(ord("b"), 9)
    w.put(257, 9)
    w.put(1, 2)  # copia corta: distanza 0 + 1 + 1 = 2, lunghezza 2
    w.put(256, 9)  # ancora 2 byte a distanza 2
    w.put(ord("!"), 9)
    w.put(257, 9)
    w.put(0, 2)  # distanza 1: copia sovrapposta
    return w.data()


def rar_archive(name, payload, size, crc, method=0x33, version=20, flags=0x8000):
    raw_name = name.encode()
    body = struct.pack("<IIBIIBBHI", len(payload), size, 2, crc, 0, version, method, len(raw_name), 0x20) + raw_name
    main = struct.pack("<HBHH", 0, 0x73, 0, 13) + bytes(6)
    return rar20.MARKER + main + struct.pack("<HBHH", 0, rar20.FILE_HEAD, flags, 7 + len(body)) + body + payload


TEXT = b"ababab!!!"


class Rar20Tests(unittest.TestCase):
    def test_unpack20_literals_and_copies(self):
        archive = rar_archive("Musica\\SONG.IT", compressed_stream(), len(TEXT), zlib.crc32(TEXT))
        entry = next(rar20.entries(archive))
        self.assertEqual((entry.name, entry.size, entry.version), ("Musica/SONG.IT", 9, 20))
        self.assertEqual(rar20.extract_first(archive), ("SONG.IT", TEXT))
        self.assertEqual(rar20.extract_first(archive, (".it",)), ("SONG.IT", TEXT))
        self.assertIsNone(rar20.extract_first(archive, (".xm",)))

    def test_errors_and_stored_files(self):
        with self.assertRaisesRegex(rar20.RarError, "CRC"):
            rar20.extract_first(rar_archive("a.it", compressed_stream(), len(TEXT), zlib.crc32(b"altro")))
        with self.assertRaisesRegex(rar20.RarError, "non supportata"):
            rar20.extract_first(rar_archive("a.it", b"x", 1, 0, version=29))
        with self.assertRaisesRegex(rar20.RarError, "cifrato"):
            rar20.extract_first(rar_archive("a.it", b"x", 1, 0, flags=0x8000 | rar20.FLAG_PASSWORD))
        with self.assertRaises(rar20.RarError):
            rar20.extract_first(b"PK\x03\x04")
        stored = rar_archive("dir/b.txt", b"ciao", 4, zlib.crc32(b"ciao"), method=rar20.METHOD_STORE)
        self.assertEqual(rar20.extract_first(stored), ("b.txt", b"ciao"))

    def test_fetch_extracts_mdr_with_python(self):
        spec = importlib.util.spec_from_file_location("fetch", PROJECT / "tools" / "fetch_mario_sounds.py")
        fetch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fetch)
        with tempfile.TemporaryDirectory() as tmp:
            mdr = Path(tmp) / "brano.mdr"
            mdr.write_bytes(rar_archive("SONG.IT", compressed_stream(), len(TEXT), zlib.crc32(TEXT)))
            module = fetch.extract_module(mdr)
            self.assertEqual(module, Path(tmp) / "brano" / "SONG.it")
            self.assertEqual(module.read_bytes(), TEXT)
            self.assertEqual(fetch.extract_module(mdr), module)  # già estratto: niente da rifare


if __name__ == "__main__":
    unittest.main()
