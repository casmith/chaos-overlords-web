# Chaos Overlords session manager.
#
# Starts a game container per player on demand, hands out a random password,
# proxies the browser to it, and reaps it when nobody is watching.
#
# This container needs the Docker socket in order to create the session
# containers. The session containers themselves never get it (SPEC section 31):
# they run the untrusted legacy software, this does not.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    STATE_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN set -eux; \
    pip install --no-cache-dir -r requirements.txt

COPY app.py sessions.py proxy.py web_ui.py session_auth.py ./

RUN set -eux; \
    useradd -u 1000 -m manager; \
    mkdir -p /data; \
    chown manager:manager /data

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=4).status==200 else 1)"

# Runs as root because it must talk to the Docker socket, whose group id varies
# by host. Give the socket to a proxy such as tecnativa/docker-socket-proxy and
# drop this to `user: manager` if that matters in your deployment.
CMD ["python3", "app.py"]
