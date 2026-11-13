"""Unit tests for YouTube uploadLimitExceeded detection (stop-queue)."""

from agents.auto_publisher import _is_yt_upload_limit_error


def test_is_yt_upload_limit_error_reason_code():
    assert _is_yt_upload_limit_error(
        '<HttpError 400 when requesting ... returned "The user has exceeded the number '
        'of videos they may upload.". Details: "[{\'message\': \'The user has exceeded '
        "the number of videos they may upload.\", 'domain': 'youtube.video', "
        "'reason': 'uploadLimitExceeded'}]\">"
    )


def test_is_yt_upload_limit_error_reason_token():
    assert _is_yt_upload_limit_error("HttpError 400: uploadLimitExceeded")


def test_is_yt_upload_limit_error_generic_words():
    assert _is_yt_upload_limit_error("Upload limit exceeded for this channel")


def test_is_yt_upload_limit_error_negatives():
    assert not _is_yt_upload_limit_error(None)
    assert not _is_yt_upload_limit_error("")
    assert not _is_yt_upload_limit_error("quotaExceeded for youtube.readonly")
    assert not _is_yt_upload_limit_error("network timeout while uploading")
