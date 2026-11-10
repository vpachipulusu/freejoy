"""Partner API key format, parsing and hashing (ADR-013)."""

import hashlib
import re
from collections.abc import Callable

import pytest

from app.core.ids import new_id
from app.core.partner_keys import (
    DUMMY_DIGEST,
    KEY_ID_LENGTH,
    SECRET_LENGTH,
    generate_key,
    key_digest,
    key_matches,
    parse_key,
    running_environment,
)

ENV = "test"


def test_a_generated_key_has_the_documented_shape() -> None:
    key = generate_key(ENV, new_id())

    assert re.fullmatch(
        rf"fj_test_[a-z0-9]{{{KEY_ID_LENGTH}}}_[0-9a-f]{{{SECRET_LENGTH}}}", key.value
    )
    assert key.key_id in key.value


def test_the_secret_comes_from_32_random_bytes() -> None:
    first = generate_key(ENV, new_id())
    second = generate_key(ENV, new_id())

    secret = first.value.rsplit("_", 1)[1]
    assert len(bytes.fromhex(secret)) == 32
    assert secret != second.value.rsplit("_", 1)[1]


def test_the_digest_is_the_sha256_of_the_full_key() -> None:
    key = generate_key(ENV, new_id())

    expected = hashlib.sha256(key.value.encode("ascii")).hexdigest()
    assert key.digest == expected == key_digest(key.value)


def test_every_generated_id_fits_the_key_id_alphabet() -> None:
    for _ in range(300):
        assert re.fullmatch(rf"[a-z0-9]{{{KEY_ID_LENGTH}}}", new_id())


def test_a_generated_key_parses_back_to_its_id_and_digest() -> None:
    key = generate_key(ENV, new_id())

    parsed = parse_key(key.value, ENV)

    assert parsed is not None
    assert parsed.key_id == key.key_id
    assert parsed.digest == key.digest


def test_the_repr_of_a_key_never_shows_the_value() -> None:
    key = generate_key(ENV, new_id())
    parsed = parse_key(key.value, ENV)

    assert key.value not in repr(key)
    assert key.digest not in repr(key)
    assert parsed is not None
    assert key.digest not in repr(parsed)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda k: k + "0",
        lambda k: k[:-1],
        lambda k: k.upper(),
        lambda k: " " + k,
        lambda k: k + "\n",
        lambda k: k.replace("fj_", "fk_", 1),
        lambda k: k.replace("_test_", "_prod_", 1),
        lambda k: k.replace("_test_", "_", 1),
        lambda k: k[:-1] + "g",
        lambda k: k[:-1] + "é",
        lambda k: k[:-4] + "_" + k[-3:],
        lambda k: "",
        lambda k: "Bearer " + k,
    ],
)
def test_a_malformed_key_does_not_parse(mutate: Callable[[str], str]) -> None:
    key = generate_key(ENV, new_id())

    assert parse_key(mutate(key.value), ENV) is None


def test_a_key_for_another_environment_does_not_parse() -> None:
    key = generate_key("production", new_id())

    assert parse_key(key.value, "test") is None
    assert parse_key(key.value, "production") is not None


def test_an_over_long_value_is_refused_before_matching() -> None:
    assert parse_key("fj_test_" + "a" * 100_000, ENV) is None


def test_generating_a_key_with_an_unwritable_environment_or_id_is_an_error() -> None:
    with pytest.raises(ValueError):
        generate_key("my_env", new_id())
    with pytest.raises(ValueError):
        generate_key(ENV, "short")


def test_a_matching_digest_is_accepted() -> None:
    key = generate_key(ENV, new_id())

    assert key_matches(key.digest, key.digest) is True


def test_a_different_digest_is_refused() -> None:
    first = generate_key(ENV, new_id())
    second = generate_key(ENV, new_id())

    assert key_matches(first.digest, second.digest) is False


def test_an_unknown_key_id_is_refused_after_a_dummy_comparison() -> None:
    key = generate_key(ENV, new_id())

    assert key_matches(key.digest, None) is False
    assert re.fullmatch(r"[0-9a-f]{64}", DUMMY_DIGEST)
    assert key.digest != DUMMY_DIGEST


@pytest.mark.parametrize(
    ("vercel_env", "environment", "expected"),
    [
        (None, "test", "test"),
        (None, "Production", "production"),
        ("preview", "production", "preview"),
        (None, "my_env", None),
        (None, "", None),
        (None, "x" * 40, None),
    ],
)
def test_the_running_environment_is_the_vercel_one_else_the_configured_one(
    vercel_env: str | None, environment: str, expected: str | None
) -> None:
    assert running_environment(vercel_env, environment) == expected
