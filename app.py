from flask import Flask, request, jsonify, session
from flask_limiter import Limiter
from flask_cors import CORS
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
import google.generativeai as genai
from flask_sqlalchemy import SQLAlchemy
import secrets
import os

load_dotenv()

app= Flask(__name__)

CORS(app, origins=['https://tnoshin.github.io'], supports_credentials=True)

def get_real_ip():
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr

limiter = Limiter(
    app=app,
    key_func=get_real_ip,
    default_limits=['200 per day','50 per hour','8 per minute'],
    storage_uri='memory://'
)

app.secret_key = os.getenv('SECRET_KEY')

database_url = os.getenv('DATABASE_URL', 'sqlite:///chat.db')
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://','postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url

app.config['SESSION_COOKIE_HTTPONLY']=True
app.config['SESSION_COOKIE_SECURE'] = os.getenv('RENDER') is not None
app.config['SESSION_COOKIE_SAMESITE']='None'


db = SQLAlchemy(app)

class message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(50))
    role = db.Column(db.String(10))
    content = db.Column(db.Text)

with app.app_context():
    db.create_all()

genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-3.1-flash-lite')

system_prompt = """ YOU ALWAYS RESPOND WITHIN 300 TOKENS. Try to keep the reply within 3-4 lines unless asked for information, then you can use more lines. You are a helpful assistant for BrightSmile Dental Clinic.
Clinic information:
- Name: BrightSmile Dental
- Hours: Monday-Friday 8 AM-6 PM, Saturday 9 AM-3 PM, Sunday closed
- Services: General checkups, teeth cleaning, fillings, whitening, extractions
- Location: 123 Dental Street, Suite 200, San Francisco, CA 94102
- Phone: (555) 123-4567
- Email: info@brightsmile.com
Website overview:
- Pages: Home, Services, About, Contact, Booking page, and a dark/light mode toggle (sun/moon icon in the navbar).
- Booking: Users can book an appointment via any of two teal buttons — "Book Appointment" (top-right navbar), "Schedule Visit" (homepage hero), or the white "Book Your Appointment" (in the CTA section above the footer). All three lead to the booking page.
- Booking page requires: First name, Last name, Date, Time, and Phone number (marked with red asterisks to indicate necessary). Optional fields: Gender, Age, Email, and an Additional Note field for allergies, concerns, or special requests.
- Contact page: Reached via the "Contact" nav link. Users can send a message or feedback using a form (Full name, Email, Message — all required). This page also shows clinic info, opening hours, and a "What to Expect" section: free initial consultation for first-time patients, gentle pain-free approach, transparent pricing (no hidden fees), and free cancellation up to 24 hours before the appointment.
Answer questions about the clinic helpfully and professionally. If asked about something unrelated to dentistry or the clinic, politely redirect. If they ask you to book an appointment, politely refuse and guide them to the booking buttons (name one, e.g. "Book Appointment" in the top-right). If they ask to leave feedback or contact the clinic directly, point them to the Contact page. If a user asks about medical symptoms, pain, or urgent dental issues, do not attempt to diagnose or give medical advice — politely redirect them to contact the clinic directly by phone. Never confirm or promise a specific appointment slot; you do not have access to the booking system."""

@app.route('/chat', methods=['POST'])
def chat():
    print(f'Real IP: {get_real_ip()}')
    print(f'X-Forwarded-For header: {request.headers.get("X-Forwarded-For")}')
    if 'session_id' not in session:
        session['session_id']=secrets.token_hex(8)
    session_id = session['session_id']

    data = request.get_json() or {}
    user_message = data.get('message','').strip()
    if not user_message:
        return jsonify({'error':'Please send a message'}), 400

    if len(user_message)>1500: #ask the customer how long they'll allow the user's msg to be
        return jsonify({'error':'Message too long(max 1500 characters)'}), 400

    db.session.add(message(session_id=session_id, role='user', content=user_message))
    db.session.commit()

    recent_msg = message.query.filter_by(session_id=session_id).order_by(message.id.desc()).limit(10).all()
    recent_msg.reverse()

    conversation_context = ''
    for m in recent_msg:
        if m.role == 'user':
            conversation_context += f'\nUser: {m.content}'
        else:
            conversation_context += f'\nAssistant: {m.content}'

    full_msg = system_prompt + '\n\nConversation so far: ' + conversation_context + '\n\nUser: ' + user_message

    try:
        reply = model.generate_content(full_msg)
        if not reply.text:
            return jsonify({'error': 'No response generated, please rephrase'}), 500
    except Exception as error:
        print(f'Gemini API error: {error}')
        return jsonify({'error':'Something went wrong. Please try again.'}), 500

    db.session.add(message(session_id=session_id, role='assistant', content=reply.text))
    db.session.commit()
    return jsonify({'response':reply.text})


@app.route('/history', methods=['GET'])
def history():
    if 'session_id' not in session:
        return jsonify({'messages':[]})
    session_id = session['session_id']
    messages = message.query.filter_by(session_id=session_id).all()

    result = []
    for m in messages:
        result.append({'role':m.role, 'content': m.content})
    return jsonify({'messages':result})

@app.route('/ping', methods=['GET'])
@limiter.exempt
def ping():
    return jsonify({'ok': True})

@app.errorhandler(429)
def rate_limit_exceeded(e):
    return jsonify({'error':'You are sending too many messages at once, please wait a moment.'}), 429

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
    
        


    