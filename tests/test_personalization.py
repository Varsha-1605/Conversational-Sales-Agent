import pytest

from backend.domain.personalization import (
    DEFAULT_ESCALATION_STORE_ID, effective_device_preference, escalation_store_id, gender_option,
)


@pytest.mark.parametrize("stored, option", [
    ("female", "woman"), ("male", "man"), ("other", "other"),
    (None, None), ("", None),                 # "Prefer not to say" is stored as empty
    ("non-binary", "other"), ("x", "other"),  # a value the page does not offer shows as other, not lost
])
def test_a_stored_gender_maps_to_the_four_options_of_the_page(stored, option):
    assert gender_option(stored) == option


@pytest.mark.parametrize("preferred, kiosk, expected", [
    ("LOC-S03", "LOC-S04", "LOC-S03"),                         # the preferred store comes first
    (None, "LOC-S04", "LOC-S04"),                              # then the kiosk's store
    (None, None, DEFAULT_ESCALATION_STORE_ID),                 # then the default store
    ("", "LOC-S04", "LOC-S04"),
])
def test_an_escalation_goes_to_the_preferred_store_then_the_kiosk_then_the_default(preferred, kiosk, expected):
    assert escalation_store_id(preferred, kiosk) == expected


@pytest.mark.parametrize("stored, effective", [
    ("voice", "web"), ("web", "web"), ("app", "app"), ("whatsapp", "whatsapp"), ("kiosk", "kiosk"),
])
def test_a_voice_device_preference_means_web(stored, effective):
    assert effective_device_preference(stored) == effective
