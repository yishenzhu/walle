FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/opt

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates bubblewrap tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/walle

COPY deploy/install_deps.py /tmp/install_deps.py
COPY pyproject.toml ./
RUN python /tmp/install_deps.py pyproject.toml && rm /tmp/install_deps.py

COPY . .

RUN chmod +x deploy/*.sh \
 && mkdir -p data/canvas data/sessions logs /var/lib/walle \
 && chown -R 10001:10001 /opt/walle /var/lib/walle

USER 10001:10001

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/walle/deploy/entrypoint.sh"]
CMD ["python", "-m", "walle.main"]
