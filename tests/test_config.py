import base64
import textwrap

import pytest

from app.config import Profile, ProfileStore, import_aws_cli_profiles
from app.errors import AppError


@pytest.fixture(autouse=True)
def fake_dpapi(monkeypatch):
    """Windows DPAPI 대신 base64 왕복으로 저장/로드 로직만 검증한다."""
    import app.config as config_module

    monkeypatch.setattr(config_module, "dpapi_encrypt", lambda data: base64.b64encode(data))
    monkeypatch.setattr(config_module, "dpapi_decrypt", lambda data: base64.b64decode(data))


def test_profile_normalizes_bucket_and_endpoint():
    profile = Profile(
        name="p1",
        access_key_id="AKIA",
        secret_access_key="secret",
        bucket="s3://toolhub-objectstorage",
        endpoint_url="http://10.169.148.36:10443/",
    )
    assert profile.bucket == "toolhub-objectstorage"
    assert profile.endpoint_url == "http://10.169.148.36:10443"


def test_profile_validate_missing_field_raises_e1002():
    profile = Profile(name="p1", access_key_id="", secret_access_key="s")
    with pytest.raises(AppError) as exc_info:
        profile.validate()
    assert exc_info.value.code == "E-1002"


def test_profile_store_save_and_load_roundtrip(tmp_path):
    config_path = tmp_path / "config.json"
    store = ProfileStore(config_path=config_path)
    profile = Profile(
        name="minio",
        access_key_id="AKIAABCDEFGH",
        secret_access_key="topsecret",
        endpoint_url="http://10.169.148.36:10443",
        bucket="toolhub-objectstorage",
    )
    store.upsert(profile)
    store.active_profile = "minio"
    store.save()

    # 디스크에 평문 시크릿이 남지 않아야 한다
    raw_text = config_path.read_text(encoding="utf-8")
    assert "topsecret" not in raw_text

    loaded = ProfileStore(config_path=config_path)
    loaded.load()
    active = loaded.get_active()
    assert active.name == "minio"
    assert active.secret_access_key == "topsecret"
    assert active.access_key_id == "AKIAABCDEFGH"


def test_profile_store_load_missing_file_is_empty(tmp_path):
    store = ProfileStore(config_path=tmp_path / "missing.json")
    store.load()
    assert store.profiles == []
    assert store.active_profile is None


def test_profile_store_get_active_no_profiles_raises_e1005(tmp_path):
    store = ProfileStore(config_path=tmp_path / "config.json")
    with pytest.raises(AppError) as exc_info:
        store.get_active()
    assert exc_info.value.code == "E-1005"


def test_import_aws_cli_profiles_reads_readonly(tmp_path):
    cred_file = tmp_path / "credentials"
    cred_file.write_text(
        textwrap.dedent(
            """
            [default]
            aws_access_key_id = AKIA1234
            aws_secret_access_key = secret1

            [work]
            aws_access_key_id = AKIA5678
            aws_secret_access_key = secret2
            """
        ).strip(),
        encoding="utf-8",
    )

    profiles = import_aws_cli_profiles(path=cred_file)

    assert {p.name for p in profiles} == {"default", "work"}
    before = cred_file.read_text(encoding="utf-8")
    assert before == cred_file.read_text(encoding="utf-8")  # 파일에 쓰지 않음 (읽기 전용)
