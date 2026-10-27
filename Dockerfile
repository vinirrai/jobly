FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY jobly ./jobly
RUN pip install --no-cache-dir .

ENV JOBLY_HOME=/data
VOLUME /data
EXPOSE 8000

ENTRYPOINT ["jobly"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
