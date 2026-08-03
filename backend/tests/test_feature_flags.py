from __future__ import annotations

from backend.app.feature_flags import decide_feature


def test_production_defaults_fail_closed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("FEATURE_CAREER_WORKSPACE_ENABLED", raising=False)
    monkeypatch.delenv("FEATURE_CAREER_WORKSPACE_ROLLOUT_PERCENT", raising=False)

    decision = decide_feature(
        "career_workspace",
        user_id=1,
        email="owner@example.com",
    )

    assert decision.enabled is False
    assert decision.rollout_percent == 0
    assert decision.reason == "kill_switch"


def test_feature_kill_switch_overrides_internal_access(monkeypatch):
    monkeypatch.setenv("FEATURE_CAREER_WORKSPACE_ENABLED", "false")
    monkeypatch.setenv("FEATURE_INTERNAL_EMAILS", "owner@example.com")

    decision = decide_feature(
        "career_workspace",
        user_id=1,
        email="owner@example.com",
    )

    assert decision.enabled is False
    assert decision.reason == "kill_switch"


def test_internal_and_invited_users_can_enter_before_percentage_rollout(monkeypatch):
    monkeypatch.setenv("FEATURE_CAREER_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("FEATURE_CAREER_WORKSPACE_ROLLOUT_PERCENT", "0")
    monkeypatch.setenv("FEATURE_INTERNAL_EMAILS", "internal@example.com")
    monkeypatch.setenv("FEATURE_CAREER_WORKSPACE_USER_IDS", "42")

    assert decide_feature(
        "career_workspace", user_id=1, email="internal@example.com"
    ).enabled
    assert decide_feature(
        "career_workspace", user_id=42, email="invited@example.com"
    ).enabled
    assert not decide_feature(
        "career_workspace", user_id=2, email="other@example.com"
    ).enabled


def test_percentage_bucket_is_stable(monkeypatch):
    monkeypatch.setenv("FEATURE_ASYNC_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("FEATURE_ASYNC_ANALYSIS_ROLLOUT_PERCENT", "25")

    first = decide_feature("async_analysis", user_id=99, email="user@example.com")
    second = decide_feature("async_analysis", user_id=99, email="user@example.com")

    assert first.bucket == second.bucket
    assert first.enabled == second.enabled
    assert first.rollout_percent == 25
