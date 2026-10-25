import { ApiError, api, applyKioskLook, currentChannel, goToPage, linkKeepingChannel } from "/scripts/api-client.js";
import { clearErrors, lockWhileWorking, problemsFromError, showProblems } from "/scripts/form-errors.js";

// Sign in (design page 2): email, password and the role the person signs in as, Customer by default.

const form = document.getElementById("sign-in-form");
const submitButton = form.querySelector("button[type=submit]");
const onKiosk = currentChannel() === "kiosk";

applyKioskLook();
document.getElementById("sign-up-link").href = linkKeepingChannel("/pages/sign-up/");
// the sign-up page sends a new customer here (?created=1) to sign in
if (new URLSearchParams(window.location.search).get("created") === "1") {
  document.getElementById("account-created").hidden = false;
}

function checkFields() {
  const problems = [];
  if (!form.elements.email.value.trim()) problems.push({ name: "email", message: "Enter your email." });
  if (!form.elements.password.value) problems.push({ name: "password", message: "Enter your password." });
  return problems;
}

// at the kiosk a failure offers help and shows nothing personal (design page 2)
function kioskHelp(message) {
  return onKiosk ? `${message} Try again, or ask an associate.` : message;
}

function problemsFrom(error) {
  if (error.code === "AUTH_FAILED") return [{ name: "password", message: kioskHelp("Email or password is incorrect. Try again.") }];
  if (error.code === "ROLE_MISMATCH") return [{ name: "role", message: kioskHelp(error.message) }];
  if (error.code === "RATE_LIMITED") return [{ message: kioskHelp("Too many attempts. Try again in a few minutes.") }];
  if (error.code === "VALIDATION_ERROR") return problemsFromError(error);
  return [{ message: kioskHelp(error.message) }];
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearErrors(form);
  const problems = checkFields();
  if (problems.length) return showProblems(form, problems);

  const body = {
    email: form.elements.email.value,
    password: form.elements.password.value,
    role: form.elements.role.value,
  };
  const kioskStore = new URLSearchParams(window.location.search).get("store");
  if (onKiosk && kioskStore) body.store_id = kioskStore; // the store this kiosk is set up for

  lockWhileWorking(submitButton, true);
  try {
    const signedIn = await api("POST", "/auth/sign-in", body);
    goToPage(signedIn.landing);
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    showProblems(form, problemsFrom(error)); // what was typed stays in the form
    lockWhileWorking(submitButton, false);
  }
});
