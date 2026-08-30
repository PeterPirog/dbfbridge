"""Polish OEM codepages (Mazovia / PIAST) for legacy FoxPro/Clipper data.

This is the single source of truth for the one-byte Mazovia table and the
Python codec registration.  The historical ``dbf_bridge.exporter.polish_codecs``
module re-exports these names for backwards compatibility.

The table maps bytes 0x00-0xFF to Unicode.  Positions 0x00-0x7F are ASCII,
0xA0-0xFF follow the cp1250 layout, and 0x80-0x9F hold the Polish letters in
the Mazovia OEM layout (different from cp1250 in that range).
"""

from __future__ import annotations

import codecs
from typing import Any

from dbfread.codepages import codepages as _DBFREAD_CODEPAGES

#: Language driver bytes known to the DBF/VFP format literature but missing
#: from dbfread's table.  0x69 is the Polish "Mazovia (Polish) MS-DOS" driver.
EXTRA_DRIVER_ENCODINGS: dict[int, str] = {
    0x69: "mazovia",
}


def driver_to_encoding(language_driver: int) -> str | None:
    """Resolve a DBF language driver byte to a Python codec name.

    Returns ``None`` when the driver byte is unknown.  Drivers mapped to the
    custom Polish pages are registered with :mod:`codecs` on demand, so a
    successful return value can always be used with ``bytes.decode``.
    """
    registered = _DBFREAD_CODEPAGES.get(language_driver)
    if registered is not None and registered[0] is not None:
        return registered[0]
    extra = EXTRA_DRIVER_ENCODINGS.get(language_driver)
    if extra is not None:
        register_polish_codecs()
    return extra


def decode_with_polish_fallback(
    text: bytes,
    primary: str,
    errors: str = "strict",
) -> tuple[str, str | None]:
    """Decode *text* with the Polish codepage fallback chain.

    Returns ``(decoded, source)`` where *source* is the encoding that
    produced the text.  Non-strict policies decode with *primary* directly
    (and *source* is *primary*); in strict mode the declared encoding is
    tried first, then the Polish fallbacks, and only as a last resort a lossy
    ``replace`` decode is returned with *source* set to ``None``.
    """
    if not text:
        return "", primary
    if errors != "strict":
        return text.decode(primary, errors=errors), primary
    try:
        return text.decode(primary, errors="strict"), primary
    except UnicodeDecodeError:
        register_polish_codecs()
        for alternative in POLISH_FALLBACK_ENCODINGS:
            if alternative == primary:
                continue
            try:
                return text.decode(alternative, errors="strict"), alternative
            except (UnicodeDecodeError, LookupError):
                continue
        return text.decode(primary, errors="replace"), None


_MAZOVIA_TABLE: dict[int, str] = {
    0x00: "\x00",
    0x01: "\x01",
    0x02: "\x02",
    0x03: "\x03",
    0x04: "\x04",
    0x05: "\x05",
    0x06: "\x06",
    0x07: "\x07",
    0x08: "\x08",
    0x09: "\t",
    0x0A: "\n",
    0x0B: "\x0b",
    0x0C: "\x0c",
    0x0D: "\r",
    0x0E: "\x0e",
    0x0F: "\x0f",
    0x10: "\x10",
    0x11: "\x11",
    0x12: "\x12",
    0x13: "\x13",
    0x14: "\x14",
    0x15: "\x15",
    0x16: "\x16",
    0x17: "\x17",
    0x18: "\x18",
    0x19: "\x19",
    0x1A: "\x1a",
    0x1B: "\x1b",
    0x1C: "\x1c",
    0x1D: "\x1d",
    0x1E: "\x1e",
    0x1F: "\x1f",
    0x20: " ",
    0x21: "!",
    0x22: '"',
    0x23: "#",
    0x24: "$",
    0x25: "%",
    0x26: "&",
    0x27: "'",
    0x28: "(",
    0x29: ")",
    0x2A: "*",
    0x2B: "+",
    0x2C: ",",
    0x2D: "-",
    0x2E: ".",
    0x2F: "/",
    0x30: "0",
    0x31: "1",
    0x32: "2",
    0x33: "3",
    0x34: "4",
    0x35: "5",
    0x36: "6",
    0x37: "7",
    0x38: "8",
    0x39: "9",
    0x3A: ":",
    0x3B: ";",
    0x3C: "<",
    0x3D: "=",
    0x3E: ">",
    0x3F: "?",
    0x40: "@",
    0x41: "A",
    0x42: "B",
    0x43: "C",
    0x44: "D",
    0x45: "E",
    0x46: "F",
    0x47: "G",
    0x48: "H",
    0x49: "I",
    0x4A: "J",
    0x4B: "K",
    0x4C: "L",
    0x4D: "M",
    0x4E: "N",
    0x4F: "O",
    0x50: "P",
    0x51: "Q",
    0x52: "R",
    0x53: "S",
    0x54: "T",
    0x55: "U",
    0x56: "V",
    0x57: "W",
    0x58: "X",
    0x59: "Y",
    0x5A: "Z",
    0x5B: "[",
    0x5C: "\\",
    0x5D: "]",
    0x5E: "^",
    0x5F: "_",
    0x60: "`",
    0x61: "a",
    0x62: "b",
    0x63: "c",
    0x64: "d",
    0x65: "e",
    0x66: "f",
    0x67: "g",
    0x68: "h",
    0x69: "i",
    0x6A: "j",
    0x6B: "k",
    0x6C: "l",
    0x6D: "m",
    0x6E: "n",
    0x6F: "o",
    0x70: "p",
    0x71: "q",
    0x72: "r",
    0x73: "s",
    0x74: "t",
    0x75: "u",
    0x76: "v",
    0x77: "w",
    0x78: "x",
    0x79: "y",
    0x7A: "z",
    0x7B: "{",
    0x7C: "|",
    0x7D: "}",
    0x7E: "~",
    0x7F: "\x7f",
    0x80: "ą",
    0x81: "ą",
    0x82: "ć",
    0x83: "ę",
    0x84: "ł",
    0x85: "ń",
    0x86: "ó",
    0x87: "ś",
    0x88: "ź",
    0x89: "ż",
    0x8A: "Ż",
    0x8B: "Ź",
    0x8C: "ć",
    0x8D: "ń",
    0x8E: "ó",
    0x8F: "Ś",
    0x90: "Ó",
    0x91: "ą",
    0x92: "ę",
    0x93: "ł",
    0x94: "ń",
    0x95: "ó",
    0x96: "ś",
    0x97: "ź",
    0x98: "ż",
    0x99: "Ż",
    0x9A: "Ź",
    0x9B: "ć",
    0x9C: "ń",
    0x9D: "ó",
    0x9E: "Ś",
    0x9F: "Ó",
    0xA0: "\u00a0",
    0xA1: "Ą",
    0xA2: "˘",
    0xA3: "Ł",
    0xA4: "¤",
    0xA5: "Ľ",
    0xA6: "Ś",
    0xA7: "§",
    0xA8: "¨",
    0xA9: "©",
    0xAA: "Ş",
    0xAB: "«",
    0xAC: "¬",
    0xAD: "\u00ad",
    0xAE: "Ž",
    0xAF: "Ż",
    0xB0: "°",
    0xB1: "ą",
    0xB2: "˛",
    0xB3: "ł",
    0xB4: "´",
    0xB5: "ľ",
    0xB6: "ś",
    0xB7: "ˇ",
    0xB8: "¸",
    0xB9: "š",
    0xBA: "ş",
    0xBB: "»",
    0xBC: "Ľ",
    0xBD: "˝",
    0xBE: "ž",
    0xBF: "ż",
    0xC0: "Ŕ",
    0xC1: "Á",
    0xC2: "Â",
    0xC3: "Ă",
    0xC4: "Ä",
    0xC5: "Ĺ",
    0xC6: "Ć",
    0xC7: "Ç",
    0xC8: "Č",
    0xC9: "É",
    0xCA: "Ę",
    0xCB: "Ë",
    0xCC: "Ě",
    0xCD: "Í",
    0xCE: "Î",
    0xCF: "Ď",
    0xD0: "Đ",
    0xD1: "Ń",
    0xD2: "Ň",
    0xD3: "Ó",
    0xD4: "Ô",
    0xD5: "Ő",
    0xD6: "Ö",
    0xD7: "×",
    0xD8: "Ř",
    0xD9: "Ů",
    0xDA: "Ú",
    0xDB: "Ű",
    0xDC: "Ü",
    0xDD: "Ý",
    0xDE: "Ţ",
    0xDF: "ß",
    0xE0: "ŕ",
    0xE1: "á",
    0xE2: "â",
    0xE3: "ă",
    0xE4: "ä",
    0xE5: "ĺ",
    0xE6: "ć",
    0xE7: "ç",
    0xE8: "č",
    0xE9: "é",
    0xEA: "ę",
    0xEB: "ë",
    0xEC: "ě",
    0xED: "í",
    0xEE: "î",
    0xEF: "ď",
    0xF0: "đ",
    0xF1: "ń",
    0xF2: "ň",
    0xF3: "ó",
    0xF4: "ô",
    0xF5: "ő",
    0xF6: "ö",
    0xF7: "÷",
    0xF8: "ř",
    0xF9: "ů",
    0xFA: "ú",
    0xFB: "ű",
    0xFC: "ü",
    0xFD: "ý",
    0xFE: "ţ",
    0xFF: "˙",
}


class TableCodec(codecs.Codec):
    """A one-byte codec built from a ``{byte: char}`` table."""

    def __init__(self, name: str, table: dict[int, str]) -> None:
        self.name = name
        self.table = table
        self.reverse: dict[str, bytes] = {char: bytes([b]) for b, char in table.items()}

    def encode(self, input: str, errors: str = "strict") -> tuple[bytes, int]:
        output = bytearray()
        for i, char in enumerate(input):
            if char in self.reverse:
                output.extend(self.reverse[char])
            else:
                if errors == "strict":
                    raise UnicodeEncodeError(
                        self.name, input, i, i + 1, f"character {char!r} not in {self.name}"
                    )
                if errors == "replace":
                    output.append(0x3F)
                elif errors == "ignore":
                    pass
                else:
                    raise ValueError(f"unsupported errors policy: {errors}")
        return bytes(output), len(input)

    def decode(self, input: bytes, errors: str = "strict") -> tuple[str, int]:
        output: list[str] = []
        for i, byte in enumerate(input):
            if byte in self.table:
                output.append(self.table[byte])
            else:
                if errors == "strict":
                    raise UnicodeDecodeError(
                        self.name, input, i, i + 1, f"byte 0x{byte:02X} not in {self.name}"
                    )
                if errors == "replace":
                    output.append("\ufffd")
                elif errors == "ignore":
                    pass
                else:
                    raise ValueError(f"unsupported errors policy: {errors}")
        return "".join(output), len(input)


_REGISTERED = False


def register_polish_codecs() -> None:
    """Register the Polish OEM codepages (Mazovia, PIAST) with :mod:`codecs`.

    Idempotent: repeated calls are cheap no-ops.
    """
    global _REGISTERED
    if _REGISTERED:
        return

    def _make_search(name: str, table: dict[int, str]) -> Any:
        codec = TableCodec(name, table)

        def _search(_encoding: str) -> codecs.CodecInfo | None:
            if _encoding.lower() == name:
                return codecs.CodecInfo(  # type: ignore[arg-type]
                    name=name,
                    encode=codec.encode,
                    decode=codec.decode,  # type: ignore[arg-type]
                )
            return None

        return _search

    codecs.register(_make_search("mazovia", _MAZOVIA_TABLE))
    # PIAST is a close relative of the Mazovia OEM page; register the same
    # table under its common alias names.
    codecs.register(_make_search("piast", _MAZOVIA_TABLE))
    codecs.register(_make_search("pki", _MAZOVIA_TABLE))

    _REGISTERED = True


#: Fallback codepages tried (in order) when the declared header codepage
#: cannot decode a value.  cp1250 is the VFP-declared page, cp852 the DOS
#: Latin-2 page, then the Polish OEM pages.
POLISH_FALLBACK_ENCODINGS: tuple[str, ...] = (
    "cp1250",
    "cp852",
    "mazovia",
    "piast",
)

__all__ = [
    "EXTRA_DRIVER_ENCODINGS",
    "POLISH_FALLBACK_ENCODINGS",
    "TableCodec",
    "decode_with_polish_fallback",
    "driver_to_encoding",
    "register_polish_codecs",
]
