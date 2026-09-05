"""Minimal QR encoder, for printing a join URL you can scan from a phone.

Exists because the alternative is asking someone to type
`http://192.168.1.42:8787/?t=MJQGMSlZCXSy4ee-EC9pxQ` into Safari, by hand,
correctly, while standing up. A scannable code in the terminal removes the one
piece of friction between "the tool is running" and "it is on my phone".

Deliberately narrow: byte mode, error-correction levels L and M, versions 1-6
(up to 134 bytes at level L). That covers any plausible LAN URL and stops short
of version 7, which is where version-information blocks start and the encoder
would roughly double in size for no benefit here.

Implements ISO/IEC 18004: encoding, Reed-Solomon over GF(256), block
interleaving, function patterns, all eight data masks with the standard penalty
scoring, and BCH format information.

Verified in the tests by *decoding* the output with OpenCV across every
supported version and level, which is the property that actually matters — a
code only has to be readable by a scanner. It is not byte-identical to `segno`:
that library emits an extra zero pad codeword, which changes the data, the
winning mask and therefore every module. Both decode to the same string.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# GF(256) with the QR primitive polynomial 0x11D
# ---------------------------------------------------------------------------

_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_tables()


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(degree: int) -> list[int]:
    """Generator polynomial for `degree` error-correction codewords."""
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, coeff in enumerate(poly):
            nxt[j] ^= coeff
            nxt[j + 1] ^= _mul(coeff, _EXP[i])
        poly = nxt
    return poly


def _ec_codewords(data: list[int], count: int) -> list[int]:
    gen = _generator(count)
    rem = list(data) + [0] * count
    for i in range(len(data)):
        factor = rem[i]
        if factor == 0:
            continue
        for j, g in enumerate(gen):
            rem[i + j] ^= _mul(g, factor)
    return rem[len(data):]


# ---------------------------------------------------------------------------
# Version / ECC tables (versions 1-6, levels L and M)
# ---------------------------------------------------------------------------

#: (version, level) -> (ec codewords per block, [(block count, data codewords)])
_BLOCKS: dict[tuple[int, str], tuple[int, list[tuple[int, int]]]] = {
    (1, "L"): (7,  [(1, 19)]),
    (2, "L"): (10, [(1, 34)]),
    (3, "L"): (15, [(1, 55)]),
    (4, "L"): (20, [(1, 80)]),
    (5, "L"): (26, [(1, 108)]),
    (6, "L"): (18, [(2, 68)]),
    (1, "M"): (10, [(1, 16)]),
    (2, "M"): (16, [(1, 28)]),
    (3, "M"): (26, [(1, 44)]),
    (4, "M"): (18, [(2, 32)]),
    (5, "M"): (24, [(2, 43)]),
    (6, "M"): (16, [(4, 27)]),
}

#: Alignment-pattern centre coordinates per version.
_ALIGN: dict[int, list[int]] = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
}

#: Two-bit level indicator used in the format information.
_LEVEL_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}

MAX_VERSION = 6


class QrError(ValueError):
    """Raised when the payload will not fit in the supported versions."""


def _capacity(version: int, level: str) -> int:
    """Byte-mode payload capacity, in bytes."""
    _, groups = _BLOCKS[(version, level)]
    data_codewords = sum(count * size for count, size in groups)
    # 4-bit mode indicator + 8-bit character count for versions 1-9.
    return (data_codewords * 8 - 12) // 8


def _pick_version(length: int, level: str) -> int:
    for version in range(1, MAX_VERSION + 1):
        if length <= _capacity(version, level):
            return version
    raise QrError(
        f"{length} bytes exceeds {_capacity(MAX_VERSION, level)}, the limit for "
        f"version {MAX_VERSION} at level {level}"
    )


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def _encode_data(payload: bytes, version: int, level: str) -> list[int]:
    _, groups = _BLOCKS[(version, level)]
    total_data = sum(count * size for count, size in groups)

    bits: list[int] = []

    def put(value: int, width: int) -> None:
        for i in range(width - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)              # byte mode
    put(len(payload), 8)        # character count, 8 bits for versions 1-9
    for byte in payload:
        put(byte, 8)

    # Terminator, then pad to a byte boundary, then alternating pad codewords.
    put(0, min(4, total_data * 8 - len(bits)))
    while len(bits) % 8:
        bits.append(0)

    codewords = [int("".join(str(b) for b in bits[i:i + 8]), 2)
                 for i in range(0, len(bits), 8)]
    for pad in _cycle([0xEC, 0x11]):
        if len(codewords) >= total_data:
            break
        codewords.append(pad)

    return codewords


def _cycle(values: list[int]):
    while True:
        for v in values:
            yield v


def _interleave(codewords: list[int], version: int, level: str) -> list[int]:
    ec_per_block, groups = _BLOCKS[(version, level)]

    blocks: list[list[int]] = []
    offset = 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(codewords[offset:offset + size])
            offset += size

    ec_blocks = [_ec_codewords(block, ec_per_block) for block in blocks]

    out: list[int] = []
    for i in range(max(len(b) for b in blocks)):
        for block in blocks:
            if i < len(block):
                out.append(block[i])
    for i in range(ec_per_block):
        for block in ec_blocks:
            out.append(block[i])
    return out


# ---------------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------------

def _blank(size: int) -> tuple[list[list[int | None]], list[list[bool]]]:
    return ([[None] * size for _ in range(size)],
            [[False] * size for _ in range(size)])


def _place_function_patterns(m: list[list[int | None]], fixed: list[list[bool]],
                             version: int) -> None:
    size = len(m)

    def finder(row: int, col: int) -> None:
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = row + r, col + c
                if not (0 <= rr < size and 0 <= cc < size):
                    continue
                inner = 2 <= r <= 4 and 2 <= c <= 4
                ring = r in (0, 6) or c in (0, 6)
                on = 0 <= r <= 6 and 0 <= c <= 6 and (ring or inner)
                m[rr][cc] = 1 if on else 0
                fixed[rr][cc] = True

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    # Timing patterns.
    for i in range(8, size - 8):
        bit = 1 if i % 2 == 0 else 0
        m[6][i] = bit
        m[i][6] = bit
        fixed[6][i] = True
        fixed[i][6] = True

    # Alignment patterns, skipping any that would collide with a finder.
    centres = _ALIGN[version]
    for r in centres:
        for c in centres:
            if (r < 8 and c < 8) or (r < 8 and c > size - 9) or (r > size - 9 and c < 8):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    on = max(abs(dr), abs(dc)) != 1
                    m[r + dr][c + dc] = 1 if on else 0
                    fixed[r + dr][c + dc] = True

    # Reserve the format-information areas.
    #
    # The two ranges are deliberately different lengths. Copy 2 is 15 bits split
    # 8/7: eight along row 8 (columns size-1 .. size-8) and seven down column 8
    # (rows size-1 .. size-7). Using 8 for both overwrites (size-8, 8), which is
    # the mandatory dark module — a bug lenient decoders forgive and strict ones
    # do not.
    for i in range(9):
        if not fixed[8][i]:
            m[8][i] = 0
            fixed[8][i] = True
        if not fixed[i][8]:
            m[i][8] = 0
            fixed[i][8] = True
    for i in range(8):
        m[8][size - 1 - i] = 0
        fixed[8][size - 1 - i] = True
    for i in range(7):
        m[size - 1 - i][8] = 0
        fixed[size - 1 - i][8] = True

    # The always-dark module, immediately above that reserved strip.
    m[size - 8][8] = 1
    fixed[size - 8][8] = True


def _place_data(m: list[list[int | None]], fixed: list[list[bool]],
                codewords: list[int]) -> None:
    size = len(m)
    bits = [(cw >> i) & 1 for cw in codewords for i in range(7, -1, -1)]
    idx = 0
    upward = True

    col = size - 1
    while col > 0:
        if col == 6:           # skip the vertical timing column
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if fixed[row][c]:
                    continue
                m[row][c] = bits[idx] if idx < len(bits) else 0
                idx += 1
        upward = not upward
        col -= 2


_MASKS = (
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
)


def _apply_mask(m: list[list[int]], fixed: list[list[bool]], mask: int) -> list[list[int]]:
    rule = _MASKS[mask]
    size = len(m)
    return [
        [
            (m[r][c] ^ 1) if (not fixed[r][c] and rule(r, c)) else m[r][c]
            for c in range(size)
        ]
        for r in range(size)
    ]


def _format_bits(level: str, mask: int) -> list[int]:
    """BCH(15,5) format information, XORed with the standard mask pattern."""
    value = (_LEVEL_BITS[level] << 3) | mask
    # Polynomial division by the BCH generator 0b10100110111.
    rem = value << 10
    for i in range(14, 9, -1):
        if rem & (1 << i):
            rem ^= 0b10100110111 << (i - 10)
    bits = ((value << 10) | rem) ^ 0b101010000010010
    return [(bits >> i) & 1 for i in range(14, -1, -1)]


def _place_format(m: list[list[int]], level: str, mask: int) -> None:
    size = len(m)
    bits = _format_bits(level, mask)

    # Copy 1: around the top-left finder.
    positions_a = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
                   (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    # Copy 2: split between bottom-left and top-right.
    positions_b = [(size - 1 - i, 8) for i in range(7)] + \
                  [(8, size - 8 + i) for i in range(8)]

    for bit, (r, c) in zip(bits, positions_a):
        m[r][c] = bit
    for bit, (r, c) in zip(bits, positions_b):
        m[r][c] = bit


def _penalty(m: list[list[int]]) -> int:
    size = len(m)
    score = 0

    # Rule 1: runs of five or more identical modules.
    for line in list(m) + [list(col) for col in zip(*m)]:
        run, prev = 1, line[0]
        for value in line[1:]:
            if value == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, value
        if run >= 5:
            score += 3 + (run - 5)

    # Rule 2: 2x2 blocks of one colour.
    for r in range(size - 1):
        for c in range(size - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3

    # Rule 3: finder-like 1:1:3:1:1 patterns.
    patterns = ([1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0], [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1])
    for line in list(m) + [list(col) for col in zip(*m)]:
        for i in range(size - 10):
            window = list(line[i:i + 11])
            if window in patterns:
                score += 40

    # Rule 4: deviation from a 50% dark ratio.
    dark = sum(sum(row) for row in m)
    percent = dark * 100 // (size * size)
    score += 10 * (abs(percent - 50) // 5)
    return score


def encode(text: str, level: str = "M") -> list[list[int]]:
    """Return the QR matrix for `text` as rows of 0/1 (1 = dark)."""
    level = level.upper()
    if level not in ("L", "M"):
        raise QrError("only levels L and M are supported")

    payload = text.encode("utf-8")
    version = _pick_version(len(payload), level)
    size = version * 4 + 17

    codewords = _interleave(_encode_data(payload, version, level), version, level)

    matrix, fixed = _blank(size)
    _place_function_patterns(matrix, fixed, version)
    _place_data(matrix, fixed, codewords)
    base = [[0 if v is None else v for v in row] for row in matrix]

    best: tuple[int, list[list[int]]] | None = None
    for mask in range(8):
        candidate = _apply_mask(base, fixed, mask)
        _place_format(candidate, level, mask)
        score = _penalty(candidate)
        if best is None or score < best[0]:
            best = (score, candidate)

    assert best is not None
    return best[1]


# ---------------------------------------------------------------------------
# Terminal rendering
# ---------------------------------------------------------------------------

def render(text: str, level: str = "M", quiet: int = 3, color: bool = True) -> str:
    """Render `text` as a scannable block of terminal text.

    Uses the upper-half-block glyph so one character carries two rows, which
    keeps the code roughly square in a terminal's 1:2 cell aspect. Colours are
    set explicitly rather than inherited: a QR code drawn in the terminal's own
    foreground colour is unscannable on half the themes in existence.
    """
    matrix = encode(text, level)
    size = len(matrix)
    span = size + quiet * 2

    def module(row: int, col: int) -> int:
        r, c = row - quiet, col - quiet
        if 0 <= r < size and 0 <= c < size:
            return matrix[r][c]
        return 0

    lines: list[str] = []
    if color:
        WHITE_FG, BLACK_FG = "\033[97m", "\033[30m"
        WHITE_BG, BLACK_BG = "\033[107m", "\033[40m"
        for row in range(0, span, 2):
            out = []
            for col in range(span):
                top = module(row, col)
                bottom = module(row + 1, col) if row + 1 < span else 0
                fg = BLACK_FG if top else WHITE_FG
                bg = BLACK_BG if bottom else WHITE_BG
                out.append(f"{fg}{bg}▀")
            lines.append("".join(out) + "\033[0m")
    else:
        # Two characters per module keeps the aspect ratio square without colour.
        for row in range(span):
            lines.append("".join("██" if module(row, col) else "  "
                                 for col in range(span)))
    return "\n".join(lines)
