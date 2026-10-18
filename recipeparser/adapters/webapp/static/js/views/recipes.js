// #/recipes — Recipe Library: browse, filter, search, and inspect recipes across runs.
import { h, clear, icon, debounce } from "../ui.js";
import { api } from "../api.js";

export async function render(root, params, query) {
  const state = {
    q: query.get("q") || "",
    run_id: query.get("run_id") || "",
    needs_review: query.get("needs_review") || "",
    judged: query.get("judged") || "",
    limit: 60,
    offset: 0,
    recipes: [],
    total: 0,
    loading: true,
    loadingMore: false,
    runs: [],
  };

  // Header & Controls Elements
  const countEl = h("span", { class: "small faint" });
  const gridEl = h("div", { class: "recipe-grid" });
  const footerEl = h("div", { class: "row", style: { justifyContent: "center", marginTop: "24px" } });

  const searchInput = h("input", {
    type: "search",
    class: "input input-sm",
    placeholder: "Search recipes by title or book…",
    value: state.q,
    oninput: (e) => onSearchInput(e.target.value),
  });

  const runSelect = h("select", {
    class: "input input-sm",
    style: { minWidth: "160px" },
    onchange: (e) => {
      state.run_id = e.target.value;
      loadRecipes(true);
    },
  }, h("option", { value: "" }, "All runs"));

  const reviewSelect = h("select", {
    class: "input input-sm",
    onchange: (e) => {
      state.needs_review = e.target.value;
      loadRecipes(true);
    },
  },
    h("option", { value: "", selected: state.needs_review === "" }, "All review statuses"),
    h("option", { value: "1", selected: state.needs_review === "1" }, "Needs review"),
    h("option", { value: "0", selected: state.needs_review === "0" }, "Clean (no review)")
  );

  const judgedSelect = h("select", {
    class: "input input-sm",
    onchange: (e) => {
      state.judged = e.target.value;
      loadRecipes(true);
    },
  },
    h("option", { value: "", selected: state.judged === "" }, "All judgements"),
    h("option", { value: "1", selected: state.judged === "1" }, "Judged"),
    h("option", { value: "0", selected: state.judged === "0" }, "Unjudged")
  );

  const clearBtn = h("button", {
    class: "btn btn-xs btn-ghost",
    title: "Clear all filters",
    onclick: () => resetFilters(),
  }, icon("x"), "Clear filters");

  function resetFilters() {
    state.q = "";
    state.run_id = "";
    state.needs_review = "";
    state.judged = "";
    searchInput.value = "";
    runSelect.value = "";
    reviewSelect.value = "";
    judgedSelect.value = "";
    loadRecipes(true);
  }

  function syncUrl() {
    const qp = new URLSearchParams();
    if (state.q.trim()) qp.set("q", state.q.trim());
    if (state.run_id) qp.set("run_id", state.run_id);
    if (state.needs_review !== "") qp.set("needs_review", state.needs_review);
    if (state.judged !== "") qp.set("judged", state.judged);
    const hash = qp.toString() ? `#/recipes?${qp.toString()}` : "#/recipes";
    history.replaceState(null, "", hash);
  }

  const onSearchInput = debounce((val) => {
    state.q = val;
    loadRecipes(true);
  }, 300);

  function recipeCard(r) {
    let mediaEl;
    if (r.cover_url) {
      mediaEl = h("div", { class: "recipe-card-media" },
        h("img", { src: r.cover_url, alt: r.title, loading: "lazy" })
      );
    } else {
      mediaEl = h("div", { class: "recipe-card-media" },
        h("div", { class: "recipe-card-placeholder" },
          icon("utensils"),
          h("span", {}, "No picture")
        )
      );
    }

    const badges = [];

    // Score badge (colored by score, marked stale if stale_score)
    if (r.score !== null && r.score !== undefined) {
      const color = r.score >= 8 ? "green" : r.score >= 5 ? "amber" : "red";
      badges.push(h("span", {
        class: `badge ${color}`,
        title: r.stale_score ? "Score may be stale (recipe edited since judgement)" : `Judge overall score: ${r.score}/10`
      }, `${r.score}/10${r.stale_score ? " (stale)" : ""}`));
    }

    // Needs review badge
    if (r.needs_review) {
      badges.push(h("span", {
        class: "badge amber",
        title: `${r.review_count || 1} review note(s)`
      }, icon("alert"), "Needs review"));
    }

    // Revision count badge
    if (r.revision > 0) {
      badges.push(h("span", {
        class: "badge purple",
        title: `Revision ${r.revision}`
      }, `edited · rev ${r.revision}`));
    }

    // Pending proposal badge
    if (r.pending) {
      badges.push(h("span", {
        class: "badge blue",
        title: "Has a pending revision proposal"
      }, icon("sparkles"), "Proposal"));
    }

    // Category tags
    if (r.categories && r.categories.length) {
      for (const cat of r.categories.slice(0, 2)) {
        badges.push(h("span", { class: "badge outline faint", title: `Category: ${cat}` }, cat));
      }
    }

    const runLabel = r.book_name
      ? `${r.book_name}${r.run_label ? " · " + r.run_label : ""}`
      : (r.run_label || r.run_id || "Run");

    return h("a", { class: "recipe-card", href: `#/recipes/${r.id}` },
      mediaEl,
      h("div", { class: "recipe-card-body" },
        h("h3", { class: "recipe-card-title", title: r.title }, r.title),
        h("div", { class: "recipe-card-meta ellipsis", title: runLabel }, runLabel),
        badges.length ? h("div", { class: "recipe-card-badges" }, ...badges) : null
      )
    );
  }

  function renderGrid() {
    const hasFilter = Boolean(state.q.trim() || state.run_id || state.needs_review !== "" || state.judged !== "");
    clearBtn.hidden = !hasFilter;

    if (state.loading) {
      countEl.textContent = "Loading recipes…";
      clear(gridEl, h("div", { class: "card empty col", style: { gridColumn: "1 / -1", padding: "60px 20px" } },
        h("div", { class: "spinner", style: { margin: "0 auto 12px", width: "20px", height: "20px" } }),
        h("span", { class: "faint" }, "Fetching recipe library…")
      ));
      clear(footerEl);
      return;
    }

    countEl.textContent = `Showing ${state.recipes.length} of ${state.total} recipe${state.total === 1 ? "" : "s"}`;

    if (!state.recipes.length) {
      clear(gridEl, h("div", { class: "card empty", style: { gridColumn: "1 / -1", padding: "60px 20px" } },
        icon("utensils"),
        h("h3", { style: { marginTop: "12px" } }, "No recipes found"),
        h("p", { class: "muted small" }, hasFilter ? "Try adjusting or clearing your filters." : "Extracted recipes will appear here as runs finish.")
      ));
      clear(footerEl);
      return;
    }

    clear(gridEl, state.recipes.map(recipeCard));

    // Paging: Load More
    if (state.recipes.length < state.total) {
      const remaining = state.total - state.recipes.length;
      clear(footerEl, h("button", {
        class: "btn btn-sm",
        disabled: state.loadingMore,
        onclick: loadMore,
      }, state.loadingMore ? [h("span", { class: "spinner" }), "Loading more…"] : `Load more (${remaining} remaining)`));
    } else {
      clear(footerEl);
    }
  }

  async function loadMore() {
    if (state.loading || state.loadingMore) return;
    state.offset = state.recipes.length;
    await loadRecipes(false);
  }

  async function loadRecipes(reset = true) {
    if (reset) {
      state.offset = 0;
      state.loading = true;
      renderGrid();
    } else {
      state.loadingMore = true;
      renderGrid();
    }
    syncUrl();

    const qp = new URLSearchParams();
    if (state.q.trim()) qp.set("q", state.q.trim());
    if (state.run_id) qp.set("run_id", state.run_id);
    if (state.needs_review !== "") qp.set("needs_review", state.needs_review);
    if (state.judged !== "") qp.set("judged", state.judged);
    qp.set("limit", String(state.limit));
    qp.set("offset", String(state.offset));

    try {
      const res = await api.get(`/api/recipes?${qp.toString()}`);
      state.total = res.total ?? 0;
      if (reset) {
        state.recipes = res.recipes || [];
      } else {
        state.recipes = state.recipes.concat(res.recipes || []);
      }
    } catch {
      // api.js handles toasts
    } finally {
      state.loading = false;
      state.loadingMore = false;
      renderGrid();
    }
  }

  async function loadRuns() {
    try {
      const res = await api.get("/api/runs", { quiet: true });
      const runs = (res && res.runs) || [];
      state.runs = runs;
      clear(runSelect,
        h("option", { value: "" }, "All runs"),
        ...runs.map((rn) => h("option", {
          value: rn.id,
          selected: rn.id === state.run_id
        }, `${rn.label || rn.id} (${rn.book_name || "run"})`))
      );
    } catch {
      // quiet fallback
    }
  }

  // Initial layout assembly
  clear(root,
    h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, "Recipe Library"),
        h("div", { class: "sub" }, "Browse, inspect, and refine extracted recipes across books and runs.")
      ),
      countEl
    ),
    h("div", { class: "recipe-library-controls" },
      h("div", { class: "recipe-library-search" },
        icon("search"),
        searchInput
      ),
      runSelect,
      reviewSelect,
      judgedSelect,
      clearBtn
    ),
    gridEl,
    footerEl
  );

  // Initial load
  loadRuns();
  loadRecipes(true);

  return () => {
    // Cleanup on view unmount
  };
}
