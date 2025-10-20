# File: app.py
from flask import Flask, request, render_template_string, redirect, url_for, jsonify
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather, Dial
from dotenv import load_dotenv
import os, sqlite3, re, datetime

load_dotenv()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
ACQ_NUMBER = os.getenv("ACQ_NUMBER")
BASE_URL = os.getenv("BASE_URL")

app = Flask(__name__)
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

DB_PATH = "calls.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT,
        call_sid TEXT,
        to_number TEXT,
        from_number TEXT,
        status TEXT,
        raw_speech TEXT,
        intent INTEGER,
        price TEXT,
        timing TEXT,
        owner_status TEXT,
        disposition TEXT
    )""")
    conn.commit()
    conn.close()

init_db()

def log_call(**data):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
    INSERT INTO calls (created_at, call_sid, to_number, from_number, status,
                       raw_speech, intent, price, timing, owner_status, disposition)
    VALUES (:created_at, :call_sid, :to_number, :from_number, :status,
            :raw_speech, :intent, :price, :timing, :owner_status, :disposition)
    """, {
        "created_at": datetime.datetime.utcnow().isoformat(),
        **{k: data.get(k) for k in [
            "call_sid","to_number","from_number","status",
            "raw_speech","intent","price","timing","owner_status","disposition"
        ]}
    })
    conn.commit()
    conn.close()

def detect_intent(text: str):
    text = (text or "").lower()
    if re.search(r"(remove me|do not call|unsubscribe)", text):
        return {"opt_out": True}
    intent = bool(re.search(r"\b(sell|selling|interested in selling|thinking of selling|moving)\b", text))
    price = re.search(r"(\$?\d[\d,]*)", text)
    timing = re.search(r"(now|soon|next month|this year|within|later)", text)
    owner = re.search(r"(i own|we own|tenant|not the owner|probate|inherited)", text)
    return {
        "intent": intent,
        "opt_out": False,
        "price": price.group(0) if price else None,
        "timing": timing.group(0) if timing else None,
        "owner_status": owner.group(0) if owner else None
    }

@app.route("/")
def index():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT * FROM calls ORDER BY id DESC LIMIT 50").fetchall()
    conn.close()
    html = """
    <h1>Vanessa – Voice AI Prototype</h1>
    <form action="/start_call" method="post">
      <input name="to" placeholder="+1..." required>
      <button>Start Call</button>
    </form>
    <h3>Recent Calls</h3>
    <table border=1 cellpadding=4>
      <tr><th>Time (UTC)</th><th>To</th><th>Status</th><th>Intent</th><th>Price</th>
          <th>Timing</th><th>Owner</th><th>Disposition</th></tr>
      {% for r in rows %}
      <tr><td>{{r[1]}}</td><td>{{r[3]}}</td><td>{{r[5]}}</td>
          <td>{{'Yes' if r[7] else 'No'}}</td><td>{{r[8] or ''}}</td>
          <td>{{r[9] or ''}}</td><td>{{r[10] or ''}}</td><td>{{r[11] or ''}}</td></tr>
      {% endfor %}
    </table>
    """
    return render_template_string(html, rows=rows)

@app.route("/start_call", methods=["POST"])
def start_call():
    to_number = request.form.get("to")
    call = client.calls.create(
        to=to_number,
        from_=TWILIO_NUMBER,
        url=f"{BASE_URL}/voice"
    )
    log_call(call_sid=call.sid, to_number=to_number, from_number=TWILIO_NUMBER,
             status="initiated", raw_speech=None, intent=0, disposition="initiated")
    return redirect(url_for("index"))

@app.route("/voice", methods=["POST", "GET"])
def voice():
    r = VoiceResponse()
    g = Gather(input="speech", action="/gather", method="POST", timeout=5)
    g.say("Hi, this is Vanessa from HomePath Solutions. Are you the homeowner, and have you considered selling recently?", voice="alice")
    r.append(g)
    r.say("Sorry, I didn’t hear you. Goodbye.", voice="alice")
    return str(r)

@app.route("/gather", methods=["POST"])
def gather():
    call_sid = request.values.get("CallSid")
    speech = request.values.get("SpeechResult", "")
    nlu = detect_intent(speech)
    log_call(call_sid=call_sid, to_number=request.values.get("To"),
             from_number=request.values.get("From"), status="gathered",
             raw_speech=speech, intent=int(nlu["intent"]),
             price=nlu["price"], timing=nlu["timing"],
             owner_status=nlu["owner_status"], disposition=None)
    r = VoiceResponse()

    if nlu["opt_out"]:
        r.say("Understood, we’ll remove you from our call list. Thank you.", voice="alice")
        return str(r)

    if nlu["intent"]:
        g = Gather(input="speech", action="/qualify", method="POST", timeout=5)
        g.say("Great. When were you hoping to sell, and what price range did you have in mind?", voice="alice")
        r.append(g)
        r.say("Thanks, connecting you now to a team member.", voice="alice")
        return str(r)
    else:
        r.say("No problem. Have a great day.", voice="alice")
        return str(r)

@app.route("/qualify", methods=["POST"])
def qualify():
    call_sid = request.values.get("CallSid")
    speech = request.values.get("SpeechResult", "")
    nlu = detect_intent(speech)
    log_call(call_sid=call_sid, to_number=request.values.get("To"),
             from_number=request.values.get("From"), status="qualified",
             raw_speech=speech, intent=1, price=nlu["price"],
             timing=nlu["timing"], owner_status=nlu["owner_status"],
             disposition="qualified")
    r = VoiceResponse()
    r.say("Thank you. Let me connect you to our acquisitions specialist.", voice="alice")
    d = Dial(caller_id=TWILIO_NUMBER)
    d.number(ACQ_NUMBER)
    r.append(d)
    return str(r)

@app.route("/api/calls")
def api_calls():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT * FROM calls ORDER BY id DESC LIMIT 50").fetchall()
    conn.close()
    return jsonify(rows)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
