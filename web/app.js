const imgA = document.getElementById("img-a");
const imgB = document.getElementById("img-b");
const caption = document.getElementById("caption");
const status = document.getElementById("status");

let showingA = true;
let config = {
  change_interval_seconds: 120,
  transition_ms: 1200,
  fit_mode: "cover",
};
let nextTimer = null;
let isLoading = false;

async function fetchJson(path) {
  const resp = await fetch(path, { cache: "no-store" });
  if (!resp.ok) {
    throw new Error(`${path} failed: ${resp.status}`);
  }
  return resp.json();
}

function setFitMode(mode) {
  const value = mode === "cover" ? "cover" : "contain";
  imgA.style.objectFit = value;
  imgB.style.objectFit = value;
}

function updateCaption(data) {
  const parts = [];
  if (data.camera_make || data.camera_model) {
    parts.push([data.camera_make, data.camera_model].filter(Boolean).join(" "));
  }
  if (data.date) {
    const date = new Date(data.date);
    if (!Number.isNaN(date.getTime())) {
      parts.push(date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }));
    }
  }

  if (parts.length === 0) {
    caption.classList.add("hidden");
    caption.textContent = "";
    return;
  }

  caption.textContent = parts.join(" · ");
  caption.classList.remove("hidden");
}

function scheduleNext() {
  if (nextTimer) {
    clearTimeout(nextTimer);
  }
  nextTimer = setTimeout(() => {
    loadFrom("/api/next");
  }, config.change_interval_seconds * 1000);
}

function setStatus(message, clearAfterMs = 0) {
  status.textContent = message;
  if (clearAfterMs) {
    setTimeout(() => {
      if (status.textContent === message) {
        status.textContent = "";
      }
    }, clearAfterMs);
  }
}

async function loadFrom(endpoint) {
  if (isLoading) {
    return;
  }
  isLoading = true;
  try {
    setStatus("Loading…");
    const data = await fetchJson(endpoint);
    const target = showingA ? imgB : imgA;
    target.onload = () => {
      target.classList.add("visible");
      const other = showingA ? imgA : imgB;
      other.classList.remove("visible");
      showingA = !showingA;
      updateCaption(data);
      setStatus("");
      isLoading = false;
      scheduleNext();
    };
    target.onerror = () => {
      setStatus("Image failed to load. Retrying…");
      isLoading = false;
      setTimeout(() => loadFrom("/api/next"), 5000);
    };
    target.src = data.url;
  } catch (err) {
    if (endpoint === "/api/prev") {
      setStatus("No previous photo", 1500);
    } else {
      setStatus("Waiting for photos…");
    }
    isLoading = false;
    setTimeout(() => loadFrom("/api/next"), 5000);
  }
}

async function init() {
  try {
    config = await fetchJson("/api/config");
  } catch (err) {
    // Keep defaults.
  }
  setFitMode(config.fit_mode);
  imgA.style.transitionDuration = `${config.transition_ms}ms`;
  imgB.style.transitionDuration = `${config.transition_ms}ms`;
  await loadFrom("/api/next");
}

document.addEventListener("keydown", (event) => {
  if (event.key === "ArrowRight" || event.key === " ") {
    event.preventDefault();
    loadFrom("/api/next");
  } else if (event.key === "ArrowLeft") {
    event.preventDefault();
    loadFrom("/api/prev");
  }
});

init();
