"""The secret scan's two shortcuts find exactly what the full scan finds (#456).

`scan_content_secrets` skips a vendor pattern when the text lacks the literal
that pattern cannot match without, and runs the key-based rules only from lines
holding a credential keyword. Both exist for speed; neither may change a result.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from repo2graph import security
from repo2graph.security import CONTENT_SECRET_PATTERNS, scan_content_secrets

SAMPLES = {
    "aws_access_key": "AKIA" + "ABCDEFGHIJKLMNOP",
    "github_fine_grained_pat": "github_pat_" + "a" * 30,
    "github_token": "ghs_" + "a" * 30,
    "gitlab_token": "glpat-" + "a" * 24,
    "slack_token": "xoxb-" + "1" * 20,
    "anthropic_key": "sk-ant-" + "a" * 30,
    "openai_key": "sk-" + "a" * 30,
    "google_key": "AIza" + "a" * 35,
    "google_oauth_client_secret": "GOCSPX-" + "a" * 24,
    "stripe_key": "rk_live_" + "a" * 24,
    "stripe_webhook_secret": "whsec_" + "a" * 24,
    "npm_token": "npm_" + "a" * 24,
    "pypi_token": "pypi-AgEIcHlwaS5vcmc" + "a" * 24,
    "huggingface_token": "hf_" + "a" * 24,
    "digitalocean_token": "dop_v1_" + "a" * 24,
    "shopify_token": "shpss_" + "a" * 24,
    "sendgrid_key": "SG." + "a" * 20 + "." + "b" * 20,
    "hashicorp_vault_token": "hvb." + "a" * 24,
    "azure_storage_sas_token": "sv=2022-11-02&sp=r&sig=" + "a" * 24,
    "slack_webhook_url": "https://hooks.slack.com/services/" + "A" * 24,
    "teams_webhook_url": "https://x.webhook.office.com/webhookb2/" + "a" * 24,
    "discord_webhook_url": "https://discord.com/api/webhooks/1/" + "a" * 24,
    "putty_private_key": "PuTTY-User-Key-File-3:",
    "jwt": "eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + ".c",
    "basic_auth_url": "https://user:pw@host",
}

ATOMS = [
    *SAMPLES.values(),
    "password", "Token", "api_key", "SECRET", "pwd", "dockerconfigjson", "key",
    " = ", "=", ": ", ":", "'", '"', "\n", "\n\n", " ", "\t", "-", "(", ")",
    "hunter2x9", "abc123def456", "${X}", "self._token", "_", ".",
]  # fmt: skip


def _full_scan(text, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(security, "_PATTERN_NEEDLES", {})
        m.setattr(security, "_keyword_matches", lambda p, t, w: list(p.finditer(t)))
        return scan_content_secrets(text)


@pytest.mark.parametrize("stype", sorted(security._PATTERN_NEEDLES))
def test_every_needle_belongs_to_a_pattern_and_its_sample_is_found(stype):
    assert stype in dict(CONTENT_SECRET_PATTERNS)
    found = {t for t, _s, _e in scan_content_secrets(f"x = {SAMPLES[stype]} \n")}
    assert stype in found, found


@settings(
    max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(st.lists(st.sampled_from(ATOMS), max_size=40).map("".join))
def test_shortcuts_find_what_the_full_scan_finds(monkeypatch, text):
    assert scan_content_secrets(text) == _full_scan(text, monkeypatch)
