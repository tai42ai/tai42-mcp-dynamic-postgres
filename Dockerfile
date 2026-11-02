# Use official Python image
FROM python:3.15-slim

# Set working directory
WORKDIR /app

# Copy only what is needed to build and install the package.
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src

# Install Python dependencies
RUN pip install --upgrade pip && pip install .

# Set environment variables (override via command line or docker-compose)
ENV PYTHONUNBUFFERED=1

# Run as an unprivileged user rather than root.
RUN adduser --system --no-create-home app
USER app

# Default command (can be overridden)
CMD ["tai42-mcp-dynamic-postgres"]
