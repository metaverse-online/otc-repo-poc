"""Dev entrypoint: `python run.py` (gunicorn uses wsgi.py)."""
from otc import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5055, debug=True)
