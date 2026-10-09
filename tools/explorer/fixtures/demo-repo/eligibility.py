"""Minimal real repository fixture for the Search Lab smoke test."""


def eligibility_validator(customer: dict[str, object]) -> bool:
    """Return true when the customer is active."""
    return bool(customer.get("active", False))
