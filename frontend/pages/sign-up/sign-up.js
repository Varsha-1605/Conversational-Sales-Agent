import { ApiError, api, applyKioskLook, linkKeepingChannel } from "/scripts/api-client.js";
import { clearErrors, lockWhileWorking, problemsFromError, showProblems } from "/scripts/form-errors.js";

// Sign up (design page 1): name, email and password create a customer account. There is no guest access.

const form = document.getElementById("sign-up-form");
const submitButton = form.querySelector("button[type=submit]");
const MIN_PASSWORD_LENGTH = 8;
const EMAIL_SHAPE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

applyKioskLook();
const signInPath = linkKeepingChannel("/pages/sign-in/");
document.getElementById("sign-in-link").href = signInPath;

function problemOf(name, value) {
  if (name === "full_name" && !value.trim()) return "Enter your name.";
  if (name === "email" && !EMAIL_SHAPE.test(value.trim())) return "Enter an email like name@example.com.";
  if (name === "password" && value.length < MIN_PASSWORD_LENGTH) return `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  return null;
}

function checkFields() {
  const problems = [];
  for (const name of ["full_name", "email", "password"]) {
    const message = problemOf(name, form.elements[name].value);
    if (message) problems.push({ name, message });
  }
  return problems;
}

function signInLink() {
  const link = document.createElement("a");
  link.href = signInPath;
  link.textContent = "Sign in";
  return link;
}

// an email that already has an account offers the way to sign in (design page 1)
function problemsFrom(error) {
  return problemsFromError(error).map((problem) => {
    if (problem.name === "email" && problem.message.startsWith("This email already")) {
      return { name: "email", message: "This email already has an account.", extra: signInLink() };
    }
    return problem;
  });
}

// check a field when the person leaves it, not only on submit (DESIGN 8). Going on to the submit button is skipped:
// submitting checks every field, and a message appearing under this one would move the button off the click.
for (const name of ["full_name", "email", "password"]) {
  form.elements[name].addEventListener("blur", (event) => {
    if (event.relatedTarget === submitButton) return;
    const message = problemOf(name, form.elements[name].value);
    const input = form.elements[name];
    const errorElement = document.getElementById(`${name}-error`);
    errorElement.hidden = !message;
    errorElement.textContent = message || "";
    if (message) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearErrors(form);
  const problems = checkFields();
  if (problems.length) return showProblems(form, problems);

  lockWhileWorking(submitButton, true);
  try {
    await api("POST", "/auth/sign-up", {
      full_name: form.elements.full_name.value,
      email: form.elements.email.value,
      password: form.elements.password.value,
    });
    // the account is made but not signed in: the person signs in next, and the sign-in page says the account is ready
    const signIn = new URL(signInPath, window.location.origin);
    signIn.searchParams.set("created", "1");
    window.location.assign(signIn);
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    showProblems(form, problemsFrom(error)); // what was typed stays in the form
    lockWhileWorking(submitButton, false);
  }
});
