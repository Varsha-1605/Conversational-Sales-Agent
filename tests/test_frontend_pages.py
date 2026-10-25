import re
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from config import PROJECT_ROOT

# The sign-up, sign-in and personalization pages (design pages 1 to 3) as the server serves them. What a script
# does in a browser is not run here; these tests check the pages are served, point only at files that exist, and
# hold the parts the design requires (labels, the Customer default, an error place under every field).

PAGES = ["/pages/sign-in/", "/pages/sign-up/", "/pages/personalization/"]
FRONTEND = PROJECT_ROOT / "frontend"


class PageParts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attributes):
        self.elements.append((tag, dict(attributes)))

    def where(self, tag, **wanted):
        return [attributes for found, attributes in self.elements
                if found == tag and all(attributes.get(name) == value for name, value in wanted.items())]


@pytest.fixture(scope="module")
def client(settings):
    return TestClient(create_app(settings))  # no `with`: no database is needed to serve files


def parts_of(client, path):
    parts = PageParts()
    parts.feed(client.get(path).text)
    return parts


@pytest.mark.parametrize("path", PAGES)
def test_each_page_is_served_with_its_styles_and_scripts(client, path):
    parts = parts_of(client, path)
    references = [attributes["href"] for attributes in parts.where("link", rel="stylesheet")]
    references += [attributes["src"] for attributes in parts.where("script")]
    assert references
    for reference in references:
        assert client.get(reference).status_code == 200, reference


@pytest.mark.parametrize("path", PAGES)
def test_each_page_has_a_language_a_title_and_one_main_heading(client, path):
    html = client.get(path).text
    assert '<html lang="en">' in html
    assert re.search(r"<title>[^<]+</title>", html)
    assert html.count("<h1") == 1


@pytest.mark.parametrize("path", PAGES)
def test_every_input_has_a_label_and_a_place_for_its_error(client, path):
    parts = parts_of(client, path)
    label_targets = {attributes.get("for") for attributes in parts.where("label")}
    ids = {attributes["id"] for _, attributes in parts.elements if "id" in attributes}
    text_inputs = [attributes for tag, attributes in parts.elements
                   if tag == "input" and attributes.get("type") in {"text", "email", "password"}]
    assert text_inputs
    for attributes in text_inputs:
        assert attributes["id"] in label_targets  # the label sits above, never a placeholder instead
        assert "placeholder" not in attributes
        assert f"{attributes['name']}-error" in ids
        assert f"{attributes['name']}-error" in attributes["aria-describedby"]


@pytest.mark.parametrize("path", PAGES)
def test_each_page_has_a_summary_for_errors_that_takes_focus(client, path):
    (summary,) = parts_of(client, path).where("div", **{"class": "error-summary"})
    assert summary["role"] == "alert" and summary["tabindex"] == "-1" and "hidden" in summary


def test_sign_in_offers_the_three_roles_with_customer_chosen_first(client):
    roles = [attributes for attributes in parts_of(client, "/pages/sign-in/").where("input", type="radio")]
    assert [role["value"] for role in roles] == ["customer", "store_associate", "administrator"]
    assert [("checked" in role) for role in roles] == [True, False, False]
    assert {role["name"] for role in roles} == {"role"}


def test_sign_in_asks_for_the_right_things_to_the_browser(client):
    parts = parts_of(client, "/pages/sign-in/")
    (email,) = parts.where("input", name="email")
    (password,) = parts.where("input", name="password")
    assert (email["type"], email["autocomplete"]) == ("email", "username")
    assert (password["type"], password["autocomplete"]) == ("password", "current-password")


def test_sign_up_asks_for_a_name_an_email_and_a_new_password_and_nothing_else(client):
    parts = parts_of(client, "/pages/sign-up/")
    fields = [attributes["name"] for tag, attributes in parts.elements
              if tag == "input" and attributes.get("type") in {"text", "email", "password", "radio", "checkbox"}]
    assert fields == ["full_name", "email", "password"]
    (password,) = parts.where("input", name="password")
    assert password["autocomplete"] == "new-password"


def test_sign_in_has_a_hidden_notice_that_a_new_account_is_ready(client):
    (notice,) = parts_of(client, "/pages/sign-in/").where("p", id="account-created")
    assert "hidden" in notice and notice["role"] == "status"
    assert '"created"' in (FRONTEND / "pages" / "sign-in" / "sign-in.js").read_text(encoding="utf-8")


def test_sign_up_sends_the_new_customer_to_sign_in_not_into_the_site():
    source = (FRONTEND / "pages" / "sign-up" / "sign-up.js").read_text(encoding="utf-8")
    assert 'searchParams.set("created", "1")' in source and "goToPage" not in source


def test_personalization_offers_four_gender_options_and_prefer_not_to_say_is_chosen_first(client):
    radios = parts_of(client, "/pages/personalization/").where("input", type="radio", name="gender")
    assert [radio["value"] for radio in radios] == ["woman", "man", "other", ""]
    assert [("checked" in radio) for radio in radios] == [False, False, False, True]


def test_personalization_consent_choices_start_unticked(client):
    parts = parts_of(client, "/pages/personalization/")
    boxes = parts.where("input", type="checkbox")
    assert {box["name"] for box in boxes} == {"personalization_allowed", "save_preferences_allowed"}
    assert not any("checked" in box for box in boxes)  # "No" is as easy as "Yes" (DESIGN 8)


def test_personalization_has_a_place_for_the_store_choices_and_a_save_button(client):
    html = client.get("/pages/personalization/").text
    assert 'id="store-choices"' in html and 'id="preferred_store_id"' in html
    assert re.search(r'<button[^>]*type="submit"[^>]*>Save</button>', html)


def test_the_api_client_knows_the_personalization_page_the_server_names_as_a_landing():
    source = (FRONTEND / "scripts" / "api-client.js").read_text(encoding="utf-8")
    assert 'personalization: "/pages/personalization/"' in source


def test_the_pages_link_to_each_other(client):
    assert 'href="/pages/sign-up/"' in client.get("/pages/sign-in/").text
    assert 'href="/pages/sign-in/"' in client.get("/pages/sign-up/").text


@pytest.mark.parametrize("script", [
    "pages/sign-in/sign-in.js", "pages/sign-up/sign-up.js", "pages/personalization/personalization.js",
    "scripts/form-errors.js"])
def test_every_module_a_script_imports_exists(script):
    source = (FRONTEND / script).read_text(encoding="utf-8")
    imported = re.findall(r'from "(/[^"]+)"', source)
    assert imported or script.endswith("form-errors.js")
    for path in imported:
        assert (FRONTEND / path.lstrip("/")).is_file(), path


def test_the_design_tokens_are_the_ones_in_the_design_document():
    tokens = (FRONTEND / "styles" / "tokens.css").read_text(encoding="utf-8").lower()
    design = (PROJECT_ROOT / ".claude" / ".plan" / "DESIGN.md").read_text(encoding="utf-8")
    palette = re.findall(r"\| `([a-z-]+)` \| `(#[0-9A-Fa-f]{6})`", design)
    assert len(palette) >= 12
    for name, colour in palette:
        assert colour.lower() in tokens, f"{name} {colour}"
