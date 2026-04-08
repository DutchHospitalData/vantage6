FROM debian:12

# Switch apt to HTTPS (port 80 blocked); bootstrap ca-certificates first
# with verification disabled since the minimal image lacks certs.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::https::Verify-Peer=false update \
    && apt-get -o Acquire::https::Verify-Peer=false install -y ca-certificates \
    && apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y openssh-server curl \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir /app

COPY services/ssh-tunnel/ /app/
RUN chmod +x /app/entry.sh

ENTRYPOINT ["/app/entry.sh"]