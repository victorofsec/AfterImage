(() => {
  "use strict";

  const viewer = document.getElementById("viewer");
  const gate = document.getElementById("gate");
  const stage = document.getElementById("stage");
  const ended = document.getElementById("ended");
  const revealButton = document.getElementById("revealButton");
  const pledgeCheckbox = document.getElementById("pledgeCheckbox");
  const canvas = document.getElementById("imageCanvas");
  const countdown = document.getElementById("countdown");
  const progressBar = document.getElementById("progressBar");
  const fragmentLabel = document.getElementById("fragmentLabel");
  const endMessage = document.getElementById("endMessage");
  const messages = {
    opening: viewer.dataset.opening,
    alreadySeen: viewer.dataset.alreadySeen,
    sessionFailed: viewer.dataset.sessionFailed,
    timeExpired: viewer.dataset.timeExpired,
    pageHidden: viewer.dataset.pageHidden,
    windowBlurred: viewer.dataset.windowBlurred,
    destroyed: viewer.dataset.destroyed,
    preparing: viewer.dataset.preparing,
    frame: viewer.dataset.frame,
    fragment: viewer.dataset.fragment,
    fragments: viewer.dataset.fragments
  };

  let active = false;
  let finished = false;
  let deadline = 0;
  let timer = null;
  const fragments = [];

  const wait = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

  function renderFrame(visibleFragments) {
    const context = canvas.getContext("2d", { alpha: false });
    context.fillStyle = "#000";
    context.fillRect(0, 0, canvas.width, canvas.height);
    for (const fragment of visibleFragments) {
      context.drawImage(fragment.bitmap, fragment.x, fragment.y);
    }
  }

  function randomInteger(maximum) {
    const randomValue = new Uint32Array(1);
    crypto.getRandomValues(randomValue);
    return randomValue[0] % maximum;
  }

  function randomFrame(maxVisible) {
    const shuffled = [...fragments];
    for (let index = shuffled.length - 1; index > 0; index -= 1) {
      const target = randomInteger(index + 1);
      [shuffled[index], shuffled[target]] = [shuffled[target], shuffled[index]];
    }
    const visibleCount = 1 + randomInteger(Math.min(maxVisible, shuffled.length));
    return shuffled.slice(0, visibleCount);
  }

  function updateTimer() {
    const remaining = Math.max(0, deadline - performance.now());
    countdown.textContent = `${(remaining / 1000).toFixed(1)} s`;
    if (remaining <= 0) finish(messages.timeExpired);
  }

  function finish(message) {
    if (finished) return;
    finished = true;
    active = false;
    window.clearInterval(timer);
    canvas.width = 1;
    canvas.height = 1;
    for (const fragment of fragments) fragment.bitmap.close();
    fragments.length = 0;
    stage.hidden = true;
    gate.hidden = true;
    endMessage.textContent = message;
    ended.hidden = false;
  }

  async function downloadFragment(config, index) {
    const response = await fetch(`${config.frame_url}/${index}`, {
      cache: "no-store",
      credentials: "same-origin",
      headers: { "Authorization": `Bearer ${config.token}` }
    });
    if (!response.ok) throw new Error("fragment_unavailable");
    const blob = await response.blob();
    if (blob.type !== "image/png") throw new Error("invalid_fragment");
    const bitmap = await createImageBitmap(blob);
    return {
      bitmap,
      x: Number(response.headers.get("X-Fragment-X")),
      y: Number(response.headers.get("X-Fragment-Y"))
    };
  }

  async function play(config) {
    canvas.width = config.width;
    canvas.height = config.height;

    // Télécharge d'abord tous les fragments, sans commencer le temps de vision.
    // Le serveur impose malgré tout leur récupération unique et dans l'ordre.
    for (let index = 0; index < config.slice_count && active; index += 1) {
      const fragment = await downloadFragment(config, index);
      if (!active) {
        fragment.bitmap.close();
        return;
      }
      fragments.push(fragment);
      progressBar.style.width = `${((index + 1) / config.slice_count) * 100}%`;
      fragmentLabel.textContent = `${messages.preparing} ${String(index + 1).padStart(2, "0")} / ${config.slice_count}`;
    }

    deadline = performance.now() + config.duration_ms;
    timer = window.setInterval(updateTimer, 50);
    updateTimer();

    let frameNumber = 0;
    while (active && performance.now() < deadline) {
      const visibleFragments = randomFrame(config.max_visible);
      renderFrame(visibleFragments);
      frameNumber += 1;
      const fragmentWord = visibleFragments.length > 1 ? messages.fragments : messages.fragment;
      fragmentLabel.textContent = `${messages.frame} ${frameNumber} · ${visibleFragments.length} ${fragmentWord}`;
      await wait(config.frame_ms);
    }
    finish(messages.timeExpired);
  }

  async function begin() {
    if (active || finished) return;
    revealButton.disabled = true;
    revealButton.textContent = messages.opening;
    try {
      const response = await fetch(viewer.dataset.sessionUrl, {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pledge_accepted: true })
      });
      if (!response.ok) throw new Error(response.status === 410 ? "already_seen" : "session_failed");
      const config = await response.json();
      active = true;
      gate.hidden = true;
      stage.hidden = false;
      await play(config);
    } catch (error) {
      finish(error.message === "already_seen"
        ? messages.alreadySeen
        : messages.sessionFailed);
    }
  }

  revealButton.addEventListener("click", begin, { once: true });
  pledgeCheckbox.addEventListener("change", () => {
    revealButton.disabled = !pledgeCheckbox.checked;
  });
  document.addEventListener("visibilitychange", () => {
    if (active && document.hidden) finish(messages.pageHidden);
  });
  window.addEventListener("blur", () => {
    if (active) finish(messages.windowBlurred);
  });
  window.addEventListener("pagehide", () => {
    if (active) finish(messages.destroyed);
  });
  canvas.addEventListener("contextmenu", (event) => event.preventDefault());
})();
