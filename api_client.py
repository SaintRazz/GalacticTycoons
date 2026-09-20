import requests

API_URL = "https://api.g2.galactictycoons.com/public/exchange/mat-prices"
GAME_DATA_URL = "https://api.g2.galactictycoons.com/gamedata.json"
REQUEST_TIMEOUT_SECONDS = 15


class ApiClientError(Exception):
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


class CommodityNotFoundError(ApiClientError):
    def __init__(self):
        super().__init__("Commodity was not found.", status_code=404)


def get_commodity_price(commodity_name, api_key, desired_quantity=1):
    """Return current price and production dependencies for a named material."""
    price_data = _get_json(API_URL, api_key)
    game_data = _get_json(GAME_DATA_URL, api_key)

    try:
        prices = price_data["prices"]
        materials = game_data["materials"]
        recipes = game_data["recipes"]
        buildings = game_data["buildings"]
    except (TypeError, KeyError):
        raise ApiClientError("The Galactic Tycoons API returned an unexpected response.")

    match = _find_named_item(prices, commodity_name, "matName")

    if not match:
        raise CommodityNotFoundError()

    current_price_cents = match.get("currentPrice")
    if not isinstance(current_price_cents, (int, float)) or current_price_cents < 0:
        raise ApiClientError(
            f'No current market listing is available for "{match.get("matName", commodity_name)}".',
            404,
        )

    material = next((item for item in materials if item.get("id") == match.get("matId")), None)
    if not material:
        raise ApiClientError("The selected material is missing from game data.")

    return {
        "commodity": match.get("matName", commodity_name),
        "quantity": desired_quantity,
        "price_per_unit": current_price_cents / 100,
        "price_cents": current_price_cents,
        "total_price": current_price_cents * desired_quantity / 100,
        "analysis": _build_production_analysis(
            material, recipes, buildings, materials, prices, desired_quantity
        ),
    }


def _get_json(url, api_key):
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise ApiClientError("Could not connect to the Galactic Tycoons API.") from error

    if not response.ok:
        if response.status_code in (401, 403):
            raise ApiClientError("The API key was rejected by Galactic Tycoons.", 502)
        if response.status_code == 429:
            raise ApiClientError("The Galactic Tycoons API rate limit was reached.", 502)
        raise ApiClientError("The Galactic Tycoons API returned an error.", 502)

    try:
        return response.json()
    except ValueError as error:
        raise ApiClientError("The Galactic Tycoons API returned invalid JSON.") from error


def _find_named_item(items, requested_name, name_key):
    requested = requested_name.casefold()
    exact_match = next(
        (item for item in items if str(item.get(name_key, "")).casefold() == requested),
        None,
    )
    return exact_match or next(
        (item for item in items if requested in str(item.get(name_key, "")).casefold()),
        None,
    )


def _build_production_analysis(
    material, recipes, buildings, materials, prices, desired_quantity=1
):
    material_by_id = {item.get("id"): item for item in materials}
    building_by_id = {item.get("id"): item for item in buildings}
    price_by_id = {item.get("matId"): item for item in prices}
    recipes_by_output = {}
    for recipe in recipes:
        recipes_by_output.setdefault(recipe.get("output", {}).get("id"), []).append(recipe)

    matching_recipes = recipes_by_output.get(material.get("id"), [])
    recipe_results = []
    for recipe in matching_recipes:
        output_amount = _amount(recipe.get("output"))
        production_batches = max(1, -(-desired_quantity // output_amount)) if output_amount else 1
        building = building_by_id.get(recipe.get("producedIn"), {})
        inputs = []
        for item in recipe.get("inputs", []):
            input_amount = _amount(item)
            input_material = material_by_id.get(item.get("id"), {})
            inputs.append({
                "id": item.get("id"),
                "name": input_material.get("name", f'Material {item.get("id")}'),
                "amount": input_amount,
                "per_unit": input_amount / output_amount if output_amount else 0,
                "total": input_amount * production_batches,
                "recipes": _build_dependency_recipes(
                    item.get("id"), recipes_by_output, material_by_id, building_by_id, set()
                ),
            })

        recipe_results.append({
            "recipe_id": recipe.get("id"),
            "building": building.get("name", f'Building {recipe.get("producedIn")}'),
            "inputs": inputs,
            "output_amount": output_amount,
            "production_batches": production_batches,
            "required_technology": recipe.get("reqTech", 0),
            "basal_resources": _aggregate_basal_resources(
                recipe, production_batches, recipes_by_output, material_by_id, price_by_id, set()
            ),
        })

    return {
        "source": "extraction" if material.get("source") == 1 else "crafting",
        "is_basic_resource": not matching_recipes or all(not recipe.get("inputs") for recipe in matching_recipes),
        "recipes": recipe_results,
    }


def _amount(item):
    return item.get("am", item.get("a", 0)) if item else 0


def _aggregate_basal_resources(
    recipe, batches, recipes_by_output, material_by_id, price_by_id, visited
):
    totals = {}
    for input_item in recipe.get("inputs", []):
        _add_basal_requirement(
            input_item.get("id"),
            _amount(input_item) * batches,
            recipes_by_output,
            material_by_id,
            price_by_id,
            totals,
            visited,
        )

    resources = []
    grand_total = 0
    for material_id, quantity in totals.items():
        material = material_by_id.get(material_id, {})
        market_item = price_by_id.get(material_id, {})
        unit_price_cents = market_item.get("currentPrice")
        unit_price = unit_price_cents / 100 if isinstance(unit_price_cents, (int, float)) and unit_price_cents >= 0 else None
        total_price = quantity * unit_price if unit_price is not None else None
        if total_price is not None:
            grand_total += total_price
        resources.append({
            "id": material_id,
            "name": material.get("name", f"Material {material_id}"),
            "quantity": quantity,
            "unit_price": unit_price,
            "total_price": total_price,
        })

    return {"resources": resources, "grand_total": grand_total}


def _add_basal_requirement(
    material_id, quantity, recipes_by_output, material_by_id, price_by_id, totals, visited
):
    if material_id in visited:
        return

    material_recipes = recipes_by_output.get(material_id, [])
    if not material_recipes:
        totals[material_id] = totals.get(material_id, 0) + quantity
        return

    recipe = material_recipes[0]
    output_amount = _amount(recipe.get("output"))
    if not output_amount:
        totals[material_id] = totals.get(material_id, 0) + quantity
        return

    recipe_batches = -(-quantity // output_amount)
    next_visited = visited | {material_id}
    for input_item in recipe.get("inputs", []):
        _add_basal_requirement(
            input_item.get("id"),
            _amount(input_item) * recipe_batches,
            recipes_by_output,
            material_by_id,
            price_by_id,
            totals,
            next_visited,
        )


def _build_dependency_recipes(material_id, recipes_by_output, material_by_id, building_by_id, visited):
    if material_id in visited:
        return []

    next_visited = visited | {material_id}
    dependency_recipes = []
    for recipe in recipes_by_output.get(material_id, []):
        building = building_by_id.get(recipe.get("producedIn"), {})
        dependency_inputs = []
        for item in recipe.get("inputs", []):
            input_material = material_by_id.get(item.get("id"), {})
            dependency_inputs.append({
                "id": item.get("id"),
                "name": input_material.get("name", f'Material {item.get("id")}'),
                "amount": _amount(item),
                "recipes": _build_dependency_recipes(
                    item.get("id"), recipes_by_output, material_by_id, building_by_id, next_visited
                ),
            })
        dependency_recipes.append({
            "recipe_id": recipe.get("id"),
            "building": building.get("name", f'Building {recipe.get("producedIn")}'),
            "output_amount": _amount(recipe.get("output")),
            "required_technology": recipe.get("reqTech", 0),
            "inputs": dependency_inputs,
            "is_basic_resource": not recipe.get("inputs"),
        })
    return dependency_recipes
