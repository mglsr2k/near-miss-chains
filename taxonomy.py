"""The shared vocabulary that lets near misses (ASRS) and accidents (NTSB) be compared.

ASRS and NTSB code their reports in different schemes, and there is no ID that links a near miss to
an accident. So build_data.py tags every report in both datasets with the same small set of
FACTORS (conditions and human/organisational contributors), EVENTS (what happened) and PHASES.

Every rule is written out here, so a reader can check exactly what "fatigue" means in each dataset.
A tag records how it was found: "coded" (from the agency's own coded fields) or "text" (a regex over
the narrative). Coded tags are more reliable; text tags catch what coders left out.
"""

# --- Factors: contributors that can chain together ---------------------------------------------
# asrs_coded: (column, substring) pairs over ASRS's coded fields (any match -> tag)
# asrs_text:  regex over the ASRS narrative + synopsis (case-insensitive)
# ntsb_coded: regex over NTSB finding descriptions (Findings table)
# ntsb_text:  regex over NTSB probable cause + factual narrative

FACTORS = {
    "fatigue": {
        "label": "Fatigue / alertness",
        "asrs_coded": [("human_factors", "Fatigue")],
        "asrs_text": r"\b(fatigued?|tired|exhausted|sleepy|sleepiness|nodded off|fell asleep|red[- ]eye|long duty day|been awake)\b",
        "ntsb_coded": r"Alertness/Fatigue|Fatigue-|Sleep",
        "ntsb_text": r"\b(tired|fell asleep|sleep deprivation|lack of sleep|hours of sleep|pilot fatigue|crew fatigue|fatigued)\b",
    },
    "communication": {
        "label": "Communication breakdown (readback, misheard clearance, CRM)",
        "asrs_coded": [("human_factors", "Communication Breakdown")],
        "asrs_text": r"\b(read ?back|hear ?back|misheard|misunderst\w*|miscommunicat\w*|blocked transmission|stepped on)\b",
        "ntsb_coded": r"Communication \(personnel\)|CRM/MRM",
        "ntsb_text": r"\b(read ?back|misunderst\w*|miscommunicat\w*|crew resource management|did not communicate)\b",
    },
    "situational_awareness": {
        "label": "Situational awareness / monitoring lapse",
        "asrs_coded": [("human_factors", "Situational Awareness")],
        "asrs_text": None,
        "ntsb_coded": r"Attention/monitoring",
        "ntsb_text": r"\b(situational awareness|inadequate (visual )?(lookout|monitoring)|failure to (monitor|see and avoid|maintain (visual )?(lookout|clearance|separation)))\b",
    },
    "distraction": {
        "label": "Distraction",
        "asrs_coded": [("human_factors", "Distraction")],
        "asrs_text": r"\bdistract\w*",
        "ntsb_coded": r"Distraction",
        "ntsb_text": r"\bdistract\w*",
    },
    "workload_time_pressure": {
        "label": "High workload / time pressure / rushing",
        "asrs_coded": [("human_factors", "Workload"), ("human_factors", "Time Pressure")],
        "asrs_text": r"\b(rushed|rushing|hurried|time pressure|task saturat\w*|behind the airplane)\b",
        "ntsb_coded": r"Workload management|Task scheduling/workload|Time pressure",
        "ntsb_text": r"\b(rushed|rushing|hurried|time pressure|high workload|task saturat\w*|self-induced pressure)\b",
    },
    "automation": {
        "label": "Automation / human-machine interface (FMS, autopilot modes)",
        "asrs_coded": [("human_factors", "Human-Machine Interface"), ("contributing", "Software and Automation")],
        "asrs_text": r"\b(automation surprise|mode confusion|wrong mode|autopilot (disconnected|did not|failed)|autothrottle)\b",
        "ntsb_coded": r"Auto flight system|Use of automation",
        "ntsb_text": r"\b(autopilot|autothrottle|automation)\b",
    },
    "confusion_disorientation": {
        "label": "Confusion / spatial disorientation",
        "asrs_coded": [("human_factors", "Confusion")],
        "asrs_text": r"\b(disoriented|spatial disorientation|vertigo|lost sight of)\b",
        "ntsb_coded": r"Perception/orientation/illusio",
        "ntsb_text": r"\b(spatial disorientation|disoriented|visual illusion)\b",
    },
    "decision_making": {
        "label": "Decision making / judgment (pressing on, continuing)",
        "asrs_coded": [],
        "asrs_text": r"\b(poor decision|bad decision|should have (gone around|diverted|declined)|press(ed)? on|get-?there-?itis|in hindsight)\b",
        "ntsb_coded": r"Decision making/judgment",
        "ntsb_text": r"\b(decision to (continue|attempt|take off|fly|land)|poor decision|inadequate decision)\b",
    },
    "procedure_checklist": {
        "label": "Procedure / checklist not followed or inadequate",
        "asrs_coded": [("contributing", "Procedure")],
        "asrs_text": r"\bchecklist\b",
        "ntsb_coded": r"Use of checklist|Use of policy/procedure|Adequacy of policy/proc|Knowledge of procedures",
        "ntsb_text": r"\b(checklist|did not follow (the )?procedures?)\b",
    },
    "training_experience": {
        "label": "Training / experience / qualification",
        "asrs_coded": [("human_factors", "Training / Qualification")],
        "asrs_text": r"\b(low time|new (hire|captain|first officer)|first time (flying|in)|unfamiliar|inexperience\w*)\b",
        "ntsb_coded": r"Experience/knowledge|Support/oversight/monitoring-Training",
        "ntsb_text": r"\b(lack of experience|inexperience\w*|limited experience|unfamiliar)\b",
    },
    "impairment_medical": {
        "label": "Impairment / medical / incapacitation",
        "asrs_coded": [("human_factors", "Physiological - Other"), ("anomaly", "Illness / Injury")],
        "asrs_text": r"\b(incapacitat\w*|hypoxi\w*|carbon monoxide|medication)\b",
        "ntsb_coded": r"Impairment/incapacitation|Health/Fitness",
        "ntsb_text": r"\b(incapacitat\w*|impair\w*|hypoxi\w*|carbon monoxide|toxicology)\b",
    },
    "weather": {
        "label": "Weather (wind, turbulence, icing, convection)",
        "asrs_coded": [("contributing", "Weather"), ("anomaly", "Weather / Turbulence")],
        "asrs_text": r"\b(windshear|wind shear|icing|thunderstorm|convective|gusts?|crosswind|tailwind)\b",
        "ntsb_coded": r"Conditions/weather/phenomena",
        "ntsb_text": r"\b(windshear|wind shear|icing|thunderstorm|convective|gusty|crosswind|tailwind)\b",
    },
    "fuel": {
        "label": "Fuel planning / management",
        "asrs_coded": [("anomaly", "Fuel Issue")],
        "asrs_text": r"\b(fuel (exhaustion|starvation|imbalance|leak)|low fuel|minimum fuel|emergency fuel|fuel emergency)\b",
        "ntsb_coded": r"Fluids-Fuel|Fuel planning|Fuel system",
        "ntsb_text": r"\b(fuel exhaustion|fuel starvation|fuel management|fuel planning|ran out of fuel)\b",
    },
    "equipment_failure": {
        "label": "Aircraft system / powerplant malfunction",
        "asrs_coded": [("anomaly", "Aircraft Equipment Problem")],
        "asrs_text": None,
        "ntsb_coded": r"^Aircraft-(Aircraft systems|Aircraft power plant|Aircraft structures|Aircraft propeller)",
        "ntsb_text": None,
    },
    "maintenance": {
        "label": "Maintenance / inspection / MEL",
        "asrs_coded": [("anomaly", "Procedural Maintenance"), ("anomaly", "MEL / CDL"), ("contributing", "MEL"),
                       ("contributing", "Logbook Entry"), ("contributing", "Incorrect / Not Installed / Unavailable Part")],
        "asrs_text": r"\b(improperly (installed|maintained)|maintenance error|not signed off)\b",
        "ntsb_coded": r"Task performance-Maintenance|Inspection-Scheduled|Inspection-Annual|Maintenance personnel",
        "ntsb_text": r"\b(improper(ly)? (maintenance|installation|installed)|maintenance personnel)\b",
    },
    "atc": {
        "label": "Air traffic control involvement",
        "asrs_coded": [("contributing", "ATC Equipment"), ("anomaly", "ATC Issue")],
        "asrs_text": None,
        "ntsb_coded": r"ATC personnel|Air traffic/operating proc",
        "ntsb_text": r"\b(controller'?s?|air traffic control(ler)?'?s?) (failure|did not|failed|incorrect|inadequate|improper)",
    },
    "airport_surface": {
        "label": "Airport / runway / surface environment",
        "asrs_coded": [("contributing", "Airport")],
        "asrs_text": r"\b(hot ?spot|confusing (taxiway|signage)|runway (was )?(contaminated|wet|icy)|braking action)\b",
        "ntsb_coded": r"Runway/land/takeoff/taxi surface|Airport",
        "ntsb_text": r"\b(contaminated runway|braking action|wet runway|icy runway|snow-covered)\b",
    },
    "night": {
        "label": "Night / low light",
        "condition": True,  # from light-condition fields, not the narrative
    },
    "imc": {
        "label": "Instrument meteorological conditions (IMC)",
        "condition": True,
    },
}

# --- Events: what happened -----------------------------------------------------------------------
# asrs: substrings of ASRS "Anomaly"; ntsb: NTSB occurrence codes (last 3 digits of Occurrence_Code)
EVENTS = {
    "loss_of_control": {"label": "Loss of control (in flight or on ground)",
                        "asrs": ["Loss Of Aircraft Control"], "ntsb": ["230", "231", "240", "241", "270"]},
    "cfit_terrain": {"label": "Terrain / obstacle (CFIT, low altitude)",
                     "asrs": ["CFTT / CFIT"], "ntsb": ["120", "220", "470"]},
    "midair_nmac": {"label": "Midair, near midair, loss of separation",
                    "asrs": ["Conflict NMAC", "Conflict Airborne Conflict"], "ntsb": ["250", "260"]},
    "runway_incursion": {"label": "Runway / taxiway incursion",
                         "asrs": ["Ground Incursion"], "ntsb": ["320"]},
    "runway_excursion": {"label": "Runway excursion, overrun, undershoot",
                         "asrs": ["Ground Excursion"], "ntsb": ["300", "370", "380"]},
    "ground_collision": {"label": "Ground collision / ground strike",
                         "asrs": ["Conflict Ground Conflict", "Ground Strike", "Ground Event / Encounter Vehicle",
                                  "Ground Event / Encounter Object"], "ntsb": ["200", "490"]},
    "altitude_deviation": {"label": "Altitude / track / speed deviation",
                           "asrs": ["Deviation - Altitude", "Deviation - Track", "Deviation - Speed"], "ntsb": []},
    "clearance_deviation": {"label": "Clearance / airspace violation",
                            "asrs": ["Procedural Clearance", "Airspace Violation", "Landing Without Clearance"], "ntsb": []},
    "unstable_approach": {"label": "Unstabilized approach / hard or abnormal landing",
                          "asrs": ["Unstabilized Approach", "Gear Up Landing"],
                          "ntsb": ["090", "092", "094", "095", "096"]},
    "fuel_event": {"label": "Fuel exhaustion / starvation / contamination",
                   "asrs": ["Fuel Issue"], "ntsb": ["190", "191", "192", "193"]},
    "power_or_system_failure": {"label": "Engine power loss or system failure",
                                "asrs": ["Aircraft Equipment Problem"],
                                "ntsb": ["330", "332", "333", "337", "338", "340", "341", "342"]},
    "weather_encounter": {"label": "Weather / turbulence / VFR into IMC",
                          "asrs": ["Weather / Turbulence", "VFR In IMC"], "ntsb": ["360", "390", "401", "402"]},
    "wake": {"label": "Wake turbulence encounter", "asrs": ["Wake Vortex"], "ntsb": []},
    "fire_smoke": {"label": "Fire / smoke / fumes", "asrs": ["Smoke / Fire / Fumes"], "ntsb": ["150"]},
    "bird_strike": {"label": "Bird / animal strike", "asrs": ["Bird / Animal"], "ntsb": ["901"]},
}

# --- Phases and operation types ------------------------------------------------------------------
# ASRS "Flight Phase" values -> shared phase
ASRS_PHASE = {
    "Parked": "ground", "Taxi": "taxi", "Takeoff / Launch": "takeoff", "Initial Climb": "climb",
    "Climb": "climb", "Cruise": "cruise", "Descent": "descent", "Initial Approach": "approach",
    "Final Approach": "approach", "Landing": "landing",
}


def ntsb_phase(code: str | None) -> str | None:
    """NTSB occurrence code's first 3 digits -> shared phase."""
    if not code or len(code) < 3:
        return None
    if not code[:3].isdigit():
        return None
    p = int(code[:3])
    if 100 <= p < 250:  # prior to flight, standing
        return "ground"
    if 250 <= p < 300:
        return "taxi"
    if 300 <= p < 350:
        return "takeoff"
    if 350 <= p < 400 or p == 401:
        return "climb"
    if p == 404:
        return "descent"
    if 400 <= p < 450:
        return "cruise"
    if 450 <= p < 500:
        return "maneuvering"
    if 500 <= p < 550:
        return "approach"
    if 550 <= p < 600:
        return "landing"
    if 600 <= p < 900:  # emergency/uncontrolled descent, post-impact
        return "other"
    return None


PHASES = ["ground", "taxi", "takeoff", "climb", "cruise", "descent", "maneuvering", "approach", "landing", "other"]


def asrs_operation(far: str) -> str:
    far = far or ""
    if "121" in far:
        return "airline_121"
    if "135" in far:
        return "charter_135"
    if "107" in far or "UAS" in far:
        return "drone_uas"
    if "Part 91" in far:
        return "general_91"
    return "other"


def ntsb_operation(far: str, category: str) -> str:
    far = (far or "").strip()
    if category and category.strip() == "UNMA" or far == "107":
        return "drone_uas"
    return {"121": "airline_121", "135": "charter_135", "091": "general_91", "091K": "general_91"}.get(far, "other")


OPERATIONS = {
    "airline_121": "Scheduled airlines (FAR Part 121)",
    "charter_135": "Charter / commuter / air taxi (Part 135)",
    "general_91": "General aviation, private and corporate (Part 91)",
    "drone_uas": "Drones (Part 107 / recreational UAS)",
    "other": "Other / unknown / foreign / public",
}

# ASRS-only: who or what caught the problem, and what was done. These are the "defenses".
DEFENSE_NOTE = (
    "Only ASRS records how a problem was detected (Detector) and what stopped it (Result). NTSB "
    "accidents, by definition, are cases where the defenses did not stop the outcome."
)
