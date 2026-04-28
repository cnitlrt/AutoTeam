from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

MIN_SIGNUP_AGE = 25
MAX_SIGNUP_AGE = 40

FIRST_NAMES = (
    "Ethan",
    "Noah",
    "Liam",
    "Mason",
    "Logan",
    "Lucas",
    "Henry",
    "Owen",
    "Nathan",
    "Caleb",
    "Wyatt",
    "Ryan",
    "Isaac",
    "Levi",
    "Adrian",
    "Evan",
    "Miles",
    "Julian",
    "Connor",
    "Cole",
)

LAST_NAMES = (
    "Carter",
    "Bennett",
    "Parker",
    "Hayes",
    "Mitchell",
    "Turner",
    "Foster",
    "Brooks",
    "Collins",
    "Reed",
    "Bailey",
    "Morgan",
    "Ward",
    "Cooper",
    "Powell",
    "Hughes",
    "Price",
    "Wood",
    "Kelly",
    "Ross",
)


@dataclass(frozen=True, slots=True)
class SignupProfile:
    full_name: str
    birth_year: int
    birth_month: int
    birth_day: int
    age: int

    @property
    def birth_date(self) -> date:
        return date(self.birth_year, self.birth_month, self.birth_day)

    @property
    def birth_year_text(self) -> str:
        return f"{self.birth_year:04d}"

    @property
    def birth_month_text(self) -> str:
        return f"{self.birth_month:02d}"

    @property
    def birth_day_text(self) -> str:
        return f"{self.birth_day:02d}"

    @property
    def age_text(self) -> str:
        return str(self.age)

    @property
    def birthday_text(self) -> str:
        return f"{self.birth_year_text}/{self.birth_month_text}/{self.birth_day_text}"

    def positional_birthday_orders(self) -> list[tuple[str, str, str]]:
        year = self.birth_year_text
        month = self.birth_month_text
        day = self.birth_day_text
        return [
            (year, month, day),
            (month, day, year),
            (day, month, year),
        ]


def calculate_age(birth_date: date, today: date) -> int:
    years = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def _replace_year_safe(value: date, year: int) -> date:
    try:
        return value.replace(year=year)
    except ValueError:
        return value.replace(year=year, day=28)


def _birthdate_bounds(
    today: date, *, min_age: int = MIN_SIGNUP_AGE, max_age: int = MAX_SIGNUP_AGE
) -> tuple[date, date]:
    oldest_allowed = _replace_year_safe(today, today.year - (max_age + 1)) + timedelta(days=1)
    youngest_allowed = _replace_year_safe(today, today.year - min_age)
    return oldest_allowed, youngest_allowed


def generate_signup_profile(
    *, today: date | None = None, rng: random.Random | random.SystemRandom | None = None
) -> SignupProfile:
    today = today or date.today()
    rng = rng or random.SystemRandom()

    oldest_allowed, youngest_allowed = _birthdate_bounds(today)
    birthday_offset = rng.randrange((youngest_allowed - oldest_allowed).days + 1)
    birth_date = oldest_allowed + timedelta(days=birthday_offset)
    age = calculate_age(birth_date, today)

    if not (MIN_SIGNUP_AGE <= age <= MAX_SIGNUP_AGE):
        raise ValueError(f"generated invalid signup age {age} for birth date {birth_date.isoformat()}")

    return SignupProfile(
        full_name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
        birth_year=birth_date.year,
        birth_month=birth_date.month,
        birth_day=birth_date.day,
        age=age,
    )
