import os
import unittest
import tempfile
import sqlite3
from app import app, get_db, init_db
from flask import session

class JEProjectTestCase(unittest.TestCase):

    def setUp(self):
        # Configure app for testing
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        
        # Create a temporary database file
        self.db_fd, self.db_path = tempfile.mkstemp()
        os.close(self.db_fd) # Close immediately to prevent Windows file locking
        app.config['DATABASE'] = self.db_path
        
        # Override the database file path globally in app module
        import app as app_module
        self.original_database = app_module.DATABASE
        app_module.DATABASE = self.db_path
        
        # Initialize test database
        init_db()
        self.client = app.test_client()

    def tearDown(self):
        # Restore original database configurations
        import app as app_module
        app_module.DATABASE = self.original_database
        
        # Safely remove the temporary database file
        if os.path.exists(self.db_path):
            try:
                os.unlink(self.db_path)
            except OSError:
                pass

    def login(self, username, password):
        return self.client.post('/admin/login', data=dict(
            username=username,
            password=password
        ), follow_redirects=True)

    def logout(self):
        return self.client.get('/admin/logout', follow_redirects=True)

    def test_default_admin_created(self):
        """Test if the default admin is created on db initialization."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM admins WHERE username = 'admin'")
        admin = cursor.fetchone()
        conn.close()
        self.assertIsNotNone(admin)
        self.assertEqual(admin[0], 'admin')

    def test_login_logout(self):
        """Test administrative login and logout flows."""
        # Try invalid login
        response = self.login('admin', 'wrongpassword')
        self.assertIn(b'Identifiants incorrects', response.data)
        
        # Try valid login
        response = self.login('admin', 'adminje')
        self.assertIn(b'Tableau de Bord', response.data)
        
        # Try logout
        response = self.logout()
        has_deconnect_msg = (
            b'Vous avez' in response.data or 
            b'deconnect' in response.data or 
            b'Connexion' in response.data
        )
        self.assertTrue(has_deconnect_msg)

    def test_dashboard_access_restricted(self):
        """Test that the dashboard is inaccessible without logging in."""
        response = self.client.get('/admin/dashboard', follow_redirects=True)
        self.assertIn(b'Veuillez vous connecter', response.data)

    def test_create_event_and_register(self):
        """Test event creation and public registration flow."""
        # Log in first
        self.login('admin', 'adminje')
        
        # Create an event
        response = self.client.post('/admin/dashboard', data=dict(
            name='Test Event',
            date='2026-12-31T18:00',
            location='Amphi A',
            description='This is a unit test event.'
        ), follow_redirects=True)
        self.assertIn(b'Test Event', response.data)
        
        # Verify event in DB
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM events WHERE name = 'Test Event'")
        event = cursor.fetchone()
        self.assertIsNotNone(event)
        event_id = event[0]
        conn.close()
        
        # Register a participant for this event (Public Flow)
        response = self.client.post(f'/event/{event_id}/register', data=dict(
            nom='Doe',
            prenom='John',
            classe='M1 Info',
            departement='Genie Logiciel',
            telephone='0600000000',
            email='john.doe@test.com'
        ), follow_redirects=True)
        
        has_success_info = (
            b'Inscription' in response.data or 
            b'John' in response.data
        )
        self.assertTrue(has_success_info)
        
        # Get participant ticket token from DB
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT ticket_token, checked_in FROM participants WHERE nom = 'Doe' AND prenom = 'John'")
        participant = cursor.fetchone()
        self.assertIsNotNone(participant)
        token = participant[0]
        checked_in = participant[1]
        self.assertEqual(checked_in, 0)
        conn.close()
        
        # Test Check-in API (simulate scan) without admin login first (should fail)
        self.logout()
        response = self.client.post('/admin/api/checkin', json=dict(token=token))
        self.assertEqual(response.status_code, 302) # Redirect to login
        
        # Log in again and validate scan check-in
        self.login('admin', 'adminje')
        response = self.client.post('/admin/api/checkin', json=dict(token=token))
        data = response.get_json()
        self.assertEqual(data['status'], 'success')
        self.assertIn('John Doe', data['name'])
        
        # Test scan check-in again (should say already checked in)
        response = self.client.post('/admin/api/checkin', json=dict(token=token))
        data = response.get_json()
        self.assertEqual(data['status'], 'already_checked_in')
        
        # Test invalid token
        response = self.client.post('/admin/api/checkin', json=dict(token='invalid-token-123'))
        data = response.get_json()
        self.assertEqual(data['status'], 'invalid')

    def test_email_case_insensitivity(self):
        """Test that registering with emails in different casing acts as a duplicate."""
        # Log in first
        self.login('admin', 'adminje')
        
        # Create an event
        self.client.post('/admin/dashboard', data=dict(
            name='Case Test Event',
            date='2026-12-31T19:00',
            location='Salle B',
            description='Testing email casing.'
        ), follow_redirects=True)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM events WHERE name = 'Case Test Event'")
        event = cursor.fetchone()
        self.assertIsNotNone(event)
        event_id = event[0]
        conn.close()

        # Register participant 1 with uppercase email
        self.client.post(f'/event/{event_id}/register', data=dict(
            nom='Smith',
            prenom='Jane',
            classe='M2 Info',
            departement='Genie Logiciel',
            telephone='0700000000',
            email='Jane.Smith@Test.com'
        ), follow_redirects=True)
        
        # Verify the participant is registered (email stored as lowercase)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM participants WHERE nom = 'Smith'")
        part = cursor.fetchone()
        self.assertIsNotNone(part)
        self.assertEqual(part[0], 'jane.smith@test.com')
        conn.close()

        # Try registering same email with lowercase casing - should fail/redirect as duplicate
        response2 = self.client.post(f'/event/{event_id}/register', data=dict(
            nom='Smith Duo',
            prenom='Jane',
            classe='M2 Info',
            departement='Genie Logiciel',
            telephone='0700000000',
            email='jane.smith@test.com'
        ), follow_redirects=True)
        
        self.assertIn(b'Cette adresse email est', response2.data)

    def test_direct_checkin_uncheckin(self):
        """Test the direct check-in and un-check-in admin endpoints."""
        # Log in first
        self.login('admin', 'adminje')
        
        # Create an event
        self.client.post('/admin/dashboard', data=dict(
            name='Direct Check Event',
            date='2026-12-31T20:00',
            location='Salle C',
            description='Testing direct checkin.'
        ), follow_redirects=True)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM events WHERE name = 'Direct Check Event'")
        event_id = cursor.fetchone()[0]
        conn.close()

        # Register participant
        self.client.post(f'/event/{event_id}/register', data=dict(
            nom='Taylor',
            prenom='Alex',
            classe='L3 Info',
            departement='Securite',
            telephone='0611111111',
            email='alex.taylor@test.com'
        ), follow_redirects=True)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, checked_in FROM participants WHERE nom = 'Taylor'")
        part = cursor.fetchone()
        part_id = part[0]
        checked_in = part[1]
        self.assertEqual(checked_in, 0)
        conn.close()

        # Try checkin without admin login (should redirect)
        self.logout()
        response = self.client.post(f'/admin/participant/{part_id}/checkin_direct', follow_redirects=True)
        self.assertIn(b'Veuillez vous connecter', response.data)

        # Log in and check-in
        self.login('admin', 'adminje')
        response = self.client.post(f'/admin/participant/{part_id}/checkin_direct', follow_redirects=True)
        has_valide_msg = (
            b'valide' in response.data or 
            b'validation' in response.data or
            b'success' in response.data or
            b'Entr' in response.data
        )
        self.assertTrue(has_valide_msg)

        # Verify checked_in state is now 1
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT checked_in FROM participants WHERE id = ?", (part_id,))
        self.assertEqual(cursor.fetchone()[0], 1)
        conn.close()

        # Direct uncheck-in
        response = self.client.post(f'/admin/participant/{part_id}/uncheckin_direct', follow_redirects=True)
        has_annulee_msg = (
            b'annul' in response.data or
            b'validation' in response.data or
            b'Restaurer' in response.data
        )
        self.assertTrue(has_annulee_msg)

        # Verify checked_in state is back to 0
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT checked_in FROM participants WHERE id = ?", (part_id,))
        self.assertEqual(cursor.fetchone()[0], 0)
        conn.close()

    def test_club_registration(self):
        """Test the club / association account registration flow."""
        # Attempt registration with missing fields — should fail
        response = self.client.post('/admin/register', data=dict(
            username='',
            club_name='Club Robotique',
            email='robot@univ.fr',
            password='Secure123!'
        ), follow_redirects=True)
        self.assertIn(b'obligatoires', response.data)

        # Successful registration
        response = self.client.post('/admin/register', data=dict(
            username='club_robot',
            club_name='Club Robotique',
            email='robot@univ.fr',
            password='Secure123!'
        ), follow_redirects=True)
        self.assertIn(b'Club Robotique', response.data)

        # Duplicate username/email should fail
        response = self.client.post('/admin/register', data=dict(
            username='club_robot',
            club_name='Club Robotique 2',
            email='robot2@univ.fr',
            password='Secure123!'
        ), follow_redirects=True)
        self.assertIn('déjà utilisé'.encode('utf-8'), response.data)

        # Login with the new club account
        response = self.login('club_robot', 'Secure123!')
        self.assertIn(b'Tableau de Bord', response.data)

    def test_multi_tenant_isolation(self):
        """Test that clubs can only see and manage their own events."""
        # Register a second club
        self.client.post('/admin/register', data=dict(
            username='club_musique',
            club_name='Club Musique',
            email='musique@univ.fr',
            password='Music456!'
        ), follow_redirects=True)

        # Log in as default admin and create an event
        self.login('admin', 'adminje')
        self.client.post('/admin/dashboard', data=dict(
            name='Admin Event',
            date='2027-01-01T10:00',
            location='Salle A',
            description='Event by default admin.'
        ), follow_redirects=True)

        import sqlite3
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM events WHERE name = 'Admin Event'")
        admin_event_id = cursor.fetchone()[0]
        conn.close()
        self.logout()

        # Log in as club_musique and verify they CANNOT see the admin's event
        self.login('club_musique', 'Music456!')
        response = self.client.get('/admin/dashboard')
        self.assertNotIn(b'Admin Event', response.data)

        # Attempt to access the admin's event detail page — should return 403
        response = self.client.get(f'/admin/event/{admin_event_id}')
        self.assertEqual(response.status_code, 403)

        # Attempt to delete the admin's event — should return 403
        response = self.client.post(f'/admin/event/{admin_event_id}/delete', follow_redirects=False)
        self.assertEqual(response.status_code, 403)
        self.logout()

if __name__ == '__main__':
    unittest.main()
