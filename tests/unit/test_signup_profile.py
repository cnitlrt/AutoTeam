import random
from datetime import date

from autoteam.signup_profile import (
    MAX_SIGNUP_AGE,
    MIN_SIGNUP_AGE,
    SignupProfile,
    calculate_age,
    generate_signup_profile,
)


class _FixedRng:
    def __init__(self, *, offsets, choices):
        self._offsets = iter(offsets)
        self._choices = iter(choices)

    def randrange(self, upper_bound):
        value = next(self._offsets)
        assert 0 <= value < upper_bound
        return value

    def choice(self, options):
        index = next(self._choices)
        return options[index]


def test_generate_signup_profile_name_is_two_ascii_words():
    profile = generate_signup_profile(today=date(2026, 4, 28), rng=random.Random(7))

    first_name, last_name = profile.full_name.split(" ")

    assert len(profile.full_name.split(" ")) == 2
    assert first_name.isascii() and first_name.isalpha()
    assert last_name.isascii() and last_name.isalpha()
    assert profile.full_name.count(" ") == 1


def test_generate_signup_profile_precise_age_stays_within_bounds():
    today = date(2026, 4, 28)
    rng = random.Random(12345)

    for _ in range(200):
        profile = generate_signup_profile(today=today, rng=rng)
        assert MIN_SIGNUP_AGE <= profile.age <= MAX_SIGNUP_AGE
        assert calculate_age(profile.birth_date, today) == profile.age


def test_generate_signup_profile_is_predictable_with_injected_today_and_rng():
    today = date(2026, 4, 28)

    oldest_allowed = date(1985, 4, 29)
    youngest_allowed = date(2001, 4, 28)
    assert calculate_age(oldest_allowed, today) == 40
    assert calculate_age(youngest_allowed, today) == 25

    oldest_profile = generate_signup_profile(today=today, rng=_FixedRng(offsets=[0], choices=[0, 0]))
    youngest_offset = (youngest_allowed - oldest_allowed).days
    youngest_profile = generate_signup_profile(
        today=today,
        rng=_FixedRng(offsets=[youngest_offset], choices=[1, 1]),
    )

    assert oldest_profile == SignupProfile("Ethan Carter", 1985, 4, 29, 40)
    assert youngest_profile == SignupProfile("Noah Bennett", 2001, 4, 28, 25)


def test_generate_signup_profile_is_not_constant():
    today = date(2026, 4, 28)
    rng = random.Random(99)

    seen = {generate_signup_profile(today=today, rng=rng) for _ in range(12)}

    assert len(seen) > 1
