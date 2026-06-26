from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ChurnSentinelMCPServer")


@mcp.tool()
def get_customer_metrics(customer_id: str) -> str:
    """Get usage metrics and contract details for a customer.

    Args:
        customer_id: The unique ID of the customer (e.g., CUST-101, CUST-102).
    """
    data = {
        "CUST-101": {
            "name": "Acme Corp",
            "tier": "Enterprise",
            "monthly_spend": 5000,
            "license_utilization": 0.35,  # Low utilization indicates high churn risk
            "support_tickets_count": 12,
            "contract_start": "2025-01-01",
        },
        "CUST-102": {
            "name": "Beta Industries",
            "tier": "Professional",
            "monthly_spend": 1200,
            "license_utilization": 0.85,
            "support_tickets_count": 2,
            "contract_start": "2025-03-15",
        },
        "CUST-103": {
            "name": "Gamma Startups",
            "tier": "Free",
            "monthly_spend": 0,
            "license_utilization": 0.90,
            "support_tickets_count": 1,
            "contract_start": "2026-02-01",
        },
    }
    customer = data.get(customer_id.upper())
    if not customer:
        return f"Customer {customer_id} not found."
    return f"Customer Metrics for {customer_id} ({customer['name']}):\n" + "\n".join(
        f"- {k}: {v}" for k, v in customer.items()
    )


@mcp.tool()
def get_customer_support_history(customer_id: str) -> str:
    """Get the recent support ticket history and sentiment tags for a customer.

    Args:
        customer_id: The unique ID of the customer (e.g., CUST-101, CUST-102).
    """
    data = {
        "CUST-101": [
            {
                "date": "2026-06-10",
                "subject": "Billing issue: Charged double last month",
                "status": "Closed",
                "sentiment": "ANGRY",
            },
            {
                "date": "2026-06-18",
                "subject": "API timeout errors in production",
                "status": "Open",
                "sentiment": "FRUSTRATED",
            },
            {
                "date": "2026-06-25",
                "subject": "Request to cancel subscription",
                "status": "Open",
                "sentiment": "CRITICAL",
            },
        ],
        "CUST-102": [
            {
                "date": "2026-05-12",
                "subject": "How to invite new users?",
                "status": "Closed",
                "sentiment": "NEUTRAL",
            },
        ],
    }
    tickets = data.get(customer_id.upper(), [])
    if not tickets:
        return f"No support ticket history found for {customer_id}."
    return f"Support Ticket History for {customer_id}:\n" + "\n".join(
        f"- [{t['date']}] {t['subject']} (Status: {t['status']}, Sentiment: {t['sentiment']})"
        for t in tickets
    )


@mcp.tool()
def get_retention_policy_rules() -> str:
    """Get the official corporate rules and limits for customer retention discounts and offers."""
    return """Corporate Retention Policy Rules:
- Enterprise Tier: Authorized to offer up to 25% discount on contract renewal, OR up to 3 months free. High-priority direct engineering contact can be assigned.
- Professional Tier: Authorized to offer up to 15% discount on renewal, OR 1 month free. Direct CSM contact can be assigned.
- Standard / Free Tier: No financial discount authorized. Offer self-service resources or standard onboarding assistance.
- Executive approval is required for any custom retention offers exceeding these limits.
"""


if __name__ == "__main__":
    mcp.run("stdio")
