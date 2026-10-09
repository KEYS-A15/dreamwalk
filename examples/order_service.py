"""Small demo: purchase validation, successful processing, and a failure path."""


class OrderError(ValueError):
    """A purchase cannot proceed."""


def validate_order(amount: float) -> float:
    """Reject nonpositive purchase amounts."""
    if amount <= 0:
        raise OrderError("Amount must be positive")
    return amount


def calculate_total(amount: float, tax_rate: float = 0.08) -> float:
    """Calculate the final total including tax."""
    return round(amount * (1 + tax_rate), 2)


class OrderService:
    """Accept orders and produce a receipt."""

    def process(self, amount: float) -> dict:
        validated = validate_order(amount)
        total = calculate_total(validated)
        return {"status": "accepted", "total": total}


def main():
    service = OrderService()
    try:
        return service.process(100)
    except OrderError as error:
        return {"status": "rejected", "reason": str(error)}


if __name__ == "__main__":
    print(main())
