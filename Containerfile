FROM docker.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Named paths only, so local secrets (.env, CLAUDE.md) can't end up in the image.
COPY manage.py ./
COPY bundleui ./bundleui
COPY catalog ./catalog
COPY container/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod 0755 /usr/local/bin/entrypoint.sh \
    && DJANGO_SECRET_KEY=build-time-only python manage.py collectstatic --noinput \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app

USER app
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["web"]
