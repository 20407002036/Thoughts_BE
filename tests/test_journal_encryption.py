import base64
import os

import pytest

from app.core.crypto import JournalCipher, JournalCryptoError
from app.core.settings import Settings
from app.repositories.journal_repository import JournalRepository, JournalRepositoryError


def make_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def encrypted_settings() -> Settings:
    return Settings(
        supabase_url=None,
        supabase_service_role_key=None,
        journal_encryption_key=make_key(),
    )


def plaintext_settings() -> Settings:
    return Settings(supabase_url=None, supabase_service_role_key=None)


def sample_payload(user_id: str = "user-1") -> dict:
    return {
        "user_id": user_id,
        "transcript": "I had a hard day but walked it off.",
        "title": "Evening walk",
        "summary": "Reflected on stress relief.",
        "mood": "calm",
        "mood_explanation": "tone relaxed by the end",
        "takeaway": "Walking helps",
        "themes": ["exercise", "stress"],
        "insights": ["consistency matters"],
        "audio_path": f"{user_id}/abc.mp3",
        "prompt_version": "v1",
        "id": "11111111-1111-1111-1111-111111111111",
    }


class TestJournalCipher:
    def test_round_trip(self):
        cipher = JournalCipher(encrypted_settings())
        payload = {"transcript": "secret words", "themes": ["a", "b"]}

        blob = cipher.encrypt_fields(payload, "user-1")

        assert blob != "secret words"
        assert "secret" not in blob
        assert cipher.decrypt_blob(blob, "user-1") == payload

    def test_wrong_user_aad_rejected(self):
        cipher = JournalCipher(encrypted_settings())
        blob = cipher.encrypt_fields({"title": "x"}, "user-1")

        with pytest.raises(JournalCryptoError):
            cipher.decrypt_blob(blob, "user-2")

    def test_tampered_blob_rejected(self):
        cipher = JournalCipher(encrypted_settings())
        raw = bytearray(base64.b64decode(cipher.encrypt_fields({"title": "x"}, "user-1")))
        raw[-1] ^= 0xFF

        with pytest.raises(JournalCryptoError):
            cipher.decrypt_blob(base64.b64encode(bytes(raw)).decode(), "user-1")

    def test_invalid_key_length_rejected(self):
        bad_key = base64.b64encode(os.urandom(16)).decode()
        with pytest.raises(JournalCryptoError):
            JournalCipher(Settings(journal_encryption_key=bad_key))

    def test_non_base64_key_rejected(self):
        with pytest.raises(JournalCryptoError):
            JournalCipher(Settings(journal_encryption_key="not-base64!!!"))

    def test_disabled_cipher_raises_on_use(self):
        cipher = JournalCipher(plaintext_settings())
        assert cipher.enabled is False
        with pytest.raises(JournalCryptoError):
            cipher.decrypt_blob("AAAA", "user-1")


class TestRepositoryEncryption:
    @pytest.fixture
    def repo(self):
        return JournalRepository(encrypted_settings())

    @pytest.fixture
    def plain_repo(self):
        return JournalRepository(plaintext_settings())

    def test_create_stores_ciphertext_and_returns_plaintext(self, repo):
        payload = sample_payload()

        saved = repo.create_entry(payload)
        stored = repo._local_entries[0]

        assert saved["title"] == payload["title"]
        assert saved["transcript"] == payload["transcript"]
        assert stored.get("data_encrypted")
        assert payload["title"] not in str(stored["data_encrypted"])
        for field in ("title", "transcript", "summary", "mood"):
            assert field not in stored

    def test_get_entry_decrypts(self, repo):
        payload = sample_payload()
        repo.create_entry(payload)

        entry = repo.get_entry(user_id=payload["user_id"], entry_id=payload["id"])

        assert entry is not None
        assert entry["title"] == payload["title"]
        assert entry["transcript"] == payload["transcript"]
        assert entry["themes"] == payload["themes"]

    def test_search_query_matches_decrypted_content(self, repo):
        first = sample_payload(user_id="user-9")
        second = {
            **sample_payload(user_id="user-9"),
            "id": "22222222-2222-2222-2222-222222222222",
            "title": "Grocery list",
            "transcript": "Bought apples and bread today.",
            "summary": "Errands and shopping.",
        }
        repo.create_entry(first)
        repo.create_entry(second)

        rows, total = repo.list_entries("user-9", query="walked")

        assert total == 1
        assert rows[0]["title"] == first["title"]

    def test_tag_filter_matches_decrypted_themes(self, repo):
        payload = sample_payload(user_id="user-8")
        repo.create_entry(payload)

        rows, total = repo.list_entries("user-8", tag="stress")

        assert total == 1
        assert rows[0]["id"] == payload["id"]

    def test_update_reencrypts_merged_content(self, repo):
        payload = sample_payload()
        repo.create_entry(payload)

        updated = repo.update_entry(
            user_id=payload["user_id"], entry_id=payload["id"], payload={"title": "Renamed"}
        )
        stored = repo._local_entries[0]

        assert updated["title"] == "Renamed"
        assert updated["transcript"] == payload["transcript"]
        assert "Renamed" not in stored["data_encrypted"]

    def test_month_filter_still_works(self, repo):
        payload = sample_payload(user_id="user-7")
        repo.create_entry(payload)

        rows, total = repo.list_entries("user-7", month="2000-01")

        assert total == 0

    def test_legacy_plaintext_mode_unaffected(self, plain_repo):
        payload = sample_payload()
        plain_repo.create_entry(payload)

        entry = plain_repo.get_entry(user_id=payload["user_id"], entry_id=payload["id"])
        stored = plain_repo._local_entries[0]

        assert entry["title"] == payload["title"]
        assert stored["title"] == payload["title"]
        assert "data_encrypted" not in stored

    def test_encrypted_row_without_key_fails_loudly(self, encrypted_settings_dict=None):
        writer = JournalRepository(encrypted_settings())
        payload = sample_payload()
        writer.create_entry(payload)

        reader = JournalRepository(plaintext_settings())
        # Simulate the same in-memory store being read without a key.
        reader._local_entries = writer._local_entries

        with pytest.raises(JournalRepositoryError):
            reader.get_entry(user_id=payload["user_id"], entry_id=payload["id"])
