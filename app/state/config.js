/**
 * app/state/config.js
 * KSL Field Sales Tool
 *
 * All application constants. Change behaviour here, not scattered
 * across view files.
 */

export const CONFIG = {
  // Default visit cycle when no prior visit exists (days)
  DEFAULT_VISIT_CYCLE_DAYS: 7,

  // Buffer weeks per ABC class — applied on top of cycle stock
  ABC_BUFFER_WEEKS: {
    A: 1.0,
    B: 0.75,
    C: 0.5,
  },

  // Number of recent invoice weeks to use for outlet run rate
  RUN_RATE_WEEKS: 8,

  // Days of invoice history to scan for active SKU classification
  ACTIVE_SKU_LOOKBACK_DAYS: 90,

  // Zoho Mail account ID for priority email sends
  ZOHO_MAIL_ACCOUNT_ID: "",   // set from token.json at runtime if available

  // Internal priority pitch email recipient
  PITCH_EMAIL_TO: "russel@kingdom.limited",

  // Drop reasons for Mode B swipe-left
  DROP_REASONS: [
    "Pricing too high",
    "Outlet sources elsewhere",
    "Not relevant to this outlet",
    "Customer not interested",
    "No shelf space",
    "Other",
  ],

  // Visit log localStorage key
  VISIT_LOG_KEY: "ksl_visit_log",

  // Session localStorage key (rep identity only, no sensitive data)
  SESSION_KEY: "ksl_session",
};
