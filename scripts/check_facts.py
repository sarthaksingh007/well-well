from wellwell.analytics.compliance import Compliance
from wellwell.analytics.facts import Facts
from wellwell.db.session import session_scope

with session_scope() as s:
    f = Facts(s)
    print("Q2", f.wells_not_producing(area="Northeast Block 8").summary)
    for r in f.wells_not_producing(area="Northeast Block 8").rows:
        print("   ", r["well"], r["wellhead_above_air_c"], r["recorded_status"], "|", r["reason"][:90], "|", r["inspector_note"])
    print("Q3", f.temperature_history("WELL-0001", weeks=13).summary)
    q7 = f.spills(area="Northeast Block 8", period="last_90_days")
    print("Q7", q7.summary, [(r["well"], str(r["date"]), r["volume_bbl"], r["lat"], r["lon"]) for r in q7.rows])
    print("Q11", f.repairs(wells=["WELL-0121", "WELL-0138", "WELL-0107"], period="this_year").card["cost_by_well"])
    q14 = f.rca_due(area="Northeast Block 7", equipment_kind="pump_jack")
    print("Q14", [(r["well"], r["type"], r["corrective_repairs_12m"]) for r in q14.rows])
    c = Compliance(f)
    q9 = c.check(well="WELL-0001", period="last_12_months")
    print("Q9", q9.card)
    for r in q9.rows:
        print("   ", r["status"], r["protocol"], r["detail"][:150], r["citations"])
    q10 = c.check(area="Southwest Block 24", period="last_90_days", protocols=["SR-02"], only_breaches=True)
    print("Q10", [(r["well"], r["detail"][:90], r["citations"]) for r in q10.rows])
    print("Q18", f.inspection_detail("INS-0101-0197").summary)
