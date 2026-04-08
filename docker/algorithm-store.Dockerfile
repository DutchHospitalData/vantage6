# Dockerfile for the algorithm store
#
# IMAGE
# -----
# * harbor2.vantage6.ai/infrastructure/algorithm-store:x.x.x
#
ARG TAG=latest
ARG BASE=4.14
ARG REGISTRY=harbor2.vantage6.ai
FROM ${REGISTRY}/infrastructure/infrastructure-base:${BASE}

LABEL version=${TAG}
LABEL maintainer="Frank Martin <f.martin@iknl.nl>; Bart van Beusekom <b.vanbeusekom@iknl.nl>"

# Switch apt to HTTPS (port 80 blocked)
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt update -y \
    && apt upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# Fix DB issue
RUN pip install psycopg2-binary

# copy source
COPY . /vantage6

# install individual packages
# TODO check which dependencies are needed - remove at least server
RUN pip install -e /vantage6/vantage6-common
RUN pip install -e /vantage6/vantage6-client
RUN pip install -e /vantage6/vantage6
RUN pip install -e /vantage6/vantage6-backend-common
RUN pip install -e /vantage6/vantage6-algorithm-store

# Overwrite uWSGI installation from the requirements.txt
# Install uWSGI from source (for RabbitMQ)
RUN apt-get install --no-install-recommends --no-install-suggests -y \
  libssl-dev python3-setuptools
RUN CFLAGS="-I/usr/local/opt/openssl/include" \
  LDFLAGS="-L/usr/local/opt/openssl/lib" \
  UWSGI_PROFILE_OVERRIDE=ssl=true \
  pip install uwsgi -Iv

RUN chmod +x /vantage6/vantage6-algorithm-store/server.sh
