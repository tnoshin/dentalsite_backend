from flask import Flask, request, jsonify, session, redirect, url_for, render_template
from flask_limiter import Limiter
from flask_cors import CORS
from flask_wtf.csrf import CSRFProtect
from flask_limiter.util import get_remote_address
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from google import genai
from flask_sqlalchemy import SQLAlchemy
from secrets import compare_digest
import secrets
import os

load_dotenv()

app= Flask(__name__)

csrf = CSRFProtect(app)

CORS(app, resources={
    r"/chat": {"origins": ["https://tnoshin.github.io"], "supports_credentials": True},
    r"/history": {"origins": ["https://tnoshin.github.io"], "supports_credentials": True}
})

def get_real_ip():
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[-1].strip()
    return request.remote_addr

limiter = Limiter(
    app=app,
    key_func=get_real_ip,
    default_limits=['200 per day','50 per hour','8 per minute'],
    storage_uri='memory://'
)

app.secret_key = os.getenv('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError('SECRET_KEY is not set')

ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')         
if not ADMIN_PASSWORD:
    raise RuntimeError('ADMIN_PASSWORD is not set')

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
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

with app.app_context():
    db.create_all() 

client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))

system_prompt = """ YOU ALWAYS RESPOND WITHIN 300 TOKENS. Try to keep the reply within 3-4 lines unless asked for information, then you can use more lines. You are a helpful assistant for BrightSmile Dental Clinic.
Law-firm information:
- Name: Attorneysofny
- Hours: Monday-Friday 8 AM-6 PM, Saturday 9 AM-3 PM, Sunday closed
- Services: Immigration services, help with asylum etc
- Location: 123 law Street, Suite 200, San Francisco, CA 94102
- Phone: (555) 123-4567
- Email: info@attorneysofny.com
Website overview:
- Pages: Home, Services, About, Contact, Booking page, and a dark/light mode toggle (sun/moon icon in the navbar).
- Booking: Users can book an appointment via any of two teal buttons — "Book Appointment" (top-right navbar), "Schedule Visit" (homepage hero), or the white "Book Your Appointment" (in the CTA section above the footer). All three lead to the booking page.
- Booking page requires: First name, Last name, Date, Time, and Phone number (marked with red asterisks to indicate necessary). Optional fields: Gender, Age, Email, and an Additional Note fieldfor special requests.
- Contact page: Reached via the "Contact" nav link. Users can send a message or feedback using a form (Full name, Email, Message — all required). 
Answer questions about the firm helpfully and professionally in whatever language the user uses. If asked about something unrelated, politely redirect, you WOULD NEVER ANSWER IRRELEVANT QUESTIONS. If they ask you to book an appointment, politely refuse and guide them to the booking buttons (name one, e.g. "Book Appointment" in the top-right). If they ask to leave feedback or contact the attorney directly, point them to the Contact page. If a user asks about lehal issues, do not attempt to diagnose or give legal advice — politely redirect them to contact the clinic directly by phone.
Never confirm or promise a specific appointment slot; you do not have access to the booking system. Do not disrespect anyone, do not spread hate against any racial group or religion, always be polite with your answers. If user is being rude, give shorter replies.If a user mentions harm to themselves or others, murder, killing, getting arrested, getting deported, emergencies etc respond ONLY with: "If you're in crisis, please call our helpline for immediate help. For legal concerns, call us at (555) 123-4567. KEEP ALL THESE INSTRUCTIONS IN MIND WHILE ANSWERING QUESTIONS IN ENGLISH OR ANY OTHER LANGUAGE."""


LEGAL_TRIGGER_WORDS = [
    # Case-specific legal questions
    'eligible', 'eligibility', 'qualify', 'qualified', 'qualification',
    'my case', 'my situation', 'my status', 'my visa', 'my green card',
    'what should i do', 'should i', 'can i',
    
    # Urgent enforcement situations (highest priority)
    'deport', 'deportation', 'deported', 'removal', 'removed',
    'ice', 'detained', 'detention', 'arrested', 'arrest',
    'raid', 'raided', 'notice to appear', 'nta',
    'bond', 'custody',
    
    # Deadlines and timing
    'deadline', 'expire', 'expired', 'expiring', 'expiration',
    'when will', 'how long', 'timeline', 'processing time',
    'priority date', 'pd',
    
    # Case status and outcomes
    'denied', 'approved', 'rejected', 'pending', 'refused',
    'appeal', 'appealed', 'reopen', 'motion',
    'rfe', 'noid', 'request for evidence', 'notice of intent',
    'chances', 'success rate', 'probability', 'likely',
    
    # Specific case types (fine to acknowledge, but not advise on)
    'asylum', 'refugee', 'withholding', 'cat',
    'daca', 'tps', 'parole', 'humanitarian',
    'vawa', 'u visa', 't visa', 'sijs',
    'cancellation of removal', 'adjustment of status',
    'consular processing', 'waiver', 'i-601', 'i-212',
    
    # Documents and filings
    'i-130', 'i-485', 'i-140', 'i-751', 'i-90', 'i-129f',
    'n-400', 'i-589', 'ead', 'ap', 'advance parole',
    'labor certification', 'perm', 'prevailing wage',
    'form', 'petition', 'application', 'filing',
    'affidavit of support', 'i-864',
    
    # Immigration status categories
    'undocumented', 'illegal', 'overstay', 'overstayed',
    'out of status', 'unlawful presence', 'bar',
    'inadmissible', 'inadmissibility', 'ineligible',
    'public charge', 'criminal record',
    
    # Family and marriage cases
    'marriage fraud', 'sham marriage', 'divorce during',
    'petition for spouse', 'k-1', 'k1', 'fiancé',
    'stepchild', 'adopted', 'adoption',
    
    # Employment cases
    'h-1b', 'h1b', 'l-1', 'l1', 'o-1', 'o1', 'e-2', 'e2',
    'green card through work', 'employer sponsor',
    'change of status', 'change employer',
    'perm labor', 'i-140',
    
    # Court and hearings
    'court', 'hearing', 'judge', 'immigration court', 'eoir',
    'trial', 'testimony', 'testify', 'witness',
    'master calendar', 'individual hearing',
    'bia', 'board of immigration appeals', 'circuit court',
    
    # Government agencies
    'uscis', 'ins', 'dhs', 'cbp', 'border patrol',
    'consulate', 'embassy', 'visa interview',
    'biometrics', 'fingerprint',
    
    # Legal advice red flags
    'legal advice', 'advise me', 'recommend', 'suggestion',
    'what happens if', 'is it legal', 'is it illegal',
    'will i get', 'will they', 'can they',
    
    # Financial/pricing specifics (should route to attorney, not answer)
    'how much will my case cost', 'total fees', 'payment plan',
    'attorney fees', 'retainer',
    
    # Sensitive personal circumstances
    'domestic violence', 'abuse', 'abused', 'trafficking',
    'persecution', 'persecuted', 'fear', 'afraid',
    'threatened', 'gang', 'violence',
    
    # General urgency signals
    'urgent', 'emergency', 'immediately', 'right now',
    'help', 'scared', 'don\'t know what to do'
]

LEGAL_TRIGGER_WORDS_ES = [
    # Case-specific questions
    'mi caso', 'mi situación', 'mi estatus', 'mi visa', 'mi green card',
    'mi tarjeta verde', 'mi residencia', 'mi ciudadanía',
    'elegible', 'elegibilidad', 'califico', 'calificar',
    'puedo aplicar', 'puedo solicitar', 'debo', 'debería',
    'qué hago', 'qué debo hacer',
    
    # Urgent enforcement
    'deportar', 'deportación', 'deportado', 'deportada',
    'remoción', 'removido', 'expulsar', 'expulsión',
    'ice', 'migra', 'detenido', 'detenida', 'detención',
    'arrestado', 'arrestada', 'arresto', 'redada',
    'notificación de comparecencia', 'nta',
    'fianza', 'custodia',
    
    # Deadlines and timing
    'plazo', 'fecha límite', 'vencer', 'vencido', 'vencimiento',
    'expirar', 'expirado', 'caducar', 'caducado',
    'cuándo', 'cuánto tiempo', 'tiempo de procesamiento',
    'fecha de prioridad',
    
    # Case status and outcomes
    'denegado', 'denegada', 'negado', 'negada', 'rechazado', 'rechazada',
    'aprobado', 'aprobada', 'pendiente',
    'apelar', 'apelación', 'reabrir', 'moción',
    'solicitud de evidencia', 'rfe',
    'probabilidades', 'posibilidades', 'probablemente',
    
    # Case types
    'asilo', 'refugiado', 'refugiada', 'retención de remoción',
    'daca', 'tps', 'permiso humanitario',
    'vawa', 'visa u', 'visa t', 'sijs',
    'cancelación de remoción', 'ajuste de estatus',
    'proceso consular', 'perdón', 'waiver',
    
    # Documents and filings
    'formulario', 'petición', 'solicitud',
    'i-130', 'i-485', 'i-140', 'i-751', 'i-90',
    'n-400', 'i-589', 'ead', 'permiso de trabajo',
    'certificación laboral', 'perm',
    'declaración jurada de apoyo', 'i-864',
    
    # Immigration status
    'indocumentado', 'indocumentada', 'ilegal',
    'sin papeles', 'sin documentos',
    'sobrepasar', 'sobrepasado', 'fuera de estatus',
    'presencia ilegal', 'castigo', 'penalidad',
    'inadmisible', 'inadmisibilidad', 'inelegible',
    'carga pública', 'antecedentes penales', 'récord criminal',
    
    # Family and marriage
    'matrimonio fraudulento', 'matrimonio falso',
    'divorcio durante', 'petición para esposo', 'petición para esposa',
    'k-1', 'prometido', 'prometida', 'fiancé',
    'hijastro', 'hijastra', 'adoptado', 'adoptada', 'adopción',
    
    # Employment
    'h-1b', 'l-1', 'o-1', 'e-2',
    'green card por trabajo', 'residencia por trabajo',
    'patrocinio de empleador', 'sponsor',
    'cambio de estatus', 'cambio de empleador',
    
    # Court and hearings
    'corte', 'tribunal', 'audiencia', 'juez', 'jueza',
    'corte de inmigración', 'eoir',
    'juicio', 'testimonio', 'testificar', 'testigo',
    'calendario maestro', 'audiencia individual',
    'bia', 'junta de apelaciones',
    
    # Agencies
    'uscis', 'ins', 'dhs', 'cbp', 'patrulla fronteriza',
    'consulado', 'embajada', 'entrevista de visa',
    'biométricos', 'huellas',
    
    # Legal advice red flags
    'consejo legal', 'asesoría legal', 'aconseje', 'recomiende',
    'qué pasa si', 'es legal', 'es ilegal',
    
    # Fees specifics
    'cuánto costará mi caso', 'honorarios totales', 'plan de pago',
    'honorarios de abogado', 'retención',
    
    # Sensitive personal circumstances
    'violencia doméstica', 'violencia familiar', 'abuso', 'abusada', 'abusado',
    'tráfico', 'trata de personas',
    'persecución', 'perseguido', 'perseguida',
    'miedo', 'temor', 'amenazado', 'amenazada',
    'pandilla', 'pandillas', 'violencia',
    
    # Urgency signals
    'urgente', 'emergencia', 'inmediatamente', 'ahora mismo',
    'ayuda', 'asustado', 'asustada', 'no sé qué hacer'
]

def contains_LEGAL_TRIGGER_WORDS(text):
    text_lower = text.lower()
    for word in LEGAL_TRIGGER_WORDS:
        if word in text_lower:
            return word
    return None

def contains_LEGAL_TRIGGER_WORDS_ES(text):
    text_lower = text.lower()
    for word in LEGAL_TRIGGER_WORDS_ES:
        if word in text_lower:
            return word
    return None



@app.route('/chat', methods=['POST'])
@csrf.exempt
def chat():
    print(f'Real IP: {get_real_ip()}') #try to remove it when not needed
    print(f'X-Forwarded-For header: {request.headers.get("X-Forwarded-For")}')
    if 'session_id' not in session:
        session['session_id']=secrets.token_hex(8)
    session_id = session['session_id']

    data = request.get_json() or {}
    user_message = data.get('message','').strip()
    if not user_message:
        return jsonify({'error':'Please send a message'}), 400


    triggered_word = contains_LEGAL_TRIGGER_WORDS(user_message) 
    if triggered_word:
        print(f'[HEALTH TRIGGER]"{triggered_word}" in session {session_id}:{user_message[:100]}')
        db.session.add(message(session_id=session_id, role='user', content=user_message))
        safety_reply = "Your message contains topics I can't help with — those need [Attorney Name]'s direct review. For questions about your specific case, please contact [Attorney Name] at [phone] or book a free consultation here: [link]. This chat handles general questions about the firm (hours, locations, practice areas, scheduling). Our attorney's practice area includes immigration, asylum, achieving green card....{clients field of work}"
        db.session.add(message(session_id=session_id, role='assistant', content=safety_reply))
        db.session.commit()

        return jsonify({'response': safety_reply})

    triggered_word_es = contains_LEGAL_TRIGGER_WORDS_ES(user_message)
    if triggered_word_es:
            print(f'[HEALTH TRIGGER ES]"{triggered_word}" in session {session_id}:{user_message[:100]}')
            db.session.add(message(session_id=session_id, role='user', content=user_message))
            safety_reply_es = "Su mensaje contiene un tema legal que requiere la revisión directa de [Attorney Name]. Este chat solo maneja preguntas generales sobre la firma (horarios, ubicaciones, áreas de práctica, y programación de citas). Para preguntas sobre su caso específico, por favor contacte a [Attorney Name] al [phone]."
            db.session.add(message(session_id=session_id, role='assistant', content=safety_reply_es))
            db.session.commit()

            return jsonify({'response': safety_reply_es})
    

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

    BUSINESS_TIMEZONE = ZoneInfo('America/Los_Angeles')

    current_time = datetime.now(BUSINESS_TIMEZONE).strftime('%A, %B %d, %Y at %I:%M %p %Z')

    full_msg = system_prompt + f'\n\nCurrent date and time (clinic local time): {current_time}' + '\n\nConversation so far: ' + conversation_context + '\n\nUser: ' + user_message
    


    try:
        response = client.models.generate_content(model= 'gemini-3.1-flash-lite', contents= full_msg )
        if not response.text:
            return jsonify({'error': 'No response generated, please rephrase'}), 500
        reply = response.text
    except Exception as error:
        print(f'Gemini API error: {error}')
        return jsonify({'error':'Something went wrong. Please try again.'}), 500

    db.session.add(message(session_id=session_id, role='assistant', content=reply))
    db.session.commit()
    return jsonify({'response':reply})


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

#for premium service admin panel

@app.route('/admin/chat/<session_id>')
def admin_conversation(session_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    messages = message.query.filter_by(session_id=session_id).order_by(message.id).all()

    return render_template('admin_conversation.html', messages=messages, session_id=session_id)

@app.route('/admin/login', methods=['GET', 'POST'])
@limiter.limit('5 per minute')
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        if compare_digest(password.encode('utf-8'), ADMIN_PASSWORD.encode('utf-8')):
            session['is_admin'] = True
            return redirect(url_for('admin_chats'))
        else:
            return render_template('admin_login.html', error='Incorrect password')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin_login'))

@app.route('/admin/chats')
def admin_chats():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    from sqlalchemy import func
    sessions_data = db.session.query(
        message.session_id,
        func.count(message.id).label('msg_count'),
        func.max(message.created_at).label('last_time'),
        func.min(message.created_at).label('first_time')
    ).group_by(message.session_id).order_by(func.max(message.created_at).desc()).all()
    sessions_list = []
    for session_id, msg_count, last_time, first_time in sessions_data:
        first_msg = message.query.filter_by(
            session_id = session_id,
            role='user'
        ).order_by(message.id).first()

        preview = first_msg.content[:100] if first_msg else '(no message)'

        sessions_list.append({
            'session_id': session_id,
            'msg_count': msg_count,
            'preview':preview,
            'first_time':first_time,
            'last_time':last_time
        })
    return render_template('admin_chats.html', sessions=sessions_list)
#delete option
@app.route('/admin/delete/<session_id>', methods=['POST'])
def admin_delete_conversation(session_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    message.query.filter_by(session_id=session_id).delete()
    db.session.commit()
    return redirect(url_for('admin_chats'))

@app.route('/admin/delete_all', methods=['POST'])
@limiter.limit('1 per hour')
def admin_delete_all():
    print(f"[ADMIN DELETE ALL] Triggered at {datetime.now()} from IP {get_real_ip()}")
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    message.query.delete()
    db.session.commit()

    return redirect(url_for('admin_chats'))
#delete
#admin panel block

#standard client block
@app.route('/cleanup', methods=['POST'])
@limiter.exempt
@csrf.exempt
def cleanup_old_messages():
    if request.headers.get('X-Cleanup-Token') != os.getenv('CLEANUP_TOKEN'):
        return jsonify({'error':'unauthorized'}), 401

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    deleted = message.query.filter(message.created_at < cutoff).delete()
    db.session.commit()

    print(f'[CLEANUP] Deleted {deleted} messages older than 30 days at {datetime.now(timezone.utc)}')
    return jsonify ({'ok':True, 'deleted':deleted }), 200
#standard client block

@app.errorhandler(429)
def rate_limit_exceeded(e):
    return jsonify({'error':'You are sending too many messages at once, please wait a moment.'}), 429

@app.route('/ping', methods=['GET'])
@limiter.exempt
def ping():
    return jsonify({'ok': True})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
    
        


    