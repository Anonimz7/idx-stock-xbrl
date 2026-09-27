"use strict";

// Loads the extension background scripts the way Firefox does: every entry of
// `background.scripts` is evaluated as a classic script in ONE shared global
// scope. A redeclared top-level binding is a fatal SyntaxError in Firefox, so
// `node --check` cannot catch it. This harness reproduces the shared scope and
// reports whether the background page registered its runtime message listener.
//
// Usage: node tests/extension_harness.js <extension-dir>

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const extensionDir = process.argv[2];
if (!extensionDir) {
  console.error("usage: node tests/extension_harness.js <extension-dir>");
  process.exit(2);
}

const messageListeners = [];
const noop = () => {};
const registeredIntervals = [];

// The real setInterval would keep this process alive forever. Recording the
// registration is enough: it proves the keepalive was armed and with which
// period, which is the whole point of the check.
const recordInterval = (handler, ms) => {
  registeredIntervals.push(ms);
  return registeredIntervals.length;
};

const sandbox = {
  console,
  setTimeout,
  clearTimeout,
  setInterval: recordInterval,
  clearInterval: noop,
  URL,
  URLSearchParams,
  TextEncoder,
  Promise,
  Math,
  JSON,
  String,
  Number,
  Boolean,
  Object,
  Array,
  Set,
  Map,
  Error,
  TypeError,
  RegExp,
  Date,
  fetch: () => Promise.reject(new Error("harness: fetch is not available")),
  // Never connects: the harness only proves the client exists and is usable.
  WebSocket: class HarnessWebSocket {
    constructor(url) {
      this.url = url;
      this.readyState = 0;
    }

    addEventListener() {}

    send() {
      throw new Error("harness: socket is not open");
    }

    close() {}
  },
  browser: {
    runtime: {
      onMessage: { addListener: (fn) => messageListeners.push(fn) },
      sendMessage: () => Promise.resolve(undefined),
      getPlatformInfo: () => Promise.resolve({ os: "harness" }),
    },
    storage: {
      local: {
        get: () => Promise.resolve({}),
        set: () => Promise.resolve(),
      },
    },
    tabs: {
      onUpdated: { addListener: noop },
      onRemoved: { addListener: noop },
    },
  },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

const manifest = JSON.parse(
  fs.readFileSync(path.join(extensionDir, "manifest.json"), "utf8")
);
const scripts = (manifest.background && manifest.background.scripts) || [];

for (const name of scripts) {
  const source = fs.readFileSync(path.join(extensionDir, name), "utf8");
  vm.runInContext(source, sandbox, { filename: name });
}

const pageHelpers = ["snapshotPage", "clickPage", "fillPage", "downloadPage"];
const report = {
  scripts,
  messageListenerCount: messageListeners.length,
  pageHelpersPresent: pageHelpers.filter((name) => typeof sandbox[name] === "function"),
  intervalPeriodsMs: registeredIntervals,
  transports: {
    webSocketUrl: typeof sandbox.webSocketUrl === "function" ? sandbox.webSocketUrl() : null,
    httpBridgeUrl:
      typeof sandbox.httpBridgeUrl === "function" ? sandbox.httpBridgeUrl("/extension/poll") : null
  }
};

console.log(JSON.stringify(report));

if (messageListeners.length !== 1) {
  console.error(`expected exactly 1 runtime message listener, got ${messageListeners.length}`);
  process.exit(1);
}
if (report.pageHelpersPresent.length !== pageHelpers.length) {
  const missing = pageHelpers.filter((name) => typeof sandbox[name] !== "function");
  console.error(`page helpers failed to load: ${missing.join(", ")}`);
  process.exit(1);
}
if (registeredIntervals.length === 0) {
  console.error("no interval was registered: the event page keepalive is missing");
  process.exit(1);
}
