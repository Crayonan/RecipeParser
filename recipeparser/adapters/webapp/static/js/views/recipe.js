// #/recipes/:id — Recipe Viewer and Recipe Lab
// Features: readable view, AI judge & suggestions, refine proposals, manual editing, pictures gallery, revision history.
import { h, clear, icon, fmt, severityBadge, diffView, openDrawer, closeDrawer, confirmDialog } from "../ui.js";
import { api } from "../api.js";

const TARGETS = [
  { value: "all", label: "Entire recipe (all)" },
  { value: "title", label: "Title" },
  { value: "description", label: "Description" },
  { value: "servings", label: "Servings" },
  { value: "times", label: "Prep & cook times" },
  { value: "ingredients", label: "Ingredients" },
  { value: "directions", label: "Directions" },
  { value: "notes", label: "Notes" },
];

const DRAFT_FIELDS = [
  { key: "title", label: "Title", type: "scalar" },
  { key: "description", label: "Description", type: "scalar" },
  { key: "servings", label: "Servings", type: "scalar" },
  { key: "prep_time", label: "Prep time", type: "scalar" },
  { key: "cook_time", label: "Cook time", type: "scalar" },
  { key: "ingredients", label: "Ingredients", type: "list" },
  { key: "directions", label: "Directions", type: "list" },
  { key: "notes", label: "Notes", type: "scalar" },
];

export async function render(root, params, query) {
  const recipeId = params.id;
  let lab = null;
  let isEditing = false;
  // The manual edit form's text, kept across re-renders (judge, filters, failed saves) until saved or cancelled.
  let editBuf = null;
  let inFlight = null; // string identifier of currently running async action, or null
  let hideResolvedSuggestions = true;

  // Refine form state
  let refineScope = "all";
  let refineInstruction = "";

  clear(root, h("div", { class: "card empty col", style: { padding: "60px 20px" } },
    h("div", { class: "spinner", style: { margin: "0 auto 12px", width: "24px", height: "24px" } }),
    h("span", { class: "faint" }, "Loading recipe lab…")
  ));

  try {
    lab = await api.get(`/api/recipes/${recipeId}/lab`);
  } catch (err) {
    clear(root,
      h("div", { class: "page-head" },
        h("a", { class: "btn btn-sm btn-ghost", href: "#/recipes" }, h("span", { style: { display: "inline-flex", transform: "rotate(180deg)" } }, icon("chevron")), "Back to library")
      ),
      h("div", { class: "alert red" },
        icon("alert"),
        h("div", {},
          h("div", { class: "alert-title" }, "Failed to load recipe"),
          h("div", { class: "small mono" }, err.message || String(err))
        )
      )
    );
    return;
  }

  function setInFlight(val) {
    inFlight = val;
    renderView();
  }

  function renderView() {
    if (!lab) return;

    // Synchronize topbar title
    const topbarTitle = document.getElementById("topbar-title");
    if (topbarTitle) {
      topbarTitle.textContent = lab.current?.title || "Recipe Lab";
    }

    // Top action bar
    const headRow = h("div", { class: "page-head" },
      h("div", { class: "row wrap", style: { gap: "10px", minWidth: 0, flex: 1 } },
        h("a", { class: "btn btn-sm btn-ghost", href: "#/recipes" }, h("span", { style: { display: "inline-flex", transform: "rotate(180deg)" } }, icon("chevron")), "Library"),
        h("a", { class: "btn btn-sm btn-ghost", href: `#/runs/${lab.recipe.run_id}`, title: lab.recipe.run_label || lab.recipe.book_name || "Run", style: { maxWidth: "420px", minWidth: 0 } },
          icon("flag"),
          h("span", { class: "ellipsis" }, lab.recipe.run_label || lab.recipe.book_name || "Run")
        ),
        lab.revision > 0
          ? h("span", { class: "badge purple", title: `Revision ${lab.revision}` }, `rev ${lab.revision}`)
          : null,
        lab.recipe.needs_review
          ? h("span", { class: "badge amber" }, icon("alert"), "Needs review")
          : null
      ),
      h("div", { class: "row", style: { gap: "8px" } },
        h("a", {
          class: "btn btn-sm btn-ghost",
          href: `/api/recipes/${recipeId}/export`,
          download: "",
          title: "Download recipe as JSON"
        }, icon("download"), "Export"),
        h("button", {
          class: `btn btn-sm ${isEditing ? "btn-ghost" : "btn-primary"}`,
          disabled: Boolean(inFlight),
          onclick: () => {
            isEditing = !isEditing;
            editBuf = null;
            renderView();
          }
        }, isEditing ? "Cancel edit" : "Edit recipe")
      )
    );

    // Build Main Column & Side Column
    const mainCol = h("div", { class: "recipe-main-col" });
    const sideCol = h("div", { class: "recipe-side-col" });

    // 1. Pending Proposal Banner (if pending)
    if (lab.pending) {
      mainCol.append(renderProposalPanel(lab.pending));
    }

    // 2. Readable Recipe View or Manual Edit Form
    if (isEditing) {
      mainCol.append(renderEditForm());
    } else {
      mainCol.append(renderRecipeSheet());
    }

    // 3. Side Column: Judge, Refine, Pictures, History
    sideCol.append(
      renderJudgeCard(),
      renderRefineCard(),
      renderPicturesCard(),
      renderHistoryCard()
    );

    const layout = h("div", { class: "recipe-layout" }, mainCol, sideCol);
    clear(root, headRow, layout);
  }

  // ── PROPOSAL PANEL ──────────────────────────────────────────────────────────
  function renderProposalPanel(pending) {
    const current = lab.current || {};
    const proposed = pending.data || {};
    const diffItems = [];

    for (const f of DRAFT_FIELDS) {
      if (f.type === "list") {
        const curLines = (current[f.key] || []).map((s) => String(s).trim()).join("\n");
        const propLines = (proposed[f.key] || []).map((s) => String(s).trim()).join("\n");
        if (curLines !== propLines) {
          diffItems.push(h("div", {},
            h("div", { class: "proposal-field-title" }, f.label),
            diffView(curLines, propLines)
          ));
        }
      } else {
        const curVal = (current[f.key] ?? "").toString().trim();
        const propVal = (proposed[f.key] ?? "").toString().trim();
        if (curVal !== propVal) {
          diffItems.push(h("div", {},
            h("div", { class: "proposal-field-title" }, f.label),
            h("div", { class: "diff" },
              h("div", { class: "del" }, `- ${curVal || "(empty)"}`),
              h("div", { class: "add" }, `+ ${propVal || "(empty)"}`)
            )
          ));
        }
      }
    }

    const isAccepting = inFlight === "accept_proposal";
    const isDiscarding = inFlight === "discard_proposal";

    return h("div", { class: "proposal-panel" },
      h("div", { class: "proposal-panel-head" },
        h("div", { class: "row", style: { gap: "8px" } },
          icon("sparkles"),
          h("b", {}, "Pending Revision Proposal"),
          pending.source ? h("span", { class: "badge purple" }, pending.source) : null
        ),
        h("span", { class: "small faint" }, fmt.ago(pending.ts))
      ),
      h("div", { class: "proposal-panel-body" },
        pending.summary ? h("div", { class: "small", style: { fontWeight: 500 } }, pending.summary) : null,
        pending.instruction
          ? h("div", { class: "small muted" }, h("b", {}, "Instruction: "), pending.instruction)
          : null,
        diffItems.length
          ? h("div", { class: "col", style: { gap: "12px", marginTop: "4px" } }, ...diffItems)
          : h("div", { class: "small faint" }, "No textual field diffs detected."),
        h("div", { class: "row mt-8", style: { gap: "10px" } },
          h("button", {
            class: "btn btn-sm btn-primary",
            disabled: Boolean(inFlight),
            onclick: async () => {
              setInFlight("accept_proposal");
              try {
                lab = await api.post(`/api/recipes/${recipeId}/revisions/${pending.id}/accept`);
              } catch {} finally {
                setInFlight(null);
              }
            }
          }, isAccepting ? [h("span", { class: "spinner" }), "Accepting…"] : [icon("check"), "Accept proposal"]),
          h("button", {
            class: "btn btn-sm btn-danger",
            disabled: Boolean(inFlight),
            onclick: async () => {
              setInFlight("discard_proposal");
              try {
                lab = await api.post(`/api/recipes/${recipeId}/revisions/${pending.id}/discard`);
              } catch {} finally {
                setInFlight(null);
              }
            }
          }, isDiscarding ? [h("span", { class: "spinner" }), "Discarding…"] : [icon("x"), "Discard"])
        )
      )
    );
  }

  // ── READABLE RECIPE SHEET ──────────────────────────────────────────────────
  function renderRecipeSheet() {
    const current = lab.current || {};
    const coverPic = (lab.pictures || []).find((p) => p.cover) ||
      (lab.pictures && lab.pictures.length ? lab.pictures[0] : null);
    const coverUrl = coverPic ? coverPic.url : lab.recipe.cover_url;

    // Meta chips
    const chips = [];
    if (current.servings) {
      chips.push(h("span", { class: "badge outline" }, icon("utensils"), current.servings));
    }
    if (current.prep_time) {
      chips.push(h("span", { class: "badge outline" }, icon("clock"), `Prep: ${current.prep_time}`));
    }
    if (current.cook_time) {
      chips.push(h("span", { class: "badge outline" }, icon("clock"), `Cook: ${current.cook_time}`));
    }
    const sourceLabel = lab.recipe.source || lab.recipe.book_name;
    if (sourceLabel) {
      chips.push(h("span", { class: "badge outline" }, icon("book"), sourceLabel));
    }
    for (const cat of lab.recipe.categories || []) {
      chips.push(h("span", { class: "badge faint" }, cat));
    }

    // Extraction review notes (compact)
    let reviewEl = null;
    const reviewNotes = lab.recipe.review || [];
    if (reviewNotes.length > 0) {
      reviewEl = h("div", { class: "alert", style: { padding: "10px 14px", gap: "10px" } },
        icon("alert"),
        h("div", { style: { flex: 1, minWidth: 0 } },
          h("div", { class: "small", style: { fontWeight: 600, marginBottom: "4px" } },
            `Extraction notes (${reviewNotes.length})`
          ),
          h("ul", { style: { margin: 0, paddingLeft: "18px", fontSize: "12.5px" }, class: "col" },
            reviewNotes.map((n) => h("li", {
              style: { color: n.kind === "needs_review" ? "var(--warning)" : "var(--text-2)" }
            }, n.text))
          )
        )
      );
    }

    // Directions: strip leading digits and dot for display only
    const directionsList = (current.directions || []).map((step) => {
      const cleanStep = String(step).replace(/^\d+\.\s+/, "");
      return h("li", {}, cleanStep);
    });

    const ingredientsList = (current.ingredients || []).map((ing) => h("li", {}, ing));

    return h("div", { class: "card recipe-sheet" },
      coverUrl
        ? h("div", { class: "recipe-hero-wrap" },
            h("img", { src: coverUrl, alt: current.title, class: "recipe-hero-img" })
          )
        : null,
      h("div", { class: "recipe-sheet-body" },
        h("div", { class: "recipe-header" },
          h("h1", { class: "recipe-sheet-title" }, current.title),
          current.description
            ? h("p", { class: "recipe-sheet-desc" }, current.description)
            : null,
          chips.length ? h("div", { class: "recipe-chips-bar" }, ...chips) : null
        ),
        reviewEl,
        h("div", {},
          h("h3", { style: { marginBottom: "12px" } }, `Ingredients (${ingredientsList.length})`),
          ingredientsList.length
            ? h("ul", { class: "recipe-ingredients-list" }, ...ingredientsList)
            : h("div", { class: "faint small" }, "No ingredients listed.")
        ),
        h("div", {},
          h("h3", { style: { marginBottom: "12px" } }, `Directions (${directionsList.length})`),
          directionsList.length
            ? h("ol", { class: "recipe-directions-list" }, ...directionsList)
            : h("div", { class: "faint small" }, "No directions listed.")
        ),
        current.notes
          ? h("div", {},
              h("h3", { style: { marginBottom: "8px" } }, "Notes"),
              h("div", { class: "recipe-notes-box" }, current.notes)
            )
          : null
      )
    );
  }

  // ── MANUAL EDIT FORM ────────────────────────────────────────────────────────
  function renderEditForm() {
    const cur = lab.current || {};
    if (!editBuf) {
      editBuf = {
        title: cur.title || "", description: cur.description || "", servings: cur.servings || "",
        prep_time: cur.prep_time || "", cook_time: cur.cook_time || "",
        ingredients: (cur.ingredients || []).join("\n"), directions: (cur.directions || []).join("\n"),
        notes: cur.notes || "", summary: "",
      };
    }
    // Every field reads from and writes to editBuf, so a re-render shows what was typed.
    const bound = (tag, key, attrs = {}) => {
      const el = h(tag, { ...attrs, oninput: (e) => { editBuf[key] = e.target.value; } });
      el.value = editBuf[key];
      return el;
    };

    const titleInput = bound("input", "title", { class: "input" });
    const descInput = bound("textarea", "description", { class: "input", rows: "2" });
    const servingsInput = bound("input", "servings", { class: "input", placeholder: "e.g. 4 servings" });
    const prepInput = bound("input", "prep_time", { class: "input", placeholder: "e.g. 15 min" });
    const cookInput = bound("input", "cook_time", { class: "input", placeholder: "e.g. 30 min" });
    const ingsInput = bound("textarea", "ingredients", { class: "input", rows: "8", style: { fontFamily: "var(--font)" } });
    const dirsInput = bound("textarea", "directions", { class: "input", rows: "10", style: { fontFamily: "var(--font)" } });
    const notesInput = bound("textarea", "notes", { class: "input", rows: "3" });
    const summaryInput = bound("input", "summary", { class: "input", placeholder: "e.g. Fixed seasoning and servings" });

    const isSaving = inFlight === "save_edit";

    async function saveEdit() {
      const data = {
        title: titleInput.value.trim() || cur.title,
        description: descInput.value.trim() || null,
        servings: servingsInput.value.trim() || null,
        prep_time: prepInput.value.trim() || null,
        cook_time: cookInput.value.trim() || null,
        ingredients: ingsInput.value.split("\n").map((s) => s.trim()).filter(Boolean),
        directions: dirsInput.value.split("\n").map((s) => s.trim()).filter(Boolean),
        notes: notesInput.value.trim() || null,
      };

      setInFlight("save_edit");
      try {
        lab = await api.post(`/api/recipes/${recipeId}/revisions`, {
          data,
          summary: summaryInput.value.trim() || null,
        });
        isEditing = false;
        editBuf = null;
      } catch {} finally {
        setInFlight(null);
      }
    }

    return h("div", { class: "card", style: { padding: "24px" } },
      h("div", { class: "card-head", style: { margin: "-24px -24px 20px -24px" } },
        h("h3", {}, `Edit Draft (Revision ${lab.revision})`),
        h("span", { class: "small faint" }, "Edits create a new accepted revision")
      ),
      h("div", { class: "col", style: { gap: "16px" } },
        h("label", { class: "field" },
          h("span", { class: "field-label" }, "Title"),
          titleInput
        ),
        h("label", { class: "field" },
          h("span", { class: "field-label" }, "Description"),
          descInput
        ),
        h("div", { class: "grid cols-3" },
          h("label", { class: "field" },
            h("span", { class: "field-label" }, "Servings"),
            servingsInput
          ),
          h("label", { class: "field" },
            h("span", { class: "field-label" }, "Prep time"),
            prepInput
          ),
          h("label", { class: "field" },
            h("span", { class: "field-label" }, "Cook time"),
            cookInput
          )
        ),
        h("label", { class: "field" },
          h("span", { class: "field-label" }, "Ingredients (one per line)"),
          ingsInput
        ),
        h("label", { class: "field" },
          h("span", { class: "field-label" }, "Directions (one per line)"),
          dirsInput
        ),
        h("label", { class: "field" },
          h("span", { class: "field-label" }, "Notes"),
          notesInput
        ),
        h("label", { class: "field" },
          h("span", { class: "field-label" }, "Change summary (optional)"),
          summaryInput
        ),
        h("div", { class: "row mt-8", style: { gap: "10px" } },
          h("button", {
            class: "btn btn-primary btn-sm",
            disabled: Boolean(inFlight),
            onclick: saveEdit
          }, isSaving ? [h("span", { class: "spinner" }), "Saving revision…"] : [icon("check"), "Save revision"]),
          h("button", {
            class: "btn btn-ghost btn-sm",
            disabled: Boolean(inFlight),
            onclick: () => {
              isEditing = false;
              editBuf = null;
              renderView();
            }
          }, "Cancel")
        )
      )
    );
  }

  // ── SIDE CARD: JUDGE ────────────────────────────────────────────────────────
  function renderJudgeCard() {
    const aiAvail = Boolean(lab.ai?.available);
    const j = lab.judgement;
    const isJudging = inFlight === "judge";

    let scoreColorClass = "score-amber";
    if (j) {
      if (j.overall >= 8) scoreColorClass = "score-green";
      else if (j.overall < 5) scoreColorClass = "score-red";
    }

    const suggestions = (j?.suggestions || []).slice();
    // Sort suggestions by severity (high > medium > low), then state (open > proposed > applied/dismissed)
    const sevWeight = { high: 0, medium: 1, low: 2 };
    const stateWeight = { open: 0, proposed: 1, applied: 2, dismissed: 2 };
    suggestions.sort((a, b) => {
      const swA = stateWeight[a.state] ?? 1;
      const swB = stateWeight[b.state] ?? 1;
      if (swA !== swB) return swA - swB;
      const rA = sevWeight[a.severity] ?? 1;
      const rB = sevWeight[b.severity] ?? 1;
      return rA - rB;
    });

    const filteredSuggestions = hideResolvedSuggestions
      ? suggestions.filter((s) => s.state !== "applied" && s.state !== "dismissed")
      : suggestions;

    return h("div", { class: "card" },
      h("div", { class: "card-head" },
        h("h3", {}, "AI Judge & Suggestions"),
        j ? h("span", { class: `badge ${j.overall >= 8 ? "green" : j.overall >= 5 ? "amber" : "red"}` }, `${j.overall}/10`) : null
      ),
      h("div", { class: "card-body col", style: { gap: "14px" } },
        !aiAvail
          ? h("div", { class: "alert amber", style: { padding: "8px 12px", fontSize: "12.5px" } },
              icon("alert"),
              h("span", {}, lab.ai?.reason || "AI model is not configured.")
            )
          : null,

        h("button", {
          class: "btn btn-sm btn-primary",
          disabled: !aiAvail || Boolean(inFlight),
          onclick: async () => {
            setInFlight("judge");
            try {
              lab = await api.post(`/api/recipes/${recipeId}/judge`);
            } catch {} finally {
              setInFlight(null);
            }
          }
        }, isJudging
          ? [h("span", { class: "spinner" }), "Judging recipe… (5-30s)"]
          : [icon("sparkles"), j ? "Re-judge recipe" : "Judge recipe"]),

        j
          ? h("div", { class: "col", style: { gap: "12px" } },
              h("div", { class: "judge-score-display" },
                h("span", { class: `judge-overall-val ${scoreColorClass}` }, j.overall),
                h("span", { class: "judge-overall-max" }, "/ 10"),
                j.stale
                  ? h("span", { class: "badge amber", style: { marginLeft: "auto" }, title: "Recipe has changed since this judgement" }, "stale")
                  : null
              ),
              j.summary ? h("p", { class: "small muted" }, j.summary) : null,

              // 4 Axis bars (1-5)
              j.scores
                ? h("div", { class: "judge-axis-grid" },
                    Object.entries(j.scores).map(([k, val]) =>
                      h("div", { class: "judge-axis-row" },
                        h("span", { class: "ellipsis faint", title: k }, k[0].toUpperCase() + k.slice(1)),
                        h("div", { class: "judge-axis-track" },
                          h("div", { class: "judge-axis-bar", style: { width: `${Math.min(100, (Number(val) / 5) * 100)}%` } })
                        ),
                        h("span", { class: "num small faint" }, `${val}/5`)
                      )
                    )
                  )
                : null,

              // Suggestions
              h("div", { class: "row between", style: { marginTop: "8px" } },
                h("b", { class: "small" }, `Suggestions (${filteredSuggestions.length}${hideResolvedSuggestions && filteredSuggestions.length !== suggestions.length ? ` of ${suggestions.length}` : ""})`),
                h("label", { class: "check small faint" },
                  h("input", {
                    type: "checkbox",
                    checked: hideResolvedSuggestions,
                    onchange: (e) => {
                      hideResolvedSuggestions = e.target.checked;
                      renderView();
                    }
                  }),
                  "Hide resolved"
                )
              ),

              filteredSuggestions.length
                ? h("div", { class: "suggestions-list" },
                    filteredSuggestions.map((s) => renderSuggestionItem(s, j.id))
                  )
                : h("div", { class: "small faint" }, suggestions.length ? "All suggestions resolved." : "No suggestions made.")
            )
          : h("div", { class: "small faint" }, "Run the AI judge to score completeness, clarity, consistency, and get suggestions.")
      )
    );
  }

  function renderSuggestionItem(s, judgementId) {
    const isMuted = s.state === "applied" || s.state === "dismissed";
    const isApplying = inFlight === `apply_${s.id}`;
    const isDismissing = inFlight === `dismiss_${s.id}`;
    const isReopening = inFlight === `reopen_${s.id}`;

    const badges = [
      h("span", { class: "badge" }, s.kind === "fix" ? "Fix" : "Tip"),
      h("span", { class: "badge outline faint" }, s.target),
      severityBadge(s.severity),
    ];
    if (s.state === "applied") {
      badges.push(h("span", { class: "badge green" }, "applied"));
    } else if (s.state === "dismissed") {
      badges.push(h("span", { class: "badge" }, "dismissed"));
    } else if (s.state === "proposed") {
      badges.push(h("span", { class: "badge purple" }, "proposed"));
    }

    const actionButtons = [];
    if (s.state === "open") {
      actionButtons.push(
        h("button", {
          class: "btn btn-xs btn-primary",
          disabled: !lab.ai?.available || Boolean(inFlight),
          title: "Generate a refine proposal from this suggestion",
          onclick: async () => {
            setInFlight(`apply_${s.id}`);
            try {
              const target = s.target === "general" ? "all" : s.target;
              const instruction = `${s.issue} — ${s.suggestion}`;
              lab = await api.post(`/api/recipes/${recipeId}/refine`, {
                instruction,
                target,
                suggestion: {
                  judgement_id: judgementId,
                  suggestion_id: s.id,
                },
              });
            } catch {} finally {
              setInFlight(null);
            }
          }
        }, isApplying ? [h("span", { class: "spinner" }), "Applying…"] : [icon("sparkles"), "Apply"]),
        h("button", {
          class: "btn btn-xs btn-ghost",
          disabled: Boolean(inFlight),
          onclick: async () => {
            setInFlight(`dismiss_${s.id}`);
            try {
              lab = await api.post(`/api/recipes/${recipeId}/suggestions/${judgementId}/${s.id}`, { action: "dismiss" });
            } catch {} finally {
              setInFlight(null);
            }
          }
        }, isDismissing ? "Dismissing…" : "Dismiss")
      );
    } else {
      actionButtons.push(
        h("button", {
          class: "btn btn-xs btn-ghost",
          disabled: Boolean(inFlight),
          onclick: async () => {
            setInFlight(`reopen_${s.id}`);
            try {
              lab = await api.post(`/api/recipes/${recipeId}/suggestions/${judgementId}/${s.id}`, { action: "reopen" });
            } catch {} finally {
              setInFlight(null);
            }
          }
        }, isReopening ? "Reopening…" : "Reopen")
      );
    }

    return h("div", { class: `suggestion-item ${isMuted ? "muted-item" : ""}` },
      h("div", { class: "row wrap", style: { gap: "4px" } }, ...badges),
      h("div", { class: "small" }, h("b", {}, "Issue: "), s.issue),
      h("div", { class: "small" }, h("b", {}, "Suggestion: "), s.suggestion),
      s.quote ? h("div", { class: "suggestion-quote" }, `“${s.quote}”`) : null,
      h("div", { class: "row", style: { gap: "6px", marginTop: "4px" } }, ...actionButtons)
    );
  }

  // ── SIDE CARD: REFINE ───────────────────────────────────────────────────────
  function renderRefineCard() {
    const aiAvail = Boolean(lab.ai?.available);
    const isRefining = inFlight === "refine";

    const scopeSelect = h("select", {
      class: "input input-sm",
      onchange: (e) => { refineScope = e.target.value; }
    }, TARGETS.map((t) => h("option", { value: t.value, selected: t.value === refineScope }, t.label)));

    const instrTextarea = h("textarea", {
      class: "input",
      rows: "3",
      placeholder: "e.g. Halve the ingredient quantities and convert baking temperatures to Celsius...",
      oninput: (e) => {
        refineInstruction = e.target.value;
        refineBtn.disabled = !aiAvail || Boolean(inFlight) || !refineInstruction.trim();
      }
    });
    instrTextarea.value = refineInstruction;

    const refineBtn = h("button", {
      class: "btn btn-sm btn-primary",
      disabled: !aiAvail || Boolean(inFlight) || !refineInstruction.trim(),
      onclick: submitRefine
    }, isRefining
      ? [h("span", { class: "spinner" }), "Refining… (5-30s)"]
      : [icon("sparkles"), "Refine"]);

    async function submitRefine() {
      if (!refineInstruction.trim()) return;
      setInFlight("refine");
      try {
        lab = await api.post(`/api/recipes/${recipeId}/refine`, {
          instruction: refineInstruction.trim(),
          target: refineScope,
          suggestion: null,
        });
        refineInstruction = "";
        instrTextarea.value = "";
      } catch {} finally {
        setInFlight(null);
      }
    }

    return h("div", { class: "card" },
      h("div", { class: "card-head" },
        h("h3", {}, "Refine Recipe")
      ),
      h("div", { class: "card-body col", style: { gap: "12px" } },
        h("label", { class: "field" },
          h("span", { class: "field-label small" }, "Target scope"),
          scopeSelect
        ),
        h("label", { class: "field" },
          h("span", { class: "field-label small" }, "Instruction"),
          instrTextarea
        ),
        refineBtn
      )
    );
  }

  // ── SIDE CARD: PICTURES ─────────────────────────────────────────────────────
  function renderPicturesCard() {
    const aiAvail = Boolean(lab.ai?.available);
    const pictures = lab.pictures || [];
    const isGenerating = inFlight === "generate_pic";
    const isUploading = inFlight === "upload_pic";

    const fileInput = h("input", {
      type: "file",
      accept: "image/jpeg,image/png,image/webp,image/*",
      style: { display: "none" },
      onchange: async (e) => {
        const file = e.target.files?.[0];
        if (!file) return;
        setInFlight("upload_pic");
        try {
          lab = await api.upload(`/api/recipes/${recipeId}/pictures`, file);
        } catch {} finally {
          fileInput.value = "";
          setInFlight(null);
        }
      }
    });

    return h("div", { class: "card" },
      h("div", { class: "card-head" },
        h("h3", {}, `Pictures (${pictures.length})`)
      ),
      h("div", { class: "card-body col", style: { gap: "12px" } },
        h("div", { class: "row wrap", style: { gap: "8px" } },
          h("button", {
            class: "btn btn-sm btn-primary",
            disabled: !aiAvail || Boolean(inFlight),
            onclick: async () => {
              setInFlight("generate_pic");
              try {
                lab = await api.post(`/api/recipes/${recipeId}/pictures/generate`);
              } catch {} finally {
                setInFlight(null);
              }
            }
          }, isGenerating ? [h("span", { class: "spinner" }), "Generating… (10-60s)"] : [icon("image"), "Generate picture"]),

          h("button", {
            class: "btn btn-sm btn-ghost",
            disabled: Boolean(inFlight),
            onclick: () => fileInput.click()
          }, isUploading ? [h("span", { class: "spinner" }), "Uploading…"] : [icon("upload"), "Upload"]),
          fileInput
        ),

        pictures.length
          ? h("div", { class: "picture-gallery-grid" },
              pictures.map((p) => {
                return h("div", {
                  class: "picture-gallery-thumb",
                  title: p.prompt || p.kind,
                  onclick: () => openPictureDrawer(p)
                },
                  h("img", { src: p.url, alt: "Recipe thumbnail" }),
                  p.cover ? h("span", { class: "picture-gallery-cover-tag" }, "Cover") : null,
                  h("div", { class: "picture-gallery-actions" },
                    !p.cover
                      ? h("button", {
                          class: "btn btn-xs btn-ghost",
                          style: { color: "#fff", padding: "0 6px", height: "20px" },
                          title: "Set as cover image",
                          onclick: async (e) => {
                            e.stopPropagation();
                            setInFlight(`cover_${p.id}`);
                            try {
                              lab = await api.post(`/api/pictures/${p.id}/cover`);
                            } catch {} finally {
                              setInFlight(null);
                            }
                          }
                        }, "Cover")
                      : h("span", {}),
                    h("button", {
                      class: "icon-btn",
                      style: { color: "#fff", width: "20px", height: "20px" },
                      title: "Delete picture",
                      onclick: async (e) => {
                        e.stopPropagation();
                        if (!confirmDialog("Delete this picture?")) return;
                        setInFlight(`del_pic_${p.id}`);
                        try {
                          lab = await api.del(`/api/pictures/${p.id}`);
                        } catch {} finally {
                          setInFlight(null);
                        }
                      }
                    }, icon("trash"))
                  )
                );
              })
            )
          : h("div", { class: "small faint" }, "No pictures yet. Generate an AI photo or upload one.")
      )
    );
  }

  function openPictureDrawer(p) {
    openDrawer("Recipe Picture", [
      h("img", {
        src: p.url,
        style: { width: "100%", maxHeight: "65vh", objectFit: "contain", borderRadius: "var(--radius)" }
      }),
      h("dl", { class: "kv mt-16" },
        h("dt", {}, "Source"), h("dd", {}, p.kind),
        h("dt", {}, "Created"), h("dd", {}, fmt.datetime(p.ts)),
        p.prompt ? [h("dt", {}, "Prompt"), h("dd", { class: "small muted" }, p.prompt)] : null
      )
    ]);
  }

  // ── SIDE CARD: HISTORY ──────────────────────────────────────────────────────
  function renderHistoryCard() {
    const revisions = lab.revisions || [];
    const currentRev = lab.revision;

    return h("div", { class: "card" },
      h("div", { class: "card-head" },
        h("h3", {}, `History (Rev ${currentRev})`),
        h("span", { class: "small faint" }, `${revisions.length} rev${revisions.length === 1 ? "" : "s"}`)
      ),
      h("div", { class: "card-body col", style: { gap: "10px" } },
        revisions.length
          ? h("div", { class: "revision-history-list" },
              revisions.map((r) => {
                const isCur = r.status === "accepted" && r.rev === currentRev;
                const canRestore = r.status === "accepted" && r.rev !== currentRev;
                const isRestoring = inFlight === `restore_${r.id}`;

                let statusBadgeColor = "";
                if (r.status === "accepted") statusBadgeColor = "green";
                else if (r.status === "pending") statusBadgeColor = "purple";

                return h("div", { class: `revision-history-item ${isCur ? "is-current" : ""}` },
                  h("div", { class: "row between", style: { gap: "6px" } },
                    h("div", { class: "row", style: { gap: "6px" } },
                      h("b", {}, r.rev ? `Rev ${r.rev}` : r.status === "pending" ? "Proposal" : "Proposal (not kept)"),
                      h("span", { class: `badge ${statusBadgeColor}` }, r.status),
                      r.source ? h("span", { class: "badge outline faint" }, r.source) : null
                    ),
                    h("span", { class: "small faint" }, fmt.ago(r.ts))
                  ),
                  h("div", { class: "small muted", style: { overflowWrap: "anywhere" } },
                    r.summary || r.instruction || "—"
                  ),
                  canRestore
                    ? h("div", { class: "row", style: { marginTop: "2px" } },
                        h("button", {
                          class: "btn btn-xs btn-ghost",
                          disabled: Boolean(inFlight),
                          onclick: async () => {
                            if (!confirmDialog(`Restore recipe to revision ${r.rev}?`)) return;
                            setInFlight(`restore_${r.id}`);
                            try {
                              lab = await api.post(`/api/recipes/${recipeId}/revisions/${r.id}/restore`);
                            } catch {} finally {
                              setInFlight(null);
                            }
                          }
                        }, isRestoring ? [h("span", { class: "spinner" }), "Restoring…"] : [icon("restart"), `Restore rev ${r.rev}`])
                      )
                    : null
                );
              })
            )
          : h("div", { class: "small faint" }, "No revisions recorded yet."),

        currentRev > 0 && JSON.stringify(lab.current) !== JSON.stringify(lab.original)
          ? h("button", {
              class: "btn btn-xs btn-ghost",
              style: { alignSelf: "flex-start", marginTop: "4px" },
              disabled: Boolean(inFlight),
              onclick: async () => {
                if (!confirmDialog("Restore the recipe to its original extraction (rev 0)?")) return;
                setInFlight("restore_0");
                try {
                  lab = await api.post(`/api/recipes/${recipeId}/revisions/0/restore`);
                } catch {} finally {
                  setInFlight(null);
                }
              }
            }, inFlight === "restore_0"
              ? [h("span", { class: "spinner" }), "Restoring…"]
              : [icon("restart"), "Restore original extraction (rev 0)"])
          : null
      )
    );
  }

  // Initial render
  renderView();

  return () => {
    // Cleanup on navigate away
  };
}
