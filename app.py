# app.py
import os
from datetime import datetime
from functools import wraps
from urllib.parse import quote_plus
from datetime import timedelta

from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from bson import ObjectId

from datetime import datetime
from bson import ObjectId
import numpy as np

from joblib import load
import numpy as np
model = load("models/risk_model.joblib")

# Load environment variables from .env (development convenience)
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")  # set real secret in .env for production

# Mongo config
MONGO_USERNAME = os.getenv("MONGO_USERNAME")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD")
MONGO_CLUSTER_URL = os.getenv("MONGO_CLUSTER_URL")
DB_NAME = os.getenv("DB_NAME", "MediPredictDB")

if not (MONGO_USERNAME and MONGO_PASSWORD and MONGO_CLUSTER_URL):
    print("Warning: Mongo credentials are not fully set in environment variables.")

ENCODED_PASSWORD = quote_plus(MONGO_PASSWORD) if MONGO_PASSWORD else ""
MONGO_URI = f"mongodb+srv://{MONGO_USERNAME}:{ENCODED_PASSWORD}@{MONGO_CLUSTER_URL}/?retryWrites=true&w=majority"

# Connect to MongoDB
try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[DB_NAME]
    users_collection = db['users']
    user_data_collection = db['user_health_records']
    # Ensure unique index on email
    users_collection.create_index("email", unique=True)
    print("--- MongoDB connected ---")
except Exception as e:
    print(f"MongoDB connection error: {e}")
    users_collection = None
    user_data_collection = None

# -------------------------
# Authentication helpers
# -------------------------
def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please login to continue.", "warning")
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return wrapped

@app.before_request
def load_user():
    """Set g.user for templates and logic."""
    user_id = session.get('user_id')
    if user_id and users_collection is not None:
        try:
            g.user = users_collection.find_one({"_id": ObjectId(user_id)})
        except Exception:
            g.user = None
    else:
        g.user = None

@app.context_processor
def inject_user():
    return {"current_user": g.user}

# -------------------------
# Routes: root -> login page
# -------------------------
@app.route('/')
def root():
    # Show login page as the first page
    if 'user_id' in session:
        return redirect(url_for('index'))
    return redirect(url_for('login'))

# Signup page (dedicated)
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')

        if not name or not email or not password:
            flash("Name, email and password are required.", "danger")
            return render_template('signup.html', name=name, email=email)

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template('signup.html', name=name, email=email)

        if users_collection is None:
            flash("User database not available. Try again later.", "danger")
            return render_template('signup.html')

        password_hash = generate_password_hash(password)

        user_doc = {
            "name": name,
            "email": email,
            "password_hash": password_hash,
            "created_at": datetime.utcnow()
        }

        try:
            result = users_collection.insert_one(user_doc)
            # Store user_id as string to avoid serialization issues
            session.clear()
            session['user_id'] = str(result.inserted_id)
            flash("Signup successful. Welcome!", "success")
            return redirect(url_for('index'))
        except Exception as e:
            # likely duplicate key error
            flash("An account with that email already exists.", "danger")
            return render_template('signup.html', name=name, email=email)

    return render_template('signup.html')

# Login page (dedicated)
@app.route('/login', methods=['GET', 'POST'])
def login():
    next_page = request.args.get('next') or url_for('index')
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not email or not password:
            flash("Email and password are required.", "danger")
            return render_template('login.html', email=email, next=next_page)

        if users_collection is None:
            flash("User database not available. Try again later.", "danger")
            return render_template('login.html')

        user = users_collection.find_one({"email": email})
        if user and check_password_hash(user.get("password_hash", ""), password):
            session.clear()
            session['user_id'] = str(user['_id'])
            flash("Login successful.", "success")
            return redirect(next_page)
        else:
            flash("Invalid email or password.", "danger")
            return render_template('login.html', email=email, next=next_page)

    return render_template('login.html', next=next_page)

# Logout
@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))

# -------------------------
# Protected index and user input
# -------------------------
@app.route('/index')
@login_required
def index():
    # This renders your current index.html (protected)
    return render_template('index.html')

@app.route('/user_input')
@login_required
def user_input():
    return render_template('user_input.html')

# Prediction route (POST) - keep protected
@app.route('/prediction', methods=['POST'])
@login_required
def prediction():
    # get form fields
    name = request.form['name']
    age = float(request.form['age'])
    gender = request.form['gender']
    height = float(request.form['height'])
    weight = float(request.form['weight'])
    bp = request.form['blood_pressure']
    cholesterol = request.form['cholesterol_level']
    sugar = request.form['blood_sugar']
    sleep = float(request.form['sleep_hrs'])
    exercise = request.form['exercise']
    smoking = request.form['smoking']
    alcohol = request.form['alcohol']

    # convert blood pressure "120/80"
    try:
        systolic, diastolic = bp.split('/')
        systolic = float(systolic)
        diastolic = float(diastolic)
    except Exception:
        # fallback default if user entered unexpected format
        systolic = 0.0
        diastolic = 0.0

    # BMI
    bmi = weight / ((height/100)**2) if height > 0 else 0.0

    # BMI Category
    if bmi < 18.5:
        bmi_cat = "Underweight"
    elif bmi < 25:
        bmi_cat = "Normal"
    elif bmi < 30:
        bmi_cat = "Overweight"
    else:
        bmi_cat = "Obese"

    bmi_map = {"Underweight":0,"Normal":1,"Overweight":2,"Obese":3}
    bmi_cat_val = bmi_map[bmi_cat]

    # convert gender
    gender_val = 1 if gender=="male" else 0

    # convert categories numeric simple mapping (keeps your mapping)
    sugar_map = {"normal": 0, "prediabetic": 3, "diabetic": 6}
    sugar_val = sugar_map.get(sugar, 0)

    chol_map = {"normal": 2, "borderline": 0, "high": 1}
    cholesterol_val = chol_map.get(cholesterol, 0)

    smoke_map = {"no":0, "occasionally":1, "yes":2}
    smoking_val = smoke_map.get(smoking, 0)

    alcohol_map = {"no":0, "occasionally":1, "yes":2}
    alcohol_val = alcohol_map.get(alcohol, 0)

    exercise_map = {"none":0, "moderate":1, "high":2}
    exercise_val = exercise_map.get(exercise, 0)

    # Prepare model input (12 features)
    x = np.array([[age, gender_val, bmi, bmi_cat_val, cholesterol_val,
                   sugar_val, smoking_val, alcohol_val, exercise_val,
                   sleep, systolic, diastolic]])
    pred = model.predict(x)[0]           # e.g. array([0,1,2,0,1])

    # Mapping text labels
    risk_map = {0:"Low Chance", 1:"Medium Chance", 2:"High Chance"}

    crd_risk = risk_map[int(pred[1])]
    hd_risk = risk_map[int(pred[2])]
    diabetes_risk = risk_map[int(pred[3])]
    liver_risk = risk_map[int(pred[4])]

    # Determine overall risk as the max of specific risks
    overall_risk_index = int(max(pred[1], pred[2], pred[3], pred[4]))
    overall_risk = risk_map[overall_risk_index]

    # --- compute percent-style aggregated scores for donut chart ---
    # mapping to percent: 0->0%, 1->50%, 2->100%
    mapping_for_score = {0: 0, 1: 50, 2: 100}
    specific_encoded = [int(pred[1]), int(pred[2]), int(pred[3]), int(pred[4])]
    specific_scores = [mapping_for_score.get(v, 0) for v in specific_encoded]
    avg_risk_percent = int(round(sum(specific_scores) / len(specific_scores)))  # 0..100
    overall_health_percent = max(0, 100 - avg_risk_percent)

    # Prepare recommendation (reuse your disease_recommend function)
    def disease_recommend(level, disease):
        if disease=="CRD":
            if level=="High Chance":
                return "Your lungs are at serious risk due to your smoking/activity profile. QUIT nicotine immediately, avoid pollution exposure, use mask outside and schedule Pulmonologist check-up within 2 weeks."
            elif level=="Medium Chance":
                return "Respiratory health is sliding. Reduce smoking frequency, start daily 25 minute brisk walk, add steam inhalation and deep breathing exercises."
            else:
                return "Your lungs are performing in safe zone. Continue regular aerobic exercise to maintain lung elasticity and oxygen exchange efficiency."
        if disease=="Heart":
            if level=="High Chance":
                return "Heart risk extremely high. Your BP/Cholestrol pattern is dangerous. Shift to low-salt, low-saturated fat diet, avoid deep fried food, start immediate cardiology consultation."
            elif level=="Medium Chance":
                return "Heart strain exists. Monitor BP weekly, take 30 minute brisk walk 5 days/week, reduce processed sugar and sodium intake."
            else:
                return "Your cardiac risk is under control. Maintain balanced diet, remain physically active and continue routine health checks every 6 months."
        if disease=="Diabetes":
            if level=="High Chance":
                return "Very high glycemic stress detected. Switch to complex carbs only, avoid sugary drinks, include fiber rich food and mandatory morning walk daily. Consult endocrinologist soon."
            elif level=="Medium Chance":
                return "Borderline diabetic pattern forming. Focus on weight reduction 5-10%, cut night eating and limit sweet/snacks intake."
            else:
                return "Blood sugar behaviour stable. Maintain consistent meals timing and avoid over-refined foods."
        if disease=="Liver":
            if level=="High Chance":
                return "Severe liver burden suspected. Stop alcohol immediately, reduce fatty/oily food, increase hydration and consult gastro/hepatologist urgently."
            elif level=="Medium Chance":
                return "Liver stress developing. Reduce weekly alcohol units, include antioxidant rich fruits (blueberry, pomegranate) and avoid heavy fried food."
            else:
                return "Liver functioning within safe zone. Maintain hydration, keep balanced diet and avoid unnecessary medication load."
        if disease=="Overall":
            if level=="High Chance":
                return "Multiple system risks detected together. Immediate lifestyle correction required across smoking, diet, sugar and alcohol domains. Doctor evaluation required soon."
            elif level=="Medium Chance":
                return "Few risk areas are rising. Prioritize early correction to prevent chronic disease formation."
            else:
                return "Overall body risk profile healthy. Maintain consistency."
        
    overall_rec = disease_recommend(overall_risk, "Overall")
    crd_rec = disease_recommend(crd_risk, "CRD")
    hd_rec = disease_recommend(hd_risk, "Heart")
    diabetes_rec = disease_recommend(diabetes_risk, "Diabetes")
    liver_rec = disease_recommend(liver_risk, "Liver")

    # Build the record to store
    record = {
        "timestamp": datetime.utcnow(),
        "inputs": {
            "name": name,
            "age": age,
            "gender": gender,
            "height": height,
            "weight": weight,
            "blood_pressure": bp,
            "cholesterol_level": cholesterol,
            "blood_sugar": sugar,
            "sleep_hrs": sleep,
            "exercise": exercise,
            "smoking": smoking,
            "alcohol": alcohol,
            "BMI": round(bmi, 2),
            "BMI_Category": bmi_cat
        },
        "predictions": {
            "encoded": [int(p) for p in pred.tolist()],
            "overall_risk": overall_risk,
            "crd_risk": crd_risk,
            "heart_risk": hd_risk,
            "diabetes_risk": diabetes_risk,
            "liver_risk": liver_risk,
            "risk_values": specific_encoded,
            "avg_risk_percent": int(avg_risk_percent),
            "overall_health_percent": int(overall_health_percent),
            "overall_risk_index": overall_risk_index
        },
        "recommendations": {
            "overall": overall_rec,
            "crd": crd_rec,
            "heart": hd_rec,
            "diabetes": diabetes_rec,
            "liver": liver_rec
        }
    }

    # Save to user's document (if users_collection available)
    try:
        if users_collection is not None and session.get("user_id"):
            users_collection.update_one(
                {"_id": ObjectId(session['user_id'])},
                {"$push": {"predictions": record}}
            )
    except Exception as e:
        print("Failed to save prediction to user document:", e)

    # Prepare data to send to template
    data = request.form.to_dict()
    data["BMI"] = round(bmi,2)
    data["BMI_Category"] = bmi_cat

    data["overall_risk"] = overall_risk
    data["crd_risk"] = crd_risk
    data["hd_risk"] = hd_risk
    data["diabetes_risk"] = diabetes_risk
    data["liver_risk"] = liver_risk

    data["overall_rec"] = overall_rec
    data["crd_rec"] = crd_rec
    data["hd_rec"] = hd_rec
    data["diabetes_rec"] = diabetes_rec
    data["liver_rec"] = liver_rec

    # numeric values for charting (ensure plain python ints, not numpy types)
    data["risk_values"] = [int(v) for v in specific_encoded]
    data["avg_risk_percent"] = int(avg_risk_percent)
    data["overall_health_percent"] = int(overall_health_percent)
    data["overall_risk_index"] = int(overall_risk_index)

    # get user's history to display (latest first)
    # get user's history to display (latest first)
    history = []
    try:
        if users_collection is not None and session.get("user_id"):
            user_doc = users_collection.find_one({"_id": ObjectId(session['user_id'])}, {"predictions": 1})
            history = user_doc.get("predictions", [])[::-1][:5]  # latest 5 predictions only
    except Exception as e:
        print("Failed to fetch user history:", e)

    # FIX: always convert timestamps → IST + formatted string
    for rec in history:
        if "timestamp" in rec and rec["timestamp"]:
            ist_time = rec["timestamp"] + timedelta(hours=5, minutes=30)
            rec["timestamp_ist"] = ist_time
            rec["timestamp_ist_str"] = ist_time.strftime('%Y-%m-%d %H:%M:%S')
        else:
            rec["timestamp_ist"] = None
            rec["timestamp_ist_str"] = None


    # pass history list to template
    return render_template("prediction.html", data=data, history=history)


# -------------------------
if __name__ == '__main__':
    debug_mode = os.getenv("FLASK_ENV", "production") == "development"
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=debug_mode)
