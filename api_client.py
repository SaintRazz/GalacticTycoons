import requests


# API endpoint containing current market prices for materials.
API_URL = "https://api.g2.galactictycoons.com/public/exchange/mat-prices"

# API endpoint containing relatively static game definitions:
# materials, recipes, buildings, etc.
GAME_DATA_URL = "https://api.g2.galactictycoons.com/gamedata.json"

# Prevent an API request from hanging indefinitely.
REQUEST_TIMEOUT_SECONDS = 15


# -------------------------------------------------------------------
# CUSTOM EXCEPTIONS
# -------------------------------------------------------------------

class ApiClientError(Exception):
    """
    General exception used when something goes wrong while interacting
    with the Galactic Tycoons API.

    status_code is presumably passed back to Flask so that the browser
    receives an appropriate HTTP response code.
    """

    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


class CommodityNotFoundError(ApiClientError):
    """
    Specialized API error for a commodity name that cannot be matched.
    """

    def __init__(self):
        super().__init__("Commodity was not found.", status_code=404)


# -------------------------------------------------------------------
# MAIN PUBLIC FUNCTION
# -------------------------------------------------------------------

def get_commodity_price(commodity_name, api_key, desired_quantity=1):
    """
    Look up a commodity by name and return:

    - commodity name
    - requested quantity
    - current market price
    - total purchase price
    - manufacturing / production analysis

    This is effectively the main entry point into this module.
    """

    # Make two API calls:
    # 1. Current exchange prices
    # 2. Game definitions such as recipes/materials/buildings
    price_data = _get_json(API_URL, api_key)
    game_data = _get_json(GAME_DATA_URL, api_key)

    # Extract the portions of the returned JSON that we need.
    #
    # If the API response structure changes or is malformed,
    # raise a controlled error rather than crashing with KeyError.
    try:
        prices = price_data["prices"]
        materials = game_data["materials"]
        recipes = game_data["recipes"]
        buildings = game_data["buildings"]
    except (TypeError, KeyError):
        raise ApiClientError(
            "The Galactic Tycoons API returned an unexpected response."
        )

    # Find the requested commodity in the market-price list.
    #
    # matName appears to be the field containing the human-readable
    # material name, e.g. "Iron".
    match = _find_named_item(prices, commodity_name, "matName")

    if not match:
        raise CommodityNotFoundError()

    # Galactic Tycoons expresses prices in cents.
    current_price_cents = match.get("currentPrice")

    # Ensure that the commodity actually has a valid current listing.
    if (
        not isinstance(current_price_cents, (int, float))
        or current_price_cents < 0
    ):
        raise ApiClientError(
            f'No current market listing is available for '
            f'"{match.get("matName", commodity_name)}".',
            404,
        )

    # Find the corresponding full material definition in gamedata.json.
    #
    # The price object identifies the material by matId,
    # while game data identifies it by id.
    material = next(
        (
            item
            for item in materials
            if item.get("id") == match.get("matId")
        ),
        None,
    )

    if not material:
        raise ApiClientError(
            "The selected material is missing from game data."
        )

    # Return one combined result containing both market information
    # and the manufacturing analysis.
    return {
        "commodity": match.get("matName", commodity_name),

        # Quantity requested by the user.
        "quantity": desired_quantity,

        # Convert cents to dollars.
        "price_per_unit": current_price_cents / 100,

        # Retain the original value as cents in case another part of
        # the application wants exact integer currency calculations.
        "price_cents": current_price_cents,

        # Cost of buying the requested quantity from the market.
        "total_price":
            current_price_cents * desired_quantity / 100,

        # Analyze recipes, dependencies, basal resources, etc.
        "analysis": _build_production_analysis(
            material,
            recipes,
            buildings,
            materials,
            prices,
            desired_quantity,
        ),
    }


# -------------------------------------------------------------------
# HTTP / JSON HANDLING
# -------------------------------------------------------------------

def _get_json(url, api_key):
    """
    Perform an authenticated GET request and return parsed JSON.

    Centralizing API access here means the rest of the program does
    not need to repeatedly deal with HTTP errors, authentication
    headers, timeouts, or JSON parsing.
    """

    try:
        response = requests.get(
            url,

            # Galactic Tycoons expects the company API key as a
            # Bearer token.
            headers={
                "Authorization": f"Bearer {api_key}"
            },

            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    except requests.RequestException as error:
        # Network failure, DNS problem, timeout, lost connection, etc.
        raise ApiClientError(
            "Could not connect to the Galactic Tycoons API."
        ) from error

    # response.ok is False for HTTP error responses such as
    # 401, 404, 429, 500, etc.
    if not response.ok:

        # Authentication / authorization failure.
        if response.status_code in (401, 403):
            raise ApiClientError(
                "The API key was rejected by Galactic Tycoons.",
                502,
            )

        # Too many API requests.
        if response.status_code == 429:
            raise ApiClientError(
                "The Galactic Tycoons API rate limit was reached.",
                502,
            )

        # Generic API failure.
        raise ApiClientError(
            "The Galactic Tycoons API returned an error.",
            502,
        )

    # Convert the HTTP response body from JSON text into
    # Python dictionaries/lists.
    try:
        return response.json()

    except ValueError as error:
        raise ApiClientError(
            "The Galactic Tycoons API returned invalid JSON."
        ) from error


# -------------------------------------------------------------------
# NAME LOOKUP
# -------------------------------------------------------------------

def _find_named_item(items, requested_name, name_key):
    """
    Find an item by human-readable name.

    Lookup is case-insensitive.

    First preference:
        exact match

    Second preference:
        substring match

    Example:
        "iron" -> "Iron"

    Potentially:
        "construction" -> first material containing "construction"
    """

    # casefold() is a stronger Unicode-safe version of lower().
    requested = requested_name.casefold()

    # First attempt an exact case-insensitive match.
    exact_match = next(
        (
            item
            for item in items
            if str(item.get(name_key, "")).casefold() == requested
        ),
        None,
    )

    # If exact match fails, use the first partial match.
    return exact_match or next(
        (
            item
            for item in items
            if requested
            in str(item.get(name_key, "")).casefold()
        ),
        None,
    )


# -------------------------------------------------------------------
# PRODUCTION / RECIPE ANALYSIS
# -------------------------------------------------------------------

def _build_production_analysis(
    material,
    recipes,
    buildings,
    materials,
    prices,
    desired_quantity=1,
):
    """
    Analyze all recipes capable of producing the selected material.

    Builds several lookup dictionaries for efficiency, then returns:

    - whether the material is extracted or crafted
    - whether it is effectively a basic resource
    - every known recipe for producing it
    - direct inputs
    - nested dependency recipes
    - fully reduced basal/raw-resource requirements
    """

    # Convert lists into dictionaries indexed by ID.
    #
    # This avoids repeatedly scanning the entire material/building
    # lists every time we need to find one object.
    material_by_id = {
        item.get("id"): item
        for item in materials
    }

    building_by_id = {
        item.get("id"): item
        for item in buildings
    }

    price_by_id = {
        item.get("matId"): item
        for item in prices
    }

    # Build a reverse lookup:
    #
    # output material ID -> recipes that produce it
    #
    # There may be more than one recipe for a material, hence
    # the value is a list rather than a single recipe.
    recipes_by_output = {}

    for recipe in recipes:
        output_id = recipe.get("output", {}).get("id")

        recipes_by_output.setdefault(
            output_id,
            [],
        ).append(recipe)

    # Find every recipe capable of producing the requested material.
    matching_recipes = recipes_by_output.get(
        material.get("id"),
        [],
    )

    recipe_results = []

    # Analyze each possible production recipe separately.
    for recipe in matching_recipes:

        # Determine how many units one recipe batch produces.
        output_amount = _amount(recipe.get("output"))

        # Determine how many complete manufacturing batches are
        # required to satisfy desired_quantity.
        #
        # This is ceiling division.
        #
        # Example:
        # Recipe makes 12 Concrete.
        # User requests 20 Concrete.
        #
        # 20 / 12 = 1.67, therefore 2 complete batches are needed.
        production_batches = (
            max(
                1,
                -(-desired_quantity // output_amount)
            )
            if output_amount
            else 1
        )

        # Determine which building produces the recipe.
        building = building_by_id.get(
            recipe.get("producedIn"),
            {},
        )

        inputs = []

        # Analyze each direct ingredient in this recipe.
        for item in recipe.get("inputs", []):

            input_amount = _amount(item)

            input_material = material_by_id.get(
                item.get("id"),
                {},
            )

            inputs.append({
                "id": item.get("id"),

                "name": input_material.get(
                    "name",
                    f'Material {item.get("id")}',
                ),

                # Ingredient quantity required by one recipe batch.
                "amount": input_amount,

                # Ingredient quantity required per ONE output item.
                #
                # Example:
                # 4 Limestone -> 12 Concrete
                #
                # Limestone per Concrete = 4 / 12 = 0.333...
                "per_unit":
                    input_amount / output_amount
                    if output_amount
                    else 0,

                # Actual amount consumed by the number of complete
                # manufacturing batches required.
                "total":
                    input_amount * production_batches,

                # Recursively investigate how this ingredient
                # itself can be manufactured.
                "recipes": _build_dependency_recipes(
                    item.get("id"),
                    recipes_by_output,
                    material_by_id,
                    building_by_id,
                    set(),
                ),
            })

        # Store everything learned about this particular recipe.
        recipe_results.append({
            "recipe_id": recipe.get("id"),

            "building": building.get(
                "name",
                f'Building {recipe.get("producedIn")}',
            ),

            "inputs": inputs,

            "output_amount": output_amount,

            "production_batches": production_batches,

            "required_technology":
                recipe.get("reqTech", 0),

            # Recursively reduce all intermediate products to the
            # lowest-level/raw resources required to make the
            # requested number of output units.
            "basal_resources":
                _aggregate_basal_resources(
                    recipe,
                    production_batches,
                    recipes_by_output,
                    material_by_id,
                    price_by_id,
                    set(),
                ),
        })

    return {
        # According to the game's source field:
        # source == 1 appears to signify extraction.
        "source":
            "extraction"
            if material.get("source") == 1
            else "crafting",

        # If no recipes produce this material, or if all recipes
        # require no ingredients, treat it as a basal/basic resource.
        "is_basic_resource":
            not matching_recipes
            or all(
                not recipe.get("inputs")
                for recipe in matching_recipes
            ),

        "recipes": recipe_results,
    }


# -------------------------------------------------------------------
# QUANTITY FIELD NORMALIZATION
# -------------------------------------------------------------------

def _amount(item):
    """
    Extract a quantity from the game's JSON.

    Apparently the API may use either:
        "am"
    or:
        "a"

    for an amount field.

    Return 0 if neither is present.
    """

    return (
        item.get("am", item.get("a", 0))
        if item
        else 0
    )


# -------------------------------------------------------------------
# FULL RECURSIVE REDUCTION TO BASAL RESOURCES
# -------------------------------------------------------------------

def _aggregate_basal_resources(
    recipe,
    batches,
    recipes_by_output,
    material_by_id,
    price_by_id,
    visited,
):
    """
    Reduce an entire recipe tree to its basal/raw materials.

    Example conceptually:

        Prefab Kit
            Iron
            Concrete
                Limestone
                Silica

    becomes:

        Iron
        Limestone
        Silica

    Quantities are aggregated across all branches of the tree.
    """

    totals = {}

    # Start with each immediate ingredient of the recipe.
    for input_item in recipe.get("inputs", []):

        _add_basal_requirement(
            input_item.get("id"),

            # Ingredient quantity required for ALL top-level batches.
            _amount(input_item) * batches,

            recipes_by_output,
            material_by_id,
            price_by_id,
            totals,
            visited,
        )

    resources = []
    grand_total = 0

    # Convert accumulated material IDs into human-readable results.
    for material_id, quantity in totals.items():

        material = material_by_id.get(
            material_id,
            {},
        )

        market_item = price_by_id.get(
            material_id,
            {},
        )

        # Fetch current market price in cents.
        unit_price_cents = market_item.get(
            "currentPrice"
        )

        # Convert valid prices to dollars.
        unit_price = (
            unit_price_cents / 100
            if isinstance(
                unit_price_cents,
                (int, float),
            )
            and unit_price_cents >= 0
            else None
        )

        # Market value of the total required raw resource.
        total_price = (
            quantity * unit_price
            if unit_price is not None
            else None
        )

        if total_price is not None:
            grand_total += total_price

        resources.append({
            "id": material_id,

            "name": material.get(
                "name",
                f"Material {material_id}",
            ),

            "quantity": quantity,
            "unit_price": unit_price,
            "total_price": total_price,
        })

    return {
        "resources": resources,
        "grand_total": grand_total,
    }


def _add_basal_requirement(
    material_id,
    quantity,
    recipes_by_output,
    material_by_id,
    price_by_id,
    totals,
    visited,
):
    """
    Recursive helper.

    Given a required material:

    - if nothing produces it, treat it as basal/raw;
    - if it can be manufactured, expand its recipe;
    - repeat until only basal materials remain.

    totals accumulates the final quantities.
    """

    # Prevent infinite recursion if game data ever contains a
    # circular recipe dependency.
    #
    # Example:
    # A requires B
    # B requires A
    if material_id in visited:
        return

    # Find recipes capable of making this material.
    material_recipes = recipes_by_output.get(
        material_id,
        [],
    )

    # No recipe produces it:
    # therefore this is treated as a basal/raw resource.
    if not material_recipes:
        totals[material_id] = (
            totals.get(material_id, 0)
            + quantity
        )
        return

    # IMPORTANT:
    # If multiple recipes exist, this code currently chooses
    # ONLY THE FIRST ONE.
    recipe = material_recipes[0]

    output_amount = _amount(
        recipe.get("output")
    )

    # If the recipe has no usable output quantity,
    # fall back to treating the material as basal.
    if not output_amount:
        totals[material_id] = (
            totals.get(material_id, 0)
            + quantity
        )
        return

    # Determine how many full batches are required.
    #
    # Again this performs ceiling division.
    recipe_batches = -(
        -quantity // output_amount
    )

    # Add the current material to the branch's visited set.
    #
    # Using set union creates a NEW set rather than modifying the
    # existing one, which is important because different recursion
    # branches need independent cycle tracking.
    next_visited = visited | {material_id}

    # Expand each ingredient in this intermediate material.
    for input_item in recipe.get("inputs", []):

        _add_basal_requirement(
            input_item.get("id"),

            _amount(input_item)
            * recipe_batches,

            recipes_by_output,
            material_by_id,
            price_by_id,
            totals,
            next_visited,
        )


# -------------------------------------------------------------------
# RECIPE TREE FOR DISPLAY
# -------------------------------------------------------------------

def _build_dependency_recipes(
    material_id,
    recipes_by_output,
    material_by_id,
    building_by_id,
    visited,
):
    """
    Build a nested tree describing HOW each intermediate material
    can itself be produced.

    Unlike _aggregate_basal_resources(), which collapses everything
    into a final raw-material shopping list, this function preserves
    the hierarchy.

    This is what allows the UI eventually to display something like:

        Prefab Kit
        ├── Iron
        └── Concrete
            ├── Limestone
            └── Silica
    """

    # Circular-dependency protection.
    if material_id in visited:
        return []

    next_visited = visited | {material_id}

    dependency_recipes = []

    # Unlike _add_basal_requirement(), this loops through ALL recipes
    # capable of producing the material.
    for recipe in recipes_by_output.get(
        material_id,
        [],
    ):

        building = building_by_id.get(
            recipe.get("producedIn"),
            {},
        )

        dependency_inputs = []

        # Recursively describe each ingredient.
        for item in recipe.get("inputs", []):

            input_material = material_by_id.get(
                item.get("id"),
                {},
            )

            dependency_inputs.append({
                "id": item.get("id"),

                "name": input_material.get(
                    "name",
                    f'Material {item.get("id")}',
                ),

                "amount": _amount(item),

                # Recursively attach recipes for this ingredient.
                "recipes":
                    _build_dependency_recipes(
                        item.get("id"),
                        recipes_by_output,
                        material_by_id,
                        building_by_id,
                        next_visited,
                    ),
            })

        dependency_recipes.append({
            "recipe_id": recipe.get("id"),

            "building": building.get(
                "name",
                f'Building {recipe.get("producedIn")}',
            ),

            "output_amount":
                _amount(recipe.get("output")),

            "required_technology":
                recipe.get("reqTech", 0),

            "inputs": dependency_inputs,

            # A recipe with no inputs is effectively treated
            # as producing a basal/basic resource.
            "is_basic_resource":
                not recipe.get("inputs"),
        })

    return dependency_recipes