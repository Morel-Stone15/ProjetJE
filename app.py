import os
from dotenv import load_dotenv
load_dotenv()

import uuid
import sqlite3
from datetime import datetime
# pyrefly: ignore [missing-import]
import segno

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'je_qrcode_manager_secret_key_12345')
DATABASE = 'database.db'

# Ensure directories exist
os.makedirs('static/css', exist_ok=True)
os.makedirs('static/js', exist_ok=True)
os.makedirs('static/qrcodes', exist_ok=True)
os.makedirs('debug_emails', exist_ok=True)

def get_db():
    db_path = app.config.get('DATABASE', DATABASE)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            club_name TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT ''
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            name TEXT NOT NULL,
            date TEXT NOT NULL,
            location TEXT NOT NULL DEFAULT '',
            description TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (admin_id) REFERENCES admins (id) ON DELETE CASCADE
        )
    ''')
    # Migration: add location column if it doesn't exist yet
    try:
        cursor.execute("ALTER TABLE events ADD COLUMN location TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass

    # Migration: add admin_id column if it doesn't exist yet
    event_cols = [row[1] for row in cursor.execute("PRAGMA table_info(events)").fetchall()]
    if 'admin_id' not in event_cols:
        try:
            cursor.execute("ALTER TABLE events ADD COLUMN admin_id INTEGER REFERENCES admins(id) ON DELETE CASCADE")
        except Exception:
            pass

    # Migration: add club_name and email columns to admins if they don't exist yet
    admin_cols = [row[1] for row in cursor.execute("PRAGMA table_info(admins)").fetchall()]
    if 'club_name' not in admin_cols:
        try:
            cursor.execute("ALTER TABLE admins ADD COLUMN club_name TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
    if 'email' not in admin_cols:
        try:
            cursor.execute("ALTER TABLE admins ADD COLUMN email TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER,
            nom TEXT NOT NULL DEFAULT '',
            prenom TEXT NOT NULL DEFAULT '',
            classe TEXT NOT NULL DEFAULT '',
            departement TEXT NOT NULL DEFAULT '',
            telephone TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL,
            ticket_token TEXT UNIQUE NOT NULL,
            checked_in INTEGER DEFAULT 0,
            checked_in_at DATETIME,
            FOREIGN KEY (event_id) REFERENCES events (id) ON DELETE CASCADE
        )
    ''')
    # Ensure unique email per event (case-insensitive)
    try:
        cursor.execute('DROP INDEX IF EXISTS idx_participants_event_email')
        cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_participants_event_email ON participants(event_id, email COLLATE NOCASE)')
    except Exception:
        pass

    # ── Smart migration ──────────────────────────────────────────────────────
    # Detect old schema: if 'name' column exists the table must be rebuilt.
    cols = [row[1] for row in cursor.execute("PRAGMA table_info(participants)").fetchall()]
    if 'name' in cols:
        print("[DB-MIGRATION] Ancienne colonne 'name' détectée — migration de la table participants…")
        cursor.execute("ALTER TABLE participants RENAME TO participants_old")
        cursor.execute('''
            CREATE TABLE participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER,
                nom TEXT NOT NULL DEFAULT '',
                prenom TEXT NOT NULL DEFAULT '',
                classe TEXT NOT NULL DEFAULT '',
                departement TEXT NOT NULL DEFAULT '',
                telephone TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL,
                ticket_token TEXT UNIQUE NOT NULL,
                checked_in INTEGER DEFAULT 0,
                checked_in_at DATETIME,
                FOREIGN KEY (event_id) REFERENCES events (id) ON DELETE CASCADE
            )
        ''')
        # Ensure unique email per event for new table (case-insensitive)
        try:
            cursor.execute('DROP INDEX IF EXISTS idx_participants_event_email')
            cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_participants_event_email ON participants(event_id, email COLLATE NOCASE)')
        except Exception:
            pass
        # Copy rows – map old `name` into `nom` (prenom stays empty for legacy rows)
        cursor.execute('''
            INSERT INTO participants
                (id, event_id, nom, prenom, classe, departement, telephone,
                 email, ticket_token, checked_in, checked_in_at)
            SELECT
                id, event_id,
                COALESCE(nom, name, ''),
                COALESCE(prenom, ''),
                COALESCE(classe, ''),
                COALESCE(departement, ''),
                COALESCE(telephone, ''),
                email, ticket_token, checked_in, checked_in_at
            FROM participants_old
        ''')
        cursor.execute("DROP TABLE participants_old")
        print("[DB-MIGRATION] Migration terminée avec succès.")
    else:
        # Fresh install: add any new columns gracefully just in case
        # Ensure emails are stored lower‑cased for existing rows
        try:
            conn.execute('UPDATE participants SET email = LOWER(email)')
        except Exception:
            pass

    # Seed default admin
    cursor.execute("SELECT COUNT(*) FROM admins")
    if cursor.fetchone()[0] == 0:
        default_username = 'admin'
        default_password = 'adminje'
        hashed_password = generate_password_hash(default_password)
        cursor.execute(
            "INSERT INTO admins (username, password_hash, club_name, email) VALUES (?, ?, ?, ?)",
            (default_username, hashed_password, 'Junior Entreprise', 'je@university.edu')
        )
        print(f"\n[DB] Compte admin créé — Identifiant: '{default_username}' | Mot de passe: '{default_password}'\n")
    else:
        # Ensure first admin has defaults if empty
        cursor.execute("UPDATE admins SET club_name = 'Junior Entreprise', email = 'je@university.edu' WHERE id = 1 AND (club_name = '' OR club_name IS NULL)")

    # Associate old events with admin_id = 1
    cursor.execute("UPDATE events SET admin_id = 1 WHERE admin_id IS NULL")

    conn.commit()
    conn.close()

init_db()

# ── Helpers ──────────────────────────────────────────────────────────────────

def format_date(raw_date):
    """Convert 'YYYY-MM-DDTHH:MM' to 'DD/MM/YYYY à HH:MM'."""
    try:
        dt = datetime.strptime(raw_date, '%Y-%m-%dT%H:%M')
        return dt.strftime('%d/%m/%Y à %H:%M')
    except Exception:
        return raw_date

app.jinja_env.globals['format_date'] = format_date
app.jinja_env.filters['format_date'] = format_date
app.jinja_env.globals['datetime'] = datetime

def generate_qr_code(token):
    """Generate (or regenerate) the QR code PNG for a ticket token.
    The QR encodes the direct ticket URL so that any standard QR reader
    (not just the built-in scanner) can open the ticket page.
    Returns the relative path of the saved PNG file.
    """
    ticket_url = f"{request.host_url.rstrip('/')}/ticket/{token}"
    qr = segno.make(ticket_url)
    qr_path = f"static/qrcodes/{token}.png"
    qr.save(qr_path, scale=6, border=4)
    return qr_path

def build_email_html(
    prenom: str,
    nom: str,
    event_name: str,
    event_date: str,
    event_location: str,
    token: str,
) -> str:
    """Generate the HTML body for the confirmation email."""
    html = f"""
    <html>
      <body style="font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #1e293b; background-color: #f8fafc; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background: #ffffff; padding: 30px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); border: 1px solid #e2e8f0;">
          <div style="text-align: center; margin-bottom: 24px;">
            <span style="font-size: 24px; font-weight: bold; color: #3b82f6;">⚡ Junior Entreprise</span>
          </div>
          <h2 style="color: #0f172a; font-size: 20px; font-weight: 700; margin-bottom: 16px; text-align: center;">Votre billet d'entrée</h2>
          <p>Bonjour <strong>{prenom} {nom}</strong>,</p>
          <p>Votre inscription pour l'événement <strong>{event_name}</strong> a bien été enregistrée.</p>
          <p style="margin: 4px 0;">📅 <strong>Date :</strong> {format_date(event_date)}</p>
          <p style="margin: 4px 0 20px;">📍 <strong>Lieu :</strong> {event_location or 'À confirmer'}</p>
          <div style="text-align: center; margin: 20px 0;">
             <img src="cid:qrcode_img" alt="QR Code" style="width: 200px; height: 200px;">
          </div>
          <hr style="border:0;border-top:1px solid #e2e8f0;margin:30px 0;">
          <p style="font-size:12px;color:#64748b;text-align:center;margin:0;">
            Cet e-mail est généré automatiquement. Ne pas répondre.
          </p>
        </div>
      </body>
    </html>
    """
    return html
def send_confirmation_email(prenom, nom, email, event_name, event_date, event_location, token, qr_file_path):
    import requests

    emailjs_service_id = os.environ.get('EMAILJS_SERVICE_ID', '')
    emailjs_template_id = os.environ.get('EMAILJS_TEMPLATE_ID', '')
    emailjs_public_key = os.environ.get('EMAILJS_PUBLIC_KEY', '')
    emailjs_private_key = os.environ.get('EMAILJS_PRIVATE_KEY', '')

    smtp_server = os.environ.get('SMTP_SERVER', '')
    smtp_port = int(os.environ.get('SMTP_PORT', '587'))
    smtp_user = os.environ.get('SMTP_USER', '')
    smtp_password = os.environ.get('SMTP_PASSWORD', '')
    smtp_sender = os.environ.get('SMTP_SENDER', 'noreply@junior-entreprise.com')

    html_content = build_email_html(prenom, nom, event_name, event_date, event_location, token)
    qr_code_url = f"{request.host_url.rstrip('/')}/static/qrcodes/{token}.png"

    # --- Choice A: EmailJS (if configured) ---
    if emailjs_service_id and emailjs_template_id and emailjs_public_key:
        payload = {
            "service_id": emailjs_service_id,
            "template_id": emailjs_template_id,
            "user_id": emailjs_public_key,
            "template_params": {
                "to_name": f"{prenom} {nom}",
                "to_email": email,
                "event_name": event_name,
                "event_date": format_date(event_date),
                "event_location": event_location or 'À confirmer',
                "ticket_id": token,
                "qr_code_url": qr_code_url,
                "ticket_web_url": f"{request.host_url.rstrip('/')}/ticket/{token}"
            }
        }
        if emailjs_private_key:
            payload["accessToken"] = emailjs_private_key
            
        try:
            response = requests.post("https://api.emailjs.com/api/v1.0/email/send", json=payload)
            if response.status_code == 200:
                print(f"[EMAILJS] E-mail envoyé avec succès : {response.text}")
                return True
            else:
                print(f"[EMAILJS-ERROR] Status: {response.status_code}, Text: {response.text}")
                raise Exception(response.text)
        except Exception as e:
            print(f"[EMAILJS-ERROR] {e}")
            debug_path = os.path.join('debug_emails', f"email_{token}_failed.html")
            local_html = html_content.replace('cid:qrcode_img', f"../static/qrcodes/{token}.png")
            with open(debug_path, 'w', encoding='utf-8') as f:
                f.write(local_html + f"\n<!-- ERROR EmailJS: {e} -->")
            return False

    # --- Choice B: SMTP (if configured) ---
    if smtp_server and smtp_user and smtp_password:
        try:
            msg = MIMEMultipart('related')
            msg['Subject'] = f"Confirmation d'inscription : {event_name}"
            msg['From']    = smtp_sender
            msg['To']      = email
            alt = MIMEMultipart('alternative')
            msg.attach(alt)
            alt.attach(MIMEText(html_content, 'html', 'utf-8'))
            with open(qr_file_path, 'rb') as img_f:
                img = MIMEImage(img_f.read())
                img.add_header('Content-ID', '<qrcode_img>')
                img.add_header('Content-Disposition', 'inline', filename=f"qrcode_{token}.png")
                msg.attach(img)
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_sender, email, msg.as_string())
            server.quit()
            print(f"[EMAIL-SMTP] Envoyé à {email}")
            return True
        except Exception as e:
            print(f"[EMAIL-SMTP-ERROR] {e}")
            debug_path = os.path.join('debug_emails', f"email_{token}_failed.html")
            local_html = html_content.replace('cid:qrcode_img', f"../static/qrcodes/{token}.png")
            with open(debug_path, 'w', encoding='utf-8') as f:
                f.write(local_html + f"\n<!-- ERROR SMTP: {e} -->")
            return False

    # --- Choice C: Fallback Local Debug File ---
    debug_path = os.path.join('debug_emails', f"email_{token}.html")
    local_html = html_content.replace('cid:qrcode_img', f"../static/qrcodes/{token}.png")
    with open(debug_path, 'w', encoding='utf-8') as f:
        f.write(local_html)
    print(f"[EMAIL-DEBUG] SMTP/EmailJS non configurés. E-mail sauvegardé dans {debug_path}")
    return False

def build_club_welcome_html(club_name, username):
    html = f"""
    <html>
      <body style="font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #1e293b; background-color: #f8fafc; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background: #ffffff; padding: 30px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); border: 1px solid #e2e8f0;">
          <div style="text-align: center; margin-bottom: 24px;">
            <span style="font-size: 24px; font-weight: bold; color: #3b82f6;">⚡ Campus QR Event</span>
          </div>
          <h2 style="color: #0f172a; font-size: 20px; font-weight: 700; margin-bottom: 16px; text-align: center;">Bienvenue sur Campus QR Event !</h2>
          <p>Bonjour <strong>{club_name}</strong>,</p>
          <p>Le compte de votre association/club a été créé avec succès sur la plateforme <strong>Campus QR Event</strong>.</p>
          <div style="background: #f1f5f9; border-radius: 8px; padding: 15px; margin: 20px 0; border: 1px solid #e2e8f0;">
            <p style="margin: 0;">👤 <strong>Identifiant de connexion :</strong> <code style="background:#e2e8f0; padding:2px 6px; border-radius:4px;">{username}</code></p>
          </div>
          <p>Vous pouvez dès à présent vous connecter pour créer vos événements, générer des QR Codes d'accès et suivre le check-in de vos participants en temps réel.</p>
          <hr style="border:0;border-top:1px solid #e2e8f0;margin:30px 0;">
          <p style="font-size:12px;color:#64748b;text-align:center;margin:0;">
            Cet e-mail est généré automatiquement. Ne pas répondre.
          </p>
        </div>
      </body>
    </html>
    """
    return html

def send_club_registration_email(club_name, email, username):
    import requests

    emailjs_service_id = os.environ.get('EMAILJS_SERVICE_ID', '')
    emailjs_welcome_template_id = os.environ.get('EMAILJS_WELCOME_TEMPLATE_ID', '') or os.environ.get('EMAILJS_TEMPLATE_ID', '')
    emailjs_public_key = os.environ.get('EMAILJS_PUBLIC_KEY', '')
    emailjs_private_key = os.environ.get('EMAILJS_PRIVATE_KEY', '')

    smtp_server = os.environ.get('SMTP_SERVER', '')
    smtp_port = int(os.environ.get('SMTP_PORT', '587'))
    smtp_user = os.environ.get('SMTP_USER', '')
    smtp_password = os.environ.get('SMTP_PASSWORD', '')
    smtp_sender = os.environ.get('SMTP_SENDER', 'noreply@junior-entreprise.com')

    html_content = build_club_welcome_html(club_name, username)

    # --- Choice A: EmailJS (if configured) ---
    if emailjs_service_id and emailjs_welcome_template_id and emailjs_public_key:
        payload = {
            "service_id": emailjs_service_id,
            "template_id": emailjs_welcome_template_id,
            "user_id": emailjs_public_key,
            "template_params": {
                "to_name": club_name,
                "to_email": email,
                "username": username
            }
        }
        if emailjs_private_key:
            payload["accessToken"] = emailjs_private_key
            
        try:
            response = requests.post("https://api.emailjs.com/api/v1.0/email/send", json=payload)
            if response.status_code == 200:
                print(f"[EMAILJS-WELCOME] E-mail envoyé avec succès : {response.text}")
                return True
            else:
                print(f"[EMAILJS-WELCOME-ERROR] Status: {response.status_code}, Text: {response.text}")
                raise Exception(response.text)
        except Exception as e:
            print(f"[EMAILJS-WELCOME-ERROR] {e}")
            debug_path = os.path.join('debug_emails', f"welcome_{username}_failed.html")
            with open(debug_path, 'w', encoding='utf-8') as f:
                f.write(html_content + f"\n<!-- ERROR EmailJS: {e} -->")
            return False

    # --- Choice B: SMTP (if configured) ---
    if smtp_server and smtp_user and smtp_password:
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = "Confirmation d'ouverture de compte Club"
            msg['From']    = smtp_sender
            msg['To']      = email
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_sender, email, msg.as_string())
            server.quit()
            print(f"[EMAIL-WELCOME-SMTP] Envoyé à {email}")
            return True
        except Exception as e:
            print(f"[EMAIL-WELCOME-SMTP-ERROR] {e}")
            debug_path = os.path.join('debug_emails', f"welcome_{username}_failed.html")
            with open(debug_path, 'w', encoding='utf-8') as f:
                f.write(html_content + f"\n<!-- ERROR SMTP: {e} -->")
            return False

    # --- Choice C: Fallback Local Debug File ---
    debug_path = os.path.join('debug_emails', f"welcome_{username}.html")
    with open(debug_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"[EMAIL-WELCOME-DEBUG] E-mail de bienvenue sauvegardé dans {debug_path}")
    return False

def login_required(f):
    import functools
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            flash("Veuillez vous connecter pour accéder à cette page.", "error")
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

# ── PUBLIC ROUTES ─────────────────────────────────────────────────────────────

@app.route('/')
def index():
    conn   = get_db()
    events = conn.execute("""
        SELECT e.*, a.club_name as organizer_name
        FROM events e
        LEFT JOIN admins a ON e.admin_id = a.id
        ORDER BY e.date DESC
    """).fetchall()
    conn.close()
    return render_template('index.html', events=events)

@app.route('/event/<int:event_id>/register', methods=['GET', 'POST'])
def event_register(event_id):
    conn  = get_db()
    event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if not event:
        conn.close()
        return "Événement introuvable", 404

    if request.method == 'POST':
        nom         = request.form.get('nom', '').strip()
        prenom      = request.form.get('prenom', '').strip()
        classe      = request.form.get('classe', '').strip()
        departement = request.form.get('departement', '').strip()
        telephone   = request.form.get('telephone', '').strip()
        email       = request.form.get('email', '').strip().lower()

        if not all([nom, prenom, classe, departement, telephone, email]):
            flash("Tous les champs sont obligatoires.", "error")
            conn.close()
            return redirect(url_for('event_register', event_id=event_id))

        token   = str(uuid.uuid4())
        # Generate QR code that encodes a direct ticket URL
        qr_path = generate_qr_code(token)

        # Check for duplicate email for this event
        existing = conn.execute(
            "SELECT 1 FROM participants WHERE event_id = ? AND email = ?",
            (event_id, email)
        ).fetchone()
        if existing:
            flash("Cette adresse email est déjà inscrite à cet événement.", "error")
            conn.close()
            return redirect(url_for('event_register', event_id=event_id))
        try:
            # Save participant to DB
            conn.execute(
                "INSERT INTO participants (event_id, nom, prenom, classe, departement, telephone, email, ticket_token) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, nom, prenom, classe, departement, telephone, email, token)
            )
            conn.commit()
            # Send confirmation email
            # Send confirmation email (ignore return value)
            send_confirmation_email(
                prenom=prenom, nom=nom, email=email,
                event_name=event['name'], event_date=event['date'],
                event_location=event['location'],
                token=token, qr_file_path=qr_path
            )
            conn.close()
            return redirect(url_for('register_success', token=token))
        except sqlite3.IntegrityError:
            conn.close()
            flash("Erreur inattendue lors de l'inscription. Veuillez réessayer.", "error")
            return redirect(url_for('event_register', event_id=event_id))

    conn.close()
    return render_template('register.html', event=event)

@app.route('/register/success/<string:token>')
def register_success(token):
    conn = get_db()
    participant = conn.execute(
        """SELECT p.*, e.name as event_name, e.date as event_date, e.location as event_location
           FROM participants p JOIN events e ON p.event_id = e.id
           WHERE p.ticket_token = ?""",
        (token,)
    ).fetchone()
    conn.close()
    if not participant:
        return "Inscription introuvable", 404
    return render_template(
        'register_success.html',
        participant=participant,
        emailjs_public_key=os.environ.get('EMAILJS_PUBLIC_KEY', ''),
        emailjs_service_id=os.environ.get('EMAILJS_SERVICE_ID', ''),
        emailjs_template_id=os.environ.get('EMAILJS_TEMPLATE_ID', '')
    )

@app.route('/ticket/<string:token>')
def ticket_view(token):
    conn = get_db()
    participant = conn.execute(
        """SELECT p.*, e.name as event_name, e.date as event_date, e.location as event_location
           FROM participants p JOIN events e ON p.event_id = e.id WHERE p.ticket_token = ?""",
        (token,)
    ).fetchone()
    conn.close()
    if not participant:
        return "Ticket introuvable", 404
    # If admin is logged in, show validation view; otherwise public ticket
    if session.get('logged_in'):
        return render_template('admin_ticket.html', participant=participant)
    else:
        return render_template('ticket.html', participant=participant)


@app.route('/admin')
@app.route('/admin/')
def admin_index():
    if session.get('logged_in'):
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('admin_login'))

@app.route('/admin/register', methods=['GET', 'POST'])
def admin_register():
    if session.get('logged_in'):
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        club_name = request.form.get('club_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '').strip()

        if not all([username, club_name, email, password]):
            flash("Tous les champs sont obligatoires.", "error")
            return render_template('register_club.html')

        conn = get_db()
        # Check if username or email already exists
        existing_user = conn.execute(
            "SELECT 1 FROM admins WHERE username = ? OR email = ?",
            (username, email)
        ).fetchone()
        if existing_user:
            flash("Cet identifiant ou cet e-mail est déjà utilisé.", "error")
            conn.close()
            return render_template('register_club.html')

        try:
            hashed_pwd = generate_password_hash(password)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO admins (username, password_hash, club_name, email) VALUES (?, ?, ?, ?)",
                (username, hashed_pwd, club_name, email)
            )
            conn.commit()
            conn.close()

            # Render success page — the welcome e-mail is triggered from the browser
            # via the EmailJS SDK to bypass server-side API restrictions.
            return render_template(
                'register_club_success.html',
                club_name=club_name,
                email=email,
                username=username,
                emailjs_public_key=os.environ.get('EMAILJS_PUBLIC_KEY', ''),
                emailjs_service_id=os.environ.get('EMAILJS_SERVICE_ID', ''),
                emailjs_welcome_template_id=os.environ.get('EMAILJS_WELCOME_TEMPLATE_ID', ''),
                emailjs_ticket_template_id=os.environ.get('EMAILJS_TEMPLATE_ID', ''),
            )
        except Exception as e:
            conn.close()
            flash("Une erreur est survenue lors de l'inscription.", "error")
            print(f"[REGISTER-ERROR] {e}")

    return render_template('register_club.html')

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('logged_in'):
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        conn  = get_db()
        admin = conn.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
        conn.close()
        if admin and check_password_hash(admin['password_hash'], password):
            session['logged_in'] = True
            session['username']  = username
            session['admin_id']  = admin['id']
            session['club_name']  = admin['club_name'] or username
            flash("Connexion réussie.", "success")
            return redirect(url_for('admin_dashboard'))
        flash("Identifiants incorrects.", "error")
    return render_template('login.html')

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    flash("Vous avez été déconnecté.", "success")
    return redirect(url_for('admin_login'))

@app.route('/admin/dashboard', methods=['GET', 'POST'])
@login_required
def admin_dashboard():
    conn = get_db()
    if request.method == 'POST':
        name        = request.form.get('name', '').strip()
        date        = request.form.get('date', '').strip()
        location    = request.form.get('location', '').strip()
        description = request.form.get('description', '').strip()
        if not name or not date:
            flash("Le nom et la date sont requis.", "error")
        else:
            conn.execute(
                "INSERT INTO events (admin_id, name, date, location, description) VALUES (?, ?, ?, ?, ?)",
                (session['admin_id'], name, date, location, description)
            )
            conn.commit()
            flash("Événement créé avec succès.", "success")
            conn.close()
            return redirect(url_for('admin_dashboard'))

    events = conn.execute("""
        SELECT e.*, COUNT(p.id) as participant_count,
               COALESCE(SUM(p.checked_in), 0) as checked_in_count
        FROM events e
        LEFT JOIN participants p ON e.id = p.event_id
        WHERE e.admin_id = ?
        GROUP BY e.id
        ORDER BY e.date DESC
    """, (session['admin_id'],)).fetchall()
    conn.close()
    return render_template('dashboard.html', events=events)

@app.route('/admin/event/<int:event_id>', methods=['GET', 'POST'])
@login_required
def event_detail(event_id):
    conn  = get_db()
    event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if not event:
        conn.close()
        return "Événement introuvable", 404

    if event['admin_id'] != session['admin_id']:
        conn.close()
        return "Accès interdit : Cet événement appartient à un autre club.", 403

    if request.method == 'POST':
        nom         = request.form.get('nom', '').strip()
        prenom      = request.form.get('prenom', '').strip()
        classe      = request.form.get('classe', '').strip()
        departement = request.form.get('departement', '').strip()
        telephone   = request.form.get('telephone', '').strip()
        email       = request.form.get('email', '').strip().lower()

        if not all([nom, prenom, classe, departement, telephone, email]):
            flash("Tous les champs sont obligatoires.", "error")
        else:
            token   = str(uuid.uuid4())
            qr_path = generate_qr_code(token)
            # Check for duplicate email in this event
            existing = conn.execute(
                "SELECT 1 FROM participants WHERE event_id = ? AND email = ?",
                (event_id, email)
            ).fetchone()
            if existing:
                flash("Cette adresse email est déjà inscrite à cet événement.", "error")
                conn.close()
                return redirect(url_for('event_detail', event_id=event_id))
            try:
                conn.execute(
                    """INSERT INTO participants
                       (event_id, nom, prenom, classe, departement, telephone, email, ticket_token)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (event_id, nom, prenom, classe, departement, telephone, email, token)
                )
                conn.commit()
                flash(f"Participant {prenom} {nom} ajouté.", "success")
                send_confirmation_email(
                    prenom=prenom, nom=nom, email=email,
                    event_name=event['name'], event_date=event['date'],
                    event_location=event['location'],
                    token=token, qr_file_path=qr_path
                )
            except sqlite3.IntegrityError:
                flash("Cet e-mail est déjà inscrit.", "error")
        conn.close()
        return redirect(url_for('event_detail', event_id=event_id))

    participants = conn.execute(
        """SELECT * FROM participants WHERE event_id = ?
           ORDER BY checked_in ASC, nom ASC""",
        (event_id,)
    ).fetchall()
    conn.close()
    register_link = f"{request.host_url.rstrip('/')}/event/{event_id}/register"
    return render_template('event_detail.html', event=event,
                           participants=participants, register_link=register_link)

@app.route('/admin/event/<int:event_id>/delete', methods=['POST'])
@login_required
def delete_event(event_id):
    conn = get_db()
    event = conn.execute("SELECT admin_id FROM events WHERE id = ?", (event_id,)).fetchone()
    if not event or event['admin_id'] != session['admin_id']:
        conn.close()
        return "Accès interdit : Cet événement appartient à un autre club.", 403
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()
    flash("Événement supprimé.", "success")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/participant/<int:part_id>/delete', methods=['POST'])
@login_required
def delete_participant(part_id):
    conn        = get_db()
    participant = conn.execute(
        """SELECT p.event_id, e.admin_id
           FROM participants p JOIN events e ON p.event_id = e.id
           WHERE p.id = ?""",
        (part_id,)
    ).fetchone()
    if participant:
        if participant['admin_id'] != session['admin_id']:
            conn.close()
            return "Accès interdit : Cet événement appartient à un autre club.", 403
        event_id = participant['event_id']
        conn.execute("DELETE FROM participants WHERE id = ?", (part_id,))
        conn.commit()
        conn.close()
        flash("Participant supprimé.", "success")
        return redirect(url_for('event_detail', event_id=event_id))
    conn.close()
    return "Participant introuvable", 404

@app.route('/admin/participant/<int:part_id>/resend', methods=['POST'])
@login_required
def resend_ticket(part_id):
    """Re-send the confirmation e-mail (or re-save to debug_emails) for a participant."""
    conn = get_db()
    row  = conn.execute(
        """SELECT p.*, e.name as event_name, e.date as event_date, e.location as event_location, e.admin_id
           FROM participants p JOIN events e ON p.event_id = e.id
           WHERE p.id = ?""",
        (part_id,)
    ).fetchone()
    if not row:
        conn.close()
        flash("Participant introuvable.", "error")
        return redirect(url_for('admin_dashboard'))

    if row['admin_id'] != session['admin_id']:
        conn.close()
        return "Accès interdit : Cet événement appartient à un autre club.", 403

    conn.close()

    qr_path = f"static/qrcodes/{row['ticket_token']}.png"
    if not os.path.exists(qr_path):
        generate_qr_code(row['ticket_token'])

    sent = send_confirmation_email(
        prenom=row['prenom'], nom=row['nom'], email=row['email'],
        event_name=row['event_name'], event_date=row['event_date'],
        event_location=row['event_location'],
        token=row['ticket_token'], qr_file_path=qr_path
    )
    if sent:
        flash(f"E-mail de confirmation renvoyé à {row['email']}.", "success")
    else:
        flash(f"SMTP non configuré — billet disponible dans debug_emails/.", "error")
    return redirect(url_for('event_detail', event_id=row['event_id']))

@app.route('/admin/scan')
@login_required
def scan():
    return render_template('scan.html')

@app.route('/admin/api/checkin', methods=['POST'])
@login_required
def api_checkin():
    data  = request.get_json() or {}
    raw   = data.get('token', '').strip()
    if not raw:
        return jsonify({'status': 'invalid', 'message': 'Token vide.'}), 400

    # The QR code encodes the full ticket URL (e.g. https://host/ticket/<token>)
    # so a scanned value may be a URL rather than a bare token — extract the token.
    if raw.startswith('http://') or raw.startswith('https://'):
        token = raw.rstrip('/').split('/')[-1]
    else:
        token = raw

    conn        = get_db()
    participant = conn.execute(
        """SELECT p.*, e.name as event_name, e.admin_id
           FROM participants p JOIN events e ON p.event_id = e.id
           WHERE p.ticket_token = ?""",
        (token,)
    ).fetchone()

    if not participant:
        conn.close()
        return jsonify({'status': 'invalid', 'message': 'QR code invalide ou ticket inconnu.'})

    if participant['admin_id'] != session['admin_id']:
        conn.close()
        return jsonify({'status': 'invalid', 'message': 'Accès refusé : Ce ticket appartient à un autre club.'})

    full_name = f"{participant['prenom']} {participant['nom']}"

    if participant['checked_in'] == 1:
        conn.close()
        return jsonify({
            'status': 'already_checked_in',
            'name': full_name,
            'event': participant['event_name'],
            'checked_in_at': participant['checked_in_at'],
            'message': f"Déjà scanné pour {full_name}."
        })

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        "UPDATE participants SET checked_in = 1, checked_in_at = ? WHERE id = ?",
        (now_str, participant['id'])
    )
    conn.commit()
    conn.close()
    return jsonify({
        'status': 'success',
        'name': full_name,
        'event': participant['event_name'],
        'message': f"Entrée validée pour {full_name}."
    })

@app.route('/admin/participant/<int:part_id>/checkin_direct', methods=['POST'])
@login_required
def checkin_direct(part_id):
    """Directly checks in a participant by ID (for admin mobile scan or link click)."""
    conn = get_db()
    participant = conn.execute(
        """SELECT p.ticket_token, p.nom, p.prenom, e.admin_id
           FROM participants p JOIN events e ON p.event_id = e.id
           WHERE p.id = ?""",
        (part_id,)
    ).fetchone()
    if not participant:
        conn.close()
        flash("Participant introuvable.", "error")
        return redirect(url_for('admin_dashboard'))
        
    if participant['admin_id'] != session['admin_id']:
        conn.close()
        return "Accès interdit : Cet événement appartient à un autre club.", 403
    
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        "UPDATE participants SET checked_in = 1, checked_in_at = ? WHERE id = ?",
        (now_str, part_id)
    )
    conn.commit()
    conn.close()
    flash(f"Entrée validée avec succès pour {participant['prenom']} {participant['nom']}.", "success")
    return redirect(url_for('ticket_view', token=participant['ticket_token']))

@app.route('/admin/participant/<int:part_id>/uncheckin_direct', methods=['POST'])
@login_required
def uncheckin_direct(part_id):
    """Cancels/reverts the check-in of a participant by ID."""
    conn = get_db()
    participant = conn.execute(
        """SELECT p.ticket_token, p.nom, p.prenom, e.admin_id
           FROM participants p JOIN events e ON p.event_id = e.id
           WHERE p.id = ?""",
        (part_id,)
    ).fetchone()
    if not participant:
        conn.close()
        flash("Participant introuvable.", "error")
        return redirect(url_for('admin_dashboard'))
        
    if participant['admin_id'] != session['admin_id']:
        conn.close()
        return "Accès interdit : Cet événement appartient à un autre club.", 403
    
    conn.execute(
        "UPDATE participants SET checked_in = 0, checked_in_at = NULL WHERE id = ?",
        (part_id,)
    )
    conn.commit()
    conn.close()
    flash(f"Validation annulée pour {participant['prenom']} {participant['nom']}.", "success")
    return redirect(url_for('ticket_view', token=participant['ticket_token']))

def reset_database_and_qrcodes():
    """Delete the SQLite DB file and all generated QR code images, then re‑initialise the schema."""
    # Delete DB file if it exists
    if os.path.exists(DATABASE):
        os.remove(DATABASE)
        print('[RESET] Database file removed')
    # Remove all QR code PNG files
    qr_folder = os.path.join('static', 'qrcodes')
    if os.path.isdir(qr_folder):
        for f in os.listdir(qr_folder):
            if f.lower().endswith('.png'):
                os.remove(os.path.join(qr_folder, f))
        print('[RESET] QR code files cleared')
    # Re‑create the DB schema
    init_db()
    print('[RESET] Database re‑initialised')

@app.route('/admin/reset', methods=['POST'])
@login_required
def admin_reset():
    """Endpoint for admin to completely reset the application state.
    Returns a simple JSON response indicating success."""
    reset_database_and_qrcodes()
    return jsonify({'status': 'ok', 'message': 'Database and QR codes have been reset'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
