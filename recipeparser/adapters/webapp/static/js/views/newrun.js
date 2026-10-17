// recipeparser/adapters/webapp/static/js/views/newrun.js
import { h, clear, icon, fmt, toast, debounce } from "../ui.js";
import { api } from "../api.js";

const STORAGE_KEY = "rp.newrun.last_options";

export async function render(root, params, query) {
  let disposed = false;

  // Fetch metadata, books, and settings
  let meta = null;
  let booksData = { uploaded: [], corpus: [], recent: [] };
  let runSettings = [];

  try {
    const [m, b, s] = await Promise.all([
      api.get("/api/meta"),
      api.get("/api/books"),
      api.get("/api/settings"),
    ]);
    meta = m;
    booksData = b;
    runSettings = (s.settings || []).filter((item) => item.scope === "run" && item.editable && !item.secret);
  } catch (err) {
    clear(root,
      h("div", { class: "alert red" },
        icon("alert"),
        h("div", {},
          h("div", { class: "alert-title" }, "Failed to load initial configuration"),
          h("div", { class: "small" }, err.message)
        )
      )
    );
    return () => { disposed = true; };
  }

  // Restore remembered options
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    saved = {};
  }

  // State
  let bookTab = saved.bookTab || "upload"; // "upload" | "corpus" | "uploaded" | "recent" | "path"
  let selectedBookPath = saved.book_path || "";
  let selectedBookName = saved.book_name || "";
  let selectedBookReplay = !!saved.book_replay;

  const defaultProvider = saved.provider || meta.defaults?.provider || "gemini";
  let selectedProvider = selectedProviderValid(defaultProvider) ? defaultProvider : (meta.providers?.[0]?.id || "gemini");

  function selectedProviderValid(pid) {
    return (meta.providers || []).some((p) => p.id === pid);
  }

  const currentProviderMeta = () => (meta.providers || []).find((p) => p.id === selectedProvider) || {};

  let genModel = saved.gen_model !== undefined ? saved.gen_model : (meta.defaults?.gen_model || "");
  let embedModel = saved.embed_model !== undefined ? saved.embed_model : (meta.defaults?.embed_model || "");
  let embeddingVerdict = { status: "pending", message: "" }; // "ok" | "rejected" | "unverified" | "pending" | "checking"

  const maxConcurrency = meta.limits?.max_concurrency || 64;
  const maxRpm = meta.limits?.max_rpm || 500;

  let concurrency = Math.min(maxConcurrency, Math.max(1, saved.concurrency || meta.defaults?.concurrency || 16));
  let rpm = Math.min(maxRpm, Math.max(1, saved.rpm || meta.defaults?.rpm || maxRpm));
  let maxRecipes = saved.max_recipes !== undefined ? saved.max_recipes : 0;
  let startPercent = saved.start_percent !== undefined ? saved.start_percent : 0;
  let bookPercent = saved.book_percent !== undefined ? saved.book_percent : 100;
  let outputs = saved.outputs || ["paprika", "cayenne", "json"];
  let label = saved.label || "";
  let overrides = saved.overrides || {}; // { KEY: value }

  let isAdvancedOpen = false;
  let isSubmitting = false;
  let serverErrorMessage = "";

  // Elements
  const bookSection = h("div", { class: "card mb-16" });
  const providerSection = h("div", { class: "card mb-16" });
  const modelsSection = h("div", { class: "card mb-16" });
  const executionSection = h("div", { class: "card mb-16" });
  const outputsSection = h("div", { class: "card mb-16" });
  const advancedSection = h("div", { class: "card mb-16" });
  const stickyBar = h("div", { class: "control-bar" });

  function persistState() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        bookTab,
        book_path: selectedBookPath,
        book_name: selectedBookName,
        book_replay: selectedBookReplay,
        provider: selectedProvider,
        gen_model: genModel,
        embed_model: embedModel,
        concurrency,
        rpm,
        max_recipes: maxRecipes,
        start_percent: startPercent,
        book_percent: bookPercent,
        outputs,
        label,
        overrides,
      }));
    } catch {
      // ignore storage failure
    }
  }

  // ── 1. Book Picker ──────────────────────────────────────────────────────────
  async function handleFileUpload(file) {
    if (!file) return;
    try {
      toast("Uploading cookbook…", file.name, "blue");
      const res = await api.upload("/api/books", file);
      selectedBookPath = res.path;
      selectedBookName = res.name;
      // Re-fetch books list to update uploaded tab
      booksData = await api.get("/api/books");
      const up = (booksData.uploaded || []).find((b) => b.path === res.path);
      selectedBookReplay = !!up?.replay;
      persistState();
      toast("Cookbook uploaded", res.name, "green");
      renderBookSection();
      renderStickyBar();
    } catch {
      // api.upload toasts error
    }
  }

  function renderBookSection() {
    const tabs = [
      { id: "upload", label: "Upload" },
      { id: "corpus", label: `Golden corpus (${booksData.corpus?.length || 0})` },
      { id: "uploaded", label: `Uploaded (${booksData.uploaded?.length || 0})` },
      { id: "recent", label: `Recent (${booksData.recent?.length || 0})` },
      { id: "path", label: "Path" },
    ];

    const tabNav = h("div", { class: "seg mb-16" },
      tabs.map((t) =>
        h("button", {
          class: bookTab === t.id ? "on" : "",
          onclick: () => { bookTab = t.id; renderBookSection(); }
        }, t.label)
      )
    );

    let tabBody;
    if (bookTab === "upload") {
      const fileInput = h("input", {
        type: "file",
        accept: ".epub,.pdf,.paprikarecipes",
        style: { display: "none" },
        onchange: (e) => {
          if (e.target.files && e.target.files[0]) {
            handleFileUpload(e.target.files[0]);
          }
        }
      });

      const dropZone = h("div", {
        class: "empty",
        style: {
          border: "2px dashed var(--border-strong)",
          borderRadius: "var(--radius)",
          cursor: "pointer",
          padding: "36px 20px"
        },
        onclick: () => fileInput.click(),
        ondragover: (e) => { e.preventDefault(); dropZone.style.borderColor = "var(--text)"; },
        ondragleave: () => { dropZone.style.borderColor = "var(--border-strong)"; },
        ondrop: (e) => {
          e.preventDefault();
          dropZone.style.borderColor = "var(--border-strong)";
          if (e.dataTransfer.files && e.dataTransfer.files[0]) {
            handleFileUpload(e.dataTransfer.files[0]);
          }
        }
      },
        fileInput,
        icon("upload", "icon"),
        h("h3", { style: { marginTop: "8px" } }, "Drop cookbook file here, or click to browse"),
        h("p", { class: "muted small" }, "Supports EPUB, PDF and Paprika (.paprikarecipes) files.")
      );
      tabBody = dropZone;
    } else if (bookTab === "corpus" || bookTab === "uploaded" || bookTab === "recent") {
      const list = bookTab === "corpus"
        ? (booksData.corpus || [])
        : bookTab === "uploaded"
          ? (booksData.uploaded || [])
          : (booksData.recent || []);

      if (list.length === 0) {
        tabBody = h("div", { class: "empty small muted" }, "No books in this category.");
      } else {
        tabBody = h("div", { class: "col", style: { maxHeight: "260px", overflowY: "auto" } },
          list.map((b) => {
            const isSelected = selectedBookPath === b.path;
            return h("div", {
              class: `row between card-pad ${isSelected ? "option-card on" : "option-card"}`,
              style: { padding: "10px 14px", cursor: "pointer", borderRadius: "var(--radius)" },
              onclick: () => {
                selectedBookPath = b.path;
                selectedBookName = b.name;
                selectedBookReplay = !!b.replay;
                persistState();
                renderBookSection();
                renderStickyBar();
              }
            },
              h("div", { class: "row", style: { minWidth: 0, gap: "10px" } },
                icon("book"),
                h("div", { class: "ellipsis" },
                  h("div", { style: { fontWeight: "500" } }, b.name),
                  b.size ? h("div", { class: "faint small" }, fmt.bytes(b.size)) : null
                )
              ),
              h("div", { class: "row" },
                b.replay ? h("span", { class: "badge green small" }, "replay available") : null,
                isSelected ? icon("check") : null
              )
            );
          })
        );
      }
    } else if (bookTab === "path") {
      tabBody = h("div", { class: "col" },
        h("label", { class: "field" },
          h("span", { class: "field-label" }, "Absolute path to cookbook on server"),
          h("input", {
            type: "text",
            class: "input",
            placeholder: "/path/to/cookbook.epub",
            value: selectedBookPath,
            oninput: (e) => {
              selectedBookPath = e.target.value.trim();
              selectedBookName = selectedBookPath.split("/").pop();
              selectedBookReplay = false;
              persistState();
              renderStickyBar();
            }
          }),
          h("span", { class: "field-help" }, "Must be accessible by the RecipeParser backend process.")
        )
      );
    }

    clear(
      bookSection,
      h("div", { class: "card-head" },
        h("h3", {}, "1. Cookbook"),
        selectedBookPath ? h("div", { class: "row small" },
          h("span", { class: "muted" }, "Selected:"),
          h("strong", {}, selectedBookName || selectedBookPath),
          selectedBookReplay ? h("span", { class: "badge green small" }, "replay available") : null
        ) : h("span", { class: "badge amber small" }, "Required")
      ),
      h("div", { class: "card-body col" },
        tabNav,
        tabBody
      )
    );
  }

  // ── 2. Provider Option Cards ────────────────────────────────────────────────
  function renderProviderSection() {
    const providers = meta.providers || [];

    const cards = providers.map((p) => {
      const isSelected = selectedProvider === p.id;
      return h("div", {
        class: `option-card ${isSelected ? "on" : ""}`,
        style: { flex: 1, minWidth: "220px", cursor: "pointer", position: "relative" },
        onclick: () => {
          selectedProvider = p.id;
          // If models don't match provider, update them
          const pMeta = currentProviderMeta();
          if (pMeta.generation_models?.length && !pMeta.generation_models.some((m) => m.id === genModel)) {
            genModel = pMeta.generation_models[0].id;
          }
          if (pMeta.embedding_models?.length && !pMeta.embedding_models.some((m) => m.id === embedModel)) {
            embedModel = pMeta.embedding_models[0].id;
          }
          persistState();
          renderProviderSection();
          renderModelsSection();
          checkEmbedding();
          renderStickyBar();
        }
      },
        h("div", { class: "row between", style: { width: "100%", marginBottom: "4px" } },
          h("div", { style: { fontWeight: "600", fontSize: "15px" } }, p.label || p.id),
          h("div", { class: "row", style: { gap: "6px" } },
            p.offline ? h("span", { class: "badge purple small" }, "offline") : null,
            isSelected ? icon("check") : null
          )
        ),
        p.note ? h("div", { class: "muted small", style: { marginBottom: "8px" } }, p.note) : null,
        !p.offline ? (
          p.api_key_set
            ? h("div", { class: "small", style: { color: "var(--accent)" } }, "API key configured")
            : h("div", { class: "small", style: { color: "var(--danger)" } },
                "Missing API key · ",
                h("a", { class: "link", href: "#/settings", onclick: (e) => e.stopPropagation() }, "Set in Settings")
              )
        ) : null
      );
    });

    clear(
      providerSection,
      h("div", { class: "card-head" },
        h("h3", {}, "2. Extraction provider")
      ),
      h("div", { class: "card-body" },
        h("div", { class: "row wrap" }, cards)
      )
    );
  }

  // ── 3. Models & Embedding Check ─────────────────────────────────────────────
  const triggerCheckEmbeddingDebounced = debounce(() => {
    checkEmbedding();
  }, 350);

  async function checkEmbedding() {
    if (!embedModel) {
      embeddingVerdict = { status: "unverified", message: "No embedding model specified." };
      renderModelsSection();
      renderStickyBar();
      return;
    }
    embeddingVerdict = { status: "checking", message: "Verifying embedding model compatibility…" };
    renderModelsSection();
    renderStickyBar();

    try {
      const res = await api.post("/api/settings/check-embedding", {
        provider: selectedProvider,
        model: embedModel,
      }, { quiet: true });
      if (disposed) return;
      embeddingVerdict = {
        status: res.verdict, // "ok" | "rejected" | "unverified"
        message: res.message || (res.verdict === "ok" ? "Compatible with 768-dim embeddings" : res.verdict),
      };
    } catch (e) {
      if (disposed) return;
      embeddingVerdict = {
        status: "rejected",
        message: e.message || "Failed to verify embedding model.",
      };
    }
    renderModelsSection();
    renderStickyBar();
  }

  function renderModelsSection() {
    const pMeta = currentProviderMeta();
    const genListId = `gen-models-${selectedProvider}`;
    const embedListId = `embed-models-${selectedProvider}`;

    const genDatalist = h("datalist", { id: genListId },
      (pMeta.generation_models || []).map((m) => h("option", { value: m.id }, m.label || m.id))
    );
    const embedDatalist = h("datalist", { id: embedListId },
      (pMeta.embedding_models || []).map((m) => h("option", { value: m.id }, m.label || m.id))
    );

    let verdictBadge = null;
    if (embeddingVerdict.status === "ok") {
      verdictBadge = h("div", { class: "alert green small", style: { padding: "6px 10px" } },
        icon("check"),
        h("span", {}, embeddingVerdict.message)
      );
    } else if (embeddingVerdict.status === "rejected") {
      verdictBadge = h("div", { class: "alert red small", style: { padding: "6px 10px" } },
        icon("alert"),
        h("span", {}, embeddingVerdict.message)
      );
    } else if (embeddingVerdict.status === "checking") {
      verdictBadge = h("div", { class: "alert small", style: { padding: "6px 10px" } },
        h("div", { class: "spinner" }),
        h("span", { class: "muted" }, embeddingVerdict.message)
      );
    } else if (embeddingVerdict.status === "unverified") {
      verdictBadge = h("div", { class: "alert amber small", style: { padding: "6px 10px" } },
        icon("alert"),
        h("span", {}, embeddingVerdict.message)
      );
    }

    clear(
      modelsSection,
      h("div", { class: "card-head" },
        h("h3", {}, "3. Models")
      ),
      h("div", { class: "card-body col" },
        h("div", { class: "grid cols-2" },
          h("label", { class: "field" },
            h("span", { class: "field-label" }, "Generation model"),
            h("input", {
              type: "text",
              class: "input",
              list: genListId,
              value: genModel,
              placeholder: meta.defaults?.gen_model || "gemini-2.5-flash",
              oninput: (e) => {
                genModel = e.target.value.trim();
                persistState();
                renderStickyBar();
              }
            }),
            genDatalist,
            h("span", { class: "field-help" }, "LLM used for recipe extraction and verification.")
          ),
          h("label", { class: "field" },
            h("span", { class: "field-label" }, "Embedding model"),
            h("input", {
              type: "text",
              class: "input",
              list: embedListId,
              value: embedModel,
              placeholder: meta.defaults?.embed_model || "text-embedding-004",
              oninput: (e) => {
                embedModel = e.target.value.trim();
                persistState();
                triggerCheckEmbeddingDebounced();
                renderStickyBar();
              }
            }),
            embedDatalist,
            h("span", { class: "field-help" }, "Must output 768 dimensions for semantic search.")
          )
        ),
        verdictBadge
      )
    );
  }

  // ── 4. Execution Limits & Book Range ─────────────────────────────────────────
  function renderExecutionSection() {
    const rangeTotal = startPercent + bookPercent;
    const isRangeValid = rangeTotal <= 100.0001 && bookPercent > 0;
    const endPercent = Math.min(100, Math.round((startPercent + bookPercent) * 10) / 10);
    const rangeSummaryText = isRangeValid
      ? `Processing ${bookPercent}% of book (${startPercent}% → ${endPercent}%)`
      : `Invalid range: sum is ${rangeTotal}% (cannot exceed 100%)`;

    clear(
      executionSection,
      h("div", { class: "card-head" },
        h("h3", {}, "4. Concurrency & Book slice")
      ),
      h("div", { class: "card-body col" },
        h("div", { class: "grid cols-2" },
          // Concurrency
          h("div", { class: "col" },
            h("label", { class: "field" },
              h("div", { class: "row between" },
                h("span", { class: "field-label" }, "Concurrency"),
                h("span", { style: { fontWeight: "600" } }, concurrency)
              ),
              h("div", { class: "row" },
                h("input", {
                  type: "range",
                  min: "1",
                  max: String(maxConcurrency),
                  value: String(concurrency),
                  oninput: (e) => {
                    concurrency = Number(e.target.value);
                    persistState();
                    renderExecutionSection();
                  }
                }),
                h("input", {
                  type: "number",
                  class: "input input-sm",
                  style: { width: "70px" },
                  min: "1",
                  max: String(maxConcurrency),
                  value: String(concurrency),
                  onchange: (e) => {
                    concurrency = Math.min(maxConcurrency, Math.max(1, Number(e.target.value) || 1));
                    persistState();
                    renderExecutionSection();
                  }
                })
              ),
              h("span", { class: "field-help" }, `Parallel worker tasks (1..${maxConcurrency}).`)
            )
          ),
          // RPM
          h("div", { class: "col" },
            h("label", { class: "field" },
              h("div", { class: "row between" },
                h("span", { class: "field-label" }, "Rate limit (RPM)"),
                h("span", { style: { fontWeight: "600" } }, rpm)
              ),
              h("div", { class: "row" },
                h("input", {
                  type: "range",
                  min: "1",
                  max: String(maxRpm),
                  value: String(rpm),
                  oninput: (e) => {
                    rpm = Number(e.target.value);
                    persistState();
                    renderExecutionSection();
                  }
                }),
                h("input", {
                  type: "number",
                  class: "input input-sm",
                  style: { width: "70px" },
                  min: "1",
                  max: String(maxRpm),
                  value: String(rpm),
                  onchange: (e) => {
                    rpm = Math.min(maxRpm, Math.max(1, Number(e.target.value) || 1));
                    persistState();
                    renderExecutionSection();
                  }
                })
              ),
              h("span", { class: "field-help" }, `Max API requests per minute to avoid 429 rate limits (1..${maxRpm}).`)
            )
          )
        ),
        h("hr"),
        h("div", { class: "grid cols-3" },
          // Recipe limit
          h("label", { class: "field" },
            h("span", { class: "field-label" }, "Recipe limit"),
            h("input", {
              type: "number",
              class: "input",
              min: "0",
              value: String(maxRecipes),
              oninput: (e) => {
                maxRecipes = Math.max(0, Number(e.target.value) || 0);
                persistState();
              }
            }),
            h("span", { class: "field-help" }, "0 = process entire slice with no limit.")
          ),
          // Start %
          h("label", { class: "field" },
            h("div", { class: "row between" },
              h("span", { class: "field-label" }, "Start offset %"),
              h("span", { class: "small muted" }, `${startPercent}%`)
            ),
            h("input", {
              type: "range",
              min: "0",
              max: "100",
              value: String(startPercent),
              oninput: (e) => {
                startPercent = Number(e.target.value);
                persistState();
                renderExecutionSection();
                renderStickyBar();
              }
            }),
            h("input", {
              type: "number",
              class: "input input-sm",
              min: "0",
              max: "100",
              value: String(startPercent),
              onchange: (e) => {
                startPercent = Math.min(100, Math.max(0, Number(e.target.value) || 0));
                persistState();
                renderExecutionSection();
                renderStickyBar();
              }
            })
          ),
          // % to process
          h("label", { class: "field" },
            h("div", { class: "row between" },
              h("span", { class: "field-label" }, "% of book to process"),
              h("span", { class: "small muted" }, `${bookPercent}%`)
            ),
            h("input", {
              type: "range",
              min: "1",
              max: "100",
              value: String(bookPercent),
              oninput: (e) => {
                bookPercent = Number(e.target.value);
                persistState();
                renderExecutionSection();
                renderStickyBar();
              }
            }),
            h("input", {
              type: "number",
              class: "input input-sm",
              min: "1",
              max: "100",
              value: String(bookPercent),
              onchange: (e) => {
                bookPercent = Math.min(100, Math.max(1, Number(e.target.value) || 1));
                persistState();
                renderExecutionSection();
                renderStickyBar();
              }
            })
          )
        ),
        h("div", {
          class: isRangeValid ? "small muted" : "small",
          style: { color: isRangeValid ? "var(--text-2)" : "var(--danger)", fontWeight: "500" }
        }, rangeSummaryText)
      )
    );
  }

  // ── 5. Outputs & Label ───────────────────────────────────────────────────────
  function renderOutputsSection() {
    const allOutputs = [
      { id: "paprika", label: "Paprika (.paprikarecipes)" },
      { id: "cayenne", label: "Cayenne Markdown" },
      { id: "json", label: "JSON export" },
    ];

    clear(
      outputsSection,
      h("div", { class: "card-head" },
        h("h3", {}, "5. Outputs & Run label")
      ),
      h("div", { class: "card-body col" },
        h("div", { class: "row wrap", style: { gap: "20px" } },
          allOutputs.map((out) =>
            h("label", { class: "check", style: { cursor: "pointer" } },
              h("input", {
                type: "checkbox",
                checked: outputs.includes(out.id),
                onchange: (e) => {
                  if (e.target.checked) {
                    if (!outputs.includes(out.id)) outputs.push(out.id);
                  } else {
                    outputs = outputs.filter((x) => x !== out.id);
                  }
                  persistState();
                }
              }),
              h("span", {}, out.label)
            )
          )
        ),
        h("hr"),
        h("label", { class: "field" },
          h("span", { class: "field-label" }, "Optional label"),
          h("input", {
            type: "text",
            class: "input",
            placeholder: "e.g. Chapter 3 test run or prompt-v2 comparison",
            value: label,
            oninput: (e) => {
              label = e.target.value;
              persistState();
            }
          }),
          h("span", { class: "field-help" }, "Shown alongside the book name in lists and compare tables.")
        )
      )
    );
  }

  // ── 6. Advanced Overrides (Collapsible) ─────────────────────────────────────
  let selectedOverrideKey = "";
  let overrideInputValue = "";

  function renderAdvancedSection() {
    const overrideKeys = Object.keys(overrides);
    const availableSettings = runSettings.filter((s) => !overrideKeys.includes(s.key));

    const head = h("div", {
      class: "card-head",
      style: { cursor: "pointer", userSelect: "none" },
      onclick: () => { isAdvancedOpen = !isAdvancedOpen; renderAdvancedSection(); }
    },
      h("div", { class: "row" },
        icon(isAdvancedOpen ? "chevron" : "chevron", isAdvancedOpen ? "icon" : "icon"),
        h("h3", {}, `6. Advanced run settings ${overrideKeys.length ? `(${overrideKeys.length} active)` : ""}`)
      ),
      h("span", { class: "muted small" }, isAdvancedOpen ? "Collapse" : "Expand")
    );

    if (!isAdvancedOpen) {
      clear(advancedSection, head);
      return;
    }

    const currentSettingMeta = runSettings.find((s) => s.key === selectedOverrideKey);

    const body = h("div", { class: "card-body col" },
      h("p", { class: "small muted mb-16" },
        "Override any run-scoped setting for this execution only. Overrides do not affect global settings."
      ),
      // Active overrides list
      overrideKeys.length > 0 ? h("div", { class: "col mb-16" },
        overrideKeys.map((k) =>
          h("div", { class: "row between card-pad", style: { padding: "8px 12px", border: "1px solid var(--border)", borderRadius: "var(--radius)" } },
            h("div", { class: "col", style: { gap: "2px" } },
              h("strong", { class: "mono small" }, k),
              h("span", { class: "small muted" }, String(overrides[k]))
            ),
            h("button", {
              class: "btn btn-xs btn-ghost",
              onclick: () => {
                delete overrides[k];
                persistState();
                renderAdvancedSection();
              }
            }, icon("trash"), "Remove")
          )
        )
      ) : null,
      // Add override control
      h("div", { class: "row wrap", style: { alignItems: "flex-end", gap: "10px" } },
        h("label", { class: "field", style: { flex: 1, minWidth: "220px" } },
          h("span", { class: "field-label small" }, "Select setting"),
          h("select", {
            class: "input input-sm",
            onchange: (e) => {
              selectedOverrideKey = e.target.value;
              const s = runSettings.find((x) => x.key === selectedOverrideKey);
              overrideInputValue = s ? String(s.default ?? "") : "";
              renderAdvancedSection();
            }
          },
            h("option", { value: "" }, "— Choose setting override —"),
            availableSettings.map((s) =>
              h("option", { value: s.key, selected: s.key === selectedOverrideKey }, `${s.key} (${s.default})`)
            )
          )
        ),
        currentSettingMeta ? h("label", { class: "field", style: { flex: 1, minWidth: "180px" } },
          h("span", { class: "field-label small" }, "Value"),
          h("input", {
            type: "text",
            class: "input input-sm",
            value: overrideInputValue,
            oninput: (e) => { overrideInputValue = e.target.value; }
          })
        ) : null,
        currentSettingMeta ? h("button", {
          class: "btn btn-sm",
          disabled: !selectedOverrideKey || !overrideInputValue.trim(),
          onclick: () => {
            if (selectedOverrideKey && overrideInputValue.trim() !== "") {
              overrides[selectedOverrideKey] = overrideInputValue.trim();
              selectedOverrideKey = "";
              overrideInputValue = "";
              persistState();
              renderAdvancedSection();
            }
          }
        }, icon("plus"), "Add override") : null
      ),
      currentSettingMeta ? h("div", { class: "faint small mt-8" },
        currentSettingMeta.description ? `${currentSettingMeta.description} ` : "",
        `Default: ${currentSettingMeta.default}`
      ) : null
    );

    clear(advancedSection, head, body);
  }

  // ── 7. Sticky Bar & Run Creation ───────────────────────────────────────────
  async function handleStartRun() {
    if (isSubmitting) return;
    serverErrorMessage = "";

    // Local validation
    if (!selectedBookPath) {
      serverErrorMessage = "Please select or upload a cookbook first.";
      renderStickyBar();
      return;
    }
    if (embeddingVerdict.status === "rejected") {
      serverErrorMessage = `Embedding model rejected: ${embeddingVerdict.message}`;
      renderStickyBar();
      return;
    }
    if (startPercent + bookPercent > 100.0001) {
      serverErrorMessage = "Start offset % + % of book cannot exceed 100%.";
      renderStickyBar();
      return;
    }

    const settingsOverrides = { ...overrides };
    // Send model overrides only if changed from defaults
    if (genModel && genModel !== meta.defaults?.gen_model) {
      settingsOverrides["GEMINI_MODEL"] = genModel;
    }
    if (embedModel && embedModel !== meta.defaults?.embed_model) {
      settingsOverrides["GEMINI_EMBEDDING_MODEL"] = embedModel;
    }

    const payload = {
      book_path: selectedBookPath,
      provider: selectedProvider,
      label: label.trim() || undefined,
      options: {
        concurrency,
        rpm,
        max_recipes: maxRecipes,
        book_percent: bookPercent,
        start_percent: startPercent,
        outputs,
        settings_overrides: settingsOverrides,
      },
    };

    isSubmitting = true;
    renderStickyBar();

    try {
      const res = await api.post("/api/runs", payload, { quiet: true });
      persistState();
      toast("Run started", `ID: ${res.id}`, "green");
      location.hash = `#/runs/${encodeURIComponent(res.id)}`;
    } catch (err) {
      isSubmitting = false;
      serverErrorMessage = err.message || "Failed to start extraction run.";
      renderStickyBar();
    }
  }

  function renderStickyBar() {
    const isValid = selectedBookPath &&
      embeddingVerdict.status !== "rejected" &&
      (startPercent + bookPercent <= 100.0001);

    const summaryText = [
      selectedBookName || selectedBookPath || "No cookbook selected",
      selectedProvider,
      `${concurrency} workers`,
      `${rpm} RPM`,
      `${bookPercent}% slice`,
    ].filter(Boolean).join(" • ");

    clear(
      stickyBar,
      serverErrorMessage ? h("div", {
        class: "alert red small",
        style: { width: "100%", marginBottom: "10px" }
      },
        icon("alert"),
        h("div", {},
          h("div", { class: "alert-title" }, "Could not start run"),
          h("div", {}, serverErrorMessage)
        )
      ) : null,
      h("div", { class: "row between", style: { width: "100%" } },
        h("div", { class: "col", style: { gap: "2px", minWidth: 0 } },
          h("strong", { class: "ellipsis" }, summaryText),
          h("span", { class: "muted small" },
            outputs.length ? `Outputs: ${outputs.join(", ")}` : "No local output files selected"
          )
        ),
        h("div", { class: "row" },
          h("a", { class: "btn btn-ghost", href: "#/" }, "Cancel"),
          h("button", {
            class: "btn btn-primary",
            disabled: !isValid || isSubmitting,
            onclick: handleStartRun
          },
            isSubmitting ? h("div", { class: "spinner" }) : icon("play"),
            isSubmitting ? "Starting run…" : "Start run"
          )
        )
      )
    );
  }

  // Render initial components
  clear(
    root,
    h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, "New run"),
        h("div", { class: "sub small" }, "Configure cookbook, model parameters, and execution limits.")
      )
    ),
    bookSection,
    providerSection,
    modelsSection,
    executionSection,
    outputsSection,
    advancedSection,
    stickyBar
  );

  renderBookSection();
  renderProviderSection();
  renderModelsSection();
  renderExecutionSection();
  renderOutputsSection();
  renderAdvancedSection();
  renderStickyBar();

  // Run initial embedding check
  checkEmbedding();

  return () => {
    disposed = true;
  };
}
