ARG GALLERYHANG_DEPS_IMAGE=python:3.11-slim
FROM ${GALLERYHANG_DEPS_IMAGE} AS dependencies
USER root
WORKDIR /build/galleryhang
COPY requirements.txt .
RUN python -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: -r requirements.txt && python -m pip check

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOST=0.0.0.0 PORT=8000 GALLERYHANG_DATA=/var/lib/galleryhang
WORKDIR /opt/galleryhang
COPY --from=dependencies /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY galleryhang ./galleryhang
COPY static ./static
RUN useradd -U -u 11523 galleryhang && install -d -o galleryhang -g galleryhang /var/lib/galleryhang
USER galleryhang
EXPOSE 8000
CMD ["python", "-m", "galleryhang"]
