"""Exact bounded decimal-string financial inputs."""

import re
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, StrictStr


def positive(value: str) -> str:
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,12})?", value):
        raise ValueError("Enter a plain decimal string with at most 12 decimal places.")
    if Decimal(value) <= 0:
        raise ValueError("Value must be greater than zero.")
    return value


def nonnegative(value: str) -> str:
    if re.fullmatch(r"0(?:\.[0-9]{1,12})?", value) and Decimal(value) == 0:
        return value
    return positive(value)


Money = Annotated[StrictStr, AfterValidator(positive)]
Cost = Annotated[StrictStr, AfterValidator(nonnegative)]
