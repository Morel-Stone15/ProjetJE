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

if __name__ == '__main__':
    unittest.main()
