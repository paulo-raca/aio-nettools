FROM python:3.10-slim as build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PYTHONUNBUFFERED True
ENV APP_SRC /src

# Install dependencies
RUN apt-get update -y && apt-get install -y git

# Create User
RUN useradd -ms /bin/bash aionettools
USER aionettools

# Install app
COPY . $APP_SRC
RUN pip install $APP_SRC

# Entrypoint
ENTRYPOINT ["python", "-m", "aionettools"]
CMD ["ping", "example.com"]
