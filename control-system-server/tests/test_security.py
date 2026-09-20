"""Secret hashing, IP resolution and allowlist behaviour."""

from __future__ import annotations

import ipaddress

import pytest

from app import security
from app.middleware_ops import _ip_allowed, client_ip


class TestClientSecretHashing:
    def test_hash_is_salted(self):
        a = security.hash_client_secret("s3cret-value")
        b = security.hash_client_secret("s3cret-value")
        assert a != b, "identical secrets must not produce identical hashes"
        assert a.startswith("pbkdf2_sha256$")

    def test_verify_round_trip(self):
        stored = security.hash_client_secret("correct-horse-battery")
        assert security.verify_client_secret("correct-horse-battery", stored) is True
        assert security.verify_client_secret("wrong", stored) is False

    def test_legacy_sha256_hashes_still_verify(self):
        """Existing clients were stored as bare SHA256; they must keep working
        across the upgrade rather than being locked out."""
        import hashlib

        legacy = hashlib.sha256("legacy-secret".encode()).hexdigest()
        assert security.verify_client_secret("legacy-secret", legacy) is True
        assert security.verify_client_secret("nope", legacy) is False

    def test_legacy_hashes_are_flagged_for_rehash(self):
        import hashlib

        legacy = hashlib.sha256("legacy-secret".encode()).hexdigest()
        assert security.client_secret_needs_rehash(legacy) is True
        assert (
            security.client_secret_needs_rehash(security.hash_client_secret("x")) is False
        )

    def test_empty_and_malformed_stored_values_do_not_authenticate(self):
        for stored in ("", "not-a-hash", "pbkdf2_sha256$bad", "pbkdf2_sha256$a$b$c"):
            assert security.verify_client_secret("anything", stored) is False


class TestSecretFingerprint:
    def test_short_secrets_do_not_leak_their_tail(self):
        """A 6-char secret must not have 4 of its characters printed in logs."""
        fp = security.secret_fingerprint("abc123")
        assert "abc123" not in fp
        assert not fp.endswith("c123")

    def test_long_secrets_show_a_recognisable_tail(self):
        fp = security.secret_fingerprint("sk_live_0123456789abcdefXYZW")
        assert fp.endswith("XYZW")


class _FakeRequest:
    def __init__(self, peer: str, forwarded: str | None = None):
        self.headers = {"x-forwarded-for": forwarded} if forwarded else {}
        self.client = type("C", (), {"host": peer})()


class TestClientIpResolution:
    def test_forwarded_header_ignored_without_trusted_proxies(self):
        """Trusting XFF unconditionally let any caller spoof the allowlist and
        their own rate-limit bucket."""
        req = _FakeRequest("10.0.0.5", forwarded="1.2.3.4")
        assert client_ip(req, trusted_proxy_count=0) == "10.0.0.5"

    def test_single_trusted_proxy_reads_the_last_hop(self):
        req = _FakeRequest("10.0.0.5", forwarded="1.2.3.4, 203.0.113.9")
        assert client_ip(req, trusted_proxy_count=1) == "203.0.113.9"

    def test_spoofed_prefix_cannot_displace_the_real_client(self):
        # Attacker sends "X-Forwarded-For: 127.0.0.1"; our proxy appends theirs.
        req = _FakeRequest("10.0.0.5", forwarded="127.0.0.1, 198.51.100.7")
        assert client_ip(req, trusted_proxy_count=1) == "198.51.100.7"

    def test_two_trusted_proxies(self):
        req = _FakeRequest(
            "10.0.0.5", forwarded="127.0.0.1, 198.51.100.7, 203.0.113.9"
        )
        assert client_ip(req, trusted_proxy_count=2) == "198.51.100.7"

    def test_falls_back_to_peer_when_header_absent(self):
        assert client_ip(_FakeRequest("10.0.0.5"), trusted_proxy_count=2) == "10.0.0.5"

    def test_more_trusted_proxies_than_hops_does_not_index_out_of_range(self):
        req = _FakeRequest("10.0.0.5", forwarded="203.0.113.9")
        assert client_ip(req, trusted_proxy_count=5) == "203.0.113.9"


class TestIpAllowlist:
    def test_empty_allowlist_matches_nothing(self):
        """Fail closed: an enforced-but-empty allowlist used to skip the check
        entirely, so deleting the last entry silently disabled enforcement."""
        assert _ip_allowed("203.0.113.9", []) is False

    def test_exact_and_cidr_matches(self):
        assert _ip_allowed("203.0.113.9", ["203.0.113.9/32"]) is True
        assert _ip_allowed("203.0.113.9", ["203.0.113.0/24"]) is True
        assert _ip_allowed("203.0.114.9", ["203.0.113.0/24"]) is False

    def test_malformed_entries_are_skipped_not_fatal(self):
        assert _ip_allowed("203.0.113.9", ["not-a-cidr", "203.0.113.0/24"]) is True

    def test_unparseable_client_ip_is_denied(self):
        assert _ip_allowed("", ["0.0.0.0/0"]) is False
        assert _ip_allowed("garbage", ["0.0.0.0/0"]) is False

    def test_ipv6(self):
        assert _ip_allowed("2001:db8::1", ["2001:db8::/32"]) is True


class TestRateLimiter:
    def test_allows_up_to_limit_then_blocks(self):
        from app.services.rate_limit import check_rate_limit, reset_buckets

        reset_buckets()
        for _ in range(5):
            allowed, _ = check_rate_limit("test:bucket", 5)
            assert allowed is True
        allowed, remaining = check_rate_limit("test:bucket", 5)
        assert allowed is False
        assert remaining == 0

    def test_buckets_are_independent(self):
        from app.services.rate_limit import check_rate_limit, reset_buckets

        reset_buckets()
        for _ in range(3):
            check_rate_limit("a", 3)
        allowed, _ = check_rate_limit("b", 3)
        assert allowed is True

    def test_token_endpoint_gets_the_tighter_auth_budget(self):
        """A leaked client_id should not be brute-forceable at the general m2m
        rate of hundreds of attempts per minute."""
        from app.services.rate_limit import budget_for_path

        token_budget, token_limit = budget_for_path("/internal/v1/token")
        m2m_budget, m2m_limit = budget_for_path("/internal/v1/tenants/x/runtime-config")
        assert token_budget == "m2m_token"
        assert m2m_budget == "m2m"
        assert token_limit <= m2m_limit

    def test_login_uses_auth_budget(self):
        from app.services.rate_limit import budget_for_path

        assert budget_for_path("/v1/auth/login")[0] == "auth"
        assert budget_for_path("/v1/tenants")[0] == "staff"


class TestProductionConfigValidation:
    """Production must refuse to boot on example secrets rather than run insecurely."""

    def _settings(self, **overrides):
        from app.config import Settings

        base = dict(
            environment="production",
            debug=False,
            redis_url="redis://redis:6379",
            jwt_secret="a-genuinely-random-production-secret-value",
            secrets_encryption_key="Zm9vYmFyYmF6cXV1eGZvb2JhcmJhenF1dXhmb28=",
            database_url="postgresql+psycopg://user:pw@db/control",
            cors_origins=["https://control.example.com"],
            # Every value the validator screens has to be overridden or the
            # ambient .env supplies the example one.
            bootstrap_owner_password="a-real-owner-password-9421",
            bootstrap_m2m_client_secret="a-real-m2m-client-secret-8830",
            nest_webhook_secret="a-real-webhook-secret-7261",
        )
        base.update(overrides)
        return Settings(**base)

    def test_valid_production_config_boots(self):
        assert self._settings().environment == "production"

    def test_example_jwt_secret_is_rejected(self):
        from app.config import INSECURE_DEFAULTS

        with pytest.raises(Exception, match="insecure configuration"):
            self._settings(jwt_secret=INSECURE_DEFAULTS["jwt_secret"])

    def test_published_env_example_jwt_is_rejected(self):
        """An older .env.example shipped a different 37-char secret that satisfied
        length and 'not the code default' checks — operators who only rotated the
        three flagged values still went live with a publicly known signing key."""
        with pytest.raises(Exception, match="JWT_SECRET|published example"):
            self._settings(jwt_secret="dev-control-jwt-secret-min-32-bytes!!")

    def test_missing_encryption_key_is_rejected(self):
        with pytest.raises(Exception, match="SECRETS_ENCRYPTION_KEY"):
            self._settings(secrets_encryption_key="")

    def test_short_jwt_secret_is_rejected(self):
        with pytest.raises(Exception, match="at least 32"):
            self._settings(jwt_secret="tooshort")

    def test_sqlite_in_production_is_rejected(self):
        with pytest.raises(Exception, match="SQLite"):
            self._settings(database_url="sqlite:///./control.db")

    def test_wildcard_cors_is_rejected(self):
        with pytest.raises(Exception, match="wildcard"):
            self._settings(cors_origins=["*"])

    def test_plaintext_http_origin_is_rejected(self):
        with pytest.raises(Exception, match="http://"):
            self._settings(cors_origins=["http://control.example.com"])

    def test_development_is_left_alone(self):
        from app.config import INSECURE_DEFAULTS, Settings

        s = Settings(
            environment="development",
            jwt_secret=INSECURE_DEFAULTS["jwt_secret"],
            secrets_encryption_key="",
            database_url="sqlite:///./control.db",
        )
        assert s.environment == "development"
