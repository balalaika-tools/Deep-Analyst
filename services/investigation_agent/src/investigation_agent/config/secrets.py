"""Process-specific secrets kept out of the non-secret settings model."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Self
from urllib.parse import SplitResult, urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_POSTGRES_SCHEMES = frozenset({"postgres", "postgresql", "postgresql+psycopg"})
SERVING_SECRET_FIELDS = frozenset({"AGENT_READER_DATABASE_URL", "AGENT_WRITER_DATABASE_URL"})
INITIALIZER_SECRET_FIELDS = frozenset(
    {"AGENT_OWNER_DATABASE_URL", "AGENT_READER_PASSWORD", "AGENT_WRITER_PASSWORD"}
)
_SECRET_NAME_PREFIX = "AGENT_"
_DOTENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)$")


class SecretsError(RuntimeError):
    """Secret configuration is invalid; messages identify fields but never values."""


class _SecretSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
        env_ignore_empty=True,
    )


def _dotenv_paths(model: type[_SecretSettings]) -> tuple[Path, ...]:
    configured = model.model_config.get("env_file")
    if configured is None:
        return ()
    if isinstance(configured, (str, os.PathLike)):
        return (Path(configured),)
    if isinstance(configured, Iterable):
        return tuple(Path(item) for item in configured)
    return ()


def _nonempty_dotenv_fields(paths: tuple[Path, ...]) -> set[str]:
    """Non-empty ``AGENT_*`` assignments; other names are Settings, not secrets."""

    names: set[str] = set()
    for path in paths:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            match = _DOTENV_ASSIGNMENT.match(line)
            if not match or not match.group(1).startswith(_SECRET_NAME_PREFIX):
                continue
            if match.group(2).strip().strip("'\""):
                names.add(match.group(1))
    return names


def _reject_unexpected_secret_fields(
    *,
    process: str,
    model: type[_SecretSettings],
    allowed: frozenset[str],
) -> None:
    environment_names = {
        name for name, value in os.environ.items() if name.startswith(_SECRET_NAME_PREFIX) and value
    }
    source_names = environment_names | _nonempty_dotenv_fields(_dotenv_paths(model))
    unexpected = sorted(source_names - allowed)
    if unexpected:
        raise SecretsError(
            f"invalid {process} secrets: unexpected or misplaced fields: {', '.join(unexpected)}"
        )


def _parse_postgres_dsn(value: SecretStr) -> SplitResult:
    raw = value.get_secret_value()
    try:
        parsed = urlsplit(raw)
        valid = bool(
            parsed.scheme in _POSTGRES_SCHEMES
            and parsed.username
            and parsed.password
            and parsed.hostname
            and parsed.path.strip("/")
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("must be a PostgreSQL DSN with user, password, host, and database")
    return parsed


class ServingSecrets(_SecretSettings):
    """The only credentials available to the long-running serving process."""

    reader_database_url: SecretStr = Field(alias="AGENT_READER_DATABASE_URL")
    writer_database_url: SecretStr = Field(alias="AGENT_WRITER_DATABASE_URL")

    @field_validator("reader_database_url")
    @classmethod
    def _reader_dsn_has_reader_role(cls, value: SecretStr) -> SecretStr:
        if _parse_postgres_dsn(value).username != "agent_reader":
            raise ValueError("must use the agent_reader database role")
        return value

    @field_validator("writer_database_url")
    @classmethod
    def _writer_dsn_has_writer_role(cls, value: SecretStr) -> SecretStr:
        if _parse_postgres_dsn(value).username != "agent_writer":
            raise ValueError("must use the agent_writer database role")
        return value


class InitializerSecrets(_SecretSettings):
    """Owner authority and new-role passwords available only to ``agent-db-init``."""

    owner_database_url: SecretStr = Field(alias="AGENT_OWNER_DATABASE_URL")
    reader_password: SecretStr = Field(alias="AGENT_READER_PASSWORD")
    writer_password: SecretStr = Field(alias="AGENT_WRITER_PASSWORD")

    @field_validator("owner_database_url")
    @classmethod
    def _owner_dsn_is_not_a_runtime_role(cls, value: SecretStr) -> SecretStr:
        if _parse_postgres_dsn(value).username in {"agent_reader", "agent_writer"}:
            raise ValueError("must use the application database owner role")
        return value

    @field_validator("reader_password", "writer_password")
    @classmethod
    def _password_has_minimum_length(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 16:
            raise ValueError("must contain at least 16 characters")
        return value

    @model_validator(mode="after")
    def _role_passwords_are_distinct(self) -> Self:
        if self.reader_password.get_secret_value() == self.writer_password.get_secret_value():
            raise ValueError("AGENT_READER_PASSWORD and AGENT_WRITER_PASSWORD must be distinct")
        return self


def _safe_validation_message(process: str, exc: ValidationError) -> str:
    aliases = {
        name: str(field.alias)
        for model in (ServingSecrets, InitializerSecrets)
        for name, field in model.model_fields.items()
    }
    problems: list[str] = []
    for error in exc.errors(include_input=False):
        location = ".".join(aliases.get(str(part), str(part)) for part in error["loc"])
        problems.append(f"{location}: {error['msg']}")
    return f"invalid {process} secrets: {', '.join(problems)}"


def load_serving_secrets() -> ServingSecrets:
    """Load only the reader and writer DSNs used by the API process."""

    _reject_unexpected_secret_fields(
        process="serving",
        model=ServingSecrets,
        allowed=SERVING_SECRET_FIELDS,
    )
    try:
        return ServingSecrets()
    except ValidationError as exc:
        raise SecretsError(_safe_validation_message("serving", exc)) from exc


def load_initializer_secrets() -> InitializerSecrets:
    """Load only the owner DSN and role passwords used by the initializer process."""

    _reject_unexpected_secret_fields(
        process="initializer",
        model=InitializerSecrets,
        allowed=INITIALIZER_SECRET_FIELDS,
    )
    try:
        return InitializerSecrets()
    except ValidationError as exc:
        raise SecretsError(_safe_validation_message("initializer", exc)) from exc


__all__ = [
    "InitializerSecrets",
    "INITIALIZER_SECRET_FIELDS",
    "SecretsError",
    "SERVING_SECRET_FIELDS",
    "ServingSecrets",
    "load_initializer_secrets",
    "load_serving_secrets",
]
