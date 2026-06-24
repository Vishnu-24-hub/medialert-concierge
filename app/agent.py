# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import os
import re
import sys

from google.adk import Workflow
from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.apps import App
from google.adk.events.request_input import RequestInput
from google.adk.tools import AgentTool
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.workflow import START, Edge, node
from mcp import StdioServerParameters

from .config import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP Server Connection
# ---------------------------------------------------------------------------
MCP_SERVER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")

_mcp_connection_params = StdioConnectionParams(
    server_params=StdioServerParameters(
        command=sys.executable,
        args=[MCP_SERVER_PATH],
    )
)

# ---------------------------------------------------------------------------
# Sub-Agents
# ---------------------------------------------------------------------------

# MedsSchedulerAgent: manages medication schedules via MCP
meds_scheduler_agent = LlmAgent(
    name="meds_scheduler_agent",
    description=(
        "Manages medication schedules. Can add, view, and check medication timings."
    ),
    model=config.model,
    instruction="""You are MediAlert's Medication Scheduler.
Your job is to help users manage their medication schedules.
Use the available MCP tools to:
- Add new medications with dosage and schedule
- Retrieve the current list of medications
- Check for known side effects of any listed drug

Always be precise with medication names, dosages, and timing.
If the user asks about potential drug interactions or side effects, use the get_drug_side_effects tool.
When done, summarize the medication schedule clearly.""",
    tools=[
        McpToolset(
            connection_params=_mcp_connection_params,
            tool_filter=["add_medication_schedule", "get_medication_schedules", "get_drug_side_effects"],
        )
    ],
)

# SymptomLoggerAgent: logs and retrieves symptoms via MCP
symptom_logger_agent = LlmAgent(
    name="symptom_logger_agent",
    description=(
        "Logs and retrieves patient symptoms. Identifies severity levels."
    ),
    model=config.model,
    instruction="""You are MediAlert's Symptom Logger.
Your job is to help users log their daily symptoms and retrieve past records.
Use the available MCP tools to:
- Log a symptom with severity (Mild, Moderate, Severe) and optional notes
- Retrieve symptom history

Always ask clearly: what symptom, what severity (Mild/Moderate/Severe), any notes?
Flag any symptom marked as 'Severe' clearly in your summary.
When done, list all recent symptoms and their severities.""",
    tools=[
        McpToolset(
            connection_params=_mcp_connection_params,
            tool_filter=["log_symptom", "get_symptom_logs"],
        )
    ],
)

# DocVisitPrepAgent: summarizes data for doctor visits
doc_visit_prep_agent = LlmAgent(
    name="doc_visit_prep_agent",
    description=(
        "Prepares a summary report and list of questions for an upcoming doctor visit."
    ),
    model=config.model,
    instruction="""You are MediAlert's Doctor Visit Prep assistant.
Your job is to prepare a concise, structured report for the user's upcoming doctor visit.
When invoked, retrieve all available context from the conversation (medications, symptoms) and:
1. Summarize current medications with dosages
2. List all logged symptoms by severity
3. Highlight any Severe symptoms or drug side effects flagged earlier
4. Suggest 3-5 clear, specific questions for the doctor based on the health data

Format the output as a clean, readable doctor prep sheet.""",
    tools=[
        McpToolset(
            connection_params=_mcp_connection_params,
            tool_filter=["get_medication_schedules", "get_symptom_logs", "get_drug_side_effects"],
        )
    ],
)

# Orchestrator with AgentTools for delegation
orchestrator_agent = LlmAgent(
    name="medialert_orchestrator",
    description="Top-level MediAlert orchestrator. Routes user requests to specialized health agents.",
    model=config.model,
    instruction="""You are MediAlert — a personal health concierge assistant.
You help users manage their medications, log symptoms, and prepare for doctor visits.

Based on the user's request, delegate to the right specialist:
- Use meds_scheduler_agent when the user wants to add/view medications or check drug side effects
- Use symptom_logger_agent when the user wants to log or review symptoms
- Use doc_visit_prep_agent when the user wants to prepare for a doctor appointment

Always confirm what action you took and provide a brief summary of the result.
Be concise, empathetic, and patient-focused.""",
    tools=[
        AgentTool(agent=meds_scheduler_agent),
        AgentTool(agent=symptom_logger_agent),
        AgentTool(agent=doc_visit_prep_agent),
    ],
)

# ---------------------------------------------------------------------------
# Workflow State Schema
# ---------------------------------------------------------------------------
from pydantic import BaseModel as PydanticBaseModel

class MediAlertState(PydanticBaseModel):
    user_request: str = ""
    security_passed: bool = False
    security_reason: str = ""
    needs_human_review: bool = False
    audit_log: list = []

# ---------------------------------------------------------------------------
# Workflow Function Nodes
# ---------------------------------------------------------------------------

@node
async def intake_node(ctx: Context, node_input: str) -> str:
    """Entry node: saves the user request to state."""
    ctx.state["user_request"] = str(node_input)
    ctx.state.setdefault("audit_log", [])
    ctx.state.setdefault("needs_human_review", False)
    ctx.state.setdefault("security_passed", False)
    logger.info("IntakeNode: received request: %s", node_input[:100])
    return node_input


@node
async def security_checkpoint(ctx: Context, node_input: str) -> str:
    """Security gate: PII scrubbing + injection detection + audit log."""
    import datetime

    request = str(node_input)
    audit = ctx.state.get("audit_log", [])
    passed = True
    reasons = []

    # ── PII Scrubbing (health domain: names, SSN, phone, email, health IDs) ──
    pii_patterns = [
        (r"\b\d{3}-\d{2}-\d{4}\b", "[SSN_REDACTED]"),           # SSN
        (r"\b\d{10,12}\b", "[HEALTHID_REDACTED]"),               # health ID / insurance
        (r"\b[\w.+-]+@[\w-]+\.\w{2,}\b", "[EMAIL_REDACTED]"),   # email
        (r"\b(\+1[\-\s]?)?\(?\d{3}\)?[\-\s]?\d{3}[\-\s]?\d{4}\b", "[PHONE_REDACTED]"),  # phone
        (r"\bpatient\s*id\s*[:#]?\s*\d+\b", "[PATIENTID_REDACTED]", ),  # patient ID
    ]
    cleaned = request
    for pattern, replacement, *_ in pii_patterns:
        if re.search(pattern, cleaned, re.IGNORECASE):
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
            reasons.append(f"PII scrubbed: {replacement}")

    # ── Prompt Injection Detection ──
    injection_keywords = [
        "ignore previous instructions",
        "forget your instructions",
        "act as a different ai",
        "bypass",
        "jailbreak",
        "disregard your",
        "override your",
        "you are now",
        "pretend you are",
        "your new instructions are",
    ]
    lowered = cleaned.lower()
    for keyword in injection_keywords:
        if keyword in lowered:
            passed = False
            reasons.append(f"Injection detected: '{keyword}'")

    # ── Domain-specific rule: consent check for severe medication changes ──
    severe_keywords = ["overdose", "stop all medications", "double the dose", "triple the dose"]
    for keyword in severe_keywords:
        if keyword in lowered:
            ctx.state["needs_human_review"] = True
            reasons.append(f"Critical health action flagged: '{keyword}'")

    # ── Audit Log Entry ──
    severity = "CRITICAL" if not passed else ("WARNING" if reasons else "INFO")
    audit_entry = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "severity": severity,
        "request_preview": cleaned[:80],
        "notes": reasons if reasons else ["OK"],
        "passed": passed,
    }
    audit.append(audit_entry)
    ctx.state["audit_log"] = audit
    ctx.state["security_passed"] = passed
    ctx.state["security_reason"] = "; ".join(reasons) if reasons else "OK"

    logger.info("SecurityCheckpoint: severity=%s, passed=%s, notes=%s", severity, passed, reasons)

    if not passed:
        return "SECURITY_BLOCK"

    return "PROCEED"


@node(rerun_on_resume=True)
async def human_review_node(ctx: Context, node_input: str):
    """HITL pause: asks a human to review flagged high-risk requests."""
    needs_review = ctx.state.get("needs_human_review", False)
    if needs_review:
        request = ctx.state.get("user_request", "")
        # Yield a RequestInput to pause and wait for human confirmation
        yield RequestInput(
            message=(
                f"⚠️ HIGH-RISK HEALTH REQUEST flagged by MediAlert.\n\n"
                f"Original request: {request[:200]}\n\n"
                "Please confirm: type 'CONFIRM' to proceed or 'DENY' to block."
            )
        )
        # After resume, check the user's response
        resume = ctx.resume_inputs or {}
        response = str(resume).upper()
        if "CONFIRM" not in response:
            ctx.state["security_passed"] = False
            ctx.state["security_reason"] = "Human reviewer denied high-risk request."
            yield "DENIED"
            return
    yield "APPROVED"


@node
async def process_request_node(ctx: Context, node_input: str) -> str:
    """Main orchestration: runs the orchestrator agent."""
    return ctx.state.get("user_request", str(node_input))


@node
async def blocked_node(ctx: Context, node_input: str) -> str:
    """Returns a safe blocked-request message."""
    reason = ctx.state.get("security_reason", "Security policy violation.")
    return (
        f"🚫 Your request has been blocked by MediAlert's security policy.\n"
        f"Reason: {reason}\n\n"
        "If you believe this is an error, please rephrase your request "
        "or contact your healthcare provider directly."
    )


@node
async def final_output_node(ctx: Context, node_input: str) -> str:
    """Final node: passes through the result."""
    return str(node_input)


# ---------------------------------------------------------------------------
# Workflow Graph
# ---------------------------------------------------------------------------

medialert_workflow = Workflow(
    name="medialert_workflow",
    description="MediAlert Concierge: medication scheduling, symptom logging, and doctor visit prep.",
    state_schema=MediAlertState,
    edges=[
        # START → intake
        (START, intake_node),
        # intake → security checkpoint
        (intake_node, security_checkpoint),
        # security checkpoint routes
        (security_checkpoint, {
            "SECURITY_BLOCK": blocked_node,
            "PROCEED": human_review_node,
        }),
        # human review routes — converge on a single target to avoid duplicate edge
        (human_review_node, {
            "APPROVED": process_request_node,
            "DENIED": blocked_node,
        }),
        # process_request → orchestrator → final
        (process_request_node, orchestrator_agent),
        # One unconditional edge from orchestrator to final output
        (orchestrator_agent, final_output_node),
        # blocked goes to final output
        (blocked_node, final_output_node),
    ],
)

# ADK App export
app = App(
    root_agent=medialert_workflow,
    name="app",
)
