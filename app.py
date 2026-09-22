from flask import Flask, jsonify, render_template, request

from api_client import (
    ApiClientError,
    CommodityNotFoundError,
    RecipeChoiceRequired,
    get_commodity_price,
)

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/price")
def price_lookup():
    payload = request.get_json(silent=True) or {}

    commodity = str(payload.get("commodity", "")).strip()
    api_key = str(payload.get("api_key", "")).strip()
    desired_quantity = payload.get("desired_quantity")

    # Dictionary of recipe decisions already made by the user
    # during this query.
    #
    # Example:
    # {
    #     "42": 107,
    #     "61": None
    # }
    #
    # Meaning:
    # material 42 -> use recipe 107
    # material 61 -> treat as externally sourced
    recipe_choices = payload.get("recipe_choices") or {}

    if not commodity:
        return jsonify({
            "error": "Enter a commodity or product name."
        }), 400

    if not api_key:
        return jsonify({
            "error": "Enter your Galactic Tycoons API key."
        }), 400

    try:
        desired_quantity = int(desired_quantity)

    except (TypeError, ValueError):
        return jsonify({
            "error": "Enter a whole-number quantity greater than zero."
        }), 400

    if desired_quantity <= 0:
        return jsonify({
            "error": "Enter a whole-number quantity greater than zero."
        }), 400

    if not isinstance(recipe_choices, dict):
        return jsonify({
            "error": "Recipe choices must be supplied as a JSON object."
        }), 400

    try:
        result = get_commodity_price(
            commodity,
            api_key,
            desired_quantity,
            recipe_choices=recipe_choices,
        )

    except RecipeChoiceRequired as choice:
        # This is not really an error.
        #
        # The production engine has reached a material that has
        # multiple possible recipes and needs the user to select one
        # before analysis can continue.
        return jsonify({
            "status": "choice_required",
            "material_id": choice.material_id,
            "material_name": choice.material_name,
            "recipes": choice.options,
        }), 200

    except CommodityNotFoundError:
        return jsonify({
            "error": f'No matching commodity found for "{commodity}".'
        }), 404

    except ApiClientError as error:
        return jsonify({
            "error": str(error)
        }), error.status_code

    # Normal completed analysis.
    return jsonify({
        "status": "complete",
        "result": result,
    })


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
    )