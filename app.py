from __future__ import annotations
from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import secrets, time

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///minerco.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# --- DB ---
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    silver = db.Column(db.Integer, default=0)
    pickaxe = db.Column(db.String(32), default="coal")
    sword = db.Column(db.String(32), default="none")
    inventory = db.Column(db.JSON, default=lambda: {
        "coal":10,"copper":0,"bronze":0,"silver":0,"gold":0,"diamond":0,"emerald":0,"rainbow":0,"goldrush":0
    })
    active_powerups = db.Column(db.JSON, default=dict)  # {strength:{mult,until}, luck:{mult,until}}
    pets = db.Column(db.JSON, default=list)             # [{id,name,rarity,effect}]
    used_codes = db.Column(db.JSON, default=list)
    achievements = db.Column(db.JSON, default=list)
    stats = db.Column(db.JSON, default=lambda: {
        "oresMined":0, "crits":0, "silverEarned":0, "silverSpent":0,
        "total": {"coal":0,"copper":0,"bronze":0,"silver":0,"gold":0,"diamond":0,"emerald":0,"rainbow":0}
    })
    last_daily = db.Column(db.Integer, default=0)  # epoch ms
    is_admin = db.Column(db.Boolean, default=False)
    session_token = db.Column(db.String(128), nullable=True)

# --- Game Data (server-authoritative) ---
ORES = ["coal","copper","bronze","silver","gold","diamond","emerald","rainbow","goldrush"]
ORE_VALUES = {"coal":1,"copper":2,"bronze":3,"silver":5,"gold":8,"diamond":15,"emerald":30,"rainbow":20,"goldrush":0}
ORE_REQ = {"coal":"coal","copper":"copper","bronze":"bronze","silver":"silver","gold":"gold","diamond":"diamond","emerald":"emerald","rainbow":"diamond","goldrush":"diamond"}
PICKAXE_COSTS = {
    "coal": {"coal":10},
    "copper": {"coal":20},
    "bronze": {"copper":30},
    "silver": {"bronze":40},
    "gold": {"silver":50},
    "diamond": {"gold":60},
    "emerald": {"diamond":100},
}
POWERUPS = {"2x Strength": {"key":"strength","mult":2,"secs":120},
            "2x Luck": {"key":"luck","mult":2,"secs":120}}
SWORDS = {"none":0, "iron":5, "steel":12, "mythril":20}
PET_POOL = [
    {"id":"mole","name":"Mole","rarity":"common","effect":{"strength":1.25}},
    {"id":"cat","name":"Lucky Cat","rarity":"rare","effect":{"luck":1.5}},
    {"id":"parrot","name":"Parrot","rarity":"uncommon","effect":{"sell":1.1}},
    {"id":"fox","name":"Amber Fox","rarity":"epic","effect":{"crit":0.08}},
    {"id":"tortoise","name":"Tortoise","rarity":"common","effect":{"sustain":1}},
]
PET_WEIGHTS = {"common":56, "uncommon":24, "rare":14, "epic":6}
CODE_REWARDS = {
    "WELCOME": lambda u: {"silver":200, "message":"Welcome bonus: +200 Silver"},
    "LUCKY":   lambda u: {"powerup":"2x Luck", "message":"+2x Luck (120s)"},
    "SHINY":   lambda u: {"item":{"diamond":5}, "message":"Diamonds x5"},
    "FREEPET": lambda u: {"freepet":True, "message":"Free pet spin"},
    "GOLDRUSH":lambda u: {"event":"gold", "message":"Gold Rush for 3 min!"},
}

# --- Helpers ---
now_ms = lambda: int(time.time()*1000)

def auth_user() -> User|None:
    token = request.headers.get("Authorization")
    return User.query.filter_by(session_token=token).first() if token else None

def tier_index(ore: str) -> int:
    return ORES.index(ore)

def can_mine(user: User, ore: str) -> bool:
    return tier_index(user.pickaxe) >= tier_index(ORE_REQ[ore])

def power_mult(user: User, key: str) -> float:
    m = 1.0
    p = user.active_powerups.get(key)
    if p and p.get("until",0) > now_ms():
        m *= float(p.get("mult",1))
    # pet effects
    if key == "strength":
        for pt in user.pets: m *= float(pt.get("effect",{}).get("strength",1))
    if key == "luck":
        for pt in user.pets: m *= float(pt.get("effect",{}).get("luck",1))
    return m

def sell_mult(user: User) -> float:
    m = 1.0
    for pt in user.pets:
        m *= float(pt.get("effect",{}).get("sell",1))
    return m

def crit_chance(user: User) -> float:
    chance = float(SWORDS.get(user.sword, 0))
    for pt in user.pets:
        chance += float(pt.get("effect",{}).get("crit",0))*100
    return chance

def add_ore(user: User, ore: str, qty: int) -> None:
    inv = dict(user.inventory)
    inv[ore] = int(inv.get(ore,0) + qty)
    user.inventory = inv
    # stats
    st = dict(user.stats)
    st["oresMined"] = int(st.get("oresMined",0) + qty)
    tot = dict(st.get("total",{}))
    if ore in tot:
        tot[ore] = int(tot.get(ore,0) + qty)
    st["total"] = tot
    user.stats = st


def serialize(user: User) -> dict:
    return {
        "username": user.username,
        "silver": user.silver,
        "pickaxe": user.pickaxe,
        "sword": user.sword,
        "inventory": user.inventory,
        "activePowerups": user.active_powerups,
        "pets": user.pets,
        "usedCodes": user.used_codes,
        "achievements": user.achievements,
        "stats": user.stats,
        "lastDaily": user.last_daily,
        "is_admin": user.is_admin,
    }

# --- Routes ---
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    username, password = data.get("username",""), data.get("password","")
    if not username or not password:
        return jsonify({"error":"Username and password required"}), 400
    user = User.query.filter_by(username=username).first()
    if user is None:
        user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
    else:
        if not check_password_hash(user.password_hash, password):
            return jsonify({"error":"Invalid password"}), 401
    user.session_token = secrets.token_hex(16)
    db.session.commit()
    return jsonify({"token": user.session_token, **serialize(user)})

@app.route("/api/state", methods=["GET"])
def state():
    user = auth_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    return jsonify(serialize(user))

@app.route("/api/mine", methods=["POST"])
def api_mine():
    user = auth_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    ore = request.json.get("ore","coal")
    if ore not in ORES:
        return jsonify({"error":"Unknown ore"}), 400
    if not can_mine(user, ore):
        return jsonify({"error":"Locked ore for your pickaxe"}), 403
    # yield
    qty = max(1, int(1 * power_mult(user, "strength")))
    # crit
    import random
    if random.random()*100 < crit_chance(user):
        qty *= 2
        st = dict(user.stats); st["crits"] = int(st.get("crits",0)+1); user.stats = st
    # special ores
    if ore == "rainbow":
        bonus = ORES[:7][random.randint(0,6)]
        add_ore(user, bonus, qty)
    elif ore == "goldrush":
        add_ore(user, "gold", qty*2)
    else:
        add_ore(user, ore, qty)
    db.session.commit()
    return jsonify(serialize(user))

@app.route("/api/sell_all", methods=["POST"])
def sell_all():
    user = auth_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    inv = dict(user.inventory)
    gain = 0
    for ore, val in ORE_VALUES.items():
        if ore == "goldrush":
            continue
        qty = int(inv.get(ore,0))
        if qty>0:
            gain += qty * val
            inv[ore] = 0
    mult = sell_mult(user)
    gain = int(gain * mult)
    user.inventory = inv
    user.silver += gain
    st = dict(user.stats); st["silverEarned"] = int(st.get("silverEarned",0) + gain); user.stats = st
    db.session.commit()
    return jsonify({"silver": user.silver, "gain": gain, **serialize(user)})

@app.route("/api/shop/pickaxe", methods=["POST"])
def shop_pickaxe():
    user = auth_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    tier = request.json.get("tier")
    if tier not in PICKAXE_COSTS:
        return jsonify({"error":"Unknown pickaxe"}), 400
    # already owned or higher
    if tier_index(user.pickaxe) >= tier_index(tier):
        return jsonify({"message":"Already owned", **serialize(user)})
    cost = PICKAXE_COSTS[tier]
    inv = dict(user.inventory)
    # check cost
    for ore, need in cost.items():
        if int(inv.get(ore,0)) < need:
            return jsonify({"error":"Not enough resources"}), 400
    for ore, need in cost.items():
        inv[ore] = int(inv.get(ore,0)) - need
    user.inventory = inv
    user.pickaxe = tier
    db.session.commit()
    return jsonify({"message":"Purchased", **serialize(user)})

@app.route("/api/shop/powerup", methods=["POST"])
def shop_powerup():
    user = auth_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    name = request.json.get("name")
    if name not in POWERUPS:
        return jsonify({"error":"Unknown powerup"}), 400
    price = 50 if name == "2x Strength" else 75
    if user.silver < price:
        return jsonify({"error":"Need more silver"}), 400
    user.silver -= price
    st = dict(user.stats); st["silverSpent"] = int(st.get("silverSpent",0) + price); user.stats = st
    eff = POWERUPS[name]
    until = now_ms() + eff["secs"]*1000
    ap = dict(user.active_powerups)
    ap[eff["key"]] = {"mult": eff["mult"], "until": until}
    user.active_powerups = ap
    db.session.commit()
    return jsonify({"message":"Activated", **serialize(user)})

@app.route("/api/pets/spin", methods=["POST"])
def pets_spin():
    user = auth_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    cost = 150
    free = bool(request.json.get("free", False))
    if user.silver < cost and not free:
        return jsonify({"error":"Need 150 silver"}), 400
    if not free:
        user.silver -= cost
        st = dict(user.stats); st["silverSpent"] = int(st.get("silverSpent",0) + cost); user.stats = st
    # roll rarity
    import random
    bag = ([("common",)*1]*PET_WEIGHTS["common"] +
           [("uncommon",)*1]*PET_WEIGHTS["uncommon"] +
           [("rare",)*1]*PET_WEIGHTS["rare"] +
           [("epic",)*1]*PET_WEIGHTS["epic"])  # simplified weights bag
    flat = [k for sub in bag for k in sub]
    rarity = random.choice(flat)
    candidates = [p for p in PET_POOL if p["rarity"]==rarity]
    pet = random.choice(candidates)
    owned = list(user.pets)
    if any(p.get("id")==pet["id"] for p in owned):
        user.silver += 50
        msg = f"Duplicate {pet['name']}. Refunded 50 silver."
    else:
        owned.append(pet)
        user.pets = owned
        msg = f"New pet: {pet['name']}!"
    db.session.commit()
    return jsonify({"message": msg, **serialize(user)})

@app.route("/api/codes/redeem", methods=["POST"])
def codes_redeem():
    user = auth_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    raw = (request.json.get("code") or "").upper().strip()
    if not raw:
        return jsonify({"error":"Code required"}), 400
    if raw in (user.used_codes or []):
        return jsonify({"error":"Code already used"}), 400
    fn = CODE_REWARDS.get(raw)
    if not fn:
        return jsonify({"error":"Invalid code"}), 400
    reward = fn(user)
    if reward.get("silver"): user.silver += int(reward["silver"]) ; st = dict(user.stats); st["silverEarned"] = int(st.get("silverEarned",0)+int(reward["silver"])) ; user.stats = st
    if reward.get("item"):
        inv = dict(user.inventory)
        for k,v in reward["item"].items(): inv[k] = int(inv.get(k,0))+int(v)
        user.inventory = inv
    if reward.get("powerup"):
        name = reward["powerup"]
        eff = POWERUPS[name]
        ap = dict(user.active_powerups)
        ap[eff["key"]] = {"mult": eff["mult"], "until": now_ms()+eff["secs"]*1000}
        user.active_powerups = ap
    if reward.get("freepet"): pass  # front can call /api/pets/spin with {free:true}
    # event ignored server-side for simplicity
    user.used_codes = (user.used_codes or []) + [raw]
    db.session.commit()
    return jsonify({"message": reward.get("message","OK"), **serialize(user)})

@app.route("/api/leaderboard", methods=["GET"])
def leaderboard():
    top = User.query.order_by(User.silver.desc()).limit(10).all()
    return jsonify([{"username": u.username, "silver": u.silver} for u in top])

@app.route("/api/daily/claim", methods=["POST"])
def daily_claim():
    user = auth_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    day_ms = 24*60*60*1000
    if user.last_daily and (now_ms() - int(user.last_daily) < day_ms):
        return jsonify({"error":"Cooldown"}), 400
    user.last_daily = now_ms()
    gain = 200
    user.silver += gain
    st = dict(user.stats); st["silverEarned"] = int(st.get("silverEarned",0)+gain); user.stats = st
    # short random powerup 60s
    eff = POWERUPS["2x Strength" if (secrets.randbelow(2)==0) else "2x Luck"]
    ap = dict(user.active_powerups); ap[eff["key"]] = {"mult": eff["mult"], "until": now_ms()+60*1000}; user.active_powerups = ap
    db.session.commit()
    return jsonify({"message":"Daily claimed", **serialize(user)})

# --- Admin ---
ADMIN_CODE = "danielis67"

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    user = auth_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    code = request.json.get("code","")
    if code == ADMIN_CODE:
        user.is_admin = True
        db.session.commit()
        return jsonify({"message":"Admin unlocked", **serialize(user)})
    return jsonify({"error":"Wrong admin code"}), 403

@app.route("/api/admin/money", methods=["POST"])
def admin_money():
    user = auth_user()
    if not user or not user.is_admin: return jsonify({"error":"Forbidden"}), 403
    user.silver += 999_999
    db.session.commit()
    return jsonify({"silver": user.silver, **serialize(user)})

@app.route("/api/admin/powerups", methods=["POST"])
def admin_powerups():
    user = auth_user()
    if not user or not user.is_admin: return jsonify({"error":"Forbidden"}), 403
    until = now_ms()+10*60*1000
    ap = dict(user.active_powerups)
    ap["strength"] = {"mult":2, "until": until}
    ap["luck"] = {"mult":2, "until": until}
    user.active_powerups = ap
    db.session.commit()
    return jsonify({"message":"All powerups for 10 minutes", **serialize(user)})

# --- Run ---
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=1000, debug=True)
