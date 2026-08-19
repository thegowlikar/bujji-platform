"""Two IV derivations, one chain: measure the disagreement before choosing.

WHY THIS EXISTS. Strike selection picks by TARGET DELTA, and Bujji now has two
ways to compute that delta:

  ENGINE (canonical today)  spot-based Black-Scholes, risk-free rate assumed
                            at a constant 6.5%, spot delta
  PARITY (options_analytics) Black-76 on a forward recovered from the chain
                            by put-call parity -- no rate assumed -- forward
                            delta

Both are legitimate. They will not agree, and the disagreement is not
cosmetic: selection ranks strikes by |delta - target|, so a shift of a few
hundredths in delta space can move the chosen strike a step or two on a
weekly chain. That is a different trade, which makes swapping them a change
to the canonical strategy authority -- an operator decision, not an
engineering one.

SO THIS MODULE DECIDES NOTHING. It runs both on the same rows and records
what each WOULD have chosen. The field that matters is `strikes_agree`: on
how many cycles would the two derivations have picked different strikes? A
week of that answers the gate question from a record instead of from
argument.

IT CALLS THE REAL ENGINE, NOT A COPY. `_build_strike_evidence` is imported
from msi_trade_construction rather than reimplemented here -- a
reimplementation would drift, and then this module would be measuring the
difference between the parity derivation and MY IDEA of the engine, which is
worse than measuring nothing. Import only: nothing in the protected package
is modified.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from bujji.msi_trade_construction.engine import _build_strike_evidence

from .engine import analyse_expiry

# The targets strike selection actually uses (msi_trade_construction.config
# .FAMILY_DELTA_TARGETS). 0.20 is the premium-selling target and therefore
# the one that matters most for a strangle seller; 0.50 is the straddle/fly
# target. Compared at the real values rather than at round numbers of my own.
DEFAULT_TARGET_DELTAS = (0.20, 0.30, 0.40, 0.50)


def _nearest_by_delta(candidates: Sequence[Tuple[float, float]],
                      target: float) -> Optional[Tuple[float, float]]:
    """(strike, delta) whose |delta| is closest to target -- the same rule
    the engine's own `_nearest_by_delta` applies."""
    if not candidates:
        return None
    return min(candidates, key=lambda pair: abs(abs(pair[1]) - target))


@dataclass(frozen=True)
class StrikeChoice:
    target_delta: float
    option_type: str
    engine_strike: Optional[float] = None
    engine_delta: Optional[float] = None
    parity_strike: Optional[float] = None
    parity_delta: Optional[float] = None

    @property
    def agree(self) -> Optional[bool]:
        """None when either side could not choose -- an unanswerable
        comparison is not an agreement."""
        if self.engine_strike is None or self.parity_strike is None:
            return None
        return self.engine_strike == self.parity_strike

    @property
    def strike_gap(self) -> Optional[float]:
        if self.engine_strike is None or self.parity_strike is None:
            return None
        return self.parity_strike - self.engine_strike

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_delta": self.target_delta, "option_type": self.option_type,
            "engine_strike": self.engine_strike, "engine_delta": self.engine_delta,
            "parity_strike": self.parity_strike, "parity_delta": self.parity_delta,
            "agree": self.agree, "strike_gap": self.strike_gap,
        }


@dataclass(frozen=True)
class DerivationDivergence:
    """One cycle's comparison, with everything needed to audit it later."""

    expiry: str
    as_of: str
    engine_solved: int = 0
    parity_solved: int = 0
    forward_recovered: Optional[float] = None
    forward_status: str = ""
    parity_r_squared: Optional[float] = None
    spot_used: Optional[float] = None
    implied_rate: Optional[float] = None      # from the recovered DF, for context only
    assumed_rate: Optional[float] = None
    atm_iv_engine: Optional[float] = None
    atm_iv_parity: Optional[float] = None
    median_abs_iv_diff: Optional[float] = None
    max_abs_delta_diff: Optional[float] = None
    choices: Tuple[StrikeChoice, ...] = ()
    status: str = "OK"
    reason: Optional[str] = None

    @property
    def strikes_agree(self) -> Optional[bool]:
        """THE GATE QUESTION. True only when every comparable target agrees."""
        verdicts = [c.agree for c in self.choices if c.agree is not None]
        if not verdicts:
            return None
        return all(verdicts)

    @property
    def disagreements(self) -> int:
        return sum(1 for c in self.choices if c.agree is False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expiry": self.expiry, "as_of": self.as_of, "status": self.status,
            "engine_solved": self.engine_solved, "parity_solved": self.parity_solved,
            "forward_recovered": self.forward_recovered,
            "forward_status": self.forward_status,
            "parity_r_squared": self.parity_r_squared, "spot_used": self.spot_used,
            "implied_rate": self.implied_rate, "assumed_rate": self.assumed_rate,
            "atm_iv_engine": self.atm_iv_engine, "atm_iv_parity": self.atm_iv_parity,
            "median_abs_iv_diff": self.median_abs_iv_diff,
            "max_abs_delta_diff": self.max_abs_delta_diff,
            "strikes_agree": self.strikes_agree, "disagreements": self.disagreements,
            "choices": [c.to_dict() for c in self.choices],
            "reason": self.reason,
        }


def compare_derivations(
    *,
    rows: Sequence[Any],
    chain_dicts: Sequence[Dict[str, Any]],
    expiry: str,
    spot: float,
    t_years: float,
    as_of: str,
    target_deltas: Sequence[float] = DEFAULT_TARGET_DELTAS,
    assumed_rate: Optional[float] = None,
) -> DerivationDivergence:
    """Run both derivations over the same chain and record the difference.

    `rows` are the observation objects the engine consumes; `chain_dicts` are
    the same contracts as plain dicts for the parity path. Both come from ONE
    fetch -- comparing two derivations against two different snapshots would
    measure the market moving, not the models disagreeing.

    Never raises: a comparison is diagnostics, and diagnostics must not be
    able to end a trading session.
    """
    import math
    from statistics import median

    try:
        evidence = _build_strike_evidence(rows, expiry, spot, t_years)
    except Exception as exc:  # noqa: BLE001
        return DerivationDivergence(expiry=expiry, as_of=as_of, status="ENGINE_FAILED",
                                    reason=f"{type(exc).__name__}: {exc}")

    parity = analyse_expiry(rows=chain_dicts, expiry=expiry, as_of=as_of)

    engine_by_key = {(e.strike, e.option_type): e for e in evidence.values()
                     if e.delta is not None and e.iv is not None}
    parity_by_key = {(c.strike, c.option_type): c for c in parity.contracts
                     if c.iv is not None and c.forward_delta is not None}

    if not parity.forward.is_usable:
        return DerivationDivergence(
            expiry=expiry, as_of=as_of, status="NO_PARITY_FORWARD",
            engine_solved=len(engine_by_key), parity_solved=0,
            forward_status=parity.forward.status, spot_used=spot,
            assumed_rate=assumed_rate,
            reason=f"parity forward unavailable ({parity.forward.status}) -- nothing to compare")

    shared = sorted(set(engine_by_key) & set(parity_by_key))
    iv_diffs = [abs(engine_by_key[k].iv - parity_by_key[k].iv) for k in shared]
    delta_diffs = [abs(abs(engine_by_key[k].delta) - abs(parity_by_key[k].forward_delta))
                   for k in shared]

    choices: List[StrikeChoice] = []
    for target in target_deltas:
        for option_type in ("CE", "PE"):
            eng = _nearest_by_delta(
                [(k[0], engine_by_key[k].delta) for k in engine_by_key if k[1] == option_type],
                target)
            par = _nearest_by_delta(
                [(k[0], parity_by_key[k].forward_delta) for k in parity_by_key
                 if k[1] == option_type], target)
            choices.append(StrikeChoice(
                target_delta=target, option_type=option_type,
                engine_strike=eng[0] if eng else None,
                engine_delta=eng[1] if eng else None,
                parity_strike=par[0] if par else None,
                parity_delta=par[1] if par else None))

    df = parity.forward.discount_factor
    implied_rate = None
    if df and 0 < df < 1 and parity.t_years and parity.t_years > 0:
        implied_rate = -math.log(df) / parity.t_years

    atm_strike = parity.skew.atm_strike
    atm_engine = None
    if atm_strike is not None:
        at_atm = [engine_by_key[k].iv for k in engine_by_key if k[0] == atm_strike]
        atm_engine = median(at_atm) if at_atm else None

    return DerivationDivergence(
        expiry=expiry, as_of=as_of, engine_solved=len(engine_by_key),
        parity_solved=len(parity_by_key), forward_recovered=parity.forward.forward,
        forward_status=parity.forward.status, parity_r_squared=parity.forward.r_squared,
        spot_used=spot, implied_rate=implied_rate, assumed_rate=assumed_rate,
        atm_iv_engine=atm_engine, atm_iv_parity=parity.skew.atm_iv,
        median_abs_iv_diff=median(iv_diffs) if iv_diffs else None,
        max_abs_delta_diff=max(delta_diffs) if delta_diffs else None,
        choices=tuple(choices))
