FROM python:3.13-alpine

WORKDIR /app

RUN apk add --no-cache bash curl docker-cli git kubectl openssh-client

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY index.html styles.css app.js server.py agent.py entrypoint.sh ./
RUN chmod +x /app/entrypoint.sh

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1

CMD ["/app/entrypoint.sh"]
