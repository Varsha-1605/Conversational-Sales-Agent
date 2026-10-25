// The one place pages talk to the server (API 1, 2): JSON, the cookie session, X-Channel and the error shape.

const CHANNELS = ["web", "app", "whatsapp", "kiosk"];

// the page each landing name from the server opens (design page 2). The pages arrive in later phases.
const PAGES = {
  personalization: "/pages/personalization/",
  home: "/pages/home/",
  associate_home: "/pages/associate-home/",
  integration_status: "/pages/integration-status/",
};

const FALLBACK_ERROR = {
  code: "INTERNAL_ERROR",
  message: "Something went wrong on our side. Please try again.",
  details: {},
  trace_id: null,
};

/** The channel this page runs under: web, unless the link says another (a kiosk opens ?channel=kiosk). */
export function currentChannel() {
  const channel = new URLSearchParams(window.location.search).get("channel");
  return CHANNELS.includes(channel) ? channel : "web";
}

/** A page opened as a kiosk (?channel=kiosk) uses the larger layout of DESIGN 3.2 and 4.1. */
export function applyKioskLook() {
  if (currentChannel() === "kiosk") document.body.classList.add("kiosk");
}

/** An error answer from the server: code, message, details and trace_id as documented in API 5. */
export class ApiError extends Error {
  constructor(status, error) {
    super(error.message);
    this.status = status;
    this.code = error.code;
    this.details = error.details;
    this.traceId = error.trace_id;
  }
}

/** Call the API. Resolves with the JSON body (null for 204) and rejects with an ApiError. */
export async function api(method, path, body) {
  let response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Channel": currentChannel() },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, { ...FALLBACK_ERROR, code: "SERVICE_UNAVAILABLE", message: "We couldn't reach the server. Check your connection and try again." });
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, (data && data.error) || FALLBACK_ERROR);
  return data;
}

/** Open a page by the name the server gave, keeping the channel this page runs under. */
export function goToPage(name) {
  const channel = currentChannel();
  const target = new URL(PAGES[name] || PAGES.home, window.location.origin);
  if (channel !== "web") target.searchParams.set("channel", channel);
  window.location.assign(target);
}

/** A page's own address with the channel kept, for links between the sign-in and sign-up pages. */
export function linkKeepingChannel(path) {
  const channel = currentChannel();
  return channel === "web" ? path : `${path}?channel=${channel}`;
}
