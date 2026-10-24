from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Money is exact INR with two decimals (DR-04): never a float. Rounding to the paisa is half up.
PAISA = Decimal("0.01")
MINUS = "−"  # the minus sign used in price summaries ("− ₹300", DESIGN 6.5)


def to_money(value) -> Decimal:
    """An exact INR amount from a Decimal, an int or a decimal string; floats are refused."""
    if isinstance(value, (float, bool)):
        raise TypeError("money must be a Decimal, an int or a decimal string, never a float")
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("not an amount of money") from error
    if not amount.is_finite():
        raise ValueError("not an amount of money")
    return amount.quantize(PAISA, rounding=ROUND_HALF_UP)


def _indian_grouping(rupees: int) -> str:
    # the last three digits, then groups of two: 1,24,999
    digits = str(rupees)
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([*groups, tail])


def format_inr(value, *, always_paise=False) -> str:
    """₹1,24,999, with paise only when not zero (or when always_paise is set). DESIGN 6.5."""
    amount = to_money(value)
    paise_total = int(abs(amount) * 100)
    rupees, paise = divmod(paise_total, 100)
    text = f"₹{_indian_grouping(rupees)}"
    if paise or always_paise:
        text += f".{paise:02d}"
    return f"{MINUS} {text}" if amount < 0 else text


def format_inr_lines(values) -> list[str]:
    """Amounts shown together: if any has paise, all show two decimals (DESIGN 6.5)."""
    amounts = [to_money(value) for value in values]
    always_paise = any(amount % 1 for amount in amounts)
    return [format_inr(amount, always_paise=always_paise) for amount in amounts]
