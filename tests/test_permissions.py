"""5장 권한 모델 회귀 테스트: guard() 우회 시도 검증."""
import pytest

from app import permissions
from app.errors import AppError


@pytest.fixture(autouse=True)
def reset_superuser():
    permissions.disable_superuser()
    yield
    permissions.disable_superuser()


def test_enable_superuser_wrong_password_raises():
    with pytest.raises(ValueError):
        permissions.enable_superuser("wrong")
    assert permissions.is_superuser() is False


def test_enable_superuser_correct_password():
    permissions.enable_superuser("123456")
    assert permissions.is_superuser() is True


def test_disable_superuser_needs_no_password():
    permissions.enable_superuser("123456")
    permissions.disable_superuser()
    assert permissions.is_superuser() is False


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("DA-share/file.txt", "DA-share/file.txt"),
        ("DA-share/", "DA-share/"),
        ("DA-share/sub/../file.txt", "DA-share/file.txt"),
        ("DA-share\\sub\\file.txt", "DA-share/sub/file.txt"),
        ("DA-share/%2e%2e/secret.txt", "secret.txt"),
        ("../secret.txt", "../secret.txt"),
        ("/DA-share/x.txt", "DA-share/x.txt"),
        ("DA-share//x.txt", "DA-share/x.txt"),
    ],
)
def test_normalize_key(raw, expected):
    assert permissions.normalize_key(raw) == expected


def test_guard_normal_mode_allows_within_da_share():
    assert permissions.guard("DA-share/builds/tool.zip", "read") == "DA-share/builds/tool.zip"


def test_guard_normal_mode_blocks_outside_da_share():
    with pytest.raises(AppError) as exc_info:
        permissions.guard("other/secret.txt", "read")
    assert exc_info.value.code == "E-3009"


def test_guard_normal_mode_blocks_bucket_root():
    with pytest.raises(AppError) as exc_info:
        permissions.guard("", "list")
    assert exc_info.value.code == "E-3009"


def test_guard_normal_mode_blocks_dotdot_traversal_out_of_da_share():
    with pytest.raises(AppError) as exc_info:
        permissions.guard("DA-share/../secret.txt", "read")
    assert exc_info.value.code == "E-3009"


def test_guard_normal_mode_blocks_backslash_traversal():
    with pytest.raises(AppError) as exc_info:
        permissions.guard("DA-share\\..\\..\\secret.txt", "read")
    assert exc_info.value.code == "E-3009"


def test_guard_normal_mode_blocks_url_encoded_traversal():
    with pytest.raises(AppError) as exc_info:
        permissions.guard("DA-share/%2e%2e/secret.txt", "read")
    assert exc_info.value.code == "E-3009"


def test_guard_normal_mode_blocks_delete_even_within_da_share():
    with pytest.raises(AppError) as exc_info:
        permissions.guard("DA-share/file.txt", "delete")
    assert exc_info.value.code == "E-3010"


def test_guard_normal_mode_blocks_rename_and_move():
    with pytest.raises(AppError) as exc_info:
        permissions.guard("DA-share/file.txt", "rename")
    assert exc_info.value.code == "E-3010"
    with pytest.raises(AppError) as exc_info2:
        permissions.guard("DA-share/file.txt", "move")
    assert exc_info2.value.code == "E-3010"


def test_guard_superuser_allows_bucket_root_and_delete():
    permissions.enable_superuser("123456")
    assert permissions.guard("", "list") == ""
    assert permissions.guard("anything/outside.txt", "delete") == "anything/outside.txt"


def test_root_prefix_switches_by_mode():
    assert permissions.root_prefix() == "DA-share/"
    permissions.enable_superuser("123456")
    assert permissions.root_prefix() == ""
