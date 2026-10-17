from enum import StrEnum


class Role(StrEnum):
    PRODUCTION_SUPERVISOR = "production_supervisor"
    FIELD_OPERATOR = "field_operator"
    FIELD_INSPECTOR = "field_inspector"
    MAINTENANCE_LEAD = "maintenance_lead"
    MAINTENANCE_TECHNICIAN = "maintenance_technician"
    HSE_OFFICER = "hse_officer"


class RecordedStatus(StrEnum):
    ACTIVE = "active"
    DOWN = "down"
    PLUGGED = "plugged"


class InspectionMethod(StrEnum):
    DRONE = "drone"
    MANUAL = "manual"


class MaintenanceType(StrEnum):
    SCHEDULED = "scheduled"
    CORRECTIVE = "corrective"


class RegulatoryEventType(StrEnum):
    SPILL_REPORT_FILED = "spill_report_filed"
    NOTICE_OF_VIOLATION = "notice_of_violation"
    SITE_INSPECTION = "site_inspection"
    NOV_RESPONSE_FILED = "nov_response_filed"


class NoteType(StrEnum):
    OPERATOR_FEEDBACK = "operator_feedback"
    OPERATOR_NOTE = "operator_note"
    CORRECTION = "correction"
    DATA_CORRECTION = "data_correction"


class NoteOrigin(StrEnum):
    DATASET = "dataset"
    COPILOT = "copilot"


class Scope(StrEnum):
    WELL = "well"
    WELL_TYPE = "well_type"
    FLEET = "fleet"


class CorrectionKind(StrEnum):
    RAW_DATA = "raw_data"
    AI_ANALYSIS = "ai_analysis"


class CorrectionStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class ClaimStatus(StrEnum):
    CONFIRMED_BY_DATA = "confirmed_by_data"
    CONTRADICTED_BY_DATA = "contradicted_by_data"
    UNCONFIRMED = "unconfirmed"


class SpillClass(StrEnum):
    MINOR = "minor"
    MODERATE = "moderate"
    REPORTABLE = "reportable"
    MAJOR = "major"


class DocKind(StrEnum):
    MANUAL = "manual"
    SOP = "sop"
    MAINTENANCE_REPORT = "maintenance_report"


class VisualKind(StrEnum):
    STATUS_CARD = "status_card"
    TABLE = "table"
    LINE = "line"
    BAR = "bar"
    MAP = "map"
    HEATMAP = "heatmap"
    NONE = "none"
