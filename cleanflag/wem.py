"""Read and write Wwise Opus voice clips (.wem, codec 0x3041) as used by Black Flag Resynced.

Layout of one clip (RIFF/WAVE):
  fmt   - standard header + extra: num_samples @0x18, frame-table count @0x1c, pre-skip @0x20
  seek  - table of uint16 Opus packet sizes, one per frame
  LIST  - label chunk carrying the 'OasisID' (links the clip to its subtitle line)
  data  - the raw Opus packets back to back

Decoding: wrap the packets in a normal Ogg Opus stream and let ffmpeg decode it.
Encoding: let ffmpeg/libopus encode, pull the packets back out of the Ogg stream,
and rebuild the clip with a new seek table. Everything else in the header is kept.
"""
import re
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


# ---------------- Ogg helpers ----------------

def _crc_table():
    t = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) if r & 0x80000000 else (r << 1)
        t.append(r & 0xFFFFFFFF)
    return t


_CRC = _crc_table()


def _ogg_crc(data):
    c = 0
    for b in data:
        c = ((c << 8) ^ _CRC[((c >> 24) ^ b) & 0xFF]) & 0xFFFFFFFF
    return c


def _ogg_page(packets, granule, serial, seq, flags):
    lacing = b""
    for p in packets:
        n = len(p)
        lacing += b"\xff" * (n // 255) + bytes([n % 255])
    hdr = struct.pack("<4sBBqIIIB", b"OggS", 0, flags, granule, serial, seq, 0, len(lacing)) + lacing
    page = bytearray(hdr + b"".join(packets))
    struct.pack_into("<I", page, 22, _ogg_crc(page))
    return bytes(page)


def packets_to_ogg(packets, channels, pre_skip, frame_samples=960, serial=0x5EA5):
    head = struct.pack("<8sBBHIhB", b"OpusHead", 1, channels, pre_skip, 48000, 0, 0)
    tags = b"OpusTags" + struct.pack("<I", 9) + b"cleanflag" + struct.pack("<I", 0)
    out = [_ogg_page([head], 0, serial, 0, 2), _ogg_page([tags], 0, serial, 1, 0)]
    seq, gran = 2, 0
    for i in range(0, len(packets), 50):  # 50 packets per page
        chunk = packets[i:i + 50]
        gran += frame_samples * len(chunk)
        last = i + 50 >= len(packets)
        out.append(_ogg_page(chunk, gran, serial, seq, 4 if last else 0))
        seq += 1
    return b"".join(out)


def ogg_to_packets(data):
    """Pull audio packets out of an Ogg Opus stream (skips OpusHead/OpusTags)."""
    packets, cur, pos = [], b"", 0
    while pos < len(data):
        assert data[pos:pos + 4] == b"OggS", "bad ogg page"
        nseg = data[pos + 26]
        lacing = data[pos + 27:pos + 27 + nseg]
        body = pos + 27 + nseg
        for n in lacing:
            cur += data[body:body + n]
            body += n
            if n < 255:
                packets.append(cur)
                cur = b""
        pos = body
    return [p for p in packets if not (p.startswith(b"OpusHead") or p.startswith(b"OpusTags"))]


# ---------------- WEM clip ----------------

@dataclass
class Wem:
    chunks: list = field(default_factory=list)  # [(id, bytes)] in original order

    @classmethod
    def parse(cls, blob):
        assert blob[:4] == b"RIFF" and blob[8:12] == b"WAVE", "not a RIFF/WAVE clip"
        size = struct.unpack_from("<I", blob, 4)[0]
        p, end, chunks = 12, 8 + size, []
        while p + 8 <= end:
            cid = blob[p:p + 4]
            cl = struct.unpack_from("<I", blob, p + 4)[0]
            chunks.append((cid, blob[p + 8:p + 8 + cl]))
            p += 8 + cl + (cl & 1)
        w = cls(chunks)
        assert w.codec == 0x3041, f"unsupported codec {hex(w.codec)}"
        return w

    def _get(self, cid):
        return next(c for i, c in self.chunks if i == cid)

    def _set(self, cid, data):
        self.chunks = [(i, data if i == cid else c) for i, c in self.chunks]

    @property
    def fmt(self):
        return self._get(b"fmt ")

    @property
    def codec(self):
        return struct.unpack_from("<H", self.fmt, 0)[0]

    @property
    def channels(self):
        return struct.unpack_from("<H", self.fmt, 2)[0]

    @property
    def num_samples(self):
        return struct.unpack_from("<i", self.fmt, 0x18)[0]

    @property
    def pre_skip(self):
        return struct.unpack_from("<H", self.fmt, 0x20)[0]

    @property
    def frame_samples(self):
        v = struct.unpack_from("<H", self.fmt, 0x12)[0]
        return v or 960

    @property
    def oasis_id(self):
        for cid, c in self.chunks:
            m = re.search(rb"OasisID(0x[0-9A-Fa-f]+)", c)
            if m:
                return m.group(1).decode()
        return None

    def packets(self):
        sizes = struct.unpack(f"<{len(self._get(b'seek')) // 2}H", self._get(b"seek"))
        data, out, p = self._get(b"data"), [], 0
        for s in sizes:
            out.append(data[p:p + s])
            p += s
        return out

    def set_packets(self, packets, num_samples=None):
        self._set(b"seek", struct.pack(f"<{len(packets)}H", *[len(p) for p in packets]))
        self._set(b"data", b"".join(packets))
        fmt = bytearray(self.fmt)
        struct.pack_into("<I", fmt, 0x1c, len(packets))
        if num_samples is not None:
            struct.pack_into("<i", fmt, 0x18, num_samples)
        # average bytes/sec, used only as a hint
        dur = max(self.num_samples / 48000, 1e-3)
        struct.pack_into("<I", fmt, 8, int(len(b"".join(packets)) / dur))
        self._set(b"fmt ", bytes(fmt))

    def build(self):
        body = b""
        for cid, c in self.chunks:
            body += cid + struct.pack("<I", len(c)) + c + (b"\x00" if len(c) & 1 else b"")
        return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body

    # ---- audio round trip ----

    def to_wav(self, wav_path):
        ogg = packets_to_ogg(self.packets(), self.channels, self.pre_skip, self.frame_samples)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "ogg", "-i", "pipe:0", "-c:a", "pcm_s16le",
                        str(wav_path)], input=ogg, check=True)

    def replace_audio(self, wav_path, bitrate=None):
        """Re-encode a (cleaned) WAV back into this clip, keeping its header and labels."""
        br = bitrate or max(16000, int(len(self._get(b"data")) * 8 / max(self.num_samples / 48000, 0.05)))
        with tempfile.TemporaryDirectory() as td:
            ogg = Path(td) / "o.opus"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav_path), "-ar", "48000",
                            "-ac", str(self.channels), "-c:a", "libopus", "-b:a", str(br),
                            "-frame_duration", str(self.frame_samples / 48), "-application", "voip",
                            "-mapping_family", "0" if self.channels <= 2 else "1", str(ogg)], check=True)
            raw = ogg.read_bytes()
        pre = struct.unpack_from("<H", raw, raw.index(b"OpusHead") + 10)[0]
        fmt = bytearray(self.fmt)
        struct.pack_into("<H", fmt, 0x20, pre)
        self._set(b"fmt ", bytes(fmt))
        self.set_packets(ogg_to_packets(raw))


def find_clips(blob):
    """Yield (offset, length, Wem) for every Opus voice clip inside an archive blob."""
    for m in re.finditer(rb"RIFF", blob):
        o = m.start()
        if blob[o + 8:o + 12] != b"WAVE":
            continue
        size = struct.unpack_from("<I", blob, o + 4)[0] + 8
        try:
            w = Wem.parse(blob[o:o + size])
        except (AssertionError, struct.error, StopIteration):
            continue
        yield o, size, w


# Pre-made 20 ms Opus packets that decode to silence (CELT, fullband). Made with libopus.
_SILENCE = {1: bytes.fromhex("f8fffe"), 2: bytes.fromhex("fcfffe")}


def silence_packet(channels=1):
    return _SILENCE[1 if channels == 1 else 2]


def silent_packet_of_size(n, channels=1):
    """A silent Opus packet exactly n bytes long (uses the Opus code-3 padding feature).

    Same length means the seek table and every other byte of the clip stay where they are.
    """
    base = silence_packet(channels)          # TOC + 2 bytes of CELT silence
    if n == len(base):
        return base
    frame = base[1:]
    toc = bytes([base[0] | 3])               # code 3: explicit frame count
    for k in range(0, n // 254 + 2):          # k bytes of 255 + one final length byte
        pad = n - 2 - (k + 1) - len(frame)
        if pad < 0:
            break
        if pad - 254 * k in range(0, 255):
            lens = bytes([255] * k + [pad - 254 * k])
            return toc + bytes([0x40 | 1]) + lens + frame + b"\x00" * pad
    return None  # too short to hold a silent frame; caller keeps the original


def decode_valid(wem):
    """Decode packet by packet. Returns (16 kHz mono float audio, number of good packets).

    Prefetch clips only hold the first part of a line inside the bank; decoding stops
    cleanly where the stored part ends.
    """
    import av
    import numpy as np
    head = packets_to_ogg([], wem.channels, wem.pre_skip)
    cc = av.CodecContext.create("opus", "r")
    i = head.index(b"OpusHead")
    cc.extradata = head[i:i + 19]
    rs = av.AudioResampler(format="flt", layout="mono", rate=16000)
    out, good = [], 0
    for pk in wem.packets():
        if not pk:
            break
        try:
            frames = cc.decode(av.Packet(pk))
        except Exception:  # noqa: BLE001
            break
        good += 1
        for fr in frames:
            for f in rs.resample(fr):
                out.append(f.to_ndarray().reshape(-1))
    for f in rs.resample(None):
        out.append(f.to_ndarray().reshape(-1))
    audio = np.concatenate(out) if out else np.zeros(0, dtype="float32")
    skip = int(wem.pre_skip / 3)  # 48k pre-skip samples -> 16k
    return audio[skip:], good


def to_pcm16k(wem):
    return decode_valid(wem)[0]


def frames_for_spans(wem, spans, pad_ms=40, limit=None):
    """Indices of the 20 ms frames overlapping any (start_sec, end_sec) span."""
    n = len(wem.packets())
    limit = n if limit is None else min(limit, n)
    fs, skip, out = wem.frame_samples, wem.pre_skip, set()
    for s, e in spans:
        a = int((s - pad_ms / 1000) * 48000) + skip
        b = int((e + pad_ms / 1000) * 48000) + skip
        out.update(range(max(0, a // fs), min(limit, b // fs + 1)))
    return sorted(out)


def mute_frames(wem, frames):
    """Replace the given frames with silent frames of exactly the same byte length.
    Nothing else in the clip moves. Returns the frames actually muted."""
    pkts = wem.packets()
    done = []
    for i in frames:
        if 0 <= i < len(pkts):
            sil = silent_packet_of_size(len(pkts[i]), wem.channels)
            if sil is not None:
                if pkts[i] != sil:
                    pkts[i] = sil
                done.append(i)
    data = b"".join(pkts)
    wem._set(b"data", data + wem._get(b"data")[len(data):])
    return done


def mute_spans(wem, spans, pad_ms=40, limit=None):
    return len(mute_frames(wem, frames_for_spans(wem, spans, pad_ms, limit)))


def build_same_size(original, wem):
    """Return `original` with only the seek-table values and audio packets overwritten in place.

    Every other byte (headers, labels, sizes, padding) is copied unchanged, so the clip
    stays exactly the same size and nothing else in the game's archive has to move.
    """
    out = bytearray(original)
    size = struct.unpack_from("<I", original, 4)[0]
    p, end, where = 12, min(len(original), 8 + size), {}
    while p + 8 <= end:
        cid = original[p:p + 4]
        cl = struct.unpack_from("<I", original, p + 4)[0]
        where[cid] = (p + 8, cl)
        p += 8 + cl + (cl & 1)
    seek, data = wem._get(b"seek"), wem._get(b"data")
    so, sl = where[b"seek"]
    do, dl = where[b"data"]
    if len(seek) != sl:
        raise ValueError("frame count changed")
    if len(data) > dl:
        raise ValueError("edited clip is larger than the original")
    out[so:so + sl] = seek
    out[do:do + dl] = data + b"\x00" * (dl - len(data))
    return bytes(out)


# ---------------- PCM editing (close-gap / splice modes) ----------------

def decode_pcm48(wem):
    """Decode a whole clip to 48 kHz float32, shape (samples, channels), pre-skip removed.
    Returns (pcm, good_packets)."""
    import av
    import numpy as np
    head = packets_to_ogg([], wem.channels, wem.pre_skip)
    cc = av.CodecContext.create("opus", "r")
    i = head.index(b"OpusHead")
    cc.extradata = head[i:i + 19]
    out, good = [], 0
    for pk in wem.packets():
        if not pk:
            break
        try:
            frames = cc.decode(av.Packet(pk))
        except Exception:  # noqa: BLE001
            break
        good += 1
        for fr in frames:
            a = fr.to_ndarray()
            a = a.T if a.shape[0] == wem.channels and a.ndim == 2 and fr.format.is_planar else a.reshape(-1, wem.channels)
            out.append(a.astype("float32"))
    pcm = np.concatenate(out) if out else np.zeros((0, wem.channels), "float32")
    # ffmpeg already drops the pre-skip from the first frame; trim to the stated length
    return pcm[:wem.num_samples], good


def encode_pcm48(pcm, channels, bitrate):
    """Encode 48 kHz float PCM into 20 ms Opus packets. Returns (packets, pre_skip)."""
    import av
    import numpy as np
    cc = av.CodecContext.create("libopus", "w")
    cc.sample_rate = 48000
    cc.layout = "mono" if channels == 1 else "stereo"
    cc.format = "flt"
    cc.bit_rate = int(bitrate)
    cc.options = {"frame_duration": "20", "application": "voip", "vbr": "on"}
    cc.open()
    pre = cc.delay if getattr(cc, "delay", 0) else 312
    data = np.ascontiguousarray(pcm.reshape(-1, channels).astype("float32"))
    pkts = []
    for s in range(0, len(data) + 960, 960):
        chunk = data[s:s + 960]
        if len(chunk) < 960:
            chunk = np.vstack([chunk, np.zeros((960 - len(chunk), channels), "float32")])
        fr = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="flt", layout=cc.layout.name)
        fr.sample_rate = 48000
        fr.pts = s
        pkts += [bytes(p) for p in cc.encode(fr)]
    pkts += [bytes(p) for p in cc.encode(None)]
    return pkts, pre


def replace_pcm(wem, pcm):
    """Re-encode `pcm` into this clip so the data fits the original byte budget and frame count.
    Returns True on success (the clip's seek/data are updated), False if it can't fit."""
    orig_pkts = wem.packets()
    n_frames, budget = len(orig_pkts), sum(map(len, orig_pkts))
    rate0 = budget * 8 / max(wem.num_samples / 48000, 0.05)
    for factor in (1.15, 1.0, 0.85, 0.7, 0.55, 0.4):
        pkts, pre = encode_pcm48(pcm, wem.channels, max(12000, rate0 * factor))
        if pre != wem.pre_skip:
            return False
        if len(pkts) > n_frames:
            pkts = pkts[:n_frames]  # trailing encoder flush beyond the original length
        pkts += [silence_packet(wem.channels)] * (n_frames - len(pkts))
        if sum(map(len, pkts)) <= budget:
            wem._set(b"seek", struct.pack(f"<{n_frames}H", *map(len, pkts)))
            wem._set(b"data", b"".join(pkts))
            return True
    return False
