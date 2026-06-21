/* db.js — localStorage-based storage for SAPHIR Pro Android
   No npm imports needed. Works in any WebView. */

const STORE_KEY = 'saphirpro_eleves';
let _nextId = 1;

function loadAll() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return [];
    return JSON.parse(raw);
  } catch (e) { return []; }
}

function saveAll(list) {
  localStorage.setItem(STORE_KEY, JSON.stringify(list));
}

export function initDB() {
  const all = loadAll();
  if (all.length) {
    _nextId = Math.max(...all.map(e => e.id)) + 1;
  }
  return Promise.resolve();
}

export function saveEleve(nom, matricule, classe, etablissement, annee, total, mo, mention, matieres) {
  const all = loadAll();
  const id = _nextId++;
  const now = new Date().toISOString().slice(0, 10);
  const eleve = { id, nom, matricule, classe, etablissement, annee, created_at: now };
  const resultat = { eleve_id: id, total: total || 0, mo: mo || 0, mention: mention || '', matieres: matieres || {}, date_calc: now };
  all.push({ ...eleve, ...resultat });
  saveAll(all);
  return Promise.resolve({ eleve, resultat });
}

export function listEleves() {
  const all = loadAll();
  all.sort((a, b) => b.id - a.id);
  return Promise.resolve(all);
}

export function getEleve(id) {
  const all = loadAll();
  const found = all.find(e => e.id === id) || null;
  return Promise.resolve(found);
}

export function deleteEleve(id) {
  let all = loadAll();
  all = all.filter(e => e.id !== id);
  saveAll(all);
  return Promise.resolve(true);
}

export function clearAll() {
  localStorage.removeItem(STORE_KEY);
  _nextId = 1;
  return Promise.resolve(0);
}
