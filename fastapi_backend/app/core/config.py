from typing import Annotated
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.env_file import settings_env_file
from app.core.totp import TotpCodec, TotpUnavailable

LOCAL_ENVIRONMENTS = frozenset({"local", "test"})
VERCEL_NON_PRODUCTION = frozenset({"preview", "development"})
EMAIL_PROVIDERS = frozenset({"", "brevo", "fake"})


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "Freejoy API"
    APP_VERSION: str = "0.1.0"
    LOG_LEVEL: str = "INFO"

    # OpenAPI docs
    OPENAPI_URL: str = "/openapi.json"
    # Testing phase only (FREEJ-48, ADR-003): serve /docs, /redoc and the schema in production too.
    # Relaxes nothing else. Remove the variable at go-live.
    API_DOCS_ENABLED: bool = False

    # Partner API (ADR-013). Off by default: while false every path under /partner/v1 answers the
    # same 404 as an unknown path. Nothing is served there yet; this is the switch for later stories.
    PARTNER_API_ENABLED: bool = False

    # Database
    DATABASE_URL: str
    EXPIRE_ON_COMMIT: bool = False

    # Deployment environment. Defaults to "production" so a deployment that forgets
    # to set it gets the strict checks. Only "local" and "test" are relaxed.
    ENVIRONMENT: str = "production"
    # Set by Vercel itself (production, preview or development). When present it
    # wins: preview and development relax the rate limit guard, production is strict.
    VERCEL_ENV: str | None = None

    # Rate limiting (ADR-007). Optional only when ENVIRONMENT is local or test, where
    # unset means rate limiting is disabled. Any other environment must set both.
    UPSTASH_REDIS_REST_URL: str | None = None
    UPSTASH_REDIS_REST_TOKEN: SecretStr | None = None

    # Error reporting. Sentry is initialised only when SENTRY_DSN is set;
    # leave it unset locally and in tests. The release comes from Vercel.
    SENTRY_DSN: SecretStr | None = None
    VERCEL_GIT_COMMIT_SHA: str | None = None

    # ADR-003: separate server-only keys per environment; never NEXT_PUBLIC_*.
    BACKEND_INTERNAL_KEY: SecretStr | None = None
    BACKEND_INTERNAL_KEY_NEXT: SecretStr | None = None
    RATE_LIMIT_EMAIL_KEY: SecretStr | None = None
    FIREBASE_ADMIN_PROJECT_ID: str | None = None
    FIREBASE_ADMIN_CLIENT_EMAIL: str | None = None
    FIREBASE_ADMIN_PRIVATE_KEY: SecretStr | None = None
    # Tolerated clock drift (seconds) when verifying Firebase tokens and judging
    # re-authentication freshness. Out of range stops startup.
    FIREBASE_CLOCK_SKEW_SECONDS: Annotated[int, Field(ge=0, le=60)] = 10
    TOTP_ENCRYPTION_KEY: SecretStr | None = None
    # Name shown in the authenticator app next to the account (the brand, as on the website).
    TOTP_ISSUER: str = "Freejoy"

    # Stripe Billing (optional module). Unset webhook secret means the webhook answers 503.
    STRIPE_WEBHOOK_SECRET: SecretStr | None = None
    STRIPE_PRICE_ID_PRO_MONTHLY: str | None = None
    STRIPE_PRICE_ID_PRO_YEARLY: str | None = None
    STRIPE_PRICE_ID_ENTERPRISE_MONTHLY: str | None = None
    STRIPE_PRICE_ID_ENTERPRISE_YEARLY: str | None = None

    # Organisation billing endpoints. Unset secret key means they answer 503.
    STRIPE_SECRET_KEY: SecretStr | None = None
    # Comma separated origins (scheme://host[:port]) that Checkout and the customer
    # portal may return to. Unset means every return URL is rejected.
    BILLING_RETURN_URL_ORIGINS: str = ""

    # Alerts email (ADR-012). Backend only. EMAIL_PROVIDER unset means nothing is ever sent.
    # "brevo" is the real provider; "fake" records messages and is refused outside local/test.
    EMAIL_PROVIDER: str = ""
    EMAIL_PROVIDER_API_KEY: SecretStr | None = None
    EMAIL_PROVIDER_LIST_ID: str = ""
    EMAIL_FROM: str = ""
    EMAIL_FROM_NAME: str = "Freejoy"
    # Where the "fake" provider appends each message as a JSON line (local/test only).
    EMAIL_FAKE_OUTBOX: str = ""
    # Public origin of the website, used for the links in emails (no path).
    PUBLIC_SITE_URL: str = "http://localhost:3000"
    # HMAC secret for confirm and unsubscribe tokens. Required outside local/test.
    SUBSCRIBER_TOKEN_SECRET: SecretStr | None = None

    # Awin offers importer (docs/developer/backend-awin-import.md). Optional for the app; the
    # command refuses to run without both. The token is only ever sent in an Authorization header.
    AWIN_PUBLISHER_ID: Annotated[int, Field(gt=0)] | None = None
    AWIN_OAUTH_API_TOKEN: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file=settings_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        # A validation error must not echo the input values (they include secrets).
        hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def require_rate_limit_config_outside_local(self) -> "Settings":
        configured = bool(self.UPSTASH_REDIS_REST_URL and self.UPSTASH_REDIS_REST_TOKEN)
        if self.VERCEL_ENV:
            relaxed = self.VERCEL_ENV in VERCEL_NON_PRODUCTION
        else:
            relaxed = self.ENVIRONMENT in LOCAL_ENVIRONMENTS
        if not configured and not relaxed:
            raise ValueError(
                "UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN must be set "
                f"in production (ENVIRONMENT='{self.ENVIRONMENT}', "
                f"VERCEL_ENV='{self.VERCEL_ENV}'); rate limiting can only be disabled "
                "for local, test, Vercel preview and Vercel development"
            )
        return self

    @model_validator(mode="after")
    def validate_totp_config(self) -> "Settings":
        if self.TOTP_ENCRYPTION_KEY is not None:
            try:
                TotpCodec(self.TOTP_ENCRYPTION_KEY.get_secret_value())
            except TotpUnavailable:
                raise ValueError(
                    "TOTP_ENCRYPTION_KEY must be a valid Fernet key"
                ) from None
        return self

    @field_validator("SUBSCRIBER_TOKEN_SECRET", "EMAIL_PROVIDER_API_KEY", mode="before")
    @classmethod
    def blank_secret_is_unset(cls, value: object) -> object:
        """A blank value (``NAME=`` copied from .env.example) means "not set", never an empty secret."""
        return None if value == "" else value

    @field_validator("AWIN_PUBLISHER_ID", mode="before")
    @classmethod
    def blank_publisher_id_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("AWIN_OAUTH_API_TOKEN", mode="before")
    @classmethod
    def clean_awin_token(cls, value: object) -> object:
        """Blank means unset; otherwise the token must be usable as a header value as it stands."""
        if not isinstance(value, str):
            return value
        token = value.strip()
        if not token:
            return None
        if not all("!" <= char <= "~" for char in token):  # printable ASCII, no spaces
            raise ValueError(
                "AWIN_OAUTH_API_TOKEN must be printable ASCII without whitespace"
            )
        return token

    @model_validator(mode="after")
    def validate_email_config(self) -> "Settings":
        """Alerts email (ADR-012): a half-configured provider stops startup, not the first signup."""
        provider = self.EMAIL_PROVIDER
        if provider not in EMAIL_PROVIDERS:
            raise ValueError("EMAIL_PROVIDER must be unset, 'brevo' or 'fake'")
        relaxed = self.relaxed_environment
        site = urlsplit(self.PUBLIC_SITE_URL)
        if (
            site.scheme not in ("http", "https")
            or not site.netloc
            or site.path not in ("", "/")
            or site.query
            or site.fragment
        ):
            raise ValueError("PUBLIC_SITE_URL must be an origin such as https://host")
        secret = self.SUBSCRIBER_TOKEN_SECRET
        if secret is not None and len(secret.get_secret_value()) < 32:
            raise ValueError("SUBSCRIBER_TOKEN_SECRET must have at least 32 characters")
        if secret is None and not relaxed:
            raise ValueError("SUBSCRIBER_TOKEN_SECRET must be set in production")
        # ENVIRONMENT only: a Vercel preview must not be able to switch the fake provider on.
        if provider == "fake" and self.ENVIRONMENT not in LOCAL_ENVIRONMENTS:
            raise ValueError("EMAIL_PROVIDER=fake is only allowed in local and test")
        if provider == "brevo":
            if not (
                self.EMAIL_PROVIDER_API_KEY
                and self.EMAIL_FROM
                and self.EMAIL_PROVIDER_LIST_ID.isdigit()
            ):
                raise ValueError(
                    "EMAIL_PROVIDER=brevo requires EMAIL_PROVIDER_API_KEY, EMAIL_FROM "
                    "and a numeric EMAIL_PROVIDER_LIST_ID"
                )
            if site.scheme != "https" and not relaxed:
                raise ValueError("PUBLIC_SITE_URL must use https in production")
        return self

    @property
    def relaxed_environment(self) -> bool:
        """True for local/test (and Vercel preview/development), where checks relax."""
        if self.VERCEL_ENV:
            return self.VERCEL_ENV in VERCEL_NON_PRODUCTION
        return self.ENVIRONMENT in LOCAL_ENVIRONMENTS

    @model_validator(mode="after")
    def validate_auth_config(self) -> "Settings":
        for name in (
            "BACKEND_INTERNAL_KEY",
            "BACKEND_INTERNAL_KEY_NEXT",
            "RATE_LIMIT_EMAIL_KEY",
        ):
            key = getattr(self, name)
            if key is not None and len(key.get_secret_value()) < 32:
                raise ValueError(f"{name} must have at least 32 characters")
        relaxed = (
            self.VERCEL_ENV in VERCEL_NON_PRODUCTION
            if self.VERCEL_ENV
            else self.ENVIRONMENT in LOCAL_ENVIRONMENTS
        )
        required = (
            "BACKEND_INTERNAL_KEY",
            "RATE_LIMIT_EMAIL_KEY",
            "FIREBASE_ADMIN_PROJECT_ID",
            "FIREBASE_ADMIN_CLIENT_EMAIL",
            "FIREBASE_ADMIN_PRIVATE_KEY",
        )
        if not relaxed and any(not getattr(self, name) for name in required):
            raise ValueError(
                "Production authentication requires internal key, email HMAC key and Firebase Admin credentials"
            )
        return self


# Required field (DATABASE_URL) comes from the environment, so the
# app fails at startup when they are missing.
settings = Settings()  # type: ignore[call-arg]
