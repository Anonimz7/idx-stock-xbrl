"use strict";

const DEFAULT_URL = "ws://127.0.0.1:8765/extension";
const STORAGE_DEFAULTS = {
  url: DEFAULT_URL,
  token: "",
  enabled: false
};
// Must stay in sync with MAX_SNAPSHOT_ELEMENTS in page_functions.js, which is
// injected into the page and cannot import from this file.
const MAX_SNAPSHOT_ELEMENTS = 2000;
const MAX_TEXT_CHARS = 100000;
const MAX_FILL_VALUE = 100000;
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);
const MAX_RETRY_MS = 3000;
// Must stay in sync with "version" in manifest.json, so the bridge can tell
// which extension build is talking to it.
const EXTENSION_VERSION = "0.1.8";
// Firefox *can* discard a non-persistent MV3 background page ("event page") once
// `extensions.background.idle.timeout` (default 30s) passes with nothing holding
// it. Per toolkit/components/extensions/parent/ext-backgroundPage.js the
// documented exemptions are: devtools attached, an open native messaging port, a
// pending async listener promise, an active StreamFilter, and a WebExtension API
// call from this context (reset reason "parentapicall"). Neither a pending fetch()
// nor an open WebSocket is on that list.
//
// Measured on this machine, a 1Hz poll loop did NOT get discarded: the bridge
// logged a steady 1Hz for 100s with no user interaction. So this keepalive is
// insurance against the documented idle path, not a fix for an observed bug.
// It stays because it costs one trivial API call per 20s and covers the cases
// that were documented to bypass the poll loop, such as waking from sleep
// (bug 1834683) and losing the process to memory pressure.
const KEEPALIVE_INTERVAL_MS = 20000;
// While the socket carries commands the HTTP poll loop only ticks at this rate,
// so a socket that drops is covered again within one tick.
const HTTP_IDLE_WHEN_SOCKET_UP_MS = 500;

class BridgeError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "BridgeError";
    this.code = code;
  }
}

let settings = { ...STORAGE_DEFAULTS };
let connectionStatus = "disconnected";
let lastError = "";
let manuallyDisconnected = true;
let bridgeRunning = false;
let bridgeGeneration = 0;
// The two transports are tracked separately: the WebSocket is primary and the
// HTTP long-poll is the fallback, so either one authenticating means connected.
let wsSocket = null;
let wsAuthenticated = false;
let wsRetryDelay = 500;
let lastWebSocketError = "";
let wsAttempts = 0;
// Why the WebSocket loop is where it is. A frozen attempt counter is ambiguous
// on its own: it reads the same whether the loop exited or is still awaiting a
// socket, so the exit reason has to be reported to tell those apart.
let wsLoopState = "idle";
let wsExitReason = "";
let wsUrlInUse = "";
let lastWebSocketClose = "";
let httpAuthenticated = false;
let httpRetryDelay = 500;
let settingsReady = Promise.resolve();

const pendingRequests = new Set();
const snapshotTabs = new Set();

function hasOwn(object, key) {
  return Object.prototype.hasOwnProperty.call(object, key);
}

function cleanText(value, limit = 400) {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value).replace(/\s+/g, " ").trim().slice(0, limit);
}

function conciseError(error, fallbackCode = "REQUEST_FAILED") {
  if (error instanceof BridgeError) {
    return error;
  }
  let message = "";
  if (error && typeof error.message === "string") {
    message = error.message;
  } else if (typeof error === "string") {
    message = error;
  }
  message = cleanText(message, 240) || "The operation failed";
  return new BridgeError(fallbackCode, message);
}

function firstParam(params, ...names) {
  for (const name of names) {
    if (hasOwn(params, name)) {
      return params[name];
    }
  }
  return undefined;
}

function requireObjectParams(params) {
  if (params === undefined || params === null) {
    return {};
  }
  if (typeof params !== "object" || Array.isArray(params)) {
    throw new BridgeError("INVALID_PARAMS", "Params must be an object");
  }
  return params;
}

function validateUrl(value) {
  if (typeof value !== "string" || !value.trim()) {
    throw new BridgeError("INVALID_URL", "Bridge URL is required");
  }
  let parsed;
  try {
    parsed = new URL(value.trim());
  } catch {
    throw new BridgeError("INVALID_URL", "Bridge URL is invalid");
  }
  if (!["ws:", "wss:", "http:", "https:"].includes(parsed.protocol) || !parsed.hostname) {
    throw new BridgeError("INVALID_URL", "Bridge URL must use ws, wss, http, or https");
  }
  if (!LOOPBACK_HOSTS.has(parsed.hostname.toLowerCase())) {
    throw new BridgeError("INVALID_URL", "Bridge URL must use a loopback host");
  }
  return parsed.href;
}

function validatePageUrl(value) {
  if (typeof value !== "string" || !value.trim()) {
    throw new BridgeError("INVALID_URL", "URL is required");
  }
  const raw = value.trim();
  if (raw === "about:blank") {
    return raw;
  }
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new BridgeError("INVALID_URL", "URL is invalid");
  }
  if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) {
    throw new BridgeError("INVALID_URL", "URL must use http, https, or about:blank");
  }
  return parsed.href;
}

function parseTabId(value) {
  let numberValue;
  if (typeof value === "number") {
    numberValue = value;
  } else if (typeof value === "string" && /^\d+$/.test(value.trim())) {
    numberValue = Number(value.trim());
  } else {
    throw new BridgeError("INVALID_TAB_ID", "tab_id must be an integer");
  }
  if (!Number.isSafeInteger(numberValue) || numberValue < 0) {
    throw new BridgeError("INVALID_TAB_ID", "tab_id must be a non-negative integer");
  }
  return numberValue;
}

function normalizeRef(value) {
  if (typeof value === "number" && Number.isSafeInteger(value) && value > 0) {
    return `e${value}`;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (/^e\d+$/.test(trimmed)) {
      return trimmed;
    }
    if (/^\d+$/.test(trimmed) && Number(trimmed) > 0) {
      return `e${Number(trimmed)}`;
    }
  }
  throw new BridgeError("INVALID_REF", "ref must be an e-number reference");
}

function publicTab(tab) {
  if (!tab || typeof tab !== "object") {
    return {};
  }
  const result = {};
  const fields = [
    "id",
    "windowId",
    "index",
    "active",
    "highlighted",
    "pinned",
    "incognito",
    "status",
    "title",
    "url",
    "openerTabId",
    "lastAccessed"
  ];
  for (const field of fields) {
    if (hasOwn(tab, field) && tab[field] !== undefined) {
      result[field] = tab[field];
    }
  }
  return result;
}

function publicState() {
  const connected = wsAuthenticated || httpAuthenticated;
  return {
    status: connectionStatus,
    connected,
    authenticated: connected,
    transport: wsAuthenticated ? "websocket" : connected ? "http" : "none",
    url: settings.url,
    error: lastError,
    retry_in_ms: 0
  };
}

function broadcastState() {
  if (!browser.runtime || typeof browser.runtime.sendMessage !== "function") {
    return;
  }
  try {
    const result = browser.runtime.sendMessage({
      type: "bridge-state",
      state: publicState()
    });
    if (result && typeof result.catch === "function") {
      result.catch(() => {});
    }
  } catch {
    return;
  }
}

function setConnectionStatus(nextStatus, message = "") {
  if (nextStatus === connectionStatus && message === lastError) {
    return;
  }
  connectionStatus = nextStatus;
  lastError = message;
  broadcastState();
}

function invalidateRefs(tabId) {
  snapshotTabs.delete(tabId);
  if (!browser.scripting || typeof browser.scripting.executeScript !== "function") {
    return;
  }
  try {
    const result = browser.scripting.executeScript({
      target: { tabId },
      func: function clearBridgeRefs() {
        const roots = [document];
        for (let index = 0; index < roots.length && roots.length < 64; index += 1) {
          const descendants = roots[index].querySelectorAll("*");
          for (const element of descendants) {
            if (element.shadowRoot && roots.length < 64) {
              roots.push(element.shadowRoot);
            }
          }
        }
        for (const root of roots) {
          for (const element of root.querySelectorAll("[data-firefox-bridge-ref]")) {
            element.removeAttribute("data-firefox-bridge-ref");
          }
        }
        return true;
      }
    });
    if (result && typeof result.catch === "function") {
      result.catch(() => {});
    }
  } catch {
    return;
  }
}

function httpBridgeUrl(path) {
  const parsed = new URL(settings.url);
  if (parsed.protocol === "ws:") {
    parsed.protocol = "http:";
  } else if (parsed.protocol === "wss:") {
    parsed.protocol = "https:";
  }
  parsed.pathname = path;
  parsed.search = "";
  parsed.hash = "";
  return parsed.href;
}

function webSocketUrl() {
  const parsed = new URL(settings.url);
  if (parsed.protocol === "http:") {
    parsed.protocol = "ws:";
  } else if (parsed.protocol === "https:") {
    parsed.protocol = "wss:";
  }
  return parsed.href;
}

// An MV3 background page is not guaranteed to persist: Firefox can discard it
// after `extensions.background.idle.timeout` (30s by default) and a discarded
// page runs no timers, so nothing can bring it back. The extension therefore
// calls a WebExtension API every 20s, which is the keepalive the Firefox source
// actually honours. On this machine the poll loop has been observed surviving
// for 100s straight without it, so treat this as insurance rather than the cause
// of past disconnects.
function startKeepalive() {
  if (
    !browser.runtime ||
    typeof browser.runtime.getPlatformInfo !== "function"
  ) {
    log("warn", "Keepalive unavailable: browser.runtime.getPlatformInfo is missing");
    return;
  }
  setInterval(() => {
    try {
      const result = browser.runtime.getPlatformInfo();
      if (result && typeof result.catch === "function") {
        // A rejected call still reached the parent process, which is the part
        // that resets the idle timer.
        result.catch(() => {});
      }
    } catch {
      return;
    }
  }, KEEPALIVE_INTERVAL_MS);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// One transport going down must not report the bridge as disconnected while the
// other is still authenticated.
function reportTransportState(fallbackStatus, message = "") {
  if (wsAuthenticated || httpAuthenticated) {
    setConnectionStatus("connected", "");
    return;
  }
  setConnectionStatus(fallbackStatus, message);
}

function log(level, message, data) {
  try {
    if (data && Object.keys(data).length > 0) {
      console[level](`[Bridge] ${message}`, data);
    } else {
      console[level](`[Bridge] ${message}`);
    }
  } catch {
    return;
  }
}

async function postHttp(path, body) {
  let response;
  try {
    response = await fetch(httpBridgeUrl(path), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${settings.token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify(body)
    });
  } catch (error) {
    throw new BridgeError(
      "HTTP_CONNECTION_FAILED",
      cleanText(error && error.message ? error.message : "Could not reach the bridge", 240)
    );
  }
  if (response.status === 401 || response.status === 403) {
    throw new BridgeError("AUTH_FAILED", "Authentication failed");
  }
  if (!response.ok) {
    throw new BridgeError("HTTP_REQUEST_FAILED", `Bridge returned HTTP ${response.status}`);
  }
  try {
    return await response.json();
  } catch {
    throw new BridgeError("INVALID_SERVER_RESPONSE", "Bridge returned invalid JSON");
  }
}

async function runPageFunction(tabId, func, args = []) {
  let results;
  try {
    results = await browser.scripting.executeScript({
      target: { tabId },
      func,
      args
    });
  } catch (error) {
    throw new BridgeError(
      "PAGE_UNAVAILABLE",
      cleanText(error && error.message ? error.message : "Page is not available", 240)
    );
  }
  if (!Array.isArray(results) || results.length === 0 || !hasOwn(results[0], "result")) {
    throw new BridgeError("PAGE_EXECUTION_FAILED", "Page returned no result");
  }
  return pageError(results[0].result);
}

function pageError(result) {
  if (result && typeof result === "object" && result.error) {
    const error = result.error;
    const code = typeof error === "object" && typeof error.code === "string"
      ? error.code
      : "PAGE_OPERATION_FAILED";
    const message = typeof error === "object" && typeof error.message === "string"
      ? error.message
      : typeof error === "string"
        ? error
        : "Page operation failed";
    throw new BridgeError(code, cleanText(message, 240));
  }
  return result;
}

async function resolveTabId(params) {
  const value = firstParam(params, "tab_id", "tabId");
  if (value !== undefined) {
    return parseTabId(value);
  }
  let tabs;
  try {
    tabs = await browser.tabs.query({ active: true, currentWindow: true });
  } catch (error) {
    throw conciseError(error, "TAB_LOOKUP_FAILED");
  }
  if (!Array.isArray(tabs) || tabs.length === 0) {
    throw new BridgeError("TAB_NOT_FOUND", "No active tab is available");
  }
  return parseTabId(tabs[0].id);
}

async function listTabs(params) {
  const query = {};
  const windowValue = firstParam(params, "window_id", "windowId");
  if (windowValue !== undefined) {
    query.windowId = parseTabId(windowValue);
  }
  let tabs;
  try {
    tabs = await browser.tabs.query(query);
  } catch (error) {
    throw conciseError(error, "TAB_LIST_FAILED");
  }
  if (!Array.isArray(tabs)) {
    throw new BridgeError("TAB_LIST_FAILED", "Tab list is invalid");
  }
  return tabs.map(publicTab);
}

async function openTab(params) {
  const url = validatePageUrl(firstParam(params, "url"));
  const activeValue = firstParam(params, "active");
  if (activeValue !== undefined && typeof activeValue !== "boolean") {
    throw new BridgeError("INVALID_PARAMS", "active must be a boolean");
  }
  const createProperties = {
    url,
    active: activeValue === undefined ? true : activeValue
  };
  const windowValue = firstParam(params, "window_id", "windowId");
  if (windowValue !== undefined) {
    createProperties.windowId = parseTabId(windowValue);
  }
  let tab;
  try {
    tab = await browser.tabs.create(createProperties);
  } catch (error) {
    throw conciseError(error, "TAB_OPEN_FAILED");
  }
  return publicTab(tab);
}

async function activateTab(params) {
  const tabId = await resolveTabId(params);
  let tab;
  try {
    tab = await browser.tabs.update(tabId, { active: true });
  } catch (error) {
    throw conciseError(error, "TAB_ACTIVATE_FAILED");
  }
  let windowId = tab && tab.windowId;
  if (windowId === undefined) {
    try {
      const current = await browser.tabs.get(tabId);
      windowId = current && current.windowId;
      tab = current;
    } catch (error) {
      throw conciseError(error, "TAB_ACTIVATE_FAILED");
    }
  }
  if (windowId === undefined || !browser.windows || typeof browser.windows.update !== "function") {
    throw new BridgeError("WINDOW_FOCUS_FAILED", "Could not focus the tab window");
  }
  try {
    await browser.windows.update(windowId, { focused: true });
  } catch (error) {
    throw conciseError(error, "WINDOW_FOCUS_FAILED");
  }
  return publicTab(tab);
}

async function closeTab(params) {
  const tabId = await resolveTabId(params);
  try {
    await browser.tabs.remove(tabId);
  } catch (error) {
    throw conciseError(error, "TAB_CLOSE_FAILED");
  }
  snapshotTabs.delete(tabId);
  return { closed: true, tab_id: tabId };
}

async function navigateTab(params) {
  const tabId = await resolveTabId(params);
  const url = validatePageUrl(firstParam(params, "url"));
  snapshotTabs.delete(tabId);
  void invalidateRefs(tabId);
  let tab;
  try {
    tab = await browser.tabs.update(tabId, { url });
  } catch (error) {
    throw conciseError(error, "TAB_NAVIGATE_FAILED");
  }
  return publicTab(tab);
}

async function snapshotTab(params) {
  const tabId = await resolveTabId(params);
  const requested = firstParam(params, "max_elements", "maxElements");
  let maxElements = MAX_SNAPSHOT_ELEMENTS;
  if (requested !== undefined) {
    if (typeof requested !== "number" || !Number.isInteger(requested) || requested < 1) {
      throw new BridgeError("INVALID_PARAMS", "max_elements must be a positive integer");
    }
    maxElements = Math.min(requested, MAX_SNAPSHOT_ELEMENTS);
  }
  const result = await runPageFunction(tabId, snapshotPage, [maxElements]);
  snapshotTabs.add(tabId);
  return result;
}

async function clickElement(params) {
  const tabId = await resolveTabId(params);
  const ref = normalizeRef(firstParam(params, "ref"));
  return runPageFunction(tabId, clickPage, [ref]);
}

async function fillElement(params) {
  const tabId = await resolveTabId(params);
  const ref = normalizeRef(firstParam(params, "ref"));
  const value = firstParam(params, "value");
  const label = firstParam(params, "label", "text");
  if (value === undefined && label === undefined) {
    throw new BridgeError("INVALID_PARAMS", "value is required");
  }
  if (value !== undefined && typeof value !== "string") {
    throw new BridgeError("INVALID_PARAMS", "value must be a string");
  }
  if (label !== undefined && typeof label !== "string") {
    throw new BridgeError("INVALID_PARAMS", "label must be a string");
  }
  if (typeof value === "string" && value.length > MAX_FILL_VALUE) {
    throw new BridgeError("VALUE_TOO_LARGE", "value is too large");
  }
  if (typeof label === "string" && label.length > MAX_FILL_VALUE) {
    throw new BridgeError("VALUE_TOO_LARGE", "label is too large");
  }
  const mode = value !== undefined ? "value" : "label";
  return runPageFunction(tabId, fillPage, [
    ref,
    value === undefined ? "" : value,
    label === undefined ? "" : label,
    mode
  ]);
}

async function pageText(params) {
  const tabId = await resolveTabId(params);
  const requested = firstParam(params, "max_chars", "maxChars");
  let maxChars = MAX_TEXT_CHARS;
  if (requested !== undefined) {
    if (typeof requested !== "number" || !Number.isInteger(requested) || requested < 1) {
      throw new BridgeError("INVALID_PARAMS", "max_chars must be a positive integer");
    }
    maxChars = Math.min(requested, MAX_TEXT_CHARS);
  }
  return runPageFunction(tabId, textPage, [maxChars]);
}

const methodHandlers = new Map([
  ["ping", async () => ({ pong: true })],
  ["tabs.list", (params) => listTabs(requireObjectParams(params))],
  ["tabs.open", (params) => openTab(requireObjectParams(params))],
  ["tabs.activate", (params) => activateTab(requireObjectParams(params))],
  ["tabs.close", (params) => closeTab(requireObjectParams(params))],
  ["tab.navigate", (params) => navigateTab(requireObjectParams(params))],
  ["tab.snapshot", (params) => snapshotTab(requireObjectParams(params))],
  ["tab.click", (params) => clickElement(requireObjectParams(params))],
  ["tab.fill", (params) => fillElement(requireObjectParams(params))],
  ["tab.text", (params) => pageText(requireObjectParams(params))],
  ["tab.select_dropdown", async (params) => {
    const p = requireObjectParams(params);
    const tabId = await resolveTabId(p);
    const ref = normalizeRef(firstParam(p, "ref"));
    const value = firstParam(p, "value");
    if (value === undefined) {
      throw new BridgeError("INVALID_PARAMS", "value is required for tab.select_dropdown");
    }
    return runPageFunction(tabId, selectDropdownPage, [ref, String(value)]);
  }],
  ["tab.select_dropdown_option", async (params) => {
    const p = requireObjectParams(params);
    const tabId = await resolveTabId(p);
    const containerRef = normalizeRef(firstParam(p, "ref"));
    const optionText = firstParam(p, "value");
    if (optionText === undefined) {
      throw new BridgeError("INVALID_PARAMS", "value is required for tab.select_dropdown_option");
    }
    return runPageFunction(tabId, selectDropdownOptionPage, [containerRef, String(optionText)]);
  }],
  ["tab.download", async (params) => {
    const p = requireObjectParams(params);
    const tabId = await resolveTabId(p);
    const ref = normalizeRef(firstParam(p, "ref"));
    const filenameParam = firstParam(p, "filename");
    if (!filenameParam) {
      throw new BridgeError("INVALID_PARAMS", "filename is required for tab.download");
    }
    const result = await runPageFunction(tabId, downloadPage, [ref, String(filenameParam)]);
    if (result && result.error) {
      pageError(result);
    }
    if (!result || !result.href) {
      throw new BridgeError("DOWNLOAD_FAILED", "Could not resolve download href");
    }
    if (
      typeof browser !== "undefined" &&
      browser.downloads &&
      typeof browser.downloads.download === "function"
    ) {
      let downloadId;
      try {
        downloadId = await browser.downloads.download({
          url: result.href,
          filename: result.filename,
          saveAs: false,
          conflictAction: "uniquify"
        });
      } catch (error) {
        throw conciseError(error, "DOWNLOAD_START_FAILED");
      }
      log("debug", "Triggered browser download", {
        href: result.href,
        filename: result.filename,
        downloadId
      });
      let finalFilename = result.filename;
      try {
        const items = await browser.downloads.search({ id: downloadId });
        if (items && items.length > 0) {
          finalFilename = items[0].filename || finalFilename;
        }
      } catch {
        // ignore, return the requested filename
      }
      return {
        downloaded: true,
        ref,
        href: result.href,
        filename: finalFilename
      };
    }
    return {
      downloaded: true,
      ref,
      href: result.href,
      filename: result.filename
    };
  }]
]);

async function handleRequestMessage(message) {
  const id = message && message.id;
  if (typeof id !== "string" || !id || id.length > 200) {
    return null;
  }
  pendingRequests.add(id);
  try {
    const handler = methodHandlers.get(message.method);
    if (!handler) {
      throw new BridgeError("UNSUPPORTED_METHOD", "Unsupported extension method");
    }
    const result = await handler(requireObjectParams(message.params));
    return {
      type: "response",
      id,
      ok: true,
      result: result === undefined ? null : result
    };
  } catch (error) {
    const bridgeError = conciseError(error);
    return {
      type: "response",
      id,
      ok: false,
      error: {
        code: bridgeError.code,
        message: cleanText(bridgeError.message, 240) || "The operation failed"
      }
    };
  } finally {
    pendingRequests.delete(id);
  }
}

async function startBridge() {
  await settingsReady;
  if (bridgeRunning || manuallyDisconnected || !settings.enabled) {
    return;
  }
  try {
    validateUrl(settings.url);
  } catch (error) {
    setConnectionStatus("error", conciseError(error, "INVALID_URL").message);
    return;
  }
  bridgeRunning = true;
  const generation = ++bridgeGeneration;
  manuallyDisconnected = false;
  httpRetryDelay = 500;
  wsRetryDelay = 500;
  log("info", "Starting bridge", { url: settings.url });
  // WebSocket first, HTTP long-poll as the fallback. Both loops run for the
  // lifetime of the bridge, but the HTTP loop stays idle while the socket is
  // authenticated, so a blocked or unavailable socket costs nothing.
  await Promise.allSettled([runWebSocketLoop(generation), runHttpLoop(generation)]);
  bridgeRunning = false;
  if (!manuallyDisconnected && settings.enabled) {
    reportTransportState("disconnected", lastError || "Bridge connection stopped");
  }
}

async function runWebSocketLoop(generation) {
  while (
    !manuallyDisconnected &&
    settings.enabled &&
    generation === bridgeGeneration
  ) {
    let socket = null;
    wsAttempts += 1;
    try {
      wsUrlInUse = webSocketUrl();
      socket = new WebSocket(wsUrlInUse);
    } catch (error) {
      lastWebSocketError = conciseError(error, "WS_CONNECTION_FAILED").message;
      reportTransportState("disconnected", lastWebSocketError);
    }
    if (socket) {
      if (!wsAuthenticated) {
        setConnectionStatus("connecting", "");
      }
      wsLoopState = "awaiting socket";
      const outcome = await runWebSocketSession(socket, generation);
      if (outcome.authenticated) {
        wsRetryDelay = 500;
        lastWebSocketError = "";
      } else {
        lastWebSocketError = outcome.message || "WebSocket closed";
      }
      if (outcome.fatal) {
        // The server rejected the token; retrying the same token cannot help.
        setConnectionStatus("error", outcome.message);
        stopWebSocketLoop(`rejected: ${outcome.message}`);
        return;
      }
      reportTransportState("disconnected", outcome.message);
    }
    if (manuallyDisconnected) {
      stopWebSocketLoop("disconnected by the user");
      return;
    }
    if (!settings.enabled) {
      stopWebSocketLoop("settings.enabled is false");
      return;
    }
    if (generation !== bridgeGeneration) {
      stopWebSocketLoop("superseded by a newer generation");
      return;
    }
    wsLoopState = "retrying";
    await sleep(wsRetryDelay);
    wsRetryDelay = Math.min(wsRetryDelay * 2, MAX_RETRY_MS);
  }
  stopWebSocketLoop("while condition cleared before the first attempt");
}

function stopWebSocketLoop(reason) {
  wsLoopState = "exited";
  wsExitReason = reason;
}

function runWebSocketSession(socket, generation) {
  wsSocket = socket;
  return new Promise((resolve) => {
    let settled = false;
    const finish = (outcome) => {
      if (settled) {
        return;
      }
      settled = true;
      if (wsSocket === socket) {
        wsSocket = null;
      }
      wsAuthenticated = false;
      try {
        socket.close();
      } catch {
        // Already closing or closed.
      }
      resolve(outcome);
    };
    socket.addEventListener("open", () => {
      if (generation !== bridgeGeneration || manuallyDisconnected) {
        finish({ authenticated: false, fatal: false, message: "Bridge stopped" });
        return;
      }
      try {
        socket.send(
          JSON.stringify({
            type: "authenticate",
            token: settings.token,
            extension: { name: "firefox-extension", version: EXTENSION_VERSION }
          })
        );
      } catch (error) {
        finish({
          authenticated: false,
          fatal: false,
          message: conciseError(error, "WS_SEND_FAILED").message
        });
      }
    });
    socket.addEventListener("message", (event) => {
      void handleWebSocketMessage(socket, event, generation, finish);
    });
    socket.addEventListener("error", () => {
      finish({
        authenticated: false,
        fatal: false,
        message: "WebSocket connection failed"
      });
    });
    socket.addEventListener("close", (event) => {
      // The close code separates "never established" (1006) from "established
      // then dropped", which is the difference between a policy block and a
      // server that hung up. The `error` event fires first and carries no code.
      lastWebSocketClose = `${event.code}${event.reason ? ` ${event.reason}` : ""}`;
      finish({
        authenticated: false,
        fatal: false,
        message: lastError || "WebSocket closed"
      });
    });
  });
}

async function handleWebSocketMessage(socket, event, generation, finish) {
  let payload;
  try {
    payload = JSON.parse(event.data);
  } catch {
    return;
  }
  if (!payload || typeof payload !== "object" || typeof payload.type !== "string") {
    return;
  }
  if (payload.type === "authenticated") {
    if (payload.ok === true) {
      wsAuthenticated = true;
      wsRetryDelay = 500;
      setConnectionStatus("connected", "");
      log("info", "WebSocket authenticated", { version: EXTENSION_VERSION });
      return;
    }
    const failure = payload.error;
    const message =
      failure && typeof failure.message === "string"
        ? cleanText(failure.message, 240) || "Authentication failed"
        : "Authentication failed";
    finish({ authenticated: false, fatal: true, message });
    return;
  }
  if (payload.type === "request") {
    if (generation !== bridgeGeneration || manuallyDisconnected) {
      return;
    }
    const response = await handleRequestMessage(payload);
    try {
      socket.send(JSON.stringify(response));
    } catch (error) {
      finish({
        authenticated: false,
        fatal: false,
        message: conciseError(error, "WS_SEND_FAILED").message
      });
    }
  }
}

// The bridge cannot inspect this page, so the HTTP poll carries the extension's
// identity and transport diagnostics on every request. That makes
// `/api/v1/status` self-describing instead of guessing which build is attached.
function bridgeSelfReport() {
  return {
    extension: { name: "firefox-extension", version: EXTENSION_VERSION },
    diagnostics: {
      status: connectionStatus,
      error: lastError,
      websocket: wsAuthenticated,
      websocket_error: lastWebSocketError,
      websocket_attempts: wsAttempts,
      websocket_state: wsLoopState,
      websocket_exit: wsExitReason,
      websocket_url: wsUrlInUse,
      websocket_close: lastWebSocketClose
    }
  };
}

async function runHttpLoop(generation) {
  while (
    !manuallyDisconnected &&
    settings.enabled &&
    generation === bridgeGeneration
  ) {
    // The socket is carrying commands. Polling as well would only add load and
    // would keep the bridge's HTTP staleness window armed for no reason.
    if (wsAuthenticated) {
      await sleep(HTTP_IDLE_WHEN_SOCKET_UP_MS);
      continue;
    }
    try {
      // Only report "connecting" while the link is actually down. Flipping to
      // "connecting" on every poll makes the popup flicker and briefly
      // re-enables the Connect button during a healthy session.
      if (!httpAuthenticated) {
        setConnectionStatus("connecting", "");
      }
      const payload = await postHttp("/extension/poll", bridgeSelfReport());
      if (generation !== bridgeGeneration || manuallyDisconnected) {
        return;
      }
      httpAuthenticated = true;
      httpRetryDelay = 500;
      setConnectionStatus("connected", "");
      log("debug", "Received poll response", { hasCommand: Boolean(payload && payload.command) });
      if (payload && payload.command) {
        const response = await handleRequestMessage(payload.command);
        if (response) {
          try {
            await postHttp("/extension/response", response);
          } catch (error) {
            if (conciseError(error).code === "AUTH_FAILED") {
              httpAuthenticated = false;
              reportTransportState("error", "Authentication failed");
            }
          }
        }
      }
    } catch (error) {
      httpAuthenticated = false;
      const bridgeError = conciseError(error, "HTTP_CONNECTION_FAILED");
      if (bridgeError.code === "AUTH_FAILED") {
        reportTransportState("error", bridgeError.message);
        return;
      }
      reportTransportState("disconnected", bridgeError.message);
      await sleep(httpRetryDelay);
      httpRetryDelay = Math.min(httpRetryDelay * 2, MAX_RETRY_MS);
    }
  }
}

function stopBridge() {
  bridgeGeneration += 1;
  bridgeRunning = false;
  if (wsSocket) {
    try {
      wsSocket.close();
    } catch {
      // Already closing or closed.
    }
  }
}

async function persistSettings(changes) {
  await browser.storage.local.set(changes);
  if (hasOwn(changes, "url")) {
    settings.url = changes.url;
  }
  if (hasOwn(changes, "token")) {
    settings.token = changes.token;
  }
  if (hasOwn(changes, "enabled")) {
    settings.enabled = changes.enabled;
  }
}

async function saveConfig(url, token) {
  const validatedUrl = validateUrl(url);
  if (typeof token !== "string") {
    throw new BridgeError("INVALID_PARAMS", "token must be a string");
  }
  const changed = validatedUrl !== settings.url || token !== settings.token;
  await persistSettings({ url: validatedUrl, token });
  if (changed && settings.enabled) {
    manuallyDisconnected = false;
    httpRetryDelay = 500;
    wsRetryDelay = 500;
    void startBridge();
  }
  return publicState();
}

async function handleRuntimeMessage(message) {
  if (!message || typeof message.type !== "string") {
    return undefined;
  }
  await settingsReady;
  if (message.type === "get-state") {
    return publicState();
  }
  if (message.type === "get-config") {
    return {
      url: settings.url,
      token: settings.token,
      enabled: settings.enabled
    };
  }
  if (message.type === "save-config") {
    try {
      const url = hasOwn(message, "url") ? message.url : settings.url;
      const token = hasOwn(message, "token") ? message.token : settings.token;
      const state = await saveConfig(url, token);
      return { ok: true, state };
    } catch (error) {
      const bridgeError = conciseError(error, "INVALID_PARAMS");
      return {
        ok: false,
        error: { code: bridgeError.code, message: bridgeError.message }
      };
    }
  }
  if (message.type === "connect") {
    try {
      if (hasOwn(message, "url") || hasOwn(message, "token")) {
        const url = hasOwn(message, "url") ? message.url : settings.url;
        const token = hasOwn(message, "token") ? message.token : settings.token;
        await saveConfig(url, token);
      }
      await persistSettings({ enabled: true });
      if (bridgeRunning) {
        stopBridge();
      }
      manuallyDisconnected = false;
      httpRetryDelay = 500;
      wsRetryDelay = 500;
      void startBridge();
      return { ok: true, state: publicState() };
    } catch (error) {
      const bridgeError = conciseError(error, "CONNECT_FAILED");
      return {
        ok: false,
        error: { code: bridgeError.code, message: bridgeError.message }
      };
    }
  }
  if (message.type === "disconnect") {
    manuallyDisconnected = true;
    stopBridge();
    settings.enabled = false;
    wsAuthenticated = false;
    httpAuthenticated = false;
    pendingRequests.clear();
    setConnectionStatus("disconnected", "");
    try {
      await persistSettings({ enabled: false });
    } catch {
      return { ok: true, state: publicState() };
    }
    return { ok: true, state: publicState() };
  }
  return undefined;
}

browser.runtime.onMessage.addListener((message) => {
  if (!message || message.type === "bridge-state") {
    return undefined;
  }
  if (
    message.type === "get-state" ||
    message.type === "get-config" ||
    message.type === "save-config" ||
    message.type === "connect" ||
    message.type === "disconnect"
  ) {
    return handleRuntimeMessage(message);
  }
  return undefined;
});

if (browser.tabs && browser.tabs.onUpdated && browser.tabs.onUpdated.addListener) {
  browser.tabs.onUpdated.addListener((tabId, changeInfo) => {
    if (!changeInfo) {
      return;
    }
    if (changeInfo.status === "loading" || typeof changeInfo.url === "string") {
      void invalidateRefs(tabId);
    }
  });
}

if (browser.tabs && browser.tabs.onRemoved && browser.tabs.onRemoved.addListener) {
  browser.tabs.onRemoved.addListener((tabId) => {
    snapshotTabs.delete(tabId);
  });
}

settingsReady = browser.storage.local.get(STORAGE_DEFAULTS).then((stored) => {
  const storedUrl = typeof stored.url === "string" ? stored.url : DEFAULT_URL;
  const storedToken = typeof stored.token === "string" ? stored.token : "";
  try {
    settings.url = validateUrl(storedUrl);
  } catch {
    settings.url = DEFAULT_URL;
  }
  settings.token = storedToken;
  settings.enabled = stored.enabled === true || (stored.enabled === undefined && Boolean(storedToken));
  manuallyDisconnected = !settings.enabled;
  broadcastState();
  if (settings.enabled) {
    void startBridge();
  }
}).catch(() => {
  settings = { ...STORAGE_DEFAULTS };
  manuallyDisconnected = true;
  setConnectionStatus("disconnected", "Could not load saved settings");
});

// Registered before the bridge starts so the event page is already protected
// while the first WebSocket handshake and the first poll are in flight.
startKeepalive();

void startBridge();
