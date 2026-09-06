const DATA_URL = "data/current.json";

const listEl = document.getElementById("ranking-list");
const weekLabelEl = document.getElementById("week-label");
const replayBtn = document.getElementById("replay-btn");
const searchInput = document.getElementById("search-input");
const emptyStateEl = document.getElementById("empty-state");

const rowsByName = new Map();
let players = [];
let previousOrder = [];
let filterText = "";
let isReplaying = false;

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Flag emoji don't render on Windows/Chrome, so we serve real flag images
// (flagcdn) and fall back to the ISO code when a country can't be resolved.
function flagMarkup(iso2, countryName) {
  const label = countryName || "Unknown";
  if (!iso2 || iso2.length !== 2) {
    return `<span class="row-flag row-flag--none" title="${escapeAttr(label)}">–</span>`;
  }
  const code = iso2.toLowerCase();
  return `<img class="row-flag" loading="lazy" width="30" height="20"
    src="https://flagcdn.com/w40/${code}.png"
    srcset="https://flagcdn.com/w80/${code}.png 2x"
    alt="${escapeAttr(label)}" title="${escapeAttr(label)}">`;
}

function hashHue(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return hash % 360;
}

function avatarMarkup(player) {
  if (player.avatar) {
    return `<img class="row-avatar" src="${escapeAttr(player.avatar)}" alt="" loading="lazy">`;
  }
  const hue = hashHue(player.name);
  const letter = player.name.trim().charAt(0).toUpperCase() || "?";
  const bg = `hsl(${hue}, 55%, 32%)`;
  const fg = `hsl(${hue}, 70%, 88%)`;
  return `<span class="row-avatar-fallback" style="background:${bg};color:${fg};">${escapeHtml(letter)}</span>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function escapeAttr(s) {
  return escapeHtml(s);
}

function moveInfo(player) {
  const { status, move } = player;
  if (status === "new") {
    return { html: `<span class="move-new">NEW</span>`, aria: "New entry" };
  }
  if (status === "return") {
    return { html: `<span class="move-return">RE</span>`, aria: "Returning entry" };
  }
  if (status === "up") {
    return {
      html: `<span class="move-up move-badge"><span class="arrow">▲</span><span class="move-count" data-count-target="${move}">0</span></span>`,
      aria: `Up ${move} place${move === 1 ? "" : "s"}`,
    };
  }
  if (status === "down") {
    const abs = Math.abs(move);
    return {
      html: `<span class="move-down move-badge"><span class="arrow">▼</span><span class="move-count" data-count-target="${abs}">0</span></span>`,
      aria: `Down ${abs} place${abs === 1 ? "" : "s"}`,
    };
  }
  return { html: `<span class="move-same move-badge">=</span>`, aria: "Unchanged" };
}

function buildRow(player) {
  const li = document.createElement("li");
  li.className = "row";
  li.dataset.name = player.name;

  if (player.rank > 3 && (player.rank - 1) % 10 === 0) {
    li.classList.add("row--decade");
    li.dataset.decadeLabel = `${player.rank}–${player.rank + 9}`;
  }

  const move = moveInfo(player);

  li.innerHTML = `
    <span class="row-num">${player.rank}</span>
    ${avatarMarkup(player)}
    <span class="row-name">${escapeHtml(player.name)}</span>
    <span class="row-country">${flagMarkup(player.country, player.country_name)}</span>
    <span class="row-move" role="text" aria-label="${escapeAttr(move.aria)}">${move.html}</span>
  `;
  return li;
}

function render() {
  listEl.innerHTML = "";
  rowsByName.clear();

  for (const player of players) {
    const row = buildRow(player);
    rowsByName.set(player.name, row);
    listEl.appendChild(row);
  }

  applyFilter();
  observeEntrances();
}

function applyFilter() {
  let visibleCount = 0;
  for (const player of players) {
    const row = rowsByName.get(player.name);
    const visible = !filterText || player.name.toLowerCase().includes(filterText);
    row.hidden = !visible;
    if (visible) visibleCount++;
  }
  emptyStateEl.hidden = visibleCount !== 0;
}

let observer;
function observeEntrances() {
  if (observer) observer.disconnect();
  const reduced = prefersReducedMotion();
  if (reduced) {
    rowsByName.forEach((row) => {
      row.classList.add("is-visible");
      row.querySelectorAll("[data-count-target]").forEach((el) => {
        el.textContent = el.dataset.countTarget;
      });
    });
    return;
  }

  observer = new IntersectionObserver((entries) => {
    const toReveal = entries.filter((e) => e.isIntersecting && !e.target.classList.contains("is-visible"));
    toReveal.forEach((entry, i) => {
      const row = entry.target;
      row.style.transitionDelay = `${(i % 10) * 30}ms`;
      row.classList.add("is-visible");
      row.querySelectorAll("[data-count-target]").forEach((el) => animateCount(el));
      observer.unobserve(row);
    });
  }, { rootMargin: "0px 0px -5% 0px", threshold: 0.15 });

  rowsByName.forEach((row) => observer.observe(row));
}

function animateCount(el) {
  const target = Number(el.dataset.countTarget);
  if (!target) { el.textContent = "0"; return; }
  const duration = 450 + Math.min(target, 20) * 5;
  const start = performance.now();
  function tick(now) {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = String(Math.round(eased * target));
    if (t < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

function reorderTo(orderedPlayers) {
  for (const player of orderedPlayers) {
    const row = rowsByName.get(player.name);
    if (row) listEl.appendChild(row);
  }
}

function setRowNumbers(usePrev) {
  players.forEach((p) => {
    const row = rowsByName.get(p.name);
    const span = row && row.querySelector(".row-num");
    if (!span) return;
    span.textContent = usePrev && p.prev_rank != null ? p.prev_rank : p.rank;
  });
}

async function replayWeek() {
  if (isReplaying || previousOrder.length === 0) return;
  isReplaying = true;
  replayBtn.disabled = true;
  replayBtn.classList.add("is-spinning");
  listEl.classList.add("is-replaying");

  const fadeNames = new Set(
    players.filter((p) => p.prev_rank == null).map((p) => p.name)
  );

  const reduced = prefersReducedMotion();

  // Step 1: snap instantly to last week's order (and last week's numbers).
  reorderTo(previousOrder);
  setRowNumbers(true);
  fadeNames.forEach((name) => rowsByName.get(name)?.classList.add("row--pending"));

  if (reduced) {
    await wait(300);
    reorderTo(players);
    setRowNumbers(false);
    fadeNames.forEach((name) => rowsByName.get(name)?.classList.remove("row--pending"));
    finishReplay();
    return;
  }

  await wait(600);

  const firstRects = new Map();
  rowsByName.forEach((el, name) => firstRects.set(name, el.getBoundingClientRect()));

  reorderTo(players);
  setRowNumbers(false);

  rowsByName.forEach((el, name) => {
    if (fadeNames.has(name)) return;
    const first = firstRects.get(name);
    const last = el.getBoundingClientRect();
    if (!first) return;
    const dx = first.left - last.left;
    const dy = first.top - last.top;
    el.style.transition = "none";
    el.style.transform = (dx || dy) ? `translate(${dx}px, ${dy}px)` : "";
    if (dy > 4) el.classList.add("row--rising");
  });

  void listEl.offsetHeight; // force reflow

  rowsByName.forEach((el, name) => {
    if (fadeNames.has(name)) return;
    el.style.transition = `transform 900ms var(--ease)`;
    el.style.transform = "";
  });

  await wait(950);

  rowsByName.forEach((el) => {
    el.style.transition = "";
    el.style.transform = "";
    el.classList.remove("row--rising");
  });

  fadeNames.forEach((name) => rowsByName.get(name)?.classList.remove("row--pending"));

  await wait(450);
  finishReplay();
}

function finishReplay() {
  isReplaying = false;
  replayBtn.disabled = previousOrder.length === 0;
  replayBtn.classList.remove("is-spinning");
  listEl.classList.remove("is-replaying");
}

function computePreviousOrder() {
  return players
    .filter((p) => p.prev_rank != null)
    .sort((a, b) => a.prev_rank - b.prev_rank);
}

function initControls() {
  searchInput.addEventListener("input", () => {
    filterText = searchInput.value.trim().toLowerCase();
    applyFilter();
  });

  replayBtn.addEventListener("click", replayWeek);
}

async function init() {
  initControls();
  try {
    const res = await fetch(DATA_URL);
    const data = await res.json();
    players = data.players;
    weekLabelEl.textContent = data.week_label;
    previousOrder = computePreviousOrder();
    replayBtn.disabled = previousOrder.length === 0;
    render();
  } catch (err) {
    weekLabelEl.textContent = "Could not load ranking data.";
    console.error(err);
  }
}

init();
