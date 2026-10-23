# Use a more standard Python image
FROM python:3.11.17-slim

# Patch trust before pip, Git, or SDK downloads (eMudhra incident, 2026-10-20).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && sed -i \
        -e 's|^mozilla/emSign_ECC_Root_CA_-_G3.crt|!&|' \
        -e 's|^mozilla/Comodo_AAA_Services_root.crt|!&|' \
        /etc/ca-certificates.conf \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# PIP_CERT replaces pip's vendored trust, including on legacy Python 3.9.
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    AWS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Create app directory
WORKDIR /app

RUN mkdir -p /root/.aws

# Install dependencies
COPY portal/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# If Uvicorn isn't in your requirements.txt, install it explicitly
RUN pip install gunicorn

# Copy app files into container
COPY portal/ .
COPY shared ./shared

# Command to run the application using Uvicorn
CMD ["gunicorn", "-w 4", "main:app", "-b", "0.0.0.0:8999"]
