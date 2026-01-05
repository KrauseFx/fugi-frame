const imgA = document.getElementById("img-a");
const imgB = document.getElementById("img-b");
const caption = document.getElementById("caption");
const status = document.getElementById("status");

let showingA = true;
let config = {
  change_interval_seconds: 120,
  transition_ms: 1200,
  fit_mode: "contain",
};

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

async function loadNext() {
  try {
    status.textContent = "Loading…";
    const data = await fetchJson("/api/next");
    const target = showingA ? imgB : imgA;
    target.onload = () => {
      target.classList.add("visible");
      const other = showingA ? imgA : imgB;
      other.classList.remove("visible");
      showingA = !showingA;
      updateCaption(data);
      status.textContent = "";
      setTimeout(loadNext, config.change_interval_seconds * 1000);
    };
    target.onerror = () => {
      status.textContent = "Image failed to load. Retrying…";
      setTimeout(loadNext, 5000);
    };
    target.src = data.url;
  } catch (err) {
    status.textContent = "Waiting for photos…";
    setTimeout(loadNext, 5000);
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
  await loadNext();
}

init();
