FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copies the app into the image (see .dockerignore for what is left out). The database is PostgreSQL, found through the DATABASE_URL setting.
COPY . .

EXPOSE 8000

# The Flask object is flask_app inside app/app.py, hence --chdir app and app:flask_app.
# Two workers with four threads each: every request opens its own database connection and closes it again, so they share nothing.
CMD ["gunicorn", "--chdir", "app", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "120", "app:flask_app"]
