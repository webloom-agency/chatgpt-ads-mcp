# Hosted ChatGPT / OpenAI Ads MCP (Python, Streamable HTTP)
# Render: Language = Docker, Root Directory = (leave empty)

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCP_TRANSPORT=http \
    MCP_STATELESS_HTTP=true \
    OPENAI_ADS_MCP_READONLY=1 \
    HOST=0.0.0.0
# Render injects PORT at runtime (often 10000). Do not hardcode it.

WORKDIR /build

# Keep python/ + LICENSE side-by-side so hatch's ../LICENSE include resolves.
COPY LICENSE ./LICENSE
COPY python ./python

WORKDIR /build/python
RUN pip install --no-cache-dir . \
    && rm -rf /root/.cache/pip

WORKDIR /app
ENV PYTHONPATH=/usr/local/lib/python3.12/site-packages

EXPOSE 8000
CMD ["python", "-m", "openai_ads_mcp"]
