const form = document.querySelector("#price-form");
const output = document.querySelector("#output");
const submitButton = document.querySelector("#submit-button");

/*
 * Holds the current query while the manufacturing analysis is in progress.
 *
 * The API key remains only in JavaScript memory during the query.
 * It is not written to disk or displayed on the page.
 */
let currentQuery = null;


/*
 * Start a completely new commodity/manufacturing query.
 */
form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const commodity = document.querySelector("#commodity").value.trim();

    const desiredQuantity = Number.parseInt(
        document.querySelector("#quantity").value,
        10
    );

    const apiKey = document.querySelector("#api-key").value;

    /*
     * Create the state for this query.
     *
     * recipeChoices begins empty. As the backend encounters materials
     * with multiple possible recipes, the user's decisions are stored
     * here and sent with every subsequent request.
     */
    currentQuery = {
        commodity,
        apiKey,
        desiredQuantity,
        recipeChoices: {},
    };

    output.hidden = false;
    output.className = "output";
    output.textContent = "Analyzing production requirements...";

    submitButton.disabled = true;

    /*
     * Clear the visible API-key field immediately.
     *
     * The key still exists temporarily in currentQuery so that recipe
     * selections can continue without making the user enter it again.
     */
    document.querySelector("#api-key").value = "";

    await continueAnalysis();
});


/*
 * Send the current query state to Flask.
 *
 * This function may be called several times for one user query:
 *
 *      initial request
 *          ↓
 *      recipe choice required
 *          ↓
 *      user selects recipe
 *          ↓
 *      same query is resubmitted
 *          ↓
 *      another recipe choice may be required
 *          ↓
 *      eventually status = "complete"
 */
async function continueAnalysis() {
    if (!currentQuery) {
        return;
    }

    output.className = "output";

    try {
        const response = await fetch("/api/price", {
            method: "POST",

            headers: {
                "Content-Type": "application/json",
            },

            body: JSON.stringify({
                commodity: currentQuery.commodity,
                api_key: currentQuery.apiKey,
                desired_quantity: currentQuery.desiredQuantity,
                recipe_choices: currentQuery.recipeChoices,
            }),
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                data.error || "The price lookup failed."
            );
        }

        /*
         * The backend reached a material with multiple possible recipes.
         *
         * Stop presenting the final result and instead ask the user
         * which recipe should be used.
         */
        if (data.status === "choice_required") {
            renderRecipeChoice(data);
            return;
        }

        /*
         * The backend successfully followed the selected production path
         * all the way to basal/external resources.
         */
        if (data.status === "complete") {
            output.innerHTML = renderResult(data.result);

            finishQuery();
            return;
        }

        /*
         * Defensive handling in case the backend returns a response
         * structure that this frontend does not recognize.
         */
        throw new Error(
            "The server returned an unexpected response."
        );

    } catch (error) {
        output.className = "output error";
        output.textContent = error.message;

        finishQuery();
    }
}


/*
 * Display a recipe-selection prompt.
 *
 * Only the immediate inputs for each available recipe are shown.
 * The alternatives are NOT recursively decomposed here.
 *
 * Once the user selects one recipe, only that recipe path will be
 * analyzed by the backend.
 */
function renderRecipeChoice(data) {
    const recipes = Array.isArray(data.recipes)
        ? data.recipes
        : [];

    const recipeMarkup = recipes.map(
        (recipe, index) => {

            const inputMarkup =
                Array.isArray(recipe.inputs) &&
                recipe.inputs.length
                    ? `
                        <ul class="choice-inputs">
                            ${recipe.inputs.map(
                                (input) => `
                                    <li>
                                        <strong>${escapeHtml(input.name)}</strong>:
                                        ${formatQuantity(input.amount)}
                                    </li>
                                `
                            ).join("")}
                        </ul>
                    `
                    : `
                        <p>
                            No material inputs.
                        </p>
                    `;

            return `
                <div class="recipe-choice">

                    <h4>
                        Option ${index + 1}:
                        ${escapeHtml(recipe.building)}
                    </h4>

                    ${
                        recipe.recipe_id
                            ? `
                                <p>
                                    Recipe ID:
                                    ${recipe.recipe_id}
                                </p>
                            `
                            : ""
                    }

                    <p>
                        Produces:
                        ${formatQuantity(recipe.output_amount)}
                        unit${recipe.output_amount === 1 ? "" : "s"}
                    </p>

                    <p>
                        Required technology:
                        ${recipe.required_technology}
                    </p>

                    <strong>Inputs</strong>

                    ${inputMarkup}

                    <button
                        type="button"
                        class="recipe-select-button"
                        data-material-id="${data.material_id}"
                        data-recipe-id="${recipe.recipe_id}"
                    >
                        Use ${escapeHtml(recipe.building)}
                    </button>

                </div>
            `;
        }
    ).join("");

    output.innerHTML = `
        <div class="recipe-selection">

            <h2>Recipe choice required</h2>

            <p>
                <strong>${escapeHtml(data.material_name)}</strong>
                can be obtained using more than one recipe.
            </p>

            <p>
                Select the production method available to your company.
                Only the selected path will be analyzed further.
            </p>

            <div class="recipe-options">
                ${recipeMarkup}
            </div>

            <div class="recipe-choice external-choice">

                <h4>None</h4>

                <p>
                    Select this if your company cannot produce
                    ${escapeHtml(data.material_name)}
                    internally.
                </p>

                <p>
                    It will be treated as an externally sourced
                    resource for this query.
                </p>

                <button
                    type="button"
                    class="recipe-none-button"
                    data-material-id="${data.material_id}"
                >
                    None — source externally
                </button>

            </div>

        </div>
    `;

    /*
     * Attach click handlers to the dynamically generated recipe buttons.
     */
    document
        .querySelectorAll(".recipe-select-button")
        .forEach((button) => {

            button.addEventListener(
                "click",
                async () => {

                    const materialId =
                        button.dataset.materialId;

                    const recipeId =
                        button.dataset.recipeId;

                    /*
                     * JSON object keys are strings, which matches what
                     * the Python backend expects.
                     */
                    currentQuery.recipeChoices[
                        materialId
                    ] = recipeId;

                    output.innerHTML =
                        "<p>Continuing analysis...</p>";

                    await continueAnalysis();
                }
            );
        });


    /*
     * Attach the handler for "None".
     *
     * null is sent to Python, where it means:
     * stop decomposing this material and treat it as external.
     */
    const noneButton =
        document.querySelector(
            ".recipe-none-button"
        );

    if (noneButton) {

        noneButton.addEventListener(
            "click",
            async () => {

                const materialId =
                    noneButton.dataset.materialId;

                currentQuery.recipeChoices[
                    materialId
                ] = null;

                output.innerHTML =
                    "<p>Continuing analysis...</p>";

                await continueAnalysis();
            }
        );
    }
}


/*
 * End the current query and remove sensitive/transient state.
 */
function finishQuery() {

    if (currentQuery) {
        currentQuery.apiKey = "";
    }

    currentQuery = null;

    submitButton.disabled = false;
}


/*
 * Format a number as US currency.
 */
function formatPrice(value) {

    return new Intl.NumberFormat(
        "en-US",
        {
            style: "currency",
            currency: "USD",
            minimumFractionDigits: 2,
        }
    ).format(value);
}


/*
 * Escape text received from the API before inserting it into HTML.
 */
function escapeHtml(value) {

    return String(value).replace(
        /[&<>'"]/g,
        (character) => ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            "'": "&#39;",
            '"': "&quot;",
        }[character])
    );
}


/*
 * Render the completed commodity/manufacturing analysis.
 */
function renderResult(data) {

    const analysis = data.analysis;

    const recipes =
        Array.isArray(analysis.recipes)
            ? analysis.recipes
            : [];

    const recipeMarkup = recipes.length
        ? recipes.map(
            (recipe, index) => `
                <li>

                    <strong>
                        Recipe ${index + 1}
                        ${
                            recipe.recipe_id
                                ? ` (ID ${recipe.recipe_id})`
                                : ""
                        }
                    </strong>

                    <span>
                        Building:
                        ${escapeHtml(recipe.building)}
                    </span>

                    <span>
                        Production batches:
                        ${recipe.production_batches}
                    </span>

                    ${
                        recipe.inputs.length
                            ? renderInputs(recipe.inputs)
                            : "<span>Precursors: None</span>"
                    }

                    <span>
                        Output:
                        ${recipe.output_amount}
                        unit${recipe.output_amount === 1 ? "" : "s"}
                    </span>

                    <span>
                        Required technology:
                        ${recipe.required_technology}
                    </span>

                    ${
                        renderBasalResources(
                            recipe.basal_resources
                        )
                    }

                </li>
            `
        ).join("")
        : `
            <li>
                No internal production recipe was selected.
                This commodity is being treated as externally sourced.
            </li>
        `;

    const facilitiesMarkup =
        renderFacilities(
            analysis.facilities || []
        );

    /*
     * Some results, especially externally sourced top-level materials,
     * store the basal summary directly on analysis rather than inside
     * a recipe.
     */
    const overallBasalMarkup =
        recipes.length === 0
            ? renderBasalResources(
                analysis.basal_resources
            )
            : "";

    return `
        <h2>
            ${escapeHtml(data.commodity)}
        </h2>

        <p class="price">
            ${formatPrice(data.price_per_unit)}
            per unit
        </p>

        <p class="total-price">
            Total for ${data.quantity}:
            ${formatPrice(data.total_price)}
        </p>

        <div class="analysis">

            <p class="origin">
                Origin:
                <strong>
                    ${escapeHtml(analysis.source)}
                </strong>
            </p>

            <h3>
                Selected production path
            </h3>

            <ul>
                ${recipeMarkup}
            </ul>

            ${overallBasalMarkup}

            ${facilitiesMarkup}

        </div>
    `;
}


/*
 * Format material quantities without unnecessary decimal places.
 */
function formatQuantity(value) {

    const number = Number(value);

    if (!Number.isFinite(number)) {
        return "0";
    }

    return Number.isInteger(number)
        ? number.toString()
        : number.toFixed(2);
}


/*
 * Display direct recipe inputs.
 */
function renderInputs(inputs) {

    return `
        <ul class="recipe-inputs">

            ${inputs.map(
                (item) => `
                    <li>

                        <span>
                            <strong>
                                ${escapeHtml(item.name)}
                            </strong>:

                            ${
                                formatQuantity(
                                    item.per_unit ??
                                    item.amount
                                )
                            }
                            per unit,

                            ${
                                formatQuantity(
                                    item.total ??
                                    item.amount
                                )
                            }
                            total
                        </span>

                        ${
                            renderNestedRecipes(
                                item.recipes || []
                            )
                        }

                    </li>
                `
            ).join("")}

        </ul>
    `;
}


/*
 * Display only the recipe path actually selected by the user.
 *
 * The updated backend no longer sends all alternative branches.
 */
function renderNestedRecipes(recipes) {

    if (!recipes.length) {
        return "";
    }

    return `
        <ul class="nested-recipes">

            ${recipes.map(
                (recipe) => `

                    <li>

                        <strong>
                            Use ${escapeHtml(recipe.building)}
                        </strong>

                        ${
                            recipe.recipe_id
                                ? ` (Recipe ${recipe.recipe_id})`
                                : ""
                        }

                        <span>
                            Produces
                            ${formatQuantity(recipe.output_amount)}
                            unit${recipe.output_amount === 1 ? "" : "s"};

                            required technology:
                            ${recipe.required_technology}
                        </span>

                        ${
                            recipe.inputs.length
                                ? renderInputs(recipe.inputs)
                                : `
                                    <span>
                                        Basal resource:
                                        no further inputs.
                                    </span>
                                `
                        }

                    </li>

                `
            ).join("")}

        </ul>
    `;
}


/*
 * Display the final flattened list of basal resources and materials
 * that the user elected to source externally.
 */
function renderBasalResources(summary) {

    if (
        !summary ||
        !Array.isArray(summary.resources) ||
        summary.resources.length === 0
    ) {
        return `
            <span class="basal-summary">
                Basal/external resources: None
            </span>
        `;
    }

    const resources =
        summary.resources.map(
            (resource) => {

                let classificationLabel =
                    "Basal resource";

                if (
                    resource.classification ===
                    "external"
                ) {
                    classificationLabel =
                        "Externally sourced";
                }

                return `
                    <li>

                        <strong>
                            ${escapeHtml(resource.name)}
                        </strong>

                        <span>
                            Type:
                            ${escapeHtml(
                                classificationLabel
                            )}
                        </span>

                        <span>
                            Quantity:
                            ${
                                formatQuantity(
                                    resource.quantity
                                )
                            }
                        </span>

                        <span>
                            Unit price:
                            ${
                                resource.unit_price === null
                                    ? "Unavailable"
                                    : formatPrice(
                                        resource.unit_price
                                    )
                            }
                        </span>

                        <span>
                            Total price:
                            ${
                                resource.total_price === null
                                    ? "Unavailable"
                                    : formatPrice(
                                        resource.total_price
                                    )
                            }
                        </span>

                    </li>
                `;
            }
        ).join("");

    return `
        <div class="basal-summary">

            <strong>
                Basal and externally sourced resources
            </strong>

            <ul>
                ${resources}
            </ul>

            <p class="grand-total">
                Grand total:
                ${formatPrice(summary.grand_total)}
            </p>

        </div>
    `;
}


/*
 * Display the production/extraction facilities actually needed
 * along the selected recipe path.
 */
function renderFacilities(facilities) {

    if (
        !Array.isArray(facilities) ||
        facilities.length === 0
    ) {
        return "";
    }

    return `
        <div class="facility-summary">

            <h3>
                Required facilities
            </h3>

            <ul>
                ${facilities.map(
                    (facility) => `
                        <li>
                            ${escapeHtml(facility)}
                        </li>
                    `
                ).join("")}
            </ul>

        </div>
    `;
}