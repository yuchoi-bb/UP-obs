from botocore.exceptions import ClientError, EndpointConnectionError

from app.errors import AppError, log_error, map_botocore_exception, mask_access_key


def test_app_error_unknown_code_falls_back_to_9001():
    err = AppError("E-9999")
    assert err.code == "E-9001"
    assert err.display_text() == "[E-9001] 처리되지 않은 오류가 발생했습니다"


def test_mask_access_key():
    assert mask_access_key("AKIAABCDEFGH") == "AKIA****"
    assert mask_access_key("") == ""


def test_map_botocore_exception_client_error_known_code():
    exc = ClientError(
        {"Error": {"Code": "NoSuchBucket", "Message": "x"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
        "ListObjects",
    )
    assert map_botocore_exception(exc) == "E-3001"


def test_map_botocore_exception_client_error_404_no_such_key():
    exc = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "x"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
        "GetObject",
    )
    assert map_botocore_exception(exc) == "E-3007"


def test_map_botocore_exception_connection_error():
    exc = EndpointConnectionError(endpoint_url="http://10.0.0.1")
    assert map_botocore_exception(exc) == "E-2001"


def test_map_botocore_exception_unknown_falls_back():
    assert map_botocore_exception(ValueError("boom")) == "E-9001"


def test_log_error_writes_expected_format(tmp_path, monkeypatch):
    import app.errors as errors_module

    monkeypatch.setattr(errors_module, "_logger", None)
    monkeypatch.setattr(errors_module, "get_log_dir", lambda: tmp_path)

    log_error("E-2002", detail="cert verify failed", req_id="abc123")

    log_file = tmp_path / "s3explorer.log"
    content = log_file.read_text(encoding="utf-8")
    assert "ERROR | E-2002 | SSL 인증서 검증에 실패했습니다 | detail=cert verify failed | req_id=abc123" in content
