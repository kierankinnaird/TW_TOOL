FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copies the app and build/timewriting.db into the image (see .dockerignore for what is left out).
COPY . .

EXPOSE 8000

# The Flask object is flask_app inside app/app.py, hence --chdir app and app:flask_app.
# One worker only: SQLite is a single file, and this keeps the demo simple.
CMD ["gunicorn", "--chdir", "app", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "120", "app:flask_app"]
