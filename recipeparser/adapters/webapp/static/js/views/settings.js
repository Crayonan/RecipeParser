// Settings view (#/settings): configure models, concurrency, keys, and pipeline flags.
import { h, clear, icon, toast } from "../ui.js";
import { api } from "../api.js";

export async function render(root, params, query) {
  let settings = [];
  let meta = null;
  let dirty = new Map(); // key -> new value (or null for reset)
  let problems = {}; // key -> problem string
  let filterText = "";
  let onlyChanged = false;
  let activeGroup = null;
  let expandedDesc = new Set();
  let showSecrets = new Set(); // set of keys currently revealed
  let checkingEmbedding = false;
  let embeddingResult = null; // { verdict, message, dimensions }

  clear(root, h("div", { class: "page-head" },
    h("div", {},
      h("h1", {}, "Settings"),
      h("div", { class: "sub" }, "Configure pipeline behavior, API providers, and execution limits.")
    )
  ), h("div", { class: "empty faint" }, "Loading settings…"));

  try {
    const [settingsRes, metaRes] = await Promise.all([
      api.get("/api/settings"),
      api.get("/api/meta").catch(() => null),
    ]);
    settings = (settingsRes && settingsRes.settings) || [];
    meta = metaRes;
  } catch (err) {
    clear(root, h("div", { class: "alert red" },
      icon("alert"),
      h("div", {},
        h("div", { class: "alert-title" }, "Failed to load settings"),
        h("div", { class: "mono small" }, err.message || String(err))
      )
    ));
    return;
  }

  // Pre-sort or organize groups
  // Requirements: "Provider & models" and "API keys" first.
  function getGroupOrder(groups) {
    const priority = ["Provider & models", "API keys"];
    const ordered = [];
    for (const p of priority) {
      if (groups.includes(p)) ordered.push(p);
    }
    const rest = groups.filter((g) => !priority.includes(g)).sort((a, b) => a.localeCompare(b));
    return [...ordered, ...rest];
  }

  // Live check embedding
  async function checkEmbedding(provider, model) {
    checkingEmbedding = true;
    embeddingResult = null;
    renderView();
    try {
      const res = await api.post("/api/settings/check-embedding", { provider, model });
      embeddingResult = res;
    } catch (e) {
      embeddingResult = { verdict: "rejected", message: e.message || "Failed to check embedding model" };
    } finally {
      checkingEmbedding = false;
      renderView();
    }
  }

  function getEffectiveValue(s) {
    if (dirty.has(s.key)) {
      const v = dirty.get(s.key);
      return v === null ? s.default : v;
    }
    return s.value;
  }

  function isKeyDirty(s) {
    return dirty.has(s.key);
  }

  function isChangedFromDefault(s) {
    if (dirty.has(s.key)) {
      const v = dirty.get(s.key);
      if (v === null) return false;
      return String(v) !== String(s.default ?? "");
    }
    // Consider changed if source is not default or value != default
    return s.source !== "default" || (s.value !== s.default && s.value !== null && s.value !== undefined);
  }

  function renderView() {
    clear(root);

    // Page Header
    const head = h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, "Settings"),
        h("div", { class: "sub" }, "Configure pipeline behavior, API providers, and execution limits.")
      )
    );

    // Top Card: Overview & provider info
    const topCard = renderTopCard();

    // Grouping
    const allGroups = Array.from(new Set(settings.map((s) => s.group || "General")));
    const orderedGroups = getGroupOrder(allGroups);

    // Filter settings
    const q = filterText.trim().toLowerCase();
    const filteredSettings = settings.filter((s) => {
      if (onlyChanged && !isChangedFromDefault(s)) return false;
      if (!q) return true;
      const matchKey = (s.key || "").toLowerCase().includes(q);
      const matchLabel = (s.label || "").toLowerCase().includes(q);
      const matchDesc = (s.description || "").toLowerCase().includes(q);
      return matchKey || matchLabel || matchDesc;
    });

    // Counts per group
    const groupCounts = {};
    for (const g of orderedGroups) {
      groupCounts[g] = settings.filter((s) => (s.group || "General") === g).length;
    }
    const filteredGroupCounts = {};
    for (const g of orderedGroups) {
      filteredGroupCounts[g] = filteredSettings.filter((s) => (s.group || "General") === g).length;
    }

    // Set default activeGroup if not set
    if (!activeGroup || !orderedGroups.includes(activeGroup)) {
      activeGroup = orderedGroups[0] || null;
    }

    // Sidebar navigation + search
    const leftNav = h("div", { style: "width: 240px; flex: none;" },
      h("div", { style: "position: sticky; top: 12px; display: flex; flex-direction: column; gap: 12px;" },
        h("div", { class: "field" },
          h("input", {
            class: "input input-sm",
            type: "search",
            placeholder: "Search settings…",
            value: filterText,
            oninput: (e) => {
              filterText = e.target.value;
              renderView();
            },
          })
        ),
        h("label", { class: "check small muted" },
          h("input", {
            type: "checkbox",
            checked: onlyChanged,
            onchange: (e) => {
              onlyChanged = e.target.checked;
              renderView();
            },
          }),
          "Only changed"
        ),
        h("div", { class: "nav", style: "margin-top: 4px;" },
          orderedGroups.map((g) => {
            const count = q || onlyChanged ? filteredGroupCounts[g] : groupCounts[g];
            const a = h("a", {
              href: `#settings-group-${slugify(g)}`,
              class: (activeGroup === g ? "active" : "") + (count === 0 ? " faint" : ""),
              onclick: (e) => {
                e.preventDefault();
                activeGroup = g;
                const el = document.getElementById(`settings-group-${slugify(g)}`);
                if (el) el.scrollIntoView({ behavior: "smooth" });
                renderNavLinks();
              },
            },
              h("span", { class: "ellipsis" }, g),
              h("span", { class: "count", style: "background: var(--bg-muted); color: var(--text-2);" }, String(count ?? 0))
            );
            return a;
          })
        )
      )
    );

    // Main section
    const mainCol = h("div", { style: "flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 24px;" });

    if (topCard) mainCol.appendChild(topCard);

    let anySectionRendered = false;
    for (const g of orderedGroups) {
      const inGroup = filteredSettings.filter((s) => (s.group || "General") === g);
      if (inGroup.length === 0) continue;
      anySectionRendered = true;
      mainCol.appendChild(renderGroupSection(g, inGroup));
    }

    if (!anySectionRendered) {
      mainCol.appendChild(
        h("div", { class: "card empty" },
          h("h3", {}, "No settings found"),
          h("div", { class: "muted" }, "Try clearing your search or filter.")
        )
      );
    }

    const layout = h("div", { style: "display: flex; gap: 24px; align-items: flex-start;" },
      leftNav,
      mainCol
    );

    root.appendChild(head);
    root.appendChild(layout);

    // Sticky Save Bar
    if (dirty.size > 0) {
      root.appendChild(renderStickyBar());
    }
  }

  function slugify(text) {
    return text.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  }

  function renderNavLinks() {
    // helper to update nav active classes without full rerender
    const links = root.querySelectorAll(".nav a");
    links.forEach((a) => {
      if (a.textContent.startsWith(activeGroup)) a.classList.add("active");
      else a.classList.remove("active");
    });
  }

  function renderTopCard() {
    return h("div", { class: "card" },
      h("div", { class: "card-head" },
        h("h3", {}, "Overview & Model Providers"),
        h("span", { class: "badge blue" }, "Snapshot per run")
      ),
      h("div", { class: "card-body col" },
        h("div", { class: "muted small" },
          "Settings apply to runs started after saving. Each run records its own exact settings snapshot for reproducibility."
        ),
        meta ? renderProvidersSummary(meta) : null
      )
    );
  }

  function renderProvidersSummary(m) {
    const providers = m.providers || [];
    return h("div", { class: "col mt-8" },
      h("div", { style: "display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px;" },
        providers.map((p) => {
          const keyBadge = p.api_key_env
            ? (p.api_key_set
                ? h("span", { class: "badge green", title: `${p.api_key_env} is set` }, "Key configured")
                : h("span", { class: "badge red", title: `${p.api_key_env} is missing` }, "No API key"))
            : h("span", { class: "badge outline faint" }, "Offline / No key");

          const genCount = (p.generation_models || []).length;
          const embCount = (p.embedding_models || []).length;

          return h("div", { class: "card-pad", style: "border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg-subtle);" },
            h("div", { class: "row between mb-16" },
              h("div", { style: "font-weight: 600;" }, p.label || p.id),
              keyBadge
            ),
            p.note ? h("div", { class: "small muted mb-16" }, p.note) : null,
            h("div", { class: "small faint" },
              `${genCount} generation model${genCount === 1 ? "" : "s"} · ${embCount} embedding model${embCount === 1 ? "" : "s"}`
            )
          );
        })
      )
    );
  }

  function renderGroupSection(groupName, items) {
    return h("div", { id: `settings-group-${slugify(groupName)}`, class: "card" },
      h("div", { class: "card-head" },
        h("h3", {}, groupName),
        h("span", { class: "badge outline faint" }, `${items.length} setting${items.length === 1 ? "" : "s"}`)
      ),
      h("div", { class: "card-body col", style: "gap: 0; padding: 0;" },
        items.map((s, idx) => renderSettingRow(s, idx === items.length - 1))
      )
    );
  }

  function renderScopeBadge(scope) {
    // scope: run | service | system | webapp
    switch (scope) {
      case "run":
        return h("span", { class: "badge blue", title: "Applies to extraction pipeline runs" }, "Runs");
      case "service":
        return h("span", { class: "badge outline faint", title: "Cayenne API service setting — not used by web-app runs" }, "Cayenne API service");
      case "system":
        return h("span", { class: "badge outline faint", title: "System runtime setting — not used by web-app runs" }, "System");
      case "webapp":
        return h("span", { class: "badge purple", title: "Web application server setting" }, "Web app");
      default:
        return h("span", { class: "badge outline faint" }, scope || "other");
    }
  }

  function renderSourceBadge(source) {
    // source: default | server env | .env | saved | run
    let cls = "badge outline faint";
    let text = source || "default";
    if (source === "saved") { cls = "badge green"; text = "saved"; }
    else if (source === ".env") { cls = "badge amber"; text = ".env"; }
    else if (source === "server env") { cls = "badge blue"; text = "server env"; }
    else if (source === "run") { cls = "badge purple"; text = "run override"; }
    return h("span", { class: cls, title: `Active source: ${source}` }, text);
  }

  function renderSettingRow(s, isLast) {
    const isDirty = dirty.has(s.key);
    const effVal = getEffectiveValue(s);
    const hasProblem = problems[s.key];
    const isSecret = s.type === "secret" || s.secret;
    const isDescExpanded = expandedDesc.has(s.key);
    const isScopeNonRun = s.scope === "service" || s.scope === "system";

    const row = h("div", {
      class: "setting-row",
      style: `padding: 16px 20px; border-bottom: ${isLast ? "0" : "1px solid var(--border)"}; display: flex; flex-direction: column; gap: 8px; ${isDirty ? "background: var(--bg-subtle);" : ""}`,
    });

    // Top line: Label, mono Key, Badges
    const headRow = h("div", { class: "row wrap between", style: "gap: 8px;" },
      h("div", { class: "row wrap", style: "gap: 8px; align-items: baseline;" },
        h("span", { style: "font-weight: 600; font-size: 14px;" }, s.label || s.key),
        h("code", { class: "mono faint", style: "font-size: 12px;" }, s.key),
        isDirty ? h("span", { class: "badge amber" }, "Unsaved") : null
      ),
      h("div", { class: "row wrap", style: "gap: 6px;" },
        renderScopeBadge(s.scope),
        renderSourceBadge(s.source)
      )
    );
    row.appendChild(headRow);

    // Explanation if service or system
    if (isScopeNonRun) {
      row.appendChild(
        h("div", { class: "small faint", style: "font-style: italic;" },
          `This setting is for the ${s.scope === "service" ? "Cayenne API service" : "host system"} and is not used by web-app runs.`
        )
      );
    }

    // Description clamped or expanded
    if (s.description) {
      const descEl = h("div", {
        class: "small muted",
        style: isDescExpanded
          ? "white-space: pre-wrap; word-break: break-word;"
          : "overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; line-height: 1.4;",
      }, s.description);

      const toggleDesc = h("a", {
        href: "#",
        class: "link small faint",
        style: "display: inline-block; margin-top: -4px;",
        onclick: (e) => {
          e.preventDefault();
          if (isDescExpanded) expandedDesc.delete(s.key);
          else expandedDesc.add(s.key);
          renderView();
        },
      }, isDescExpanded ? "Show less" : "Show more");

      row.appendChild(descEl);
      if (s.description.length > 120 || s.description.includes("\n")) {
        row.appendChild(toggleDesc);
      }
    }

    // Default value indication
    const defaultText = s.default !== null && s.default !== undefined && s.default !== ""
      ? String(s.default)
      : "(empty)";
    const defRow = h("div", { class: "small faint" },
      "Default: ",
      h("code", { class: "mono" }, defaultText)
    );
    row.appendChild(defRow);

    // Input Control Area
    const controlArea = h("div", { class: "row wrap", style: "gap: 12px; align-items: center; margin-top: 4px;" });

    if (!s.editable) {
      // Non-editable rows read-only with explanation
      const roInput = h("input", {
        class: "input input-sm",
        style: "max-width: 320px; background: var(--bg-muted); cursor: not-allowed;",
        disabled: true,
        value: effVal !== null && effVal !== undefined ? String(effVal) : "",
      });
      controlArea.appendChild(roInput);
      controlArea.appendChild(
        h("span", { class: "small faint" }, "Read-only: configured via system environment or non-editable.")
      );
    } else {
      // Editable input by type
      const inputEl = buildInput(s, effVal);
      controlArea.appendChild(inputEl);

      // Per-row 'Reset to default' button
      const canReset = isDirty || (s.source !== "default" && effVal !== s.default);
      if (canReset) {
        const resetBtn = h("button", {
          class: "btn btn-xs btn-ghost",
          title: "Reset this setting to its built-in default",
          onclick: () => {
            dirty.set(s.key, null);
            delete problems[s.key];
            renderView();
          },
        }, icon("trash"), "Reset to default");
        controlArea.appendChild(resetBtn);
      }
    }

    row.appendChild(controlArea);

    // Problem inline message
    if (hasProblem) {
      row.appendChild(
        h("div", { class: "alert red small", style: "padding: 6px 10px; margin-top: 4px;" },
          icon("alert"),
          h("span", {}, String(hasProblem))
        )
      );
    }

    // Special: GEMINI_EMBEDDING_MODEL live check UI
    if (s.key === "GEMINI_EMBEDDING_MODEL") {
      row.appendChild(renderEmbeddingChecker(effVal));
    }

    return row;
  }

  function buildInput(s, effVal) {
    const key = s.key;

    if (s.type === "bool") {
      const isChecked = effVal === true || effVal === "True" || effVal === "true" || effVal === "1";
      return h("label", { class: "check", style: "user-select: none;" },
        h("input", {
          type: "checkbox",
          checked: isChecked,
          onchange: (e) => {
            dirty.set(key, e.target.checked ? "true" : "false");
            delete problems[key];
            renderView();
          },
        }),
        h("span", { class: "small" }, isChecked ? "Enabled" : "Disabled")
      );
    }

    if (s.type === "choice" && Array.isArray(s.choices) && s.choices.length > 0) {
      return h("select", {
        class: `input input-sm ${problems[key] ? "invalid" : ""}`,
        style: "max-width: 320px;",
        onchange: (e) => {
          dirty.set(key, e.target.value);
          delete problems[key];
          renderView();
        },
      },
        s.choices.map((c) =>
          h("option", { value: c, selected: String(effVal) === String(c) }, c)
        )
      );
    }

    if (s.type === "int" || s.type === "float" || s.type === "optional_int") {
      const isFloat = s.type === "float";
      const numInput = h("input", {
        class: `input input-sm ${problems[key] ? "invalid" : ""}`,
        type: "number",
        step: isFloat ? "any" : "1",
        min: s.min !== null && s.min !== undefined ? String(s.min) : undefined,
        max: s.max !== null && s.max !== undefined ? String(s.max) : undefined,
        placeholder: s.type === "optional_int" ? "None / empty" : "",
        style: "max-width: 180px;",
        value: effVal !== null && effVal !== undefined ? String(effVal) : "",
        oninput: (e) => {
          const val = e.target.value.trim();
          dirty.set(key, val === "" ? null : val);
          delete problems[key];
        },
        onchange: () => renderView(),
      });
      return numInput;
    }

    if (s.type === "secret" || s.secret) {
      // Secret password input with show toggle and 'set ••••1234' state + Remove
      const isRevealed = showSecrets.has(key);
      const isSet = s.is_set || (dirty.has(key) && dirty.get(key) !== null && dirty.get(key) !== "");
      const isDirty = dirty.has(key);
      const curVal = isDirty ? (dirty.get(key) ?? "") : "";

      const wrap = h("div", { class: "row wrap", style: "gap: 8px; align-items: center;" });

      const secInput = h("input", {
        class: `input input-sm ${problems[key] ? "invalid" : ""}`,
        type: isRevealed ? "text" : "password",
        placeholder: isSet && !isDirty ? `Configured (${s.value || "••••"})` : "Enter new secret…",
        style: "max-width: 280px;",
        value: curVal,
        oninput: (e) => {
          dirty.set(key, e.target.value);
          delete problems[key];
        },
        onchange: () => renderView(),
      });
      wrap.appendChild(secInput);

      // Show/Hide toggle
      const eyeBtn = h("button", {
        class: "btn btn-xs btn-ghost",
        type: "button",
        title: isRevealed ? "Hide value" : "Reveal input",
        onclick: () => {
          if (isRevealed) showSecrets.delete(key);
          else showSecrets.add(key);
          renderView();
        },
      }, isRevealed ? "Hide" : "Show");
      wrap.appendChild(eyeBtn);

      // Remove button if set
      if (isSet) {
        const removeBtn = h("button", {
          class: "btn btn-xs btn-danger",
          type: "button",
          title: "Clear and remove this secret",
          onclick: () => {
            dirty.set(key, "");
            delete problems[key];
            renderView();
          },
        }, "Remove");
        wrap.appendChild(removeBtn);
      }

      return wrap;
    }

    // Default: string with datalist suggestions if available
    const dlId = `datalist-${slugify(key)}`;
    const strWrap = h("div", { style: "display: inline-flex; align-items: center; gap: 6px; max-width: 360px; width: 100%;" });

    const strInput = h("input", {
      class: `input input-sm ${problems[key] ? "invalid" : ""}`,
      type: "text",
      list: s.suggestions && s.suggestions.length ? dlId : undefined,
      placeholder: s.default ? `e.g. ${s.default}` : "",
      value: effVal !== null && effVal !== undefined ? String(effVal) : "",
      oninput: (e) => {
        dirty.set(key, e.target.value);
        delete problems[key];
      },
      onchange: () => renderView(),
    });
    strWrap.appendChild(strInput);

    if (s.suggestions && s.suggestions.length) {
      const dl = h("datalist", { id: dlId },
        s.suggestions.map((sg) => h("option", { value: sg }))
      );
      strWrap.appendChild(dl);
    }

    return strWrap;
  }

  function renderEmbeddingChecker(currentModel) {
    const effProvider = getEffectiveValue({ key: "RP_PROVIDER", default: "gemini", value: "gemini" }) || "gemini";
    const box = h("div", {
      class: "card-pad mt-8",
      style: "background: var(--bg-subtle); border: 1px solid var(--border); border-radius: var(--radius); display: flex; flex-direction: column; gap: 8px;",
    });

    const head = h("div", { class: "row between", style: "align-items: center;" },
      h("div", { class: "small", style: "font-weight: 600;" }, "Embedding Model Verification (1536 dims required)"),
      h("button", {
        class: "btn btn-xs",
        disabled: checkingEmbedding,
        onclick: () => checkEmbedding(effProvider, currentModel || ""),
      }, checkingEmbedding ? h("span", { class: "spinner" }) : null, "Check embedding model")
    );
    box.appendChild(head);

    if (embeddingResult) {
      let badgeCls = "badge outline faint";
      if (embeddingResult.verdict === "ok") badgeCls = "badge green";
      else if (embeddingResult.verdict === "rejected") badgeCls = "badge red";
      else if (embeddingResult.verdict === "unverified") badgeCls = "badge amber";

      box.appendChild(
        h("div", { class: "row wrap", style: "gap: 8px; align-items: center;" },
          h("span", { class: badgeCls }, embeddingResult.verdict || "unknown"),
          h("span", { class: "small muted" }, embeddingResult.message || "")
        )
      );
    } else {
      box.appendChild(
        h("div", { class: "small faint" },
          "Click to verify whether the configured model outputs 1536 dimensions matching the existing vector store."
        )
      );
    }

    return box;
  }

  function renderStickyBar() {
    const count = dirty.size;
    return h("div", { class: "sticky-bar" },
      h("span", { class: "small muted", style: "margin-right: auto;" },
        `${count} unsaved change${count === 1 ? "" : "s"}`
      ),
      h("button", {
        class: "btn btn-sm btn-ghost",
        onclick: () => {
          dirty.clear();
          problems = {};
          renderView();
        },
      }, "Discard"),
      h("button", {
        class: "btn btn-sm btn-primary",
        onclick: async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          const origText = btn.textContent;
          btn.textContent = "Saving…";
          try {
            const payload = { values: {} };
            for (const [k, v] of dirty.entries()) {
              payload.values[k] = v;
            }
            const res = await api.put("/api/settings", payload);
            settings = (res && res.settings) || [];
            dirty.clear();
            problems = {};
            toast("Settings saved", "Changes will apply to future runs.", "green");
            renderView();
          } catch (err) {
            btn.disabled = false;
            btn.textContent = origText;
            if (err.problems && typeof err.problems === "object") {
              problems = err.problems;
              toast("Invalid settings", "Please fix highlighted fields.", "red");
              renderView();
            }
          }
        },
      }, "Save changes")
    );
  }

  renderView();

  return () => {
    // Cleanup if any
    dirty.clear();
    problems = {};
  };
}
