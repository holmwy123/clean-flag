"""Reader/writer for the game's text archive ("LocalizationPackage") that holds subtitles and menu text.

Format (worked out from the structure of the game's own files; same layout the community translation
tools for this game use):
  [4 sig 6F9C3C6E][4 size][u32 name_len ...][...header...]  -> `base` offset
  at base (big-endian):  u16 max_num, u16 count, then `count` tree nodes of 4 bytes (right u16, left u16)
     node with left == 0 is a leaf: `right` is a UTF-16 character; otherwise it is a pair (left, right)
  then u16 block_count, then block_count x 16-byte records (id u32, hash u32, data_off u32, loc_off u32)
  each block: u16 (n-1), n offsets (u16 cumulative, optionally + 2 helper bytes), then the packed lines.
  a packed line is a list of node codes: 1 byte (< max_num), 2 bytes, or 0xFF + 2 bytes.
Unchanged lines are kept byte for byte; only edited lines are re-encoded with the file's own tree.
"""
import struct
import zlib

BMS_MAGIC = bytes.fromhex("33AAFB5799FA0410")
LOC_SIG = bytes.fromhex("6F9C3C6E")


def u16(d, o): return struct.unpack_from(">H", d, o)[0]
def u32(d, o): return struct.unpack_from(">I", d, o)[0]


class Loc:
    def __init__(self, data):
        if len(data) < 100 or LOC_SIG not in data[:16]:
            raise ValueError("not a text package")
        self.data = data
        nl = struct.unpack_from("<I", data, 8)[0]
        p = 8 + 4 + nl + 1 + 1 + 4 + 4 + 20 + 4 + 8
        self.base = p
        self.max_num = u16(data, p)
        self.count = u16(data, p + 2)
        self.tree_pos = p + 4
        self.tbl_off = self.tree_pos + self.count * 4
        self.nb = u16(data, self.tbl_off)
        self.tbl = self.tbl_off + 2
        self._memo = {}
        self.blocks = self._read_blocks()

    # ---- tree ----
    def node_text(self, idx):
        if idx == 0:
            return ""
        if idx in self._memo:
            return self._memo[idx]
        d = self.data
        stack, out = [idx], []
        while stack:
            cur = stack.pop()
            if cur == 0:
                continue
            o = self.tree_pos + cur * 4
            if o + 3 >= len(d):
                continue
            right, left = u16(d, o), u16(d, o + 2)
            if left == 0:
                out.append(self._wstr(o))
            else:
                stack.append(right)
                stack.append(left)
        self._memo[idx] = "".join(out)
        return self._memo[idx]

    def _wstr(self, o):
        d, chars = self.data, []
        while o + 1 < len(d):
            v = u16(d, o)
            o += 2
            if v == 0:
                break
            chars.append(chr(v))
        return "".join(chars)

    def decode_bytes(self, raw):
        out, pos, n = [], 0, len(raw)
        while pos < n:
            bv = raw[pos]
            if bv < self.max_num:
                out.append(self.node_text(bv + 1)); pos += 1
            elif bv < 255:
                if pos + 1 >= n: break
                v = ((raw[pos] << 8) | raw[pos + 1]) - 255 * self.max_num
                out.append(self.node_text(v + 1)); pos += 2
            else:
                if pos + 2 >= n: break
                v = (raw[pos + 1] << 8) | raw[pos + 2]
                out.append(self.node_text(v + 1)); pos += 3
        return "".join(out)

    # ---- blocks ----
    def _read_blocks(self):
        d, blocks, ptr = self.data, [], self.tbl
        for bi in range(self.nb):
            bid, bhash, du_raw, lo_raw = struct.unpack_from(">IIII", d, ptr)
            ptr += 16
            du, lo = du_raw + self.base, lo_raw + self.base + 2
            rc = u16(d, lo - 2)
            n = rc + 1
            stride = None
            for st in (2, 4):                                   # offset table: 2 or 4 bytes per entry
                if lo + n * st > len(d):
                    continue
                offs, prev, ok = [], 0, True
                for i in range(n):
                    cur = u16(d, lo + i * st)
                    if cur < prev or du + cur > len(d):
                        ok = False; break
                    offs.append(cur); prev = cur
                if ok:
                    stride = st; break
            if stride is None:
                raise ValueError(f"block {bi}: bad offset table")
            helpers = [bytes(d[lo + i * 4 + 2: lo + i * 4 + 4]) for i in range(n)] if stride == 4 else None
            ents, prev = [], 0
            for cur in offs:
                ents.append(bytes(d[du + prev: du + cur])); prev = cur
            blocks.append({"id": bid, "hash": bhash, "stride": stride, "helpers": helpers, "raw": ents})
        return blocks

    def texts(self):
        return [self.decode_bytes(r) for b in self.blocks for r in b["raw"]]

    # ---- encoding with the file's own tree ----
    def _encoder(self):
        if hasattr(self, "_enc"):
            return self._enc
        best = {}                                           # string -> cheapest node index
        for idx in range(1, self.count):
            if idx == self.max_num:
                continue
            s = self.node_text(idx)
            if not s or "\x00" in s:
                continue
            w = 1 if idx < self.max_num else (2 if idx <= 254 * 256 + 255 - 255 * self.max_num + 1 else 3)
            if s not in best or w < best[s][0]:
                best[s] = (w, idx)
        self._enc = (best, max(len(s) for s in best))
        return self._enc

    def _code(self, idx):
        if idx < self.max_num:
            return bytes([idx - 1])
        two_hi = 254 * 256 + 255 - 255 * self.max_num + 1
        if idx <= two_hi:
            v = (idx - 1) + 255 * self.max_num
            return bytes([(v >> 8) & 255, v & 255])
        v = idx - 1
        return bytes([255, (v >> 8) & 255, v & 255])

    def encode(self, text):
        best, mx = self._encoder()
        n = len(text)
        if n == 0:
            return b""
        INF = 1 << 30
        cost, choice = [0] * (n + 1), [None] * n
        for i in range(n - 1, -1, -1):
            b, bc = INF, None
            for j in range(i + 1, min(n, i + mx) + 1):
                hit = best.get(text[i:j])
                if hit and hit[0] + cost[j] < b:
                    b, bc = hit[0] + cost[j], (hit[1], j)
            if bc is None:
                raise ValueError(f"character not in this file's alphabet: {text[i]!r}")
            cost[i], choice[i] = b, bc
        out, i = bytearray(), 0
        while i < n:
            idx, j = choice[i]
            out += self._code(idx); i = j
        return bytes(out)

    def rebuild(self, new_texts):
        """New package bytes. new_texts: one string per line, same order/count as texts(). Unchanged lines stay byte-identical."""
        old = self.texts()
        if len(new_texts) != len(old):
            raise ValueError("line count mismatch")
        d = self.data
        out = bytearray(d[:self.base])
        out += struct.pack(">HH", self.max_num, self.count)
        out += d[self.tree_pos: self.tree_pos + self.count * 4]
        out += struct.pack(">H", len(self.blocks))
        table_pos = len(out)
        out += b"\x00" * (len(self.blocks) * 16)
        k, changed, reverted = 0, 0, 0
        for bi, b in enumerate(self.blocks):
            ents = []
            for raw in b["raw"]:
                if new_texts[k] != old[k]:
                    ents.append(self.encode(new_texts[k])); changed += 1
                else:
                    ents.append(raw)
                k += 1
            if sum(len(e) for e in ents) > 0xFFFF:       # too big to re-encode: keep this block exactly as the game has it
                ents = list(b["raw"]); reverted += len(ents); changed -= sum(1 for q in range(len(ents)) if new_texts[k - len(ents) + q] != old[k - len(ents) + q])
            n = len(ents)
            region = bytearray(struct.pack(">H", n - 1))
            cum = 0
            for i, e in enumerate(ents):
                cum += len(e)
                region += struct.pack(">H", cum)
                if b["stride"] == 4:
                    region += b["helpers"][i]
            data_start = len(region)
            for e in ents:
                region += e
            lo_raw = len(out) - self.base
            du_raw = len(out) + data_start - self.base
            struct.pack_into(">IIII", out, table_pos + bi * 16, b["id"], b["hash"], du_raw, lo_raw)
            out += region
        total = len(out) - self.base
        struct.pack_into("<I", out, self.base - 4, total)
        struct.pack_into("<I", out, 4, total + 41)
        self.reverted = reverted
        return bytes(out), changed


# ---- wrapping into the forge's entry format ----

def ck0(c):
    a = zlib.adler32(c)
    s1, s2 = a & 0xFFFF, a >> 16
    return (((s2 - len(c)) % 65521) << 16) | ((s1 - 1) % 65521)


def pack_block(data, chunk=262144):
    out = bytearray(BMS_MAGIC + struct.pack("<H", 3) + b"\x08" + b"\x00\x00\x04\x80")
    parts = [data[i:i + chunk] for i in range(0, len(data), chunk)] or [b""]
    out += struct.pack("<I", len(parts))
    for c in parts:
        out += struct.pack("<II", len(c), len(c))
    for c in parts:
        out += struct.pack("<I", ck0(c)) + c
    return bytes(out)


def make_entry(fid, loc_bytes):
    meta = bytearray(20)
    meta[0] = 1
    struct.pack_into("<Q", meta, 2, fid)
    struct.pack_into("<I", meta, 10, len(loc_bytes))
    return pack_block(bytes(meta)) + pack_block(loc_bytes)
