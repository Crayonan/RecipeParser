// Prompts view (#/prompts): edit, diff, and inspect pipeline prompt templates.
import { h, clear, icon, toast, diffView, debounce, confirmDialog } from "../ui.js";
import { api } from "../api.js";

export async function render(root, params, query) {
  let prompts = [];
  let selectedName = null;
  let activeTab = "edit"; // "edit" | "diff" | "default"
  let editBuffer = "";
  let isDirty = false;
  let problems = [];
  let validating = false;
  let saving = false;

  clear(root, h("div", { class: "page-head" },
    h("div", {},
      h("h1", {}, "Prompts"),
      h("div", { class: "sub" }, "Inspect and customize system prompts used across extraction stages.")
    )
  ), h("div", { class: "empty faint" }, "Loading prompts…"));

  try {
    const res = await api.get("/api/prompts");
    prompts = (res && res.prompts) || [];
  } catch (err) {
    clear(root, h("div", { class: "alert red" },
      icon("alert"),
      h("div", {},
        h("div", { class: "alert-title" }, "Failed to load prompts"),
        h("div", { class: "mono small" }, err.message || String(err))
      )
    ));
    return;
  }

  // Pre-select first prompt
  if (prompts.length > 0) {
    selectedName = prompts[0].name;
    initBuffer(prompts[0]);
  }

  function initBuffer(p) {
    editBuffer = p.override !== null && p.override !== undefined ? p.override : (p.default || "");
    isDirty = false;
    problems = [];
  }

  const debouncedValidate = debounce(async (name, template) => {
    validating = true;
    updateStatusIndicators();
    try {
      const res = await api.post(`/api/prompts/${encodeURIComponent(name)}/validate`, { template });
      problems = (res && res.problems) || [];
    } catch (e) {
      problems = [e.message || "Validation failed"];
    } finally {
      validating = false;
      renderValidationAndSaveState();
    }
  }, 350);

  function switchPrompt(name) {
    if (name === selectedName) return;
    if (isDirty) {
      const confirmed = confirmDialog("You have unsaved changes in the current prompt. Discard them?");
      if (!confirmed) return;
    }
    selectedName = name;
    const p = prompts.find((item) => item.name === name);
    if (p) initBuffer(p);
    renderView();
  }

  function insertPlaceholder(name) {
    const textarea = root.querySelector("textarea.prompt-editor");
    const tag = `{{${name}}}`;
    if (!textarea) {
      editBuffer += tag;
      isDirty = true;
      renderView();
      return;
    }
    const start = textarea.selectionStart ?? editBuffer.length;
    const end = textarea.selectionEnd ?? editBuffer.length;
    const before = editBuffer.substring(0, start);
    const after = editBuffer.substring(end);
    editBuffer = before + tag + after;
    isDirty = true;
    textarea.value = editBuffer;
    textarea.focus();
    const newPos = start + tag.length;
    textarea.setSelectionRange(newPos, newPos);

    debouncedValidate(selectedName, editBuffer);
    updateStatusIndicators();
  }

  function updateStatusIndicators() {
    const saveBtn = root.querySelector(".btn-save-prompt");
    if (saveBtn) {
      saveBtn.disabled = saving || problems.length > 0 || !isDirty;
    }
  }

  function renderValidationAndSaveState() {
    const container = root.querySelector(".prompt-problems-box");
    if (container) {
      clear(container);
      if (problems.length > 0) {
        container.appendChild(
          h("div", { class: "alert red small col", style: "gap: 4px;" },
            problems.map((prob) =>
              h("div", { class: "row", style: "gap: 6px; align-items: flex-start;" },
                icon("alert"),
                h("span", {}, prob)
              )
            )
          )
        );
      }
    }
    updateStatusIndicators();
  }

  function renderView() {
    clear(root);

    // Page Header
    const head = h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, "Prompts"),
        h("div", { class: "sub" }, "Inspect and customize system prompts used across extraction stages.")
      )
    );

    // Callout note: Edits apply to runs started after saving; each run records prompts; offline replay ignores edits.
    const note = h("div", { class: "alert blue", style: "margin-bottom: 20px;" },
      icon("info"),
      h("div", { class: "small" },
        h("div", { style: "font-weight: 600;" }, "Prompt customization rules"),
        h("div", { class: "muted mt-8" },
          "Edits apply to runs started after saving. Each run permanently records the exact prompt snapshot it executed. Offline replays strictly ignore edited prompts to reproduce original run outputs."
        )
      )
    );

    // Left List Column
    const leftList = h("div", { style: "width: 320px; flex: none; display: flex; flex-direction: column; gap: 8px;" },
      h("div", { class: "small muted", style: "font-weight: 600; padding: 0 4px;" }, "Pipeline Prompts"),
      h("div", { class: "col", style: "gap: 6px;" },
        prompts.map((p) => {
          const isSelected = p.name === selectedName;
          const hasOverride = p.override !== null && p.override !== undefined;

          return h("div", {
            class: `option-card ${isSelected ? "on" : ""}`,
            style: "padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; cursor: pointer;",
            onclick: () => switchPrompt(p.name),
          },
            h("div", { class: "row between", style: "width: 100%;" },
              h("span", { style: "font-weight: 600; font-size: 13.5px;" }, p.title || p.name),
              hasOverride
                ? h("span", { class: "badge amber" }, "Edited")
                : h("span", { class: "badge outline faint" }, "Default")
            ),
            h("div", { class: "row wrap", style: "gap: 6px; align-items: center;" },
              h("code", { class: "mono faint", style: "font-size: 11px;" }, p.name),
              p.stage ? h("span", { class: "badge blue", style: "font-size: 11px; height: 18px;" }, p.stage) : null
            )
          );
        })
      )
    );

    // Right Editor Column
    const currentPrompt = prompts.find((p) => p.name === selectedName);
    const rightCol = h("div", { style: "flex: 1; min-width: 0;" },
      currentPrompt ? renderEditor(currentPrompt) : h("div", { class: "card empty" }, "Select a prompt")
    );

    const layout = h("div", { style: "display: flex; gap: 24px; align-items: flex-start;" },
      leftList,
      rightCol
    );

    root.appendChild(head);
    root.appendChild(note);
    root.appendChild(layout);
  }

  function renderEditor(p) {
    const hasOverride = p.override !== null && p.override !== undefined;
    const defaultText = p.default || "";

    const card = h("div", { class: "card" });

    // Card Header
    const cardHead = h("div", { class: "card-head" },
      h("div", { class: "row wrap", style: "gap: 10px; align-items: baseline;" },
        h("h3", {}, p.title || p.name),
        h("code", { class: "mono faint" }, p.name),
        p.stage ? h("span", { class: "badge blue" }, p.stage) : null,
        hasOverride ? h("span", { class: "badge amber" }, "Customized") : null
      ),
      // Seg view toggle: Edit / Diff vs default / Default
      h("div", { class: "seg" },
        h("button", {
          class: activeTab === "edit" ? "on" : "",
          onclick: () => { activeTab = "edit"; renderView(); },
        }, "Edit"),
        h("button", {
          class: activeTab === "diff" ? "on" : "",
          onclick: () => { activeTab = "diff"; renderView(); },
        }, "Diff vs default"),
        h("button", {
          class: activeTab === "default" ? "on" : "",
          onclick: () => { activeTab = "default"; renderView(); },
        }, "Default")
      )
    );
    card.appendChild(cardHead);

    const body = h("div", { class: "card-body col", style: "gap: 16px;" });

    // Description
    if (p.description) {
      body.appendChild(h("div", { class: "small muted" }, p.description));
    }

    // Placeholder chips
    const placeholders = p.placeholders || [];
    const requiredSet = new Set(p.required || []);
    if (placeholders.length > 0) {
      const chipRow = h("div", { class: "row wrap", style: "gap: 6px; align-items: center;" },
        h("span", { class: "small faint" }, "Placeholders:"),
        placeholders.map((ph) => {
          const isReq = requiredSet.has(ph);
          return h("button", {
            class: "chip",
            type: "button",
            title: isReq ? `{{${ph}}} (required) — click to insert` : `{{${ph}}} — click to insert`,
            onclick: () => insertPlaceholder(ph),
          },
            `{{${ph}}}`,
            isReq ? h("span", { style: "color: var(--danger); margin-left: 3px; font-weight: bold;" }, "*") : null
          );
        })
      );
      body.appendChild(chipRow);
    }

    // View contents depending on tab
    if (activeTab === "edit") {
      // Monospace textarea editor
      const textarea = h("textarea", {
        class: "input code prompt-editor",
        style: "min-height: 380px; font-size: 13px; line-height: 1.5; resize: vertical;",
        value: editBuffer,
        oninput: (e) => {
          editBuffer = e.target.value;
          isDirty = true;
          debouncedValidate(p.name, editBuffer);
          updateStatusIndicators();
        },
      });
      body.appendChild(textarea);

      // Problems box
      const probBox = h("div", { class: "prompt-problems-box" });
      if (problems.length > 0) {
        probBox.appendChild(
          h("div", { class: "alert red small col", style: "gap: 4px;" },
            problems.map((prob) =>
              h("div", { class: "row", style: "gap: 6px; align-items: flex-start;" },
                icon("alert"),
                h("span", {}, prob)
              )
            )
          )
        );
      }
      body.appendChild(probBox);

      // Action buttons: Save, Reset to default
      const actions = h("div", { class: "row between", style: "align-items: center; margin-top: 8px;" },
        h("div", { class: "row", style: "gap: 8px;" },
          h("button", {
            class: "btn btn-sm btn-primary btn-save-prompt",
            disabled: saving || problems.length > 0 || !isDirty,
            onclick: async () => {
              saving = true;
              updateStatusIndicators();
              try {
                const res = await api.put(`/api/prompts/${encodeURIComponent(p.name)}`, { template: editBuffer });
                prompts = (res && res.prompts) || [];
                const updated = prompts.find((x) => x.name === p.name);
                if (updated) initBuffer(updated);
                toast("Prompt saved", `Prompt ${p.title || p.name} updated.`, "green");
                renderView();
              } catch (err) {
                toast("Save failed", err.message || "Failed to save prompt", "red");
              } finally {
                saving = false;
                renderView();
              }
            },
          }, saving ? "Saving…" : "Save prompt"),
          isDirty ? h("span", { class: "badge amber" }, "Unsaved changes") : null
        ),
        hasOverride
          ? h("button", {
              class: "btn btn-sm btn-danger",
              type: "button",
              onclick: async () => {
                const ok = confirmDialog(`Reset "${p.title || p.name}" to built-in default? Custom override will be removed.`);
                if (!ok) return;
                try {
                  const res = await api.del(`/api/prompts/${encodeURIComponent(p.name)}`);
                  prompts = (res && res.prompts) || [];
                  const updated = prompts.find((x) => x.name === p.name);
                  if (updated) initBuffer(updated);
                  toast("Prompt reset", "Restored built-in default.", "green");
                  renderView();
                } catch (err) {
                  toast("Reset failed", err.message || "Failed to reset prompt", "red");
                }
              },
            }, icon("trash"), "Reset to default")
          : null
      );
      body.appendChild(actions);

    } else if (activeTab === "diff") {
      // Diff vs default (diffView)
      const diffContainer = h("div", {},
        h("div", { class: "small muted mb-16" }, "Comparing current editor template with built-in default:"),
        diffView(defaultText, editBuffer)
      );
      body.appendChild(diffContainer);

    } else if (activeTab === "default") {
      // Read-only default template
      const defContainer = h("div", {},
        h("div", { class: "small muted mb-16" }, "Built-in default template:"),
        h("pre", { class: "block", style: "max-height: 480px; font-size: 13px;" }, defaultText)
      );
      body.appendChild(defContainer);
    }

    // Prompt History section
    const historyList = p.history || [];
    if (historyList.length > 0) {
      const histSection = h("div", { style: "margin-top: 16px; border-top: 1px solid var(--border); padding-top: 16px;" },
        h("div", { class: "small muted mb-16", style: "font-weight: 600;" }, "Version History"),
        h("div", { class: "col", style: "gap: 8px;" },
          historyList.map((hItem) => {
            return h("div", {
              class: "card-pad row between wrap",
              style: "background: var(--bg-subtle); border: 1px solid var(--border); border-radius: var(--radius); gap: 10px;",
            },
              h("div", { class: "col", style: "gap: 4px;" },
                h("div", { class: "row", style: "gap: 8px; align-items: center;" },
                  h("span", { class: "badge outline faint small" }, hItem.action || "saved"),
                  h("span", { class: "small muted" }, hItem.saved_at || "")
                )
              ),
              h("div", { class: "row", style: "gap: 8px;" },
                h("button", {
                  class: "btn btn-xs btn-ghost",
                  onclick: () => {
                    // View diff against this historical version
                    activeTab = "diff";
                    editBuffer = hItem.template || "";
                    isDirty = true;
                    renderView();
                  },
                }, "View diff"),
                h("button", {
                  class: "btn btn-xs btn-primary",
                  onclick: () => {
                    editBuffer = hItem.template || "";
                    isDirty = true;
                    activeTab = "edit";
                    debouncedValidate(p.name, editBuffer);
                    toast("Restored into editor", "Click Save to apply this version.", "blue");
                    renderView();
                  },
                }, "Restore into editor")
              )
            );
          })
        )
      );
      body.appendChild(histSection);
    }

    card.appendChild(body);
    return card;
  }

  renderView();

  return () => {
    // Cleanup
    isDirty = false;
    problems = [];
  };
}
