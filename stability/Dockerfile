# Use a more standard Python image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Create app directory
WORKDIR /app

RUN mkdir -p /root/.aws

# Install dependencies
COPY proxy/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install gunicorn
# Copy app files into container
COPY stability/ .
COPY shared ./shared

# Command to run the application using Uvicorn
CMD ["bash", "start.sh"]
