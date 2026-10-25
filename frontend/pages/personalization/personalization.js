import { ApiError, api, applyKioskLook, goToPage, linkKeepingChannel } from "/scripts/api-client.js";
import { clearErrors, lockWhileWorking, problemsFromError, showProblems } from "/scripts/form-errors.js";

// Personalization (design page 3): name, gender, preferred store and the two consent choices. Both choices start
// unticked, and "No" is as easy as "Yes". A new customer lands here once, after their first sign-in.

const form = document.getElementById("personalization-form");
const submitButton = form.querySelector("button[type=submit]");
const savedNotice = document.getElementById("saved-notice");
const storeChoices = document.getElementById("store-choices");
let firstTime = false; // true until a preferred store has been saved once

applyKioskLook();

function goToSignIn() {
  window.location.assign(linkKeepingChannel("/pages/sign-in/"));
}

function showStores(stores, chosenId) {
  storeChoices.replaceChildren();
  for (const store of stores) {
    const row = document.createElement("div");
    row.className = "choice";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "preferred_store_id";
    input.id = `store-${store.store_id}`;
    input.value = store.store_id;
    input.checked = store.store_id === chosenId;
    const label = document.createElement("label");
    label.htmlFor = input.id;
    label.textContent = store.store_name;
    row.append(input, label);
    storeChoices.append(row);
  }
}

function fill(saved) {
  form.elements.namedItem("name").value = saved.name;
  form.elements.gender.value = saved.gender || "";
  form.elements.personalization_allowed.checked = saved.personalization_allowed;
  form.elements.save_preferences_allowed.checked = saved.save_preferences_allowed;
  showStores(saved.stores, saved.preferred_store_id);
}

function checkFields() {
  const problems = [];
  if (!form.elements.namedItem("name").value.trim()) problems.push({ name: "name", message: "Enter the name we should call you." });
  if (!form.elements.preferred_store_id.value) {
    problems.push({ name: "preferred_store_id", message: "Choose the store you usually visit." });
  }
  return problems;
}

async function load() {
  try {
    const saved = await api("GET", "/personalization");
    firstTime = saved.preferred_store_id === null;
    fill(saved);
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    if (error.status === 401) return goToSignIn(); // not signed in, or the session ended
    showProblems(form, problemsFromError(error));
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearErrors(form);
  savedNotice.hidden = true;
  const problems = checkFields();
  if (problems.length) return showProblems(form, problems);

  lockWhileWorking(submitButton, true);
  try {
    const saved = await api("PUT", "/personalization", {
      name: form.elements.namedItem("name").value,
      gender: form.elements.gender.value || null, // "Prefer not to say" is sent as empty
      preferred_store_id: form.elements.preferred_store_id.value,
      personalization_allowed: form.elements.personalization_allowed.checked,
      save_preferences_allowed: form.elements.save_preferences_allowed.checked,
    });
    if (firstTime) return goToPage("home"); // the first save carries on into the site
    fill(saved);
    savedNotice.hidden = false;
    savedNotice.focus();
    lockWhileWorking(submitButton, false);
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    if (error.status === 401) return goToSignIn();
    showProblems(form, problemsFromError(error)); // what was typed stays in the form
    lockWhileWorking(submitButton, false);
  }
});

load();
