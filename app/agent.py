import datetime
import json
import logging
import re
import sys
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.apps import App
from google.adk.events import RequestInput
from google.adk.models import Gemini
from google.adk.tools import AgentTool
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from google.adk.workflow import START, Edge, Workflow, node
from mcp import StdioServerParameters

from app.config import config

logger = logging.getLogger("customer_churn_sentinel")

# ---------------------------------------------------------------------------
# MCP Toolset Setup
# ---------------------------------------------------------------------------

mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
        )
    )
)

# ---------------------------------------------------------------------------
# Specialized Sub-Agents
# ---------------------------------------------------------------------------

risk_analysis_agent = LlmAgent(
    name="risk_analysis_agent",
    model=Gemini(model=config.model),
    instruction="""You are a Churn Risk Analysis Specialist.
    Analyze the customer's support tickets, engagement patterns, sentiment, and usage logs.
    Use the get_customer_metrics and get_customer_support_history tools to retrieve customer details.
    Identify the root causes of their frustration (e.g., technical bugs, pricing, missing features).
    Assign a numerical risk score (0.0 to 1.0) and a risk level (Low, Medium, High).
    Your response MUST include a clear score in the format 'Score: 0.XX'.
    """,
    tools=[mcp_toolset],
)

retention_strategy_agent = LlmAgent(
    name="retention_strategy_agent",
    model=Gemini(model=config.model),
    instruction="""You are a Customer Retention Strategist.
    Based on the risk analysis and score provided, craft a tailored retention plan.
    Use the get_retention_policy_rules tool to verify authorized discount ranges.
    Propose specific actions (e.g., discount, free training, dedicated account manager support).
    Draft a personalized email response to the customer addressing their specific complaints.
    """,
    tools=[mcp_toolset],
)

# ---------------------------------------------------------------------------
# Orchestrator / Coordinator Agent
# ---------------------------------------------------------------------------

coordinator_agent = LlmAgent(
    name="coordinator_agent",
    model=Gemini(model=config.model),
    instruction="""You are the Customer Churn Sentinel Coordinator.
    Your task is to coordinate the analysis and retention planning for a customer at risk of churn.

    1. First, call the risk_analysis_agent tool with the customer's case details.
    2. Then, call the retention_strategy_agent tool with the analysis results.

    Provide a unified summary containing:
    - The customer case summary.
    - The numerical churn risk score (e.g., 'Score: 0.85').
    - The recommended retention offer and draft email.

    Ensure you output the score in the exact format 'Score: 0.XX' so that it can be parsed.
    """,
    tools=[
        AgentTool(risk_analysis_agent),
        AgentTool(retention_strategy_agent),
    ],
    mode="single_turn",
)

# ---------------------------------------------------------------------------
# PII Patterns: Email, Phone, SSN, Credit Card, Customer ID
# ---------------------------------------------------------------------------
_PII_PATTERNS = [
    (re.compile(r"[\w\.-]+@[\w\.-]+\.\w+"), "[EMAIL_REDACTED]"),
    (
        re.compile(
            r"\+?\d{1,4}?[-.\s]?\(?\d{1,3}?\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,9}"
        ),
        "[PHONE_REDACTED]",
    ),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),
    (re.compile(r"\b(?:\d[ -]*?){13,16}\b"), "[CREDITCARD_REDACTED]"),
]

# Prompt injection keywords to block
_INJECTION_KEYWORDS = [
    "ignore previous instructions",
    "ignore all instructions",
    "system prompt",
    "print your instructions",
    "reveal your instructions",
    "jailbreak",
    "forget your role",
    "act as if",
    "do anything now",
]

# Domain-specific: competitor names used in a suspicious context
_COMPETITOR_ABUSE_KEYWORDS = [
    "use competitor data",
    "competitor pricing exploit",
    "cheat pricing model",
]


def _scrub_pii(text: str) -> tuple[str, list[str]]:
    """Apply all PII regex patterns and return (scrubbed_text, list_of_types_redacted)."""
    scrubbed = text
    redacted_types: list[str] = []
    for pattern, replacement in _PII_PATTERNS:
        result = pattern.sub(replacement, scrubbed)
        if result != scrubbed:
            redacted_types.append(replacement)
            scrubbed = result
    return scrubbed, redacted_types


def _detect_injection(text: str) -> str | None:
    """Returns the first matched injection keyword or None if clean."""
    lowered = text.lower()
    for kw in _INJECTION_KEYWORDS:
        if kw in lowered:
            return kw
    return None


def _detect_competitor_abuse(text: str) -> str | None:
    """Domain rule: detect competitor pricing abuse attempts."""
    lowered = text.lower()
    for kw in _COMPETITOR_ABUSE_KEYWORDS:
        if kw in lowered:
            return kw
    return None


def _emit_audit_log(event: str, severity: str, extra: dict) -> None:
    """Emit a structured JSON audit log entry."""
    entry = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "event": event,
        "severity": severity,
        **extra,
    }
    if severity == "CRITICAL":
        logger.critical(json.dumps(entry))
    elif severity == "WARNING":
        logger.warning(json.dumps(entry))
    else:
        logger.info(json.dumps(entry))


@node
async def security_checkpoint(ctx: Context, node_input: str) -> str:
    """Security gate: PII scrubbing, prompt injection detection, domain policy check, and audit logging.

    Position in graph: Entry point immediately after START — every query passes through here first.
    Routes:
      'clean'          → coordinator_agent (proceed normally)
      'security_event' → security_handler  (block and audit)
    """
    if not config.pii_redaction_enabled and not config.injection_detection_enabled:
        ctx.state["security_status"] = "SKIPPED"
        ctx.route = "clean"
        return node_input

    ctx.state["security_status"] = "CLEAN"
    ctx.state["original_query"] = node_input

    # 1. PII Scrubbing — email, phone, SSN, credit card
    scrubbed, redacted_types = _scrub_pii(node_input)
    ctx.state["scrubbed_query"] = scrubbed
    if redacted_types:
        _emit_audit_log(
            event="pii_scrubbed",
            severity="WARNING",
            extra={"types_redacted": redacted_types, "query_preview": scrubbed[:80]},
        )

    # 2. Prompt Injection Detection
    injection_hit = (
        _detect_injection(node_input) if config.injection_detection_enabled else None
    )
    if injection_hit:
        ctx.state["security_status"] = "BLOCKED"
        ctx.state["block_reason"] = f"prompt_injection: '{injection_hit}'"
        _emit_audit_log(
            event="prompt_injection_blocked",
            severity="CRITICAL",
            extra={"matched_keyword": injection_hit},
        )
        ctx.route = "security_event"
        return f"[SECURITY_BLOCK] Prompt injection detected: '{injection_hit}'"

    # 3. Domain-specific rule: competitor pricing abuse
    abuse_hit = _detect_competitor_abuse(node_input)
    if abuse_hit:
        ctx.state["security_status"] = "BLOCKED"
        ctx.state["block_reason"] = f"competitor_abuse_policy: '{abuse_hit}'"
        _emit_audit_log(
            event="competitor_abuse_policy_violation",
            severity="CRITICAL",
            extra={"matched_keyword": abuse_hit},
        )
        ctx.route = "security_event"
        return "[SECURITY_BLOCK] Policy violation: competitor pricing abuse detected."

    # 4. All clear — proceed
    _emit_audit_log(
        event="security_checkpoint_passed",
        severity="INFO",
        extra={"pii_redacted": bool(redacted_types), "query_preview": scrubbed[:80]},
    )
    ctx.route = "clean"
    return scrubbed


@node
async def security_handler(ctx: Context, node_input: str) -> dict:
    """Terminal node for flagged security events. Logs and returns rejection details."""
    block_reason = ctx.state.get("block_reason", "unknown_policy_violation")
    _emit_audit_log(
        event="security_event_handled",
        severity="CRITICAL",
        extra={"block_reason": block_reason, "action": "REQUEST_REJECTED"},
    )
    return {
        "status": "REJECTED",
        "security_status": "BLOCKED",
        "block_reason": block_reason,
        "message": "Your request has been blocked by the security policy. Please contact support if you believe this is an error.",
    }


@node
async def decision_gate(ctx: Context, node_input: str) -> str:
    """Parses coordinator agent output and decides the approval/review path."""
    ctx.state["coordinator_response"] = node_input

    # Parse score
    score_match = re.search(r"score:\s*(0\.\d+|1\.0|\d+%)", node_input, re.IGNORECASE)
    score = 0.5
    if score_match:
        val = score_match.group(1)
        if "%" in val:
            score = float(val.replace("%", "")) / 100.0
        else:
            score = float(val)

    ctx.state["churn_score"] = score

    # High risk requires human review
    if score >= 0.8:
        ctx.route = "needs_review"
        return f"High risk churn detected (Score: {score}). Routing to Human Review."
    else:
        ctx.route = "auto_approved"
        return f"Auto-approving retention strategy (Score: {score})."


@node
async def human_review_node(ctx: Context, node_input: str) -> AsyncGenerator[Any, None]:
    """Human-in-the-loop step requesting manual approval for high-risk offers."""
    interrupt_id = "human_approval"

    if interrupt_id in ctx.resume_inputs:
        response = ctx.resume_inputs[interrupt_id]
        ctx.state["human_feedback"] = response
        yield f"Human feedback received: {response}"
        return

    yield RequestInput(
        interrupt_id=interrupt_id,
        message="Please review and approve the retention strategy for this high-risk customer.",
        payload={
            "score": ctx.state.get("churn_score"),
            "coordinator_response": ctx.state.get("coordinator_response"),
        },
    )


@node
async def final_output(ctx: Context, node_input: str) -> dict:
    """Aggregates all results and returns the final customer sentiment strategy."""
    return {
        "status": "APPROVED",
        "security_status": ctx.state.get("security_status"),
        "churn_score": ctx.state.get("churn_score"),
        "coordinator_response": ctx.state.get("coordinator_response"),
        "human_feedback": ctx.state.get("human_feedback"),
        "message": node_input,
    }


# ---------------------------------------------------------------------------
# Workflow Graph & App Definition
# ---------------------------------------------------------------------------

workflow = Workflow(
    name="churn_sentinel_workflow",
    edges=[
        Edge(from_node=START, to_node=security_checkpoint),
        Edge(from_node=security_checkpoint, to_node=coordinator_agent, route="clean"),
        Edge(
            from_node=security_checkpoint,
            to_node=security_handler,
            route="security_event",
        ),
        Edge(from_node=coordinator_agent, to_node=decision_gate),
        Edge(from_node=decision_gate, to_node=human_review_node, route="needs_review"),
        Edge(from_node=decision_gate, to_node=final_output, route="auto_approved"),
        Edge(from_node=human_review_node, to_node=final_output),
    ],
)

app = App(
    root_agent=workflow,
    name="app",
)

root_agent = workflow
