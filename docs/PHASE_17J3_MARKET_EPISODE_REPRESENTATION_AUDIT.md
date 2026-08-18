# Phase 17J.3 — Market Episode Representation Audit

**Status: AUDIT ONLY. No code.** Answers whether `Episode` is the
correct similarity boundary, before touching the metric that just
failed validation (`PHASE_17J2_REVISIT_SWEEP_VALIDATION_RESULT.md`).

---

## 1. Does `Episode` preserve event sequence, temporal ordering, duration, transitions, magnitude, lifecycle?

Read in full: `bujji/market_episode/models.py` (the `Episode`
dataclass, 10 fields), `bujji/market_episode/engine.py` (growth/
transition logic, previously fully audited in `PHASE_17GA3`),
`bujji/market_episode/query.py`.

| Property | Preserved? | Detail |
|---|---|---|
| **Event sequence (membership)** | Yes, but order is INSERTION order, not independently verified temporal order | `originating_event_ids: Tuple[str, ...]` only ever grows by appending the newly-joined event's id (`_grow_episode`). Nothing inside `market_episode.engine` checks that appended events are chronologically later than what's already there — it trusts the caller to call `process_event` in timestamp order. Confirmed this trust is currently well-placed (`reality_structure_bridge.build_episodes_and_events` iterates `HistoricalObservationStore.range()`'s results, which are ascending-timestamp by construction, 17H.4) — but this is a caller discipline, not an engine guarantee. |
| **Temporal ordering (per-event timestamps)** | **No, not on `Episode` itself.** | `Episode` carries exactly THREE timestamps: `start_time` (founding event, fixed forever), `latest_update` (most recent snapshot), `end_time` (only once CLOSED). There is no per-event timestamp list on `Episode` — to know WHEN each member event happened, a consumer must independently hold the original `MarketEvent` objects and look up each `event_id`'s own `.timestamp` field. `Episode` alone is not self-sufficient for reconstructing a time series. |
| **Duration** | Partially | `end_time - start_time` is derivable once CLOSED; while OPEN, only `start_time`→`latest_update` elapsed time is knowable, and that changes on the next growth/transition. No cumulative "time spent in each state" field exists anywhere. |
| **Transitions (state-change history)** | **No — only the CURRENT state is stored.** | `current_state` is a single field, overwritten (as a new immutable snapshot) on every transition. There is no field recording "went QUIESCENT at time X, reactivated at time Y." `taxonomy.VALID_TRANSITIONS` defines what transitions are legal, but nothing on `Episode` records which transitions actually occurred, or when, beyond what's implicit in comparing snapshots. |
| **Magnitude** | **No — zero magnitude fields on `Episode`.** | Price magnitude (the `delta` that drove an event) lives ONLY in `MarketEvent.detail["delta"]`, referenced by `Episode.originating_event_ids`, never copied onto `Episode` itself (by explicit design — see `models.py`'s own docstring: "NEVER copies an event's or observation's payload"). To know HOW BIG a move was, a consumer must join back to the original events. |
| **Lifecycle (creation → closure)** | Partially | `current_state` plus `start_time`/`end_time` give the two hard boundary points (creation, closure) and whatever state the episode happens to be in when read. The QUIESCENT transition point specifically (when it went quiet before closing) is not recorded as a field, though it IS implicitly reconstructable if every snapshot along the way was kept (see §2). |

**Headline finding: `Episode` as a single object is a compact
MEMBERSHIP + CURRENT-STATE record, not a trajectory record.** It
answers "what belongs to this episode and what state is it in right
now" very well. It does not, by itself, answer "how did this episode
evolve" — that requires assembling multiple `Episode` snapshots over
time plus the referenced `MarketEvent`s' own timestamps/magnitudes.
This is not a defect — it is a deliberate, documented design choice
(append-only immutable snapshots, ids-only referencing) — but it means
a single `Episode.get()`-style read was never going to carry a
trajectory, and the current similarity engine's failure (comparing
only a session's FINAL structure state) is consistent with reading
only the last snapshot of an object that was never meant to be read
that way alone.

## 2. Can a historical session be represented as an Episode Timeline from existing infrastructure?

**Yes — the infrastructure already exists, unused by the current
bridge.** Three separate append-only journals already exist, one per
layer of the stack, all following the identical pattern (mirrors
Series 74/75's convention, structurally verified append-only — see
`market_episode/journal.py`'s own cited test,
`test_journal_append_only_structurally`):

| Layer | Journal | Records |
|---|---|---|
| Events | `live_market_events.journal.MarketEventJournal.record_event()` | Every `MarketEvent`, as detected |
| Episodes | `market_episode.journal.MarketEpisodeJournal.record_episode()` | Every `Episode` SNAPSHOT — explicitly documented to support "multiple snapshots per episode_id (one per growth/transition event)" (its own `read_episode_snapshots()` docstring) |
| Price structure | `msi_price_structure.journal` `.record_assessment()` | Every `PriceStructureAssessment` |

**`reality_structure_bridge.build_episodes_and_events()` (17G.A) does
not use any of these.** It loops `process_event`/`advance_time` and
keeps only the FINAL `episodes`/`events` tuples, discarding every
intermediate snapshot along the way — the loop already visits every
intermediate state, it just doesn't record it. Wiring the bridge to
call `MarketEpisodeJournal.record_episode(episode)` (and, if desired,
`msi_price_structure.journal`'s equivalent) after each step would
produce a full, ordered Episode Timeline for a historical session
using **zero new architecture** — the exact same reuse-not-rebuild
pattern this whole engagement has followed since 17H. This is
implementation work, correctly out of scope for this audit-only phase,
but the audit's answer to "can this be done from existing
infrastructure" is an unambiguous yes.

## 3. Should similarity compare (A) final structure state, (B) episode evolution path, or (C) both?

**The evidence from `PHASE_17J2_REVISIT_SWEEP_VALIDATION_RESULT.md`
directly supports B or C over A alone.** That sweep's own diagnosis:
*"A single end-of-session snapshot compresses away exactly the path
information that would distinguish these shapes... 2020-03-23's
violent morning drop is invisible in a 15:25 read."* Combined with
§1's finding here — that `Episode`/`PriceStructureAssessment` were
never designed to be read as a single final value in the first place —
**(A) has now been both empirically shown insufficient and
structurally explained as insufficient**, not just suspected.

- **(A) final state alone**: already tried, already failed validation.
  Not recommended on its own.
- **(B) episode evolution path**: directly addresses the diagnosed
  cause (shape, not endpoint, is what distinguishes a V-shaped
  reversal from a grinding breakdown from a gap-shock). Requires the
  timeline capability from §2.
- **(C) both**: final state is cheap, already-built, and not without
  information (it does capture where a session ended up structurally,
  which is itself sometimes relevant) — combining it with a path
  comparison rather than discarding it outright loses nothing and
  costs little, since the final state is a proper subset of what an
  evolution path already contains (the path's last point IS the final
  state).

**This audit's finding favors (C)**, not because averaging two
approaches is inherently better, but because (B)'s data already
contains (A) as its terminal point — there is no real cost to keeping
both available for the design phase that follows, only a design
question (how to weight/combine them, explicitly out of scope for this
audit per the "no scoring formulas" restriction).

## 4. Explicitly not introduced here, per this phase's restriction

No weights, no scoring formula, no ML, no clustering, no strategy
logic. This document identifies WHAT should be compared (structural
membership/state history reconstructed via existing journals) and
answers whether the infrastructure to build it exists (yes) — it does
not specify HOW two timelines would be compared, what distance
function would apply to a path, or any threshold. That is the next,
separate, deliberately-scoped design phase.

## 5. Summary

1. `Episode` alone is a membership + current-state record, not a
   trajectory — confirmed by full model/engine read, not assumed.
2. A full historical Episode Timeline is buildable from entirely
   existing, unused journal infrastructure (`MarketEpisodeJournal` and
   its siblings at every layer) — zero new architecture required, only
   wiring the already-built 17G.A bridge to record instead of discard
   intermediate snapshots.
3. The validation failure is now explained, not just observed: reading
   only the final state of an object explicitly designed to carry a
   growth history was always going to lose exactly the path
   information that distinguishes one stress event from another.
   Evidence favors comparing episode evolution paths (B), with final
   structure state retained as a cheap, already-available special case
   (C) rather than discarded.

**The correct object of comparison, going forward, is an Episode
Timeline (a full ordered sequence of Episode/assessment snapshots across
a session), not a single end-of-session `Episode`/`PriceStructureAssessment`
read.** Defining how two such timelines get compared remains an open,
separate design decision.
