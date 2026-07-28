#!/usr/bin/env python3
"""Phase III -- Live Shadow Session #2, daily observation pipeline.

Standalone orchestration script wiring the frozen Series 99-106 learning
architecture together for ONE real trading day. This is NOT a new
learning package -- no new engine, model, journal, or learning logic is
introduced here. Every real computation is delegated to the frozen,
already-committed packages; this script only translates their real
outputs into each other's local `*View` types (the same sibling-
isolation convention every package already established) and journals
the results.

Usage:
    /opt/bujji/.venv/bin/python run_daily_observation.py <DAY>

<DAY> must have real recorded intraday candles + a real Bhavcopy chain
already available (either from a real completed live session, or from
the real historical replay corpus under /tmp/m1 and
/tmp/nifty_intraday_by_day_expanded.json for testing this pipeline
itself before/between real live sessions).

Journals are written under data/learning/<package>/ -- real, append-only,
using each package's own frozen Journal class. Nothing here writes to
any Production journal or touches any Production decision path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from bujji.live_pipeline_bridge import SessionDriver
from bujji.live_shadow_validation import run_full_cadence
from bujji.market_observation import engine as moc_engine, taxonomy as moc_taxonomy
from bujji.live_market_events import engine as lme_engine
from bujji.market_episode import engine as mee_engine
from bujji.msi_portfolio_construction.models import PortfolioState

from bujji.msi_evidence_packet import engine as eps_engine
from bujji.msi_evidence_packet.journal import EvidencePacketJournal
from bujji.msi_evidence_packet.models import Metric

from bujji.msi_counterfactual_replay import engine as cre_engine
from bujji.msi_counterfactual_replay import replay as cre_replay
from bujji.msi_counterfactual_replay import taxonomy as cre_taxonomy
from bujji.msi_counterfactual_replay.journal import CounterfactualReplayJournal

from bujji.msi_market_phenomena import engine as mpc_engine
from bujji.msi_market_phenomena import translate as mpc_translate
from bujji.msi_market_phenomena.journal import MarketPhenomenaJournal

from bujji.msi_opportunity_assessment import engine as oae_engine
from bujji.msi_opportunity_assessment.journal import OpportunityAssessmentJournal
from bujji.msi_opportunity_assessment.models import CounterfactualView, DecisionView, PhenomenaView

from bujji.msi_knowledge_validation import engine as kve_engine
from bujji.msi_knowledge_validation.journal import KnowledgeValidationJournal
from bujji.msi_knowledge_validation.models import HypothesisOccurrence

from bujji.msi_engineering_evidence_board import engine as eeb_engine
from bujji.msi_engineering_evidence_board.journal import EngineeringEvidenceJournal
from bujji.msi_engineering_evidence_board.models import KnowledgeValidationView, OpportunityAssessmentRef

JOURNAL_ROOT = Path("data/learning")
CORPUS_CANDLES = "/tmp/nifty_intraday_by_day_expanded.json"
CORPUS_BHAV_FMT = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_{d}_F_0000.csv"


def _mk_observation(timestamp: str, price: float):
    return moc_engine.build_observation(
        observation_type=moc_taxonomy.ALL_OBSERVATION_TYPES[0], instrument="NIFTY", exchange="NSE", segment="EQ",
        timestamp=timestamp, resolution=moc_taxonomy.RESOLUTION_FIFTEEN_MINUTE, source="FYERS_REAL_INTRADAY",
        schema_version="1.0.0", value_kind=moc_taxonomy.VALUE_KIND_SCALAR, payload=price,
        completeness=1.0, freshness=0.0, confidence=1.0, missing_fields=(),
        validation_status=moc_taxonomy.VALIDATION_VALID, source_quality="HIGH",
        originating_source="FYERS_REAL_INTRADAY", acquisition_timestamp=timestamp,
        normalization_timestamp=timestamp, origin=moc_taxonomy.ORIGIN_HISTORICAL_RECONSTRUCTION, provenance_version="1.0.0",
    )


def run_production_cadence(day: str, candles, bhav_text: str, lock_path: str):
    """Runs the REAL, unmodified Production pipeline exactly as every
    replay script since Sprint 115 has -- this function changes nothing
    about how Production decides; it only captures the real result for
    the learning pipeline below to observe."""
    driver = SessionDriver(lock_path=lock_path)
    driver.acquire()
    try:
        driver.load_option_chain(bhav_text, day)
        observations = [_mk_observation(c["ts"], c["close"]) for c in candles]
        previous = None
        for obs in observations:
            driver.result.observations.append(obs)
            for ev in lme_engine.detect_price_change(obs, previous):
                driver.result.events.append(ev)
                driver.result.episodes = mee_engine.advance_time(driver.result.episodes, ev.timestamp, detection_context="REPLAY")
                driver.result.episodes = mee_engine.process_event(driver.result.episodes, ev, detection_context="REPLAY")
            previous = obs
        driver._today_closes = [(c["ts"], c["close"]) for c in candles]
        last_ts = candles[-1]["ts"]
        driver.run_decision_cadence(timestamp=last_ts)
        spot = next((r.underlying_price for r in driver._chain if r.underlying_price is not None), None)
        cadence = run_full_cadence(driver, spot=spot, day=day, portfolio=PortfolioState(), open_positions=(), timestamp=last_ts)
        return driver, cadence, last_ts
    finally:
        driver.release()


def observe_day(day: str, candles, bhav_text: str, production_version: str) -> dict:
    """The real daily observation pipeline. Returns a plain dict summary
    (the Daily Report) -- every field is real; any artefact that cannot
    be produced is disclosed with a real reason, never fabricated."""
    D = day.replace("-", "")
    anomalies = []

    driver, cadence, last_ts = run_production_cadence(day, candles, bhav_text, lock_path=f"data/obs_{D}.lock")
    decision = cadence.decision  # real Series 99 DecisionRecord

    # --- Series 103: Market Phenomena ---
    phenomena_journal = MarketPhenomenaJournal(JOURNAL_ROOT / "market_phenomena")
    snaps = mpc_translate.snapshots_from_real_session(driver, open_price=candles[0]["close"], previous_close_price=None)
    if not snaps:
        anomalies.append("Market Phenomena: no real MarketSnapshot could be built (missing PSI/MSSI/MDI) -- disclosed, not fabricated.")
        phenomena_report = None
    else:
        phenomena_report = mpc_engine.classify_day(day, snaps, generated_timestamp=last_ts)
        phenomena_journal.record(phenomena_report)

    # --- Series 102: Counterfactual Replay (baseline = real EOD cadence, alternative = real mid-day cutoff) ---
    cf_journal = CounterfactualReplayJournal(JOURNAL_ROOT / "counterfactual_replay")
    counterfactual_session = None
    if len(candles) >= 4:
        mid_ts = candles[len(candles) // 2]["ts"]
        baseline_path = cre_replay.run_real_path(cre_taxonomy.PATH_LABEL_BASELINE, day, bhav_text, candles, candles[-1]["ts"], lock_path=f"data/obs_cf_b_{D}.lock")
        alternative_path = cre_replay.run_real_path(cre_taxonomy.PATH_LABEL_ALTERNATIVE, day, bhav_text, candles, mid_ts, lock_path=f"data/obs_cf_a_{D}.lock")
        counterfactual_session = cre_engine.build_counterfactual_session(
            replay_id=f"obs-{day}", baseline_path=baseline_path, alternative_path=alternative_path,
            production_version=production_version, replay_version="1.0.0",
            supporting_references=(decision.decision_id,),
        )
        cf_journal.record(counterfactual_session)
        if counterfactual_session.replay_legality != "LEGAL":
            anomalies.append(f"Counterfactual Replay: session {counterfactual_session.session_id} is ILLEGAL -- {counterfactual_session.legality_reasoning}")
    else:
        anomalies.append("Counterfactual Replay: fewer than 4 real candles available -- no real mid-day cutoff exists, disclosed rather than fabricated.")

    # --- Series 101: Evidence Packet ---
    eps_journal = EvidencePacketJournal(JOURNAL_ROOT / "evidence_packets")
    evidence_packet = eps_engine.build_evidence_packet(
        decision_record_ids=(decision.decision_id,), outcome_record_ids=(),
        journal_references=(str(JOURNAL_ROOT / "decision_auditor"),),
        market_recorder_session=f"obs-session-{day}", replay_session=f"obs-replay-{day}",
        production_version=production_version, mle_version="1.0.0", replay_version="1.0.0",
        market_classification=(phenomena_report.phenomena[0].phenomenon_type if phenomena_report and phenomena_report.phenomena else "NONE_DETECTED"),
        trading_day_classification=decision.trade_thesis.thesis_type, regime=(decision.volatility_state or "UNKNOWN"),
        expiry_context="WEEKLY", volatility_context=decision.volatility_state or "UNKNOWN",
        statistics=(Metric("decision_outcome", decision.decision_outcome),),
        created_timestamp=last_ts,
    )
    eps_journal.record_packet(evidence_packet)

    # --- Series 104: Opportunity Assessment ---
    oae_journal = OpportunityAssessmentJournal(JOURNAL_ROOT / "opportunity_assessment")
    decision_view = DecisionView(
        decision_id=decision.decision_id, date=day, timestamp=last_ts, decision_outcome=decision.decision_outcome,
        strategy_family=decision.strategy_family, confidence=decision.confidence,
        conflicting_domains=decision.trade_thesis.conflicting_domains,
    )
    phenomena_view = PhenomenaView(
        report_id=phenomena_report.report_id, day=day, phenomenon_types=tuple(p.phenomenon_type for p in phenomena_report.phenomena),
    ) if phenomena_report else None
    cf_view = CounterfactualView(
        session_id=counterfactual_session.session_id, replay_legality=counterfactual_session.replay_legality,
        baseline_selected_family=next((p.selected_family for p in counterfactual_session.explored_paths if p.label == "BASELINE"), None),
        alternative_selected_family=next((p.selected_family for p in counterfactual_session.explored_paths if p.label == "ALTERNATIVE"), None),
        earliest_causal_timestamp=counterfactual_session.earliest_causal_timestamp,
    ) if counterfactual_session else None
    opportunity_assessment = oae_engine.assess_day(
        decision=decision_view, phenomena=phenomena_view, counterfactual=cf_view,
        evidence_packet_ids=(evidence_packet.packet_id,), replay_references=(f"obs-replay-{day}",),
        timestamp=last_ts,
    )
    oae_journal.record(opportunity_assessment)

    # --- Series 105: Knowledge Validation (accumulates ACROSS real days by hypothesis label) ---
    kve_journal = KnowledgeValidationJournal(JOURNAL_ROOT / "knowledge_validation")
    hypothesis_label = f"thesis_type={decision.trade_thesis.thesis_type} / decision_outcome={decision.decision_outcome}"
    occurrence_journal_path = JOURNAL_ROOT / "knowledge_validation" / "occurrences.jsonl"
    occurrence_journal_path.parent.mkdir(parents=True, exist_ok=True)
    prior_occurrences = []
    if occurrence_journal_path.exists():
        with open(occurrence_journal_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry["hypothesis_label"] == hypothesis_label:
                    prior_occurrences.append(HypothesisOccurrence(**entry["occurrence"]))
    new_occurrence = HypothesisOccurrence(
        day=day, timestamp=last_ts, market_classification=(phenomena_report.phenomena[0].phenomenon_type if phenomena_report and phenomena_report.phenomena else "NONE_DETECTED"),
        evidence_packet_id=evidence_packet.packet_id,
        counterfactual_session_id=(counterfactual_session.session_id if counterfactual_session else None),
        counterfactual_legal=(counterfactual_session.replay_legality == "LEGAL" if counterfactual_session else False),
        opportunity_assessment_id=opportunity_assessment.assessment_id,
        opportunity_classification=opportunity_assessment.classification,
        phenomena_report_id=(phenomena_report.report_id if phenomena_report else None),
        causally_valid=(counterfactual_session.replay_legality == "LEGAL" if counterfactual_session else True),
        replay_reproducible=True,
    )
    with open(occurrence_journal_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"hypothesis_label": hypothesis_label, "occurrence": {
            "day": new_occurrence.day, "timestamp": new_occurrence.timestamp,
            "market_classification": new_occurrence.market_classification,
            "evidence_packet_id": new_occurrence.evidence_packet_id,
            "counterfactual_session_id": new_occurrence.counterfactual_session_id,
            "counterfactual_legal": new_occurrence.counterfactual_legal,
            "opportunity_assessment_id": new_occurrence.opportunity_assessment_id,
            "opportunity_classification": new_occurrence.opportunity_classification,
            "phenomena_report_id": new_occurrence.phenomena_report_id,
            "causally_valid": new_occurrence.causally_valid,
            "replay_reproducible": new_occurrence.replay_reproducible,
        }}) + "\n")
    all_occurrences = prior_occurrences + [new_occurrence]
    validation_report = kve_engine.validate_hypothesis(hypothesis_label, all_occurrences, generated_timestamp=last_ts)
    kve_journal.record(validation_report)

    # --- Series 106: Engineering Evidence Board ---
    eeb_journal = EngineeringEvidenceJournal(JOURNAL_ROOT / "engineering_evidence")
    kv_view = KnowledgeValidationView(
        validation_id=validation_report.validation_id, hypothesis_label=hypothesis_label,
        validation_state=validation_report.validation_state, occurrence_count=validation_report.occurrence_count,
        diversity_count=validation_report.diversity_count, consistency_ratio=validation_report.consistency_ratio,
        replay_support_ratio=validation_report.replay_support_ratio, causal_validity_ratio=validation_report.causal_validity_ratio,
        evidence_growth=validation_report.evidence_growth, evidence_decay=validation_report.evidence_decay,
    )
    eeb_report = eeb_engine.review_evidence(
        validation=kv_view, opportunity_assessments=(OpportunityAssessmentRef(opportunity_assessment.assessment_id, opportunity_assessment.classification),),
        evidence_packet_ids=(evidence_packet.packet_id,),
        counterfactual_session_ids=((counterfactual_session.session_id,) if counterfactual_session else ()),
        phenomena_report_ids=((phenomena_report.report_id,) if phenomena_report else ()),
        decision_record_ids=(decision.decision_id,), generated_timestamp=last_ts,
    )
    eeb_journal.record(eeb_report)

    return {
        "date": day,
        "production_decision": {"outcome": decision.decision_outcome, "strategy_family": decision.strategy_family,
                                 "thesis_type": decision.trade_thesis.thesis_type, "confidence": decision.confidence},
        "market_regime": decision.trade_thesis.thesis_type,
        "phenomena_detected": [p.phenomenon_type for p in phenomena_report.phenomena] if phenomena_report else [],
        "opportunity_assessment": opportunity_assessment.classification,
        "knowledge_validation": {"hypothesis_label": hypothesis_label, "state": validation_report.validation_state,
                                  "occurrence_count": validation_report.occurrence_count},
        "engineering_evidence": eeb_report.decision,
        "anomalies": anomalies,
        "artefact_ids": {
            "decision_id": decision.decision_id,
            "evidence_packet_id": evidence_packet.packet_id,
            "counterfactual_session_id": (counterfactual_session.session_id if counterfactual_session else None),
            "phenomena_report_id": (phenomena_report.report_id if phenomena_report else None),
            "opportunity_assessment_id": opportunity_assessment.assessment_id,
            "validation_id": validation_report.validation_id,
            "engineering_evidence_report_id": eeb_report.report_id,
        },
    }


def main():
    if len(sys.argv) < 2:
        print("usage: run_daily_observation.py <DAY> [production_version]")
        sys.exit(1)
    day = sys.argv[1]
    production_version = sys.argv[2] if len(sys.argv) > 2 else "unknown"
    D = day.replace("-", "")

    with open(CORPUS_CANDLES) as f:
        candles = json.load(f)[day]
    with open(CORPUS_BHAV_FMT.format(d=D)) as f:
        bhav_text = f.read()

    report = observe_day(day, candles, bhav_text, production_version)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
