"""S3Client가 모든 오퍼레이션 진입점에서 guard()를 실제로 호출하는지 검증.

CLAUDE.md 원칙: "경로 검증(guard())를 우회하는 S3 호출을 만들지 말 것" —
버튼을 숨기는 것과 별개로, S3Client 자체가 마지막 방어선이어야 한다.
"""
import pytest
from moto import mock_aws

from app import permissions
from app.config import Profile
from app.errors import AppError
from app.s3client import S3Client

BUCKET = "toolhub-objectstorage"


@pytest.fixture(autouse=True)
def reset_permissions():
    permissions.disable_superuser()
    yield
    permissions.disable_superuser()


@pytest.fixture
def client():
    with mock_aws():
        profile = Profile(
            name="t", access_key_id="testing", secret_access_key="testing", region="us-east-1", bucket=BUCKET
        )
        c = S3Client(profile)
        c._client.create_bucket(Bucket=BUCKET)
        c._client.put_object(Bucket=BUCKET, Key="DA-share/ok.txt", Body=b"x")
        c._client.put_object(Bucket=BUCKET, Key="outside/secret.txt", Body=b"x")
        yield c


def test_list_objects_outside_da_share_blocked(client):
    with pytest.raises(AppError) as exc_info:
        list(client.list_objects(BUCKET, prefix="outside/"))
    assert exc_info.value.code == "E-3009"


def test_list_objects_bucket_root_blocked_in_normal_mode(client):
    with pytest.raises(AppError) as exc_info:
        list(client.list_objects(BUCKET))
    assert exc_info.value.code == "E-3009"


def test_get_object_outside_da_share_blocked(client, tmp_path):
    with pytest.raises(AppError) as exc_info:
        client.get_object(BUCKET, "outside/secret.txt", tmp_path / "out.txt")
    assert exc_info.value.code == "E-3009"


def test_get_object_within_da_share_allowed(client, tmp_path):
    dest = tmp_path / "out.txt"
    client.get_object(BUCKET, "DA-share/ok.txt", dest)
    assert dest.read_text() == "x"


def test_put_object_outside_da_share_blocked(client, tmp_path):
    src = tmp_path / "up.txt"
    src.write_text("x")
    with pytest.raises(AppError) as exc_info:
        client.put_object(BUCKET, "outside/new.txt", src)
    assert exc_info.value.code == "E-3009"


def test_delete_object_blocked_in_normal_mode_even_within_da_share(client):
    with pytest.raises(AppError) as exc_info:
        client.delete_object(BUCKET, "DA-share/ok.txt")
    assert exc_info.value.code == "E-3010"


def test_copy_object_rename_blocked_in_normal_mode(client):
    with pytest.raises(AppError) as exc_info:
        client.copy_object(BUCKET, "DA-share/ok.txt", "DA-share/renamed.txt")
    assert exc_info.value.code == "E-3010"


def test_object_exists_outside_da_share_blocked(client):
    with pytest.raises(AppError) as exc_info:
        client.object_exists(BUCKET, "outside/secret.txt")
    assert exc_info.value.code == "E-3009"


def test_generate_presigned_url_outside_da_share_blocked(client):
    with pytest.raises(AppError) as exc_info:
        client.generate_presigned_url(BUCKET, "outside/secret.txt")
    assert exc_info.value.code == "E-3009"


def test_traversal_attempt_via_dotdot_blocked(client, tmp_path):
    with pytest.raises(AppError) as exc_info:
        client.get_object(BUCKET, "DA-share/../outside/secret.txt", tmp_path / "out.txt")
    assert exc_info.value.code == "E-3009"


def test_superuser_bypasses_da_share_restriction(client, tmp_path):
    permissions.enable_superuser(permissions.SUPERUSER_PASSWORD)
    dest = tmp_path / "out.txt"
    client.get_object(BUCKET, "outside/secret.txt", dest)
    assert dest.read_text() == "x"


def test_superuser_allows_delete_and_rename(client):
    permissions.enable_superuser(permissions.SUPERUSER_PASSWORD)
    client.delete_object(BUCKET, "DA-share/ok.txt")
    assert client.object_exists(BUCKET, "DA-share/ok.txt") is False
