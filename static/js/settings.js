// #/settings  People (group, household, color, protected, active), groups, households (read-only: a household is
// the label its members share), occasions (new, copy from, rename, archive) and the PIN. While locked, protected
// people are not listed at all; unprotecting or deleting one needs the unlock, which the server enforces with a
// 423 that opens the PIN pad.
import { events, get, send } from "./api.js";
import { currentOccasion, esc, isUnlocked, renderChecked, renderColor, renderOptions, setOccasion, state, toast } from "./core.js";
import { isSchool, roleChoices } from "./mode.js";

const KINDS = [
  { value: "christmas", label: "Christmas" }, { value: "birthday", label: "Birthday" },
  { value: "graduation", label: "Graduation" }, { value: "other", label: "Other" },
];
const NEUTRAL = "#9aa29c"; // what the color picker shows for a person with no color; only sent once touched

// ---------------------------------------------------------------- payloads (pure)
const needName = (name, what) => {
  if (!name.trim()) throw new Error(`${what} needs a name.`);
  return name.trim();
};

export function personPayload(v) {
  const body = {
    name: needName(v.name, "A person"), group_id: v.group === "" ? null : Number(v.group),
    household: v.household.trim(), protected: v.protected,
  };
  if (v.active !== undefined) body.active = v.active;
  if (v.colorTouched) body.color = v.color;
  if (v.role !== undefined) body.role = v.role.trim(); // school mode only: the form has the field only there
  return body;
}

export function groupPayload(v) {
  const body = { name: needName(v.name, "A group") };
  if (v.sort !== undefined && v.sort !== "") {
    if (!/^\d+$/.test(v.sort.trim())) throw new Error("Order must be a whole number.");
    body.sort_order = Number(v.sort);
  }
  return body;
}

export function occasionPayload(v) {
  const body = { name: needName(v.name, "An occasion") };
  if (!/^\d{4}-\d{2}-\d{2}$/.test(v.date)) throw new Error("Pick a date.");
  body.date = v.date;
  if (v.kind !== undefined) body.kind = v.kind;
  if (v.copyFrom) body.copy_from = Number(v.copyFrom);
  if (v.archived !== undefined) body.archived = v.archived;
  return body;
}

export function pinPayload(v, hasPin) {
  if (!/^\d{4,8}$/.test(v.pin)) throw new Error("A PIN is 4 to 8 digits.");
  if (v.pin !== v.again) throw new Error("The two PINs differ.");
  if (!hasPin) return { pin: v.pin };
  if (!v.current) throw new Error("Enter your current PIN.");
  return { pin: v.pin, current: v.current };
}

// Households as they may be shown: while locked, protected members (and households left empty) are dropped.
export function visibleHouseholds(households, people, unlocked) {
  const byId = new Map(people.map(p => [p.id, p]));
  return households
    .map(h => ({ label: h.label, names: h.person_ids.map(id => byId.get(id)).filter(p => p && (unlocked || !p.protected)).map(p => p.name) }))
    .filter(h => h.names.length);
}

// ---------------------------------------------------------------- markup
const renderBadge = (on, text) => (on ? ` <span class="badge">${esc(text)}</span>` : "");
const renderDim = on => (on ? " dim" : "");

const renderGroupOptions = (groups, selected) => renderOptions([{ value: "", label: "No group" }, ...groups.map(g => ({ value: g.id, label: g.name }))], selected ?? "");

// School mode swaps the Protected box for a Role field (the server ignores role in family mode and refuses protected
// in school mode, so each mode's form shows only its own).
const renderProtectedBox = (p, school) => (school ? "" : `
      <label class="check"><input name="protected" type="checkbox"${renderChecked(p?.protected)}> Protected (hidden while locked)</label>`);
const renderRoleField = (p, school) => (school
  ? `<label class="field"><span>Role</span><input name="role" type="text" maxlength="50" list="roles" value="${esc(p?.role ?? "")}" placeholder="homeroom, assistant, ..."></label>`
  : "");

export const renderPersonForm = (p, groups, school = false) => `
  <form class="fields" data-form="${esc(p ? "person-edit" : "person-add")}" data-id="${esc(p?.id ?? "")}" autocomplete="off">
    <label class="field"><span>Name</span><input name="name" type="text" maxlength="100" value="${esc(p?.name ?? "")}" required></label>
    <div class="two">
      <label class="field"><span>Group</span><select name="group">${renderGroupOptions(groups, p?.group_id)}</select></label>
      <label class="field"><span>Household</span><input name="household" type="text" maxlength="100" list="households" value="${esc(p?.household ?? "")}"></label>
    </div>
    ${renderRoleField(p, school)}
    <div class="inline-form"><label class="field"><span>Color</span><input name="color" type="color" value="${esc(p?.color ?? NEUTRAL)}"></label>${renderProtectedBox(p, school)}</div>
    ${renderActiveBox(p)}
    <p class="form-error" role="alert"></p>
    <div class="actions"><button class="btn primary" type="submit">${esc(p ? "Save" : "Add person")}</button>${renderPersonEnd(p)}</div>
  </form>`;
const renderActiveBox = p => (p ? `<label class="check"><input name="active" type="checkbox"${renderChecked(p.active)}> Active (inactive people drop out of new occasions and keep their history)</label>` : "");
const renderPersonEnd = p => (p ? `<button class="btn" type="button" data-act="cancel-edit">Cancel</button><span class="spacer"></span>${renderPersonDelete(p)}` : "");
const renderPersonDelete = p => (p.is_user ? "" : `<button class="btn danger" type="button" data-act="delete-person" data-id="${esc(p.id)}">Delete</button>`);

const renderPerson = (p, groups) => `<li class="row${renderDim(!p.active)}"${renderColor(p.color)}>
  <div class="main"><div class="name">${esc(p.name)}${renderBadge(p.protected, "protected")}${renderBadge(Boolean(p.role), p.role)}${renderBadge(!p.active, "inactive")}${renderBadge(p.is_user, "owner")}</div>
    <div class="line">${esc([groups.find(g => g.id === p.group_id)?.name, p.household].filter(Boolean).join(" · "))}</div></div>
  <button type="button" class="btn small" data-act="edit-person" data-id="${esc(p.id)}">Edit</button>
  <div class="slot" data-slot="person-${esc(p.id)}"></div>
</li>`;
const renderPeopleList = (people, groups) => people.map(p => renderPerson(p, groups)).join("");

const hiddenSentence = count => `${count} protected ${count === 1 ? "person is" : "people are"} hidden while locked.`;
const renderHiddenNote = count => (count
  ? `<p class="notice">${esc(hiddenSentence(count))} <button type="button" class="btn small" data-act="unlock">Unlock</button></p>`
  : "");

const renderGroupRow = g => `<li><form class="inline-form" data-form="group-edit" data-id="${esc(g.id)}">
  <label class="field"><span>Name</span><input name="name" type="text" maxlength="100" value="${esc(g.name)}" required></label>
  <label class="field"><span>Order</span><input name="sort" type="text" inputmode="numeric" value="${esc(g.sort_order)}"></label>
  <button class="btn" type="submit">Save</button><button class="btn danger" type="button" data-act="delete-group" data-id="${esc(g.id)}">Delete</button>
  <p class="form-error" role="alert"></p></form></li>`;
const renderGroupRows = groups => groups.map(renderGroupRow).join("");

const renderHouseholdRow = h => `<li class="row"><div class="main"><div class="name">${esc(h.label)}</div><div class="line">${esc(h.names.join(", "))}</div></div></li>`;
const renderHouseholdRows = list => list.map(renderHouseholdRow).join("");
const renderHouseholdOptions = list => list.map(h => `<option value="${esc(h.label)}"></option>`).join("");

const renderOccasion = o => `<li class="row${renderDim(o.archived)}">
  <div class="main"><div class="name">${esc(o.name)}${renderBadge(o.archived, "archived")}${renderBadge(o.current, "current")}</div><div class="line">${esc(o.kind)} · ${esc(o.date)}</div></div>
  <button type="button" class="btn small" data-act="edit-occasion" data-id="${esc(o.id)}">Edit</button>
  <div class="slot" data-slot="occasion-${esc(o.id)}"></div>
</li>`;
const renderOccasionRows = list => list.map(renderOccasion).join("");

export const renderOccasionEdit = o => `
  <form class="fields" data-form="occasion-edit" data-id="${esc(o.id)}">
    <div class="two"><label class="field"><span>Name</span><input name="name" type="text" maxlength="100" value="${esc(o.name)}" required></label>
      <label class="field"><span>Date</span><input name="date" type="date" value="${esc(o.date)}" required></label></div>
    <label class="check"><input name="archived" type="checkbox"${renderChecked(o.archived)}> Archived</label>
    <p class="form-error" role="alert"></p>
    <div class="actions"><button class="btn primary" type="submit">Save</button><button class="btn" type="button" data-act="cancel-edit">Cancel</button></div>
  </form>`;

const renderCopyOptions = (occasions, selected) => renderOptions([{ value: "", label: "Start empty" }, ...occasions.map(o => ({ value: o.id, label: o.name }))], selected);

const renderRoleList = d => (d.school ? `<datalist id="roles">${renderRoleOptions(d.roles)}</datalist>` : "");
const renderRoleOptions = roles => roles.map(r => `<option value="${esc(r)}"></option>`).join("");
// A school instance has no lock, so no PIN.
const renderPinSection = d => (d.school ? "" : renderPinForm(d.me.has_pin));

const renderPinForm = hasPin => `
  <form class="card fields" data-form="pin" autocomplete="off">
    <h3>PIN</h3>
    <p class="muted small">The PIN opens the protected part of the app after it locks itself. Setting or changing it does not unlock it.</p>
    ${renderCurrentPin(hasPin)}
    <div class="two"><label class="field"><span>${esc(hasPin ? "New PIN" : "PIN")}</span><input name="pin" type="password" inputmode="numeric" pattern="[0-9]*" minlength="4" maxlength="8" autocomplete="new-password"></label>
      <label class="field"><span>Again</span><input name="again" type="password" inputmode="numeric" pattern="[0-9]*" minlength="4" maxlength="8" autocomplete="new-password"></label></div>
    <p class="form-error" role="alert"></p>
    <div class="actions"><button class="btn primary" type="submit">${esc(hasPin ? "Change PIN" : "Set PIN")}</button></div>
  </form>`;
const renderCurrentPin = hasPin => (hasPin
  ? `<label class="field"><span>Current PIN</span><input name="current" type="password" inputmode="numeric" pattern="[0-9]*" maxlength="8" autocomplete="current-password"></label>`
  : "");

export const renderSettings = d => `
  <div class="head"><h2>Settings</h2><span class="sub">${esc(d.me.email)}</span></div>
  <datalist id="households">${renderHouseholdOptions(d.households)}</datalist>
  ${renderRoleList(d)}
  <div class="cols">
    <div class="col">
      <section class="card"><h3>People</h3>
        ${renderHiddenNote(d.hiddenCount)}
        <ul class="rows">${renderPeopleList(d.people, d.groups)}</ul>
        <div data-slot="person-new"><button type="button" class="btn big-add" data-act="new-person">Add a person</button></div>
      </section>
    </div>
    <div class="col">
      <section class="card"><h3>Occasions</h3>
        <ul class="rows">${renderOccasionRows(d.occasions)}</ul>
        <form class="fields" data-form="occasion-add">
          <label class="field"><span>New occasion</span><input name="name" type="text" maxlength="100" placeholder="Name" required></label>
          <div class="two"><label class="field"><span>Kind</span><select name="kind">${renderOptions(KINDS, "christmas")}</select></label>
            <label class="field"><span>Date</span><input name="date" type="date" required></label></div>
          <label class="field"><span>Copy people and budgets from</span><select name="copyFrom">${renderCopyOptions(d.occasions, d.copyDefault)}</select></label>
          <p class="form-error" role="alert"></p>
          <div class="actions"><button class="btn primary" type="submit">Create occasion</button></div>
        </form>
      </section>
      <section class="card"><h3>Groups</h3>
        <ul class="rows">${renderGroupRows(d.groups)}</ul>
        <form class="inline-form" data-form="group-add"><label class="field"><span>New group</span><input name="name" type="text" maxlength="100" required></label>
          <button class="btn" type="submit">Add</button><p class="form-error" role="alert"></p></form>
      </section>
      <section class="card"><h3>Households</h3>
        <p class="muted small">A household is the label its members share; set it on each person. It is one choice in the Assign menu.</p>
        <ul class="rows">${renderHouseholdRows(d.households)}</ul>
      </section>
      ${renderPinSection(d)}
      <p class="muted small"><a href="#/places">Addresses and hiding places</a></p>
      <p class="muted small"><a href="#/tokens">Agent tokens</a></p>
      <p class="muted small"><a href="/cdn-cgi/access/logout">Sign out</a></p>
    </div>
  </div>`;

// ---------------------------------------------------------------- behaviour
const field = (form, name) => form.elements[name];
const text = (form, name) => field(form, name)?.value ?? "";
const box = (form, name) => (field(form, name) ? field(form, name).checked : undefined);

export async function render(ctx) {
  const { view } = ctx;
  const unlocked = isUnlocked();
  const [groups, households] = await Promise.all([get("/api/groups"), get("/api/households")]);
  if (!ctx.alive()) return;
  const shown = state.people.filter(p => unlocked || !p.protected);
  const current = currentOccasion();
  const data = {
    me: state.me, groups, people: shown, hiddenCount: state.people.length - shown.length,
    households: visibleHouseholds(households, state.people, unlocked), occasions: state.occasions,
    copyDefault: current ? current.id : "",
    school: isSchool(state.me), roles: roleChoices(state.people),
  };
  view.innerHTML = renderSettings(data);
  const fromWasLocked = !unlocked;

  const run = async (form, action, done) => {
    const error = form.querySelector(".form-error");
    if (error) error.textContent = "";
    try {
      await action();
    } catch (err) {
      if (error) error.textContent = err.locked ? "Locked. Unlock, then try again." : err.message;
      else toast(err.message);
      return false;
    }
    if (done) toast(done);
    return true;
  };

  view.addEventListener("input", e => {
    if (e.target.type === "color") e.target.dataset.touched = "1";
  });

  view.addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.target;
    const kind = form.dataset.form, id = form.dataset.id;
    let ok = false;
    if (kind === "person-add" || kind === "person-edit") {
      const color = field(form, "color");
      ok = await run(form, () => send(kind === "person-add" ? "POST" : "PATCH", kind === "person-add" ? "/api/people" : `/api/people/${id}`,
        personPayload({
          name: text(form, "name"), group: text(form, "group"), household: text(form, "household"),
          protected: box(form, "protected"), active: box(form, "active"), color: color.value, colorTouched: color.dataset.touched === "1",
          role: field(form, "role") ? text(form, "role") : undefined,
        })), kind === "person-add" ? "Added" : "Saved");
    } else if (kind === "group-add" || kind === "group-edit") {
      ok = await run(form, () => send(kind === "group-add" ? "POST" : "PATCH", kind === "group-add" ? "/api/groups" : `/api/groups/${id}`,
        groupPayload({ name: text(form, "name"), sort: field(form, "sort") ? text(form, "sort") : undefined })), "Saved");
    } else if (kind === "occasion-add") {
      ok = await run(form, async () => {
        const made = await send("POST", "/api/occasions", occasionPayload({
          name: text(form, "name"), kind: text(form, "kind"), date: text(form, "date"), copyFrom: text(form, "copyFrom"),
        }));
        setOccasion(made.id);
      }, "Created");
    } else if (kind === "occasion-edit") {
      ok = await run(form, () => send("PATCH", `/api/occasions/${id}`, occasionPayload({
        name: text(form, "name"), date: text(form, "date"), archived: box(form, "archived"),
      })), "Saved");
    } else if (kind === "pin") {
      const hasPin = state.me.has_pin;
      ok = await run(form, () => send("POST", "/api/lock/pin", pinPayload(
        { pin: text(form, "pin"), again: text(form, "again"), current: text(form, "current") }, hasPin)), "PIN saved");
      // Setting a PIN does not unlock: when the app was locked, ask for the PIN straight away.
      if (ok) {
        state.me.has_pin = true;
        if (fromWasLocked) events.dispatchEvent(new Event("locked"));
      }
    }
    if (ok) ctx.rerender();
  });

  view.addEventListener("click", async e => {
    const act = e.target.closest("[data-act]");
    if (!act) return;
    const id = act.dataset.id;
    if (act.dataset.act === "new-person") {
      const slot = view.querySelector('[data-slot="person-new"]');
      slot.innerHTML = renderPersonForm(null, await get("/api/groups"), data.school);
      slot.querySelector("input[name=name]").focus();
    } else if (act.dataset.act === "edit-person") {
      const slot = view.querySelector(`[data-slot="person-${id}"]`);
      slot.innerHTML = renderPersonForm(state.people.find(p => p.id === Number(id)), await get("/api/groups"), data.school);
    } else if (act.dataset.act === "edit-occasion") {
      view.querySelector(`[data-slot="occasion-${id}"]`).innerHTML = renderOccasionEdit(state.occasions.find(o => o.id === Number(id)));
    } else if (act.dataset.act === "cancel-edit") {
      act.closest(".slot").innerHTML = "";
    } else if (act.dataset.act === "delete-person" || act.dataset.act === "delete-group") {
      const isPerson = act.dataset.act === "delete-person";
      if (!globalThis.confirm(isPerson ? "Delete this person? A person with gifts cannot be deleted; make them inactive instead." : "Delete this group? Its people stay, ungrouped.")) return;
      if (await run(act.closest("form") ?? view, () => send("DELETE", `/api/${isPerson ? "people" : "groups"}/${id}`), "Deleted")) ctx.rerender();
    }
  });
}
