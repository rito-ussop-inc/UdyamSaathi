import json
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "dairy_model.json"

with open(DATA_FILE, "r", encoding="utf-8") as f:
    MODEL = json.load(f)

def analyze_dairy_plan(plan):
    cows = max(1, int(plan["cows"]))
    own_capital = max(0, float(plan["capital"]))

    m = MODEL["dairy"]
    shed_free_cap = int(m.get("shed_free_capacity", 3))
    fodder_free_cap = int(m.get("fodder_free_cows", 4))
    family_cap = int(m.get("family_labour_capacity", 5))
    no_buyer_disc = float(m.get("no_distributor_discount", 0.10))

    animal_cost = cows * m["cow_cost"]
    if plan["existing_shed"]:
        # Existing shed covers only a small home herd; expansion still costs.
        shed_cost = max(0, cows - shed_free_cap) * m["shed_cost_per_cow"]
    else:
        shed_cost = cows * m["shed_cost_per_cow"]
    equipment = m["equipment_base"] if cows <= 3 else m["equipment_base"] + (cows - 3) * m["equipment_extra_per_cow"]

    # Initial project estimate
    project_cost = animal_cost + shed_cost + equipment + m["transport"] + m["insurance_per_cow"] * cows
    loan_needed = max(0, project_cost - own_capital)

    # Monthly economics (capacity-aware so bigger is not always better)
    concentrate = cows * m["concentrate_per_cow_month"]
    if plan["fodder_land"]:
        # Own land feeds only a few cows; beyond that you still buy green fodder.
        green_fodder = max(0, cows - fodder_free_cap) * m["green_fodder_per_cow_month"]
    else:
        green_fodder = cows * m["green_fodder_per_cow_month"]
    dry_fodder = cows * m["dry_fodder_per_cow_month"]
    if plan["family_labour"]:
        if cows <= family_cap:
            labour = 0
        else:
            # Family covers 5; each extra block of 5 needs a hired hand.
            extra = cows - family_cap
            hands = (extra + 4) // 5
            labour = hands * m["hired_labour_month"]
    else:
        # Hired labour scales with herd: ~1 hand per 5 cows.
        hands = (cows + 4) // 5
        labour = hands * m["hired_labour_month"]
    vet = cows * m["vet_per_cow_month"]
    utilities = m["utilities_month"]
    misc = m["misc_month"]

    monthly_cost = concentrate + green_fodder + dry_fodder + labour + vet + utilities + misc

    milk_litres = cows * m["milk_yield_litre_per_cow_day"] * 30
    revenue = milk_litres * m["milk_price_per_litre"]
    if not plan.get("distributor", True):
        # No assured buyer -> lower realization / wastage.
        revenue = revenue * (1 - no_buyer_disc)
    operating_surplus = revenue - monthly_cost

    # ---------- Smart Scheme Router (PS Module 2) ----------
    # Margin 10% / loan 90%. Project cost decides the tier:
    #   <= Rs 1.40L -> Micro Finance (6.5%, 3y, 3-mo moratorium)
    #   else        -> Term Loan (8%, 7y, 6-mo moratorium)
    margin_frac = float(m.get("margin_fraction", 0.10))
    schemes = m.get("schemes", {})
    micro = schemes.get("micro", {"name": "Micro Finance Scheme", "max_project_cost": 140000,
                                  "max_loan": 125000, "annual_interest": 0.065,
                                  "tenure_years": 3, "moratorium_months": 3})
    term = schemes.get("term", {"name": "Term Loan Scheme", "max_project_cost": 5000000,
                                "max_loan": 4500000, "annual_interest": 0.08,
                                "tenure_years": 7, "moratorium_months": 6})
    sch = micro if project_cost <= micro["max_project_cost"] else term
    sch_key = "micro" if sch is micro else "term"

    margin_required = round(project_cost * margin_frac, 2)
    margin_shortfall = round(max(0.0, margin_required - own_capital), 2)
    max_loan_90pct = round(min(project_cost * (1 - margin_frac), sch["max_loan"]), 2)
    max_supportable_project = round(min(own_capital / margin_frac if own_capital > 0 else 0.0,
                                        term["max_project_cost"]), 2)
    eligible = bool(own_capital + 1 >= margin_required
                    and loan_needed <= sch["max_loan"] + 1
                    and project_cost <= term["max_project_cost"] + 1)

    # Moratorium-aware EMI: tenure INCLUDES the moratorium. Interest accrues
    # during the holiday (conservative assumption), then the grown balance is
    # amortized over the remaining months.
    rate = sch["annual_interest"] / 12
    months = sch["tenure_years"] * 12
    mor = int(sch["moratorium_months"])
    pay_months = max(1, months - mor)
    if loan_needed <= 0:
        emi = 0.0
        bal_after_mor = 0.0
    else:
        bal_after_mor = loan_needed * (1 + rate) ** mor
        emi = bal_after_mor * rate * (1 + rate) ** pay_months / ((1 + rate) ** pay_months - 1)

    after_emi = operating_surplus - emi

    # Quarterly repayment schedule (quarters; moratorium quarters deferred).
    schedule = []
    if loan_needed > 0:
        bal = float(loan_needed)
        q_pay = q_prin = q_int = 0.0
        for mo in range(1, months + 1):
            intr = bal * rate
            if mo <= mor:
                bal += intr
                q_int += intr
            else:
                princ = min(emi - intr, bal)
                bal -= princ
                q_pay += princ + intr
                q_prin += princ
                q_int += intr
            if mo % 3 == 0 or mo == months:
                schedule.append({
                    "q": (mo + 2) // 3,
                    "months": f"{mo - (mo - 1) % 3}-{mo}" if mo % 3 == 0 else f"{mo}-end",
                    "payment": round(q_pay, 2),
                    "principal": round(q_prin, 2),
                    "interest": round(q_int, 2),
                    "balance": round(max(0.0, bal), 2),
                    "moratorium": mo <= mor,
                })
                q_pay = q_prin = q_int = 0.0

    if after_emi >= 5000:
        risk = "LOW"
    elif after_emi >= 0:
        risk = "MEDIUM"
    else:
        risk = "HIGH"

    recommendation = (
        "Plan looks reasonably serviceable under these assumptions."
        if risk == "LOW"
        else "Plan needs adjustment before taking the full loan."
        if risk == "MEDIUM"
        else "Loan pressure is high. Consider changing the business configuration before borrowing."
    )

    return {
        "business": "dairy",
        "cows": cows,
        "project_cost": round(project_cost, 2),
        "own_capital": round(own_capital, 2),
        "loan_needed": round(loan_needed, 2),
        "monthly_cost": round(monthly_cost, 2),
        "estimated_monthly_revenue": round(revenue, 2),
        "operating_surplus": round(operating_surplus, 2),
        "estimated_emi": round(emi, 2),
        "after_emi": round(after_emi, 2),
        "risk": risk,
        "recommendation": recommendation,
        "scheme": {
            "key": sch_key,
            "name": sch["name"],
            "annual_interest": sch["annual_interest"],
            "tenure_years": sch["tenure_years"],
            "moratorium_months": mor,
            "pay_months": pay_months,
            "max_loan": sch["max_loan"],
        },
        "margin_required": margin_required,
        "margin_shortfall": margin_shortfall,
        "max_loan_90pct": max_loan_90pct,
        "max_supportable_project": max_supportable_project,
        "eligible": eligible,
        "quarterly_schedule": schedule,
        "assumptions_note": "Prototype model only. Validate all official/local cost and scheme assumptions before production use."
    }
