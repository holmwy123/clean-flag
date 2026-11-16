"""Tiny voice-similarity measure (numpy only): average log-mel spectrum of the voiced parts.
Good enough to prefer the same character's voice when picking a donor word."""
import numpy as np

_MEL = {}


def _mel_bank(n_fft=1024, sr=48000, n_mels=32, fmin=80, fmax=8000):
    key = (n_fft, n_mels)
    if key not in _MEL:
        m = lambda f: 2595 * np.log10(1 + f / 700)
        im = lambda x: 700 * (10 ** (x / 2595) - 1)
        pts = im(np.linspace(m(fmin), m(fmax), n_mels + 2))
        bins = np.floor((n_fft + 1) * pts / sr).astype(int)
        fb = np.zeros((n_mels, n_fft // 2 + 1))
        for i in range(1, n_mels + 1):
            l, c, r = bins[i - 1], bins[i], bins[i + 1]
            fb[i - 1, l:c] = (np.arange(l, c) - l) / max(c - l, 1)
            fb[i - 1, c:r] = (r - np.arange(c, r)) / max(r - c, 1)
        _MEL[key] = fb
    return _MEL[key]


def voice_print(pcm, sr=48000):
    """pcm: (n,) or (n, ch) float. Returns a unit vector, or None if too little speech."""
    x = pcm.mean(axis=1) if pcm.ndim == 2 else pcm
    n_fft, hop = 1024, 480
    if len(x) < n_fft * 4:
        return None
    win = np.hanning(n_fft)
    frames = np.lib.stride_tricks.sliding_window_view(x, n_fft)[::hop] * win
    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    energy = spec.sum(axis=1)
    voiced = energy > np.percentile(energy, 60)
    if voiced.sum() < 8:
        return None
    mel = np.log(spec[voiced] @ _mel_bank().T + 1e-9).mean(axis=0)
    mel = mel - mel.mean()
    return mel / (np.linalg.norm(mel) + 1e-9)


def similarity(a, b):
    return float(np.dot(a, b)) if a is not None and b is not None else -1.0
