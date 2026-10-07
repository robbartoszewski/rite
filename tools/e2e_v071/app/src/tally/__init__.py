"""tally — parse and add up money amounts. Throwaway app for rite's e2e gate."""

from decimal import Decimal, InvalidOperation


def parse_amount(text: str) -> Decimal:
    """'12.50' -> Decimal('12.50'). Raises ValueError on anything else."""
    try:
        value = Decimal(text.strip())
    except (InvalidOperation, AttributeError) as e:
        raise ValueError(f"not an amount: {text!r}") from e
    if not value.is_finite():
        raise ValueError(f"not an amount: {text!r}")
    return value
