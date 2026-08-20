FROM python:3.13-alpine

WORKDIR /app

COPY index.html styles.css app.js server.py ./

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1

CMD ["python", "server.py"]
