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

import base64
import re
import datetime
import json
import os
from typing import Any, Literal
from zoneinfo import ZoneInfo

import google.auth
from google.auth.exceptions import DefaultCredentialsError

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# Configure authentication based on .env / environment settings
if os.environ.get("GOOGLE_API_KEY"):
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "False"
else:
    try:
        _, project_id = google.auth.default()
        if project_id:
            os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
    except DefaultCredentialsError:
        pass

from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.adk.events.request_input import RequestInput
from google.adk.models import Gemini
from google.adk.workflow import Workflow, node
from google.genai import types
from pydantic import BaseModel, Field

# Load threshold and model from config
from .config import AUTO_APPROVAL_THRESHOLD, LLM_MODEL


class ExpenseReport(BaseModel):
    amount: float = Field(description="The dollar amount of the expense.")
    submitter: str = Field(description="The person submitting the expense.")
    category: str = Field(
        description="The category of the expense (e.g. Travel, Meals, Supplies)."
    )
    description: str = Field(description="A brief description of the expense.")
    date: str = Field(description="The date of the expense.")


class RiskReview(BaseModel):
    risk_score: int = Field(description="Risk rating from 1 to 10.")
    risk_factors: list[str] = Field(
        description="List of detected risk factors or red flags."
    )
    alert_raised: bool = Field(
        description="Whether an alert should be raised to the human reviewer."
    )
    summary: str = Field(description="A brief explanation of the risk review.")


class ExpenseOutcome(BaseModel):
    amount: float = Field(description="Approved amount.")
    submitter: str = Field(description="Submitter name.")
    status: str = Field(description="Approval status (Approved/Rejected).")
    decision_type: str = Field(
        description="How the decision was made (Auto-Approved/Human Approved/Human Rejected)."
    )
    risk_summary: str = Field(description="Summary of risk evaluation.")


def parse_expense_event(node_input: Any) -> ExpenseReport:
    """Parses the incoming JSON event.

    Handles base64-encoded strings (Pub/Sub) and plain JSON.
    """
    raw_str = ""
    if isinstance(node_input, types.Content):
        parts = node_input.parts
        if parts:
            raw_str = parts[0].text or ""
    elif isinstance(node_input, str):
        raw_str = node_input
    elif isinstance(node_input, dict):
        data_payload = node_input
    else:
        raise ValueError(f"Unexpected input type: {type(node_input)}")

    if raw_str:
        try:
            data_payload = json.loads(raw_str)
        except json.JSONDecodeError:
            data_payload = {"data": raw_str}

    # Extract 'message' or 'data'
    message_data = None
    if isinstance(data_payload, dict):
        if "message" in data_payload and isinstance(data_payload["message"], dict):
            message_data = data_payload["message"].get("data")
        else:
            message_data = data_payload.get("data")

    if not message_data:
        if isinstance(data_payload, dict) and "amount" in data_payload:
            return ExpenseReport(**data_payload)
        raise ValueError("Invalid event payload: 'data' key not found.")

    # Decode base64 or parse directly
    if isinstance(message_data, str):
        try:
            decoded_bytes = base64.b64decode(message_data)
            decoded_str = decoded_bytes.decode("utf-8")
            expense_dict = json.loads(decoded_str)
        except Exception:
            try:
                expense_dict = json.loads(message_data)
            except json.JSONDecodeError:
                raise ValueError("Failed to parse data as JSON.")
    elif isinstance(message_data, dict):
        expense_dict = message_data
    else:
        raise ValueError("Unexpected type for 'data' key.")

    return ExpenseReport(**expense_dict)


def rule_evaluator(node_input: ExpenseReport) -> Event:
    """Evaluates the expense against the threshold rule."""
    if node_input.amount < AUTO_APPROVAL_THRESHOLD:
        return Event(
            output=node_input.model_dump(),
            actions=EventActions(
                route="auto_approve",
                state_delta={"expense_report": node_input.model_dump()},
            ),
        )
    else:
        return Event(
            output=node_input.model_dump(),
            actions=EventActions(
                route="needs_review",
                state_delta={"expense_report": node_input.model_dump()},
            ),
        )


def auto_approve(node_input: dict) -> ExpenseOutcome:
    """Instantly auto-approves the expense under the threshold."""
    return ExpenseOutcome(
        amount=node_input.get("amount", 0.0),
        submitter=node_input.get("submitter", ""),
        status="Approved",
        decision_type="Auto-Approved",
        risk_summary="Expense is under the threshold. No risk check required.",
    )


def security_checkpoint(ctx: Context, node_input: dict) -> Event:
    """Security Checkpoint: scrubs PII and checks for prompt injections."""
    description = node_input.get("description", "")

    # 1. Scrub PII (SSN and Credit Cards)
    # SSN pattern: XXX-XX-XXXX or XXXXXXXXX (9 digits)
    ssn_pattern = r"\b\d{3}-\d{2}-\d{4}\b|\b\d{9}\b"
    # Credit Card pattern: XXXX-XXXX-XXXX-XXXX or XXXXXXXXXXXXXXXX (16 digits)
    cc_pattern = r"\b(?:\d{4}-\d{4}-\d{4}-\d{4}|\d{16})\b"

    redacted_categories = []

    scrubbed_desc = description
    if re.search(ssn_pattern, scrubbed_desc):
        scrubbed_desc = re.sub(ssn_pattern, "[REDACTED SSN]", scrubbed_desc)
        redacted_categories.append("SSN")

    if re.search(cc_pattern, scrubbed_desc):
        scrubbed_desc = re.sub(cc_pattern, "[REDACTED CREDIT CARD]", scrubbed_desc)
        redacted_categories.append("Credit Card")

    # Update the expense report with the scrubbed description
    scrubbed_report = dict(node_input)
    scrubbed_report["description"] = scrubbed_desc

    # 2. Defend against prompt injection
    # Common prompt injection signals
    injection_patterns = [
        r"ignore\s+(?:all\s+)?previous\s+instructions",
        r"bypass\s+(?:the\s+)?rules",
        r"auto-approve\s+this\s+expense",
        r"override\s+(?:the\s+)?threshold",
        r"set\s+risk\s+score\s+to\s+1",
        r"system\s+prompt\s+override",
        r"you\s+must\s+approve",
    ]

    injection_detected = False
    for pattern in injection_patterns:
        if re.search(pattern, scrubbed_desc, re.IGNORECASE):
            injection_detected = True
            break

    # Update state with the clean/scrubbed expense report and redacted categories
    state_delta = {
        "expense_report": scrubbed_report,
        "redacted_categories": redacted_categories,
    }

    if injection_detected:
        # Construct pre-filled RiskReview output to pass directly to human_approval_gate
        suspicious_outcome = {
            "risk_score": 10,
            "risk_factors": ["PROMPT INJECTION DETECTED"],
            "alert_raised": True,
            "summary": "Prompt injection attempt detected in description. Bypassed AI review.",
        }
        return Event(
            output=suspicious_outcome,
            actions=EventActions(
                route="suspicious",
                state_delta=state_delta,
            ),
        )
    else:
        # Route to llm_review with scrubbed payload
        return Event(
            output=scrubbed_report,
            actions=EventActions(
                route="clean",
                state_delta=state_delta,
            ),
        )


llm_review = LlmAgent(
    name="llm_review",
    model=Gemini(
        model=LLM_MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=(
        "You are a risk auditing AI. Review the following expense report for risk factors:\n"
        "Submitter: {expense_report[submitter]}\n"
        "Amount: ${expense_report[amount]}\n"
        "Category: {expense_report[category]}\n"
        "Description: {expense_report[description]}\n"
        "Date: {expense_report[date]}\n\n"
        "Assess the risk score (1-10), identify any suspicious characteristics or policy violations, "
        "and determine if an alert should be raised."
    ),
    output_schema=RiskReview,
    output_key="risk_review",
)


@node(rerun_on_resume=True)
async def human_approval_gate(ctx: Context, node_input: dict):
    """Pauses the workflow for human approval and retrieves the response."""
    expense = ctx.state.get("expense_report", {})
    risk_score = node_input.get("risk_score", 1)
    risk_factors = ", ".join(node_input.get("risk_factors", []))
    summary = node_input.get("summary", "")

    if not ctx.resume_inputs or "human_decision" not in ctx.resume_inputs:
        msg = (
            f"🚨 EXPENSE AUDIT ALERT: ${expense.get('amount')} expense by {expense.get('submitter')} requires review.\n"
            f"Risk Score: {risk_score}/10\n"
            f"Risk Factors: {risk_factors}\n"
            f"AI Audit Summary: {summary}\n"
            "Please approve or reject this expense by entering: Approved or Rejected"
        )
        yield RequestInput(interrupt_id="human_decision", message=msg)
        return

    decision = ctx.resume_inputs["human_decision"].strip().lower()
    if decision in ["approved", "approve", "yes", "y"]:
        yield Event(output=node_input, actions=EventActions(route="approved"))
    else:
        yield Event(output=node_input, actions=EventActions(route="rejected"))


def record_approved_outcome(ctx: Context, node_input: dict) -> ExpenseOutcome:
    """Records approval by a human auditor."""
    expense = ctx.state.get("expense_report", {})
    return ExpenseOutcome(
        amount=expense.get("amount", 0.0),
        submitter=expense.get("submitter", ""),
        status="Approved",
        decision_type="Human Approved",
        risk_summary=node_input.get("summary", "Audited by AI."),
    )


def record_rejected_outcome(ctx: Context, node_input: dict) -> ExpenseOutcome:
    """Records rejection by a human auditor."""
    expense = ctx.state.get("expense_report", {})
    return ExpenseOutcome(
        amount=expense.get("amount", 0.0),
        submitter=expense.get("submitter", ""),
        status="Rejected",
        decision_type="Human Rejected",
        risk_summary=node_input.get("summary", "Audited by AI."),
    )


root_agent = Workflow(
    name="root_agent",
    edges=[
        ("START", parse_expense_event),
        (parse_expense_event, rule_evaluator),
        (
            rule_evaluator,
            {
                "auto_approve": auto_approve,
                "needs_review": security_checkpoint,
            },
        ),
        (
            security_checkpoint,
            {
                "clean": llm_review,
                "suspicious": human_approval_gate,
            },
        ),
        (llm_review, human_approval_gate),
        (
            human_approval_gate,
            {
                "approved": record_approved_outcome,
                "rejected": record_rejected_outcome,
            },
        ),
    ],
)

app = App(
    root_agent=root_agent,
    name="expense_agent",
    resumability_config=ResumabilityConfig(is_resumable=True),
)
