"""Upload expiry rules."""

from __future__ import annotations

import logging

import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from tests.support.env import load_required

from file_mcp.lifecycle import ensure_object_retention, ensure_upload_expiry
from file_mcp.s3 import make_s3_client


def _client(settings):
    client = make_s3_client(settings)
    client.create_bucket(Bucket=settings.s3_bucket)
    return client


def _rules(client, bucket: str) -> list[dict]:
    return client.get_bucket_lifecycle_configuration(Bucket=bucket)["Rules"]


def test_rule_added_and_foreign_rules_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = _client(settings)
        client.put_bucket_lifecycle_configuration(
            Bucket=settings.s3_bucket,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "logs",
                        "Filter": {"Prefix": "logs/"},
                        "Status": "Enabled",
                        "Expiration": {"Days": 7},
                    }
                ]
            },
        )
        assert ensure_upload_expiry(client, settings.s3_bucket, 30) is True
        rules = {rule["ID"]: rule for rule in _rules(client, settings.s3_bucket)}
    assert set(rules) == {"logs", "file-mcp-uploads-expiry"}
    assert rules["file-mcp-uploads-expiry"]["Expiration"]["Days"] == 30
    assert rules["file-mcp-uploads-expiry"]["Filter"] == {"Prefix": "uploads/"}
    assert rules["logs"]["Expiration"]["Days"] == 7


def test_rule_updated_when_days_change(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = _client(settings)
        assert ensure_upload_expiry(client, settings.s3_bucket, 30) is True
        assert ensure_upload_expiry(client, settings.s3_bucket, 14) is True
        rule = next(
            item
            for item in _rules(client, settings.s3_bucket)
            if item["ID"] == "file-mcp-uploads-expiry"
        )
    assert rule["Expiration"]["Days"] == 14
    assert rule["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"] == 1


def test_restart_keeps_prefix_raised_for_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = _client(settings)
        client.put_bucket_lifecycle_configuration(
            Bucket=settings.s3_bucket,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "logs",
                        "Filter": {"Prefix": "logs/"},
                        "Status": "Enabled",
                        "Expiration": {"Days": 3},
                    }
                ]
            },
        )
        assert ensure_upload_expiry(client, settings.s3_bucket, 30) is True
        assert ensure_object_retention(client, settings.s3_bucket, 90) is True
        assert ensure_upload_expiry(client, settings.s3_bucket, 30) is True
        rules = {rule["ID"]: rule for rule in _rules(client, settings.s3_bucket)}
        assert rules["logs"]["Expiration"]["Days"] == 3
        assert rules["file-mcp-uploads-expiry"]["Expiration"]["Days"] == 90
        assert rules["file-mcp-retention-90"]["Expiration"]["Days"] == 90
        assert ensure_upload_expiry(client, settings.s3_bucket, 120) is True
        rules = {rule["ID"]: rule for rule in _rules(client, settings.s3_bucket)}
    assert rules["logs"]["Expiration"]["Days"] == 3
    assert rules["file-mcp-uploads-expiry"]["Expiration"]["Days"] == 120
    assert rules["file-mcp-retention-90"]["Expiration"]["Days"] == 90


def test_no_write_when_rule_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = _client(settings)
        writes = {"n": 0}
        original = client.put_bucket_lifecycle_configuration

        def counting(**kwargs):
            writes["n"] += 1
            return original(**kwargs)

        client.put_bucket_lifecycle_configuration = counting
        assert ensure_upload_expiry(client, settings.s3_bucket, 30) is True
        assert ensure_upload_expiry(client, settings.s3_bucket, 30) is True
    assert writes["n"] == 1


def test_unsupported_lifecycle_only_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class _Unsupported:
        def get_bucket_lifecycle_configuration(self, **_kwargs: object) -> None:
            raise ClientError(
                {
                    "Error": {"Code": "NotImplemented", "Message": "no"},
                    "ResponseMetadata": {"HTTPStatusCode": 501},
                },
                "GetBucketLifecycleConfiguration",
            )

    caplog.set_level(logging.WARNING)
    assert ensure_upload_expiry(_Unsupported(), "file-mcp", 30) is False
    matched = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "lifecycle_unsupported"
    ]
    assert matched
    assert "will not expire automatically" in matched[0].getMessage()


def test_shorter_retention_adds_tag_rule_without_lowering_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = _client(settings)
        ensure_upload_expiry(client, settings.s3_bucket, 30)
        assert ensure_object_retention(client, settings.s3_bucket, 7) is True
        rules = {rule["ID"]: rule for rule in _rules(client, settings.s3_bucket)}
    assert rules["file-mcp-uploads-expiry"]["Expiration"]["Days"] == 30
    assert rules["file-mcp-retention-7"]["Expiration"]["Days"] == 7
    assert rules["file-mcp-retention-7"]["Filter"]["Tag"]["Value"] == "7"


def test_longer_retention_raises_prefix_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_required(monkeypatch)
    with mock_aws():
        client = _client(settings)
        client.put_bucket_lifecycle_configuration(
            Bucket=settings.s3_bucket,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "logs",
                        "Filter": {"Prefix": "logs/"},
                        "Status": "Enabled",
                        "Expiration": {"Days": 3},
                    }
                ]
            },
        )
        ensure_upload_expiry(client, settings.s3_bucket, 30)
        assert ensure_object_retention(client, settings.s3_bucket, 90) is True
        rules = {rule["ID"]: rule for rule in _rules(client, settings.s3_bucket)}
    assert rules["logs"]["Expiration"]["Days"] == 3
    assert rules["file-mcp-uploads-expiry"]["Expiration"]["Days"] == 90
    assert rules["file-mcp-retention-90"]["Expiration"]["Days"] == 90
