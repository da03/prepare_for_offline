const $ = (id) => document.getElementById(id);

function setStatus(msg, cls = "muted") {
  const el = $("status");
  el.textContent = msg;
  el.className = cls;
}

// Load saved connection settings.
chrome.storage.local.get(["base", "token"], (data) => {
  if (data.base) $("base").value = data.base;
  if (data.token) $("token").value = data.token;
});

$("pair").addEventListener("click", () => {
  const base = $("base").value.trim().replace(/\/$/, "");
  const token = $("token").value.trim();
  chrome.storage.local.set({ base, token }, () => {
    setStatus("Connection saved.", "ok");
  });
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
  const { base, token } = await chrome.storage.local.get(["base", "token"]);
  if (!base || !token) {
    setStatus("Set the app address and token first.", "err");
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
    setStatus("Sending to local pack…");
    const res = await fetch(`${base}/api/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-App-Token": token },
      body: JSON.stringify(result),
    });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    const data = await res.json();
    setStatus(
      data.status === "already_saved" ? "Already in your pack." : "Saved to your offline pack.",
      "ok"
    );
  } catch (e) {
    setStatus(String(e.message || e), "err");
  }
});
