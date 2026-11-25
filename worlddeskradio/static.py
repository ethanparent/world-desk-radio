"""Make the radio's static: a 24-second loop of between-stations noise, so the project needs no
audio files. It's made of layers, the way a real receiver hisses and crackles:

    hiss        noise shaped like a receiver's: 250 Hz to 6.5 kHz, softer at the top, with a
                slight presence peak, plus a quiet low rumble
    fading      slow, irregular swells and dips in strength (radio people call it QSB)
    crackles    sferics, the clicks of distant lightning, at random and now and then in
                clusters, plus the odd longer crunch
    whistles    rare faint tones gliding in pitch: two far-off stations beating together
    murmur      now and then a distant station talking, too faint to understand

Every layer is shaped in the frequency domain over the whole loop, which makes it repeat
seamlessly: the last sample leads straight into the first, with no crossfade and no click.

    python -m worlddeskradio.static        # writes static.wav (the radio does this itself)
"""

import wave

import numpy as np

RATE = 32000
SECONDS = 24
LOUDNESS = 0.17           # RMS before the soft limit: about 0.16 after, like the old static


def needs_update(path):
    """True if static.wav is missing or was made by an older version of this file."""
    try:
        with wave.open(str(path)) as w:
            return w.getframerate() != RATE or w.getnframes() != RATE * SECONDS
    except (OSError, EOFError, wave.Error):
        return True


def _shaped(rng, n, gain):
    """Loopable noise with the spectrum gain(frequencies in Hz) (an array)."""
    spectrum = np.fft.rfft(rng.standard_normal(n))
    out = np.fft.irfft(spectrum * gain(np.fft.rfftfreq(n, 1 / RATE)), n)
    return out / (np.std(out) + 1e-12)


def _band(low, high, low_order=2, high_order=3):
    """A smooth band-pass: about 6 dB per octave per order outside low..high."""
    def gain(f):
        f = np.maximum(f, 1e-3)
        return 1 / np.sqrt((1 + (low / f) ** (2 * low_order)) * (1 + (f / high) ** (2 * high_order)))
    return gain


def _slow(rng, n, cutoff):
    """A smooth, loopable random wander with no features faster than cutoff Hz (std 1)."""
    return _shaped(rng, n, lambda f: np.exp(-(f / cutoff) ** 2))


def _place(track, event, at):
    """Add an event into the loop at sample `at`, wrapping past the end back to the start."""
    idx = (at + np.arange(len(event))) % len(track)
    np.add.at(track, idx, event)


def make(seed=1944):
    """The static as float samples in -1..1."""
    rng = np.random.default_rng(seed)
    n = RATE * SECONDS

    # Hiss: a receiver's band, tilted towards the low end (pinkish), with a gentle bump near
    # 2.6 kHz where small speakers and AM filters ring a little.
    def hiss_gain(f):
        tilt = np.minimum(1.0, (1000 / np.maximum(f, 1)) ** 0.35)
        bump = 1 + 0.35 * np.exp(-((f - 2600) / 900) ** 2)
        return _band(250, 6500)(f) * tilt * bump
    hiss = _shaped(rng, n, hiss_gain)
    rumble = _shaped(rng, n, _band(60, 320, 2, 2)) * 0.22

    # Fading: slow swells (a few seconds) plus a faint quicker flutter, never quite to silence.
    fading = 1 + 0.26 * _slow(rng, n, 0.25) + 0.07 * _slow(rng, n, 6)
    fading = np.clip(fading, 0.35, None)
    noise = (hiss + rumble) * fading

    # Crackles: short decaying ticks, about four a second at random, in occasional bursts,
    # clearly above the hiss; plus the odd longer crunch.
    band = _band(400, 7000, 1, 2)(np.fft.rfftfreq(n, 1 / RATE))
    ticks = np.zeros(n)
    times = np.cumsum(rng.exponential(1 / 4.0, size=int(SECONDS * 8)))
    times = times[times < SECONDS]
    bursts = rng.choice(len(times), size=max(1, len(times) // 12), replace=False)
    times = np.concatenate([times] + [times[b] + np.cumsum(rng.exponential(0.035, 4)) for b in bursts])
    for at in times:
        length = int(RATE * rng.uniform(0.0006, 0.004))
        tick = rng.standard_normal(length) * np.exp(-np.arange(length) / (length / 3))
        _place(ticks, tick / np.abs(tick).max() * rng.lognormal(0.0, 0.45), int(at * RATE) % n)
    crunches = np.zeros(n)
    for at in rng.uniform(0, SECONDS, size=int(SECONDS / 7)):
        length = int(RATE * rng.uniform(0.03, 0.12))
        envelope = np.abs(np.convolve(rng.standard_normal(length), np.ones(40) / 40, "same"))
        crunch = rng.standard_normal(length) * envelope
        _place(crunches, crunch / np.abs(crunch).max(), int(at * RATE))
    clicks = (np.fft.irfft(np.fft.rfft(ticks) * band, n) * 9.0
              + np.fft.irfft(np.fft.rfft(crunches) * band, n) * 4.0)

    # Whistles: a couple of faint tones a loop, gliding slowly, rising and falling in strength.
    whistles = np.zeros(n)
    for _ in range(3):
        length = int(RATE * rng.uniform(2.5, 5.5))
        f0, f1 = rng.uniform(700, 2400), rng.uniform(700, 2400)
        freq = np.linspace(f0, f1, length) + 6 * np.sin(2 * np.pi * rng.uniform(4, 6) * np.arange(length) / RATE)
        phase = 2 * np.pi * np.cumsum(freq) / RATE
        envelope = np.sin(np.pi * np.arange(length) / length) ** 2
        _place(whistles, np.sin(phase) * envelope * rng.uniform(0.2, 0.32), int(rng.uniform(0, n)))

    # A distant station murmuring: voice-band noise with speech-like formants, pulsing at the
    # pace of syllables, very faint.
    murmur = np.zeros(n)
    voice = _shaped(rng, n, lambda f: (np.exp(-((f - 550) / 220) ** 2) + 0.7 * np.exp(-((f - 1400) / 380) ** 2)
                                       + 0.4 * np.exp(-((f - 2500) / 500) ** 2)))
    for _ in range(2):
        length = int(RATE * rng.uniform(3, 6))
        start = int(rng.uniform(0, n))
        syllables = np.clip(_slow(rng, length, 5)[:length], 0, None) ** 1.5
        envelope = np.sin(np.pi * np.arange(length) / length) ** 2
        idx = (start + np.arange(length)) % n
        murmur[idx] += voice[idx] * syllables * envelope * 0.2

    mix = noise + clicks + whistles + murmur
    mix *= LOUDNESS / np.sqrt(np.mean(mix ** 2))
    return np.tanh(mix * 1.15) / 1.15                  # rounds off the loudest crackles


def generate(path, seed=1944):
    samples = make(seed)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


if __name__ == "__main__":
    generate("static.wav")
