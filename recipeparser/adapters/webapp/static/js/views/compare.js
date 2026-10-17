// recipeparser/adapters/webapp/static/js/views/compare.js
// Side-by-side run comparison: metrics, settings diff, prompts, errors, recipes, timelines.
import { h, clear, icon, fmt, statusBadge, diffView, barChart, openDrawer } from "../ui.js";
import { api } from "../api.js";

// Metric comparison direction:
// higher-better: recipes, recipes/min, recipes per chunk
// lower-better: duration, failed calls, failure rate, latency, tokens, cost, errors, warnings
const ROW_RULES = {
  duration_secs: { higherBetter: false, type: "duration" },
  "recipes.total": { higherBetter: true, type: "num" },
  "recipes.needs_review": { higherBetter: false, type: "num" },
  "recipes.per_chunk": { higherBetter: true, type: "float2" },
  "recipes.per_minute": { higherBetter: true, type: "float2" },
  "api.calls": { higherBetter: null, type: "num" },
  "api.failed": { higherBetter: false, type: "num" },
  "api.failure_rate": { higherBetter: false, type: "pct" },
  "api.avg_ms": { higherBetter: false, type: "ms" },
  "api.p95_ms": { higherBetter: false, type: "ms" },
  "api.avg_rpm": { higherBetter: true, type: "float1" },
  "api.peak_rpm": { higherBetter: true, type: "num" },
  "tokens.prompt": { higherBetter: false, type: "num" },
  "tokens.output": { higherBetter: false, type: "num" },
  "tokens.thought": { higherBetter: false, type: "num" },
  "tokens.total": { higherBetter: false, type: "num" },
  cost_usd: { higherBetter: false, type: "usd" },
  "errors.errors": { higherBetter: false, type: "num" },
  "errors.warnings": { higherBetter: false, type: "num" },
  "chunks.selected": { higherBetter: null, type: "num" },
  "chunks.done": { higherBetter: null, type: "num" },
  "options.concurrency": { higherBetter: null, type: "num" },
  "options.rpm": { higherBetter: null, type: "num" },
  "options.max_recipes": { higherBetter: null, type: "num" },
  "options.book_percent": { higherBetter: null, type: "pct_int" },
};

function formatVal(v, rule) {
  if (v === null || v === undefined) return "—";
  if (!rule || !rule.type) return String(v);
  switch (rule.type) {
    case "duration": return fmt.dur(v);
    case "num": return fmt.num(v);
    case "float1": return typeof v === "number" ? v.toFixed(1) : fmt.num(v, 1);
    case "float2": return typeof v === "number" ? v.toFixed(2) : fmt.num(v, 2);
    case "pct": return fmt.pct(Number(v) * 100, 1);
    case "pct_int": return `${v}%`;
    case "ms": return fmt.ms(v);
    case "usd": return fmt.usd(v);
    default: return String(v);
  }
}

export async function render(root, params, query) {
  const idsParam = query.get("ids") || "";
  const initialIds = idsParam.split(",").map((s) => s.trim()).filter(Boolean);

  if (initialIds.length < 2) {
    await renderPicker(root, initialIds);
  } else {
    await renderComparison(root, initialIds);
  }

  return () => {};
}

async function renderPicker(root, preselected = []) {
  clear(root,
    h("div", { class: "page" },
      h("div", { class: "page-head" },
        h("div", {},
          h("h1", {}, "Compare runs"),
          h("p", { class: "sub" }, "Select two or more runs to compare metrics, settings, prompts, errors, and extracted recipes side by side.")
        )
      ),
      h("div", { class: "card card-pad" },
        h("div", { class: "empty", style: { padding: "20px 0" } },
          h("div", { class: "spinner" }),
          h("p", { class: "muted mt-8" }, "Loading runs…")
        )
      )
    )
  );

  let runsData;
  try {
    runsData = await api.get("/api/runs");
  } catch (e) {
    clear(root,
      h("div", { class: "page" },
        h("div", { class: "page-head" },
          h("div", {}, h("h1", {}, "Compare runs"))
        ),
        h("div", { class: "alert red" },
          h("div", { class: "alert-title" }, "Could not load runs"),
          h("div", {}, e.message || "Unable to reach server.")
        )
      )
    );
    return;
  }

  const runs = runsData?.runs || [];
  const selected = new Set(preselected);

  function update() {
    const compareBtn = root.querySelector("#compare-btn");
    const countSpan = root.querySelector("#selected-count");
    if (compareBtn) compareBtn.disabled = selected.size < 2;
    if (countSpan) countSpan.textContent = `${selected.size} selected`;
  }

  const body = h("div", { class: "card-body" },
    runs.length === 0
      ? h("div", { class: "empty" },
          h("h3", {}, "No runs found"),
          h("p", { class: "muted" }, "Run an extraction first before comparing.")
        )
      : h("div", { class: "table-wrap" },
          h("table", { class: "table" },
            h("thead", {},
              h("tr", {},
                h("th", { style: { width: "36px" } }),
                h("th", {}, "Run / Book"),
                h("th", {}, "Status"),
                h("th", {}, "Created"),
                h("th", { class: "num" }, "Recipes"),
                h("th", { class: "num" }, "Duration")
              )
            ),
            h("tbody", {},
              runs.map((r) => {
                const cb = h("input", {
                  type: "checkbox",
                  checked: selected.has(r.id),
                  onchange: (e) => {
                    if (e.target.checked) selected.add(r.id);
                    else selected.delete(r.id);
                    update();
                  },
                });
                return h("tr", {
                  style: { cursor: "pointer" },
                  onclick: (e) => {
                    if (e.target.tagName !== "INPUT" && e.target.tagName !== "A") {
                      cb.checked = !cb.checked;
                      cb.dispatchEvent(new Event("change"));
                    }
                  },
                },
                  h("td", {}, cb),
                  h("td", {},
                    h("div", { style: { fontWeight: 500 } }, r.label || r.book_name || r.id),
                    r.label && r.book_name ? h("div", { class: "faint small" }, r.book_name) : null,
                    h("div", { class: "mono faint small" }, r.id.slice(0, 8))
                  ),
                  h("td", {}, statusBadge(r.status)),
                  h("td", { class: "nowrap muted small" }, fmt.datetime(r.created_at)),
                  h("td", { class: "num nowrap" }, fmt.num(r.recipes_count)),
                  h("td", { class: "num nowrap muted" }, fmt.dur(r.duration_secs))
                );
              })
            )
          )
        )
  );

  const head = h("div", { class: "page-head" },
    h("div", {},
      h("h1", {}, "Compare runs"),
      h("p", { class: "sub" }, "Select two or more runs to compare side by side.")
    ),
    h("div", { class: "row" },
      h("span", { id: "selected-count", class: "muted small" }, `${selected.size} selected`),
      h("button", {
        id: "compare-btn",
        class: "btn btn-primary",
        disabled: selected.size < 2,
        onclick: () => {
          if (selected.size >= 2) {
            window.location.hash = `#/compare?ids=${Array.from(selected).join(",")}`;
          }
        },
      }, "Compare runs")
    )
  );

  clear(root, h("div", { class: "page" }, head, h("div", { class: "card" }, body)));
}

async function renderComparison(root, currentIds) {
  clear(root,
    h("div", { class: "page" },
      h("div", { class: "card card-pad" },
        h("div", { class: "empty", style: { padding: "40px 0" } },
          h("div", { class: "spinner" }),
          h("p", { class: "muted mt-8" }, "Loading run comparison…")
        )
      )
    )
  );

  let data;
  try {
    data = await api.get(`/api/compare?ids=${currentIds.join(",")}`);
  } catch (e) {
    clear(root,
      h("div", { class: "page" },
        h("div", { class: "page-head" },
          h("div", {}, h("h1", {}, "Compare runs")),
          h("a", { class: "btn btn-ghost btn-sm", href: "#/compare" }, "Pick different runs")
        ),
        h("div", { class: "alert red" },
          h("div", { class: "alert-title" }, "Failed to compare runs"),
          h("div", {}, e.message || "An error occurred while loading comparison data.")
        )
      )
    );
    return;
  }

  const { runs, rows, settings_diff, prompts_diff, errors_by_code, recipes, timelines } = data;

  function removeRun(rid) {
    const next = currentIds.filter((id) => id !== rid);
    if (next.length < 2) {
      window.location.hash = `#/compare?ids=${next.join(",")}`;
    } else {
      window.location.hash = `#/compare?ids=${next.join(",")}`;
    }
  }

  async function openAddModal() {
    let allRuns = [];
    try {
      const res = await api.get("/api/runs");
      allRuns = (res?.runs || []).filter((r) => !currentIds.includes(r.id));
    } catch {
      allRuns = [];
    }

    const list = h("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } },
      allRuns.length === 0
        ? h("p", { class: "muted" }, "No other runs available.")
        : allRuns.map((r) =>
            h("div", {
              class: "card card-pad row between",
              style: { padding: "10px 14px", cursor: "pointer" },
              onclick: () => {
                const next = [...currentIds, r.id];
                window.location.hash = `#/compare?ids=${next.join(",")}`;
              },
            },
              h("div", {},
                h("div", { style: { fontWeight: 500 } }, r.label || r.book_name || r.id),
                h("div", { class: "muted small" }, `${r.book_name || ""} • ${fmt.datetime(r.created_at)}`),
                h("div", { class: "mono faint small" }, r.id.slice(0, 8))
              ),
              h("div", { class: "row" },
                statusBadge(r.status),
                h("button", { class: "btn btn-sm btn-primary" }, "Add")
              )
            )
          )
    );

    openDrawer("Add run to comparison", list);
  }

  // Header element
  const headEl = h("div", { class: "page-head" },
    h("div", {},
      h("h1", {}, "Run comparison"),
      h("p", { class: "sub" }, `Comparing ${runs.length} runs side by side.`)
    ),
    h("div", { class: "row" },
      h("button", { class: "btn btn-sm", onclick: openAddModal }, icon("plus"), "Add run"),
      h("a", { class: "btn btn-ghost btn-sm", href: "#/compare" }, "Change selection")
    )
  );

  // 1. Run Header Columns & Metrics table
  const colCount = runs.length;

  const theadTr = h("tr", {},
    h("th", { style: { width: "220px", minWidth: "180px" } }, "Metric"),
    runs.map((r) =>
      h("th", { style: { minWidth: "180px", verticalAlign: "top" } },
        h("div", { class: "row between", style: { marginBottom: "6px" } },
          h("a", { class: "link", href: `#/runs/${r.id}`, style: { fontWeight: 600, fontSize: "14px" } },
            r.label || r.id.slice(0, 8)
          ),
          h("button", {
            class: "icon-btn",
            style: { width: "22px", height: "22px" },
            title: "Remove from comparison",
            onclick: (e) => { e.stopPropagation(); removeRun(r.id); },
          }, icon("x"))
        ),
        r.book ? h("div", { class: "muted small ellipsis", title: r.book }, r.book) : null,
        h("div", { class: "mt-8 row", style: { gap: "6px" } }, statusBadge(r.status)),
        h("div", { class: "faint small mt-8" }, fmt.datetime(r.created_at))
      )
    )
  );

  // Process rows
  const tbodyTrs = (rows || []).map((row) => {
    const rule = ROW_RULES[row.key] || { higherBetter: null, type: null };
    const values = row.values || [];
    const firstVal = values[0];

    // Compute best/worst if numeric and rule.higherBetter !== null
    let bestVal = null;
    let worstVal = null;
    if (rule.higherBetter !== null) {
      const numVals = values.map((v) => (v !== null && v !== undefined && !Number.isNaN(Number(v)) ? Number(v) : null));
      const validNums = numVals.filter((v) => v !== null);
      if (validNums.length > 1) {
        const minVal = Math.min(...validNums);
        const maxVal = Math.max(...validNums);
        if (minVal !== maxVal) {
          if (rule.higherBetter === true) {
            bestVal = maxVal;
            worstVal = minVal;
          } else {
            bestVal = minVal;
            worstVal = maxVal;
          }
        }
      }
    }

    return h("tr", {},
      h("td", { style: { fontWeight: 500 } }, row.label),
      values.map((v, idx) => {
        const isNumeric = v !== null && v !== undefined && !Number.isNaN(Number(v));
        const numV = isNumeric ? Number(v) : null;

        let deltaEl = null;
        if (idx > 0 && isNumeric && firstVal !== null && firstVal !== undefined && !Number.isNaN(Number(firstVal))) {
          const numFirst = Number(firstVal);
          if (numFirst !== 0) {
            const pctDelta = ((numV - numFirst) / Math.abs(numFirst)) * 100;
            const sign = pctDelta > 0 ? "+" : "";
            const text = `${sign}${pctDelta.toFixed(1)}%`;

            let color = "muted";
            if (rule.higherBetter === true) {
              color = pctDelta > 0 ? "best" : pctDelta < 0 ? "worst" : "muted";
            } else if (rule.higherBetter === false) {
              color = pctDelta < 0 ? "best" : pctDelta > 0 ? "worst" : "muted";
            }

            deltaEl = h("span", {
              class: `small ${color}`,
              style: { marginLeft: "8px", fontWeight: 600, fontSize: "11.5px" },
            }, text);
          }
        }

        let cellCls = "";
        if (numV !== null) {
          if (bestVal !== null && numV === bestVal) cellCls = "best";
          else if (worstVal !== null && numV === worstVal) cellCls = "worst";
        }

        return h("td", { class: cellCls },
          h("div", { class: "row", style: { gap: "4px" } },
            h("span", {}, formatVal(v, rule)),
            deltaEl
          )
        );
      })
    );
  });

  const metricsCard = h("div", { class: "card mb-16" },
    h("div", { class: "card-head" },
      h("h3", {}, "Performance & extraction metrics"),
      h("span", { class: "muted small" }, "Deltas compare vs Run 1")
    ),
    h("div", { class: "table-wrap" },
      h("table", { class: "table" },
        h("thead", {}, theadTr),
        h("tbody", {}, tbodyTrs)
      )
    )
  );

  // 2. Timelines
  const timelineCard = h("div", { class: "card mb-16" },
    h("div", { class: "card-head" },
      h("h3", {}, "Activity timelines"),
      h("span", { class: "muted small" }, "Requests and extracted recipes by minute")
    ),
    h("div", { class: "card-body" },
      h("div", { class: `grid cols-${Math.min(colCount, 3)}` },
        runs.map((r, i) => {
          const t = timelines?.[i] || [];
          const series = t.map((item) => ({
            label: `${item.minute}m`,
            ok: item.ok || 0,
            failed: item.failed || 0,
            recipes: item.recipes || 0,
          }));
          const chartKeys = [
            { key: "ok", cls: "chart" },
            { key: "failed", cls: "chart-bar-failed" },
            { key: "recipes", cls: "chart-bar-recipes" },
          ];

          return h("div", { class: "card card-pad" },
            h("div", { class: "row between mb-16" },
              h("a", { class: "link", href: `#/runs/${r.id}`, style: { fontWeight: 500 } }, r.label || r.id.slice(0, 8)),
              h("span", { class: "faint small" }, `${series.length} min`)
            ),
            series.length === 0
              ? h("p", { class: "faint small", style: { textAlign: "center", padding: "20px 0" } }, "No timeline events recorded")
              : barChart(series, chartKeys, { height: 120 }),
            h("div", { class: "legend mt-8", style: { fontSize: "11px" } },
              h("span", { style: { "--c": "var(--text)" } }, "API calls"),
              h("span", { style: { "--c": "var(--danger)" } }, "Failed"),
              h("span", { style: { "--c": "var(--accent)" } }, "Recipes")
            )
          );
        })
      )
    )
  );

  // 3. Settings differences table
  const settingsDiffs = settings_diff || [];
  const settingsCard = h("div", { class: "card mb-16" },
    h("div", { class: "card-head" },
      h("h3", {}, "Settings differences"),
      h("span", { class: "muted small" }, `${settingsDiffs.length} settings differ`)
    ),
    settingsDiffs.length === 0
      ? h("div", { class: "card-body faint small" }, "All configuration settings were identical across these runs.")
      : h("div", { class: "table-wrap" },
          h("table", { class: "table" },
            h("thead", {},
              h("tr", {},
                h("th", { style: { width: "220px" } }, "Setting"),
                runs.map((r) => h("th", {}, r.label || r.id.slice(0, 8)))
              )
            ),
            h("tbody", {},
              settingsDiffs.map((s) =>
                h("tr", {},
                  h("td", { class: "mono small" }, s.key),
                  (s.values || []).map((v) =>
                    h("td", { class: "mono small" }, v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v))
                  )
                )
              )
            )
          )
        )
  );

  // 4. Prompt differences
  const promptsDiffs = prompts_diff || [];
  const promptsCard = h("div", { class: "card mb-16" },
    h("div", { class: "card-head" },
      h("h3", {}, "Prompt differences"),
      h("span", { class: "muted small" }, `${promptsDiffs.length} customized prompts`)
    ),
    promptsDiffs.length === 0
      ? h("div", { class: "card-body faint small" }, "No run had custom prompt overrides.")
      : h("div", { class: "table-wrap" },
          h("table", { class: "table" },
            h("thead", {},
              h("tr", {},
                h("th", { style: { width: "220px" } }, "Prompt name"),
                runs.map((r) => h("th", {}, r.label || r.id.slice(0, 8))),
                h("th", { style: { width: "120px" } }, "Status")
              )
            ),
            h("tbody", {},
              promptsDiffs.map((p) =>
                h("tr", {},
                  h("td", { style: { fontWeight: 500 } }, p.name),
                  (p.edited || []).map((ed) =>
                    h("td", {}, ed ? h("span", { class: "badge purple" }, "edited") : h("span", { class: "faint small" }, "default"))
                  ),
                  h("td", {},
                    p.same
                      ? h("span", { class: "badge green" }, "identical")
                      : h("span", { class: "badge amber" }, "differed")
                  )
                )
              )
            )
          )
        )
  );

  // 5. Errors-by-code matrix
  const errorsMatrix = errors_by_code || [];
  const errorsCard = h("div", { class: "card mb-16" },
    h("div", { class: "card-head" },
      h("h3", {}, "Errors by code"),
      h("span", { class: "muted small" }, `${errorsMatrix.length} error types`)
    ),
    errorsMatrix.length === 0
      ? h("div", { class: "card-body faint small" }, "No errors were logged across these runs.")
      : h("div", { class: "table-wrap" },
          h("table", { class: "table" },
            h("thead", {},
              h("tr", {},
                h("th", { style: { width: "220px" } }, "Error code"),
                runs.map((r) => h("th", { class: "num" }, r.label || r.id.slice(0, 8)))
              )
            ),
            h("tbody", {},
              errorsMatrix.map((e) =>
                h("tr", {},
                  h("td", { class: "mono small" }, e.code),
                  (e.values || []).map((cnt) =>
                    h("td", { class: `num ${cnt > 0 ? "worst" : ""}` }, fmt.num(cnt))
                  )
                )
              )
            )
          )
        )
  );

  // 6. Recipe overlap (common/union + only in run X lists, collapsible)
  const recipeData = recipes || { union: 0, common: 0, only: [] };
  const onlyLists = recipeData.only || [];

  const recipeOverlapCard = h("div", { class: "card mb-16" },
    h("div", { class: "card-head" },
      h("h3", {}, "Recipe extraction overlap"),
      h("div", { class: "row", style: { gap: "8px" } },
        h("span", { class: "badge green" }, `Common: ${fmt.num(recipeData.common)}`),
        h("span", { class: "badge blue" }, `Union: ${fmt.num(recipeData.union)}`)
      )
    ),
    h("div", { class: "card-body" },
      h("p", { class: "muted small mb-16" },
        `${fmt.num(recipeData.common)} recipes were extracted in all runs (${recipeData.union ? Math.round((recipeData.common / recipeData.union) * 100) : 0}% overlap). Below are recipes unique to only one run.`
      ),
      h("div", { class: `grid cols-${Math.min(colCount, 3)}` },
        runs.map((r, i) => {
          const list = onlyLists[i] || [];
          const detailsEl = h("details", { style: { marginTop: "8px" } },
            h("summary", { style: { cursor: "pointer", fontSize: "13px", color: "var(--text-2)" } },
              `View ${list.length} recipe titles`
            ),
            h("ul", {
              style: {
                margin: "8px 0 0 0",
                paddingLeft: "18px",
                maxHeight: "220px",
                overflowY: "auto",
                fontSize: "12.5px",
                lineHeight: "1.6",
              },
            },
              list.map((title) => h("li", {}, title))
            )
          );

          return h("div", { class: "card card-pad" },
            h("div", { class: "row between" },
              h("div", { style: { fontWeight: 500 } }, r.label || r.id.slice(0, 8)),
              h("span", { class: "badge" }, `${list.length} unique`)
            ),
            list.length === 0
              ? h("p", { class: "faint small mt-8" }, "No unique recipes")
              : detailsEl
          );
        })
      )
    )
  );

  clear(root,
    h("div", { class: "page" },
      headEl,
      metricsCard,
      timelineCard,
      settingsCard,
      promptsCard,
      errorsMatrix.length > 0 ? errorsCard : null,
      recipeOverlapCard
    )
  );
}
