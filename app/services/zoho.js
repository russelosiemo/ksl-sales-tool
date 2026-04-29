/**
 * app/services/zoho.js
 * KSL Field Sales Tool
 *
 * All live Zoho Inventory API calls made from the browser.
 * Every function here requires a valid access token.
 * Token is read from data/app/token.json by cache.js and
 * passed into each call by the view layer.
 *
 * No function in this file reads from the static JSON cache.
 * No function in this file touches the DOM.
 */

const INVENTORY_BASE = "https://www.zohoapis.com/inventory/v1";

async function _get(token, orgId, path, params = {}) {
  const url = new URL(`${INVENTORY_BASE}${path}`);
  url.searchParams.set("organization_id", orgId);
  for (const [k, v] of Object.entries(params)) {
    url.searchParams.set(k, v);
  }
  const resp = await fetch(url.toString(), {
    headers: { Authorization: `Zoho-oauthtoken ${token}` },
  });
  if (!resp.ok) {
    throw new Error(`Zoho API ${path} returned ${resp.status}`);
  }
  return resp.json();
}

/**
 * Fetch the customer's invoiced SKUs for the last N days.
 * Used to determine which SKUs are "active" at this outlet.
 * Returns array of {sku, name, quantity_invoiced, rate, date}
 */
export async function fetchCustomerActiveSKUs(token, orgId, customerId, days = 90) {
  const dateFrom = new Date();
  dateFrom.setDate(dateFrom.getDate() - days);
  const from = dateFrom.toISOString().slice(0, 10);

  const data = await _get(token, orgId, "/invoices", {
    customer_id: customerId,
    date_start:  from,
    per_page:    200,
    sort_column: "date",
    sort_order:  "D",
  });

  const skuMap = {};
  for (const inv of data.invoices || []) {
    for (const li of inv.line_items || []) {
      const sku = li.sku?.trim();
      if (!sku || !parseFloat(li.quantity_invoiced)) continue;
      if (!skuMap[sku]) {
        skuMap[sku] = {
          sku,
          name:             li.name || "",
          quantity_invoiced:0,
          rate:             parseFloat(li.rate) || 0,
          last_date:        inv.date || "",
        };
      }
      skuMap[sku].quantity_invoiced += parseFloat(li.quantity_invoiced) || 0;
      if ((inv.date || "") > skuMap[sku].last_date) {
        skuMap[sku].last_date = inv.date;
      }
    }
  }
  return Object.values(skuMap);
}

/**
 * Fetch weekly outlet run rate per SKU for this customer.
 * Looks back 8 weeks, buckets by ISO week, returns avg per week.
 * Returns {sku: {weekly_avg, weeks_seen, last_date}}
 */
export async function fetchOutletRunRates(token, orgId, customerId) {
  const dateFrom = new Date();
  dateFrom.setDate(dateFrom.getDate() - 56); // 8 weeks
  const from = dateFrom.toISOString().slice(0, 10);

  const data = await _get(token, orgId, "/invoices", {
    customer_id: customerId,
    date_start:  from,
    per_page:    200,
    sort_column: "date",
    sort_order:  "D",
  });

  const skuWeeks = {};
  for (const inv of data.invoices || []) {
    const d = new Date(inv.date);
    const wk = _isoWeek(d);
    for (const li of inv.line_items || []) {
      const sku = li.sku?.trim();
      if (!sku) continue;
      const qty = parseFloat(li.quantity_invoiced) || 0;
      if (qty <= 0) continue;
      if (!skuWeeks[sku]) skuWeeks[sku] = { weeks: {}, last_date: "" };
      skuWeeks[sku].weeks[wk] = (skuWeeks[sku].weeks[wk] || 0) + qty;
      if ((inv.date || "") > skuWeeks[sku].last_date) {
        skuWeeks[sku].last_date = inv.date;
      }
    }
  }

  const result = {};
  for (const [sku, data] of Object.entries(skuWeeks)) {
    const vals      = Object.values(data.weeks);
    const total     = vals.reduce((a, b) => a + b, 0);
    const weeks_seen = vals.length;
    result[sku] = {
      weekly_avg: weeks_seen > 0 ? Math.round((total / weeks_seen) * 10) / 10 : 0,
      weeks_seen,
      last_date:  data.last_date,
    };
  }
  return result;
}

/**
 * Fetch the last sales order date for this customer.
 * Returns ISO date string or null.
 */
export async function fetchLastSODate(token, orgId, customerId) {
  const data = await _get(token, orgId, "/salesorders", {
    customer_id: customerId,
    per_page:    1,
    sort_column: "date",
    sort_order:  "D",
  });
  const sos = data.salesorders || [];
  return sos.length > 0 ? sos[0].date : null;
}

/**
 * Fetch the last invoice date for this customer.
 * Returns ISO date string or null.
 */
export async function fetchLastInvoiceDate(token, orgId, customerId) {
  const data = await _get(token, orgId, "/invoices", {
    customer_id: customerId,
    per_page:    1,
    sort_column: "date",
    sort_order:  "D",
  });
  const invs = data.invoices || [];
  return invs.length > 0 ? invs[0].date : null;
}

/**
 * Send a priority email via Zoho Mail.
 */
export async function sendPriorityEmail(token, accountId, { to, subject, html }) {
  const resp = await fetch(
    `https://mail.zoho.com/api/accounts/${accountId}/messages`,
    {
      method:  "POST",
      headers: {
        Authorization:  `Zoho-oauthtoken ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        fromAddress: "russel@kingdom.limited",
        toAddress:   to,
        subject,
        content:     html,
        mailFormat:  "html",
        priority:    "high",
      }),
    }
  );
  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(`Mail send failed: ${resp.status} ${err}`);
  }
  return resp.json();
}

// ISO week key helper: "2026-W12"
function _isoWeek(d) {
  const jan4   = new Date(d.getFullYear(), 0, 4);
  const startOfWeek = new Date(jan4);
  startOfWeek.setDate(jan4.getDate() - jan4.getDay() + 1);
  const week = Math.ceil(((d - startOfWeek) / 86400000 + 1) / 7);
  return `${d.getFullYear()}-W${String(week).padStart(2, "0")}`;
}
