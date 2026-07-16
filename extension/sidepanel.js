const $ = (id) => document.getElementById(id);

function setStatus(msg, cls = "muted") {
  const el = $("status");
  el.textContent = msg;
  el.className = cls;
}

async function loadContexts(base, token, preferred) {
  const res = await fetch(`${base}/api/contexts`, {
    headers: { "X-App-Token": token },
  });
  if (!res.ok) throw new Error(`Could not connect (${res.status})`);
  const { contexts } = await res.json();
  const select = $("context");
  select.innerHTML = "";
  if (!contexts.length) {
    const option = document.createElement("option");
    option.textContent = "Create a context in the app";
    option.value = "";
    select.append(option);
    select.disabled = true;
    return;
  }
  for (const context of contexts) {
    const option = document.createElement("option");
    option.value = context.context_id;
    option.textContent = context.name;
    if (context.context_id === preferred) option.selected = true;
    select.append(option);
  }
  select.disabled = false;
  await chrome.storage.local.set({ contextId: select.value });
}

// Load saved connection settings.
chrome.storage.local.get(["base", "token", "contextId"], async (data) => {
  if (data.base) $("base").value = data.base;
  if (data.token) $("token").value = data.token;
  if (data.base && data.token) {
    try {
      await loadContexts(data.base, data.token, data.contextId);
      setStatus("Connected.", "ok");
    } catch (e) {
      setStatus(String(e.message || e), "err");
    }
  }
});

$("pair").addEventListener("click", async () => {
  const base = $("base").value.trim().replace(/\/$/, "");
  const token = $("token").value.trim();
  if (!base || !token) {
    setStatus("Enter the address and token from Settings → Advanced.", "err");
    return;
  }
  try {
    await loadContexts(base, token);
    await chrome.storage.local.set({ base, token });
    setStatus("Connected.", "ok");
  } catch (e) {
    setStatus(String(e.message || e), "err");
  }
});

$("context").addEventListener("change", () => {
  chrome.storage.local.set({ contextId: $("context").value });
});

// Extract page content in the tab's context (no server-side fetching).
function extractPage() {
  const clone = document.body ? document.body.cloneNode(true) : null;
  if (clone) {
    clone.querySelectorAll("script,style,noscript,svg").forEach((n) => n.remove());
  }
  return {
    title: document.title || "",
    text: (clone ? clone.innerText : document.documentElement.innerText || "").slice(0, 200000),
    url: location.href,
  };
}

$("save").addEventListener("click", async () => {
  const { base, token, contextId } = await chrome.storage.local.get([
    "base",
    "token",
    "contextId",
  ]);
  if (!base || !token || !contextId) {
    setStatus("Connect to the app and choose a context first.", "err");
    return;
  }
  setStatus("Reading page…");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) throw new Error("No active tab");
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractPage,
    });
    setStatus("Saving to your context…");
    const res = await fetch(`${base}/api/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-App-Token": token },
      body: JSON.stringify({ ...result, context_id: contextId }),
    });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    const data = await res.json();
    setStatus(
      data.status === "already_saved"
        ? "Already saved."
        : "Saved. Prepare the context again to include it offline.",
      "ok"
    );
  } catch (e) {
    setStatus(String(e.message || e), "err");
  }
});
