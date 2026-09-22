import math
import requests


API_URL = "https://api.g2.galactictycoons.com/public/exchange/mat-prices"
GAME_DATA_URL = "https://api.g2.galactictycoons.com/gamedata.json"
REQUEST_TIMEOUT_SECONDS = 15


class ApiClientError(Exception):
    """General error raised while communicating with Galactic Tycoons."""

    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


class CommodityNotFoundError(ApiClientError):
    """Raised when the requested commodity cannot be found."""

    def __init__(self):
        super().__init__("Commodity was not found.", status_code=404)


class RecipeChoiceRequired(Exception):
    """
    Raised when a material has multiple possible production recipes
    and the user has not yet selected which recipe should be used.

    The UI can catch this condition, display the available recipes,
    and resubmit the original query with a recipe choice.
    """

    def __init__(self, material, options):
        material_name = material.get(
            "name",
            f'Material {material.get("id")}',
        )

        super().__init__(
            f'Recipe choice required for "{material_name}".'
        )

        self.material_id = material.get("id")
        self.material_name = material_name
        self.options = options


def get_commodity_price(
    commodity_name,
    api_key,
    desired_quantity=1,
    recipe_choices=None,
):
    """
    Return current market-price information and production analysis
    for a named material.

    recipe_choices is a dictionary containing recipe decisions made
    by the user during the current query.

    Example:

        {
            "42": 107,
            "61": 203,
            "88": None
        }

    Meaning:

        material 42 -> use recipe 107
        material 61 -> use recipe 203
        material 88 -> treat as externally sourced
    """

    if recipe_choices is None:
        recipe_choices = {}

    price_data = _get_json(API_URL, api_key)
    game_data = _get_json(GAME_DATA_URL, api_key)

    try:
        prices = price_data["prices"]
        materials = game_data["materials"]
        recipes = game_data["recipes"]
        buildings = game_data["buildings"]
    except (TypeError, KeyError):
        raise ApiClientError(
            "The Galactic Tycoons API returned an unexpected response."
        )

    match = _find_named_item(
        prices,
        commodity_name,
        "matName",
    )

    if not match:
        raise CommodityNotFoundError()

    current_price_cents = match.get("currentPrice")

    if (
        not isinstance(current_price_cents, (int, float))
        or current_price_cents < 0
    ):
        raise ApiClientError(
            f'No current market listing is available for '
            f'"{match.get("matName", commodity_name)}".',
            404,
        )

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

    return {
        "commodity": match.get(
            "matName",
            commodity_name,
        ),
        "quantity": desired_quantity,
        "price_per_unit": current_price_cents / 100,
        "price_cents": current_price_cents,
        "total_price":
            current_price_cents * desired_quantity / 100,
        "analysis": _build_production_analysis(
            material,
            recipes,
            buildings,
            materials,
            prices,
            desired_quantity,
            recipe_choices,
        ),
    }


def _get_json(url, api_key):
    """Perform an authenticated GET request and return parsed JSON."""

    try:
        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {api_key}"
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    except requests.RequestException as error:
        raise ApiClientError(
            "Could not connect to the Galactic Tycoons API."
        ) from error

    if not response.ok:

        if response.status_code in (401, 403):
            raise ApiClientError(
                "The API key was rejected by Galactic Tycoons.",
                502,
            )

        if response.status_code == 429:
            raise ApiClientError(
                "The Galactic Tycoons API rate limit was reached.",
                502,
            )

        raise ApiClientError(
            "The Galactic Tycoons API returned an error.",
            502,
        )

    try:
        return response.json()

    except ValueError as error:
        raise ApiClientError(
            "The Galactic Tycoons API returned invalid JSON."
        ) from error


def _find_named_item(items, requested_name, name_key):
    """
    Find an item by name.

    Exact case-insensitive matches are preferred.
    If none exists, the first partial match is returned.
    """

    requested = requested_name.casefold()

    exact_match = next(
        (
            item
            for item in items
            if str(
                item.get(name_key, "")
            ).casefold() == requested
        ),
        None,
    )

    return exact_match or next(
        (
            item
            for item in items
            if requested
            in str(
                item.get(name_key, "")
            ).casefold()
        ),
        None,
    )


def _build_production_analysis(
    material,
    recipes,
    buildings,
    materials,
    prices,
    desired_quantity=1,
    recipe_choices=None,
):
    """
    Build the production analysis for the requested material.

    Unlike the earlier implementation, this analyzes only ONE recipe
    path at each decision point.

    If multiple recipes exist and no choice has yet been supplied,
    RecipeChoiceRequired is raised.
    """

    if recipe_choices is None:
        recipe_choices = {}

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

    recipes_by_output = {}

    for recipe in recipes:
        output_id = recipe.get(
            "output",
            {},
        ).get("id")

        recipes_by_output.setdefault(
            output_id,
            [],
        ).append(recipe)

    material_id = material.get("id")

    matching_recipes = recipes_by_output.get(
        material_id,
        [],
    )

    # Select the one recipe path that should be used.
    #
    # This may raise RecipeChoiceRequired if more than one exists.
    selected_recipe = _select_recipe(
        material_id,
        matching_recipes,
        material_by_id,
        building_by_id,
        recipe_choices,
    )

    # If no recipe exists, or the user explicitly chose "None",
    # the queried material itself is considered external/basal.
    if selected_recipe is None:

        market_item = price_by_id.get(
            material_id,
            {},
        )

        resource = _build_priced_resource(
            material_id,
            desired_quantity,
            material_by_id,
            market_item,
            classification="external",
        )

        grand_total = (
            resource["total_price"]
            if resource["total_price"] is not None
            else 0
        )

        return {
            "source": (
                "extraction"
                if material.get("source") == 1
                else "external"
            ),
            "is_basic_resource": True,
            "recipes": [],
            "basal_resources": {
                "resources": [resource],
                "grand_total": grand_total,
            },
            "facilities": [],
        }

    output_amount = _amount(
        selected_recipe.get("output")
    )

    production_batches = (
        max(
            1,
            math.ceil(
                desired_quantity / output_amount
            ),
        )
        if output_amount
        else 1
    )

    building = building_by_id.get(
        selected_recipe.get("producedIn"),
        {},
    )

    building_name = building.get(
        "name",
        f'Building {selected_recipe.get("producedIn")}',
    )

    inputs = []

    for item in selected_recipe.get("inputs", []):

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
            "amount": input_amount,
            "per_unit": (
                input_amount / output_amount
                if output_amount
                else 0
            ),
            "total":
                input_amount * production_batches,
            "recipes":
                _build_dependency_recipes(
                    item.get("id"),
                    recipes_by_output,
                    material_by_id,
                    building_by_id,
                    recipe_choices,
                    set(),
                ),
        })

    basal_resources = _aggregate_basal_resources(
        selected_recipe,
        production_batches,
        recipes_by_output,
        material_by_id,
        building_by_id,
        price_by_id,
        recipe_choices,
        set(),
    )

    facilities = set(
        basal_resources.get(
            "facilities",
            [],
        )
    )

    # The top-level production building is required as well.
    if building_name:
        facilities.add(building_name)

    recipe_result = {
        "recipe_id": selected_recipe.get("id"),
        "building": building_name,
        "inputs": inputs,
        "output_amount": output_amount,
        "production_batches": production_batches,
        "required_technology":
            selected_recipe.get("reqTech", 0),
        "basal_resources": {
            "resources":
                basal_resources["resources"],
            "grand_total":
                basal_resources["grand_total"],
        },
    }

    return {
        "source": (
            "extraction"
            if material.get("source") == 1
            else "crafting"
        ),
        "is_basic_resource":
            not selected_recipe.get("inputs"),
        "recipes": [recipe_result],
        "basal_resources": {
            "resources":
                basal_resources["resources"],
            "grand_total":
                basal_resources["grand_total"],
        },
        "facilities": sorted(facilities),
    }


def _select_recipe(
    material_id,
    material_recipes,
    material_by_id,
    building_by_id,
    recipe_choices,
):
    """
    Decide which production recipe should be used for a material.

    Rules:

    - No recipes:
        return None.

    - Exactly one recipe:
        use it automatically.

    - Multiple recipes:
        require user selection.

    - User selects None/"none":
        treat the material as externally sourced for this query.
    """

    material = material_by_id.get(
        material_id,
        {},
    )

    if not material_recipes:
        return None

    if len(material_recipes) == 1:
        return material_recipes[0]

    # JSON object keys generally arrive as strings.
    # Supporting both string and integer keys makes the function
    # easier to use from Python as well.
    missing = object()

    choice = recipe_choices.get(
        str(material_id),
        recipe_choices.get(
            material_id,
            missing,
        ),
    )

    # No decision has yet been supplied.
    if choice is missing:

        options = []

        for recipe in material_recipes:

            building = building_by_id.get(
                recipe.get("producedIn"),
                {},
            )

            recipe_inputs = []

            for item in recipe.get("inputs", []):

                input_material = material_by_id.get(
                    item.get("id"),
                    {},
                )

                recipe_inputs.append({
                    "id": item.get("id"),
                    "name": input_material.get(
                        "name",
                        f'Material {item.get("id")}',
                    ),
                    "amount": _amount(item),
                })

            options.append({
                "recipe_id": recipe.get("id"),
                "building": building.get(
                    "name",
                    f'Building {recipe.get("producedIn")}',
                ),
                "output_amount":
                    _amount(
                        recipe.get("output")
                    ),
                "required_technology":
                    recipe.get("reqTech", 0),
                "inputs": recipe_inputs,
            })

        raise RecipeChoiceRequired(
            material,
            options,
        )

    # Explicitly choosing None means:
    # this company cannot or does not wish to produce this material
    # internally for the current query.
    if choice is None:
        return None

    if (
        isinstance(choice, str)
        and choice.casefold() == "none"
    ):
        return None

    selected = next(
        (
            recipe
            for recipe in material_recipes
            if str(
                recipe.get("id")
            ) == str(choice)
        ),
        None,
    )

    if selected is None:
        raise ApiClientError(
            f'Invalid recipe choice for '
            f'"{material.get("name", material_id)}".'
        )

    return selected


def _amount(item):
    """Read a quantity stored as either 'am' or 'a'."""

    return (
        item.get(
            "am",
            item.get("a", 0),
        )
        if item
        else 0
    )


def _aggregate_basal_resources(
    recipe,
    batches,
    recipes_by_output,
    material_by_id,
    building_by_id,
    price_by_id,
    recipe_choices,
    visited,
):
    """
    Reduce the SELECTED production path to its final raw/external
    resources.

    Unselected alternative recipes are never decomposed.
    """

    totals = {}

    externalized = set()
    extracted = set()
    facilities = set()

    for input_item in recipe.get(
        "inputs",
        [],
    ):

        _add_basal_requirement(
            input_item.get("id"),
            _amount(input_item) * batches,
            recipes_by_output,
            material_by_id,
            building_by_id,
            totals,
            externalized,
            extracted,
            facilities,
            recipe_choices,
            visited,
        )

    resources = []
    grand_total = 0

    for material_id, quantity in totals.items():

        market_item = price_by_id.get(
            material_id,
            {},
        )

        if material_id in externalized:
            classification = "external"

        elif material_id in extracted:
            classification = "basal"

        else:
            classification = "basal"

        resource = _build_priced_resource(
            material_id,
            quantity,
            material_by_id,
            market_item,
            classification,
        )

        if resource["total_price"] is not None:
            grand_total += resource[
                "total_price"
            ]

        resources.append(resource)

    resources.sort(
        key=lambda item: item["name"]
    )

    return {
        "resources": resources,
        "grand_total": grand_total,
        "facilities": sorted(facilities),
    }


def _add_basal_requirement(
    material_id,
    quantity,
    recipes_by_output,
    material_by_id,
    building_by_id,
    totals,
    externalized,
    extracted,
    facilities,
    recipe_choices,
    visited,
):
    """
    Recursively decompose one material.

    The recursion stops when:

    1. no recipe exists;
    2. the user selected "None";
    3. the selected recipe has no inputs.

    In case 1 or 2, the material is treated as externally sourced.

    In case 3, it is treated as a raw/basal extracted resource and
    the corresponding extraction facility is recorded.
    """

    if material_id in visited:
        return

    material_recipes = recipes_by_output.get(
        material_id,
        [],
    )

    # If multiple recipes exist this may raise
    # RecipeChoiceRequired and halt the current analysis.
    selected_recipe = _select_recipe(
        material_id,
        material_recipes,
        material_by_id,
        building_by_id,
        recipe_choices,
    )

    # No selected recipe:
    #
    # - no recipe exists, or
    # - user explicitly selected "None".
    #
    # In either case this becomes an externally obtained input.
    if selected_recipe is None:

        totals[material_id] = (
            totals.get(material_id, 0)
            + quantity
        )

        externalized.add(material_id)
        return

    building = building_by_id.get(
        selected_recipe.get("producedIn"),
        {},
    )

    building_name = building.get(
        "name",
        f'Building {selected_recipe.get("producedIn")}',
    )

    if building_name:
        facilities.add(building_name)

    inputs = selected_recipe.get(
        "inputs",
        [],
    )

    # A valid recipe with no inputs represents a basal extraction
    # step such as mining/farming/etc.
    if not inputs:

        totals[material_id] = (
            totals.get(material_id, 0)
            + quantity
        )

        extracted.add(material_id)
        return

    output_amount = _amount(
        selected_recipe.get("output")
    )

    if not output_amount:

        totals[material_id] = (
            totals.get(material_id, 0)
            + quantity
        )

        externalized.add(material_id)
        return

    recipe_batches = math.ceil(
        quantity / output_amount
    )

    next_visited = (
        visited | {material_id}
    )

    for input_item in inputs:

        _add_basal_requirement(
            input_item.get("id"),
            _amount(input_item)
            * recipe_batches,
            recipes_by_output,
            material_by_id,
            building_by_id,
            totals,
            externalized,
            extracted,
            facilities,
            recipe_choices,
            next_visited,
        )


def _build_dependency_recipes(
    material_id,
    recipes_by_output,
    material_by_id,
    building_by_id,
    recipe_choices,
    visited,
):
    """
    Build the dependency tree for ONLY the selected recipe path.

    If multiple recipes exist and the user has not chosen one,
    RecipeChoiceRequired is raised.

    Unselected recipes are not recursively expanded.
    """

    if material_id in visited:
        return []

    material_recipes = recipes_by_output.get(
        material_id,
        [],
    )

    selected_recipe = _select_recipe(
        material_id,
        material_recipes,
        material_by_id,
        building_by_id,
        recipe_choices,
    )

    # External / explicitly unavailable material.
    if selected_recipe is None:
        return []

    next_visited = (
        visited | {material_id}
    )

    building = building_by_id.get(
        selected_recipe.get("producedIn"),
        {},
    )

    dependency_inputs = []

    for item in selected_recipe.get(
        "inputs",
        [],
    ):

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
            "recipes":
                _build_dependency_recipes(
                    item.get("id"),
                    recipes_by_output,
                    material_by_id,
                    building_by_id,
                    recipe_choices,
                    next_visited,
                ),
        })

    return [{
        "recipe_id":
            selected_recipe.get("id"),

        "building":
            building.get(
                "name",
                f'Building {selected_recipe.get("producedIn")}',
            ),

        "output_amount":
            _amount(
                selected_recipe.get("output")
            ),

        "required_technology":
            selected_recipe.get(
                "reqTech",
                0,
            ),

        "inputs":
            dependency_inputs,

        "is_basic_resource":
            not selected_recipe.get("inputs"),
    }]


def _build_priced_resource(
    material_id,
    quantity,
    material_by_id,
    market_item,
    classification,
):
    """
    Build the final display object for one basal/external resource,
    including its current market price and total market value.
    """

    material = material_by_id.get(
        material_id,
        {},
    )

    unit_price_cents = market_item.get(
        "currentPrice"
    )

    unit_price = (
        unit_price_cents / 100
        if isinstance(
            unit_price_cents,
            (int, float),
        )
        and unit_price_cents >= 0
        else None
    )

    total_price = (
        quantity * unit_price
        if unit_price is not None
        else None
    )

    return {
        "id": material_id,

        "name": material.get(
            "name",
            f"Material {material_id}",
        ),

        "quantity": quantity,

        "classification":
            classification,

        "unit_price":
            unit_price,

        "total_price":
            total_price,
    }