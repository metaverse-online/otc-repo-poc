"""WSGI entrypoint for gunicorn: `gunicorn -w 2 -b 0.0.0.0:5055 wsgi:app`."""
from otc import create_app

app = create_app()
