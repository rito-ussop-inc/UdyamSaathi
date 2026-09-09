// --- Access control ---
// If the user hasn't logged in (no token stored), send them to the login page.
if (!localStorage.getItem("saathi_token")) {
  window.location.replace("login.html");
}
// If the user hasn't selected a business yet, send them to business selection.
if (!localStorage.getItem("selectedBusiness")) {
  window.location.replace("business.html");
}

const pages = document.querySelectorAll(".page");
const navButtons = document.querySelectorAll("[data-page]");

let lastPage = "home";

function showPage(name) {
  // Auto-save the plan when the user leaves the plan page.
  if (lastPage === "plan" && name !== "plan") savePlan();
  lastPage = name;
  pages.forEach(page => page.classList.toggle("active", page.id === `page-${name}`));
  document.querySelectorAll(".nav-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.page === name);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

navButtons.forEach(btn => {
  btn.addEventListener("click", (e) => {
    e.preventDefault();
    showPage(btn.dataset.page);
  });
});

const toast = document.getElementById("toast");
const t = (k, v) => (window.I18N ? window.I18N.t(k, v) : k);
function notify(message) {
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2600);
}

// Cow stepper
let cows = 3;
const cowCount = document.getElementById("cowCount");
function setCows(n) {
  cows = Math.max(1, Math.min(12, Math.round(n)));
  cowCount.textContent = cows.toLocaleString(numLocale());
}
document.getElementById("minusCow").addEventListener("click", () => setCows(cows - 1));
document.getElementById("plusCow").addEventListener("click", () => setCows(cows + 1));

// Toggles
function setToggle(toggle, on) {
  toggle.classList.toggle("active", on);
  toggle.closest(".toggle-row").classList.toggle("on", on);
}
document.querySelectorAll(".toggle").forEach(toggle => {
  toggle.addEventListener("click", () => setToggle(toggle, !toggle.classList.contains("active")));
});

// --- Backend API Base URL ---
// Local dev (localhost) talks to :8000; any hosted domain (Vercel, etc.)
// uses same-origin (port 443/80) — never append :8000 there.
const API_BASE = (() => {
  const proto = window.location.protocol;
  const host = window.location.hostname;
  if (!proto.startsWith("http") || !host) return "http://127.0.0.1:8000";
  if (host === "localhost" || host === "127.0.0.1") return `${proto}//${host}:8000`;
  return window.location.origin;
})();

// --- Per-user plan persistence ---
const getToken = () => localStorage.getItem("saathi_token");

// Capital field shows native digits + Indian grouping; parse back robustly.
function latinDigits(s) {
  return String(s == null ? "" : s).replace(/[०-९]/g, c => "०१२३४५६७८९".indexOf(c)).replace(/[০-৯]/g, c => "০১২৩৪৫৬৭৮৯".indexOf(c));
}
function parseCapitalInput() {
  const raw = latinDigits(document.getElementById("capitalInput").value);
  const digits = raw.replace(/[^0-9]/g, "");
  return Math.max(0, Number(digits || 0));
}
function formatCapitalInput() {
  const el = document.getElementById("capitalInput");
  if (!el) return;
  const n = parseCapitalInput();
  el.value = n ? n.toLocaleString(numLocale()) : "";
}
(function wireCapitalInput() {
  const el = document.getElementById("capitalInput");
  if (!el) return;
  formatCapitalInput();
  el.addEventListener("focus", () => {
    // Plain digits while editing for easy typing; native format returns on blur.
    const n = parseCapitalInput();
    el.value = n ? String(n) : "";
    setTimeout(() => { try { el.select(); } catch (e) {} }, 0);
  });
  el.addEventListener("blur", formatCapitalInput);
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") el.blur();
  });
})();
function readPlan() {
  return {
    cows: cows,
    capital: parseCapitalInput(),
    existing_shed: document.querySelector('.toggle[data-toggle="shed"]').classList.contains("active"),
    fodder_land: document.querySelector('.toggle[data-toggle="fodder"]').classList.contains("active"),
    family_labour: document.querySelector('.toggle[data-toggle="family"]').classList.contains("active"),
    distributor: document.querySelector('.toggle[data-toggle="buyer"]').classList.contains("active")
  };
}

function applyPlan(plan) {
  if (!plan) return;
  if (typeof plan.cows === "number") setCows(plan.cows);
  if (typeof plan.capital === "number") { document.getElementById("capitalInput").value = plan.capital; formatCapitalInput(); }
  const map = { existing_shed: "shed", fodder_land: "fodder", family_labour: "family", distributor: "buyer" };
  for (const [key, dataKey] of Object.entries(map)) {
    if (typeof plan[key] === "boolean") {
      setToggle(document.querySelector(`.toggle[data-toggle="${dataKey}"]`), plan[key]);
    }
  }
}

async function loadSavedPlan() {
  const token = getToken();
  if (!token) return;
  try {
    const res = await fetch(`${API_BASE}/api/plan`, {
      headers: { "Authorization": "Bearer " + token }
    });
    if (res.ok) {
      const data = await res.json();
      applyPlan(data.plan);
    }
  } catch (err) {
    console.warn("Could not load saved plan:", err);
  }
}

async function savePlan() {
  const token = getToken();
  if (!token) return;
  try {
    await fetch(`${API_BASE}/api/plan`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token
      },
      body: JSON.stringify(readPlan())
    });
  } catch (err) {
    console.warn("Could not save plan:", err);
  }
}

// Load the user's saved plan when the app starts (only after login/access check).
loadSavedPlan();

// ---- Dynamic Analysis & Risk Scoring (Decision Lab) ----
function formatINR(n) {
  const sign = n < 0 ? "-" : "";
  return sign + "₹" + Math.abs(Math.round(n)).toLocaleString(numLocale());
}

// Compact "₹34k" style used by the scenario cards (digits follow language).
function formatK(n) {
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  let k = abs / 1000;
  k = Math.round(k * 10) / 10;
  const loc = numLocale();
  const s = Number.isInteger(k) ? k.toLocaleString(loc) : k.toLocaleString(loc, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  return sign + "₹" + s + "k";
}

function setRiskPill(risk) {
  const pill = document.getElementById("riskPill");
  pill.classList.remove("risk-low", "risk-med", "risk-high");
  const riskKey = risk === "LOW" ? "LOW" : risk === "MEDIUM" ? "MEDIUM" : "HIGH";
  const label = riskKey === "LOW" ? t("risk_low") : riskKey === "MEDIUM" ? t("risk_med") : t("risk_high");
  pill.textContent = "RISK: " + label;
  pill.classList.add(riskKey === "LOW" ? "risk-low" : riskKey === "MEDIUM" ? "risk-med" : "risk-high");
}

// Locale for numbers follows the selected language (native digits).
function numLocale() {
  const l = (window.I18N ? window.I18N.getLang() : "en");
  // -u-nu-deva forces Devanagari digits (hi-IN alone defaults to Latin digits).
  return l === "hi" ? "hi-IN-u-nu-deva" : l === "bn" ? "bn-IN" : "en-IN";
}

// Latest analysis data, kept for the debate agents and simulation.
let lastAnalysis = null;
let lastPlan = null;

function renderDecision(plan, data) {
  // Keep the latest data for the debate agents.
  lastAnalysis = data;
  lastPlan = plan;

  // Risk pill
  setRiskPill(data.risk);

  // Opportunity bullets (reflect the user's plan)
  const adv = [];
  if (plan.fodder_land) adv.push(t("adv_fod_on"));
  else adv.push(t("adv_fod_off"));
  if (plan.family_labour) adv.push(t("adv_fam_on"));
  else adv.push(t("adv_fam_off"));
  if (plan.distributor) adv.push(t("adv_buy_on"));
  else adv.push(t("adv_buy_off"));
  document.getElementById("advocateList").innerHTML = adv
    .map(x => `<li><span>+</span> ${x}</li>`).join("");

  // Risk bullets (reflect the user's plan)
  const risk = [];
  if (plan.existing_shed) risk.push(t("rsk_shed_on"));
  else risk.push(t("rsk_shed_off"));
  risk.push(t("rsk_output", { cows: plan.cows }));
  risk.push(t("rsk_loan", { loan: formatINR(data.loan_needed), pk: (data.project_cost / 1000).toFixed(0) }));
  document.getElementById("challengerList").innerHTML = risk
    .map(x => `<li><span>!</span> ${x}</li>`).join("");

  // Recommendation (backend sends English; map the 3 known strings to current lang)
  const recMap = {
    "Plan looks reasonably serviceable under these assumptions.": t("rec_low_x"),
    "Plan needs adjustment before taking the full loan.": t("rec_med_x"),
    "Loan pressure is high. Consider changing the business configuration before borrowing.": t("rec_high_x")
  };
  const recText = recMap[data.recommendation] || data.recommendation;
  const rec = {
    LOW: [t("rec_low"), recText],
    MEDIUM: [t("rec_med"), recText],
    HIGH: [t("rec_high"), recText]
  }[data.risk] || ["Review your plan", recText];
  document.getElementById("recTitle").textContent = rec[0];
  document.getElementById("recText").textContent = rec[1];

  renderSwot(plan, data);
  renderScheme(data);
}

function swotLi(x) {
  return `<li>${x}</li>`;
}

// SWOT feasibility card (PS Module 1 §3) built from YOUR numbers, not generic text.
function renderSwot(plan, data) {
  const nb = (window._lastNearby && window._lastNearby.data) || null;
  const nbCount = nb && nb.count ? nb.count : 0;
  const S = [], W = [], O = [], T = [];
  if (plan.fodder_land) S.push(t("adv_fod_on")); else W.push(t("adv_fod_off"));
  if (plan.family_labour) S.push(t("adv_fam_on")); else W.push(t("adv_fam_off"));
  if (plan.distributor) S.push(t("adv_buy_on")); else W.push(t("adv_buy_off"));
  if (plan.existing_shed) S.push(t("rsk_shed_on")); else W.push(t("rsk_shed_off"));
  if (data.after_emi >= 5000) S.push(t("sw_s_cushion", { n: formatINR(data.after_emi) }));
  else W.push(t("sw_w_thin", { n: formatINR(data.after_emi) }));
  if (data.loan_needed > Math.max(4 * Number(plan.capital || 0), 150000)) {
    W.push(t("sw_w_lev", { loan: formatINR(data.loan_needed) }));
  }
  if (nbCount > 0) O.push(t("sw_o_buyers", { n: nbCount.toLocaleString(numLocale()) }));
  else O.push(t("sw_o_mapmore"));
  const nbGaps = (nb && nb.gaps) || [];
  nbGaps.slice(0, 2).forEach(g => O.push(t("sw_o_gap", { type: g })));
  if (!plan.distributor) O.push(t("sw_o_findbuyer"));
  else O.push(t("sw_o_route"));
  O.push(t("sw_o_scale"));
  const dip = Math.round(data.estimated_monthly_revenue * 0.10);
  T.push(t("sw_t_price", { n: formatINR(dip) }));
  T.push(t("sw_t_feed"));
  const ops = data.operating_surplus > 0 ? Math.round(data.estimated_emi / data.operating_surplus * 100) : 99;
  T.push(t("sw_t_emi", { p: ops.toLocaleString(numLocale()) }));
  if (!plan.distributor) T.push(t("sw_t_single"));
  document.getElementById("swotS").innerHTML = S.slice(0, 4).map(swotLi).join("");
  document.getElementById("swotW").innerHTML = W.slice(0, 4).map(swotLi).join("");
  document.getElementById("swotO").innerHTML = O.slice(0, 4).map(swotLi).join("");
  document.getElementById("swotT").innerHTML = T.slice(0, 4).map(swotLi).join("");
  const sp = document.getElementById("swotRiskPill");
  if (sp) {
    sp.textContent = "RISK: " + (data.risk === "LOW" ? t("risk_low") : data.risk === "MEDIUM" ? t("risk_med") : t("risk_high"));
    sp.className = "score-pill " + (data.risk === "LOW" ? "risk-low" : data.risk === "MEDIUM" ? "risk-med" : "risk-high");
  }
}

// Scheme Router card (PS Module 2): structuring + quarterly schedule.
function renderScheme(data) {
  const sch = data.scheme;
  if (!sch) return;
  const isMicro = sch.key === "micro";
  document.getElementById("schemeName").textContent = isMicro ? t("sch_micro") : t("sch_term");
  const pill = document.getElementById("schemePill");
  pill.textContent = data.eligible ? t("sc_elig_ok") : t("sc_elig_no");
  pill.className = "score-pill " + (data.eligible ? "risk-low" : "risk-high");
  document.getElementById("schemeTerms").textContent = t("sc_terms", {
    rate: (sch.annual_interest * 100).toLocaleString(numLocale()),
    yrs: sch.tenure_years.toLocaleString(numLocale()),
    mor: sch.moratorium_months.toLocaleString(numLocale())
  });
  const sRow = (l, v) => `<div class="opt-row"><span>${l}</span><b>${v}</b></div>`;
  document.getElementById("schemeBody").innerHTML =
    sRow(t("opt_cost"), formatINR(data.project_cost)) +
    sRow(t("sc_margin"), formatINR(data.margin_required)) +
    sRow(t("own_cap"), formatINR(data.own_capital)) +
    sRow(t("opt_loan"), formatINR(data.loan_needed)) +
    sRow(t("sc_wc"), formatINR(data.working_capital_3mo || data.monthly_cost * 3)) +
    sRow(t("sc_emi"), formatINR(data.estimated_emi));
  document.getElementById("schemeEntBody").innerHTML =
    sRow(t("sc_maxproj"), formatINR(data.max_supportable_project)) +
    sRow(t("sc_maxloan"), formatINR(data.max_loan_90pct)) +
    sRow(t("sc_tenure"), t("sc_yrs", { n: sch.tenure_years.toLocaleString(numLocale()) })) +
    sRow(t("sc_moratorium"), t("sc_mos", { n: sch.moratorium_months.toLocaleString(numLocale()) })) +
    sRow(t("opt_risk"), riskLabel(data.risk));
  const elig = document.getElementById("schemeElig");
  if (data.eligible) {
    elig.textContent = t("sc_elig_ok_txt");
    elig.className = "scheme-elig ok";
  } else {
    elig.textContent = t("sc_elig_no_txt", { n: formatINR(data.margin_shortfall) });
    elig.className = "scheme-elig warn";
  }
  const tb = document.getElementById("schemeSched");
  if (tb && Array.isArray(data.quarterly_schedule)) {
    tb.innerHTML = data.quarterly_schedule.map(q =>
      `<tr class="${q.moratorium ? "moratorium" : ""}"><td>Q${q.q.toLocaleString(numLocale())}</td><td>${q.months}</td>` +
      `<td>${formatINR(q.payment)}</td><td>${formatINR(q.principal)}</td>` +
      `<td>${formatINR(q.interest)}</td><td>${formatINR(q.balance)}</td></tr>`
    ).join("");
  }
}

async function runAnalysis() {
  if (runAnalysis._running) return;
  runAnalysis._running = true;
  const plan = readPlan();
  savePlan();
  const token = getToken();
  try {
    const res = await fetch(`${API_BASE}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(plan)
    });
    if (!res.ok) { notify(t("t_analysis_fail")); return; }
    const data = await res.json();
    renderDecision(plan, data);
    // Run 24-month simulation automatically to keep forecast chart and metrics in sync
    runSimulation();
  } catch (err) {
    console.error("Analysis error:", err);
    notify(t("t_no_server"));
  } finally {
    runAnalysis._running = false;
  }
}

// Run analysis when the user opens the Decision Lab (keeps it fresh).
const _showPage = showPage;
showPage = function (name) {
  if (name === "decision" && lastPage !== "decision") {
    runAnalysis();
  }
  return _showPage(name);
};

// Analyze button
document.getElementById("analyzeBtn").addEventListener("click", () => {
  const plan = readPlan();
  runAnalysis();
  notify(t("t_plan_saved", { cows: plan.cows.toLocaleString(numLocale()), cap: plan.capital.toLocaleString(numLocale()) }));
  setTimeout(() => showPage("decision"), 550);
});

// ---- Voice -> Plan form mapper ----
const NUMBER_WORDS = {
  zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9,
  ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16,
  seventeen: 17, eighteen: 18, nineteen: 19,
  twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60, seventy: 70, eighty: 80, ninety: 90
};
const SCALE_WORDS = { hundred: 100, thousand: 1000, lakh: 100000, lac: 100000, crore: 10000000 };
const NUMBER_WORDS_HI = {
  "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5, "छह": 6, "छै": 6, "सात": 7, "आठ": 8, "नौ": 9,
  "दस": 10, "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15, "पन्द्रह": 15, "सोलह": 16, "सत्रह": 17, "अट्ठारह": 18, "उन्नीस": 19, "उननीस": 19,
  "बीस": 20, "तीस": 30, "चालीस": 40, "पचास": 50, "साठ": 60, "सत्तर": 70, "अस्सी": 80, "नब्बे": 90,
  "ek": 1, "do": 2, "teen": 3, "tin": 3, "chaar": 4, "paanch": 5, "panch": 5, "cheh": 6, "chhah": 6,
  "saat": 7, "sath": 7, "aath": 8, "ath": 8, "nau": 9, "nao": 9, "das": 10, "dus": 10,
  "gyarah": 11, "barah": 12, "terah": 13, "chaudah": 14, "pandrah": 15, "solah": 16, "satrah": 17, "atharah": 18, "unnis": 19,
  "bees": 20, "bis": 20, "tees": 30, "chalis": 40, "pachas": 50, "sattar": 70, "assi": 80, "nabbe": 90
};
const SCALE_WORDS_HI = {
  "सौ": 100, "हज़ार": 1000, "हजार": 1000, "लाख": 100000, "करोड़": 10000000, "करोड": 10000000,
  "sau": 100, "hazaar": 1000, "hajaar": 1000, "hazar": 1000, "lakh": 100000, "lac": 100000, "lakhs": 100000, "crore": 10000000
};
const NUMBER_WORDS_BN = {
  "এক": 1, "দুই": 2, "দুটো": 2, "দুটি": 2, "তিন": 3, "তিনটি": 3, "তিনটে": 3, "চার": 4, "চারটি": 4, "পাঁচ": 5, "ছয়": 6, "ছটা": 6, "সাত": 7, "আট": 8, "নয়": 9, "নটা": 9,
  "দশ": 10, "এগারো": 11, "বারো": 12, "তেরো": 13, "চৌদ্দ": 14, "পনেরো": 15, "পনরো": 15, "ষোলো": 16, "ষোল": 16, "সতেরো": 17, "আঠারো": 18, "ঊনিশ": 19, "উনিশ": 19,
  "কুড়ি": 20, "বিশ": 20, "তিরিশ": 30, "চল্লিশ": 40, "পঞ্চাশ": 50, "ষাট": 60, "সত্তর": 70, "আশি": 80, "নব্বই": 90,
  "dui": 2, "tin": 3, "char": 4, "paanch": 5, "panch": 5, "chhoy": 6, "saat": 7, "aat": 8, "noy": 9, "dosh": 10,
  "egaro": 11, "baro": 12, "tero": 13, "kuri": 20, "bish": 20, "tirish": 30
};
const SCALE_WORDS_BN = {
  "শ": 100, "শো": 100, "একশ": 100, "হাজার": 1000, "লাখ": 100000, "লক্ষ": 100000, "কোটি": 10000000,
  "sho": 100, "hajar": 1000, "hazaar": 1000, "lakh": 100000, "lac": 100000, "kuti": 10000000, "koti": 10000000
};
function voiceLang() { return (window.I18N ? window.I18N.getLang() : "en"); }

function wordsToNumbers(text) {
  // Normalize native digits (३, ৪…) to Latin so digit tokens parse in any language.
  const norm = String(text).replace(/[०-९]/g, c => "०१२३४५६७८९".indexOf(c)).replace(/[০-৯]/g, c => "০১২৩৪৫৬৭৮৯".indexOf(c));
  const tokens = norm.toLowerCase().replace(/[-,]/g, " ").split(/\s+/).filter(Boolean);
  const numbers = [];
  // Mixed speech: always English + native maps for the active language.
  const vl = voiceLang();
  const NW = Object.assign({}, NUMBER_WORDS,
    vl === "hi" ? NUMBER_WORDS_HI : vl === "bn" ? NUMBER_WORDS_BN : {});
  const SW = Object.assign({}, SCALE_WORDS,
    vl === "hi" ? SCALE_WORDS_HI : vl === "bn" ? SCALE_WORDS_BN : {});

  function parsePhrase(phraseTokens) {
    let total = 0;
    let subgroup = 0;
    let seen = false;
    for (const w of phraseTokens) {
      if (/^\d+$/.test(w)) {
        subgroup += parseInt(w, 10);
        seen = true;
        continue;
      }
      if (NW[w] !== undefined) {
        subgroup += NW[w];
        seen = true;
      } else if (SW[w] !== undefined) {
        const scale = SW[w];
        if (scale >= 100 && scale < 1000) {
          subgroup = subgroup === 0 ? 100 : subgroup * 100;
        } else {
          const group = (subgroup || 1) * scale;
          total += group;
          subgroup = 0;
        }
        seen = true;
      }
    }
    return { value: total + subgroup, seen };
  }

  let phrase = [];
  for (const token of tokens) {
    if (NW[token] !== undefined || SW[token] !== undefined || /^\d+$/.test(token)) {
      phrase.push(token);
    } else {
      if (phrase.length) {
        const r = parsePhrase(phrase);
        if (r.seen) numbers.push(r.value);
        phrase = [];
      }
    }
  }
  if (phrase.length) {
    const r = parsePhrase(phrase);
    if (r.seen) numbers.push(r.value);
  }
  return numbers;
}

const NEG_WORDS = ["no", "not", "dont", "don't", "doesn't", "without", "none", "unavailable", "lack", "no existing", "do not", "does not", "can't", "cant"];
const POS_WORDS = ["yes", "have", "got", "has", "there is", "there's", "available", "own", "with", "already"];
const NEG_WORDS_HI = ["नहीं", "नही", "बिना", "नहीं है", "नही है", "कोई नहीं"];
const POS_WORDS_HI = ["हाँ", "हां", "है", "हैं", "रखता", "रखती", "पास", "मौजूद", "उपलब्ध", "अपना", "अपनी", "मिला", "मिली", "पाया", "पायी", "लिया", "ली"];
const NEG_WORDS_BN = ["নয়", "নই", "নেই", "নাই", "ছাড়া", "বিনা"];
const POS_WORDS_BN = ["হ্যাঁ", "হ্যা", "আছে", "আছেন", "রয়েছে", "নিজের", "আমার", "পেয়েছি", "পেয়েছে", "পেলাম", "নিলাম", "নিয়েছি"];
const NEG_WORDS_LAT = ["nahi", "nahin", "naheen", "nei", "nai", "noy", "bina", "binaa", "mat"];
const POS_WORDS_LAT = ["haan", "han", "hai", "hain", "ache", "aache", "achhe", "rakhta", "rakhti", "paas", "nijer", "amar"];

// Decide yes/no for a single toggle.
// `keys` is an array of synonyms; the first phrase found in the transcript is used so that
// "distributor", "buyer", "market", "cowshed", "barn" etc. all map to the right toggle.
// Uses clause boundaries (and/but/comma/period) so neighbour statements don't interfere,
// then attributes "yes/no" to the keyword via the nearest signal word within that clause.
function decideToggle(t, keys) {
  const key = Array.isArray(keys) ? keys.find((k) => t.includes(k)) : keys;
  if (!key) return null;

  const wordIdx = t.indexOf(key);
  // nearest clause boundary before the keyword (EN + HI + BN conjunctions)
  const leftRe = /(?:\s+(?:but|and|also|so|however|और|लेकिन|पर|तथा|मगर|আর|কিন্তু|তবে|এবং)\s+|\s*,\s*|\s*\.\s*|।\s*)/g;
  const leftText = t.slice(0, wordIdx);
  let segStart = 0, m;
  while ((m = leftRe.exec(leftText)) !== null) segStart = m.index + m[0].length;

  // nearest clause boundary after the keyword
  const rightText = t.slice(wordIdx + key.length);
  const rm = rightText.match(/\s+(?:but|and|also|so|however|और|लेकिन|पर|तथा|मगर|আর|কিন্তু|তবে|এবং)\s+|\s*,\s*|\s*\.\s*|।\s*/);
  const segEnd = rm ? wordIdx + key.length + rm.index : t.length;

  const seg = t.slice(segStart, segEnd);
  const keyInSeg = wordIdx - segStart;

  // "don't have/own/with", "no ... have" -> negative even though "have" is present
  if (new RegExp("(?:dont|don't|doesn't|do not|does not|not|no|can't|cant)\\s+(?:have|own|got|with)\\b").test(seg)) {
    return false;
  }

  // HI/BN explicit negation wins over nearby positive words
  // (e.g. "शेड नहीं है": नहीं beats है; \b is ASCII-only so use explicit separators).
  if (/(^|[ ,.।!?])(नहीं|नही|बिना)(?=[ ,.।!?]|$)/.test(seg)) {
    return false;
  }
  if (/(^|[ ,.।!?])(নয়|নেই|নাই|ছাড়া|বিনা)(?=[ ,.।!?]|$)/.test(seg)) {
    return false;
  }

  // nearest signal word (positive/negative) to the keyword within the clause.
  // Native + Latin-transliterated markers merge in for HI/BN (mixed speech).
  const vl2 = (window.I18N ? window.I18N.getLang() : "en");
  const EXTRA_NEG = vl2 === "hi" ? NEG_WORDS_HI : vl2 === "bn" ? NEG_WORDS_BN : [];
  const EXTRA_POS = vl2 === "hi" ? POS_WORDS_HI : vl2 === "bn" ? POS_WORDS_BN : [];
  const markers = [];
  for (const w of POS_WORDS.concat(EXTRA_POS, POS_WORDS_LAT)) { let i = seg.indexOf(w); while (i !== -1) { markers.push({ pos: i, neg: false, dist: Math.abs(i - keyInSeg) }); i = seg.indexOf(w, i + 1); } }
  for (const w of NEG_WORDS.concat(EXTRA_NEG, NEG_WORDS_LAT)) { let i = seg.indexOf(w); while (i !== -1) { markers.push({ pos: i, neg: true, dist: Math.abs(i - keyInSeg) }); i = seg.indexOf(w, i + 1); } }

  if (!markers.length) return null;
  markers.sort((a, b) => a.dist - b.dist);
  return markers[0].neg ? false : true;
}

function applySpeechToPlan(text) {
  // Normalize native cow nouns to "cow" so one regex covers EN/HI/BN + Hinglish.
  const raw = (text || "");
  let tx = raw.toLowerCase()
    .replace(/गायें|गाय|गौ|गऊ/g, "cow").replace(/গাভী|গরু/g, "cow")
    .replace(/gaaye|gaay|gaiya|goru/g, "cow");
  const numbers = wordsToNumbers(raw);
  const applied = [];
  let cowCount = null;
  let cowNumber = null;
  let capital = null;

  // ---- Cows: number near cow/cattle/animal/buffalo (EN + HI + BN words) ----
  // Accepts both orders: "three cows" and "cows three" (গরু তিনটি).
  const cowMatch = tx.match(/(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|twenty|thirty|एक|दो|तीन|चार|पाँच|पांच|छह|सात|आठ|नौ|दस|ek|do|teen|tin|char|chaar|paanch|panch|cheh|saat|sath|aath|nau|das|एक|দুই|তিন|চার|পাঁচ|ছয়|সাত|আট|নয়|দশ|dui|noy|dosh)(?:টি|টা|টো|টে|জন|খানা)?[ ,]*\s*(cow|cattle|animal|buffalo)/)
    || tx.match(/(cow|cattle|animal|buffalo)[ ,]*(?:টি|টা|টো|টে|জন|খানা)?\s*(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|twenty|thirty|एक|दो|तीन|चार|पाँच|पांच|छह|सात|आठ|नौ|दस|ek|do|teen|tin|char|chaar|paanch|panch|cheh|saat|sath|aath|nau|das|एक|দুই|তিন|চার|পাঁচ|ছয়|সাত|আট|নয়|দশ|dui|noy|dosh)/);
  if (cowMatch) {
    const parsed = cowMatch[1] !== undefined && /^(cow|cattle|animal|buffalo)$/.test(cowMatch[1]) ? cowMatch[2] : cowMatch[1];
    const wordMap = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, twenty: 20, thirty: 30 };
    const hiMap = { "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5, "छह": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10, "ek": 1, "do": 2, "teen": 3, "tin": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "cheh": 6, "saat": 7, "sath": 7, "aath": 8, "nau": 9, "das": 10 };
    const bnMap = { "এক": 1, "দুই": 2, "তিন": 3, "চার": 4, "পাঁচ": 5, "ছয়": 6, "সাত": 7, "আট": 8, "নয়": 9, "দশ": 10, "dui": 2, "noy": 9, "dosh": 10 };
    const n = /^\d+$/.test(parsed) ? parseInt(parsed, 10) : (wordMap[parsed] !== undefined ? wordMap[parsed] : (hiMap[parsed] !== undefined ? hiMap[parsed] : bnMap[parsed]));
    if (n !== undefined) {
      cowCount = Math.max(1, Math.min(12, n));
      cowNumber = n;
      applied.push(`${cowCount.toLocaleString(numLocale())} ${t("sp_cows")}`);
    }
  }

  // Fallback: lone small number + cow noun but no adjacency (e.g. "गायें हैं पांच",
  // "গরু আছে তিনটি") — clearly a herd size, not capital.
  if (cowCount === null && numbers.length === 1 && numbers[0] >= 1 && numbers[0] <= 12
      && /(cow|cattle|animal|buffalo)/.test(tx)) {
    const loneMoney = /(lakh|lac|crore|thousand|hundred|लाख|करोड़|करोड|लक्ष|কোটি|हज़ार|हजार|capital|rupee|पूँजी|मूलधन|মূলধন|টাকা|पैसे|पैसा)/.test(tx);
    if (!loneMoney) {
      cowCount = Math.max(1, Math.min(12, numbers[0]));
      cowNumber = numbers[0];
      applied.push(`${cowCount.toLocaleString(numLocale())} ${t("sp_cows")}`);
    }
  }

  // ---- Capital: the largest number that is not the cow count ----
  const capitalCandidates = numbers.filter((n) => cowNumber === null || n !== cowNumber);
  capital = capitalCandidates.length ? Math.max(...capitalCandidates) : null;

  // Treat a lone small number as "thousand" only if it's clearly money.
  const capitalHint = /(capital|rupee|rupees|invest|investing|money|budget|amount|lakh|thousand|rs|पूँजी|पूंजी|रुपये|रुपए|पैसे|पैसा|निवेश|मूलधन|মূলধন|টাকা|টাকার|বিনিয়োগ)/.test(tx);
  if (capital === null && capitalHint && cowCount !== null && numbers.includes(cowNumber)) {
    capital = cowNumber * 1000;
  }

  if (capital !== null && capital < 1000) capital = capital * 1000;

  // ---- Toggles: decide each item from the words NEAR it (local context) ----
  for (const item of [
    { btn: document.querySelector('.toggle[data-toggle="shed"]'), key: ["shed", "cowshed", "stable", "barn", "shedding", "शेड", "गौशाला", "गोशाला", "गौशाले", "গোয়াল", "শেড", "গোয়ালঘর"], label: "sp_shed" },
    { btn: document.querySelector('.toggle[data-toggle="fodder"]'), key: ["fodder", "feed", "चारा", "हरा चारा", "भूसा", "खाद्य", "सूखा चारा", "খাদ্য", "সবুজ খাদ্য", "ঘাস", "খড়"], label: "sp_fodder" },
    { btn: document.querySelector('.toggle[data-toggle="family"]'), key: ["family", "family labour", "परिवार", "पारिवारिक", "परिवारिक", "পরিবার", "পারিবারিক"], label: "sp_family" },
    { btn: document.querySelector('.toggle[data-toggle="buyer"]'), key: ["buyer", "distributor", "market", "supplier", "retailer", "dealer", "customer", "खरीदार", "खरीददार", "ग्राहक", "क्रेता", "ক্রেতা", "কাস্টমার", "বাজার"], label: "sp_buyer" }
  ]) {
    if (!item.btn) continue;
    const decision = decideToggle(tx, item.key);
    if (decision === null) continue;
    setToggle(item.btn, decision);
    applied.push(`${decision ? t("sp_yes") : t("sp_no")} ${t(item.label)}`);
  }

  // ---- Apply capital ----
  if (capital !== null && capital > 0) {
    document.getElementById("capitalInput").value = capital;
    formatCapitalInput();
    applied.push(`₹${capital.toLocaleString(numLocale())} ${t("sp_capital")}`);
  }

  // ---- Apply cow count ----
  if (cowCount !== null) {
    cows = cowCount;
    document.getElementById("cowCount").textContent = cows.toLocaleString(numLocale());
  }

  console.debug("[voice] transcript:", text, "| numbers:", numbers, "| applied:", applied);
  return applied;
}

// Voice input — record audio, convert to WAV, send to backend for transcription
const voiceBtn = document.getElementById("voiceBtn");

function encodeWav(audioBuffer) {
  const sampleRate = audioBuffer.sampleRate;
  const samples = audioBuffer.getChannelData(0);
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  const writeString = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };

  writeString(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }

  return new Blob([view], { type: "audio/wav" });
}

let mediaRecorder = null;
let mediaStream = null;
let recordedBlobs = [];
let isRecording = false;
let decodeContext = null;
let currentMediaRecorder = null;

const voiceLbl = {
  get def() { return '<span class="mic">◉</span> ' + t("hero_speak"); },
  get rec() { return '<span class="mic">◉</span> ' + t("voice_stop"); },
  get proc() { return t("voice_proc"); },
  get fail() { return t("voice_fail"); }
};

function setVoiceLabel(html, recording) {
  voiceBtn.innerHTML = html;
  voiceBtn.classList.toggle("recording-pulse", recording);
}

function stopRecording() {
  if (currentMediaRecorder && currentMediaRecorder.state === "recording") {
    currentMediaRecorder.stop();
  }
}

voiceBtn.addEventListener("click", async () => {
  if (isRecording) {
    stopRecording();
    return;
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    console.error("Microphone access denied:", err);
    alert(t("t_mic_denied"));
    return;
  }

  // Pick a supported container (prefer webm, fall back to any available type)
  const mimeTypes = ["audio/webm", "audio/ogg", "audio/mp4"];
  const supported = mimeTypes.find((t) => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t));

  try {
    const mr = new MediaRecorder(stream, supported ? { mimeType: supported } : undefined);
    recordedBlobs = [];
    currentMediaRecorder = mr;
    mediaStream = stream;

    mr.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) recordedBlobs.push(event.data);
    };

    mr.onstop = async () => {
      isRecording = false;
      setVoiceLabel(voiceLbl.proc, false);

      if (!recordedBlobs.length) {
        setVoiceLabel(voiceLbl.def, false);
        notify(t("t_nothing"));
        stream.getTracks().forEach((t) => t.stop());
        return;
      }

      try {
        const blob = new Blob(recordedBlobs, { type: supported || stream.audio || "audio/webm" });
        const arrayBuffer = await blob.arrayBuffer();

        let audioBuffer;
        try {
          if (!decodeContext) {
            decodeContext = new (window.AudioContext || window.webkitAudioContext)();
          }
          if (decodeContext.state === "suspended") { await decodeContext.resume(); }
          audioBuffer = await decodeContext.decodeAudioData(arrayBuffer);
        } catch (decErr) {
          const detail = (decErr && (decErr.message || decErr.name)) || "unknown";
          console.error("Audio decode failed:", detail);
          try { window._lastVoiceError = { where: "decode", detail: String(detail), time: Date.now() }; } catch (e) {}
          setVoiceLabel(voiceLbl.fail, false);
          notify(t("t_decode_fail") + " [" + detail + "]");
          stream.getTracks().forEach((tr) => tr.stop());
          return;
        }
        const wavBlob = encodeWav(audioBuffer);

        const formData = new FormData();
        formData.append("audio_file", wavBlob, "recording.wav");
        formData.append("lang", window.I18N ? window.I18N.getLang() : "en");

        const response = await fetch(`${API_BASE}/api/transcribe`, {
          method: "POST",
          body: formData
        });

        const data = await response.json();
        if (data.status === "success" && data.transcript) {
          const applied = applySpeechToPlan(data.transcript);
          setVoiceLabel(voiceLbl.def, false);

          if (applied && applied.length) {
            notify(t("t_using", { list: applied.join(", ") }));
            showPage("plan");
          } else {
            notify(t("t_heard", { txt: data.transcript }));
          }
        } else {
          setVoiceLabel(voiceLbl.fail, false);
          notify(data.error || t("t_trans_fail"));
        }
      } catch (err) {
        const detail = (err && (err.message || err.name)) || "unknown";
        console.error("Transcription upload failed:", detail);
        try { window._lastVoiceError = { where: "upload", detail: String(detail), time: Date.now() }; } catch (e) {}
        setVoiceLabel(voiceLbl.fail, false);
        notify(t("t_trans_backend") + " [" + detail + "]");
      }

      stream.getTracks().forEach((t) => t.stop());
    };

    mr.onerror = () => {
      isRecording = false;
      stream.getTracks().forEach((t) => t.stop());
      setVoiceLabel(voiceLbl.def, false);
      notify(t("t_rec_err"));
    };

    mr.start();
    isRecording = true;
    setVoiceLabel(voiceLbl.rec, true);
    notify(t("voice_listen"));
  } catch (err) {
    console.error("Recording setup failed:", err);
    stream.getTracks().forEach((t) => t.stop());
    setVoiceLabel(voiceLbl.def, false);
    notify(t("t_rec_setup"));
  }
});

// Simulation engine (Monte Carlo & deterministic 24-month projection)
// Chart geometry: viewBox 720 x 190. curvy (Catmull-Rom -> bezier) paths.
const simBtn = document.getElementById("simulateBtn");
const simFill = document.getElementById("simFill");
const simLine = document.getElementById("simLine");
const simStress = document.getElementById("simStress");
const simOpportunity = document.getElementById("simOpportunity");
const simZero = document.getElementById("simZero");

function fmt1(x) {
  return Number(x || 0).toLocaleString(numLocale(), { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}
function formatAxisK(v) {
  const abs = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  const k = abs / 1000;
  const loc = numLocale();
  const s = k >= 100 ? Math.round(k).toLocaleString(loc) : (Number.isInteger(k) ? k.toLocaleString(loc) : k.toLocaleString(loc, { minimumFractionDigits: 1, maximumFractionDigits: 1 }));
  return sign + "₹" + s + "k";
}

function smoothPath(points) {
  if (!points || points.length < 2) return "";
  let d = `M${points[0][0].toFixed(1)} ${points[0][1].toFixed(1)}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i - 1] || points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] || p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C${c1x.toFixed(1)} ${c1y.toFixed(1)}, ${c2x.toFixed(1)} ${c2y.toFixed(1)}, ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
  }
  return d;
}

function renderSimulation(data) {
  if (!data || !data.expected) return;
  const months = data.months || 25;
  const series = { expected: data.expected, opportunity: data.opportunity, stress: data.stress };

  // Y range with headroom so curves and labels aren't clipped at edges.
  let minV = Math.min(data.min_y !== undefined ? data.min_y : -10000, 0);
  let maxV = Math.max(data.max_y !== undefined ? data.max_y : 50000, 10000);
  const pad = Math.max(1000, (maxV - minV) * 0.08);
  minV -= pad;
  maxV += pad;

  const valueToY = (v) => 190 - ((v - minV) / (maxV - minV)) * 190;

  function toPoints(arr) {
    const len = arr.length;
    return arr.map((v, i) => [((i / (len - 1)) * 720), valueToY(v)]);
  }

  const expPts = toPoints(series.expected);
  const oppPts = toPoints(series.opportunity);
  const strPts = toPoints(series.stress);

  const oppD = smoothPath(oppPts);
  const expD = smoothPath(expPts);
  const strD = smoothPath(strPts);

  // Position break-even zero baseline
  if (simZero) {
    const zeroY = Math.max(0, Math.min(190, valueToY(0)));
    simZero.setAttribute("y1", zeroY.toFixed(1));
    simZero.setAttribute("y2", zeroY.toFixed(1));
  }

  if (simOpportunity) simOpportunity.setAttribute("d", oppD);
  if (simFill) simFill.setAttribute("d", expD + ` L720 190 L0 190 Z`);
  if (simLine) simLine.setAttribute("d", expD);
  if (simStress) simStress.setAttribute("d", strD);

  // Update Y-axis tick labels (4 evenly spaced ticks over the range).
  const ticks = [0, 1, 2, 3].map(t => minV + ((maxV - minV) * (3 - t)) / 3);
  const t0 = document.getElementById("yTick0");
  const t1 = document.getElementById("yTick1");
  const t2 = document.getElementById("yTick2");
  const t3 = document.getElementById("yTick3");
  if (t0) t0.textContent = formatAxisK(ticks[0]);
  if (t1) t1.textContent = formatAxisK(ticks[1]);
  if (t2) t2.textContent = formatAxisK(ticks[2]);
  if (t3) t3.textContent = formatAxisK(ticks[3]);

  window._lastSim = data;
  // Scenario values (cumulative surplus after 24 months)
  if (data.final) {
    const scOpp = document.getElementById("scenarioOpportunity");
    const scExp = document.getElementById("scenarioExpected");
    const scStr = document.getElementById("scenarioStress");
    if (scOpp) scOpp.textContent = formatK(data.final.opportunity);
    if (scExp) scExp.textContent = formatK(data.final.expected);
    if (scStr) scStr.textContent = formatK(data.final.stress);
  }
}

async function runSimulation() {
  const token = getToken();
  if (simBtn) {
    simBtn.innerHTML = t("simulating_lbl");
    simBtn.disabled = true;
  }

  try {
    const res = await fetch(`${API_BASE}/api/simulate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token
      },
      body: JSON.stringify(readPlan())
    });
    if (!res.ok) throw new Error("simulate failed");
    const data = await res.json();
    renderSimulation(data);
    return data;
  } catch (err) {
    console.error("Simulate error:", err);
    notify(t("t_sim_fail"));
    return null;
  } finally {
    if (simBtn) {
      simBtn.innerHTML = t("sim_btn");
      simBtn.disabled = false;
    }
  }
}

if (simBtn) {
  simBtn.addEventListener("click", async () => {
    const data = await runSimulation();
    if (data) {
      notify(t("t_sim_ok"));
    }
  });
}

// Optimization
const optimizeModal = document.getElementById("optimizeModal");
const optCurrentBody = document.getElementById("optCurrentBody");
const optOptimalBody = document.getElementById("optOptimalBody");

function riskLabel(r) { return r === "LOW" ? t("risk_low") : r === "MEDIUM" ? t("risk_med") : t("risk_high"); }

function optRow(label, value) {
  return `<div class="opt-row"><span>${label}</span><b>${value}</b></div>`;
}

let _optimizeResult = null;

function renderOptimize(data) {
  const cur = data.current.analysis;
  const opt = data.optimal.analysis;

  document.getElementById("optReason").textContent = data.reason;
  // Never trust a stale "already optimal" for HIGH risk — force honest title.
  let status = data.status || ((data.optimal.plan.cows === data.current.plan.cows) ? "already_optimal" : "optimized");
  if (data.current.analysis.risk === "HIGH" && status === "already_optimal") status = "no_viable";
  const titleMap = {
    already_optimal: t("mo_already"),
    no_viable: t("mo_noviable"),
    over_leveraged: t("mo_overlev"),
    fixable: t("mo_fixable"),
    optimized: t("mo_opt")
  };
  document.getElementById("optTitle").textContent = titleMap[status] || "Optimized plan";
  // Offer Apply when the suggestion actually changes something (cows or any toggle).
  const planKeys = ["cows", "capital", "existing_shed", "fodder_land", "family_labour", "distributor"];
  const differs = planKeys.some(k => data.optimal.plan[k] !== data.current.plan[k]);
  const canApply = (status === "optimized" || status === "fixable") && differs;
  document.getElementById("optApply").style.display = canApply ? "" : "none";

  const cowFmt = (c) => Number(c).toLocaleString(numLocale());
  optCurrentBody.innerHTML =
    optRow(t("opt_cows"), cowFmt(cur.cows)) +
    optRow(t("opt_cost"), formatINR(cur.project_cost)) +
    optRow(t("opt_loan"), formatINR(cur.loan_needed)) +
    optRow(t("opt_surplus"), formatINR(cur.after_emi)) +
    optRow(t("opt_risk"), riskLabel(cur.risk));

  optOptimalBody.innerHTML =
    optRow(t("opt_cows"), cowFmt(opt.cows)) +
    optRow(t("opt_cost"), formatINR(opt.project_cost)) +
    optRow(t("opt_loan"), formatINR(opt.loan_needed)) +
    optRow(t("opt_surplus"), formatINR(opt.after_emi)) +
    optRow(t("opt_risk"), riskLabel(opt.risk));

  _optimizeResult = data.optimal;
  window._lastOptimize = data;
  optimizeModal.hidden = false;
}

// Re-render language-dependent dynamic UI when the language changes.
window.onLanguageApplied = function () {
  try {
    if (lastPlan && lastAnalysis) renderDecision(lastPlan, lastAnalysis);
    else if (lastAnalysis) setRiskPill(lastAnalysis.risk);
  } catch (e) {}
  try {
    if (window._lastNearby && window._lastNearby.data) {
      renderNearby(window._lastNearby.data, { requestId: nearbyRequestId });
    }
  } catch (e) {}
  try {
    if (window._lastSim) renderSimulation(window._lastSim);
  } catch (e) {}
  try {
    document.getElementById("cowCount").textContent = cows.toLocaleString(numLocale());
  } catch (e) {}
  try {
    const ce = document.getElementById("capitalInput");
    if (ce && document.activeElement !== ce) formatCapitalInput();
  } catch (e) {}
  try {
    if (window._lastOptimize && !optimizeModal.hidden) renderOptimize(window._lastOptimize);
  } catch (e) {}
  try {
    if (!chatOverlay.hidden) {
      document.getElementById("chatAgentRole").textContent = t(activeAgent === "advocate" ? "ag_adv_role" : "ag_ch_role");
      document.getElementById("chatAgentName").textContent = t(activeAgent === "advocate" ? "ag_adv_name" : "ag_ch_name");
      const chips = activeAgent === "advocate"
        ? [t("chip_a1"), t("chip_a2"), t("chip_a3")]
        : [t("chip_c1"), t("chip_c2"), t("chip_c3")];
      chatChips.innerHTML = chips.map(c => `<button class="chat-chip" data-q="${c}">${c}</button>`).join("");
    }
  } catch (e) {}
  try {
    if (simBtn && simBtn.disabled === false && simBtn.innerHTML.indexOf("⏳") === -1) simBtn.innerHTML = t("sim_btn");
    const ob = document.getElementById("optimizeBtn");
    if (ob && ob.innerHTML.indexOf("⏳") === -1) ob.innerHTML = `<span>${t("opt_btn")}</span> <span>→</span>`;
    if (!isRecording && voiceBtn) voiceBtn.innerHTML = voiceLbl.def;
    if (useLocationBtn && !useLocationBtn.disabled) locBtnText.textContent = t("btn_use_loc");
  } catch (e) {}
};

document.getElementById("optimizeBtn").addEventListener("click", async () => {
  const token = getToken();
  const btn = document.getElementById("optimizeBtn");
  const original = btn.innerHTML;
  btn.innerHTML = t("optimizing_lbl");

  try {
    const res = await fetch(`${API_BASE}/api/optimize`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token
      },
      body: JSON.stringify({ ...readPlan(), lang: (window.I18N ? window.I18N.getLang() : "en") })
    });
    if (!res.ok) { notify(t("t_opt_fail")); return; }
    renderOptimize(await res.json());
  } catch (err) {
    console.error("Optimize error:", err);
    notify(t("t_opt_noserver"));
  } finally {
    btn.innerHTML = original;
  }
});

// Keep my plan -> just close
document.getElementById("optKeep").addEventListener("click", () => {
  optimizeModal.hidden = true;
});
// Close button
document.getElementById("optClose").addEventListener("click", () => {
  optimizeModal.hidden = true;
});
// Close on overlay click
optimizeModal.addEventListener("click", (e) => {
  if (e.target === optimizeModal) optimizeModal.hidden = true;
});

// Apply optimized plan -> update the form, save, re-run analysis, go to decision
document.getElementById("optApply").addEventListener("click", () => {
  if (!_optimizeResult) return;
  const p = _optimizeResult.plan;
  setCows(p.cows);
  document.getElementById("capitalInput").value = p.capital;
  formatCapitalInput();
  const map = { existing_shed: "shed", fodder_land: "fodder", family_labour: "family", distributor: "buyer" };
  for (const [key, dataKey] of Object.entries(map)) {
    if (typeof p[key] === "boolean") {
      setToggle(document.querySelector(`.toggle[data-toggle="${dataKey}"]`), p[key]);
    }
  }
  optimizeModal.hidden = true;
  savePlan();
  runAnalysis();
  notify(t("t_opt_applied", { cows: p.cows }));
  showPage("decision");
});

// ================== Interactive Debate Agents ==================
const AGENTS = {
  advocate: {
    icon: "↗",
    role: "THE OPPORTUNITY VIEW",
    name: "Opportunity Advisor",
    tone: "positive"
  },
  challenger: {
    icon: "!",
    role: "THE RISK VIEW",
    name: "Risk Challenger",
    tone: "cautious"
  }
};

const chatOverlay = document.getElementById("chatOverlay");
const chatBody = document.getElementById("chatBody");
const chatChips = document.getElementById("chatChips");
const chatInput = document.getElementById("chatInput");
let activeAgent = "advocate";

function fmtINR(n) { return formatINR(n); }

// Make sure we have analysis data for the current plan (fetch if needed).
async function ensureAnalysis() {
  if (lastAnalysis && lastPlan) return { plan: lastPlan, data: lastAnalysis };
  const plan = readPlan();
  try {
    const res = await fetch(`${API_BASE}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(plan)
    });
    if (res.ok) {
      const data = await res.json();
      lastAnalysis = data;
      lastPlan = plan;
      return { plan, data };
    }
  } catch (e) { /* fall through */ }
  return { plan, data: null };
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function renderChatText(text) {
  // Escape, then convert **bold** and newlines to HTML safely.
  let out = escapeHtml(text);
  out = out.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  out = out.replace(/\n/g, "<br>");
  return out;
}

function addChatMessage(text, who) {
  const div = document.createElement("div");
  div.className = "chat-msg " + who;
  if (who === "bot") {
    div.innerHTML = renderChatText(text);
  } else {
    div.textContent = text;
  }
  chatBody.appendChild(div);
  chatBody.scrollTop = chatBody.scrollHeight;
}

function addTyping() {
  const div = document.createElement("div");
  div.className = "chat-msg bot typing";
  div.id = "typingMsg";
  div.textContent = "…";
  chatBody.appendChild(div);
  chatBody.scrollTop = chatBody.scrollHeight;
  return div;
}
function removeTyping() {
  const t = document.getElementById("typingMsg");
  if (t) t.remove();
}

// ----- Contextual, rule-based reply engine -----
function agentReply(text, agent) {
  const p = lastPlan;
  const d = lastAnalysis;
  const tone = agent === "advocate" ? "positive" : "cautious";

  if (!d) {
    return "I couldn't pull your latest analysis. Please run your plan first, then ask me again.";
  }

  const q = text.toLowerCase();
  const rev = formatINR(d.estimated_monthly_revenue);
  const cost = formatINR(d.monthly_cost);
  const os = formatINR(d.operating_surplus);
  const surplus = formatINR(d.after_emi);
  const loan = formatINR(d.loan_needed);
  const emi = formatINR(d.estimated_emi);
  const project = formatINR(d.project_cost);

  // MILK PRICE / REVENUE / INCOME
  if (/(milk|price|revenue|income|sell|sale|rate)/.test(q)) {
    if (tone === "positive") {
      return `You sell ${p.cows} cow${p.cows>1?"s":""} at ₹36.70/L, bringing about ${rev}/month in milk revenue. That's a healthy top line — and with an existing buyer it's a realistic number. Push for stable contracts to protect it.`;
    }
    return `Your milk revenue is roughly ${rev}/month at ₹36.70/L. If the realized price slips even 10%, that top line falls too — milk price is your single biggest exposure. Lock in the best rate you can.`;
  }

  // FEED / FODDER / COST / EXPENSE
  if (/(feed|fodder|cost|expense|chara|dry)/.test(q)) {
    const landNote = p.fodder_land
      ? "Your own fodder land removes the green-fodder bill."
      : "Without fodder land you pay extra for green fodder every month.";
    if (tone === "positive") {
      return `Monthly operating costs come to about ${cost}. ${landNote} Concentrate and dry fodder are the main recurring items — buying feed in bulk for your herd size can trim this.`;
    }
    return `Monthly costs run about ${cost}. ${landNote} Feed is a fixed, unavoidable recurring bill — if feed prices climb, your thin margin absorbs it. Budget a buffer.`;
  }

  // LOAN / EMI / DEBT / BORROW
  if (/(loan|emi|debt|borrow|credit|bank|principal)/.test(q)) {
    if (tone === "positive") {
      return `You'd borrow ${loan} of the ${project} project cost, with an EMI near ${emi}/month. After that, you're left with about ${surplus}/month. For this scale of dairy, that's a workable loan — just keep the repayment on schedule.`;
    }
    return `Be careful: you'd borrow ${loan} of a ${project} setup. That EMI (≈ ${emi}/month) is the pressure point — it leaves just ${surplus}/month after repayments. A missed sale or an illness can quickly flip that into a loss.`;
  }

  // PROFIT / SURPLUS / SAVE / EARN
  if (/(profit|surplus|save|earn|benefit|margin)/.test(q)) {
    if (tone === "positive") {
      return `After every cost and your loan EMI, this plan nets about ${surplus}/month (before EMI it's ${os}). Reinvest part of that surplus into better feed or vet care to lift production further.`;
    }
    return `The surplus is thin — about ${surplus}/month after the EMI. That's roughly ${formatINR(d.after_emi*24)} in a full year. It works on paper, but there's very little room for surprises.`;
  }

  // BUYER / DISTRIBUTOR (offline fallback mirrors backend topic explainer)
  if (/(distributor|buyer|buyers|customer|supplier|market access)/.test(q)) {
    const gain = Math.round(d.estimated_monthly_revenue * 0.10 / 1.1);
    if (p.distributor) {
      if (tone === "positive") {
        return `You already have an assured buyer — worth roughly ${formatINR(gain)}/month vs selling without one (about 10% better realization). Protect that contract; it's what makes ${rev}/month realistic.`;
      }
      return `Your buyer is load-bearing: without it you'd lose roughly ${formatINR(gain)}/month (10% lower realization), dropping surplus from ${surplus}/month. Have a backup buyer before you borrow.`;
    }
    if (tone === "positive") {
      return `No buyer yet — locking one adds roughly ${formatINR(gain)}/month (10% better realization) on ${rev}/month revenue. That single fix often moves risk a full tier.`;
    }
    return `Risk without a buyer: you realize ~10% less on ${rev}/month, leaving ${surplus}/month (${d.risk}). A price dip then hits the full shortfall — secure the buyer first.`;
  }

  // RISK / SAFE / DANGEROUS / WHAT COULD GO WRONG
  if (/(risk|safe|danger|loss|wrong|fail|worry|concern|uncertain|variab)/.test(q)) {
    const emiShare = d.operating_surplus > 0 ? Math.round(d.estimated_emi / d.operating_surplus * 100) : 999;
    if (tone === "positive") {
      return `Right now this plan is ${d.risk} risk with ${surplus}/month after EMI. Best upside lever: ${p.distributor ? "your buyer already protects revenue" : "lock a buyer (+~10% revenue)"} — ask me about profit or milk price next.`;
    }
    return `Top risks for YOUR ${p.cows}-cow plan: 1) EMI ${emi}/month is ${emiShare}% of operating surplus (${os}); 2) cushion only ${surplus}/month (${d.risk}); 3) ${p.distributor ? "lose the buyer and ~10% revenue goes with it" : "no buyer — a price dip hits the full shortfall"}. Keep 2–3 months of ${cost} costs as reserve.`;
  }

  // COWS / SHED / HERD SIZE / REDUCE / INCREASE
  if (/(cow|shed|herd|increase|reduce|more|less|bigger|smaller|expand)/.test(q)) {
    if (tone === "positive") {
      return `You're planning ${p.cows} cow${p.cows>1?"s":""} with a ${formatINR(d.project_cost)} setup${p.existing_shed ? " (shed already there)" : " (a new shed is budgeted)"}. Scaling adds milk volume, and your operating surplus (${os}) gives you room to grow steadily.`;
    }
    if (d.after_emi < 0) {
      return `At ${p.cows} cows you're short about ${formatINR(-d.after_emi)}/month after the loan. The debt is the problem — reducing the herd lowers the project cost and the EMI together. Re-run the optimizer to find a sustainable size.`;
    }
    return `Your ${p.cows}-cow plan clears the EMI by about ${surplus}/month, so it's sustainable as-is. If you want to be safer, a slightly smaller herd cuts both the shed build cost and the loan.`;
  }

  // GREETING
  if (/(hi|hello|hey|namaste)\b/.test(q)) {
    return tone === "positive"
      ? `Namaste! I'm your Opportunity Advisor. I look for the upside in your ${p.cows}-cow dairy plan — ask me about profit, milk price, or growing the herd.`
      : `Namaste! I'm your Risk Challenger. I poke holes in the ${p.cows}-cow plan so nothing surprises you. Ask me about the loan, milk price, or what could go wrong.`;
  }

  // THANKS
  if (/(thank|thanks|thx|great|nice)/.test(q)) {
    return tone === "positive" ? "You're welcome — here to help you grow." : "Anytime — better to test assumptions now than face them later.";
  }

  // FALLBACK
  if (tone === "positive") {
    return `Good question! Here's what stands out: your ${p.cows}-cow plan earns about ${os}/month before the loan, and about ${surplus}/month after it. I focus on opportunities — ask me about increasing income, cutting feed costs, or scaling up.`;
  }
  return `Good question. The key number to watch here is your surplus after the EMI — about ${surplus}/month (rated ${d.risk} risk). Ask me about the loan pressure, milk price, feed costs, or whether to reduce the herd.`;
}

function openAgent(agent) {
  activeAgent = agent;
  const meta = AGENTS[agent];
  document.getElementById("chatAgentIcon").textContent = meta.icon;
  document.getElementById("chatAgentRole").textContent = meta.role;
  document.getElementById("chatAgentName").textContent = meta.name;

  chatBody.innerHTML = "";
  chatOverlay.hidden = false;

  document.getElementById("chatAgentRole").textContent = t(agent === "advocate" ? "ag_adv_role" : "ag_ch_role");
  document.getElementById("chatAgentName").textContent = t(agent === "advocate" ? "ag_adv_name" : "ag_ch_name");

  ensureAnalysis().then(() => {
    const p = lastPlan || {};
    const greeting = t(agent === "advocate" ? "greet_a" : "greet_c", { cows: p.cows || "" });
    addChatMessage(greeting, "bot");
  });

  const chips = agent === "advocate"
    ? [t("chip_a1"), t("chip_a2"), t("chip_a3")]
    : [t("chip_c1"), t("chip_c2"), t("chip_c3")];
  chatChips.innerHTML = chips.map(c => `<button class="chat-chip" data-q="${c}">${c}</button>`).join("");
  chatInput.value = "";
  chatInput.focus();
}

async function sendChat() {
  const text = chatInput.value.trim();
  if (!text) return;
  addChatMessage(text, "user");
  chatInput.value = "";
  addTyping();
  const plan = readPlan();
  let answer = null;
  try {
    const res = await fetch(`${API_BASE}/api/agent/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: text, plan, agent: activeAgent, lang: (window.I18N ? window.I18N.getLang() : "en") })
    });
    if (res.ok) {
      const data = await res.json();
      answer = data.answer;
    }
  } catch (e) { /* fall back to local rules */ }
  setTimeout(() => {
    removeTyping();
    addChatMessage(answer || agentReply(text, activeAgent), "bot");
    chatBody.scrollTop = chatBody.scrollHeight;
  }, 350 + Math.random() * 250);
}

// Agent buttons open the drawer
document.querySelectorAll(".text-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const isRisk = btn.closest(".challenger");
    openAgent(isRisk ? "challenger" : "advocate");
  });
});

// Chat interactions
document.getElementById("chatSend").addEventListener("click", sendChat);
chatInput.addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });
document.getElementById("chatClose").addEventListener("click", () => { chatOverlay.hidden = true; });
chatOverlay.addEventListener("click", (e) => { if (e.target === chatOverlay) chatOverlay.hidden = true; });
chatChips.addEventListener("click", (e) => {
  const chip = e.target.closest(".chat-chip");
  if (chip) { chatInput.value = chip.dataset.q; sendChat(); }
});

// Solid topbar after scrolling so page content never smears through the glass.
(function wireTopbarScroll() {
  const bar = document.querySelector(".topbar");
  if (!bar) return;
  const onScroll = () => bar.classList.toggle("scrolled", window.scrollY > 24);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
})();

// Profile dropdown menu
const profileWrap = document.querySelector(".profile-wrap");
const profileBtn = document.getElementById("profileBtn");

// Show the logged-in user's profile info
(function populateProfile() {
  const stored = localStorage.getItem("saathi_user");
  if (stored) {
    try {
      const user = JSON.parse(stored);
      const name = user.name || user.username || "User";
      const initial = (user.name || user.username || "U").trim().charAt(0).toUpperCase();
      document.getElementById("profileName").textContent = name;
      document.getElementById("menuName").textContent = name;
      document.getElementById("profileAvatar").textContent = initial;
      document.getElementById("menuAvatar").textContent = initial;
      if (user.email) document.getElementById("menuEmail").textContent = user.email;
    } catch (err) { /* ignore malformed stored profile */ }
  }
})();

if (profileBtn && profileWrap) {
  profileBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    profileWrap.classList.toggle("open");
  });

  // Close when clicking outside
  document.addEventListener("click", (e) => {
    if (!profileWrap.contains(e.target)) profileWrap.classList.remove("open");
  });

  // Menu actions
  profileWrap.addEventListener("click", (e) => {
    const item = e.target.closest(".profile-menu-item");
    if (!item) return;
    const action = item.dataset.action;
    profileWrap.classList.remove("open");

    if (action === "logout") {
      localStorage.removeItem("saathi_token");
      localStorage.removeItem("saathi_remember_user");
      localStorage.removeItem("saathi_user");
      notify(t("t_logged_out"));
      setTimeout(() => window.location.replace("login.html?logout=1"), 500);
    } else if (action === "go-home") {
      showPage("home");
    } else if (action === "view-plan") {
      showPage("plan");
    } else if (action === "decision") {
      showPage("decision");
    }
  });
}

// ================== Real Location & Interactive Map ==================
const useLocationBtn = document.getElementById("useLocationBtn");
const locationSearch = document.getElementById("locationSearch");
const locSuggestions = document.getElementById("locSuggestions");
const locBtnText = document.getElementById("locBtnText");
const locStatusPill = document.getElementById("locStatusPill");
const locTitle = document.getElementById("locTitle");
const locSub = document.getElementById("locSub");
const nearbyPanel = document.getElementById("nearbyPanel");
const nearbyList = document.getElementById("nearbyList");
const nearbyCount = document.getElementById("nearbyCount");
const mapEl = document.getElementById("locationMap");

let userMap = null;
let userMarker = null;
let poiLayer = null;
let currentLocation = null;
let relocating = false;
let nearbyRequestId = 0;
let nearbyController = null;
let lastMapClickMs = 0;

const POI_ICONS = {
  "Milk Collection Centre": "🥛",
  "Dairy Farm": "🐄",
  "Dairy Market": "🛒",
  "Feed / Fodder Supplier": "🌾",
  "Veterinary Service": "💊",
  "Other Dairy Business": "🏪",
};

function escPop(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

async function saveUserLocation(lat, lng) {
  const token = getToken();
  if (!token) return null;
  try {
    const res = await fetch(`${API_BASE}/api/location`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
      body: JSON.stringify({ latitude: lat, longitude: lng })
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.location || null;
  } catch (err) {
    console.warn("Could not save location:", err);
    return null;
  }
}

async function searchGeocode(q) {
  try {
    const res = await fetch(`${API_BASE}/api/geocode?q=${encodeURIComponent(q)}&limit=6`);
    if (!res.ok) return { results: [] };
    const data = await res.json();
    return data && Array.isArray(data.results) ? data : { results: [] };
  } catch (err) {
    console.warn("Geocode search failed:", err);
    return { results: [] };
  }
}

function renderLocSuggestions(results) {
  if (!locSuggestions) return;
  if (!results || !results.length) {
    locSuggestions.innerHTML = `<li class="loc-search-empty">No places found.</li>`;
    locSuggestions.hidden = false;
    return;
  }
  locSuggestions.innerHTML = results.map((r) => {
    const sub = [r.locality, r.state, r.country].filter(Boolean).join(", ");
    return `<li data-lat="${r.lat}" data-lon="${r.lon}">${escPop(r.short || r.label)}<small>${escPop(sub)}</small></li>`;
  }).join("");
  locSuggestions.hidden = false;
}

function closeLocSuggestions() {
  if (locSuggestions) locSuggestions.hidden = true;
}

function initLocationSearch() {
  if (!locationSearch || !locSuggestions) return;
  let timer = null;
  locationSearch.addEventListener("input", () => {
    clearTimeout(timer);
    const q = locationSearch.value.trim();
    if (!q) { closeLocSuggestions(); return; }
    timer = setTimeout(async () => {
      const { results } = await searchGeocode(q);
      renderLocSuggestions(results);
    }, 350);
  });

  locSuggestions.addEventListener("click", (e) => {
    const li = e.target.closest("li[data-lat]");
    if (!li) return;
    applyDetectedLocation(parseFloat(li.dataset.lat), parseFloat(li.dataset.lon));
    locationSearch.value = "";
    closeLocSuggestions();
  });

  locationSearch.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === "Escape") closeLocSuggestions();
  });

  document.addEventListener("click", (e) => {
    if (e.target !== locationSearch && !locSuggestions.contains(e.target)) closeLocSuggestions();
  });
}

async function fetchSavedLocation() {
  const token = getToken();
  if (!token) return null;
  try {
    const res = await fetch(`${API_BASE}/api/location`, { headers: { "Authorization": "Bearer " + token } });
    if (!res.ok) return null;
    const data = await res.json();
    return data.location || null;
  } catch (err) {
    console.warn("Could not load saved location:", err);
    return null;
  }
}

async function fetchNearby(lat, lng) {
  const token = getToken();
  if (!token) return { places: [], fromCache: true };
  // Cancel any in-flight nearby search so fast map moves don't pile up
  // Overpass requests and show stale "many seconds later" results.
  if (nearbyController) {
    try { nearbyController.abort(); } catch (e) { /* ignore */ }
  }
  nearbyController = new AbortController();
  const signal = nearbyController.signal;
  const timer = setTimeout(() => { try { nearbyController.abort(); } catch (e) {} }, 25000);
  try {
    const res = await fetch(`${API_BASE}/api/nearby?lat=${encodeURIComponent(lat)}&lng=${encodeURIComponent(lng)}&radius=10000&business=dairy`, { signal });
    if (!res.ok) return { places: [], error: true, status: res.status };
    return await res.json();
  } catch (err) {
    if (err && err.name === "AbortError") return { places: [], aborted: true };
    console.warn("Could not load nearby places:", err);
    return { places: [], error: true };
  } finally {
    clearTimeout(timer);
  }
}

function showLocation(loc) {
  currentLocation = loc;
  const parts = [loc.locality, loc.state].filter(Boolean);
  locTitle.textContent = parts.length ? parts.join(", ") : t("loc_you");
  const detail = [loc.locality, loc.district, loc.state].filter(Boolean).join(", ");
  locSub.textContent = detail ? detail : t("loc_found");
  locStatusPill.textContent = t("pill_located");
  locStatusPill.className = "status-pill risk-low";

  // Persist so the location is available to the plan/analysis flow and reloads.
  try {
    localStorage.setItem("saathi_location", JSON.stringify(loc));
  } catch (e) { /* storage may be unavailable */ }

  // Reflect the detected location on the Plan page sidebar too (if present).
  const planName = document.getElementById("planLocationName");
  const planDetail = document.getElementById("planLocationDetail");
  if (planName) {
    planName.textContent = loc.locality || loc.state || t("loc_you");
  }
  if (planDetail) {
    const d = [loc.locality, loc.district, loc.state, loc.country].filter(Boolean).join(", ");
    planDetail.textContent = d || t("loc_found");
  }
}

const DEFAULT_MAP = { lat: 22.5726, lng: 88.3639, zoom: 11 }; // Kolkata metro fallback

function placeUserMarker(lat, lng) {
  if (userMarker) userMarker.setLatLng([lat, lng]);
  else userMarker = L.circleMarker([lat, lng], { radius: 8, color: "#e57b35", weight: 3, fillColor: "#ffad70", fillOpacity: 0.7 }).addTo(userMap).bindPopup("<strong>You are here</strong>");
}

function wireMapClick() {
  if (!userMap || userMap._clickWired) return;
  userMap._clickWired = true;
  // Clicking the map background (not a POI marker) moves/re-sets your location.
  // Debounced so rapid clicks don't fire overlapping Overpass searches.
  userMap.on("click", (e) => {
    const nowMs = Date.now();
    if (nowMs - lastMapClickMs < 900) return;
    lastMapClickMs = nowMs;
    if (relocating) return;
    const { lat: mlat, lng: mlng } = e.latlng;
    relocating = true;
    try {
      placeUserMarker(mlat, mlng);
      userMap.panTo([mlat, mlng], { animate: true });
      applyDetectedLocation(mlat, mlng);
    } finally {
      setTimeout(() => { relocating = false; }, 900);
    }
  });
}

function initMap(lat, lng, zoom, mark) {
  if (!mapEl) return;
  if (typeof L === "undefined") {
    mapEl.innerHTML = `<div style="display:grid;place-items:center;height:100%;color:var(--muted);font-size:12px;padding:20px;text-align:center;">${t("map_cdn_fail")}</div>`;
    return;
  }
  const z = zoom || 13;
  const showMark = mark !== false;
  if (userMap) {
    userMap.setView([lat, lng], z);
    if (showMark) placeUserMarker(lat, lng);
    wireMapClick();
    return;
  }
  userMap = L.map(mapEl).setView([lat, lng], z);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors"
  }).addTo(userMap);
  if (showMark) placeUserMarker(lat, lng);
  wireMapClick();
}

function placeLabel(p) {
  return p.name || p.type || "Nearby place";
}

function showNearbyLoading() {
  if (!nearbyPanel) return;
  nearbyPanel.hidden = false;
  nearbyCount.textContent = t("searching");
  nearbyList.innerHTML = `<li class="nearby-empty">🔍 ${t("searching")}</li>`;
}

function clearPoiLayer() {
  if (poiLayer && userMap) {
    try { userMap.removeLayer(poiLayer); } catch (e) { /* ignore */ }
  }
  poiLayer = null;
}

function dairyDivIcon(emoji) {
  return L.divIcon({
    className: "dairy-pin-wrap",
    html: `<div class="dairy-pin">${emoji}</div>`,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    popupAnchor: [0, -14]
  });
}

let poiMarkers = [];

function renderNearby(data, opts) {
  if (!nearbyPanel) return;
  opts = opts || {};
  nearbyPanel.hidden = false;

  // Drop stale responses from an older map move.
  if (opts.requestId && opts.requestId !== nearbyRequestId) return;
  if (data && data.aborted) return;

  const degraded = data && data.partial_failures && data.partial_failures.length;
  const requestError = data && (data.error || data.status >= 500);

  // Always clear old pins first so the map never shows the previous city's pins.
  clearPoiLayer();
  poiMarkers = [];

  if (userMap) {
    try { userMap.invalidateSize(); } catch (e) {}
  }

  if (!data || !data.places || !data.places.length) {
    nearbyCount.textContent = "0 nearby";
    const radiusShown = data && data.radius_m ? Math.round(data.radius_m / 1000) : 10;
    let msg;
    if (requestError) {
      msg = `<li class="nearby-empty">${t("unavail")} <br><button class="secondary-btn small" id="nearbyRetry" style="margin-top:8px">${t("retry")}</button></li>`;
    } else {
      msg = `<li class="nearby-empty">${t("none_found", { r: radiusShown })}<br><button class="secondary-btn small" id="nearbyRetry" style="margin-top:8px">${t("retry_wider")}</button></li>`;
    }
    nearbyList.innerHTML = msg;
    try {
      document.getElementById("nearbyDensity").hidden = true;
      document.getElementById("nearbyGaps").hidden = true;
      document.getElementById("nearbyChannels").hidden = true;
    } catch (e) {}
    const rb = document.getElementById("nearbyRetry");
    if (rb && currentLocation) {
      rb.addEventListener("click", () => {
        if (typeof currentLocation.latitude === "number") {
          applyDetectedLocation(currentLocation.latitude, currentLocation.longitude, { force: true });
        } else if (typeof currentLocation.lat === "number") {
          applyDetectedLocation(currentLocation.lat, currentLocation.lng, { force: true });
        }
      });
    }
    return;
  }

  const radiusShown = data && data.radius_m ? Math.round(data.radius_m / 1000) : 10;
  const cachedNote = data.cached ? t("nb_cached") : "";
  const widenNote = data.widened ? t("nb_widen") : "";
  nearbyCount.textContent = t("nb_count", { n: data.places.length.toLocaleString(numLocale()), r: radiusShown.toLocaleString(numLocale()) }) + cachedNote + widenNote;
  window._lastNearby = { data, requestId: opts.requestId };
  let note = "";
  if (degraded) {
    note += `<li class="nearby-empty" style="padding:8px 10px">${t("partial_note")}</li>`;
  }
  if (data.widened) {
    note += `<li class="nearby-empty" style="padding:8px 10px">${t("widen_note", { r: radiusShown })}</li>`;
  }
  try {
    const densEl = document.getElementById("nearbyDensity");
    if (densEl) {
      const cats = data.by_category || {};
      const keys = Object.keys(cats);
      if (keys.length) {
        const parts = keys.slice(0, 6).map(k => `${POI_ICONS[k] || "📍"} ${k} ×${Number(cats[k]).toLocaleString(numLocale())}`);
        densEl.innerHTML = `<b>${t("nb_density")}</b> ` + parts.join(" · ");
        densEl.hidden = false;
      } else { densEl.hidden = true; }
    }
  } catch (e) {}
  try {
    const gapsEl = document.getElementById("nearbyGaps");
    if (gapsEl) {
      const gaps = data.gaps || [];
      if (gaps.length) {
        gapsEl.innerHTML = `<b>${t("nb_gaps_h")}</b> ` + gaps.slice(0, 4).map(g =>
          `<div>◌ ${t("nb_gap", { type: g })}</div>`).join("");
        gapsEl.hidden = false;
      } else { gapsEl.hidden = true; }
    }
  } catch (e) {}
  try {
    const chEl = document.getElementById("nearbyChannels");
    if (chEl) {
      const chs = data.channels || [];
      if (chs.length) {
        chEl.innerHTML = `<b>${t("ch_h")}</b>` + chs.map(c =>
          `<div class="channel-row"><span>${t("ch_" + c.channel)}</span>` +
          c.places.slice(0, 3).map(p => `<div>📍 ${escPop(p.name || "")} — ${fmt1(p.distance_km)} km</div>`).join("") +
          `</div>`).join("");
        chEl.hidden = false;
      } else { chEl.hidden = true; }
    }
  } catch (e) {}
  const shown = data.places.slice(0, 12);
  nearbyList.innerHTML = note + shown.map((p, idx) => {
    const emoji = POI_ICONS[p.type] || "📍";
    const srcBadge = p.source === "curated" ? " · " + t("verified_tag") : "";
    return `<li data-poi="${idx}" style="cursor:pointer"><span class="nearby-emoji">${emoji}</span><div class="nearby-info"><strong>${escPop(placeLabel(p))}</strong><small>${escPop(p.type)}${escPop(srcBadge)}</small></div><span class="nearby-dist">${fmt1(p.distance_km)} km</span></li>`;
  }).join("");

  // --- Dairy pins directly ON the map ---
  if (!userMap) return;
  poiLayer = L.layerGroup().addTo(userMap);

  data.places.slice(0, 25).forEach((p) => {
    const emoji = POI_ICONS[p.type] || "📍";
    const marker = L.marker([p.lat, p.lon], { icon: dairyDivIcon(emoji), title: placeLabel(p) }).addTo(poiLayer);
    const type = p.type === "Milk Collection Centre" ? "Milk Buyer" : p.type;
    const addr = p.address ? `<div class="poi-addr">${escPop(p.address)}</div>` : "";
    const src = p.source === "curated" ? `<div class="poi-dist">✓ Verified listing</div>` : (p.source === "photon" ? `<div class="poi-dist">OSM live via Photon</div>` : "");
    marker.bindPopup(
      `<div class="poi-popup"><strong>${escPop(placeLabel(p))}</strong><span class="poi-type">${escPop(type)}</span>${addr}<div class="poi-dist">Distance: ${fmt1(p.distance_km)} km</div>${src}</div>`
    );
    poiMarkers.push(marker);
  });

  // Clicking a list row pans to + opens the map pin.
  nearbyList.querySelectorAll("li[data-poi]").forEach((li) => {
    li.addEventListener("click", () => {
      const m = poiMarkers[Number(li.dataset.poi)];
      if (m && userMap) {
        userMap.panTo(m.getLatLng(), { animate: true });
        setTimeout(() => m.openPopup(), 250);
      }
    });
  });

  // Zoom the map so you + dairies are all visible.
  try {
    if (poiMarkers.length && currentLocation) {
      const pts = poiMarkers.slice(0, 12).map(m => m.getLatLng());
      const cLat = currentLocation.latitude !== undefined ? currentLocation.latitude : currentLocation.lat;
      const cLng = currentLocation.longitude !== undefined ? currentLocation.longitude : currentLocation.lng;
      if (typeof cLat === "number") pts.push(L.latLng(cLat, cLng));
      const bounds = L.latLngBounds(pts);
      userMap.fitBounds(bounds.pad(0.25), { maxZoom: 13, animate: true });
    }
  } catch (e) { /* ignore fit errors */ }
}

function applyDetectedLocation(lat, lng, opts) {
  opts = opts || {};
  const myId = ++nearbyRequestId;
  // If user spams map clicks, ignore all but the latest (unless forced retry).
  if (opts.force) nearbyRequestId = myId;

  locStatusPill.textContent = t("loc_setting");
  locStatusPill.className = "status-pill";

  // Update map + loading UI instantly — don't wait for reverse-geocode.
  initMap(lat, lng);
  if (userMarker) userMarker.setLatLng([lat, lng]);
  clearPoiLayer();
  showNearbyLoading();
  currentLocation = { latitude: lat, longitude: lng };
  try { userMap && userMap.invalidateSize(); } catch (e) {}

  // Reverse-geocode (name) and nearby-search (pins) in parallel.
  const locPromise = saveUserLocation(lat, lng);
  const nearbyPromise = fetchNearby(lat, lng);

  locPromise.then((loc) => {
    if (myId !== nearbyRequestId) return; // stale move, ignore
    if (loc) {
      showLocation(loc);
      currentLocation = loc;
    } else {
      locTitle.textContent = `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
      locSub.textContent = t("loc_offline");
      locStatusPill.textContent = t("pill_located");
      locStatusPill.className = "status-pill risk-low";
    }
  });

  nearbyPromise.then(async (nearby) => {
    if (myId !== nearbyRequestId) return; // an even newer click already started
    // One silent auto-retry on upstream outage (fixes your Pic 2 case).
    if (nearby && nearby.error && !opts.retried) {
      await new Promise(r => setTimeout(r, 1500));
      if (myId !== nearbyRequestId) return;
      const retry = await fetchNearby(lat, lng);
      if (myId !== nearbyRequestId) return;
      renderNearby(retry, { requestId: myId });
      return;
    }
    renderNearby(nearby, { requestId: myId });
  });
}

function detectLocation() {
  if (!navigator.geolocation) {
    notify(t("t_geo_unsupported"));
    locStatusPill.textContent = "UNAVAILABLE";
    return;
  }

  locBtnText.textContent = t("detecting");
  locBtnText.classList.add("detecting");
  useLocationBtn.disabled = true;
  locStatusPill.textContent = t("loc_detecting_s");
  locStatusPill.className = "status-pill";

  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const { latitude, longitude } = pos.coords;
      locBtnText.textContent = t("btn_use_loc");
      locBtnText.classList.remove("detecting");
      useLocationBtn.disabled = false;
      applyDetectedLocation(latitude, longitude);
    },
    (err) => {
      locBtnText.textContent = t("btn_use_loc");
      locBtnText.classList.remove("detecting");
      useLocationBtn.disabled = false;
      locStatusPill.className = "status-pill";
      let msg = t("t_geo_fail");
      if (err.code === 1) msg = t("t_geo_denied");
      else if (err.code === 2) msg = t("t_geo_unavail");
      else if (err.code === 3) msg = t("t_geo_timeout");
      notify(msg);
      locStatusPill.textContent = t("loc_retry");
      locSub.textContent = t("loc_sub_d");
      locTitle.textContent = t("loc_title_d");
    },
    { enableHighAccuracy: true, timeout: 12000, maximumAge: 30000 }
  );
}

// Wire up the "Use My Location" button.
if (useLocationBtn) {
  useLocationBtn.addEventListener("click", detectLocation);
}

// Wire up the location search box (city/place autocomplete).
initLocationSearch();

// On load, pre-fill a previously saved location (rest of the app still works if absent).
(async function loadSavedLocation() {
  let saved = await fetchSavedLocation();
  // Fall back to the locally cached copy if the backend isn't reachable.
  if (!saved) {
    try {
      const cached = localStorage.getItem("saathi_location");
      if (cached) saved = JSON.parse(cached);
    } catch (e) { /* ignore */ }
  }
  if (saved && typeof saved.latitude === "number") {
    const { latitude, longitude } = saved;
    const myId = ++nearbyRequestId;
    currentLocation = saved;
    initMap(latitude, longitude);
    showLocation(saved);
    showNearbyLoading();
    const nearby = await fetchNearby(latitude, longitude);
    renderNearby(nearby, { requestId: myId });
  } else {
    // No location yet (fresh browser): still show a live map so the box is
    // never an empty dark panel. Clicking it sets the location.
    initMap(DEFAULT_MAP.lat, DEFAULT_MAP.lng, DEFAULT_MAP.zoom, false);
  }
})();

