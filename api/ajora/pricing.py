import re

PRICE_FIELDS = {"fee_inr", "certificate_fee_inr", "deposit_inr"}
AMOUNT = r"(?P<amount>[0-9][0-9,]*(?:\.[0-9]{1,2})?)(?![0-9]|[,.][0-9])"
MONEY = re.compile(
    rf"(?:(?:₹|\bINR\b|\bRs\.?)\s*{AMOUNT}|(?P<suffix>[0-9][0-9,]*(?:\.[0-9]{{1,2}})?)\s*(?:/-|rupees\b|INR\b))",
    re.I,
)
CONTEXT = {
    "fee_inr": r"\b(?:registration|participation|admission|entry|course|workshop|event)\s*(?:fee|charge|cost|price)s?\b|\b(?:registration|participation|admission|entry)\b",
    "certificate_fee_inr": r"\b(?:e[- ]?)?certificat(?:e|ion|es)\b",
    "deposit_inr": r"\b(?:deposit|security amount)\b",
}
NO_ENTRY_FEE = re.compile(
    r"\b(?:free\s+(?:entry|registration|participation|admission)|(?:entry|registration|participation|admission)(?:\s+(?:is|fee|cost|:))*\s*:?\s*free|"
    r"free\s+to\s+(?:attend|register|participate)|no\s+(?:entry|registration|participation|admission)\s+(?:fee|charge))\b", re.I,
)
NO_CERTIFICATE_FEE = re.compile(
    r"\b(?:free\s+(?:e[- ]?)?certificate|(?:e[- ]?)?certificate\s+(?:is\s+)?(?:free|included)|"
    r"no\s+(?:e[- ]?)?certificate\s+(?:fee|charge)|(?:e[- ]?)?certificate\b[^.;]{0,50}\b(?:at no (?:extra |additional )?(?:cost|charge)|no additional fee))\b", re.I,
)
NO_DEPOSIT = re.compile(r"\b(?:no\s+(?:security\s+)?deposit|deposit\s+(?:is\s+)?not\s+required)\b", re.I)
UNCERTAIN = re.compile(r"\b(?:not free|may be free|might be free|free of charge pending|to be confirmed|tentative|free trial)\b", re.I)
ZERO_TERMS = {"fee_inr": NO_ENTRY_FEE, "certificate_fee_inr": NO_CERTIFICATE_FEE, "deposit_inr": NO_DEPOSIT}


def requires_price_check(candidate):
    return any(getattr(option, field) is not None for option in candidate.attendance for field in PRICE_FIELDS)


def supports_amount(field, amount, excerpt):
    if UNCERTAIN.search(excerpt):
        return False
    prices = set()
    for item in MONEY.finditer(excerpt):
        literal = item.group("amount") or item.group("suffix")
        whole, _, decimal = literal.replace(",", "").partition(".")
        if len(whole) > 9 or decimal.strip("0"):
            return False
        prices.add(int(whole))
    if prices:
        if prices != {amount} or not re.search(CONTEXT[field], excerpt, re.I):
            return False
        # Each quote must identify one type of charge.
        return not any(re.search(CONTEXT[other], excerpt, re.I) for other in PRICE_FIELDS if other != field)
    return amount == 0 and bool(ZERO_TERMS[field].search(excerpt))


def verify_prices(candidate, evidence, structured):
    for index, option in enumerate(candidate.attendance):
        for field in sorted(PRICE_FIELDS):
            amount = getattr(option, field)
            if amount is None:
                continue
            key = f"attendance.{index}.{field}"
            excerpt = evidence[key]
            # Structured offers with an explicit INR currency already capture the exact fee and mode.
            if field == "fee_inr" and any(
                source.title == candidate.title and source.starts == candidate.starts and
                source.attendance and source.attendance[0].mode == option.mode and
                source.attendance[0].fee_inr == amount and
                not option.levels and claims.get("attendance.0.fee_inr") == excerpt
                for source, claims in structured
            ):
                continue
            if not supports_amount(field, amount, excerpt):
                raise ValueError(f"{key} needs an explicit INR amount or an explicit no-charge statement for that fee")
