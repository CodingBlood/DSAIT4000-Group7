FROM python:3.12.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        curl \
        make \
        postgresql-client \
        git \
    && rm -rf /var/lib/apt/lists/*

# /workspace is the starter folder and has no .git of its own. compose mounts
# the repository's git directory at /repo/.git so the pipeline can record a
# real code revision in run.json.
RUN git config --system --add safe.directory /workspace \
    && git config --system --add safe.directory /repo/.git

COPY docker/requirements.txt /tmp/course-requirements.txt
RUN pip install --no-cache-dir -r /tmp/course-requirements.txt

WORKDIR /workspace
EXPOSE 8888

