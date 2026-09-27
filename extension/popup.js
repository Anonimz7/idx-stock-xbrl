const urlInput = document.getElementById("url");
const tokenInput = document.getElementById("token");
const statusOutput = document.getElementById("status");
const transportOutput = document.getElementById("transport");
const errorOutput = document.getElementById("error");
const form = document.getElementById("settings-form");
const connectButton = document.getElementById("connect");
const disconnectButton = document.getElementById("disconnect");

const loopbackHosts = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);
let pollTimer = null;

function showError(message) {
  errorOutput.textContent = message || "";
  errorOutput.hidden = !message;
}

// Which transport is carrying commands. The WebSocket is the primary path and
// HTTP long-polling is the fallback, so seeing "http" here means the socket is
// not usable right now.
const TRANSPORT_LABELS = {
  websocket: "Transport: WebSocket (push)",
  http: "Transport: HTTP polling (fallback)"
};

function setState(state) {
  if (!state || typeof state !== "object") {
    return;
  }
  const status = typeof state.status === "string" ? state.status : "disconnected";
  statusOutput.textContent = status;
  const label = TRANSPORT_LABELS[state.transport];
  transportOutput.textContent = label || "";
  transportOutput.hidden = !label;
  if (status === "error") {
    showError(state.error || "Connection error");
  } else if (state.error) {
    showError(state.error);
  } else {
    showError("");
  }
  connectButton.disabled = state.connected || status === "connecting";
  disconnectButton.disabled = status === "disconnected" && !state.connected;
}

function validateUrl(value) {
  let parsed;
  try {
    parsed = new URL(value.trim());
  } catch {
    throw new Error("Enter a valid bridge URL");
  }
  if (
    !["ws:", "wss:", "http:", "https:"].includes(parsed.protocol) ||
    !parsed.hostname
  ) {
    throw new Error("Bridge URL must use ws, wss, http, or https");
  }
  if (!loopbackHosts.has(parsed.hostname.toLowerCase())) {
    throw new Error("Bridge URL must use a loopback host");
  }
  return parsed.href;
}

async function send(message) {
  return browser.runtime.sendMessage(message);
}

async function refresh() {
  try {
    const state = await send({ type: "get-state" });
    setState(state);
  } catch (error) {
    setState({ status: "error", error: error && error.message ? error.message : "Background unavailable" });
  }
}

async function saveConfig() {
  const url = validateUrl(urlInput.value);
  const token = tokenInput.value;
  const response = await send({ type: "save-config", url, token });
  if (!response || response.ok !== true) {
    throw new Error(response && response.error ? response.error.message : "Could not save settings");
  }
  setState(response.state);
  return response;
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  saveConfig().catch((error) => showError(error.message || "Could not save settings"));
});

connectButton.addEventListener("click", () => {
  saveConfig()
    .then(() => send({ type: "connect" }))
    .then((response) => {
      if (!response || response.ok !== true) {
        throw new Error(response && response.error ? response.error.message : "Could not connect");
      }
      setState(response.state);
    })
    .catch((error) => showError(error.message || "Could not connect"));
});

disconnectButton.addEventListener("click", () => {
  send({ type: "disconnect" })
    .then((response) => {
      if (response && response.state) {
        setState(response.state);
      } else {
        refresh();
      }
    })
    .catch((error) => showError(error.message || "Could not disconnect"));
});

browser.runtime.onMessage.addListener((message) => {
  if (message && message.type === "bridge-state") {
    setState(message.state);
  }
});

(async () => {
  try {
    const config = await send({ type: "get-config" });
    if (config && typeof config.url === "string") {
      urlInput.value = config.url;
    }
    if (config && typeof config.token === "string") {
      tokenInput.value = config.token;
    }
  } catch (error) {
    showError(error && error.message ? error.message : "Could not load settings");
  }
  await refresh();
  pollTimer = window.setInterval(refresh, 750);
})();

window.addEventListener("unload", () => {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
  }
});
