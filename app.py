from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import secrets

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///minerco.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ---------------- Models ---------------- #
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    silver = db.Column(db.Integer, default=0)
    pickaxe = db.Column(db.String(32), default="coal")
    inventory = db.Column(db.JSON, default=lambda: {
        "coal": 10, "copper": 0, "bronze": 0,
        "silver": 0, "gold": 0, "diamond": 0, "emerald": 0
    })
    powerups = db.Column(db.JSON, default=list)
    pets = db.Column(db.JSON, default=list)
    used_codes = db.Column(db.JSON, default=list)
    is_admin = db.Column(db.Boolean, default=False)
    session_token = db.Column(db.String(128), nullable=True)

def auth_user():
    token = request.headers.get("Authorization")
    if not token:
        return None
    return User.query.filter_by(session_token=token).first()

# ---------------- Routes ---------------- #
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    username, password = data["username"], data["password"]

    user = User.query.filter_by(username=username).first()
    if user is None:
        user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
    else:
        if not check_password_hash(user.password_hash, password):
            return jsonify({"error": "Invalid password"}), 401

    user.session_token = secrets.token_hex(16)
    db.session.commit()
    return jsonify({"token": user.session_token, "username": user.username, "is_admin": user.is_admin})

@app.route("/api/state", methods=["GET"])
def get_state():
    user = auth_user()
    if not user: return jsonify({"error": "Unauthorized"}), 401
    return jsonify({
        "username": user.username,
        "inventory": user.inventory,
        "silver": user.silver,
        "pickaxe": user.pickaxe,
        "powerups": user.powerups,
        "pets": user.pets
    })

@app.route("/api/mine", methods=["POST"])
def mine():
    user = auth_user()
    if not user: return jsonify({"error": "Unauthorized"}), 401
    ore = request.json.get("ore", "coal")
    inv = user.inventory
    inv[ore] = inv.get(ore, 0) + 1
    user.inventory = inv
    db.session.commit()
    return jsonify({"inventory": user.inventory})

@app.route("/api/leaderboard", methods=["GET"])
def leaderboard():
    top = User.query.order_by(User.silver.desc()).limit(10).all()
    return jsonify([{"username": u.username, "silver": u.silver} for u in top])

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    user = auth_user()
    if not user: return jsonify({"error": "Unauthorized"}), 401
    code = request.json.get("code")
    if code == "danielis67":
        user.is_admin = True
        db.session.commit()
        return jsonify({"message": "Admin unlocked"})
    return jsonify({"error": "Wrong admin code"}), 403

@app.route("/api/admin/money", methods=["POST"])
def admin_money():
    user = auth_user()
    if not user or not user.is_admin:
        return jsonify({"error": "Forbidden"}), 403
    user.silver += 999999
    db.session.commit()
    return jsonify({"silver": user.silver})

# ---------------- Run ---------------- #
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=1000, debug=True)
