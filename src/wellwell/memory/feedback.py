import re
from datetime import date, datetime

from sqlalchemy import func, select

from wellwell.analytics.compliance import Compliance
from wellwell.analytics.facts import NOT_RUNNING_HINTS, Facts, _date, spill_class
from wellwell.analytics.periods import today
from wellwell.analytics.result import ToolResult, cite
from wellwell.db.models import Claim, Conversation, Correction, Inspection, Message, Note, RuleOverride, Well
from wellwell.domain.enums import (
    ClaimStatus,
    CorrectionKind,
    CorrectionStatus,
    NoteOrigin,
    NoteType,
    Scope,
)

CORRECTABLE_FIELDS = {"wellhead_temp_c", "ambient_temp_c", "spill_detected", "spill_est_volume_bbl"}
RULES = {"rca_min_corrective", "producing_min_delta_c"}
RUNNING_STATES = {"producing", "running", "online", "active"}
STOPPED_STATES = {"not_producing", "shut_in", "down", "stopped", "offline"}


def _next_id(session, model, column, prefix: str) -> str:
    """Readable IDs like COR-0007: one more than the highest number already used for this prefix."""
    existing = session.scalars(select(column).where(column.like(f"{prefix}-%"))).all()
    numbers = [int(value.rsplit("-", 1)[1]) for value in existing if value.rsplit("-", 1)[1].isdigit()]
    return f"{prefix}-{max(numbers, default=0) + 1:04d}"


def _add_with_id(session, build, model, column, prefix: str, attempts: int = 5):
    """Adds a row under a fresh ID, retrying if someone else took that ID at the same moment."""
    from sqlalchemy.exc import IntegrityError

    for _ in range(attempts):
        row = build(_next_id(session, model, column, prefix))
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
            return row
        except IntegrityError:
            continue
    raise RuntimeError(f"Could not allocate a new {prefix} id")


class Feedback:
    """Everything people tell the copilot: corrections, shared notes, team rules and claims.

    Nothing overwrites the original data. Corrections and notes sit beside the records, carry who said
    it and why, and are applied by the views and tools for every user at the scope given.
    """

    def __init__(self, facts: Facts, user_id: str):
        self.facts = facts
        self.session = facts.session
        self.user_id = user_id
        self.user_name = facts.directory.user_name(user_id) or user_id

    def record_correction(self, field: str, corrected_value: str | float | bool, reason: str,
                          inspection_id: str | None = None, well: str | None = None, on_date: str | None = None,
                          kind: str = "raw_data") -> ToolResult:
        if field not in CORRECTABLE_FIELDS:
            raise ValueError(f"field must be one of {sorted(CORRECTABLE_FIELDS)}")
        if inspection_id is None:
            detail = self.facts.inspection_detail(well=well, on_date=on_date)
            inspection_id = detail.card["inspection_id"]
        inspection = self.session.get(Inspection, inspection_id)
        if inspection is None:
            raise ValueError(f"Unknown inspection {inspection_id}")
        before = self.facts.inspection_detail(inspection_id).card
        original = getattr(inspection, field)
        value = str(corrected_value).lower() if field == "spill_detected" else str(float(corrected_value))

        previous = self.session.scalars(select(Correction).where(
            Correction.target_id == inspection_id, Correction.field == field,
            Correction.status == CorrectionStatus.ACTIVE)).all()
        disagreements = []
        for old in previous:
            if old.author_user_id != self.user_id and old.corrected_value != value:
                disagreements.append(
                    f"{self.facts.directory.user_name(old.author_user_id)} corrected this to {old.corrected_value} "
                    f"({old.correction_id}); your value {value} replaces it, both stay on record.")
            old.status = CorrectionStatus.SUPERSEDED

        correction = _add_with_id(self.session, lambda new_id: Correction(
            correction_id=new_id,
            kind=CorrectionKind(kind), scope=Scope.WELL, asset_id=inspection.asset_id, target_table="inspections",
            target_id=inspection_id, field=field, original_value=str(original), corrected_value=value,
            reason=reason, author_user_id=self.user_id, created_at=datetime.now(),
        ), Correction, Correction.correction_id, "COR")
        after = self.facts.inspection_detail(inspection_id).card

        effects = []
        if field in ("wellhead_temp_c", "ambient_temp_c"):
            effects.append(f"Now {after['above_air_c']} °C above air: "
                           f"{'producing' if after['producing'] else 'not producing'}"
                           f"{' (was not producing)' if after['producing'] and not before['producing'] else ''}"
                           f"{' (was producing)' if before['producing'] and not after['producing'] else ''}.")
            if after["producing"] and inspection.notes and NOT_RUNNING_HINTS.search(inspection.notes):
                disagreements.append(
                    f"The same inspection has the inspector's note \"{inspection.notes}\" "
                    f"({self.facts.directory.user_name(inspection.inspector_user_id)}), which disagrees with the "
                    "corrected reading. Please confirm.")
        compliance_rows = []
        if field in ("spill_est_volume_bbl", "spill_detected") and after["spill_counts"]:
            old_class, new_class = spill_class(before["effective_volume_bbl"]), spill_class(after["effective_volume_bbl"])
            effects.append(f"Spill class {old_class.value if old_class else 'n/a'} -> {new_class.value if new_class else 'n/a'}.")
            spill = self.facts._one("SELECT * FROM inspection_facts WHERE inspection_id = :i", i=inspection_id)
            compliance_rows = Compliance(self.facts)._spill_checks(spill, datetime.combine(today(), datetime.min.time()))
            for row in compliance_rows:
                effects.append(f"[{row['status']}] {row['protocol']}: {row['detail']}")

        unit = " °C" if field.endswith("_temp_c") else " bbl" if field.endswith("_bbl") else ""
        summary = (f"Recorded {correction.correction_id} against {inspection_id} ({inspection.asset_id}, "
                   f"{inspection.inspection_date:%d %b %Y}): {field} {original}{unit} -> {value}{unit}. The original "
                   f"value {original}{unit} is kept for reference; corrected by {self.user_name}, reason: {reason}. "
                   "It now applies to every later answer for every user. When you reply, state the original value, "
                   "the corrected value, who corrected it and why. " + " ".join(effects))
        if disagreements:
            summary += " Conflicts: " + " ".join(disagreements)
        return ToolResult(summary=summary, card={"correction_id": correction.correction_id, "before": before,
                                                 "after": after, "effects": effects, "disagreements": disagreements},
                          rows=compliance_rows, visual={"kind": "status_card"},
                          citations=cite(correction.correction_id, inspection_id,
                                         *[c for r in compliance_rows for c in r["citations"]]),
                          warnings=disagreements)

    def record_shared_note(self, well: str, text: str, valid_from: str | None = None, valid_to: str | None = None,
                           scope: str = "well", well_type: str | None = None) -> ToolResult:
        asset_id = self.facts.directory.resolve_well(well) if scope == "well" else None
        if scope == "well" and asset_id is None:
            raise ValueError(f"Unknown well '{well}'")
        note = _add_with_id(self.session, lambda new_id: Note(
            note_id=new_id, origin=NoteOrigin.COPILOT, scope=Scope(scope),
            asset_id=asset_id, well_type=well_type or (self.session.get(Well, asset_id).well_type if asset_id else None),
            created_at=datetime.now(), author_user_id=self.user_id, note_type=NoteType.OPERATOR_FEEDBACK, text=text,
            valid_from=date.fromisoformat(valid_from) if valid_from else today(),
            valid_to=date.fromisoformat(valid_to) if valid_to else None, dataset="copilot",
        ), Note, Note.note_id, "FB")
        target = asset_id or f"all {well_type} wells" if scope == "well_type" else asset_id
        summary = (f"Saved shared note {note.note_id} for {target} from {self.user_name}: \"{text}\" "
                   f"(valid {note.valid_from} to {note.valid_to or 'open'}). Every user asking about it will see this.")
        return ToolResult(summary=summary, card={"note_id": note.note_id, "author": self.user_name, "text": text},
                          citations=[note.note_id])

    def record_rule(self, rule: str, value: float, statement: str, well_type: str | None = None,
                    equipment_kind: str | None = None) -> ToolResult:
        if rule not in RULES:
            raise ValueError(f"rule must be one of {sorted(RULES)}")
        scope = Scope.WELL_TYPE if well_type else Scope.FLEET
        manual = self.facts._rule(well_type or "T200").get(rule)
        for old in self.session.scalars(select(RuleOverride).where(
                RuleOverride.rule == rule, RuleOverride.well_type == well_type,
                RuleOverride.equipment_kind == equipment_kind, RuleOverride.status == CorrectionStatus.ACTIVE)):
            old.status = CorrectionStatus.SUPERSEDED
        override = _add_with_id(self.session, lambda new_id: RuleOverride(
            override_id=new_id, scope=scope,
            well_type=well_type.upper() if well_type else None, rule=rule, equipment_kind=equipment_kind,
            value=float(value), manual_value=manual, statement=statement, author_user_id=self.user_id,
            created_at=datetime.now(),
        ), RuleOverride, RuleOverride.override_id, "RULE")
        target = f"all {override.well_type} wells only" if well_type else "every well"
        summary = (f"Saved team rule {override.override_id} from {self.user_name}: {rule}"
                   f"{' for ' + equipment_kind if equipment_kind else ''} = {value} for {target} "
                   f"(manual says {manual}). It applies to later answers for every user, at this scope only.")
        return ToolResult(summary=summary, card={"override_id": override.override_id}, citations=[override.override_id])

    def record_claim(self, well: str, claimed_state: str, statement: str, since: str | None = None) -> ToolResult:
        """Checks what a user says about a well against the latest inspection and other people's statements."""
        asset_id = self.facts.directory.resolve_well(well)
        if asset_id is None:
            raise ValueError(f"Unknown well '{well}'")
        state = claimed_state.lower().replace(" ", "_")
        claims_running = state in RUNNING_STATES
        if not claims_running and state not in STOPPED_STATES:
            raise ValueError("claimed_state must be producing/running/online or not_producing/shut_in/down")
        since_day = date.fromisoformat(since) if since else None
        status_card = self.facts.well_status(asset_id).card
        latest = status_card["latest_inspection"]
        latest_day = _date(latest["inspected_at"])
        producing = status_card["producing_now"]

        if since_day and latest_day < since_day:
            status, verdict = ClaimStatus.UNCONFIRMED, "No inspection since then can confirm or contradict it."
        elif producing == claims_running:
            status = ClaimStatus.CONFIRMED_BY_DATA
            verdict = (f"Matches the {latest_day:%d %b} inspection {latest['inspection_id']} by {latest['inspector']} "
                       f"({latest['temp_above_air_c']} °C above air).")
        else:
            status = ClaimStatus.CONTRADICTED_BY_DATA
            verdict = (f"Contradicted by the {latest_day:%d %b} inspection {latest['inspection_id']} by "
                       f"{latest['inspector']}: wellhead {latest['temp_above_air_c']} °C above air, so the well is "
                       f"{'producing' if producing else 'not producing'}. Recorded as unconfirmed; please confirm.")

        others = []
        for claim in self.facts._claims(asset_id):
            other_running = claim["claimed_state"] in RUNNING_STATES
            if other_running != claims_running:
                others.append(f"{claim['author']} said \"{claim['statement']}\" ({claim['claim_id']}, {claim['status']}).")
        for note in status_card["reasons_not_producing"]:
            if claims_running and note.startswith("Note "):
                others.append(note)

        note = _add_with_id(self.session, lambda new_id: Note(
            note_id=new_id, origin=NoteOrigin.COPILOT, scope=Scope.WELL,
            asset_id=asset_id, well_type=status_card["well_type"], created_at=datetime.now(),
            author_user_id=self.user_id, note_type=NoteType.OPERATOR_FEEDBACK,
            text=f"{statement} [{'confirmed by data' if status == ClaimStatus.CONFIRMED_BY_DATA else 'unconfirmed'}]",
            valid_from=since_day or today(), valid_to=None, dataset="copilot",
        ), Note, Note.note_id, "FB")
        claim = _add_with_id(self.session, lambda new_id: Claim(
            claim_id=new_id, asset_id=asset_id,
            author_user_id=self.user_id, statement=statement, claimed_state=state, effective_from=since_day,
            status=status, evidence={"inspection_id": latest["inspection_id"], "producing": producing},
            note_id=note.note_id, created_at=datetime.now(),
        ), Claim, Claim.claim_id, "CLM")
        summary = f"{self.user_name}'s statement about {asset_id} recorded as {claim.claim_id} ({status.value}). {verdict}"
        if others:
            summary += " Other statements: " + " ".join(others)
        return ToolResult(summary=summary, card={"claim_id": claim.claim_id, "status": status.value,
                                                 "verdict": verdict, "other_statements": others},
                          citations=cite(claim.claim_id, latest["inspection_id"], note.note_id,
                                         *re.findall(r"\b(?:CLM-\d+|CTX-\d{4}-\d{3}|FB-\d+)\b", " ".join(others))),
                          warnings=others)


class PrivateMemory:
    """A user's own conversation history. Never readable by anyone else."""

    def __init__(self, session, user_id: str, current_conversation_id: int | None):
        self.session = session
        self.user_id = user_id
        self.current_conversation_id = current_conversation_id

    def recall(self, days_ago: int | None = None, limit: int = 3) -> ToolResult:
        query = select(Conversation).where(Conversation.user_id == self.user_id)
        if self.current_conversation_id:
            query = query.where(Conversation.conversation_id != self.current_conversation_id)
        conversations = self.session.scalars(query.order_by(Conversation.conversation_id.desc())).all()
        exact_day = True
        if days_ago is not None:
            on_day = [c for c in conversations if (today() - c.started_on).days == days_ago]
            exact_day = bool(on_day)
            conversations = on_day or conversations
        conversations = conversations[:limit]
        if not conversations:
            return ToolResult(summary="This user has no earlier conversations. Say plainly that you have no such earlier "
                                      "conversation with them; do not guess and never use another user's history.")
        rows = []
        for conversation in conversations:
            messages = self.session.scalars(select(Message).where(
                Message.conversation_id == conversation.conversation_id).order_by(Message.message_id)).all()
            wells: list[str] = []
            for message in messages:
                for asset_id in message.asset_ids or []:
                    if asset_id not in wells:
                        wells.append(asset_id)
            rows.append({
                "conversation_id": conversation.conversation_id, "started_on": conversation.started_on,
                "title": conversation.title,
                "questions": [m.content for m in messages if m.role == "user"],
                "answer_wells_in_order": wells,
                "last_answer": next((m.content for m in reversed(messages) if m.role == "assistant"), None),
            })
        listing = " | ".join(
            f"#{r['conversation_id']} on {r['started_on']}: asked {r['questions']}; "
            f"{len(r['answer_wells_in_order'])} well(s) in the answers {r['answer_wells_in_order']}" for r in rows)
        if exact_day:
            summary = "Earlier conversations of this user (most recent first): " + listing
        else:
            summary = (f"This user has NO conversation from {days_ago} day(s) ago. Their other earlier conversations, "
                       "only as candidates: " + listing + ". Use one ONLY if it clearly matches what the user describes "
                       "(same topic and the same number of wells). If none matches, say plainly that you have no such "
                       "earlier conversation with them and ask which wells they mean. Never use another user's history.")
        return ToolResult(summary=summary, rows=rows)
