"""Pure-Python educational tools used by the laboratories.

Everything here is deterministic and side-effect free so it can be unit tested
without Discord. The models are deliberately simplified teaching models.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

SPARK = "▁▂▃▄▅▆▇█"

# --------------------------------------------------------------------------
# Neuron simulation: leaky integrate-and-fire (LIF)
# --------------------------------------------------------------------------
LIF_V_REST = -70.0     # mV
LIF_V_THRESH = -55.0   # mV
LIF_V_RESET = -75.0    # mV
LIF_TAU = 10.0         # ms membrane time constant
LIF_R = 10.0           # MOhm (so 1 nA of input gives 10 mV of steady drive)
LIF_REFRACTORY = 2.0   # ms
LIF_RHEOBASE_NA = (LIF_V_THRESH - LIF_V_REST) / LIF_R  # 1.5 nA


@dataclass
class LIFResult:
    current_na: float
    duration_ms: float
    trace: list[float]
    spike_times: list[float]

    @property
    def spike_count(self) -> int:
        return len(self.spike_times)

    @property
    def rate_hz(self) -> float:
        return self.spike_count / (self.duration_ms / 1000.0)


def simulate_lif(current_na: float, duration_ms: float = 200.0, dt: float = 0.1) -> LIFResult:
    """Simulate a leaky integrate-and-fire neuron with constant injected current.

    dV/dt = (-(V - V_rest) + R*I) / tau, integrated with the Euler method.
    """
    if not (0.0 <= current_na <= 10.0):
        raise ValueError("Input current must be between 0 and 10 nA.")
    if not (20.0 <= duration_ms <= 500.0):
        raise ValueError("Duration must be between 20 and 500 ms.")
    steps = int(round(duration_ms / dt))
    v = LIF_V_REST
    trace: list[float] = []
    spikes: list[float] = []
    refractory_until = -1.0
    for i in range(steps):
        t = i * dt
        if t < refractory_until:
            v = LIF_V_RESET
        else:
            v += dt * (-(v - LIF_V_REST) + LIF_R * current_na) / LIF_TAU
            if v >= LIF_V_THRESH:
                spikes.append(round(t, 3))
                trace.append(LIF_V_THRESH)  # clip the plotted peak at threshold
                v = LIF_V_RESET
                refractory_until = t + LIF_REFRACTORY
                continue
        trace.append(v)
    return LIFResult(current_na, duration_ms, trace, spikes)


def sparkline(values: list[float], lo: float, hi: float, width: int = 48,
              top_bins: frozenset[int] = frozenset()) -> str:
    """Down-sample ``values`` into ``width`` bins (bin mean) and map to block characters.

    Bins listed in ``top_bins`` (those containing a spike) are drawn at full height.
    """
    if not values:
        return ""
    n = len(values)
    out = []
    for b in range(width):
        if b in top_bins:
            out.append(SPARK[-1])
            continue
        start, end = b * n // width, max((b + 1) * n // width, b * n // width + 1)
        chunk = values[start:end]
        v = sum(chunk) / len(chunk)
        frac = 0.0 if hi == lo else (v - lo) / (hi - lo)
        frac = min(1.0, max(0.0, frac))
        out.append(SPARK[int(round(frac * (len(SPARK) - 1)))])
    return "".join(out)


def _spike_bins(spike_times: list[float], duration_ms: float, width: int) -> frozenset[int]:
    return frozenset(min(width - 1, int(t / duration_ms * width)) for t in spike_times)


def spike_marks(spike_times: list[float], duration_ms: float, width: int = 48) -> str:
    bins = _spike_bins(spike_times, duration_ms, width)
    return "".join("▲" if i in bins else "·" for i in range(width))


def render_lif(result: LIFResult, width: int = 48) -> str:
    """Monospace plot: membrane potential, spike markers, time axis and voltage scale."""
    bins = _spike_bins(result.spike_times, result.duration_ms, width)
    left, right = "0 ms", f"{int(result.duration_ms)} ms"
    axis = "    " + left + " " * max(width - len(left) - len(right), 1) + right
    return (
        f"V   {sparkline(result.trace, LIF_V_RESET, LIF_V_THRESH, width, bins)}\n"
        f"AP  {spike_marks(result.spike_times, result.duration_ms, width)}\n"
        f"{axis}\n"
        f"▁ = {LIF_V_RESET:g} mV (reset)   █ = {LIF_V_THRESH:g} mV (threshold / spike)"
    )


# --------------------------------------------------------------------------
# Perceptron (single artificial neuron) learning logic gates
# --------------------------------------------------------------------------
GATES = {
    "AND": {(0, 0): 0, (0, 1): 0, (1, 0): 0, (1, 1): 1},
    "OR": {(0, 0): 0, (0, 1): 1, (1, 0): 1, (1, 1): 1},
    "NAND": {(0, 0): 1, (0, 1): 1, (1, 0): 1, (1, 1): 0},
    "XOR": {(0, 0): 0, (0, 1): 1, (1, 0): 1, (1, 1): 0},
}


@dataclass
class PerceptronResult:
    gate: str
    weights: tuple[float, float]
    bias: float
    epochs_run: int
    converged: bool
    errors_per_epoch: list[int]
    predictions: dict[tuple[int, int], int]


def train_perceptron(gate: str, max_epochs: int = 25, lr: float = 0.1) -> PerceptronResult:
    """Train a single threshold neuron with the perceptron learning rule."""
    if gate not in GATES:
        raise ValueError(f"Unknown gate {gate!r}")
    table = GATES[gate]
    w1 = w2 = b = 0.0
    errors_hist: list[int] = []
    converged = False
    epochs = 0
    for epoch in range(1, max_epochs + 1):
        epochs = epoch
        errors = 0
        for (x1, x2), target in table.items():
            out = 1 if (w1 * x1 + w2 * x2 + b) > 0 else 0
            err = target - out
            if err:
                errors += 1
                w1 += lr * err * x1
                w2 += lr * err * x2
                b += lr * err
        errors_hist.append(errors)
        if errors == 0:
            converged = True
            break
    preds = {k: 1 if (w1 * k[0] + w2 * k[1] + b) > 0 else 0 for k in table}
    return PerceptronResult(gate, (round(w1, 3), round(w2, 3)), round(b, 3), epochs, converged,
                            errors_hist, preds)


# --------------------------------------------------------------------------
# DNA / molecular tools
# --------------------------------------------------------------------------
_BASES = "TCAG"
_AMINO = "FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG"
CODON_TABLE: dict[str, str] = {
    a + b + c: _AMINO[i]
    for i, (a, b, c) in enumerate((x, y, z) for x in _BASES for y in _BASES for z in _BASES)
}
_COMPLEMENT = str.maketrans("ACGT", "TGCA")

MAX_DNA_LENGTH = 300


def clean_dna(raw: str) -> str:
    seq = "".join(raw.split()).upper()
    if not seq:
        raise ValueError("Please enter a DNA sequence.")
    if len(seq) > MAX_DNA_LENGTH:
        raise ValueError(f"Sequence is too long (max {MAX_DNA_LENGTH} bases).")
    bad = sorted(set(seq) - set("ACGT"))
    if bad:
        raise ValueError("Only A, C, G and T are allowed (found: " + ", ".join(bad[:5]) + ").")
    return seq


def reverse_complement(seq: str) -> str:
    return seq.translate(_COMPLEMENT)[::-1]


def transcribe(coding_strand: str) -> str:
    """mRNA with the same sequence as the coding (sense) strand, T -> U."""
    return coding_strand.replace("T", "U")


def translate(coding_strand: str) -> tuple[str, bool]:
    """Translate reading frame 1 of the coding strand. Returns (protein, hit_stop)."""
    protein = []
    for i in range(0, len(coding_strand) - 2, 3):
        aa = CODON_TABLE[coding_strand[i:i + 3]]
        if aa == "*":
            return "".join(protein), True
        protein.append(aa)
    return "".join(protein), False


def gc_content(seq: str) -> float:
    return 100.0 * (seq.count("G") + seq.count("C")) / len(seq)


@dataclass
class DNAReport:
    sequence: str
    complement_template: str
    mrna: str
    protein: str
    hit_stop: bool
    gc: float
    leftover_bases: int


def analyse_dna(raw: str) -> DNAReport:
    seq = clean_dna(raw)
    protein, stop = translate(seq)
    leftover = len(seq) % 3 if not stop else 0
    return DNAReport(seq, reverse_complement(seq), transcribe(seq), protein, stop,
                     gc_content(seq), leftover)


# --------------------------------------------------------------------------
# Number explorer
# --------------------------------------------------------------------------
MAX_EXPLORE = 10 ** 12


def factorise(n: int) -> dict[int, int]:
    """Trial-division factorisation (fast enough for n <= 10**12)."""
    if n < 1:
        raise ValueError("n must be positive")
    factors: dict[int, int] = {}
    for p in (2, 3):
        while n % p == 0:
            factors[p] = factors.get(p, 0) + 1
            n //= p
    f = 5
    while f * f <= n:
        for p in (f, f + 2):
            while n % p == 0:
                factors[p] = factors.get(p, 0) + 1
                n //= p
        f += 6
    if n > 1:
        factors[n] = factors.get(n, 0) + 1
    return factors


def collatz_steps(n: int, limit: int = 100000) -> Optional[int]:
    steps = 0
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        steps += 1
        if steps > limit:
            return None
    return steps


@dataclass
class NumberReport:
    n: int
    is_prime: bool
    factors: dict[int, int]
    divisor_count: int
    divisor_sum: int
    totient: int
    collatz: Optional[int]


def explore_number(n: int) -> NumberReport:
    if not (1 <= n <= MAX_EXPLORE):
        raise ValueError(f"Enter a whole number from 1 to {MAX_EXPLORE:,}.")
    f = factorise(n)
    dcount = 1
    dsum = 1
    phi = n
    for p, e in f.items():
        dcount *= e + 1
        dsum *= (p ** (e + 1) - 1) // (p - 1)
        phi = phi // p * (p - 1)
    is_prime = n > 1 and f == {n: 1}
    return NumberReport(n, is_prime, f, dcount, dsum, phi, collatz_steps(n))


def format_factorisation(f: dict[int, int]) -> str:
    if not f:
        return "1"
    return " × ".join(f"{p}" if e == 1 else f"{p}^{e}" for p, e in sorted(f.items()))


def primes_up_to(n: int) -> list[int]:
    if n < 2:
        return []
    sieve = bytearray([1]) * (n + 1)
    sieve[0:2] = b"\x00\x00"
    for i in range(2, int(math.isqrt(n)) + 1):
        if sieve[i]:
            sieve[i * i::i] = bytearray(len(sieve[i * i::i]))
    return [i for i, v in enumerate(sieve) if v]
