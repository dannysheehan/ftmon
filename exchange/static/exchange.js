"use strict";

// Filtering existing server-rendered cards keeps the catalogue useful when
// JavaScript is unavailable and makes enhancement failure non-destructive.
const search = document.querySelector("#exchange-search");
const cards = [...document.querySelectorAll("[data-recipe-id]")];
const count = document.querySelector("#result-count");
const filters = {
  category: document.querySelector("#category-filter"),
  platform: document.querySelector("#platform-filter"),
  protocol: document.querySelector("#protocol-filter"),
  privilege: document.querySelector("#privilege-filter"),
};

if (search && count && cards.length) {
  fetch("search-index.v1.json", { credentials: "same-origin" })
    .then((response) => {
      if (!response.ok) throw new Error("search index unavailable");
      return response.json();
    })
    .then((document) => {
      const recipes = new Map(document.recipes.map((recipe) => [recipe.id, recipe]));
      const apply = () => {
        const query = search.value.trim().toLowerCase();
        let visible = 0;
        cards.forEach((card) => {
          const recipe = recipes.get(card.dataset.recipeId);
          const matches = recipe
            && (!query || recipe.search_text.includes(query))
            && (!filters.category.value || recipe.category === filters.category.value)
            && (!filters.platform.value || recipe.platforms.includes(filters.platform.value))
            && (!filters.protocol.value || recipe.kind === filters.protocol.value)
            && (!filters.privilege.value || recipe.privilege === filters.privilege.value);
          card.hidden = !matches;
          if (matches) visible += 1;
        });
        count.textContent = `${visible} recipe${visible === 1 ? "" : "s"}`;
      };
      search.addEventListener("input", apply);
      Object.values(filters).forEach((filter) => filter.addEventListener("change", apply));
    })
    .catch(() => { count.textContent = `${cards.length} recipes; search unavailable`; });
}
