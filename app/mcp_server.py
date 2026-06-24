import json
import os
from mcp.server.fastmcp import FastMCP

# Define FastMCP server
mcp = FastMCP("MediAlert Server")

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "health_state.json")

def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"schedules": [], "symptoms": []}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"schedules": [], "symptoms": []}

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

@mcp.tool()
def add_medication_schedule(name: str, dosage: str, frequency: str, time_of_day: str) -> str:
    """
    Adds a new medication schedule entry for the user.
    
    Args:
        name: Name of the medication (e.g., "Lisinopril").
        dosage: Dosage amount (e.g., "10mg").
        frequency: How often to take (e.g., "Once daily").
        time_of_day: Time of day to take (e.g., "Morning", "8:00 AM").
    """
    state = load_state()
    entry = {
        "name": name.strip(),
        "dosage": dosage.strip(),
        "frequency": frequency.strip(),
        "time_of_day": time_of_day.strip()
    }
    state["schedules"].append(entry)
    save_state(state)
    return f"Successfully added medication schedule: {name} ({dosage}), taken {frequency} in the {time_of_day}."

@mcp.tool()
def get_medication_schedules() -> str:
    """
    Retrieves all active medication schedules.
    """
    state = load_state()
    schedules = state.get("schedules", [])
    if not schedules:
        return "No active medication schedules found."
    
    result = "Active Medication Schedules:\n"
    for i, s in enumerate(schedules, 1):
        result += f"{i}. {s['name']} - Dosage: {s['dosage']} | Frequency: {s['frequency']} | Time: {s['time_of_day']}\n"
    return result

@mcp.tool()
def log_symptom(symptom_name: str, severity: str, notes: str = "") -> str:
    """
    Logs a patient's daily symptom details.
    
    Args:
        symptom_name: Name/description of the symptom (e.g., "Mild headache").
        severity: Severity level: "Mild", "Moderate", "Severe".
        notes: Any additional comments or context.
    """
    state = load_state()
    entry = {
        "symptom": symptom_name.strip(),
        "severity": severity.strip(),
        "notes": notes.strip()
    }
    state["symptoms"].append(entry)
    save_state(state)
    return f"Successfully logged symptom: {symptom_name} (Severity: {severity})."

@mcp.tool()
def get_symptom_logs() -> str:
    """
    Retrieves all past symptom logs.
    """
    state = load_state()
    symptoms = state.get("symptoms", [])
    if not symptoms:
        return "No symptom logs found."
    
    result = "Logged Symptoms History:\n"
    for i, s in enumerate(symptoms, 1):
        notes_str = f" | Notes: {s['notes']}" if s['notes'] else ""
        result += f"{i}. {s['symptom']} (Severity: {s['severity']}){notes_str}\n"
    return result

@mcp.tool()
def get_drug_side_effects(drug_name: str) -> str:
    """
    Looks up known side effects and potential interactions for a specific drug.
    
    Args:
        drug_name: The name of the medication to check.
    """
    # Simple mock database of drug side effects
    db = {
        "lisinopril": "Dry cough, dizziness, high potassium levels. Warning: Do not take during pregnancy or combine with potassium supplements.",
        "metformin": "Nausea, diarrhea, stomach upset, metallic taste. Warning: Avoid excessive alcohol consumption due to lactic acidosis risk.",
        "atorvastatin": "Muscle pain, headache, nasal congestion. Warning: Avoid grapefruit/grapefruit juice.",
        "albuterol": "Tremors, nervousness, rapid heart rate, headache.",
        "ibuprofen": "Stomach pain, heartburn, dizziness. Warning: Can cause stomach bleeding or kidney issues with prolonged use.",
        "aspirin": "Stomach upset, easy bruising. Warning: Risk of bleeding. Do not give to children/teens."
    }
    
    name = drug_name.lower().strip()
    if name in db:
        return f"Known side effects for {drug_name}: {db[name]}"
    
    return f"No side effects data found for '{drug_name}'. Advise the patient to consult their pharmacist or physician."

if __name__ == "__main__":
    mcp.run()
