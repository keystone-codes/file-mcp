"""Bucket lifecycle for ``uploads/``.

The prefix rule is the floor for objects that have no shorter tag rule.
A tag rule of ``retention-days=<N>`` expires that object after N days.
If N is longer than the prefix rule, the prefix is raised so it does not
delete the object first. A provider that rejects lifecycle configuration
does not stop the process.
"""

from __future__ import annotations

import logging

from botocore.exceptions import BotoCoreError, ClientError

from file_mcp.s3util import error_code

_LOG = logging.getLogger("file_mcp.lifecycle")

PREFIX_RULE_ID = "file-mcp-uploads-expiry"
RETENTION_TAG = "retention-days"
_MISSING_LIFECYCLE = frozenset({"NoSuchLifecycleConfiguration"})
_WARN = "files under uploads/ will not expire automatically; configure expiry on the bucket by hand"


def ensure_upload_expiry(client, bucket: str, days: int) -> bool:
    """Install the prefix rule. Return False when lifecycle cannot be written."""
    try:
        rules = _load_rules(client, bucket)
    except ClientError as exc:
        if error_code(exc) in _MISSING_LIFECYCLE:
            rules = []
        else:
            _warn(exc)
            return False
    except (BotoCoreError, NotImplementedError) as exc:
        _warn(exc)
        return False
    if any(_prefix_matches(rule, days) for rule in rules):
        return True
    kept = [rule for rule in rules if rule.get("ID") != PREFIX_RULE_ID]
    kept.append(_prefix_rule(days))
    return _write_rules(client, bucket, kept)


def ensure_object_retention(client, bucket: str, days: int) -> bool:
    """Add the tag rule for ``days`` and raise the prefix floor when needed."""
    try:
        rules = _load_rules(client, bucket)
    except ClientError as exc:
        if error_code(exc) in _MISSING_LIFECYCLE:
            rules = []
        else:
            _warn(exc)
            return False
    except (BotoCoreError, NotImplementedError) as exc:
        _warn(exc)
        return False
    updated = _with_retention(rules, days)
    if updated == rules:
        return True
    return _write_rules(client, bucket, updated)


def _load_rules(client, bucket: str) -> list[dict]:
    response = client.get_bucket_lifecycle_configuration(Bucket=bucket)
    rules = response.get("Rules") or []
    return list(rules)


def _write_rules(client, bucket: str, rules: list[dict]) -> bool:
    try:
        client.put_bucket_lifecycle_configuration(
            Bucket=bucket,
            LifecycleConfiguration={"Rules": rules},
        )
    except (ClientError, BotoCoreError, NotImplementedError) as exc:
        _warn(exc)
        return False
    return True


def _with_retention(rules: list[dict], days: int) -> list[dict]:
    tag_id = f"file-mcp-retention-{days}"
    found = False
    updated: list[dict] = []
    for rule in rules:
        if rule.get("ID") == tag_id:
            found = True
            updated.append(rule if _tag_matches(rule, days) else _tag_rule(days))
            continue
        if rule.get("ID") == PREFIX_RULE_ID:
            current = (rule.get("Expiration") or {}).get("Days")
            if isinstance(current, int) and not isinstance(current, bool) and current < days:
                updated.append(_prefix_rule(days))
            else:
                updated.append(rule)
            continue
        updated.append(rule)
    if not found:
        updated.append(_tag_rule(days))
    return updated


def _prefix_rule(days: int) -> dict:
    return {
        "ID": PREFIX_RULE_ID,
        "Filter": {"Prefix": "uploads/"},
        "Status": "Enabled",
        "Expiration": {"Days": days},
        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
    }


def _tag_rule(days: int) -> dict:
    return {
        "ID": f"file-mcp-retention-{days}",
        "Filter": {"Tag": {"Key": RETENTION_TAG, "Value": str(days)}},
        "Status": "Enabled",
        "Expiration": {"Days": days},
    }


def _prefix_matches(rule: dict, days: int) -> bool:
    if rule.get("ID") != PREFIX_RULE_ID or rule.get("Status") != "Enabled":
        return False
    filt = rule.get("Filter") or {}
    if filt.get("Prefix") != "uploads/":
        return False
    if (rule.get("Expiration") or {}).get("Days") != days:
        return False
    abort = rule.get("AbortIncompleteMultipartUpload") or {}
    return abort.get("DaysAfterInitiation") == 1


def _tag_matches(rule: dict, days: int) -> bool:
    if rule.get("Status") != "Enabled":
        return False
    if (rule.get("Expiration") or {}).get("Days") != days:
        return False
    tag = (rule.get("Filter") or {}).get("Tag") or {}
    return tag.get("Key") == RETENTION_TAG and tag.get("Value") == str(days)


def _warn(exc: BaseException) -> None:
    extra: dict[str, object] = {"event": "lifecycle_unsupported"}
    if isinstance(exc, ClientError):
        extra["error_code"] = error_code(exc)
    _LOG.warning(_WARN, extra=extra)
