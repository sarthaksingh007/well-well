import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.conftest import ScriptedLLM
from wellwell.analytics.compliance import Compliance
from wellwell.analytics.facts import Facts
from wellwell.cli import DEFAULT_SAMPLE
from wellwell.db.base import Base
from wellwell.db.models import Correction, Inspection, Maintenance, Note, Well, WellTypeRule
from wellwell.db.session import get_read_only_engine
from wellwell.harness.agent import Agent, AgentConfig
from wellwell.harness.llm import LLMError
from wellwell.harness.tools import ToolBox
from wellwell.ingestion.loader import DatasetLoader
from wellwell.memory.feedback import Feedback, PrivateMemory


def final(answer, step=1, kind=None):
    return json.dumps({"final": {"answer": answer, "visual": {"step": step, "kind": kind}}})


def call(tool, **args):
    return json.dumps({"calls": [{"tool": tool, "args": args}]})


# ---------------------------------------------------------------- tools with awkward input

def test_unknown_well_and_area_are_clear_errors(session):
    facts = Facts(session)
    with pytest.raises(ValueError, match="Unknown well"):
        facts.well_status("WELL-9999")
    with pytest.raises(ValueError, match="Unknown area"):
        facts.wells_not_producing(area="Atlantis Block 1")


def test_well_ids_are_forgiving_about_format(session):
    facts = Facts(session)
    assert facts.directory.resolve_well("well 1") == "WELL-0001"
    assert facts.directory.resolve_well("WELL-1") == "WELL-0001"
    for text_value in ("NE-8", "ne 08", "Northeast Block 8", "northeast block 8", "NE-08", "Block 8"):
        assert facts.directory.resolve_area(text_value) == "NE-08"
    assert facts.directory.resolve_area("Block 99") is None


def test_period_with_no_data_returns_empty_not_error(session):
    facts = Facts(session)
    assert facts.downtime(wells=["WELL-0001"], period="2019").rows[0]["downtime_days"] == 0
    assert facts.spills(well="WELL-0001", period="2019").rows == []


def test_top_n_larger_than_scope_is_fine(session):
    result = Facts(session).downtime(area="Northeast Block 7", period="last_4_months", top_n=500)
    assert 0 < len(result.rows) <= 50


def test_aggregate_rejects_unknown_metric_and_dimension(session):
    facts = Facts(session)
    with pytest.raises(ValueError):
        facts.aggregate(metric="happiness", rows_by="area")
    with pytest.raises(ValueError):
        facts.aggregate(metric="spill_count", rows_by="planet")


def test_short_history_well_does_not_break_temperature_chart(session):
    newest = session.scalar(select(Well.asset_id).order_by(Well.installed_on.desc()))
    result = Facts(session).temperature_history(newest, weeks=500)
    assert len(result.rows) >= 1


def test_wells_argument_as_comma_string_is_accepted(session):
    toolbox = ToolBox(session, "U-201", None)
    result = toolbox.run("repairs", {"wells": "WELL-0121, WELL-0138", "period": "this_year"})
    assert set(result.card["cost_by_well"]) == {"WELL-0121", "WELL-0138"}


def test_tool_registry_rejects_unknown_tool_and_missing_args(session):
    toolbox = ToolBox(session, "U-201", None)
    with pytest.raises(ValueError, match="Unknown tool"):
        toolbox.run("delete_everything", {})
    with pytest.raises(ValueError, match="needs well"):
        toolbox.run("well_status", {})


def test_injection_text_in_arguments_is_just_a_bad_value(session):
    with pytest.raises(ValueError):
        Facts(session).wells_not_producing(area="x'; DROP TABLE wells; --")
    assert session.scalar(select(func.count()).select_from(Well)) >= 200


# ---------------------------------------------------------------- SQL and database safety

@pytest.mark.parametrize("sql", ["DELETE FROM wells", "INSERT INTO notes(note_id) VALUES ('x')",
                                 "SELECT * FROM messages", "SELECT 1; SELECT 2"])
def test_query_tool_refuses_unsafe_sql(session, sql):
    with pytest.raises(ValueError):
        ToolBox(session, "U-201", None).run("query_sql", {"sql": sql})


def test_read_only_connection_cannot_write_even_without_the_guard():
    with get_read_only_engine().connect() as connection, pytest.raises(DBAPIError):
        connection.execute(text("UPDATE wells SET name = 'x' WHERE asset_id = 'WELL-0001'"))


def test_query_tool_answers_and_limits_rows(session):
    result = ToolBox(session, "U-201", None).run(
        "query_sql", {"sql": "SELECT inspection_id FROM inspection_facts WHERE producing = 0"})
    assert 0 < len(result.rows) <= 200


# ---------------------------------------------------------------- memory and feedback

def test_correction_keeps_original_flips_status_and_flags_note(session):
    feedback = Feedback(Facts(session), "U-201")
    result = feedback.record_correction("wellhead_temp_c", 41.0, "Thermal gun faulty, re-measured",
                                        well="WELL-0164", on_date="2026-10-15")
    correction = session.get(Correction, result.card["correction_id"])
    assert correction.original_value == "24.0" and correction.corrected_value == "41.0"
    assert result.card["after"]["producing"] and result.card["after"]["above_air_c"] == 21.1
    assert any("Pump jack not moving" in d for d in result.card["disagreements"])
    assert "WELL-0164" not in [r["well"] for r in Facts(session).wells_not_producing(area="Northeast Block 8").rows]


def test_second_correction_supersedes_but_both_stay(session):
    facts = Facts(session)
    Feedback(facts, "U-201").record_correction("spill_est_volume_bbl", 5.4, "crew measured", well="WELL-0151",
                                               on_date="2026-09-24", kind="ai_analysis")
    second = Feedback(facts, "U-204").record_correction("spill_est_volume_bbl", 5.0, "second look",
                                                        well="WELL-0151", on_date="2026-09-24", kind="ai_analysis")
    rows = session.scalars(select(Correction).where(Correction.target_id == "INS-0151-0201",
                                                    Correction.field == "spill_est_volume_bbl")).all()
    statuses = [r.status.value for r in rows]
    assert statuses.count("active") == 1 and statuses.count("superseded") >= 1
    assert next(r for r in rows if r.status.value == "active").author_user_id == "U-204"
    assert any("Michael Turner" in d for d in second.card["disagreements"])


def test_volume_correction_makes_spill_reportable(session):
    facts = Facts(session)
    Feedback(facts, "U-201").record_correction("spill_est_volume_bbl", 5.4, "crew measured", well="WELL-0151",
                                               on_date="2026-09-24", kind="ai_analysis")
    spills = facts.spills(area="Northeast Block 8", period="last_90_days", reportable_only=True)
    assert [r["well"] for r in spills.rows] == ["WELL-0151"]
    checks = Compliance(facts).check(well="WELL-0151", period="last_90_days").rows
    assert any(c["protocol"] == "SR-01" and c["status"] == "breach" for c in checks)
    assert any(c["protocol"] == "SR-03" and "23 Nov 2026" in c["detail"] for c in checks)


def test_team_rule_applies_to_its_well_type_only(session):
    session.execute(text("UPDATE rule_overrides SET status = 'superseded'"))
    facts = Facts(session)
    before = {r["well"] for r in facts.rca_due(area="Northeast Block 7", equipment_kind="pump_jack").rows}
    Feedback(facts, "U-201").record_rule("rca_min_corrective", 2, "T350 pump jack RCA after 2", well_type="T350",
                                         equipment_kind="pump_jack")
    after = {r["well"]: r for r in facts.rca_due(area="Northeast Block 7", equipment_kind="pump_jack").rows}
    assert before == {"WELL-0108", "WELL-0121", "WELL-0138"}
    assert set(after) == {"WELL-0108", "WELL-0120", "WELL-0121", "WELL-0138"}
    assert "WELL-0102" not in after
    assert after["WELL-0121"]["threshold"] == 3


def test_claims_are_checked_against_the_data(session):
    facts = Facts(session)
    agrees = Feedback(facts, "U-201").record_claim("WELL-0175", "shut_in", "Shut in since 14 Oct", since="2026-10-14")
    disagrees = Feedback(facts, "U-204").record_claim("WELL-0175", "producing", "WELL-0175 is running fine")
    assert agrees.card["status"] == "confirmed_by_data"
    assert disagrees.card["status"] == "contradicted_by_data"
    assert any("Michael Turner" in s for s in disagrees.card["other_statements"])


def test_shared_note_reaches_other_users(session):
    facts = Facts(session)
    Feedback(facts, "U-102").record_shared_note("WELL-0007", "Shut in from 17 Oct for a 10-day planned workover",
                                                valid_from="2026-10-17", valid_to="2026-10-27")
    card = Facts(session).well_status("WELL-0007").card
    assert any("workover" in n["text"] and n["author"] == "Jake Morales" for n in card["current_and_planned_notes"])


def test_feedback_validates_inputs(session):
    feedback = Feedback(Facts(session), "U-201")
    with pytest.raises(ValueError):
        feedback.record_correction("recorded_status", "down", "x", well="WELL-0001", on_date="2026-10-15")
    with pytest.raises(ValueError):
        feedback.record_correction("wellhead_temp_c", 40, "x", well="WELL-0001", on_date="1999-01-01")
    with pytest.raises(ValueError):
        feedback.record_rule("delete_rules", 1, "x", well_type="T350")
    with pytest.raises(ValueError):
        feedback.record_claim("WELL-0001", "dancing", "it dances")


def test_private_memory_never_crosses_users(session):
    from wellwell.db.models import Conversation, Message

    mine = Conversation(user_id="U-201", started_on=__import__("datetime").date(2026, 10, 16))
    session.add(mine)
    session.flush()
    session.add(Message(conversation_id=mine.conversation_id, user_id="U-201", role="assistant", content="x",
                        asset_ids=["WELL-0121"], sent_on=mine.started_on))
    session.flush()
    theirs = PrivateMemory(session, "U-305", None).recall(days_ago=1)
    assert "no earlier conversations" in theirs.summary.lower()
    assert "WELL-0121" in PrivateMemory(session, "U-201", None).recall(days_ago=1).summary


# ---------------------------------------------------------------- agent harness behaviour

def _agent(session, replies):
    llm = ScriptedLLM(replies)
    return Agent(llm, ToolBox(session, "U-201", None), "system", AgentConfig(max_rounds=4)), llm


def test_agent_recovers_from_bad_json_and_unknown_tool(session):
    agent, _ = _agent(session, ["not json at all", call("teleport", well="WELL-0001"),
                                call("well_status", well="WELL-0001"),
                                final("WELL-0001 is not producing [INS-0001-0203].")])
    result = agent.run("Summarise WELL-0001 for me", "")
    assert result.steps[0].error and result.steps[1].result
    assert result.citations == ["INS-0001-0203"]


def test_agent_strips_citations_no_tool_returned(session):
    agent, _ = _agent(session, [call("well_status", well="WELL-0001"),
                                final("Producing per [INS-0001-0203] and [MNT-9999-001].")])
    result = agent.run("Is WELL-0001 producing?", "")
    assert "MNT-9999-001" not in result.answer
    assert result.trust["unknown_citations_removed"] == ["MNT-9999-001"]


def test_agent_insists_on_a_tool_for_factual_questions(session):
    agent, llm = _agent(session, [final("It is fine."), call("well_status", well="WELL-0001"), final("Checked.")])
    result = agent.run("Summarise WELL-0001 for me", "")
    assert result.steps and "must call at least one tool" in llm.prompts[1]


def test_named_visual_overrides_model_choice(session):
    agent, _ = _agent(session, [call("wells_not_producing", area="Northeast Block 8"),
                                final("Four wells.", step=1, kind="bar")])
    result = agent.run("Which wells are down in Northeast Block 8? Show them in a table.", "")
    assert result.visual["kind"] == "table"


def test_agent_degrades_gracefully_when_out_of_rounds(session):
    agent, _ = _agent(session, [call("well_status", well="WELL-0001")] * 4)
    result = agent.run("Is WELL-0001 producing?", "")
    assert result.trust["degraded"] and "WELL-0001" in result.answer


def test_model_outage_surfaces_as_llm_error(session):
    agent, _ = _agent(session, [LLMError("Gemma is unavailable")])
    with pytest.raises(LLMError):
        agent.run("Summarise WELL-0001 for me", "")


# ---------------------------------------------------------------- loading new data

def _sqlite_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'load.db'}")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_loader_is_idempotent(tmp_path):
    small = tmp_path / "small"
    shutil.copytree(DEFAULT_SAMPLE / "fleet", small / "fleet")
    for well in ("WELL-0001", "WELL-0101"):
        shutil.copytree(DEFAULT_SAMPLE / "assets" / well, small / "assets" / well)
    with _sqlite_session(tmp_path) as db:
        first = DatasetLoader(db, small, "sample").run().counts
        db.commit()
        second = DatasetLoader(db, small, "sample").run().counts
        db.commit()
        assert first["inspections"] == second["inspections"]
        assert db.scalar(select(func.count()).select_from(Inspection)) == first["inspections"]
        assert db.scalar(select(func.count()).select_from(Correction)) == 1
        rules = {r.well_type: r.producing_min_delta_c for r in db.scalars(select(WellTypeRule))}
        assert rules == {"T200": 15.0, "T350": 20.0}


def test_test_dataset_with_new_wells_merges_alongside(tmp_path):
    base = tmp_path / "sample"
    shutil.copytree(DEFAULT_SAMPLE / "fleet", base / "fleet")
    shutil.copytree(DEFAULT_SAMPLE / "assets" / "WELL-0001", base / "assets" / "WELL-0001")
    test = tmp_path / "test"
    shutil.copytree(DEFAULT_SAMPLE / "fleet", test / "fleet")
    new_dir = test / "assets" / "WELL-0201"
    shutil.copytree(DEFAULT_SAMPLE / "assets" / "WELL-0001", new_dir)
    for path in new_dir.rglob("*"):
        if path.is_file():
            path.write_text(path.read_text().replace("WELL-0001", "WELL-0201").replace("-0001-", "-0201-"))
    for path in list((new_dir / "maintenance-reports").iterdir()):
        path.rename(path.with_name(path.name.replace("-0001-", "-0201-")))
    with _sqlite_session(tmp_path) as db:
        DatasetLoader(db, base, "sample").run()
        report = DatasetLoader(db, test, "test").run()
        db.commit()
        assert {w.asset_id for w in db.scalars(select(Well))} == {"WELL-0001", "WELL-0201"}
        assert db.scalar(select(func.count()).select_from(Maintenance).where(
            Maintenance.asset_id == "WELL-0201", Maintenance.report_containment_hours.is_not(None))) > 0
        assert db.scalar(select(func.count()).select_from(Note).where(Note.asset_id == "WELL-0201")) == 3
        assert not [w for w in report.warnings if "not found" in w]


def test_missing_report_file_is_a_warning_not_a_crash(tmp_path):
    base = tmp_path / "sample"
    shutil.copytree(DEFAULT_SAMPLE / "fleet", base / "fleet")
    shutil.copytree(DEFAULT_SAMPLE / "assets" / "WELL-0001", base / "assets" / "WELL-0001")
    next(Path(base / "assets" / "WELL-0001" / "maintenance-reports").iterdir()).unlink()
    with _sqlite_session(tmp_path) as db:
        report = DatasetLoader(db, base, "sample").run()
        assert any("not found" in w for w in report.warnings)


def test_model_lost_after_tools_still_returns_the_facts(session):
    agent, _ = _agent(session, [call("well_status", well="WELL-0001"), LLMError("Gemma is unavailable")])
    result = agent.run("Is WELL-0001 producing?", "")
    assert result.trust["degraded"] and "INS-0001-0203" in result.answer
