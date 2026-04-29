/**
 * app/services/cache.js
 * KSL Field Sales Tool
 *
 * Reads the static JSON data files written by the pipeline.
 * All files are fetched once per session and held in module-level
 * variables. Subsequent calls return the cached copy immediately.
 *
 * Base URL resolves relative to the page origin so it works both
 * on GitHub Pages and locally via file:// with a dev server.
 */

const BASE = "./data/app";

let _catalogue  = null;
let _stock      = null;
let _velocity   = null;
let _reps       = null;
let _tokenData  = null;

async function _fetchJSON(path) {
  const resp = await fetch(`${BASE}/${path}?_=${Date.now()}`);
  if (!resp.ok) throw new Error(`Failed to load ${path}: ${resp.status}`);
  return resp.json();
}

/**
 * Load and cache all static data files.
 * Call once at app startup before rendering any views.
 * Returns {catalogue, stock, velocity, reps, token, orgId}
 */
export async function loadAllCache() {
  const [cat, stk, vel, reps, tok] = await Promise.all([
    _fetchJSON("catalogue.json"),
    _fetchJSON("stock.json"),
    _fetchJSON("velocity.json"),
    _fetchJSON("reps.json"),
    _fetchJSON("token.json"),
  ]);

  _catalogue  = cat;
  _stock      = stk;
  _velocity   = vel;
  _reps       = reps;
  _tokenData  = tok;

  return {
    catalogue: _catalogue,
    stock:     _stock,
    velocity:  _velocity,
    reps:      _reps,
    token:     _tokenData.access_token,
    orgId:     _tokenData.org_id,
  };
}

/** Returns the flat array of catalogue items. */
export function getCatalogueItems() {
  return _catalogue?.items || [];
}

/** Returns stock entry for a SKU: {wh1, wh2, combined} or null. */
export function getStock(sku) {
  return _stock?.stock?.[sku] || null;
}

/** Returns velocity entry for a SKU or null. */
export function getVelocity(sku) {
  const items = _velocity?.velocity || [];
  return items.find(v => v.sku === sku) || null;
}

/** Returns velocity map keyed by SKU for fast lookup. */
export function getVelocityMap() {
  const items = _velocity?.velocity || [];
  return Object.fromEntries(items.map(v => [v.sku, v]));
}

/** Returns the rep object matching repId, or null. */
export function getRep(repId) {
  return _reps?.reps?.find(r => r.rep_id === repId) || null;
}

/** Returns all reps (for login selector). */
export function getAllReps() {
  return _reps?.reps || [];
}

/** Returns the current access token string. */
export function getToken() {
  return _tokenData?.access_token || "";
}

/** Returns the Zoho org ID. */
export function getOrgId() {
  return _tokenData?.org_id || "";
}

/** Returns stock meta: last_updated, wh1, wh2 names. */
export function getStockMeta() {
  return _stock?._meta || {};
}
