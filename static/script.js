const form = document.querySelector("#price-form");
const output = document.querySelector("#output");
const submitButton = document.querySelector("#submit-button");

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    output.hidden = false;
    output.className = "output";
    output.textContent = "Looking up the live market price...";
    submitButton.disabled = true;

    const commodity = document.querySelector("#commodity").value.trim();
    const desiredQuantity = Number.parseInt(document.querySelector("#quantity").value, 10);
    const apiKey = document.querySelector("#api-key").value;

    try {
        const response = await fetch("/api/price", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ commodity, api_key: apiKey, desired_quantity: desiredQuantity }),
        });
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "The price lookup failed.");
        }

        output.innerHTML = renderResult(data);
    } catch (error) {
        output.className = "output error";
        output.textContent = error.message;
    } finally {
        document.querySelector("#api-key").value = "";
        submitButton.disabled = false;
    }
});

function formatPrice(value) {
    return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: 2,
    }).format(value);
}

function escapeHtml(value) {
    return value.replace(/[&<>'"]/g, (character) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "'": "&#39;",
        '"': "&quot;",
    }[character]));
}

function renderResult(data) {
    const analysis = data.analysis;
    const recipeMarkup = analysis.recipes.map((recipe, index) => `
        <li>
            <strong>Recipe ${index + 1}${recipe.recipe_id ? ` (ID ${recipe.recipe_id})` : ""}</strong>
            <span>Building: ${escapeHtml(recipe.building)}</span>
            <span>Production batches: ${recipe.production_batches}</span>
            ${recipe.inputs.length ? renderInputs(recipe.inputs) : "<span>Precursors: None</span>"}
            <span>Output: ${recipe.output_amount} unit${recipe.output_amount === 1 ? "" : "s"}</span>
            <span>Required technology: ${recipe.required_technology}</span>
            ${renderBasalResources(recipe.basal_resources)}
        </li>
    `).join("");

    return `
        <h2>${escapeHtml(data.commodity)}</h2>
        <p class="price">${formatPrice(data.price_per_unit)} per unit</p>
        <p class="total-price">Total for ${data.quantity}: ${formatPrice(data.total_price)}</p>
        <div class="analysis">
            <p class="origin">Origin: <strong>${escapeHtml(analysis.source)}</strong></p>
            <h3>Production recipes</h3>
            <ul>${recipeMarkup}</ul>
        </div>
    `;
}

function formatQuantity(value) {
    return Number.isInteger(value) ? value.toString() : value.toFixed(2);
}

function renderInputs(inputs) {
    return `<ul class="recipe-inputs">${inputs.map((item) => `
        <li>
            <span><strong>${escapeHtml(item.name)}</strong>: ${formatQuantity(item.per_unit ?? item.amount)} per unit, ${formatQuantity(item.total ?? item.amount)} total</span>
            ${renderNestedRecipes(item.recipes || [])}
        </li>
    `).join("")}</ul>`;
}

function renderNestedRecipes(recipes) {
    if (!recipes.length) {
        return "";
    }

    return `<ul class="nested-recipes">${recipes.map((recipe) => `
        <li>
            <strong>Use ${escapeHtml(recipe.building)}</strong>${recipe.recipe_id ? ` (Recipe ${recipe.recipe_id})` : ""}
            <span>Produces ${recipe.output_amount} unit${recipe.output_amount === 1 ? "" : "s"}; required technology: ${recipe.required_technology}</span>
            ${recipe.inputs.length ? renderInputs(recipe.inputs) : "<span>Basal resource: no further recipe.</span>"}
        </li>
    `).join("")}</ul>`;
}

function renderBasalResources(summary) {
    if (!summary || !Array.isArray(summary.resources) || summary.resources.length === 0) {
        return "<span class=\"basal-summary\">Basal resources: None</span>";
    }

    const resources = summary.resources.map((resource) => `
        <li>
            <strong>${escapeHtml(resource.name)}</strong>
            <span>Quantity: ${formatQuantity(resource.quantity)}</span>
            <span>Unit price: ${resource.unit_price === null ? "Unavailable" : formatPrice(resource.unit_price)}</span>
            <span>Total price: ${resource.total_price === null ? "Unavailable" : formatPrice(resource.total_price)}</span>
        </li>
    `).join("");

    return `
        <div class="basal-summary">
            <strong>Basal resources for this recipe</strong>
            <ul>${resources}</ul>
            <p class="grand-total">Grand total: ${formatPrice(summary.grand_total)}</p>
        </div>
    `;
}
