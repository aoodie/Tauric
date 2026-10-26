import pytest
from cryptography.fernet import Fernet, InvalidToken

from tauric.credential_keys import encryption, key_material, stage_rotation


def test_staged_rotation_keeps_immutable_history_and_encrypts_with_new_key(tmp_path):
    old_key = Fernet.generate_key()
    source, destination = tmp_path / "active", tmp_path / "staged"
    source.write_bytes(old_key)
    historical = Fernet(old_key).encrypt(b"historical-model-or-broker-secret")
    result = stage_rotation(source, destination)
    assert source.read_bytes() == old_key and not result["activated"]
    assert destination.stat().st_mode & 0o777 == 0o600
    ring = encryption(destination.read_bytes())
    assert ring.decrypt(historical) == b"historical-model-or-broker-secret"
    new_ciphertext = ring.encrypt(b"new-secret")
    assert Fernet(key_material(destination.read_bytes())[0]).decrypt(new_ciphertext) == b"new-secret"
    with pytest.raises(InvalidToken):
        Fernet(old_key).decrypt(new_ciphertext)
    assert encryption(old_key + b"\n").decrypt(historical) == b"historical-model-or-broker-secret"


def test_rotation_refuses_to_overwrite_active_or_staged_keys(tmp_path):
    source, destination = tmp_path / "active", tmp_path / "staged"
    source.write_bytes(Fernet.generate_key())
    stage_rotation(source, destination)
    before = source.read_bytes(), destination.read_bytes()
    for target in (source, destination):
        with pytest.raises(FileExistsError):
            stage_rotation(source, target)
    assert before == (source.read_bytes(), destination.read_bytes())


@pytest.mark.parametrize("data", [b"", b"not-a-key", b"x\n" * 33])
def test_invalid_keyrings_fail_closed(data):
    with pytest.raises(ValueError, match="keyring"):
        encryption(data)
