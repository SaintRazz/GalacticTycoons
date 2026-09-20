from flask import Flask, jsonify, render_template, request

from api_client import ApiClientError, CommodityNotFoundError, get_commodity_price

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

    if not commodity:
        return jsonify({"error": "Enter a commodity or product name."}), 400
    if not api_key:
        return jsonify({"error": "Enter your Galactic Tycoons API key."}), 400
    try:
        desired_quantity = int(desired_quantity)
    except (TypeError, ValueError):
        return jsonify({"error": "Enter a whole-number quantity greater than zero."}), 400
    if desired_quantity <= 0:
        return jsonify({"error": "Enter a whole-number quantity greater than zero."}), 400

    try:
        result = get_commodity_price(commodity, api_key, desired_quantity)
    except CommodityNotFoundError:
        return jsonify({"error": f'No matching commodity found for "{commodity}".'}), 404
    except ApiClientError as error:
        return jsonify({"error": str(error)}), error.status_code

    return jsonify(result)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
