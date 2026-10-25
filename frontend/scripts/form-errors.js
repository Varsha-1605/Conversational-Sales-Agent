// Error display for forms (DESIGN 8): a list of errors at the top that links to each field, the error under its
// field, and nothing the person typed is ever cleared. Every field has a "<name>-error" paragraph after its input.

function fieldOf(form, name) {
  return form.elements.namedItem(name);
}

function errorElementOf(form, name) {
  return form.querySelector(`#${name}-error`);
}

export function clearErrors(form) {
  for (const element of form.querySelectorAll(".field-error")) {
    element.hidden = true;
    element.textContent = "";
  }
  for (const input of form.querySelectorAll("[aria-invalid]")) input.removeAttribute("aria-invalid");
  const summary = form.parentElement.querySelector(".error-summary");
  summary.hidden = true;
  summary.querySelector("ul").replaceChildren();
}

/** Show an error under one field; `extra` is an optional element placed after the message (a link). A field this
 * form does not have is skipped: its problem still appears in the list at the top. */
export function showFieldError(form, name, message, extra) {
  const element = errorElementOf(form, name);
  if (!element) return;
  element.textContent = message;
  if (extra) element.append(" ", extra);
  element.hidden = false;
  const field = fieldOf(form, name);
  if (field && field.setAttribute) field.setAttribute("aria-invalid", "true");
}

/**
 * Show the list of problems at the top, each linking to its field, and move focus to it.
 * Each problem is { name, message }; a problem with no field name (a wrong password) has no link.
 */
export function showSummary(form, problems) {
  const summary = form.parentElement.querySelector(".error-summary");
  const list = summary.querySelector("ul");
  list.replaceChildren();
  for (const problem of problems) {
    const item = document.createElement("li");
    if (problem.name) {
      const link = document.createElement("a");
      link.href = `#${problem.name}`;
      link.textContent = problem.message;
      item.append(link);
    } else {
      item.textContent = problem.message;
    }
    list.append(item);
  }
  summary.hidden = false;
  summary.focus();
}

/**
 * Show problems both ways: each under its own field and all in the list at the top. A problem is
 * { name, message, extra }; `extra` is an optional element placed after the message under its field.
 */
export function showProblems(form, problems) {
  for (const problem of problems) {
    if (problem.name) showFieldError(form, problem.name, problem.message, problem.extra);
  }
  showSummary(form, problems);
}

/** The problems a failed request names: one per field for a VALIDATION_ERROR, else one general message. */
export function problemsFromError(error) {
  if (error.code === "VALIDATION_ERROR" && Array.isArray(error.details)) {
    return error.details.map((detail) => ({ name: detail.field, message: `${detail.problem} ${detail.fix}` }));
  }
  return [{ message: error.message }];
}

/** Lock the submit button while the request runs, so nothing is sent twice (DESIGN 6.1). */
export function lockWhileWorking(button, working) {
  if (working) {
    button.dataset.label = button.textContent;
    button.textContent = "Processing…";
  } else {
    button.textContent = button.dataset.label;
  }
  button.disabled = working;
}
