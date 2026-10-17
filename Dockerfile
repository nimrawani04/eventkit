FROM python:3.12-slim

WORKDIR /app

COPY requirements-dev.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .
RUN pip install --no-cache-dir --no-deps --no-build-isolation -e .

CMD ["eventkit", "--help"]
