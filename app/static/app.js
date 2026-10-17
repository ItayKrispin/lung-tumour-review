const caseList = document.querySelector("#case-list");
const timerEl = document.querySelector("#timer");
const toolImage = document.querySelector("#tool-image");
const expertImage = document.querySelector("#expert-image");
const toolCanvas = document.querySelector("#tool-canvas");
const expertCanvas = document.querySelector("#expert-canvas");
const sliceInput = document.querySelector("#slice");
const sliceLabel = document.querySelector("#slice-label");
const visitMeta = document.querySelector("#visit-meta");
const noduleList = document.querySelector("#nodules");
const exportButton = document.querySelector("#export");
const startOverButton = document.querySelector("#start-over");
const resetConfirm = document.querySelector("#reset-confirm");
const messageEl = document.querySelector("#message");
const radiusInput = document.querySelector("#radius");

const state = {
  cases: [],
  caseId: null,
  meta: null,
  slice: 0,
  nodules: [],
  expert: {},
  selected: null,
  mode: "propose",
  elapsedBase: 0,
  elapsedAt: 0,
  drawing: null,
  exportChecked: new Set(),
  exportable: new Map(),
  declined: new Set(),
  showExpert: true,
};

sliceInput.addEventListener("input", () => {
  state.slice = Number(sliceInput.value);
  showSlice();
});

document.querySelector("#mode-propose").onclick = () => setMode("propose");
document.querySelector("#mode-paint").onclick = () => setMode("paint");
document.querySelector("#mode-erase").onclick = () => setMode("erase");
exportButton.onclick = () => exportChecked();
startOverButton.onclick = () => {
  if (!state.caseId || startOverButton.disabled) return;
  startOverButton.hidden = true;
  resetConfirm.hidden = false;
};
document.querySelector("#reset-no").onclick = () => hideResetPrompt();
document.querySelector("#reset-yes").onclick = () => confirmReset();
document.querySelector("#show-expert").onchange = () => {
  state.showExpert = document.querySelector("#show-expert").checked;
  applyExpertVisibility();
  showSlice();
  renderNodules();
};
document.querySelector("#timepoint").onchange = () => saveTimepoint();

toolCanvas.addEventListener("pointerdown", onPointerDown);
toolCanvas.addEventListener("pointermove", onPointerMove);
window.addEventListener("pointerup", onPointerUp);
window.addEventListener("keydown", (event) => {
  if (!state.meta || event.target.matches("input, select, textarea")) return;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") changeSlice(1);
  if (event.key === "ArrowLeft" || event.key === "ArrowUp") changeSlice(-1);
});
setInterval(paintTimer, 500);

loadCases();

async function loadCases() {
  state.cases = await getJSON("/api/cases");
  renderCaseList();
}

function renderCaseList() {
  caseList.replaceChildren();
  const practice = state.cases.filter((item) => item.source !== "research");
  const research = state.cases.filter((item) => item.source === "research");
  appendSection("Practice set", practice);
  const researchTitle = document.createElement("h3");
  researchTitle.className = "section-label";
  researchTitle.textContent = "Research scans";
  caseList.append(researchTitle);
  const note = document.createElement("p");
  note.className = "meta";
  note.textContent = research.length
    ? "These scans have no expert outline."
    : "Drop a DICOM series into data/inbox, then rescan.";
  caseList.append(note);
  const rescan = document.createElement("button");
  rescan.type = "button";
  rescan.textContent = "Rescan inbox";
  rescan.onclick = () => rescanInbox();
  caseList.append(rescan);
  appendPatientGroups(research);
  highlightCurrentVisit();
}

function appendSection(label, visits) {
  const title = document.createElement("h3");
  title.className = "section-label";
  title.textContent = label;
  caseList.append(title);
  appendPatientGroups(visits);
}

function appendPatientGroups(visits) {
  const groups = new Map();
  for (const item of visits) {
    if (!groups.has(item.patient_id)) groups.set(item.patient_id, []);
    groups.get(item.patient_id).push(item);
  }
  for (const [patient, patientVisits] of groups) {
    const patientTitle = document.createElement("p");
    patientTitle.className = "patient";
    patientTitle.textContent = patient;
    caseList.append(patientTitle);
    appendVisits(patientVisits);
  }
}

function appendVisits(visits) {
  for (const visit of visits) {
    if (visit.exportable) state.exportable.set(visit.id, true);
    if (visit.exportable && !state.declined.has(visit.id)) state.exportChecked.add(visit.id);
    const row = document.createElement("div");
    row.className = "visit-row";
    row.dataset.caseId = visit.id;
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = state.exportChecked.has(visit.id);
    box.setAttribute("aria-label", `Include ${visit.timepoint} in the export`);
    box.addEventListener("change", () => {
      if (box.checked) {
        state.exportChecked.add(visit.id);
        state.declined.delete(visit.id);
      } else {
        state.exportChecked.delete(visit.id);
        if (state.exportable.get(visit.id)) state.declined.add(visit.id);
      }
    });
    const button = document.createElement("button");
    button.className = "visit";
    button.textContent = visitLabel(visit);
    button.onclick = () => openCase(visit.id, button);
    row.append(box, button);
    caseList.append(row);
  }
}

function visitLabel(visit) {
  return `${visit.timepoint} · series ${visit.series_number} · ${visit.scan_date}`;
}

function highlightCurrentVisit() {
  document.querySelectorAll(".visit").forEach((item) => item.classList.remove("current"));
  if (!state.caseId) return;
  const row = caseList.querySelector(`.visit-row[data-case-id="${CSS.escape(state.caseId)}"]`);
  if (row) row.querySelector(".visit").classList.add("current");
}

async function rescanInbox() {
  setMessage("Reading data/inbox…");
  try {
    state.cases = await postJSON("/api/inbox/rescan", {});
    renderCaseList();
    const count = state.cases.filter((item) => item.source === "research").length;
    setMessage(count ? `${count} research scan${count === 1 ? "" : "s"}.` : "No DICOM series in data/inbox.");
  } catch (error) {
    setMessage(error.message, true);
  }
}

async function openCase(caseId, button) {
  if (state.caseId === caseId) return;
  setMessage("Opening visit…");
  const payload = await postJSON(`/api/cases/${caseId}/open`, {});
  state.caseId = caseId;
  state.meta = payload.case;
  state.selected = null;
  state.showExpert = Boolean(payload.case.has_expert);
  applyState(payload);
  hideResetPrompt();
  startOverButton.disabled = false;
  state.slice = openingSlice();
  sliceInput.disabled = false;
  sliceInput.max = String(payload.case.n_slices - 1);
  sliceInput.value = String(state.slice);
  document.querySelectorAll(".visit").forEach((item) => item.classList.remove("current"));
  button.classList.add("current");
  paintVisitMeta();
  renderNodules();
  showSlice();
}

function paintVisitMeta() {
  const visit = state.meta;
  visitMeta.textContent = `${visit.patient_id} ${visit.timepoint}, ${visit.scan_date}, series ${visit.series_number}, ${visit.slice_thickness} mm`;
  const wrap = document.querySelector("#timepoint-wrap");
  const input = document.querySelector("#timepoint");
  wrap.hidden = Boolean(visit.timepoint_locked);
  input.value = visit.timepoint;
}

async function saveTimepoint() {
  if (!state.caseId || state.meta.timepoint_locked) return;
  const input = document.querySelector("#timepoint");
  try {
    const payload = await postJSON(`/api/cases/${encodeURIComponent(state.caseId)}/timepoint`, {
      timepoint: input.value,
    });
    state.meta = payload.case;
    const listed = state.cases.find((item) => item.id === state.caseId);
    if (listed) listed.timepoint = payload.case.timepoint;
    paintVisitMeta();
    const row = caseList.querySelector(`.visit-row[data-case-id="${CSS.escape(state.caseId)}"] .visit`);
    if (row && listed) row.textContent = visitLabel(listed);
    setMessage("");
  } catch (error) {
    input.value = state.meta.timepoint;
    setMessage(error.message, true);
  }
}

function expertVisible() {
  return Boolean(state.meta && state.meta.has_expert && state.showExpert);
}

function applyExpertVisibility() {
  const show = expertVisible();
  document.querySelector("#expert-pane").hidden = !show;
  document.querySelector("#viewports").classList.toggle("single", !show);
  document.querySelector("#show-expert-wrap").hidden = !(state.meta && state.meta.has_expert);
  document.querySelector("#show-expert").checked = state.showExpert;
  document.querySelector("#expert-note").hidden = !(state.meta && !state.meta.has_expert);
}

function openingSlice() {
  if (expertVisible() && Object.keys(state.expert).length) return largestExpertSlice();
  return Math.max(0, Math.floor((state.meta.n_slices - 1) / 2));
}

function largestExpertSlice() {
  let best = 0;
  let bestScore = -1;
  for (const [key, polygons] of Object.entries(state.expert)) {
    const score = polygons.reduce((sum, polygon) => sum + polygon.length, 0);
    if (score > bestScore) {
      bestScore = score;
      best = Number(key);
    }
  }
  return best;
}

function showSlice() {
  if (!state.caseId) return;
  applyExpertVisibility();
  const url = `/api/cases/${state.caseId}/slice/${state.slice}.png?t=${Date.now()}`;
  const showExpert = expertVisible();
  setMessage("Loading slices…");
  let pending = showExpert ? 2 : 1;
  const ready = () => {
    pending -= 1;
    if (pending > 0) return;
    resizeCanvas(toolCanvas, toolImage);
    if (showExpert) resizeCanvas(expertCanvas, expertImage);
    drawTool();
    if (showExpert) drawExpert();
    setMessage("");
  };
  toolImage.onload = ready;
  if (showExpert) expertImage.onload = ready;
  toolImage.src = url;
  if (showExpert) expertImage.src = url;
  sliceLabel.textContent = `Slice ${state.slice + 1} of ${state.meta.n_slices}`;
}

function resizeCanvas(canvas, image) {
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
}

function drawTool() {
  const ctx = toolCanvas.getContext("2d");
  ctx.clearRect(0, 0, toolCanvas.width, toolCanvas.height);
  for (const nodule of state.nodules) {
    const runs = nodule.rle[String(state.slice)] || [];
    drawRle(ctx, runs, nodule.color, nodule.id === state.selected ? 0.45 : 0.28);
    if (nodule.axes && nodule.axes.slice_index === state.slice) {
      drawCaliper(ctx, nodule.axes.long, nodule.color);
      drawCaliper(ctx, nodule.axes.short, nodule.color);
    }
  }
}

function drawExpert() {
  const ctx = expertCanvas.getContext("2d");
  ctx.clearRect(0, 0, expertCanvas.width, expertCanvas.height);
  const polygons = state.expert[String(state.slice)] || [];
  ctx.strokeStyle = "#e15a3a";
  ctx.fillStyle = "rgba(225, 90, 58, 0.35)";
  ctx.lineWidth = 2;
  for (const polygon of polygons) {
    ctx.beginPath();
    polygon.forEach(([x, y], index) => (index ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }
}

function drawRle(ctx, runs, color, alpha) {
  ctx.save();
  ctx.fillStyle = color;
  ctx.globalAlpha = alpha;
  const width = toolCanvas.width;
  for (const [start, length] of runs) {
    let remaining = length;
    let pos = start;
    while (remaining > 0) {
      const y = Math.floor(pos / width);
      const x = pos % width;
      const take = Math.min(remaining, width - x);
      ctx.fillRect(x, y, take, 1);
      pos += take;
      remaining -= take;
    }
  }
  ctx.restore();
}

function drawCaliper(ctx, endpoints, color) {
  const [a, b] = endpoints;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(a[0], a[1]);
  ctx.lineTo(b[0], b[1]);
  ctx.stroke();
  for (const point of [a, b]) {
    ctx.beginPath();
    ctx.arc(point[0], point[1], 4, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

function renderNodules() {
  noduleList.replaceChildren();
  if (!state.nodules.length) {
    const empty = document.createElement("p");
    empty.className = "meta";
    empty.textContent = "No nodules yet.";
    noduleList.append(empty);
    return;
  }
  for (const nodule of state.nodules) {
    noduleList.append(noduleCard(nodule));
  }
}

function noduleCard(nodule) {
  const card = document.createElement("article");
  card.className = "nodule" + (nodule.id === state.selected ? " selected" : "");
  card.style.borderLeftColor = nodule.color;
  const head = document.createElement("header");
  const title = document.createElement("strong");
  title.textContent = `Nodule ${nodule.id}`;
  const status = document.createElement("span");
  status.className = `status ${nodule.status}`;
  status.textContent = nodule.status;
  head.append(title, status);
  card.append(head);
  const measured = nodule.measurements;
  const numbers = document.createElement("p");
  numbers.textContent = measured.long_mm == null
    ? "No outline."
    : `Long ${measured.long_mm.toFixed(1)} mm · short ${measured.short_mm.toFixed(1)} mm · mean ${measured.mean_mm.toFixed(1)} mm · volume ${Math.round(measured.volume_mm3)} mm³`;
  card.append(numbers);
  if (expertVisible() && measured.dice != null) {
    const overlap = document.createElement("p");
    overlap.className = "meta";
    overlap.textContent = `Overlap with expert outline ${Math.round(measured.dice * 100)}% · slice instance ${measured.instance_number}`;
    card.append(overlap);
  }
  const actions = document.createElement("div");
  actions.className = "actions";
  actions.append(
    actionButton("Accept", () => setStatus(nodule.id, "accepted")),
    actionButton("Fail", () => setStatus(nodule.id, "failed")),
    actionButton("Delete", () => removeNodule(nodule.id)),
  );
  card.append(actions);
  card.append(fields(nodule));
  card.addEventListener("click", (event) => {
    if (event.target.closest("button, input, select")) return;
    state.selected = nodule.id;
    if (measured.slice_index != null) {
      state.slice = measured.slice_index;
      sliceInput.value = String(state.slice);
      showSlice();
    }
    renderNodules();
    drawTool();
  });
  return card;
}

function fields(nodule) {
  const form = document.createElement("div");
  form.className = "fields";
  form.append(
    selectField("Lobe", nodule.lobe, ["", "RUL", "RML", "RLL", "LUL", "LLL"], (value) => saveField(nodule.id, "lobe", value)),
    selectField("Type", nodule.type, ["", "solid", "part-solid", "ground-glass"], (value) => saveField(nodule.id, "type", value)),
    textField("Solid component (mm)", nodule.solid_component, (value) => saveField(nodule.id, "solid_component", value)),
  );
  const match = selectField(
    "Match to prior",
    nodule.match_to_prior,
    ["", "Confident", "Uncertain", "Not found"],
    (value) => saveField(nodule.id, "match_to_prior", value),
  );
  if (state.meta.first_visit) match.querySelector("select").disabled = true;
  form.append(
    match,
    textField("Measured by", nodule.measured_by, (value) => saveField(nodule.id, "measured_by", value)),
    textField("Notes", nodule.notes, (value) => saveField(nodule.id, "notes", value)),
  );
  if (state.meta.first_visit) {
    const note = document.createElement("p");
    note.className = "meta";
    note.textContent = "First visit: Match to prior stays empty.";
    form.append(note);
  }
  return form;
}

function selectField(labelText, value, options, onChange) {
  const label = document.createElement("label");
  label.textContent = labelText;
  const select = document.createElement("select");
  for (const optionValue of options) {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = optionValue || "—";
    if (optionValue === value) option.selected = true;
    select.append(option);
  }
  select.onchange = () => onChange(select.value);
  label.append(select);
  return label;
}

function textField(labelText, value, onChange) {
  const label = document.createElement("label");
  label.textContent = labelText;
  const input = document.createElement("input");
  input.type = "text";
  input.value = value || "";
  input.onchange = () => onChange(input.value);
  label.append(input);
  return label;
}

function actionButton(text, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = text;
  button.onclick = onClick;
  return button;
}

function onPointerDown(event) {
  const point = eventPoint(event);
  if (state.mode === "propose") {
    const grabbed = nearEndpoint(point);
    if (grabbed) {
      state.drawing = { kind: "axis", ...grabbed, moved: false };
      return;
    }
    state.drawing = { kind: "propose", point, moved: false };
    return;
  }
  if (!state.selected) {
    setMessage("Select a nodule before painting.", true);
    return;
  }
  state.drawing = { kind: "paint", points: [point] };
}

function onPointerMove(event) {
  if (!state.drawing) return;
  const point = eventPoint(event);
  if (state.drawing.kind === "propose" || state.drawing.kind === "axis") {
    const start = state.drawing.point || state.drawing.origin;
    if (Math.hypot(point.x - start.x, point.y - start.y) > 3) state.drawing.moved = true;
  }
  if (state.drawing.kind === "axis" && state.drawing.moved) {
    const nodule = state.nodules.find((item) => item.id === state.selected);
    if (!nodule || !nodule.axes) return;
    nodule.axes[state.drawing.which][state.drawing.end] = [point.x, point.y];
    drawTool();
  }
  if (state.drawing.kind === "paint") state.drawing.points.push(point);
}

async function onPointerUp() {
  const drawing = state.drawing;
  state.drawing = null;
  if (!drawing || !state.caseId) return;
  if (drawing.kind === "propose" && !drawing.moved) {
    setMessage("Proposing an outline…");
    try {
      const payload = await postJSON(`/api/cases/${state.caseId}/nodules`, {
        slice: state.slice,
        x: drawing.point.x,
        y: drawing.point.y,
      });
      applyState(payload);
      state.selected = payload.nodules[payload.nodules.length - 1].id;
      const focus = payload.nodules.find((item) => item.id === state.selected);
      if (focus && focus.measurements.slice_index != null) {
        state.slice = focus.measurements.slice_index;
        sliceInput.value = String(state.slice);
        showSlice();
      }
      renderNodules();
      drawTool();
      setMessage(payload.warning || "", Boolean(payload.warning));
    } catch (error) {
      setMessage(error.message, true);
    }
  }
  if (drawing.kind === "paint") {
    try {
      const payload = await postJSON(`/api/cases/${state.caseId}/nodules/${state.selected}/paint`, {
        slice: state.slice,
        points: drawing.points,
        radius: Number(radiusInput.value),
        mode: state.mode,
      });
      applyState(payload);
      renderNodules();
      drawTool();
    } catch (error) {
      setMessage(error.message, true);
    }
  }
  if (drawing.kind === "axis" && drawing.moved) {
    const nodule = state.nodules.find((item) => item.id === state.selected);
    try {
      const payload = await postJSON(`/api/cases/${state.caseId}/nodules/${nodule.id}/axes`, {
        slice: state.slice,
        long: nodule.axes.long,
        short: nodule.axes.short,
      });
      applyState(payload);
      renderNodules();
      drawTool();
    } catch (error) {
      setMessage(error.message, true);
    }
  }
}

function nearEndpoint(point) {
  const nodule = state.nodules.find((item) => item.id === state.selected);
  if (!nodule || !nodule.axes || nodule.axes.slice_index !== state.slice) return null;
  for (const which of ["long", "short"]) {
    for (let end = 0; end < 2; end += 1) {
      const [x, y] = nodule.axes[which][end];
      if (Math.hypot(point.x - x, point.y - y) < 10) {
        return { which, end, origin: { x, y }, point: { x, y } };
      }
    }
  }
  return null;
}

async function setStatus(noduleId, status) {
  const payload = await postJSON(`/api/cases/${state.caseId}/nodules/${noduleId}/status`, { status });
  applyState(payload);
  renderNodules();
}

async function removeNodule(noduleId) {
  const response = await fetch(`/api/cases/${state.caseId}/nodules/${noduleId}`, { method: "DELETE" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Delete failed");
  if (state.selected === noduleId) state.selected = null;
  applyState(payload);
  renderNodules();
  drawTool();
}

async function saveField(noduleId, key, value) {
  const payload = await postJSON(`/api/cases/${state.caseId}/nodules/${noduleId}/fields`, { [key]: value });
  applyState(payload);
}

function applyState(payload) {
  state.nodules = payload.nodules;
  state.elapsedBase = payload.elapsed_seconds;
  state.elapsedAt = Date.now();
  state.expert = payload.expert_polygons || {};
  if (state.caseId) syncExportChoice(state.caseId, state.nodules);
}

function exportBox(caseId) {
  for (const row of caseList.querySelectorAll(".visit-row")) {
    if (row.dataset.caseId === caseId) return row.querySelector("input");
  }
  return null;
}

function syncExportChoice(caseId, nodules) {
  const exportable = nodules.some((nodule) => nodule.status === "accepted" || nodule.status === "edited");
  const was = state.exportable.get(caseId) === true;
  if (exportable && !was && !state.declined.has(caseId)) state.exportChecked.add(caseId);
  if (!exportable && was) {
    state.exportChecked.delete(caseId);
    state.declined.delete(caseId);
  }
  state.exportable.set(caseId, exportable);
  const box = exportBox(caseId);
  if (box) box.checked = state.exportChecked.has(caseId);
}

function dropFromExport(caseId) {
  state.exportChecked.delete(caseId);
  state.declined.delete(caseId);
  state.exportable.set(caseId, false);
  const box = exportBox(caseId);
  if (box) box.checked = false;
}

function hideResetPrompt() {
  resetConfirm.hidden = true;
  startOverButton.hidden = false;
}

async function confirmReset() {
  if (!state.caseId) return;
  const caseId = state.caseId;
  hideResetPrompt();
  try {
    const payload = await postJSON(`/api/cases/${caseId}/reset`, {});
    state.selected = null;
    dropFromExport(caseId);
    applyState(payload);
    renderNodules();
    drawTool();
    paintTimer();
    setMessage("This visit was cleared.");
  } catch (error) {
    setMessage(error.message, true);
  }
}

async function exportChecked() {
  const caseIds = state.cases.filter((item) => state.exportChecked.has(item.id)).map((item) => item.id);
  if (!caseIds.length) {
    setMessage("Check at least one visit.", true);
    return;
  }
  setMessage("Preparing the sheet…");
  try {
    const response = await fetch("/api/export.csv", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case_ids: caseIds }),
    });
    if (!response.ok) {
      let message = "Export failed";
      try {
        const payload = await response.json();
        message = payload.error || message;
      } catch (error) {
        /* response was not JSON */
      }
      setMessage(message, true);
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "reviewed-visits.csv";
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setMessage("");
  } catch (error) {
    setMessage(error.message, true);
  }
}

function setMode(mode) {
  state.mode = mode;
  for (const name of ["propose", "paint", "erase"]) {
    document.querySelector(`#mode-${name}`).classList.toggle("active", name === mode);
  }
}

function changeSlice(delta) {
  const next = Math.min(Math.max(state.slice + delta, 0), state.meta.n_slices - 1);
  if (next === state.slice) return;
  state.slice = next;
  sliceInput.value = String(next);
  showSlice();
}

function eventPoint(event) {
  const rect = toolCanvas.getBoundingClientRect();
  return {
    x: ((event.clientX - rect.left) * toolCanvas.width) / rect.width,
    y: ((event.clientY - rect.top) * toolCanvas.height) / rect.height,
  };
}

function paintTimer() {
  if (!state.caseId) return;
  const seconds = state.elapsedBase + (Date.now() - state.elapsedAt) / 1000;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60);
  timerEl.textContent = `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function setMessage(text, isError) {
  messageEl.textContent = text;
  messageEl.classList.toggle("error", Boolean(isError));
}

async function getJSON(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
}

async function postJSON(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
}
