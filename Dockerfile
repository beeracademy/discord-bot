FROM python:3.8

RUN pip install --no-cache-dir poetry
RUN poetry config virtualenvs.create false

WORKDIR /app

COPY poetry.lock pyproject.toml /app/
RUN poetry install --no-dev --no-interaction

COPY . /app

ARG GIT_COMMIT_HASH
ENV GIT_COMMIT_HASH $GIT_COMMIT_HASH

CMD ["python", "bot.py"]
