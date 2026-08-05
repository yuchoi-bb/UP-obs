import pytest
from moto import mock_aws

from app.config import Profile
from app.errors import AppError
from app.s3client import S3Client, resolve_addressing_style, resolve_proxies


def test_resolve_addressing_style_auto_with_endpoint_is_path():
    profile = Profile(name="p", endpoint_url="http://10.169.148.36:10443")
    assert resolve_addressing_style(profile) == "path"


def test_resolve_addressing_style_auto_without_endpoint_is_virtual():
    profile = Profile(name="p", endpoint_url="")
    assert resolve_addressing_style(profile) == "virtual"


def test_resolve_addressing_style_manual_override():
    profile = Profile(name="p", endpoint_url="", addressing_style="path")
    assert resolve_addressing_style(profile) == "path"


def test_resolve_proxies_bypassed_for_private_ip():
    profile = Profile(name="p", endpoint_url="http://10.169.148.36:10443", proxy="http://proxy:8080")
    assert resolve_proxies(profile) == {}


def test_resolve_proxies_applied_for_public_endpoint():
    profile = Profile(name="p", endpoint_url="", proxy="http://proxy:8080")
    assert resolve_proxies(profile) == {"http": "http://proxy:8080", "https": "http://proxy:8080"}


@pytest.fixture
def moto_profile():
    return Profile(
        name="test",
        access_key_id="testing",
        secret_access_key="testing",
        region="us-east-1",
        bucket="toolhub-objectstorage",
    )


@mock_aws
def test_s3client_list_get_put_roundtrip(moto_profile, tmp_path):
    client = S3Client(moto_profile)
    client._client.create_bucket(Bucket=moto_profile.bucket)

    src_file = tmp_path / "upload.txt"
    src_file.write_text("hello up-obs")
    client.put_object(moto_profile.bucket, "builds/upload.txt", src_file)

    assert client.object_exists(moto_profile.bucket, "builds/upload.txt") is True
    assert client.object_exists(moto_profile.bucket, "builds/missing.txt") is False

    pages = list(client.list_objects(moto_profile.bucket, prefix="builds/"))
    keys = [obj["Key"] for page in pages for obj in page.get("Contents", [])]
    assert "builds/upload.txt" in keys

    dest_file = tmp_path / "download.txt"
    client.get_object(moto_profile.bucket, "builds/upload.txt", dest_file)
    assert dest_file.read_text() == "hello up-obs"


@mock_aws
def test_s3client_common_prefixes_folder_mapping(moto_profile):
    client = S3Client(moto_profile)
    client._client.create_bucket(Bucket=moto_profile.bucket)
    client._client.put_object(Bucket=moto_profile.bucket, Key="DA-share/v1/file.txt", Body=b"x")
    client._client.put_object(Bucket=moto_profile.bucket, Key="DA-share/v1/", Body=b"")

    pages = list(client.list_objects(moto_profile.bucket, prefix="DA-share/", delimiter="/"))
    prefixes = [p["Prefix"] for page in pages for p in page.get("CommonPrefixes", [])]
    assert "DA-share/v1/" in prefixes


@mock_aws
def test_s3client_list_objects_recursive_when_delimiter_empty(moto_profile):
    client = S3Client(moto_profile)
    client._client.create_bucket(Bucket=moto_profile.bucket)
    client._client.put_object(Bucket=moto_profile.bucket, Key="DA-share/v1/a.txt", Body=b"x")
    client._client.put_object(Bucket=moto_profile.bucket, Key="DA-share/v1/sub/b.txt", Body=b"x")

    pages = list(client.list_objects(moto_profile.bucket, prefix="DA-share/", delimiter=""))
    keys = [obj["Key"] for page in pages for obj in page.get("Contents", [])]
    common_prefixes = [p for page in pages for p in page.get("CommonPrefixes", [])]
    assert set(keys) == {"DA-share/v1/a.txt", "DA-share/v1/sub/b.txt"}
    assert common_prefixes == []


@mock_aws
def test_s3client_get_object_missing_key_raises_e3007(moto_profile, tmp_path):
    client = S3Client(moto_profile)
    client._client.create_bucket(Bucket=moto_profile.bucket)

    with pytest.raises(AppError) as exc_info:
        client.get_object(moto_profile.bucket, "no/such/key.txt", tmp_path / "out.txt")
    assert exc_info.value.code == "E-3007"


@mock_aws
def test_s3client_list_objects_missing_bucket_raises_e3001(moto_profile):
    client = S3Client(moto_profile)
    with pytest.raises(AppError) as exc_info:
        list(client.list_objects(moto_profile.bucket))
    assert exc_info.value.code == "E-3001"


@mock_aws
def test_s3client_create_folder_marker(moto_profile):
    client = S3Client(moto_profile)
    client._client.create_bucket(Bucket=moto_profile.bucket)

    client.create_folder(moto_profile.bucket, "DA-share/new-folder")

    assert client.object_exists(moto_profile.bucket, "DA-share/new-folder/") is True
    pages = list(client.list_objects(moto_profile.bucket, prefix="DA-share/", delimiter="/"))
    prefixes = [p["Prefix"] for page in pages for p in page.get("CommonPrefixes", [])]
    assert "DA-share/new-folder/" in prefixes
