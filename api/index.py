import os
from app import app

def handler(request, response):
    """Vercel serverless entry point for the Flask app.
    Converts the Vercel request dict into a WSGI environ and forwards it to the Flask instance.
    """
    # Vercel provides a helper to build a WSGI environ from the incoming request
    environ = request.get_wsgi_environ()
    return app(environ, response.start_response)
