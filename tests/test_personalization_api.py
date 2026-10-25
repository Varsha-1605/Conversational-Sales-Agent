import pytest
from sqlalchemy import select, update

from backend.db.schema import customers
from backend.domain.consent import ConsentUse, is_allowed, load_consent
from tests.signed_in_app import (
    ADMINISTRATOR, ASSOCIATE, CUSTOMER, DEMO_CUSTOMERS, NEW_CUSTOMER, error_of, running_app, sign_in, sign_in_as,
    sign_up,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("remove_new_customers_afterwards")]

OTHER_CUSTOMER = DEMO_CUSTOMERS[0]
STORE_IDS = ["LOC-S01", "LOC-S02", "LOC-S03", "LOC-S04", "LOC-S05", "LOC-S06"]
CHOICES = {"name": "Rahul Nair", "gender": "man", "preferred_store_id": "LOC-S02",
           "personalization_allowed": True, "save_preferences_allowed": False}  # what the seed holds for CUSTOMER


@pytest.fixture
def seeded_customers_restored(committing_database):
    """Tests change the seeded customers' choices; put every column back afterwards."""
    with committing_database.connect() as connection:
        saved = [dict(row) for row in connection.execute(select(customers)).mappings()]
    yield
    with committing_database.begin() as connection:
        for row in saved:
            connection.execute(update(customers).where(customers.c.customer_id == row["customer_id"]).values(**row))


def stored(engine, customer_id):
    with engine.connect() as connection:
        return connection.execute(select(customers).where(customers.c.customer_id == customer_id)).mappings().one()


def save(client, origin, **changes):
    return client.put("/api/personalization", json={**CHOICES, **changes}, headers=origin)


# reading

def test_a_customer_reads_their_own_choices_and_the_stores_to_pick_from(client, origin):
    sign_in_as(client, origin, CUSTOMER)
    body = client.get("/api/personalization").json()
    assert {key: body[key] for key in CHOICES} == CHOICES
    assert [store["store_id"] for store in body["stores"]] == STORE_IDS
    names = {store["store_id"]: store["store_name"] for store in body["stores"]}
    assert names["LOC-S01"] == "Indiranagar Store"
    assert "password" not in str(body)


def test_each_customer_sees_only_their_own_choices(client, origin):
    sign_in_as(client, origin, OTHER_CUSTOMER)
    body = client.get("/api/personalization").json()
    assert (body["name"], body["preferred_store_id"]) == (
        OTHER_CUSTOMER["full_name"], OTHER_CUSTOMER["preferred_store_id"])


def test_only_a_signed_in_customer_may_use_personalization(client, origin):
    for call in (lambda: client.get("/api/personalization"), lambda: save(client, origin)):
        error_of(call(), "AUTH_REQUIRED", 401)
    for staff, role in ((ASSOCIATE, "store_associate"), (ADMINISTRATOR, "administrator")):
        client.cookies.clear()
        sign_in_as(client, origin, staff, role=role)
        error_of(client.get("/api/personalization"), "FORBIDDEN", 403)
        error_of(save(client, origin), "FORBIDDEN", 403)


def test_an_expired_session_is_asked_to_sign_in_again(client, origin, clock):
    sign_in_as(client, origin, CUSTOMER)
    clock.advance(minutes=31)
    error_of(client.get("/api/personalization"), "AUTH_REQUIRED", 401)


# saving

def test_every_choice_is_saved_and_read_back(client, origin, committing_database, seeded_customers_restored):
    sign_in_as(client, origin, CUSTOMER)
    response = save(client, origin, name="  Rahul N.  ", gender="woman", preferred_store_id="LOC-S03",
                    personalization_allowed=False, save_preferences_allowed=True)
    assert response.status_code == 200
    expected = {"name": "Rahul N.", "gender": "woman", "preferred_store_id": "LOC-S03",
                "personalization_allowed": False, "save_preferences_allowed": True}  # the name is trimmed
    assert {key: response.json()[key] for key in expected} == expected
    assert {key: client.get("/api/personalization").json()[key] for key in expected} == expected
    row = stored(committing_database, CUSTOMER["customer_id"])
    assert (row["full_name"], row["gender"], row["preferred_store_id"]) == ("Rahul N.", "female", "LOC-S03")


@pytest.mark.parametrize("option, stored_value", [
    ("woman", "female"), ("man", "male"), ("other", "other"), (None, None)])
def test_gender_is_stored_in_the_seeds_words_and_prefer_not_to_say_is_empty(
        client, origin, committing_database, seeded_customers_restored, option, stored_value):
    sign_in_as(client, origin, CUSTOMER)
    save(client, origin, gender=option)
    assert stored(committing_database, CUSTOMER["customer_id"])["gender"] == stored_value
    assert client.get("/api/personalization").json()["gender"] == option


def test_only_the_session_customer_changes_whatever_the_request_names(
        client, origin, committing_database, seeded_customers_restored):
    sign_in_as(client, origin, CUSTOMER)
    before = dict(stored(committing_database, OTHER_CUSTOMER["customer_id"]))
    response = client.put("/api/personalization", params={"customer_id": OTHER_CUSTOMER["customer_id"]},
                          json={**CHOICES, "name": "Changed Name"}, headers=origin)
    assert response.status_code == 200
    assert stored(committing_database, CUSTOMER["customer_id"])["full_name"] == "Changed Name"
    assert dict(stored(committing_database, OTHER_CUSTOMER["customer_id"])) == before


@pytest.mark.parametrize("changes, field", [
    ({"preferred_store_id": "LOC-NOWHERE"}, "preferred_store_id"),
    ({"preferred_store_id": "LOC-W01"}, "preferred_store_id"),  # the warehouse is not a store
    ({"preferred_store_id": ""}, "preferred_store_id"),
    ({"name": ""}, "name"),
    ({"name": "   "}, "name"),
    ({"name": "x" * 101}, "name"),
    ({"gender": "robot"}, "gender"),
])
def test_a_bad_choice_is_named_and_nothing_at_all_is_saved(
        client, origin, committing_database, seeded_customers_restored, changes, field):
    sign_in_as(client, origin, CUSTOMER)
    before = dict(stored(committing_database, CUSTOMER["customer_id"]))
    body = error_of(save(client, origin, **{"personalization_allowed": False, **changes}), "VALIDATION_ERROR", 422)
    assert field in {problem["field"] for problem in body["details"]}
    assert all(problem["problem"] and problem["fix"] for problem in body["details"])
    assert dict(stored(committing_database, CUSTOMER["customer_id"])) == before  # even the valid choices were not kept


@pytest.mark.parametrize("missing", list(CHOICES))
def test_every_choice_must_be_sent(client, origin, missing):
    sign_in_as(client, origin, CUSTOMER)
    body = {key: value for key, value in CHOICES.items() if key != missing}
    error_of(client.put("/api/personalization", json=body, headers=origin), "VALIDATION_ERROR", 422)


def test_a_field_the_page_does_not_have_is_refused(client, origin):
    sign_in_as(client, origin, CUSTOMER)
    error_of(save(client, origin, loyalty_tier="Gold"), "VALIDATION_ERROR", 422)
    error_of(save(client, origin, customer_id=OTHER_CUSTOMER["customer_id"]), "VALIDATION_ERROR", 422)


def test_a_save_from_a_foreign_origin_is_refused(client, origin):
    sign_in_as(client, origin, CUSTOMER)
    error_of(save(client, {"Origin": "https://evil.example"}), "FORBIDDEN", 403)


# consent (AC-24, CX-05, CX-06)

def with_saved_preferences(engine, customer_id, preferences):
    with engine.begin() as connection:
        connection.execute(update(customers).where(customers.c.customer_id == customer_id).values(
            save_preferences_allowed=True, saved_preferences=preferences))


def test_turning_save_consent_off_deletes_saved_preferences_in_the_same_step(
        client, origin, committing_database, seeded_customers_restored):
    with_saved_preferences(committing_database, CUSTOMER["customer_id"], {"size": "M", "fit": "slim"})
    sign_in_as(client, origin, CUSTOMER)
    assert save(client, origin, save_preferences_allowed=True).status_code == 200
    assert stored(committing_database, CUSTOMER["customer_id"])["saved_preferences"] == {"size": "M", "fit": "slim"}
    assert save(client, origin, save_preferences_allowed=False).status_code == 200
    assert stored(committing_database, CUSTOMER["customer_id"])["saved_preferences"] is None


def test_turning_personalization_off_keeps_saved_preferences_but_the_consent_gate_sees_it_at_once(
        client, origin, committing_database, seeded_customers_restored):
    with_saved_preferences(committing_database, CUSTOMER["customer_id"], {"size": "M"})
    sign_in_as(client, origin, CUSTOMER)
    save(client, origin, personalization_allowed=False, save_preferences_allowed=True)
    row = stored(committing_database, CUSTOMER["customer_id"])
    assert row["saved_preferences"] == {"size": "M"}  # kept, but not used for suggestions
    with committing_database.connect() as connection:
        consent = load_consent(connection, CUSTOMER["customer_id"])
    assert not is_allowed(consent, ConsentUse.PERSONALIZED_SUGGESTIONS)
    assert is_allowed(consent, ConsentUse.SAVE_MEMORY)


# a new customer: Personalization once, after the first sign-in

def test_a_new_customer_lands_on_personalization_until_a_store_is_chosen_then_on_home(client, origin):
    assert sign_up(client, origin).status_code == 201
    first = sign_in(client, origin, NEW_CUSTOMER["email"], NEW_CUSTOMER["password"])
    assert first.json()["landing"] == "personalization"

    body = client.get("/api/personalization").json()
    assert (body["name"], body["gender"], body["preferred_store_id"]) == ("Asha Verma", None, None)
    assert (body["personalization_allowed"], body["save_preferences_allowed"]) == (False, False)  # both start off

    assert save(client, origin, name="Asha Verma", gender=None, preferred_store_id="LOC-S04",
                personalization_allowed=False, save_preferences_allowed=False).status_code == 200
    assert client.get("/api/me").json()["landing"] == "home"
    client.post("/api/auth/sign-out", headers=origin)
    assert sign_in(client, origin, NEW_CUSTOMER["email"], NEW_CUSTOMER["password"]).json()["landing"] == "home"


def test_a_new_customer_who_leaves_without_saving_sees_personalization_at_the_next_sign_in(client, origin):
    sign_up(client, origin)
    sign_in(client, origin, NEW_CUSTOMER["email"], NEW_CUSTOMER["password"])
    client.post("/api/auth/sign-out", headers=origin)
    again = sign_in(client, origin, NEW_CUSTOMER["email"], NEW_CUSTOMER["password"])
    assert again.json()["landing"] == "personalization"


def test_a_seeded_customer_who_already_has_a_store_goes_straight_home(client, origin):
    assert sign_in_as(client, origin, CUSTOMER).json()["landing"] == "home"


# privacy of the log (NF-04)

def test_the_name_and_choices_are_never_logged(
        capsys, restore_logging, test_settings, clock, tmp_path, origin, seeded_customers_restored):
    with running_app(test_settings, clock, tmp_path) as client:
        sign_in_as(client, origin, CUSTOMER)
        save(client, origin, name="Zed Quillfeather", gender="woman")
    output = capsys.readouterr().out
    assert "personalization saved" in output and CUSTOMER["customer_id"] in output
    assert "Zed Quillfeather" not in output and "female" not in output
