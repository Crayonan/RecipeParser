// recipeparser/adapters/webapp/static/js/views/errors.js
// Global error/alert log to document and fix issues across runs.
import { h, clear, icon, fmt, severityBadge, openDrawer, closeDrawer, toast, debounce } from "../ui.js";
import { api } from "../api.js";
import { live } from "../live.js";

export async function render(root, params, query) {
  let state = {
    unresolvedOnly: query.get("unresolved") === "1" || false,
    severity: query.get("severity") || "",
    code: query.get("code") || "",
    loading: true,
    data: { errors: [], summary: [] },
  };

  async function load() {
    try {
      const q = new URLSearchParams();
      if (state.unresolvedOnly) q.set("unresolved", "true");
      if (state.severity) q.set("severity", state.severity);
      if (state.code) q.set("code", state.code);

      const qs = q.toString() ? `?${q.toString()}` : "";
      state.data = await api.get(`/api/errors${qs}`);
    } catch (e) {
      toast("Error loading log", e.message, "red");
    } finally {
      state.loading = false;
      renderView();
    }
  }

  function setFilters({ unresolvedOnly, severity, code }) {
    if (unresolvedOnly !== undefined) state.unresolvedOnly = unresolvedOnly;
    if (severity !== undefined) state.severity = severity;
    if (code !== undefined) state.code = code;

    const q = new URLSearchParams();
    if (state.unresolvedOnly) q.set("unresolved", "1");
    if (state.severity) q.set("severity", state.severity);
    if (state.code) q.set("code", state.code);

    const hashPath = q.toString() ? `#/errors?${q.toString()}` : "#/errors";
    window.location.hash = hashPath;
    state.loading = true;
    renderView();
    load();
  }

  async function openCallDrawer(callId) {
    openDrawer("API call inspection",
      h("div", { class: "empty", style: { padding: "40px 0" } },
        h("div", { class: "spinner" }),
        h("p", { class: "muted mt-8" }, "Loading API call details…")
      )
    );

    try {
      const call = await api.get(`/api/calls/${callId}`);
      const body = h("div", { class: "col", style: { gap: "16px" } },
        h("div", { class: "grid cols-3" },
          h("div", { class: "card card-pad" },
            h("div", { class: "faint small" }, "Status"),
            h("div", { style: { fontWeight: 600, marginTop: "4px" } },
              call.ok ? h("span", { class: "badge green" }, "OK 200") : h("span", { class: "badge red" }, `Failed ${call.status_code || ""}`)
            )
          ),
          h("div", { class: "card card-pad" },
            h("div", { class: "faint small" }, "Model & Kind"),
            h("div", { style: { fontWeight: 500, marginTop: "4px" } }, `${call.kind} (${call.model || "—"})`)
          ),
          h("div", { class: "card card-pad" },
            h("div", { class: "faint small" }, "Latency & Tokens"),
            h("div", { style: { fontWeight: 500, marginTop: "4px" } }, `${fmt.ms(call.duration_ms)} • ${fmt.num(call.total_tokens)} tok`)
          )
        ),
        call.error ? h("div", { class: "alert red" },
          h("div", { class: "alert-title" }, "API error response"),
          h("pre", { class: "pre", style: { margin: "8px 0 0 0" } }, call.error)
        ) : null,
        h("div", {},
          h("h3", { style: { marginBottom: "8px" } }, "Prompt text"),
          h("pre", { class: "pre", style: { maxHeight: "280px", overflow: "auto" } }, call.prompt_text || "—")
        ),
        h("div", {},
          h("h3", { style: { marginBottom: "8px" } }, "Model response"),
          h("pre", { class: "pre", style: { maxHeight: "280px", overflow: "auto" } }, call.response_text || "—")
        )
      );
      openDrawer(`API call #${callId}`, body);
    } catch (e) {
      openDrawer(`API call #${callId}`,
        h("div", { class: "alert red" },
          h("div", { class: "alert-title" }, "Could not load API call"),
          h("div", {}, e.message)
        )
      );
    }
  }

  function openErrorDrawer(err) {
    let currentResolved = Boolean(err.resolved);
    let noteVal = err.resolution_note || "";

    const noteTextarea = h("textarea", {
      class: "input",
      rows: 3,
      placeholder: "Add resolution notes, why this occurred, or fix verification…",
    }, noteVal);

    async function toggleResolve(btn) {
      btn.disabled = true;
      try {
        const nextResolved = !currentResolved;
        const res = await api.patch(`/api/errors/${err.id}`, {
          resolved: nextResolved,
          note: noteTextarea.value,
        });
        currentResolved = Boolean(res.resolved);
        err.resolved = res.resolved;
        err.resolution_note = res.resolution_note;
        err.resolved_at = res.resolved_at;
        toast("Updated error status", nextResolved ? "Marked as resolved" : "Reopened error", "green");
        renderDrawerContent();
        renderView();
      } catch (e) {
        toast("Failed to update status", e.message, "red");
      } finally {
        btn.disabled = false;
      }
    }

    function renderDrawerContent() {
      const actions = h("div", { class: "row", style: { gap: "8px" } },
        h("button", {
          class: `btn btn-sm ${currentResolved ? "btn-ghost" : "btn-primary"}`,
          onclick: (e) => toggleResolve(e.currentTarget),
        }, currentResolved ? "Reopen error" : "Mark resolved")
      );

      const content = h("div", { class: "col", style: { gap: "16px" } },
        h("div", { class: "row wrap", style: { gap: "8px" } },
          severityBadge(err.severity),
          h("span", { class: "badge purple" }, err.category),
          h("span", { class: "mono small muted" }, err.code),
          currentResolved ? h("span", { class: "badge green" }, "Resolved") : h("span", { class: "badge amber" }, "Unresolved")
        ),
        h("div", {},
          h("h2", { style: { fontSize: "17px", fontWeight: 600 } }, err.title),
          h("div", { class: "muted small mt-8" },
            `Logged at ${fmt.datetime(err.ts)} (${fmt.ago(err.ts)})`
          )
        ),
        h("div", { class: "grid cols-2" },
          h("div", { class: "card card-pad" },
            h("div", { class: "faint small" }, "Run"),
            h("div", { class: "mt-8" },
              h("a", { class: "link", href: `#/runs/${err.run_id}`, style: { fontWeight: 500 } },
                err.run_label || err.book_name || err.run_id.slice(0, 8)
              ),
              err.book_name ? h("div", { class: "muted small" }, err.book_name) : null
            )
          ),
          h("div", { class: "card card-pad" },
            h("div", { class: "faint small" }, "Location"),
            h("div", { class: "mt-8 small" },
              err.stage ? h("div", {}, `Stage: ${err.stage}`) : null,
              err.chunk !== null && err.chunk !== undefined ? h("div", {}, `Chunk: #${err.chunk}`) : null,
              !err.stage && (err.chunk === null || err.chunk === undefined) ? h("span", { class: "muted" }, "General / pipeline") : null
            )
          )
        ),
        h("div", {},
          h("div", { style: { fontWeight: 600, marginBottom: "6px" } }, "Error message"),
          h("div", { class: "card card-pad mono", style: { whiteSpace: "pre-wrap", wordBreak: "break-word" } },
            err.message
          )
        ),
        err.cause ? h("div", { class: "alert amber" },
          h("div", { class: "alert-title" }, "Probable cause"),
          h("div", {}, err.cause)
        ) : null,
        err.fix ? h("div", { class: "alert green" },
          h("div", { class: "alert-title" }, "Recommended fix"),
          h("div", {}, err.fix)
        ) : null,
        err.api_call_id ? h("div", { class: "row between card card-pad" },
          h("div", {},
            h("div", { style: { fontWeight: 500 } }, `Associated API Call #${err.api_call_id}`),
            h("div", { class: "muted small" }, "Inspect the exact prompt payload and model error response")
          ),
          h("button", { class: "btn btn-sm", onclick: () => openCallDrawer(err.api_call_id) }, "View API call")
        ) : null,
        err.traceback ? h("div", {},
          h("div", { style: { fontWeight: 600, marginBottom: "6px" } }, `Traceback (${err.exception_type || "Exception"})`),
          h("pre", { class: "pre", style: { maxHeight: "240px", overflow: "auto" } }, err.traceback)
        ) : null,
        h("hr", {}),
        h("div", { class: "col", style: { gap: "8px" } },
          h("label", { class: "field-label" }, "Resolution note"),
          noteTextarea,
          h("div", { class: "row between" },
            err.resolved_at ? h("span", { class: "faint small" }, `Resolved ${fmt.datetime(err.resolved_at)}`) : h("span", {}),
            h("button", {
              class: "btn btn-sm",
              onclick: async (e) => {
                const b = e.currentTarget;
                b.disabled = true;
                try {
                  await api.patch(`/api/errors/${err.id}`, { note: noteTextarea.value, resolved: currentResolved });
                  toast("Saved note", "", "green");
                } catch (err2) {
                  toast("Failed to save note", err2.message, "red");
                } finally {
                  b.disabled = false;
                }
              },
            }, "Save note")
          )
        )
      );

      openDrawer(`Error #${err.id}`, content, actions);
    }

    renderDrawerContent();
  }

  function renderView() {
    // 1. Explanatory Header
    const banner = h("div", { class: "alert blue mb-16", style: { gap: "16px" } },
      icon("info"),
      h("div", { style: { flex: 1 } },
        h("div", { class: "alert-title" }, "Severity definitions"),
        h("div", { class: "row wrap mt-8", style: { gap: "16px", fontSize: "13px" } },
          h("div", {}, h("span", { class: "badge amber" }, "warning"), " Handled automatically; run proceeds normally."),
          h("div", {}, h("span", { class: "badge red" }, "error"), " Work lost or partial failure in chunk; run continues with remaining tasks."),
          h("div", {}, h("span", { class: "badge dark" }, "fatal"), " Unrecoverable issue; pipeline or run was aborted/stopped.")
        )
      )
    );

    // 2. Summary cards per code
    const summaryList = state.data.summary || [];
    const summaryCards = h("div", { class: "grid cols-3 mb-16" },
      summaryList.map((s) => {
        const isSelected = state.code === s.code;
        return h("div", {
          class: `card card-pad ${isSelected ? "option-card on" : "option-card"}`,
          style: { cursor: "pointer", transition: "all .12s" },
          onclick: () => {
            setFilters({ code: isSelected ? "" : s.code });
          },
        },
          h("div", { class: "row between" },
            h("span", { class: "mono small", style: { fontWeight: 600 } }, s.code),
            severityBadge(s.severity)
          ),
          h("div", { style: { fontWeight: 600, fontSize: "14px", marginTop: "6px" }, class: "ellipsis" }, s.title),
          h("div", { class: "muted small ellipsis" }, s.category),
          h("div", { class: "row between mt-16" },
            h("span", { class: "value", style: { fontSize: "20px", fontWeight: 700 } }, `${s.count} total`),
            s.unresolved > 0
              ? h("span", { class: "badge amber" }, `${s.unresolved} unresolved`)
              : h("span", { class: "badge green" }, "all resolved")
          ),
          h("div", { class: "faint small mt-8" },
            `${s.runs} run${s.runs === 1 ? "" : "s"} • last seen ${fmt.ago(s.last_ts)}`
          ),
          s.fix ? h("div", { class: "muted small mt-8 ellipsis", title: s.fix }, `Fix: ${s.fix}`) : null
        );
      })
    );

    // 3. Filter controls
    const filterBar = h("div", { class: "card card-pad mb-16 row wrap between" },
      h("div", { class: "row wrap", style: { gap: "12px" } },
        h("label", { class: "row", style: { cursor: "pointer", gap: "6px" } },
          h("input", {
            type: "checkbox",
            checked: state.unresolvedOnly,
            onchange: (e) => setFilters({ unresolvedOnly: e.target.checked }),
          }),
          h("span", { style: { fontWeight: 500, fontSize: "13.5px" } }, "Unresolved only")
        ),
        h("div", { class: "row", style: { gap: "6px" } },
          h("span", { class: "muted small" }, "Severity:"),
          h("select", {
            class: "input input-sm",
            style: { width: "120px" },
            value: state.severity,
            onchange: (e) => setFilters({ severity: e.target.value }),
          },
            h("option", { value: "" }, "All"),
            h("option", { value: "warning" }, "Warning"),
            h("option", { value: "error" }, "Error"),
            h("option", { value: "fatal" }, "Fatal")
          )
        ),
        state.code ? h("div", { class: "row", style: { gap: "6px" } },
          h("span", { class: "badge purple" }, `Code: ${state.code}`),
          h("button", {
            class: "icon-btn",
            style: { width: "20px", height: "20px" },
            onclick: () => setFilters({ code: "" }),
            title: "Clear code filter",
          }, icon("x"))
        ) : null
      ),
      h("div", { class: "muted small" },
        `${state.data.errors.length} occurrence${state.data.errors.length === 1 ? "" : "s"}`
      )
    );

    // 4. Occurrences table
    const errorsList = state.data.errors || [];
    const occurrencesTable = h("div", { class: "card" },
      h("div", { class: "card-head" },
        h("h3", {}, "Occurrences"),
        h("span", { class: "muted small" }, "Click any row to open the resolution drawer")
      ),
      errorsList.length === 0
        ? h("div", { class: "empty" },
            h("h3", {}, "No errors found"),
            h("p", { class: "muted" }, "No errors match your current filter settings.")
          )
        : h("div", { class: "table-wrap" },
            h("table", { class: "table" },
              h("thead", {},
                h("tr", {},
                  h("th", { style: { width: "110px" } }, "Time"),
                  h("th", {}, "Run / Book"),
                  h("th", { style: { width: "80px" } }, "Chunk"),
                  h("th", { style: { width: "90px" } }, "Stage"),
                  h("th", { style: { width: "90px" } }, "Severity"),
                  h("th", { style: { width: "130px" } }, "Code"),
                  h("th", {}, "Title & Message"),
                  h("th", { style: { width: "90px" } }, "Status")
                )
              ),
              h("tbody", {},
                errorsList.map((err) => {
                  return h("tr", {
                    style: { cursor: "pointer" },
                    onclick: (e) => {
                      if (e.target.tagName !== "A") {
                        openErrorDrawer(err);
                      }
                    },
                  },
                    h("td", { class: "nowrap muted small" }, fmt.time(err.ts)),
                    h("td", {},
                      h("a", {
                        class: "link",
                        href: `#/runs/${err.run_id}`,
                        style: { fontWeight: 500 },
                        onclick: (e) => e.stopPropagation(),
                      }, err.run_label || err.book_name || err.run_id.slice(0, 8)),
                      err.book_name ? h("div", { class: "faint small" }, err.book_name) : null
                    ),
                    h("td", { class: "mono small muted" }, err.chunk !== null && err.chunk !== undefined ? `#${err.chunk}` : "—"),
                    h("td", { class: "muted small" }, err.stage || "—"),
                    h("td", {}, severityBadge(err.severity)),
                    h("td", { class: "mono small", style: { fontWeight: 500 } }, err.code),
                    h("td", {},
                      h("div", { style: { fontWeight: 500 } }, err.title),
                      h("div", { class: "muted small ellipsis", style: { maxWidth: "420px" } }, err.message)
                    ),
                    h("td", {},
                      err.resolved
                        ? h("span", { class: "badge green" }, "resolved")
                        : h("span", { class: "badge amber" }, "open")
                    )
                  );
                })
              )
            )
          )
    );

    const headEl = h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, "Errors & alerts"),
        h("p", { class: "sub" }, "Global log of warnings, pipeline errors, and API faults across all extraction runs.")
      ),
      h("div", { class: "row" },
        h("button", {
          class: "btn btn-sm",
          onclick: () => { state.loading = true; renderView(); load(); },
        }, "Refresh")
      )
    );

    clear(root,
      h("div", { class: "page" },
        headEl,
        banner,
        summaryList.length > 0 ? summaryCards : null,
        filterBar,
        occurrencesTable
      )
    );
  }

  // Live refresh debounced when events have level ERROR or CRITICAL
  const debouncedRefresh = debounce(() => {
    load();
  }, 1000);

  const unsub = live.on("events", (events) => {
    const hasError = events.some((ev) => ev.level === "ERROR" || ev.level === "CRITICAL");
    if (hasError) {
      debouncedRefresh();
    }
  });

  await load();

  return () => {
    unsub();
  };
}
