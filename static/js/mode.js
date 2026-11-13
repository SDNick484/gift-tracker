// What differs between a family instance and a school instance (GET /api/me says `mode: "school"`; a family
// instance sends no mode key). Pure: no DOM, so Node can test it and any screen can import it.

export const ROLE_SUGGESTIONS = ["homeroom", "assistant", "special subject", "administrator"];

// Screens that do not exist in school mode: the server answers 404 for their routes.
const FAMILY_ONLY_SCREENS = ["board"];

export const isSchool = me => me?.mode === "school";

// The idle lock, the Lock button and the PIN exist only in a family instance.
export const lockApplies = me => Boolean(me) && !isSchool(me);

// The loader for a route. `family` and `school` map a route name to a loader; in school mode the school entry
// replaces the family one and the family-only screens are gone (null: "nothing at this address").
export function pickScreen(name, family, school, me) {
  if (!isSchool(me)) return family[name] ?? null;
  if (FAMILY_ONLY_SCREENS.includes(name)) return null;
  return school[name] ?? family[name] ?? null;
}

const same = (a, b) => (a ?? "").trim().toLowerCase() === (b ?? "").trim().toLowerCase();

// The suggestions, then any other role in use, each once (case-insensitive; the first spelling wins).
export function roleChoices(people) {
  const seen = new Map();
  for (const role of [...ROLE_SUGGESTIONS, ...people.map(p => p.role)]) {
    const text = (role ?? "").trim();
    if (text && !seen.has(text.toLowerCase())) seen.set(text.toLowerCase(), text);
  }
  return [...seen.values()];
}

// Roles that at least one active person has, in the order they first appear: what "add everyone with role" can use.
export function activeRoles(people) {
  const seen = new Map();
  for (const p of people) {
    const text = (p.role ?? "").trim();
    if (p.active && text && !seen.has(text.toLowerCase())) seen.set(text.toLowerCase(), text);
  }
  return [...seen.values()];
}

export const peopleWithRole = (people, role) => people.filter(p => p.active && p.role && same(p.role, role));
