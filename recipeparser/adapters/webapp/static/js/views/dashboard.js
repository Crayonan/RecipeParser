// recipeparser/adapters/webapp/static/js/views/dashboard.js
import { h, clear, icon, fmt, statusBadge, confirmDialog } from "../ui.js";
import { api } from "../api.js";
import { live } from "../live.js";

export async function render(root, params, query) {
  let disposed = false;
  let runs = [];
  let errorCount = 0;
  let activeRunsList = [];
  const selectedRunIds = new Set();
  let statusFilter = "all"; // "all" | "active" | "finished" | "problems"
  let searchQuery = "";

  // Structure containers
  const headContainer = h("div", { class: "page-head" });
  const activeSection = h("div", { class: "mb-16", style: { display: "none" } });
  const statsContainer = h("div", { class: "grid cols-4 mb-16" });
  const tableSection = h("div", { class: "card" });

  clear(root, headContainer, activeSection, statsContainer, tableSection);

  // 1. Page Head
  function renderHead() {
    clear(
      headContainer,
      h("div", {},
        h("h1", {}, "Runs"),
        h("div", { class: "sub small" }, "Live recipe extractions, progress, and history.")
      ),
      h("div", { class: "row" },
        h("a", { class: "btn btn-primary", href: "#/new" },
          icon("plus"),
          "New run"
        )
      )
    );
  }

  // 2. Summary Stats Row
  function renderStats() {
    const totalRuns = runs.length;
    const recipesExtracted = runs.reduce((sum, r) => sum + (r.recipes_done || 0), 0);
    const failedRuns = runs.filter((r) =>
      r.status === "failed" || r.status === "crashed" || r.status === "killed"
    ).length;

    clear(
      statsContainer,
      h("div", { class: "card stat" },
        h("div", { class: "label" }, "Total runs"),
        h("div", { class: "value" }, fmt.num(totalRuns)),
        h("div", { class: "hint" }, "Historical and active")
      ),
      h("div", { class: "card stat" },
        h("div", { class: "label" }, "Recipes extracted"),
        h("div", { class: "value" }, fmt.num(recipesExtracted)),
        h("div", { class: "hint" }, "Across all runs")
      ),
      h("div", { class: "card stat" },
        h("div", { class: "label" }, "Runs failed / crashed"),
        h("div", { class: "value", style: failedRuns ? { color: "var(--danger)" } : {} }, fmt.num(failedRuns)),
        h("div", { class: "hint" }, "Failed, killed or crashed")
      ),
      h("div", { class: "card stat" },
        h("div", { class: "label" }, "Unresolved errors"),
        h("div", { class: "value", style: errorCount ? { color: "var(--danger)" } : {} }, fmt.num(errorCount)),
        h("div", { class: "hint" },
          errorCount > 0
            ? h("a", { class: "link", href: "#/errors" }, "View in Errors & alerts")
            : "No active errors"
        )
      )
    );
  }

  // 3. Active Runs Section
  async function handleControl(runId, cmd) {
    if (cmd === "kill") {
      if (!confirmDialog(`Kill run ${runId}? The process will be terminated immediately.`)) {
        return;
      }
    }
    try {
      await api.post(`/api/runs/${encodeURIComponent(runId)}/${cmd}`);
      await reloadRuns();
    } catch {
      // api.post toasts error automatically
    }
  }

  function renderActiveRuns() {
    if (activeRunsList.length === 0) {
      activeSection.style.display = "none";
      clear(activeSection);
      return;
    }
    activeSection.style.display = "block";

    const cards = activeRunsList.map((run) => {
      const pct = run.progress ?? (run.chunks_total ? Math.round((run.chunks_done || 0) * 100 / run.chunks_total) : 0);
      const isPaused = run.status === "paused" || run.status === "pausing";
      const pauseResumeCmd = isPaused ? "resume" : "pause";
      const pauseResumeIcon = isPaused ? "play" : "pause";
      const pauseResumeLabel = isPaused ? "Resume" : "Pause";

      return h("div", { class: "card mb-16", style: { borderColor: "var(--border-strong)" } },
        h("div", { class: "card-head" },
          h("div", { class: "row", style: { flexWrap: "wrap" } },
            statusBadge(run.status),
            h("a", {
              href: `#/runs/${encodeURIComponent(run.id)}`,
              style: { fontWeight: "600", fontSize: "15px" }
            }, run.book_name || run.book_path || run.id),
            run.label ? h("span", { class: "badge" }, run.label) : null
          ),
          h("div", { class: "btn-group" },
            h("button", {
              class: "btn btn-sm",
              title: `${pauseResumeLabel} extraction`,
              onclick: (e) => { e.stopPropagation(); handleControl(run.id, pauseResumeCmd); }
            },
              icon(pauseResumeIcon),
              pauseResumeLabel
            ),
            h("button", {
              class: "btn btn-sm btn-danger",
              title: "Stop cleanly after current chunks",
              onclick: (e) => { e.stopPropagation(); handleControl(run.id, "stop"); }
            },
              icon("stop"),
              "Stop"
            ),
            h("button", {
              class: "btn btn-sm btn-kill",
              title: "Kill process immediately",
              onclick: (e) => { e.stopPropagation(); handleControl(run.id, "kill"); }
            },
              icon("kill"),
              "Kill"
            )
          )
        ),
        h("div", { class: "card-body col" },
          h("div", { class: "row between small" },
            h("div", { class: "row" },
              h("span", { class: "muted" }, "Stage:"),
              h("strong", {}, run.stage || "processing"),
              h("span", { class: "faint" }, "•"),
              h("span", { class: "muted" }, "Elapsed:"),
              h("span", {}, fmt.dur(run.elapsed))
            ),
            h("div", { class: "row" },
              h("span", { class: "muted" }, "Chunks:"),
              h("span", {}, `${fmt.num(run.chunks_done || 0)} / ${fmt.num(run.chunks_total || 0)}`),
              h("span", { class: "muted" }, `(${fmt.pct(pct, 1)})`),
              h("span", { class: "faint" }, "•"),
              h("span", { class: "muted" }, "Recipes:"),
              h("span", { style: { fontWeight: "600", color: "var(--accent)" } }, fmt.num(run.recipes_done || 0)),
              (run.errors > 0 || run.warnings > 0) ? h("span", { class: "faint" }, "•") : null,
              run.errors > 0 ? h("span", { class: "badge red small" }, `${run.errors} err`) : null,
              run.warnings > 0 ? h("span", { class: "badge amber small" }, `${run.warnings} warn`) : null
            )
          ),
          h("div", { class: "progress", style: { height: "8px" } },
            h("div", {
              class: run.errors ? "amber" : "green",
              style: { width: `${Math.min(100, Math.max(0, pct))}%` }
            })
          )
        )
      );
    });

    clear(
      activeSection,
      h("h3", { class: "mb-16", style: { display: "flex", alignItems: "center", gap: "8px" } },
        h("span", { class: "badge blue pulse" }, h("span", { class: "dot" }), "Active runs"),
        h("span", { class: "muted small" }, `(${activeRunsList.length})`)
      ),
      cards
    );
  }

  // 4. Runs Table Section
  function filterRuns() {
    return runs.filter((r) => {
      // Status filter
      if (statusFilter === "active") {
        if (!r.active) return false;
      } else if (statusFilter === "finished") {
        if (r.active) return false;
      } else if (statusFilter === "problems") {
        const hasErr = (r.errors > 0) || r.status === "failed" || r.status === "crashed" || r.status === "killed" || r.status === "completed_with_errors";
        if (!hasErr) return false;
      }

      // Search filter
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        const str = [
          r.id,
          r.label,
          r.book_name,
          r.book_path,
          r.provider,
          r.gen_model,
          r.status,
          r.stage,
          r.outcome
        ].filter(Boolean).join(" ").toLowerCase();
        if (!str.includes(q)) return false;
      }
      return true;
    });
  }

  function renderTable() {
    const filtered = filterRuns();

    // Table head / controls toolbar
    const toolbar = h("div", { class: "card-head", style: { flexWrap: "wrap" } },
      h("div", { class: "row wrap", style: { flex: "1" } },
        h("div", { class: "seg" },
          ["all", "active", "finished", "problems"].map((st) =>
            h("button", {
              class: statusFilter === st ? "on" : "",
              onclick: () => { statusFilter = st; renderTable(); }
            }, st.charAt(0).toUpperCase() + st.slice(1))
          )
        ),
        h("input", {
          type: "search",
          class: "input input-sm",
          style: { width: "220px" },
          placeholder: "Search runs, books, models…",
          value: searchQuery,
          oninput: (e) => { searchQuery = e.target.value.trim(); renderTable(); }
        })
      ),
      h("div", { class: "row" },
        h("button", {
          class: "btn btn-sm",
          disabled: selectedRunIds.size < 2,
          onclick: () => {
            const ids = Array.from(selectedRunIds).join(",");
            location.hash = `#/compare?ids=${encodeURIComponent(ids)}`;
          }
        },
          icon("compare"),
          `Compare selected (${selectedRunIds.size})`
        )
      )
    );

    let bodyContent;
    if (runs.length === 0) {
      bodyContent = h("div", { class: "empty" },
        h("h3", {}, "No runs yet"),
        h("p", { class: "muted mb-16" }, "Start an extraction by choosing a cookbook and extraction provider."),
        h("a", { class: "btn btn-primary", href: "#/new" },
          icon("plus"),
          "Start your first run"
        )
      );
    } else if (filtered.length === 0) {
      bodyContent = h("div", { class: "empty" },
        h("h3", {}, "No matching runs"),
        h("p", { class: "muted" }, "Try clearing filters or search terms.")
      );
    } else {
      const allFilteredSelected = filtered.length > 0 && filtered.every((r) => selectedRunIds.has(r.id));
      const trs = filtered.map((run) => {
        const isSelected = selectedRunIds.has(run.id);
        const pct = run.progress ?? (run.chunks_total ? Math.round((run.chunks_done || 0) * 100 / run.chunks_total) : 0);
        const providerModel = [run.provider, run.gen_model].filter(Boolean).join(" · ");

        return h("tr", {
          onclick: (e) => {
            // If user clicked directly on checkbox, don't navigate
            if (e.target.tagName === "INPUT") return;
            location.hash = `#/runs/${encodeURIComponent(run.id)}`;
          }
        },
          h("td", { style: { width: "36px" } },
            h("input", {
              type: "checkbox",
              checked: isSelected,
              onclick: (e) => {
                e.stopPropagation();
                if (e.target.checked) selectedRunIds.add(run.id);
                else selectedRunIds.delete(run.id);
                renderTable();
              }
            })
          ),
          h("td", { style: { width: "120px" } }, statusBadge(run.status)),
          h("td", {},
            h("div", { style: { fontWeight: "500" } },
              run.book_name || run.book_path || run.id,
              run.label ? h("span", { class: "badge faint", style: { marginLeft: "8px" } }, run.label) : null
            ),
            run.outcome
              ? h("div", { class: "muted small ellipsis", style: { maxWidth: "420px" } }, run.outcome)
              : null
          ),
          h("td", { class: "small muted nowrap" }, providerModel || "—"),
          h("td", { class: "num" },
            h("span", { style: { fontWeight: "600" } }, fmt.num(run.recipes_done ?? 0))
          ),
          h("td", { class: "nowrap" },
            (run.errors > 0)
              ? h("span", { class: "badge red small", style: { marginRight: "4px" } }, `${run.errors} err`)
              : null,
            (run.warnings > 0)
              ? h("span", { class: "badge amber small" }, `${run.warnings} warn`)
              : null,
            (!run.errors && !run.warnings) ? h("span", { class: "faint small" }, "0") : null
          ),
          h("td", { style: { width: "140px" } },
            h("div", { class: "row", style: { gap: "6px" } },
              h("div", { class: "progress", style: { flex: 1, height: "6px" } },
                h("div", {
                  class: run.status === "failed" || run.status === "crashed" ? "red" : (run.errors ? "amber" : "green"),
                  style: { width: `${Math.min(100, Math.max(0, pct))}%` }
                })
              ),
              h("span", { class: "small faint", style: { minWidth: "34px", textAlign: "right" } },
                fmt.pct(pct)
              )
            )
          ),
          h("td", { class: "small muted nowrap" }, fmt.dur(run.elapsed)),
          h("td", { class: "small muted nowrap", title: fmt.datetime(run.created_at) }, fmt.ago(run.created_at))
        );
      });

      const table = h("div", { class: "table-wrap" },
        h("table", { class: "table" },
          h("thead", {},
            h("tr", {},
              h("th", { style: { width: "36px" } },
                h("input", {
                  type: "checkbox",
                  checked: allFilteredSelected,
                  onchange: (e) => {
                    const checked = e.target.checked;
                    filtered.forEach((r) => {
                      if (checked) selectedRunIds.add(r.id);
                      else selectedRunIds.delete(r.id);
                    });
                    renderTable();
                  }
                })
              ),
              h("th", {}, "Status"),
              h("th", {}, "Book / Label"),
              h("th", {}, "Provider / Model"),
              h("th", { class: "num" }, "Recipes"),
              h("th", {}, "Errors"),
              h("th", {}, "Progress"),
              h("th", {}, "Duration"),
              h("th", {}, "Created")
            )
          ),
          h("tbody", {}, trs)
        )
      );
      bodyContent = table;
    }

    clear(tableSection, toolbar, bodyContent);
  }

  async function reloadRuns() {
    if (disposed) return;
    try {
      const data = await api.get("/api/runs?limit=200", { quiet: true });
      if (disposed) return;
      runs = data.runs || [];
      activeRunsList = runs.filter((r) => r.active);
      renderStats();
      renderActiveRuns();
      renderTable();
    } catch {
      // quiet request, network error handled
    }
  }

  async function reloadErrors() {
    if (disposed) return;
    try {
      const data = await api.get("/api/errors?unresolved=1&limit=500", { quiet: true });
      if (disposed) return;
      errorCount = (data.errors || []).length;
      renderStats();
    } catch {
      // quiet
    }
  }

  // Initial render
  renderHead();
  renderStats();
  renderTable();

  // Load data
  await Promise.all([reloadRuns(), reloadErrors()]);

  // Subscriptions
  const unsubs = [];

  // Live runs stream update
  unsubs.push(
    live.on("runs", (activeList) => {
      if (disposed) return;
      activeRunsList = activeList || [];
      // Also update runs state for the active entries
      const map = new Map(activeRunsList.map((r) => [r.id, r]));
      let changed = false;
      runs = runs.map((r) => {
        if (map.has(r.id)) {
          changed = true;
          return { ...r, ...map.get(r.id) };
        }
        return r;
      });
      // If there is an active run not yet in `runs` or just finished, reload list
      for (const ar of activeRunsList) {
        if (!runs.some((r) => r.id === ar.id)) {
          reloadRuns();
          return;
        }
      }
      renderActiveRuns();
      if (changed) renderTable();
    })
  );

  // Live events stream update
  unsubs.push(
    live.on("events", (evs) => {
      if (disposed) return;
      let shouldReload = false;
      for (const e of evs) {
        if (typeof e.kind === "string" && e.kind.startsWith("run.")) {
          shouldReload = true;
          break;
        }
      }
      if (shouldReload) {
        reloadRuns();
        reloadErrors();
      }
    })
  );

  return () => {
    disposed = true;
    unsubs.forEach((fn) => fn());
  };
}
