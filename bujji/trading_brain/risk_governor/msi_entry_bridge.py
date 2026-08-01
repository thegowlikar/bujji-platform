"""MSI-to-PaperBroker entry-order-construction bridge — BUJJI Options
OS, Numeric Risk Governor integration.

STANDALONE. Not wired into run_live_shadow.py or any live entrypoint
yet -- that is a separate, explicitly-approved step. No import of
bujji.broker.fyers or bujji.broker.hybrid anywhere in this module --
verified by tests/test_entry_order_construction.py's own AST-based
import check, the same pattern already proven for
production_runtime/d0_rehearsal_runtime.py.

Bridges two generations deliberately kept separate everywhere else in
this codebase: MSI's own `TradeConstructionAssessment`/`StrikeLeg`
(Series 90) is the input; `bujji.core.models.OrderRequest`/
`OptionContract` (the shape `PaperBroker.place_order()` actually
consumes) is the only output shape ever produced. Trading Brain v3's
OWN OrderRequest type is never touched here -- nothing on this path
needs it.

Pipeline, matching the same one-composition-owner discipline as
Gate B's engine.assess() and MIL Next's snapshot_builder.build_snapshot():

  1. mint a Gate A position group for this trade intent (or return a
     NO_TRADE-shaped result immediately if MSI itself never
     constructed a trade)
  2. build defined-risk / portfolio-limit / capital-check inputs
  3. call the real Governor assess()
  4. ALLOW -> build real OrderRequest objects (never before this point)
     VETO  -> stop; zero OrderRequest objects are ever constructed

DISCLOSED, HONEST STATUS: exactly SIX real MSI strategy families have
a reviewed, closed-form defined-risk formula wired:

  - LONG_DIRECTIONAL (a single long option leg, `bujji.
    msi_trade_construction.engine._build_legs`'s "LONG_DIRECTIONAL"
    branch: one BUY leg, taxonomy.ROLE_LONG, nothing else). Maximum
    loss is the premium paid, in full -- reuses Gate B's existing
    LONG_OPTION_PREMIUM_PAID formula verbatim.
  - NEUTRAL_PREMIUM_BUYING (a long strangle: buy CE + buy PE, both
    taxonomy.ROLE_LONG). Maximum loss is the SUM of premium paid across
    both legs -- textbook-correct for any all-long combination
    regardless of strike/expiry, using MULTI_LEG_LONG_PREMIUM_PAID (a
    genuine generalization of LONG_OPTION_PREMIUM_PAID to N required
    long legs, reviewed and added alongside this family).
  - VOLATILITY_EXPANSION (a long ATM straddle: buy CE + buy PE, both
    taxonomy.ROLE_LONG -- the SAME `_build_legs` branch as
    NEUTRAL_PREMIUM_BUYING, just ATM-anchored strikes instead of
    delta-targeted ones). Mechanically identical payoff shape, so this
    reuses MULTI_LEG_LONG_PREMIUM_PAID and the same role-translation
    logic verbatim -- no new formula math was needed for this one.
  - IRON_CONDOR (four legs: short CE + short PE at delta-targeted
    strikes, long CE + long PE wings at short_strike +/- a configured
    width). A genuinely NEW formula, IRON_CONDOR_MAX_WING_WIDTH_MINUS_
    TOTAL_CREDIT: an iron condor is two independent vertical credit
    spreads sharing one position; at expiry at most one side can be
    breached, so maximum loss is the WIDER of the two wing widths minus
    the TOTAL combined credit from both spreads (never just the
    breached side's own credit, and never assuming symmetric wings).
    `_build_legs` assigns taxonomy.ROLE_SHORT to BOTH the short call
    and short put (same collision class NEUTRAL_PREMIUM_BUYING already
    surfaced -- disambiguated by option_type), but taxonomy.
    ROLE_WING_UPPER/ROLE_WING_LOWER are already distinct per leg by
    construction (WING_UPPER is always the long call, WING_LOWER always
    the long put -- confirmed by reading `_build_legs` directly).
  - IRON_FLY (four legs, the SAME `_build_legs` branch as IRON_CONDOR --
    `if family in ("IRON_CONDOR", "IRON_FLY")`, confirmed by reading the
    branch directly -- differing only in HOW the short strikes are
    chosen: IRON_CONDOR targets a configured delta on each side
    independently, IRON_FLY anchors the short call ATM and puts the
    short put at that SAME strike, i.e. a butterfly-shaped condor with
    both shorts at one strike. The resulting leg SHAPE is identical:
    ROLE_SHORT x2 (CE+PE), ROLE_WING_UPPER (long call), ROLE_WING_LOWER
    (long put). The payoff mechanics `_iron_condor_max_loss` already
    encodes -- at most one side breached, max loss = wider wing width
    minus TOTAL combined credit, via max() so asymmetric wings (here,
    guaranteed whenever expected-move-derived wing width differs from
    the ATM-vs-delta strike offset) are handled correctly -- apply
    identically, since nothing in that formula assumes the two short
    strikes are distinct. Reuses IRON_CONDOR_MAX_WING_WIDTH_MINUS_
    TOTAL_CREDIT and the identical role-translation lambda verbatim --
    no new formula math, only a new dict entry, same pattern as
    VOLATILITY_EXPANSION reusing NEUTRAL_PREMIUM_BUYING's formula.
  - BUTTERFLY (three legs, `_build_legs`'s dedicated "BUTTERFLY" branch:
    buy 1x lower CE wing, sell 2x ATM CE body, buy 1x upper CE wing --
    a genuinely different payoff shape from the iron condor family, a
    single-option-type long butterfly, not two credit spreads). A
    genuinely NEW formula, BUTTERFLY_NET_DEBIT_PAID: at expiry, at or
    beyond either wing the structure's total value collapses to zero,
    so maximum loss is exactly the net debit paid to establish it --
    cost of both wings minus the body's premium received, each leg
    using its own actual quantity (never assuming the body's 2x ratio
    holds, in case of malformed/partial-fill data). taxonomy.
    ROLE_WING_LOWER/ROLE_BODY/ROLE_WING_UPPER are already distinct role
    strings by construction (no collision class here at all, unlike
    every prior family) -- role translation is the identity function.

Audited finding NEUTRAL_PREMIUM_BUYING's addition surfaced: `_build_legs`
assigns the SAME MSI role string ("LONG") to BOTH legs of a
straddle/strangle -- a flat role-string translation would have
collided and silently dropped one leg. Role translation is therefore a
per-leg FUNCTION (disambiguating by `leg.option_type` where needed),
not a flat dict, for every family from here on.

Every OTHER real MSI strategy family (COVERED/NEUTRAL_PREMIUM_SELLING/
RATIO/SHORT_DIRECTIONAL/SYNTHETIC/CALENDAR/VOLATILITY_COMPRESSION)
still has NO formula and still VETOes UNDEFINED_RISK_NO_STRESS_MODEL,
unconditionally. VOLATILITY_COMPRESSION is
VOLATILITY_EXPANSION's naked-short twin (SELL both legs instead of
BUY) from the exact same `_build_legs` branch -- sharing the branch
does NOT mean sharing defined-risk eligibility; it stays vetoed,
genuinely unbounded, tested explicitly. COVERED can never be safely
treated as
defined-risk through this path: its short call leg is only bounded if
genuinely covered by a real underlying equity position, which this
options-leg-only bridge has no way to confirm -- vetoing it is
correct, not a gap to "fix" without equity-position tracking existing
first. Combined with Gate C's own still-uncertified margin provider,
every real call through this bridge STILL VETOes today (on the capital
check, if not on defined risk) -- by design, matching this whole
session's fail-closed discipline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from bujji.core.models import OptionContract as CoreOptionContract
from bujji.core.models import OrderRequest as CoreOrderRequest
from bujji.core.models import OrderResult as CoreOrderResult
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.msi_trade_construction.models import StrikeLeg, TradeConstructionAssessment
from bujji.trading_brain.risk_governor.capital_check import CapitalCheckAssessment, CapitalCheckInput, assess_capital
from bujji.trading_brain.risk_governor.defined_risk import DefinedRiskAssessment, StrategyRiskProfile, assess_defined_risk
from bujji.trading_brain.risk_governor.engine import RiskVerdict, assess
from bujji.trading_brain.risk_governor.portfolio_limits import PortfolioLimitAssessment, PortfolioLimits, assess_portfolio_limits
from bujji.trading_brain.risk_governor.position_group_fold import PositionGroupState, fold
from bujji.trading_brain.risk_governor.position_group_mint import mint_position_group_id

Clock = Callable[[], datetime]

# Per-family spec: (required Gate B role labels, Gate B formula name,
# leg-aware role-translation function). `lot_size` is deliberately NOT
# baked in here -- it's a real, caller-supplied runtime value (the
# actual NIFTY lot size in effect), never hardcoded into a static
# profile; the real StrategyRiskProfile is constructed per-call below.
#
# Role translation is a FUNCTION of the leg, not a flat dict keyed by
# MSI's raw role string -- audited finding: bujji.msi_trade_construction.
# engine._build_legs assigns the SAME MSI role ("LONG") to BOTH legs of
# a straddle/strangle (e.g. NEUTRAL_PREMIUM_BUYING's buy-CE + buy-PE).
# A flat string->string translation would collide and silently drop one
# leg from the resulting dict. Disambiguating by leg.option_type (CE/PE)
# instead.
#
# LONG_DIRECTIONAL, NEUTRAL_PREMIUM_BUYING, VOLATILITY_EXPANSION,
# IRON_CONDOR, IRON_FLY, and BUTTERFLY are populated -- see module
# docstring for why every other real MSI family still has none.
_MSI_FORMULA_SPECS: Dict[str, Tuple[Tuple[str, ...], str, Callable[["StrikeLeg"], str]]] = {
    "LONG_DIRECTIONAL": (("LONG_LEG",), "LONG_OPTION_PREMIUM_PAID", lambda leg: "LONG_LEG"),
    "NEUTRAL_PREMIUM_BUYING": (
        ("LONG_LEG_CE", "LONG_LEG_PE"), "MULTI_LEG_LONG_PREMIUM_PAID",
        lambda leg: f"LONG_LEG_{leg.option_type}",
    ),
    # Mechanically identical to NEUTRAL_PREMIUM_BUYING -- same
    # bujji.msi_trade_construction.engine._build_legs branch ("BUY"
    # side -> ROLE_LONG for both legs), same all-long, already-bounded
    # payoff (an ATM long straddle instead of a delta-targeted long
    # strangle). Reuses the identical formula and role-translation
    # logic verbatim -- no new formula math, only a new dict entry.
    "VOLATILITY_EXPANSION": (
        ("LONG_LEG_CE", "LONG_LEG_PE"), "MULTI_LEG_LONG_PREMIUM_PAID",
        lambda leg: f"LONG_LEG_{leg.option_type}",
    ),
    # Four legs: bujji.msi_trade_construction.engine._build_legs's
    # IRON_CONDOR branch assigns taxonomy.ROLE_SHORT to BOTH the short
    # call and short put (same collision class as NEUTRAL_PREMIUM_BUYING
    # -- disambiguated by option_type), and taxonomy.ROLE_WING_UPPER /
    # ROLE_WING_LOWER to the long call / long put respectively -- those
    # two are already distinct role strings by construction (WING_UPPER
    # is always the long CALL, WING_LOWER is always the long PUT,
    # confirmed by reading _build_legs directly, never assumed), so no
    # option_type suffix is needed for them.
    "IRON_CONDOR": (
        ("SHORT_LEG_CE", "SHORT_LEG_PE", "LONG_LEG_CE", "LONG_LEG_PE"),
        "IRON_CONDOR_MAX_WING_WIDTH_MINUS_TOTAL_CREDIT",
        lambda leg: (
            f"SHORT_LEG_{leg.option_type}" if leg.role == "SHORT"
            else "LONG_LEG_CE" if leg.role == "WING_UPPER"
            else "LONG_LEG_PE" if leg.role == "WING_LOWER"
            else leg.role
        ),
    ),
    # Mechanically identical to IRON_CONDOR -- same `_build_legs` branch,
    # same 4-role shape (ROLE_SHORT x2 + ROLE_WING_UPPER + ROLE_WING_LOWER),
    # same defined-risk payoff. See module docstring for the ATM-vs-
    # delta-targeted short-strike distinction that does NOT change the
    # formula's applicability.
    "IRON_FLY": (
        ("SHORT_LEG_CE", "SHORT_LEG_PE", "LONG_LEG_CE", "LONG_LEG_PE"),
        "IRON_CONDOR_MAX_WING_WIDTH_MINUS_TOTAL_CREDIT",
        lambda leg: (
            f"SHORT_LEG_{leg.option_type}" if leg.role == "SHORT"
            else "LONG_LEG_CE" if leg.role == "WING_UPPER"
            else "LONG_LEG_PE" if leg.role == "WING_LOWER"
            else leg.role
        ),
    ),
    # Three legs, all distinct roles by construction (WING_LOWER, BODY,
    # WING_UPPER) -- no collision class here, so translation is simply
    # the identity function. See module docstring for the formula.
    "BUTTERFLY": (
        ("WING_LOWER", "BODY", "WING_UPPER"),
        "BUTTERFLY_NET_DEBIT_PAID",
        lambda leg: leg.role,
    ),
}


@dataclass(frozen=True)
class EntryOrderConstructionResult:
    position_group_id: Optional[str]        # None when MSI never constructed a trade -- nothing minted
    verdict: Optional[RiskVerdict]           # None for the same reason
    order_requests: Tuple[CoreOrderRequest, ...]   # non-empty ONLY when verdict.decision == "ALLOW"
    decision_trace: str


def _leg_client_order_id(position_group_id: str, index: int) -> str:
    return f"{position_group_id}-LEG-{index}"


def _leg_to_core_contract(leg: StrikeLeg, underlying: str, lot_size: int) -> CoreOptionContract:
    from bujji.core.enums import OptionType

    symbol = f"{underlying}{leg.expiry}{int(leg.strike)}{leg.option_type}"
    return CoreOptionContract(
        symbol=symbol, underlying=underlying, strike=int(leg.strike),
        option_type=OptionType.CE if leg.option_type == "CE" else OptionType.PE,
        expiry=leg.expiry, lot_size=lot_size,
    )


def construct_and_gate_entry(
    decision_id: str,
    trade: TradeConstructionAssessment,
    underlying: str,
    lot_size: int,
    journal: PositionGroupJournal,
    active_group_states: List[PositionGroupState],
    exposure_by_position_group_id: Dict[str, float],
    portfolio_limits: PortfolioLimits,
    configured_risk_capital: float,
    clock: Clock,
) -> EntryOrderConstructionResult:
    """Pure w.r.t. everything except the journal append (Gate A's own,
    already-transactional, already-tested write path). Never calls a
    broker of any kind -- see dispatch_via_paper_broker() for the
    ONLY function in this module that ever does, and only after this
    function's own verdict is ALLOW."""
    if not trade.constructed or not trade.legs:
        return EntryOrderConstructionResult(
            position_group_id=None, verdict=None, order_requests=(),
            decision_trace=f"NO_TRADE: MSI did not construct a trade ({trade.rejection_reason}).",
        )

    # Audited finding: leg.ratio <= 0 would silently produce a negative
    # or zero requested_quantity, corrupting every downstream quantity/
    # exposure computation without ever raising. A non-positive ratio is
    # a malformed construction, never a legitimate strategy shape --
    # reject before minting anything, same discipline as the
    # not-constructed/no-legs check above.
    malformed_legs = [leg for leg in trade.legs if leg.ratio <= 0]
    if malformed_legs:
        return EntryOrderConstructionResult(
            position_group_id=None, verdict=None, order_requests=(),
            decision_trace=(
                f"NO_TRADE: {len(malformed_legs)} leg(s) have a non-positive ratio "
                f"(malformed construction, never a legitimate strategy shape)."
            ),
        )

    mint = mint_position_group_id(journal, decision_id, trade.strategy_family, underlying, clock=clock)
    pg_id = mint.position_group_id

    contract_client_order_map: Dict[str, str] = {}
    requested_quantities: Dict[str, int] = {}
    core_contracts: Dict[str, CoreOptionContract] = {}
    core_prices: Dict[str, Optional[float]] = {}
    core_sides: Dict[str, str] = {}
    leg_roles: Dict[str, str] = {}

    formula_spec = _MSI_FORMULA_SPECS.get(trade.strategy_family)
    role_translation_fn = formula_spec[2] if formula_spec else None

    for i, leg in enumerate(trade.legs):
        contract_id = f"C{i}"
        coid = _leg_client_order_id(pg_id, i)
        contract_client_order_map[contract_id] = coid
        requested_quantities[coid] = leg.ratio * lot_size
        core_contracts[coid] = _leg_to_core_contract(leg, underlying, lot_size)
        core_prices[coid] = leg.premium
        core_sides[coid] = leg.side
        # Translated to Gate B's own role vocabulary (LONG_LEG, or
        # LONG_LEG_CE/LONG_LEG_PE for multi-leg families) only for
        # families with a real formula wired -- MSI's raw role string
        # (e.g. taxonomy.ROLE_LONG == "LONG") is never assumed to match
        # Gate B's labels by coincidence, and is disambiguated per-leg
        # (by option_type) rather than by a flat string->string mapping,
        # since MSI assigns the SAME role to multiple legs of a
        # straddle/strangle. Untranslated families keep the raw MSI
        # role, harmless since profile is None for them and
        # defined_risk.py never reaches a role lookup.
        leg_roles[coid] = role_translation_fn(leg) if role_translation_fn else leg.role

    journal.append_event(
        pg_id, "CONSTRUCTED", f"{pg_id}:CONSTRUCTED:0",
        {"contract_client_order_map": contract_client_order_map,
         "requested_quantities": requested_quantities,
         "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
        clock=clock,
    )
    state = fold(journal.read_events(pg_id))

    orders_for_defined_risk = {
        coid: _make_trading_brain_shaped_order(coid, core_prices[coid])
        for coid in contract_client_order_map.values()
    }
    profile = None
    if formula_spec is not None:
        required_roles, formula_name, _ = formula_spec
        profile = StrategyRiskProfile(
            strategy_id=trade.strategy_family, required_leg_roles=required_roles,
            formula=formula_name, lot_size=lot_size,
        )
    defined_risk = assess_defined_risk(
        state,
        contracts_by_client_order_id=core_contracts,
        orders_by_client_order_id=orders_for_defined_risk,
        leg_roles=leg_roles, profile=profile, clock=clock,
    )

    all_states = list(active_group_states) + [state]
    exposures = dict(exposure_by_position_group_id)
    if pg_id not in exposures:
        # Audited finding: `core_prices[coid] or 0.0` would silently
        # treat an unknown premium (leg.premium is None -- a real,
        # disclosed-as-possible MSI state) as ZERO notional, understating
        # exposure/concentration rather than flagging it as unknown. If
        # ANY leg's premium is unresolved, this group's exposure is
        # UNTRUSTED -- deliberately left absent from `exposures` so
        # assess_portfolio_limits' own existing, already-tested
        # fail-closed rule (EXPOSURE_DATA_MISSING for an active group
        # with no exposure entry) fires, rather than inventing a second,
        # parallel veto path for the same underlying problem.
        any_premium_missing = any(core_prices[coid] is None for coid in requested_quantities)
        if not any_premium_missing:
            exposures[pg_id] = sum(
                (requested_quantities[coid] * core_prices[coid]) for coid in requested_quantities
            )
    portfolio_assessment = assess_portfolio_limits(all_states, exposures, portfolio_limits, clock=clock)

    capital_check = assess_capital(
        CapitalCheckInput(
            margin_verified=False,   # no certified whole-book provider exists -- see Gate C scaffold
            required_margin=None, available_capital=None,
            configured_risk_capital=configured_risk_capital, margin_source="NONE",
        ),
        clock=clock,
    )

    verdict = assess(state, defined_risk, portfolio_assessment, capital_check, clock=clock)

    order_requests: Tuple[CoreOrderRequest, ...] = ()
    if verdict.decision == "ALLOW":
        # Audited finding: defined_risk.py was told (via
        # _make_trading_brain_shaped_order) that every leg is a LIMIT
        # order -- that claim must be made TRUE for the real order this
        # bridge actually constructs, never left to default to a MARKET
        # order (limit_price=None) by omission. A risk assessment that
        # priced this as a bounded LIMIT order must never be allowed to
        # authorize an actually-unbounded MARKET order.
        order_requests = tuple(
            CoreOrderRequest(
                contract=core_contracts[coid],
                side=_to_core_side(core_sides[coid]),
                quantity=requested_quantities[coid],
                client_order_id=coid,
                limit_price=core_prices[coid],
                reference_price=core_prices[coid],
                tag=f"MSI:{trade.strategy_family}:{decision_id}",
            )
            for coid in contract_client_order_map.values()
        )

    return EntryOrderConstructionResult(
        position_group_id=pg_id, verdict=verdict, order_requests=order_requests,
        decision_trace=verdict.decision_trace,
    )


def _to_core_side(side: str):
    from bujji.core.enums import Side
    return Side.BUY if side == "BUY" else Side.SELL


def _make_trading_brain_shaped_order(client_order_id: str, reference_price: Optional[float]):
    """A minimal stand-in matching only the fields defined_risk.py's
    market-order check actually reads (order_type, reference_price) --
    never a real Trading Brain v3 OrderRequest, since this bridge never
    constructs real v3 orders. Kept intentionally tiny and local rather
    than importing v3's real, much larger OrderRequest for two fields."""
    from types import SimpleNamespace
    return SimpleNamespace(order_type="LIMIT", reference_price=reference_price)


async def dispatch_via_paper_broker(
    order_requests: Tuple[CoreOrderRequest, ...],
    paper_broker,
) -> Tuple[CoreOrderResult, ...]:
    """The ONLY function in this module that ever calls a broker method.
    Structurally restricted to PaperBroker: an isinstance check, not
    just a type hint, since a type hint alone is not enforced at
    runtime and this codebase's own established discipline (P1-1's
    disable_live_execution(), Gate D0's import-boundary test) is to
    make safety guarantees structural, not conventions someone could
    accidentally bypass by passing the wrong object."""
    from bujji.broker.paper import PaperBroker

    if not isinstance(paper_broker, PaperBroker):
        raise TypeError(
            f"dispatch_via_paper_broker() only ever accepts a PaperBroker instance, "
            f"got {type(paper_broker)!r}"
        )

    results = []
    for request in order_requests:
        result = await paper_broker.place_order(request)
        results.append(result)
    return tuple(results)
