"use strict";

// Every helper in this file is injected into a page through
// scripting.executeScript({ func }), which serializes the function body only.
// Nothing at the top level of this file is available in the page, and this file
// shares one scope with background.js, so all state must stay function-local.

function snapshotPage(maxElements) {
  // Keep in sync with MAX_SNAPSHOT_ELEMENTS in background.js.
  const elementLimit = 2000;
  const limit = Math.max(1, Math.min(Number(maxElements) || 1, elementLimit));
  const roots = [document];
  for (let index = 0; index < roots.length && roots.length < 64; index += 1) {
    const descendants = roots[index].querySelectorAll("*");
    for (const element of descendants) {
      if (element.shadowRoot && roots.length < 64) {
        roots.push(element.shadowRoot);
      }
    }
  }
  const queryAll = (selector) => {
    const matches = [];
    for (const root of roots) {
      for (const element of root.querySelectorAll(selector)) {
        matches.push(element);
      }
    }
    return matches;
  };
  for (const element of queryAll("[data-firefox-bridge-ref]")) {
    element.removeAttribute("data-firefox-bridge-ref");
  }

  const clean = (value, maximum = 400) => {
    if (value === null || value === undefined) {
      return "";
    }
    return String(value).replace(/\s+/g, " ").trim().slice(0, maximum);
  };
  const visible = (element) => {
    if (element.hidden || element.getAttribute("aria-hidden") === "true") {
      return false;
    }
    if ("isConnected" in element && !element.isConnected) {
      return false;
    }
    const view = element.ownerDocument && element.ownerDocument.defaultView
      ? element.ownerDocument.defaultView
      : window;
    let style;
    try {
      style = view.getComputedStyle(element);
    } catch {
      return false;
    }
    if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse") {
      return false;
    }
    if (style.opacity === "0") {
      return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const headingLevel = (element) => {
    const match = /^H([1-6])$/.exec(element.tagName || "");
    return match ? Number(match[1]) : 0;
  };
  const roleFor = (element) => {
    const explicit = clean(element.getAttribute("role"), 80);
    if (explicit) {
      return explicit.split(/\s+/)[0];
    }
    const tag = element.tagName.toLowerCase();
    if (tag === "a" && element.hasAttribute("href")) {
      return "link";
    }
    if (tag === "button" || tag === "summary") {
      return "button";
    }
    if (tag === "textarea" || element.isContentEditable) {
      return "textbox";
    }
    if (tag === "select") {
      return element.multiple ? "listbox" : "combobox";
    }
    if (tag === "option") {
      return "option";
    }
    if (tag === "input") {
      const type = (element.type || "text").toLowerCase();
      if (type === "checkbox") {
        return "checkbox";
      }
      if (type === "radio") {
        return "radio";
      }
      if (type === "range") {
        return "slider";
      }
      if (type === "number") {
        return "spinbutton";
      }
      if (type === "search") {
        return "searchbox";
      }
      if (["button", "submit", "reset", "image", "file"].includes(type)) {
        return "button";
      }
      return "textbox";
    }
    return "generic";
  };
  const interactive = (element) => {
    const tag = element.tagName.toLowerCase();
    if (["a", "button", "input", "textarea", "select", "summary", "option"].includes(tag)) {
      return !(tag === "a" && !element.hasAttribute("href"));
    }
    if (tag === "audio" || tag === "video") {
      return element.hasAttribute("controls");
    }
    if (element.isContentEditable || element.getAttribute("contenteditable") === "") {
      return true;
    }
    const role = roleFor(element);
    if ([
      "button", "link", "checkbox", "radio", "switch", "tab",
      "menuitem", "menuitemcheckbox", "menuitemradio", "option",
      "textbox", "searchbox", "combobox", "listbox", "slider", "spinbutton"
    ].includes(role)) {
      return true;
    }
    if (element.hasAttribute("onclick")) {
      return true;
    }
    const tabIndex = element.getAttribute("tabindex");
    return tabIndex !== null && Number(tabIndex) >= 0;
  };
  const nameFor = (element) => {
    const ariaLabel = clean(element.getAttribute("aria-label"));
    if (ariaLabel) {
      return ariaLabel;
    }
    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy) {
      const labels = [];
      for (const id of labelledBy.split(/\s+/)) {
        const label = element.ownerDocument.getElementById(id);
        if (label) {
          labels.push(label.textContent || "");
        }
      }
      const named = clean(labels.join(" "));
      if (named) { return named; }
    }
    if (element.labels) {
      const labels = [];
      for (const label of element.labels) {
        labels.push(label.textContent || "");
      }
      const named = clean(labels.join(" "));
      if (named) { return named; }
    }
    const alt = clean(element.getAttribute("alt"));
    if (alt) { return alt; }
    const title = clean(element.getAttribute("title"));
    if (title) { return title; }
    const tag = element.tagName.toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") {
      const placeholder = clean(element.getAttribute("placeholder"));
      if (placeholder) { return placeholder; }
    }
    let text = "";
    try {
      text = element.innerText || element.textContent || "";
    } catch {
      text = element.textContent || "";
    }
    return clean(text);
  };
  const valueFor = (element) => {
    const tag = element.tagName.toLowerCase();
    if (tag === "input") {
      if ((element.type || "").toLowerCase() === "file") { return ""; }
      return clean(element.value, 500);
    }
    if (tag === "textarea" || tag === "button") {
      return clean(element.value, 500);
    }
    if (tag === "select") {
      if (element.multiple) {
        return Array.from(element.selectedOptions || [])
          .map((option) => clean(option.value, 500))
          .slice(0, 20);
      }
      return clean(element.value, 500);
    }
    return undefined;
  };
  const stateFor = (element) => {
    const state = {};
    const tag = element.tagName.toLowerCase();
    if (tag === "input" && typeof element.checked === "boolean") { state.checked = element.checked; }
    if (tag === "option" && typeof element.selected === "boolean") { state.selected = element.selected; }
    if (typeof element.disabled === "boolean") { state.disabled = element.disabled; }
    if (typeof element.readOnly === "boolean") { state.readOnly = element.readOnly; }
    if (typeof element.required === "boolean") { state.required = element.required; }
    const expanded = element.getAttribute("aria-expanded");
    if (expanded === "true" || expanded === "false") { state.expanded = expanded === "true"; }
    const pressed = element.getAttribute("aria-pressed");
    if (pressed === "true" || pressed === "false" || pressed === "mixed") { state.pressed = pressed; }
    return state;
  };
  const depthFor = (element) => {
    let depth = 0;
    let parent = element.parentElement;
    while (parent && depth < 64) { depth += 1; parent = parent.parentElement; }
    return depth;
  };

  const selector = [
    "h1","h2","h3","h4","h5","h6","a[href]","button","input","textarea",
    "select","option","summary","audio[controls]","video[controls]",
    "[contenteditable]","[role]","[onclick]","[tabindex]:not([tabindex='-1'])"
  ].join(",");
  const candidates = queryAll(selector);
  const seen = new Set();
  const elements = [];
  let truncated = false;
  for (const element of candidates) {
    if (seen.has(element)) { continue; }
    seen.add(element);
    const level = headingLevel(element);
    if (!level && !interactive(element)) { continue; }
    if (!visible(element)) { continue; }
    if (elements.length >= limit) { truncated = true; break; }
    const ref = `e${elements.length + 1}`;
    element.setAttribute("data-firefox-bridge-ref", ref);
    const item = {
      ref,
      role: level ? "heading" : roleFor(element),
      name: nameFor(element),
      tag: element.tagName.toLowerCase(),
      depth: depthFor(element)
    };
    if (level) { item.level = level; }
    if (element.tagName.toLowerCase() === "input") {
      item.type = (element.type || "text").toLowerCase();
    } else if (element.tagName.toLowerCase() === "button") {
      item.type = (element.getAttribute("type") || "submit").toLowerCase();
    }
    const value = valueFor(element);
    if (value !== undefined) { item.value = value; }
    const state = stateFor(element);
    if (Object.keys(state).length > 0) { item.state = state; }
    if (element.hasAttribute("href")) {
      item.href = clean(element.href || element.getAttribute("href"), 2048);
    }
    elements.push(item);
  }
  return {
    url: String(window.location.href).slice(0, 4096),
    title: clean(document.title, 512),
    elements,
    truncated
  };
}

function clickPage(ref) {
  const findElement = () => {
    const roots = [document];
    for (let index = 0; index < roots.length && roots.length < 64; index += 1) {
      const descendants = roots[index].querySelectorAll("*");
      for (const element of descendants) {
        if (element.shadowRoot && roots.length < 64) { roots.push(element.shadowRoot); }
      }
    }
    for (const root of roots) {
      for (const element of root.querySelectorAll("[data-firefox-bridge-ref]")) {
        if (element.getAttribute("data-firefox-bridge-ref") === ref) { return element; }
      }
    }
    return null;
  };
  const element = findElement();
  if (!element || ("isConnected" in element && !element.isConnected)) {
    return { error: { code: "ELEMENT_NOT_FOUND", message: "Snapshot reference not found" } };
  }
  try { element.scrollIntoView({ block: "center", inline: "center" }); } catch { /* ignore */ }
  element.click();
  return { clicked: true, ref };
}

function fillPage(ref, value) {
  const findElement = () => {
    const roots = [document];
    for (let index = 0; index < roots.length && roots.length < 64; index += 1) {
      const descendants = roots[index].querySelectorAll("*");
      for (const element of descendants) {
        if (element.shadowRoot && roots.length < 64) { roots.push(element.shadowRoot); }
      }
    }
    for (const root of roots) {
      for (const element of root.querySelectorAll("[data-firefox-bridge-ref]")) {
        if (element.getAttribute("data-firefox-bridge-ref") === ref) { return element; }
      }
    }
    return null;
  };
  const element = findElement();
  if (!element || ("isConnected" in element && !element.isConnected)) {
    return { error: { code: "ELEMENT_NOT_FOUND", message: "Snapshot reference not found" } };
  }
  element.focus();
  element.value = value;
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  return { filled: true, ref };
}

function textPage(maxChars) {
  const limit = Math.max(1, Math.min(Number(maxChars) || 1, 100000));
  const root = document.body || document.documentElement;
  let text = "";
  if (root) {
    try { text = typeof root.innerText === "string" ? root.innerText : root.textContent || ""; }
    catch { text = root.textContent || ""; }
  }
  const normalized = String(text).replace(/\s+/g, " ").trim();
  return { text: normalized.slice(0, limit), truncated: normalized.length > limit };
}

function downloadPage(ref, filename) {
  const findElement = () => {
    const roots = [document];
    for (let index = 0; index < roots.length && roots.length < 64; index += 1) {
      const descendants = roots[index].querySelectorAll("*");
      for (const element of descendants) {
        if (element.shadowRoot && roots.length < 64) { roots.push(element.shadowRoot); }
      }
    }
    for (const root of roots) {
      for (const element of root.querySelectorAll("[data-firefox-bridge-ref]")) {
        if (element.getAttribute("data-firefox-bridge-ref") === ref) { return element; }
      }
    }
    return null;
  };
  const element = findElement();
  if (!element || ("isConnected" in element && !element.isConnected)) {
    return { error: { code: "ELEMENT_NOT_FOUND", message: "Snapshot reference not found" } };
  }
  const href = String(element.href || element.getAttribute("href") || "").trim();
  if (!href) {
    return { error: { code: "NO_HREF", message: "Element does not have a downloadable href" } };
  }
  const cleanFilename = typeof filename === "string" && filename.trim()
    ? String(filename.trim())
    : String(element.getAttribute("download") || href.split("/").pop() || "download");
  return { download: true, ref, href, filename: cleanFilename };
}

function selectDropdownPage(ref, value) {
  const findElement = () => {
    const roots = [document];
    for (let index = 0; index < roots.length && roots.length < 64; index += 1) {
      const descendants = roots[index].querySelectorAll("*");
      for (const element of descendants) {
        if (element.shadowRoot && roots.length < 64) { roots.push(element.shadowRoot); }
      }
    }
    for (const root of roots) {
      for (const element of root.querySelectorAll("[data-firefox-bridge-ref]")) {
        if (element.getAttribute("data-firefox-bridge-ref") === ref) { return element; }
      }
    }
    return null;
  };
  const clean = (v) => {
    if (v === null || v === undefined) { return ""; }
    return String(v).replace(/\s+/g, " ").trim();
  };
  const dispatch = (element, type) => {
    try { element.dispatchEvent(new Event(type, { bubbles: true, cancelable: true })); } catch { /* ignore */ }
  };
  const element = findElement();
  if (!element || ("isConnected" in element && !element.isConnected)) {
    return { error: { code: "ELEMENT_NOT_FOUND", message: "Snapshot reference not found" } };
  }
  const cleanValue = clean(value);
  const container = element.closest(".vs") || element.parentElement;
  // Step 1: open the dropdown by clicking the toggle.
  try { element.click(); } catch {
    return { error: { code: "CLICK_FAILED", message: "Dropdown toggle could not be clicked" } };
  }
  // Step 2: type the value in the search input (for searchable v-select).
  let searchInput = null;
  if (container) { searchInput = container.querySelector("input.vs__search"); }
  if (!searchInput) { searchInput = element.querySelector("input.vs__search"); }
  if (!searchInput && container) {
    const inputs = container.querySelectorAll("input[type=search]");
    searchInput = inputs[inputs.length - 1] || null;
  }
  if (searchInput) {
    dispatch(searchInput, "focus");
    searchInput.value = cleanValue;
    dispatch(searchInput, "input");
    dispatch(searchInput, "change");
    dispatch(searchInput, "keydown");
  }
  // Step 3: find and click the matching option (synchronous, fast).
  // After typing, v-select filters options synchronously via the input event.
  let clickedOption = false;
  let optionText = "";
  if (container) {
    const options = container.querySelectorAll("[role=option], .vs__option");
    for (const option of options) {
      const text = clean(option.textContent);
      const val = clean(option.getAttribute("data-value") || option.getAttribute("value") || "");
      if (text === cleanValue || val === cleanValue) {
        try { option.scrollIntoView({ block: "center", inline: "center" }); } catch { /* ignore */ }
        try { option.click(); clickedOption = true; optionText = clean(option.textContent); } catch { /* ignore */ }
        break;
      }
    }
  }
  if (!clickedOption) {
    // Fallback: search entire document for the option.
    const allOptions = document.querySelectorAll("[role=option], .vs__option");
    for (const option of allOptions) {
      const text = clean(option.textContent);
      const val = clean(option.getAttribute("data-value") || option.getAttribute("value") || "");
      if (text === cleanValue || val === cleanValue) {
        try { option.scrollIntoView({ block: "center", inline: "center" }); } catch { /* ignore */ }
        try { option.click(); clickedOption = true; optionText = clean(option.textContent); } catch { /* ignore */ }
        break;
      }
    }
  }
  return {
    opened: true,
    value: cleanValue,
    has_search: !!searchInput,
    option_clicked: clickedOption,
    option_text: optionText
  };
}

function selectDropdownOptionPage(containerRef, optionText) {
  // Find the v-select container via its ref (assigned during snapshot).
  const findElement = () => {
    const roots = [document];
    for (let index = 0; index < roots.length && roots.length < 64; index += 1) {
      const descendants = roots[index].querySelectorAll("*");
      for (const element of descendants) {
        if (element.shadowRoot && roots.length < 64) { roots.push(element.shadowRoot); }
      }
    }
    for (const root of roots) {
      for (const element of root.querySelectorAll("[data-firefox-bridge-ref]")) {
        if (element.getAttribute("data-firefox-bridge-ref") === containerRef) {
          return element;
        }
      }
    }
    return null;
  };
  const clean = (v) => {
    if (v === null || v === undefined) { return ""; }
    return String(v).replace(/\s+/g, " ").trim();
  };
  const element = findElement();
  if (!element || (!element.isConnected && ("isConnected" in element))) {
    return { error: { code: "ELEMENT_NOT_FOUND", message: "Container ref not found" } };
  }
  // Search within the v-select container (or the closest .vs ancestor) for
  // a visible option whose text matches.
  const container = element.closest(".vs") || element.parentElement || element;
  const cleanTarget = clean(optionText);
  const options = container.querySelectorAll("[role=option], .vs__option");
  for (const option of options) {
    const text = clean(option.textContent);
    const val = clean(option.getAttribute("data-value") || option.getAttribute("value") || "");
    if (text === cleanTarget || val === cleanTarget) {
      try {
        option.scrollIntoView({ block: "center", inline: "center" });
      } catch { /* ignore */ }
      option.click();
      return { clicked: true, text: clean(option.textContent) };
    }
  }
  return { error: { code: "OPTION_NOT_FOUND", message: "No matching dropdown option" } };
}
