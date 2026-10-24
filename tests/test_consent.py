import pytest
from sqlalchemy import update

from backend.db.schema import customers
from backend.domain.consent import Consent, ConsentUse, is_allowed, load_consent

SAVE_USES = {ConsentUse.SAVE_MEMORY}


@pytest.mark.parametrize("personalization", [True, False])
@pytest.mark.parametrize("save", [True, False])
@pytest.mark.parametrize("use", list(ConsentUse))
def test_each_use_follows_its_choice(personalization, save, use):
    consent = Consent(personalization_allowed=personalization, save_preferences_allowed=save)
    expected = save if use in SAVE_USES else personalization
    assert is_allowed(consent, use) is expected


def test_an_unknown_use_is_refused():
    with pytest.raises(ValueError):
        is_allowed(Consent(True, True), "sell_my_data")


@pytest.mark.integration
def test_consent_is_read_as_stored(rolled_back_connection):
    # seeded: CUST-0002 allows personalization, CUST-0009 declines it
    assert load_consent(rolled_back_connection, "CUST-0002") == Consent(True, False)
    assert load_consent(rolled_back_connection, "CUST-0009") == Consent(False, False)
    rolled_back_connection.execute(update(customers).where(customers.c.customer_id == "CUST-0002")
                                   .values(save_preferences_allowed=True))
    assert is_allowed(load_consent(rolled_back_connection, "CUST-0002"), ConsentUse.SAVE_MEMORY)


@pytest.mark.integration
def test_unknown_customer_has_no_consent(rolled_back_connection):
    with pytest.raises(LookupError):
        load_consent(rolled_back_connection, "CUST-9999")
