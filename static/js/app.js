document.addEventListener("DOMContentLoaded", () => {
  const evaluateBtn = document.getElementById("evaluate-btn");
  const jdInput = document.getElementById("jd-input");
  const resultsDiv = document.getElementById("results");

  if (evaluateBtn) {
    evaluateBtn.addEventListener("click", async () => {
      const text = jdInput ? jdInput.value.trim() : "";
      if (!text) {
        showAlert("Please paste a job description or URL.", "warning");
        return;
      }
      setLoading(true);
      resultsDiv.innerHTML = loadingSpinner();

      try {
        const response = await fetch("/evaluate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ jd_text: text }),
        });
        const payload = await response.json();

        if (response.ok && payload.success) {
          renderResult(payload);
          // Auto-refresh the tracker table after a short delay
          setTimeout(() => window.location.reload(), 600);
        } else if (response.ok && payload.duplicate) {
          showAlert(
            `⚠️ Duplicate detected (${payload.duplicate_type?.replace(/_/g, " ")}): ${payload.error}`,
            "warning"
          );
        } else {
          showAlert(payload.error || "Evaluation failed. Please try again.", "error");
        }
      } catch (err) {
        showAlert("Network error. Please check your connection.", "error");
        console.error(err);
      } finally {
        setLoading(false);
      }
    });
  }

  function setLoading(loading) {
    if (!evaluateBtn) return;
    evaluateBtn.disabled = loading;
    evaluateBtn.textContent = loading ? "Evaluating…" : "Evaluate Job";
    evaluateBtn.classList.toggle("opacity-60", loading);
    evaluateBtn.classList.toggle("cursor-not-allowed", loading);
  }

  function loadingSpinner() {
    return `<div class="flex items-center gap-3 text-gray-400 py-8 px-6">
      <svg class="animate-spin h-5 w-5 text-indigo-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
      </svg>
      <span class="text-sm">Claude is evaluating this job against Aishik's profile…</span>
    </div>`;
  }

  function showAlert(message, type) {
    if (!resultsDiv) return;
    const colors = {
      warning: "bg-yellow-900/50 border-yellow-700 text-yellow-200",
      error: "bg-red-900/50 border-red-700 text-red-200",
      success: "bg-green-900/50 border-green-700 text-green-200",
    };
    const wrapper = document.createElement("div");
    wrapper.className = `border rounded-lg p-4 ${colors[type] || colors.error}`;
    const p = document.createElement("p");
    p.className = "text-sm";
    p.textContent = message;
    wrapper.appendChild(p);
    resultsDiv.innerHTML = "";
    resultsDiv.appendChild(wrapper);
  }

  function gradeColor(grade) {
    return { A: "text-green-400", B: "text-blue-400", C: "text-yellow-400", D: "text-orange-400", F: "text-red-400" }[grade] || "text-gray-400";
  }

  function scoreBar(score) {
    const pct = Math.round((score / 5) * 100);
    const color = score >= 4 ? "bg-green-500" : score >= 3 ? "bg-yellow-500" : "bg-red-500";
    return `<div class="flex items-center gap-2">
      <div class="flex-1 bg-gray-700 rounded-full h-1.5">
        <div class="${color} h-1.5 rounded-full" style="width:${pct}%"></div>
      </div>
      <span class="text-xs text-gray-400 w-6 text-right">${score}</span>
    </div>`;
  }

  function visaBadge(status) {
    const map = {
      yes: "bg-green-900 text-green-300",
      no: "bg-red-900 text-red-300",
      unclear: "bg-yellow-900 text-yellow-300",
    };
    const label = { yes: "Visa ✓", no: "No Visa", unclear: "Visa ?" }[status] || "Visa ?";
    return `<span class="px-2 py-0.5 rounded text-xs font-medium ${map[status] || map.unclear}">${label}</span>`;
  }

  function recBadge(rec) {
    const map = {
      Apply: "bg-green-800 text-green-200",
      Consider: "bg-yellow-800 text-yellow-200",
      Skip: "bg-red-800 text-red-200",
    };
    return `<span class="px-3 py-1 rounded-full text-sm font-semibold uppercase tracking-wider ${map[rec] || "bg-gray-700 text-gray-200"}">${rec}</span>`;
  }

  function strengthDot(strength) {
    return { strong: "bg-green-400", moderate: "bg-yellow-400", gap: "bg-red-400" }[strength] || "bg-gray-400";
  }

  function severityColor(severity) {
    return { high: "text-red-400", medium: "text-yellow-400", low: "text-gray-400" }[severity] || "text-gray-400";
  }

  function renderResult(payload) {
    if (!resultsDiv) return;
    const d = payload.data || {};
    const dims = d.dimensions || {};
    const matches = d.matches || [];
    const gaps = d.gaps || [];
    const questions = d.interview_questions || [];
    const proofPoints = d.key_proof_points || [];
    const visaKeywords = d.visa_keywords_found || [];

    const dimOrder = [
      ["role_match", "Role Match"],
      ["channel_fit", "Channel Fit"],
      ["budget_scale", "Budget Scale"],
      ["team_scope", "Team Scope"],
      ["compensation", "Compensation"],
      ["geographic", "Geographic"],
      ["company_stage", "Company Stage"],
      ["brand_quality", "Brand Quality"],
      ["growth_path", "Growth Path"],
      ["interview_likelihood", "Interview Likelihood"],
    ];

    resultsDiv.innerHTML = `
    <div class="space-y-5">

      <!-- Header Card -->
      <div class="bg-gray-800 border border-gray-700 rounded-xl p-6">
        <div class="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
          <div class="flex-1 min-w-0">
            <h3 class="text-lg font-semibold text-white truncate">${esc(d.company || "Unknown Company")}</h3>
            <p class="text-gray-400 text-sm mt-0.5">${esc(d.role || "")}</p>
            <p class="text-gray-600 text-xs mt-0.5">${esc(d.location || "")}${d.region ? ` &middot; ${d.region}` : ""}</p>
            ${d.salary_listed ? `<p class="text-indigo-400 text-xs mt-1">💰 ${esc(d.salary_listed)}</p>` : ""}
          </div>
          <div class="flex flex-col items-end gap-2 shrink-0">
            <div class="flex items-baseline gap-1">
              <span class="text-5xl font-bold ${gradeColor(d.grade)}">${d.grade || "—"}</span>
              <span class="text-gray-500 text-sm">&nbsp;${d.score != null ? d.score.toFixed(2) : "—"}/5</span>
            </div>
            ${recBadge(d.recommendation || "")}
            ${visaBadge(d.visa_sponsorship || "unclear")}
          </div>
        </div>
        ${d.archetype ? `<div class="mt-3 pt-3 border-t border-gray-700"><span class="text-xs text-gray-500 uppercase tracking-wider">Archetype: </span><span class="text-xs text-indigo-400 font-medium">${esc(d.archetype.replace(/_/g, " "))}</span></div>` : ""}
        ${d.skip_reason ? `<div class="mt-3 pt-3 border-t border-gray-700 text-sm text-red-400"><span class="font-medium">Skip reason: </span>${esc(d.skip_reason)}</div>` : ""}
        ${visaKeywords.length ? `<div class="mt-2 text-xs text-gray-500">Visa keywords: ${visaKeywords.map(k => `<span class="text-gray-300">${esc(k)}</span>`).join(", ")}</div>` : ""}
      </div>

      <!-- Dimensions -->
      <div class="bg-gray-800 border border-gray-700 rounded-xl p-5">
        <h4 class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-4">Score Breakdown</h4>
        <div class="space-y-3">
          ${dimOrder.map(([key, label]) => {
            const dim = dims[key] || {};
            const s = dim.score != null ? dim.score : "—";
            const w = dim.weight != null ? `×${dim.weight}` : "";
            const note = dim.note || "";
            return `<div>
              <div class="flex items-center justify-between mb-1">
                <span class="text-xs text-gray-300">${label} <span class="text-gray-600">${w}</span></span>
                <span class="text-xs font-medium text-gray-300">${s}/5</span>
              </div>
              ${typeof s === "number" ? scoreBar(s) : ""}
              ${note ? `<p class="text-xs text-gray-500 mt-0.5">${esc(note)}</p>` : ""}
            </div>`;
          }).join("")}
        </div>
      </div>

      <!-- Matches & Gaps -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-5">
        <!-- Matches -->
        ${matches.length ? `
        <div class="bg-gray-800 border border-gray-700 rounded-xl p-5">
          <h4 class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Matches</h4>
          <div class="space-y-2">
            ${matches.map(m => `
            <div class="flex items-start gap-2">
              <span class="mt-1.5 w-2 h-2 rounded-full shrink-0 ${strengthDot(m.strength)}"></span>
              <div>
                <p class="text-xs font-medium text-gray-200">${esc(m.requirement || "")}</p>
                <p class="text-xs text-gray-500">${esc(m.proof_point || "")}</p>
              </div>
            </div>`).join("")}
          </div>
        </div>` : ""}

        <!-- Gaps -->
        ${gaps.length ? `
        <div class="bg-gray-800 border border-gray-700 rounded-xl p-5">
          <h4 class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Gaps</h4>
          <div class="space-y-2">
            ${gaps.map(g => `
            <div>
              <p class="text-xs font-medium ${severityColor(g.severity)}">${esc(g.gap || "")}</p>
              ${g.mitigation ? `<p class="text-xs text-gray-500 mt-0.5">↳ ${esc(g.mitigation)}</p>` : ""}
            </div>`).join("")}
          </div>
        </div>` : ""}
      </div>

      <!-- Cover Letter Hook -->
      ${d.cover_letter_hook ? `
      <div class="bg-indigo-950 border border-indigo-800 rounded-xl p-5">
        <h4 class="text-xs font-semibold text-indigo-400 uppercase tracking-wider mb-2">Cover Letter Hook</h4>
        <p class="text-sm text-gray-200 italic leading-relaxed">"${esc(d.cover_letter_hook)}"</p>
      </div>` : ""}

      <!-- Key Proof Points + Interview Questions -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-5">
        ${proofPoints.length ? `
        <div class="bg-gray-800 border border-gray-700 rounded-xl p-5">
          <h4 class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Key Proof Points</h4>
          <ul class="space-y-1.5">
            ${proofPoints.map(p => `<li class="text-xs text-gray-300 flex items-start gap-2"><span class="text-indigo-400 mt-0.5">›</span>${esc(p)}</li>`).join("")}
          </ul>
        </div>` : ""}

        ${questions.length ? `
        <div class="bg-gray-800 border border-gray-700 rounded-xl p-5">
          <h4 class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Likely Interview Questions</h4>
          <ul class="space-y-1.5">
            ${questions.map(q => `<li class="text-xs text-gray-300 flex items-start gap-2"><span class="text-yellow-400 mt-0.5">?</span>${esc(q)}</li>`).join("")}
          </ul>
        </div>` : ""}
      </div>

      <!-- Actions -->
      <div class="flex items-center gap-3 pb-2">
        ${payload.saved
          ? `<span class="flex items-center gap-2 px-4 py-2 bg-green-900/60 border border-green-700 text-green-300 text-sm font-medium rounded-lg">
               <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
               Saved to tracker
             </span>`
          : payload.duplicate
          ? `<span class="flex items-center gap-2 px-4 py-2 bg-yellow-900/60 border border-yellow-700 text-yellow-300 text-sm font-medium rounded-lg">
               ⚠️ ${esc(payload.duplicate_message || "Duplicate — not saved")}
             </span>`
          : `<button
               onclick='saveJob(${JSON.stringify(JSON.stringify(payload))})'
               class="px-5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors">
               Save to Tracker
             </button>`
        }
        <button
          onclick="document.getElementById('jd-input').value=''; document.getElementById('results').innerHTML='';"
          class="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm rounded-lg transition-colors">
          Clear
        </button>
      </div>
    </div>`;
  }
});

function esc(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

async function saveJob(payloadJson) {
  let payload;
  try {
    payload = JSON.parse(payloadJson);
  } catch (e) {
    alert("Failed to parse evaluation data.");
    return;
  }

  try {
    const response = await fetch("/api/save-job", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (result.success) {
      alert("Job saved to tracker!");
      window.location.reload();
    } else {
      alert("Could not save: " + (result.error || "Unknown error"));
    }
  } catch (err) {
    alert("Network error saving job.");
    console.error(err);
  }
}
