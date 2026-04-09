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

      try {
        const response = await fetch("/evaluate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ jd_text: text }),
        });

        const data = await response.json();

        if (response.ok && data.success) {
          renderResult(data);
        } else {
          showAlert(data.error || "Evaluation failed. Please try again.", "error");
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
    evaluateBtn.textContent = loading ? "Evaluating..." : "Evaluate Job";
    evaluateBtn.classList.toggle("opacity-60", loading);
    evaluateBtn.classList.toggle("cursor-not-allowed", loading);
  }

  function showAlert(message, type) {
    if (!resultsDiv) return;
    const colors = {
      warning: "bg-yellow-900 border-yellow-600 text-yellow-200",
      error: "bg-red-900 border-red-600 text-red-200",
      success: "bg-green-900 border-green-600 text-green-200",
    };
    resultsDiv.innerHTML = `
      <div class="border rounded-lg p-4 ${colors[type] || colors.error}">
        <p>${message}</p>
      </div>`;
  }

  function renderResult(data) {
    if (!resultsDiv) return;
    const d = data.data || {};
    const score = d.score || 0;
    const scoreColor = score >= 75 ? "text-green-400" : score >= 50 ? "text-yellow-400" : "text-red-400";
    const recBadge = {
      apply: "bg-green-800 text-green-200",
      skip: "bg-red-800 text-red-200",
      reach: "bg-yellow-800 text-yellow-200",
    }[d.recommendation] || "bg-gray-700 text-gray-200";

    const highlights = (d.match_highlights || []).map(h => `<li class="flex items-start gap-2"><span class="text-green-400 mt-0.5">✓</span>${h}</li>`).join("");
    const gaps = (d.gaps || []).map(g => `<li class="flex items-start gap-2"><span class="text-red-400 mt-0.5">✗</span>${g}</li>`).join("");

    resultsDiv.innerHTML = `
      <div class="bg-gray-800 border border-gray-700 rounded-xl p-6 space-y-5">
        <div class="flex items-center justify-between flex-wrap gap-4">
          <div>
            <p class="text-sm text-gray-400 uppercase tracking-wider mb-1">Fit Score</p>
            <p class="text-5xl font-bold ${scoreColor}">${score}<span class="text-2xl text-gray-500">/100</span></p>
          </div>
          <span class="px-4 py-2 rounded-full text-sm font-semibold uppercase tracking-wider ${recBadge}">
            ${d.recommendation || "N/A"}
          </span>
        </div>

        ${d.summary ? `<p class="text-gray-300 text-sm leading-relaxed border-l-2 border-indigo-500 pl-4">${d.summary}</p>` : ""}

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          ${highlights ? `
          <div>
            <h3 class="text-sm font-semibold text-gray-400 uppercase mb-2">Strengths</h3>
            <ul class="space-y-1 text-sm text-gray-300">${highlights}</ul>
          </div>` : ""}

          ${gaps ? `
          <div>
            <h3 class="text-sm font-semibold text-gray-400 uppercase mb-2">Gaps</h3>
            <ul class="space-y-1 text-sm text-gray-300">${gaps}</ul>
          </div>` : ""}
        </div>

        ${d.talking_point ? `
        <div class="bg-indigo-950 border border-indigo-800 rounded-lg p-4">
          <h3 class="text-sm font-semibold text-indigo-400 uppercase mb-2">Cover Letter Talking Point</h3>
          <p class="text-sm text-gray-300 italic">"${d.talking_point}"</p>
        </div>` : ""}

        <div class="flex gap-3 pt-2">
          <button onclick="saveJob(${JSON.stringify(data).replace(/"/g, '&quot;')})"
            class="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm rounded-lg transition-colors">
            Save to Tracker
          </button>
        </div>
      </div>`;
  }
});

async function saveJob(data) {
  try {
    const response = await fetch("/api/save-job", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const result = await response.json();
    if (result.success) {
      alert("Job saved to tracker!");
      window.location.reload();
    } else {
      alert("Failed to save: " + (result.error || "Unknown error"));
    }
  } catch (err) {
    alert("Network error saving job.");
    console.error(err);
  }
}
